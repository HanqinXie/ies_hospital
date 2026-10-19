"""Deterministic two-generation offline search of unified feedback parameters.

Each theta is one *current-observation feedback policy* across multiple root
families, evaluated in complete trajectories. This module never supplies a
future event clock or a future action sequence to the online policy.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from tools.policy_search.closed_loop import register_batch, run_batch
from tools.policy_search.policy import parameter_bounds
from tools.policy_search.preference import paired_metrics, p0_dominates, search_key


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def choose_scenes(manifest, *, role, roots_per_condition, pressure_index):
    """Outcome-free coverage of each registered condition at a fixed pressure."""
    by_condition = {}
    for scene in manifest["scenarios"]:
        if scene["experiment_role"] == role and scene["pressure_point_index"] == pressure_index:
            by_condition.setdefault(scene["condition_id"], []).append(scene)
    if len(by_condition) != 6:
        raise ValueError("Six named conditions are needed for balanced search")
    chosen = []
    for condition in sorted(by_condition):
        rows = sorted(by_condition[condition], key=lambda s: (s["time_block_id"], s["id"]))
        roots = set()
        for scene in rows:
            if scene["root_family_id"] in roots:
                continue
            chosen.append(scene)
            roots.add(scene["root_family_id"])
            if len(roots) == roots_per_condition:
                break
        if len(roots) != roots_per_condition:
            raise ValueError("Insufficient disjoint roots in a named condition")
    return chosen


def initial_policy_specs():
    """Six pre-outcome configurations across two substantive feedback families."""
    configurations = [
        ("blend_health", "blend", [.65, 2.0, 0.0, 0.0, 0.0]),
        ("blend_overload", "blend", [.8, 1.0, 2.5, 0.0, 0.0]),
        ("blend_grid_abs", "blend", [.8, 1.0, 1.0, .8, -.5]),
        ("mode_response", "mode", [0, 0, 0, 0, .8, 2.0, .5, -.5, .3, 1.0, .2, 0]),
        ("mode_prepare", "mode", [.5, 1.0, .8, .3, .7, 2.0, .6, -.4, .4, 1.0, .3, .2]),
        ("mode_recovery", "mode", [.25, 1.0, .5, 0, .8, 2.5, .8, -.3, .8, 1.5, .8, .4]),
    ]
    return [{"policy_id": "secure_milp", "kind": "secure"}] + [
        {"policy_id": name, "kind": "feedback", "family": family, "theta": theta}
        for name, family, theta in configurations]


def evolved_policy_specs(analysis):
    """Bounded deterministic mutation of the best fully observed train parent.

    This is a small black-box evolutionary step, not online horizon control.
    If one family has no observable score, retain its pre-registered first
    parent for structural exploration rather than silently removing the route.
    """
    parents = {}
    initial = {s["policy_id"]: s for s in initial_policy_specs()[1:]}
    metrics = analysis["metrics"]
    for family in ("blend", "mode"):
        options = [s for s in initial.values() if s["family"] == family]
        options.sort(key=lambda s: (search_key(metrics[s["policy_id"]]), s["policy_id"]))
        parents[family] = options[0]
    variations = []
    for family in ("blend", "mode"):
        parent = parents[family]
        bounds = parameter_bounds(family)
        for index, (coordinate, delta) in enumerate(
            ((0, .2), (0, -.25), (2 if family == "blend" else 6, .45),
             (4 if family == "blend" else 7, -.4))):
            theta = list(parent["theta"])
            low, high = bounds[coordinate]
            theta[coordinate] = min(high, max(low, theta[coordinate] + delta))
            if theta == parent["theta"]:
                theta[coordinate] = (low + high) / 2.0
            variations.append({"policy_id": f"g2_{family}_{index+1}", "kind": "feedback",
                               "family": family, "theta": theta})
    return [{"policy_id": "secure_milp", "kind": "secure"}] + variations


def register_generation(run_dir, generation):
    run = Path(run_dir)
    manifest_path = run / "new_data/global_manifest.json"
    manifest = _read(manifest_path)
    preference = run / "design/RESEARCH_PREFERENCE_v1.json"
    if generation == 1:
        scenes = choose_scenes(manifest, role="train", roots_per_condition=1, pressure_index=3)
        specs = initial_policy_specs()
    elif generation == 2:
        analysis = _read(run / "search/generation_1/ANALYSIS.json")
        scenes = choose_scenes(manifest, role="train", roots_per_condition=2, pressure_index=3)
        specs = evolved_policy_specs(analysis)
    else:
        raise ValueError("Only two registered search generations")
    folder = run / f"search/generation_{generation}"
    plan_path = folder / "registered_plan.json"
    plan = register_batch(manifest_path, plan_path, scenario_ids=[s["id"] for s in scenes],
                          policy_specs=specs, preference_path=preference, phase="train")
    _new(folder / "SEARCH_REGISTRATION.json",
         {"schema": "policy_search.offline_generation.v1", "generation": generation,
          "source_sha256": _sha(__file__), "manifest_sha256": _sha(manifest_path),
          "preference_sha256": _sha(preference), "scenario_ids": plan["scenario_ids"],
          "policy_specs": specs, "plan_signature": plan["signature"],
          "selection_uses_train_outcomes_only": generation == 2,
          "whole_policy_continuation": True, "online_future_actions": False})
    return plan


def analyze_generation(run_dir, generation):
    folder = Path(run_dir) / f"search/generation_{generation}"
    summaries = sorted((folder / "results").glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("No actual generation results")
    summary = _read(summaries[-1])
    plan = _read(folder / "registered_plan.json")
    if summary["terminal_pair_count"] != plan["registered_pair_count"]:
        raise ValueError("Generation has nonterminal pairs; do not rank incomplete batch")
    metrics = {s["policy_id"]: paired_metrics(summary["results"], s["policy_id"])
               for s in plan["policy_specs"]}
    candidate_ids = [s["policy_id"] for s in plan["policy_specs"] if s["kind"] == "feedback"]
    front = [name for name in candidate_ids if not any(
        p0_dominates(metrics[other], metrics[name]) for other in candidate_ids if other != name)]
    analysis = {"schema": "policy_search.generation_analysis.v1", "generation": generation,
                "summary_sha256": _sha(summaries[-1]), "plan_signature": plan["signature"],
                "metrics": metrics, "p0_pareto_ids": front,
                "search_allocation_order": sorted(candidate_ids,
                                                 key=lambda name: (search_key(metrics[name]), name)),
                "incomplete_does_not_mean_zero": True,
                "research_preference_not_clinical": True}
    _new(folder / "ANALYSIS.json", analysis)
    return analysis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("register", "run", "analyze"))
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--generation", type=int, choices=(1, 2), required=True)
    parser.add_argument("--max-new-pairs", type=int)
    args = parser.parse_args()
    if args.operation == "register":
        value = register_generation(args.run_dir, args.generation)
        print(json.dumps({"plan_signature": value["signature"],
                          "registered_pair_count": value["registered_pair_count"]}))
    elif args.operation == "run":
        folder = args.run_dir / f"search/generation_{args.generation}"
        value = run_batch(folder / "registered_plan.json", folder / "results",
                          max_new_pairs=args.max_new_pairs)
        print(json.dumps({"terminal_pair_count": value["terminal_pair_count"],
                          "registered_pair_count": value["registered_pair_count"],
                          "status_counts": value["status_counts"]}))
    else:
        value = analyze_generation(args.run_dir, args.generation)
        print(json.dumps({"p0_pareto_ids": value["p0_pareto_ids"],
                          "search_allocation_order": value["search_allocation_order"]}))


if __name__ == "__main__":
    main()
