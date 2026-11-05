"""Separately bound train comparison for the v5 observed-mode v2 correction.

The frozen v1 runner cannot register a new policy kind. This wrapper registers
ordinary feedback specifications, then *explicitly* replaces only two named
factory returns during run_batch via a scoped in-process patch. A signed
sidecar SHA-binds this wrapper, mode_v2, the original alternative library and
the unchanged P0 tolerance config. The factory is restored in a finally block.
No simulator, physical criterion, action interface or old artifact is edited.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.policy_search import closed_loop
from tools.policy_search.mode_v2 import (MODE_DETECTOR_ID, ModeV2FeedbackPolicy,
                                         P0_SOLVER_RESIDUAL_TOL_KW)
from tools.policy_search.policy import FeedbackPolicy


SCHEMA = "policy_search.mode_v2_batch.v1"
SOURCE_FILES = (
    "tools/policy_search/mode_v2.py",
    "tools/policy_search/mode_v2_batch.py",
    "tools/policy_search/alternative.py",
    "configs/resilience_assumptions.json",
)
V2_IDS = ("prepare_only_mode_v2", "recovery_only_mode_v2")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _new_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)


def _source_binding():
    return {name: _sha(ROOT / name) for name in SOURCE_FILES}


def _check_tolerance():
    config = _read(ROOT / "configs/resilience_assumptions.json")
    if float(config["p0_solver_feasibility_tol_kW"]) != P0_SOLVER_RESIDUAL_TOL_KW:
        raise ValueError("Mode v2 tolerance differs from original P0 solver tolerance")
    if float(config["p0_unserved_abs_tol_kW"]) != 0.01:
        raise ValueError("Original P0 absolute tolerance differs from registered value")


def _specs_from_v1(old_specs):
    by_id = {spec["policy_id"]: spec for spec in old_specs}
    if len(by_id) != len(old_specs):
        raise ValueError("Duplicate old policy IDs")
    if by_id.get("secure_milp") != {"policy_id": "secure_milp", "kind": "secure"}:
        raise ValueError("Original secure baseline missing")
    old = []
    new = []
    for name, new_id in zip(("prepare_only", "recovery_only"), V2_IDS):
        spec = by_id.get(name)
        if spec is None or spec.get("kind") != "feedback" or spec.get("family") != "mode":
            raise ValueError("Original mode policy missing")
        FeedbackPolicy(spec["theta"], name, family="mode")
        old.append(dict(spec))
        new.append({**spec, "policy_id": new_id})
    return [dict(by_id["secure_milp"]), *old, *new]


def _train_scenes(old_plan, manifest):
    by_id = {scene["id"]: scene for scene in manifest["scenarios"]}
    chosen = []
    for condition in sorted({by_id[sid]["condition_id"] for sid in old_plan["scenario_ids"]}):
        rows = [by_id[sid] for sid in old_plan["scenario_ids"]
                if by_id[sid]["condition_id"] == condition and
                by_id[sid]["pressure_point_index"] == 2]
        if len(rows) != 1 or rows[0]["experiment_role"] != "train" or rows[0]["is_final_blind"]:
            raise ValueError("Expected one unsealed moderate train scene per condition")
        chosen.append(rows[0])
    if len(chosen) != 6 or len({s["root_family_id"] for s in chosen}) != 6:
        raise ValueError("Mode v2 train roots are not six distinct condition roots")
    return chosen


@contextmanager
def scoped_mode_v2_factory(registered_specs):
    """Disclose and scope the only monkeypatch; all other kinds use frozen v1."""
    if set(registered_specs) != set(V2_IDS) and set(registered_specs) != {V2_IDS[0]}:
        raise ValueError("Unexpected mode v2 factory identities")
    for policy_id, spec in registered_specs.items():
        if spec["policy_id"] != policy_id or spec.get("kind") != "feedback" or spec.get("family") != "mode":
            raise ValueError("Mode v2 factory specification mismatch")
        ModeV2FeedbackPolicy(spec["theta"], policy_id)
    original = closed_loop._factory

    def versioned_factory(spec):
        policy_id = spec.get("policy_id")
        if policy_id in registered_specs:
            if spec != registered_specs[policy_id]:
                raise ValueError("Registered mode v2 specification changed")
            return ModeV2FeedbackPolicy(spec["theta"], policy_id)
        return original(spec)

    closed_loop._factory = versioned_factory
    try:
        yield
    finally:
        closed_loop._factory = original


def register(run_dir):
    """Register 6 train scenes x 5 matched policies; does not solve episodes."""
    _check_tolerance()
    run = Path(run_dir).resolve()
    old_path = run / "search/alternative_modes/registered_plan.json"
    old_plan, manifest = closed_loop.verify_batch(old_path)
    if old_plan["phase"] != "train":
        raise ValueError("Mode v2 correction can only use train-root old plan")
    folder = run / "search/mode_v2_train"
    scenes = _train_scenes(old_plan, manifest)
    specs = _specs_from_v1(old_plan["policy_specs"])
    plan_path = folder / "registered_plan.json"
    plan = closed_loop.register_batch(
        run / "new_data/global_manifest.json", plan_path,
        scenario_ids=[s["id"] for s in scenes], policy_specs=specs,
        preference_path=run / "design/RESEARCH_PREFERENCE_v1.json", phase="train")
    sidecar = {
        "schema": SCHEMA,
        "registered_plan_sha256": _sha(plan_path),
        "registered_plan_signature": plan["signature"],
        "old_alternative_plan_sha256": _sha(old_path),
        "old_alternative_plan_signature": old_plan["signature"],
        "source_sha256": _source_binding(),
        "mode_detector_id": MODE_DETECTOR_ID,
        "mode_p0_solver_residual_tol_kW": P0_SOLVER_RESIDUAL_TOL_KW,
        "mode_threshold_source_key": "configs/resilience_assumptions.json:p0_solver_feasibility_tol_kW",
        "physical_P0_classification_unmodified": True,
        "factory_patch": {
            "target": "tools.policy_search.closed_loop._factory",
            "scope": "run_batch call only; try/finally restores original",
            "replacement_policy_ids": list(V2_IDS),
            "other_policy_ids": "frozen v1 factory",
            "not_silent": True,
        },
        "selection_basis": "one pre-registered moderate train scene per each of six conditions; no outcome filter",
        "scenario_ids": plan["scenario_ids"],
        "root_family_ids": [s["root_family_id"] for s in scenes],
        "policy_specs": specs,
        "registered_pair_count": plan["registered_pair_count"],
        "final_blind_opened": False,
    }
    sidecar["signature"] = digest(sidecar)
    _new_json(folder / "MODE_V2_REGISTRATION.json", sidecar)
    return sidecar


def verify_registration(run_dir):
    """Fail closed if any version, physical, preference or plan byte changed."""
    run = Path(run_dir).resolve()
    folder = run / "search/mode_v2_train"
    sidecar = _read(folder / "MODE_V2_REGISTRATION.json")
    if sidecar.get("schema") != SCHEMA or sidecar.get("signature") != digest(
            {k: v for k, v in sidecar.items() if k != "signature"}):
        raise ValueError("Mode v2 sidecar signature changed")
    _check_tolerance()
    if sidecar["source_sha256"] != _source_binding():
        raise ValueError("Mode v2 source/config bytes changed after registration")
    old_path = run / "search/alternative_modes/registered_plan.json"
    if _sha(old_path) != sidecar["old_alternative_plan_sha256"]:
        raise ValueError("Old alternative plan changed")
    old_plan, manifest = closed_loop.verify_batch(old_path)
    if old_plan["signature"] != sidecar["old_alternative_plan_signature"]:
        raise ValueError("Old alternative plan signature changed")
    plan_path = folder / "registered_plan.json"
    if _sha(plan_path) != sidecar["registered_plan_sha256"]:
        raise ValueError("Mode v2 registered plan changed")
    plan, _ = closed_loop.verify_batch(plan_path)
    if (plan["signature"] != sidecar["registered_plan_signature"] or
            plan["scenario_ids"] != sidecar["scenario_ids"] or
            plan["policy_specs"] != sidecar["policy_specs"] or
            plan["registered_pair_count"] != sidecar["registered_pair_count"] or
            plan["scenario_ids"] != [s["id"] for s in _train_scenes(old_plan, manifest)] or
            plan["policy_specs"] != _specs_from_v1(old_plan["policy_specs"])):
        raise ValueError("Mode v2 batch differs from versioned registration")
    return sidecar, plan


def run_registered(run_dir, *, max_new_pairs=None):
    sidecar, plan = verify_registration(run_dir)
    folder = Path(run_dir).resolve() / "search/mode_v2_train"
    versions = {spec["policy_id"]: spec for spec in plan["policy_specs"]
                if spec["policy_id"] in V2_IDS}
    print(json.dumps({"event": "scoped_factory_patch_begin",
                      "target": sidecar["factory_patch"]["target"],
                      "replacement_policy_ids": list(versions),
                      "sidecar_signature": sidecar["signature"]}))
    with scoped_mode_v2_factory(versions):
        summary = closed_loop.run_batch(folder / "registered_plan.json",
                                        folder / "results", max_new_pairs=max_new_pairs)
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
        print(json.dumps({"registered_pair_count": sidecar["registered_pair_count"],
                          "sidecar_signature": sidecar["signature"],
                          "factory_patch": sidecar["factory_patch"]}))
    elif args.operation == "verify":
        sidecar, plan = verify_registration(args.run_dir)
        print(json.dumps({"verified": True, "plan_signature": plan["signature"],
                          "sidecar_signature": sidecar["signature"]}))
    else:
        summary = run_registered(args.run_dir, max_new_pairs=args.max_new_pairs)
        print(json.dumps({"terminal_pair_count": summary["terminal_pair_count"],
                          "registered_pair_count": summary["registered_pair_count"],
                          "status_counts": summary["status_counts"]}))


if __name__ == "__main__":
    main()
