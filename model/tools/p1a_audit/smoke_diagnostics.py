"""Replay exactly the pre-existing 2x4x2 smoke conditions; keep all failures."""
import copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.state_transition import clean
from scenario_sampler import sample_scenarios
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
env=HospitalIESEnv(ROOT);results=[]
for s in sample_scenarios(env.hourly,2,6,20261012,suite_id='smoketest'):
    for policy in ('priority_milp','secure_milp'):
        env.reset(scenario=s)
        error=None
        try:
            for _ in range(6):env.step({} if policy=='priority_milp' else {'security':{'gt_loading_penalty_USD_per_kWh':.35}})
        except (RuntimeError,ValueError) as exc:
            error=getattr(exc,'simulation_diagnostic',dict(message=str(exc)))
        item=dict(scenario=s,policy=policy,steps=env.steps_done,error=error,
                  segments=copy.deepcopy(env.segment_history),events=copy.deepcopy(env.event_history))
        (out/(s['id']+'__'+policy+'.json')).write_text(json.dumps(clean(item),indent=2),encoding='utf-8')
        results.append(dict(id=s['id'],policy=policy,steps=env.steps_done,error=error))
        print(s['id'],policy,env.steps_done,error and error['message'],flush=True)
(out/'summary.json').write_text(json.dumps(clean(results),indent=2),encoding='utf-8')
sys.exit(int(any(x['error'] for x in results)))
