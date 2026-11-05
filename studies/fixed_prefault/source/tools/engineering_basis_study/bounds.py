"""Pure, unit-separated engineering bounds; never constructs a simulator.

Public functions accept JSON-like mappings, reject unknown keys/nonfinite or
negative numbers, and return JSON-serializable dictionaries. ``None`` denotes
unknown input, never zero. Each storage call concerns exactly one carrier.

``cold_generation_upper_bound`` relaxes readiness/queues and maximizes EC+ABS
within simultaneous nameplate, shared-tower, steam and electric constraints.
Unknown EC water-side heat split is the explicit [0, all-in] envelope; all-in
electricity remains payable at either endpoint. Tower factors 1/1.25 identify
alternative nominal-ton interpretations, not modified equipment evidence.

``storage_prefix_replay`` uses the original discrete exponential-loss balance.
``ideal_recharge_necessary_bound`` instead permits ideal in-window recharge;
its positive ENS is a necessary shortage, zero does not prove feasibility.
``startup_bridge_requirements`` uses demand UPPER and already-online supply
LOWER envelopes only, credits no new/unready chiller and invents no warmup.
``transfer_path_qualification`` separates destination, OPEN station power and
post-return restart; building UPS is never a station source.
``service_sufficiency_certificate`` requires explicit lower/upper envelopes,
all power/path qualifications and a passing actual storage prefix witness.
"""
from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
import itertools
import json
import math

RT_TO_KW = 3.51685284
_STORE_FIELDS = {'carrier', 'capacity_kWh', 'power_kW', 'initial_kWh',
                 'charge_efficiency', 'discharge_efficiency', 'loss_per_hour'}


def _fields(value, allowed):
    if not isinstance(value, Mapping):
        raise ValueError('A JSON object is required')
    extra = set(value) - allowed
    missing = allowed - set(value)
    if extra:
        raise ValueError('Unknown fields: ' + repr(sorted(extra, key=str)))
    if missing:
        raise ValueError('Missing fields: ' + repr(sorted(missing)))
    return dict(value)


def _number(value, name, *, unknown=False, positive=False, upper=None):
    if value is None and unknown:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0.
            or (positive and value == 0.) or (upper is not None and value > upper)):
        raise ValueError('Invalid finite nonnegative ' + name)
    return float(value)


def _boolean(value, name):
    if value is not None and type(value) is not bool:
        raise ValueError('Expected boolean or unknown: ' + name)
    return value


def _rows(values, fields, *, boolean_fields=()):
    if not isinstance(values, (list, tuple)):
        raise ValueError('An explicit interval list is required')
    result = []
    for value in values:
        row = _fields(value, fields)
        for key in row:
            row[key] = (_boolean(row[key], key) if key in boolean_fields else
                        _number(row[key], key, unknown=key != 'duration_hours',
                                positive=key == 'duration_hours'))
        result.append(row)
    return result


def _store(value):
    config = _fields(value, _STORE_FIELDS)
    if config['carrier'] not in ('e', 'h', 'c'):
        raise ValueError('Exactly one carrier e, h or c is required')
    for key in _STORE_FIELDS - {'carrier'}:
        config[key] = _number(config[key], key, unknown=True,
                              positive=key.endswith('efficiency'),
                              upper=1. if key.endswith('efficiency') or key == 'loss_per_hour' else None)
    if (config['initial_kWh'] is not None and config['capacity_kWh'] is not None
            and config['initial_kWh'] > config['capacity_kWh']):
        raise ValueError('Initial inventory exceeds capacity')
    return config


def _json(value):
    # Also rejects derived infinities, including arithmetic overflow.
    json.dumps(value, allow_nan=False)
    return value


def _all(values):
    values = list(values)
    return False if False in values else None if None in values else True


def _ge(left, right):
    return None if left is None or right is None else left >= right


