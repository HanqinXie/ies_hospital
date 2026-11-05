"""Three focused tests for report denominators and protection semantics."""
from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from tools.four_scenarios.analysis import _sha, _write_csv_new
from tools.control_improvement.ranking import TARGET_NAMES


def _module():
    try:
        return importlib.import_module("tools.state_dispatch.report_tables")
    except ModuleNotFoundError:
        return None


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _batch(tmp_path, name, *, unknown=False):
    directory = tmp_path / name / "analysis"
    directory.mkdir(parents=True)
    rows, events, decisions = [], [], []
    for sid, category in (("fault", "L3"), ("normal", "N2")):
        for pid, cost in (("secure_milp", 100.), ("priority_milp", 120.), ("state", 110.)):
            incomplete = unknown and sid == "fault" and pid == "state"
            row = {"scenario_id": sid, "root_family_id": "same_root", "analysis_class": category,
                "analysis_cohort": "development", "policy_id": pid, "status": "solver_error" if incomplete else "completed",
                "observed_until_minute": 300. if incomplete else 600., "event_end_minute": 200.,
                "recovery_30min": None if incomplete else True, "rescue_eligible": incomplete,
                "rescue_confirmed": None, "final_diesel_status": "OFF",
                "final_GT_previous_output_kW_json": [100., 100., 0.],
                "final_extra_faulted_devices_json": ["GT_3"],
                "gt_protection_trip_count": 1, "minimum_output_interlock_count": 1,
                "no_export_interlock_count": 1}
            for window in ("primary", "full"):
                row[window + "_full_horizon"] = not incomplete
                row[window + "_observed_minutes"] = 240. if incomplete else 360. if window == "primary" else 600.
                for metric in TARGET_NAMES:
                    row[window + "_" + metric] = None if incomplete else 0.
                row[window + "_P0_failure"] = 1 if incomplete else 0
                row[window + "_actual_energy_fee_USD"] = None if incomplete else cost
                row[window + "_secondary_GT_trips"] = None if incomplete else 999.
                for p in range(3):
                    row[f"{window}_P{p}_max_temperature_C"] = None if incomplete else 24. + p
                for p in range(4):
                    for carrier in "ehc":
                        row[f"{window}_observed_prefix_P{p}_{carrier}_ENS_kWh"] = 0.
                row[window + "_observed_prefix_P0_failure_minutes"] = 10. if incomplete else 0.
            rows.append(row)
            decisions.append({"scenario_id": sid, "policy_id": pid, "root_family_id": "same_root",
                "analysis_class": category, "analysis_cohort": "development", "decision_time_min": 75.,
                "mode": "prepare" if pid == "state" else None,
                "guard_active": True if pid == "state" else None, "strategy_fallback": False,
                "submitted_request_json": {"targets": {"gt_1": 100.}} if pid == "state" else {},
                "request_changed_from_same_state_plan": True if pid == "state" else None,
                "projected_plan_changed_from_same_state_plan": False if pid == "state" else None,
                "actual_execution_differs_from_parallel_secure_milp": True if pid == "state" else False})
            for kind, minute in (("protection_trip", 75.), ("no_export_interlock", 90.),
                                 ("minimum_output_interlock", 420.)):
                events.append({"scenario_id": sid, "policy_id": pid, "event_kind": kind,
                               "device": "GT_1", "time_minute": minute})
    points = [{"scenario_id": "fault", "root_family_id": "same_root", "policy_id": "state",
               "analysis_class": "L3", "analysis_cohort": "boundary", "axis_value": .4,
               "fixed_context_json": "{}", "p0_status": "tested_no_failure"}]
    intervals = [{"root_family_id": "same_root", "policy_id": "state", "analysis_cohort": "boundary",
                  "tested_values_json": [.4, .5], "adjacent_status_changes_json": [
                      {"left": .4, "right": .5, "left_status": "tested_no_failure", "right_status": "observed_failure"}],
                  "interval_semantics": "tested discrete coordinates only; gaps unknown"}]
    for filename, values in (("TRAJECTORY_RESULTS.csv", rows), ("PROTECTION_AND_INTERLOCK_EVENTS.csv", events),
                             ("BOUNDARY_POINTS.csv", points), ("BOUNDARY_INTERVALS.csv", intervals),
                             ("DECISION_EFFECTS.csv", decisions)):
        _write_csv_new(directory / filename, values)
    audit = directory.parent / "AUDIT.json"
    _write(audit, {"passed": True, "registered_pair_count": 6, "terminal_pair_count": 6,
                   "recomputed_pair_count": 6, "matched_initial_state_and_stream": True})
    _write(directory / "SUMMARY.json", {"registered_trajectory_count": 6,
        "audit_path": str(audit), "audit_sha256": _sha(audit), "trajectory_results": rows,
        "table_row_counts": {"TRAJECTORY_RESULTS": 6, "DECISION_EFFECTS": 6}, "final_blind_opened": False})
    return directory


