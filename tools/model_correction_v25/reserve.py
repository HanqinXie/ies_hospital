"""Declared research inventory targets and same-state source substitution proof.

Targets are operating preferences, never evidence of feasible hospital autonomy.
The inherited common balance pays real plant electricity and all service locks.
"""
from __future__ import annotations
import copy
import math
import time
import numpy as np

from hospital_ies.execution import ModelConstraintInfeasible,SolverExecutionError
from hospital_ies.model import RT_TO_KW
from tools.model_correctness_v23.evidence import successful_backend_evidence,retained_backend_evidence


def operating_rule_config(value=None):
    cfg=copy.deepcopy(value or {'name':'current_service_bridge'})
    if not isinstance(cfg,dict) or set(cfg)-{'name','design_minutes'}:raise ValueError('Unknown V25 operating rule')
    name=cfg.get('name')
    if name=='current_service_bridge':return dict(name=name,design_minutes=None,rule_id=name)
    minutes=cfg.get('design_minutes',30.)
    if name!='sustained_critical' or isinstance(minutes,bool) or minutes not in (30.,60.):raise ValueError('Sustained inventory design requires registered 30 or 60 minute research duration')
    return dict(name=name,design_minutes=float(minutes),rule_id=f'sustained_critical_{int(minutes)}min')


def sustained(core):
    return getattr(core.env,'operating_rule',{}).get('name')=='sustained_critical'


def correct_legacy_reserve_explanations(value):
    """Correct explanatory text only; old policy numbers and choices survive."""
    if isinstance(value,list):return [correct_legacy_reserve_explanations(x) for x in value]
    if not isinstance(value,dict):return copy.deepcopy(value)
    result={k:correct_legacy_reserve_explanations(v) for k,v in value.items()}
    legacy='existing gas boiler has no separate electrical auxiliary in this model'
    if result.get('heat_reference')==legacy:
        result.update(legacy_heat_reference=legacy,legacy_reference_omits_station_auxiliary=True,
            heat_reference='historical policy reference excludes station auxiliary; M22+ execution pays actual boiler auxiliary/restart from non-building-UPS sources; see m25_reserve branch_reference',
            reference_is_actual_supply_proof=False,policy_scalar_values_unchanged=True)
    return result


