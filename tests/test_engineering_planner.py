"""v14 real, small current-state joint problems; no annual fixtures or raw."""
import copy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from hospital_ies.execution import execution_contract
from hospital_ies.model import load_plant, RT_TO_KW, gt_level_properties
from hospital_ies.resilience import RuntimeState, empty_faults
from tools.coordinated_dispatch import planner as old


def api():
    try:
        return importlib.import_module('tools.engineering_model.planner')
    except ModuleNotFoundError:
        pytest.fail('Versioned engineering planner is missing')


@pytest.fixture
def problem():
    return dict(row=SimpleNamespace(timestamp=pd.Timestamp('2025-01-15 10:00'), outdoor_temperature_C=20.),
        plant=load_plant(ROOT / 'configs/plant_configs.json', 'doe_capacity_matched_exploratory'),
        loads=np.array([[120., 0., 100.], [110., 0., 80.], [50., 0., 40.], [417., 0., 368.]]),
        availability=empty_faults(), state=RuntimeState(np.array([125., 0., 156.]), 2400., set(), np.zeros(3), {}),
        dt_h=.25, grid_import_limit_kW=350., diesel_start_available=False, gt_ramp_kW_per_h=900.,
        solver_time_limit_sec=6., config=json.loads((ROOT / 'configs/resilience_assumptions.json').read_text(encoding='utf-8')),
        execution_contract=execution_contract({'grid_import_mode': 'arrived_ceiling',
            'gt_local_down_rate_kW_per_hour': 900., 'gt_local_down_delay_minutes': .25}))


def context(p, **changes):
    return dict(actual_gt_kW=p['state'].gt_prev.tolist(), gt_running=(p['state'].gt_prev > 1e-9).tolist(),
        gt_can_start=[True]*3, gt_can_stop=[True]*3, gt_start_pending=[False]*3, **changes)


BASE = dict(variant='coupled', regulator_consistent=True, critical_reserve_multiplier=1.25,
    preserve_one_gt_standby=True, connection_mode='retain_when_no_gt')


def test_old_normal_preparation_uses_extra_cut_despite_joint_zero_service_optimum(problem):
    r = old.solve_joint(**problem, joint_options=BASE)
    assert r['joint_diagnostic']['service_reference']['normalized_lex_values'][2:] == [0., 0.]
    assert r['lost'][2:, 0].sum() > 1.


def test_current_joint_service_is_restored_in_one_generated_command(problem):
    before = copy.deepcopy(problem['state'])
    r = api().solve_joint(**problem, joint_options=BASE, execution_context=context(problem))
    assert np.max(r['lost']) < .02
    assert np.max(r['command_disconnected']) < .02
    assert max(map(abs, r['energy_balance_residuals'])) <= 1e-7
    assert r['joint_diagnostic']['engineering']['service_order'] == ['P0', 'P1', 'P2', 'P3']
    np.testing.assert_array_equal(problem['state'].gt_prev, before.gt_prev)


def test_equivalent_gt_combinations_keep_actual_running_ports(problem):
    r = api().solve_joint(**problem, joint_options=BASE, execution_context=context(problem))
    assert (np.array(r['action']['gt_electric_kW']) > 1e-4).tolist() == [True, False, True]
    assert r['joint_diagnostic']['engineering']['GT_command_state_changes'] == 0


def test_necessary_new_gt_is_not_blocked_by_continuity(problem):
    p = copy.deepcopy(problem)
    p['loads'][:] = 0.; p['loads'][0, 0] = 500.
    p['state'].gt_prev[:] = [250., 0., 0.]
    p['availability']['GRID'] = 0.; p['availability']['GT_3'] = 0.
    r = api().solve_joint(**p, execution_context=context(p))
    assert r['lost'][0, 0] < 1e-3
    assert r['action']['gt_electric_kW'][1] > 1.


def test_actual_minimum_off_permissions_bind_new_command_but_fault_overrides_minimum_up(problem):
    p = copy.deepcopy(problem)
    p['availability']['GT_1'] = 0.
    c = context(p); c['gt_can_start'][1] = False; c['gt_can_stop'][0] = False
    r = api().solve_joint(**p, execution_context=c)
    assert r['action']['gt_electric_kW'][:2] == [0., 0.]
    assert r['lost'][3, 0] > 0.


def test_full_supply_and_hold_mask_hooks_report_joint_witnesses(problem):
    r = api().solve_joint(**problem, joint_options=BASE, execution_context=context(problem),
        engineering_options={'diagnose_full_supply': True, 'diagnose_hold_combination': True})
    d = r['joint_diagnostic']['engineering']['same_state_diagnostics']
    assert d['full_supply']['status'] == 'feasible'
    assert d['hold_combination_full_supply']['status'] == 'feasible'
    assert d['full_supply']['scope'] == 'generated_command_not_immediate_execution'
    assert max(map(abs, np.ravel(d['full_supply']['unserved_kW']))) <= 1e-7


