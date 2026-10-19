"""Freeze an outcome-free v8 crossed design on the twelve seen v7 backgrounds.

No trajectory or final-blind background is read.  Main N4/L3 controls are
shared across pressure cells through explicit matched-four-cell IDs; the
separate sensitivity cells each have their own matched controls.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import csv
import hashlib
from itertools import product
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.data_contract import SCHEMA_VERSIONS
from hospital_ies.dataset_manifest import build_manifest, digest
from hospital_ies.resilience import validate_scenario
from tools.control_improvement.data_design import validate_observation_support
from tools.four_scenarios.data_design import SHOCK_LEVELS, SHOCK_MODES


VERSION = "four_scenarios.v8.deepening_design.v1"
SEED = 20261016
V7_MANIFEST = Path("outputs/v7_four_scenarios_20261016_run01/new_data/global_manifest.json")
LOAD_FILE = Path("inputs/loads/hospital_ies_demand_8760.csv")
CONTRACT_FILE = Path("configs/p1b_execution_research.json")
CLASSES = ("N2", "N4", "L1", "L2", "L3", "L4a", "L4b")
SHOCK_CLASSES = ("L2", "L4a", "L4b")
FOUR_CLASSES = ("N4", "L3", "L4a", "L4b")
AXES = ("delay", "duration", "availability")
SEVERITY_BY_AVAILABILITY = {0.0: "full_outage", 0.25: "severe", 0.5: "moderate"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _signed(value: dict) -> bool:
    return value.get("signature") == digest({k: v for k, v in value.items()
                                               if k != "signature"})


def _write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _v7_roots(root: Path) -> tuple[list[dict], dict]:
    """Use only signed, seen v7 scenario definitions; never consult outcomes."""
    manifest = _read(root / V7_MANIFEST)
    if not _signed(manifest) or manifest.get("four_scenario_roots") != 12:
        raise ValueError("Frozen v7 source manifest signature or root count differs")
    if manifest.get("policy_search_final_12_backgrounds_unopened") is not True:
        raise ValueError("Original final backgrounds were not declared sealed")
    old_scenes = manifest.get("scenarios", [])
    by_root: dict[str, dict[str, dict]] = {}
    for scene in old_scenes:
        if (scene.get("experiment_role") != "train" or scene.get("is_final_blind")
                or scene.get("analysis_class") not in CLASSES):
            raise ValueError("V7 source contains a non-train or unknown scene")
        by_root.setdefault(scene["root_family_id"], {})[scene["analysis_class"]] = scene
    if (len(old_scenes) != 84 or len(by_root) != 12 or
            any(set(classes) != set(CLASSES) for classes in by_root.values())):
        raise ValueError("V7 seven-class matched root inventory differs")
    days = [next(iter(classes.values()))["source_day_index"]
            for classes in by_root.values()]
    if len(set(days)) != 12:
        raise ValueError("V7 source root calendar days are not distinct")
    return sorted(by_root.values(), key=lambda rows: rows["N4"]["source_day_index"]), manifest


def _cell_id(root_id: str, axis: str, level: str, mode: str,
             stratum: str, delay: float, duration: float, availability: float) -> str:
    return "v8_cell:" + digest([VERSION, root_id, axis, level, mode, stratum,
                                 delay, duration, availability])[:24]


def _shock(mode: str, stratum: str) -> tuple[dict, dict]:
    row = next((row for row in SHOCK_LEVELS if row[0] == stratum), None)
    if mode not in SHOCK_MODES or row is None:
        raise ValueError("Unknown registered shock mode or level")
    _, amplitudes, p0_e = row
    shock = {carrier: float(value if mode in (carrier, "mixed") else 0.0)
             for carrier, value in amplitudes.items()}
    priority = {"P0": {"e": float(p0_e)}} if p0_e and mode in ("e", "mixed") else {}
    return shock, priority


def _scene(old: dict, *, root_id: str, axis: str, level: str, cell_id: str,
           mode: str | None = None, stratum: str | None = None,
           delay: float | None = None, shock_duration: float | None = None,
           availability: float | None = None) -> dict:
    scene = copy.deepcopy(old)
    name = scene["analysis_class"]
    scene["id"] = "v8_case_" + digest([VERSION, root_id, name, axis, level,
                                         mode, stratum, delay, shock_duration,
                                         availability])[:20]
    scene["sample_schema_version"] = VERSION
    scene["design_cell_id"] = cell_id
    scene["design_axis"] = axis
    scene["design_level"] = level
    scene["sampling_cohort"] = "main" if axis == "main" else "sensitivity"
    scene["sampling_role"] = "matched_seen_background_crossed_four_class"
    scene["sampling_method"] = "v7_seen_roots_outcome_free_factor_cross"
    scene["distribution_id"] = VERSION + ".q_design"
    scene["purpose"] = "v8_seen_background_four_class_mechanism_study"
    scene["experiment_role"] = "train"
    scene["is_final_blind"] = False
    scene["exogenous_stream_id"] = "v8_stream:" + digest([scene["id"], "exogenous"])
    if delay is not None:
        scene["response_delay_minutes"] = float(delay)
    if name in SHOCK_CLASSES:
        if mode is None or stratum is None:
            raise ValueError("Shock scene needs registered direction and level")
        scene["shock_mode"] = mode
        scene["stratum"] = stratum
        scene["shock"], scene["shock_by_priority"] = _shock(mode, stratum)
        if shock_duration is not None:
            scene["shock_duration_minutes"] = float(shock_duration)
    if name in ("L1", "L3", "L4b") and availability is not None:
        if float(availability) not in SEVERITY_BY_AVAILABILITY:
            raise ValueError("Fault availability outside prior registered severity values")
        device = scene["initiating_device"]
        scene["faults"] = {device: float(availability)}
        scene["fault_severity_layer"] = SEVERITY_BY_AVAILABILITY[float(availability)]
    if name in ("L3", "L4b"):
        # The old v7 L4b offset clock cannot be used for a pure interaction.
        scene["fault_start_minute"] = float(old["fault_start_minute"])
    events = []
    if name in ("L1", "L3", "L4b"):
        events.append({"origin": "exogenous", "kind": "device_fault",
                       "device": scene["initiating_device"],
                       "availability": scene["faults"][scene["initiating_device"]],
                       "start_minute": scene["fault_start_minute"],
                       "end_minute": scene["fault_start_minute"] + scene["fault_duration_minutes"]})
    if name in SHOCK_CLASSES:
        events.append({"origin": "exogenous", "kind": "load_shock",
                       "start_minute": scene["shock_start_minute"],
                       "end_minute": scene["shock_start_minute"] + scene["shock_duration_minutes"],
                       "shock": copy.deepcopy(scene["shock"]),
                       "shock_by_priority": copy.deepcopy(scene["shock_by_priority"])})
    scene["exogenous_events"] = events
    scene["actual_p0_load_multiplier"] = {
        carrier: (1 + scene["shock"].get(carrier, 0.0)) *
                 (1 + scene["shock_by_priority"].get("P0", {}).get(carrier, 0.0))
        for carrier in ("e", "h", "c")}
    if validate_observation_support(scene) != scene["full_source_support"]:
        raise ValueError("V8 source support differs from frozen v7 root")
    validate_scenario(scene)
    return scene


def _group(root_scenes: dict[str, dict], axis: str, level: str,
           mode: str, stratum: str, delay: float, shock_duration: float,
           availability: float, controls: dict[str, dict],
           shocks: dict[str, dict], cell_id: str) -> dict:
    root_scene = root_scenes["N4"]
    return {
        "design_cell_id": cell_id, "design_axis": axis, "design_level": level,
        "root_family_id": root_scene["root_family_id"],
        "season_quarter": root_scene["season_quarter"],
        "shock_mode": mode, "stratum": stratum,
        "base_state_id": root_scene["base_state_id"],
        "response_delay_minutes": float(delay),
        "shock_start_minute": float(root_scenes["L2"]["shock_start_minute"]),
        "fault_start_minute": float(root_scenes["L3"]["fault_start_minute"]),
        "shock_duration_minutes": float(shock_duration),
        "fault_duration_minutes": float(root_scenes["L3"]["fault_duration_minutes"]),
        "fault_availability": float(availability),
        "scene_ids": {name: (controls[name] if name in controls else shocks[name])["id"]
                      for name in FOUR_CLASSES},
    }


def _coverage(scenes: list[dict], groups: list[dict]) -> tuple[dict, list[dict]]:
    main = [group for group in groups if group["design_axis"] == "main"]
    sensitivity = [group for group in groups if group["design_axis"] != "main"]
    levels = tuple(row[0] for row in SHOCK_LEVELS)
    main_present = {(group["season_quarter"], group["shock_mode"], group["stratum"])
                    for group in main}
    sensitivity_present = {(group["season_quarter"], group["design_axis"],
                            group["shock_mode"], group["stratum"])
                           for group in sensitivity}
    missing = [
        {"season_quarter": quarter, "design_axis": axis,
         "shock_mode": mode, "stratum": stratum,
         "reason": "not selected in small balanced sensitivity fraction"}
        for quarter, axis, mode, stratum in product(range(1, 5), AXES, SHOCK_MODES, levels)
        if (quarter, axis, mode, stratum) not in sensitivity_present
    ]
    return {
        "independent_seen_background_count": 12,
        "new_independent_background_count": 0,
        "main_quarter_mode_strength_cells": len(main_present),
        "main_quarter_mode_strength_expected": 4 * len(SHOCK_MODES) * len(levels),
        "sensitivity_quarter_axis_cells": len(sensitivity),
        "sensitivity_quarter_axis_expected": 4 * len(AXES),
        "sensitivity_mode_strength_fraction_by_axis": {
            axis: sum(group["design_axis"] == axis for group in sensitivity) /
                  (4 * len(SHOCK_MODES) * len(levels)) for axis in AXES},
        "scene_counts_by_axis_and_class": {
            f"{axis}|{name}": sum(scene["design_axis"] == axis and
                                  scene["analysis_class"] == name for scene in scenes)
            for axis, name in product(("main",) + AXES, CLASSES)},
    }, missing


def build_proposal(workspace_root: Path | str = ROOT, *, seed: int = SEED) -> dict:
    """Generate 528 pre-registered specifications with zero outcome access."""
    if type(seed) is not int or seed < 0:
        raise ValueError("Design seed must be a nonnegative integer")
    root = Path(workspace_root).resolve()
    roots, old_manifest = _v7_roots(root)
    scenes: list[dict] = []
    groups: list[dict] = []
    by_quarter: dict[int, list[dict]] = {quarter: [] for quarter in range(1, 5)}
    for old in roots:
        root_id = old["N4"]["root_family_id"]
        quarter = old["N4"]["season_quarter"]
        by_quarter[quarter].append(old)
        base_cell = "v8_base:" + digest([VERSION, root_id])[:24]
        controls = {}
        for name in ("N2", "N4", "L1", "L3"):
            controls[name] = _scene(old[name], root_id=root_id, axis="main",
                                    level="baseline", cell_id=base_cell,
                                    delay=15.0 if name != "N2" else 0.0)
            scenes.append(controls[name])
        fault = old["L3"]
        original_availability = float(fault["faults"][fault["initiating_device"]])
        shock_duration = float(old["L2"]["shock_duration_minutes"])
        for mode, row in product(SHOCK_MODES, SHOCK_LEVELS):
            stratum = row[0]
            level = mode + "|" + stratum
            cell_id = _cell_id(root_id, "main", level, mode, stratum, 15.0,
                               shock_duration, original_availability)
            shock_scenes = {}
            for name in SHOCK_CLASSES:
                template = old[name]
                if name == "L4b":
                    template = copy.deepcopy(template)
                    template["fault_start_minute"] = fault["fault_start_minute"]
                    template["fault_duration_minutes"] = fault["fault_duration_minutes"]
                scene = _scene(template, root_id=root_id, axis="main", level=level,
                               cell_id=cell_id, mode=mode, stratum=stratum,
                               delay=15.0, shock_duration=shock_duration,
                               availability=original_availability if name == "L4b" else None)
                shock_scenes[name] = scene
                scenes.append(scene)
            groups.append(_group(old, "main", level, mode, stratum, 15.0,
                                 shock_duration, original_availability,
                                 controls, shock_scenes, cell_id))
    if any(len(rows) != 3 for rows in by_quarter.values()):
        raise ValueError("Frozen v7 roots no longer give three per quarter")
    for quarter in range(1, 5):
        old = min(by_quarter[quarter], key=lambda rows: rows["N4"]["source_day_index"])
        root_id = old["N4"]["root_family_id"]
        mode, stratum = old["L2"]["shock_mode"], old["L2"]["stratum"]
        base_duration = float(old["L2"]["shock_duration_minutes"])
        fault = old["L3"]
        base_availability = float(fault["faults"][fault["initiating_device"]])
        values = sorted(SEVERITY_BY_AVAILABILITY)
        other_availability = values[(values.index(base_availability) + 1) % len(values)]
        for axis in AXES:
            delay = 30.0 if axis == "delay" else 15.0
            duration = 300.0 if axis == "duration" else base_duration
            availability = other_availability if axis == "availability" else base_availability
            level = ("response_delay_minutes=30" if axis == "delay" else
                     "shock_duration_minutes=300" if axis == "duration" else
                     "fault_availability=" + str(availability))
            cell_id = _cell_id(root_id, axis, level, mode, stratum, delay,
                               duration, availability)
            new = {}
            for name in FOUR_CLASSES:
                template = old[name]
                if name == "L4b":
                    template = copy.deepcopy(template)
                    template["fault_start_minute"] = fault["fault_start_minute"]
                    template["fault_duration_minutes"] = fault["fault_duration_minutes"]
                scene = _scene(template, root_id=root_id, axis=axis, level=level,
                               cell_id=cell_id, mode=mode if name in SHOCK_CLASSES else None,
                               stratum=stratum if name in SHOCK_CLASSES else None,
                               delay=delay, shock_duration=duration if name in SHOCK_CLASSES else None,
                               availability=availability if name in ("L3", "L4b") else None)
                new[name] = scene
                scenes.append(scene)
            groups.append(_group(old, axis, level, mode, stratum, delay, duration,
                                 availability, new, new, cell_id))
    if len(scenes) != 528 or len(groups) != 156 or len({s["id"] for s in scenes}) != len(scenes):
        raise ValueError("V8 crossed design count or identity differs")
    coverage, missing = _coverage(scenes, groups)
    source_sha = {
        V7_MANIFEST.as_posix(): _sha(root / V7_MANIFEST),
        LOAD_FILE.as_posix(): _sha(root / LOAD_FILE),
        CONTRACT_FILE.as_posix(): _sha(root / CONTRACT_FILE),
        "tools/four_scenarios/data_design.py": _sha(root / "tools/four_scenarios/data_design.py"),
        "tools/four_scenarios/deepening_design.py": _sha(Path(__file__)),
    }
    proposal = {
        "version": VERSION + ".proposal", "seed": seed,
        "source_v7_manifest_signature": old_manifest["signature"],
        "source_v7_seen_root_count": 12,
        "final_12_backgrounds_unopened": True,
        "experiment_role": "train",
        "independent_background_semantics": "same twelve seen v7 day roots; no new year or blind test",
        "scenarios": scenes,
        "matched_four_cell_groups": groups,
        "analysis_class_counts": dict(Counter(scene["analysis_class"] for scene in scenes)),
        "design_axis_counts": dict(Counter(scene["design_axis"] for scene in scenes)),
        "design_coverage": coverage,
        "missing_combinations": missing,
        "probability_semantics": {"p_reference": None, "importance_weight": None,
                                  "not_hospital_failure_probability": True},
        "source_sha256": source_sha,
    }
    proposal["signature"] = digest(proposal)
    return proposal


def verify_proposal(proposal: dict, workspace_root: Path | str = ROOT) -> bool:
    if not isinstance(proposal, dict) or not _signed(proposal):
        raise ValueError("V8 proposal signature differs")
    if proposal != build_proposal(workspace_root, seed=proposal["seed"]):
        raise ValueError("V8 proposal differs from source registration")
    return True


def build_deepening_manifest(proposal: dict, workspace_root: Path | str = ROOT,
                             *, proposal_path: Path | None = None) -> dict:
    root = Path(workspace_root).resolve()
    verify_proposal(proposal, root)
    scenes = proposal["scenarios"]
    splits = {}
    for scene in scenes:
        support = scene["full_source_support"]
        splits[scene["id"]] = {
            "group_id": scene["root_family_id"], "split": "train",
            "experiment_role": "train", "fit_allowed": True,
            "selection_allowed": False, "development_only": False,
            "split_version": VERSION, "embargo_minutes_each_side": 60,
            "raw_start_minute": support["raw_start_minute"],
            "raw_end_minute": support["raw_end_minute"],
            "time_block_id": scene["time_block_id"],
            "purpose": scene["purpose"], "is_final_blind": False,
        }
    options = {"seed": proposal["seed"], "security_weight": .35,
               "label_horizons": [15, 30, 60, 120], "history_minutes": 60,
               "score_start_minute": 60, "score_end_minute": 420,
               "observed_end_minute": 600}
    manifest = build_manifest(root, scenes, ("priority_milp", "secure_milp"),
                              _read(root / CONTRACT_FILE), SCHEMA_VERSIONS,
                              options, splits)
    manifest.pop("signature")
    manifest.update(
        manifest_version=VERSION + ".manifest",
        four_scenario_source_signature=proposal["signature"],
        four_scenario_source_sha256=proposal["source_sha256"],
        four_scenario_proposal_file_sha256=(_sha(proposal_path) if proposal_path else None),
        four_scenario_roots=12, four_scenario_specifications=len(scenes),
        four_scenario_analysis_class_counts=proposal["analysis_class_counts"],
        matched_four_cell_groups=proposal["matched_four_cell_groups"],
        design_coverage=proposal["design_coverage"],
        missing_combinations=proposal["missing_combinations"],
        policy_search_final_12_backgrounds_unopened=True,
    )
    manifest["signature"] = digest(manifest)
    return manifest


_COVERAGE_FIELDS = (
    "id", "root_family_id", "source_day_index", "season_quarter", "analysis_class",
    "design_cell_id", "design_axis", "design_level", "shock_mode", "stratum",
    "initiating_device", "fault_severity_layer", "fault_availability",
    "shock_start_minute", "shock_duration_minutes", "fault_start_minute",
    "fault_duration_minutes", "response_delay_minutes", "base_state_id",
    "experiment_role", "is_final_blind",
)


def _write_coverage(path: Path, scenes: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_COVERAGE_FIELDS)
        writer.writeheader()
        for scene in scenes:
            fault = scene.get("faults", {})
            writer.writerow({
                key: (next(iter(fault.values())) if key == "fault_availability" and fault
                      else None if key == "fault_availability" else scene.get(key))
                for key in _COVERAGE_FIELDS})


def freeze(workspace_root: Path | str, run_dir: Path | str, *, seed: int = SEED) -> dict:
    """Write proposal, runner manifest and full design table once; run no episode."""
    root, run = Path(workspace_root).resolve(), Path(run_dir).resolve()
    proposal_path = run / "design/SOURCE_PROPOSAL.json"
    manifest_path = run / "new_data/global_manifest.json"
    coverage_path = run / "design/DESIGN_COVERAGE.csv"
    if any(path.exists() for path in (proposal_path, manifest_path, coverage_path)):
        raise FileExistsError("V8 source is already frozen")
    proposal = build_proposal(root, seed=seed)
    manifest = build_deepening_manifest(proposal, root)
    _write_new(proposal_path, proposal)
    manifest.pop("signature")
    manifest["four_scenario_proposal_file_sha256"] = _sha(proposal_path)
    manifest["signature"] = digest(manifest)
    _write_new(manifest_path, manifest)
    _write_coverage(coverage_path, proposal["scenarios"])
    return {"proposal_path": str(proposal_path), "proposal_sha256": _sha(proposal_path),
            "manifest_path": str(manifest_path), "manifest_sha256": _sha(manifest_path),
            "coverage_path": str(coverage_path), "coverage_sha256": _sha(coverage_path),
            "roots": 12, "scenarios": 528, "matched_four_cell_groups": 156,
            "episodes_started": 0, "final_12_backgrounds_unopened": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("freeze",))
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    print(json.dumps(freeze(args.workspace, args.run_dir, seed=args.seed), ensure_ascii=False))


if __name__ == "__main__":
    main()
