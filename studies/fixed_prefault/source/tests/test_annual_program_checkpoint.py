"""Pure checkpoint regression tests: no plant construction or physical solve."""
from __future__ import annotations

import copy
from collections import namedtuple
import hashlib
import importlib
import json
from pathlib import Path
import random
import subprocess
import sys
from types import CodeType, MethodType, SimpleNamespace

import numpy as np
import pytest


MODULE_PATH = Path(__file__).parents[1] / 'tools/annual_program/checkpoint.py'
sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parents[1] / 'src')]
BINDING = {'source': {'head': 'frozen-source'}, 'inputs': {'loads': 'frozen-input'},
           'config': {'hardware': 'unchanged', 'method': 'M_ready'}}


def api():
    assert MODULE_PATH.is_file(), 'The annual constructor-free checkpoint helper is missing'
    return importlib.import_module('tools.annual_program.checkpoint')


class Env:
    def __init__(self):
        raise AssertionError('Environment constructor must not run during restore')

    def reset(self, **kwargs):
        raise AssertionError('Reset must not run during checkpoint work')


class Core:
    def __init__(self, *args):
        raise AssertionError('Core constructor must not run during restore')

    def observe(self):
        raise AssertionError('Observation must not run during checkpoint work')


class Controller:
    def __init__(self):
        raise AssertionError('Controller constructor must not run during restore')


def runtime():
    from tools.annual_readiness.station import ContinuousStationState
    from hospital_ies.resilience import RuntimeState

    mailbox = {'observed_at': 14.0, 'ceiling': 80.0}
    env = object.__new__(Env)
    core = object.__new__(Core)
    controller = object.__new__(Controller)
    station = ContinuousStationState(requalification_minutes=60.0)
    station.unsupported_minutes = 7.5
    station.last_supply_end = 15.0
    station.claimed_actual_tokens.add('actual-start:1')
    station.total_diesel_starts_used = station.diesel_starts_used = 1
    env.__dict__.update(root=Path('D:/bound-source'), state=RuntimeState(
        np.array([25., 0., 0.]), 1900., {'GT_3'}, np.array([2., 0., 0.]), {'zone': 0.5}),
        core=core, segment_history=None, terminated=False, steps_done=1,
        engineering_core_type=Core, reset=MethodType(Env.reset, env),
        _solver_row_sidecar=SimpleNamespace(path=Path('E:/old-sidecar.gz')))
    core.__dict__.update(env=env, time=15.0, _m23_advance_depth=0, failed=False,
        station=station, pending_commands=[{'id': 2, 'arrival_minute': 17.0,
        'dispatch': {'gt_electric_kW': [0., 50., 0.]}}],
        gt_start_pending={1: {'ready': 19., 'started': 14., 'requested': 50.}},
        pending_contact_restore={'arrival_minute': 18.0},
        segments=[{'start_minute': 0., 'end_minute': 15.}],
        arrived_history={None: {'grid_import_kW': 50.}, 1: {'grid_import_kW': 55.}},
        annual_last_supply_proof={'supported': False},
        _m22_selection_key=(15.0, 'key'), cursor=np.int64(3),
        local_rng=np.random.default_rng(19))
    env.segment_history = core.segments
    controller.__dict__.update(alias='M', config={'unchanged': True}, mailbox=mailbox,
        history_min=np.array([[1., 2., 3.]] * 4),
        history_clock=0., path_tuple=(Path('E:/history'), {'seen'}))
    return env, controller


def plan_for(env, controller):
    result = {owner: {key: 'state' for key in vars(value)}
              for owner, value in [('env', env), ('core', env.core), ('controller', controller)]}
    result['env'].update(core='alias:core', segment_history='alias:core.segments',
        root='rebuild', engineering_core_type='rebuild', reset='rebuild',
        _solver_row_sidecar='external')
    result['core']['env'] = 'alias:env'
    result['controller']['mailbox'] = 'inplace'
    return result


