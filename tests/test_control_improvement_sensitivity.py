"""Synthetic binding/ledger checks only; no hospital fits or continuations."""
import copy,importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from hospital_ies.dataset_manifest import digest
B=ROOT/'outputs/v43_control_improvement_20261014_run01'

def module():
    p=B/'sensitivity_runner.py'
    assert p.is_file(), 'Sensitivity register/bind/runner feature is not implemented'
    spec=importlib.util.spec_from_file_location('sensitivity_under_review',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def inputs():
    config={'name':'nominal','diesel_mode':'legacy_explicit_permission'}
    ids=[f's{i:02d}' for i in range(24)]
    specs=[{'id':sid,'experiment_role':'development_boundary','root_family_id':f'r{i//2}',
            'step_minutes':15,'duration_steps':40,'initialization_seed':42,'faults':{'GRID':0},'future':[99,160]} for i,sid in enumerate(ids)]
    parent={'scenarios':specs,'splits':{s['id']:{'group_id':s['root_family_id'],'experiment_role':'development_boundary'} for s in specs},'options':{'seed':101,'label_horizons':[15,30,60,120]},'execution_contract':config,'schemas':{'feature':'synthetic'},'files':[],'runtime':{},'simulator_hash':'unchangedphysical','policies':['priority_milp','secure_milp']}
    parent['signature']=digest(parent)
    profiles=[{'configuration_id':x,'execution_contract':dict(config,name=x),'configuration_sha256':digest(dict(config,name=x)),'not_OEM_calibration':True,'physical_capacities_and_failure_thresholds_unchanged':True} for x in ['nominal','strict','bus','start2']]
    reg={'scenario_ids':ids,'configurations':profiles,'root_families':12,'maximum_actual_episodes':192,'policies':['current_rule_projection0','execution_aware_RF_rank_projection0_seed4101'],'representative_training_seed_predeclared':4101,'score_interval_minutes':[60,420],'full_interval_minutes':[0,600],'source_bindings':[]}
    reg['signature']=digest(reg);return parent,reg

def fake_build(root,specs,policies,contract,schemas,options,splits):
    m={'scenarios':copy.deepcopy(specs),'policies':sorted(policies),'execution_contract':copy.deepcopy(contract),'schemas':schemas,'options':copy.deepcopy(options),'splits':copy.deepcopy(splits),'files':[],'runtime':{},'simulator_hash':'unchangedphysical','execution_contract_id':contract['name']+':'+digest(contract),'assumption_set_id':'sensitivity:'+digest(contract)}
    m['signature']=digest(m);return m

def test_profiles_preserve_all_exogenous_seed_and_roles_without_parent_mutation(monkeypatch):
    m=module();parent,reg=inputs();before=copy.deepcopy(parent);monkeypatch.setattr(m,'build_manifest',fake_build)
    profiles=m.derive_profile_manifests(parent,reg)
    assert len(profiles)==4 and parent==before
    for derived in profiles:
        assert derived['scenarios']==parent['scenarios'] and derived['options']==parent['options'] and derived['splits']==parent['splits']
        assert derived['simulator_hash']=='unchangedphysical'
        assert derived['execution_contract'] in [c['execution_contract'] for c in reg['configurations']]
        assert derived['policies']==sorted(reg['policies'])
        body=copy.deepcopy(derived);assert body.pop('signature')==digest(body)

@pytest.mark.parametrize('mutation',['duplicate','missing','role','rootcount','budget','config','seed'])
def test_registration_rejects_support_or_configuration_drift(mutation,monkeypatch):
    m=module();parent,reg=inputs();monkeypatch.setattr(m,'build_manifest',fake_build)
    if mutation=='duplicate':reg['scenario_ids'][-1]=reg['scenario_ids'][0]
    elif mutation=='missing':reg['scenario_ids'][-1]='absent'
    elif mutation=='role':parent['scenarios'][0]['experiment_role']='pilot_train'
    elif mutation=='rootcount':reg['root_families']=11
    elif mutation=='budget':reg['maximum_actual_episodes']=191
    elif mutation=='config':reg['configurations'][0]['execution_contract']['name']='tampered'
    elif mutation=='seed':reg['representative_training_seed_predeclared']=4102
    with pytest.raises(ValueError):m.derive_profile_manifests(parent,reg)

def test_known_prefix_positive_and_unknown_remaining_targets_are_retained():
    m=module();unknown={'targets':[1.,None]+[None]*17,'target_mask':[True]+[False]*18}
    complete={'targets':[0.,0.]+[0.]*17,'target_mask':[True]*19}
    record=m.paired_window(complete,unknown)
    assert record['comparison_complete'] is False and record['target_delta'][0]==1.
    assert record['target_delta'][1:]==[None]*18
    assert record['known_target_mask']==[True]+[False]*18

def test_physical_initial_only_normalizes_declared_fault_set_and_mode():
    m=module();a={'state':{'gt_prev':[1.,2.,3.],'extra_faulted':['GT_2','GT_1'],'topology_mode':'legacy'},'executed':{'GT':[1,2,3]}}
    b=copy.deepcopy(a);b['state']['extra_faulted'].reverse();b['state']['topology_mode']='emergency_bus'
    assert m.physical_initial(a)==m.physical_initial(b)
    b['state']['gt_prev'].reverse();assert m.physical_initial(a)!=m.physical_initial(b)
    assert m.physical_initial(None) is None

@pytest.mark.parametrize('fault',['prototype','seed','parent','artifact'])
def test_model_binding_rejects_wrong_scope_seed_parent_or_bytes(tmp_path,monkeypatch,fault):
    m=module();parent,reg=inputs();model=tmp_path/'RF_seed4101';model.mkdir();(model/'model.joblib').write_bytes(b'synthetic-not-loaded')
    metadata={'seed':4101,'source_binding':{'prototype_train_only':False,'manifest_sha256':'parent-bytes'},'artifacts':{'model.joblib':m.sha(model/'model.joblib')}}
    if fault=='prototype':metadata['source_binding']['prototype_train_only']=True
    elif fault=='seed':metadata['seed']=4102
    elif fault=='parent':metadata['source_binding']['manifest_sha256']='other-parent'
    elif fault=='artifact':(model/'model.joblib').write_bytes(b'tamper')
    (model/'metadata.json').write_text(json.dumps(metadata),encoding='utf-8')
    monkeypatch.setattr(m,'load_rank_model',lambda p:SimpleNamespace(metadata=metadata))
    with pytest.raises(ValueError):m.checked_rank_binding(model,'parent-bytes')

def test_registration_model_binding_and_run_are_separate_commands():
    m=module();parser=m.argument_parser()
    r=parser.parse_args(['register','parent.json','prereg.json','REGISTER']);assert r.command=='register' and not hasattr(r,'model')
    b=parser.parse_args(['bind','REGISTER','MODEL','BIND.json']);assert b.command=='bind'
    run=parser.parse_args(['run','BIND.json','ACTUAL']);assert run.command=='run'

def test_profile_pair_masks_do_not_treat_unobserved_failure_as_zero():
    m=module();a={'targets':[None]*19,'target_mask':[False]*19};result=m.paired_window(a,a)
    assert result['target_delta']==[None]*19 and not any(result['known_target_mask'])

def test_unknown_initial_state_is_explicitly_unverified():
    m=module();result=m.initial_pair_check([None,None],None)
    assert result['pair_verified'] is False and result['parent_verified'] is False and result['reason']=='initial_state_unknown'

def test_initial_pair_or_parent_power_drift_is_not_ignored():
    m=module();a={'state':{'gt_prev':[1,2,3],'extra_faulted':[]},'executed':{'diesel':0}}
    b=copy.deepcopy(a);b['executed']['diesel']=1
    with pytest.raises(ValueError):m.initial_pair_check([a,b],a)
    with pytest.raises(ValueError):m.initial_pair_check([a,a],b)

def test_runner_counts_all_192_attempts_and_retains_unknown_masks(tmp_path,monkeypatch):
    m=module();parent,reg=inputs();monkeypatch.setattr(m,'build_manifest',fake_build)
    profiles=m.derive_profile_manifests(parent,reg)
    initial={'state':{'gt_prev':[1,2,3],'extra_faulted':[]},'executed':{'diesel':0}}
    plan={'signature':'synthetic','registered_manifest_profiles':profiles,'rank_model_directory':str(tmp_path/'MODEL'),
          'baseline_initial_states':{s['id']:initial for s in parent['scenarios']},'maximum_actual_episodes':192}
    monkeypatch.setattr(m,'verify_bound_plan',lambda p:(plan,parent))
    monkeypatch.setattr(m,'HospitalIESEnv',lambda root,execution_contract:SimpleNamespace(execution_contract=execution_contract))
    monkeypatch.setattr(m,'make_policy',lambda recipe_id,**kw:SimpleNamespace(policy_id=recipe_id))
    attempts=[]
    def episode(env,scenario,policy,seed,manifest,**kw):
        attempts.append((scenario['id'],policy.policy_id,seed,env.execution_contract['name']))
        unknown=scenario['id']=='s00'
        return {'scenario':scenario,'seed':seed,'initial_state':copy.deepcopy(initial),'solver_calls':[],'policy_diagnostics':[],
            'summary':{'status':'model_constraint_infeasible' if unknown else 'completed','observed_until_minute':276 if unknown else 600},
            'solver_errors':[{'category':'model_constraint_infeasible'}] if unknown else [],'segments':[]}
    monkeypatch.setattr(m,'run_policy_episode',episode)
    def window(payload,start,horizon,env):
        unknown=payload['summary']['status']!='completed'
        return {'targets':[1.]+[None]*18 if unknown else [0.]*19,'target_mask':[True]+[False]*18 if unknown else [True]*19,'full_horizon':not unknown}
    monkeypatch.setattr(m,'window_targets',window)
    report=m.run_plan(tmp_path/'PLAN.json',tmp_path/'ACTUAL')
    assert report['actual_episodes']==192 and len(attempts)==192
    assert report['status_counts']=={'model_constraint_infeasible':8,'completed':184}
    assert len(report['pairs'])==96 and len(report['scores'])==192
    assert len(list((tmp_path/'ACTUAL').rglob('payload_*.json')))==192
    unknown=[x for x in report['scores'] if x['status']=='model_constraint_infeasible']
    assert all(x['primary_score']['targets'][1:]==[None]*18 for x in unknown)
    assert all(x['primary_score']['targets'][0]==1 for x in unknown)
    assert all(x[2]==101 for x in attempts)
    with pytest.raises(FileExistsError):m.run_plan(tmp_path/'PLAN.json',tmp_path/'ACTUAL')
    assert len(attempts)==192
