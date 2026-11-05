"""Current observed supply and one-command promises; no exogenous future access."""
from __future__ import annotations

import copy
import math
from numbers import Real

import numpy as np

SCHEMA = 'operating_response.readiness.v15.ramp_window_v2'
CONTEXT_FIELDS = frozenset(('schema', 'observation_time_minute', 'actual_gt_kW',
    'gt_physical_status', 'earliest_start_minute', 'pending_starts', 'gt_can_stop',
    'arrived_dispatch', 'actual_disconnected_kW', 'observed_critical_unserved_kW',
    'known_command_queue', 'gt_ramp_anchor_kW', 'gt_ramp_budget_kW', 'diesel_connected',
    'actual_diesel_kW', 'diesel_arrived_cap_kW'))
DISPATCH_FIELDS = frozenset(('gt_electric_kW', 'grid_import_kW', 'diesel_kW',
    'boiler_steam_lbph', 'absorption_cooling_kW', 'electric_chiller_cooling_kW',
    'hrsg_steam_lbph', 'tower_rejection_kW', 'gt_fuel_MMBtu_h'))
STATUSES = frozenset(('RUNNING', 'OFF', 'STARTING', 'WAIT_MIN_OFF',
    'INTERLOCK_LATCHED', 'PROTECTION_LOCKED', 'EXOGENOUS_UNAVAILABLE'))
OPERATING_DEFAULTS = dict(preparation_profile='K1', phase_constraints_enabled=True, continuity=True)


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < 0.:
        raise ValueError('Invalid nonnegative readiness value: '+name)
    return float(value)


def matrix(value, shape, name):
    data = np.asarray(value)
    if data.shape != shape or data.dtype.kind not in 'fiu' or not np.isfinite(data).all() or np.any(data < 0.):
        raise ValueError('Invalid readiness matrix: '+name)
    return data.astype(float).tolist()


def command_ramp_window(*, executed_gt_kW, observation_time_minute,
                        previous_ramp_time_minute, previous_ramp_anchor_kW,
                        previous_ramp_budget_kW, dt_minutes, gt_ramp_kW_per_h):
    """Describe the existing ordinary-command window, without issuing budget.

    Mirrors the original _clamp clock: calls at the same timestamp share its
    prior anchor and budget. A genuinely new time uses the current executed
    setting as before, and only the original elapsed-time budget. Actual
    delivered GT ports remain a separate readiness observation.
    """
    executed = matrix(executed_gt_kW, (3,), 'executed_gt_kW')
    now = number(observation_time_minute, 'observation_time_minute')
    dt = number(dt_minutes, 'dt_minutes')
    if dt == 0.:
        raise ValueError('Ordinary ramp window needs a positive decision interval')
    rate = number(gt_ramp_kW_per_h, 'gt_ramp_kW_per_h')
    old_budget = number(previous_ramp_budget_kW, 'previous_ramp_budget_kW')
    if previous_ramp_time_minute is None:
        elapsed = dt
    else:
        previous = number(previous_ramp_time_minute, 'previous_ramp_time_minute')
        # The original execution clock uses EPS=1e-9 minutes for this branch.
        if previous > now+1e-9:
            raise ValueError('Ramp window cannot come from a future execution')
        if abs(now-previous) <= 1e-9:
            return dict(gt_ramp_anchor_kW=matrix(previous_ramp_anchor_kW, (3,), 'previous_ramp_anchor_kW'),
                        gt_ramp_budget_kW=old_budget)
        elapsed = min(dt, now-previous)
    return dict(gt_ramp_anchor_kW=executed,
                gt_ramp_budget_kW=number(rate*elapsed/60., 'computed_ramp_budget_kW'))


def validate_dispatch(value):
    if not isinstance(value, dict) or set(value)-DISPATCH_FIELDS or not {'gt_electric_kW', 'grid_import_kW', 'diesel_kW'} <= set(value):
        raise ValueError('Invalid arrived/permitted dispatch fields')
    result = {}
    for key, val in value.items():
        result[key] = matrix(val, (3,), key) if key in ('gt_electric_kW', 'electric_chiller_cooling_kW') else number(val, key)
    return result


