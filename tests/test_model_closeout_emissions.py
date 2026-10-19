"""V19 post-run CO2 ledger: units, unknowns and shaft-work accounting."""
from __future__ import annotations

import copy
import importlib
from pathlib import Path

import pytest


def calculator():
    path = Path(__file__).resolve().parents[1] / "tools/model_closeout/emissions.py"
    assert path.is_file(), "V19 post-run emissions calculator is not implemented"
    return importlib.import_module("tools.model_closeout.emissions").evaluate_emissions


def factors():
    return {
        "case_id": "CAMX2023r2_DG75pct",
        "grid_kgCO2_per_kWh": 428.464 * 0.45359237 / 1000,
        "natural_gas_kgCO2_per_MMBtu_HHV": 53.06,
        "natural_gas_heat_basis": "HHV",
        "diesel_kgCO2_per_MMBtu_HHV": 73.96,
        "diesel_MMBtu_HHV_per_US_gallon": 0.138,
        "diesel_heat_basis": "HHV",
        "US_gallon_liters": 3.785411784,
        "diesel_liters_per_shaft_kWh": 118 / 456,
    }


def resources():
    # The 1 kWh start/rotor charge is already in mechanical/debit work.
    return {
        "experiment_id": "synthetic_units_only",
        "grid_purchase_kWh": 1000.0,
        "GT_gas_MMBtu": 2.0,
        "boiler_gas_MMBtu": 3.0,
        "natural_gas_total_MMBtu": 5.0,
        "DG_fuel_budget_debit_shaft_equivalent_kWh": 12.0,
        "DG_shaft_work_kWh": 12.0,
        "DG_electric_work_kWh": 9.0,
        "DG_loss_work_kWh": 2.0,
        "DG_rotor_delta_kWh": 1.0,
    }


def test_units_and_startup_loss_rotor_are_counted_once():
    row = resources()
    original = copy.deepcopy(row)
    f = factors()
    result = calculator()([row], f)[0]
    liters = 12 * (118 / 456)
    fuel_hhv = liters / 3.785411784 * 0.138
    assert result["grid_CO2_kg"] == pytest.approx(194.34800121968)
    assert result["natural_gas_CO2_kg"] == pytest.approx(265.3)
    assert result["grid_plus_gas_CO2_kg"] == pytest.approx(459.64800121968)
    assert result["DG_fuel_liters_conditional"] == pytest.approx(liters)
    assert result["DG_chemical_energy_MMBtu_HHV_conditional"] == pytest.approx(fuel_hhv)
    assert result["DG_CO2_kg_conditional"] == pytest.approx(fuel_hhv * 73.96)
    assert result["total_CO2_kg_conditional"] == pytest.approx(459.64800121968 + fuel_hhv * 73.96)
    assert result["DG_exact_CO2_unknown"] is True
    assert result["emissions_status"] == "conditional_estimate"
    assert row == original


def test_missing_explicit_shaft_debit_is_unknown_not_electric_fuel():
    row = resources()
    del row["DG_fuel_budget_debit_shaft_equivalent_kWh"]
    row["DG_fuel_budget_debit_kWh_equivalent"] = 12.0
    result = calculator()([row], factors())[0]
    assert result["grid_plus_gas_CO2_kg"] is not None
    assert result["DG_CO2_kg_conditional"] is None
    assert result["total_CO2_kg_conditional"] is None
    assert "DG_fuel_budget_debit_shaft_equivalent_kWh" in result["emissions_unknown_fields"]


def test_partial_dg_receipt_stays_unknown():
    row = resources()
    row["partial_DG_receipt_not_interpolated"] = True
    result = calculator()([row], factors())[0]
    assert result["DG_CO2_kg_conditional"] is None
    assert result["grid_plus_gas_CO2_kg"] is not None


def test_unknown_gas_does_not_discard_grid_or_conditional_diesel():
    row = resources()
    row["GT_gas_MMBtu"] = None
    row["natural_gas_total_MMBtu"] = None
    result = calculator()([row], factors())[0]
    assert result["grid_CO2_kg"] is not None
    assert result["boiler_gas_CO2_kg"] == pytest.approx(159.18)
    assert result["natural_gas_CO2_kg"] is None
    assert result["grid_plus_gas_CO2_kg"] is None
    assert result["DG_CO2_kg_conditional"] is not None
    assert result["total_CO2_kg_conditional"] is None


def test_signed_rotor_discharge_is_not_negative_fuel():
    row = resources()
    row.update(DG_fuel_budget_debit_shaft_equivalent_kWh=10.0,
               DG_shaft_work_kWh=10.0, DG_rotor_delta_kWh=-1.0)
    result = calculator()([row], factors())[0]
    assert result["DG_fuel_liters_conditional"] == pytest.approx(10 * 118 / 456)


@pytest.mark.parametrize("field,value", [
    ("grid_purchase_kWh", -1),
    ("GT_gas_MMBtu", float("nan")),
    ("DG_fuel_budget_debit_shaft_equivalent_kWh", float("inf")),
    ("boiler_gas_MMBtu", "not-an-energy"),
])
def test_invalid_observed_energy_is_rejected(field, value):
    row = resources()
    row[field] = value
    with pytest.raises(ValueError, match=field):
        calculator()([row], factors())


def test_debit_mechanical_disagreement_is_rejected():
    row = resources()
    row["DG_shaft_work_kWh"] = 13.0
    with pytest.raises(ValueError, match="shaft.*debit|debit.*shaft"):
        calculator()([row], factors())


def test_rotor_energy_identity_disagreement_is_rejected():
    row = resources()
    row["DG_loss_work_kWh"] = 0.0
    with pytest.raises(ValueError, match="rotor|balance"):
        calculator()([row], factors())


def test_gas_total_disagreement_is_rejected():
    row = resources()
    row["natural_gas_total_MMBtu"] = 4.0
    with pytest.raises(ValueError, match="gas.*total|total.*gas"):
        calculator()([row], factors())


def test_lhv_case_cannot_silently_use_hhv_factors():
    f = factors()
    f["natural_gas_heat_basis"] = "LHV"
    with pytest.raises(ValueError, match="HHV"):
        calculator()([resources()], f)


def test_factor_binding_changes_with_preregistered_sensitivity():
    f = factors()
    a = calculator()([resources()], f)[0]
    f["case_id"] = "CAMX2023r2_DG25pct"
    f["diesel_liters_per_shaft_kWh"] = 50 / 152
    b = calculator()([resources()], f)[0]
    assert len(a["emissions_factor_sha256"]) == 64
    assert a["emissions_factor_sha256"] != b["emissions_factor_sha256"]
    assert b["DG_CO2_kg_conditional"] > a["DG_CO2_kg_conditional"]
    assert a["grid_plus_gas_CO2_kg"] == b["grid_plus_gas_CO2_kg"]


def test_no_diesel_work_is_not_called_a_measured_diesel_inventory():
    row = resources()
    for field in ("DG_fuel_budget_debit_shaft_equivalent_kWh", "DG_shaft_work_kWh",
                  "DG_electric_work_kWh", "DG_loss_work_kWh", "DG_rotor_delta_kWh"):
        row[field] = 0.0
    result = calculator()([row], factors())[0]
    assert result["DG_CO2_kg_conditional"] == 0.0
    assert result["DG_exact_CO2_unknown"] is True


def test_empty_input_stays_empty():
    assert calculator()([], factors()) == []
