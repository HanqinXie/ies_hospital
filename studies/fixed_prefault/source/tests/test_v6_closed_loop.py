"""v6 paired registration, source binding and lossless attempt accounting."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.dataset_manifest import digest

MANIFEST = ROOT / "outputs/v5_policy_search_20261015_run01/new_data/global_manifest.json"


def _train_case():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return next(scene["id"] for scene in manifest["scenarios"]
                if scene["experiment_role"] == "train")


def _register(tmp_path, policy_specs=None, manifest_path=MANIFEST):
    from tools.policy_redesign.closed_loop import register_batch

    preference = tmp_path / "research_preference.json"
    preference.write_text('{"p0_first":true}', encoding="utf-8")
    plan = tmp_path / "registered_plan.json"
    specs = policy_specs or [
        {"policy_id": "secure_milp", "kind": "secure"},
        {"policy_id": "v6_port", "kind": "v6_feedback", "family": "port_residual",
         "theta": [0.3, 0, 0, 0, 0, 0]},
    ]
    registered = register_batch(manifest_path, plan, scenario_ids=[_train_case()],
                                policy_specs=specs, preference_path=preference,
                                phase="train")
    return plan, preference, registered


def test_v6_registration_binds_real_manifest_inputs_source_and_policy(tmp_path):
    from tools.policy_redesign.closed_loop import verify_batch

    plan_path, preference, plan = _register(tmp_path)
    assert plan["registered_pair_count"] == 2
    assert plan["final_blind_opened"] is False
    assert "tools/policy_redesign/policy.py" in plan["source_sha256"]
    assert "tools/policy_redesign/closed_loop.py" in plan["source_sha256"]
    assert "tools/reliability_first/policy.py" in plan["source_sha256"]
    assert plan["physical_input_inventory_sha256"]
    assert verify_batch(plan_path)[0] == plan
    preference.write_text('{"p0_first":false}', encoding="utf-8")
    with pytest.raises(ValueError, match="preference"):
        verify_batch(plan_path)


def test_v6_sealed_final_background_refused_even_if_manifest_is_resigned(tmp_path):
    from tools.policy_redesign.closed_loop import register_batch

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sid = _train_case()
    scene = next(scene for scene in manifest["scenarios"] if scene["id"] == sid)
    scene["is_final_blind"] = True
    manifest["splits"][sid]["is_final_blind"] = True
    manifest["signature"] = digest({k: v for k, v in manifest.items() if k != "signature"})
    modified = tmp_path / "marked_final.json"
    modified.write_text(json.dumps(manifest), encoding="utf-8")
    preference = tmp_path / "preference.json"
    preference.write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError, match="sealed"):
        register_batch(modified, tmp_path / "plan.json", scenario_ids=[sid],
                       policy_specs=[{"policy_id": "secure_milp", "kind": "secure"}],
                       preference_path=preference, phase="train")


def test_v6_model_bytes_are_frozen_at_registration(tmp_path):
    from tools.policy_redesign.closed_loop import verify_batch

    model = tmp_path / "model"
    model.mkdir()
    weights = model / "weights.bin"
    weights.write_bytes(b"original-weights")
    specs = [
        {"policy_id": "secure_milp", "kind": "secure"},
        {"policy_id": "old_guard", "kind": "strict_guard", "model_dir": str(model)},
    ]
    plan_path, _, plan = _register(tmp_path, specs)
    assert plan["model_bindings"]["old_guard"]["files"]["weights.bin"]
    weights.write_bytes(b"changed-weights")
    with pytest.raises(ValueError, match="Model bytes"):
        verify_batch(plan_path)


def test_v6_runner_preserves_exception_then_terminal_unknown_without_zero_fill(
        tmp_path, monkeypatch):
    from tools.policy_redesign import closed_loop as runner

    specs = [{"policy_id": "secure_milp", "kind": "secure"}]
    plan_path, _, _ = _register(tmp_path, specs)
    monkeypatch.setattr(runner, "HospitalIESEnv", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "_factory", lambda spec: SimpleNamespace(policy_id=spec["policy_id"]))
    monkeypatch.setattr(runner, "window_targets",
                        lambda payload, start, end, env: {"full_horizon": False,
                                                           "targets": None})
    monkeypatch.setattr(runner, "_payload_binding", lambda payload, scene, manifest:
                        {"passed": True, "initial_state_sha256": "same",
                         "exogenous_stream_id": scene["exogenous_stream_id"]})
    monkeypatch.setattr(runner, "_command_counts", lambda payload: {})
    calls = []

    def episode(env, scene, policy, seed, manifest, horizons):
        calls.append(scene["id"])
        if len(calls) == 1:
            raise RuntimeError("saved solver exception")
        return {"scenario": copy.deepcopy(scene), "summary": {
                    "status": "bounded_observation_unknown", "observed_until_minute": 90.0},
                "decisions": [], "error": None}

    monkeypatch.setattr(runner, "run_policy_episode", episode)
    out = tmp_path / "results"
    first = runner.run_batch(plan_path, out, max_new_pairs=1)
    assert first["status_counts"] == {"runner_exception": 1}
    assert first["terminal_pair_count"] == 0
    second = runner.run_batch(plan_path, out, max_new_pairs=1)
    assert second["status_counts"] == {"bounded_observation_unknown": 1}
    assert second["unknown_primary_pair_count"] == 1
    assert second["terminal_pair_count"] == 1
    assert runner.run_batch(plan_path, out, max_new_pairs=1)["attempted_this_call"] == 0
    assert len(calls) == 2
    pair = next((out / "pairs").iterdir())
    assert (pair / "attempt_0001/error.json").is_file()
    assert (pair / "attempt_0001/result.json").is_file()
    assert (pair / "attempt_0002/trajectory.json.gz").is_file()
    assert (pair / "attempt_0002/result.json").is_file()
    events = [json.loads(line) for line in (out / "attempts.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["started", "finished"] * 2


def test_v6_resume_recovers_interrupted_attempt_and_keeps_its_raw_bytes(
        tmp_path, monkeypatch):
    from tools.policy_redesign import closed_loop as runner

    specs = [{"policy_id": "secure_milp", "kind": "secure"}]
    plan_path, _, plan = _register(tmp_path, specs)
    sid = plan["scenario_ids"][0]
    out = tmp_path / "results"
    out.mkdir()
    (out / "registered_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    pair = runner._pair_path(out, sid, "secure_milp")
    orphan = pair / "attempt_0001"
    orphan.mkdir(parents=True)
    raw = orphan / "trajectory.json.gz"
    raw.write_bytes(b"preserve partial attempt bytes")
    runner._journal(out / "attempts.jsonl", event="started", scenario_id=sid,
                    policy_id="secure_milp", attempt=orphan.name, phase="train")

    monkeypatch.setattr(runner, "HospitalIESEnv", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "_factory", lambda spec: SimpleNamespace(policy_id=spec["policy_id"]))
    monkeypatch.setattr(runner, "window_targets",
                        lambda payload, start, end, env: {"full_horizon": False,
                                                           "targets": None})
    monkeypatch.setattr(runner, "_payload_binding", lambda payload, scene, manifest:
                        {"passed": True, "initial_state_sha256": "same",
                         "exogenous_stream_id": scene["exogenous_stream_id"]})
    monkeypatch.setattr(runner, "_command_counts", lambda payload: {})
    monkeypatch.setattr(runner, "run_policy_episode", lambda env, scene, policy, seed,
                        manifest, horizons: {"scenario": copy.deepcopy(scene),
                                             "summary": {"status": "bounded_unknown"},
                                             "decisions": [], "error": None})
    summary = runner.run_batch(plan_path, out, max_new_pairs=1)
    recovered = json.loads((orphan / "result.json").read_text(encoding="utf-8"))
    assert recovered["status"] == "runner_interrupted"
    assert recovered["terminal"] is False
    assert recovered["trajectory_sha256"] == runner._sha(raw)
    assert (orphan / "error.json").is_file()
    assert raw.read_bytes() == b"preserve partial attempt bytes"
    assert (pair / "attempt_0002/result.json").is_file()
    assert summary["terminal_pair_count"] == 1
    events = [json.loads(line) for line in (out / "attempts.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["started", "recovered", "started", "finished"]
