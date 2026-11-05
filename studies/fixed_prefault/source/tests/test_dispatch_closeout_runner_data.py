"""Closeout definitions and immutable runner boundaries, without physical runs."""
from __future__ import annotations

from collections import Counter
import copy
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
SOURCE = ROOT / "outputs/v9_state_dispatch_20261016_run01/new_data/global_manifest.json"
BASE = [{"policy_id": "secure_milp", "kind": "secure"},
        {"policy_id": "priority_milp", "kind": "priority"}]


def module(name):
    try:
        result = importlib.import_module("tools.dispatch_closeout." + name)
    except ModuleNotFoundError:
        result = None
    assert result is not None, "Missing independent closeout " + name
    return result


def test_design_registers_24_development_and_16_new_perturbations():
    proposal = module("data_design").build_proposal(ROOT)
    assert Counter(s["analysis_cohort"] for s in proposal["scenarios"]) == {
        "development": 24, "holdout": 16}
    assert proposal["new_independent_background_count"] == 0
    assert proposal["final_12_backgrounds_unopened"] is True
    assert len({s["physical_scenario_signature"] for s in proposal["scenarios"]}) == 40
    assert Counter(s["analysis_class"] for s in proposal["scenarios"]
                   if s["analysis_cohort"] == "holdout") == {
        "L1": 3, "L3": 3, "L2": 4, "L4b": 4, "L4a": 2}


def test_sources_are_exact_seen_v9_definitions_with_explicit_coordinates():
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    old = {s["id"]: s for s in source["scenarios"]}
    scenes = module("data_design").build_proposal(ROOT)["scenarios"]
    assert len({s["root_family_id"] for s in scenes}) == 12
    for scene in scenes:
        origin = old[scene["source_v9_scenario_id"]]
        for field in ("root_family_id", "base_state_id", "full_source_support",
                      "grid_limit_kW", "pre_event_grid_limit_kW", "duration_steps"):
            assert scene[field] == origin[field]
        holdout = scene["analysis_cohort"] == "holdout"
        assert scene["experiment_role"] == ("confirmation" if holdout else "development")
        assert scene["selection_allowed"] is (not holdout)
        assert scene["fit_allowed"] is False
        assert scene["is_final_blind"] is False
        assert scene["registered_coordinate_change"]
        if not holdout:
            assert scene["exogenous_events"] == origin["exogenous_events"]
            assert scene["response_delay_minutes"] == origin["response_delay_minutes"]
        else:
            assert scene["response_delay_minutes"] in (23.75, 27.5)
        for event in scene["exogenous_events"]:
            assert 60 <= event["start_minute"] < event["end_minute"] <= 450


def test_confirmation_is_physically_new_and_fault_pairs_share_events():
    proposal = module("data_design").build_proposal(ROOT)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    old_physical = {s["physical_scenario_signature"] for s in source["scenarios"]}
    byid = {s["id"]: s for s in proposal["scenarios"]}
    for scene in proposal["scenarios"]:
        if scene["analysis_cohort"] == "holdout":
            assert scene["physical_scenario_signature"] not in old_physical
    assert len(proposal["matched_fault_groups"]) == 6
    for group in proposal["matched_fault_groups"]:
        a, b = (byid[group["scene_ids"][name]] for name in ("L1", "L3"))
        assert a["exogenous_events"] == b["exogenous_events"]
        assert a["base_state_id"] == b["base_state_id"]
        assert a["enable_propagation"] is False and b["enable_propagation"] is True


def test_design_reads_no_outcomes_or_final_registry(monkeypatch):
    design = module("data_design")
    originals = {name: getattr(Path, name) for name in ("read_text", "read_bytes")}
    def guarded(name):
        def read(path, *args, **kwargs):
            assert not {"results", "analysis", "final_blind"} & set(path.parts)
            if path.name == "global_manifest.json":
                assert path.resolve() == SOURCE.resolve()
            return originals[name](path, *args, **kwargs)
        return read
    for name in originals:
        monkeypatch.setattr(Path, name, guarded(name))
    assert len(design.build_proposal(ROOT)["scenarios"]) == 40


def test_manifest_freeze_is_write_once_and_current_runtime_bound(tmp_path):
    design = module("data_design")
    receipt = design.freeze(ROOT, tmp_path / "run")
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    assert receipt["episodes_started"] == 0
    assert manifest["study_name"] == "hospital_ies_dispatch_closeout_v10"
    assert len(manifest["scenarios"]) == len(manifest["splits"]) == 40
    assert manifest["policy_search_final_12_backgrounds_unopened"] is True
    from tools.policy_redesign.closed_loop import _verify_manifest
    assert _verify_manifest(manifest)
    with pytest.raises(FileExistsError):
        design.freeze(ROOT, tmp_path / "run")


def test_runner_factory_preserves_v9_and_new_identity(monkeypatch):
    runner = module("closed_loop")
    from tools.state_dispatch import policy_v2, closed_loop_v2
    spec = next(s for s in policy_v2.candidate_specs() if s["policy_id"] == "state_guarded_spread")
    assert type(runner._factory(spec)) is policy_v2.StateDispatchPolicy
    assert runner.SCHEMA != closed_loop_v2.SCHEMA
    stub = ModuleType("tools.dispatch_closeout.policy")
    class Policy:
        def __init__(self, config, policy_id):
            self.config, self.policy_id = config, policy_id
    stub.StateDispatchPolicy = Policy
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    new = {"policy_id": "execution_reserve", "kind": "state_dispatch", "config": {"a": 1}}
    assert runner._factory(new).policy_id == "execution_reserve"
    assert runner._check_specs(BASE + [spec, new])[-1] == new
    assert runner._factory(BASE[0]).policy_id == "secure_milp"
    with pytest.raises(ValueError):
        runner._factory({"policy_id": "unregistered", "kind": "state_dispatch", "config": {}})


