"""Registered local perturbations preserve seen-root source and holdout roles."""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from hospital_ies.resilience import validate_scenario
from tools.p2b_experiment.design import support_interval


V8 = ROOT / "outputs/v8_four_scenarios_deepening_20261016_run01/new_data/global_manifest.json"


def design():
    return importlib.import_module("tools.state_dispatch.data_design")


def test_local_design_has_119_specs_with_separate_development_boundary_holdout():
    proposal = design().build_proposal(ROOT)
    assert proposal["study_name"] == "hospital_ies_state_dispatch_v9"
    assert Counter(s["analysis_cohort"] for s in proposal["scenarios"]) == {
        "development": 32, "boundary": 59, "holdout": 28}
    assert len(proposal["scenarios"]) == len({s["id"] for s in proposal["scenarios"]}) == 119
    assert {k: len(v) for k, v in proposal["ids_by_cohort"].items()} == {
        "development": 32, "boundary": 59, "holdout": 28}
    assert proposal["new_independent_background_count"] == 0
    assert proposal["final_12_backgrounds_unopened"] is True
    assert proposal["design_coverage"] and proposal["missing_combinations"]


def test_source_support_and_physical_limits_are_unchanged_for_twelve_seen_roots():
    old = json.loads(V8.read_text(encoding="utf-8"))
    roots = {s["root_family_id"]: s for s in old["scenarios"]}
    proposal = design().build_proposal(ROOT)
    assert {s["root_family_id"] for s in proposal["scenarios"]} == set(roots)
    for s in proposal["scenarios"]:
        prior = roots[s["root_family_id"]]
        validate_scenario(s)
        for key in ("start_hour", "base_state_id", "time_block_id", "grid_limit_kW",
                    "pre_event_grid_limit_kW", "full_source_support"):
            assert s[key] == prior[key]
        assert s["full_source_support"] == support_interval(s)
        assert s["step_minutes"] == 15 and s["duration_steps"] == 40
        assert not s["is_final_blind"]
        assert s["sampling_weight_is_failure_probability"] is False


def test_boundary_grid_is_local_and_retains_known_negative_backgrounds():
    scenes = design().build_proposal(ROOT)["scenarios"]
    boundary = [s for s in scenes if s["analysis_cohort"] == "boundary"]
    assert Counter(s["design_axis"] for s in boundary) == {
        "heat_amplitude": 34, "response_window": 15,
        "event_phase": 6, "cool_duration": 4}
    heat = [s for s in boundary if s["design_axis"] == "heat_amplitude"
            and s["analysis_class"] == "L2"]
    for day in (99, 341, 363, 187, 298):
        rows = [s for s in heat if s["source_day_index"] == day]
        assert {s["shock"]["h"] for s in rows} == {.35, .4125, .475, .5375, .60}
        assert all(s["shock"]["e"] == s["shock"]["c"] == 0
                   and s["shock_by_priority"] == {} for s in rows)
    assert {s["response_delay_minutes"] for s in boundary
            if s["design_axis"] == "response_window"} == {15, 18.75, 22.5, 26.25, 30}


def test_holdout_roles_are_confirmation_without_selection_or_fitting():
    proposal = design().build_proposal(ROOT)
    for scene in proposal["scenarios"]:
        holdout = scene["analysis_cohort"] == "holdout"
        assert scene["experiment_role"] == ("confirmation" if holdout else "development")
        assert scene["selection_allowed"] is (not holdout)
        assert scene["fit_allowed"] is False
        if holdout:
            assert scene["response_delay_minutes"] == 25
            assert scene["same_background_new_perturbation"] is True
            for event in scene["exogenous_events"]:
                assert event["start_minute"] % 15 == 7.5
                assert event["end_minute"] % 15 == 7.5
    assert proposal["holdout_semantics"] == "same_seen_background_new_perturbation_not_cross_background"


def test_event_table_is_coherent_and_no_duplicate_physical_scenario_crosses_cohorts():
    scenes = design().build_proposal(ROOT)["scenarios"]
    assert len({s["physical_scenario_signature"] for s in scenes}) == len(scenes)
    for s in scenes:
        for event in s["exogenous_events"]:
            kind = "fault" if event["kind"] == "device_fault" else "shock"
            assert event["start_minute"] == s[f"{kind}_start_minute"]
            assert event["end_minute"] == event["start_minute"] + s[f"{kind}_duration_minutes"]
            assert 60 <= event["start_minute"] < event["end_minute"] <= 450
        assert s["actual_p0_load_multiplier"] == {
            carrier: (1 + s["shock"].get(carrier, 0)) *
            (1 + s["shock_by_priority"].get("P0", {}).get(carrier, 0))
            for carrier in ("e", "h", "c")}
    phases = [s for s in scenes if s["analysis_cohort"] == "boundary"
              and s["design_axis"] == "event_phase"]
    assert {s["design_axis_value"] for s in phases} == {5, 10}
    assert all(e["start_minute"] % 15 and e["end_minute"] % 15
               for s in phases for e in s["exogenous_events"])


