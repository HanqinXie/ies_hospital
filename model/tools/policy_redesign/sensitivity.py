"""Audited v6 development same-root five-pressure sensitivity.

Pressure points vary a registered composite load/fault assumption. They are
neither single-parameter causal interventions nor hospital event frequencies.
This module only reads frozen plans, scores and raw-audit receipts. It never
chooses an online action or reruns the physical model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.policy_redesign.closed_loop import verify_batch
from tools.policy_redesign.select_develop import (_verify_freeze, phase_panel,
                                                  verify_saved_audit)
from tools.policy_search.sensitivity import (_event_schedule,
                                             summarize_pressure_crossing)


POINTS = (0, 1, 2, 3, 4)
ANCHOR = 2
PREREG_SCHEMA = "policy_redesign.v6.development_sensitivity_preregistration.v1"
OUTPUT_SCHEMA = "policy_redesign.v6.audited_crossed_pressure.v1"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def development_design(plan, manifest):
    """Check the registered phase, role, windows and same-root pressure cross."""
    ids = phase_panel(manifest, "development")
    if (plan.get("phase") != "development" or plan.get("scenario_ids") != ids or
            plan.get("score_windows") != {"primary": [60, 420], "full": [0, 600]} or
            plan.get("final_blind_opened") is not False):
        raise ValueError("Development plan/score windows differ from registered 30-scene panel")
    policies = [spec["policy_id"] for spec in plan.get("policy_specs", [])]
    if (not policies or len(policies) != len(set(policies)) or
            "secure_milp" not in policies or
            plan.get("registered_pair_count") != 30 * len(policies)):
        raise ValueError("Development registered policy denominator differs")
    lookup = {scene["id"]: scene for scene in manifest["scenarios"]}
    roots = {}
    for sid in ids:
        scene = lookup[sid]
        root = scene["root_family_id"]
        points = roots.setdefault(root, {})
        index = scene["pressure_point_index"]
        if index in points:
            raise ValueError("Duplicate development root pressure")
        points[index] = scene
    rows = []
    for root, scenes in roots.items():
        if set(scenes) != set(POINTS):
            raise ValueError("Incomplete development five-pressure root")
        reference = scenes[ANCHOR]
        identity = ("condition_id", "time_block_id", "base_state_id",
                    "exogenous_stream_id", "initialization_seed")
        for key in identity:
            if reference.get(key) is None or any(scene.get(key) != reference[key]
                                                 for scene in scenes.values()):
                raise ValueError("Same-root development identity differs: " + key)
        schedule = _event_schedule(reference)
        if any(_event_schedule(scene) != schedule for scene in scenes.values()):
            raise ValueError("Same-root development exogenous event schedule differs")
        rows.append({"condition_id": reference["condition_id"],
                     "root_family_id": root,
                     "scenario_ids_by_pressure": {str(point): scenes[point]["id"]
                                                  for point in POINTS}})
    rows.sort(key=lambda row: (row["condition_id"], row["root_family_id"]))
    return {"pressure_point_indices": list(POINTS),
            "anchor_pressure_point_index": ANCHOR,
            "pressure_points_are_composite_assumptions": True,
            "not_hospital_event_probabilities": True,
            "registered_scene_count": len(ids),
            "registered_pair_count": 30 * len(policies),
            "policy_ids": policies, "roots": rows}


def summarize_windows(plan, manifest, summary):
    """Pure dual-window summary; caller must first verify all saved raw bytes."""
    development_design(plan, manifest)
    return {window: summarize_pressure_crossing(
                plan, manifest, summary, window=window,
                required_points=POINTS, anchor_point=ANCHOR)
            for window in ("primary_score", "full_observation")}


def _contract(run_dir):
    run = Path(run_dir).resolve()
    folder = run / "development"
    plan_path = folder / "registered_plan.json"
    freeze_path = run / "selection/FROZEN_SELECTION.json"
    plan, manifest = verify_batch(plan_path)
    frozen, _, _ = _verify_freeze(freeze_path)
    if (plan.get("policy_specs") != frozen.get("policy_specs") or
            plan.get("model_bindings") != frozen.get("model_bindings") or
            plan.get("source_sha256") != frozen.get("source_sha256") or
            plan.get("manifest_sha256") != frozen.get("manifest_sha256") or
            plan.get("preference_sha256") != frozen.get("preference_sha256") or
            frozen.get("final_blind_opened") is not False):
        raise ValueError("Development plan differs from frozen selection design")
    design = development_design(plan, manifest)
    manifest_path = Path(plan["manifest_path"])
    if _sha(manifest_path) != plan["manifest_sha256"]:
        raise ValueError("Development manifest bytes differ")
    return plan, manifest, {"development_plan_sha256": _sha(plan_path),
                            "development_plan_signature": plan["signature"],
                            "manifest_sha256": _sha(manifest_path),
                            "selection_freeze_sha256": _sha(freeze_path),
                            "sensitivity_source_sha256": _sha(__file__),
                            "pressure_summary_source_sha256": _sha(
                                Path(summarize_pressure_crossing.__code__.co_filename)),
                            "design": design}


def _existing_results(folder):
    results = folder / "results"
    return results.exists() and any(results.rglob("*"))


def preregister_sensitivity(run_dir):
    """Freeze the five-pressure design after plan registration, before attempt 1."""
    run = Path(run_dir).resolve()
    folder = run / "development"
    output = folder / "SENSITIVITY_PREREGISTRATION.json"
    if output.exists():
        raise FileExistsError("V6 sensitivity preregistration is immutable")
    _, _, fields = _contract(run)
    if _existing_results(folder):
        raise ValueError("Sensitivity must be registered before any development result or attempt")
    body = {"schema": PREREG_SCHEMA, "registered_unix": time.time(),
            "registered_before_any_development_attempt": True,
            "final_12_backgrounds_unopened": True, **fields}
    _write_new(output, {**body, "signature": digest(body)})
    if _existing_results(folder):
        raise ValueError("Development result appeared during sensitivity preregistration")
    return output


def verify_preregistration(run_dir):
    """Recheck receipt bytes, plan, manifest, source and attempt chronology."""
    run = Path(run_dir).resolve()
    folder = run / "development"
    output = folder / "SENSITIVITY_PREREGISTRATION.json"
    receipt = _read(output)
    _, _, fields = _contract(run)
    registered = receipt.get("registered_unix")
    body = {"schema": PREREG_SCHEMA, "registered_unix": registered,
            "registered_before_any_development_attempt": True,
            "final_12_backgrounds_unopened": True, **fields}
    if (type(registered) not in (int, float) or
            receipt != {**body, "signature": digest(body)}):
        raise ValueError("V6 sensitivity preregistration plan/source/design differs")
    journal = folder / "results/attempts.jsonl"
    if journal.exists():
        started = 0
        for lineno, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Malformed development journal line {lineno}") from error
            if event.get("event") == "started":
                started += 1
                if (type(event.get("time_unix")) not in (int, float) or
                        event["time_unix"] < registered):
                    raise ValueError("Development started before sensitivity preregistration")
        if not started and _existing_results(folder):
            raise ValueError("Development results have no registered start event")
    elif _existing_results(folder):
        raise ValueError("Development results have no attempt journal")
    return output


def analyze_audited_batch(run_dir, audit_path=None):
    """Summarize both windows only after the full original-raw v6 audit passes."""
    run = Path(run_dir).resolve()
    prereg_path = verify_preregistration(run)
    folder = run / "development"
    plan_path = folder / "registered_plan.json"
    audit_path = Path(audit_path or folder / "AUDIT_V6.json").resolve()
    if audit_path.parent != folder:
        raise ValueError("Development sensitivity audit must be in development directory")
    verify_saved_audit(plan_path, audit_path, "development")
    audit = _read(audit_path)
    if audit.get("passed") is not True or audit.get("errors") not in (None, []):
        raise ValueError("Development raw audit did not pass")
    summary_path = Path(audit["summary_path"])
    if not summary_path.is_file() or _sha(summary_path) != audit["summary_sha256"]:
        raise ValueError("Development audited summary bytes differ")
    plan, manifest, _ = _contract(run)
    summary = _read(summary_path)
    windows = summarize_windows(plan, manifest, summary)
    output = {"schema": OUTPUT_SCHEMA,
              "development_plan_sha256": _sha(plan_path),
              "development_plan_signature": plan["signature"],
              "manifest_sha256": _sha(plan["manifest_path"]),
              "summary_sha256": _sha(summary_path),
              "raw_audit_sha256": _sha(audit_path),
              "raw_audit_passed": True,
              "sensitivity_preregistration_sha256": _sha(prereg_path),
              "sensitivity_source_sha256": _sha(__file__),
              "registered_pair_count": audit["registered_pair_count"],
              "unknown_primary_pair_ids": audit["unknown_primary_pair_ids"],
              "unknown_full_pair_ids": audit["unknown_full_pair_ids"],
              "final_12_backgrounds_unopened": True,
              "windows": windows}
    path = folder / "SENSITIVITY_BY_CONDITION_PRESSURE.json"
    _write_new(path, output)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preregister", "analyze"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--audit", type=Path,
                        help="Passing v6 development raw audit; analyze only")
    args = parser.parse_args()
    if args.command == "preregister":
        if args.audit is not None:
            parser.error("--audit applies only to analyze")
        print(preregister_sensitivity(args.run_dir))
    else:
        print(analyze_audited_batch(args.run_dir, args.audit))


if __name__ == "__main__":
    main()
