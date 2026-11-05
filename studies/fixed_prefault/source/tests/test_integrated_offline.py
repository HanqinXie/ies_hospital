"""Synthetic contract checks; never fit from unbound hospital data."""
import copy,gzip,hashlib,importlib,importlib.util,json
from pathlib import Path
import numpy as np
import pytest

def offline():
    assert importlib.util.find_spec('tools.integrated_experiment.offline') is not None,'integrated offline comparison required'
    return importlib.import_module('tools.integrated_experiment.offline')

def payload(sid='synthetic',role='pilot_train',failure=135.,status='completed'):
    from hospital_ies.data_contract import first_failure_label
    spec=dict(id=sid,root_family_id='root_'+sid,experiment_role=role,time_block_id='block_'+sid,
        score_interval_minutes=[60,420],condition_id='grid250_delay45',grid_limit_kW=250.,response_delay_minutes=45.,
        faults={'EC_2':.5},shock={'e':.5,'h':.2,'c':.4},shock_by_priority={},shock_start_minute=75.,
        shock_duration_minutes=240.,fault_start_minute=45.,fault_duration_minutes=240.,start_hour=101,
        duration_steps=32,step_minutes=15,distribution_id='synthetic_only')
    observed=480. if status=='completed' else 120.
    segments=[]
    for start in np.arange(0.,observed,15.):
        end=start+15.;failed=bool(failure is not None and end>=failure)
        segments.append(dict(start_minute=float(start),end_minute=float(end),classification=dict(p0_guarantee_failure=failed),
            unserved_kW=[[.001 if start==60 else 1. if failed else 0.,0.,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]],
            violation=dict(P0=False,P1=False,P2=False,P3=False),temperature=dict(P0=24.,P1=25.,P2=26.,P3=None),
            cost_USD_step=1.,energy_balance_residuals=[0.,0.,0.],steam_balance_residual_lbph=0.))
    decisions=[]
    for t in np.arange(0.,min(observed,420.),15.):
        features=dict(grid_import_limit_kW=250.,control_available=bool(t>=120.),hour_of_year=101.,
            **{f'P{p}_{c}_demand_kW':float(10+p) for p in range(4) for c in ('e','h','c')})
        meta={k:dict(unit='kW',source='synthetic current observation',measurement='synthetic',available_time_min=float(t),valid_time_min=float(t)) for k in features}
        decisions.append(dict(decision_time_min=float(t),features=features,feature_metadata=meta,known_command_queue=[],
            labels={'60':first_failure_label(float(t),60.,failure if failure is not None and failure<=observed else None,observed,status)}))
    return dict(scenario=spec,identity=dict(policy_id='priority_milp',group_id='group_'+sid),policy_id='priority_milp',
        segments=segments,decisions=decisions,summary=dict(scenario_id=sid,policy_id='priority_milp',status=status,
            observed_until_minute=observed,full_trajectory_outcome_valid=status=='completed',p0_failed_any=int(failure is not None and failure<=observed) if status=='completed' else None),
        initial_state=dict(executed=dict(gt_electric_kW=[100.,100.,100.],grid_import_kW=250.,diesel_kW=0.,boiler_steam_lbph=100.),state=dict(thermal_delta={'zone':0.})))

def synthetic_score(p):
    # Explicit synthetic observational fee; real port/tariff formula has independent tests.
    from tools.p2b_experiment.audit import score_payload
    row=score_payload(p)
    row.update(actual_energy_fee_USD=4. if row['score_outcome_valid'] else None,
        observed_actual_energy_fee_USD=4.,energy_fee_outcome_valid=bool(row['score_outcome_valid']))
    return row

