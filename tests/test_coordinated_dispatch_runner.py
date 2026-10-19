"""Small runner adaptation checks; no annual environment or production runs."""
import copy
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

def api():
    try: return importlib.import_module('tools.coordinated_dispatch.closed_loop')
    except ModuleNotFoundError: pytest.fail('New shared service/switch runner is missing')

def specs():
    return [dict(policy_id='execution_reserve',kind='state_dispatch',config={}),
        dict(policy_id='joint_standby_1p25_h2',kind='joint',config=dict(variant='coupled',preserve_one_gt_standby=True,critical_reserve_multiplier=1.25)),
        dict(policy_id='fixed',kind='coordinated',config=dict(coordination_mode='fixed',fixed_preference='bridge_first'))]

def manifest(a):
    return dict(execution_model_id='M0',planning_interface_id=a.PLANNING_INTERFACE,
        numerical_contract_id=a.NUMERICAL_CONTRACT_ID,standby_contract_id=a.STANDBY_CONTRACT_ID,
        service_switch_contract_id=a.SERVICE_SWITCH_CONTRACT_ID,schemas={'action':a.ACTION_SCHEMA},
        scenarios=[dict(id='one',start_hour=100,duration_steps=40,step_minutes=15.)])

def test_small_matched_panel_does_not_require_unrelated_historical_policies(monkeypatch):
    a=api();wanted=specs();made=[]
    monkeypatch.setattr(a,'_factory',lambda spec:made.append(copy.deepcopy(spec)))
    assert a._check_specs(wanted)==wanted and len(made)==3
    with pytest.raises(ValueError):a._check_specs(wanted+[wanted[-1]])
    with pytest.raises(ValueError):a._check_specs([dict(policy_id='secure_milp',kind='priority')])
    with pytest.raises(ValueError):a._check_specs([dict(policy_id='bad',kind='future_oracle',config={})])

def test_new_factory_keeps_v11_and_execution_reserve_routes():
    a=api()
    old=importlib.import_module('tools.autonomous_dispatch.closed_loop_v5')
    first,second,_=specs()
    assert type(a._factory(first)) is type(old._factory(first))
    assert type(a._factory(second)) is type(old._factory(second))
    assert set(old.SOURCE_FILES)<set(a.SOURCE_FILES)
    for name in ('planner','policy','interface','closed_loop'):
        assert 'tools/coordinated_dispatch/'+name+'.py' in a.SOURCE_FILES
    assert a._runner_namespace()['_factory'] is a._factory

def test_selective_factory_requires_new_contract_and_excludes_final12(monkeypatch):
    a=api();got={}
    def create(root,**kwargs):got.update(kwargs);return got
    monkeypatch.setattr(a,'CoordinatedHospitalIESEnv',create)
    m=manifest(a);factory=a._environment_factory({'scenario_ids':['one']},m)
    factory(ROOT,execution_contract={})
    assert got['allowed_hours']==list(range(100,111)) and got['model_id']=='M0'
    for key in ('service_switch_contract_id','planning_interface_id','numerical_contract_id','schemas'):
        bad=copy.deepcopy(m);bad.pop(key)
        with pytest.raises(ValueError):a._environment_factory({'scenario_ids':['one']},bad)
    m['scenarios'][0]['start_hour']=505
    with pytest.raises(ValueError,match='sealed'):
        a._environment_factory({'scenario_ids':['one']},m)

def test_confirmation_refuses_missing_or_changed_development_freeze(tmp_path,monkeypatch):
    a=api();m=manifest(a);ps=specs()
    with pytest.raises(ValueError,match='freeze'):
        a.check_confirmation_freeze(dict(phase='confirmation',policy_specs=ps),m,tmp_path)
    monkeypatch.setattr(a,'_source_binding',lambda:{'current':'abc'})
    frozen=dict(source_sha256={'current':'abc'},policy_specs=ps,execution_model_id='M0',
        development_completed=True,numerical_contract_id=a.NUMERICAL_CONTRACT_ID,
        service_switch_contract_id=a.SERVICE_SWITCH_CONTRACT_ID,planning_interface_id=a.PLANNING_INTERFACE)
    f=tmp_path/'FREEZE.json';f.write_text(json.dumps(frozen),encoding='utf-8')
    p=tmp_path/'PREF.json';p.write_text(json.dumps({'confirmation_gate':{'policy_freeze_path':str(f),'policy_freeze_sha256':a._sha(f)}}),encoding='utf-8')
    plan=dict(phase='confirmation',policy_specs=ps,preference_path=str(p))
    a.check_confirmation_freeze(plan,m,tmp_path)
    frozen['development_completed']=False;f.write_text(json.dumps(frozen),encoding='utf-8')
    with pytest.raises(ValueError):a.check_confirmation_freeze(plan,m,tmp_path)
