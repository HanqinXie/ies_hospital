"""Small synthetic action-conditioned proxy checks; no hospital simulation."""
import copy,importlib,importlib.util,json
from pathlib import Path
import numpy as np
import pytest

def proxy():
    assert importlib.util.find_spec('tools.integrated_experiment.action_proxy') is not None,'action proxy tool required'
    return importlib.import_module('tools.integrated_experiment.action_proxy')

def history():
    return [dict(decision_time_min=float(t),features={'grid_import_limit_kW':250.,'control_available':True},
        feature_metadata={k:dict(unit='kW' if k.startswith('grid') else 'dimensionless',source='synthetic current state',measurement='synthetic',available_time_min=float(t),valid_time_min=float(t)) for k in ('grid_import_limit_kW','control_available')}) for t in (45,60,75,90)]

def plan():
    return dict(command=dict(generated_minute=90.,dispatch=dict(gt_electric_kW=[100.,110.,120.],grid_import_kW=250.,diesel_kW=0.,absorption_cooling_kW=400.)))

def window(full=True):
    return dict(horizon_minutes=60,full_horizon=full,ENS_kWh_by_priority_carrier=[[1.,2.,3.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]] if full else None,
        P0_failure_in_window=1 if full else None,actual_energy_fee_USD=40. if full else None,observed_minutes=60. if full else 15.)

def identity(root='root1',role='train'):
    return dict(scenario_id='synthetic',root_family_id=root,group_id='group_'+root,source_role='pilot_train',proxy_role=role,snapshot_minute=90.,candidate_id='original_current_policy')

def test_root_split_is_pre_outcome_deterministic_and_duplicates_never_cross():
    p=proxy();roots=['root'+str(i) for i in range(20)];roles=p.split_roots(roots)
    assert sum(v=='train' for v in roles.values())==16 and sum(v=='internal_validation' for v in roles.values())==4
    assert roles==p.split_roots(list(reversed(roots))+roots[:2])

def test_baseline_unspecified_targets_remain_null_with_explicit_false_masks():
    p=proxy();row=p.build_row(history(),plan(),{'security':{'gt_loading_penalty_USD_per_kWh':.35}},window(),identity())
    last=row['sequence'][-1]
    for name in p.ACTION_NAMES:
        assert last['requested_'+name] is None and last['requested_'+name+'_specified'] is False
    assert last['known_baseline_projection_gt_1']==100.
    assert row['requested_targets']==[None]*6 and row['requested_target_mask']==[False]*6

def test_explicit_zero_request_is_distinct_from_unspecified_baseline():
    p=proxy();request=dict(security={'gt_loading_penalty_USD_per_kWh':.35},targets={'diesel':0.})
    row=p.build_row(history(),plan(),request,window(),identity());last=row['sequence'][-1]
    assert last['requested_diesel']==0. and last['requested_diesel_specified'] is True
    assert last['requested_gt_1'] is None and last['requested_gt_1_specified'] is False

def test_window_future_and_postprojection_diagnostics_cannot_be_predictors():
    p=proxy();w=window();row=p.build_row(history(),plan(),{'security':{'gt_loading_penalty_USD_per_kWh':.35}},w,identity())
    other=copy.deepcopy(w);other['actual_energy_fee_USD']=9999.;other['ENS_kWh_by_priority_carrier'][0]=[99.,99.,99.]
    changed=p.build_row(history(),plan(),{'security':{'gt_loading_penalty_USD_per_kWh':.35}},other,identity(),permitted_dispatch=plan()['command']['dispatch'])
    assert row['sequence']==changed['sequence'] and row['targets']!=changed['targets']
    assert not any('candidate_id' in k or 'future' in k or 'ENS' in k for k in row['sequence'][-1])

def test_future_information_projection_and_nonsecure_weights_rejected():
    p=proxy();h=history();h[-1]['feature_metadata']['control_available']['valid_time_min']=105.
    request={'security':{'gt_loading_penalty_USD_per_kWh':.35}}
    with pytest.raises(ValueError,match='future'):p.build_row(h,plan(),request,window(),identity())
    c=plan();c['command']['generated_minute']=105.
    with pytest.raises(ValueError,match='known'):p.build_row(history(),c,request,window(),identity())
    with pytest.raises(ValueError,match='secure'):p.build_row(history(),plan(),{'security':{'gt_loading_penalty_USD_per_kWh':0.}},window(),identity())

