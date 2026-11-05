"""Actual versus arrived DG ports; speculative solves cannot advance mechanics."""
import importlib,copy,sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def core():
    try:m=importlib.import_module('tools.operating_response.execution')
    except ModuleNotFoundError as e:pytest.fail('Missing M15 execution: '+str(e),pytrace=False)
    from tools.engineering_model.environment import DynamicEngineeringCore
    class Small(m.LocalResponseMixin,DynamicEngineeringCore):pass
    x=object.__new__(Small)
    x.gt_clocks=None;x.time=0.;x.events=[]
    x.env=SimpleNamespace(state=SimpleNamespace(gt_prev=np.zeros(3),last_diesel_kW=20.,last_grid_import_kW=0.),
        execution_contract={'grid_import_mode':'arrived_ceiling'},
        plant=SimpleNamespace(emergency_diesel_kW=900.))
    a=dict(gt_electric_kW=[0.,0.,0.],grid_import_kW=0.,diesel_kW=800.)
    x.executed=copy.deepcopy(a)
    return x,a

def test_arrived_diesel_ceiling_is_not_actual_electrical_supply():
    x,a=core();x._commit_sources(a,actual=False)
    assert x.state.last_diesel_kW==20.
    a['diesel_kW']=0.;x._commit_sources(a,actual=False)
    assert x.state.last_diesel_kW==0.

def test_true_terminal_solution_commits_actual_diesel_port():
    x,a=core();a['diesel_kW']=250.;x._commit_sources(a,actual=True)
    assert x.state.last_diesel_kW==250.

def test_preview_mechanics_does_not_consume_fuel_or_modify_rotor():
    x,a=core()
    from tools.operating_response.diesel import DieselRotor
    x.diesel_rotor=DieselRotor();x.diesel_rotor.spin_up(900.,0.,1.)
    x.env.state.diesel_status='CONNECTED';x.env.state.diesel_start_minute=0.
    x.env.config={'diesel_start_delay_minutes':15.}
    x.env.state.diesel_energy_remaining_kWh=2400.
    before=copy.deepcopy(x.diesel_rotor.as_dict())
    trial,receipt=x.preview_diesel(1.,200.,connected=True)
    assert x.diesel_rotor.as_dict()==before
    assert x.state.diesel_energy_remaining_kWh==2400.
    assert receipt['electrical_work_kWh']>0.
    assert trial.as_dict()!=before

def test_native_event_clock_path_retains_normal_and_start_boundaries():
    x,a=core();x.pending_commands=[];x.gt_start_pending={}
    x.pending_shed=None;x.local_down=None;x.grid_outage_start=None
    x.env.scenario={'duration_steps':40,'step_minutes':15.,'stage':4,
        'fault_start_minute':95.,'fault_duration_minutes':240.,
        'shock_start_minute':95.,'shock_duration_minutes':180.}
    x.env.config={'diesel_start_delay_minutes':15.}
    x.env.state.diesel_start_minute=None
    assert x._next_boundary(15.)==15.
    x.gt_start_pending={0:{'started':5.,'ready':15.,'requested':132.}}
    assert x._next_boundary(20.)==5.

def test_average_gt_ramp_cannot_be_called_a_resolved_diesel_speed_path():
    x,a=core()
    from tools.operating_response.execution import LocalResponseDomainError
    with pytest.raises(LocalResponseDomainError):
        x.validate_electrical_path([200.,0.,0.],[195.,0.,0.],True,250.)
    x.validate_electrical_path([200.,0.,0.],[195.,0.,0.],False,0.)
    x.validate_electrical_path([200.,0.,0.],[200.,0.,0.],True,250.)

def test_actual_hospital_step_runs_native_clock_with_no_free_diesel_energy():
    from tools.operating_response.environment import OperatingHospitalIESEnv
    from tools.operating_response.data_design import normal_proposal,SEALED_DAYS
    scene=copy.deepcopy(normal_proposal()['scenarios'][0]);scene['duration_steps']=1
    first=scene['start_hour']
    env=OperatingHospitalIESEnv(ROOT,allowed_hours=[first,first+1],sealed_days=SEALED_DAYS,
        model_id='M15_local_response')
    env.reset(scenario=scene,seed=20261017)
    *_,info=env.step({'targets':{},'security':{}})
    assert env.core.time==15.
    assert info['segments']
    assert env.state.last_diesel_kW==0.
    assert env.core.diesel_rotor.rotor_energy_kWs==0.
    for s in info['segments']:
        assert max(map(abs,s['energy_balance_residuals']))<1e-4
        assert abs(s['diesel_local_response']['energy_residual_kWh'])<1e-7

def test_actual_startup_consumes_budget_before_connected_diesel_forms_supply():
    from tools.operating_response.environment import OperatingHospitalIESEnv
    from tools.operating_response.data_design import normal_proposal,SEALED_DAYS
    scene=copy.deepcopy(normal_proposal()['scenarios'][0]);scene['duration_steps']=2
    scene.update(stage=1,faults={'GRID':0.,'GT_1':0.,'GT_2':0.,'GT_3':0.},
        fault_start_minute=0.,fault_duration_minutes=120.,response_delay_minutes=0.)
    first=scene['start_hour']
    env=OperatingHospitalIESEnv(ROOT,allowed_hours=[first,first+1],sealed_days=SEALED_DAYS,
        model_id='M15_local_response')
    env.reset(scenario=scene,seed=20261017)
    initial=float(env.state.diesel_energy_remaining_kWh)
    *_,info=env.step({'targets':{},'security':{}})
    assert all(s['action']['diesel_kW']==0. for s in info['segments'])
    spent=sum(s['diesel_local_response']['mechanical_work_kWh'] for s in info['segments'])
    assert spent>0.
    assert initial-env.state.diesel_energy_remaining_kWh==pytest.approx(spent,abs=1e-7)
    assert env.core.diesel_rotor.rotor_energy_kWs>0.
    assert env.state.diesel_status=='CONNECTED' and env.state.last_diesel_kW==0.
    *_,info2=env.step({'targets':{},'security':{}})
    assert any(s['action']['diesel_kW']>0. for s in info2['segments'])
    assert all(abs(s['diesel_local_response']['energy_residual_kWh'])<1e-6 for s in info2['segments'])

def test_actual_partial_startup_preserves_the_fuel_exhaustion_boundary_reason():
    from tools.operating_response.environment import OperatingHospitalIESEnv
    from tools.operating_response.data_design import normal_proposal,SEALED_DAYS
    from tools.operating_response.execution import LocalResponseDomainError
    scene=copy.deepcopy(normal_proposal()['scenarios'][0]);scene['duration_steps']=1
    scene.update(stage=1,faults={'GRID':0.,'GT_1':0.,'GT_2':0.,'GT_3':0.},
        fault_start_minute=0.,fault_duration_minutes=120.,response_delay_minutes=0.)
    first=scene['start_hour']
    env=OperatingHospitalIESEnv(ROOT,allowed_hours=[first,first+1],sealed_days=SEALED_DAYS,
        model_id='M15_local_response')
    env.reset(scenario=scene,seed=20261017)
    env.state.diesel_energy_remaining_kWh=1.
    with pytest.raises(LocalResponseDomainError):env.step({'targets':{},'security':{}})
    assert 0.<env.core.time<15.
    receipts=[s['diesel_local_response'] for s in env.core.segments]
    assert receipts[-1].get('fuel_budget_domain_exhausted') is True
    assert sum(r['mechanical_work_kWh'] for r in receipts)==pytest.approx(1.,abs=1e-7)
    assert all(s['action']['diesel_kW']==0. for s in env.core.segments)
