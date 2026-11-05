"""Independent regime runner registration, bindings and attempt preservation."""
from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.dataset_manifest import digest

MANIFEST = ROOT / "outputs/v5_policy_search_20261015_run01/new_data/global_manifest.json"


def _module():
    return importlib.import_module("tools.policy_redesign.regime_closed_loop")


def _train_scene():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return next(scene["id"] for scene in manifest["scenarios"]
                if scene["experiment_role"] == "train")


def _register(tmp_path, specs=None):
    runner = _module()
    preference = tmp_path / "preference.json"
    preference.write_text('{"p0_first":true}', encoding="utf-8")
    specs = specs or [{"policy_id": "secure_milp", "kind": "secure"},
                      {"policy_id": "regime_probe", "kind": "regime_feedback",
                       "theta": [0.0] * 6 + [0.3] * 6 + [0.0] * 6}]
    plan_path = tmp_path / "registered_plan.json"
    plan = runner.register_regime_batch(
        MANIFEST, plan_path, scenario_ids=[_train_scene()],
        policy_specs=specs, preference_path=preference, phase="train")
    return plan_path, preference, plan


def test_regime_batch_binds_distinct_runner_policy_and_all_original_inputs(tmp_path):
    runner = _module()
    plan_path, preference, plan = _register(tmp_path)
    assert plan["registered_pair_count"] == 2
    assert plan["final_blind_opened"] is False
    for name in ("tools/policy_redesign/regime_policy.py",
                 "tools/policy_redesign/regime_closed_loop.py",
                 "tools/policy_redesign/policy.py",
                 "tools/policy_redesign/closed_loop.py",
                 "tools/control_improvement/episode.py"):
        assert name in plan["source_sha256"]
    assert plan["physical_input_inventory_sha256"]
    assert runner.verify_regime_batch(plan_path)[0] == plan
    preference.write_text('{"p0_first":false}', encoding="utf-8")
    with pytest.raises(ValueError, match="preference"):
        runner.verify_regime_batch(plan_path)


def test_regime_batch_refuses_sealed_final_even_in_resigned_manifest(tmp_path):
    runner = _module()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sid = _train_scene()
    scene = next(scene for scene in manifest["scenarios"] if scene["id"] == sid)
    scene["is_final_blind"] = True
    manifest["splits"][sid]["is_final_blind"] = True
    manifest["signature"] = digest({k: v for k, v in manifest.items()
                                    if k != "signature"})
    modified = tmp_path / "resigned.json"
    modified.write_text(json.dumps(manifest), encoding="utf-8")
    pref = tmp_path / "pref.json"
    pref.write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError, match="sealed"):
        runner.register_regime_batch(
            modified, tmp_path / "plan.json", scenario_ids=[sid],
            policy_specs=[{"policy_id": "secure_milp", "kind": "secure"}],
            preference_path=pref, phase="train")


def test_regime_runner_retains_exception_then_terminal_unknown_without_zero(tmp_path, monkeypatch):
    runner = _module()
    plan_path, _, plan = _register(tmp_path,
        specs=[{"policy_id": "secure_milp", "kind": "secure"}])
    monkeypatch.setattr(runner, "HospitalIESEnv", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "_factory", lambda spec: SimpleNamespace(policy_id=spec["policy_id"]))
    monkeypatch.setattr(runner, "window_targets", lambda *args, **kwargs:
                        {"full_horizon": False, "targets": [None] * 19})
    monkeypatch.setattr(runner, "_payload_binding", lambda payload, scene, manifest:
                        {"passed": True, "initial_state_sha256": "same",
                         "exogenous_stream_id": "same_stream"})
    monkeypatch.setattr(runner, "_command_counts", lambda payload: {})
    calls = []

    def episode(env, scene, policy, seed, manifest, horizons):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("solver exception")
        return {"scenario": copy.deepcopy(scene), "summary": {
            "status": "bounded_observation_unknown", "observed_until_minute": 90.0},
            "decisions": [], "error": None}

    monkeypatch.setattr(runner, "run_policy_episode", episode)
    out = tmp_path / "results"
    first = runner.run_regime_batch(plan_path, out, max_new_pairs=1)
    assert first["status_counts"] == {"runner_exception": 1}
    assert first["terminal_pair_count"] == 0
    second = runner.run_regime_batch(plan_path, out, max_new_pairs=1)
    assert second["status_counts"] == {"bounded_observation_unknown": 1}
    assert second["terminal_pair_count"] == 1
    assert second["unknown_primary_pair_count"] == 1
    assert second["results"][0]["primary_score"]["targets"] == [None] * 19
    assert runner.run_regime_batch(plan_path, out, max_new_pairs=1)["attempted_this_call"] == 0
    pair = next((out / "pairs").iterdir())
    assert (pair / "attempt_0001/error.json").is_file()
    assert (pair / "attempt_0001/result.json").is_file()
    assert (pair / "attempt_0002/trajectory.json.gz").is_file()
    assert (pair / "attempt_0002/result.json").is_file()
    assert len(calls) == 2


def test_regime_runner_reports_unmatched_initial_state_not_matched(tmp_path, monkeypatch):
    runner = _module()
    plan_path, _, plan = _register(tmp_path)
    monkeypatch.setattr(runner, "HospitalIESEnv", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "_factory", lambda spec: SimpleNamespace(policy_id=spec["policy_id"]))
    monkeypatch.setattr(runner, "window_targets", lambda *args, **kwargs:
                        {"full_horizon": False, "targets": [None] * 19})
    monkeypatch.setattr(runner, "_payload_binding", lambda payload, scene, manifest:
                        {"passed": True, "initial_state_sha256":
                         payload["identity"]["policy_id"],
                         "exogenous_stream_id": "same_stream"})
    monkeypatch.setattr(runner, "_command_counts", lambda payload: {})

    def episode(env, scene, policy, seed, manifest, horizons):
        return {"scenario": copy.deepcopy(scene),
                "identity": {"policy_id": policy.policy_id},
                "summary": {"status": "bounded_observation_unknown"},
                "decisions": [], "error": None}

    monkeypatch.setattr(runner, "run_policy_episode", episode)
    summary = runner.run_regime_batch(plan_path, tmp_path / "results")
    assert summary["registered_pair_count"] == 2
    assert summary["terminal_pair_count"] == 2
    assert summary["matched_initial_state_and_stream"] is False
