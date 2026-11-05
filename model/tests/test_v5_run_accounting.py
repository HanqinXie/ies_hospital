"""Focused read-only accounting regressions for registered v5 runs."""

import hashlib
import json

import pytest

from tools.policy_search.run_accounting import (
    _commands, _fits, _known_window, collect_accounting, collect_batch,
    write_final_accounting,
)


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _pair_path(batch, scenario_id):
    key = json.dumps([scenario_id, "secure_milp"], sort_keys=True,
                     ensure_ascii=True, separators=(",", ":"))
    return batch / "results/pairs" / hashlib.sha256(key.encode()).hexdigest()


def _batch(tmp_path, scenario_ids=("case_a", "case_b")):
    batch = tmp_path / "search/generation_test"
    plan = {"schema": "policy_search.matched_full_feedback.v1",
            "signature": "registered-test-signature", "phase": "train",
            "scenario_ids": list(scenario_ids),
            "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"}],
            "registered_pair_count": len(scenario_ids),
            "final_blind_opened": False}
    _json(batch / "registered_plan.json", plan)
    return batch


def _result(batch, scenario_id, *, status, full):
    attempt = _pair_path(batch, scenario_id) / "attempt_0001"
    targets = [0.0] * 19 if full else [0.0] + [None] * 18
    score = {"full_horizon": full, "targets": targets}
    _json(attempt / "result.json",
          {"scenario_id": scenario_id, "policy_id": "secure_milp",
           "plan_signature": "registered-test-signature", "terminal": True,
           "status": status, "primary_score": score,
           "full_observation": score})


def test_terminal_truncation_remains_unknown_not_completed(tmp_path):
    batch = _batch(tmp_path)
    _result(batch, "case_a", status="completed", full=True)
    _result(batch, "case_b", status="model_constraint_infeasible", full=False)
    _json(batch / "results/summary_0001.json",
          {"plan_signature": "registered-test-signature",
           "registered_pair_count": 2, "recorded_pair_count": 2,
           "terminal_pair_count": 2, "status_counts":
           {"completed": 1, "model_constraint_infeasible": 1}})

    counted = collect_batch(tmp_path, batch / "registered_plan.json")

    assert counted["registered_pairs"] == 2
    assert counted["attempts"] == 2
    assert counted["terminal_pairs"] == 2
    assert counted["completed_pairs"] == 1
    assert counted["unknown_primary_pairs"] == 1
    assert counted["unknown_full_pairs"] == 1
    assert counted["ready"] is True


def test_missing_registered_pair_never_creates_final_receipt(tmp_path):
    batch = _batch(tmp_path)
    _result(batch, "case_a", status="completed", full=True)

    counted = collect_batch(tmp_path, batch / "registered_plan.json")
    assert counted["registered_pairs"] == 2
    assert counted["terminal_pairs"] == 1
    assert counted["missing_pairs"] == 1
    assert counted["ready"] is False

    with pytest.raises(ValueError, match="registered pairs are not terminal"):
        write_final_accounting(tmp_path, workspace_root=tmp_path)
    assert not (tmp_path / "review/RUN_ACCOUNTING.json").exists()


def test_actual_elapsed_only_command_receipt_keeps_exit_and_unknown_interval(tmp_path):
    _json(tmp_path / "logs/analysis.command.json",
          {"name": "actual_analysis", "command": ["python", "-m", "analysis"],
           "exit_code": 0, "elapsed_wall_seconds": 22.25,
           "receipt_source": "actual exec_command response"})

    commands = _commands(tmp_path)

    assert commands["receipt_count"] == 1
    assert commands["nonzero_exit_count"] == 0
    assert commands["elapsed_seconds_sum"] == 22.25
    assert commands["interval_union_seconds"] == 0.0
    assert commands["receipts"][0]["started_utc"] is None
    assert commands["issues"] == []


def test_results_plan_without_source_registration_is_not_silent(tmp_path):
    _batch(tmp_path, ("case_a",))
    _json(tmp_path / "development/results/registered_plan.json",
          {"schema": "policy_search.matched_full_feedback.v1",
           "signature": "orphaned-source-plan"})

    report = collect_accounting(tmp_path, workspace_root=tmp_path)

    assert any("orphan results plan" in issue for issue in report["issues"])
    assert report["finalizable"] is False


