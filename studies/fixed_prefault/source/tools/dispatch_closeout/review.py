"""Small read-only closeout tables over the existing audited v9 definitions.

Pass only completed, audited analysis directories. Unrun confirmation remains
pending. No simulator, optimizer, sealed registry or unseen outcome is read.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys
from types import FunctionType

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tools.state_dispatch import analysis, report_tables as previous

EXPECTED = ("development_normal", "development_mechanism", "confirmation_a", "confirmation_b")
SCALARS = ("critical_electric_kW", "critical_heat_kW", "critical_cooling_ec_equivalent_kW",
    "arrived_GT_heat_proxy_kW", "arrived_boiler_heat_kW", "current_planned_boiler_steam_lbph",
    "planned_ABS_steam_lbph", "thermal_GT_preference_kW", "readiness_preference_kW",
    "current_known_response_wait_minutes")
FLAGS = ("clock_exposed", "strategy_fallback", "same_state_request_changed",
    "same_state_projected_plan_changed", "projection_adjusted_soft_request",
    "generated_command_executed_in_observed_prefix", "linked_arrival_clamp_changed_generated_plan")


def _changed(left, right):
    if left is None or right is None:
        return None
    return any(abs(float(a) - float(b)) > analysis.ACTION_EFFECT_TOLERANCE_KW
               for a, b in zip(left, right))


def _request_six(request, diagnostic):
    targets = (request or {}).get("targets", {})
    if set(targets) == set(analysis.ACTION_NAMES):
        return analysis._six([targets[key] for key in analysis.ACTION_NAMES])
    return analysis._six(diagnostic.get("requested_six_targets_kW"))


def decision_rows(batch, context, payload):
    """Link generated command ID to its own observed arrival execution.