def cold_generation_upper_bound(spec):
    """Shared EC/ABS optimistic bound in kW_c with exact rational vertices.

    Required fields are explicit, including known source coefficients and
    ``steam_available_lbph``/``electric_available_kW``. Their None values
    explicitly RELAX those resources, rather than assuming a supply of zero.
    An unknown tower capacity cannot yield a numeric upper bound.
    """
    fields = {'electric_chillers_rt', 'absorption_rt', 'tower_rejection_kW',
              'tower_semantics_factor', 'ec_all_in_kW_per_rt', 'ec_condenser_kW_per_rt',
              'absorption_electric_kW_per_rt', 'absorption_steam_lb_per_rt',
              'steam_kWh_per_lb', 'steam_available_lbph', 'electric_available_kW'}
    value = _fields(spec, fields)
    chillers = value['electric_chillers_rt']
    if not isinstance(chillers, (list, tuple)) or not chillers:
        raise ValueError('Explicit EC nameplate list required')
    chillers = [_number(x, 'EC rating') for x in chillers]
    for key in fields - {'electric_chillers_rt'}:
        value[key] = _number(value[key], key, unknown=key in {
            'ec_condenser_kW_per_rt', 'tower_rejection_kW',
            'steam_available_lbph', 'electric_available_kW'},
            positive=key in {'ec_all_in_kW_per_rt', 'absorption_steam_lb_per_rt', 'steam_kWh_per_lb'})
    factor = value['tower_semantics_factor']
    if factor not in (1., 1.25):
        raise ValueError('Explicit tower semantics factor must be 1 or 1.25')
    alpha, all_in = value['ec_condenser_kW_per_rt'], value['ec_all_in_kW_per_rt']
    if alpha is not None and alpha > all_in:
        raise ValueError('EC condenser coefficient exceeds stated all-in envelope')
    result = dict(schema='hospital_ies.engineering_bounds.cold.v1', carrier='c',
        unit='kW_c', ec_condenser_kW_per_rt=alpha, tower_semantics_factor=factor,
        interpretation='optimistic_resource_upper_bound_not_dispatch',
        source_coefficients=dict(RT_TO_KW=RT_TO_KW, **{k: value[k] for k in (
            'ec_all_in_kW_per_rt', 'absorption_electric_kW_per_rt',
            'absorption_steam_lb_per_rt', 'steam_kWh_per_lb')}),
        coefficient_basis=dict(all_in_electric='configs/plant_configs.json:historical_pnnl_performance',
            steam_conversion='configs/plant_configs.json:engineering_assumptions_not_from_source',
            condenser_heat_split='unknown plant-specific split; explicit relaxation envelope',
            nominal_tower_semantics='conditional 12k/15k Btu per hour unit interpretations'),
        relaxations=['No readiness, queue, ramp, auxiliary-distribution or priority restrictions.',
                     'Current steam/electric None explicitly relaxes that resource.',
                     'EC total all-in electric coefficient remains payable at every heat-split endpoint.'],
        branches=[], sufficient_feasibility_proved=False)
    if value['tower_rejection_kW'] is None:
        result.update(status='unknown', maximum_cooling_kW_c=None,
                      minimum_of_upper_bounds_kW_c=None)
        return _json(result)
    F = lambda x: Fraction(str(x))
    ec_cap, abs_cap = F(math.fsum(chillers)), F(value['absorption_rt'])
    steam = value['steam_available_lbph']
    if steam is not None:
        abs_cap = min(abs_cap, F(steam) / F(value['absorption_steam_lb_per_rt']))
    for coefficient in ([0., all_in] if alpha is None else [alpha]):
        # x=aggregate EC_RT, y=ABS_RT. All simultaneous linear constraints.
        constraints = [(F(-1), F(0), F(0)), (F(0), F(-1), F(0)),
                       (F(1), F(0), ec_cap), (F(0), F(1), abs_cap),
                       (F(RT_TO_KW)+F(coefficient),
                        F(RT_TO_KW)+F(value['absorption_steam_lb_per_rt'])*F(value['steam_kWh_per_lb']),
                        F(value['tower_rejection_kW'])*F(factor))]
        electricity = value['electric_available_kW']
        if electricity is not None:
            constraints.append((F(all_in), F(value['absorption_electric_kW_per_rt']), F(electricity)))
        candidates = []
        for (a, b, c), (d, e, f) in itertools.combinations(constraints, 2):
            determinant = a*e-b*d
            if determinant == 0:
                continue
            x, y = (c*e-b*f)/determinant, (a*f-c*d)/determinant
            if all(u*x+v*y <= w for u, v, w in constraints):
                candidates.append((x, y))
        x, y = max(candidates, key=lambda point: (sum(point), -point[1]))
        ec, absorption = float(x), float(y)
        witness = dict(EC_RT=ec, ABS_RT=absorption,
            cooling_kW_c=float(F(RT_TO_KW)*(x+y)),
            EC_electric_kW=float(F(all_in)*x),
            ABS_electric_kW=float(F(value['absorption_electric_kW_per_rt'])*y),
            ABS_steam_lbph=float(F(value['absorption_steam_lb_per_rt'])*y),
            tower_rejection_kW=float(constraints[4][0]*x+constraints[4][1]*y))
        result['branches'].append(dict(ec_condenser_kW_per_rt=coefficient,
            maximum_cooling_kW_c=witness['cooling_kW_c'], witness=witness))
    limits = [x['maximum_cooling_kW_c'] for x in result['branches']]
    result.update(status='bounded', maximum_cooling_kW_c=max(limits),
                  minimum_of_upper_bounds_kW_c=min(limits),
                  unknown_ec_heat_split_is_envelope=alpha is None)
    return _json(result)


