"""Local native solver for the five separately locked Hospital IES layers.

The adapter retains the scipy-shaped injection point, but does not call scipy.
Every attempt is validated in the original physical and priority-lock units.
Two uniform numerical configurations share the caller's original time budget;
neither previous incumbents nor scenario-specific fallback rules are used.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import threading
import time
import numpy as np
from scipy import sparse
from scipy.optimize import OptimizeResult
import gurobipy as gp
from .solver_result_cache import (accepted_result_cache_scope, accepted_result_cache_stats,
    active_cache, problem_key, json_sha, provenance, code_sha)
from .engineering_numerics import (active_engineering_numerics, engineering_profile,
    numerical_tolerances, numerical_validation_acceptable,zero_natural_bound_validation,
    original_validation_acceptable,engineering_numerics_scope)

NUMERICAL_VERSION = 'integrated.gurobi.explicit_lex.v1'
ENGINEERING_NUMERICAL_VERSION = 'integrated.gurobi.explicit_lex.engineering_fast.v1'
_ORIGINAL = ContextVar('hospital_ies_original_solver_domain', default=None)
_LOCAL = threading.local()
_COMMON = dict(OutputFlag=0, Threads=1, Method=1, Seed=0,
    DualReductions=0, FeasibilityTol=1e-9, IntFeasTol=1e-9,
    OptimalityTol=1e-9, MIPGap=1e-6, MIPGapAbs=0.)
_RECOVERY = (dict(NumericFocus=1, Presolve=2), dict(NumericFocus=3, Presolve=0))
_VALIDATION = dict(physical_rows_bounds=1e-7, normalized_locks=1e-10, integrality=1e-9)

def descriptor():
    value = dict(backend='gurobipy', version=list(gp.gurobi.version()),
        numerical_version=NUMERICAL_VERSION, explicit_lex_layers=5,
        max_attempts_per_layer=2, parameters=dict(_COMMON),
        recovery_order=[dict(x) for x in _RECOVERY], raw_validation=dict(_VALIDATION),
        time_budget='shared original per-layer limit; model construction included',
        objective_units='unchanged', remote_services=False)
    if active_engineering_numerics() is not None:
        value['engineering_profile'] = engineering_profile()
        value['numerical_version'] = ENGINEERING_NUMERICAL_VERSION
        value['parameters'].update(engineering_profile()['native_parameters'])
        value['raw_validation'] = numerical_tolerances()
    return value

def _cache_policy_tokens():
    """Cheap, type-exact tokens for every mutable dependency of descriptor()."""
    def flat(value):
        if type(value) is not dict or any(type(k) is not str or type(v) not in (str,int,float,bool,type(None)) for k,v in value.items()):
            return None
        return tuple((k,type(v),v.hex() if type(v) is float else v) for k,v in sorted(value.items()))
    common=flat(_COMMON);recovery=tuple(flat(v) for v in _RECOVERY);validation=flat(_VALIDATION)
    if common is None or validation is None or any(v is None for v in recovery):return None
    return (NUMERICAL_VERSION,tuple(gp.gurobi.version()),common,recovery,validation)

def active_original_problem():
    return _ORIGINAL.get()

@contextmanager
def original_problem(matrix, lower, upper, bounds_lower, bounds_upper, integrality, rows):
    token = _ORIGINAL.set(dict(matrix=sparse.csr_matrix(matrix).copy(),
        lower=np.asarray(lower, dtype=float).copy(), upper=np.asarray(upper, dtype=float).copy(),
        bounds_lower=np.asarray(bounds_lower, dtype=float).copy(),
        bounds_upper=np.asarray(bounds_upper, dtype=float).copy(),
        integrality=np.asarray(integrality).copy(), rows=[dict(r) for r in rows]))
    try:
        yield _ORIGINAL.get()
    finally:
        _ORIGINAL.reset(token)

def _environment():
    if not hasattr(_LOCAL, 'environment'):
        env = gp.Env(empty=True)
        env.setParam('OutputFlag', 0)
        env.start()
        _LOCAL.environment = env
    return _LOCAL.environment

def _gurobi_attempt(problem, parameters):
    """One native attempt; exposed separately for explicit error injection."""
    engineering = active_engineering_numerics()
    if engineering is not None:
        engineering.counts['native_calls']+=1
        if problem.get('nonlinear') is not None:engineering.counts['nonlinear_native_calls']+=1
    built_at=time.perf_counter()
    with gp.Model(env=_environment()) as model:
        if engineering is not None:
            engineering.counts['fresh_native_models_built']=engineering.counts.get('fresh_native_models_built',0)+1
        for key, value in parameters.items():
            model.setParam(key, value)
        integ = problem['integrality']
        kinds = np.where(integ == 1, gp.GRB.INTEGER, gp.GRB.CONTINUOUS)
        x = model.addMVar(len(problem['c']), lb=problem['bounds_lower'],
            ub=problem['bounds_upper'], vtype=kinds)
        model.setObjective(problem['c'] @ x, gp.GRB.MINIMIZE)
        matrix = problem['matrix']
        lo = problem['lower']; hi = problem['upper']
        equality = np.isfinite(lo) & np.isfinite(hi) & (lo == hi)
        lower = np.isfinite(lo) & ~equality
        upper = np.isfinite(hi) & ~equality
        if equality.any(): model.addMConstr(matrix[equality], x, '=', lo[equality])
        if lower.any(): model.addMConstr(matrix[lower], x, '>', lo[lower])
        if upper.any(): model.addMConstr(matrix[upper], x, '<', hi[upper])
        nonlinear=problem.get('nonlinear')
        if nonlinear is not None:
            if gp.gurobi.version()[0]<13:raise RuntimeError('SF2 exact nonlinear backend requires Gurobi13; no linear fallback')
            model.setParam('NonConvex',2)
            for i,item in enumerate(nonlinear.equalities):
                model.addGenConstrNL(x[item.output_index].item(),
                    item.coefficient*(x[item.input_index].item()**item.exponent),name=f'SF2_fuel_{i}')
            remaining=parameters['TimeLimit']-(time.perf_counter()-built_at)
            if remaining<=0:raise TimeoutError('SF2 original solve budget exhausted during graph construction')
            model.setParam('TimeLimit',remaining)
        model.optimize()
        native = int(model.Status)
        status = 0 if native == gp.GRB.OPTIMAL else 2 if native == gp.GRB.INFEASIBLE else 3 if native == gp.GRB.UNBOUNDED else 1 if native in (gp.GRB.TIME_LIMIT, gp.GRB.ITERATION_LIMIT, gp.GRB.NODE_LIMIT, gp.GRB.SOLUTION_LIMIT, gp.GRB.INTERRUPTED) else 4
        has_x = model.SolCount > 0
        raw = np.asarray(x.X, dtype=float) if has_x else None
        is_mip = bool(model.IsMIP)
        bound = float(model.ObjBound) if is_mip else float(model.ObjVal) if status == 0 else None
        gap = float(model.MIPGap) if is_mip and has_x else 0. if status == 0 else None
        return OptimizeResult(status=status, x=raw, fun=float(model.ObjVal) if has_x else None,
            mip_dual_bound=bound, mip_gap=gap,
            mip_node_count=int(model.NodeCount) if is_mip else 0,
            native_status=native, message=f'Gurobi native status {native}', backend='gurobipy')

def _flatten(constraints, n):
    if constraints is None: return sparse.csr_matrix((0,n)), np.empty(0), np.empty(0)
    if hasattr(constraints, 'A'): constraints = [constraints]
    arrays=[]; lower=[]; upper=[]
    for constraint in constraints:
        a = sparse.csr_matrix(constraint.A)
        arrays.append(a)
        lower.append(np.broadcast_to(constraint.lb, (a.shape[0],)).copy())
        upper.append(np.broadcast_to(constraint.ub, (a.shape[0],)).copy())
    return sparse.vstack(arrays, format='csr'), np.concatenate(lower), np.concatenate(upper)

def _record(number,parameters):
    return dict(attempt=number,parameters=parameters,accepted=False,status=None,native_status=None,
        raw_x=None,validation=None,raw_objective=None,res_fun=None,mip_dual_bound=None,mip_gap=None,
        error_type=None,error=None)

def _evaluate(record,result,c,original,validate_vector,original_gap):
    raw=None if result.x is None else np.asarray(result.x,dtype=float).copy()
    record.update(status=int(result.status),native_status=getattr(result,'native_status',None),
        raw_x=None if raw is None else raw.tolist(),res_fun=getattr(result,'fun',None),
        mip_dual_bound=getattr(result,'mip_dual_bound',None),mip_gap=getattr(result,'mip_gap',None))
    if raw is not None:
        record['raw_objective']=float(c@raw)
        gap=getattr(result,'mip_gap',None);value=getattr(result,'fun',None)
        objective_consistent=value is not None and np.isfinite(value) and abs(float(value)-record['raw_objective'])<=1e-9*max(1.,abs(float(value)),abs(record['raw_objective']))
        compact=(zero_natural_bound_validation(c,original,raw) if result.status==0
            and objective_consistent and gap is not None and gap<=original_gap else None)
        validation=compact if compact is not None else validate_vector(original['matrix'],original['lower'],original['upper'],
            original['bounds_lower'],original['bounds_upper'],original['integrality'],raw,original['rows'])
        record['validation']=validation
        record['objective_consistent']=bool(objective_consistent)
        record['accepted']=bool(result.status==0 and original_validation_acceptable(validation)
            and objective_consistent and gap is not None and gap<=original_gap)

def milp(c, *, integrality=None, bounds=None, constraints=None, options=None, nonlinear=None):
    """Solve an unchanged linear domain with a finite, recorded native policy."""
    from .solver_diagnostics import validate_vector
    c = np.asarray(c, dtype=float).copy(); n = len(c)
    integrality = np.broadcast_to(0 if integrality is None else integrality, (n,)).copy()
    if np.any(~np.isin(integrality, [0,1])):
        raise ValueError('Hospital backend permits continuous and integer variables only')
    blo = np.zeros(n) if bounds is None else np.broadcast_to(bounds.lb, (n,)).astype(float).copy()
    bhi = np.full(n, np.inf) if bounds is None else np.broadcast_to(bounds.ub, (n,)).astype(float).copy()
    matrix, lower, upper = _flatten(constraints, n)
    problem = dict(c=c, integrality=integrality, bounds_lower=blo, bounds_upper=bhi,
        matrix=matrix, lower=lower, upper=upper)
    original = active_original_problem() or dict(matrix=matrix, lower=lower, upper=upper,
        bounds_lower=blo, bounds_upper=bhi, integrality=integrality,
        rows=[dict(kind='physical', name=f'row_{i}') for i in range(matrix.shape[0])])
    if nonlinear is not None:
        from .nonlinear import NonlinearSpec
        if type(nonlinear) is not NonlinearSpec:raise ValueError('Typed exact nonlinear graph required')
        nonlinear.validate_domain(blo,bhi)
        problem['nonlinear']=nonlinear
        original=dict(original,nonlinear=nonlinear)
    options = dict(options or {}); budget=float(options.get('time_limit', 6.))
    original_gap=float(options.get('mip_rel_gap', 1e-6))
    started=time.perf_counter(); attempts=[]; last=None
    nonlinear_evidence=None
    if nonlinear is not None:
        from .nonlinear import emit_nonlinear_domain
        nonlinear_evidence=emit_nonlinear_domain(problem,original,options)
    engineering=active_engineering_numerics()
    if engineering is not None and np.isfinite(budget) and budget>0.:
        replay=engineering.candidate(problem,original,validate_vector)
        if replay is not None:
            replay.numerical_version=ENGINEERING_NUMERICAL_VERSION
            replay.elapsed_seconds=time.perf_counter()-started
            replay.attempts[0]['elapsed_seconds']=replay.elapsed_seconds
            if nonlinear is not None:
                from .nonlinear import complete_nonlinear_call
                replay.nonlinear_domain_evidence=nonlinear_evidence
                replay.attempts[0]['nonlinear_domain_evidence']=nonlinear_evidence
                replay.attempts[0]['nonlinear_graph']=nonlinear.as_dict()
                complete_nonlinear_call(replay,nonlinear_evidence)
            return replay
    cache=active_cache() if nonlinear is None and engineering is None else None;key=None;source=None;policy_sha=None;call_id=None;rejection=None
    if cache is not None:
        call_id=cache.begin_call()
        code=getattr(_gurobi_attempt,'__code__',None)
        policy,policy_sha,source,policy_signature=cache.certificates(_gurobi_attempt,descriptor,_cache_policy_tokens())
        try:
            key=problem_key(problem,original,options,policy,source,(id(_gurobi_attempt),id(code)),policy_signature=policy_signature)
        except (TypeError,ValueError,OverflowError):cache.counts['key_unavailable']+=1
        if key is not None and np.isfinite(budget) and budget>0:
            candidate,rejection=cache.lookup(key)
            if candidate is not None:
                values,origin=candidate;tick=time.perf_counter()
                replay=_record(1,dict(_COMMON,**_RECOVERY[0],MIPGap=original_gap,TimeLimit=budget-(tick-started)))
                try:
                    result=OptimizeResult(values)
                    _evaluate(replay,result,c,original,validate_vector,original_gap)
                except Exception as error:replay.update(status=4,error_type=type(error).__name__,error=str(error))
                remaining=budget-(time.perf_counter()-started)
                if replay['accepted'] and remaining>0:
                    replay['parameters']['TimeLimit']=remaining
                    replay['elapsed_seconds']=time.perf_counter()-tick
                    replay['native_optimize_performed']=False
                    replay['result_cache_provenance']=provenance(cache,call_id,key,source,policy_sha,replay,origin=origin)
                    cache.counts['hits']+=1
                    result.attempts=[replay];result.backend='gurobipy';result.numerical_version=NUMERICAL_VERSION
                    result.elapsed_seconds=time.perf_counter()-started
                    return result
                rejection=dict(reason='current_original_validation_or_objective_rejected' if remaining>0 else 'current_budget_exhausted',
                    raw_x=replay['raw_x'],validation=replay['validation'],raw_objective=replay['raw_objective'],
                    objective_consistent=replay.get('objective_consistent'),error_type=replay['error_type'],error=replay['error'])
            if rejection is not None:cache.reject(key)
    for number, recovery in enumerate(_RECOVERY, start=1):
        remaining=budget-(time.perf_counter()-started)
        if remaining <= 0:
            break
        parameters=dict(_COMMON, **recovery, MIPGap=original_gap, TimeLimit=remaining)
        if engineering is not None:parameters.update(engineering_profile()['native_parameters'])
        record=_record(number,parameters)
        if engineering is not None:
            record.update(optimizer_called=True,engineering_profile=engineering_profile()['profile'],
                engineering_candidate_search=deepcopy(engineering.last_search))
        if nonlinear is not None:
            record['nonlinear_domain_evidence']=nonlinear_evidence
            record['nonlinear_graph']=nonlinear.as_dict()
            record['result_cache_policy']=dict(enabled=False,
                reason='legacy_exact_result_cache_does_not_certify_nonlinear_graphs',graph_identity=nonlinear.identity)
        if rejection is not None and number==1:record['result_cache_rejection']=rejection
        tick=time.perf_counter()
        try:
            if cache is not None:cache.counts['native_calls']+=1
            result=_gurobi_attempt(problem, parameters); last=result
            _evaluate(record,result,c,original,validate_vector,original_gap)
            if nonlinear is not None and getattr(result,'x',None) is not None:
                from .nonlinear import evaluate_nonlinear
                with engineering_numerics_scope(profile='exact'):
                    validation=evaluate_nonlinear(nonlinear,result.x,c,original)
                record['nonlinear_validation']=validation
                true_cost=validation['true_objective'];bound=getattr(result,'mip_dual_bound',None)
                native_cost=getattr(result,'fun',None)
                objective_tolerance=1e-9*max(1.,abs(true_cost),abs(native_cost or 0.))
                consistent=native_cost is not None and abs(true_cost-native_cost)<=objective_tolerance
                true_gap=(max(0.,true_cost-bound)/max(abs(true_cost),1e-10) if bound is not None and np.isfinite(bound) else None)
                validation.update(true_objective_consistent=bool(consistent),true_global_bound=bound,
                    true_relative_gap=true_gap,original_relative_gap_limit=original_gap)
                record['accepted']=bool(record['accepted'] and original_validation_acceptable(validation) and consistent
                    and true_gap is not None and true_gap<=original_gap and bound<=true_cost+objective_tolerance)
        except Exception as error:
            record.update(accepted=False,status=4,error_type=type(error).__name__,error=str(error))
        record['elapsed_seconds']=time.perf_counter()-tick
        if cache is not None:
            record['native_optimize_performed']=True
            record['result_cache_provenance']=provenance(cache,call_id,key,source,policy_sha,record)
        attempts.append(record)
        if record['accepted']:
            if engineering is not None:
                certificate=engineering.certificate(problem,original,record['validation'],'native_optimization',
                    getattr(result,'mip_dual_bound',None),record['raw_objective'])
                record.update(engineering_acceptance=certificate,optimizer_called=True,
                    engineering_model_reused=bool(getattr(result,'engineering_model_reused',False)))
                result.engineering_acceptance=certificate;result.optimizer_called=True
                engineering.remember(problem,original,result)
            if cache is not None and key is not None and number==1 and np.isfinite(budget) and budget>0 \
                    and getattr(result,'native_status',None)==gp.GRB.OPTIMAL and getattr(result,'backend',None)=='gurobipy':
                cache.put(key,result,record['result_cache_provenance'])
            result.attempts=attempts; result.backend='gurobipy'; result.numerical_version=(
                ENGINEERING_NUMERICAL_VERSION if engineering is not None else NUMERICAL_VERSION)
            if nonlinear is not None:result.nonlinear_domain_evidence=nonlinear_evidence
            result.elapsed_seconds=time.perf_counter()-started
            if nonlinear is not None:
                from .nonlinear import complete_nonlinear_call
                complete_nonlinear_call(result,nonlinear_evidence)
            return result
    if attempts and len(attempts)==2 and all(a['status']==2 for a in attempts):
        status=2; raw=None
    elif attempts and all(a['status']==3 for a in attempts):
        status=3; raw=None
    elif time.perf_counter()-started>=budget and all(a['status']==1 for a in attempts):
        status=1; raw=None if last is None else getattr(last,'x',None)
    else:
        status=4; raw=None if last is None else getattr(last,'x',None)
    result=OptimizeResult(status=status, x=raw, fun=None if last is None else getattr(last,'fun',None),
        mip_dual_bound=None if last is None else getattr(last,'mip_dual_bound',None),
        mip_gap=None if last is None else getattr(last,'mip_gap',None),
        mip_node_count=None if last is None else getattr(last,'mip_node_count',None),
        message='Native finite recovery exhausted; see raw attempts', backend='gurobipy',
        numerical_version=ENGINEERING_NUMERICAL_VERSION if engineering is not None else NUMERICAL_VERSION,
        attempts=attempts, elapsed_seconds=time.perf_counter()-started,
        **({'nonlinear_domain_evidence':nonlinear_evidence} if nonlinear is not None else {}))
    if nonlinear is not None:
        from .nonlinear import complete_nonlinear_call
        complete_nonlinear_call(result,nonlinear_evidence)
    return result
