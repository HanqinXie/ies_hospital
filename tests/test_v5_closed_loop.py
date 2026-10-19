"""Registration and evidence preservation for continuing v5 policies."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hospital_ies.dataset_manifest import digest


def _manifest(tmp_path, *, final=False):
    scenario = {"id": "case_1", "experiment_role": "train", "is_final_blind": final}
    body = {"scenarios": [scenario], "splits": {"case_1": {"experiment_role": "train", "is_final_blind": final}},
            "options": {"seed": 5}, "simulator_hash": "physics", "execution_contract_id": "contract",
            "execution_contract": {"name": "fake"}}
    body["signature"] = digest(body)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_v5_registration_refuses_sealed_final(tmp_path):
    from tools.policy_search.closed_loop import register_batch

    manifest = _manifest(tmp_path, final=True)
    pref = tmp_path / "preference.json"
    pref.write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError, match="sealed"):
        register_batch(manifest, tmp_path / "plan.json", scenario_ids=["case_1"],
                       policy_specs=[{"policy_id": "secure_milp", "kind": "secure"}],
                       preference_path=pref, phase="train")


def test_v5_batch_binding_and_plan_tamper_are_rejected(tmp_path):
    from tools.policy_search.closed_loop import register_batch, verify_batch

    manifest = _manifest(tmp_path)
    pref = tmp_path / "preference.json"
    pref.write_text('{"clinical_standard":false}', encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    register_batch(manifest, plan_path, scenario_ids=["case_1"],
                   policy_specs=[{"policy_id": "secure_milp", "kind": "secure"}],
                   preference_path=pref, phase="train")
    assert verify_batch(plan_path)[0]["scenario_ids"] == ["case_1"]
    changed = json.loads(plan_path.read_text(encoding="utf-8"))
    changed["phase"] = "development"
    plan_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="signature"):
        verify_batch(plan_path)
