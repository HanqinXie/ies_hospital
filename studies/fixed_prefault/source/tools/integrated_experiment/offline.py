"""Audited, development-only risk and consequence comparison.

Online-risk inputs are four legal observation frames. The separate consequence
proxy may read the complete external specification and annual background; it
is never an online alarm or controller. Unknown labels and outcomes stay null.
"""
from __future__ import annotations
import argparse
from collections import Counter
from importlib import metadata as package_metadata
import copy,gzip,hashlib,json,sys,time,traceback
from pathlib import Path
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier,RandomForestRegressor
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import average_precision_score,roc_auc_score,brier_score_loss,confusion_matrix

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.p2b_learning.risk import SequencePreprocessor,choose_threshold,fit_risk_candidate,make_gru
from .firstformal_warning import warning_view
SEEDS=(4101,4102,4103)
ROLES=('pilot_train','pilot_validation','development_boundary')
TARGETS=tuple(f'P{p}_{c}_ENS_kWh' for p in range(4) for c in ('e','h','c'))+('actual_energy_fee_USD',)+tuple(f'P{p}_max_temperature_C' for p in range(3))
TARGET_UNITS={k:'USD' if k=='actual_energy_fee_USD' else 'degC' if k.endswith('_C') else 'kWh' for k in TARGETS}
EXPENSE_SEMANTICS=dict(target='actual_energy_fee_USD',source='actual GT/grid/diesel/boiler port output and original illustrative tariffs',
    excludes='security penalty and unused-steam tie-break',accounting_shadow='cost_USD',accounting_shadow_is_energy_fee=False,
    tariff_scope='illustrative research assumptions, not measured hospital tariffs')
STATIC_KEYS=('grid_import_limit_kW','control_available')+tuple(f'P{p}_{c}_demand_kW' for p in range(4) for c in ('e','h','c'))
FORBIDDEN={'stage','scenario_id','family_id','root_family_id','group_id','condition_id','time_block_id','policy_id','first_failure_observed_minute','future_P0_failure_H','label','labels'}

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()
def write(p,value):Path(p).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def reference(p):
    p=Path(p).resolve()
    try:path=p.relative_to(ROOT).as_posix()
    except ValueError:path=str(p)
    return dict(path=path,sha256=sha(p))
def path_of(r):
    p=Path(r['path']);return p if p.is_absolute() else ROOT/p
def finite(v):return v is not None and isinstance(v,(int,float,np.number)) and np.isfinite(v)

def registry():
    return [dict(config_id='risk_constant',task='risk',recipe='risk_simple_baseline',algorithm='constant',input='four_legal_frames'),
        dict(config_id='risk_static_depth2',task='risk',recipe='risk_simple_baseline',algorithm='shallow',max_depth=2,input='current_grid_control_demand'),
        dict(config_id='risk_RF200_depth6_leaf2',task='risk',recipe='risk_RF',algorithm='RF',n_estimators=200,max_depth=6,min_samples_leaf=2,input='four_legal_frames'),
        dict(config_id='risk_GRU16',task='risk',recipe='risk_GRU',algorithm='GRU',hidden_size=16,epochs=30,patience=5,learning_rate=.001,batch_size=64,input='four_legal_frames'),
        dict(config_id='consequence_mean16',task='consequence',recipe='consequence_mean',algorithm='mean',targets=list(TARGETS),input='complete_external_spec_and_background_offline_only'),
        dict(config_id='consequence_RF200_depth6_leaf2_16',task='consequence',recipe='consequence_RF',algorithm='RF',n_estimators=200,max_depth=6,min_samples_leaf=2,targets=list(TARGETS),target_loss_scale='training mean/std per target; std<=1e-8 uses scale1; inverse to original units',input='complete_external_spec_and_background_offline_only')]

def configuration_budget(configs):
    return dict(method_recipes=len({c['recipe'] for c in configs}),configurations_including_consequence_targets=sum(len(c.get('targets',[None])) for c in configs),
        seed_policy_fits_accounted_separately=True,multioutput_target_fits_not_hidden=True)

def _load_background():
    import pandas as pd
    from hospital_ies.pipeline import read_data
    from hospital_ies.resilience import build_zone_groups
    hourly=read_data(ROOT);zones=pd.read_csv(ROOT/'inputs/loads/critical_zones_8760.csv')
    groups=build_zone_groups(zones,hourly).set_index('hour_of_year')
    return groups,hourly

def _background_features(spec,table):
    groups,hourly=table;hours=list(range(int(spec['start_hour'])+1,int(spec['start_hour'])+7));output={}
    for p in range(4):
        for c in ('e','h','c'):
            values=np.asarray([groups.loc[h,f'P{p}_{c}_kW'] for h in hours],float)
            if not np.isfinite(values).all():raise ValueError('Offline annual background is incomplete')
            output[f'background_P{p}_{c}_mean_kW']=float(values.mean());output[f'background_P{p}_{c}_peak_kW']=float(values.max())
    temperatures=np.asarray([hourly.iloc[h-1]['outdoor_temperature_C'] for h in hours],float)
    output['background_outdoor_mean_C']=float(temperatures.mean());output['background_outdoor_peak_C']=float(temperatures.max())
    return output

def _load_energy_context(background):
    from hospital_ies.model import load_plant
    return load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory'),background[1]

def _energy_window(payload,context):
    from .energy_metrics import window_energy_fee
    return window_energy_fee(payload,*context,*payload['scenario'].get('score_interval_minutes',[60,420]))

def compact_digest(payloads,scores):
    return canonical(dict(payloads=payloads,scores=[dict(scenario_id=k[0],policy_id=k[1],score=scores[k]) for k in sorted(scores)]))

def assert_support_isolation(specs):
    from tools.p2b_experiment.design import support_interval
    supports=[]
    for s in specs:
        interval=support_interval(s)
        supports.append(dict(scenario_id=s['id'],role=s['experiment_role'],start=interval['raw_start_minute'],end=interval['raw_end_minute']))
    active=[]
    for s in sorted(supports,key=lambda r:(r['start'],r['end'],r['scenario_id'])):
        active=[p for p in active if p['end']>s['start']]
        if any(p['role']!=s['role'] for p in active):raise ValueError('Cross-role raw support overlap despite different family/group IDs')
        active.append(s)
    return dict(cross_role_overlaps=0,checked_specifications=len(supports),source_endpoint_hour_reserved=True)

