"""Read-only subsegment accounting and complete-attempt P2B audit."""
from collections import Counter
import csv
import json
import hashlib
from pathlib import Path
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.p2b_experiment.runner import load_payload,payload_path,verify_manifest
GROUPS=('P0','P1','P2','P3');CARRIERS=('e','h','c')


def checkpoint_digest(path,payload,scenario_id,policy_id,base):
    raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    return dict(scenario_id=scenario_id,policy_id=policy_id,
        path=Path(path).relative_to(base).as_posix(),
        checkpoint_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        payload_sha256=hashlib.sha256(raw).hexdigest())


def score_payload(p,start=60.,end=420.):
    from hospital_ies.resilience import ZONE_PRIORITY
    summary=p['summary'];segments=p['segments']
    observed=segments[-1]['end_minute'] if segments else 0.
    complete=observed>=end-1e-9
    energy=np.zeros((4,3));cost=0.;failed=False
    temps={g:None for g in GROUPS};violations={g:0. for g in GROUPS}
    for s in segments:
        dt=max(0.,min(end,s['end_minute'])-max(start,s['start_minute']))
        if dt<=0:continue
        energy+=np.asarray(s['unserved_kW'])*dt/60
        cost+=s['cost_USD_step']*dt/(s['end_minute']-s['start_minute'])
        failed=failed or bool(s['classification']['p0_guarantee_failure'])
        for g in GROUPS:
            value=s.get('temperature',{}).get(g)
            if value is not None:temps[g]=value if temps[g] is None else max(temps[g],value)
            violations[g]+=dt*bool(s.get('violation',{}).get(g,False))
    first=next((s for s in segments if s['classification']['p0_guarantee_failure']),None)
    recovery=next((s['end_minute'] for s in segments if first and s['start_minute']>=first['end_minute']
        and not s['classification']['p0_guarantee_failure']),None)
    out=dict(score_outcome_valid=bool(complete),score_P0_failure=1 if failed else 0 if complete else None,
        observed_until_minute=observed,cost_USD=cost if complete else None,
        observed_cost_USD=cost,first_failure_observed_minute=first['end_minute'] if first else None,
        first_failure_interval_start=first['start_minute'] if first else None,
        recovery_observed_minute=recovery,recovery_not_observed=bool(first and recovery is None))
    margins={g:[] for g in GROUPS}
    for d in p.get('decisions',[]):
        if start<=d['decision_time_min']<min(end,observed+1e-9):
            for zone,priority in ZONE_PRIORITY.items():
                value=d.get('features',{}).get('zone_margin_'+zone)
                if value is not None:margins[f'P{priority}'].append(value)
    for gi,g in enumerate(GROUPS):
        out[f'{g}_max_temperature_C']=temps[g] if complete else None
        out[f'observed_{g}_max_temperature_C']=temps[g]
        out[f'{g}_violation_minutes']=violations[g] if complete else None
        out[f'observed_{g}_violation_minutes']=violations[g]
        margin=min(margins[g]) if margins[g] else None
        out[f'{g}_min_zone_margin_C']=margin if complete else None
        out[f'observed_{g}_min_zone_margin_C']=margin
        for ki,k in enumerate(CARRIERS):
            name=f'{g}_{k}_ENS_kWh'
            out[name]=float(energy[gi,ki]) if complete else None
            out['observed_'+name]=float(energy[gi,ki])
    return out


def pair_category(a,b):
    if not a['score_outcome_valid'] or not b['score_outcome_valid']:return 'any_unknown'
    if a['score_P0_failure']!=b['score_P0_failure']:return 'one_P0_failure'
    if a['score_P0_failure']==1:return 'both_P0_failure'
    fields=[f'{g}_{k}_ENS_kWh' for g in GROUPS for k in CARRIERS]+['cost_USD']
    fields += [f'{g}_{kind}' for g in GROUPS for kind in ('max_temperature_C','violation_minutes')]
    comparisons=[]
    for k in fields:
        if a[k] is None or b[k] is None:
            if k=='P3_max_temperature_C' and a[k] is None and b[k] is None:continue
            raise ValueError('Missing modeled metric in complete-score pair: '+k)
        comparisons.append(np.isclose(a[k],b[k],rtol=0 if k.endswith('_max_temperature_C') else 1e-9,atol=1e-6))
    equal=all(comparisons)
    return 'both_no_P0_same_other' if equal else 'both_no_P0_other_different'


