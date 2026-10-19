"""Read actual P2A artifacts, independently recompute contract checks and totals."""
import csv
import json
from pathlib import Path
import sys
from collections import Counter
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.dataset_manifest import digest,load_checkpoint,checkpoint_path
from hospital_ies.dataset_export import write_json
from hospital_ies.data_contract import family_splits
BASE=ROOT/'outputs/v43_p2a_20261013_120000'

def audit(name):
    run=BASE/name;plan=json.loads((BASE/'diagnostic_plan.json').read_text(encoding='utf-8'))
    payloads=[];summaries=[];splits=[];metadata={};checks=[];ledger=[]
    for batch in plan['batches']:
        directory=run/batch['name'];m=json.loads((directory/'manifest_p2a.json').read_text(encoding='utf-8'))
        metadata[batch['name']]=json.loads((directory/'dataset_metadata.json').read_text(encoding='utf-8'))
        splits.extend(json.loads((directory/'split_manifest.json').read_text(encoding='utf-8'))['rows'])
        for s in m['scenarios']:
            for policy in m['policies']:
                p=load_checkpoint(checkpoint_path(directory,s['id'],policy),m['signature'])
                payloads.append(p);summaries.append(p['summary'])
                energy=np.zeros((4,3));previous=0.
                for segment in p['segments']:
                    assert abs(segment['start_minute']-previous)<1e-8,('time gap',s['id'])
                    assert segment['end_minute']>segment['start_minute']
                    previous=segment['end_minute']
                    energy+=np.array(segment['unserved_kW'])*(segment['end_minute']-segment['start_minute'])/60
                    assert max(map(abs,segment['energy_balance_residuals']))<1e-4
                    assert abs(segment['steam_balance_residual_lbph'])<1e-4
                for gi,g in enumerate(('P0','P1','P2','P3')):
                    for ki,k in enumerate(('e','h','c')):assert abs(energy[gi,ki]-p['summary'][f'{g}_{k}_ENS_kWh'])<1e-6
                if p['error'] and not p['summary']['physical_service_failure']:
                    assert p['summary']['p0_failed_any'] is None
                for d in p['decisions']:
                    t=d['decision_time_min'];assert t==d['observation_time_min']
                    assert all(x['available_time_min']<=t for x in d['feature_metadata'].values())
                    assert all(c['generated_minute']<=t for c in d['known_command_queue'])
                    for horizon,label in d['labels'].items():
                        if label['value']==0:assert previous>=t+float(horizon)-1e-9
                        if label['value']==1:assert t<label['first_failure_observed_minute']<=t+float(horizon)
                        if not label['valid']:assert label['value'] is None
                        if label['model_error_truncated']:assert not label['administrative_censoring']
                row={k:p['summary'][k] for k in ('scenario_id','policy_id','event_class','status','steps','physical_service_failure',
                    'p0_failed_any','observed_until_minute','first_failure_observed_minute','full_trajectory_outcome_valid',
                    'cost_USD_observed','max_balance_residual_kW','max_steam_residual_lbph')}
                row.update(batch=batch['name'],segment_count=len(p['segments']),
                    **p['summary']['event_cause_counts'],error_type=p['error']['type'] if p['error'] else None)
                ledger.append(row)
    original=json.loads((BASE/'baseline_replay/scenarios.json').read_text(encoding='utf-8'))
    for s in original:
        pair=[p for p in payloads if p['identity']['scenario_id']==s['id']]
        assert len(pair)==2
        assert pair[0]['initial_state']==pair[1]['initial_state']
        for p in pair:
            baseline=json.loads((BASE/'baseline_replay'/(s['id']+'__'+p['identity']['policy_id']+'.json')).read_text(encoding='utf-8'))
            assert p['seed']==baseline['seed']==20261012
            assert {k:p['scenario'][k] for k in s}==s
            assert p['initial_state']['state']==baseline['initial_state']
            checks.append(dict(scenario_id=s['id'],policy=p['identity']['policy_id'],
                original_scenario_equal=True,original_seed_equal=True,original_initial_state_equal=True))
    for sid in {p['identity']['scenario_id'] for p in payloads}:
        pair=[p for p in payloads if p['identity']['scenario_id']==sid]
        assert len(pair)==2 and pair[0]['scenario']==pair[1]['scenario']
        assert pair[0]['initial_state']==pair[1]['initial_state']
    global_splits=family_splits(splits,seed=20261012,embargo_minutes=60)
    status=Counter(s['status'] for s in summaries)
    first=[s for s in summaries if s['physical_service_failure']]
    report=dict(source_run=name,artifact_checks_passed=True,scenario_specifications=len({p['identity']['scenario_id'] for p in payloads}),
        conservative_independent_groups=len({x['group_id'] for x in global_splits.values()}),
        attempted=len(payloads),status_counts=dict(status),observed_service_failure=len(first),
        unknown_full_outcome=sum(not s['full_trajectory_outcome_valid'] for s in summaries),
        completed_steps=sum(s['steps'] for s in summaries),observed_subintervals=sum(len(p['segments']) for p in payloads),
        max_balance_residual_kW=max(s['max_balance_residual_kW'] or 0 for s in summaries),
        max_steam_residual_lbph=max(s['max_steam_residual_lbph'] or 0 for s in summaries),
        event_cause_counts={k:sum(s['event_cause_counts'][k] for s in summaries) for k in summaries[0]['event_cause_counts']},
        original_18_trajectories_checked=len(checks),metadata=metadata,
        by_event_class={e:dict(Counter(s['status'] for s in summaries if s['event_class']==e)) for e in sorted({s['event_class'] for s in summaries})},
        service_failure_ids=[dict(id=s['scenario_id'],policy=s['policy_id'],time=s['first_failure_observed_minute']) for s in first],
        error_ids=[dict(id=p['identity']['scenario_id'],policy=p['identity']['policy_id'],status=p['summary']['status'],
                       observed_until=p['summary']['observed_until_minute'],message=p['error']['message']) for p in payloads if p['error']],
        split_counts=dict(Counter(x['split'] for x in global_splits.values())),
        training_or_blind_test=False,posthoc_resampling=False)
    write_json(run/'DIAGNOSTIC_AUDIT.json',report);write_json(run/'original_pairing_checks.json',checks)
    write_json(run/'global_lineage_groups.json',global_splits)
    with (run/'attempt_ledger.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(ledger[0]));w.writeheader();w.writerows(ledger)
    print(json.dumps({k:v for k,v in report.items() if k not in ('metadata',)},ensure_ascii=True,indent=2))

if __name__=='__main__':audit(sys.argv[1])
