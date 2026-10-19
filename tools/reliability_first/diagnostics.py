"""Causal snapshot labels and finite-candidate opportunity diagnostics.

These functions read *actual* saved subsegments and branch outcomes. They do
not relabel historical datasets, invent unknown endings, or give policies a
future event script. An ex-post best candidate is limited to the registered
candidate/stress set and is never an online controller or safety certificate.
"""
from __future__ import annotations

import math
from collections import defaultdict


_CARRIERS = ("e", "h", "c")
_PRIORITIES = ("P0", "P1", "P2", "P3")
_OUTCOME_KEYS = ("P0_failure", "P0_failure_minutes", "P0_e_ENS_kWh",
                 "P0_h_ENS_kWh", "P0_c_ENS_kWh")


def _segments(payload: dict) -> tuple[list[dict], float]:
    rows = sorted(payload["segments"], key=lambda s: float(s["start_minute"]))
    cursor = 0.0
    for s in rows:
        left, right = float(s["start_minute"]), float(s["end_minute"])
        if not math.isclose(left, cursor, abs_tol=1e-7) or not right > left:
            raise ValueError("Original subsegments are not contiguous")
        if len(s["unserved_kW"]) != 4 or any(len(p) != 3 for p in s["unserved_kW"]):
            raise ValueError("Actual ENS subsegment must have four priorities and three carriers")
        cursor = right
    observed = float(payload["summary"]["observed_until_minute"])
    if not math.isclose(observed, cursor, abs_tol=1e-7):
        raise ValueError("Observed endpoint disagrees with original subsegments")
    return rows, observed


def snapshot_state_labels(payload: dict, t: float, window: float = 120) -> dict:
    """Separate prefix history/current state from future first event and loss.

    ``future_first_failure_binary`` is a *pre-first-failure* task only.
    ``post_recovery_refailure_binary`` is a different task. A currently failed
    state has neither label, but its observed future loss remains measurable.
    Complete future metrics require the whole [t,t+window) subsegment window;
    an observed positive is retained even if later follow-up is censored.
    """
    segments, observed = _segments(payload)
    t, window = float(t), float(window)
    if not math.isfinite(t + window) or t < 0 or window <= 0 or t > observed + 1e-7:
        raise ValueError("Snapshot/window outside observed prefix")
    if any(s["start_minute"] < t < s["end_minute"] for s in segments):
        raise ValueError("Snapshot is not an original decision/subsegment boundary")
    past = [s for s in segments if float(s["end_minute"]) <= t + 1e-7]
    ever = any(bool(s["classification"]["p0_guarantee_failure"]) for s in past)
    current = bool(past[-1]["classification"]["p0_guarantee_failure"]) if past else False
    recovered = ever and not current
    end = t + window
    duration = 0.0
    covered = 0.0
    ens = [[0.0] * 3 for _ in range(4)]
    first_new = None
    was_failed = current
    for s in segments:
        left = max(t, float(s["start_minute"]))
        right = min(end, float(s["end_minute"]))
        overlap = max(0.0, right - left)
        if not overlap:
            continue
        covered += overlap
        failed = bool(s["classification"]["p0_guarantee_failure"])
        if failed:
            duration += overlap
            if not was_failed and first_new is None:
                first_new = left
        was_failed = failed
        for priority in range(4):
            for carrier in range(3):
                value = float(s["unserved_kW"][priority][carrier])
                if not math.isfinite(value) or value < -1e-8:
                    raise ValueError("Invalid actual unserved power")
                ens[priority][carrier] += max(0.0, value) * overlap / 60.0
    complete = math.isclose(covered, window, abs_tol=1e-7)
    known_positive = first_new is not None
    return {
        "snapshot_minute": t,
        "window_minutes": window,
        "observed_until_minute": observed,
        "source_status": payload["summary"].get("status"),
        "ever_failed_before_or_at_snapshot": ever,
        "current_failed_at_snapshot": current,
        "recovered_at_snapshot": recovered,
        "future_first_failure_binary": (
            None if ever else 1 if known_positive else 0 if complete else None),
        "post_recovery_refailure_binary": (
            1 if known_positive else 0 if complete else None) if recovered else None,
        "first_new_failure_onset_minute": first_new,
        "future_window_complete": complete,
        "future_failure_minutes": duration if complete else None,
        "observed_prefix_future_failure_minutes": duration,
        "future_ENS_kWh_by_priority_carrier": ens if complete else None,
        "observed_prefix_future_ENS_kWh_by_priority_carrier": ens,
        "unknown_reason": None if complete else "future_window_censored_or_unreached",
        "first_event_semantics": "First new P0-failing subsegment onset at/after current boundary; not an already-failed episode",
        "loss_semantics": "Original subsegment overlap-integrated electrical/heating/cooling ENS; unknown full windows are null",
    }


