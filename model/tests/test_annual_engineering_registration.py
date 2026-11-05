"""Pure numerical-policy registration, no IES environment or optimizer."""
from pathlib import Path
import sys
from copy import deepcopy
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.annual_program.registration import make_case,validate_case
PHYSICAL='annual_responsive_dynamic_bridge_20261104.v1'

def test_engineering_profile_is_explicit_and_bound_without_altering_physical_stream():
    kwargs=dict(method='M_ready',duration_minutes=120.,execution_revision=PHYSICAL)
    exact=make_case(**kwargs)
    engineering=make_case(**kwargs,numerical_profile='engineering_fast_v1')
    assert engineering['numerical_execution']['profile']=='engineering_fast_v1'
    assert engineering['annual_program']['parameters']['numerical_profile']=='engineering_fast_v1'
    assert engineering['id']!=exact['id']
    for field in ('base_state_id','exogenous_stream_id','m21_physical_exogenous_sha256',
                  'duration_steps','step_minutes','model_closure_events'):
        assert engineering['scenario'][field]==exact['scenario'][field]
    assert validate_case(engineering)==engineering
    assert 'numerical_execution' not in exact
    assert 'numerical_profile' not in exact['annual_program']['parameters']

def test_unknown_profile_and_legacy_owner_are_rejected():
    with pytest.raises(ValueError):make_case('M_ready',numerical_profile='unregistered')
    with pytest.raises(ValueError):make_case('M_ready',numerical_profile='engineering_fast_v1')

def test_numerical_budget_changes_cannot_borrow_a_registered_case():
    case=make_case('C_ready',execution_revision=PHYSICAL,numerical_profile='engineering_fast_v1')
    changed=deepcopy(case);changed['numerical_execution']['profile']='unregistered'
    with pytest.raises(ValueError):validate_case(changed)
