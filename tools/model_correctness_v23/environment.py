"""M23 opt-in causal correction of queued preparation and actual event closure."""
from __future__ import annotations
import copy
import math
from pathlib import Path
import json
import numpy as np
from hospital_ies.state_transition import EPS
from tools.engineering_model.environment import isolated
from tools.model_closure_v22 import environment as base
from tools.sci_engineering import environment as sci
from tools.operating_response import environment as operating
from . import MODEL_ID,CONTRACT_ID
from .protection import RestartState
from .evidence import DomainEvidenceStore,evidence_scope,all_rows_solver_log

validate_scenario=sci.validate_scenario
validate_events=base.validate_events
DEFAULTS=dict(preparation=True,storage_fix=True,boiler_domain_search=True,thermal_coupling=None)

def correctness_config(value=None):
    if not isinstance(value or {},dict) or set(value or {})-set(DEFAULTS):raise ValueError('Unknown M23 correctness configuration')
    result=dict(DEFAULTS,**copy.deepcopy(value or {}))
    for name in ('preparation','storage_fix','boiler_domain_search'):
        if type(result[name]) is not bool:raise ValueError('M23 switches must be boolean')
    thermal=result['thermal_coupling']
    if thermal is not None and (not isinstance(thermal,dict) or set(thermal)-{'reference_csv','parameters_json','max_recovery_kW','recovery_horizon_h'} or not {'reference_csv','parameters_json'}<=set(thermal)):
        raise ValueError('M23 thermal coupling requires reference_csv and parameters_json')
    return result

def close_storage_interval(start_minute,end_minute,solve,stores,*,initial_result=None,max_probes=48):
    """Locate a storage boundary with the powers re-solved for that duration.

    Trials are pure; the same store snapshot is read throughout. Bounds are
    the next real event/command/native integration boundary, never a new
    upper controller decision. The original energy tolerances are retained.
    """
    maximum=float(end_minute-start_minute)
    if maximum<=0.:raise ValueError('Positive actual interval required')
    def preview(span,result):
        target=maximum;crossed=False;residuals={}
        for prefix,store in stores:
            a=result['action'];d=float(a.get(prefix+'_discharge_kW',0.));c=float(a.get(prefix+'_charge_kW',0.));cfg=store.config
            energy=store.energy_kWh*math.exp(-cfg['loss_per_hour']*span/60.)+(c*cfg['charge_efficiency']-d/cfg['discharge_efficiency'])*span/60.
            residuals[prefix]=energy
            crossed|=energy < -1e-10 or energy > cfg['capacity_kWh']+1e-10
            boundary=store.boundary_hours(discharge_kW=d,charge_kW=c,max_hours=maximum/60.)*60.
            target=min(target,boundary)
        return target,crossed,residuals
    span=maximum;low=0.;high=maximum;trials=[];result=initial_result
    for i in range(max_probes):
        if result is None:result=solve(span)
        target,crossed,residuals=preview(span,result)
        trials.append(dict(duration_minutes=span,predicted_boundary_minutes=target,projected_inventory_kWh=residuals,crossed=crossed))
        if not crossed and (target>=maximum-1e-10 or abs(target-span)<=1e-9):
            return start_minute+span,result,dict(converged=True,probe_count=len(trials),probes=trials,inventory_committed=False)
        if crossed:high=min(high,span)
        else:low=max(low,span)
        if high-low<=1e-9:
            # Settle the safe side with one final fresh solve; no inventory
            # is rounded or removed by this locator.
            span=low;result=solve(span);target,crossed,residuals=preview(span,result)
            if not crossed:return start_minute+span,result,dict(converged=True,probe_count=len(trials)+1,probes=trials,inventory_committed=False)
        candidate=target if low+1e-12<target<high-1e-12 else (low+high)/2.
        if candidate<=1e-12:raise RuntimeError('M23 no positive storage-consistent interval')
        span=candidate;result=None
    raise RuntimeError('M23 storage event/power closure did not converge within its finite bound')

