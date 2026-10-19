import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from test_engineering_planner import problem,BASE
from test_model_closeout_contact_semantics import no_ready_source
from test_operating_readiness import current,dispatch
from test_execution_recovery_numerics import limits

def test_current_critical_reserve_uses_remaining_shaft_and_conversion_auxiliary():
    from tools.model_closeout.readiness import critical_reserve_rule
    r=critical_reserve_rule([[40.,20.,100.],[50.,30.,100.]],remaining_kWh=2400.,
        ec_kWe_per_kWc=.2,shaft_loss_bound_kW=10.,minimum_hours=1.)
    assert r['reference_critical_electric_kW']==130.
    assert r['reference_shaft_kW']==140.
    assert r['reserve_hours']==pytest.approx(2400./140.)
    assert r['actual_outage_endpoint_used'] is False

def test_reserve_boolean_is_registered_and_future_options_are_rejected():
    from tools.model_closeout.readiness import coordination_options
    assert coordination_options({'critical_reserve':True})['critical_reserve'] is True
    assert coordination_options({'critical_reserve':False})['critical_reserve'] is False
    with pytest.raises(ValueError):coordination_options({'critical_reserve':1})
    with pytest.raises(ValueError):coordination_options({'actual_outage_end':812.5})

def test_c19_reserve_does_not_undercut_useful_starting_critical_permission(problem):
    from tools.model_closeout.planner import solve_joint
    p=no_ready_source(problem)
    readiness=current([0.,0.,0.],observation_time_minute=45.,
        arrived_dispatch=dispatch([0.,0.,0.],diesel=500.),diesel_arrived_cap_kW=500.,
        observed_critical_unserved_kW=p['loads'][:2].tolist())
    r=solve_joint(**p,joint_options=BASE,execution_limits=limits(),readiness_context=readiness,
        coordination_options={'critical_reserve':True,'execution_recovery':True})
    assert r['action']['diesel_kW']>=90.-1e-5
    assert r['planned_current_action']['diesel_kW']==0.

def test_observed_grid_wait_prepares_a_conditional_gt_bridge_without_dg_credit(problem):
    from tools.model_closeout.planner import solve_joint
    p=no_ready_source(problem);p['loads'][:]=0.;p['loads'][:2,0]=100.
    p['availability']['GT_1']=1.;p['diesel_start_available']=True
    p['state'].diesel_status='CONNECTED'
    readiness=current([0.,0.,0.],observation_time_minute=75.,diesel_connected=True,
        actual_diesel_kW=200.,arrived_dispatch=dispatch([0.,0.,0.],diesel=500.),diesel_arrived_cap_kW=500.)
    r=solve_joint(**p,joint_options=BASE,execution_limits=limits(),readiness_context=readiness,
        coordination_options={'critical_reserve':False},grid_bridge_request=True)
    assert sum(r['action']['gt_electric_kW'])>=200.-1e-5
    diagnostic=r['joint_diagnostic']
    assert diagnostic['model_closeout']['grid_open_bridge_requested'] is True
