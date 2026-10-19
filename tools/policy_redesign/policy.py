"""Current-observation six-port feedback requests for v6 research.

Requests are preferences for the unchanged priority MILP.  A generated plan,
queued arrival, actual dispatch and P0--P3 outcome are separate records in the
original environment; no request here is an actuator bypass or a future plan.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Sequence

import numpy as np

from hospital_ies.data_contract import validate_action
from tools.control_improvement.candidates import validate_context
from tools.control_improvement.runtime import project_current
from tools.integrated_learning.policies import ACTION_NAMES, baseline_request, legal_history


PARAMETER_SPECS = {
    "port_residual": (
        "gt_1_residual", "gt_2_residual", "gt_3_residual", "grid_import_residual",
        "diesel_residual", "absorption_residual",
    ),
    "source_allocation": (
        "gt_total_residual", "gt_1_allocation_bias", "gt_2_allocation_bias",
        "gt_3_allocation_bias", "grid_import_residual", "diesel_residual",
        "absorption_residual",
    ),
}


def parameter_names(family: str = "port_residual") -> tuple[str, ...]:
    try:
        return PARAMETER_SPECS[family]
    except KeyError as error:
        raise ValueError("Unknown v6 feedback family") from error


def parameter_bounds(family: str = "port_residual") -> tuple[tuple[float, float], ...]:
    return ((-1.0, 1.0),) * len(parameter_names(family))


def zero_theta(family: str = "port_residual") -> list[float]:
    return [0.0] * len(parameter_names(family))


def _signed(value, count: int, name: str) -> np.ndarray:
    if isinstance(value, (str, bytes, dict)):
        raise ValueError(f"Invalid {name} vector")
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid {name} vector") from error
    if (array.shape != (count,) or not np.all(np.isfinite(array))
            or np.any(np.abs(array) > 1.0)):
        raise ValueError(f"{name} must have {count} finite coordinates in [-1,1]")
    return array


def _nonnegative(value, count: int, name: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid {name}") from error
    if array.shape != (count,) or not np.all(np.isfinite(array)) or np.any(array < 0):
        raise ValueError(f"Invalid current {name}")
    return array


def _finite_nonnegative(value, name: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"Invalid {name}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid {name}") from error
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Invalid {name}")
    return number


def current_port_limits(context: dict, features: dict, *, diesel_rated_kW: float,
                        diesel_available: bool, step_hours: float = 0.25) -> np.ndarray:
    """Current request ceilings; the original projection rechecks at arrival.

    `diesel_available` is the existing current-boundary permission result, not
    a new authorization.  Observed CONNECTED status, availability and fuel are
    required as well.  The 15-minute fuel budget only bounds this request;
    delayed commands remain subject to the later physical state.
    """
    if not isinstance(context, dict) or not isinstance(features, dict):
        raise ValueError("Current context and legal features are required")
    if type(diesel_available) not in (bool, np.bool_):
        raise ValueError("Current diesel permission must be boolean")
    duration = _finite_nonnegative(step_hours, "step_hours")
    if duration <= 0:
        raise ValueError("Positive current step_hours required")
    cap_gt = _nonnegative(context["capacity_GT_kW"], 3, "GT capacities")
    grid = (_finite_nonnegative(context["grid_import_limit_kW"], "grid limit")
            * _finite_nonnegative(features["availability_GRID"], "GRID availability"))
    abs_cap = _finite_nonnegative(context["capacity_ABS_kW"], "ABS capacity")
    diesel_rating = _finite_nonnegative(diesel_rated_kW, "diesel rating")
    diesel_availability = _finite_nonnegative(features["availability_DIESEL"], "DIESEL availability")
    fuel = _finite_nonnegative(features["diesel_energy_kWh"], "diesel fuel")
    status = features["diesel_status"]
    if not isinstance(status, str):
        raise ValueError("Observed diesel status required")
    diesel = (min(diesel_rating * diesel_availability, fuel / duration)
              if diesel_available and status == "CONNECTED" and fuel > 0 else 0.0)
    return np.asarray([*cap_gt, grid, diesel, abs_cap], dtype=float)


def decode_residual(secure_targets, context: dict, features: dict,
                    normalized_six_residual, *, diesel_rated_kW: float,
                    diesel_available: bool, step_hours: float = 0.25) -> np.ndarray:
    """Map six signed physical-capacity fractions to absolute requested kW.

    Shared by learned and nonlearning v6 policies.  It changes only a request;
    `project_current` and the original environment enforce service and physics.
    """
    secure = _nonnegative(secure_targets, 6, "secure six-port projection")
    residual = _signed(normalized_six_residual, 6, "six-port residual")
    upper = current_port_limits(context, features, diesel_rated_kW=diesel_rated_kW,
                                diesel_available=diesel_available, step_hours=step_hours)
    return np.minimum(upper, np.maximum(0.0, secure + residual * upper))


def encode_residual(requested_targets, secure_targets, context: dict, features: dict,
                    *, diesel_rated_kW: float, diesel_available: bool,
                    step_hours: float = 0.25) -> np.ndarray:
    """Canonical normalized label for a request representable by this decoder.

    Projection is many-to-one at bounds.  Saturated requests get the smallest
    residual that reaches the same boundary; unsupported zero-scale changes are
    rejected instead of silently converted into supervised zero labels.
    """
    requested = _nonnegative(requested_targets, 6, "requested six-port targets")
    secure = _nonnegative(secure_targets, 6, "secure six-port projection")
    kwargs = dict(diesel_rated_kW=diesel_rated_kW,
                  diesel_available=diesel_available, step_hours=step_hours)
    upper = current_port_limits(context, features, **kwargs)
    delta = requested - secure
    if np.any((upper <= 0.0) & (np.abs(delta) > 1e-7)):
        raise ValueError("Request is not representable at a zero-scale port")
    label = np.divide(delta, upper, out=np.zeros(6, dtype=float), where=upper > 0)
    if np.any(np.abs(label) > 1.0 + 1e-7):
        raise ValueError("Request is not representable by bounded residual")
    label = np.clip(label, -1.0, 1.0)
    reproduced = decode_residual(secure, context, features, label, **kwargs)
    if not np.allclose(reproduced, requested, atol=1e-7, rtol=0.0):
        raise ValueError("Request is not representable after legal clipping")
    return label


def _allocate(total: float, capacity: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Finite capped proportional allocation, including zero-total GT baseline."""
    result = np.zeros(3, dtype=float)
    remaining = min(max(0.0, total), float(capacity.sum()))
    for _ in range(3):
        room = np.maximum(0.0, capacity - result)
        use = np.where(room > 1e-9, weights, 0.0)
        if use.sum() <= 1e-12:
            use = room
        if use.sum() <= 1e-12 or remaining <= 1e-9:
            break
        addition = np.minimum(room, remaining * use / use.sum())
        result += addition
        remaining -= float(addition.sum())
    return result