def storage_prefix_replay(spec, intervals):
    """Audit explicit held charge/discharge powers, preserving failed prefixes.

    No clipping, endpoint repair, interpolation, carrier mixing or silent
    replacement of an unknown flow. Violations make the whole witness fail.
    """
    config = _store(spec)
    rows = _rows(intervals, {'duration_hours', 'charge_kW', 'discharge_kW'})
    energy, result_rows, first_failure = config['initial_kWh'], [], None
    unknown = any(v is None for k, v in config.items() if k != 'carrier')
    for index, row in enumerate(rows):
        violations = []
        duration, charge, discharge = row['duration_hours'], row['charge_kW'], row['discharge_kW']
        for name, power in (('charge_power', charge), ('discharge_power', discharge)):
            if power is not None and config['power_kW'] is not None and power > config['power_kW']:
                violations.append(name)
        if charge is not None and discharge is not None and min(charge, discharge) > 0.:
            violations.append('simultaneous_charge_discharge')
        known = not unknown and charge is not None and discharge is not None and energy is not None
        before = energy
        if known:
            standing = before * (1.-math.exp(-config['loss_per_hour']*duration))
            energy = before-standing+charge*duration*config['charge_efficiency']-discharge*duration/config['discharge_efficiency']
            if energy < 0.:
                violations.append('inventory_overdraft')
            if energy > config['capacity_kWh']:
                violations.append('capacity_overfill')
        else:
            unknown, standing, energy = True, None, None
        if violations and first_failure is None:
            first_failure = index
        result_rows.append(dict(row, initial_kWh=before, final_kWh=energy,
                                standing_loss_kWh=standing, violations=violations))
    status = 'fail' if first_failure is not None else 'unknown' if unknown else 'pass'
    return _json(dict(schema='hospital_ies.engineering_bounds.storage_prefix.v1',
        carrier=config['carrier'], stock_unit='kWh_'+config['carrier'], power_unit='kW_'+config['carrier'],
        store_config=config,
        status=status, final_kWh=energy, first_failed_prefix=first_failure, rows=result_rows,
        recurrence='E_next=E_before*exp(-loss_h*dt_h)+eta_c*P_charge*dt_h-P_discharge*dt_h/eta_d',
        clipped_or_repaired=False))