def test_sequences_keep_all_three_roles_and_formal_pre_warning_despite_tiny_raw():
    o=offline();ps=[payload('a'),payload('b','pilot_validation'),payload('c','development_boundary')]
    rows=o.build_risk_rows(ps,'priority_milp')
    assert {r['role'] for r in rows}=={'pilot_train','pilot_validation','development_boundary'}
    r=next(r for r in rows if r['scenario_id']=='a' and r['decision_time_min']==75.)
    assert r['label']==1 and r['known_abnormal_at_decision']
    assert r['formal_warning_eligible'] and r['first_interval']==[120.,135.]
    assert len(r['sequence'])==4 and r['padding_mask']==[1,1,1,1]
    assert not any('shock' in k or k.endswith('_id') or k=='stage' for k in r['sequence'][-1])

def test_unknown_negative_after_error_is_not_zero_filled_or_training_row():
    rows=offline().build_risk_rows([payload(status='solver_error',failure=None)],'priority_milp')
    assert [r['decision_time_min'] for r in rows]==[60.]
    assert rows[0]['label']==0 and not rows[0]['trajectory_known_negative']

def test_future_metadata_and_predictor_identifiers_are_rejected():
    o=offline();p=payload();p['decisions'][0]['feature_metadata']['control_available']['valid_time_min']=15.
    with pytest.raises(ValueError,match='Future'):o.build_risk_rows([p],'priority_milp')
    p=payload();p['decisions'][0]['features']['scenario_id']='leaking'
    p['decisions'][0]['feature_metadata']['scenario_id']=copy.deepcopy(p['decisions'][0]['feature_metadata']['control_available'])
    with pytest.raises(ValueError,match='predictor'):o.build_risk_rows([p],'priority_milp')

def test_duplicate_rows_and_cross_role_groups_cannot_inflate_training():
    o=offline();p=payload()
    with pytest.raises(ValueError,match='duplicate'):o.build_risk_rows([p,p],'priority_milp')
    a=payload('a');b=payload('b','development_boundary');b['identity']['group_id']=a['identity']['group_id']
    with pytest.raises(ValueError,match='isolation'):o.assert_role_isolation(o.build_risk_rows([a,b],'priority_milp'))

def test_static_rule_only_reads_current_declared_grid_control_demands():
    o=offline();r=o.build_risk_rows([payload()], 'priority_milp')[0]
    s=o.static_rows([r])[0]
    assert set(s['sequence'][-1])=={'grid_import_limit_kW','control_available'}|{f'P{p}_{c}_demand_kW' for p in range(4) for c in ('e','h','c')}
    assert 'hour_of_year' not in s['sequence'][-1]

def test_threshold_uses_only_validation_and_single_class_is_explicit_half():
    o=offline();r=o.threshold_from_validation([0,1],[.2,.8])
    assert r['threshold']==.8 and r['recall']==1.
    r=o.threshold_from_validation([0,0],[.2,.8])
    assert r['threshold']==.5 and r['status']=='fixed_half_single_class_diagnostic'

def test_metrics_keep_formal_early_and_no_tiny_early_parallel():
    o=offline();rows=o.build_risk_rows([payload()], 'priority_milp')
    probabilities=np.asarray([1. if r['decision_time_min']==75. else 0. for r in rows])
    m=o.risk_evaluation(rows,probabilities,.5)
    assert m['formal_before_interval_detected_scenarios']==1
    assert m['before_interval_without_tiny_detected_scenarios']==0
    assert m['positive_first_alerts'][0]['known_abnormal_at_alert']

def test_frozen_evaluation_has_no_effect_on_training_preprocessing():
    from tools.p2b_learning.risk import SequencePreprocessor
    o=offline();train=o.build_risk_rows([payload('a')],'priority_milp');frozen=o.build_risk_rows([payload('f','development_boundary')],'priority_milp')
    for row in frozen:
        for frame in row['sequence']:frame['grid_import_limit_kW']=1e9
    prep=SequencePreprocessor().fit(train)
    assert prep.numeric['grid_import_limit_kW']['mean']==250.
    assert o.training_roles(train+frozen)['pilot_train']==train

