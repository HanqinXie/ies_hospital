"""Audit-bound, read-only comparison of registered 0–600-minute policy outcomes.

The analysis never reruns a controller. Incomplete trajectories retain their
observed target components and null unknowns, but never enter full-window
totals. P0 comparison is limited to the complete cases common to *all*
registered policies; cost has no exchange rate against P0 harm.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from tools.policy_search.preference import paired_metrics
from tools.policy_search.preference_v2 import _verified_inputs, p0_dominates_v2


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "policy_search.audited_full_window.v1"
BATCH_PATHS = {"generation_1": Path("search/generation_1"),
               "generation_2": Path("search/generation_2"),
               "alternative_modes": Path("search/alternative_modes"),
               "selection": Path("selection"),
               "calibration": Path("calibration"),
               "development": Path("development")}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_batch(location, batch=None):
    """Resolve either an exact registered batch directory or run plus batch."""
    path = Path(location)
    if (path / "registered_plan.json").is_file():
        folder = path
        run = folder.parent.parent if folder.parent.name == "search" else folder.parent
        if batch is not None and BATCH_PATHS.get(batch) != folder.relative_to(run):
            raise ValueError("batch does not match registered batch directory")
    else:
        if batch is None:
            raise ValueError("batch is required when location is a run directory")
        if batch not in BATCH_PATHS:
            raise ValueError("Unknown registered batch")
        run = path
        folder = run / BATCH_PATHS[batch]
    if not (folder / "registered_plan.json").is_file():
        raise FileNotFoundError("Registered batch plan is missing")
    return run, folder


def _complete_metric(rows, policy_id):
    metric = paired_metrics(rows, policy_id, score_field="full_observation")
    complete = sorted(sid for sid, case in metric["individual_cases"].items()
                      if case["complete_paired_window"])
    case_temperatures = {sid: metric["individual_cases"][sid]["delta_targets"][14]
                         for sid in complete}
    worst = max(case_temperatures.values()) if case_temperatures else None
    metric.update(complete_paired_case_ids=complete,
                  case_P0_max_temperature_C_delta=case_temperatures,
                  largest_case_P0_temperature_delta_C=worst,
                  largest_case_P0_temperature_increase_C=(max(0.0, worst)
                                                          if worst is not None else None))
    return metric


def _validate_full_scores(plan, summary, audit):
    if plan.get("score_windows", {}).get("full") != [0.0, 600.0]:
        raise ValueError("Registered full window must be 0–600 minutes")
    ids = list(plan.get("scenario_ids", []))
    policies = [spec["policy_id"] for spec in plan.get("policy_specs", [])]
    if (not ids or not policies or len(ids) != len(set(ids)) or
            len(policies) != len(set(policies)) or "secure_milp" not in policies):
        raise ValueError("Registered scenario/policy set is invalid")
    expected = {(sid, pid) for sid in ids for pid in policies}
    rows = summary.get("results", [])
    actual = [(row.get("scenario_id"), row.get("policy_id")) for row in rows]
    if len(actual) != len(expected) or len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError("Registered result row set differs")
    unknown = []
    for row in rows:
        score = row.get("full_observation")
        if not isinstance(score, dict):
            raise ValueError("Missing full_observation score")
        targets, mask, full = (score.get("targets"), score.get("target_mask"),
                               score.get("full_horizon"))
        if (not isinstance(targets, list) or len(targets) != 19 or
                not isinstance(mask, list) or len(mask) != 19 or
                any(type(value) is not bool for value in mask) or
                mask != [value is not None for value in targets] or
                type(full) is not bool):
            raise ValueError("full_observation target mask differs")
        if any(value is not None and
               (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value)) for value in targets):
            raise ValueError("full_observation target value invalid")
        if full != all(mask):
            raise ValueError("full_observation full-window mask differs")
        if not full:
            if any(value is not None for value in targets[1:]) or targets[0] not in (None, 1, 1.0):
                raise ValueError("full_observation unknown targets differ")
            unknown.append(f"{row['scenario_id']}/{row['policy_id']}")
    if sorted(unknown) != audit.get("unknown_full_pair_ids"):
        raise ValueError("unknown_full_pair_ids differs from audit receipt")
    return ids, policies, rows, sorted(unknown)


def compare_full_window(plan, summary, audit):
    """Build full-window metrics from inputs already verified against raw audit."""
    ids, policies, rows, unknown_pairs = _validate_full_scores(plan, summary, audit)
    metrics = {pid: _complete_metric(rows, pid) for pid in policies}
    complete_by_policy = {pid: metrics[pid]["complete_paired_case_ids"] for pid in policies}
    common = sorted(set(ids).intersection(*(set(values) for values in complete_by_policy.values())))
    common_set = set(common)
    common_rows = [row for row in rows if row["scenario_id"] in common_set]
    common_metrics = {pid: _complete_metric(common_rows, pid) for pid in policies}
    candidates = [pid for pid in policies if pid != "secure_milp"]
    front = ([pid for pid in candidates if not any(
        p0_dominates_v2(common_metrics[other], common_metrics[pid])
        for other in candidates if other != pid)] if common else [])
    secure = common_metrics["secure_milp"]
    secure_relations = {pid: {
        "candidate_P0_dominates_secure": p0_dominates_v2(common_metrics[pid], secure),
        "secure_P0_dominates_candidate": p0_dominates_v2(secure, common_metrics[pid])}
        for pid in candidates}
    return {"schema": SCHEMA, "score_field": "full_observation",
            "window_start_minute": 0.0, "window_end_minute": 600.0,
            "registered_case_ids": ids,
            "registered_case_count": len(ids),
            "policy_ids": policies,
            "audited_unknown_full_pair_ids": unknown_pairs,
            "complete_paired_case_ids_by_policy": complete_by_policy,
            "all_policy_common_complete_case_ids": common,
            "all_policy_common_unknown_case_ids": sorted(set(ids) - common_set),
            "all_policy_common_complete_case_count": len(common),
            "metrics": metrics,
            "per_policy_metrics_may_have_different_complete_support": True,
            "metrics_on_all_policy_common_cases": common_metrics,
            "p0_pareto_ids_on_all_policy_common_cases": front,
            "p0_relation_to_secure_on_all_policy_common_cases": secure_relations,
            "full_window_claim_requires_all_policy_common_complete_cases": True,
            "incomplete_target_components_remain_null": True,
            "clinical_exchange_rate": None,
            "cost_cannot_offset_P0": True}


def analyze_full_window(folder, run):
    """Verify exact audited bytes, then compare without writing or simulating."""
    folder, run = Path(folder), Path(run)
    summaries = sorted((folder / "results").glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("No batch summary exists")
    plan_path = folder / "registered_plan.json"
    summary_path = summaries[-1]
    audit_path = folder / "AUDIT.json"
    registration_path = folder / "SEARCH_REGISTRATION.json"
    revision_path = run / "design/PREFERENCE_V2_SOURCE_FREEZE.json"
    freeze_path = run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"
    plan, summary, audit = _verified_inputs(
        plan_path, summary_path, audit_path, revision_path, freeze_path)
    if audit.get("registration_sha256") != _sha(registration_path):
        raise ValueError("SEARCH_REGISTRATION bytes differ from audit receipt")
    if (audit.get("manifest_sha256") != plan.get("manifest_sha256") or
            audit["manifest_sha256"] != _sha(plan["manifest_path"])):
        raise ValueError("Manifest bytes differ from audit receipt")
    if (audit.get("preference_sha256") != plan.get("preference_sha256") or
            audit["preference_sha256"] != _sha(plan["preference_path"])):
        raise ValueError("Research preference bytes differ from audit receipt")
    if audit.get("final_blind_opened") is not False:
        raise ValueError("Final blind seal differs from audit receipt")
    output = compare_full_window(plan, summary, audit)
    output.update(plan_signature=plan["signature"], plan_sha256=_sha(plan_path),
                  summary_sha256=_sha(summary_path), audit_sha256=_sha(audit_path),
                  registration_sha256=_sha(registration_path),
                  manifest_sha256=_sha(plan["manifest_path"]),
                  research_preference_sha256=_sha(plan["preference_path"]),
                  v2_revision_sha256=_sha(revision_path),
                  search_source_freeze_sha256=_sha(freeze_path),
                  full_window_source_sha256=_sha(__file__),
                  preference_source_sha256=_sha(ROOT / "tools/policy_search/preference.py"),
                  preference_v2_source_sha256=_sha(ROOT / "tools/policy_search/preference_v2.py"),
                  audit_source_sha256=_sha(ROOT / "tools/policy_search/audit.py"),
                  audit_passed=True, final_blind_opened=False,
                  post_hoc_full_window_diagnostic=True)
    return output


def write_full_window(location, batch=None):
    """Write a new immutable analysis beside an already audited batch."""
    run, folder = resolve_batch(location, batch)
    output = analyze_full_window(folder, run)
    path = folder / "ANALYSIS_FULL_WINDOW.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2, ensure_ascii=False, allow_nan=False)
    return path, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("location", type=Path,
                        help="Registered batch directory or v5 run directory")
    parser.add_argument("--batch", choices=tuple(BATCH_PATHS),
                        help="Required when location is the run directory")
    args = parser.parse_args()
    path, result = write_full_window(args.location, args.batch)
    print(json.dumps({"path": str(path),
                      "registered_case_count": result["registered_case_count"],
                      "common_complete_case_count": result["all_policy_common_complete_case_count"],
                      "audited_unknown_full_pair_count": len(result["audited_unknown_full_pair_ids"]),
                      "p0_pareto_ids_on_common": result["p0_pareto_ids_on_all_policy_common_cases"]},
                     ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