def captured():
    env, controller = runtime()
    document = api().capture_checkpoint(env, controller, binding=BINDING,
        field_plan=plan_for(env, controller), runner_state={
            'committed_end_minute': 15.0, 'block_count': 1, 'event_cursor': 9,
            'solver_base': 31, 'error_cursor': 0, 'initial_preparation': {'ups': 50.0},
            'preparation_fee': 3.0, 'operating_fees': (1.0, 2.0),
            'journal_receipt': {'digest': 'acknowledged-block', 'index': 0}})
    return env, controller, document


def restore(document, *, env=None, controller=None, **overrides):
    if env is None:
        env, controller = runtime()
    rebuilt = {'env.root': env.root, 'env.engineering_core_type': Core, 'env.reset': env.reset}
    external = {'env._solver_row_sidecar': SimpleNamespace(path=Path('E:/new-sidecar.gz'))}
    arguments = dict(template_env=env, template_controller=controller,
        expected_binding=BINDING, rebuilt_fields=rebuilt, external_fields=external)
    arguments.update(overrides)
    return api().restore_checkpoint(document, **arguments)


def reseal(document):
    document['content_sha256'] = hashlib.sha256(json.dumps(
        {key: value for key, value in document.items() if key != 'content_sha256'},
        ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(',', ':')).encode('utf8')).hexdigest()
    return document


def test_checkpoint_helper_is_available():
    assert callable(api().capture_checkpoint)


def test_all_live_state_roundtrips_without_constructors_reset_or_observation():
    original, original_controller, document = captured()
    returned = restore(document)
    env, controller = returned['env'], returned['controller']
    assert type(env) is Env and type(env.core) is Core
    assert env.core.env is env and env.segment_history is env.core.segments
    assert env.core.station.unsupported_minutes == 7.5
    assert env.core.station.claimed_actual_tokens == {'actual-start:1'}
    assert env.core.pending_commands == original.core.pending_commands
    assert env.core.pending_commands is not original.core.pending_commands
    assert env.core.pending_contact_restore == {'arrival_minute': 18.0}
    assert env.core.gt_start_pending == {1: {'ready': 19., 'started': 14., 'requested': 50.}}
    assert env.core.arrived_history[None]['grid_import_kW'] == 50.
    assert type(env.core.cursor) is np.int64
    np.testing.assert_array_equal(env.state.gt_prev, original.state.gt_prev)
    assert env.state.extra_faulted == {'GT_3'}
    assert controller.path_tuple == original_controller.path_tuple
    assert env.reset.__self__ is env
    assert env._solver_row_sidecar.path == Path('E:/new-sidecar.gz')
    assert returned['runner_state']['event_cursor'] == 9
    assert returned['runner_state']['operating_fees'] == (1., 2.)
    for owner, value in [('env', original), ('core', original.core), ('controller', original_controller)]:
        assert set(document['coverage'][owner]) == set(vars(value))
        assert set(vars(returned['env'].core if owner == 'core' else returned[owner])) == set(vars(value))


def test_shared_factory_mailbox_is_restored_in_place():
    _, _, document = captured()
    template, controller = runtime()
    mailbox = controller.mailbox
    mailbox.clear()
    mailbox['unrelated_fresh_value'] = True
    factory_read = lambda: mailbox.get('ceiling')
    returned = restore(document, env=template, controller=controller)
    assert returned['controller'].mailbox is mailbox
    assert factory_read() == 80.0
    assert 'unrelated_fresh_value' not in mailbox


def test_global_and_local_rng_continue_exactly():
    old_python, old_numpy = random.getstate(), np.random.get_state()
    try:
        random.seed(31)
        np.random.seed(41)
        env, _, document = captured()
        expected = (random.random(), np.random.random(), env.core.local_rng.random())
        random.random()
        np.random.random()
        returned = restore(document)
        assert (random.random(), np.random.random(), returned['env'].core.local_rng.random()) == expected
    finally:
        random.setstate(old_python)
        np.random.set_state(old_numpy)


@pytest.mark.parametrize('owner', ['env', 'core', 'controller'])
def test_unclassified_live_field_is_rejected(owner):
    env, controller = runtime()
    plan = plan_for(env, controller)
    target = {'env': env, 'core': env.core, 'controller': controller}[owner]
    target.new_unknown_field = 1
    with pytest.raises(ValueError, match='coverage|unclassified|partition'):
        api().capture_checkpoint(env, controller, binding=BINDING, field_plan=plan,
            runner_state={'committed_end_minute': 15.0})