def preparation_profile(name='K1'):
    if name not in ('K0', 'K1', 'K2'):
        raise ValueError('Unknown pre-registered preparation profile')
    index = ('K0', 'K1', 'K2').index(name)
    return dict(name=name, critical_multiplier=(1., 1.5, 2.)[index],
        hold_duration_minutes=(15., 30., 60.)[index], arrival_delay_bounds_minutes=[0., 30.],
        priorities=['P0', 'P1'], carriers=['electricity', 'heat', 'cooling'],
        initial_contingencies=['GRID_loss', 'single_GT_loss', 'single_HRSG_loss'],
        assumed_simultaneous_GRID_and_GT_loss=False, actual_arrival_known=False,
        numerical_domain_is_OEM_or_clinical_standard=False)


def operating_options(value=None):
    if value is not None and (not isinstance(value, dict) or set(value)-set(OPERATING_DEFAULTS)):
        raise ValueError('Unsupported operating options; no future inputs')
    result = {**OPERATING_DEFAULTS, **copy.deepcopy(value or {})}
    preparation_profile(result['preparation_profile'])
    for key in ('phase_constraints_enabled', 'continuity'):
        if type(result[key]) is not bool:
            raise ValueError(key+' must be boolean')
    return result


def validate_context(value):
    if not isinstance(value, dict) or set(value) != CONTEXT_FIELDS or value['schema'] != SCHEMA:
        raise ValueError('Missing or unsupported strict current readiness context')
    result = copy.deepcopy(value)
    now = number(value['observation_time_minute'], 'observation_time_minute')
    for key in ('actual_gt_kW', 'earliest_start_minute', 'gt_ramp_anchor_kW'):
        result[key] = matrix(value[key], (3,), key)
    result['gt_ramp_budget_kW'] = number(value['gt_ramp_budget_kW'], 'gt_ramp_budget_kW')
    statuses = value['gt_physical_status']
    if not isinstance(statuses, (list, tuple)) or len(statuses) != 3 or any(s not in STATUSES for s in statuses):
        raise ValueError('Invalid observed GT physical status')
    for power, status in zip(result['actual_gt_kW'], statuses):
        if (power > 1e-9) != (status == 'RUNNING'):
            raise ValueError('Actual GT power and physical status disagree')
    if not isinstance(value['gt_can_stop'], (list, tuple)) or len(value['gt_can_stop']) != 3 or any(type(v) is not bool for v in value['gt_can_stop']):
        raise ValueError('Invalid actual GT stop permissions')
    result['gt_can_stop'] = list(value['gt_can_stop'])
    result['gt_physical_status'] = list(statuses)
    pending = value['pending_starts']
    if not isinstance(pending, (list, tuple)) or len(pending) != 3:
        raise ValueError('Three observed pending slots required')
    result['pending_starts'] = []
    for i, p in enumerate(pending):
        if p is None:
            if statuses[i] in ('STARTING', 'WAIT_MIN_OFF'):
                raise ValueError('Waiting GT is missing its arrived request')
            result['pending_starts'].append(None); continue
        if not isinstance(p, dict) or set(p) != {'started', 'ready', 'requested'}:
            raise ValueError('Unknown actual pending request fields')
        record = {key:number(v, 'pending.'+key) for key,v in p.items()}
        if record['ready'] < record['started'] or record['requested'] <= 0. or statuses[i] not in ('STARTING', 'WAIT_MIN_OFF'):
            raise ValueError('Inconsistent pending request')
        result['pending_starts'].append(record)
    result['arrived_dispatch'] = validate_dispatch(value['arrived_dispatch'])
    for key, shape in (('actual_disconnected_kW',(4,3)), ('observed_critical_unserved_kW',(2,3))):
        result[key] = matrix(value[key], shape, key)
    if type(value['diesel_connected']) is not bool:
        raise ValueError('Diesel connection must be an observed boolean')
    result['actual_diesel_kW'] = number(value['actual_diesel_kW'], 'actual_diesel_kW')
    result['diesel_arrived_cap_kW'] = number(value['diesel_arrived_cap_kW'], 'diesel_arrived_cap_kW')
    if not value['diesel_connected'] and result['actual_diesel_kW'] > 1e-9:
        raise ValueError('Positive diesel actual output without connection')
    queue = value['known_command_queue']
    if not isinstance(queue, (list, tuple)):
        raise ValueError('Invalid current queue promise ledger')
    result['known_command_queue'] = []; ids = set()
    for command in queue:
        if not isinstance(command, dict) or set(command) != {'command_id','generated_minute','planned_arrival_minute','permitted_dispatch'}:
            raise ValueError('Unknown queue fields; actual future arrival is forbidden')
        cid = command['command_id']
        if type(cid) is not int or cid <= 0 or cid in ids:
            raise ValueError('Invalid queue command identity')
        ids.add(cid)
        generated = number(command['generated_minute'], 'queue generated time')
        # Only a genuine controller-visible acknowledgement may contain a
        # planned time. Unknown ACK stays null; the simulator release script
        # is never a substitute. Registered profile bounds are separate data.
        arrival = (None if command['planned_arrival_minute'] is None else
            number(command['planned_arrival_minute'], 'queue acknowledged planned time'))
        if generated > now or (arrival is not None and arrival < generated):
            raise ValueError('Queue promise was not available at the decision')
        result['known_command_queue'].append(dict(command_id=cid,generated_minute=generated,
            planned_arrival_minute=arrival, permitted_dispatch=validate_dispatch(command['permitted_dispatch'])))
    return result