def check_payload(p):
    previous=0.;first=None
    for s in p['segments']:
        assert abs(s['start_minute']-previous)<1e-8, 'Noncontiguous actual subsegments'
        assert s['end_minute']>previous
        previous=s['end_minute']
        assert max(map(abs,s['energy_balance_residuals']))<=1e-4
        assert abs(s['steam_balance_residual_lbph'])<=1e-4
        if first is None and s['classification']['p0_guarantee_failure']:first=previous
    for d in p['decisions']:
        t=d['decision_time_min']
        assert all(m['available_time_min']<=t+1e-9 for m in d['feature_metadata'].values())
        assert all(c['generated_minute']<=t+1e-9 for c in d['known_command_queue'])
        for h,l in d['labels'].items():
            positive=first is not None and t<first<=t+float(h)
            risk=first is None or first>t
            full=previous>=t+float(h)-1e-9
            valid=risk and (positive or full)
            assert l['valid']==valid
            assert l['value']==(int(positive) if valid else None)
            status=p.get('summary',{}).get('status','completed')
            reason=(None if valid else 'post_first_failure' if not risk else
                {'completed':'administrative_end','model_constraint_infeasible':'model_error',
                 'solver_error':'solver_error','unsupported_model_regime':'unsupported_model_regime',
                 'year_end':'year_end'}.get(status,'unknown_termination'))
            expected=dict(at_risk=risk,full_horizon=full,unknown_reason=reason,
                administrative_censoring=reason in ('administrative_end','year_end'),
                model_error_truncated=status in ('model_constraint_infeasible','solver_error','unsupported_model_regime'),
                first_failure_observed_minute=first,window_left_open=True,window_right_closed=True,
                observed_window_minutes=max(0.,min(float(h),previous-t)))
            for key,value in expected.items():
                if key in l:assert l[key]==value,(key,l[key],value)
    summary=p.get('summary',{})
    if 'physical_service_failure' in summary:assert summary['physical_service_failure']==(first is not None)
    if 'first_failure_observed_minute' in summary:assert summary['first_failure_observed_minute']==first
    if 'p0_failed_any' in summary:
        assert summary['p0_failed_any']==(1 if first is not None else 0 if summary['status']=='completed' else None)
    if 'observed_until_minute' in summary:assert abs(summary['observed_until_minute']-previous)<1e-9
    if 'completed' in summary:assert summary['completed']==(summary['status']=='completed')
    if 'full_trajectory_outcome_valid' in summary:
        assert summary['full_trajectory_outcome_valid']==(summary['status']=='completed')
        if summary['full_trajectory_outcome_valid'] and 'requested_end_minute' in summary:
            assert abs(previous-summary['requested_end_minute'])<1e-9
    if p.get('summary',{}).get('P0_e_ENS_kWh') is not None:
        for gi,g in enumerate(GROUPS):
            for ki,k in enumerate(CARRIERS):
                total=sum(s['unserved_kW'][gi][ki]*(s['end_minute']-s['start_minute'])/60 for s in p['segments'])
                assert np.isclose(total,p['summary'][f'{g}_{k}_ENS_kWh'],atol=1e-6,rtol=1e-9)


