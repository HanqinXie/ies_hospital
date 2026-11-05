"""Independent fixed-incident P1B replay. Never resumes; retains every failure."""
import sys,json,copy,hashlib,csv
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.state_transition import clean
from scenario_sampler import sample_scenarios

def write(path,obj):path.write_text(json.dumps(clean(obj),ensure_ascii=False,indent=2),encoding='utf-8')

def run(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    contract=json.loads((ROOT/'configs/p1b_execution_research.json').read_text())
    env=HospitalIESEnv(ROOT,execution_contract=contract)
    scenarios=sample_scenarios(env.hourly,2,6,20261012,suite_id='smoketest')
    scenarios.append(dict(id='testL4',stage=4,start_hour=4300,duration_steps=2,step_minutes=15,
        shock={'e':.85,'c':.55},enable_propagation=True,pre_event_warmup_steps=8,
        grid_limit_kW=250,response_delay_steps=2,automatic_fast_shedding=True))
    write(out/'scenarios.json',scenarios);write(out/'execution_contract.json',contract)
    summaries=[];sources=[]
    for s in scenarios:
        for policy in ('priority_milp','secure_milp'):
            print('START',s['id'],policy,flush=True)
            error=None;env.reset(scenario=copy.deepcopy(s),seed=20261012)
            initial=clean(copy.deepcopy(env.state));initial=clean(vars(env.state))
            original_solve=env.core._solve;calls=[0]
            def bounded(*args,**kwargs):
                calls[0]+=1
                if calls[0]>1500:raise RuntimeError(f'Audit guard: >1500 solves at time {env.core.time}')
                return original_solve(*args,**kwargs)
            env.core._solve=bounded
            try:
                for _ in range(s['duration_steps']):
                    env.step({} if policy=='priority_milp' else {'security':{'gt_loading_penalty_USD_per_kWh':.35}})
            except Exception as exc:error=dict(type=type(exc).__name__,message=str(exc))
            payload=dict(scenario=s,policy=policy,seed=20261012,initial_state=initial,
                events=env.event_history,segments=env.segment_history,solver_errors=env.solver_errors,
                solver_calls=env.core.solver_calls,error=error,completed_steps=env.steps_done)
            write(out/(s['id']+'__'+policy+'.json'),payload)
            for seg in env.segment_history:
                a=seg['action'];loads=seg['connected_loads_kW'];disc=seg['disconnected_loads_kW']
                sources.append(dict(scenario=s['id'],policy=policy,start=seg['start_minute'],end=seg['end_minute'],
                    command_id=seg['command_id'],GT1=a['gt_electric_kW'][0],GT2=a['gt_electric_kW'][1],GT3=a['gt_electric_kW'][2],
                    grid=a['grid_import_kW'],diesel=a['diesel_kW'],connected_e=sum(x[0] for x in loads),
                    disconnected_e=sum(x[0] for x in disc),disconnected_c=sum(x[2] for x in disc),
                    auxiliary=env.core.aux(a),surplus_before_unserved=sum(a['gt_electric_kW'])+a['grid_import_kW']+a['diesel_kW']-sum(x[0] for x in loads)-env.core.aux(a),
                    balance_max=max(map(abs,seg['energy_balance_residuals'])),cost=seg['cost_USD_step']))
            row=dict(id=s['id'],policy=policy,completed=env.steps_done,segments=len(env.segment_history),error=error,
                events={k:sum(e['kind']==k for e in env.event_history) for k in ('no_export_interlock','minimum_output_interlock','automatic_load_shed','protection_trip')})
            summaries.append(row);print(json.dumps(row),flush=True)
    write(out/'summary.json',summaries)
    with (out/'source_and_subintervals.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(sources[0]));w.writeheader();w.writerows(sources)
    return int(any(x['error'] for x in summaries))

if __name__=='__main__':raise SystemExit(run(sys.argv[1]))
