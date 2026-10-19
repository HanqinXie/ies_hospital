"""New union/queue/runner plumbing with disposable mocks, no physical episodes."""
import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def api():
    try:return importlib.import_module('tools.coordinated_dispatch.interface')
    except ModuleNotFoundError:pytest.fail('Independent coordinated interface is missing')


def manifest(a):
    return dict(execution_model_id='M0',numerical_contract_id=a.NUMERICAL_CONTRACT_ID,
        standby_contract_id=a.STANDBY_CONTRACT_ID,service_switch_contract_id=a.SERVICE_SWITCH_CONTRACT_ID,
        planning_interface_id=a.PLANNING_INTERFACE,schemas={'action':a.ACTION_SCHEMA})


def test_new_request_is_canonical_isolated_and_old_union_is_unchanged():
    a=api();old=importlib.import_module('tools.autonomous_dispatch.standby')
    new={'coordinated_joint':{'options':{'coordination_preference':'bridge_first'},'risk_loads_kW':np.ones((4,3)).tolist()}}
    before=copy.deepcopy(new);got=a.validate_action(new)
    assert got['coordinated_joint']['options']['service_budget_fractions']==[.05,.20] and new==before
    for legacy in ({},{'targets':{'gt_1':100.}},{'joint':{'options':{'variant':'coupled','preserve_one_gt_standby':True}}}):
        assert a.validate_action(legacy)==old.validate_action(legacy)
    for bad in ({**new,'joint':{}},{'coordinated_joint':{'future_script':[]}},{'coordinated_joint':{'options':{'features_enabled':1}}},
        {'coordinated_joint':{'risk_loads_kW':[[float('nan')]*3]*4}}):
        with pytest.raises(ValueError):a.validate_action(bad)
    with pytest.raises(ValueError):old.validate_action(new)


def test_new_core_routes_only_new_request_and_preserves_existing_queue_and_state(monkeypatch):
    a=api();planner=importlib.import_module('tools.coordinated_dispatch.planner');old=importlib.import_module('tools.autonomous_dispatch.standby')
    seen=[];dispatch=dict(gt_electric_kW=[0.]*3,grid_import_kW=0.,diesel_kW=200.,boiler_steam_lbph=300.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.]*3)
    lost=np.array([[0.]*3,[0.]*3,[10.]*3,[20.]*3])
    def solve(*args,**kwargs):
        seen.append(kwargs);return dict(action=dispatch,planned_current_action=dispatch,lost=lost,command_disconnected=np.zeros((4,3)),joint_diagnostic={})
    monkeypatch.setattr(planner,'solve_joint',solve)
    core=a.CoordinatedTransitionCore.__new__(a.CoordinatedTransitionCore)
    core.failed=False;core.time=15.;core.sequence=9;core.events=[];core.solver_calls=[];core.gt_start_pending={}
    existing={'id':9,'dispatch':{'gt_electric_kW':[0.,0.,0.]}}
    core.pending_commands=[copy.deepcopy(existing)]
    state=SimpleNamespace(gt_prev=np.zeros(3),diesel_energy_remaining_kWh=1234.,overload_minutes=np.array([3.,0.,0.]))
    core.env=SimpleNamespace(plant=object(),dt_h=.25,scenario={},execution_contract={},state=state,
        config={'grid_import_limit_kW':350.,'gt_ramp_kW_per_hour':900.,'solver_time_limit_sec':6.})
    core.data_at=lambda t:(100,None,np.ones((4,3)),{})
    core.delayed_at=lambda t:(True,27.5);core.diesel_available=lambda t,av:False
    command=core.generate_command({'coordinated_joint':{'options':{'coordination_preference':'bridge_first'}}})
    assert seen[0]['joint_options']['coordination_preference']=='bridge_first' and seen[0]['retention_eligible'] is True
    assert command['id']==10 and command['generated_minute']==15. and command['arrival_minute']==27.5
    assert core.pending_commands[0]==existing and core.pending_commands[-1]==core.events[-1]['command']==command
    assert command['disconnected']==np.zeros((4,3)).tolist() and command['joint_dispatch']['planning_interface']==a.PLANNING_INTERFACE
    assert state.diesel_energy_remaining_kWh==1234. and state.overload_minutes.tolist()==[3.,0.,0.]
    assert a.CoordinatedTransitionCore.boundary is old.StandbyTransitionCore.boundary
    assert a.CoordinatedTransitionCore.advance is old.StandbyTransitionCore.advance
    sentinel=object();calls=[]
    monkeypatch.setattr(old.StandbyTransitionCore,'generate_command',lambda self,action:(calls.append(action),sentinel)[1])
    assert core.generate_command({}) is sentinel and core.generate_command({'joint':{}}) is sentinel
    assert calls==[{}, {'joint':{}}] and len(seen)==1


def test_selective_reset_installs_new_core_and_keeps_m0_only(monkeypatch):
    a=api();old=importlib.import_module('tools.autonomous_dispatch.environment')
    def reset(self,*,scenario,seed=None):return globals()['TransitionCore']
    monkeypatch.setattr(old.OriginalEnvironment,'reset',reset)
    env=a.CoordinatedHospitalIESEnv.__new__(a.CoordinatedHospitalIESEnv)
    env.allowed_hours=frozenset(range(100,111));env.sealed_days=frozenset();env.model_id='M0'
    assert env.reset(scenario={'start_hour':100,'duration_steps':40,'step_minutes':15.}) is a.CoordinatedTransitionCore
    env.model_id='M1_ordered_boundary'
    with pytest.raises(ValueError):env.reset(scenario={'start_hour':100,'duration_steps':40,'step_minutes':15.})


def test_exporter_binds_new_validation_and_all_contracts_without_current_problem(monkeypatch):
    a=api();episode=importlib.import_module('tools.autonomous_dispatch.episode')
    def export(*args,**kwargs):return dict(policy_environment=globals()['PolicyEnvironment'])
    monkeypatch.setattr(episode.original,'run_episode',export)
    env=SimpleNamespace(model_id='M0',input_access_receipt={'sealed_numeric_rows_parsed':0})
    result=a.run_policy_episode(env,{},object(),1,manifest(a))
    assert result['policy_environment'] is a.CoordinatedPolicyEnvironment
    assert result['planning_interface']==a.PLANNING_INTERFACE and result['action_schema']==a.ACTION_SCHEMA
    for key in ('numerical_contract_id','standby_contract_id','service_switch_contract_id'):
        assert result[key]==manifest(a)[key]
    for key in ('numerical_contract_id','standby_contract_id','service_switch_contract_id','planning_interface_id','schemas','execution_model_id'):
        bad=manifest(a);bad.pop(key)
        with pytest.raises(ValueError):a.run_policy_episode(env,{},object(),1,bad)