def load_bound_cohorts(cohort_paths):
    """Require all attempted pairs and immutable actual final audit bytes."""
    from .runner import verify_manifest
    from .data_audit import _check_identity,_check_information
    from tools.p2b_experiment.audit import score_payload,check_payload
    if not cohort_paths:raise ValueError('At least one audited new-development cohort required')
    cohorts=[];payloads=[];scores={};bindings=[]
    references=[reference(Path(__file__).with_name(name)) for name in ('data_audit.py','firstformal_warning.py','energy_metrics.py')]
    seen=set();groups={};roots={};background=None;energy_context=None;core_hash=None
    for item in cohort_paths:
        mp=Path(item.get('manifest',item.get('manifest_path'))).resolve();m=json.loads(mp.read_text(encoding='utf-8'))
        if m.get('integrated_cohort')!='new_development':raise ValueError('Only audited new_development data may fit this comparison; original_replay is regression evidence')
        verify_manifest(m)
        ap=Path(item.get('audit',item.get('audit_path'))).resolve();a=json.loads(ap.read_text(encoding='utf-8'))
        if a.get('passed') is not True or a.get('complete_budget') is not True or a.get('manifest_signature')!=m['signature'] or a.get('manifest_sha256')!=sha(mp):
            raise ValueError('Actual complete audit does not bind the current manifest')
        if a.get('audit_source_sha256')!=sha(Path(__file__).with_name('data_audit.py')) or a.get('formal_warning_source_sha256')!=sha(Path(__file__).with_name('firstformal_warning.py')) or a.get('formal_warning_view_version')!='integrated.first_formal_warning.v1':
            raise ValueError('Current audit implementation/formal-warning view not bound')
        if a.get('energy_metrics_source_sha256')!=sha(Path(__file__).with_name('energy_metrics.py')):raise ValueError('Current actual energy-fee implementation not bound')
        if core_hash is not None and core_hash!=m['simulator_hash']:raise ValueError('Different physical cores cannot form one fitted cohort')
        core_hash=m['simulator_hash'];specs={s['id']:s for s in m['scenarios']};expected={(sid,p) for sid in specs for p in m['policies']}
        entries=a.get('checkpoint_digests',[]);keys=[(e['scenario_id'],e['policy_id']) for e in entries]
        if len(keys)!=len(expected) or set(keys)!=expected or len(set(keys))!=len(keys):raise ValueError('Audit checkpoint coverage missing or duplicate')
        if a.get('attempted_trajectories')!=len(expected) or a.get('attempted_specifications')!=len(specs):raise ValueError('Attempt denominator differs from registered specifications')
        score_path=ap.parent/'trajectory_scores.json'
        if a.get('output_sha256',{}).get(score_path.name)!=sha(score_path):raise ValueError('Audited score artifact changed')
        score_rows=json.loads(score_path.read_text(encoding='utf-8'));score_keys=[(r['scenario_id'],r['policy_id']) for r in score_rows]
        if len(score_keys)!=len(expected) or len(set(score_keys))!=len(score_keys) or set(score_keys)!=expected:raise ValueError('Score denominator missing or duplicate')
        score_lookup=dict(zip(score_keys,score_rows));references.extend([reference(mp),reference(ap),reference(score_path)])
        if background is None:background=_load_background();energy_context=_load_energy_context(background)
        for entry,key in zip(entries,keys):
            if key in seen:raise ValueError('Duplicate scenario/policy across fitted cohorts')
            seen.add(key);sid,policy=key;spec=specs[sid];role=spec['experiment_role'];assignment=m['splits'][sid]
            if spec.get('is_final_blind') or assignment.get('is_final_blind'):raise ValueError('Sealed final data are prohibited')
            if role not in ROLES:raise ValueError('Unregistered offline role')
            for store,value in ((groups,assignment['group_id']),(roots,spec['root_family_id'])):
                if value in store and store[value]!=role:raise ValueError('Cross-cohort family/background role isolation failed')
                store[value]=role
            pth=Path(entry['path']);pth=pth if pth.is_absolute() else mp.parent/pth
            if sha(pth)!=entry['checkpoint_sha256']:raise ValueError('Checkpoint bytes changed after audit')
            wrapper=json.loads(gzip.decompress(pth.read_bytes()));p=wrapper['payload']
            if canonical(p)!=entry['payload_sha256']:raise ValueError('Canonical checkpoint payload differs from final audit')
            if any(wrapper.get(k)!=v for k,v in dict(scenario_id=sid,policy_id=policy,manifest_signature=m['signature']).items()):raise ValueError('Checkpoint wrapper identity differs')
            check_payload(p);_check_information(p);_check_identity(p,spec,m)
            if p['summary'].get('scenario_id')!=sid or p['summary'].get('policy_id')!=policy:raise ValueError('Summary identity differs')
            calculated=score_payload(p,*spec.get('score_interval_minutes',[60,420]));calculated.update(_energy_window(p,energy_context));row=score_lookup[key]
            if row['experiment_role']!=role or row['group_id']!=assignment['group_id']:raise ValueError('Score group/role differs from manifest')
            for target in TARGETS+('cost_USD','score_outcome_valid','score_P0_failure','energy_fee_outcome_valid','observed_actual_energy_fee_USD'):
                original=calculated.get(target);saved=row.get(target)
                if original is None:
                    if saved is not None:raise ValueError('Unknown outcome was filled: '+target)
                elif isinstance(original,bool):
                    if saved is not original:raise ValueError('Score completeness differs')
                elif saved is None or abs(float(saved)-float(original))>1e-9:raise ValueError('Actual subsegment target differs: '+target)
            compact={k:p.get(k) for k in ('scenario','identity','policy_id','seed','initial_state','summary','decisions','segments')}
            compact['policy_id']=policy;compact['_binding']=dict(manifest_signature=m['signature'],checkpoint_path=reference(pth)['path'],checkpoint_sha256=entry['checkpoint_sha256'],payload_sha256=entry['payload_sha256'])
            compact['_offline_background']=_background_features(spec,background)
            payloads.append(compact);scores[key]=row;bindings.append(dict(scenario_id=sid,policy_id=policy,**compact['_binding']))
            if len(bindings)%50==0:print(json.dumps(dict(event='checkpoint_binding_progress',verified_pairs=len(bindings)),ensure_ascii=False),flush=True)
        cohorts.append(dict(manifest=reference(mp),audit=reference(ap),manifest_signature=m['signature'],specifications=len(specs),attempts=len(expected),sampling_phase=m.get('integrated_sampling_phase')))
    initial={}
    for p in payloads:
        sid=p['scenario']['id'];state=p.get('initial_state')
        if state is not None:
            if sid in initial and initial[sid]!=state:raise ValueError('Paired policies have different recorded initial states')
            initial[sid]=state
    support_check=assert_support_isolation({p['scenario']['id']:p['scenario'] for p in payloads}.values())
    return dict(audited=True,cohorts=cohorts,payloads=payloads,scores=scores,bindings=bindings,binding_digest=canonical(bindings),
        compact_input_digest=compact_digest(payloads,scores),support_isolation=support_check,
        bound_references=references,simulator_hash=core_hash,scope='new audited development only; frozen evaluation is not final blind')

def _verify_bindings(cohorts):
    if cohorts.get('audited') is not True or not cohorts.get('bindings') or not cohorts.get('cohorts'):raise ValueError('Actual audited new cohorts required before fitting')
    if compact_digest(cohorts['payloads'],cohorts['scores'])!=cohorts.get('compact_input_digest'):raise ValueError('Bound compact payload/score values changed in memory')
    from .runner import verify_manifest
    for cohort in cohorts['cohorts']:
        manifest=cohort['manifest'];audit=cohort['audit']
        if sha(path_of(manifest))!=manifest['sha256'] or sha(path_of(audit))!=audit['sha256']:raise ValueError('Audited cohort reference changed before fit')
        verify_manifest(json.loads(path_of(manifest).read_text(encoding='utf-8')))
    for r in cohorts['bound_references']:
        if sha(path_of(r))!=r['sha256']:raise ValueError('Bound audit/score/observation source changed')
    for r in cohorts['bindings']:
        if sha(path_of(dict(path=r['checkpoint_path'])))!=r['checkpoint_sha256']:raise ValueError('Actual checkpoint changed after binding')
    if canonical(cohorts['bindings'])!=cohorts['binding_digest']:raise ValueError('Input identity binding changed')
    assert_support_isolation({p['scenario']['id']:p['scenario'] for p in cohorts['payloads']}.values())