def test_fit_with_null_device_is_attempted_but_not_gpu_verified(tmp_path):
    folder = tmp_path / "models/incomplete_fit"
    _json(folder / "fit_started.json", {"status": "started"})
    _json(folder / "fit_report.json",
          {"actual_train_device": None, "actual_holdout_device": None,
           "optimizer_updates": None, "trained_weight_sha256": None})

    fits = _fits(tmp_path)

    assert fits["attempted_optimizer_fit_directories"] == 1
    assert fits["verified_cuda_fits"] == 0
    assert fits["fits"][0]["actual_train_device"] is None


def test_full_horizon_flag_does_not_turn_null_target_into_completion(tmp_path):
    batch = _batch(tmp_path, ("case_a",))
    _result(batch, "case_a", status="completed", full=True)
    result_path = _pair_path(batch, "case_a") / "attempt_0001/result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["primary_score"]["targets"][3] = None
    _json(result_path, result)
    _json(batch / "results/summary_0001.json",
          {"plan_signature": "registered-test-signature",
           "registered_pair_count": 1, "recorded_pair_count": 1,
           "terminal_pair_count": 1, "status_counts": {"completed": 1}})

    counted = collect_batch(tmp_path, batch / "registered_plan.json")

    assert counted["terminal_pairs"] == 1
    assert counted["completed_pairs"] == 0
    assert counted["unknown_primary_pairs"] == 1
    assert counted["ready"] is False


def test_nonfinite_target_or_false_mask_is_not_known():
    row = {"primary_score": {"full_horizon": True,
                             "targets": [0.0] * 18 + [float("nan")],
                             "target_mask": [True] * 19}}
    assert _known_window(row, "primary_score") is False
    row["primary_score"]["targets"][18] = 0.0
    row["primary_score"]["target_mask"][18] = False
    assert _known_window(row, "primary_score") is False


def test_terminal_count_is_distinct_from_missing_summary_readiness(tmp_path):
    batch = _batch(tmp_path, ("case_a",))
    _result(batch, "case_a", status="completed", full=True)

    report = collect_accounting(tmp_path, workspace_root=tmp_path)

    assert report["totals"]["terminal_pairs"] == 1
    assert report["all_registered_pairs_terminal"] is True
    assert report["all_batch_receipts_consistent"] is False
    assert any("missing published summary" in issue for issue in report["issues"])
    assert report["finalizable"] is False


def test_retry_attempts_keep_failed_and_unknown_attempt_counts(tmp_path):
    batch = _batch(tmp_path, ("case_a",))
    first = _pair_path(batch, "case_a") / "attempt_0001/result.json"
    _json(first, {"scenario_id": "case_a", "policy_id": "secure_milp",
                  "plan_signature": "registered-test-signature",
                  "status": "runner_exception", "terminal": False})
    second = _pair_path(batch, "case_a") / "attempt_0002/result.json"
    score = {"full_horizon": False, "targets": [0.0] + [None] * 18}
    _json(second, {"scenario_id": "case_a", "policy_id": "secure_milp",
                   "plan_signature": "registered-test-signature",
                   "status": "model_constraint_infeasible", "terminal": True,
                   "primary_score": score, "full_observation": score})
    _json(batch / "results/summary_0001.json",
          {"plan_signature": "registered-test-signature",
           "registered_pair_count": 1, "recorded_pair_count": 1,
           "terminal_pair_count": 1,
           "status_counts": {"model_constraint_infeasible": 1}})

    counted = collect_batch(tmp_path, batch / "registered_plan.json")

    assert counted["attempts"] == 2
    assert counted["terminal_attempts"] == 1
    assert counted["completed_attempts"] == 0
    assert counted["unknown_primary_attempts"] == 1
    assert counted["attempt_status_counts"] == {
        "model_constraint_infeasible": 1, "runner_exception": 1}


def test_terminal_train_plan_cannot_stand_in_for_unregistered_development(tmp_path):
    batch = _batch(tmp_path, ("case_a",))
    _result(batch, "case_a", status="completed", full=True)
    _json(batch / "results/summary_0001.json",
          {"plan_signature": "registered-test-signature",
           "registered_pair_count": 1, "recorded_pair_count": 1,
           "terminal_pair_count": 1, "status_counts": {"completed": 1}})

    report = collect_accounting(tmp_path, workspace_root=tmp_path)

    assert report["all_registered_pairs_terminal"] is True
    assert "development" in report["missing_required_phase_plans"]
    assert report["finalizable"] is False
