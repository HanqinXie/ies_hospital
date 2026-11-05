"""Reproducible v3 fault scenario rollouts; training labels from MILP simulation.

Outputs scenario-grouped decision samples, targets, expert actions, full traces
and clinical-zone thermal proxies. No random train/test splitting of time rows.
"""
from pathlib import Path
import json,argparse,sys
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from run_faults import prepare
from hospital_ies.resilience import simulate_scenario,validate_scenario,thermal_data


from scenario_sampler import sample_scenarios  # v4.2: complete coverage + stratified design

def make_dataset(out:Path,episodes_per_stage:int,steps:int,seed:int):
    hourly,zones,allocated,plant,config=prepare()
    scenarios=sample_scenarios(hourly,episodes_per_stage,steps,seed)
    out.mkdir(parents=True,exist_ok=True)
    cache=thermal_data(ROOT,zones,float(config['mass_participation']),float(config['effective_U_W_m2K']))
    traces=[];zone_outputs=[];summaries=[]
    for i,s in enumerate(scenarios,1):
        trace,zone,summary=simulate_scenario(ROOT,s,plant,hourly,allocated,zones,config,cache)
        traces.append(trace);zone_outputs.append(zone);summaries.append(summary)
        print(f'[{i:03d}/{len(scenarios)}] {s["id"]}: {summary["secondary_fault_count"]} secondary trips, P0 fail={summary["p0_failure_any"]}',flush=True)
    df=pd.concat(traces,ignore_index=True)
    df.to_csv(out/'expert_trajectories.csv',index=False)
    pd.concat(zone_outputs,ignore_index=True).to_csv(out/'zone_thermal_trajectories.csv',index=False)
    pd.DataFrame(summaries).to_csv(out/'episode_summary.csv',index=False)
    (out/'scenario_definitions.json').write_text(json.dumps(scenarios,indent=2,ensure_ascii=False),encoding='utf-8')
    # No decisions are concealed from the full expert data; learner uses a strict
    # allowlist of pre-decision features in train_risk_baseline.py.
    metadata={'episodes':len(scenarios),'step_count':len(df),'seed':seed,
        'step_minutes':15,'generated_with':'physics-constrained stagewise MILP + uncalibrated GT protective transient proxy',
        'no_train_test_leakage':'Group split by scenario_id only; no random row split',
        'notes':['Not real clinical data','No validated GT protection curves','No full network topology',
                 'Expert outputs are post-decision, and MUST NOT be used as features for pre-decision risk prediction']}
    (out/'dataset_meta.json').write_text(json.dumps(metadata,indent=2,ensure_ascii=False),encoding='utf-8')
    return metadata

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--episodes-per-stage',type=int,default=2)
    p.add_argument('--steps',type=int,default=6);p.add_argument('--seed',type=int,default=20261011)
    p.add_argument('--output-dir',type=Path,default=ROOT/'outputs/v3_training_preview')
    a=p.parse_args()
    if a.episodes_per_stage<1 or a.steps<2 or a.steps>48:p.error('episodes >=1 and steps 2..48')
    print(json.dumps(make_dataset(a.output_dir,a.episodes_per_stage,a.steps,a.seed),indent=2))