def test_consequence_units_masks_and_offline_only_schema():
    o=offline();ps=[payload('a'),payload('b',status='solver_error',failure=None)]
    scores={(p['scenario']['id'],'priority_milp'):synthetic_score(p) for p in ps}
    rows=o.build_consequence_rows(ps,scores,'priority_milp')
    assert len(rows)==2 and len(o.TARGETS)==16
    assert all(rows[0]['target_mask']) and not any(rows[1]['target_mask'])
    assert all(v is None for v in rows[1]['targets'])
    assert all(f['online_risk_allowed'] is False for f in o.consequence_feature_schema(rows[0]['features']))
    assert set(o.TARGET_UNITS.values())=={'kWh','USD','degC'}

def test_regression_metrics_keep_original_units_tail_and_constant_scale():
    o=offline();m=o.consequence_metrics([1.,10.],[2.,3.],[0.,10.],'kWh')
    assert m['MAE']==4. and m['max_underprediction']==7.
    assert m['unit']=='kWh' and m['negative_prediction_count']==0
    assert m['tail_train_q90']['N']==1
    c=o.consequence_metrics([1.],[2.],[1.,1.],'USD')
    assert c['normalized_RMSE'] is None and c['training_std']==0.

def test_candidate_registry_is_finite_and_target_accounting_explicit():
    o=offline();configs=o.registry()
    assert {c['task'] for c in configs}=={'risk','consequence'}
    assert len([c for c in configs if c['task']=='risk'])==4
    assert len([c for c in configs if c['task']=='consequence'])==2
    assert o.configuration_budget(configs)['configurations_including_consequence_targets']==36
    assert o.SEEDS==(4101,4102,4103)

def test_started_fit_and_error_are_append_only_and_before_actual_attempt(tmp_path):
    o=offline();ledger=tmp_path/'attempts.jsonl';seen=[]
    def fail():
        lines=[json.loads(x) for x in ledger.read_text(encoding='utf-8').splitlines()]
        assert lines[-1]['event']=='started';seen.append(1);raise RuntimeError('forced offline attempt')
    result=o.logged_fit(ledger,dict(config_id='synthetic',policy='priority_milp',seed=4101,target_names=['P0_e_ENS_kWh']),fail)
    assert seen and result['status']=='failed'
    lines=[json.loads(x) for x in ledger.read_text(encoding='utf-8').splitlines()]
    assert [x['event'] for x in lines]==['started','failed'] and lines[-1]['error_type']=='RuntimeError'
    assert 'forced offline attempt' in lines[-1]['traceback']

def test_real_constant_and_shallow_model_reload_from_saved_raw_inputs(tmp_path):
    o=offline();rows=o.build_risk_rows([payload('a'),payload('b',failure=None)],'priority_milp')
    from tools.p2b_learning.risk import SequencePreprocessor
    static=o.static_rows(rows);prep=SequencePreprocessor().fit(static);x=prep.transform(static);y=np.array([r['label'] for r in rows])
    for algorithm in ('constant','shallow'):
        folder=tmp_path/algorithm
        pred,diag=o.fit_risk_model(algorithm,4101,x,y,x,y,folder)
        reload=o.reload_model_probabilities(folder,x)
        assert np.array_equal(pred,reload) and diag['reload_max_abs_error']==0.
    single=np.zeros_like(y)
    folder=tmp_path/'requested_gru_single_class'
    pred,diag=o.fit_risk_model('GRU',4101,x,single,x,single,folder)
    descriptor=json.loads((folder/'model_descriptor.json').read_text(encoding='utf-8'))
    assert np.array_equal(pred,np.zeros(len(x)))
    assert diag['requested_device']=='cuda:0' and diag['actual_algorithm']=='constant'
    assert diag['fit_device']==descriptor['training_device']=='cpu'

def test_loading_requires_bound_current_audit_before_any_fit(tmp_path,monkeypatch):
    o=offline();m=dict(integrated_cohort='original_replay')
    mp=tmp_path/'manifest.json';mp.write_text(json.dumps(m),encoding='utf-8')
    with pytest.raises(ValueError,match='new_development'):o.load_bound_cohorts([dict(manifest=mp,audit=tmp_path/'missing.json')])
    with pytest.raises(ValueError,match='audited'):o.run_offline(dict(payloads=[]),tmp_path/'fit')

