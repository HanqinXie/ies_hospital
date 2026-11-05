"""Opt-in functional qualifications and ordinary queued readiness requests.

No DC charger, battery energy, connection, capacity or future calendar is
inferred. Normal-source support is a declared functional research boundary.
"""
from __future__ import annotations
import copy
import math
from types import MethodType
import numpy as np
from hospital_ies.state_transition import EPS
from tools.model_closure_v22.environment import EngineeringCore as ClosureCore
from tools.model_correctness_v23.station_paths import audit_supply
from tools.engineering_basis_study.environment import with_engineering_basis
from . import MODEL_ID, CONTRACT_ID
from .station import ContinuousStationState
from .policy import prepare_online_readiness


def settled_normal_source(segment,plant,*,grid_bus_state,building_delivered_electric_kW=None):
    """Read a settled interval. Aggregate AC proof is not a DC energy proof."""
    action=segment.get('action',{})
    try:
        if building_delivered_electric_kW is None:
            delivered=segment['service_request_ledger']['delivered_kW']
            building=sum(float(row[0]) for row in delivered)
        else:
            building=float(building_delivered_electric_kW)
        proof=audit_supply(action,plant,building_delivered_electric_kW=building)
        imported=action.get('grid_import_kW')
        supplied=(grid_bus_state=='GRID' and isinstance(imported,(int,float))
                  and not isinstance(imported,bool) and math.isfinite(imported)
                  and imported>EPS and proof['electric_balance_valid']
                  and proof['plant_source_eligible'])
        error=None
    except (KeyError,TypeError,ValueError,IndexError) as exc:
        proof=None;supplied=False;error=dict(type=type(exc).__name__,message=str(exc))
    return dict(supported=bool(supplied),grid_bus_state=grid_bus_state,
                proof=proof,error=error,source='actual settled non-UPS AC interval',
                functional_normal_supply_assumption=True,DC_energy_kWh=None,
                charger_connection_or_capacity_proved=False)


def current_normal_source(core,hardware):
    return bool(core.grid_bus_state=='GRID' and hardware.get('GRID',0.)>=1.-EPS
                and float(core.executed.get('grid_import_kW',0.))>EPS
                and getattr(core,'annual_last_supply_proof',{}).get('supported') is True)


def qualify_hardware(core,minute,hardware):
    """Pure hardware query; a start-only qualification cannot stop live DG."""
    f=copy.deepcopy(hardware);station=core.station;cfg=station.config
    normal=current_normal_source(core,f)
    control=station.available(hardware=f.get('GT_CONTROL_DC',1.)>=1.-EPS
        and cfg['gt_control_dc_available'],normal_source_now=normal)
    f['GT_CONTROL_DC']=float(control)
    if not control:
        for i in (1,2,3):f[f'GT_{i}']=0.
    start=station.available(hardware=f.get('DG_START_DC',1.)>=1.-EPS
        and cfg['dg_start_dc_available'],normal_source_now=normal)
    f['DG_START_DC']=float(start)
    status=core.state.diesel_status
    if status=='STARTING':
        if not start:f['DIESEL']=0.
    elif status not in ('READY','CONNECTED'):
        if not start or station.diesel_starts_used>=cfg['dg_qualified_starts']:f['DIESEL']=0.
    return f


def qualification_boundary(core,end):
    station=core.station
    f=core._hardware_data_at(core.time)[-1]
    normal=current_normal_source(core,f)
    candidates=[end]
    autonomy=station.config['control_autonomy_minutes']-station.unsupported_minutes
    # Even a previously supported source may cease importing in the next
    # accepted physical solve. Split conservatively before possible depletion.
    if autonomy>EPS:candidates.append(core.time+autonomy)
    if normal:
        tau=core.annual_config.get('requalification_minutes')
        debt=station.unsupported_minutes>0. or station.diesel_starts_used>0
        remaining=(tau-station.continuous_supported_minutes if tau is not None and debt else None)
        if remaining is not None and remaining>EPS:candidates.append(core.time+remaining)
    return min(candidates)


