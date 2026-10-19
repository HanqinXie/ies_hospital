"""Current-only GT loading-penalty feedback uses an existing action field."""
from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.data_contract import validate_action
from tools.integrated_learning.policies import DEVICES, baseline_request


def _module():
    return importlib.import_module("tools.policy_redesign.security_policy")


def _record(*, grid=1.0, damaged=None, future_fault=300.0):
    features = {"time_minute": 90.0}
    features.update({f"availability_{device}": 1.0 for device in DEVICES})
    features["availability_GRID"] = grid
    if damaged:
        features[f"availability_{damaged}"] = 0.0
    return {"decision_time_min": 90.0, "features": features,
            "feature_metadata": {name: {"available_time_min": 90.0,
                                        "valid_time_min": 90.0}
                                 for name in features},
            "scenario": {"future_fault_start": future_fault}}


def test_secure_weight_vector_is_exact_original_request():
    policy = _module().SecurityWeightPolicy((.35, .35, .35), "security_identity")
    for record in (_record(), _record(grid=0.0), _record(damaged="HRSG_1")):
        policy.reset_policy_memory()
        request, diag = policy.decide([record])
        assert request == baseline_request("secure_milp")
        assert "targets" not in request
        assert diag["future_rollouts_online"] == 0


@pytest.mark.parametrize("record,expected_regime,expected_weight", [
    (_record(), "normal", .35),
    (_record(grid=0.0), "grid_islanded", 0.0),
    (_record(damaged="HRSG_2"), "other_damaged", 3.0),
    (_record(grid=0.0, damaged="GT_1"), "grid_islanded", 0.0),
])
def test_current_regime_selects_only_registered_weight(
        record, expected_regime, expected_weight):
    policy = _module().SecurityWeightPolicy((.35, 0.0, 3.0), "security_unit")
    request, diag = policy.decide([record])
    assert request == {"security": {"gt_loading_penalty_USD_per_kWh": expected_weight}}
    validate_action(request)
    assert diag["regime"] == expected_regime
    assert diag["selected_weight_USD_per_kWh"] == expected_weight
    assert diag["request_is_not_execution"] is True


def test_future_script_does_not_affect_soft_target():
    policy = _module().SecurityWeightPolicy((.35, 0.0, 3.0), "security_unit")
    left = policy.decide([_record(grid=0.0, future_fault=1.0)])[0]
    policy.reset_policy_memory()
    right = policy.decide([_record(grid=0.0, future_fault=500.0)])[0]
    assert left == right


@pytest.mark.parametrize("weights", [(-.01, .35, .35), (10.01, .35, .35),
                                      (.35, .35), (.35, math.nan, .35)])
def test_invalid_weight_range_or_shape_rejected(weights):
    with pytest.raises(ValueError):
        _module().SecurityWeightPolicy(weights, "invalid")
