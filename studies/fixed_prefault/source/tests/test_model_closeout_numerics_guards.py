"""Synthetic numerical-guard probes; no Gurobi model or physical episode runs."""
from __future__ import annotations
import copy
import importlib
import itertools
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import pytest
from scipy import sparse
from scipy.optimize import Bounds,LinearConstraint,OptimizeResult
from hospital_ies.solver_diagnostics import LayerCollector


def candidate(**changes):
    value=dict(status=0,native_status=2,x=np.array([1.,0.]),fun=1.,
        mip_dual_bound=1.,mip_gap=0.,message='Synthetic native optimal certificate',backend='synthetic')
    value.update(changes)
    return OptimizeResult(**value)


@pytest.fixture
def guarded(monkeypatch):
    module=importlib.import_module('tools.model_closeout.numerics')
    matrix=sparse.csr_matrix([[1.,0.],[1.,0.]])
    lower=np.array([1.,-np.inf]);upper=np.array([np.inf,1.])
    objective=np.array([1.,0.]);integrality=np.array([0,1])
    bounds=Bounds([0.,0.],[10.,1.])
    rows=[dict(name='actual_balance',kind='physical',unit='native'),
          dict(name='prior_priority',kind='priority_lock',unit='normalized')]
    variables=[dict(index=i,name='x'+str(i),unit='native') for i in range(2)]
    kwargs=dict(integrality=integrality,bounds=bounds,
        constraints=[LinearConstraint(matrix,lower,upper)],options=dict(time_limit=6.,mip_rel_gap=1e-6),
        rank=20,variables=variables,rows=rows,locks=[dict(source_rank=19,upper=1.)],
        input_context={'synthetic_guard_test':True},row_scaling=False,previous_candidate=np.array([1.,0.]))
    old=OptimizeResult(status=4,x=None,fun=None,mip_dual_bound=None,mip_gap=None,
        message='Synthetic earlier numerical failure',attempts=[dict(attempt=1,accepted=False,status=2,native_status=3)])
    state=dict(module=module,kwargs=kwargs,matrix=matrix,lower=lower,upper=upper,
        objective=objective,previous=old,calls=[],previous_calls=[],replacement=candidate(),collector=None)
    clock=itertools.count(0.,.01)
    monkeypatch.setattr(module,'time',SimpleNamespace(perf_counter=lambda:next(clock)))
    monkeypatch.setattr(module,'active_collector',lambda:state['collector'])
    def initial(solver,c,**kw):
        state['previous_calls'].append(solver)
        if state['collector'] is not None:
            collect={k:v for k,v in kw.items() if k not in ('previous_candidate','row_scaling')}
            layer=state['collector'].begin(c,**collect)
            state['collector'].finish(layer,old,.01)
        return old
    def native(c,**kw):
        state['calls'].append(dict(c=np.asarray(c).copy(),**copy.deepcopy(kw)))
        if isinstance(state['replacement'],Exception):raise state['replacement']
        return state['replacement']
    monkeypatch.setattr(module,'previous_solve',initial)
    monkeypatch.setattr(module,'native_primal_start',native)
    state['solve']=lambda solver=None:module.solve_layer_with_lock_presentation(
        module.native_milp if solver is None else solver,objective,**kwargs)
    return state


def test_exhausted_original_budget_does_not_start_native_recovery(guarded,monkeypatch):
    s=guarded;clock=iter([0.,6.])
    monkeypatch.setattr(s['module'],'time',SimpleNamespace(perf_counter=lambda:next(clock)))
    assert s['solve']() is s['previous']
    assert s['calls']==[]


@pytest.mark.parametrize('bad_prior',[None,[.5,0.],[1.+2e-10,0.],[1.,.5]])
def test_no_strictly_qualified_prior_means_no_native_recovery(guarded,bad_prior):
    s=guarded;s['kwargs']['previous_candidate']=None if bad_prior is None else np.asarray(bad_prior)
    assert s['solve']() is s['previous']
    assert s['calls']==[]


def test_injected_solver_cannot_be_bypassed_by_real_native_recovery(guarded):
    s=guarded
    def injected(*a,**k):raise AssertionError('Only the injected previous-layer adapter should receive this identity')
    assert s['solve'](injected) is s['previous']
    assert s['previous_calls']==[injected] and s['calls']==[]


def test_existing_accepted_result_gets_no_extra_attempt(guarded):
    s=guarded;s['previous'].status=0
    assert s['solve']() is s['previous']
    assert s['calls']==[]