def ideal_recharge_necessary_bound(spec, intervals):
    """Optimal relaxed useful-energy buffer, including earlier recharge.

    Initial/capacity useful energy retain eta_d. Later charging is ideal and
    instantaneous, losses are zero, discharge power is retained, allocation
    and all other paths are relaxed. Greedy use of the relaxed buffer is exact
    for constant interval envelopes; lost service cannot be erased later.
    """
    config = _store(spec)
    rows = _rows(intervals, {'duration_hours', 'demand_kW', 'generation_upper_kW'})
    required = ('initial_kWh', 'capacity_kWh', 'discharge_efficiency', 'power_kW')
    known = all(config[k] is not None for k in required) and all(
        row[k] is not None for row in rows for k in ('demand_kW', 'generation_upper_kW'))
    result = dict(schema='hospital_ies.engineering_bounds.ideal_recharge.v1',
        carrier=config['carrier'], unit='kWh_'+config['carrier'], rows=[],
        sufficient_feasibility_proved=False,
        relaxations=['Zero standing loss; ideal cycle efficiency for in-window recharge.',
                     'Unlimited recharge power, retained useful capacity and discharge power.',
                     'Readiness/queues/auxiliary paths/allocation relaxed; generation is an UPPER bound.'])
    if not known:
        result.update(necessary_ENS_lower_bound_kWh=None, conclusion='unknown')
        return _json(result)
    useful = config['initial_kWh']*config['discharge_efficiency']
    capacity = config['capacity_kWh']*config['discharge_efficiency']
    shortages = []
    for row in rows:
        before, duration = useful, row['duration_hours']
        net = (row['generation_upper_kW']-row['demand_kW'])*duration
        if net >= 0.:
            useful, shortage = min(capacity, useful+net), 0.
        else:
            provided = min(-net, config['power_kW']*duration, useful)
            useful, shortage = useful-provided, -net-provided
        shortages.append(shortage)
        result['rows'].append(dict(row, useful_stock_before_kWh=before,
                                   useful_stock_after_kWh=useful, necessary_shortage_kWh=shortage))
    bound = math.fsum(shortages)
    result.update(necessary_ENS_lower_bound_kWh=bound,
                  conclusion='positive_necessary_shortage' if bound > 0. else 'cannot_prove_infeasible')
    return _json(result)


