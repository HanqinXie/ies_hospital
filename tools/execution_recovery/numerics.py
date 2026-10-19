"""Finite command representation changes inside the unchanged original domain.

The emitted finalizer changes only declared coordinates at their own boundary.
Separate failed-layer refinement is finite, same-integer, preserves critical
service coordinates, and must pass the original domain/objective contracts.
Neither path widens feasibility thresholds or applies a blanket projection.
"""
from __future__ import annotations

import hashlib
import math
import copy

import numpy as np

from hospital_ies.solver_diagnostics import flatten_constraints, validate_vector, json_value
from tools.operating_response.readiness import validate_dispatch, DISPATCH_FIELDS

NUMERICAL_CONTRACT_ID = 'hospital_ies.bounded_emitted_command_finalization.v18.v1'
ORIGINAL_TOLERANCE = 1e-7
LOCK_TOLERANCE = 1e-10
INTEGER_TOLERANCE = 1e-9
EMITTED_BOUNDARY_BUDGET = 1e-7
EXECUTION_LIMIT_FIELDS = frozenset(('schema', 'hardware_grid_available_fraction', 'grid_bus_state',
    'effective_grid_import_limit_kW', 'effective_diesel_limit_kW', 'diesel_conditionally_permitted',
    'grid_transfer_remaining_minutes', 'gt_protection_reference_kW', 'gt_trip_multiple',
    'gt_hold_minutes', 'gt_overload_minutes'))


def validate_execution_limits(value):
    """Accept only the causal common-model interface, never future scripts."""
    if not isinstance(value,dict):
        raise ValueError('Invalid strict execution limits fields/schema')
    version=value.get('schema')
    fields=EXECUTION_LIMIT_FIELDS if version=='hospital_ies.execution_limits.v18.v1' else EXECUTION_LIMIT_FIELDS|{'effective_dispatch'}
    if version not in ('hospital_ies.execution_limits.v18.v1','hospital_ies.execution_limits.v18.v2') or set(value)!=fields:
        raise ValueError('Invalid strict execution limits fields/schema')
    result = copy.deepcopy(value)
    for key in ('hardware_grid_available_fraction', 'effective_grid_import_limit_kW',
                'effective_diesel_limit_kW', 'gt_protection_reference_kW', 'gt_trip_multiple', 'gt_hold_minutes'):
        val = value[key]
        if isinstance(val, bool) or not isinstance(val, (int, float, np.number)) or not math.isfinite(val) or val < 0.:
            raise ValueError('Invalid execution limits number: ' + key)
        result[key] = float(val)
    if result['hardware_grid_available_fraction'] > 1. or min(result['gt_protection_reference_kW'], result['gt_trip_multiple'], result['gt_hold_minutes']) <= 0.:
        raise ValueError('Invalid execution limits physical references')
    if value['grid_bus_state'] not in ('GRID', 'ISLAND', 'RETURN_PREP', 'TRANSFER_OPEN') or type(value['diesel_conditionally_permitted']) is not bool:
        raise ValueError('Invalid execution limits connection state')
    timer = value['grid_transfer_remaining_minutes']
    if timer is not None and (isinstance(timer, bool) or not isinstance(timer, (int, float)) or not math.isfinite(timer) or timer < 0.):
        raise ValueError('Invalid execution limits transfer timer')
    result['gt_overload_minutes'] = _numeric_vector(value['gt_overload_minutes'], (3,), 'execution limits overload clocks').tolist()
    if min(result['gt_overload_minutes']) < 0.:
        raise ValueError('Invalid execution limits overload clocks')
    grid, diesel = result['effective_grid_import_limit_kW'], result['effective_diesel_limit_kW']
    if (grid > 0. and diesel > 0.) or (value['grid_bus_state'] != 'GRID' and grid > 0.) or (value['grid_bus_state'] == 'TRANSFER_OPEN' and diesel > 0.):
        raise ValueError('Invalid execution limits topology: simultaneous or open-transfer supply')
    if not value['diesel_conditionally_permitted'] and diesel > 0.:
        raise ValueError('Invalid execution limits diesel capability without eligibility')
    if version=='hospital_ies.execution_limits.v18.v2':
        effective=value['effective_dispatch']
        if not isinstance(effective,dict) or set(effective)!=DISPATCH_FIELDS:
            raise ValueError('Invalid execution limits canonical effective dispatch fields')
        result['effective_dispatch']=validate_dispatch(effective)
        if result['effective_dispatch']['grid_import_kW']!=grid or result['effective_dispatch']['diesel_kW']!=diesel:
            raise ValueError('Inconsistent execution limits effective dispatch grid/diesel caps')
    return result


