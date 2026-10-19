"""Outcome-free source design contracts for the four-scenario study."""
from __future__ import annotations

from collections import Counter
import importlib
from itertools import product
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.resilience import validate_scenario
from tools.p2b_experiment.design import support_interval


CLASSES = {"N2", "N4", "L1", "L2", "L3", "L4a", "L4b"}


def design():
    return importlib.import_module("tools.four_scenarios.data_design")


@pytest.fixture(scope="module")
def proposal():
    return design().build_proposal(ROOT)


def test_eligible_windows_exclude_old_reserved_and_sealed_support():
    module = design()
    occupied = [
        {"raw_start_minute": 10 * 1440, "raw_end_minute": 11 * 1440},
        {"raw_start_minute": 12 * 1440, "raw_end_minute": 13 * 1440},
    ]
    windows = module.eligible_windows(
        occupied, candidate_days=[10, 11, 12], start_hours=[5, 6]
    )
    assert {row["day_index"] for row in windows} == {11}
    assert all(row["raw_end_minute"] - row["raw_start_minute"] == 660
               for row in windows)
    assert all(row["embargo_start_minute"] == row["raw_start_minute"] - 60
               and row["embargo_end_minute"] == row["raw_end_minute"] + 60
               for row in windows)


def test_final_twelve_metadata_excludes_full_days_even_if_old_intervals_omit_them(tmp_path):
    module = design()
    sealed = list(range(100, 112))
    def block(day):
        return {"day_index": day, "raw_start_minute": day * 1440,
                "raw_end_minute": (day + 1) * 1440}
    v5 = {"sealed_final_days": sealed, "excluded_support": [],
          "blocks": [block(day) for day in range(54)]}
    v6 = {"sealed_final_days": sealed,
          "blocks": [block(day) for day in range(54, 66)]}
    for relpath, value in ((module.V5_PROPOSAL, v5), (module.V6_PROPOSAL, v6)):
        value["signature"] = module.digest(value)
        path = tmp_path / relpath
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    intervals = module.prior_exposures(tmp_path)["intervals"]
    for day in sealed:
        assert any(row["raw_start_minute"] <= day * 1440
                   and row["raw_end_minute"] >= (day + 1) * 1440
                   for row in intervals)


def test_real_proposal_has_twelve_independent_seasonal_roots_and_84_scenes(proposal):
    module = design()
    assert module.verify_proposal(proposal, ROOT)
    roots, scenes = proposal["roots"], proposal["scenarios"]
    assert len(roots) == 12
    assert len({root["day_index"] for root in roots}) == 12
    assert Counter(root["season_quarter"] for root in roots) == Counter({1: 3, 2: 3, 3: 3, 4: 3})
    assert len(scenes) == 84
    assert len({scene["id"] for scene in scenes}) == 84
    assert Counter(scene["analysis_class"] for scene in scenes) == {name: 12 for name in CLASSES}
    assert all(scene["experiment_role"] == "train" and not scene["is_final_blind"]
               for scene in scenes)
    assert proposal["final_12_backgrounds_unopened"] is True
    assert proposal["available_daytime_roots_by_quarter"] == {"1": 3, "2": 3, "3": 3, "4": 3}


def test_seven_scenes_per_root_share_background_and_twin_event_parameters(proposal):
    for root in proposal["roots"]:
        by = {scene["analysis_class"]: scene for scene in proposal["scenarios"]
              if scene["root_family_id"] == root["root_family_id"]}
        assert set(by) == CLASSES
        assert len({scene["start_hour"] for scene in by.values()}) == 1
        assert len({scene["initialization_seed"] for scene in by.values()}) == 1
        assert len({scene["grid_limit_kW"] for scene in by.values()}) == 1
        assert {scene["grid_limit_kW"] for scene in by.values()} == {850.0}
        for name in ("N2", "N4"):
            ref = by[name]
            assert ref["faults"] == ref["shock"] == {}
            assert ref["exogenous_events"] == []
            assert ref["reference_role"] == "normal_no_disturbance"
        assert by["N2"]["enable_propagation"] is False
        assert by["N4"]["enable_propagation"] is True
        assert {by[name]["event_class"] for name in ("N2", "N4")} == {"L2", "L4"}
        for key in ("faults", "fault_start_minute", "fault_duration_minutes"):
            assert by["L1"][key] == by["L3"][key]
        for key in ("shock", "shock_by_priority", "shock_start_minute", "shock_duration_minutes"):
            assert by["L2"][key] == by["L4a"][key] == by["L4b"][key]
        assert by["L4b"]["faults"] == by["L3"]["faults"]
        assert by["L4b"]["fault_start_minute"] - by["L4b"]["shock_start_minute"] in (-30, 0, 30)
        assert by["L1"]["faults"] and not by["L1"]["shock"]
        assert by["L2"]["shock"] and not by["L2"]["faults"]
        for scene in by.values():
            validate_scenario(scene)


def test_every_scene_has_real_660_minute_support_and_no_prior_overlap(proposal):
    exposures = design().prior_exposures(ROOT)["intervals"]
    for scene in proposal["scenarios"]:
        support = support_interval(scene)
        assert scene["full_source_support"] == support
        assert support["raw_end_minute"] - support["raw_start_minute"] == 660
        a = support["raw_start_minute"] - 60
        b = support["raw_end_minute"] + 60
        assert all(b <= old["raw_start_minute"] or old["raw_end_minute"] <= a
                   for old in exposures)


def test_registered_factor_cross_tabulation_breaks_fault_shock_confounding(proposal):
    combined = [scene for scene in proposal["scenarios"]
                if scene["analysis_class"] == "L4b"]
    modes = {"e", "h", "c", "mixed"}
    levels = {"ordinary", "elevated", "boundary_candidate"}
    severities = {"full_outage", "severe", "moderate"}
    domains = {"electric", "steam", "cooling", "backup"}
    assert set((scene["shock_mode"], scene["stratum"]) for scene in combined) == set(product(modes, levels))
    assert set((scene["stratum"], scene["fault_severity_layer"])
               for scene in combined) == set(product(levels, severities))
    assert set((scene["initiating_domain"], scene["fault_severity_layer"])
               for scene in combined) == set(product(domains, severities))
    for mode in modes:
        assert len({scene["initiating_domain"] for scene in combined
                    if scene["shock_mode"] == mode}) >= 2
    table = proposal["factor_cross_tabulation"]
    assert table["shock_level_by_fault_severity"] == {
        f"{level}|{severity}": sum(scene["stratum"] == level
                                  and scene["fault_severity_layer"] == severity
                                  for scene in combined)
        for level, severity in product(sorted(levels), sorted(severities))
    }


def test_manifest_and_temp_freeze_are_signed_runner_compatible_and_write_once(tmp_path):
    module = design()
    proposal = module.build_proposal(ROOT)
    manifest = module.build_four_manifest(proposal, ROOT)
    assert manifest["signature"]
    assert len(manifest["scenarios"]) == len(manifest["splits"]) == 84
    assert all(manifest["splits"][scene["id"]]["experiment_role"] == "train"
               for scene in manifest["scenarios"])
    from tools.policy_redesign.closed_loop import _verify_manifest
    assert _verify_manifest(manifest)
    receipt = module.freeze(ROOT, tmp_path / "isolated_run")
    assert Path(receipt["proposal_path"]).is_file()
    assert Path(receipt["manifest_path"]).is_file()
    with pytest.raises(FileExistsError):
        module.freeze(ROOT, tmp_path / "isolated_run")
