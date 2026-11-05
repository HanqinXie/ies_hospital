"""Production-backend counterexample: unreachable room cooling, paid tank charge.

This is an explicitly synthetic, small port fixture. Native inputs and plant
parameters are unchanged; only its current load/auxiliary observation is set.
"""
import copy
import json
import sys
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_sci_environment import scenario


def cold_case(initial_soc=0., *, historical=False, negative=None, engineering_options=None,correctness_options=None):
    from tools.model_correctness_v23.environment import OperatingHospitalIESEnv as Historical
    if historical:
        Environment=Historical
    else:
        try:
            from tools.engineering_completion_v24.environment import OperatingHospitalIESEnv as Environment
        except ModuleNotFoundError:
            Environment=Historical # RED exercises the actual missing behavior.
    authority=json.loads((ROOT/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json').read_text(encoding='utf-8'))
    sealed=[int(x['time_block_id'].rsplit('_',1)[1]) for x in authority['p2b_time_blocks'] if x['reserved_only']]
    config=dict(sci_engineering=dict(cold_store=dict(capacity_kWh=60.,power_kW=120.,initial_soc=initial_soc)))
    if engineering_options is not None:config['engineering_completion_v24']=engineering_options
    if correctness_options is not None:config['model_correctness_v23']=correctness_options
    env=Environment(ROOT,allowed_hours=[4001,4002],sealed_days=sealed,model_config=config)
    spec=scenario();spec.update(grid_limit_kW=100.,fault_start_minute=0.,fault_duration_minutes=45.,
        faults=dict(GT_1=0.,GT_2=0.,GT_3=0.,DIESEL=0.,ABS=0.,BOILER_1=0.,BOILER_2=0.,BOILER_3=0.),
        model_closure_events=[dict(time_minute=0.,availability={'AUX_FAN_P0':0.})])
    env.reset(scenario=spec,seed=9)
    core=env.core;original=core._hardware_data_at
    def small_current(minute):
        h,row,_,availability=original(minute)
        row=copy.copy(row);loads=np.zeros((4,3));loads[0]=[20.,0.,100.]
        for group in range(4):
            for carrier,index in [('e',0),('h',1),('c',2)]:setattr(row,f'P{group}_reference_{carrier}_kW',loads[group,index])
            for kind in ('fan','chw_pump','heat_pump'):setattr(row,f'P{group}_{kind}_kW',0.)
        row.P0_fan_kW=5.;row.P0_chw_pump_kW=2.
        availability.update(EC_2=0.,EC_3=0.)
        if negative=='pump':availability['AUX_CHW_PUMP']=0.
        if negative=='source':availability['EC_1']=0.
        if negative=='tower':availability['TOWER']=0.
        return h,row,loads,availability
    core._hardware_data_at=small_current
    permission=dict(core.executed,gt_electric_kW=[0.,0.,0.],grid_import_kW=100.,diesel_kW=0.,
        boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[100.,0.,0.])
    if negative=='permission':permission['electric_chiller_cooling_kW']=[0.,0.,0.]
    if negative=='electric_permission':permission['grid_import_kW']=15.
    core.executed=copy.deepcopy(permission);core.arrived_dispatch=copy.deepcopy(permission)
    core.pending_commands=[]
    return env,permission


@pytest.mark.parametrize('initial_soc',[0.,.1])
def test_unreachable_service_can_pay_to_charge_without_reducing_any_service(initial_soc):
    env,permission=cold_case(initial_soc);core=env.core
    _,_,loads,availability=core.data_at(0.)
    before=(core.time,core.cold_store.energy_kWh,core.station.diesel_starts_used,copy.deepcopy(core.pending_commands))
    result=core._solve(loads,availability,1./60.,'v24_counterexample',{},execution_action=permission)
    assert result['action']['cold_charge_kW']==pytest.approx(100.,abs=1e-5)
    assert result['action']['cold_discharge_kW']==0.
    assert result['action']['grid_import_kW']==pytest.approx(38.89457323,abs=1e-5)
    assert result['action']['cold_storage_pump_kW']==pytest.approx(2.,abs=1e-6)
    assert before==(core.time,core.cold_store.energy_kWh,core.station.diesel_starts_used,core.pending_commands)
    assert all(layer['backend']=='gurobipy' for layer in result['solver_backend_layers'])
    old,old_permission=cold_case(initial_soc,historical=True);_,_,old_loads,old_f=old.core.data_at(0.)
    historical=old.core._solve(old_loads,old_f,1./60.,'v23_counterexample',{},execution_action=old_permission)
    assert historical['action']['cold_charge_kW']==0.
    # Exact equality was too strong: historical final-cost dispatch consumes
    # 2e-6 kW of the existing P0 numerical lock, while the new recharge guard
    # retains its exact witness. The contractual condition is no reduction.
    assert np.all(np.asarray(result['lost'])<=np.asarray(historical['lost'])+1e-9)
    assert result['lost'][0,2]==historical['lost'][0,2]==100.
    assert result['cost_USD_step']>historical['cost_USD_step']


@pytest.mark.parametrize('negative',['pump','source','tower','permission','electric_permission'])
def test_charge_requires_real_source_pump_tower_and_arrived_permission(negative):
    env,permission=cold_case(negative=negative);core=env.core;_,_,loads,availability=core.data_at(0.)
    result=core._solve(loads,availability,1./60.,'v24_negative',{},execution_action=permission)
    assert result['action']['cold_charge_kW']==0.
    assert core.cold_store.energy_kWh==0.


def test_source_shortage_cannot_recharge_service_lock_slack():
    env,permission=cold_case();core=env.core;_,_,loads,availability=core.data_at(0.)
    availability['AUX_FAN_P0']=1.;availability['EC_1']=.01
    result=core._solve(loads,availability,1./60.,'v24_source_shortage',{},execution_action=permission)
    assert result['lost'][0,2]>1.
    assert result['action']['cold_charge_kW']==0.
    assert core.cold_store.energy_kWh==0.


def test_actual_interval_commits_paid_charge_once():
    env,_=cold_case();core=env.core;core.advance(1.)
    segments=env.segment_history
    assert segments and segments[0]['start_minute']==0. and segments[-1]['end_minute']==1.
    charged=sum(x['m21_storage']['cold_store']['charged_input_kWh'] for x in segments)
    assert charged==pytest.approx(100./60.,abs=1e-6)
    assert core.cold_store.energy_kWh==pytest.approx(100./60.*.95,abs=1e-6)
    assert all(abs(x['m21_storage']['cold_store']['balance_residual_kWh'])<1e-8 for x in segments)
    assert all(x['action']['cold_storage_pump_kW']>0. for x in segments)


def test_repeated_fan_failure_charge_and_source_failure_discharge_balance():
    env,_=cold_case();core=env.core
    env.model_closure_events=[
        dict(time_minute=0.,availability={'AUX_FAN_P0':0.}),
        dict(time_minute=1.,availability={'AUX_FAN_P0':1.,'EC_1':0.}),
        dict(time_minute=2.,availability={'AUX_FAN_P0':0.,'EC_1':1.}),
        dict(time_minute=3.,availability={'AUX_FAN_P0':1.,'EC_1':0.}),
        dict(time_minute=4.,availability={'EC_1':1.})]
    core.advance(5.)
    segments=env.segment_history
    charge=[x for x in segments if x['action']['cold_charge_kW']>1.]
    discharge=[x for x in segments if x['action']['cold_discharge_kW']>1.]
    assert len(charge)>=2 and len(discharge)>=2
    assert all(x['availability']['AUX_FAN_P0']==0. for x in charge)
    assert all(x['availability']['AUX_FAN_P0']==1. and x['availability']['EC_1']==0. for x in discharge)
    first=[x for x in discharge if 1.<=x['start_minute']<2.]
    assert first[-1]['end_minute']-1.==pytest.approx(.9025,abs=1e-7)
    assert all(x['action']['cold_charge_kW']==0. for x in segments if x['availability']['EC_1']==0.)
    assert all(x['execution_model_id']=='M24_engineering_completion' for x in segments)
    assert all(x['action']['cold_storage_pump_kW']>0. for x in charge+discharge)
    charged=sum(x['m21_storage']['cold_store']['charged_input_kWh'] for x in segments)
    discharged=sum(x['m21_storage']['cold_store']['discharged_kWh'] for x in segments)
    losses=sum(x['m21_storage']['cold_store']['conversion_loss_kWh']+x['m21_storage']['cold_store']['standing_loss_kWh'] for x in segments)
    assert core.cold_store.energy_kWh==pytest.approx(charged-discharged-losses,abs=1e-8)
    assert all(abs(x['m21_storage']['cold_store']['balance_residual_kWh'])<1e-8 for x in segments)
    assert sum(x['cost_USD_step'] for x in segments)>0.


def test_partial_inventory_source_shortage_depletes_without_any_recharge_pulse():
    env,_=cold_case(initial_soc=1./60.);core=env.core
    env.model_closure_events=[dict(time_minute=0.,availability={'AUX_FAN_P0':1.,'EC_1':.01})]
    core.advance(3.)
    segments=env.segment_history
    assert all(x['action']['cold_charge_kW']==0. for x in segments)
    assert core.cold_store.energy_kWh<1e-8
    assert sum(x['m21_storage']['cold_store']['discharged_kWh'] for x in segments)==pytest.approx(.95,abs=1e-8)
    assert all(abs(x['m21_storage']['cold_store']['balance_residual_kWh'])<1e-8 for x in segments)


def test_optional_charge_solver_failure_is_recorded_and_keeps_legal_no_charge():
    from hospital_ies.execution import SolverExecutionError
    from tools.engineering_completion_v24.cold_modes import search
    env,permission=cold_case();core=env.core;_,_,loads,availability=core.data_at(0.)
    solve=core._storage_solver(loads,availability,1./60.,execution_action=permission)
    baseline=solve(core._base_ports())
    def failed_optional_probe(ports,extra=None):
        if extra and extra.get('_m24_exact_service_guard'):
            error=SolverExecutionError('explicit regression: optional witness backend unavailable')
            error.solver_backend_attempts=[{'backend':'injected_test_failure','status':4}]
            raise error
        return solve(ports,extra)
    result,ports,diagnostic=search(core,failed_optional_probe,baseline,loads,availability,1./60.)
    assert result is baseline and ports['cold_charge_kW']==0.
    assert diagnostic['candidate_solver_errors']==1
    assert diagnostic['safe_no_charge_retained_after_probe_failure']
    failed=[x for x in diagnostic['candidates'] if x['status']=='candidate_solver_error']
    # Full failure proof now lives once in the existing solver log; the
    # per-segment candidate receipt retains its index and compact statuses.
    assert len(failed)==1
    assert core.solver_calls[failed[0]['solver_call_index']]['solver_backend_layers'][0]['status']==4
    assert core.cold_store.energy_kWh==0. and core.time==0.


def test_explicit_tower_kw_capacity_reaches_real_solver_and_is_logged():
    # The declared 1.25 research boundary is feasible for the unchanged
    # normal initialization. A 100-kW whole-plant tower is physically
    # infeasible before this synthetic current-port fixture is installed.
    capacity=5275.27926
    env,permission=cold_case(engineering_options={'tower_rejection_capacity_kW':capacity})
    assert env.plant.cooling_tower_rejection_kW==capacity
    core=env.core;_,_,loads,availability=core.data_at(0.)
    result=core._solve(loads,availability,1./60.,'v24_tower_capacity',{},execution_action=permission)
    rows=[row for layer in result['solver_backend_layers'] for attempt in layer['attempts']
        for row in attempt.get('validation',{}).get('rows',[]) if row.get('name')=='tower.rejection']
    assert rows and all(row['upper']==capacity for row in rows)
    core.advance(.1)
    assert all(x['m24_tower_capacity']['capacity_kW']==capacity for x in env.segment_history)
    assert all(x['m24_tower_capacity']['basis']=='explicit_research_kW_assumption' for x in env.segment_history)


def test_whole_plant_tower_below_unchanged_normal_requirement_is_not_silently_repaired():
    with pytest.raises(RuntimeError,match='MILP failed'):
        cold_case(engineering_options={'tower_rejection_capacity_kW':100.})


def test_m24_default_tower_capacity_keeps_historical_value_and_recorded_basis():
    env,_=cold_case();old,_=cold_case(historical=True)
    assert env.plant.cooling_tower_rejection_kW==pytest.approx(old.plant.cooling_tower_rejection_kW,abs=1e-10)
    assert env.operating_model_config['engineering_completion_v24']['tower_rejection_capacity_kW']==4220.223408
    assert env.config['_engineering_completion_v24']['tower_rejection_capacity_kW']==4220.223408


def test_shared_episode_runner_uses_m24_snapshot_and_actual_identity():
    from hospital_ies import dataset_export
    from tools.engineering_completion_v24 import MODEL_ID,CONTRACT_ID
    from tools.engineering_completion_v24.environment import run_episode
    from tools.model_correctness_v23.policy import SciPolicy
    env,_=cold_case();spec=scenario();spec.update(duration_steps=1,faults={'GRID':1.},fault_start_minute=0.,fault_duration_minutes=15.)
    spec=dataset_export.normalize_scenario(spec,9)
    manifest=dict(signature='m24-small-regression',simulator_hash='test-only-source',execution_contract_id=CONTRACT_ID,
        assumption_set_id=CONTRACT_ID,model_config_signature='m24-small-config',schemas={},
        splits={spec['id']:dict(group_id=spec['family_id'],split='development',fit_allowed=False)})
    snapshots=[]
    class ObservedPolicy(SciPolicy):
        def decide(self,records):
            snapshots.extend(records)
            return super().decide(records)
    result=run_episode(env,spec,ObservedPolicy('R',plant=env.plant),9,manifest)
    assert result['summary']['status']=='completed',result.get('error')
    assert result['execution_model_id']==MODEL_ID and result['study_contract_id']==CONTRACT_ID
    assert snapshots and all(x['engineering_execution_model_id']==MODEL_ID for x in snapshots)
    assert all(x['execution_model_id']==MODEL_ID for x in result['segments'])


def test_segment_mode_receipt_uses_existing_solver_log_without_raw_row_duplication():
    env,_=cold_case();env.core.advance(.1)
    for segment in env.segment_history:
        receipt=segment['m24_cold_mode_selection']
        encoded=json.dumps(receipt)
        assert 'raw_x' not in encoded and '"rows"' not in encoded
        for candidate in receipt['candidates']:
            call=env.core.solver_calls[candidate['solver_call_index']]
            assert call['phase']=='m24_cold_mode_candidate'
            assert call['solver_backend_layers']


def test_m24_validation_aliases_reuse_historical_validation_without_new_semantics():
    from tools.engineering_completion_v24 import environment
    from tools.model_correctness_v23 import environment as historical
    assert environment.validate_scenario is historical.validate_scenario
    assert environment.validate_events is historical.validate_events


def charge_only_case(*,no_charge_path=False,initial_soc=0.):
    from hospital_ies.model import gt_level_properties
    env,permission=cold_case(initial_soc=initial_soc);core=env.core;previous=core._hardware_data_at
    minimum=float(gt_level_properties(env.plant,float(core.data_at(0.)[1].outdoor_temperature_C))[0][0])
    def current(minute):
        hour,row,loads,availability=previous(minute);row=copy.copy(row);loads=loads.copy()
        loads[0,0]=102.;row.P0_reference_e_kW=102.;availability['GT_2']=1.
        return hour,row,loads,availability
    core._hardware_data_at=current
    permission.update(gt_electric_kW=[0.,minimum,0.],grid_import_kW=0.,electric_chiller_cooling_kW=[0. if no_charge_path else 150.,0.,0.])
    core.executed=copy.deepcopy(permission);core.arrived_dispatch=copy.deepcopy(permission)
    return env,permission


def test_charge_only_feasible_domain_is_searched_when_idle_has_no_electric_sink():
    from hospital_ies.execution import ModelConstraintInfeasible
    env,permission=charge_only_case();core=env.core;_,_,loads,availability=core.data_at(0.)
    solve=core._storage_solver(loads,availability,1./60.,execution_action=permission)
    with pytest.raises(ModelConstraintInfeasible):solve(core._base_ports())
    before=(core.time,core.cold_store.energy_kWh,copy.deepcopy(core.executed))
    result=core._solve(loads,availability,1./60.,'v24_charge_only_domain',{},execution_action=permission)
    from hospital_ies.model import RT_TO_KW
    # The independent original-lock witness was 118.050066 kW. Exact
    # service preservation delivers the 1.02e-5 kW that its cost lock had
    # ceded, so derive the guarded value from the unchanged energy balance.
    expected=(permission['gt_electric_kW'][1]-97.)/(env.plant.ec_electric_kW_per_rt/RT_TO_KW+env.sci_config['cold_pump_kW_per_kW'])
    assert result['action']['cold_charge_kW']==pytest.approx(expected,abs=1e-7)
    assert result['action']['gt_electric_kW']==permission['gt_electric_kW']
    assert result['lost'][0,0]==pytest.approx(5.,abs=1e-7) and result['lost'][0,2]==100.
    assert max(map(abs,result['energy_balance_residuals']))<1e-7
    assert result['m22_mode_selection']['no_charge_domain_status']=='registered_domains_infeasible'
    assert result['m22_mode_selection']['charge_guard_reference_domain']=='charge'
    assert result['m22_mode_selection']['no_store_critical_cold_deficit_kW'] is None
    assert before==(core.time,core.cold_store.energy_kWh,core.executed)
    core._select_current_ports(loads,availability)
    assert core._m22_ports['cold_charge_kW']>0. and not core.errors


def test_absent_sink_in_every_registered_mode_still_raises_with_candidate_evidence():
    from hospital_ies.execution import ModelConstraintInfeasible
    env,permission=charge_only_case(no_charge_path=True);core=env.core;_,_,loads,availability=core.data_at(0.)
    with pytest.raises(ModelConstraintInfeasible) as captured:
        core._solve(loads,availability,1./60.,'v24_no_legal_domain',{},execution_action=permission)
    diagnostic=captured.value.command_compilation_diagnostic
    assert diagnostic['status']=='registered_storage_domains_exhausted'
    assert any(x['cold_mode']=='idle' for x in diagnostic['candidates'])
    assert any(x['cold_mode']=='charge_witness' for x in diagnostic['candidates'])
    assert core.executed['gt_electric_kW']==permission['gt_electric_kW']
    assert core.cold_store.energy_kWh==0. and core.time==0.


def test_cold_mode_search_rejects_missing_original_hierarchy_contract():
    with pytest.raises(ValueError,match='requires.*boiler_domain_search'):
        cold_case(correctness_options={'boiler_domain_search':False})
    env,_=cold_case(correctness_options={'boiler_domain_search':False},engineering_options={'cold_mode_search':False})
    assert not env.engineering_completion_config['cold_mode_search']


def test_disabled_inherited_event_fix_is_explicit_in_m24_mode_receipt():
    env,permission=cold_case(correctness_options={'storage_fix':False});core=env.core
    _,_,loads,availability=core.data_at(0.)
    result=core._solve(loads,availability,1./60.,'v24_explicit_disabled_event_fix',{},execution_action=permission)
    assert result['m22_mode_selection']['storage_event_fix_enabled'] is False


def test_actual_charge_only_domain_reaches_sink_interlock_before_gt_is_stopped():
    env,permission=charge_only_case();core=env.core
    core.advance(.1)
    assert env.segment_history and all(x['action']['gt_electric_kW']==permission['gt_electric_kW'] for x in env.segment_history)
    assert all(x['action']['cold_charge_kW']>100. for x in env.segment_history)
    assert core.cold_store.energy_kWh>0.
    assert not any(x['kind']=='no_export_interlock' for x in core.events)
    assert all(abs(x['m21_storage']['cold_store']['balance_residual_kWh'])<1e-8 for x in env.segment_history)


def test_same_time_connected_load_change_invalidates_m24_current_mode_cache():
    env,_=cold_case(initial_soc=.1);core=env.core;_,_,loads,availability=core.data_at(0.)
    availability['AUX_FAN_P0']=1.
    core._select_current_ports(loads,availability)
    assert core._m22_ports['cold_charge_kW']==0.
    changed=loads.copy();changed[0,2]=0.
    core._select_current_ports(changed,availability)
    assert core._m22_ports['cold_charge_kW']>0.
    assert core.time==0. and core.cold_store.energy_kWh==6.


def test_charge_only_sink_can_use_real_near_full_headroom_below_maintenance_deadband():
    env,permission=charge_only_case(initial_soc=.99);core=env.core
    assert not core.cold_recharge.allowed(core.cold_store.energy_kWh,60.)
    _,_,loads,availability=core.data_at(0.)
    result=core._solve(loads,availability,.1/60.,'v24_near_full_charge_only',{},execution_action=permission)
    assert result['action']['cold_charge_kW']>100.
    assert result['m22_mode_selection']['maintenance_recharge_deadband_bypassed']
    core.advance(.1)
    assert all(x['action']['gt_electric_kW']==permission['gt_electric_kW'] for x in env.segment_history)
    assert 59.4<core.cold_store.energy_kWh<60.


def test_maintenance_deadband_is_kept_when_no_charge_operation_is_legal():
    env,permission=cold_case(initial_soc=.99);core=env.core;_,_,loads,availability=core.data_at(0.)
    result=core._solve(loads,availability,.1/60.,'v24_maintenance_deadband',{},execution_action=permission)
    assert result['action']['cold_charge_kW']==0.
    assert not result['m22_mode_selection']['maintenance_recharge_deadband_bypassed']
