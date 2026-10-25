"""M23 bounded off/run search preserves real steam and electric sinks."""
import copy
import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_engineering_planner import problem
from tools.operating_response.physical_balance import solve_physical
from hospital_ies.execution import ModelConstraintInfeasible

def ports(problem):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][0]=[127.,100.,0.]
    p['availability'].update(GRID=0.,DIESEL=0.,GT_2=0.,GT_3=0.,HRSG_1=0.,ABS=0.,EC_1=0.,EC_2=0.,EC_3=0.)
    p['config']['_model_closure_v22']={};p['config']['_model_correctness_v23']={}
    a=dict(gt_electric_kW=[128.,0.,0.],grid_import_kW=0.,diesel_kW=0.,boiler_steam_lbph=1000.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.])
    return p,a

def test_off_infeasible_real_positive_domain_executes_from_arrived_permission(problem):
    p,a=ports(problem);r=solve_physical(**p,execution_action=a)
    assert r['action']['boiler_steam_lbph']>0.
    # P0 service remains the original joint E/H lex objective; the run may
    # trade a small E shortfall for real H delivery. Aux is paid, not fixed
    # to the initially available 1 kW electric surplus.
    assert r['action']['boiler_auxiliary_kW']>.5
    assert r['action']['boiler_auxiliary_kW']==pytest.approx(128.-r['served'][:,0].sum(),abs=1e-5)
    assert r['served'][0,1]>0.
    assert r['action']['boiler_steam_lbph']*p['plant'].steam_kWh_per_lb*p['plant'].exchanger_eff==pytest.approx(r['served'][:,1].sum(),abs=1e-5)
    assert r['m23_boiler_domains']['status']=='legal_domain_selected'
    assert r['m23_boiler_domains']['inventory_committed'] is False
    assert max(map(abs,r['energy_balance_residuals']))<1e-4

def test_pending_permission_cannot_be_used_and_no_sink_remains_rejected(problem):
    p,a=ports(problem);a['boiler_steam_lbph']=0.
    with pytest.raises(ModelConstraintInfeasible):solve_physical(**p,execution_action=a)

def test_joint_compiler_searches_real_positive_domain_without_reusing_zero_locks(problem):
    from tools.sci_engineering import planner,policy
    from test_state_coordinated_planner import context
    p,a=ports(problem);p['state'].gt_prev[:]=[128.,0.,0.]
    ready=context(p,gt_ramp_anchor_kW=[128.,0.,0.],gt_ramp_budget_kW=0.,gt_can_stop=[False,True,True])
    ready['arrived_dispatch'].update(boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.],diesel_kW=0.)
    result=planner.solve_joint(**p,readiness_context=ready,coordination_options=policy.policy_config('M'))
    assert result['action']['boiler_steam_lbph']>0.
    assert result['planned_current_action']['boiler_steam_lbph']>0.
    diagnostic=result['joint_diagnostic']['m23_boiler_domains']
    assert diagnostic['positive_command_domain_searched'] and not diagnostic['prior_candidate_locks_reused']
    actual=solve_physical(**p,execution_action=result['action'])
    assert actual['served'][0,1]>1. and actual['action']['boiler_auxiliary_kW']>.5

def test_conditional_permission_does_not_burn_boiler_when_hrsg_covers_heat(problem):
    p,a=ports(problem);p['loads'][0]=[200.,100.,0.]
    p['availability'].update(GRID=1.,HRSG_1=1.)
    a['grid_import_kW']=100.;p['execution_contract']['grid_import_mode']='arrived_ceiling'
    actual=solve_physical(**p,execution_action=a)
    assert actual['action']['boiler_steam_lbph']==0.
    assert actual['action']['boiler_auxiliary_kW']==0.
    assert np.max(actual['lost'])<1e-4
    from tools.sci_engineering import planner,policy
    from test_state_coordinated_planner import context
    p['state'].gt_prev[:]=[128.,0.,0.]
    ready=context(p,gt_ramp_anchor_kW=[128.,0.,0.],gt_ramp_budget_kW=0.,gt_can_stop=[False,True,True])
    result=planner.solve_joint(**p,readiness_context=ready,coordination_options=policy.policy_config('M'))
    assert result['action']['boiler_steam_lbph']>0. # queued standby is retained
    assert result['planned_current_action']['boiler_steam_lbph']==0.
    p,a=ports(problem);p['loads'][0]=[127.5,0.,0.];p['availability']['HRSG_1']=1.
    with pytest.raises(ModelConstraintInfeasible):solve_physical(**p,execution_action=a)