def test_batch_denominators_remain_separate_and_unknown_is_not_zero(tmp_path):
    module = _module()
    assert module is not None, "Report-table aggregator is not implemented"
    a, b = _batch(tmp_path, "development", unknown=True), _batch(tmp_path, "development_v2")
    report = module.build_report_tables(tmp_path, batches={"development": a, "development_v2": b})
    groups = [r for r in report["batch_window_class_policy"] if r["window"] == "full"
              and r["analysis_class"] == "L3" and r["policy_id"] == "state"]
    assert len(groups) == 2
    assert all(r["registered_scenario_count"] == 1 and r["independent_root_count"] == 1 for r in groups)
    incomplete = next(r for r in groups if r["batch"] == "development")
    assert incomplete["unknown_window_count"] == 1
    assert incomplete["P0_failure_minutes_all_registered"] is None
    assert incomplete["P0_failure_minutes_observed_prefix_total"] == 10.
    assert incomplete["minimum_output_interlock_count_observed_prefix_total"] == 0
    pairs = [r for r in report["paired_comparisons"] if r["batch"] == "development_v2"
             and r["window"] == "full" and r["analysis_class"] == "N2" and r["candidate_policy_id"] == "state"]
    assert {r["reference_policy_id"]: r["actual_energy_fee_USD_delta_all_registered"] for r in pairs} == {
        "secure_milp": 10., "priority_milp": -10.}
    decision = next(r for r in report["decision_summary"] if r["batch"] == "development_v2"
                    and r["window"] == "full" and r["analysis_class"] == "L3" and r["policy_id"] == "state")
    assert decision["candidate_decisions"] == 1
    assert decision["strategy_fallback_true_count"] == 0
    assert decision["request_changed_from_same_state_plan_true_count"] == 1
    assert decision["projected_plan_changed_from_same_state_plan_true_count"] == 0
    assert decision["actual_execution_differs_from_parallel_secure_milp_true_count"] == 1


def test_window_events_do_not_confuse_true_GT_protection_with_interlocks(tmp_path):
    module = _module()
    assert module is not None
    path = _batch(tmp_path, "development")
    report = module.build_report_tables(tmp_path, batches={"development": path})
    groups = {r["window"]: r for r in report["batch_window_class_policy"] if
              r["analysis_class"] == "L3" and r["policy_id"] == "state"}
    assert groups["full"]["gt_protection_trip_count_all_registered"] == 1
    assert groups["full"]["no_export_interlock_count_all_registered"] == 1
    assert groups["full"]["minimum_output_interlock_count_all_registered"] == 1
    assert groups["primary"]["minimum_output_interlock_count_all_registered"] == 0
    assert groups["primary"]["gt_protection_trip_count_all_registered"] == 1
    assert groups["full"]["legacy_secondary_GT_trips_all_registered"] == 999.


def test_rebuild_is_explicit_and_boundary_coordinates_are_not_interpolated(tmp_path):
    module = _module()
    assert module is not None
    path = _batch(tmp_path, "development")
    report = module.build_report_tables(tmp_path, batches={"development": path})
    interval = report["boundary_intervals"][0]
    assert interval["tested_values_json"] == [.4, .5]
    assert interval["adjacent_status_changes_json"][0]["right"] == .5
    assert "safe_threshold" not in interval
    assert (tmp_path / "review/FINAL_METRICS.json").is_file()
    assert report["missing_expected_batches"] == ["development_v2", "diagnostics", "boundary", "confirmation"]
    with pytest.raises(FileExistsError):
        module.build_report_tables(tmp_path, batches={"development": path})
    rebuilt = module.build_report_tables(tmp_path, batches={"development": path}, replace=True)
    assert rebuilt["input_trajectory_count"] == 6
