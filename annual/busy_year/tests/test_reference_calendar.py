import importlib
import gzip
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def api():
    try:
        return importlib.import_module('calendar_plan')
    except ModuleNotFoundError:
        pytest.fail('Actual-journal reference validation is missing', pytrace=False)


def test_missing_reference_does_not_freeze_calendar(tmp_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        api().resolve_plan(tmp_path / 'missing')


def test_summary_only_is_not_actual_production_evidence(tmp_path):
    (tmp_path / 'RESULT.json').write_text('{"status":"completed"}', encoding='utf8')
    with pytest.raises(ValueError):
        api().resolve_plan(tmp_path)


@pytest.mark.parametrize('action,device', [({'gt_electric_kW': [1., 2.]}, 'GT_3'),
    ({'gt_electric_kW': [1., 2., True]}, 'GT_3'),
    ({'boiler_steam_lbph': None}, 'BOILER_1'),
    ({'electric_chiller_cooling_kW': [1., -1., 1.]}, 'EC_1')])
def test_missing_or_invalid_actual_production_is_not_zero_filled(action, device):
    with pytest.raises(ValueError):
        api().actual_production(action, device)


def test_actual_output_units_and_selected_component():
    m = api()
    assert m.actual_production({'gt_electric_kW': [1., 2., 3.]}, 'GT_3') == 3.
    assert m.actual_production({'boiler_steam_lbph': 4.}, 'BOILER_1') == 4.
    assert m.actual_production({'electric_chiller_cooling_kW': [5., 6., 7.]}, 'EC_1') == 5.


def test_window_requires_complete_contiguous_actual_segments():
    m = api()
    segments = [dict(start_minute=0., end_minute=30., action={'boiler_steam_lbph': 100.}),
                dict(start_minute=31., end_minute=60., action={'boiler_steam_lbph': 100.})]
    with pytest.raises(ValueError):
        m.integrate_window(segments, 0., 60., 'BOILER_1')


def test_window_integrates_only_before_anchor_and_zero_remains_zero():
    m = api()
    segments = [dict(start_minute=0., end_minute=30., action={'boiler_steam_lbph': 0.}),
                dict(start_minute=30., end_minute=60., action={'boiler_steam_lbph': 0.}),
                dict(start_minute=60., end_minute=90., action={'boiler_steam_lbph': 999.})]
    assert m.integrate_window(segments, 0., 60., 'BOILER_1') == 0.


def test_absent_reference_proof_refuses_revalidation():
    with pytest.raises(ValueError):
        api().verify_reference_proof(api().candidate_plan())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False)+'\n', encoding='utf8')


