"""V26 temperature/report identity and field-boundary regressions."""
import importlib
import numpy as np
import pandas as pd
import pytest


def api():
    return importlib.import_module('tools.model_correction_v26.temperature_contract')


def path(times):
    t=np.asarray(times,float)
    return pd.DataFrame(dict(bucket_end=pd.Timestamp('2006-07-20 14:10'),dt_h=np.diff(np.r_[0,t]),elapsed_h=t))


def test_relative_report_requires_an_explicit_physical_background():
    with pytest.raises(ValueError,match='background'):
        api().relative_report(path([1.]),path([.5,1.]),[0.,0.])


def test_same_added_constant_cancels_under_different_report_clocks():
    n,a=path([.5,1.]),path([.25,.5,.75,1.])
    m=api().relative_report(n,a,2*a.elapsed_h,background_normal_C=n.elapsed_h,background_actual_C=a.elapsed_h,background_source='model_state',interval_minutes=60)
    shifted=api().relative_report(n,a,2*a.elapsed_h,background_normal_C=n.elapsed_h+20,background_actual_C=a.elapsed_h+20,background_source='model_state',interval_minutes=60)
    assert m.delta_C.iloc[0]==pytest.approx(1.125)
    assert shifted.delta_C.iloc[0]==pytest.approx(1.125)


def test_a_common_varying_background_does_not_automatically_cancel():
    n,a=path([.5,1.]),path([.25,.5,.75,1.])
    result=api().relative_report(n,a,np.zeros(4),background_normal_C=n.elapsed_h,background_actual_C=a.elapsed_h,background_source='model_state',interval_minutes=60)
    assert result.perturbation_report_C.iloc[0]==0
    assert result.common_background_report_difference_C.iloc[0]==pytest.approx(-.125)
    assert result.delta_C.iloc[0]==pytest.approx(-.125)


def test_offline_measured_background_cannot_be_marked_online_qualified():
    with pytest.raises(ValueError,match='online'):
        api().relative_report(path([1.]),path([1.]),[0.],background_normal_C=[20.],background_actual_C=[20.],background_source='offline_measured_reference',online=True,interval_minutes=60)


def test_online_reporting_refuses_future_samples():
    with pytest.raises(ValueError,match='completed'):
        api().relative_report(path([1.]),path([1.]),[0.],background_normal_C=[20.],background_actual_C=[20.],background_source='model_state',online=True,completed_h=.5,interval_minutes=60)


def test_system_group_multiplier_is_applied_once_and_only_to_its_field():
    f=pd.DataFrame(dict(system_air_kW=[-10.],heating_kW=[0.],cooling_kW=[2.],surface_convection_kW=[1.],internal_convective_kW=[.4],interzone_kW=[0.],outdoor_transfer_kW=[-.1],system_convective_kW=[0.]))
    r=api().room_sensible_ports(f,group_multiplier=5.,area_scale=2.)
    assert r.system_sensible_kW.iloc[0]==-1.
    assert r.delivered_h_minus_c_kW.iloc[0]==-1.
    assert r.surface_convection_kW.iloc[0]==.5
    assert r.sensible_into_air_kW.iloc[0]==pytest.approx(-.35)


def test_lifting_dynamic_states_with_fixed_ambient_is_a_different_experiment():
    # A passive scalar body held against a fixed ambient loses a raised initial
    # temperature; report-offset invariance must not be imposed on dynamics.
    initial_offset=20.;conductance=1.;capacity=2.;dt_h=1.
    final_offset=initial_offset*np.exp(-conductance*dt_h/capacity)
    assert final_offset!=initial_offset
