"""Additive margin interface checks, with no physical input or trajectory reads."""
import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def api():
    try:
        return importlib.import_module('tools.autonomous_dispatch.margin')
    except ModuleNotFoundError:
        pytest.fail('Versioned margin interface is missing')


def runner():
    try:
        return importlib.import_module('tools.autonomous_dispatch.closed_loop_v3')
    except ModuleNotFoundError:
        pytest.fail('Versioned margin runner is missing')


def test_canonical_margin_action_preserves_risk_and_old_validator():
    old = importlib.import_module('tools.autonomous_dispatch.connections')
    request = {'joint': {'options': {'variant': 'coupled', 'critical_reserve_multiplier': 1.5},
                         'risk_loads_kW': np.ones((4, 3)).tolist()}}
    before = copy.deepcopy(request)
    with pytest.raises(ValueError):
        old.validate_action(request)
    canonical = api().validate_action(request)
    expected = importlib.import_module('tools.autonomous_dispatch.planner_margin')._options(request['joint']['options'])
    assert canonical['joint']['options'] == expected
    assert canonical['joint']['risk_loads_kW'] == request['joint']['risk_loads_kW']
    assert request == before
    assert api().validate_action({}) == {}


@pytest.mark.parametrize('value', [True, 1, '1.5', float('nan'), 2.01])
def test_margin_multiplier_is_strict_finite_float(value):
    with pytest.raises(ValueError):
        api().validate_action({'joint': {'options': {'variant': 'coupled', 'critical_reserve_multiplier': value}}})


@pytest.mark.parametrize('field', ['future_fault_end', 'future_load_multiplier', 'horizon_steps'])
def test_margin_cannot_carry_future_script_fields(field):
    with pytest.raises(ValueError):
        api().validate_action({'joint': {'options': {'variant': 'coupled', field: 2.}}})


def test_policy_canonical_options_reach_same_clock_request():
    a = api()
    policy = a.MarginDispatchPolicy({'variant': 'coupled', 'critical_reserve_multiplier': 1.5}, 'margin_15')
    assert policy.config['critical_reserve_multiplier'] == 1.5
    assert policy.config['connection_mode'] == 'planned_shed'
    features = {f'{g}_{c}_demand_kW': 20. for g in ('P0', 'P1', 'P2', 'P3') for c in ('e', 'h', 'c')}
    records = [{'decision_time_min': 15., 'features': features,
                'feature_metadata': {k: {'available_time_min': 15., 'valid_time_min': 15.} for k in features}}]
    policy.bind_current(SimpleNamespace(time_minute=15., loads=np.full((4, 3), 50.)))
    request, info = policy.decide(records)
    assert request['joint']['options'] == policy.config
    assert request['joint']['risk_loads_kW'] == np.full((4, 3), 20.).tolist()
    assert info['planning_interface'] == 'joint_command.margin.v3'
    assert info['future_rollouts_online'] == 0


@pytest.mark.parametrize('running,startup,queued,eligible', [
    (False, False, False, True), (True, False, False, False),
    (False, True, False, False), (False, False, True, False)])
def test_core_forwards_margin_and_current_retention_eligibility(monkeypatch, running, startup, queued, eligible):
    a = api()
    planner = importlib.import_module('tools.autonomous_dispatch.planner_margin')
    seen = {}
    result = {'optimizer_warning': '', 'solver_backend_layers': []}
    def solve(*args, **kwargs):
        seen.update(kwargs)
        return result
    monkeypatch.setattr(planner, 'solve_joint', solve)
    core = a.MarginTransitionCore.__new__(a.MarginTransitionCore)
    core.time = 15.; core.solver_calls = []
    state = SimpleNamespace(gt_prev=np.array([100. if running else 0., 0., 0.]))
    core.gt_start_pending = {0: {'ready': 30.}} if startup else {}
    core.pending_commands = [{'dispatch': {'gt_electric_kW': [100., 0., 0.]}}] if queued else []
    core.env = SimpleNamespace(plant=object(), dt_h=.25, scenario={}, state=state, execution_contract={},
        config={'grid_import_limit_kW': 800., 'gt_ramp_kW_per_hour': 500., 'solver_time_limit_sec': 6.})
    core.data_at = lambda t: (100, object(), np.ones((4, 3)), {})
    core.diesel_available = lambda t, availability: False
    request = a.validate_action({'joint': {'options': {'variant': 'coupled', 'critical_reserve_multiplier': 1.5},
                                         'risk_loads_kW': np.ones((4, 3)).tolist()}})
    assert core._solve_joint(request, np.ones((4, 3)), {}) is result
    assert seen['joint_options'] == request['joint']['options']
    assert seen['joint_options']['critical_reserve_multiplier'] == 1.5
    assert seen['retention_eligible'] is eligible
    assert seen['risk_loads'] == request['joint']['risk_loads_kW']
    assert core.solver_calls[0]['phase'] == 'joint_margin_command_generation'


