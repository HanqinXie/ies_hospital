"""Current-fuel budget release and isolated 53-source plumbing; no trajectories."""
import copy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from test_coordinated_dispatch_policy import row


def api(name):
    try:return importlib.import_module('tools.coordinated_dispatch.'+name)
    except ModuleNotFoundError:pytest.fail('Independent adaptive budget '+name+' is missing')


def support(fuel=100.,power=100.,status='CONNECTED'):
    return row(15.,availability_GRID=0.,grid_import_prev_kW=0.,arrived_grid_import_limit_kW=0.,
        diesel_prev_kW=power,diesel_energy_kWh=fuel,diesel_status=status)


def test_budget_release_uses_actual_positive_dg_and_strict_current_coverage_without_mutation():
    a=api('policy_adaptive');policy=a.AdaptiveBudgetPolicy({},'adaptive',budget_feedback=True)
    r=support();before=copy.deepcopy(r);base=copy.deepcopy(policy.config)
    request,diagnostic=policy.decide([r])
    assert request['coordinated_joint']['options']['service_budget_fractions']==[1.,1.]
    assert diagnostic['budget_release_active'] is True
    assert diagnostic['budget_feedback_actual_diesel_kW']==100.
    assert diagnostic['budget_feedback_fuel_remaining_kWh']==100.
    assert diagnostic['budget_feedback_coverage_hours']==1.
    assert diagnostic['base_service_budget_fractions']==[.05,.20]
    assert diagnostic['effective_service_budget_fractions']==[1.,1.]
    assert diagnostic['requested_joint_options']==request['coordinated_joint']['options']
    assert policy.config==base and r==before and not hasattr(policy,'bind_current')
    for record in (support(1.,0.,'STARTING'),support(1.,0.,'CONNECTED'),support(200.),support(201.)):
        action,info=policy.decide([record])
        assert info['budget_release_active'] is False
        assert action['coordinated_joint']['options']['service_budget_fractions']==[.05,.20]


def test_fixed_state_by_constant_adaptive_changes_only_declared_order_and_budget():
    a=api('policy_adaptive');records=[support(50.)];actions={}
    for mode in ('fixed','state'):
        for feedback in (False,True):
            p=a.AdaptiveBudgetPolicy({'coordination_mode':mode,'fixed_preference':'standby_first'},
                mode+str(feedback),budget_feedback=feedback)
            actions[(mode,feedback)]=p.decide(records)[0]['coordinated_joint']['options']
    for (mode,feedback),options in actions.items():
        assert options['coordination_preference']==('standby_first' if mode=='fixed' else 'bridge_first')
        assert options['service_budget_fractions']==([1.,1.] if feedback else [.05,.20])
        assert {k:v for k,v in options.items() if k not in ('coordination_preference','service_budget_fractions')}=={
            k:v for k,v in actions[('fixed',False)].items() if k not in ('coordination_preference','service_budget_fractions')}


def test_adaptive_keeps_old_observation_poison_rejection_and_flag_type_validation():
    a=api('policy_adaptive');p=a.AdaptiveBudgetPolicy({},'feedback',budget_feedback=True)
    r=support();r['feature_metadata']['diesel_energy_kWh']['valid_time_min']=100.
    with pytest.raises(ValueError):p.decide([r])
    r=support();r['labels']={'future':True}
    with pytest.raises(ValueError):p.decide([r])
    class Poison:
        def __deepcopy__(self,memo):raise AssertionError('Scenario/root copied')
        def __getattr__(self,key):raise AssertionError('Scenario/root read')
    plain=support();r=copy.deepcopy(plain);r.update(scenario=Poison(),root=Poison())
    assert p.decide([r])==p.decide([plain])
    for value in (0,1,None,'true',[]):
        with pytest.raises(ValueError):a.AdaptiveBudgetPolicy({},'bad',budget_feedback=value)


