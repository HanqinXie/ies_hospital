"""Four-stage IES fault / load shock / cascade research prototype.

Usage: python run_faults.py --examples --steps 8
       python run_faults.py --stage 4 --scenario-file configs/four_stage_examples.json
"""
import sys,argparse,json
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from hospital_ies.pipeline import read_data
from hospital_ies.model import load_plant
from hospital_ies.resilience import build_zone_groups,simulate_scenario,validate_scenario,thermal_data


def prepare(root=ROOT):
    hourly=read_data(root)
    zones=pd.read_csv(root/'inputs/loads/critical_zones_8760.csv')
    allocated=build_zone_groups(zones,hourly)
    plant=load_plant(root/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    conf=json.loads((root/'configs/resilience_assumptions.json').read_text(encoding='utf-8'))
    return hourly,zones,allocated,plant,conf


def run_scenarios(scenarios,output_dir:Path,steps:int|None=None):
    hourly,zones,allocated,plant,conf=prepare()
    output_dir.mkdir(parents=True,exist_ok=True)
    thermal_cache=thermal_data(ROOT,zones,float(conf['mass_participation']),float(conf['effective_U_W_m2K']))
    all_trace=[];all_zones=[];all_summ=[]
    for s in scenarios:
        s=dict(s)
        if steps is not None:s['duration_steps']=steps
        validate_scenario(s)
        print(f"[{s['id']}] stage={s['stage']} {s['duration_steps']} steps",flush=True)
        trace,detail,summary=simulate_scenario(ROOT,s,plant,hourly,allocated,zones,conf,thermal_cache)
        all_trace.append(trace);all_zones.append(detail);all_summ.append(summary)
    trace_all=pd.concat(all_trace,ignore_index=True)
    trace_all.to_csv(output_dir/'dispatch_steps.csv',index=False)
    pd.concat(all_zones,ignore_index=True).to_csv(output_dir/'zone_thermal_steps.csv',index=False)
    pd.DataFrame(all_summ).to_csv(output_dir/'scenario_summary.csv',index=False)
    (output_dir/'scenario_summary.json').write_text(json.dumps(all_summ,indent=2,ensure_ascii=False),encoding='utf-8')
    (output_dir/'scenarios.json').write_text(json.dumps(scenarios,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Output:',output_dir)
    for r in all_summ:print(r['id'],'P0 failure=',r['p0_failure_any'],'secondary=',r['secondary_faults'])
    return trace_all,all_summ

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--scenario-file',type=Path,default=ROOT/'configs/four_stage_examples.json')
    parser.add_argument('--stage',type=int,choices=[1,2,3,4],default=None)
    parser.add_argument('--steps',type=int,default=None)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'outputs/v3_four_stage_demo')
    parser.add_argument('--examples',action='store_true',help='Run four included example scenarios')
    a=parser.parse_args()
    scenarios=json.loads(a.scenario_file.read_text(encoding='utf-8'))
    if a.stage is not None:scenarios=[s for s in scenarios if s['stage']==a.stage]
    if not scenarios:parser.error('No scenarios matched')
    run_scenarios(scenarios,a.output_dir,a.steps)