def bound_fixture(tmp_path,monkeypatch):
    o=offline();from tools.integrated_experiment import runner
    monkeypatch.setattr(runner,'verify_manifest',lambda m:None) # source-release gate has separate real tests
    monkeypatch.setattr(o,'_load_background',lambda:None)
    monkeypatch.setattr(o,'_background_features',lambda spec,table:dict(background_mean_kW=10.))
    monkeypatch.setattr(o,'_load_energy_context',lambda background:None)
    monkeypatch.setattr(o,'_energy_window',lambda p,context:{k:synthetic_score(p)[k] for k in ('actual_energy_fee_USD','observed_actual_energy_fee_USD','energy_fee_outcome_valid')})
    p=payload();s=p['scenario'];policy='priority_milp';assignment=dict(group_id=p['identity']['group_id'],experiment_role=s['experiment_role'])
    m=dict(integrated_cohort='new_development',signature='synthetic_release',simulator_hash='synthetic_core',scenarios=[s],policies=[policy],splits={s['id']:assignment},options=dict(seed=4101),
        execution_contract_id='synthetic_execution',assumption_set_id='synthetic_assumptions',schemas=dict(feature='synthetic'))
    p['seed']=4101;p['identity'].update(assignment);p['identity'].update({k:m[k] for k in ('execution_contract_id','simulator_hash','assumption_set_id','schemas')});p['identity']['manifest_signature']=m['signature']
    mp=tmp_path/'manifest.json';mp.write_text(json.dumps(m),encoding='utf-8')
    raw=gzip.compress(json.dumps(dict(scenario_id=s['id'],policy_id=policy,manifest_signature=m['signature'],payload=p)).encode('utf-8'))
    cp=tmp_path/'checkpoint.json.gz';cp.write_bytes(raw)
    row=dict(scenario_id=s['id'],policy_id=policy,experiment_role=s['experiment_role'],group_id=assignment['group_id'],**synthetic_score(p))
    sp=tmp_path/'trajectory_scores.json';sp.write_text(json.dumps([row]),encoding='utf-8')
    a=dict(passed=True,complete_budget=True,manifest_signature=m['signature'],manifest_sha256=o.sha(mp),attempted_trajectories=1,attempted_specifications=1,
        output_sha256={sp.name:o.sha(sp)},audit_source_sha256=o.sha(Path(o.__file__).with_name('data_audit.py')),
        energy_metrics_source_sha256=o.sha(Path(o.__file__).with_name('energy_metrics.py')),
        formal_warning_source_sha256=o.sha(Path(o.__file__).with_name('firstformal_warning.py')),formal_warning_view_version='integrated.first_formal_warning.v1',
        checkpoint_digests=[dict(scenario_id=s['id'],policy_id=policy,path=cp.name,checkpoint_sha256=o.sha(cp),payload_sha256=o.canonical(p))])
    ap=tmp_path/'audit.json';ap.write_text(json.dumps(a),encoding='utf-8');return o,mp,ap,cp,p

def test_actual_gzip_payload_score_binding_and_current_audit_source(tmp_path,monkeypatch):
    o,mp,ap,cp,p=bound_fixture(tmp_path,monkeypatch)
    c=o.load_bound_cohorts([dict(manifest=mp,audit=ap)])
    assert c['audited'] and c['payloads'][0]['_binding']['payload_sha256']==o.canonical(p)
    a=json.loads(ap.read_text(encoding='utf-8'));a['audit_source_sha256']='stale';ap.write_text(json.dumps(a),encoding='utf-8')
    with pytest.raises(ValueError,match='audit implementation'):o.load_bound_cohorts([dict(manifest=mp,audit=ap)])

