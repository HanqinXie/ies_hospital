"""Current-observation startup bridge regression; no physical episodes."""
import importlib.util
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('v18_bridge_math',ROOT/'outputs/v18_execution_recovery_20261018_run01/review/c18_key02_handoff/bridge_replay.py')
replay=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay)


def test_existing_running_gt_is_retained_for_the_cold_diesel_startup_bridge():
    from tools.execution_recovery import planner
    kwargs=replay.inputs();result=planner.solve_joint(**kwargs)
    assert np.asarray(result['lost'])[:2].max()<.001
    # The recorded legal starting GT can supply the coupled critical carriers
    # through the original fifteen-minute DG startup; all-off cannot do so.
    assert result['action']['gt_electric_kW'][0]>=kwargs['readiness_context']['actual_gt_kW'][0]-1e-7
    bridge=result['joint_diagnostic']['coordination']['startup_bridge']
    assert np.asarray(bridge['unserved_kW'])[:2].max()<.001
    assert bridge['gt_electric_kW'][0]>0.
    assert bridge['gt_electric_kW'][1:]==[0.,0.]
    assert bridge['diesel_kW']==0.
    assert bridge['grid_import_kW']==0.


def test_endpoint_with_cold_diesel_does_not_claim_executable_bridge_reserve():
    from tools.execution_recovery import planner
    result=planner.solve_joint(**replay.inputs(no_running_GT=True))
    coordination=result['joint_diagnostic']['coordination']
    endpoint=coordination['response_scenarios']['response_GRID_loss']
    assert endpoint['diesel_kW']>0.
    assert endpoint['has_physical_balance_certificate']
    # A true static R-endpoint balance cannot erase the pre-connection gap.
    assert endpoint['executable_preparation_credit'] is False
    bridge=coordination['startup_bridge']
    assert bridge['gt_electric_kW']==[0.,0.,0.]
    assert bridge['diesel_kW']==0.
    assert bridge['status']=='no_current_ready_port_bridge'
    assert bridge['ordinary_GT_startup_still_subject_to_original_delay'] is True
    assert bridge['unserved_kW'][0][0]>.01
    assert bridge['unserved_kW'][0][2]>.01


def test_all_gt_hardware_unavailable_reports_bridge_deficit_without_solver_abort():
    from tools.execution_recovery import planner
    result=planner.solve_joint(**replay.inputs(unavailable_GT=True))
    assert np.asarray(result['lost'])[:2].max()<.001  # current healthy GRID
    bridge=result['joint_diagnostic']['coordination']['startup_bridge']
    assert bridge['status']=='no_current_ready_port_bridge'
    assert bridge['gt_electric_kW']==[0.,0.,0.]
    assert bridge['diesel_kW']==0.
    assert bridge['unserved_kW'][0][0]>.01


def test_connected_diesel_is_not_credited_across_the_open_return_transfer():
    from tools.execution_recovery import planner
    kwargs=replay.saved.inputs(replay.saved.fixture())
    assert kwargs['readiness_context']['diesel_connected']
    result=planner.solve_joint(**kwargs)
    bridge=result['joint_diagnostic']['coordination']['startup_bridge']
    assert bridge['diesel_kW']==bridge['grid_import_kW']==0.
    assert bridge['eligible_currently_running_GT_mask']==[False,False,True]


def test_original_fixed_source_equations_admit_the_retained_observed_bridge():
    kwargs=replay.inputs()
    original=replay.json.loads((replay.HERE/'BRIDGE_FIXTURE.json').read_text(encoding='utf-8'))['original_queued_dispatch']
    retained=replay.fixed_source_balance(kwargs,original,retain_observed_GT=True)
    stopped=replay.fixed_source_balance(kwargs,original,retain_observed_GT=False)
    assert np.asarray(retained['lost'])[:2].max()<.001
    assert retained['action']['gt_electric_kW']==kwargs['readiness_context']['actual_gt_kW']
    assert retained['action']['diesel_kW']==retained['action']['grid_import_kW']==0.
    assert retained['action']['boiler_steam_lbph']==original['boiler_steam_lbph']
    assert max(map(abs,retained['energy_balance_residuals']))<1e-7
    assert stopped['lost'][0,0]>.01
    assert stopped['lost'][0,2]>.01


def test_finite_support_preference_preserves_a_useful_arrived_pending_start():
    from tools.execution_recovery import planner
    f=replay.json.loads((replay.HERE/'NUMERIC02_PENDING_FIXTURE.json').read_text(encoding='utf-8'))
    kwargs=replay.saved.inputs(f);kwargs['execution_limits']=f['execution_limits']
    assert f['readiness_context']['pending_starts'][2]==dict(started=47.5,ready=62.5,requested=225.)
    result=planner.solve_joint(**kwargs)
    assert result['action']['gt_electric_kW'][2]==225.
    # The old arrived process still needs its original final 2.5 minutes.
    assert result['planned_current_action']['gt_electric_kW']==[0.,0.,0.]
    assert result['action']['diesel_kW']>0.
    assert result['joint_diagnostic']['coordination']['startup_bridge']['gt_electric_kW']==[0.,0.,0.]
    assert np.asarray(result['lost'])[:2].max()<.001
    arrived=result['joint_diagnostic']['risk_scenarios']['arrived_current']
    assert np.asarray(arrived['unserved_kW'])[:2].max()<.001
    names=[item['name'] for item in result['joint_diagnostic']['layers']]
    assert names.index('standby_third_GT_exposure')<names.index('conditional_DG_endpoint_pressure')
    assert names.index('conditional_DG_endpoint_pressure')<names.index('normal_online_GT_count_after_preparation')


def test_pending_fuel_preference_still_allows_cancelling_a_useless_start():
    from tools.execution_recovery import planner
    f=replay.json.loads((replay.HERE/'NUMERIC02_PENDING_FIXTURE.json').read_text(encoding='utf-8'))
    f['loads_kW']=np.zeros((4,3)).tolist();f['request']['risk_loads_kW']=np.zeros((4,3)).tolist()
    f['readiness_context']['observed_critical_unserved_kW']=np.zeros((2,3)).tolist()
    kwargs=replay.saved.inputs(f);kwargs['execution_limits']=f['execution_limits']
    result=planner.solve_joint(**kwargs)
    assert result['action']['gt_electric_kW']==[0.,0.,0.]
