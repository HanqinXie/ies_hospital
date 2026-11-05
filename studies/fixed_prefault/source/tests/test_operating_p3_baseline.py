"""Selective SQL baseline extraction; sealed poison never enters numeric parse."""
import csv
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.idf_parameters import zone_parameter_table
from hospital_ies.resilience import ZONE_PRIORITY


def api():
    try:return importlib.import_module('tools.operating_response.p3_baseline')
    except ModuleNotFoundError:pytest.fail('v15 P3 baseline extractor not implemented')


def source_db(path,*,conflicting=False):
    """Synthetic values for P3; real first two permitted CSV hours for alignment."""
    zones=zone_parameter_table(ROOT/'inputs/energyplus/Hospital_SanDiego.idf')
    with (ROOT/'inputs/loads/critical_zones_8760.csv').open(encoding='utf8') as stream:
        reader=csv.DictReader(stream);critical={}
        for row in reader:
            h=int(row['hour_of_year'])
            if h>2:break
            critical[(h,row['zone_name'])]=row
    c=sqlite3.connect(path)
    c.executescript('CREATE TABLE EnvironmentPeriods(EnvironmentPeriodIndex INTEGER,EnvironmentName TEXT,EnvironmentType INTEGER);'
        'CREATE TABLE Time(TimeIndex INTEGER,SimulationDays INTEGER,Hour INTEGER,EnvironmentPeriodIndex INTEGER);'
        'CREATE TABLE ReportDataDictionary(ReportDataDictionaryIndex INTEGER,KeyValue TEXT,Name TEXT,ReportingFrequency TEXT,Units TEXT);'
        'CREATE TABLE ReportData(ReportDataDictionaryIndex INTEGER,TimeIndex INTEGER,Value);')
    c.execute('INSERT INTO EnvironmentPeriods VALUES(3,"RUNPERIOD 1",3)')
    c.executemany('INSERT INTO Time VALUES(?,?,?,3)',[(10000+h,(h-1)//24+1,(h-1)%24+1) for h in range(1,8761)])
    names=(('Zone Mean Air Temperature','zone_mean_air_temperature_C','C'),
           ('Zone Thermostat Cooling Setpoint Temperature','thermostat_cooling_setpoint_C','C'),
           ('Zone Air System Sensible Cooling Rate','zone_sensible_cooling_kW','W'))
    dictionary=0
    for zone in zones:
        z=zone['zone_name']
        for name,column,unit in names:
            dictionary+=1;c.execute('INSERT INTO ReportDataDictionary VALUES(?,?,?,"Hourly",?)',(dictionary,z,name,unit))
            for h in (1,2):
                value=float(critical[(h,z)][column]) if z in ZONE_PRIORITY else {'C':23.,'W':1000.}[unit]
                if unit=='W' and z in ZONE_PRIORITY:value*=1000.
                if conflicting and z in ZONE_PRIORITY and column=='zone_mean_air_temperature_C':value+=1.
                c.execute('INSERT INTO ReportData VALUES(?,?,?)',(dictionary,10000+h,value))
            # Original sealed day21. A bulk ReportData parse would hit poison.
            c.execute('INSERT INTO ReportData VALUES(?,?,?)',(dictionary,10000+21*24+1,'SEALED_POISON_DO_NOT_PARSE'))
    c.commit();c.close()


def test_exports_41_actual_source_baselines_only_granted_hours_with_null_fault_temperature(tmp_path):
    a=api();db=tmp_path/'source.sql';source_db(db);out=tmp_path/'baseline'
    r=a.extract_baseline([1,2],out,root=ROOT,database=db)
    assert r['p3_zone_count']==41 and r['exported_row_count']==82
    assert r['allowed_hours']==[1,2] and r['sealed_numeric_values_read']==0
    assert r['critical_source_alignment']['verified'] and r['critical_source_alignment']['rows_compared']==28
    rows=list(csv.DictReader((out/'P3_BASELINE.csv').open(encoding='utf-8-sig')))
    assert {int(x['hour_of_year']) for x in rows}=={1,2}
    assert all(x['priority']=='P3' and x['temperature_evaluation_status']=='baseline_only' for x in rows)
    assert all(x['disturbed_temperature_C']=='' and x['temperature_violation']=='' for x in rows)
    assert not r['P3_disturbed_temperature_coverage'] and r['physical_episodes_started']==0
    assert len(json.loads((out/'P3_SOURCE_RECEIPT.json').read_text(encoding='utf8'))['source_sha256'])>=2
    with pytest.raises(FileExistsError):a.extract_baseline([1,2],out,root=ROOT,database=db)


def test_rejects_sealed_grant_before_opening_missing_database(tmp_path):
    a=api()
    with pytest.raises(ValueError,match='[Ss]ealed'):
        a.extract_baseline([21*24+1],tmp_path/'out',root=ROOT,database=tmp_path/'missing.sql')
    assert not (tmp_path/'out').exists()


def test_source_conflict_is_explicit_and_not_exported_as_matching_baseline(tmp_path):
    a=api();db=tmp_path/'source.sql';source_db(db,conflicting=True)
    with pytest.raises(ValueError,match='align'):
        a.extract_baseline([1,2],tmp_path/'out',root=ROOT,database=db)
    assert not (tmp_path/'out').exists()
