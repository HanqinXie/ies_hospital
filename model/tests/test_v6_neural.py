"""Outcome-ranked v6 neural labels and deployment safeguards."""
from __future__ import annotations

from types import SimpleNamespace
import gzip
import hashlib
import json
import weakref

import numpy as np
import pytest
import torch

from tools.policy_redesign.neural import (
    OutcomeRankedActor,
    best_epoch_index,
    collect_outcome_ranked_examples,
    fit_outcome_ranked,
    load_cpu_for_repro,
    physical_context_scales,
    rank_complete_root,
    require_full_registered_train_panel,
    stratified_root_split,
)


def _result(scene, policy, *, p0_failure=0, minutes=0, p0_e=0.0,
            fee=0.0, completed=True):
    targets = [0.0] * 19
    targets[0], targets[1], targets[2], targets[18] = (
        p0_failure, minutes, p0_e, fee)
    score = {
        "full_horizon": completed,
        "target_mask": [completed] * 19,
        "targets": targets if completed else [None] * 19,
    }
    return {
        "scenario_id": scene,
        "policy_id": policy,
        "status": "completed" if completed else "model_constraint_infeasible",
        "terminal": True,
        "primary_score": score,
        "full_observation": score,
    }


def test_root_ranking_masks_unknown_and_places_p0_before_fee():
    rows = [
        _result("a", "secure_milp", p0_failure=1, minutes=20, p0_e=30, fee=10),
        _result("b", "secure_milp", p0_failure=0, fee=10),
        _result("a", "candidate", p0_failure=0, fee=1000),
        _result("b", "candidate", p0_failure=0, fee=1000),
        _result("a", "unknown", p0_failure=0, fee=0),
        _result("b", "unknown", completed=False),
    ]
    ranked = rank_complete_root(rows, ("a", "b"), ("secure_milp", "candidate", "unknown"))
    assert ranked["winner_policy_id"] == "candidate"
    assert ranked["ineligible_unknown_policy_ids"] == ["unknown"]
    assert ranked["eligible_policy_ids"] == ["candidate", "secure_milp"]


def test_root_ranking_prefers_secure_on_exact_outcome_tie():
    rows = [_result("a", p) for p in ("secure_milp", "candidate")]
    ranked = rank_complete_root(rows, ("a",), ("secure_milp", "candidate"))
    assert ranked["winner_policy_id"] == "secure_milp"


@pytest.mark.parametrize("field", ["minutes", "p0_e"])
def test_root_ranking_uses_worst_paired_p0_increase_before_total_gain(field):
    rows = [
        _result("a", "secure_milp", p0_failure=1, **{field: 100}),
        _result("b", "secure_milp", p0_failure=1, **{field: 1}),
        _result("a", "candidate", p0_failure=1, **{field: 90}),
        _result("b", "candidate", p0_failure=1, **{field: 5}),
    ]
    ranked = rank_complete_root(rows, ("a", "b"), ("secure_milp", "candidate"))
    assert ranked["winner_policy_id"] == "secure_milp"
    assert "relative secure" in ranked["ranking_semantics"]


def test_root_outcome_key_matches_registered_search_priority_components():
    from tools.policy_redesign.search import priority_key
    from tools.policy_search.preference import paired_metrics

    rows = [
        _result("a", "secure_milp", p0_failure=1, minutes=20, p0_e=5, fee=5),
        _result("b", "secure_milp", p0_failure=0, minutes=0, p0_e=2, fee=9),
        _result("a", "candidate", p0_failure=0, minutes=0, p0_e=1, fee=30),
        _result("b", "candidate", p0_failure=1, minutes=2, p0_e=3, fee=1),
    ]
    rows[-1]["full_observation"] = json.loads(json.dumps(rows[-1]["primary_score"]))
    rows[-1]["full_observation"]["targets"][2] = 4.0
    ranked = rank_complete_root(rows, ("a", "b"), ("secure_milp", "candidate"))
    primary = priority_key(paired_metrics(rows, "candidate", score_field="primary_score"))
    full = priority_key(paired_metrics(rows, "candidate", score_field="full_observation"))
    assert ranked["outcome_keys"]["candidate"] == list(
        primary[1:14] + full[1:14] + primary[14:] + full[14:])


