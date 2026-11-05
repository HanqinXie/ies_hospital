"""Exact command selection stays outside the volatile engineering leaf cache."""
import inspect
import numpy as np
import pytest
from scipy.optimize import Bounds, LinearConstraint

from hospital_ies import engineering_numerics as e
from hospital_ies import solver_backend as backend
from tools.sci_engineering import planner


def test_candidate_families_bind_exact_objective_bytes_and_expand_bounded_capacity():
    def problem(c):
        return dict(c=np.array(c),integrality=np.array([0,0]),bounds_lower=np.array([0.,0.]),
            bounds_upper=np.array([2.,2.]))
    assert e._family_key(problem([1.,0.])) != e._family_key(problem([2.,0.]))
    assert e._family_key(problem([1.,0.])) != e._family_key(problem([np.nextafter(1.,2.),0.]))
    assert e.engineering_profile()['max_candidate_families']==256


def test_distinct_objective_candidates_do_not_evict_each_other_and_current_rhs_is_checked():
    with e.engineering_numerics_scope() as state:
        for index in range(20):
            result=backend.milp([float(index+1),0.],bounds=Bounds([0.,0.],[2.,2.]),
                constraints=LinearConstraint([[1.,1.]],[1.],[1.]))
            assert result.status==0
        assert state.stats()['candidate_families']==20
        before=state.stats()['native_calls']
        cached=backend.milp([1.,0.],bounds=Bounds([0.,0.],[2.,2.]),constraints=LinearConstraint([[1.,1.]],[1.],[1.]))
        assert cached.optimizer_called is False and state.stats()['native_calls']==before
        changed=backend.milp([1.,0.],bounds=Bounds([0.,0.],[2.,2.]),constraints=LinearConstraint([[1.,1.]],[1.5],[1.5]))
        assert changed.optimizer_called is True and abs(changed.x.sum()-1.5)<1e-7


def _call():
    return planner.solve_joint(None,None,None,None,None,.25,850.,False,900.)


def test_public_command_planner_uses_exact_policy_and_restores_engineering_leaf_scope(monkeypatch):
    seen=[]
    def candidate(**kwargs):
        seen.append((e.active_engineering_numerics(),backend.descriptor()['parameters']['FeasibilityTol']))
        return {'joint_diagnostic':{}}
    monkeypatch.setattr(planner,'_solve_joint_candidate',candidate)
    assert e.engineering_profile().get('planner_policy')=='unchanged_exact_command_generation'
    with e.engineering_numerics_scope() as state:
        assert _call()=={'joint_diagnostic':{}}
        assert seen==[(None,1e-9)]
        assert e.active_engineering_numerics() is state
        assert state.stats()['native_calls']==0
    assert _call()=={'joint_diagnostic':{}}
    assert seen[-1]==(None,1e-9)
    assert hasattr(planner.solve_joint,'__wrapped__')
    assert inspect.signature(planner.solve_joint)==inspect.signature(planner.solve_joint.__wrapped__)


def test_exact_planner_exception_restores_outer_engineering_scope(monkeypatch):
    def candidate(**kwargs):
        assert e.active_engineering_numerics() is None
        raise RuntimeError('pure injected planner failure')
    monkeypatch.setattr(planner,'_solve_joint_candidate',candidate)
    with e.engineering_numerics_scope() as state:
        with pytest.raises(RuntimeError,match='pure injected planner failure'):_call()
        assert e.active_engineering_numerics() is state