@pytest.fixture(scope='module')
def synthetic_reference(tmp_path_factory):
    """A fake annual index with genuine hash/clock rules; never science evidence."""
    m = api()
    root = tmp_path_factory.mktemp('synthetic_normal_reference')
    source = {'fake_code_%04d.py' % n:'1'*64 for n in range(m.REFERENCE_SOURCE_COUNT)}
    inputs = {'fake_input_%02d.csv' % n:'2'*64 for n in range(36)}
    # Deliberately test a small data object while retaining production's complete
    # 35040-block index shape. Only constants for expected identities are patched
    # by the test fixture; production CLI exposes no such overrides.
    case = {'id':'synthetic_normal_case', 'annual_program':{'parameters':dict(
        method='M_ready', mode='N', configuration='H2', start_hour=1,
        duration_minutes=525600., calendar=[],execution_revision=m.EXECUTION_REVISION)},
        'execution_revision':dict(model_id=m.EXECUTION_REVISION)}
    pins = dict(source=m.canonical_sha256(source), inputs=m.canonical_sha256(inputs),
                case=m.canonical_sha256(case))
    identity = dict(case_id=case['id'], model_id=m.MODEL_ID, git_commit=m.REFERENCE_COMMIT,
        source_sha256=pins['source'], input_sha256=pins['inputs'], case_sha256=pins['case'])
    write_json(root/'CASE.json', {'case':case})
    write_json(root/'BINDINGS.json', dict(source=source, inputs=inputs, git_commit=m.REFERENCE_COMMIT))
    write_json(root/'RESULT.json', dict(status='completed', observed_until_minute=525600.,
        requested_end_minute=525600., unknown_tail_minutes=0., source_and_inputs_unchanged=True,
        execution_model_id=m.EXECUTION_REVISION,
        annual_dynamic_owner_contract='hospital_ies.annual_dynamic.accepted_engineering_owner.v1',
        **identity))
    write_json(root/'evidence/IDENTITY.json', identity)
    identity_sha = m.sha256(root/'evidence/IDENTITY.json')
    windows = {}
    for item in (r for r in m.candidate_plan()['fault_intervals'] if r['event_id'] in m.ANCHOR_IDS):
        for day in range(7):
            anchor = item['start_minute']+day*1440.
            for index in range(int((anchor-60.)/15.), int(anchor/15.)):
                windows[index] = (item['event_id'], day)
    records, raw_list, previous = [], [], None
    for n in range(35040):
        a, b = n*15., (n+1)*15.
        relative = 'blocks/block_%06d.json.gz' % n
        raw_sha = '0'*64
        if n in windows:
            event_id, day = windows[n]
            positive = day >= {'E1':0, 'E2':1, 'E3':2,'E8':0}[event_id]
            action = dict(gt_electric_kW=[0., 0., 10. if positive else 0.],
                boiler_steam_lbph=100. if positive else 0.,
                electric_chiller_cooling_kW=[20. if positive else 0., 0., 0.])
            block = dict(start_minute=a, end_minute=b,
                segments=[dict(start_minute=a, end_minute=b, action=action)], solver_calls=[])
            path = root/'evidence'/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(json.dumps(block, allow_nan=False).encode(), mtime=0))
            raw_sha = m.sha256(path)
        record = dict(schema=m.JOURNAL_SCHEMA, index=n, file=relative, sha256=raw_sha,
            identity_sha256=identity_sha, previous_digest=previous, start_minute=a, end_minute=b,
            segment_count=1, solver_call_count=0)
        encoded = (json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=False)+'\n').encode()
        record['digest'] = hashlib.sha256(encoded).hexdigest()
        previous = record['digest']
        records.append(record)
        raw_list.append(dict(file=relative, sha256=raw_sha))
    (root/'evidence/blocks.jsonl').write_text(''.join(json.dumps(r, sort_keys=True,
        separators=(',', ':'))+'\n' for r in records), encoding='utf8')
    write_json(root/'evidence/SUMMARY.json', dict(schema=m.JOURNAL_SCHEMA, status='completed',
        integrity_verified=True, journal_write_error=None, identity=identity, identity_sha256=identity_sha,
        observation=dict(observed_from_minute=0., observed_until_minute=525600.,
            observed_duration_minutes=525600., requested_end_minute=525600., unknown_tail_minutes=0.,
            complete=True, contiguous=True, quantities_are_observed_prefix_only=True),
        block_count=35040, last_digest=previous, raw_files_sha256=raw_list))
    return root, pins


@pytest.fixture
def reference(synthetic_reference, monkeypatch):
    root, pins = synthetic_reference
    m = api()
    monkeypatch.setattr(m, 'REFERENCE_SOURCE_SHA256', pins['source'])
    monkeypatch.setattr(m, 'REFERENCE_INPUT_SHA256', pins['inputs'])
    monkeypatch.setattr(m, 'REFERENCE_CASE_SHA256', pins['case'])
    return root


def test_full_index_and_actual_raw_select_first_positive_candidates(reference):
    m = api()
    plan = m.resolve_plan(reference)
    assert plan['status'] == 'frozen'
    default = m.candidate_plan()['fault_intervals']
    assert [r['start_minute'] for r in plan['fault_intervals'][:3]] == [
        default[0]['start_minute'], default[1]['start_minute']+1440., default[2]['start_minute']+2880.]
    assert len(plan['reference_proof']['candidate_checks']) == 7
    assert plan['reference_proof']['selected_anchors']['E8']['device']=='EC_1_PRIMARY_PUMP'
    assert m.verify_reference_proof(plan)
    assert m.exposure_hours(plan) == dict(N=8012., D=669.75, F=76., FD=2.25)


def test_real_builder_and_runtime_agree_on_frozen_case_layout(reference, tmp_path, monkeypatch):
    """Only the annual reference and package binding are synthetic, never a run."""
    import build_cases
    import runtime_checks
    binding = dict(package_manifest_sha256='3'*64, source_sha256='4'*64,
                   input_sha256='5'*64, git_commit='6'*40)
    monkeypatch.setattr(build_cases, '_package_binding', lambda: binding.copy())
    monkeypatch.setattr(runtime_checks, 'verify_package', lambda _: dict(
        manifest_sha256=binding['package_manifest_sha256'],
        source_tree_sha256=binding['source_sha256'], input_sha256=binding['input_sha256'],
        git_commit=binding['git_commit']))
    frozen = tmp_path / 'external_frozen'
    index = build_cases.freeze(reference, frozen)
    rows = runtime_checks.load_frozen_cases(build_cases.PACKAGE, frozen)
    assert index['calendar_state'] == 'frozen'
    assert index['annual_admitted'] is False
    assert len(rows) == 3
    assert len({row['case_id'] for row in rows}) == 3
    assert all(Path(row['file']).is_file() for row in rows)
    assert {row['method'] for row in rows} == set(build_cases.METHODS)


