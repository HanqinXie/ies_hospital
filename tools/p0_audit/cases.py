"""Small v4.2 counterexamples; never mutate production files or configuration."""
from __future__ import annotations
import copy
import numpy as np
from hospital_ies.model import gt_level_properties
from hospital_ies.resilience import (
    RuntimeState, empty_faults, pre_control_cascade, _solve_milp, simulate_scenario,
)

def proxy_case(env, *, balanced=False, controlled=False, sustained=False):
    power = 360. if sustained else 300.
    state = RuntimeState(np.array([power,power,0.]),2400.,set(),
                         np.array([10.,10.,0.]) if sustained else np.zeros(3),{})
    loads = np.zeros((4,3))
    loads[0,0] = 2*power if balanced or sustained else 1800.
    config = dict(env.config, pre_event_electric_demand_kW=300. if balanced else 2*power,
                  pre_event_cooling_demand_kW=0., precontrol_load_shed_enabled=False)
    trips, stress, debug = pre_control_cascade(state,empty_faults(),loads,env.plant,
        config,15,controlled,0.,0.,False)
    return dict(trips=trips,stress=stress,debug=debug,
                timer=state.overload_minutes.tolist(),previous_GT_kW=2*power,
                connected_electric_kW=float(loads[:,0].sum()))

def delay_case(env, delay):
    scenario = dict(id='p0_delay_setpoint', stage=2, start_hour=5456,
        duration_steps=4,step_minutes=15,enable_propagation=False,
        shock={'e':1.,'c':.5},shock_start_step=1,response_delay_steps=delay,
        automatic_fast_shedding=False)
    env.reset(scenario=scenario,seed=20261012)
    records=[]
    for _ in range(4):
        _,_,_,_,info=env.step({})
        pre=info['pre_observation'];a=info['executed_action']
        records.append(dict(step=info['step'],delayed=info['control_delayed'],
            before=[*pre['gt_prev_kW'],pre['grid_import_prev_kW']],
            after=[*a['gt_electric_kW'],a['grid_import_kW']],
            lost=info['unserved_kW'].tolist()))
    return dict(scenario=scenario,records=records)

def derate_case(env, availability, load_kw=100.):
    row=next(env.hourly.iloc[[5455]].itertuples(index=False))
    rated=float(gt_level_properties(env.plant,float(row.outdoor_temperature_C))[0][-1])
    state=RuntimeState(np.array([rated,0.,0.]),2400.,set(),np.zeros(3),{})
    loads=np.zeros((4,3));loads[0,0]=load_kw
    faults=empty_faults();faults['GT_1']=availability
    result=dict(availability=availability,previous_kW=rated,
        normal_ramp_lower_kW=rated-225.,capacity_kW=rated*availability)
    try:
        sol=_solve_milp(row,env.plant,loads,faults,state,.25,850.,False,900.,config=env.config)
        result.update(success=True,GT1_kW=sol['action']['gt_electric_kW'][0],
                      balance=sol['energy_balance_residuals'])
    except RuntimeError as exc:
        result.update(success=False,error=str(exc))
    return result

def timing_scenario(minutes):
    return dict(id='p0_timing_'+str(minutes),stage=4,start_hour=5456,
        duration_steps=2,step_minutes=minutes,enable_propagation=True,
        shock={'e':3.},response_delay_steps=2,grid_limit_kW=0.,
        pre_event_warmup_steps=6,automatic_fast_shedding=False,
        precontrol_sheddable_fraction_by_priority={'P1':0.,'P2':0.,'P3':0.})

def env_timing_case(env, minutes):
    scenario=timing_scenario(minutes)
    env.reset(scenario=scenario,seed=20261012)
    records=[]
    for _ in range(2):
        _,_,_,_,info=env.step({})
        records.append(dict(step=info['step'],new_trips=info['new_trips'],
            time=info['trip_event_time_minute'],effective_step=info['trip_effective_from_step'],
            availability=info['pre_observation']['availability'],
            GT_kW=info['executed_action']['gt_electric_kW'],
            lost=info['unserved_kW'].tolist(),stress=info['precontrol_GT_stress_ratio']))
    return dict(scenario=scenario,hold_minutes=env.config['gt_overload_hold_minutes'],
                records=records,events=copy.deepcopy(env.trip_events))

def legacy_timing_case(env):
    scenario=timing_scenario(15)
    # Zero sheddable load capability isolates protection timing; only the local
    # diagnostic copy changes, identically for both public simulation paths.
    config=dict(env.config,precontrol_load_shed_enabled=False)
    trace,_,summary=simulate_scenario(env.root,scenario,env.plant,env.hourly,
        env.groups.reset_index(),env.zones,config,env.thermal_cache)
    cols=['step','minute','new_trips','active_faults',
          'gt_electric_kW_1','gt_electric_kW_2','gt_electric_kW_3']
    return dict(scenario=scenario,local_assumption='no automatic shedding',
                records=trace[cols].to_dict('records'),summary=summary)

def resume_case(root, scratch):
    """Replay existing checkpoints in an isolated copy; generate no new accidents.

    Only add JSON whitespace (no physical parameter change). The documented
    byte-hash dependency guard must still notice the changed dependency.
    """
    import hashlib
    import shutil
    import generate_nonmpc_training as generator
    scratch.mkdir(parents=True,exist_ok=False)
    for folder in ('configs','inputs'):
        shutil.copytree(root/folder,scratch/folder)
    for name in ('scenario_sampler.py','generate_nonmpc_training.py',
                 'src/hospital_ies/control_env.py','src/hospital_ies/resilience.py'):
        dest=scratch/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/name,dest)
    output=scratch/'replayed_checkpoints'
    shutil.copytree(root/'outputs/v42_coverage_validation',output)
    original_root=generator.ROOT
    try:
        generator.ROOT=scratch
        common=dict(episodes_per_stage=20,steps=12,seed=20261012,
                    out=output,resume=True,risk_horizon=4,suite_id='coverage')
        unchanged=generator.run(**common)
        path=scratch/'configs/plant_configs.json'
        old=path.read_bytes();path.write_bytes(old+b'\n')
        result=dict(unchanged_resume_steps=unchanged['steps'],
                    hash_before=hashlib.sha256(old).hexdigest(),
                    hash_after=hashlib.sha256(path.read_bytes()).hexdigest(),
                    changed='configs/plant_configs.json: newline only',
                    copied_existing_families=80,new_families=0)
        try:
            replay=generator.run(**common)
            result.update(rejected=False,resumed_steps=replay['steps'])
        except ValueError as exc:
            result.update(rejected=True,error=str(exc))
        return result
    finally:
        generator.ROOT=original_root