class ReadinessCoreMixin:
    def _close_actual_interval(self,end,next_time,connected,f,row,cmd,sol,fixed_args):
        if not isinstance(self.station,ContinuousStationState):
            return super()._close_actual_interval(end,next_time,connected,f,row,cmd,sol,fixed_args)
        bound=qualification_boundary(self,min(end,next_time))
        # A shortened actual interval needs its own physical/storage solve.
        # Passing the previous duration's candidate would corrupt its energy.
        result=super()._close_actual_interval(end,bound,connected,f,row,cmd,
            sol if bound==next_time else None,fixed_args)
        finish,actual,*_=result
        proof=settled_normal_source(dict(action=actual['action']),self.env.plant,
            grid_bus_state=self.grid_bus_state,
            building_delivered_electric_kW=float(np.asarray(actual['served'],float)[:,0].sum()))
        remaining=max(0.,self.station.config['control_autonomy_minutes']-self.station.unsupported_minutes)
        action=actual['action']
        credited_GT=(f.get('GT_CONTROL_DC',0.)>EPS and
            (sum(action.get('gt_electric_kW',[]))>EPS or action.get('gt_start_electric_kW',0.)>EPS))
        credited_DG_start=(f.get('DG_START_DC',0.)>EPS and
            getattr(getattr(self,'state',None),'diesel_status',None)=='STARTING')
        if not proof['supported'] and finish-self.time>remaining+EPS and (credited_GT or credited_DG_start):
            raise RuntimeError('Actual source does not support the credited control qualification; no expired prior-source credit')
        return result

    def _hardware_data_at(self,minute):
        if not isinstance(self.station,ContinuousStationState):return super()._hardware_data_at(minute)
        # Skip only the old absolute qualification gate. Preserve its exact
        # pre-gate fan/path transformations and registered availability events.
        h,row,loads,f=super(ClosureCore,self)._hardware_data_at(minute)
        f=dict(f)
        if 'fan_fault_design_components' in self.env.auxiliary_metadata:
            from tools.model_closure_v22.auxiliary import fan_path_availability
            from hospital_ies.state_transition import event_window
            start,end=event_window(self.env.scenario,'fault')
            faults=self.env.scenario.get('auxiliary_faults',{}) if start<=minute<end else {}
            for g in range(4):f[f'AUX_FAN_P{g}']=fan_path_availability(vars(row),self.env.auxiliary_metadata,faults,f'P{g}')
        f.update(STATION_AC=1.,GT_CONTROL_DC=1.,DG_START_DC=1.)
        for event in getattr(self.env,'model_closure_events',[]):
            if event['time_minute']>minute:break
            f.update(event['availability'])
        return h,row,loads,qualify_hardware(self,minute,f)

    def _next_boundary(self,end):
        boundary=super()._next_boundary(end)
        if isinstance(self.station,ContinuousStationState):boundary=qualification_boundary(self,boundary)
        return boundary

    def _grid_return_ready(self,hardware):
        config=getattr(self,'annual_config',{})
        if not config.get('transfer_recheck') or self.grid_bus_state!='RETURN_PREP':
            return super()._grid_return_ready(hardware)
        if (hardware['GRID']<1.-EPS or self.arrived_dispatch['grid_import_kW']<=EPS
                or not self._destination_return_ready(hardware)):
            return False
        if self._critical_preservation_probe(hardware,diesel=False):
            self.log('annual_open_bridge_requalified',actual_current_non_DG_bridge=True,
                     future_state_used=False,pending_GT_credited=False)
            return True
        working=(hardware['DIESEL']>EPS and self.state.diesel_status=='CONNECTED'
                 and self.state.diesel_energy_remaining_kWh>EPS
                 and not self.diesel_rotor.fuel_exhausted
                 and self.arrived_dispatch['diesel_kW']>EPS)
        if working:working=self._critical_preservation_probe(hardware,diesel=True)
        if self.bridge_wait_started_minute is None:self.bridge_wait_started_minute=self.time
        deadline=self._finite_return_deadline()
        if working and deadline is not None and self.time<deadline-EPS:
            self.log('annual_open_bridge_lost_wait',current_DG_support=True,
                     deadline_minute=deadline,ordinary_wait_bounded=True)
            return False
        self.bridge_wait_fallback_reason='annual_finite_source_fallback'
        self.log('annual_open_bridge_fallback',reason=self.bridge_wait_fallback_reason,
                 finite_open_gap_accepted=True,current_DG_support=bool(working),deadline_minute=deadline)
        return True

    def _prepare_command_dispatch(self,dispatch,diagnostic,loads):
        value,trace=super()._prepare_command_dispatch(dispatch,diagnostic,loads)
        if getattr(self,'annual_config',{}).get('online_preparation'):
            _,_,_,f=self._hardware_data_at(self.time)
            value,preparation=prepare_online_readiness(value,
                actual_gt_kW=np.asarray(self.state.gt_prev).tolist(),availability=f,
                rated_kW=self.env.plant.gt_rated_kW,minimum_fraction=min(self.env.plant.levels),
                critical_request_kW=float(any(float(loads[g,j])>EPS
                    for g in (0,1) for j in (0,1,2))))
            preparation['critical_input_is_presence_activation_not_cross_carrier_score']=True
            trace['annual_online_preparation']=preparation
        return value,trace

    def _after_actual_segment(self,segment,result):
        super()._after_actual_segment(segment,result)
        if not isinstance(self.station,ContinuousStationState):return
        # The final request ledger is attached by the outer advance exporter
        # later. Here the accepted actual solve already supplies served E.
        building=float(np.asarray(result['served'],float)[:,0].sum())
        proof=settled_normal_source(segment,self.env.plant,grid_bus_state=self.grid_bus_state,
                                    building_delivered_electric_kW=building)
        before=copy.deepcopy(self.station.observe())
        self.station.commit_supply(segment['start_minute'],segment['end_minute'],
                                   normal_source_supported=proof['supported'])
        self.annual_last_supply_proof=copy.deepcopy(proof)
        segment['m22_station'].update(self.station.observe())
        segment['annual_readiness']=dict(model_id=MODEL_ID,qualification_before=before,
            qualification_after=self.station.observe(),actual_normal_source_proof=proof,
            control_DC_energy_kWh=None,chemical_diesel_fuel_liters=None,
            inventory_reset=False,capacity_changed=False)

    def observe(self):
        value=super().observe()
        if isinstance(self.station,ContinuousStationState):
            value['annual_readiness']=dict(model_id=MODEL_ID,observation_time_minute=self.time,
                available_time_minute=self.time,qualification=self.station.observe(),
                functional_source_assumption=True)
        return value


