"""Evidence-first matched development comparison and v4 report rendering."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import os
from pathlib import Path

import numpy as np

from tools.control_improvement.ranking import TARGET_NAMES
from hospital_ies.dataset_manifest import digest


def _target_values(result):
    """Return independently observed coordinates; a partial P0 event survives censoring."""
    score = (result or {}).get("primary_score") or {}
    values, mask = score.get("targets"), score.get("target_mask")
    if not isinstance(values, list) or not isinstance(mask, list) or (
            len(values) != len(TARGET_NAMES) or len(mask) != len(TARGET_NAMES)):
        return [None] * len(TARGET_NAMES)
    out = []
    for present, value in zip(mask, values):
        try:
            number = float(value) if present and value is not None else None
        except (TypeError, ValueError, OverflowError):
            number = None
        out.append(number if number is not None and np.isfinite(number) else None)
    return out


def _known(result):
    return bool(result and result.get("status") == "completed"
                and (result.get("primary_score") or {}).get("full_horizon") is True
                and all(value is not None for value in _target_values(result)))


def _pair_bound(reference, candidate, globally_matched):
    if not reference or not candidate:
        return False
    a, b = reference.get("binding"), candidate.get("binding")
    if a is None or b is None:
        return globally_matched is True
    return (a.get("passed") is True and b.get("passed") is True
            and a.get("initial_state_sha256") == b.get("initial_state_sha256")
            and a.get("exogenous_stream_id") == b.get("exogenous_stream_id"))


def paired_policy_summary(plan, summary, policy_id, *, scenario_ids=None,
                          reference_policy_id="secure_milp"):
    ids = list(plan["scenario_ids"] if scenario_ids is None else scenario_ids)
    registered = {s["policy_id"] for s in plan["policy_specs"]}
    if (policy_id not in registered or reference_policy_id not in registered
            or policy_id == reference_policy_id):
        raise ValueError("Comparison needs a registered nonbaseline policy")
    if len(ids) != len(set(ids)) or set(ids) - set(plan["scenario_ids"]):
        raise ValueError("Invalid scenario subset")
    by_pair = {(r["scenario_id"], r["policy_id"]): r for r in summary["results"]}
    rows = []
    deltas = []
    new_failure = reduced_failure = partial_new_failure = duration_worse = 0
    status_counts = Counter()
    interventions = 0
    for sid in ids:
        baseline = by_pair.get((sid, reference_policy_id))
        candidate = by_pair.get((sid, policy_id))
        status_counts[(candidate or {}).get("status", "missing")] += 1
        bound = _pair_bound(baseline, candidate, summary.get("matched_initial_state_passed"))
        known = bound and _known(baseline) and _known(candidate)
        a = _target_values(baseline) if bound else [None] * len(TARGET_NAMES)
        b = _target_values(candidate) if bound else [None] * len(TARGET_NAMES)
        delta = [bv - av if av is not None and bv is not None else None
                 for av, bv in zip(a, b)]
        entry = {"scenario_id": sid, "policy_id": policy_id,
                 "reference_policy_id": reference_policy_id, "paired_known": bool(known),
                 "matched_initial_state_and_exogenous": bool(bound),
                 "secure_status": (baseline or {}).get("status", "missing"),
                 "candidate_status": (candidate or {}).get("status", "missing"),
                 "secure_targets": (baseline or {}).get("primary_score", {}).get("targets"),
                 "candidate_targets": ((candidate or {}).get("primary_score") or {}).get("targets"),
                 "paired_delta": delta,
                 "paired_target_mask": [value is not None for value in delta]}
        if not known:
            entry["unknown_reason"] = ("initial_or_exogenous_binding" if not bound else
                                       "incomplete_or_unobserved_primary_window")
        if delta[0] is not None:
            new_failure += int(a[0] == 0 and b[0] == 1)
            reduced_failure += int(a[0] == 1 and b[0] == 0)
            partial_new_failure += int(not known and a[0] == 0 and b[0] == 1)
        if delta[1] is not None:
            duration_worse += int(delta[1] > 1e-6)
        deltas.append(delta)
        interventions += int((candidate or {}).get("command_counts", {}).get("intervention_steps", 0))
        rows.append(entry)
    counts = [sum(delta[j] is not None for delta in deltas) for j in range(len(TARGET_NAMES))]
    total = [sum(delta[j] for delta in deltas if delta[j] is not None)
             if counts[j] else None for j in range(len(TARGET_NAMES))]
    worst = [max(delta[j] for delta in deltas if delta[j] is not None)
             if counts[j] else None for j in range(len(TARGET_NAMES))]
    n = sum(row["paired_known"] for row in rows)
    def block(values):
        return [values[2 + 3*p:5 + 3*p] for p in range(4)]
    result = {"policy_id": policy_id, "registered_case_count": len(ids),
              "reference_policy_id": reference_policy_id,
              "matched_known_case_count": n,
              "unknown_or_unmatched_case_count": len(ids) - n,
              "known_target_case_counts": counts,
              "unknown_target_case_counts": [len(ids) - count for count in counts],
              "candidate_status_counts": dict(status_counts),
              "new_P0_failure_cases": new_failure,
              "confirmed_new_P0_failure_cases_on_partial_window": partial_new_failure,
              "reduced_P0_failure_cases": reduced_failure,
              "P0_failure_minutes_delta": total[1],
              "P0_duration_worse_cases": duration_worse,
              "worst_case_P0_duration_increase_minutes": worst[1],
              "ENS_kWh_delta_by_priority_carrier": block(total),
              "worst_case_ENS_kWh_increase_by_priority_carrier": block(worst),
              "mean_temperature_delta_C_by_P0_P1_P2": [total[j]/counts[j] if counts[j] else None
                                                           for j in (14, 15, 16)],
              "worst_case_temperature_increase_C_by_P0_P1_P2": worst[14:17],
              "secondary_GT_trip_delta": total[17],
              "worst_case_secondary_GT_trip_increase": worst[17],
              "actual_energy_fee_delta_USD": total[18],
              "intervention_steps": interventions,
              "unknowns_are_not_zero_filled": True,
              "metric_window_minutes": [60, 420]}
    return result, rows


def _f(value, digits=3):
    return "未定" if value is None else f"{value:+.{digits}f}"


def _all_nonworse(values, tolerance):
    return all(value is not None and value <= tolerance for value in values)


def _recommend(policies):
    """Predeclared conservative research preference, not a clinical guarantee."""
    eligible = []
    explanations = []
    for row in policies:
        if (row["unknown_or_unmatched_case_count"] != 0
                or row.get("known_target_case_counts") !=
                [row["registered_case_count"]] * len(TARGET_NAMES)):
            explanations.append("完整配对和全部目标可观测性不足")
            continue
        p0 = row["ENS_kWh_delta_by_priority_carrier"][0]
        worst = row["worst_case_ENS_kWh_increase_by_priority_carrier"]
        if (row["new_P0_failure_cases"] > 0
                or row["worst_case_P0_duration_increase_minutes"] > 1e-6
                or not _all_nonworse(worst[0], .01)
                or row["worst_case_temperature_increase_C_by_P0_P1_P2"][0] > .1):
            explanations.append("存在 P0 个案恶化")
            continue
        if (not _all_nonworse([v for part in worst[1:] for v in part], .01)
                or not _all_nonworse([v for part in row["ENS_kWh_delta_by_priority_carrier"][1:]
                                      for v in part], .01)
                or not _all_nonworse(row["worst_case_temperature_increase_C_by_P0_P1_P2"][1:], .1)
                or row["worst_case_secondary_GT_trip_increase"] > 0):
            explanations.append("存在 P1–P3 ENS、温度或机组保护事件恶化")
            continue
        if (row["reduced_P0_failure_cases"] > 0
                or row["P0_failure_minutes_delta"] < -1e-6
                or any(value < -.01 for value in p0)):
            eligible.append(row)
        else:
            explanations.append("未证明关键服务有超出研究分辨率的改善")
    if not eligible:
        why = "；".join(sorted(set(explanations))) if explanations else "尚无已评价候选"
        return "secure_milp", f"保守研究筛选未支持替换原 secure：{why}。"
    best = min(eligible, key=lambda r: (-r["reduced_P0_failure_cases"],
                                        r["P0_failure_minutes_delta"],
                                        sum(r["ENS_kWh_delta_by_priority_carrier"][0]),
                                        r["actual_energy_fee_delta_USD"]))
    return best["policy_id"], "该候选通过预列的关键服务保守筛选；仍需实测参数与院方评估。"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verified_closed_loop(closed_loop_dir):
    """Read the registered plan and latest actual attempts, rejecting mixed summaries."""
    from tools.reliability_first.closed_loop import _validated_plan

    plan, manifest = _validated_plan(closed_loop_dir / "registered_plan.json")
    summaries = sorted(closed_loop_dir.glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("No actual matched closed-loop summary")
    summary_path = summaries[-1]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("plan_signature") != plan["signature"]:
        raise ValueError("Summary/registered plan signature mismatch")
    actual = {(r["scenario_id"], r["policy_id"]): r for r in summary["results"]}
    if len(actual) != len(summary["results"]):
        raise ValueError("Duplicate reported scenario/policy pair")
    registered = {(sid, spec["policy_id"]) for sid in plan["scenario_ids"]
                  for spec in plan["policy_specs"]}
    if set(actual) - registered:
        raise ValueError("Summary contains unregistered scenario/policy")
    if (summary.get("registered_pair_count") != len(registered)
            or summary.get("terminal_pair_count") != sum(bool(r.get("terminal")) for r in actual.values())
            or summary.get("status_counts") != dict(Counter(r.get("status") for r in actual.values()))):
        raise ValueError("Summary counters do not match the recorded pair rows")
    for sid, pid in registered:
        pair = closed_loop_dir / "pairs" / digest([sid, pid])
        attempts = sorted(p for p in pair.glob("attempt_*") if p.is_dir())
        result = None
        for attempt in reversed(attempts):
            path = attempt / "result.json"
            if path.is_file():
                result = json.loads(path.read_text(encoding="utf-8"))
                if result.get("trajectory_path"):
                    raw = attempt / result["trajectory_path"]
                    if not raw.is_file() or _sha(raw) != result["trajectory_sha256"]:
                        raise ValueError(f"Closed-loop raw hash mismatch: {sid}/{pid}")
                break
        if result != actual.get((sid, pid)):
            raise ValueError(f"Summary does not match latest registered attempt: {sid}/{pid}")
    if len(actual) == len(registered) and all(r.get("terminal") for r in actual.values()):
        matched = all(len({actual[(sid, spec["policy_id"])]["binding"]["initial_state_sha256"]
                           for spec in plan["policy_specs"]}) == 1 and
                      len({actual[(sid, spec["policy_id"])]["binding"]["exogenous_stream_id"]
                           for spec in plan["policy_specs"]}) == 1 and
                      all(actual[(sid, spec["policy_id"])]["binding"]["passed"]
                          for spec in plan["policy_specs"])
                      for sid in plan["scenario_ids"])
    else:
        matched = None
    if summary.get("matched_initial_state_passed") != matched:
        raise ValueError("Summary matched pairing flag differs from actual results")
    return plan, manifest, summary, summary_path


def _branch_inventory(run_dir):
    """Count every frozen training snapshot, including never reached/attempted."""
    from tools.reliability_first.collect import _result_ok, registered_attempts

    design_path = run_dir / "design/branch_collection_v1.json"
    plan_path = run_dir / "design/snapshot_plan_v2.json"
    design = json.loads(design_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if _sha(plan_path) != design["plan_sha256"]:
        raise ValueError("Branch plan changed after registration")
    manifest_signature = json.loads((run_dir / "new_data/global_manifest.json")
                                    .read_text(encoding="utf-8"))["signature"]
    indices = design["early_indices"] + design["remaining_indices"]
    if len(indices) != len(set(indices)):
        raise ValueError("Duplicate registered branch snapshot")
    journal_path = run_dir / "branches/collection_attempts.jsonl"
    finished = defaultdict(list)
    started = Counter()
    if journal_path.is_file():
        for line in journal_path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("index") not in indices:
                continue
            if event.get("event") == "started":
                started[event["index"]] += 1
            elif event.get("event") == "finished":
                finished[event["index"]].append(event)
    counts = Counter()
    phase = defaultdict(Counter)
    strict = 0
    candidate_comparisons = Counter()
    label_state = Counter()
    execution_classes = Counter()
    candidate_count_total = future_count_total = 0
    counterexample = None
    entries = []
    for index in indices:
        row = plan[index]
        events = finished[index]
        selected = None
        if index == design["pilot_reuse"]["index"]:
            selected = run_dir / design["pilot_reuse"]["path"] / "BRANCH_RESULT.json"
        else:
            stage = "early" if index in design["early_indices"] else "remaining"
            attempts = registered_attempts(run_dir, index, stage)
            if len(attempts) > 1:
                raise ValueError(f"Duplicate successfully registered branch attempts: {index}")
            if attempts:
                selected = attempts[0] / "BRANCH_RESULT.json"
        status = ("unattempted" if started[index] == 0 else
                  "unfinished_collection_attempt" if len(events) < started[index] else
                  "failed_collection_attempt")
        branch = None
        if selected and selected.is_file():
            ok, branch = _result_ok(selected, row, design["branch_source_sha256"],
                                    manifest_signature=manifest_signature)
            if not ok:
                raise ValueError(f"Selected branch lacks verified provenance: {index}")
            if (branch.get("scenario_id") != row["scenario_id"]
                    or branch.get("snapshot_minute") != row["snapshot_minute"]):
                raise ValueError(f"Registered branch identity mismatch: {index}")
            status = branch.get("status", "unknown")
            if status == "complete":
                if (branch.get("schema") != "reliability_first.branch_result.v1"
                        or branch.get("group_id") != row["group_id"]
                        or branch.get("source_sha256") != design["branch_source_sha256"]
                        or branch.get("D0_passed") is not True):
                    raise ValueError(f"Complete branch missing frozen binding/D0: {index}")
                for name, expected in branch.get("raw_trajectory_hashes", {}).items():
                    raw = selected.parent / name
                    if not raw.is_file() or _sha(raw) != expected:
                        raise ValueError(f"Branch raw hash mismatch: {index}/{name}")
                if not branch.get("raw_trajectory_hashes"):
                    raise ValueError(f"Branch raw hashes absent: {index}")
                labels = branch.get("snapshot_labels") or {}
                state = ("currently_failed" if labels.get("current_failed_at_snapshot") else
                         "recovered_after_prior_failure" if labels.get("recovered_at_snapshot") else
                         "not_yet_failed" if labels.get("ever_failed_before_or_at_snapshot") is False
                         else "state_unknown")
                label_state[state] += 1
                if state == "not_yet_failed":
                    label_state["prevention_label_positive"] += int(
                        labels.get("future_first_failure_binary") == 1)
                    label_state["prevention_label_unknown"] += int(
                        labels.get("future_first_failure_binary") is None)
                if state == "recovered_after_prior_failure":
                    label_state["refailure_label_positive"] += int(
                        labels.get("post_recovery_refailure_binary") == 1)
                    label_state["refailure_label_unknown"] += int(
                        labels.get("post_recovery_refailure_binary") is None)
                candidate_count_total += int(branch.get("candidate_count", 0))
                future_count_total += int(branch.get("future_count", 0))
                strict += int(any(o.get("strict_opportunity_across_stress_set") is True
                                  for o in branch.get("opportunities", [])))
                for opportunity in branch.get("opportunities", []):
                    for category in opportunity.get("effective_execution_classes", []):
                        execution_classes[category] += 1
                    for future in opportunity.get("per_future", []):
                        candidate_comparisons[future.get("comparison", "unknown")] += 1
                        delta = future.get("delta") or {}
                        worsening = delta.get("P0_failure_minutes")
                        if worsening is not None and (counterexample is None or
                                                      worsening > counterexample["P0_failure_minutes_delta"]):
                            counterexample = {"index": index, "scenario_id": row["scenario_id"],
                                              "snapshot_minute": row["snapshot_minute"],
                                              "candidate_id": opportunity.get("candidate_id"),
                                              "future_id": future.get("future_id"),
                                              "P0_failure_minutes_delta": worsening,
                                              "P0_e_ENS_kWh_delta": delta.get("P0_e_ENS_kWh")}
            else:
                status += ("_verified_legacy" if branch.get("legacy_provenance_verified")
                           else "_with_native_provenance")
        counts[status] += 1
        phase[row["snapshot_phase_design"]][status] += 1
        entries.append({"index": index, "scenario_id": row["scenario_id"],
                        "phase": row["snapshot_phase_design"], "status": status,
                        "selected_result": (selected.relative_to(run_dir).as_posix()
                                            if selected and selected.is_file() else None)})
    return {"registered_count": len(indices), "status_counts": dict(counts),
            "collection_attempt_starts": sum(started.values()),
            "collection_attempt_finishes": sum(len(rows) for rows in finished.values()),
            "collection_attempts_without_finish": sum(started.values()) - sum(len(rows) for rows in finished.values()),
            "by_phase": {key: dict(value) for key, value in phase.items()},
            "strict_opportunity_snapshots": strict,
            "snapshot_label_state_counts": dict(label_state),
            "effective_execution_class_counts": dict(execution_classes),
            "candidate_count_sum_on_complete": candidate_count_total,
            "future_count_sum_on_complete": future_count_total,
            "candidate_future_comparisons": dict(candidate_comparisons),
            "largest_observed_P0_duration_counterexample": counterexample,
            "entries": entries}


def _action_coverage(closed_loop_dir, plan, summary):
    by_policy = defaultdict(lambda: {"decision_steps": 0, "intervention_steps": 0,
                                      "after_secure_first_P0_failure_steps": 0,
                                      "candidate_ids": Counter(), "decision_reasons": Counter(),
                                      "intervention_minutes": []})
    results = {(r["scenario_id"], r["policy_id"]): r for r in summary["results"]}
    for sid in plan["scenario_ids"]:
        baseline = results.get((sid, "secure_milp")) or {}
        onset = ((baseline.get("primary_score") or {})
                 .get("first_failure_classification_in_window_minute"))
        for spec in plan["policy_specs"]:
            pid = spec["policy_id"]
            result = results.get((sid, pid))
            if not result or not result.get("trajectory_path"):
                continue
            pair = closed_loop_dir / "pairs" / digest([sid, pid])
            attempts = sorted(p for p in pair.glob("attempt_*") if p.is_dir())
            path = next((attempt / result["trajectory_path"] for attempt in reversed(attempts)
                         if (attempt / "result.json").is_file() and
                         json.loads((attempt / "result.json").read_text(encoding="utf-8")) == result), None)
            if path is None:
                raise ValueError("Registered raw policy path missing")
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            current = by_policy[pid]
            for decision in payload.get("policy_diagnostics") or []:
                if not decision:
                    continue
                current["decision_steps"] += 1
                reason = (decision.get("gate_reason") or decision.get("fallback_reason") or
                          decision.get("strategy_status") or "unclassified")
                if isinstance(reason, str) and reason.startswith("{"):
                    try:
                        gate = json.loads(reason)
                        reason = gate.get("code", reason)
                    except json.JSONDecodeError:
                        pass
                current["decision_reasons"][reason] += 1
                if decision.get("selected_candidate_index") not in (None, 0):
                    current["intervention_steps"] += 1
                    current["candidate_ids"][decision.get("selected_candidate_id") or "unknown"] += 1
                    minute = decision.get("decision_time_min")
                    if minute is not None:
                        current["intervention_minutes"].append(float(minute))
                        current["after_secure_first_P0_failure_steps"] += int(
                            onset is not None and float(minute) >= float(onset))
    return {pid: {**value, "candidate_ids": dict(value["candidate_ids"]),
                  "decision_reasons": dict(value["decision_reasons"]),
                  "intervention_minute_range": ([min(value["intervention_minutes"]),
                                                 max(value["intervention_minutes"])]
                                                if value["intervention_minutes"] else None)}
            for pid, value in by_policy.items()}


def _soft_probe_inventory(run_dir, *, probe_source=None):
    """Verify the versioned training-only candidate probe before citing counts."""
    run_dir = Path(run_dir).resolve()
    summary_path = run_dir / "soft_objective_probe_v2/PROBE_SUMMARY.json"
    if not summary_path.is_file():
        return None
    record = json.loads(summary_path.read_text(encoding="utf-8"))
    if record.get("schema") != "reliability_first.soft_objective_probe_summary.v1":
        raise ValueError("Unknown soft-objective probe summary schema")
    source = Path(probe_source or Path(__file__).with_name("soft_objective_probe.py"))
    bound = {
        "probe_source_sha256": source,
        "design_sha256": run_dir / "design/soft_objective_probe_v2.json",
        "manifest_sha256": run_dir / "new_data/global_manifest.json",
        "snapshot_plan_sha256": run_dir / "design/snapshot_plan_v2.json",
        "branch_collection_design_sha256": run_dir / "design/branch_collection_v1.json",
    }
    for key, path in bound.items():
        if not path.is_file() or record.get("source_binding", {}).get(key) != _sha(path):
            raise ValueError(f"Soft-objective probe source binding mismatch: {key}")
    branch_source = json.loads(bound["branch_collection_design_sha256"].read_text(encoding="utf-8"))
    if record.get("source_binding", {}).get("branch_source_sha256") != branch_source.get("branch_source_sha256"):
        raise ValueError("Soft-objective probe original branch source binding mismatch")
    registered = record.get("registered_snapshots")
    completed = record.get("completed_snapshots")
    unknown = record.get("unknown_snapshots")
    not_attempted = record.get("not_attempted_snapshots")
    if not all(isinstance(n, int) and n >= 0 for n in
               (registered, completed, unknown, not_attempted)) or (
                   completed + unknown + not_attempted != registered):
        raise ValueError("Soft-objective probe snapshot denominators disagree")
    attempts = record.get("attempts") or {}
    started, finished = (attempts.get("new_continuations_started"),
                         attempts.get("new_continuations_finished"))
    if not all(isinstance(n, int) and n >= 0 for n in (started, finished)) or finished > started:
        raise ValueError("Soft-objective probe attempt counts disagree")
    hashes = record.get("raw_trajectory_hashes")
    if not isinstance(hashes, dict):
        raise ValueError("Soft-objective probe raw hash index missing")
    entries = record.get("results")
    if not isinstance(entries, list) or len(entries) != registered:
        raise ValueError("Soft-objective probe per-snapshot result register missing")
    entry_status = Counter()
    indices = set()
    result_hashes = {}
    for entry in entries:
        index, status = entry.get("index"), entry.get("status")
        if not isinstance(index, int) or index in indices:
            raise ValueError("Soft-objective probe duplicate/invalid snapshot index")
        indices.add(index)
        entry_status[status] += 1
        if status in {"not_attempted", "interrupted_no_terminal_receipt"}:
            continue
        name = entry.get("result_path")
        result_path = (run_dir / name).resolve() if isinstance(name, str) else run_dir
        if not result_path.is_relative_to(run_dir) or not result_path.is_file() or (
                _sha(result_path) != entry.get("result_sha256")):
            raise ValueError(f"Soft-objective probe result hash mismatch: {index}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if (result.get("schema") != "reliability_first.soft_objective_probe_result.v1"
                or result.get("index") != index or result.get("status") != status
                or result.get("source_binding") != record["source_binding"]):
            raise ValueError(f"Soft-objective probe result identity mismatch: {index}")
        for raw_name, expected in result.get("raw_trajectory_hashes", {}).items():
            if raw_name in result_hashes and result_hashes[raw_name] != expected:
                raise ValueError(f"Soft-objective probe conflicting raw hash: {raw_name}")
            result_hashes[raw_name] = expected
    if (entry_status["complete"] != completed or entry_status["unknown"] != unknown
            or entry_status["not_attempted"] != not_attempted
            or entry_status["interrupted_no_terminal_receipt"] !=
            record.get("interrupted_snapshots", 0) or result_hashes != hashes):
        raise ValueError("Soft-objective probe result/summary counts disagree")
    for name, expected in hashes.items():
        raw = (run_dir / name).resolve()
        if not raw.is_relative_to(run_dir) or not raw.is_file() or _sha(raw) != expected:
            raise ValueError(f"Soft-objective probe raw hash mismatch: {name}")
    return {**record, "verified_raw_count": len(hashes),
            "summary_path": summary_path.relative_to(run_dir).as_posix(),
            "summary_sha256": _sha(summary_path)}


def _identity_order_audit(run_dir, *, audit_source=None, probe_source=None):
    """Verify the read-only adjudication without changing the official unknowns."""
    run_dir = Path(run_dir).resolve()
    path = run_dir / "soft_objective_probe_v2/IDENTITY_ORDER_AUDIT_v1.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if (record.get("schema") != "reliability_first.soft_probe_identity_order_audit.v1"
            or record.get("status") != "complete"
            or record.get("derived_from_saved_raw_only") is not True
            or record.get("new_rollout_attempts") != 0):
        raise ValueError("Identity-order audit status or read-only scope mismatch")
    summary_path = run_dir / "soft_objective_probe_v2/PROBE_SUMMARY.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    binding = record.get("source_binding") or {}
    fixed_paths = {
        "audit_source_sha256": Path(audit_source or Path(__file__).with_name("soft_objective_probe_audit.py")),
        "probe_source_sha256": Path(probe_source or Path(__file__).with_name("soft_objective_probe.py")),
        "design_sha256": run_dir / "design/soft_objective_probe_v2.json",
        "probe_summary_sha256": summary_path,
    }
    for key, source in fixed_paths.items():
        if not source.is_file() or binding.get(key) != _sha(source):
            raise ValueError(f"Identity-order audit source binding mismatch: {key}")
    root = Path(__file__).resolve().parents[2]
    other_sources = {
        "window_targets_source_sha256": "tools/control_improvement/branches.py",
        "window_outcome_source_sha256": "tools/integrated_learning/truth.py",
        "diagnostics_source_sha256": "tools/reliability_first/diagnostics.py",
        "energy_fee_source_sha256": "tools/integrated_experiment/energy_metrics.py",
        "target_names_source_sha256": "tools/control_improvement/ranking.py",
        "physical_state_source_sha256": "src/hospital_ies/resilience.py",
        "initial_state_serialization_source_sha256": "src/hospital_ies/dataset_export.py",
        "energy_tariff_source_sha256": "src/hospital_ies/model.py",
    }
    for key, name in other_sources.items():
        if key in binding and binding[key] != _sha(root / name):
            raise ValueError(f"Identity-order audit source binding mismatch: {key}")
    preserved = {key: summary.get(key) for key in ("completed_snapshots", "unknown_snapshots")}
    if record.get("official_v2_status_preserved") != preserved:
        raise ValueError("Identity-order audit changed official probe-v2 statuses")
    unknowns = {entry["index"]: entry for entry in summary.get("results", [])
                if entry.get("status") == "unknown"}
    indices = record.get("unknown_snapshot_indices_examined")
    if (not isinstance(indices, list) or len(indices) != len(set(indices))
            or set(indices) != set(unknowns)
            or set(record.get("affected_snapshot_indices", [])) != set(indices)):
        raise ValueError("Identity-order audit unknown index register mismatch")
    result_hashes = binding.get("result_sha256_by_index") or {}
    plan_hashes = binding.get("attempt_plan_sha256_by_index") or {}
    source_result_hashes = binding.get("source_result_sha256_by_index") or {}
    if any(set(table) != {str(index) for index in indices}
           for table in (result_hashes, plan_hashes, source_result_hashes)):
        raise ValueError("Identity-order audit per-index binding incomplete")
    for index in indices:
        entry = unknowns[index]
        result = (run_dir / entry["result_path"]).resolve()
        plan = run_dir / f"soft_objective_probe_v2/snapshot_{index:03d}/attempt_0001/plan.json"
        if (not result.is_relative_to(run_dir) or not result.is_file()
                or _sha(result) != entry.get("result_sha256")
                or result_hashes[str(index)] != _sha(result)
                or not plan.is_file() or plan_hashes[str(index)] != _sha(plan)
                or source_result_hashes[str(index)] != entry.get("source_result_sha256")):
            raise ValueError(f"Identity-order audit source result/plan mismatch: {index}")
    hashes = record.get("raw_trajectory_hashes")
    new_count = record.get("verified_new_raw_count")
    secure_count = record.get("verified_secure_raw_count")
    if (not isinstance(hashes, dict) or not all(isinstance(n, int) and n >= 0
            for n in (new_count, secure_count)) or len(hashes) != new_count + secure_count):
        raise ValueError("Identity-order audit raw register/count mismatch")
    for name, expected in hashes.items():
        raw = (run_dir / name).resolve()
        if not raw.is_relative_to(run_dir) or not raw.is_file() or _sha(raw) != expected:
            raise ValueError(f"Identity-order audit raw hash mismatch: {name}")
    if (record.get("unresolved_pair_count") != 0
            or sum(sum(counts.get(label, 0) for label in ("better", "tie", "worse", "unknown"))
                   for counts in record.get("per_candidate", {}).values()) !=
            record.get("order_only_verified_pair_count")):
        raise ValueError("Identity-order audit pair classification mismatch")
    return {**record, "verified_raw_count": len(hashes),
            "audit_path": path.relative_to(run_dir).as_posix(), "audit_sha256": _sha(path)}


def _first_probe_failure(run_dir):
    """Keep the superseded v1 smoke/error and its exact old source visible."""
    run_dir = Path(run_dir).resolve()
    path = run_dir / "soft_objective_probe/PROBE_SUMMARY.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    source = run_dir / "soft_objective_probe/SOURCE_SNAPSHOT.py"
    expected = (record.get("source_binding") or {}).get("probe_source_sha256")
    if not source.is_file() or _sha(source) != expected:
        raise ValueError("Superseded v1 probe old source snapshot SHA mismatch")
    failures = []
    for entry in record.get("results", []):
        name = entry.get("result_path")
        if not name:
            continue
        result_path = (run_dir / name).resolve()
        if not result_path.is_relative_to(run_dir) or not result_path.is_file() or (
                _sha(result_path) != entry.get("result_sha256")):
            raise ValueError("Superseded v1 probe result SHA mismatch")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("source_binding") != record.get("source_binding"):
            raise ValueError("Superseded v1 probe source binding mismatch")
        if result.get("status") != "complete":
            failures.append({"index": entry.get("index"), "status": result.get("status"),
                             "errors": [{"type": error.get("type"), "message": error.get("message")}
                                        for error in result.get("errors", [])]})
    return {"summary_path": path.relative_to(run_dir).as_posix(),
            "summary_sha256": _sha(path), "source_sha256": expected,
            "registered_snapshots": record.get("registered_snapshots"),
            "unknown_snapshots": record.get("unknown_snapshots"),
            "new_continuations_started": (record.get("attempts") or {}).get("new_continuations_started"),
            "failures": failures}


def render_report(run_dir: Path, closed_loop_dir: Path, output_path: Path) -> dict:
    run_dir = Path(run_dir).resolve()
    closed_loop_dir = Path(closed_loop_dir).resolve()
    plan, manifest, summary, summary_path = _verified_closed_loop(closed_loop_dir)
    if _sha(run_dir / "new_data/global_manifest.json") != plan["manifest_sha256"]:
        raise ValueError("Report run manifest differs from the closed-loop registration")
    conditions = {s["id"]: s["condition_id"] for s in manifest["scenarios"]}
    policy_ids = [s["policy_id"] for s in plan["policy_specs"] if s["policy_id"] != "secure_milp"]
    stats = []
    all_rows = []
    for pid in policy_ids:
        item, cases = paired_policy_summary(plan, summary, pid)
        stats.append(item)
        all_rows.extend(cases)
    rule_rows = []
    if "secure_current_rule" in policy_ids:
        for pid in policy_ids:
            if pid != "secure_current_rule":
                item, _ = paired_policy_summary(plan, summary, pid,
                                                reference_policy_id="secure_current_rule")
                rule_rows.append(item)
    by_condition = []
    for condition in sorted({conditions[sid] for sid in plan["scenario_ids"]}):
        subset = [sid for sid in plan["scenario_ids"] if conditions[sid] == condition]
        for pid in policy_ids:
            item, _ = paired_policy_summary(plan, summary, pid, scenario_ids=subset)
            by_condition.append({"condition_id": condition, **item})
    recommendation, reason = _recommend(stats)
    complete = (summary.get("matched_initial_state_passed") is True
                and summary.get("terminal_pair_count") == plan["maximum_trajectory_attempts"]
                and len(plan["scenario_ids"]) == 60)
    branch_inventory = _branch_inventory(run_dir)
    soft_probe = _soft_probe_inventory(run_dir)
    identity_order_audit = _identity_order_audit(run_dir)
    if soft_probe is not None and identity_order_audit is None:
        raise ValueError("Probe-v2 identity-order sidecar missing from final report evidence")
    first_probe = _first_probe_failure(run_dir)
    action_coverage = _action_coverage(closed_loop_dir, plan, summary)
    learning = []
    for fit in sorted((run_dir / "learning").glob("*_fit*/FIT_REPORT.json")):
        record = json.loads(fit.read_text(encoding="utf-8"))
        learning.append({"path": fit.relative_to(run_dir).as_posix(),
                         "actual_fits": record.get("actual_fits"),
                         "device": record.get("actual_train_device", record.get("training_device")),
                         "train_rows": record.get("train_rows"),
                         "validation_rows": record.get("validation_rows"),
                         "optimizer_updates": record.get("optimizer_updates"),
                         "initial_weight_sha256": record.get("initial_weight_sha256"),
                         "trained_weight_sha256": record.get("trained_weight_sha256"),
                         "first_epoch": (record.get("epoch_losses") or [None])[0],
                         "last_epoch": (record.get("epoch_losses") or [None])[-1],
                         "peak_cuda_allocated_bytes": record.get("peak_cuda_allocated_bytes"),
                         "elapsed_seconds": record.get("elapsed_seconds")})
    from tools.reliability_first.audit import audit_baseline_attempts
    baseline_audit = audit_baseline_attempts(
        manifest, sorted((run_dir / "new_data/shards").glob("shard_*/attempts.jsonl")))
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    def link(path):
        return Path(os.path.relpath(path, output_path.parent)).as_posix()
    def service(values):
        return " / ".join(_f(value) for value in values)
    def table(rows):
        out = ["| 候选减参照 | 完整/登记 | 新增/减少 P0 | P0 分钟 | P0 电/热/冷 ENS kWh | P1–P3 电/热/冷 ENS kWh | P0/P1/P2 最大温度最坏增加 °C | 实际费用 USD | 部分窗口确认新增 P0 |",
               "| --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: |"]
        for item in rows:
            ens = item["ENS_kWh_delta_by_priority_carrier"]
            temp = item["worst_case_temperature_increase_C_by_P0_P1_P2"]
            lower = "; ".join(f"P{p}: {service(ens[p])}" for p in (1, 2, 3))
            out.append(f"| {item['policy_id']} | {item['matched_known_case_count']}/{item['registered_case_count']} | "
                       f"+{item['new_P0_failure_cases']}/-{item['reduced_P0_failure_cases']} | "
                       f"{_f(item['P0_failure_minutes_delta'])} | {service(ens[0])} | "
                       f"{lower} | {service(temp)} | {_f(item['actual_energy_fee_delta_USD'])} | "
                       f"{item['confirmed_new_P0_failure_cases_on_partial_window']} |")
        return out
    status_counts = summary.get("status_counts") or {}
    no_result_count = plan["maximum_trajectory_attempts"] - len(summary["results"])
    lines = ["# 医院 IES v4 可靠性优先连续研发报告", "",
             f"证据状态：{'60 场景均有终态记录的开发配对，未完整的物理结果仍按未知计' if complete else '开发配对未完成，仅报告已运行证据'}。原最终 12 背景未解封。",
             "", "## 相对原 secure 的关键服务与费用", "",
             "数值为同初态、同外生流的候选减参照，窗口 60–420 分钟。分目标只汇总双方已观测的配对；未知不填零。温度栏是逐例最大增量中的最坏值。",
             ""] + table(stats)
    if rule_rows:
        lines += ["", "## 相对匹配非学习规则 secure_current_rule", ""] + table(rule_rows)
    lines += ["", f"研究推荐：**{recommendation}**。{reason}",
              "该筛选在研究分辨率内拒绝 P0 个案恶化及 P1–P3 服务/温度/保护事件恶化；费用仅作最后参考。不是临床效益、现场可靠性概率或许可证明。", "",
              "## 完整分母、未知与有限动作机会", "",
              f"新规格两原基线登记 {baseline_audit['registered_policy_runs']} 条，启动 {baseline_audit['started_attempts']} 次、"
              f"终态 {baseline_audit['finished_attempts']} 次、未终止 {baseline_audit['unfinished_attempts']} 次；"
              f"结果状态 {baseline_audit['statuses']}。模型域终止、其他中断与未达分别保留在原始日志；"
              "不能把每次模型不完整直接等同于已证明的约束矛盾。",
              f"开发登记 {plan['maximum_trajectory_attempts']} 条，终态 {summary.get('terminal_pair_count')} 条、"
              f"尚无结果 {no_result_count} 条，实际状态 {status_counts}；初态和外生流配对核验 {summary.get('matched_initial_state_passed')}。",
              f"训练侧快照登记 {branch_inventory['registered_count']} 条，状态 {branch_inventory['status_counts']}；"
              f"全部具名压力未来中至少一个候选严格改善的快照 {branch_inventory['strict_opportunity_snapshots']} 条。"
              f"逐未来结果 {branch_inventory['candidate_future_comparisons']}。",
              f"采集 wrapper 启动/记载终态 {branch_inventory['collection_attempt_starts']}/"
              f"{branch_inventory['collection_attempt_finishes']}，未记载终态 "
              f"{branch_inventory['collection_attempts_without_finish']}；失败重试不抵消旧尝试。",
              f"快照前缀状态/标签 {branch_inventory['snapshot_label_state_counts']}；"
              f"有效执行类别 {branch_inventory['effective_execution_class_counts']}。"
              "已失效/已恢复状态不将历史首次事件当成可由未来动作取消的标签。"]
    counterexample = branch_inventory["largest_observed_P0_duration_counterexample"]
    if counterexample and counterexample["P0_failure_minutes_delta"] > 0:
        lines += [f"已见严重候选反例：快照 {counterexample['index']} 在 {counterexample['snapshot_minute']} 分钟，"
                  f"{counterexample['candidate_id']} / {counterexample['future_id']} 相对 secure 增加"
                  f" {counterexample['P0_failure_minutes_delta']:.3f} 分钟 P0 失效、"
                  f"{_f(counterexample['P0_e_ENS_kWh_delta'])} kWh P0 电缺供。"]
    if soft_probe is not None:
        attempts = soft_probe["attempts"]
        lines += ["", "## 第二轮训练侧合法候选试验", "",
                  f"冻结登记 {soft_probe['registered_snapshots']} 个训练快照，完成/未知/未尝试 "
                  f"{soft_probe['completed_snapshots']}/{soft_probe['unknown_snapshots']}/"
                  f"{soft_probe['not_attempted_snapshots']}；新增续演启动/终态 "
                  f"{attempts['new_continuations_started']}/{attempts['new_continuations_finished']}。"
                  f"原始轨迹 SHA 核对 {soft_probe['verified_raw_count']} 份。",
                  f"逐候选比较 {soft_probe.get('per_candidate', {})}；逐具名未来 "
                  f"{soft_probe.get('per_future', {})}。已确认的跨压力集合严格机会 "
                  f"{soft_probe.get('strict_opportunity_candidate_count')} 个候选/"
                  f"{soft_probe.get('strict_opportunity_snapshot_count')} 个快照。",
                  "这里比较的是训练侧单次改变后 secure 续行，未知与失败仍在分母中；"
                  "其权重未进入下述 v1 神经/树闭环训练，也不能替代开发场景完整闭环或证明可上线收益。",
                  f"原始摘要：`{link(run_dir / soft_probe['summary_path'])}`。"]
    if identity_order_audit is not None:
        counts = identity_order_audit["official_v2_status_preserved"]
        lines += [f"对 v2 身份序列异常所作的独立只读旁路审计：原始完成/未知仍为 "
                  f"{counts['completed_snapshots']}/{counts['unknown_snapshots']}，没有新增续演；"
                  f"受影响的 {len(identity_order_audit['affected_snapshot_indices'])} 个快照中，"
                  f"{identity_order_audit['order_only_verified_pair_count']} 个候选-未来配对只在"
                  f"`extra_faulted` 集合导出顺序上不同，未解决配对 "
                  f"{identity_order_audit['unresolved_pair_count']}。",
                  f"旁路核验 {identity_order_audit['verified_new_raw_count']} 条新候选和 "
                  f"{identity_order_audit['verified_secure_raw_count']} 条 secure 原始轨迹，"
                  f"逐候选结果 {identity_order_audit['per_candidate']}，严格机会快照 "
                  f"{identity_order_audit['strict_opportunity_snapshot_count']}。"
                  "这是已导出状态和保存轨迹的事后训练侧核对，不是内存隐藏状态的独立证明；"
                  "官方 unknown 不据此改写。",
                  f"只读审计：`{link(run_dir / identity_order_audit['audit_path'])}`。"]
    if first_probe is not None:
        lines += [f"保留的前一版探针诊断：未知快照 {first_probe['unknown_snapshots']}/"
                  f"{first_probe['registered_snapshots']}、新续演启动 {first_probe['new_continuations_started']}；"
                  f"异常 {first_probe['failures']}。旧源码与失败结果按 SHA 核验，"
                  f"原记录：`{link(run_dir / first_probe['summary_path'])}`。"]
    lines += ["训练侧具名压力集合没有实证概率；事后有限候选最优不能用于在线选动作。", "",
              "## 实际训练和闭环动作覆盖", ""]
    for item in learning:
        lines.append(f"- `{item['path']}`：{item['device']}，训练/验证 {item['train_rows']}/{item['validation_rows']} 行，"
                     f"实际 fit {item['actual_fits']}，优化步 {item['optimizer_updates']}，峰值显存 {item['peak_cuda_allocated_bytes']} 字节。")
        if item["device"] == "cuda:0":
            start = item["first_epoch"] or {}
            end = item["last_epoch"] or {}
            lines.append(f"  权重 SHA 起/终 `{item['initial_weight_sha256']}` / `{item['trained_weight_sha256']}`；"
                         f"掩码验证损失 {start.get('validation_masked_mse')} → {end.get('validation_masked_mse')}。")
    if not learning:
        lines.append("- 尚无完成的训练证据。")
    classes = sorted({"neural_cuda" if item["device"] == "cuda:0" else "non_neural_tree"
                      for item in learning})
    lines.append(f"实际学习配方类 {classes}，fit 实例总数 {sum(int(item['actual_fits'] or 0) for item in learning)}；"
                 "原 secure 和具名非学习规则作为闭环对照，均不计学习 fit。")
    lines += [""]
    for pid, coverage in action_coverage.items():
        lines.append(f"- `{pid}`：{coverage['intervention_steps']}/{coverage['decision_steps']} 决策步偏离 secure；"
                     f"其中 {coverage['after_secure_first_P0_failure_steps']} 步在原 secure 首次 P0 失效后；"
                     f"动作 {coverage['candidate_ids']}；决定/拒绝原因 {coverage['decision_reasons']}。")
    lines += ["单步候选真值只评价一次改变后 secure 续行；重复干预的效益或恶化须以上述完整闭环为准。",
              "", "## 条件分层与复查入口", "",
              "开发 condition_id 分层只作描述：每个新背景对应一个配置，不能解释成同一背景的单参数因果敏感性。"
              "有配对的策略在每一开发场景共享相同配置。",
              f"逐例 19 项目标与未知：`PAIRED_CASES.json`；条件分层：`SENSITIVITY_BY_CONDITION.json`；"
              f"动作覆盖：`ACTION_COVERAGE.json`；快照状态：`BRANCH_INVENTORY.json`。",
              f"原始闭环轨迹：`{link(closed_loop_dir / 'pairs')}`；分支原始续行：`{link(run_dir / 'branches')}`；"
              f"模型与学习输入：`{link(run_dir / 'learning')}`。",
              "物理能力、P0–P3 判据、命令队列和六动作权限未改。当前理想观测、保护和设备参数仍需外部校准。", ""]
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    for name, value in (("PAIRED_SUMMARY.json", stats),
                        ("PAIRED_VS_RULE.json", rule_rows),
                        ("PAIRED_CASES.json", all_rows),
                        ("SENSITIVITY_BY_CONDITION.json", {
                            "interpretation": "condition_id strata, not same-background one-factor causal sensitivity",
                            "matched_within_scenario": True, "rows": by_condition}),
                        ("BRANCH_INVENTORY.json", branch_inventory),
                        ("ACTION_COVERAGE.json", action_coverage),
                        *(([("SOFT_OBJECTIVE_PROBE_AUDIT.json", soft_probe)] if soft_probe is not None else [])),
                        *(([("IDENTITY_ORDER_AUDIT_VERIFIED.json", identity_order_audit)]
                           if identity_order_audit is not None else [])),
                        *(([("FIRST_PROBE_FAILURE.json", first_probe)] if first_probe is not None else [])),
                        ("REPORT_PROVENANCE.json", {"development_plan_signature": plan["signature"],
                                                    "manifest_sha256": plan["manifest_sha256"],
                                                    "summary_path": link(summary_path),
                                                    "summary_sha256": _sha(summary_path),
                                                    "registered_plan_path": link(closed_loop_dir / "registered_plan.json"),
                                                    "registered_plan_sha256": _sha(closed_loop_dir / "registered_plan.json"),
                                                    "probe_identity_order_audit_sha256": (
                                                        identity_order_audit["audit_sha256"]
                                                        if identity_order_audit is not None else None),
                                                    "complete_60_case_development": complete,
                                                    "recommendation": recommendation})):
        with (output_path.parent / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return {"complete": complete, "recommendation": recommendation,
            "policies": policy_ids, "matched_initial_state_passed": summary.get("matched_initial_state_passed")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("closed_loop_dir", type=Path)
    parser.add_argument("output_path", type=Path)
    args = parser.parse_args()
    print(json.dumps(render_report(args.run_dir, args.closed_loop_dir, args.output_path),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