def test_joint_full_supply_infeasible_retains_real_priority_deficit(problem):
    p = copy.deepcopy(problem); p['loads'][:] = 0.; p['loads'][0, 0] = 100.
    p['availability'].update({f'GT_{i}': 0. for i in (1, 2, 3)})
    p['availability']['GRID'] = 0.; p['state'].gt_prev[:] = 0.
    r = api().solve_joint(**p, engineering_options={'diagnose_full_supply': True})
    assert r['joint_diagnostic']['engineering']['same_state_diagnostics']['full_supply']['status'] == 'infeasible'
    assert r['lost'][0, 0] == pytest.approx(100., abs=1e-7)


def test_new_contact_mode_separates_connected_shortfall_from_delivered_energy(problem):
    p = copy.deepcopy(problem); p['loads'][:] = 0.; p['loads'][:, 0] = [40., 40., 100., 100.]
    p['availability']['GRID'] = 0.; p['availability'].update(GT_1=.5, GT_2=0., GT_3=0.)
    p['state'].gt_prev[:] = [100., 0., 0.]
    r = api().solve_joint(**p, execution_context=context(p), engineering_options={'contact_mode': 'separate'})
    assert r['lost'][2:, 0].sum() > 1.
    assert np.max(r['command_disconnected'][2:]) <= 1e-7
    assert sum(r['action']['gt_electric_kW']) == pytest.approx(r['served'][:, 0].sum(), abs=1e-7)
    assert r['joint_diagnostic']['engineering']['connected_unserved_kW'][2][0] > 0.


def test_diagnostics_distinguish_arrived_ports_from_new_feasible_commands(problem):
    p = copy.deepcopy(problem); p['state'].gt_prev[:] = 0.
    c = context(p, arrived_grid_import_limit_kW=0., arrived_dispatch=dict(gt_electric_kW=[0., 0., 0.],
        diesel_kW=0., boiler_steam_lbph=0., absorption_cooling_kW=0., electric_chiller_cooling_kW=[0., 0., 0.]),
        actual_disconnected_kW=np.zeros((4, 3)).tolist())
    r = api().solve_joint(**p, execution_context=c, engineering_options={'diagnose_full_supply': True})
    d = r['joint_diagnostic']['engineering']['same_state_diagnostics']
    assert d['full_supply']['status'] == 'feasible'
    assert d['execution_held_full_supply']['status'] == 'infeasible'
    assert d['execution_held_full_supply']['scope'] == 'arrived_ports_and_contacts_only'


def test_rejects_hidden_future_options_and_false_actual_ledgers(problem):
    for extra in ({'future_restore_minute': 12.}, {'scenario_id': 'normal'}):
        with pytest.raises(ValueError): api().solve_joint(**problem, execution_context=extra)
    c = context(problem); c['actual_gt_kW'][0] += 1.
    with pytest.raises(ValueError): api().solve_joint(**problem, execution_context=c)
    for bad in (-1., .051, float('nan')):
        with pytest.raises(ValueError): api().solve_joint(**problem,
            engineering_options={'cost_equivalence_USD_per_step': bad})


def test_minimum_up_no_export_infeasibility_is_explicit_not_fictitious_supply(problem):
    p = copy.deepcopy(problem); p['loads'][:] = 0.; p['loads'][0, 0] = 10.
    p['state'].gt_prev[:] = [125., 0., 0.]
    c = context(p); c['gt_can_stop'][0] = False
    r = api().solve_joint(**p, execution_context=c)
    release = r['joint_diagnostic']['engineering']['minimum_time_safety_release']
    assert [v['device'] for v in release] == ['GT_1']
    assert release[0]['original_solver_status'] == 2
    assert release[0]['execution_must_apply_registered_stop_or_protection']
    assert r['solver_backend_layers'][0]['status'] == 2
    assert r['solver_backend_layers'][1]['status'] == 0
    assert max(map(abs, r['energy_balance_residuals'])) <= 1e-7


def test_endurance_changes_service_order_only_for_observed_disturbed_fuel_state(problem):
    p = copy.deepcopy(problem); p['loads'][:] = 0.; p['loads'][:, 0] = [40., 40., 100., 100.]
    p['state'].gt_prev[:] = 0.; p['state'].diesel_energy_remaining_kWh = 50.
    p['state'].last_diesel_kW = 200.; p['diesel_start_available'] = True
    p['availability']['GRID'] = 0.; p['availability'].update(GT_1=0., GT_2=0., GT_3=0.)
    service = api().solve_joint(**p)
    endurance = api().solve_joint(**p, engineering_options={'mode': 'endurance_restore'})
    assert endurance['joint_diagnostic']['engineering']['endurance_preparation_active']
    assert endurance['lost'][:2].sum() < 1e-3
    assert endurance['action']['diesel_kW'] < service['action']['diesel_kW']-50.
    assert endurance['lost'][2:].sum() > service['lost'][2:].sum()+50.
    p['availability']['GRID'] = 1.
    normal = api().solve_joint(**p, engineering_options={'mode': 'endurance_restore'})
    assert not normal['joint_diagnostic']['engineering']['endurance_preparation_active']
    assert normal['lost'].sum() < .02


