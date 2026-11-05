"""Standby request/queue/runner contracts; no physical-input or episode runs."""
import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def api():
    try:
        return importlib.import_module('tools.autonomous_dispatch.standby')
    except ModuleNotFoundError:
        pytest.fail('Independent standby interface is missing')


def manifest(a):
    return dict(execution_model_id='M0', numerical_contract_id=a.NUMERICAL_CONTRACT_ID,
                standby_contract_id=a.STANDBY_CONTRACT_ID, planning_interface_id=a.PLANNING_INTERFACE,
                schemas={'action':a.ACTION_SCHEMA})


def test_standby_canonical_validation_keeps_full_flag_and_rejects_future_or_nonboolean():
    a=api(); old=importlib.import_module('tools.autonomous_dispatch.margin')
    request={'joint':{'options':{'variant':'coupled','preserve_one_gt_standby':True,
                                'critical_reserve_multiplier':1.5},'risk_loads_kW':np.ones((4,3)).tolist()}}
    original=copy.deepcopy(request)
    with pytest.raises(ValueError):old.validate_action(request)
    canonical=a.validate_action(request)
    assert canonical['joint']['options']['preserve_one_gt_standby'] is True
    assert canonical['joint']['options']['critical_reserve_multiplier']==1.5
    assert canonical['joint']['risk_loads_kW']==request['joint']['risk_loads_kW'] and request==original
    assert a.validate_action({'joint':{}})['joint']['options']['preserve_one_gt_standby'] is False
    for value in (1,0,'true',None,[]):
        with pytest.raises(ValueError):a.validate_action({'joint':{'options':{'variant':'coupled','preserve_one_gt_standby':value}}})
    with pytest.raises(ValueError):a.validate_action({'joint':{'options':{'variant':'economic','preserve_one_gt_standby':True}}})
    with pytest.raises(ValueError):a.validate_action({'joint':{'options':{'future_grid_end':60.}}})
    assert a.validate_action({})=={}


def test_standby_policy_same_clock_request_contains_full_canonical_configuration():
    a=api(); policy=a.StandbyDispatchPolicy({'variant':'coupled','preserve_one_gt_standby':True},'standby_on')
    features={f'{g}_{c}_demand_kW':20. for g in ('P0','P1','P2','P3') for c in ('e','h','c')}
    history=[dict(decision_time_min=15.,features=features,
        feature_metadata={k:dict(available_time_min=15.,valid_time_min=15.) for k in features})]
    policy.bind_current(SimpleNamespace(time_minute=15.,loads=np.full((4,3),50.)))
    action,info=policy.decide(history)
    assert action['joint']['options']==policy.config and policy.config['preserve_one_gt_standby'] is True
    assert action['joint']['risk_loads_kW']==np.full((4,3),20.).tolist()
    assert info['planning_interface']=='joint_command.standby.v5' and info['future_rollouts_online']==0


def test_standby_core_forwards_options_and_current_eligibility_through_original_queue(monkeypatch):
    a=api(); planner=importlib.import_module('tools.autonomous_dispatch.planner_standby');seen=[]
    dispatch={'gt_electric_kW':[0.]*3,'grid_import_kW':0.,'diesel_kW':200.,'boiler_steam_lbph':300.,
              'absorption_cooling_kW':0.,'electric_chiller_cooling_kW':[0.]*3}
    lost=np.array([[0.]*3,[0.]*3,[10.]*3,[20.]*3])
    def solve(*args,**kwargs):
        seen.append(kwargs)
        return dict(action=dispatch,planned_current_action=dispatch,lost=lost,
                    command_disconnected=np.zeros((4,3)),joint_diagnostic={})
    monkeypatch.setattr(planner,'solve_joint',solve)
    core=a.StandbyTransitionCore.__new__(a.StandbyTransitionCore)
    core.failed=False;core.time=15.;core.sequence=0;core.pending_commands=[];core.events=[];core.solver_calls=[];core.gt_start_pending={}
    core.env=SimpleNamespace(plant=object(),dt_h=.25,scenario={},execution_contract={},state=SimpleNamespace(gt_prev=np.zeros(3)),
        config={'grid_import_limit_kW':350.,'gt_ramp_kW_per_hour':900.,'solver_time_limit_sec':6.})
    core.data_at=lambda t:(100,None,np.ones((4,3)),{})
    core.delayed_at=lambda t:(True,27.5);core.diesel_available=lambda t,av:False
    command=core.generate_command({'joint':{'options':{'variant':'coupled','preserve_one_gt_standby':True}}})
    assert seen[0]['joint_options']['preserve_one_gt_standby'] is True and seen[0]['retention_eligible'] is True
    assert command['arrival_minute']==27.5 and command['disconnected']==np.zeros((4,3)).tolist()
    assert command['joint_dispatch']['planning_interface']=='joint_command.standby.v5'
    assert command['joint_dispatch']['diagnostic']['planned_unserved_kW']==lost.tolist()
    assert core.pending_commands[0]==core.events[-1]['command']==command
    core.pending_commands.append({'dispatch':{'gt_electric_kW':[100.,0.,0.]}})
    core._solve_joint(command['request'],np.ones((4,3)),{})
    assert seen[-1]['retention_eligible'] is False


