"""Security-weight route has its own immutable matched runner."""
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

MANIFEST = ROOT / "outputs/v5_policy_search_20261015_run01/new_data/global_manifest.json"


def _module():
    return importlib.import_module("tools.policy_redesign.security_closed_loop")


def _train_scene():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return next(scene["id"] for scene in manifest["scenarios"]
                if scene["experiment_role"] == "train")


def _register(tmp_path, specs=None):
    runner = _module()
    pref = tmp_path / "preference.json"
    pref.write_text('{"p0_first":true}', encoding="utf-8")
    specs = specs or [{"policy_id": "secure_milp", "kind": "secure"},
                      {"policy_id": "security_unit", "kind": "security_feedback",
                       "weights": [.35, 0.0, 3.0]}]
    path = tmp_path / "registered_plan.json"
    plan = runner.register_security_batch(
        MANIFEST, path, scenario_ids=[_train_scene()], policy_specs=specs,
        preference_path=pref, phase="train")
    return path, pref, plan


def test_security_registration_binds_current_route_and_physical_inputs(tmp_path):
    runner = _module()
    path, pref, plan = _register(tmp_path)
    assert plan["registered_pair_count"] == 2
    assert plan["final_blind_opened"] is False
    for name in ("tools/policy_redesign/security_policy.py",
                 "tools/policy_redesign/security_closed_loop.py",
                 "tools/policy_redesign/regime_policy.py",
                 "tools/policy_redesign/regime_closed_loop.py",
                 "tools/policy_redesign/closed_loop.py",
                 "tools/control_improvement/episode.py"):
        assert name in plan["source_sha256"]
    assert plan["physical_input_inventory_sha256"]
    assert runner.verify_security_batch(path)[0] == plan
    pref.write_text('{"p0_first":false}', encoding="utf-8")
    with pytest.raises(ValueError, match="preference"):
        runner.verify_security_batch(path)


def test_security_runner_keeps_exception_and_terminal_unknown(tmp_path, monkeypatch):
    runner = _module()
    path, _, _ = _register(tmp_path,
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
            raise RuntimeError("solver error")
        return {"scenario": copy.deepcopy(scene), "summary": {
            "status": "bounded_observation_unknown"}, "decisions": [], "error": None}

    monkeypatch.setattr(runner, "run_policy_episode", episode)
    out = tmp_path / "results"
    first = runner.run_security_batch(path, out, max_new_pairs=1)
    assert first["terminal_pair_count"] == 0
    assert first["status_counts"] == {"runner_exception": 1}
    second = runner.run_security_batch(path, out, max_new_pairs=1)
    assert second["terminal_pair_count"] == 1
    assert second["unknown_primary_pair_count"] == 1
    assert second["results"][0]["primary_score"]["targets"] == [None] * 19
    pair = next((out / "pairs").iterdir())
    assert (pair / "attempt_0001/error.json").is_file()
    assert (pair / "attempt_0002/trajectory.json.gz").is_file()
    assert runner.run_security_batch(path, out, max_new_pairs=1)["attempted_this_call"] == 0


def test_security_runner_keeps_unmatched_initial_state_visible(tmp_path, monkeypatch):
    runner = _module()
    path, _, _ = _register(tmp_path)
    monkeypatch.setattr(runner, "HospitalIESEnv", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "_factory", lambda spec: SimpleNamespace(policy_id=spec["policy_id"]))
    monkeypatch.setattr(runner, "window_targets", lambda *args, **kwargs:
                        {"full_horizon": False, "targets": [None] * 19})
    monkeypatch.setattr(runner, "_payload_binding", lambda payload, scene, manifest:
                        {"passed": True, "initial_state_sha256":
                         payload["identity"]["policy_id"],
                         "exogenous_stream_id": "same_stream"})
    monkeypatch.setattr(runner, "_command_counts", lambda payload: {})
    monkeypatch.setattr(runner, "run_policy_episode",
        lambda env, scene, policy, seed, manifest, horizons:
        {"scenario": copy.deepcopy(scene), "identity": {"policy_id": policy.policy_id},
         "summary": {"status": "bounded_observation_unknown"},
         "decisions": [], "error": None})
    summary = runner.run_security_batch(path, tmp_path / "results")
    assert summary["terminal_pair_count"] == 2
    assert summary["matched_initial_state_and_stream"] is False
