"""Actual execution and P0 loss must be compared on aligned raw segments."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from tools.policy_search.execution_effect import diagnose_execution_effects


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _pair_key(sid, pid):
    return hashlib.sha256(json.dumps([sid, pid], sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _segment(a, b, *, grid, failure, ens=0.0):
    return {"start_minute": a, "end_minute": b,
            "action": {"gt_electric_kW": [0, 0, 0], "grid_import_kW": grid,
                       "diesel_kW": 0, "absorption_cooling_kW": 0},
            "classification": {"p0_guarantee_failure": failure},
            "unserved_kW": [[ens, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]],
            "temperature": {"P0": 24.0}}


def _fixture(tmp_path):
    run = tmp_path / "v5"
    batch = run / "search/generation_1"
    plan = {"schema": "policy_search.matched_full_feedback.v1", "signature": "plan-a",
            "phase": "train", "scenario_ids": ["worse", "unknown"],
            "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"},
                             {"policy_id": "candidate", "kind": "feedback"}],
            "registered_pair_count": 4,
            "source_sha256": {"src/hospital_ies/state_transition.py": "frozen-source-hash"}}
    _json(batch / "registered_plan.json", plan)
    cases = [
        ("worse", "secure_milp", True,
         [_segment(0, 100, grid=100, failure=False),
          _segment(100, 120, grid=100, failure=True)], [1, 20, 0, 0, 0]),
        ("worse", "candidate", True,
         [_segment(0, 80, grid=100, failure=False),
          _segment(80, 120, grid=90, failure=True, ens=1)], [1, 40, 40/60, 0, 0]),
        ("unknown", "secure_milp", True,
         [_segment(0, 120, grid=100, failure=False)], [0, 0, 0, 0, 0]),
        ("unknown", "candidate", False,
         [_segment(0, 90, grid=90, failure=False)], [None, None, None, None, None]),
    ]
    for sid, pid, full, segments, first5 in cases:
        folder = batch / "results/pairs" / _pair_key(sid, pid) / "attempt_0001"
        payload = {"identity": {"scenario_id": sid, "policy_id": pid},
                   "summary": {"status": "completed" if full else "model_constraint_infeasible"},
                   # Deliberately different request at t=0: it cannot substitute
                   # for the actual segment action, whose first difference is t=80.
                   "decisions": [{"decision_time_min": 0,
                                  "requested_command": {"targets": {"grid_import": 77 if pid == "candidate" else 100}}}],
                   "segments": segments}
        raw = folder / "trajectory.json.gz"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(gzip.compress(json.dumps(payload).encode()))
        sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        _json(folder / "result.json", {"scenario_id": sid, "policy_id": pid,
                                       "plan_signature": "plan-a", "terminal": True,
                                       "status": payload["summary"]["status"],
                                       "trajectory_path": raw.name, "trajectory_sha256": sha,
                                       "binding": {"passed": True,
                                                   "initial_state_sha256": "same",
                                                   "exogenous_stream_id": "same"},
                                       "primary_score": {"full_horizon": full,
                                                         "targets": first5 + [0] * 9 + [24, 24, 24, 0, 0]}})
    return run


def test_actual_segment_difference_precedes_extra_p0_minutes(tmp_path):
    run = _fixture(tmp_path)
    result = diagnose_execution_effects(run, batches=("search/generation_1",),
                                        window=(0, 120))
    batch = result["batches"][0]
    assert batch["registered_candidate_pair_count"] == 2
    assert batch["complete_matched_pair_count"] == 1
    assert batch["unknown_pairs"][0]["scenario_id"] == "unknown"
    row = batch["P0_worse_complete_pairs"][0]
    assert row["first_actual_dispatch_difference_minute"] == 80
    assert row["first_extra_P0_failure_minute"] == 80
    assert row["extra_P0_failure_minutes"] == 20
    assert row["net_P0_failure_minutes_delta"] == 20
    assert row["first_actual_dispatch_difference"]["six_port_delta_kW"][3] == -10
    assert row["first_P0_raw_ENS_excess_minute"] == 80
    assert row["candidate_raw_sha256_verified"] is True
    assert result["registered_source_sha256"]["src/hospital_ies/state_transition.py"] == "frozen-source-hash"