def effective_held_dispatch(raw_dispatch,*,execution_limits,availability,plant,grid_import_limit_kW):
    """Current actuator limits/fixed boiler setting, never sampled delivery.

    V1 mathematical fixtures lack the current actuator snapshot. Their fallback
    masks only the held limits consumed by these planners; unmeasured source
    fields remain explicitly identified rather than synthesized as actual ports.
    """
    raw=copy.deepcopy(raw_dispatch or {})
    meta=dict(represents_actual_delivery=False,raw_permission_ledger_unchanged=True)
    if execution_limits is None:
        return raw,dict(meta,source='legacy_raw_dispatch_no_m18_limits',unsampled_source_fields=list(raw))
    limits=validate_execution_limits(execution_limits)
    if 'effective_dispatch' in limits:
        return copy.deepcopy(limits['effective_dispatch']),dict(meta,
            source='v2_observed_effective_actuator_dispatch',unsampled_source_fields=[],
            actual_GT_DG_ports_must_use_separate_observations=True)
    from hospital_ies.model import RT_TO_KW
    # Validate provided values without turning missing fixture fields into
    # invented sampled source measurements in the returned mapping.
    validate_dispatch({**dict(gt_electric_kW=[0.]*3,grid_import_kW=0.,diesel_kW=0.),**raw})
    held=copy.deepcopy(raw)
    cap=dict(boiler_steam_lbph=plant.boiler_steam_lbph/3*sum(availability[f'BOILER_{i+1}'] for i in range(3)),
        absorption_cooling_kW=plant.absorption_rt*RT_TO_KW*availability['ABS'],
        grid_import_kW=min(grid_import_limit_kW*availability['GRID'],limits['effective_grid_import_limit_kW']),
        diesel_kW=min(plant.emergency_diesel_kW*availability['DIESEL'],limits['effective_diesel_limit_kW']))
    for key,value in cap.items():
        if key in held:held[key]=min(float(held[key]),float(value))
    if 'electric_chiller_cooling_kW' in held:
        held['electric_chiller_cooling_kW']=[min(float(value),float(plant.electric_chillers_rt[i]*RT_TO_KW*availability[f'EC_{i+1}']))
            for i,value in enumerate(held['electric_chiller_cooling_kW'])]
    return held,dict(meta,source='v1_hardware_masked_permission_fallback',
        unsampled_source_fields=[key for key in ('gt_electric_kW','hrsg_steam_lbph','tower_rejection_kW','gt_fuel_MMBtu_h') if key in raw],
        missing_permission_fields=sorted(DISPATCH_FIELDS-set(raw)),
        actual_GT_DG_ports_must_use_separate_observations=True)


def _numeric_vector(value, shape, name, *, finite=True):
    data = np.asarray(value)
    if data.shape != shape or data.dtype.kind not in 'fiu':
        raise ValueError(name + ' has wrong shape or nonnumeric values')
    if (finite and not np.isfinite(data).all()) or (not finite and np.isnan(data).any()):
        raise ValueError(name + ' must contain finite values (bounds may use infinity)')
    return data.astype(float, copy=True)


def _accepted(validation):
    return (validation['max_original_violation'] <= ORIGINAL_TOLERANCE
        and validation['max_lock_violation'] <= LOCK_TOLERANCE
        and validation['max_integrality_distance'] <= INTEGER_TOLERANCE)


