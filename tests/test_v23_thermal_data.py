from datetime import datetime
import importlib
import sqlite3
import numpy as np
import pandas as pd
import pytest


def module():
    spec = importlib.util.find_spec('tools.model_correctness_v23.thermal_data')
    assert spec is not None, 'V23 native five-minute thermal-data implementation is missing'
    return importlib.import_module('tools.model_correctness_v23.thermal_data')


def test_idf_changes_only_timestep_period_target_availability_and_outputs():
    api = module()
    source = 'Version,26.2; Timestep,6; RunPeriod,A,1,1,,12,31,,Sunday,No,No,No,Yes,Yes; Coil:Cooling:Water,VAV_OR Cooling Coil,AlwaysOn,0.2; Coil:Cooling:Water,Other Coil,AlwaysOn,0.3;'
    result = api.derive_idf(source, event=datetime(2006,7,20,12), outage_hours=2)
    assert 'Timestep,\n  12;' in result
    assert 'Other Coil,AlwaysOn,0.3' in result
    assert 'V23_OR_COOLING_AVAILABILITY' in result
    assert 'Zone Air System Sensible Heating Rate,Timestep' in result
    assert 'Zone Air Heat Balance Air Energy Storage Rate,Timestep' in result


def test_five_minute_contract_rejects_hourly_without_interpolating():
    api = module()
    frame = pd.DataFrame({'temperature_C':[22.,23.,24.]}, index=pd.date_range('2006-07-20',periods=3,freq='1h'))
    with pytest.raises(ValueError, match='five-minute'):
        api.validate_native_frame(frame)


def test_sql_clock_is_standard_time_with_dst_as_explicit_schedule_offset():
    api = module()
    standard, schedule = api.sql_clocks(7,20,11,5,1)
    assert standard == datetime(2006,7,20,11,5)
    assert schedule == datetime(2006,7,20,12,5)


def test_representative_zone_delivery_never_receives_whole_priority_group():
    api = module()
    allocated=api.allocate_priority_delivery({'OR1':10.,'OR2':50.,'OTHER':40.}, {'OR1':1.,'OR2':5.,'OTHER':1.},60.)
    assert allocated['OR1'] == pytest.approx(6.)
    assert allocated['OR2'] == pytest.approx(6.)
    assert sum(allocated[z]*m for z,m in {'OR1':1.,'OR2':5.,'OTHER':1.}.items()) == pytest.approx(60.)


def test_protocol_rejects_overlapping_full_source_support():
    api=module()
    with pytest.raises(ValueError,match='overlap'):
        api.validate_protocol({'dates':[{'split':'calibration','month':1,'day':22},{'split':'validation','month':1,'day':23}], 'pre_days':2,'post_days':2})

def test_two_day_prerun_never_contains_the_event_outage():
    api=module()
    source='Timestep,6; RunPeriod,A,1,1,,12,31,,Sunday; Coil:Cooling:Water,VAV_OR Cooling Coil,AlwaysOn,0.2;'
    result=api.derive_idf(source,event=datetime(2006,7,20,12),outage_hours=2,pre_days=2)
    assert 'Through: 7/19,For: AllDays,Until: 24:00,1' in result

def test_five_minute_reporting_retains_original_one_hour_sizing_window():
    api=module()
    source='Timestep,6; Sizing:Parameters,1,1,6; RunPeriod,A,1,1,,12,31,,Sunday; Coil:Cooling:Water,VAV_OR Cooling Coil,AlwaysOn,0.2;'
    result=api.derive_idf(source,event=datetime(2006,7,20,12),outage_hours=2)
    assert 'Sizing:Parameters,\n  1,\n  1,\n  12;' in result

def test_native_ten_minute_option_preserves_sizing_and_capacity_inputs():
    api=module()
    source='Timestep,6; Sizing:Parameters,1,1,6; RunPeriod,A,1,1,,12,31,,Sunday; Coil:Cooling:Water,VAV_OR Cooling Coil,AlwaysOn,0.2;'
    result=api.derive_idf(source,event=datetime(2006,7,20,12),outage_hours=2,interval_minutes=10)
    assert 'Timestep,\n  6;' in result
    assert 'Sizing:Parameters,\n  1,\n  1,\n  6;' in result


def test_native_ten_minute_contract_accepts_only_real_ten_minute_points():
    api=module()
    frame=pd.DataFrame({'temperature_C':[22.,23.,24.]},index=pd.date_range('2006-07-20',periods=3,freq='10min'))
    api.validate_native_frame(frame,interval_minutes=10)
    with pytest.raises(ValueError,match='native'):
        api.validate_native_frame(frame.iloc[[0,2]],interval_minutes=10)