def decode_source_allocation(secure_targets, context: dict, features: dict,
                             normalized_seven_parameters, *, diesel_rated_kW: float,
                             diesel_available: bool, step_hours: float = 0.25) -> np.ndarray:
    """Signed GT-total shift plus observed-HRSG-aware allocation and three ports."""
    secure = _nonnegative(secure_targets, 6, "secure six-port projection")
    theta = _signed(normalized_seven_parameters, 7, "source-allocation parameters")
    shared = np.asarray([0.0, 0.0, 0.0, theta[4], theta[5], theta[6]])
    result = decode_residual(secure, context, features, shared,
                             diesel_rated_kW=diesel_rated_kW,
                             diesel_available=diesel_available, step_hours=step_hours)
    if theta[0] != 0.0 or np.any(theta[1:4] != 0.0):
        cap = _nonnegative(context["capacity_GT_kW"], 3, "GT capacities")
        health = _nonnegative(context["availability_HRSG"], 3, "HRSG availability")
        total = np.clip(float(secure[:3].sum()) + theta[0] * float(cap.sum()),
                        0.0, float(cap.sum()))
        weights = cap * np.maximum(health, 0.05) * np.exp(2.0 * theta[1:4])
        result[:3] = _allocate(float(total), cap, weights)
    return result


