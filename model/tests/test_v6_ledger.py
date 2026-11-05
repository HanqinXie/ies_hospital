"""Global v6 budget/attempt inventory tests; fixture outcomes are illustrative."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from tools.policy_redesign import ledger
from hospital_ies.dataset_manifest import digest


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan(batch, manifest, scenes, specs, phase, *, schema="policy_redesign.matched_full_feedback.v1"):
    body = {"schema": schema,
            "manifest_path": str(manifest.resolve()), "manifest_sha256": _sha(manifest),
            "phase": phase, "scenario_ids": scenes, "policy_specs": specs,
            "registered_pair_count": len(scenes) * len(specs),
            "final_blind_opened": False}
    body["signature"] = digest(body)
    path = batch / "registered_plan.json"
    _write(path, body)
    _write(batch / "results/registered_plan.json", body)
    return path


def _attempt(batch, sid, pid, number, *, status=None, terminal=None,
             full_horizon=None, raw=b"real stored bytes"):
    folder = batch / "results/pairs" / digest([sid, pid]) / f"attempt_{number:04d}"
    folder.mkdir(parents=True)
    raw_path = folder / "trajectory.json.gz"
    raw_path.write_bytes(raw)
    if status is not None:
        row = {"scenario_id": sid, "policy_id": pid, "status": status,
               "terminal": terminal, "trajectory_path": raw_path.name,
               "trajectory_sha256": _sha(raw_path)}
        if full_horizon is not None:
            row["primary_score"] = {"full_horizon": full_horizon,
                                    "targets": [0.0] * 19 if full_horizon else [None] * 19}
        _write(folder / "result.json", row)
        if status == "runner_exception":
            _write(folder / "error.json", {"status": status, "message": "fixture failure"})
    return folder


def _fixture(tmp_path):
    run = tmp_path / "v6_run"
    new_manifest = run / "new_data/global_manifest.json"
    _write(new_manifest, {"manifest_version": "policy_redesign.v6.new60.v1",
                          "scenarios": [{"id": "new_a", "experiment_role": "selection",
                                         "is_final_blind": False},
                                        {"id": "new_b", "experiment_role": "development",
                                         "is_final_blind": False}]})
    old_manifest = tmp_path / "v5_train_manifest.json"
    _write(old_manifest, {"manifest_version": "policy_search.v5.train",
                          "scenarios": [{"id": f"old_{i}", "experiment_role": "train",
                                         "is_final_blind": False} for i in range(3)]})
    train = run / "search/adaptive/seed_0001/generation_0001"
    selection = run / "selection/batch_01"
    _plan(train, old_manifest, ["old_0"],
          [{"policy_id": "secure_milp", "kind": "secure"},
           {"policy_id": "feedback_a", "kind": "v6_feedback",
            "family": "port_residual", "theta": [0.8, 0, 0, 0, 0, 0]}], "train")
    _plan(selection, new_manifest, ["new_a"],
          [{"policy_id": "secure_milp", "kind": "secure"},
           {"policy_id": "feedback_b", "kind": "v6_feedback",
            "family": "source_allocation", "theta": [0.8, 0, 0, 0, 0, 0, 0]}],
          "selection")
    _attempt(train, "old_0", "secure_milp", 1, status="complete", terminal=True,
             full_horizon=True)
    _attempt(train, "old_0", "feedback_a", 1, status="runner_exception",
             terminal=False)
    _attempt(train, "old_0", "feedback_a", 2, status="truncated",
             terminal=True, full_horizon=False)
    _attempt(selection, "new_a", "secure_milp", 1, raw=b"orphan raw has SHA")
    started = run / "learning/smoke/model_attempt_0002"
    _write(started / "fit_started.json", {"schema": "fit_started", "requested_device": "cuda:0"})
    _write(started / "fit_report.json", {"architecture": "Actor", "hidden": 32,
                                         "optimizer_updates": 20,
                                         "actual_train_device": "cuda:0"})
    _write(run / "learning/smoke/model/fit_error.json",
           {"failure_stage": "before CUDA initialization", "error_type": "TypeError"})
    commands = [
        {"name": "v6_search_run", "command": ["python", "-m", "tools.policy_redesign.search", "run"],
         "started_utc": "2026-10-16T00:00:00+00:00",
         "finished_utc": "2026-10-16T00:00:10+00:00", "elapsed_seconds": 10,
         "exit_code": 0},
        {"name": "v6_fit_failed", "command": ["python", "fit.py"],
         "started_utc": "2026-10-16T00:00:05+00:00",
         "finished_utc": "2026-10-16T00:00:15+00:00", "elapsed_seconds": 10,
         "exit_code": 1},
    ]
    (run / "commands_and_exit_codes.txt").write_text(
        "\n".join(json.dumps(x) for x in commands) + "\n", encoding="utf-8")
    _write(run / "logs/v6_search_run.command.json", commands[0])  # duplicate receipt
    _write(run / "search/adaptive/search_plan.json",
           {"schema": "policy_redesign.adaptive_feedback_search.v1.plan",
            "seeds": [1, 2, 3], "min_generations": 4, "max_generations": 6})
    return run, old_manifest


def test_global_ledger_counts_actual_attempts_not_terminal_receipts(tmp_path):
    run, old_manifest = _fixture(tmp_path)
    record = ledger.build_ledger(run, v5_manifest_path=old_manifest, write_outputs=True)
    budget = record["budget"]
    assert budget["actual_environment_attempts"]["used"] == 4
    assert budget["new_exogenous_specs"]["used"] == 2
    assert budget["reused_v5_manifest_train_specs"] == 3
    assert budget["reused_v5_train_spec_ids_in_v6_plans"] == 1
    assert budget["major_recipes"]["observed_distinct"] == 3
    assert budget["neural_fit_invocations"]["used"] == 2
    assert budget["confirmed_optimizer_fits"]["used"] == 1
    assert budget["recorded_command_union_wall_seconds"] == 15.0
    assert record["registered_plan_count"] == 2  # runner plan copies are deduplicated
    assert record["terminal_result_receipt_count"] == 2
    assert record["complete_primary_score_receipt_count"] == 1
    assert record["all_registered_batches_terminal"] is False
    assert len(record["attempts"]) == 4
    assert any(row["reason"] == "attempt_without_result_receipt" for row in record["unknowns"])
    assert any(row["reason"] == "registered_pair_unattempted" and
               row["policy_id"] == "feedback_b" for row in record["unknowns"])
    assert any(row["status"] == "runner_exception" for row in record["failed_attempts"])
    assert len(record["raw_sha_index"]) == 4
    csv_path = run / "review/ALL_ATTEMPTS_INDEX.csv"
    assert (run / "review/ALL_ATTEMPTS_LEDGER.json").is_file()
    with csv_path.open(newline="", encoding="utf-8") as stream:
        assert len(list(csv.DictReader(stream))) == 4


def test_ledger_does_not_call_truncated_terminal_a_completed_episode(tmp_path):
    run, old_manifest = _fixture(tmp_path)
    record = ledger.build_ledger(run, v5_manifest_path=old_manifest)
    truncated = next(x for x in record["attempts"] if x["status"] == "truncated")
    assert truncated["terminal"] is True
    assert truncated["primary_full_horizon"] is False
    assert truncated["outcome_class"] == "terminal_unknown_primary"
    assert "old_0/feedback_a" in record["unknown_primary_pair_ids"]


def test_ledger_registers_regime_and_security_feedback_attempts(tmp_path):
    run, old_manifest = _fixture(tmp_path)
    variants = (
        ("regime", "policy_redesign.regime_matched_full_feedback.v1",
         "regime_feedback", {"theta": [0.0] * 18}),
        ("security", "policy_redesign.security_matched_full_feedback.v1",
         "security_feedback", {"weights": [0.35, 3.0, 3.0]}),
        ("reserve", "policy_redesign.reserve_diagnostic.v1.plan",
         "reserve_feedback", {"theta": [1.0]}),
    )
    for name, schema, kind, parameters in variants:
        batch = run / "diagnostics" / f"{name}_smoke"
        policy_id = f"{name}_candidate"
        _plan(batch, old_manifest, ["old_1"],
              [{"policy_id": "secure_milp", "kind": "secure"},
               {"policy_id": policy_id, "kind": kind, **parameters}],
              "train", schema=schema)
        _attempt(batch, "old_1", policy_id, 1, status="runner_exception", terminal=False)
    record = ledger.build_ledger(run, v5_manifest_path=old_manifest)
    assert record["integrity_errors"] == []
    assert record["registered_plan_count"] == 5
    assert record["budget"]["actual_environment_attempts"]["used"] == 7
    assert {"regime_feedback", "security_feedback", "reserve_feedback"}.issubset(
        {row["recipe_id"] for row in record["major_recipe_inventory"]})
    for name, _, kind, _ in variants:
        attempt = next(row for row in record["attempts"]
                       if row["policy_id"] == f"{name}_candidate")
        assert attempt["kind"] == kind
        assert attempt["outcome_class"] == "nonterminal_unknown"
