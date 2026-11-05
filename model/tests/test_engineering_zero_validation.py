"""Zero natural-bound leaf logs check every current row without row dictionaries."""
from copy import deepcopy
from types import SimpleNamespace
import numpy as np
import pytest
from scipy.optimize import Bounds,LinearConstraint,OptimizeResult

from hospital_ies import engineering_numerics as e,solver_backend as b,solver_diagnostics as diagnostics


SCHEMA='hospital_ies.engineering_current_domain_validation_summary.v1'


def zero(rhs=1.):
    return b.milp([1.,0.],bounds=Bounds([0.,0.],[2.,2.]),constraints=LinearConstraint([[1.,1.]],[rhs],[rhs]))


def assert_summary(result):
    validation=result.attempts[-1]['validation']
    assert validation['schema']==SCHEMA
    assert validation['all_current_rows_checked'] is True
    assert validation['row_count']==1 and validation['variable_count']==2
    assert validation['raw_rows_retained'] is False and validation['raw_rows_recoverable'] is False
    assert validation['current_domain_sha256']==result.engineering_acceptance['current_domain_sha256']
    assert not ({'rows','rows_ref','worst_rows','bound_violation','integer_distance'}&set(validation))
    assert validation['max_original_violation']<=1e-6
    return validation


def test_native_and_cached_zero_check_all_current_rows_without_rendering_details(monkeypatch):
    calls=[];original=diagnostics.validate_vector
    def counted(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(diagnostics,'validate_vector',counted)
    with e.engineering_numerics_scope():
        first=zero();assert first.status==0 and first.optimizer_called is True
        assert_summary(first)
        replay=zero();assert replay.status==0 and replay.optimizer_called is False
        assert_summary(replay)
    assert calls==[], 'Accepted linear zero layers should not allocate complete row dictionaries'


def test_current_rhs_still_rejects_old_zero_candidate_then_checks_current_domain():
    with e.engineering_numerics_scope() as state:
        zero()
        changed=zero(1.25)
        assert changed.status==0 and changed.optimizer_called is True
        assert abs(changed.x.sum()-1.25)<1e-7
        assert state.stats()['candidate_rejections']==1
        assert_summary(changed)


@pytest.mark.parametrize('cost',[1.,-1.])
def test_nonzero_repeated_native_calls_keep_full_rows(cost):
    with e.engineering_numerics_scope():
        args=dict(bounds=Bounds([0.],[3.]),constraints=LinearConstraint([[1.]],[1.],[2.]))
        first=b.milp([cost],**args);replay=b.milp([cost],**args)
        assert first.status==replay.status==0
        # Positive/negative production reuse was explicitly retired after the
        # trajectory mismatch; both fresh native solves must keep full rows.
        assert first.optimizer_called is True and replay.optimizer_called is True
        for result in (first,replay):
            assert 'rows' in result.attempts[-1]['validation']
            assert 'worst_rows' in result.attempts[-1]['validation']
            assert result.attempts[-1]['validation'].get('schema')!=SCHEMA


def test_native_failed_zero_candidate_keeps_full_failure_rows(monkeypatch):
    def invalid(problem,parameters):
        return OptimizeResult(status=0,native_status=2,x=np.array([1.+2e-8]),fun=0.,mip_dual_bound=0.,mip_gap=0.,backend='gurobipy')
    monkeypatch.setattr(b,'_gurobi_attempt',invalid)
    with e.engineering_numerics_scope():
        with b.original_problem([[1.]],[-np.inf],[1.],[0.],[2.],[0],[dict(kind='priority_lock')]):
            result=b.milp([0.],bounds=Bounds([0.],[2.]),constraints=LinearConstraint([[1.]],[-np.inf],[1.]))
    assert result.status==4 and len(result.attempts)==2
    assert all(a['accepted'] is False and 'rows' in a['validation'] and 'worst_rows' in a['validation'] for a in result.attempts)


def test_nonlinear_zero_layers_keep_full_raw_and_analytic_validation_rows():
    from hospital_ies.nonlinear import NonlinearSpec,PowerLawEquality
    graph=NonlinearSpec((PowerLawEquality(0,1,.1,.673,'a'*64),))
    with e.engineering_numerics_scope():
        args=dict(bounds=Bounds([128.,0.],[128.,10.]),nonlinear=graph)
        first=b.milp([0.,0.],**args);replay=b.milp([0.,0.],**args)
        assert first.status==replay.status==0
        for result in (first,replay):
            attempt=result.attempts[-1]
            assert 'rows' in attempt['validation']
            assert 'rows' in attempt['nonlinear_validation']['true_validation']


def test_logging_recognizes_explicit_summary_without_inventing_recoverable_rows():
    from tools.annual_program import engineering_logging as logging
    with e.engineering_numerics_scope():summary=assert_summary(zero())
    assert logging._validation(summary)==summary
    malformed=deepcopy(summary);malformed['all_current_rows_checked']=False
    with pytest.raises(ValueError):logging._validation(malformed)
    malformed=deepcopy(summary);malformed['raw_rows_retained']=True
    with pytest.raises(ValueError):logging._validation(malformed)


def test_exact_default_and_exact_planner_scope_keep_full_rows_and_metadata_declares_migration():
    from tools.annual_program import runner
    exact=zero();assert 'rows' in exact.attempts[-1]['validation']
    assert e.engineering_profile().get('validation_detail_policy')=='zero_natural_bound_current_domain_maxima_else_full_rows'
    env=SimpleNamespace(engineering_numerical_profile=e.engineering_profile())
    migration=runner._execution_log_migrations(env)
    assert migration['engineering_zero_validation']['schema']==SCHEMA
    assert migration['engineering_zero_validation']['raw_rows_retained'] is False
    assert migration['engineering_zero_validation']['all_current_rows_checked'] is True
    assert 'engineering_zero_validation' not in runner._execution_log_migrations(SimpleNamespace())
