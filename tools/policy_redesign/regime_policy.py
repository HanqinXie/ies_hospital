"""Three-regime, current-observation feedback over the original six requests.

The templates alter only a request to the unchanged hospital controller. The
original MILP, command queue, arrival projection and service locks retain
authority over physical execution.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Sequence

import numpy as np

from hospital_ies.data_contract import validate_action
from tools.control_improvement.candidates import validate_context
from tools.control_improvement.runtime import project_current
from tools.integrated_learning.policies import (ACTION_NAMES, DEVICES,
                                                baseline_request, legal_history)
from tools.policy_redesign.policy import current_port_limits, decode_residual


REGIMES = ("normal", "grid_islanded", "other_damaged")
NON_GRID_DEVICES = tuple(device for device in DEVICES if device != "GRID")
PARAMETER_NAMES = tuple(f"{regime}_{action}_residual"
                        for regime in REGIMES for action in ACTION_NAMES)


def _theta(value: Sequence[float]) -> np.ndarray:
    if isinstance(value, (str, bytes, dict)):
        raise ValueError("Regime theta must be an 18-coordinate vector")
    try:
        vector = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Invalid regime theta") from error
    if (vector.shape != (18,) or not np.all(np.isfinite(vector))
            or np.any(np.abs(vector) > 1.0)):
        raise ValueError("Regime theta must have 18 finite coordinates in [-1,1]")
    return vector


def regime_for_features(features: dict) -> str:
    """Choose a regime from the legally observed current availability only."""
    if not isinstance(features, dict):
        raise ValueError("Current legal features required")
    availability = {}
    for device in ("GRID", *NON_GRID_DEVICES):
        value = features.get(f"availability_{device}")
        if isinstance(value, bool):
            raise ValueError(f"Invalid observed availability_{device}")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"Missing observed availability_{device}") from error
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise ValueError(f"Invalid observed availability_{device}")
        availability[device] = number
    if availability["GRID"] <= 1e-9:
        return "grid_islanded"
    if any(availability[device] < 1.0 - 1e-9 for device in NON_GRID_DEVICES):
        return "other_damaged"
    return "normal"


def candidate_specs() -> list[dict]:
    """Named exploration proposals; none is presumed beneficial or safe."""
    def vector(*, normal=(), island=(), damaged=()):
        result = [0.0] * 18
        for offset, entries in ((0, normal), (6, island), (12, damaged)):
            for port, value in entries:
                result[offset + port] = float(value)
        return result

    return [
        {"policy_id": "regime_island_gt_diesel", "kind": "regime_feedback",
         "theta": vector(island=((0, .3), (1, .3), (2, .3), (4, .75)))},
        {"policy_id": "regime_damage_source_cooling", "kind": "regime_feedback",
         "theta": vector(damaged=((0, .2), (1, .2), (2, .2),
                                  (3, .25), (4, .3), (5, .25)))},
        {"policy_id": "regime_dual_response", "kind": "regime_feedback",
         "theta": vector(island=((0, .4), (1, .4), (2, .4),
                                  (4, 1.0), (5, .2)),
                         damaged=((0, .3), (1, .3), (2, .3),
                                  (3, .2), (4, .5), (5, .3)))},
    ]


class RegimeFeedbackPolicy:
    """A complete-episode feedback map with three six-port residual templates."""

    def __init__(self, theta: Sequence[float], policy_id: str):
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("Nonempty regime policy ID required")
        self.theta = tuple(map(float, _theta(theta)))
        self.policy_id = policy_id
        self.current_problem = None
        self._last_time = None

    def bind_current(self, problem) -> None:
        self.current_problem = problem

    def reset_policy_memory(self) -> None:
        self.current_problem = None
        self._last_time = None

    def decide(self, records):
        history = legal_history(records)
        if self.current_problem is None:
            raise ValueError("Regime policy needs a bound current problem")
        problem = self.current_problem
        self.current_problem = None
        now = float(history[-1]["decision_time_min"])
        if self._last_time is not None and now <= self._last_time and now != 0.0:
            raise ValueError("Regime feedback decision clock did not advance")
        self._last_time = now
        features = history[-1]["features"]
        regime = regime_for_features(features)
        offset = REGIMES.index(regime) * 6
        selected = self.theta[offset:offset + 6]
        secure_request = baseline_request("secure_milp")
        diagnostic = {
            "model_id": self.policy_id, "feedback_family": "regime_six_port",
            "feedback_theta": list(self.theta), "regime": regime,
            "regime_source": "current_legal_availability_only",
            "selected_template": list(selected),
            "strategy_fallback": False, "fallback_reason": None,
            "selected_candidate_id": "secure_baseline", "selected_candidate_index": 0,
            "baseline_policy": "secure_milp",
            "baseline_request_submitted_unchanged": True,
            "current_planning_queries": [], "future_rollouts_online": 0,
        }
        if all(value == 0.0 for value in selected):
            diagnostic["strategy_status"] = "secure_baseline_identity"
            return copy.deepcopy(secure_request), diagnostic

        projected = project_current(problem, secure_request)
        diagnostic["current_planning_queries"] = [{
            "candidate_id": "secure_baseline", "status": projected["status"],
            "projected_targets_kW": copy.deepcopy(projected.get("projected_targets_kW")),
            "elapsed_seconds": projected.get("elapsed_seconds"),
            "solver_calls": copy.deepcopy(projected.get("solver_calls", [])),
        }]
        if projected["status"] != "success":
            diagnostic.update(strategy_status="secure_projection_fallback",
                              strategy_fallback=True,
                              fallback_reason="secure_projection_" + str(projected["status"]))
            return copy.deepcopy(secure_request), diagnostic
        context = problem.policy_context(history, projected["command"])
        validate_context(history, context)
        secure = np.asarray(projected["projected_targets_kW"], dtype=np.float64)
        kwargs = {"diesel_rated_kW": float(problem.plant.emergency_diesel_kW),
                  "diesel_available": bool(problem.diesel_available),
                  "step_hours": float(problem.dt_h)}
        upper = current_port_limits(context, features, **kwargs)
        requested = decode_residual(secure, context, features, selected, **kwargs)
        diagnostic.update(
            current_context=copy.deepcopy(context),
            diesel_available_current=kwargs["diesel_available"],
            current_step_hours=kwargs["step_hours"],
            diesel_rated_kW=kwargs["diesel_rated_kW"],
            secure_projected_six_targets_kW=secure.tolist(),
            request_capacity_scale_kW=upper.tolist(),
            request_legal_mask=[bool(x > 0) for x in upper],
            requested_six_targets_kW=requested.tolist(),
            delta_from_secure_plan_kW=(requested - secure).tolist(),
            request_is_not_execution=True,
        )
        if np.max(np.abs(requested - secure)) <= 1e-7:
            diagnostic["strategy_status"] = "secure_no_effective_target_change"
            return copy.deepcopy(secure_request), diagnostic
        request = copy.deepcopy(secure_request)
        request["targets"] = dict(zip(ACTION_NAMES, map(float, requested)))
        validate_action(request)
        diagnostic.update(strategy_status="regime_six_port_request",
                          selected_candidate_id="regime_" + regime,
                          selected_candidate_index=1,
                          baseline_request_submitted_unchanged=False)
        return request, diagnostic
