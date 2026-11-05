"""A solver implementation change is a real simulation version change."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from hospital_ies.dataset_manifest import build_manifest


def test_backend_bytes_are_in_simulator_version(tmp_path):
    directory = tmp_path / 'src/hospital_ies'
    directory.mkdir(parents=True)
    source = directory / 'solver_backend.py'
    source.write_text('# backend version one\n', encoding='utf-8')
    args = (tmp_path, [], ['priority_milp', 'secure_milp'], {'name': 'test'}, {}, {}, {})
    before = build_manifest(*args)
    source.write_text('# backend version two\n', encoding='utf-8')
    after = build_manifest(*args)
    assert before['simulator_hash'] != after['simulator_hash']
    assert before['signature'] != after['signature']


def test_legacy_reopened_evidence_stays_revoked_after_source_replacement():
    from tools.p2c_experiment.replay import validate_gate
    import pytest
    root = Path(__file__).resolve().parents[1]
    path = root / 'outputs/v43_p2c_20261013_run01/solver_diagnostics/S_gate_v3.json'
    with pytest.raises(ValueError, match='S gate reopened'):
        validate_gate(path)


def test_canonical_comparison_excludes_remaining_solver_wallclock_only():
    from hospital_ies.dataset_manifest import canonical_payload
    a = {'attempts': [{'parameters': {'TimeLimit': 5.99, 'Presolve': 2},
                       'status': 0, 'raw_x': [225.]}], 'delay_minutes': .25}
    b = {'attempts': [{'parameters': {'TimeLimit': 5.98, 'Presolve': 2},
                       'status': 0, 'raw_x': [225.]}], 'delay_minutes': .25}
    assert canonical_payload(a) == canonical_payload(b)
    b['attempts'][0]['status'] = 2
    assert canonical_payload(a) != canonical_payload(b)
    b['attempts'][0]['status'] = 0
    b['attempts'][0]['raw_x'][0] = 224.
    assert canonical_payload(a) != canonical_payload(b)