def test_retargeted_fault_domain_metadata_follows_actual_device():
    domains = {"GRID": "electric", "GT_1": "electric", "GT_2": "electric",
               "HRSG_1": "steam", "DIESEL": "backup", "ABS": "cooling",
               "TOWER_1": "cooling"}
    for scene in design().build_proposal(ROOT)["scenarios"]:
        if scene["faults"]:
            device = next(iter(scene["faults"]))
            assert scene["initiating_domain"] == domains[device]
        else:
            assert "initiating_domain" not in scene


def test_fault_groups_keep_matched_l1_l3_prefix_and_include_nonbinding_controls():
    proposal = design().build_proposal(ROOT)
    by_id = {s["id"]: s for s in proposal["scenarios"]}
    groups = proposal["matched_fault_groups"]
    assert len(groups) == 11
    for group in groups:
        a, b = (by_id[group["scene_ids"][name]] for name in ("L1", "L3"))
        assert a["base_state_id"] == b["base_state_id"]
        assert a["exogenous_events"] == b["exogenous_events"]
        assert a["exogenous_stream_id"] == b["exogenous_stream_id"]
        assert a["response_delay_minutes"] == b["response_delay_minutes"]
        assert a["enable_propagation"] is False and b["enable_propagation"] is True
    development = [s for s in proposal["scenarios"] if s["analysis_cohort"] == "development"]
    assert Counter(s["analysis_class"] for s in development) == {
        "N2": 12, "L1": 4, "L3": 4, "L2": 4, "L4a": 4, "L4b": 4}
    assert {s["binding_expectation_from_v8"] for s in development
            if s["analysis_class"] in ("L1", "L3")} == {
        "previously_binding_GRID_context", "previously_nonbinding_fault_context"}


def test_signed_proposal_rejects_even_resigned_physical_or_role_changes():
    module = design()
    proposal = module.build_proposal(ROOT)
    assert module.verify_proposal(proposal, ROOT)
    for key, value in (("grid_limit_kW", 900), ("experiment_role", "confirmation")):
        changed = copy.deepcopy(proposal)
        changed["scenarios"][0][key] = value
        changed.pop("signature")
        changed["signature"] = digest(changed)
        with pytest.raises(ValueError):
            module.verify_proposal(changed, ROOT)


def test_design_reads_no_saved_outcomes_or_sealed_registry(monkeypatch):
    module = design()
    originals = {name: getattr(Path, name) for name in ("read_text", "read_bytes")}
    def guarded(name):
        def read(path, *args, **kwargs):
            assert not {"results", "analysis", "analysis_v3_pre_event_verified"} & set(path.parts)
            if path.name == "global_manifest.json":
                assert path.resolve() == V8.resolve()
            return originals[name](path, *args, **kwargs)
        return read
    for name in originals:
        monkeypatch.setattr(Path, name, guarded(name))
    assert len(module.build_proposal(ROOT)["scenarios"]) == 119


def test_frozen_manifest_supports_two_phases_and_write_once_without_episodes(tmp_path):
    module = design()
    receipt = module.freeze(ROOT, tmp_path / "v9_run")
    assert receipt["episodes_started"] == 0
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    assert len(manifest["scenarios"]) == len(manifest["splits"]) == 119
    assert manifest["study_name"] == "hospital_ies_state_dispatch_v9"
    assert manifest["policy_search_final_12_backgrounds_unopened"] is True
    from tools.policy_redesign.closed_loop import _verify_manifest, register_batch
    assert _verify_manifest(manifest)
    for s in manifest["scenarios"]:
        split = manifest["splits"][s["id"]]
        assert split["experiment_role"] == s["experiment_role"]
        assert split["fit_allowed"] is False
        assert split["selection_allowed"] == s["selection_allowed"]
    coverage = Path(receipt["coverage_path"]).read_text(encoding="utf-8-sig").splitlines()
    assert len(coverage) == 120 and "analysis_cohort" in coverage[0]
    ids = manifest["ids_by_cohort"]
    for cohort, phase in (("development", "development"), ("holdout", "confirmation")):
        plan = register_batch(receipt["manifest_path"], tmp_path / f"{cohort}.json",
                              scenario_ids=ids[cohort][:1],
                              policy_specs=[{"policy_id": "priority_milp", "kind": "priority"},
                                            {"policy_id": "secure_milp", "kind": "secure"}],
                              preference_path=ROOT / "outputs/v8_four_scenarios_deepening_20261016_run01/design/RESEARCH_PREFERENCE.json",
                              phase=phase)
        assert plan["registered_pair_count"] == 2
    with pytest.raises(FileExistsError):
        module.freeze(ROOT, tmp_path / "v9_run")
