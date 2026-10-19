"""Read-only H120/native validation contracts; no new hospital truth."""
import importlib,copy
import pytest
def auditmod():return importlib.import_module('tools.control_improvement.data_audit')

def test_H120_missing_raw_label_rejected_not_fabricated():
    p={'decisions':[{'labels':{str(h):{} for h in (15,30,60)}}]}
    with pytest.raises(AssertionError,match='horizon|H120'):auditmod().check_horizon_contract(p,[15,30,60,120])

def test_H120_unknown_and_observed_positive_are_kept_exact():
    p={'decisions':[{'labels':{str(h):dict(valid=False,value=None,unknown_reason='solver_error',full_horizon=False) for h in (15,30,60,120)}}]}
    original=copy.deepcopy(p);auditmod().check_horizon_contract(p,[15,30,60,120]);assert p==original

def layer(rank,violation=0.,accepted=True):
    return dict(rank=rank,status=0,backend='gurobipy',numerical_version='integrated.gurobi.explicit_lex.v1',attempts=[dict(accepted=accepted,status=0,raw_x=[0.],
        validation=dict(max_physical_row_violation=violation,max_bound_violation=0.,max_lock_violation=0.,max_integrality_distance=0.))])

def test_success_preserves_all_five_layers_and_native_attempts():
    p=dict(solver_calls=[dict(status='success',solver_backend_layers=[layer(i) for i in range(5)])])
    v=auditmod().native_diagnostics(p);assert v['native_layers']==5 and v['accepted_attempts']==5
    p['solver_calls'][0]['solver_backend_layers'].pop()
    with pytest.raises(AssertionError,match='five|rank'):auditmod().native_diagnostics(p)

def test_invalid_raw_accepted_physical_residual_rejected():
    p=dict(solver_calls=[dict(status='success',solver_backend_layers=[layer(i,2e-7 if i==1 else 0.) for i in range(5)])])
    with pytest.raises(AssertionError,match='physical|residual'):auditmod().native_diagnostics(p)

def test_solver_error_diagnostics_not_declared_hospital_failure():
    p=dict(solver_calls=[dict(status='solver_error',solver_backend_layers=[dict(rank=0,status=2,attempts=[dict(accepted=False,status=2)])])])
    v=auditmod().native_diagnostics(p)
    assert v['failed_call_records']==1 and v['accepted_attempts']==0 and v['nonaccepted_attempts']==1
    assert 'p0_failed_any' not in v

def test_auditor_uses_new_guard_without_old240_import():
    import inspect
    source=inspect.getsource(auditmod().audit)
    assert 'from .data_runner import verify_manifest' in source
    assert 'check_horizon_contract' in source and 'native_diagnostics' in source