def finalize_command_vector(x, *, constraints, lower, upper, integrality, rows,
                            variables, emitted_coordinates, build_command, objectives=()):
    """Return corrected original vector, exactly validated dispatch and evidence.

    ``emitted_coordinates`` maps original coordinate indices to the largest
    absolute unit-conversion factor used when emitting that coordinate. Every
    coordinate is subject to BOTH native and emitted absolute residual budgets.
    Objective rows/locks are inputs; this function never constructs new locks.
    """
    n = len(lower)
    raw = _numeric_vector(x, (n,), 'candidate')
    lo = _numeric_vector(lower, (n,), 'lower bounds', finite=False)
    hi = _numeric_vector(upper, (n,), 'upper bounds', finite=False)
    integer = _numeric_vector(integrality, (n,), 'integrality')
    if np.any(lo > hi) or np.any(integer != np.rint(integer)) or np.any((integer < 0) | (integer > 3)):
        raise ValueError('Invalid original domain bounds or integrality')
    matrix, row_low, row_high = flatten_constraints(constraints)
    if matrix.shape[1] != n or len(rows) != matrix.shape[0] or len(variables) != n:
        raise ValueError('Original domain metadata shape mismatch')
    if not np.isfinite(matrix.data).all() or np.isnan(row_low).any() or np.isnan(row_high).any():
        raise ValueError('Original domain has nonfinite matrix or invalid rows')
    if not isinstance(emitted_coordinates, dict):
        raise ValueError('Emitted coordinate map must be explicit')
    candidate = raw.copy()
    corrections = []
    for index, scale in sorted(emitted_coordinates.items()):
        if type(index) is not int or not 0 <= index < n:
            raise ValueError('Invalid emitted coordinate index')
        if isinstance(scale, bool) or not isinstance(scale, (int, float, np.number)) or not math.isfinite(scale) or scale <= 0.:
            raise ValueError('Invalid emitted unit conversion')
        value = float(raw[index])
        boundary = lo[index] if value < lo[index] else hi[index] if value > hi[index] else None
        if boundary is None:
            continue
        delta = float(boundary - value)
        if abs(delta) > ORIGINAL_TOLERANCE or abs(delta * scale) > EMITTED_BOUNDARY_BUDGET:
            raise ValueError(f'Emitted boundary residual exceeds finite budget at coordinate {index}')
        candidate[index] = boundary
        variable = variables[index]
        corrections.append(dict(index=index, name=variable.get('name', f'x{index}'),
            native_unit=variable.get('unit', 'original'), raw=value, corrected=float(boundary), delta=delta,
            emitted_units_per_native_unit=float(scale), emitted_raw=float(value * scale),
            emitted_corrected=float(boundary * scale), emitted_delta=float(delta * scale)))
    raw_validation = validate_vector(matrix, row_low, row_high, lo, hi, integer, raw, rows)
    validation = validate_vector(matrix, row_low, row_high, lo, hi, integer, candidate, rows)
    if not _accepted(raw_validation) or not _accepted(validation):
        error = ValueError('Command finalization violates the unchanged original domain')
        error.numerical_finalization = dict(corrections=corrections,
            raw_validation=raw_validation, validation=validation)
        raise error
    action = validate_dispatch(build_command(candidate.copy()))
    deltas = []
    for name, coefficients in objectives:
        co = _numeric_vector(coefficients, (n,), 'objective ' + name)
        before, after = float(co @ raw), float(co @ candidate)
        deltas.append(dict(name=name, original_value=before, finalized_value=after, delta=after-before))
    digest = hashlib.sha256()
    for data in (matrix.data, matrix.indices, matrix.indptr, row_low, row_high, lo, hi, integer):
        digest.update(np.ascontiguousarray(data).tobytes())
    diagnostic = dict(schema=NUMERICAL_CONTRACT_ID, corrections=corrections,
        original_domain_sha256=digest.hexdigest(), original_row_count=matrix.shape[0],
        original_variable_count=n, original_thresholds=dict(physical_rows_bounds=ORIGINAL_TOLERANCE,
            priority_locks=LOCK_TOLERANCE, integrality=INTEGER_TOLERANCE),
        emitted_boundary_budget=EMITTED_BOUNDARY_BUDGET, objective_deltas=deltas,
        exact_finite_nonnegative_dispatch_validated=True, objective_locks_modified=False)
    if corrections:
        diagnostic.update(raw_vector=raw.tolist(), finalized_vector=candidate.tolist(),
                          raw_validation=json_value(raw_validation), validation=json_value(validation))
    else:
        diagnostic['validation'] = {key: value for key, value in validation.items() if not isinstance(value, list)}
    return candidate, action, diagnostic


