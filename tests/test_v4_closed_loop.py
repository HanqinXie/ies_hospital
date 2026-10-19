"""v4.2 no-MPC policy and sampler tests. Original v2/v3 tests are preserved."""
import sys,json
from pathlib import Path
import numpy as np,pandas as pd,pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT))
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.resilience import empty_faults,_solve_milp,loads_for_step
from scenario_sampler import sample_scenarios,coverage_summary,SINGLE_DEVICES,COMMON_MODE_DEVICES
from generate_nonmpc_training import run,add_future_risk_labels

@pytest.fixture(scope='module')
def env():return HospitalIESEnv(ROOT)

def scenario(stage=1,h=4300,steps=2):
    s={'id':'test','stage':stage,'start_hour':h,'duration_steps':steps,
       'step_minutes':15,'enable_propagation':stage>=3}
    if stage in (1,3):s['faults']={'GT_1':0.}
    else:s['shock']={'e':0.2,'c':0.3}
    if stage>=3:s['response_delay_steps']=1
    return s

def test_reproducible_reset_and_energy_balance(env):
    outputs=[]
    for _ in range(2):
        ob,_=env.reset(scenario=scenario(stage=2),seed=42)
        trace=[]
        for _ in range(2):
            ob,_,_,_,info=env.step({})
            trace.append((info['executed_action']['grid_import_kW'],info['p0_guarantee_failure']))
            assert max(map(abs,info['energy_balance_residuals']))<1e-4
        outputs.append(trace)
    assert outputs[0]==outputs[1]

def test_security_policy_is_feasible_not_economic_cost(env):
    s=scenario(stage=2,h=6347,steps=2)
    ob,_=env.reset(scenario=s)
    _,_,_,_,a=env.step({})
    gt_default=sum(a['executed_action']['gt_electric_kW'])
    ob,_=env.reset(scenario=s)
    _,_,_,_,b=env.step({'security':{'gt_loading_penalty_USD_per_kWh':0.5}})
    gt_safe=sum(b['executed_action']['gt_electric_kW'])
    assert gt_safe<=gt_default+1e-5
    assert b['security_objective_penalty_USD_step']>=0
    assert max(map(abs,b['energy_balance_residuals']))<1e-4
    # Synthetic security proxy must not be counted as actual operating expenditure.
    assert b['cost_USD_step']>=0

def test_unknown_security_action_rejected(env):
    env.reset(scenario=scenario())
    with pytest.raises(ValueError):env.step({'security':{'unsafe_override':12}})
    env.reset(scenario=scenario())
    with pytest.raises(ValueError):env.step({'security':{'gt_loading_penalty_USD_per_kWh':float('nan')}})

def test_one_tower_derate_without_fake_total_shutdown(env):
    row=next(env.hourly.iloc[[6346]].itertuples(index=False))
    loads=loads_for_step(env.groups.loc[6347].to_dict(),{'c':2.})
    env.reset(scenario=scenario(h=6347))
    av=empty_faults()
    full=_solve_milp(row,env.plant,loads,av,env.state,.25,850.,False,900.,config=env.config)
    av['TOWER_1']=0.
    part=_solve_milp(row,env.plant,loads,av,env.state,.25,850.,False,900.,config=env.config)
    assert part['action']['tower_rejection_kW']<=.75*env.plant.cooling_tower_rejection_kW+1e-5
    assert part['lost'][:,2].sum()>=full['lost'][:,2].sum()-1e-5

def test_full_N_minus_1_and_l3_initiators(env):
    a=sample_scenarios(env.hourly,100,12,20261012)
    b=sample_scenarios(env.hourly,100,12,20261012)
    assert a==b
    c=coverage_summary(a)
    assert c['L1_missing_devices']==[]
    assert c['L3_missing_initiators']==[]
    assert c['L4_max_exact_template_count']<=2
    assert len(c['L4_strata'])==4
    assert set(c['model_devices'])==set(SINGLE_DEVICES+COMMON_MODE_DEVICES)
    assert all(x['faults'] for x in a if x['stage']==3)

def test_current_shock_no_advance_leak(env):
    s=scenario(stage=4,steps=5)
    s['shock_start_step']=2;s['response_delay_steps']=0
    o,_=env.reset(scenario=s)
    baseline=o['loads_kW'][0,0]
    o,*_=env.step({})
    assert o['loads_kW'][0,0]==pytest.approx(baseline)
    o,*_=env.step({})
    assert o['loads_kW'][0,0]>baseline*1.1

def test_actual_dispatch_changes_pre_fault_gt_state(env):
    s={'id':'preposition','stage':3,'start_hour':6347,'duration_steps':4,
       'step_minutes':15,'faults':{'GT_1':0.},'fault_start_step':2,
       'enable_propagation':True,'response_delay_steps':2,
       'pre_event_warmup_steps':8,'grid_limit_kW':850.,'automatic_fast_shedding':False}
    def run_policy(weight):
        ob,_=env.reset(scenario=s)
        before=[]
        for i in range(4):
            ob,_,_,_,info=env.step({'security':{'gt_loading_penalty_USD_per_kWh':weight}})
            before.append(info['executed_action']['gt_electric_kW'])
        return np.asarray(before)
    a=run_policy(0);b=run_policy(1.0)
    assert max(abs(a[0]-b[0]))>1e-4

def test_future_labels_exclude_current_and_tail():
    x=pd.DataFrame({'episode_id':['A']*5,'step':range(5),'p0_failed':[1,0,1,0,0]})
    y=add_future_risk_labels(x,horizon=2)
    assert y['future_P0_failure_H'].tolist()==[1,1,0,0,0]
    assert y['future_horizon_valid'].tolist()==[True,True,True,False,False]

def test_nonmpc_dataset_both_strategies_no_leak(tmp_path):
    from scripts.audit_v42_dataset import audit
    # P1B: identical incidents/seed/initialization/objectives, explicit local
    # execution capabilities for both policies. Strict original is separately
    # retained as the no-sink infeasibility control in test_v43_p1b.py.
    contract=json.loads((ROOT/'configs/p1b_execution_research.json').read_text())
    fixed=json.loads((ROOT/'tests/fixtures/p1b_fixed_scenarios.json').read_text(encoding='utf-8'))[:8]
    res=run(2,6,20261012,tmp_path,risk_horizon=2,suite_id='smoketest',execution_contract=contract,scenario_specs=fixed)
    x=pd.read_csv(tmp_path/'closed_loop_expert_steps.csv')
    assert res['trajectories']==16 and res['steps']==96
    assert set(x.policy)=={'priority_milp','secure_milp'}
    assert x.groupby(['episode_family','policy']).size().eq(6).all()
    result=audit(tmp_path,strict=True)
    assert result['future_shock_leakage']==[]
    assert result['max_abs_balance_residual_kW']<1e-4
