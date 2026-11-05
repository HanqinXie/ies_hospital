"""Current unserved demand is not the intention of a later ordinary cut."""
import copy
import importlib
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from test_engineering_planner import problem,BASE,context as engineering_context
from test_operating_readiness import current,dispatch
from test_execution_recovery_numerics import limits

def implementation(module):
    """Before the fork exists, reproduce the actual retained M18 behavior."""
    candidate='tools.model_closeout.'+module
    try:exists=importlib.util.find_spec(candidate) is not None
    except ModuleNotFoundError:exists=False
    return importlib.import_module(candidate if exists else 'tools.execution_recovery.'+module)

def no_ready_source(problem):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][0,0]=40.;p['loads'][1,0]=50.
    p['state'].gt_prev[:]=0.;p['availability']['GRID']=0.
    for i in range(1,4):p['availability'][f'GT_{i}']=0.
    p['state'].diesel_status='STARTING';p['state'].diesel_start_minute=32.5
    p['diesel_start_available']=False
    return p

def solve(p,module):
    permitted=dispatch([0.,0.,0.],grid=350.,diesel=500.)
    readiness=current([0.,0.,0.],observation_time_minute=45.,arrived_dispatch=permitted,
        diesel_arrived_cap_kW=500.,observed_critical_unserved_kW=p['loads'][:2].tolist())
    kwargs=dict(joint_options=BASE,execution_limits=limits())
    if module=='engineering_planner':kwargs['execution_context']=engineering_context(p,arrived_dispatch=permitted)
    else:
        kwargs['readiness_context']=readiness
        if module=='planner':kwargs['coordination_options']={'execution_recovery':True}
    return implementation(module).solve_joint(**p,**kwargs)

@pytest.mark.parametrize('module',['engineering_planner','operating_planner','planner'])
def test_unready_diesel_shortage_does_not_issue_future_critical_cut(problem,module):
    p=no_ready_source(problem);before=copy.deepcopy(p['state'])
    result=solve(p,module)
    assert np.asarray(result['lost'])[:2,0].tolist()==pytest.approx([40.,50.],abs=1e-7)
    assert result['planned_current_action']['diesel_kW']==0.
    assert np.max(np.asarray(result['command_disconnected'])[:2])<=1e-7, implementation(module).__name__
    assert result['action']['diesel_kW']>=90.-1e-5,'request restoration and a useful conditional source permission jointly'
    np.testing.assert_array_equal(p['state'].gt_prev,before.gt_prev)

@pytest.mark.parametrize('module',['engineering_planner','operating_planner','planner'])
def test_unavailable_resources_remain_real_shortage_without_automatic_cut_intent(problem,module):
    p=no_ready_source(problem);p['availability']['DIESEL']=0.;p['state'].diesel_energy_remaining_kWh=0.
    p['loads'][0,1:]=[12.,15.]
    for i in range(1,4):p['availability'][f'BOILER_{i}']=0.
    result=solve(p,module)
    assert np.asarray(result['lost'])[0].tolist()==pytest.approx([40.,12.,15.],abs=1e-7)
    assert result['action']['diesel_kW']==0.
    assert np.max(np.asarray(result['command_disconnected'])[:2])<=1e-7
    assert max(map(abs,result['energy_balance_residuals']))<=1e-7
