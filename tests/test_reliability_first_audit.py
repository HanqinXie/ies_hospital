"""Read-only v4 audit contract: missing work is incomplete, never passed."""
from __future__ import annotations

import hashlib
import importlib
import json


def audit():
    return importlib.import_module("tools.reliability_first.audit")


def test_snapshot_group_mismatch_is_failure():
    manifest = {"scenarios": [
        {"id": "case_train", "root_family_id": "root_a", "experiment_role": "pilot_train"},
        {"id": "case_dev", "root_family_id": "root_b", "experiment_role": "development_boundary"}],
        "splits": {"case_train": {"group_id": "group_a", "experiment_role": "pilot_train"},
                   "case_dev": {"group_id": "group_b", "experiment_role": "development_boundary"}}}
    plan = [{"scenario_id": "case_train", "root_family_id": "root_a",
             "experiment_role": "pilot_train", "group_id": "root_a", "snapshot_minute": 120,
             "conditional_futures": 4, "selection_uses_future_event_clock": False}]
    result = audit().audit_snapshot_groups(manifest, plan)
    assert result["status"] == "failed"
    assert any("group_id" in issue for issue in result["issues"])


def test_started_failed_and_unfinished_baseline_attempts_stay_in_denominator(tmp_path):
    manifest = {"signature": "sig", "scenarios": [
        {"id": "a", "root_family_id": "r1", "experiment_role": "pilot_train"},
        {"id": "b", "root_family_id": "r2", "experiment_role": "pilot_validation"}],
        "policies": ["priority_milp", "secure_milp"]}
    path = tmp_path / "attempts.jsonl"
    events = [
        {"event": "started", "scenario_id": "a", "policy_id": "priority_milp", "manifest_signature": "sig"},
        {"event": "finished", "scenario_id": "a", "policy_id": "priority_milp", "manifest_signature": "sig", "status": "completed"},
        {"event": "started", "scenario_id": "a", "policy_id": "secure_milp", "manifest_signature": "sig"},
        {"event": "finished", "scenario_id": "a", "policy_id": "secure_milp", "manifest_signature": "sig", "status": "model_constraint_infeasible"},
        {"event": "started", "scenario_id": "b", "policy_id": "priority_milp", "manifest_signature": "sig"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
    result = audit().audit_baseline_attempts(manifest, [path])
    assert result["status"] == "incomplete"
    assert result["started_attempts"] == 3
    assert result["finished_attempts"] == 2
    assert result["unfinished_attempts"] == 1
    assert result["statuses"]["model_constraint_infeasible"] == 1
    assert result["fully_completed_pairs"] == 0
    assert result["registered_pairs"] == 2


def test_baseline_reconciled_and_interrupted_unknown_are_terminal_not_success(tmp_path):
    manifest = {"signature": "sig", "scenarios": [{"id": "a"}],
                "policies": ["priority_milp", "secure_milp"]}
    path = tmp_path / "attempts.jsonl"
    events = [
        {"event": "started", "scenario_id": "a", "policy_id": "priority_milp", "manifest_signature": "sig"},
        {"event": "adjudicated_interrupted_unknown", "scenario_id": "a", "policy_id": "priority_milp",
         "manifest_signature": "sig", "status": "interrupted_unknown"},
        {"event": "started", "scenario_id": "a", "policy_id": "secure_milp", "manifest_signature": "sig"},
        {"event": "reconciled_checkpoint", "scenario_id": "a", "policy_id": "secure_milp",
         "manifest_signature": "sig", "status": "completed"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")
    result = audit().audit_baseline_attempts(manifest, [path])
    assert result["status"] == "passed"
    assert result["started_attempts"] == result["finished_attempts"] == 2
    assert result["statuses"]["interrupted_unknown"] == 1
    assert result["fully_completed_pairs"] == 0


def test_budget_counts_retries_failures_and_unknown_starts():
    counts = {"new_specifications": 300, "baseline_started": 601,
              "snapshots_started": 2, "action_future_started": 4,
              "other_full_started": 3, "method_classes": 2,
              "configurations": 3, "fit_started": 1, "aggregation_rounds": 0}
    result = audit().audit_budget(counts)
    assert result["status"] == "passed"
    assert result["observed"]["baseline_started"] == 601
    counts["action_future_started"] = 24001
    assert audit().audit_budget(counts)["status"] == "failed"


def test_fit_instance_accounting_charges_tree_targets_and_unfinished_starts(tmp_path):
    root = tmp_path / "learning"
    for name, start, report in (
        ("pilot_tree", {"model": "per-target ExtraTreesRegressor", "device": "cpu"}, {"actual_fits": 19}),
        ("pilot_nn", {"requested_device": "cuda:0"}, {"actual_fits": 1}),
        ("interrupted_tree", {"model": "per-target ExtraTreesRegressor", "device": "cpu"}, None)):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "fit_started.json").write_text(json.dumps(start), encoding="utf-8")
        if report is not None:
            (folder / "FIT_REPORT.json").write_text(json.dumps(report), encoding="utf-8")
    result = audit().fit_instance_evidence(root)
    assert result["invocation_starts"] == 3
    assert result["reported_actual_instances"] == 20
    assert result["conservative_unreported_reservation"] == 19
    assert result["charged_instances"] == 39


def test_cuda_fit_report_needs_real_updates_and_matching_artifact_hashes(tmp_path):
    folder = tmp_path / "fit01"
    folder.mkdir()
    (folder / "fit_started.json").write_text("{}", encoding="utf-8")
    (folder / "model.pt").write_bytes(b"weights")
    report = {"actual_fits": 1, "train_rows": 16, "validation_rows": 8,
              "optimizer_updates": 4, "epoch_losses": [{"epoch": 1}],
              "initial_weight_sha256": "a", "trained_weight_sha256": "b",
              "actual_train_device": "cuda:0", "actual_validation_device": "cuda:0",
              "peak_cuda_allocated_bytes": 2048, "reload_max_abs_difference": 0.0,
              "source_binding": {"manifest_signature": "sig"}}
    metadata = {"schema": "reliability_first.gpu_paired_action_value.v1",
                "actual_train_device": "cuda:0", "actual_validation_device": "cuda:0",
                "source_binding": report["source_binding"],
                "artifacts": {"model.pt": hashlib.sha256(b"weights").hexdigest()}}
    metadata_bytes = json.dumps(metadata).encode("utf-8")
    (folder / "metadata.json").write_bytes(metadata_bytes)
    report["metadata_sha256"] = hashlib.sha256(metadata_bytes).hexdigest()
    (folder / "FIT_REPORT.json").write_text(json.dumps(report), encoding="utf-8")
    good = audit().audit_cuda_fits(tmp_path, expected_manifest_signature="sig")
    assert good["status"] == "passed" and good["successful_new_cuda_fits"] == 1
    report["source_binding"] = {"manifest_sha256": "abc"}
    metadata["source_binding"] = report["source_binding"]
    metadata_bytes = json.dumps(metadata).encode("utf-8")
    (folder / "metadata.json").write_bytes(metadata_bytes)
    report["metadata_sha256"] = hashlib.sha256(metadata_bytes).hexdigest()
    (folder / "FIT_REPORT.json").write_text(json.dumps(report), encoding="utf-8")
    assert audit().audit_cuda_fits(tmp_path, expected_manifest_signature="sig",
                                   expected_manifest_sha256="abc")["status"] == "passed"
    assert audit().audit_cuda_fits(tmp_path, expected_manifest_signature="sig",
                                   expected_manifest_sha256="wrong")["status"] == "failed"
    report["optimizer_updates"] = 0
    (folder / "FIT_REPORT.json").write_text(json.dumps(report), encoding="utf-8")
    assert audit().audit_cuda_fits(tmp_path, expected_manifest_signature="sig")["status"] == "failed"


def test_protected_file_hash_mismatch_fails(tmp_path):
    original = tmp_path / "inputs" / "original.csv"
    original.parent.mkdir()
    original.write_bytes(b"changed")
    reference = {"sections": {"input_and_source_integrity": {"original_file_checks": [
        {"path": "inputs/original.csv", "expected_sha256": hashlib.sha256(b"old").hexdigest()}]}}}
    result = audit().audit_protected_hashes(tmp_path, reference)
    assert result["status"] == "failed"
    assert result["mismatched_files"] == ["inputs/original.csv"]


def test_missing_nn_closed_loop_and_package_are_incomplete(tmp_path):
    assert audit().audit_cuda_fits(tmp_path, expected_manifest_signature="sig")["status"] == "incomplete"
    assert audit().audit_review_package(tmp_path, None, None)["status"] == "incomplete"
    assert audit().audit_development_pairing([], required_policy_ids=("secure_milp", "rule", "nn"))["status"] == "incomplete"


def test_branch_attempts_include_reference_d0_retries_and_unfinished(tmp_path):
    branches = tmp_path / "branches"
    attempt = branches / "snapshot_000" / "attempt_0001"
    attempt.mkdir(parents=True)
    (branches / "collection_attempts.jsonl").write_text(json.dumps({
        "event": "started", "index": 0, "attempt_directory": str(attempt)}) + "\n", encoding="utf-8")
    events = [
        {"event": "started", "attempt_type": "snapshot_capture", "status": "running"},
        {"event": "finished", "attempt_type": "snapshot_capture", "status": "completed"},
        {"event": "started", "attempt_type": "D0_secure"},
        {"event": "finished", "attempt_type": "D0_secure", "status": "completed"},
        {"event": "started", "attempt_type": "current_action_future_continuation"},
        {"event": "finished", "attempt_type": "current_action_future_continuation", "status": "completed"},
        {"event": "started", "attempt_type": "current_action_future_continuation"},
    ]
    (attempt / "attempts.jsonl").write_text("\n".join(json.dumps(x) for x in events), encoding="utf-8")
    result = audit().audit_branch_attempts(tmp_path)
    assert result["status"] == "incomplete"
    assert result["snapshots_started"] == 1
    assert result["action_future_started"] == 4
    assert result["unfinished_inner_attempts"] == 1
    assert result["unfinished_collection_attempts"] == 1


def test_closed_loop_binding_and_unknowns_stay_visible():
    rows = [{"scenario_id": "s", "policy_id": policy, "status": status, "terminal": True,
             "binding": {"initial_state_sha256": "initial", "exogenous_stream_id": "stream", "passed": True}}
            for policy, status in (("secure_milp", "completed"), ("rule", "model_constraint_infeasible"),
                                   ("nn", "completed"))]
    result = audit().audit_development_pairing(rows, required_policy_ids=("secure_milp", "rule", "nn"))
    assert result["status"] == "passed"
    assert result["statuses"]["model_constraint_infeasible"] == 1
    assert result["matched_scenarios"] == 1
    rows[-1]["binding"]["exogenous_stream_id"] = "other"
    assert audit().audit_development_pairing(rows, required_policy_ids=("secure_milp", "rule", "nn"))["status"] == "failed"