def test_runner_registration_binds_new_sources_and_rejects_drift(tmp_path, monkeypatch):
    runner = module("closed_loop")
    sources = tmp_path / "sources"
    for name in runner.SOURCE_FILES:
        path = sources / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", sources)
    receipt = module("data_design").freeze(ROOT, tmp_path / "run")
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    pref = tmp_path / "preference.json"
    pref.write_text('{}', encoding="utf-8")
    path = tmp_path / "plan.json"
    plan = runner.register_batch(receipt["manifest_path"], path,
        scenario_ids=manifest["ids_by_cohort"]["development"][:1], policy_specs=BASE,
        preference_path=pref, phase="development")
    assert runner.verify_batch(path)[0] == plan
    assert {"tools/dispatch_closeout/closed_loop.py", "tools/dispatch_closeout/policy.py"} <= set(plan["source_sha256"])
    (sources / "tools/dispatch_closeout/policy.py").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="source bytes changed"):
        runner.verify_batch(path)


def test_closeout_saved_analysis_rejects_old_schema_before_reading_raws(tmp_path):
    runner = module("closed_loop")
    pp, ap = tmp_path / "plan.json", tmp_path / "audit.json"
    pp.write_text(json.dumps({"schema": "old"}), encoding="utf-8")
    ap.write_text(json.dumps({"schema": "old"}), encoding="utf-8")
    with pytest.raises(ValueError, match="closeout.*schema"):
        runner.analyze_batch(tmp_path / "manifest.json", pp, tmp_path / "results", ap, tmp_path / "analysis")


def test_runner_retains_unknown_and_immutable_attempts(tmp_path, monkeypatch):
    runner = module("closed_loop")
    source = tmp_path / "sources"
    for name in runner.SOURCE_FILES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", source)
    receipt = module("data_design").freeze(ROOT, tmp_path / "run")
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    pref, pp = tmp_path / "preference.json", tmp_path / "plan.json"
    pref.write_text('{}', encoding="utf-8")
    runner.register_batch(receipt["manifest_path"], pp,
        scenario_ids=manifest["ids_by_cohort"]["development"][:1], policy_specs=BASE,
        preference_path=pref, phase="development")
    monkeypatch.setattr(runner, "HospitalIESEnv", lambda *a, **kw: object())
    monkeypatch.setattr(runner, "window_targets", lambda *a, **kw:
                        {"full_horizon": False, "targets": [None] * 19})
    monkeypatch.setattr(runner, "_payload_binding", lambda *a, **kw:
        {"passed": True, "initial_state_sha256": "same", "exogenous_stream_id": "same"})
    monkeypatch.setattr(runner, "_command_counts", lambda *a: {})
    def episode(env, scene, policy, seed, manifest, horizons):
        return {"scenario": copy.deepcopy(scene), "identity": {"policy_id": policy.policy_id},
                "summary": {"status": "bounded_observation_unknown"}, "decisions": [], "error": None}
    monkeypatch.setattr(runner, "run_policy_episode", episode)
    results = tmp_path / "results"
    summary = runner.run_batch(pp, results)
    assert summary["schema"] == runner.SCHEMA + ".summary"
    assert summary["registered_pair_count"] == summary["terminal_pair_count"] == 2
    assert summary["unknown_primary_pair_count"] == 2
    assert runner.run_batch(pp, results)["attempted_this_call"] == 0
    audit = runner.audit_batch(pp, results, include_action_effect=False)
    assert audit["schema"] == runner.AUDIT_SCHEMA
    assert audit["passed"] is True
    assert len(audit["unknown_full_pair_ids"]) == 2


def test_analysis_uses_existing_raw_hash_and_unknown_semantics(tmp_path, monkeypatch):
    runner = module("closed_loop")
    fixture_spec = importlib.util.spec_from_file_location(
        "closeout_v9_raw_fixture", ROOT / "tests/test_state_dispatch_analysis.py")
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    mp, pp, results, ap = fixture.batch.__wrapped__(tmp_path)
    plan = json.loads(pp.read_text(encoding="utf-8"))
    plan["schema"] = runner.SCHEMA
    fixture._write(pp, plan)
    audit = json.loads(ap.read_text(encoding="utf-8"))
    audit.update(schema=runner.AUDIT_SCHEMA, plan_sha256=runner._sha(pp))
    fixture._write(ap, audit)
    monkeypatch.setattr(runner, "verify_batch", lambda path: (plan, {}))
    monkeypatch.setattr(runner, "_source_binding", lambda: {"wrapper": "fixture"})
    report = runner.analyze_batch(mp, pp, results, ap, tmp_path / "analysis")
    assert report["schema"] == runner.ANALYSIS_SCHEMA
    assert report["registered_trajectory_count"] == 12
    unknown = next(r for r in report["trajectory_results"]
                   if r["scenario_id"] == "s1" and r["policy_id"] == "state_b")
    assert unknown["full_P0_failure"] == 1 and unknown["full_P0_failure_minutes"] is None
    receipt = json.loads((tmp_path / "analysis/CLOSEOUT_SOURCE_BINDING.json").read_text(encoding="utf-8"))
    assert receipt["physical_trajectories_started"] == 0
    assert receipt["wrapper_source_sha256"] == runner._sha(ROOT / "tools/dispatch_closeout/closed_loop.py")
