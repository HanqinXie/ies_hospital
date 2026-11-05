"""Third-GT reserve requests use only the current, original six-port contract."""
from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.data_contract import validate_action
from tools.integrated_learning.policies import ACTION_NAMES, baseline_request


def _module():
    return importlib.import_module("tools.policy_redesign.reserve_policy")


def _record(*, future_fault=300.0, t=75.0, gt_availability=(1.0, 1.0, 1.0)):
    features = {
        "time_minute": t, "availability_GRID": 1.0,
        "availability_DIESEL": 1.0, "diesel_status": "OFF",
        "diesel_energy_kWh": 2400.0,
    }
    for i in (1, 2, 3):
        features[f"availability_GT_{i}"] = gt_availability[i - 1]
        features[f"state_GT_{i}"] = "OFF" if i == 1 else "RUNNING"
        features[f"pre_GT_{i}_kW"] = (0.0, 126.528, 192.425)[i - 1]
        features[f"overload_minutes_GT_{i}"] = 0.0
    return {
        "decision_time_min": t, "features": features,
        "feature_metadata": {key: {"available_time_min": t, "valid_time_min": t}
                             for key in features},
        "scenario": {"future_fault_start": future_fault},
    }


def _context(secure, *, capacities=(316.32, 316.32, 316.32), hrsg=(1.0, 1.0, 1.0)):
    return {
        "time_minute": 75.0, "arrival_minute": 75.0, "delayed": False,
        "execution_basis_targets_kW": list(secure),
        "current_plan_targets_kW": list(secure),
        "capacity_GT_kW": list(capacities),
        "availability_HRSG": list(hrsg),
        "overload_minutes_GT": [0.0, 0.0, 0.0],
        "capacity_ABS_kW": 703.370568,
        "grid_import_limit_kW": 350.0,
        "loads_kW": [[100.0, 100.0, 100.0] for _ in range(4)],
    }


def _decide(monkeypatch, theta, *, secure=None, record=None,
            capacities=(316.32, 316.32, 316.32), hrsg=(1.0, 1.0, 1.0)):
    module = _module()
    secure = [0.0, 126.528, 192.425, 350.0, 0.0, 293.61] if secure is None else secure
    context = _context(secure, capacities=capacities, hrsg=hrsg)
    problem = SimpleNamespace(
        plant=SimpleNamespace(gt_rated_kW=320.0, emergency_diesel_kW=900.0,
                              levels=(0.4, 0.6, 0.8, 1.0)),
        config={"gt_protection_trip_multiple": 1.05,
                "gt_overload_hold_minutes": 15.0},
        diesel_available=False, dt_h=0.25,
    )
    problem.policy_context = lambda history, command: copy.deepcopy(context)
    calls = []

    def project(current, request):
        assert current is problem and request == baseline_request("secure_milp")
        calls.append(1)
        return {"command": {"request": copy.deepcopy(request)},
                "projected_targets_kW": list(secure), "status": "success",
                "solver_calls": [], "elapsed_seconds": 0.0}

    monkeypatch.setattr(module, "project_current", project)
    policy = module.ReserveFeedbackPolicy(theta, "reserve_unit")
    policy.bind_current(problem)
    result = policy.decide([_record() if record is None else record])
    return result, calls


def test_theta_zero_is_exact_original_secure_without_extra_projection(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "project_current", lambda *_: pytest.fail("projected"))
    policy = module.ReserveFeedbackPolicy([0.0], "reserve_identity")
    policy.bind_current(object())
    request, diagnostic = policy.decide([_record()])
    assert request == baseline_request("secure_milp")
    assert diagnostic["strategy_status"] == "secure_baseline_identity"
    assert diagnostic["future_rollouts_online"] == 0


def test_idle_third_gt_is_requested_at_original_minimum_with_grid_displacement(monkeypatch):
    (request, diagnostic), calls = _decide(monkeypatch, [1.0])
    validate_action(request)
    targets = [request["targets"][key] for key in ACTION_NAMES]
    assert targets == pytest.approx([126.528, 126.528, 126.528, 289.369, 0.0, 293.61])
    assert sum(targets[:4]) == pytest.approx(0.0 + 126.528 + 192.425 + 350.0)
    assert diagnostic["reserve_idle_gt_index"] == 1
    assert diagnostic["reserve_grid_displacement_kW"] == pytest.approx(60.631)
    assert diagnostic["reserve_minimum_stable_kW"] == pytest.approx([126.528] * 3)
    assert diagnostic["secure_projected_six_targets_kW"] == pytest.approx(
        [0.0, 126.528, 192.425, 350.0, 0.0, 293.61])
    assert diagnostic["requested_six_targets_kW"] == pytest.approx(targets)
    assert diagnostic["request_is_not_execution"] is True
    assert diagnostic["future_rollouts_online"] == 0
    assert calls == [1]


def test_insufficient_grid_to_offset_minimum_stable_start_returns_secure(monkeypatch):
    secure = [0.0, 126.528, 192.425, 30.0, 0.0, 293.61]
    (request, diagnostic), calls = _decide(monkeypatch, [1.0], secure=secure)
    assert request == baseline_request("secure_milp")
    assert diagnostic["reserve_trigger_reason"] == "insufficient_grid_displacement"
    assert diagnostic["strategy_status"] == "secure_no_effective_target_change"
    assert calls == [1]


@pytest.mark.parametrize("secure,availability,hrsg,reason", [
    ([126.528, 126.528, 192.425, 350.0, 0.0, 293.61],
     (1.0, 1.0, 1.0), (1.0, 1.0, 1.0), "secure_not_two_online"),
    ([0.0, 0.0, 192.425, 350.0, 0.0, 293.61],
     (1.0, 1.0, 1.0), (1.0, 1.0, 1.0), "secure_not_two_online"),
    ([0.0, 126.528, 192.425, 350.0, 0.0, 293.61],
     (0.0, 1.0, 1.0), (1.0, 1.0, 1.0), "gt_or_hrsg_unavailable"),
    ([0.0, 126.528, 192.425, 350.0, 0.0, 293.61],
     (1.0, 1.0, 1.0), (0.0, 1.0, 1.0), "gt_or_hrsg_unavailable"),
])
def test_no_new_permission_or_forced_intervention(monkeypatch, secure, availability,
                                               hrsg, reason):
    record = _record(gt_availability=availability)
    (request, diagnostic), _ = _decide(monkeypatch, [1.0], secure=secure,
                                      record=record, hrsg=hrsg)
    assert request == baseline_request("secure_milp")
    assert diagnostic["reserve_trigger_reason"] == reason


def test_future_scenario_script_is_never_a_policy_input(monkeypatch):
    (left, left_diag), _ = _decide(monkeypatch, [1.0], record=_record(future_fault=5.0))
    (right, right_diag), _ = _decide(monkeypatch, [1.0], record=_record(future_fault=450.0))
    assert left == right
    assert left_diag["requested_six_targets_kW"] == right_diag["requested_six_targets_kW"]
    assert left_diag["future_rollouts_online"] == 0


def test_future_valid_feature_clock_is_rejected(monkeypatch):
    record = _record()
    record["feature_metadata"]["pre_GT_2_kW"]["available_time_min"] = 90.0
    with pytest.raises(ValueError, match="future-valid"):
        _decide(monkeypatch, [1.0], record=record)


@pytest.mark.parametrize("theta", [[], [0.5], [1.0, 0.0], [float("nan")]])
def test_only_finite_binary_single_parameter_is_accepted(theta):
    with pytest.raises(ValueError):
        _module().ReserveFeedbackPolicy(theta, "invalid")
