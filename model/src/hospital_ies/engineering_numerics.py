"""Opt-in volatile numerical acceleration; physical state is never cached.

Candidate coordinates are only proposals. Every reuse checks CURRENT original
rows, bounds, integrality and analytic SF2 equalities. A natural box lower bound
or a newly evaluated LP dual witness proves the current objective quality.
No old objective bound, rounded physical state or native OPTIMAL status is reused.
"""
from __future__ import annotations
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import hashlib
import json
import os
import threading
import time

import gurobipy as gp
import numpy as np
from scipy.optimize import OptimizeResult


_ACTIVE = ContextVar('hospital_ies_engineering_numerics', default=None)
ZERO_VALIDATION_SCHEMA = 'hospital_ies.engineering_current_domain_validation_summary.v1'
_EXACT = dict(physical_rows_bounds=1e-7, normalized_locks=1e-10,
              integrality=1e-9, nonlinear_equality_MMBtu_h=1e-7)
_PROFILE = dict(schema='hospital_ies.engineering_numerical_profile.v1',
    profile='engineering_fast_v1',
    tolerances=dict(physical_rows_bounds=1e-7, normalized_locks=1e-10,
        integrality=1e-9, nonlinear_equality_MMBtu_h=1e-7),
    native_parameters=dict(FeasibilityTol=1e-9, OptimalityTol=1e-9, IntFeasTol=1e-9),
    native_execution_policy='unchanged_fresh_native_no_warm_start',
    native_acceptance_policy='unchanged_original',
    candidate_gate=dict(physical_rows_bounds=1e-7,normalized_locks=1e-10,integrality=1e-9),
    objective_bound_abs_tolerance=1e-10,
    mip_gap_policy='caller_gap_unchanged_for_every_layer',
    candidate_policy='linear_nonnegative_exact_zero_natural_bound_and_exact_zero_objective_exact_original_gate_only',
    nonlinear_policy='unchanged_exact_nonlinear_fresh_native_no_candidate_reuse',
    state_policy='process_thread_local_volatile_not_checkpointed_no_physical_rounding',
    initialization_policy='unchanged_exact_reference_before_engineering_execution',
    planner_policy='unchanged_exact_command_generation',
    validation_detail_policy='zero_natural_bound_current_domain_maxima_else_full_rows',
    max_native_models=32, max_candidate_families=256, candidates_per_family=3)


def engineering_profile(profile='engineering_fast_v1'):
    if profile != 'engineering_fast_v1':
        raise ValueError('Unknown engineering numerical profile')
    return deepcopy(_PROFILE)


def active_engineering_numerics():
    state = _ACTIVE.get()
    if state is not None:
        state.check_owner()
    return state


def numerical_tolerances():
    return dict(_PROFILE['tolerances'] if active_engineering_numerics() is not None else _EXACT)


def numerical_validation_acceptable(validation):
    if validation is None:
        return False
    tol = numerical_tolerances()
    check = validation.get('true_validation', validation)
    if 'true_validation' in validation:
        residual = validation.get('raw_nonlinear_max_residual', np.inf)
        if not np.isfinite(residual) or residual > tol['nonlinear_equality_MMBtu_h']:
            return False
    values=np.asarray([check['max_original_violation'],check['max_lock_violation'],
                       check['max_integrality_distance']],dtype=float)
    if not np.isfinite(values).all() or np.any(values<0.):return False
    return bool(check['max_original_violation'] <= tol['physical_rows_bounds']
        and check['max_lock_violation'] <= tol['normalized_locks']
        and check['max_integrality_distance'] <= tol['integrality'])


def original_validation_acceptable(validation):
    """Production native/candidate acceptance keeps the unchanged exact gate."""
    if validation is None:return False
    check=validation.get('true_validation',validation)
    if 'true_validation' in validation:
        residual=validation.get('raw_nonlinear_max_residual',np.inf)
        if not np.isfinite(residual) or residual<0. or residual>_EXACT['nonlinear_equality_MMBtu_h']:return False
    return all(np.isfinite(check.get(key,np.inf)) and 0.<=check[key]<=limit for key,limit in (
        ('max_original_violation',_EXACT['physical_rows_bounds']),
        ('max_lock_violation',_EXACT['normalized_locks']),('max_integrality_distance',_EXACT['integrality'])))