def build_risk_rows(payloads,policy):
    records=[];seen=set()
    for p in payloads:
        actual=p.get('policy_id',p.get('identity',{}).get('policy_id',p['summary'].get('policy_id')))
        if actual!=policy:continue
        spec=p['scenario'];sid=spec['id'];key=(sid,policy)
        if key in seen:raise ValueError('duplicate risk payload pair')
        seen.add(key);role=spec['experiment_role']
        if role not in ROLES:raise ValueError('Unregistered risk role')
        decisions=p['decisions']
        if [d['decision_time_min'] for d in decisions]!=sorted({d['decision_time_min'] for d in decisions}):raise ValueError('duplicate or unordered decision rows')
        for d in decisions:
            if any(k in FORBIDDEN or k.startswith(('future_','planned_shock','posterior_')) or k.endswith('_id') for k in d['features']):raise ValueError('Forbidden risk predictor identifier/future field')
        view=warning_view(p,horizon=60,score_interval=tuple(spec.get('score_interval_minutes',[60,420])),history_minutes=60)
        for index,v in enumerate(view['rows']):
            label=v['label']
            if not v['in_score_interval'] or not label['valid'] or not label['at_risk']:continue
            if label['value'] not in (0,1):raise ValueError('Valid risk label must be binary')
            selected=decisions[max(0,index-3):index+1];keys=set(decisions[index]['features'])
            if any(set(d['features'])!=keys for d in selected):raise ValueError('Inconsistent legal feature schema')
            pad=4-len(selected);sequence=[{k:None for k in keys} for _ in range(pad)]+[copy.deepcopy(d['features']) for d in selected]
            records.append(dict(scenario_id=sid,policy_id=policy,root_family_id=spec['root_family_id'],group_id=p.get('identity',{}).get('group_id',spec.get('group_id',spec['root_family_id'])),
                role=role,decision_time_min=float(v['decision_time_min']),label=int(label['value']),sequence=sequence,padding_mask=[0]*pad+[1]*len(selected),
                first_interval=v['first_failure_interval'],known_abnormal_at_decision=v['legacy_any_tiny_abnormal'],formal_warning_eligible=v['formal_warning_eligible'],
                trajectory_known_negative=p['summary']['status']=='completed' and p['summary'].get('p0_failed_any')==0,
                evaluation_only=dict(condition_id=spec.get('condition_id'),grid_limit_kW=spec.get('grid_limit_kW'),response_delay_minutes=spec.get('response_delay_minutes'),
                    fault_signature=json.dumps(spec.get('faults',{}),sort_keys=True,separators=(',',':')),background_id=spec.get('time_block_id')),
                source_binding=p.get('_binding'),observation_count=len(selected)))
    return records

def assert_role_isolation(records):
    for key in ('scenario_id','root_family_id','group_id'):
        role_by={}
        for r in records:
            if r[key] in role_by and role_by[r[key]]!=r['role']:raise ValueError('Offline role isolation failed: '+key)
            role_by[r[key]]=r['role']

def training_roles(records):
    assert_role_isolation(records)
    return {role:[r for r in records if r['role']==role] for role in ROLES}

def static_rows(rows):
    result=copy.deepcopy(rows)
    for r in result:
        for frame in r['sequence']:
            if not set(STATIC_KEYS)<=set(frame):raise ValueError('Static baseline missing current demand/control/grid')
        r['sequence']=[{k:frame[k] for k in STATIC_KEYS} for frame in r['sequence']]
    return result

def threshold_from_validation(y,p):
    y=np.asarray(y,int)
    if len(np.unique(y))<2:return dict(threshold=.5,status='fixed_half_single_class_diagnostic',recall=None,precision=None,selection_scope='fixed diagnostic; no dual-class threshold claim')
    result=choose_threshold(y,p);result['selection_scope']='pilot_validation only; frozen evaluation excluded'
    return result

def risk_evaluation(rows,p,threshold):
    y=np.array([r['label'] for r in rows],int);p=np.asarray(p,float)
    if len(p)!=len(rows):raise ValueError('Prediction identity count differs')
    if not len(rows):return dict(N=0,status='no_valid_rows',PR_AUC_average_precision=None,ROC_AUC=None)
    if not np.isfinite(p).all() or np.any((p<0)|(p>1)):raise ValueError('Invalid risk probabilities')
    both=len(np.unique(y))==2;alert=p>=threshold if threshold is not None else np.zeros(len(y),bool)
    tn,fp,fn,tp=confusion_matrix(y,alert,labels=[0,1]).ravel() if threshold is not None else (0,0,0,0)
    positive={r['scenario_id'] for r in rows if r['label']==1};detected={r['scenario_id'] for r,a in zip(rows,alert) if r['label']==1 and a}
    early={r['scenario_id'] for r,a in zip(rows,alert) if r['label']==1 and a and r['first_interval'] is not None and r['decision_time_min']<r['first_interval'][0]}
    clean={r['scenario_id'] for r,a in zip(rows,alert) if r['label']==1 and a and r['first_interval'] is not None and r['decision_time_min']<r['first_interval'][0] and not r['known_abnormal_at_decision']}
    alerts=[]
    for sid in sorted(detected):
        r=next(r for r,a in zip(rows,alert) if r['scenario_id']==sid and r['label']==1 and a);interval=r['first_interval']
        alerts.append(dict(scenario_id=sid,root_family_id=r['root_family_id'],decision_time_min=r['decision_time_min'],first_failure_interval=interval,
            lead_to_interval_start_min=None if interval is None else interval[0]-r['decision_time_min'],lead_to_classification_min=None if interval is None else interval[1]-r['decision_time_min'],
            known_abnormal_at_alert=r['known_abnormal_at_decision']))
    missed=sorted(positive-detected);negative_scenarios={r['scenario_id'] for r in rows if r['trajectory_known_negative']}
    missed_characteristics=[]
    for sid in missed:
        r=max((r for r in rows if r['scenario_id']==sid and r['label']==1),key=lambda r:r['decision_time_min']);last=r['sequence'][-1]
        missed_characteristics.append(dict(scenario_id=sid,root_family_id=r['root_family_id'],latest_valid_positive_decision_minute=r['decision_time_min'],**r['evaluation_only'],
            current_control_available=last.get('control_available'),current_grid_import_limit_kW=last.get('grid_import_limit_kW'),
            current_carrier_demand_kW={c:sum(float(last[f'P{p}_{c}_demand_kW']) for p in range(4)) for c in ('e','h','c')},
            known_abnormal_at_decision=r['known_abnormal_at_decision']))
    return dict(N=len(rows),positive_rows=int(y.sum()),negative_rows=int((y==0).sum()),single_class=not both,
        PR_AUC_average_precision=float(average_precision_score(y,p)) if both else None,ROC_AUC=float(roc_auc_score(y,p)) if both else None,
        Brier=float(brier_score_loss(y,p)),threshold=threshold,confusion_matrix=None if threshold is None else [[int(tn),int(fp)],[int(fn),int(tp)]],
        precision=float(tp/(tp+fp)) if tp+fp else None,recall=float(tp/(tp+fn)) if tp+fn else None,
        positive_scenarios=len(positive),positive_root_families=len({r['root_family_id'] for r in rows if r['label']==1}),
        known_complete_negative_scenarios=len(negative_scenarios),known_complete_negative_root_families=len({r['root_family_id'] for r in rows if r['trajectory_known_negative']}),
        missed_scenario_ids=missed,missed_root_family_ids=sorted({r['root_family_id'] for r in rows if r['scenario_id'] in missed}),
        missed_case_characteristics=missed_characteristics,
        missed_condition_counts=dict(Counter(str(r['condition_id']) for r in missed_characteristics)),
        false_alarm_complete_negative_ids=sorted({r['scenario_id'] for r,a in zip(rows,alert) if a and r['trajectory_known_negative']}),
        formal_before_interval_detected_scenarios=len(early),before_interval_without_tiny_detected_scenarios=len(clean),
        positive_first_alerts=alerts,legacy_tiny_raw_rows=sum(r['known_abnormal_at_decision'] for r in rows),
        formal_valid_without_tiny_rows=sum(not r['known_abnormal_at_decision'] for r in rows),
        formal_early_definition='alarm in positive window before first formal failure interval start; tiny-raw history retained in parallel',
        score_scope='development; frozen development evaluation is not final blind')

