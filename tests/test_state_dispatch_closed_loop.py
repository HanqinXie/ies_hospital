"""State-dispatch registration and saved-raw audit without physical rollouts."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.dataset_manifest import digest

MANIFEST = ROOT / "outputs/v5_policy_search_20261015_run01/new_data/global_manifest.json"
BASE = [{"policy_id": "secure_milp", "kind": "secure"},
        {"policy_id": "priority_milp", "kind": "priority"}]


def _module():
    return importlib.import_module("tools.state_dispatch.closed_loop")


def test_state_dispatch_runner_exports_four_entrypoints():
    try:
        module = _module()
    except ModuleNotFoundError:
        module = None
    assert module is not None, "State-dispatch registered runner is not implemented"
    assert all(callable(getattr(module, name, None)) for name in
               ("register_batch", "verify_batch", "run_batch", "audit_batch"))


@pytest.fixture
def runner(tmp_path, monkeypatch):
    module = _module()
    source_root = tmp_path / "source_tree"
    for name in module.SOURCE_FILES:
        path = source_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("registered source fixture: " + name, encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", source_root)
    stub = ModuleType("tools.state_dispatch.policy")

    class StateDispatchPolicy:
        def __init__(self, config, policy_id):
            if not isinstance(config, dict):
                raise ValueError("config must be a dict")
            self.config = copy.deepcopy(config)
            self.policy_id = policy_id

    stub.StateDispatchPolicy = StateDispatchPolicy
    monkeypatch.setitem(sys.modules, "tools.state_dispatch.policy", stub)
    return module


def _register(runner, tmp_path, specs=None, manifest_path=MANIFEST):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    sid = next(scene["id"] for scene in manifest["scenarios"]
               if scene["experiment_role"] == "train")
    pref = tmp_path / "preference.json"
    pref.write_text('{"p0_first":true}', encoding="utf-8")
    path = tmp_path / "registered_plan.json"
    specs = BASE + [{"policy_id": "state_unit", "kind": "state_dispatch",
                     "config": {"thermal_prepare": True}}] if specs is None else specs
    plan = runner.register_batch(
        manifest_path, path, scenario_ids=[sid], policy_specs=specs,
        preference_path=pref, phase="train")
    return path, pref, plan


def _mock_episode_dependencies(runner, monkeypatch):
    monkeypatch.setattr(runner, "HospitalIESEnv", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "window_targets", lambda *args, **kwargs:
                        {"full_horizon": False, "targets": [None] * 19})
    monkeypatch.setattr(runner, "_payload_binding", lambda payload, scene, manifest:
                        {"passed": True, "initial_state_sha256": "same",
                         "exogenous_stream_id": "same_stream"})
    monkeypatch.setattr(runner, "_command_counts", lambda payload: {})

    def episode(env, scene, policy, seed, manifest, horizons):
        return {"scenario": copy.deepcopy(scene), "identity": {"policy_id": policy.policy_id},
                "summary": {"status": "bounded_observation_unknown",
                            "observed_until_minute": 90.0}, "decisions": [], "error": None}

    monkeypatch.setattr(runner, "run_policy_episode", episode)
    return episode


def test_registration_binds_fixed_source_list_and_unchanged_baselines(runner, tmp_path):
    path, _, plan = _register(runner, tmp_path)
    assert plan["registered_pair_count"] == 3
    assert plan["policy_specs"][:2] == BASE
    assert plan["final_blind_opened"] is False
    assert plan["score_windows"] == {"primary": [60, 420], "full": [0, 600]}
    for name in ("tools/state_dispatch/closed_loop.py", "tools/state_dispatch/policy.py",
                 "tools/policy_redesign/regime_closed_loop.py",
                 "tools/policy_redesign/audit.py", "tools/control_improvement/episode.py",
                 "tools/control_improvement/branches.py"):
        assert name in plan["source_sha256"]
    unrelated = runner.ROOT / "tools/state_dispatch/later_analysis.py"
    unrelated.write_text("later report", encoding="utf-8")
    assert runner.verify_batch(path)[0] == plan
    policy_source = runner.ROOT / "tools/state_dispatch/policy.py"
    policy_source.write_text("changed after registration", encoding="utf-8")
    with pytest.raises(ValueError, match="source bytes changed"):
        runner.verify_batch(path)


@pytest.mark.parametrize("spec", [
    {"policy_id": "secure_milp", "kind": "secure", "config": {}},
    {"policy_id": "renamed", "kind": "priority"},
    {"policy_id": "priority_milp", "kind": "state_dispatch", "config": {}},
    {"policy_id": "state_unit", "kind": "state_dispatch", "config": []},
    {"policy_id": "state_unit", "kind": "state_dispatch"},
    {"policy_id": "state_unit", "kind": "state_dispatch", "config": {}, "theta": []},
])
def test_registration_rejects_altered_baseline_or_config_shape(runner, tmp_path, spec):
    with pytest.raises((ValueError, TypeError)):
        _register(runner, tmp_path, specs=[BASE[0], spec])


def test_registration_refuses_sealed_final_without_opening_any_actual_final(runner, tmp_path):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scene = next(scene for scene in manifest["scenarios"] if scene["experiment_role"] == "train")
    scene["is_final_blind"] = True
    manifest["splits"][scene["id"]]["is_final_blind"] = True
    manifest["signature"] = digest({k: v for k, v in manifest.items() if k != "signature"})
    path = tmp_path / "resigned.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="sealed"):
        _register(runner, tmp_path, manifest_path=path)


def test_preference_and_immutable_plan_are_checked(runner, tmp_path):
    path, pref, _ = _register(runner, tmp_path)
    with pytest.raises(FileExistsError):
        _register(runner, tmp_path)
    pref.write_text('{"p0_first":false}', encoding="utf-8")
    with pytest.raises(ValueError, match="preference"):
        runner.verify_batch(path)


def test_factory_delays_new_policy_import_and_keeps_baseline_requests_exact(runner):
    record = {"decision_time_min": 0, "features": {}, "feature_metadata": {}}
    assert runner._factory(BASE[0]).decide([record])[0] == {
        "security": {"gt_loading_penalty_USD_per_kWh": .35}}
    assert runner._factory(BASE[1]).decide([record])[0] == {}
    assert runner._factory({"policy_id": "state_unit", "kind": "state_dispatch",
                            "config": {"a": 1}}).config == {"a": 1}


def test_runner_keeps_exception_unknown_raw_and_never_retries_terminal(runner, tmp_path, monkeypatch):
    path, _, _ = _register(runner, tmp_path, specs=[BASE[0]])
    episode = _mock_episode_dependencies(runner, monkeypatch)
    calls = []

    def fail_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("retained solver exception")
        return episode(*args, **kwargs)

    monkeypatch.setattr(runner, "run_policy_episode", fail_once)
    out = tmp_path / "results"
    first = runner.run_batch(path, out, max_new_pairs=1)
    assert first["status_counts"] == {"runner_exception": 1}
    assert first["terminal_pair_count"] == 0
    second = runner.run_batch(path, out, max_new_pairs=1)
    assert second["terminal_pair_count"] == 1
    assert second["unknown_primary_pair_count"] == 1
    assert second["results"][0]["primary_score"]["targets"] == [None] * 19
    pair = next((out / "pairs").iterdir())
    assert (pair / "attempt_0001/error.json").is_file()
    assert (pair / "attempt_0002/trajectory.json.gz").is_file()
    assert runner.run_batch(path, out)["attempted_this_call"] == 0
    assert len(calls) == 2
    assert second["schema"].startswith("state_dispatch.")


def test_audit_reuses_saved_raw_recomputation_without_mutating_old_verify(runner, tmp_path, monkeypatch):
    path, _, _ = _register(runner, tmp_path)
    _mock_episode_dependencies(runner, monkeypatch)
    out = tmp_path / "results"
    runner.run_batch(path, out)
    original = importlib.import_module("tools.policy_redesign.audit")
    old_verify = original.verify_batch
    report = runner.audit_batch(path, out, include_action_effect=False)
    assert original.verify_batch is old_verify
    assert report["passed"] is True
    assert report["registered_pair_count"] == report["recomputed_pair_count"] == 3
    assert len(report["unknown_primary_pair_ids"]) == 3
    assert report["unknown_is_null_not_zero"] is True
    pair = next((out / "pairs").iterdir())
    raw = pair / "attempt_0001/trajectory.json.gz"
    raw.write_bytes(raw.read_bytes() + b"tampered")
    changed = runner.audit_batch(path, out, include_action_effect=False)
    assert changed["passed"] is False
    assert any("SHA" in error for error in changed["errors"])


def test_cli_has_register_verify_run_audit_and_reports_audit_failure(runner, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(runner, "verify_batch", lambda path: ({
        "signature": "signed", "registered_pair_count": 3}, {}))
    assert runner.main(["verify", "--plan", str(tmp_path / "plan.json")]) == 0
    assert json.loads(capsys.readouterr().out)["registered_pair_count"] == 3

    def audit(*args, **kwargs):
        calls.append(kwargs)
        return {"passed": False, "registered_pair_count": 3, "recomputed_pair_count": 2,
                "unknown_primary_pair_ids": ["s/p"], "errors": ["raw SHA differs"]}

    monkeypatch.setattr(runner, "audit_batch", audit)
    assert runner.main(["audit", "--plan", "p.json", "--out", "results",
                        "--json", "audit.json", "--markdown", "audit.md",
                        "--skip-action-effect"]) == 1
    assert calls[0]["include_action_effect"] is False
