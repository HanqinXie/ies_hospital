"""Pure/synthetic offline learning tests; no hospital simulations."""
import numpy as np
import pytest
import csv
import io
import json
import hashlib
import copy
import gzip

from tools.p2b_learning.regression import (
    candidate_registry, regression_gate, sample_measure, regression_metrics,
    assert_group_isolation, fit_candidate, serialize_model,
)


def rows(n=120,role='pilot_train',groups=24):
    return [dict(scenario_id=f'{role}_{i}',policy_id='priority_milp',study_stratum='L2_mixed',experiment_role=role,group_id=f'{role}_g{i%groups}',root_family_id=f'{role}_root{i}',score_outcome_valid=True,score_P0_failure=False,cost_USD=float(i)) for i in range(n)]


def test_fixed_candidate_budget_and_failed_high_order_math_gate():
    registry=candidate_registry()
    assert len(registry)==14
    assert len({r['class'] for r in registry})==4
    assert len({r['config_id'] for r in registry})==14
    assert next(r for r in registry if r['config_id']=='DSPCE_p3')['eligible'] is False


def test_group_leakage_rejected_even_if_scenario_ids_differ():
    train=rows();valid=rows(30,'pilot_validation',6)
    assert_group_isolation(train,valid)
    valid[0]['group_id']=train[0]['group_id']
    with pytest.raises(ValueError,match='group'):assert_group_isolation(train,valid)


def test_regression_gate_counts_unknowns_and_constant_targets():
    train=rows();valid=rows(30,'pilot_validation',6)
    train[0]['score_outcome_valid']=False
    gate=regression_gate(train,valid,'cost_USD',basis_count=20)
    assert gate['eligible'] and gate['train_valid']==119 and gate['train_unknown']==1
    for row in train:row['cost_USD']=0.
    gate=regression_gate(train,valid,'cost_USD',basis_count=20)
    assert not gate['eligible'] and 'constant_training_target' in gate['reasons']


def test_polynomial_gate_requires_three_times_basis_count():
    gate=regression_gate(rows(60),rows(30,'pilot_validation',6),'cost_USD',basis_count=21)
    assert not gate['eligible'] and 'less_than_three_times_basis_count' in gate['reasons']


def test_polynomial_rank_gate_uses_actual_lstsq_cutoff():
    rng=np.random.default_rng(927)
    x=rng.normal(size=(120,3));x[:,1]=x[:,0]+1e-6*rng.normal(size=120)
    y=1+x[:,0]+x[:,2]**2;basis=rng.normal(size=(4096,3))
    config=next(c for c in candidate_registry() if c['config_id']=='DNIPC_p2')
    with pytest.raises(ValueError,match='rank'):fit_candidate(config,3201,x,y,basis,{'synthetic':True})


def test_measure_sampling_uses_registered_discrete_prior():
    measure={'shock_e':{'family':'uniform','bounds':[0.,1.8]},'shock_c':{'family':'uniform','bounds':[0.,1.4]},'calendar_hour':{'family':'discrete_uniform','values':[7,103,247],'probability_per_value':1/3}}
    x=sample_measure(measure,4096,3201)
    assert set(x[:,2])=={7,103,247}
    assert np.all((x[:,:2]>=0)&(x[:,:2]<=[1.8,1.4]))
    assert np.array_equal(x,sample_measure(measure,4096,3201))


def test_negative_predictions_and_group_bootstrap_are_not_hidden():
    y=np.array([0.,0.,1.,3.,5.,9.]);p=np.array([-1.,.1,.5,2.,4.,8.]);g=np.array(['a','a','b','b','c','c'])
    result=regression_metrics(y,p,train_y=np.arange(10.),groups=g,observed_failure=np.array([False,False,False,True,True,True]),bootstrap_repeats=20)
    assert result['negative_prediction_fraction']==pytest.approx(1/6)
    assert result['max_underprediction']==1.
    assert result['bootstrap']['unit']=='connected_group'
    assert result['boundary_by_observed_failure']['failure']['N']==3


