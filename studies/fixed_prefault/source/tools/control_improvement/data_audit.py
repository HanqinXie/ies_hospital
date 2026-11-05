"""Read-only same-core attempt audit, raw labels and parallel formal-warning view.

The release check is delegated to runner.verify_manifest. Passing this audit
means recorded data agree with their declared contract; errors remain unknown.
"""
from __future__ import annotations
from collections import Counter
import argparse,csv,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.p2b_experiment.runner import load_payload,payload_path
from tools.p2b_experiment.audit import check_payload,score_payload,pair_category,checkpoint_digest,write_csv
from tools.integrated_experiment.firstformal_warning import warning_view,causal_feature_view
from tools.integrated_experiment.energy_metrics import window_energy_fee
import numpy as np

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_json(path,value):Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')

def check_horizon_contract(p,horizons):
    expected={str(h) for h in horizons}
    for d in p.get('decisions',[]):
        assert set(d['labels'])==expected,'Raw horizon/H120 contract missing or changed; do not fabricate labels'

def native_diagnostics(p):
    """Inspect accepted records against the unchanged original-unit limits."""
    counts=Counter();maxima=dict(max_physical_row_violation=0.,max_bound_violation=0.,max_lock_violation=0.,max_integrality_distance=0.)
    for call in p.get('solver_calls',[]):
        layers=call.get('solver_backend_layers',[])
        if call['status']=='success':
            assert [x['rank'] for x in layers]==list(range(5)),'Successful solve must retain all five rank layers'
        else:counts['failed_call_records']+=1
        for layer in layers:
            counts['native_layers']+=1
            accepted=[a for a in layer['attempts'] if a.get('accepted')]
            counts['nonaccepted_attempts']+=len(layer['attempts'])-len(accepted)
            if call['status']=='success':assert len(accepted)==1 and layer['status']==0,'Successful native layer has no unique accepted attempt'
            for a in accepted:
                counts['accepted_attempts']+=1
                assert a['status']==0 and a['raw_x'] and np.all(np.isfinite(a['raw_x'])),'Invalid accepted native primal'
                v=a['validation']
                for k,limit in [('max_physical_row_violation',1e-7),('max_bound_violation',1e-7),('max_lock_violation',1e-10),('max_integrality_distance',1e-9)]:
                    assert np.isfinite(v[k]) and v[k]<=limit,'Accepted original physical/lock/integer residual exceeds unchanged limit: '+k
                    maxima[k]=max(maxima[k],v[k])
    return dict(**{k:int(counts[k]) for k in ['native_layers','accepted_attempts','nonaccepted_attempts','failed_call_records']},**maxima)

def _check_information(p):
    prior=-1.
    for index,d in enumerate(p['decisions']):
        t=float(d['decision_time_min']);assert t>prior,'Decision time continuity/order'
        prior=t;causal_feature_view(p,index,60)
        graph=d.get('graph',{})
        for x in graph.get('nodes',[])+graph.get('edges',[]):
            assert x['available_time_min']<=t+1e-9,'Future graph information'
        assert all(x.get('edge_type')=='functional_dependency' for x in graph.get('edges',[])),'Graph semantics changed'
        assert not graph.get('statistical_edges',[]),'Unregistered fitted graph edges'

def _check_identity(p,s,m):
    assert p['scenario']==s,'Scenario identity/specification changed'
    status=p['summary']['status'];missing=[]
    incomplete=status in ('interrupted_unknown','orchestration_error')
    if 'seed' in p:assert p['seed']==m['options']['seed'],'Initialization seed mismatch'
    else:
        assert incomplete,'Missing initialization seed'
        missing.append('seed_not_observed')
    identity=p.get('identity')
    if identity is None:
        assert incomplete,'Missing payload identity'
        missing.append('runtime_identity_not_observed')
    else:
        assignment=m['splits'][s['id']]
        for key in ('group_id','experiment_role'):
            assert identity[key]==assignment[key],'Group/role identity changed: '+key
        for key in ('execution_contract_id','simulator_hash','assumption_set_id','schemas','manifest_signature'):
            expected=m['signature'] if key=='manifest_signature' else m[key]
            assert identity[key]==expected,'Frozen identity changed: '+key
            if key in p['summary']:assert p['summary'][key]==expected,'Summary identity changed'
    return missing

