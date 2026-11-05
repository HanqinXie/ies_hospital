"""Independent dimensional/resource counterexamples; no hospital episodes."""
import importlib.util
import json
import math
from pathlib import Path

import pytest


@pytest.fixture
def bounds():
    path = Path(__file__).resolve().parents[1] / 'tools/engineering_basis_study/bounds.py'
    if not path.is_file():
        class MissingImplementation:
            def __getattr__(self, name):
                pytest.fail('Missing pure engineering-bounds feature: ' + name)
        return MissingImplementation()
    spec = importlib.util.spec_from_file_location('engineering_bounds_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cooling(**updates):
    value = dict(electric_chillers_rt=[200., 200., 300.], absorption_rt=200.,
                 tower_rejection_kW=4220.223408 * .01, tower_semantics_factor=1.,
                 ec_all_in_kW_per_rt=.77, ec_condenser_kW_per_rt=.77,
                 absorption_electric_kW_per_rt=.14, absorption_steam_lb_per_rt=19.5,
                 steam_kWh_per_lb=.30, steam_available_lbph=None,
                 electric_available_kW=None)
    value.update(updates)
    return value


def store(**updates):
    value = dict(carrier='c', capacity_kWh=60., power_kW=120., initial_kWh=60.,
                 charge_efficiency=.95, discharge_efficiency=.95, loss_per_hour=0.)
    value.update(updates)
    return value


def transfer(**updates):
    value = dict(destination_supply_qualified=True, open_gap_hours=.25/60.,
                 station_path_available=True, station_aux_required_kW=.5,
                 open_non_ups_station_supply_lower_kW=0., building_ups_available_kW=100.,
                 boiler_ready_before_open=True, boiler_request_held=True,
                 restart_power_required_kW=.5, post_non_ups_station_supply_lower_kW=10.,
                 restart_required_hours=1./60., post_observed_hours=1./60.,
                 post_station_continuous=True)
    value.update(updates)
    return value


def test_original_g6_bound_is_joint_tower_energy_bound(bounds):
    cold = bounds.cold_generation_upper_bound(cooling())
    assert cold['maximum_cooling_kW_c'] == pytest.approx(34.621913165228406)
    assert cold['maximum_cooling_kW_c'] < 900 * 3.51685284
    result = bounds.ideal_recharge_necessary_bound(store(), [dict(
        duration_hours=1., demand_kW=98.87851037496024,
        generation_upper_kW=cold['maximum_cooling_kW_c'])])
    assert result['necessary_ENS_lower_bound_kWh'] == pytest.approx(7.2565972097318365)
    assert result['conclusion'] == 'positive_necessary_shortage'


def test_alternate_tower_unit_zero_bound_does_not_prove_feasible(bounds):
    cold = bounds.cold_generation_upper_bound(cooling(tower_semantics_factor=1.25))
    assert cold['maximum_cooling_kW_c'] == pytest.approx(43.27739145653551)
    result = bounds.ideal_recharge_necessary_bound(store(), [dict(
        duration_hours=1., demand_kW=98.87851037496024,
        generation_upper_kW=cold['maximum_cooling_kW_c'])])
    assert result['necessary_ENS_lower_bound_kWh'] == 0.
    assert result['conclusion'] == 'cannot_prove_infeasible'
    assert result['sufficient_feasibility_proved'] is False


def test_ec_heat_split_zero_does_not_zero_electric_all_in_cost(bounds):
    result = bounds.cold_generation_upper_bound(cooling(ec_condenser_kW_per_rt=0.))
    witness = result['branches'][0]['witness']
    assert result['maximum_cooling_kW_c'] == pytest.approx(42.20223408)
    assert witness['EC_electric_kW'] == pytest.approx(witness['EC_RT'] * .77)
    assert witness['EC_electric_kW'] > 0.


def test_unknown_ec_heat_split_returns_explicit_envelope(bounds):
    result = bounds.cold_generation_upper_bound(cooling(ec_condenser_kW_per_rt=None))
    assert result['ec_condenser_kW_per_rt'] is None
    assert result['minimum_of_upper_bounds_kW_c'] == pytest.approx(34.621913165228406)
    assert result['maximum_cooling_kW_c'] == pytest.approx(42.20223408)
    assert len(result['branches']) == 2
    assert result['interpretation'] == 'optimistic_resource_upper_bound_not_dispatch'


def test_steam_and_electric_limits_can_favor_abs_over_ec(bounds):
    result = bounds.cold_generation_upper_bound(cooling(
        tower_rejection_kW=1000., electric_chillers_rt=[10.], absorption_rt=10.,
        electric_available_kW=1.4, steam_available_lbph=195.))
    witness = result['branches'][0]['witness']
    assert witness['ABS_RT'] == pytest.approx(10.)
    assert witness['ABS_steam_lbph'] == pytest.approx(195.)
    assert witness['ABS_electric_kW'] == pytest.approx(1.4)
    assert witness['EC_RT'] == pytest.approx(0.)


def test_zero_tower_and_nameplate_caps_are_retained(bounds):
    assert bounds.cold_generation_upper_bound(cooling(tower_rejection_kW=0.))['maximum_cooling_kW_c'] == 0.
    result = bounds.cold_generation_upper_bound(cooling(
        tower_rejection_kW=1e6, electric_chillers_rt=[1., 2.], absorption_rt=1.,
        steam_available_lbph=0.))
    assert result['maximum_cooling_kW_c'] == pytest.approx(3 * 3.51685284)


def test_storage_uses_original_exponential_then_charge_discharge_recurrence(bounds):
    result = bounds.storage_prefix_replay(store(initial_kWh=10., loss_per_hour=.1), [
        dict(duration_hours=1., charge_kW=2., discharge_kW=0.),
        dict(duration_hours=.5, charge_kW=0., discharge_kW=3.)])
    first = 10 * math.exp(-.1) + 2 * .95
    expected = first * math.exp(-.05) - 1.5 / .95
    assert result['status'] == 'pass'
    assert result['final_kWh'] == pytest.approx(expected)
    assert result['rows'][0]['standing_loss_kWh'] == pytest.approx(10 * (1-math.exp(-.1)))


def test_storage_power_failure_is_not_repaired_by_large_energy(bounds):
    result = bounds.storage_prefix_replay(store(power_kW=1.), [
        dict(duration_hours=.01, charge_kW=0., discharge_kW=2.)])
    assert result['status'] == 'fail'
    assert 'discharge_power' in result['rows'][0]['violations']


def test_storage_early_overdraft_survives_later_refill(bounds):
    result = bounds.storage_prefix_replay(store(initial_kWh=0., charge_efficiency=1., discharge_efficiency=1.), [
        dict(duration_hours=1., charge_kW=0., discharge_kW=10.),
        dict(duration_hours=1., charge_kW=10., discharge_kW=0.)])
    assert result['final_kWh'] == pytest.approx(0.)
    assert result['status'] == 'fail'
    assert result['first_failed_prefix'] == 0
    assert result['rows'][0]['final_kWh'] == -10.


@pytest.mark.parametrize('row,violation', [
    (dict(duration_hours=1., charge_kW=1., discharge_kW=1.), 'simultaneous_charge_discharge'),
    (dict(duration_hours=1., charge_kW=1., discharge_kW=0.), 'capacity_overfill'),
])
def test_storage_does_not_clip_invalid_actual_prefixes(bounds, row, violation):
    result = bounds.storage_prefix_replay(store(), [row])
    assert result['status'] == 'fail'
    assert violation in result['rows'][0]['violations']


def test_unknown_storage_flow_is_not_filled_with_zero(bounds):
    result = bounds.storage_prefix_replay(store(), [dict(duration_hours=1., charge_kW=None, discharge_kW=1.)])
    assert result['status'] == 'unknown'
    assert result['final_kWh'] is None
    assert result['rows'][0]['charge_kW'] is None


def test_ideal_recharge_can_cover_later_deficit(bounds):
    result = bounds.ideal_recharge_necessary_bound(store(initial_kWh=0., capacity_kWh=20.), [
        dict(duration_hours=1., demand_kW=0., generation_upper_kW=10.),
        dict(duration_hours=1., demand_kW=10., generation_upper_kW=0.)])
    assert result['necessary_ENS_lower_bound_kWh'] == 0.
    assert result['rows'][0]['useful_stock_after_kWh'] == 10.
    assert result['conclusion'] == 'cannot_prove_infeasible'


def test_later_ideal_recharge_cannot_erase_early_shortage(bounds):
    result = bounds.ideal_recharge_necessary_bound(store(initial_kWh=0.), [
        dict(duration_hours=1., demand_kW=10., generation_upper_kW=0.),
        dict(duration_hours=1., demand_kW=0., generation_upper_kW=10.)])
    assert result['necessary_ENS_lower_bound_kWh'] == 10.


def test_ideal_recharge_still_retains_capacity_and_discharge_power(bounds):
    result = bounds.ideal_recharge_necessary_bound(store(initial_kWh=0., capacity_kWh=2., power_kW=1., discharge_efficiency=1.), [
        dict(duration_hours=1., demand_kW=0., generation_upper_kW=100.),
        dict(duration_hours=1., demand_kW=4., generation_upper_kW=0.)])
    assert result['necessary_ENS_lower_bound_kWh'] == 3.


def test_unknown_generation_bound_remains_unknown(bounds):
    result = bounds.ideal_recharge_necessary_bound(store(), [dict(
        duration_hours=1., demand_kW=1., generation_upper_kW=None)])
    assert result['necessary_ENS_lower_bound_kWh'] is None
    assert result['conclusion'] == 'unknown'


def test_bridge_unknown_warmup_reports_acceptance_deadline_not_invented_tau(bounds):
    result = bounds.startup_bridge_requirements(store(initial_kWh=10., discharge_efficiency=1.), [dict(
        duration_hours=2., demand_upper_kW=10., online_supply_lower_kW=5., paths_qualified=True)])
    assert result['minimum_initial_kWh'] == 10.
    assert result['maximum_covered_hours'] == pytest.approx(2.)
    assert result['maximum_acceptable_ready_delay_hours'] == pytest.approx(2.)
    assert result['new_source_ready_after_hours'] is None
    assert result['bridge_horizon_sufficient'] is True
    assert result['new_source_handoff_sufficient'] is None


def test_bridge_loss_minimum_inventory_and_partial_depletion(bounds):
    result = bounds.startup_bridge_requirements(store(initial_kWh=10., discharge_efficiency=1., loss_per_hour=.1), [dict(
        duration_hours=2., demand_upper_kW=10., online_supply_lower_kW=5., paths_qualified=True)])
    assert result['minimum_initial_kWh'] == pytest.approx(10 * math.exp(.2))
    assert 0 < result['maximum_covered_hours'] < 2
    assert result['bridge_horizon_sufficient'] is False


def test_bridge_instantaneous_power_deficit_has_zero_coverage(bounds):
    result = bounds.startup_bridge_requirements(store(power_kW=1.), [dict(
        duration_hours=1., demand_upper_kW=10., online_supply_lower_kW=0., paths_qualified=True)])
    assert result['maximum_covered_hours'] == 0.
    assert result['power_qualified'] is False
    assert result['bridge_horizon_sufficient'] is False


def test_bridge_unknown_path_or_online_supply_cannot_certify(bounds):
    path = bounds.startup_bridge_requirements(store(), [dict(
        duration_hours=1., demand_upper_kW=1., online_supply_lower_kW=1., paths_qualified=None)])
    supply = bounds.startup_bridge_requirements(store(), [dict(
        duration_hours=1., demand_upper_kW=1., online_supply_lower_kW=None, paths_qualified=True)])
    assert path['bridge_horizon_sufficient'] is None
    assert supply['minimum_initial_kWh'] is None


def test_destination_proof_and_building_ups_do_not_bridge_station(bounds):
    result = bounds.transfer_path_qualification(transfer())
    assert result['destination']['qualified'] is True
    assert result['open_station']['qualified'] is False
    assert result['boiler_ready_after_open'] is False
    assert result['post_restart']['qualified'] is True
    assert result['building_UPS_is_station_source'] is False


def test_station_bridge_preserves_ready_without_fake_zero_restart_time(bounds):
    result = bounds.transfer_path_qualification(transfer(open_non_ups_station_supply_lower_kW=.5))
    assert result['open_station']['qualified'] is True
    assert result['boiler_ready_after_open'] is True
    assert result['post_restart']['status'] == 'not_applicable'
    assert result['restart_completion_hours_after_return'] is None


def test_unknown_restart_is_not_claimed_ready(bounds):
    result = bounds.transfer_path_qualification(transfer(restart_required_hours=None))
    assert result['post_restart']['qualified'] is None
    assert result['restart_completion_hours_after_return'] is None


def test_unknown_prior_ready_does_not_create_observed_restart_endpoint(bounds):
    result = bounds.transfer_path_qualification(transfer(
        boiler_ready_before_open=None, open_non_ups_station_supply_lower_kW=1.))
    assert result['boiler_ready_after_open'] is None
    assert result['post_restart']['qualified'] is True
    assert result['restart_completion_hours_after_return'] is None


def test_request_revocation_loses_ready_even_with_station_electricity(bounds):
    result = bounds.transfer_path_qualification(transfer(
        boiler_request_held=False, open_non_ups_station_supply_lower_kW=100.))
    assert result['boiler_ready_after_open'] is False
    assert result['post_restart']['qualified'] is False


def test_sufficient_certificate_requires_real_supply_lower_and_all_paths(bounds):
    inventory = bounds.storage_prefix_replay(store(), [dict(duration_hours=1., charge_kW=0., discharge_kW=1.)])
    good = dict(duration_hours=1., demand_upper_kW=2., supply_lower_kW=1., paths_qualified=True)
    assert bounds.service_sufficiency_certificate(inventory, [good])['conditional_sufficient'] is True
    assert bounds.service_sufficiency_certificate(inventory, [dict(good, supply_lower_kW=None)])['conditional_sufficient'] is None
    assert bounds.service_sufficiency_certificate(inventory, [dict(good, paths_qualified=None)])['conditional_sufficient'] is None


def test_service_certificate_requires_numerical_balance_after_storage_charge(bounds):
    inventory = bounds.storage_prefix_replay(store(initial_kWh=10.), [dict(duration_hours=1., charge_kW=1., discharge_kW=0.)])
    result = bounds.service_sufficiency_certificate(inventory, [dict(
        duration_hours=1., demand_upper_kW=2., supply_lower_kW=2., paths_qualified=True)])
    assert result['conditional_sufficient'] is False
    assert result['rows'][0]['service_margin_kW'] == -1.


def test_service_certificate_rejects_unbound_time_intervals(bounds):
    inventory = bounds.storage_prefix_replay(store(), [dict(duration_hours=1., charge_kW=0., discharge_kW=0.)])
    with pytest.raises(ValueError, match='interval'):
        bounds.service_sufficiency_certificate(inventory, [dict(
            duration_hours=2., demand_upper_kW=0., supply_lower_kW=0., paths_qualified=True)])


def test_boolean_attestation_without_numeric_balance_cannot_certify(bounds):
    inventory = bounds.storage_prefix_replay(store(), [dict(duration_hours=1., charge_kW=0., discharge_kW=0.)])
    flags = dict(demand_upper_bound_known=True, supply_lower_bound_known=True,
                 power_qualified=True, paths_qualified=True)
    with pytest.raises(ValueError, match='interval'):
        bounds.service_sufficiency_certificate(inventory, flags)


def test_service_certificate_rejects_unknown_storage_result_fields(bounds):
    inventory = bounds.storage_prefix_replay(store(), [])
    with pytest.raises(ValueError, match='Unknown'):
        bounds.service_sufficiency_certificate(dict(inventory, hidden_override=True), [])


def test_service_certificate_cannot_trust_a_forged_pass_status(bounds):
    inventory = bounds.storage_prefix_replay(store(initial_kWh=0.), [dict(
        duration_hours=1., charge_kW=0., discharge_kW=1.)])
    assert inventory['status'] == 'fail'
    inventory['status'] = 'pass'
    inventory['first_failed_prefix'] = None
    with pytest.raises(ValueError):
        bounds.service_sufficiency_certificate(inventory, [dict(
            duration_hours=1., demand_upper_kW=1., supply_lower_kW=0., paths_qualified=True)])


def test_service_certificate_recomputes_the_recorded_inventory_witness(bounds):
    inventory = bounds.storage_prefix_replay(store(), [dict(
        duration_hours=1., charge_kW=0., discharge_kW=0.)])
    inventory['rows'][0]['discharge_kW'] = 1000.
    with pytest.raises(ValueError):
        bounds.service_sufficiency_certificate(inventory, [dict(
            duration_hours=1., demand_upper_kW=1000., supply_lower_kW=0., paths_qualified=True)])


def test_service_certificate_requires_a_nonempty_observation(bounds):
    inventory = bounds.storage_prefix_replay(store(), [])
    with pytest.raises(ValueError):
        bounds.service_sufficiency_certificate(inventory, [])


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1., True])
def test_rejects_invalid_numeric_resource_inputs(bounds, bad):
    with pytest.raises(ValueError):
        bounds.cold_generation_upper_bound(cooling(tower_rejection_kW=bad))
    with pytest.raises(ValueError):
        bounds.storage_prefix_replay(store(initial_kWh=bad), [])


def test_rejects_unknown_fields_and_carrier_mixing(bounds):
    with pytest.raises(ValueError, match='Unknown'):
        bounds.cold_generation_upper_bound(cooling(hidden_capacity=100.))
    with pytest.raises(ValueError, match='Unknown'):
        bounds.storage_prefix_replay(store(), [dict(duration_hours=1., charge_kW=0., discharge_kW=0., hidden=1)])
    with pytest.raises(ValueError):
        bounds.storage_prefix_replay(store(carrier='e+h+c'), [])
    with pytest.raises(ValueError):
        bounds.cold_generation_upper_bound(cooling(ec_condenser_kW_per_rt=.78))


def test_all_results_are_json_serializable_without_nonfinite_values(bounds):
    outputs = [bounds.cold_generation_upper_bound(cooling(ec_condenser_kW_per_rt=None)),
               bounds.storage_prefix_replay(store(), []),
               bounds.ideal_recharge_necessary_bound(store(), []),
               bounds.startup_bridge_requirements(store(), []),
               bounds.transfer_path_qualification(transfer())]
    assert json.loads(json.dumps(outputs, allow_nan=False)) == outputs