def test_success_requires_actual_optimize_and_preserves_original_domain(guarded):
    s=guarded;prior=s['kwargs']['previous_candidate'].copy();answer=s['solve']()
    assert answer is s['replacement'] and answer.status==0
    assert len(s['calls'])==1 and len(answer.attempts)==2
    call=s['calls'][0]
    np.testing.assert_array_equal(call['matrix'].toarray(),s['matrix'].toarray())
    for key,value in [('c',s['objective']),('lower',s['lower']),('upper',s['upper']),
                      ('bounds_lower',s['kwargs']['bounds'].lb),('bounds_upper',s['kwargs']['bounds'].ub),
                      ('integrality',s['kwargs']['integrality']),('start',prior)]:
        np.testing.assert_array_equal(call[key],value)
    np.testing.assert_array_equal(s['kwargs']['previous_candidate'],prior)
    assert call['parameters']['Method']==0 and call['parameters']['Presolve']==0
    assert call['parameters']['NumericFocus']==3 and 0<call['parameters']['TimeLimit']<6.
    assert answer.attempts[0]['accepted'] is False and answer.attempts[1]['accepted']
    assert answer.primal_start_recovery['previous_vector_returned_without_optimization'] is False
    assert answer.primal_start_recovery['actual_solver_optimization_executed'] is True


@pytest.mark.parametrize('replacement',[
    candidate(status=4,native_status=9),
    candidate(mip_dual_bound=None),
    candidate(fun=None),
    candidate(mip_dual_bound=.9,mip_gap=.1),
    candidate(x=np.array([1.+2e-10,0.]),fun=1.+2e-10,mip_dual_bound=1.+2e-10),
    candidate(x=np.array([1.-2e-7,0.]),fun=1.-2e-7,mip_dual_bound=1.-2e-7),
    candidate(x=np.array([1.,.5])),
    RuntimeError('Synthetic native construction/optimization error'),
],ids=['nonoptimal','missing_bound','missing_objective','dual_gap','lock_residue',
       'physical_residue','fractional_integer','native_exception'])
def test_missing_certificate_or_strict_violation_is_unknown_and_attempt_is_retained(guarded,replacement):
    s=guarded;s['replacement']=replacement;answer=s['solve']()
    assert answer.status==4 and answer.x is None
    assert len(s['calls'])==1 and len(answer.attempts)==2
    assert answer.attempts[0]['native_status']==3
    assert answer.attempts[1]['accepted'] is False
    assert answer.primal_start_recovery['accepted'] is False
    assert 'unresolved' in answer.message


@pytest.mark.parametrize('replacement',[
    candidate(mip_dual_bound=float('nan')),
    candidate(mip_dual_bound=float('inf')),
    candidate(mip_dual_bound=2.),
    candidate(status=0,native_status=3),
    candidate(fun=1.+5e-8,mip_dual_bound=1.+5e-8),
],ids=['nan_bound','positive_infinite_bound','bound_exceeds_primal',
       'contradictory_native_status','outside_original_objective_consistency_budget'])
def test_invalid_optimality_certificate_cannot_pass_recovery_guard(guarded,replacement):
    s=guarded;s['replacement']=replacement;answer=s['solve']()
    assert answer.status==4, 'An invalid native certificate must remain numerical unknown'
    assert answer.attempts[-1]['accepted'] is False
    assert answer.primal_start_recovery['accepted'] is False


@pytest.mark.parametrize('accepted',[True,False])
def test_collector_keeps_initial_failure_and_appends_recovery_evidence(guarded,accepted):
    s=guarded;collector=LayerCollector();s['collector']=collector
    seed={k:v for k,v in s['kwargs'].items() if k not in ('previous_candidate','row_scaling')}
    seed['rank']=19;layer=collector.begin(s['objective'],**seed)
    collector.finish(layer,candidate(),0.)
    if not accepted:s['replacement']=candidate(x=np.array([1.+2e-10,0.]),fun=1.+2e-10,mip_dual_bound=1.+2e-10)
    answer=s['solve']()
    assert len(collector.layers)==2
    latest=collector.layers[-1]
    assert latest['rank']==20 and latest['result']['status']==(0 if accepted else 4)
    assert len(latest['result']['attempts'])==2
    assert latest['result']['attempts'][0]['native_status']==3
    assert latest['result']['attempts'][-1]['accepted'] is accepted
    assert latest['primal_start_recovery']['accepted'] is accepted
    assert latest['primal_start_recovery']==answer.primal_start_recovery
    np.testing.assert_array_equal(collector.layers[0]['result']['x_raw'],np.array([1.,0.]))
