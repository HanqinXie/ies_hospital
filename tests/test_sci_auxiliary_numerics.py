"""Actual regression for the C19 propagation failure at minute 75.

The episode is stopped after minute 90 solely to bound this regression's cost;
its initialization, warmup, fault and first six decisions match the failed pilot.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from scripts.sci_campaign import pilot_proposal
from tools.sci_engineering.contracts import normalize_study_scenario,exporter_metadata
from tools.sci_engineering.environment import OperatingHospitalIESEnv,run_episode
from tools.sci_engineering.policy import policy_specs,factory
from tools.sci_engineering import design

def test_fraction_bound_acceptance_cannot_hide_original_auxiliary_kw_violation():
    import numpy as np
    import pytest
    from hospital_ies.execution import SolverExecutionError
    from tools.sci_engineering.service import validate_auxiliary_kW_bounds
    indices=np.arange(12).reshape(4,3)
    values=np.ones(12);values[0]+=5e-8
    required=np.full((4,3),100.)
    # The fraction residual passes 1e-7, but maps to 5e-6 kW.
    with pytest.raises(SolverExecutionError,match='auxiliary.*kW'):
        validate_auxiliary_kW_bounds(values,indices,required,np.ones(12))

def test_true_c19_propagation_case_remains_numerically_executable_after_75_minutes():
    scene=normalize_study_scenario(next(s for s in pilot_proposal()['scenarios'] if s['canonical_class']=='L2'))
    scene['duration_steps']=6
    env=OperatingHospitalIESEnv(ROOT,allowed_hours=design.allowed_hours({'scenarios':[scene]}),
        sealed_days=design.SEALED_DAYS,model_id=design.MODEL_ID,model_config={})
    manifest=dict(execution_contract_id='hospital_ies.sci_engineering_execution.v21.v1',
        simulator_hash='regression',signature='regression',
        model_config_signature=design.digest(env.operating_model_config),**exporter_metadata([scene]))
    payload=run_episode(env,scene,factory(policy_specs()['C'],plant=env.plant),scene['initialization_seed'],manifest)
    assert payload.get('error') is None,payload.get('error',{}).get('message')
    assert payload['summary']['status']=='completed'
    assert payload['summary']['observed_until_minute']==90.