def test_post_audit_checkpoint_byte_change_prevents_loading(tmp_path,monkeypatch):
    o,mp,ap,cp,p=bound_fixture(tmp_path,monkeypatch);cp.write_bytes(cp.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='Checkpoint bytes'):o.load_bound_cohorts([dict(manifest=mp,audit=ap)])

def test_consequence_saved_preprocessor_and_raw_input_reload(tmp_path):
    o=offline();from tools.p2b_learning.risk import SequencePreprocessor
    ps=[payload('a'),payload('b',failure=None)]
    rows=o.build_consequence_rows(ps,{(p['scenario']['id'],'priority_milp'):synthetic_score(p) for p in ps},'priority_milp')
    prep=SequencePreprocessor().fit(o._feature_records(rows));x=prep.transform(o._feature_records(rows))[:,-1,:];y=np.array([r['targets'] for r in rows])
    pp=tmp_path/'prep.json';pp.write_text(json.dumps(prep.to_dict()),encoding='utf-8');trial=tmp_path/'model'
    o.fit_consequence_model('RF',4101,x,y,trial)
    artifacts=[dict(path=p.name,sha256=o.sha(p)) for p in trial.iterdir() if p.is_file()]
    (trial/'provenance.json').write_text(json.dumps(dict(artifacts=artifacts,preprocessor_path='../prep.json',preprocessor_sha256=o.sha(pp))),encoding='utf-8')
    assert np.array_equal(o.reload_consequence(trial,x),o.reload_consequence_trial(trial,rows))
    pp.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='preprocessor'):o.reload_consequence_trial(trial,rows)

def test_risk_reload_compares_original_fitted_predictions_not_two_reloads(tmp_path,monkeypatch):
    o=offline();x=np.ones((6,4,2),dtype=np.float32);y=np.array([0,1,0,1,0,1])
    monkeypatch.setattr(o,'reload_model_probabilities',lambda directory,values:np.zeros(len(values)))
    with pytest.raises(ValueError,match='serialized'):o.fit_risk_model('constant',4101,x,y,x,y,tmp_path/'bad_reload')

def test_three_seed_selection_cannot_read_frozen_metrics():
    o=offline();trials=[]
    for config,score,frozen in [('risk_constant',.6,1.),('risk_static_depth2',.7,0.)]:
        for seed in o.SEEDS:
            trials.append(dict(config_id=config,seed=seed,status='completed',evaluation=dict(pilot_validation=dict(metrics=dict(PR_AUC_average_precision=score)),development_boundary=dict(metrics=dict(PR_AUC_average_precision=frozen)))))
    assert o.select_risk_configuration(trials)['selected']['config_id']=='risk_static_depth2'
    for t in trials:t['evaluation']['development_boundary']['metrics']['PR_AUC_average_precision']=1e9
    assert o.select_risk_configuration(trials)['selected']['config_id']=='risk_static_depth2'

def test_actual_RF_and_GRU_synthetic_fit_reload(tmp_path):
    o=offline();rng=np.random.default_rng(73);x=rng.normal(size=(12,4,3)).astype(np.float32);y=np.arange(12)%2;vx=x[:4];vy=y[:4]
    for algorithm in ('RF','GRU'):
        folder=tmp_path/algorithm;p,d=o.fit_risk_model(algorithm,4101,x,y,vx,vy,folder)
        assert p.shape==(4,) and np.isfinite(p).all()
        assert np.array_equal(p,o.reload_model_probabilities(folder,vx)) and d['reload_max_abs_error']==0.
        expected_device='cuda:0' if algorithm=='GRU' else 'cpu'
        descriptor=json.loads((folder/'model_descriptor.json').read_text(encoding='utf-8'))
        completed=json.loads((folder/'training_completed.json').read_text(encoding='utf-8'))
        assert d['fit_device']==descriptor['training_device']==completed['training_device']==expected_device

