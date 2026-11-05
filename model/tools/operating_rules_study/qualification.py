"""Read-only equipment arithmetic and finite research qualification audit.

No environment, episode, optimizer, load-value reader, or controller is created.
The independent formulas use configuration/source quantities before comparing
against the original model's pure parameter functions. Agreement is arithmetic
consistency, never measured hospital or OEM calibration.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
RT_TO_KW = 3.51685284
KWH_PER_MMBTU = 293.0710702
TEMPERATURES_C = (-10., 0., 14.999999, 15., 15.000001, 25., 35., 45.)


def independent_plant(config, case):
    """Reconstruct the exact registered parameter map without load_plant()."""
    s = config['source_equipment']
    f = config['historical_pnnl_performance']
    a = config['engineering_assumptions_not_from_source']
    c = config['study_cases'][case]
    original_gt = s['gas_turbines']['rated_electric_kW_each']
    hrsg_original = f['hrsg_steam_exponential_constant_lb_per_hr'] * math.exp(
        f['hrsg_steam_exponential_exponent_per_kW'] * original_gt)
    return dict(
        gt_count=s['gas_turbines']['count'], gt_rated_kW=original_gt * c['gas_turbine_scale'],
        hrsg_rated_lbph=hrsg_original * c['hrsg_scale'],
        absorption_rt=s['absorption_single_effect']['rated_cooling_RT_each'] * c['absorption_scale'],
        electric_chillers_rt=[v * c['electric_chiller_scale'] for v in
                             s['centrifugal_electric_chillers']['rated_cooling_RT_each']],
        boiler_steam_lbph=s['boilers']['count'] * s['boilers']['rated_steam_lb_per_hr_each'] * c['boiler_scale'],
        cooling_tower_rejection_kW=s['cooling_towers']['count'] * s['cooling_towers']['rated_cooling_RT_each']
                                  * c['electric_chiller_scale'] * RT_TO_KW,
        emergency_diesel_kW=s['emergency_diesel_generators']['count']
                           * s['emergency_diesel_generators']['rated_electric_kW_each'] * c['diesel_scale'],
        steam_kWh_per_lb=a['kWh_thermal_per_lb_steam'],
        exchanger_eff=a['steam_to_building_heat_exchanger_efficiency'],
        abs_steam_lb_per_rt=f['existing_absorption_steam_lb_per_RT_hr'],
        abs_electric_kW_per_rt=f['existing_absorption_aux_kW_per_RT'],
        ec_electric_kW_per_rt=f['existing_electric_chiller_all_in_kW_per_RT'],
        boiler_eff=f['boiler_thermal_efficiency'],
        nominal_gt_capacity_used_for_heat_rate_kW=original_gt)


def independent_gt_points(config, case, temperature_C):
    """Static four-point fit and the distinct dynamic full-load-slope proxy."""
    if not math.isfinite(float(temperature_C)):
        raise ValueError('Finite temperature required')
    p = independent_plant(config, case)
    f = config['historical_pnnl_performance']
    fahrenheit = 32. + 9. * float(temperature_C) / 5.
    power_factor = max(0., 1.23836 - .00404 * fahrenheit)
    rate_factor = (.965544 + .000584 * fahrenheit if fahrenheit <= 59.
                   else .910320 + .00152 * fahrenheit)
    heat_rate = f['gt_hhv_heat_rate_Btu_per_kWh_constant'] - f['gt_hhv_heat_rate_Btu_per_kWh_ln_coeff'] * math.log(
        p['nominal_gt_capacity_used_for_heat_rate_kW'])
    result = []
    for fraction in config['engineering_assumptions_not_from_source']['gas_turbine_operating_load_fractions']:
        power = p['gt_rated_kW'] * power_factor * fraction
        correction = f['gt_partload_correction_multiplier'] * fraction ** f['gt_partload_correction_exponent']
        result.append(dict(fraction=fraction, temperature_C=float(temperature_C),
            ambient_power_factor=power_factor, ambient_heat_rate_factor=rate_factor,
            nominal_800kW_heat_rate_Btu_per_kWh=heat_rate,
            electric_kW=power, steam_lbph=p['hrsg_rated_lbph'] * fraction,
            fuel_MMBtu_h=power * heat_rate * correction * rate_factor / 1e6,
            runtime_linear_fuel_MMBtu_h=power * heat_rate * f['gt_partload_correction_multiplier'] * rate_factor / 1e6))
    return result


def converter_envelope(config, case):
    """Coincident nameplate arithmetic, not a dispatch or feasibility claim."""
    p = independent_plant(config, case)
    absorption, electric = p['absorption_rt'], sum(p['electric_chillers_rt'])
    abs_steam = absorption * p['abs_steam_lb_per_rt']
    abs_rejection = absorption * (RT_TO_KW + p['abs_steam_lb_per_rt'] * p['steam_kWh_per_lb'])
    ec_rejection = electric * (RT_TO_KW + p['ec_electric_kW_per_rt'])
    boiler_heat = p['boiler_steam_lbph'] * p['steam_kWh_per_lb']
    all_rejection = abs_rejection + ec_rejection
    return dict(boiler_steam_kW_th=boiler_heat,
        boiler_building_heat_kW=boiler_heat * p['exchanger_eff'],
        boiler_fuel_MMBtu_h=boiler_heat / p['boiler_eff'] / KWH_PER_MMBTU,
        ABS_cooling_kW=absorption * RT_TO_KW, ABS_steam_lbph=abs_steam,
        ABS_steam_kW_th=abs_steam * p['steam_kWh_per_lb'],
        ABS_electric_kW=absorption * p['abs_electric_kW_per_rt'],
        ABS_heat_input_COP_proxy=RT_TO_KW / (p['abs_steam_lb_per_rt'] * p['steam_kWh_per_lb']),
        EC_cooling_kW=electric * RT_TO_KW, EC_all_in_electric_kW=electric * p['ec_electric_kW_per_rt'],
        EC_all_in_COP_proxy=RT_TO_KW / p['ec_electric_kW_per_rt'],
        ABS_tower_rejection_kW=abs_rejection, EC_tower_rejection_kW=ec_rejection,
        all_chillers_nameplate_cooling_kW=(absorption + electric) * RT_TO_KW,
        all_chillers_tower_rejection_kW=all_rejection,
        shared_tower_limit_kW=p['cooling_tower_rejection_kW'],
        all_chillers_shared_tower_feasible=all_rejection <= p['cooling_tower_rejection_kW'],
        all_chillers_tower_excess_kW=max(0., all_rejection - p['cooling_tower_rejection_kW']),
        EC_RT_if_ABS_at_nameplate_and_tower_only=max(0., min(electric,
            (p['cooling_tower_rejection_kW'] - abs_rejection) / (RT_TO_KW + p['ec_electric_kW_per_rt']))),
        boiler_alone_supports_ABS_nameplate=p['boiler_steam_lbph'] >= abs_steam,
        qualification='Separate steam, electric, tower, auxiliary and actual command constraints still apply; '
                      '0.14/0.77 already include source condenser/tower auxiliaries; do not add them twice.')


def finite_station_rights(station, *, minute, starts_used):
    """Independent strict episode-clock deadline and actual-start token count."""
    deadline, starts = station['control_autonomy_minutes'], station['dg_qualified_starts']
    if (isinstance(minute, bool) or not math.isfinite(float(minute)) or float(minute) < 0.
        or isinstance(deadline, bool) or not math.isfinite(float(deadline)) or float(deadline) < 0.
        or type(starts) is not int or starts < 0 or type(starts_used) is not int or not 0 <= starts_used <= starts):
        raise ValueError('Invalid finite station qualification state')
    control = float(minute) < float(deadline)
    return dict(GT_control_available=bool(station['gt_control_dc_available'] and control),
        DG_start_available=bool(station['dg_start_dc_available'] and control and starts_used < starts),
        starts_remaining=starts - starts_used, recharge_model_present=False)


def _literal_assignment(path, name):
    for node in ast.parse(path.read_text(encoding='utf8')).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f'Missing literal assignment {name}: {path}')


def _false_keyword_evidence(path, name):
    values = [node.value.value for node in ast.walk(ast.parse(path.read_text(encoding='utf8')))
              if isinstance(node, ast.keyword) and node.arg == name and isinstance(node.value, ast.Constant)]
    return bool(values) and all(v is False for v in values)


def build_qualification(root=ROOT):
    """Read source/registration metadata and pure components, never load values."""
    root = Path(root).resolve()
    sys.path.insert(0, str(root / 'src'))
    from hospital_ies import model
    from tools.model_closure_v22.station import StationState
    from tools.model_correctness_v23.station_paths import DedicatedDCState
    if Path(model.__file__).resolve() != (root / 'src/hospital_ies/model.py').resolve():
        raise ValueError('The loaded model belongs to a different source root')
    read = lambda relative: json.loads((root / relative).read_text(encoding='utf8'))
    config = read('configs/plant_configs.json')
    registries = {v: read(f'configs/{v}/cases.json') for v in ('model_review_v30', 'model_closeout_v31')}
    checks, cases = [], {}
    def check(label, expected, actual):
        expected, actual = float(expected), float(actual)
        checks.append(dict(id=label, independent=expected, original=actual, absolute_difference=abs(expected-actual),
                           passed=math.isclose(expected, actual, rel_tol=1e-12, abs_tol=1e-9)))
    for case in config['study_cases']:
        original = model.load_plant(root / 'configs/plant_configs.json', case)
        independent = independent_plant(config, case)
        for field, expected in independent.items():
            actual = getattr(original, field)
            for i, (e, a) in enumerate(zip(expected, actual)) if isinstance(expected, list) else [(0, (expected, actual))]:
                check(f'{case}.plant.{field}.{i}', e, a)
        points = []
        for temperature in TEMPERATURES_C:
            original_levels = model.gt_level_properties(original, temperature)
            independent_levels = independent_gt_points(config, case, temperature)
            for j, item in enumerate(independent_levels):
                for i, field in enumerate(('electric_kW', 'steam_lbph', 'fuel_MMBtu_h')):
                    check(f'{case}.T={temperature}.fraction={item["fraction"]}.{field}', item[field], original_levels[i][j])
            points.extend(independent_levels)
        cases[case] = dict(plant=independent, GT_points=points, converter_envelope=converter_envelope(config, case))
    station = registries['model_review_v30']['model_closure_v22']['station']
    registration_agreement = all(v['model_closure_v22']['station'] == station for v in registries.values())
    pure_station = StationState(station)
    probes = []
    for minute, used in ((0., 0), (1439.999, 1), (1440., 0), (10., 2), (525600., 0)):
        actual_dc = DedicatedDCState(autonomy_minutes=station['control_autonomy_minutes'],
                                    qualified_starts=station['dg_qualified_starts'])
        for i in range(used):
            actual_dc = actual_dc.request(True).commit(float(i), hardware_available=True, start_token=f'audit-only-{i}')
        expected = finite_station_rights(station, minute=minute, starts_used=used)
        actual = dict(GT_control_available=pure_station.control_available(minute, station['gt_control_dc_available']),
            DG_start_available=actual_dc.available(minute, hardware_available=station['dg_start_dc_available']),
            starts_remaining=actual_dc.observe()['diesel_starts_remaining'],
            recharge_model_present=actual_dc.observe()['recharge_model_present'])
        probes.append(dict(minute=minute, starts_used=used, independent=expected, pure_original=actual,
                           passed=expected == actual))
    sealed = list(_literal_assignment(root / 'tools/sci_engineering/design.py', 'SEALED_DAYS'))
    thermal_disabled = all(v['model_correctness_v23']['thermal_coupling'] is None for v in registries.values())
    stress_disabled = all(v['model_closure_v22']['research_stress_proxy'] is False for v in registries.values())
    cold_false = _false_keyword_evidence(root / 'tools/model_correction_v26/cold_ledger.py', 'interface_qualified')
    with (root / 'inputs/reference/nmc_sd_performance_parameters.csv').open(encoding='utf-8-sig', newline='') as stream:
        performance_source_rows = list(csv.DictReader(stream))
    with (root / 'inputs/reference/nmc_sd_existing_equipment.csv').open(encoding='utf-8-sig', newline='') as stream:
        equipment_source_rows = list(csv.DictReader(stream))
    parameter_register = []
    def register(item, classification, evidence, limitation):
        parameter_register.append(dict(item=item, classification=classification, evidence=evidence, limitation=limitation))
    register('historical equipment count and ratings', 'historical_source', equipment_source_rows,
             '2001 source plant; DOE building is not identified as that real hospital; makes/models absent')
    register('existing ABS 19.5 lb/(RT h), 0.14 kW/RT; EC 0.77 kW/RT; boiler efficiency 0.80',
             'historical_source', 'inputs/reference/nmc_sd_performance_parameters.csv, source pages 3 and 8',
             'Source modeling parameters; not measured present-hospital off-design performance')
    register('GT temperature, nominal heat rate and part-load fits; HRSG exponential fit',
             'prospective_source_surrogate', [r for r in performance_source_rows if 'GT ' in r['parameter'] or 'HRSG ' in r['parameter']],
             'Prospective equipment fits transferred to original modules; HRSG fit at 800 kW then scaled, not old-unit measured calibration')
    register('module-equivalent research capacities', 'research_scaling', config['study_cases'],
             'Scale factors are synthetic study cases, not marketed nameplates or load/capacity tuning in this audit')
    register('steam enthalpy 0.30 kWh/lb; exchanger 0.95; linear HRSG fraction and four load points',
             'unverified_research_assumption', config['engineering_assumptions_not_from_source'],
             'Steam pressure, temperature, condensate condition and real load curves not identified')
    register('shared tower rating RT conversion and off-design duty', 'unverified_research_assumption',
             'src/hospital_ies/model.py and inputs/reference/nmc_sd_performance_parameters.csv',
             'Nominal tower RT is not an OEM wet-bulb/approach/flow-dependent condenser duty; explicit bank constraint retained')
    register('runtime GT continuous 40-100% full-load fuel/steam slopes', 'unverified_research_assumption',
             'tools/operating_response/physical_balance.py:solve_physical',
             'Static four-point part-load fuel curve differs from runtime full-load-slope approximation; neither is calibrated here')
    register('station/DC 1440 episode minutes and two actual start attempts', 'unverified_research_assumption', station,
             'Finite functional rights, not Ah/kWh/CCA autonomy; hardware restoration does not recharge or renew rights')
    register('DG shaft-equivalent inventory and transferred rotor parameters', 'unverified_research_assumption',
             'tools/operating_response/diesel.py and current hardware_profiles',
             'Mechanical work budget is not liters, chemical heat or a calibrated fuel-consumption curve')
    register('historical 2001 prices and illustrative time bands', 'unverified_research_assumption',
             config['historical_2001_prices_USD'], 'Price values are source historical values; weekday/hour mapping is unverified and no current tariff is asserted')
    missing = [
        dict(id='continuous_station_control', required='Actual continuous GT control/DC supply topology, sources, loads, failure paths and continuity beyond 1440 episode minutes.'),
        dict(id='DC_charge_and_requalification', required='Battery/charger ratings and measured capacity, charge path, energy/CCA and start-work accounting; lawful recharge/requalification rule.'),
        dict(id='replenishment_contract', required='DG shaft-to-chemical fuel map if fuel claims are needed, initial stock, finite NG/diesel/storage replenishment schedule, rates and availability; no free daily refill.'),
        dict(id='year_end_state_contract', required='Continuous initial/final GT/rotor/DC/store/queue/contact state and cost contract, maintenance and endpoint accounting; no daily reset.'),
        dict(id='sealed_support_authorization', required='A separately authorized annual data protocol that respects the original 12 sealed day supports; the present task does not authorize opening them.'),
        dict(id='off_design_validation', required='GT/HRSG/boiler/chiller/tower operating curves and actual hydraulic/electrical topology before plant-specific or OEM performance claims.'),
    ]
    binding_paths = ['configs/plant_configs.json', 'configs/model_review_v30/cases.json', 'configs/model_closeout_v31/cases.json',
        'inputs/reference/nmc_sd_existing_equipment.csv', 'inputs/reference/nmc_sd_performance_parameters.csv',
        'inputs/reference/provenance.json', 'inputs/sci_engineering/AUXILIARY_PARAMETER_AND_SOURCE_REGISTER.csv',
        'src/hospital_ies/model.py', 'tools/operating_response/physical_balance.py', 'tools/operating_response/diesel.py',
        'tools/model_closure_v22/station.py', 'tools/model_closure_v22/environment.py',
        'tools/model_correctness_v23/station_paths.py', 'tools/model_correctness_v23/environment.py',
        'tools/model_review_v28/validation.py', 'tools/model_correction_v26/cold_ledger.py',
        'tools/sci_engineering/design.py', 'docs/operating_rules_study/IMPLEMENTATION_PLAN.md',
        'tools/operating_rules_study/qualification.py']
    bindings = [dict(path=path, size_bytes=(root / path).stat().st_size,
                     sha256=hashlib.sha256((root / path).read_bytes()).hexdigest()) for path in binding_paths]
    return dict(schema='hospital_ies.operating_rules.qualification.20261029.v1', root=str(root),
        scope=dict(hospital_episodes_executed=0, optimizers_run=0, annual_load_values_read=False,
                   original_inputs_or_results_written=False, purpose='Independent arithmetic and source/qualification inventory'),
        mathematical_agreement=dict(all_passed=all(c['passed'] for c in checks), numeric_checks=len(checks),
                                    max_absolute_difference=max(c['absolute_difference'] for c in checks), checks=checks),
        cases=cases, parameter_register=parameter_register,
        finite_station=dict(registered_station=station, registered_v30_v31_agree=registration_agreement,
            actual_pure_component_probes_match=all(p['passed'] for p in probes), boundary_probes=probes,
            deadline_clock='episode elapsed time, strict minute < 1440, not time since grid failure',
            actual_failed_start_consumes_qualification=True, queued_or_cancelled_request_is_not_actual_start=True,
            recharge_model_present=False, hardware_recovery_renews_rights=False),
        DG_accounting=dict(inventory_unit='kWh shaft-equivalent mechanical work', chemical_fuel_qualified=False,
            chemical_fuel_liters=None, chemical_energy_MMBtu=None,
            balance='shaft work = electrical work + shaft loss work + rotor energy change',
            limitation='Rotor energy is not an extra electrical store; no chemical fuel or runtime-autonomy claim follows from the shaft budget.'),
        qualifications=dict(measured_hospital_calibration=False, OEM_qualified=False, clinical_qualified=False,
            room_temperature_coupling_qualified=False, room_temperature_coupling_disabled=thermal_disabled,
            dynamic_chilled_water_interface_qualified=False, cold_source_explicitly_unqualified=cold_false,
            research_stress_proxy_disabled=stress_disabled,
            limitation='Energy-service ENS and physical source/steam/cooling coupling remain modeled; disabled room-temperature or hydraulic interfaces cannot cancel ENS or establish clinical safety.'),
        sealed_support=dict(days=sealed, original_final12_opened_by_this_audit=False,
            original_final12_evaluated=False, metadata_only=True, current_release_authorized=False),
        annual_continuous=dict(ready_to_run_directly=False, year_minutes=8760. * 60.,
            station_qualification_minutes=float(station['control_autonomy_minutes']),
            daily_reset_is_valid_continuity=False, missing_materials=missing,
            conclusion='The current finite model is executable only within its explicitly registered bounded qualification. '
                       'Direct 8760-hour continuous GT/DG research is not presently qualified or authorized; '
                       'daily reset, enlarging the 1440-minute/two-start rights or opening sealed supports would change that contract. '
                       'Independent finite-window studies can continue under unchanged physics.'),
        bindings=bindings)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True, help='Fresh JSON evidence path on E:')
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.drive.upper() != 'E:':
        parser.error('New qualification evidence must be written to E:')
    result = build_qualification(args.root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf8') as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')
    passed = (result['mathematical_agreement']['all_passed']
              and result['finite_station']['actual_pure_component_probes_match']
              and result['finite_station']['registered_v30_v31_agree']
              and result['qualifications']['room_temperature_coupling_disabled']
              and result['qualifications']['research_stress_proxy_disabled']
              and result['qualifications']['cold_source_explicitly_unqualified'])
    print(json.dumps(dict(output=str(output), arithmetic_checks_passed=passed,
        numeric_checks=result['mathematical_agreement']['numeric_checks'],
        annual_continuous_ready=False, measured_hospital_calibration=False), ensure_ascii=False))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
