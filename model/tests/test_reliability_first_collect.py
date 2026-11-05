import json
import gzip
import hashlib
import pytest

from tools.reliability_first.collect import _result_ok, plan_collection


def test_collection_uses_only_train_validation_groups_without_future_outcomes(tmp_path):
    plan = []
    for role, groups in (("pilot_train", 3), ("pilot_validation", 2),
                         ("development_boundary", 1)):
        for group in range(groups):
            for phase in range(4):
                plan.append({"scenario_id": f"{role}{group}", "group_id": f"{role}{group}",
                             "experiment_role": role, "snapshot_minute": phase * 15})
    path = tmp_path / "snapshot_plan_v2.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    receipt = plan_collection(path, early_train_groups=2, early_validation_groups=1)
    assert len(receipt["early_indices"]) == 12
    assert len(receipt["remaining_indices"]) == 8
    assert set(receipt["early_indices"]).isdisjoint(receipt["remaining_indices"])
    assert len(set(receipt["early_indices"] + receipt["remaining_indices"])) == 20
    assert all(plan[i]["experiment_role"] != "development_boundary"
               for i in receipt["early_indices"] + receipt["remaining_indices"])
    assert receipt["early_group_counts"] == {"pilot_train": 2, "pilot_validation": 1}


def test_legacy_unreached_requires_frozen_attempt_provenance_and_writes_sidecar(tmp_path):
    row = {"scenario_id": "scene_a", "group_id": "group_a",
           "experiment_role": "pilot_train", "snapshot_minute": 30.0}
    source = {"branches.py": "frozen_source"}
    attempt = tmp_path / "snapshot_000" / "attempt_0001"
    attempt.mkdir(parents=True)
    (attempt / "plan.json").write_text(json.dumps({"schema": "reliability_first.branch.v1",
         "entry": row, "manifest_signature": "manifest_1", "source_sha256": source}),
         encoding="utf-8")
    raw = attempt / "reference_capture.json.gz"
    with gzip.open(raw, "wt", encoding="utf-8") as stream:
        json.dump({"identity": {"scenario_id": "scene_a", "group_id": "group_a",
                                "manifest_signature": "manifest_1"},
                   "summary": {"status": "model_constraint_infeasible",
                               "observed_until_minute": 15.0}}, stream)
    raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    (attempt / "attempts.jsonl").write_text("\n".join([
        json.dumps({"event": "started", "attempt_type": "snapshot_capture",
                    "scenario_id": "scene_a", "minute": 30.0}),
        json.dumps({"event": "finished", "attempt_type": "snapshot_capture",
                    "status": "model_constraint_infeasible", "raw_sha256": raw_sha})
    ]) + "\n", encoding="utf-8")
    result_path = attempt / "BRANCH_RESULT.json"
    result_path.write_text(json.dumps({"status": "snapshot_not_reached",
        "scenario_id": "scene_a", "snapshot_minute": 30.0,
        "reference_status": "model_constraint_infeasible", "fit_rows": [], "unknown": True}),
        encoding="utf-8")
    ok, result = _result_ok(result_path, row, source, manifest_signature="manifest_1")
    assert ok is True
    assert result["schema"] == "reliability_first.branch_result.v1"
    assert result["group_id"] == "group_a" and result["role"] == "train"
    assert result["source_sha256"] == source
    assert result["legacy_provenance_verified"] is True
    sidecar = attempt / "TERMINAL_PROVENANCE.json"
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["reference_capture_sha256"] == raw_sha


def test_legacy_unreached_with_different_producer_is_not_reused(tmp_path):
    row = {"scenario_id": "scene_a", "group_id": "group_a",
           "experiment_role": "pilot_train", "snapshot_minute": 30.0}
    attempt = tmp_path / "attempt_0001"
    attempt.mkdir()
    (attempt / "plan.json").write_text(json.dumps({"schema": "reliability_first.branch.v1",
        "entry": row, "manifest_signature": "manifest_1",
        "source_sha256": {"branches.py": "different"}}), encoding="utf-8")
    result_path = attempt / "BRANCH_RESULT.json"
    result_path.write_text(json.dumps({"status": "snapshot_not_reached",
        "scenario_id": "scene_a", "snapshot_minute": 30.0,
        "reference_status": "model_constraint_infeasible", "fit_rows": [], "unknown": True}),
        encoding="utf-8")
    ok, _ = _result_ok(result_path, row, {"branches.py": "registered"},
                       manifest_signature="manifest_1")
    assert ok is False
    assert not (attempt / "TERMINAL_PROVENANCE.json").exists()


def test_complete_branch_requires_same_registered_manifest_plan(tmp_path):
    row = {"scenario_id": "scene_a", "group_id": "group_a",
           "experiment_role": "pilot_train", "snapshot_minute": 30.0}
    source = {"branches.py": "registered"}
    attempt = tmp_path / "attempt_0001"
    attempt.mkdir()
    (attempt / "plan.json").write_text(json.dumps({
        "schema": "reliability_first.branch.v1", "entry": row,
        "manifest_signature": "wrong_manifest", "source_sha256": source}), encoding="utf-8")
    path = attempt / "BRANCH_RESULT.json"
    path.write_text(json.dumps({"schema": "reliability_first.branch_result.v1",
        "status": "complete", "scenario_id": "scene_a", "group_id": "group_a",
        "role": "train", "snapshot_minute": 30.0, "D0_passed": True,
        "source_sha256": source}), encoding="utf-8")
    with pytest.raises(ValueError, match="plan"):
        _result_ok(path, row, source, manifest_signature="registered_manifest")
