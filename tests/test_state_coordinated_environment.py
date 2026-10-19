"""C uses the ordinary M16 queue; telemetry meters completed actions only.

These are boundary/adapter tests with synthetic states and a fake dispatch
solver. They start no hospital trajectory or annual-data loader.
"""
import copy
import importlib
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def api():
    try:
        return importlib.import_module('tools.state_coordinated.environment')
    except ModuleNotFoundError as exc:
        pytest.fail('V17 ordinary coordinated environment is missing: ' + str(exc), pytrace=False)


def request():
    return {'state_coordinated_joint': {'coordination': {
        'response_minutes': 30., 'support_minutes': 60., 'delay_bound_minutes': 30.,
        'cooling_preparation': True, 'time_energy_preparation': True}}}


def core():
    from test_essential_execution import small_core
    original, loads, f, row = small_core()
    m = api()
    result = m.StateCoordinatedCore(original.env, original.executed)
    result.time = 10.
    result.data_at = original.data_at
    result.env.scenario.update(fault_start_minute=0., response_delay_minutes=20.)
    result.env.config['solver_time_limit_sec'] = 6.
    return result, loads, f, row


def terminal_action():
    return dict(gt_electric_kW=[145., 0., 0.], grid_import_kW=400., diesel_kW=50.,
        boiler_steam_lbph=75., hrsg_steam_lbph=1200., absorption_cooling_kW=40.,
        electric_chiller_cooling_kW=[10., 20., 30.], tower_rejection_kW=200.,
        gt_fuel_MMBtu_h=2.)


def test_c_command_is_queued_without_changing_actual_ports_or_consuming_fuel(monkeypatch):
    m = api()
    c, loads, _, _ = core()
    before = copy.deepcopy(c.executed)
    fuel = c.state.diesel_energy_remaining_kWh
    captured = {}
    def solve(*args, **kwargs):
        captured.update(kwargs)
        return dict(action=terminal_action(), lost=np.zeros((4, 3)),
                    planned_current_action=terminal_action(), joint_diagnostic={'test_only': True})
    monkeypatch.setattr(m.planner, 'solve_joint', solve)
    cmd = c.generate_command(request())
    assert c.executed == before
    assert c.state.diesel_energy_remaining_kWh == fuel
    assert c.time == 10. and c.active_command is None
    assert c.pending_commands == [cmd] and cmd['arrival_minute'] == 20.
    assert cmd['generated_minute'] == 10. and cmd['id'] == 1
    assert cmd['reference_cooling_kW'] == float(loads[:, 2].sum())
    assert cmd['joint_dispatch']['planning_is_not_execution'] is True
    assert 'operating_options' not in captured
    assert captured['coordination_options']['support_minutes'] == 60.
    context = captured['readiness_context']
    assert context['diesel_start_minute'] is None
    assert context['diesel_local_response']['rotor']['fuel_exhausted'] is False
    assert all(q['planned_arrival_minute'] is None for q in context['known_command_queue'])
    assert 'diesel_local_response' not in c.readiness_context()
    assert c.events[-1]['kind'] == 'command_generated'


def test_unrecognized_future_fields_are_rejected_before_queue_mutation():
    c, _, _, _ = core()
    bad = request()
    bad['state_coordinated_joint']['future_fault_time'] = 123.
    with pytest.raises(ValueError):
        c.generate_command(bad)
    assert c.pending_commands == [] and c.sequence == 0


def test_c_keeps_the_original_e_and_k1_generate_command_route(monkeypatch):
    m = api()
    from tools.essential_fixes.environment import EssentialOperatingCore
    c, _, _, _ = core()
    marker = object()
    seen = []
    def original(self, action):
        seen.append(copy.deepcopy(action))
        return marker
    monkeypatch.setattr(EssentialOperatingCore, 'generate_command', original)
    for action in ({'engineering_joint': {}}, {'operating_joint': {}}):
        assert c.generate_command(action) is marker
    assert seen == [{'engineering_joint': {}}, {'operating_joint': {}}]


