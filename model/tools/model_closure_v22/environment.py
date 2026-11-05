"""M22 causal adapter: current source paths, actual startup work and finite stores."""
from __future__ import annotations
import copy
import math
import time
import numpy as np
from hospital_ies.control_env import HospitalIESEnv as NativeEnvironment
from hospital_ies.state_transition import EPS,event_window
from hospital_ies.execution import ModelConstraintInfeasible,SolverExecutionError
from tools.engineering_model.environment import isolated
from tools.sci_engineering import environment as legacy
from tools.operating_response.physical_balance import solve_physical
from tools.operating_response import environment as operating
from . import MODEL_ID,CONTRACT_ID
from .station import StationState,validate_config as station_config
from .storage import DEFAULTS as STORAGE_DEFAULTS,ColdRechargeState,bridge_target_kWh

DEFAULTS=dict(station={},storage={},auxiliary={},instantaneous_diesel_current=True,research_stress_proxy=False)

def closure_config(value=None):
    if not isinstance(value or {},dict) or set(value or {})-set(DEFAULTS):raise ValueError('Unknown M22 closure configuration')
    cfg=copy.deepcopy(DEFAULTS);cfg.update(copy.deepcopy(value or {}));cfg['station']=station_config(cfg['station'])
    if not isinstance(cfg['storage'],dict) or set(cfg['storage'])-set(STORAGE_DEFAULTS):raise ValueError('Unknown M22 storage parameter')
    cfg['storage']=dict(STORAGE_DEFAULTS,**cfg['storage'])
    for name in ('instantaneous_diesel_current','research_stress_proxy'):
        if type(cfg[name]) is not bool:raise ValueError('M22 switch must be boolean')
    for k,v in cfg['storage'].items():
        if k=='preserve_bridge':
            if type(v) is not bool:raise ValueError('Bridge switch must be boolean')
        elif isinstance(v,bool) or not math.isfinite(float(v)) or float(v)<0.:raise ValueError('Invalid M22 storage parameter')
    if cfg['storage']['cold_recharge_deadband_fraction']>1.:raise ValueError('Invalid cold hysteresis fraction')
    return cfg

def validate_events(events):
    if not isinstance(events,list):raise ValueError('M22 events must be a list')
    from hospital_ies.resilience import empty_faults
    allowed=set(empty_faults())|{'STATION_AC','GT_CONTROL_DC','DG_START_DC','AUX_FAN_P0','AUX_FAN_P1','AUX_FAN_P2','AUX_FAN_P3','AUX_CHW_PUMP','AUX_HEAT_PUMP'}
    previous=-1.
    for event in events:
        if not isinstance(event,dict) or set(event)!={'time_minute','availability'}:raise ValueError('Invalid M22 external event')
        t=event['time_minute'];a=event['availability']
        if isinstance(t,bool) or not math.isfinite(float(t)) or t<0. or t<previous:raise ValueError('M22 event clocks must be ordered')
        if not isinstance(a,dict) or not a or set(a)-allowed:raise ValueError('Unknown M22 event device')
        if any(isinstance(v,bool) or not math.isfinite(float(v)) or not 0.<=float(v)<=1. for v in a.values()):raise ValueError('Invalid M22 event availability')
        previous=t
    return copy.deepcopy(events)

