"""Focused regression for read-only v5 raw-score verification."""

from tools.policy_search.score_recheck import score_differences


def _score(*, full=True):
    values = [0.0] * 19 if full else [1, *([None] * 18)]
    return {
        "start_minute": 60.0,
        "horizon_minutes": 360.0,
        "full_horizon": full,
        "unknown_after_window_prefix": not full,
        "observed_minutes": 360.0 if full else 45.0,
        "targets": values,
        "target_mask": [value is not None for value in values],
        "observed_prefix_failure_minutes": 15.0,
    }


def test_exact_recomputed_score_and_unknown_mask_preserved():
    complete = _score()
    truncated = _score(full=False)
    assert score_differences(complete, dict(complete), "primary_score") == []
    assert score_differences(truncated, dict(truncated), "primary_score") == []


def test_tampered_target_or_unknown_imputation_is_detected():
    recomputed = _score()
    stored = _score()
    stored["targets"] = list(stored["targets"])
    stored["targets"][2] = 0.000001
    assert any("target_2" in difference for difference in
               score_differences(stored, recomputed, "primary_score"))

    recomputed = _score(full=False)
    stored = _score(full=False)
    stored["targets"] = list(stored["targets"])
    stored["targets"][1] = 0.0
    assert any("target_1" in difference for difference in
               score_differences(stored, recomputed, "primary_score"))


def test_score_metadata_and_mask_mismatch_are_detected():
    recomputed = _score()
    stored = _score()
    stored["target_mask"] = [True] * 18 + [False]
    stored["observed_minutes"] = 359.999999
    differences = score_differences(stored, recomputed, "primary_score")
    assert any("target_mask" in difference for difference in differences)
    assert any("observed_minutes" in difference for difference in differences)