def test_frozen_source_role_is_prohibited_even_if_proxy_split_claims_train():
    p=proxy();i=identity();i['source_role']='development_boundary'
    with pytest.raises(ValueError,match='training-side'):p.build_row(history(),plan(),{'security':{'gt_loading_penalty_USD_per_kWh':.35}},window(),i)

def test_unknown_full_targets_not_zero_and_positive_prefix_failure_is_retained():
    p=proxy();w=window(False);w['P0_failure_in_window']=1
    r=p.build_row(history(),plan(),{'security':{'gt_loading_penalty_USD_per_kWh':.35}},w,identity())
    assert r['targets']==[None,None,None,1.,None] and r['target_mask']==[False,False,False,True,False]
    assert not r['fit_complete_five_targets']

def test_registry_reuses_recipes_but_counts_all_action_input_target_configs():
    p=proxy();r=p.registry()
    assert {c['recipe'] for c in r}=={'consequence_mean','consequence_RF'}
    assert sum(len(c['targets']) for c in r)==10 and len(p.TARGETS)==5 and p.SEEDS==(4101,4102,4103)

def test_mean_and_RF_actual_synthetic_fit_reload_and_train_only_scale(tmp_path):
    p=proxy();rng=np.random.default_rng(12);x=rng.normal(size=(12,3));y=rng.normal(size=(12,5));y[:,3]=np.arange(12)%2;y[:,4]*=1e5
    for algorithm in ('mean','RF'):
        directory=tmp_path/algorithm;pred,meta=p.fit_model(algorithm,4101,x,y,x,directory)
        assert np.array_equal(pred,p.reload_model(directory,x)) and meta['reload_max_abs_error']==0.
        if algorithm=='RF':
            scale=json.loads((directory/'target_scaler.json').read_text(encoding='utf-8'))
            assert np.allclose(scale['mean'],y.mean(axis=0)) and np.allclose(scale['std'],y.std(axis=0))
            assert scale['fit_role']=='train' and scale['targets']==list(p.TARGETS)

def test_fit_rejects_unbound_data_before_model_call(tmp_path):
    with pytest.raises(ValueError,match='bound'):proxy().run_action_proxy(dict(rows=[]),tmp_path/'fit')

def test_effective_action_diversity_does_not_claim_requests_were_executed():
    p=proxy();request={'security':{'gt_loading_penalty_USD_per_kWh':.35}}
    a=p.build_row(history(),plan(),request,window(),identity(),permitted_dispatch=plan()['command']['dispatch'],actual_dispatch=plan()['command']['dispatch'])
    request['targets']={'gt_1':90.};i=identity();i['candidate_id']='alternative'
    b=p.build_row(history(),plan(),request,window(),i,permitted_dispatch=plan()['command']['dispatch'],actual_dispatch=plan()['command']['dispatch'])
    d=p.action_diversity([a,b])
    assert d['snapshot_groups'][0]['requested_variants']==2
    assert d['snapshot_groups'][0]['permitted_variants']==1 and d['snapshot_groups'][0]['first_interval_actual_variants']==1
    assert d['request_projection_changed_rows']==1

