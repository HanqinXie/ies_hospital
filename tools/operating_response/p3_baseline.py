"""Allowed-hour P3 EnergyPlus baseline export, never disturbed temperatures.

The original 41 noncritical IDF zones remain one P3 service priority. Baseline
values are fetched only for explicit nonsealed source hours; all-year reads
are restricted to hour/zone identity, dictionary metadata and byte hashes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from numbers import Integral
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from hospital_ies.idf_parameters import zone_parameter_table
from hospital_ies.resilience import ZONE_PRIORITY
from tools.autonomous_dispatch.selective_data import _identity,_selected
from .data_design import SEALED_DAYS,allowed_hours

SCHEMA='hospital_ies.operating_P3_baseline.v15.v1'
VARIABLES={
    'Zone Mean Air Temperature':('baseline_zone_temperature_C','C'),
    'Zone Thermostat Cooling Setpoint Temperature':('baseline_cooling_setpoint_C','C'),
    'Zone Air System Sensible Cooling Rate':('baseline_sensible_cooling_kW','W')}


def _sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def extract_baseline(hours,out,*,root=ROOT,database=None):
    raw=list(hours)
    if (not raw or len(set(raw))!=len(raw) or any(isinstance(h,bool) or not isinstance(h,Integral)
            or not 1<=int(h)<=8760 for h in raw)):
        raise ValueError('Distinct integer allowed hours in 1..8760 required')
    grant=sorted(int(h) for h in raw)
    if any((h-1)//24 in SEALED_DAYS for h in grant):
        raise ValueError('Sealed final12 numeric source hours are not authorized')
    root=Path(root).resolve();out=Path(out).resolve()
    if out.drive.lower()=='c:':raise ValueError('Project data must use D/E, not C')
    names=('P3_BASELINE.csv','P3_ZONE_MAPPING.csv','P3_SOURCE_RECEIPT.json')
    if any((out/name).exists() for name in names):raise FileExistsError('Keep previous baseline export immutable')
    db=Path(database) if database is not None else root.parent/'Hospital_SanDiego_Starter/simulation/engineering_audit_20261010/eplusout.sql'
    db=db.resolve()
    if not db.is_file():raise FileNotFoundError(db)
    idf=root/'inputs/energyplus/Hospital_SanDiego.idf'
    params={p['zone_name']:p for p in zone_parameter_table(idf)}
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as connection:
        env=[int(r[0]) for r in connection.execute('SELECT EnvironmentPeriodIndex FROM EnvironmentPeriods WHERE EnvironmentType=3')]
        if len(env)!=1:raise ValueError('Exactly one annual runperiod source required')
        identities=list(connection.execute('SELECT TimeIndex,SimulationDays,Hour FROM Time WHERE EnvironmentPeriodIndex=? ORDER BY TimeIndex',(env[0],)))
        mapping={(int(day)-1)*24+int(hour):int(index) for index,day,hour in identities}
        if len(identities)!=8760 or set(mapping)!=set(range(1,8761)):
            raise ValueError('Annual SQL source hour identity is not complete 1..8760')
        dictionaries={}
        for name,(_,unit) in VARIABLES.items():
            records=list(connection.execute('SELECT ReportDataDictionaryIndex,KeyValue,Units FROM ReportDataDictionary WHERE Name=? AND ReportingFrequency="Hourly"',(name,)))
            zones={str(key).upper() for _,key,_ in records}
            if len(records)!=55 or len(zones)!=55 or zones!=set(params) or any(u!=unit for _,_,u in records):
                raise ValueError('SQL/IDF 55-zone mapping, required variable or units differ')
            dictionaries[name]=records
        zone_names=set(params)-set(ZONE_PRIORITY)
        if len(zone_names)!=41:raise ValueError('Expected original 41 P3 zones outside fixed14 critical mapping')
        indices=[mapping[h] for h in grant];reverse={mapping[h]:h for h in grant}
        placeholders=','.join('?' for _ in indices);variable_marks=','.join('?' for _ in VARIABLES)
        # The WHERE clause is the physical-value grant. No unselected Value
        # enters Python, including original sealed source hours.
        query=f'''SELECT r.TimeIndex,d.KeyValue,d.Name,r.Value
            FROM ReportData r JOIN ReportDataDictionary d
              ON r.ReportDataDictionaryIndex=d.ReportDataDictionaryIndex
            WHERE r.TimeIndex IN ({placeholders}) AND d.Name IN ({variable_marks})
              AND d.ReportingFrequency='Hourly' '''
        values={}
        for index,zone,name,value in connection.execute(query,(*indices,*VARIABLES)):
            h=reverse[int(index)];zone=str(zone).upper();key=(h,zone,name)
            if key in values:raise ValueError('Duplicate selected SQL zone value')
            value=float(value)
            if not math.isfinite(value):raise ValueError('Nonfinite selected SQL baseline value')
            values[key]=value/1000. if VARIABLES[name][1]=='W' else value
    expected=len(grant)*55*len(VARIABLES)
    if len(values)!=expected:raise ValueError('Incomplete allowed-hour SQL zone baseline coverage')
    source_csv=root/'inputs/loads/critical_zones_8760.csv'
    identity,_,_=_identity(source_csv,zone=True)
    critical=_selected(source_csv,identity,set(grant))
    columns={'Zone Mean Air Temperature':'zone_mean_air_temperature_C',
             'Zone Thermostat Cooling Setpoint Temperature':'thermostat_cooling_setpoint_C',
             'Zone Air System Sensible Cooling Rate':'zone_sensible_cooling_kW'}
    max_error={name:0. for name in columns}
    for row in critical.itertuples(index=False):
        for name,column in columns.items():
            left=values[(int(row.hour_of_year),str(row.zone_name).upper(),name)];right=float(getattr(row,column))
            max_error[name]=max(max_error[name],abs(left-right))
            if not math.isclose(left,right,rel_tol=1e-7,abs_tol=1e-5):
                raise ValueError('SQL baseline does not align with existing critical-zone source: '+name)
    rows=[];zone_map=[]
    for zone in sorted(zone_names):
        p=params[zone]
        zone_map.append(dict(zone_name=zone,priority='P3',zone_multiplier=p['zone_multiplier'],
            volume_m3=p['volume_m3'],floor_area_from_volume_m2=p['floor_area_from_volume_m2'],
            mapping='Original55 IDF zones minus unchanged14 ZONE_PRIORITY critical zones',
            temperature_evaluation_status='baseline_only',disturbed_thermal_model_added=False))
        for h in grant:
            row=dict(hour_of_year=h,source_time_index=mapping[h],zone_name=zone,priority='P3',
                zone_multiplier=p['zone_multiplier'],temperature_evaluation_status='baseline_only',
                disturbed_temperature_C=None,temperature_violation=None)
            row.update({field:values[(h,zone,name)] for name,(field,_) in VARIABLES.items()})
            rows.append(row)
    out.mkdir(parents=True,exist_ok=True)
    for name,table in (('P3_BASELINE.csv',rows),('P3_ZONE_MAPPING.csv',zone_map)):
        with (out/name).open('x',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(table[0]));writer.writeheader();writer.writerows(table)
    receipt=dict(schema=SCHEMA,allowed_hours=grant,sealed_final_days=list(SEALED_DAYS),
        p3_zone_count=41,exported_row_count=len(rows),sql_physical_values_read=len(values),
        critical_source_alignment=dict(verified=True,rows_compared=len(critical),
            max_absolute_error_by_variable=max_error,absolute_tolerance=1e-5,relative_tolerance=1e-7),
        source_sha256={str(db):_sha(db),'inputs/energyplus/Hospital_SanDiego.idf':_sha(idf),
            'inputs/loads/critical_zones_8760.csv':_sha(source_csv)},
        output_sha256={name:_sha(out/name) for name in names if name.endswith('.csv')},
        source_database=str(db),source_runperiod=env[0],source_annual_identity_rows=8760,
        sealed_numeric_values_read=0,unselected_numeric_values_read=0,
        P3_disturbed_temperature_coverage=False,temperature_status='baseline_only',
        actual_P3_cold_service_evaluated_in_trajectory_separately=True,
        clinical_temperature_guarantee=False,physical_episodes_started=0,
        receipt_is_not_disturbed_temperature_validation=True)
    with (out/'P3_SOURCE_RECEIPT.json').open('x',encoding='utf8') as stream:
        json.dump(receipt,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--database',type=Path)
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args(argv)
    proposal=json.loads(args.manifest.read_text(encoding='utf8'))
    receipt=extract_baseline(allowed_hours(proposal),args.output,root=args.root,database=args.database)
    print(json.dumps(dict(p3_zone_count=receipt['p3_zone_count'],exported_row_count=receipt['exported_row_count'],
        sealed_numeric_values_read=0,P3_disturbed_temperature_coverage=False),ensure_ascii=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