def test_unsupported_state_does_not_silently_drop_behavior_or_change_global_codec():
    from tools.model_review_v27.objective_snapshot import PHYSICAL_CLASSES
    before = set(PHYSICAL_CLASSES)
    env, controller = runtime()
    env.core.unregistered = object()
    with pytest.raises(TypeError, match='Unsupported|Unregistered|unsupported|unregistered'):
        api().capture_checkpoint(env, controller, binding=BINDING,
            field_plan=plan_for(env, controller), runner_state={'committed_end_minute': 15.0})
    assert PHYSICAL_CLASSES == before


def test_list_subclass_requires_explicit_rebuild_or_external_classification():
    class BehavioralLog(list):
        pass
    env, controller = runtime()
    env.core.behavioral_log = BehavioralLog([{'original': True}])
    with pytest.raises(TypeError, match='subclass|Unsupported|Unregistered'):
        api().capture_checkpoint(env, controller, binding=BINDING,
            field_plan=plan_for(env, controller), runner_state={'committed_end_minute': 15.0})


@pytest.mark.parametrize('part', ['source', 'inputs', 'config'])
def test_changed_bound_identity_is_rejected_before_mailbox_mutation(part):
    _, _, document = captured()
    template, controller = runtime()
    controller.mailbox.clear()
    controller.mailbox['sentinel'] = 1
    changed = copy.deepcopy(BINDING)
    changed[part]['drift'] = True
    with pytest.raises(ValueError, match='binding'):
        restore(document, env=template, controller=controller, expected_binding=changed)
    assert controller.mailbox == {'sentinel': 1}


def test_rebuilt_field_values_and_dynamic_types_are_verified():
    _, _, document = captured()
    template, controller = runtime()
    with pytest.raises(ValueError, match='rebuild|signature'):
        restore(document, env=template, controller=controller, rebuilt_fields={
            'env.root': Path('D:/different-source'), 'env.engineering_core_type': Core,
            'env.reset': template.reset})


@pytest.mark.parametrize('kind', ['rebuilt_fields', 'external_fields'])
def test_missing_rebind_is_rejected(kind):
    _, _, document = captured()
    with pytest.raises(ValueError, match='rebind|fields'):
        restore(document, **{kind: {}})


def test_external_resource_type_drift_is_rejected():
    _, _, document = captured()
    with pytest.raises(ValueError, match='type|signature'):
        restore(document, external_fields={'env._solver_row_sidecar': object()})


def test_unknown_template_field_is_rejected_before_shared_state_changes():
    _, _, document = captured()
    template, controller = runtime()
    template.new_constructor_field = 1
    controller.mailbox.clear()
    with pytest.raises(ValueError, match='template|coverage|unclassified'):
        restore(document, env=template, controller=controller)
    assert controller.mailbox == {}