def startup_bridge_requirements(spec, intervals, *, new_source_ready_after_hours=None):
    """No-charge bridge against explicit demand-upper / online-supply-lower.

    Computed coverage is limited to the REGISTERED interval domain. Unknown
    warmup stays None; the deadline is an acceptance requirement, never an OEM
    time constant or a prediction. No output of a new source is credited.
    """
    config = _store(spec)
    rows = _rows(intervals, {'duration_hours', 'demand_upper_kW', 'online_supply_lower_kW', 'paths_qualified'},
                 boolean_fields={'paths_qualified'})
    ready = _number(new_source_ready_after_hours, 'new source ready delay', unknown=True)
    horizon = math.fsum(row['duration_hours'] for row in rows)
    numeric_known = all(config[k] is not None for k in _STORE_FIELDS-{'carrier', 'charge_efficiency'})
    numeric_known = numeric_known and all(row[k] is not None for row in rows
        for k in ('demand_upper_kW', 'online_supply_lower_kW'))
    path = _all(row['paths_qualified'] for row in rows)
    result = dict(schema='hospital_ies.engineering_bounds.startup_bridge.v1',
        carrier=config['carrier'], stock_unit='kWh_'+config['carrier'],
        registered_horizon_hours=horizon, new_source_ready_after_hours=ready,
        new_source_handoff_sufficient=None, new_source_output_credited=False,
        minimum_initial_kWh=None, maximum_covered_hours=None,
        maximum_acceptable_ready_delay_hours=None, bridge_horizon_sufficient=None,
        power_qualified=None, paths_qualified=path,
        interpretation='conditional_envelope_only_not_measured_warmup',
        coverage_censored_at_registered_horizon=None)
    if not numeric_known:
        return _json(result)
    deficits = [max(0., row['demand_upper_kW']-row['online_supply_lower_kW']) for row in rows]
    minimum = 0.
    try:
        for row, deficit in reversed(list(zip(rows, deficits))):
            minimum = (minimum+deficit*row['duration_hours']/config['discharge_efficiency'])*math.exp(
                config['loss_per_hour']*row['duration_hours'])
    except OverflowError as exc:
        raise ValueError('Derived minimum inventory exceeds finite arithmetic') from exc
    power = all(deficit <= config['power_kW'] for deficit in deficits)
    energy, covered, stopped = config['initial_kWh'], 0., False
    for row, deficit in zip(rows, deficits):
        if row['paths_qualified'] is not True or deficit > config['power_kW']:
            stopped = True
            break
        duration = row['duration_hours']
        def inventory(t):
            return energy*math.exp(-config['loss_per_hour']*t)-deficit*t/config['discharge_efficiency']
        after = inventory(duration)
        if after < 0.:
            low, high = 0., duration
            for _ in range(80):
                middle = (low+high)/2.
                if inventory(middle) >= 0.:
                    low = middle
                else:
                    high = middle
            covered += low
            stopped = True
            break
        energy, covered = after, covered+duration
    # Unknown path does not become a numeric power/energy guarantee.
    coverage = None if path is None else covered
    feasible = _all([path, power, minimum <= config['capacity_kWh'], minimum <= config['initial_kWh']])
    result.update(minimum_initial_kWh=minimum, maximum_covered_hours=coverage,
        maximum_acceptable_ready_delay_hours=coverage, bridge_horizon_sufficient=feasible,
        power_qualified=power, coverage_censored_at_registered_horizon=not stopped if coverage is not None else None,
        readiness_deadline_met=None if ready is None or coverage is None else ready <= coverage)
    return _json(result)


def transfer_path_qualification(spec):
    """Three independent path qualifications; no actual UPS-to-station port."""
    bool_fields = {'destination_supply_qualified', 'station_path_available',
                   'boiler_ready_before_open', 'boiler_request_held', 'post_station_continuous'}
    fields = bool_fields | {'open_gap_hours', 'station_aux_required_kW',
        'open_non_ups_station_supply_lower_kW', 'building_ups_available_kW',
        'restart_power_required_kW', 'post_non_ups_station_supply_lower_kW',
        'restart_required_hours', 'post_observed_hours'}
    value = _fields(spec, fields)
    for key in fields:
        value[key] = (_boolean(value[key], key) if key in bool_fields else
                      _number(value[key], key, unknown=key != 'open_gap_hours'))
    destination = value['destination_supply_qualified']
    gap = value['open_gap_hours']
    open_station = True if gap == 0. else _all([value['station_path_available'], _ge(
        value['open_non_ups_station_supply_lower_kW'], value['station_aux_required_kW'])])
    retained = _all([value['boiler_ready_before_open'], value['boiler_request_held'], open_station])
    if retained is True:
        post = dict(qualified=True, status='not_applicable', reason='ready_not_lost')
        completion = None
    else:
        post_ok = _all([value['boiler_request_held'], value['station_path_available'],
            value['post_station_continuous'], _ge(value['post_non_ups_station_supply_lower_kW'],
                value['restart_power_required_kW']),
            _ge(value['post_observed_hours'], value['restart_required_hours'])])
        post = dict(qualified=post_ok, status='qualified' if post_ok is True else 'failed' if post_ok is False else 'unknown',
                    reason='requires_actual_continuous_non_UPS_restart_supply')
        completion = value['restart_required_hours'] if post_ok is True and retained is False else None
    return _json(dict(schema='hospital_ies.engineering_bounds.transfer_paths.v1',
        destination=dict(qualified=destination, proof_is_open_gap_proof=False),
        open_station=dict(qualified=open_station, gap_hours=gap,
            non_UPS_supply_lower_kW=value['open_non_ups_station_supply_lower_kW'],
            required_station_kW=value['station_aux_required_kW']),
        boiler_ready_after_open=retained, post_restart=post,
        restart_completion_hours_after_return=completion,
        restart_timing_is_conditional_requirement=True,
        building_UPS_is_station_source=False,
        building_UPS_available_kW=value['building_ups_available_kW'],
        clinical_service_sufficiency_proved=False))


