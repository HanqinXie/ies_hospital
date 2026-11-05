"""Read-only P2B reporting from final audited checkpoints; never runs a simulator."""
import csv
import json
import sys
from pathlib import Path
from collections import Counter, defaultdict
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.p2b_experiment.runner import load_payload
from tools.p2b_experiment.audit import write_csv


def main(base):
    base=Path(base); experiment=base/'experiment'
    m=json.loads((experiment/'global_manifest.json').read_text(encoding='utf-8'))
    audit=json.loads((experiment/'audits/final/audit.json').read_text(encoding='utf-8'))
    assert audit['passed'] and audit['complete_budget']
    out=base/'report_data'
    out.mkdir(exist_ok=False)
    with (experiment/'audits/final/trajectory_scores.csv').open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
    with (experiment/'audits/final/paired_outcomes.csv').open(encoding='utf-8',newline='') as f:pairs=list(csv.DictReader(f))
    grouped=defaultdict(list)
    for r in rows:grouped[(r['experiment_role'],r['event_class'],r['policy_id'])].append(r)
    groups=[]
    for (role,cl,policy),rr in grouped.items():
        pos=[r for r in rr if r['score_P0_failure']=='1'];valid=[r for r in rr if r['score_outcome_valid']=='True']
        groups.append(dict(role=role,event_class=cl,policy=policy,attempted=len(rr),completed=sum(r['status']=='completed' for r in rr),score_valid=len(valid),score_unknown=len(rr)-len(valid),observed_P0=sum(r['physical_service_failure']=='True' for r in rr),score_P0=len(pos),positive_root_families=len({r['root_family_id'] for r in pos}),positive_groups=len({r['group_id'] for r in pos}),all_root_families=len({r['root_family_id'] for r in rr}),all_groups=len({r['group_id'] for r in rr})))
    write_csv(out/'coverage_status_by_role_class_policy.csv',groups)
    continuous=[]
    fields=[f'P{g}_{c}_ENS_kWh' for g in range(4) for c in 'ehc']+['cost_USD']+[f'P{g}_{k}' for g in range(4) for k in ['max_temperature_C','min_zone_margin_C','violation_minutes']]
    for key,rr in grouped.items():
        for field in fields:
            values=np.array([float(r[field]) for r in rr if r['score_outcome_valid']=='True' and r[field]!=''])
            if len(values):continuous.append(dict(role=key[0],event_class=key[1],policy=key[2],metric=field,N=len(values),mean=values.mean(),min=values.min(),max=values.max(),q95=np.quantile(values,.95)))
    write_csv(out/'complete_score_consequences.csv',continuous)
    ledger=[];events=[];labels=Counter();failures=[];solver=Counter();skips=0;calls=0;times=[];error_stages=Counter()
    for shard in m['p2b_shards']:
        directory=experiment/'shards'/shard['id']
        journal=[json.loads(x) for x in (directory/'attempts.jsonl').read_text(encoding='utf-8').splitlines()]
        with (out/'all_attempt_events.jsonl').open('a',encoding='utf-8') as f:
            for r in journal:f.write(json.dumps(dict(shard=shard['id'],**r),ensure_ascii=False)+'\n')
        for sid in shard['scenario_ids']:
            for policy in m['policies']:
                p=load_payload(directory,sid,policy,m['signature']);s=p['scenario'];a=p['summary']
                sc=next(r for r in rows if r['scenario_id']==sid and r['policy_id']==policy)
                ledger.append(dict(scenario_id=sid,policy=policy,role=s['experiment_role'],event_class=s['event_class'],initiating_device=s.get('initiating_device'),root_family=s['root_family_id'],group_id=m['splits'][sid]['group_id'],time_block=s['time_block_id'],status=a['status'],steps=a.get('steps'),observed_until=a.get('observed_until_minute'),first_failure=a.get('first_failure_observed_minute'),score_outcome_valid=sc['score_outcome_valid'],error_type=(p.get('error') or {}).get('type'),checkpoint=sc['source_checkpoint']))
                for event in p['events']:events.append(dict(scenario_id=sid,policy=policy,event=event))
                if p.get('error'):failures.append(dict(scenario_id=sid,policy=policy,event_class=s['event_class'],role=s['experiment_role'],observed_until=a.get('observed_until_minute'),observed_P0=a['physical_service_failure'],error=p['error']))
                for e in p.get('solver_errors',[]):error_stages[(e.get('phase'),e.get('message'))]+=1
                for call in p.get('solver_calls',[]):
                    solver[(call.get('phase'),call.get('status'))]+=1;calls+=1;times.append(call.get('elapsed_seconds',0))
                skips+=a.get('economic_tiebreak_skipped_steps',0)
                for d in p['decisions']:
                    if not 60<=d['decision_time_min']<420:continue
                    for h,l in d['labels'].items():
                        kind='positive' if l['value']==1 else 'negative' if l['value']==0 else l['unknown_reason']
                        labels[(s['experiment_role'],s['event_class'],policy,h,kind,l['full_horizon'])]+=1
    write_csv(out/'all_800_trajectory_ledger.csv',ledger)
    write_csv(out/'label_counts_by_class.csv',[dict(role=k[0],event_class=k[1],policy=k[2],H=k[3],kind=k[4],full_horizon=k[5],count=v) for k,v in labels.items()])
    for filename,data in [('events.jsonl',events),('failures.jsonl',failures)]:
        with (out/filename).open('w',encoding='utf-8') as f:
            for r in data:f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n')
    byid=defaultdict(dict)
    for r in rows:byid[r['scenario_id']][r['policy_id']]=r
    comparison=[]
    for pair in pairs:
        a=byid[pair['scenario_id']]['priority_milp'];b=byid[pair['scenario_id']]['secure_milp'];r=dict(pair)
        if a['score_outcome_valid']==b['score_outcome_valid']=='True':
            for field in fields:
                if a[field]!='' and b[field]!='':r['secure_minus_priority_'+field]=float(b[field])-float(a[field])
        comparison.append(r)
    write_csv(out/'paired_deltas.csv',comparison)
    result=dict(audit_sha256=__import__('hashlib').sha256((experiment/'audits/final/audit.json').read_bytes()).hexdigest(),attempts=len(ledger),root_families=len({r['root_family'] for r in ledger}),groups=len({r['group_id'] for r in ledger}),time_blocks=len({r['time_block'] for r in ledger}),solver_calls=calls,solver_counts=[dict(phase=k[0],status=k[1],count=v) for k,v in solver.items()],solver_seconds=sum(times),solver_max_seconds=max(times),economic_tiebreak_skipped_steps=skips,statuses=dict(Counter(r['status'] for r in ledger)),event_records=len(events),coverage=groups,examples=dict(unknown=[r['scenario_id'] for r in rows if r['score_outcome_valid']=='False'][:10],one_P0=[r['scenario_id'] for r in pairs if r['category']=='one_P0_failure'],observed_P0=[r['scenario_id'] for r in rows if r['score_P0_failure']=='1'][:10]),negative_probability_claim='No real hospital incident probability or final blind-test estimate')
    result['score_P0_binary_state_counts']=dict(Counter('known_positive' if r['score_P0_failure']=='1' else 'known_negative' if r['score_P0_failure']=='0' else 'unknown' for r in rows))
    result['observed_positive_with_incomplete_score']=sum(r['score_P0_failure']=='1' and r['score_outcome_valid']=='False' for r in rows)
    result['error_stages']=[dict(phase=k[0],message=k[1],count=v) for k,v in error_stages.items()]
    assert len(ledger)==800 and len(pairs)==400
    (out/'summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('coverage','examples')},indent=2))


if __name__=='__main__':main(sys.argv[1])
