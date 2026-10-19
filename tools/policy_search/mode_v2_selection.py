"""Independently register mode-v2 policies on the six frozen selection roots.

This module reads only preregistered plans, the outcome-free manifest and
source/parameter bindings. It does not inspect selection results for design.
For execution it reuses mode_v2_batch's explicitly scoped factory patch;
neither the frozen runner nor previously registered policies are edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.policy_search import closed_loop
from tools.policy_search.mode_v2 import MODE_DETECTOR_ID, P0_SOLVER_RESIDUAL_TOL_KW
from tools.policy_search.mode_v2_batch import (V2_IDS, _check_tolerance,
                                                _specs_from_v1,
                                                scoped_mode_v2_factory,
                                                verify_registration as verify_train)


SCHEMA = "policy_search.mode_v2_selection.v1"
FOLDER = "search/mode_v2_selection"
SOURCE_FILES = (
    "tools/policy_search/mode_v2_selection.py",
    "tools/policy_search/mode_v2.py",
    "tools/policy_search/mode_v2_batch.py",
    "tools/policy_search/alternative.py",
    "configs/resilience_assumptions.json",
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def source_binding():
    return {name: _sha(ROOT / name) for name in SOURCE_FILES}


def build_selection_design(run_dir):
    """Use the already frozen six selection scenarios without outcome data."""
    _check_tolerance()
    run = Path(run_dir).resolve()
    train_sidecar, train_plan = verify_train(run)
    main_path = run / "selection/registered_plan.json"
    main, manifest = closed_loop.verify_batch(main_path)
    old_path = run / "search/alternative_modes/registered_plan.json"
    old, old_manifest = closed_loop.verify_batch(old_path)
    if (main["phase"] != "selection" or len(main["scenario_ids"]) != 6 or
            main["manifest_sha256"] != train_plan["manifest_sha256"] or
            old["manifest_sha256"] != main["manifest_sha256"] or
            main["preference_sha256"] != train_plan["preference_sha256"] or
            old_manifest["signature"] != manifest["signature"]):
        raise ValueError("Main selection, train and alternative design binding differs")
    by_id = {s["id"]: s for s in manifest["scenarios"]}
    scenes = [by_id[sid] for sid in main["scenario_ids"]]
    roots = [s["root_family_id"] for s in scenes]
    if (len(set(roots)) != 6 or
            len({s["condition_id"] for s in scenes}) != 6 or
            any(s["experiment_role"] != "selection" or s["is_final_blind"] for s in scenes) or
            set(roots) & set(train_sidecar["root_family_ids"])):
        raise ValueError("Selection root role, condition coverage or disjointness differs")
    return {
        "phase": "selection",
        "scenario_ids": list(main["scenario_ids"]),
        "main_selection_scenario_ids": list(main["scenario_ids"]),
        "root_family_ids": roots,
        "train_mode_v2_root_family_ids": list(train_sidecar["root_family_ids"]),
        "policy_specs": _specs_from_v1(old["policy_specs"]),
        "main_selection_plan_sha256": _sha(main_path),
        "main_selection_plan_signature": main["signature"],
        "old_alternative_plan_sha256": _sha(old_path),
        "old_alternative_plan_signature": old["signature"],
        "train_mode_v2_registration_sha256": _sha(run / "search/mode_v2_train/MODE_V2_REGISTRATION.json"),
        "train_mode_v2_registration_signature": train_sidecar["signature"],
        "selection_outcomes_read": False,
        "final_blind_opened": False,
    }


def register(run_dir):
    run = Path(run_dir).resolve()
    if (run / "selection/ANALYSIS_PREFERENCE_V2.json").exists():
        raise ValueError("Register corrected mode policies before main selection outcome analysis")
    design = build_selection_design(run)
    folder = run / FOLDER
    plan_path = folder / "registered_plan.json"
    plan = closed_loop.register_batch(
        run / "new_data/global_manifest.json", plan_path,
        scenario_ids=design["scenario_ids"], policy_specs=design["policy_specs"],
        preference_path=run / "design/RESEARCH_PREFERENCE_v1.json",
        phase="selection")
    if plan["registered_pair_count"] != 30:
        raise ValueError("Expected exact 30-pair corrected-mode selection batch")
    sidecar = {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "registered_before_main_selection_analysis": True,
        "main_selection_analysis_absent_at_registration": True,
        "source_sha256": source_binding(),
        "registered_plan_sha256": _sha(plan_path),
        "registered_plan_signature": plan["signature"],
        "mode_detector_id": MODE_DETECTOR_ID,
        "mode_p0_solver_residual_tol_kW": P0_SOLVER_RESIDUAL_TOL_KW,
        "physical_P0_classification_unmodified": True,
        "factory_patch": {
            "target": "tools.policy_search.closed_loop._factory",
            "implementation_source": "tools/policy_search/mode_v2_batch.py:scoped_mode_v2_factory",
            "scope": "run_batch call only; try/finally restores original",
            "replacement_policy_ids": list(V2_IDS),
            "other_policy_ids": "frozen v1 factory",
            "not_silent": True,
        },
        "registered_pair_count": plan["registered_pair_count"],
        **design,
    }
    sidecar["signature"] = digest(sidecar)
    path = folder / "MODE_V2_SELECTION_REGISTRATION.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(sidecar, stream, indent=2, ensure_ascii=False, allow_nan=False)
    return sidecar


def verify_registration(run_dir):
    run = Path(run_dir).resolve()
    folder = run / FOLDER
    sidecar = _read(folder / "MODE_V2_SELECTION_REGISTRATION.json")
    if (sidecar.get("schema") != SCHEMA or
            sidecar.get("signature") != digest({k: v for k, v in sidecar.items() if k != "signature"}) or
            sidecar.get("source_sha256") != source_binding()):
        raise ValueError("Corrected-mode selection sidecar/source changed")
    design = build_selection_design(run)
    for key, expected in design.items():
        if sidecar.get(key) != expected:
            raise ValueError("Corrected-mode selection design changed: " + key)
    plan_path = folder / "registered_plan.json"
    if _sha(plan_path) != sidecar["registered_plan_sha256"]:
        raise ValueError("Corrected-mode selection plan bytes changed")
    plan, _ = closed_loop.verify_batch(plan_path)
    if (plan["signature"] != sidecar["registered_plan_signature"] or
            plan["phase"] != "selection" or
            plan["scenario_ids"] != design["scenario_ids"] or
            plan["policy_specs"] != design["policy_specs"] or
            plan["registered_pair_count"] != 30):
        raise ValueError("Corrected-mode selection plan differs from sidecar")
    return sidecar, plan


def run_registered(run_dir, *, max_new_pairs=None):
    sidecar, plan = verify_registration(run_dir)
    folder = Path(run_dir).resolve() / FOLDER
    versions = {s["policy_id"]: s for s in plan["policy_specs"] if s["policy_id"] in V2_IDS}
    print(json.dumps({"event": "scoped_factory_patch_begin",
                      "policy_ids": list(versions), "sidecar_signature": sidecar["signature"]}))
    with scoped_mode_v2_factory(versions):
        summary = closed_loop.run_batch(folder / "registered_plan.json", folder / "results",
                                        max_new_pairs=max_new_pairs)
    print(json.dumps({"event": "scoped_factory_patch_restored",
                      "sidecar_signature": sidecar["signature"]}))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("register", "verify", "run"))
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--max-new-pairs", type=int)
    args = parser.parse_args()
    if args.operation == "register":
        sidecar = register(args.run_dir)
        value = {"registered_pair_count": sidecar["registered_pair_count"],
                 "sidecar_signature": sidecar["signature"]}
    elif args.operation == "verify":
        sidecar, plan = verify_registration(args.run_dir)
        value = {"verified": True, "registered_pair_count": plan["registered_pair_count"],
                 "sidecar_signature": sidecar["signature"],
                 "plan_signature": plan["signature"]}
    else:
        summary = run_registered(args.run_dir, max_new_pairs=args.max_new_pairs)
        value = {"terminal_pair_count": summary["terminal_pair_count"],
                 "registered_pair_count": summary["registered_pair_count"],
                 "status_counts": summary["status_counts"]}
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
