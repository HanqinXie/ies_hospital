"""Extra sensible recovery cold requires its own actual fan/pump energy."""
import copy
import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_engineering_planner import problem
from tools.operating_response.physical_balance import solve_physical

def solve_recovery(problem,grid,fan=1.):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][0,0]=10.;p['state'].gt_prev[:]=0.
    p['availability'].update(GT_1=0.,GT_2=0.,GT_3=0.,ABS=0.,AUX_FAN_P0=fan)
    p['config']['_model_closure_v22']=dict(recovery_cooling_request_kW=20.,recovery_pump_kW_per_kW=.02,recovery_fan_kW_per_kW=.1)
    p['execution_contract']=dict(p['execution_contract'],diesel_dispatch_mode='arrived_ceiling')
    a=dict(gt_electric_kW=[0.,0.,0.],grid_import_kW=grid,diesel_kW=0.,boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[100.,0.,0.])
    return solve_physical(**p,execution_action=a)

def test_recovery_cannot_borrow_nominal_fan_availability_without_electricity(problem):
    assert solve_recovery(problem,10.)['action']['recovery_cooling_kW']<1e-5
    powered=solve_recovery(problem,100.)
    assert powered['action']['recovery_cooling_kW']==pytest.approx(20.,abs=1e-5)
    assert powered['action']['recovery_fan_electric_kW']==pytest.approx(2.,abs=1e-5)
    assert powered['action']['recovery_pump_electric_kW']==pytest.approx(.4,abs=1e-5)
    assert max(map(abs,powered['energy_balance_residuals']))<1e-4
    assert solve_recovery(problem,100.,fan=0.)['action']['recovery_cooling_kW']<1e-5

def test_active_recovery_rejects_missing_actual_fan_power_registration():
    from tools.model_closure_v22.physical import current_context
    with pytest.raises(ValueError,match='fan'):
        current_context({'_model_closure_v22':dict(recovery_cooling_request_kW=10.)})