def write_csv(path,rows):
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with Path(path).open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def audit(manifest_path,name):
    manifest_path=Path(manifest_path);base=manifest_path.parent
    m=json.loads(manifest_path.read_text(encoding='utf-8'));verify_manifest(m)
    specs={s['id']:s for s in m['scenarios']};rows=[];pairs=[];error_records=[]
    by_class=Counter();labels=Counter();cause=Counter();segment_count=0;decision_count=0
    maxbalance=0.;maxsteam=0.;starts=[];checkpoint_digests=[]
    for shard in m['p2b_shards']:
        folder=base/'shards'/shard['id']
        journal=folder/'attempts.jsonl'
        if not journal.exists():
            assert not list((folder/'checkpoints').glob('*.json.gz')), 'Checkpoint without journal'
            continue
        records=[json.loads(x) for x in journal.read_text(encoding='utf-8').splitlines()]
        opened=set();closed=set()
        for r in records:
            key=(r['scenario_id'],r['policy_id'])
            assert r['manifest_signature']==m['signature']
            assert key[0] in shard['scenario_ids'] and key[1] in m['policies']
            if r['event']=='started':
                assert key not in opened
                opened.add(key);starts.append(key)
            elif r['event'] in ('finished','reconciled_checkpoint','adjudicated_interrupted_unknown'):
                assert key in opened and key not in closed
                closed.add(key)
            else:raise AssertionError('Unknown journal event')
        for sid in shard['scenario_ids']:
            pair=[];scores=[]
            for policy in m['policies']:
                if not payload_path(folder,sid,policy).exists():continue
                p=load_payload(folder,sid,policy,m['signature']);check_payload(p)
                checkpoint_digests.append(checkpoint_digest(payload_path(folder,sid,policy),p,sid,policy,base))
                s=specs[sid];a=m['splits'][sid];summary=p['summary'];score=score_payload(p)
                row=dict(scenario_id=sid,policy_id=policy,study_stratum=s.get('study_stratum'),
                    experiment_role=s['experiment_role'],distribution_id=s['distribution_id'],
                    group_id=a['group_id'],root_family_id=s['root_family_id'],time_block_id=s['time_block_id'],
                    status=summary['status'],event_class=s['event_class'],initiating_device=s.get('initiating_device'),
                    physical_service_failure=summary.get('physical_service_failure',False),
                    full_trajectory_outcome_valid=summary.get('full_trajectory_outcome_valid',False),
                    source_checkpoint=str(payload_path(folder,sid,policy).relative_to(base)),**score)
                cause.update(summary.get('event_cause_counts',{}))
                row.update(summary.get('event_cause_counts',{}))
                row['economic_tiebreak_skipped_steps']=summary.get('economic_tiebreak_skipped_steps',0)
                row['solve_time_seconds']=sum(x.get('elapsed_seconds',0) for x in p.get('solver_calls',[]))
                rows.append(row);pair.append(p);scores.append(row)
                by_class[(s['event_class'],summary['status'])]+=1
                segment_count+=len(p['segments']);decision_count+=len(p['decisions'])
                for seg in p['segments']:
                    maxbalance=max(maxbalance,max(map(abs,seg['energy_balance_residuals'])))
                    maxsteam=max(maxsteam,abs(seg['steam_balance_residual_lbph']))
                for d in p['decisions']:
                    if not 60<=d['decision_time_min']<420:continue
                    for h,l in d['labels'].items():
                        labels[(s['experiment_role'],policy,h,'positive' if l['value']==1 else 'negative' if l['value']==0 else str(l['unknown_reason']))]+=1
                if p.get('error'):error_records.append(dict(scenario_id=sid,policy_id=policy,status=summary['status'],error=p['error']))
            if len(pair)==2:
                if all(p.get('initial_state') is not None for p in pair):
                    assert pair[0]['initial_state']==pair[1]['initial_state'],sid
                assert all(p.get('scenario')==specs[sid] for p in pair),sid
                assert all(p.get('seed',20261012)==20261012 for p in pair)
                for p in pair:
                    if p.get('identity'):assert p['identity']['group_id']==m['splits'][sid]['group_id']
                pairs.append(dict(scenario_id=sid,category=pair_category(*scores),
                    priority_P0=scores[0]['score_P0_failure'],secure_P0=scores[1]['score_P0_failure']))
    assert len(starts)==len(set(starts))<=800
    assert all(sid in specs and policy in m['policies'] for sid,policy in starts)
    missing_attempts=sorted(set(starts)-{(r['scenario_id'],r['policy_id']) for r in rows})
    assert not missing_attempts,('Started attempts without checkpoint; cannot pass audit',missing_attempts)
    assert set(starts)=={(r['scenario_id'],r['policy_id']) for r in rows},'Orphan checkpoint without attempted journal'
    dest=base/'audits'/name
    if dest.exists():raise FileExistsError('Audit attempt already exists')
    dest.mkdir(parents=True)
    write_csv(dest/'trajectory_scores.csv',rows);write_csv(dest/'paired_outcomes.csv',pairs)
    report=dict(passed=True,complete_budget=len(starts)==len(specs)*2 and len(pairs)==len(specs),
        manifest_signature=m['signature'],attempted_trajectories=len(starts),checkpoints=len(rows),
        attempted_specifications=len({sid for sid,_ in starts}),complete_pairs=len(pairs),
        status_counts=dict(Counter(r['status'] for r in rows)),
        score_valid=sum(r['score_outcome_valid'] for r in rows),
        score_unknown=sum(not r['score_outcome_valid'] for r in rows),
        observed_service_failure=sum(r['physical_service_failure'] for r in rows),
        score_observed_P0_failures=sum(r['score_P0_failure']==1 for r in rows),
        score_positive_root_families=len({r['root_family_id'] for r in rows if r['score_P0_failure']==1}),
        score_positive_connected_groups=len({r['group_id'] for r in rows if r['score_P0_failure']==1}),
        status_by_class=[dict(event_class=k[0],status=k[1],count=v) for k,v in by_class.items()],
        cause_counts=dict(cause),decision_attempts=decision_count,subsegments=segment_count,
        max_balance_residual_kW=maxbalance,max_steam_residual_lbph=maxsteam,
        pair_categories=dict(Counter(p['category'] for p in pairs)),
        labels=[dict(role=k[0],policy=k[1],horizon=k[2],kind=k[3],count=v) for k,v in labels.items()],
        legacy_q_design_field_note='Frozen P2A summary q_design is a legacy distribution-ID field; consult explicit P2B distribution_id and study measure',
        evaluation='complete score subset only; unknown fraction stays in attempted denominator')
    report['thermal_statistics_semantics']='Full score fields masked unless complete; observed_ fields prefix only. Zone margin is sampled at decision boundaries. No P3 zone model margin.'
    report['scores_sha256']=hashlib.sha256((dest/'trajectory_scores.csv').read_bytes()).hexdigest()
    report['audit_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['checkpoint_digests']=checkpoint_digests
    (dest/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (dest/'errors.json').write_text(json.dumps(error_records,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('labels','status_by_class','checkpoint_digests')},indent=2))
    return report


if __name__=='__main__':audit(sys.argv[1],sys.argv[2])
