"""Current secure-anchored action continuations on registered stress futures."""
from __future__ import annotations
import argparse
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
import numpy as np
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.data_contract import decision_snapshot
from hospital_ies.dataset_export import json_clean
from tools.control_improvement.branches import compare_control_continuation, window_targets
from tools.control_improvement.candidates import candidate_numeric_features, choose_current_rule
from tools.control_improvement.runtime import extract_current_problem
from tools.control_improvement.ranking import TARGET_NAMES, paired_delta
from tools.integrated_learning.policies import BaselinePolicy, baseline_request, legal_history
from tools.integrated_learning.snapshot import ExecutionSnapshot, capture_execution
from tools.integrated_learning.truth import journal, save
from tools.control_improvement.episode import run_policy_episode

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_raw(path, payload):
    """Keep the complete trajectory in a compressed, portable JSON stream."""
    path = Path(path)
    if path.exists():
        raise FileExistsError("Existing raw trajectory must not be overwritten")
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=3) as stream:
        json.dump(json_clean(payload), stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class FirstSecureCandidatePolicy:
    """Training-only first current request, then the original secure policy."""

    def __init__(self, request, policy_id):
        self.first_request = copy.deepcopy(request)
        self.first = True
        self.policy_id = policy_id

    def decide(self, records):
        legal_history(records)
        first = self.first
        self.first = False
        request = self.first_request if first else baseline_request("secure_milp")
        return copy.deepcopy(request), {"policy_id": self.policy_id, "model_id": None,
                                        "strategy_fallback": False,
                                        "strategy_status": "registered_first_request_then_secure",
                                        "future_rollouts_online": 0}


def aggregate_future_windows(results, future_ids, candidate_ids):
    """Componentwise worst stress for each action, followed by secure deltas.

    Futures are a named pressure set, not a probability sample. Incomplete
    coordinates remain unknown; neither candidate nor future is dropped.
    """
    if not future_ids or not candidate_ids or len(set(future_ids)) != len(future_ids) or len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("empty or repeated registered future/candidate IDs")
    lookup = {}
    for row in results:
        key = row["future_id"], row["candidate_id"]
        if key in lookup:
            raise ValueError("duplicate candidate/future pair")
        if key[0] not in future_ids or key[1] not in candidate_ids:
            raise ValueError("unregistered candidate/future pair")
        window = row["window"]
        values, mask = window["targets"], window["target_mask"]
        if len(values) != len(TARGET_NAMES) or len(mask) != len(TARGET_NAMES):
            raise ValueError("candidate outcome dimension mismatch")
        if any(type(m) is not bool for m in mask):
            raise ValueError("candidate outcome mask is not boolean")
        if any((v is None or not math.isfinite(float(v))) if m else v is not None for v, m in zip(values, mask)):
            raise ValueError("candidate outcome value/mask mismatch")
        lookup[key] = row
    expected = {(future_id, candidate_id) for future_id in future_ids for candidate_id in candidate_ids}
    if set(lookup) != expected:
        raise ValueError("missing candidate/future pair")
    aggregated = []
    for candidate_id in candidate_ids:
        windows = [lookup[(future_id, candidate_id)]["window"] for future_id in future_ids]
        values = [max(float(window["targets"][j]) for window in windows)
                  if all(window["target_mask"][j] for window in windows) else None
                  for j in range(len(TARGET_NAMES))]
        aggregated.append({"candidate_id": candidate_id,
                           "aggregate_targets": values,
                           "aggregate_mask": [value is not None for value in values],
                           "future_status": ["known" if all(window["target_mask"]) else "unknown" for window in windows],
                           "future_ids": list(future_ids),
                           "aggregation": "coordinatewise_max_named_stress_set_no_probability"})
    reference = aggregated[0]["aggregate_targets"]
    for row in aggregated:
        row["delta_y"], row["target_mask"] = paired_delta(row["aggregate_targets"], reference)
    return aggregated


def _execution_class(payload, minute):
    generated = next((d.get("command_generated") for d in payload.get("decisions", [])
                      if float(d["decision_time_min"]) == float(minute)), None)
    if not generated:
        return "request_not_generated"
    command_id = generated["id"]
    lifecycle = [e for e in payload.get("events", []) if
                 e.get("kind") in ("command_arrived", "command_executed", "command_superseded")
                 and (e.get("command_id") == command_id or e.get("command", {}).get("id") == command_id)]
    if any(e["kind"] == "command_executed" for e in lifecycle):
        return "command_executed"
    if any(e["kind"] == "command_superseded" for e in lifecycle):
        return "superseded_before_execution"
    if any(e["kind"] == "command_arrived" for e in lifecycle):
        return "arrived_without_confirmed_execution"
    return "not_arrived_in_observed_window"


def run_snapshot_branch(env, scenario, manifest, entry, out):
    """One immutable secure snapshot and finite same-future offline branches."""
    from tools.reliability_first.data import conditional_futures
    from tools.reliability_first.diagnostics import opportunity_rows, snapshot_state_labels
    from tools.reliability_first.policy import prepare_secure_candidates

    out = Path(out)
    if out.exists():
        raise FileExistsError("Each snapshot attempt needs a fresh output directory")
    if entry["scenario_id"] != scenario["id"] or entry["experiment_role"] != scenario["experiment_role"]:
        raise ValueError("Registered snapshot/scenario identity differs")
    if entry["group_id"] != manifest["splits"][scenario["id"]]["group_id"]:
        raise ValueError("Registered snapshot group differs from frozen manifest")
    if entry["is_final_blind"] or entry["experiment_role"] == "development_boundary":
        raise ValueError("Development/final outcomes must not enter v4 fitting branches")
    t = float(entry["snapshot_minute"])
    if int(entry["snapshot_step"]) * float(scenario["step_minutes"]) != t:
        raise ValueError("Snapshot clock/step mismatch")
    out.mkdir(parents=True)
    log = out / "attempts.jsonl"
    source_binding = {name: sha(Path(__file__).with_name(name)) for name in
                      ("branches.py", "data.py", "diagnostics.py", "policy.py")}
    plan = {"schema": "reliability_first.branch.v1", "entry": entry,
            "manifest_signature": manifest["signature"],
            "manifest_scenario_sha256": hashlib.sha256(json.dumps(scenario, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
            "source_sha256": source_binding, "candidate_limit": 8,
            "continuation_policy_id": "secure_milp", "conditional_futures": 4,
            "aggregation": "coordinatewise_max_named_stress_set_no_probability",
            "online_future_access": False}
    save(out / "plan.json", plan)
    snapshots = {}
    seed = int(manifest["options"]["seed"])
    journal(log, event="started", attempt_type="snapshot_capture", scenario_id=scenario["id"], minute=t)
    reference = run_policy_episode(env, scenario, BaselinePolicy("secure_milp"), seed, manifest,
                                   horizons=(15, 30, 60, 120),
                                   snapshot_steps=(int(entry["snapshot_step"]),), snapshots=snapshots)
    save_raw(out / "reference_capture.json.gz", reference)
    journal(log, event="finished", attempt_type="snapshot_capture", status=reference["summary"]["status"],
            raw_sha256=sha(out / "reference_capture.json.gz"))
    if t not in snapshots:
        result = {"status": "snapshot_not_reached", "scenario_id": scenario["id"], "snapshot_minute": t,
                  "reference_status": reference["summary"]["status"], "fit_rows": [],
                  "capture_attempts": 1, "continuation_attempts": 0, "unknown": True}
        save(out / "BRANCH_RESULT.json", result)
        return result
    item = snapshots[t]
    execution = item["execution"]
    execution.save(out / "execution_snapshot.json")
    restored = ExecutionSnapshot.load(out / "execution_snapshot.json", template=env,
                                      expected_binding=execution.binding)
    original_final = capture_execution(env, binding=execution.binding)
    journal(log, event="started", attempt_type="D0_secure", scenario_id=scenario["id"], minute=t)
    d0_env = restored.fork()
    d0 = run_policy_episode(d0_env, scenario, BaselinePolicy("secure_milp"), seed, manifest,
                            reset=False, prefix_decisions=item["prefix_decisions"],
                            horizons=(15, 30, 60, 120))
    save_raw(out / "D0_secure.json.gz", d0)
    comparison = compare_control_continuation(reference, d0, t,
                    reference_final=original_final,
                    restored_final=capture_execution(d0_env, binding=execution.binding))
    save(out / "D0_comparison.json", comparison)
    journal(log, event="finished", attempt_type="D0_secure", status=d0["summary"]["status"],
            passed=comparison["passed"], raw_sha256=sha(out / "D0_secure.json.gz"))
    if not comparison["passed"]:
        result = {"status": "D0_mismatch", "scenario_id": scenario["id"],
                  "snapshot_minute": t, "fit_rows": [], "capture_attempts": 1,
                  "continuation_attempts": 1, "unknown": True}
        save(out / "BRANCH_RESULT.json", result)
        return result
    current = restored.fork()
    current.core.boundary()
    observation = decision_snapshot(current)
    history = copy.deepcopy(item["prefix_decisions"]) + [observation]
    before = capture_execution(current, binding=execution.binding).content_sha256
    problem = extract_current_problem(current)
    context, candidates, queries, equal_groups = prepare_secure_candidates(
        problem, history, attempt_callback=lambda **record: journal(log, minute=t, **record))
    after = capture_execution(current, binding=execution.binding).content_sha256
    if before != after:
        raise AssertionError("Pure current secure candidate query contaminated execution state")
    rule = choose_current_rule(history, context, candidates)
    save(out / "CURRENT_CANDIDATES.json", {"context": context, "history": history,
        "candidates": candidates, "queries": queries, "rule_candidate_index": rule,
        "equal_dispatch_groups_descriptive_only": equal_groups, "online_future_access": False})
    futures = conditional_futures(scenario, t, count=4)
    future_ids = ["original", "stress1", "stress2", "stress3"]
    save(out / "CONDITIONAL_FUTURES.json", {"ids": future_ids, "scenarios": futures,
        "same_known_prefix_minute": t, "probabilities": None,
        "meaning": "Named study stress set, not incident probabilities"})
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    outcomes = []
    for future_index, (future_id, future_scenario) in enumerate(zip(future_ids, futures)):
        for candidate_index, candidate in enumerate(candidates):
            if future_index == 0 and candidate_index == 0:
                payload, branch_env, raw_path = d0, d0_env, out / "D0_secure.json.gz"
            else:
                branch_env = restored.fork()
                branch_env.scenario = copy.deepcopy(future_scenario)
                request = candidate["request"]
                policy = FirstSecureCandidatePolicy(request, f"v4_{future_id}_{candidate_index:02d}")
                journal(log, event="started", attempt_type="current_action_future_continuation",
                        future_id=future_id, candidate_id=candidate["candidate_id"], minute=t)
                payload = run_policy_episode(branch_env, future_scenario, policy, seed, manifest,
                        reset=False, prefix_decisions=item["prefix_decisions"],
                        horizons=(15, 30, 60, 120), observation_end_minute=t + 120.)
                raw_path = out / f"future_{future_index:02d}_candidate_{candidate_index:02d}.json.gz"
                save_raw(raw_path, payload)
                journal(log, event="finished", attempt_type="current_action_future_continuation",
                        future_id=future_id, candidate_id=candidate["candidate_id"],
                        status=payload["summary"]["status"], raw_sha256=sha(raw_path))
            window = window_targets(payload, t, 120., branch_env)
            outcomes.append({"future_id": future_id, "candidate_id": candidate["candidate_id"],
                             "candidate_index": candidate_index,
                             "status": payload["summary"]["status"], "window": window,
                             "target_names": list(TARGET_NAMES), "targets": window["targets"],
                             "effective_execution_class": _execution_class(payload, t),
                             "trajectory_file": raw_path.name, "trajectory_sha256": sha(raw_path)})
    aggregated = aggregate_future_windows(outcomes, future_ids, candidate_ids)
    role = "train" if entry["experiment_role"] == "pilot_train" else "validation"
    snapshot_id = f"{scenario['id']}:t{t:g}"
    rows = []
    for index, (candidate, result) in enumerate(zip(candidates, aggregated)):
        rows.append({"row_id": f"{snapshot_id}:{candidate['candidate_id']}",
                     "snapshot_id": snapshot_id, "scenario_id": scenario["id"],
                     "group_id": entry["group_id"], "role": role,
                     "candidate_index": index, "candidate_id": candidate["candidate_id"],
                     "x": candidate_numeric_features(history, context, candidate),
                     "delta_y": result["delta_y"], "target_mask": result["target_mask"],
                     "loads_kW": context["loads_kW"],
                     "outcome_future_status": result["future_status"]})
    opportunity = opportunity_rows(outcomes, baseline_candidate_id="secure_baseline")
    try:
        labels = snapshot_state_labels(reference, t, 120)
    except ValueError as error:
        labels = {"unknown_reason": str(error), "source_status": reference["summary"]["status"]}
    result = {"schema": "reliability_first.branch_result.v1", "status": "complete",
              "scenario_id": scenario["id"], "snapshot_minute": t, "snapshot_id": snapshot_id,
              "group_id": entry["group_id"], "role": role,
              "capture_attempts": 1, "continuation_attempts": len(outcomes),
              "current_planning_queries": len(queries), "D0_passed": True,
              "candidate_count": len(candidates), "future_count": len(futures),
              "fit_rows": rows, "outcomes": outcomes, "aggregated": aggregated,
              "opportunities": opportunity, "snapshot_labels": labels,
              "unknown_rows": sum(not all(row["target_mask"]) for row in rows),
              "source_sha256": source_binding,
              "raw_trajectory_hashes": {p.name: sha(p) for p in out.glob("*.json.gz")}}
    save(out / "BRANCH_RESULT.json", result)
    if {name: sha(Path(__file__).with_name(name)) for name in source_binding} != source_binding:
        raise AssertionError("Branch source changed during execution")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("snapshot_plan", type=Path)
    parser.add_argument("index", type=int)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    from tools.control_improvement.data_runner import verify_manifest
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    verify_manifest(manifest)
    plan = json.loads(args.snapshot_plan.read_text(encoding="utf-8"))
    if not 0 <= args.index < len(plan):
        raise ValueError("Snapshot plan index outside registered design")
    entry = plan[args.index]
    by_id = {scenario["id"]: scenario for scenario in manifest["scenarios"]}
    scenario = by_id[entry["scenario_id"]]
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    result = run_snapshot_branch(env, scenario, manifest, entry, args.output_directory)
    print(json.dumps({key: result.get(key) for key in
                      ("status", "snapshot_id", "candidate_count", "future_count",
                       "capture_attempts", "continuation_attempts", "unknown_rows")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
