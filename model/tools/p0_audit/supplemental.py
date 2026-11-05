"""P0 source/trace evidence beyond the three supplied read-only diagnostics."""
from __future__ import annotations
import argparse
import ast
import copy
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

p=argparse.ArgumentParser()
p.add_argument('--root',type=Path,default=Path.cwd())
p.add_argument('--output-dir',type=Path,required=True)
a=p.parse_args();root=a.root.resolve();out=a.output_dir.resolve()
out.mkdir(parents=True,exist_ok=True)
sys.path[:0]=[str(root),str(root/'src')]
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.training_schema import build_features,FEATURES,ACTIONS
from hospital_ies.resilience import _solve_milp,empty_faults
from hospital_ies.model import gt_level_properties
from train_nonmpc_baselines import _groups_split
from generate_nonmpc_training import _policy_action
from tools.p0_audit.cases import proxy_case,delay_case,derate_case,env_timing_case,legacy_timing_case,resume_case

def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(tuple,list)):return [clean(v) for v in x]
    if isinstance(x,np.ndarray):return clean(x.tolist())
    if isinstance(x,np.generic):return x.item()
    return x

env=HospitalIESEnv(root)
r={
    'balanced_persistent_load':proxy_case(env,balanced=True),
    'sustained_uncontrolled':proxy_case(env,sustained=True),
    'sustained_controlled':proxy_case(env,sustained=True,controlled=True),
    'delay_zero':delay_case(env,0),'delay_three':delay_case(env,3),
    'derate_01':derate_case(env,.1),
    'full_trip_control':derate_case(env,0.,200.),
    'healthy_ramp_control':derate_case(env,1.,200.),
    'env_timing_15':env_timing_case(env,15),
    'env_timing_30':env_timing_case(env,30),
    'legacy_timing_15':legacy_timing_case(env),
}
scenarios=json.loads((root/'outputs/v42_coverage_validation/scenario_definitions.json').read_text(encoding='utf-8'))
traces=[];defs=[];events=[];replay=[]
for family in ('coverage_L4_0009_h2723','coverage_L3_0018_h7767'):
    s=next(x for x in scenarios if x['id']==family)
    defs.append(s)
    for policy in ('priority_milp','secure_milp'):
        ob,_=env.reset(scenario=s,seed=20261012)
        initial=clean(ob)
        for _ in range(s['duration_steps']):
            ob,_,_,_,info=env.step(_policy_action(policy,ob,.35))
        frame=env.traces()[0].copy();frame['policy']=policy
        traces.append(frame)
        for ev in env.trip_events:events.append(dict(family=family,policy=policy,**ev))
        ens={f'{g}_{k}':float(frame[f'{g}_{k}_unserved_kW'].sum()*.25)
             for g in ('P0','P1','P2','P3') for k in ('e','h','c')}
        replay.append(dict(family=family,policy=policy,initial=initial,ENS_kWh=ens,
            cost_USD=float(frame.cost_USD_step.sum()),trips=len(env.trip_events),
            P0_failure=bool(frame.p0_failed.any())))
r['fresh_paired_replays']=replay
pd.concat(traces,ignore_index=True).to_csv(out/'step_trace.csv',index=False)
pd.DataFrame(events).to_csv(out/'event_trace.csv',index=False)
defs.extend(r[k]['scenario'] for k in ('delay_zero','delay_three','env_timing_15','env_timing_30','legacy_timing_15'))
(out/'scenario_definitions.json').write_text(json.dumps(defs,indent=2),encoding='utf-8')

stored=pd.read_csv(root/'outputs/v42_coverage_validation/closed_loop_expert_steps.csv')
summary=pd.read_csv(root/'outputs/v42_coverage_validation/closed_loop_episode_summary.csv')
r['stored_output_recalculation']={policy:dict(
    P0_failure_families=int(group.p0_failed_any.sum()),secondary_trips=int(group.secondary_fault_count.sum()),
    fallback_steps=int(group.projection_fallback_steps.sum()),economic_skips=int(group.economic_tiebreak_skipped_steps.sum()))
    for policy,group in summary.groupby('policy')}
