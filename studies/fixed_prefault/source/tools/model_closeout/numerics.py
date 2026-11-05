"""One bounded native primal warm start after a witnessed numerical failure.

M18 original-domain validation and command finalization are retained verbatim.
The preceding vector seeds an actual optimization of the current objective;
it is never returned as a substitute for that optimization.
"""
from __future__ import annotations
import copy
import hashlib
import math
import time
import numpy as np
from scipy.optimize import OptimizeResult
import gurobipy as gp
from hospital_ies.solver_backend import _environment,_COMMON,milp as native_milp
from hospital_ies.solver_diagnostics import active_collector,flatten_constraints,validate_vector
from tools.execution_recovery.numerics import *
from tools.execution_recovery.numerics import solve_layer_with_lock_presentation as previous_solve

PRIMAL_START_CONTRACT='hospital_ies.original_domain_native_primal_start.v19.v1'

def _accepted(v):
    return (v['max_original_violation']<=1e-7 and v['max_lock_violation']<=1e-10
        and v['max_integrality_distance']<=1e-9)

def native_primal_start(c,*,matrix,lower,upper,bounds_lower,bounds_upper,integrality,start,parameters,
                       deadline_monotonic=None):
    """Same native model, including all locks; one actual optimize call."""
    with gp.Model(env=_environment()) as model:
        for key,value in parameters.items():model.setParam(key,value)
        kinds=np.where(np.asarray(integrality)==1,gp.GRB.INTEGER,gp.GRB.CONTINUOUS)
        x=model.addMVar(len(c),lb=bounds_lower,ub=bounds_upper,vtype=kinds)
        model.setObjective(c@x,gp.GRB.MINIMIZE)
        equal=np.isfinite(lower)&np.isfinite(upper)&(lower==upper)
        lo=np.isfinite(lower)&~equal;hi=np.isfinite(upper)&~equal
        if equal.any():model.addMConstr(matrix[equal],x,'=',lower[equal])
        if lo.any():model.addMConstr(matrix[lo],x,'>',lower[lo])
        if hi.any():model.addMConstr(matrix[hi],x,'<',upper[hi])
        x.Start=np.asarray(start).copy()
        remaining=(float(parameters['TimeLimit']) if deadline_monotonic is None
            else deadline_monotonic-time.perf_counter())
        if remaining<=0.:
            return OptimizeResult(status=1,native_status=None,x=None,fun=None,mip_dual_bound=None,
                mip_gap=None,message='Original layer budget exhausted during model construction',
                backend='gurobipy',optimization_executed=False)
        model.setParam('TimeLimit',remaining)
        model.optimize()
        native=int(model.Status);has=model.SolCount>0
        status=0 if native==gp.GRB.OPTIMAL else 2 if native==gp.GRB.INFEASIBLE else 4
        candidate=np.asarray(x.X,float) if has else None
        bound=float(model.ObjBound) if model.IsMIP else float(model.ObjVal) if status==0 else None
        if bound is not None and not math.isfinite(bound):bound=None
        return OptimizeResult(status=status,native_status=native,x=candidate,
            fun=float(model.ObjVal) if has else None,mip_dual_bound=bound,
            mip_gap=float(model.MIPGap) if model.IsMIP and has else 0. if status==0 else None,
            message=f'Gurobi primal-start native status {native}',backend='gurobipy',
            optimization_executed=True,actual_time_limit_seconds=remaining)