@pytest.mark.parametrize('kind',['features','scores'])
def test_compact_in_memory_values_cannot_change_after_genuine_binding(tmp_path,monkeypatch,kind):
    o,mp,ap,cp,p=bound_fixture(tmp_path,monkeypatch);c=o.load_bound_cohorts([dict(manifest=mp,audit=ap)])
    if kind=='features':c['payloads'][0]['decisions'][0]['features']['grid_import_limit_kW']=99999.
    else:c['scores'][('synthetic','priority_milp')]['cost_USD']=99999.
    with pytest.raises(ValueError,match='compact'):o._verify_bindings(c)

def test_different_ids_cannot_hide_cross_role_raw_support_overlap():
    o=offline();a=payload('a')['scenario'];b=payload('b','pilot_validation')['scenario'];b['start_hour']=a['start_hour']+1
    with pytest.raises(ValueError,match='raw support'):o.assert_support_isolation([a,b])
    b['start_hour']=a['start_hour']+20
    assert o.assert_support_isolation([a,b])['cross_role_overlaps']==0

def test_formal_failure_without_eligible_positive_window_has_separate_denominator():
    o=offline();p=payload('failed_before_score',failure=15.)
    population=o._risk_population([p],'priority_milp')
    assert population['attempted_trajectories']==1
    assert population['formal_failure_scenarios']==['failed_before_score']
    missed=population['first_formal_failures_without_eligible_positive_windows']
    assert missed[0]['scenario_id']=='failed_before_score' and missed[0]['reason']=='first_failure_at_or_before_score_start'
    assert not o.build_risk_rows([p],'priority_milp')

def test_consequence_RF_scales_each_target_from_training_before_joint_impurity(tmp_path):
    o=offline();rng=np.random.default_rng(13);x=rng.normal(size=(12,3));y=rng.normal(size=(12,16));y[:,12]*=1e6;y[:,15]=26.
    trial=tmp_path/'scaled';o.fit_consequence_model('RF',4101,x,y,trial)
    assert (trial/'target_scaler.json').exists(),'raw-unit joint impurity must not silently mix kWh, USD, degC'
    d=json.loads((trial/'target_scaler.json').read_text(encoding='utf-8'))
    assert d['fit_role']=='pilot_train' and np.allclose(d['mean'],y.mean(axis=0))
    assert np.allclose(d['std'],y.std(axis=0)) and d['scale'][-1]==1.
    assert d['targets']==list(o.TARGETS)
    p=o.reload_consequence(trial,x)
    assert p.shape==y.shape and np.all(p[:,-1]==26.)

def test_consequence_expense_target_is_actual_energy_fee_not_accounting_tie_break():
    o=offline();p=payload()
    from tools.p2b_experiment.audit import score_payload
    score=score_payload(p);score.update(actual_energy_fee_USD=4.,cost_USD=10.)
    row=o.build_consequence_rows([p],{('synthetic','priority_milp'):score},'priority_milp')[0]
    assert o.TARGETS[12]=='actual_energy_fee_USD'
    assert row['targets'][12]==4. and row['accounting_shadow_cost_USD']==10.
    assert row['accounting_shadow_is_energy_fee'] is False

def test_missing_actual_energy_fee_stays_unknown_despite_known_accounting_cost():
    o=offline();p=payload()
    from tools.p2b_experiment.audit import score_payload
    score=score_payload(p);score['cost_USD']=10.
    row=o.build_consequence_rows([p],{('synthetic','priority_milp'):score},'priority_milp')[0]
    assert row['targets'][12] is None and row['target_mask'][12] is False
    assert row['accounting_shadow_cost_USD']==10.

def test_fee_implementation_cannot_change_between_loading_and_fitting(tmp_path,monkeypatch):
    o,mp,ap,cp,p=bound_fixture(tmp_path,monkeypatch);c=o.load_bound_cohorts([dict(manifest=mp,audit=ap)])
    original_sha=o.sha
    monkeypatch.setattr(o,'sha',lambda path:'changed' if Path(path).name=='energy_metrics.py' else original_sha(path))
    with pytest.raises(ValueError,match='Bound'):o._verify_bindings(c)
