"""Outcome-free contracts for the v8 four-scenario crossed design."""
from __future__ import annotations

from collections import Counter, defaultdict
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.resilience import validate_scenario
from tools.p2b_experiment.design import support_interval


V7_MANIFEST = ROOT / "outputs/v7_four_scenarios_20261016_run01/new_data/global_manifest.json"
CLASSES = ("N2", "N4", "L1", "L2", "L3", "L4a", "L4b")


def design():
    return importlib.import_module("tools.four_scenarios.deepening_design")


@pytest.fixture(scope="module")
def proposal():
    return design().build_proposal(ROOT)


def test_crossed_design_uses_only_twelve_seen_v7_roots_and_stays_below_budget(proposal):
    old = json.loads(V7_MANIFEST.read_text(encoding="utf-8"))
    old_roots = {scene["root_family_id"]: scene["source_day_index"]
                 for scene in old["scenarios"]}
    scenes = proposal["scenarios"]
    assert len(scenes) == 528
    assert len({scene["id"] for scene in scenes}) == 528
    assert {scene["root_family_id"]: scene["source_day_index"]
            for scene in scenes} == old_roots
    assert Counter(scene["design_axis"] for scene in scenes) == {
        "main": 480, "delay": 16, "duration": 16, "availability": 16}
    assert Counter(scene["analysis_class"] for scene in scenes) == {
        "N2": 12, "N4": 24, "L1": 12, "L2": 144,
        "L3": 24, "L4a": 156, "L4b": 156}
    assert all(scene["experiment_role"] == "train" and not scene["is_final_blind"]
               for scene in scenes)
    assert proposal["final_12_backgrounds_unopened"] is True


def test_main_crossing_and_explicit_four_cell_join(proposal):
    scenes = {scene["id"]: scene for scene in proposal["scenarios"]}
    groups = proposal["matched_four_cell_groups"]
    assert len(groups) == 156
    main = [group for group in groups if group["design_axis"] == "main"]
    assert len(main) == 144
    assert len({group["design_cell_id"] for group in groups}) == len(groups)
    by_root = defaultdict(list)
    for group in main:
        by_root[group["root_family_id"]].append(group)
        assert set(group["scene_ids"]) == {"N4", "L3", "L4a", "L4b"}
        four = {key: scenes[sid] for key, sid in group["scene_ids"].items()}
        assert all(scene["analysis_class"] == name for name, scene in four.items())
        assert {scene["base_state_id"] for scene in four.values()} == {
            group["base_state_id"]}
        assert {scene["response_delay_minutes"] for scene in four.values()} == {15.0}
        assert all(scene["enable_propagation"] for scene in four.values())
        assert four["N4"]["faults"] == four["N4"]["shock"] == {}
        assert four["L3"]["fault_start_minute"] == four["L4b"]["fault_start_minute"]
        assert four["L3"]["faults"] == four["L4b"]["faults"]
        assert four["L4a"]["shock_start_minute"] == four["L4b"]["shock_start_minute"]
        assert four["L4a"]["shock"] == four["L4b"]["shock"]
        assert four["L4a"]["shock_by_priority"] == four["L4b"]["shock_by_priority"]
        assert four["L4a"]["design_cell_id"] == four["L4b"]["design_cell_id"]
        assert group["fault_start_minute"] == four["L3"]["fault_start_minute"]
        assert group["shock_start_minute"] == four["L4a"]["shock_start_minute"]
    assert len(by_root) == 12
    assert all({(g["shock_mode"], g["stratum"]) for g in rows}
               == {(mode, level) for mode in ("e", "h", "c", "mixed")
                   for level in ("ordinary", "elevated", "boundary_candidate")}
               for rows in by_root.values())
    assert all(len({g["scene_ids"]["N4"] for g in rows}) == 1
               and len({g["scene_ids"]["L3"] for g in rows}) == 1
               for rows in by_root.values())
    pure = [scene for scene in scenes.values() if scene["analysis_class"] == "L2"]
    assert len(pure) == 144
    assert all(not scene["enable_propagation"] and not scene["faults"]
               for scene in pure)


