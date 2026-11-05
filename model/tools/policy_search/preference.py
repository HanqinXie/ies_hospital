"""Preregistered, unit-preserving priority comparisons for complete policies."""
from __future__ import annotations

from collections import defaultdict
import math

from tools.control_improvement.ranking import TARGET_NAMES


def _finite_pair(a, b):
    return a is not None and b is not None and math.isfinite(float(a)) and math.isfinite(float(b))


def paired_metrics(rows, candidate_id, *, baseline_id="secure_milp", score_field="primary_score"):
    """Compute exact common-case effects; preserve all missing components.

    Only complete window pairs enter search aggregates. Per-target observed
    counts and individual null deltas remain visible, including truncated
    prefixes whose observed P0 event may nevertheless be known.
    """
    by = defaultdict(dict)
    for row in rows:
        sid, pid = row["scenario_id"], row["policy_id"]
        if pid in by[sid]:
            raise ValueError("Duplicate scenario/policy result")
        by[sid][pid] = row
    cases = {}
    full_deltas = []
    unknown = []
    observed_by_target = [0] * len(TARGET_NAMES)
    for sid in sorted(by):
        ref = by[sid].get(baseline_id)
        cand = by[sid].get(candidate_id)
        a = None if cand is None else cand.get(score_field, {}).get("targets")
        b = None if ref is None else ref.get(score_field, {}).get("targets")
        if a is not None and len(a) != len(TARGET_NAMES):
            raise ValueError("Candidate target schema differs")
        if b is not None and len(b) != len(TARGET_NAMES):
            raise ValueError("Baseline target schema differs")
        delta = [(float(x) - float(y)) if _finite_pair(x, y) else None
                 for x, y in zip(a or [None] * len(TARGET_NAMES), b or [None] * len(TARGET_NAMES))]
        observed_by_target = [n + int(v is not None) for n, v in zip(observed_by_target, delta)]
        complete = bool(cand and ref and cand.get(score_field, {}).get("full_horizon")
                        and ref.get(score_field, {}).get("full_horizon")
                        and all(v is not None for v in delta))
        if complete:
            full_deltas.append(delta)
        else:
            unknown.append(sid)
        cases[sid] = {"candidate_status": None if cand is None else cand.get("status"),
                      "baseline_status": None if ref is None else ref.get("status"),
                      "complete_paired_window": complete, "delta_targets": delta,
                      "candidate_targets": a, "baseline_targets": b}
    metric = {"schema": "policy_search.paired_priority.v1", "candidate_id": candidate_id,
              "baseline_id": baseline_id, "score_field": score_field,
              "registered_case_count": len(cases), "complete_paired_case_count": len(full_deltas),
              "unknown_case_ids": unknown, "target_names": list(TARGET_NAMES),
              "observable_paired_count_by_target": dict(zip(TARGET_NAMES, observed_by_target)),
              "individual_cases": cases, "clinical_exchange_rate": None}
    if not full_deltas:
        metric.update({key: None for key in (
            "new_P0_failure_cases", "resolved_P0_failure_cases", "P0_failure_minutes_total_delta",
            "largest_case_P0_failure_minutes_increase", "P0_e_ENS_kWh_delta",
            "P0_h_ENS_kWh_delta", "P0_c_ENS_kWh_delta",
            "largest_case_P0_e_ENS_increase", "largest_case_P0_h_ENS_increase",
            "largest_case_P0_c_ENS_increase")})
        metric["total_delta_by_target"] = [None] * len(TARGET_NAMES)
        return metric
    new = sum(cases[sid]["baseline_targets"][0] == 0 and
              cases[sid]["candidate_targets"][0] == 1 for sid in cases
              if cases[sid]["complete_paired_window"])
    resolved = sum(cases[sid]["baseline_targets"][0] == 1 and
                   cases[sid]["candidate_targets"][0] == 0 for sid in cases
                   if cases[sid]["complete_paired_window"])
    totals = [sum(d[j] for d in full_deltas) for j in range(len(TARGET_NAMES))]
    metric.update(new_P0_failure_cases=int(new), resolved_P0_failure_cases=int(resolved),
                  P0_failure_minutes_total_delta=totals[1],
                  largest_case_P0_failure_minutes_increase=max(0.0, *(d[1] for d in full_deltas)),
                  P0_e_ENS_kWh_delta=totals[2], P0_h_ENS_kWh_delta=totals[3],
                  P0_c_ENS_kWh_delta=totals[4],
                  largest_case_P0_e_ENS_increase=max(0.0, *(d[2] for d in full_deltas)),
                  largest_case_P0_h_ENS_increase=max(0.0, *(d[3] for d in full_deltas)),
                  largest_case_P0_c_ENS_increase=max(0.0, *(d[4] for d in full_deltas)),
                  total_delta_by_target=totals)
    return metric


def search_key(metric):
    """Procedural simulation-allocation order; not a clinical utility score."""
    if metric["complete_paired_case_count"] == 0:
        return (math.inf,)
    return (len(metric["unknown_case_ids"]), metric["new_P0_failure_cases"],
            metric["largest_case_P0_failure_minutes_increase"],
            metric["largest_case_P0_e_ENS_increase"],
            metric["largest_case_P0_h_ENS_increase"],
            metric["largest_case_P0_c_ENS_increase"],
            metric["P0_failure_minutes_total_delta"],
            metric["P0_e_ENS_kWh_delta"], metric["P0_h_ENS_kWh_delta"],
            metric["P0_c_ENS_kWh_delta"], -metric["resolved_P0_failure_cases"])


def p0_dominates(a, b):
    """Strict Pareto dominance of P0 effects on identical complete case support."""
    if a["unknown_case_ids"] != b["unknown_case_ids"] or not a["complete_paired_case_count"]:
        return False
    keys = ("new_P0_failure_cases", "largest_case_P0_failure_minutes_increase",
            "largest_case_P0_e_ENS_increase", "largest_case_P0_h_ENS_increase",
            "largest_case_P0_c_ENS_increase", "P0_failure_minutes_total_delta",
            "P0_e_ENS_kWh_delta", "P0_h_ENS_kWh_delta", "P0_c_ENS_kWh_delta")
    left = [a[k] for k in keys]
    right = [b[k] for k in keys]
    return all(x <= y for x, y in zip(left, right)) and any(x < y for x, y in zip(left, right))
