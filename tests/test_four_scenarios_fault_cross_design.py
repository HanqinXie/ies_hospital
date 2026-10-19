"""Outcome-free L1/L3 device cross on already-seen v7 day roots."""

import json
from pathlib import Path

import pytest

from tools.four_scenarios.fault_cross_design import (
    build_fault_cross_manifest,
    build_fault_cross_proposal,
    freeze,
)


ROOT = Path(__file__).resolve().parents[1]


def test_fault_cross_is_matched_and_not_new_backgrounds():
    proposal = build_fault_cross_proposal(ROOT)
    scenes = proposal["scenarios"]
    groups = proposal["matched_fault_groups"]
    assert len(scenes) == 16
    assert len(groups) == 8
    assert {s["analysis_class"] for s in scenes} == {"L1", "L3"}
    assert {s["season_quarter"] for s in scenes} == {1, 2, 3, 4}
    assert proposal["source_v8_seen_root_count"] == 12
    assert proposal["new_independent_background_count"] == 0
    assert proposal["final_12_backgrounds_unopened"] is True
    by_id = {s["id"]: s for s in scenes}
    for group in groups:
        l1, l3 = (by_id[group["scene_ids"][k]] for k in ("L1", "L3"))
        assert l1["base_state_id"] == l3["base_state_id"]
        assert l1["faults"] == l3["faults"]
        assert l1["fault_start_minute"] == l3["fault_start_minute"]
        assert l1["fault_duration_minutes"] == l3["fault_duration_minutes"]
        assert l1["enable_propagation"] is False
        assert l3["enable_propagation"] is True
        assert l1["faults"] == {group["initiating_device"]: group["availability"]}
        assert l1["initiating_device"] != group["source_root_initiating_device"]
        assert l1["experiment_role"] == "train"
        assert l1["is_final_blind"] is False


def test_fault_cross_manifest_registers_both_original_policies():
    proposal = build_fault_cross_proposal(ROOT)
    manifest = build_fault_cross_manifest(proposal, ROOT)
    assert len(manifest["scenarios"]) == 16
    assert set(manifest["policies"]) == {
        "secure_milp", "priority_milp"
    }
    assert manifest["source_v8_manifest_signature"] == proposal["source_v8_manifest_signature"]
    assert manifest["policy_search_final_12_backgrounds_unopened"] is True


def test_fault_cross_freeze_is_one_time_and_no_episodes(tmp_path):
    receipt = freeze(ROOT, tmp_path / "fault_cross")
    assert receipt["episodes_started"] == 0
    assert receipt["scenarios"] == 16
    assert receipt["matched_fault_groups"] == 8
    path = Path(receipt["manifest_path"])
    assert json.loads(path.read_text(encoding="utf-8"))["signature"]
    with pytest.raises(FileExistsError):
        freeze(ROOT, tmp_path / "fault_cross")
