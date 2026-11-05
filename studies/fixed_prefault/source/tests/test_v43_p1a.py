"""P1A checks with criteria registered before implementation/results."""
import copy
from pathlib import Path
import sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.resilience import RuntimeState,empty_faults,_solve_milp,simulate_scenario
from hospital_ies.model import gt_level_properties

@pytest.fixture(scope='module')
def env():return HospitalIESEnv(ROOT)

def absolute_case(dt):
    return dict(id='P1A_absolute_clock',stage=4,start_hour=5456,
        duration_steps=int(60/dt),step_minutes=dt,enable_propagation=True,
        shock={'e':3.},shock_start_minute=7.5,response_delay_minutes=67.5,
        grid_limit_kW=0.,pre_event_warmup_steps=6,automatic_fast_shedding=False,
        precontrol_sheddable_fraction_by_priority={'P1':0.,'P2':0.,'P3':0.})

def rollout(env,s,initial_targets=False):
    env.reset(scenario=copy.deepcopy(s),seed=20261012)
    a=env.core.executed
    targets={**{f'gt_{i+1}':v for i,v in enumerate(a['gt_electric_kW'])},
             'grid_import':a['grid_import_kW'],'diesel':a['diesel_kW'],
             'absorption_cooling_kW':a['absorption_cooling_kW']}
    for step in range(s['duration_steps']):
        env.step({'targets':targets} if initial_targets and step==0 else {})
    return env.traces()[0],copy.deepcopy(env.segment_history),copy.deepcopy(env.trip_events)

def test_absolute_time_step_halving(env):
    results=[]
    for dt in (30,15,7.5):
        trace,segments,events=rollout(env,absolute_case(dt),initial_targets=True)
        results.append((np.sum([np.asarray(x['unserved_kW'])*(x['end_minute']-x['start_minute'])/60 for x in segments],axis=0),
                        sorted(e['trip_time_minute'] for e in events),copy.deepcopy(env.state.thermal_delta),
                        np.sum([np.r_[x['action']['gt_electric_kW'],x['action']['grid_import_kW'],x['action']['diesel_kW']]
                            *(x['end_minute']-x['start_minute'])/60 for x in segments],axis=0)))
        assert events and all(e['trip_time_minute']==pytest.approx(22.5,abs=1e-8) for e in events)
        assert max(max(map(abs,x['energy_balance_residuals'])) for x in segments)<=1e-4
        expected=trace[[f'{g}_{k}_unserved_kW' for g in ('P0','P1','P2','P3') for k in ('e','h','c')]].sum().to_numpy()*dt/60
        np.testing.assert_allclose(expected,results[-1][0].ravel(),atol=1e-6,rtol=0)
    for energy,times,temp,supply in results[1:]:
        np.testing.assert_allclose(energy,results[0][0],atol=1e-4,rtol=0)
        np.testing.assert_allclose(times,results[0][1],atol=1e-8,rtol=0)
        np.testing.assert_allclose(list(temp.values()),list(results[0][2].values()),atol=1e-6,rtol=0)
        np.testing.assert_allclose(supply,results[0][3],atol=1e-4,rtol=0)

def test_command_queue_arrival_and_reset(env):
    s=dict(id='P1A_queue',stage=2,start_hour=5456,duration_steps=4,step_minutes=15,
        enable_propagation=False,shock={'e':1.},response_delay_minutes=30)
    obs,_=env.reset(scenario=s)
    env.step({})
    assert env.core.pending_commands
    assert not [e for e in env.event_history if e['kind']=='command_executed']
    env.step({})
    executed=[e for e in env.event_history if e['kind']=='command_executed']
    assert executed and min(e['time_minute'] for e in executed)==30
    env.reset(scenario=s)
    assert env.core.pending_commands==[] and env.event_history==[] and env.segment_history==[]
    np.testing.assert_allclose(env.state.gt_prev,obs['gt_prev_kW'],atol=1e-6,rtol=0)