def partition_metrics(rows,p,threshold):
    result={}
    for field in ('condition_id','background_id','grid_limit_kW','response_delay_minutes','fault_signature'):
        output={}
        for value in sorted({str(r['evaluation_only'][field]) for r in rows}):
            idx=[i for i,r in enumerate(rows) if str(r['evaluation_only'][field])==value]
            output[value]=risk_evaluation([rows[i] for i in idx],np.asarray(p)[idx],threshold)
        result[field]=output
    return result

def logged_fit(ledger,identity,fn):
    ledger=Path(ledger);ledger.parent.mkdir(parents=True,exist_ok=True)
    def append(value):
        with ledger.open('a',encoding='utf-8') as f:f.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n');f.flush()
        # A multioutput estimator is one actual fit, with sixteen explicit
        # target/seed/policy accounting units sharing the same fit identity.
        with ledger.with_name('target_fit_units.jsonl').open('a',encoding='utf-8') as f:
            for target in identity.get('target_names',[]):
                f.write(json.dumps(dict(**value,target_name=target,shared_estimator_fit_id=canonical(identity)),ensure_ascii=False,allow_nan=False)+'\n')
            f.flush()
    identity=copy.deepcopy(identity);append(dict(event='started',utc=time.time(),**identity));started=time.perf_counter()
    print(json.dumps(dict(event='fit_started',config_id=identity.get('config_id'),policy=identity.get('policy'),seed=identity.get('seed'),target_count=len(identity.get('target_names',[]))),ensure_ascii=False),flush=True)
    try:
        value=fn();result=dict(status='completed',elapsed_seconds=time.perf_counter()-started,value=value)
        append(dict(event='completed',utc=time.time(),elapsed_seconds=result['elapsed_seconds'],**identity))
    except Exception as error:
        result=dict(status='failed',elapsed_seconds=time.perf_counter()-started,error_type=type(error).__name__,traceback=traceback.format_exc())
        append(dict(event='failed',utc=time.time(),**identity,**result))
    print(json.dumps(dict(event='fit_finished',config_id=identity.get('config_id'),policy=identity.get('policy'),seed=identity.get('seed'),status=result['status'],elapsed_seconds=result['elapsed_seconds']),ensure_ascii=False),flush=True)
    return result

def fit_risk_model(algorithm,seed,x,y,vx,vy,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False);y=np.asarray(y,int);vy=np.asarray(vy,int)
    if not len(y) or not len(vy):raise ValueError('Risk training and validation must each contain valid rows')
    if not set(y)<=set((0,1)) or not set(vy)<=set((0,1)):raise ValueError('Risk labels must be observed binary outcomes')
    single=len(np.unique(y))<2;actual='constant' if single else algorithm
    requested_device='cuda:0' if algorithm=='GRU' else 'cpu'
    diag=dict(requested_algorithm=algorithm,actual_algorithm=actual,single_class_train_diagnostic=single,
              requested_device=requested_device,fit_device='cpu' if actual!='GRU' else None)
    write(directory/'training_started.json',dict(task='risk',requested_algorithm=algorithm,actual_algorithm=actual,
        requested_device=requested_device,seed=seed,training_rows=len(y),validation_rows=len(vy),input_shape=list(x.shape),utc=time.time()))
    if actual=='constant':
        write(directory/'constant.json',dict(probability=float(y.mean()),fit_rows=len(y)))
        original_pred=np.full(len(vx),float(y.mean()))
    elif actual in ('shallow','RF'):
        model=DecisionTreeClassifier(max_depth=2,min_samples_leaf=2,random_state=seed) if actual=='shallow' else RandomForestClassifier(n_estimators=200,max_depth=6,min_samples_leaf=2,random_state=seed,n_jobs=1)
        inputs=x[:,-1,:] if actual=='shallow' else x.reshape(len(x),-1)
        model.fit(inputs,y);joblib.dump(model,directory/'model.joblib',compress=3)
        validation_inputs=vx[:,-1,:] if actual=='shallow' else vx.reshape(len(vx),-1)
        original_pred=model.predict_proba(validation_inputs)[:,list(model.classes_).index(1)]
    elif actual=='GRU':
        config=dict(**{'class':'GRU'},hidden_size=16,epochs=30,patience=5)
        pred,extra=fit_risk_candidate(config,seed,x,y,vx,vy,directory);diag.update(extra)
        if diag['fit_device']!='cuda:0':raise RuntimeError('Existing risk GRU must train on cuda:0')
        original_pred=pred
    else:raise ValueError('Unregistered risk model')
    write(directory/'model_descriptor.json',dict(task='risk',requested_algorithm=algorithm,algorithm=actual,
        requested_device=requested_device,training_device=diag['fit_device'],seed=seed,input_size=int(x.shape[-1]),hidden_size=16))
    p=reload_model_probabilities(directory,vx)
    diag['reload_max_abs_error']=float(np.max(abs(p-original_pred))) if len(p) else 0.
    if diag['reload_max_abs_error']>1e-12:raise ValueError('Risk serialized prediction differs from original fitted model')
    write(directory/'training_completed.json',dict(task='risk',actual_algorithm=actual,training_device=diag['fit_device'],
        seed=seed,reload_max_abs_error=diag['reload_max_abs_error'],utc=time.time()))
    return p,diag