def finalize_conditional_grid_permission(x, *, constraints, lower, upper, integrality,
                                        rows, variables, ceiling, execution_limits, objectives=()):
    """Separate a healthy, topology-masked return permission from actual import.

    This common M18 interface completion does not enable the old live-GRID
    headroom preference. Every other coordinate, original row and lock stays.
    """
    n = len(lower)
    raw = _numeric_vector(x, (n,), 'return permission candidate')
    record = dict(schema='hospital_ies.m18_conditional_return_permission.v1',
        applied=False, ceiling_before_kW=float(raw[ceiling]), ceiling_after_kW=float(raw[ceiling]),
        all_non_ceiling_variables_unchanged=True, all_non_permission_coordinates_unchanged=True,
        method='one_variable_original_domain_interval',
        original_live_grid_headroom_preference_changed=False, command_is_immediate_execution=False,
        transfer_and_physical_readiness_proof_still_required=True, objective_deltas=[])
    if execution_limits is None:
        record['reason'] = 'no_m18_execution_limits'
        return raw, record
    limits = validate_execution_limits(execution_limits)
    record['execution_limits'] = limits
    if (limits['hardware_grid_available_fraction'] < 1.-1e-9
            or limits['grid_bus_state'] == 'GRID'
            or limits['effective_grid_import_limit_kW'] != 0.):
        record['reason'] = 'not_healthy_topology_masked_return'
        return raw, record
    if type(ceiling) is not int or not 0 <= ceiling < n:
        raise ValueError('Invalid return permission coordinate')
    lo = _numeric_vector(lower, (n,), 'return lower bounds', finite=False)
    hi = _numeric_vector(upper, (n,), 'return upper bounds', finite=False)
    integer = _numeric_vector(integrality, (n,), 'return integrality')
    matrix, row_low, row_high = flatten_constraints(constraints)
    if matrix.shape[1] != n or len(rows) != matrix.shape[0] or len(variables) != n:
        raise ValueError('Return permission original domain shape mismatch')
    if not np.isfinite(matrix.data).all():
        raise ValueError('Return permission original domain matrix must be finite')
    validation = validate_vector(matrix,row_low,row_high,lo,hi,integer,raw,rows)
    if not _accepted(validation):
        raise ValueError('Return permission input violates the unchanged original domain')
    before = float(raw[ceiling])
    interval_low, interval_high = float(lo[ceiling]), float(hi[ceiling])
    coefficient = matrix[:,ceiling].toarray().ravel()
    rest = np.asarray(matrix@raw).ravel()-coefficient*before
    for a,b,l,u in zip(coefficient,rest,row_low,row_high):
        if a > 0.:
            interval_low=max(interval_low,float((l-b)/a))
            interval_high=min(interval_high,float((u-b)/a))
        elif a < 0.:
            interval_low=max(interval_low,float((u-b)/a))
            interval_high=min(interval_high,float((l-b)/a))
    record.update(feasible_ceiling_interval_kW=json_value([interval_low,interval_high]),
        all_original_constraints_and_locks_checked=True,
        original_row_count=matrix.shape[0], original_variable_count=n,
        validation={k:v for k,v in validation.items() if not isinstance(v,list)})
    if not math.isfinite(interval_high) or interval_high <= before or interval_high < interval_low:
        record['reason']='original_domain_does_not_allow_larger_permission'
        return raw,record
    candidate=raw.copy()
    candidate[ceiling]=max(interval_low,float(np.nextafter(interval_high,before)))
    checked=validate_vector(matrix,row_low,row_high,lo,hi,integer,candidate,rows)
    if not _accepted(checked):
        record.update(reason='return_permission_rejected_by_original_domain_validation',
            rejected_ceiling_kW=float(candidate[ceiling]), rejected_validation=json_value(checked))
        return raw,record
    deltas=[]
    for name,co in objectives:
        co=_numeric_vector(co,(n,),'return objective '+name)
        original,final=float(co@raw),float(co@candidate)
        deltas.append(dict(name=name,original_value=original,finalized_value=final,delta=final-original))
    record.update(applied=True,reason='healthy_topology_masked_conditional_permission_completed',
        ceiling_after_kW=float(candidate[ceiling]),delta_kW=float(candidate[ceiling]-before),
        validation={k:v for k,v in checked.items() if not isinstance(v,list)},objective_deltas=deltas)
    return candidate,record


