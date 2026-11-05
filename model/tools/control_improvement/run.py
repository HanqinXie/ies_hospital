"""Register and execute bounded new-version action/ranking research."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import time
import numpy as np
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.dataset_manifest import digest
from tools.integrated_experiment.runner import ROOT
from tools.integrated_learning.truth import save,journal
from tools.integrated_learning.policies import BaselinePolicy,PolicyController
from tools.integrated_learning.models import load_predictor
from tools.p2b_experiment.runner import load_payload,payload_path
from .branches import source_hashes,run_branch_snapshot,window_targets
from .models import sha,write,fit_rank_models,load_rank_model,SEEDS
from .ranking import fit_demand_scale,ranking_metrics
from .ranking import paired_delta
from .ranking import RESEARCH_RESOLUTION
from .candidates import candidate_numeric_features
from .runtime import ExecutionAwarePolicy
from .episode import run_policy_episode


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def verify(m):
    from .data_runner import verify_manifest
    return verify_manifest(m)


def select_prototype_snapshots(rows,ids):
    selected=[]
    for sid,t in [(sid,180.) for sid in ids[:6]]+[(ids[0],60.),(ids[1],420.)]:
        row=next((r for r in rows if r['scenario_id']==sid and float(r['snapshot_minute'])==t),None)
        if row is None or row['experiment_role']!='pilot_train':raise ValueError('Prototype snapshot absent from registered training plan')
        selected.append(copy.deepcopy(row))
    return selected


def freeze_branch_plan(manifest_path,snapshot_plan_path,out,*,prototype_ids_path=None,
                       continuation_policy='priority_milp',bounded_horizon=False,prototype_train_only=False):
    mp=Path(manifest_path);m=read(mp);verify(m);sp=Path(snapshot_plan_path);registered=read(sp)
    if sha(ROOT/registered['proposal_path'])!=registered['proposal_sha256']:raise ValueError('Frozen snapshot proposal bytes changed')
    if continuation_policy not in ('priority_milp','current_rule_projection0'):raise ValueError('Unregistered continuation recipe')
    if continuation_policy!='priority_milp' and not bounded_horizon:raise ValueError('Version2 continuation requires explicit bounded observation')
    rows=registered['snapshots']
    if prototype_ids_path:rows=select_prototype_snapshots(rows,read(prototype_ids_path)['scenario_ids'])
    specs={s['id']:s for s in m['scenarios']}
    for r in rows:
        s=specs[r['scenario_id']]
        if r['experiment_role']!=s['experiment_role'] or r['group_id']!=m['splits'][s['id']]['group_id']:raise ValueError('Snapshot role/group differs from actual manifest')
        if r['snapshot_step']*float(s['step_minutes'])!=r['snapshot_minute']:raise ValueError('Snapshot absolute clock differs from step')
        if r['snapshot_minute']+120>float(s['step_minutes'])*s['duration_steps']:raise ValueError('Registered fullH120 unsupported')
    out=Path(out)
    if out.exists():raise FileExistsError('Frozen branch plan cannot be overwritten')
    plan={'schema':'control_improvement.branch_plan.v1','manifest_path':mp.resolve().relative_to(ROOT).as_posix(),
        'manifest_sha256':sha(mp),'manifest_signature':m['signature'],'snapshot_plan_path':sp.resolve().relative_to(ROOT).as_posix(),
        'snapshot_plan_sha256':sha(sp),'source_sha256':source_hashes(),'snapshots':rows,'prototype_train_only':bool(prototype_ids_path or prototype_train_only),
        'maximum_capture_attempts':len(rows),'maximum_continuation_attempts':len(rows)*(9 if bounded_horizon else 8),
        'maximum_current_planning_queries':len(rows)*9,'unknowns_remain_null':True,'conditional_futures':1,
        'continuation_policy':continuation_policy,'selection_uses_outcomes':False,
        'D0_continuation_policy':'priority_milp','bounded_candidate_horizon_minutes':120 if bounded_horizon else None,
        'research_preference_resolution':RESEARCH_RESOLUTION if bounded_horizon else None}
    plan['signature']=digest(plan);write(out,plan);return plan


def validate_plan(path):
    p=read(path);body=copy.deepcopy(p);signature=body.pop('signature')
    if digest(body)!=signature or p['source_sha256']!=source_hashes():raise ValueError('Registered learning plan/source changed')
    mp=ROOT/p['manifest_path']
    if sha(mp)!=p['manifest_sha256']:raise ValueError('Parent manifest bytes changed')
    m=read(mp);verify(m)
    if m['signature']!=p['manifest_signature']:raise ValueError('Parent manifest signature changed')
    if 'snapshot_plan_path' in p and sha(ROOT/p['snapshot_plan_path'])!=p['snapshot_plan_sha256']:raise ValueError('Parent snapshot list changed')
    return p,m


def bound_baseline(mp,m,sid,policy='priority_milp'):
    shard=next(sh for sh in m['p2b_shards'] if sid in sh['scenario_ids']);folder=Path(mp).parent/'shards'/shard['id']
    payload=load_payload(folder,sid,policy,m['signature']);path=payload_path(folder,sid,policy)
    spec=next(s for s in m['scenarios'] if s['id']==sid)
    if payload['scenario']!=spec or payload['seed']!=m['options']['seed']:raise ValueError('Baseline accident/seed changed')
    return payload,{'path':path.resolve().relative_to(ROOT).as_posix(),'sha256':sha(path),'canonical_payload_sha256':digest(payload)}


def run_branch_plan(plan_path,out):
    plan,m=validate_plan(plan_path);out=Path(out)
    if out.exists():raise FileExistsError('Every branch experiment needs fresh output')
    out.mkdir(parents=True);write(out/'registered_plan.json',plan);specs={s['id']:s for s in m['scenarios']}
    env=HospitalIESEnv(ROOT,execution_contract=m['execution_contract']);results=[]
    for i,r in enumerate(plan['snapshots']):
        baseline,binding=bound_baseline(ROOT/plan['manifest_path'],m,r['scenario_id'])
        directory=out/f'snapshot_{i:03d}';write(out/f'baseline_{i:03d}.json',binding)
        result=run_branch_snapshot(env,specs[r['scenario_id']],m['options']['seed'],m,directory,
            snapshot_step=r['snapshot_step'],baseline_payload=baseline,role=r['experiment_role'],group_id=r['group_id'],
            continuation_policy=plan['continuation_policy'],bounded_horizon=bool(plan.get('bounded_candidate_horizon_minutes')))
        results.append({'directory':directory.name,'result_sha256':sha(directory/'BRANCH_RESULT.json'),
            'scenario_id':r['scenario_id'],'snapshot_minute':r['snapshot_minute'],'passed':result['passed'],
            'capture_attempts':result['capture_attempts'],'continuation_attempts':result['continuation_attempts'],
            'current_planning_queries':result.get('current_planning_queries',0),'unknown_outcomes':result.get('unknown_outcomes',0),
            'reason':result.get('reason'),'truth_integrity_passed':result.get('truth_integrity_passed',result['passed']),
            'unreached_snapshot':result.get('unknown_snapshot',False)})
        # D0 contamination blocks only this truth-version branch. A normal
        # solver-truncated candidate still remains as unknown in its ledger.
        if not result['passed'] and result.get('reason')!='snapshot_not_reached':break
    report={'plan_signature':plan['signature'],'snapshots_registered':len(plan['snapshots']),'snapshots_attempted':len(results),
        'capture_attempts':sum(r['capture_attempts'] for r in results),'continuation_attempts':sum(r['continuation_attempts'] for r in results),
        'current_planning_queries':sum(r['current_planning_queries'] for r in results),'results':results,
        'all_reached_D0_passed':all(r['passed'] for r in results if not r['unreached_snapshot']),
        'all_D0_passed':all(r['passed'] for r in results),'truth_integrity_passed':all(r['truth_integrity_passed'] for r in results),
        'unreached_snapshots':sum(r['unreached_snapshot'] for r in results),'prototype_train_only':plan['prototype_train_only']}
    validate_plan(plan_path);write(out/'ACTION_BRANCH_REPORT.json',report);return report


def evaluate_registered_states(rows,predictor,scale,registered_states,*,resolution=None):
    """Keep every fixed state in the denominator; unknown best => null regret."""
    bystate={};evaluations=[]
    for r in rows:bystate.setdefault((r['scenario_id'],float(r['snapshot_minute'])),[]).append(r)
    for state in registered_states:
        sid=state['scenario_id'];t=float(state['snapshot_minute']);items=bystate.get((sid,t),[])
        items=sorted(items,key=lambda r:r['candidate_index'])
        indices=[r['candidate_index'] for r in items]
        if len(indices)!=len(set(indices)) or any(i not in range(8) for i in indices):raise ValueError('Invalid registered action indices')
        count=np.zeros(19,int);errors=np.zeros(19);zero=np.zeros(19);pred=None;error=None
        if items:
            try:pred=np.asarray(predictor.predict(np.asarray([r['x'] for r in items])),float)
            except Exception as e:error={'type':type(e).__name__,'message':str(e)}
            if pred is not None and (pred.shape!=(len(items),19) or not np.all(np.isfinite(pred))):
                error={'type':'InvalidPrediction','message':'Rank outputs unavailable/nonfinite'};pred=None
            for i,r in enumerate(items):
                for j,(value,valid) in enumerate(zip(r['delta_y'],r['target_mask'])):
                    if valid and value is not None:
                        count[j]+=1;zero[j]+=abs(float(value))
                        if pred is not None:errors[j]+=abs(float(value)-pred[i,j])
        full=len(items)==8 and all(all(r['target_mask']) for r in items) and pred is not None
        result={'scenario_id':sid,'snapshot_minute':t,'group_id':state['group_id'],'candidate_rows_observed':len(items),
            'missing_action_indices':sorted(set(range(8))-set(indices)),'full_ranking_outcome_valid':full,
            'known_target_count':count.tolist(),'difference_MAE_by_target':[float(errors[j]/n) if n and pred is not None else None for j,n in enumerate(count)],
            'zero_effect_difference_MAE_by_target':[float(zero[j]/n) if n else None for j,n in enumerate(count)],
            'selected_regret_by_target':None,'rule_regret_by_target':None,'reference_regret_by_target':None,
            'pairwise_order_accuracy':None,'selected_action_index':None,'predicted_tie_count':None,'prediction_error':error,
            'unknown_reason':None if full else 'snapshot_unreached' if not items else 'incomplete_action_outcomes_or_prediction'}
        if full:result.update(ranking_metrics([r['delta_y'] for r in items],pred,scale,reference_index=0,rule_index=items[0]['rule_index'],resolution=resolution))
        evaluations.append(result)
    return {'registered_state_count':len(registered_states),'full_ranking_state_count':sum(r['full_ranking_outcome_valid'] for r in evaluations),
        'unknown_or_unreached_state_count':sum(not r['full_ranking_outcome_valid'] for r in evaluations),'states':evaluations,
        'all_registered_validation_groups_retained':True}


def verify_bound_branch_bytes(directory,result):
    directory=Path(directory)
    if not result.get('input_artifacts'):raise ValueError('Actual raw branch input byte bindings missing')
    bindings=list(result['input_artifacts'].items())+[(o['trajectory_file'],o['trajectory_sha256']) for o in result.get('outcomes',[])]
    for name,expected in bindings:
        if Path(name).name!=name or not (directory/name).is_file() or sha(directory/name)!=expected:
            raise ValueError('Actual raw branch artifact bytes changed: '+name)
    return True


def reconstruct_bound_fit_rows(directory,result,env):
    """Recompute actual targets/masks/deltas/current inputs; never trust a row."""
    directory=Path(directory);verify_bound_branch_bytes(directory,result)
    if result.get('unknown_snapshot'):
        if result['fit_rows']:raise ValueError('Unreached snapshot cannot have observed fit rows')
        return []
    current=read(directory/'CURRENT_CANDIDATES.json');context=current['context'];history=current['history'];candidates=current['candidates']
    if len(candidates)!=8 or len(result['outcomes'])!=8 or len(result['fit_rows'])!=8:raise ValueError('Registered eight-action raw binding incomplete')
    observed=[];t=float(result['snapshot_minute'])
    for c,o in zip(candidates,result['outcomes']):
        if c['candidate_id']!=o['candidate_id']:raise ValueError('Raw current candidate/outcome pairing differs')
        payload=read(directory/o['trajectory_file']);window=window_targets(payload,t,120.,env)
        if window!=o['windows'][1]:raise ValueError('Stored action target differs from original raw subsegments/mask')
        observed.append(window['targets'])
    rows=[]
    for i,(c,claimed) in enumerate(zip(candidates,result['fit_rows'])):
        expected_role='train' if result['role']=='pilot_train' else 'validation'
        expected_id=result['scenario_id']+f':t{t:g}:'+c['candidate_id']
        if claimed['scenario_id']!=result['scenario_id'] or float(claimed['snapshot_minute'])!=t or claimed['group_id']!=result['group_id'] or claimed['role']!=expected_role or claimed['row_id']!=expected_id:
            raise ValueError('Claimed fit row lineage/role/group differs from its registered snapshot')
        delta,mask=paired_delta(observed[i],observed[0]);x=candidate_numeric_features(history,context,c)
        if claimed['candidate_index']!=i or claimed['delta_y']!=delta or claimed['target_mask']!=mask or claimed['x']!=x:
            raise ValueError('Claimed fit row differs from reconstructed raw current inputs/target/delta/mask')
        if claimed['snapshot_sha256']!=sha(directory/'execution_snapshot.json') or claimed['outcome_sha256']!=result['outcomes'][i]['trajectory_sha256']:
            raise ValueError('Claimed fit row raw snapshot/trajectory bytes differ')
        rows.append(copy.deepcopy(claimed))
    return rows


def fit_from_branches(branch_directory,out):
    base=Path(branch_directory);plan,m=validate_plan(base/'registered_plan.json');report=read(base/'ACTION_BRANCH_REPORT.json')
    if len(report['results'])!=len(plan['snapshots']) or not report.get('truth_integrity_passed',report['all_D0_passed']):raise ValueError('Registered branch budget/truth integrity incomplete')
    all_rows=[];bindings=[];env=HospitalIESEnv(ROOT,execution_contract=m['execution_contract'])
    for item in report['results']:
        path=base/item['directory']/'BRANCH_RESULT.json'
        if sha(path)!=item['result_sha256']:raise ValueError('Actual branch result bytes changed')
        r=read(path)
        registered=next(s for s in plan['snapshots'] if s['scenario_id']==item['scenario_id'] and float(s['snapshot_minute'])==float(item['snapshot_minute']))
        if r['scenario_id']!=registered['scenario_id'] or float(r['snapshot_minute'])!=float(registered['snapshot_minute']) or r['role']!=registered['experiment_role'] or r['group_id']!=registered['group_id']:
            raise ValueError('Raw result lineage/role/group differs from frozen plan')
        all_rows.extend(reconstruct_bound_fit_rows(path.parent,r,env))
        bindings.append({'path':path.resolve().relative_to(ROOT).as_posix(),'sha256':sha(path),
            'raw_input_artifacts':r['input_artifacts'],'raw_outcome_artifacts':[(o['trajectory_file'],o['trajectory_sha256']) for o in r.get('outcomes',[])]})
    unknown=[r for r in all_rows if not all(r['target_mask'])]
    smoke_groups=None
    if plan['prototype_train_only']:
        if any(r['role']!='train' for r in all_rows):raise ValueError('Smoke uses training roots only')
        groups=sorted({r['group_id'] for r in plan['snapshots']});smoke_groups={'train':groups[:4],'development_smoke_validation':groups[4:]}
        if len(groups)<6:raise ValueError('Registered six-root smoke support missing')
        for r in all_rows:r['role']='train' if r['group_id'] in groups[:4] else 'validation'
    complete=[copy.deepcopy(r) for r in all_rows if all(r['target_mask'])]
    train=[r for r in complete if r['role']=='train'];val=[r for r in complete if r['role']=='validation']
    registered_validation=[r for r in plan['snapshots'] if r['group_id'] in smoke_groups['development_smoke_validation']] if smoke_groups else [r for r in plan['snapshots'] if r['experiment_role']=='pilot_validation']
    if not train or not val:
        out=Path(out)
        if out.exists():raise FileExistsError('Fit-blocked attempts require fresh output')
        out.mkdir(parents=True);blocked={'status':'blocked_no_complete_training_or_validation_support','actual_fits':0,
            'registered_snapshots':len(plan['snapshots']),'registered_validation_states':registered_validation,
            'complete_train_rows':len(train),'complete_validation_rows':len(val),'unknown_rows':unknown,
            'unreached_snapshots':report.get('unreached_snapshots',0),'smoke_group_assignment':smoke_groups,
            'source_sha256':source_hashes(),'no_reselection':True}
        write(out/'FIT_BLOCKED.json',blocked);return blocked
    # Demand-only scales use every reached training state, including states
    # with censored action labels. Future outcome completeness cannot select
    # which currently observed demand contributes to normalization.
    scales=fit_demand_scale([r for r in all_rows if r['role']=='train'],120.)
    binding={'branch_plan_signature':plan['signature'],'manifest_path':plan['manifest_path'],'manifest_sha256':plan['manifest_sha256'],
        'branch_report_sha256':sha(base/'ACTION_BRANCH_REPORT.json'),'actual_branch_files':bindings,'source_sha256':source_hashes(),
        'excluded_from_fit_unknown_rows':[r['row_id'] for r in unknown],'unknown_rows_retained_in_evaluation_denominator':True,
        'unreached_snapshots':report.get('unreached_snapshots',0),'registered_validation_states':registered_validation,
        'prototype_train_only':plan['prototype_train_only'],'smoke_group_assignment':smoke_groups,
        'prediction_information':'current problem, four numeric legal frames, requested and current projected candidate; no future labels',
        'scale_fit':'training-current demand*120min/60; max1kWh','scale_units':'kWh','continuation_policy':plan['continuation_policy'],
        'research_preference_resolution':plan.get('research_preference_resolution'),
        'bounded_candidate_horizon_minutes':plan.get('bounded_candidate_horizon_minutes')}
    result=fit_rank_models(train,val,scales,out,source_binding=binding)
    # True within-state regret, not global absolute target MAE alone.
    evaluation=[]
    for model in result['models']:
        predictor=load_rank_model(Path(out)/model['directory'])
        measured=evaluate_registered_states([r for r in all_rows if r['role']=='validation'],predictor,scales,registered_validation,
            resolution=plan.get('research_preference_resolution'))
        evaluation.append({'seed':model['seed'],**measured})
    write(Path(out)/'WITHIN_STATE_RANKING.json',{'evaluations':evaluation,'unknown_rows':len(unknown),'registered_rows':len(all_rows),
        'prototype_train_only':plan['prototype_train_only'],'representative_seed_predeclared':4101,'all_three_seeds_reported':True})
    return result


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('freeze-branches');f.add_argument('manifest');f.add_argument('snapshot_plan');f.add_argument('out');f.add_argument('--prototype-ids')
    f.add_argument('--continuation-policy',choices=('priority_milp','current_rule_projection0'),default='priority_milp')
    f.add_argument('--bounded-horizon',action='store_true');f.add_argument('--prototype-train-only',action='store_true')
    b=sub.add_parser('branches');b.add_argument('plan');b.add_argument('out')
    fit=sub.add_parser('fit');fit.add_argument('branches');fit.add_argument('out')
    a=p.parse_args()
    if a.command=='freeze-branches':result=freeze_branch_plan(a.manifest,a.snapshot_plan,a.out,prototype_ids_path=a.prototype_ids,
        continuation_policy=a.continuation_policy,bounded_horizon=a.bounded_horizon,prototype_train_only=a.prototype_train_only)
    elif a.command=='branches':result=run_branch_plan(a.plan,a.out)
    else:result=fit_from_branches(a.branches,a.out)
    print(json.dumps({k:v for k,v in result.items() if k not in ('results','models','snapshots')},ensure_ascii=False))


if __name__=='__main__':main()