def reserve_status(core,loads,f):
    rule=core.env.operating_rule
    if not sustained(core):
        return dict(control_rule=copy.deepcopy(rule),UPS_bridge_target_kWh=core._bridge_target(loads),
            cold_sustained_target_defined=False,guaranteed_duration_claimed=False)
    hours=rule['design_minutes']/60.;critical=np.asarray(loads,float)[:2].sum(axis=0)
    non_ups_upper=sum(min(float(p),float(core.state.gt_prev[i]))*float(f.get(f'GT_{i+1}',1.)) for i,p in enumerate(core.executed['gt_electric_kW']))
    if core.grid_bus_state=='GRID':non_ups_upper+=float(core.executed['grid_import_kW'])*float(f['GRID'])
    if core.diesel_available(core.time,f):non_ups_upper+=float(core.arrived_dispatch['diesel_kW'])
    result=dict(control_rule=copy.deepcopy(rule),research_design_minutes=rule['design_minutes'],
        guarantee_or_OEM_requirement=False,guaranteed_duration_claimed=False,
        current_critical_and_actual_restart_before_reserve=True,
        reserve_before_general_service=True,original_per_priority_E_H_C_sum_unchanged=True,
        objective_layer='bridge',objective_semantics='sum_asset_normalized_inventory_shortfall_change_per_hour',
        objective_numerical_contract='m25.reserve.shortfall_change_rate.v2',
        exact_same_snapshot_order='positive_duration_scaling_and_initial_constant_subtraction')
    for name,store,demand in (('ups',core.ups,critical[0]),('cold',core.cold_store,critical[2])):
        nominal=float(demand)*hours/store.config['discharge_efficiency']
        # Conservative: expose the entire designed withdrawal to the full
        # registered standing loss, consistent with FiniteStore's convention.
        target=nominal*math.exp(store.config['loss_per_hour']*hours);capacity=store.config['capacity_kWh']
        if name=='cold':
            reachable=sum(float(loads[g,2])*min(float(f.get(f'AUX_FAN_P{g}',1.)),float(f.get('AUX_CHW_PUMP',1.))) for g in (0,1))
        else:reachable=float(demand)
        hardware=bool(reachable>0. and store.config['power_kW']>0. and store.energy_kWh>0.)
        power_bound=min(store.config['power_kW'],reachable,store.energy_kWh*math.exp(-store.config['loss_per_hour']*hours)*store.config['discharge_efficiency']/hours)
        result[name]=dict(uncapped_target_kWh=target,nominal_no_standing_loss_target_kWh=nominal,
            design_standing_loss_allowance_kWh=target-nominal,standing_loss_bound_included=True,
            standing_loss_bound='all_design_withdrawal_exposed_to_full_design_duration',
            capacity_limited_target_kWh=min(capacity,target),
            capacity_kWh=capacity,available_inventory_kWh=store.energy_kWh,
            inventory_shortfall_kWh=max(0.,target-store.energy_kWh),capacity_shortfall_kWh=max(0.,target-capacity),
            rated_power_kW=store.config['power_kW'],current_reachable_critical_kW=reachable,
            hardware_inventory_power_bound_kW=power_bound,hardware_path_eligible=hardware,
            current_usable_power_kW=power_bound if name=='ups' or non_ups_upper>1e-9 else 0.,
            current_path_deliverable=False if not hardware or (name=='cold' and non_ups_upper<=1e-9) else None,
            positive_delivery_requires_actual_common_balance=True,
            target_is_feasible_supply_certificate=False)
    station=core.station.config
    # Building E already contains original fan/CHW/heat-pump allocations.
    # Only the additional tank pump and actual station ports are separate.
    boiler=(station['boiler_fixed_kW']+station['boiler_kW_per_kWsteam']*float(critical[1])/core.env.plant.exchanger_eff) if critical[1]>0. else 0.
    cold_possible=result['cold']['hardware_inventory_power_bound_kW']
    ups_possible=result['ups']['current_usable_power_kW']
    tank_pump=cold_possible*core.env.sci_config['cold_pump_kW_per_kW']
    ec=max(0.,float(critical[2])-cold_possible)*core.env.plant.ec_electric_kW_per_rt/RT_TO_KW
    starts=0.
    for i,pending in core.gt_start_pending.items():
        required=max(core.env.execution_contract['gt_start_delay_minutes'],pending['requested']/float(core.env.config['gt_ramp_kW_per_hour'])*60.)
        starts+=max(0.,required-core._m22_gt_progress.get(i,0.))*station['gt_starter_kW_per_unit']/60.
    restart=(max(0.,station['boiler_restart_minutes']-core.station.boiler_powered_minutes)*station['boiler_fixed_kW']/60.
        if critical[1]>0. and not core.station.boiler_ready else 0.)
    shaft_loss=core.diesel_rotor.nominal_loss_kW
    shaft=max(0.,float(critical[0])-ups_possible)+tank_pump+ec+boiler+shaft_loss
    needed=shaft*hours+starts+restart
    result['branch_reference']=dict(building_critical_electric_kW=float(critical[0]),
        building_auxiliary_already_included=True,building_UPS_supplies_station=False,
        extra_tank_pump_kW=tank_pump,full_critical_tank_pump_kW=float(critical[2])*core.env.sci_config['cold_pump_kW_per_kW'],
        residual_EC_all_in_electric_kW=ec,critical_boiler_auxiliary_kW=boiler,
        boiler_auxiliary_formula='0.5kW_on + 0.012kW_per_kWsteam times useful_heat/exchanger_eff (configured values)',
        required_remaining_GT_start_electric_kWh=starts,required_boiler_restart_electric_kWh=restart,
        current_station_AC_path_available=bool(f.get('STATION_AC',1.)>=1.-1e-9),
        current_non_UPS_arrived_source_upper_kW=non_ups_upper,arrived_upper_is_actual_supply=False,
        current_DG_connected=bool(core.diesel_available(core.time,f)),
        current_shaft_loss_reference_kW=shaft_loss,DG_reference_shaft_kW=shaft,
        DG_reference_shaft_work_kWh=needed,remaining_DG_shaft_work_kWh=float(core.state.diesel_energy_remaining_kWh),
        DG_reference_inventory_shortfall_kWh=max(0.,needed-float(core.state.diesel_energy_remaining_kWh)),
        DG_preservation_control_added=False,reference_is_source_support_diagnostic_only=True,
        current_inventory_averaged_over_design_duration=True,actual_current_balance_still_required=True,
        delivered_future_thermal_or_electric_supply_proved=False,
        heat_reference='M22+ actual gas-boiler station auxiliary and paid restart require non-building-UPS source')
    return result


def solver_context(core,dt_h):
    _,_,loads,f=core.data_at(core.time);status=reserve_status(core,loads,f)
    return {name:dict(target_kWh=status[name]['uncapped_target_kWh'],
        initial_energy_kWh=store.energy_kWh,
        retained_energy_kWh=store.energy_kWh*math.exp(-store.config['loss_per_hour']*dt_h),
        charge_efficiency=store.config['charge_efficiency'],discharge_efficiency=store.config['discharge_efficiency'])
        for name,store in (('ups',core.ups),('cold',core.cold_store))}


