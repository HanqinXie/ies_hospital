"""Read audited state-dispatch raws without simulation or online future input.

Service, recovery and terminal-state definitions are inherited from the v8
analysis. Local design quotas remain separate cohorts and discrete coordinates;
their frequencies are not hospital failure probabilities.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import csv
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.data_contract import validate_action
from tools.four_scenarios import analysis as previous
from tools.four_scenarios import deepening_analysis as deepening
from tools.integrated_learning.policies import ACTION_NAMES, feature_whitelist, legal_history, six_targets

SCHEMA = "state_dispatch.audited_saved_raw_analysis.v1"
REFERENCES = ("secure_milp", "priority_milp")
WINDOWS = ("primary", "full")
# Same descriptive action-effect resolution used by the existing raw audit.
ACTION_EFFECT_TOLERANCE_KW = 1e-6
REASONS = frozenset({"observed_demand_rise", "currently_unavailable_equipment",
    "new_observed_equipment_loss", "observed_protection_accumulation",
    "observed_P0_disconnection", "current_observations_quiet"})


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _fixed_context(scene):
    """Keep other physical coordinates fixed when displaying a tested axis."""
    fields = ("base_state_id", "start_hour", "step_minutes", "duration_steps",
              "grid_limit_kW", "pre_event_grid_limit_kW", "pre_event_warmup_steps",
              "enable_propagation", "response_delay_minutes", "shock", "shock_by_priority",
              "thermal_disturbance", "precontrol_sheddable_fraction_by_priority")
    fixed = {key: copy.deepcopy(scene[key]) for key in fields if key in scene}
    events = []
    for source in scene.get("exogenous_events", []):
        event = {key: copy.deepcopy(source[key]) for key in
                 ("kind", "device", "availability", "start_minute", "shock", "shock_by_priority")
                 if key in source}
        if source.get("end_minute") is not None and source.get("start_minute") is not None:
            event["duration_minutes"] = float(source["end_minute"]) - float(source["start_minute"])
        events.append(event)
    fixed["events"] = events
    axis, value = scene.get("design_axis"), scene.get("design_axis_value")
    if axis in {"heat_amplitude", "heat_readiness", "new_heat_timing"}:
        fixed.get("shock", {}).pop("h", None)
        for event in events:
            event.get("shock", {}).pop("h", None)
    elif axis == "response_window":
        fixed.pop("response_delay_minutes", None)
    elif axis in {"cool_duration", "new_cool_timing"}:
        for event in events:
            event.pop("duration_minutes", None)
    elif axis == "event_phase" and isinstance(value, (int, float)):
        for event in events:
            if event.get("start_minute") is not None:
                event["start_minute"] = float(event["start_minute"]) - float(value)
    elif axis in {"fault_context", "new_fault_timing"}:
        for event in events:
            if event.get("kind") == "device_fault":
                event.pop("device", None)
                event.pop("availability", None)
    return _canonical(fixed)


def _context(scene):
    return {"analysis_cohort": scene.get("analysis_cohort", scene.get("sampling_cohort", "unregistered")),
            "design_axis": scene.get("design_axis"),
            "axis_value": scene.get("design_axis_value"),
            "design_axis_value": scene.get("design_axis_value"),
            "matched_group_id": scene.get("matched_group_id"),
            "fixed_context_json": _fixed_context(scene)}


def _deltas(rows, policy_ids):
    """Reuse the original contrast with each frozen baseline as reference."""
    by_scene = defaultdict(dict)
    for row in rows:
        by_scene[row["scenario_id"]][row["policy_id"]] = row
    output = []
    for sid, policies in sorted(by_scene.items()):
        for reference in REFERENCES:
            for pid in policy_ids:
                if pid == reference:
                    continue
                candidate, base = policies[pid], policies.get(reference)
                if base is None:
                    record = {"scenario_id": sid, "candidate_policy_id": pid,
                              "reference_policy_id": reference, "reference_available": False}
                    for window in WINDOWS:
                        record[window + "_complete_pair"] = False
                        for metric in previous.TARGET_NAMES:
                            name = window + "_" + metric
                            record[name + "_candidate"] = candidate.get(name)
                            record[name + "_reference"] = None
                            record[name + ("_known_delta" if metric == "P0_failure" else "_delta")] = None
                else:
                    anchor = {**base, "policy_id": "secure_milp"}
                    probe = {**candidate, "policy_id": "__candidate__"}
                    original = deepening.policy_deltas([anchor, probe], ["secure_milp", "__candidate__"])[0]
                    record = {key[:-7] + "_reference" if key.endswith("_secure") else
                              "reference_observed_until_minute" if key == "secure_observed_until_minute" else key:
                              value for key, value in original.items()}
                    record.update(candidate_policy_id=pid, reference_policy_id=reference,
                                  reference_available=True)
                record.update({key: candidate[key] for key in
                               ("root_family_id", "analysis_class", "analysis_cohort", "design_axis",
                                "axis_value", "design_axis_value", "fixed_context_json")})
                output.append(record)
    return output


def _group_summary(rows, keys):
    output = previous._group_summary(rows, keys)
    grouped = defaultdict(list)
    for row in rows:
        grouped[tuple(row[key] for key in keys)].append(row)
    for summary in output:
        group = grouped[tuple(summary[key] for key in keys)]
        for name in ("gt_protection_trip_count", "minimum_output_interlock_count",
                     "no_export_interlock_count"):
            summary[name + "_observed_total"] = sum(row[name] for row in group)
        for window in WINDOWS:
            complete = [row for row in group if row[window + "_full_horizon"]]
            for metric in ("P0_max_temperature_C", "P1_max_temperature_C", "P2_max_temperature_C"):
                values = [row[window + "_" + metric] for row in complete]
                value = max(values, default=None)
                summary[window + "_" + metric + "_complete_case_max"] = value
                summary[window + "_" + metric + "_all_registered"] = value if len(complete) == len(group) else None
        summary["design_frequency_is_failure_probability"] = False
    return output


def _coordinate_key(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return (0, float(value))
    return (1, _canonical(value))


def _boundaries(rows):
    points, grouped = [], defaultdict(list)
    for row in rows:
        binary = row["full_P0_failure"]
        status = ("observed_failure" if binary == 1 else "tested_no_failure"
                  if binary == 0 and row["full_full_horizon"] else "unknown")
        point = {key: row[key] for key in ("scenario_id", "root_family_id", "analysis_class",
                    "policy_id", "analysis_cohort", "design_axis", "axis_value", "fixed_context_json")}
        point.update(p0_status=status, full_complete=row["full_full_horizon"],
                     full_P0_failure_minutes=row["full_P0_failure_minutes"],
                     observed_prefix_P0_failure_minutes=row["full_observed_prefix_P0_failure_minutes"])
        points.append(point)
        group = tuple(point[key] for key in ("analysis_cohort", "root_family_id", "analysis_class",
                      "policy_id", "design_axis", "fixed_context_json"))
        grouped[group].append(point)
    intervals = []
    names = ("analysis_cohort", "root_family_id", "analysis_class", "policy_id",
             "design_axis", "fixed_context_json")
    for key, subset in sorted(grouped.items(), key=lambda pair: _canonical(pair[0])):
        tested = sorted(subset, key=lambda row: (_coordinate_key(row["axis_value"]), row["scenario_id"]))
        changes = [{"left": a["axis_value"], "left_status": a["p0_status"],
                    "right": b["axis_value"], "right_status": b["p0_status"]}
                   for a, b in zip(tested, tested[1:])
                   if a["axis_value"] != b["axis_value"] and a["p0_status"] != b["p0_status"]
                   and _coordinate_key(a["axis_value"])[0] == _coordinate_key(b["axis_value"])[0] == 0
                   and "unknown" not in (a["p0_status"], b["p0_status"])]
        intervals.append({**dict(zip(names, key)), "registered_point_count": len(subset),
            "tested_values_json": [row["axis_value"] for row in tested],
            "tested_no_failure_values_json": [row["axis_value"] for row in tested if row["p0_status"] == "tested_no_failure"],
            "observed_failure_values_json": [row["axis_value"] for row in tested if row["p0_status"] == "observed_failure"],
            "unknown_values_json": [row["axis_value"] for row in tested if row["p0_status"] == "unknown"],
            "adjacent_status_changes_json": changes,
            "interval_semantics": "tested discrete coordinates only; untested values and gaps remain unknown"})
    return points, intervals


def _six(value):
    if value is None:
        return None
    vector = list(value)
    if len(vector) != 6 or any(not math.isfinite(float(v)) for v in vector):
        raise ValueError("Malformed saved six-port vector")
    return list(map(float, vector))


def _dispatch(value):
    return None if value is None else _six(six_targets(value))


def _difference(left, right):
    return None if left is None or right is None else [a - b for a, b in zip(left, right)]


def _changed(delta):
    return None if delta is None else any(abs(x) > ACTION_EFFECT_TOLERANCE_KW for x in delta)


def _decisions(scene, pid, payload):
    """Export a narrow current-input/command record, not arbitrary diagnostics."""
    output = []
    observed = float(payload["summary"]["observed_until_minute"])
    for decision in payload.get("decisions", []):
        now = float(decision["decision_time_min"])
        if not math.isfinite(now) or now < 0 or now > observed + previous.EPS:
            raise ValueError("Decision lies outside the actual observed prefix")
        features = {k: v for k, v in decision.get("features", {}).items() if k in feature_whitelist()}
        legal = legal_history([{"decision_time_min": now, "features": features,
            "feature_metadata": {k: decision.get("feature_metadata", {})[k] for k in features}}])[0]
        diagnostic = decision.get("policy_diagnostic") or {}
        request = decision.get("requested_command")
        if request is not None:
            validate_action(request)
        targets = (request or {}).get("targets", {})
        explicit = _six([targets[key] for key in ACTION_NAMES]) if set(targets) == set(ACTION_NAMES) else None
        proposed = _six(diagnostic.get("requested_six_targets_kW"))
        ask = explicit if explicit is not None else proposed
        command = decision.get("command_generated")
        plan = _dispatch(None if command is None else command.get("dispatch"))
        actual = _dispatch(decision.get("actual_execution"))
        anchor = _six(diagnostic.get("same_state_base_plan_targets_kW",
                                     diagnostic.get("secure_projected_six_targets_kW")))
        request_delta, plan_delta = _difference(ask, anchor), _difference(plan, anchor)
        mode = diagnostic.get("mode")
        row = {"scenario_id": scene["id"], "root_family_id": scene["root_family_id"],
            "analysis_class": scene["analysis_class"], "policy_id": pid,
            **_context(scene), "decision_time_min": now,
            "mode": mode if mode in {"prepare", "emergency", "recovery"} else None,
            "mode_reasons_json": [reason for reason in diagnostic.get("reasons", []) if reason in REASONS],
            "strategy_status": diagnostic.get("strategy_status"),
            "strategy_fallback": diagnostic.get("strategy_fallback"),
            "fallback_reason": diagnostic.get("fallback_reason"),
            "guard_active": diagnostic.get("guard_active"),
            "observed_cooling_to_electric_ratio": diagnostic.get("observed_cooling_to_electric_ratio"),
            "current_legal_grid_capacity_kW": diagnostic.get("current_legal_grid_capacity_kW"),
            "arrived_grid_port_kW": diagnostic.get("arrived_grid_port_kW"),
            "new_damage_observed": diagnostic.get("new_damage_observed"),
            "persistent_damage_is_not_repaired": diagnostic.get("persistent_damage_is_not_repaired"),
            "route": diagnostic.get("route") if diagnostic.get("route") in {
                "cooling_dominant_source_diversification", "current_fleet_preparation"} else None,
            "legal_current_features_json": legal["features"],
            "submitted_request_json": copy.deepcopy(request),
            "requested_targets_kW_json": ask,
            "submitted_target_presence_mask_json": [key in targets for key in ACTION_NAMES],
            "submitted_partial_targets_json": targets,
            "submitted_gt_loading_weight_USD_per_kWh": (request or {}).get("security", {}).get("gt_loading_penalty_USD_per_kWh", 0.) if request is not None else None,
            "same_state_base_plan_targets_kW_json": anchor,
            "projected_targets_kW_json": plan, "actual_targets_kW_json": actual,
            "requested_minus_same_state_plan_kW_json": request_delta,
            "projected_minus_same_state_plan_kW_json": plan_delta,
            "request_changed_from_same_state_plan": _changed(request_delta),
            "projected_plan_changed_from_same_state_plan": _changed(plan_delta),
            "request_is_execution": False,
            "generated_command_id": None if command is None else command.get("id"),
            "generated_minute": None if command is None else command.get("generated_minute"),
            "planned_arrival_minute": None if command is None else command.get("arrival_minute"),
            "arrived_command_record_count": len(decision.get("arrived_commands") or []),
            "parallel_execution_difference_is_not_same_state_causal_effect": True}
        output.append(row)
    return output


def _decision_comparisons(rows):
    indexed = {(row["scenario_id"], row["policy_id"], row["decision_time_min"]): row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError("Duplicate saved decision clock")
    for row in rows:
        for reference in REFERENCES:
            other = indexed.get((row["scenario_id"], reference, row["decision_time_min"]))
            row["submitted_request_differs_from_parallel_" + reference] = (
                None if other is None or row["submitted_request_json"] is None
                or other["submitted_request_json"] is None else
                row["submitted_request_json"] != other["submitted_request_json"])
            for field in ("requested_targets", "projected_targets", "actual_targets"):
                key = field + "_kW_json"
                delta = _difference(
                    row[key], None if other is None else other[key])
                row[field + "_minus_parallel_" + reference + "_kW_json"] = delta
                if field in {"projected_targets", "actual_targets"}:
                    name = "projected_plan" if field == "projected_targets" else "actual_execution"
                    row[name + "_differs_from_parallel_" + reference] = _changed(delta)


def _write_table(path, rows):
    fields = sorted({key for row in rows for key in row}) or ["scenario_id", "policy_id"]
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: previous._csv_value(value) for key, value in row.items()})


def analyze_batch(manifest, plan, results, audit, out_dir):
    """Require a complete passed raw audit, then emit new descriptive tables."""
    destination = Path(out_dir).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("Use a fresh state-dispatch analysis directory")
    batch = deepening._load_audited_batch(manifest, plan, results, audit)
    if batch["manifest"].get("policy_search_final_12_backgrounds_unopened") is False:
        raise ValueError("Original final backgrounds were opened")
    rows, decisions, event_rows = [], [], []
    for original in batch["row_list"]:
        scene = batch["scenes"][original["scenario_id"]]
        row = {**original, **_context(scene)}
        rows.append(row)
        raw = Path(row["raw_path"])
        if previous._sha(raw) != row["raw_sha256"]:
            raise ValueError("Raw SHA changed during analysis")
        payload = previous._open_raw(raw)
        decisions.extend(_decisions(scene, row["policy_id"], payload))
        for event in payload.get("events", []):
            if event.get("kind") in {"protection_trip", "no_export_interlock", "minimum_output_interlock"}:
                event_rows.append({"scenario_id": scene["id"], "policy_id": row["policy_id"],
                    "root_family_id": scene["root_family_id"], "analysis_class": scene["analysis_class"],
                    **_context(scene), "event_kind": event["kind"], "time_minute": event.get("time_minute"),
                    "device": event.get("device"), "stress_ratio": event.get("stress_ratio"),
                    "true_GT_protection_trip": event["kind"] == "protection_trip" and str(event.get("device", "")).startswith("GT_")})
    _decision_comparisons(decisions)
    deltas = _deltas(rows, batch["policy_ids"])
    groups = _group_summary(rows, ("analysis_cohort", "analysis_class", "policy_id"))
    roots = _group_summary(rows, ("analysis_cohort", "root_family_id", "analysis_class", "policy_id",
                                 "design_axis", "fixed_context_json"))
    points, intervals = _boundaries(rows)
    unknown = []
    for row in rows:
        reasons = [window + "_unknown" for window in WINDOWS if not row[window + "_full_horizon"]]
        if row["full_P0_failure"] == 1:
            reasons.append("observed_P0_failure")
        if row["status"] != "completed":
            reasons.append("noncompleted_status")
        if reasons:
            unknown.append({**row, "analysis_flags_json": reasons})
    tables = {"TRAJECTORY_RESULTS": rows, "POLICY_DELTAS": deltas,
        "COHORT_CLASS_POLICY_SUMMARY": groups, "ROOT_RESULTS": roots,
        "BOUNDARY_POINTS": points, "BOUNDARY_INTERVALS": intervals,
        "UNKNOWN_AND_FAILURES": unknown, "DECISION_EFFECTS": decisions,
        "PROTECTION_AND_INTERLOCK_EVENTS": event_rows}
    report = {"schema": SCHEMA, "registered_trajectory_count": len(rows),
        "registered_scenario_count": len(batch["scenario_ids"]),
        "unknown_full_trajectory_count": sum(not row["full_full_horizon"] for row in rows),
        "unknown_primary_trajectory_count": sum(not row["primary_full_horizon"] for row in rows),
        "policy_ids": batch["policy_ids"], "reference_policy_ids": list(REFERENCES),
        "target_names": list(previous.TARGET_NAMES), "score_windows": previous.WINDOWS,
        "manifest_path": str(Path(manifest).resolve()), "manifest_sha256": previous._sha(manifest),
        "plan_path": str(Path(plan).resolve()), "plan_sha256": previous._sha(plan),
        "audit_path": str(Path(audit).resolve()), "audit_sha256": previous._sha(audit),
        "analysis_source_sha256": {name: previous._sha(ROOT / name) for name in
            ("tools/state_dispatch/analysis.py", "tools/four_scenarios/deepening_analysis.py", "tools/four_scenarios/analysis.py")},
        "scenarios_by_cohort": dict(Counter(_context(batch["scenes"][sid])["analysis_cohort"] for sid in batch["scenario_ids"])),
        "independent_roots_by_cohort": {cohort: sorted({row["root_family_id"] for row in rows if row["analysis_cohort"] == cohort})
            for cohort in sorted({row["analysis_cohort"] for row in rows})},
        "new_independent_background_count": batch["manifest"].get("new_independent_background_count"),
        "design_frequency_is_failure_probability": False,
        "boundary_semantics": "Only measured discrete coordinates; no interpolated continuous safety range",
        "decision_metadata_is_posthoc_not_online_policy_input": True,
        "parallel_execution_difference_is_not_same_state_causal_effect": True,
        "unknown_values_are_null": True, "final_blind_opened": False,
        "table_row_counts": {name: len(values) for name, values in tables.items()},
        **{name.lower(): values for name, values in tables.items()}}
    destination.mkdir(parents=True, exist_ok=True)
    for name, values in tables.items():
        _write_table(destination / (name + ".csv"), values)
    with (destination / "SUMMARY.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "plan", "results", "audit", "out-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    report = analyze_batch(args.manifest, args.plan, args.results, args.audit, args.out_dir)
    print(json.dumps({key: report[key] for key in
                     ("registered_trajectory_count", "unknown_full_trajectory_count")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
