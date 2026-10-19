"""Six recipes/eight seed variants under one registered current physical core."""
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
from .policies import RECIPES,make_policy
from .models import sha,write,SEEDS,load_rank_model
from .branches import source_hashes,window_targets
from .episode import run_policy_episode
from .run import read,verify
from .ranking import RESEARCH_RESOLUTION


def policy_variants():
    return [{'recipe_id':r,'seed':s} for r in RECIPES for s in (SEEDS if r=='execution_aware_RF_rank_projection0' else (None,))]


def register_protocol(manifest_path,out,*,scenario_ids=None,prototype=False):
    mp=Path(manifest_path);m=read(mp);verify(m);byid={s['id']:s for s in m['scenarios']}
    if scenario_ids is None:
        scenario_ids=sorted(s['id'] for s in m['scenarios'] if s['experiment_role']=='development_boundary')
    if len(scenario_ids)>200 or not scenario_ids or len(set(scenario_ids))!=len(scenario_ids) or any(sid not in byid for sid in scenario_ids):raise ValueError('Registered closedloop scene budget/support invalid')
    expected_role='pilot_train' if prototype else 'development_boundary'
    if any(byid[sid]['experiment_role']!=expected_role for sid in scenario_ids):raise ValueError('Prototype/new-development role mixing')
    out=Path(out)
    if out.exists():raise FileExistsError('Evaluation distribution/list cannot be overwritten')
    p={'schema':'control_improvement.closed_protocol.v1','manifest_path':mp.resolve().relative_to(ROOT).as_posix(),
        'manifest_sha256':sha(mp),'manifest_signature':m['signature'],'source_sha256':source_hashes(),
        'scenario_ids':list(scenario_ids),'policy_variants':policy_variants(),'recipe_count':len(RECIPES),
        'maximum_trajectory_attempts':len(scenario_ids)*len(policy_variants()),'prototype_train_only':bool(prototype),
        'primary_score_minutes':[60.,420.],'full_observation_minutes':[0.,600.],'outcomes_used_for_list_selection':False,
        'research_preference':'P0flag,duration,P0/P1/P2/P3sum-carrier-normalized-ENS,fee; individual outcomes separate',
        'research_preference_resolution':RESEARCH_RESOLUTION,
        'model_artifacts_at_registration':None,'model_binding_before_actual_evaluation_required':True,
        'seed_repeats_are_not_independent_backgrounds':True}
    p['signature']=digest(p);write(out,p);return p


def _valid_protocol(path):
    p=read(path);body=copy.deepcopy(p);sig=body.pop('signature')
    if digest(body)!=sig or p['source_sha256']!=source_hashes():raise ValueError('Registered closed protocol/source changed')
    mp=ROOT/p['manifest_path']
    if sha(mp)!=p['manifest_sha256']:raise ValueError('Registered physical data version bytes changed')
    m=read(mp);verify(m)
    if m['signature']!=p['manifest_signature']:raise ValueError('Registered physical data signature changed')
    return p,m


def bind_models(protocol_path,out,*,bc_directory,rank_directory):
    p,m=_valid_protocol(protocol_path);bc=Path(bc_directory).resolve();rank=Path(rank_directory).resolve()
    # Load validates all weights/preprocessing. Never reselect using new eval.
    make_policy('frozen_BC_projection035',bc_directory=bc)
    rank_bind=[]
    for seed in SEEDS:
        model=load_rank_model(rank/f'RF_seed{seed}')
        if bool(model.metadata['source_binding']['prototype_train_only'])!=bool(p['prototype_train_only']):raise ValueError('Smoke/formal fitted model scope differs from registered evaluation')
        if model.metadata['source_binding']['manifest_sha256']!=p['manifest_sha256']:raise ValueError('Rank trained on another unbound physical data version')
        rank_bind.append({'seed':seed,'metadata_sha256':sha(rank/f'RF_seed{seed}'/'metadata.json')})
    out=Path(out)
    if out.exists():raise FileExistsError('Frozen model bindings cannot be overwritten')
    plan={'schema':'control_improvement.closed_plan.v1','protocol_path':Path(protocol_path).resolve().relative_to(ROOT).as_posix(),
        'protocol_sha256':sha(protocol_path),'protocol_signature':p['signature'],
        'bc_directory':bc.relative_to(ROOT).as_posix(),'bc_metadata_sha256':sha(bc/'model_metadata.json'),
        'rank_directory':rank.relative_to(ROOT).as_posix(),'rank_bindings':rank_bind,
        'actual_new_rank_fits':3,'frozen_prior_BC_is_not_new_fit':True,
        'representative_rank_seed_predeclared':4101,'all_three_rank_seeds_evaluated':True,
        'new_run_BC_torch_intraop_threads':2,'source_sha256':source_hashes()}
    plan['signature']=digest(plan);write(out,plan);return plan


