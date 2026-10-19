"""Independent, outcome-free sham-clock N4 controls for frozen v8 cells."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hospital_ies.resilience import validate_scenario
from hospital_ies.state_transition import event_window
from tools.p2b_experiment.design import support_interval

V8_MANIFEST = ROOT / "outputs/v8_four_scenarios_deepening_20261016_run01/new_data/global_manifest.json"


def design():
    return importlib.import_module("tools.four_scenarios.clockmatched_design")


@pytest.fixture(scope="module")
def proposal():
    return design().build_proposal(ROOT)


def test_only_twenty_four_no_load_no_fault_clockmatched_controls(proposal):
    old = json.loads(V8_MANIFEST.read_text(encoding="utf-8"))
    by_id = {scene["id"]: scene for scene in old["scenarios"]}
    scenes = proposal["scenarios"]
    assert len(scenes) == len({scene["id"] for scene in scenes}) == 24
    assert Counter(scene["design_axis"] for scene in scenes) == {
        "main": 12, "delay": 4, "duration": 4, "availability": 4}
    assert all(scene["analysis_class"] == "N4_clockmatched"
               and scene["reference_role"] == "no_load_no_fault_clockmatched_control"
               and scene["shadow_event_clock_only"] is True
               and scene["stage"] == 4 and scene["enable_propagation"] is True
               and scene["shock"] == scene["shock_by_priority"] == scene["faults"] == {}
               and scene["exogenous_events"] == []
               and scene["actual_p0_load_multiplier"] == {"e": 1.0, "h": 1.0, "c": 1.0}
               and scene["experiment_role"] == "train" and not scene["is_final_blind"]
               for scene in scenes)
    assert {scene["root_family_id"] for scene in scenes} == {
        scene["root_family_id"] for scene in old["scenarios"]}
    for scene in scenes:
        validate_scenario(scene)
        assert scene["full_source_support"] == support_interval(scene)
        old_n4 = next(s for s in old["scenarios"] if s["root_family_id"] == scene["root_family_id"]
                      and s["analysis_class"] == "N4" and
                      (s["design_axis"] == scene["design_axis"] or scene["design_axis"] == "main"))
        assert scene["start_hour"] == old_n4["start_hour"]
        assert scene["base_state_id"] == old_n4["base_state_id"]
        assert scene["grid_limit_kW"] == old_n4["grid_limit_kW"]
        assert scene["pre_event_grid_limit_kW"] == old_n4["pre_event_grid_limit_kW"]
        assert scene["full_source_support"] == old_n4["full_source_support"]
    assert proposal["final_12_backgrounds_unopened"] is True


def test_all_original_groups_map_to_clockmatched_n4_with_exact_gate_clocks(proposal):
    old = json.loads(V8_MANIFEST.read_text(encoding="utf-8"))
    old_groups = {g["design_cell_id"]: g for g in old["matched_four_cell_groups"]}
    by_scene = {scene["id"]: scene for scene in proposal["scenarios"]}
    mapped = proposal["matched_clock_control_groups"]
    assert len(mapped) == len(old_groups) == 156
    assert {g["design_cell_id"] for g in mapped} == set(old_groups)
    main_by_root = defaultdict(set)
    for row in mapped:
        original = old_groups[row["design_cell_id"]]
        assert row["original_scene_ids"] == original["scene_ids"]
        assert row["design_axis"] == original["design_axis"]
        assert row["root_family_id"] == original["root_family_id"]
        scene = by_scene[row["clockmatched_scene_id"]]
        assert scene["root_family_id"] == row["root_family_id"]
        assert event_window(scene, "shock") == (
            original["shock_start_minute"],
            original["shock_start_minute"] + original["shock_duration_minutes"])
        assert event_window(scene, "fault") == (
            original["fault_start_minute"],
            original["fault_start_minute"] + original["fault_duration_minutes"])
        assert scene["response_delay_minutes"] == original["response_delay_minutes"]
        assert scene["base_state_id"] == original["base_state_id"]
        assert row["shock_start_minute"] == row["fault_start_minute"]
        if row["design_axis"] == "main":
            main_by_root[row["root_family_id"]].add(scene["id"])
    assert len(main_by_root) == 12
    assert all(len(ids) == 1 for ids in main_by_root.values())
    assert len({row["clockmatched_scene_id"] for row in mapped
                if row["design_axis"] != "main"}) == 12


def test_proposal_source_binding_rejects_tampering(proposal):
    module = design()
    assert module.verify_proposal(proposal, ROOT)
    assert proposal["source_v8_manifest_sha256"] == hashlib.sha256(V8_MANIFEST.read_bytes()).hexdigest()
    damaged = json.loads(json.dumps(proposal))
    damaged["matched_clock_control_groups"][0]["response_delay_minutes"] = 900
    with pytest.raises(ValueError):
        module.verify_proposal(damaged, ROOT)


def test_separate_manifest_and_freeze_are_runner_compatible_and_write_once(tmp_path, proposal):
    module = design()
    manifest = module.build_clockmatched_manifest(proposal, ROOT)
    assert len(manifest["scenarios"]) == len(manifest["splits"]) == 24
    assert manifest["matched_clock_control_groups"] == proposal["matched_clock_control_groups"]
    assert manifest["policy_search_final_12_backgrounds_unopened"] is True
    from tools.policy_redesign.closed_loop import _verify_manifest, register_batch
    assert _verify_manifest(manifest)
    run = tmp_path / "clockmatched_run"
    receipt = module.freeze(ROOT, run)
    assert receipt["episodes_started"] == 0
    assert Path(receipt["proposal_path"]).is_file()
    assert Path(receipt["manifest_path"]).is_file()
    assert not (run / "main/results").exists()
    saved = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    assert _verify_manifest(saved)
    plan = register_batch(receipt["manifest_path"], tmp_path / "batch.json",
                          scenario_ids=[saved["scenarios"][0]["id"]],
                          policy_specs=[{"policy_id": "priority_milp", "kind": "priority"},
                                        {"policy_id": "secure_milp", "kind": "secure"}],
                          preference_path=ROOT / "outputs/v7_four_scenarios_20261016_run01/design/RESEARCH_PREFERENCE.json",
                          phase="train")
    assert plan["registered_pair_count"] == 2
    with pytest.raises(FileExistsError):
        module.freeze(ROOT, run)
