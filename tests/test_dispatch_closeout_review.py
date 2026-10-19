"""Read-only closeout report reuse and linked request/arrival execution evidence."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def review():
    try:
        result = importlib.import_module("tools.dispatch_closeout.review")
    except ModuleNotFoundError:
        result = None
    assert result is not None, "Closeout read-only report tool missing"
    return result


def test_expected_batches_match_two_development_and_two_confirmation_halves():
    assert review().EXPECTED == (
        "development_normal", "development_mechanism", "confirmation_a", "confirmation_b")


def action(gt):
    return {"gt_electric_kW": [gt, 100., 0.], "grid_import_kW": 400.,
            "diesel_kW": 0., "absorption_cooling_kW": 120.}


def test_linked_arrival_is_separate_from_step_execution_and_soft_request():
    payload = {"summary": {"observed_until_minute": 60.}, "decisions": [{
        "decision_time_min": 0., "requested_command": {"targets": dict(zip(
            ("gt_1", "gt_2", "gt_3", "grid_import", "diesel", "absorption_cooling_kW"),
            [200., 100., 0., 400., 0., 120.]))},
        "command_generated": {"id": 4, "arrival_minute": 20., "dispatch": action(180.)},
        "actual_execution": action(90.), "policy_diagnostic": {
            "feedback_family": "dispatch_closeout", "critical_heat_kW": 150.,
            "same_state_base_plan_targets_kW": [100., 100., 0., 400., 0., 120.],
            "clock_exposed": True, "strategy_fallback": False,
            "diesel_sustainability": {"support_hours": 2.}}}],
        "events": [{"kind": "command_executed", "command_id": 4, "time_minute": 20.,
                    "executed_dispatch": action(160.)}]}
    context = {"scenario_id": "s", "analysis_class": "L2", "analysis_cohort": "development",
               "policy_id": "execution_reserve", "root_family_id": "r"}
    row = review().decision_rows("development_mechanism", context, payload)[0]
    assert row["requested_targets_kW_json"][0] == 200.
    assert row["projected_targets_kW_json"][0] == 180.
    assert row["same_step_actual_targets_kW_json"][0] == 90.
    assert row["linked_arrival_executed_targets_kW_json"][0] == 160.
    assert row["same_state_request_changed"] is True
    assert row["same_state_projected_plan_changed"] is True
    assert row["projection_adjusted_soft_request"] is True
    assert row["linked_arrival_clamp_changed_generated_plan"] is True
    assert row["actual_execution_is_not_the_new_request"] is True


def test_missing_new_diagnostic_and_unobserved_arrival_remain_null():
    context = {"scenario_id": "s", "analysis_class": "N2", "analysis_cohort": "development",
               "policy_id": "secure_milp", "root_family_id": "r"}
    rows = review().decision_rows("development_normal", context, {
        "summary": {"observed_until_minute": 15.}, "decisions": [{"decision_time_min": 0.,
        "requested_command": {}, "command_generated": {"id": 1, "dispatch": action(100.)}}], "events": []})
    assert rows[0]["critical_heat_kW"] is None
    assert rows[0]["same_state_request_changed"] is None
    assert rows[0]["linked_arrival_clamp_changed_generated_plan"] is None
    assert rows[0]["generated_command_executed_in_observed_prefix"] is False


def test_decision_summary_preserves_unknown_and_individual_units():
    context = {"scenario_id": "s", "analysis_class": "L2", "analysis_cohort": "development",
               "policy_id": "execution_reserve", "root_family_id": "r"}
    payload = {"summary": {"observed_until_minute": 120.}, "decisions": [{
        "decision_time_min": t, "policy_diagnostic": {"feedback_family": "dispatch_closeout",
            "critical_heat_kW": h, "clock_exposed": flag}}
        for t, h, flag in ((0., 100., False), (60., 200., True), (75., 300., None))]}
    rows = review().decision_rows("development_mechanism", context, payload)
    summary = review().summarize_decisions(rows)
    full = next(r for r in summary if r["window"] == "full")
    assert full["decision_count"] == 3
    assert full["clock_exposed_true_count"] == full["clock_exposed_false_count"] == full["clock_exposed_unknown_count"] == 1
    assert full["critical_heat_kW_min"] == 100. and full["critical_heat_kW_max"] == 300.
    assert "safety_score" not in full


def test_build_reuses_old_metric_tables_without_claiming_pending_confirmation(tmp_path, monkeypatch):
    tool = review()
    fixture_spec = importlib.util.spec_from_file_location(
        "closeout_review_raw_fixture", ROOT / "tests/test_state_dispatch_analysis.py")
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    batch = fixture.batch.__wrapped__(tmp_path)
    from tools.state_dispatch import analysis
    directory = tmp_path / "analyzed"
    analysis.analyze_batch(*batch, directory)
    report = tool.build_report_tables(tmp_path, tmp_path / "review", batches={"development_mechanism": directory})
    assert report["input_trajectory_count"] == 12
    assert report["missing_expected_batches"] == ["development_normal", "confirmation_a", "confirmation_b"]
    assert report["unknown_metrics_remain_null"] is True
    assert report["physical_trajectories_started"] == 0
    assert (tmp_path / "review/CLOSEOUT_REVIEW.json").is_file()
    assert (tmp_path / "review/STATE_BASIS_DECISIONS.csv").is_file()
    assert (tmp_path / "review/RECOVERY_AND_TERMINAL.csv").is_file()
    assert any(r["P0_pair_classification"] == "new_P0_failure" for r in report["paired_cases"])
    with pytest.raises(FileExistsError):
        tool.build_report_tables(tmp_path, tmp_path / "review", batches={"development_mechanism": directory})


def test_review_rejects_raw_sha_drift_before_emitting_tables(tmp_path):
    tool = review()
    fixture_spec = importlib.util.spec_from_file_location(
        "closeout_review_drift_fixture", ROOT / "tests/test_state_dispatch_analysis.py")
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    batch = fixture.batch.__wrapped__(tmp_path)
    from tools.state_dispatch import analysis
    directory = tmp_path / "analyzed"
    report = analysis.analyze_batch(*batch, directory)
    Path(report["trajectory_results"][0]["raw_path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Raw SHA"):
        tool.build_report_tables(tmp_path, tmp_path / "review", batches={"development_mechanism": directory})
    assert not (tmp_path / "review/CLOSEOUT_REVIEW.json").exists()