def test_separate_carrier_feasibility_does_not_claim_joint_full_supply(problem):
    p = copy.deepcopy(problem); p['loads'][:] = 0.; p['state'].gt_prev[:] = 0.
    p['availability'].update(GT_1=0., GT_2=0., GT_3=0., ABS=0.)
    cooling = 100.; grid = cooling/RT_TO_KW*p['plant'].ec_electric_kW_per_rt
    p['grid_import_limit_kW'] = grid
    outcomes = []
    for electric, cold in ((grid, 0.), (0., cooling), (grid, cooling)):
        p['loads'][0] = [electric, 0., cold]
        r = api().solve_joint(**p, engineering_options={'diagnose_full_supply': True})
        outcomes.append(r['joint_diagnostic']['engineering']['same_state_diagnostics']['full_supply']['status'])
    assert outcomes == ['feasible', 'feasible', 'infeasible']


def test_priority_lock_numerical_remainders_do_not_trigger_disturbed_endurance(problem):
    p = copy.deepcopy(problem); p['availability']['GRID'] = 0.
    tiny = np.zeros((2, 3)); tiny[:, 0] = 1e-4
    c = context(p, observed_critical_unserved_kW=tiny.tolist())
    r = api().solve_joint(**p, execution_context=c, engineering_options={'mode': 'endurance_restore'})
    assert not r['joint_diagnostic']['engineering']['endurance_preparation_active']


def test_joint_zero_low_service_optima_do_not_become_repeated_physical_contact_cuts(problem):
    """Pilot regression: two allowed ENS lock tails accumulate at execution.

    A 1e-5 normalized P3 plan loss becomes a real ordinary cut. The unchanged
    fixed-execution solve can then leave its own numerical ENS remainder in
    the connected load. Here jointly feasible zero P2/P3 optima must survive
    every new preparatory, economic and continuity choice as zero plan loss.
    """
    r = api().solve_joint(**problem, joint_options=BASE, execution_context=context(problem))
    layers = {layer['name']: layer for layer in r['joint_diagnostic']['layers']}
    assert layers['current_P2']['objective_value'] == pytest.approx(0., abs=1e-12)
    assert layers['current_P3']['objective_value'] == pytest.approx(0., abs=1e-12)
    assert np.max(r['lost'][2:]) <= 1e-7
    assert np.max(r['command_disconnected'][2:]) <= 1e-7
    for name in ('current_P0', 'current_P1'):
        assert layers[name]['lock_tolerance'] == 1e-7


def test_generated_cut_does_not_stack_with_unchanged_fixed_execution_ens_remainder(problem):
    from hospital_ies.resilience import _solve_milp
    planned = api().solve_joint(**problem, joint_options=BASE, execution_context=context(problem))
    connected = problem['loads']-planned['command_disconnected']
    state = copy.deepcopy(problem['state'])
    state.gt_prev[:] = planned['action']['gt_electric_kW']
    executed = _solve_milp(problem['row'], problem['plant'], connected, problem['availability'], state,
        problem['dt_h'], problem['grid_import_limit_kW'], False, problem['gt_ramp_kW_per_h'],
        problem['solver_time_limit_sec'], problem['config'], execution_contract=problem['execution_contract'],
        execution_action=planned['action'])
    total_loss = executed['lost']+planned['command_disconnected']
    # The original execution model still has a small positive ENS remainder.
    assert executed['lost'][3, 0] > 0.
    original_bound = float(connected[:, 0].sum())*1e-5
    assert total_loss[3, 0] <= original_bound+1e-7
    assert max(map(abs, executed['energy_balance_residuals'])) <= 1e-7


def import_headroom_problem(problem):
    """Small saved legal pre-event state, with no event or identity input."""
    p = copy.deepcopy(problem)
    p['row'] = SimpleNamespace(timestamp=pd.Timestamp('2001-05-10 06:00'), outdoor_temperature_C=13.166666666666666)
    p['loads'] = np.array([[91.94579352435011, 23.337247650090607, 39.08856955048581],
        [69.28921359980536, 105.81032204946612, 25.3523298655942],
        [38.26199394146866, 0., 31.50842468494526], [282.2604233163554, 0., 37.434407429320345]])
    p['state'].gt_prev[:] = [129.7064961, 0., 129.706496]
    p['state'].last_grid_import_kW = 242.07868901168487
    p['grid_import_limit_kW'] = 850.
    low = np.array([[43.69583493367987, 23.337247650090607, 27.419454964783974],
        [48.03530370980536, 105.81032204946612, 19.821515824264125],
        [18.830044790643658, 0., 20.09691222817966], [192.73815398476083, 0., 37.434407429320345]])
    current = context(p, arrived_grid_import_limit_kW=242.08365111315598)
    return p, low, current


def assert_same_physical_plan(before, after):
    for key, value in before['planned_current_action'].items():
        np.testing.assert_array_equal(after['planned_current_action'][key], value)
    np.testing.assert_array_equal(after['lost'], before['lost'])
    np.testing.assert_array_equal(after['command_disconnected'], before['command_disconnected'])
    assert after['optimization_objective_USD_step'] == before['optimization_objective_USD_step']


