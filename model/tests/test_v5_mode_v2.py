"""Regression for the v5 mode-policy solver-residual misclassification."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hospital_ies.dataset_manifest import digest
from tools.policy_search.policy import _observed_mode


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "outputs/v5_policy_search_20261015_run01"
SCENE = "control_case_225c926364ef6dde565f"
POLICY = "prepare_only"
RAW_SHA = "34aa707adb7b9344ffcc80d4bfb691ac2ee397efa0fc634ba74d7f1510b248a9"


def _real_decisions():
    pair = RUN / "search/alternative_modes/results/pairs" / digest([SCENE, POLICY])
    raw = pair / "attempt_0001/trajectory.json.gz"
    assert raw.is_file(), "The frozen alternative trajectory is required for this regression"
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == RAW_SHA
    with gzip.open(raw, "rt", encoding="utf-8") as stream:
        return json.load(stream)["decisions"]


def test_real_t0_solver_residual_is_not_a_policy_disturbance():
    from tools.policy_search.mode_v2 import observed_mode_v2

    first = _real_decisions()[0]
    features = first["features"]
    assert first["decision_time_min"] == 0
    assert 1e-8 < features["P0_e_disconnected_kW"] < 0.001
    assert features["grid_outage_elapsed_minutes"] == 0
    assert min(value for key, value in features.items() if key.startswith("availability_")) == 1
    assert _observed_mode(features, False) == first["policy_diagnostic"]["feedback_mode"] == "disturbed"
    assert observed_mode_v2(features, False) == "normal"


def test_actual_fault_and_recovery_still_switch_modes():
    from tools.policy_search.mode_v2 import observed_mode_v2

    decisions = _real_decisions()
    fault = next(row for row in decisions
                 if row["decision_time_min"] > 0 and
                 any(key.startswith("availability_") and value < 1 - 1e-8
                     for key, value in row["features"].items()))
    assert observed_mode_v2(fault["features"], False) == "disturbed"
    recovered = dict(decisions[0]["features"])
    recovered["P0_e_disconnected_kW"] = 0.0
    assert observed_mode_v2(recovered, True) == "recovery"
    recovered["P0_e_disconnected_kW"] = 0.0011
    assert observed_mode_v2(recovered, True) == "disturbed"


def test_versioned_factory_scope_preserves_frozen_v1_dispatch():
    from tools.policy_search import closed_loop
    from tools.policy_search.mode_v2 import ModeV2FeedbackPolicy
    from tools.policy_search.mode_v2_batch import scoped_mode_v2_factory
    from tools.policy_search.policy import FeedbackPolicy

    theta = [.65, 1., .7, -.2] + [0.] * 8
    old = {"policy_id": "prepare_only", "kind": "feedback", "family": "mode", "theta": theta}
    new = {**old, "policy_id": "prepare_only_mode_v2"}
    original = closed_loop._factory
    with scoped_mode_v2_factory({new["policy_id"]: new}):
        assert isinstance(closed_loop._factory(new), ModeV2FeedbackPolicy)
        assert type(closed_loop._factory(old)) is FeedbackPolicy
    assert closed_loop._factory is original