secure=stored.loc[stored.policy.eq('secure_milp')]
valid=secure.loc[secure.future_horizon_valid]
main=valid.loc[valid.sampling_cohort.ne('stress_ood')]
ood=valid.loc[valid.sampling_cohort.eq('stress_ood')]
train,test=_groups_split(main,'future_P0_failure_H',20261012)
splits={name:sorted(frame.episode_family.unique()) for name,frame in
        (('train',main.loc[train]),('test',main.loc[test]),('stress_ood',ood))}
r['split_families']=splits
r['split_counts']={name:len(ids) for name,ids in splits.items()}
r['split_intersections']={f'{x}_{y}':sorted(set(splits[x])&set(splits[y]))
                         for x,y in (('train','test'),('train','stress_ood'),('test','stress_ood'))}
# Feature encoding contrast with every other known feature held equal.
pair=pd.concat([stored.iloc[[0]],stored.iloc[[0]]],ignore_index=True)
pair['active_faults']='GT_1';pair['GT_1_availability']=[.5,0.]
feat=build_features(pair)
r['feature_collision']={'availability_inputs':[.5,0.],
    'max_feature_difference':float(np.abs(feat.iloc[0]-feat.iloc[1]).max()),
    'fault_GT_1':feat.fault_GT_1.tolist(),'features':FEATURES,
    'scope':'encoding diagnostic with all other observed state equal; not proof all real trajectories are indistinguishable'}

# Inspect actual AST assignment, not a handwritten copy of the hashed files.
tree=ast.parse((root/'generate_nonmpc_training.py').read_text(encoding='utf-8'))
node=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign)
          and any(isinstance(t,ast.Name) and t.id=='code_hashes' for t in n.targets))
hashed=ast.literal_eval(node.value.generators[0].iter)
dependencies=sorted([str(x.relative_to(root)).replace('\\','/') for directory in ('src','configs','inputs/loads','inputs/energyplus')
    for x in (root/directory).rglob('*') if x.is_file() and '__pycache__' not in x.parts
    and x.suffix.lower() in ('.py','.json','.csv','.idf')])
r['resume_manifest_source']={'hashed_paths':hashed,'unhashed_dependencies':sorted(set(dependencies)-set(hashed))}
r['resume_dependency_replay']=resume_case(root,out/'.resume_copy')

tree=ast.parse((root/'src/hospital_ies/resilience.py').read_text(encoding='utf-8'))
accepted=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Assign)
    and any(isinstance(t,ast.Name) and t.id=='accepted' for t in n.targets))
keys=[ast.literal_eval(k) for k in accepted.keys]
row=next(env.hourly.iloc[[5455]].itertuples(index=False))
env.reset(scenario=dict(id='action_interface_probe',stage=2,start_hour=5456,duration_steps=2,
    step_minutes=15,enable_propagation=False,shock={}))
loads=env._observation()['loads_kW'];rejections={}
for target in ('electric_chiller_cooling_kW_1','electric_chiller_cooling_kW_2','electric_chiller_cooling_kW_3','boiler_steam_lbph'):
    try:
        _solve_milp(row,env.plant,loads,empty_faults(),env.state,.25,850.,False,900.,
                    config=env.config,tracking_targets={target:100.})
        rejections[target]='accepted'
    except ValueError as exc:rejections[target]=str(exc)
r['action_interface']={'regression_outputs':ACTIONS,'projection_targets':keys,'rejections':rejections,
    'source_review':'train_nonmpc_baselines.train fits/predicts/scores; no env.step learned-policy loop'}
rate=float(gt_level_properties(env.plant,float(row.outdoor_temperature_C))[0][-1])
r['rating_basis']={'hour':5456,'nominal_GT_kW':env.plant.gt_rated_kW,'ambient_GT_kW':rate,
    'availability':.1,'operating_cap_kW':rate*.1,
    'physical_judgement':'protection basis requires assumption; do not automatically multiply nameplate by availability'}
(out/'supplemental.json').write_text(json.dumps(clean(r),indent=2,ensure_ascii=False),encoding='utf-8')
(out/'solver_errors.json').write_text(json.dumps([r['derate_01']],indent=2),encoding='utf-8')
print(json.dumps(clean(r),indent=2,ensure_ascii=False))
