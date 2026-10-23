"""Parse authorized hourly values only; preserve the legacy 8760 iloc contract.

Identity-column reads and byte hashes may cover the original annual files.
Physical values outside the explicit hour grant never enter a DataFrame.  This
module keeps legacy calls sealed; M21 releases only once-bound final support.
"""
from __future__ import annotations

import hashlib
import json
from numbers import Integral
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from hospital_ies.pipeline import REQUIRED
from hospital_ies.resilience import ZONE_PRIORITY, build_zone_groups

VERSION = "autonomous_dispatch.v11.selective_inputs.v1"
FILES = ("hospital_ies_demand_8760.csv", "electricity_boundary_8760.csv",
         "thermal_boundary_8760.csv", "critical_zones_8760.csv")
SEALED_AUTHORITY = Path("outputs/v43_p2b_20261013_run01/experiment/global_manifest.json")


def _integers(values, low, high, noun):
    items = list(values)
    if (not items or any(isinstance(v, bool) or not isinstance(v, Integral)
                         or not low <= int(v) <= high for v in items)
            or len(items) != len(set(items))):
        raise ValueError(f"Authorized {noun} must be distinct integers in {low}..{high}")
    return sorted(int(v) for v in items)


def _byte_identity(path):
    sha = hashlib.sha256()
    newlines, last = 0, b""
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
            newlines += chunk.count(b"\n")
            last = chunk[-1:]
    return sha.hexdigest(), newlines + bool(last and last != b"\n")


def _identity(path, zone=False):
    columns = ["hour_of_year", "zone_name"] if zone else ["hour_of_year"]
    identity = pd.read_csv(path, usecols=columns)
    sha, physical_lines = _byte_identity(path)
    if physical_lines != len(identity) + 1:
        raise ValueError("CSV physical row structure unsupported for selective skiprows")
    hours = pd.to_numeric(identity.hour_of_year, errors="raise")
    if hours.isna().any() or (hours % 1 != 0).any():
        raise ValueError("Input hour identity must be integer")
    identity["hour_of_year"] = hours.astype(int)
    if zone:
        names = identity.zone_name.str.upper()
        if (len(identity) != 14 * 8760 or set(names) != set(ZONE_PRIORITY)
                or identity.assign(zone_name=names).duplicated(["hour_of_year", "zone_name"]).any()
                or not identity.groupby("hour_of_year").size().reindex(range(1, 8761), fill_value=0).eq(14).all()):
            raise ValueError("Critical zone identity coverage incomplete")
    elif identity.hour_of_year.tolist() != list(range(1, 8761)):
        raise ValueError("Input hour identity is not 1..8760")
    return identity, sha, columns


def _selected(path, identity, allowed):
    keep = identity.hour_of_year.isin(allowed).to_numpy()
    skip = (np.flatnonzero(~keep) + 1).tolist()  # Header is physical row zero.
    selected = pd.read_csv(path, skiprows=skip)
    if selected.hour_of_year.astype(int).tolist() != identity.loc[keep, "hour_of_year"].tolist():
        raise ValueError("Selected CSV hour identity differs from the pre-parse grant")
    return selected


def _validate_selected(primary, eb, tb, zones):
    used = set(REQUIRED) | {"P_e_facility_kW", "P_e_plant_kW",
                            "Q_h_heating_coils_kW", "Q_h_DHW_water_use_kW"}
    if used - set(primary):
        raise ValueError("Input missing " + ",".join(sorted(used - set(primary))))
    primary["timestamp"] = pd.to_datetime(primary.timestamp, errors="raise")
    if primary[list(used)].isna().any().any():
        raise ValueError("Selected input contains missing values")
    for name, table in (("Electricity", eb), ("Thermal", tb)):
        if (table.hour_of_year.astype(int).tolist() != primary.hour_of_year.astype(int).tolist()
                or not (primary.timestamp.to_numpy() == pd.to_datetime(table.timestamp).to_numpy()).all()):
            raise ValueError(name + " table not aligned with main demand")
    if (primary.timestamp.duplicated().any()
            or not (primary.timestamp.diff().iloc[1:].to_numpy() ==
                    pd.to_timedelta(primary.hour_of_year.diff().iloc[1:], unit="h").to_numpy()).all()):
        raise ValueError("Selected time index is not aligned with hourly identity")
    checks = {
        "electricity_facility": (primary.P_e_facility_kW, eb.P_e_facility_kW),
        "nonplant": (primary.P_e_nonplant_kW, eb.P_e_nonplant_kW),
        "plant": (primary.P_e_plant_kW, eb.P_e_plant_kW),
        "unresolved": (primary.P_e_unresolved_kW, eb.P_e_unresolved_kW),
        "cooling": (primary.Q_c_demand_kW, tb.Q_c_terminal_coils_kW),
        "space_heat": (primary.Q_h_heating_coils_kW, tb.Q_h_heating_coils_kW),
        "DHW": (primary.Q_h_DHW_water_use_kW, tb.Q_h_DHW_water_use_kW),
    }
    for name, (left, right) in checks.items():
        if not np.allclose(np.asarray(left, dtype=float), np.asarray(right, dtype=float), rtol=1e-7, atol=1e-5):
            raise ValueError("Conflicting inputs: " + name)
    if np.max(np.abs((primary.P_e_nonplant_kW + primary.P_e_plant_kW +
                      primary.P_e_unresolved_kW - primary.P_e_facility_kW).to_numpy(dtype=float))) > 1e-5:
        raise ValueError("EnergyPlus electric boundary does not balance")
    if np.max(np.abs((primary.Q_h_heating_coils_kW + primary.Q_h_DHW_water_use_kW -
                      primary.Q_h_demand_kW).to_numpy(dtype=float))) > 1e-5:
        raise ValueError("EnergyPlus heat decomposition does not balance")
    zone_columns = ("zone_multiplier", "interior_equipment_kW", "interior_lighting_kW",
                    "zone_mean_air_temperature_C", "thermostat_cooling_setpoint_C",
                    "zone_sensible_cooling_kW", "associated_reheat_coil_heating_kW")
    if set(zone_columns) - set(zones) or not np.isfinite(zones[list(zone_columns)].to_numpy(dtype=float)).all():
        raise ValueError("Selected critical zone physical fields incomplete")


