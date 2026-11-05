# M19 sparse fork: independent twelve request contacts; M18 physical rows retained.
"""V18 bounded C18/C4-compat planner over the common M18 execution core.

Preserves original energy equalities/capacities/P0-P1 locks; versions service
ordering, commanded contacts, actual-combination tie breaking, and diagnostics.
This creates ordinary queued commands, never immediate source capability.
"""
from __future__ import annotations

import copy
import math
import time
from dataclasses import asdict

import numpy as np
from scipy.optimize import Bounds, LinearConstraint

from hospital_ies.execution import (
    ModelConstraintInfeasible, SolverExecutionError, execution_contract as resolve_contract,
)
from hospital_ies.model import RT_TO_KW, KWH_PER_MMBTU, gt_level_properties, illustrative_tariff
from hospital_ies.resilience import GROUPS, CARRIERS, lex_lock_tolerance
from hospital_ies.solver_backend import milp
from tools.autonomous_dispatch import numerics, planner_standby as frozen
from tools.autonomous_dispatch.planner_standby import _options as _standby_options
from hospital_ies.solver_diagnostics import (active_collector,flatten_constraints,validate_vector,
    solve_layer as original_solve_layer)
from .numerics import (finalize_command_vector, validate_execution_limits,
    finalize_conditional_grid_permission, solve_layer_with_lock_presentation, effective_held_dispatch)
from .readiness import (validate_context as validate_readiness_context,
    operating_options as validate_operating_options, preparation_profile, phase_sources,
    coordination_options as validate_coordination_options, response_envelope, diesel_support_budget,
    critical_reserve_rule)

NUMERICAL_CONTRACT_ID = 'operating_response.original_domain_finite_presentation_recovery.v15'
STANDBY_CONTRACT_ID = frozen.STANDBY_CONTRACT_ID
SERVICE_SWITCH_CONTRACT_ID = 'current_service_budget_handoff.v1'
ENGINEERING_CONTRACT_ID = 'execution_recovery.certified_preparation_and_current_priority.v18'
NEW_DEFAULT_OPTIONS = dict(service_budget_enabled=True, service_budget_fractions=[.05, .20],
    handoff_enabled=True, coordination_preference='standby_first', features_enabled=True)
PREFERENCES = ('standby_first', 'bridge_first', 'service_first', 'bridge_service_first')
solve_layer = solve_layer_with_lock_presentation
# Only these post-cost preferences/independent diagnostics may retain a prior
# strictly feasible command. Current service, preparation and cost are absent.
OPTIONAL_POST_COST_LAYERS = frozenset(('actual_GT_combination_changes',
    'ordinary_low_priority_disconnected_power', 'actual_GT_power_change',
    'healthy_converter_permission', 'coordinated_GRID_permission', 'held_actual_diagnostic'))
LATE_SOFT_LAYERS=frozenset(('standby_third_GT_exposure','normal_online_GT_count_after_preparation'))


def late_soft_service_locks_complete(name, rows, phases, *, recovery=False):
    if name not in LATE_SOFT_LAYERS and name!='preparation_P1':return False
    stages=['current']+[phase for phase in ('arrived_current','regulated_current') if phase in phases]
    required={'lock.'+stage+'_'+group for stage in stages for group in GROUPS}
    existing={row['name'] for row in rows if row.get('kind')=='priority_lock'}
    if name=='preparation_P1':
        if not recovery:return False
        required|={'lock.actual_command_shaft_support','lock.protection_target_excess','lock.preparation_P0'}
        if any('startup_bridge' in row['name'] for row in rows):
            required|={'lock.startup_bridge_P0','lock.startup_bridge_P1','lock.startup_bridge_surplus'}
    return required.issubset(existing)


ENGINEERING_DEFAULTS = dict(mode='service_restore', contact_mode='planned_shed',
    continuity=True, cost_equivalence_USD_per_step=1e-5,
    diagnose_full_supply=False, diagnose_hold_combination=False, grid_headroom_preference=False,
    execution_aware_start_preference=False, execution_aware_commitment_preference=False,
    p0_preparation_before_commitment=False)
# Existing regulator command-generation deadband, not a new physical tolerance.
LOCAL_DOWN_REQUEST_DEADBAND_KW = 1e-4
LOCAL_DOWN_REQUEST_DEADBAND_SOURCE = 'hospital_ies.state_transition.TransitionCore._request_local_down'
# Dispatch headroom only, never a relay/feasibility tolerance change.
PROTECTION_DISPATCH_MARGIN_KW=.01
CONTEXT_FIELDS = frozenset(('actual_gt_kW', 'gt_running', 'gt_can_start', 'gt_can_stop',
    'gt_start_pending', 'arrived_grid_import_limit_kW', 'arrived_dispatch',
    'actual_disconnected_kW', 'observed_critical_unserved_kW'))


def engineering_options(value=None):
    if value is not None and (not isinstance(value, dict) or set(value)-set(ENGINEERING_DEFAULTS)):
        raise ValueError('Unsupported engineering options; no future inputs allowed')
    result = {**ENGINEERING_DEFAULTS, **copy.deepcopy(value or {})}
    if result['mode'] not in ('service_restore', 'endurance_restore'):
        raise ValueError('Unknown engineering mode')
    if result['contact_mode'] not in ('planned_shed', 'separate'):
        raise ValueError('Unknown engineering contact mode')
    for key in ('continuity', 'diagnose_full_supply', 'diagnose_hold_combination', 'grid_headroom_preference',
                'execution_aware_start_preference', 'execution_aware_commitment_preference',
                'p0_preparation_before_commitment'):
        if type(result[key]) is not bool:
            raise ValueError(key+' must be boolean')
    band = result['cost_equivalence_USD_per_step']
    if isinstance(band, bool) or not isinstance(band, (int, float)) or not math.isfinite(band) or not 0. <= band <= .05:
        raise ValueError('Economic equivalence must be USD per current step in [0,.05]')
    result['cost_equivalence_USD_per_step'] = float(band)
    return result


def _guarded_grid_headroom(x, *, requested, separate_ceiling, ceiling, gp, go,
                           stable_floor, context, lower, upper, constraints):
    """Optimize only the separate permission coordinate of the solved plan.

    This one-dimensional linear solve leaves every physical, contact, risk,
    tracking-error and continuity variable fixed. In particular it cannot move
    the GT master after testing its floor guard. All original rows and locks
    restrict its interval, including current importer tracking preferences.
    """
    planned_on = x[go] > .5
    actual = np.asarray(context['actual_gt_kW'])
    actual_on = np.asarray(context['gt_running'], dtype=bool)
    deviation = np.where(planned_on, np.maximum(0., x[gp]-stable_floor), 0.)
    actual_deviation = np.where(actual_on, np.maximum(0., actual-stable_floor), 0.)
    planned_floor = bool(np.all(deviation <= LOCAL_DOWN_REQUEST_DEADBAND_KW))
    actual_floor = bool(np.all(actual_deviation <= LOCAL_DOWN_REQUEST_DEADBAND_KW))
    matching = bool(np.array_equal(actual_on, planned_on))
    pending = bool(any(context['gt_start_pending']))
    before = float(x[ceiling])
    record = dict(requested=requested, separate_ceiling=separate_ceiling,
        all_online_GT_at_floor_guard=planned_floor, all_actual_online_GT_at_floor_guard=actual_floor,
        actual_and_planned_combination_match=matching, any_actual_start_pending=pending,
        operational_deadband_kW=LOCAL_DOWN_REQUEST_DEADBAND_KW,
        operational_deadband_source=LOCAL_DOWN_REQUEST_DEADBAND_SOURCE,
        deadband_is_physical_or_service_tolerance=False,
        planned_GT_before_kW=x[gp].tolist(), actual_GT_kW=actual.tolist(),
        planned_GT_running_mask=planned_on.tolist(), actual_GT_running_mask=actual_on.tolist(),
        online_floor_kW=np.where(planned_on, stable_floor, 0.).tolist(),
        deviation_kW=deviation.tolist(), actual_deviation_kW=actual_deviation.tolist(),
        ceiling_before_kW=before, ceiling_after_kW=before, maximum_legal_current_ceiling_kW=float(upper[ceiling]),
        applied=False, all_non_ceiling_variables_unchanged=True,
        all_original_constraints_and_locks_checked=False,
        method='one_variable_linear_feasible_interval', command_is_immediate_execution=False)
    reason = ('disabled' if not requested else 'original_contract_has_no_separate_import_permission' if not separate_ceiling
        else 'actual_GT_start_pending' if pending else 'actual_and_planned_GT_combinations_differ' if not matching
        else 'actual_GT_above_original_local_down_deadband' if not actual_floor
        else 'planned_GT_above_original_local_down_deadband' if not planned_floor else None)
    if reason is not None:
        record['reason'] = reason
        return x, record

    interval_low, interval_high = float(lower[ceiling]), float(upper[ceiling])
    checked = []
    for constraint in constraints:
        matrix = np.atleast_2d(np.asarray(constraint.A, dtype=float))
        coefficient = matrix[:, ceiling]
        values = np.asarray(matrix@x).reshape(-1)
        lb = np.broadcast_to(constraint.lb, values.shape)
        ub = np.broadcast_to(constraint.ub, values.shape)
        rest = values-coefficient*before
        for a, b, lo, hi in zip(coefficient, rest, lb, ub):
            if a > 0.:
                interval_low = max(interval_low, float((lo-b)/a))
                interval_high = min(interval_high, float((hi-b)/a))
            elif a < 0.:
                interval_low = max(interval_low, float((hi-b)/a))
                interval_high = min(interval_high, float((lo-b)/a))
        checked.append((coefficient, values, lb, ub))
    record.update(all_original_constraints_and_locks_checked=True,
        feasible_ceiling_interval_kW=[interval_low, interval_high],
        checked_constraint_rows=sum(len(v) for _, v, _, _ in checked))
    if interval_high < max(before, interval_low):
        record['reason'] = 'no_increasing_permission_inside_original_numeric_boundary'
        return x, record
    after = interval_high
    # Step one floating-point representable value inside a derived upper
    # endpoint; this is arithmetic rounding, not a relaxed constraint/lock.
    if after > before:after = float(np.nextafter(after, before))
    maximum_before = maximum_after = 0.
    for coefficient, values, lb, ub in checked:
        new_values = values+coefficient*(after-before)
        old_violation = np.maximum(0., np.maximum(lb-values, values-ub))
        new_violation = np.maximum(0., np.maximum(lb-new_values, new_values-ub))
        scale = np.maximum(1., np.maximum(np.abs(values), np.abs(new_values)))
        rounding = 32.*np.finfo(float).eps*scale
        if np.any(new_violation > np.maximum(old_violation, rounding)):
            raise SolverExecutionError('GRID permission tie-break violates an original constraint or lock')
        maximum_before = max(maximum_before, float(np.max(old_violation)))
        maximum_after = max(maximum_after, float(np.max(new_violation)))
    if not lower[ceiling] <= after <= upper[ceiling]:
        raise SolverExecutionError('GRID permission tie-break violates the original importer bounds')
    result = x.copy(); result[ceiling] = after
    record.update(applied=after > before, ceiling_after_kW=after,
        reason='guarded_permission_maximized' if after > before else 'already_at_largest_feasible_permission',
        maximum_original_row_violation_before_native_units=maximum_before,
        maximum_original_row_violation_after_native_units=maximum_after,
        validation_rounding_basis='32 floating point eps times row magnitude; no physics or service tolerance changed')
    return result, record


def _context(value, state, loads):
    if value is not None and (not isinstance(value, dict) or set(value)-CONTEXT_FIELDS):
        raise ValueError('Unknown execution ledger fields; future inputs forbidden')
    result = copy.deepcopy(value or {})
    actual = np.asarray(result.get('actual_gt_kW', state.gt_prev), dtype=float)
    if actual.shape != (3,) or not np.isfinite(actual).all() or np.any(actual < 0.) or not np.allclose(actual, state.gt_prev, rtol=0., atol=1e-9):
        raise ValueError('Actual GT ledger must equal current executed state')
    result['actual_gt_kW'] = actual.tolist()
    for key, default in (('gt_running', (actual > 1e-9).tolist()), ('gt_can_start', [True]*3),
                         ('gt_can_stop', [True]*3), ('gt_start_pending', [False]*3)):
        row = result.get(key, default)
        if not isinstance(row, (list, tuple)) or len(row) != 3 or any(type(v) is not bool for v in row):
            raise ValueError(key+' must contain three actual-ledger booleans')
        result[key] = list(row)
    if any(actual[i] > 1e-9 and not result['gt_running'][i] for i in range(3)):
        raise ValueError('Positive actual output is not marked running')
    for key, shape in (('actual_disconnected_kW', (4, 3)), ('observed_critical_unserved_kW', (2, 3))):
        if key in result:
            data = np.asarray(result[key], dtype=float)
            if data.shape != shape or not np.isfinite(data).all() or np.any(data < 0.):
                raise ValueError('Invalid '+key)
            result[key] = data.tolist()
    if 'arrived_grid_import_limit_kW' in result:
        v = result['arrived_grid_import_limit_kW']
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0.:
            raise ValueError('Invalid arrived importer ledger')
    if 'arrived_dispatch' in result:
        dispatch = result['arrived_dispatch']
        allowed = {'gt_electric_kW', 'grid_import_kW', 'diesel_kW', 'boiler_steam_lbph',
            'absorption_cooling_kW', 'electric_chiller_cooling_kW', 'hrsg_steam_lbph',
            'tower_rejection_kW', 'gt_fuel_MMBtu_h'}
        if not isinstance(dispatch, dict) or set(dispatch)-allowed:
            raise ValueError('Invalid arrived dispatch fields')
        for key, v in dispatch.items():
            data = np.asarray(v, dtype=float)
            shape = (3,) if key in ('gt_electric_kW', 'electric_chiller_cooling_kW') else ()
            if data.shape != shape or not np.isfinite(data).all() or np.any(data < 0.):
                raise ValueError('Invalid arrived dispatch '+key)
    return result


def _options(value):
    if value is not None and not isinstance(value, dict):
        raise ValueError('Unsupported coordinated planning options')
    supplied = {**dict(variant='coupled',regulator_consistent=True,preserve_one_gt_standby=True,
        connection_mode='retain_when_no_gt'), **copy.deepcopy(value or {})}
    additions = {key: supplied.pop(key, copy.deepcopy(default))
                 for key, default in NEW_DEFAULT_OPTIONS.items()}
    for key in ('service_budget_enabled', 'handoff_enabled', 'features_enabled'):
        if type(additions[key]) is not bool:
            raise ValueError(key+' must be a boolean')
    fractions = additions['service_budget_fractions']
    if (not isinstance(fractions, (list, tuple)) or len(fractions) != 2 or
            any(type(x) not in (int, float) or not math.isfinite(x) or not 0. <= x <= 1.
                for x in fractions)):
        raise ValueError('service_budget_fractions must be two finite numbers in [0,1]')
    additions['service_budget_fractions'] = [float(x) for x in fractions]
    preference = additions['coordination_preference']
    if not isinstance(preference, str) or preference not in PREFERENCES:
        raise ValueError('Unknown coordination_preference')
    return dict(_standby_options(supplied), **additions)