class EngineeringCore(base.EngineeringCore):
    def __init__(self,env,initial_action):
        self._m23_thermal_prepared=None
        self._m23_event_closure=None
        super().__init__(env,initial_action)
        if env.enable_all_validation_rows:
            self.solver_calls=all_rows_solver_log(self.solver_calls,env.domain_evidence)
        self._m23_restart=RestartState(restart_minutes=self.station.config['boiler_restart_minutes'],restart_power_kW=self.station.config['boiler_fixed_kW'],ready=self.station.boiler_ready,status='ready' if self.station.boiler_ready else 'dispatch_stopped')

    def _current_context(self,f):
        value=super()._current_context(f)
        if sum(f[f'BOILER_{i}'] for i in (1,2,3))<=EPS:value['boiler_restart_kW']=0.
        if self.station.config['boiler_restart_minutes']==0.:
            value['boiler_ready']=self._zero_restart_eligible(f)
            value['boiler_restart_kW']=0.
        coupling=getattr(self.env,'thermal_coupling',None)
        if coupling is not None:
            _,_,loads,_=self.data_at(self.time)
            prepared=coupling.prepare_current({'time_minute':self.time},loads)
            self._m23_thermal_prepared=prepared
            value.update(recovery_cooling_request_kW=prepared['recovery_cooling_request_kW'],recovery_pump_kW_per_kW=.02,recovery_fan_kW_per_kW=.1)
        return value

    def _zero_restart_eligible(self,f):
        if self.station.config['boiler_restart_minutes']!=0. or f['STATION_AC']<1.-EPS:return False
        if float(self.arrived_dispatch.get('boiler_steam_lbph',0.))<=EPS or sum(f[f'BOILER_{i}'] for i in (1,2,3))<=EPS:return False
        source=sum(float(power)*float(f[f'GT_{i+1}']) for i,power in enumerate(self.executed['gt_electric_kW']))
        if self.grid_bus_state=='GRID' and f['GRID']>EPS:source+=float(self.executed['grid_import_kW'])
        if self.diesel_available(self.time,f):source+=float(self.executed['diesel_kW'])
        return source>EPS

    def _boiler_restart_boundary(self):
        cfg=self.station.config
        if cfg['boiler_restart_minutes']==0.:return None
        return self.time+max(1e-6,cfg['boiler_restart_minutes']-self.station.boiler_powered_minutes)

    def boundary(self):
        super().boundary()
        if self.station.config['boiler_restart_minutes']==0.:
            eligible=self._zero_restart_eligible(self.data_at(self.time)[-1])
            self.station.boiler_ready=eligible;self.station.boiler_powered_minutes=0.
            self._m23_restart=RestartState(restart_minutes=0.,restart_power_kW=self.station.config['boiler_fixed_kW'],
                ready=eligible,status='ready' if eligible else 'functional_unavailable')

    def _raise_evidence_io_error(self,exc):
        if isinstance(exc,OSError):raise exc

    def _physical_solver_config(self,dt_h,f):
        cfg=super()._physical_solver_config(dt_h,f)
        if self.env.correctness_config['boiler_domain_search']:cfg['_model_correctness_v23']={'preparation':self.env.correctness_config['preparation']}
        return cfg

    def _solve(self,*args,**kwargs):
        with evidence_scope(self.env.domain_evidence):return super()._solve(*args,**kwargs)

    def non_diesel_witness(self):
        from .release import non_diesel_witness
        with evidence_scope(self.env.domain_evidence):return non_diesel_witness(self)

    def _electric_sink_upper(self,loads,f,row):
        value=super()._electric_sink_upper(loads,f,row)
        inherited=value.get('maximum_held_station_auxiliary_kW',0.)
        context=self._current_context(f);actual=0.
        if context['station_ac_available']:
            actual=context['gt_start_kW']+context['boiler_restart_kW']
            permission=float(self.arrived_dispatch.get('boiler_steam_lbph',0.))
            absorption=float(self.executed.get('absorption_cooling_kW',0.))
            from hospital_ies.model import RT_TO_KW
            useful=float(loads[:,1].sum())/(self.env.plant.steam_kWh_per_lb*self.env.plant.exchanger_eff)+absorption/RT_TO_KW*self.env.plant.abs_steam_lb_per_rt
            steam=min(permission,useful,self.env.plant.boiler_steam_lbph/3.*sum(f[f'BOILER_{i}'] for i in (1,2,3)))
            if context['boiler_ready'] and steam>EPS:actual+=context['boiler_fixed_kW']+context['boiler_kW_per_kWsteam']*steam*self.env.plant.steam_kWh_per_lb
        value['upper_kW']+=actual-inherited
        value['maximum_held_station_auxiliary_kW']=actual
        value['conditional_permission_alone_is_station_sink']=False
        return value

    def _prepare_command_dispatch(self,dispatch,diagnostic,loads):
        value=copy.deepcopy(dispatch);diagnostic=copy.deepcopy(diagnostic)
        old=value['boiler_steam_lbph'];critical=float(loads[:2,1].sum())
        extra=0.;extra_headroom=0.
        if self.env.thermal_coupling is not None:
            prepared=self.env.thermal_coupling.prepare_current({'time_minute':self.time},loads)
            extra=float(prepared['recovery_cooling_request_kW'])
        if self.env.correctness_config['preparation']:
            value['boiler_steam_lbph']=max(old,min(self.env.plant.boiler_steam_lbph,critical/(self.env.plant.steam_kWh_per_lb*self.env.plant.exchanger_eff)))
            # Existing EC/ABS commands already are independent ceilings.
            # Retain current critical cold preparation across present faults;
            # actual hardware/flow/source constraints still govern execution.
            residual=float(loads[:2,2].sum())
            for i,capacity in enumerate(self.env.plant.electric_chillers_rt):
                from hospital_ies.model import RT_TO_KW
                request=min(residual,capacity*RT_TO_KW)
                value['electric_chiller_cooling_kW'][i]=max(value['electric_chiller_cooling_kW'][i],request)
                residual=max(0.,residual-request)
            # This is a new ordinary queued permission derived from current
            # thermal debt. It grants no additional arrived local authority.
            residual=extra
            for i,capacity in enumerate(self.env.plant.electric_chillers_rt):
                addition=min(residual,max(0.,capacity*RT_TO_KW-value['electric_chiller_cooling_kW'][i]))
                value['electric_chiller_cooling_kW'][i]+=addition
                residual-=addition;extra_headroom+=addition
        diagnostic['m23_preparation']=dict(enabled=self.env.correctness_config['preparation'],current_critical_heat_kW=critical,
            original_boiler_target_lbph=old,queued_boiler_ceiling_lbph=value['boiler_steam_lbph'],
            current_extra_recovery_request_kW=extra,queued_extra_converter_headroom_kW=extra_headroom,
            conditional_permission_is_actual_supply=False,ordinary_queue_and_decision_clock_unchanged=True,
            future_repair_information_used=False,boiler_semantics='arrived_conditional_ceiling')
        return value,diagnostic

    def _cold_mode(self,deficit):
        if deficit>.01:
            return 'discharge' if self.cold_store.energy_kWh>1e-9 else 'idle'
        return 'charge' if self.cold_recharge.allowed(self.cold_store.energy_kWh,self.cold_store.config['capacity_kWh']) else 'idle'

    def _choose_storage_candidate(self,candidates):
        from .domains import select_candidate
        result=select_candidate([candidate for candidate,_ in candidates])
        return next(item for item in candidates if item[0] is result)

    def _select_current_ports(self,loads,f):
        # Inherited implementation calls the shared selectable helper below.
        with evidence_scope(self.env.domain_evidence):return super()._select_current_ports(loads,f)

    def _next_boundary(self,end):
        previous=getattr(self,'_storage_candidate',None)
        if self.env.correctness_config['storage_fix']:self._storage_candidate=None
        try:minute=super()._next_boundary(end)
        finally:
            if self.env.correctness_config['storage_fix']:self._storage_candidate=previous
        coupling=getattr(self.env,'thermal_coupling',None)
        if coupling is not None:
            boundary=coupling.next_reference_boundary(self.time)
            if boundary is not None:minute=min(minute,boundary)
        return minute

    def _close_actual_interval(self,end,next_time,connected,f,row,cmd,sol,fixed_args):
        bound=min(next_time,self._next_boundary(end))
        saved={}
        def solve(span):
            minute=self.time+span
            interval,start,finish=self._local_interval_action(minute,f,row)
            args=dict(fixed_args,execution_action=interval)
            result=self._solve(connected,f,span/60.,'m23_event_power_probe',cmd,**args)
            saved[minute]=(interval,start,finish)
            return result
        interval,start,stop=self._local_interval_action(bound,f,row)
        initial=sol if bound==next_time and interval==fixed_args['execution_action'] else None
        if initial is not None:saved[bound]=(interval,start,stop)
        if self.env.correctness_config['storage_fix']:
            finish,result,receipt=close_storage_interval(self.time,bound,solve,[('ups',self.ups),('cold',self.cold_store)],initial_result=initial)
            self._m23_event_closure=receipt
        else:finish=bound;result=initial if initial is not None else solve(bound-self.time);self._m23_event_closure=None
        interval,start,stop=saved[finish]
        fixed_args['execution_action']=interval
        return finish,result,interval,start,stop

    def _after_actual_segment(self,segment,result):
        before=RestartState(restart_minutes=self.station.config['boiler_restart_minutes'],restart_power_kW=self.station.config['boiler_fixed_kW'],
            ready=self.station.boiler_ready,continuous_powered_minutes=self.station.boiler_powered_minutes,
            protective_latch=getattr(self,'_m23_restart',RestartState()).protective_latch,
            status=getattr(self,'_m23_restart',RestartState()).status)
        super()._after_actual_segment(segment,result)
        a=segment['action'];f=segment['availability'];minutes=segment['end_minute']-segment['start_minute']
        source=sum(a['gt_electric_kW'])+a['grid_import_kW']+a['diesel_kW']
        request=before.request(permission_arrived=float(self.arrived_dispatch.get('boiler_steam_lbph',0.))>EPS,
            source_available=bool(f['STATION_AC']>=1.-EPS and source>EPS),device_available=bool(sum(f[f'BOILER_{i}'] for i in (1,2,3))>EPS))
        self._m23_restart=before.commit(minutes,request,restart_power_supplied=bool(a.get('boiler_restart_electric_kW',0.)>=self.station.config['boiler_fixed_kW']-EPS))
        self.station.boiler_ready=self._m23_restart.ready;self.station.boiler_powered_minutes=self._m23_restart.continuous_powered_minutes
        segment['m22_station'].update(boiler_ready=self.station.boiler_ready,boiler_continuous_power_minutes=self.station.boiler_powered_minutes)
        segment['m23_restoration']=self._m23_restart.observe()
        segment['m23_event_closure']=copy.deepcopy(self._m23_event_closure)
        segment['m23_boiler_domains']=copy.deepcopy(result.get('m23_boiler_domains'))
        coupling=getattr(self.env,'thermal_coupling',None)
        if coupling is not None:
            segment['m23_temperature_diagnostic']=coupling.commit_actual(self._m23_thermal_prepared,minutes/60.,float(result['served'][0,2]),
                float(a.get('recovery_cooling_kW',0.)),actual_priority_heating_kW=float(result['served'][0,1]))
        segment.update(execution_model_id=MODEL_ID,model_correctness_contract_id=CONTRACT_ID)

    def generate_command(self,action):
        with evidence_scope(self.env.domain_evidence):command=super().generate_command(action)
        command['execution_model_id']=MODEL_ID
        for event in reversed(self.events):
            if event.get('kind')=='command_generated' and event.get('command',{}).get('id')==command['id']:
                event['command']['execution_model_id']=MODEL_ID
                break
        return command

    def advance(self,end):
        first=len(self.segments)
        self._m23_advance_depth=getattr(self,'_m23_advance_depth',0)+1
        try:
            with evidence_scope(self.env.domain_evidence):return super().advance(end)
        finally:
            # The inherited outer wrappers own M21/M22 metadata. This outer
            # version boundary binds every completed actual prefix to M23.
            for segment in self.segments[first:]:segment.update(execution_model_id=MODEL_ID,model_correctness_contract_id=CONTRACT_ID)
            self._m23_advance_depth-=1
            if self._m23_advance_depth==0 and self.env.actual_segment_observer is not None:
                for segment in self.segments[getattr(self,'_m23_observed_segments',0):]:
                    self.env.actual_segment_observer(segment)
                    self._m23_observed_segments=getattr(self,'_m23_observed_segments',0)+1

    def observe(self):
        value=super().observe();value['sci_model_id']=MODEL_ID
        value['model_correctness_v23']=dict(observation_time_minute=self.time,available_time_minute=self.time,
            conditional_preparation_enabled=self.env.correctness_config['preparation'],storage_event_fix_enabled=self.env.correctness_config['storage_fix'],
            boiler_dispatch_mode='arrived_conditional_ceiling')
        return value