def test_registered_import_headroom_preference_keeps_lawful_permission_above_used_energy(problem):
    """A 1e-7 kW preparation tail is inside the original 1e-4 local-down deadband.

    It must not change current GT/cost/contact/converter variables to win a
    ceiling tie. This remains an ordinary delayed permission request.
    """
    p, low, current = import_headroom_problem(problem)
    old_e = api().solve_joint(**p, joint_options=BASE, risk_loads=low, execution_context=current,
        engineering_options={'mode': 'endurance_restore'})
    assert old_e['action']['grid_import_kW'] < 300.
    candidate = api().solve_joint(**p, joint_options=BASE, risk_loads=low, execution_context=current,
        engineering_options={'mode': 'endurance_restore', 'grid_headroom_preference': True})
    assert candidate['action']['grid_import_kW'] == pytest.approx(850., abs=1e-7)
    assert candidate['planned_current_action']['grid_import_kW'] < 300.
    assert np.max(candidate['lost'][2:]) <= 1e-7
    assert candidate['cost_USD_step'] <= old_e['cost_USD_step']+1e-5+1e-7
    assert p['state'].last_grid_import_kW == 242.07868901168487
    d = candidate['joint_diagnostic']['engineering']['grid_headroom']
    assert d['all_online_GT_at_floor_guard']
    assert d['operational_deadband_kW'] == 1e-4
    assert d['operational_deadband_source'] == 'hospital_ies.state_transition.TransitionCore._request_local_down'
    assert max(d['deviation_kW']) > 1e-9
    assert max(d['deviation_kW']) < 1e-4
    assert d['applied']
    assert d['actual_and_planned_combination_match']
    assert d['all_actual_online_GT_at_floor_guard']
    assert not d['any_actual_start_pending']
    assert d['all_non_ceiling_variables_unchanged']
    assert d['all_original_constraints_and_locks_checked']
    assert_same_physical_plan(old_e, candidate)


def test_import_headroom_preserves_above_floor_heat_sources_and_old_ceiling(problem):
    p = copy.deepcopy(problem)
    p['grid_import_limit_kW'] = 850.
    p['availability'].update(BOILER_1=0., BOILER_2=0., BOILER_3=0., EC_1=0., EC_2=0., EC_3=0.)
    floor = .4*float(gt_level_properties(p['plant'], p['row'].outdoor_temperature_C)[0][-1])
    p['state'].gt_prev[:] = [floor, 0., floor]
    kwargs = dict(joint_options=BASE, execution_context=context(p))
    baseline = api().solve_joint(**p, **kwargs, engineering_options={'mode': 'endurance_restore'})
    assert baseline['action']['grid_import_kW'] < 849.
    candidate = api().solve_joint(**p, **kwargs,
        engineering_options={'mode': 'endurance_restore', 'grid_headroom_preference': True})
    d = candidate['joint_diagnostic']['engineering']['grid_headroom']
    assert max(d['deviation_kW']) > 1e-4
    assert not d['all_online_GT_at_floor_guard']
    assert not d['applied']
    assert d['reason'] == 'planned_GT_above_original_local_down_deadband'
    assert candidate['action'] == baseline['action']
    assert_same_physical_plan(baseline, candidate)


@pytest.mark.parametrize('boundary', ['GRID_unavailable', 'fixed_import_contract'])
def test_import_headroom_respects_grid_fault_and_original_fixed_contract(problem, boundary):
    p, low, current = import_headroom_problem(problem)
    if boundary == 'GRID_unavailable':
        p['availability']['GRID'] = 0.
    else:
        p['execution_contract']['grid_import_mode'] = 'fixed'
    baseline = api().solve_joint(**p, joint_options=BASE, risk_loads=low, execution_context=current,
        engineering_options={'mode': 'endurance_restore'})
    candidate = api().solve_joint(**p, joint_options=BASE, risk_loads=low, execution_context=current,
        engineering_options={'mode': 'endurance_restore', 'grid_headroom_preference': True})
    if boundary == 'GRID_unavailable':
        assert candidate['action']['grid_import_kW'] == 0.
    else:
        assert not candidate['joint_diagnostic']['separate_grid_ceiling']
        assert candidate['action']['grid_import_kW'] == candidate['planned_current_action']['grid_import_kW']
    assert not candidate['joint_diagnostic']['engineering']['grid_headroom']['applied']
    assert_same_physical_plan(baseline, candidate)


def test_import_headroom_remains_inside_original_tracking_cost_locks(problem):
    p, low, current = import_headroom_problem(problem)
    kwargs = dict(joint_options=BASE, risk_loads=low, execution_context=current,
        tracking_targets={'grid_import': 250.})
    baseline = api().solve_joint(**p, **kwargs, engineering_options={'mode': 'endurance_restore'})
    candidate = api().solve_joint(**p, **kwargs,
        engineering_options={'mode': 'endurance_restore', 'grid_headroom_preference': True})
    assert candidate['joint_diagnostic']['engineering']['grid_headroom']['all_online_GT_at_floor_guard']
    assert candidate['action']['grid_import_kW'] == pytest.approx(250., abs=1e-7)
    assert candidate['tracking_penalty_USD_step'] == baseline['tracking_penalty_USD_step']
    assert_same_physical_plan(baseline, candidate)


