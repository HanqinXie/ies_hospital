"""Actual terminal balances: bridge stock and station electricity are distinct."""
from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.model import load_plant
from hospital_ies.resilience import RuntimeState,empty_faults
from tools.operating_response.physical_balance import solve_physical

def terminal(*,diesel=200.,grid=0.,electric=100.,heat=0.,general=0.,ups=160.,station=True,bridge=True,starter=0.,gt=0.):
    plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    row=SimpleNamespace(timestamp=pd.Timestamp('2017-07-10 12:00'),outdoor_temperature_C=25.)
    f=empty_faults()
    for k in ('GT_1','GT_2','GT_3','ABS','EC_1','EC_2','EC_3'):f[k]=0.
    if gt:f['GT_1']=1.
    f['GRID']=float(grid>0.);f['DIESEL']=float(diesel>0.)
    state=RuntimeState(np.array([gt,0.,0.]),2400.,set(),np.zeros(3),{})
    loads=np.zeros((4,3));loads[0]=[electric,heat,0.];loads[2,0]=general
    cfg=dict(sci_service_dependencies=True,_sci_storage_ports=dict(ups_discharge_kW=ups,ups_charge_kW=0.),
        _model_closure_v22=dict(station_ac_available=station,boiler_ready=True,
            boiler_fixed_kW=.5,boiler_kW_per_kWsteam=.012,gt_start_kW=starter,
            ups_energy_kWh=20.,ups_target_kWh=30.,ups_discharge_efficiency=.95,
            preserve_bridge=bridge,recovery_cooling_request_kW=0.,recovery_pump_kW_per_kW=.02))
    action=dict(gt_electric_kW=[gt,0.,0.],grid_import_kW=grid,diesel_kW=diesel,
        boiler_steam_lbph=1000. if heat>0. else 0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.])
    contract=dict(diesel_mode='legacy_explicit_permission',diesel_dispatch_mode='arrived_ceiling',grid_import_mode='arrived_ceiling')
    return solve_physical(row,plant,loads,f,state,.25,grid,diesel>0.,900.,6.,cfg,
        execution_action=action,execution_contract=contract)

def test_ready_diesel_does_not_drain_below_existing_bridge_stock_for_fuel_saving():
    r=terminal(general=40.)
    assert np.max(r['lost'])<1e-4
    assert r['action']['ups_discharge_kW']<1e-4
    assert r['action']['diesel_kW']==pytest.approx(140.,abs=1e-4)

def test_present_critical_shortage_can_use_inventory_below_bridge_target():
    r=terminal(diesel=30.)
    assert r['lost'][0,0]<1e-4
    assert r['action']['ups_discharge_kW']==pytest.approx(70.,abs=1e-4)

def test_preserving_bridge_is_an_explicit_general_service_tradeoff():
    r=terminal(diesel=100.,general=80.)
    assert r['lost'][0,0]<1e-4
    assert r['action']['ups_discharge_kW']<1e-4
    assert r['lost'][2,0]==pytest.approx(80.,abs=1e-4)

def test_building_ups_cannot_power_boiler_station_or_manufacture_steam():
    r=terminal(diesel=0.,electric=10.,heat=40.)
    assert r['served'][0,0]==pytest.approx(10.,abs=1e-4)
    assert r['action']['boiler_steam_lbph']<1e-4
    assert r['served'][0,1]<1e-4

def test_real_station_source_pays_boiler_auxiliary_once_in_actual_balance():
    r=terminal(diesel=0.,grid=500.,electric=10.,heat=40.,ups=0.)
    assert r['served'][0,1]==pytest.approx(40.,abs=1e-4)
    expected=.5+.012*r['action']['boiler_steam_lbph']*.30
    assert r['action']['grid_import_kW']==pytest.approx(10.+expected,abs=1e-4)
    assert r['action']['boiler_auxiliary_kW']==pytest.approx(expected,abs=1e-4)
    assert max(map(abs,r['energy_balance_residuals']))<1e-4

def test_lost_station_feeder_stops_steam_even_when_building_grid_is_live():
    r=terminal(diesel=0.,grid=500.,electric=10.,heat=40.,ups=0.,station=False)
    assert r['served'][0,0]==pytest.approx(10.,abs=1e-4)
    assert r['served'][0,1]<1e-4

def test_unpowered_gt_start_is_rejected_without_failing_building_ups_service():
    r=terminal(diesel=0.,electric=10.,starter=10.)
    assert r['served'][0,0]==pytest.approx(10.,abs=1e-4)
    assert r['action']['gt_start_electric_kW']==0.

def test_powered_gt_start_is_paid_from_source_side():
    r=terminal(diesel=0.,grid=100.,electric=10.,starter=10.,ups=0.)
    assert r['action']['gt_start_electric_kW']==pytest.approx(10.,abs=1e-4)
    assert r['action']['grid_import_kW']==pytest.approx(20.,abs=1e-4)

def test_zero_boiler_setting_cannot_be_a_half_kw_dummy_surplus_sink():
    from hospital_ies.execution import ModelConstraintInfeasible
    with pytest.raises(ModelConstraintInfeasible):
        terminal(diesel=0.,electric=127.5,ups=0.,gt=128.)

def test_pre_ready_boiler_uses_real_restart_power_without_steam():
    from tools.model_closure_v22.physical import current_context
    ctx=current_context({'_model_closure_v22':dict(boiler_ready=False,boiler_restart_kW=.5)})
    assert ctx['boiler_restart_kW']==.5

def test_held_boiler_is_not_stopped_only_to_save_fuel_when_source_can_supply_it():
    r=terminal(gt=128.,grid=500.,electric=140.,heat=40.,ups=0.)
    assert r['action']['boiler_steam_lbph']==pytest.approx(1000.,abs=1e-4)
    assert r['action']['boiler_auxiliary_kW']==pytest.approx(4.1,abs=1e-4)