def reload_model_probabilities(directory,x):
    directory=Path(directory);metadata=json.loads((directory/'model_descriptor.json').read_text(encoding='utf-8'));algorithm=metadata['algorithm']
    if algorithm=='constant':return np.full(len(x),json.loads((directory/'constant.json').read_text(encoding='utf-8'))['probability'])
    if algorithm in ('RF','shallow'):
        model=joblib.load(directory/'model.joblib');inputs=x[:,-1,:] if algorithm=='shallow' else x.reshape(len(x),-1)
        return model.predict_proba(inputs)[:,list(model.classes_).index(1)] if len(x) else np.empty(0)
    import torch
    if not len(x):return np.empty(0)
    if not torch.cuda.is_available():raise RuntimeError('CUDA is required for existing risk GRU inference')
    device=torch.device('cuda:0');model=make_gru(metadata['input_size'],metadata['hidden_size']).to(device);model.load_state_dict(torch.load(directory/'state_dict.pt',map_location='cpu',weights_only=True));model.eval()
    with torch.no_grad():return torch.sigmoid(model(torch.tensor(x,device=device))).cpu().numpy()

def reload_risk_trial(directory,raw_rows):
    directory=Path(directory);p=json.loads((directory/'provenance.json').read_text(encoding='utf-8'))
    for f in p['artifacts']:
        if sha(directory/f['path'])!=f['sha256']:raise ValueError('Saved risk artifact changed')
    prep_path=directory/p['preprocessor_path']
    if sha(prep_path)!=p['preprocessor_sha256']:raise ValueError('Saved training preprocessor changed')
    records=static_rows(raw_rows) if p['input_representation']=='current_grid_control_demand' else raw_rows
    prep=SequencePreprocessor.from_dict(json.loads(prep_path.read_text(encoding='utf-8')))
    return reload_model_probabilities(directory,prep.transform(records)) if records else np.empty(0)

def consequence_features(p):
    s=p['scenario'];f=dict(calendar_start_hour=float(s['start_hour']),grid_limit_kW=float(s['grid_limit_kW']),response_delay_minutes=float(s.get('response_delay_minutes',s.get('response_delay_steps',0)*s.get('step_minutes',15))),
        shock_start_minute=float(s.get('shock_start_minute',0)),shock_duration_minutes=float(s.get('shock_duration_minutes',0)),fault_start_minute=float(s.get('fault_start_minute',0)),fault_duration_minutes=float(s.get('fault_duration_minutes',0)),
        fault_device=';'.join(sorted(s.get('faults',{}))) or '__NONE__')
    for c in ('e','h','c'):
        f['external_shock_'+c]=float(s.get('shock',{}).get(c,0.))
        for priority in ('P0','P1','P2','P3'):f[f'external_{priority}_{c}_increment']=float(s.get('shock_by_priority',{}).get(priority,{}).get(c,0.))
    for dev in ('EC_2','TOWER_3'):f['external_fault_availability_'+dev]=float(s.get('faults',{}).get(dev,1.))
    executed=(p.get('initial_state') or {}).get('executed',{});state=(p.get('initial_state') or {}).get('state',{})
    f['initial_GT_sum_kW']=sum(executed['gt_electric_kW']) if executed.get('gt_electric_kW') is not None else None
    for key in ('grid_import_kW','diesel_kW','boiler_steam_lbph'):f['initial_'+key]=executed.get(key)
    thermal=state.get('thermal_delta');f['initial_max_thermal_delta_C']=max(thermal.values(),default=0.) if thermal is not None else None
    if p.get('_offline_background') is not None:f.update(p['_offline_background'])
    else:
        history=[d for d in p['decisions'] if d['decision_time_min']<60]
        for key in STATIC_KEYS[2:]:
            vals=[d['features'].get(key) for d in history];known=[float(v) for v in vals if finite(v)]
            f['prefix_background_'+key]=float(np.mean(known)) if known else None
    return f

def consequence_feature_schema(features):
    return [dict(name=k,unit='kW' if k.endswith('_kW') else 'degC' if k.endswith('_C') else 'lb/h' if k.endswith('_lbph') else 'min' if 'minute' in k else 'hour' if 'hour' in k else 'category' if isinstance(v,str) else 'dimensionless',online_risk_allowed=False,
        source='complete exogenous specification, recorded initial state and background; offline proxy only') for k,v in sorted(features.items())]

def build_consequence_rows(payloads,scores,policy):
    result=[]
    for p in payloads:
        actual=p.get('policy_id',p.get('identity',{}).get('policy_id',p['summary'].get('policy_id')))
        if actual!=policy:continue
        s=p['scenario'];score=scores[(s['id'],policy)];complete=p['summary'].get('full_trajectory_outcome_valid') is True and score.get('score_outcome_valid') is True
        targets=[float(score[k]) if complete and finite(score.get(k)) else None for k in TARGETS]
        result.append(dict(scenario_id=s['id'],policy_id=policy,root_family_id=s['root_family_id'],group_id=p.get('identity',{}).get('group_id',s['root_family_id']),role=s['experiment_role'],
            features=consequence_features(p),targets=targets,target_mask=[v is not None for v in targets],full_trajectory_valid=complete,
            condition_id=s.get('condition_id'),background_id=s.get('time_block_id'),score_interval_minutes=s.get('score_interval_minutes',[60,420]),source_binding=p.get('_binding'),
            accounting_shadow_cost_USD=float(score['cost_USD']) if complete and finite(score.get('cost_USD')) else None,accounting_shadow_is_energy_fee=False))
    assert_role_isolation(result);return result

def consequence_metrics(y,p,train_y,unit):
    y=np.asarray(y,float);p=np.asarray(p,float);train_y=np.asarray(train_y,float)
    if not len(y):return dict(N=0,unit=unit,status='no_known_targets',normalized_RMSE=None)
    if not np.isfinite(y).all() or not np.isfinite(p).all() or not np.isfinite(train_y).all():raise ValueError('Unknown target/prediction cannot enter numeric metrics')
    error=p-y;std=float(train_y.std());rmse=float(np.sqrt(np.mean(error**2)));tail=float(np.quantile(train_y,.9));mask=y>=tail
    return dict(N=len(y),unit=unit,MAE=float(np.mean(abs(error))),RMSE=rmse,training_std=std,normalized_RMSE=rmse/std if std>1e-8 else None,
        constant_training_target=std<=1e-8,max_abs_error=float(np.max(abs(error))),max_underprediction=float(max(0.,np.max(y-p))),negative_prediction_count=int((p<0).sum()),
        truth_mean=float(y.mean()),prediction_mean=float(p.mean()),tail_train_q90=dict(threshold=tail,N=int(mask.sum()),MAE=float(np.mean(abs(error[mask]))) if mask.any() else None,
            max_underprediction=float(max(0.,np.max(y[mask]-p[mask]))) if mask.any() else None))

def _feature_records(rows):
    return [dict(sequence=[{k:None for k in r['features']}]*3+[r['features']],padding_mask=[0,0,0,1]) for r in rows]

