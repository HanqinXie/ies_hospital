"""Versioned, audited P0 comparison matching the original research preference.

This is a post-generation-1 correction to the *analysis procedure*. It adds
resolved P0 failures and case-level P0 maximum-temperature change to the P0
Pareto vector. It does not rewrite the v1 rank, change a controller, or turn
unobserved outcomes into zero. Only an independently audited summary with the
same registered paired support may be compared.
"""
from __future__ import annotations

import argparse
from itertools import combinations
import hashlib
import json
from pathlib import Path

from tools.policy_search.preference import paired_metrics


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "policy_search.audited_preference_v2.v1"
SOURCE_NAMES = (
    "tools/policy_search/search.py",
    "tools/policy_search/preference.py",
    "tools/policy_search/closed_loop.py",
    "tools/policy_search/policy.py",
    "tools/policy_search/preference_v2.py",
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(value):
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verified_inputs(plan_path, summary_path, audit_path, revision_path, old_freeze_path):
    plan, summary, audit = (_read(p) for p in (plan_path, summary_path, audit_path))
    revision = _read(revision_path)
    if plan.get("signature") != _digest({k: v for k, v in plan.items() if k != "signature"}):
        raise ValueError("plan_signature invalid")
    if plan.get("schema") != "policy_search.matched_full_feedback.v1":
        raise ValueError("plan_schema invalid")
    if audit.get("schema") != "policy_search.evidence_audit.v1" or audit.get("passed") is not True or audit.get("errors"):
        raise ValueError("audit receipt is not passing")
    for name, path in (("plan_sha256", plan_path), ("summary_sha256", summary_path),
                       ("source_freeze_sha256", old_freeze_path)):
        if audit.get(name) != _sha(path):
            raise ValueError(name + " differs from audited bytes")
    if (audit.get("plan_signature") != plan["signature"] or
            audit.get("registered_pair_count") != plan.get("registered_pair_count") or
            audit.get("matched_initial_state_and_stream") is not True):
        raise ValueError("audit plan or matched-pair identity differs")
    if audit.get("audit_source_sha256") != _sha(ROOT / "tools/policy_search/audit.py"):
        raise ValueError("audit_source_sha256 changed")
    if revision.get("schema") != "policy_search.preference_v2_source_freeze.v1":
        raise ValueError("v2 revision schema missing")
    if revision.get("registered_before_selection_development_outcomes") is not True:
        raise ValueError("v2 timing declaration missing")
    if revision.get("post_generation_1_outcome_correction") is not True:
        raise ValueError("v2 correction disclosure missing")
    if revision.get("v1_source_freeze_sha256") != _sha(old_freeze_path):
        raise ValueError("v1_source_freeze_sha256 differs")
    if (revision.get("research_preference_sha256") != plan.get("preference_sha256") or
            plan.get("preference_sha256") != _sha(plan["preference_path"])):
        raise ValueError("research_preference_sha256 differs")
    if revision.get("audit_source_sha256") != _sha(ROOT / "tools/policy_search/audit.py"):
        raise ValueError("v2 audit_source_sha256 differs")
    for name in SOURCE_NAMES:
        if revision.get("source_sha256", {}).get(name) != _sha(ROOT / name):
            raise ValueError("v2_source_sha256 differs: " + name)
    if summary.get("schema") != "policy_search.matched_full_feedback.v1.summary":
        raise ValueError("summary_schema invalid")
    if summary.get("plan_signature") != plan["signature"]:
        raise ValueError("summary_plan_signature differs")
    ids = plan.get("scenario_ids", [])
    specs = plan.get("policy_specs", [])
    policies = [s["policy_id"] for s in specs]
    expected = {(sid, pid) for sid in ids for pid in policies}
    rows = summary.get("results", [])
    actual = [(r.get("scenario_id"), r.get("policy_id")) for r in rows]
    if (len(expected) != len(ids) * len(policies) or
            len(rows) != len(expected) or len(set(actual)) != len(actual) or
            set(actual) != expected or
            plan.get("registered_pair_count") != len(expected) or
            summary.get("registered_pair_count") != len(expected) or
            summary.get("recorded_pair_count") != len(expected) or
            summary.get("terminal_pair_count") != len(expected)):
        raise ValueError("registered_pair_set differs")
    identities = {sid: set() for sid in ids}
    for row in rows:
        sid = row["scenario_id"]
        binding = row.get("binding", {})
        if (row.get("terminal") is not True or row.get("plan_signature") != plan["signature"] or
                row.get("phase") != plan.get("phase") or
                binding.get("passed") is not True or
                not all(binding.get("checks", {}).values()) or
                not binding.get("initial_state_sha256") or
                not binding.get("exogenous_stream_id")):
            raise ValueError("binding.passed or row identity differs")
        identities[sid].add((binding["initial_state_sha256"], binding["exogenous_stream_id"]))
        score = row.get("primary_score", {})
        targets, mask = score.get("targets"), score.get("target_mask")
        if (not isinstance(targets, list) or len(targets) != 19 or
                not isinstance(mask, list) or len(mask) != 19 or
                mask != [x is not None for x in targets] or
                bool(score.get("full_horizon")) != all(mask)):
            raise ValueError("primary_score unknown mask differs")
    if any(len(values) != 1 for values in identities.values()):
        raise ValueError("matched physical pair identity differs")
    return plan, summary, audit


def _extended_metric(rows, policy_id):
    metric = paired_metrics(rows, policy_id)
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


def p0_dominates_v2(a, b):
    """P0 Pareto order only on identical complete paired cases; lower is better."""
    if (not a.get("complete_paired_case_ids") or
            a["complete_paired_case_ids"] != b.get("complete_paired_case_ids")):
        return False
    keys = ("new_P0_failure_cases", "P0_failure_minutes_total_delta",
            "largest_case_P0_failure_minutes_increase", "P0_e_ENS_kWh_delta",
            "P0_h_ENS_kWh_delta", "P0_c_ENS_kWh_delta",
            "largest_case_P0_e_ENS_increase", "largest_case_P0_h_ENS_increase",
            "largest_case_P0_c_ENS_increase", "largest_case_P0_temperature_delta_C")
    left = [a[key] for key in keys] + [-a["resolved_P0_failure_cases"]]
    right = [b[key] for key in keys] + [-b["resolved_P0_failure_cases"]]
    return all(x <= y for x, y in zip(left, right)) and any(x < y for x, y in zip(left, right))


def analyze_audited_batch(plan_path, summary_path, audit_path, revision_path,
                          old_freeze_path):
    """Compare all policies without cost offset or unequal support dominance."""
    plan, summary, _ = _verified_inputs(plan_path, summary_path, audit_path,
                                        revision_path, old_freeze_path)
    rows = summary["results"]
    metrics = {spec["policy_id"]: _extended_metric(rows, spec["policy_id"])
               for spec in plan["policy_specs"]}
    candidates = [spec["policy_id"] for spec in plan["policy_specs"]
                  if spec["policy_id"] != "secure_milp"]
    front = [pid for pid in candidates if not any(
        p0_dominates_v2(metrics[other], metrics[pid])
        for other in candidates if other != pid)]
    support_mismatch = [[a, b] for a, b in combinations(candidates, 2)
                        if metrics[a]["complete_paired_case_ids"] !=
                           metrics[b]["complete_paired_case_ids"]]
    return {"schema": SCHEMA, "plan_signature": plan["signature"],
            "plan_sha256": _sha(plan_path), "summary_sha256": _sha(summary_path),
            "audit_sha256": _sha(audit_path), "v2_revision_sha256": _sha(revision_path),
            "preference_v2_source_sha256": _sha(__file__),
            "registered_case_ids": list(plan["scenario_ids"]),
            "metrics": metrics, "p0_pareto_ids": front,
            "incomparable_due_to_support": support_mismatch,
            "clinical_exchange_rate": None,
            "cost_cannot_offset_P0": True,
            "v1_outputs_preserved": True,
            "post_generation_1_outcome_correction": True}


def analyze_generation(run_dir, generation):
    run = Path(run_dir)
    folder = run / f"search/generation_{generation}"
    summaries = sorted((folder / "results").glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("No scored summary exists")
    analysis = analyze_audited_batch(
        folder / "registered_plan.json", summaries[-1], folder / "AUDIT.json",
        run / "design/PREFERENCE_V2_SOURCE_FREEZE.json",
        run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json")
    path = folder / "ANALYSIS_PREFERENCE_V2.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(analysis, stream, indent=2, ensure_ascii=False, allow_nan=False)
    return analysis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--generation", type=int, required=True)
    args = parser.parse_args()
    value = analyze_generation(args.run_dir, args.generation)
    print(json.dumps({"p0_pareto_ids": value["p0_pareto_ids"],
                      "incomparable_due_to_support": value["incomparable_due_to_support"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
