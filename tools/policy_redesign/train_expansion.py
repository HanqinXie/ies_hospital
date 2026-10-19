"""Register a broad old-train-root panel after adaptive search, before fit."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tools.policy_redesign.closed_loop import register_batch, run_batch
from tools.policy_redesign.search import _checked, SCHEMA as SEARCH_SCHEMA
from tools.control_improvement.ranking import TARGET_NAMES
from hospital_ies.dataset_manifest import digest

RUN = ROOT / "outputs/v6_policy_redesign_20261016_run01"
MANIFEST = ROOT / "outputs/v5_policy_search_20261015_run01/new_data/global_manifest.json"
PREFERENCE = RUN / "design/RESEARCH_PREFERENCE_v1.json"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def choose_train_panel(manifest, *, pressure_indices=(0, 1, 2, 3, 4)):
    """Use all five pressure scenes on each of the 30 registered v5 train roots."""
    pressure = set(pressure_indices)
    if tuple(sorted(pressure_indices)) != tuple(range(5)):
        raise ValueError("Training expansion requires all five pressure points")
    scenes = sorted((scene for scene in manifest["scenarios"]
                     if scene["experiment_role"] == "train"
                     and scene["pressure_point_index"] in pressure),
                    key=lambda scene: (scene["condition_id"], scene["time_block_id"],
                                       scene["pressure_point_index"]))
    roots = {}
    conditions = {}
    for scene in scenes:
        roots.setdefault(scene["root_family_id"], []).append(scene)
        conditions.setdefault(scene["condition_id"], set()).add(scene["root_family_id"])
    if (len(scenes) != 150 or len({scene["id"] for scene in scenes}) != 150 or
            len(roots) != 30 or len(conditions) != 6 or
            any(len(root_ids) != 5 for root_ids in conditions.values()) or
            any(scene.get("is_final_blind") or
                manifest["splits"][scene["id"]].get("experiment_role") != "train"
                for scene in scenes)):
        raise ValueError("Training expansion leaves registered full root groups")
    if any(len(rows) != 5 or
           {scene["pressure_point_index"] for scene in rows} != pressure or
           len({scene["condition_id"] for scene in rows}) != 1
           for rows in roots.values()):
        raise ValueError("Each train root needs one complete five-pressure panel")
    return scenes


def _checked_generation_audit(directory, evaluation, search_plan):
    """Require saved 19-target raw recheck and current plan/summary/raw bytes."""
    directory = Path(directory).resolve()
    audit_path = directory / "AUDIT.json"
    if not audit_path.is_file():
        raise ValueError(f"Search raw AUDIT.json missing: {directory}")
    audit = _read(audit_path)
    plan_path = directory / "registered_plan.json"
    summary_copy = directory / "summary_for_search.json"
    registered = _read(plan_path)
    summary_path = Path(audit.get("summary_path", "")).resolve()
    if (registered.get("schema") != "policy_redesign.matched_full_feedback.v1" or
            registered.get("signature") != digest({
                k: v for k, v in registered.items() if k != "signature"}) or
            registered.get("phase") != "train" or
            registered.get("scenario_ids") != search_plan["scenario_ids"] or
            len(registered.get("policy_specs", [])) != 11 or
            registered.get("registered_pair_count") != 44 or
            evaluation.get("registered_pair_count") != 44 or
            evaluation.get("terminal_pair_count") != 44 or
            evaluation.get("matched_initial_state_and_stream") is not True):
        raise ValueError(f"Search raw audit plan/evaluation denominator differs: {directory}")
    expected = {(sid, spec["policy_id"])
                for sid in registered["scenario_ids"]
                for spec in registered["policy_specs"]}
    if len(expected) != 44:
        raise ValueError(f"Search raw audit registered pair identity differs: {directory}")
    if (audit.get("schema") != "policy_redesign.v6.matched_batch_audit.v1" or
            audit.get("passed") is not True or audit.get("errors") != [] or
            audit.get("phase") != "train" or
            audit.get("unknown_primary_pair_ids") != [] or
            audit.get("unknown_full_pair_ids") != [] or
            audit.get("matched_initial_state_and_stream") is not True or
            audit.get("target_names") != list(TARGET_NAMES) or
            audit.get("scoring_source_sha256") !=
            registered.get("source_sha256", {}).get(
                "tools/control_improvement/branches.py") or
            audit.get("scoring_source_sha256") != _sha(
                ROOT / "tools/control_improvement/branches.py") or
            audit.get("registered_pair_count") != 44 or
            audit.get("recorded_pair_count") != 44 or
            audit.get("terminal_pair_count") != 44 or
            audit.get("recomputed_pair_count") != 44 or
            Path(audit.get("plan_path", "")).resolve() != plan_path or
            audit.get("plan_sha256") != _sha(plan_path) or
            audit.get("plan_signature") != registered["signature"] or
            audit.get("summary_sha256") != _sha(summary_copy) or
            not summary_path.is_file() or
            not summary_path.is_relative_to((directory / "results").resolve()) or
            not summary_path.name.startswith("summary_") or
            _sha(summary_path) != audit["summary_sha256"]):
        raise ValueError(f"Search raw audit status or plan/summary SHA differs: {directory}")
    summary = _read(summary_path)
    if (summary.get("plan_signature") != registered["signature"] or
            summary.get("registered_pair_count") != 44 or
            summary.get("terminal_pair_count") != 44 or
            summary.get("matched_initial_state_and_stream") is not True):
        raise ValueError(f"Search raw audit runner summary differs: {directory}")
    rows = audit.get("pair_ledger")
    if not isinstance(rows, list) or len(rows) != 44:
        raise ValueError(f"Search raw audit pair ledger denominator differs: {directory}")
    seen = set()
    for row in rows:
        key = (row.get("scenario_id"), row.get("policy_id"))
        if key not in expected or key in seen:
            raise ValueError(f"Search raw audit pair identity differs: {directory}")
        seen.add(key)
        if (row.get("terminal") is not True or row.get("score_recomputed") is not True or
                row.get("binding_passed") is not True or
                row.get("primary_full_horizon") is not True or
                row.get("full_observation_horizon") is not True or
                (row.get("recomputed_binding") or {}).get("passed") is not True):
            raise ValueError(f"Search raw audit pair not fully rechecked: {key}")
        for field in ("recomputed_primary_score", "recomputed_full_observation"):
            score = row.get(field) or {}
            targets = score.get("targets")
            if (score.get("full_horizon") is not True or
                    score.get("target_names") != list(TARGET_NAMES) or
                    score.get("target_mask") != [True] * 19 or
                    not isinstance(targets, list) or len(targets) != 19 or
                    any(type(value) not in (int, float) or not math.isfinite(value)
                        for value in targets)):
                raise ValueError(f"Search raw audit 19-target {field} incomplete: {key}")
        attempts = row.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ValueError(f"Search raw audit attempt ledger absent: {key}")
        latest = attempts[-1]
        if (not latest.get("raw_path") or not latest.get("result_path") or
                row.get("latest_result_sha256") != latest.get("result_sha256")):
            raise ValueError(f"Search raw audit latest result/raw absent: {key}")
        for attempt in attempts:
            for kind in ("raw", "result"):
                path_value = attempt.get(f"{kind}_path")
                declared_sha = attempt.get(f"{kind}_sha256")
                if path_value is None:
                    continue
                path = Path(path_value).resolve()
                if (not path.is_relative_to(directory) or not path.is_file() or
                        not declared_sha or _sha(path) != declared_sha):
                    raise ValueError(f"Search raw audit {kind} SHA differs: {key}")
            if (attempt.get("declared_raw_sha256") is not None and
                    attempt["declared_raw_sha256"] != attempt.get("raw_sha256")):
                raise ValueError(f"Search raw audit declared raw SHA differs: {key}")
    if seen != expected:
        raise ValueError(f"Search raw audit pair ledger incomplete: {directory}")
    return audit_path, _sha(audit_path)


def select_search_specs(search_dir, *, per_family=1):
    search = Path(search_dir)
    plan = _checked(search / "search_plan.json", SEARCH_SCHEMA + ".plan")
    expected_evaluations = []
    for seed in plan["seeds"]:
        stop = search / f"seed_{seed:04d}" / "stop.json"
        if not stop.is_file():
            raise ValueError("Adaptive search must finish its registered seeds before expansion")
        stopped = _checked(stop, SEARCH_SCHEMA + ".seed_stop")
        if stopped["seed"] != seed or stopped["completed_generations"] < plan["min_generations"]:
            raise ValueError("Seed stop receipt and minimum generations differ")
        for generation in range(1, stopped["completed_generations"] + 1):
            expected_evaluations.append(search / f"seed_{seed:04d}" /
                                        f"generation_{generation:04d}" / "evaluation.json")
    evaluations = sorted(search.glob("seed_*/generation_*/evaluation.json"))
    if set(evaluations) != set(expected_evaluations):
        raise ValueError("Search generation evaluation set differs from signed stop receipts")
    registered_generations = {path.parent for path in search.glob(
        "seed_*/generation_*/registered_plan.json")}
    if registered_generations != {path.parent for path in expected_evaluations}:
        raise ValueError("Search registered generation set differs from signed stop receipts")
    if len(evaluations) < len(plan["seeds"]) * plan["min_generations"]:
        raise ValueError("Fewer completed generations than registered minimum")
    per = {}
    for path in evaluations:
        value = _checked(path, SEARCH_SCHEMA + ".evaluation")
        if (path.parent.name != f"generation_{value['generation']:04d}"
                or path.parent.parent.name != f"seed_{value['seed']:04d}"
                or value["seed"] not in plan["seeds"]):
            raise ValueError("Evaluation identity differs from registered search directory")
        if (_sha(path.parent / "summary_for_search.json") != value["runner_summary_sha256"]
                or _sha(path.parent / "registered_plan.json") != value["runner_plan_sha256"]):
            raise ValueError("Runner summary/plan source differs from search evaluation")
        if value["scenario_ids"] != plan["scenario_ids"]:
            raise ValueError("Search candidates have unequal scenario support")
        audit_path, audit_sha = _checked_generation_audit(path.parent, value, plan)
        for row in value["candidates"]:
            if (row["complete_paired_case_count"] != len(plan["scenario_ids"])
                    or row["unknown_case_ids"]):
                continue
            per.setdefault(row["family"], []).append((
                tuple(row["priority_key"]), row, path, audit_path, audit_sha))
    selected = []
    evidence = []
    for family in plan["families"]:
        group = sorted(per.get(family, []), key=lambda item: (item[0], item[1]["policy_id"]))
        seen = set()
        for key, row, path, audit_path, audit_sha in group:
            theta_key = tuple(row["theta"])
            if theta_key in seen:
                continue
            seen.add(theta_key)
            selected.append({"policy_id": row["policy_id"], "kind": "v6_feedback",
                             "family": family, "theta": row["theta"]})
            evidence.append({"policy_id": row["policy_id"], "family": family,
                             "train_priority_key": list(key),
                             "search_evaluation_path": str(path),
                             "search_evaluation_sha256": _sha(path),
                             "search_raw_audit_path": str(audit_path),
                             "search_raw_audit_sha256": audit_sha})
            if len(seen) >= per_family:
                break
        if len(seen) < per_family:
            raise ValueError("Insufficient complete candidates for one feedback family")
    return selected, evidence


def register_expansion(search_dir, expansion_dir, *, per_family=1):
    manifest = _read(MANIFEST)
    scenes = choose_train_panel(manifest)
    selected, evidence = select_search_specs(search_dir, per_family=per_family)
    # These two physically active mechanisms were fully matched in old train
    # smoke; their inclusion broadens state coverage, not unseen selection.
    controls = [
        {"policy_id": "v6_gt_up_030", "kind": "v6_feedback",
         "family": "port_residual", "theta": [.3, .3, .3, 0, 0, 0]},
        {"policy_id": "v6_abs_up_050", "kind": "v6_feedback",
         "family": "port_residual", "theta": [0, 0, 0, 0, 0, .5]},
    ]
    specs = [{"policy_id": "secure_milp", "kind": "secure"}] + selected + controls
    if len({spec["policy_id"] for spec in specs}) != len(specs):
        raise ValueError("Selected search policy overlaps fixed train control")
    out = Path(expansion_dir)
    plan = register_batch(MANIFEST, out / "registered_plan.json",
                          scenario_ids=[scene["id"] for scene in scenes],
                          policy_specs=specs, preference_path=PREFERENCE, phase="train")
    record = {"schema": "policy_redesign.v6.train_expansion.v1",
              "search_plan_path": str(Path(search_dir) / "search_plan.json"),
              "search_plan_sha256": _sha(Path(search_dir) / "search_plan.json"),
              "manifest_path": str(MANIFEST), "manifest_sha256": _sha(MANIFEST),
              "pressure_indices": list(range(5)), "registered_train_roots": 30,
              "registered_train_scenarios": 150,
              "registered_policy_count": len(specs),
              "registered_pair_count": len(scenes) * len(specs),
              "complete_five_pressure_root_count": 30,
              "selection_data_used": False, "development_data_used": False,
              "search_candidate_evidence": evidence,
              "fixed_old_train_smoke_controls": controls,
              "batch_plan_signature": plan["signature"]}
    _new_json(out / "TRAIN_EXPANSION_REGISTRATION.json", record)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("register", "run"))
    parser.add_argument("--search-dir", type=Path, default=RUN / "search/adaptive_main")
    parser.add_argument("--expansion-dir", type=Path, default=RUN / "training/expanded_30roots_p01234")
    parser.add_argument("--max-new-pairs", type=int)
    args = parser.parse_args()
    if args.operation == "register":
        value = register_expansion(args.search_dir, args.expansion_dir)
        print(json.dumps({"registered_train_scenarios": value["registered_train_scenarios"],
                          "plan_signature": value["batch_plan_signature"]}))
    else:
        value = run_batch(args.expansion_dir / "registered_plan.json",
                          args.expansion_dir / "results", max_new_pairs=args.max_new_pairs)
        print(json.dumps({"terminal_pair_count": value["terminal_pair_count"],
                          "registered_pair_count": value["registered_pair_count"],
                          "status_counts": value["status_counts"]}))