def test_state_coordinated_core_inherits_the_same_m16_physical_transition():
    m = api()
    from tools.essential_fixes.environment import EssentialOperatingCore, model_contract
    assert m.StateCoordinatedCore.advance is EssentialOperatingCore.advance
    assert m.StateCoordinatedCore._correct_gt_floor is EssentialOperatingCore._correct_gt_floor
    assert m.model_contract('M16_essential', {}) == model_contract('M16_essential', {})


def test_environment_default_study_identity_matches_the_v17_manifest():
    from tools.state_coordinated.data_design import STUDY_CONTRACT_ID
    assert api().STUDY_CONTRACT_ID == STUDY_CONTRACT_ID


def test_solver_failure_is_retained_before_any_command_or_fake_action(monkeypatch):
    m = api()
    c, _, _, _ = core()
    before = copy.deepcopy(c.executed)
    def fail(*args, **kwargs):
        raise ValueError('synthetic C feasibility diagnostic')
    monkeypatch.setattr(m.planner, 'solve_joint', fail)
    with pytest.raises(ValueError, match='synthetic C feasibility diagnostic'):
        c.generate_command(request())
    assert c.failed is True and c.pending_commands == []
    assert c.executed == before
    assert c.errors[-1]['phase'] == 'state_coordinated_command_generation'
    assert c.errors[-1]['message'] == 'synthetic C feasibility diagnostic'


def test_reset_injects_new_command_core_without_replacing_m16_registration_checks(monkeypatch):
    m = api()
    from tools.essential_fixes import environment as base
    captured = {}
    def native_reset(self, *, scenario, seed=None):
        captured['core_type'] = TransitionCore
        captured['validator'] = validate_scenario
        captured['scenario'] = scenario
        return 'synthetic_reset'
    monkeypatch.setattr(base.NativeEnvironment, 'reset', native_reset)
    env = object.__new__(m.OperatingHospitalIESEnv)
    env.model_id = 'M16_essential'
    env.allowed_hours = {1, 2}
    env.sealed_days = set()
    scene = dict(start_hour=1, duration_steps=4, step_minutes=15.)
    assert env.reset(scenario=scene, seed=17) == 'synthetic_reset'
    assert captured['core_type'] is m.StateCoordinatedCore
    assert captured['validator'] is m.validate_scenario
    assert env.planning_queries == []
    env.allowed_hours = {1}
    with pytest.raises(ValueError):
        env.reset(scenario=scene, seed=17)


def matched_l4b():
    return dict(stage=4, start_hour=1, duration_steps=8, event_class='L4b',
        event_schema_version='p2a.events.v1', faults={'GRID': 0.}, shock={'e': .35},
        enable_propagation=False, v17_matched_propagation_ablation=True)


def test_explicit_v17_l4b_ablation_changes_only_the_propagation_flag_contract():
    from hospital_ies.resilience import validate_scenario as original
    scene = matched_l4b()
    before = copy.deepcopy(scene)
    with pytest.raises(ValueError, match='enable_propagation'):
        original(scene)
    checked = api().validate_scenario(scene)
    assert checked == scene and scene == before
    assert checked['stage'] == 4 and checked['faults'] == {'GRID': 0.}
    assert checked['enable_propagation'] is False


@pytest.mark.parametrize('patch', [
    {'v17_matched_propagation_ablation': False},
    {'v17_matched_propagation_ablation': 1},
    {'stage': 3, 'event_class': 'L3', 'shock': {}},
    {'event_class': 'L4a', 'faults': {}},
    {'faults': {'GRID': 2.}},
    {'event_schema_version': 'invalid'},
])
def test_v17_ablation_adapter_retains_all_other_native_rejections(patch):
    scene = matched_l4b()
    scene.update(patch)
    with pytest.raises(ValueError):
        api().validate_scenario(scene)