def test_durable_save_load_and_exclusive_file_identity(tmp_path):
    _, _, document = captured()
    path = tmp_path / 'checkpoint.json'
    receipt = api().save_checkpoint(document, path)
    assert receipt['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert api().load_checkpoint(path, expected_binding=BINDING) == document
    with pytest.raises(FileExistsError):
        api().save_checkpoint(document, path)


def test_payload_corruption_is_rejected(tmp_path):
    _, _, document = captured()
    path = tmp_path / 'checkpoint.json'
    api().save_checkpoint(document, path)
    corrupted = json.loads(path.read_text(encoding='utf8'))
    corrupted['minute'] = 16.0
    path.write_text(json.dumps(corrupted), encoding='utf8')
    with pytest.raises(ValueError, match='SHA|integrity|digest'):
        api().load_checkpoint(path, expected_binding=BINDING)


def test_resealed_coverage_omission_is_rejected():
    _, _, document = captured()
    document['coverage']['core'].pop('station')
    with pytest.raises(ValueError, match='coverage|fields|partition'):
        restore(reseal(document))


@pytest.mark.parametrize('change', ['depth', 'clock', 'failed'])
def test_only_committed_quiescent_boundaries_are_accepted(change):
    env, controller = runtime()
    runner = {'committed_end_minute': 15.0}
    if change == 'depth':
        env.core._m23_advance_depth = 1
    elif change == 'clock':
        runner['committed_end_minute'] = 14.0
    else:
        env.core.failed = True
    with pytest.raises(ValueError, match='quiescent|committed|failed'):
        api().capture_checkpoint(env, controller, binding=BINDING,
            field_plan=plan_for(env, controller), runner_state=runner)


def test_binding_requires_source_inputs_and_configuration():
    env, controller = runtime()
    with pytest.raises(ValueError, match='binding'):
        api().capture_checkpoint(env, controller, binding={'source': 'only-source'},
            field_plan=plan_for(env, controller), runner_state={'committed_end_minute': 15.0})


def test_dataframe_rebuild_verifies_actual_values_and_dtypes():
    import pandas as pd
    env, controller = runtime()
    env.hourly = pd.DataFrame({'hour': [1, 2], 'load': [10.5, 20.5]})
    plan = plan_for(env, controller)
    plan['env']['hourly'] = 'rebuild'
    document = api().capture_checkpoint(env, controller, binding=BINDING, field_plan=plan,
        runner_state={'committed_end_minute': 15.0})
    template, template_controller = runtime()
    rebuilt = {'env.root': template.root, 'env.engineering_core_type': Core,
        'env.reset': template.reset, 'env.hourly': env.hourly.copy(deep=True)}
    restored = restore(document, env=template, controller=template_controller, rebuilt_fields=rebuilt)
    assert restored['env'].hourly.equals(env.hourly)
    rebuilt['env.hourly'].loc[0, 'load'] = 10.6
    with pytest.raises(ValueError, match='rebuild|signature'):
        restore(document, env=template, controller=template_controller, rebuilt_fields=rebuilt)


def test_unreset_template_with_no_live_core_restores_without_initialization():
    _, _, document = captured()
    template, controller = runtime()
    template.core = None
    template.state = None
    template.segment_history = []
    returned = restore(document, env=template, controller=controller)
    assert returned['env'].core.time == 15.
    assert returned['env'].core.env is returned['env']


def test_namespace_objects_and_nonfinite_arrays_preserve_typed_state():
    env, controller = runtime()
    env.core.prepared = SimpleNamespace(path=Path('E:/prepared'), value=(1, 2))
    env.core.nonfinite = np.array([np.nan, np.inf, -np.inf], dtype='float32')
    document = api().capture_checkpoint(env, controller, binding=BINDING,
        field_plan=plan_for(env, controller), runner_state={'committed_end_minute': 15.})
    returned = restore(document)
    assert type(returned['env'].core.prepared) is SimpleNamespace
    assert returned['env'].core.prepared.path == Path('E:/prepared')
    assert returned['env'].core.nonfinite.dtype == np.dtype('float32')
    np.testing.assert_array_equal(returned['env'].core.nonfinite, env.core.nonfinite)


def test_root_back_references_require_alias_classification():
    env, controller = runtime()
    plan = plan_for(env, controller)
    plan['core']['env'] = 'external'
    with pytest.raises(ValueError, match='back.reference|alias'):
        api().capture_checkpoint(env, controller, binding=BINDING, field_plan=plan,
            runner_state={'committed_end_minute': 15.})


def test_repeated_state_uses_explicit_typed_references_instead_of_silent_copy():
    # V1 rejected repeated state. V2 declares local graph identities because
    # native reset legitimately shares nested config dictionaries by shallow copy.
    env, controller = runtime()
    env.core.second_queue_name = env.core.pending_commands
    document = api().capture_checkpoint(env, controller, binding=BINDING,
        field_plan=plan_for(env, controller), runner_state={'committed_end_minute': 15.})
    assert document['schema'].endswith('.v2')
    assert '"type": "ref"' in json.dumps(document['state'])
    returned = restore(document)
    core = returned['env'].core
    assert core.second_queue_name is core.pending_commands
    assert core.pending_commands is not env.core.pending_commands
    core.second_queue_name.append({'id': 3})
    assert core.pending_commands[-1] == {'id': 3}


def test_native_shallow_config_aliases_restore_across_all_state_owners():
    env, controller = runtime()
    nested = {'restart_minutes': 12., 'enabled': [True, False]}
    env.config = {'_model_correctness_v23': nested, 'unchanged': 1.}
    env.cascade_config = dict(env.config, extra_declared_setting=True)
    env.core.config_part = nested
    controller.config['native_settings'] = nested
    document = api().capture_checkpoint(env, controller, binding=BINDING,
        field_plan=plan_for(env, controller), runner_state={'committed_end_minute': 15.})
    returned = restore(document)
    actual = returned['env'].config['_model_correctness_v23']
    assert actual is returned['env'].cascade_config['_model_correctness_v23']
    assert actual is returned['env'].core.config_part
    assert actual is returned['controller'].config['native_settings']
    assert actual is not nested
    actual['enabled'].append(True)
    assert returned['env'].cascade_config['_model_correctness_v23']['enabled'] == [True, False, True]


@pytest.mark.parametrize('kind', ['array', 'rng', 'object', 'set'])
def test_typed_mutable_aliases_preserve_identity_and_values(kind):
    env, controller = runtime()
    value = {'array': lambda: np.array([1., np.nan], dtype='float32'),
        'rng': lambda: np.random.default_rng(19),
        'object': lambda: SimpleNamespace(values=[1., 2.]),
        'set': lambda: {1, 2}}[kind]()
    env.shared_value = value
    env.core.shared_value = value
    controller.shared_value = value
    document = api().capture_checkpoint(env, controller, binding=BINDING,
        field_plan=plan_for(env, controller), runner_state={'committed_end_minute': 15.})
    returned = restore(document)
    actual = returned['env'].shared_value
    assert actual is returned['env'].core.shared_value is returned['controller'].shared_value
    assert actual is not value and type(actual) is type(value)
    if kind == 'array':
        np.testing.assert_array_equal(actual, value)
    elif kind == 'rng':
        assert actual.random() == value.random()
    elif kind == 'object':
        assert actual.values == value.values
    else:
        assert actual == value


def test_shared_inplace_mailbox_alias_rebinds_to_the_factory_dictionary():
    env, controller = runtime()
    env.core.shared_mailbox = controller.mailbox
    document = api().capture_checkpoint(env, controller, binding=BINDING,
        field_plan=plan_for(env, controller), runner_state={'committed_end_minute': 15.})
    template, template_controller = runtime()
    mailbox = template_controller.mailbox
    mailbox.clear()
    mailbox['fresh'] = True
    returned = restore(document, env=template, controller=template_controller)
    assert returned['controller'].mailbox is mailbox
    assert returned['env'].core.shared_mailbox is mailbox
    mailbox['ceiling'] = 90.
    assert returned['env'].core.shared_mailbox['ceiling'] == 90.


def test_undeclared_container_cycle_is_still_rejected():
    env, controller = runtime()
    env.core.cycle = []
    env.core.cycle.append(env.core.cycle)
    with pytest.raises(ValueError, match='[Cc]yclic|cycle'):
        api().capture_checkpoint(env, controller, binding=BINDING,
            field_plan=plan_for(env, controller), runner_state={'committed_end_minute': 15.})


@pytest.mark.parametrize('encoded', [
    {'type': 'ref', 'value': 9},
    {'type': 'ref', 'value': True},
    {'type': 'shared', 'id': 0, 'value': {'type': 'ref', 'value': 0}},
    {'type': 'shared', 'id': 0, 'value': 1},
    [{'type': 'shared', 'id': 0, 'value': []}, {'type': 'shared', 'id': 0, 'value': []}],
])
def test_malformed_typed_shared_references_are_rejected(encoded):
    with pytest.raises(ValueError, match='[Rr]ef|[Ss]hared|typed|[Cc]yclic'):
        api()._decode(encoded)


def test_dangling_reference_in_resealed_checkpoint_rejects_before_factory_mutation():
    _, _, document = captured()
    document['state']['core'] = {'type': 'ref', 'value': 999999}
    template, controller = runtime()
    controller.mailbox.clear()
    controller.mailbox['sentinel'] = True
    with pytest.raises(ValueError, match='[Rr]ef'):
        restore(reseal(document), env=template, controller=controller)
    assert controller.mailbox == {'sentinel': True}


def test_v1_checkpoint_representation_is_not_admitted_as_v2():
    _, _, document = captured()
    document['schema'] = 'hospital_ies.annual_program.quiescent_checkpoint.v1'
    with pytest.raises(ValueError, match='schema'):
        restore(reseal(document))


def test_standalone_row_codec_retains_the_nonreference_representation():
    shared = [1, 2]
    encoded = api()._encode({'first': shared, 'second': shared})
    assert encoded == {'type': 'dict', 'value': [['first', [1, 2]], ['second', [1, 2]]]}
    assert api()._decode(encoded) == {'first': [1, 2], 'second': [1, 2]}


def test_rebuild_function_closure_values_are_verified():
    env, controller = runtime()

    def method_factory(value):
        def method(self):
            return value
        return method

    env.special_method = MethodType(method_factory('registered'), env)
    plan = plan_for(env, controller)
    plan['env']['special_method'] = 'rebuild'
    document = api().capture_checkpoint(env, controller, binding=BINDING, field_plan=plan,
        runner_state={'committed_end_minute': 15.})
    template, template_controller = runtime()
    rebuilt = {'env.root': template.root, 'env.engineering_core_type': Core,
        'env.reset': template.reset,
        'env.special_method': MethodType(method_factory('different'), template)}
    with pytest.raises(ValueError, match='rebuild|signature'):
        restore(document, env=template, controller=template_controller, rebuilt_fields=rebuilt)


def compiled_rebuild_method(source):
    namespace = {'__name__': 'checkpoint_code_fixture'}
    exec(compile(source, 'checkpoint_code_fixture.py', 'exec', dont_inherit=True), namespace)
    return namespace['rebuilt']


def test_rebuild_code_fingerprint_is_stable_when_a_string_constant_is_interned():
    constant = 'a non identifier checkpoint constant {:04d}'.format(1)
    method = compiled_rebuild_method('def rebuilt(self):\n    return ' + json.dumps(constant) + '\n')
    before = api()._fingerprint(method)
    sys.intern(next(item for item in method.__code__.co_consts if type(item) is str))
    assert api()._fingerprint(method) == before
    assert before['code_fingerprint_algorithm'] == 'python.code_fields.v1'


def test_native_v28_and_readiness_reset_code_fingerprints_are_interning_stable():
    # Inspect the real producer functions and nested reset code without creating
    # an environment, calling reset, or solving any physical trajectory.
    from tools.annual_readiness.environment import with_readiness
    from tools.model_review_v28.environment import OperatingHospitalIESEnv

    def intern_constants(value):
        if type(value) is str:
            sys.intern(value)
        elif type(value) is CodeType:
            for item in value.co_consts:
                intern_constants(item)
        elif type(value) in (tuple, frozenset):
            for item in value:
                intern_constants(item)

    for function in (OperatingHospitalIESEnv.reset, with_readiness):
        before = api()._fingerprint(function)
        intern_constants(function.__code__)
        assert api()._fingerprint(function) == before


def test_rebuild_code_fingerprint_survives_execution_and_a_fresh_process():
    source = ('def rebuilt(self):\n'
        '    constants = (b"literal bytes", 3+4j, ..., (1, None, True))\n'
        '    nested = lambda value: value + 1\n'
        '    return nested(1) in {1, 2, 3}, constants, "runtime checkpoint literal"\n')
    method = compiled_rebuild_method(source)
    before = api()._fingerprint(method)
    for _ in range(100):
        method(None)
    assert api()._fingerprint(method) == before
    script = ('import json\nfrom tools.annual_program.checkpoint import _fingerprint\n'
        "namespace = {'__name__': 'checkpoint_code_fixture'}\n"
        'exec(compile(' + json.dumps(source) + ", 'checkpoint_code_fixture.py', 'exec', dont_inherit=True), namespace)\n"
        "print(json.dumps(_fingerprint(namespace['rebuilt']), sort_keys=True))\n")
    result = subprocess.run([sys.executable, '-c', script], cwd=MODULE_PATH.parents[2],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == before


@pytest.mark.parametrize('change', ['constant', 'opcode'])
def test_changed_rebuild_code_is_rejected_even_with_the_same_function_identity(change):
    original_source = 'def rebuilt(self):\n    return 10 + self.steps_done\n'
    changed_source = ('def rebuilt(self):\n    return 11 + self.steps_done\n' if change == 'constant'
        else 'def rebuilt(self):\n    return 10 - self.steps_done\n')
    env, controller = runtime()
    env.special_method = MethodType(compiled_rebuild_method(original_source), env)
    plan = plan_for(env, controller)
    plan['env']['special_method'] = 'rebuild'
    document = api().capture_checkpoint(env, controller, binding=BINDING, field_plan=plan,
        runner_state={'committed_end_minute': 15.})
    template, template_controller = runtime()
    rebuilt = {'env.root': template.root, 'env.engineering_core_type': Core,
        'env.reset': template.reset,
        'env.special_method': MethodType(compiled_rebuild_method(changed_source), template)}
    with pytest.raises(ValueError, match='rebuild|signature'):
        restore(document, env=template, controller=template_controller, rebuilt_fields=rebuilt)


def test_rebuilt_thermal_namedtuple_cache_has_compact_verified_fingerprint():
    Row = namedtuple('Pandas', ['hour_of_year', 'zone_name', 'value'])
    env, controller = runtime()
    env.thermal_cache = ({'zone': {'capacity': 5.}},
        {hour: {'zone': Row(hour, 'zone', 5.0)} for hour in range(1000)})
    plan = plan_for(env, controller)
    plan['env']['thermal_cache'] = 'rebuild'
    document = api().capture_checkpoint(env, controller, binding=BINDING, field_plan=plan,
        runner_state={'committed_end_minute': 15.})
    assert len(json.dumps(document['coverage']['env']['thermal_cache']['fingerprint'])) < 1000
    template, template_controller = runtime()
    rebuilt = {'env.root': template.root, 'env.engineering_core_type': Core,
        'env.reset': template.reset, 'env.thermal_cache': copy.deepcopy(env.thermal_cache)}
    returned = restore(document, env=template, controller=template_controller, rebuilt_fields=rebuilt)
    assert returned['env'].thermal_cache[1][500]['zone'].value == 5.0
    rebuilt['env.thermal_cache'][1][500]['zone'] = Row(500, 'zone', 6.0)
    with pytest.raises(ValueError, match='rebuild|signature'):
        restore(document, env=template, controller=template_controller, rebuilt_fields=rebuilt)


def v28_object_frame():
    """V28 attaches source-reference dictionaries to otherwise ordinary hourly data."""
    import pandas as pd
    frame = pd.DataFrame({'hour_of_year': np.array([1, 2, 3], dtype='int64'),
        'timestamp': pd.date_range('2017-01-01', periods=3, freq='h'),
        'Q_h_demand_kW': [12.5, 13.5, 14.5],
        'v28_thermal_service_trace': [
            {'path': 'source.csv', 'hour_of_year': 1, 'nested': {
                'array': np.array([1., np.nan], dtype='float32'), 'flags': (True, None)}},
            None, float('nan')],
        'v28_local_thermal_source_reference': [
            {'hour_of_year': 1, 'laundry_naturalgas_kW': 3.5},
            {'hour_of_year': 2, 'laundry_naturalgas_kW': 4.5}, None]})
    frame.index = pd.Index([10, 20, 30], dtype='int64', name='source_row')
    return frame


def test_v28_dictionary_dataframe_rebuild_roundtrips_without_stringifying_cells():
    env, controller = runtime()
    env.hourly = v28_object_frame()
    plan = plan_for(env, controller)
    plan['env']['hourly'] = 'rebuild'
    document = api().capture_checkpoint(env, controller, binding=BINDING, field_plan=plan,
        runner_state={'committed_end_minute': 15.})
    fingerprint = document['coverage']['env']['hourly']['fingerprint']
    assert len(json.dumps(fingerprint)) < 2000
    template, template_controller = runtime()
    rebuilt_frame = v28_object_frame()
    rebuilt = {'env.root': template.root, 'env.engineering_core_type': Core,
        'env.reset': template.reset, 'env.hourly': rebuilt_frame}
    returned = restore(document, env=template, controller=template_controller, rebuilt_fields=rebuilt)
    assert returned['env'].hourly is rebuilt_frame
    assert type(returned['env'].hourly.at[10, 'v28_thermal_service_trace']) is dict
    assert returned['env'].hourly.at[10, 'v28_thermal_service_trace']['nested']['array'].dtype == np.dtype('float32')


def test_v28_dictionary_dataframe_hashes_nested_array_elements_hidden_by_numpy_repr():
    original = v28_object_frame()
    changed = v28_object_frame()
    original.at[10, 'v28_thermal_service_trace']['nested']['array'] = np.arange(2000.)
    changed.at[10, 'v28_thermal_service_trace']['nested']['array'] = np.arange(2000.)
    changed.at[10, 'v28_thermal_service_trace']['nested']['array'][1000] += .25
    assert str(original.at[10, 'v28_thermal_service_trace']) == str(changed.at[10, 'v28_thermal_service_trace'])
    assert api()._fingerprint(original) != api()._fingerprint(changed)


def test_v28_dictionary_dataframe_rejects_unregistered_object_cell_instead_of_repr():
    frame = v28_object_frame()
    frame.at[20, 'v28_thermal_service_trace'] = object()
    with pytest.raises(TypeError, match='Unregistered|Unsupported'):
        api()._fingerprint(frame)


@pytest.mark.parametrize('change', ['dictionary_value', 'dictionary_key', 'dictionary_order',
    'array_value', 'array_dtype', 'none_to_nan', 'numeric', 'timestamp', 'index_value',
    'index_dtype', 'index_name', 'column_order', 'column_name', 'numeric_dtype'])
def test_v28_dictionary_dataframe_rebuild_rejects_each_bound_content_change(change):
    import pandas as pd
    env, controller = runtime()
    env.hourly = v28_object_frame()
    plan = plan_for(env, controller)
    plan['env']['hourly'] = 'rebuild'
    document = api().capture_checkpoint(env, controller, binding=BINDING, field_plan=plan,
        runner_state={'committed_end_minute': 15.})
    changed = v28_object_frame()
    trace = changed.at[10, 'v28_thermal_service_trace']
    if change == 'dictionary_value':
        trace['hour_of_year'] = 9
    elif change == 'dictionary_key':
        trace[1] = trace.pop('hour_of_year')
    elif change == 'dictionary_order':
        changed.at[10, 'v28_thermal_service_trace'] = dict(reversed(list(trace.items())))
    elif change == 'array_value':
        trace['nested']['array'][0] = 1.25
    elif change == 'array_dtype':
        trace['nested']['array'] = trace['nested']['array'].astype('float64')
    elif change == 'none_to_nan':
        changed.at[20, 'v28_thermal_service_trace'] = float('nan')
    elif change == 'numeric':
        changed.at[10, 'Q_h_demand_kW'] += .25
    elif change == 'timestamp':
        changed.at[10, 'timestamp'] += pd.Timedelta(minutes=1)
    elif change == 'index_value':
        changed.index = pd.Index([11, 20, 30], name='source_row')
    elif change == 'index_dtype':
        changed.index = changed.index.astype('int32')
    elif change == 'index_name':
        changed.index.name = 'different_source_row'
    elif change == 'column_order':
        changed = changed.iloc[:, ::-1]
    elif change == 'column_name':
        changed = changed.rename(columns={'v28_thermal_service_trace': 'different_trace'})
    else:
        changed['Q_h_demand_kW'] = changed['Q_h_demand_kW'].astype('float32')
    template, template_controller = runtime()
    rebuilt = {'env.root': template.root, 'env.engineering_core_type': Core,
        'env.reset': template.reset, 'env.hourly': changed}
    with pytest.raises(ValueError, match='rebuild|signature'):
        restore(document, env=template, controller=template_controller, rebuilt_fields=rebuilt)