def branch_fixture(tmp_path,monkeypatch):
    p=proxy();from types import SimpleNamespace
    import pandas as pd
    from tools.integrated_learning import run_control
    from tools.integrated_learning.truth import window_outcome
    plant=SimpleNamespace(gas_price_usd_per_mmbtu=5.,steam_kWh_per_lb=.3,boiler_eff=.9,
        tariff={'electricity_onpeak_per_kWh':.2,'electricity_midpeak_per_kWh':.15,'electricity_offpeak_per_kWh':.1})
    hourly=pd.DataFrame([dict(hour_of_year=1,timestamp=pd.Timestamp('2026-01-01 12:00:00'))])
    monkeypatch.setattr(p,'_load_energy_context',lambda:(plant,hourly))
    roots=['root1','root2'];roles=p.split_roots(roots);specs=[dict(id='case_'+r,root_family_id=r,experiment_role='pilot_train',start_hour=101+100*i,duration_steps=10,step_minutes=15) for i,r in enumerate(roots)]
    snapshots=[dict(scenario_id=s['id'],root_family_id=s['root_family_id'],role='pilot_train',snapshot_minute=90.,snapshot_step=6) for s in specs]
    control=dict(signature='synthetic_control',manifest_signature='synthetic_manifest',snapshots=snapshots)
    manifest=dict(signature='synthetic_manifest',simulator_hash='synthetic_core',scenarios=specs,options=dict(seed=4101),
        splits={s['id']:dict(group_id='group_'+s['root_family_id'],experiment_role='pilot_train') for s in specs})
    cp=tmp_path/'control.json';cp.write_text(json.dumps(control),encoding='utf-8')
    monkeypatch.setattr(run_control,'validate_plan',lambda path:(control,manifest))
    reg=dict(schema='integrated.action_proxy_roots.v1',control_plan=p.reference(cp),selected_snapshots=snapshots,root_roles=roles,seed=4101,
        targets=list(p.TARGETS),seeds=list(p.SEEDS),source_role='pilot_train',frozen_role_prohibited=True)
    reg['signature']=p.canonical(reg);rp=tmp_path/'roots.json';rp.write_text(json.dumps(reg),encoding='utf-8')
    base=tmp_path/'branches';base.mkdir();(base/'registered_plan.json').write_text(json.dumps(control),encoding='utf-8');results=[]
    for i,spec in enumerate(specs):
        folder=base/('snapshot_%03d'%i);folder.mkdir();baseline={'security':{'gt_loading_penalty_USD_per_kWh':.35}}
        requests=[baseline,dict(**baseline,targets={'gt_1':90.}),dict(**baseline,targets={'absorption_cooling_kW':360.})]
        candidates=[dict(candidate_id=name,request=request) for name,request in zip(('original_current_policy','GT_minus10pct_grid_request','ABS_minus10pct_request'),requests)]
        segments=[dict(start_minute=float(t),end_minute=float(t+15),hour_of_year=1,unserved_kW=[[1.,2.,3.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]],
            classification=dict(p0_guarantee_failure=t>=105),cost_USD_step=7.,action=dict(gt_fuel_MMBtu_h=1.,boiler_steam_lbph=0.,grid_import_kW=250.,diesel_kW=0.,gt_electric_kW=[100.,110.,120.],absorption_cooling_kW=400.)) for t in range(0,150,15)]
        reference=dict(scenario=spec,seed=4101,identity=dict(group_id='group_'+spec['root_family_id']),segments=segments,
            decisions=history(),summary=dict(status='completed',observed_until_minute=150.))
        (folder/'reference_capture.json').write_text(json.dumps(reference),encoding='utf-8')
        baseline_file=base/('baseline_source_%03d.json'%i);baseline_file.write_text(json.dumps(reference),encoding='utf-8')
        (base/('baseline_reference_%03d.json'%i)).write_text(json.dumps(dict(scenario_id=spec['id'],policy_id='secure_milp',checkpoint_path=str(baseline_file.resolve()),
            checkpoint_sha256=p.sha(baseline_file),manifest_signature=manifest['signature'])),encoding='utf-8')
        (folder/'capture_vs_original.json').write_text(json.dumps(dict(passed=True,differences=[])),encoding='utf-8')
        (folder/'current_plan.json').write_text(json.dumps(plan()),encoding='utf-8')
        (folder/'candidates.json').write_text(json.dumps(dict(snapshot_minute=90.,candidates=candidates,future_used_for_generation=False)),encoding='utf-8')
        (folder/'D0_comparison.json').write_text(json.dumps(dict(passed=True,differences=[])),encoding='utf-8')
        (folder/'plan.json').write_text(json.dumps(dict(scenario_id=spec['id'],seed=4101,snapshot_step=6,manifest_signature=manifest['signature'],counterfactual_enabled=True)),encoding='utf-8')
        snap=folder/'execution_snapshot.json';snap.write_text('{}',encoding='utf-8');outcomes=[]
        for candidate in candidates:
            row=copy.deepcopy(history()[-1]);row.update(requested_command=candidate['request'],permitted_command=plan()['command']['dispatch'],actual_execution=plan()['command']['dispatch'])
            payload=copy.deepcopy(reference);payload.update(decisions=[row],continuation_start_minute=90.)
            name='D0_original_continuation' if candidate['candidate_id']=='original_current_policy' else candidate['candidate_id']
            (folder/(name+'.json')).write_text(json.dumps(payload),encoding='utf-8')
            outcomes.append(dict(candidate=candidate,windows=[window_outcome(payload,90.,60.,SimpleNamespace(plant=plant,hourly=hourly))]))
        result=dict(passed=True,D0_passed=True,scenario_id=spec['id'],snapshot_minute=90.,branches_attempted=3,
            capture_trajectories_attempted=1,outcomes=outcomes,execution_snapshot_sha256=p.sha(snap),future_for_policy=False)
        (folder/'BRANCH_RESULT.json').write_text(json.dumps(result),encoding='utf-8');results.append(result)
    report=dict(plan_signature=control['signature'],snapshots_attempted=2,registered_snapshots=2,branches_attempted=6,
        capture_trajectories_attempted=2,all_attempted_D0_passed=True,results=results)
    (base/'ACTION_BRANCH_REPORT.json').write_text(json.dumps(report),encoding='utf-8')
    return p,rp,base

