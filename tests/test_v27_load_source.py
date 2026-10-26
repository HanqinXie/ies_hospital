"""Real source-clock, signed integration and conditional-allocation risks."""
import importlib
import sqlite3
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def api():
    return importlib.import_module('tools.model_review_v27.load_source')


def sql_fixture(path):
    c=sqlite3.connect(path)
    c.executescript('''CREATE TABLE EnvironmentPeriods(EnvironmentPeriodIndex INTEGER,EnvironmentType INTEGER);
    INSERT INTO EnvironmentPeriods VALUES(3,3);
    CREATE TABLE Time(TimeIndex INTEGER,Year INTEGER,Month INTEGER,Day INTEGER,Hour INTEGER,Minute INTEGER,Dst INTEGER,Interval INTEGER,IntervalType INTEGER,EnvironmentPeriodIndex INTEGER);
    INSERT INTO Time VALUES(1,2017,7,20,8,0,1,60,1,3);
    INSERT INTO Time VALUES(2,2017,7,20,9,0,1,60,1,3);
    CREATE TABLE ReportVariableDataDictionary(ReportVariableDataDictionaryIndex INTEGER,VariableName TEXT,KeyValue TEXT,VariableUnits TEXT,VariableType TEXT,TimestepType TEXT,ReportingFrequency TEXT);
    INSERT INTO ReportVariableDataDictionary VALUES(1,'Cooling Coil Total Cooling Rate','COIL','W','Avg','HVAC System','Hourly');
    CREATE TABLE ReportVariableData(ReportVariableDataDictionaryIndex INTEGER,TimeIndex INTEGER,VariableValue REAL);
    INSERT INTO ReportVariableData VALUES(1,1,1000);
    INSERT INTO ReportVariableData VALUES(1,2,2000);''')
    c.commit();c.close();return path


def test_sql_hour_ends_are_standard_time_and_do_not_add_dst(tmp_path):
    p=sql_fixture(tmp_path/'source.sql')
    result=api().read_hourly_rates(p,'Cooling Coil Total Cooling Rate',['COIL'])
    assert result.index.tolist()==[pd.Timestamp('2017-07-20 08:00'),pd.Timestamp('2017-07-20 09:00')]
    assert result.COIL.tolist()==[1.,2.]


def test_sql_unknown_key_or_nonfinite_data_is_not_filled_zero(tmp_path):
    p=sql_fixture(tmp_path/'source.sql')
    with pytest.raises(ValueError,match='Missing|missing'):
        api().read_hourly_rates(p,'Cooling Coil Total Cooling Rate',['COIL','ABSENT'])
    c=sqlite3.connect(p);c.execute('update ReportVariableData set VariableValue=NULL where TimeIndex=2');c.commit();c.close()
    with pytest.raises(ValueError,match='finite|complete|Unknown'):
        api().read_hourly_rates(p,'Cooling Coil Total Cooling Rate',['COIL'])


def test_actual_interval_integration_splits_hour_boundary_and_keeps_sign():
    result=api().integrate_intervals_hourly(['2017-07-20 09:10','2017-07-20 09:30'],[1/3,1/3],np.array([[100.,-50.]]))
    assert result.index.tolist()==[pd.Timestamp('2017-07-20 09:00'),pd.Timestamp('2017-07-20 10:00')]
    assert np.allclose(result.iloc[:,0],[100/6,0.],atol=1e-12)


def test_subsecond_clock_split_does_not_drop_nanosecond_duration():
    result=api().integrate_intervals_hourly(['2017-07-20 09:00:00.000000001'],[1/3600],np.array([[1.]]))
    assert abs(float(result.sum().iloc[0])-1/3600)<1e-15


def mapping_fixture():
    from hospital_ies.resilience import ZONE_PRIORITY
    hours=[4753,4754];times=pd.to_datetime(['2017-07-18 01:00','2017-07-18 02:00'])
    annual=pd.DataFrame(dict(hour_of_year=hours,timestamp=times,P_e_nonplant_kW=[10.,10.],P_e_unresolved_kW=[0.,0.],Q_h_demand_kW=[20.,20.],Q_c_demand_kW=[100.,100.],Q_c_terminal_coils_kW=[100.,100.]))
    zones=pd.DataFrame([dict(hour_of_year=h,zone_name=z,zone_multiplier=5. if z=='OR2_MULT5_FLR_2' else 1.,interior_equipment_kW=0.,interior_lighting_kW=0.,associated_reheat_coil_heating_kW=0.,zone_sensible_cooling_kW=1.) for h in hours for z in ZONE_PRIORITY])
    delta=pd.DataFrame(0.,index=times,columns=['total',*ZONE_PRIORITY]);delta['total']=[20.,-5.];delta['OR2_MULT5_FLR_2']=[1.,-.1]
    return annual,zones,delta


def test_conditional_mapping_uses_all_zone_deltas_and_multiplier_once():
    a,z,d=mapping_fixture();aa,zz,detail=api().apply_conditional_deltas(a,z,d)
    assert a.Q_c_demand_kW.tolist()==[100.,100.]
    assert aa.Q_c_demand_kW.tolist()==[120.,95.]
    assert zz[zz.zone_name.eq('OR2_MULT5_FLR_2')].zone_sensible_cooling_kW.tolist()==[2.,.9]
    assert np.allclose(detail.P0_delta_c_kW,[5.,-.5])
    assert np.allclose(detail.P3_delta_c_kW,[15.,-4.5])
    assert np.allclose(detail[[f'P{g}_new_c_kW' for g in range(4)]].sum(axis=1),aa.Q_c_demand_kW)


def test_conditional_mapping_rejects_negative_and_proxy_excess_without_clip():
    a,z,d=mapping_fixture();d.loc[d.index[0],'total']=-101.
    with pytest.raises(ValueError,match='negative|Negative'):
        api().apply_conditional_deltas(a,z,d)
    a,z,d=mapping_fixture();d.loc[d.index[0],'OR2_MULT5_FLR_2']=100.
    with pytest.raises(ValueError,match='proxy|Proxy'):
        api().apply_conditional_deltas(a,z,d)


def test_calendar_mapping_checks_weekday_and_never_uses_dst_shift():
    ref=pd.to_datetime(['2006-07-20 09:00']);annual=pd.to_datetime(['2017-07-20 09:00'])
    assert api().map_standard_hours(ref,annual).tolist()==[0]
    with pytest.raises(ValueError,match='weekday|calendar'):
        api().map_standard_hours(ref,pd.to_datetime(['2018-07-20 09:00']))
