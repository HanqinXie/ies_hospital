"""Hourly NMCSD-inspired CHP/HRSG/boiler/absorption/electric-chiller MILP.

The model is a steady-state hourly dispatch, not a dynamic plant digital twin.
Gas turbines are aggregated into integer counts at four discrete load levels.
Thus unit minimum load and an explicit discrete part-load heat rate are represented,
but ramp/startup, minimum up/down, maintenance, grid faults, and CHP compliance are not.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

RT_TO_KW = 3.51685284
KWH_PER_MMBTU = 293.0710702


@dataclass(frozen=True)
class Plant:
    case: str
    gt_count: int
    gt_rated_kW: float
    hrsg_rated_lbph: float
    absorption_rt: float
    electric_chillers_rt: tuple[float, ...]
    boiler_steam_lbph: float
    cooling_tower_rejection_kW: float
    emergency_diesel_kW: float
    steam_kWh_per_lb: float
    exchanger_eff: float
    abs_steam_lb_per_rt: float
    abs_electric_kW_per_rt: float
    ec_electric_kW_per_rt: float
    boiler_eff: float
    nominal_gt_capacity_used_for_heat_rate_kW: float
    tariff: dict
    gas_price_usd_per_mmbtu: float
    levels: tuple[float, ...]
    inclusion_unresolved: str

    def as_dict(self) -> dict:
        return vars(self)


def load_plant(config_path: Path, case: str) -> Plant:
    cfg = json.loads(Path(config_path).read_text(encoding='utf-8'))
    source, performance, assumptions = (cfg[k] for k in (
        'source_equipment', 'historical_pnnl_performance',
        'engineering_assumptions_not_from_source'))
    scales = cfg['study_cases'][case]
    gt_original = source['gas_turbines']['rated_electric_kW_each']
    # Prospective HRSG fit from PNNL appendix, used as engineering proxy for old units.
    hrsg_nominal_original = (performance['hrsg_steam_exponential_constant_lb_per_hr'] *
                             math.exp(performance['hrsg_steam_exponential_exponent_per_kW'] * gt_original))
    return Plant(
        case=case,
        gt_count=source['gas_turbines']['count'],
        gt_rated_kW=gt_original * scales['gas_turbine_scale'],
        hrsg_rated_lbph=hrsg_nominal_original * scales['hrsg_scale'],
        absorption_rt=source['absorption_single_effect']['rated_cooling_RT_each'] * scales['absorption_scale'],
        electric_chillers_rt=tuple(n * scales['electric_chiller_scale'] for n in
                                   source['centrifugal_electric_chillers']['rated_cooling_RT_each']),
        boiler_steam_lbph=source['boilers']['count'] * source['boilers']['rated_steam_lb_per_hr_each'] * scales['boiler_scale'],
        cooling_tower_rejection_kW=(source['cooling_towers']['count'] *
                                     source['cooling_towers']['rated_cooling_RT_each'] *
                                     scales['electric_chiller_scale'] * RT_TO_KW),
        emergency_diesel_kW=(source['emergency_diesel_generators']['count'] *
                             source['emergency_diesel_generators']['rated_electric_kW_each'] *
                             scales['diesel_scale']),
        steam_kWh_per_lb=assumptions['kWh_thermal_per_lb_steam'],
        exchanger_eff=assumptions['steam_to_building_heat_exchanger_efficiency'],
        abs_steam_lb_per_rt=performance['existing_absorption_steam_lb_per_RT_hr'],
        abs_electric_kW_per_rt=performance['existing_absorption_aux_kW_per_RT'],
        ec_electric_kW_per_rt=performance['existing_electric_chiller_all_in_kW_per_RT'],
        boiler_eff=performance['boiler_thermal_efficiency'],
        nominal_gt_capacity_used_for_heat_rate_kW=gt_original,
        tariff=cfg['historical_2001_prices_USD'],
        gas_price_usd_per_mmbtu=cfg['historical_2001_prices_USD']['gas_per_MMBtu'],
        levels=tuple(assumptions['gas_turbine_operating_load_fractions']),
        inclusion_unresolved=assumptions['default_unresolved_electricity_treatment'],
    )


def ambient_gt_power_factor(temp_C: float) -> float:
    temp_F = temp_C * 1.8 + 32.0
    return max(0.0, 1.23836 - 0.00404 * temp_F)


def ambient_gt_heat_rate_factor(temp_C: float) -> float:
    temp_F = temp_C * 1.8 + 32.0
    if temp_F <= 59:
        return 0.965544 + 0.000584 * temp_F
    return 0.910320 + 0.00152 * temp_F


def gt_level_properties(plant: Plant, temp_C: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return power kW, steam lb/h, fuel MMBtu/h for ONE GT at each discrete level.

    The first two formulas originate from PNNL Appendix, except HRSG steam part-load
    scaling and module capacity scaling, which are stated engineering assumptions.
    """
    heat_rate = 29477 - 1954.9 * math.log(plant.nominal_gt_capacity_used_for_heat_rate_kW)
    factor_p = ambient_gt_power_factor(temp_C)
    factor_f = ambient_gt_heat_rate_factor(temp_C)
    fractions = np.array(plant.levels, dtype=float)
    kw = plant.gt_rated_kW * factor_p * fractions
    steam = plant.hrsg_rated_lbph * fractions
    correction = 0.9851 * fractions ** (-0.327)
    fuel_mmbtu_per_hour = kw * heat_rate * correction * factor_f / 1e6
    return kw, steam, fuel_mmbtu_per_hour


