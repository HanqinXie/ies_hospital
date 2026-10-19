"""Register and run matched secure/rule/learned v4 development comparisons."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.reliability_first.closed_loop import register_closed_loop, run_closed_loop
from tools.reliability_first.selection import build_current_policy, _registered_calibration


ROOT = Path(__file__).resolve().parents[2]
LOGIC_FILES = ("tools/reliability_first/run_eval.py",
               "tools/reliability_first/selection.py",
               "tools/reliability_first/calibration.py",
               "tools/reliability_first/neural.py",
               "tools/reliability_first/tree.py",
               "tools/control_improvement/candidates.py",
               "tools/control_improvement/ranking.py")


def _logic_binding():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in LOGIC_FILES}


def _logic_receipt_path(plan_path: Path) -> Path:
    return Path(plan_path).with_suffix(".algorithm_source_sha256.json")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _new_design_binding(manifest_path: Path) -> dict[str, str]:
    """Accept only the frozen new-data cohort and its independent design receipt."""
    manifest_path = Path(manifest_path).resolve()
    run_dir = manifest_path.parent.parent
    if manifest_path != run_dir / "new_data" / "global_manifest.json":
        raise ValueError("New development manifest must use new_data/global_manifest.json")
    if not manifest_path.is_relative_to(ROOT.resolve()):
        raise ValueError("New development manifest is outside the workspace")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("manifest_version") != "control_improvement.fixed_new300.v1"
            or manifest.get("control_improvement_cohort") != "new_fixed_development"):
        raise ValueError("Manifest is not the registered new development cohort")
    design = run_dir / "design"
    proposal_path = design / "source_proposal.json"
    snapshot_path = design / "snapshot_plan_v2.json"
    collection_path = design / "branch_collection_v1.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    collection = json.loads(collection_path.read_text(encoding="utf-8"))
    proposal_identity = manifest.get("control_improvement_proposal", {})
    expected_path = (ROOT / proposal_identity.get("path", "")).resolve()
    if (expected_path != proposal_path.resolve()
            or proposal_identity.get("sha256") != _sha(proposal_path)
            or manifest.get("control_improvement_data_design", {}).get("proposal_sha256")
               != proposal.get("proposal_sha256")
            or proposal.get("version") != "control_improvement.fixed_mechanisms.v1"):
        raise ValueError("New manifest proposal/design identity changed")
    if (collection.get("schema") != "reliability_first.collection_design.v1"
            or collection.get("manifest_sha256") != _sha(manifest_path)
            or collection.get("plan_sha256") != _sha(snapshot_path)):
        raise ValueError("New manifest has no matching frozen collection design")
    if not isinstance(snapshot, list) or not snapshot:
        raise ValueError("Frozen new snapshot plan is empty")
    development_indices = [i for i, row in enumerate(snapshot)
                           if row.get("experiment_role") == "development_boundary"]
    if not development_indices or collection.get("development_indices_excluded") != development_indices:
        raise ValueError("Frozen collection design did not exclude all development rows")
    training_indices = collection.get("early_indices", []) + collection.get("remaining_indices", [])
    if (not all(isinstance(i, int) and not isinstance(i, bool) for i in training_indices)
            or len(training_indices) != len(set(training_indices))
            or set(training_indices) != set(range(len(snapshot))) - set(development_indices)):
        raise ValueError("Frozen collection training indices include development or omit pilot rows")
    scenarios = {row["id"]: row for row in manifest["scenarios"]}
    for row in snapshot:
        sid = row["scenario_id"]
        if (sid not in scenarios or scenarios[sid].get("experiment_role") != row.get("experiment_role")
                or manifest["splits"][sid].get("experiment_role") != row.get("experiment_role")
                or row.get("is_final_blind", False)):
            raise ValueError("Frozen new design and manifest role identity differ")
    return {path.relative_to(ROOT.resolve()).as_posix(): _sha(path)
            for path in (proposal_path, snapshot_path, collection_path)}


def _validate_independent_calibrations(model_dirs, manifest_path, design_binding):
    manifest_sha = _sha(Path(manifest_path))
    design_sha = design_binding[next(name for name in design_binding
                                    if name.endswith("/branch_collection_v1.json"))]
    snapshot_sha = design_binding[next(name for name in design_binding
                                      if name.endswith("/snapshot_plan_v2.json"))]
    for model_dir in model_dirs:
        directory = (ROOT / model_dir).resolve()
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        calibration = _registered_calibration(directory, metadata)
        source = calibration["source_binding"]
        if (source.get("stage") != "full"
                or source.get("manifest_sha256") != manifest_sha
                or source.get("collection_design_sha256") != design_sha
                or source.get("snapshot_plan_sha256") != snapshot_sha):
            raise ValueError("Independent gate calibration belongs to a different new design")


def register_development(manifest_path: Path, plan_path: Path, *, nn_dir: str,
                         tree_dir: str, limit: int | None = None,
                         calibration_file: str | None = None) -> dict:
    design_binding = _new_design_binding(manifest_path)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    scenes = [s for s in manifest["scenarios"] if s["experiment_role"] == "development_boundary"]
    ids = [s["id"] for s in scenes]
    if limit is not None:
        if limit < 1:
            raise ValueError("Development smoke limit must be positive")
        ids = ids[:limit]
    if not ids:
        raise ValueError("New manifest has no development scenarios")
    if calibration_file is None and limit != 1:
        raise ValueError("Formal development registration requires independent gate calibration")
    gate = {"error_multiplier": 1.0}
    if calibration_file is not None:
        if calibration_file != "gate_calibration.json":
            raise ValueError("Independent calibration must be same-model gate_calibration.json")
        for model_dir in (nn_dir, tree_dir):
            if not (ROOT / model_dir / calibration_file).is_file():
                raise FileNotFoundError("Independent gate calibration missing: " + model_dir)
        _validate_independent_calibrations((nn_dir, tree_dir), manifest_path, design_binding)
        gate["calibration_file"] = calibration_file
    specs = [
        {"policy_id": "secure_milp", "kind": "baseline"},
        {"policy_id": "secure_current_rule", "kind": "rule"},
        {"policy_id": "nn_ungated", "kind": "ungated", "model_dir": nn_dir},
        {"policy_id": "nn_gated", "kind": "gated", "model_dir": nn_dir,
         "gate": dict(gate)},
        {"policy_id": "tree_gated", "kind": "gated", "model_dir": tree_dir,
         "gate": dict(gate)},
    ]
    mode = ("uncalibrated_exploration_smoke" if calibration_file is None else
            "calibrated_smoke" if limit == 1 else "formal_development")
    plan = register_closed_loop(manifest_path, plan_path, scenario_ids=ids,
                                policy_specs=specs, phase="development",
                                design_binding=design_binding, registration_mode=mode)
    with _logic_receipt_path(plan_path).open("x", encoding="utf-8") as stream:
        json.dump({"plan_signature": plan["signature"], "source_sha256": _logic_binding()},
                  stream, indent=2, ensure_ascii=False)
    return plan


def run_development(plan_path: Path, out_dir: Path, *, max_new_pairs: int | None = None):
    binding_path = _logic_receipt_path(plan_path)
    if not binding_path.is_file():
        raise FileNotFoundError("Registered algorithm source receipt is missing")
    receipt = json.loads(binding_path.read_text(encoding="utf-8"))
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if receipt != {"plan_signature": plan["signature"], "source_sha256": _logic_binding()}:
        raise ValueError("Registered algorithm implementation bytes changed")
    return run_closed_loop(plan_path, out_dir, policy_factory=build_current_policy,
                           max_new_pairs=max_new_pairs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("manifest", type=Path)
    reg.add_argument("plan", type=Path)
    reg.add_argument("--nn-dir", required=True)
    reg.add_argument("--tree-dir", required=True)
    reg.add_argument("--calibration-file")
    reg.add_argument("--limit", type=int)
    run = sub.add_parser("run")
    run.add_argument("plan", type=Path)
    run.add_argument("output_directory", type=Path)
    run.add_argument("--max-new-pairs", type=int)
    args = parser.parse_args()
    result = (register_development(args.manifest, args.plan, nn_dir=args.nn_dir,
                                   tree_dir=args.tree_dir, limit=args.limit,
                                   calibration_file=args.calibration_file)
              if args.command == "register" else
              run_development(args.plan, args.output_directory,
                              max_new_pairs=args.max_new_pairs))
    if args.command == "register":
        print(json.dumps({"plan_signature": result["signature"],
                          "scenarios": len(result["scenario_ids"]),
                          "policies": [s["policy_id"] for s in result["policy_specs"]]},
                         ensure_ascii=False))
    else:
        print(json.dumps({key: result[key] for key in
                          ("registered_pair_count", "attempted_this_call",
                           "terminal_pair_count", "matched_initial_state_passed",
                           "status_counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
