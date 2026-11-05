"""Actual M22 adapter contracts and absence of schedule from current snapshot."""
import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_sci_environment import scenario

def make(config=None):
    from tools.model_closure_v22.environment import OperatingHospitalIESEnv
    import json
    authority=json.loads((ROOT/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json').read_text(encoding='utf-8'))
    sealed=[int(x['time_block_id'].rsplit('_',1)[1]) for x in authority['p2b_time_blocks'] if x['reserved_only']]
    return OperatingHospitalIESEnv(ROOT,allowed_hours=[4001,4002],sealed_days=sealed,model_config=config or {})

def test_repeated_events_change_only_current_availability_and_snapshot_has_no_schedule():
    from tools.model_closure_v22.environment import decision_snapshot
    e=make();s=scenario();s['model_closure_events']=[dict(time_minute=5.,availability={'STATION_AC':0.}),dict(time_minute=10.,availability={'STATION_AC':1.})]
    e.reset(scenario=s,seed=1)
    assert e.core._hardware_data_at(6.)[-1]['STATION_AC']==0.
    assert e.core._hardware_data_at(11.)[-1]['STATION_AC']==1.
    snap=decision_snapshot(e)
    assert 'model_closure_events' not in str(snap)
    assert snap['engineering_execution_model_id']=='M22_model_closure'

def test_storage_and_station_probes_do_not_commit_inventory_or_start_progress():
    e=make(dict(sci_engineering=dict(ups=dict(capacity_kWh=40.,power_kW=160.,initial_soc=.5),cold_store=dict(capacity_kWh=100.,power_kW=100.,initial_soc=.5))))
    e.reset(scenario=scenario(),seed=2);c=e.core
    before=(c.ups.energy_kWh,c.cold_store.energy_kWh,c.station.observe())
    _,_,loads,f=c.data_at(0.)
    for _ in range(2):c._physical_solver_config(.25,f)
    assert before==(c.ups.energy_kWh,c.cold_store.energy_kWh,c.station.observe())

def test_default_proxy_is_isolated_and_dc_qualifications_are_observable():
    e=make();e.reset(scenario=scenario(),seed=3)
    assert not e.scenario['enable_propagation']
    assert e.core.observe()['model_closure_v22']['station']['diesel_starts_remaining']==2

def test_actual_boiler_restore_pays_continuous_restart_before_steam():
    from tools.model_closure_v22.environment import decision_snapshot
    from tools.model_closure_v22.policy import SciPolicy
    e=make();s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.,faults={f'GT_{i}':0. for i in (1,2,3)},
        model_closure_events=[dict(time_minute=0.,availability={'STATION_AC':0.}),dict(time_minute=.5,availability={'STATION_AC':1.})])
    e.reset(scenario=s,seed=7)
    request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
    e.core.generate_command(request);e.core.advance(2.)
    segments=e.segment_history
    assert all(x['action']['boiler_steam_lbph']<1e-4 for x in segments if x['start_minute']<1.5-1e-8)
    assert any(x['action']['boiler_steam_lbph']>1. for x in segments if x['start_minute']>=1.5-1e-8)
    restart=sum(x['action'].get('boiler_restart_electric_kW',0.)*(x['end_minute']-x['start_minute'])/60. for x in segments)
    assert restart==pytest.approx(.5/60.,abs=1e-7)
    assert all(max(map(abs,x['energy_balance_residuals']))<1e-4 for x in segments)
    for x in segments:
        station=x['m22_station'];a=x['action']
        if x['start_minute']<.5-1e-8:
            assert station['boiler_execution_status']=='station_path_lost'
            assert a['boiler_auxiliary_kW']==0. and a['boiler_restart_electric_kW']==0.
        elif x['start_minute']<1.5-1e-8:
            assert station['boiler_ready_before_actual_interval'] is False
            assert a['boiler_restart_electric_kW']==pytest.approx(.5)
            assert a['boiler_steam_lbph']<1e-4
            assert station['boiler_execution_status']==('restart_completed' if station['boiler_ready'] else 'restarting')
        else:
            assert station['boiler_execution_status']=='running'
            assert station['boiler_ready_before_actual_interval'] and station['boiler_ready']
            assert a['boiler_auxiliary_kW']>0. and a['boiler_steam_lbph']>0.

@pytest.mark.parametrize('failure',['healthy','pump','fan','empty'])
def test_healthy_grid_cold_deficit_uses_real_storage_and_faults_block_delivery(failure):
    from tools.model_closure_v22.environment import decision_snapshot
    from tools.model_closure_v22.policy import SciPolicy
    e=make(dict(sci_engineering=dict(cold_store=dict(capacity_kWh=100.,power_kW=100.,initial_soc=0. if failure=='empty' else 1.))))
    faults={f'GT_{i}':0. for i in (1,2,3)};faults.update(ABS=0.,EC_1=0.,EC_2=0.,EC_3=0.,TOWER=.5)
    s=scenario();s.update(faults=faults,fault_start_minute=0.,fault_duration_minutes=45.)
    if failure=='pump':s['auxiliary_faults']={'AUX_CHW_PUMP':0.}
    if failure=='fan':s['auxiliary_faults']={'AUX_FAN':0.}
    e.reset(scenario=s,seed=9);request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
    before=e.core.cold_store.energy_kWh;e.core.generate_command(request);e.core.advance(1.)
    delivered=sum(x['action'].get('cold_discharge_kW',0.)*(x['end_minute']-x['start_minute'])/60. for x in e.segment_history)
    if failure=='healthy':
        assert delivered>.1 and e.core.cold_store.energy_kWh<before
        assert any(x['action']['cold_storage_pump_kW']>0. for x in e.segment_history)
    else:assert delivered<1e-5

def test_online_GT_stops_at_explicit_finite_control_DC_expiry():
    from tools.model_closure_v22.environment import decision_snapshot
    from tools.model_closure_v22.policy import SciPolicy
    e=make(dict(model_closure_v22=dict(station=dict(control_autonomy_minutes=.5))))
    e.reset(scenario=scenario(),seed=8);request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
    e.core.generate_command(request);e.core.advance(1.)
    assert any(sum(x['action']['gt_electric_kW'])>1. for x in e.segment_history if x['start_minute']<.5)
    assert all(sum(x['action']['gt_electric_kW'])==0. for x in e.segment_history if x['start_minute']>=.5)
    assert e.core.observe()['model_closure_v22']['station']['GT_control_DC_available']==0.

def test_building_UPS_cannot_complete_an_unpowered_cold_GT_start():
    from tools.model_closure_v22.environment import decision_snapshot
    from tools.model_closure_v22.policy import SciPolicy
    from tools.engineering_model.execution import GTClockLedger
    e=make(dict(sci_engineering=dict(ups=dict(capacity_kWh=40.,power_kW=160.,initial_soc=1.))))
    s=scenario();s.update(faults={'GRID':0.,'DIESEL':0.},fault_start_minute=0.,fault_duration_minutes=45.)
    e.reset(scenario=s,seed=11);c=e.core
    # Explicit constructed cold initial condition: no inherited warm unit.
    c.executed['gt_electric_kW']=[0.,0.,0.];c.state.gt_prev[:]=0.;c.gt_clocks=GTClockLedger([0.,0.,0.])
    request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
    c.generate_command(request);c.advance(12.)
    assert c.gt_start_pending
    assert all(sum(x['action']['gt_electric_kW'])==0. and x['action']['gt_start_electric_kW']==0. for x in e.segment_history)
    assert all(value==0. for value in c._m22_gt_progress.values())

@pytest.mark.parametrize('alias',['M','C'])
def test_joint_generated_command_reaches_short_actual_execution(alias):
    from tools.model_closure_v22.environment import decision_snapshot
    from tools.model_closure_v22.policy import SciPolicy
    e=make();e.reset(scenario=scenario(),seed=13)
    request,_=SciPolicy(alias,plant=e.plant).decide([decision_snapshot(e)])
    e.core.generate_command(request);e.core.advance(1.)
    assert e.segment_history and e.core.time==1.
    assert all(max(map(abs,x['energy_balance_residuals']))<1e-4 for x in e.segment_history)

def test_grid_return_proof_uses_current_station_and_inventory_not_old_config(monkeypatch):
    from hospital_ies.execution import ModelConstraintInfeasible
    from tools.execution_recovery import execution as inherited
    e=make(dict(sci_engineering=dict(ups=dict(capacity_kWh=40.,power_kW=160.,initial_soc=1.))))
    s=scenario();s['model_closure_events']=[dict(time_minute=0.,availability={'STATION_AC':0.})]
    e.reset(scenario=s,seed=15);e.core.ups.energy_kWh=0.
    e.config['_model_closure_v22']={'station_ac_available':True}
    e.config['_sci_storage_ports']={'ups_discharge_kW':160.}
    observed=[]
    def candidate(*args,**kwargs):
        observed.append(args[10]);raise ModelConstraintInfeasible('registered test candidate')
    monkeypatch.setattr(inherited,'solve_physical',candidate)
    assert not e.core._grid_return_ready(e.core._hardware_data_at(0.)[-1])
    assert observed and not observed[0]['_model_closure_v22']['station_ac_available']
    assert observed[0]['_sci_storage_ports']['ups_discharge_kW']==0.
    assert e.config['_model_closure_v22']['station_ac_available'] is True

def test_infeasible_held_mode_probe_does_not_abort_an_ordinary_command(monkeypatch):
    from hospital_ies.execution import ModelConstraintInfeasible
    from tools.model_closure_v22 import environment as module
    from tools.model_closure_v22.policy import SciPolicy
    e=make(dict(sci_engineering=dict(ups=dict(capacity_kWh=40.,power_kW=160.,initial_soc=1.))));e.reset(scenario=scenario(),seed=15)
    request,_=SciPolicy('R',plant=e.plant).decide([module.decision_snapshot(e)])
    # A new boundary invalidates the preceding snapshot's pure mode cache.
    e.core.time=.1
    def rejected(*args,**kwargs):raise ModelConstraintInfeasible('one held mode is not the whole legal command domain')
    monkeypatch.setattr(module,'solve_physical',rejected)
    e.core.generate_command(request)
    assert not e.core.failed and not e.core.errors
    assert e.core.pending_commands
    assert any(x['phase']=='m22_current_port_selection' and x['status']=='candidate_infeasible' for x in e.core.solver_calls)

def test_explicit_bridge_off_does_not_reintroduce_stock_protection_during_mode_comparison():
    e=make(dict(sci_engineering=dict(ups=dict(capacity_kWh=40.,power_kW=160.,initial_soc=.5)),model_closure_v22=dict(storage=dict(preserve_bridge=False))))
    e.reset(scenario=scenario(),seed=17);c=e.core;c.grid_bus_state='ISLAND';c.state.diesel_status='CONNECTED'
    _,_,loads,f=c.data_at(0.);loads[:]=0.;loads[0,0]=100.;loads[2,0]=80.
    f.update(GRID=0.,GT_1=0.,GT_2=0.,GT_3=0.)
    action=dict(c.executed,gt_electric_kW=[0.,0.,0.],grid_import_kW=0.,diesel_kW=100.1,boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.])
    result=c._solve(loads,f,.1,'mode_switch_test',{},execution_action=action)
    assert result['lost'][2,0]<1e-4
    assert result['action']['ups_discharge_kW']>79.