def selective_inputs(root, allowed_hours, *, sealed_days, release_receipt=None):
    """Return (8760 hourly, selected zones, indexed groups, audited grant).

    Raw file hashing and hour/zone identities cover the original files; only
    explicitly granted physical rows are parsed. Legacy calls keep final days
    sealed; only the once-bound M21 final-blind receipt can release its exact
    registered support. Caller-supplied fake receipts remain rejected.
    """
    allowed = _integers(allowed_hours, 1, 8760, "hours")
    sealed = _integers(sealed_days, 0, 364, "sealed days")
    root = Path(root).resolve()
    final_release = None
    if release_receipt is not None:
        from tools.sci_engineering.release import validate_input_release
        final_release = validate_input_release(root, allowed, sealed, release_receipt)
    elif any((hour - 1) // 24 in sealed for hour in allowed):
        raise ValueError("Authorized hours intersect sealed final days")
    authority = root / SEALED_AUTHORITY
    if authority.exists():
        registered = json.loads(authority.read_text(encoding="utf-8"))
        expected = sorted(int(row["time_block_id"].rsplit("_", 1)[1])
                          for row in registered["p2b_time_blocks"] if row["reserved_only"])
        if sealed != expected or len(expected) != 12:
            raise ValueError("Supplied sealed days differ from original final authority")
    selected, file_meta = [], {}
    for name in FILES:
        path = root / "inputs/loads" / name
        identity, sha, columns = _identity(path, zone=name == FILES[-1])
        table = _selected(path, identity, allowed)
        selected.append(table)
        file_meta["inputs/loads/" + name] = dict(sha256=sha, identity_only_columns=columns,
            identity_rows=len(identity), numeric_rows=len(table), numeric_parsed_hours=allowed)
    primary, eb, tb, zones = selected
    _validate_selected(primary, eb, tb, zones)
    # Same physical decomposition, before NaN padding.  There is no new scaling.
    groups = build_zone_groups(zones, primary).set_index("hour_of_year")
    groups = groups.reindex(pd.Index(range(1, 8761), name="hour_of_year"))
    hourly = primary.set_index("hour_of_year", drop=False).reindex(range(1, 8761))
    hourly["hour_of_year"] = np.arange(1, 8761)
    hourly = hourly.reset_index(drop=True)
    metadata = dict(version=VERSION, allowed_hours=allowed, sealed_days=sealed,
        sealed_numeric_rows_parsed=(sum(int(table.hour_of_year.map(lambda h: (int(h)-1)//24 in sealed).sum()) for table in selected) if final_release else 0),
        final_release_implemented=final_release is not None,
        numeric_parsed_hours_by_file={name: item["numeric_parsed_hours"] for name, item in file_meta.items()},
        files=file_meta, unauthorized_physical_padding="NaN; identity hour retained",
        value_validation_scope="selected values only; original tolerances and build_zone_groups",
        identity_validation_scope="annual hour and zone identity only; original file bytes hashed")
    if final_release is not None:
        metadata["m21_final_release"] = final_release
        metadata["sealed_numeric_rows_parsed_unit"] = "physical rows across four selected input tables"
    return hourly, zones, groups, metadata
