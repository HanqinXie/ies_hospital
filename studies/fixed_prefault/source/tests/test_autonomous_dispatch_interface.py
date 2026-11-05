import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def api():
    return importlib.import_module('tools.autonomous_dispatch.contracts')


def test_joint_request_is_explicit_and_original_request_stays_compatible():
    a = api()
    request = {'joint': {'options': {'variant': 'coupled', 'support_hours': 2.},
                         'risk_loads_kW': np.ones((4, 3)).tolist()}}
    assert a.validate_action(request) == request
    assert a.validate_action({}) == {}
    assert a.validate_action({'security': {'gt_loading_penalty_USD_per_kWh': .35}})


@pytest.mark.parametrize('action', [
    {'joint': {'options': {'future_outage_end': 300.}}},
    {'joint': {'options': {'variant': 'unknown'}}},
    {'joint': {'options': {'support_hours': 0.}}},
    {'joint': {'options': {}, 'risk_loads_kW': [[-1.] * 3] * 4}},
    {'joint': {'options': {}}, 'targets': {'diesel': 10.}},
])
def test_hidden_information_and_ambiguous_joint_targets_are_rejected(action):
    with pytest.raises(ValueError):
        api().validate_action(action)


def test_historical_envelope_uses_legal_current_past_loads_and_no_future():
    a = api()
    policy = a.JointDispatchPolicy({'variant': 'coupled'}, 'joint_test')
    current = SimpleNamespace(time_minute=15., loads=np.full((4, 3), 50.))
    features = {f'{g}_{c}_demand_kW': 20. for g in ('P0', 'P1', 'P2', 'P3') for c in ('e', 'h', 'c')}
    records = [{'decision_time_min': 0., 'features': features,
                'feature_metadata': {k: {'available_time_min': 0., 'valid_time_min': 0.} for k in features}},
               {'decision_time_min': 15., 'features': {k: 50. for k in features},
                'feature_metadata': {k: {'available_time_min': 15., 'valid_time_min': 15.} for k in features}}]
    policy.bind_current(current)
    request, diagnostic = policy.decide(records)
    assert request['joint']['risk_loads_kW'] == np.full((4, 3), 20.).tolist()
    assert diagnostic['future_rollouts_online'] == 0
    assert diagnostic['history_observation_count'] == 2
    with pytest.raises(ValueError):
        policy.decide(records)


def test_joint_core_queues_complete_plan_without_old_reference_total(monkeypatch):
    module = importlib.import_module('tools.autonomous_dispatch.environment')
    core = module.JointTransitionCore.__new__(module.JointTransitionCore)
    core.failed = False; core.time = 15.; core.sequence = 0
    core.pending_commands = []; core.events = []; core.solver_calls = []
    core.env = SimpleNamespace(dt_h=.25, planning_queries=[])
    core.data_at = lambda t: (1, None, np.ones((4, 3)), {})
    core.delayed_at = lambda t: (True, 27.5)
    dispatch = {'gt_electric_kW': [0., 0., 0.], 'grid_import_kW': 850.,
                'diesel_kW': 25., 'boiler_steam_lbph': 500.,
                'absorption_cooling_kW': 0., 'electric_chiller_cooling_kW': [2., 3., 4.]}
    solution = {'action': dispatch, 'lost': np.array([[0.]*3, [0.]*3, [10.]*3, [20.]*3]),
                'optimizer_warning': '', 'joint_diagnostic': {'layers': ['P0', 'P1', 'fuel', 'P2', 'P3']},
                'planned_current_action': {**dispatch, 'grid_import_kW': 0.}}
    core._solve_joint = lambda request, loads, f: solution
    request = {'joint': {'options': {'variant': 'endurance'}}}
    command = core.generate_command(request)
    assert command['request'] == request
    assert command['dispatch'] == dispatch
    assert command['arrival_minute'] == 27.5
    assert command['disconnected'][2] == [10.] * 3
    assert core.pending_commands == [command]
    assert command['joint_dispatch']['planned_current_action']['grid_import_kW'] == 0.
    assert core.events[-1]['command']['request'] == request