def test_real_csv_boolean_and_integer_failure_values_keep_unknown_separate():
    from tools.p2b_learning.regression import parse_bool
    data=list(csv.DictReader(io.StringIO('score_outcome_valid,score_P0_failure\nTrue,1\nTrue,0\nFalse,\n')))
    assert [parse_bool(r['score_P0_failure']) for r in data]==[True,False,None]
    assert [parse_bool(r['score_outcome_valid']) for r in data]==[True,True,False]
    with pytest.raises(ValueError):parse_bool('2')


def audit_fixture(tmp_path):
    from tools.p2b_learning.regression import canonical_hash
    scenarios=[{'id':f's{i}','experiment_role':'pilot_train' if i<320 else 'pilot_validation','study_stratum':'L2_mixed','root_family_id':f'r{i}'} for i in range(400)]
    manifest={'scenarios':scenarios,'policies':['priority_milp','secure_milp'],'splits':{s['id']:{'group_id':s['root_family_id']} for s in scenarios},'p2b_study_distribution':{'strata':{}}}
    manifest['signature']=canonical_hash(manifest)
    rows=[{'scenario_id':s['id'],'policy_id':p,'experiment_role':s['experiment_role'],'study_stratum':s['study_stratum'],'group_id':s['root_family_id'],'root_family_id':s['root_family_id'],'score_outcome_valid':'True','score_P0_failure':'0'} for s in scenarios for p in manifest['policies']]
    csvpath=tmp_path/'scores.csv'
    with csvpath.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    audit={'passed':True,'complete_budget':True,'manifest_signature':manifest['signature'],'attempted_trajectories':800,'checkpoints':800,'attempted_specifications':400,'complete_pairs':400,'scores_sha256':hashlib.sha256(csvpath.read_bytes()).hexdigest(),'score_rows_canonical_sha256':canonical_hash(rows)}
    path=tmp_path/'audit.json';path.write_text(json.dumps(audit))
    receipt={'passed':True,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'scores_path':str(csvpath),'scores_sha256':audit['scores_sha256'],'manifest_signature':manifest['signature']}
    return manifest,rows,receipt


def test_final_preflight_binds_audit_manifest_scores_and_complete_pairs(tmp_path):
    from tools.p2b_learning.regression import learning_preflight
    manifest,rows,receipt=audit_fixture(tmp_path)
    learning_preflight(manifest,rows,manifest['p2b_study_distribution'],receipt)
    bad=[dict(r) for r in rows];bad[0]['group_id']='fabricated'
    with pytest.raises(ValueError):learning_preflight(manifest,bad,manifest['p2b_study_distribution'],receipt)
    with pytest.raises(ValueError):learning_preflight(manifest,rows[:-1],manifest['p2b_study_distribution'],receipt)
    with pytest.raises(ValueError):learning_preflight(manifest,rows,{'strata':{'different':{}}},receipt)
    fake=tmp_path/'fake.json';fake.write_text(json.dumps({'passed':True}))
    receipt['path']=str(fake);receipt['sha256']=hashlib.sha256(fake.read_bytes()).hexdigest()
    with pytest.raises(ValueError):learning_preflight(manifest,rows,manifest['p2b_study_distribution'],receipt)


