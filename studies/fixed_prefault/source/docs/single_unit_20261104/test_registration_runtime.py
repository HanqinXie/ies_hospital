"""Focused declaration/lowering regressions; no formal scientific trajectories."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.sci_engineering.design import digest

ORIGINAL = Path('E:/hospital_ies_cooling_dynamics_20261104_run01/cases')


def api(name='registration'):
    try:
        return importlib.import_module('tools.single_unit_20261104.' + name)
    except ModuleNotFoundError as exc:
        if not exc.name.startswith('tools.single_unit_20261104'):
            raise
        pytest.fail('Explicit single-unit declaration/lowering adapter is missing', pytrace=False)


def base(topic='T06', variant='unattended'):
    path = ORIGINAL / (topic + '_' + ('U' if variant == 'unattended' else 'R') + '.json')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    return value, dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize('kind,topic,target,native', [
    ('tower_unit', 'T05', 'TOWER_1', 'TOWER_1'),
    ('primary_pump', 'T06', 'EC_1_PRIMARY_PUMP', 'EC_1'),
])
@pytest.mark.parametrize('variant', ['unattended', 'responsive'])
def test_derivative_preserves_base_and_authenticates_single_cause(kind, topic, target, native, variant):
    old, binding = base(topic, variant); before = deepcopy(old)
    case = api().make_case(old, kind=kind, variant=variant, base_binding=binding)
    assert old == before and case['minute1'] == old['minute1']
    assert case['id'] != old['id'] and case['id'] == case['scenario']['id']
    meta = case['unit_study']
    assert meta['base_case'] == old and meta['base_binding'] == binding
    assert case['scenario']['model_closure_events'] == [
        dict(time_minute=60., availability={target: 0.}),
        dict(time_minute=120., availability={target: 1.})]
    assert meta['native_scene']['model_closure_events'] == [
        dict(time_minute=60., availability={native: 0.}),
        dict(time_minute=120., availability={native: 1.})]
    assert meta['declared_scene_sha256'] == digest(case['scenario'])
    assert meta['native_scene_sha256'] == digest(meta['native_scene'])
    assert api().validate_case(case) == case
    for field in ('configuration', 'model_closure_v22', 'model_correctness_v23',
                  'engineering_completion_v24', 'model_correction_v25', 'policy', 'operating_rule_id'):
        assert case.get(field) == old.get(field)


@pytest.mark.parametrize('topic', ['T05', 'T06'])
def test_healthy_no_dispatch_is_separate_zero_event_unattended_case(topic):
    old, binding = base(topic)
    case = api().make_case(old, kind='healthy_no_dispatch', variant='unattended', base_binding=binding)
    assert case['scenario']['model_closure_events'] == []
    assert case['unit_study']['native_scene']['model_closure_events'] == []
    assert case['minute1']['upper_commands_disabled_at_minute'] == 60.
    assert case['scenario']['normal_verification'] is True
    with pytest.raises(ValueError):
        old, binding = base(topic, 'responsive')
        api().make_case(old, kind='healthy_no_dispatch', variant='responsive', base_binding=binding)


@pytest.mark.parametrize('damage', ['load', 'events', 'native', 'metadata', 'base', 'source', 'difference'])
def test_mutation_is_rejected(damage):
    old, binding = base()
    case = api().make_case(old, kind='primary_pump', variant='unattended', base_binding=binding)
    if damage == 'load': case['scenario']['grid_limit_kW'] += 1.
    elif damage == 'events': case['scenario']['model_closure_events'][0]['availability']['AUX_CHW_PUMP'] = 0.
    elif damage == 'native': case['unit_study']['native_scene']['model_closure_events'][0]['availability']['EC_2'] = 0.
    elif damage == 'metadata': case['minute1']['variant'] = 'responsive'
    elif damage == 'base': case['unit_study']['base_case']['scenario']['duration_steps'] = 181
    elif damage == 'source': case['unit_study']['source_baseline_commit'] = '0' * 40
    else: case['unit_study']['strict_difference_list'] = []
    with pytest.raises(ValueError): api().validate_case(case)


def test_base_binding_is_required_and_byte_verified(tmp_path):
    old, binding = base()
    with pytest.raises(ValueError):
        api().make_case(old, kind='primary_pump', variant='unattended', base_binding=None)
    file = tmp_path / 'CASE.json'; file.write_text(json.dumps(old), encoding='utf8')
    pin = dict(path=str(file.resolve()), sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    case = api().make_case(old, kind='primary_pump', variant='unattended', base_binding=pin)
    file.write_text(json.dumps(dict(old, id='tampered')), encoding='utf8')
    with pytest.raises(ValueError): api().validate_case(case)


def test_old_minute_registration_and_native_event_validator_remain_strict():
    from tools.annual_readiness.registration import load_registry
    from tools.minute1_20261103.registration import validate_case as old_validate
    from tools.model_closure_v22.environment import validate_events
    old, binding = base()
    case = api().make_case(old, kind='primary_pump', variant='unattended', base_binding=binding)
    assert old_validate(old, load_registry()) == old
    with pytest.raises(ValueError): old_validate(case, load_registry())
    with pytest.raises(ValueError): validate_events(case['scenario']['model_closure_events'])
    assert validate_events(case['unit_study']['native_scene']['model_closure_events'])


def test_causal_ledger_separates_raw_hardware_from_functional_interlock_and_repairs():
    old, binding = base()
    case = api().make_case(old, kind='primary_pump', variant='unattended', base_binding=binding)
    fn = api('runtime').causal_ledger
    for minute, pump in [(59., 1.), (60., 0.), (119.9, 0.), (120., 1.)]:
        got = fn(case, minute, {'EC_1': pump, 'AUX_CHW_PUMP': 1.})
        assert got['raw_component_availability']['EC_1'] == 1.
        assert got['raw_component_availability']['EC_1_PRIMARY_PUMP'] == pump
        assert got['effective_functional_availability']['EC_1'] == pump
        assert got['sole_exogenous_cause'] == 'EC_1_PRIMARY_PUMP'
        assert got['current_time_minute'] == minute
        assert 'calendar' not in json.dumps(got)
    with pytest.raises(ValueError): fn(case, 60., {'EC_1': 1., 'AUX_CHW_PUMP': 1.})
    with pytest.raises(ValueError): fn(case, 60., {'EC_1': 0., 'AUX_CHW_PUMP': 0.})


def test_segment_extension_calls_native_commit_once_and_adds_no_physical_override():
    rt = api('runtime')
    old, binding = base()
    case = api().make_case(old, kind='primary_pump', variant='unattended', base_binding=binding)
    class Native:
        def _after_actual_segment(self, segment, result):
            self.commits += 1; segment['native_committed'] = True
    class Core(rt.SingleUnitLedgerMixin, Native): pass
    core = Core(); core.commits = 0; core.env = SimpleNamespace(single_unit_case=case)
    # Native hardware_availability is attached by an outer advance exporter
    # after this hook; the accepted result already owns the exact raw map.
    segment = dict(start_minute=60.)
    core._after_actual_segment(segment, {'_cooling_raw_availability': {'EC_1': 0., 'AUX_CHW_PUMP': 1.}})
    assert core.commits == 1 and segment['native_committed']
    assert segment['single_unit_causal_ledger']['raw_component_availability']['EC_1'] == 1.
    assert not ({'_solve', '_local_interval_action', '_hardware_data_at', 'boundary', '_next_boundary'}
                & set(vars(rt.SingleUnitLedgerMixin)))


def test_bound_reset_accepts_only_declared_scene_and_passes_exact_lowered_scene():
    rt = api('runtime')
    old, binding = base()
    case = api().make_case(old, kind='primary_pump', variant='unattended', base_binding=binding)
    seen = []
    def native_reset(*, scenario, seed=None):
        seen.append((deepcopy(scenario), seed)); return 'native result'
    env = SimpleNamespace(reset=native_reset, engineering_core_type=type('Core', (), {}),
                          episode_snapshot_function=lambda e: {'native': True})
    rt.install_adapter(env, case)
    assert env.reset(scenario=deepcopy(case['scenario']), seed=7) == 'native result'
    assert seen == [(case['unit_study']['native_scene'], 7)]
    wrong = deepcopy(case['scenario']); wrong['model_closure_events'][0]['availability']['EC_1'] = 0.
    with pytest.raises(ValueError): env.reset(scenario=wrong)
    assert len(seen) == 1
