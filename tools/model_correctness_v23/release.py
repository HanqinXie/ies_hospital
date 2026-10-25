"""M23 current source qualification: finite bridges never authorize release.

The sustained proof holds current loads and connected/arrived source rights;
it is not a prediction of future faults, demand or outage duration.
"""
from __future__ import annotations
import copy
import time
import numpy as np
from hospital_ies.execution import ModelConstraintInfeasible
from hospital_ies.state_transition import EPS
from tools.model_closure_v22.environment import EngineeringCore as M22Core
from tools.operating_response.physical_balance import solve_physical


def _known_clocks(core,action):
    now=float(core.time);clocks=[];cfg=core.env.recovery_config
    if core.grid_bus_state=='RETURN_PREP' and core.grid_return_started_minute is not None:
        clocks.append(dict(kind='observed_grid_return_preparation',remaining_minutes=max(0.,core.grid_return_started_minute+cfg['grid_return_prepare_minutes']-now)+cfg['grid_open_transfer_minutes'],
            eligible_non_DG_target=True,conditional_on_current_conditions_remaining_available=True))
    elif core.grid_bus_state=='TRANSFER_OPEN' and core.grid_open_started_minute is not None:
        clocks.append(dict(kind='observed_open_transfer',remaining_minutes=max(0.,core.grid_open_started_minute+cfg['grid_open_transfer_minutes']-now),
            eligible_non_DG_target=True,conditional_on_current_conditions_remaining_available=True))
    hardware=core._hardware_data_at(now)[-1]
    active=[p for p in core.gt_start_pending.values() if p['started']<=now+EPS]
    expected=len(active)*core.station.config['gt_starter_kW_per_unit']
    paid=bool(expected>0. and action.get('gt_start_electric_kW',0.)>=expected-1e-6 and hardware['STATION_AC']>=1.-EPS and hardware['GT_CONTROL_DC']>EPS)
    for i,pending in sorted(core.gt_start_pending.items()):
        if pending['started']>now+EPS or core.arrived_dispatch['gt_electric_kW'][i]<=EPS or hardware[f'GT_{i+1}']<=EPS:continue
        required=max(core.env.execution_contract['gt_start_delay_minutes'],pending['requested']/float(core.env.config['gt_ramp_kW_per_hour'])*60.)
        remaining=max(0.,required-core._m22_gt_progress.get(i,0.))
        clocks.append(dict(kind='currently_started_GT',device=f'GT_{i+1}',remaining_minutes=remaining if paid else None,eligible_non_DG_target=paid,
            current_start_power_fully_supplied=paid,
            conditional_on_continuous_paid_start_and_current_conditions=True))
    if core.state.diesel_start_minute is not None and core.state.diesel_status in ('STARTING','READY'):
        remaining=max(0.,core.state.diesel_start_minute+core.env.config['diesel_start_delay_minutes']+core.env.execution_contract['ats_transfer_minutes']-now)
        clocks.append(dict(kind='currently_started_DG',remaining_minutes=remaining,eligible_non_DG_target=False,
            conditional_on_current_conditions_remaining_available=True))
    # Unknown command arrivals are intentionally not read from the physical
    # queue. The public readiness queue marks them unacknowledged/None.
    return clocks


def _finite_bridge(core,instantaneous):
    action=instantaneous.get('actual_action',{});ports={};durations=[]
    for name,prefix,store in (('ups','ups',core.ups),('cold','cold',core.cold_store)):
        power=action.get(prefix+'_discharge_kW');support=None
        if power is not None and float(power)>0.:
            upper=store.energy_kWh*store.config['discharge_efficiency']/float(power)
            support=store.boundary_hours(discharge_kW=float(power),charge_kW=0.,max_hours=upper)*60. if upper>0. else 0.
            durations.append(support)
        ports[name]=dict(energy_kWh=float(store.energy_kWh),actual_discharge_kW=None if power is None else float(power),
            discharge_efficiency=store.config['discharge_efficiency'],loss_per_hour=store.config['loss_per_hour'],
            support_minutes=support,support_basis='Current actual draw with original standing-loss convention; unused inventory is not infinite support')
    clocks=_known_clocks(core,action);targets=[float(x['remaining_minutes']) for x in clocks if x['eligible_non_DG_target']]
    required=min(targets) if targets else None
    support=min(durations) if durations else None
    enough=(bool(instantaneous.get('ready')) and support>=required-EPS) if support is not None and required is not None else None
    return dict(**ports,support_minutes_until_first_store_boundary=support,known_transition_clocks=clocks,
        required_support_minutes=required,supports_required_window=enough,target_capacity_proven=False,
        release_authorized=False,unknown_queue_arrivals_used=False,true_remaining_outage_used=False,
        interpretation='Conditional duration diagnostic only; neither a future source guarantee nor authority to release reserve')