def test_adaptive_runner_isolates_factory_and_preserves_original_51_source_route():
    a=api('closed_loop_adaptive');old=api('closed_loop')
    assert len(old.SOURCE_FILES)==51 and len(a.SOURCE_FILES)==53
    assert set(a.SOURCE_FILES)-set(old.SOURCE_FILES)=={
        'tools/coordinated_dispatch/policy_adaptive.py','tools/coordinated_dispatch/closed_loop_adaptive.py'}
    spec=dict(policy_id='adaptive',kind='coordinated',config={'coordination_mode':'fixed',
        'fixed_preference':'bridge_first','budget_feedback':True})
    before=copy.deepcopy(spec);p=a._factory(spec)
    assert p.budget_feedback is True and 'budget_feedback' not in p.config and spec==before
    assert a._check_specs([spec])==[spec]
    with pytest.raises(ValueError):old._check_specs([spec])
    for value in (1,None,'yes'):
        bad=copy.deepcopy(spec);bad['config']['budget_feedback']=value
        with pytest.raises(ValueError):a._check_specs([bad])
    ns=a._runner_namespace()
    for key in ('_factory','_source_binding','run_policy_episode','_payload_binding'):
        assert ns[key] is getattr(a,key)
    assert a._globals()['_environment_factory'] is a._environment_factory


def test_adaptive_payload_tag_is_checked_and_no_underlying_environment_is_replaced(monkeypatch):
    a=api('closed_loop_adaptive');old=api('closed_loop');seen=[]
    def run(*args,**kwargs):seen.append(args[0]);return dict(original_payload=True)
    monkeypatch.setattr(old,'run_policy_episode',run)
    monkeypatch.setattr(old,'_payload_binding',lambda *args:dict(passed=True,checks={'original_binding':True}))
    manifest={'budget_feedback_contract_id':a.BUDGET_FEEDBACK_CONTRACT_ID}
    env=object();payload=a.run_policy_episode(env,{},object(),1,manifest)
    assert seen==[env] and payload['budget_feedback_contract_id']==a.BUDGET_FEEDBACK_CONTRACT_ID
    assert a._payload_binding(payload,{},manifest)['passed'] is True
    with pytest.raises(ValueError):a.run_policy_episode(env,{},object(),1,{})
    marker=object()
    monkeypatch.setattr(old,'_environment_factory',lambda *args:marker)
    assert a._environment_factory({},manifest) is marker
    with pytest.raises(ValueError):a._environment_factory({}, {})
    payload.pop('budget_feedback_contract_id')
    assert a._payload_binding(payload,{},manifest)['passed'] is False


def test_adaptive_analysis_adds_full_53_receipt_without_overwriting_common_component_receipt(monkeypatch,tmp_path):
    a=api('closed_loop_adaptive');old=api('closed_loop')
    paths={n:tmp_path/(n+'.json') for n in ('manifest','plan','audit')}
    for p in paths.values():p.write_text('{}',encoding='utf-8')
    out=tmp_path/'analysis';common=b'{"runtime_source_count":51}'
    def analyze(*args):
        globals()['verify_batch'](args[1])
        out.mkdir()
        for n in ('SUMMARY.json','SERVICE_SWITCH_SUMMARY.json'):(out/n).write_text('{}',encoding='utf-8')
        (out/'COORDINATED_SOURCE_BINDING.json').write_bytes(common)
        return {'registered_trajectory_count':1}
    monkeypatch.setattr(a,'verify_batch',lambda *args:None)
    monkeypatch.setattr(old,'analyze_batch',analyze)
    result=a.analyze_batch(paths['manifest'],paths['plan'],tmp_path/'results',paths['audit'],out)
    receipt=json.loads((out/'ADAPTIVE_SOURCE_BINDING.json').read_text(encoding='utf-8'))
    assert result['registered_trajectory_count']==1 and (out/'COORDINATED_SOURCE_BINDING.json').read_bytes()==common
    assert receipt['runtime_source_count']==53 and len(receipt['registered_runtime_sources'])==53
    assert receipt['extra_raw_opens']==0 and receipt['physical_trajectories_started']==0
