"""All four policy adapters execute their ordinary route on M19."""
import copy
import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

@pytest.mark.parametrize('name',['E','K1','C19','C18_M19'])
def test_versioned_environment_routes_and_measured_service_ledger(name):
    from tools.model_closeout import environment as m
    from tools.model_closeout.policy import StateCoordinatedPolicy
    from tools.essential_fixes.runner import factory,policy_specs
    from tools.operating_response.data_design import normal_proposal,SEALED_DAYS
    scene=copy.deepcopy(normal_proposal()['scenarios'][0]);scene['duration_steps']=1
    first=scene['start_hour']
    env=m.OperatingHospitalIESEnv(ROOT,allowed_hours=[first,first+1],sealed_days=SEALED_DAYS)
    env.reset(scenario=scene,seed=20261018);env.core.boundary()
    policy=(factory(policy_specs()[name]) if name in ('E','K1') else
        StateCoordinatedPolicy({'critical_reserve':name=='C19'},policy_id=name))
    request,_=policy.decide([m.decision_snapshot(env)])
    env.step(request)
    assert env.model_id=='M19_model_closeout'
    assert env.core.time==15. and not env.solver_errors
    assert env.core.active_command['execution_model_id']==env.model_id
    assert all(s['execution_model_id']==env.model_id for s in env.core.segments)
    for s in env.core.segments:
        ledger=s['service_request_ledger']
        assert ledger['active_cut_kW']==s['disconnected_loads_kW']
        assert ledger['original_demand_kW']==s['loads_kW']

def test_old_model_identity_cannot_silently_use_new_dynamics():
    from tools.model_closeout.environment import model_contract
    with pytest.raises(ValueError):model_contract('M18_execution_recovery',{})
