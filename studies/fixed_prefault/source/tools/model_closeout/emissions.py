"""Independent V19 post-run CO2 evaluation; never a dispatch objective.

Chemical diesel use is unknown. Conditional constant specific consumption
is applied to the explicit shaft-work debit, which already includes modeled
startup, declared loss and rotor charging. Source tables and scope limits
are registered in docs/v19/EMISSIONS_METHOD.md.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping


SHAFT_DEBIT = "DG_fuel_budget_debit_shaft_equivalent_kWh"
BOUNDARY = "operational_combustion_plus_location_based_electricity_CO2_only"


def _number(value, field, *, signed=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field}: boolean is not an energy or factor")
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{field}: not a numeric value") from exc
    if not math.isfinite(result) or (not signed and result < 0):
        raise ValueError(f"{field}: must be finite" + ("" if signed else " and nonnegative"))
    return result


def _same(a, b):
    # Accounting identity check, not a relaxation of any physical/service gate.
    return abs(a - b) <= max(1e-6, 1e-9 * max(abs(a), abs(b)))


def _flag(value, field):
    if value in (None, "", False, "false", "False", "0", 0):
        return False
    if value in (True, "true", "True", "1", 1):
        return True
    raise ValueError(f"{field}: explicit boolean required")


def evaluate_emissions(resource_rows: Iterable[Mapping], factor_case: Mapping) -> list[dict]:
    """Return copied resource rows with CO2 components and conditional DG totals.

    Required energy names are grid_purchase_kWh, GT_gas_MMBtu,
    boiler_gas_MMBtu (or a separately recorded natural_gas_total_MMBtu), and
    DG_fuel_budget_debit_shaft_equivalent_kWh. Missing numeric inputs yield
    None components; invalid numeric values or inconsistent energy identities
    raise ValueError. No raw files, solver, state transition or policy are read.
    Factor SHA binds canonical JSON including its source/version metadata.
    """
    if not factor_case.get("case_id"):
        raise ValueError("factor case_id is required")
    for fuel in ("natural_gas", "diesel"):
        if factor_case.get(fuel + "_heat_basis") != "HHV":
            raise ValueError(f"{fuel}: explicit HHV factor basis required")
    fields = (
        "grid_kgCO2_per_kWh", "natural_gas_kgCO2_per_MMBtu_HHV",
        "diesel_kgCO2_per_MMBtu_HHV", "diesel_MMBtu_HHV_per_US_gallon",
        "US_gallon_liters", "diesel_liters_per_shaft_kWh",
    )
    f = {}
    for field in fields:
        value = _number(factor_case.get(field), field)
        if value is None or value <= 0:
            raise ValueError(f"{field}: positive factor required")
        f[field] = value
    factor_sha = hashlib.sha256(json.dumps(dict(factor_case), sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()

    output = []
    for original in resource_rows:
        row = dict(original)
        unknown = []

        def observed(field, *, signed=False):
            value = _number(row.get(field), field, signed=signed)
            if value is None:
                unknown.append(field)
            return value

        grid = observed("grid_purchase_kWh")
        gt = observed("GT_gas_MMBtu")
        boiler = observed("boiler_gas_MMBtu")
        gas_total = _number(row.get("natural_gas_total_MMBtu"), "natural_gas_total_MMBtu")
        if gt is not None and boiler is not None:
            component_total = gt + boiler
            if gas_total is not None and not _same(component_total, gas_total):
                raise ValueError("natural gas total disagrees with GT plus boiler")
            gas_total = component_total
        elif gas_total is None:
            unknown.append("natural_gas_total_MMBtu")
        for fuel in ("natural_gas", "diesel"):
            if row.get(fuel + "_heat_basis", "HHV") != "HHV":
                raise ValueError(f"{fuel}: resource basis is not registered HHV")

        debit = observed(SHAFT_DEBIT)
        work = _number(row.get("DG_shaft_work_kWh"), "DG_shaft_work_kWh")
        if debit is not None and work is not None and not _same(debit, work):
            raise ValueError("DG shaft work disagrees with fuel-budget shaft debit")
        electric = _number(row.get("DG_electric_work_kWh"), "DG_electric_work_kWh")
        loss = _number(row.get("DG_loss_work_kWh"), "DG_loss_work_kWh")
        rotor = _number(row.get("DG_rotor_delta_kWh"), "DG_rotor_delta_kWh", signed=True)
        if all(v is not None for v in (work, electric, loss, rotor)):
            if not _same(work, electric + loss + rotor):
                raise ValueError("DG shaft/electric/loss/rotor energy balance disagrees")
        if _flag(row.get("partial_DG_receipt_not_interpolated"), "partial_DG_receipt_not_interpolated"):
            debit = None
            unknown.append("partial_DG_receipt_not_interpolated")

        gas_factor = f["natural_gas_kgCO2_per_MMBtu_HHV"]
        grid_co2 = None if grid is None else grid * f["grid_kgCO2_per_kWh"]
        gas_co2 = None if gas_total is None else gas_total * gas_factor
        subtotal = None if grid_co2 is None or gas_co2 is None else grid_co2 + gas_co2
        liters = None if debit is None else debit * f["diesel_liters_per_shaft_kWh"]
        chemical = None if liters is None else (
            liters / f["US_gallon_liters"] * f["diesel_MMBtu_HHV_per_US_gallon"])
        diesel_co2 = None if chemical is None else chemical * f["diesel_kgCO2_per_MMBtu_HHV"]
        conditional_total = None if subtotal is None or diesel_co2 is None else subtotal + diesel_co2
        row.update(
            emissions_factor_case_id=factor_case["case_id"], emissions_factor_sha256=factor_sha,
            CO2_boundary=BOUNDARY,
            grid_CO2_kg=grid_co2,
            GT_gas_CO2_kg=None if gt is None else gt * gas_factor,
            boiler_gas_CO2_kg=None if boiler is None else boiler * gas_factor,
            natural_gas_CO2_kg=gas_co2, grid_plus_gas_CO2_kg=subtotal,
            DG_fuel_liters_conditional=liters,
            DG_chemical_energy_MMBtu_HHV_conditional=chemical,
            DG_CO2_kg_conditional=diesel_co2, total_CO2_kg_conditional=conditional_total,
            DG_exact_CO2_unknown=True,
            DG_startup_idle_lowload_conversion="constant-s extrapolation; actual chemical fuel unknown",
            emissions_unknown_fields=";".join(dict.fromkeys(unknown)),
            emissions_status="conditional_estimate" if conditional_total is not None else "partial_inputs_unknown",
            emissions_is_dispatch_objective=False,
        )
        output.append(row)
    return output