def test_synthetic_full_regression_pipeline_has_no_simulator_calls(tmp_path):
    from tools.p2b_learning.regression import canonical_hash,run_regression,TARGETS
    manifest,rows,receipt=audit_fixture(tmp_path)
    rng=np.random.default_rng(3201)
    for i,s in enumerate(manifest['scenarios']):
        s['experiment_role']='pilot_train' if i<120 else 'pilot_validation' if i<150 else 'development_boundary'
        s['study_stratum']='L2_mixed' if i<150 else None
        s['offline_inputs']=[float(rng.random()),float(rng.random()),float((i//5)*24+7)]
        manifest['splits'][s['id']]['group_id']=f'g{i//5}'
    for r in rows:
        s=manifest['scenarios'][int(r['scenario_id'][1:])]
        r.update(experiment_role=s['experiment_role'],study_stratum=s['study_stratum'] or '',group_id=manifest['splits'][s['id']]['group_id'])
        for target in TARGETS:r[target]='0.0'
        r['cost_USD']=str(10+2*s['offline_inputs'][0]+.2*s['offline_inputs'][1]+.001*s['offline_inputs'][2])
    measure={'shock_e':{'family':'uniform','bounds':[0.,1.]},'shock_c':{'family':'uniform','bounds':[0.,1.]},'calendar_hour':{'family':'discrete_uniform','values':[float(i*24+7) for i in range(30)],'probability_per_value':1/30}}
    distribution={'strata':{'L2_mixed':{'basis_measure':measure,'validation_measure':measure,'input_order':['shock_e','shock_c','calendar_hour'],'input_units':['relative_increment','relative_increment','one_based_hour'],'design_distribution':'synthetic software fixture'}}}
    manifest['p2b_study_distribution']=distribution;manifest.pop('signature');manifest['signature']=canonical_hash(manifest)
    csvpath=tmp_path/'scores.csv'
    with csvpath.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    audit=json.loads((tmp_path/'audit.json').read_text());audit.update(manifest_signature=manifest['signature'],scores_sha256=hashlib.sha256(csvpath.read_bytes()).hexdigest(),score_rows_canonical_sha256=canonical_hash(rows))
    (tmp_path/'audit.json').write_text(json.dumps(audit));receipt.update(sha256=hashlib.sha256((tmp_path/'audit.json').read_bytes()).hexdigest(),scores_sha256=audit['scores_sha256'],manifest_signature=manifest['signature'])
    result=run_regression(manifest,rows,distribution,tmp_path/'synthetic_learning',receipt)
    assert result['no_hospital_environment_calls']
    assert sum(r['status']=='fitted' for r in result['results'])==2
    assert all('complete-score subset' in s['error_measure_interpretation'] for s in result['selections'])


@pytest.mark.parametrize('class_name',['DNIPC','DSPCE','RF'])
def test_synthetic_candidate_fit_and_reload(tmp_path,class_name):
    rng=np.random.default_rng(3201);x=rng.normal(size=(120,3));y=2+x[:,0]-.3*x[:,1]+.1*x[:,2]**2
    basis=rng.normal(size=(4096,3));config=next(r for r in candidate_registry() if r['class']==class_name and r['task']=='regression' and r['eligible'])
    model,diag=fit_candidate(config,3201,x,y,basis,{'synthetic':True})
    restored=serialize_model(model,config,tmp_path/'model',{'fit_dataset_ids':['synthetic_only'],'data_sha256':'abc'})
    assert np.allclose(model.predict(x),restored.predict(x),rtol=0,atol=1e-12)
    assert diag['fit_seconds']>=0


def decision(t,value,label):
    return {'decision_time_min':t,'features':{'observed':value,'state':'ON'},'feature_metadata':{'observed':{'available_time_min':t},'state':{'available_time_min':t}},'labels':{'60':{'valid':True,'at_risk':True,'value':label}},'first_failure_observation_interval':[90.,105.] if label else None,'post_outcome':{'secret':999}}


def test_risk_sequences_use_only_past_observations_and_no_post_fields():
    from tools.p2b_learning.risk import build_sequences
    spec={'id':'s','experiment_role':'pilot_train','root_family_id':'r','score_interval_minutes':[60,420]}
    payload={'scenario':spec,'policy_id':'priority_milp','summary':{'status':'completed','p0_failed_any':1},'decisions':[decision(t,t,1) for t in [0,15,30,45,60,75]],'segments':[]}
    data=build_sequences([payload],{'s':{'group_id':'g'}},'priority_milp')
    assert len(data)==2
    assert [r['observed'] for r in data[0]['sequence']]==[15,30,45,60]
    assert 'post_outcome' not in data[0]['sequence'][0]
    payload['decisions'][2]['feature_metadata']['observed']['available_time_min']=46
    with pytest.raises(ValueError,match='Future'):build_sequences([payload],{'s':{'group_id':'g'}},'priority_milp')


def test_risk_gate_cannot_count_same_positive_family_as_negative_accident():
    from tools.p2b_learning.risk import risk_gate
    data=[]
    for role in ['pilot_train','pilot_validation']:
        for family in range(12):
            for label in [0,1]:data.append({'scenario_id':f'{role}_{family}','policy_id':'priority_milp','decision_time_min':float(label*15),'role':role,'root_family_id':f'{role}_{family}','group_id':f'{role}_g{family}','label':label,'trajectory_known_negative':False})
    gate=risk_gate(data)
    assert not gate['eligible']
    assert gate['roles']['pilot_train']['negative_only_known_root_families']==0


def test_risk_train_only_transform_and_same_sequence_information():
    from tools.p2b_learning.risk import SequencePreprocessor
    records=[{'sequence':[{'a':1.,'category':'A'}]*4,'padding_mask':[1,1,1,1]}, {'sequence':[{'a':3.,'category':'B'}]*4,'padding_mask':[1,1,1,1]}]
    proc=SequencePreprocessor().fit(records)
    x=proc.transform(records)
    other=[{'sequence':[{'a':100.,'category':'UNSEEN'}]*4,'padding_mask':[1,1,1,1]}]
    assert proc.to_dict()['numeric']['a']['mean']==2.
    assert 'UNSEEN' not in proc.to_dict()['categorical']['category']
    assert proc.transform(other).shape[1:]==x.shape[1:]
    assert x.reshape(len(x),-1).size==x.size


def test_threshold_uses_validation_recall_rule_and_reports_family_times():
    from tools.p2b_learning.risk import choose_threshold,risk_metrics
    y=np.array([0,0,1,1]);p=np.array([.1,.3,.8,.9])
    threshold=choose_threshold(y,p)
    assert threshold['recall']>=.9 and threshold['precision']==1.
    rows=[{'scenario_id':f's{i}','root_family_id':f'r{i}','decision_time_min':60.,'first_interval':[75.,90.] if y[i] else None,'known_abnormal_at_decision':False} for i in range(4)]
    metrics=risk_metrics(y,p,threshold['threshold'],rows)
    assert metrics['strict_early_detected_families']==2
    assert metrics['same_failure_interval_detected_families']==0


@pytest.mark.parametrize('kind',['RF','GRU'])
def test_risk_synthetic_fit_and_weight_reload(tmp_path,kind):
    from tools.p2b_learning.risk import fit_risk_candidate
    rng=np.random.default_rng(3201);x=rng.normal(size=(64,4,5)).astype(np.float32);vx=rng.normal(size=(24,4,5)).astype(np.float32)
    y=(x[:,-1,0]>0).astype(int);vy=(vx[:,-1,0]>0).astype(int)
    config=dict(next(c for c in candidate_registry() if c['class']==kind and c['task']=='risk'))
    if kind=='GRU':config['epochs']=2  # synthetic software check, never a hospital trial
    p,diag=fit_risk_candidate(config,3201,x,y,vx,vy,tmp_path/'model')
    assert p.shape==(24,) and np.all((p>=0)&(p<=1))
    assert diag['serialization_max_abs_error']<=1e-12
    assert len(diag['training_history'])==(2 if kind=='GRU' else 0)
    assert diag['fit_device']==('cuda:0' if kind=='GRU' else 'cpu')


@pytest.mark.parametrize('misplaced', ['model', 'train_x', 'train_y', 'validation_x', 'validation_y'])
def test_risk_gru_rejects_cpu_model_or_fit_tensor(tmp_path,monkeypatch,misplaced):
    import torch
    from tools.p2b_learning import risk

    assert torch.cuda.is_available(), 'CUDA is required for the registered GRU device test'
    x=np.zeros((8,4,2),dtype=np.float32);y=np.arange(8)%2
    vx=np.ones((4,4,2),dtype=np.float32);vy=np.arange(4)%2
    if misplaced=='model':
        original_factory=risk.make_gru
        def cpu_model(*args):
            model=original_factory(*args)
            model.to=lambda device:model
            return model
        monkeypatch.setattr(risk,'make_gru',cpu_model)
    else:
        target={'train_x':x,'train_y':y,'validation_x':vx,'validation_y':vy}[misplaced]
        original_tensor=torch.tensor
        def cpu_tensor(*args,**kwargs):
            if args and args[0] is target:kwargs['device']='cpu'
            return original_tensor(*args,**kwargs)
        monkeypatch.setattr(torch,'tensor',cpu_tensor)
    config={'class':'GRU','hidden_size':2,'epochs':1,'patience':1}
    with pytest.raises(RuntimeError,match='risk GRU.*cuda:0'):
        risk.fit_risk_candidate(config,4101,x,y,vx,vy,tmp_path/'risk')


@pytest.mark.parametrize('kind,device',[('RF','cpu'),('GRU','cuda:0')])
def test_risk_run_records_fit_and_requested_devices_in_provenance(tmp_path,monkeypatch,kind,device):
    from tools.p2b_learning import risk
    records=[{'role':role,'scenario_id':role+str(i),'decision_time_min':60,
              'sequence':[{'load':float(i)} for _ in range(4)],'padding_mask':[1]*4,'label':i%2}
             for role in ('pilot_train','pilot_validation') for i in range(2)]
    monkeypatch.setattr(risk,'learning_preflight',lambda *args:{})
    monkeypatch.setattr(risk,'risk_payload_preflight',lambda *args:{'verified_digest_list_sha256':'synthetic'})
    monkeypatch.setattr(risk,'build_fit_provenance',lambda *args:{'synthetic':True})
    monkeypatch.setattr(risk,'build_sequences',lambda *args:records)
    monkeypatch.setattr(risk,'risk_gate',lambda *args:{'eligible':True})
    monkeypatch.setattr(risk,'candidate_registry',lambda:[{'config_id':'synthetic_'+kind,'task':'risk','class':kind}])
    monkeypatch.setattr(risk,'SEEDS',(4101,))
    monkeypatch.setattr(risk,'choose_threshold',lambda *args:{'threshold':.5})
    monkeypatch.setattr(risk,'risk_metrics',lambda *args:{'PR_AUC_average_precision':.5})
    def synthetic_fit(config,seed,x,y,vx,vy,directory):
        directory.mkdir()
        (directory/'state_dict.pt').write_bytes(b'synthetic weight marker')
        return np.array([.1,.9]),{'fit_device':device,'model_validation_prediction_seconds':0.}
    monkeypatch.setattr(risk,'fit_risk_candidate',synthetic_fit)
    monkeypatch.setattr(risk,'reload_risk_probabilities',lambda directory,rows:np.array([.1,.9]))
    output=tmp_path/'run'
    result=risk.run_risk({'signature':'synthetic','policies':['priority_milp'],'splits':{}},[],{},[],output,{})
    trial=result['reports'][0]['trials'][0]
    assert trial['status']=='completed'
    provenance=json.loads((output/'priority_milp'/f'synthetic_{kind}__seed4101'/'provenance.json').read_text())
    assert provenance['training_device']==device
    assert provenance['requested_device']==('cuda:0' if kind=='GRU' else 'cpu')
    assert provenance['files'][0]['sha256']==hashlib.sha256(b'synthetic weight marker').hexdigest()


def risk_checkpoint_fixture(tmp_path):
    from tools.p2b_learning.regression import canonical_hash
    manifest,rows,receipt=audit_fixture(tmp_path)
    manifest['options']={'seed':20261012};manifest.pop('signature');manifest['signature']=canonical_hash(manifest)
    payloads=[];digests=[]
    folder=tmp_path/'checkpoints';folder.mkdir()
    for s in manifest['scenarios']:
        for policy in manifest['policies']:
            identity={'scenario_id':s['id'],'policy_id':policy,'continuation_policy_id':policy,'group_id':manifest['splits'][s['id']]['group_id'],'manifest_signature':manifest['signature']}
            payload={'scenario':s,'identity':identity,'summary':{'scenario_id':s['id'],'policy_id':policy,'status':'completed','p0_failed_any':0},'seed':20261012,'decisions':[],'segments':[]}
            canonical=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
            checkpoint=folder/f'{s["id"]}_{policy}.json.gz';checkpoint.write_bytes(gzip.compress(json.dumps({'payload':payload}).encode('utf-8'),mtime=0))
            digests.append({'scenario_id':s['id'],'policy_id':policy,'path':str(checkpoint),'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),'payload_sha256':hashlib.sha256(canonical).hexdigest()})
            payloads.append(payload)
    audit=json.loads((tmp_path/'audit.json').read_text());audit.update(manifest_signature=manifest['signature'],checkpoint_digests=digests)
    (tmp_path/'audit.json').write_text(json.dumps(audit));receipt.update(sha256=hashlib.sha256((tmp_path/'audit.json').read_bytes()).hexdigest(),manifest_signature=manifest['signature'],checkpoint_root=str(tmp_path))
    return manifest,rows,receipt,payloads


def test_risk_payloads_are_bound_before_output_or_fit(tmp_path):
    from tools.p2b_learning.risk import run_risk
    manifest,rows,receipt,payloads=risk_checkpoint_fixture(tmp_path)
    for name,changed in [('duplicate',payloads+[payloads[0]]),('missing',payloads[:-1])]:
        target=tmp_path/name
        with pytest.raises(ValueError):run_risk(manifest,rows,manifest['p2b_study_distribution'],changed,target,receipt)
        assert not target.exists()
    changed=copy.deepcopy(payloads);changed[0]['scenario']['root_family_id']='forged'
    with pytest.raises(ValueError):run_risk(manifest,rows,manifest['p2b_study_distribution'],changed,tmp_path/'mutated',receipt)
    assert not (tmp_path/'mutated').exists()


def test_risk_checkpoint_byte_and_payload_digest_binding(tmp_path):
    from tools.p2b_learning.risk import risk_payload_preflight
    manifest,rows,receipt,payloads=risk_checkpoint_fixture(tmp_path)
    evidence=risk_payload_preflight(manifest,payloads,receipt)
    assert evidence['verified_pilot_payloads']==800
    altered=copy.deepcopy(payloads);altered[0]['decisions']=[dict(decision(60,999,1),**altered[0]['identity'])]
    with pytest.raises(ValueError,match='payload'):risk_payload_preflight(manifest,altered,receipt)
    path=next((tmp_path/'checkpoints').glob('*.gz'));path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='checkpoint'):risk_payload_preflight(manifest,payloads,receipt)


def test_risk_duplicate_sequences_and_rows_cannot_inflate_gate():
    from tools.p2b_learning.risk import build_sequences,risk_gate
    spec={'id':'s','experiment_role':'pilot_train','root_family_id':'r','score_interval_minutes':[60,420]}
    payload={'scenario':spec,'policy_id':'priority_milp','summary':{'status':'completed','p0_failed_any':0},'decisions':[decision(60,1,0)],'segments':[]}
    with pytest.raises(ValueError,match='duplicate'):build_sequences([payload,payload],{'s':{'group_id':'g'}},'priority_milp')
    rows=build_sequences([payload],{'s':{'group_id':'g'}},'priority_milp')
    with pytest.raises(ValueError,match='duplicate'):risk_gate(rows+rows)


@pytest.mark.parametrize('kind',['RF','GRU'])
def test_risk_raw_record_serialization_roundtrip_and_preprocessor_hash(tmp_path,kind):
    from tools.p2b_learning.risk import SequencePreprocessor,fit_risk_candidate,reload_risk_probabilities
    rng=np.random.default_rng(3201)
    def records(n):return [{'sequence':[{'numeric':float(v),'category':'A' if v>0 else 'B'} for v in rng.normal(size=4)],'padding_mask':[1,1,1,1]} for _ in range(n)]
    train=records(32);valid=records(12);proc=SequencePreprocessor().fit(train)
    x=proc.transform(train);vx=proc.transform(valid);y=np.array([i%2 for i in range(32)]);vy=np.array([i%2 for i in range(12)])
    config=dict(next(c for c in candidate_registry() if c['class']==kind and c['task']=='risk'))
    if kind=='GRU':config['epochs']=2
    folder=tmp_path/'model';pred,diag=fit_risk_candidate(config,3201,x,y,vx,vy,folder)
    assert diag['fit_device']==('cuda:0' if kind=='GRU' else 'cpu')
    preprocessor_path=tmp_path/'preprocessor.json';preprocessor_path.write_text(json.dumps(proc.to_dict()))
    provenance={'config':config,'input_size':x.shape[-1],'preprocessor_path':'../preprocessor.json','preprocessor_sha256':hashlib.sha256(preprocessor_path.read_bytes()).hexdigest(),'files':[{'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in folder.iterdir() if p.is_file()]}
    (folder/'provenance.json').write_text(json.dumps(provenance))
    assert np.max(np.abs(reload_risk_probabilities(folder,valid)-pred))<=1e-12
    preprocessor_path.write_text(preprocessor_path.read_text()+' ')
    with pytest.raises(ValueError,match='preprocessor'):reload_risk_probabilities(folder,valid)


def test_fit_provenance_verifies_original_math_and_explicit_correction():
    from tools.p2b_learning.provenance import build_fit_provenance
    provenance=build_fit_provenance(candidate_registry(),[3201,3202,3203])
    assert len(provenance['candidate_configurations'])==14
    assert provenance['mathematical_evidence']['verified_validation_artifacts']==60
    assert provenance['mathematical_evidence']['source_overrides_used']==['tools/p2b_methods/validate.py']
    paths={item['path'] for item in provenance['implementation_sources']}
    assert {'tools/p2b_methods/basis.py','tools/p2b_methods/dmorph.py','tools/p2b_learning/risk.py','tools/p2b_learning/regression.py'}<=paths


def test_math_source_drift_is_rejected_with_no_silent_override(tmp_path):
    from tools.p2b_learning.provenance import verify_source_entries
    path=tmp_path/'helper.py';path.write_text('original')
    entries=[{'path':'helper.py','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]
    verify_source_entries(tmp_path,entries,{})
    path.write_text('changed')
    with pytest.raises(ValueError,match='source'):verify_source_entries(tmp_path,entries,{})


def test_insufficient_risk_report_retains_exact_audit_receipt(tmp_path):
    from tools.p2b_learning.risk import run_risk
    manifest,rows,receipt,payloads=risk_checkpoint_fixture(tmp_path)
    folder=tmp_path/'risk_report'
    report=run_risk(manifest,rows,manifest['p2b_study_distribution'],payloads,folder,receipt)
    assert all(item['status']=='insufficient_data' for item in report['reports'])
    assert report['audit_evidence']==receipt
    saved=json.loads((folder/'RISK_REPORT.json').read_text(encoding='utf-8'))
    assert saved['audit_evidence']==receipt
