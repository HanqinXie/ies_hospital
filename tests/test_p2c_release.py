"""Global release is separate from the 111-call numerical subgate."""
import json
from pathlib import Path
import pytest


def test_freeze_rejects_missing_global_release_before_local_numerical_gate(tmp_path):
    from tools.p2c_experiment.replay import freeze
    # This deliberately invalid numerical path must never be read first.
    with pytest.raises(ValueError, match='global.*release'):
        freeze(tmp_path/'new', tmp_path/'numerical_missing.json')
    assert not (tmp_path/'new').exists()


def test_failed_release_rejected_even_with_nominal_source_map(tmp_path):
    from tools.p2c_experiment.release import validate_release
    p = tmp_path/'failed.json'
    p.write_text(json.dumps(dict(passed=False,source_sha256={'x':'y'})))
    with pytest.raises(ValueError,match='not passed'):
        validate_release(p)


def test_archived_absolute_evidence_rebases_to_package_root(tmp_path):
    from tools.p2c_experiment.release import ROOT, resolve_evidence
    original = ROOT/'outputs/v43_p2c_20261013_run01/logs/01_baseline.command.json'
    assert resolve_evidence(str(original),root=tmp_path) == tmp_path/original.relative_to(ROOT)


def test_regression_command_must_write_the_bound_junit():
    from tools.p2c_experiment.release import validate_command_binding
    with pytest.raises(ValueError,match='JUnit'):
        validate_command_binding(dict(command=['python','-m','pytest','--junitxml=unrelated.xml']),Path('actual.xml'))


def test_independent_review_must_bind_current_numerical_sources():
    from tools.p2c_experiment.release import validate_review_binding
    source = {'src/hospital_ies/resilience.py':'current','src/hospital_ies/solver_diagnostics.py':'current'}
    with pytest.raises(ValueError,match='source'):
        validate_review_binding(dict(passed=True,source_sha256={**source,'src/hospital_ies/resilience.py':'stale'}),source)
