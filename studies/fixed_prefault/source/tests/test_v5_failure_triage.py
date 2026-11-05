"""Evidence-preserving failure triage over registered raw artifacts."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from tools.policy_search.failure_triage import collect_failure_triage, write_failure_triage


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _raw(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(value).encode()))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _v5_pair_key(sid, pid):
    encoded = json.dumps([sid, pid], sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _failure_payload(*, status=2, native=3, with_validation=False):
    attempt = {"attempt": 1, "status": status, "native_status": native,
               "accepted": False, "elapsed_seconds": 0.12,
               "validation": ({"max_original_violation": 0.02,
                               "rows": [{"name": "priority.P0.normalized_ENS.upper",
                                         "violation": 0.02}]}
                              if with_validation else None)}
    error = {"time_minute": 279.0, "phase": "fixed_execution",
             "category": "model_constraint_infeasible", "error_type": "ModelConstraintInfeasible",
             "message": "v3 MILP failure: rank=0, status=2",
             "solver_backend_attempts": [{"rank": 0, "status": status,
                                          "attempts": [attempt, dict(attempt, attempt=2)]}]}
    return {"identity": {"scenario_id": "scene-a", "policy_id": "secure_milp"},
            "summary": {"status": "model_constraint_infeasible"},
            "error": {"type": "ModelConstraintInfeasible", "classification": "model_constraint_infeasible",
                      "message": error["message"], "diagnostic": error},
            "solver_errors": [error],
            "solver_calls": [{"time_minute": 279.0, "phase": "fixed_execution",
                              "status": "error", "elapsed_seconds": 0.25,
                              "solver_backend_layers": error["solver_backend_attempts"]}]}


def _fixture(tmp_path, *, complete=True, native=3, status=2, with_validation=False):
    v5 = tmp_path / "v5"
    v4 = tmp_path / "v4"
    batch = v5 / "search/generation_1"
    _json(batch / "registered_plan.json",
          {"schema": "policy_search.matched_full_feedback.v1", "signature": "plan-a",
           "scenario_ids": ["scene-a"], "policy_specs": [{"policy_id": "secure_milp"}],
           "registered_pair_count": 1, "phase": "train"})
    if complete:
        attempt = batch / "results/pairs" / _v5_pair_key("scene-a", "secure_milp") / "attempt_0001"
        raw_hash = _raw(attempt / "trajectory.json.gz",
                        _failure_payload(status=status, native=native,
                                         with_validation=with_validation))
        _json(attempt / "result.json", {"scenario_id": "scene-a", "policy_id": "secure_milp",
                                        "plan_signature": "plan-a", "terminal": True,
                                        "status": "model_constraint_infeasible",
                                        "observed_until_minute": 279.0,
                                        "trajectory_path": "trajectory.json.gz",
                                        "trajectory_sha256": raw_hash})

    _json(v4 / "new_data/global_manifest.json",
          {"signature": "old-manifest", "scenarios": [{"id": "old-a"}],
           "policies": ["priority_milp", "secure_milp"],
           "p2b_shards": [{"id": "shard_00", "scenario_ids": ["old-a"]}]})
    shard = v4 / "new_data/shards/shard_00"
    shard.mkdir(parents=True)
    journal = [{"event": "started", "scenario_id": "old-a", "policy_id": "secure_milp",
                "manifest_signature": "old-manifest"},
               {"event": "finished", "scenario_id": "old-a", "policy_id": "secure_milp",
                "manifest_signature": "old-manifest", "status": "model_constraint_infeasible"}]
    (shard / "attempts.jsonl").write_text("\n".join(json.dumps(x) for x in journal) + "\n")
    # P2B shard key uses json.dumps defaults, unlike the v5 canonical pair key.
    old_key = hashlib.sha256(json.dumps(["old-a", "secure_milp"]).encode()).hexdigest()
    old_raw = shard / "checkpoints" / f"{old_key}.json.gz"
    old_hash = _raw(old_raw, {"manifest_signature": "old-manifest",
                              "scenario_id": "old-a", "policy_id": "secure_milp",
                              "payload": {"summary": {"status": "model_constraint_infeasible"}}})
    old_raw.with_suffix(".sha256").write_text(old_hash)

    dev = v4 / "closed_loop/development60_v1_runs"
    dev_attempt = dev / "pairs" / _v5_pair_key("old-a", "secure_milp") / "attempt_0001"
    dev_hash = _raw(dev_attempt / "trajectory.json.gz",
                    {"identity": {"scenario_id": "old-a", "policy_id": "secure_milp"},
                     "summary": {"status": "model_constraint_infeasible"}})
    dev_result = {"scenario_id": "old-a", "policy_id": "secure_milp",
                  "status": "model_constraint_infeasible", "terminal": True,
                  "trajectory_path": "trajectory.json.gz", "trajectory_sha256": dev_hash}
    _json(dev_attempt / "result.json", dev_result)
    _json(dev / "summary_0001.json",
          {"registered_pair_count": 1, "terminal_pair_count": 1,
           "results": [dev_result]})
    return v5, v4


def test_native_solver_status_is_evidence_not_independent_proof(tmp_path):
    v5, v4 = _fixture(tmp_path)
    result = collect_failure_triage(v5, v4, batches=("search/generation_1",))
    batch = result["v5_batches"][0]
    assert batch["registered_pair_count"] == batch["terminal_pair_count"] == 1
    row = batch["terminal_model_constraint_failures"][0]
    assert row["solver_error"]["time_minute"] == 279.0
    assert row["solver_error"]["phase"] == "fixed_execution"
    assert row["solver_error"]["failed_rank"] == 0
    assert row["classification"] == "native_infeasible_on_finite_recoveries"
    assert row["independent_infeasibility_proof"] is False
    assert row["solver_error"]["offending_constraint"] is None
    assert len(row["solver_error"]["backend_attempts"]) == 2
    assert len(result["v4_historical"]["baseline_failures"]) == 1
    assert len(result["v4_historical"]["development_failures"]) == 1
    assert result["v4_historical"]["baseline_failures"][0]["raw_sha256_verified"] is True
    assert result["v4_historical"]["development_failures"][0]["raw_sha256_verified"] is True


def test_incomplete_batch_refuses_final_write_but_read_only_collects(tmp_path):
    v5, v4 = _fixture(tmp_path, complete=False)
    collected = collect_failure_triage(v5, v4, batches=("search/generation_1",))
    assert collected["v5_batches"][0]["terminal_pair_count"] == 0
    output = v5 / "diagnostics/FAILURE_TRIAGE.json"
    with pytest.raises(ValueError, match="nonterminal"):
        write_failure_triage(v5, v4, output, batches=("search/generation_1",))
    assert not output.exists()


def test_rejected_numeric_solution_is_not_called_model_proof(tmp_path):
    v5, v4 = _fixture(tmp_path, native=2, status=0, with_validation=True)
    result = collect_failure_triage(v5, v4, batches=("search/generation_1",))
    row = result["v5_batches"][0]["terminal_model_constraint_failures"][0]
    assert row["classification"] == "numerical_validation_rejection"
    assert row["solver_error"]["witnessed_violation_rows"][0]["name"] == "priority.P0.normalized_ENS.upper"
    assert row["independent_infeasibility_proof"] is False
