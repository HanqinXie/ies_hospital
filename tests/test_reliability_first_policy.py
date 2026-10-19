"""Secure-anchored policy contract; synthetic projections are not hospital evidence."""
from __future__ import annotations

import copy

import numpy as np

from tools.integrated_learning.policies import BaselinePolicy, baseline_request


def _record(t=60.0):
    features = {"time_minute": t, "control_available": True}
    return [{"decision_time_min": t, "features": features,
             "feature_metadata": {k: {"available_time_min": t, "valid_time_min": t}
                                  for k in features},
             "scenario": {"unobserved_future_fault": t + 75.0}, "labels": {"120": 1}}]


def test_identity_submits_original_secure_request_without_a_projection(monkeypatch):
    import tools.reliability_first.policy as module

    def forbidden_projection(*_args, **_kwargs):
        raise AssertionError("identity mode must not query or re-project secure")

    monkeypatch.setattr(module, "project_current", forbidden_projection)
    policy = module.SecureAnchoredPolicy(mode="identity")
    policy.bind_current(object())
    request, diagnostic = policy.decide(_record())
    baseline, _ = BaselinePolicy("secure_milp").decide(_record())
    assert request == baseline == baseline_request("secure_milp")
    assert request == {"security": {"gt_loading_penalty_USD_per_kWh": .35}}
    assert diagnostic["selected_candidate_id"] == "secure_baseline"
    assert diagnostic["current_planning_queries"] == []


def _full_record(t=60.0):
    from tools.control_improvement.candidates import HISTORY_KEYS

    features = {k: 0.0 for k in HISTORY_KEYS}
    features["time_minute"] = t
    features["control_available"] = 1.0
    return [{"decision_time_min": t, "features": features,
             "feature_metadata": {k: {"available_time_min": t, "valid_time_min": t}
                                  for k in features},
             "scenario": {"future_fault": 75.0}, "labels": {"120": 1}}]


def _context():
    return {"time_minute": 60.0, "arrival_minute": 75.0, "delayed": True,
            "execution_basis_targets_kW": [100.0, 160.0, 0.0, 120.0, 0.0, 200.0],
            "current_plan_targets_kW": [180.0, 80.0, 0.0, 120.0, 0.0, 200.0],
            "capacity_GT_kW": [320.0, 320.0, 0.0],
            "availability_HRSG": [1.0, .3, 1.0],
            "overload_minutes_GT": [0.0, 10.0, 0.0],
            "capacity_ABS_kW": 600.0, "grid_import_limit_kW": 350.0,
            "loads_kW": np.ones((4, 3)).tolist(),
            "pending_command_ledger": {"available": False, "generated_minute": None,
                                       "planned_arrival_minute": None, "planned_targets_kW": None,
                                       "requested_targets_kW": None, "request_mask": None}}


def _fake_current_problem(monkeypatch, *, all_equal=False):
    import tools.reliability_first.policy as module
    from tools.integrated_learning.policies import ACTION_NAMES

    calls = []

    class Problem:
        def policy_context(self, history, command):
            assert command["request"] == baseline_request("secure_milp")
            assert history[-1]["decision_time_min"] == 60.0
            return _context()

    def projection(problem, request):
        calls.append(copy.deepcopy(request))
        if request == baseline_request("secure_milp"):
            six = [180.0, 80.0, 0.0, 120.0, 0.0, 200.0]
        elif not request:
            six = [190.0, 70.0, 0.0, 120.0, 0.0, 200.0]
        else:
            six = [float(request["targets"][name]) for name in ACTION_NAMES]
        if all_equal:
            six = [180.0, 80.0, 0.0, 120.0, 0.0, 200.0]
        command = {"request": copy.deepcopy(request), "dispatch": {
            "gt_electric_kW": six[:3], "grid_import_kW": six[3], "diesel_kW": six[4],
            "absorption_cooling_kW": six[5], "boiler_steam_lbph": 17.0,
            "electric_chiller_cooling_kW": [12.0, 13.0, 14.0]},
            "disconnected": [[0.0] * 3] * 4, "arrival_minute": 75.0,
            "weight": .35 if request.get("security") else 0.0}
        return {"command": command, "projected_targets_kW": six,
                "solver_calls": [], "events": [], "elapsed_seconds": 0.0,
                "status": "success"}

    monkeypatch.setattr(module, "project_current", projection)
    return Problem(), calls


