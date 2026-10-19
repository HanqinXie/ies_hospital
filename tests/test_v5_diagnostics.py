"""Frozen v4 evidence for the v5 same-state action-chain diagnosis."""
from pathlib import Path

import pytest

from tools.policy_search.diagnostics import build_action_diagnostic, write_action_diagnostic


ROOT = Path(__file__).resolve().parents[1]
V4 = ROOT / "outputs/v43_reliability_first_20261015_run01"


def test_weight_reaches_final_objective_but_not_the_saved_current_plan(tmp_path):
    report = build_action_diagnostic(V4, snapshot_indices=(2, 92, 139))
    assert report["registered_snapshot_count"] == 24
    assert report["official_status_counts"] == {"complete": 22, "unknown": 2}
    assert report["weight_probe_summary"]["all_weight_dispatches_equal_secure"] is True
    assert report["weight_probe_summary"]["weight_plan_changed_count"] == 0

    by_index = {row["index"]: row for row in report["representative_snapshots"]}
    first = by_index[2]
    high = next(row for row in first["weight_candidates"] if row["weight_USD_per_kWh"] == 10.0)
    assert high["rank4_objective_delta_USD"] == pytest.approx(311.9812576, abs=1e-6)
    assert high["rank4_objective_delta_matches_coefficient"] is True
    assert high["full_dispatch_equal_secure"] is True
    assert first["active_lock_rows"]["GT_3.minimum_stable"]["activity"] == pytest.approx(0.0, abs=1e-7)

    outage = by_index[92]
    assert outage["effective_current_grid_limit_kW"] == 0.0
    grid = next(row for row in outage["residual_candidates"] if row["candidate_id"] == "residual_grid_relief")
    assert grid["requested_grid_kW"] > 0.0
    assert grid["projected_grid_kW"] == 0.0
    balance = next(row for row in outage["residual_candidates"] if row["candidate_id"] == "residual_GT_balance")
    assert balance["max_six_port_plan_delta_kW"] == pytest.approx(23.8392064, abs=1e-6)
    assert balance["original_future_execution"]["command_executed_minute"] == 119.0
    assert balance["original_future_execution"]["next_command_executed_minute"] == 120.0
    assert balance["original_future_execution"]["effective_minutes_before_next_command"] == 1.0
    assert balance["original_future_label"] == "tie"

    assert by_index[139]["official_status"] == "unknown"
    assert by_index[139]["unknown_outcome_count"] == 16

    destination = tmp_path / "v5_action_diagnostic"
    path = write_action_diagnostic(V4, destination, snapshot_indices=(2, 92, 139))
    assert path == destination / "ACTION_CHAIN_DIAGNOSTIC.json"
    assert path.is_file()
    with pytest.raises(FileExistsError):
        write_action_diagnostic(V4, destination, snapshot_indices=(2, 92, 139))
