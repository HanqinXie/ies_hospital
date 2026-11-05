import importlib
import pytest


def api():
    spec=importlib.util.find_spec('tools.model_correctness_v23.thermal_model')
    assert spec is not None, 'V23 causal thermal state implementation is missing'
    return importlib.import_module('tools.model_correctness_v23.thermal_model')


def test_two_state_retains_heat_and_closes_energy_after_actual_delivery():
    m=api(); state=m.TwoState(3.,2.,.2,4.,2.,.1)
    result=m.commit(state,actual_extra_cooling_kW=1.,dt_h=.5,sensible_delta_kW=2.)
    assert abs(result.balance_residual_kWh)<1e-11
    assert result.state.mass_delta_C>0
    assert result.state.air_delta_C>0
    assert result.actual_extra_cooling_kWh==pytest.approx(.5)


def test_exact_two_state_is_invariant_to_substep_partition():
    m=api(); initial=m.TwoState(0.,0.,.2,4.,2.,.1)
    whole=m.commit(initial,0.,1.,sensible_delta_kW=3.).state
    divided=initial
    for _ in range(12):divided=m.commit(divided,0.,1/12,sensible_delta_kW=3.).state
    assert divided.air_delta_C==pytest.approx(whole.air_delta_C,abs=1e-11)
    assert divided.mass_delta_C==pytest.approx(whole.mass_delta_C,abs=1e-11)


def test_recovery_request_is_pure_and_only_actual_delivery_updates_state():
    m=api();state=m.TwoState(3.,2.,.2,4.,2.,.1)
    request=m.recovery_request_kW(state,max_power_kW=5.)
    assert 0<request<=5
    assert state.air_delta_C==3.
    actual=m.commit(state,request,.5).state
    denied=m.commit(state,0.,.5).state
    assert actual.air_delta_C<denied.air_delta_C


def test_absolute_system_boundary_rejects_second_ventilation_term():
    m=api();state=m.TwoState(0.,0.,.2,4.,2.,.1)
    with pytest.raises(ValueError,match='ventilation'):
        m.commit(state,0.,1.,sensible_delta_kW=2.,ventilation_delta_kW=1.)


def test_negative_thermal_energy_is_not_clipped_or_reset():
    m=api();state=m.TwoState(0.,0.,.2,4.,2.,.1)
    result=m.commit(state,5.,.5)
    assert result.state.air_delta_C<0
    assert result.state.mass_delta_C<0
    assert abs(result.balance_residual_kWh)<1e-11

def test_fit_never_reads_validation_arrays():
    m=api()
    cases=[dict(case_id='cal',split='calibration',forcing_kW=[1.,1.,1.],actual_delta_C=[.1,.2,.3],dt_h=1/6),dict(case_id='withheld',split='validation',forcing_kW=object(),actual_delta_C=object())]
    result=m.bounded_fit(cases,order=1,lower=[.1,.01],upper=[10.,1.],starts=[[1.,.1]])
    assert result['fit_case_ids']==['cal']
    assert result['validation_used'] is False


def test_unrecovered_truth_is_censored_instead_of_zero_recovery_error():
    import pandas as pd
    import numpy as np
    m=api();clock=pd.date_range('2006-07-20T12:10',periods=60,freq='10min')
    frame=pd.DataFrame(dict(baseline_temperature_C=np.ones(60)*22.,actual_temperature_C=np.ones(60)*23.,setpoint_C=np.ones(60)*22.),index=clock)
    result=m.paired_metrics(dict(event_start='2006-07-20T12:00',outage_hours=2),frame,np.zeros(60))
    assert result['observed_recovery_minutes'] is None
    assert result['modeled_recovery_minutes']==10.
    assert result['recovery_time_error_minutes'] is None
    assert result['recovery_status']=='one_or_both_right_censored'
    assert result['complete_input_interval_minutes']==10.
