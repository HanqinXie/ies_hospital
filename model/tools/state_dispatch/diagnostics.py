"""Seven bounded execution-assumption diagnostics and saved-raw timing export.

This module never registers a batch or executes an environment. Gate changes
are diagnostic assumptions, not improvements of the baseline controllers.
"""
from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from hospital_ies.dataset_manifest import build_manifest, canonical_payload, digest
from hospital_ies.resilience import validate_scenario

VERSION = "state_dispatch.v9.timing_diagnostics.v1"
POLICIES = ["priority_milp", "secure_milp"]
SOURCE_MANIFESTS = (
    "outputs/v8_four_scenarios_deepening_20261016_run01/new_data/global_manifest.json",
    "outputs/v8_four_scenarios_clockmatched_20261016_run01/new_data/global_manifest.json",
)
SOURCE_IDS = ("v8_case_9a0d280553f4c659b321", "v8_cm_9611c60c94256c1a2ed4c149")
SAVED_PRIORITY_RAWS = {
    "day298": (
        "outputs/v8_four_scenarios_deepening_20261016_run01/main/results/pairs/40f194e267d726274afa819e8a7963e70b9ebb9bcc701ce93ba2a6aa001fc011/attempt_0001/trajectory.json.gz",
        "445b8af40677d35667ca39304dccd5bba19437a6c4e5a4253c097cb14e627072"),
    "day341": (
        "outputs/v8_four_scenarios_clockmatched_20261016_run01/main/results/pairs/a25cd586a7f5f99c7e2adb845ea5579d162e68d03dc4f21b3a6f23f151f3f6c9/attempt_0001/trajectory.json.gz",
        "c0ae25c6e538cf49439bf8efb5b334b48f8d511d258a3d0c0b892292c7ea8ae3"),
}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _signed(value):
    return value.get("signature") == digest({k: v for k, v in value.items() if k != "signature"})


def _sources(root):
    result = []
    for relative, sid in zip(SOURCE_MANIFESTS, SOURCE_IDS):
        manifest = _read(root / relative)
        if not _signed(manifest) or manifest.get("policy_search_final_12_backgrounds_unopened") is not True:
            raise ValueError("Frozen source manifest signature or blind-background guard differs")
        matches = [s for s in manifest["scenarios"] if s["id"] == sid]
        if len(matches) != 1 or matches[0].get("is_final_blind") is not False:
            raise ValueError("Expected seen-background source scenario absent or not eligible")
        result.append((manifest, matches[0]))
    if result[0][0]["execution_contract"] != result[1][0]["execution_contract"]:
        raise ValueError("Source execution contracts differ")
    return result


def build_proposal(workspace_root=ROOT):
    root = Path(workspace_root).resolve()
    sources = _sources(root)
    variants = (
        (0, "day298_delay30", {}, 150),
        (0, "day298_delay15", {"response_delay_minutes": 15.0}, 150),
        (0, "day298_delay0", {"response_delay_minutes": 0.0}, 150),
        (1, "day341_shadow", {}, 120),
        (1, "day341_gate_t0", {"shock_start_minute": 0.0, "fault_start_minute": 0.0,
                               "shadow_event_clock_only": False}, 0),
        (1, "day341_delay0", {"response_delay_minutes": 0.0}, 120),
        # L3/L4 validation requires propagation.  The supported L2 adapter
        # disables it without a core bypass; both stages anchor response at
        # the shock clock, and this N4 case has no fault/shock events.
        (1, "day341_no_propagation", {"enable_propagation": False,
                                      "stage": 2, "event_class": "L2"}, 120),
    )
    scenes = []
    for index, label, changes, prefix_end in variants:
        parent = sources[index][1]
        scene = copy.deepcopy(parent)
        scene.update(copy.deepcopy(changes))
        scene.update(id="v9_diag_" + digest([VERSION, label, parent["id"], changes])[:24],
                     diagnostic_variant=label, diagnostic_parent_scenario_id=parent["id"],
                     diagnostic_common_prefix_end_minute=float(prefix_end),
                     diagnostic_assumption_changes=copy.deepcopy(changes),
                     diagnostic_semantics="execution_assumption_diagnostic_not_policy_improvement",
                     diagnostic_prefix_note=("gate_t0 differs in model gates from time zero; no asserted nonempty common prefix"
                                             if prefix_end == 0 else
                                             "physical and legal-observation prefix must be checked before the changed gate"),
                     purpose="v9_seen_background_execution_gate_diagnostic", experiment_role="train",
                     reference_role="execution_assumption_diagnostic", is_final_blind=False,
                     distribution_id=VERSION + ".q_design", sample_schema_version=VERSION,
                     sampling_cohort="timing_gate_diagnostic", design_axis="execution_gate",
                     design_level=label)
        validate_scenario(scene)
        scenes.append(scene)
    proposal = {"version": VERSION, "seed": sources[0][0]["options"]["seed"], "scenarios": scenes,
                "policies": POLICIES.copy(), "final_12_backgrounds_unopened": True,
                "new_independent_backgrounds": 0, "diagnostic_scene_count": 7,
                "source_manifests": {relative: {"sha256": _sha(root / relative),
                                               "signature": source[0]["signature"]}
                                     for relative, source in zip(SOURCE_MANIFESTS, sources)},
                "interpretation": "same exogenous streams; assumption variants; no policy superiority or risk denominator"}
    proposal["signature"] = digest(proposal)
    return proposal


