"""Real joint balances: a new arrived DG ceiling is not a free power sink."""
import importlib
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
from hospital_ies.execution import ModelConstraintInfeasible,execution_contract

def solve(load=200.,ceiling=800.,*,local=True,available=True,gt=0.,energy=2400.):
    try:fn=importlib.import_module('tools.operating_response.physical_balance').solve_physical
    except ModuleNotFoundError as exc:pytest.fail('Missing finite-response port balance: '+str(exc),pytrace=False)
    p=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    f=empty_faults();f['GRID']=0.
    for i in (1,2,3):f[f'GT_{i}']=1. if i==1 and gt else 0.
    s=RuntimeState(np.array([gt,0.,0.]),energy,set(),np.zeros(3),{})
    a=dict(gt_electric_kW=[gt,0.,0.],grid_import_kW=0.,diesel_kW=ceiling,
        boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.])
    d=np.zeros((4,3));d[0,0]=load
    c=execution_contract({})
    if local:c['diesel_dispatch_mode']='arrived_ceiling'
    return fn(SimpleNamespace(timestamp=pd.Timestamp('2020-01-01'),outdoor_temperature_C=20.),
        p,d,f,s,.25,850.,available,900.,6.,{},execution_action=a,execution_contract=c)

def test_fixed_diesel_oversupply_still_infeasible_in_old_contract():
    with pytest.raises(ModelConstraintInfeasible):solve(local=False)

def test_new_port_follows_only_real_current_connected_load_below_arrived_cap():
    r=solve()
    assert r['action']['diesel_kW']==pytest.approx(200.,abs=.001)
    assert np.max(np.abs(r['energy_balance_residuals']))<1e-5
    assert r['lost'][0,0]<.001

def test_no_diesel_permission_or_unarrived_higher_request_creates_no_supply():
    r=solve(available=False)
    assert r['action']['diesel_kW']==0.
    assert r['lost'][0,0]==pytest.approx(200.,abs=.001)
    r=solve(ceiling=150.)
    assert r['action']['diesel_kW']==pytest.approx(150.,abs=.001)
    assert r['lost'][0,0]==pytest.approx(50.,abs=.001)

def test_new_diesel_response_cannot_absorb_surplus_gt_or_disconnected_load():
    with pytest.raises(ModelConstraintInfeasible):solve(load=100.,gt=250.,ceiling=800.)

def test_terminal_cap_does_not_spread_shaft_budget_across_a_trial_interval():
    # Mechanical integration must truncate the physical interval at exhaustion;
    # the terminal algebraic model cannot preemptively average Pe to 40kW.
    r=solve(energy=10.)
    assert r['action']['diesel_kW']==pytest.approx(200.,abs=.001)
