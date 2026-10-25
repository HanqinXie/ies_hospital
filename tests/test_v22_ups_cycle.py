"""UPS supplies current service benefits, not a free fuel-saving cycle."""
import copy
import json
import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_v22_core_environment import make
from test_sci_environment import scenario
from test_engineering_planner import problem
from test_state_coordinated_planner import context
from tools.model_closure_v22.environment import decision_snapshot
from tools.model_closure_v22.policy import SciPolicy,policy_config
from tools.sci_engineering import planner
from tools.operating_response.physical_balance import solve_physical

@pytest.mark.parametrize('source',['GRID','ready_DG'])
def test_live_source_does_not_cycle_UPS_for_fuel_savings(source,tmp_path):
    config=dict(sci_engineering=dict(ups=dict(capacity_kWh=40.,power_kW=160.,initial_soc=1.),diesel_start_delay_minutes=1./6.))
    e=make(config);s=scenario()
    if source=='ready_DG':s.update(faults=dict(GRID=0.,GT_1=0.,GT_2=0.,GT_3=0.),fault_start_minute=0.,fault_duration_minutes=45.)
    e.reset(scenario=s,seed=23)
    # Apply the current t=0 external boundary before taking its observation;
    # DG permission still arrives normally and cannot bypass its start clock.
    e.core.boundary()
    request,_=SciPolicy('C' if source=='GRID' else 'R',plant=e.plant).decide([decision_snapshot(e)])
    e.core.generate_command(request);e.core.advance(4.)
    selected=[x for x in e.segment_history if source=='GRID' or (x['start_minute']>=2. and x['pre_state']['diesel_status']=='CONNECTED')]
    (tmp_path/'actual_short_prefix.json').write_text(json.dumps(dict(source=source,command=e.core.active_command,segments=e.segment_history),default=str),encoding='utf-8')
    assert selected
    assert all(x['action']['grid_import_kW' if source=='GRID' else 'diesel_kW']>1. for x in selected)
    if source=='ready_DG':assert e.core.arrived_dispatch['diesel_kW']==e.plant.emergency_diesel_kW
    discharged=sum(x['m21_storage']['ups']['discharged_kWh'] for x in selected)
    assert discharged<1e-5
    assert all(x['action']['ups_discharge_kW']<1e-4 for x in selected)
    if source=='GRID':
        assert sum(x['m21_storage']['ups']['charged_input_kWh'] for x in selected)<1e-5
        assert e.core.ups.energy_kWh==pytest.approx(40.,abs=1e-5)

def test_current_planner_does_not_substitute_bridge_stock_for_adequate_grid(problem):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][0,0]=100.;p['state'].gt_prev[:]=0.
    p['availability'].update(GT_1=0.,GT_2=0.,GT_3=0.);p['grid_import_limit_kW']=200.
    p['config'].update(_model_closure_v22=dict(ups_energy_kWh=40.,ups_target_kWh=30.),
        _sci_storage_ports=dict(ups_discharge_kW=160.,ups_charge_kW=0.,cold_discharge_kW=0.,cold_charge_kW=0.,cold_pump_kW_per_kW=.02))
    r=planner.solve_joint(**p,coordination_options=policy_config('M'),readiness_context=context(p))
    assert r['planned_current_action']['ups_discharge_kW']<1e-4
    assert r['planned_current_action']['grid_import_kW']==pytest.approx(100.,abs=1e-4)

@pytest.mark.parametrize('critical,general,diesel,stock,target,expected_discharge',[(100.,0.,30.,20.,30.,70.),(100.,80.,100.,40.,30.,80.)])
def test_UPS_still_supplies_real_current_service_benefits(problem,critical,general,diesel,stock,target,expected_discharge):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][0,0]=critical;p['loads'][2,0]=general;p['state'].gt_prev[:]=0.
    p['availability'].update(GRID=0.,GT_1=0.,GT_2=0.,GT_3=0.);p['diesel_start_available']=True;p['dt_h']=1./60.
    p['config'].update(_model_closure_v22=dict(ups_energy_kWh=stock,ups_target_kWh=target),
        _sci_storage_ports=dict(ups_discharge_kW=160.,ups_charge_kW=0.,cold_discharge_kW=0.,cold_charge_kW=0.,cold_pump_kW_per_kW=.02))
    p['execution_contract']=dict(p['execution_contract'],diesel_dispatch_mode='arrived_ceiling')
    action=dict(gt_electric_kW=[0.,0.,0.],grid_import_kW=0.,diesel_kW=diesel,boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.])
    r=solve_physical(**p,execution_action=action)
    assert np.max(r['lost'])<1e-4
    assert r['action']['ups_discharge_kW']==pytest.approx(expected_discharge,abs=1e-4)