def test_standby_reset_uses_current_core_and_existing_ordered_boundary_mix(monkeypatch):
    a=api(); original=importlib.import_module('tools.autonomous_dispatch.environment')
    def reset(self,*,scenario,seed=None):return globals()['TransitionCore']
    monkeypatch.setattr(original.OriginalEnvironment,'reset',reset)
    for model in ('M0','M1_ordered_boundary'):
        env=a.StandbyHospitalIESEnv.__new__(a.StandbyHospitalIESEnv)
        env.allowed_hours=frozenset(range(100,111));env.sealed_days=frozenset();env.model_id=model
        kind=env.reset(scenario={'start_hour':100,'duration_steps':40,'step_minutes':15.})
        assert issubclass(kind,a.StandbyTransitionCore)
        if model=='M1_ordered_boundary':assert issubclass(kind,importlib.import_module('tools.autonomous_dispatch.model_order').OrderedBoundaryMixin)


def test_standby_exporter_requires_all_versioned_contracts_and_binds_new_validator(monkeypatch):
    a=api(); episode=importlib.import_module('tools.autonomous_dispatch.episode')
    def export(*args,**kwargs):return dict(policy_environment=globals()['PolicyEnvironment'])
    monkeypatch.setattr(episode.original,'run_episode',export)
    env=SimpleNamespace(model_id='M0',input_access_receipt={'sealed_numeric_rows_parsed':0})
    payload=a.run_policy_episode(env,{},object(),1,manifest(a))
    assert payload['policy_environment'] is a.StandbyPolicyEnvironment
    for key in ('numerical_contract_id','standby_contract_id'):assert payload[key]==manifest(a)[key]
    assert payload['planning_interface']==a.PLANNING_INTERFACE
    for key in ('numerical_contract_id','standby_contract_id','planning_interface_id','schemas'):
        bad=manifest(a);bad.pop(key)
        with pytest.raises(ValueError):a.run_policy_episode(env,{},object(),1,bad)


def test_v5_runner_keeps_44_sources_and_uses_47_source_contract_factory(monkeypatch):
    a=api(); old=importlib.import_module('tools.autonomous_dispatch.closed_loop_v4');before=old._source_binding()
    runner=importlib.import_module('tools.autonomous_dispatch.closed_loop_v5')
    assert old._source_binding()==before and set(old.SOURCE_FILES)<set(runner.SOURCE_FILES)
    assert len(runner.SOURCE_FILES)==47 and runner.previous.__name__=='tools.autonomous_dispatch.closed_loop'
    assert runner.SCHEMA=='autonomous_dispatch.standby.matched_full_feedback.v5'
    assert runner.ACTION_SCHEMA=='autonomous_dispatch.legacy_or_joint_action.v5'
    for name in ('planner_standby','standby','closed_loop_v5'):assert f'tools/autonomous_dispatch/{name}.py' in runner.SOURCE_FILES
    specs=[{'policy_id':'priority_milp','kind':'priority'},{'policy_id':'secure_milp','kind':'secure'},
           {'policy_id':'standby_on','kind':'joint','config':{'variant':'coupled','preserve_one_gt_standby':True}}]
    assert runner._check_specs(specs)==specs and isinstance(runner._factory(specs[-1]),a.StandbyDispatchPolicy)
    ns=runner._runner_namespace();assert ns['run_policy_episode'] is a.run_policy_episode and ns['_factory'] is runner._factory
    got={}
    def env(root,**kwargs):got.update(kwargs);return got
    monkeypatch.setattr(runner,'StandbyHospitalIESEnv',env)
    m=manifest(a);m['scenarios']=[dict(id='one',start_hour=100,duration_steps=40,step_minutes=15.)]
    factory=runner._environment_factory({'scenario_ids':['one']},m);factory(Path('D:/metadata-only'),execution_contract={})
    assert got['allowed_hours']==list(range(100,111)) and got['model_id']=='M0'
    for key in ('numerical_contract_id','standby_contract_id','planning_interface_id','schemas'):
        bad=copy.deepcopy(m);bad.pop(key)
        with pytest.raises(ValueError):runner._environment_factory({'scenario_ids':['one']},bad)
    assert runner._globals()['_environment_factory'] is runner._environment_factory