def solve_joint(row, plant, loads, availability, state, dt_h,
                grid_import_limit_kW, diesel_start_available, gt_ramp_kW_per_h,
                solver_time_limit_sec=6., config=None, tracking_targets=None,
                security_gt_penalty_USD_per_kWh=0., execution_contract=None, *,
                joint_options=None, risk_loads=None, retention_eligible=False, connection_mode=None,
                engineering_options=None, execution_context=None, readiness_context=None, coordination_options=None, execution_limits=None,
                grid_bridge_request=False):
    """Return one queued-command plan plus separately balanced current use.

    ``action.grid_import_kW`` is the generated importer ceiling only when the
    EXISTING contract permits local import up to an arrived ceiling. Actual
    current used import and cost are in ``planned_current_action``. Economic
    compatibility binds these two quantities equal. All static copies keep
    GT/diesel/boiler sources fixed to this command (except the named outage).
    Risk surplus is never returned as a real action or included in main balance.
    """
    coordination = validate_coordination_options(coordination_options)
    if type(grid_bridge_request) is not bool:raise ValueError('grid_bridge_request must be a current-state boolean')
    recovery = coordination['execution_recovery']
    execution_limits = None if execution_limits is None else validate_execution_limits(execution_limits)
    operating = validate_operating_options(dict(preparation_profile='K0',phase_constraints_enabled=True))
    profile = preparation_profile(operating['preparation_profile'])
    readiness = validate_readiness_context(readiness_context)
    if not np.allclose(readiness['actual_gt_kW'], state.gt_prev, rtol=0., atol=1e-9):
        raise ValueError('Readiness actual GT must equal physical current state')
    if execution_context is not None:
        raise ValueError('v15 accepts only its strict readiness context, not a second ledger')
    execution_context = dict(actual_gt_kW=readiness['actual_gt_kW'],
        gt_running=[v>1e-9 for v in readiness['actual_gt_kW']],
        gt_can_start=[s not in ('PROTECTION_LOCKED','EXOGENOUS_UNAVAILABLE') for s in readiness['gt_physical_status']],
        gt_can_stop=readiness['gt_can_stop'],gt_start_pending=[v is not None for v in readiness['pending_starts']],
        arrived_grid_import_limit_kW=readiness['arrived_dispatch']['grid_import_kW'],
        arrived_dispatch=readiness['arrived_dispatch'],actual_disconnected_kW=readiness['actual_disconnected_kW'],
        observed_critical_unserved_kW=readiness['observed_critical_unserved_kW'])
    engineering = globals()['engineering_options']({**dict(mode='endurance_restore',continuity=operating['continuity'],
        execution_aware_commitment_preference=False), **(engineering_options or {})})
    # C optimizes preparation before normal GT-count/continuity preferences.
    # Old E/K1 combination locks remain in their immutable planner route.
    engineering['execution_aware_commitment_preference'] = False
    context = _context(execution_context, state, loads)
    # Persistent arrived permissions survive outages. Held copies instead use
    # the current actuator settings/caps; sampled GT/DG outputs remain separate.
    held_dispatch,held_dispatch_provenance=effective_held_dispatch(context['arrived_dispatch'],
        execution_limits=execution_limits,availability=availability,plant=plant,
        grid_import_limit_kW=grid_import_limit_kW)
    if type(retention_eligible) is not bool:
        raise ValueError('Retention eligibility must be a current-ledger boolean')
    if joint_options is not None and not isinstance(joint_options, dict):
        raise ValueError('Unsupported joint planning options')
    requested_options = copy.deepcopy(joint_options or {})
    if connection_mode is not None:
        if 'connection_mode' in requested_options and requested_options['connection_mode'] != connection_mode:
            raise ValueError('Conflicting connection modes')
        requested_options['connection_mode'] = connection_mode
    options = _options(requested_options)
    options['critical_reserve_multiplier'] = profile['critical_multiplier']
    if not options['features_enabled']:
        raise ValueError('Engineering requests cannot disable their versioned contract')
    variant = options['variant']
    contract = resolve_contract(execution_contract)
    regulator_active = (options['regulator_consistent']
        and contract['grid_import_mode'] == 'arrived_ceiling'
        and contract['gt_local_down_rate_kW_per_hour'] > 0.
        and availability['GRID'] > 0.)
    loads = np.asarray(loads, dtype=float).copy()
    if loads.shape != (4, 3) or not np.all(np.isfinite(loads)) or np.any(loads < 0):
        raise ValueError('Current loads must be finite nonnegative 4-by-3 carrier powers')
    if not math.isfinite(float(dt_h)) or dt_h <= 0:
        raise ValueError('Invalid current interval')
    if not math.isfinite(float(grid_import_limit_kW)) or grid_import_limit_kW < 0:
        raise ValueError('Invalid current grid import limit')
    if risk_loads is not None:
        risk_loads = np.asarray(risk_loads, dtype=float).copy()
        if (risk_loads.shape != (4, 3) or not np.all(np.isfinite(risk_loads))
                or np.any(risk_loads < 0) or np.any(risk_loads > loads+1e-9)):
            raise ValueError('Historical risk floor must be finite, nonnegative and no larger than current loads')
    assert len(plant.electric_chillers_rt) == 3 and plant.gt_count == 3

    support = diesel_support_budget(readiness,rated_kW=plant.emergency_diesel_kW,
        remaining_kWh=state.diesel_energy_remaining_kWh,support_minutes=coordination['support_minutes'],
        startup_delay_minutes=float((config or {}).get('diesel_start_delay_minutes',0.)))
    fuel_hours = support['actual_shaft_support_hours']
    support_hours = coordination['support_minutes']/60.
    observed_loss = np.asarray(context.get('observed_critical_unserved_kW', np.zeros((2, 3))))
    observed_critical = float(observed_loss.sum())
    cfg = config or {}
    protection_weights=None;protection_overload=None;protection_hold=None
    if recovery:
        protection_hold=(float(cfg.get('gt_overload_hold_minutes',15.)) if execution_limits is None
            else execution_limits['gt_hold_minutes'])
        protection_overload=np.asarray(state.overload_minutes if execution_limits is None
            else execution_limits['gt_overload_minutes'],dtype=float)
        if (not math.isfinite(protection_hold) or protection_hold<=0. or protection_overload.shape!=(3,)
                or not np.isfinite(protection_overload).all() or np.any(protection_overload<0.)):
            raise ValueError('Invalid observed GT protection clocks or original hold time')
        # Bounded present-state preference only. The original protection
        # threshold/hold and every current critical-service lock are unchanged.
        protection_weights=1.+np.minimum(protection_overload/protection_hold,1.)
        protection_running=np.array([availability[f'GT_{i+1}']>0. and state.gt_prev[i]>1e-4 for i in range(3)])
        protection_count=int(protection_running.sum());protection_divisor=max(1,protection_count)
        from hospital_ies.resilience import pre_control_cascade
        observed_connected=np.maximum(0.,loads-np.asarray(readiness['actual_disconnected_kW']))
        _,observed_stress,observed_response=pre_control_cascade(copy.deepcopy(state),availability,observed_connected,plant,
            {**cfg,'precontrol_load_shed_enabled':False},0.,False,
            grid_import_limit_kW=state.last_grid_import_kW/availability['GRID'] if availability['GRID']>0. else 0.,
            diesel_response_kW=state.last_diesel_kW,apply_trips=False)
        protection_cooling_reference=(float(state.cooling_reference_kW) if state.cooling_reference_kW is not None else
            float(cfg.get('pre_event_cooling_demand_kW',observed_connected[:,2].sum())))
        protection_cold_ratio=plant.ec_electric_kW_per_rt/RT_TO_KW*float(cfg['precontrol_cooling_to_el_fraction'])
        observed_cold_proxy=max(0.,float(observed_connected[:,2].sum())-protection_cooling_reference)*protection_cold_ratio
    service_solver_tol = float(cfg.get('p0_solver_feasibility_tol_kW', .001))
    service_abs_tol = float(cfg.get('p0_unserved_abs_tol_kW', .01))
    service_rel_tol = float(cfg.get('p0_unserved_rel_tol', .001))
    observed_fraction = np.divide(observed_loss, loads[:2], out=np.zeros((2, 3)), where=loads[:2] > service_solver_tol)
    observed_critical_material = bool(np.any((observed_loss > service_solver_tol)
        & ((observed_loss >= service_abs_tol) | (observed_fraction >= service_rel_tol))))
    endurance_active = (engineering['mode'] == 'endurance_restore' and availability['GRID'] < 1.-1e-9
        and (recovery or (fuel_hours is not None and fuel_hours < support_hours) or observed_critical_material))
    command_support_active = coordination['time_energy_preparation'] and endurance_active
    reserve_active=bool(coordination['critical_reserve'] and command_support_active
        and state.diesel_energy_remaining_kWh>0.)
    reserve_rule=critical_reserve_rule(loads[:2],remaining_kWh=state.diesel_energy_remaining_kWh,
        ec_kWe_per_kWc=plant.ec_electric_kW_per_rt/RT_TO_KW,
        shaft_loss_bound_kW=support['shaft_loss_bound_kW'],minimum_hours=support_hours)
    reserve_support=diesel_support_budget(readiness,rated_kW=plant.emergency_diesel_kW,
        remaining_kWh=state.diesel_energy_remaining_kWh,support_minutes=60.*reserve_rule['reserve_hours'],
        startup_delay_minutes=float((config or {}).get('diesel_start_delay_minutes',0.)))

    gp = np.arange(3); go = np.arange(3, 6)
    ia = 6; ie = np.arange(7, 10); ib = 10; ig = 11; idg = 12; iw = 13
    unmet = lambda group, carrier: 14+group*3+carrier
    accepted = {'gt_1': 0, 'gt_2': 1, 'gt_3': 2, 'grid_import': ig,
                'diesel': idg, 'absorption_cooling_kW': ia}
    targets = {}
    for key, value in (tracking_targets or {}).items():
        if key not in accepted or not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f'Invalid current tracking target {key}={value}')
        targets[key] = (accepted[key], float(value)/(RT_TO_KW if key == 'absorption_cooling_kW' else 1.))

    names = [f'GT_{i+1}.electric' for i in range(3)]+[f'GT_{i+1}.on' for i in range(3)]+[
        'absorption.cooling', 'EC_1.cooling', 'EC_2.cooling', 'EC_3.cooling',
        'boiler.steam', 'grid.import', 'diesel.electric', 'unused.steam']+[
        f'{g}.unserved.{carrier}' for g in GROUPS for carrier in CARRIERS]
    units = ['kW']*3+['binary']*3+['RT']*4+['lb/h', 'kW', 'kW', 'lb/h']+['kW']*12
    def allocate(name, unit):
        index = len(names); names.append(name); units.append(unit)
        return index
    target_indices = {key: allocate(f'tracking.{key}.absolute_error', 'RT' if index == ia else 'kW')
                      for key, (index, _) in targets.items()}
    separate_ceiling = variant in ('diverse', 'coupled') and contract['grid_import_mode'] == 'arrived_ceiling'
    ceiling = allocate('command.grid_import_ceiling', 'kW') if separate_ceiling else ig
    converter_ceilings = np.array([allocate('command.'+name+'.cooling_ceiling','RT')
        for name in ('absorption','EC_1','EC_2','EC_3')])
    converter_permission_gap=np.array([allocate('preference.'+name+'.unused_permission','RT')
        for name in ('absorption','EC_1','EC_2','EC_3')])
    diesel_ceiling = allocate('command.diesel_arrived_ceiling','kW')
    # Existing arrived startups carry an old process target and clock. They
    # are not a new delivered port in the current ordinary ramp window.
    command_gp=gp.copy();command_go=go.copy();pending_targets={}
    for i,pending in enumerate(readiness['pending_starts']):
        if pending is not None:
            target=allocate(f'command.GT_{i+1}.retained_pending_target','kW')
            keep=allocate(f'command.GT_{i+1}.keep_pending','binary')
            command_gp[i]=target;command_go[i]=keep;pending_targets[i]=(target,keep)
    if 'grid_import' in targets:
        # This target has always described the generated importer command.
        # Current imported energy is a different variable under arrived_ceiling.
        targets['grid_import'] = (ceiling, targets['grid_import'][1])
    # Sustained shaft work is a real bound on preparation copies, never a
    # soft electrical-only preference or a new hard cap on current service.
    fuel = None
    regulator_excess = np.array([allocate(f'preparation.GT_{i+1}.above_stable_floor', 'kW')
                                 for i in range(3)]) if regulator_active else None
    retention = allocate('command.retain_low_contacts', 'binary')
    contacts = np.arange(14, 26).reshape(4, 3).copy()
    # M19: demand shortage and an ordinary isolation request are independent.
    for group in range(4):
        for carrier in range(3):
            contacts[group, carrier] = allocate(f'command.{GROUPS[group]}.{CARRIERS[carrier]}.disconnected', 'kW')
    copies = []; cases = []
    if variant in ('diverse', 'coupled'):
        pressure_loads = loads.copy()
        pressure_loads[:2] *= options['critical_reserve_multiplier']
        if recovery:
            # The inherited K1 margin becomes REAL boiler steam in this
            # shared-command loss copy, with the original gas cost.
            pressure_loads[:2,1] *= preparation_profile('K1')['critical_multiplier']
        cases = [('GRID_loss', pressure_loads)]
        cases += [(f'GT_{i+1}_loss', pressure_loads) for i in range(3)]
        cases += [(f'HRSG_{i+1}_loss', pressure_loads) for i in range(3)]
        if coordination['cooling_preparation']:
            cases += [('ABS_loss',pressure_loads)]
            cases += [(f'EC_{i+1}_loss',pressure_loads) for i in range(3)]
        if variant == 'coupled':
            cases.append(('low_history', loads if risk_loads is None else risk_loads))
    if options['handoff_enabled']:
        # A registered permission-loss condition on THIS command after arrival,
        # not a prediction of when GRID recovers and not a pre-arrival source.
        cases.append(('DG_withdrawal_GRID_ceiling', loads))
    if operating['phase_constraints_enabled']:
        cases += [('pre_'+label,demand) for label,demand in list(cases)
            if label == 'GRID_loss' or label.startswith(('GT_','HRSG_','EC_','ABS_'))]
        cases.append(('arrived_current', loads))
        if regulator_active:
            cases.append(('regulated_current',loads))
    if coordination['time_energy_preparation']:
        cases += [('response_'+label,demand) for label,demand in list(cases)
            if label == 'GRID_loss' or label.startswith(('GT_','HRSG_','EC_','ABS_'))]
    cases.append(('held_actual', loads))
    if recovery:
        # Endpoint preparation and the pre-connection bridge are distinct.
        # The latter cannot use cold DG or a future GT startup target.
        cases=[(label,demand) for label,demand in cases if label.startswith('response_')
            or label in ('low_history','arrived_current','regulated_current','held_actual')]
        cases.append(('startup_bridge',loads))
        if grid_bridge_request:
            # A conditional target for the observed return process, without an
            # R-minute readiness promise. Real OPEN still waits for actual GT.
            cases.append(('grid_open_target',loads))
    shared_response_gt=(np.array([allocate(f'phase.shared_response.GT_{i+1}.target','kW') for i in range(3)])
        if coordination['time_energy_preparation'] else None)
    shared_response_on=(np.array([allocate(f'phase.shared_response.GT_{i+1}.ready','binary') for i in range(3)])
        if coordination['time_energy_preparation'] else None)
    for label, demand in cases:
        start = len(names)
        for device in ('absorption', 'EC_1', 'EC_2', 'EC_3'):
            allocate(f'risk.{label}.{device}.cooling', 'RT')
        rg = allocate(f'risk.{label}.grid_import', 'kW')
        rw = allocate(f'risk.{label}.unused_steam', 'lb/h')
        ru = len(names)
        for group in GROUPS:
            for carrier in CARRIERS:
                allocate(f'risk.{label}.{group}.unserved.{carrier}', 'kW')
        rs = allocate(f'risk.{label}.electric_surplus_defect', 'kW')
        is_pre = label == 'arrived_current' or label.startswith('pre_')
        is_response=label.startswith('response_')
        base_label = label[9:] if is_response else label[4:] if label.startswith('pre_') else label
        phase_diesel = allocate('phase.'+label+'.permitted_diesel','kW') if label!='held_actual' else None
        certificate=allocate('preparation.'+label+'.balanced_certificate','binary') if recovery and (is_response or label in ('startup_bridge','grid_open_target')) else None
        credited_unmet=np.array([allocate('preparation.'+label+'.'+g+'.uncredited.'+k,'kW') for g in GROUPS for k in CARRIERS]) if certificate is not None else None
        response_gt = shared_response_gt if is_response else None
        response_on = shared_response_on if is_response else None
        gt_mask = np.ones(3); heat_mask = np.ones(3)
        if base_label.startswith('GT_') and base_label.endswith('_loss'):
            gt_mask[int(base_label[3])-1] = 0.
        if base_label.startswith('HRSG_') and base_label.endswith('_loss'):
            heat_mask[int(base_label[5])-1] = 0.
        if is_pre or label=='startup_bridge':
            gt_mask *= (np.asarray(readiness['actual_gt_kW']) > 1e-9).astype(float)
        copies.append(dict(name=label, loads=np.asarray(demand).copy(), converters=np.arange(start, start+4),
                           grid=rg, steam=rw, unmet=ru, surplus=rs,
                            gt_mask=gt_mask, heat_mask=heat_mask, phase_diesel=phase_diesel,
                            is_pre=is_pre,base_label=base_label,
                             is_response=is_response,response_gt=response_gt,response_on=response_on,
                           certificate=certificate,credited_unmet=credited_unmet,
                           diesel_mask=0. if base_label in ('DG_withdrawal_GRID_ceiling','startup_bridge','grid_open_target') else 1.))

    standby_excess = allocate('preparation.third_GT_on_excess', 'GT count')
    budget_violation = np.array([[allocate(f'service_budget.{GROUPS[p]}.{k}.violation', 'kWh')
        for k in CARRIERS] for p in (2, 3)])
    power_changes = np.array([allocate(f'continuity.GT_{i+1}.absolute_change', 'kW') for i in range(3)])
    state_changes = np.array([allocate(f'continuity.GT_{i+1}.state_change', 'GT changes') for i in range(3)])
    command_support_excess = (allocate('preference.command_diesel_support_excess','shaft equivalent kWh')
        if command_support_active else None)
    reserve_excess=allocate('preference.current_critical_reserve_excess','shaft equivalent kWh') if reserve_active else None
    protection_excess=np.array([allocate(f'preference.GT_{i+1}.protection_excess','kW') for i in range(3)]) if recovery else None
    protection_gross=allocate('preference.current_allocated_gross_pressure','kW') if recovery else None
    protection_cold_growth=allocate('preference.current_connected_cooling_growth','kW cold') if recovery else None
    n = len(names)
    vector = lambda: np.zeros(n)
    lower = np.zeros(n); upper = np.full(n, np.inf)
    integrality = np.zeros(n, dtype=int); integrality[go] = 1
    integrality[retention] = 1
    for target,keep in pending_targets.values():
        integrality[keep]=1;upper[keep]=1.
    for case in copies:
        if case['certificate'] is not None:
            integrality[case['certificate']]=1;upper[case['certificate']]=1.
            upper[case['credited_unmet']]=case['loads'].reshape(-1)
        if case['is_response']:
            integrality[case['response_on']] = 1
            upper[case['response_on']] = 1.
    upper[budget_violation] = 0.
    upper[state_changes] = 1.
    upper[retention] = 0.  # Old no-GT retention binary is unused in the new contact algorithm.
    matrix = []; row_low = []; row_high = []; metadata = []
    def add(coefficients, low, high, name, unit='kW', kind='physical', source='original physical constraint'):
        r = vector()
        if isinstance(coefficients, dict):
            for index, value in coefficients.items():r[index] = value
        else:r[:] = coefficients
        matrix.append(r); row_low.append(low); row_high.append(high)
        metadata.append(dict(name=name, unit=unit, source=source, kind=kind))

    ppoints, spoints, fpoints = gt_level_properties(plant, float(row.outdoor_temperature_C))
    rated = float(ppoints[-1]); steam_per_kW = float(spoints[-1]/rated); fuel_per_kW = float(fpoints[-1]/rated)
    heat_factor = plant.steam_kWh_per_lb*plant.exchanger_eff
    hrsg = np.array([availability[f'HRSG_{i+1}'] for i in range(3)])
    stable_floor = .4*rated*np.array([availability[f'GT_{i+1}'] for i in range(3)])
    response = response_envelope(readiness,response_minutes=coordination['response_minutes'],
        delay_bound_minutes=coordination['delay_bound_minutes'],gt_start_delay_minutes=contract['gt_start_delay_minutes'],
        gt_ramp_kW_per_h=gt_ramp_kW_per_h,rated_gt_kW=rated)
    e = vector(); e[gp] = 1.; e[ig] = 1.; e[idg] = 1.
    e[ia] = -plant.abs_electric_kW_per_rt; e[ie] = -plant.ec_electric_kW_per_rt
    h = vector(); h[gp] = steam_per_kW*hrsg; h[ib] = 1.; h[ia] = -plant.abs_steam_lb_per_rt; h[iw] = -1.
    c = vector(); c[ia] = RT_TO_KW; c[ie] = RT_TO_KW
    for i in range(4):e[unmet(i, 0)] = 1.; h[unmet(i, 1)] = 1./heat_factor; c[unmet(i, 2)] = 1.
    main_balance = np.array([e, h, c])
    main_rhs = np.array([loads[:, 0].sum(), loads[:, 1].sum()/heat_factor, loads[:, 2].sum()])
    for r, rhs, carrier, unit in zip(main_balance, main_rhs, ('electric', 'steam', 'cooling'), ('kW', 'lb/h', 'kW')):
        add(r, rhs, rhs, f'balance.{carrier}', unit, source='original energy hub equality')
    if contract['diesel_mode'] == 'emergency_bus':
        coefficients = {idg: 1.}
        allowed = [i for i, g in enumerate(GROUPS) if g in contract['diesel_allowed_priorities']]
        coefficients.update({unmet(i, 0): 1. for i in allowed})
        add(coefficients, -np.inf, float(loads[allowed, 0].sum()), 'diesel.allowed_bus')
    for i in range(3):
        av = availability[f'GT_{i+1}']
        add({i: 1., 3+i: -rated*av}, -np.inf, 0., f'GT_{i+1}.capacity')
        add({i: -1., 3+i: .4*rated*av}, -np.inf, 0., f'GT_{i+1}.minimum_stable')
        if av > 0:
            # Reuse the original ordinary execution window. Recentring this
            # budget on the most recently executed actual port would issue a
            # second allowance; elapsed=0 after an arrival must not erase it.
            change = readiness['gt_ramp_budget_kW']
            base = min(readiness['gt_ramp_anchor_kW'][i], rated*av)
            add({i: 1.}, max(0., base-change), base+change, f'GT_{i+1}.ordinary_ramp')
        upper[i] = rated*av; upper[3+i] = 1. if av > 0 else 0.
        if av > 0.:
            if not context['gt_running'][i] and not context['gt_start_pending'][i] and not context['gt_can_start'][i]:
                upper[3+i] = 0.
            if context['gt_running'][i] and not context['gt_can_stop'][i]:
                lower[3+i] = 1.
        change_index = int(power_changes[i]); previous = context['actual_gt_kW'][i]
        add({i: 1., change_index: -1.}, -np.inf, previous, f'continuity.GT_{i+1}.positive_change', kind='preference')
        add({i: -1., change_index: -1.}, -np.inf, -previous, f'continuity.GT_{i+1}.negative_change', kind='preference')
        was_running = context['gt_running'][i]
        add({int(state_changes[i]): 1., int(go[i]): 1. if was_running else -1.},
            float(was_running), float(was_running), f'continuity.GT_{i+1}.actual_state_change',
            unit='GT changes', kind='preference')
    if regulator_active:
        # This equality only names slack already implied nonnegative by the
        # original minimum-stable constraint. It never hard-limits main GP.
        for i, excess in enumerate(regulator_excess):
            add({int(excess): 1., int(gp[i]): -1., int(go[i]): float(stable_floor[i])}, 0., 0.,
                f'preparation.GT_{i+1}.above_stable_floor',
                source='soft preference under existing local-down contract; not an execution prediction')
    if recovery:
        reference=plant.gt_rated_kW if execution_limits is None else execution_limits['gt_protection_reference_kW']
        multiple=float(cfg.get('gt_protection_trip_multiple',1.05)) if execution_limits is None else execution_limits['gt_trip_multiple']
        # Original gross proxy, evaluated on THIS current allocation. Only
        # real modeled ports can replace a lower running GT; conditional
        # ceilings/pending starts cannot erase its pressure. Cold growth is
        # a protection risk term and never enters the actual energy equality.
        add({protection_cold_growth:1.,**{int(contacts[i,2]):1. for i in range(4)}},
            float(loads[:,2].sum())-protection_cooling_reference,np.inf,
            'preference.protection.current_cooling_growth','kW cold',kind='preference',
            source='original current cooling-growth proxy with original coefficient')
        gross={protection_gross:1.,protection_cold_growth:-protection_cold_ratio,ig:1.,idg:1.,
            ia:-plant.abs_electric_kW_per_rt,**{int(i):-plant.ec_electric_kW_per_rt for i in ie}}
        gross.update({int(gp[i]):1. for i in range(3) if protection_running[i]})
        gross.update({int(contacts[i,0]):1. for i in range(4)})
        add(gross,float(loads[:,0].sum()),np.inf,'preference.protection.current_allocated_gross_pressure','kW',
            kind='preference',source='original gross algebra; current ports and real connected demand, no conditional permission')
        for i in range(3):
            add({int(command_gp[i]):1.,int(protection_excess[i]):-1.,
                 protection_gross:1./protection_divisor if protection_running[i] else 0.},
                -np.inf,reference*multiple-PROTECTION_DISPATCH_MARGIN_KW,
                f'preference.GT_{i+1}.protection_target','kW',kind='preference',
                source='unchanged relay threshold; current running-port gross pressure plus .01kW dispatch headroom, soft excess preserves critical service')
    # New ordinary command contact choices. Connected-but-unserved remains
    # unserved in the balance and in the execution protection/stress ledger.
    for group in range(4):
        for carrier in range(3):
            cut = int(contacts[group, carrier]); unserved = unmet(group, carrier); demand = loads[group, carrier]
            upper[cut] = demand
            add({cut: 1., unserved: -1.}, -np.inf, 0.,
                f'command.{GROUPS[group]}.{CARRIERS[carrier]}.cut_below_unserved')
            if group >= 2 and engineering['contact_mode'] == 'planned_shed':
                add({cut: 1., unserved: -1.}, 0., 0.,
                    f'command.{GROUPS[group]}.{CARRIERS[carrier]}.cut_equals_unserved',
                    source='explicit old-cut scheduling comparator')
    tower_co = np.array([RT_TO_KW+plant.abs_steam_lb_per_rt*plant.steam_kWh_per_lb]+[RT_TO_KW+plant.ec_electric_kW_per_rt]*3)
    tower_limit = plant.cooling_tower_rejection_kW*availability['TOWER']*sum(availability.get(f'TOWER_{j+1}', 1.) for j in range(4))/4.
    tower = vector(); tower[ia] = tower_co[0]; tower[ie] = tower_co[1:]
    add(tower, -np.inf, tower_limit, 'tower.rejection')
    for key, (index, desired) in targets.items():
        error = target_indices[key]; unit = 'RT' if index == ia else 'kW'
        add({index: 1., error: -1.}, -np.inf, desired, f'tracking.{key}.positive', unit, source='original current target preference')
        add({index: -1., error: -1.}, -np.inf, -desired, f'tracking.{key}.negative', unit, source='original current target preference')
    upper[ia] = plant.absorption_rt*availability['ABS']
    upper[ie] = np.array(plant.electric_chillers_rt)*np.array([availability[f'EC_{i+1}'] for i in range(3)])
    upper[converter_ceilings] = upper[np.r_[ia,ie]]
    upper[converter_permission_gap]=upper[converter_ceilings]
    for permission,gap in zip(converter_ceilings,converter_permission_gap):
        add({int(permission):1.,int(gap):1.},upper[permission],upper[permission],
            'preference.'+names[permission]+'.unused_identity','RT',kind='preference',
            source='nonnegative permission headroom identity; no change to actual conversion capacity')
    for used,permission in zip(np.r_[ia,ie],converter_ceilings):
        add({int(used):1.,int(permission):-1.},-np.inf,0.,'command.'+names[used]+'.below_arrived_ceiling','RT',
            source='existing ordinary converter command ceiling, actual use remains balanced')
    upper[ib] = plant.boiler_steam_lbph/3*sum(availability[f'BOILER_{i+1}'] for i in range(3))
    upper[ig] = grid_import_limit_kW*availability['GRID']
    upper[idg] = min(plant.emergency_diesel_kW*availability['DIESEL'], state.diesel_energy_remaining_kWh/dt_h) if diesel_start_available else 0.
    upper[diesel_ceiling] = plant.emergency_diesel_kW*availability['DIESEL'] if state.diesel_energy_remaining_kWh>0. else 0.
    if execution_limits is not None and not execution_limits['diesel_conditionally_permitted']:
        upper[diesel_ceiling]=0.
    add({idg:1.,diesel_ceiling:-1.},-np.inf,0.,'command.diesel_used_below_ceiling')
    if command_support_active:
        # An unbounded nonnegative epigraph extends every original feasible
        # plan, even when overhead alone exceeds the remaining budget. The
        # signed RHS must retain that deficit; this is not a physical fuel cap.
        add({diesel_ceiling:support_hours,command_support_excess:-1.},-np.inf,
            state.diesel_energy_remaining_kWh-support['overhead_shaft_work_kWh'],
            'preference.actual_command_shaft_support','shaft equivalent kWh',kind='preference',
            source='ordinary command ceiling duration plus startup, positive storage and bounded shaft loss; soft excess')
    if reserve_active:
        add({diesel_ceiling:reserve_rule['reserve_hours'],reserve_excess:-1.},-np.inf,
            state.diesel_energy_remaining_kWh-reserve_support['overhead_shaft_work_kWh'],
            'preference.current_critical_reserve','shaft equivalent kWh',kind='preference',
            source='current P0/P1 demand and existing conversion auxiliary reference; remaining shaft budget; no outage endpoint')
    if contract['diesel_mode'] == 'emergency_bus' and availability['GRID'] > 0:upper[idg] = 0.
    for i in range(4):
        for k in range(3):upper[unmet(i, k)] = loads[i, k]
    if separate_ceiling:
        upper[ceiling] = grid_import_limit_kW  # persistent conditional permission; effective ig remains gated
        add({ig: 1., ceiling: -1.}, -np.inf, 0., 'command.import_used_below_ceiling', source='existing arrived_ceiling interface')
    if fuel is not None:
        add({idg: 1., fuel: -1.}, -np.inf, state.diesel_energy_remaining_kWh/options['support_hours'],
            'preparation.endurance_preference', source='registered preparation preference, not physical fuel horizon')

    response_domain=[]
    for i,(target,keep) in pending_targets.items():
        pending=readiness['pending_starts'][i];requested=pending['requested']
        maximum=rated*availability[f'GT_{i+1}']
        # These are the unchanged actual-port rows. No output before the real
        # startup clock, and no additional ordinary ramp budget is issued.
        upper[gp[i]]=0.;upper[go[i]]=0.
        upper[target]=maximum
        add({target:1.,keep:-requested},0.,0.,f'C_domain.GT_{i+1}.preserve_pending_target',
            kind='controller_command_domain',source='C-only original arrived startup target or cancellation')
        add({target:1.,keep:-maximum},-np.inf,0.,f'command.GT_{i+1}.pending_capacity',
            source='original GT capacity applied to retained conditional startup target')
        add({target:-1.,keep:stable_floor[i]},-np.inf,0.,f'command.GT_{i+1}.pending_minimum_stable',
            source='original minimum stable row; no separate floating point floor pre-judgment')
        response_domain.append(dict(device=f'GT_{i+1}',rule='preserve_actual_pending_target_or_cancel',
            requested_kW=requested,cancellation_available=True,old_ready_minute=pending['ready'],
            old_started_minute=pending['started'],current_actual_port_kW=0.,
            ordinary_ramp_applies_to_actual_port_not_old_startup_target=True,
            original_capacity_and_minimum_rows_retained=True,C_only=True,physical_capacity_change=False))
    if coordination['time_energy_preparation']:
        for i,status in enumerate(readiness['gt_physical_status']):
            maximum=rated*availability[f'GT_{i+1}'];pending=readiness['pending_starts'][i]
            gate=0.
            if status=='RUNNING':
                gate=1.
            elif pending is not None:
                gate=float(pending['ready']<=response['deadline_minute'])
            else:
                reachable=min(maximum,response['gt_max_kW'][i])
                if reachable>0. and reachable>=stable_floor[i]:
                    # Restrict C's candidate command, rather than use a
                    # solver-tolerance-dependent strict < readiness branch.
                    # Equality at the R cap is therefore unambiguously READY.
                    add({int(gp[i]):1.,int(go[i]):-reachable},-np.inf,0.,f'C_domain.GT_{i+1}.response_target_ceiling',
                        kind='controller_command_domain',source='C-only conservative within-response target; not physical capacity')
                    gate=1.
                    response_domain.append(dict(device=f'GT_{i+1}',rule='within_response_target_or_off',
                        target_ceiling_kW=reachable,equal_to_ceiling_is_ready=True,
                        C_only=True,physical_capacity_change=False))
                # If even the existing stable floor cannot be ready by R,
                # late eventual commands remain available but supply is zero
                # in every R copy. Default D=R=30 retains this behavior.
            add({int(shared_response_gt[i]):1.,int(command_gp[i]):-gate},0.,0.,f'phase.shared_response.GT_{i+1}.same_command_target')
            add({int(shared_response_on[i]):1.,int(command_go[i]):-gate},0.,0.,f'phase.shared_response.GT_{i+1}.deterministic_ready')
            upper[shared_response_gt[i]]=maximum

    for case in copies:
        label = case['name']; demand = case['loads']; conv = case['converters']
        rg = case['grid']; rw = case['steam']; ru = case['unmet']; rs = case['surplus']
        mask = case['gt_mask']
        conditional=label not in ('held_actual','arrived_current','regulated_current','startup_bridge')
        source_gp=command_gp if conditional else gp;source_go=command_go if conditional else go
        case['source_gp']=source_gp;case['source_go']=source_go
        re = vector(); re[source_gp] = mask; re[rg] = 1.; re[rs] = -1.
        re[conv] = -np.array([plant.abs_electric_kW_per_rt]+[plant.ec_electric_kW_per_rt]*3)
        rh = vector(); rh[source_gp] = steam_per_kW*hrsg*mask*case['heat_mask']; rh[ib] = 1.; rh[conv[0]] = -plant.abs_steam_lb_per_rt; rh[rw] = -1.
        case['regulator_floor'] = regulator_active and not case['is_pre'] and label not in ('held_actual','startup_bridge')
        if case['regulator_floor']:
            # Existing local import regulation can unload each commanded-on
            # port to its original minimum stable output. Apply the SAME GO
            # electric/HRSG dependence in this static pressure assumption.
            re[source_gp] = 0.; re[source_go] = stable_floor*mask
            rh[source_gp] = 0.; rh[source_go] = stable_floor*mask*steam_per_kW*hrsg*case['heat_mask']
        held = label == 'held_actual'
        pre = case['is_pre']
        phase_diesel = case['phase_diesel']
        if case['is_response']:
            ready_gp=case['response_gt'];ready_go=case['response_on']
            re[source_gp]=0.;re[source_go]=0.;rh[source_gp]=0.;rh[source_go]=0.
            if case['regulator_floor']:
                re[ready_go]=stable_floor*mask
                rh[ready_go]=stable_floor*mask*steam_per_kW*hrsg*case['heat_mask']
            else:
                re[ready_gp]=mask
                rh[ready_gp]=mask*steam_per_kW*hrsg*case['heat_mask']
        if not held:
            re[phase_diesel]=1.
            upper[phase_diesel]=upper[diesel_ceiling]*case['diesel_mask']
            if execution_limits is not None and availability['GRID']>0. and case['base_label']!='GRID_loss':
                upper[phase_diesel]=0.  # no GRID/DG parallel preparation endpoint
            if grid_bridge_request and label=='response_GRID_loss':
                upper[phase_diesel]=0.  # requested OPEN bridge cannot use DG or GRID
            add({phase_diesel:1.,diesel_ceiling:-1.},-np.inf,0.,'phase.'+label+'.diesel_below_same_command')
            if coordination['time_energy_preparation'] and label not in ('arrived_current','regulated_current'):
                upper[phase_diesel]=min(upper[phase_diesel],support['electric_support_ceiling_kW'])
                add({phase_diesel:coordination['support_minutes']/60.},-np.inf,
                    max(0.,state.diesel_energy_remaining_kWh-support['overhead_shaft_work_kWh']),
                    'phase.'+label+'.sustained_shaft_work','shaft equivalent kWh',
                    source='registered duration plus startup, positive storage and bounded shaft loss')
        if held:
            # Fixed observed sources, never credited from this candidate command.
            re[gp] = 0.; re[idg] = 0.; rh[gp] = 0.; rh[ib] = 0.
        if pre:
            # No surplus escape route in the actionable current-service guard.
            if label == 'arrived_current':upper[rs] = 0.
            # The DG current command is not a new already-connected bridge.
            re[idg] = 0.; re[phase_diesel] = 1.
            upper[phase_diesel] = (min(upper[idg],readiness['diesel_arrived_cap_kW'])
                if readiness['diesel_connected'] else 0.)
            if execution_limits is not None:
                upper[phase_diesel]=min(upper[phase_diesel],execution_limits['effective_diesel_limit_kW'])
            if coordination['time_energy_preparation'] and label!='arrived_current':
                upper[phase_diesel]=min(upper[phase_diesel],support['electric_support_ceiling_kW'])
        if label=='regulated_current':
            upper[rs]=0.
        rc = vector(); rc[conv] = RT_TO_KW
        for i in range(4):
            re[ru+3*i] = 1.; rh[ru+3*i+1] = 1./heat_factor; rc[ru+3*i+2] = 1.
            for k in range(3):
                upper[ru+3*i+k] = demand[i, k]
                ratio = demand[i, k]/loads[i, k] if loads[i, k] > 0 else 0.
                coefficients = {ru+3*i+k: 1.}
                cut_lower = min(demand[i,k],readiness['actual_disconnected_kW'][i][k]) if held else 0.
                if not held:coefficients[int(contacts[i,k])] = -ratio
                add(coefficients, cut_lower, np.inf,
                    f'risk.{label}.{GROUPS[i]}.{CARRIERS[k]}.same_disconnected_contacts',
                    source='same generated physical contact cuts, not numerical unserved service')
        case['balance'] = np.array([re, rh, rc])
        case['rhs'] = np.array([demand[:, 0].sum(), demand[:, 1].sum()/heat_factor, demand[:, 2].sum()])
        if held:
            actual = np.asarray(readiness['actual_gt_kW'])
            case['rhs'][0] -= actual.sum()+readiness['actual_diesel_kW']
            case['rhs'][1] -= actual@hrsg*steam_per_kW+held_dispatch.get('boiler_steam_lbph',0.)
        if case['certificate'] is not None:
            certificate=case['certificate'];big_m=3.*rated+plant.emergency_diesel_kW+grid_import_limit_kW
            add({rs:1.,certificate:big_m},-np.inf,big_m,'preparation.'+label+'.certified_no_surplus',
                source='positive surplus explicitly invalidates every preparation credit in this copy')
            for j,credit in enumerate(case['credited_unmet']):
                add({int(credit):1.,ru+j:-1.},0.,np.inf,'preparation.'+label+'.uncredited_above_unserved')
                add({int(credit):1.,certificate:float(demand.reshape(-1)[j])},float(demand.reshape(-1)[j]),np.inf,
                    'preparation.'+label+'.failed_copy_has_no_credit')
        for r, rhs, carrier, unit in zip(case['balance'], case['rhs'], ('electric', 'steam', 'cooling'), ('kW', 'lb/h', 'kW')):
            add(r, rhs, rhs, f'risk.{label}.balance.{carrier}', unit,
                source='static single-command diagnostic; surplus defect is NOT real disposal')
        for index, master in zip(conv, converter_ceilings):
            upper[index] = upper[master]
            if case['base_label']=='ABS_loss' and index==conv[0]:upper[index]=0.
            if case['base_label'].startswith('EC_') and case['base_label'].endswith('_loss'):
                if index==conv[int(case['base_label'][3])]:upper[index]=0.
            if held:
                port = held_dispatch
                limits = [port.get('absorption_cooling_kW',0.)]+port.get('electric_chiller_cooling_kW',[0.]*3)
                upper[index] = min(upper[index],limits[list(conv).index(index)]/RT_TO_KW)
            else:
                add({index: 1., int(master): -1.}, -np.inf, 0., f'risk.{label}.{names[index]}.held_ceiling', 'RT',
                    source='original arrived converter ceiling, no new command')
        upper[rg] = 0. if case['base_label'] in ('GRID_loss','startup_bridge','grid_open_target') else upper[ig]
        if held:
            upper[rg] = min(upper[rg],held_dispatch.get('grid_import_kW',0.))
        elif case['base_label'] not in ('GRID_loss','startup_bridge','grid_open_target'):
            add({rg: 1., ceiling: -1.}, 0. if contract['grid_import_mode'] == 'fixed' else -np.inf, 0.,
                f'risk.{label}.shared_grid_command', source='original fixed or arrived_ceiling importer contract')
        add({int(index): float(value) for index, value in zip(conv, tower_co)}, -np.inf, tower_limit,
            f'risk.{label}.tower.rejection')
        if contract['diesel_mode'] == 'emergency_bus' and not held:
            coefficients = {phase_diesel: 1., rs: -1.}
            coefficients.update({ru+3*i: 1. for i in allowed})
            add(coefficients, -np.inf, float(demand[allowed, 0].sum()), f'risk.{label}.diesel.allowed_bus',
                source='same allowed bus; same diagnostic surplus defect marks unusable power')

    # z is nonnegative with NO finite upper bound. This epigraph always
    # extends an old feasible plan; it does not hard-cap online GT count.
    add({int(i): 1. for i in command_go} | {standby_excess: -1.}, -np.inf, 2.,
        'preparation.third_GT_on_exposure', 'GT count', kind='preference',
        source='soft current-command preference; no added physical standby capability')

    operating_config = operating
    operating = vector()
    operating[gp] = fuel_per_kW*plant.gas_price_usd_per_mmbtu*dt_h
    operating[ib] = plant.steam_kWh_per_lb/plant.boiler_eff/KWH_PER_MMBTU*plant.gas_price_usd_per_mmbtu*dt_h
    operating[ig] = illustrative_tariff(row.timestamp, plant)*dt_h
    operating[idg] = .30*dt_h; operating[iw] = 1e-8
    security_weight = float(security_gt_penalty_USD_per_kWh)
    if not math.isfinite(security_weight) or not 0 <= security_weight <= 10.:
        raise ValueError('security_gt_penalty_USD_per_kWh must be in [0,10]')
    cost = operating.copy(); cost[gp] += security_weight*dt_h
    for index in target_indices.values():cost[index] = float((config or {}).get('action_tracking_penalty_USD_per_kW_step', .20))*dt_h
    def priority_objective(group, prefix='current'):
        co = vector()
        for k in range(3):co[unmet(group, k)] = 1./max(1., float(loads[:, k].sum()))
        return (f'{prefix}_{GROUPS[group]}', co, 'normalized carrier ENS', group)

    constraints = [LinearConstraint(np.asarray(matrix), np.asarray(row_low), np.asarray(row_high))]
    variables = [dict(index=i, name=name, unit=units[i]) for i, name in enumerate(names)]
    diagnostic_input = {}
    if active_collector() is not None:
        diagnostic_input = dict(row=dict(timestamp=str(row.timestamp), outdoor_temperature_C=float(row.outdoor_temperature_C)),
            plant=plant.as_dict(), loads_kW=loads, availability=availability, state=asdict(state), dt_h=dt_h,
            grid_import_limit_kW=grid_import_limit_kW, diesel_start_available=diesel_start_available,
            gt_ramp_kW_per_h=gt_ramp_kW_per_h, config=config, tracking_targets=tracking_targets,
            security_gt_penalty_USD_per_kWh=security_weight, execution_contract=contract,
            joint_options=options, risk_loads=risk_loads, retention_eligible=retention_eligible,
            engineering_options=engineering, execution_context=context)
    locks = []; layers = []; reference_layers = []; backend_layers = []
    safety_release = []
    late_preference_fallbacks = []
    numerical_presentation_recoveries = []
    actual_running = np.asarray(context['gt_running'], dtype=bool)
    actual_diesel = float(state.last_diesel_kW)
    lex_values = [None]*4; x = None; final_objectives = []
    incumbent_source = None
    soft_optimization_stopped=False

    def run_layer(item, *, reference=False, lock=True, tolerance_override=None):
        nonlocal x, incumbent_source, soft_optimization_stopped
        name, co, unit, group = item
        rank = len(backend_layers)
        layer_started = time.perf_counter()
        result = solve_layer(milp, co, integrality=integrality, bounds=Bounds(lower, upper), constraints=constraints,
            options={'time_limit': solver_time_limit_sec, 'mip_rel_gap': 1e-6, 'presolve': False}, rank=rank,
            variables=variables, rows=metadata, locks=locks, input_context=diagnostic_input, row_scaling=False,previous_candidate=x)
        backend_layers.append(dict(rank=rank, layer=name, phase='service_reference' if reference else 'current_plan',
            backend=getattr(result, 'backend', 'injected'), numerical_version=getattr(result, 'numerical_version', None),
            status=int(result.status), attempts=getattr(result, 'attempts', [])))
        refinement=getattr(result,'priority_lock_refinement',None)
        if refinement is not None:
            numerical_presentation_recoveries.append(dict(layer=name,rank=rank,**copy.deepcopy(refinement)))
            backend_layers[-1]['priority_lock_refinement']=copy.deepcopy(refinement)
        if (result.status in (2,4) and name=='operating_cost_and_original_preferences'
                and not reference and x is not None):
            matrix_now,low_now,high_now=flatten_constraints(constraints)
            previous_validation=validate_vector(matrix_now,low_now,high_now,lower,upper,integrality,x,metadata)
            remaining=solver_time_limit_sec-(time.perf_counter()-layer_started)
            if (remaining>0. and previous_validation['max_original_violation']<=1e-7
                    and previous_validation['max_lock_violation']<=1e-10
                    and previous_validation['max_integrality_distance']<=1e-9):
                # A witnessed original domain must not be called physically
                # infeasible solely because this optional cost presentation
                # defeated native presolve. Retry only this current objective,
                # once, with positive row scaling and the SAME original-unit
                # posterior validation. No lock, bound or service tolerance moves.
                recovery=dict(layer=name,original_rank=rank,original_status=int(result.status),
                    original_message=str(result.message),remaining_layer_budget_seconds=remaining,
                    presentation='positive_row_scaling_same_original_domain',
                    previous_incumbent_validation=previous_validation,
                    previous_incumbent_returned_instead_of_optimization=False,
                    original_domain_and_thresholds_unchanged=True)
                rank=len(backend_layers)
                result=original_solve_layer(milp,co,integrality=integrality,bounds=Bounds(lower,upper),
                    constraints=constraints,options={'time_limit':remaining,'mip_rel_gap':1e-6,'presolve':False},
                    rank=rank,variables=variables,rows=metadata,locks=locks,input_context=diagnostic_input,row_scaling=True)
                recovery.update(recovery_rank=rank,recovery_status=int(result.status))
                numerical_presentation_recoveries.append(recovery)
                backend_layers.append(dict(rank=rank,layer=name,phase='same_layer_presentation_recovery',
                    backend=getattr(result,'backend','injected'),numerical_version=getattr(result,'numerical_version',None),
                    presentation='positive_row_scaling_same_original_domain',status=int(result.status),
                    attempts=getattr(result,'attempts',[])))
        if name == 'current_P0' and result.status == 2 and np.any(lower[go] > 0.) and not safety_release:
            # A minimum-run requirement can conflict with no-export balance.
            # Ask for a safety exit explicitly; execution still applies its
            # ordinary-stop hold or actual forced interlock. No supply is added.
            held = [int(i) for i in range(3) if lower[go[i]] > 0.]
            safety_release.extend(dict(device=f'GT_{i+1}', reason='strict_minimum_up_current_balance_infeasible',
                requested_safety_exit=True, original_solver_status=2,
                execution_must_apply_registered_stop_or_protection=True) for i in held)
            lower[go[held]] = 0.
            return run_layer(item, reference=reference, lock=lock, tolerance_override=tolerance_override)
        reused_incumbent = False
        if ((result.status != 0 or result.x is None) and name in OPTIONAL_POST_COST_LAYERS
                and not reference and x is not None):
            # A failed post-cost preference is not a new physical failure.
            # Recheck the previous accepted candidate against ALL original
            # current rows/bounds/locks. The failed native attempts stay once
            # in backend_layers; do not duplicate their historical payload.
            matrix_now,low_now,high_now = flatten_constraints(constraints)
            validation = validate_vector(matrix_now,low_now,high_now,lower,upper,integrality,x,metadata)
            if (validation['max_original_violation'] <= 1e-7
                    and validation['max_lock_violation'] <= 1e-10
                    and validation['max_integrality_distance'] <= 1e-9):
                reused_incumbent = True
                lock = False  # The retained preference value is not an optimum.
                summary = {key:value for key,value in validation.items() if not isinstance(value,list)}
                summary.update(original_row_count_checked=int(matrix_now.shape[0]),
                    original_variable_count_checked=n,all_original_rows_bounds_and_locks_checked=True)
                record = dict(layer=name,rank=rank,solver_status=int(result.status),
                    solver_message=str(result.message),source_layer=incumbent_source['layer'],
                    source_rank=incumbent_source['rank'],failed_attempts_backend_rank=rank,
                    reason='allowlisted_post_cost_layer_failed_original_domain_incumbent_retained',
                    optional_layer_optimum_claimed=False,new_optimum_lock_added=False,validation=summary,
                    original_physical_and_priority_tolerances_unchanged=True)
                late_preference_fallbacks.append(record)
                backend_layers[-1].update(fallback_used=True,optional_layer_optimum_claimed=False,
                    incumbent_validation=summary)
        if ((result.status!=0 or result.x is None) and not reused_incumbent and not reference
                and x is not None and late_soft_service_locks_complete(name,metadata,phase_copies,recovery=coordination['execution_recovery'])):
            # This late preference follows every current/actionable service
            # lock. A failed numerical optimization is not a new optimum.
            matrix_now,low_now,high_now=flatten_constraints(constraints)
            validation=validate_vector(matrix_now,low_now,high_now,lower,upper,integrality,x,metadata)
            if (validation['max_original_violation']<=1e-7 and validation['max_lock_violation']<=1e-10
                    and validation['max_integrality_distance']<=1e-9):
                reused_incumbent=True;lock=False;soft_optimization_stopped=True
                summary={key:value for key,value in validation.items() if not isinstance(value,list)}
                summary.update(original_row_count_checked=int(matrix_now.shape[0]),original_variable_count_checked=n,
                    all_original_rows_bounds_and_locks_checked=True)
                record=dict(layer=name,rank=rank,solver_status=int(result.status),solver_message=str(result.message),
                    stage='late_soft_after_complete_service',source_layer=incumbent_source['layer'],
                    source_rank=incumbent_source['rank'],failed_attempts_backend_rank=rank,
                    reason='complete_current_and_actionable_service_locks_fixed_late_soft_failed_prior_candidate_retained',
                    all_current_and_arrived_service_locks_complete=True,remaining_soft_optimization_skipped=True,
                    unoptimized_soft_layers=[item[0] for item in ordered[next(i for i,item in enumerate(ordered) if item[0]==name):]]+['operating_cost_and_original_preferences'],
                    preparation_optimum_unknown=name=='preparation_P1',
                    all_present_actionable_service_T_and_protection_locks_preserved=True,
                    optional_layer_optimum_claimed=False,new_optimum_lock_added=False,validation=summary,
                    original_physical_and_priority_tolerances_unchanged=True)
                late_preference_fallbacks.append(record)
                backend_layers[-1].update(fallback_used=True,optional_layer_optimum_claimed=False,incumbent_validation=summary)
        if (result.x is None or result.status != 0) and not reused_incumbent:
            error = ModelConstraintInfeasible if result.status == 2 else SolverExecutionError
            failure = error(f'Coordinated current MILP failure: layer={name}, rank={rank}, status={result.status}, message={result.message}')
            failure.solver_backend_attempts = backend_layers
            failure.joint_diagnostic = dict(options=options, completed_layers=layers,
                completed_reference_layers=reference_layers, failing_layer=name,
                engineering_options=engineering, execution_context=context, minimum_time_safety_release=safety_release)
            raise failure
        if not reused_incumbent:
            x = np.asarray(result.x, dtype=float)
            incumbent_source = dict(layer=name,rank=rank)
        value = float(co@x)
        layer = dict(rank=rank, name=name, objective_value=value, unit=unit)
        if refinement is not None and refinement.get('accepted'):
            layer.update(optimization_status='gap_certified_feasible_numerical_refinement',exact_optimum_claimed=False,
                priority_lock_refinement=copy.deepcopy(refinement))
        if reused_incumbent:layer.update(optimization_status='not_optimized_reused_preceding_feasible_incumbent',
            optional_layer_optimum_claimed=False,solver_status=int(result.status),fallback_used=True)
        if reused_incumbent and name=='held_actual_diagnostic':
            layer['objective_value']=None  # No arbitrary unoptimized held deficit metric.
        if group is not None and not reference:lex_values[group] = value
        if lock:
            tolerance = (tolerance_override if tolerance_override is not None else
                lex_lock_tolerance(group, value, config) if group is not None else options['risk_tolerance'])
            bound = value+tolerance
            constraints.append(LinearConstraint(co, -np.inf, bound))
            locks.append(dict(source_rank=rank, objective=co.copy(), objective_constant=0.,
                incumbent_raw_objective=value, tolerance=tolerance, upper=bound, relation='<=', unit=unit,
                source='temporary service reference' if reference else 'raw original priority or explicit preparation optimum'))
            metadata.append(dict(name=f'lock.{name}', unit=unit, source='explicit lexicographic lock', kind='priority_lock'))
            layer.update(lock_tolerance=tolerance, lock_upper=bound)
        if reference:reference_layers.append(layer)
        else:
            layers.append(layer); final_objectives.append(item)
        return value

    phase_copies = {case['name']:case for case in copies if case['name'] in ('arrived_current','regulated_current','held_actual')}
    preparation_copies = [case for case in copies if case['name'] not in phase_copies and (not recovery or case['is_response'])]
    bridge_case=next((case for case in copies if case['name']=='startup_bridge'),None)
    grid_target_case=next((case for case in copies if case['name']=='grid_open_target'),None)
    def phase_objective(group,phase='arrived_current'):
        co = vector(); case = phase_copies[phase]
        for k in range(3):co[case['unmet']+group*3+k] = 1./max(1.,float(loads[:,k].sum()))
        return (f'{phase}_{GROUPS[group]}',co,'normalized conditional current carrier ENS at the named execution stage',None)
    # Original normalized P0/P1 locks precede every new preference/guard.
    run_layer(priority_objective(0)); run_layer(priority_objective(1))
    if 'arrived_current' in phase_copies:
        run_layer(phase_objective(0),tolerance_override=0.)
        run_layer(phase_objective(1),tolerance_override=0.)
    if 'regulated_current' in phase_copies:
        run_layer(phase_objective(0,'regulated_current'),tolerance_override=0.)
        run_layer(phase_objective(1,'regulated_current'),tolerance_override=0.)
    # Request continuity is subordinate to the unchanged present P0/P1 service
    # locks. A zero request cut is never credited as physical delivered energy.
    # Keep these mandatory before any recoverable late soft solver stop.
    for group in (0, 1):
        request = vector()
        for carrier in range(3):
            request[contacts[group, carrier]] = 1. / max(1., float(loads[:, carrier].sum()))
        run_layer((f'ordinary_{GROUPS[group]}_active_cut', request,
                   'normalized explicitly disconnected carrier request', None), tolerance_override=0.)
    priority_constraints = list(constraints)
    priority_locks = copy.deepcopy(locks); priority_metadata = copy.deepcopy(metadata)
    reference_values = list(lex_values)
    for group in (2, 3):reference_values[group] = run_layer(priority_objective(group, 'reference'), reference=True)
    run_layer(('reference_operating_cost', cost, 'USD per current step', None), reference=True)
    reference_x = x.copy()
    reference_unserved = reference_x[14:26].reshape(4, 3).copy()
    for layer, item in zip(reference_layers, [priority_objective(2), priority_objective(3),
                                            ('reference_operating_cost', cost, 'USD per current step', None)]):
        layer['final_objective_value'] = float(item[1]@reference_x)
    if active_collector() is not None:active_collector().postprocess(reference_x.copy())

    # Remove ONLY temporary P2/P3/cost locks. Physical rows and original P0/P1
    # canonical locks are copied without changing any value or tolerance.
    constraints[:] = priority_constraints
    locks[:] = priority_locks; metadata[:] = priority_metadata
    allowed_budget = np.zeros((2, 3))

    standby = []
    if options['preserve_one_gt_standby']:
        co = vector(); co[standby_excess] = 1.
        standby = [('standby_third_GT_exposure', co, 'GT count above two commanded on; not minimum total GT count', None)]
    co=vector();co[command_go]=1.
    standby += [('normal_online_GT_count_after_preparation',co,'commanded online GT count; soft preference only',None)]
    preparation = []
    bridge_preparation=[]
    if bridge_case is not None:
        for group in (0,1):
            co=vector()
            for k in range(3):
                co[int(bridge_case['credited_unmet'][group*3+k])]=1./max(1.,float(loads[:,k].sum()))
            bridge_preparation.append(('startup_bridge_'+GROUPS[group],co,
                'normalized current-carrier deficit before backup connection or during open transfer',None))
        co=vector();co[bridge_case['surplus']]=1.
        bridge_preparation.append(('startup_bridge_surplus',co,'unabsorbable electric kW; never a real sink',None))
    if copies:
        for group in (0, 1):
            co = vector()
            for case in preparation_copies:
                for k in range(3):
                    index=case['unmet']+group*3+k if case['credited_unmet'] is None else int(case['credited_unmet'][group*3+k])
                    co[index] = 1./max(1., float(case['loads'][:, k].sum()))
            preparation.append((f'preparation_{GROUPS[group]}', co, 'sum of normalized static carrier ENS; not probability', None))
    if copies or fuel is not None:
        co = vector()
        if fuel is not None:co[fuel] = 1.
        for case in preparation_copies:co[case['surplus']] = 1.
        preparation.append(('preparation_defect', co, 'kW: fuel preference excess plus static unabsorbable surplus', None))
    if regulator_active:
        co = vector(); co[regulator_excess] = 1.
        preparation.append(('regulator_GT_excess', co, 'kW above existing minimum stable GT outputs', None))
    # In a currently healthy, undisturbed state, avoid acquiring a new source
    # merely to improve a hypothetical outage copy. Current P0--P3 optima
    # still precede this preference, so necessary starts remain feasible.
    # OFF->ON alone is counted: ordinary legal stops are not discouraged.
    current_sources_healthy = all(float(v) >= 1.-1e-9 for v in availability.values())
    actual_fuel_pressure = fuel_hours is not None and fuel_hours < support_hours
    any_start_pending = bool(any(context['gt_start_pending']))
    start_reason = ('disabled' if not engineering['execution_aware_start_preference']
        else 'observed_device_or_GRID_unavailability' if not current_sources_healthy
        else 'observed_critical_material_shortfall' if observed_critical_material
        else 'observed_actual_diesel_fuel_pressure' if actual_fuel_pressure
        else 'actual_GT_start_pending' if any_start_pending else 'current_service_before_new_source_preparation')
    start_active = start_reason == 'current_service_before_new_source_preparation'
    start_preference = []
    if start_active:
        co = vector(); co[go[~actual_running]] = 1.
        start_preference = [('normal_current_new_GT_starts', co, 'actual OFF to planned ON count', None)]
    normal_startup_preference = dict(requested=engineering['execution_aware_start_preference'],
        active=start_active, reason=start_reason, current_sources_healthy=current_sources_healthy,
        observed_critical_material=observed_critical_material, actual_diesel_fuel_pressure=actual_fuel_pressure,
        any_actual_start_pending=any_start_pending, actual_GT_running_mask=actual_running.tolist(),
        resolved_GT_start_delay_minutes=contract['gt_start_delay_minutes'],
        minimum_new_GT_starts=None, current_service_locks_precede_start_preference=True,
        ordinary_stops_penalized=False, hypothetical_preparation_can_be_worse=True,
        source_and_startup_limits_unchanged=True, command_is_immediate_execution=False)
    # R5 is an explicit alternative to R4's start-only preference. A healthy
    # current source may otherwise be stopped for static economics and become
    # unavailable when a later ordinary demand change requires a new start.
    # No later demand is input here: preserve the CURRENT legal combination
    # when its Hamming optimum is zero under the preceding current service
    # locks. Necessary stops/starts have a positive optimum and remain allowed.
    commitment_reason = ('disabled' if not engineering['execution_aware_commitment_preference']
        else 'observed_device_or_GRID_unavailability' if not current_sources_healthy
        else 'observed_critical_material_shortfall' if observed_critical_material
        else 'observed_actual_diesel_fuel_pressure' if actual_fuel_pressure
        else 'actual_GT_start_pending' if any_start_pending else 'current_service_before_GT_commitment_changes')
    commitment_active = commitment_reason == 'current_service_before_GT_commitment_changes'
    commitment_preference = []
    if commitment_active:
        co = vector(); co[state_changes] = 1.
        commitment_preference = [('normal_current_GT_state_changes', co, 'actual GT ON/OFF state changes', None)]
    normal_commitment_preference = dict(requested=engineering['execution_aware_commitment_preference'],
        active=commitment_active, reason=commitment_reason, current_sources_healthy=current_sources_healthy,
        observed_critical_material=observed_critical_material, actual_diesel_fuel_pressure=actual_fuel_pressure,
        any_actual_start_pending=any_start_pending, actual_GT_running_mask=actual_running.tolist(),
        minimum_GT_state_changes=None, current_service_locks_precede_commitment_preference=True,
        ordinary_stops_penalized=True, necessary_stops_and_starts_allowed=True,
        three_online_GT_may_be_retained=True, static_economics_can_be_worse=True,
        source_and_startup_limits_unchanged=True, command_is_immediate_execution=False)
    # Preserve the legacy R6 request metadata below. The actual v15 order is
    # built afterward: both critical preparation layers precede continuity,
    # behind healthy current and pre-ready service. No future schedule is used.
    p0_preparation = []
    remaining_preparation = preparation
    if commitment_active and engineering['p0_preparation_before_commitment']:
        p0_preparation = [item for item in preparation if item[0] == 'preparation_P0']
        remaining_preparation = [item for item in preparation if item[0] != 'preparation_P0']
    normal_commitment_preference.update(
        p0_preparation_before_commitment_requested=engineering['p0_preparation_before_commitment'],
        p0_preparation_before_commitment_active=bool(p0_preparation),
        p0_preparation_layers_before_commitment=[item[0] for item in p0_preparation])
    service = [priority_objective(2), priority_objective(3)]
    if 'arrived_current' in phase_copies:
        service += [phase_objective(2),phase_objective(3)]
    if 'regulated_current' in phase_copies:
        service += [phase_objective(2,'regulated_current'),phase_objective(3,'regulated_current')]
    # Both registered critical preparation layers precede continuity. Current
    # healthy P0--P3 and pre-ready service remain ahead of preparation.
    command_support_preference = []
    if command_support_active:
        co=vector();co[command_support_excess]=1.
        command_support_preference=[('actual_command_shaft_support',co,'shaft equivalent kWh excess over registered duration budget',None)]
    if recovery:
        co=vector();co[protection_excess]=protection_weights
        protection_preference=[('protection_target_excess',co,'weighted kW above unchanged GT relay reference; current observed hold utilization only',None)]
        endpoint_DG_preference=[]
        if endurance_active and preparation_copies:
            co=vector()
            for case in preparation_copies:co[case['phase_diesel']]=1.
            endpoint_DG_preference=[('conditional_DG_endpoint_pressure',co,
                'sum of conditional R-endpoint DG kW; not simultaneous output or actual shaft work',None)]
        # Retain a useful real pending start before minimizing total online
        # count, without crediting its output in current/pre-ready balances.
        third_GT=[item for item in standby if item[0]=='standby_third_GT_exposure']
        online_count=[item for item in standby if item[0]!='standby_third_GT_exposure']
        reserve_preference=[]
        if reserve_active:
            co=vector();co[reserve_excess]=1.
            reserve_preference=[('current_critical_energy_reserve',co,'shaft equivalent kWh soft reserve excess',None)]
        grid_target_preference=[]
        if grid_target_case is not None:
            for group in (0,1):
                co=vector()
                for carrier in range(3):
                    co[int(grid_target_case['credited_unmet'][3*group+carrier])]=1./max(1.,float(loads[:,carrier].sum()))
                grid_target_preference.append(('grid_open_target_'+GROUPS[group],co,
                    'conditional ordinary GT target critical deficit; no promised completion time',None))
        if reserve_active:
            # Preserve a useful conditional critical restoration request before
            # trading lower-priority service for energy reserve. Startup work
            # must not shave a critical permission below its necessary load.
            critical_preparation=[item for item in preparation if item[0] in ('preparation_P0','preparation_P1')]
            other_preparation=[item for item in preparation if item[0] not in ('preparation_P0','preparation_P1')]
            ordered=(protection_preference+command_support_preference+grid_target_preference+bridge_preparation+critical_preparation+
                reserve_preference+service+other_preparation+commitment_preference+third_GT+
                endpoint_DG_preference+online_count)
        else:
            ordered=(protection_preference+command_support_preference+grid_target_preference+service+bridge_preparation+preparation+
                commitment_preference+third_GT+endpoint_DG_preference+online_count)
    else:
        ordered = (preparation+command_support_preference+service+standby if endurance_active else
            service+preparation+commitment_preference+standby)
    # A normalized P2/P3 lock allowance would become a real contact cut and
    # stack with the unchanged fixed-execution remainder. Preserve the new
    # current service optima exactly; original P0/P1/reference locks stay intact.
    for item in ordered:
        value = run_layer(item, tolerance_override=0. if item[3] in (2, 3) or item[0].startswith(('arrived_current_','regulated_current_'))
            or item[0] in ('normal_current_new_GT_starts', 'normal_current_GT_state_changes', 'actual_command_shaft_support','protection_target_excess','current_critical_energy_reserve') else None)
        if item[0] == 'normal_current_new_GT_starts':normal_startup_preference['minimum_new_GT_starts'] = value
        if item[0] == 'normal_current_GT_state_changes':normal_commitment_preference['minimum_GT_state_changes'] = value
        if soft_optimization_stopped:break
    actual_layer_order = [item['name'] for item in layers]
    normal_commitment_preference['P1_preparation_precedes_commitment'] = (
        'preparation_P1' in actual_layer_order and 'normal_current_GT_state_changes' in actual_layer_order
        and actual_layer_order.index('preparation_P1') < actual_layer_order.index('normal_current_GT_state_changes'))
    if not soft_optimization_stopped:
        run_layer(('operating_cost_and_original_preferences', cost, 'USD per current step', None),
            lock=engineering['continuity'] or engineering['contact_mode'] == 'separate',
            tolerance_override=engineering['cost_equivalence_USD_per_step'])
    cost_optimum = None if soft_optimization_stopped else float(cost@x)
    if engineering['continuity'] and not soft_optimization_stopped:
        co = vector(); co[state_changes] = 1.
        run_layer(('actual_GT_combination_changes', co, 'GT changes', None))
    if engineering['contact_mode'] == 'separate' and not soft_optimization_stopped:
        co = vector(); co[contacts[2:].ravel()] = 1.
        run_layer(('ordinary_low_priority_disconnected_power', co, 'sum of carrier kW', None))
    if engineering['continuity'] and not soft_optimization_stopped:
        co = vector(); co[power_changes] = 1.
        run_layer(('actual_GT_power_change', co, 'kW', None), lock=False)
    x, grid_headroom = _guarded_grid_headroom(x, requested=engineering['grid_headroom_preference'],
        separate_ceiling=separate_ceiling, ceiling=ceiling, gp=gp, go=go, stable_floor=stable_floor,
        context=context, lower=lower, upper=upper, constraints=constraints)
    if coordination['cooling_preparation'] and not soft_optimization_stopped:
        # Nonnegative gap minimization keeps the original solver's positive
        # lex-lock domain. Lock this permission preference before the final
        # GRID tie-break so one free permission coordinate cannot erase another.
        co=vector();co[converter_permission_gap]=1.
        run_layer(('healthy_converter_permission',co,'minimum unused healthy ordinary converter permission after service and cost locks',None),
            lock=True,tolerance_override=0.)
    if separate_ceiling and 'regulated_current' in phase_copies and not soft_optimization_stopped:
        endpoint=phase_copies['regulated_current']
        endpoint_loss=x[endpoint['unmet']:endpoint['unmet']+12].reshape(4,3)
        current_loss=x[14:26].reshape(4,3)
        # A larger permission keeps the same fixed boiler and a jointly
        # balanced converter/tower endpoint at regulated GT floors. It never
        # certifies the unresolved continuous GT plus DG mechanical path.
        safe_endpoint=bool(np.all(endpoint_loss<=current_loss+1e-7))
        before=float(x[ceiling])
        if safe_endpoint:
            co=vector();co[ceiling]=-1.
            run_layer(('coordinated_GRID_permission',co,'maximum legal ordinary import permission after current regulated-service locks',None),lock=False)
        grid_headroom.update(requested=True,method='joint_regulated_current_endpoint_under_original_locks',
            reason='regulated_current_endpoint_preserves_service' if safe_endpoint else 'regulated_current_endpoint_has_extra_service_loss',
            applied=bool(x[ceiling]>before),ceiling_before_kW=before,ceiling_after_kW=float(x[ceiling]),
            same_fixed_boiler_and_joint_tower_verified=True,all_non_ceiling_variables_unchanged=False,
            finite_GT_DG_path_certified=False)
    for layer, item in zip(layers, final_objectives):layer['final_objective_value'] = float(item[1]@x)

    gts = np.maximum(0., x[gp]); lost = np.maximum(0., x[14:26].reshape(4, 3)); served = loads-lost
    # Physical balances are evaluated on USED import, never command ceiling.
    residual = main_balance@x-main_rhs
    residuals = [float(residual[0]), float(residual[2]), float(residual[1]*heat_factor)]
    if max(map(abs, residuals)) > 1e-4 or abs(float(residual[1])) > 1e-4:
        raise SolverExecutionError(f'Joint main balance error: kW={residuals}, steam_lbph={residual[1]}')
    current_action = dict(gt_electric_kW=gts.tolist(), grid_import_kW=float(x[ig]), diesel_kW=float(x[idg]),
        absorption_cooling_kW=float(x[ia]*RT_TO_KW), electric_chiller_cooling_kW=(x[ie]*RT_TO_KW).tolist(),
        boiler_steam_lbph=float(x[ib]), hrsg_steam_lbph=float(gts@hrsg*steam_per_kW),
        tower_rejection_kW=float(tower@x), gt_fuel_MMBtu_h=float(gts.sum()*fuel_per_kW))
    command_action = copy.deepcopy(current_action); command_action['grid_import_kW'] = float(x[ceiling])
    command_action['gt_electric_kW']=x[command_gp].tolist()
    command_action['hrsg_steam_lbph']=float(x[command_gp]@hrsg*steam_per_kW)
    command_action['gt_fuel_MMBtu_h']=float(x[command_gp].sum()*fuel_per_kW)
    command_action['absorption_cooling_kW']=float(x[converter_ceilings[0]]*RT_TO_KW)
    command_action['electric_chiller_cooling_kW']=(x[converter_ceilings[1:]]*RT_TO_KW).tolist()
    command_action['diesel_kW']=float(x[diesel_ceiling])
    # Constant held-copy metrics never influence a command or pretend that a
    # newly issued command can repair an already observed transition gap.
    held_case = phase_copies['held_actual']; held_co = vector()
    for g in range(4):
        for k in range(3):held_co[held_case['unmet']+g*3+k] = (4-g)/max(1.,float(loads[:,k].sum()))
    held_co[held_case['surplus']] = 1.
    selected_x = x.copy()
    if not soft_optimization_stopped:
        run_layer(('held_actual_diagnostic',held_co,'diagnostic normalized deficit plus surplus',None),lock=False)
    # This independent copy has no master coupling; retain the selected command
    # vector in case a solver chooses another equivalent master for diagnostics.
    held_optimized = not soft_optimization_stopped and not layers[-1].get('fallback_used',False)
    held_solution = x.copy()
    x = selected_x
    if held_optimized:
        x[held_case['converters'][0]:held_case['surplus']+1] = held_solution[held_case['converters'][0]:held_case['surplus']+1]
    x,m18_conditional_return_permission=finalize_conditional_grid_permission(x,constraints=constraints,
        lower=lower,upper=upper,integrality=integrality,rows=metadata,variables=variables,
        ceiling=int(ceiling),execution_limits=execution_limits,
        objectives=[(item[0],item[1]) for item in final_objectives])
    def build_command(value):
        command_gt=value[command_gp]
        return dict(gt_electric_kW=command_gt.tolist(),grid_import_kW=float(value[ceiling]),
            diesel_kW=float(value[diesel_ceiling]),boiler_steam_lbph=float(value[ib]),
            absorption_cooling_kW=float(value[converter_ceilings[0]]*RT_TO_KW),
            electric_chiller_cooling_kW=(value[converter_ceilings[1:]]*RT_TO_KW).tolist(),
            hrsg_steam_lbph=float(command_gt@hrsg*steam_per_kW),tower_rejection_kW=float(tower@value),
            gt_fuel_MMBtu_h=float(command_gt.sum()*fuel_per_kW))
    # Only actual queued coordinates are finalized. Independent ig/idg and
    # pending current GT ports remain internal original-domain residuals.
    emitted={int(i):1. for i in np.r_[ib,ceiling,diesel_ceiling,command_gp,contacts.reshape(-1)]}
    emitted.update({int(i):max(RT_TO_KW,float(tower_co[j])) for j,i in enumerate(np.r_[ia,ie])})
    emitted.update({int(i):RT_TO_KW for i in converter_ceilings})
    for j,i in enumerate(command_gp):emitted[int(i)]=max(1.,float(hrsg[j]*steam_per_kW),fuel_per_kW)
    x,command_action,command_finalization=finalize_command_vector(x,constraints=constraints,lower=lower,upper=upper,
        integrality=integrality,rows=metadata,variables=variables,emitted_coordinates=emitted,build_command=build_command,
        objectives=[(item[0],item[1]) for item in final_objectives])
    gts=x[gp].copy();lost=np.maximum(0.,x[14:26].reshape(4,3));served=loads-lost
    residual=main_balance@x-main_rhs;residuals=[float(residual[0]),float(residual[2]),float(residual[1]*heat_factor)]
    current_action=dict(gt_electric_kW=gts.tolist(),grid_import_kW=float(x[ig]),diesel_kW=float(x[idg]),
        absorption_cooling_kW=float(x[ia]*RT_TO_KW),electric_chiller_cooling_kW=(x[ie]*RT_TO_KW).tolist(),
        boiler_steam_lbph=float(x[ib]),hrsg_steam_lbph=float(gts@hrsg*steam_per_kW),
        tower_rejection_kW=float(tower@x),gt_fuel_MMBtu_h=float(gts.sum()*fuel_per_kW))
    risk_records = {}
    for case in copies:
        side = case['balance']@x-case['rhs']; conv = x[case['converters']]
        case_gt = (np.asarray(readiness['actual_gt_kW']) if case['name']=='held_actual' else
            stable_floor*x[case['response_on']]*case['gt_mask'] if case['is_response'] and case['regulator_floor'] else
            x[case['response_gt']]*case['gt_mask'] if case['is_response'] else
            stable_floor*x[case['source_go']]*case['gt_mask'] if case['regulator_floor'] else x[case['source_gp']]*case['gt_mask'])
        risk_records[case['name']] = dict(single_shared_command=True, loads_kW=case['loads'].tolist(),
            gt_electric_kW=case_gt.tolist(), hrsg_gt_basis_kW=(case_gt*case['heat_mask']).tolist(),
            hrsg_steam_lbph=float((case_gt*case['heat_mask'])@hrsg*steam_per_kW),
            GT_source_basis=('actual_observed_ports_only' if case['name']=='held_actual' else
                'retained_currently_running_GT_same_original_ordinary_ramp' if case['name']=='startup_bridge' else
                'same_arrived_command_actual_running_only' if case['is_pre'] else
                'current_command_on_at_existing_stable_floor' if case['regulator_floor']
                             else 'shared_conditional_command_GT_with_named_loss'),
             diesel_kW=(readiness['actual_diesel_kW'] if case['name']=='held_actual' else
                 float(x[case['phase_diesel']])),
             boiler_steam_lbph=(held_dispatch.get('boiler_steam_lbph',0.) if case['name']=='held_actual' else float(x[ib])),
             grid_import_kW=float(x[case['grid']]),
            absorption_cooling_kW=float(conv[0]*RT_TO_KW), electric_chiller_cooling_kW=(conv[1:]*RT_TO_KW).tolist(),
            unserved_kW=x[case['unmet']:case['unmet']+12].reshape(4, 3).tolist(),
            electric_surplus_defect_kW=float(x[case['surplus']]),
            balance_residuals=[float(side[0]), float(side[2]), float(side[1]*heat_factor)],
            steam_balance_residual_lbph=float(side[1]), diagnostic_not_actual_execution=True,
            effective_preparation_credit=(bool(x[case['certificate']]>.5) and float(x[case['surplus']])<=1e-7) if case['certificate'] is not None and case['name']!='grid_open_target' else False,
            has_physical_balance_certificate=(bool(x[case['certificate']]>.5) and float(x[case['surplus']])<=1e-7) if case['certificate'] is not None else False,
            executable_preparation_credit=False,
            R_minute_actual_readiness_guaranteed=False,
            R_bounded_conditional_endpoint=bool(case['is_response']),
            completion_time_bound_minutes=coordination['response_minutes'] if case['is_response'] else None,
            continuous_path_executable_credit=False,
            preparation_status=('balanced_static_endpoint_not_continuous_path' if case['certificate'] is not None and x[case['certificate']]>.5 and x[case['surplus']]<=1e-7 else 'failed_or_diagnostic_no_preparation_credit') if recovery else 'legacy_conditional_witness_not_certified',
            no_surplus_sink_added=True)
        if case['name']=='held_actual' and not held_optimized:
            # Observed GT/DG ports and the effective fixed boiler setting are
            # retained. Unoptimized conversion/deficit values are not witnesses.
            risk_records[case['name']].update(status='unknown_solver_failure',
                unserved_kW=None,absorption_cooling_kW=None,electric_chiller_cooling_kW=None,
                grid_import_kW=None,electric_surplus_defect_kW=None,balance_residuals=None,
                steam_balance_residual_lbph=None,optimized_witness=False,
                failure_backend_rank=layers[-1]['rank'])
    risk_records['held_actual']['held_dispatch_provenance']=held_dispatch_provenance
    security_penalty = security_weight*dt_h*float(gts.sum())
    tracking_penalty = sum(float(cost[index]*x[index]) for index in target_indices.values())
    diagnostic = dict(schema='coordinated_dispatch.current_service_handoff.v1', options=options, layers=layers,
        command_finalization=command_finalization,
        critical_reserve_multiplier=options['critical_reserve_multiplier'],
        critical_envelope_active=options['critical_reserve_multiplier'] > 1.,
        critical_envelope_is_prediction=False,
        critical_envelope_semantics='Registered static current-demand envelope; main loads and low history unchanged',
        fuel_excess_kW=0. if fuel is None else float(x[fuel]),
        fuel_preference_bound_kW=None if fuel is None else float(state.diesel_energy_remaining_kWh/options['support_hours']),
        risk_scenarios=risk_records, grid_used_kW=float(x[ig]), command_grid_ceiling_kW=float(x[ceiling]),
        separate_grid_ceiling=separate_ceiling, existing_grid_contract=contract['grid_import_mode'],
        low_history_supplied=risk_loads is not None, risk_surplus_is_actual_sink=False,
        grid_tracking_basis='generated command ceiling; operating tariff uses current imported energy',
        scenario_weights_are_probabilities=False, future_exogenous_input=False, actual_time_advanced=0.,
        single_command_not_future_sequence=True, master_variable_count=26, total_variable_count=n,
        current_priority_locks_unchanged=True,
        preparation_semantics='Static current stress copies and registered duration preference, not predictions or real disposal')
    diagnostic.update(preserve_one_gt_standby=options['preserve_one_gt_standby'],
        standby_soft_preference_active=options['preserve_one_gt_standby'],
        standby_third_gt_on_excess=float(x[standby_excess]),
        standby_planned_GT_on=x[command_go].tolist(), standby_planned_GT_on_count=float(x[command_go].sum()),
        standby_layer_name='standby_third_GT_exposure', standby_contract_id=STANDBY_CONTRACT_ID,
        standby_is_execution_guarantee=False,
        standby_semantics='Soft third commanded-on GT exposure after original P0/P1 locks; not arrived reserve or future protection',
        numerical_contract_id=NUMERICAL_CONTRACT_ID, solver_row_scaling=bool(numerical_presentation_recoveries),
        primary_solver_row_scaling=False, original_validation_domain_unchanged=True,
        canonical_problem_changed=True, original_physical_capacity_and_current_priority_locks_unchanged=True,
        original_backend_validation_thresholds_unchanged=True)
    diagnostic.update(regulator_consistent=options['regulator_consistent'], regulator_pressure_active=regulator_active,
        regulator_excess_kW=0. if regulator_excess is None else float(x[regulator_excess].sum()),
        regulator_excess_by_GT_kW=[0., 0., 0.] if regulator_excess is None else x[regulator_excess].tolist(),
        command_GT_on=x[command_go].tolist(), regulator_floor_GT_kW=(stable_floor*x[command_go]).tolist(),
        regulator_semantics='Existing stable-floor pressure assumption; not arrived output or a temporal prediction',
        regulator_is_execution_guarantee=False)
    command_disconnected = np.maximum(0., x[contacts])
    retention_selected = bool(x[retention] > .5)
    diagnostic.update(connection_mode=options['connection_mode'], retention_eligible=retention_eligible,
        retention_selected=retention_selected, connection_gate_active=retention_selected,
        command_disconnected_kW=command_disconnected.tolist(), planned_unserved_kW=lost.tolist(),
        automatic_shedding_not_overridden=True, eligibility_requires_no_pending_GT_start=True,
        contact_variable_count=12, critical_cold_contact_variable_count=2, retention_binary_count=1,
        retention_means_no_new_GT_command=True)
    def reference_action(y, command):
        powers = y[command_gp if command else gp]
        return dict(gt_electric_kW=powers.tolist(), grid_import_kW=float(y[ceiling if command else ig]),
            diesel_kW=float(y[idg]), absorption_cooling_kW=float(y[ia]*RT_TO_KW),
            electric_chiller_cooling_kW=(y[ie]*RT_TO_KW).tolist(), boiler_steam_lbph=float(y[ib]),
            hrsg_steam_lbph=float(powers@hrsg*steam_per_kW), tower_rejection_kW=float(tower@y),
            gt_fuel_MMBtu_h=float(powers.sum()*fuel_per_kW))
    diagnostic.update(service_switch_contract_id=SERVICE_SWITCH_CONTRACT_ID, features_enabled=True,
        service_reference=dict(unserved_kW=reference_unserved.tolist(), action=reference_action(reference_x, True),
            planned_current_action=reference_action(reference_x, False), normalized_lex_values=reference_values,
            cost_USD_step=float(operating@reference_x), optimization_objective_USD_step=float(cost@reference_x),
            layers=reference_layers, reference_computed_before_preparation=True,
            per_carrier_values_are_one_lexicographic_reference_not_independent_minima=True),
        service_budget=dict(enabled=False, fractions=[0., 0.],
            allowed_extra_kWh=allowed_budget.tolist(),
            used_extra_kWh=np.maximum(0.,dt_h*(lost[2:]-reference_unserved[2:])).tolist(),
            violation_kWh=x[budget_violation].tolist(), units='kWh per current original decision step',
            actual_delayed_ENS_is_not_bounded=True),
        handoff_enabled=options['handoff_enabled'],
        handoff_copy_name='DG_withdrawal_GRID_ceiling' if options['handoff_enabled'] else None,
        handoff_copy_status='registered_same_new_command_pressure' if options['handoff_enabled'] else 'disabled',
        handoff_is_restoration_prediction=False, handoff_is_pre_arrival_supply=False,
        coordination_preference='current_disturbed_endurance' if endurance_active else 'service_first', objective_order=[z['name'] for z in layers],
        temporary_reference_locks_removed=True, thermal_model_added=False)
    if active_collector() is not None:active_collector().postprocess(x.copy())
    same_state = {}

    def probe(name, *, hold=False, arrived=False):
        """Side solve on copied constraints. Never dispatch or advance time."""
        probe_lower = lower.copy(); probe_upper = upper.copy()
        probe_upper[14:26] = 0.
        rows = copy.deepcopy(priority_metadata[:np.asarray(matrix).shape[0]])
        extra = []; extra_low = []; extra_high = []
        def fixed(index, value, label):
            co = vector(); co[index] = 1.
            extra.append(co); extra_low.append(value); extra_high.append(value)
            rows.append(dict(name='diagnostic.'+label, unit=units[index], kind='diagnostic', source='same actual-state side solve'))
        if hold:
            for i in range(3):fixed(int(go[i]), float(actual_running[i]), f'GT_{i+1}.keep_actual_combination')
        if arrived:
            dispatch = held_dispatch
            for i in range(3):fixed(i, context['actual_gt_kW'][i], f'GT_{i+1}.actual_output')
            fixed(idg, float(state.last_diesel_kW), 'diesel.actual_output')
            fixed(ib, float(dispatch.get('boiler_steam_lbph', 0.)), 'boiler.arrived_output')
            probe_upper[ig] = min(probe_upper[ig], dispatch.get('grid_import_kW',0.))
            probe_upper[ceiling] = min(probe_upper[ceiling], dispatch.get('grid_import_kW',0.))
            if execution_limits is not None:
                probe_upper[idg] = min(probe_upper[idg],dispatch.get('diesel_kW',0.))
            probe_upper[ia] = min(probe_upper[ia], float(dispatch.get('absorption_cooling_kW', 0.))/RT_TO_KW)
            probe_upper[ie] = np.minimum(probe_upper[ie], np.asarray(dispatch.get('electric_chiller_cooling_kW', [0.]*3))/RT_TO_KW)
            disconnected = np.asarray(context['actual_disconnected_kW'])
            for p in range(4):
                for k in range(3):fixed(int(contacts[p, k]), float(min(disconnected[p, k], loads[p, k])), f'{GROUPS[p]}.{CARRIERS[k]}.actual_cut')
        probe_constraints = [constraints[0]]
        if extra:probe_constraints.append(LinearConstraint(np.asarray(extra), extra_low, extra_high))
        scope = 'arrived_ports_and_contacts_only' if arrived else 'generated_command_not_immediate_execution'
        try:
            result = solve_layer(milp, vector(), integrality=integrality, bounds=Bounds(probe_lower, probe_upper),
                constraints=probe_constraints, options={'time_limit': solver_time_limit_sec, 'mip_rel_gap': 1e-6, 'presolve': False},
                rank=len(backend_layers), variables=variables, rows=rows, locks=[], input_context=diagnostic_input, row_scaling=False)
            backend_layers.append(dict(rank=len(backend_layers), layer='diagnostic_'+name,
                phase='same_state_diagnostic', status=int(result.status), backend=getattr(result, 'backend', 'injected'),
                attempts=getattr(result, 'attempts', [])))
            record = dict(status='feasible' if result.status == 0 and result.x is not None else 'infeasible' if result.status == 2 else 'unknown',
                solver_status=int(result.status), message=str(result.message), scope=scope,
                future_exogenous_input=False, actual_time_advanced=0.,
                held_dispatch_provenance=held_dispatch_provenance if arrived else None)
            if record['status'] == 'feasible':
                y = np.asarray(result.x)
                record.update(unserved_kW=y[14:26].reshape(4, 3).tolist(), action=reference_action(y, True),
                    planned_current_action=reference_action(y, False), cost_USD_step=float(operating@y))
                if active_collector() is not None:active_collector().postprocess(y.copy())
            return record
        except Exception as error:
            return dict(status='unknown', scope=scope, error_type=type(error).__name__, message=str(error),
                future_exogenous_input=False, actual_time_advanced=0.)

    if engineering['diagnose_full_supply']:
        same_state['full_supply'] = probe('full_supply')
        if all(k in context for k in ('arrived_dispatch', 'arrived_grid_import_limit_kW', 'actual_disconnected_kW')):
            same_state['execution_held_full_supply'] = probe('execution_held_full_supply', arrived=True)
        else:
            same_state['execution_held_full_supply'] = dict(status='not_run', scope='arrived_ports_and_contacts_only',
                reason='complete_arrived_source_and_contact_ledger_not_supplied')
    if engineering['diagnose_hold_combination']:
        same_state['hold_combination_full_supply'] = probe('hold_combination_full_supply', hold=True)
    changes = int(np.sum((x[go] > .5) != actual_running))
    normal_commitment_preference['planned_GT_state_changes'] = changes
    normal_startup_preference['planned_new_GT_starts'] = int(np.sum((x[go] > .5) & ~actual_running))
    diagnostic['engineering'] = dict(contract_id=ENGINEERING_CONTRACT_ID, options=engineering,
        service_order=['P0','P1','P2','P3'] if recovery else ['P0', 'P1'] if endurance_active else ['P0', 'P1', 'P2', 'P3'],
        execution_recovery=recovery,command_finalization=command_finalization,
        m18_conditional_return_permission=m18_conditional_return_permission,
        late_soft_optimization_stopped=soft_optimization_stopped,
        cost_optimization_completed=not soft_optimization_stopped,
        protection_reference_kW=plant.gt_rated_kW if execution_limits is None else execution_limits['gt_protection_reference_kW'],
        protection_target_excess_kW=None if protection_excess is None else x[protection_excess].tolist(),
        protection_excess_weights=None if protection_weights is None else protection_weights.tolist(),
        protection_observed_overload_minutes=None if protection_overload is None else protection_overload.tolist(),
        protection_original_hold_minutes=protection_hold,
        protection_weight_formula='1 + min(observed_overload_minutes / original_hold_minutes, 1)' if recovery else None,
        protection_clock_basis='strict_M18_current_observation' if recovery and execution_limits is not None else 'current_state_clock' if recovery else 'C4_legacy_no_weight',
        endurance_preparation_active=endurance_active, actual_diesel_fuel_support_hours=fuel_hours,
        fuel_pressure_basis='shaft_work_with_loss_startup_and_positive_storage',
        observed_critical_unserved_kW=observed_critical, observed_critical_material=observed_critical_material,
        observed_critical_fraction_basis='previous measured loss over lawful current demand; control signal only, not reclassification',
        actual_GT_running_mask=actual_running.tolist(),
        GT_command_state_changes=changes, actual_GT_power_change_kW=float(np.abs(gts-np.asarray(context['actual_gt_kW'])).sum()),
        cost_optimum_USD_step=cost_optimum, final_cost_USD_step=float(cost@x),
        cost_equivalence_USD_per_step=engineering['cost_equivalence_USD_per_step'],
        economic_band_is_OEM_startup_cost=False, minimum_time_safety_release=safety_release,
        grid_headroom=grid_headroom,
        normal_startup_preference=normal_startup_preference,
        normal_commitment_preference=normal_commitment_preference,
        same_state_diagnostics=same_state, connected_unserved_kW=np.maximum(0., lost-command_disconnected).tolist(),
        retained_contacts_are_delivered_energy=False, actual_protection_and_automatic_shedding_unchanged=True,
        restoration_request=not endurance_active, command_is_immediate_execution=False)
    readiness_phases = phase_sources(readiness,command_action,
        gt_start_delay_minutes=contract['gt_start_delay_minutes'],gt_ramp_kW_per_h=gt_ramp_kW_per_h,execution_limits=execution_limits)
    for phase,label in (('held_before_arrival','held_actual'),('arrived_before_new_ready','arrived_current')):
        record = readiness_phases[phase]
        if label in risk_records:
            measured = risk_records[label]
            record.update(unserved_kW=measured['unserved_kW'],
                joint_balance_residuals=measured['balance_residuals'],
                electric_surplus_defect_kW=measured['electric_surplus_defect_kW'],
                joint_witness_status=('unknown_solver_failure' if measured.get('status')=='unknown_solver_failure' else
                    'infeasible_no_real_surplus_sink' if measured['electric_surplus_defect_kW']>1e-4 else 'conditional_joint_witness'),
                grid_import_kW=measured['grid_import_kW'])
        else:record.update(unserved_kW=None,joint_witness_status='phase_constraint_disabled_ablation')
    readiness_phases['arrived_before_new_ready']['preparation_scenarios'] = {
        k:v for k,v in risk_records.items() if k.startswith('pre_')}
    readiness_phases['committed_ready'].update(unserved_kW=lost.tolist(),
        preparation_scenarios={k:v for k,v in risk_records.items() if k not in ('held_actual','arrived_current','startup_bridge') and not k.startswith('pre_')})
    diagnostic.update(schema='operating_response.current_command_readiness.v15', connection_mode=engineering['contact_mode'],
        preparation_profile=profile,operating_options=operating_config,readiness_phases=readiness_phases,
        late_preference_fallbacks=late_preference_fallbacks,
        numerical_presentation_recoveries=numerical_presentation_recoveries,
        held_deficit_is_not_current_command_controllable=True,actual_pre_ready_and_promised_supply_are_separate=True,
        retention_eligible=None, retention_selected=False, connection_gate_active=False,
        retention_means_no_new_GT_command=False, eligibility_requires_no_pending_GT_start=False)
    diagnostic['schema']='model_closeout.current_command_carrier_readiness.v19'
    diagnostic['model_closeout']=dict(critical_reserve_active=reserve_active,
        critical_reserve_rule=reserve_rule,critical_reserve_support=reserve_support,
        critical_reserve_excess_kWh=None if reserve_excess is None else float(x[reserve_excess]),
        grid_open_bridge_requested=grid_bridge_request,
        request_contact_variable_count=12,current_unmet_is_active_cut_intent=False,
        original_current_critical_service_locks_unchanged=True)
    startup_bridge=None
    if bridge_case is not None:
        measured=risk_records['startup_bridge'];eligible=(np.asarray(readiness['actual_gt_kW'])>1e-9)&np.array([availability[f'GT_{i+1}']>0. for i in range(3)])
        critical_loss=np.asarray(measured['unserved_kW'])[:2]
        balanced=bool(measured['has_physical_balance_certificate'])
        critical_ready=balanced and not np.any(critical_loss>service_solver_tol)
        startup_bridge=dict(schema='hospital_ies.C18.current_ready_port_bridge.v18.v1',
            status='current_ready_port_bridge_balanced' if critical_ready else
                'no_current_ready_port_bridge' if not eligible.any() else 'current_ready_port_bridge_deficit',
            eligible_currently_running_GT_mask=eligible.tolist(),gt_electric_kW=measured['gt_electric_kW'],
            diesel_kW=measured['diesel_kW'],grid_import_kW=measured['grid_import_kW'],
            boiler_steam_lbph=measured['boiler_steam_lbph'],absorption_cooling_kW=measured['absorption_cooling_kW'],
            electric_chiller_cooling_kW=measured['electric_chiller_cooling_kW'],unserved_kW=measured['unserved_kW'],
            electric_surplus_defect_kW=measured['electric_surplus_defect_kW'],
            has_physical_balance_certificate=balanced,instantaneous_critical_bridge_balance_credit=critical_ready,
            continuous_path_executable_credit=False,executable_preparation_credit=False,
            source_basis='retained_currently_running_GT_under_original_ordinary_ramp_GRID_DG_zero',
            covers_cold_DG_startup_and_open_return_transfer=True,
            ordinary_GT_startup_still_subject_to_original_delay=True,
            no_current_ready_port_is_not_global_unavoidability=True,
            ordinary_command_arrival_required=True,actual_supply_guaranteed=False,
            actual_trajectory_validation_required=True)
    command_support_requirement = support_hours*command_action['diesel_kW']+support['overhead_shaft_work_kWh']
    command_support_diagnostic = dict(active=command_support_active,
        reason='time_energy_and_original_endurance_state' if command_support_active else
            'time_energy_ablation' if not coordination['time_energy_preparation'] else 'original_endurance_state_inactive',
        support_minutes=coordination['support_minutes'],uses_command_diesel_ceiling=True,
        command_diesel_ceiling_kW=command_action['diesel_kW'],remaining_shaft_work_kWh=state.diesel_energy_remaining_kWh,
        overhead_shaft_work_kWh=support['overhead_shaft_work_kWh'],
        budget_requirement_kWh=command_support_requirement if command_support_active else None,
        budget_excess_kWh=float(x[command_support_excess]) if command_support_active else None,
        static_duration_budget_satisfied=(command_support_requirement<=state.diesel_energy_remaining_kWh+1e-7)
            if command_support_active else None,
        numerical_row_tolerance_kWh=1e-7,excess_is_fuel_debit=False,physical_current_fuel_limit_unchanged=True,
        original_normalized_critical_locks_unchanged=True,actual_supply_guaranteed=False,
        ordinary_command_arrival_required=True,finite_diesel_speed_path_certified=False)
    diagnostic['coordination']=dict(options=coordination,response_envelope=response,diesel_support=support,
        startup_bridge=startup_bridge,
        protection_observation=(dict(schema='hospital_ies.C18.current_gross_protection.v18.v1',
            current_precontrol_GT_transfer_kW=observed_response['precontrol_GT_transfer_kW'],
            current_cooling_proxy_kW=observed_cold_proxy,current_original_stress_ratios=observed_stress,
            current_connected_electric_kW=float(observed_connected[:,0].sum()),
            current_connected_cooling_kW=float(observed_connected[:,2].sum()),
            current_measured_auxiliary_kW=float(state.auxiliary_request_kW),
            current_GT_kW=np.asarray(state.gt_prev).tolist(),current_GRID_used_kW=float(state.last_grid_import_kW),
            current_DG_used_kW=float(state.last_diesel_kW),current_cooling_reference_kW=protection_cooling_reference,
            observed_running_GT_count=protection_count,planning_pressure_divisor=protection_divisor,
            pressure_divisor_basis='fixed current measured running GT count, max(1,n); no claimed future denominator',
            eligible_running_GT_mask=protection_running.tolist(),dispatch_margin_kW=PROTECTION_DISPATCH_MARGIN_KW,
            original_relay_reference_kW=reference,original_relay_multiple=multiple,
            planning_gross_pressure_variable_kW=float(x[protection_gross]),
            planning_connected_cooling_growth_variable_kW=float(x[protection_cold_growth]),
            planning_actual_GT_kW=x[gp].tolist(),planning_GRID_used_kW=float(x[ig]),planning_DG_used_kW=float(x[idg]),
            planning_real_conversion_auxiliary_kW=float(x[ia]*plant.abs_electric_kW_per_rt+x[ie].sum()*plant.ec_electric_kW_per_rt),
            pending_or_off_GT_cannot_reduce_current_gross_pressure=True,
            pending_target_has_no_current_allocated_pressure=True,
            cold_proxy_is_added_to_actual_energy_load=False,
            actual_clocks_mutated=False,observation_method='original pure proxy on copied current state, dt0, apply_tripsFalse',
            real_replacement_enforced_by_original_energy_balances=True,
            conditional_ceilings_are_not_current_support=True) if recovery else None),
        conditional_DG_endpoint_pressure=dict(active=bool(recovery and endurance_active and preparation_copies),
            sum_conditional_R_DG_kW=sum(float(x[case['phase_diesel']]) for case in preparation_copies)
                if recovery and endurance_active else None,
            endpoint_count=len(preparation_copies) if recovery and endurance_active else 0,
            semantics='unweighted static R-endpoint preference; no probability, simultaneous dispatch or actual shaft-work claim',
            actual_fuel_debit=False,pending_GT_is_not_current_supply=True,
            original_T_shaft_constraint_unchanged=True,
            useful_pending_retention_is_preference_not_mandatory_hold=True),
        actual_command_support=command_support_diagnostic,
        shared_response_GT_target_kW=None if shared_response_gt is None else x[shared_response_gt].tolist(),
        shared_response_GT_ready_mask=None if shared_response_on is None else (x[shared_response_on]>.5).tolist(),
        response_GT_state_shared_across_all_contingencies=True,
        C_command_domain_restrictions=response_domain,
        C_command_domain_is_common_physical_capacity=False,
        response_constraints_active=coordination['time_energy_preparation'],
        time_energy_constraints_active=coordination['time_energy_preparation'],
        cooling_constraints_active=coordination['cooling_preparation'],
        converter_commands_are_independent_ordinary_ceilings=True,
        healthy_converter_permission_preference_active=coordination['cooling_preparation'],
        critical_cold_contact_semantics='independent active-cut intention preferred minimal after critical service; actual unmet remains physical ENS',
        critical_cold_contact_policy_applies_to_all_C_ablations=True,
        critical_electric_heat_and_low_priority_contacts_unchanged=False,
        all_twelve_request_contacts_independent_from_current_unmet=True,
        existing_pending_command_targets_separate_from_current_actual_ports=True,
        pending_command_targets_kW={f'GT_{i+1}':float(x[target]) for i,(target,keep) in pending_targets.items()},
        planned_current_GT_ports_kW=gts.tolist(),conditional_command_GT_targets_kW=x[command_gp].tolist(),
        converter_command_ceilings_kW=(x[converter_ceilings]*RT_TO_KW).tolist(),
        diesel_command_ceiling_kW=float(x[diesel_ceiling]),boiler_command_is_actual_fixed_steam=True,
        critical_heat_preparation_multiplier=preparation_profile('K1')['critical_multiplier'] if recovery else 1.,
        heat_reserve_uses_current_observed_load_only=True,heat_reserve_is_real_paid_steam=True,
        zero_surplus_certificates_required_for_effective_preparation_credit=recovery,
        low_history_is_uncredited_diagnostic=recovery,
        response_scenarios={k:v for k,v in risk_records.items() if k.startswith('response_')},
        eventual_scenarios={k:v for k,v in risk_records.items() if k not in ('held_actual','arrived_current','startup_bridge') and not k.startswith(('pre_','response_'))},
        structural_shared_tower_deficit=bool(tower_limit<=1e-9 and loads[:2,2].sum()>1e-9),
        finite_diesel_speed_path_certified=False,known_unresolved_joint_path='Conditional static endpoints; grid_open_target has no completion bound. M19 checks only its registered aggregate execution domain, not manufacturer mechanical dynamics.')
    readiness_phases['committed_ready']['preparation_scenarios']=diagnostic['coordination']['eventual_scenarios']
    readiness_phases['response_within_registered_bound']=dict(**response,
        preparation_scenarios=diagnostic['coordination']['response_scenarios'])
    return dict(action=command_action, planned_current_action=current_action, lost=lost, served=served,
        command_disconnected=command_disconnected, planned_unserved=lost.copy(),
        lex_values=lex_values, cost_USD_step=float(operating@x), tracking_penalty_USD_step=tracking_penalty,
        optimization_objective_USD_step=float(cost@x), security_objective_penalty_USD_step=security_penalty,
        energy_balance_residuals=residuals, steam_balance_residual_lbph=float(residual[1]),
        optimizer_warning=('Allowlisted late preference failed; strictly validated prior command retained; failed preferences not optimized'
            if late_preference_fallbacks else ''), solver_backend_layers=backend_layers, joint_diagnostic=diagnostic)