def test_actual_file_loader_binds_plan_raw_window_and_memory(tmp_path,monkeypatch):
    p,rp,base=branch_fixture(tmp_path,monkeypatch);b=p.load_bound_branches(rp,base)
    assert b['bound'] and len(b['rows'])==6 and b['complete_snapshots']==2
    assert {r['proxy_role'] for r in b['rows']}=={'train','internal_validation'}
    p.verify_bindings(b);b['rows'][0]['targets'][4]+=999.
    with pytest.raises(ValueError,match='memory'):p.verify_bindings(b)

def test_wrong_legacy_fee_and_incomplete_branch_denominators_rejected(tmp_path,monkeypatch):
    p,rp,base=branch_fixture(tmp_path,monkeypatch);rp0=base/'snapshot_000'/'BRANCH_RESULT.json'
    r=json.loads(rp0.read_text(encoding='utf-8'));r['outcomes'][0]['windows'][0]['actual_energy_fee_USD']+=999.
    rp0.write_text(json.dumps(r),encoding='utf-8')
    with pytest.raises(ValueError,match='window|report'):p.load_bound_branches(rp,base)
    report=base/'ACTION_BRANCH_REPORT.json';r=json.loads(report.read_text(encoding='utf-8'));r['snapshots_attempted']=1;report.write_text(json.dumps(r),encoding='utf-8')
    with pytest.raises(ValueError,match='complete'):p.load_bound_branches(rp,base)

def test_postload_raw_file_mutation_prevents_fitting(tmp_path,monkeypatch):
    p,rp,base=branch_fixture(tmp_path,monkeypatch);b=p.load_bound_branches(rp,base)
    file=base/'snapshot_000'/'D0_original_continuation.json';file.write_text(file.read_text(encoding='utf-8')+' ',encoding='utf-8')
    with pytest.raises(ValueError,match='bytes'):p.run_action_proxy(b,tmp_path/'fit')
    assert not (tmp_path/'fit').exists()

def test_actual_synthetic_bound_run_logs_six_fits_and_thirty_target_units(tmp_path,monkeypatch):
    p,rp,base=branch_fixture(tmp_path,monkeypatch);b=p.load_bound_branches(rp,base)
    report=p.run_action_proxy(b,tmp_path/'fit')
    assert report['actual_fit_attempts']==6 and report['actual_target_fit_units']==30 and report['failed_fit_attempts']==0
    assert report['development_proxy_only'] and not report['online_controller']
    assert (tmp_path/'fit'/'fitted_inputs.npz').exists() and (tmp_path/'fit'/'raw_rows_and_masks.json').exists()

