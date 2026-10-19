"""Freeze matched L1/L3 fault-device crosses on four already-seen day roots.

This is a targeted model-mechanism supplement, not new independent days.  The
choice of roots, devices and availability is fixed before inspecting v8 outcomes.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import build_manifest, digest
from hospital_ies.resilience import validate_scenario
from tools.control_improvement.data_design import validate_observation_support
from tools.four_scenarios.deepening_design import SEVERITY_BY_AVAILABILITY


VERSION = "four_scenarios.v8.fault_cross.v1"
V8_MANIFEST = Path("outputs/v8_four_scenarios_deepening_20261016_run01/new_data/global_manifest.json")
# One previously seen root per quarter.  Each device is tested on a root where
# it was not the initial fault in v7/v8, preserving genuine nonbinding cases.
FAULT_CROSS = {
    51: (("GT_1", 0.5), ("HRSG_1", 0.25)),
    123: (("GRID", 0.0), ("GT_2", 0.5)),
    228: (("GT_1", 0.25), ("BOILER_1", 0.0)),
    363: (("GRID", 0.0), ("ABS", 0.25)),
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signed(value: dict) -> bool:
    return value.get("signature") == digest({k: v for k, v in value.items()
                                               if k != "signature"})


def _source(root: Path) -> dict:
    source = _read(root / V8_MANIFEST)
    if not _signed(source) or len(source.get("scenarios", [])) != 528:
        raise ValueError("Frozen v8 source manifest differs")
    if source.get("policy_search_final_12_backgrounds_unopened") is not True:
        raise ValueError("Final backgrounds are not sealed")
    return source


def build_fault_cross_proposal(workspace_root: Path | str = ROOT) -> dict:
    root = Path(workspace_root).resolve()
    source = _source(root)
    controls = {
        (int(s["source_day_index"]), s["analysis_class"]): s
        for s in source["scenarios"]
        if s.get("design_axis") == "main" and s["analysis_class"] in ("L1", "L3")
    }
    scenes, groups = [], []
    for day, alternatives in FAULT_CROSS.items():
        original_l1 = controls[(day, "L1")]
        original_l3 = controls[(day, "L3")]
        if original_l1["base_state_id"] != original_l3["base_state_id"]:
            raise ValueError("Source L1/L3 initial state differs")
        original_device = original_l3["initiating_device"]
        for device, availability in alternatives:
            if device == original_device or availability not in SEVERITY_BY_AVAILABILITY:
                raise ValueError("Fault cross contains source device or unregistered severity")
            cell_id = "v8_fault_cross:" + digest([VERSION, day, device, availability])[:24]
            pair_ids = {}
            for name, old in (("L1", original_l1), ("L3", original_l3)):
                scene = copy.deepcopy(old)
                scene["id"] = "v8_fault_cross_" + digest([VERSION, day, device,
                                                           availability, name])[:20]
                scene["sample_schema_version"] = VERSION
                scene["design_cell_id"] = cell_id
                scene["design_axis"] = "fault_device_cross"
                scene["design_level"] = f"{device}|availability={availability}"
                scene["sampling_cohort"] = "fault_cross"
                scene["sampling_role"] = "matched_seen_background_L1_L3_device_cross"
                scene["sampling_method"] = "outcome_free_four_quarter_targeted_device_cross"
                scene["distribution_id"] = VERSION
                scene["purpose"] = "v8_seen_background_fault_device_mechanism"
                scene["experiment_role"] = "train"
                scene["is_final_blind"] = False
                scene["exogenous_stream_id"] = "v8_fault_stream:" + digest(
                    [scene["id"], "exogenous"])
                scene["initiating_device"] = device
                scene["faults"] = {device: float(availability)}
                scene["fault_severity_layer"] = SEVERITY_BY_AVAILABILITY[availability]
                scene["exogenous_events"] = [{
                    "origin": "exogenous", "kind": "device_fault",
                    "device": device, "availability": float(availability),
                    "start_minute": scene["fault_start_minute"],
                    "end_minute": scene["fault_start_minute"] + scene["fault_duration_minutes"],
                }]
                if validate_observation_support(scene) != scene["full_source_support"]:
                    raise ValueError("Fault cross changes source support")
                validate_scenario(scene)
                scenes.append(scene)
                pair_ids[name] = scene["id"]
            groups.append({
                "design_cell_id": cell_id,
                "root_family_id": original_l3["root_family_id"],
                "source_day_index": day,
                "season_quarter": original_l3["season_quarter"],
                "source_root_initiating_device": original_device,
                "initiating_device": device,
                "availability": float(availability),
                "fault_start_minute": original_l3["fault_start_minute"],
                "fault_duration_minutes": original_l3["fault_duration_minutes"],
                "base_state_id": original_l3["base_state_id"],
                "scene_ids": pair_ids,
            })
    if len(scenes) != 16 or len(groups) != 8 or len({s["id"] for s in scenes}) != 16:
        raise ValueError("Fault cross inventory differs")
    proposal = {
        "version": VERSION + ".proposal",
        "source_v8_manifest_signature": source["signature"],
        "source_v8_manifest_sha256": _sha(root / V8_MANIFEST),
        "source_v8_seen_root_count": 12,
        "selected_seen_root_count": 4,
        "new_independent_background_count": 0,
        "final_12_backgrounds_unopened": True,
        "selection_registered_before_fault_cross_outcomes": True,
        "experiment_role": "train",
        "scenarios": scenes,
        "matched_fault_groups": groups,
        "source_code_sha256": _sha(Path(__file__)),
    }
    proposal["signature"] = digest(proposal)
    return proposal


def build_fault_cross_manifest(proposal: dict,
                               workspace_root: Path | str = ROOT) -> dict:
    root = Path(workspace_root).resolve()
    if not _signed(proposal) or proposal != build_fault_cross_proposal(root):
        raise ValueError("Fault cross proposal differs from source registration")
    source = _source(root)
    old_by = {(s["source_day_index"], s["analysis_class"]): s
              for s in source["scenarios"]
              if s.get("design_axis") == "main" and s["analysis_class"] in ("L1", "L3")}
    splits = {}
    for scene in proposal["scenarios"]:
        old = old_by[(scene["source_day_index"], scene["analysis_class"])]
        split = copy.deepcopy(source["splits"][old["id"]])
        split["split_version"] = VERSION
        split["purpose"] = scene["purpose"]
        splits[scene["id"]] = split
    manifest = build_manifest(root, proposal["scenarios"],
                              ("priority_milp", "secure_milp"),
                              source["execution_contract"], source["schemas"],
                              source["options"], splits)
    manifest.pop("signature")
    manifest.update({
        "manifest_version": VERSION + ".manifest",
        "source_v8_manifest_signature": proposal["source_v8_manifest_signature"],
        "source_v8_manifest_sha256": proposal["source_v8_manifest_sha256"],
        "fault_cross_source_signature": proposal["signature"],
        "fault_cross_source_code_sha256": proposal["source_code_sha256"],
        "matched_fault_groups": proposal["matched_fault_groups"],
        "fault_cross_seen_roots": 4,
        "fault_cross_specifications": 16,
        "new_independent_background_count": 0,
        "policy_search_final_12_backgrounds_unopened": True,
    })
    manifest["signature"] = digest(manifest)
    return manifest


def freeze(workspace_root: Path | str, run_dir: Path | str) -> dict:
    root, run = Path(workspace_root).resolve(), Path(run_dir).resolve()
    proposal_path = run / "design/SOURCE_PROPOSAL.json"
    manifest_path = run / "new_data/global_manifest.json"
    coverage_path = run / "design/FAULT_CROSS_COVERAGE.csv"
    if any(path.exists() for path in (proposal_path, manifest_path, coverage_path)):
        raise FileExistsError("Fault cross source is already frozen")
    proposal = build_fault_cross_proposal(root)
    manifest = build_fault_cross_manifest(proposal, root)
    for path in (proposal_path, manifest_path, coverage_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    with proposal_path.open("x", encoding="utf-8") as stream:
        json.dump(proposal, stream, ensure_ascii=False, indent=2, allow_nan=False)
    with manifest_path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
    with coverage_path.open("x", encoding="utf-8", newline="") as stream:
        columns = ("design_cell_id", "source_day_index", "season_quarter",
                   "source_root_initiating_device", "initiating_device", "availability",
                   "fault_start_minute", "fault_duration_minutes", "L1_scene_id", "L3_scene_id")
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for group in proposal["matched_fault_groups"]:
            writer.writerow({**{key: group[key] for key in columns[:-2]},
                             "L1_scene_id": group["scene_ids"]["L1"],
                             "L3_scene_id": group["scene_ids"]["L3"]})
    return {"proposal_path": str(proposal_path), "proposal_sha256": _sha(proposal_path),
            "manifest_path": str(manifest_path), "manifest_sha256": _sha(manifest_path),
            "coverage_path": str(coverage_path), "coverage_sha256": _sha(coverage_path),
            "scenarios": 16, "matched_fault_groups": 8,
            "episodes_started": 0, "final_12_backgrounds_unopened": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("freeze",))
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(freeze(args.workspace, args.run_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
