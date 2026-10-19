"""Export already-tested recovery/queue cases and a retained solver exception."""
import copy,json,sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.state_transition import clean
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
env=HospitalIESEnv(ROOT)

def save(name,data):
    (out/(name+'.json')).write_text(json.dumps(clean(data),indent=2),encoding='utf-8')

s=dict(id='P1A_recovery',stage=1,start_hour=5456,duration_steps=3,step_minutes=15,
       enable_propagation=False,faults={'GT_1':.1},fault_start_minute=7.5,fault_duration_minutes=7.5)
env.reset(scenario=s)
action={'targets':{'gt_1':308.94656,'gt_2':0.,'gt_3':0.}}
for _ in range(3):env.step(action)
save('recovery',dict(scenario=s,request=action,events=env.event_history,segments=env.segment_history,solver_calls=env.core.solver_calls))
env.traces()[0].to_csv(out/'recovery_steps.csv',index=False,encoding='utf-8-sig')

s=dict(id='P1A_queue',stage=2,start_hour=5456,duration_steps=4,step_minutes=15,
       enable_propagation=False,shock={'e':1.},response_delay_minutes=30)
env.reset(scenario=s)
for _ in range(2):env.step({})
data=copy.deepcopy(dict(scenario=s,events=env.event_history,segments=env.segment_history,pending=env.core.pending_commands))
env.reset(scenario=s)
data['after_reset']=dict(time_minute=env.core.time,pending=env.core.pending_commands,events=env.event_history,
                         segments=env.segment_history,overload_minutes=env.state.overload_minutes)
save('queue_reset',data)

def fail(*args,**kwargs):raise TimeoutError('P1A diagnostic injected solver TimeoutError; no fallback permitted')
with patch('hospital_ies.state_transition._solve_milp',fail):
    try:env.step({})
    except TimeoutError as exc:
        assert exc.simulation_diagnostic==env.solver_errors[-1]
        assert env.core.failed and env.steps_done==0 and not env.history
        save('timeout_error',dict(error=exc.simulation_diagnostic,events=env.event_history,solver_calls=env.core.solver_calls))
    else:raise AssertionError('Solver exception was swallowed')
print('Recovery/queue/reset traces exported; actual injected TimeoutError preserved and rethrown; assertions passed.')
