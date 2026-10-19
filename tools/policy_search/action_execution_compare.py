"""Read-only, raw-SHA-bound comparison of requests, commands and execution.

Current-plan differences use each policy's saved *same-state* secure projection.
Actual dispatch differences use matched trajectories and may reflect diverged
states; they are outcome differences, not isolated current-action effects.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from tools.policy_search.execution_effect import _open_raw, _payload, _sha, _six


ROOT = Path(__file__).resolve().parents[2]
PORTS = ("gt_1", "gt_2", "gt_3", "grid_import", "diesel", "absorption_cooling_kW")
PLAN_DIFFERENCE_KW = 1.0
ACTUAL_DIFFERENCE_KW = 1.0
NUMERICAL_DIFFERENCE_KW = 1e-7
TIME_TOLERANCE_MIN = 1e-8


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _label(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _six_numbers(values) -> list[float]:
    if not isinstance(values, (tuple, list)) or len(values) != 6:
        raise ValueError("Saved current six-port vector is absent or malformed")
    result = [float(value) for value in values]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("Saved current six-port vector is nonfinite")
    return result


def _current_secure_plan(diagnostic: dict) -> list[float] | None:
    context = diagnostic.get("current_context") or {}
    if context.get("current_plan_targets_kW") is not None:
        return _six_numbers(context["current_plan_targets_kW"])
    queries = diagnostic.get("current_planning_queries") or []
    baseline = [q for q in queries if q.get("candidate_id") == "secure_baseline"
                and q.get("status") == "success"]
    if len(baseline) == 1:
        return _six_numbers(baseline[0]["projected_targets_kW"])
    return None


def summarize_chain(payload: dict) -> dict:
    """Count only fields saved in the raw decision and event ledger."""
    decisions, events = payload["decisions"], payload["events"]
    event_counts = Counter(event["kind"] for event in events)
    summary = {"decision_count": len(decisions),
               "diagnostic_present_count": 0,
               "explicit_target_request_count": 0,
               "partial_target_request_count": 0,
               "request_comparable_to_current_secure_count": 0,
               "request_vs_current_secure_gt1kW_count": 0,
               "current_secure_projection_available_count": 0,
               "projected_plan_vs_current_secure_gt1kW_count": 0,
               "projected_plan_vs_current_secure_numerically_different_count": 0,
               "strategy_fallback_count": 0, "gate_rejected_count": 0,
               "gate_reason_counts": {},
               "command_generated_count": event_counts["command_generated"],
               "command_arrived_count": event_counts["command_arrived"],
               "command_executed_count": event_counts["command_executed"],
               "command_superseded_count": event_counts["command_superseded"]}
    reasons = Counter()
    for decision in decisions:
        request = decision["requested_command"]
        generated = decision["command_generated"]
        if request != generated["request"]:
            raise ValueError("Raw request differs from generated command request")
        diagnostic = decision.get("policy_diagnostic")
        if diagnostic is None:
            diagnostic = {}
        else:
            summary["diagnostic_present_count"] += 1
        if diagnostic.get("strategy_fallback") is True:
            summary["strategy_fallback_count"] += 1
        rejected = (diagnostic.get("gate_rejected") is True or
                    diagnostic.get("strategy_status") == "secure_gate_refusal")
        if rejected:
            summary["gate_rejected_count"] += 1
            reasons[str(diagnostic.get("gate_reason") or diagnostic.get("strategy_status"))] += 1
        secure_plan = _current_secure_plan(diagnostic)
        targets = request.get("targets")
        if targets is not None:
            summary["explicit_target_request_count"] += 1
            if not isinstance(targets, dict) or set(targets) != set(PORTS):
                summary["partial_target_request_count"] += 1
            elif secure_plan is not None:
                requested = _six_numbers([targets[name] for name in PORTS])
                summary["request_comparable_to_current_secure_count"] += 1
                if max(abs(a - b) for a, b in zip(requested, secure_plan)) > PLAN_DIFFERENCE_KW:
                    summary["request_vs_current_secure_gt1kW_count"] += 1
        if secure_plan is not None:
            projected = _six(generated["dispatch"])
            summary["current_secure_projection_available_count"] += 1
            largest = max(abs(a - b) for a, b in zip(projected, secure_plan))
            if largest > PLAN_DIFFERENCE_KW:
                summary["projected_plan_vs_current_secure_gt1kW_count"] += 1
            if largest > NUMERICAL_DIFFERENCE_KW:
                summary["projected_plan_vs_current_secure_numerically_different_count"] += 1
    summary["gate_reason_counts"] = dict(sorted(reasons.items()))
    return summary


def _validate_segments(segments: list[dict]):
    previous = None
    for segment in segments:
        a, b = float(segment["start_minute"]), float(segment["end_minute"])
        if not math.isfinite(a) or not math.isfinite(b) or b <= a:
            raise ValueError("Raw segment has invalid time interval")
        if previous is not None and a < previous - TIME_TOLERANCE_MIN:
            raise ValueError("Raw segments overlap or are out of order")
        previous = b


def _segment_at(segments: list[dict], minute: float) -> dict | None:
    return next((s for s in segments if float(s["start_minute"]) <= minute <
                 float(s["end_minute"])), None)


def align_dispatch(candidate: list[dict], secure: list[dict],
                   window: tuple[float, float] | list[float]) -> dict:
    """Align actual subinterval actions; preserve every unobserved minute."""
    start, end = map(float, window)
    if not 0 <= start < end:
        raise ValueError("Invalid comparison window")
    _validate_segments(candidate)
    _validate_segments(secure)
    edges = {start, end}
    for segment in candidate + secure:
        for key in ("start_minute", "end_minute"):
            point = float(segment[key])
            if start < point < end:
                edges.add(point)
    edges = sorted(edges)
    aligned = unobserved = divergent = numerically_divergent = 0.0
    aligned_intervals = 0
    first = max_diff = None
    port_minutes = [0.0] * 6
    for a, b in zip(edges, edges[1:]):
        mid = (a + b) / 2
        left, right = _segment_at(candidate, mid), _segment_at(secure, mid)
        if left is None or right is None:
            unobserved += b - a
            continue
        aligned_intervals += 1
        aligned += b - a
        diff = [abs(x - y) for x, y in zip(_six(left["action"]), _six(right["action"]))]
        largest = max(diff)
        max_diff = largest if max_diff is None else max(max_diff, largest)
        if largest > NUMERICAL_DIFFERENCE_KW:
            numerically_divergent += b - a
        if largest > ACTUAL_DIFFERENCE_KW:
            divergent += b - a
            if first is None:
                first = a
        port_minutes = [old + (b - a if delta > ACTUAL_DIFFERENCE_KW else 0.0)
                        for old, delta in zip(port_minutes, diff)]
    return {"window_minutes": [start, end], "aligned_interval_count": aligned_intervals,
            "aligned_minutes": aligned, "unobserved_minutes": unobserved,
            "complete_alignment": unobserved <= TIME_TOLERANCE_MIN,
            "divergence_minutes_gt1kW_on_aligned_prefix": divergent,
            "numerical_divergence_minutes_gt1e-7kW_on_aligned_prefix": numerically_divergent,
            "divergent_port_minutes_gt1kW_on_aligned_prefix": dict(zip(PORTS, port_minutes)),
            "first_divergence_minute_gt1kW": first,
            "max_abs_six_port_difference_kW_on_aligned_prefix": max_diff}


def _match_binding(candidate: dict, secure: dict) -> bool:
    a, b = candidate.get("binding") or {}, secure.get("binding") or {}
    return bool(a.get("passed") and b.get("passed") and
                a.get("initial_state_sha256") == b.get("initial_state_sha256") and
                a.get("exogenous_stream_id") == b.get("exogenous_stream_id"))


def validate_audit_file_hashes(audit: dict, sid: str, pid: str, attempt: str,
                               result_sha: str, raw_sha: str) -> None:
    """Tie reopened bytes to the audit receipt, not only the result's raw SHA."""
    matches = [row for row in audit["attempts"]
               if row.get("pair_id") == f"{sid}/{pid}" and row.get("attempt") == attempt]
    if len(matches) != 1:
        raise ValueError(f"audit attempt SHA binding missing or duplicated: {sid}/{pid}/{attempt}")
    hashes = matches[0].get("file_sha256") or {}
    if hashes.get("result.json") != result_sha or hashes.get("trajectory.json.gz") != raw_sha:
        raise ValueError(f"audit result/raw SHA mismatch: {sid}/{pid}/{attempt}")


