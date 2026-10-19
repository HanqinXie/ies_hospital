"""Small offline current-action proxy; never an online selector or controller.

The continuation is the original secure policy. Inputs contain only four legal
frames, explicit requested-port masks and the separately known CURRENT plan.
Actual projections, executions and future outcomes are diagnostics/targets.
"""
from __future__ import annotations
import argparse,copy,json,sys,time
from collections import Counter
from pathlib import Path
import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import average_precision_score,roc_auc_score,brier_score_loss,confusion_matrix
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from .offline import canonical,sha,write,reference,path_of,finite,logged_fit,consequence_metrics,SequencePreprocessor
from tools.integrated_learning.policies import ACTION_NAMES,legal_history,six_targets
from hospital_ies.data_contract import validate_action
SEEDS=(4101,4102,4103)
TARGETS=('P0_e_ENS_kWh_H60','P0_h_ENS_kWh_H60','P0_c_ENS_kWh_H60','P0_failure_H60','actual_energy_fee_USD_H60')
UNITS={k:'USD' if 'fee' in k else 'binary' if 'failure' in k else 'kWh' for k in TARGETS}
ROLES=('train','internal_validation')

def registry():
    return [dict(config_id='action_proxy_mean5',recipe='consequence_mean',algorithm='mean',targets=list(TARGETS),input='four_legal_frames_plus_masked_request_and_known_current_plan'),
        dict(config_id='action_proxy_RF200_depth6_leaf2_5',recipe='consequence_RF',algorithm='RF',n_estimators=200,max_depth=6,min_samples_leaf=2,
            target_loss_scale='train-only mean/std; std<=1e-8 uses 1; inverse predictions to original units',targets=list(TARGETS),input='four_legal_frames_plus_masked_request_and_known_current_plan')]