@pytest.mark.parametrize('boundary', ['actual_above_floor', 'actual_start_pending', 'new_planned_GT'])
def test_import_headroom_requires_actual_floor_ports_without_pending_starts(problem, boundary):
    p, low, current = import_headroom_problem(problem)
    if boundary == 'actual_above_floor':
        p['state'].gt_prev[0] += .01
        current = context(p)
    elif boundary == 'actual_start_pending':
        current['gt_start_pending'][1] = True
    else:
        p = copy.deepcopy(problem)
        p['state'].gt_prev[:] = 0.
        low = None
        current = context(p)
    kwargs = dict(joint_options=BASE, risk_loads=low, execution_context=current)
    baseline = api().solve_joint(**p, **kwargs, engineering_options={'mode': 'endurance_restore'})
    candidate = api().solve_joint(**p, **kwargs,
        engineering_options={'mode': 'endurance_restore', 'grid_headroom_preference': True})
    d = candidate['joint_diagnostic']['engineering']['grid_headroom']
    if boundary == 'actual_above_floor':
        assert not d['all_actual_online_GT_at_floor_guard']
    elif boundary == 'actual_start_pending':
        assert d['any_actual_start_pending']
    else:
        assert not d['actual_and_planned_combination_match']
    assert not d['applied']
    assert candidate['action'] == baseline['action']
    assert_same_physical_plan(baseline, candidate)


# Three small current-state hubs transcribed from LONG_R3_STARTUP_OBJECTIVE_EXTRACT.
# Only demand/temperature/actual-port numbers enter a solve, never day/event IDs.
NORMAL_START_STATES = [
    (11.58333333333333, 131.72326410000002, 850.,
     [[60.37772154605919, 39.41842402456698, 31.582731704042487],
      [55.11994033980536, 116.0419281037232, 21.771899092225524],
      [26.554496116383046, 0., 24.7103946668163], [229.61328714740534, 0., 39.77108822890163]]),
    (15.583333333333316, 128., 350.,
     [[91.94579352435011, 21.406268682706408, 40.33519974812154],
      [69.28921359980536, 93.74508139846904, 27.01110604398648],
      [38.26199394146866, 0., 31.164870502092764], [282.5606009356021, 0., 84.86270934486127]]),
    (10.333333333333321, 134.9035521, 350.,
     [[91.94579352435011, 34.94859909002413, 37.98883043797247],
      [69.28921359980536, 109.73575380570568, 24.92473812027339],
      [38.26199394146866, 0., 31.43052681951315], [380.52701626949624, 0., 11.896703262037235]])]


def normal_start_problem(problem, value):
    temp, actual, grid, loads = value
    p = copy.deepcopy(problem)
    p['row'] = SimpleNamespace(timestamp=pd.Timestamp('2001-01-15 10:00'), outdoor_temperature_C=temp)
    p['state'].gt_prev[:] = [0., actual, 0.]
    p['grid_import_limit_kW'] = grid
    p['loads'] = np.array(loads)
    p['execution_contract']['gt_start_delay_minutes'] = 10.
    return p


def current_arrival_without_new_GT(p, plan):
    """Static same-demand port check, not a startup/queue or temporal replay."""
    from hospital_ies.resilience import _solve_milp
    arrived = copy.deepcopy(plan['action'])
    for i in range(3):
        if p['state'].gt_prev[i] <= 1e-9:arrived['gt_electric_kW'][i] = 0.
    state = copy.deepcopy(p['state'])
    state.gt_prev[:] = arrived['gt_electric_kW']
    return _solve_milp(p['row'], p['plant'], p['loads']-plan['command_disconnected'],
        p['availability'], state, p['dt_h'], p['grid_import_limit_kW'],
        p['diesel_start_available'], p['gt_ramp_kW_per_h'], p['solver_time_limit_sec'],
        p['config'], execution_contract=p['execution_contract'], execution_action=arrived)


@pytest.mark.parametrize('value', NORMAL_START_STATES)
def test_execution_start_preference_removes_preparation_startup_service_gap(problem, value):
    p = normal_start_problem(problem, value)
    options = dict(mode='endurance_restore', grid_headroom_preference=True,
        diagnose_hold_combination=True)
    baseline = api().solve_joint(**p, joint_options=BASE, execution_context=context(p), engineering_options=options)
    assert baseline['action']['gt_electric_kW'][0] > 1.
    assert baseline['joint_diagnostic']['engineering']['same_state_diagnostics']['hold_combination_full_supply']['status'] == 'feasible'
    # New GT/HRSG is unavailable before ready; the old lowered GRID permission
    # and same new boiler/converter caps cannot cover current P3 electricity.
    assert current_arrival_without_new_GT(p, baseline)['lost'][3, 0] > 1.
    candidate = api().solve_joint(**p, joint_options=BASE, execution_context=context(p),
        engineering_options={**options, 'execution_aware_start_preference': True})
    assert (np.array(candidate['action']['gt_electric_kW']) > 1e-4).tolist() == [False, True, False]
    assert np.max(candidate['lost'][2:]) <= 1e-7
    assert candidate['lost'][:2].max() < .001
    layers = [x['name'] for x in candidate['joint_diagnostic']['layers']]
    assert layers.index('current_P3') < layers.index('normal_current_new_GT_starts') < layers.index('preparation_P0')
    d = candidate['joint_diagnostic']['engineering']['normal_startup_preference']
    assert d['active'] and d['minimum_new_GT_starts'] == 0.
    assert d['current_service_locks_precede_start_preference']
    endpoint = current_arrival_without_new_GT(p, candidate)
    assert endpoint['lost'][3, 0] < .01
    assert np.max(endpoint['lost'][:, 2]) < .01
    assert max(map(abs, endpoint['energy_balance_residuals'])) <= 1e-7


