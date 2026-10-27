"""Pure serializer and fee export checks; synthetic objects are test fixtures."""
from types import SimpleNamespace
import copy
import importlib

def test_terminal_uses_end_state_without_observation_or_solver():
    terminal = importlib.import_module('tools.model_review_v30.terminal')
    class Forbidden:
        def __call__(self, *args, **kwargs):
            raise AssertionError('terminal must never execute observation/solver')
    state=SimpleNamespace(diesel_status='OFF', diesel_energy_remaining_kWh=300., gt_prev=[120.,0.,0.],last_diesel_kW=0.)
    core=SimpleNamespace(time=90., pending_commands=[{'arrival_minute':105.}], pending_contact_restore=None,
        gt_clocks=[{'mode':'ON','powered_minutes':10.}], gt_start_pending={}, grid_bus_state='GRID',
        state=state, executed={'gt_electric_kW':[200.,0.,0.],'diesel_kW':200.},
        station=SimpleNamespace(boiler_ready=True), ups=SimpleNamespace(energy_kWh=40.),
        cold_store=SimpleNamespace(energy_kWh=28.), solver_calls=[{'phase':'actual'}], observe=Forbidden())
    env=SimpleNamespace(core=core,state=state,decision_snapshot=Forbidden())
    before=copy.deepcopy((vars(state),core.pending_commands,core.solver_calls))
    snapshot=terminal.terminal_snapshot(env,90.)
    assert snapshot['snapshot_time_minute']==90.
    assert snapshot['GT']['actual_electric_kW']==[120.,0.,0.]
    assert snapshot['DG']['actual_electric_kW']==0.
    assert snapshot['DG']['arrived_ceiling_kW']==200.
    assert snapshot['inventories']['cold_store']['stock_kWh']==28.
    assert snapshot['pending_commands']==[{'arrival_minute':105.}]
    assert snapshot['side_effect_check']['unchanged'] is True
    assert before==(vars(state),core.pending_commands,core.solver_calls)
    snapshot['pending_commands'][0]['arrival_minute']=999.
    assert core.pending_commands[0]['arrival_minute']==105.

def test_missing_core_is_unknown_not_zero():
    terminal=importlib.import_module('tools.model_review_v30.terminal')
    value=terminal.terminal_snapshot(SimpleNamespace(core=None),None)
    assert value['completeness']=='unknown_no_live_core'
    assert value['inventories'] is None

def test_unfinished_start_uses_actual_powered_progress_not_ready_timestamp():
    terminal=importlib.import_module('tools.model_review_v30.terminal')
    state=SimpleNamespace(gt_prev=[0.,0.,0.])
    core=SimpleNamespace(time=90.,gt_start_pending={2:{'started':75.,'ready':85.,'requested':20.}},
        _m22_gt_progress={2:3.},solver_calls=[],executed={},ups=SimpleNamespace(energy_kWh=0.),
        cold_store=SimpleNamespace(energy_kWh=0.))
    env=SimpleNamespace(state=state,core=core,execution_contract={'gt_start_delay_minutes':10.},
        config={'gt_ramp_kW_per_hour':120.})
    snap=terminal.terminal_snapshot(env,90.)
    pending=snap['GT']['unfinished_starts'][0]
    assert pending['required_powered_minutes']==10.
    assert pending['actual_powered_minutes']==3.
    assert pending['remaining_powered_minutes']==7.
    assert pending['actually_completed'] is False

def test_dg_timer_and_boiler_powered_work_are_distinct_remaining_requirements():
    terminal=importlib.import_module('tools.model_review_v30.terminal')
    state=SimpleNamespace(gt_prev=[0.,0.,0.],diesel_status='STARTING',diesel_start_minute=80.)
    core=SimpleNamespace(time=90.,gt_start_pending={},solver_calls=[],executed={},
        station=SimpleNamespace(boiler_ready=False,boiler_powered_minutes=.4,
            config={'boiler_restart_minutes':1.}),ups=SimpleNamespace(energy_kWh=0.),cold_store=SimpleNamespace(energy_kWh=0.))
    env=SimpleNamespace(state=state,core=core,sci_config={'diesel_start_delay_minutes':15.})
    snap=terminal.terminal_snapshot(env,90.)
    assert snap['DG']['startup']['remaining_timer_minutes']==5.
    assert snap['DG']['startup']['cranking_energy_kWh'] is None
    assert snap['station']['remaining_restart_powered_minutes']==.6

def test_runner_pure_fee_is_distinct_from_objective_cost():
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from tools.sci_engineering.runner import compact_metrics
    matrix=[[1.,0.,0.] for _ in range(4)];zero=[[0.,0.,0.] for _ in range(4)]
    seg=dict(start_minute=0.,end_minute=60.,hour_of_year=1,cost_USD_step=7.0009,grid_tariff_USD_kWh=.1,
        service_request_ledger=dict(original_demand_kW=matrix,delivered_kW=matrix,active_cut_kW=zero,connected_unserved_kW=zero),
        action=dict(grid_import_kW=10.,diesel_kW=2.,gt_fuel_MMBtu_h=0.,boiler_steam_lbph=0.),
        diesel_local_response=dict(mechanical_work_kWh=3.,electrical_work_kWh=2.,loss_work_kWh=1.,rotor_delta_kWh=0.,fuel_budget_debit_kWh_equivalent=3.,original_electric_fee_USD=.6,shaft_equivalent_fee_USD=.9))
    import pandas as pd
    env=SimpleNamespace(plant=SimpleNamespace(steam_kWh_per_lb=1.,boiler_eff=.8,gas_price_usd_per_mmbtu=4.,ec_electric_kW_per_rt=.7),
        sci_config={'cold_pump_kW_per_kW':.02},hourly=pd.DataFrame([dict(hour_of_year=1,grid_price_usd_per_kWh=.1)]))
    value=compact_metrics(dict(segments=[seg],scenario=dict(duration_steps=4,step_minutes=15.),summary={},m21_initial_storage={}),env=env)
    import pytest
    assert value['economics']['energy_cost_USD']==pytest.approx(7.3009)
    assert value['economics']['actual_fee_USD2001']==pytest.approx(1.9)