def test_root_split_keeps_all_pressure_scenes_together_and_every_condition_in_validation():
    scenes = [
        {"id": f"{condition}_{root}_{pressure}", "condition_id": condition,
         "root_family_id": f"{condition}_{root}", "pressure_point_index": pressure}
        for condition in ("GRID0", "normal") for root in range(5) for pressure in range(5)
    ]
    split = stratified_root_split(scenes, seed=61)
    fit, val = set(split["fit_roots"]), set(split["validation_roots"])
    assert not fit & val
    assert len(fit) == 8 and len(val) == 2
    assert {root.split("_")[0] for root in val} == {"GRID0", "normal"}
    assert all(all(s["root_family_id"] in fit for s in scenes if s["root_family_id"] == root)
               for root in fit)
    excluded = {"GRID0_0", "GRID0_1", "normal_0", "normal_1"}
    independent = stratified_root_split(
        scenes, seed=61, validation_exclude_root_ids=excluded)
    assert not set(independent["validation_roots"]) & excluded
    assert excluded <= set(independent["fit_roots"])
    assert {root.split("_")[0] for root in independent["validation_roots"]} == {
        "GRID0", "normal"}


def test_formal_fit_requires_every_train_root_and_all_five_pressure_scenes():
    scenes = [{"id": f"s{i}", "root_family_id": "r0", "pressure_point_index": i,
               "experiment_role": "train", "is_final_blind": False}
              for i in range(5)]
    manifest = {"scenarios": scenes, "splits": {
        s["id"]: {"experiment_role": "train", "is_final_blind": False}
        for s in scenes}}
    audit = {"registered_train_panel_scenario_ids": [s["id"] for s in scenes],
             "root_rankings": [{"root_family_id": "r0", "registered_panel_scenes": 5,
                                "partial_pressure_support": False}]}
    assert require_full_registered_train_panel(audit, manifest) == {
        "train_root_count": 1, "train_scenario_count": 5,
        "pressure_point_indices_per_root": [0, 1, 2, 3, 4]}
    audit["registered_train_panel_scenario_ids"] = ["s0"]
    with pytest.raises(ValueError, match="full five-pressure"):
        require_full_registered_train_panel(audit, manifest)


def test_runtime_diesel_rating_scales_900_kw_to_one_without_training_statistics():
    plant = SimpleNamespace(gt_rated_kW=320.0, emergency_diesel_kW=900.0,
                            absorption_rt=200.0)
    scales = physical_context_scales(plant, grid_reference_kW=850.0)
    assert scales.shape == (58,)
    assert np.all(scales > 0)
    assert np.all(scales[[4, 10, 44, 50]] == 900.0)
    assert (np.asarray([900.0]) / scales[4])[0] == 1.0


def test_checkpoint_selection_uses_earliest_lowest_validation_and_patience():
    losses = [1.0, 0.7, 0.8, 0.9, 1.2, 0.6]
    assert best_epoch_index(losses, patience=2) == (2, 4)
    with pytest.raises(ValueError, match="validation"):
        best_epoch_index([float("nan")], patience=2)