def phase_sources(context, command, *, gt_start_delay_minutes, gt_ramp_kW_per_h):
    c = validate_context(context); a = validate_dispatch(command)
    now = float(c['observation_time_minute']); delay = number(gt_start_delay_minutes, 'GT delay')
    rate = number(gt_ramp_kW_per_h, 'GT ramp')
    if rate == 0.:
        raise ValueError('GT startup reachability needs a positive inherited ramp rate')
    actual = c['actual_gt_kW']; targets = a['gt_electric_kW']
    arrived = [target if status == 'RUNNING' else 0. for target,status in zip(targets,c['gt_physical_status'])]
    earliest = []
    for i, target in enumerate(targets):
        if target <= 1e-9 or c['gt_physical_status'][i] in ('PROTECTION_LOCKED','EXOGENOUS_UNAVAILABLE'):
            earliest.append(None); continue
        pending = c['pending_starts'][i]
        if actual[i] > 1e-9: effective = now
        elif pending is not None: effective = max(now,pending['ready'])
        else: effective = max(now,c['earliest_start_minute'][i])+max(delay,target/rate*60.)
        earliest.append(effective)
    promised = [dict(**q,planned_arrival_is_guarantee=False,
        arrival_acknowledged=q['planned_arrival_minute'] is not None) for q in c['known_command_queue']]
    held = dict(gt_electric_kW=actual, actual_gt_electric_kW=actual,
        hrsg_gt_basis_kW=actual, actual_diesel_kW=c['actual_diesel_kW'],
        diesel_permitted_cap_kW=c['diesel_arrived_cap_kW'] if c['diesel_connected'] else 0.,
        grid_import_limit_kW=c['arrived_dispatch']['grid_import_kW'], actual_supply=True,
        source_basis='observed_executed_ports_and_arrived_permissions')
    pre = dict(gt_electric_kW=arrived,hrsg_gt_basis_kW=arrived,
        diesel_permitted_cap_kW=min(a['diesel_kW'], c['diesel_arrived_cap_kW']) if c['diesel_connected'] else 0.,
        actual_supply=False, conditional_on_current_command_arrival=True,
        source_basis='same_arrived_command_with_no_new_GT_or_HRSG_before_ready')
    post = dict(gt_electric_kW=[p if t is not None else 0. for p,t in zip(targets,earliest)],
        earliest_possible_effective_minute=earliest,assured_by_minute=[None]*3,
        actual_supply=False,conditional_on_no_new_fault=True,
        source_basis='current_command_conditional_promise_not_actual_future_supply')
    return dict(held_before_arrival=held,arrived_before_new_ready=pre,committed_ready=post,
        queue_promises=promised,actual_arrival_known=False)