def test_coherently_changed_saved_fee_is_rejected_by_actual_source_recalculation(tmp_path,monkeypatch):
    p,rp,base=branch_fixture(tmp_path,monkeypatch);file=base/'snapshot_000'/'BRANCH_RESULT.json'
    result=json.loads(file.read_text(encoding='utf-8'));result['outcomes'][0]['windows'][0]['actual_energy_fee_USD']+=999.
    file.write_text(json.dumps(result),encoding='utf-8')
    file=base/'ACTION_BRANCH_REPORT.json';report=json.loads(file.read_text(encoding='utf-8'));report['results'][0]=result;file.write_text(json.dumps(report),encoding='utf-8')
    with pytest.raises(ValueError,match='window'):p.load_bound_branches(rp,base)

def test_original_baseline_checkpoint_is_bound_through_fitting(tmp_path,monkeypatch):
    p,rp,base=branch_fixture(tmp_path,monkeypatch);b=p.load_bound_branches(rp,base)
    file=base/'baseline_source_000.json';file.write_text(file.read_text(encoding='utf-8')+' ',encoding='utf-8')
    with pytest.raises(ValueError,match='bytes'):p.verify_bindings(b)

def test_internal_proxy_fold_checks_actual_raw_support_not_only_different_root_ids():
    p=proxy();a=dict(id='a',root_family_id='a',experiment_role='pilot_train',start_hour=101,duration_steps=32,step_minutes=15)
    b=dict(a,id='b',root_family_id='b',start_hour=102)
    with pytest.raises(ValueError,match='raw support'):p.verify_internal_support([a,b],{'a':'train','b':'internal_validation'})
    b['start_hour']=201
    assert p.verify_internal_support([a,b],{'a':'train','b':'internal_validation'})['cross_role_overlaps']==0

def test_actual_horizon_action_diversity_is_separate_from_identical_first_interval():
    p=proxy();a=p.build_row(history(),plan(),{'security':{'gt_loading_penalty_USD_per_kWh':.35}},window(),identity(),actual_dispatch=plan()['command']['dispatch'])
    b=copy.deepcopy(a);b['candidate_id']='later_arrival'
    a['postdecision_diagnostics']['actual_H60_mean_ports']=[100.,110.,120.,250.,0.,400.]
    b['postdecision_diagnostics']['actual_H60_mean_ports']=[90.,110.,120.,260.,0.,400.]
    report=p.action_diversity([a,b])['snapshot_groups'][0]
    assert report['first_interval_actual_variants']==1 and report['actual_H60_mean_variants']==2

def test_errored_candidate_before_observed_submission_retains_unknown_targets(tmp_path,monkeypatch):
    p,rp,base=branch_fixture(tmp_path,monkeypatch);folder=base/'snapshot_000';file=folder/'GT_minus10pct_grid_request.json'
    payload=json.loads(file.read_text(encoding='utf-8'));payload['decisions']=[];payload['segments']=[s for s in payload['segments'] if s['end_minute']<=90.]
    payload['summary'].update(status='solver_error',observed_until_minute=90.)
    payload['solver_errors']=[dict(type='forced synthetic error',input='preserved raw context')]
    payload['error']=dict(type='forced synthetic error',message='same top-level error representation')
    file.write_text(json.dumps(payload),encoding='utf-8');w=p._window_outcome(payload,90.,p._load_energy_context())
    file=folder/'BRANCH_RESULT.json';result=json.loads(file.read_text(encoding='utf-8'));result['outcomes'][1]['windows']=[w];file.write_text(json.dumps(result),encoding='utf-8')
    file=base/'ACTION_BRANCH_REPORT.json';report=json.loads(file.read_text(encoding='utf-8'));report['results'][0]=result;file.write_text(json.dumps(report),encoding='utf-8')
    bound=p.load_bound_branches(rp,base);r=next(r for r in bound['rows'] if r['scenario_id']=='case_root1' and r['candidate_id']=='GT_minus10pct_grid_request')
    assert r['targets']==[None]*5 and r['target_mask']==[False]*5 and not r['fit_complete_five_targets']
    assert r['request_submission_observed'] is False and r['branch_status']=='solver_error'
    assert r['solver_error_records_count']==1 and r['top_level_error_present'] is True
    assert r['error_records_count']==2 and r['error_record_count_semantics']=='record representations, not unique exception count'
    assert len(bound['rows'])==6 and r['source_payload']['sha256']==p.sha(folder/'GT_minus10pct_grid_request.json')