def test_execution_start_preference_allows_current_service_required_new_GT(problem):
    p = copy.deepcopy(problem); p['state'].gt_prev[:] = [125., 0., 0.]
    r = api().solve_joint(**p, joint_options=BASE, execution_context=context(p),
        engineering_options={'mode': 'endurance_restore', 'execution_aware_start_preference': True})
    assert sum(x > 1e-4 for x in r['action']['gt_electric_kW'][1:]) >= 1
    assert r['joint_diagnostic']['engineering']['normal_startup_preference']['minimum_new_GT_starts'] >= 1.
    assert np.max(r['lost'][2:]) <= 1e-7
    assert r['lost'][:2].max() < .001


def test_execution_start_preference_allows_ordinary_GT_stop(problem):
    p = copy.deepcopy(problem); p['loads'][:] = 0.; p['loads'][:, 0] = [60., 60., 80., 200.]
    floor = .4*float(gt_level_properties(p['plant'], p['row'].outdoor_temperature_C)[0][-1])
    p['state'].gt_prev[:] = floor
    r = api().solve_joint(**p, joint_options=BASE, execution_context=context(p),
        engineering_options={'mode': 'endurance_restore', 'execution_aware_start_preference': True})
    assert sum(x > 1e-4 for x in r['action']['gt_electric_kW']) <= 2
    assert r['joint_diagnostic']['engineering']['normal_startup_preference']['minimum_new_GT_starts'] == 0.
    assert np.max(r['lost'][2:]) <= 1e-7


@pytest.mark.parametrize('signal', ['GRID_degraded', 'critical_loss', 'actual_fuel_pressure', 'pending_start', 'component_fault'])
def test_execution_start_preference_preserves_observed_emergency_or_pending_branches(problem, signal):
    p = normal_start_problem(problem, NORMAL_START_STATES[1]); changes = {}
    if signal == 'GRID_degraded':p['availability']['GRID'] = .5
    elif signal == 'critical_loss':changes['observed_critical_unserved_kW'] = [[1., 0., 0.], [0., 0., 0.]]
    elif signal == 'actual_fuel_pressure':
        p['state'].last_diesel_kW = 200.; p['state'].diesel_energy_remaining_kWh = 50.
    elif signal == 'pending_start':changes['gt_start_pending'] = [True, False, False]
    else:p['availability']['EC_1'] = 0.
    current = context(p); current.update(changes)
    r = api().solve_joint(**p, joint_options=BASE, execution_context=current,
        engineering_options={'mode': 'endurance_restore', 'execution_aware_start_preference': True})
    assert not r['joint_diagnostic']['engineering']['normal_startup_preference']['active']
    assert 'normal_current_new_GT_starts' not in [x['name'] for x in r['joint_diagnostic']['layers']]


NORMAL_STOP_STATES = [
    (9.250000000000014, [0., 132.88678410000003, 132.886784],
     [[43.69583493367987, 51.483499628801006, 31.34430780594291],
      [48.03530370980536, 114.58531056546846, 22.00417811224908],
      [18.830044790643658, 0., 22.796329663915856], [225.27777363782187, 0., 18.527373735072075]]),
    (16.999999999999986, [0., 126.13836799999999, 126.13836799999999],
     [[43.69583493367987, 32.09627614250975, 33.9889672979651],
      [48.03530370980536, 100.1739709970823, 24.87022053396343],
      [18.830044790643658, 0., 22.197232694654332], [208.9172601414338, 0., 162.51541500948207]])]


@pytest.mark.parametrize('value', NORMAL_STOP_STATES)
def test_execution_commitment_preference_preserves_feasible_ports_before_economic_stop(problem, value):
    """Saved current states at the R4 stop nodes, without any later load input."""
    temp, actual, loads = value
    p = copy.deepcopy(problem); p['row'].outdoor_temperature_C = temp
    p['state'].gt_prev[:] = actual; p['loads'] = np.array(loads)
    p['execution_contract']['gt_start_delay_minutes'] = 10.
    options = dict(mode='endurance_restore',grid_headroom_preference=True,diagnose_hold_combination=True)
    r4 = api().solve_joint(**p,joint_options=BASE,execution_context=context(p),
        engineering_options={**options,'execution_aware_start_preference':True})
    assert sum(x>1e-4 for x in r4['action']['gt_electric_kW']) == 1
    assert r4['joint_diagnostic']['engineering']['same_state_diagnostics']['hold_combination_full_supply']['status']=='feasible'
    r5 = api().solve_joint(**p,joint_options=BASE,execution_context=context(p),
        engineering_options={**options,'execution_aware_commitment_preference':True})
    assert (np.array(r5['action']['gt_electric_kW'])>1e-4).tolist() == [False,True,True]
    assert np.max(r5['lost'][2:]) <= 1e-7
    d=r5['joint_diagnostic']['engineering']['normal_commitment_preference']
    assert d['active'] and d['minimum_GT_state_changes']==0.
    assert not r5['joint_diagnostic']['engineering']['normal_startup_preference']['requested']
    layers=[x['name'] for x in r5['joint_diagnostic']['layers']]
    assert layers.index('current_P3') < layers.index('normal_current_GT_state_changes') < layers.index('preparation_P0')


