"""Solver-free regression of saved v9 contract counterexamples, without reruns."""
import importlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def evidence():
    assert (ROOT / "tools/dispatch_closeout/contract_probe.py").is_file(), (
        "The bounded closeout contract probe has not been implemented"
    )
    return importlib.import_module("tools.dispatch_closeout.contract_probe").run_probe(ROOT)


def test_day298_small_minimum_difference_is_preserved(evidence):
    failures = [r for r in evidence["day298"] if r["policy_id"] != "priority_milp"]
    assert len(failures) == 4
    for row in failures:
        assert row["generated_before_minimum_interlock"]
        assert row["minimum_deficit_kW"] == pytest.approx(0.155136, abs=1e-9)
        assert row["failure_intervals"] == [[182.5, 195.0]]
        assert row["P0_h_ENS_kWh"] > 32


def test_arrival_revalidation_does_not_reoptimize_heat(evidence):
    for row in evidence["arrival_method_probes"]:
        assert row["real_boundary_called"]
        assert row["superseded_command_ids"] == [12]
        assert row["active_command_id"] == 13
        assert row["actual_gt_kW"] == [0.0, 0.0, 0.0]
        assert row["actual_boiler_lbph"] == 0.0
        assert row["old_command_boiler_lbph"] > 900


def test_fuel_recurrence_is_not_first_recovery(evidence):
    rows = {r["policy_id"]: r for r in evidence["day051_development"]}
    candidate = rows["state_guarded_spread"]
    assert candidate["P0_failure_minutes"] == pytest.approx(52.5)
    assert candidate["failure_intervals"][-3:] == [[285.0, 300.0], [300.0, 315.0], [315.0, 330.0]]
    assert candidate["diesel_energy_at_270_kWh"] == pytest.approx(37.5325815, abs=1e-5)
    assert rows["priority_milp"]["P0_failure_minutes"] == pytest.approx(22.5)
    assert rows["secure_milp"]["P0_failure_minutes"] == pytest.approx(22.5)


def test_overproduction_prefix_does_not_fill_unknown_tail(evidence):
    rows = {r["policy_id"]: r for r in evidence["day051_confirmation"]}
    for pid in ("secure_milp", "state_guarded_spread"):
        row = rows[pid]
        assert row["observed_until_minute"] == 277.5
        assert row["minimum_unabsorbable_surplus_kW"] > 18
        assert row["full_failure_minutes"] is None
        assert row["full_ENS_kWh"] is None
        assert row["full_tail_unknown"] is True
    assert rows["secure_milp"]["minimum_unabsorbable_surplus_kW"] == pytest.approx(18.794540817, abs=1e-8)
    assert rows["state_guarded_spread"]["minimum_unabsorbable_surplus_kW"] == pytest.approx(298.310261035, abs=1e-8)
    assert evidence["new_trajectory_attempts"] == evidence["new_MILP_calls"] == 0
    assert evidence["current_state_auxiliary_LP_queries"] == 2
    assert evidence["final_blind_opened"] is False
