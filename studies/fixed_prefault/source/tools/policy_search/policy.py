"""Small full-episode feedback policies using only the current legal boundary.

The policy is called again at every original 15-minute decision boundary.  It
submits a six-port *request*, never an executed setpoint; the unchanged MILP,
priority locks, queue, arrival checks and physical evolution remain in env.step.
The secure projection here is a pure current-state planning query and never
advances the simulator or reads the exogenous event script.
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


_BLEND = (
    ("strength", 0.0, 1.0),
    ("gt_health_bias", 0.0, 3.0),
    ("gt_overload_bias", 0.0, 3.0),
    ("grid_relief_fraction", 0.0, 1.0),
    ("absorption_shift_fraction", -1.0, 1.0),
)
_MODE = tuple(
    (f"{mode}_{name}", lower, upper)
    for mode in ("normal", "disturbed", "recovery")
    for name, lower, upper in (
        ("strength", 0.0, 1.0),
        ("gt_health_and_overload_bias", 0.0, 3.0),
        ("grid_relief_fraction", 0.0, 1.0),
        ("absorption_shift_fraction", -1.0, 1.0),
    )
)
PARAMETER_SPECS = {"blend": _BLEND, "mode": _MODE}


def parameter_names(family: str) -> tuple[str, ...]:
    """Stable order for serialized theta vectors and offline optimizers."""
    if family not in PARAMETER_SPECS:
        raise ValueError("Unknown feedback family")
    return tuple(name for name, _, _ in PARAMETER_SPECS[family])


def parameter_bounds(family: str) -> tuple[tuple[float, float], ...]:
    if family not in PARAMETER_SPECS:
        raise ValueError("Unknown feedback family")
    return tuple((lower, upper) for _, lower, upper in PARAMETER_SPECS[family])


def zero_theta(family: str) -> list[float]:
    """The exact original secure identity in either parameterization."""
    return [0.0] * len(parameter_names(family))


def _registered_theta(theta: Sequence[float], family: str) -> tuple[float, ...]:
    bounds = parameter_bounds(family)
    if isinstance(theta, (str, bytes, dict)):
        raise ValueError("Theta must be an ordered numeric vector")
    try:
        values = list(theta)
    except TypeError as error:
        raise ValueError("Theta must be an ordered numeric vector") from error
    if len(values) != len(bounds):
        raise ValueError("Theta dimension differs from registered family")
    result = []
    for value, (lower, upper) in zip(values, bounds):
        if isinstance(value, (bool, np.bool_)):
            raise ValueError("Boolean theta coordinate")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Nonnumeric theta coordinate") from error
        if not math.isfinite(number) or not lower <= number <= upper:
            raise ValueError("Theta coordinate outside registered finite bounds")
        result.append(number)
    return tuple(result)


def _allocate(total: float, capacity: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Allocate requested GT load; the original MILP remains authoritative."""
    cap = np.maximum(0.0, np.asarray(capacity, dtype=float))
    weight = np.maximum(0.0, np.asarray(weights, dtype=float))
    result = np.zeros(3, dtype=float)
    remaining = min(float(total), float(cap.sum()))
    for _ in range(3):
        room = np.maximum(0.0, cap - result)
        valid = room > 1e-9
        available_weights = np.where(valid, weight, 0.0)
        if available_weights.sum() <= 1e-12:
            available_weights = room
        if available_weights.sum() <= 1e-12 or remaining <= 1e-9:
            break
        addition = np.minimum(room, remaining * available_weights / available_weights.sum())
        result += addition
        remaining -= float(addition.sum())
    return result


def _observed_mode(features: dict, previously_disturbed: bool) -> str:
    """Mode transitions use observed device status and served-load state only."""
    impaired = any(
        key.startswith("availability_") and float(value) < 1.0 - 1e-8
        for key, value in features.items()
    )
    p0_disconnected = any(
        float(features.get(f"P0_{carrier}_disconnected_kW", 0.0)) > 1e-8
        for carrier in ("e", "h", "c")
    )
    outage_observed = float(features.get("grid_outage_elapsed_minutes", 0.0)) > 0.0
    if impaired or p0_disconnected or outage_observed:
        return "disturbed"
    return "recovery" if previously_disturbed else "normal"