def test_execution_commitment_preference_allows_physically_necessary_no_sink_stop(problem):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][:,0]=[40.,30.,20.,10.]
    floor=.4*float(gt_level_properties(p['plant'],p['row'].outdoor_temperature_C)[0][-1])
    p['state'].gt_prev[:]=floor
    r=api().solve_joint(**p,joint_options=BASE,execution_context=context(p),
        engineering_options={'mode':'endurance_restore','execution_aware_commitment_preference':True})
    # Even one original stable-floor GT exceeds total demand; no export or
    # cooling/steam demand can absorb its output. All three must legally stop.
    assert r['action']['gt_electric_kW']==[0.,0.,0.]
    assert r['joint_diagnostic']['engineering']['normal_commitment_preference']['minimum_GT_state_changes']==3.
    assert np.max(r['lost'][2:]) <= 1e-7


def test_execution_commitment_preference_allows_service_required_new_GT(problem):
    p=copy.deepcopy(problem);p['state'].gt_prev[:]=[125.,0.,0.]
    r=api().solve_joint(**p,joint_options=BASE,execution_context=context(p),
        engineering_options={'mode':'endurance_restore','execution_aware_commitment_preference':True})
    assert sum(x>1e-4 for x in r['action']['gt_electric_kW'][1:]) >= 1
    assert r['joint_diagnostic']['engineering']['normal_commitment_preference']['minimum_GT_state_changes'] >= 1.
    assert np.max(r['lost'][2:]) <= 1e-7


def test_execution_commitment_preference_can_retain_three_jointly_feasible_GT(problem):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][:,0]=[60.,60.,80.,200.]
    floor=.4*float(gt_level_properties(p['plant'],p['row'].outdoor_temperature_C)[0][-1])
    p['state'].gt_prev[:]=floor
    r=api().solve_joint(**p,joint_options=BASE,execution_context=context(p),
        engineering_options={'mode':'endurance_restore','execution_aware_commitment_preference':True})
    assert sum(x>1e-4 for x in r['action']['gt_electric_kW']) == 3
    assert r['joint_diagnostic']['engineering']['normal_commitment_preference']['minimum_GT_state_changes']==0.
    assert np.max(r['lost'][2:]) <= 1e-7


@pytest.mark.parametrize('signal',['GRID_degraded','critical_loss','actual_fuel_pressure','pending_start','component_fault'])
def test_execution_commitment_preference_preserves_observed_emergency_or_pending_branches(problem,signal):
    p=normal_start_problem(problem,NORMAL_START_STATES[1]);current=context(p)
    if signal=='GRID_degraded':p['availability']['GRID']=.5
    elif signal=='critical_loss':current['observed_critical_unserved_kW']=[[1.,0.,0.],[0.,0.,0.]]
    elif signal=='actual_fuel_pressure':
        p['state'].last_diesel_kW=200.;p['state'].diesel_energy_remaining_kWh=50.
    elif signal=='pending_start':current['gt_start_pending']=[True,False,False]
    else:p['availability']['EC_1']=0.
    r=api().solve_joint(**p,joint_options=BASE,execution_context=current,
        engineering_options={'mode':'endurance_restore','execution_aware_commitment_preference':True})
    assert not r['joint_diagnostic']['engineering']['normal_commitment_preference']['active']
    assert 'normal_current_GT_state_changes' not in [x['name'] for x in r['joint_diagnostic']['layers']]


P0_PREPARATION_STATES = [
    (10.333333333333321, [[91.94579352435011,41.852186017821275,35.011588379796734],
      [69.28921359980536,108.70387700080951,24.66558887429764],
      [38.26199394146866,0.,28.459822716516975],[279.51476301253297,0.,17.75360928032238]],None,False),
    (13.166666666666664, [[131.37931308318713,17.55582722797743,46.28276592489915],
      [99.21589261164966,92.65290524689622,32.06651222492587],
      [50.46299148406898,0.,37.205405353128825],[374.49223007690847,0.,25.72057837164658]],
     [[91.94579352435011,17.55582722797743,35.011588379796734],
      [69.28921359980536,92.65290524689622,24.66558887429764],
      [38.26199394146866,0.,28.459822716516975],[279.51476301253297,0.,17.75360928032238]],True)]


