"""Finite diesel rotor/shaft contracts, independent of hospital trajectory code."""
import copy
import importlib
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def rotor(**kwargs):
    try:
        module = importlib.import_module("tools.operating_response.diesel")
    except ModuleNotFoundError as exc:
        pytest.fail(f"M15 finite diesel rotor not implemented: {exc}", pytrace=False)
    return module.DieselRotor(**kwargs)


def ready(**kwargs):
    result = rotor(**kwargs)
    result.spin_up(900., 0., 1.)
    return result


def assert_conserved(receipt):
    assert receipt["mechanical_work_kWh"] >= 0.
    assert receipt["electrical_work_kWh"] >= 0.
    assert receipt["loss_work_kWh"] >= -1e-12
    assert receipt["energy_residual_kWh"] == pytest.approx(0., abs=2e-11)
    assert receipt["mechanical_work_kWh"] == pytest.approx(
        receipt["electrical_work_kWh"] + receipt["loss_work_kWh"]
        + receipt["rotor_delta_kWh"], abs=2e-11)


def test_default_off_state_and_inertia_units_are_explicit():
    machine = rotor()
    state = machine.as_dict()
    assert state["rated_kW"] == 900.
    assert state["apparent_base_kVA"] == 1125.
    assert state["nominal_rotor_energy_kWs"] == pytest.approx(.7359 * 1125.)
    assert state["rotor_energy_kWs"] == 0.
    assert state["speed_pu"] == 0.
    assert state["mechanical_kW"] == 0.
    assert "research" in state["parameter_validity"].lower()


def test_spinup_charges_energy_and_real_declared_shaft_loss_without_electricity():
    machine = rotor()
    receipt = machine.spin_up(900., 0., 1.)
    nominal = .7359 * 1125.
    assert receipt["electrical_work_kWh"] == 0.
    assert receipt["rotor_delta_kWh"] == pytest.approx(nominal / 3600.)
    assert receipt["loss_work_kWh"] == pytest.approx(.02 * 900. * 900. / 2. / 3600.)
    assert machine.speed_pu == pytest.approx(1.)
    assert_conserved(receipt)


def test_partial_spinup_and_restarted_profile_never_reset_residual_energy():
    machine = rotor()
    first = machine.spin_up(450., 0., .5)
    assert machine.speed_pu == pytest.approx(math.sqrt(.5))
    machine.advance(5., 0., connected=False, run_enabled=False)
    before = machine.rotor_energy_kWs
    second = machine.spin_up(900., 0., 1.)
    assert second["state_before"]["rotor_energy_kWs"] == before
    assert second["rotor_delta_kWh"] == pytest.approx(
        (machine.nominal_rotor_energy_kWs - before) / 3600.)
    assert first["mechanical_work_kWh"] > 0.
    assert_conserved(second)


def test_split_linear_startup_equals_whole_energy_and_losses():
    whole = rotor()
    split = rotor()
    a = whole.spin_up(900., 0., 1.)
    b = split.spin_up(225., 0., .25)
    c = split.spin_up(675., .25, 1.)
    assert split.rotor_energy_kWs == pytest.approx(whole.rotor_energy_kWs)
    assert b["mechanical_work_kWh"] + c["mechanical_work_kWh"] == pytest.approx(
        a["mechanical_work_kWh"])
    assert_conserved(b)
    assert_conserved(c)


def test_first_delayed_load_step_draws_finite_rotor_energy_not_free_mechanical_power():
    machine = ready()
    before = machine.mechanical_kW
    receipt = machine.advance(.02, 200.)
    assert not receipt["domain_exceeded"]
    assert receipt["electrical_work_kWh"] == pytest.approx(200. * .02 / 3600.)
    assert machine.mechanical_kW == pytest.approx(before, abs=1e-8)
    assert machine.rotor_energy_kWs < machine.nominal_rotor_energy_kWs
    assert_conserved(receipt)


def test_constant_load_speed_recovers_and_shaft_work_includes_loss():
    machine = ready()
    receipt = machine.advance(30., 200.)
    assert not receipt["domain_exceeded"]
    assert abs(machine.speed_pu - 1.) <= .0011
    assert machine.mechanical_kW > 200.
    assert receipt["actual_seconds"] == 30.
    assert_conserved(receipt)


def test_unload_absorbs_mechanical_difference_then_recovers_without_sink():
    machine = ready()
    machine.advance(20., 300.)
    before = machine.rotor_energy_kWs
    transient = machine.advance(.04, 0.)
    assert machine.rotor_energy_kWs > before
    assert transient["electrical_work_kWh"] == 0.
    assert transient["rotor_delta_kWh"] > 0.
    assert_conserved(transient)
    tail = machine.advance(30., 0.)
    assert not tail["domain_exceeded"]
    assert abs(machine.speed_pu - 1.) <= .0011
    assert_conserved(tail)


def test_inadequate_response_stops_at_lower_domain_crossing_and_keeps_prefix():
    machine = ready(config={"governor_tau_seconds": .8, "governor_delay_seconds": .2})
    receipt = machine.advance(10., 850.)
    assert receipt["domain_exceeded"]
    assert receipt["domain_reason"] == "speed_below_research_domain"
    assert 0. < receipt["actual_seconds"] < 10.
    assert machine.speed_pu == pytest.approx(.9, abs=2e-9)
    assert receipt["electrical_work_kWh"] == pytest.approx(
        850. * receipt["actual_seconds"] / 3600.)
    assert_conserved(receipt)


