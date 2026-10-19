"""M15 finite diesel mechanics over the unchanged M14 GT/queue contract."""
from __future__ import annotations
from dataclasses import asdict
import copy
import math
import time as clock
import numpy as np
from hospital_ies.execution import ModelConstraintInfeasible,maximum_auxiliary_sink
from hospital_ies.model import gt_level_properties
from hospital_ies.state_transition import clean,event_window,EPS
from hospital_ies.resilience import update_thermal,p0_failure_classification
from .diesel import DieselRotor
from .physical_balance import solve_physical
from .thermal_readiness import next_hrsg_ready_minute

MODEL_ID='M15_local_response'
DYNAMICS_CONTRACT_ID='hospital_ies.operating_response.v15.v1'

class LocalResponseDomainError(ModelConstraintInfeasible):
    category='local_response_domain_exceeded'

class LocalResponseMixin:
    def __init__(self,env,initial_action):
        self.diesel_rotor=DieselRotor(rated_kW=env.plant.emergency_diesel_kW,
            config=env.local_response_config)
        super().__init__(env,initial_action)
        self.log('local_diesel_model_initialized',model_id=MODEL_ID,
            port_semantics='arrived enable/ceiling; Pe differs from Pm',
            rotor=self.diesel_rotor.as_dict(),
            fuel_semantics='same numeric kWh-electric-equivalent budget debited by modeled shaft work; not liters')

    def _commit_sources(self,a,actual=False):
        previous=float(self.state.last_diesel_kW)
        super()._commit_sources(a,actual=actual)
        if not actual:
            self.state.last_diesel_kW=min(previous,max(0.,float(a['diesel_kW'])))

    def preview_diesel(self,seconds,electrical_kW,*,connected=None):
        trial=copy.deepcopy(self.diesel_rotor)
        status=self.state.diesel_status
        connected=(status=='CONNECTED') if connected is None else bool(connected)
        if status in ('STARTING','READY'):
            start=self.state.diesel_start_minute
            delay=float(self.env.config['diesel_start_delay_minutes'])*60.
            age=max(0.,self.time*60.-float(start)*60.) if start is not None else 0.
            a=min(1.,age/max(delay,1e-9));b=min(1.,(age+seconds)/max(delay,1e-9))
            receipt=trial.spin_up(seconds,a,b)
        else:
            receipt=trial.advance(seconds,float(electrical_kW),connected=connected,
                run_enabled=connected and self.state.diesel_energy_remaining_kWh>EPS)
        return trial,receipt

    def _solve(self,loads,f,dt_h,phase,action,**kwargs):
        e=self.env;_,row,_,_=self.data_at(self.time);began=clock.perf_counter()
        try:
            result=solve_physical(row,e.plant,loads,f,self.state,dt_h,
                float(e.scenario.get('grid_limit_kW',e.config['grid_import_limit_kW'])),
                self.diesel_available(self.time,f),float(e.config['gt_ramp_kW_per_hour']),
                float(e.config['solver_time_limit_sec']),e.config,
                execution_contract=getattr(e,'operating_execution_contract',e.execution_contract),**kwargs)
            self.solver_calls.append(dict(time_minute=self.time,phase=phase,dt_h=dt_h,
                elapsed_seconds=clock.perf_counter()-began,status='success',
                optimizer_warning=result.get('optimizer_warning',''),
                solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers',[]))))
            return result
        except Exception as exc:self._record_error(exc,loads,f,dt_h,phase,action,began)

    def _rearm_allowed(self,i,dispatch,f):
        minimum_diesel=copy.deepcopy(dispatch);minimum_diesel['diesel_kW']=0.
        return super()._rearm_allowed(i,minimum_diesel,f)

    def _surplus_interlock(self,loads,f,row):
        """Only noncurtailable GT is isolated; DG Pe has its own finite-energy test."""
        c=self.env.execution_contract
        if c['surplus_interlock']=='none':return
        while True:
            began=clock.perf_counter()
            try:aux=maximum_auxiliary_sink(self.env.plant,row,loads,f,self.executed)
            except Exception as exc:self._record_error(exc,loads,f,0.,'surplus_diagnostic',self.active_command,began)
            supply=sum(self.executed['gt_electric_kW'])
            if c['grid_import_mode']=='fixed':supply+=self.executed['grid_import_kW']
            surplus=supply-float(loads[:,0].sum())-aux
            if surplus<=1e-4:break
            if c['surplus_interlock_delay_minutes']>0:return
            i=int(np.argmax(self.executed['gt_electric_kW']))
            if self.executed['gt_electric_kW'][i]<=EPS:return
            self._latch_gt(i,f,'no_export_interlock',power_surplus_kW=surplus,
                connected_electric_kW=float(loads[:,0].sum()),maximum_real_auxiliary_kW=aux,
                diesel_minimum_electric_kW=0.,diesel_arrived_ceiling_kW=self.executed['diesel_kW'])
        floor=.4*float(gt_level_properties(self.env.plant,float(row.outdoor_temperature_C))[0][-1])
        if c['surplus_interlock_delay_minutes']==0:
            for i,p in enumerate(list(self.executed['gt_electric_kW'])):
                if EPS<p<floor*f[f'GT_{i+1}']-1e-4:
                    self._latch_gt(i,f,'minimum_output_interlock',actual_kW=p,minimum_kW=floor*f[f'GT_{i+1}'])

    def _latch_gt(self,i,f,reason,**detail):
        dev=f'GT_{i+1}';before=copy.deepcopy(self.executed)
        self.executed['gt_electric_kW'][i]=0.;f[dev]=0.
        self.gt_start_pending.pop(i,None)
        self._transition_reason='forced_algebraic_interlock'
        self._commit_sources(self.executed)
        self.interlock_latched[dev]=dict(reason=reason,latched_minute=self.time,
            reset_eligible_minute=self.time+self.env.engineering_config['interlock_rearm_wait_minutes'])
        self.log(reason,device=dev,before=before,after=self.executed,**detail)
        self.log('algebraic_interlock_latched',device=dev,latch=self.interlock_latched[dev],
            hardware_failure_asserted=False,automatic_restart=False,true_protection_cleared=False)
        self.env.trip_events.append(dict(device=dev,start_minute=self.time,trip_time_minute=self.time,
            step=self.env.steps_done,hold_minutes=0.,stress_ratio=None,trigger=reason,
            initiating_faults=copy.deepcopy(self.env.scenario.get('faults',{})),**detail))

    def _limit_by_diesel(self,seconds,electrical_kW,connected):
        trial,receipt=self.preview_diesel(seconds,electrical_kW,connected=connected)
        safe=float(receipt['actual_seconds'])
        fuel=float(self.state.diesel_energy_remaining_kWh)
        if receipt['mechanical_work_kWh']>fuel+1e-9:
            lo=0.;hi=safe
            for _ in range(36):
                mid=(lo+hi)/2
                _,r=self.preview_diesel(mid,electrical_kW,connected=connected)
                if r['mechanical_work_kWh']<=fuel:lo=mid
                else:hi=mid
            trial,receipt=self.preview_diesel(lo,electrical_kW,connected=connected)
            receipt['fuel_budget_domain_exhausted']=True
            safe=lo
        return safe,trial,receipt

    def validate_electrical_path(self,source_start,source_end,connected,arrived_ceiling_kW):
        # Even a zero average Pe can conceal a positive endpoint as the GT
        # ramps. Reject the whole coupled path whenever DG could participate.
        if connected and arrived_ceiling_kW>EPS and np.any(np.abs(np.asarray(source_start)-np.asarray(source_end))>EPS):
            raise LocalResponseDomainError('Simultaneous continuous GT ramp and DG terminal path unresolved; interval average is not a speed trajectory')

    def observe(self):
        value=super().observe()
        value['diesel_local_response']=dict(model_id=MODEL_ID,rotor=self.diesel_rotor.as_dict(),
            actual_electric_kW=float(self.state.last_diesel_kW),
            arrived_ceiling_kW=float(self.executed['diesel_kW']),
            permission_status=self.state.diesel_status)
        return value

    # advance/_next_boundary are the auditable bounded native forks appended
    # by review/build_execution_fork.py, with local preview/commit explicit.

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
        candidates.extend(p['started'] for p in self.gt_start_pending.values() if p['started']>self.time+EPS)
        thermal_cfg=getattr(self.env,'hrsg_thermal_config',{})
        if thermal_cfg.get('warmup_delay_minutes',0.)>0.:
            thermal_clock=getattr(self,'gt_clocks',None)
            thermal_ready=next_hrsg_ready_minute(time_minute=self.time,
                gt_running=None if thermal_clock is None else thermal_clock.running.tolist(),
                last_actual_on_minute=None if thermal_clock is None else thermal_clock.last_on.tolist(),
                config=thermal_cfg)
            if thermal_ready is not None:candidates.append(thermal_ready)
        return min(x for x in candidates if x>self.time+EPS)

    def advance(self,end):
        e=self.env;start_segment=len(self.segments)
        while self.time<end-EPS:
            self.boundary()
            if getattr(e,'hrsg_thermal_config',{}).get('warmup_delay_minutes',0.)>0.:
                thermal=self.hrsg_thermal_state(self.time)
                key=[(d['GT_running'],d['last_actual_on_minute'],d['effective_availability'],d['reason']) for d in thermal['devices']]
                if key!=getattr(self,'_last_hrsg_thermal_key',None):
                    self.log('hrsg_thermal_readiness_changed',research_model_sensitivity=True,
                        exogenous_fault=False,true_protection=False,thermal=thermal)
                    self._last_hrsg_thermal_key=key
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
            try:self.validate_electrical_path(source_start,source_end,self.diesel_available(self.time,f),interval_action['diesel_kW'])
            except LocalResponseDomainError as exc:
                self._record_error(exc,connected,f,0.,'diesel_terminal_path_domain',cmd,clock.perf_counter())
            # Trial only: physical/protection probes must not advance mechanics.
            local_domain_boundary=None
            for local_attempt in range(8):
                span=(next_time-self.time)*60.
                safe,_,local_receipt=self._limit_by_diesel(span,sol['action']['diesel_kW'],
                    self.diesel_available(self.time,f))
                if local_receipt['domain_exceeded'] or local_receipt.get('fuel_budget_domain_exhausted'):
                    local_domain_boundary=dict(minute=self.time+safe/60.,
                        domain_exceeded=bool(local_receipt['domain_exceeded']),
                        fuel_budget_domain_exhausted=bool(local_receipt.get('fuel_budget_domain_exhausted')))
                if safe>=span-1e-7:break
                if safe<=1e-8:
                    self.log('local_diesel_domain_exceeded',receipt=local_receipt)
                    self._record_error(LocalResponseDomainError('No positive valid local diesel interval'),
                        connected,f,0.,'diesel_local_dynamic_domain',cmd,clock.perf_counter())
                next_time=self.time+safe/60.
                interval_action,source_start,source_end=self._local_interval_action(next_time,f,row)
                fixed_args['execution_action']=interval_action
                sol=self._solve(connected,f,(next_time-self.time)/60,'diesel_domain_split',cmd,**fixed_args)
            else:raise RuntimeError('Finite local diesel split did not converge')
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
            # Commit only the final, event-split actual interval. Original
            # electrical service/cost definitions are retained separately.
            safe,rotor,local_receipt=self._limit_by_diesel(dt*3600.,sol['action']['diesel_kW'],
                self.diesel_available(self.time,f))
            if safe<dt*3600.-1e-6:
                raise RuntimeError('Uncommitted local dynamic interval after protection split')
            # Re-previewing exactly the valid prefix may no longer report the
            # attempted crossing. Preserve its cause only if the final physical
            # interval actually reaches it (a prior protection split does not).
            if local_domain_boundary is not None and abs(next_time-local_domain_boundary['minute'])<=1e-8:
                local_receipt['domain_exceeded']|=local_domain_boundary['domain_exceeded']
                if local_domain_boundary['fuel_budget_domain_exhausted']:
                    local_receipt['fuel_budget_domain_exhausted']=True
            self.diesel_rotor=rotor
            debit=float(local_receipt['mechanical_work_kWh'])
            self.state.diesel_energy_remaining_kWh=max(0.,self.state.diesel_energy_remaining_kWh-debit)
            local_receipt['fuel_budget_debit_kWh_equivalent']=debit
            local_receipt['arrived_ceiling_kW']=float(interval_action['diesel_kW'])
            local_receipt['actual_terminal_kW']=float(sol['action']['diesel_kW'])
            local_receipt['original_electric_fee_USD']=.30*float(local_receipt['electrical_work_kWh'])
            local_receipt['shaft_equivalent_fee_USD']=.30*debit
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
            seg['diesel_local_response']=clean(local_receipt)
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
            if local_receipt['domain_exceeded'] or local_receipt.get('fuel_budget_domain_exhausted'):
                self.log('local_diesel_domain_exceeded',receipt=local_receipt,
                    reason='speed validity domain or explicitly exhausted modeled shaft-work budget')
                self._record_error(LocalResponseDomainError('Finite diesel response left registered validity domain'),
                    connected,f,0.,'diesel_local_dynamic_domain',cmd,clock.perf_counter())
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
