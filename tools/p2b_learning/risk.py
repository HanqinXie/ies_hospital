"""Conditional P2B first-observed-failure development comparison.

Both RF and GRU consume the identical latest four legal observation vectors and
padding masks. Scenario scripts, labels and posterior results are never inputs.
"""
import argparse
from collections import Counter
import copy
import csv
import hashlib
import json
from pathlib import Path
import time
import traceback
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score,roc_auc_score,brier_score_loss,confusion_matrix
from .regression import candidate_registry,parse_bool,learning_preflight,SEEDS,_write
from .provenance import build_fit_provenance


def payload_digest(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()


def risk_payload_preflight(manifest,payloads,audit_evidence):
    """Bind pilot payload content and current checkpoint bytes to final audit."""
    audit_path=Path(audit_evidence['path'])
    if hashlib.sha256(audit_path.read_bytes()).hexdigest()!=audit_evidence['sha256']:raise ValueError('audit hash mismatch before payload binding')
    audit=json.loads(audit_path.read_text(encoding='utf-8'))
    if audit.get('passed') is not True or audit.get('complete_budget') is not True or audit.get('manifest_signature')!=manifest['signature']:raise ValueError('payload audit is not the final manifest audit')
    specs={s['id']:s for s in manifest['scenarios']}
    expected_all={(sid,policy) for sid in specs for policy in manifest['policies']}
    expected={(sid,policy) for sid,policy in expected_all if specs[sid]['experiment_role'] in ('pilot_train','pilot_validation')}
    entries=audit.get('checkpoint_digests',[])
    entry_keys=[(e['scenario_id'],e['policy_id']) for e in entries]
    if len(entry_keys)!=len(expected_all) or len(set(entry_keys))!=len(entry_keys) or set(entry_keys)!=expected_all:raise ValueError('audited checkpoint digest coverage is incomplete or duplicate')
    by_key={key:e for key,e in zip(entry_keys,entries)}
    actual=[]
    for payload in payloads:
        summary=payload.get('summary',{});identity=payload.get('identity')
        policy=identity.get('policy_id') if identity else payload.get('policy_id',summary.get('policy_id'))
        actual.append((payload.get('scenario',{}).get('id'),policy))
    if len(actual)!=len(expected) or len(set(actual))!=len(actual) or set(actual)!=expected:raise ValueError('risk payload pair set is duplicate, missing or unowned')
    checkpoint_root=Path(audit_evidence.get('checkpoint_root',audit_path.resolve().parents[2]))
    verified=[];empty_unusable=0
    for payload,key in zip(payloads,actual):
        sid,policy=key;spec=specs[sid];entry=by_key[key]
        if payload['scenario']!=spec:raise ValueError('payload scenario differs from frozen manifest')
        summary=payload.get('summary',{});identity=payload.get('identity');decisions=payload.get('decisions',[])
        if summary.get('scenario_id')!=sid or summary.get('policy_id')!=policy:raise ValueError('payload summary pair identity mismatch')
        empty_error=not decisions and summary.get('status') in ('orchestration_error','interrupted_unknown')
        if identity is None:
            if not empty_error:raise ValueError('payload missing observation identity')
            empty_unusable+=1
        else:
            required={'scenario_id':sid,'policy_id':policy,'continuation_policy_id':policy,'group_id':manifest['splits'][sid]['group_id'],'manifest_signature':manifest['signature']}
            for field in ['family_id','parent_template_id','base_state_id','exogenous_stream_id','distribution_id']:
                if field in spec:required[field]=spec[field]
            for field in ['execution_contract_id','assumption_set_id','simulator_hash','schemas']:
                if field in manifest:required[field]=manifest[field]
            required.update(manifest['splits'][sid])
            if any(identity.get(k)!=v for k,v in required.items()):raise ValueError('payload identity/role/group differs from manifest')
            for decision in decisions:
                if any(decision.get(k)!=v for k,v in required.items()):raise ValueError('decision row identity differs from manifest')
        if payload.get('seed')!=manifest['options']['seed'] and not(empty_error and 'seed' not in payload):raise ValueError('payload initialization seed differs from manifest')
        digest=payload_digest(payload)
        if digest!=entry.get('payload_sha256'):raise ValueError('payload canonical digest differs from audited checkpoint')
        path=Path(entry['path'])
        if not path.is_absolute():path=checkpoint_root/path
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry.get('checkpoint_sha256'):raise ValueError('checkpoint bytes differ from final audit')
        verified.append({'scenario_id':sid,'policy_id':policy,'checkpoint_sha256':entry['checkpoint_sha256'],'payload_sha256':digest})
    return {'verified_pilot_payloads':len(verified),'empty_unusable_error_payloads':empty_unusable,'verified_digest_list_sha256':payload_digest(verified),'audit_checkpoint_digests_bound':True,'input_hashes':verified}


def build_sequences(payloads,assignments,policy):
    records=[];seen=set()
    for payload in payloads:
        spec=payload['scenario'];sid=spec['id']
        actual_policy=payload.get('policy_id',payload.get('summary',{}).get('policy_id',payload.get('identity',{}).get('policy_id')))
        if actual_policy!=policy or spec['experiment_role'] not in ('pilot_train','pilot_validation'):continue
        if (sid,actual_policy) in seen:raise ValueError('duplicate payload pair cannot create repeated risk rows')
        seen.add((sid,actual_policy))
        decisions=sorted(payload['decisions'],key=lambda d:d['decision_time_min'])
        if len({d['decision_time_min'] for d in decisions})!=len(decisions):raise ValueError('duplicate decision times')
        feature_keys=None
        for i,d in enumerate(decisions):
            if feature_keys is None:feature_keys=set(d['features'])
            if set(d['features'])!=feature_keys or set(d['feature_metadata'])!=feature_keys:raise ValueError('inconsistent feature schema')
            if any(v['available_time_min']>d['decision_time_min'] for v in d['feature_metadata'].values()):raise ValueError('Future feature availability')
            start,end=spec.get('score_interval_minutes',[60,420]);t=d['decision_time_min']
            label=d['labels']['60']
            if not start<=t<end or not label['valid'] or not label['at_risk']:continue
            if label['value'] not in (0,1):raise ValueError('valid binary label required')
            selected=decisions[max(0,i-3):i+1]
            pad=4-len(selected);sequence=[{k:None for k in feature_keys} for _ in range(pad)]+[copy.deepcopy(row['features']) for row in selected]
            previous=[s for s in payload.get('segments',[]) if s['end_minute']<=t+1e-9]
            abnormal=False
            if previous:
                segment=previous[-1]
                abnormal=bool(np.any(np.asarray(segment.get('unserved_kW',np.zeros((4,3))))[0]>1e-8) or segment.get('violation',{}).get('P0',False))
            summary=payload['summary']
            records.append({'scenario_id':sid,'policy_id':policy,'root_family_id':spec['root_family_id'],'group_id':assignments[sid]['group_id'],'role':spec['experiment_role'],'decision_time_min':float(t),'label':int(label['value']),'sequence':sequence,'padding_mask':[0]*pad+[1]*len(selected),'first_interval':d.get('first_failure_observation_interval'),'known_abnormal_at_decision':abnormal,'trajectory_known_negative':summary.get('status')=='completed' and parse_bool(summary.get('p0_failed_any')) is False,'observation_count':len(selected)})
    return records


def risk_gate(records):
    row_keys=[(r['scenario_id'],r['policy_id'],r['decision_time_min']) for r in records]
    if len(set(row_keys))!=len(row_keys):raise ValueError('duplicate risk decision rows cannot inflate sufficiency')
    roles={};reasons=[]
    for role,minimum_families,minimum_groups,min_positive_rows in [('pilot_train',10,5,20),('pilot_validation',5,2,5)]:
        rows=[r for r in records if r['role']==role]
        positive=[r for r in rows if r['label']==1];negative=[r for r in rows if r['label']==0]
        pf={r['root_family_id'] for r in positive};nf={r['root_family_id'] for r in negative}
        ngood={r['root_family_id'] for r in negative if r['trajectory_known_negative']}-pf
        pg={r['group_id'] for r in positive};ng={r['group_id'] for r in negative if r['root_family_id'] in ngood}
        counts={'positive_rows':len(positive),'negative_rows':len(negative),'positive_root_families':len(pf),'negative_window_root_families':len(nf),'positive_negative_window_family_overlap':len(pf&nf),'negative_only_known_root_families':len(ngood),'positive_groups':len(pg),'negative_only_known_groups':len(ng),'positive_family_ids':sorted(pf),'negative_only_known_family_ids':sorted(ngood)}
        roles[role]=counts
        if len(pf)<minimum_families:reasons.append(role+': too few positive root families')
        if len(ngood)<minimum_families:reasons.append(role+': too few known negative root families')
        if len(pg)<minimum_groups:reasons.append(role+': too few positive groups')
        if len(ng)<minimum_groups:reasons.append(role+': too few known negative groups')
        if len(positive)<min_positive_rows:reasons.append(role+': too few positive rows')
        if len(negative)<20:reasons.append(role+': too few negative rows')
    tr=[r for r in records if r['role']=='pilot_train'];va=[r for r in records if r['role']=='pilot_validation']
    for field in ['group_id','root_family_id']:
        if {r[field] for r in tr}&{r[field] for r in va}:raise ValueError('risk train/validation '+field+' leakage')
    return {'eligible':not reasons,'reasons':reasons,'roles':roles,'negative_family_interpretation':'known completed no-failure families, excluding any family with positive windows; earlier negative windows of a positive family do not create a negative accident','scope':'development sufficiency only, not statistical proof'}


class SequencePreprocessor:
    def fit(self,records):
        rows=[row for record in records for row,mask in zip(record['sequence'],record['padding_mask']) if mask]
        if not rows:raise ValueError('no observed training inputs')
        self.keys=sorted(rows[0]);self.numeric={};self.categorical={}
        if any(set(r)!=set(self.keys) for r in rows):raise ValueError('training feature schema differs')
        for key in self.keys:
            values=[r[key] for r in rows if r[key] is not None]
            if any(isinstance(v,str) for v in values):self.categorical[key]=sorted(set(str(v) for v in values))+['__MISSING__']
            else:
                v=np.asarray(values,float);v=v[np.isfinite(v)]
                median=float(np.median(v)) if len(v) else 0.;mean=float(v.mean()) if len(v) else 0.;std=float(v.std()) if len(v) else 1.
                self.numeric[key]={'median':median,'mean':mean,'scale':std if std>1e-12 else 1.}
        return self

    def transform(self,records):
        result=[]
        for record in records:
            seq=[]
            for row,mask in zip(record['sequence'],record['padding_mask']):
                if set(row)!=set(self.keys):raise ValueError('prediction feature schema differs')
                vector=[]
                for key in self.keys:
                    value=row[key]
                    if key in self.numeric:
                        stat=self.numeric[key];missing=value is None or not np.isfinite(float(value))
                        vector.extend([(float(value) if not missing else stat['median'])-stat['mean'],float(missing)])
                        vector[-2]/=stat['scale']
                    else:
                        word='__MISSING__' if value is None else str(value)
                        vector.extend(float(word==category) for category in self.categorical[key])
                if not mask:vector=[0.]*len(vector)
                seq.append(vector+[float(mask)])
            result.append(seq)
        return np.asarray(result,dtype=np.float32)

    def to_dict(self):return {'keys':self.keys,'numeric':self.numeric,'categorical':self.categorical,'history_length':4,'last_channel':'padding_mask','fit_role':'pilot_train'}

    @classmethod
    def from_dict(cls,metadata):
        if metadata.get('history_length')!=4 or metadata.get('last_channel')!='padding_mask' or metadata.get('fit_role')!='pilot_train':raise ValueError('invalid preprocessor schema')
        obj=cls();obj.keys=metadata['keys'];obj.numeric=metadata['numeric'];obj.categorical=metadata['categorical']
        if set(obj.keys)!=set(obj.numeric)|set(obj.categorical):raise ValueError('invalid preprocessor feature coverage')
        return obj


def choose_threshold(y,probability):
    y=np.asarray(y,int);p=np.asarray(probability,float)
    if len(np.unique(y))<2:return {'threshold':None,'status':'single_class_not_estimable','recall':None,'precision':None}
    options=[]
    for threshold in np.unique(np.r_[0.,p,1.]):
        pred=p>=threshold;tp=int(np.sum(pred&(y==1)));fp=int(np.sum(pred&(y==0)))
        recall=tp/int(np.sum(y==1));precision=tp/(tp+fp) if tp+fp else 0.
        if recall>=.9:options.append((precision,float(threshold),recall))
    if not options:return {'threshold':None,'status':'recall_target_unreachable','recall':None,'precision':None}
    precision,threshold,recall=max(options)
    return {'threshold':threshold,'status':'validation_selected_development_estimate','recall':recall,'precision':precision}


def risk_metrics(y,probability,threshold,rows):
    y=np.asarray(y,int);p=np.asarray(probability,float)
    both=len(np.unique(y))==2
    result={'N':len(y),'positive_rows':int(y.sum()),'PR_AUC_average_precision':float(average_precision_score(y,p)) if both else None,'ROC_AUC':float(roc_auc_score(y,p)) if both else None,'Brier':float(brier_score_loss(y,p)),'metric_scope':'development validation with its own model/threshold selection; not blind performance'}
    if threshold is None:return result
    alert=p>=threshold;tn,fp,fn,tp=confusion_matrix(y,alert,labels=[0,1]).ravel()
    result.update(threshold=threshold,confusion_matrix=[[int(tn),int(fp)],[int(fn),int(tp)]],precision=float(tp/(tp+fp)) if tp+fp else None,recall=float(tp/(tp+fn)) if tp+fn else None)
    positive_families={r['root_family_id'] for r,label in zip(rows,y) if label==1}
    detected={r['root_family_id'] for r,label,a in zip(rows,y,alert) if label==1 and a}
    strict={r['root_family_id'] for r,label,a in zip(rows,y,alert) if label==1 and a and r['first_interval'] is not None and r['decision_time_min']<r['first_interval'][0]}
    same={r['root_family_id'] for r,label,a in zip(rows,y,alert) if label==1 and a and r['first_interval'] is not None and r['first_interval'][0]<=r['decision_time_min']<r['first_interval'][1]}-strict
    negative_families={r['root_family_id'] for r in rows if r.get('trajectory_known_negative',r.get('first_interval') is None)}-positive_families
    false_alarm={r['root_family_id'] for r,a in zip(rows,alert) if a and r['root_family_id'] in negative_families}
    result.update(positive_family_denominator=len(positive_families),detected_families=len(detected),family_recall=len(detected)/len(positive_families) if positive_families else None,strict_early_detected_families=len(strict),same_failure_interval_detected_families=len(same),missed_family_ids=sorted(positive_families-detected),known_negative_family_denominator=len(negative_families),false_alarm_families=len(false_alarm),false_alarm_family_ids=sorted(false_alarm),valid_negative_decision_hours=float(np.sum(y==0)*.25),false_alarm_negative_decision_hours=float(np.sum(alert&(y==0))*.25),alerts_with_already_known_abnormality=sum(bool(a and r['known_abnormal_at_decision']) for r,a in zip(rows,alert)))
    first_alerts=[]
    positive_scenarios={r['scenario_id'] for r,label in zip(rows,y) if label==1}
    for sid in sorted(positive_scenarios):
        candidates=[r for r,label,a in zip(rows,y,alert) if r['scenario_id']==sid and label==1 and a]
        if not candidates:continue
        first=min(candidates,key=lambda r:r['decision_time_min']);interval=first['first_interval']
        first_alerts.append({'scenario_id':sid,'root_family_id':first['root_family_id'],'decision_time_min':first['decision_time_min'],'first_failure_interval':interval,'lead_to_interval_start_min':interval[0]-first['decision_time_min'] if interval else None,'lead_to_classification_observation_min':interval[1]-first['decision_time_min'] if interval else None,'known_abnormal_at_alert':first['known_abnormal_at_decision']})
    result['first_positive_window_alerts']=first_alerts
    result['missed_scenario_ids']=sorted(positive_scenarios-{r['scenario_id'] for r in first_alerts})
    return result


def make_gru(input_size,hidden):
    import torch
    class GRU(torch.nn.Module):
        def __init__(self):
            super().__init__();self.gru=torch.nn.GRU(input_size,hidden,batch_first=True);self.output=torch.nn.Linear(hidden,1)
        def forward(self,x):
            sequence,_=self.gru(x);return self.output(sequence[:,-1]).squeeze(-1)
    return GRU()


def _require_cuda_device():
    import torch
    if not torch.cuda.is_available():raise RuntimeError('CUDA is required for existing risk GRU training and inference')
    return torch.device('cuda:0')


def fit_risk_candidate(config,seed,x,y,vx,vy,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);start=time.perf_counter()
    if config['class']=='RF':
        model=RandomForestClassifier(n_estimators=200,max_depth=config['max_depth'],min_samples_leaf=config['min_samples_leaf'],random_state=seed,n_jobs=1).fit(x.reshape(len(x),-1),y)
        joblib.dump(model,directory/'model.joblib',compress=3)
        prediction_start=time.perf_counter()
        pred=model.predict_proba(vx.reshape(len(vx),-1))[:,list(model.classes_).index(1)]
        prediction_seconds=time.perf_counter()-prediction_start
        restored=joblib.load(directory/'model.joblib');reload_pred=restored.predict_proba(vx.reshape(len(vx),-1))[:,list(restored.classes_).index(1)]
        training=[];fit_device='cpu'
    else:
        import torch
        device=_require_cuda_device();torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)
        model=make_gru(x.shape[-1],config['hidden_size']).to(device);optimizer=torch.optim.Adam(model.parameters(),lr=.001)
        loss_fn=torch.nn.BCEWithLogitsLoss();xt=torch.tensor(x,device=device);yt=torch.tensor(y,dtype=torch.float32,device=device);vxt=torch.tensor(vx,device=device);vyt=torch.tensor(vy,dtype=torch.float32,device=device)
        model_devices={str(parameter.device) for parameter in model.parameters()}
        tensor_devices={'training_X':str(xt.device),'training_y':str(yt.device),'validation_X':str(vxt.device),'validation_y':str(vyt.device)}
        if model_devices!={'cuda:0'} or any(actual!='cuda:0' for actual in tensor_devices.values()):
            raise RuntimeError(f'risk GRU model and fit tensors must use cuda:0: model={sorted(model_devices)}, tensors={tensor_devices}')
        fit_device=next(iter(model_devices))
        training=[];best=float('inf');best_state=None;stale=0;generator=torch.Generator().manual_seed(seed)
        for epoch in range(config['epochs']):
            model.train();permutation=torch.randperm(len(xt),generator=generator);train_loss=0.
            for begin in range(0,len(xt),64):
                idx=permutation[begin:begin+64].to(device);optimizer.zero_grad();loss=loss_fn(model(xt[idx]),yt[idx]);loss.backward();optimizer.step();train_loss+=float(loss.detach())*len(idx)
            model.eval()
            with torch.no_grad():valid_loss=float(loss_fn(model(vxt),vyt))
            training.append({'epoch':epoch+1,'train_BCE':train_loss/len(xt),'validation_BCE':valid_loss})
            if valid_loss<best:
                best=valid_loss;best_state=copy.deepcopy(model.state_dict());stale=0
            else:stale+=1
            if stale>=config['patience']:break
        model.load_state_dict(best_state);model.eval()
        prediction_start=time.perf_counter()
        with torch.no_grad():pred=torch.sigmoid(model(vxt)).cpu().numpy()
        prediction_seconds=time.perf_counter()-prediction_start
        torch.save(best_state,directory/'state_dict.pt')
        restored=make_gru(x.shape[-1],config['hidden_size']).to(device);restored.load_state_dict(torch.load(directory/'state_dict.pt',map_location='cpu',weights_only=True));restored.eval()
        with torch.no_grad():reload_pred=torch.sigmoid(restored(vxt)).cpu().numpy()
    error=float(np.max(np.abs(pred-reload_pred)))
    if error>1e-12:raise ValueError('risk serialized prediction mismatch')
    return pred,{'fit_seconds_including_validation_and_serialization':time.perf_counter()-start,'model_validation_prediction_seconds':prediction_seconds,'training_history':training,'serialization_max_abs_error':error,'input_shape':list(x.shape),'algorithm':config['class'],'fit_device':fit_device}


