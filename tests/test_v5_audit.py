"""Independent v5 evidence audit; no IES trajectories are simulated here."""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hospital_ies.dataset_manifest import digest
from tools.policy_search.closed_loop import register_batch


ROOT = Path(__file__).resolve().parents[1]
SOURCE_NAMES = ("tools/policy_search/search.py", "tools/policy_search/preference.py",
                "tools/policy_search/closed_loop.py", "tools/policy_search/policy.py")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _score(start, horizon, *, full=True, observed=None):
    targets = [0.0] * 19 if full else [1.0] + [None] * 18
    return {"start_minute": float(start), "horizon_minutes": float(horizon),
            "observed_minutes": float(horizon if full else observed),
            "full_horizon": full, "unknown_after_window_prefix": not full,
            "targets": targets, "target_mask": [v is not None for v in targets]}


def _fixture(tmp_path, *, incomplete=False, changed_initial=False):
    scene = {"id": "case_1", "experiment_role": "train", "is_final_blind": False,
             "exogenous_stream_id": "stream_1"}
    source = ROOT / "src/hospital_ies/control_env.py"
    files = [{"path": "src/hospital_ies/control_env.py", "bytes": source.stat().st_size,
              "sha256": _sha(source)}]
    contract = {"name": "fixture_contract"}
    body = {"scenarios": [scene],
            "splits": {"case_1": {"experiment_role": "train", "is_final_blind": False,
                                  "group_id": "group_1"}},
            "options": {"seed": 5}, "files": files, "simulator_hash": digest(files),
            "execution_contract_id": "fixture_contract:" + digest(contract),
            "execution_contract": contract}
    body["signature"] = digest(body)
    manifest = tmp_path / "manifest.json"
    _write(manifest, body)
    preference = tmp_path / "preference.json"
    _write(preference, {"clinical_standard": False})
    folder = tmp_path / "search/generation_1"
    plan_path = folder / "registered_plan.json"
    specs = [{"policy_id": "secure_milp", "kind": "secure"},
             {"policy_id": "current_rule", "kind": "current_rule"}]
    plan = register_batch(manifest, plan_path, scenario_ids=["case_1"],
                          policy_specs=specs, preference_path=preference, phase="train")
    registration = folder / "SEARCH_REGISTRATION.json"
    _write(registration, {"schema": "policy_search.offline_generation.v1",
                          "generation": 1, "source_sha256": _sha(ROOT / SOURCE_NAMES[0]),
                          "manifest_sha256": _sha(manifest),
                          "preference_sha256": _sha(preference),
                          "scenario_ids": plan["scenario_ids"],
                          "policy_specs": plan["policy_specs"],
                          "plan_signature": plan["signature"]})
    freeze = tmp_path / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"
    _write(freeze, {"schema": "policy_search.algorithm_source_freeze.v1",
                    "source_sha256": {name: _sha(ROOT / name) for name in SOURCE_NAMES},
                    "preference_sha256": _sha(preference),
                    "v5_new_manifest_sha256": _sha(manifest)})
    result_dir = folder / "results"
    rows = []
    for spec in specs:
        pid = spec["policy_id"]
        is_unknown = incomplete and pid == "current_rule"
        initial_state = {"x": 2 if changed_initial and pid == "current_rule" else 1}
        identity = {"scenario_id": scene["id"], "manifest_signature": body["signature"],
                    "simulator_hash": body["simulator_hash"],
                    "execution_contract_id": body["execution_contract_id"],
                    "group_id": "group_1", "exogenous_stream_id": "stream_1"}
        status = "model_constraint_infeasible" if is_unknown else "completed"
        payload = {"identity": identity, "scenario": scene, "seed": 5,
                   "initial_state": initial_state,
                   "summary": {"status": status,
                               "observed_until_minute": 260.0 if is_unknown else 600.0}}
        attempt = result_dir / "pairs" / digest([scene["id"], pid]) / "attempt_0001"
        attempt.mkdir(parents=True)
        raw = attempt / "trajectory.json.gz"
        with gzip.open(raw, "wt", encoding="utf-8") as stream:
            json.dump(payload, stream)
        binding = {"passed": True,
                   "checks": {key: True for key in ("scenario_id", "manifest_signature",
                           "simulator_hash", "execution_contract_id", "group_id",
                           "scenario_equal", "seed_equal", "initial_state_present")},
                   "initial_state_sha256": digest(initial_state),
                   "exogenous_stream_id": "stream_1"}
        row = {"schema": "policy_search.matched_full_feedback.v1.pair",
               "scenario_id": scene["id"], "policy_id": pid, "kind": spec["kind"],
               "phase": "train", "plan_signature": plan["signature"], "status": status,
               "terminal": True, "trajectory_path": raw.name, "trajectory_sha256": _sha(raw),
               "observed_until_minute": payload["summary"]["observed_until_minute"],
               "primary_score": _score(60, 360, full=not is_unknown, observed=200),
               "full_observation": _score(0, 600, full=not is_unknown, observed=260),
               "binding": binding}
        _write(attempt / "result.json", row)
        rows.append(row)
    summary = result_dir / "summary_0001.json"
    _write(summary, {"schema": "policy_search.matched_full_feedback.v1.summary",
                     "plan_signature": plan["signature"], "registered_pair_count": 2,
                     "recorded_pair_count": 2, "terminal_pair_count": 2,
                     "matched_initial_state_and_stream": not changed_initial,
                     "unknown_primary_pair_count": int(incomplete), "results": rows})
    return plan_path, summary, freeze, folder / "AUDIT.json", registration


