"""Legal current-state feedback for the existing GT loading soft objective.

Only ``action.security.gt_loading_penalty_USD_per_kWh`` changes. The original
secure policy remains the 0.35 baseline; P0--P3 service locks, MILP, physics,
command queue and arriving execution are untouched. A weight is an objective
coefficient, not a capacity, permission, trip threshold or safety guarantee.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Sequence

from hospital_ies.data_contract import validate_action
from tools.integrated_learning.policies import baseline_request, legal_history
from tools.policy_redesign.regime_policy import REGIMES, regime_for_features


SECURE_WEIGHT = 0.35
PARAMETER_NAMES = tuple(f"{regime}_gt_loading_penalty_USD_per_kWh"
                        for regime in REGIMES)


def _weights(value: Sequence[float]) -> tuple[float, float, float]:
    if isinstance(value, (str, bytes, dict)):
        raise ValueError("Security weights require three numeric coordinates")
    try:
        weights = tuple(float(x) for x in value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Invalid security weights") from error
    if (len(weights) != 3 or any(not math.isfinite(x) or not 0.0 <= x <= 10.0
                                 for x in weights)):
        raise ValueError("Three finite GT loading weights in [0,10] required")
    return weights


def candidate_specs() -> list[dict]:
    """Named directional probes, not presumed P0 improvements."""
    return [
        {"policy_id": "security_all_low", "kind": "security_feedback",
         "weights": [0.0, 0.0, 0.0]},
        {"policy_id": "security_fault_reactive_low", "kind": "security_feedback",
         "weights": [SECURE_WEIGHT, 0.0, 0.0]},
        {"policy_id": "security_preempt_high_fault_low", "kind": "security_feedback",
         "weights": [3.0, 0.0, 0.0]},
        {"policy_id": "security_fault_reactive_high", "kind": "security_feedback",
         "weights": [SECURE_WEIGHT, 3.0, 3.0]},
    ]


class SecurityWeightPolicy:
    """Unified full-episode feedback map from current availability to weight."""

    def __init__(self, weights: Sequence[float], policy_id: str):
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("Nonempty security policy ID required")
        self.weights = _weights(weights)
        self.policy_id = policy_id
        self._last_time = None

    def reset_policy_memory(self) -> None:
        self._last_time = None

    def decide(self, records):
        history = legal_history(records)
        now = float(history[-1]["decision_time_min"])
        if self._last_time is not None and now <= self._last_time and now != 0.0:
            raise ValueError("Security policy decision clock did not advance")
        self._last_time = now
        regime = regime_for_features(history[-1]["features"])
        selected = self.weights[REGIMES.index(regime)]
        request = {"security": {"gt_loading_penalty_USD_per_kWh": selected}}
        validate_action(request)
        identity = selected == SECURE_WEIGHT
        diagnostic = {
            "model_id": self.policy_id, "feedback_family": "current_security_weight",
            "registered_weight_by_regime_USD_per_kWh": dict(zip(REGIMES, self.weights)),
            "regime": regime, "regime_source": "current_legal_availability_only",
            "selected_weight_USD_per_kWh": selected,
            "secure_weight_USD_per_kWh": SECURE_WEIGHT,
            "delta_from_secure_weight_USD_per_kWh": selected - SECURE_WEIGHT,
            "strategy_fallback": False,
            "strategy_status": ("secure_baseline_identity" if identity
                                else "security_weight_request"),
            "baseline_policy": "secure_milp",
            "baseline_request_submitted_unchanged": identity,
            "request_is_not_execution": True,
            "future_rollouts_online": 0,
        }
        return copy.deepcopy(baseline_request("secure_milp")) if identity else request, diagnostic
