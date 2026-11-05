"""The v2 policy route cannot alter or resume a frozen v1 namespace."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
MANIFEST = ROOT / "outputs/v5_policy_search_20261015_run01/new_data/global_manifest.json"
BASE = [{"policy_id": "secure_milp", "kind": "secure"},
        {"policy_id": "priority_milp", "kind": "priority"}]


def _module():
    return importlib.import_module("tools.state_dispatch.closed_loop_v2")


def test_v2_runner_exists_with_separate_schema():
    try:
        module = _module()
    except ModuleNotFoundError:
        module = None
    assert module is not None, "Version 2 runner is not implemented"
    from tools.state_dispatch import closed_loop as v1
    assert module.SCHEMA != v1.SCHEMA


def test_real_v2_guard_config_constructs_without_using_v1_validator():
    module = _module()
    from tools.state_dispatch import policy_v2
    for spec in policy_v2.candidate_specs():
        policy = module._factory(spec)
        assert type(policy) is policy_v2.StateDispatchPolicy
        assert policy.v2_config["cooling_guard_ratio"] in (.8, 10.)
        assert module._check_specs(BASE + [spec])[-1] == spec


@pytest.fixture
def runner(tmp_path, monkeypatch):
    module = _module()
    source = tmp_path / "sources"
    for name in module.SOURCE_FILES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", source)
    stub = ModuleType("tools.state_dispatch.policy_v2")

    class StateDispatchPolicy:
        def __init__(self, config, policy_id):
            self.config = copy.deepcopy(config)
            self.policy_id = policy_id
            self.version = 2

    stub.StateDispatchPolicy = StateDispatchPolicy
    monkeypatch.setitem(sys.modules, "tools.state_dispatch.policy_v2", stub)
    return module


def _register(runner, tmp_path):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sid = next(s["id"] for s in manifest["scenarios"] if s["experiment_role"] == "train")
    pref = tmp_path / "preference.json"
    pref.write_text('{}', encoding="utf-8")
    path = tmp_path / "plan.json"
    specs = BASE + [{"policy_id": "state_v2", "kind": "state_dispatch",
                     "config": {"cooling_guard_ratio": 1.5, "guard_weight": 0.35}}]
    plan = runner.register_batch(MANIFEST, path, scenario_ids=[sid], policy_specs=specs,
                                 preference_path=pref, phase="train")
    return path, plan


def test_v2_registration_binds_v1_and_only_explicit_v2_dependencies(runner, tmp_path):
    from tools.state_dispatch import closed_loop as v1
    path, plan = _register(runner, tmp_path)
    assert set(v1.SOURCE_FILES) | {"tools/state_dispatch/closed_loop_v2.py",
                                "tools/state_dispatch/policy_v2.py"} == set(plan["source_sha256"])
    assert plan["schema"] == runner.SCHEMA
    assert plan["policy_specs"][:2] == BASE
    assert runner.verify_batch(path)[0] == plan
    (runner.ROOT / "tools/state_dispatch/later_v2_plot.py").write_text("report", encoding="utf-8")
    assert runner.verify_batch(path)[0] == plan
    (runner.ROOT / "tools/state_dispatch/policy_v2.py").write_text("drift", encoding="utf-8")
    with pytest.raises(ValueError, match="source bytes changed"):
        runner.verify_batch(path)


def test_v2_spec_factory_constructs_new_class_and_keeps_baseline_identity(runner):
    spec = {"policy_id": "state_v2", "kind": "state_dispatch",
            "config": {"cooling_guard_ratio": 1.25, "guard_weight": 0.}}
    assert runner._check_specs(BASE + [spec])[-1] == spec
    assert runner._factory(spec).version == 2
    assert runner._factory(spec).config == spec["config"]
    record = {"decision_time_min": 0., "features": {}, "feature_metadata": {}}
    assert runner._factory(BASE[0]).decide([record])[0] == {
        "security": {"gt_loading_penalty_USD_per_kWh": .35}}
    assert runner._factory(BASE[1]).decide([record])[0] == {}


def test_v1_verifier_is_not_changed_and_rejects_v2_schema(runner, tmp_path):
    from tools.state_dispatch import closed_loop as v1
    old_verify, old_factory, old_schema = v1.verify_batch, v1._factory, v1.SCHEMA
    path, _ = _register(runner, tmp_path)
    with pytest.raises(ValueError, match="signature"):
        v1.verify_batch(path)
    assert (v1.verify_batch, v1._factory, v1.SCHEMA) == (old_verify, old_factory, old_schema)


def test_v2_run_and_saved_raw_audit_retain_unknown_without_global_patch(runner, tmp_path, monkeypatch):
    path, plan = _register(runner, tmp_path)
    monkeypatch.setattr(runner, "HospitalIESEnv", lambda *a, **kw: object())
    monkeypatch.setattr(runner, "window_targets", lambda *a, **kw:
                        {"full_horizon": False, "targets": [None] * 19})
    monkeypatch.setattr(runner, "_payload_binding", lambda *a, **kw:
                        {"passed": True, "initial_state_sha256": "same", "exogenous_stream_id": "same"})
    monkeypatch.setattr(runner, "_command_counts", lambda *a: {})
    calls = []

    def episode(env, scene, policy, seed, manifest, horizons):
        calls.append(policy.policy_id)
        return {"scenario": copy.deepcopy(scene), "identity": {"policy_id": policy.policy_id},
                "summary": {"status": "bounded_observation_unknown"}, "decisions": [], "error": None}

    monkeypatch.setattr(runner, "run_policy_episode", episode)
    out = tmp_path / "results"
    summary = runner.run_batch(path, out)
    assert summary["schema"] == runner.SCHEMA + ".summary"
    assert summary["registered_pair_count"] == summary["terminal_pair_count"] == 3
    assert summary["unknown_primary_pair_count"] == 3
    assert runner.run_batch(path, out)["attempted_this_call"] == 0
    original_audit = importlib.import_module("tools.policy_redesign.audit")
    old_verify = original_audit.verify_batch
    report = runner.audit_batch(path, out, include_action_effect=False)
    assert report["schema"] == runner.AUDIT_SCHEMA
    assert report["passed"] is True
    assert len(report["unknown_full_pair_ids"]) == 3
    assert original_audit.verify_batch is old_verify
    assert calls == [s["policy_id"] for s in plan["policy_specs"]]


def test_v2_cli_dispatches_to_v2_verify(runner, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, "verify_batch", lambda path: ({
        "signature": "v2_only", "registered_pair_count": 3}, {}))
    assert runner.main(["verify", "--plan", str(tmp_path / "plan.json")]) == 0
    assert json.loads(capsys.readouterr().out)["plan_signature"] == "v2_only"
