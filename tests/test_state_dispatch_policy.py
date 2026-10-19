import importlib
import copy
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def api():
    try:
        return importlib.import_module('tools.state_dispatch.policy')
    except ModuleNotFoundError:
        pytest.fail('State dispatch policy implementation is missing')


def features(heat=100.0, electric=100.0, cooling=100.0):
    from tools.integrated_learning.policies import DEVICES
    f = {'availability_' + d: 1.0 for d in DEVICES}
    for i in (1, 2, 3):
        f.update({f'pre_GT_{i}_kW': 0.0, f'capacity_GT_{i}': 300.0,
                  f'state_GT_{i}': 'OFF', f'overload_minutes_GT_{i}': 0.0})
    for p in ('P0', 'P1', 'P2', 'P3'):
        for c, value in (('e', electric), ('h', heat), ('c', cooling)):
            f[f'{p}_{c}_demand_kW'] = value
            f[f'{p}_{c}_disconnected_kW'] = 0.0
    f.update(arrived_grid_import_limit_kW=850.0, grid_import_limit_kW=850.0,
             grid_import_prev_kW=400.0, diesel_prev_kW=0.0,
             diesel_energy_kWh=2400.0, diesel_status='OFF', control_available=True)
    return f


def record(t, f):
    return {'decision_time_min': float(t), 'features': f,
            'feature_metadata': {k: {'available_time_min': float(t), 'valid_time_min': float(t)} for k in f}}


def test_demand_jump_enters_emergency_without_failure_label():
    a = api()
    tracker = a.StateModeTracker(a.validate_config({}))
    assert tracker.update(features())['mode'] == 'prepare'
    state = tracker.update(features(heat=160.0))
    assert state['mode'] == 'emergency'
    assert 'observed_demand_rise' in state['reasons']


def test_recovery_hysteresis_exits_and_never_clears_protection():
    a = api()
    tracker = a.StateModeTracker(a.validate_config({'stable_decisions': 2}))
    f = features()
    tracker.update(f)
    f['overload_minutes_GT_2'] = 5.0
    assert tracker.update(f)['mode'] == 'emergency'
    assert f['overload_minutes_GT_2'] == 5.0
    f['overload_minutes_GT_2'] = 0.0
    assert tracker.update(f)['mode'] == 'recovery'
    assert tracker.update(f)['mode'] == 'prepare'


def test_three_unit_request_can_increase_gt_using_planned_grid_displacement():
    a = api()
    plan = np.array([150.0, 150.0, 0.0, 500.0, 0.0, 100.0])
    request, detail = a.spread_targets(plan, [300.0]*3, [1.0]*3, [0.0]*3,
                                       fraction=0.55, minimum_fraction=0.4)
    assert sum(request[:3]) > sum(plan[:3])
    assert request[:3] == pytest.approx([165.0]*3)
    assert sum(request[:4]) == pytest.approx(sum(plan[:4]))
    assert request[3] == pytest.approx(305.0)
    assert detail['request_is_not_execution'] is True
    assert plan.tolist() == [150.0, 150.0, 0.0, 500.0, 0.0, 100.0]


def test_infeasible_three_minimums_do_not_manufacture_sink_or_supply():
    a = api()
    plan = np.array([120.0, 0.0, 0.0, 20.0, 0.0, 0.0])
    request, detail = a.spread_targets(plan, [300.0]*3, [1.0]*3, [0.0]*3,
                                       fraction=0.55, minimum_fraction=0.4)
    assert request == pytest.approx(plan)
    assert detail['reason'] == 'insufficient_electric_absorption_for_fleet'


def test_failed_hrsg_unit_not_counted_as_thermal_ready_and_zero_diesel_preserved():
    a = api()
    plan = np.array([250.0, 200.0, 0.0, 300.0, 0.0, 100.0])
    request, detail = a.spread_targets(plan, [300.0]*3, [1.0, 1.0, 0.0], [0.0]*3,
                                       fraction=0.55, minimum_fraction=0.4)
    assert request[2] == 0.0
    assert request[4:] == pytest.approx(plan[4:])
    assert detail['eligible_gt_indices'] == [1, 2]


def test_stress_bias_changes_allocation_without_changing_total_sources():
    a = api()
    plan = np.array([250.0, 250.0, 0.0, 300.0, 0.0, 50.0])
    request, _ = a.spread_targets(plan, [300.0]*3, [1.0]*3, [10.0, 0.0, 0.0],
                                 fraction=0.65, minimum_fraction=0.4)
    assert request[0] < request[1]
    assert np.all(request[:3] >= 120.0)
    assert sum(request[:4]) == pytest.approx(sum(plan[:4]))


def test_identity_is_exact_baseline_and_forbidden_information_is_rejected():
    a = api()
    from tools.integrated_learning.policies import baseline_request
    p = a.StateDispatchPolicy({'identity': 'secure'}, 'identity_probe')
    req, diag = p.decide([record(0, features())])
    assert req == baseline_request('secure_milp')
    assert diag['baseline_request_submitted_unchanged'] is True
    bad = record(15, features())
    bad['features']['scenario_id'] = 'future_sensitive'
    bad['feature_metadata']['scenario_id'] = {'available_time_min': 15, 'valid_time_min': 15}
    with pytest.raises(ValueError, match='whitelist'):
        p.decide([bad])
    future = record(15, features())
    future['feature_metadata']['diesel_status']['valid_time_min'] = 30
    with pytest.raises(ValueError, match='future'):
        p.decide([future])


def test_bad_config_rejected_and_input_mapping_not_mutated():
    a = api()
    for cfg in ({'prepare_weight': float('nan')}, {'prepare_fraction': -0.5},
                {'stable_decisions': 0}, {'future_trip_time': 165}):
        with pytest.raises(ValueError):
            a.validate_config(cfg)
    cfg = {'prepare_weight': 0.1}
    saved = copy.deepcopy(cfg)
    a.StateDispatchPolicy(cfg, 'probe')
    assert cfg == saved
