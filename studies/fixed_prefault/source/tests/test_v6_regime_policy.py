"""Current-observation regime feedback keeps the original six-port boundary."""
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
from tools.integrated_learning.policies import ACTION_NAMES, DEVICES, baseline_request


def _module():
    return importlib.import_module("tools.policy_redesign.regime_policy")


def _record(*, grid=1.0, damaged=None, diesel="OFF", fuel=1000.0,
            future_fault=300.0):
    features = {"time_minute": 90.0, "diesel_status": diesel,
                "diesel_energy_kWh": float(fuel)}
    features.update({f"availability_{device}": 1.0 for device in DEVICES})
    features["availability_GRID"] = float(grid)
    if damaged is not None:
        features[f"availability_{damaged}"] = 0.0
    return {"decision_time_min": 90.0, "features": features,
            "feature_metadata": {key: {"available_time_min": 90.0,
                                       "valid_time_min": 90.0}
                                 for key in features},
            "scenario": {"future_fault_start": future_fault}}


def _context(secure):
    return {"time_minute": 90.0, "arrival_minute": 90.0, "delayed": False,
            "execution_basis_targets_kW": list(secure),
            "current_plan_targets_kW": list(secure),
            "capacity_GT_kW": [350.0, 350.0, 350.0],
            "availability_HRSG": [1.0, 1.0, 1.0],
            "overload_minutes_GT": [0.0, 0.0, 0.0],
            "capacity_ABS_kW": 500.0, "grid_import_limit_kW": 850.0,
            "loads_kW": [[100.0, 100.0, 100.0] for _ in range(4)]}


def _decide(monkeypatch, theta, record, *, secure=None, diesel_available=False):
    module = _module()
    secure = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0] if secure is None else secure
    context = _context(secure)
    problem = SimpleNamespace(plant=SimpleNamespace(emergency_diesel_kW=900.0),
                              diesel_available=diesel_available, dt_h=0.25)
    problem.policy_context = lambda history, command: copy.deepcopy(context)
    calls = []

    def project(current, request):
        assert current is problem and request == baseline_request("secure_milp")
        calls.append(1)
        return {"command": {"request": copy.deepcopy(request)},
                "projected_targets_kW": list(secure), "status": "success",
                "solver_calls": [], "elapsed_seconds": 0.0}

    monkeypatch.setattr(module, "project_current", project)
    policy = module.RegimeFeedbackPolicy(theta, "regime_unit")
    policy.bind_current(problem)
    return policy.decide([record]), calls


def test_zero_theta_is_exact_secure_without_projection(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "project_current", lambda *_: pytest.fail("projected"))
    policy = module.RegimeFeedbackPolicy([0.0] * 18, "identity")
    policy.bind_current(object())
    request, diagnostic = policy.decide([_record(grid=0.0)])
    assert request == baseline_request("secure_milp")
    assert diagnostic["regime"] == "grid_islanded"
    assert diagnostic["future_rollouts_online"] == 0


@pytest.mark.parametrize("record,expected", [
    (_record(), "normal"),
    (_record(grid=0.0), "grid_islanded"),
    (_record(damaged="HRSG_2"), "other_damaged"),
    (_record(grid=0.0, damaged="GT_1"), "grid_islanded"),
])
def test_regime_uses_only_current_availability(record, expected):
    module = _module()
    assert module.regime_for_features(record["features"]) == expected
    changed = copy.deepcopy(record)
    changed["scenario"]["future_fault_start"] = 5.0
    assert module.regime_for_features(changed["features"]) == expected


def test_island_template_can_raise_gt_and_currently_permitted_diesel(monkeypatch):
    theta = [0.0] * 18
    theta[6:12] = [0.3, 0.3, 0.3, 0.5, 1.0, 0.2]
    (request, diagnostic), calls = _decide(
        monkeypatch, theta, _record(grid=0.0, diesel="CONNECTED"),
        diesel_available=True)
    validate_action(request)
    assert tuple(request["targets"][key] for key in ACTION_NAMES) == pytest.approx(
        (105.0, 105.0, 105.0, 0.0, 900.0, 100.0))
    assert diagnostic["regime"] == "grid_islanded"
    assert diagnostic["selected_template"] == pytest.approx(theta[6:12])
    assert calls == [1]


def test_template_cannot_raise_unpermitted_diesel_or_exceed_current_port_bound(monkeypatch):
    theta = [0.0] * 18
    theta[6:12] = [1.0] * 6
    (request, diagnostic), _ = _decide(
        monkeypatch, theta, _record(grid=0.0, diesel="STARTING", fuel=0.0),
        diesel_available=False)
    assert request["targets"]["diesel"] == 0.0
    assert request["targets"]["grid_import"] == 0.0
    assert request["targets"]["gt_1"] == 350.0
    assert diagnostic["request_legal_mask"][4] is False


def test_other_regime_template_does_not_act_while_current_regime_is_normal(monkeypatch):
    theta = [0.0] * 18
    theta[12] = 0.5
    (request, diagnostic), calls = _decide(monkeypatch, theta, _record())
    assert request == baseline_request("secure_milp")
    assert diagnostic["regime"] == "normal"
    assert calls == []


@pytest.mark.parametrize("theta", [[0.0] * 17, [0.0] * 17 + [1.1]])
def test_invalid_template_shape_or_bounds_rejected(theta):
    with pytest.raises(ValueError):
        _module().RegimeFeedbackPolicy(theta, "invalid")


def test_named_candidate_templates_are_bounded_and_keep_normal_secure():
    specs = _module().candidate_specs()
    assert len(specs) == 3
    assert len({spec["policy_id"] for spec in specs}) == 3
    for spec in specs:
        assert spec["kind"] == "regime_feedback"
        assert spec["theta"][:6] == [0.0] * 6
        assert len(spec["theta"]) == 18
        assert all(-1.0 <= value <= 1.0 for value in spec["theta"])
        _module().RegimeFeedbackPolicy(spec["theta"], spec["policy_id"])
