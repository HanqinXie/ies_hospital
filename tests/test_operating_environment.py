"""Model identity and only lawful readiness observations, no annual fixture."""
import importlib,sys,copy
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def module():
    try:return importlib.import_module('tools.operating_response.environment')
    except ModuleNotFoundError as e:pytest.fail('Missing versioned operating environment: '+str(e),pytrace=False)

def test_m14_contract_and_identity_remain_exact():
    from tools.engineering_model.environment import model_contract
    assert module().model_contract('M14_engineering',{})==model_contract('M14_engineering',{})

def test_m15_is_explicit_and_does_not_change_capacity_or_connection_permissions():
    m=module();old,_=m.model_contract('M14_engineering',{})
    new,c=m.model_contract('M15_local_response',{})
    assert new['diesel_dispatch_mode']=='arrived_ceiling'
    assert new['diesel_mode']==old['diesel_mode']
    assert new['diesel_allowed_priorities']==old['diesel_allowed_priorities']
    assert new['grid_import_mode']==old['grid_import_mode']
    assert c['gt_dynamics']['gt_start_delay_minutes']==10.
    assert c['diesel_local_response']['inertia_H_seconds']==pytest.approx(.7359)

def test_unknown_m15_configuration_is_rejected_instead_of_ignored():
    with pytest.raises(ValueError):module().model_contract('M15_local_response',{'instant_balance':True})

def test_readiness_does_not_promote_queue_plan_into_assured_supply():
    from tools.engineering_model.execution import GTClockLedger
    m=module();x=object.__new__(m.OperatingCommandCore)
    x.time=45.;x.gt_clocks=GTClockLedger([132.,0.,0.]);x.interlock_latched={}
    x.gt_start_pending={1:dict(started=40.,ready=50.,requested=132.)}
    a=dict(gt_electric_kW=[132.,0.,0.],grid_import_kW=0.,diesel_kW=800.,
        boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.])
    x.executed=a;x.segments=[];x.ramp_time=30.
    x.pending_commands=[dict(id=4,generated_minute=30.,arrival_minute=72.5,dispatch=copy.deepcopy(a))]
    s=SimpleNamespace(gt_prev=np.array([132.,0.,0.]),last_diesel_kW=0.,diesel_status='CONNECTED',
        disconnected_loads=np.zeros((4,3)))
    x.env=SimpleNamespace(state=s,dt_minutes=15.,config={'gt_ramp_kW_per_hour':900.},
        engineering_config={'interlock_rearm_enabled':True})
    from hospital_ies.resilience import empty_faults
    x.data_at=lambda t:(1,SimpleNamespace(),np.zeros((4,3)),empty_faults())
    c=x.readiness_context()
    assert c['actual_diesel_kW']==0.
    assert c['diesel_arrived_cap_kW']==800.
    assert c['pending_starts'][1]['ready']==50.
    # Corrects the initial test's mistaken equation of simulator release with
    # an acknowledged plan. No independently observed ACK exists in this model.
    assert c['known_command_queue'][0]['planned_arrival_minute'] is None
    x.pending_commands[0]['arrival_minute']=999.
    assert x.readiness_context()['known_command_queue']==c['known_command_queue']
    assert not any('assured' in key or 'actual_arrival' in key for key in c['known_command_queue'][0])
    assert c['actual_gt_kW']==[132.,0.,0.]

@pytest.mark.parametrize('policy_key',['F','E','K2'])
def test_actual_joint_policy_accepts_explicit_m15_model_without_legacy_schema_leak(policy_key):
    from tools.operating_response.data_design import normal_proposal,SEALED_DAYS
    from tools.operating_response.runner import factory,policy_specs
    m=module();scene=copy.deepcopy(normal_proposal()['scenarios'][0]);scene['duration_steps']=1
    first=scene['start_hour']
    env=m.OperatingHospitalIESEnv(ROOT,allowed_hours=[first,first+1],sealed_days=SEALED_DAYS,
        model_id='M15_local_response')
    env.reset(scenario=scene,seed=20261017)
    action,_=factory(policy_specs()[policy_key]).decide([m.decision_snapshot(env)])
    *_,info=env.step(action)
    assert env.core.time==15.
    assert info['segments']
    assert all('diesel_local_response' in s for s in info['segments'])
