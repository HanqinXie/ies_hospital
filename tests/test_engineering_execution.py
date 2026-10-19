"""v14 GT execution-clock contracts; no annual inputs or physical simulation.

These clocks account for actual electrical state changes. They do not dispatch
equipment, grant repair authority, change protection thresholds, or edit ENS.
The initial age convention is an explicit research initial condition.
"""
import importlib
import json
import math
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def ledger(initial, **kwargs):
    """Missing implementation is an explicit red assertion, never a skip."""
    try:
        module = importlib.import_module("tools.engineering_model.execution")
    except ModuleNotFoundError as error:
        pytest.fail(f"v14 GTClockLedger is not implemented: {error}", pytrace=False)
    assert hasattr(module, "GTClockLedger"), "v14 GTClockLedger is not implemented"
    return module.GTClockLedger(initial, **kwargs)


def test_initial_online_and_offline_ages_are_explicitly_satisfied():
    clocks = ledger([100.0, 0.0, 200.0], time_minute=7.0,
                    minimum_up_minutes=60.0, minimum_down_minutes=30.0)
    assert clocks.can_stop(0, 7.0)
    assert clocks.can_stop(2, 7.0)
    assert clocks.earliest_start(1, 7.0) == pytest.approx(7.0)
    # Constructing an already-running initial state is not a new startup.
    assert clocks.commit([100.0, 0.0, 200.0], 7.0) == []


def test_actual_edges_start_minimum_up_and_down_clocks():
    clocks = ledger([100.0, 0.0, 0.0])
    events = clocks.commit([0.0, 200.0, 0.0], 10.0)
    assert len(events) == 2
    assert clocks.earliest_start(0, 10.0) == pytest.approx(40.0)
    assert not clocks.can_stop(1, 69.999)
    assert clocks.can_stop(1, 70.0)
    assert clocks.earliest_start(0, 50.0) == pytest.approx(50.0)


def test_repeated_states_and_positive_output_changes_do_not_reset_ages():
    clocks = ledger([100.0, 0.0, 0.0])
    assert len(clocks.commit([100.0, 200.0, 0.0], 10.0)) == 1
    assert clocks.commit([120.0, 225.0, 0.0], 20.0) == []
    assert clocks.commit([120.0, 225.0, 0.0], 20.0) == []
    assert not clocks.can_stop(1, 69.999)
    assert clocks.can_stop(1, 70.0)
    assert len(clocks.commit([0.0, 225.0, 0.0], 30.0)) == 1
    assert clocks.commit([0.0, 225.0, 0.0], 35.0) == []
    assert clocks.earliest_start(0, 35.0) == pytest.approx(60.0)


def test_forced_trip_records_off_edge_even_while_minimum_up_is_active():
    clocks = ledger([0.0, 0.0, 0.0])
    clocks.commit([200.0, 0.0, 0.0], 5.0)
    assert not clocks.can_stop(0, 6.0)
    assert len(clocks.commit([0.0, 0.0, 0.0], 6.0, reason="forced_trip")) == 1
    assert clocks.earliest_start(0, 6.0) == pytest.approx(36.0)
    # The ledger records actual truth; protection cannot be blocked by min-up.
    assert clocks.commit([0.0, 0.0, 0.0], 10.0, reason="actual_execution") == []
    assert clocks.earliest_start(0, 10.0) == pytest.approx(36.0)


def test_unit_clocks_are_independent_and_restarts_begin_new_up_intervals():
    clocks = ledger([0.0, 0.0, 0.0])
    clocks.commit([100.0, 0.0, 0.0], 5.0)
    clocks.commit([100.0, 200.0, 0.0], 10.0)
    assert clocks.can_stop(0, 65.0)
    assert not clocks.can_stop(1, 65.0)
    assert clocks.earliest_start(2, 65.0) == pytest.approx(65.0)
    clocks.commit([0.0, 200.0, 0.0], 65.0)
    assert clocks.earliest_start(0, 65.0) == pytest.approx(95.0)
    clocks.commit([150.0, 200.0, 0.0], 95.0)
    assert not clocks.can_stop(0, 154.999)
    assert clocks.can_stop(0, 155.0)
    assert clocks.can_stop(1, 95.0)


@pytest.mark.parametrize("bad_time", [4.0, -1.0, math.nan, math.inf, -math.inf])
def test_invalid_commit_time_is_rejected_without_partial_clock_mutation(bad_time):
    clocks = ledger([0.0, 0.0, 0.0])
    clocks.commit([100.0, 0.0, 0.0], 5.0)
    before = clocks.as_dict(5.0)
    with pytest.raises(ValueError):
        clocks.commit([0.0, 0.0, 0.0], bad_time)
    assert clocks.as_dict(5.0) == before
    assert not clocks.can_stop(0, 64.999)
    assert clocks.can_stop(0, 65.0)


@pytest.mark.parametrize("bad_powers", [
    [math.nan, 0.0, 0.0],
    [100.0, math.inf, 0.0],
    [100.0, 0.0, -math.inf],
    [-1.0, 0.0, 0.0],
    [100.0, 0.0],
])
def test_invalid_actual_powers_are_rejected_transactionally(bad_powers):
    clocks = ledger([0.0, 0.0, 0.0])
    clocks.commit([100.0, 0.0, 0.0], 5.0)
    before = clocks.as_dict(5.0)
    with pytest.raises(ValueError):
        clocks.commit(bad_powers, 6.0)
    assert clocks.as_dict(5.0) == before
    assert not clocks.can_stop(0, 64.999)
    assert clocks.can_stop(0, 65.0)


def test_snapshots_are_serializable_and_queries_do_not_advance_execution_time():
    initial = [0.0, 0.0, 0.0]
    clocks = ledger(initial)
    initial[0] = 300.0
    assert clocks.commit([0.0, 0.0, 0.0], 0.0) == []
    clocks.commit([100.0, 0.0, 0.0], 5.0)
    snapshot = clocks.as_dict(1000.0)
    assert isinstance(snapshot, dict)
    json.dumps(snapshot, allow_nan=False)
    assert clocks.can_stop(0, 1000.0)
    # Offline diagnostics at a later hypothetical time must not mutate the
    # actual event clock or prevent a real next observation at minute 6.
    assert clocks.commit([110.0, 0.0, 0.0], 6.0) == []
    assert not clocks.can_stop(0, 64.999)
    assert clocks.can_stop(0, 65.0)
