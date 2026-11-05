"""Static branch previews may generate commands, never execute or mutate a mother."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_review_v27.objective_snapshot import _encode
from tools.integrated_learning.snapshot import digest


class FakeController:
    policy_id='C_static_test'
    def __init__(self):self.calls=0
    def decide(self,history):
        self.calls+=1
        assert [h['decision_time_min'] for h in history]==[75.,90.]
        assert all('scenario' not in h and 'labels' not in h for h in history)
        history[0]['features']['demand']=9999.
        return {'state_coordinated_joint':{}},{'ordinary_command_only':True}


class FakeCore:
    def __init__(self,env,bad_advance=False):
        self.env=env;self.time=90.;self.sequence=6;self.pending_commands=[];self.events=[];self.solver_calls=[];self.errors=[]
        self.executed={'gt_electric_kW':[0.,0.,0.],'grid_import_kW':500.}
        self.bad_advance=bad_advance
    def data_at(self,time):
        assert time==self.time
        return 1,SimpleNamespace(timestamp=pd.Timestamp('2017-07-20 9:00')),np.zeros((4,3)),{}
    def generate_command(self,request):
        self.sequence+=1
        if self.bad_advance:self.time+=15.
        layer={'rank':0,'name':'operating_cost_and_original_preferences','objective_value':3.,'lock_upper':3.00001}
        command={'id':self.sequence,'generated_minute':90.,'arrival_minute':90.,'request':copy.deepcopy(request),
            'dispatch':{'gt_electric_kW':[100.,0.,0.]},'joint_dispatch':{
                'planned_current_action':{'gt_fuel_MMBtu_h':.2,'boiler_steam_lbph':10.,'grid_import_kW':100.,'diesel_kW':0.,'gt_start_electric_kW':10.,'boiler_restart_electric_kW':0.},
                'diagnostic':{'layers':[layer],'command_GT_on':[True,False,False],'objective_order':[layer['name']],
                    'engineering':{'cost_optimum_USD_step':3.,'final_cost_USD_step':3.}},
                'readiness_context':{'actual_gt_kW':[0.,0.,0.],'pending_starts':[None,None,None]}}}
        self.pending_commands.append(command);self.events.append({'kind':'command_generated','command':command});self.solver_calls.append({'status':'success'})
        return command
    def advance(self,*args):raise AssertionError('Static preview cannot advance')


class FakeEnv:
    def __init__(self,bad_advance=False):
        self.state=SimpleNamespace(diesel_energy_kWh=2400.,gt_prev=np.zeros(3))
        self.dt_h=.25
        self.plant=SimpleNamespace(gas_price_usd_per_mmbtu=4.,steam_kWh_per_lb=.3,boiler_eff=.8,
            tariff={'electricity_offpeak_per_kWh':.05,'electricity_midpeak_per_kWh':.1,'electricity_onpeak_per_kWh':.2})
        self.closure_config={'station':{'gt_starter_kW_per_unit':10.}}
        self.core=FakeCore(self,bad_advance)
    def step(self,*args):raise AssertionError('Static preview cannot step')
    def reset(self,*args):raise AssertionError('Static preview cannot reset')


class FakeSnapshot:
    def __init__(self,bad_advance=False):
        self.mother=FakeEnv(bad_advance);self.minute=90.;self.binding={'source':'frozen-test'}
        self.document={'identity':'mother'};self.legal_history=[{'decision_time_min':75.,'features':{'demand':1.}}]
        self.current_observation={'decision_time_min':90.,'features':{'demand':2.},'execution_limits':{'effective_grid_import_limit_kW':850.}}
    @property
    def content_sha256(self):return digest(self.document)
    @property
    def physical_state_sha256(self):return digest(_encode(vars(self.mother.state)))
    def fork(self,*,io_resources=None,restore_random=True):
        assert io_resources=={'branch_resource':'new'}
        result=copy.deepcopy(self.mother);result.core.env=result;return result


def api():
    from tools.model_review_v27.objective_inspection import inspect_snapshot
    return inspect_snapshot


def test_static_preview_generates_on_an_independent_clone_without_executing():
    s=FakeSnapshot();controller=FakeController();before=s.physical_state_sha256
    r=api()(s,controller,io_resources={'branch_resource':'new'})
    assert r['status']=='static_command_generated'
    assert r['snapshot_content_sha256_before']==r['snapshot_content_sha256_after']
    assert r['mother_physical_state_sha256_before']==r['mother_physical_state_sha256_after']==before
    assert r['physical_time_advanced_minutes']==0. and r['clone_physical_invariants_unchanged']
    assert r['command']['id']==7 and r['mother_unchanged']
    assert s.mother.core.sequence==6 and s.mother.core.pending_commands==[]
    assert controller.calls==0 and s.legal_history[0]['features']['demand']==1.
    assert r['actual_held_dispatch_before_preview']['gt_electric_kW']==[0.,0.,0.]
    assert r['actual_energy_cost_USD_step'] is None
    assert r['planned_cost_components']['DG_planning_proxy_USD_step']==0.
    assert r['startup_request']['new_GT_command_indices']==[0]
    assert r['startup_request']['actual_startup_energy_delivered_kWh'] is None


def test_time_advancing_preview_is_explicit_failed_unknown_not_zero_filled():
    s=FakeSnapshot(bad_advance=True)
    r=api()(s,FakeController(),io_resources={'branch_resource':'new'})
    assert r['status']=='failed_static_preview' and r['error']['type']=='RuntimeError'
    assert r['physical_time_advanced_minutes']==15.
    assert not r['clone_physical_invariants_unchanged'] and r['mother_unchanged']
    assert s.mother.core.time==90. and r['actual_energy_cost_USD_step'] is None


def test_offline_labelled_history_is_rejected_without_generating_command():
    s=FakeSnapshot();s.legal_history[0]['labels']={'future':True}
    r=api()(s,FakeController(),io_resources={'branch_resource':'new'})
    assert r['status']=='failed_static_preview' and r['error']['type']=='ValueError'
    assert r['command'] is None and s.mother.core.sequence==6


def test_planning_failure_retains_raw_backend_failure_without_zero_result(monkeypatch):
    def fail(core,request):
        core.solver_calls.append({'status':'failed','solver_backend_layers':[{'status':9,'message':'test timeout'}]})
        error=RuntimeError('test planning failed');error.solver_backend_attempts=[{'status':9,'message':'test timeout'}]
        raise error
    monkeypatch.setattr(FakeCore,'generate_command',fail)
    r=api()(FakeSnapshot(),FakeController(),io_resources={'branch_resource':'new'})
    assert r['status']=='failed_static_preview' and r['command'] is None
    assert r['error']['solver_backend_attempts']==[{'status':9,'message':'test timeout'}]
    assert r['new_solver_call_records'][-1]['status']=='failed'
    assert r['actual_energy_cost_USD_step'] is None and r['mother_unchanged']