def build_diagnostic_manifest(proposal, workspace_root=ROOT):
    root = Path(workspace_root).resolve()
    if not _signed(proposal) or proposal != build_proposal(root):
        raise ValueError("Diagnostic proposal differs from its signed frozen sources")
    source = _sources(root)[0][0]
    splits = {}
    for scene in proposal["scenarios"]:
        support = scene["full_source_support"]
        splits[scene["id"]] = dict(
            group_id=scene["root_family_id"], split="train", experiment_role="train",
            fit_allowed=False, selection_allowed=False, development_only=True,
            split_version=VERSION, embargo_minutes_each_side=60,
            raw_start_minute=support["raw_start_minute"], raw_end_minute=support["raw_end_minute"],
            time_block_id=scene["time_block_id"], purpose=scene["purpose"], is_final_blind=False)
    manifest = build_manifest(root, proposal["scenarios"], POLICIES,
                              copy.deepcopy(source["execution_contract"]), copy.deepcopy(source["schemas"]),
                              copy.deepcopy(source["options"]), splits)
    manifest.pop("signature")
    manifest.update(manifest_version=VERSION + ".manifest", diagnostic_proposal_signature=proposal["signature"],
                    diagnostic_source_manifests=proposal["source_manifests"],
                    diagnostic_tool_sha256=_sha(Path(__file__)),
                    diagnostic_semantics="execution_assumption_diagnostic_not_policy_improvement",
                    policy_search_final_12_backgrounds_unopened=True)
    manifest["signature"] = digest(manifest)
    return manifest


def _write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def freeze_diagnostics(workspace_root, run_dir):
    root, run = Path(workspace_root).resolve(), Path(run_dir).resolve()
    proposal_path = run / "design/SOURCE_PROPOSAL.json"
    manifest_path = run / "new_data/global_manifest.json"
    if proposal_path.exists() or manifest_path.exists():
        raise FileExistsError("Diagnostic design is already frozen")
    proposal = build_proposal(root)
    manifest = build_diagnostic_manifest(proposal, root)
    _write_new(proposal_path, proposal)
    _write_new(manifest_path, manifest)
    return {"proposal_path": str(proposal_path), "proposal_sha256": _sha(proposal_path),
            "manifest_path": str(manifest_path), "manifest_sha256": _sha(manifest_path),
            "scenarios": 7, "planned_baseline_pairs": 14, "episodes_started": 0,
            "final_12_backgrounds_unopened": True}


TIMING_FIELDS = (
    "raw_sha256", "scenario_id", "policy_id", "record_type", "record_index",
    "time_minute", "end_minute", "event_kind", "event_id", "command_id", "generated_minute",
    "arrival_minute", "superseded_by", "device", "dispatch_role", "GT_1_kW", "GT_2_kW", "GT_3_kW",
    "grid_import_kW", "boiler_steam_lbph", "hrsg_steam_lbph", "P0_e_unserved_kW",
    "P0_h_unserved_kW", "P0_c_unserved_kW", "stress_ratio", "power_surplus_kW", "details_json")


