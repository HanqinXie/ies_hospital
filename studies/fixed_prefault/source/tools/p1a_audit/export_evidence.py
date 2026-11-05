"""Write P1A minimal cases and fresh event/energy evidence, never training data."""
import copy,json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.state_transition import clean
from tools.p0_audit.cases import proxy_case,delay_case,derate_case,env_timing_case,legacy_timing_case,timing_scenario
from test_v43_p1a import absolute_case,rollout

def main(out):
    out.mkdir(parents=True,exist_ok=True)
    def save(name,obj):
        (out/(name+'.json')).write_text(json.dumps(clean(obj),indent=2,ensure_ascii=False),encoding='utf-8')
    env=HospitalIESEnv(ROOT)
    core=dict(R01=proxy_case(env,balanced=True),R03_uncontrolled=proxy_case(env,controlled=False,sustained=True),
        R03_controlled=proxy_case(env,controlled=True,sustained=True),R04=derate_case(env,.1))
    core['R02_immediate']=delay_case(env,0);core['R02_delayed']=delay_case(env,3)
    core['R05_15']=env_timing_case(env,15);core['R05_30']=env_timing_case(env,30);core['R06']=legacy_timing_case(env)
    save('r01_r06',core)
    results=[]
    for dt in (30,15,7.5):
        s=absolute_case(dt)
        trace,segments,trips=rollout(env,s,initial_targets=True)
        prefix='absolute_'+str(dt)
        save(prefix+'_scenario',s);save(prefix+'_events',env.event_history)
        save(prefix+'_segments',segments);save(prefix+'_solver_calls',env.core.solver_calls)
        trace.to_csv(out/(prefix+'_steps.csv'),index=False,encoding='utf-8-sig')
        initial=next(e['command']['request'] for e in env.event_history if e['kind']=='command_generated')
        result=dict(dt_minutes=dt,initial_request=initial,trips=trips,
            ENS_kWh=np.sum([np.asarray(x['unserved_kW'])*(x['end_minute']-x['start_minute'])/60 for x in segments],axis=0),
            source_kWh=np.sum([np.r_[x['action']['gt_electric_kW'],x['action']['grid_import_kW'],x['action']['diesel_kW']]
                *(x['end_minute']-x['start_minute'])/60 for x in segments],axis=0),
            temperature_delta_C=copy.deepcopy(env.state.thermal_delta),
            maximum_balance_kW=max(max(map(abs,x['energy_balance_residuals'])) for x in segments),
            maximum_steam_residual_lbph=max(abs(x['steam_balance_residual_lbph']) for x in segments))
        results.append(clean(result))
    ref=results[0]
    compare=[dict(dt_minutes=r['dt_minutes'],
        ENS_max_delta_kWh=float(np.max(np.abs(np.asarray(r['ENS_kWh'])-ref['ENS_kWh']))),
        source_max_delta_kWh=float(np.max(np.abs(np.asarray(r['source_kWh'])-ref['source_kWh']))),
        temperature_max_delta_C=max(abs(v-ref['temperature_delta_C'][z]) for z,v in r['temperature_delta_C'].items()),
        trip_max_delta_minute=max(abs(a['trip_time_minute']-b['trip_time_minute']) for a,b in zip(r['trips'],ref['trips']))) for r in results]
    save('step_comparison',dict(results=results,comparison=compare))
    # Exactly the legacy L4 input, with its old implicit automatic capability
    # made explicit; retain error and all partial events instead of hiding it.
    s=dict(id='testL4',stage=4,start_hour=4300,duration_steps=2,step_minutes=15,
       shock={'e':.85,'c':.55},enable_propagation=True,pre_event_warmup_steps=8,
       grid_limit_kW=250,response_delay_steps=2,automatic_fast_shedding=True)
    error=None
    try:rollout(env,s)
    except (ValueError,RuntimeError) as exc:error=exc.simulation_diagnostic
    save('legacy_L4_retained',dict(scenario=s,error=error,events=env.event_history,segments=env.segment_history))
    print(json.dumps(dict(R01_transfer_kW=core['R01']['debug']['precontrol_GT_transfer_kW'],
        R03_timer=core['R03_controlled']['timer'],R04=core['R04'],step_comparison=compare,
        legacy_L4_error=error and error['message']),ensure_ascii=False,indent=2))
    if error:return 1
    return 0

if __name__=='__main__':sys.exit(main(Path(sys.argv[1])))
