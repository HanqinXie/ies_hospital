"""The revised target gate explicitly covers the inherited C_ready failure."""
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))


def implementation():
    return importlib.import_module('scripts.server_validation')


def test_c_ready_gate_is_the_known_summer_counterexample_without_state_reset():
    module = implementation()
    build = getattr(module, 'c_ready_validation_case', None)
    assert callable(build), 'Target gate omits the actual inherited C_ready failure background'
    case = build()
    parameters = case['annual_program']['parameters']
    assert parameters['method'] == 'C_ready'
    assert parameters['configuration'] == 'H2'
    assert parameters['mode'] == 'FD'
    assert parameters['start_hour'] == 4137
    assert parameters['duration_minutes'] == 180.
    assert parameters['demand_amplitude'] == .25
    assert parameters['demand_start_minute'] == 30.25
    assert parameters['demand_duration_minutes'] == 115.
    assert parameters['calendar'] == [
        dict(time_minute=60.25, availability={'TOWER': .01}),
        dict(time_minute=90.25, availability={'TOWER': 1.}),
        dict(time_minute=95.25, availability={'TOWER': .01}),
        dict(time_minute=140.25, availability={'TOWER': 1.}),
    ]


def test_old_seven_producer_gate_cannot_claim_c_ready_coverage(tmp_path):
    module = implementation()
    old = dict(status='passed', kind='busy_year_target_scientific_gate',
        producer_concurrency=1, actual_producer_invocations=7, accepted_result_cache=False,
        artifacts=[])
    with pytest.raises(ValueError, match='incomplete'):
        module.verify_gate(PACKAGE, tmp_path, old)


def test_c_ready_case_worker_does_not_substitute_the_m_ready_case(tmp_path, monkeypatch):
    module = implementation()
    worker = getattr(module, 'c_ready_case_worker', None)
    assert callable(worker), 'C_ready must have an independently bound case-builder command'
    monkeypatch.setattr(module, '_require_target', lambda: None)
    expected = {'case_identity': 'C_ready_only'}
    monkeypatch.setattr(module, 'c_ready_validation_case', lambda: expected)
    output = tmp_path / 'case.json'
    worker(output)
    assert json.loads(output.read_text('utf8')) == expected


def test_gate_rejects_a_changed_c_ready_case_before_accepting_scientific_results(tmp_path, monkeypatch):
    module = implementation()
    work = tmp_path / 'work'
    folder = work / 'validation/scientific'
    folder.mkdir(parents=True)
    case = {'case_identity': 'M_ready'}
    c_ready = {'case_identity': 'C_ready'}
    paths = []
    for name, value in [('CASE.json', case), ('C_READY_CASE.json', dict(c_ready, changed=True))]:
        path = folder / name
        path.write_text(json.dumps(value), 'utf8')
        paths.append(path)
    monkeypatch.setattr(module, 'validation_case', lambda: case)
    monkeypatch.setattr(module, 'c_ready_validation_case', lambda: c_ready, raising=False)
    monkeypatch.setattr(module, 'verify_package', lambda _: {'source_tree_sha256': 'a' * 64})
    gate = dict(execution_profile=module.execution_contract(),status='passed', kind='busy_year_target_scientific_gate', producer_concurrency=1,
        actual_producer_invocations=12, accepted_result_cache=False,
        deployment_checks=dict(license='skipped_by_user_request', environment_dependencies='skipped_by_user_request'),
        artifacts=[dict(path=path.relative_to(work).as_posix(), sha256=module.sha256(path)) for path in paths])
    with pytest.raises(ValueError, match='C_ready case'):
        module.verify_gate(PACKAGE, work, gate)


def test_c_ready_producer_is_counted_by_the_same_concurrency_guard(tmp_path):
    module = implementation()
    runs = module.Runs(PACKAGE / 'source', tmp_path, 10., 10_000_000, 1, 1)
    owner = SimpleNamespace(refresh=lambda: [], proc=SimpleNamespace(poll=lambda: None))
    runs.jobs.append(dict(label='c_ready_full', owned=owner))
    runs.guard()
    assert runs.peak_active_producers == 1, 'C_ready cannot bypass the existing single-producer count'