def _aggregate_window(rows: list[dict], name: str) -> dict:
    values = [row[name] for row in rows if row[name] is not None]
    return {"raw_pair_count": len(values),
            "complete_alignment_pair_count": sum(x["complete_alignment"] for x in values),
            "observed_divergence_pair_count_gt1kW": sum(
                x["divergence_minutes_gt1kW_on_aligned_prefix"] > 0 for x in values),
            "complete_divergence_pair_count_gt1kW": sum(
                x["complete_alignment"] and x["divergence_minutes_gt1kW_on_aligned_prefix"] > 0
                for x in values),
            "aligned_minutes": sum(x["aligned_minutes"] for x in values),
            "unobserved_minutes": sum(x["unobserved_minutes"] for x in values),
            "divergence_minutes_gt1kW_on_aligned_intervals": sum(
                x["divergence_minutes_gt1kW_on_aligned_prefix"] for x in values),
            "max_abs_six_port_difference_kW_on_aligned_intervals":
                max((x["max_abs_six_port_difference_kW_on_aligned_prefix"]
                     for x in values if x["max_abs_six_port_difference_kW_on_aligned_prefix"] is not None),
                    default=None)}


def _aggregate(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["policy_id"], row["condition_id"])].append(row)
    numeric_keys = ("decision_count", "diagnostic_present_count",
                    "explicit_target_request_count", "partial_target_request_count",
                    "request_comparable_to_current_secure_count",
                    "request_vs_current_secure_gt1kW_count",
                    "current_secure_projection_available_count",
                    "projected_plan_vs_current_secure_gt1kW_count",
                    "projected_plan_vs_current_secure_numerically_different_count",
                    "strategy_fallback_count", "gate_rejected_count",
                    "command_generated_count", "command_arrived_count",
                    "command_executed_count", "command_superseded_count")
    result = []
    for (pid, condition), subset in sorted(groups.items()):
        chains = [row["chain"] for row in subset if row["chain"] is not None]
        reasons = Counter(reason for chain in chains for reason, count in
                          chain["gate_reason_counts"].items() for _ in range(count))
        result.append({"policy_id": pid, "condition_id": condition,
                       "registered_pair_count": len(subset), "raw_pair_count": len(chains),
                       "status_counts": dict(sorted(Counter(row["status"] for row in subset).items())),
                       "chain_counts_on_raw_pairs": {key: sum(chain[key] for chain in chains)
                                                     for key in numeric_keys},
                       "gate_reason_counts_on_raw_pairs": dict(sorted(reasons.items())),
                       "main_vs_secure": _aggregate_window(subset, "main_vs_secure"),
                       "full_vs_secure": _aggregate_window(subset, "full_vs_secure")})
    return result