def split_roots(roots):
    ordered=sorted(set(roots),key=lambda r:(canonical([4101,r]),r))
    if len(ordered)<2:raise ValueError('Independent train/internal-validation roots required')
    n=max(1,len(ordered)//5)
    return {r:'internal_validation' if i<n else 'train' for i,r in enumerate(ordered)}

def verify_internal_support(specs,roles):
    from .offline import assert_support_isolation
    return assert_support_isolation([dict(s,experiment_role=roles[s['root_family_id']]) for s in specs])

def _ports(value):
    if value is None:return None
    ports=six_targets(value)
    if not np.isfinite(ports).all():raise ValueError('Nonfinite port diagnostics')
    return ports.tolist()

def build_row(history,current_plan,request,window,identity,*,permitted_dispatch=None,actual_dispatch=None):
    if identity.get('source_role')!='pilot_train':raise ValueError('Action proxy is training-side only; frozen/validation source prohibited')
    if identity.get('proxy_role') not in ROLES:raise ValueError('Unregistered proxy role')
    legal=legal_history(history);t=float(identity['snapshot_minute'])
    if not legal or legal[-1]['decision_time_min']!=t:raise ValueError('Current history differs from registered snapshot clock')
    request=validate_action(request)
    if request.get('security',{}).get('gt_loading_penalty_USD_per_kWh')!=.35:raise ValueError('Fixed original secure continuation/weight required')
    command=current_plan['command']
    if not finite(command.get('generated_minute')) or command['generated_minute']>t:raise ValueError('Current plan projection must already be known')
    known=_ports(command['dispatch']);asked=request.get('targets',{})
    masks=[k in asked for k in ACTION_NAMES];requested=[float(asked[k]) if k in asked else None for k in ACTION_NAMES]
    keys=set(legal[-1]['features']);pad=4-len(legal)
    if any(set(d['features'])!=keys for d in legal):raise ValueError('Inconsistent four-frame feature schema')
    sequence=[{k:None for k in keys} for _ in range(pad)]+[copy.deepcopy(d['features']) for d in legal]
    # Requests/current planning results are available at this decision only.
    # Baseline unspecified targets remain null, with an explicit false mask.
    for i,frame in enumerate(sequence):
        for j,name in enumerate(ACTION_NAMES):
            frame['requested_'+name]=requested[j] if i==3 else None
            frame['requested_'+name+'_specified']=bool(masks[j]) if i==3 else False
            frame['known_baseline_projection_'+name]=known[j] if i==3 else None
    if window.get('horizon_minutes')!=60:raise ValueError('Proxy horizon is fixed to 60 actual minutes')
    full=window.get('full_horizon') is True;ens=window.get('ENS_kWh_by_priority_carrier')
    targets=[float(ens[0][j]) if full and ens is not None and finite(ens[0][j]) else None for j in range(3)]
    failure=window.get('P0_failure_in_window')
    if failure not in (0,1,None):raise ValueError('Unknown or binary original failure flag required')
    targets.append(float(failure) if failure==1 or full and failure==0 else None)
    targets.append(float(window['actual_energy_fee_USD']) if full and finite(window.get('actual_energy_fee_USD')) else None)
    mask=[v is not None for v in targets];permitted=_ports(permitted_dispatch);actual=_ports(actual_dispatch)
    changed=None if permitted is None or not any(masks) else any(abs(permitted[j]-requested[j])>1e-7 for j in range(6) if masks[j])
    return dict(**copy.deepcopy(identity),sequence=sequence,padding_mask=[0]*pad+[1]*len(legal),
        source_feature_metadata=[copy.deepcopy(d['feature_metadata']) for d in legal],requested_command=request,
        requested_targets=requested,requested_target_mask=masks,known_baseline_projection=known,
        targets=targets,target_mask=mask,fit_complete_five_targets=full and all(mask),observed_horizon_minutes=window.get('observed_minutes'),
        postdecision_diagnostics=dict(permitted_ports=permitted,first_interval_actual_ports=actual,request_projection_changed=changed),
        target_semantics='Original classified P0 failure anywhere in next 60 min, including already-failed states; not a first-failure risk label')

def _unique(values):
    representatives=[]
    for value in values:
        if value is None:continue
        if not any(all(a==b if a is None or b is None or isinstance(a,bool) or isinstance(b,bool) else abs(a-b)<=1e-7 for a,b in zip(value,prior)) for prior in representatives):representatives.append(value)
    return len(representatives)

def action_diversity(rows):
    groups={}
    for r in rows:groups.setdefault((r['root_family_id'],r['snapshot_minute'],r.get('snapshot_source','')),[]).append(r)
    report=[]
    for key,items in sorted(groups.items()):
        report.append(dict(root_family_id=key[0],snapshot_minute=key[1],snapshot_source=key[2],candidate_rows=len(items),
            requested_variants=_unique([r['requested_targets']+r['requested_target_mask'] for r in items]),
            permitted_variants=_unique([r['postdecision_diagnostics']['permitted_ports'] for r in items]),
            first_interval_actual_variants=_unique([r['postdecision_diagnostics']['first_interval_actual_ports'] for r in items]),
            actual_H60_mean_variants=_unique([r['postdecision_diagnostics'].get('actual_H60_mean_ports') for r in items])))
    return dict(snapshot_groups=report,request_projection_changed_rows=sum(r['postdecision_diagnostics']['request_projection_changed'] is True for r in rows),
        request_projection_unknown_rows=sum(r['postdecision_diagnostics']['request_projection_changed'] is None for r in rows),
        numerical_distinctness_absolute_kW=1e-7,semantics='Requested, MILP permitted, and first-interval actual are distinct; none of the latter are predictors',
        limitation='Few effectively distinct actions and incomplete causal state may limit proxy action identification; fit still runs')

def fit_model(algorithm,seed,x,y,vx,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False);x=np.asarray(x,float);y=np.asarray(y,float)
    if y.ndim!=2 or y.shape[1]!=5 or not len(y) or not np.isfinite(y).all():raise ValueError('Only common complete five-target rows may fit')
    write(directory/'training_started.json',dict(algorithm=algorithm,seed=seed,training_rows=len(y),targets=list(TARGETS),utc=time.time()))
    if algorithm=='mean':
        mean=y.mean(axis=0);np.save(directory/'mean.npy',mean);original=np.tile(mean,(len(vx),1))
    elif algorithm=='RF':
        mean=y.mean(axis=0);std=y.std(axis=0);scale=np.where(std>1e-8,std,1.)
        model=RandomForestRegressor(n_estimators=200,max_depth=6,min_samples_leaf=2,n_jobs=1,random_state=seed)
        model.fit(x,(y-mean)/scale);original=model.predict(vx)*scale+mean if len(vx) else np.empty((0,5))
        joblib.dump(model,directory/'model.joblib',compress=3)
        write(directory/'target_scaler.json',dict(mean=mean.tolist(),std=std.tolist(),scale=scale.tolist(),fit_role='train',targets=list(TARGETS),units=UNITS))
    else:raise ValueError('Only preregistered consequence mean/RF recipes are allowed')
    write(directory/'model_descriptor.json',dict(algorithm=algorithm,seed=seed,targets=list(TARGETS),units=UNITS,continuation='secure_milp',online_controller=False))
    reloaded=reload_model(directory,vx);error=float(np.max(abs(original-reloaded))) if len(original) else 0.
    if error>1e-12:raise ValueError('Original fitted and serialized proxy predictions differ')
    write(directory/'training_completed.json',dict(seed=seed,actual_estimator_fit_calls=1,target_fit_units=5,reload_max_abs_error=error,utc=time.time()))
    return reloaded,dict(reload_max_abs_error=error,actual_estimator_fit_calls=1,target_fit_units=5)

def reload_model(directory,x):
    directory=Path(directory);d=json.loads((directory/'model_descriptor.json').read_text(encoding='utf-8'))
    if not len(x):return np.empty((0,5))
    if d['algorithm']=='mean':return np.tile(np.load(directory/'mean.npy'),(len(x),1))
    scale=json.loads((directory/'target_scaler.json').read_text(encoding='utf-8'))
    return joblib.load(directory/'model.joblib').predict(x)*np.asarray(scale['scale'])+np.asarray(scale['mean'])

def _load_energy_context():
    from hospital_ies.model import load_plant
    from hospital_ies.pipeline import read_data
    return load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory'),read_data(ROOT)

def _window_outcome(payload,start,context):
    from .energy_metrics import window_energy_fee
    end=start+60.;ens=np.zeros((4,3));covered=0.;failure=[]
    for segment in payload['segments']:
        overlap=max(0.,min(end,segment['end_minute'])-max(start,segment['start_minute']))
        if not overlap:continue
        values=np.asarray(segment['unserved_kW'],float)
        if values.shape!=(4,3) or not np.isfinite(values).all():raise ValueError('Unknown actual ENS in observed branch interval')
        ens+=values*overlap/60.;covered+=overlap
        if segment['classification']['p0_guarantee_failure']:failure.append(segment['end_minute'])
    full=abs(covered-60.)<=1e-8
    fees=window_energy_fee(payload,*context,start,end)
    if fees['energy_fee_outcome_valid'] is not full:raise ValueError('Observed ENS/fee horizon masks differ')
    return dict(horizon_minutes=60,full_horizon=full,ENS_kWh_by_priority_carrier=ens.tolist() if full else None,
        P0_failure_in_window=1 if failure else 0 if full else None,actual_energy_fee_USD=fees['actual_energy_fee_USD'],observed_minutes=covered)

def _actual_ports_trace(payload,start):
    trace=[];weighted=np.zeros(6);covered=0.
    for s in payload['segments']:
        left=max(start,s['start_minute']);right=min(start+60.,s['end_minute'])
        if right<=left:continue
        ports=_ports(s['action']);weighted+=np.asarray(ports)*(right-left);covered+=right-left
        trace.append(dict(start_minute=left,end_minute=right,actual_ports=ports))
    return dict(actual_H60_ports_trace=trace,actual_H60_mean_ports=(weighted/covered).tolist() if covered else None,
        actual_H60_observed_minutes=covered,actual_H60_complete=abs(covered-60.)<=1e-8)

def _same(left,right,path='window'):
    if left is None or right is None:
        if left is not right:raise ValueError('Actual branch window unknown mask differs: '+path)
    elif isinstance(left,dict):
        for k in left:_same(left[k],right.get(k),path+'.'+k)
    elif isinstance(left,list):
        if not isinstance(right,list) or len(left)!=len(right):raise ValueError('Actual branch window shape differs: '+path)
        for i,(a,b) in enumerate(zip(left,right)):_same(a,b,path+'[%d]'%i)
    elif isinstance(left,bool):
        if left is not right:raise ValueError('Actual branch window completeness differs: '+path)
    elif not finite(right) or abs(float(left)-float(right))>1e-9:raise ValueError('Actual branch window differs: '+path)

def load_bound_branches(registration_path,branch_root):
    """Bind a complete D0-passed run; independently rescore its immutable bytes."""
    from tools.integrated_learning.run_control import validate_plan
    rp=Path(registration_path).resolve();registration=json.loads(rp.read_text(encoding='utf-8'));body=copy.deepcopy(registration);signature=body.pop('signature',None)
    if registration.get('schema')!='integrated.action_proxy_roots.v1' or canonical(body)!=signature:raise ValueError('Root registration was changed')
    control_ref=registration['control_plan'];cp=path_of(control_ref)
    if sha(cp)!=control_ref['sha256']:raise ValueError('Registered control-plan bytes changed')
    control,manifest=validate_plan(cp)
    if registration['selected_snapshots']!=control['snapshots']:raise ValueError('Snapshot selections changed after pre-outcome split')
    snapshots=control['snapshots'];roles=registration['root_roles'];specs={s['id']:s for s in manifest['scenarios']}
    if registration.get('seed')!=4101 or registration.get('targets')!=list(TARGETS) or registration.get('seeds')!=list(SEEDS):raise ValueError('Unregistered proxy methods/targets/seeds')
    if roles!=split_roots([s['root_family_id'] for s in snapshots]):raise ValueError('Pre-outcome root split rule changed')
    if any(s['role']!='pilot_train' or specs[s['scenario_id']]['experiment_role']!='pilot_train' for s in snapshots):raise ValueError('Only training-side source roots are allowed')
    support=verify_internal_support([specs[s['scenario_id']] for s in snapshots],roles)
    base=Path(branch_root).resolve();refs=[reference(rp),reference(cp)]+[reference(p) for p in (Path(__file__),Path(__file__).with_name('energy_metrics.py'),Path(__file__).with_name('offline.py'),ROOT/'tools/p2b_learning/risk.py')]
    if 'manifest_path' in control:refs.append(reference(ROOT/control['manifest_path']))
    def read(path):
        path=Path(path);value=json.loads(path.read_text(encoding='utf-8'));refs.append(reference(path));return value
    registered=read(base/'registered_plan.json')
    if registered!=control:raise ValueError('Branch run used a different registered control plan')
    report=read(base/'ACTION_BRANCH_REPORT.json');n=len(snapshots)
    if report.get('plan_signature')!=control['signature'] or report.get('snapshots_attempted')!=n or report.get('registered_snapshots')!=n or report.get('branches_attempted')!=3*n or report.get('capture_trajectories_attempted')!=n or report.get('all_attempted_D0_passed') is not True:
        raise ValueError('Incomplete registered branch/D0 denominators; complete coverage is required')
    if len(report.get('results',[]))!=n:raise ValueError('Complete per-snapshot report required')
    if {p.name for p in base.glob('snapshot_*') if p.is_dir()}!={f'snapshot_{i:03d}' for i in range(n)}:raise ValueError('Unexpected or missing branch snapshot directory')
    context=_load_energy_context();rows=[];group_roles={};raw_binding=[]
    for index,snapshot in enumerate(snapshots):
        folder=base/f'snapshot_{index:03d}';result=read(folder/'BRANCH_RESULT.json');spec=specs[snapshot['scenario_id']]
        if result!=report['results'][index]:raise ValueError('Branch report and actual snapshot report differ')
        if result.get('passed') is not True or result.get('D0_passed') is not True or result.get('future_for_policy') is not False or result.get('branches_attempted')!=3:raise ValueError('D0/current-only candidate gate failed')
        if result['scenario_id']!=spec['id'] or result['snapshot_minute']!=snapshot['snapshot_minute'] or snapshot['root_family_id']!=spec['root_family_id']:raise ValueError('Registered snapshot identity/time differs')
        d0=read(folder/'D0_comparison.json')
        if d0.get('passed') is not True or d0.get('differences'):raise ValueError('Original continuation D0 did not pass')
        baseline=read(base/f'baseline_reference_{index:03d}.json');baseline_path=Path(baseline['checkpoint_path'])
        baseline_path=baseline_path if baseline_path.is_absolute() else ROOT/baseline_path
        if any(baseline.get(k)!=v for k,v in dict(scenario_id=spec['id'],policy_id='secure_milp',manifest_signature=manifest['signature']).items()) or sha(baseline_path)!=baseline['checkpoint_sha256']:
            raise ValueError('Original parent baseline bytes/identity changed')
        refs.append(reference(baseline_path));capture=read(folder/'capture_vs_original.json')
        if capture.get('passed') is not True or capture.get('differences'):raise ValueError('Capture differed from actual original baseline')
        snapfile=folder/'execution_snapshot.json';refs.append(reference(snapfile))
        if sha(snapfile)!=result['execution_snapshot_sha256']:raise ValueError('Original execution snapshot bytes changed')
        perplan=read(folder/'plan.json')
        if any(perplan.get(k)!=v for k,v in dict(scenario_id=spec['id'],seed=manifest['options']['seed'],snapshot_step=snapshot['snapshot_step'],manifest_signature=manifest['signature'],counterfactual_enabled=True).items()):raise ValueError('Per-snapshot attempt plan changed')
        reference_payload=read(folder/'reference_capture.json');current=read(folder/'current_plan.json');candidates=read(folder/'candidates.json')
        if reference_payload['scenario']!=spec or reference_payload.get('seed')!=manifest['options']['seed']:raise ValueError('Captured exogenous scenario/seed differs')
        if candidates.get('future_used_for_generation') is not False or candidates['snapshot_minute']!=snapshot['snapshot_minute']:raise ValueError('Unregistered future candidate generation')
        ids=[c['candidate_id'] for c in candidates['candidates']]
        if ids!=['original_current_policy','GT_minus10pct_grid_request','ABS_minus10pct_request'] or len(result['outcomes'])!=3:raise ValueError('Registered three candidate denominator differs')
        t=snapshot['snapshot_minute'];history=[d for d in reference_payload['decisions'] if d['decision_time_min']<=t]
        root=spec['root_family_id'];role=roles[root];group=reference_payload['identity']['group_id']
        if group!=manifest['splits'][spec['id']]['group_id'] or spec.get('is_final_blind') or manifest['splits'][spec['id']].get('is_final_blind'):raise ValueError('Actual parent group/frozen identity differs')
        if group in group_roles and group_roles[group]!=role:raise ValueError('Actual family group crosses proxy train/validation')
        group_roles[group]=role
        for candidate,outcome in zip(candidates['candidates'],result['outcomes']):
            if candidate!=outcome['candidate']:raise ValueError('Candidate request/outcome binding differs')
            name='D0_original_continuation' if candidate['candidate_id']=='original_current_policy' else candidate['candidate_id']
            payload=read(folder/(name+'.json'))
            if payload['scenario']!=spec or payload.get('seed')!=manifest['options']['seed'] or payload.get('continuation_start_minute')!=t:raise ValueError('Branch exogenous stream/seed/clock differs')
            decisions=payload['decisions'];first=next((d for d in decisions if d['decision_time_min']==t),None)
            submitted=first is not None and first.get('requested_command')==candidate['request'];status=payload['summary']['status']
            if first is not None and first['features']!=history[-1]['features']:raise ValueError('Current branch observation differs')
            if not submitted and (status=='completed' or first is not None and first.get('requested_command') is not None):raise ValueError('Current branch request differs')
            if any(d.get('requested_command')!={'security':{'gt_loading_penalty_USD_per_kWh':.35}} for d in decisions if d['decision_time_min']>t and d.get('requested_command') is not None):raise ValueError('Branch continuation was not fixed secure')
            stored=next((w for w in outcome['windows'] if w['horizon_minutes']==60),None)
            if stored is None:raise ValueError('Registered H60 branch window missing')
            calculated=_window_outcome(payload,t,context);_same(calculated,stored)
            ident=dict(scenario_id=spec['id'],root_family_id=root,group_id=group,source_role='pilot_train',proxy_role=role,snapshot_minute=t,
                candidate_id=candidate['candidate_id'],snapshot_source=reference(folder/'BRANCH_RESULT.json')['path'])
            row=build_row(history,current,candidate['request'],calculated,ident,permitted_dispatch=first.get('permitted_command') if first else None,actual_dispatch=first.get('actual_execution') if first else None)
            row['postdecision_diagnostics'].update(_actual_ports_trace(payload,t))
            solver_errors=len(payload.get('solver_errors',[]));legacy_errors=len(payload.get('errors',[]));top_error=payload.get('error') is not None
            row.update(request_submission_observed=submitted,branch_status=status,error_records_count=solver_errors+legacy_errors+int(top_error),
                solver_error_records_count=solver_errors,legacy_error_records_count=legacy_errors,top_level_error_present=top_error,
                error_record_count_semantics='record representations, not unique exception count',
                fit_exclusion_reason='request_submission_unobserved' if not submitted else 'incomplete_five_target_horizon' if not row['fit_complete_five_targets'] else None)
            row['fit_complete_five_targets']=row['fit_complete_five_targets'] and submitted
            row['source_payload']=reference(folder/(name+'.json'));rows.append(row)
            raw_binding.append(dict(scenario_id=spec['id'],candidate_id=candidate['candidate_id'],snapshot_minute=t,branch_payload_sha256=canonical(payload),row_digest=canonical(row)))
    return dict(bound=True,registration=reference(rp),control_plan=reference(cp),branch_report=reference(base/'ACTION_BRANCH_REPORT.json'),
        rows=rows,rows_digest=canonical(rows),raw_binding=raw_binding,raw_binding_digest=canonical(raw_binding),raw_references=refs,
        complete_snapshots=n,root_roles=roles,raw_support_isolation=support,actual_source_role='pilot_train',scope='development offline proxy only, fixed secure continuation')

def verify_bindings(bound):
    if bound.get('bound') is not True or not bound.get('raw_references') or not bound.get('registration'):raise ValueError('Actual bound branch data required before fitting')
    if canonical(bound['rows'])!=bound.get('rows_digest') or canonical(bound['raw_binding'])!=bound.get('raw_binding_digest'):raise ValueError('Bound branch rows changed in memory')
    for r in bound['raw_references']:
        if sha(path_of(r))!=r['sha256']:raise ValueError('Bound branch/source bytes changed')
    from tools.integrated_learning.run_control import validate_plan
    validate_plan(path_of(bound['control_plan']))

def _failure_metrics(y,p):
    if not len(y):return dict(N=0,status='no_known_targets')
    if not np.isfinite(p).all():raise ValueError('Nonfinite proxy failure predictions')
    if np.any((p<0)|(p>1)):return dict(N=len(y),status='raw_regression_probability_outside_unit_interval',min=float(min(p)),max=float(max(p)),probabilities_not_clipped=True)
    y=np.asarray(y,int);alert=np.asarray(p)>=.5;tn,fp,fn,tp=confusion_matrix(y,alert,labels=[0,1]).ravel();both=len(set(y))==2
    return dict(N=len(y),single_class=not both,threshold=.5,threshold_scope='preassigned diagnostic; no threshold fitting',Brier=float(brier_score_loss(y,p)),
        PR_AUC=float(average_precision_score(y,p)) if both else None,ROC_AUC=float(roc_auc_score(y,p)) if both else None,
        confusion_matrix=[[int(tn),int(fp)],[int(fn),int(tp)]],precision=float(tp/(tp+fp)) if tp+fp else None,recall=float(tp/(tp+fn)) if tp+fn else None)

def run_action_proxy(bound,out):
    verify_bindings(bound);out=Path(out)
    if out.exists():raise FileExistsError('Preserve every proxy fit attempt; use a fresh output directory')
    rows=bound['rows'];roles={role:[r for r in rows if r['proxy_role']==role and r['fit_complete_five_targets']] for role in ROLES}
    if any(not roles[role] for role in ROLES):raise ValueError('No common complete five-target train/validation rows; unknown attempts remain retained')
    out.mkdir(parents=True);write(out/'raw_rows_and_masks.json',rows);write(out/'input_bindings.json',{k:v for k,v in bound.items() if k!='rows'})
    write(out/'FIT_PREREGISTRATION.json',dict(registry=registry(),seeds=SEEDS,root_roles=bound['root_roles'],registration=bound['registration'],
        fixed_continuation='secure_milp',horizon_minutes=60,development_proxy_only=True,online_controller=False,configuration_count_added=10,raw_support_isolation=bound['raw_support_isolation'],
        expected_estimator_fits=6,expected_target_fit_units=30,unknown_targets_not_zero=True,root_split_scope='registered pilot_train roots; internal validation is not independent final test',
        source_sha256={reference(p)['path']:sha(p) for p in (Path(__file__),ROOT/'tools/integrated_experiment/energy_metrics.py',ROOT/'tools/p2b_learning/risk.py')},
        baseline_unspecified_is_null_with_mask=True,loss_scale='training target mean/std, inverse original units',selection='three-seed mean internal-validation NRMSE; nonconstant training targets only'))
    prep=SequencePreprocessor().fit(roles['train']);prep_path=out/'train_only_preprocessor.json';write(prep_path,prep.to_dict())
    X={role:prep.transform(items).reshape(len(items),-1) for role,items in roles.items()};Y={role:np.asarray([r['targets'] for r in items],float) for role,items in roles.items()}
    fitted=out/'fitted_inputs.npz';np.savez_compressed(fitted,train_X=X['train'],train_y=Y['train'],validation_X=X['internal_validation'],validation_y=Y['internal_validation'])
    ledger=out/'fit_attempts.jsonl';trials=[]
    for config in registry():
        for seed in SEEDS:
            trial_dir=out/(config['config_id']+'__seed'+str(seed));identity=dict(task='action_conditioned_proxy',config_id=config['config_id'],recipe=config['recipe'],seed=seed,
                target_names=list(TARGETS),fitted_input_sha256=sha(fitted),registered_roots_sha256=bound['registration']['sha256'])
            def dofit():
                validation,diag=fit_model(config['algorithm'],seed,X['train'],Y['train'],X['internal_validation'],trial_dir);evaluation={}
                reloaded_prep=SequencePreprocessor.from_dict(json.loads(prep_path.read_text(encoding='utf-8')))
                for role,items in roles.items():
                    value=reload_model(trial_dir,X[role]);raw=reload_model(trial_dir,reloaded_prep.transform(items).reshape(len(items),-1))
                    error=float(np.max(abs(value-raw))) if len(value) else 0.
                    if error>1e-12:raise ValueError('Original legal raw-input proxy reload differs')
                    np.save(trial_dir/(role+'_predictions.npy'),value)
                    write(trial_dir/(role+'_predictions.json'),[dict(scenario_id=r['scenario_id'],root_family_id=r['root_family_id'],candidate_id=r['candidate_id'],snapshot_minute=r['snapshot_minute'],
                        truth={k:r['targets'][j] for j,k in enumerate(TARGETS)},prediction={k:float(value[i,j]) for j,k in enumerate(TARGETS)}) for i,r in enumerate(items)])
                    evaluation[role]=dict(metrics={k:consequence_metrics(Y[role][:,j],value[:,j],Y['train'][:,j],UNITS[k]) for j,k in enumerate(TARGETS)},
                        P0_failure=_failure_metrics(Y[role][:,3],value[:,3]),raw_input_reload_max_abs_error=error)
                write(trial_dir/'provenance.json',dict(config=config,train_root_ids=sorted({r['root_family_id'] for r in roles['train']}),validation_root_ids=sorted({r['root_family_id'] for r in roles['internal_validation']}),
                    fit_row_ids=[[r['scenario_id'],r['snapshot_minute'],r['candidate_id']] for r in roles['train']],validation_row_ids=[[r['scenario_id'],r['snapshot_minute'],r['candidate_id']] for r in roles['internal_validation']],
                    preprocessor=reference(prep_path),fitted_inputs=reference(fitted),registration=bound['registration'],input_rows_digest=bound['rows_digest'],
                    artifacts=[reference(p) for p in sorted(trial_dir.iterdir()) if p.is_file()],source_sha256=sha(__file__)))
                return dict(diagnostics=diag,evaluation=evaluation)
            result=logged_fit(ledger,identity,dofit);trial=dict(**identity,status=result['status'],trial_directory=trial_dir.relative_to(out).as_posix(),elapsed_seconds=result['elapsed_seconds'])
            trial.update(result['value'] if result['status']=='completed' else {k:v for k,v in result.items() if k!='status'});trial_dir.mkdir(parents=True,exist_ok=True);write(trial_dir/'trial.json',trial);trials.append(trial)
    verify_bindings(bound);counts=[json.loads(v) for v in ledger.read_text(encoding='utf-8').splitlines()];started=[r for r in counts if r['event']=='started'];closed=[r for r in counts if r['event']!='started']
    if len(started)!=len(closed):raise ValueError('Actual proxy fit completion count differs')
    candidates=[]
    for config in registry():
        matches=[t for t in trials if t['config_id']==config['config_id'] and t['status']=='completed'];scores=[m['normalized_RMSE'] for t in matches for m in t['evaluation']['internal_validation']['metrics'].values() if m['normalized_RMSE'] is not None]
        if len(matches)==3 and scores:candidates.append(dict(config_id=config['config_id'],mean_internal_validation_NRMSE=float(np.mean(scores))))
    masks={role:{k:dict(known=sum(r['target_mask'][j] for r in rows if r['proxy_role']==role),unknown=sum(not r['target_mask'][j] for r in rows if r['proxy_role']==role)) for j,k in enumerate(TARGETS)} for role in ROLES}
    report=dict(registration=bound['registration'],trials=trials,actual_fit_attempts=len(started),actual_target_fit_units=sum(len(r['target_names']) for r in started),failed_fit_attempts=sum(t['status']=='failed' for t in trials),
        candidate_configurations=candidates,selected_configuration=min(candidates,key=lambda c:c['mean_internal_validation_NRMSE']) if candidates else None,representative_seed_preassigned=4101,
        rows_by_role={role:sum(r['proxy_role']==role for r in rows) for role in ROLES},complete_fit_rows_by_role={role:len(items) for role,items in roles.items()},
        independent_roots_by_role={role:len({r['root_family_id'] for r in rows if r['proxy_role']==role}) for role in ROLES},target_masks=masks,
        action_diversity=action_diversity(rows),training_target_std={k:float(Y['train'][:,j].std()) for j,k in enumerate(TARGETS)},
        development_proxy_only=True,online_controller=False,no_environment_solver_calls=True,future_scripts_not_predictors=True,
        limit='Few same-state action variants and incomplete causal state limit identifiability; action projection may remove most requested diversity; internal root holdout is not final blind',
        reused_recipes=['consequence_mean','consequence_RF'],new_input_target_configurations=10,global_configurations_after_addition=49)
    write(out/'ACTION_PROXY_REPORT.json',report);return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--registration',required=True);p.add_argument('--branches',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    report=run_action_proxy(load_bound_branches(a.registration,a.branches),a.out)
    print(json.dumps(dict(actual_fit_attempts=report['actual_fit_attempts'],target_units=report['actual_target_fit_units'],failed=report['failed_fit_attempts']),ensure_ascii=False))
    if report['failed_fit_attempts']:raise SystemExit(1)
if __name__=='__main__':main()
