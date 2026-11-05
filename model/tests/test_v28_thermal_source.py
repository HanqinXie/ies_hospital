"""Guard only the output-only source boundary and accepted-interval physics."""
import importlib

import numpy as np
import pandas as pd
import pytest


def api():
    return importlib.import_module('tools.model_review_v28.thermal_source')


def test_observation_does_not_change_physical_objects_or_existing_requests():
    m=api();source='Version,26.2;\nTimestep,6;\nWaterHeater:Mixed,Tank,1,Setpoint,2;\nOutput:Variable,*,Existing,Hourly;\n'
    observed=m.add_observation_outputs(source,[('Tank','Water Heater Heating Energy','Detailed')])
    assert observed.startswith(source)
    assert not m.physical_object_diff(source,observed)
    assert 'Output:Variable,Tank,Water Heater Heating Energy,Detailed;' in observed
    altered=observed.replace('Setpoint,2;','Setpoint,0.1;')
    assert m.physical_object_diff(source,altered)


def test_missing_or_duplicate_native_key_is_not_filled_with_zero():
    m=api()
    rows=[dict(ReportDataDictionaryIndex=1,Name='Water Use Equipment Heating Energy',KeyValue='A',Units='J',Type='Sum',ReportingFrequency='HVAC System Timestep')]
    with pytest.raises(ValueError,match='Missing|ambiguous'):
        m.require_variable_keys(rows,'Water Use Equipment Heating Energy',['A','B'],'HVAC System Timestep','J','Sum')
    with pytest.raises(ValueError,match='Missing|ambiguous'):
        m.require_variable_keys(rows+rows,'Water Use Equipment Heating Energy',['A'],'HVAC System Timestep','J','Sum')
    with pytest.raises(ValueError,match='unit|aggregation'):
        m.require_variable_keys(rows,'Water Use Equipment Heating Energy',['A'],'HVAC System Timestep','W','Avg')


def test_requested_fixture_flow_uses_peak_schedule_and_effective_multiplier_once():
    m=api();rho=999.898;cp=4180.
    flow,power=m.fixture_request(np.array([.001,.001]),np.array([1.,.5]),4.,np.array([60.,60.]),np.array([20.,20.]),rho,cp)
    np.testing.assert_allclose(flow,[3.999592,1.999796],rtol=0,atol=1e-12)
    np.testing.assert_allclose(power,flow*4180.*40.,rtol=0,atol=1e-9)
    # Supplied actual hot flow is deliberately absent: it cannot reduce request.
    with pytest.raises(ValueError,match='negative|finite'):
        m.fixture_request(np.array([.001]),np.array([-1.]),1.,np.array([60.]),np.array([20.]),rho,cp)


def test_product_is_formed_before_unequal_interval_and_cross_hour_integration():
    m=api()
    ends=['2017-07-20 01:05','2017-07-20 01:20']
    hours=np.array([10.,15.])/60.
    # 10kW over00:55–01:05, then -4kW over01:05–01:20.
    got=m.integrate_native_power(ends,hours,np.array([[10.,-4.]]))
    assert list(got.index)==list(pd.to_datetime(['2017-07-20 01:00','2017-07-20 02:00']))
    np.testing.assert_allclose(got[0],[10./12.,10./12.-1.],atol=1e-12,rtol=0)
    # Reversed/nonfinite durations must not be silently dropped.
    with pytest.raises(ValueError):m.integrate_native_power(ends,[0.,.25],[[10.,-4.]])


def test_signed_tank_balance_uses_effective_heating_once_and_keeps_storage():
    m=api()
    values=dict(heater_J=100.,use_J=-80.,source_J=0.,loss_J=-10.,off_parasitic_to_tank_J=0.,on_parasitic_to_tank_J=0.,vent_J=0.,net_J=10.)
    assert m.tank_energy_residual(**values)==0.
    values['net_J']=-5.
    assert m.tank_energy_residual(**values)==15.


def test_schedule_parser_rejects_unknown_daytype_instead_of_zero_request():
    m=api()
    fields=['Schedule:Compact','Flow','Fraction','Through:12/31','For:Weekdays','Until:08:00','0','Until:24:00','1','For:Weekends Holidays','Until:24:00','0.5']
    assert m.compact_schedule_value(fields,'Monday',8.)==0.
    assert m.compact_schedule_value(fields,'Sunday',9.)==.5
    with pytest.raises(ValueError,match='day|schedule'):
        m.compact_schedule_value(fields,'UnknownDay',9.)
