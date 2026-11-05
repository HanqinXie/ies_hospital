"""Adaptive, outcome-driven search over complete v6 feedback policies.

The search sees only saved episode outcomes.  Deployed policies in this module
are ordinary current-observation ``v6_feedback`` policies; no future scenario
script, horizon optimizer or command execution shortcut is introduced.

``register_search`` freezes one train-root panel, a preference and a finite
attempt budget. ``advance_search`` registers a generation before evaluating
it with the immutable matched runner. Saved proposals, runner attempts, raw
trajectories and evaluations make restart deterministic and auditable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import time

from tools.policy_redesign.closed_loop import register_batch, run_batch
from tools.policy_redesign.policy import parameter_names
from tools.policy_search.preference import paired_metrics


SCHEMA = "policy_redesign.adaptive_feedback_search.v1"
SECURE_SPEC = {"policy_id": "secure_milp", "kind": "secure"}
ROOT = Path(__file__).resolve().parents[2]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _signed(value):
    body = dict(value)
    body["signature"] = _digest(body)
    return body


def _checked(path, schema):
    value = _read(path)
    if value.get("schema") != schema or value.get("signature") != _digest(
            {k: v for k, v in value.items() if k != "signature"}):
        raise ValueError(f"Signed search artifact changed: {path}")
    return value


def _journal(root, *, event_id, event, **fields):
    path = Path(root) / "journal.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if json.loads(line).get("event_id") == event_id:
                return
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event_id": event_id, "event": event,
                                 "time_unix": time.time(), **fields},
                                ensure_ascii=False, allow_nan=False) + "\n")


def _validate_integer(value, name, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def register_search(manifest_path, search_dir, *, scenario_ids, preference_path,
                    seeds=(11, 23, 37), families=("port_residual", "source_allocation"),
                    population_size=8, min_generations=3, max_generations=8,
                    patience=3, mutation_scale=0.3, max_actual_attempts=6000):
    """Preregister a common train panel and adaptive search limits.

    The actual runner validates manifest inventory, phase, physics and source
    binding again before each batch. This layer binds one design across seeds.
    """
    manifest = Path(manifest_path).resolve()
    preference = Path(preference_path).resolve()
    if not manifest.is_file() or not preference.is_file():
        raise FileNotFoundError("Manifest and preregistered preference are required")
    pref = _read(preference)
    if (pref.get("schema") != "policy_redesign.v6.preregistered_preference.v1" or
            pref.get("registered_before_new_selection_or_development_outcomes") is False or
            pref.get("final_12_backgrounds_unopened") is False):
        raise ValueError("Expected frozen v6 P0-first research preference")
    ids = list(scenario_ids)
    if not ids or len(ids) != len(set(ids)) or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError("Common train scenario IDs must be unique and nonempty")
    if len(ids) > 1500:
        raise ValueError("Scenario panel exceeds new exogenous-spec ceiling")
    seeds = tuple(seeds)
    if len(seeds) < 3 or len(seeds) != len(set(seeds)):
        raise ValueError("At least three distinct search seeds are required")
    for seed in seeds:
        _validate_integer(seed, "seed")
    families = tuple(families)
    if not families or len(families) != len(set(families)):
        raise ValueError("At least one unique feedback family is required")
    for family in families:
        parameter_names(family)
    _validate_integer(population_size, "population_size", minimum=len(families))
    if population_size > 79:
        raise ValueError("At most 79 search candidates fit with the secure baseline")
    if any(len(seeds) * sum(i % len(families) == j for i in range(population_size)) < 8
           for j in range(len(families))):
        raise ValueError("Too few first-generation candidates for signed directional coverage")
    _validate_integer(min_generations, "min_generations", minimum=3)
    if type(max_generations) is not int or max_generations < 3:
        raise ValueError("Adaptive search needs at least three generations")
    if max_generations < min_generations:
        raise ValueError("Adaptive search needs at least three generations")
    _validate_integer(patience, "patience", minimum=1)
    _validate_integer(max_actual_attempts, "max_actual_attempts", minimum=1)
    if max_actual_attempts > 30000:
        raise ValueError("Search budget exceeds global v6 actual-attempt ceiling")
    if isinstance(mutation_scale, bool) or not isinstance(mutation_scale, (int, float)):
        raise ValueError("mutation_scale must be finite and positive")
    if not math.isfinite(mutation_scale) or not 0 < mutation_scale <= 1:
        raise ValueError("mutation_scale must be in (0,1]")
    root = Path(search_dir)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Use a fresh adaptive search directory")
    root.mkdir(parents=True, exist_ok=True)
    plan = _signed({
        "schema": SCHEMA + ".plan", "phase": "train",
        "manifest_path": str(manifest), "manifest_sha256": _sha(manifest),
        "preference_path": str(preference), "preference_sha256": _sha(preference),
        "search_source_sha256": _sha(__file__),
        "scenario_ids": ids, "seeds": list(seeds), "families": list(families),
        "population_size": population_size, "min_generations": min_generations,
        "max_generations": max_generations, "patience": patience,
        "mutation_scale": float(mutation_scale),
        "max_actual_attempts": max_actual_attempts,
        "secure_policy_spec": SECURE_SPEC,
        "final_blind_opened": False,
    })
    _new_json(root / "search_plan.json", plan)
    _journal(root, event_id="search:registered", event="registered",
             plan_signature=plan["signature"], common_case_count=len(ids),
             seeds=list(seeds), max_actual_attempts=max_actual_attempts)
    return plan


def _load_plan(root):
    plan = _checked(Path(root) / "search_plan.json", SCHEMA + ".plan")
    if (plan["phase"] != "train" or plan["final_blind_opened"] is not False or
            plan["secure_policy_spec"] != SECURE_SPEC):
        raise ValueError("Search execution boundary changed")
    if (_sha(plan["manifest_path"]) != plan["manifest_sha256"] or
            _sha(plan["preference_path"]) != plan["preference_sha256"] or
            _sha(__file__) != plan["search_source_sha256"]):
        raise ValueError("Search source, manifest or preference changed after registration")
    return plan


def _worst_target_increase(metric, index):
    values = [case["delta_targets"][index] for case in metric["individual_cases"].values()
              if case["complete_paired_window"]]
    return max(0.0, *values)


def priority_key(metric):
    """Exact preregistered P0-first *allocation order*, never a utility score.

    Unknown support leads. P0 event net and new cases precede all magnitudes;
    duration precedes each P0 carrier ENS. P0 temperature precedes lower
    priorities; modeled cost is strictly the final tie breaker.
    """
    n = metric["registered_case_count"]
    if n <= 0:
        raise ValueError("Cannot rank an empty paired panel")
    unknown = len(metric["unknown_case_ids"])
    if not metric["complete_paired_case_count"]:
        return (unknown, n + 1, n + 1)
    totals = metric["total_delta_by_target"]
    return (
        unknown,
        metric["new_P0_failure_cases"] - metric["resolved_P0_failure_cases"],
        metric["new_P0_failure_cases"], -metric["resolved_P0_failure_cases"],
        metric["largest_case_P0_failure_minutes_increase"],
        metric["P0_failure_minutes_total_delta"],
        metric["largest_case_P0_e_ENS_increase"],
        metric["largest_case_P0_h_ENS_increase"],
        metric["largest_case_P0_c_ENS_increase"],
        metric["P0_e_ENS_kWh_delta"], metric["P0_h_ENS_kWh_delta"],
        metric["P0_c_ENS_kWh_delta"],
        _worst_target_increase(metric, 14), totals[14],
        *(totals[i] for i in (*range(5, 14), 15, 16, 17)),
        totals[18],
    )


def _archive_digest(archive):
    return _digest(archive)


def _direction_catalog(family):
    """Sparse, signed probes in normalized units, not new physical capacity."""
    count = len(parameter_names(family))

    def vector(entries):
        values = [0.0] * count
        for index, value in entries.items():
            values[index] = value
        return values

    if family == "port_residual":
        directions = (
            {0: 0.8, 1: 0.8, 2: 0.8},
            {0: -0.8, 1: -0.8, 2: -0.8},
            {4: 0.8}, {4: -0.8},
            {3: 0.8}, {3: -0.8},
            {5: 0.8}, {5: -0.8},
            {0: 0.8}, {0: -0.8},
            {1: 0.8}, {1: -0.8},
            {2: 0.8}, {2: -0.8},
        )
    else:
        directions = (
            {0: 0.8}, {0: -0.8},
            {5: 0.8}, {5: -0.8},
            {4: 0.8}, {4: -0.8},
            {6: 0.8}, {6: -0.8},
            {1: 0.8, 2: -0.8}, {1: -0.8, 2: 0.8},
            {1: 0.8, 3: -0.8}, {1: -0.8, 3: 0.8},
        )
    return [vector(entries) for entries in directions]


def propose_population(*, seed, generation, population_size, families, archive=(),
                       mutation_scale=0.3, seed_index=0):
    """Deterministically propose six-port feedback policies from evaluated parents.

    The archive contains prior *episode* rankings, never imitation labels.
    Family-specific parent selection keeps both legal representations alive.
    """
    _validate_integer(seed, "seed")
    _validate_integer(seed_index, "seed_index")
    _validate_integer(generation, "generation", minimum=1)
    _validate_integer(population_size, "population_size", minimum=1)
    families = tuple(families)
    if not families or population_size < len(families):
        raise ValueError("Every requested family needs a candidate")
    for family in families:
        parameter_names(family)
    if not math.isfinite(float(mutation_scale)) or not 0 < float(mutation_scale) <= 1:
        raise ValueError("Invalid mutation scale")
    rng = random.Random(int(hashlib.sha256(
        f"v6-feedback:{seed}:{generation}".encode("utf-8")).hexdigest(), 16))
    ranked = {}
    for family in families:
        ranked[family] = sorted(
            (row for row in archive if row["family"] == family and
             row.get("complete_paired_case_count", 1) > 0),
            key=lambda row: (tuple(row["priority_key"]), row["policy_id"]))
    specs, lineage = [], []
    family_counts = {family: 0 for family in families}
    family_slots = {family: sum(i % len(families) == j for i in range(population_size))
                    for j, family in enumerate(families)}
    for index in range(population_size):
        family = families[index % len(families)]
        local_index = family_counts[family]
        family_counts[family] += 1
        parents = ranked[family]
        parent = parents[0] if generation > 1 and parents else None
        if parent is None:
            catalogue = _direction_catalog(family)
            direction = seed_index * family_slots[family] + local_index
            if generation > 1:
                direction += generation * family_slots[family]
            theta = catalogue[direction % len(catalogue)].copy()
            mode = "sparse_directional_exploration"
        else:
            direction = ([float(a) - float(b) for a, b in
                          zip(parent["theta"], parents[1]["theta"])]
                         if len(parents) > 1 else [0.0] * len(parent["theta"]))
            scale = float(mutation_scale) * (1.0 + 0.25 * (index % 3))
            active = {j for j, value in enumerate(parent["theta"]) if abs(value) > 1e-9}
            if len(active) < 3 and index % 3 == 2:
                inactive = [j for j in range(len(parent["theta"])) if j not in active]
                if inactive:
                    active.add(inactive[rng.randrange(len(inactive))])
            theta = [round(max(-1.0, min(1.0, float(x) +
                       0.25 * direction[j] + rng.gauss(0.0, scale))), 9)
                     if j in active else 0.0
                     for j, x in enumerate(parent["theta"])]
            mode = "outcome_ranked_mutation"
        policy_id = f"seed{seed}_g{generation}_c{index:02d}"
        specs.append({"policy_id": policy_id, "kind": "v6_feedback",
                      "family": family, "theta": theta})
        lineage.append({"policy_id": policy_id, "family": family,
                        "parent_policy_id": None if parent is None else parent["policy_id"],
                        "parent_priority_key": None if parent is None else parent["priority_key"],
                        "generation": generation, "mode": mode})
    return {"schema": SCHEMA + ".proposal", "seed": seed,
            "seed_index": seed_index,
            "generation": generation, "population_size": population_size,
            "archive_sha256": _archive_digest(archive),
            "policy_specs": specs, "lineage": lineage}


def _seed_dir(root, seed):
    return Path(root) / f"seed_{seed:04d}"


def _generation_dir(root, seed, generation):
    return _seed_dir(root, seed) / f"generation_{generation:04d}"


def _load_evaluations(root, seed, last_generation):
    evaluations = []
    for gen in range(1, last_generation + 1):
        path = _generation_dir(root, seed, gen) / "evaluation.json"
        if not path.is_file():
            raise ValueError("Prior generation evaluation missing")
        evaluations.append(_checked(path, SCHEMA + ".evaluation"))
    return evaluations


def _archive(evaluations):
    return [entry for evaluation in evaluations for entry in evaluation["candidates"]]


def _best(archive):
    eligible = [row for row in archive if row["complete_paired_case_count"] > 0]
    return min(eligible, key=lambda row: (tuple(row["priority_key"]), row["policy_id"])) if eligible else None


def _stale_generations(evaluations):
    if not evaluations:
        return 0
    best_key = None
    stale = 0
    for evaluation in evaluations:
        current = _best(evaluation["candidates"])
        current_key = None if current is None else tuple(current["priority_key"])
        if current_key is not None and (best_key is None or current_key < best_key):
            best_key, stale = current_key, 0
        else:
            stale += 1
    return stale


def _actual_attempts(root):
    # Count physical runner attempt directories; journal counts also cover a
    # caller-provided test runner and a just-finished call before disk scan.
    on_disk = sum(1 for _ in Path(root).glob("seed_*/generation_*/results/pairs/*/attempt_*"))
    path = Path(root) / "journal.jsonl"
    logged = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("event") == "runner_returned":
                logged += event["attempted_this_call"]
    return max(on_disk, logged)


def _validate_terminal_summary(summary, plan):
    expected = {(sid, spec["policy_id"]) for sid in plan["scenario_ids"]
                for spec in plan["policy_specs"]}
    rows = summary["results"]
    seen = [(row["scenario_id"], row["policy_id"]) for row in rows]
    if (summary["registered_pair_count"] != len(expected) or
            summary["recorded_pair_count"] != len(expected) or
            summary["terminal_pair_count"] != len(expected) or
            len(seen) != len(set(seen)) or set(seen) != expected or
            not all(row.get("terminal") for row in rows)):
        raise ValueError("Search batch does not have one terminal result per registered pair")
    if summary["matched_initial_state_and_stream"] is not True:
        raise ValueError("Search batch initial states or exogenous streams differ")
    if not all(row.get("binding", {}).get("passed") is True for row in rows):
        raise ValueError("Search batch has invalid physical/source bindings")


def _evaluate_summary(summary, plan, proposal, seed, generation):
    _validate_terminal_summary(summary, plan)
    rows = summary["results"]
    baseline = paired_metrics(rows, "secure_milp")
    baseline_key = priority_key(baseline)
    candidates = []
    for spec, lineage in zip(proposal["policy_specs"], proposal["lineage"]):
        pid = spec["policy_id"]
        primary = paired_metrics(rows, pid, score_field="primary_score")
        full = paired_metrics(rows, pid, score_field="full_observation")
        key = priority_key(primary)
        candidates.append({
            "policy_id": pid, "family": spec["family"], "theta": spec["theta"],
            "parent_policy_id": lineage["parent_policy_id"],
            "lineage_mode": lineage["mode"],
            "priority_key": list(key),
            "beats_secure_on_complete_common_panel": (
                primary["complete_paired_case_count"] == len(plan["scenario_ids"])
                and key < baseline_key),
            "complete_paired_case_count": primary["complete_paired_case_count"],
            "unknown_case_ids": primary["unknown_case_ids"],
            "primary_metrics": primary, "full_metrics": full,
        })
    order = sorted(candidates, key=lambda row: (tuple(row["priority_key"]), row["policy_id"]))
    return {"schema": SCHEMA + ".evaluation", "seed": seed,
            "generation": generation, "scenario_ids": plan["scenario_ids"],
            "registered_pair_count": summary["registered_pair_count"],
            "terminal_pair_count": summary["terminal_pair_count"],
            "matched_initial_state_and_stream": True,
            "baseline_priority_key": list(baseline_key),
            "baseline_primary_metrics": baseline,
            "candidate_order": [row["policy_id"] for row in order],
            "candidates": candidates,
            "all_raw_and_unknown_preserved_in_runner": True}


def _seed_stop(root, seed, reason, generations):
    path = _seed_dir(root, seed) / "stop.json"
    if not path.exists():
        _new_json(path, _signed({"schema": SCHEMA + ".seed_stop", "seed": seed,
                                 "reason": reason, "completed_generations": generations}))
    _journal(root, event_id=f"seed:{seed}:stop", event="seed_stopped",
             seed=seed, reason=reason, completed_generations=generations)


def advance_search(search_dir, *, max_new_pairs_per_call=None):
    """Evaluate at most one pending generation/chunk, then return its state."""
    root = Path(search_dir)
    plan = _load_plan(root)
    if max_new_pairs_per_call is not None:
        _validate_integer(max_new_pairs_per_call, "max_new_pairs_per_call", minimum=1)
    for seed in plan["seeds"]:
        stop_path = _seed_dir(root, seed) / "stop.json"
        if stop_path.is_file():
            _checked(stop_path, SCHEMA + ".seed_stop")
            continue
        for generation in range(1, plan["max_generations"] + 1):
            directory = _generation_dir(root, seed, generation)
            evaluation_path = directory / "evaluation.json"
            if evaluation_path.is_file():
                evaluation = _checked(evaluation_path, SCHEMA + ".evaluation")
                _journal(root, event_id=f"seed:{seed}:gen:{generation}:evaluated",
                         event="evaluated", seed=seed, generation=generation,
                         evaluation_sha256=_sha(evaluation_path),
                         best_policy_id=evaluation["candidate_order"][0])
                continue
            prior = _load_evaluations(root, seed, generation - 1)
            if generation > plan["min_generations"] and _stale_generations(prior) >= plan["patience"]:
                _seed_stop(root, seed, "patience_after_min_generations", generation - 1)
                break
            used = _actual_attempts(root)
            remaining = plan["max_actual_attempts"] - used
            if remaining <= 0:
                return {"status": "budget_exhausted", "actual_attempts": used,
                        "seed": seed, "next_generation": generation}
            archive = _archive(prior)
            proposal_path = directory / "proposals.json"
            if proposal_path.is_file():
                proposal = _checked(proposal_path, SCHEMA + ".proposal")
                if proposal["archive_sha256"] != _archive_digest(archive):
                    raise ValueError("Proposal parent archive changed on resume")
            else:
                proposal = _signed(propose_population(
                    seed=seed, generation=generation,
                    seed_index=plan["seeds"].index(seed),
                    population_size=plan["population_size"],
                    families=plan["families"], archive=archive,
                    mutation_scale=plan["mutation_scale"]))
                _new_json(proposal_path, proposal)
            _journal(root, event_id=f"seed:{seed}:gen:{generation}:proposed",
                     event="proposed", seed=seed, generation=generation,
                     proposal_sha256=_sha(proposal_path),
                     parent_policy_ids=[x["parent_policy_id"] for x in proposal["lineage"]])
            batch_path = directory / "registered_plan.json"
            specs = [SECURE_SPEC] + proposal["policy_specs"]
            if batch_path.is_file():
                batch = _read(batch_path)
                if batch.get("scenario_ids") != plan["scenario_ids"] or batch.get("policy_specs") != specs:
                    raise ValueError("Resumed runner batch differs from frozen search design")
            else:
                batch = register_batch(plan["manifest_path"], batch_path,
                                       scenario_ids=plan["scenario_ids"],
                                       policy_specs=specs,
                                       preference_path=plan["preference_path"],
                                       phase="train")
            _journal(root, event_id=f"seed:{seed}:gen:{generation}:registered",
                     event="batch_registered", seed=seed, generation=generation,
                     runner_plan_sha256=_sha(batch_path),
                     registered_pair_count=len(plan["scenario_ids"]) * len(specs))
            cap = remaining if max_new_pairs_per_call is None else min(remaining, max_new_pairs_per_call)
            summary = run_batch(batch_path, directory / "results", max_new_pairs=cap)
            attempted = summary["attempted_this_call"]
            if type(attempted) is not int or attempted < 0 or attempted > cap:
                raise ValueError("Runner attempt count exceeds registered search budget")
            _journal(root, event_id=f"seed:{seed}:gen:{generation}:runner:{_actual_attempts(root)}:{attempted}",
                     event="runner_returned", seed=seed, generation=generation,
                     attempted_this_call=attempted,
                     terminal_pair_count=summary["terminal_pair_count"],
                     registered_pair_count=summary["registered_pair_count"])
            if summary["terminal_pair_count"] != summary["registered_pair_count"]:
                return {"status": "running" if attempted else "stalled",
                        "seed": seed, "generation": generation,
                        "actual_attempts": _actual_attempts(root),
                        "terminal_pair_count": summary["terminal_pair_count"],
                        "registered_pair_count": summary["registered_pair_count"]}
            try:
                evaluation = _evaluate_summary(summary, batch, proposal, seed, generation)
            except ValueError as error:
                receipt = directory / "integrity_failure.json"
                if not receipt.exists():
                    _new_json(receipt, {"schema": SCHEMA + ".integrity_failure",
                                        "reason": str(error), "seed": seed,
                                        "generation": generation})
                return {"status": "integrity_failed", "seed": seed,
                        "generation": generation, "reason": str(error)}
            source = directory / "summary_for_search.json"
            if not source.exists():
                _new_json(source, summary)
            evaluation["runner_summary_sha256"] = _sha(source)
            evaluation["runner_plan_sha256"] = _sha(batch_path)
            _new_json(evaluation_path, _signed(evaluation))
            _journal(root, event_id=f"seed:{seed}:gen:{generation}:evaluated",
                     event="evaluated", seed=seed, generation=generation,
                     evaluation_sha256=_sha(evaluation_path),
                     best_policy_id=evaluation["candidate_order"][0])
            return {"status": "advanced", "seed": seed, "generation": generation,
                    "actual_attempts": _actual_attempts(root),
                    "best_policy_id": evaluation["candidate_order"][0]}
        else:
            _seed_stop(root, seed, "max_generations", plan["max_generations"])
    used = _actual_attempts(root)
    return {"status": "complete", "actual_attempts": used,
            "seeds": list(plan["seeds"]),
            "completed_generations_by_seed": {
                str(seed): len(list(_seed_dir(root, seed).glob("generation_*/evaluation.json")))
                for seed in plan["seeds"]}}


def run_search(search_dir, *, max_steps=None, max_new_pairs_per_call=None):
    """Continue until completion, a finite step limit, a gate failure or budget."""
    if max_steps is not None:
        _validate_integer(max_steps, "max_steps", minimum=1)
    steps = 0
    while max_steps is None or steps < max_steps:
        result = advance_search(search_dir, max_new_pairs_per_call=max_new_pairs_per_call)
        steps += 1
        if result["status"] not in ("advanced", "running"):
            return result
    return {"status": "step_limit", "steps": steps,
            "actual_attempts": _actual_attempts(search_dir)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register")
    register.add_argument("--manifest", required=True)
    register.add_argument("--search-dir", required=True)
    register.add_argument("--preference", required=True)
    register.add_argument("--scenario-id", action="append", required=True)
    register.add_argument("--seed", action="append", type=int)
    register.add_argument("--family", action="append")
    register.add_argument("--population-size", type=int, default=8)
    register.add_argument("--min-generations", type=int, default=3)
    register.add_argument("--max-generations", type=int, default=8)
    register.add_argument("--patience", type=int, default=3)
    register.add_argument("--mutation-scale", type=float, default=0.3)
    register.add_argument("--max-actual-attempts", type=int, default=6000)
    run = sub.add_parser("run")
    run.add_argument("--search-dir", required=True)
    run.add_argument("--max-steps", type=int)
    run.add_argument("--max-new-pairs-per-call", type=int)
    args = parser.parse_args(argv)
    if args.command == "register":
        outcome = register_search(args.manifest, args.search_dir,
                                  scenario_ids=args.scenario_id,
                                  preference_path=args.preference,
                                  seeds=args.seed or (11, 23, 37),
                                  families=args.family or ("port_residual", "source_allocation"),
                                  population_size=args.population_size,
                                  min_generations=args.min_generations,
                                  max_generations=args.max_generations,
                                  patience=args.patience,
                                  mutation_scale=args.mutation_scale,
                                  max_actual_attempts=args.max_actual_attempts)
    else:
        outcome = run_search(args.search_dir, max_steps=args.max_steps,
                             max_new_pairs_per_call=args.max_new_pairs_per_call)
    print(json.dumps(outcome, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
