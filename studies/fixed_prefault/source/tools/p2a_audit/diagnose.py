"""Pre-register then run <=23 development specs; keep all unsuccessful attempts."""
import copy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.pipeline import read_data
from hospital_ies.dataset_export import write_json
from generate_nonmpc_training import run
from scenario_sampler import sample_scenarios
BASE=ROOT/'outputs/v43_p2a_20261013_120000'

def register():
    dest=BASE/'diagnostic_plan.json'
    if dest.exists():raise FileExistsError('Pre-registration cannot be overwritten')
    frozen=json.loads((BASE/'baseline_replay/scenarios.json').read_text(encoding='utf-8'))
    sampled=sample_scenarios(read_data(ROOT),2,6,20261013,suite_id='p2a_diagnostic',
        event_classes=('L1','L2','L3','L4a','L4b'))
    # Independently prescribe the two interface timing cases BEFORE execution.
    b=[s for s in sampled if s['event_class']=='L4b']
    for i,s in enumerate(b):
        s['shock_start_minute']=0.;s['fault_start_minute']=0. if i==0 else 7.5
        # Rebuild derived script/identity after this declared design intervention.
        from hospital_ies.dataset_manifest import digest
        for event in s['exogenous_events']:
            kind='shock' if event['kind']=='load_shock' else 'fault'
            event['start_minute']=s[kind+'_start_minute']
            event['end_minute']=s[kind+'_start_minute']+s[kind+'_duration_minutes']
        s['exogenous_stream_id']='preregistered_timing:'+digest(s['exogenous_events'])
        s['timing_design']='prescribed simultaneous / 7.5-minute stagger; not a random probability sample'
    additional=[
        dict(id='p2a_demand_drop',stage=2,start_hour=4300,duration_steps=6,step_minutes=15,
            shock={'e':.4,'c':.2},shock_start_minute=0.,shock_duration_minutes=7.5),
        dict(id='p2a_diesel_start_grid_recovery',stage=1,start_hour=4300,duration_steps=6,step_minutes=15,
            faults={'GRID':0.},fault_start_minute=0.,fault_duration_minutes=30.,pre_event_grid_limit_kW=850.),
        dict(id='p2a_solved_P0_shortage',stage=1,start_hour=4300,duration_steps=6,step_minutes=15,
            faults={'GT_1':0.,'GT_2':0.,'GT_3':0.,'GRID':0.,'DIESEL':0.},
            grid_limit_kW=0.,pre_event_grid_limit_kW=850.)]
    delayed=copy.deepcopy(next(s for s in frozen if s['id']=='smoketest_L3_0000_h3255'))
    delayed.update(id='p2a_original_L3_delay001',family_id='smoketest_L3_0000',
        parent_template_id='smoketest_L3_0000',diagnostic_regime='positive_delay_no_absorption')
    contract=json.loads((ROOT/'configs/p1b_execution_research.json').read_text())
    late=dict(contract,surplus_interlock_delay_minutes=.01)
    write_json(dest,dict(seed=20261013,policies=['priority_milp','secure_milp'],
        plan_version='p2a.diagnostic.preregistered.v1',maximum_specifications=23,
        purpose='development only; no blind-test or population-probability claim',
        batches=[dict(name='research',scenarios=frozen+sampled+additional,contract=contract),
                 dict(name='unsupported_positive_delay',scenarios=[delayed],contract=late)],
        integration_atol_kWh=1e-6,balance_atol_kW=1e-4,steam_balance_atol_lbph=1e-4,
        repeat_atol=1e-6,repeat_rtol=1e-9,excluded_repeat_fields=['wall_clock_timing']))
    print('registered',len(frozen+sampled+additional)+1,'specifications; no simulation')

def execute(name):
    plan=json.loads((BASE/'diagnostic_plan.json').read_text(encoding='utf-8'))
    addendum=json.loads((BASE/'diagnostic_seed_addendum.json').read_text(encoding='utf-8'))
    out=BASE/name
    if out.exists():raise FileExistsError('No resume/overwrite for diagnostic attempt')
    out.mkdir();all_results={}
    for batch in plan['batches']:
        all_results[batch['name']]=run(steps=6,risk_horizon=2,seed=addendum['initialization_seed'],out=out/batch['name'],
            scenario_specs=batch['scenarios'],execution_contract=batch['contract'])
    write_json(out/'batch_metadata.json',all_results)
    print(json.dumps(all_results,ensure_ascii=True))

if __name__=='__main__':
    if sys.argv[1]=='register':register()
    elif sys.argv[1]=='seed_addendum':
        dest=BASE/'diagnostic_seed_addendum.json'
        if dest.exists():raise FileExistsError('Immutable preregistration addendum')
        write_json(dest,dict(initialization_seed=20261012,sampling_seed=20261013,
            reason='Before any P2A diagnostic trajectories: preserve original P1B initialization seed; sampled exogenous scripts remain byte-identical to preregistered plan. P1B reset currently deterministic independent of seed.'))
        print('seed provenance clarified before diagnostic execution')
    else:execute(sys.argv[1])