def timing_rows(payload, raw_sha256=""):
    if payload.get("identity", {}).get("is_final_blind") is True:
        raise ValueError("Final blind raw is outside diagnostic scope")
    common = {"raw_sha256": raw_sha256, "scenario_id": payload["scenario"]["id"],
              "policy_id": payload["identity"]["policy_id"]}
    rows = []
    for index, event in enumerate(payload.get("events", [])):
        row = dict(common, record_type="event", record_index=index,
                   time_minute=event["time_minute"], event_kind=event["kind"],
                   event_id=event.get("event_id"), command_id=event.get("command_id"),
                   generated_minute=event.get("generated_minute"), arrival_minute=event.get("arrival_minute"),
                   superseded_by=event.get("by"), device=event.get("device"),
                   stress_ratio=event.get("stress_ratio"), power_surplus_kW=event.get("power_surplus_kW"),
                   details_json=json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        action = {}
        if event["kind"] == "command_generated":
            command = event["command"]
            row.update(command_id=command["id"], generated_minute=command["generated_minute"],
                       arrival_minute=command["arrival_minute"], dispatch_role="planned_request")
            action = command["dispatch"]
        elif event["kind"] == "command_executed":
            row["dispatch_role"] = "boundary_executed_setpoint_not_interval_delivery"
            action = event["executed_dispatch"]
        if action:
            row.update({f"GT_{i+1}_kW": action["gt_electric_kW"][i] for i in range(3)})
            row.update({key: action.get(key) for key in ("grid_import_kW", "boiler_steam_lbph")})
        rows.append(row)
    for index, segment in enumerate(payload.get("segments", [])):
        action = segment["action"]
        row = dict(common, record_type="segment", record_index=index, time_minute=segment["start_minute"],
                   end_minute=segment["end_minute"], command_id=segment.get("command_id"),
                   dispatch_role="actual_interval_delivery", event_kind="interval",
                   details_json=json.dumps({"stress": segment["protection_stress"],
                                            "debug": segment["protection_debug"]}, separators=(",", ":")))
        row.update({f"GT_{i+1}_kW": action["gt_electric_kW"][i] for i in range(3)})
        row.update({key: action.get(key) for key in ("grid_import_kW", "boiler_steam_lbph", "hrsg_steam_lbph")})
        row.update({f"P0_{carrier}_unserved_kW": segment["unserved_kW"][0][i]
                    for i, carrier in enumerate(("e", "h", "c"))})
        rows.append(row)
    return sorted(rows, key=lambda r: (r["time_minute"], r["record_type"] != "event", r["record_index"]))


def extract_timing(raw_path, csv_path, *, expected_sha256=None):
    raw_path, csv_path = Path(raw_path), Path(csv_path)
    if csv_path.exists():
        raise FileExistsError("Timing export already exists")
    sha = _sha(raw_path)
    if expected_sha256 is not None and sha != expected_sha256:
        raise ValueError("Saved raw SHA256 differs")
    with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    rows = timing_rows(payload, sha)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=TIMING_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    receipt = {"raw_path": str(raw_path.resolve()), "raw_sha256": sha,
               "csv_path": str(csv_path.resolve()), "csv_sha256": _sha(csv_path), "records": len(rows),
               "scenario_id": payload["scenario"]["id"], "policy_id": payload["identity"]["policy_id"],
               "new_trajectory_attempts": 0}
    _write_new(csv_path.with_suffix(".sha256.json"), receipt)
    return receipt


def compare_prefix(left, right, end_minute):
    """Compare completed pre-gate physical segments and legal observations only.

    Identity metadata and future labels are deliberately excluded. Empty prefix
    equality is explicitly marked vacuous, rather than evidence of matching.
    """
    if end_minute < 0:
        raise ValueError("Prefix end must be nonnegative")
    for key in ("base_state_id", "exogenous_stream_id"):
        if left["identity"].get(key) != right["identity"].get(key):
            raise ValueError("Different source initial state or exogenous stream")
    physical_keys = ("start_minute", "end_minute", "availability", "pre_state", "post_state", "action",
                     "loads_kW", "connected_loads_kW", "disconnected_loads_kW", "unserved_kW",
                     "protection_stress", "protection_debug")
    def physical(payload):
        return [{k: s[k] for k in physical_keys} for s in payload["segments"]
                if s["end_minute"] <= end_minute]
    def observations(payload):
        return [{k: d[k] for k in ("decision_time_min", "features", "known_command_queue", "arrived_command_id")}
                for d in payload["decisions"] if d["decision_time_min"] < end_minute]
    lp, rp = physical(left), physical(right)
    lo, ro = observations(left), observations(right)
    return {"prefix_end_minute": end_minute, "physical_segments": [len(lp), len(rp)],
            "observation_decisions": [len(lo), len(ro)], "vacuous_prefix": not lp and not rp,
            "physical_prefix_equal": canonical_payload(lp) == canonical_payload(rp),
            "observation_prefix_equal": canonical_payload(lo) == canonical_payload(ro),
            "physical_prefix_sha256": [digest(canonical_payload(lp)), digest(canonical_payload(rp))],
            "observation_prefix_sha256": [digest(canonical_payload(lo)), digest(canonical_payload(ro))]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("freeze", "extract-saved"))
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "freeze":
        result = freeze_diagnostics(args.workspace, args.run_dir)
    else:
        result = {label: extract_timing(args.workspace / raw, args.run_dir / (label + "_priority_timing.csv"),
                                        expected_sha256=sha)
                  for label, (raw, sha) in SAVED_PRIORITY_RAWS.items()}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
