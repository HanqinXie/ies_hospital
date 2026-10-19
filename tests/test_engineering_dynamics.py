"""Actuator timing tests using original clamp and a small explicit plant state."""
import copy
import importlib
from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def core():
    try:m=importlib.import_module('tools.engineering_model.execution')
    except ModuleNotFoundError as exc:pytest.fail('Missing engineering dynamics: '+str(exc),pytrace=False)
    from hospital_ies.state_transition import TransitionCore
    from hospital_ies.resilience import empty_faults
    from hospital_ies.model import load_plant
    from hospital_ies.execution import execution_contract
    class SmallCore(m.EngineeringDynamicsMixin,TransitionCore):
        def data_at(self,t):return 1,SimpleNamespace(outdoor_temperature_C=20.),np.ones((4,3))*100.,empty_faults()
    x=object.__new__(SmallCore)
    x.time=0.;x.events=[];x.pending_commands=[];x.sequence=0
    x.env=SimpleNamespace(plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory'),
        config={'gt_ramp_kW_per_hour':900.,'grid_import_limit_kW':350.},dt_minutes=15.,scenario={'grid_limit_kW':350.},
        state=SimpleNamespace(gt_prev=np.zeros(3),last_grid_import_kW=0.,last_diesel_kW=0.,
            diesel_status='OFF',diesel_energy_remaining_kWh=2400.,extra_faulted=set()),
        execution_contract=execution_contract({'gt_start_delay_minutes':10.}),
        engineering_config=m.validate_dynamics({}))
    x.executed=dict(gt_electric_kW=[0.,0.,0.],grid_import_kW=0.,diesel_kW=0.,
        boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.])
    x.gt_start_pending={};x.ramp_time=None;x.ramp_anchor=None;x.ramp_budget=0.
    x.gt_clocks=m.GTClockLedger([0.,0.,0.]);x.interlock_latched={};x._arriving_command=None
    return x


def target(x,power):
    a=copy.deepcopy(x.executed);a['gt_electric_kW'][0]=power
    return a


def test_positive_arrived_command_waits_and_repeated_request_keeps_start_clock():
    x=core();f=x.data_at(0)[3]
    a=x._clamp(target(x,180.),f,normal_ramp=True)
    assert a['gt_electric_kW'][0]==0.
    p=copy.deepcopy(x.gt_start_pending[0]);assert p['started']==0.
    x.time=5.;a=x._clamp(target(x,180.),f,normal_ramp=True)
    assert a['gt_electric_kW'][0]==0.
    assert x.gt_start_pending[0]['started']==0.
    assert x.gt_start_pending[0]['ready']==p['ready']


def test_minimum_up_holds_normal_stop_but_forced_fault_is_immediate():
    x=core();f=x.data_at(0)[3]
    x.gt_clocks.commit([180.,0.,0.],0.);x.executed['gt_electric_kW'][0]=180.
    x.env.state.gt_prev=np.array([180.,0.,0.]);x.time=5.
    held=x._clamp(target(x,0.),f,normal_ramp=True)
    assert held['gt_electric_kW'][0]==180.
    f['GT_1']=0.
    forced=x._clamp(target(x,180.),f,normal_ramp=False)
    assert forced['gt_electric_kW'][0]==0.
    x._commit_sources(forced)
    assert x.gt_clocks.earliest_start(0,5.)==35.


def test_minimum_off_delays_startup_without_pretending_it_is_running():
    x=core();f=x.data_at(0)[3]
    x.gt_clocks.commit([180.,0.,0.],0.)
    x.gt_clocks.commit([0.,0.,0.],1.)
    x.time=5.
    a=x._clamp(target(x,180.),f,normal_ramp=True)
    assert a['gt_electric_kW'][0]==0.
    assert x.gt_start_pending[0]['started']==31.
    assert x.gt_start_pending[0]['ready']>=41.


