"""Versioned observation-only mode detector for v5 feedback comparisons.

Only the policy's *mode selection* ignores P0 disconnected power at or below
the original 0.001 kW solver-feasibility tolerance. Raw observation, physical
classification, service scores, projection, queue and execution are untouched.
The frozen v1 feedback policy remains available as a matched control.
"""
from __future__ import annotations

import copy

import numpy as np

from hospital_ies.data_contract import validate_action
from tools.control_improvement.candidates import validate_context
from tools.control_improvement.runtime import project_current
from tools.integrated_learning.policies import ACTION_NAMES, baseline_request, legal_history
from tools.policy_search.policy import FeedbackPolicy, _request_targets


MODE_DETECTOR_ID = "policy_search.observed_mode.v2"
# configs/resilience_assumptions.json:p0_solver_feasibility_tol_kW; the
# registration wrapper verifies and SHA-binds that unchanged source config.
P0_SOLVER_RESIDUAL_TOL_KW = 0.001


def observed_mode_v2(features: dict, previously_disturbed: bool) -> str:
    """Classify current legal observations, without future-event information."""
    impaired = any(
        key.startswith("availability_") and float(value) < 1.0 - 1e-8
        for key, value in features.items()
    )
    p0_disconnected = any(
        float(features.get(f"P0_{carrier}_disconnected_kW", 0.0))
        > P0_SOLVER_RESIDUAL_TOL_KW
        for carrier in ("e", "h", "c")
    )
    outage_observed = float(features.get("grid_outage_elapsed_minutes", 0.0)) > 0.0
    if impaired or p0_disconnected or outage_observed:
        return "disturbed"
    return "recovery" if previously_disturbed else "normal"


class ModeV2FeedbackPolicy(FeedbackPolicy):
    """Frozen v1 six-port feedback with only mode recognition versioned."""

    def __init__(self, theta, policy_id):
        super().__init__(theta, policy_id, family="mode")

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
        mode = observed_mode_v2(features, self._previously_disturbed)
        if mode == "disturbed":
            self._previously_disturbed = True
        secure_request = baseline_request("secure_milp")
        diagnostic = {
            "model_id": self.policy_id,
            "feedback_family": self.family,
            "feedback_theta": list(self.theta),
            "feedback_mode": mode,
            "mode_detector_id": MODE_DETECTOR_ID,
            "mode_p0_solver_residual_tol_kW": P0_SOLVER_RESIDUAL_TOL_KW,
            "strategy_fallback": False,
            "fallback_reason": None,
            "selected_candidate_id": "secure_baseline",
            "selected_candidate_index": 0,
            "current_planning_queries": [],
            "baseline_policy": "secure_milp",
            "baseline_request_submitted_unchanged": True,
            "future_rollouts_online": 0,
        }
        gains = self._gains(mode)
        if all(value == 0.0 for value in self.theta):
            diagnostic["strategy_status"] = "secure_baseline_identity"
            return copy.deepcopy(secure_request), diagnostic
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
