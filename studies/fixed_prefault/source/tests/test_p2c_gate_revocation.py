"""A local111 certificate cannot release a numerical source reopened by replay."""
import copy,json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'outputs/v43_p2c_20261013_run01/solver_diagnostics'

def test_real_reopened_v3_local_certificate_is_rejected():
    from tools.p2c_experiment.replay import validate_gate
    assert (D/'S_REOPENED_FINAL_FAILED.json').is_file()
    with pytest.raises(ValueError,match='S gate reopened'):
        validate_gate(D/'S_gate_v3.json')

def test_copying_local_certificate_does_not_escape_known_revocation(tmp_path):
    from tools.p2c_experiment.replay import validate_gate
    copied=tmp_path/'copied_local_certificate.json'
    copied.write_bytes((D/'S_gate_v3.json').read_bytes())
    with pytest.raises(ValueError,match='S gate reopened'):
        validate_gate(copied)

def test_revocation_does_not_claim_a_different_future_source_is_failed():
    from tools.p2c_experiment.replay import assert_not_revoked
    gate=json.loads((D/'S_gate_v3.json').read_text(encoding='utf-8'))
    gate=copy.deepcopy(gate)
    gate['source_sha256']['src/hospital_ies/resilience.py']='0'*64
    # Only tests the revocation match. This synthetic hash is not a release.
    assert_not_revoked(gate,D)

def test_matching_revocation_with_corrupt_raw_evidence_fails_closed(tmp_path):
    from tools.p2c_experiment.replay import assert_not_revoked
    gate=json.loads((D/'S_gate_v3.json').read_text(encoding='utf-8'))
    marker=json.loads((D/'S_REOPENED_FINAL_FAILED.json').read_text(encoding='utf-8'))
    marker['actual_new_failure_summary_sha256']='0'*64
    (tmp_path/'S_REOPENED_FINAL_FAILED.json').write_text(json.dumps(marker),encoding='utf-8')
    with pytest.raises(ValueError,match='revocation raw evidence changed'):
        assert_not_revoked(gate,tmp_path)
