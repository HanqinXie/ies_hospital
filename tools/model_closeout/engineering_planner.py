# M19 sparse fork: independent twelve request contacts; M18 physical rows retained.
"""M18 interface-compatible fork of the unchanged E preference hierarchy.

Preserves original energy equalities/capacities/P0-P1 locks; versions service
ordering, commanded contacts, actual-combination tie breaking, and diagnostics.
This creates ordinary queued commands, never immediate source capability.
"""
from __future__ import annotations

import copy
import math
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
from hospital_ies.solver_diagnostics import active_collector

from .numerics import (finalize_command_vector, validate_execution_limits,
    finalize_conditional_grid_permission, solve_layer_with_lock_presentation, effective_held_dispatch)

NUMERICAL_CONTRACT_ID = numerics.NUMERICAL_CONTRACT_ID
STANDBY_CONTRACT_ID = frozen.STANDBY_CONTRACT_ID
SERVICE_SWITCH_CONTRACT_ID = 'current_service_budget_handoff.v1'
ENGINEERING_CONTRACT_ID = 'engineering.current_service_continuity.v14'
NEW_DEFAULT_OPTIONS = dict(service_budget_enabled=True, service_budget_fractions=[.05, .20],
    handoff_enabled=True, coordination_preference='standby_first', features_enabled=True)
PREFERENCES = ('standby_first', 'bridge_first', 'service_first', 'bridge_service_first')
solve_layer = solve_layer_with_lock_presentation
ENGINEERING_DEFAULTS = dict(mode='service_restore', contact_mode='planned_shed',
    continuity=True, cost_equivalence_USD_per_step=1e-5,
    diagnose_full_supply=False, diagnose_hold_combination=False, grid_headroom_preference=False,
    execution_aware_start_preference=False, execution_aware_commitment_preference=False,
    p0_preparation_before_commitment=False)
