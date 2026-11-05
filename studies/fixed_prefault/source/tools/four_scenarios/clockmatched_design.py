"""Freeze separate no-load/no-fault N4 controls with matched shadow clocks.

The original v8 528-scene manifest and plan stay immutable.  These 24 controls
reuse its twelve seen backgrounds.  A shadow clock is an explicit research
condition on command delay and propagation gating, not a physical shock.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.data_contract import SCHEMA_VERSIONS
from hospital_ies.dataset_manifest import build_manifest, digest
from hospital_ies.resilience import validate_scenario
from hospital_ies.state_transition import event_window
from tools.control_improvement.data_design import validate_observation_support


VERSION = "four_scenarios.v8.clockmatched_design.v1"
SEED = 20261016
V8_MANIFEST = Path("outputs/v8_four_scenarios_deepening_20261016_run01/new_data/global_manifest.json")
V8_PROPOSAL = Path("outputs/v8_four_scenarios_deepening_20261016_run01/design/SOURCE_PROPOSAL.json")
LOAD_FILE = Path("inputs/loads/hospital_ies_demand_8760.csv")
CONTRACT_FILE = Path("configs/p1b_execution_research.json")
FOUR_CLASSES = ("N4", "L3", "L4a", "L4b")


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


def _source(root: Path) -> tuple[dict, dict[str, dict], list[dict]]:
    manifest = _read(root / V8_MANIFEST)
    if (not _signed(manifest) or len(manifest.get("scenarios", [])) != 528 or
            len(manifest.get("matched_four_cell_groups", [])) != 156 or
            manifest.get("policy_search_final_12_backgrounds_unopened") is not True):
        raise ValueError("Frozen v8 source design signature or denominator differs")
    proposal_path = root / V8_PROPOSAL
    if (not proposal_path.is_file() or
            manifest.get("four_scenario_proposal_file_sha256") != _sha(proposal_path)):
        raise ValueError("Frozen v8 source proposal bytes differ")
    by_id = {scene["id"]: scene for scene in manifest["scenarios"]}
    if len(by_id) != 528:
        raise ValueError("Frozen v8 scene ID inventory differs")
    groups = manifest["matched_four_cell_groups"]
    if len({group["design_cell_id"] for group in groups}) != 156:
        raise ValueError("Frozen v8 matched-cell identities differ")
    return manifest, by_id, groups


def _checked_group(group: dict, by_id: dict[str, dict]) -> dict[str, dict]:
    ids = group["scene_ids"]
    if set(ids) != set(FOUR_CLASSES):
        raise ValueError("Frozen v8 four-cell class mapping differs")
    scenes = {name: by_id[ids[name]] for name in FOUR_CLASSES}
    if any(scene["analysis_class"] != name for name, scene in scenes.items()):
        raise ValueError("Frozen v8 four-cell scene class differs")
    if len({scene["base_state_id"] for scene in scenes.values()}) != 1:
        raise ValueError("Frozen v8 four-cell initial state differs")
    if len({scene["response_delay_minutes"] for scene in scenes.values()}) != 1:
        raise ValueError("Frozen v8 four-cell response delay differs")
    if (group["fault_start_minute"] != scenes["L3"]["fault_start_minute"] or
            group["fault_start_minute"] != scenes["L4b"]["fault_start_minute"] or
            group["shock_start_minute"] != scenes["L4a"]["shock_start_minute"] or
            group["shock_start_minute"] != scenes["L4b"]["shock_start_minute"] or
            group["fault_start_minute"] != group["shock_start_minute"]):
        raise ValueError("Frozen v8 fault/shock absolute clocks do not align")
    if (group["shock_duration_minutes"] != scenes["L4a"]["shock_duration_minutes"] or
            group["shock_duration_minutes"] != scenes["L4b"]["shock_duration_minutes"] or
            group["fault_duration_minutes"] != scenes["L3"]["fault_duration_minutes"] or
            group["fault_duration_minutes"] != scenes["L4b"]["fault_duration_minutes"]):
        raise ValueError("Frozen v8 matched event duration differs")
    if (group["response_delay_minutes"] != scenes["N4"]["response_delay_minutes"] or
            group["root_family_id"] != scenes["N4"]["root_family_id"]):
        raise ValueError("Frozen v8 control clock or root differs")
    return scenes


def _shadow_scene(normal: dict, group: dict, key: str) -> dict:
    scene = copy.deepcopy(normal)
    scene["id"] = "v8_cm_" + digest([VERSION, key, group["shock_start_minute"],
                                      group["shock_duration_minutes"],
                                      group["fault_duration_minutes"],
                                      group["response_delay_minutes"]])[:24]
    scene["analysis_class"] = "N4_clockmatched"
    scene["reference_role"] = "no_load_no_fault_clockmatched_control"
    scene["shadow_event_clock_only"] = True
    scene["sample_schema_version"] = VERSION
    scene["design_cell_id"] = "v8_clock_control:" + digest([VERSION, key])[:24]
    scene["design_axis"] = group["design_axis"]
    scene["design_level"] = group["design_level"]
    scene["sampling_cohort"] = "clockmatched_supplement"
    scene["sampling_role"] = "seen_background_no_load_no_fault_shadow_clock"
    scene["sampling_method"] = "frozen_v8_group_clock_match_no_outcome_access"
    scene["distribution_id"] = VERSION + ".q_design"
    scene["purpose"] = "clockmatched_four_cell_interaction_control_only"
    scene["experiment_role"] = "train"
    scene["is_final_blind"] = False
    scene["exogenous_stream_id"] = "v8_cm_stream:" + digest([scene["id"], "empty_exogenous"])
    scene["shock_start_minute"] = float(group["shock_start_minute"])
    scene["shock_duration_minutes"] = float(group["shock_duration_minutes"])
    scene["fault_start_minute"] = float(group["fault_start_minute"])
    scene["fault_duration_minutes"] = float(group["fault_duration_minutes"])
    scene["response_delay_minutes"] = float(group["response_delay_minutes"])
    scene["shock"] = {}
    scene["shock_by_priority"] = {}
    scene["faults"] = {}
    scene["exogenous_events"] = []
    scene["actual_p0_load_multiplier"] = {"e": 1.0, "h": 1.0, "c": 1.0}
    if (event_window(scene, "shock")[0] != group["shock_start_minute"] or
            validate_observation_support(scene) != normal["full_source_support"]):
        raise ValueError("Clockmatched control changes original source support or gate")
    validate_scenario(scene)
    return scene


def build_proposal(workspace_root: Path | str = ROOT, *, seed: int = SEED) -> dict:
    """Construct 24 no-shock/no-fault controls; do not read simulation outcomes."""
    if type(seed) is not int or seed < 0:
        raise ValueError("Design seed must be a nonnegative integer")
    root = Path(workspace_root).resolve()
    old_manifest, by_id, groups = _source(root)
    scenes: list[dict] = []
    mapping: list[dict] = []
    shared: dict[str, dict] = {}
    shared_clock: dict[str, tuple] = {}
    for group in groups:
        four = _checked_group(group, by_id)
        key = (group["root_family_id"] if group["design_axis"] == "main"
               else group["design_cell_id"])
        clock = tuple(group[name] for name in (
            "shock_start_minute", "shock_duration_minutes",
            "fault_start_minute", "fault_duration_minutes", "response_delay_minutes"))
        if key in shared:
            if shared_clock[key] != clock or four["N4"]["id"] != shared[key]["source_n4_id"]:
                raise ValueError("One shared main root has divergent shadow clocks")
            shadow = shared[key]["scene"]
        else:
            shadow = _shadow_scene(four["N4"], group, key)
            shadow["source_n4_id"] = four["N4"]["id"]
            shared[key] = {"scene": shadow, "source_n4_id": four["N4"]["id"]}
            shared_clock[key] = clock
            scenes.append(shadow)
        mapping.append({
            "design_cell_id": group["design_cell_id"],
            "design_axis": group["design_axis"],
            "design_level": group["design_level"],
            "root_family_id": group["root_family_id"],
            "season_quarter": group["season_quarter"],
            "original_scene_ids": copy.deepcopy(group["scene_ids"]),
            "clockmatched_scene_id": shadow["id"],
            "base_state_id": group["base_state_id"],
            "shock_start_minute": group["shock_start_minute"],
            "shock_duration_minutes": group["shock_duration_minutes"],
            "fault_start_minute": group["fault_start_minute"],
            "fault_duration_minutes": group["fault_duration_minutes"],
            "response_delay_minutes": group["response_delay_minutes"],
        })
    if (len(scenes) != 24 or len(mapping) != 156 or
            len({scene["id"] for scene in scenes}) != len(scenes) or
            len({scene["root_family_id"] for scene in scenes}) != 12):
        raise ValueError("Clockmatched supplement denominator differs")
    source_sha = {
        V8_MANIFEST.as_posix(): _sha(root / V8_MANIFEST),
        V8_PROPOSAL.as_posix(): _sha(root / V8_PROPOSAL),
        LOAD_FILE.as_posix(): _sha(root / LOAD_FILE),
        CONTRACT_FILE.as_posix(): _sha(root / CONTRACT_FILE),
        "tools/four_scenarios/clockmatched_design.py": _sha(Path(__file__)),
    }
    proposal = {
        "version": VERSION + ".proposal", "seed": seed,
        "source_v8_manifest_path": V8_MANIFEST.as_posix(),
        "source_v8_manifest_sha256": source_sha[V8_MANIFEST.as_posix()],
        "source_v8_manifest_signature": old_manifest["signature"],
        "source_v8_group_count": 156,
        "scenarios": scenes,
        "matched_clock_control_groups": mapping,
        "analysis_class_counts": dict(Counter(scene["analysis_class"] for scene in scenes)),
        "design_axis_counts": dict(Counter(scene["design_axis"] for scene in scenes)),
        "reference_semantics": "No fault or load shock; timed command delay and propagation gate are shadow research conditions, not a true undisturbed normal reference",
        "experiment_role": "train",
        "independent_background_semantics": "same twelve seen v7/v8 day roots; no new blind background",
        "final_12_backgrounds_unopened": True,
        "source_sha256": source_sha,
    }
    proposal["signature"] = digest(proposal)
    return proposal


def verify_proposal(proposal: dict, workspace_root: Path | str = ROOT) -> bool:
    if not isinstance(proposal, dict) or not _signed(proposal):
        raise ValueError("Clockmatched proposal signature differs")
    if proposal != build_proposal(workspace_root, seed=proposal["seed"]):
        raise ValueError("Clockmatched proposal differs from frozen v8 source")
    return True


def build_clockmatched_manifest(proposal: dict, workspace_root: Path | str = ROOT,
                                *, proposal_path: Path | None = None) -> dict:
    root = Path(workspace_root).resolve()
    verify_proposal(proposal, root)
    scenes = proposal["scenarios"]
    splits = {}
    for scene in scenes:
        support = scene["full_source_support"]
        splits[scene["id"]] = {
            "group_id": scene["root_family_id"], "split": "train",
            "experiment_role": "train", "fit_allowed": False,
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
        clockmatched_source_signature=proposal["signature"],
        clockmatched_source_sha256=proposal["source_sha256"],
        clockmatched_proposal_file_sha256=(_sha(proposal_path) if proposal_path else None),
        clockmatched_roots=12,
        clockmatched_specifications=len(scenes),
        source_v8_manifest_path=proposal["source_v8_manifest_path"],
        source_v8_manifest_sha256=proposal["source_v8_manifest_sha256"],
        source_v8_manifest_signature=proposal["source_v8_manifest_signature"],
        matched_clock_control_groups=proposal["matched_clock_control_groups"],
        reference_semantics=proposal["reference_semantics"],
        policy_search_final_12_backgrounds_unopened=True,
    )
    manifest["signature"] = digest(manifest)
    return manifest


def freeze(workspace_root: Path | str, run_dir: Path | str, *, seed: int = SEED) -> dict:
    """Write the standalone supplement once; never start a solver episode."""
    root, run = Path(workspace_root).resolve(), Path(run_dir).resolve()
    proposal_path = run / "design/SOURCE_PROPOSAL.json"
    manifest_path = run / "new_data/global_manifest.json"
    if proposal_path.exists() or manifest_path.exists():
        raise FileExistsError("Clockmatched supplement already frozen")
    proposal = build_proposal(root, seed=seed)
    manifest = build_clockmatched_manifest(proposal, root)
    _write_new(proposal_path, proposal)
    manifest.pop("signature")
    manifest["clockmatched_proposal_file_sha256"] = _sha(proposal_path)
    manifest["signature"] = digest(manifest)
    _write_new(manifest_path, manifest)
    return {
        "proposal_path": str(proposal_path), "proposal_sha256": _sha(proposal_path),
        "manifest_path": str(manifest_path), "manifest_sha256": _sha(manifest_path),
        "roots": 12, "scenarios": 24, "mapped_four_cell_groups": 156,
        "episodes_started": 0, "final_12_backgrounds_unopened": True,
    }


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