def reload_risk_probabilities(directory,raw_records):
    """Reload preprocessing and weights, then predict from raw legal records."""
    directory=Path(directory);metadata=json.loads((directory/'provenance.json').read_text(encoding='utf-8'))
    prep_path=(directory/metadata['preprocessor_path']).resolve()
    if hashlib.sha256(prep_path.read_bytes()).hexdigest()!=metadata['preprocessor_sha256']:raise ValueError('preprocessor hash mismatch')
    for entry in metadata['files']:
        if hashlib.sha256((directory/entry['path']).read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('risk model artifact hash mismatch')
    preprocessor=SequencePreprocessor.from_dict(json.loads(prep_path.read_text(encoding='utf-8')))
    values=preprocessor.transform(raw_records)
    if values.shape[-1]!=metadata['input_size']:raise ValueError('preprocessor encoded dimension mismatch')
    config=metadata['config']
    if config['class']=='RF':
        model=joblib.load(directory/'model.joblib')
        return model.predict_proba(values.reshape(len(values),-1))[:,list(model.classes_).index(1)]
    import torch
    device=_require_cuda_device()
    model=make_gru(values.shape[-1],config['hidden_size']).to(device);model.load_state_dict(torch.load(directory/'state_dict.pt',map_location='cpu',weights_only=True));model.eval()
    with torch.no_grad():return torch.sigmoid(model(torch.tensor(values,device=device))).cpu().numpy()


def run_risk(manifest,score_rows,study_distribution,payloads,output_dir,audit_evidence):
    preflight=learning_preflight(manifest,score_rows,study_distribution,audit_evidence)
    payload_preflight=risk_payload_preflight(manifest,payloads,audit_evidence)
    implementation_provenance=build_fit_provenance(candidate_registry(),SEEDS)
    out=Path(output_dir)
    if out.exists():raise FileExistsError('new immutable risk output directory required')
    out.mkdir(parents=True);reports=[]
    _write(out/'FIT_RUN_MANIFEST.json',implementation_provenance)
    for policy in manifest['policies']:
        rows=build_sequences(payloads,manifest['splits'],policy);gate=risk_gate(rows)
        folder=out/policy;folder.mkdir();_write(folder/'sufficiency_gate.json',gate)
        row_meta=[{k:v for k,v in r.items() if k not in ('sequence','padding_mask')} for r in rows]
        _write(folder/'row_manifest.json',row_meta)
        result={'policy':policy,'gate':gate,'status':'insufficient_data','no_fitting_when_gate_fails':True}
        if not gate['eligible']:
            reports.append(result);continue
        train=[r for r in rows if r['role']=='pilot_train'];valid=[r for r in rows if r['role']=='pilot_validation']
        preprocessor=SequencePreprocessor().fit(train);x=preprocessor.transform(train)
        preprocessing_start=time.perf_counter();vx=preprocessor.transform(valid);preprocessing_seconds=time.perf_counter()-preprocessing_start
        y=np.array([r['label'] for r in train]);vy=np.array([r['label'] for r in valid])
        _write(folder/'train_only_preprocessor.json',preprocessor.to_dict());np.savez_compressed(folder/'fitted_inputs.npz',train_X=x,train_y=y,validation_X=vx,validation_y=vy)
        trials=[]
        for config in [c for c in candidate_registry() if c['task']=='risk']:
            for seed in SEEDS:
                trial_dir=folder/f'{config["config_id"]}__seed{seed}'
                trial={'config_id':config['config_id'],'seed':seed,'status':'attempted'}
                try:
                    pred,diag=fit_risk_candidate(config,seed,x,y,vx,vy,trial_dir)
                    diag['validation_preprocessing_seconds']=preprocessing_seconds
                    diag['validation_preprocessing_plus_prediction_seconds']=preprocessing_seconds+diag['model_validation_prediction_seconds']
                    threshold=choose_threshold(vy,pred);trial.update(status='completed',diagnostics=diag,threshold=threshold,metrics=risk_metrics(vy,pred,threshold['threshold'],valid))
                    np.save(trial_dir/'validation_probabilities.npy',pred)
                    _write(trial_dir/'provenance.json',{'manifest_signature':manifest['signature'],'config':config,'seed':seed,'requested_device':'cuda:0' if config['class']=='GRU' else 'cpu','training_device':diag['fit_device'],'fit_dataset_ids':sorted({r['scenario_id'] for r in train}),'fit_row_ids':[[r['scenario_id'],r['decision_time_min']] for r in train],'implementation_provenance':implementation_provenance,'fitted_input_npz_sha256':hashlib.sha256((folder/'fitted_inputs.npz').read_bytes()).hexdigest(),'train_only_preprocessing':True,'feature_source':'decision.features only; 4 history vectors + padding masks','source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'input_size':x.shape[-1],'preprocessor_path':'../train_only_preprocessor.json','preprocessor_sha256':hashlib.sha256((folder/'train_only_preprocessor.json').read_bytes()).hexdigest(),'audited_payload_digest_list_sha256':payload_preflight['verified_digest_list_sha256'],'files':[{'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in trial_dir.iterdir() if p.is_file()]})
                    raw_reload=reload_risk_probabilities(trial_dir,valid)
                    trial['diagnostics']['raw_record_reload_max_abs_error']=float(np.max(np.abs(raw_reload-pred)))
                    if trial['diagnostics']['raw_record_reload_max_abs_error']>1e-12:raise ValueError('raw observation preprocessing/weight reload mismatch')
                except Exception as exc:trial.update(status='failed',error_type=type(exc).__name__,traceback=traceback.format_exc())
                trial_dir.mkdir(exist_ok=True);_write(trial_dir/'trial.json',trial);trials.append(trial)
        candidates=[]
        for config in [c for c in candidate_registry() if c['task']=='risk']:
            good=[t for t in trials if t['config_id']==config['config_id'] and t['status']=='completed']
            if len(good)==3:candidates.append({'config_id':config['config_id'],'mean_validation_PR_AUC_average_precision':float(np.mean([t['metrics']['PR_AUC_average_precision'] for t in good]))})
        result.update(status='fitted' if candidates else 'all_candidates_failed',trials=trials,selection={'rule':'mean development validation average precision across three fixed seeds','candidates':candidates,'selected':max(candidates,key=lambda r:r['mean_validation_PR_AUC_average_precision']) if candidates else None})
        reports.append(result)
    report={'reports':reports,'audit_evidence':copy.deepcopy(audit_evidence),'preflight':preflight,'payload_preflight':payload_preflight,'risk_horizon_minutes':60,'score_interval_minutes':[60,420],'input_window_observations':4,'candidate_registry':candidate_registry(),'seeds':list(SEEDS),'strict_early_definition':'alert decision precedes first classified-failure subinterval start; t<T_first_end alone is insufficient','no_hospital_environment_calls':True}
    _write(out/'RISK_REPORT.json',report);return report


def main():
    parser=argparse.ArgumentParser()
    for key in ['manifest','scores','study-distribution','audit-receipt','out']:parser.add_argument('--'+key,required=True)
    args=parser.parse_args();manifest_path=Path(args.manifest)
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'));distribution=json.loads(Path(args.study_distribution).read_text(encoding='utf-8'));receipt=json.loads(Path(args.audit_receipt).read_text(encoding='utf-8'))
    with Path(args.scores).open(encoding='utf-8-sig',newline='') as f:score_rows=list(csv.DictReader(f))
    learning_preflight(manifest,score_rows,distribution,receipt)
    from tools.p2b_experiment.runner import load_payload
    specs={s['id']:s for s in manifest['scenarios']};payloads=[]
    for shard in manifest['p2b_shards']:
        for sid in shard['scenario_ids']:
            if specs[sid]['experiment_role'] not in ('pilot_train','pilot_validation'):continue
            for policy in manifest['policies']:
                payloads.append(load_payload(manifest_path.parent/'shards'/shard['id'],sid,policy,manifest['signature']))
    run_risk(manifest,score_rows,distribution,payloads,args.out,receipt)


if __name__=='__main__':main()
