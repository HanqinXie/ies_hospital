"""P2B scenario consequence fits, full attempt ledger and frozen selection.

Inputs are audited score rows, a frozen manifest and its declared measure.
The module never imports an environment or evaluates a hospital response.
"""
import argparse
import csv
import hashlib
import json
from math import comb
from pathlib import Path
import time
import traceback
import warnings
import joblib
import numpy as np
from scipy.linalg import svdvals
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score
from threadpoolctl import threadpool_limits
from tools.p2b_methods import DNIPCModel,DSPCEModel
from .provenance import build_fit_provenance

SEEDS=(3201,3202,3203)
TARGETS=tuple(f'{p}_{c}_ENS_kWh' for p in ('P0','P3') for c in ('e','h','c'))+('cost_USD',)


def candidate_registry():
    candidates=[]
    for method in ['DNIPC','DSPCE']:
        for degree in [2,3]:
            eligible=not(method=='DSPCE' and degree==3)
            candidates.append(dict(config_id=f'{method}_p{degree}',**{'class':method},task='regression',degree=degree,eligible=eligible,reason=None if eligible else 'public_high_order_math_trial_nonconverged; no claim all lowdim p3 must fail'))
    for task in ['regression','risk']:
        for depth in [6,None]:
            for leaf in [2,5]:
                candidates.append(dict(config_id=f'RF_{task}_depth{depth}_leaf{leaf}',**{'class':'RF'},task=task,max_depth=depth,min_samples_leaf=leaf,n_estimators=200,eligible=True))
    for hidden in [16,32]:
        candidates.append(dict(config_id=f'GRU_hidden{hidden}',**{'class':'GRU'},task='risk',hidden_size=hidden,layers=1,learning_rate=.001,batch_size=64,epochs=30,patience=5,eligible=True))
    return candidates


def parse_bool(value):
    """CSV boolean/integer enum, preserving unknown as None."""
    if value is None:return None
    if isinstance(value,(bool,np.bool_)):return bool(value)
    if isinstance(value,str):
        word=value.strip().lower()
        if word in ('','none','null','nan'):return None
        if word in ('true','1','1.0'):return True
        if word in ('false','0','0.0'):return False
        raise ValueError('invalid boolean enum: '+value)
    if isinstance(value,(int,float,np.number)):
        if not np.isfinite(value):return None
        if value in (0,1):return bool(value)
    raise ValueError('invalid boolean enum')


