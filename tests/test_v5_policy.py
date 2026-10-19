"""Whole-feedback requests retain the original physical execution boundary."""
from __future__ import annotations

import copy
import importlib
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.data_contract import validate_action
from tools.integrated_learning.policies import ACTION_NAMES, baseline_request


def _policy_module():
    return importlib.import_module("tools.policy_search.policy")


def _record(t, *, fault=False, future_fault=300.0):
    features = {
        "time_minute": float(t),
        "control_available": True,
        "availability_GT_1": 0.5 if fault else 1.0,
        "availability_GRID": 1.0,
        "P0_e_disconnected_kW": 0.0,
        "P0_h_disconnected_kW": 0.0,
        "P0_c_disconnected_kW": 0.0,
        "overload_minutes_GT_1": 0.0,
        "overload_minutes_GT_2": 0.0,
        "overload_minutes_GT_3": 0.0,
    }
    return {"decision_time_min": float(t), "features": features,
            "feature_metadata": {key: {"available_time_min": float(t),
                                       "valid_time_min": float(t)} for key in features},
            "scenario": {"future_fault_start": future_fault},
            "labels": {"120": int(future_fault <= t + 120)}}


def _context(t, *, cap=(320.0, 320.0, 0.0), health=(1.0, 0.3, 1.0),
             overload=(0.0, 10.0, 0.0), grid_limit=350.0):
    return {"time_minute": float(t), "arrival_minute": float(t), "delayed": False,
            "execution_basis_targets_kW": [100.0, 160.0, 0.0, 120.0, 0.0, 200.0],
            "current_plan_targets_kW": [180.0, 80.0, 0.0, 120.0, 0.0, 200.0],
            "capacity_GT_kW": list(cap), "availability_HRSG": list(health),
            "overload_minutes_GT": list(overload), "capacity_ABS_kW": 600.0,
            "grid_import_limit_kW": float(grid_limit), "loads_kW": [[1.0] * 3 for _ in range(4)]}


class _Problem:
    def __init__(self, context):
        self.context = context

    def policy_context(self, history, command):
        assert command["request"] == baseline_request("secure_milp")
        assert history[-1]["decision_time_min"] == self.context["time_minute"]
        return copy.deepcopy(self.context)


def _fake_projection(monkeypatch, module):
    calls = []

    def project(problem, request):
        calls.append(copy.deepcopy(request))
        assert request == baseline_request("secure_milp")
        plan = problem.context["current_plan_targets_kW"]
        return {"command": {"request": copy.deepcopy(request), "dispatch": {
                    "gt_electric_kW": plan[:3], "grid_import_kW": plan[3],
                    "diesel_kW": plan[4], "absorption_cooling_kW": plan[5]}},
                "projected_targets_kW": copy.deepcopy(plan), "status": "success",
                "solver_calls": [], "events": [], "elapsed_seconds": 0.0}

    monkeypatch.setattr(module, "project_current", project)
    return calls


@pytest.mark.parametrize("family", ["blend", "mode"])
def test_zero_theta_submits_exact_secure_request_without_extra_projection(monkeypatch, family):
    module = _policy_module()
    monkeypatch.setattr(module, "project_current", lambda *_: pytest.fail("identity queried solver"))
    policy = module.FeedbackPolicy(module.zero_theta(family), "identity_test", family=family)
    policy.bind_current(object())
    request, diagnostic = policy.decide([_record(0.0)])
    assert request == baseline_request("secure_milp")
    assert request == {"security": {"gt_loading_penalty_USD_per_kWh": 0.35}}
    assert diagnostic["strategy_status"] == "secure_baseline_identity"
    assert diagnostic["future_rollouts_online"] == 0


def test_blend_feedback_changes_legal_six_port_request_on_new_observation(monkeypatch):
    module = _policy_module()
    calls = _fake_projection(monkeypatch, module)
    theta = [0.8, 2.0, 2.0, 0.5, -0.2]
    policy = module.FeedbackPolicy(theta, "blend_policy", family="blend")
    first = _record(0.0)
    policy.bind_current(_Problem(_context(0.0)))
    request1, info1 = policy.decide([first])
    validate_action(request1)
    assert set(request1) == {"security", "targets"}
    assert tuple(request1["targets"]) == ACTION_NAMES
    assert request1["targets"]["grid_import"] > 120.0
    assert request1["targets"]["absorption_cooling_kW"] < 200.0
    assert info1["feedback_mode"] == "all_states"
    second = _record(15.0, fault=True)
    policy.bind_current(_Problem(_context(15.0, cap=(160.0, 320.0, 0.0),
                                          health=(0.5, 1.0, 1.0),
                                          overload=(12.0, 0.0, 0.0))))
    request2, info2 = policy.decide([first, second])
    validate_action(request2)
    assert request2["targets"] != request1["targets"]
    assert info2["feedback_mode"] == "all_states"
    assert calls == [baseline_request("secure_milp")] * 2


def test_future_script_and_labels_cannot_change_current_feedback(monkeypatch):
    module = _policy_module()
    _fake_projection(monkeypatch, module)
    theta = [0.7, 1.0, 0.5, 0.2, 0.0]
    requests = []
    for future in (75.0, 450.0):
        policy = module.FeedbackPolicy(theta, "same_policy", family="blend")
        policy.bind_current(_Problem(_context(0.0)))
        requests.append(policy.decide([_record(0.0, future_fault=future)])[0])
    assert requests[0] == requests[1]


def test_mode_policy_uses_observed_disturbance_and_recovery_memory(monkeypatch):
    module = _policy_module()
    _fake_projection(monkeypatch, module)
    theta = [0.2, 0.0, 0.0, 0.0,
             1.0, 2.0, 0.5, -0.3,
             0.5, 1.0, 0.1, 0.2]
    policy = module.FeedbackPolicy(theta, "mode_policy", family="mode")
    records = []
    output = []
    for t, fault in ((0.0, False), (15.0, True), (30.0, False)):
        records.append(_record(t, fault=fault))
        policy.bind_current(_Problem(_context(t)))
        output.append(policy.decide(records))
    assert [diagnostic["feedback_mode"] for _, diagnostic in output] == [
        "normal", "disturbed", "recovery"]
    assert len({tuple(request.get("targets", {}).values()) for request, _ in output}) == 3
    assert all(diagnostic["future_rollouts_online"] == 0 for _, diagnostic in output)


@pytest.mark.parametrize("family,theta", [
    ("blend", [0.1]),
    ("blend", [0.1, 0.0, math.nan, 0.0, 0.0]),
    ("blend", [1.1, 0.0, 0.0, 0.0, 0.0]),
    ("mode", [0.0] * 11 + [2.0]),
])
def test_theta_is_finite_registered_and_bounded(family, theta):
    module = _policy_module()
    with pytest.raises(ValueError):
        module.FeedbackPolicy(theta, "invalid", family=family)


def test_secure_projection_failure_is_not_hidden_as_policy_fallback(monkeypatch):
    module = _policy_module()

    def broken_projection(*_):
        raise RuntimeError("registered current solve failed")

    monkeypatch.setattr(module, "project_current", broken_projection)
    policy = module.FeedbackPolicy([0.8, 1.0, 0.0, 0.0, 0.0], "projection_failure")
    policy.bind_current(_Problem(_context(0.0)))
    with pytest.raises(RuntimeError, match="registered current solve failed"):
        policy.decide([_record(0.0)])
