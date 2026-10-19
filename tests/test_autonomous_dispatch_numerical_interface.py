"""Four additive unscaled-route interface checks; no inputs, raw, or solver run."""
import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def module(name):
    try:
        return importlib.import_module('tools.autonomous_dispatch.' + name)
    except ModuleNotFoundError as error:
        if error.name == 'tools.autonomous_dispatch.' + name:
            pytest.fail('Explicit unscaled adapter is missing: ' + name)
        raise


def test_unscaled_joint_keeps_margin_queue_clock_contacts_and_options(monkeypatch):
    route = module('numerical_route')
    numerics = module('numerics')
    margin = module('margin')
    old = module('planner_margin')
    seen = {}
    dispatch = {'gt_electric_kW': [0.] * 3, 'grid_import_kW': 0., 'diesel_kW': 200.,
                'boiler_steam_lbph': 300., 'absorption_cooling_kW': 0., 'electric_chiller_cooling_kW': [0.] * 3}
    lost = np.array([[0.] * 3, [0.] * 3, [10.] * 3, [20.] * 3])
    def solve(*args, **kwargs):
        seen.update(kwargs)
        return dict(action=dispatch, lost=lost, planned_current_action=dispatch,
                    command_disconnected=np.zeros((4, 3)), joint_diagnostic={})
    monkeypatch.setattr(numerics, 'solve_joint', solve)
    def forbidden(*args, **kwargs):
        pytest.fail('Unscaled core escaped to the original scaled planner')
    monkeypatch.setattr(old, 'solve_joint', forbidden)
    core = route.UnscaledTransitionCore.__new__(route.UnscaledTransitionCore)
    core.failed = False; core.time = 15.; core.sequence = 0
    core.pending_commands = []; core.events = []; core.solver_calls = []; core.gt_start_pending = {}
    state = SimpleNamespace(gt_prev=np.zeros(3))
    core.env = SimpleNamespace(plant=object(), dt_h=.25, scenario={}, state=state, execution_contract={},
        config={'grid_import_limit_kW': 800., 'gt_ramp_kW_per_hour': 500., 'solver_time_limit_sec': 6.})
    core.data_at = lambda t: (100, object(), np.full((4, 3), 50.), {})
    core.delayed_at = lambda t: (True, 27.5)
    core.diesel_available = lambda t, availability: False
    request = {'joint': {'options': {'variant': 'coupled', 'critical_reserve_multiplier': 1.5},
                         'risk_loads_kW': np.ones((4, 3)).tolist()}}
    command = core.generate_command(request)
    assert issubclass(route.UnscaledTransitionCore, margin.MarginTransitionCore)
    assert command['arrival_minute'] == 27.5
    assert command == core.pending_commands[0] == core.events[-1]['command']
    assert command['disconnected'] == np.zeros((4, 3)).tolist()
    assert command['joint_dispatch']['diagnostic']['planned_unserved_kW'] == lost.tolist()
    assert command['joint_dispatch']['planning_interface'] == margin.PLANNING_INTERFACE
    assert seen['joint_options'] == command['request']['joint']['options']
    assert seen['risk_loads'] == request['joint']['risk_loads_kW']
    assert seen['retention_eligible'] is True
    assert core.solver_calls[0]['phase'] == 'joint_margin_command_generation'


def test_unscaled_reset_isolated_for_M0_and_existing_M1_mixin(monkeypatch):
    route = module('numerical_route')
    original = module('environment')
    margin = module('margin')
    mixin = module('model_order').OrderedBoundaryMixin
    original_core = original.SelectiveHospitalIESEnv.reset.__globals__['JointTransitionCore']
    def reset(self, *, scenario, seed=None):
        return globals()['TransitionCore']
    monkeypatch.setattr(original.OriginalEnvironment, 'reset', reset)
    for model_id in ['M0', 'M1_ordered_boundary']:
        env = route.UnscaledHospitalIESEnv.__new__(route.UnscaledHospitalIESEnv)
        env.allowed_hours = frozenset(range(100, 111)); env.sealed_days = frozenset(); env.model_id = model_id
        selected = env.reset(scenario={'start_hour': 100, 'duration_steps': 40, 'step_minutes': 15.})
        assert issubclass(selected, route.UnscaledTransitionCore)
        assert issubclass(selected, mixin) == (model_id == 'M1_ordered_boundary')
        old_env = margin.MarginHospitalIESEnv.__new__(margin.MarginHospitalIESEnv)
        old_env.allowed_hours = env.allowed_hours; old_env.sealed_days = env.sealed_days; old_env.model_id = model_id
        old_selected = old_env.reset(scenario={'start_hour': 100, 'duration_steps': 40, 'step_minutes': 15.})
        assert issubclass(old_selected, margin.MarginTransitionCore)
        assert not issubclass(old_selected, route.UnscaledTransitionCore)
    assert original.SelectiveHospitalIESEnv.reset.__globals__['JointTransitionCore'] is original_core