def _request_targets(secure_targets: np.ndarray, context: dict, features: dict,
                     gains: tuple[float, float, float, float, float]) -> np.ndarray:
    strength, health_bias, overload_bias, grid_fraction, absorption_shift = gains
    secure = np.asarray(secure_targets, dtype=float)
    if secure.shape != (6,) or not np.all(np.isfinite(secure)) or np.any(secure < 0.0):
        raise ValueError("Current secure projection has invalid six ports")
    capacity = np.asarray(context["capacity_GT_kW"], dtype=float)
    health = np.asarray(context["availability_HRSG"], dtype=float)
    overload = np.asarray(context["overload_minutes_GT"], dtype=float)
    weights = (capacity * np.maximum(health, 0.05) ** health_bias
               * np.exp(-overload_bias * np.minimum(overload / 15.0, 20.0)))
    balanced = _allocate(float(secure[:3].sum()), capacity, weights)
    target = secure.copy()
    target[:3] = (1.0 - strength) * secure[:3] + strength * balanced
    grid_availability = float(features.get("availability_GRID", 1.0))
    import_upper = max(0.0, float(context["grid_import_limit_kW"]) * grid_availability)
    headroom = max(0.0, import_upper - float(secure[3]))
    shift = strength * grid_fraction * min(headroom, float(target[:3].sum()))
    if shift > 0.0:
        target[:3] *= max(0.0, 1.0 - shift / float(target[:3].sum()))
        target[3] += shift
    abs_capacity = float(context["capacity_ABS_kW"])
    target[5] = min(abs_capacity, max(0.0, secure[5] + strength * absorption_shift * abs_capacity))
    return target


class FeedbackPolicy:
    """A fixed theta used repeatedly over a whole matched episode.

    ``blend`` uses one continuous gain profile for every state. ``mode`` uses
    separate normal, currently disturbed and observed-recovery profiles.  The
    policy keeps only whether a disturbance has *already* been observed.
    Callers should construct a new instance per rollout; a clock reset also
    resets this memory if an instance is reused.
    """

    def __init__(self, theta: Sequence[float], policy_id: str, *, family: str = "blend"):
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("Feedback policy ID must be nonempty")
        self.family = family
        self.theta = _registered_theta(theta, family)
        self.policy_id = policy_id
        self.current_problem = None
        self._previously_disturbed = False
        self._last_time = None

    def bind_current(self, problem) -> None:
        """Bind the current-only problem supplied by BoundaryBoundPolicy."""
        self.current_problem = problem

    def reset_policy_memory(self) -> None:
        self._previously_disturbed = False
        self._last_time = None
        self.current_problem = None

    def _gains(self, mode: str) -> tuple[float, float, float, float, float]:
        if self.family == "blend":
            return self.theta
        offset = {"normal": 0, "disturbed": 4, "recovery": 8}[mode]
        strength, health_and_overload, grid, absorption = self.theta[offset:offset + 4]
        return strength, health_and_overload, health_and_overload, grid, absorption

    def decide(self, records):
        history = legal_history(records)
        if self.current_problem is None:
            raise ValueError("Feedback policy needs a bound current problem")
        problem = self.current_problem
        self.current_problem = None
        now = float(history[-1]["decision_time_min"])
        if self._last_time is not None and now <= self._last_time:
            if now != 0.0:
                raise ValueError("Feedback policy clock did not advance")
            self._previously_disturbed = False
        self._last_time = now
        features = history[-1]["features"]
        mode = ("all_states" if self.family == "blend" else
                _observed_mode(features, self._previously_disturbed))
        if mode == "disturbed":
            self._previously_disturbed = True
        secure_request = baseline_request("secure_milp")
        diagnostic = {
            "model_id": self.policy_id,
            "feedback_family": self.family,
            "feedback_theta": list(self.theta),
            "feedback_mode": mode,
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
        gains = self._gains(mode)
        if gains[0] == 0.0:
            diagnostic["strategy_status"] = "secure_mode_no_intervention"
            return copy.deepcopy(secure_request), diagnostic

        projected = project_current(problem, secure_request)
        context = problem.policy_context(history, projected["command"])
        validate_context(history, context)
        secure_targets = np.asarray(projected["projected_targets_kW"], dtype=float)
        requested = _request_targets(secure_targets, context, features, gains)
        diagnostic["current_planning_queries"] = [{
            "candidate_id": "secure_baseline",
            "status": projected["status"],
            "projected_targets_kW": secure_targets.tolist(),
            "elapsed_seconds": projected.get("elapsed_seconds"),
            "solver_calls": copy.deepcopy(projected.get("solver_calls", [])),
        }]
        diagnostic["current_context"] = copy.deepcopy(context)
        diagnostic["requested_six_targets_kW"] = requested.tolist()
        diagnostic["delta_from_secure_plan_kW"] = (requested - secure_targets).tolist()
        if np.max(np.abs(requested - secure_targets)) <= 1e-7:
            diagnostic["strategy_status"] = "secure_no_effective_target_change"
            return copy.deepcopy(secure_request), diagnostic
        request = copy.deepcopy(secure_request)
        request["targets"] = dict(zip(ACTION_NAMES, map(float, requested)))
        validate_action(request)
        diagnostic.update(strategy_status="feedback_six_port_request",
                          selected_candidate_id=f"feedback_{self.family}_{mode}",
                          selected_candidate_index=1,
                          baseline_request_submitted_unchanged=False)
        return request, diagnostic
