"""Isolated reserve policy diagnostic registration and saved-raw checks."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

MANIFEST = ROOT / "outputs/v5_policy_search_20261015_run01/new_data/global_manifest.json"
EXPECTED = (
    "control_case_aff69c7a098637665534",  # EC0 p4: old secure P0 failure.
    "control_case_ce85057bf2097d63dc44",  # GT50 p1: old GT-up counterexample.
    "control_case_2e9cbda5558ba9339642",  # normal p2: side-effect control.
)


def test_reserve_registration_is_exact_three_old_train_cases_and_signed(tmp_path):
    from tools.policy_redesign import reserve_diagnostic as diagnostic

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    lookup = {scene["id"]: scene for scene in manifest["scenarios"]}
    assert [(lookup[sid]["condition_id"], lookup[sid]["pressure_point_index"])
            for sid in EXPECTED] == [
                ("EC0_grid350_delay30", 4),
                ("GT50_grid350_delay30", 1),
                ("normal_grid850_delay15", 2),
            ]
    plan_path = tmp_path / "reserve_plan.json"
    plan = diagnostic.register_diagnostic(plan_path)
    assert tuple(plan["scenario_ids"]) == EXPECTED
    assert plan["registered_pair_count"] == 6
    assert plan["policy_specs"] == [
        {"policy_id": "secure_milp", "kind": "secure"},
        {"policy_id": "reserve_gt3", "kind": "reserve_feedback", "theta": [1.0]},
    ]
    assert plan["phase"] == "train"
    assert plan["final_blind_opened"] is False
    assert "tools/policy_redesign/reserve_policy.py" in plan["source_sha256"]
    assert "tools/policy_redesign/reserve_diagnostic.py" in plan["source_sha256"]
    assert diagnostic.verify_diagnostic(plan_path)[0] == plan

    edited = json.loads(plan_path.read_text(encoding="utf-8"))
    edited["scenario_ids"].reverse()
    plan_path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(ValueError, match="signature"):
        diagnostic.verify_diagnostic(plan_path)


def test_reserve_registration_rejects_any_scenario_substitution(tmp_path):
    from tools.policy_redesign import reserve_diagnostic as diagnostic

    with pytest.raises(ValueError, match="exact three"):
        diagnostic.register_diagnostic(tmp_path / "substituted.json",
                                       scenario_ids=EXPECTED[:2])
    assert not (tmp_path / "substituted.json").exists()


def _synthetic_payload(scene, policy, manifest, *, unknown=False):
    sid = scene["id"]
    return {
        "identity": {
            "scenario_id": sid, "policy_id": policy.policy_id,
            "manifest_signature": manifest["signature"],
            "simulator_hash": manifest["simulator_hash"],
            "execution_contract_id": manifest["execution_contract_id"],
            "group_id": manifest["splits"][sid]["group_id"],
            "exogenous_stream_id": scene["exogenous_stream_id"],
        },
        "scenario": scene, "seed": manifest["options"]["seed"],
        "initial_state": {"same_scenario": sid},
        "summary": {"status": "model_constraint_infeasible" if unknown else "completed",
                    "observed_until_minute": 279.0 if unknown else 600.0},
        "events": [], "decisions": [],
    }


def test_reserve_runner_and_audit_recompute_saved_raw_and_preserve_unknown(
        tmp_path, monkeypatch):
    from tools.policy_redesign import reserve_diagnostic as diagnostic

    plan_path = tmp_path / "reserve_plan.json"
    diagnostic.register_diagnostic(plan_path)
    monkeypatch.setattr(diagnostic, "HospitalIESEnv", lambda *a, **k: object())
    monkeypatch.setattr(diagnostic, "_factory", lambda spec: SimpleNamespace(
        policy_id=spec["policy_id"]))
    monkeypatch.setattr(diagnostic, "run_policy_episode",
                        lambda env, scene, policy, seed, manifest, **options:
                        _synthetic_payload(scene, policy, manifest,
                                           unknown=scene["id"] == EXPECTED[1] and
                                           policy.policy_id == "reserve_gt3"))

    def score(payload, start, horizon, env):
        complete = payload["summary"]["status"] == "completed"
        return {"start_minute": start, "horizon_minutes": horizon,
                "full_horizon": complete,
                "targets": [1.0] + [0.0] * 18 if complete else [None] * 19}

    monkeypatch.setattr(diagnostic, "window_targets", score)
    results = tmp_path / "results"
    summary = diagnostic.run_diagnostic(plan_path, results)
    assert summary["registered_pair_count"] == 6
    assert summary["terminal_pair_count"] == 6
    assert summary["status_counts"] == {"completed": 5,
                                         "model_constraint_infeasible": 1}
    assert summary["matched_initial_state_and_stream"] is True
    report = diagnostic.audit_diagnostic(plan_path, results)
    assert report["passed"] is True
    assert report["recomputed_pair_count"] == 6
    assert report["unknown_primary_pair_ids"] == [EXPECTED[1] + "/reserve_gt3"]
    assert report["paired_primary"]["complete_paired_case_count"] == 2

    raw = results / "pairs" / diagnostic.pair_id(EXPECTED[0], "reserve_gt3") / "attempt_0001" / "trajectory.json.gz"
    with raw.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="raw SHA"):
        diagnostic.audit_diagnostic(plan_path, results)


def test_reserve_runner_exception_has_error_receipt_and_no_fabricated_score(
        tmp_path, monkeypatch):
    from tools.policy_redesign import reserve_diagnostic as diagnostic

    plan_path = tmp_path / "reserve_plan.json"
    diagnostic.register_diagnostic(plan_path)
    monkeypatch.setattr(diagnostic, "HospitalIESEnv", lambda *a, **k: object())
    monkeypatch.setattr(diagnostic, "_factory", lambda spec: SimpleNamespace(
        policy_id=spec["policy_id"]))

    def fail(*args, **kwargs):
        raise RuntimeError("diagnostic fixture failure")

    monkeypatch.setattr(diagnostic, "run_policy_episode", fail)
    results = tmp_path / "results"
    summary = diagnostic.run_diagnostic(plan_path, results, max_new_pairs=1)
    assert summary["terminal_pair_count"] == 0
    receipt_dir = results / "pairs" / diagnostic.pair_id(EXPECTED[0], "secure_milp") / "attempt_0001"
    row = json.loads((receipt_dir / "result.json").read_text(encoding="utf-8"))
    error = json.loads((receipt_dir / "error.json").read_text(encoding="utf-8"))
    assert row["status"] == "runner_exception" and row["terminal"] is False
    assert row["primary_score"]["targets"] == [None] * 19
    assert error["error_type"] == "RuntimeError"
    assert not (receipt_dir / "trajectory.json.gz").exists()
