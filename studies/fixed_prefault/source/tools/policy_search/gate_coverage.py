"""Read-only, audited calibration gate coverage and matched P0 tradeoff."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path

from tools.policy_search.action_execution_compare import (
    ROOT, PORTS, summarize_chain, validate_audit_file_hashes)
from tools.policy_search.execution_effect import _open_raw, _payload, _sha


GATES = {"nn_ungated": None, "nn_gate_t025": .25,
         "nn_gate_t050": .50, "nn_gate_t075": .75}
TARGETS = ("P0_failure", "P0_failure_minutes", "P0_e_ENS_kWh",
           "P0_h_ENS_kWh", "P0_c_ENS_kWh", "actual_energy_fee_USD")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _label(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _legal_observation(decision: dict) -> bool:
    """Mechanical saved-time check; batch AUDIT remains the full legal-info audit."""
    try:
        minute = float(decision["decision_time_min"])
        times = (float(decision["observation_time_min"]),
                 float(decision["history_end_min"]))
        meta = decision["feature_metadata"]
        if not isinstance(meta, dict) or not meta or not decision.get("features") and "features" in decision:
            return False
        times += tuple(float(row["available_time_min"]) for row in meta.values())
        return math.isfinite(minute) and all(math.isfinite(t) and t <= minute + 1e-8
                                              for t in times)
    except (KeyError, TypeError, ValueError):
        return False


def summarize_gate_raw(payload: dict, policy_id: str, threshold: float | None) -> dict:
    """Use logged score as teacher-intervention propensity, never safety odds."""
    if policy_id not in GATES or GATES[policy_id] != threshold:
        raise ValueError("Unexpected gate policy or threshold")
    chain = summarize_chain(payload)
    counts = {"decision_count": len(payload["decisions"]),
              "saved_legal_observation_decision_count": 0,
              "model_score_available_count": 0,
              "score_below_own_threshold_count": 0,
              "gate_rejected_count": 0,
              "explicit_six_port_request_count": 0,
              "strategy_fallback_count": chain["strategy_fallback_count"],
              "command_generated_count": chain["command_generated_count"],
              "command_arrived_count": chain["command_arrived_count"],
              "command_executed_count": chain["command_executed_count"],
              "command_superseded_count": chain["command_superseded_count"],
              "score_below_fixed_thresholds": {"0.25": 0, "0.50": 0, "0.75": 0},
              "fallback_reasons": {}}
    reasons = Counter()
    for decision in payload["decisions"]:
        diagnostic = decision.get("policy_diagnostic") or {}
        if diagnostic.get("gate_mode") != ("none" if threshold is None else "confidence"):
            raise ValueError("Raw gate mode differs from registered policy")
        if diagnostic.get("gate_threshold") != threshold:
            raise ValueError("Raw gate threshold differs from registered policy")
        if _legal_observation(decision):
            counts["saved_legal_observation_decision_count"] += 1
        request = decision["requested_command"]
        targets = request.get("targets")
        if targets is not None:
            if not isinstance(targets, dict) or set(targets) != set(PORTS):
                raise ValueError("Partial or non-six-port neural request")
            counts["explicit_six_port_request_count"] += 1
        rejected = diagnostic.get("gate_rejected") is True
        if rejected:
            counts["gate_rejected_count"] += 1
            if targets is not None:
                raise ValueError("Gate-rejected decision still requested six ports")
        if diagnostic.get("strategy_fallback") is True:
            reasons[str(diagnostic.get("fallback_reason") or "unspecified")] += 1
        value = diagnostic.get("intervention_probability")
        if value is not None:
            probability = float(value)
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("Invalid teacher-intervention propensity")
            counts["model_score_available_count"] += 1
            for label, cutoff in (("0.25", .25), ("0.50", .50), ("0.75", .75)):
                counts["score_below_fixed_thresholds"][label] += probability < cutoff
            if threshold is not None:
                below = probability < threshold
                counts["score_below_own_threshold_count"] += below
                if not diagnostic.get("strategy_fallback") and rejected != below:
                    raise ValueError("Logged gate rejection differs from score threshold")
        elif rejected:
            raise ValueError("Gate rejection lacks model score")
    counts["fallback_reasons"] = dict(sorted(reasons.items()))
    if counts["explicit_six_port_request_count"] != chain["explicit_target_request_count"]:
        raise ValueError("Six-port request count differs from raw action-chain count")
    if counts["gate_rejected_count"] != chain["gate_rejected_count"]:
        raise ValueError("Gate-rejected count differs from raw action-chain count")
    return counts


def same_support_outcome(metric: dict, common_case_ids: list[str],
                         registered_case_ids: list[str]) -> dict:
    """Project audited per-case deltas onto one support; keep missing as null."""
    names = metric["target_names"]
    if len(names) != len(set(names)) or not set(TARGETS) <= set(names):
        raise ValueError("Audited P0/ENS/cost target names changed")
    if set(metric["individual_cases"]) != set(registered_case_ids):
        raise ValueError("Audited case inventory differs from registration")
    if not set(common_case_ids) <= set(registered_case_ids):
        raise ValueError("Common support contains unregistered case")
    if len(common_case_ids) != len(set(common_case_ids)):
        raise ValueError("Repeated common-support case")
    indices = {name: names.index(name) for name in TARGETS}
    per_case = {}
    for sid in registered_case_ids:
        row = metric["individual_cases"][sid]
        complete = row.get("complete_paired_window") is True
        if sid in common_case_ids and not complete:
            raise ValueError("Common support includes incomplete paired case")
        delta = row.get("delta_targets")
        if complete and (not isinstance(delta, list) or len(delta) != len(names)):
            raise ValueError("Complete paired case lacks target deltas")
        per_case[sid] = {"candidate_status": row.get("candidate_status"),
                         "baseline_status": row.get("baseline_status"),
                         "complete_paired_window": complete,
                         "on_same_support": sid in common_case_ids,
                         "delta": {name: delta[index] for name, index in indices.items()}
                         if sid in common_case_ids else None}
    totals = {}
    for name in TARGETS:
        values = [per_case[sid]["delta"][name] for sid in common_case_ids]
        totals[name] = sum(values) if all(value is not None for value in values) else None
    return {"registered_case_count": len(registered_case_ids),
            "same_support_case_ids": list(common_case_ids),
            "same_support_case_count": len(common_case_ids),
            "unknown_case_ids": [sid for sid in registered_case_ids
                                 if sid not in common_case_ids],
            "delta_on_same_support": totals, "per_case": per_case}


def _common_ids(metrics: dict, policy_ids: list[str], registered: list[str]) -> list[str]:
    return [sid for sid in registered if all(
        metrics[pid]["individual_cases"][sid].get("complete_paired_window") is True
        for pid in policy_ids)]


def validate_common_support(computed: list[str], audited: list[str]) -> None:
    """Case membership is fixed; presentation order need not match plan order."""
    if len(computed) != len(set(computed)) or len(audited) != len(set(audited)) or set(computed) != set(audited):
        raise ValueError("Full-window common support differs from audited analysis")


def compare_calibration(batch_dir: Path) -> dict:
    from tools.policy_search.closed_loop import verify_batch

    batch = Path(batch_dir).resolve()
    plan_path, audit_path = batch / "registered_plan.json", batch / "AUDIT.json"
    v2_path, full_path = batch / "ANALYSIS_PREFERENCE_V2.json", batch / "ANALYSIS_FULL_WINDOW.json"
    plan, manifest = verify_batch(plan_path)
    audit, v2, full = _read(audit_path), _read(v2_path), _read(full_path)
    if plan["phase"] != "calibration" or not set(GATES) <= {
            item["policy_id"] for item in plan["policy_specs"]}:
        raise ValueError("Registered calibration gate policies missing")
    plan_sha, audit_sha = _sha(plan_path), _sha(audit_path)
    if not (audit.get("passed") is True and audit.get("registered_pair_count") == 48
            and audit.get("observed_pair_count") == 48
            and audit.get("plan_sha256") == plan_sha
            and audit.get("plan_signature") == plan["signature"]
            and audit.get("manifest_sha256") == plan["manifest_sha256"]):
        raise ValueError("Calibration batch audit or plan binding failed")
    if not (v2.get("plan_sha256") == full.get("plan_sha256") == plan_sha
            and v2.get("audit_sha256") == full.get("audit_sha256") == audit_sha
            and v2.get("plan_signature") == full.get("plan_signature") == plan["signature"]
            and full.get("audit_passed") is True and full.get("final_blind_opened") is False):
        raise ValueError("Audited v2/full-window analysis binding failed")
    if v2.get("preference_v2_source_sha256") != _sha(
            ROOT / "tools/policy_search/preference_v2.py"):
        raise ValueError("v2 preference source changed")
    if full.get("full_window_source_sha256") != _sha(
            ROOT / "tools/policy_search/full_window.py"):
        raise ValueError("full-window source changed")
    registered = plan["scenario_ids"]
    if set(registered) != set(v2["registered_case_ids"]) or set(registered) != set(
            full["registered_case_ids"]):
        raise ValueError("Outcome case support differs from registration")
    policy_ids = ["secure_milp", *GATES]
    primary_ids = _common_ids(v2["metrics"], policy_ids, registered)
    full_ids = _common_ids(full["metrics_on_all_policy_common_cases"], policy_ids, registered)
    validate_common_support(full_ids, full["all_policy_common_complete_case_ids"])
    scene_map = {scene["id"]: scene for scene in manifest["scenarios"]}
    rows = []
    for pid, threshold in GATES.items():
        for sid in registered:
            result, meta = _payload(batch, sid, pid, plan["signature"])
            if result is None or meta is None or result.get("terminal") is not True:
                raise ValueError(f"Missing terminal gate result: {sid}/{pid}")
            payload, raw_sha, raw_path = _open_raw(result, meta, sid, pid)
            result_sha = _sha(meta["result_path"])
            validate_audit_file_hashes(audit, sid, pid, meta["attempt"].name,
                                       result_sha, raw_sha)
            counts = summarize_gate_raw(payload, pid, threshold)
            stored = result.get("command_counts") or {}
            for field, key in (("decision_count", "requested_decisions"),
                               ("explicit_six_port_request_count", "explicit_target_requests"),
                               ("command_arrived_count", "arrived_commands"),
                               ("command_executed_count", "executed_commands"),
                               ("command_superseded_count", "superseded_commands"),
                               ("strategy_fallback_count", "strategy_fallback_steps")):
                if key in stored and counts[field] != stored[key]:
                    raise ValueError(f"Result count differs from raw: {sid}/{pid}/{key}")
            rows.append({"scenario_id": sid, "condition_id": scene_map[sid]["condition_id"],
                         "policy_id": pid, "status": result["status"],
                         "observed_until_minute": result.get("observed_until_minute"),
                         "result_path": _label(meta["result_path"]),
                         "result_sha256": result_sha,
                         "raw_path": _label(raw_path), "raw_sha256": raw_sha,
                         "counts": counts})
    policies = {}
    numeric_keys = tuple(key for key, value in rows[0]["counts"].items()
                         if isinstance(value, int))
    for pid, threshold in GATES.items():
        subset = [row for row in rows if row["policy_id"] == pid]
        counts = {key: sum(row["counts"][key] for row in subset) for key in numeric_keys}
        counts["score_below_fixed_thresholds"] = {
            label: sum(row["counts"]["score_below_fixed_thresholds"][label]
                       for row in subset) for label in ("0.25", "0.50", "0.75")}
        counts["fallback_reasons"] = dict(sorted(Counter({
            reason: sum(row["counts"]["fallback_reasons"].get(reason, 0)
                        for row in subset)
            for reason in {key for row in subset for key in row["counts"]["fallback_reasons"]}
        }).items()))
        policies[pid] = {"gate_mode": "none" if threshold is None else "confidence",
                         "gate_threshold": threshold,
                         "raw_pair_count": len(subset),
                         "status_counts": dict(sorted(Counter(row["status"] for row in subset).items())),
                         "counts": counts,
                         "primary_v2_same_support": same_support_outcome(
                             v2["metrics"][pid], primary_ids, registered),
                         "full_window_same_support": same_support_outcome(
                             full["metrics_on_all_policy_common_cases"][pid],
                             full_ids, registered)}
    return {"schema": "policy_search.gate_coverage_p0_tradeoff.v1",
            "meaning": {"model_score": "GRU propensity for a nonlearning teacher intervention, not a calibrated probability of safety or P0 benefit",
                        "gate_rejection": "Logged confidence threshold decision; not a guarantee of P0 protection",
                        "legal_decision": "Saved observation, history and feature availability times do not exceed decision time; complete legal-info audit is AUDIT.json",
                        "unknown": "Incomplete paired outcome remains null and is excluded from same-support deltas",
                        "command": "Counts reflect logged command event kinds, not beneficial interventions"},
            "source_binding": {"analysis_source_sha256": _sha(Path(__file__)),
                               "action_chain_source_sha256": _sha(ROOT / "tools/policy_search/action_execution_compare.py"),
                               "raw_reader_source_sha256": _sha(ROOT / "tools/policy_search/execution_effect.py"),
                               "plan_path": _label(plan_path), "plan_sha256": plan_sha,
                               "plan_signature": plan["signature"],
                               "manifest_sha256": plan["manifest_sha256"],
                               "registered_source_sha256": plan["source_sha256"],
                               "model_bindings": {pid: plan["model_bindings"][pid] for pid in GATES},
                               "audit_path": _label(audit_path), "audit_sha256": audit_sha,
                               "v2_analysis_path": _label(v2_path), "v2_analysis_sha256": _sha(v2_path),
                               "full_analysis_path": _label(full_path), "full_analysis_sha256": _sha(full_path)},
            "registered_case_ids": registered,
            "primary_same_support_case_ids": primary_ids,
            "primary_unknown_case_ids": [sid for sid in registered if sid not in primary_ids],
            "full_same_support_case_ids": full_ids,
            "full_unknown_case_ids": [sid for sid in registered if sid not in full_ids],
            "raw_pair_count": len(rows), "per_pair": rows, "policies": policies}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = args.output or args.batch_dir / "GATE_COVERAGE_P0_TRADEOFF.json"
    result = compare_calibration(args.batch_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"output": str(target), "raw_pair_count": result["raw_pair_count"],
                      "primary_same_support_case_count": len(result["primary_same_support_case_ids"]),
                      "full_same_support_case_count": len(result["full_same_support_case_ids"]),
                      "policy_counts": {pid: row["counts"] for pid, row in result["policies"].items()}},
                     ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
