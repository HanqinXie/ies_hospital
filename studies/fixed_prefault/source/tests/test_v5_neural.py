"""v5 GRU imitation contracts: provenance, legal observations and CUDA only."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hospital_ies.dataset_manifest import digest
from tools.control_improvement.candidates import HISTORY_KEYS
from tools.integrated_learning.policies import ACTION_NAMES


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _observation(t):
    features = {key: 1.0 for key in HISTORY_KEYS}
    features["time_minute"] = float(t)
    metadata = {key: {"available_time_min": float(t), "valid_time_min": float(t)}
                for key in features}
    return {"decision_time_min": float(t), "features": features, "feature_metadata": metadata}


def _context(t):
    return {"time_minute": float(t), "arrival_minute": float(t), "delayed": False,
            "execution_basis_targets_kW": [40., 0., 0., 20., 0., 10.],
            "current_plan_targets_kW": [45., 0., 0., 15., 0., 10.],
            "capacity_GT_kW": [100., 100., 100.], "availability_HRSG": [1., 1., 1.],
            "overload_minutes_GT": [0., 0., 0.], "loads_kW": [[1., 1., 1.]] * 4,
            "capacity_ABS_kW": 100., "grid_import_limit_kW": 300.,
            "pending_command_ledger": {"available": False, "generated_minute": None,
                                       "planned_arrival_minute": None, "planned_targets_kW": None,
                                       "requested_targets_kW": None, "request_mask": None}}


def _decision(t, *, intervene):
    row = _observation(t)
    secure = [45., 0., 0., 15., 0., 10.]
    requested = [40., 5., 0., 15., 0., 10.] if intervene else secure
    request = {"security": {"gt_loading_penalty_USD_per_kWh": .35}}
    if intervene:
        request["targets"] = dict(zip(ACTION_NAMES, requested))
    row.update(requested_command=request, policy_diagnostic={
        "current_context": _context(t),
        "current_planning_queries": [{"candidate_id": "secure_baseline",
                                      "projected_targets_kW": secure, "status": "success"}],
        "requested_six_targets_kW": requested,
        "delta_from_secure_plan_kW": [a - b for a, b in zip(requested, secure)],
        "strategy_status": "feedback_six_port_request" if intervene else "secure_no_effective_target_change",
        "future_rollouts_online": 0, "strategy_fallback": False})
    return row


def _source(tmp_path, *, role="train", bad_sha=False):
    from tools.policy_search.neural import collect_training_examples

    tmp_path.mkdir(parents=True, exist_ok=True)
    case = {"id": "case_1", "root_family_id": "root_1", "experiment_role": role,
            "is_final_blind": False}
    manifest = {"scenarios": [case], "splits": {"case_1": {"experiment_role": role,
                "group_id": "root_group_1", "is_final_blind": False}}}
    manifest["signature"] = digest(manifest)
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    result_dir = tmp_path / "results"
    attempt = result_dir / "pairs" / "pair_1" / "attempt_0001"
    attempt.mkdir(parents=True)
    plan = {"manifest_path": str(mp.resolve()), "manifest_sha256": _sha(mp),
            "manifest_signature": manifest["signature"], "phase": "train",
            "scenario_ids": ["case_1"],
            "policy_specs": [{"policy_id": "teacher", "kind": "feedback", "theta": [1]}]}
    plan["signature"] = digest(plan)
    (result_dir / "registered_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    payload = {"scenario": case, "summary": {"status": "completed"},
               "decisions": [_decision(0., intervene=True), _decision(15., intervene=False)]}
    raw = attempt / "trajectory.json.gz"
    with gzip.open(raw, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    result = {"scenario_id": "case_1", "policy_id": "teacher", "kind": "feedback",
              "phase": "train", "plan_signature": plan["signature"], "status": "completed",
              "terminal": True, "trajectory_path": raw.name,
              "trajectory_sha256": "0" * 64 if bad_sha else _sha(raw),
              "binding": {"passed": True}}
    rp = attempt / "result.json"
    rp.write_text(json.dumps(result), encoding="utf-8")
    return mp, rp, collect_training_examples


def test_collects_full_feedback_teacher_actions_with_secure_identity(tmp_path):
    mp, rp, collect = _source(tmp_path)
    rows, audit = collect(mp, [rp], teacher_policy_id="teacher")
    assert len(rows) == 2
    assert rows[0]["root_family_id"] == rows[1]["root_family_id"] == "root_1"
    assert rows[0]["intervention"] is True and rows[1]["intervention"] is False
    assert rows[0]["delta_kW"] == [-5., 5., 0., 0., 0., 0.]
    assert rows[1]["delta_kW"] == [0.] * 6
    assert rows[0]["history_frame_count"] == 1 and rows[1]["history_frame_count"] == 2
    assert audit["source_trajectories"][0]["sha256"] == _sha(rp.parent / "trajectory.json.gz")
    assert audit["unknown_or_unusable_decisions"] == 0


def test_rejects_nontrain_or_tampered_trajectory_before_fit(tmp_path):
    mp, rp, collect = _source(tmp_path / "dev", role="development")
    with pytest.raises(ValueError, match="train"):
        collect(mp, [rp], teacher_policy_id="teacher")
    mp, rp, collect = _source(tmp_path / "bad", bad_sha=True)
    with pytest.raises(ValueError, match="SHA"):
        collect(mp, [rp], teacher_policy_id="teacher")


def test_encoder_rejects_future_feature_and_keeps_padding_mask(tmp_path):
    from tools.policy_search.neural import encode_observation

    h = [_observation(0.), _observation(15.)]
    frames, context = encode_observation(h, _context(15.))
    assert frames.shape == (4, len(HISTORY_KEYS) + 1)
    assert np.array_equal(frames[:, 0], [0., 0., 1., 1.])
    assert context.ndim == 1 and np.all(np.isfinite(context))
    bad = copy.deepcopy(h)
    bad[-1]["feature_metadata"]["time_minute"]["available_time_min"] = 30.
    with pytest.raises(ValueError, match="future|Unavailable"):
        encode_observation(bad, _context(15.))


def test_no_cpu_training_fallback_and_error_receipt(tmp_path, monkeypatch):
    from tools.policy_search.neural import train_from_rollouts

    mp, rp, _ = _source(tmp_path / "data")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    model_dir = tmp_path / "model"
    with pytest.raises(RuntimeError, match="CUDA"):
        train_from_rollouts(mp, [rp], model_dir, teacher_policy_id="teacher", epochs=1)
    error = json.loads((model_dir / "fit_error.json").read_text(encoding="utf-8"))
    assert error["error_type"] == "RuntimeError" and "CUDA" in error["message"]


def test_gru_is_recurrent_and_gate_variant_rejects_noncalibration(tmp_path):
    from tools.policy_search.neural import _GRUNet, CONTEXT_DIM, FRAME_DIM, create_gate_variant

    model = _GRUNet(FRAME_DIM, CONTEXT_DIM, hidden=8)
    assert isinstance(model.gru, torch.nn.GRU)
    residual, gate_logit = model(torch.zeros(2, 4, FRAME_DIM), torch.zeros(2, CONTEXT_DIM))
    assert residual.shape == (2, 6) and gate_logit.shape == (2,)
    assert torch.all(torch.abs(residual) <= 1)
    with pytest.raises(ValueError, match="calibration"):
        create_gate_variant(tmp_path / "missing_model", tmp_path / "variant",
                            threshold=.5, calibration_source={
                                "role": "development", "root_family_ids": ["dev"],
                                "source_sha256": "a" * 64})