def test_lockout_cannot_restart_from_held_command_or_clear_true_protection():
    x=core();f=x.data_at(0)[3]
    x.interlock_latched['GT_1']={'reason':'no_export_interlock','latched_minute':0.,'reset_eligible_minute':30.}
    x.env.state.extra_faulted.add('GT_2')
    x.time=60.
    a=x._clamp(target(x,180.),f,normal_ramp=False)
    assert a['gt_electric_kW'][0]==0.
    assert 'GT_1' in x.interlock_latched and 'GT_2' in x.env.state.extra_faulted


def test_rearm_uses_clamped_actual_kw_not_availability_times_requested_kw():
    x=core();f=x.data_at(0)[3];f['GT_1']=.5
    row=x.data_at(0)[1];loads=np.ones((4,3))*20.
    x.data_at=lambda t:(1,row,loads,f)
    cuts=np.zeros((4,3));cuts[3,0]=5.
    x._arriving_command={'id':1,'disconnected':cuts.tolist()}
    x.interlock_latched['GT_1']={'reason':'no_export_interlock','latched_minute':0.,'reset_eligible_minute':30.}
    x.time=60.
    assert not x._rearm_allowed(0,target(x,100.),f)


def test_stress_probe_cannot_write_actual_clock_or_actual_start_events():
    x=core();before=x.gt_clocks.as_dict(0.);events=copy.deepcopy(x.events)
    original=x.env.state
    x._protection=lambda connected,minutes:([],{'GT_1':1.}, {})
    result=x._stress_for_solution(np.ones((4,3)),{'action':target(x,180.)})
    assert result=={'GT_1':1.}
    assert x.env.state is original
    assert x.gt_clocks.as_dict(0.)==before and x.events==events


def test_zero_startup_sensitivity_still_enforces_minimum_off():
    x=core();f=x.data_at(0)[3]
    x.env.engineering_config['gt_start_delay_minutes']=0.
    x.env.execution_contract['gt_start_delay_minutes']=0.
    x.gt_clocks.commit([180.,0.,0.],0.);x.gt_clocks.commit([0.,0.,0.],1.)
    x.time=5.
    result=x._clamp(target(x,180.),f,normal_ramp=True)
    assert result['gt_electric_kW'][0]==0.
    assert x.gt_start_pending[0]['started']==31.


def test_zero_startup_waiting_start_is_cancelled_by_later_arrived_stop():
    x=core();f=x.data_at(0)[3]
    x.env.engineering_config['gt_start_delay_minutes']=0.
    x.env.execution_contract['gt_start_delay_minutes']=0.
    x.gt_clocks.commit([180.,0.,0.],0.);x.gt_clocks.commit([0.,0.,0.],1.)
    x.time=5.;x._clamp(target(x,180.),f,normal_ramp=True)
    assert 0 in x.gt_start_pending
    x.time=10.;stopped=x._clamp(target(x,0.),f,normal_ramp=True)
    assert stopped['gt_electric_kW'][0]==0. and 0 not in x.gt_start_pending


def test_normal_down_request_uses_stable_floor_then_stops_without_false_trip():
    x=core();f=x.data_at(0)[3]
    from tools.engineering_model.execution import GTClockLedger
    x.executed['gt_electric_kW'][0]=300.;x.env.state.gt_prev=np.array([300.,0.,0.])
    x.gt_clocks=GTClockLedger([300.,0.,0.]);x.time=15.
    first=x._clamp(target(x,0.),f,normal_ramp=True)
    assert 100.<first['gt_electric_kW'][0]<300.
    x.executed=first;x._commit_sources(first);x.time=30.
    stopped=x._clamp(target(x,0.),f,normal_ramp=True)
    assert stopped['gt_electric_kW'][0]==0.
    assert not x.env.state.extra_faulted and not x.interlock_latched


@pytest.mark.parametrize('bad',[-1.,float('nan'),float('inf')])
def test_dynamic_parameters_reject_undefined_time(bad):
    x=core()
    from tools.engineering_model.execution import validate_dynamics
    with pytest.raises(ValueError):validate_dynamics({'minimum_up_minutes':bad})