def test_margin_queue_keeps_v2_clock_contacts_and_v3_event_tag():
    a = api()
    old = importlib.import_module('tools.autonomous_dispatch.connections')
    assert issubclass(a.MarginTransitionCore, old.ConnectionTransitionCore)
    core = a.MarginTransitionCore.__new__(a.MarginTransitionCore)
    core.failed = False; core.time = 15.; core.sequence = 0
    core.pending_commands = []; core.events = []
    core.data_at = lambda t: (100, None, np.ones((4, 3)), {})
    core.delayed_at = lambda t: (True, 27.5)
    dispatch = {'gt_electric_kW': [0.] * 3, 'grid_import_kW': 0., 'diesel_kW': 200.,
                'boiler_steam_lbph': 300., 'absorption_cooling_kW': 0., 'electric_chiller_cooling_kW': [0.] * 3}
    lost = np.array([[0.] * 3, [0.] * 3, [10.] * 3, [20.] * 3])
    core._solve_joint = lambda request, loads, availability: dict(action=dispatch, lost=lost,
        planned_current_action=dispatch, command_disconnected=np.zeros((4, 3)), joint_diagnostic={})
    command = core.generate_command({'joint': {'options': {'variant': 'coupled', 'critical_reserve_multiplier': 1.5}}})
    assert command['arrival_minute'] == 27.5
    assert command['disconnected'] == np.zeros((4, 3)).tolist()
    assert command['joint_dispatch']['diagnostic']['planned_unserved_kW'] == lost.tolist()
    assert command['request']['joint']['options']['critical_reserve_multiplier'] == 1.5
    assert command['joint_dispatch']['planning_interface'] == 'joint_command.margin.v3'
    assert core.events[-1]['command'] == command == core.pending_commands[0]


@pytest.mark.parametrize('model_id', ['M0', 'M1_ordered_boundary'])
def test_margin_reset_selects_margin_core_with_existing_m1_mix(monkeypatch, model_id):
    a = api()
    original = importlib.import_module('tools.autonomous_dispatch.environment')
    def reset(self, *, scenario, seed=None):
        return globals()['TransitionCore']
    monkeypatch.setattr(original.OriginalEnvironment, 'reset', reset)
    env = a.MarginHospitalIESEnv.__new__(a.MarginHospitalIESEnv)
    env.allowed_hours = frozenset(range(100, 111)); env.sealed_days = frozenset(); env.model_id = model_id
    core_type = env.reset(scenario={'start_hour': 100, 'duration_steps': 40, 'step_minutes': 15.})
    assert issubclass(core_type, a.MarginTransitionCore)
    if model_id == 'M1_ordered_boundary':
        mixin = importlib.import_module('tools.autonomous_dispatch.model_order').OrderedBoundaryMixin
        assert issubclass(core_type, mixin)


def test_episode_bridge_uses_margin_validator_and_payload_tag(monkeypatch):
    a = api()
    episode = importlib.import_module('tools.autonomous_dispatch.episode')
    def export(env, scenario, policy, seed, manifest, **options):
        return {'policy_environment': globals()['PolicyEnvironment']}
    monkeypatch.setattr(episode.original, 'run_episode', export)
    env = SimpleNamespace(model_id='M0', input_access_receipt={'sealed_numeric_rows_parsed': 0})
    payload = a.run_policy_episode(env, {}, object(), 1, {})
    assert payload['policy_environment'] is a.MarginPolicyEnvironment
    assert payload['planning_interface'] == 'joint_command.margin.v3'
    assert payload['execution_model_id'] == 'M0'
    assert payload['input_access_receipt'] == env.input_access_receipt


def test_v3_runner_factory_and_all_runtime_bindings(monkeypatch):
    old = importlib.import_module('tools.autonomous_dispatch.closed_loop_v2')
    before = old._source_binding()
    new = runner(); a = api()
    assert old._source_binding() == before
    assert new.previous.__name__ == 'tools.autonomous_dispatch.closed_loop'
    assert set(old.SOURCE_FILES) <= set(new.SOURCE_FILES)
    for name in ('margin', 'planner_margin', 'closed_loop_v3'):
        assert f'tools/autonomous_dispatch/{name}.py' in new.SOURCE_FILES
    assert new.SCHEMA == 'autonomous_dispatch.margin.matched_full_feedback.v3'
    assert new.ACTION_SCHEMA == 'autonomous_dispatch.legacy_or_joint_action.v3'
    specs = [{'policy_id': 'priority_milp', 'kind': 'priority'}, {'policy_id': 'secure_milp', 'kind': 'secure'},
             {'policy_id': 'margin_15', 'kind': 'joint', 'config': {'variant': 'coupled', 'critical_reserve_multiplier': 1.5}}]
    assert new._check_specs(specs) == specs
    assert isinstance(new._factory(specs[-1]), a.MarginDispatchPolicy)
    namespace = new._runner_namespace()
    assert namespace['run_policy_episode'] is a.run_policy_episode
    assert namespace['_factory'] is new._factory
    assert namespace['SCHEMA'] == new.SCHEMA
    captured = {}
    def env(root, **kwargs):
        captured.update(kwargs)
        return captured
    monkeypatch.setattr(new, 'MarginHospitalIESEnv', env)
    factory = new._environment_factory({'scenario_ids': ['one']}, {'execution_model_id': 'M0',
        'scenarios': [{'id': 'one', 'start_hour': 100, 'duration_steps': 40, 'step_minutes': 15.}]})
    factory(Path('D:/metadata_only'), execution_contract={})
    assert captured['allowed_hours'] == list(range(100, 111))
    assert captured['model_id'] == 'M0'
    assert captured['sealed_days'] == [21, 26, 59, 77, 92, 127, 140, 196, 264, 284, 344, 359]
    assert new._globals()['_environment_factory'] is new._environment_factory
    assert new._globals()['_payload_binding'] is new._payload_binding