class OperatingHospitalIESEnv(base.OperatingHospitalIESEnv):
    def __init__(self,root,*,allowed_hours,sealed_days,model_id=MODEL_ID,model_config=None,solver_evidence_directory=None,actual_segment_observer=None,enable_all_validation_rows=False,**kwargs):
        if model_id!=MODEL_ID:raise ValueError('Explicit M23 model identity required')
        config=copy.deepcopy(model_config or {});self.correctness_config=correctness_config(config.pop('model_correctness_v23',{}))
        if actual_segment_observer is not None and not callable(actual_segment_observer):raise TypeError('Actual segment observer must be callable')
        self.actual_segment_observer=actual_segment_observer
        if type(enable_all_validation_rows) is not bool:raise TypeError('All-row logging switch must be boolean')
        if enable_all_validation_rows and solver_evidence_directory is None:raise ValueError('All-row logging requires an evidence directory')
        self.enable_all_validation_rows=enable_all_validation_rows
        self.domain_evidence=DomainEvidenceStore(solver_evidence_directory,enable_all_validation_rows=enable_all_validation_rows) if solver_evidence_directory is not None else None
        self.thermal_coupling=None;self._thermal_initial=None
        super().__init__(root,allowed_hours=allowed_hours,sealed_days=sealed_days,model_id=base.MODEL_ID,model_config=config,**kwargs)
        self.model_id=MODEL_ID;self.operating_model_config['model_correctness_v23']=copy.deepcopy(self.correctness_config)
        self.operating_execution_contract.update(model_correctness_contract_id=CONTRACT_ID,boiler_dispatch_mode='arrived_conditional_ceiling')
        if self.correctness_config['boiler_domain_search']:self.config['_model_correctness_v23']={'preparation':self.correctness_config['preparation']}
        thermal=self.correctness_config['thermal_coupling']
        if thermal is not None:
            from .thermal_coupling import ThermalCoupling,load_reference_baseline
            reference_path=Path(thermal['reference_csv']);parameter_path=Path(thermal['parameters_json'])
            if not reference_path.is_absolute():reference_path=Path(root)/reference_path
            if not parameter_path.is_absolute():parameter_path=Path(root)/parameter_path
            import pandas as pd
            frame=pd.read_csv(reference_path)
            reference={float(minute):{row['zone']:dict(heating_kW=float(row['heating_kW']),cooling_kW=float(row['cooling_kW'])) for row in rows.to_dict('records')} for minute,rows in frame.groupby('elapsed_interval_start_minute')}
            self._thermal_initial=dict(reference=reference,parameters=json.loads(parameter_path.read_text(encoding='utf-8')),
                max_recovery_kW=thermal.get('max_recovery_kW',100.),recovery_horizon_h=thermal.get('recovery_horizon_h',1.))
            self.thermal_coupling=ThermalCoupling(**copy.deepcopy(self._thermal_initial))

    def reset(self,*,scenario,seed=None):
        if self._thermal_initial is not None:
            from .thermal_coupling import ThermalCoupling
            self.thermal_coupling=ThermalCoupling(**copy.deepcopy(self._thermal_initial))
        original=copy.deepcopy(scenario);self.model_closure_events=validate_events(original.pop('model_closure_events',[]))
        for key in ('_model_closure_v22','_sci_storage_ports'):self.config.pop(key,None)
        reset=isolated(sci.OperatingHospitalIESEnv.reset,dict(sci.OperatingHospitalIESEnv.reset.__globals__,EngineeringCore=EngineeringCore))
        value=reset(self,scenario=original,seed=seed)
        return value