def _outcome_values(row: dict) -> dict | None:
    source = row
    if "target_names" in row and "targets" in row:
        source = dict(zip(row["target_names"], row["targets"]))
    if row.get("status") not in ("completed", "bounded_observation_completed"):
        return None
    try:
        values = {key: float(source[key]) for key in _OUTCOME_KEYS}
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) and v >= -1e-8 for v in values.values()):
        return None
    return values


def _lex_compare(actual: dict, reference: dict, *, minute_resolution: float,
                 ens_resolution: float) -> int:
    """Return -1/0/+1 for candidate better/tie/worse in registered units."""
    for key in _OUTCOME_KEYS:
        difference = actual[key] - reference[key]
        resolution = (0.0 if key == "P0_failure" else minute_resolution
                      if key == "P0_failure_minutes" else ens_resolution)
        if difference < -resolution:
            return -1
        if difference > resolution:
            return 1
    return 0


def opportunity_rows(rows: list[dict], *, baseline_candidate_id: str,
                     minute_resolution: float = 1e-6,
                     ens_resolution: float = 0.01) -> list[dict]:
    """Keep every finite-candidate/stress result and compare paired outcomes.

    A candidate is never selected separately for each realized future. The
    pressure set has no validated probability weights: this reports paired
    better/tie/worse counts and worst counterexamples, not expected safety or
    a chance constraint. ``predicted_improvement`` is optional and diagnosed
    without changing any saved true label.
    """
    if not baseline_candidate_id or minute_resolution < 0 or ens_resolution < 0:
        raise ValueError("Named baseline and nonnegative research resolutions required")
    by_candidate: dict[str, dict] = defaultdict(dict)
    for row in rows:
        candidate, future = row["candidate_id"], row["future_id"]
        if future in by_candidate[candidate]:
            raise ValueError("Duplicate candidate/future branch result")
        by_candidate[candidate][future] = row
    if baseline_candidate_id not in by_candidate:
        raise ValueError("Actual executable baseline absent from candidate result set")
    futures = sorted({fid for candidate in by_candidate.values() for fid in candidate}, key=str)
    out = []
    for candidate in sorted(by_candidate):
        better = worse = tied = unknown = false_improvement = 0
        per_future = []
        execution_classes = set()
        predicted = {r["predicted_improvement"] for r in by_candidate[candidate].values()
                     if r.get("predicted_improvement") is not None}
        if len(predicted) > 1:
            raise ValueError("One current action has inconsistent predictions across exogenous futures")
        predicted_value = next(iter(predicted)) if predicted else None
        for future in futures:
            actual_row = by_candidate[candidate].get(future)
            reference_row = by_candidate[baseline_candidate_id].get(future)
            actual = _outcome_values(actual_row) if actual_row is not None else None
            reference = _outcome_values(reference_row) if reference_row is not None else None
            if actual_row is not None and actual_row.get("effective_execution_class"):
                execution_classes.add(actual_row["effective_execution_class"])
            if actual is None or reference is None:
                unknown += 1
                per_future.append({"future_id": future, "comparison": "unknown",
                                   "candidate_status": actual_row.get("status") if actual_row else "missing",
                                   "baseline_status": reference_row.get("status") if reference_row else "missing",
                                   "delta": None})
                continue
            comparison = _lex_compare(actual, reference,
                                      minute_resolution=minute_resolution,
                                      ens_resolution=ens_resolution)
            better += comparison < 0
            worse += comparison > 0
            tied += comparison == 0
            false_improvement += bool(predicted_value is True and comparison >= 0)
            per_future.append({"future_id": future,
                               "comparison": "better" if comparison < 0 else "worse" if comparison > 0 else "tie",
                               "candidate_status": actual_row["status"],
                               "baseline_status": reference_row["status"],
                               "delta": {key: actual[key] - reference[key] for key in _OUTCOME_KEYS}})
        out.append({
            "candidate_id": candidate, "baseline_candidate_id": baseline_candidate_id,
            "registered_stress_futures": len(futures),
            "better_future_count": better, "tie_future_count": tied,
            "worse_future_count": worse, "unknown_future_count": unknown,
            "all_stress_futures_nonworse": None if unknown else worse == 0,
            "strict_opportunity_across_stress_set": None if unknown else better > 0 and worse == 0,
            "predicted_improvement": predicted_value,
            "false_improvement_future_count": false_improvement if predicted_value is not None else None,
            "effective_execution_classes": sorted(execution_classes),
            "per_future": per_future,
            "research_resolution": {"P0_failure_minutes": minute_resolution,
                                    "P0_ENS_each_carrier_kWh": ens_resolution,
                                    "clinical_threshold_changed": False},
            "study_stress_is_not_a_probability_distribution": True,
            "ex_post_scope": "Registered finite candidates and common conditional futures only",
        })
    return out
