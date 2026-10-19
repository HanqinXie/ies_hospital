"""Local native solver for the five separately locked Hospital IES layers.

The adapter retains the scipy-shaped injection point, but does not call scipy.
Every attempt is validated in the original physical and priority-lock units.
Two uniform numerical configurations share the caller's original time budget;
neither previous incumbents nor scenario-specific fallback rules are used.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time
import numpy as np
from scipy import sparse
from scipy.optimize import OptimizeResult
import gurobipy as gp

NUMERICAL_VERSION = 'integrated.gurobi.explicit_lex.v1'
_ORIGINAL = ContextVar('hospital_ies_original_solver_domain', default=None)
_LOCAL = threading.local()
_COMMON = dict(OutputFlag=0, Threads=1, Method=1, Seed=0,
    DualReductions=0, FeasibilityTol=1e-9, IntFeasTol=1e-9,
    OptimalityTol=1e-9, MIPGap=1e-6, MIPGapAbs=0.)
_RECOVERY = (dict(NumericFocus=1, Presolve=2), dict(NumericFocus=3, Presolve=0))
_VALIDATION = dict(physical_rows_bounds=1e-7, normalized_locks=1e-10, integrality=1e-9)

def descriptor():
    return dict(backend='gurobipy', version=list(gp.gurobi.version()),
        numerical_version=NUMERICAL_VERSION, explicit_lex_layers=5,
        max_attempts_per_layer=2, parameters=dict(_COMMON),
        recovery_order=[dict(x) for x in _RECOVERY], raw_validation=dict(_VALIDATION),
        time_budget='shared original per-layer limit; model construction included',
        objective_units='unchanged', remote_services=False)

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
    with gp.Model(env=_environment()) as model:
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

def milp(c, *, integrality=None, bounds=None, constraints=None, options=None):
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
    options = dict(options or {}); budget=float(options.get('time_limit', 6.))
    original_gap=float(options.get('mip_rel_gap', 1e-6))
    started=time.perf_counter(); attempts=[]; last=None
    for number, recovery in enumerate(_RECOVERY, start=1):
        remaining=budget-(time.perf_counter()-started)
        if remaining <= 0:
            break
        parameters=dict(_COMMON, **recovery, MIPGap=original_gap, TimeLimit=remaining)
        record=dict(attempt=number, parameters=parameters, accepted=False, status=None,
            native_status=None, raw_x=None, validation=None, raw_objective=None,
            res_fun=None, mip_dual_bound=None, mip_gap=None, error_type=None, error=None)
        tick=time.perf_counter()
        try:
            result=_gurobi_attempt(problem, parameters); last=result
            raw=None if result.x is None else np.asarray(result.x, dtype=float).copy()
            record.update(status=int(result.status), native_status=getattr(result,'native_status',None),
                raw_x=None if raw is None else raw.tolist(), res_fun=getattr(result,'fun',None),
                mip_dual_bound=getattr(result,'mip_dual_bound',None), mip_gap=getattr(result,'mip_gap',None))
            if raw is not None:
                validation=validate_vector(original['matrix'],original['lower'],original['upper'],
                    original['bounds_lower'],original['bounds_upper'],original['integrality'],raw,original['rows'])
                record['validation']=validation; record['raw_objective']=float(c@raw)
                gap=getattr(result,'mip_gap',None)
                value=getattr(result,'fun',None)
                objective_consistent=value is not None and np.isfinite(value) and abs(float(value)-record['raw_objective'])<=1e-9*max(1.,abs(float(value)),abs(record['raw_objective']))
                record['objective_consistent']=bool(objective_consistent)
                record['accepted']=bool(result.status==0 and validation['max_original_violation']<=1e-7
                    and validation['max_lock_violation']<=1e-10 and validation['max_integrality_distance']<=1e-9
                    and objective_consistent and gap is not None and gap<=original_gap)
        except Exception as error:
            record.update(status=4,error_type=type(error).__name__,error=str(error))
        record['elapsed_seconds']=time.perf_counter()-tick
        attempts.append(record)
        if record['accepted']:
            result.attempts=attempts; result.backend='gurobipy'; result.numerical_version=NUMERICAL_VERSION
            result.elapsed_seconds=time.perf_counter()-started
            return result
    if attempts and len(attempts)==2 and all(a['status']==2 for a in attempts):
        status=2; raw=None
    elif attempts and all(a['status']==3 for a in attempts):
        status=3; raw=None
    elif time.perf_counter()-started>=budget and all(a['status']==1 for a in attempts):
        status=1; raw=None if last is None else getattr(last,'x',None)
    else:
        status=4; raw=None if last is None else getattr(last,'x',None)
    return OptimizeResult(status=status, x=raw, fun=None if last is None else getattr(last,'fun',None),
        mip_dual_bound=None if last is None else getattr(last,'mip_dual_bound',None),
        mip_gap=None if last is None else getattr(last,'mip_gap',None),
        mip_node_count=None if last is None else getattr(last,'mip_node_count',None),
        message='Native finite recovery exhausted; see raw attempts', backend='gurobipy',
        numerical_version=NUMERICAL_VERSION, attempts=attempts, elapsed_seconds=time.perf_counter()-started)
