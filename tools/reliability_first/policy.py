"""Current-information policy anchored to the original secure MILP request."""
from __future__ import annotations

import copy
import time

import numpy as np

from tools.control_improvement.candidates import (candidate_numeric_features,
                                                  choose_current_rule,
                                                  current_candidates)
from tools.control_improvement.ranking import TARGET_NAMES, preference_key
from tools.control_improvement.runtime import context_provenance, project_current
from tools.integrated_learning.policies import baseline_request, legal_history


def prepare_secure_candidates(problem, history, *, attempt_callback=None, candidate_limit=8):
    """Query only the current problem, retaining each request and full dispatch.

    Equality of projected six ports is descriptive. Different requests may
    later have different tracking and holding effects, so no request is merged.
    An unavailable alternative is logged and excluded; failure of the secure
    reference itself propagates instead of being disguised as a policy result.
    """
    legal = legal_history(history)
    if not 4 <= int(candidate_limit) <= 8:
        raise ValueError("Current candidate limit must be 4 through 8")
    queries = []

    def run(name, request, *, baseline=False):
        if attempt_callback:
            attempt_callback(event="started", attempt_type="isolated_current_projection", candidate_id=name)
        try:
            projected = project_current(problem, request)
        except Exception as error:
            queries.append({"candidate_id": name, "status": "error",
                            "error_type": type(error).__name__, "message": str(error),
                            "diagnostic": copy.deepcopy(getattr(error, "current_projection_diagnostic", None))})
            if attempt_callback:
                attempt_callback(event="finished", attempt_type="isolated_current_projection",
                                 candidate_id=name, status="error")
            if baseline:
                error.secure_baseline_projection = queries[-1]
                raise
            return None
        queries.append(dict(candidate_id=name, **projected))
        if attempt_callback:
            attempt_callback(event="finished", attempt_type="isolated_current_projection",
                             candidate_id=name, status="success")
        return projected

    secure_request = baseline_request("secure_milp")
    secure = run("secure_baseline", secure_request, baseline=True)
    context = problem.policy_context(legal, secure["command"])
    candidates = [{"candidate_id": "secure_baseline", "request": copy.deepcopy(secure_request),
                   "projected_targets_kW": copy.deepcopy(secure["projected_targets_kW"]),
                   "current_command": copy.deepcopy(secure["command"]),
                   "projection_status": "success"}]
    for candidate in current_candidates(legal, context)[:int(candidate_limit) - 1]:
        candidate = copy.deepcopy(candidate)
        projected = run(candidate["candidate_id"], candidate["request"])
        if projected is None:
            continue
        candidate.update(projected_targets_kW=copy.deepcopy(projected["projected_targets_kW"]),
                         current_command=copy.deepcopy(projected["command"]),
                         projection_status="success")
        candidates.append(candidate)
    groups = []
    for i, candidate in enumerate(candidates):
        ports = np.asarray(candidate["projected_targets_kW"], dtype=float)
        found = next((g for g in groups if np.max(np.abs(
            ports - np.asarray(candidates[g[0]]["projected_targets_kW"], dtype=float))) <= 1e-7), None)
        if found is None:
            groups.append([i])
        else:
            found.append(i)
    return context, candidates, queries, groups