@pytest.mark.parametrize('value',P0_PREPARATION_STATES)
def test_p0_preparation_precedes_commitment_without_promoting_P1_only_start(problem,value):
    temp,loads,history,needs_p0_preparation=value
    p=copy.deepcopy(problem);p['row'].outdoor_temperature_C=temp
    p['loads']=np.array(loads);p['state'].gt_prev[:]=[0.,132.343808,0.]
    p['grid_import_limit_kW']=850.;p['execution_contract']['gt_start_delay_minutes']=10.
    options=dict(mode='endurance_restore',grid_headroom_preference=True,
        execution_aware_commitment_preference=True)
    kwargs=dict(joint_options=BASE,execution_context=context(p),risk_loads=p['loads'] if history is None else np.array(history))
    old=api().solve_joint(**p,**kwargs,engineering_options=options)
    old_layers={v['name']:v for v in old['joint_diagnostic']['layers']}
    assert sum(v>1e-4 for v in old['action']['gt_electric_kW'])==1
    if needs_p0_preparation:assert old_layers['preparation_P0']['objective_value']>.01
    else:assert old_layers['preparation_P1']['objective_value']>.1
    new=api().solve_joint(**p,**kwargs,
        engineering_options={**options,'p0_preparation_before_commitment':True})
    layers={v['name']:v for v in new['joint_diagnostic']['layers']}
    assert layers['preparation_P0']['objective_value'] <= 1e-10
    assert np.max(new['lost'][2:]) <= 1e-7
    assert new['lost'][:2].max() < .001
    count=sum(v>1e-4 for v in new['action']['gt_electric_kW'])
    assert count >= 2 if needs_p0_preparation else count == 1
    order=list(layers)
    assert order.index('current_P3') < order.index('preparation_P0') < order.index('normal_current_GT_state_changes') < order.index('preparation_P1')
    assert new['joint_diagnostic']['engineering']['normal_commitment_preference']['p0_preparation_before_commitment_active']


@pytest.mark.parametrize('value',NORMAL_STOP_STATES)
def test_p0_preparation_keeps_two_current_ports_when_critical_P0_optimum_already_zero(problem,value):
    temp,actual,loads=value
    p=copy.deepcopy(problem);p['row'].outdoor_temperature_C=temp
    p['state'].gt_prev[:]=actual;p['loads']=np.array(loads)
    new=api().solve_joint(**p,joint_options=BASE,execution_context=context(p),
        engineering_options=dict(mode='endurance_restore',execution_aware_commitment_preference=True,
            p0_preparation_before_commitment=True))
    assert (np.array(new['action']['gt_electric_kW'])>1e-4).tolist()==[False,True,True]
    assert np.max(new['lost'][2:]) <= 1e-7


NORMAL_POSITIVE_P1_STATES = [
    (17.58333333333331,[0.,125.36268799999993,281.6409574832242],
     [[131.37931308318713,117.20175942235123,61.80581759380867],
      [99.21589261164966,52.78788108756836,42.403580737171296],
      [50.46299148406898,0.,44.25978526260415],[442.28552610221897,0.,413.0111562484533]]),
    (21.583333333333314,[0.,124.50943999999998,277.4279220851951],
     [[131.37931308318713,107.3050364737965,60.26551393139759],
      [99.21589261164966,50.391898610702825,40.70395284349269],
      [50.46299148406898,0.,41.662765120220335],[409.9551218367364,0.,684.9559125859703]])]


@pytest.mark.parametrize('value',NORMAL_POSITIVE_P1_STATES)
def test_p0_preparation_does_not_promote_third_GT_to_reduce_normal_static_P1_loss(problem,value):
    temp,actual,loads=value
    p=copy.deepcopy(problem);p['row'].outdoor_temperature_C=temp
    p['state'].gt_prev[:]=actual;p['loads']=np.array(loads)
    new=api().solve_joint(**p,joint_options=BASE,execution_context=context(p),
        engineering_options=dict(mode='endurance_restore',execution_aware_commitment_preference=True,
            p0_preparation_before_commitment=True))
    layers={v['name']:v for v in new['joint_diagnostic']['layers']}
    assert layers['preparation_P0']['objective_value'] <= 1e-10
    assert layers['preparation_P1']['objective_value'] > .01
    assert (np.array(new['action']['gt_electric_kW'])>1e-4).tolist()==[False,True,True]
    assert np.max(new['lost'][2:]) <= 1e-7


def test_p0_preparation_leaves_disturbed_endurance_order_unchanged(problem):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][:,0]=[40.,40.,100.,100.]
    p['state'].gt_prev[:]=0.;p['state'].last_diesel_kW=200.;p['state'].diesel_energy_remaining_kWh=50.
    p['availability'].update(GRID=0.,GT_1=0.,GT_2=0.,GT_3=0.);p['diesel_start_available']=True
    options=dict(mode='endurance_restore',execution_aware_commitment_preference=True)
    old=api().solve_joint(**p,execution_context=context(p),engineering_options=options)
    new=api().solve_joint(**p,execution_context=context(p),
        engineering_options={**options,'p0_preparation_before_commitment':True})
    assert_same_physical_plan(old,new)
    assert not new['joint_diagnostic']['engineering']['normal_commitment_preference']['p0_preparation_before_commitment_active']
