"""One current capability contract in the command master and execution copies."""
import copy
import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_engineering_planner import problem
from test_state_coordinated_planner import context
from tools.sci_engineering import planner,policy

def current_problem(problem):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][0,0]=100.;p['state'].gt_prev[:]=0.
    p['availability'].update(GRID=0.,GT_1=0.,GT_2=0.,GT_3=0.)
    p['config']['_model_closure_v22']=dict(ups_energy_kWh=20.,ups_target_kWh=30.)
    return p

def test_current_DG_permission_does_not_spread_fuel_over_whole_observation_step(problem):
    p=current_problem(problem);p['state'].diesel_energy_remaining_kWh=1.;p['diesel_start_available']=True
    r=planner.solve_joint(**p,coordination_options=policy.policy_config('M'),readiness_context=context(p,diesel_connected=True,diesel_arrived_cap_kW=900.))
    assert np.asarray(r['lost'])[0,0]<1e-4
    assert r['action']['diesel_kW']>=100.-1e-4

def test_UPS_is_available_in_current_planning_and_held_witness(problem):
    p=current_problem(problem);p['diesel_start_available']=False
    p['config']['_sci_storage_ports']=dict(ups_discharge_kW=160.,ups_charge_kW=0.,cold_discharge_kW=0.,cold_charge_kW=0.,cold_pump_kW_per_kW=.02)
    r=planner.solve_joint(**p,coordination_options=policy.policy_config('M'),readiness_context=context(p))
    assert np.asarray(r['lost'])[0,0]<1e-4
    assert r['planned_current_action']['ups_discharge_kW']>=100.-1e-4

def test_disabled_research_proxy_removes_its_soft_target_only(problem):
    p=current_problem(problem);p['diesel_start_available']=True
    r=planner.solve_joint(**p,coordination_options=policy.policy_config('C'),readiness_context=context(p,diesel_connected=True,diesel_arrived_cap_kW=900.))
    order=r['joint_diagnostic']['objective_order']
    assert 'protection_target_excess' not in order
    assert 'actual_command_shaft_support' in order

def test_pre_ready_current_boiler_has_no_steam_but_legal_restart_command_remains(problem):
    p=current_problem(problem);p['availability']['GRID']=1.;p['loads'][0,1]=40.
    p['config']['_model_closure_v22']['boiler_ready']=False
    r=planner.solve_joint(**p,coordination_options=policy.policy_config('M'),readiness_context=context(p))
    assert r['planned_current_action']['boiler_steam_lbph']<1e-5
    assert r['action']['boiler_steam_lbph']>100.

def test_arrived_phase_boiler_cannot_invent_an_intermediate_steam_setting(problem):
    p=current_problem(problem);p['loads'][0,1]=50.;p['loads'][2,0]=200.
    p['state'].diesel_energy_remaining_kWh=600.;p['diesel_start_available']=True
    r=planner.solve_joint(**p,coordination_options=policy.policy_config('C',dict(reserve_enabled=True,critical_preparation_first=True,legacy_C19_order=False)),
        readiness_context=context(p,diesel_connected=True,diesel_arrived_cap_kW=900.))
    actual=r['joint_diagnostic']['risk_scenarios']['arrived_current']['boiler_steam_lbph']
    assert actual<1e-7 or abs(actual-r['action']['boiler_steam_lbph'])<1e-7

def test_current_planner_pays_only_starts_retained_by_the_new_command(problem):
    p=current_problem(problem);p['availability']['GRID']=1.;p['grid_import_limit_kW']=200.
    p['config']['_model_closure_v22'].update(gt_start_kW=10.,boiler_ready=False,boiler_restart_kW=.5)
    # Context requests alone are not retained command permissions. Give the
    # old starter its real pending ledger, and allow M to cancel it. The old
    # fixture's unconditional 10kW assertion wrongly charged a cancelled start.
    p['availability']['GT_2']=1.
    p['loads'][0,1]=40.;ready=context(p);ready['arrived_dispatch']['boiler_steam_lbph']=1000.
    ready['pending_starts'][1]=dict(started=0.,ready=10.,requested=150.)
    ready['gt_physical_status'][1]='STARTING';ready['arrived_dispatch']['gt_electric_kW'][1]=150.
    r=planner.solve_joint(**p,coordination_options=policy.policy_config('M'),readiness_context=ready)
    assert r['action']['boiler_steam_lbph']>0.
    assert r['action']['gt_electric_kW'][1]==0.
    assert r['planned_current_action']['gt_start_electric_kW']==0.
    assert r['planned_current_action']['boiler_restart_electric_kW']==.5
    assert abs(r['planned_current_action']['grid_import_kW']-100.5)<1e-4
    # Before arrival, the old positive pending/boiler permissions still need
    # their real 10+.5kW. Preserve the original 110.5kW check at that stage.
    from tools.operating_response.physical_balance import solve_physical
    held=copy.deepcopy(ready['arrived_dispatch']);held['gt_electric_kW']=p['state'].gt_prev.tolist()
    actual_held=solve_physical(**p,execution_action=held)
    assert actual_held['action']['gt_start_electric_kW']==10.
    assert actual_held['action']['boiler_restart_electric_kW']==.5
    assert abs(actual_held['action']['grid_import_kW']-110.5)<1e-4
    p['config']['_model_closure_v22']['preserve_bridge']=False
    unreserved=planner.solve_joint(**p,coordination_options=policy.policy_config('M'),readiness_context=ready)
    assert unreserved['action']['gt_electric_kW'][1]==0.
    assert unreserved['planned_current_action']['gt_start_electric_kW']==0.
    assert unreserved['planned_current_action']['boiler_restart_electric_kW']==.5