def _hash(*values):
    h = hashlib.sha256()
    for value in values:
        if isinstance(value, np.ndarray):
            a = np.ascontiguousarray(value)
            h.update(str((a.shape, a.dtype.str)).encode('ascii')); h.update(a.tobytes())
        else:
            h.update(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8'))
    return h.hexdigest()


def _masks(problem):
    lo, hi = problem['lower'], problem['upper']
    eq = np.isfinite(lo) & np.isfinite(hi) & (lo == hi)
    low = np.isfinite(lo) & ~eq; high = np.isfinite(hi) & ~eq
    return eq, low, high


def _structure_key(problem):
    a = problem['matrix']; eq, low, high = _masks(problem)
    nl = problem.get('nonlinear')
    return _hash(a.shape, a.indptr, a.indices, problem['integrality'], eq, low, high,
                 None if nl is None else nl.identity)


def _family_key(problem):
    integer = np.asarray(problem['integrality'], dtype=bool)
    nl = problem.get('nonlinear')
    return _hash(problem['c'], problem['integrality'],
        problem['bounds_lower'][integer], problem['bounds_upper'][integer],
        None if nl is None else nl.identity)


def _natural_bound(c, original):
    indices = np.flatnonzero(c)
    if not len(indices): return 0.
    extremes = np.where(c[indices] >= 0., original['bounds_lower'][indices], original['bounds_upper'][indices])
    if not np.isfinite(extremes).all(): return None
    value = float(c[indices] @ extremes)
    return value if np.isfinite(value) else None


def _check_current_domain(problem):
    """Unbounded sides are legal; NaN and wrong-direction infinity are not."""
    a=problem['matrix'];m,n=a.shape
    c=np.asarray(problem.get('c',np.zeros(n)))
    integral=np.asarray(problem['integrality'])
    if (not np.isfinite(a.data).all() or c.shape!=(n,) or not np.isfinite(c).all()
            or integral.shape!=(n,) or not np.isin(integral,(0,1)).all()
            or ('rows' in problem and len(problem['rows'])!=m)):
        raise ValueError('Invalid current engineering domain matrix/objective/integrality/metadata')
    for low,high,size in ((problem['lower'],problem['upper'],m),
                          (problem['bounds_lower'],problem['bounds_upper'],n)):
        low=np.asarray(low);high=np.asarray(high)
        if (low.shape!=(size,) or high.shape!=(size,) or np.isnan(low).any() or np.isnan(high).any()
                or np.isposinf(low).any() or np.isneginf(high).any() or np.any(low>high)):
            raise ValueError('Invalid current engineering domain row/variable bounds')


def _current_domain_summary(original, value):
    """Check every current row/bound without allocating per-row log dictionaries."""
    if not np.isfinite(value).all(): raise ValueError('Non-finite performance candidate')
    activity = np.asarray(original['matrix'] @ value).ravel()
    if not np.isfinite(activity).all():raise ValueError('Non-finite current engineering domain activity')
    violation = np.maximum(0., np.maximum(original['lower'] - activity, activity - original['upper']))
    bounds = np.maximum(0., np.maximum(original['bounds_lower'] - value, value - original['bounds_upper']))
    lock = np.fromiter((r.get('kind') == 'priority_lock' for r in original['rows']), dtype=bool,
        count=len(original['rows']))
    integral = value[np.asarray(original['integrality'], dtype=bool)]
    physical_max=float(violation[~lock].max(initial=0.));bound_max=float(bounds.max(initial=0.))
    scale=np.maximum(1.,np.maximum(abs(activity),np.maximum(
        np.where(np.isfinite(original['lower']),abs(original['lower']),0.),
        np.where(np.isfinite(original['upper']),abs(original['upper']),0.))))
    return dict(max_original_violation=max(physical_max,bound_max),max_physical_row_violation=physical_max,
        max_bound_violation=bound_max,max_row_violation=float(violation.max(initial=0.)),
        max_scaled_row_violation=float((violation/scale).max(initial=0.)),
        max_lock_violation=float(violation[lock].max(initial=0.)),
        max_integrality_distance=float(np.abs(integral - np.rint(integral)).max(initial=0.)),
        original_rows_checked=int(original['matrix'].shape[0]), original_variables_checked=len(value))


def zero_natural_bound_validation(c,original,value,*,checked=None):
    """Accepted linear zero objectives may omit row dictionaries, never checks."""
    if active_engineering_numerics() is None or original.get('nonlinear') is not None:return None
    c=np.asarray(c);objective=float(c@value);bound=_natural_bound(c,original)
    tolerance=_PROFILE['objective_bound_abs_tolerance']
    if (not np.all(c>=0.) or not np.isfinite(objective) or bound is None
            or abs(bound)>tolerance or abs(objective)>tolerance):return None
    summary=_current_domain_summary(original,value) if checked is None else dict(checked)
    if not original_validation_acceptable(summary):return None
    summary.update(schema=ZERO_VALIDATION_SCHEMA,validation_detail='current_full_domain_maxima_only',
        all_current_rows_checked=True,row_count=int(original['matrix'].shape[0]),variable_count=len(value),
        raw_rows_retained=False,raw_rows_recoverable=False,
        objective_coefficients_nonnegative=True,nonlinear_domain=False,
        current_objective=objective,current_natural_lower_bound=bound,
        validation_thresholds=numerical_tolerances(),
        effective_validation_thresholds=dict(_PROFILE['candidate_gate']),
        current_domain_sha256=_domain_sha(dict(c=c),original))
    return summary


def _dual_bound(c, original, witness):
    """Any sign-feasible row multipliers + current box bounds give a valid LB.

    Recompute residual reduced costs from CURRENT original A and c. This does
    not borrow the old ObjBound and does not fix a discrete combination.
    """
    eq, low, high = _masks(original)
    rows = witness['row_map']; senses = witness['senses']
    if original['matrix'].shape[0] != witness['original_row_count']:
        return None
    if rows.size and int(rows.max()) >= original['matrix'].shape[0]: return None
    if (np.any(~eq[rows[senses == '=']]) or np.any(~low[rows[senses == '>']])
            or np.any(~high[rows[senses == '<']])):
        return None
    pi = witness['pi'].copy()
    pi[senses == '>'] = np.maximum(0., pi[senses == '>'])
    pi[senses == '<'] = np.minimum(0., pi[senses == '<'])
    rhs = np.where(senses == '<', original['upper'][rows], original['lower'][rows])
    if not np.isfinite(rhs).all() or not np.isfinite(pi).all(): return None
    reduced = c - np.asarray(original['matrix'][rows].T @ pi).ravel()
    indices = np.flatnonzero(reduced)
    extremes = np.where(reduced[indices] >= 0., original['bounds_lower'][indices], original['bounds_upper'][indices])
    if not np.isfinite(extremes).all(): return None
    # Subtract a conservative arithmetic allowance, expressed in this objective's units.
    terms = np.concatenate((pi * rhs, reduced[indices] * extremes))
    value = float(np.sum(terms)); rounding = 64. * np.finfo(float).eps * max(1., float(np.abs(terms).sum()))
    return value - rounding if np.isfinite(value) else None


def _domain_sha(problem, original):
    a = original['matrix']; nl = problem.get('nonlinear')
    return _hash(a.shape, a.data, a.indices, a.indptr, original['lower'], original['upper'],
        original['bounds_lower'], original['bounds_upper'], original['integrality'], problem['c'],
        [r.get('kind', 'physical') for r in original['rows']], None if nl is None else nl.identity)


class EngineeringNumerics:
    def __init__(self):
        self.pid = os.getpid(); self.thread = threading.get_ident()
        self.models = OrderedDict(); self.candidates = OrderedDict(); self.duals = OrderedDict()
        self.closed = False
        self.last_search = None
        self.counts = {name: 0 for name in ('calls', 'native_calls', 'native_model_builds',
            'native_model_reuses', 'native_coefficient_updates', 'native_model_evictions',
            'native_warm_starts', 'nonlinear_native_calls', 'candidate_checks',
            'candidate_rejections', 'candidate_bound_misses', 'bound_skips', 'dual_bound_skips')}

    def check_owner(self):
        if self.closed or self.pid != os.getpid() or self.thread != threading.get_ident():
            raise RuntimeError('Engineering performance state cannot cross process/thread/scope ownership')

    def stats(self):
        return dict(self.counts, profile=_PROFILE['profile'], schema='hospital_ies.engineering_numerics_stats.v1',
            native_models_live=len(self.models), candidate_families=len(self.candidates),
            checkpointed=False, physical_state_cached=False, closed=self.closed)

    def close(self):
        for entry in self.models.values(): entry['model'].dispose()
        self.models.clear(); self.candidates.clear(); self.duals.clear(); self.closed = True

    def certificate(self, problem, original, validation, mode, lower_bound, objective):
        domain_sha=(validation['current_domain_sha256'] if validation.get('schema')==ZERO_VALIDATION_SCHEMA
            else _domain_sha(problem,original))
        return dict(schema='hospital_ies.engineering_acceptance.v1', profile=_PROFILE['profile'], mode=mode,
            proof_kind=('native_current_problem_bound' if mode == 'native_optimization' else mode),
            original_domain_checked=True, current_domain_sha256=domain_sha,
            validation_thresholds=numerical_tolerances(),
            effective_validation_thresholds=dict(_EXACT if mode=='native_optimization' else _PROFILE['candidate_gate']),
            validation_summary={k: validation[k] for k in ('max_original_violation', 'max_lock_violation', 'max_integrality_distance')},
            optimality=dict(lower_bound=lower_bound, objective=objective,
                gap_upper_bound=None if lower_bound is None else max(0., objective - lower_bound),
                objective_bound_abs_tolerance=_PROFILE['objective_bound_abs_tolerance'],
                native_optimal_status_claimed=mode == 'native_optimization',
                previous_problem_bound_used=False), physical_state_replaced=False)

    def candidate(self, problem, original, validate_vector):
        self.counts['calls'] += 1
        _check_current_domain(problem)
        if original is not problem:_check_current_domain(original)
        key = _family_key(problem); values = self.candidates.get(key, ())
        self.last_search = dict(family_found=bool(values), candidate_checks=0, rejections=[],
            bound_misses=0, lookup_uses_rounded_physical_values=False)
        natural = _natural_bound(problem['c'], original)
        if (problem.get('nonlinear') is not None or not np.all(problem['c']>=0.) or natural!=0.):
            self.last_search['reason']='outside_exact_zero_linear_candidate_class'
            return None
        for value in reversed(values):
            objective=float(problem['c']@value)
            if not np.isfinite(objective) or objective!=0.:
                self.counts['candidate_rejections']+=1
                self.last_search['rejections'].append(dict(reason='candidate_objective_not_exact_zero',
                    objective=None if not np.isfinite(objective) else objective))
                continue
            self.counts['candidate_checks'] += 1
            self.last_search['candidate_checks'] += 1
            try:
                check = _current_domain_summary(original, value)
            except (ValueError, TypeError, OverflowError) as error:
                self.counts['candidate_rejections'] += 1
                self.last_search['rejections'].append(dict(reason='candidate_validation_error',
                    error_type=type(error).__name__, error=str(error)))
                continue
            if not original_validation_acceptable(check):
                self.counts['candidate_rejections'] += 1
                self.last_search['rejections'].append(dict(reason='current_original_domain_rejected',
                    max_original_violation=check['max_original_violation'], max_lock_violation=check['max_lock_violation'],
                    max_integrality_distance=check['max_integrality_distance'],
                    nonlinear_residual=None))
                continue
            mode = 'current_domain_natural_bound'; bound = natural
            self.counts['bound_skips'] += 1
            compact=(zero_natural_bound_validation(problem['c'],original,value,checked=check)
                if mode=='current_domain_natural_bound' and problem.get('nonlinear') is None else None)
            check = compact if compact is not None else validate_vector(original['matrix'], original['lower'], original['upper'],
                original['bounds_lower'], original['bounds_upper'], original['integrality'], value, original['rows'])
            certificate = self.certificate(problem, original, check, mode, bound, objective)
            relative_gap = max(0., objective - bound) / max(abs(objective), 1e-10)
            record = dict(attempt=1, parameters={}, accepted=True, status=0, native_status=None,
                raw_x=value.tolist(), validation=check, raw_objective=objective, res_fun=objective,
                mip_dual_bound=bound, mip_gap=relative_gap, error_type=None, error=None,
                optimizer_called=False, engineering_acceptance=certificate, objective_consistent=True,
                engineering_candidate_search=deepcopy(self.last_search), elapsed_seconds=0.)
            return OptimizeResult(status=0, native_status=None, x=value.copy(), fun=objective,
                mip_dual_bound=bound, mip_gap=relative_gap, mip_node_count=0, backend='gurobipy', optimizer_called=False,
                message='Current original-domain candidate certified by current objective lower bound; native optimizer skipped',
                engineering_acceptance=certificate, attempts=[record])
        return None

    def remember(self, problem, original, result):
        if (problem.get('nonlinear') is not None or not np.all(problem['c']>=0.)
                or _natural_bound(problem['c'],original)!=0. or float(problem['c']@result.x)!=0.):return
        key = _family_key(problem); value = np.asarray(result.x, dtype=float).copy()
        values = self.candidates.pop(key, [])
        if not values or not np.array_equal(values[-1], value): values.append(value)
        self.candidates[key] = values[-_PROFILE['candidates_per_family']:]
        while len(self.candidates) > _PROFILE['max_candidate_families']: self.candidates.popitem(last=False)
        witness = getattr(result, 'engineering_dual_witness', None)
        if witness is not None and problem.get('nonlinear') is None:
            # pi from positive row scaling remains a sign-feasible proposal;
            # current original reduced costs are rebuilt before every proof.
            witness = dict(witness, original_row_count=original['matrix'].shape[0])
            self.duals[_structure_key(problem)] = witness
            while len(self.duals) > _PROFILE['max_native_models']: self.duals.popitem(last=False)

    def native_attempt(self, problem, parameters, environment):
        # Quarantined experimental model-pool path. Production backend does
        # not call this method: all actual native solves remain fresh/exact.
        self.check_owner(); built_at = time.perf_counter(); self.counts['native_calls'] += 1
        key = _structure_key(problem); entry = self.models.get(key)
        if entry is not None: self.models.move_to_end(key)
        eq, low, high = _masks(problem)
        row_map = np.concatenate((np.flatnonzero(eq), np.flatnonzero(low), np.flatnonzero(high)))
        senses = np.concatenate((np.full(eq.sum(), '='), np.full(low.sum(), '>'), np.full(high.sum(), '<')))
        rhs = np.concatenate((problem['lower'][eq], problem['lower'][low], problem['upper'][high]))
        expanded = problem['matrix'][row_map].tocsr()
        reused = entry is not None
        if not reused:
            model = gp.Model(env=environment)
            kinds = np.where(problem['integrality'] == 1, gp.GRB.INTEGER, gp.GRB.CONTINUOUS)
            x = model.addMVar(len(problem['c']), lb=problem['bounds_lower'], ub=problem['bounds_upper'], vtype=kinds)
            mc = model.addMConstr(expanded, x, senses, rhs) if len(row_map) else None
            nl = problem.get('nonlinear')
            if nl is not None:
                if gp.gurobi.version()[0] < 13: model.dispose(); raise RuntimeError('SF2 requires native Gurobi13')
                model.setParam('NonConvex', 2)
                for i, item in enumerate(nl.equalities):
                    model.addGenConstrNL(x[item.output_index].item(),
                        item.coefficient * (x[item.input_index].item() ** item.exponent), name=f'SF2_fuel_{i}')
            model.update()
            entry = dict(model=model, x=x, mc=mc, data=expanded.data.copy(), previous=None,
                vars=x.tolist(), constrs=[] if mc is None else mc.tolist(),
                coo_rows=np.repeat(np.arange(expanded.shape[0]), np.diff(expanded.indptr)), coo_cols=expanded.indices.copy())
            self.counts['native_model_builds'] += 1
        else:
            self.counts['native_model_reuses'] += 1
            model, x, mc = entry['model'], entry['x'], entry['mc']
            x.LB = problem['bounds_lower']; x.UB = problem['bounds_upper']
            if mc is not None: mc.RHS = rhs
            changed = np.flatnonzero(entry['data'] != expanded.data)
            if len(changed):
                for i in changed:
                    model.chgCoeff(entry['constrs'][int(entry['coo_rows'][i])],
                        entry['vars'][int(entry['coo_cols'][i])], float(expanded.data[i]))
                self.counts['native_coefficient_updates'] += len(changed); entry['data'] = expanded.data.copy()
        self.models[key] = entry
        while len(self.models) > _PROFILE['max_native_models']:
            _, old = self.models.popitem(last=False); old['model'].dispose(); self.counts['native_model_evictions'] += 1
        for name, value in parameters.items(): model.setParam(name, value)
        x.Obj = problem['c']; model.ModelSense = gp.GRB.MINIMIZE
        if entry['previous'] is not None and bool(np.any(problem['integrality'])):
            x.Start = entry['previous']; self.counts['native_warm_starts'] += 1
        if problem.get('nonlinear') is not None: self.counts['nonlinear_native_calls'] += 1
        remaining = float(parameters['TimeLimit']) - (time.perf_counter() - built_at)
        if remaining <= 0.: raise TimeoutError('Engineering current native budget exhausted during construction/update')
        model.setParam('TimeLimit', remaining); model.optimize()
        native = int(model.Status)
        status = (0 if native == gp.GRB.OPTIMAL else 2 if native == gp.GRB.INFEASIBLE else
            3 if native == gp.GRB.UNBOUNDED else 1 if native in (gp.GRB.TIME_LIMIT, gp.GRB.ITERATION_LIMIT,
            gp.GRB.NODE_LIMIT, gp.GRB.SOLUTION_LIMIT, gp.GRB.INTERRUPTED) else 4)
        has_x = model.SolCount > 0; raw = np.asarray(x.X, dtype=float) if has_x else None
        is_mip = bool(model.IsMIP); bound = float(model.ObjBound) if is_mip else float(model.ObjVal) if status == 0 else None
        gap = float(model.MIPGap) if is_mip and has_x else 0. if status == 0 else None
        if raw is not None: entry['previous'] = raw.copy()
        result = OptimizeResult(status=status, x=raw, fun=float(model.ObjVal) if has_x else None,
            mip_dual_bound=bound, mip_gap=gap, mip_node_count=int(model.NodeCount) if is_mip else 0,
            native_status=native, message=f'Gurobi engineering native status {native}', backend='gurobipy',
            optimizer_called=True, engineering_model_reused=reused)
        if status == 0 and not is_mip and problem.get('nonlinear') is None:
            result.engineering_dual_witness = dict(row_map=row_map, senses=senses,
                pi=np.empty(0) if mc is None else np.asarray(mc.Pi, dtype=float))
        return result


@contextmanager
def engineering_numerics_scope(profile='engineering_fast_v1'):
    if profile == 'exact':
        token = _ACTIVE.set(None)
        try: yield None
        finally: _ACTIVE.reset(token)
        return
    engineering_profile(profile)
    state = EngineeringNumerics(); token = _ACTIVE.set(state)
    try: yield state
    finally:
        _ACTIVE.reset(token); state.close()


def engineering_numerics_stats():
    state = active_engineering_numerics()
    return None if state is None else state.stats()


def unchanged_exact_command_generation(function):
    """Keep nonunique native planner choices out of the leaf performance cache.

    Equal scalar objectives do not imply equal emitted commands. Exact planner
    calls are deliberately outside engineering native_calls/skip counters.
    """
    @wraps(function)
    def run(*args, **kwargs):
        if active_engineering_numerics() is None:
            return function(*args, **kwargs)
        with engineering_numerics_scope(profile='exact'):
            return function(*args, **kwargs)
    return run
