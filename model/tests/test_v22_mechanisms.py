"""One fixed physical base, with both soft mechanisms numerically binding."""
import copy
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_engineering_planner import problem
from test_state_coordinated_planner import context
from tools.sci_engineering import planner
from tools.model_closure_v22 import policy

def test_extra_reserve_and_preparation_order_each_have_a_binding_case(problem):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][0]=[100.,50.,0.];p['loads'][2,0]=200.
    p['state'].gt_prev[:]=0.;p['state'].diesel_energy_remaining_kWh=600.
    p['availability'].update(GRID=0.,GT_1=0.,GT_2=0.,GT_3=0.);p['diesel_start_available']=True
    p['config']['_model_closure_v22']={}
    results={};evidence=[]
    for reserve in (False,True):
        for prepare in (False,True):
            cfg=policy.policy_config('C',dict(reserve_enabled=reserve,critical_preparation_first=prepare,legacy_C19_order=False))
            r=planner.solve_joint(**p,readiness_context=context(p,diesel_connected=True,diesel_arrived_cap_kW=900.),coordination_options=cfg)
            results[reserve,prepare]=r
            layers=r['joint_diagnostic']['layers'];order=r['joint_diagnostic']['objective_order']
            assert ('current_critical_energy_reserve' in order) is reserve
            assert (order.index('preparation_P0')<order.index('arrived_current_P2')) is prepare
            evidence.append(dict(reserve=reserve,preparation_first=prepare,diesel_command_kW=r['action']['diesel_kW'],
                critical_unserved_kW=r['lost'][:2].tolist(),general_unserved_kW=r['lost'][2:].tolist(),
                mechanism_objectives=[{k:v for k,v in layer.items() if k in ('name','objective_value','final_objective_value','unit')} for layer in layers if layer['name'].startswith(('current_critical','preparation_P'))]))
    assert results[False,False]['lost'][2,0]<1e-4
    assert results[True,False]['lost'][2,0]>199.
    a=results[True,False];b=results[True,True]
    score=lambda r:next(v['final_objective_value'] for v in r['joint_diagnostic']['layers'] if v['name']=='preparation_P0')
    assert score(a)>score(b)+.001
    assert b['action']['diesel_kW']>a['action']['diesel_kW']+.2
    print(json.dumps(evidence,sort_keys=True))

def test_frozen_A_default_and_registry_remain_240_minutes():
    assert policy.SciPolicy('A').config['release_after_minutes']==240.
    assert policy.policy_specs()['A']['config']['release_after_minutes']==240.
