"""Export actual finite-ramp, protection, diesel and negative-control evidence."""
import sys,json,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.execution import ModelConstraintInfeasible
from hospital_ies.state_transition import clean
from scenario_sampler import sample_scenarios
import test_v43_p1b as cases

def main(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    def save(name,obj):(out/(name+'.json')).write_text(json.dumps(clean(obj),ensure_ascii=False,indent=2),encoding='utf-8')
    env=HospitalIESEnv(ROOT);reset=env.reset;counter=[0];label=['control']
    def capturing_reset(*args,**kwargs):
        result=reset(*args,**kwargs);advance=env.core.advance
        def capturing_advance(end):
            result=advance(end);counter[0]+=1
            save(label[0]+'_'+str(counter[0]),dict(scenario=env.scenario,execution_contract=env.execution_contract,
                events=env.event_history,segments=env.segment_history,solver_calls=env.core.solver_calls,
                trips=env.trip_events,state=vars(env.state),end_minute=end))
            return result
        env.core.advance=capturing_advance;return result
    env.reset=capturing_reset
    for remaining in (.1,.2):
        label[0]='ramp_protection_remaining_'+str(remaining)
        cases.test_real_local_ramp_protection_halving(env,remaining)
    label[0]='finite_down';cases.test_finite_local_down_energy_and_step_halving(env)
    label[0]='diesel_energy';cases.test_diesel_port_energy_and_grid_recovery(env)
    env.reset=reset;neg=[]
    s=next(x for x in sample_scenarios(env.hourly,2,6,20261012,suite_id='smoketest') if x['id']=='smoketest_L3_0000_h3255')
    for delay in (None,.01,1.):
        from hospital_ies.execution import execution_contract
        env.execution_contract=execution_contract() if delay is None else cases.profile(surplus_interlock_delay_minutes=delay)
        error=None
        try:cases.run_case(env,s)
        except ModelConstraintInfeasible as exc:error=exc.simulation_diagnostic
        assert error and env.core.time==60. and env.steps_done==4
        save('negative_delay_'+str(delay),dict(scenario=s,execution_contract=env.execution_contract,
            error=error,events=env.event_history,segments=env.segment_history))
        neg.append(dict(delay=delay,time=env.core.time,steps=env.steps_done,category=error['category']))
    save('summary',dict(negative_controls=neg,exported_advances=counter[0],assertions='passed'))
    print(json.dumps(dict(exported_advances=counter[0],negative_controls=neg)))

if __name__=='__main__':main(sys.argv[1])
