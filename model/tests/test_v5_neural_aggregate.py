"""DAgger-style v5 relabeling uses legal NN-visited states, not future returns."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hospital_ies.dataset_manifest import digest
from tools.control_improvement.candidates import HISTORY_KEYS
from tools.integrated_learning.policies import ACTION_NAMES


TEACHER = {"policy_id": "blend_overload", "kind": "feedback", "family": "blend",
           "theta": [0.8, 1.0, 2.5, 0.0, 0.0]}
NN_ID = "gru_g1_blend_overload_ungated"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _context(t):
    return {"time_minute": t, "arrival_minute": t, "delayed": False,
            "execution_basis_targets_kW": [40., 0., 0., 20., 0., 10.],
            "current_plan_targets_kW": [45., 0., 0., 15., 0., 10.],
            "capacity_GT_kW": [100., 100., 100.], "availability_HRSG": [1., 1., 1.],
            "overload_minutes_GT": [0., 0., 0.], "loads_kW": [[1., 1., 1.]] * 4,
            "capacity_ABS_kW": 100., "grid_import_limit_kW": 300.,
            "pending_command_ledger": {"available": False, "generated_minute": None,
                                       "planned_arrival_minute": None, "planned_targets_kW": None,
                                       "requested_targets_kW": None, "request_mask": None}}


def _decision(t, *, with_context=True):
    features = {key: 1.0 for key in HISTORY_KEYS}
    features["time_minute"] = t
    row = {"decision_time_min": t, "features": features,
           "feature_metadata": {key: {"available_time_min": t, "valid_time_min": t}
                                for key in features},
           "requested_command": {"security": {"gt_loading_penalty_USD_per_kWh": .35},
                                 "targets": dict(zip(ACTION_NAMES, [43., 2., 0., 15., 0., 10.]))}}
    row["policy_diagnostic"] = {
        "model_id": NN_ID, "future_rollouts_online": 0,
        "current_planning_queries": [{"candidate_id": "secure_baseline", "status": "success",
                                      "projected_targets_kW": [45., 0., 0., 15., 0., 10.]}],
        "current_context": _context(t) if with_context else None,
    }
    return row


def _registered_source(tmp_path, *, missing_context=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    scene = {"id": "case_1", "root_family_id": "root_1",
             "experiment_role": "train", "is_final_blind": False}
    manifest = {"scenarios": [scene], "splits": {"case_1": {
        "group_id": "group_1", "experiment_role": "train", "is_final_blind": False}}}
    manifest["signature"] = digest(manifest)
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    result_dir = tmp_path / "results"
    attempt = result_dir / "pairs" / "pair_1" / "attempt_0001"
    attempt.mkdir(parents=True)
    plan = {"manifest_path": str(mp.resolve()), "manifest_sha256": _sha(mp),
            "manifest_signature": manifest["signature"], "phase": "train",
            "scenario_ids": ["case_1"],
            "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"}, TEACHER,
                             {"policy_id": NN_ID, "kind": "neural_feedback", "model_dir": "toy"}]}
    plan["signature"] = digest(plan)
    (result_dir / "registered_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    payload = {"scenario": scene, "summary": {"status": "completed"},
               "decisions": [_decision(0.), _decision(15., with_context=not missing_context)]}
    raw = attempt / "trajectory.json.gz"
    with gzip.open(raw, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    result = {"scenario_id": "case_1", "policy_id": NN_ID, "kind": "neural_feedback",
              "phase": "train", "plan_signature": plan["signature"], "status": "completed",
              "terminal": True, "trajectory_path": raw.name, "trajectory_sha256": _sha(raw),
              "binding": {"passed": True}}
    rp = attempt / "result.json"
    rp.write_text(json.dumps(result), encoding="utf-8")
    return mp, rp


def test_relabels_nn_visited_states_with_frozen_feedback_rule(tmp_path):
    from tools.policy_search.neural_aggregate import relabel_nn_rollouts
    from tools.policy_search.policy import _request_targets

    mp, rp = _registered_source(tmp_path)
    rows, audit = relabel_nn_rollouts(mp, [rp], teacher_spec=TEACHER,
                                     neural_policy_id=NN_ID)
    assert len(rows) == 2 and audit["unusable_decisions"] == 0
    assert rows[0]["source_role"] == "nn_visited_relabel"
    expected = _request_targets(np.asarray([45., 0., 0., 15., 0., 10.]),
                                _context(0.), _decision(0.)["features"],
                                (0.8, 1.0, 2.5, 0., 0.))
    assert np.allclose(rows[0]["requested_targets_kW"], expected)
    assert rows[0]["delta_kW"] == pytest.approx((expected - np.asarray([45., 0., 0., 15., 0., 10.])).tolist())
    assert rows[0]["history_frame_count"] == 1 and rows[1]["history_frame_count"] == 2
    assert audit["source_trajectories"][0]["sha256"] == _sha(rp.parent / "trajectory.json.gz")


def test_missing_current_context_is_counted_not_fabricated(tmp_path):
    from tools.policy_search.neural_aggregate import relabel_nn_rollouts

    mp, rp = _registered_source(tmp_path, missing_context=True)
    rows, audit = relabel_nn_rollouts(mp, [rp], teacher_spec=TEACHER,
                                     neural_policy_id=NN_ID)
    assert len(rows) == 1 and audit["unusable_decisions"] == 1
    assert audit["unusable_reasons"]["missing_current_context_or_projection"] == 1
