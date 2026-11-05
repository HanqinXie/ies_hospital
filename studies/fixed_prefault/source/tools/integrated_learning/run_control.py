"""Register finite lists, then run actual restored branches/learned episodes."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import numpy as np
from tools.integrated_experiment.runner import verify_manifest,ROOT,RecordedInitialEnv
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.dataset_manifest import digest
from .models import sha,write,load_predictor
from .policies import BaselinePolicy,PolicyController
from .episode import run_episode
from .truth import run_snapshot_branches,journal,save,window_outcome


def learner_source():
    paths=sorted(Path(__file__).parent.glob('*.py'))+[ROOT/'tools/integrated_experiment/energy_metrics.py']
    return {p.relative_to(ROOT).as_posix():sha(p) for p in paths}


def select_training_snapshots(manifest,n=20):
    if not 1<=n<=300:raise ValueError('Snapshot budget must be 1..300')
    roots={}
    for s in manifest['scenarios']:
        if s['experiment_role']=='pilot_train':roots.setdefault(s['root_family_id'],[]).append(s)
    representatives=[]
    for root,specs in roots.items():
        selected=min(specs,key=lambda s:(abs(float(s.get('pressure_coordinate',.5))-.5),s['id']))
        representatives.append(selected)
    pools={}
    for s in representatives:pools.setdefault(s.get('condition_id',s['event_class']),[]).append(s)
    for pool in pools.values():pool.sort(key=lambda s:s['id'])
    chosen=[]
    while len(chosen)<n and any(pools.values()):
        for key in sorted(pools):
            if pools[key]:chosen.append(pools[key].pop(0))
            if len(chosen)==n:break
    return [{'scenario_id':s['id'],'snapshot_step':6 if i%2==0 else 11,
             'snapshot_minute':90. if i%2==0 else 165.,'condition_id':s.get('condition_id'),
             'root_family_id':s['root_family_id'],'role':'pilot_train'} for i,s in enumerate(chosen)]


def freeze_plan(manifest_path,out,*,count=20,model_directory=None,closed_loop_ids=()):
    mp=Path(manifest_path);m=json.loads(mp.read_text(encoding='utf-8'));verify_manifest(m)
    out=Path(out)
    if out.exists():raise FileExistsError('Never overwrite a frozen control plan')
    if len(closed_loop_ids)>200 or len(set(closed_loop_ids))!=len(closed_loop_ids):raise ValueError('Closed-loop family budget/uniqueness violated')
    specs={s['id']:s for s in m['scenarios']}
    if any(sid not in specs for sid in closed_loop_ids):raise ValueError('Unregistered closed-loop accident')
    model=None
    if model_directory:
        directory=Path(model_directory).resolve();loaded=load_predictor(directory)
        model={'directory':directory.relative_to(ROOT).as_posix(),
               'metadata_sha256':sha(directory/'model_metadata.json'),'teacher_source':'secure_milp',
               'fixed_projection_weight_USD_per_kWh':.35,'fixed_fallback':'secure_milp'}
        report=json.loads((directory.parent/'BC_REPORT.json').read_text(encoding='utf-8'))
        if report['selected_model_directory']!=directory.name or report['selected_model_metadata_sha256']!=model['metadata_sha256']:
            raise ValueError('Closed-loop candidate differs from frozen development-validation selection')
        model['selection_report']={'path':(directory.parent/'BC_REPORT.json').relative_to(ROOT).as_posix(),
                                   'sha256':sha(directory.parent/'BC_REPORT.json')}
    if closed_loop_ids and model is None:raise ValueError('Actual learned model must be frozen before learned closed-loop plan')
    plan={'schema':'integrated.control_plan.v1','manifest_path':mp.resolve().relative_to(ROOT).as_posix(),
        'manifest_sha256':sha(mp),'manifest_signature':m['signature'],'learner_source_sha256':learner_source(),
        'snapshots':select_training_snapshots(m,count),'closed_loop_scenario_ids':list(closed_loop_ids),
        'model':model,'model_seen_future':False,'numeric_absolute_tolerance':1e-7,'time_absolute_tolerance':1e-10,
        'snapshot_selection':'One median-pressure representative per training root, condition-round-robin, clock90/165 alternating; no outcomes',
        'maximum_branch_attempts':count*3,'maximum_capture_attempts':count,'maximum_closed_loop_attempts':len(closed_loop_ids)*3}
    plan['signature']=digest(plan);write(out,plan);return plan


def validate_plan(path):
    path=Path(path);p=json.loads(path.read_text(encoding='utf-8'));body=copy.deepcopy(p);signature=body.pop('signature')
    if digest(body)!=signature:raise ValueError('Control plan changed after registration')
    if p['learner_source_sha256']!=learner_source():raise ValueError('Learning/control source changed after plan registration')
    mp=ROOT/p['manifest_path']
    if sha(mp)!=p['manifest_sha256']:raise ValueError('Actual parent manifest changed')
    m=json.loads(mp.read_text(encoding='utf-8'));verify_manifest(m)
    if m['signature']!=p['manifest_signature']:raise ValueError('Wrong control parent manifest')
    if p['model']:
        model=p['model'];directory=ROOT/model['directory']
        if sha(directory/'model_metadata.json')!=model['metadata_sha256']:raise ValueError('Selected model metadata changed')
        ref=model['selection_report']
        if sha(ROOT/ref['path'])!=ref['sha256']:raise ValueError('Selection report changed')
        load_predictor(directory)
    return p,m


def make_env(manifest,specs):
    if manifest['integrated_cohort']=='original_replay':
        from tools.p2c_experiment.replay import original_payload,OLD
        old=json.loads((OLD/'global_manifest.json').read_text(encoding='utf-8'));initial={}
        for s in specs:
            pair=[original_payload(old,s['id'],p) for p in ('priority_milp','secure_milp')]
            if pair[0]['initial_state']!=pair[1]['initial_state']:raise ValueError('Original initial states differ')
            initial[s['id']]=pair[0]['initial_state']
        return RecordedInitialEnv(ROOT,execution_contract=manifest['execution_contract'],recorded_initial_by_id=initial)
    return HospitalIESEnv(ROOT,execution_contract=manifest['execution_contract'])


def run_branches(plan_path,out):
    plan,m=validate_plan(plan_path);out=Path(out)
    if out.exists():raise FileExistsError('Fresh branch run directory required')
    out.mkdir(parents=True);save(out/'registered_plan.json',plan);specs={s['id']:s for s in m['scenarios']}
    selected=[specs[r['scenario_id']] for r in plan['snapshots']];env=make_env(m,selected);results=[]
    for i,row in enumerate(plan['snapshots']):
        # An actual D0 mismatch stops subsequent truth generation. All earlier
        # attempt directories and original failure output remain untouched.
        from tools.p2b_experiment.runner import load_payload,payload_path
        sid=row['scenario_id'];base=(ROOT/plan['manifest_path']).parent
        shard=next(sh for sh in m['p2b_shards'] if sid in sh['scenario_ids'])
        folder=base/'shards'/shard['id'];path=payload_path(folder,sid,'secure_milp')
        baseline=load_payload(folder,sid,'secure_milp',m['signature'])
        if baseline['scenario']!=specs[sid] or baseline.get('seed')!=m['options']['seed']:
            raise ValueError('Registered baseline scenario or seed differs')
        save(out/f'baseline_reference_{i:03d}.json',{'scenario_id':sid,'policy_id':'secure_milp',
            'checkpoint_path':path.relative_to(ROOT).as_posix(),'checkpoint_sha256':sha(path),
            'manifest_signature':m['signature'],'comparison_label_horizons':m['options']['label_horizons']})
        result=run_snapshot_branches(env,specs[sid],m['options']['seed'],m,
            out/f'snapshot_{i:03d}',snapshot_step=row['snapshot_step'],baseline_payload=baseline)
        results.append(result)
        if not result['passed']:break
    report={'plan_signature':plan['signature'],'snapshots_attempted':len(results),'registered_snapshots':len(plan['snapshots']),
        'branches_attempted':sum(r['branches_attempted'] for r in results),'capture_trajectories_attempted':len(results),
        'all_attempted_D0_passed':all(r['passed'] for r in results),'results':results}
    save(out/'ACTION_BRANCH_REPORT.json',report);validate_plan(plan_path);return report


def run_closed_loop(plan_path,out):
    plan,m=validate_plan(plan_path);out=Path(out)
    if out.exists():raise FileExistsError('Fresh closed-loop directory required')
    if not plan['model'] or not plan['closed_loop_scenario_ids']:raise ValueError('Model and exact accident list required')
    out.mkdir(parents=True);save(out/'registered_plan.json',plan);specs={s['id']:s for s in m['scenarios']}
    selected=[specs[sid] for sid in plan['closed_loop_scenario_ids']];env=make_env(m,selected)
    model=load_predictor(ROOT/plan['model']['directory']);scores=[]
    for s in selected:
        initial=[]
        for policy in (BaselinePolicy('priority_milp'),BaselinePolicy('secure_milp'),
                       PolicyController(model,model_id='BC_selected',fallback_policy='secure_milp',projection_policy='secure_milp')):
            journal(out/'attempts.jsonl',event='started',scenario_id=s['id'],policy_id=policy.policy_id)
            payload=run_episode(env,s,policy,m['options']['seed'],m,horizons=(15,30,60,120))
            save(out/(s['id']+'__'+policy.policy_id+'.json'),payload)
            initial.append(payload.get('initial_state'))
            score=window_outcome(payload,60.,360.,env)
            scores.append(dict(scenario_id=s['id'],policy_id=policy.policy_id,
                role=s['experiment_role'],root_family_id=s['root_family_id'],group_id=m['splits'][s['id']]['group_id'],
                strategy_fallback_steps=payload['summary']['strategy_fallback_steps'],
                projection_changed_steps=payload['summary']['projection_changed_steps'],
                solver_seconds=sum(c.get('elapsed_seconds',0.) for c in payload['solver_calls']),**score))
            journal(out/'attempts.jsonl',event='finished',scenario_id=s['id'],policy_id=policy.policy_id,status=payload['summary']['status'])
        if any(state!=initial[0] for state in initial[1:]):raise AssertionError('Closed-loop paired initial state mismatch')
    report={'plan_signature':plan['signature'],'families_attempted':len(selected),'trajectory_attempts':len(scores),
            'scores':scores,'scope':'Frozen development/development historical closed loop, not final blind or hardware safety proof'}
    save(out/'CLOSED_LOOP_REPORT.json',report);validate_plan(plan_path);return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    f=sub.add_parser('freeze');f.add_argument('manifest');f.add_argument('out');f.add_argument('--count',type=int,default=20)
    f.add_argument('--model');f.add_argument('--closed-loop-ids',nargs='*',default=[])
    for name in ('branches','closed-loop'):
        p=sub.add_parser(name);p.add_argument('plan');p.add_argument('out')
    args=parser.parse_args()
    if args.command=='freeze':result=freeze_plan(args.manifest,args.out,count=args.count,model_directory=args.model,closed_loop_ids=args.closed_loop_ids)
    elif args.command=='branches':result=run_branches(args.plan,args.out)
    else:result=run_closed_loop(args.plan,args.out)
    print(json.dumps(result,ensure_ascii=False,indent=2))