def test_valid_registered_batch_yields_immutable_passing_receipt(tmp_path):
    from tools.policy_search.audit import audit_batch

    args = _fixture(tmp_path)
    receipt = audit_batch(*args[:4], registration_path=args[4])
    assert receipt["passed"] is True
    assert receipt["registered_pair_count"] == 2
    assert receipt["unknown_primary_pair_ids"] == []
    assert args[3].is_file()


def test_raw_trajectory_hash_mismatch_fails_audit(tmp_path):
    from tools.policy_search.audit import audit_batch

    args = _fixture(tmp_path)
    raw = next((args[1].parent / "pairs").rglob("trajectory.json.gz"))
    raw.write_bytes(raw.read_bytes() + b"tampered")
    receipt = audit_batch(*args[:4], registration_path=args[4])
    assert receipt["passed"] is False
    assert any("raw_sha256" in error for error in receipt["errors"])


def test_different_initial_state_fails_even_when_each_row_binding_passes(tmp_path):
    from tools.policy_search.audit import audit_batch

    args = _fixture(tmp_path, changed_initial=True)
    receipt = audit_batch(*args[:4], registration_path=args[4])
    assert receipt["passed"] is False
    assert any("matched_initial_state" in error for error in receipt["errors"])


def test_truncated_unknown_masks_are_preserved_and_counted(tmp_path):
    from tools.policy_search.audit import audit_batch

    args = _fixture(tmp_path, incomplete=True)
    receipt = audit_batch(*args[:4], registration_path=args[4])
    assert receipt["passed"] is True
    assert receipt["unknown_primary_pair_ids"] == ["case_1/current_rule"]
    assert receipt["target_observed_count_by_window"]["primary_score"] == [2] + [1] * 18


def test_fabricated_nonnull_future_of_truncated_result_fails(tmp_path):
    from tools.policy_search.audit import audit_batch

    args = _fixture(tmp_path, incomplete=True)
    summary = json.loads(args[1].read_text(encoding="utf-8"))
    candidate = next(row for row in summary["results"] if row["policy_id"] == "current_rule")
    candidate["primary_score"]["targets"][1] = 0.0
    candidate["primary_score"]["target_mask"][1] = True
    _write(args[1], summary)
    for path in (args[1].parent / "pairs").rglob("result.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row["policy_id"] == "current_rule":
            _write(path, candidate)
    receipt = audit_batch(*args[:4], registration_path=args[4])
    assert receipt["passed"] is False
    assert any("unknown_mask" in error for error in receipt["errors"])


def test_source_freeze_mismatch_fails_before_scoring(tmp_path):
    from tools.policy_search.audit import audit_batch

    args = _fixture(tmp_path)
    freeze = json.loads(args[2].read_text(encoding="utf-8"))
    freeze["source_sha256"]["tools/policy_search/preference.py"] = "0" * 64
    _write(args[2], freeze)
    receipt = audit_batch(*args[:4], registration_path=args[4])
    assert receipt["passed"] is False
    assert any("source_freeze" in error for error in receipt["errors"])


def test_summary_with_missing_registered_pair_fails(tmp_path):
    from tools.policy_search.audit import audit_batch

    args = _fixture(tmp_path)
    summary = json.loads(args[1].read_text(encoding="utf-8"))
    summary["results"] = summary["results"][:1]
    _write(args[1], summary)
    receipt = audit_batch(*args[:4], registration_path=args[4])
    assert receipt["passed"] is False
    assert any("pair_set" in error for error in receipt["errors"])
