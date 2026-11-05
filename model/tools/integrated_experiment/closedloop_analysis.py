"""Read-only whole-list, full-horizon developmental control comparison."""
from __future__ import annotations
import argparse
import copy
from collections import Counter
import json
from pathlib import Path

from .summarize import measures, metric_comparison, SERVICE_FIELDS


def compare_pair(before, after):
    complete = before['score_outcome_valid'] and after['score_outcome_valid']
    failure_delta = (None if before.get('score_P0_failure') is None or after.get('score_P0_failure') is None
                     else after['score_P0_failure'] - before['score_P0_failure'])
    return dict(service_comparison=metric_comparison(before, after, SERVICE_FIELDS) if complete else 'unknown',
                guarantee_comparison=metric_comparison(before, after, ['score_P0_failure']) if complete else 'unknown',
                fee_comparison=metric_comparison(before, after, ['actual_energy_fee_USD']) if complete else 'unknown',
                observed_failure_change=failure_delta,
                metric_differences={k: None if before.get(k) is None or after.get(k) is None else after[k]-before[k]
                                    for k in SERVICE_FIELDS + ['actual_energy_fee_USD']})


def analyze(plan_path, run_dir, out):
    from tools.integrated_learning.run_control import validate_plan, ROOT
    from tools.integrated_learning.branches import compare_continuation
    from tools.integrated_experiment.dependency import read, sha, reference
    from tools.p2b_experiment.runner import load_payload, payload_path
    from tools.p2b_experiment.audit import check_payload
    from hospital_ies.control_env import HospitalIESEnv
    plan, manifest = validate_plan(plan_path)
    run_dir, out = Path(run_dir), Path(out)
    if out.exists(): raise FileExistsError(out)
    report = read(run_dir/'CLOSED_LOOP_REPORT.json')
    if report['plan_signature'] != plan['signature']: raise ValueError('Closed-loop plan mismatch')
    ids = plan['closed_loop_scenario_ids']; policies = ['priority_milp', 'secure_milp', 'BC_selected']
    expected = {(sid, p) for sid in ids for p in policies}
    journal = [json.loads(line) for line in (run_dir/'attempts.jsonl').read_text(encoding='utf-8').splitlines()]
    for event in ('started', 'finished'):
        recorded = [(r['scenario_id'], r['policy_id']) for r in journal if r['event']==event]
        if len(recorded)!=len(set(recorded)) or set(recorded)!=expected:
            raise ValueError('Closed-loop whole-list attempt journal mismatch')
    env = HospitalIESEnv(ROOT, execution_contract=manifest['execution_contract'])
    specs = {s['id']:s for s in manifest['scenarios']}; rows=[]; reproduction=[]; score_rows=[]
    for sid in ids:
        initial=[]
        for policy in policies:
            path=run_dir/(sid+'__'+policy+'.json'); payload=read(path); check_payload(payload)
            if payload['scenario']!=specs[sid] or payload['seed']!=manifest['options']['seed']:
                raise ValueError('Closed-loop external specification or seed differs')
            initial.append(payload['initial_state'])
            summary=payload['summary']
            row=dict(scenario_id=sid, policy_id=policy, root_family_id=specs[sid]['root_family_id'],
                     condition_id=specs[sid]['condition_id'], role=specs[sid]['experiment_role'],
                     status=summary['status'], checkpoint=reference(path), **measures(payload,env,0.,480.))
            row.update(strategy_fallback_steps=summary['strategy_fallback_steps'],
                       projection_changed_steps=summary['projection_changed_steps'],
                       decision_steps=len(payload['decisions']))
            rows.append(row)
            score_rows.append(dict(scenario_id=sid,policy_id=policy,**measures(payload,env,60.,420.)))
            if policy!='BC_selected':
                shard=next(sh for sh in manifest['p2b_shards'] if sid in sh['scenario_ids'])
                folder=(ROOT/plan['manifest_path']).parent/'shards'/shard['id']
                original=load_payload(folder,sid,policy,manifest['signature'])
                # H120 is an additional export label, not a physical behavior.
                # Compare all originally registered label horizons unchanged.
                compare=copy.deepcopy(payload)
                for decision in compare['decisions']:
                    decision['labels']={h:v for h,v in decision['labels'].items()
                                        if float(h) in manifest['options']['label_horizons']}
                result=compare_continuation(original,compare,0.)
                reproduction.append(dict(scenario_id=sid,policy_id=policy,
                    original_checkpoint=reference(payload_path(folder,sid,policy)),**result))
        if initial[1:] != [initial[0],initial[0]]: raise ValueError('Unequal three-policy initial states')
    by={(r['scenario_id'],r['policy_id']):r for r in rows}; pairs=[]
    for sid in ids:
        for baseline in policies[:2]:
            pairs.append(dict(scenario_id=sid,baseline=baseline,condition_id=specs[sid]['condition_id'],
                              root_family_id=specs[sid]['root_family_id'],
                              **compare_pair(by[sid,baseline],by[sid,'BC_selected'])))
    result=dict(plan=reference(plan_path),run_report=reference(run_dir/'CLOSED_LOOP_REPORT.json'),
        planned_specifications=len(ids),planned_trajectories=len(expected),attempted_trajectories=len(rows),
        full_horizon_minutes=[0,480],primary_score_minutes=[60,420],
        comparison_absolute_tolerance=1e-6,comparison_semantics='Coordinate service comparisons and fees separate; original guarantee standard unchanged',
        source_sha256=sha(__file__),baseline_registered_reproduction_passed=all(r['passed'] for r in reproduction),
        baseline_registered_comparisons=len(reproduction),
        per_policy={p:dict(status_counts=dict(Counter(r['status'] for r in rows if r['policy_id']==p)),
            full_outcomes_known=sum(r['score_outcome_valid'] for r in rows if r['policy_id']==p),
            observed_P0_failures=sum(r['score_P0_failure']==1 for r in rows if r['policy_id']==p),
            observed_P0_negative_complete=sum(r['score_P0_failure']==0 for r in rows if r['policy_id']==p),
            P0_unknown=sum(r['score_P0_failure'] is None for r in rows if r['policy_id']==p),
            strategy_fallback_steps=sum(r['strategy_fallback_steps'] for r in rows if r['policy_id']==p),
            projection_changed_steps=sum(r['projection_changed_steps'] for r in rows if r['policy_id']==p)) for p in policies},
        comparisons={p:{name:dict(Counter(r[name] for r in pairs if r['baseline']==p))
                       for name in ('service_comparison','guarantee_comparison','fee_comparison')} for p in policies[:2]},
        scope='All preregistered developmental backgrounds, not final blind, real hospital safety or causal rescue certification')
    out.mkdir(parents=True)
    for name,value in [('CLOSED_LOOP_ANALYSIS.json',result),('full_horizon_trajectories.json',rows),
                       ('score_window_trajectories.json',score_rows),('paired_comparisons.json',pairs),
                       ('registered_baseline_reproduction.json',reproduction)]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result['baseline_registered_reproduction_passed']:
        raise AssertionError('Original baseline reproduction failed; evidence retained')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('plan');p.add_argument('run_dir');p.add_argument('out')
    a=p.parse_args();analyze(a.plan,a.run_dir,a.out)
