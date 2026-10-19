"""P1A shared event clock and command execution, without a new safety policy.

The inherited nameplate/equal-sharing protection surrogate and thermal model
remain uncalibrated. Numerical service shortfall is NOT a disconnected load.
No unapproved surplus sink, emergency policy or solver fallback is introduced.
"""
from __future__ import annotations
from dataclasses import asdict
import copy
import math
import time as clock
import numpy as np
from .model import gt_level_properties, RT_TO_KW
from .execution import ModelConstraintInfeasible,SolverExecutionError,maximum_auxiliary_sink,limited_down_endpoint
from .resilience import (CARRIERS,GROUPS,ZONE_PRIORITY,scenario_faults,loads_for_step,
    _solve_milp,pre_control_cascade,update_thermal,p0_failure_classification)

EPS=1e-9

def clean(value):
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,set)):return [clean(v) for v in value]
    if isinstance(value,np.ndarray):return clean(value.tolist())
    if isinstance(value,np.generic):return value.item()
    return value

def event_window(s,kind):
    dt=float(s.get('step_minutes',15))
    start=float(s.get(kind+'_start_minute',float(s.get(kind+'_start_step',0))*dt))
    duration=float(s.get(kind+'_duration_minutes',float(s.get(kind+'_duration_steps',s['duration_steps']))*dt))
    return start,start+duration