def test_tampered_actual_block_fails_even_when_summary_is_unchanged(reference):
    m = api()
    original = m.candidate_plan()['fault_intervals'][0]['start_minute']
    path = reference/'evidence/blocks'/('block_%06d.json.gz' % int((original-60.)/15.))
    saved = path.read_bytes()
    try:
        path.write_bytes(saved+b'tampered')
        with pytest.raises(ValueError, match='raw block SHA'):
            m.resolve_plan(reference)
    finally:
        path.write_bytes(saved)


def test_changed_original_normal_source_is_not_accepted(reference):
    m = api()
    path = reference/'BINDINGS.json'
    saved = path.read_bytes()
    try:
        value = json.loads(saved)
        value['source']['fake_code_0000.py'] = '9'*64
        write_json(path, value)
        with pytest.raises(ValueError, match='source/input binding'):
            m.resolve_plan(reference)
    finally:
        path.write_bytes(saved)


def test_reference_index_gap_or_chain_tamper_is_not_accepted(reference):
    m = api()
    path = reference/'evidence/blocks.jsonl'
    saved = path.read_bytes()
    try:
        first, remainder = saved.split(b'\n', 1)
        value = json.loads(first)
        value['end_minute'] = 30.
        path.write_bytes(json.dumps(value).encode()+b'\n'+remainder)
        with pytest.raises(ValueError, match='index identity'):
            m.resolve_plan(reference)
    finally:
        path.write_bytes(saved)


def test_wrong_normal_method_or_annual_condition_is_not_accepted(reference):
    m = api()
    path = reference/'CASE.json'
    saved = path.read_bytes()
    try:
        value = json.loads(saved)
        value['case']['annual_program']['parameters']['method'] = 'C_ready'
        write_json(path, value)
        with pytest.raises(ValueError, match='exact original normal'):
            m.resolve_plan(reference)
    finally:
        path.write_bytes(saved)


def test_calendar_cannot_skip_an_earlier_positive_candidate(reference):
    m = api()
    plan = m.resolve_plan(reference)
    chosen = plan['fault_intervals'][0]
    chosen['start_minute'] += 1440.
    chosen['end_minute'] += 1440.
    plan['plan_sha256'] = m.plan_digest(plan)
    with pytest.raises(ValueError, match='Chosen anchor'):
        m.validate_plan(plan, require_frozen=True)


def test_numeric_one_is_not_unchanged_source_boolean_receipt(reference):
    m = api()
    path = reference/'RESULT.json'
    saved = path.read_bytes()
    try:
        value = json.loads(saved)
        value['source_and_inputs_unchanged'] = 1
        write_json(path, value)
        with pytest.raises(ValueError):
            m.resolve_plan(reference)
    finally:
        path.write_bytes(saved)


def test_zero_production_in_all_registered_candidates_stops_resolution(reference, monkeypatch):
    m = api()
    original = m._Reference.window
    def zero(self, anchor, device):
        value = original(self, anchor, device)
        value['actual_integral'] = 0.
        return value
    monkeypatch.setattr(m._Reference, 'window', zero)
    with pytest.raises(ValueError, match='calendar remains unresolved'):
        m.resolve_plan(reference)


def test_calendar_freeze_cannot_write_inside_original_reference(tmp_path, monkeypatch):
    import freeze_calendar
    reference = tmp_path/'original_normal_producer'
    reference.mkdir()
    target = reference/'new_calendar'
    calls = []
    def resolver(path):
        calls.append(path)
        return {'reference_proof':{}, 'status':'frozen'}
    monkeypatch.setattr(freeze_calendar, 'resolve_plan', resolver)
    with pytest.raises(ValueError, match='reference'):
        freeze_calendar.freeze(reference, target)
    assert calls == []
    assert not target.exists()


def test_calendar_freeze_output_cannot_contain_reference(tmp_path, monkeypatch):
    import freeze_calendar
    target = tmp_path/'future_calendar'
    reference = target/'reference'
    calls = []
    monkeypatch.setattr(freeze_calendar, 'resolve_plan', lambda path:calls.append(path))
    with pytest.raises(ValueError, match='reference'):
        freeze_calendar.freeze(reference, target)
    assert calls == []
    assert not target.exists()