def validate_solver_context(config):
    value=(config or {}).get('_model_correction_v25_reserve')
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'ups','cold'}:raise ValueError('Invalid V25 reserve asset context')
    for item in value.values():
        if not isinstance(item,dict) or set(item)!={'target_kWh','initial_energy_kWh','retained_energy_kWh','charge_efficiency','discharge_efficiency'}:raise ValueError('Invalid V25 reserve quantities')
        if any(isinstance(v,bool) or not math.isfinite(float(v)) or v<0. for v in item.values()):raise ValueError('Invalid finite V25 reserve quantity')
        if not 0.<item['charge_efficiency']<=1. or not 0.<item['discharge_efficiency']<=1.:raise ValueError('Invalid V25 reserve efficiency')
    return value


class SustainedReserveMixin:
    def _prepare_command_dispatch(self,dispatch,diagnostic,loads):
        command,details=super()._prepare_command_dispatch(dispatch,diagnostic,loads)
        return command,correct_legacy_reserve_explanations(details)

    def _current_context(self,f):
        value=super()._current_context(f)
        if sustained(self):value['preserve_bridge']=True
        return value

    def _physical_solver_config(self,dt_h,f):
        value=super()._physical_solver_config(dt_h,f)
        if sustained(self):value['_model_correction_v25_reserve']=solver_context(self,dt_h)
        return value

    def _storage_solver(self,loads,f,dt_h,**kwargs):
        original=super()._storage_solver(loads,f,dt_h,**kwargs)
        if not sustained(self):return original
        context=solver_context(self,dt_h)
        def solve(ports,extra_config=None):
            extra=dict(extra_config or {},_model_correction_v25_reserve=context)
            return original(ports,extra)
        return solve

    def _solve(self,loads,f,dt_h,phase,action,**kwargs):
        result=super()._solve(loads,f,dt_h,phase,action,**kwargs)
        if not sustained(self):return result
        _,_,original_loads,_=self.data_at(self.time)
        report=reserve_status(self,original_loads,f)
        key=(self.time,dt_h,repr(np.asarray(loads).tolist()),repr(sorted(f.items())),repr(kwargs),
            self.ups.energy_kWh,self.cold_store.energy_kWh,self.station.boiler_ready,repr(self.gt_start_pending))
        cached=getattr(self,'_m25_no_store_cache',None)
        if cached is not None and cached[0]==key:witness=copy.deepcopy(cached[1])
        else:
            began=time.perf_counter();solve=self._storage_solver(loads,f,dt_h,**kwargs)
            ports=self._base_ports();ports.update(ups_discharge_kW=0.,ups_charge_kW=0.,cold_discharge_kW=0.,cold_charge_kW=0.)
            try:reference=solve(ports)
            except (ModelConstraintInfeasible,SolverExecutionError) as exc:
                witness=dict(status='registered_no_store_domain_infeasible' if isinstance(exc,ModelConstraintInfeasible) else 'unknown_solver_failure',
                    error_type=type(exc).__name__,message=str(exc),inventory_committed=False,same_current_state=True,
                    pending_target_credited=False,global_physical_infeasibility_claimed=False)
                layers=retained_backend_evidence(getattr(exc,'solver_backend_attempts',[]))
            else:
                witness=dict(status='solved',inventory_committed=False,same_current_state=True,
                    pending_target_credited=False,action=copy.deepcopy(reference['action']),unserved_kW=np.asarray(reference['lost']).tolist(),
                    energy_balance_residuals=reference['energy_balance_residuals'])
                layers=successful_backend_evidence(reference.get('solver_backend_layers',[]))
            self.solver_calls.append(dict(time_minute=self.time,dt_h=dt_h,phase='m25_selected_no_storage_witness',
                status='success' if witness['status']=='solved' else witness['status'],inventory_committed=False,
                elapsed_seconds=time.perf_counter()-began,solver_backend_layers=layers))
            self._m25_no_store_cache=(key,copy.deepcopy(witness))
        report['no_storage_witness']=witness
        if witness['status']=='solved':
            delta=np.asarray(witness['unserved_kW'])-np.asarray(result['lost'])
            report.update(current_service_gain_vs_no_storage_kW=delta.tolist(),
                general_service_gain_via_common_balance_kW=delta[2:].tolist(),
                source_substitution_included=True,recipient_label_is_not_eligibility_proof=True)
        result['m25_reserve']=report
        return result
