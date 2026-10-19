"""Failure-preserving v4 branch aggregation contracts."""
import pytest

from tools.reliability_first.branches import aggregate_future_windows


def _window(p0, minutes, fee, *, valid=True):
    values = [float(p0), float(minutes)] + [0.0] * 16 + [float(fee)]
    return {"targets": values if valid else [None] * 19,
            "target_mask": [bool(valid)] * 19,
            "full_horizon": bool(valid)}


def test_aggregate_each_action_across_same_futures_before_delta():
    results = [
        {"future_id": "original", "candidate_id": "secure", "window": _window(0, 0, 10)},
        {"future_id": "stress", "candidate_id": "secure", "window": _window(1, 15, 20)},
        {"future_id": "original", "candidate_id": "shift", "window": _window(1, 15, 9)},
        {"future_id": "stress", "candidate_id": "shift", "window": _window(0, 0, 19)},
    ]
    rows = aggregate_future_windows(results, ["original", "stress"], ["secure", "shift"])
    assert rows[0]["aggregate_targets"][:2] == [1.0, 15.0]
    assert rows[1]["aggregate_targets"][:2] == [1.0, 15.0]
    assert rows[0]["delta_y"] == [0.0] * 19
    assert rows[1]["delta_y"][:2] == [0.0, 0.0]
    assert rows[1]["delta_y"][-1] == -1.0
    assert rows[1]["target_mask"] == [True] * 19


def test_unknown_future_is_retained_and_never_zero_filled():
    results = [
        {"future_id": "original", "candidate_id": "secure", "window": _window(0, 0, 10)},
        {"future_id": "stress", "candidate_id": "secure", "window": _window(0, 0, 10)},
        {"future_id": "original", "candidate_id": "shift", "window": _window(0, 0, 9)},
        {"future_id": "stress", "candidate_id": "shift", "window": _window(0, 0, 0, valid=False)},
    ]
    rows = aggregate_future_windows(results, ["original", "stress"], ["secure", "shift"])
    assert rows[0]["delta_y"] == [0.0] * 19
    assert rows[1]["aggregate_targets"] == [None] * 19
    assert rows[1]["delta_y"] == [None] * 19
    assert rows[1]["target_mask"] == [False] * 19
    assert rows[1]["future_status"] == ["known", "unknown"]


def test_missing_or_repeated_candidate_future_pair_rejected():
    rows = [{"future_id": "a", "candidate_id": "secure", "window": _window(0, 0, 1)}]
    with pytest.raises(ValueError, match="missing"):
        aggregate_future_windows(rows, ["a", "b"], ["secure"])
    with pytest.raises(ValueError, match="duplicate"):
        aggregate_future_windows(rows + rows, ["a"], ["secure"])