def test_cpu_repro_loads_best_checkpoint_and_real_example(tmp_path):
    import hashlib
    import json
    from tools.policy_redesign import neural

    model = OutcomeRankedActor(hidden=8)
    torch.save(model.state_dict(), tmp_path / "best.pt")
    np.savez_compressed(tmp_path / "fit_examples.npz",
                        validation_frame=np.zeros((1, 4, neural.FRAME_DIM), dtype=np.float32),
                        validation_context=np.ones((1, neural.CONTEXT_DIM), dtype=np.float32))
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    metadata = {
        "schema": neural.SCHEMA, "architecture": "OutcomeRankedActor",
        "hidden": 8, "frame_dim": neural.FRAME_DIM,
        "context_dim": neural.CONTEXT_DIM,
        "source_module_sha256": sha(neural.Path(neural.__file__)),
        "observation_encoder_source_sha256": sha(
            neural.ROOT / "tools/policy_search/neural.py"),
        "best_weight_sha256": neural._weight_sha(model),
        "deployed_checkpoint": "best.pt",
        "teacher_intervention_gate": False,
        "artifacts": {name: sha(tmp_path / name)
                      for name in ("best.pt", "fit_examples.npz")},
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    loaded, example = load_cpu_for_repro(tmp_path)
    assert all(str(p.device) == "cpu" for p in loaded.parameters())
    with torch.inference_mode():
        expected = model(example["frames"], example["context"])
        actual = loaded(example["frames"], example["context"])
    assert torch.allclose(actual, expected)
    metadata["observation_encoder_source_sha256"] = "0" * 64
    (tmp_path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="observation encoder"):
        load_cpu_for_repro(tmp_path)
    metadata["observation_encoder_source_sha256"] = sha(
        neural.ROOT / "tools/policy_search/neural.py")
    (tmp_path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (tmp_path / "best.pt").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA"):
        load_cpu_for_repro(tmp_path)


def test_complete_registered_panel_labels_real_diesel_request_and_marks_partial_pressure(
        tmp_path, monkeypatch):
    from hospital_ies.dataset_manifest import digest
    from tools.control_improvement.candidates import HISTORY_KEYS
    from tools.integrated_learning.policies import ACTION_NAMES
    from tools.policy_redesign import neural
    from tools.policy_redesign.policy import current_port_limits

    monkeypatch.setattr(neural, "HospitalIESEnv", lambda *args, **kwargs: object(),
                        raising=False)
    monkeypatch.setattr(
        neural, "window_targets",
        lambda payload, start, length, env: payload["scores"][
            "primary" if start == 60.0 else "full"], raising=False)

    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    scenes = [
        {"id": f"grid_{j}", "root_family_id": "grid_root", "condition_id": "GRID0_grid850_delay15",
         "experiment_role": "train", "is_final_blind": False}
        for j in range(5)
    ]
    manifest = {"scenarios": scenes, "execution_contract": {}, "splits": {
        scene["id"]: {"experiment_role": "train", "is_final_blind": False}
        for scene in scenes}}
    manifest["signature"] = digest(manifest)
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    results = tmp_path / "results"
    plan = {"manifest_path": str(mp.resolve()), "manifest_sha256": sha(mp),
            "manifest_signature": manifest["signature"], "phase": "train",
            "source_sha256": {"tools/control_improvement/branches.py": sha(
                neural.ROOT / "tools/control_improvement/branches.py")},
            "score_windows": {"primary": [60, 420], "full": [0, 600]},
            "scenario_ids": ["grid_0"], "policy_specs": [
                {"policy_id": "secure_milp", "kind": "secure"},
                {"policy_id": "candidate", "kind": "v6_feedback", "theta": [0.0] * 6},
                {"policy_id": "candidate_tie", "kind": "v6_feedback", "theta": [0.0] * 6}]}
    plan["signature"] = digest(plan)
    results.mkdir()
    (results / "registered_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    context = {"time_minute": 0.0, "arrival_minute": 0.0, "delayed": False,
               "execution_basis_targets_kW": [40., 0., 0., 20., 0., 10.],
               "current_plan_targets_kW": [45., 0., 0., 15., 0., 10.],
               "capacity_GT_kW": [100., 100., 100.], "availability_HRSG": [1., 1., 1.],
               "overload_minutes_GT": [0., 0., 0.], "loads_kW": [[1., 1., 1.]] * 4,
               "capacity_ABS_kW": 100., "grid_import_limit_kW": 300.,
               "pending_command_ledger": {"available": False, "generated_minute": None,
                                          "planned_arrival_minute": None,
                                          "planned_targets_kW": None,
                                          "requested_targets_kW": None, "request_mask": None}}
    features = {key: 1.0 for key in HISTORY_KEYS}
    features.update(time_minute=0.0, diesel_energy_kWh=2400.0,
                    diesel_status="CONNECTED", availability_DIESEL=1.0,
                    availability_GRID=1.0)
    metadata = {key: {"available_time_min": 0.0, "valid_time_min": 0.0}
                for key in features}
    secure = [45., 0., 0., 15., 0., 10.]
    requested = [40., 5., 0., 15., 900., 10.]
    upper = current_port_limits(context, features, diesel_rated_kW=900.,
                                diesel_available=True, step_hours=.25)
    decision = {"decision_time_min": 0.0, "features": features,
                "feature_metadata": metadata,
                "requested_command": {"security": {"gt_loading_penalty_USD_per_kWh": .35},
                                      "targets": dict(zip(ACTION_NAMES, requested))},
                "policy_diagnostic": {
                    "current_context": context, "future_rollouts_online": 0,
                    "strategy_fallback": False,
                    "current_planning_queries": [{"candidate_id": "secure_baseline",
                                                  "status": "success",
                                                  "projected_targets_kW": secure}],
                    "requested_six_targets_kW": requested,
                    "request_capacity_scale_kW": upper.tolist(),
                    "diesel_available_current": True,
                    "current_step_hours": .25, "diesel_rated_kW": 900.0}}
    paths = []
    for pid, kind, failure, fuel_request in (
            ("secure_milp", "secure", 1, None),
            ("candidate", "v6_feedback", 0, decision),
            ("candidate_tie", "v6_feedback", 1, None)):
        attempt = results / "pairs" / pid / "attempt_0001"
        attempt.mkdir(parents=True)
        score = _result("grid_0", pid, p0_failure=failure)["primary_score"]
        payload = {"scenario": scenes[0], "summary": {"status": "completed"},
                   "decisions": [] if fuel_request is None else [fuel_request],
                   "scores": {"primary": score, "full": score}}
        raw = attempt / "trajectory.json.gz"
        with gzip.open(raw, "wt", encoding="utf-8") as stream:
            json.dump(payload, stream)
        result = {"scenario_id": "grid_0", "policy_id": pid, "kind": kind,
                  "phase": "train", "plan_signature": plan["signature"],
                  "status": "completed", "terminal": True,
                  "trajectory_path": raw.name, "trajectory_sha256": sha(raw),
                  "binding": {"passed": True}, "primary_score": score,
                  "full_observation": score}
        rp = attempt / "result.json"
        rp.write_text(json.dumps(result), encoding="utf-8")
        paths.append(rp)
    original_json_load = json.load
    live_payloads = []
    peak_live_payloads = 0

    class TrackedPayload(dict):
        pass

    def tracked_json_load(stream, *args, **kwargs):
        nonlocal peak_live_payloads
        payload = TrackedPayload(original_json_load(stream, *args, **kwargs))
        live_payloads.append(weakref.ref(payload))
        peak_live_payloads = max(
            peak_live_payloads, sum(reference() is not None for reference in live_payloads))
        return payload

    monkeypatch.setattr(neural.json, "load", tracked_json_load)
    rows, audit = collect_outcome_ranked_examples(
        mp, paths, policy_ids=("secure_milp", "candidate", "candidate_tie"))
    assert peak_live_payloads <= 2  # One current raw plus at most one previous local.
    assert len(rows) == 1
    assert rows[0]["target_residual"][4] == 1.0
    assert rows[0]["legal_port_mask"][4]
    assert audit["grid0_diesel_nonzero_decisions"] == 0  # request, not prior execution
    assert type(audit["grid0_diesel_nonzero_decisions"]) is int
    json.dumps(audit, allow_nan=False)
    assert audit["root_rankings"][0]["partial_pressure_support"] is True
    assert audit["root_rankings"][0]["ranking"]["winner_policy_id"] == "candidate"
    assert audit["source_registered_plans"] == [{
        "plan_path": str((results / "registered_plan.json").resolve()),
        "plan_sha256": sha(results / "registered_plan.json"),
        "plan_signature": plan["signature"],
        "phase": "train", "scenario_ids": ["grid_0"],
    }]
    tampered = json.loads(paths[1].read_text(encoding="utf-8"))
    tampered["primary_score"]["targets"][0] = 1.0
    paths[1].write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="Stored score differs from raw"):
        collect_outcome_ranked_examples(
            mp, paths, policy_ids=("secure_milp", "candidate", "candidate_tie"))


def test_fit_failure_keeps_attempt_receipt_without_cpu_training(tmp_path):
    attempt = tmp_path / "failed_cuda_fit"
    with pytest.raises(FileNotFoundError):
        fit_outcome_ranked(tmp_path / "missing_manifest.json", [], attempt,
                           policy_ids=("secure_milp", "candidate"),
                           search_plan_path=tmp_path / "missing_search_plan.json")
    receipt = json.loads((attempt / "fit_error.json").read_text(encoding="utf-8"))
    assert receipt["requested_device"] == "cuda:0"
    assert receipt["error_type"] == "FileNotFoundError"
    assert not (attempt / "best.pt").exists()
