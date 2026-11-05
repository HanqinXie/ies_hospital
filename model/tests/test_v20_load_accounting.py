"""V20 field-specific grouping regressions on unchanged original hourly inputs."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from hospital_ies import resilience as api


@pytest.fixture(scope="module")
def annual():
    return (pd.read_csv(ROOT / "inputs/loads/hospital_ies_demand_8760.csv"),
            pd.read_csv(ROOT / "inputs/loads/critical_zones_8760.csv"))


@pytest.fixture
def first(annual):
    hourly, zones = annual
    return hourly.iloc[:1].copy(), zones.loc[zones.hour_of_year == 1].copy()


# Original engineering_audit_20261010/eplusout.sql, annual TimeIndex=1:
# Electric Equipment Electricity Rate / Lights Electricity Rate, W, object keys
# <zone>_MISCPLUG_EQUIP / <zone>_LIGHTS. These representative object variables
# differ from the already-multiplied Interior*:Electricity:Zone:<zone> meters.
@pytest.mark.parametrize("zone,mult,equipment_W,lights_W", [
    ("OR1_FLR_2", 1, 628.4544122950514, 67.79992323859203),
    ("ER_EXAM1_MULT4_FLR_1", 4, 179.93460000000002, 209.99979677375987),
    ("OR2_MULT5_FLR_2", 5, 628.4544122950514, 67.79992323859197),
    ("IC_PATROOM3_MULT6_FLR_2", 6, 269.90192, 140.6248116530401),
])
def test_actual_zone_meter_is_used_once(first, zone, mult, equipment_W, lights_W):
    hourly, zones = first
    selected = zones.loc[zones.zone_name == zone].iloc[0]
    assert selected.zone_multiplier == mult
    assert selected.interior_equipment_kW == pytest.approx(equipment_W * mult / 1000., abs=1e-12)
    assert selected.interior_lighting_kW == pytest.approx(lights_W * mult / 1000., abs=1e-12)
    # Isolate a real meter row without changing its data or the building total.
    zones.loc[zones.zone_name != zone, ["interior_equipment_kW", "interior_lighting_kW"]] = 0.
    grouped = api.build_zone_groups(zones, hourly).iloc[0]
    expected = selected.interior_equipment_kW + selected.interior_lighting_kW
    assert grouped[f"P{api.ZONE_PRIORITY[zone]}_e_kW"] == pytest.approx(expected, abs=1e-12)


def test_actual_h1_priority_electricity_equals_zone_meter_sums(first):
    hourly, zones = first
    grouped = api.build_zone_groups(zones, hourly).iloc[0]
    meter = zones.interior_equipment_kW + zones.interior_lighting_kW
    assert meter.sum() < hourly.iloc[0].P_e_nonplant_kW + hourly.iloc[0].P_e_unresolved_kW
    priorities = zones.zone_name.map(api.ZONE_PRIORITY)
    for priority in range(3):
        assert grouped[f"P{priority}_e_kW"] == pytest.approx(meter.loc[priorities == priority].sum(), abs=1e-12)
    total = hourly.iloc[0].P_e_nonplant_kW + hourly.iloc[0].P_e_unresolved_kW
    assert grouped.P3_e_kW == pytest.approx(total - meter.sum(), abs=1e-12)


def test_actual_h1_reheat_components_are_used_once_and_leave_other_coils_and_dhw(first):
    hourly, zones = first
    grouped = api.build_zone_groups(zones, hourly).iloc[0]
    coil = zones.associated_reheat_coil_heating_kW.clip(lower=0)
    assert coil.sum() < hourly.iloc[0].Q_h_heating_coils_kW < hourly.iloc[0].Q_h_demand_kW
    for priority in range(3):
        expected = coil.loc[zones.zone_name.map(api.ZONE_PRIORITY) == priority].sum()
        assert grouped[f"P{priority}_h_kW"] == pytest.approx(expected, abs=1e-12)
    assert grouped.P3_h_kW == pytest.approx(hourly.iloc[0].Q_h_demand_kW - coil.sum(), abs=1e-12)


def test_annual_priorities_are_nonnegative_conservative_and_leave_explicit_p3(annual):
    hourly, zones = annual
    grouped = api.build_zone_groups(zones, hourly)
    assert len(grouped) == 8760
    totals = dict(e=hourly.P_e_nonplant_kW + hourly.P_e_unresolved_kW,
                  h=hourly.Q_h_demand_kW, c=hourly.Q_c_demand_kW)
    for carrier, total in totals.items():
        values = grouped[[f"P{p}_{carrier}_kW" for p in range(4)]].to_numpy()
        assert np.isfinite(values).all() and np.min(values) >= 0.
        np.testing.assert_allclose(values.sum(axis=1), total, rtol=0., atol=1e-10)
        np.testing.assert_allclose(values[:, 3], np.maximum(0., total - values[:, :3].sum(axis=1)), rtol=0., atol=1e-10)


def test_grouping_diagnostics_exposes_uncompressed_fields_and_each_carrier(first):
    hourly, zones = first
    assert hasattr(api, "grouping_diagnostics"), "Per-hour pre-compression accounting is required"
    audit = api.grouping_diagnostics(zones, hourly)
    assert audit.attrs["load_accounting_contract_id"] == "hospital_ies.load_accounting.v20.v1"
    assert audit.hour_of_year.tolist() == [1]
    for carrier in api.CARRIERS:
        for suffix in ("proxy_sum_kW", "building_total_kW", "compression_scale", "proxy_excess_kW", "compression_applied", "P3_residual_kW"):
            assert f"{carrier}_{suffix}" in audit
        assert all(f"P{p}_{carrier}_raw_kW" in audit for p in range(3))
    assert audit.iloc[0].e_proxy_sum_kW == pytest.approx((zones.interior_equipment_kW + zones.interior_lighting_kW).sum(), abs=1e-12)
    assert audit.iloc[0].h_proxy_sum_kW == pytest.approx(zones.associated_reheat_coil_heating_kW.clip(lower=0).sum(), abs=1e-12)
    assert audit.iloc[0].c_proxy_sum_kW == pytest.approx((zones.zone_sensible_cooling_kW.clip(lower=0) * zones.zone_multiplier).sum(), abs=1e-12)


@pytest.mark.parametrize("carrier", ["e", "h", "c"])
def test_compression_retains_raw_excess_instead_of_hiding_it(first, carrier):
    hourly, zones = first
    assert hasattr(api, "grouping_diagnostics"), "Raw excess must be independently inspectable"
    raw = dict(e=(zones.interior_equipment_kW + zones.interior_lighting_kW).sum(),
        h=zones.associated_reheat_coil_heating_kW.clip(lower=0).sum(),
        c=(zones.zone_sensible_cooling_kW.clip(lower=0) * zones.zone_multiplier).sum())[carrier]
    if carrier == "e":
        hourly["P_e_nonplant_kW"] = raw / 2.; hourly["P_e_unresolved_kW"] = 0.
    elif carrier == "h":
        hourly["Q_h_demand_kW"] = raw / 2.
    else:
        hourly["Q_c_demand_kW"] = raw / 2.
    row = api.grouping_diagnostics(zones, hourly).iloc[0]
    assert row[f"{carrier}_proxy_sum_kW"] == pytest.approx(raw, abs=1e-12)
    assert row[f"{carrier}_compression_scale"] == pytest.approx(.5, abs=1e-12)
    assert row[f"{carrier}_proxy_excess_kW"] == pytest.approx(raw / 2., abs=1e-12)
    assert bool(row[f"{carrier}_compression_applied"])
    assert row[f"{carrier}_P3_residual_kW"] == pytest.approx(0., abs=1e-12)


def test_representative_zone_temperature_does_not_scale_with_repeated_zone_count(first):
    hourly, zones = first
    zone = "OR2_MULT5_FLR_2"
    hourly["Q_c_demand_kW"] = 10000.  # Unit-test envelope prevents proxy compression.
    zones.loc[zones.zone_name != zone, "zone_sensible_cooling_kW"] = 0.
    params, by_hour = api.thermal_data(ROOT, zones)
    temperatures, aggregate_requests = [], []
    representative = zones.loc[zones.zone_name == zone].iloc[0].zone_sensible_cooling_kW
    for count in (1., 4., 5., 6.):
        repeated = zones.copy(); repeated.loc[repeated.zone_name == zone, "zone_multiplier"] = count
        grouped = api.build_zone_groups(repeated, hourly).iloc[0]
        loads = np.array([[grouped[f"P{p}_{k}_kW"] for k in api.CARRIERS] for p in range(4)])
        loss = np.zeros((4, 3)); loss[0, 2] = .5 * loads[0, 2]
        state = api.RuntimeState(np.zeros(3), 0., set(), np.zeros(3), {})
        api.update_thermal(state, params, by_hour[1], loss, loads, .25, 2.)
        aggregate_requests.append(loads[0, 2]); temperatures.append(state.thermal_delta[zone])
    np.testing.assert_allclose(aggregate_requests, representative * np.array([1., 4., 5., 6.]), rtol=0., atol=1e-12)
    np.testing.assert_allclose(temperatures, [temperatures[0]] * 4, rtol=0., atol=1e-12)
    assert temperatures[0] > 0.


def test_default_selective_input_route_uses_the_same_corrected_meter_boundary():
    from tools.autonomous_dispatch.selective_data import selective_inputs
    authority = json.loads((ROOT / "outputs/v43_p2b_20261013_run01/experiment/global_manifest.json").read_text(encoding="utf-8"))
    sealed = sorted(int(r["time_block_id"].rsplit("_", 1)[1]) for r in authority["p2b_time_blocks"] if r["reserved_only"])
    _, zones, grouped, receipt = selective_inputs(ROOT, [1], sealed_days=sealed)
    meter = zones.interior_equipment_kW + zones.interior_lighting_kW
    for p in range(3):
        expected = meter.loc[zones.zone_name.map(api.ZONE_PRIORITY) == p].sum()
        assert grouped.loc[1, f"P{p}_e_kW"] == pytest.approx(expected, abs=1e-12)
    assert receipt["sealed_numeric_rows_parsed"] == 0