def fit_consequence_model(algorithm,seed,x,y,directory):
    directory=Path(directory);directory.mkdir(exist_ok=False,parents=True)
    write(directory/'training_started.json',dict(task='consequence',algorithm=algorithm,seed=seed,training_rows=len(y),target_names=list(TARGETS),estimator_fit_calls=1,fitted_target_count=16,utc=time.time()))
    if algorithm=='mean':
        model=np.mean(y,axis=0);np.save(directory/'mean.npy',model)
    elif algorithm=='RF':
        mean=np.mean(y,axis=0);std=np.std(y,axis=0);scale=np.where(std>1e-8,std,1.)
        scaled=(y-mean)/scale
        write(directory/'target_scaler.json',dict(targets=list(TARGETS),mean=mean.tolist(),std=std.tolist(),scale=scale.tolist(),units=TARGET_UNITS,fit_role='pilot_train',
            training_rows=len(y),constant_like_threshold=1e-8,joint_impurity_units='dimensionless standardized target variance; no raw kWh/USD/degC mixing'))
        model=RandomForestRegressor(n_estimators=200,max_depth=6,min_samples_leaf=2,random_state=seed,n_jobs=1).fit(x,scaled)
        joblib.dump(model,directory/'model.joblib',compress=3)
    else:raise ValueError('Unregistered consequence model')
    write(directory/'model_descriptor.json',dict(task='consequence',algorithm=algorithm,seed=seed,targets=list(TARGETS),unit=TARGET_UNITS,estimator_fit_calls=1,fitted_targets=len(TARGETS)))
    write(directory/'training_completed.json',dict(task='consequence',algorithm=algorithm,seed=seed,fitted_target_count=16,utc=time.time()))
    return dict(algorithm=algorithm,estimator_fit_calls=1,fitted_targets=len(TARGETS))

def reload_consequence(directory,x):
    directory=Path(directory);m=json.loads((directory/'model_descriptor.json').read_text(encoding='utf-8'))
    if not len(x):return np.empty((0,len(TARGETS)))
    if m['algorithm']=='mean':return np.tile(np.load(directory/'mean.npy'),(len(x),1))
    scaler=json.loads((directory/'target_scaler.json').read_text(encoding='utf-8'))
    return joblib.load(directory/'model.joblib').predict(x)*np.asarray(scaler['scale'])+np.asarray(scaler['mean'])

def reload_consequence_trial(directory,raw_rows):
    directory=Path(directory);p=json.loads((directory/'provenance.json').read_text(encoding='utf-8'))
    for f in p['artifacts']:
        if sha(directory/f['path'])!=f['sha256']:raise ValueError('Saved consequence artifact changed')
    prep_path=directory/p['preprocessor_path']
    if sha(prep_path)!=p['preprocessor_sha256']:raise ValueError('Saved consequence preprocessor changed')
    prep=SequencePreprocessor.from_dict(json.loads(prep_path.read_text(encoding='utf-8')))
    x=prep.transform(_feature_records(raw_rows))[:,-1,:] if raw_rows else np.empty((0,0))
    return reload_consequence(directory,x)

def _write_predictions(path,rows,p,threshold):
    import csv
    fields=['scenario_id','policy_id','root_family_id','group_id','role','decision_time_min','label','probability','alert','threshold','first_interval_start','first_interval_end','known_abnormal_at_decision','formal_warning_eligible']
    with Path(path).open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r,prob in zip(rows,p):
            v={k:r[k] for k in fields if k in r};interval=r['first_interval']
            v.update(probability=float(prob),alert=None if threshold is None else bool(prob>=threshold),threshold=threshold,first_interval_start=None if interval is None else interval[0],first_interval_end=None if interval is None else interval[1]);writer.writerow(v)

def _provenance(trial,folder,prep_path,input_path,train,valid,cohorts,config):
    files=[dict(path=p.name,sha256=sha(p)) for p in sorted(trial.iterdir()) if p.is_file()]
    value=dict(config=config,manifest_signatures=[x['manifest_signature'] for x in cohorts['cohorts']],binding_digest=cohorts['binding_digest'],
        train_only_preprocessor=True,preprocessor_path='../'+prep_path.name,preprocessor_sha256=sha(prep_path),fitted_input_npz_sha256=sha(input_path),
        input_representation=config['input'],fit_scenario_ids=sorted({r['scenario_id'] for r in train}),validation_scenario_ids=sorted({r['scenario_id'] for r in valid}),
        fit_row_ids=[[r['scenario_id'],r.get('decision_time_min')] for r in train],validation_row_ids=[[r['scenario_id'],r.get('decision_time_min')] for r in valid],
        frozen_eval_excluded_from_fit_earlystop_threshold_selection=True,artifacts=files,implementation_sha256=sha(__file__))
    write(trial/'provenance.json',value);return value

def _risk_population(payloads,policy):
    counts=Counter();excluded=[];failures=[];no_positive_window=[];attempts=0;unknown=[]
    for p in payloads:
        if p['policy_id']!=policy:continue
        attempts+=1
        view=warning_view(p,60,tuple(p['scenario'].get('score_interval_minutes',[60,420])))
        first=view['first_classification_minute'];eligible_positive=any(r['in_score_interval'] and r['label']['valid'] and r['label']['at_risk'] and r['label']['value']==1 for r in view['rows'])
        if first is not None:
            failures.append(p['scenario']['id'])
            if not eligible_positive:
                start,end=p['scenario'].get('score_interval_minutes',[60,420]);times=[r['decision_time_min'] for r in view['rows'] if r['in_score_interval']]
                reason='first_failure_at_or_before_score_start' if first<=start else 'first_failure_after_last_available_warning_horizon' if times and first>max(times)+60 else 'no_valid_at_risk_warning_decision'
                no_positive_window.append(dict(scenario_id=p['scenario']['id'],role=p['scenario']['experiment_role'],first_classification_minute=first,reason=reason,not_counted_as_false_negative=True))
        elif p['summary']['status']!='completed':unknown.append(p['scenario']['id'])
        for r in view['rows']:
            if not r['in_score_interval']:continue
            l=r['label'];role=p['scenario']['experiment_role'];kind='positive' if l['value']==1 else 'negative' if l['value']==0 else str(l['unknown_reason'])
            counts[(role,kind)]+=1
            if not l['valid'] or not l['at_risk']:excluded.append(dict(scenario_id=p['scenario']['id'],policy_id=policy,role=role,decision_time_min=r['decision_time_min'],label=l))
    return dict(counts=[dict(role=k[0],label_kind=k[1],count=v) for k,v in counts.items()],excluded_unknown_or_postfirst_rows=excluded,
        attempted_trajectories=attempts,formal_failure_scenarios=sorted(failures),first_formal_failures_without_eligible_positive_windows=no_positive_window,
        unknown_no_formal_failure_yet_scenario_ids=sorted(unknown),denominator_semantics='All attempts retained; model FN denominator is eligible positive-window accidents; other formal failures and unknown prefixes are separate')

def select_risk_configuration(trials):
    candidates=[]
    for c in [x for x in registry() if x['task']=='risk']:
        matches=[t for t in trials if t['config_id']==c['config_id'] and t['status']=='completed']
        scores=[t['evaluation']['pilot_validation']['metrics']['PR_AUC_average_precision'] for t in matches]
        if len(matches)==3 and {t['seed'] for t in matches}==set(SEEDS) and all(v is not None for v in scores):
            candidates.append(dict(config_id=c['config_id'],mean_validation_PR_AUC=float(np.mean(scores))))
    return dict(rule='mean pilot_validation PR-AUC across three registered seeds; frozen development_boundary excluded',candidates=candidates,
        selected=max(candidates,key=lambda c:c['mean_validation_PR_AUC']) if candidates else None,representative_seed=4101)

