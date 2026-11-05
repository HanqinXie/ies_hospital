"""v11 data authorization uses synthetic inputs; original final loads stay closed."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hospital_ies.pipeline import read_data
from hospital_ies.resilience import ZONE_PRIORITY, build_zone_groups


def api(module, name):
    try:
        loaded = importlib.import_module("tools.autonomous_dispatch." + module)
    except ModuleNotFoundError:
        pytest.fail("Missing v11 data authorization implementation: " + module)
    assert hasattr(loaded, name), "Missing v11 data API: " + name
    return getattr(loaded, name)


@pytest.fixture
def synthetic_root(tmp_path):
    load = tmp_path / "inputs/loads"
    load.mkdir(parents=True)
    hours = np.arange(1, 8761)
    ts = pd.date_range("2001-01-01", periods=8760, freq="h")
    primary = pd.DataFrame(dict(timestamp=ts, hour_of_year=hours,
        P_e_facility_kW=100., P_e_nonplant_kW=80., P_e_plant_kW=10.,
        P_e_unresolved_kW=10., Q_h_demand_kW=30., Q_c_demand_kW=40.,
        Q_h_heating_coils_kW=20., Q_h_DHW_water_use_kW=10., outdoor_temperature_C=20.))
    eb = primary[["timestamp", "hour_of_year", "P_e_facility_kW", "P_e_nonplant_kW",
                  "P_e_plant_kW", "P_e_unresolved_kW"]].copy()
    tb = primary[["timestamp", "hour_of_year", "Q_h_heating_coils_kW",
                  "Q_h_DHW_water_use_kW"]].copy()
    tb["Q_c_terminal_coils_kW"] = 40.
    zones = pd.DataFrame(dict(timestamp=np.repeat(ts.to_numpy(), 14),
        hour_of_year=np.repeat(hours, 14), zone_name=np.tile(sorted(ZONE_PRIORITY), 8760),
        zone_multiplier=1., interior_equipment_kW=3., interior_lighting_kW=1.,
        zone_mean_air_temperature_C=22., thermostat_heating_setpoint_C=20.,
        thermostat_cooling_setpoint_C=24., zone_sensible_heating_kW=0.,
        zone_sensible_cooling_kW=2., associated_reheat_coil_name="synthetic",
        associated_reheat_coil_heating_kW=1.))
    for name, table in [("hospital_ies_demand_8760.csv", primary),
                        ("electricity_boundary_8760.csv", eb),
                        ("thermal_boundary_8760.csv", tb),
                        ("critical_zones_8760.csv", zones)]:
        table.to_csv(load / name, index=False)
    return tmp_path


def test_selected_values_equal_original_physics_and_preserve_iloc(synthetic_root):
    original = read_data(synthetic_root)
    zones = pd.read_csv(synthetic_root / "inputs/loads/critical_zones_8760.csv")
    expected = build_zone_groups(zones, original).set_index("hour_of_year")
    allowed = [1, 2, 73, 74]
    hourly, selected_zones, groups, metadata = api("selective_data", "selective_inputs")(
        synthetic_root, allowed, sealed_days=[1])
    assert len(hourly) == 8760 and len(groups) == 8760
    pd.testing.assert_frame_equal(hourly.iloc[np.array(allowed)-1].reset_index(drop=True),
                                  original.iloc[np.array(allowed)-1].reset_index(drop=True))
    pd.testing.assert_frame_equal(groups.loc[allowed], expected.loc[allowed])
    assert len(selected_zones) == 14 * len(allowed)
    assert hourly.iloc[24]["P_e_nonplant_kW"] != hourly.iloc[24]["P_e_nonplant_kW"]
    assert groups.loc[25].isna().all()
    assert metadata["sealed_numeric_rows_parsed"] == 0
    assert all(value == allowed for value in metadata["numeric_parsed_hours_by_file"].values())


def test_unselected_physical_poison_is_never_parsed(synthetic_root):
    load = synthetic_root / "inputs/loads"
    for name in ("hospital_ies_demand_8760.csv", "electricity_boundary_8760.csv",
                 "thermal_boundary_8760.csv", "critical_zones_8760.csv"):
        path = load / name
        d = pd.read_csv(path)
        mask = d.hour_of_year.between(25, 48)
        numeric = next(k for k in d if k not in ("timestamp", "hour_of_year", "zone_name")
                       and pd.api.types.is_numeric_dtype(d[k]))
        d[numeric] = d[numeric].astype(object)
        d.loc[mask, numeric] = "SEALED_PHYSICAL_VALUE_MUST_NOT_PARSE"
        d.to_csv(path, index=False)
    hourly, zones, groups, metadata = api("selective_data", "selective_inputs")(
        synthetic_root, [1, 2, 73], sealed_days=[1])
    assert hourly.iloc[0].P_e_nonplant_kW == 80.
    assert set(zones.hour_of_year) == {1, 2, 73}
    assert np.isfinite(groups.loc[[1, 2, 73]].to_numpy()).all()
    assert metadata["sealed_numeric_rows_parsed"] == 0


def test_sealed_request_fails_before_any_csv_read(synthetic_root, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Authorization must be checked before CSV I/O")
    monkeypatch.setattr(pd, "read_csv", forbidden)
    with pytest.raises(ValueError, match="sealed"):
        api("selective_data", "selective_inputs")(synthetic_root, [25], sealed_days=[1])


@pytest.mark.parametrize("allowed", [[], [0], [8761], [True], [1.5], [1, 1]])
def test_invalid_authorized_hours_rejected(synthetic_root, allowed):
    with pytest.raises(ValueError, match="hour"):
        api("selective_data", "selective_inputs")(synthetic_root, allowed, sealed_days=[1])


def test_selected_boundary_conflict_is_not_hidden(synthetic_root):
    path = synthetic_root / "inputs/loads/electricity_boundary_8760.csv"
    d = pd.read_csv(path)
    d.loc[0, "P_e_nonplant_kW"] += 1.
    d.to_csv(path, index=False)
    with pytest.raises(ValueError, match="Conflicting inputs: nonplant"):
        api("selective_data", "selective_inputs")(synthetic_root, [1], sealed_days=[1])


def test_zone_identity_gap_rejected(synthetic_root):
    path = synthetic_root / "inputs/loads/critical_zones_8760.csv"
    d = pd.read_csv(path).iloc[1:]
    d.to_csv(path, index=False)
    with pytest.raises(ValueError, match="zone.*coverage|zone.*identity"):
        api("selective_data", "selective_inputs")(synthetic_root, [1], sealed_days=[1])


def test_release_receipt_cannot_open_final_data(synthetic_root):
    with pytest.raises(ValueError, match="release|final"):
        api("selective_data", "selective_inputs")(
            synthetic_root, [25], sealed_days=[1], release_receipt={"approved": True})


def test_selective_api_imports_from_workspace_root():
    completed = subprocess.run([sys.executable, "-c",
        "from tools.autonomous_dispatch.selective_data import selective_inputs"],
        cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_physical_csv_pass_requires_precomputed_row_grant(synthetic_root, monkeypatch):
    original = pd.read_csv
    physical_passes = []
    def checked(path, *args, **kwargs):
        if "usecols" in kwargs:
            assert set(kwargs["usecols"]) <= {"hour_of_year", "zone_name"}
        else:
            assert "skiprows" in kwargs, "Full annual physical parsing bypasses authorization"
            skipped = set(kwargs["skiprows"])
            assert (337 if Path(path).name == "critical_zones_8760.csv" else 25) in skipped
            physical_passes.append(Path(path).name)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(pd, "read_csv", checked)
    api("selective_data", "selective_inputs")(synthetic_root, [1, 2, 73], sealed_days=[1])
    assert len(physical_passes) == 4


def test_proposal_uses_54_seen_roots_without_reading_load_values(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Proposal must use only signed source metadata")
    monkeypatch.setattr(pd, "read_csv", forbidden)
    proposal = api("data_design", "build_source_proposal")(ROOT)
    assert len(proposal["roots"]) == len({r["source_day_index"] for r in proposal["roots"]}) == 54
    assert proposal["new_independent_background_count"] == 0
    assert proposal["final_evaluation_generated"] is False
    assert len(proposal["ids_by_cohort"]["development"]) == 540
    assert len(proposal["ids_by_cohort"]["normal"]) == 54
    assert len(proposal["ids_by_cohort"]["new_perturbation"]) == 162
    assert len(proposal["ids_by_cohort"]["development_regression"]) == 4
    sealed = set(proposal["sealed_final_days"])
    for scene in proposal["scenarios"]:
        assert not scene["is_final_blind"]
        a, b = [scene["full_source_support"][k] for k in ("raw_start_minute", "raw_end_minute")]
        assert not any(a < (day+1)*1440 and day*1440 < b for day in sealed)
        if scene["analysis_cohort"] == "new_perturbation":
            assert scene["requires_policy_freeze"] and not scene["selection_allowed"]


def test_regression_scenarios_keep_original_physical_contract():
    proposal = api("data_design", "build_source_proposal")(ROOT)
    regressions = [s for s in proposal["scenarios"] if s["analysis_cohort"] == "development_regression"]
    fields = api("data_design", "PHYSICAL_FIELDS")
    for scene in regressions:
        source = json.loads((ROOT / scene["source_metadata_path"]).read_text(encoding="utf-8"))
        old = next(s for s in source["scenarios"] if s["id"] == scene["source_scenario_id"])
        assert {k: scene.get(k) for k in fields} == {k: old.get(k) for k in fields}
        assert scene["physical_scenario_signature"] == old["physical_scenario_signature"]
    assert {s["regression_mechanism"] for s in regressions} == {
        "day187_compound", "day298_compound", "day051_fuel_recurrence", "day051_demand_drop_overproduction"}


def test_seal_audit_does_not_certify_historical_content_unread():
    audit = api("data_design", "build_final_seal_audit")(ROOT)
    assert audit["registered_support_isolation_verified"]
    assert not audit["historical_sealed_contents_never_read"]
    assert not audit["strict_final_release_authorized"]
    assert audit["final_evaluation_generated"] is False
    assert audit["historical_numeric_input_validation_used_sealed_rows"]
