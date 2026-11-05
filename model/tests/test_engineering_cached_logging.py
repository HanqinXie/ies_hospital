"""The real logging entry must accept certified skips without native OPTIMAL."""
from copy import deepcopy
from scipy.optimize import Bounds,LinearConstraint
from hospital_ies.engineering_numerics import engineering_numerics_scope
from hospital_ies.solver_backend import milp
from tools.annual_program.engineering_logging import summarize_solver_call


def cached_call():
    with engineering_numerics_scope():
        args=dict(bounds=Bounds([0.,0.],[2.,2.]),constraints=LinearConstraint([[1.,1.]],1.,1.))
        milp([1.,0.],**args)
        result=milp([1.,0.],**args)
    assert result.optimizer_called is False and result.native_status is None
    return dict(phase='m24_cold_mode_candidate',status='success',inventory_committed=False,
        solver_backend_layers=[dict(layer=0,rank=0,backend='gurobipy',status=0,attempts=result.attempts)])


def test_certified_zero_skip_uses_the_real_compaction_entry():
    call=cached_call();before=deepcopy(call)
    compact=summarize_solver_call(call)
    assert compact is not None
    assert call==before
    attempt=compact['solver_backend_layers'][0]['attempts'][0]
    assert attempt['native_status'] is None and attempt['optimizer_called'] is False
    assert attempt['validation']['raw_rows_retained'] is False


def test_bad_skip_receipt_or_actual_call_keeps_full_evidence():
    call=cached_call()
    bad=deepcopy(call);bad['solver_backend_layers'][0]['attempts'][0]['engineering_acceptance']['optimality']['objective']=1.
    assert summarize_solver_call(bad) is None
    actual=deepcopy(call);actual['phase']='fixed_execution'
    assert summarize_solver_call(actual) is None
