"""Static critical-demand envelope fork of the frozen connection planner.

Numerical unserved service and physical commanded disconnection are separate.
A current-ledger eligibility flag allows ONE binary retention decision. Retaining
P2/P3 contacts also forces all newly commanded GT powers to zero; no new source,
reconnection speed, automatic shedding rule or execution permission is added.
Only two static pressure copies enlarge current critical carrier loads by a
registered constant. No future loads are read and the main problem is unchanged.
Multiplier one delegates the frozen connection planner's numerical route.
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
from .planner_connections import solve_joint as _connections_solve_joint
from hospital_ies.solver_diagnostics import active_collector, solve_layer


DEFAULT_OPTIONS = {'variant': 'economic', 'support_hours': 2., 'risk_tolerance': 1e-7,
                   'connection_mode': 'planned_shed', 'regulator_consistent': False,
                   'critical_reserve_multiplier': 1.}
VARIANTS = frozenset(('economic', 'endurance', 'diverse', 'coupled'))


def _options(value):
    if value is not None and (not isinstance(value, dict) or set(value)-set(DEFAULT_OPTIONS)):
        raise ValueError('Unsupported joint planning options; future scripts/horizons are forbidden')
    result = {**DEFAULT_OPTIONS, **copy.deepcopy(value or {})}
    if not isinstance(result['variant'], str) or result['variant'] not in VARIANTS:
        raise ValueError('Unknown joint planning variant')
    if result['connection_mode'] not in ('planned_shed', 'retain_when_no_gt'):
        raise ValueError('Unknown connection mode')
    if type(result['regulator_consistent']) is not bool:
        raise ValueError('regulator_consistent must be a boolean')
    multiplier = result['critical_reserve_multiplier']
    if type(multiplier) is not float or not math.isfinite(multiplier) or not 1. <= multiplier <= 2.:
        raise ValueError('critical_reserve_multiplier must be a finite float in [1,2]')
    if multiplier > 1. and result['variant'] not in ('diverse', 'coupled'):
        raise ValueError('Critical demand envelope requires static diverse/coupled copies')
    for key, low, high in (('support_hours', .25, 8.), ('risk_tolerance', 0., 1e-5)):
        x = result[key]
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not low <= x <= high:
            raise ValueError('Invalid joint planning '+key)
        result[key] = float(x)
    return result


def solve_joint(row, plant, loads, availability, state, dt_h,
                grid_import_limit_kW, diesel_start_available, gt_ramp_kW_per_h,
                solver_time_limit_sec=6., config=None, tracking_targets=None,
                security_gt_penalty_USD_per_kWh=0., execution_contract=None, *,
                joint_options=None, risk_loads=None, retention_eligible=False, connection_mode=None):
    """Return one queued-command plan plus separately balanced current use.

    ``action.grid_import_kW`` is the generated importer ceiling only when the
    EXISTING contract permits local import up to an arrived ceiling. Actual
    current used import and cost are in ``planned_current_action``. Economic
    compatibility binds these two quantities equal. All static copies keep
    GT/diesel/boiler sources fixed to this command (except the named outage).
    Risk surplus is never returned as a real action or included in main balance.
    """
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
    if retention_eligible and np.any(np.abs(np.asarray(state.gt_prev, dtype=float)) > 1e-9):
        raise ValueError('Retention eligibility conflicts with currently running GT ports')
    if options['critical_reserve_multiplier'] == 1.:
        baseline = _connections_solve_joint(row, plant, loads, availability, state, dt_h,
            grid_import_limit_kW, diesel_start_available, gt_ramp_kW_per_h,
            solver_time_limit_sec, config, tracking_targets, security_gt_penalty_USD_per_kWh,
            execution_contract, joint_options={k:v for k,v in options.items() if k != 'critical_reserve_multiplier'},
            risk_loads=risk_loads, retention_eligible=retention_eligible)
        baseline['joint_diagnostic'].update(options=options, critical_reserve_multiplier=1.,
            critical_envelope_active=False, critical_envelope_is_prediction=False,
            critical_envelope_semantics='Registered static current-demand envelope; main loads and low history unchanged')
        return baseline
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
    if 'grid_import' in targets:
        # This target has always described the generated importer command.
        # Current imported energy is a different variable under arrived_ceiling.
        targets['grid_import'] = (ceiling, targets['grid_import'][1])
    fuel = allocate('preparation.fuel_excess', 'kW') if variant in ('endurance', 'coupled') else None
    regulator_excess = np.array([allocate(f'preparation.GT_{i+1}.above_stable_floor', 'kW')
                                 for i in range(3)]) if regulator_active else None
    retention = allocate('command.retain_low_contacts', 'binary')
    contacts = np.arange(14, 26).reshape(4, 3).copy()
    for group in (2, 3):
        for carrier in range(3):
            contacts[group, carrier] = allocate(f'command.{GROUPS[group]}.{CARRIERS[carrier]}.disconnected', 'kW')
    copies = []
    if variant in ('diverse', 'coupled'):
        pressure_loads = loads.copy()
        pressure_loads[:2] *= options['critical_reserve_multiplier']
        cases = [('GRID_loss', pressure_loads), ('GT_common_loss', pressure_loads)]
        if variant == 'coupled':
            cases.append(('low_history', loads if risk_loads is None else risk_loads))
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
                               gt_mask=np.zeros(3) if label == 'GT_common_loss' else np.ones(3)))

    n = len(names)
    vector = lambda: np.zeros(n)
    lower = np.zeros(n); upper = np.full(n, np.inf)
    integrality = np.zeros(n, dtype=int); integrality[go] = 1
    integrality[retention] = 1
    upper[retention] = float(options['connection_mode'] == 'retain_when_no_gt' and retention_eligible)
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
    if regulator_active:
        # This equality only names slack already implied nonnegative by the
        # original minimum-stable constraint. It never hard-limits main GP.
        for i, excess in enumerate(regulator_excess):
            add({int(excess): 1., int(gp[i]): -1., int(go[i]): float(stable_floor[i])}, 0., 0.,
                f'preparation.GT_{i+1}.above_stable_floor',
                source='soft preference under existing local-down contract; not an execution prediction')
    # r=1 is allowed only by the current ledger and means this command starts
    # no GT. Low service can remain unserved while its physical contacts stay on.
    total_gt_capacity = rated*sum(availability[f'GT_{i+1}'] for i in range(3))
    coefficients = {int(i): 1. for i in gp}; coefficients[retention] = total_gt_capacity
    add(coefficients, -np.inf, total_gt_capacity, 'command.retained_contacts_require_zero_GT',
        source='connection algorithm restriction, no additional physical capability')
    for group in (2, 3):
        for carrier in range(3):
            cut = int(contacts[group, carrier]); unserved = unmet(group, carrier); demand = loads[group, carrier]
            upper[cut] = demand
            add({cut: 1., unserved: -1.}, -np.inf, 0.,
                f'command.{GROUPS[group]}.{CARRIERS[carrier]}.cut_below_unserved')
            add({cut: 1., retention: demand}, -np.inf, demand,
                f'command.{GROUPS[group]}.{CARRIERS[carrier]}.retained_contacts_zero_cut')
            add({cut: 1., unserved: -1., retention: demand}, 0., np.inf,
                f'command.{GROUPS[group]}.{CARRIERS[carrier]}.ordinary_cut_equals_unserved')
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
    for i in range(4):
        for k in range(3):upper[unmet(i, k)] = loads[i, k]
    if separate_ceiling:
        upper[ceiling] = upper[ig]
        add({ig: 1., ceiling: -1.}, -np.inf, 0., 'command.import_used_below_ceiling', source='existing arrived_ceiling interface')
    if fuel is not None:
        add({idg: 1., fuel: -1.}, -np.inf, state.diesel_energy_remaining_kWh/options['support_hours'],
            'preparation.endurance_preference', source='registered preparation preference, not physical fuel horizon')

    for case in copies:
        label = case['name']; demand = case['loads']; conv = case['converters']
        rg = case['grid']; rw = case['steam']; ru = case['unmet']; rs = case['surplus']
        mask = case['gt_mask']
        re = vector(); re[gp] = mask; re[idg] = 1.; re[rg] = 1.; re[rs] = -1.
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
        upper[rg] = 0. if label == 'GRID_loss' else upper[ig]
        if label != 'GRID_loss':
            add({rg: 1., ceiling: -1.}, 0. if contract['grid_import_mode'] == 'fixed' else -np.inf, 0.,
                f'risk.{label}.shared_grid_command', source='original fixed or arrived_ceiling importer contract')
        add({int(index): float(value) for index, value in zip(conv, tower_co)}, -np.inf, tower_limit,
            f'risk.{label}.tower.rejection')
        if contract['diesel_mode'] == 'emergency_bus':
            coefficients = {idg: 1., rs: -1.}
            coefficients.update({ru+3*i: 1. for i in allowed})
            add(coefficients, -np.inf, float(demand[allowed, 0].sum()), f'risk.{label}.diesel.allowed_bus',
                source='same allowed bus; same diagnostic surplus defect marks unusable power')

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
    objectives = []
    def priority(group):
        co = vector()
        for k in range(3):co[unmet(group, k)] = 1./max(1., float(loads[:, k].sum()))
        objectives.append((f'current_{GROUPS[group]}', co, 'normalized carrier ENS', group))
    priority(0); priority(1)
    if copies:
        for group in (0, 1):
            co = vector()
            for case in copies:
                for k in range(3):co[case['unmet']+group*3+k] = 1./max(1., float(case['loads'][:, k].sum()))
            objectives.append((f'preparation_{GROUPS[group]}', co, 'sum of normalized static carrier ENS; not probability', None))
    if copies or fuel is not None:
        co = vector()
        if fuel is not None:co[fuel] = 1.
        for case in copies:co[case['surplus']] = 1.
        objectives.append(('preparation_defect', co, 'kW: fuel preference excess plus static unabsorbable surplus', None))
    if regulator_active:
        co = vector(); co[regulator_excess] = 1.
        objectives.append(('regulator_GT_excess', co, 'kW above existing minimum stable GT outputs', None))
    priority(2); priority(3)
    objectives.append(('operating_cost_and_original_preferences', cost, 'USD per current step', None))
    constraints = [LinearConstraint(np.asarray(matrix), np.asarray(row_low), np.asarray(row_high))]
    variables = [dict(index=i, name=name, unit=units[i]) for i, name in enumerate(names)]
    diagnostic_input = {}
    if active_collector() is not None:
        diagnostic_input = dict(row=dict(timestamp=str(row.timestamp), outdoor_temperature_C=float(row.outdoor_temperature_C)),
            plant=plant.as_dict(), loads_kW=loads, availability=availability, state=asdict(state), dt_h=dt_h,
            grid_import_limit_kW=grid_import_limit_kW, diesel_start_available=diesel_start_available,
            gt_ramp_kW_per_h=gt_ramp_kW_per_h, config=config, tracking_targets=tracking_targets,
            security_gt_penalty_USD_per_kWh=security_weight, execution_contract=contract,
            joint_options=options, risk_loads=risk_loads, retention_eligible=retention_eligible)
    locks = []; layers = []; backend_layers = []; lex_values = [None]*4; x = None
    for rank, (name, co, unit, group) in enumerate(objectives):
        result = solve_layer(milp, co, integrality=integrality, bounds=Bounds(lower, upper), constraints=constraints,
            options={'time_limit': solver_time_limit_sec, 'mip_rel_gap': 1e-6, 'presolve': False}, rank=rank,
            variables=variables, rows=metadata, locks=locks, input_context=diagnostic_input, row_scaling=True)
        backend_layers.append(dict(rank=rank, layer=name, backend=getattr(result, 'backend', 'injected'),
            numerical_version=getattr(result, 'numerical_version', None), status=int(result.status),
            attempts=getattr(result, 'attempts', [])))
        if result.x is None or result.status != 0:
            error = ModelConstraintInfeasible if result.status == 2 else SolverExecutionError
            failure = error(f'Joint current MILP failure: layer={name}, rank={rank}, status={result.status}, message={result.message}')
            failure.solver_backend_attempts = backend_layers
            failure.joint_diagnostic = dict(options=options, completed_layers=layers, failing_layer=name)
            raise failure
        x = np.asarray(result.x, dtype=float)
        value = float(co@x)
        layer = dict(rank=rank, name=name, objective_value=value, unit=unit)
        if group is not None:lex_values[group] = value
        if rank < len(objectives)-1:
            tolerance = lex_lock_tolerance(group, value, config) if group is not None else options['risk_tolerance']
            bound = value+tolerance
            constraints.append(LinearConstraint(co, -np.inf, bound))
            locks.append(dict(source_rank=rank, objective=co.copy(), objective_constant=0.,
                incumbent_raw_objective=value, tolerance=tolerance, upper=bound, relation='<=', unit=unit,
                source='raw current or preparation optimum, original P0-P3 tolerances unchanged'))
            metadata.append(dict(name=f'lock.{name}', unit=unit, source='explicit lexicographic lock', kind='priority_lock'))
            layer.update(lock_tolerance=tolerance, lock_upper=bound)
        layers.append(layer)

    for layer, (_, co, _, _) in zip(layers, objectives):
        layer['final_objective_value'] = float(co@x)
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
    risk_records = {}
    for case in copies:
        side = case['balance']@x-case['rhs']; conv = x[case['converters']]
        case_gt = stable_floor*x[go] if case['regulator_floor'] else gts*case['gt_mask']
        risk_records[case['name']] = dict(single_shared_command=True, loads_kW=case['loads'].tolist(),
            gt_electric_kW=case_gt.tolist(), hrsg_steam_lbph=float(case_gt@hrsg*steam_per_kW),
            GT_source_basis=('current_command_on_at_existing_stable_floor' if case['regulator_floor']
                             else 'shared_current_planned_GT_with_named_loss'),
            diesel_kW=float(x[idg]), boiler_steam_lbph=float(x[ib]), grid_import_kW=float(x[case['grid']]),
            absorption_cooling_kW=float(conv[0]*RT_TO_KW), electric_chiller_cooling_kW=(conv[1:]*RT_TO_KW).tolist(),
            unserved_kW=x[case['unmet']:case['unmet']+12].reshape(4, 3).tolist(),
            electric_surplus_defect_kW=float(x[case['surplus']]),
            balance_residuals=[float(side[0]), float(side[2]), float(side[1]*heat_factor)],
            steam_balance_residual_lbph=float(side[1]), diagnostic_not_actual_execution=True)
    security_penalty = security_weight*dt_h*float(gts.sum())
    tracking_penalty = sum(float(cost[index]*x[index]) for index in target_indices.values())
    diagnostic = dict(schema='autonomous_dispatch.current_joint_margin.v1', options=options, layers=layers,
        critical_reserve_multiplier=options['critical_reserve_multiplier'], critical_envelope_active=True,
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
    diagnostic.update(regulator_consistent=options['regulator_consistent'], regulator_pressure_active=regulator_active,
        regulator_excess_kW=0. if regulator_excess is None else float(x[regulator_excess].sum()),
        regulator_excess_by_GT_kW=[0., 0., 0.] if regulator_excess is None else x[regulator_excess].tolist(),
        command_GT_on=x[go].tolist(), regulator_floor_GT_kW=(stable_floor*x[go]).tolist(),
        regulator_semantics='Existing stable-floor pressure assumption; not arrived output or a temporal prediction',
        regulator_is_execution_guarantee=False)
    command_disconnected = np.maximum(0., x[contacts])
    retention_selected = bool(x[retention] > .5)
    diagnostic.update(connection_mode=options['connection_mode'], retention_eligible=retention_eligible,
        retention_selected=retention_selected, connection_gate_active=retention_selected,
        command_disconnected_kW=command_disconnected.tolist(), planned_unserved_kW=lost.tolist(),
        automatic_shedding_not_overridden=True, eligibility_requires_no_pending_GT_start=True,
        contact_variable_count=6, retention_binary_count=1,
        retention_means_no_new_GT_command=True)
    if active_collector() is not None:active_collector().postprocess(x.copy())
    return dict(action=command_action, planned_current_action=current_action, lost=lost, served=served,
        command_disconnected=command_disconnected, planned_unserved=lost.copy(),
        lex_values=lex_values, cost_USD_step=float(operating@x), tracking_penalty_USD_step=tracking_penalty,
        optimization_objective_USD_step=float(cost@x), security_objective_penalty_USD_step=security_penalty,
        energy_balance_residuals=residuals, steam_balance_residual_lbph=float(residual[1]),
        optimizer_warning='', solver_backend_layers=backend_layers, joint_diagnostic=diagnostic)
