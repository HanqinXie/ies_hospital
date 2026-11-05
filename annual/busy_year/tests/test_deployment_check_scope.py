"""User-requested skip covers deployment probes, never scientific evidence."""
import importlib
import json
from pathlib import Path
import sys

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))

CHECK_SCOPE = {
    'license': 'skipped_by_user_request',
    'environment_dependencies': 'skipped_by_user_request',
}


class ScientificStageReached(RuntimeError):
    pass


def implementation():
    return importlib.import_module('scripts.server_validation')


def test_default_validation_skips_deployment_probes_and_preserves_science(monkeypatch, tmp_path):
    module = implementation()
    observed = []

    class BoundedRuns:
        def __init__(self, *args):
            pass

        def command(self, label, command, **kwargs):
            observed.append((label, command))
            if label == 'fault_full':
                raise ScientificStageReached('registered scientific stage')

        def close(self):
            pass

    monkeypatch.setattr(module, '_require_target', lambda: None)
    monkeypatch.setattr(module, 'Runs', BoundedRuns)
    with pytest.raises(ScientificStageReached):
        module.run_validation(PACKAGE, tmp_path / 'work', {'identity': 'toy_scope_only'})
    assert [label for label, _ in observed] == ['default_suite','case_builder', 'fault_full']
    registration = json.loads((tmp_path / 'work/validation/REGISTRATION.json').read_text('utf8'))
    assert registration['deployment_checks'] == CHECK_SCOPE
    assert registration['actual_producer_invocations'] == 12
    assert registration['unknown_is_failure'] is True
    failure = json.loads((tmp_path / 'work/validation/FAILURE.json').read_text('utf8'))
    assert failure['status'] == 'failed'


def gate_fixture(module, tmp_path, scope):
    work = tmp_path / 'work'
    folder = work / 'validation/scientific'
    folder.mkdir(parents=True)
    case = {'registered_case': 'not_a_real_model'}
    path = folder / 'CASE.json'
    path.write_text(json.dumps(case), 'utf8')
    c_path = folder / 'C_READY_CASE.json'
    c_path.write_text(json.dumps(case), 'utf8')
    gate = dict(execution_profile=module.execution_contract(),status='passed', kind='busy_year_target_scientific_gate',
        producer_concurrency=1, actual_producer_invocations=12, accepted_result_cache=False,
        deployment_checks=scope,
        artifacts=[dict(path=item.relative_to(work).as_posix(), sha256=module.sha256(item)) for item in (path, c_path)])
    return work, case, gate


def test_scientific_gate_does_not_require_a_license_artifact(monkeypatch, tmp_path):
    module = implementation()
    work, case, gate = gate_fixture(module, tmp_path, CHECK_SCOPE)
    monkeypatch.setattr(module, 'validation_case', lambda: case)
    monkeypatch.setattr(module, 'c_ready_validation_case', lambda: case)
    monkeypatch.setattr(module, 'verify_package', lambda _: {'source_tree_sha256': 'a' * 64})

    def reach_scientific_evidence(*args):
        raise ScientificStageReached('producer evidence remains required')

    monkeypatch.setattr(module, '_completed', reach_scientific_evidence)
    with pytest.raises(ScientificStageReached):
        module.verify_gate(PACKAGE, work, gate)
    assert not (work / 'validation/scientific/LICENSE.json').exists()


@pytest.mark.parametrize('scope', [None, {},
    dict(CHECK_SCOPE, license='passed'), dict(CHECK_SCOPE, environment_dependencies='passed')])
def test_gate_never_claims_skipped_deployment_checks_passed(tmp_path, scope):
    module = implementation()
    work, _, gate = gate_fixture(module, tmp_path, scope)
    with pytest.raises(ValueError, match='deployment check scope'):
        module.verify_gate(PACKAGE, work, gate)