class SecureAnchoredPolicy:
    """Select among current requests while preserving exact secure identity.

    The first candidate is always the original security-only request. Its
    complete dispatch is retained for inspection; its six ports are never
    submitted as a substitute request. No conditional future is available to
    this controller.
    """

    def __init__(self, *, mode="identity", predictor=None, gate=None, candidate_limit=8):
        if mode not in ("identity", "rule", "gated", "ungated"):
            raise ValueError("Unknown secure-anchored mode")
        if not 4 <= int(candidate_limit) <= 8:
            raise ValueError("Current candidate limit must be 4 through 8")
        self.mode = mode
        self.predictor = predictor
        self.gate = gate
        self.candidate_limit = int(candidate_limit)
        self.policy_id = f"secure_anchored_{mode}"
        self.current_problem = None

    def bind_current(self, problem):
        self.current_problem = problem

    def decide(self, records):
        legal = legal_history(records)
        if self.current_problem is None:
            raise ValueError("Policy needs a boundary-bound current problem")
        problem = self.current_problem
        self.current_problem = None
        request = baseline_request("secure_milp")
        diagnostic = {
            "model_id": self.policy_id,
            "strategy_fallback": False,
            "strategy_status": "secure_baseline_identity",
            "fallback_reason": None,
            "gate_reason": "identity",
            "selected_candidate_id": "secure_baseline",
            "selected_candidate_index": 0,
            "current_planning_queries": [],
            "candidates": [{"candidate_id": "secure_baseline", "request": copy.deepcopy(request)}],
            "candidate_delta_predictions": None,
            "current_context": None,
            "future_rollouts_online": 0,
            "baseline_policy": "secure_milp",
            "baseline_request_submitted_unchanged": True,
        }
        if self.mode == "identity":
            return copy.deepcopy(request), diagnostic
        if self.mode in ("gated", "ungated") and self.predictor is None:
            diagnostic.update(strategy_fallback=True, strategy_status="secure_no_predictor",
                              gate_reason="no_predictor")
            return copy.deepcopy(request), diagnostic

        start = time.perf_counter()
        context, candidates, queries, groups = prepare_secure_candidates(
            problem, legal, candidate_limit=self.candidate_limit)
        metadata = context_provenance(context)
        metadata["schema"] = "reliability_first.current_secure_context.v1"
        metadata["fields"]["current_plan_targets_kW"]["source"] = (
            "Unchanged secure MILP on pure current problem; planned not executed")
        diagnostic.update(strategy_status="secure_anchored_current_candidate",
                          gate_reason=None, current_context=context,
                          current_context_metadata=metadata,
                          candidates=candidates, current_planning_queries=queries,
                          current_projection_query_seconds=time.perf_counter() - start,
                          baseline_dispatch_preserved=True,
                          candidate_projection_failures=sum(q["status"] == "error" for q in queries),
                          candidate_count_attempted=len(queries),
                          candidate_count_available=len(candidates),
                          equal_dispatch_groups_descriptive_only=groups)
        selected = 0
        if self.mode == "rule":
            selected = choose_current_rule(legal, context, candidates)
            diagnostic["gate_reason"] = "current_nonlearning_rule"
        else:
            prediction = None
            try:
                x = np.asarray([candidate_numeric_features(legal, context, c)
                                for c in candidates], dtype=float)
                prediction = np.asarray(self.predictor.predict(x), dtype=float)
                diagnostic["candidate_delta_predictions"] = prediction.tolist()
                if prediction.shape != (len(candidates), len(TARGET_NAMES)) or not np.all(np.isfinite(prediction)):
                    raise ValueError("Unavailable candidate difference predictions")
                if not np.array_equal(prediction[0], np.zeros(len(TARGET_NAMES))):
                    raise ValueError("Predictor must produce structural zero for secure self-difference")
                model_metadata = getattr(self.predictor, "metadata", {})
                scale = np.asarray(model_metadata["scale_kWh"], dtype=float)
                if scale.shape != (4, 3) or not np.all(np.isfinite(scale)) or np.any(scale <= 0):
                    raise ValueError("Predictor needs positive registered train-only scale_kWh")
                keys = [preference_key(y, scale) for y in prediction]
                best = min(keys)
                ties = [i for i, key in enumerate(keys) if key == best]
                diagnostic["predicted_best_indices"] = ties
                if self.mode == "ungated":
                    selected = 0 if 0 in ties else ties[0]
                    diagnostic["gate_reason"] = "ungated_predicted_preference"
                elif self.gate is None:
                    diagnostic.update(strategy_fallback=True,
                                      strategy_status="secure_gate_refusal",
                                      gate_reason="no_calibrated_gate")
                else:
                    proposed, reason = self.gate(prediction.copy(), copy.deepcopy(context),
                                                 copy.deepcopy(candidates))
                    if isinstance(proposed, bool) or not isinstance(proposed, (int, np.integer)) or not 0 <= proposed < len(candidates):
                        raise ValueError("Gate selected an unavailable current candidate")
                    selected = int(proposed)
                    diagnostic["gate_reason"] = str(reason)
                    if selected == 0:
                        diagnostic.update(strategy_fallback=True,
                                          strategy_status="secure_gate_refusal")
            except Exception as error:
                selected = 0
                diagnostic.update(strategy_fallback=True, strategy_status="secure_model_fallback",
                                  gate_reason="model_or_gate_error",
                                  fallback_reason={"error_type": type(error).__name__, "message": str(error)})
        choice = candidates[selected]
        diagnostic.update(selected_candidate_id=choice["candidate_id"], selected_candidate_index=selected,
                          baseline_request_submitted_unchanged=selected == 0)
        return copy.deepcopy(choice["request"]), diagnostic