class EngineeringCore(legacy.EngineeringCore):
    def __init__(self,env,initial_action):
        self.station=StationState(env.closure_config['station'],warm_boiler=initial_action.get('boiler_steam_lbph',0.)>EPS)
        storage=env.closure_config['storage']
        self.cold_recharge=ColdRechargeState(storage['cold_recharge_deadband_kWh'],storage['cold_recharge_deadband_fraction'])
        self._m22_ports=None;self._m22_gt_progress={};self._m22_last_dg_start=None
        self._m22_selection_key=None
        super().__init__(env,initial_action)
        self.log('m22_station_initial_state',station=self.station.observe(),initial_GT_online=np.asarray(initial_action['gt_electric_kW'])>EPS,
            warm_start_is_inherited=True,building_UPS_supplies_station=False)

    def _hardware_data_at(self,minute):
        h,row,loads,f=super()._hardware_data_at(minute);f=dict(f)
        if 'fan_fault_design_components' in self.env.auxiliary_metadata:
            from .auxiliary import fan_path_availability
            start,end=event_window(self.env.scenario,'fault')
            faults=self.env.scenario.get('auxiliary_faults',{}) if start<=minute<end else {}
            for g in range(4):f[f'AUX_FAN_P{g}']=fan_path_availability(vars(row),self.env.auxiliary_metadata,faults,f'P{g}')
        f.update(STATION_AC=1.,GT_CONTROL_DC=1.,DG_START_DC=1.)
        for event in getattr(self.env,'model_closure_events',[]):
            if event['time_minute']>minute:break
            f.update(event['availability'])
        cfg=self.station.config
        control=self.station.control_available(minute,f['GT_CONTROL_DC']>=1.-EPS and cfg['gt_control_dc_available'])
        f['GT_CONTROL_DC']=float(control)
        if not control:
            for i in (1,2,3):f[f'GT_{i}']=0.
        dc=self.station.control_available(minute,f['DG_START_DC']>=1.-EPS and cfg['dg_start_dc_available'])
        f['DG_START_DC']=float(dc)
        status=getattr(self.state,'diesel_status','OFF')
        if not dc or (status not in ('STARTING','READY','CONNECTED') and self.station.diesel_starts_used>=cfg['dg_qualified_starts']):f['DIESEL']=0.
        return h,row,loads,f

    def observe(self):
        v=super().observe();_,_,loads,f=self.data_at(self.time)
        v['availability']={k:x for k,x in v['availability'].items() if k not in ('STATION_AC','GT_CONTROL_DC','DG_START_DC')}
        v['sci_model_id']=MODEL_ID;v['diesel_local_response']['model_id']=MODEL_ID
        v['model_closure_v22']=dict(observation_time_minute=self.time,available_time_minute=self.time,
            station=dict(self.station.observe(),station_AC_available=f['STATION_AC'],GT_control_DC_available=f['GT_CONTROL_DC'],DG_start_DC_available=f['DG_START_DC']),
            UPS_bridge_target_kWh=self._bridge_target(loads),cold_recharge_active=self.cold_recharge.active,
            research_stress_proxy=self.env.closure_config['research_stress_proxy'])
        return v

    def _bridge_target(self,loads):
        c=self.ups.config
        return bridge_target_kWh(float(loads[:2,0].sum()),c['capacity_kWh'],c['discharge_efficiency'],
            observation_minutes=self.env.closure_config['storage']['bridge_observation_minutes'],
            start_minutes=float(self.env.config['diesel_start_delay_minutes']),
            transfer_minutes=max(float(self.env.execution_contract['ats_transfer_minutes']),self.env.recovery_config['grid_open_transfer_minutes']))

    def _current_context(self,f):
        _,row,loads,_=self.data_at(self.time);cfg=self.station.config
        active=[i for i,pending in self.gt_start_pending.items() if pending['started']<=self.time+EPS]
        starter=len(active)*cfg['gt_starter_kW_per_unit'] if f['GT_CONTROL_DC']>EPS else 0.
        boiler_request=float(self.arrived_dispatch.get('boiler_steam_lbph',0.))>EPS
        return dict(station_ac_available=f['STATION_AC']>=1.-EPS,boiler_ready=self.station.boiler_ready,
            boiler_fixed_kW=cfg['boiler_fixed_kW'],boiler_kW_per_kWsteam=cfg['boiler_kW_per_kWsteam'],
            boiler_restart_kW=cfg['boiler_fixed_kW'] if boiler_request and not self.station.boiler_ready else 0.,gt_start_kW=starter,
            gt_starter_kW_per_unit=cfg['gt_starter_kW_per_unit'],
            ups_energy_kWh=self.ups.energy_kWh,ups_target_kWh=self._bridge_target(loads),ups_discharge_efficiency=self.ups.config['discharge_efficiency'],
            cold_energy_kWh=self.cold_store.energy_kWh,cold_discharge_efficiency=self.cold_store.config['discharge_efficiency'],
            research_stress_proxy=self.env.closure_config['research_stress_proxy'],
            preserve_bridge=self.env.closure_config['storage']['preserve_bridge'],instantaneous_diesel_current=self.env.closure_config['instantaneous_diesel_current'])

    def _base_ports(self,*,ups_charge=False,cold_mode='idle'):
        def available(s,charge):
            return s.config['power_kW'] if (s.energy_kWh<s.config['capacity_kWh']-1e-9 if charge else s.energy_kWh>1e-9) else 0.
        return dict(ups_discharge_kW=0. if ups_charge else available(self.ups,False),ups_charge_kW=available(self.ups,True) if ups_charge else 0.,
            cold_discharge_kW=available(self.cold_store,False) if cold_mode=='discharge' else 0.,
            cold_charge_kW=available(self.cold_store,True) if cold_mode=='charge' else 0.,cold_pump_kW_per_kW=self.env.sci_config['cold_pump_kW_per_kW'])

    def _storage_ports(self,dt_h,f):
        ports=copy.deepcopy(self._m22_ports if self._m22_ports is not None else self._base_ports())
        for prefix,store in (('ups',self.ups),('cold',self.cold_store)):
            if store.energy_kWh<=1e-9:ports[prefix+'_discharge_kW']=0.
            if store.energy_kWh>=store.config['capacity_kWh']-1e-9:ports[prefix+'_charge_kW']=0.
        return ports

    def _physical_solver_config(self,dt_h,f):
        return dict(self.env.config,_sci_storage_ports=self._storage_ports(dt_h,f),_model_closure_v22=self._current_context(f))

    def _current_probe(self,method,hardware,**kwargs):
        # Legacy nested return/bridge proofs read env.config directly. Give
        # this serial, pure proof one current compiled context, then restore
        # the ordinary configuration object, even when the proof fails.
        previous=self.env.config
        total=self._hardware_data_at(self.time)[2]
        self._select_current_ports(np.maximum(0.,total-self.state.disconnected_loads),hardware)
        self.env.config=self._physical_solver_config(self.env.dt_h,hardware)
        try:return method(hardware,**kwargs)
        finally:self.env.config=previous

    def _grid_return_ready(self,hardware):
        return self._current_probe(super()._grid_return_ready,hardware)

    def _critical_preservation_probe(self,hardware,*,diesel):
        return self._current_probe(super()._critical_preservation_probe,hardware,diesel=diesel)

    def _select_current_ports(self,loads,f):
        key=(self.time,repr(sorted(f.items())),repr(self.executed),self.ups.energy_kWh,self.cold_store.energy_kWh,self.station.boiler_ready,repr(self.gt_start_pending))
        if key==self._m22_selection_key:return
        if max(self.ups.config['capacity_kWh'],self.cold_store.config['capacity_kWh'])<=0.:
            self._m22_ports=self._base_ports();self._m22_selection_key=key;return
        _,row,_,_=self.data_at(self.time);began=time.perf_counter()
        try:
            cfg=dict(self.env.config,_sci_storage_ports=self._base_ports(),_model_closure_v22=self._current_context(f))
            result=solve_physical(row,self.env.plant,loads,f,self.state,self.env.dt_h,
                float(self.env.scenario.get('grid_limit_kW',self.env.config['grid_import_limit_kW'])),self.diesel_available(self.time,f),
                float(self.env.config['gt_ramp_kW_per_hour']),float(self.env.config['solver_time_limit_sec']),cfg,
                execution_action=self.executed,execution_contract=self.env.operating_execution_contract)
        except ModelConstraintInfeasible as exc:
            # A stale held setpoint (e.g. an hourly GT floor) can be rejected
            # before the existing local interlock/ordinary new command acts.
            # This is one mode candidate, never an actual execution failure.
            self._m22_ports=self._base_ports();self._m22_selection_key=key
            self.solver_calls.append(dict(time_minute=self.time,phase='m22_current_port_selection',status='candidate_infeasible',message=str(exc),
                current_only=True,inventory_committed=False,elapsed_seconds=time.perf_counter()-began,
                solver_backend_layers=copy.deepcopy(getattr(exc,'solver_backend_attempts',[]))))
            return
        except Exception as exc:self._record_error(exc,loads,f,self.env.dt_h,'m22_current_port_selection_solver_error',self.active_command,began)
        deficit=float(np.asarray(result['lost'])[:2,2].sum())
        mode=self._cold_mode(deficit) if hasattr(self,'_cold_mode') and self.env.correctness_config['storage_fix'] else ('discharge' if deficit>.01 and self.cold_store.energy_kWh>1e-9 else ('charge' if self.cold_recharge.allowed(self.cold_store.energy_kWh,self.cold_store.config['capacity_kWh']) else 'idle'))
        charge=self.ups.energy_kWh<self.ups.config['capacity_kWh']-1e-9 and result['action'].get('ups_discharge_kW',0.)<1e-6
        self._m22_ports=self._base_ports(ups_charge=charge,cold_mode=mode);self._m22_selection_key=key
        self.solver_calls.append(dict(time_minute=self.time,phase='m22_current_port_selection',status='success',cold_mode=mode,
            current_only=True,inventory_committed=False,elapsed_seconds=time.perf_counter()-began,
            solver_backend_layers=copy.deepcopy(result['solver_backend_layers'])))

    def _electric_sink_upper(self,loads,f,row):
        if hasattr(self,'_search_cold_modes') and self.env.engineering_completion_config['cold_mode_search']:
            # The no-export decision must see the same lawful current
            # charging domain as the subsequent actual physical solve.
            self._select_current_ports(loads,f)
        value=super()._electric_sink_upper(loads,f,row);context=self._current_context(f)
        steam=min(float(self.arrived_dispatch.get('boiler_steam_lbph',0.)),self.env.plant.boiler_steam_lbph/3.*sum(f[f'BOILER_{i}'] for i in (1,2,3)))
        station=0.
        if context['station_ac_available']:
            station=context['gt_start_kW']+context['boiler_restart_kW']
            if context['boiler_ready'] and steam>EPS:station+=context['boiler_fixed_kW']+context['boiler_kW_per_kWsteam']*steam*self.env.plant.steam_kWh_per_lb
        value['upper_kW']+=station;value['maximum_held_station_auxiliary_kW']=station
        return value

    def _clamp(self,dispatch,f,normal_ramp=False):
        # Preserve inherited arrived permission/ramp logic; replace elapsed
        # wall time by accumulated, actually powered startup work.
        # V30 fix: remaining work begins no earlier than the actual min-off
        # eligibility stored in pending.started, even while that lies ahead.
        for i,pending in self.gt_start_pending.items():
            required=max(self.env.execution_contract['gt_start_delay_minutes'],pending['requested']/float(self.env.config['gt_ramp_kW_per_hour'])*60.)
            pending['ready']=max(self.time,pending['started'])+max(0.,required-self._m22_gt_progress.get(i,0.))
        result=super()._clamp(dispatch,f,normal_ramp=normal_ramp)
        for i,pending in self.gt_start_pending.items():
            required=max(self.env.execution_contract['gt_start_delay_minutes'],pending['requested']/float(self.env.config['gt_ramp_kW_per_hour'])*60.)
            pending['ready']=max(self.time,pending['started'])+max(0.,required-self._m22_gt_progress.get(i,0.))
            if self._m22_gt_progress.get(i,0.)<required-EPS:result['gt_electric_kW'][i]=0.
        return result

    def boundary(self):
        super().boundary()
        if self._hardware_data_at(self.time)[-1]['STATION_AC']<1.-EPS:
            self.station.boiler_ready=False;self.station.boiler_powered_minutes=0.
        if self.state.diesel_start_minute is not None and self.state.diesel_start_minute!=self._m22_last_dg_start:
            f=self._hardware_data_at(self.time)[-1]
            if not self.station.claim_diesel_start(self.time,f['DG_START_DC']>EPS):raise RuntimeError('Unqualified actual DG start')
            self._m22_last_dg_start=self.state.diesel_start_minute
            self.log('m22_DG_dedicated_DC_start',classification='dependency',station=self.station.observe())
        if self.state.diesel_start_minute is None:self._m22_last_dg_start=None
        for i in list(self._m22_gt_progress):
            if i not in self.gt_start_pending:self._m22_gt_progress.pop(i)

    def _next_boundary(self,end):
        t=super()._next_boundary(end);cfg=self.station.config
        candidates=[x['time_minute'] for x in getattr(self.env,'model_closure_events',[]) if x['time_minute']>self.time+EPS]
        if cfg['control_autonomy_minutes']>self.time+EPS:candidates.append(cfg['control_autonomy_minutes'])
        if not self.station.boiler_ready and float(self.arrived_dispatch.get('boiler_steam_lbph',0.))>EPS:
            restart_boundary=(self._boiler_restart_boundary() if hasattr(self,'_boiler_restart_boundary') else
                self.time+max(1e-6,cfg['boiler_restart_minutes']-self.station.boiler_powered_minutes))
            if restart_boundary is not None:candidates.append(restart_boundary)
        # Powered startup state is resolved at bounded actual intervals; an
        # unpowered pending target does not advance simply because time did.
        if self.gt_start_pending:candidates.append(self.time+1.)
        return min([t]+[x for x in candidates if x>self.time+EPS])

    def _storage_solver(self,loads,f,dt_h,**kwargs):
        """Build pure same-snapshot solves for actual and current-port trials."""
        e=self.env;_,row,_,_=self.data_at(self.time)
        def solve(ports,extra_config=None):
            cfg=dict(e.config,_sci_storage_ports=ports,_model_closure_v22=self._current_context(f))
            cfg.update(extra_config or {})
            return solve_physical(row,e.plant,loads,f,self.state,dt_h,float(e.scenario.get('grid_limit_kW',e.config['grid_import_limit_kW'])),
                self.diesel_available(self.time,f),float(e.config['gt_ramp_kW_per_hour']),float(e.config['solver_time_limit_sec']),cfg,
                execution_contract=e.operating_execution_contract,**kwargs)
        return solve

    def _solve(self,loads,f,dt_h,phase,action,**kwargs):
        began=time.perf_counter();e=self.env
        solve=self._storage_solver(loads,f,dt_h,**kwargs)
        try:
            search_modes=hasattr(self,'_search_cold_modes') and self.env.engineering_completion_config['cold_mode_search']
            baseline_failure=None
            try:baseline=solve(self._base_ports())
            except (ModelConstraintInfeasible,SolverExecutionError) as exc:
                if not search_modes:raise
                baseline=None;baseline_failure=exc
            if search_modes:
                result,ports,selection=self._search_cold_modes(solve,baseline,loads,f,dt_h,baseline_failure=baseline_failure)
                self._m22_ports=copy.deepcopy(ports)
                if phase!='local_down_endpoint':self._storage_candidate=dict(time_minute=self.time,action=copy.deepcopy(result['action']))
                result['m22_mode_selection']=selection
                self.solver_calls.append(dict(time_minute=self.time,phase=phase,dt_h=dt_h,status='success',elapsed_seconds=time.perf_counter()-began,
                    solver_backend_layers=result['solver_backend_layers'],m24_cold_mode_selection=copy.deepcopy(selection)))
                return result
            deficit=float(np.asarray(baseline['lost'])[:2,2].sum())
            mode=self._cold_mode(deficit) if hasattr(self,'_cold_mode') and self.env.correctness_config['storage_fix'] else ('discharge' if deficit>.01 and self.cold_store.energy_kWh>1e-9 else ('charge' if self.cold_recharge.allowed(self.cold_store.energy_kWh,self.cold_store.config['capacity_kWh']) else 'idle'))
            candidates=[]
            for charge in ([False,True] if self.ups.energy_kWh<self.ups.config['capacity_kWh']-1e-9 and self.ups.config['capacity_kWh']>0. else [False]):
                ports=self._base_ports(ups_charge=charge,cold_mode=mode)
                candidate=baseline if ports==self._base_ports() else solve(ports)
                candidates.append((candidate,ports))
            def key(item):
                r,ports=item;a=r['action'];lost=np.asarray(r['lost']);normal=np.maximum(1.,loads.sum(axis=0))
                context=self._current_context(f)
                debt=(max(0.,a.get('ups_discharge_kW',0.)*dt_h/self.ups.config['discharge_efficiency']-max(0.,self.ups.energy_kWh-context['ups_target_kWh']))
                    if context['preserve_bridge'] else 0.)
                return tuple(round(float((lost[g]/normal).sum()),7) for g in (0,1))+(round(debt,7),)+tuple(round(float((lost[g]/normal).sum()),7) for g in (2,3))+(-float(a.get('boiler_station_running',False)),round(a.get('ups_discharge_kW',0.),7),-round(a.get('ups_charge_kW',0.)+a.get('cold_charge_kW',0.),5),r['cost_USD_step'])
            result,ports=self._choose_storage_candidate(candidates) if hasattr(self,'_choose_storage_candidate') and self.env.correctness_config['boiler_domain_search'] else min(candidates,key=key)
            self._m22_ports=copy.deepcopy(ports)
            if phase!='local_down_endpoint':self._storage_candidate=dict(time_minute=self.time,action=copy.deepcopy(result['action']))
            result['m22_mode_selection']=dict(cold_mode=mode,no_store_critical_cold_deficit_kW=deficit,candidate_count=len(candidates),inventory_committed=False)
            self.solver_calls.append(dict(time_minute=self.time,phase=phase,dt_h=dt_h,status='success',elapsed_seconds=time.perf_counter()-began,solver_backend_layers=result['solver_backend_layers']))
            return result
        except Exception as exc:self._record_error(exc,loads,f,dt_h,phase,action,began)

    def _after_actual_segment(self,segment,result):
        super()._after_actual_segment(segment,result);a=segment['action'];minutes=segment['end_minute']-segment['start_minute']
        f=self._hardware_data_at(segment['start_minute'])[-1];cfg=self.station.config
        requested=float(self.arrived_dispatch.get('boiler_steam_lbph',0.))>EPS
        boiler_ready_before=bool(self.station.boiler_ready)
        station_live=f['STATION_AC']>=1.-EPS and (a.get('boiler_auxiliary_kW',0.)>EPS or a.get('boiler_restart_electric_kW',0.)>=cfg['boiler_fixed_kW']-EPS)
        self.station.commit(minutes,boiler_requested=requested,boiler_restart_supplied=a.get('boiler_restart_electric_kW',0.)>=cfg['boiler_fixed_kW']-EPS,station_live=station_live)
        active=[i for i,pending in self.gt_start_pending.items() if pending['started']<=segment['start_minute']+EPS]
        expected=len(active)*cfg['gt_starter_kW_per_unit']
        supplied=expected>EPS and a.get('gt_start_electric_kW',0.)>=expected-1e-6
        for i in active:
            self._m22_gt_progress[i]=self._m22_gt_progress.get(i,0.)+minutes if supplied else self._m22_gt_progress.get(i,0.)
        receipt=segment['m21_storage']['cold_store'];self.cold_recharge.commit(receipt['initial_kWh'],receipt['final_kWh'],self.cold_store.config['capacity_kWh'],charged_input_kWh=receipt['charged_input_kWh'])
        segment['m22_station']=self.station.observe();segment['m22_mode_selection']=copy.deepcopy(result.get('m22_mode_selection'))
        restart_supplied=a.get('boiler_restart_electric_kW',0.)>=cfg['boiler_fixed_kW']-EPS
        source_power=sum(a['gt_electric_kW'])+a['grid_import_kW']+a['diesel_kW']
        if f['STATION_AC']<1.-EPS:status='station_path_lost'
        elif not requested:status='no_permission'
        elif a.get('boiler_steam_lbph',0.)>EPS and a.get('boiler_auxiliary_kW',0.)>EPS:status='running'
        elif not boiler_ready_before and restart_supplied:
            status='restart_completed' if self.station.boiler_ready else 'restarting'
        elif not boiler_ready_before or source_power<=EPS:status='source_unpowered'
        else:status='held_setting_unexecutable_after_service_locks'
        segment['m22_station'].update(boiler_execution_status=status,
            boiler_ready_before_actual_interval=boiler_ready_before,
            boiler_restart_power_supplied=bool(restart_supplied and requested and not boiler_ready_before),
            actual_source_electric_kW=float(source_power),building_UPS_is_station_source=False)
        segment['execution_model_id']=MODEL_ID;segment['model_closure_contract_id']=CONTRACT_ID

    def generate_command(self,action):
        _,_,loads,f=self.data_at(self.time)
        # Refresh the shared current mode using the same held actuator map.
        # This proof settles no inventory or startup clock.
        self._select_current_ports(loads,f)
        self.env.config.update(self._physical_solver_config(self.env.dt_h,f))
        command=super().generate_command(action);command['execution_model_id']=MODEL_ID
        return command

    def _protection(self,loads,minutes,automatic=False):
        if not self.env.closure_config['research_stress_proxy']:
            if minutes>0.:self.state.overload_minutes[:]=0.
            return [],{},dict(research_stress_proxy_enabled=False,hard_capacities_and_interlocks_unchanged=True)
        return super()._protection(loads,minutes,automatic=automatic)

    def non_diesel_witness(self):
        """Pure actual current non-DG e/h/c proof, never a repair forecast."""
        _,row,total,f=self.data_at(self.time);loads=np.maximum(0.,total-self.state.disconnected_loads)
        self._select_current_ports(loads,f)
        candidate=copy.deepcopy(self.executed);candidate['diesel_kW']=0.
        if f['GRID']<=EPS:candidate['grid_import_kW']=0.
        if sum(candidate['gt_electric_kW'])+candidate['grid_import_kW']<=EPS:
            return dict(ready=False,status='no_connected_non_DG_source',current_only=True,pending_target_credited=False)
        began=time.perf_counter()
        try:
            result=solve_physical(row,self.env.plant,loads,f,self.state,self.env.dt_h,
                float(self.env.scenario.get('grid_limit_kW',self.env.config['grid_import_limit_kW'])),False,
                float(self.env.config['gt_ramp_kW_per_hour']),float(self.env.config['solver_time_limit_sec']),
                self._physical_solver_config(self.env.dt_h,f),execution_action=candidate,execution_contract=self.env.operating_execution_contract)
        except Exception as exc:
            if hasattr(self,'_raise_evidence_io_error'):self._raise_evidence_io_error(exc)
            self.solver_calls.append(dict(time_minute=self.time,phase='m22_non_DG_release_witness',status='candidate_infeasible' if isinstance(exc,ModelConstraintInfeasible) else 'solver_error',exception_type=type(exc).__name__,message=str(exc),
                current_only=True,inventory_committed=False,elapsed_seconds=time.perf_counter()-began,
                solver_backend_layers=copy.deepcopy(getattr(exc,'solver_backend_attempts',[]))))
            return dict(ready=False,status='candidate_infeasible' if isinstance(exc,ModelConstraintInfeasible) else 'solver_error',error_type=type(exc).__name__,message=str(exc),current_only=True,pending_target_credited=False)
        self.solver_calls.append(dict(time_minute=self.time,phase='m22_non_DG_release_witness',status='success',
            current_only=True,inventory_committed=False,elapsed_seconds=time.perf_counter()-began,
            solver_backend_layers=copy.deepcopy(result['solver_backend_layers'])))
        deficit=np.asarray(result['lost'])[:2]+self.state.disconnected_loads[:2]
        return dict(ready=bool(np.max(deficit)<1e-4),status='solved',current_only=True,pending_target_credited=False,
            critical_unserved_kW=deficit.tolist(),actual_action=copy.deepcopy(result['action']),energy_balance_residuals=result['energy_balance_residuals'],inventory_committed=False)

    def advance(self,end):
        first=len(self.segments)
        try:return super().advance(end)
        finally:
            for s in self.segments[first:]:s.update(execution_model_id=MODEL_ID,model_closure_contract_id=CONTRACT_ID)