def decision_snapshot(env):
    value=base.decision_snapshot(env);value['engineering_execution_model_id']=MODEL_ID
    value['model_correctness_v23']=copy.deepcopy(env.core.observe()['model_correctness_v23'])
    return value

class PolicyEnvironment(operating.OperatingPolicyEnvironment):
    step=isolated(operating.native_episode.PolicyEnvironment.step,dict(operating.native_episode.PolicyEnvironment.step.__globals__,decision_snapshot=decision_snapshot,
        validate_action=lambda value:__import__('tools.model_correctness_v23.policy',fromlist=['validate_action']).validate_action(value)))

def run_episode(env,scenario,controller,seed,manifest):
    from types import SimpleNamespace
    from .export import json_clean
    original=operating.dataset_export.run_trajectory
    exporter=isolated(original,dict(original.__globals__,json_clean=json_clean))
    execute=isolated(operating.run_episode,dict(operating.run_episode.__globals__,decision_snapshot=decision_snapshot,
        OperatingPolicyEnvironment=PolicyEnvironment,dataset_export=SimpleNamespace(run_trajectory=exporter)))
    result=execute(env,scenario,controller,seed,manifest)
    result.update(execution_model_id=MODEL_ID,study_contract_id=CONTRACT_ID,model_correctness_contract_id=CONTRACT_ID)
    result['m21_initial_storage']={name:getattr(env.core,name).initial_charged_input_kWh for name in ('ups','cold_store')} if env.core else None
    result['events']=base.observed_model_events(scenario,result.get('summary',{}).get('observed_until_minute',0.),has_segments=bool(result.get('segments')),
        events=result.get('events',[]),research_stress_proxy=env.closure_config['research_stress_proxy'])
    return result