def test_normal_ramps_and_fault_recovery(env):
    row=next(env.hourly.iloc[[5455]].itertuples(index=False))
    rated=float(gt_level_properties(env.plant,float(row.outdoor_temperature_C))[0][-1])
    state=RuntimeState(np.array([rated,0.,0.]),2400.,set(),np.zeros(3),{})
    loads=np.zeros((4,3));loads[0,0]=500.
    for avail in (.1,0.,1.,1.):
        f=empty_faults();f['GT_1']=avail
        before=state.gt_prev.copy()
        sol=_solve_milp(row,env.plant,loads,f,state,.25,850.,False,900.,config=env.config,
            tracking_targets={'gt_1':rated,'gt_2':0.,'gt_3':0.})
        after=np.array(sol['action']['gt_electric_kW'])
        assert after[0]<=rated*avail+1e-4
        assert after[0]<=before[0]+225+1e-4
        state.gt_prev=after
    assert state.gt_prev[0]>0
    # Without a capacity loss, ordinary down-ramp remains binding.
    loads[0,0]=200.;state.gt_prev=np.array([rated,0.,0.])
    sol=_solve_milp(row,env.plant,loads,empty_faults(),state,.25,850.,False,900.,config=env.config,
        tracking_targets={'gt_1':0.})
    assert rated-225-1e-4<=sol['action']['gt_electric_kW'][0]<=rated+1e-4

def test_both_entrypoints_share_event_energy(env):
    s=absolute_case(30)
    direct,_,events=rollout(env,s)
    trace,_,summary=simulate_scenario(ROOT,s,env.plant,env.hourly,env.groups.reset_index(),
        env.zones,env.config,env.thermal_cache)
    cols=[f'{g}_{k}_unserved_kW' for g in ('P0','P1','P2','P3') for k in ('e','h','c')]
    np.testing.assert_allclose(direct[cols],trace[cols],atol=1e-6,rtol=0)
    assert summary['trip_events']==events
    for left,right in zip(env.segment_history,summary['segment_trace']):
        assert left['start_minute']==right['start_minute'] and left['end_minute']==right['end_minute']
        for key in left['action']:
            np.testing.assert_allclose(left['action'][key],right['action'][key],atol=1e-6,rtol=0)

def test_solver_exception_keeps_diagnostic_input(env,monkeypatch):
    import hospital_ies.state_transition as transition
    s=dict(id='P1A_injected_error',stage=2,start_hour=5456,duration_steps=2,
           step_minutes=15,enable_propagation=False,shock={})
    env.reset(scenario=s)
    def fail(*args,**kwargs):raise RuntimeError('P1A injected solver timeout')
    monkeypatch.setattr(transition,'_solve_milp',fail)
    with pytest.raises(RuntimeError,match='injected solver timeout') as caught:env.step({})
    error=env.solver_errors[-1]
    assert error['scenario_id']==s['id'] and error['time_minute']==0
    assert error['state']['gt_prev'] and error['loads_kW'] and error['command'] is not None
    assert env.steps_done==0 and not env.history
    assert caught.value.simulation_diagnostic==error


def test_same_timestamp_commands_do_not_multiply_ramp(env):
    s=dict(id='P1A_ramp_clock',stage=2,start_hour=5456,duration_steps=2,
           step_minutes=15,enable_propagation=False,shock={})
    env.reset(scenario=s)
    env.core.executed['gt_electric_kW']=[0.,0.,0.]
    env.state.gt_prev[:]=0.
    env.core.time=15.
    f=empty_faults();dispatch=copy.deepcopy(env.core.executed)
    dispatch['gt_electric_kW']=[300.,300.,300.]
    # Two simultaneously due calls must share one reachability budget.
    one=env.core._clamp(dispatch,f,normal_ramp=True)
    env.core.executed=one;env.core._commit_sources(one)
    two=env.core._clamp(dispatch,f,normal_ramp=True)
    np.testing.assert_allclose(one['gt_electric_kW'],two['gt_electric_kW'],atol=1e-6,rtol=0)


def test_automatic_shedding_has_cumulative_fraction_cap(env):
    s=dict(id='P1A_shed_cap',stage=4,start_hour=5456,duration_steps=2,
           step_minutes=15,enable_propagation=True,shock={'e':3.},
           automatic_fast_shedding=True,precontrol_sheddable_fraction_by_priority={'P3':.1})
    env.reset(scenario=s)
    loads=env.core.data_at(0)[2]
    for _ in range(5):env.core._automatic_shed(loads)
    assert np.all(env.core.automatic_disconnected[3]<=.1*loads[3]+1e-6)
    assert not np.any(env.core.automatic_disconnected[:3])


