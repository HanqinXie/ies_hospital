"""Original domain, finite native recovery and explicit injection contract."""
import importlib
import importlib.util
import numpy as np
import pytest
from scipy.optimize import Bounds,LinearConstraint,OptimizeResult

def backend():
    assert importlib.util.find_spec('hospital_ies.solver_backend') is not None,'new local native backend is required'
    return importlib.import_module('hospital_ies.solver_backend')

def problem():
    return dict(c=np.array([1.]),integrality=np.array([0]),bounds=Bounds([0.],[2.]),
        constraints=[LinearConstraint([[1.]],[-np.inf],[1.])],options=dict(time_limit=6.,mip_rel_gap=1e-6))

def fake_result(value,status=0):
    return OptimizeResult(status=status,x=None if status else np.array([value]),fun=None if status else value,
        mip_dual_bound=None if status else value,mip_gap=None if status else 0.,mip_node_count=0,
        native_status=2 if status==0 else 3,message='test native result',backend='gurobipy')

def test_descriptor_names_local_backend_explicit_layers_and_strict_parameters():
    d=backend().descriptor()
    assert d['backend']=='gurobipy'
    assert d['version']==[13,0,2]
    assert d['explicit_lex_layers']==5
    assert d['max_attempts_per_layer']==2
    assert d['parameters']['MIPGapAbs']==0.
    assert d['parameters']['MIPGap']==1e-6
    assert d['raw_validation']==dict(physical_rows_bounds=1e-7,normalized_locks=1e-10,integrality=1e-9)

def test_actual_native_ranged_rows_integral_domain_and_input_arrays_unchanged():
    b=backend();c=np.array([1.,1.]);A=np.array([[2.,1.]])
    original_c=c.copy();original_A=A.copy()
    r=b.milp(c,integrality=[1,0],bounds=Bounds([0.,0.],[1.,10.]),
        constraints=[LinearConstraint(A,[3.],[4.])],options=dict(time_limit=6.,mip_rel_gap=1e-6))
    assert r.status==0 and r.backend=='gurobipy'
    assert r.x.tolist()==[1.,1.] and r.fun==2.
    assert r.mip_dual_bound==2. and r.mip_gap<=1e-6
    assert len(r.attempts)==1 and r.attempts[0]['accepted']
    assert np.array_equal(c,original_c) and np.array_equal(A,original_A)

def test_original_priority_lock_rejects_candidate_then_recovers_without_clipping(monkeypatch):
    b=backend();seen=[]
    def attempt(p,parameters):
        seen.append(parameters)
        return fake_result(1.+2e-9 if len(seen)==1 else 1.)
    monkeypatch.setattr(b,'_gurobi_attempt',attempt)
    with b.original_problem(np.array([[1.]]),[-np.inf],[1.],[0.],[2.],[0],[dict(kind='priority_lock',name='original')]):
        r=b.milp(**problem())
    assert r.status==0 and r.x[0]==1.
    assert len(r.attempts)==2
    assert r.attempts[0]['raw_x'][0]>1.
    assert not r.attempts[0]['accepted'] and r.attempts[1]['accepted']
    assert seen[0]['Presolve']==2 and seen[1]['Presolve']==0
    assert all(p['MIPGap']==1e-6 and p['MIPGapAbs']==0 for p in seen)
    assert all(0<p['TimeLimit']<=6 for p in seen)

def test_invalid_second_candidate_remains_solver_error_and_raw_attempts_retained(monkeypatch):
    b=backend();monkeypatch.setattr(b,'_gurobi_attempt',lambda p,parameters:fake_result(1.+2e-8))
    with b.original_problem(np.array([[1.]]),[-np.inf],[1.],[0.],[2.],[0],[dict(kind='priority_lock')]):
        r=b.milp(**problem())
    assert r.status==4 and len(r.attempts)==2
    assert all(a['raw_x'][0]>1. for a in r.attempts)
    assert all(not a['accepted'] for a in r.attempts)
    assert all(a['validation']['max_lock_violation']>1e-10 for a in r.attempts)

def test_true_original_constraint_infeasibility_not_recorded_as_zero_success():
    b=backend();r=b.milp([1.],integrality=[0],bounds=Bounds([0.],[0.]),
        constraints=[LinearConstraint([[1.]],[1.],[1.])],options=dict(time_limit=6.,mip_rel_gap=1e-6))
    assert r.status==2 and r.x is None
    assert len(r.attempts)==2 and all(a['status']==2 for a in r.attempts)

def test_native_exception_recovery_records_error_without_exposing_credentials(monkeypatch):
    b=backend();n=[]
    def attempt(p,parameters):
        n.append(1)
        if len(n)==1:raise RuntimeError('forced numeric exception')
        return fake_result(0.)
    monkeypatch.setattr(b,'_gurobi_attempt',attempt)
    r=b.milp(**problem())
    assert r.status==0 and len(r.attempts)==2
    assert r.attempts[0]['error_type']=='RuntimeError'
    assert r.attempts[1]['accepted']

def test_original_context_resets_after_nested_exception():
    b=backend();assert b.active_original_problem() is None
    with pytest.raises(RuntimeError):
        with b.original_problem([[1.]],[0.],[1.],[0.],[1.],[0],[dict(kind='physical')]):
            assert b.active_original_problem() is not None
            raise RuntimeError('test')
    assert b.active_original_problem() is None

def test_nonoptimal_bound_is_not_relabelled_as_optimal(monkeypatch):
    b=backend()
    def attempt(p,parameters):
        r=fake_result(1.);r.mip_dual_bound=0.;r.mip_gap=1.;return r
    monkeypatch.setattr(b,'_gurobi_attempt',attempt)
    r=b.milp(**problem())
    assert r.status==4 and len(r.attempts)==2
    assert all(not a['accepted'] for a in r.attempts)

def test_bounded_domain_without_linear_rows_still_validates_raw_bounds():
    b=backend();r=b.milp([1.],bounds=Bounds([2.],[3.]),options=dict(time_limit=6.,mip_rel_gap=1e-6))
    assert r.status==0 and r.x.tolist()==[2.]
    assert r.attempts[0]['validation']['max_original_violation']==0.
