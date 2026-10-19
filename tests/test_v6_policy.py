"""v6 request freedom and legal-current-state boundaries."""
from __future__ import annotations

import copy
import importlib
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.data_contract import validate_action
from tools.integrated_learning.policies import ACTION_NAMES, baseline_request


def _module():
    return importlib.import_module("tools.policy_redesign.policy")


def _record(*, grid=1.0, diesel="OFF", fuel=1000.0, t=0.0, future_fault=300.0):
    features = {
        "time_minute": float(t),
        "availability_GRID": float(grid),
        "availability_DIESEL": 1.0,
        "diesel_status": diesel,
        "diesel_energy_kWh": float(fuel),
    }
    return {
        "decision_time_min": float(t),
        "features": features,
        "feature_metadata": {
            key: {"available_time_min": float(t), "valid_time_min": float(t)}
            for key in features
        },
        "scenario": {"future_fault_start": future_fault},
    }


def _context(secure, *, gt_cap=(350.0, 350.0, 350.0), grid_cap=850.0, abs_cap=500.0):
    return {
        "time_minute": 0.0,
        "arrival_minute": 0.0,
        "delayed": False,
        "execution_basis_targets_kW": list(secure),
        "current_plan_targets_kW": list(secure),
        "capacity_GT_kW": list(gt_cap),
        "availability_HRSG": [1.0, 1.0, 1.0],
        "overload_minutes_GT": [0.0, 0.0, 0.0],
        "capacity_ABS_kW": float(abs_cap),
        "grid_import_limit_kW": float(grid_cap),
        "loads_kW": [[100.0, 100.0, 100.0] for _ in range(4)],
    }


class _CurrentProblem:
    def __init__(self, context, *, diesel_available=False, rating=900.0):
        self.context = context
        self.diesel_available = diesel_available
        self.plant = SimpleNamespace(emergency_diesel_kW=rating)
        self.dt_h = 0.25
        self.execution_contract = {"diesel_mode": "legacy_explicit_permission"}

    def policy_context(self, history, command):
        assert command["request"] == baseline_request("secure_milp")
        assert history[-1]["decision_time_min"] == self.context["time_minute"]
        return copy.deepcopy(self.context)


def _decision(monkeypatch, family, theta, secure, *, grid=1.0, diesel="OFF",
              fuel=1000.0, diesel_available=False, gt_cap=(350.0, 350.0, 350.0),
              grid_cap=850.0, abs_cap=500.0, future_fault=300.0):
    module = _module()
    context = _context(secure, gt_cap=gt_cap, grid_cap=grid_cap, abs_cap=abs_cap)
    problem = _CurrentProblem(context, diesel_available=diesel_available)
    calls = []

    def project(current, request):
        assert current is problem
        assert request == baseline_request("secure_milp")
        calls.append(copy.deepcopy(request))
        return {
            "command": {"request": copy.deepcopy(request)},
            "projected_targets_kW": list(secure),
            "status": "success",
            "solver_calls": [],
            "elapsed_seconds": 0.0,
        }

    monkeypatch.setattr(module, "project_current", project)
    policy = module.ResidualFeedbackPolicy(theta, "v6_unit_policy", family=family)
    policy.bind_current(problem)
    result = policy.decide([_record(grid=grid, diesel=diesel, fuel=fuel,
                                    future_fault=future_fault)])
    return result, calls


@pytest.mark.parametrize("family", ["port_residual", "source_allocation"])
def test_zero_theta_is_exact_secure_identity_without_current_projection(monkeypatch, family):
    module = _module()
    monkeypatch.setattr(module, "project_current", lambda *_: pytest.fail("identity projected"))
    policy = module.ResidualFeedbackPolicy(module.zero_theta(family), "secure_identity", family=family)
    policy.bind_current(object())
    request, diagnostic = policy.decide([_record()])
    assert request == baseline_request("secure_milp")
    assert diagnostic["strategy_status"] == "secure_baseline_identity"
    assert diagnostic["future_rollouts_online"] == 0