def test_public_secure_candidate_helper_preserves_equal_plan_requests(monkeypatch):
    import tools.reliability_first.policy as module

    problem, calls = _fake_current_problem(monkeypatch, all_equal=True)
    events = []
    context, candidates, queries, groups = module.prepare_secure_candidates(
        problem, _full_record(), attempt_callback=lambda **event: events.append(event))
    assert context["current_plan_targets_kW"] == [180.0, 80.0, 0.0, 120.0, 0.0, 200.0]
    assert len(calls) == len(candidates) == len(queries) == 8
    assert groups == [list(range(8))]
    assert candidates[0]["request"] == baseline_request("secure_milp")
    assert candidates[1]["request"] == {}
    assert candidates[0]["current_command"]["dispatch"]["electric_chiller_cooling_kW"] == [12.0, 13.0, 14.0]
    assert len(events) == 16
    assert [event["event"] for event in events] == ["started", "finished"] * 8


def test_rule_preserves_secure_dispatch_and_distinct_requests(monkeypatch):
    import tools.reliability_first.policy as module

    problem, calls = _fake_current_problem(monkeypatch)
    policy = module.SecureAnchoredPolicy(mode="rule")
    policy.bind_current(problem)
    request, diagnostic = policy.decide(_full_record())
    assert len(calls) == len(diagnostic["candidates"]) == 8
    assert calls[0] == baseline_request("secure_milp")
    assert diagnostic["candidates"][0]["request"] == calls[0]
    assert diagnostic["candidates"][0]["current_command"]["dispatch"]["boiler_steam_lbph"] == 17.0
    assert diagnostic["candidates"][0]["current_command"]["weight"] == .35
    assert diagnostic["candidates"][1]["request"] == {}
    assert request == diagnostic["candidates"][diagnostic["selected_candidate_index"]]["request"]
    assert len(diagnostic["current_planning_queries"]) == 8
    assert diagnostic["future_rollouts_online"] == 0


class _Predictor:
    metadata = {"scale_kWh": np.ones((4, 3)).tolist()}

    def __init__(self, *, invalid=False):
        self.invalid = invalid

    def predict(self, x):
        prediction = np.zeros((len(x), 19))
        prediction[1, 0] = -1.0
        if self.invalid:
            prediction[0, 0] = .1
        return prediction


def test_gated_default_refuses_change_while_ungated_can_select(monkeypatch):
    import tools.reliability_first.policy as module

    problem, _ = _fake_current_problem(monkeypatch)
    gated = module.SecureAnchoredPolicy(mode="gated", predictor=_Predictor())
    gated.bind_current(problem)
    request, diagnostic = gated.decide(_full_record())
    assert request == baseline_request("secure_milp")
    assert diagnostic["selected_candidate_index"] == 0
    assert diagnostic["strategy_fallback"] is True
    assert diagnostic["candidate_delta_predictions"][1][0] == -1.0
    assert diagnostic["gate_reason"] == "no_calibrated_gate"

    ungated = module.SecureAnchoredPolicy(mode="ungated", predictor=_Predictor())
    ungated.bind_current(problem)
    request, diagnostic = ungated.decide(_full_record())
    assert diagnostic["selected_candidate_index"] == 1
    assert request == {}
    assert diagnostic["strategy_fallback"] is False


def test_custom_gate_and_invalid_baseline_prediction(monkeypatch):
    import tools.reliability_first.policy as module

    problem, _ = _fake_current_problem(monkeypatch)
    accept = module.SecureAnchoredPolicy(mode="gated", predictor=_Predictor(),
                                         gate=lambda prediction, context, candidates:
                                         (1, "registered_accept"))
    accept.bind_current(problem)
    request, diagnostic = accept.decide(_full_record())
    assert request == {}
    assert diagnostic["gate_reason"] == "registered_accept"

    invalid = module.SecureAnchoredPolicy(mode="ungated", predictor=_Predictor(invalid=True))
    invalid.bind_current(problem)
    request, diagnostic = invalid.decide(_full_record())
    assert request == baseline_request("secure_milp")
    assert diagnostic["strategy_fallback"] is True
    assert diagnostic["fallback_reason"]["error_type"] == "ValueError"
    assert diagnostic["candidate_delta_predictions"][0][0] == .1