def run_closed_plan(plan_path,out):
    plan=read(plan_path);body=copy.deepcopy(plan);signature=body.pop('signature')
    if digest(body)!=signature or plan['source_sha256']!=source_hashes():raise ValueError('Closedloop model plan/source changed')
    pp=ROOT/plan['protocol_path']
    if sha(pp)!=plan['protocol_sha256']:raise ValueError('Registered evaluation protocol bytes changed')
    protocol,m=_valid_protocol(pp);bc=ROOT/plan['bc_directory'];rank=ROOT/plan['rank_directory']
    if sha(bc/'model_metadata.json')!=plan['bc_metadata_sha256']:raise ValueError('Frozen prior BC changed')
    for b in plan['rank_bindings']:
        if sha(rank/f"RF_seed{b['seed']}"/'metadata.json')!=b['metadata_sha256']:raise ValueError('Frozen rank artifact changed')
    out=Path(out)
    if out.exists():raise FileExistsError('Retain every previous closedloop attempt; fresh output required')
    out.mkdir(parents=True);write(out/'registered_plan.json',plan);write(out/'registered_protocol.json',protocol)
    specs={s['id']:s for s in m['scenarios']};env=HospitalIESEnv(ROOT,execution_contract=m['execution_contract']);scores=[]
    policies=[make_policy(v['recipe_id'],bc_directory=bc,rank_directory=rank,seed=v['seed'] or 4101,
        formal=not protocol['prototype_train_only']) for v in protocol['policy_variants']]
    for sid in protocol['scenario_ids']:
        s=specs[sid];initial=[]
        for variant,policy in zip(protocol['policy_variants'],policies):
            journal(out/'attempts.jsonl',event='started',scenario_id=sid,policy_id=policy.policy_id,recipe_id=variant['recipe_id'],training_seed=variant['seed'])
            start=time.perf_counter();payload=run_policy_episode(env,s,policy,m['options']['seed'],m,horizons=(15,30,60,120));wall=time.perf_counter()-start
            path=out/(sid+'__'+policy.policy_id+'.json');save(path,payload);initial.append(payload.get('initial_state'))
            native=sum(len(call.get('solver_backend_layers',[])) for call in payload['solver_calls'])
            query_native=sum(len(call.get('solver_backend_layers',[])) for d in payload['policy_diagnostics'] for q in d.get('current_planning_queries',[]) for call in q.get('solver_calls',[]))
            scores.append({'scenario_id':sid,'policy_id':policy.policy_id,'recipe_id':variant['recipe_id'],'training_seed':variant['seed'],
                'root_family_id':s['root_family_id'],'group_id':m['splits'][sid]['group_id'],'status':payload['summary']['status'],
                'payload_path':path.name,'payload_sha256':sha(path),'end_to_end_seconds':wall,
                'physical_solver_seconds':sum(c['elapsed_seconds'] for c in payload['solver_calls']),
                'current_projection_query_seconds':sum(q['elapsed_seconds'] for d in payload['policy_diagnostics'] for q in d.get('current_planning_queries',[])),
                'policy_feature_candidate_projection_rank_ms':sum(d['inference_ms'] for d in payload['policy_diagnostics']),
                'physical_objective_layers_attempted':native,'current_query_objective_layers_attempted':query_native,
                'strategy_fallback_steps':payload['summary']['strategy_fallback_steps'],
                'primary_score':window_targets(payload,60.,360.,env),'full_observation':window_targets(payload,0.,600.,env)})
            journal(out/'attempts.jsonl',event='finished',scenario_id=sid,policy_id=policy.policy_id,status=payload['summary']['status'],
                end_to_end_seconds=wall,payload_sha256=sha(path))
        if any(x!=initial[0] for x in initial[1:]):raise AssertionError('Closedloop strategies did not share the same original initial state')
    _valid_protocol(pp)
    result={'plan_signature':plan['signature'],'registered_specs':len(protocol['scenario_ids']),'trajectory_attempts':len(scores),
        'independent_background_roots':len({specs[sid]['root_family_id'] for sid in protocol['scenario_ids']}),
        'six_recipes':6,'seed_variants':8,'prototype_train_only':protocol['prototype_train_only'],'scores':scores,
        'scope':'Development simulation, not final blind or hospital hardware safety validation'}
    write(out/'CLOSED_LOOP_REPORT.json',result);return result


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    r=sub.add_parser('register');r.add_argument('manifest');r.add_argument('out');r.add_argument('--prototype-ids')
    b=sub.add_parser('bind');b.add_argument('protocol');b.add_argument('out');b.add_argument('bc');b.add_argument('rank')
    run=sub.add_parser('run');run.add_argument('plan');run.add_argument('out')
    a=p.parse_args()
    if a.command=='register':result=register_protocol(a.manifest,a.out,scenario_ids=read(a.prototype_ids)['scenario_ids'][:4] if a.prototype_ids else None,prototype=bool(a.prototype_ids))
    elif a.command=='bind':result=bind_models(a.protocol,a.out,bc_directory=a.bc,rank_directory=a.rank)
    else:result=run_closed_plan(a.plan,a.out)
    print(json.dumps({k:v for k,v in result.items() if k not in ('scores','scenario_ids','policy_variants','source_sha256')},ensure_ascii=False))


if __name__=='__main__':main()
