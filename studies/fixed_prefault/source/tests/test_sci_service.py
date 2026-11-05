"""Counterexamples: delivered thermal service needs real auxiliary power."""
from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.model import load_plant
from hospital_ies.resilience import RuntimeState,empty_faults
from tools.operating_response.physical_balance import solve_physical

def case(fault=None):
    plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    import pandas as pd
    row=SimpleNamespace(timestamp=pd.Timestamp('2017-07-10 12:00'),outdoor_temperature_C=25.,
        **{f'P{i}_{k}_kW':(5. if i==0 else 0.) for i in range(4)
           for k in ('fan','chw_pump','heat_pump')})
    loads=np.zeros((4,3));loads[0]=[30.,60.,100.]
    availability=empty_faults()
    if fault:availability[fault]=0.
    state=RuntimeState(np.zeros(3),2400.,set(),np.zeros(3),{})
    return solve_physical(row,plant,loads,availability,state,.25,850.,False,900.,6.,
        {'sci_service_dependencies':True})

@pytest.mark.parametrize('device,carrier',[('AUX_FAN_P0',2),('AUX_CHW_PUMP',2),('AUX_HEAT_PUMP',1)])
def test_failed_necessary_auxiliary_limits_actual_delivery(device,carrier):
    result=case(device)
    assert result['served'][0,carrier] < 1e-4
    assert result['lost'][0,carrier] > 1.

def test_restored_auxiliaries_restore_actual_service_and_power_ledger():
    result=case()
    assert np.max(np.abs(result['lost'])) < 1e-3
    aux=result['service_dependencies']['auxiliary_delivered_kW']
    assert np.asarray(aux).shape==(4,3)
    assert np.sum(aux[0]) <= result['served'][0,0]+1e-4
    assert max(map(abs,result['energy_balance_residuals'])) < 1e-4