class ResidualFeedbackPolicy:
    """A fixed online feedback map evaluated on the full original episode."""

    def __init__(self, theta: Sequence[float], policy_id: str,
                 *, family: str = "port_residual"):
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("Nonempty v6 policy ID required")
        self.theta = tuple(map(float, _signed(theta, len(parameter_names(family)), "theta")))
        self.policy_id = policy_id
        self.family = family
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
            raise ValueError("v6 policy needs a bound current problem")
        problem = self.current_problem
        self.current_problem = None
        now = float(history[-1]["decision_time_min"])
        if self._last_time is not None and now <= self._last_time:
            if now != 0.0:
                raise ValueError("v6 feedback decision clock did not advance")
        self._last_time = now
        secure_request = baseline_request("secure_milp")
        diagnostic = {
            "model_id": self.policy_id,
            "feedback_family": self.family,
            "feedback_theta": list(self.theta),
            "strategy_fallback": False,
            "fallback_reason": None,
            "selected_candidate_id": "secure_baseline",
            "selected_candidate_index": 0,
            "current_planning_queries": [],
            "baseline_policy": "secure_milp",
            "baseline_request_submitted_unchanged": True,
            "future_rollouts_online": 0,
        }
        if all(value == 0.0 for value in self.theta):
            diagnostic["strategy_status"] = "secure_baseline_identity"
            return copy.deepcopy(secure_request), diagnostic

        projected = project_current(problem, secure_request)
        context = problem.policy_context(history, projected["command"])
        validate_context(history, context)
        secure = _nonnegative(projected["projected_targets_kW"], 6, "secure projection")
        features = history[-1]["features"]
        kwargs = dict(diesel_rated_kW=problem.plant.emergency_diesel_kW,
                      diesel_available=bool(problem.diesel_available),
                      step_hours=float(problem.dt_h))
        upper = current_port_limits(context, features, **kwargs)
        if self.family == "port_residual":
            requested = decode_residual(secure, context, features, self.theta, **kwargs)
        else:
            requested = decode_source_allocation(secure, context, features, self.theta, **kwargs)
        diagnostic["current_planning_queries"] = [{
            "candidate_id": "secure_baseline",
            "status": projected["status"],
            "projected_targets_kW": secure.tolist(),
            "elapsed_seconds": projected.get("elapsed_seconds"),
            "solver_calls": copy.deepcopy(projected.get("solver_calls", [])),
        }]
        diagnostic["current_context"] = copy.deepcopy(context)
        diagnostic["diesel_available_current"] = kwargs["diesel_available"]
        diagnostic["current_step_hours"] = kwargs["step_hours"]
        diagnostic["diesel_rated_kW"] = float(kwargs["diesel_rated_kW"])
        diagnostic["secure_projected_six_targets_kW"] = secure.tolist()
        diagnostic["request_capacity_scale_kW"] = upper.tolist()
        diagnostic["request_legal_mask"] = [bool(x > 0) for x in upper]
        diagnostic["requested_six_targets_kW"] = requested.tolist()
        diagnostic["delta_from_secure_plan_kW"] = (requested - secure).tolist()
        diagnostic["request_is_not_execution"] = True
        if np.max(np.abs(requested - secure)) <= 1e-7:
            diagnostic["strategy_status"] = "secure_no_effective_target_change"
            return copy.deepcopy(secure_request), diagnostic

        request = copy.deepcopy(secure_request)
        request["targets"] = dict(zip(ACTION_NAMES, map(float, requested)))
        validate_action(request)
        diagnostic.update(strategy_status="v6_six_port_request",
                          selected_candidate_id=f"v6_{self.family}",
                          selected_candidate_index=1,
                          baseline_request_submitted_unchanged=False)
        return request, diagnostic
