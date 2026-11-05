"""Finite actual D0/action branches; every attempt has immutable output."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import time
import traceback
import numpy as np
from hospital_ies.dataset_export import json_clean
from .episode import run_episode
from .snapshot import capture_execution,ExecutionSnapshot
from .policies import BaselinePolicy
from .branches import compare_continuation,current_candidates,FirstRequestPolicy
from .models import write,sha


def save(path,payload):write(path,json_clean(payload))


def journal(path,**record):
    with Path(path).open('a',encoding='utf-8') as f:
        f.write(json.dumps(dict(time_unix=time.time(),**record),ensure_ascii=False,allow_nan=False)+'\n')


def actual_energy_fee(env,segment):
    """Original illustrative tariffs, excluding all artificial objective terms."""
    from tools.integrated_experiment.energy_metrics import segment_energy_fee_USD
    return segment_energy_fee_USD(segment,env.plant,env.hourly)


def window_outcome(payload,start,horizon,env=None):
    end=start+horizon;segments=payload['segments'];prefix=np.zeros((4,3));covered=0.;cost=0.;fee=0.;fail=[]
    for s in segments:
        overlap=max(0.,min(end,s['end_minute'])-max(start,s['start_minute']))
        if not overlap:continue
        covered+=overlap;prefix+=np.asarray(s['unserved_kW'])*overlap/60
        fraction=overlap/(s['end_minute']-s['start_minute']);cost+=s['cost_USD_step']*fraction
        if env is not None:fee+=actual_energy_fee(env,s)*fraction
        if s['classification']['p0_guarantee_failure']:fail.append(s['end_minute'])
    full=abs(covered-horizon)<=1e-8
    return {'start_minute':start,'horizon_minutes':horizon,'observed_minutes':covered,'full_horizon':full,
        'ENS_kWh_by_priority_carrier':prefix.tolist() if full else None,
        'observed_prefix_ENS_kWh_by_priority_carrier':prefix.tolist(),
        'accounting_cost_USD':cost if full else None,'observed_prefix_accounting_cost_USD':cost,
        'actual_energy_fee_USD':fee if full and env is not None else None,
        'P0_failure_in_window':1 if fail else 0 if full else None,
        'first_failure_classification_in_window_minute':min(fail) if fail else None,
        'status':payload['summary']['status'],'unknown_after_window_prefix':not full,
        'failure_semantics':'Original subinterval classifier; already-failed current states are not first-failure forecasts',
        'cost_semantics':'accounting cost inherits unused-steam tie-break; actual fee excludes security/tracking/tie-break and uses illustrative tariffs'}


def run_snapshot_branches(env,scenario,seed,manifest,out,*,snapshot_step=6,baseline_payload=None,counterfactual=True):
    """One snapshot, three attempts including original D0; never resample."""
    from tools.integrated_experiment.runner import verify_manifest
    verify_manifest(manifest)
    registered=next((s for s in manifest['scenarios'] if s['id']==scenario['id']),None)
    if scenario!=registered:raise ValueError('Branch scenario differs from registered exogenous specification')
    if counterfactual and scenario['experiment_role']!='pilot_train':raise ValueError('Counterfactual labels are training-side only')
    out=Path(out)
    if out.exists():raise FileExistsError('Retain all attempted branches; fresh directory required')
    out.mkdir(parents=True);log=out/'attempts.jsonl';snapshots={}
    horizons=tuple(manifest['options']['label_horizons'])
    save(out/'plan.json',{'scenario_id':scenario['id'],'seed':seed,'snapshot_step':snapshot_step,
        'manifest_signature':manifest['signature'],'candidate_definitions':['original secure','GT minus10% plus requested grid','ABS minus10%'],
        'numeric_absolute_tolerance':1e-7,'time_absolute_tolerance':1e-10,'horizons':[60,120],
        'counterfactual_enabled':counterfactual,
        'truth_budget_this_snapshot':{'capture_trajectory':1,'candidate_trajectories':3 if counterfactual else 1,
                                     'current_planning_queries':1 if counterfactual else 0}})
    journal(log,event='started',attempt_type='snapshot_capture',scenario_id=scenario['id'],policy_id='secure_milp')
    reference=run_episode(env,scenario,BaselinePolicy('secure_milp'),seed,manifest,
        horizons=horizons,snapshot_steps=(snapshot_step,),snapshots=snapshots)
    save(out/'reference_capture.json',reference)
    journal(log,event='finished',attempt_type='snapshot_capture',status=reference['summary']['status'])
    if baseline_payload is not None:
        comparison=compare_continuation(baseline_payload,reference,0.)
        save(out/'capture_vs_original.json',comparison)
        if not comparison['passed']:raise AssertionError('External capture changed original baseline physical trajectory')
    if len(snapshots)!=1:
        save(out/'blocked.json',{'reason':'Registered snapshot not reached','reference_status':reference['summary']['status'],
            'branches_attempted':0,'resampled':False})
        return {'passed':False,'reason':'snapshot_not_reached','branches_attempted':0}
    t,item=next(iter(snapshots.items()));execution=item['execution'];execution.save(out/'execution_snapshot.json')
    reference_final=capture_execution(env,binding=execution.binding)
    # Validate the portable serialized restore, not just an in-memory deepcopy.
    restored=ExecutionSnapshot.load(out/'execution_snapshot.json',template=env,expected_binding=execution.binding)
    fork=restored.fork();journal(log,event='started',attempt_type='D0_original_continuation',time_minute=t)
    continuation=run_episode(fork,scenario,BaselinePolicy('secure_milp'),seed,manifest,
        reset=False,prefix_decisions=item['prefix_decisions'],horizons=horizons)
    save(out/'D0_original_continuation.json',continuation)
    d0=compare_continuation(reference,continuation,t,reference_final=reference_final,
        restored_final=capture_execution(fork,binding=execution.binding))
    save(out/'D0_comparison.json',d0)
    journal(log,event='finished',attempt_type='D0_original_continuation',status=continuation['summary']['status'],D0_passed=d0['passed'])
    if not d0['passed']:
        save(out/'blocked.json',{'reason':'D0 original continuation differs','branches_attempted':1,'resampled':False})
        return {'passed':False,'reason':'D0_failed','branches_attempted':1}
    if not counterfactual:
        result={'passed':True,'D0_passed':True,'branches_attempted':1,'capture_trajectories_attempted':1,
            'scenario_id':scenario['id'],'snapshot_minute':t,'training_labels_generated':False,
            'execution_snapshot_sha256':sha(out/'execution_snapshot.json')}
        save(out/'D0_RESULT.json',result);verify_manifest(manifest);return result
    current=restored.fork();current.core.boundary()
    from hospital_ies.data_contract import decision_snapshot
    observation=decision_snapshot(current)
    journal(log,event='started',attempt_type='isolated_current_planning_query',time_minute=t)
    try:
        plan=current.core.generate_command({'security':{'gt_loading_penalty_USD_per_kWh':.35}})
    except Exception as exc:
        save(out/'planning_error.json',{'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc(),
            'solver_errors':current.solver_errors,'solver_calls':current.core.solver_calls})
        journal(log,event='finished',attempt_type='isolated_current_planning_query',status='error',time_minute=t)
        raise
    journal(log,event='finished',attempt_type='isolated_current_planning_query',status='success',time_minute=t)
    save(out/'current_plan.json',{'command':plan,'solver_calls':current.core.solver_calls,'events':current.event_history})
    candidates=current_candidates([observation],plan['dispatch'])
    save(out/'candidates.json',{'snapshot_minute':t,'candidates':candidates,'future_used_for_generation':False})
    outcomes=[dict(candidate=candidates[0],windows=[window_outcome(continuation,t,h,fork) for h in (60,120)])]
    for candidate in candidates[1:]:
        fork=restored.fork();name=candidate['candidate_id']
        policy=FirstRequestPolicy(candidate['request'],'secure_milp',name)
        journal(log,event='started',attempt_type='counterfactual',candidate_id=name,time_minute=t)
        payload=run_episode(fork,scenario,policy,seed,manifest,reset=False,
            prefix_decisions=item['prefix_decisions'],horizons=horizons)
        save(out/(name+'.json'),payload)
        journal(log,event='finished',attempt_type='counterfactual',candidate_id=name,status=payload['summary']['status'])
        outcomes.append(dict(candidate=candidate,windows=[window_outcome(payload,t,h,fork) for h in (60,120)],
            projection_changed_steps=payload['summary']['projection_changed_steps'],
            first_permitted_command=payload['decisions'][0].get('permitted_command') if payload['decisions'] else None,
            first_arrived_commands=payload['decisions'][0].get('arrived_commands') if payload['decisions'] else None))
    result={'passed':True,'D0_passed':True,'branches_attempted':3,'capture_trajectories_attempted':1,
            'scenario_id':scenario['id'],'snapshot_minute':t,'outcomes':outcomes,
            'execution_snapshot_sha256':sha(out/'execution_snapshot.json'),'future_for_policy':False}
    save(out/'BRANCH_RESULT.json',result);verify_manifest(manifest);return result
