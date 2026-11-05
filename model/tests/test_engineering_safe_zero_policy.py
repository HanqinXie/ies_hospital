"""Production reuses only exact-zero linear candidates; native remains original."""
import numpy as np
import pytest
from scipy import sparse
from scipy.optimize import Bounds,LinearConstraint,OptimizeResult
from hospital_ies import engineering_numerics as e,solver_backend as b


def zero():
    return b.milp([1.,0.],bounds=Bounds([0.,0.],[2.,2.]),constraints=LinearConstraint([[1.,1.]],[1.],[1.]))


def test_safe_profile_declares_original_native_and_exact_zero_candidate_gates():
    p=e.engineering_profile()
    assert p['native_execution_policy']=='unchanged_fresh_native_no_warm_start'
    assert p['native_acceptance_policy']=='unchanged_original'
    assert p['native_parameters']==dict(FeasibilityTol=1e-9,OptimalityTol=1e-9,IntFeasTol=1e-9)
    assert p['candidate_gate']==dict(physical_rows_bounds=1e-7,normalized_locks=1e-10,integrality=1e-9)
    assert 'exact_zero' in p['candidate_policy']


@pytest.mark.parametrize('cost',[1.,-1.])
def test_positive_and_negative_production_calls_are_fresh_and_never_remembered(cost):
    with e.engineering_numerics_scope() as state:
        args=dict(bounds=Bounds([0.],[3.]),constraints=LinearConstraint([[1.]],[1.],[2.]))
        results=[b.milp([cost],**args),b.milp([cost],**args)]
        assert all(r.status==0 and r.optimizer_called is True for r in results)
        assert state.stats()['native_calls']==2
        assert state.stats()['native_model_builds']==state.stats()['native_model_reuses']==0
        assert not state.models and not state.candidates
        for result in results:
            assert 'rows' in result.attempts[-1]['validation']
            assert all(result.attempts[-1]['parameters'][k]==1e-9 for k in ('FeasibilityTol','OptimalityTol','IntFeasTol'))


def test_exact_zero_candidate_with_only_engineering_feasibility_is_rejected():
    with e.engineering_numerics_scope() as state:
        zero();cached=next(iter(state.candidates.values()))[-1]
        cached[1]+=3e-7  # current objective remains exactly0; exact original balance fails.
        result=zero()
        assert result.status==0 and result.optimizer_called is True
        assert abs(result.x.sum()-1.)<1e-9
        assert state.stats()['candidate_rejections']==1


def test_near_zero_objective_is_not_exact_zero_and_fails_before_current_domain_scan():
    with e.engineering_numerics_scope() as state:
        zero();cached=next(iter(state.candidates.values()))[-1]
        cached[0]=1e-12;cached[1]=1.-1e-12
        result=zero()
        assert result.status==0 and result.optimizer_called is True
        assert state.last_search['rejections'][0]['reason']=='candidate_objective_not_exact_zero'


def test_native_original_gate_rejects_a_point_that_wide_profile_would_accept(monkeypatch):
    def invalid(problem,parameters):
        return OptimizeResult(status=0,native_status=2,x=np.array([3e-7]),fun=0.,mip_dual_bound=0.,mip_gap=0.,backend='gurobipy')
    monkeypatch.setattr(b,'_gurobi_attempt',invalid)
    with e.engineering_numerics_scope():
        result=b.milp([0.],bounds=Bounds([0.],[1.]),constraints=LinearConstraint([[1.]],[0.],[0.]))
    assert result.status==4 and len(result.attempts)==2
    assert all(a['accepted'] is False and 'rows' in a['validation'] for a in result.attempts)


def test_native_nonlinear_analytic_gate_also_remains_original(monkeypatch):
    from hospital_ies.nonlinear import NonlinearSpec,PowerLawEquality
    graph=NonlinearSpec((PowerLawEquality(0,1,.1,.673,'a'*64),))
    raw=np.array([128.,.1*128.**.673+3e-7])
    def invalid(problem,parameters):
        return OptimizeResult(status=0,native_status=2,x=raw.copy(),fun=0.,mip_dual_bound=0.,mip_gap=0.,backend='gurobipy')
    monkeypatch.setattr(b,'_gurobi_attempt',invalid)
    with e.engineering_numerics_scope():
        result=b.milp([0.,0.],bounds=Bounds([128.,0.],[128.,10.]),nonlinear=graph)
    assert result.status==4
    assert all(a['nonlinear_validation']['accepted'] is False and 'rows' in a['validation'] for a in result.attempts)


def test_generic_current_lp_dual_math_is_retained_without_production_positive_reuse():
    original=dict(matrix=sparse.csr_matrix([[1.]]),lower=np.array([1.]),upper=np.array([np.inf]),
        bounds_lower=np.array([0.]),bounds_upper=np.array([3.]))
    witness=dict(row_map=np.array([0]),senses=np.array(['>']),pi=np.array([1.]),original_row_count=1)
    assert e._dual_bound(np.array([1.]),original,witness)==pytest.approx(1.,abs=1e-12)
    original['lower']=np.array([1.25])
    assert e._dual_bound(np.array([1.]),original,witness)==pytest.approx(1.25,abs=1e-12)