def test_sensitivity_axes_cover_each_quarter_with_matched_controls(proposal):
    scenes = {scene["id"]: scene for scene in proposal["scenarios"]}
    groups = [group for group in proposal["matched_four_cell_groups"]
              if group["design_axis"] != "main"]
    assert len(groups) == 12
    assert Counter((g["season_quarter"], g["design_axis"]) for g in groups) == {
        (q, axis): 1 for q in range(1, 5)
        for axis in ("delay", "duration", "availability")}
    for group in groups:
        four = {key: scenes[sid] for key, sid in group["scene_ids"].items()}
        assert len(set(group["scene_ids"].values())) == 4
        assert {scene["design_cell_id"] for scene in four.values()} == {
            group["design_cell_id"]}
        assert {scene["base_state_id"] for scene in four.values()} == {
            group["base_state_id"]}
        assert len({scene["response_delay_minutes"] for scene in four.values()}) == 1
        assert four["L3"]["fault_start_minute"] == four["L4b"]["fault_start_minute"]
        assert four["L4a"]["shock_start_minute"] == four["L4b"]["shock_start_minute"]
        if group["design_axis"] == "delay":
            assert group["response_delay_minutes"] == 30.0
        else:
            assert group["response_delay_minutes"] == 15.0
        if group["design_axis"] == "duration":
            assert group["shock_duration_minutes"] == 300.0
        if group["design_axis"] == "availability":
            assert group["fault_availability"] in (0.0, 0.25, 0.5)


def test_all_scenes_keep_source_support_physics_and_train_roles(proposal):
    old = json.loads(V7_MANIFEST.read_text(encoding="utf-8"))
    old_by_root = {scene["root_family_id"]: scene for scene in old["scenarios"]}
    for scene in proposal["scenarios"]:
        validate_scenario(scene)
        assert scene["full_source_support"] == support_interval(scene)
        old_scene = old_by_root[scene["root_family_id"]]
        assert scene["start_hour"] == old_scene["start_hour"]
        assert scene["grid_limit_kW"] == old_scene["grid_limit_kW"]
        assert scene["pre_event_grid_limit_kW"] == old_scene["pre_event_grid_limit_kW"]
        assert scene["full_source_support"] == old_scene["full_source_support"]
        assert scene["root_family_id"] == scene["family_id"]
        assert scene["experiment_role"] == "train"
        assert scene["sampling_weight_is_failure_probability"] is False
        assert scene["design_axis"] in ("main", "delay", "duration", "availability")
        assert isinstance(scene["design_cell_id"], str) and scene["design_cell_id"]
        assert isinstance(scene["design_level"], str) and scene["design_level"]


def test_proposal_registers_counts_coverage_gaps_and_source_integrity(proposal):
    module = design()
    assert module.verify_proposal(proposal, ROOT)
    assert proposal["analysis_class_counts"] == {
        "N2": 12, "N4": 24, "L1": 12, "L2": 144,
        "L3": 24, "L4a": 156, "L4b": 156}
    assert proposal["design_axis_counts"] == {
        "main": 480, "delay": 16, "duration": 16, "availability": 16}
    assert proposal["design_coverage"]
    assert proposal["missing_combinations"]
    altered = json.loads(json.dumps(proposal))
    altered["scenarios"][0]["grid_limit_kW"] = 900.0
    with pytest.raises(ValueError):
        module.verify_proposal(altered, ROOT)


def test_runner_manifest_and_freeze_are_signed_write_once_and_emit_coverage(tmp_path, proposal):
    module = design()
    manifest = module.build_deepening_manifest(proposal, ROOT)
    assert len(manifest["scenarios"]) == len(manifest["splits"]) == 528
    assert manifest["policy_search_final_12_backgrounds_unopened"] is True
    from tools.policy_redesign.closed_loop import _verify_manifest, register_batch
    assert _verify_manifest(manifest)
    receipt = module.freeze(ROOT, tmp_path / "isolated_run")
    assert Path(receipt["proposal_path"]).is_file()
    assert Path(receipt["manifest_path"]).is_file()
    coverage_path = Path(receipt["coverage_path"])
    assert coverage_path.is_file()
    lines = coverage_path.read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 529
    assert "design_cell_id" in lines[0] and "source_day_index" in lines[0]
    plan = register_batch(receipt["manifest_path"], tmp_path / "batch.json",
                          scenario_ids=[manifest["scenarios"][0]["id"]],
                          policy_specs=[{"policy_id": "priority_milp", "kind": "priority"},
                                        {"policy_id": "secure_milp", "kind": "secure"}],
                          preference_path=ROOT / "outputs/v7_four_scenarios_20261016_run01/design/RESEARCH_PREFERENCE.json",
                          phase="train")
    assert plan["registered_pair_count"] == 2
    with pytest.raises(FileExistsError):
        module.freeze(ROOT, tmp_path / "isolated_run")