class OperatingHospitalIESEnv(legacy.OperatingHospitalIESEnv):
    def __init__(self,root,*,allowed_hours,sealed_days,model_id=MODEL_ID,model_config=None,**kwargs):
        if model_id!=MODEL_ID:raise ValueError('Explicit M22 model identity required')
        config=copy.deepcopy(model_config or {});self.closure_config=closure_config(config.pop('model_closure_v22',{}))
        super().__init__(root,allowed_hours=allowed_hours,sealed_days=sealed_days,model_config=config,**kwargs)
        self.model_id=MODEL_ID;self.operating_execution_contract['model_closure_contract_id']=CONTRACT_ID
        self.operating_model_config['model_closure_v22']=copy.deepcopy(self.closure_config)
        auxiliary_options=self.closure_config['auxiliary']
        if not isinstance(auxiliary_options,dict) or set(auxiliary_options)-{'pump_weight_exponent','actual_power_scale','fan_priority_shares'}:raise ValueError('Unknown M22 auxiliary transform')
        if 'fan_fault_design_components' not in self.auxiliary_metadata:
            from .auxiliary import compute_allocation
            from tools.sci_engineering.auxiliary import compute_auxiliary_allocation
            transformed=compute_allocation(self.root,**auxiliary_options)
            baseline=compute_auxiliary_allocation(self.root).set_index('hour_of_year')
            selected=transformed[transformed.hour_of_year.isin(self.allowed_hours)].copy();indexed=selected.set_index('hour_of_year')
            self.auxiliary_metadata=copy.deepcopy(transformed.attrs['metadata'])
            for g in range(4):
                direct=indexed[f'P{g}_base_e_kW'].copy()
                if g==3:direct-=baseline.loc[indexed.index,'source_fans_total_kW']+baseline.loc[indexed.index,'source_chw_pump_kW']
                electric=direct+indexed[f'P{g}_fan_kW']+indexed[f'P{g}_chw_pump_kW']+indexed[f'P{g}_heat_pump_kW']
                if (electric<0.).any():raise ValueError('Actual auxiliary transform exceeds original P3 component boundary')
                self.groups.loc[indexed.index,f'P{g}_e_kW']=electric
                self.hourly.loc[indexed.index.to_numpy()-1,f'P{g}_reference_e_kW']=electric.to_numpy()
            for name in selected.columns:
                if name!='hour_of_year':self.hourly.loc[selected.hour_of_year.to_numpy()-1,name]=selected[name].to_numpy()
            self.input_access_receipt['sci_auxiliary_source_sha256']=self.auxiliary_metadata['allocation_sha256']

    def reset(self,*,scenario,seed=None):
        original=copy.deepcopy(scenario);self.model_closure_events=validate_events(original.pop('model_closure_events',[]))
        for key in ('_model_closure_v22','_sci_storage_ports'):self.config.pop(key,None)
        reset=isolated(legacy.OperatingHospitalIESEnv.reset,dict(legacy.OperatingHospitalIESEnv.reset.__globals__,EngineeringCore=EngineeringCore))
        value=reset(self,scenario=original,seed=seed)
        return value