def _bool(value):
    return parse_bool(value) is True


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def learning_preflight(manifest,score_rows,study_distribution,audit_evidence):
    """Bind every fitting row to the final audited 800-pair CSV and manifest."""
    body=dict(manifest);signature=body.pop('signature')
    if canonical_hash(body)!=signature:raise ValueError('manifest signature mismatch')
    if study_distribution!=manifest['p2b_study_distribution']:raise ValueError('study distribution differs from frozen manifest')
    if audit_evidence.get('passed') is not True:raise ValueError('successful final audit receipt required')
    path=Path(audit_evidence['path']);scores_path=Path(audit_evidence['scores_path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=audit_evidence['sha256']:raise ValueError('audit hash mismatch')
    audit=json.loads(path.read_text(encoding='utf-8'))
    if audit.get('passed') is not True or audit.get('complete_budget') is not True or audit.get('manifest_signature')!=signature:
        raise ValueError('audit does not attest completed frozen manifest')
    expected={'attempted_trajectories':800,'checkpoints':800,'attempted_specifications':400,'complete_pairs':400}
    if any(audit.get(k)!=v for k,v in expected.items()):raise ValueError('final audit counts are not 800 trajectories / 400 complete pairs')
    actual_sha=hashlib.sha256(scores_path.read_bytes()).hexdigest()
    if actual_sha!=audit.get('scores_sha256') or actual_sha!=audit_evidence.get('scores_sha256'):raise ValueError('score CSV bytes mismatch')
    with scores_path.open(encoding='utf-8-sig',newline='') as file:audited_rows=list(csv.DictReader(file))
    if canonical_hash(score_rows)!=canonical_hash(audited_rows):raise ValueError('supplied rows differ from audited CSV rows')
    if audit.get('score_rows_canonical_sha256') not in (None,canonical_hash(score_rows)):raise ValueError('audit canonical rows mismatch')
    specs={s['id']:s for s in manifest['scenarios']}
    if len(specs)!=400 or len(manifest['policies'])!=2:raise ValueError('registered complete design must contain 400 specifications and 2 policies')
    expected_pairs={(sid,policy) for sid in specs for policy in manifest['policies']}
    actual_pairs=[(r['scenario_id'],r['policy_id']) for r in score_rows]
    if len(actual_pairs)!=800 or len(set(actual_pairs))!=800 or set(actual_pairs)!=expected_pairs:raise ValueError('missing, duplicate or unowned score pairs')
    for row in score_rows:
        spec=specs[row['scenario_id']];assignment=manifest['splits'][row['scenario_id']]
        expected_fields={'root_family_id':spec['root_family_id'],'experiment_role':spec['experiment_role'],'study_stratum':spec.get('study_stratum'),'group_id':assignment['group_id']}
        for key,value in expected_fields.items():
            if (row.get(key) or None)!=(value or None):raise ValueError('score identity differs from manifest: '+key)
        validity=parse_bool(row.get('score_outcome_valid'));failure=parse_bool(row.get('score_P0_failure'))
        if validity is True and failure is None:raise ValueError('complete score outcome has unknown failure class')
    return {'manifest_signature':signature,'scores_sha256':actual_sha,'score_rows_canonical_sha256':canonical_hash(score_rows),'counts':expected}


def _finite(value):
    try:return value is not None and value!='' and bool(np.isfinite(float(value)))
    except (TypeError,ValueError):return False


def valid_rows(rows,target):
    return [r for r in rows if _bool(r.get('score_outcome_valid')) and _finite(r.get(target))]


def assert_group_isolation(train,valid):
    for key in ['scenario_id','group_id','root_family_id']:
        left={r[key] for r in train};right={r[key] for r in valid}
        if left&right:raise ValueError(f'{key} group isolation violated')


def regression_gate(train,valid,target,basis_count=None):
    assert_group_isolation(train,valid)
    tr=valid_rows(train,target);va=valid_rows(valid,target)
    std=float(np.std([float(r[target]) for r in tr])) if tr else None
    reasons=[]
    if len(tr)<60:reasons.append('fewer_than_60_valid_train_specs')
    if len(va)<15:reasons.append('fewer_than_15_valid_validation_specs')
    if len({r['group_id'] for r in tr})<10:reasons.append('fewer_than_10_train_groups')
    if len({r['group_id'] for r in va})<3:reasons.append('fewer_than_3_validation_groups')
    if std is not None and std<=1e-8:reasons.append('constant_training_target')
    if basis_count is not None and len(tr)<3*basis_count:reasons.append('less_than_three_times_basis_count')
    return {'eligible':not reasons,'reasons':reasons,'train_all':len(train),'validation_all':len(valid),'train_valid':len(tr),'validation_valid':len(va),'train_unknown':len(train)-len(tr),'validation_unknown':len(valid)-len(va),'train_groups':len({r['group_id'] for r in tr}),'validation_groups':len({r['group_id'] for r in va}),'train_std':std,'basis_count':basis_count,'train_unknown_ids':[r['scenario_id'] for r in train if r not in tr],'validation_unknown_ids':[r['scenario_id'] for r in valid if r not in va]}


def sample_measure(measure,n,seed):
    rng=np.random.default_rng(seed)
    x=np.empty((n,3))
    for j,key in enumerate(['shock_e','shock_c']):
        if measure[key]['family']!='uniform':raise ValueError('unregistered continuous measure')
        lo,hi=measure[key]['bounds'];x[:,j]=rng.uniform(lo,hi,n)
    hours=measure['calendar_hour']
    if hours['family']!='discrete_uniform' or not np.isclose(hours['probability_per_value'],1/len(hours['values'])):raise ValueError('unregistered calendar measure')
    x[:,2]=rng.choice(hours['values'],n)
    return x


def regression_metrics(y,pred,train_y,groups,observed_failure,bootstrap_repeats=0):
    y=np.asarray(y,float);pred=np.asarray(pred,float);train_y=np.asarray(train_y,float)
    groups=np.asarray(groups);failure=np.asarray([parse_bool(v) for v in observed_failure],object)
    err=pred-y;rmse=float(np.sqrt(np.mean(err**2)));train_std=float(np.std(train_y))
    result={'N':len(y),'RMSE':rmse,'MAE':float(np.mean(np.abs(err))),'normalized_RMSE':rmse/train_std if train_std>1e-8 else None,'R2':float(r2_score(y,pred)) if len(y)>1 and y.std()>1e-8 else None,'max_abs_error':float(np.max(np.abs(err))),'max_underprediction':float(max(0,np.max(y-pred))),'negative_prediction_fraction':float(np.mean(pred<0)),'truth_mean':float(y.mean()),'prediction_mean':float(pred.mean()),'truth_std':float(y.std()),'prediction_std':float(pred.std()),'quantile_probabilities':[.05,.5,.9,.95],'truth_quantiles':np.quantile(y,[.05,.5,.9,.95]).tolist(),'prediction_quantiles':np.quantile(pred,[.05,.5,.9,.95]).tolist()}
    threshold=float(np.quantile(train_y,.9));severe=y>=threshold
    result['severe_train_q90_threshold']=threshold
    result['severe_validation']={'N':int(severe.sum()),'RMSE':float(np.sqrt(np.mean(err[severe]**2))) if severe.any() else None,'max_underprediction':float(max(0,np.max(y[severe]-pred[severe]))) if severe.any() else None}
    result['boundary_by_observed_failure']={}
    for name,mask in [('failure',failure==True),('no_failure',failure==False),('unknown_failure_class',failure==None)]:
        result['boundary_by_observed_failure'][name]={'N':int(mask.sum()),'RMSE':float(np.sqrt(np.mean(err[mask]**2))) if mask.any() else None,'MAE':float(np.mean(np.abs(err[mask]))) if mask.any() else None}
    if bootstrap_repeats:
        unique=np.unique(groups);rng=np.random.default_rng(3221);samples=[]
        group_indices=[np.flatnonzero(groups==g) for g in unique]
        for _ in range(bootstrap_repeats):
            idx=np.concatenate([group_indices[i] for i in rng.integers(len(unique),size=len(unique))])
            samples.append(float(np.sqrt(np.mean(err[idx]**2))))
        result['bootstrap']={'unit':'connected_group','repeats':bootstrap_repeats,'seed':3221,'groups':len(unique),'RMSE_95percent_interval':np.quantile(samples,[.025,.975]).tolist(),'limitation':'development validation interval; only a few held-out groups, statistically unstable'}
    return result


def fit_candidate(config,seed,x,y,basis_samples,basis_measure):
    start=time.perf_counter()
    if not config['eligible']:raise ValueError('candidate failed independent math gate')
    if config['class']=='RF':
        model=RandomForestRegressor(n_estimators=config['n_estimators'],max_depth=config['max_depth'],min_samples_leaf=config['min_samples_leaf'],random_state=seed,n_jobs=1).fit(x,y)
        diag={'algorithm':'random_forest_regressor'}
    else:
        cls=DNIPCModel if config['class']=='DNIPC' else DSPCEModel
        model=cls(config['degree'],basis_measure=basis_measure,input_names=['shock_e','shock_c','calendar_hour'],input_units=['relative_increment','relative_increment','one_based_hour']).fit(x,y,basis_samples=basis_samples)
        if config['class']=='DNIPC':rank=model.diagnostics_['fit']['rank']
        else:
            singular=svdvals(model.basis_.transform(x));rank=int(np.count_nonzero(singular>1e-12*singular[0]))
        diag={'algorithm':model.method,'polynomial':model.diagnostics_,'design_rank':int(rank),'design_rank_cutoff_relative':1e-12}
        reason=None
        if rank!=len(model.coef_):reason='polynomial training design is not full column rank at actual solver cutoff'
        if config['class']=='DSPCE':
            fit=model.diagnostics_['fit']
            diag['full_rank_interpretation']='with full column rank, the weighted step reduces to ordinary least squares before pruning; not underdetermined sparse identification'
            if fit['final_ols_rank']!=fit['retained_terms']:reason='D-MORPH retained final OLS is not full column rank'
            if not fit['converged']:reason='D-MORPH did not converge; trial is not eligible for selection'
        if reason:
            error=ValueError(reason);error.model=model;error.diagnostics=diag
            raise error
    return model,{**diag,'fit_seconds':time.perf_counter()-start}


def serialize_model(model,config,path,provenance):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    if config['class']=='RF':
        joblib.dump(model,path/'model.joblib',compress=3)
        restored=joblib.load(path/'model.joblib')
    else:
        model.save(path/'polynomial')
        restored=type(model).load(path/'polynomial')
    files=[]
    for file in path.rglob('*'):
        if file.is_file():files.append({'path':file.relative_to(path).as_posix(),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    (path/'provenance.json').write_text(json.dumps({'config':config,**provenance,'files':files},indent=2,allow_nan=False),encoding='utf-8')
    return restored


def _write(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')


def run_regression(manifest,score_rows,study_distribution,output_dir,audit_evidence):
    preflight=learning_preflight(manifest,score_rows,study_distribution,audit_evidence)
    implementation_provenance=build_fit_provenance(candidate_registry(),SEEDS)
    out=Path(output_dir)
    if out.exists():raise FileExistsError('new immutable learning run directory required')
    out.mkdir(parents=True)
    _write(out/'FIT_RUN_MANIFEST.json',implementation_provenance)
    configs=candidate_registry();_write(out/'candidate_registry.json',configs)
    scenarios={s['id']:s for s in manifest['scenarios']}
    # Rows must match registered roles/strata. Development and final data are never used.
    study=[]
    seen=set()
    for row in score_rows:
        key=(row['scenario_id'],row['policy_id'])
        if key in seen:raise ValueError('duplicate score row')
        seen.add(key);spec=scenarios[row['scenario_id']]
        if row['experiment_role']!=spec['experiment_role']:raise ValueError('score role differs from frozen manifest')
        if spec['experiment_role'] not in ('pilot_train','pilot_validation'):continue
        if row['study_stratum']!=spec['study_stratum']:raise ValueError('score stratum differs from frozen manifest')
        study.append(row)
    score_hash=hashlib.sha256(json.dumps(score_rows,sort_keys=True,default=str).encode()).hexdigest()
    results=[];selections=[]
    basis_cache={};prior_cache={}
    for stratum,dist in study_distribution['strata'].items():
        for seed in SEEDS:basis_cache[(stratum,seed)]=sample_measure(dist['basis_measure'],32768,seed)
        prior_cache[stratum]=sample_measure(dist['basis_measure'],32768,3211)
        for policy in manifest['policies']:
            all_train=[r for r in study if r['study_stratum']==stratum and r['policy_id']==policy and r['experiment_role']=='pilot_train']
            all_valid=[r for r in study if r['study_stratum']==stratum and r['policy_id']==policy and r['experiment_role']=='pilot_validation']
            assert_group_isolation(all_train,all_valid)
            for target in TARGETS:
                condition=f'{stratum}__{policy}__{target}';directory=out/condition;directory.mkdir()
                gate=regression_gate(all_train,all_valid,target);_write(directory/'data_gate.json',gate)
                tr=valid_rows(all_train,target);va=valid_rows(all_valid,target)
                if not tr or not va:
                    results.append({'condition':condition,'status':'insufficient_data','data_gate':gate});continue
                x=np.array([scenarios[r['scenario_id']]['offline_inputs'] for r in tr],float);y=np.array([float(r[target]) for r in tr])
                vx=np.array([scenarios[r['scenario_id']]['offline_inputs'] for r in va],float);vy=np.array([float(r[target]) for r in va])
                groups=np.array([r['group_id'] for r in va]);failure=np.array([parse_bool(r['score_P0_failure']) for r in va],object)
                np.savez_compressed(directory/'dataset.npz',train_X=x,train_y=y,validation_X=vx,validation_y=vy)
                ids={'train':[r['scenario_id'] for r in tr],'validation':[r['scenario_id'] for r in va],'train_groups':[r['group_id'] for r in tr],'validation_groups':groups.tolist()}
                _write(directory/'dataset_ids.json',ids)
                baseline=regression_metrics(vy,np.full(len(vy),y.mean()),y,groups,failure,1000)
                _write(directory/'constant_baseline.json',{'status':'constant_training_target' if y.std()<=1e-8 else 'constant_mean_baseline','metrics':baseline})
                if not gate['eligible']:
                    results.append({'condition':condition,'status':'insufficient_or_constant','data_gate':gate,'constant_baseline':baseline});continue
                trials=[]
                for config in [c for c in configs if c['task']=='regression']:
                    ngate=regression_gate(all_train,all_valid,target,comb(3+config['degree'],3) if config['class']!='RF' else None)
                    if not config['eligible'] or not ngate['eligible']:
                        trials.append({'config_id':config['config_id'],'status':'skipped','reason':config.get('reason') or ngate['reasons']});continue
                    for seed in SEEDS:
                        trial_dir=directory/f'{config["config_id"]}__seed{seed}';trial_dir.mkdir()
                        trial={'config_id':config['config_id'],'seed':seed,'status':'attempted','class':config['class'],'path':str(trial_dir)}
                        try:
                            with warnings.catch_warnings(record=True) as caught:
                                warnings.simplefilter('always')
                                model,diag=fit_candidate(config,seed,x,y,basis_cache[(stratum,seed)],dist['basis_measure'])
                            trial['warnings']=[str(w.message) for w in caught];trial['diagnostics']=diag
                            start=time.perf_counter();pred=model.predict(vx);trial['prediction_seconds']=time.perf_counter()-start
                            trial['metrics']=regression_metrics(vy,pred,y,groups,failure)
                            restored=serialize_model(model,config,trial_dir/'model',{'seed':seed,'fit_dataset_ids':ids['train'],'fit_group_ids':ids['train_groups'],'manifest_signature':manifest.get('signature'),'score_rows_sha256':score_hash,'fit_dataset_npz_sha256':hashlib.sha256((directory/'dataset.npz').read_bytes()).hexdigest(),'implementation_provenance':implementation_provenance,'input_order':dist['input_order'],'input_units':dist['input_units'],'basis_measure':dist['basis_measure'],'design_distribution':dist['design_distribution'],'validation_distribution':dist['validation_measure'],'validation_sampling_condition':'observed complete-score subset; missing unknown outcomes may be informative','target_unit':'USD' if target=='cost_USD' else 'kWh','target':target,'policy':policy,'condition':stratum,'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
                            delta=float(np.max(np.abs(restored.predict(vx)-pred)))
                            trial['serialization_max_abs_error']=delta
                            if delta>1e-12:raise ValueError('serialized prediction mismatch')
                            np.save(trial_dir/'validation_predictions.npy',pred)
                            trial['status']='completed'
                        except Exception as exc:
                            trial['status']='failed';trial['error_type']=type(exc).__name__;trial['traceback']=traceback.format_exc()
                            if hasattr(exc,'model'):
                                trial['diagnostics']=exc.diagnostics
                                serialize_model(exc.model,config,trial_dir/'failed_model',{'seed':seed,'fit_dataset_ids':ids['train'],'score_rows_sha256':score_hash,'fit_dataset_npz_sha256':hashlib.sha256((directory/'dataset.npz').read_bytes()).hexdigest(),'implementation_provenance':implementation_provenance,'status':'failed_candidate_not_for_inference','failure':str(exc)})
                        _write(trial_dir/'trial.json',trial);trials.append(trial)
                candidates=[]
                for config in [c for c in configs if c['task']=='regression']:
                    good=[t for t in trials if t['config_id']==config['config_id'] and t['status']=='completed']
                    if len(good)==len(SEEDS):
                        candidates.append({'config_id':config['config_id'],'mean_validation_normalized_RMSE':float(np.mean([t['metrics']['normalized_RMSE'] for t in good])),'seed_scores':[t['metrics']['normalized_RMSE'] for t in good]})
                selected=min(candidates,key=lambda c:c['mean_validation_normalized_RMSE']) if candidates else None
                selection={'condition':condition,'selection_rule':'mean normalized validation RMSE across three fixed seeds; no best-seed selection','configurations':candidates,'selected':selected,'status':'fitted' if selected else 'all_candidates_failed_or_skipped','data_gate':gate,'error_measure_interpretation':'metrics condition on the observed complete-score subset of the nominal held-out validation distribution; missing unknown outcomes may be informative, so full-domain error cannot be inferred'}
                if selected:
                    config=next(c for c in configs if c['config_id']==selected['config_id'])
                    selected_evidence=[]
                    for trial in [t for t in trials if t['config_id']==selected['config_id'] and t['status']=='completed']:
                        path=Path(trial['path']);pred=np.load(path/'validation_predictions.npy')
                        if config['class']=='RF':model=joblib.load(path/'model/model.joblib')
                        else:model=(DNIPCModel if config['class']=='DNIPC' else DSPCEModel).load(path/'model/polynomial')
                        stats_pred=model.predict(prior_cache[stratum])
                        evidence={'seed':trial['seed'],'validation_metrics':regression_metrics(vy,pred,y,groups,failure,1000),'p_study_surrogate_prediction_statistics':{'N':32768,'seed':3211,'mean':float(stats_pred.mean()),'std':float(stats_pred.std()),'quantiles':np.quantile(stats_pred,[.05,.5,.9,.95]).tolist(),'negative_prediction_fraction':float(np.mean(stats_pred<0)),'measure':dist['basis_measure'],'truth_simulated':False,'interpretation':'surrogate prediction under entire preregistered prior; not simulation truth or empirical hospital risk'}}
                        _write(path/'selected_evidence.json',evidence);selected_evidence.append(evidence)
                    selection['selected_seed_evidence']=selected_evidence
                _write(directory/'selection.json',selection);selections.append(selection)
                results.append({'condition':condition,'status':selection['status'],'trials':trials,'selection':selected})
                print(condition,selection['status'],selected['config_id'] if selected else None,flush=True)
    report={'scope':'offline scenario consequences with legal full exogenous inputs; not online risk or action counterfactuals','error_measure_interpretation':'observed complete-score subset only; unknown outcomes may be informatively missing; no full-domain error inference','candidate_configuration_budget':14,'candidate_classes':['DNIPC','DSPCE','RF','GRU'],'seeds':list(SEEDS),'audit_evidence':audit_evidence,'preflight':preflight,'manifest_signature':manifest.get('signature'),'score_rows_sha256':score_hash,'attempted_score_rows':len(score_rows),'results':results,'selections':selections,'no_hospital_environment_calls':True}
    _write(out/'REGRESSION_REPORT.json',report);return report


def main():
    parser=argparse.ArgumentParser()
    for key in ['manifest','scores','study-distribution','audit-receipt','out']:parser.add_argument('--'+key,required=True)
    args=parser.parse_args()
    manifest=json.loads(Path(args.manifest).read_text(encoding='utf-8'));distribution=json.loads(Path(args.study_distribution).read_text(encoding='utf-8'));receipt=json.loads(Path(args.audit_receipt).read_text(encoding='utf-8'))
    with Path(args.scores).open(encoding='utf-8-sig',newline='') as file:rows=list(csv.DictReader(file))
    with threadpool_limits(limits=2):run_regression(manifest,rows,distribution,args.out,receipt)


if __name__=='__main__':main()