def test_unscaled_episode_reuses_margin_exporter_and_marks_numerical_contract(monkeypatch):
    route = module('numerical_route')
    margin = module('margin')
    episode = module('episode')
    calls = []
    def export(env, scenario, policy, seed, manifest, **options):
        calls.append(manifest)
        return {'policy_environment': globals()['PolicyEnvironment']}
    monkeypatch.setattr(episode.original, 'run_episode', export)
    env = SimpleNamespace(model_id='M0', input_access_receipt={'sealed_numeric_rows_parsed': 0})
    manifest = {'numerical_contract_id': 'unscaled_original_rows.v1'}
    payload = route.run_policy_episode(env, {}, object(), 1, manifest)
    assert payload['policy_environment'] is margin.MarginPolicyEnvironment
    assert payload['planning_interface'] == 'joint_command.margin.v3'
    assert payload['execution_model_id'] == 'M0'
    assert payload['input_access_receipt'] == env.input_access_receipt
    assert payload['numerical_contract_id'] == 'unscaled_original_rows.v1'
    for wrong in [{}, {'numerical_contract_id': 'scaled_or_unknown'}]:
        with pytest.raises(ValueError, match='numerical'):
            route.run_policy_episode(env, {}, object(), 1, wrong)
    assert len(calls) == 1


def test_v4_runner_keeps_v3_policy_and_rejects_unbound_numerical_contract(monkeypatch):
    old = module('closed_loop_v3')
    before = old._source_binding()
    new = module('closed_loop_v4'); route = module('numerical_route'); margin = module('margin')
    assert old._source_binding() == before
    assert new.previous.__name__ == 'tools.autonomous_dispatch.closed_loop'
    assert len(old.SOURCE_FILES) == 41 and len(new.SOURCE_FILES) == 44
    assert set(new.SOURCE_FILES) - set(old.SOURCE_FILES) == {
        'tools/autonomous_dispatch/numerics.py', 'tools/autonomous_dispatch/numerical_route.py',
        'tools/autonomous_dispatch/closed_loop_v4.py'}
    assert new.SCHEMA == 'autonomous_dispatch.unscaled.matched_full_feedback.v4'
    assert new.ACTION_SCHEMA == old.ACTION_SCHEMA
    specs = [{'policy_id': 'priority_milp', 'kind': 'priority'}, {'policy_id': 'secure_milp', 'kind': 'secure'},
             {'policy_id': 'margin_15', 'kind': 'joint', 'config': {'variant': 'coupled', 'critical_reserve_multiplier': 1.5}}]
    assert new._check_specs(specs) == specs
    assert isinstance(new._factory(specs[-1]), margin.MarginDispatchPolicy)
    namespace = new._runner_namespace()
    assert namespace['run_policy_episode'] is route.run_policy_episode
    assert namespace['_factory'] is new._factory
    assert namespace['_payload_binding'] is new._payload_binding
    assert new._globals()['_environment_factory'] is new._environment_factory
    manifest = {'execution_model_id': 'M0', 'numerical_contract_id': 'unscaled_original_rows.v1',
        'scenarios': [{'id': 'one', 'start_hour': 100, 'duration_steps': 40, 'step_minutes': 15.}]}
    plan = {'scenario_ids': ['one']}
    captured = {}
    def env(root, **kwargs):
        captured.update(kwargs)
        return captured
    monkeypatch.setattr(new, 'UnscaledHospitalIESEnv', env)
    new._environment_factory(plan, manifest)(Path('D:/metadata_only'), execution_contract={})
    assert captured['allowed_hours'] == list(range(100, 111)) and captured['model_id'] == 'M0'
    # The inherited verifier reads and checks its own source/physical contract in production;
    # this seam isolates the additional required numerical marker without physical input reads.
    supplied = {'manifest': manifest}
    monkeypatch.setattr(new.previous, 'verify_batch', lambda path: (plan, supplied['manifest']))
    monkeypatch.setattr(new, '_read', lambda path: supplied['manifest'])
    monkeypatch.setattr(new.previous, 'register_batch', lambda *args, **kwargs: 'registered')
    assert new.verify_batch('metadata_only') == (plan, manifest)
    assert new.register_batch('metadata_only', 'unused', scenario_ids=['one'], policy_specs=specs,
                              preference_path='unused', phase='development') == 'registered'
    for wrong in [None, 'scaled_or_unknown']:
        supplied['manifest'] = copy.deepcopy(manifest)
        if wrong is None:
            supplied['manifest'].pop('numerical_contract_id')
        else:
            supplied['manifest']['numerical_contract_id'] = wrong
        with pytest.raises(ValueError, match='numerical'):
            new.verify_batch('metadata_only')
        with pytest.raises(ValueError, match='numerical'):
            new.register_batch('metadata_only', 'unused')
        with pytest.raises(ValueError, match='numerical'):
            new._environment_factory(plan, supplied['manifest'])
    monkeypatch.setattr(new.previous, '_payload_binding', lambda *args: {'checks': {'original': True}, 'passed': True})
    payload = {'planning_interface': margin.PLANNING_INTERFACE, 'numerical_contract_id': 'unscaled_original_rows.v1'}
    binding = new._payload_binding(payload, {}, manifest)
    assert binding['passed'] and binding['checks']['original']
    for wrong in [None, 'scaled_or_unknown']:
        changed = copy.deepcopy(payload)
        changed['numerical_contract_id'] = wrong
        assert not new._payload_binding(changed, {}, manifest)['passed']
    monkeypatch.setattr(new.previous, '_payload_binding', lambda *args: {'checks': {'original': False}, 'passed': False})
    assert not new._payload_binding(payload, {}, manifest)['passed']