def meter_env(*, now=20., end=15.):
    action = terminal_action()
    action['grid_import_kW'] = 120.
    return SimpleNamespace(core=SimpleNamespace(time=now, segments=[dict(
        start_minute=0., end_minute=end, command_id=7, action=action)],
        executed=dict(grid_import_kW=850., absorption_cooling_kW=999.)))


def test_completed_actual_telemetry_never_substitutes_arrived_caps_or_changes_ledger():
    m = api()
    env = meter_env()
    before = copy.deepcopy(env.core.segments)
    value = m.actual_supply_telemetry(env)
    assert value['actual_dispatch']['grid_import_kW'] == 120.
    assert value['actual_dispatch']['absorption_cooling_kW'] == 40.
    assert value['interval_end_minute'] == value['valid_time_minute'] == 15.
    assert value['available_time_minute'] == value['observation_time_minute'] == 20.
    assert value['staleness_minutes'] == 5.
    assert value['sample_kind'] == 'completed_interval_average'
    value['actual_dispatch']['electric_chiller_cooling_kW'][0] = 999.
    assert env.core.segments == before


def test_initial_actual_telemetry_explicitly_has_no_completed_delivery():
    m = api()
    env = SimpleNamespace(core=SimpleNamespace(time=0., segments=[]))
    value = m.actual_supply_telemetry(env)
    assert value['actual_dispatch'] is None
    assert value['valid_time_minute'] is None
    assert value['staleness_minutes'] is None
    assert value['sample_kind'] == 'no_completed_interval'


@pytest.mark.parametrize('now,end', [(20., 21.), (20., 0.), (float('nan'), 15.)])
def test_actual_telemetry_rejects_uncompleted_or_invalid_clocks(now, end):
    with pytest.raises(ValueError):
        api().actual_supply_telemetry(meter_env(now=now, end=end))


def test_additive_snapshot_is_current_and_does_not_expose_queue_release(monkeypatch):
    m = api()
    original = dict(decision_time_min=20., observation_time_min=20.,
        known_command_queue=[dict(command_id=1, planned_arrival_minute=None)])
    before = copy.deepcopy(original)
    monkeypatch.setattr(m.old, 'decision_snapshot', lambda env: original)
    value = m.decision_snapshot(meter_env())
    assert original == before
    assert value['known_command_queue'][0]['planned_arrival_minute'] is None
    assert value['actual_supply_telemetry']['actual_dispatch']['grid_import_kW'] == 120.


def test_episode_adapter_uses_the_same_snapshot_for_policy_and_export(monkeypatch, tmp_path):
    m = api()
    expected = m.decision_snapshot
    def no_episode(env, scenario, policy, seed, manifest):
        return dict(decisions=[],
            policy_snapshot_is_custom=PolicyEnvironment.step.__globals__['decision_snapshot'] is expected,
            exporter_snapshot_is_custom=run_trajectory.__globals__['decision_snapshot'] is expected,
            combined_validator=PolicyEnvironment.step.__globals__['validate_action'])
    monkeypatch.setattr(m.old.native_episode, 'run_episode', no_episode)
    monkeypatch.setattr(m.old, 'ROOT', tmp_path)
    env = SimpleNamespace(model_id='M16_essential', operating_model_config={},
        operating_execution_contract={}, input_access_receipt={})
    payload = m.run_episode(env, dict(start_hour=1, duration_steps=1), None, 17,
                            {'model_config_signature': 'test', 'study_contract_id': 'v17_test'})
    assert payload['policy_snapshot_is_custom'] and payload['exporter_snapshot_is_custom']
    assert payload['combined_validator'] is m.validate_action
    assert payload['engineering_model_contract_id'] == 'hospital_ies.essential_engineering.v16.v1'
    assert payload['study_contract_id'] == 'v17_test'
    assert payload['actual_supply_telemetry_schema'] == m.TELEMETRY_SCHEMA