def compare_batch(batch_dir: Path) -> dict:
    """Reopen every registered trajectory only after its batch audit passes."""
    from tools.policy_search.closed_loop import verify_batch

    batch = Path(batch_dir).resolve()
    plan_path, audit_path = batch / "registered_plan.json", batch / "AUDIT.json"
    if not audit_path.is_file():
        raise FileNotFoundError("Audited batch required before action comparison")
    plan, manifest = verify_batch(plan_path)
    audit = _read(audit_path)
    if not (audit.get("passed") is True and audit.get("plan_sha256") == _sha(plan_path)
            and audit.get("plan_signature") == plan["signature"]
            and audit.get("manifest_sha256") == plan["manifest_sha256"]):
        raise ValueError("Batch audit/source binding differs from registered plan")
    scene_map = {s["id"]: s for s in manifest["scenarios"]}
    policy_ids = [spec["policy_id"] for spec in plan["policy_specs"]]
    raw = {}
    rows = []
    for sid in plan["scenario_ids"]:
        scene = scene_map[sid]
        for pid in policy_ids:
            result, meta = _payload(batch, sid, pid, plan["signature"])
            if result is None or meta is None or not result.get("terminal"):
                raise ValueError(f"Audited batch has no terminal result: {sid}/{pid}")
            payload, digest, raw_path = _open_raw(result, meta, sid, pid)
            result_digest = _sha(meta["result_path"])
            validate_audit_file_hashes(audit, sid, pid, meta["attempt"].name,
                                       result_digest, digest)
            chain = summarize_chain(payload)
            stored = result.get("command_counts") or {}
            for measured, stored_name in (("decision_count", "requested_decisions"),
                                          ("explicit_target_request_count", "explicit_target_requests"),
                                          ("command_generated_count", "planned_commands"),
                                          ("command_arrived_count", "arrived_commands"),
                                          ("command_executed_count", "executed_commands"),
                                          ("command_superseded_count", "superseded_commands"),
                                          ("strategy_fallback_count", "strategy_fallback_steps")):
                if stored_name in stored and chain[measured] != stored[stored_name]:
                    raise ValueError(f"Stored command count differs from raw: {sid}/{pid}/{stored_name}")
            raw[(sid, pid)] = (result, payload)
            rows.append({"scenario_id": sid, "root_family_id": scene["root_family_id"],
                         "condition_id": scene["condition_id"],
                         "pressure_point_index": scene.get("pressure_point_index"),
                         "policy_id": pid, "status": result["status"],
                         "observed_until_minute": result.get("observed_until_minute"),
                         "result_path": _label(meta["result_path"]),
                         "result_sha256": result_digest,
                         "raw_path": _label(raw_path), "raw_sha256": digest,
                         "chain": chain, "main_vs_secure": None, "full_vs_secure": None})
        secure = raw[(sid, "secure_milp")][0]
        for row in rows[-len(policy_ids):]:
            candidate, payload = raw[(sid, row["policy_id"])]
            if not _match_binding(candidate, secure):
                raise ValueError(f"Matched initial state or stream differs: {sid}/{row['policy_id']}")
            secure_payload = raw[(sid, "secure_milp")][1]
            row["main_vs_secure"] = align_dispatch(payload["segments"],
                                                    secure_payload["segments"],
                                                    plan["score_windows"]["primary"])
            row["full_vs_secure"] = align_dispatch(payload["segments"],
                                                    secure_payload["segments"],
                                                    plan["score_windows"]["full"])
    direct = []
    if {"nn_gru_aggregate_r1", "blend_overload"} <= set(policy_ids):
        for sid in plan["scenario_ids"]:
            neural, nn_raw = raw[(sid, "nn_gru_aggregate_r1")]
            teacher, teacher_raw = raw[(sid, "blend_overload")]
            if not _match_binding(neural, teacher):
                raise ValueError(f"NN/teacher initial state or stream differs: {sid}")
            direct.append({"scenario_id": sid,
                           "condition_id": scene_map[sid]["condition_id"],
                           "nn_status": neural["status"], "teacher_status": teacher["status"],
                           "main_nn_vs_teacher": align_dispatch(
                               nn_raw["segments"], teacher_raw["segments"],
                               plan["score_windows"]["primary"]),
                           "full_nn_vs_teacher": align_dispatch(
                               nn_raw["segments"], teacher_raw["segments"],
                               plan["score_windows"]["full"])})
    score_path = batch / "SCORE_RECHECK.json"
    return {"schema": "policy_search.action_execution_comparison.v1",
            "semantics": {"same_state_plan_comparison":
                          "Saved current secure projection in each policy's own current state; unavailable remains uncounted",
                          "actual_dispatch_comparison":
                          "Matched raw segment actions after trajectories may diverge; not isolated same-state causal effects",
                          "command_overwrite": "Count only raw command_superseded events; no separate overwrite event inferred",
                          "unknown": "Unobserved alignment minutes retained; zero observed divergence on a partial window is not a full-window tie",
                          "thresholds_kW": {"request_and_plan": PLAN_DIFFERENCE_KW,
                                            "actual_dispatch": ACTUAL_DIFFERENCE_KW,
                                            "numerical_trace": NUMERICAL_DIFFERENCE_KW}},
            "source_binding": {"analysis_source_sha256": _sha(Path(__file__)),
                               "raw_reader_source_sha256": _sha(ROOT / "tools/policy_search/execution_effect.py"),
                               "registered_source_sha256": plan["source_sha256"],
                               "plan_path": _label(plan_path), "plan_sha256": _sha(plan_path),
                               "plan_signature": plan["signature"],
                               "manifest_sha256": plan["manifest_sha256"],
                               "audit_path": _label(audit_path), "audit_sha256": _sha(audit_path),
                               "score_recheck_sha256": _sha(score_path) if score_path.is_file() else None},
            "batch": plan["phase"], "registered_pair_count": plan["registered_pair_count"],
            "raw_pair_count": len(rows), "per_pair": rows,
            "by_policy_condition": _aggregate(rows),
            "nn_aggregate_vs_nonlearning_blend_overload": {
                "nn_policy_id": "nn_gru_aggregate_r1", "teacher_policy_id": "blend_overload",
                "kind": "direct matched full-feedback trajectory comparison, not same-state action attribution",
                "per_scenario": direct,
                "main": _aggregate_window(
                    [{"main": x["main_nn_vs_teacher"]} for x in direct], "main"),
                "full": _aggregate_window(
                    [{"full": x["full_nn_vs_teacher"]} for x in direct], "full")}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = args.output or args.batch_dir / "ACTION_EXECUTION_COMPARISON.json"
    result = compare_batch(args.batch_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"output": str(target),
                      "registered_pair_count": result["registered_pair_count"],
                      "raw_pair_count": result["raw_pair_count"],
                      "nn_teacher_main": result["nn_aggregate_vs_nonlearning_blend_overload"]["main"]},
                     ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