@pytest.mark.parametrize("family,theta", [
    ("port_residual", [0.5, 0.0, 0.0, 0.0, 0.0, 0.0]),
    ("source_allocation", [0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
])
def test_zero_gt_secure_plan_can_generate_positive_gt_request(monkeypatch, family, theta):
    secure = [0.0, 0.0, 0.0, 300.0, 0.0, 100.0]
    (request, diagnostic), calls = _decision(monkeypatch, family, theta, secure)
    validate_action(request)
    assert sum(request["targets"][f"gt_{i}"] for i in (1, 2, 3)) > 100.0
    assert diagnostic["requested_six_targets_kW"][:3] != secure[:3]
    assert calls == [baseline_request("secure_milp")]


@pytest.mark.parametrize("family,diesel_index", [
    ("port_residual", 4),
    ("source_allocation", 5),
])
def test_legal_connected_diesel_can_change_by_physical_scale_in_both_directions(
        monkeypatch, family, diesel_index):
    theta = [0.0] * len(_module().parameter_names(family))
    theta[diesel_index] = 0.5
    secure = [100.0, 100.0, 0.0, 0.0, 0.0, 100.0]
    (request, diagnostic), _ = _decision(monkeypatch, family, theta, secure,
                                         grid=0.0, diesel="CONNECTED",
                                         diesel_available=True)
    assert request["targets"]["diesel"] == pytest.approx(450.0)
    assert diagnostic["diesel_available_current"] is True
    assert diagnostic["current_step_hours"] == 0.25
    assert diagnostic["diesel_rated_kW"] == 900.0
    theta[diesel_index] = -0.5
    secure[4] = 600.0
    (request, _), _ = _decision(monkeypatch, family, theta, secure, grid=0.0,
                                diesel="CONNECTED", diesel_available=True)
    assert request["targets"]["diesel"] == pytest.approx(150.0)


@pytest.mark.parametrize("grid,diesel,fuel,available", [
    (1.0, "OFF", 1000.0, False),
    (0.0, "STARTING", 1000.0, False),
    (0.0, "CONNECTED", 0.0, False),
])
def test_illegal_diesel_cannot_be_raised_by_residual(monkeypatch, grid, diesel, fuel, available):
    secure = [0.0, 0.0, 0.0, 300.0 if grid else 0.0, 0.0, 0.0]
    (request, diagnostic), _ = _decision(
        monkeypatch, "port_residual", [0, 0, 0, 0, 1, 0], secure,
        grid=grid, diesel=diesel, fuel=fuel, diesel_available=available)
    assert request == baseline_request("secure_milp")
    assert diagnostic["strategy_status"] == "secure_no_effective_target_change"
    assert diagnostic["request_legal_mask"][4] is False


def test_current_observed_capacity_bounds_and_future_script_independence(monkeypatch):
    theta = [1.0, 1.0, 1.0, 1.0, 0.0, 1.0]
    secure = [100.0, 0.0, 0.0, 100.0, 0.0, 0.0]
    params = dict(grid=0.5, gt_cap=(120.0, 0.0, 200.0), grid_cap=350.0,
                  abs_cap=90.0)
    (left, _), _ = _decision(monkeypatch, "port_residual", theta, secure,
                            future_fault=75.0, **params)
    (right, _), _ = _decision(monkeypatch, "port_residual", theta, secure,
                             future_fault=450.0, **params)
    assert left == right
    assert tuple(left["targets"][key] for key in ACTION_NAMES) == pytest.approx(
        (120.0, 0.0, 200.0, 175.0, 0.0, 90.0))


def test_shared_decoder_inverse_uses_same_effective_physical_scales():
    module = _module()
    secure = [0.0, 0.0, 0.0, 0.0, 0.0, 10.0]
    context = _context(secure, gt_cap=(350.0, 0.0, 350.0), grid_cap=850.0,
                       abs_cap=100.0)
    features = _record(grid=0.0, diesel="CONNECTED", fuel=100.0)["features"]
    options = dict(diesel_rated_kW=900.0, diesel_available=True,
                   step_hours=0.25)
    scales = module.current_port_limits(context, features, **options)
    assert scales.tolist() == pytest.approx([350.0, 0.0, 350.0, 0.0, 400.0, 100.0])
    normalized = [0.5, 0.0, -0.25, 0.0, 0.5, -0.1]
    requested = module.decode_residual(secure, context, features, normalized, **options)
    recovered = module.encode_residual(requested, secure, context, features, **options)
    assert recovered.tolist() == pytest.approx([0.5, 0.0, 0.0, 0.0, 0.5, -0.1])
    assert module.decode_residual(secure, context, features, recovered,
                                  **options).tolist() == pytest.approx(requested)
    illegal = requested.copy()
    illegal[4] = 900.0
    with pytest.raises(ValueError, match="representable"):
        module.encode_residual(illegal, secure, context, features, **options)


@pytest.mark.parametrize("family,theta", [
    ("port_residual", [0.0] * 5),
    ("port_residual", [0.0] * 5 + [math.nan]),
    ("source_allocation", [0.0] * 6 + [1.1]),
])
def test_reject_wrong_shape_nonfinite_or_out_of_bounds(family, theta):
    with pytest.raises(ValueError):
        _module().ResidualFeedbackPolicy(theta, "invalid", family=family)
