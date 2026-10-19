"""Read-only SQL/IDF/CSV evidence for the V20 thermal multiplier review.

Only the explicitly supplied new output directory is created. No project import,
EnergyPlus execution, source export, simulation, or original-file write occurs.
"""
from __future__ import annotations

import argparse
from array import array
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

HEAT = "Heating Coil Heating Rate"
COOL = "Zone Air System Sensible Cooling Rate"
ZONE_HEAT = "Zone Air System Sensible Heating Rate"
COIL_COOL = "Cooling Coil Total Cooling Rate"
DHW = "Water Use Equipment Heating Rate"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_idf(path):
    # Matches this source's simple, comment-delimited IDF grammar. No macros.
    clean = "\n".join(line.split("!", 1)[0] for line in path.read_text(encoding="utf-8-sig").splitlines())
    result = defaultdict(list)
    for block in clean.split(";"):
        f = [x.strip().upper() for x in block.split(",")]
        if len(f) > 1 and f[0]:
            result[f[0]].append(f[1:])
    return result


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def write_csv(path, rows):
    with path.open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--starter", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    project, starter, out = args.project.resolve(), args.starter.resolve(), args.out.resolve()
    assert out.drive.lower() in ("d:", "e:")
    assert not out.is_relative_to(project) and not out.is_relative_to(starter)
    out.mkdir(parents=True, exist_ok=False)
    paths = dict(
        sql=starter / "simulation/engineering_audit_20261010/eplusout.sql",
        audit_idf=starter / "modified_idf/Hospital_SanDiego_262_engineering_audit.idf",
        source_idf=starter / "Hospital_SanDiego.idf",
        project_idf=project / "inputs/energyplus/Hospital_SanDiego.idf",
        exporter=starter / "scripts/energyplus_boundary_export.py",
        zone_csv=project / "inputs/loads/critical_zones_8760.csv",
        building_csv=project / "inputs/loads/hospital_ies_demand_8760.csv",
        thermal_csv=project / "inputs/loads/thermal_boundary_8760.csv",
        source_zone_csv=starter / "outputs_corrected/critical_zones_8760.csv",
        source_building_csv=starter / "outputs_corrected/hospital_ies_demand_8760.csv",
        provenance=project / "inputs/reference/provenance.json",
        export_summary=project / "inputs/reference/export_summary.json",
        error_log=starter / "simulation/engineering_audit_20261010/eplusout.err",
        consumer=project / "src/hospital_ies/resilience.py",
        thermal_parameters=project / "src/hospital_ies/idf_parameters.py",
    )
    before = {key: dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path)) for key, path in paths.items()}
    provenance = json.loads(paths["provenance"].read_text(encoding="utf-8-sig"))
    assert before["sql"]["sha256"] == provenance["simulation_runs"][0]["sqlite_sha256"]
    assert before["audit_idf"]["sha256"] == provenance["derived_audit_idfs_sha256"]["modified_idf/Hospital_SanDiego_262_engineering_audit.idf"]
    assert before["zone_csv"]["sha256"] == before["source_zone_csv"]["sha256"]
    assert before["building_csv"]["sha256"] == before["source_building_csv"]["sha256"]
    idf, original = parse_idf(paths["audit_idf"]), parse_idf(paths["source_idf"])
    objects = {kind: {f[0]: f for f in rows} for kind, rows in idf.items()}
    assert not idf.get("ZONEGROUP") and not idf.get("ZONELIST")
    with paths["zone_csv"].open(encoding="utf-8-sig", newline="") as stream:
        first_rows = [r for r in csv.DictReader(stream) if int(r["hour_of_year"]) == 1]
    focus = sorted(r["zone_name"] for r in first_rows)
    assert len(focus) == 14 and len(set(focus)) == 14
    con = sqlite3.connect(paths["sql"].as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    envs = [r[0] for r in con.execute("SELECT EnvironmentPeriodIndex FROM EnvironmentPeriods WHERE EnvironmentType=3")]
    assert len(envs) == 1
    times = list(con.execute("SELECT * FROM Time WHERE EnvironmentPeriodIndex=? ORDER BY TimeIndex", envs))
    assert len(times) == 8760 and len({r["TimeIndex"] for r in times}) == 8760
    assert all(r["Interval"] == 60 and r["Minute"] == 0 for r in times)
    time_to_i = {r["TimeIndex"]: i for i, r in enumerate(times)}
    stamps = [(datetime(r["Year"], r["Month"], r["Day"]) + timedelta(hours=r["Hour"])).strftime("%Y-%m-%d %H:%M") for r in times]
    zones = {r["ZoneName"]: dict(r) for r in con.execute("SELECT * FROM Zones")}
    names = (HEAT, COOL, ZONE_HEAT, COIL_COOL, DHW)
    dictionary = [dict(r) for r in con.execute("SELECT * FROM ReportDataDictionary WHERE ReportingFrequency='Hourly' AND Name IN (?,?,?,?,?)", names)]
    selected = [r for r in dictionary if r["Name"] not in (COOL, ZONE_HEAT) or r["KeyValue"] in focus]
    assert all(r["Units"] == "W" and r["Type"] == "Avg" and r["IsMeter"] == 0 for r in selected)
    keys = {(r["Name"], r["KeyValue"]): r for r in selected}
    assert len(keys) == len(selected)
    data = {r["ReportDataDictionaryIndex"]: array("d", [math.nan]) * 8760 for r in selected}
    counts = Counter()
    marks = ",".join("?" for _ in data)
    for r in con.execute("SELECT TimeIndex,ReportDataDictionaryIndex,Value FROM ReportData WHERE ReportDataDictionaryIndex IN (" + marks + ")", list(data)):
        if r["TimeIndex"] not in time_to_i:
            continue
        index, i = r["ReportDataDictionaryIndex"], time_to_i[r["TimeIndex"]]
        assert math.isnan(data[index][i]), "Duplicate physical row"
        data[index][i] = r["Value"] / 1000.0
        counts[index] += 1
    assert all(counts[index] == 8760 and all(math.isfinite(x) for x in values) for index, values in data.items())

    def series(name, key):
        return data[keys[(name, key)]["ReportDataDictionaryIndex"]]

    coil_keys = [r["KeyValue"] for r in selected if r["Name"] == HEAT]
    assert len(coil_keys) == 62 and set(coil_keys) == set(objects["COIL:HEATING:WATER"])
    links = []
    for zone in focus:
        z = zones[zone]
        idf_zone = objects["ZONE"][zone]
        multiplier = z["Multiplier"] * z["ListMultiplier"]
        assert z["ListMultiplier"] == 1 and float(idf_zone[6]) == multiplier
        coil = zone + " VAV BOX REHEAT COIL"
        connection = objects["ZONEHVAC:EQUIPMENTCONNECTIONS"][zone]
        equipment = objects["ZONEHVAC:EQUIPMENTLIST"][connection[1]]
        assert equipment[2] == "ZONEHVAC:AIRDISTRIBUTIONUNIT"
        adu = objects["ZONEHVAC:AIRDISTRIBUTIONUNIT"][equipment[3]]
        assert adu[2] == "AIRTERMINAL:SINGLEDUCT:VAV:REHEAT"
        terminal = objects[adu[2]][adu[3]]
        assert terminal[9:11] == ["COIL:HEATING:WATER", coil]
        coil_object = objects["COIL:HEATING:WATER"][coil]
        assert terminal[13] == adu[1] == coil_object[7]
        assert adu[1] in objects["NODELIST"][connection[2]][1:]
        references = [f[0] for f in idf[adu[2]] if coil in f]
        assert len(references) == 1
        for kind, name in [("ZONE", zone), ("COIL:HEATING:WATER", coil)]:
            assert objects[kind][name] == {f[0]: f for f in original[kind]}[name]
        links.append(dict(zone_name=zone, zone_multiplier=multiplier,
            SQL_Zone_Multiplier=z["Multiplier"], SQL_ListMultiplier=z["ListMultiplier"],
            coil_key=coil, heating_dictionary_index=keys[(HEAT, coil)]["ReportDataDictionaryIndex"],
            cooling_dictionary_index=keys[(COOL, zone)]["ReportDataDictionaryIndex"],
            equipment_list=equipment[0], air_distribution_unit=adu[0], terminal=terminal[0],
            coil_reference_count=len(references), outlet_node=adu[1], IDF_connection_verified=True,
            reheat_kW_hour1=series(HEAT, coil)[0], cooling_single_zone_kW_hour1=series(COOL, zone)[0],
            cooling_replicated_kW_hour1=series(COOL, zone)[0] * multiplier))
    assert len({r["coil_key"] for r in links}) == 14
    max_errors = {field: 0.0 for field in ("associated_reheat_coil_heating_kW", "zone_sensible_cooling_kW", "zone_sensible_heating_kW")}
    row_counts = Counter()
    mappings = [("associated_reheat_coil_heating_kW", HEAT), ("zone_sensible_cooling_kW", COOL), ("zone_sensible_heating_kW", ZONE_HEAT)]
    with paths["zone_csv"].open(encoding="utf-8-sig", newline="") as stream:
        for r in csv.DictReader(stream):
            i, zone = int(r["hour_of_year"]) - 1, r["zone_name"]
            assert r["timestamp"] == stamps[i] and float(r["zone_multiplier"]) == zones[zone]["Multiplier"] * zones[zone]["ListMultiplier"]
            assert r["associated_reheat_coil_name"] == zone + " VAV BOX REHEAT COIL"
            row_counts[(i, zone)] += 1
            for field, name in mappings:
                key = r["associated_reheat_coil_name"] if name == HEAT else zone
                error = abs(float(r[field]) - series(name, key)[i])
                max_errors[field] = max(max_errors[field], error)
                assert error <= 1e-9, (field, i, zone, error)
    assert len(row_counts) == 14 * 8760 and set(row_counts.values()) == {1}
    sums = {name: array("d", [0.0]) * 8760 for name in (HEAT, COIL_COOL, DHW)}
    for r in selected:
        if r["Name"] in sums:
            for i, value in enumerate(data[r["ReportDataDictionaryIndex"]]):
                sums[r["Name"]][i] += value
    with paths["building_csv"].open(encoding="utf-8-sig", newline="") as stream:
        building = list(csv.DictReader(stream))
    assert len(building) == 8760
    hours = []
    building_errors = Counter()
    for i, r in enumerate(building):
        assert int(r["hour_of_year"]) == i + 1 and r["timestamp"] == stamps[i]
        for field, expected in (("Q_h_heating_coils_kW", sums[HEAT][i]), ("Q_h_DHW_water_use_kW", sums[DHW][i]), ("Q_h_demand_kW", sums[HEAT][i] + sums[DHW][i]), ("Q_c_demand_kW", sums[COIL_COOL][i])):
            error = abs(float(r[field]) - expected)
            building_errors[field] = max(building_errors[field], error)
            assert error < 1e-9, (field, i, error)
        selected_h = sum(series(HEAT, link["coil_key"])[i] for link in links)
        selected_h_mult = sum(series(HEAT, link["coil_key"])[i] * link["zone_multiplier"] for link in links)
        selected_c = sum(series(COOL, link["zone_name"])[i] for link in links)
        selected_c_mult = sum(series(COOL, link["zone_name"])[i] * link["zone_multiplier"] for link in links)
        assert sums[HEAT][i] - selected_h >= -1e-9
        hours.append(dict(hour_of_year=i + 1, timestamp=stamps[i],
            selected14_reheat_as_exported_kW=selected_h,
            selected14_reheat_times_multiplier_kW=selected_h_mult,
            all62_heating_coils_kW=sums[HEAT][i], other48_coils_kW=sums[HEAT][i] - selected_h,
            DHW_kW=sums[DHW][i], building_Q_h_demand_kW=sums[HEAT][i] + sums[DHW][i],
            selected14_cooling_single_zone_sum_kW=selected_c,
            selected14_cooling_times_multiplier_kW=selected_c_mult,
            building_terminal_total_cooling_kW=sums[COIL_COOL][i],
            old_heat_proxy_exceeds_coils=selected_h_mult > sums[HEAT][i] + 1e-9,
            old_heat_proxy_exceeds_total_heat=selected_h_mult > sums[HEAT][i] + sums[DHW][i] + 1e-9,
            cooling_sensible_proxy_exceeds_terminal_total=selected_c_mult > sums[COIL_COOL][i] + 1e-9))
    sim = [dict(r) for r in con.execute("SELECT * FROM Simulations")]
    con.close()
    unchanged = {key: sha(path) == before[key]["sha256"] for key, path in paths.items()}
    assert all(unchanged.values())
    annual = {key + "_kWh": sum(float(r[key]) for r in hours) for key in list(hours[0])[2:11]}
    totals = {key + "_hours": sum(bool(r[key]) for r in hours) for key in list(hours[0])[11:]}
    receipt = dict(schema="hospital_ies.v20.thermal_field_evidence.v1", created_utc=datetime.now(timezone.utc).isoformat(),
        status="all_requested_SQL_IDF_CSV_matches_passed", sql_open_mode="ro; PRAGMA query_only=ON",
        energyplus_not_run=True, original_files_not_written=True, all_sources_unchanged=unchanged,
        sources=before, simulation_table_as_recorded=sim, runperiod_index=envs[0],
        time_rows=8760, interval_minutes=60, exported_hour_timestamp="end_of_hour; Hour24 rolls to next date",
        dictionary_counts=dict(Counter(r["Name"] for r in dictionary)), dictionary_rows=selected,
        CSV_rows_compared=14 * 8760, numeric_values_compared=3 * 14 * 8760,
        zone_CSV_max_abs_error_kW=max_errors, building_CSV_max_abs_error_kW=dict(building_errors),
        IDF_ZoneList_objects=len(idf.get("ZONELIST", [])), IDF_ZoneGroup_objects=len(idf.get("ZONEGROUP", [])),
        focus_multipliers=sorted({r["zone_multiplier"] for r in links}),
        unique_selected_coils=14, building_coil_keys=62, unselected_coils=48,
        first_hour=hours[0], annual_integrals_at_1h=annual, exceedance_counts=totals,
        limits=["Coil-side heat and net zone sensible heat/cooling have different boundaries.",
            "No inference that 14-zone sensible cooling must equal whole-building total terminal cooling.",
            "Node flows and inlet/outlet temperatures were not requested in this SQL; no new direct per-hour coil mass/enthalpy reconstruction.",
            "SQL Simulations completion flags are preserved verbatim; inspect the original .err final success line separately.",
            "No new EnergyPlus simulation or counterfactual model was run."])
    write_csv(out / "ZONE_COIL_KEY_MAPPING.csv", links)
    write_csv(out / "HOURLY_BOUNDARY_MATCH.csv", hours)
    write_json(out / "THERMAL_EVIDENCE.json", receipt)
    write_json(out / "OUTPUT_SHA256.json", {p.name: dict(bytes=p.stat().st_size, sha256=sha(p)) for p in out.iterdir() if p.is_file()})
    print(json.dumps(dict(status=receipt["status"], out=str(out), first_hour=hours[0],
        max_zone_errors=max_errors, max_building_errors=dict(building_errors),
        annual=annual, exceedance_counts=totals), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