LOCK_PRESENTATION_CONTRACT_ID='hospital_ies.original_domain_priority_row_presentation.v18.v1'
PRIORITY_REFINEMENT_CONTRACT_ID='hospital_ies.same_layer_gap_certified_candidate_refinement.v18.v1'


def refine_priority_lock_candidate(*,native_candidate,previous_candidate,objective,matrix,row_lower,row_upper,
        lower,upper,integrality,rows,variables,native_objective,native_dual_bound,native_gap,
        allowed_relative_gap,remaining_budget_seconds):
    """At most sixteen same-integer halfsteps; no extra native solve or lock.

    A refined feasible point must meet BOTH the existing objective-consistency
    contract and relative MIP gap. It is never described as an exact optimum.
    Current and arrived/regulated critical-service coordinates remain bit exact.
    """
    import time
    began=time.perf_counter();n=len(lower)
    raw=_numeric_vector(native_candidate,(n,),'native refinement candidate')
    previous=_numeric_vector(previous_candidate,(n,),'previous refinement candidate')
    co=_numeric_vector(objective,(n,),'refinement objective')
    lo=_numeric_vector(lower,(n,),'refinement lower bounds',finite=False)
    hi=_numeric_vector(upper,(n,),'refinement upper bounds',finite=False)
    integer=_numeric_vector(integrality,(n,),'refinement integrality')
    if matrix.shape!=(len(rows),n) or len(variables)!=n:raise ValueError('Refinement original metadata shape mismatch')
    evidence=dict(schema=PRIORITY_REFINEMENT_CONTRACT_ID,accepted=False,max_halfsteps=16,trials=[],
        exact_optimum_claimed=False,original_thresholds=dict(physical=ORIGINAL_TOLERANCE,locks=LOCK_TOLERANCE,integrality=INTEGER_TOLERANCE),
        objective_consistency_relative_tolerance=1e-9,original_allowed_relative_gap=allowed_relative_gap,
        previous_point_is_endpoint_only=True,previous_point_returned_as_solution=False,
        original_objective_rows_bounds_and_locks_unchanged=True)
    def rejected(reason):
        evidence.update(reason=reason,elapsed_seconds=time.perf_counter()-began);return None,evidence
    if remaining_budget_seconds<=0:return rejected('original_layer_budget_exhausted')
    values=(native_objective,native_dual_bound,native_gap,allowed_relative_gap)
    if any(v is None or not math.isfinite(float(v)) for v in values):return rejected('missing_finite_original_objective_or_gap_certificate')
    native_value=float(co@raw)
    if abs(native_value-float(native_objective))>1e-9*max(1.,abs(native_value),abs(float(native_objective))):return rejected('native_objective_inconsistent')
    if native_gap<0 or allowed_relative_gap<0 or native_gap>allowed_relative_gap:return rejected('native_gap_not_within_original_contract')
    mask=integer>0
    evidence['same_integer_point']=bool(np.array_equal(raw[mask],previous[mask]))
    if not evidence['same_integer_point']:return rejected('different_integer_points')
    protected=[i for i,v in enumerate(variables) if any(v.get('name','').startswith(prefix+group+'.unserved.')
        for prefix in ('','risk.arrived_current.','risk.regulated_current.') for group in ('P0','P1'))]
    evidence['protected_critical_coordinates']=protected
    prior_check=validate_vector(matrix,row_lower,row_upper,lo,hi,integer,previous,rows)
    native_check=validate_vector(matrix,row_lower,row_upper,lo,hi,integer,raw,rows)
    evidence['previous_validation']={k:v for k,v in prior_check.items() if not isinstance(v,list)}
    if not _accepted(prior_check):return rejected('previous_candidate_fails_same_current_original_domain')
    original_previous=previous.copy()
    critical_difference=np.abs(previous[protected]-raw[protected])
    if np.max(critical_difference,initial=0.)>ORIGINAL_TOLERANCE:return rejected('material_critical_endpoint_difference')
    adjustments=[dict(index=int(i),name=variables[i].get('name'),unit=variables[i].get('unit'),
        previous_value=float(previous[i]),pinned_value=float(raw[i]),delta=float(raw[i]-previous[i]))
        for i in protected if previous[i]!=raw[i]]
    previous[protected]=raw[protected]
    aligned_check=validate_vector(matrix,row_lower,row_upper,lo,hi,integer,previous,rows)
    evidence.update(critical_endpoint_adjustments=adjustments,critical_endpoint_original_validation_passed=_accepted(aligned_check),
        critical_endpoint_validation={k:v for k,v in aligned_check.items() if not isinstance(v,list)},
        free_coordinate_indices=[i for i in range(n) if not mask[i] and i not in protected],
        convex_feasibility_assumed=False)
    if not _accepted(aligned_check):return rejected('critical_pinned_endpoint_fails_original_domain')
    if (native_check['max_original_violation']>ORIGINAL_TOLERANCE or native_check['max_integrality_distance']>INTEGER_TOLERANCE
        or native_check['max_lock_violation']<=LOCK_TOLERANCE):return rejected('not_a_native_optimal_priority_lock_residue')
    best=None
    for power in range(1,17):
        if time.perf_counter()-began>=remaining_budget_seconds:return rejected('original_layer_budget_exhausted_during_refinement')
        alpha=1.-2.**-power;candidate=raw+alpha*(previous-raw)
        check=validate_vector(matrix,row_lower,row_upper,lo,hi,integer,candidate,rows)
        value=float(co@candidate);difference=value-native_value
        consistency=abs(value-float(native_objective))<=1e-9*max(1.,abs(float(native_objective)),abs(value))
        gap=(abs(value-float(native_dual_bound))/abs(value) if value!=0. else
            0. if float(native_dual_bound)==0. else math.inf)
        accepted=_accepted(check) and consistency and gap<=allowed_relative_gap
        evidence['trials'].append(dict(alpha=alpha,original_validation_passed=_accepted(check),
            max_lock_violation=check['max_lock_violation'],objective_delta=difference,
            objective_consistency_passed=bool(consistency),relative_gap=gap if math.isfinite(gap) else None,accepted=bool(accepted)))
        if accepted:
            best=(candidate,alpha,check,value,difference,gap);break
    if best is None:return rejected('no_halfstep_passes_original_domain_and_both_objective_contracts')
    candidate,alpha,check,value,difference,gap=best
    if np.array_equal(candidate,original_previous):return rejected('floating_halfstep_equals_previous_point_no_distinct_refinement')
    changes=[dict(index=int(i),name=variables[i].get('name'),unit=variables[i].get('unit'),native_value=float(raw[i]),
        refined_value=float(candidate[i]),delta=float(candidate[i]-raw[i])) for i in np.flatnonzero(candidate-raw)]
    evidence.update(accepted=True,reason='same_layer_gap_certified_feasible_numeric_refinement',alpha=alpha,
        native_objective=native_value,refined_objective=value,objective_delta=difference,native_dual_bound=float(native_dual_bound),
        native_reported_objective=float(native_objective),relative_raw_dot_delta=difference,
        relative_gap=gap,objective_consistency_passed=True,critical_service_coordinates_unchanged=True,
        candidate_coordinate_changes=changes,validation={k:v for k,v in check.items() if not isinstance(v,list)},
        native_vector_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),previous_vector_sha256=hashlib.sha256(original_previous.tobytes()).hexdigest(),
        critical_pinned_endpoint_sha256=hashlib.sha256(previous.tobytes()).hexdigest(),
        refined_vector_sha256=hashlib.sha256(candidate.tobytes()).hexdigest(),elapsed_seconds=time.perf_counter()-began)
    return candidate,evidence


