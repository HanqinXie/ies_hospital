"""Focused T06-shaped regression; pure arithmetic, no reset/trajectory."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.model import load_plant,RT_TO_KW,KWH_PER_MMBTU,illustrative_tariff
from hospital_ies.resilience import empty_faults
from tools.noopt_unattended_20261104.forward import evaluate_forward
from tools.noopt_unattended_20261104.runtime import forbid_optimization


def t06_shape(mode):
    # Observed T06_U_attempt01 t60-61 shape: GT/HRSG0, cold reference retained,
    # tower hard outage, boiler2418.3881276, useful heat93.0709144kW.
    # Other rows here are synthetic; this is not replayed scientific evidence.
    plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    row=SimpleNamespace(outdoor_temperature_C=25.,timestamp=pd.Timestamp('2020-01-01'))
    loads=np.array([[80.,20.,300.],[80.,20.,300.],[100.,20.,450.],[443.,33.07091439896391,450.]])
    action=dict(gt_electric_kW=[0.,0.,0.],grid_import_kW=847.775604873894,diesel_kW=0.,
        boiler_steam_lbph=2418.38812756492,absorption_cooling_kW=378.0770997847257,
        electric_chiller_cooling_kW=[696.1294339426163,0.,0.])
    reference=deepcopy(action);reference['_steam_abs_fraction']=.8668317020695805
    faults=empty_faults()
    if mode=='tower_trip':faults['TOWER']=0.
    if mode=='no_cold_demand':loads[:,2]=0.
    if mode=='bounded_running':action['boiler_steam_lbph']=reference['boiler_steam_lbph']=120.
    return dict(row=row,plant=plant,loads=loads,availability=faults,execution_action=action,
        reference=reference,context=dict(station_ac_available=True,boiler_ready=True,
            boiler_fixed_kW=.5,boiler_kW_per_kWsteam=.012,gt_start_kW=0.,boiler_restart_kW=0.),
        stores={name:dict(energy_kWh=0.,capacity_kWh=0.,power_kW=0.) for name in ('ups','cold')},
        dt_h=1./60.,config={'grid_import_limit_kW':850.,'cold_pump_kW_per_kW':.02},
        execution_contract={'diesel_mode':'emergency_bus','diesel_allowed_priorities':['P0','P1']})


@pytest.mark.parametrize('mode',['tower_trip','no_cold_demand','bounded_running'])
def test_actual_useful_steam_closes_boiler_pressure_without_optimization(mode):
    p=t06_shape(mode);original=deepcopy(p['execution_action']);reference=deepcopy(p['reference'])
    guard=SimpleNamespace(time=60.,env=SimpleNamespace(minute1_variant='unattended',dt_minutes=1.),_noopt_optimizer_attempts=[])
    with forbid_optimization(guard):result=evaluate_forward(**p)
    a=result['action'];plant=p['plant']
    useful=float(result['served'][:,1].sum())/(plant.steam_kWh_per_lb*plant.exchanger_eff)
    useful+=a['absorption_cooling_kW']/RT_TO_KW*plant.abs_steam_lb_per_rt
    assert a['boiler_steam_lbph']<=useful+1e-7
    assert result['unused_steam_lbph']<=a['hrsg_steam_lbph']+1e-7
    assert 0.<=a['boiler_steam_lbph']<=original['boiler_steam_lbph']
    assert a['boiler_auxiliary_kW']==pytest.approx(.5+.012*plant.steam_kWh_per_lb*a['boiler_steam_lbph'] if a['boiler_steam_lbph']>0. else 0.,abs=1e-7)
    expected=(a['grid_import_kW']*illustrative_tariff(p['row'].timestamp,plant)+a['boiler_steam_lbph']*plant.steam_kWh_per_lb/plant.boiler_eff/KWH_PER_MMBTU*plant.gas_price_usd_per_mmbtu)*p['dt_h']
    assert result['cost_USD_step']==pytest.approx(expected,abs=1e-7)
    assert max(map(abs,result['energy_balance_residuals']))<1e-4
    assert abs(result['steam_balance_residual_lbph'])<1e-4
    assert not guard._noopt_optimizer_attempts and result['dispatch_optimizer_calls']==0
    assert p['execution_action']==original and p['reference']==reference
    assert a['electric_chiller_cooling_kW'][1:]==[0.,0.]
    if mode=='tower_trip':assert a['absorption_cooling_kW']==0. and sum(a['electric_chiller_cooling_kW'])==0.
    if mode=='bounded_running':assert a['boiler_steam_lbph']==pytest.approx(120.,abs=1e-7)
