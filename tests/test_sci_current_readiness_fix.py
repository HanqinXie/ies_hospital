"""M obeys known current execution limits without gaining a reserve horizon."""
import copy

import numpy as np
import pytest

from test_engineering_planner import problem
from test_state_coordinated_planner import context
from tools.sci_engineering import planner, policy
from hospital_ies.model import gt_level_properties


def solve(p, readiness):
    return planner.solve_joint(**p, readiness_context=readiness,
        coordination_options=policy.policy_config('M'))


def test_current_M_preserves_observed_minimum_up_limit(problem):
    p = copy.deepcopy(problem)
    p['loads'][:] = 0.; p['loads'][0, 0] = 500.
    p['grid_import_limit_kW'] = 850.
    p['state'].gt_prev[:] = [128., 0., 0.]
    readiness = context(p, gt_can_stop=[False, True, True])
    result = solve(p, readiness)
    rated = float(gt_level_properties(p['plant'], p['row'].outdoor_temperature_C)[0][-1])
    assert result['action']['gt_electric_kW'][0] >= .4 * rated - 1e-4


def test_current_M_zero_ready_supply_keeps_legal_new_start_command(problem):
    p = copy.deepcopy(problem)
    p['loads'][:] = 0.; p['loads'][0] = [500., 50., 0.]
    p['state'].gt_prev[:] = 0.
    p['availability']['GRID'] = 0.
    readiness = context(p)
    result = solve(p, readiness)
    # A target may start a healthy OFF unit; it cannot supply the current gap.
    assert sum(result['action']['gt_electric_kW']) > 100.
    assert sum(result['planned_current_action']['gt_electric_kW']) == pytest.approx(0., abs=1e-7)
    assert result['planned_current_action']['hrsg_steam_lbph'] == pytest.approx(0., abs=1e-7)
    assert result['lost'][0, 0] >= 499.9


def test_current_M_validates_readiness_against_observed_physical_state(problem):
    readiness = context(problem)
    readiness['actual_gt_kW'][0] += 1.
    with pytest.raises(ValueError, match='Readiness actual GT'):
        solve(problem, readiness)


def test_current_M_pending_start_remains_a_command_not_current_heat(problem):
    p = copy.deepcopy(problem)
    p['loads'][:] = 0.; p['loads'][0] = [500., 50., 0.]
    p['state'].gt_prev[:] = 0.
    p['availability']['GRID'] = 0.
    readiness = context(p)
    readiness['gt_physical_status'][0] = 'STARTING'
    readiness['pending_starts'][0] = dict(started=0., ready=20., requested=200.)
    result = solve(p, readiness)
    assert result['planned_current_action']['gt_electric_kW'][0] == pytest.approx(0., abs=1e-7)
    assert result['planned_current_action']['hrsg_steam_lbph'] == pytest.approx(0., abs=1e-7)
    assert any(abs(result['action']['gt_electric_kW'][0] - value) <= 1e-7 for value in (0., 200.))


def test_current_M_has_only_observed_current_service_and_cost_objectives(problem):
    result = solve(problem, context(problem))
    d = result['joint_diagnostic']
    assert 'arrived_current_P0' in d['objective_order']
    assert 'arrived_current_P3' in d['objective_order']
    assert d['risk_scenarios'] == {}
    assert not any(any(word in name for word in ('preparation', 'reserve', 'response_',
        'shaft_support', 'startup_bridge', 'standby', 'endpoint_pressure'))
        for name in d['objective_order'])
    assert d['sci']['reserve_enabled'] is False
    assert d['sci']['critical_preparation_first'] is False
    assert max(map(abs, result['energy_balance_residuals'])) <= 1e-4


def test_current_M_auxiliary_evidence_uses_the_same_ready_supply_witness(problem):
    p = copy.deepcopy(problem)
    p['loads'][:] = 0.; p['loads'][0] = [500., 50., 100.]
    p['state'].gt_prev[:] = 0.; p['availability']['GRID'] = 0.
    p['config']['sci_service_dependencies'] = True
    p['row'].P0_fan_kW = 10.; p['row'].P0_chw_pump_kW = 5.
    result = solve(p, context(p))
    auxiliary = np.asarray(result['joint_diagnostic']['sci']['current_auxiliary_served_kW'])
    assert auxiliary.sum() <= result['served'][:, 0].sum() + 1e-7
    assert auxiliary.sum() == pytest.approx(0., abs=1e-7)
