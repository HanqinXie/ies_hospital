"""Actual error-prefix, observation, action and energy integration checks."""
import copy
import json
from pathlib import Path
import sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def settings(out):
    return dict(out=out,steps=4,risk_horizon=1,policies=('priority_milp',),
        execution_contract=json.loads((ROOT/'configs/p1b_execution_research.json').read_text()),
        scenario_specs=[dict(id='actual_prefix',stage=2,start_hour=4300,duration_steps=4,
           shock={'e':.2},shock_start_minute=7.5,shock_duration_minutes=15.)])

def test_solver_timeout_preserves_prefix_and_unknown_future(tmp_path,monkeypatch):
    from generate_nonmpc_training import run
    from hospital_ies.state_transition import TransitionCore
    from hospital_ies.execution import SolverExecutionError
    import hospital_ies.state_transition as module
    original=TransitionCore._solve
    def injected(self,*args,**kwargs):
        if self.time>=15:
            def fail(*a,**k):raise TimeoutError('P2A explicit timeout injection')
            with monkeypatch.context() as patch:
                patch.setattr(module,'_solve_milp',fail)
                return original(self,*args,**kwargs)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(TransitionCore,'_solve',injected)
    result=run(**settings(tmp_path))
    assert result['solver_error']==1 and result['attempted']==1
    error=json.loads((tmp_path/'errors.json').read_text())[0]
    assert error['solver_errors'][0]['error_type']=='TimeoutError'
    assert error['error']['classification']=='solver_error'
    uq=json.loads((tmp_path/'scenario_uq.json').read_text())[0]
    assert uq['p0_failed_any'] is None and not uq['full_trajectory_outcome_valid']
    decisions=json.loads((tmp_path/'decision_timeseries.json').read_text())
    assert decisions[0]['labels']['15']['value']==0
    assert decisions[0]['labels']['30']['value'] is None
    assert decisions[0]['labels']['30']['unknown_reason']=='solver_error'
    assert decisions[-1]['outcome_mask'] is False
    assert len(json.loads((tmp_path/'subinterval_trace.json').read_text()))>=1

def test_solved_shortage_is_valid_failure_not_solver_error(tmp_path):
    from generate_nonmpc_training import run
    args=settings(tmp_path)
    args['scenario_specs']=[dict(id='P0_valid_shortage',stage=1,start_hour=4300,duration_steps=4,
        faults={'GT_1':0.,'GT_2':0.,'GT_3':0.,'GRID':0.,'DIESEL':0.},grid_limit_kW=0.,
        pre_event_grid_limit_kW=850.)]
    r=run(**args)
    assert r['completed']==1 and r['physical_service_failure']==1 and r['solver_error']==0
    rows=json.loads((tmp_path/'decision_timeseries.json').read_text())
    assert rows[0]['labels']['15']['value']==1
    assert rows[1]['labels']['15']['at_risk'] is False

def test_subinterval_energy_and_legacy_pre_action_features(tmp_path):
    from generate_nonmpc_training import run
    import pandas as pd
    args=settings(tmp_path);result=run(**args)
    assert result['completed']==1
    segments=[x['segment'] for x in json.loads((tmp_path/'subinterval_trace.json').read_text())]
    assert any(s['end_minute']==7.5 for s in segments)
    uq=json.loads((tmp_path/'scenario_uq.json').read_text())[0]
    for gi,g in enumerate(('P0','P1','P2','P3')):
        for ki,k in enumerate(('e','h','c')):
            integral=sum(s['unserved_kW'][gi][ki]*(s['end_minute']-s['start_minute'])/60 for s in segments)
            assert uq[f'{g}_{k}_ENS_kWh']==pytest.approx(integral,abs=1e-6,rel=1e-9)
    legacy=pd.read_csv(tmp_path/'closed_loop_expert_steps.csv')
    assert abs(legacy.iloc[0].demand_shock_e)<1e-9
    online=pd.read_csv(tmp_path/'decision_features.csv')
    assert not any(k.startswith(('future_','post_','p0_failed')) for k in online)

@pytest.mark.parametrize('key',['gt_1','gt_2','gt_3','grid_import','diesel','absorption_cooling_kW'])
def test_each_formal_target_is_actually_accepted_by_environment(key):
    from hospital_ies.control_env import HospitalIESEnv
    from hospital_ies.training_schema import action_targets,ACTIONS
    env=HospitalIESEnv(ROOT,execution_contract=settings(None)['execution_contract'])
    env.reset(scenario=dict(id='target_contract',stage=2,start_hour=4300,duration_steps=2,shock={'e':.1}))
    action={'targets':{key:10.}}
    _,_,_,_,info=env.step(action)
    assert info['requested_action']==action
    assert info['command_id']==1
    assert action_targets([10]*6)['targets'][key]==10
    assert len(ACTIONS)==6

def test_online_features_reject_future_available_metadata():
    from hospital_ies.training_schema import build_decision_features
    with pytest.raises(ValueError,match='Future'):
        build_decision_features([dict(decision_time_min=0,features={'x':1},feature_metadata={'x':{'available_time_min':1}})])

def test_invalid_reset_never_inherits_previous_trajectory(tmp_path,monkeypatch):
    from generate_nonmpc_training import run
    args=settings(tmp_path)
    args['scenario_specs'].append(dict(id='invalid_reset',stage=99,start_hour=4300,duration_steps=4))
    run(**args)
    import hospital_ies.dataset_manifest as dm
    cp=dm.load_checkpoint(dm.checkpoint_path(tmp_path,'invalid_reset','priority_milp'),
        json.loads((tmp_path/'manifest_p2a.json').read_text())['signature'])
    assert cp['segments']==[] and cp['events']==[] and cp['zones']==[]
    assert cp['rows']==[] and cp['summary']['steps']==0
    assert cp['summary']['p0_failed_any'] is None