def with_readiness(method,*,requalification_minutes=None,overlay_spec=None,thermal_profile=None):
    if method not in ('M_available','C_available','M_ready','C_ready','M_cooling_available'):
        raise ValueError('Unknown annual readiness arrangement')
    old_method=method.replace('_ready','_available')
    original=with_engineering_basis(old_method,overlay_spec=overlay_spec,thermal_profile=thermal_profile)
    config=dict(requalification_minutes=requalification_minutes,
                online_preparation=method.endswith('_ready'),transfer_recheck=method.endswith('_ready'))
    # Validate finite named assumptions before constructing an environment.
    ContinuousStationState(requalification_minutes=requalification_minutes)
    def create(*args,**kwargs):
        env=original.OperatingHospitalIESEnv(*args,**kwargs)
        env.engineering_core_type=type('AnnualReadinessCore',(ReadinessCoreMixin,env.engineering_core_type),{})
        old_reset=env.reset
        def reset(self,*,scenario,seed=None):
            returned=old_reset(scenario=scenario,seed=seed)
            old=self.core.station
            station=ContinuousStationState(old.config,warm_boiler=old.boiler_ready,
                                           requalification_minutes=requalification_minutes)
            station.boiler_powered_minutes=old.boiler_powered_minutes
            station.diesel_starts_used=old.diesel_starts_used
            station.total_diesel_starts_used=old.diesel_starts_used
            self.core.station=station
            self.core.annual_config=copy.deepcopy(config)
            self.core.annual_last_supply_proof={}
            self.model_id=MODEL_ID
            self.operating_model_config['annual_readiness']=dict(config,model_id=MODEL_ID,
                contract_id=CONTRACT_ID,source_qualification_not_energy=True,
                control_DC_kWh_unknown=True,old_model_files_unchanged=True)
            observation=self._observation()
            return (observation,returned[1]) if isinstance(returned,tuple) else observation
        env.reset=MethodType(reset,env)
        return env
    from types import SimpleNamespace
    return SimpleNamespace(OperatingHospitalIESEnv=create,validate_scenario=original.validate_scenario,
        validate_events=original.validate_events,controller_factory=original.controller_factory)