def test_last_hour_can_finish_without_next_year_lookup(env):
    s=dict(id='P1A_year_end',stage=2,start_hour=8760,duration_steps=4,
           step_minutes=15,enable_propagation=False,shock={})
    rollout(env,s)
    assert env.terminated and env.core.time==60.


def test_fault_recovery_does_not_clear_protection_lock(env):
    s=dict(id='P1A_locked_recovery',stage=3,start_hour=5456,duration_steps=3,
           step_minutes=15,enable_propagation=True,faults={'GT_1':0.},
           fault_start_minute=7.5,fault_duration_minutes=7.5,response_delay_minutes=30.)
    env.reset(scenario=s)
    env.state.extra_faulted.add('GT_1')
    env.core.time=15.;env.core.boundary()
    assert env.core.observe()['availability']['GT_1']==0.
    assert env.core.executed['gt_electric_kW'][0]==0.


def test_midstep_fault_and_boundary_recovery_obey_capacity_and_ramp(env):
    s=dict(id='P1A_recovery',stage=1,start_hour=5456,duration_steps=3,step_minutes=15,
           enable_propagation=False,faults={'GT_1':.1},fault_start_minute=7.5,fault_duration_minutes=7.5)
    env.reset(scenario=s)
    for _ in range(3):env.step({'targets':{'gt_1':308.94656,'gt_2':0.,'gt_3':0.}})
    segments=env.segment_history
    down=next(x for x in segments if x['start_minute']==7.5)
    up=next(x for x in segments if x['start_minute']==15.)
    assert down['action']['gt_electric_kW'][0]<=30.894656+1e-4
    assert down['availability']['GT_1']==.1 and up['availability']['GT_1']==1.
    assert down['action']['gt_electric_kW'][0]<up['action']['gt_electric_kW'][0]<=down['action']['gt_electric_kW'][0]+225.+1e-4
    assert any(e['kind']=='forced_capacity_or_permission_clamp' and e['time_minute']==7.5 for e in env.event_history)


def test_failed_reset_clears_old_queue_and_retains_error(env,monkeypatch):
    import hospital_ies.control_env as control
    s=dict(id='P1A_reset_error',stage=2,start_hour=5456,duration_steps=2,
           step_minutes=15,enable_propagation=False,shock={'e':1.},response_delay_steps=2)
    env.reset(scenario=s);env.step({});assert env.core.pending_commands
    def fail(*args,**kwargs):raise RuntimeError('P1A initialization failure')
    monkeypatch.setattr(control,'_solve_milp',fail)
    with pytest.raises(RuntimeError,match='initialization failure') as caught:env.reset(scenario=s)
    assert env.core is None and env.terminated and not env.history
    assert caught.value.simulation_diagnostic==env.solver_errors[-1]
    assert env.solver_errors[-1]['phase']=='initialization'


def test_shared_down_ramp_budget_and_short_arrival_interval(env):
    s=dict(id='P1A_down_ramp_clock',stage=2,start_hour=5456,duration_steps=2,
           step_minutes=15,enable_propagation=False,shock={})
    env.reset(scenario=s);env.core.executed['gt_electric_kW']=[300.,300.,300.]
    dispatch=copy.deepcopy(env.core.executed);dispatch['gt_electric_kW']=[0.,0.,0.]
    one=env.core._clamp(dispatch,empty_faults(),normal_ramp=True)
    env.core.executed=one
    two=env.core._clamp(dispatch,empty_faults(),normal_ramp=True)
    np.testing.assert_allclose(one['gt_electric_kW'],[75.,75.,75.],atol=1e-6,rtol=0)
    np.testing.assert_allclose(two['gt_electric_kW'],one['gt_electric_kW'],atol=1e-6,rtol=0)
    env.core.time=7.5;dispatch['gt_electric_kW']=[300.,300.,300.]
    three=env.core._clamp(dispatch,empty_faults(),normal_ramp=True)
    np.testing.assert_allclose(three['gt_electric_kW'],[187.5]*3,atol=1e-6,rtol=0)
