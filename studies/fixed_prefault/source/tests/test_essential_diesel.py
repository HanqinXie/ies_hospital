"""Fuel exhaustion cuts torque and supply without deleting rotor energy."""
import copy
import importlib
import math
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def machine(**kwargs):
    try:
        module = importlib.import_module("tools.essential_fixes.diesel")
    except ModuleNotFoundError as exc:
        pytest.fail(f"v16 fuel exhaustion continuation is missing: {exc}", pytrace=False)
    return module.DieselRotor(**kwargs)


def ready(**kwargs):
    result = machine(**kwargs)
    result.spin_up(900., 0., 1.)
    result.advance(30., 300.)
    return result


def assert_conserved(receipt):
    assert receipt["energy_residual_kWh"] == pytest.approx(0., abs=2e-11)
    assert receipt["mechanical_work_kWh"] == pytest.approx(
        receipt["electrical_work_kWh"] + receipt["loss_work_kWh"]
        + receipt["rotor_delta_kWh"], abs=2e-11)


def test_fuel_cut_is_zero_time_with_no_removed_rotor_energy():
    rotor = ready()
    before = rotor.as_dict()
    receipt = rotor.exhaust_fuel()
    assert receipt["actual_seconds"] == 0.
    assert receipt["operation"] == "fuel_exhaustion"
    assert receipt["fuel_exhausted"] is True
    assert receipt["mechanical_kW_before_cut"] == before["mechanical_kW"]
    assert rotor.mechanical_kW == 0.
    assert rotor.rotor_energy_kWs == before["rotor_energy_kWs"]
    assert receipt["rotor_delta_kWh"] == 0.
    assert all(value == 0. for _, value in rotor.as_dict()["delayed_governor_history"])
    assert rotor.as_dict()["fuel_exhausted"] is True
    assert "minute" in rotor.as_dict()["shutdown_assumption"].lower()
    assert_conserved(receipt)


@pytest.mark.parametrize("loss_fraction", [.02, 0.])
def test_exhausted_coast_has_no_fuel_work_or_electricity(loss_fraction):
    rotor = ready(config={"shaft_loss_fraction": loss_fraction})
    rotor.exhaust_fuel()
    before = rotor.as_dict()
    receipt = rotor.advance(120., 0., connected=False, run_enabled=False)
    expected = before["rotor_energy_kWs"] * math.exp(
        -rotor.nominal_loss_kW / rotor.nominal_rotor_energy_kWs * 120.)
    assert rotor.rotor_energy_kWs == pytest.approx(expected)
    assert rotor.mechanical_kW == 0.
    assert receipt["actual_seconds"] == 120.
    assert receipt["mechanical_work_kWh"] == 0.
    assert receipt["electrical_work_kWh"] == 0.
    assert receipt["loss_work_kWh"] == pytest.approx(
        (before["rotor_energy_kWs"] - expected) / 3600.)
    assert rotor.mechanical_work_kWh == before["mechanical_work_kWh"]
    assert rotor.electrical_work_kWh == before["electrical_work_kWh"]
    assert receipt["domain_exceeded"] is False
    assert receipt["domain_reason"] is None
    assert_conserved(receipt)


def test_stale_connected_load_and_startup_requests_cannot_restart_empty_diesel():
    rotor = ready()
    rotor.exhaust_fuel()
    before = rotor.as_dict()
    requested = rotor.advance(60., 300., connected=True, run_enabled=True)
    startup = rotor.spin_up(60., 0., 1.)
    for receipt in (requested, startup):
        assert receipt["fuel_exhausted"] is True
        assert receipt["request_rejected_reason"] == "fuel_exhausted"
        assert receipt["mechanical_work_kWh"] == 0.
        assert receipt["electrical_work_kWh"] == 0.
        assert receipt["actual_connected"] is False
        assert_conserved(receipt)
    assert rotor.rotor_energy_kWs < before["rotor_energy_kWs"]
    assert rotor.fuel_exhausted is True
    assert rotor.mechanical_kW == 0.
    assert rotor.mechanical_work_kWh == before["mechanical_work_kWh"]


def test_long_disconnected_decay_is_supported_below_running_speed_domain():
    rotor = ready()
    rotor.exhaust_fuel()
    receipt = rotor.advance(86400., 0., connected=False, run_enabled=False)
    assert rotor.speed_pu < rotor.config["speed_min_pu"]
    assert receipt["actual_seconds"] == 86400.
    assert receipt["domain_exceeded"] is False
    assert rotor.rotor_energy_kWs >= 0.
    assert_conserved(receipt)


def test_fuel_exhaustion_and_deepcopy_preview_do_not_modify_original():
    original = ready()
    before = copy.deepcopy(original.as_dict())
    trial = copy.deepcopy(original)
    trial.exhaust_fuel()
    trial.advance(60., 0., connected=False, run_enabled=False)
    assert original.as_dict() == before
    assert original.fuel_exhausted is False
    assert trial.fuel_exhausted is True


def test_repeated_fuel_cut_is_idempotent_for_energy_time_and_cumulative_work():
    rotor = ready()
    rotor.exhaust_fuel()
    before = rotor.as_dict()
    receipt = rotor.exhaust_fuel()
    assert rotor.as_dict() == before
    assert receipt["already_fuel_exhausted"] is True
    assert receipt["mechanical_work_kWh"] == 0.
    assert_conserved(receipt)


def test_split_coast_matches_whole_interval_energy_and_losses():
    whole = ready()
    whole.exhaust_fuel()
    split = copy.deepcopy(whole)
    a = whole.advance(120., 0., connected=False, run_enabled=False)
    b = split.advance(23., 0., connected=False, run_enabled=False)
    c = split.advance(97., 0., connected=False, run_enabled=False)
    assert split.rotor_energy_kWs == pytest.approx(whole.rotor_energy_kWs)
    assert b["loss_work_kWh"] + c["loss_work_kWh"] == pytest.approx(a["loss_work_kWh"])
    assert_conserved(a)
    assert_conserved(b)
    assert_conserved(c)


def test_existing_supported_diesel_response_is_identical_before_exhaustion():
    from tools.operating_response.diesel import DieselRotor as OldRotor
    old, new = OldRotor(), machine()
    for rotor in (old, new):
        rotor.spin_up(900., 0., 1.)
        rotor.advance(30., 300.)
    for key in ("rotor_energy_kWs", "mechanical_kW", "mechanical_work_kWh",
                "electrical_work_kWh", "loss_work_kWh", "elapsed_seconds"):
        assert getattr(new, key) == getattr(old, key)
    assert new.fuel_exhausted is False


@pytest.mark.parametrize("seconds,power", [(-1., 0.), (1., -1.), (1., 901.), (float("inf"), 0.)])
def test_invalid_exhausted_requests_are_rejected_before_mutation(seconds, power):
    rotor = ready()
    rotor.exhaust_fuel()
    before = rotor.as_dict()
    with pytest.raises(ValueError):
        rotor.advance(seconds, power, connected=False, run_enabled=False)
    assert rotor.as_dict() == before
