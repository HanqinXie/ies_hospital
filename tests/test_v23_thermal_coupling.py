import importlib
import numpy as np
import pytest


def api():
    spec=importlib.util.find_spec('tools.model_correctness_v23.thermal_coupling')
    assert spec is not None, 'V23 actual-delivery coupling is missing'
    return importlib.import_module('tools.model_correctness_v23.thermal_coupling')


def coupling():
    m=api();reference={float(t):{'OR1':{'heating_kW':1.,'cooling_kW':10.},'OR2':{'heating_kW':1.,'cooling_kW':10.}} for t in [0,10,20]}
    parameters=dict(reference_parameters=[.2,4.,2.,.1],zone_scales={'OR1':1.,'OR2':1.},zone_multipliers={'OR1':1.,'OR2':5.})
    return m.ThermalCoupling(reference,parameters,max_recovery_kW=100.)


def loads():
    result=np.zeros((4,3));result[0]=[10.,10.,100.];return result


def test_probe_is_pure_and_whole_group_delivery_conserves_with_other_and_multiplier():
    c=coupling();prepared=c.prepare_current({'time_minute':0.},loads(),100.)
    assert prepared['recovery_cooling_request_kW']==0.
    assert c.observe()['revision']==0
    actual=c.commit_actual(prepared,1/6,60.,0.,actual_priority_heating_kW=10.)
    assert actual['zones']['OR1']['main_delivered_cooling_kW']==pytest.approx(6.)
    assert actual['zones']['OR2']['main_delivered_cooling_kW']==pytest.approx(6.)
    assert actual['other_main_delivered_cooling_kW']==pytest.approx(24.)
    assert actual['allocated_main_cooling_kW']==pytest.approx(60.)
    assert actual['zones']['OR1']['sensible_delta_kW']==pytest.approx(4.)
    assert abs(actual['thermal_balance_residual_kWh'])<1e-10
    assert c.observe()['revision']==1


def test_recovery_is_actual_only_and_not_counted_twice_in_sensible_forcing():
    c=coupling();p=c.prepare_current({'time_minute':0.},loads(),100.)
    c.commit_actual(p,1/6,60.,0.,actual_priority_heating_kW=10.)
    p=c.prepare_current({'time_minute':10.},loads(),100.)
    request=p['recovery_cooling_request_kW'];assert request>0
    result=c.commit_actual(p,1/6,100.,request/2,actual_priority_heating_kW=10.)
    assert result['actual_extra_cooling_kWh']==pytest.approx(request/12)
    assert result['allocated_extra_recovery_kW']==pytest.approx(request/2)
    assert result['zones']['OR1']['sensible_delta_kW']==0.
    assert result['zones']['OR2']['sensible_delta_kW']==0.


def test_duplicate_or_stale_actual_commit_is_rejected_without_mutation():
    c=coupling();p=c.prepare_current({'time_minute':0.},loads(),100.)
    c.commit_actual(p,1/6,60.,0.,actual_priority_heating_kW=10.)
    state=c.observe()
    with pytest.raises(ValueError,match='stale|duplicate'):
        c.commit_actual(p,1/6,60.,0.,actual_priority_heating_kW=10.)
    assert c.observe()==state


def test_reference_scope_mismatch_never_assigns_a_priority_total_to_one_room():
    c=coupling();small=loads();small[0,2]=20.
    with pytest.raises(ValueError,match='reference|scope'):
        c.prepare_current({'time_minute':0.},small,100.)
    assert c.observe()['revision']==0


def test_future_or_missing_reference_never_uses_unseen_values_or_temperatures():
    c=coupling()
    with pytest.raises(ValueError,match='reference'):
        c.prepare_current({'time_minute':31.},loads(),100.)
    with pytest.raises(ValueError,match='time_minute'):
        c.prepare_current({'wall_C':80.,'future_temperature_C':90.},loads(),100.)

def test_actual_segment_must_split_at_real_native_reference_boundary():
    c=coupling();p=c.prepare_current({'time_minute':0.},loads(),100.)
    assert p['next_reference_boundary_minute']==10.
    with pytest.raises(ValueError,match='boundary'):
        c.commit_actual(p,.25,60.,0.,actual_priority_heating_kW=10.)
    assert c.observe()['revision']==0

def test_real_interval_clock_never_replays_already_committed_time():
    c=coupling();p=c.prepare_current({'time_minute':0.},loads(),100.)
    c.commit_actual(p,1/6,60.,0.,actual_priority_heating_kW=10.)
    repeated=c.prepare_current({'time_minute':0.},loads(),100.)
    before=c.observe()
    with pytest.raises(ValueError,match='clock'):
        c.commit_actual(repeated,1/6,60.,0.,actual_priority_heating_kW=10.)
    assert c.observe()==before
