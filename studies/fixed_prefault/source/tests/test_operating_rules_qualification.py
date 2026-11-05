"""Independent arithmetic and finite-qualification tests; no plant episodes."""
import copy
import importlib
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASE = 'doe_capacity_matched_exploratory'


def api():
    name = 'tools.operating_rules_study.qualification'
    assert importlib.util.find_spec(name) is not None, 'read-only qualification module missing'
    return importlib.import_module(name)


def config():
    return json.loads((ROOT / 'configs/plant_configs.json').read_text(encoding='utf8'))


def test_recomputes_scaled_equipment_without_hospital_simulation():
    p = api().independent_plant(config(), CASE)
    assert p['gt_rated_kW'] == 320.
    assert p['electric_chillers_rt'] == [200., 200., 300.]
    assert p['absorption_rt'] == 200.
    assert p['boiler_steam_lbph'] == 3000.
    assert p['cooling_tower_rejection_kW'] == pytest.approx(4220.223408)
    assert p['emergency_diesel_kW'] == 900.


def test_hrsg_prospective_fit_uses_original_module_before_scaling():
    p = api().independent_plant(config(), CASE)
    original_fit = 5623.74 * math.exp(.0003202 * 800.)
    assert p['hrsg_rated_lbph'] == pytest.approx(original_fit * .4)
    assert p['hrsg_rated_lbph'] != pytest.approx(5623.74 * math.exp(.0003202 * 320.))
    assert p['hrsg_rated_lbph'] != pytest.approx(7265. * .4, abs=.1)


def test_gt_temperature_and_partload_use_original_800kw_heat_rate():
    point = api().independent_gt_points(config(), CASE, 15.)[0]
    heat_rate = 29477. - 1954.9 * math.log(800.)
    assert point['fraction'] == .4
    assert point['electric_kW'] == pytest.approx(128.)
    assert point['fuel_MMBtu_h'] == pytest.approx(128. * heat_rate * .9851 * .4 ** (-.327) / 1e6)
    assert point['runtime_linear_fuel_MMBtu_h'] < point['fuel_MMBtu_h']


def test_gt_ambient_piecewise_formula_and_nonnegative_power():
    low = api().independent_gt_points(config(), CASE, 0.)[-1]
    high = api().independent_gt_points(config(), CASE, 35.)[-1]
    assert low['ambient_heat_rate_factor'] == pytest.approx(.965544 + .000584 * 32.)
    assert high['ambient_heat_rate_factor'] == pytest.approx(.910320 + .00152 * 95.)
    assert high['electric_kW'] == pytest.approx(320. * (1.23836 - .00404 * 95.))
    assert api().independent_gt_points(config(), CASE, 200.)[-1]['electric_kW'] == 0.


def test_independent_gt_reads_configuration_coefficients_instead_of_hardcoding():
    changed = copy.deepcopy(config())
    changed['historical_pnnl_performance']['gt_hhv_heat_rate_Btu_per_kWh_constant'] += 1000.
    changed_fuel = api().independent_gt_points(changed, CASE, 15.)[-1]['fuel_MMBtu_h']
    original_fuel = api().independent_gt_points(config(), CASE, 15.)[-1]['fuel_MMBtu_h']
    assert changed_fuel - original_fuel == pytest.approx(320. * 1000. * .9851 / 1e6)


def test_boiler_absorption_electric_chiller_and_shared_tower_arithmetic():
    b = api().converter_envelope(config(), CASE)
    assert b['boiler_steam_kW_th'] == 900.
    assert b['boiler_building_heat_kW'] == 855.
    assert b['boiler_fuel_MMBtu_h'] == pytest.approx(900. / .8 / 293.0710702)
    assert b['ABS_steam_lbph'] == 3900.
    # 200*.14 differs from 28 by 3.55e-15 in binary floats; no physical tolerance changes.
    assert b['ABS_electric_kW'] == pytest.approx(28., rel=0., abs=1e-12)
    assert b['EC_all_in_electric_kW'] == 539.
    assert b['all_chillers_nameplate_cooling_kW'] == pytest.approx(900. * 3.51685284)
    assert b['all_chillers_shared_tower_feasible'] is False
    assert b['all_chillers_tower_excess_kW'] > 650.
    assert b['EC_RT_if_ABS_at_nameplate_and_tower_only'] < 700.
    assert b['boiler_alone_supports_ABS_nameplate'] is False


@pytest.mark.parametrize('minute,used,control,start', [
    (0., 0, True, True), (1439.999, 1, True, True),
    (1440., 0, False, False), (10., 2, True, False), (525600., 0, False, False),
])
def test_finite_station_rights_have_strict_deadline_and_two_actual_starts(minute, used, control, start):
    station = {'control_autonomy_minutes': 1440., 'dg_qualified_starts': 2,
               'gt_control_dc_available': True, 'dg_start_dc_available': True}
    got = api().finite_station_rights(station, minute=minute, starts_used=used)
    assert got['GT_control_available'] is control
    assert got['DG_start_available'] is start
    assert got['recharge_model_present'] is False


@pytest.mark.parametrize('minute,used', [(-1., 0), (float('nan'), 0), (0., 3), (0., .5), (0., True)])
def test_finite_rights_reject_unknown_or_invalid_state(minute, used):
    station = {'control_autonomy_minutes': 1440., 'dg_qualified_starts': 2,
               'gt_control_dc_available': True, 'dg_start_dc_available': True}
    with pytest.raises(ValueError):
        api().finite_station_rights(station, minute=minute, starts_used=used)


def test_actual_source_bound_report_keeps_arithmetic_and_qualification_separate():
    report = api().build_qualification(ROOT)
    assert report['mathematical_agreement']['all_passed'] is True
    assert report['mathematical_agreement']['numeric_checks'] > 150
    assert report['finite_station']['actual_pure_component_probes_match'] is True
    assert report['annual_continuous']['ready_to_run_directly'] is False
    assert report['annual_continuous']['daily_reset_is_valid_continuity'] is False
    assert report['annual_continuous']['year_minutes'] == 525600.
    ids = {x['id'] for x in report['annual_continuous']['missing_materials']}
    assert {'continuous_station_control', 'DC_charge_and_requalification', 'replenishment_contract',
            'year_end_state_contract', 'sealed_support_authorization'} <= ids
    assert report['qualifications']['room_temperature_coupling_qualified'] is False
    assert report['qualifications']['dynamic_chilled_water_interface_qualified'] is False
    assert report['qualifications']['measured_hospital_calibration'] is False
    assert report['DG_accounting']['chemical_fuel_qualified'] is False
    assert report['sealed_support']['original_final12_opened_by_this_audit'] is False
    assert len(report['sealed_support']['days']) == 12
    assert {x['classification'] for x in report['parameter_register']} >= {
        'historical_source', 'prospective_source_surrogate', 'research_scaling', 'unverified_research_assumption'}
    assert any(x['path'] == 'inputs/reference/nmc_sd_performance_parameters.csv' for x in report['bindings'])
    assert report['scope']['hospital_episodes_executed'] == 0
