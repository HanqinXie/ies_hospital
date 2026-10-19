"""Immutable matched-pair closed-loop receipts without rerunning the simulator."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

def _digest(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _manifest(path):
    scenarios = [
        {"id": "new_dev_a", "experiment_role": "development_boundary", "root_family_id": "root_a"},
        {"id": "new_dev_b", "experiment_role": "development_boundary", "root_family_id": "root_b"},
        {"id": "train_a", "experiment_role": "pilot_train", "root_family_id": "root_train"},
        {"id": "sealed_final", "experiment_role": "development_boundary", "root_family_id": "root_final", "is_final_blind": True},
    ]
    body = {"manifest_version": "control_improvement.fixed_new300.v1", "scenarios": scenarios,
            "splits": {s["id"]: {"group_id": s["root_family_id"],
                                  "experiment_role": s["experiment_role"],
                                  "is_final_blind": bool(s.get("is_final_blind"))} for s in scenarios},
            "options": {"seed": 20261015}, "execution_contract": {"name": "test"},
            "execution_contract_id": "contract_test", "simulator_hash": "physical_test"}
    body["signature"] = _digest(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return body


def _specs():
    return [{"policy_id": "secure_milp", "kind": "baseline"},
            {"policy_id": "secure_identity", "kind": "identity"}]


def test_register_restricts_formal_development_and_binds_manifest(tmp_path, monkeypatch):
    import tools.reliability_first.closed_loop as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "verify_manifest", lambda manifest: True)
    manifest_path = tmp_path / "new_data" / "global_manifest.json"
    _manifest(manifest_path)
    with pytest.raises(ValueError, match="role"):
        module.register_closed_loop(manifest_path, tmp_path / "invalid_train.json",
                                    scenario_ids=["train_a"], policy_specs=_specs())
    with pytest.raises(ValueError, match="final"):
        module.register_closed_loop(manifest_path, tmp_path / "invalid_final.json",
                                    scenario_ids=["sealed_final"], policy_specs=_specs())
    plan = module.register_closed_loop(manifest_path, tmp_path / "plan.json",
                                       scenario_ids=["new_dev_a", "new_dev_b"], policy_specs=_specs())
    assert plan["manifest_sha256"] == module.sha(manifest_path)
    assert plan["scenario_ids"] == ["new_dev_a", "new_dev_b"]
    assert plan["maximum_trajectory_attempts"] == 4
    assert plan["score_windows"] == {"primary": [60.0, 420.0], "full": [0.0, 600.0]}
    assert {"tools/reliability_first/closed_loop.py",
            "tools/reliability_first/run_eval.py",
            "tools/reliability_first/selection.py",
            "tools/reliability_first/calibration.py",
            "tools/reliability_first/neural.py",
            "tools/reliability_first/tree.py",
            "tools/control_improvement/candidates.py",
            "tools/control_improvement/ranking.py"} <= set(plan["source_sha256"])


def test_resume_rechecks_registered_selection_source_before_skipping_completed_pairs(tmp_path, monkeypatch):
    import tools.reliability_first.closed_loop as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "verify_manifest", lambda manifest: True)
    manifest_path = tmp_path / "new_data" / "global_manifest.json"
    _manifest(manifest_path)
    plan_path = tmp_path / "plan.json"
    module.register_closed_loop(manifest_path, plan_path,
                                scenario_ids=["new_dev_a"], policy_specs=[_specs()[0]])
    calls, factory = _fake_runner(monkeypatch, module)
    out = tmp_path / "closed"
    module.run_closed_loop(plan_path, out, policy_factory=factory)
    assert len(calls) == 1
    original = module._source_binding

    def modified_selection():
        binding = original()
        binding["tools/reliability_first/selection.py"] = "changed"
        return binding

    monkeypatch.setattr(module, "_source_binding", modified_selection)
    with pytest.raises(ValueError, match="source"):
        module.run_closed_loop(plan_path, out, policy_factory=factory)
    assert len(calls) == 1


def test_resume_rechecks_frozen_new_design_files(tmp_path, monkeypatch):
    import tools.reliability_first.closed_loop as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "verify_manifest", lambda manifest: True)
    manifest_path = tmp_path / "new_data" / "global_manifest.json"
    _manifest(manifest_path)
    design_path = tmp_path / "design" / "source_proposal.json"
    design_path.parent.mkdir()
    design_path.write_text('{"registered":true}', encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    module.register_closed_loop(manifest_path, plan_path,
                                scenario_ids=["new_dev_a"], policy_specs=[_specs()[0]],
                                design_binding={"design/source_proposal.json": module.sha(design_path)})
    calls, factory = _fake_runner(monkeypatch, module)
    out = tmp_path / "closed"
    module.run_closed_loop(plan_path, out, policy_factory=factory)
    design_path.write_text('{"registered":false}', encoding="utf-8")
    with pytest.raises(ValueError, match="design"):
        module.run_closed_loop(plan_path, out, policy_factory=factory)
    assert len(calls) == 1


def test_formal_registration_requires_frozen_design_and_independent_gate(tmp_path, monkeypatch):
    import tools.reliability_first.closed_loop as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "verify_manifest", lambda manifest: True)
    manifest_path = tmp_path / "new_data" / "global_manifest.json"
    _manifest(manifest_path)
    with pytest.raises(ValueError, match="design"):
        module.register_closed_loop(manifest_path, tmp_path / "no_design.json",
                                    scenario_ids=["new_dev_a"], policy_specs=_specs(),
                                    registration_mode="formal_development")
    design_path = tmp_path / "design" / "source_proposal.json"
    design_path.parent.mkdir()
    design_path.write_text('{"registered":true}', encoding="utf-8")
    model_dir = tmp_path / "nn"
    model_dir.mkdir()
    (model_dir / "model.pt").write_bytes(b"test model")
    specs = _specs()[:1] + [{"policy_id": "nn_gated", "kind": "gated",
                            "model_dir": "nn", "gate": {"error_multiplier": 1.0}}]
    with pytest.raises(ValueError, match="calibration"):
        module.register_closed_loop(manifest_path, tmp_path / "no_calibration.json",
                                    scenario_ids=["new_dev_a"], policy_specs=specs,
                                    registration_mode="formal_development",
                                    design_binding={"design/source_proposal.json": module.sha(design_path)})


def _fake_payload(manifest, scenario, policy, *, status="completed"):
    sid = scenario["id"]
    return {"identity": {"scenario_id": sid, "simulator_hash": manifest["simulator_hash"],
                         "execution_contract_id": manifest["execution_contract_id"],
                         "manifest_signature": manifest["signature"],
                         "group_id": manifest["splits"][sid]["group_id"],
                         "exogenous_stream_id": "stream:" + sid},
            "scenario": scenario, "seed": manifest["options"]["seed"],
            "initial_state": {"same_original_state": sid},
            "summary": {"status": status, "observed_until_minute": 600.0 if status == "completed" else 75.0},
            "decisions": [{"decision_time_min": 0.0, "requested_command": {"security": {}},
                           "command_generated": {"id": 1, "dispatch": {"gt_electric_kW": [0.0, 0.0, 0.0]}},
                           "policy_diagnostic": {"strategy_fallback": policy.policy_id == "secure_identity"}}],
            "events": [{"kind": "command_generated", "command_id": 1},
                       {"kind": "command_arrived", "command_id": 1},
                       {"kind": "command_executed", "command_id": 1}],
            "segments": [], "solver_calls": [], "solver_errors": [], "error": None}


def _fake_runner(monkeypatch, module, *, fail_first=False, unknown_policy=None):
    class FakeEnv:
        def __init__(self, root, *, config=None, execution_contract=None):
            self.root = root
            self.config = config
            self.contract = execution_contract

    class FakePolicy:
        def __init__(self, policy_id):
            self.policy_id = policy_id

    calls = []

    def run(env, scenario, policy, seed, manifest, **_kwargs):
        calls.append((scenario["id"], policy.policy_id))
        if fail_first and len(calls) == 1:
            raise RuntimeError("interrupted before raw trajectory")
        status = "model_constraint_infeasible" if policy.policy_id == unknown_policy else "completed"
        return _fake_payload(manifest, scenario, policy, status=status)

    def score(payload, start, horizon, env):
        full = payload["summary"]["status"] == "completed"
        return {"start_minute": start, "horizon_minutes": horizon,
                "full_horizon": full, "P0_failure_in_window": 0 if full else None,
                "targets": [0.0] * 19 if full else [None] * 19}

    monkeypatch.setattr(module, "HospitalIESEnv", FakeEnv)
    monkeypatch.setattr(module, "run_policy_episode", run)
    monkeypatch.setattr(module, "window_targets", score)
    return calls, lambda spec: FakePolicy(spec["policy_id"])


def test_resumable_run_saves_complete_compressed_raw_and_skips_finished_pairs(tmp_path, monkeypatch):
    import tools.reliability_first.closed_loop as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "verify_manifest", lambda manifest: True)
    manifest_path = tmp_path / "new_data" / "global_manifest.json"
    _manifest(manifest_path)
    plan_path = tmp_path / "plan.json"
    module.register_closed_loop(manifest_path, plan_path,
                                scenario_ids=["new_dev_a", "new_dev_b"], policy_specs=_specs())
    calls, factory = _fake_runner(monkeypatch, module)
    out = tmp_path / "closed"
    first = module.run_closed_loop(plan_path, out, policy_factory=factory)
    assert first["attempted_this_call"] == 4 and first["completed_pair_count"] == 4
    assert first["matched_initial_state_passed"] is True
    raw_paths = list((out / "pairs").glob("*/attempt_0001/trajectory.json.gz"))
    assert len(raw_paths) == 4
    with gzip.open(raw_paths[0], "rt", encoding="utf-8") as stream:
        assert json.load(stream)["summary"]["status"] == "completed"
    second = module.run_closed_loop(plan_path, out, policy_factory=factory)
    assert second["attempted_this_call"] == 0 and len(calls) == 4
    assert len((out / "attempts.jsonl").read_text(encoding="utf-8").splitlines()) == 8


def test_unknown_trajectory_is_retained_and_not_counted_as_complete(tmp_path, monkeypatch):
    import tools.reliability_first.closed_loop as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "verify_manifest", lambda manifest: True)
    manifest_path = tmp_path / "new_data" / "global_manifest.json"
    _manifest(manifest_path)
    plan_path = tmp_path / "plan.json"
    module.register_closed_loop(manifest_path, plan_path,
                                scenario_ids=["new_dev_a"], policy_specs=_specs())
    calls, factory = _fake_runner(monkeypatch, module, unknown_policy="secure_identity")
    result = module.run_closed_loop(plan_path, tmp_path / "closed", policy_factory=factory)
    assert result["attempted_this_call"] == 2
    assert result["completed_pair_count"] == 1
    assert result["model_constraint_infeasible_count"] == 1
    assert result["unknown_primary_pair_count"] == 1
    assert len(calls) == 2


def test_interrupted_attempt_is_kept_and_resume_uses_new_attempt_directory(tmp_path, monkeypatch):
    import tools.reliability_first.closed_loop as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "verify_manifest", lambda manifest: True)
    manifest_path = tmp_path / "new_data" / "global_manifest.json"
    _manifest(manifest_path)
    plan_path = tmp_path / "plan.json"
    module.register_closed_loop(manifest_path, plan_path,
                                scenario_ids=["new_dev_a"], policy_specs=[_specs()[0]])
    calls, factory = _fake_runner(monkeypatch, module, fail_first=True)
    out = tmp_path / "closed"
    first = module.run_closed_loop(plan_path, out, policy_factory=factory)
    assert first["runner_exception_count"] == 1
    first_attempt = next((out / "pairs").glob("*/attempt_0001/error.json"))
    original = first_attempt.read_bytes()
    second = module.run_closed_loop(plan_path, out, policy_factory=factory)
    assert second["completed_pair_count"] == 1
    assert first_attempt.read_bytes() == original
    assert len(list((out / "pairs").glob("*/attempt_0002/trajectory.json.gz"))) == 1
    assert len(calls) == 2


def test_identity_pilot_compares_final_execution_state(tmp_path, monkeypatch):
    import tools.reliability_first.closed_loop as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "verify_manifest", lambda manifest: True)
    manifest_path = tmp_path / "new_data" / "global_manifest.json"
    _manifest(manifest_path)
    _fake_runner(monkeypatch, module)
    final_states = []

    def capture(env, *, binding):
        state = {"run": len(final_states) + 1, "binding": binding}
        final_states.append(state)
        return state

    def compare(reference, identity, start, *, reference_final=None, restored_final=None):
        assert reference_final is final_states[0]
        assert restored_final is final_states[1]
        return {"passed": True, "differences": []}

    monkeypatch.setattr(module, "compare_continuation", compare)
    monkeypatch.setattr(module, "capture_execution", capture, raising=False)
    report = module.run_identity_pilot(manifest_path, "new_dev_a", tmp_path / "identity")
    assert report["passed"] is True
    assert len(final_states) == 2
