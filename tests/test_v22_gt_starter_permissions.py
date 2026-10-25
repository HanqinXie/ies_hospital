"""Actual startup electricity follows retained/created eligible permissions."""
import copy
import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_engineering_planner import problem
from test_state_coordinated_planner import context
from test_v22_core_environment import make
from test_sci_environment import scenario
from tools.sci_engineering import planner,policy
from tools.model_closure_v22.environment import decision_snapshot
from tools.model_closure_v22.policy import SciPolicy
from tools.engineering_model.execution import GTClockLedger
from hospital_ies.model import gt_level_properties

def pending_problem(problem,electric):
    p=copy.deepcopy(problem);p['row'].outdoor_temperature_C=12.416666666666666
    p['loads'][:]=0.;p['loads'][0,0]=electric;p['state'].gt_prev[:]=[128.,0.,0.]
    p['availability'].update(GRID=0.,DIESEL=0.,GT_3=0.,ABS=0.,EC_1=0.,EC_2=0.,EC_3=0.)
    rated=gt_level_properties(p['plant'],p['row'].outdoor_temperature_C)[0][-1]
    p['availability']['GT_1']=128./(.4*rated);p['grid_import_limit_kW']=0.;p['diesel_start_available']=False
    p['config']['_model_closure_v22']=dict(gt_start_kW=10.)
    ready=context(p);ready['gt_physical_status'][1]='STARTING'
    ready['pending_starts'][1]=dict(started=0.,ready=10.,requested=150.)
    ready['arrived_dispatch'].update(boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.],diesel_kW=0.)
    ready['arrived_dispatch']['gt_electric_kW'][1]=150.
    return p,ready

def arrive_and_integrate(p,ready,result):
    e=make();e.reset(scenario=scenario(),seed=37);c=e.core
    row=copy.deepcopy(p['row']);row.sci_clinical_increment_kW=np.zeros((4,3)).tolist();row.sci_auxiliary_increment_kW=[0.]*4
    f=dict(p['availability'],STATION_AC=1.,GT_CONTROL_DC=1.,DG_START_DC=1.)
    c._hardware_data_at=lambda minute:(4001,row,p['loads'].copy(),dict(f))
    c.executed=copy.deepcopy(ready['arrived_dispatch']);c.executed['gt_electric_kW']=p['state'].gt_prev.tolist()
    c.arrived_dispatch=copy.deepcopy(ready['arrived_dispatch']);c.state.gt_prev[:]=p['state'].gt_prev
    c.gt_clocks=GTClockLedger(p['state'].gt_prev);c.gt_start_pending={i:copy.deepcopy(item) for i,item in enumerate(ready['pending_starts']) if item}
    c._m22_gt_progress={i:0. for i in c.gt_start_pending};c.ramp_time=0.;c.ramp_anchor=p['state'].gt_prev.copy();c.ramp_budget=225.
    c.pending_commands.append(dict(id=1,generated_minute=0.,arrival_minute=0.,dispatch=copy.deepcopy(result['action']),
        disconnected=np.asarray(result['command_disconnected']).tolist(),reference_cooling_kW=0.,weight=0.,request={}))
    c.advance(.1)
    return c

@pytest.mark.parametrize('electric,kept,starter',[(118.,True,10.),(128.,False,0.)])
def test_pending_keep_or_cancel_matches_actual_power_and_progress(problem,electric,kept,starter):
    p,ready=pending_problem(problem,electric)
    result=planner.solve_joint(**p,readiness_context=ready,coordination_options=policy.policy_config('M'))
    assert (result['action']['gt_electric_kW'][1]>0.) is kept
    assert result['planned_current_action']['gt_start_electric_kW']==pytest.approx(starter,abs=1e-7)
    c=arrive_and_integrate(p,ready,result)
    assert c.segments and not c.errors
    assert all(x['action']['gt_start_electric_kW']==pytest.approx(starter,abs=1e-7) for x in c.segments)
    assert (1 in c.gt_start_pending) is kept
    if kept:assert c._m22_gt_progress[1]==pytest.approx(.1)
    else:assert 1 not in c._m22_gt_progress

@pytest.mark.parametrize('eligible',[True,False])
def test_new_start_current_eligibility_matches_actual_arrival_and_power(eligible):
    e=make();e.reset(scenario=scenario(),seed=39);c=e.core
    c.executed['gt_electric_kW']=[0.,0.,0.];c.state.gt_prev[:]=0.;c.gt_clocks=GTClockLedger([0.,0.,0.]);c.gt_start_pending={}
    if not eligible:c.gt_clocks.last_off[:]=0.
    request,_=SciPolicy('M',plant=e.plant).decide([decision_snapshot(e)])
    command=c.generate_command(request);planned=command['joint_dispatch']['planned_current_action'];c.boundary()
    assert c.gt_start_pending
    active=[i for i,item in c.gt_start_pending.items() if item['started']<=c.time+1e-9]
    expected=10.*len(active)
    assert (expected>0.) is eligible
    assert planned['gt_start_electric_kW']==pytest.approx(expected,abs=1e-7)
    c.advance(.1)
    assert all(x['action']['gt_start_electric_kW']==pytest.approx(expected,abs=1e-7) for x in c.segments)
    if eligible:assert all(c._m22_gt_progress.get(i,0.)==pytest.approx(.1) for i in active)
    else:assert not any(c._m22_gt_progress.values())