Same-step execution belongs to the original queue and is retained separately.
Differences here describe saved records; they are not trajectory causal effects.
"""
    executed = defaultdict(list)
    for event in payload.get("events", []):
        if event.get("kind") == "command_executed":
            executed[event.get("command_id")].append(event)
    output = []
    observed = float(payload["summary"]["observed_until_minute"])
    for decision in payload.get("decisions", []):
        now = float(decision["decision_time_min"])
        if not math.isfinite(now) or now < 0 or now > observed + 1e-6:
            raise ValueError("Decision outside the actually observed raw prefix")
        d = decision.get("policy_diagnostic") or {}
        command = decision.get("command_generated") or {}
        command_id = command.get("id")
        links = sorted(executed.get(command_id, []), key=lambda e: float(e["time_minute"])) if command_id is not None else []
        arrival = links[0] if links else {}
        request = _request_six(decision.get("requested_command"), d)
        base = analysis._six(d.get("same_state_base_plan_targets_kW"))
        projected = analysis._dispatch(command.get("dispatch"))
        actual = analysis._dispatch(decision.get("actual_execution"))
        arrived = analysis._dispatch(arrival.get("executed_dispatch"))
        row = {"batch": batch, **{name: context.get(name) for name in
            ("scenario_id", "root_family_id", "analysis_class", "analysis_cohort", "policy_id")},
            "decision_time_min": now, "observed_until_minute": observed,
            "feedback_family": d.get("feedback_family"), "mode": d.get("mode"),
            "new_state_basis_recorded": d.get("feedback_family") == "dispatch_closeout",
            **{name: d.get(name) for name in SCALARS},
            "clock_exposed": d.get("clock_exposed"), "strategy_fallback": d.get("strategy_fallback"),
            "conditional_protection_remaining_minutes_json": d.get("conditional_protection_remaining_minutes"),
            "pending_command_dependencies_json": d.get("pending_command_dependencies"),
            "diesel_sustainability_json": d.get("diesel_sustainability"),
            "allocation_json": d.get("allocation"), "registered_config_json": d.get("registered_config"),
            "same_state_base_targets_kW_json": base,
            "requested_targets_kW_json": request, "projected_targets_kW_json": projected,
            "same_step_actual_targets_kW_json": actual,
            "linked_arrival_executed_targets_kW_json": arrived,
            "generated_command_id": command_id,
            "planned_arrival_minute": command.get("arrival_minute"),
            "linked_first_execution_minute": arrival.get("time_minute"),
            "generated_command_executed_in_observed_prefix": bool(links) if command_id is not None else None,
            "same_state_request_changed": _changed(request, base),
            "same_state_projected_plan_changed": _changed(projected, base),
            "projection_adjusted_soft_request": _changed(projected, request),
            "linked_arrival_clamp_changed_generated_plan": _changed(arrived, projected),
            "actual_execution_is_not_the_new_request": True,
            "same_step_actual_is_not_linked_new_command_execution": True,
            "parallel_trajectory_difference_is_not_same_state_causal_effect": True,
            "readiness_is_preference_not_joint_capacity_certificate": d.get("readiness_is_preference_not_joint_capacity_certificate"),
            "conditional_clock_is_not_trip_forecast": d.get("conditional_clock_is_not_trip_forecast"),
            "future_rollouts_online": d.get("future_rollouts_online")}
        output.append(row)
    return output


def summarize_decisions(rows):
    groups = defaultdict(list)
    names = ("batch", "analysis_cohort", "window", "analysis_class", "policy_id")
    for row in rows:
        for window, (start, end) in previous.WINDOWS.items():
            if start <= row["decision_time_min"] < end:
                key = tuple(window if name == "window" else row[name] for name in names)
                groups[key].append(row)
    output = []
    for key, group in sorted(groups.items()):
        row = {**dict(zip(names, key)), "decision_count": len(group),
            "independent_root_count": len({r["root_family_id"] for r in group}),
            "new_state_basis_decision_count": sum(r["new_state_basis_recorded"] for r in group),
            "mode_counts_json": dict(Counter(r.get("mode") or "not_recorded" for r in group)),
            "command_link_scope": "Commands generated in the scoring window; own executions anywhere in the observed raw prefix"}
        for flag in FLAGS:
            for suffix, value in (("true", True), ("false", False), ("unknown", None)):
                row[flag + "_" + suffix + "_count"] = sum(r[flag] is value for r in group)
        for name in SCALARS:
            values = [float(r[name]) for r in group if r.get(name) is not None]
            if any(not math.isfinite(x) for x in values):
                raise ValueError("Nonfinite basis diagnostic")
            row[name + "_known_count"] = len(values)
            row[name + "_min"] = min(values, default=None)
            row[name + "_max"] = max(values, default=None)
        output.append(row)
    return output


def _raw_decisions(batches):
    output, bindings = [], []
    for batch, directory in sorted(batches.items()):
        # Validate CSV, audit and summary denominators using the unchanged tool.
        rows, binding, _, _ = previous._load_batch(batch, directory)
        bindings.append(binding)
        for row in rows:
            raw = Path(row["raw_path"])
            if previous.original._sha(raw) != row["raw_sha256"]:
                raise ValueError("Raw SHA changed before closeout review: " + str(raw))
            payload = previous.original._open_raw(raw)
            output.extend(decision_rows(batch, row, payload))
    return output, bindings


def _normal_cost_rates(report):
    output = []
    by_key = {(r["batch"], r["analysis_cohort"], r["window"], r["policy_id"]): r
              for r in report["normal_n2_costs"]}
    for original in report["normal_n2_costs"]:
        row = dict(original)
        start, end = previous.WINDOWS[row["window"]]
        hours = row["registered_scenario_count"] * (end - start) / 60.
        total = row["actual_energy_fee_USD_all_registered"]
        row["registered_observation_hours"] = hours
        row["fee_USD_per_registered_hour_all_registered"] = total / hours if total is not None else None
        for reference in previous.REFERENCES:
            base = by_key.get((row["batch"], row["analysis_cohort"], row["window"], reference))
            fee = None if base is None else base["actual_energy_fee_USD_all_registered"]
            row["fee_fraction_change_vs_" + reference] = ((total - fee) / fee
                if total is not None and fee is not None and fee != 0 else None)
        output.append(row)
    return output


def build_report_tables(run, out_dir=None, *, batches, replace=False):
    """Use explicit completed analysis batches and preserve unrun batch roles."""
    run = Path(run).resolve()
    destination = Path(out_dir).resolve() if out_dir else run / "review/metrics"
    if not batches or set(batches) - set(EXPECTED):
        raise ValueError("Explicit closeout completed batch mapping required")
    batches = {name: Path(path).resolve() for name, path in batches.items()}
    own_names = ("CLOSEOUT_REVIEW.json", "STATE_BASIS_DECISIONS.csv", "STATE_BASIS_SUMMARY.csv", "NORMAL_COST_RATES.csv")
    if not replace and any((destination / name).exists() for name in own_names):
        raise FileExistsError("Closeout review exists; choose a fresh directory or replace=True")
    decisions, bindings = _raw_decisions(batches)
    namespace = dict(previous.__dict__)
    namespace["EXPECTED"] = EXPECTED
    build = FunctionType(previous.build_report_tables.__code__, namespace,
        previous.build_report_tables.__name__, previous.build_report_tables.__defaults__)
    build.__kwdefaults__ = previous.build_report_tables.__kwdefaults__
    inherited = build(run, destination, batches=batches, replace=replace)
    summary = summarize_decisions(decisions)
    rates = _normal_cost_rates(inherited)
    report = {**inherited, "schema": "dispatch_closeout.audited_review.v1",
        "inherited_metric_table_schema": inherited["schema"],
        "review_source_sha256": previous.original._sha(Path(__file__)),
        "metric_aggregation_source_sha256": previous.original._sha(Path(previous.__file__)),
        "physical_trajectories_started": 0, "optimizer_calls": 0,
        "new_state_basis_summary": summary, "normal_cost_rates": rates,
        "state_basis_decision_count": len(decisions),
        "raw_inputs_reverified": True, "completed_input_bindings": bindings,
        "pending_batches_are_not_zero_results": True,
        "confirmation_loaded": any(name.startswith("confirmation_") for name in batches),
        "confirmation_loaded_batches": [name for name in EXPECTED if name.startswith("confirmation_") and name in batches],
        "both_confirmation_batches_loaded": all(name in batches for name in ("confirmation_a", "confirmation_b")),
        "current_operating_basis_is_not_physical_safety_certificate": True,
        "inherited_final_metrics_sha256": previous.original._sha(destination / "FINAL_METRICS.json")}
    for name, rows in (("STATE_BASIS_DECISIONS", decisions), ("STATE_BASIS_SUMMARY", summary),
                       ("NORMAL_COST_RATES", rates)):
        (destination / (name + ".csv")).write_text(previous._csv_text(rows), encoding="utf-8-sig")
    (destination / "CLOSEOUT_REVIEW.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--batch", action="append", required=True, help="name=completed-analysis-directory")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    report = build_report_tables(args.run, args.out_dir,
        batches=dict(value.split("=", 1) for value in args.batch), replace=args.replace)
    print(json.dumps({key: report[key] for key in
        ("loaded_batches", "missing_expected_batches", "input_trajectory_count", "state_basis_decision_count")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
