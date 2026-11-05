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

def solve(loads,ports,grid=0.):
    plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    row=SimpleNamespace(timestamp=pd.Timestamp('2017-07-10 12:00'),outdoor_temperature_C=25.)
    f=empty_faults()
    for k in ('GT_1','GT_2','GT_3','EC_1','EC_2','EC_3','ABS','DIESEL','TOWER'):f[k]=0.
    f['GRID']=float(grid>0.)
    state=RuntimeState(np.zeros(3),2400.,set(),np.zeros(3),{})
    return solve_physical(row,plant,np.asarray(loads,float),f,state,.25,grid,False,900.,6.,
        dict(sci_service_dependencies=True,_sci_storage_ports=ports))

def test_ups_is_a_real_finite_electric_port_in_balance():
    loads=np.zeros((4,3));loads[0,0]=10.
    result=solve(loads,dict(ups_discharge_kW=6.,ups_charge_kW=0.))
    assert result['served'][0,0]==pytest.approx(6.,abs=1e-4)
    assert result['action']['ups_discharge_kW']==pytest.approx(6.,abs=1e-4)

def test_cold_store_requires_real_pump_electricity_and_bypasses_failed_tower():
    loads=np.zeros((4,3));loads[0,2]=50.
    ports=dict(cold_discharge_kW=50.,cold_charge_kW=0.,cold_pump_kW_per_kW=.02)
    good=solve(loads,ports,grid=20.)
    assert good['served'][0,2]==pytest.approx(50.,abs=1e-4)
    assert good['action']['grid_import_kW']==pytest.approx(1.,abs=1e-4)
    bad=solve(loads,ports,grid=0.)
    assert bad['served'][0,2]<1e-4

def test_recharging_consumes_actual_grid_energy_after_service():
    loads=np.zeros((4,3));loads[0,0]=10.
    result=solve(loads,dict(ups_discharge_kW=0.,ups_charge_kW=2.),grid=20.)
    assert result['action']['ups_charge_kW']==pytest.approx(2.,abs=1e-4)
    assert result['action']['grid_import_kW']==pytest.approx(12.,abs=1e-4)
    assert max(map(abs,result['energy_balance_residuals']))<1e-4