def non_diesel_witness(core):
    instantaneous=M22Core.non_diesel_witness(core)
    _,row,total,f=core.data_at(core.time);loads=np.maximum(0.,total-core.state.disconnected_loads)
    candidate=copy.deepcopy(core.executed);candidate['diesel_kW']=0.
    if f['GRID']<=EPS:candidate['grid_import_kW']=0.
    sustained=dict(ready=False,status='no_connected_non_DG_source',current_only=True,pending_target_credited=False,inventory_committed=False)
    if sum(candidate['gt_electric_kW'])+candidate['grid_import_kW']>EPS:
        cfg=copy.deepcopy(core._physical_solver_config(core.env.dt_h,f))
        # Neither finite stored output nor a finite charging sink can prove
        # ongoing support. Startup and thermal-recovery work are transient.
        cfg['_sci_storage_ports']=dict(ups_discharge_kW=0.,ups_charge_kW=0.,cold_discharge_kW=0.,cold_charge_kW=0.,
            cold_pump_kW_per_kW=core.env.sci_config['cold_pump_kW_per_kW'])
        cfg['_model_closure_v22'].update(gt_start_kW=0.,boiler_restart_kW=0.,recovery_cooling_request_kW=0.)
        began=time.perf_counter()
        try:
            result=solve_physical(row,core.env.plant,loads,f,core.state,core.env.dt_h,
                float(core.env.scenario.get('grid_limit_kW',core.env.config['grid_import_limit_kW'])),False,
                float(core.env.config['gt_ramp_kW_per_hour']),float(core.env.config['solver_time_limit_sec']),cfg,
                execution_action=candidate,execution_contract=core.env.operating_execution_contract)
            deficit=np.asarray(result['lost'])[:2]+core.state.disconnected_loads[:2]
            sustained.update(ready=bool(np.max(deficit)<1e-4),status='solved',critical_unserved_kW=deficit.tolist(),
                actual_action=copy.deepcopy(result['action']),energy_balance_residuals=result['energy_balance_residuals'])
            core.solver_calls.append(dict(time_minute=core.time,phase='m23_sustained_non_DG_release_witness',status='success',
                current_only=True,inventory_committed=False,elapsed_seconds=time.perf_counter()-began,solver_backend_layers=copy.deepcopy(result.get('solver_backend_layers',[]))))
        except OSError:
            raise
        except Exception as exc:
            status='candidate_infeasible' if isinstance(exc,ModelConstraintInfeasible) else 'solver_error'
            sustained.update(status=status,error_type=type(exc).__name__,message=str(exc))
            core.solver_calls.append(dict(time_minute=core.time,phase='m23_sustained_non_DG_release_witness',status=status,
                current_only=True,inventory_committed=False,elapsed_seconds=time.perf_counter()-began,message=str(exc),
                solver_backend_layers=copy.deepcopy(getattr(exc,'solver_backend_attempts',[]))))
    bridge=_finite_bridge(core,instantaneous)
    return dict(ready=sustained['ready'],status='sustained_non_DG_ready' if sustained['ready'] else 'finite_bridge_only' if instantaneous.get('ready') else sustained['status'],
        instantaneous=instantaneous,sustained_non_DG=sustained,finite_bridge=bridge,
        actual_action=copy.deepcopy(sustained.get('actual_action')),critical_unserved_kW=copy.deepcopy(sustained.get('critical_unserved_kW')),
        current_only=True,pending_target_credited=False,inventory_committed=False,true_remaining_outage_used=False,
        release_rule='Sustained current connected non-DG sources only; finite bridges are diagnostic',
        sustained_definition='Store discharge/charge and transient startup/recovery sinks excluded; current loads and current hardware/permissions held, not a prediction')