def service_sufficiency_certificate(storage_result, intervals):
    """Bind numeric demand/supply envelopes to every actual storage interval.

    Intervals contain duration_hours, demand_upper_kW, supply_lower_kW and
    paths_qualified. Supply is external to THIS store. Charging is a sink,
    discharging is a source; arbitrary ``known=True`` assertions are rejected.
    All other physical/path qualifications remain explicit caller premises.
    """
    rows = _rows(intervals, {'duration_hours', 'demand_upper_kW', 'supply_lower_kW', 'paths_qualified'},
                 boolean_fields={'paths_qualified'})
    storage_result = _fields(storage_result, {'schema', 'carrier', 'stock_unit', 'power_unit',
        'status', 'final_kWh', 'first_failed_prefix', 'rows', 'recurrence', 'clipped_or_repaired', 'store_config'})
    if (not isinstance(storage_result, Mapping)
            or storage_result.get('schema') != 'hospital_ies.engineering_bounds.storage_prefix.v1'
            or storage_result.get('carrier') not in ('e', 'h', 'c')
            or storage_result.get('status') not in ('pass', 'fail', 'unknown')):
        raise ValueError('An actual one-carrier storage-prefix result is required')
    stocks = storage_result.get('rows')
    if not isinstance(stocks, list) or len(stocks) != len(rows):
        raise ValueError('Service/storage interval counts differ')
    if not stocks:
        raise ValueError('A nonempty interval observation is required for a service certificate')
    stock_fields = {'duration_hours', 'charge_kW', 'discharge_kW',
                    'initial_kWh', 'final_kWh', 'standing_loss_kWh', 'violations'}
    stocks = [_fields(stock, stock_fields) for stock in stocks]
    replay = storage_prefix_replay(storage_result['store_config'], [
        {key: stock[key] for key in ('duration_hours', 'charge_kW', 'discharge_kW')}
        for stock in stocks])
    if json.dumps(storage_result, sort_keys=True, allow_nan=False) != json.dumps(replay, sort_keys=True, allow_nan=False):
        raise ValueError('Storage-prefix result differs from its numeric recurrence witness')
    inventory = {'pass': True, 'fail': False, 'unknown': None}[storage_result['status']]
    checks = []
    for envelope, stock in zip(rows, stocks):
        stock = _fields(stock, {'duration_hours', 'charge_kW', 'discharge_kW',
                               'initial_kWh', 'final_kWh', 'standing_loss_kWh', 'violations'})
        if envelope['duration_hours'] != stock.get('duration_hours'):
            raise ValueError('Service/storage interval duration differs')
        charge = _number(stock.get('charge_kW'), 'actual store charge', unknown=True)
        discharge = _number(stock.get('discharge_kW'), 'actual store discharge', unknown=True)
        demand, supply = envelope['demand_upper_kW'], envelope['supply_lower_kW']
        margin = None if None in (charge, discharge, demand, supply) else supply+discharge-demand-charge
        checks.append(dict(envelope, service_margin_kW=margin,
            balance_qualified=None if margin is None else margin >= 0.))
    qualified = _all([inventory, *[x for row in checks for x in
                                  (row['balance_qualified'], row['paths_qualified'])]])
    return _json(dict(schema='hospital_ies.engineering_bounds.service_certificate.v1',
        carrier=storage_result['carrier'], conditional_sufficient=qualified,
        rows=checks, inventory_prefix_qualified=inventory,
        scope='Only stated demand upper/supply lower envelopes, interval domain and qualified paths.',
        annual_or_clinical_guarantee=False))