def test_unready_connected_machine_does_not_receive_free_nominal_inertia():
    machine = rotor()
    receipt = machine.advance(1., 10.)
    assert receipt["domain_exceeded"]
    assert receipt["actual_seconds"] == 0.
    assert machine.rotor_energy_kWs == 0.


def test_breaker_open_cannot_deliver_electricity_and_coast_keeps_energy_account():
    machine = ready()
    with pytest.raises(ValueError, match="disconnected"):
        machine.advance(1., 10., connected=False, run_enabled=False)
    receipt = machine.advance(120., 0., connected=False, run_enabled=False)
    assert not receipt["domain_exceeded"]
    assert 0. < machine.speed_pu < .9
    assert receipt["electrical_work_kWh"] == 0.
    assert receipt["loss_work_kWh"] > 0.
    assert_conserved(receipt)


def test_no_loss_coast_does_not_delete_rotating_energy():
    machine = ready(config={"shaft_loss_fraction": 0.})
    receipt = machine.advance(30., 0., connected=False, run_enabled=False)
    assert machine.rotor_energy_kWs >= machine.nominal_rotor_energy_kWs
    assert receipt["loss_work_kWh"] == 0.
    assert_conserved(receipt)


def test_deepcopy_preview_preserves_original_state_and_delay_history():
    machine = ready()
    before = copy.deepcopy(machine.as_dict())
    preview = copy.deepcopy(machine)
    preview.advance(.2, 200.)
    assert machine.as_dict() == before
    left = machine.advance(.2, 100.)
    identical = ready().advance(.2, 100.)
    assert left["state_after"] == identical["state_after"]


def test_split_advance_preserves_delay_and_work_state():
    whole = ready()
    split = copy.deepcopy(whole)
    a = whole.advance(2., 200.)
    b = split.advance(.7, 200.)
    c = split.advance(1.3, 200.)
    assert split.speed_pu == pytest.approx(whole.speed_pu, abs=3e-5)
    assert b["mechanical_work_kWh"] + c["mechanical_work_kWh"] == pytest.approx(
        a["mechanical_work_kWh"], abs=1e-5)
    assert_conserved(a)
    assert_conserved(b)
    assert_conserved(c)


@pytest.mark.parametrize("config", [
    {"inertia_H_seconds": 0.}, {"power_factor_base": 1.1},
    {"governor_tau_seconds": float("nan")}, {"governor_delay_seconds": -1.},
    {"shaft_loss_fraction": -.1}, {"speed_min_pu": 1.01},
    {"speed_max_pu": .99}, {"speed_deadband_pu": True}, {"future_load": 1.},
])
def test_invalid_or_unknown_parameters_are_rejected(config):
    with pytest.raises(ValueError):
        rotor(config=config)


@pytest.mark.parametrize("seconds,power", [(-1., 0.), (1., -1.), (1., 901.), (float("inf"), 0.)])
def test_invalid_interval_or_active_rating_excess_rejected_before_mutation(seconds, power):
    machine = ready()
    before = machine.as_dict()
    with pytest.raises(ValueError):
        machine.advance(seconds, power)
    assert machine.as_dict() == before


def test_long_stable_interval_has_exact_account_and_no_future_external_inputs():
    machine = ready()
    machine.advance(20., 100.)
    receipt = machine.advance(3600., 100.)
    assert not receipt["domain_exceeded"]
    assert receipt["actual_seconds"] == 3600.
    assert receipt["electrical_work_kWh"] == pytest.approx(100.)
    assert abs(machine.speed_pu - 1.) <= .0011
    assert_conserved(receipt)


def test_upper_speed_crossing_retains_energy_instead_of_clipping_it_away():
    # Deliberately isolate slow shaft rejection with no sensing delay. 200kW
    # did not cross the domain after friction; 300kW does. The domain and
    # conservation assertions stay unchanged; no model parameter is fitted.
    machine = ready(config={"governor_tau_seconds": .8, "governor_delay_seconds": 0.})
    settled = machine.advance(30., 300.)
    assert not settled["domain_exceeded"]
    receipt = machine.advance(10., 0., connected=False, run_enabled=False)
    assert receipt["domain_exceeded"]
    assert receipt["domain_reason"] == "speed_above_research_domain"
    assert 0. < receipt["actual_seconds"] < 10.
    assert machine.speed_pu == pytest.approx(1.1, abs=2e-9)
    assert receipt["rotor_delta_kWh"] > 0.
    assert_conserved(receipt)


def test_resolved_step_halving_preserves_supported_transient():
    default = ready()
    finer = ready(config={"integration_max_step_seconds": .003})
    left = default.advance(2., 200.)
    right = finer.advance(2., 200.)
    assert not left["domain_exceeded"] and not right["domain_exceeded"]
    assert default.speed_pu == pytest.approx(finer.speed_pu, abs=4e-6)
    assert left["mechanical_work_kWh"] == pytest.approx(
        right["mechanical_work_kWh"], abs=2e-6)
    assert_conserved(right)


def test_zero_delay_is_finite_actuator_response_and_zero_duration_cannot_start():
    machine = ready(config={"governor_delay_seconds": 0.})
    before = machine.mechanical_kW
    receipt = machine.advance(.001, 200.)
    assert before < machine.mechanical_kW < 200.
    assert_conserved(receipt)
    stopped = rotor()
    with pytest.raises(ValueError, match="zero seconds"):
        stopped.spin_up(0., 0., 1.)
    assert stopped.rotor_energy_kWs == 0.

