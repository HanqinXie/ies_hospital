"""Current-observation third-GT reserve request on the original six ports.

This is a train-side research candidate, not a protection or start guarantee.
The unchanged MILP, command queue, arrival revalidation, ramp, and service
locks decide whether a requested start becomes a plan and actual output.
"""
from __future__ import annotations

import copy
from collections.abc import Sequence

import numpy as np

from hospital_ies.data_contract import validate_action
from tools.control_improvement.candidates import validate_context
from tools.control_improvement.runtime import project_current
from tools.integrated_learning.policies import ACTION_NAMES, baseline_request, legal_history
from tools.policy_redesign.policy import current_port_limits


def _finite_vector(value, shape, name):
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid {name}") from error
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f"Invalid {name}")
    return array


def _binary_theta(theta: Sequence[float]) -> tuple[float]:
    if isinstance(theta, (str, bytes, dict)):
        raise ValueError("Reserve theta must be one binary parameter")
    value = _finite_vector(theta, (1,), "reserve theta")
    if value[0] not in (0.0, 1.0):
        raise ValueError("Reserve theta must be exactly zero or one")
    return (float(value[0]),)


def reserve_request_targets(secure_targets, context: dict, features: dict, *,
                            minimum_fraction: float) -> tuple[np.ndarray, str, int | None, float]:
    """Request a third stable GT while conserving current GT-plus-grid targets.

    Donors are reduced only to the original stable minimum. Any remaining
    third-unit minimum is matched by a lower *requested* grid import. A target
    never claims a start, delivered reserve, or protection relief.
    """
    secure = _finite_vector(secure_targets, (6,), "secure six-port projection")
    capacities = _finite_vector(context["capacity_GT_kW"], (3,), "current GT capacities")
    hrsg = _finite_vector(context["availability_HRSG"], (3,), "current HRSG availability")
    gt_available = _finite_vector([features[f"availability_GT_{i}"] for i in (1, 2, 3)],
                                  (3,), "current GT availability")
    pre_gt = _finite_vector([features[f"pre_GT_{i}_kW"] for i in (1, 2, 3)],
                            (3,), "observed previous GT output")
    state = [features[f"state_GT_{i}"] for i in (1, 2, 3)]
    if (not np.isfinite(minimum_fraction) or minimum_fraction != 0.4
            or np.any(secure < 0) or np.any(capacities < 0) or np.any(pre_gt < 0)
            or np.any((gt_available < 0) | (gt_available > 1))
            or np.any((hrsg < 0) | (hrsg > 1))
            or any(not isinstance(value, str) for value in state)):
        raise ValueError("Current reserve input or original minimum-stable fraction differs")
    if (np.any(capacities <= 0) or np.any(gt_available <= 0)
            or np.any(hrsg <= 0) or any(value == "FAILED" for value in state)):
        return secure.copy(), "gt_or_hrsg_unavailable", None, 0.0
    minimum = minimum_fraction * capacities
    online = [i for i in range(3) if secure[i] > 1e-7]
    idle = [i for i in range(3) if secure[i] <= 1e-7]
    if len(online) != 2 or len(idle) != 1 or sum(pre_gt > 1e-7) not in (2, 3):
        return secure.copy(), "secure_not_two_online", None, 0.0
    if any(secure[i] < minimum[i] - 1e-6 for i in online):
        return secure.copy(), "secure_below_minimum_stable", None, 0.0
    idle_index = idle[0]
    requested = secure.copy()
    requested[idle_index] = minimum[idle_index]
    remaining = float(minimum[idle_index])
    for i in sorted(online, key=lambda index: (-secure[index], index)):
        shift = min(remaining, max(0.0, float(secure[i] - minimum[i])))
        requested[i] -= shift
        remaining -= shift
    if remaining > secure[3] + 1e-7:
        return secure.copy(), "insufficient_grid_displacement", None, 0.0
    requested[3] = max(0.0, float(secure[3] - remaining))
    if np.any(requested[:3] > capacities + 1e-7):
        raise ValueError("Reserve request exceeds observed GT capacity")
    return requested, "third_gt_minimum_request", idle_index, remaining


class ReserveFeedbackPolicy:
    """Binary current-feedback candidate; theta zero is exact secure identity."""

    def __init__(self, theta: Sequence[float], policy_id: str):
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("Nonempty reserve policy ID required")
        self.theta = _binary_theta(theta)
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
            raise ValueError("Reserve policy needs a bound current problem")
        problem = self.current_problem
        self.current_problem = None
        now = float(history[-1]["decision_time_min"])
        if self._last_time is not None and now <= self._last_time and now != 0.0:
            raise ValueError("Reserve feedback decision clock did not advance")
        self._last_time = now
        secure_request = baseline_request("secure_milp")
        diagnostic = {
            "model_id": self.policy_id, "feedback_family": "third_gt_reserve",
            "feedback_theta": list(self.theta), "strategy_fallback": False,
            "fallback_reason": None, "selected_candidate_id": "secure_baseline",
            "selected_candidate_index": 0, "baseline_policy": "secure_milp",
            "baseline_request_submitted_unchanged": True,
            "current_planning_queries": [], "future_rollouts_online": 0,
            "reserve_trigger_reason": "theta_zero",
        }
        if self.theta[0] == 0.0:
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
                              fallback_reason="secure_projection_" + str(projected["status"]),
                              reserve_trigger_reason="secure_projection_failed")
            return copy.deepcopy(secure_request), diagnostic
        context = problem.policy_context(history, projected["command"])
        validate_context(history, context)
        features = history[-1]["features"]
        secure = _finite_vector(projected["projected_targets_kW"], (6,),
                                "secure six-port projection")
        levels = _finite_vector(problem.plant.levels, (4,), "original GT operating levels")
        minimum_fraction = float(min(levels))
        kwargs = {"diesel_rated_kW": float(problem.plant.emergency_diesel_kW),
                  "diesel_available": bool(problem.diesel_available),
                  "step_hours": float(problem.dt_h)}
        upper = current_port_limits(context, features, **kwargs)
        requested, reason, idle_index, grid_displacement = reserve_request_targets(
            secure, context, features, minimum_fraction=minimum_fraction)
        if np.any(requested > upper + 1e-7):
            raise ValueError("Reserve request exceeds current legal port bound")
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
            reserve_trigger_reason=reason,
            reserve_idle_gt_index=None if idle_index is None else idle_index + 1,
            reserve_minimum_stable_kW=(minimum_fraction *
                                       np.asarray(context["capacity_GT_kW"], dtype=float)).tolist(),
            reserve_grid_displacement_kW=float(grid_displacement),
            original_protection_trip_multiple=float(problem.config["gt_protection_trip_multiple"]),
            original_protection_hold_minutes=float(problem.config["gt_overload_hold_minutes"]),
            reserve_execution_semantics="request_only_original_milp_queue_and_arrival_authoritative",
        )
        if np.max(np.abs(requested - secure)) <= 1e-7:
            diagnostic["strategy_status"] = "secure_no_effective_target_change"
            return copy.deepcopy(secure_request), diagnostic
        request = copy.deepcopy(secure_request)
        request["targets"] = dict(zip(ACTION_NAMES, map(float, requested)))
        validate_action(request)
        diagnostic.update(strategy_status="reserve_six_port_request",
                          selected_candidate_id="reserve_third_gt_minimum",
                          selected_candidate_index=1,
                          baseline_request_submitted_unchanged=False)
        return request, diagnostic