def decision_snapshot(env):
    value=legacy.decision_snapshot(env);value['engineering_execution_model_id']=MODEL_ID
    value['model_closure_v22']=copy.deepcopy(env.core.observe()['model_closure_v22'])
    value['model_closure_v22']['current_non_diesel_feasibility']=env.core.non_diesel_witness()
    return value

class PolicyEnvironment(operating.OperatingPolicyEnvironment):
    step=isolated(operating.native_episode.PolicyEnvironment.step,dict(operating.native_episode.PolicyEnvironment.step.__globals__,decision_snapshot=decision_snapshot,
        validate_action=lambda value:__import__('tools.model_closure_v22.policy',fromlist=['validate_action']).validate_action(value)))

def observed_model_events(scene,observed_until,*,has_segments,events,research_stress_proxy):
    """Offline observed event ledger; no schedule enters policy observations."""
    from tools.sci_engineering.contracts import observed_engineering_events
    exported=copy.deepcopy(events)
    if research_stress_proxy:
        for event in exported:
            if event.get('kind')=='protection_trip':
                event.update(legacy_cause_class=event.get('cause_class'),cause_class='research_stress_proxy',
                    mechanism_class='research_proxy',actual_not_nameplate_overload=True,
                    physical_nameplate_overload_evidence=False)
    if has_segments:
        added=observed_engineering_events(scene,observed_until,has_segments=True)
        for i,event in enumerate(scene.get('model_closure_events',[])):
            if float(event['time_minute'])>float(observed_until):continue
            added.append(dict(event_id=f'm22_exo:availability:{i}',time_minute=float(event['time_minute']),
                kind='m22_exogenous_availability_changed',origin='exogenous',cause_class='registered_model_closure_availability',
                parent_event_id=None,details=dict(availability=copy.deepcopy(event['availability'])),
                provenance='Registered M22 boundary observed in completed actual prefix; no inferred causal parent'))
        seen={event.get('event_id') for event in exported}
        exported.extend(event for event in added if event['time_minute']<=float(observed_until) and event['event_id'] not in seen)
    return sorted(exported,key=lambda event:(event['time_minute'],str(event.get('event_id',''))))

def run_episode(env,scenario,controller,seed,manifest):
    execute=isolated(operating.run_episode,dict(operating.run_episode.__globals__,decision_snapshot=decision_snapshot,OperatingPolicyEnvironment=PolicyEnvironment))
    result=execute(env,scenario,controller,seed,manifest)
    result.update(execution_model_id=MODEL_ID,study_contract_id=CONTRACT_ID,model_closure_contract_id=CONTRACT_ID)
    result['m21_initial_storage']={name:getattr(env.core,name).initial_charged_input_kWh for name in ('ups','cold_store')} if env.core else None
    result['events']=observed_model_events(scenario,result.get('summary',{}).get('observed_until_minute',0.),
        has_segments=bool(result.get('segments')),events=result.get('events',[]),
        research_stress_proxy=env.closure_config['research_stress_proxy'])
    return result