def audit(manifest_path,name):
    # Lazy import: pure view/design tools must not need the release runner.
    from .data_runner import verify_manifest
    manifest_path=Path(manifest_path).resolve();base=manifest_path.parent
    m=json.loads(manifest_path.read_text(encoding='utf-8'));verify_manifest(m)
    dest=base/'audits'/name
    if dest.exists():raise FileExistsError('Audit attempt already exists')
    specs={s['id']:s for s in m['scenarios']};policies=m['policies'];rows=[];pairs=[];errors=[];digests=[];ledger=[]
    rawlabels=[];formalrows=[];formalcounts={str(h):Counter() for h in m['options']['label_horizons']}
    labels=Counter();conditioncounts=Counter();maxbalance=0.;maxsteam=0.;segment_count=0;decision_count=0
    allstarted=set();allclosed=set();allpayloads=set();training={}
    nativecounts=Counter();nativemaxima=dict(max_physical_row_violation=0.,max_bound_violation=0.,max_lock_violation=0.,max_integrality_distance=0.)
    score_start=m['options'].get('score_start_minute',60.);score_end=m['options'].get('score_end_minute',420.)
    from hospital_ies.pipeline import read_data
    from hospital_ies.model import load_plant
    hourly=read_data(ROOT);plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    registered_shards={s['id'] for s in m['p2b_shards']}
    for folder in (base/'shards').glob('*'):
        if folder.is_dir() and folder.name not in registered_shards:
            assert not (folder/'attempts.jsonl').exists() and not list((folder/'checkpoints').glob('*.json.gz')),'Unregistered shard contains attempts/checkpoints'
    for shard in m['p2b_shards']:
        folder=base/'shards'/shard['id'];journal=folder/'attempts.jsonl'
        records=[json.loads(x) for x in journal.read_text(encoding='utf-8').splitlines()] if journal.exists() else []
        opened=set();closed=set();finalstatus={}
        for r in records:
            key=r['scenario_id'],r['policy_id'];assert r['manifest_signature']==m['signature'],'Journal source/manifest mismatch'
            assert key[0] in shard['scenario_ids'] and key[1] in policies,'Unregistered attempted pair'
            if r['event']=='started':
                assert key not in opened and key not in allstarted,'Duplicate attempted trajectory';opened.add(key);allstarted.add(key)
            elif r['event'] in ('finished','reconciled_checkpoint','adjudicated_interrupted_unknown'):
                assert key in opened and key not in closed,'Invalid attempt completion';closed.add(key);allclosed.add(key)
                if 'status' in r:finalstatus[key]=r['status']
            else:raise AssertionError('Unknown journal event')
        expectedpaths={payload_path(folder,sid,policy) for sid in shard['scenario_ids'] for policy in policies}
        assert all(p in expectedpaths for p in (folder/'checkpoints').glob('*.json.gz')),'Orphan/unregistered checkpoint file'
        for sid in shard['scenario_ids']:
            s=specs[sid];assignment=m['splits'][sid];pair={};scores={}
            if s['experiment_role']=='pilot_train':training.setdefault(sid,dict(experiment_role='pilot_train',manifest_signature=m['signature'],
                group_id=assignment['group_id'],root_family_id=s['root_family_id'],policy_outcomes={}))
            for policy in policies:
                key=sid,policy;path=payload_path(folder,sid,policy)
                entry=dict(scenario_id=sid,policy_id=policy,condition_id=s.get('condition_id'),event_class=s['event_class'],
                    experiment_role=s['experiment_role'],group_id=assignment['group_id'],root_family_id=s['root_family_id'],time_block_id=s['time_block_id'],
                    status='not_attempted',attempted=key in opened,manifest_signature=m['signature'])
                if not path.exists():
                    assert key not in opened,'Started attempt without checkpoint; audit must not fabricate outcome'
                    ledger.append(entry);continue
                assert key in opened,'Orphan checkpoint without attempted journal'
                assert key in closed,'Checkpoint has no closed journal entry'
                p=load_payload(folder,sid,policy,m['signature']);check_payload(p);_check_information(p);check_horizon_contract(p,m['options']['label_horizons'])
                native=native_diagnostics(p)
                nativecounts.update({k:native[k] for k in ['native_layers','accepted_attempts','nonaccepted_attempts','failed_call_records']})
                for k in nativemaxima:nativemaxima[k]=max(nativemaxima[k],native[k])
                missing=_check_identity(p,s,m);summary=p['summary'];status=summary['status']
                if key in finalstatus:assert finalstatus[key]==status,'Journal/checkpoint status mismatch'
                allpayloads.add(key);digest=checkpoint_digest(path,p,sid,policy,base);digests.append(digest)
                score=score_payload(p,score_start,score_end)
                score.update(window_energy_fee(p,plant,hourly,score_start,score_end))
                assert score['energy_fee_outcome_valid']==score['score_outcome_valid'],'Energy fee window mask differs from original score horizon'
                entry.update(status=status,checkpoint_sha256=digest['checkpoint_sha256'],**score);ledger.append(entry)
                row=dict(scenario_id=sid,policy_id=policy,condition_id=s.get('condition_id'),experiment_role=s['experiment_role'],
                    distribution_id=s['distribution_id'],group_id=assignment['group_id'],root_family_id=s['root_family_id'],time_block_id=s['time_block_id'],
                    status=status,event_class=s['event_class'],initiating_device=s.get('initiating_device'),
                    physical_service_failure=summary.get('physical_service_failure',False),full_trajectory_outcome_valid=summary.get('full_trajectory_outcome_valid',False),
                    source_checkpoint=digest['path'],checkpoint_sha256=digest['checkpoint_sha256'],payload_sha256=digest['payload_sha256'],
                    missing_observations=','.join(missing),manifest_signature=m['signature'],simulator_hash=m['simulator_hash'],**score)
                row.update(summary.get('event_cause_counts',{}));row['economic_tiebreak_skipped_steps']=summary.get('economic_tiebreak_skipped_steps',0)
                row['solve_time_seconds']=sum(x.get('elapsed_seconds',0) for x in p.get('solver_calls',[]))
                rows.append(row);pair[policy]=p;scores[policy]=row
                kind='observed_positive' if score['score_P0_failure']==1 else 'complete_negative' if score['score_P0_failure']==0 else 'unknown'
                conditioncounts[(s.get('condition_id',s['event_class']),s['experiment_role'],policy,kind)]+=1
                segment_count+=len(p['segments']);decision_count+=len(p['decisions'])
                for seg in p['segments']:
                    maxbalance=max(maxbalance,max(map(abs,seg['energy_balance_residuals'])));maxsteam=max(maxsteam,abs(seg['steam_balance_residual_lbph']))
                for d in p['decisions']:
                    for h,l in d['labels'].items():
                        rawlabels.append(dict(scenario_id=sid,policy_id=policy,group_id=assignment['group_id'],experiment_role=s['experiment_role'],
                            decision_time_min=d['decision_time_min'],horizon_minutes=float(h),manifest_signature=m['signature'],checkpoint_sha256=digest['checkpoint_sha256'],label=l))
                        if score_start<=d['decision_time_min']<score_end:
                            labels[(s['experiment_role'],policy,h,'positive' if l['value']==1 else 'negative' if l['value']==0 else l['unknown_reason'])]+=1
                for h in m['options']['label_horizons']:
                    view=warning_view(p,horizon=h,score_interval=(score_start,score_end),history_minutes=m['options'].get('history_minutes',60))
                    formalcounts[str(h)].update(view['counts'])
                    for v in view['rows']:
                        if not v['in_score_interval']:continue
                        formalrows.append(dict(scenario_id=sid,policy_id=policy,group_id=assignment['group_id'],root_family_id=s['root_family_id'],
                            experiment_role=s['experiment_role'],decision_time_min=v['decision_time_min'],horizon_minutes=float(h),
                            formal_warning_eligible=v['formal_warning_eligible'],legacy_any_tiny_abnormal=v['legacy_any_tiny_abnormal'],
                            prior_raw_P0_unserved_kW=v['prior_raw_P0_unserved_kW'],prior_P0_temperature_violation=v['prior_P0_temperature_violation'],
                            first_classification_minute=v['first_classification_minute'],first_failure_interval=v['first_failure_interval'],timing_relation=v['timing_relation'],
                            label=v['label'],manifest_signature=m['signature'],checkpoint_sha256=digest['checkpoint_sha256']))
                if p.get('error'):errors.append(dict(scenario_id=sid,policy_id=policy,status=status,error=p['error'],checkpoint_sha256=digest['checkpoint_sha256']))
                if sid in training:training[sid]['policy_outcomes'][policy]=dict(**score,checkpoint_path=digest['path'],checkpoint_sha256=digest['checkpoint_sha256'],payload_sha256=digest['payload_sha256'])
            if len(pair)==len(policies):
                initials=[p.get('initial_state') for p in pair.values()]
                if all(x is not None for x in initials):assert all(x==initials[0] for x in initials),'Paired initial state differs'
                else:assert all(p['summary']['status'] in ('interrupted_unknown','orchestration_error') or p.get('initial_state') is not None for p in pair.values()),'Missing paired initial state'
                a=scores['priority_milp'];b=scores['secure_milp'];pairs.append(dict(scenario_id=sid,category=pair_category(a,b),
                    priority_P0=a['score_P0_failure'],secure_P0=b['score_P0_failure'],both_complete_outcomes=a['score_outcome_valid'] and b['score_outcome_valid'],
                    group_id=assignment['group_id'],root_family_id=s['root_family_id'],experiment_role=s['experiment_role']))
    assert allstarted==allclosed==allpayloads,'Attempt journal/checkpoint bijection failed'
    assert len(ledger)==len(specs)*len(policies),'Planned denominator not retained'
    dest.mkdir(parents=True)
    for filename,value in [('trajectory_scores.json',rows),('attempt_ledger.json',ledger),('training_outcomes.json',training),('errors.json',errors)]:write_json(dest/filename,value)
    write_csv(dest/'trajectory_scores.csv',rows);write_csv(dest/'paired_outcomes.csv',pairs);write_csv(dest/'attempt_ledger.csv',ledger)
    for filename,items in [('raw_labels.jsonl',rawlabels),('formal_warning_rows.jsonl',formalrows)]:
        with (dest/filename).open('w',encoding='utf-8') as f:
            for item in items:f.write(json.dumps(item,ensure_ascii=False,allow_nan=False)+'\n')
    report=dict(passed=True,audit_scope='Declared metadata/numerics/labels only; not clinical realism or a new core release',
        complete_budget=len(allstarted)==len(ledger),planned_specifications=len(specs),planned_trajectories=len(ledger),
        manifest_signature=m['signature'],manifest_sha256=sha(manifest_path),initialization_seed=m['options']['seed'],simulator_hash=m['simulator_hash'],
        attempted_trajectories=len(allstarted),unattempted_trajectories=len(ledger)-len(allstarted),checkpoints=len(rows),
        attempted_specifications=len({k[0] for k in allstarted}),complete_pairs=len(pairs),complete_outcome_pairs=sum(p['both_complete_outcomes'] for p in pairs),
        status_counts=dict(Counter(r['status'] for r in rows)),solver_error_trajectories=sum(r['status']=='solver_error' for r in rows),
        score_valid=sum(r['score_outcome_valid'] for r in rows),score_unknown=sum(not r['score_outcome_valid'] for r in rows),
        observed_service_failure=sum(r['physical_service_failure'] for r in rows),score_observed_P0_failures=sum(r['score_P0_failure']==1 for r in rows),
        score_positive_root_families=len({r['root_family_id'] for r in rows if r['score_P0_failure']==1}),score_positive_connected_groups=len({r['group_id'] for r in rows if r['score_P0_failure']==1}),
        decision_attempts=decision_count,subsegments=segment_count,max_balance_residual_kW=maxbalance,max_steam_residual_lbph=maxsteam,
        labels=[dict(role=k[0],policy=k[1],horizon=k[2],kind=k[3],count=v) for k,v in labels.items()],
        condition_role_policy_outcomes=[dict(condition=k[0],role=k[1],policy=k[2],kind=k[3],count=v) for k,v in conditioncounts.items()],
        formal_warning_counts={k:dict(v) for k,v in formalcounts.items()},pair_categories=dict(Counter(p['category'] for p in pairs)),checkpoint_digests=digests,
        statistical_semantics='All attempts in denominator. Prefix-positive labels retained; incomplete negative/cost/ENS/thermal outcomes stay null. New coverage is not real failure probability.',
        information_semantics='Declared causal feature/graph time checked; ex post evaluation labels never become predictors.',
        formal_warning_semantics='First formal classified guarantee failure and first failure interval; legacy tiny raw ENS remains parallel, unchanged.',
        audit_source_sha256=sha(__file__),formal_warning_source_sha256=sha(ROOT/'tools/integrated_experiment/firstformal_warning.py'),
        energy_metrics_source_sha256=sha(ROOT/'tools/integrated_experiment/energy_metrics.py'),
        recorded_label_horizons=m['options']['label_horizons'],observed_horizon_minutes=m['options']['observed_end_minute'],
        native_diagnostics=dict(**dict(nativecounts),**nativemaxima),
        cost_semantics='cost_USD is original accounting expense including unused-steam tie-break; actual_energy_fee_USD is observed GT/boiler gas + grid + diesel only, at unchanged illustrative prices; incomplete full fee remains null',
        formal_warning_view_version='integrated.first_formal_warning.v1')
    report['output_sha256']={p.name:sha(p) for p in dest.iterdir() if p.is_file()}
    write_json(dest/'audit.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('checkpoint_digests','labels','condition_role_policy_outcomes')},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('manifest');p.add_argument('name');a=p.parse_args();audit(a.manifest,a.name)