def solve_layer_with_lock_presentation(solver,c,*,integrality,bounds,constraints,options,rank,
                                      variables,rows,locks,input_context,row_scaling=False,previous_candidate=None):
    began=time.perf_counter();budget=float((options or {}).get('time_limit',6.))
    collector=active_collector();position=None if collector is None else len(collector.layers)
    result=previous_solve(solver,c,integrality=integrality,bounds=bounds,constraints=constraints,
        options=options,rank=rank,variables=variables,rows=rows,locks=locks,
        input_context=input_context,row_scaling=row_scaling,previous_candidate=previous_candidate)
    if result.status==0 or previous_candidate is None or solver is not native_milp:return result
    matrix,lower,upper=flatten_constraints(constraints)
    validation=validate_vector(matrix,lower,upper,bounds.lb,bounds.ub,integrality,previous_candidate,rows)
    remaining=budget-(time.perf_counter()-began)
    if not _accepted(validation) or remaining<=0.:return result
    evidence=dict(schema=PRIMAL_START_CONTRACT,attempted=True,accepted=False,
        original_budget_seconds=budget,remaining_budget_seconds=remaining,max_additional_native_attempts=1,
        previous_candidate_usage='MIPStart for actual current-objective optimization only',
        previous_vector_returned_without_optimization=False,objective_improvement_claimed=False,
        start_sha256=hashlib.sha256(np.ascontiguousarray(previous_candidate,dtype=float).tobytes()).hexdigest(),
        prior_validation={k:v for k,v in validation.items() if not isinstance(v,list)},
        original_objective_bounds_and_locks_unchanged=True,
        original_validation_thresholds=dict(physical_rows_bounds=1e-7,priority_locks=1e-10,integrality=1e-9))
    parameters=dict(_COMMON,Method=0,NumericFocus=3,Presolve=0,
        MIPGap=float((options or {}).get('mip_rel_gap',1e-6)),TimeLimit=remaining)
    attempt=dict(attempt=len(result.attempts)+1,parameters=parameters,
        presentation='native_primal_MIP_start_same_original_domain',accepted=False,status=4,
        start_sha256=evidence['start_sha256'],validation=None)
    started=time.perf_counter();replacement=None
    try:
        replacement=native_primal_start(np.asarray(c),matrix=matrix,lower=lower,upper=upper,
            bounds_lower=np.asarray(bounds.lb),bounds_upper=np.asarray(bounds.ub),integrality=np.asarray(integrality),
            start=previous_candidate,parameters=parameters,deadline_monotonic=began+budget)
        checked=validate_vector(matrix,lower,upper,bounds.lb,bounds.ub,integrality,replacement.x,rows)
        raw_objective=None if replacement.x is None else float(np.asarray(c)@replacement.x)
        finite_objective=(raw_objective is not None and np.isfinite(raw_objective)
            and replacement.fun is not None and np.isfinite(replacement.fun))
        objective_budget=(1e-9*max(1.,abs(raw_objective),abs(replacement.fun)) if finite_objective else 0.)
        consistency=bool(finite_objective and abs(raw_objective-replacement.fun)<=objective_budget)
        finite_bound=(replacement.mip_dual_bound is not None and np.isfinite(replacement.mip_dual_bound))
        bound_direction=bool(finite_objective and finite_bound
            and replacement.mip_dual_bound<=replacement.fun+objective_budget)
        gap=(None if not finite_objective or not finite_bound or not bound_direction else
            max(0.,replacement.fun-replacement.mip_dual_bound)/max(1e-10,abs(replacement.fun)))
        native_gap=replacement.mip_gap
        accepted=bool(replacement.status==0 and replacement.native_status==gp.GRB.OPTIMAL
            and _accepted(checked) and consistency and bound_direction
            and native_gap is not None and np.isfinite(native_gap) and 0.<=native_gap<=float((options or {}).get('mip_rel_gap',1e-6))
            and gap is not None and gap<=float((options or {}).get('mip_rel_gap',1e-6)))
        attempt.update(status=int(replacement.status),native_status=replacement.native_status,accepted=accepted,
            raw_x=None if replacement.x is None else replacement.x.tolist(),res_fun=replacement.fun,
            mip_dual_bound=replacement.mip_dual_bound,mip_gap=replacement.mip_gap,
            raw_objective=raw_objective,objective_consistent=consistency,
            finite_dual_bound=bool(finite_bound),dual_bound_direction_consistent=bound_direction,
            objective_consistency_budget=objective_budget,validation=checked)
        evidence.update(accepted=accepted,actual_solver_optimization_executed=getattr(replacement,'optimization_executed',True),
            actual_objective=replacement.fun,actual_dual_bound=replacement.mip_dual_bound,relative_gap=gap)
    except Exception as exc:
        attempt.update(error_type=type(exc).__name__,message=str(exc))
        evidence.update(error_type=type(exc).__name__,message=str(exc),actual_solver_optimization_executed=False)
    attempt['elapsed_seconds']=time.perf_counter()-started
    attempts=copy.deepcopy(result.attempts)+[attempt]
    if evidence['accepted']:
        prior=copy.deepcopy(getattr(result,'same_domain_presentation_recovery',{}))
        result=replacement
        result.same_domain_presentation_recovery=dict(prior,recovery_attempted=True,recovery_accepted=True,
            additional_primal_start=evidence,original_objective_bounds_and_locks_unchanged=True,
            previous_or_other_state_candidate_used=True,previous_usage='native MIPStart only; new optimum certificate required')
    else:
        result.status=4
        result.message='Witnessed original-domain numerical optimization unresolved; native failures retained'
    result.attempts=attempts;result.primal_start_recovery=evidence;result.numerical_version=PRIMAL_START_CONTRACT
    if collector is not None and len(collector.layers)>position:
        layer=collector.layers[position]
        collector.finish(layer,result,time.perf_counter()-began)
        layer['primal_start_recovery']=copy.deepcopy(evidence)
    return result