def _run_risk(cohorts,out,ledger):
    reports=[]
    for policy in ('priority_milp','secure_milp'):
        rows=build_risk_rows(cohorts['payloads'],policy);roles=training_roles(rows);folder=out/policy/'risk';folder.mkdir(parents=True)
        write(folder/'raw_legal_rows.json',rows);write(folder/'population_masks.json',_risk_population(cohorts['payloads'],policy));trials=[]
        train=roles['pilot_train'];valid=roles['pilot_validation']
        if not train or not valid:
            reports.append(dict(policy=policy,status='no_observed_training_or_validation_rows',rows_by_role={k:len(v) for k,v in roles.items()}));continue
        for config in [c for c in registry() if c['task']=='risk']:
            use_static=config['algorithm']=='shallow';encoded={role:static_rows(values) if use_static else values for role,values in roles.items()}
            prep=SequencePreprocessor().fit(encoded['pilot_train']);x=prep.transform(encoded['pilot_train']);vx=prep.transform(encoded['pilot_validation'])
            values={role:prep.transform(items) if items else np.empty((0,4,x.shape[-1]),dtype=np.float32) for role,items in encoded.items()}
            y=np.array([r['label'] for r in train]);vy=np.array([r['label'] for r in valid]);prep_path=folder/(config['config_id']+'_preprocessor.json');write(prep_path,prep.to_dict())
            input_path=folder/(config['config_id']+'_fitted_inputs.npz');np.savez_compressed(input_path,train_X=x,train_y=y,validation_X=vx,validation_y=vy,
                frozen_evaluation_X=values['development_boundary'],frozen_evaluation_y=np.array([r['label'] for r in roles['development_boundary']]))
            for seed in SEEDS:
                trial_dir=folder/(config['config_id']+'__seed'+str(seed));identity=dict(task='risk',config_id=config['config_id'],policy=policy,seed=seed,target_names=['future_P0_failure_H60'],fitted_input_sha256=sha(input_path))
                def dofit():
                    pred,diag=fit_risk_model(config['algorithm'],seed,x,y,vx,vy,trial_dir)
                    threshold=threshold_from_validation(vy,pred);scope={}
                    if diag['actual_algorithm']=='shallow':
                        from sklearn.tree import export_text
                        names=[]
                        for key in prep.keys:
                            names.extend([key+'.train_z',key+'.missing'] if key in prep.numeric else [key+'=='+v for v in prep.categorical[key]])
                        names.append('padding_mask')
                        (trial_dir/'shallow_rules.txt').write_text(export_text(joblib.load(trial_dir/'model.joblib'),feature_names=names),encoding='utf-8')
                    _provenance(trial_dir,folder,prep_path,input_path,train,valid,cohorts,config)
                    for role,items in roles.items():
                        p=reload_model_probabilities(trial_dir,values[role]);reload=reload_risk_trial(trial_dir,items)
                        error=float(np.max(abs(p-reload))) if len(p) else 0.
                        if error>1e-12:raise ValueError('Raw-input risk reload differs')
                        np.save(trial_dir/(role+'_probabilities.npy'),p);_write_predictions(trial_dir/(role+'_alarms.csv'),items,p,threshold['threshold'])
                        write(trial_dir/(role+'_alarms.metadata.json'),dict(schema='integrated.offline.alarms.v1',model_id=config['config_id']+'__seed'+str(seed),role=role,policy=policy,
                            threshold=threshold,probabilities_sha256=sha(trial_dir/(role+'_probabilities.npy')),csv_sha256=sha(trial_dir/(role+'_alarms.csv')),
                            row_identity_digest=canonical([[r['scenario_id'],r['policy_id'],r['decision_time_min']] for r in items]),input_binding_digest=cohorts['binding_digest'],
                            only_validation_selects_threshold=True,frozen_evaluation_not_final_blind=True))
                        scope[role]=dict(metrics=risk_evaluation(items,p,threshold['threshold']),by_condition_and_background=partition_metrics(items,p,threshold['threshold']),raw_reload_max_abs_error=error)
                    return dict(threshold=threshold,diagnostics=diag,evaluation=scope)
                attempted=logged_fit(ledger,identity,dofit);trial=dict(**identity,status=attempted['status'],trial_directory=trial_dir.relative_to(out).as_posix(),elapsed_seconds=attempted['elapsed_seconds'])
                trial.update(attempted['value'] if attempted['status']=='completed' else {k:v for k,v in attempted.items() if k!='status'})
                trial_dir.mkdir(parents=True,exist_ok=True);write(trial_dir/'trial.json',trial);trials.append(trial)
        selection=select_risk_configuration(trials)
        reports.append(dict(policy=policy,status='compared' if selection['candidates'] else 'single_class_or_failed_selection_not_estimable',trials=trials,selection=selection,
            rows_by_role={k:len(v) for k,v in roles.items()},families_by_role={k:len({r['root_family_id'] for r in v}) for k,v in roles.items()},groups_by_role={k:len({r['group_id'] for r in v}) for k,v in roles.items()}))
    return reports

