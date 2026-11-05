"""Re-register selected frozen package scenes into a fresh physical run.

Old plans are provenance containing original absolute paths. This tool never
runs those paths or resamples from parent designs. Real replay requires the
full dependencies and a genuine Gurobi installation/license.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tools.four_scenarios.package import _archive_name, _read, _sha, _within, verify_integrity

ROUTES = {
    "state_dispatch.matched_full_feedback.v1": "tools.state_dispatch.closed_loop",
    "state_dispatch.matched_full_feedback.v2": "tools.state_dispatch.closed_loop_v2",
}
PHYSICS_FILES = frozenset({
    "src/hospital_ies/model.py", "src/hospital_ies/resilience.py",
    "src/hospital_ies/control_env.py", "src/hospital_ies/state_transition.py",
    "src/hospital_ies/execution.py", "src/hospital_ies/thermal_inertia.py",
    "src/hospital_ies/idf_parameters.py", "configs/plant_configs.json",
    "configs/resilience_assumptions.json", "src/hospital_ies/solver_backend.py",
    "src/hospital_ies/solver_diagnostics.py",
})


def _write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _backend(name, root):
    backend = importlib.import_module(name)
    if backend.ROOT.resolve() != root:
        raise ValueError("Physical runner must be imported from the extracted package source")
    return backend


def _preference(package, batch, plan, root):
    expected = plan.get("preference_sha256")
    declared = batch.get("preference_file")
    candidates = ([declared] if declared else [r["archive_path"] for r in package["entries"]
                                               if r["sha256"] == expected])
    if not expected or not candidates:
        raise FileNotFoundError("Frozen preference bytes are not present in this package")
    path = _within(root / _archive_name(sorted(candidates)[0]), root)
    if _sha(path) != expected:
        raise ValueError("Packaged frozen preference SHA differs")
    return path


def replay_request(extracted_root, batch_name, *, scenario_ids=None):
    """Resolve only included selected scenes; old absolute paths remain evidence."""
    root = Path(extracted_root).resolve()
    package = verify_integrity(root)
    if package.get("kind") != "v9_state_dispatch_selected_raw":
        raise ValueError("Not a state-dispatch selected-raw package")
    record = next((b for b in package["batches"] if b["name"] == batch_name), None)
    if record is None:
        raise ValueError("Unknown packaged batch")
    paths = {key: _within(root / _archive_name(item["path"]), root)
             for key, item in record["evidence"].items()}
    for key, path in paths.items():
        if _sha(path) != record["evidence"][key]["sha256"]:
            raise ValueError("Frozen batch evidence SHA differs")
    manifest, plan = _read(paths["manifest"]), _read(paths["plan"])
    if plan.get("manifest_sha256") != _sha(paths["manifest"]):
        raise ValueError("Frozen plan/manifest binding differs")
    route = ROUTES.get(plan.get("schema"))
    if route is None:
        raise ValueError("No supported frozen runner route for this plan schema")
    selected = sorted({item["scenario_id"] for item in package["examples"] if item["batch"] == batch_name})
    ids = list(scenario_ids) if scenario_ids is not None else selected[:1]
    if not ids or len(ids) > 4 or len(ids) != len(set(ids)) or not set(ids) <= set(selected):
        raise ValueError("Choose one to four distinct selected scene IDs from the package")
    by_id = {scene["id"]: scene for scene in manifest["scenarios"]}
    scenes = [copy.deepcopy(by_id[sid]) for sid in ids]
    if any(scene.get("is_final_blind") is not False or
           scene.get("experiment_role") != plan["phase"] for scene in scenes):
        raise ValueError("Selected frozen scene is sealed or phase-mismatched")
    policies = copy.deepcopy(plan["policy_specs"])
    if len(scenes) * len(policies) > 32:
        raise ValueError("Small portable replay is limited to 32 registered pairs")
    # Current v9 policies carry finite config directly. Historical learned
    # weights are not silently copied or replaced by another controller.
    if any(spec.get("model_dir") for spec in policies):
        raise ValueError("This selected policy needs external learned weights; portable replay is unavailable")
    preference = _preference(package, record, plan, root)
    excluded = {item["path"]: item for item in package.get("external_registered_inputs", [])}
    for item in manifest["files"]:
        name = _archive_name(item["path"])
        if name in excluded:
            if not name.startswith("inputs/literature/") or excluded[name]["sha256"] != item["sha256"]:
                raise ValueError("Only registered non-runtime literature may remain external")
            continue
        path = _within(root / name, root)
        if not path.is_file() or path.stat().st_size != item["bytes"] or _sha(path) != item["sha256"]:
            raise ValueError("Frozen physical/runtime input SHA differs: " + name)
    for name, expected in plan.get("source_sha256", {}).items():
        path = _within(root / _archive_name(name), root)
        if not path.is_file() or _sha(path) != expected:
            raise ValueError("Frozen policy/runner source SHA differs: " + name)
    return dict(root=str(root), batch=batch_name, scenarios=scenes, scenario_ids=ids,
        policy_specs=policies, phase=plan["phase"], runner_module=route,
        preference_path=str(preference), preference_sha256=_sha(preference),
        source_manifest=manifest, source_manifest_sha256=_sha(paths["manifest"]),
        source_plan_sha256=_sha(paths["plan"]), original_absolute_plan_is_provenance_only=True)


def prepare_replay(extracted_root, output_dir, batch_name, *, scenario_ids=None):
    """Build a current-root runtime manifest and genuinely new registered plan."""
    root, output = Path(extracted_root).resolve(), Path(output_dir).resolve()
    if output.is_relative_to(root):
        raise ValueError("Physical replay output must be outside the immutable extracted package")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError("Portable replay requires a new or empty output directory")
    request = replay_request(root, batch_name, scenario_ids=scenario_ids)
    backend = _backend(request["runner_module"], root)
    from hospital_ies.dataset_manifest import build_manifest, digest
    old = request["source_manifest"]
    scenes = request["scenarios"]
    splits = {scene["id"]: copy.deepcopy(old["splits"][scene["id"]]) for scene in scenes}
    manifest = build_manifest(root, scenes, [s["policy_id"] for s in request["policy_specs"]],
        copy.deepcopy(old["execution_contract"]), copy.deepcopy(old["schemas"]),
        copy.deepcopy(old["options"]), splits)
    if manifest["simulator_hash"] != old["simulator_hash"]:
        raise ValueError("Current package physics differs from frozen source manifest")
    if old.get("execution_contract_id") and manifest["execution_contract_id"] != old["execution_contract_id"]:
        raise ValueError("Current package execution contract differs from frozen source")
    manifest.pop("signature")
    manifest.update(manifest_version="state_dispatch.portable_replay.manifest.v1",
        policy_search_final_12_backgrounds_unopened=True,
        portable_origin_manifest_sha256=request["source_manifest_sha256"],
        portable_origin_plan_sha256=request["source_plan_sha256"],
        portable_replay_semantics="same frozen selected scenes and physical inputs; fresh local runtime and path registration")
    manifest["signature"] = digest(manifest)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path, plan_path = output / "global_manifest.json", output / "registered_plan.json"
    _write_new(manifest_path, manifest)
    plan = backend.register_batch(manifest_path, plan_path,
        scenario_ids=request["scenario_ids"], policy_specs=request["policy_specs"],
        preference_path=Path(request["preference_path"]), phase=request["phase"])
    receipt = dict(root=str(root), batch=batch_name, runner_module=request["runner_module"],
        manifest_path=str(manifest_path), plan_path=str(plan_path), results_path=str(output / "results"),
        scenario_ids=request["scenario_ids"], policy_specs=request["policy_specs"], phase=request["phase"],
        preference_path=request["preference_path"], preference_sha256=request["preference_sha256"],
        origin_manifest_sha256=request["source_manifest_sha256"], origin_plan_sha256=request["source_plan_sha256"],
        registered_pair_count=plan["registered_pair_count"], plan_signature=plan["signature"],
        manifest_sha256=_sha(manifest_path), plan_sha256=_sha(plan_path),
        episodes_started=0, resampled_scenarios=False, parent_design_files_are_runtime_dependencies=False,
        original_absolute_plan_is_provenance_only=True,
        runtime_rebound_to_current_installation=True, frozen_physics_hash=manifest["simulator_hash"])
    _write_new(output / "REPLAY_REGISTRATION.json", receipt)
    return receipt


def run_replay(extracted_root, output_dir, batch_name, *, scenario_ids=None):
    receipt = prepare_replay(extracted_root, output_dir, batch_name, scenario_ids=scenario_ids)
    backend = _backend(receipt["runner_module"], Path(receipt["root"]))
    summary = backend.run_batch(receipt["plan_path"], receipt["results_path"])
    output = Path(output_dir).resolve()
    audit = backend.audit_batch(receipt["plan_path"], receipt["results_path"],
        output_json=output / "AUDIT.json", output_markdown=output / "AUDIT.md")
    registered = summary.get("registered_pair_count", 0)
    pairs = audit.get("pair_ledger", [])
    complete_pairs = sum(
        pair.get("primary_full_horizon") is True
        and pair.get("full_observation_horizon") is True
        and pair.get("recomputed_full_observation", {}).get("full_horizon") is True
        and pair.get("recomputed_full_observation", {}).get("observed_minutes") == 600.0
        for pair in pairs)
    full_rerun = (audit["passed"] is True and registered > 0
        and summary.get("terminal_pair_count") == registered
        and summary.get("unknown_primary_pair_count") == 0
        and audit.get("recomputed_pair_count") == registered
        and len(pairs) == registered and complete_pairs == registered)
    result = dict(registration=receipt, summary_counts={key: summary.get(key) for key in
        ("registered_pair_count", "terminal_pair_count", "unknown_primary_pair_count", "attempted_this_call")},
        audit_passed=audit["passed"], audit_error_count=len(audit.get("errors", [])),
        physical_simulation_attempted=True,
        complete_600min_pair_count=complete_pairs,
        full_physical_simulator_rerun=full_rerun,
        original_saved_results_unchanged=True)
    _write_new(output / "PHYSICAL_REPLAY_RESULT.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("list", "prepare", "run"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--batch")
    parser.add_argument("--scene", action="append", dest="scenes")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if ROOT.resolve() != root:
        raise ValueError("Run physical replay using the extracted package source as the current root")
    if args.operation == "list":
        package = verify_integrity(root)
        value = dict(selected_scenes={name: sorted({item["scenario_id"] for item in package["examples"]
            if item["batch"] == name}) for name in sorted({item["batch"] for item in package["examples"]})})
    else:
        if not args.batch or args.output is None:
            parser.error("prepare/run require --batch and a new external --output directory")
        try:
            value = (prepare_replay if args.operation == "prepare" else run_replay)(
                root, args.output, args.batch, scenario_ids=args.scenes)
        except Exception as error:
            output = args.output.resolve()
            if output.is_dir() and not output.is_relative_to(root) and not (output / "REPLAY_ERROR.json").exists():
                _write_new(output / "REPLAY_ERROR.json", dict(error_type=type(error).__name__,
                    message=str(error), traceback=traceback.format_exc(),
                    original_saved_results_unchanged=True, completion_not_claimed=True))
            raise
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if args.operation == "run" and not value["full_physical_simulator_rerun"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