def solve_layer_with_lock_presentation(solver,c,*,integrality,bounds,constraints,options,rank,
                                      variables,rows,locks,input_context,row_scaling=False,previous_candidate=None):
    """One original solve and one fixed x1000 lock-row presentation, if needed.

    Both presentations share the original wall-clock budget and the backend's
    original-unit posterior validation context. No prior solution is returned.
    """
    import time
    from scipy import sparse
    from scipy.optimize import LinearConstraint
    from hospital_ies.solver_diagnostics import solve_layer as original_layer,active_collector
    began=time.perf_counter()
    budget=float((options or {}).get('time_limit',6.))
    matrix,row_low,row_high=flatten_constraints(constraints)
    original_lower=np.asarray(bounds.lb).copy();original_upper=np.asarray(bounds.ub).copy()
    lock_mask=np.array([r.get('kind')=='priority_lock' for r in rows],dtype=bool)
    if len(lock_mask)!=matrix.shape[0]:
        raise ValueError('Priority presentation original row metadata mismatch')
    collector=active_collector()
    collector_position=None if collector is None else len(collector.layers)
    evidence=dict(schema=LOCK_PRESENTATION_CONTRACT_ID,original_budget_seconds=budget,
        max_presentations=2,max_native_attempts_total=4,positive_priority_row_factor=1000.,
        recovery_attempted=False,recovery_accepted=False,
        original_objective_bounds_and_locks_unchanged=True,
        original_validation_thresholds=dict(physical_rows_bounds=ORIGINAL_TOLERANCE,
            priority_locks=LOCK_TOLERANCE,integrality=INTEGER_TOLERANCE),
        previous_or_other_state_candidate_used=False)
    completed_attempts=[]

    def native_with_finite_presentation(co,*,integrality,bounds,constraints,options):
        initial_options=dict(options)
        initial_options['time_limit']=max(0.,budget-(time.perf_counter()-began))
        evidence['active_presentation']='initial_original_domain_rows'
        initial=solver(co,integrality=integrality,bounds=bounds,constraints=constraints,options=initial_options)
        initial_attempts=copy.deepcopy(getattr(initial,'attempts',[]))
        for attempt in initial_attempts:
            attempt.update(presentation='initial_original_domain_rows',presentation_row_factor=1.)
        completed_attempts[:]=copy.deepcopy(initial_attempts)
        evidence['initial_status']=int(initial.status)
        eligible=(int(initial.status)==4 and lock_mask.any() and any(
            a.get('status')==0 and a.get('validation') is not None
            and a['validation']['max_original_violation']<=ORIGINAL_TOLERANCE
            and a['validation']['max_integrality_distance']<=INTEGER_TOLERANCE
            and a['validation']['max_lock_violation']>LOCK_TOLERANCE for a in initial_attempts))
        result=initial
        if eligible:
            remaining=budget-(time.perf_counter()-began)
            evidence['remaining_budget_seconds_at_recovery']=max(0.,remaining)
            if remaining>0.:
                a,lower,upper=flatten_constraints(constraints)
                scales=np.where(lock_mask,1000.,1.)
                scale_sha=hashlib.sha256(np.ascontiguousarray(scales).tobytes()).hexdigest()
                evidence.update(recovery_attempted=True,relative_row_scales=scales.tolist(),row_scales_sha256=scale_sha)
                scaled=[LinearConstraint(sparse.diags(scales)@a,lower*scales,upper*scales)]
                recovery_options=dict(options,time_limit=max(0.,budget-(time.perf_counter()-began)))
                evidence['active_presentation']='priority_lock_rows_positive_x1000'
                result=solver(co,integrality=integrality,bounds=bounds,constraints=scaled,options=recovery_options)
                recovered_attempts=copy.deepcopy(getattr(result,'attempts',[]))
                for attempt in recovered_attempts:
                    attempt.update(presentation='priority_lock_rows_positive_x1000',presentation_row_factor=1000.,
                        row_scales_sha256=scale_sha,remaining_original_budget_seconds=recovery_options['time_limit'])
                completed_attempts[:]=copy.deepcopy(initial_attempts+recovered_attempts)
                checked=(validate_vector(matrix,row_low,row_high,original_lower,original_upper,
                    integrality,result.x,rows) if result.x is not None else None)
                accepted=int(result.status)==0 and checked is not None and _accepted(checked)
                evidence.update(recovery_status=int(result.status),recovery_accepted=bool(accepted),
                    recovery_original_validation=None if checked is None else
                        {k:v for k,v in checked.items() if not isinstance(v,list)},
                    reason='same_original_objective_optimized_and_original_thresholds_passed' if accepted else
                        'fixed_positive_presentation_failed_no_candidate_accepted')
                if not accepted:
                    result.status=4
                    result.message='Fixed original-domain priority-row presentation failed; original failed attempts retained'
                result.attempts=initial_attempts+recovered_attempts
            else:
                evidence['reason']='original_layer_time_budget_exhausted_before_recovery'
                result.attempts=initial_attempts
        else:
            evidence['reason']='initial_original_presentation_accepted' if int(initial.status)==0 else 'not_the_optimal_priority_lock_residue_family'
            result.attempts=initial_attempts
        if int(result.status)!=0 and eligible and previous_candidate is not None:
            source=next((a for a in initial_attempts if a.get('status')==0 and a.get('raw_x') is not None
                and a.get('validation') is not None and a['validation']['max_original_violation']<=ORIGINAL_TOLERANCE
                and a['validation']['max_integrality_distance']<=INTEGER_TOLERANCE and a.get('objective_consistent') is True),None)
            if source is not None:
                candidate,refinement=refine_priority_lock_candidate(native_candidate=source['raw_x'],previous_candidate=previous_candidate,
                    objective=c,matrix=matrix,row_lower=row_low,row_upper=row_high,lower=original_lower,upper=original_upper,
                    integrality=integrality,rows=rows,variables=variables,native_objective=source.get('res_fun'),
                    native_dual_bound=source.get('mip_dual_bound'),native_gap=source.get('mip_gap'),
                    allowed_relative_gap=float((options or {}).get('mip_rel_gap',1e-6)),remaining_budget_seconds=budget-(time.perf_counter()-began))
                result.priority_lock_refinement=copy.deepcopy(refinement);evidence['priority_lock_refinement']=copy.deepcopy(refinement)
                if candidate is not None:
                    result.status=0;result.x=candidate;result.fun=float(np.asarray(c)@candidate)
                    result.mip_dual_bound=refinement['native_dual_bound'];result.mip_gap=refinement['relative_gap']
                    result.message='Gap-certified feasible numerical refinement; exact optimum not claimed'
                    evidence['reason']='same_layer_gap_certified_feasible_numeric_refinement'
        for global_attempt,attempt in enumerate(result.attempts,1):
            attempt['global_presentation_attempt']=global_attempt
        completed_attempts[:]=copy.deepcopy(result.attempts)
        evidence['active_presentation']=None
        evidence['elapsed_seconds']=time.perf_counter()-began
        result.same_domain_presentation_recovery=copy.deepcopy(evidence)
        result.numerical_version=LOCK_PRESENTATION_CONTRACT_ID
        return result

    result=None
    try:
        result=original_layer(native_with_finite_presentation,c,integrality=integrality,bounds=bounds,
            constraints=constraints,options=options,rank=rank,variables=variables,rows=rows,
            locks=locks,input_context=input_context,row_scaling=row_scaling)
        return result
    except Exception as error:
        evidence.update(exception_type=type(error).__name__,exception_message=str(error),
            reason='exception_during_'+str(evidence.get('active_presentation') or 'original_layer_collection'))
        # _record_error persists this established exception interface even
        # when collection is disabled in a normal physical episode.
        error.same_domain_presentation_recovery=copy.deepcopy(evidence)
        if not getattr(error,'solver_backend_attempts',None):
            error.solver_backend_attempts=[dict(rank=rank,phase='same_domain_presentation_exception',
                backend='gurobipy',numerical_version=LOCK_PRESENTATION_CONTRACT_ID,status=4,
                attempts=copy.deepcopy(completed_attempts),same_domain_presentation_recovery=copy.deepcopy(evidence))]
        raise
    finally:
        evidence['elapsed_seconds']=time.perf_counter()-began
        if result is not None:
            result.same_domain_presentation_recovery=copy.deepcopy(evidence)
        if collector is not None and len(collector.layers)>collector_position:
            layer=collector.layers[collector_position]
            layer['same_domain_presentation_recovery']=copy.deepcopy(evidence)
            layer['solver_matrix_semantics']='Serialized matrix is initial presentation; additional lock presentation is reconstructed by recorded relative row scales'
            layer['solver'].update(native_backend_max_attempts_per_presentation=2,max_attempts_per_layer=4,
                max_presentations_per_layer=2,numerical_version=LOCK_PRESENTATION_CONTRACT_ID,
                time_budget='single original layer budget shared by both presentations including construction')
            # original_layer.finish(error) has no result object, hence no
            # attempt list. Preserve completed native work before re-raising.
            if layer.get('result') is not None and len(layer['result'].get('attempts',[]))<len(completed_attempts):
                layer['result']['attempts']=copy.deepcopy(completed_attempts)
