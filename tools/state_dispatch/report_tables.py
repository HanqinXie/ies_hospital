"""Compact report tables from already-audited v9 analysis CSVs only.

Batch/cohort denominators stay separate. This does not rerun physics, select a
policy, read unanalysed holdout outcomes, or interpolate a boundary.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import io
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tools.four_scenarios import analysis as original

WINDOWS = original.WINDOWS
REFERENCES = ("secure_milp", "priority_milp")
EXPECTED = ("development", "development_v2", "diagnostics", "boundary", "confirmation")
EVENT_METRICS = ("gt_protection_trip_count", "no_export_interlock_count", "minimum_output_interlock_count")
METRICS = tuple(name for name in original.TARGET_NAMES if name != "P0_failure")
TEMPERATURES = frozenset({"P0_max_temperature_C", "P1_max_temperature_C", "P2_max_temperature_C"})
FLAGS = ("guard_active", "strategy_fallback", "request_changed_from_same_state_plan",
         "projected_plan_changed_from_same_state_plan") + tuple(
    name + "_differs_from_parallel_" + ref for ref in REFERENCES
    for name in ("submitted_request", "projected_plan", "actual_execution"))


def _value(text):
    if text == "" or text is None:
        return None
    if text in ("True", "False"):
        return text == "True"
    if text.startswith(("[", "{")):
        return json.loads(text)
    try:
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("Nonfinite report input")
        return value
    except ValueError:
        return text


def _read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return [{key: _value(value) for key, value in row.items()} for row in csv.DictReader(stream)]


def _metric_name(name):
    return "legacy_secondary_GT_trips" if name == "secondary_GT_trips" else name


def _event_values(events, row, window):
    if events is None:
        return {name: None for name in EVENT_METRICS}
    start, end = WINDOWS[window]
    subset = [e for e in events if e["scenario_id"] == row["scenario_id"]
              and e["policy_id"] == row["policy_id"] and start <= float(e["time_minute"]) < end
              and float(e["time_minute"]) <= float(row["observed_until_minute"]) + original.EPS]
    return {
        "gt_protection_trip_count": sum(e.get("event_kind") == "protection_trip"
            and str(e.get("device", "")).startswith("GT_") for e in subset),
        "no_export_interlock_count": sum(e.get("event_kind") == "no_export_interlock" for e in subset),
        "minimum_output_interlock_count": sum(e.get("event_kind") == "minimum_output_interlock" for e in subset)}


def _load_batch(name, directory):
    directory = Path(directory).resolve()
    if directory.is_file():
        directory = directory.parent
    table, summary_path = directory / "TRAJECTORY_RESULTS.csv", directory / "SUMMARY.json"
    summary = original._read_json(summary_path)
    rows = _read_csv(table)
    count = len(rows)
    audit_path = Path(summary["audit_path"])
    audit = original._read_json(audit_path)
    if (not count or summary.get("registered_trajectory_count") != count
            or summary.get("table_row_counts", {}).get("TRAJECTORY_RESULTS") != count
            or original._sha(audit_path) != summary.get("audit_sha256")
            or audit.get("passed") is not True or audit.get("matched_initial_state_and_stream") is not True
            or any(audit.get(key) != count for key in
                   ("registered_pair_count", "terminal_pair_count", "recomputed_pair_count"))
            or summary.get("final_blind_opened") is not False):
        raise ValueError("Complete passed analysis/audit denominator required: " + name)
    indexed = {(row["scenario_id"], row["policy_id"]): row for row in rows}
    sources = {(r["scenario_id"], r["policy_id"]): r for r in summary.get("trajectory_results", [])}
    if len(indexed) != count or set(indexed) != set(sources):
        raise ValueError("Analysis CSV identity/denominator differs from SUMMARY: " + name)
    keys = ("analysis_class", "analysis_cohort", "root_family_id", "status", "raw_sha256") + tuple(
        window + "_" + metric for window in WINDOWS for metric in original.TARGET_NAMES) + tuple(
        window + "_full_horizon" for window in WINDOWS)
    for key, row in indexed.items():
        if any(row.get(field) != sources[key].get(field) for field in keys):
            raise ValueError("Analysis CSV values differ from saved SUMMARY: " + name)
        row["batch"] = name
        row["analysis_directory"] = str(directory)
    events_path = directory / "PROTECTION_AND_INTERLOCK_EVENTS.csv"
    events = _read_csv(events_path) if events_path.is_file() else None
    for row in rows:
        for window in WINDOWS:
            row["_" + window + "_events"] = _event_values(events, row, window)
    bindings = {"batch": name, "analysis_directory": str(directory), "trajectory_count": count,
        "analysis_summary_sha256": original._sha(summary_path), "trajectory_csv_sha256": original._sha(table),
        "audit_path": str(audit_path), "audit_sha256": original._sha(audit_path),
        "protection_events_available": events is not None}
    companions = {}
    for stem in ("BOUNDARY_POINTS", "BOUNDARY_INTERVALS"):
        path = directory / (stem + ".csv")
        companions[stem.lower()] = [{"batch": name, **r} for r in _read_csv(path)] if path.is_file() else []
        if path.is_file():
            bindings[stem.lower() + "_sha256"] = original._sha(path)
    decisions, decision_binding = _decision_summary(name, directory, summary)
    bindings.update(decision_binding)
    return rows, bindings, companions, decisions


def _decision_summary(batch, directory, summary):
    path = directory / "DECISION_EFFECTS.csv"
    if not path.is_file():
        return [], {"decision_table_available": False}
    groups, count = {}, 0
    needed = {"analysis_cohort", "analysis_class", "policy_id", "decision_time_min", "mode",
              "submitted_request_json"} | set(FLAGS)
    # Stream once through the large decision CSV; retain only aggregate counts.
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for raw in csv.DictReader(stream):
            row = {key: _value(raw.get(key)) for key in needed}
            count += 1
            for window, (start, end) in WINDOWS.items():
                if row["decision_time_min"] is None or not start <= row["decision_time_min"] < end:
                    continue
                key = (row["analysis_cohort"], window, row["analysis_class"], row["policy_id"])
                item = groups.setdefault(key, {"decision_count": 0, "candidate_decisions": 0,
                    "mode_counts_json": Counter(), "explicit_target_request_decisions": 0,
                    **{flag + "_" + value + "_count": 0 for flag in FLAGS for value in ("true", "false", "unknown")}})
                item["decision_count"] += 1
                item["candidate_decisions"] += row["policy_id"] not in REFERENCES
                item["mode_counts_json"][row["mode"] or "not_recorded"] += 1
                request = row["submitted_request_json"]
                item["explicit_target_request_decisions"] += bool(isinstance(request, dict) and request.get("targets"))
                for flag in FLAGS:
                    value = "true" if row[flag] is True else "false" if row[flag] is False else "unknown"
                    item[flag + "_" + value + "_count"] += 1
    expected = summary.get("table_row_counts", {}).get("DECISION_EFFECTS")
    if expected is not None and expected != count:
        raise ValueError("Decision table denominator differs from analysis SUMMARY: " + batch)
    names = ("analysis_cohort", "window", "analysis_class", "policy_id")
    rows = [{"batch": batch, **dict(zip(names, key)), **item,
        "same_state_effects_use_candidate_current_base_plan": True,
        "parallel_differences_after_divergence_are_not_same_state_causal_effects": True}
        for key, item in sorted(groups.items())]
    return rows, {"decision_table_available": True, "decision_source_row_count": count,
                  "decision_csv_sha256": original._sha(path)}


def _aggregate(rows, keys):
    groups = defaultdict(list)
    for row in rows:
        for window in WINDOWS:
            groups[tuple(row.get(key) for key in keys) + (window,)].append(row)
    output = []
    for key, group in sorted(groups.items()):
        window = key[-1]
        complete = [r for r in group if r[window + "_full_horizon"] is True]
        item = {**dict(zip(keys + ("window",), key)),
            "registered_scenario_count": len(group), "independent_root_count": len({r["root_family_id"] for r in group}),
            "root_family_ids_json": sorted({r["root_family_id"] for r in group}),
            "complete_window_count": len(complete), "unknown_window_count": len(group) - len(complete),
            "known_P0_failure_case_count": sum(r[window + "_P0_failure"] == 1 for r in group),
            "known_P0_no_failure_case_count": sum(r[window + "_P0_failure"] == 0 for r in group),
            "unknown_P0_binary_count": sum(r[window + "_P0_failure"] is None for r in group),
            "recovery_whole_trajectory_applicable_count": sum(r.get("event_end_minute") is not None for r in group),
            "recovery_whole_trajectory_confirmed_count": sum(r.get("recovery_30min") is True for r in group),
            "recovery_whole_trajectory_unknown_count": sum(r.get("event_end_minute") is not None and r.get("recovery_30min") is None for r in group),
            "rescue_after_observed_failure_eligible_count": sum(r.get("rescue_eligible") is True for r in group),
            "rescue_after_observed_failure_confirmed_count": sum(r.get("rescue_confirmed") is True for r in group)}
        for metric in METRICS:
            field, name = window + "_" + metric, _metric_name(metric)
            values = [r[field] for r in complete if r.get(field) is not None]
            value = (max(values) if metric in TEMPERATURES else sum(values)) if values else None
            item[name + ("_complete_case_max" if metric in TEMPERATURES else "_complete_case_total")] = value
            item[name + "_all_registered"] = value if len(values) == len(group) else None
        prefix_fields = ("P0_failure_minutes",) + tuple(f"P{p}_{c}_ENS_kWh" for p in range(4) for c in "ehc")
        for metric in prefix_fields:
            values = [r.get(window + "_observed_prefix_" + metric) for r in group]
            item[metric + "_observed_prefix_total"] = sum(values) if all(v is not None for v in values) else None
        for metric in EVENT_METRICS:
            values = [r["_" + window + "_events"][metric] for r in group]
            known = [v for v in values if v is not None]
            item[metric + "_observed_prefix_total"] = sum(known) if known else None
            item[metric + "_all_registered"] = sum(known) if len(known) == len(group) and len(complete) == len(group) else None
        output.append(item)
    return output


def _paired(rows):
    by_batch = defaultdict(dict)
    for row in rows:
        by_batch[row["batch"]][(row["scenario_id"], row["policy_id"])] = row
    cases = []
    for batch, indexed in sorted(by_batch.items()):
        policies = sorted({pid for _, pid in indexed})
        for reference in REFERENCES:
            for pid in policies:
                if pid == reference:
                    continue
                ids = sorted({sid for sid, policy in indexed if policy in (reference, pid)})
                for sid in ids:
                    candidate, base = indexed.get((sid, pid)), indexed.get((sid, reference))
                    context = candidate or base
                    for window in WINDOWS:
                        a = None if candidate is None else candidate[window + "_P0_failure"]
                        b = None if base is None else base[window + "_P0_failure"]
                        category = ("unknown" if a is None or b is None else "new_P0_failure" if (a, b) == (1, 0)
                            else "avoided_P0_failure" if (a, b) == (0, 1) else "both_failed" if a == 1 else "both_no_failure")
                        complete = bool(candidate and base and candidate[window + "_full_horizon"] and base[window + "_full_horizon"])
                        item = {"batch": batch, "scenario_id": sid, "root_family_id": context["root_family_id"],
                            "analysis_cohort": context["analysis_cohort"], "analysis_class": context["analysis_class"],
                            "window": window, "candidate_policy_id": pid, "reference_policy_id": reference,
                            "complete_pair": complete, "P0_pair_classification": category,
                            "P0_failure_candidate": a, "P0_failure_reference": b}
                        for metric in METRICS + EVENT_METRICS:
                            name = _metric_name(metric)
                            def value(row):
                                if row is None:
                                    return None
                                return row["_" + window + "_events"][metric] if metric in EVENT_METRICS else row.get(window + "_" + metric)
                            left, right = value(candidate), value(base)
                            item[name + "_candidate"], item[name + "_reference"] = left, right
                            item[name + "_delta"] = left - right if complete and left is not None and right is not None else None
                        cases.append(item)
    groups = defaultdict(list)
    names = ("batch", "analysis_cohort", "window", "analysis_class", "candidate_policy_id", "reference_policy_id")
    for row in cases:
        groups[tuple(row[name] for name in names)].append(row)
    summaries = []
    for key, group in sorted(groups.items()):
        item = {**dict(zip(names, key)), "registered_case_count": len(group),
            "independent_root_count": len({r["root_family_id"] for r in group}),
            "complete_pair_count": sum(r["complete_pair"] for r in group),
            **{name + "_case_count": sum(r["P0_pair_classification"] == name for r in group)
               for name in ("new_P0_failure", "avoided_P0_failure", "both_failed", "both_no_failure", "unknown")}}
        for metric in METRICS + EVENT_METRICS:
            name = _metric_name(metric)
            values = [r[name + "_delta"] for r in group if r[name + "_delta"] is not None]
            value = (max(values) if metric in TEMPERATURES else sum(values)) if values else None
            item[name + ("_delta_complete_pair_max" if metric in TEMPERATURES else "_delta_complete_pair_total")] = value
            item[name + "_delta_all_registered"] = value if len(values) == len(group) else None
        summaries.append(item)
    return cases, summaries


def _csv_text(rows):
    stream = io.StringIO(newline="")
    fields = sorted({key for row in rows for key in row}) or ["batch", "scenario_id"]
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: original._csv_value(value) for key, value in row.items()})
    return stream.getvalue()


def build_report_tables(run, out_dir=None, *, batches=None, replace=False):
    run = Path(run).resolve()
    destination = Path(out_dir).resolve() if out_dir else run / "review"
    if batches is None:
        batches = {path.name: path / "analysis" for path in sorted(run.iterdir()) if path.is_dir()
                   and (path / "analysis/TRAJECTORY_RESULTS.csv").is_file()
                   and (path / "analysis/SUMMARY.json").is_file()}
    if not batches:
        raise ValueError("No completed audited analysis tables available")
    rows, inputs, points, intervals, decision_summary = [], [], [], [], []
    for name, directory in sorted(batches.items()):
        values, binding, companions, decisions = _load_batch(name, directory)
        rows.extend(values); inputs.append(binding)
        points.extend(companions["boundary_points"]); intervals.extend(companions["boundary_intervals"])
        decision_summary.extend(decisions)
    grouped = _aggregate(rows, ("batch", "analysis_cohort", "analysis_class", "policy_id"))
    root_results = _aggregate(rows, ("batch", "analysis_cohort", "root_family_id", "analysis_class", "policy_id"))
    paired_cases, comparisons = _paired(rows)
    normal = []
    for row in grouped:
        if row["analysis_class"] != "N2":
            continue
        value = {key: row[key] for key in ("batch", "analysis_cohort", "window", "policy_id",
            "registered_scenario_count", "independent_root_count", "unknown_window_count",
            "actual_energy_fee_USD_all_registered", "known_P0_failure_case_count")}
        for ref in REFERENCES:
            comparison = next((r for r in comparisons if r["batch"] == row["batch"]
                and r["analysis_cohort"] == row["analysis_cohort"] and r["window"] == row["window"]
                and r["analysis_class"] == "N2" and r["candidate_policy_id"] == row["policy_id"]
                and r["reference_policy_id"] == ref), None)
            value["actual_energy_fee_USD_delta_vs_" + ref] = (0. if row["policy_id"] == ref
                and row["unknown_window_count"] == 0 else None if comparison is None else comparison["actual_energy_fee_USD_delta_all_registered"])
        normal.append(value)
    terminal = [{key: value for key, value in row.items() if key in
        {"batch", "analysis_cohort", "scenario_id", "analysis_class", "root_family_id", "policy_id", "status",
         "event_end_minute", "observed_until_minute", "axis_value", "design_axis", "fixed_context_json"}
        or key.startswith(("final_", "recovery_", "rescue_", "post_event_", "minutes_to_", "sustained_to_"))}
        for row in rows]
    report = {"schema": "state_dispatch.available_audited_report_tables.v1", "inputs": inputs,
        "report_tables_source_sha256": original._sha(Path(__file__)),
        "loaded_batches": sorted(batches), "missing_expected_batches": [name for name in EXPECTED if name not in batches],
        "scope": "Available audited batches only; no pooled probability or independent-background claim",
        "input_trajectory_count": len(rows), "batch_window_class_policy": grouped,
        "root_window_class_policy": root_results, "paired_cases": paired_cases,
        "paired_comparisons": comparisons, "normal_n2_costs": normal,
        "recovery_and_terminal": terminal, "boundary_points": points, "boundary_intervals": intervals,
        "decision_summary": decision_summary,
        "window_event_counting": "start <= event_time < end; true GT protection and both interlocks separate",
        "recovery_terminal_scope": "Whole observed trajectory, separate from each scoring window",
        "boundary_semantics": "Copied tested discrete coordinates only; no interpolation",
        "parallel_differences_are_not_same_state_causal_effects": True,
        "unknown_metrics_remain_null": True, "holdout_execution_performed": False}
    tables = {"BATCH_WINDOW_CLASS_POLICY": grouped, "ROOT_WINDOW_CLASS_POLICY": root_results,
        "PAIRED_CASES": paired_cases, "PAIRED_COMPARISONS": comparisons, "NORMAL_N2_COSTS": normal,
        "RECOVERY_AND_TERMINAL": terminal, "FINAL_BOUNDARY_POINTS": points,
        "FINAL_BOUNDARY_INTERVALS": intervals, "DECISION_SUMMARY": decision_summary}
    artifacts = {name + ".csv": _csv_text(values) for name, values in tables.items()}
    artifacts["FINAL_METRICS.json"] = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    lines = ["# 已审计批次数据表", "", "| batch | trajectories | decision rows |", "|---|---:|---:|"]
    lines += [f"| {r['batch']} | {r['trajectory_count']} | {r.get('decision_source_row_count')} |" for r in inputs]
    lines += ["", "未纳入批次：" + ", ".join(report["missing_expected_batches"]), "",
              "分母按batch/cohort/window/class/policy分别保留；未知为空；真实GT保护与两类联锁分开。",
              "同状态请求/投影效果与平行轨迹执行差异分别统计。边界仅列已测离散坐标。", ""]
    artifacts["FINAL_TABLE_INDEX.md"] = "\n".join(lines)
    if not replace and any((destination / name).exists() for name in artifacts):
        raise FileExistsError("Derived report tables exist; use replace=True to rebuild explicitly")
    destination.mkdir(parents=True, exist_ok=True)
    for name, contents in artifacts.items():
        temporary = destination / (name + ".tmp")
        temporary.write_text(contents, encoding="utf-8-sig" if name.endswith(".csv") else "utf-8")
        temporary.replace(destination / name)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--batch", action="append", default=[], help="Explicit name=analysis-directory, repeatable")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    batches = dict(value.split("=", 1) for value in args.batch) if args.batch else None
    report = build_report_tables(args.run, args.out_dir, batches=batches, replace=args.replace)
    print(json.dumps({"loaded_batches": report["loaded_batches"],
                      "input_trajectory_count": report["input_trajectory_count"],
                      "missing_expected_batches": report["missing_expected_batches"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