# Existing regulator command-generation deadband, not a new physical tolerance.
LOCAL_DOWN_REQUEST_DEADBAND_KW = 1e-4
LOCAL_DOWN_REQUEST_DEADBAND_SOURCE = 'hospital_ies.state_transition.TransitionCore._request_local_down'
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
    supplied = copy.deepcopy(value or {})
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
                engineering_options=None, execution_context=None, execution_limits=None):
    """Return one queued-command plan plus separately balanced current use.

    ``action.grid_import_kW`` is the generated importer ceiling only when the
    EXISTING contract permits local import up to an arrived ceiling. Actual
    current used import and cost are in ``planned_current_action``. Economic
    compatibility binds these two quantities equal. All static copies keep
    GT/diesel/boiler sources fixed to this command (except the named outage).
    Risk surplus is never returned as a real action or included in main balance.
    """
    limits = None if execution_limits is None else validate_execution_limits(execution_limits)
    engineering = globals()['engineering_options'](engineering_options)
    context = _context(execution_context, state, loads)
    held_dispatch,held_dispatch_provenance=effective_held_dispatch(context.get('arrived_dispatch',{}),
        execution_limits=limits,availability=availability,plant=plant,grid_import_limit_kW=grid_import_limit_kW)

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
    diesel_ceiling = allocate('command.diesel_conditional_ceiling', 'kW') if limits is not None else idg
    if 'grid_import' in targets:
        # This target has always described the generated importer command.
        # Current imported energy is a different variable under arrived_ceiling.
        targets['grid_import'] = (ceiling, targets['grid_import'][1])
    fuel = allocate('preparation.fuel_excess', 'kW') if (variant in ('endurance', 'coupled')
        or engineering['mode'] == 'endurance_restore') else None
    regulator_excess = np.array([allocate(f'preparation.GT_{i+1}.above_stable_floor', 'kW')
                                 for i in range(3)]) if regulator_active else None
    retention = allocate('command.retain_low_contacts', 'binary')
    contacts = np.arange(14, 26).reshape(4, 3).copy()
    for group in range(4):
        for carrier in range(3):
            contacts[group, carrier] = allocate(f'command.{GROUPS[group]}.{CARRIERS[carrier]}.disconnected', 'kW')
    copies = []; cases = []
    if variant in ('diverse', 'coupled'):
        pressure_loads = loads.copy()
        pressure_loads[:2] *= options['critical_reserve_multiplier']
        cases = [('GRID_loss', pressure_loads), ('GT_common_loss', pressure_loads)]
        if variant == 'coupled':
            cases.append(('low_history', loads if risk_loads is None else risk_loads))
    if options['handoff_enabled']:
        # A registered permission-loss condition on THIS command after arrival,
        # not a prediction of when GRID recovers and not a pre-arrival source.
        cases.append(('DG_withdrawal_GRID_ceiling', loads))
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
        copies.append(dict(name=label, loads=np.asarray(demand).copy(), converters=np.arange(start, start+4),
                           grid=rg, steam=rw, unmet=ru, surplus=rs,
                           gt_mask=np.zeros(3) if label == 'GT_common_loss' else np.ones(3),
                           diesel_mask=0. if label == 'DG_withdrawal_GRID_ceiling' else 1.))

    standby_excess = allocate('preparation.third_GT_on_excess', 'GT count')
    budget_violation = np.array([[allocate(f'service_budget.{GROUPS[p]}.{k}.violation', 'kWh')
        for k in CARRIERS] for p in (2, 3)])
    power_changes = np.array([allocate(f'continuity.GT_{i+1}.absolute_change', 'kW') for i in range(3)])
    state_changes = np.array([allocate(f'continuity.GT_{i+1}.state_change', 'GT changes') for i in range(3)])
    n = len(names)
    vector = lambda: np.zeros(n)
    lower = np.zeros(n); upper = np.full(n, np.inf)
    integrality = np.zeros(n, dtype=int); integrality[go] = 1
    integrality[retention] = 1
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
            change = gt_ramp_kW_per_h*dt_h
            base = min(float(state.gt_prev[i]), rated*av)
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
    upper[ib] = plant.boiler_steam_lbph/3*sum(availability[f'BOILER_{i+1}'] for i in range(3))
    upper[ig] = grid_import_limit_kW*availability['GRID']
    upper[idg] = min(plant.emergency_diesel_kW*availability['DIESEL'], state.diesel_energy_remaining_kWh/dt_h) if diesel_start_available else 0.
    if contract['diesel_mode'] == 'emergency_bus' and availability['GRID'] > 0:upper[idg] = 0.
    if limits is not None:
        # New commands may replace old permissions. Current topology and true
        # connection/start readiness gate main delivery; old permission only
        # gates the pre-arrival/held copies below.
        upper[idg] = (min(plant.emergency_diesel_kW*availability['DIESEL'],
                         state.diesel_energy_remaining_kWh/dt_h) if diesel_start_available
                         and limits['grid_bus_state'] in ('ISLAND', 'RETURN_PREP') else 0.)
        upper[diesel_ceiling] = (plant.emergency_diesel_kW*availability['DIESEL']
            if limits['diesel_conditionally_permitted'] and state.diesel_energy_remaining_kWh>0. else 0.)
        add({idg:1., diesel_ceiling:-1.}, -np.inf, 0., 'command.actual_diesel_below_conditional_permission')
    for i in range(4):
        for k in range(3):upper[unmet(i, k)] = loads[i, k]
    if separate_ceiling:
        upper[ceiling] = grid_import_limit_kW if limits is not None else upper[ig]
        add({ig: 1., ceiling: -1.}, -np.inf, 0., 'command.import_used_below_ceiling', source='existing arrived_ceiling interface')
    if fuel is not None:
        add({diesel_ceiling: 1., fuel: -1.}, -np.inf, state.diesel_energy_remaining_kWh/options['support_hours'],
            'preparation.endurance_preference', source='registered preparation preference, not physical fuel horizon')

    for case in copies:
        label = case['name']; demand = case['loads']; conv = case['converters']
        rg = case['grid']; rw = case['steam']; ru = case['unmet']; rs = case['surplus']
        mask = case['gt_mask']
        case_diesel = (diesel_ceiling if limits is not None and
            (label == 'GRID_loss' or upper[ig] == 0.) else idg)
        case['diesel_source'] = case_diesel
        re = vector(); re[gp] = mask; re[case_diesel] = case['diesel_mask']; re[rg] = 1.; re[rs] = -1.
        re[conv] = -np.array([plant.abs_electric_kW_per_rt]+[plant.ec_electric_kW_per_rt]*3)
        rh = vector(); rh[gp] = steam_per_kW*hrsg*mask; rh[ib] = 1.; rh[conv[0]] = -plant.abs_steam_lb_per_rt; rh[rw] = -1.
        case['regulator_floor'] = regulator_active and label == 'GRID_loss'
        if case['regulator_floor']:
            # Existing local import regulation can unload each commanded-on
            # port to its original minimum stable output. Apply the SAME GO
            # electric/HRSG dependence in this static pressure assumption.
            re[gp] = 0.; re[go] = stable_floor
            rh[gp] = 0.; rh[go] = stable_floor*steam_per_kW*hrsg
        rc = vector(); rc[conv] = RT_TO_KW
        for i in range(4):
            re[ru+3*i] = 1.; rh[ru+3*i+1] = 1./heat_factor; rc[ru+3*i+2] = 1.
            for k in range(3):
                upper[ru+3*i+k] = demand[i, k]
                ratio = demand[i, k]/loads[i, k] if loads[i, k] > 0 else 0.
                add({ru+3*i+k: 1., int(contacts[i, k]): -ratio}, 0., np.inf,
                    f'risk.{label}.{GROUPS[i]}.{CARRIERS[k]}.same_disconnected_contacts',
                    source='same generated physical contact cuts, not numerical unserved service')
        case['balance'] = np.array([re, rh, rc])
        case['rhs'] = np.array([demand[:, 0].sum(), demand[:, 1].sum()/heat_factor, demand[:, 2].sum()])
        for r, rhs, carrier, unit in zip(case['balance'], case['rhs'], ('electric', 'steam', 'cooling'), ('kW', 'lb/h', 'kW')):
            add(r, rhs, rhs, f'risk.{label}.balance.{carrier}', unit,
                source='static single-command diagnostic; surplus defect is NOT real disposal')
        for index, master in zip(conv, np.r_[ia, ie]):
            upper[index] = upper[master]
            add({index: 1., int(master): -1.}, -np.inf, 0., f'risk.{label}.{names[index]}.held_ceiling', 'RT',
                source='original arrived converter ceiling, no new command')
        upper[rg] = (grid_import_limit_kW if limits is not None and label == 'DG_withdrawal_GRID_ceiling' else
                     0. if label == 'GRID_loss' else upper[ig])
        if label != 'GRID_loss':
            add({rg: 1., ceiling: -1.}, 0. if contract['grid_import_mode'] == 'fixed' else -np.inf, 0.,
                f'risk.{label}.shared_grid_command', source='original fixed or arrived_ceiling importer contract')
        add({int(index): float(value) for index, value in zip(conv, tower_co)}, -np.inf, tower_limit,
            f'risk.{label}.tower.rejection')
        if contract['diesel_mode'] == 'emergency_bus':
            coefficients = {case_diesel: case['diesel_mask'], rs: -1.}
            coefficients.update({ru+3*i: 1. for i in allowed})
            add(coefficients, -np.inf, float(demand[allowed, 0].sum()), f'risk.{label}.diesel.allowed_bus',
                source='same allowed bus; same diagnostic surplus defect marks unusable power')

    # z is nonnegative with NO finite upper bound. This epigraph always
    # extends an old feasible plan; it does not hard-cap online GT count.
    add({int(i): 1. for i in go} | {standby_excess: -1.}, -np.inf, 2.,
        'preparation.third_GT_on_exposure', 'GT count', kind='preference',
        source='soft current-command preference; no added physical standby capability')

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
    actual_running = np.asarray(context['gt_running'], dtype=bool)
    actual_diesel = float(state.last_diesel_kW)
    fuel_hours = state.diesel_energy_remaining_kWh/actual_diesel if actual_diesel > 1e-9 else None
    observed_loss = np.asarray(context.get('observed_critical_unserved_kW', np.zeros((2, 3))))
    observed_critical = float(observed_loss.sum())
    cfg = config or {}
    service_solver_tol = float(cfg.get('p0_solver_feasibility_tol_kW', .001))
    service_abs_tol = float(cfg.get('p0_unserved_abs_tol_kW', .01))
    service_rel_tol = float(cfg.get('p0_unserved_rel_tol', .001))
    observed_fraction = np.divide(observed_loss, loads[:2], out=np.zeros((2, 3)), where=loads[:2] > service_solver_tol)
    observed_critical_material = bool(np.any((observed_loss > service_solver_tol)
        & ((observed_loss >= service_abs_tol) | (observed_fraction >= service_rel_tol))))
    endurance_active = (engineering['mode'] == 'endurance_restore' and availability['GRID'] < 1.-1e-9
        and ((fuel_hours is not None and fuel_hours < options['support_hours']) or observed_critical_material))
    lex_values = [None]*4; x = None; final_objectives = []
    numerical_presentation_recoveries=[]

    def run_layer(item, *, reference=False, lock=True, tolerance_override=None):
        nonlocal x
        name, co, unit, group = item
        rank = len(backend_layers)
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
        if result.x is None or result.status != 0:
            error = ModelConstraintInfeasible if result.status == 2 else SolverExecutionError
            failure = error(f'Coordinated current MILP failure: layer={name}, rank={rank}, status={result.status}, message={result.message}')
            failure.solver_backend_attempts = backend_layers
            failure.joint_diagnostic = dict(options=options, completed_layers=layers,
                completed_reference_layers=reference_layers, failing_layer=name,
                engineering_options=engineering, execution_context=context, minimum_time_safety_release=safety_release)
            raise failure
        x = np.asarray(result.x, dtype=float)
        value = float(co@x)
        layer = dict(rank=rank, name=name, objective_value=value, unit=unit)
        if refinement is not None and refinement.get('accepted'):
            layer.update(optimization_status='gap_certified_feasible_numerical_refinement',exact_optimum_claimed=False,
                priority_lock_refinement=copy.deepcopy(refinement))
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

    # Original normalized P0/P1 locks precede every new preference/guard.
    run_layer(priority_objective(0)); run_layer(priority_objective(1))
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
    preparation = []
    if copies:
        for group in (0, 1):
            co = vector()
            for case in copies:
                for k in range(3):co[case['unmet']+group*3+k] = 1./max(1., float(case['loads'][:, k].sum()))
            preparation.append((f'preparation_{GROUPS[group]}', co, 'sum of normalized static carrier ENS; not probability', None))
    if copies or fuel is not None:
        co = vector()
        if fuel is not None:co[fuel] = 1.
        for case in copies:co[case['surplus']] = 1.
        preparation.append(('preparation_defect', co, 'kW: fuel preference excess plus static unabsorbable surplus', None))
    if regulator_active:
        co = vector(); co[regulator_excess] = 1.
        preparation.append(('regulator_GT_excess', co, 'kW above existing minimum stable GT outputs', None))
    # In a currently healthy, undisturbed state, avoid acquiring a new source
    # merely to improve a hypothetical outage copy. Current P0--P3 optima
    # still precede this preference, so necessary starts remain feasible.
    # OFF->ON alone is counted: ordinary legal stops are not discouraged.
    current_sources_healthy = all(float(v) >= 1.-1e-9 for v in availability.values())
    actual_fuel_pressure = fuel_hours is not None and fuel_hours < options['support_hours']
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
    # R6 protects P0 outage preparation from an initially sparse combination.
    # P1 preparation deliberately remains later: its nonzero normal GRID-loss
    # electric pressure can otherwise demand a new third GT before it is ready.
    # These are current static copies, never a future outage/startup schedule.
    p0_preparation = []
    remaining_preparation = preparation
    if commitment_active and engineering['p0_preparation_before_commitment']:
        p0_preparation = [item for item in preparation if item[0] == 'preparation_P0']
        remaining_preparation = [item for item in preparation if item[0] != 'preparation_P0']
    normal_commitment_preference.update(
        p0_preparation_before_commitment_requested=engineering['p0_preparation_before_commitment'],
        p0_preparation_before_commitment_active=bool(p0_preparation),
        p0_preparation_layers_before_commitment=[item[0] for item in p0_preparation],
        P1_preparation_precedes_commitment=False)
    service = [priority_objective(2), priority_objective(3)]
    ordered = (preparation+service+standby if endurance_active else
        service+p0_preparation+commitment_preference+start_preference+standby+remaining_preparation)
    # A normalized P2/P3 lock allowance would become a real contact cut and
    # stack with the unchanged fixed-execution remainder. Preserve the new
    # current service optima exactly; original P0/P1/reference locks stay intact.
    for item in ordered:
        value = run_layer(item, tolerance_override=0. if item[3] in (2, 3)
            or item[0] in ('normal_current_new_GT_starts', 'normal_current_GT_state_changes') else None)
        if item[0] == 'normal_current_new_GT_starts':normal_startup_preference['minimum_new_GT_starts'] = value
        if item[0] == 'normal_current_GT_state_changes':normal_commitment_preference['minimum_GT_state_changes'] = value
    run_layer(('operating_cost_and_original_preferences', cost, 'USD per current step', None),
        lock=engineering['continuity'] or engineering['contact_mode'] == 'separate',
        tolerance_override=engineering['cost_equivalence_USD_per_step'])
    cost_optimum = float(cost@x)
    if engineering['continuity']:
        co = vector(); co[state_changes] = 1.
        run_layer(('actual_GT_combination_changes', co, 'GT changes', None))
    if engineering['contact_mode'] == 'separate':
        co = vector(); co[contacts[2:].ravel()] = 1.
        run_layer(('ordinary_low_priority_disconnected_power', co, 'sum of carrier kW', None))
    if engineering['continuity']:
        co = vector(); co[power_changes] = 1.
        run_layer(('actual_GT_power_change', co, 'kW', None), lock=False)
    x, grid_headroom = _guarded_grid_headroom(x, requested=engineering['grid_headroom_preference'],
        separate_ceiling=separate_ceiling, ceiling=ceiling, gp=gp, go=go, stable_floor=stable_floor,
        context=context, lower=lower, upper=upper, constraints=constraints)
    for layer, item in zip(layers, final_objectives):layer['final_objective_value'] = float(item[1]@x)

    def build_emitted_command(y):
        powers = y[gp]
        return dict(gt_electric_kW=powers.tolist(), grid_import_kW=float(y[ceiling]),
            diesel_kW=float(y[diesel_ceiling]), boiler_steam_lbph=float(y[ib]),
            absorption_cooling_kW=float(y[ia]*RT_TO_KW),
            electric_chiller_cooling_kW=(y[ie]*RT_TO_KW).tolist(),
            hrsg_steam_lbph=float(powers@hrsg*steam_per_kW), tower_rejection_kW=float(tower@y),
            gt_fuel_MMBtu_h=float(powers.sum()*fuel_per_kW))
    emitted = {int(i):1. for i in np.r_[gp, ceiling, diesel_ceiling, ib, contacts.ravel()]}
    emitted.update({int(i):max(RT_TO_KW, float(tower_co[j])) for j,i in enumerate(np.r_[ia, ie])})
    emitted.update({int(i):max(1., float(hrsg[j]*steam_per_kW), fuel_per_kW) for j,i in enumerate(gp)})
    x, m18_conditional_return_permission = finalize_conditional_grid_permission(x,
        constraints=constraints, lower=lower, upper=upper, integrality=integrality,
        rows=metadata, variables=variables, ceiling=int(ceiling), execution_limits=limits,
        objectives=[(item[0], item[1]) for item in final_objectives])
    x, finalized_command, numerical_finalization = finalize_command_vector(x,
        constraints=constraints, lower=lower, upper=upper, integrality=integrality,
        rows=metadata, variables=variables, emitted_coordinates=emitted,
        build_command=build_emitted_command, objectives=[(item[0], item[1]) for item in final_objectives])
    for layer,item in zip(layers,final_objectives):layer['final_objective_value']=float(item[1]@x)
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
    command_action = finalized_command
    risk_records = {}
    for case in copies:
        side = case['balance']@x-case['rhs']; conv = x[case['converters']]
        case_gt = stable_floor*x[go] if case['regulator_floor'] else gts*case['gt_mask']
        risk_records[case['name']] = dict(single_shared_command=True, loads_kW=case['loads'].tolist(),
            gt_electric_kW=case_gt.tolist(), hrsg_steam_lbph=float(case_gt@hrsg*steam_per_kW),
            GT_source_basis=('current_command_on_at_existing_stable_floor' if case['regulator_floor']
                             else 'shared_current_planned_GT_with_named_loss'),
            diesel_kW=float(x[case['diesel_source']]*case['diesel_mask']), boiler_steam_lbph=float(x[ib]), grid_import_kW=float(x[case['grid']]),
            absorption_cooling_kW=float(conv[0]*RT_TO_KW), electric_chiller_cooling_kW=(conv[1:]*RT_TO_KW).tolist(),
            unserved_kW=x[case['unmet']:case['unmet']+12].reshape(4, 3).tolist(),
            electric_surplus_defect_kW=float(x[case['surplus']]),
            balance_residuals=[float(side[0]), float(side[2]), float(side[1]*heat_factor)],
            steam_balance_residual_lbph=float(side[1]), diagnostic_not_actual_execution=True)
    # Legacy static copies retain their original optimization role. A
    # disposal defect cannot be credited as executable backup, and even a
    # zero-defect static point lacks a joint time/energy execution certificate.
    for witness in risk_records.values():
        surplus_positive=witness['electric_surplus_defect_kW']>0.
        witness.update(preparation_credit_schema='hospital_ies.static_preparation_credit.v18.v1',
            has_physical_balance_certificate=False,executable_preparation_credit=False,
            preparation_status='uncredited_static_witness' if surplus_positive else 'uncertified_static_witness',
            preparation_credit_reason='positive_electric_surplus_without_real_sink' if surplus_positive else
                'no_joint_time_energy_execution_certificate',zero_surplus_alone_certifies_backup=False)
    security_penalty = security_weight*dt_h*float(gts.sum())
    tracking_penalty = sum(float(cost[index]*x[index]) for index in target_indices.values())
    diagnostic = dict(schema='coordinated_dispatch.current_service_handoff.v1', options=options, layers=layers,
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
    diagnostic['numerical_finalization'] = numerical_finalization
    diagnostic['m18_conditional_return_permission'] = m18_conditional_return_permission
    diagnostic['m18_interface_compatibility'] = dict(
        enabled=limits is not None, execution_limits=limits,
        actual_grid_limit_kW=float(upper[ig]), actual_diesel_limit_kW=float(upper[idg]),
        conditional_grid_permission_upper_kW=float(upper[ceiling]),
        conditional_diesel_permission_upper_kW=float(upper[diesel_ceiling]),
        conditional_diesel_command_kW=float(x[diesel_ceiling]),
        physical_actual_diesel_kW=float(x[idg]),
        original_preference_order_and_coefficients_preserved=True,
        original_combined_diesel_command_actual_semantics_split=True,
        added_DG_maximization_preference=False, conditional_permission_is_actual_supply=False)
    diagnostic.update(preserve_one_gt_standby=options['preserve_one_gt_standby'],
        standby_soft_preference_active=options['preserve_one_gt_standby'],
        standby_third_gt_on_excess=float(x[standby_excess]),
        standby_planned_GT_on=x[go].tolist(), standby_planned_GT_on_count=float(x[go].sum()),
        standby_layer_name='standby_third_GT_exposure', standby_contract_id=STANDBY_CONTRACT_ID,
        standby_is_execution_guarantee=False,
        standby_semantics='Soft third commanded-on GT exposure after original P0/P1 locks; not arrived reserve or future protection',
        numerical_contract_id=NUMERICAL_CONTRACT_ID, solver_row_scaling=False,
        canonical_problem_changed=True, original_physical_rows_and_current_priority_locks_unchanged=True,
        original_backend_validation_thresholds_unchanged=True)
    diagnostic.update(regulator_consistent=options['regulator_consistent'], regulator_pressure_active=regulator_active,
        regulator_excess_kW=0. if regulator_excess is None else float(x[regulator_excess].sum()),
        regulator_excess_by_GT_kW=[0., 0., 0.] if regulator_excess is None else x[regulator_excess].tolist(),
        command_GT_on=x[go].tolist(), regulator_floor_GT_kW=(stable_floor*x[go]).tolist(),
        regulator_semantics='Existing stable-floor pressure assumption; not arrived output or a temporal prediction',
        regulator_is_execution_guarantee=False)
    command_disconnected = x[contacts].copy()
    retention_selected = bool(x[retention] > .5)
    diagnostic.update(connection_mode=options['connection_mode'], retention_eligible=retention_eligible,
        retention_selected=retention_selected, connection_gate_active=retention_selected,
        command_disconnected_kW=command_disconnected.tolist(), planned_unserved_kW=lost.tolist(),
        automatic_shedding_not_overridden=True, eligibility_requires_no_pending_GT_start=True,
        contact_variable_count=12, retention_binary_count=1,
        retention_means_no_new_GT_command=True)
    def reference_action(y, command):
        powers = y[gp]
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
            probe_upper[ig] = min(probe_upper[ig], dispatch.get('grid_import_kW',context['arrived_grid_import_limit_kW']))
            probe_upper[ceiling] = min(probe_upper[ceiling], dispatch.get('grid_import_kW',context['arrived_grid_import_limit_kW']))
            if limits is not None:probe_upper[idg]=min(probe_upper[idg],dispatch.get('diesel_kW',0.))
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
        m18_conditional_return_permission=m18_conditional_return_permission,
        service_order=['P0', 'P1'] if endurance_active else ['P0', 'P1', 'P2', 'P3'],
        endurance_preparation_active=endurance_active, actual_diesel_fuel_support_hours=fuel_hours,
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
    diagnostic.update(numerical_presentation_recoveries=numerical_presentation_recoveries,
        schema='engineering_model.current_service_continuity.v14', connection_mode=engineering['contact_mode'],
        retention_eligible=None, retention_selected=False, connection_gate_active=False,
        retention_means_no_new_GT_command=False, eligibility_requires_no_pending_GT_start=False)
    return dict(action=command_action, planned_current_action=current_action, lost=lost, served=served,
        command_disconnected=command_disconnected, planned_unserved=lost.copy(),
        lex_values=lex_values, cost_USD_step=float(operating@x), tracking_penalty_USD_step=tracking_penalty,
        optimization_objective_USD_step=float(cost@x), security_objective_penalty_USD_step=security_penalty,
        energy_balance_residuals=residuals, steam_balance_residual_lbph=float(residual[1]),
        optimizer_warning='', solver_backend_layers=backend_layers, joint_diagnostic=diagnostic)