def illustrative_tariff(timestamp, plant: Plant) -> float:
    """Simple synthetic TOU mapping; PNNL provides prices but not AL-TOU time mapping."""
    if timestamp.weekday() >= 5:
        return plant.tariff['electricity_offpeak_per_kWh']
    h = timestamp.hour
    if 12 <= h < 18:
        return plant.tariff['electricity_onpeak_per_kWh']
    if 8 <= h < 12 or 18 <= h < 22:
        return plant.tariff['electricity_midpeak_per_kWh']
    return plant.tariff['electricity_offpeak_per_kWh']


def solve_hour(row, plant: Plant, solver_time_limit_sec: float = 8.0) -> dict:
    """Mixed-integer economic dispatch with exact balance equalities.

    Objective is an hourly fuel + grid variable-cost surrogate at original 2001 prices.
    For original (single-effect) absorption and existing electric chillers use the
    historical assumptions on PDF pp.3 and 8. No onsite diesel dispatch in grid mode.
    """
    dt = row.timestamp
    p_bldg = float(row.P_e_nonplant_kW)
    p_unresolved = float(row.P_e_unresolved_kW)
    extra = float(getattr(row,'electric_extra_demand_kW',0.0))
    p_required = p_bldg + (p_unresolved if plant.inclusion_unresolved == 'include_all' else 0.0) + extra
    q_heat = float(row.Q_h_demand_kW)
    q_cool = float(row.Q_c_demand_kW)
    temp_C = float(row.outdoor_temperature_C)
    if not all(np.isfinite([p_required, q_heat, q_cool, temp_C])) or min(p_required, q_heat, q_cool) < -1e-7:
        raise ValueError(f'Invalid load/temperature at {dt}')

    # Variable layout: integer counts GT at each permitted level (0.4,0.6,0.8,1.0),
    # single absorption RT, three electric chiller RT, boiler steam lb/h, grid kW,
    # HRSG unused steam-equivalent lb/h. GT choice is a per-hour operating-point approximation.
    nl = len(plant.levels)
    ne = len(plant.electric_chillers_rt)
    ia = nl
    ie = np.arange(ia + 1, ia + 1 + ne)
    ib = int(ie[-1] + 1)
    ig = ib + 1
    iv = ig + 1
    nvars = iv + 1
    p_gt, s_gt, f_gt = gt_level_properties(plant, temp_C)
    tariff = illustrative_tariff(dt, plant)

    c = np.zeros(nvars)
    c[:nl] = f_gt * plant.gas_price_usd_per_mmbtu
    c[ib] = plant.steam_kWh_per_lb / plant.boiler_eff / KWH_PER_MMBTU * plant.gas_price_usd_per_mmbtu
    c[ig] = tariff
    c[iv] = 1e-7   # gentle tie-breaker, negligible physical cost

    # Three balances: electrical supply, cooling, and steam mass/enthalpy proxy.
    A = np.zeros((4, nvars))
    b = np.zeros(4)
    A[0, :nl] = p_gt
    A[0, ia] = -plant.abs_electric_kW_per_rt
    A[0, ie] = -plant.ec_electric_kW_per_rt
    A[0, ig] = 1.0
    b[0] = p_required

    A[1, ia] = RT_TO_KW
    A[1, ie] = RT_TO_KW
    b[1] = q_cool

    A[2, :nl] = s_gt
    A[2, ia] = -plant.abs_steam_lb_per_rt
    A[2, ib] = 1.0
    A[2, iv] = -1.0
    b[2] = q_heat / (plant.steam_kWh_per_lb * plant.exchanger_eff)

    # Cooling-tower heat rejection constraint (engineering approximation).
    A[3, ia] = RT_TO_KW + plant.abs_steam_lb_per_rt * plant.steam_kWh_per_lb
    A[3, ie] = RT_TO_KW + plant.ec_electric_kW_per_rt
    b[3] = plant.cooling_tower_rejection_kW

    unit_count = np.zeros(nvars)
    unit_count[:nl] = 1.0
    count_constraint = LinearConstraint(unit_count, -np.inf, plant.gt_count)
    eq_constraint = LinearConstraint(A[:3], b[:3], b[:3])
    tower_constraint = LinearConstraint(A[3], -np.inf, b[3])
    low = np.zeros(nvars)
    high = np.array([float(plant.gt_count)] * nl + [plant.absorption_rt] +
                    list(plant.electric_chillers_rt) + [plant.boiler_steam_lbph, np.inf, np.inf])
    integral = np.zeros(nvars, dtype=int)
    integral[:nl] = 1
    result = milp(c=c, integrality=integral, bounds=Bounds(low, high),
                  constraints=[eq_constraint, tower_constraint, count_constraint],
                  options={'time_limit': solver_time_limit_sec, 'mip_rel_gap': 1e-8})
    if result.x is None or result.status != 0:
        raise RuntimeError(f'MILP failed at {dt}: status={result.status}, message={result.message}')

    x = result.x
    n_units = np.rint(x[:nl]).astype(int)
    absorption = float(x[ia])
    e_chillers = np.asarray(x[ie])
    boiler_steam = float(x[ib])
    grid = float(x[ig])
    unused_hrsg = float(x[iv])
    gt_el = float(n_units @ p_gt)
    gt_steam = float(n_units @ s_gt)
    gt_fuel = float(n_units @ f_gt)
    boiler_fuel = boiler_steam * plant.steam_kWh_per_lb / plant.boiler_eff / KWH_PER_MMBTU
    ec_el = float(e_chillers.sum()) * plant.ec_electric_kW_per_rt
    abs_el = absorption * plant.abs_electric_kW_per_rt
    steam_heat = q_heat / (plant.steam_kWh_per_lb * plant.exchanger_eff)
    cooling_from_abs = absorption * RT_TO_KW
    cooling_from_el = float(e_chillers.sum()) * RT_TO_KW
    heat_rejection = float(A[3] @ x)

    residual_e = grid + gt_el - (p_required + ec_el + abs_el)
    residual_s = gt_steam + boiler_steam - steam_heat - absorption * plant.abs_steam_lb_per_rt - unused_hrsg
    residual_c = cooling_from_abs + cooling_from_el - q_cool
    if max(abs(residual_e),abs(residual_s),abs(residual_c)) > 1e-4:
        raise RuntimeError(f'Balance mismatch at {dt}: {residual_e},{residual_s},{residual_c}')
    ret = {
        'timestamp':str(dt), 'hour_of_year':int(row.hour_of_year),
        'P_e_nonplant_kW':p_bldg, 'P_e_unresolved_kW':p_unresolved,
        'P_e_service_including_unresolved_kW':p_required,
        'DOE_fans_existing_kW':float(getattr(row,'DOE_fans_existing_kW',0.0)),
        'fan_incremental_electric_kW':float(getattr(row,'fan_incremental_electric_kW',0.0)),
        'oxygen_production_Nm3_h':float(getattr(row,'oxygen_production_Nm3_h',0.0)),
        'oxygen_incremental_electric_kW':float(getattr(row,'oxygen_incremental_electric_kW',0.0)),
        'electric_extra_demand_kW':extra,
        'Q_h_demand_kW':q_heat, 'Q_c_demand_kW':q_cool,
        'outdoor_temperature_C':temp_C,
        'GT_electric_kW':gt_el, 'GT_on_units':int(n_units.sum()),
        'HRSG_steam_lbph':gt_steam, 'GT_gas_MMBtu_h':gt_fuel,
        'boiler_steam_lbph':boiler_steam, 'boiler_gas_MMBtu_h':boiler_fuel,
        'steam_hospital_heat_lbph':steam_heat,
        'steam_absorption_lbph':absorption*plant.abs_steam_lb_per_rt,
        'HRSG_unused_steam_equivalent_lbph':unused_hrsg,
        'grid_import_kW':grid, 'grid_export_kW':0.0,
        'abs_cooling_RT':absorption, 'abs_cooling_kW':cooling_from_abs,
        'el_cooling_RT':float(e_chillers.sum()), 'el_cooling_kW':cooling_from_el,
        'el_chiller_electric_kW':ec_el, 'abs_aux_electric_kW':abs_el,
        'cooling_tower_rejection_kW_estimated':heat_rejection,
        'cooling_tower_rejection_limit_kW':plant.cooling_tower_rejection_kW,
        'emergency_diesel_dispatched_kW':0.0,
        'electricity_balance_error_kW':residual_e,
        'steam_balance_error_lbph':residual_s,
        'cooling_balance_error_kW':residual_c,
        'grid_tariff_USD_kWh':tariff,
        'operating_cost_USD_h':grid*tariff+(gt_fuel+boiler_fuel)*plant.gas_price_usd_per_mmbtu,
        'milp_objective_USD_h':float(result.fun)
    }
    for j, lev in enumerate(plant.levels):
        ret[f'GT_units_at_{int(lev*100)}pct'] = int(n_units[j])
    for j, val in enumerate(e_chillers, 1):
        ret[f'electric_chiller_{j}_RT'] = float(val)
    return ret