class TransitionCore:
    def __init__(self,env,initial_action):
        self.env=env;self.time=0.;self.pending_commands=[];self.sequence=0
        self.executed=copy.deepcopy(initial_action);self.active_command=None
        self.events=[];self.segments=[];self.errors=[];self.failed=False
        self.last_availability=None;self.grid_outage_start=None
        self.last_shock_active=None
        self.automatic_disconnected=np.zeros((4,3))
        self.ramp_time=None;self.ramp_anchor=None;self.ramp_budget=0.
        self.solver_calls=[];self.pending_shed=None;self.local_down=None;self.gt_start_pending={}
        self.auto_shed_latched=False
        self._commit_sources(initial_action,actual=True)
        initial_load=loads_for_step(env.groups.loc[env.scenario['start_hour']].to_dict(),{})
        self.connection_basis_loads=initial_load.copy()
        self.disconnected_fraction=np.zeros((4,3));self.automatic_fraction=np.zeros((4,3))
        env.state.cooling_reference_kW=float(initial_load[:,2].sum())
        env.state.auxiliary_request_kW=self.aux(initial_action)

    @property
    def state(self):return self.env.state

    def aux(self,a):
        p=self.env.plant
        return (float(a['absorption_cooling_kW'])*p.abs_electric_kW_per_rt+
                sum(a['electric_chiller_cooling_kW'])*p.ec_electric_kW_per_rt)/RT_TO_KW

    def log(self,kind,**values):
        self.events.append(clean(dict(event_id=len(self.events)+1,kind=kind,time_minute=self.time,**values)))

    def data_at(self,t):
        e=self.env;s=e.scenario
        hour=min(len(e.hourly),int(s['start_hour'])+int(math.floor((t+EPS)/60)))
        row=next(e.hourly.iloc[[hour-1]].itertuples(index=False))
        start,end=event_window(s,'shock');active=start<=t<end
        loads=loads_for_step(e.groups.loc[hour].to_dict(),s.get('shock',{}) if active else {},
                             s.get('shock_by_priority',{}) if active else {})
        attribution=self.thermal_attribution(t)
        if attribution is not None:
            _,byhour=e.thermal_cache
            for name,value in attribution['sensible_kW'].items():loads[ZONE_PRIORITY[name],2]+=value*float(byhour[hour][name].zone_multiplier)
            for group,value in attribution['process_cold_kW'].items():loads[GROUPS.index(group),2]+=value
        f=scenario_faults(s,t/e.dt_minutes,self.state)
        return hour,row,loads,f

    def thermal_attribution(self,t):
        if self.env.execution_contract['thermal_mode']!='attributed':return None
        d=self.env.scenario.get('thermal_disturbance',{})
        active=float(d.get('start_minute',0.))<=t<float(d.get('end_minute',float('inf')))
        values=dict(sensible_kW=d.get('sensible_kW',{}) if active else {},process_cold_kW=d.get('process_cold_kW',{}) if active else {})
        if set(values['sensible_kW'])-set(ZONE_PRIORITY) or set(values['process_cold_kW'])-set(GROUPS):raise ValueError('Unknown thermal attribution object')
        if any(not math.isfinite(v) or v<0 for mapping in values.values() for v in mapping.values()):raise ValueError('Invalid thermal disturbance')
        return copy.deepcopy(values)

    def delayed_at(self,t):
        s=self.env.scenario
        start,_=event_window(s,'fault' if s['stage'] in (1,3) else 'shock')
        delay=float(s.get('response_delay_minutes',float(s.get('response_delay_steps',0))*self.env.dt_minutes))
        return start<=t<start+delay,start+delay

    def diesel_available(self,t,f):
        permitted=(f['GRID']==0.) if self.env.execution_contract['diesel_mode']=='emergency_bus' else (f['GRID']<1 or bool(self.env.scenario.get('diesel_on_grid_allowed',False)))
        return permitted and self.state.diesel_status=='CONNECTED' and f['DIESEL']>0 and self.state.diesel_energy_remaining_kWh>EPS

    def _update_support_states(self,f):
        c=self.env.execution_contract;s=self.state
        s.topology_mode=c['diesel_mode'];before=s.diesel_status
        permitted=(f['GRID']==0) if c['diesel_mode']=='emergency_bus' else (f['GRID']<1 or bool(self.env.scenario.get('diesel_on_grid_allowed',False)))
        if f['DIESEL']<=0:status='FAILED'
        elif s.diesel_energy_remaining_kWh<=EPS:status='EMPTY'
        elif not permitted:status='OFF'
        else:
            if s.diesel_start_minute is None:s.diesel_start_minute=self.time
            age=self.time-s.diesel_start_minute
            delay=float(self.env.config['diesel_start_delay_minutes'])
            status='STARTING' if age<delay-EPS else ('READY' if age<delay+c['ats_transfer_minutes']-EPS else 'CONNECTED')
        if status in ('FAILED','EMPTY','OFF'):s.diesel_start_minute=None
        s.diesel_status=status
        if status!=before:self.log('diesel_state_changed',before=before,after=status,mode=c['diesel_mode'],allowed_priorities=c['diesel_allowed_priorities'])

    def observe(self):
        h,row,loads,f=self.data_at(self.time)
        return dict(step=self.env.steps_done,time_minute=self.time,
            grid_outage_elapsed_minutes=max(0.,self.time-self.grid_outage_start) if self.grid_outage_start is not None else 0.,
            hour_of_year=h,outdoor_temperature_C=float(row.outdoor_temperature_C),
            grid_import_limit_kW=float(self.env.scenario.get('grid_limit_kW',self.env.config['grid_import_limit_kW'])),
            loads_kW=loads.copy(),availability=f,gt_prev_kW=self.state.gt_prev.copy(),
            diesel_energy_kWh=float(self.state.diesel_energy_remaining_kWh),
            grid_import_prev_kW=float(self.state.last_grid_import_kW),secondary_faults=sorted(self.state.extra_faulted),
            diesel_status=self.state.diesel_status,topology_mode=self.state.topology_mode,
            arrived_grid_import_limit_kW=float(self.executed['grid_import_kW']),
            gt_status=['FAILED' if f[f'GT_{i+1}']<=0 else 'STARTING' if i in self.gt_start_pending else
                       'RUNNING' if self.state.gt_prev[i]>EPS else 'OFF' for i in range(3)],
            zone_thermal_delta_C=dict(self.state.thermal_delta),control_available=not self.delayed_at(self.time)[0])

    def _commit_sources(self,a,actual=False):
        self.state.gt_prev=np.asarray(a['gt_electric_kW'],dtype=float).copy()
        value=float(a['grid_import_kW'])
        self.state.last_grid_import_kW=(value if actual or self.env.execution_contract['grid_import_mode']=='fixed'
                                      else min(value,self.state.last_grid_import_kW))
        self.state.last_diesel_kW=float(a['diesel_kW'])

    def _solve(self,loads,f,dt_h,phase,action,**kwargs):
        e=self.env;_,row,_,_=self.data_at(self.time)
        started=clock.perf_counter()
        try:
            result=_solve_milp(row,e.plant,loads,f,self.state,dt_h,
                float(e.scenario.get('grid_limit_kW',e.config['grid_import_limit_kW'])),
                self.diesel_available(self.time,f),float(e.config['gt_ramp_kW_per_hour']),
                float(e.config['solver_time_limit_sec']),e.config,execution_contract=e.execution_contract,**kwargs)
            self.solver_calls.append(dict(time_minute=self.time,phase=phase,dt_h=dt_h,
                elapsed_seconds=clock.perf_counter()-started,optimizer_warning=result.get('optimizer_warning',''),status='success',
                solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers',[]))))
            return result
        except Exception as exc:
            self._record_error(exc,loads,f,dt_h,phase,action,started)

    def _record_error(self,exc,loads,f,dt_h,phase,action,started):
        e=self.env;_,row,_,_=self.data_at(self.time)
        record=clean(dict(scenario_id=e.scenario.get('id'),scenario=copy.deepcopy(e.scenario),
            step=e.steps_done,time_minute=self.time,phase=phase,loads_kW=loads,
            availability=f,state=asdict(self.state),command=action,
            executed_action=self.executed,pending_commands=self.pending_commands,
            local_execution_state=dict(pending_shed=self.pending_shed,local_down=self.local_down,
                gt_start_pending=self.gt_start_pending,auto_shed_latched=self.auto_shed_latched,
                automatic_disconnected=self.automatic_disconnected,disconnected_fraction=self.disconnected_fraction,
                automatic_fraction=self.automatic_fraction,connection_basis_loads=self.connection_basis_loads,
                grid_outage_start=self.grid_outage_start,ramp_time=self.ramp_time,ramp_anchor=self.ramp_anchor,ramp_budget=self.ramp_budget),
            config=e.config,execution_contract=e.execution_contract,plant=e.plant.as_dict(),dt_h=dt_h,
            row=dict(timestamp=str(row.timestamp),outdoor_temperature_C=float(row.outdoor_temperature_C)),
            completed_segments=copy.deepcopy(self.segments),events=copy.deepcopy(self.events),
            category=getattr(exc,'category','solver_exception'),error_type=type(exc).__name__,message=str(exc),
            solver_backend_attempts=copy.deepcopy(getattr(exc,'solver_backend_attempts',[]))))
        self.errors.append(record);self.failed=True
        self.solver_calls.append(dict(time_minute=self.time,phase=phase,dt_h=dt_h,
            elapsed_seconds=clock.perf_counter()-started,status='error',message=str(exc),
            solver_backend_layers=copy.deepcopy(getattr(exc,'solver_backend_attempts',[]))))
        self.log('solver_error',phase=phase,message=str(exc))
        error=exc if isinstance(exc,(ModelConstraintInfeasible,SolverExecutionError,ValueError)) else SolverExecutionError(str(exc))
        error.simulation_diagnostic=copy.deepcopy(record)
        if error is exc:raise error
        raise error from exc

    def generate_command(self,action):
        if self.failed:raise RuntimeError('Previous solver failure retained; reset before continuing')
        if not isinstance(action,dict) or set(action)-{'targets','security'}:raise ValueError('Invalid action mapping')
        targets=action.get('targets',{});security=action.get('security',{})
        if not isinstance(targets,dict) or not isinstance(security,dict) or set(security)-{'gt_loading_penalty_USD_per_kWh'}:
            raise ValueError('Invalid action targets/security')
        weight=float(security.get('gt_loading_penalty_USD_per_kWh',0.))
        # Validation/planning uses current information only. A solved command
        # is queued, not applied by the physical solver before arrival.
        _,_,loads,f=self.data_at(self.time)
        sol=self._solve(loads,f,self.env.dt_h,'command_generation',action,
            tracking_targets=targets,security_gt_penalty_USD_per_kWh=weight)
        delayed,release=self.delayed_at(self.time)
        self.sequence+=1
        cmd=dict(id=self.sequence,generated_minute=self.time,arrival_minute=release if delayed else self.time,
            request=copy.deepcopy(action),dispatch=sol['action'],disconnected=sol['lost'].tolist(),
            reference_cooling_kW=float(loads[:,2].sum()),weight=weight,
            optimizer_warning=sol.get('optimizer_warning',''))
        self.pending_commands.append(cmd)
        self.log('command_generated',command=cmd)
        return cmd

    def _clamp(self,dispatch,f,normal_ramp=False):
        e=self.env;p=e.plant;_,row,_,_=self.data_at(self.time)
        rated=float(gt_level_properties(p,float(row.outdoor_temperature_C))[0][-1])
        a=copy.deepcopy(dispatch);before=np.asarray(self.executed['gt_electric_kW'])
        if normal_ramp and (self.ramp_time is None or abs(self.time-self.ramp_time)>EPS):
            elapsed=e.dt_minutes if self.ramp_time is None else min(e.dt_minutes,self.time-self.ramp_time)
            self.ramp_budget=float(e.config['gt_ramp_kW_per_hour'])*elapsed/60
            self.ramp_anchor=before.copy();self.ramp_time=self.time
        for i in range(3):
            cap=rated*f[f'GT_{i+1}'];v=min(cap,max(0.,float(a['gt_electric_kW'][i])))
            if normal_ramp:
                base=min(self.ramp_anchor[i],cap);change=self.ramp_budget
                v=min(base+change,max(max(0.,base-change),v))
            a['gt_electric_kW'][i]=v
            delay=e.execution_contract['gt_start_delay_minutes']
            if cap<=EPS:self.gt_start_pending.pop(i,None)
            elif normal_ramp and delay>0 and before[i]<=EPS:
                if v<=EPS:self.gt_start_pending.pop(i,None)
                else:
                    # Start-up accumulates reachability from its own start.
                    # A newer short-interval command must not replace a valid
                    # online target with an intermediate sub-minimum port.
                    requested=min(cap,max(0.,float(dispatch['gt_electric_kW'][i])))
                    if i not in self.gt_start_pending:
                        wait=max(delay,requested/float(e.config['gt_ramp_kW_per_hour'])*60)
                        self.gt_start_pending[i]=dict(ready=self.time+wait,requested=requested,started=self.time)
                        self.log('gt_start_requested',device=f'GT_{i+1}',ready_minute=self.time+wait,requested_kW=requested)
                    self.gt_start_pending[i]['requested']=requested
                    pending=self.gt_start_pending[i]
                    pending['ready']=max(pending['started']+delay,pending['started']+requested/float(e.config['gt_ramp_kW_per_hour'])*60)
                    if self.time<self.gt_start_pending[i]['ready']-EPS:a['gt_electric_kW'][i]=0.
        a['grid_import_kW']=min(float(a['grid_import_kW']),float(e.scenario.get('grid_limit_kW',e.config['grid_import_limit_kW']))*f['GRID'])
        a['diesel_kW']=min(float(a['diesel_kW']),p.emergency_diesel_kW*f['DIESEL']) if self.diesel_available(self.time,f) and self.state.diesel_energy_remaining_kWh>EPS else 0.
        a['boiler_steam_lbph']=min(float(a['boiler_steam_lbph']),p.boiler_steam_lbph/3*sum(f[f'BOILER_{i}'] for i in (1,2,3)))
        a['absorption_cooling_kW']=min(float(a['absorption_cooling_kW']),p.absorption_rt*RT_TO_KW*f['ABS'])
        a['electric_chiller_cooling_kW']=[min(float(v),p.electric_chillers_rt[i]*RT_TO_KW*f[f'EC_{i+1}']) for i,v in enumerate(a['electric_chiller_cooling_kW'])]
        return a

    def boundary(self):
        _,_,loads,f=self.data_at(self.time)
        self._sync_connections(loads)
        self._update_support_states(f)
        if f['GRID']<1 and self.grid_outage_start is None:self.grid_outage_start=self.time
        if f['GRID']>=1:self.grid_outage_start=None
        if self.last_availability is not None and f!=self.last_availability:
            self.log('availability_changed',before=self.last_availability,after=f)
            self.auto_shed_latched=False;self.pending_shed=None
        self.last_availability=dict(f)
        start,end=event_window(self.env.scenario,'shock');active=start<=self.time<end
        if self.last_shock_active is not None and active!=self.last_shock_active:self.log('shock_changed',active=active)
        self.last_shock_active=active
        before=copy.deepcopy(self.executed)
        self.executed=self._clamp(self.executed,f)
        if self.executed!=before:
            self.log('forced_capacity_or_permission_clamp',before=before,after=self.executed,availability=f)
        self._commit_sources(self.executed)
        ready=[i for i,p in self.gt_start_pending.items() if p['ready']<=self.time+EPS]
        if ready:
            target=copy.deepcopy(self.executed)
            for i in ready:target['gt_electric_kW'][i]=self.gt_start_pending[i]['requested']
            self.executed=self._clamp(target,f);self._commit_sources(self.executed)
            for i in ready:
                self.gt_start_pending.pop(i,None)
                self.log('gt_start_completed',device=f'GT_{i+1}',actual_kW=self.executed['gt_electric_kW'][i])
        due=[c for c in self.pending_commands if c['arrival_minute']<=self.time+EPS]
        if due:
            for cmd in due:self.log('command_arrived',command_id=cmd['id'],generated_minute=cmd['generated_minute'])
            latest=max(due,key=lambda c:(c['generated_minute'],c['id']))
            for cmd in due:
                if cmd is not latest:self.log('command_superseded',command_id=cmd['id'],by=latest['id'])
            self.pending_commands=[c for c in self.pending_commands if c not in due]
            actual=self._clamp(latest['dispatch'],f,normal_ramp=True)
            self.executed=actual;self.active_command=latest;self._commit_sources(actual)
            old_disconnected=self.state.disconnected_loads.copy()
            self.state.disconnected_loads=np.minimum(loads,np.maximum(0.,np.asarray(latest['disconnected'])))
            restored=np.maximum(0.,old_disconnected-self.state.disconnected_loads)
            if restored.sum()>EPS:self.log('load_connection_restored',restored_kW=restored,command_id=latest['id'])
            self.automatic_disconnected[:]=0.
            self.disconnected_fraction=np.divide(self.state.disconnected_loads,loads,out=np.zeros_like(loads),where=loads>EPS)
            self.automatic_fraction[:]=0.
            self.pending_shed=None;self.local_down=None
            self.auto_shed_latched=False
            self.state.auxiliary_request_kW=self.aux(actual)
            self.state.cooling_reference_kW=latest['reference_cooling_kW']
            self.log('command_executed',command_id=latest['id'],generated_minute=latest['generated_minute'],
                     arrival_minute=latest['arrival_minute'],requested_dispatch=latest['dispatch'],executed_dispatch=actual,
                     reason='revalidated_against_current_capacity_and_ramp')

    def _next_boundary(self,end):
        s=self.env.scenario;candidates=[end,(math.floor(self.time/60)+1)*60]
        for kind in ('fault','shock'):candidates.extend(event_window(s,kind))
        candidates.extend(c['arrival_minute'] for c in self.pending_commands)
        candidates.extend(c['ready'] for c in self.gt_start_pending.values())
        if self.pending_shed is not None:candidates.append(self.pending_shed['arrival'])
        if self.local_down is not None:
            candidates.append(self.local_down['arrival'])
            if self.time>=self.local_down['arrival']-EPS:
                candidates.append(self.time+self.env.execution_contract['local_max_segment_minutes'])
                rate=self.env.execution_contract['gt_local_down_rate_kW_per_hour']
                for current,target in zip(self.executed['gt_electric_kW'],self.local_down['target']):
                    if current>target+EPS:candidates.append(self.time+(current-target)/rate*60)
        d=s.get('thermal_disturbance',{})
        candidates.extend(d[k] for k in ('start_minute','end_minute') if k in d)
        if self.state.diesel_start_minute is not None:
            ready=self.state.diesel_start_minute+float(self.env.config['diesel_start_delay_minutes'])
            candidates.extend([ready,ready+self.env.execution_contract['ats_transfer_minutes']])
        if self.grid_outage_start is not None:
            candidates.append(self.grid_outage_start+float(self.env.config['diesel_start_delay_minutes']))
        if self.executed['diesel_kW']>EPS:
            candidates.append(self.time+60*self.state.diesel_energy_remaining_kWh/self.executed['diesel_kW'])
        return min(x for x in candidates if x>self.time+EPS)

    def _protection(self,loads,minutes,automatic=False):
        e=self.env;_,_,_,f=self.data_at(self.time)
        cfg=dict(e.cascade_config,precontrol_load_shed_enabled=automatic)
        # Only delivered source output is credited. Available grid/diesel
        # headroom is not an executed increase while a command is pending.
        state=copy.deepcopy(self.state) if minutes==0 else self.state
        return pre_control_cascade(state,f,loads,e.plant,cfg,minutes,False,
            self.state.last_grid_import_kW/max(f['GRID'],EPS) if f['GRID'] else 0.,
            self.state.last_diesel_kW,apply_trips=False)

    def _stress_for_solution(self,connected,solution):
        original=self.env.state;self.env.state=copy.deepcopy(original)
        try:
            self._commit_sources(solution['action'],actual=True)
            self.state.auxiliary_request_kW=self.aux(solution['action'])
            return self._protection(connected,0.)[1]
        finally:self.env.state=original

    def _ramp_threshold_boundary(self,connected,f,row,next_time,cmd,source_start,source_end):
        """Bracket a threshold crossing using real port solves, no timer writes.

        Event-free finite down segments are at most local_max_segment_minutes.
        This detects endpoint-bracketed crossings; not a frequency model.
        """
        if not np.any(source_start-source_end>EPS):return next_time
        dt=(next_time-self.time)/60
        def evaluate(frac):
            a=copy.deepcopy(self.executed);a['gt_electric_kW']=(source_start+(source_end-source_start)*frac).tolist()
            x=self._solve(connected,f,dt,'local_ramp_protection_probe',cmd,execution_action=a)
            return self._stress_for_solution(connected,x)
        first=evaluate(0.);last=evaluate(1.)
        threshold=float(self.env.config['gt_protection_trip_multiple'])+1e-9
        earliest=1.
        for dev in set(first)&set(last):
            if (first[dev]>threshold)==(last[dev]>threshold):continue
            lo=0.;hi=1.;above=first[dev]>threshold
            for _ in range(40):
                mid=(lo+hi)/2
                if (evaluate(mid).get(dev,0.)>threshold)==above:lo=mid
                else:hi=mid
                if (hi-lo)*(next_time-self.time)<=1e-9:break
            root=(lo+hi)/2
            if root*(next_time-self.time)>1e-8:earliest=min(earliest,root)
        return self.time+earliest*(next_time-self.time)

    def _automatic_shed(self,loads,sol=None):
        if not self.env.scenario.get('automatic_fast_shedding',False):return
        if self.auto_shed_latched:return
        connected=np.maximum(0.,loads-self.state.disconnected_loads)
        if sol is None:
            _,_,_,f=self.data_at(self.time)
            sol=self._solve(connected,f,self.env.dt_h,'shedding_measurement',self.active_command,
                execution_action=self.executed)
        p=self.env.plant
        factor=p.ec_electric_kW_per_rt/RT_TO_KW*float(self.env.config['precontrol_cooling_to_el_fraction'])
        shares=self.env.cascade_config.get('precontrol_sheddable_fraction_by_priority',{'P3':1.,'P2':1.,'P1':1.})
        real_request=max(0.,float(sol['lost'][:,0].sum()))
        cold_request=max(0.,float(connected[:,2].sum())-self.state.cooling_reference_kW)
        budgets={0:real_request,2:cold_request}
        extra=np.zeros((4,3))
        for i in (3,2,1):
            for k in (0,2):
                remaining=max(0.,loads[i,k]*float(shares.get(GROUPS[i],0.))-self.automatic_disconnected[i,k])
                # Only currently unserved service can be disconnected here.
                # Cold stress in electric-equivalent kW is never an E load.
                amount=min(float(sol['lost'][i,k]),connected[i,k],remaining,budgets[k])
                extra[i,k]=max(0.,amount);budgets[k]-=amount
        if extra.sum()>EPS:
            if self.pending_shed is None:
                self.pending_shed=dict(arrival=self.time+self.env.execution_contract['automatic_shedding_delay_minutes'],requested=extra.copy())
                self.log('automatic_shed_requested',real_electric_request_kW=real_request,cold_request_kW=cold_request,
                    cold_proxy_kW=cold_request*factor,arrival_minute=self.pending_shed['arrival'])
            if self.time+EPS<self.pending_shed['arrival']:return
            extra=np.minimum(extra,self.pending_shed['requested'])
            self.state.disconnected_loads+=extra
            self.automatic_disconnected+=extra
            self.disconnected_fraction=np.divide(self.state.disconnected_loads,loads,out=self.disconnected_fraction.copy(),where=loads>EPS)
            self.automatic_fraction=np.divide(self.automatic_disconnected,loads,out=self.automatic_fraction.copy(),where=loads>EPS)
            self.pending_shed=None
            self.auto_shed_latched=True
            self.log('automatic_load_shed',disconnected_kW=extra,capability='existing_scenario_priority_fractions',
                real_electric_request_kW=real_request,cold_proxy_kW=cold_request*factor,
                actual_electric_disconnected_kW=float(extra[:,0].sum()),cold_service_disconnected_kW=float(extra[:,2].sum()),
                objects=[dict(priority=GROUPS[i],carrier=CARRIERS[k],power_kW=extra[i,k]) for i in range(4) for k in range(3) if extra[i,k]>EPS])
        elif self.pending_shed is not None:
            self.log('automatic_shed_request_cancelled',reason='no remaining actionable service deficit')
            self.pending_shed=None

    def _sync_connections(self,loads):
        if not np.array_equal(loads,self.connection_basis_loads):
            self.auto_shed_latched=False;self.pending_shed=None
            self.disconnected_fraction=np.divide(self.state.disconnected_loads,self.connection_basis_loads,
                out=self.disconnected_fraction.copy(),where=self.connection_basis_loads>EPS)
            self.automatic_fraction=np.divide(self.automatic_disconnected,self.connection_basis_loads,
                out=self.automatic_fraction.copy(),where=self.connection_basis_loads>EPS)
            before=self.state.disconnected_loads.copy()
            self.state.disconnected_loads=np.minimum(loads,self.disconnected_fraction*loads)
            self.automatic_disconnected=np.minimum(loads,self.automatic_fraction*loads)
            if before.sum()>EPS:self.log('disconnected_port_demand_changed',before_kW=before,after_kW=self.state.disconnected_loads,
                reason='same disconnected contact fractions; exogenous demand change, not reconnection')
            self.connection_basis_loads=loads.copy()

    def _surplus_interlock(self,loads,f,row):
        c=self.env.execution_contract
        if c['surplus_interlock']=='none':return
        while True:
            started=clock.perf_counter()
            try:aux=maximum_auxiliary_sink(self.env.plant,row,loads,f,self.executed)
            except Exception as exc:self._record_error(exc,loads,f,0.,'surplus_diagnostic',self.active_command,started)
            supply=sum(self.executed['gt_electric_kW'])+self.executed['diesel_kW']
            if c['grid_import_mode']=='fixed':supply+=self.executed['grid_import_kW']
            surplus=supply-loads[:,0].sum()-aux
            if surplus<=1e-4:break
            # A delayed relay cannot dispose of energy in the intervening
            # interval. The normal physical solve below retains infeasibility.
            if c['surplus_interlock_delay_minutes']>0:return
            i=int(np.argmax(self.executed['gt_electric_kW']))
            if self.executed['gt_electric_kW'][i]<=EPS:return
            before=copy.deepcopy(self.executed);device=f'GT_{i+1}'
            self.state.extra_faulted.add(device);self.executed['gt_electric_kW'][i]=0.;f[device]=0.
            self._commit_sources(self.executed)
            self.log('no_export_interlock',device=device,power_surplus_kW=surplus,
                connected_electric_kW=loads[:,0].sum(),maximum_real_auxiliary_kW=aux,
                before=before,after=self.executed,
                command_arrival_minute=min((x['arrival_minute'] for x in self.pending_commands),default=None),
                assumption='zero-delay algebraic isolation; not a calibrated relay')
            self.env.trip_events.append(dict(device=device,start_minute=self.time,trip_time_minute=self.time,
                step=self.env.steps_done,hold_minutes=0.,stress_ratio=None,trigger='no_export_interlock',
                power_surplus_kW=surplus,initiating_faults=copy.deepcopy(self.env.scenario.get('faults',{}))))
        minimum=.4*float(gt_level_properties(self.env.plant,float(row.outdoor_temperature_C))[0][-1])
        if c['surplus_interlock_delay_minutes']==0:
            for i,power in enumerate(self.executed['gt_electric_kW']):
                if EPS<power<minimum*f[f'GT_{i+1}']-1e-4:
                    device=f'GT_{i+1}'
                    self.log('minimum_output_interlock',device=device,actual_kW=power,minimum_kW=minimum*f[device])
                    self.env.trip_events.append(dict(device=device,start_minute=self.time,trip_time_minute=self.time,
                        step=self.env.steps_done,hold_minutes=0.,stress_ratio=None,trigger='minimum_output_interlock',
                        actual_kW=power,minimum_kW=minimum*f[device],initiating_faults=copy.deepcopy(self.env.scenario.get('faults',{}))))
                    self.state.extra_faulted.add(device);self.executed['gt_electric_kW'][i]=0.;f[device]=0.
            self._commit_sources(self.executed)

    def _request_local_down(self,sol):
        c=self.env.execution_contract
        if self.local_down is not None or c['grid_import_mode']!='arrived_ceiling' or c['gt_local_down_rate_kW_per_hour']<=0:return
        gap=self.executed['grid_import_kW']-sol['action']['grid_import_kW']-float(sol['lost'][:,0].sum())
        if gap<=1e-4:return
        _,row,_,f=self.data_at(self.time)
        minimum=.4*float(gt_level_properties(self.env.plant,float(row.outdoor_temperature_C))[0][-1])
        target=np.array(self.executed['gt_electric_kW'],float)
        # Fixed device order is a declared local regulator allocation, not a
        # re-run of either strategy. It cannot increase a source.
        for i in range(3):
            down=min(gap,max(0.,target[i]-minimum*f[f'GT_{i+1}']))
            target[i]-=down;gap-=down
        if np.max(np.array(self.executed['gt_electric_kW'])-target)>1e-4:
            self.local_down=dict(arrival=self.time+c['gt_local_down_delay_minutes'],target=target.tolist())
            self.log('local_down_requested',**self.local_down,rate_kW_per_hour=c['gt_local_down_rate_kW_per_hour'])

    def _local_interval_action(self,next_time,f,row):
        a=copy.deepcopy(self.executed);start=np.array(a['gt_electric_kW'],float);end=start.copy()
        c=self.env.execution_contract
        if self.local_down is not None and self.time>=self.local_down['arrival']-EPS:
            floor=.4*float(gt_level_properties(self.env.plant,float(row.outdoor_temperature_C))[0][-1])*np.array([f[f'GT_{i}'] for i in (1,2,3)])
            end=limited_down_endpoint(start,self.local_down['target'],floor,c['gt_local_down_rate_kW_per_hour'],next_time-self.time)
            a['gt_electric_kW']=((start+end)/2).tolist()
        return a,start,end

    def advance(self,end):
        e=self.env;start_segment=len(self.segments)
        while self.time<end-EPS:
            self.boundary()
            h,row,loads,f=self.data_at(self.time)
            self.state.disconnected_loads=np.minimum(loads,self.state.disconnected_loads)
            connected=np.maximum(0.,loads-self.state.disconnected_loads)
            self._surplus_interlock(connected,f,row)
            next_time=min(end,self._next_boundary(end))
            pre_state=clean(asdict(self.state))
            cmd=self.active_command or {}
            interval_action,source_start,source_end=self._local_interval_action(next_time,f,row)
            fixed_args=dict(execution_action=interval_action,tracking_targets=cmd.get('request',{}).get('targets',{}),
                            security_gt_penalty_USD_per_kWh=cmd.get('weight',0.))
            sol=self._solve(connected,f,(next_time-self.time)/60,'fixed_execution',cmd,**fixed_args)
            self._automatic_shed(loads,sol)
            changed=np.maximum(0.,loads-self.state.disconnected_loads)
            if not np.array_equal(changed,connected):
                connected=changed
                sol=self._solve(connected,f,(next_time-self.time)/60,'after_actual_shedding',cmd,**fixed_args)
            self._request_local_down(sol)
            revised=min(next_time,self._next_boundary(end))
            if revised<next_time-EPS:
                next_time=revised
                interval_action,source_start,source_end=self._local_interval_action(next_time,f,row)
                fixed_args['execution_action']=interval_action
                sol=self._solve(connected,f,(next_time-self.time)/60,'local_event_split',cmd,**fixed_args)
            # Actual converter delivery may be limited by fuel/steam/capacity.
            # Fixed electrical source constraints prevent a new dispatch.
            self._commit_sources(sol['action'],actual=True)
            self.state.auxiliary_request_kW=self.aux(sol['action'])
            enabled=e.scenario.get('enable_propagation',False)
            event_start,_=event_window(e.scenario,'fault' if e.scenario['stage'] in (1,3) else 'shock')
            stress={};debug={};new=[]
            if enabled and self.time>=event_start-EPS:
                crossing=self._ramp_threshold_boundary(connected,f,row,next_time,cmd,source_start,source_end)
                if crossing<next_time-1e-8:
                    next_time=crossing
                    interval_action,source_start,source_end=self._local_interval_action(next_time,f,row)
                    fixed_args['execution_action']=interval_action
                    sol=self._solve(connected,f,(next_time-self.time)/60,'protection_threshold_split',cmd,**fixed_args)
                    self._commit_sources(sol['action'],actual=True);self.state.auxiliary_request_kW=self.aux(sol['action'])
                # Zero-duration inspection cannot consume protection time.
                _,stress,debug=self._protection(connected,0.)
                threshold=float(e.config['gt_protection_trip_multiple'])
                hold=float(e.config['gt_overload_hold_minutes'])
                remaining=[max(0.,hold-self.state.overload_minutes[int(dev[-1])-1])
                    for dev,ratio in stress.items() if ratio>threshold+1e-9]
                if remaining and self.time+min(remaining)<next_time-EPS:
                    next_time=self.time+min(remaining)
                    if next_time<=self.time+EPS:raise RuntimeError('Uncommitted protection event at boundary')
                    interval_action,source_start,source_end=self._local_interval_action(next_time,f,row)
                    fixed_args['execution_action']=interval_action
                    sol=self._solve(connected,f,(next_time-self.time)/60,'fixed_execution',cmd,**fixed_args)
                    self._commit_sources(sol['action'],actual=True)
                    self.state.auxiliary_request_kW=self.aux(sol['action'])
                new,stress,debug=self._protection(connected,next_time-self.time)
            dt=(next_time-self.time)/60
            terminal=None
            if np.any(source_start-source_end>EPS):
                endpoint=copy.deepcopy(self.executed);endpoint['gt_electric_kW']=source_end.tolist()
                terminal=self._solve(connected,f,dt,'local_down_endpoint',cmd,execution_action=endpoint,
                    tracking_targets=cmd.get('request',{}).get('targets',{}),security_gt_penalty_USD_per_kWh=cmd.get('weight',0.))
            lost=sol['lost']+self.state.disconnected_loads
            self.state.diesel_energy_remaining_kWh=max(0.,self.state.diesel_energy_remaining_kWh-sol['action']['diesel_kW']*dt)
            params,byhour=e.thermal_cache
            temp,viol=update_thermal(self.state,params,byhour[h],lost,loads,dt,float(e.config['thermal_setpoint_margin_C']),attribution=self.thermal_attribution(self.time))
            cls=p0_failure_classification(lost,loads,viol['P0'],e.config,dt,e.cumulative_p0)
            e.cumulative_p0=cls['cumulative_effective_energy_kWh']
            weight=self.active_command['weight'] if self.active_command else 0.
            seg=dict(start_minute=self.time,end_minute=next_time,step=e.steps_done,hour_of_year=h,
                command_id=self.active_command['id'] if self.active_command else None,
                execution_mode='finite_local_down' if np.any(source_start-source_end>EPS) else 'held_executed_setpoints',availability=f,pre_state=pre_state,
                source_start_gt_kW=source_start,source_end_gt_kW=source_end,
                arrived_source_setpoints=copy.deepcopy(self.executed),execution_contract=e.execution_contract['name'],
                auxiliary_electric_kW=self.aux(sol['action']),thermal_attribution=self.thermal_attribution(self.time),
                loads_kW=loads,connected_loads_kW=connected,disconnected_loads_kW=self.state.disconnected_loads.copy(),
                unserved_kW=lost,action=sol['action'],cost_USD_step=sol['cost_USD_step'],
                tracking_penalty_USD_step=sol['tracking_penalty_USD_step'],
                security_objective_penalty_USD_step=weight*sum(sol['action']['gt_electric_kW'])*dt,
                energy_balance_residuals=sol['energy_balance_residuals'],steam_balance_residual_lbph=sol['steam_balance_residual_lbph'],
                protection_stress=stress,protection_debug=debug,post_state=clean(asdict(self.state)),
                temperature=temp,violation=viol,classification=cls,optimizer_warning=sol.get('optimizer_warning',''))
            self.segments.append(clean(seg))
            self.executed['gt_electric_kW']=source_end.tolist()
            self.state.gt_prev=source_end.copy()
            if terminal is not None:
                self._commit_sources(terminal['action'],actual=True)
                self.state.auxiliary_request_kW=self.aux(terminal['action'])
                self.segments[-1]['terminal_action']=clean(terminal['action'])
            if self.local_down is not None and np.max(np.abs(source_end-np.array(self.local_down['target'])))<=1e-6:
                self.events.append(clean(dict(event_id=len(self.events)+1,kind='local_down_completed',
                    time_minute=next_time,target=self.local_down['target'],completed_minute=next_time)))
                self.local_down=None
            self.segments[-1]['post_state']=clean(asdict(self.state))
            self.time=next_time
            for device in new:
                if device in self.state.extra_faulted:continue
                self.state.extra_faulted.add(device)
                ev=dict(device=device,start_minute=self.time-float(e.config['gt_overload_hold_minutes']),
                        trip_time_minute=self.time,step=e.steps_done,stress_ratio=stress[device],
                        hold_minutes=float(e.config['gt_overload_hold_minutes']),
                        protection_reference_kW=float(e.plant.gt_rated_kW),
                        trigger='continuous_residual_stress_surrogate',initiating_faults=copy.deepcopy(e.scenario.get('faults',{})))
                e.trip_events.append(ev);self.log('protection_trip',**ev)
            # Next boundary applies and logs forced setpoint changes. Do not
            # mutate output silently here before the boundary handler sees it.
        self.boundary()
        return self.segments[start_segment:]

def aggregate_segments(segments,dt_h):
    """Return interval-average powers and integrated costs, never last power*dt."""
    weights=np.array([(s['end_minute']-s['start_minute'])/60/dt_h for s in segments])
    def mean(key):return sum(np.asarray(s[key])*w for s,w in zip(segments,weights))
    action={}
    for key in segments[0]['action']:
        val=sum(np.asarray(s['action'][key])*w for s,w in zip(segments,weights))
        action[key]=val.tolist() if np.ndim(val) else float(val)
    lost=mean('unserved_kW');loads=mean('loads_kW')
    return dict(action=action,lost=lost,loads=loads,served=loads-lost,
        cost_USD_step=sum(s['cost_USD_step'] for s in segments),
        tracking_penalty_USD_step=sum(s['tracking_penalty_USD_step'] for s in segments),
        security_objective_penalty_USD_step=sum(s['security_objective_penalty_USD_step'] for s in segments),
        energy_balance_residuals=np.max(np.abs([s['energy_balance_residuals'] for s in segments]),axis=0).tolist(),
        steam_balance_residual_lbph=max(abs(s['steam_balance_residual_lbph']) for s in segments),
        optimizer_warning=';'.join(sorted(set(s['optimizer_warning'] for s in segments if s['optimizer_warning']))))