def _run_consequence(cohorts,out,ledger):
    reports=[]
    for policy in ('priority_milp','secure_milp'):
        allrows=build_consequence_rows(cohorts['payloads'],cohorts['scores'],policy);folder=out/policy/'consequence';folder.mkdir(parents=True);write(folder/'raw_rows_and_target_masks.json',allrows)
        roles={role:[r for r in allrows if r['role']==role and all(r['target_mask'])] for role in ROLES};train=roles['pilot_train'];valid=roles['pilot_validation'];trials=[]
        masks={role:{target:dict(known=sum(r['target_mask'][j] for r in allrows if r['role']==role),unknown=sum(not r['target_mask'][j] for r in allrows if r['role']==role)) for j,target in enumerate(TARGETS)} for role in ROLES}
        if not train or not valid:
            reports.append(dict(policy=policy,status='no_complete_observed_training_validation_targets',target_masks=masks));continue
        prep=SequencePreprocessor().fit(_feature_records(train));values={role:prep.transform(_feature_records(items))[:,-1,:] if items else np.empty((0,len(prep.transform(_feature_records(train))[0,-1]))) for role,items in roles.items()}
        y=np.asarray([r['targets'] for r in train]);x=values['pilot_train'];prep_path=folder/'train_only_preprocessor.json';write(prep_path,prep.to_dict());write(folder/'offline_feature_schema.json',consequence_feature_schema(train[0]['features']))
        input_path=folder/'fitted_inputs.npz';np.savez_compressed(input_path,train_X=x,train_y=y,validation_X=values['pilot_validation'],validation_y=np.array([r['targets'] for r in valid]),
            frozen_evaluation_X=values['development_boundary'],frozen_evaluation_y=np.array([r['targets'] for r in roles['development_boundary']]))
        for config in [c for c in registry() if c['task']=='consequence']:
            for seed in SEEDS:
                trial_dir=folder/(config['config_id']+'__seed'+str(seed));identity=dict(task='consequence',config_id=config['config_id'],policy=policy,seed=seed,target_names=list(TARGETS),fitted_input_sha256=sha(input_path),fitted_target_count=16,estimator_fit_calls=1)
                def dofit():
                    diag=fit_consequence_model(config['algorithm'],seed,x,y,trial_dir);scope={}
                    _provenance(trial_dir,folder,prep_path,input_path,train,valid,cohorts,config)
                    for role,items in roles.items():
                        p=reload_consequence(trial_dir,values[role]);again=reload_consequence_trial(trial_dir,items);error=float(np.max(abs(p-again))) if len(p) else 0.
                        if error>1e-12:raise ValueError('Consequence model reload differs')
                        np.save(trial_dir/(role+'_predictions.npy'),p);truth=np.asarray([r['targets'] for r in items]) if items else np.empty((0,16))
                        metrics={target:consequence_metrics(truth[:,j],p[:,j],y[:,j],TARGET_UNITS[target]) for j,target in enumerate(TARGETS)}
                        groups={}
                        for key in ('condition_id','background_id'):
                            groups[key]={}
                            for value in sorted({str(r[key]) for r in items}):
                                ids=[i for i,r in enumerate(items) if str(r[key])==value]
                                groups[key][value]={target:consequence_metrics(truth[ids,j],p[ids,j],y[:,j],TARGET_UNITS[target]) for j,target in enumerate(TARGETS)}
                        scope[role]=dict(metrics=metrics,by_condition_and_background=groups,reload_max_abs_error=error)
                        write(trial_dir/(role+'_predictions.json'),[dict(scenario_id=r['scenario_id'],group_id=r['group_id'],role=role,prediction={target:float(p[i,j]) for j,target in enumerate(TARGETS)},truth={target:r['targets'][j] for j,target in enumerate(TARGETS)}) for i,r in enumerate(items)])
                    return dict(diagnostics=diag,evaluation=scope)
                attempted=logged_fit(ledger,identity,dofit);trial=dict(**identity,status=attempted['status'],trial_directory=trial_dir.relative_to(out).as_posix(),elapsed_seconds=attempted['elapsed_seconds'])
                trial.update(attempted['value'] if attempted['status']=='completed' else {k:v for k,v in attempted.items() if k!='status'});trial_dir.mkdir(exist_ok=True);write(trial_dir/'trial.json',trial);trials.append(trial)
        candidates=[]
        for c in [x for x in registry() if x['task']=='consequence']:
            matches=[t for t in trials if t['config_id']==c['config_id'] and t['status']=='completed']
            nrmse=[m['normalized_RMSE'] for t in matches for m in t['evaluation']['pilot_validation']['metrics'].values() if m['normalized_RMSE'] is not None]
            if len(matches)==3 and nrmse:candidates.append(dict(config_id=c['config_id'],mean_validation_normalized_RMSE=float(np.mean(nrmse))))
        selected=min(candidates,key=lambda c:c['mean_validation_normalized_RMSE']) if candidates else None
        reports.append(dict(policy=policy,status='compared' if candidates else 'constant_or_failed_selection_not_estimable',trials=trials,target_masks=masks,
            complete_common_target_rows={k:len(v) for k,v in roles.items()},unknown_or_partial_rows_retained=[r['scenario_id'] for r in allrows if not all(r['target_mask'])],
            selection=dict(rule='mean validation training-scale normalized RMSE over nonconstant targets/three seeds; no cross-unit raw averaging or frozen selection',candidates=candidates,selected=selected),
            proxy_only=True,full_trajectory_and_score_required=True,score_interval_minutes=[60,420],unmodeled_temperature='P3 has no modeled zone temperature target'))
    return reports

def run_offline(cohorts,out):
    _verify_bindings(cohorts);out=Path(out)
    if out.exists():raise FileExistsError('Fresh immutable offline fit directory required')
    out.mkdir(parents=True);configs=registry();budget=configuration_budget(configs)
    if budget['method_recipes']>5 or budget['configurations_including_consequence_targets']>36:raise ValueError('Preregistered offline configuration budget exceeded')
    runtime=dict(python=sys.version,packages={name:package_metadata.version(name) for name in ('numpy','scipy','scikit-learn','joblib','torch')},
        GRU_device='cuda:0',GRU_device_semantics='required when actual_algorithm is GRU; observed device in model_descriptor.training_device',
        GRU_threads=None,RF_threads=1)
    write(out/'FIT_PREREGISTRATION.json',dict(registry=configs,seeds=SEEDS,budget=budget,runtime=runtime,input_cohorts=cohorts['cohorts'],binding_digest=cohorts['binding_digest'],
        source_sha256={reference(p)['path']:sha(p) for p in (Path(__file__),ROOT/'tools/p2b_learning/risk.py',ROOT/'tools/integrated_experiment/firstformal_warning.py',ROOT/'tools/integrated_experiment/energy_metrics.py')},
        train_role='pilot_train',threshold_earlystop_selection_role='pilot_validation',frozen_evaluation_role='development_boundary',
        compact_input_digest=cohorts['compact_input_digest'],cross_cohort_raw_support_isolation=cohorts['support_isolation'],
        no_environment_reset_step_or_solver_calls=True,unknown_labels_targets_not_zero=True,representative_seed_preassigned=4101,
        consequence_offline_future_spec_allowed=True,risk_future_spec_forbidden=True,expense_semantics=EXPENSE_SEMANTICS))
    write(out/'input_checkpoint_bindings.json',cohorts['bindings']);ledger=out/'fit_attempts.jsonl'
    risk=_run_risk(cohorts,out,ledger);consequence=_run_consequence(cohorts,out,ledger);_verify_bindings(cohorts)
    attempted=[json.loads(x) for x in ledger.read_text(encoding='utf-8').splitlines()];started=[x for x in attempted if x['event']=='started'];finished=[x for x in attempted if x['event']!='started']
    if len(started)!=len(finished):raise ValueError('Fit attempt completion accounting differs')
    failed=[x for x in finished if x['event']=='failed'];report=dict(risk=risk,consequence=consequence,cohorts=cohorts['cohorts'],binding_digest=cohorts['binding_digest'],registry=configs,seeds=SEEDS,
        actual_fit_attempts=len(started),actual_target_seed_policy_fit_units=sum(len(x['target_names']) for x in started),failed_attempts=len(failed),all_failed_attempts_preserved=failed,
        recipes_and_configurations=budget,expense_semantics=EXPENSE_SEMANTICS,development_evaluation_not_final_blind=True,no_environment_calls=True,original800_not_used_for_fit=True,
        statistical_limit='Purposeful new coverage and adaptive train support are development experiments, not clinical incidence or final blind accuracy')
    write(out/'OFFLINE_REPORT.json',report);print(json.dumps(dict(actual_fit_attempts=report['actual_fit_attempts'],target_fit_units=report['actual_target_seed_policy_fit_units'],failed_attempts=len(failed)),ensure_ascii=False));return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--cohort',nargs=2,action='append',required=True,metavar=('MANIFEST','AUDIT'));p.add_argument('--out',required=True);a=p.parse_args()
    report=run_offline(load_bound_cohorts([dict(manifest=x[0],audit=x[1]) for x in a.cohort]),a.out)
    if report['failed_attempts']:raise SystemExit(1)
if __name__=='__main__':main()
