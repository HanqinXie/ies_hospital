"""State endpoints, report outputs and causal own-clock advancement differ."""
import importlib
import importlib.util
import numpy as np
import pandas as pd
import pytest

P2 = [.2,5.,.5,.08]
P3 = [.08,1.5,5.,.7,.5,.08]


def module():
    name = 'tools.model_correction_v25.thermal_model'
    assert importlib.util.find_spec(name) is not None, 'V25 state/report split is missing'
    return importlib.import_module(name)


def path(q,durations):
    return pd.DataFrame(dict(bucket_end=pd.to_datetime(['2006-07-20T12:10:00']*len(q)),
                             dt_h=np.asarray(durations)/60,net_sensible_kW=q))


def test_one_substep_report_matches_state_end_not_continuous_analytic_mean():
    m = module()
    result = m.replay_path(path([2.],[10.]),P2)
    assert result.mean_C.iloc[0] == result.endpoint_C.iloc[0]
    assert result.continuous_mean_C.iloc[0] < result.mean_C.iloc[0]


def test_nonuniform_report_is_weighted_individual_advanced_endpoints():
    m = module()
    result = m.replay_path(path([3.,-2.],[2.,8.]),P2)
    _,_,phi1,gamma1,*_ = m.kernel(P2,2/60)
    first = gamma1*3.
    _,_,phi2,gamma2,*_ = m.kernel(P2,8/60)
    last = phi2@first-gamma2*2.
    expected = .2*first[0]+.8*last[0]
    assert result.mean_C.iloc[0] == pytest.approx(expected)


@pytest.mark.parametrize('parameters',[P2,P3])
def test_online_advances_its_own_completed_interval_and_conserves_energy(parameters):
    result = module().advance_completed(np.zeros(len(parameters)//2 if len(parameters)==4 else 3),parameters,
        net_sensible_kW=4.,dt_h=1/6,max_step_minutes=1.)
    assert result['substeps'] == 10
    assert abs(result['balance_residual_kWh']) < 1e-12
    assert result['reported_mean_C'] < result['endpoint_C']
    assert result['continuous_mean_C'] < result['reported_mean_C']


def test_different_actual_and_normal_substeps_are_replayed_separately():
    m = module()
    normal = path([0.,2.],[5.,5.])
    fault = path([0.,3.,-1.],[1.,2.,7.])
    result = m.replay_pair(normal,fault,P3)
    a,b = m.replay_path(normal,P3),m.replay_path(fault,P3)
    assert result.mean_C.iloc[0] == pytest.approx(b.mean_C.iloc[0]-a.mean_C.iloc[0])
    assert result.endpoint_C.iloc[0] == pytest.approx(b.endpoint_C.iloc[0]-a.endpoint_C.iloc[0])


@pytest.mark.parametrize('parameters',[[0.,1.,1.,1.],[1.,-1.,1.,1.],[1.,1.,1.,1.,1.1,1.]])
def test_nonpassive_parameters_rejected(parameters):
    with pytest.raises(ValueError):
        module().replay_path(path([1.],[10.]),parameters)


def test_online_interface_enforces_own_step_at_most_one_minute():
    with pytest.raises(ValueError):
        module().advance_completed(np.zeros(2),P2,net_sensible_kW=1.,dt_h=1/6,max_step_minutes=2.)
