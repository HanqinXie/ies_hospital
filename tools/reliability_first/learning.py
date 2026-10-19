"""Build secure-relative fit and independent calibration inputs from frozen branches."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from tools.control_improvement.ranking import fit_demand_scale
from tools.reliability_first.collect import _result_ok, _save_once, _sha, registered_attempts


def _find_result(run_dir: Path, index: int, row: dict, selection: dict, *,
                 manifest_signature: str):
    paths = []
    reuse = selection.get("pilot_reuse", {})
    if index == reuse.get("index"):
        paths.append(run_dir / reuse["path"] / "BRANCH_RESULT.json")
    stage = "early" if index in selection["early_indices"] else "remaining"
    paths.extend(p / "BRANCH_RESULT.json" for p in registered_attempts(run_dir, index, stage))
    if len(paths) > 1:
        raise ValueError(f"Multiple registered successful branch attempts for frozen index {index}")
    for path in paths:
        ok, result = _result_ok(path, row, selection["branch_source_sha256"],
                                manifest_signature=manifest_signature)
        if ok:
            return path, result
    raise FileNotFoundError(f"No completed branch result for frozen index {index}")


def _verify_raw_trajectory_hashes(result_path: Path, result: dict) -> int:
    """Check all complete-branch raw bytes against their frozen result receipt."""
    hashes = result.get("raw_trajectory_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("Complete branch lacks raw trajectory hash inventory")
    attempt = result_path.parent
    actual = {p.name for p in attempt.glob("*.json.gz") if p.is_file()}
    if set(hashes) != actual or "reference_capture.json.gz" not in hashes or "D0_secure.json.gz" not in hashes:
        raise ValueError("Complete branch raw trajectory inventory differs")
    for name, expected in hashes.items():
        if (Path(name).name != name or not isinstance(expected, str)
                or len(expected) != 64 or _sha(attempt / name) != expected):
            raise ValueError(f"Complete branch raw trajectory hash differs: {name}")
    for outcome in result.get("outcomes", []):
        name = outcome.get("trajectory_file")
        if name not in hashes or outcome.get("trajectory_sha256") != hashes[name]:
            raise ValueError("Branch outcome raw trajectory reference differs")
    return len(hashes)


def assemble_fit_input(run_dir: Path, *, stage: str) -> tuple[dict, dict, dict]:
    run_dir = Path(run_dir).resolve()
    selection_path = run_dir / "design/branch_collection_v1.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    plan_path = run_dir / "design/snapshot_plan_v2.json"
    manifest_path = run_dir / "new_data/global_manifest.json"
    if (_sha(plan_path) != selection["plan_sha256"]
            or _sha(manifest_path) != selection["manifest_sha256"]):
        raise ValueError("Collection source identity changed")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if stage not in {"pilot8", "early", "full"}:
        raise ValueError("stage must be pilot8, early or full")
    indices = list(selection["early_indices"][:8] if stage == "pilot8"
                   else selection["early_indices"])
    if stage == "full":
        indices += list(selection["remaining_indices"])
    early_validation_groups = {plan[i]["group_id"] for i in selection["early_indices"]
                               if plan[i]["experiment_role"] == "pilot_validation"}
    partitions = {"train": [], "validation": [], "calibration": []}
    statuses = Counter()
    result_shas = {}
    source_paths = {}
    provenance_sidecars = {}
    verified_raw_count = 0
    for index in indices:
        row = plan[index]
        if row["experiment_role"] not in {"pilot_train", "pilot_validation"}:
            raise ValueError("Training input contains development or blind group")
        path, result = _find_result(run_dir, index, row, selection,
                                    manifest_signature=manifest["signature"])
        statuses[result["status"]] += 1
        result_shas[str(index)] = _sha(path)
        source_paths[str(index)] = str(path.relative_to(run_dir))
        if result.get("legacy_provenance_verified"):
            provenance_sidecars[str(index)] = result["legacy_provenance_sidecar_sha256"]
        if result["status"] != "complete":
            continue
        verified_raw_count += _verify_raw_trajectory_hashes(path, result)
        partition = ("train" if row["experiment_role"] == "pilot_train"
                     else "validation" if row["group_id"] in early_validation_groups
                     else "calibration")
        rows = result["fit_rows"]
        if not rows or rows[0]["candidate_index"] != 0 or rows[0]["candidate_id"] != "secure_baseline":
            raise ValueError(f"Branch {index} lacks original secure candidate zero")
        if [r["candidate_index"] for r in rows] != list(range(len(rows))):
            raise ValueError(f"Branch {index} candidate indices are not contiguous")
        if any(r["group_id"] != row["group_id"] or r["scenario_id"] != row["scenario_id"]
               for r in rows):
            raise ValueError(f"Branch {index} row split or scenario mismatch")
        expected_role = "train" if partition == "train" else "validation"
        if any(r["role"] != expected_role for r in rows):
            raise ValueError(f"Branch {index} fit role mismatch")
        partitions[partition].extend(rows)
    groups = {name: {r["group_id"] for r in rows} for name, rows in partitions.items()}
    if (groups["train"] & groups["validation"] or groups["train"] & groups["calibration"]
            or groups["validation"] & groups["calibration"]):
        raise ValueError("Training, selection and calibration group overlap")
    if not partitions["train"] or not partitions["validation"]:
        raise ValueError("Fit needs reached independent train and validation snapshots")
    baseline_rows = [r for r in partitions["train"] if r["candidate_index"] == 0]
    scale = fit_demand_scale(baseline_rows, 120.0).tolist()
    binding = {"manifest_sha256": _sha(manifest_path),
               "manifest_signature": manifest.get("signature"),
               "snapshot_plan_sha256": _sha(plan_path),
               "collection_design_sha256": _sha(selection_path),
               "branch_source_sha256": selection["branch_source_sha256"],
               "branch_result_sha256_by_index": result_shas,
               "legacy_terminal_provenance_sha256_by_index": provenance_sidecars,
               "stage": stage,
               "continuation_policy_id": "secure_milp",
               "label_aggregation": "coordinatewise_max_named_stress_set_no_probability"}
    fit = {"schema": "reliability_first.fit_input.v1",
           "train": partitions["train"], "validation": partitions["validation"],
           "source_binding": binding, "scale_kWh": scale}
    calibration = {"schema": "reliability_first.calibration_input.v1",
                   "rows": partitions["calibration"], "source_binding": binding,
                   "excluded_from_fit": True}
    unknown = {name: sum(not observed for r in rows for observed in r["target_mask"])
               for name, rows in partitions.items()}
    audit = {"schema": "reliability_first.fit_source_audit.v1", "stage": stage,
             "snapshot_count": len(indices), "status_counts": dict(statuses),
             "rows_by_partition": {name: len(rows) for name, rows in partitions.items()},
             "groups_by_partition": {name: len(group) for name, group in groups.items()},
             "unknown_target_values_by_partition": unknown,
             "source_result_paths_by_index": source_paths,
             "verified_raw_trajectory_file_count": verified_raw_count,
             "legacy_terminal_provenance_sha256_by_index": provenance_sidecars,
             "unknown_and_unreached_retained": True,
             "final_12_backgrounds_excluded": True}
    return fit, calibration, audit


def write_fit_input(run_dir: Path, stage: str, out: Path) -> dict:
    out = Path(out)
    if out.exists():
        raise FileExistsError("Use a fresh versioned learning input directory")
    fit, calibration, audit = assemble_fit_input(run_dir, stage=stage)
    out.mkdir(parents=True)
    _save_once(out / "fit_input.json", fit)
    _save_once(out / "calibration_input.json", calibration)
    _save_once(out / "SOURCE_DATA_AUDIT.json", audit)
    return {"stage": stage, "fit_input_sha256": _sha(out / "fit_input.json"),
            "calibration_input_sha256": _sha(out / "calibration_input.json"),
            "audit_sha256": _sha(out / "SOURCE_DATA_AUDIT.json"),
            "rows_by_partition": audit["rows_by_partition"],
            "status_counts": audit["status_counts"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--stage", required=True, choices=("pilot8", "early", "full"))
    args = parser.parse_args()
    print(json.dumps(write_fit_input(args.run_dir, args.stage, args.output_directory),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
