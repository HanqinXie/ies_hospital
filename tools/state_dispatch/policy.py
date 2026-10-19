"""Current-state preparation, emergency and recovery on the original six ports.

No state advance, exogenous script, scenario identity or future observations
are available here. Targets are preferences; the original planner, queue,
arrival validation and physical execution remain authoritative.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from hospital_ies.data_contract import validate_action
from tools.control_improvement.runtime import project_current
from tools.integrated_learning.policies import (ACTION_NAMES, DEVICES,
                                                baseline_request, legal_history)


DEFAULT_CONFIG = {
    'identity': None,
    'prepare_weight': 0.0,
    'emergency_weight': 0.35,
    'recovery_weight': 0.35,
    'spread_prepare': True,
    'spread_emergency': True,
    'spread_recovery': False,
    'prepare_fraction': 0.55,
    'emergency_fraction': 0.55,
    'recovery_fraction': 0.45,
    'relative_demand_rise': 0.08,
    'absolute_demand_rise_kW': 5.0,
    'stress_trigger_minutes': 0.1,
    'disconnected_trigger_kW': 0.1,
    'stable_decisions': 2,
}


def validate_config(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_CONFIG):
        raise ValueError('Unsupported state-dispatch configuration')
    config = {**DEFAULT_CONFIG, **copy.deepcopy(value)}
    if config['identity'] not in (None, 'secure', 'priority'):
        raise ValueError('Unknown baseline identity')
    for key in ('spread_prepare', 'spread_emergency', 'spread_recovery'):
        if type(config[key]) is not bool:
            raise ValueError('Spread switches must be boolean')
    for key, lower, upper in (
        ('prepare_weight', 0, 10), ('emergency_weight', 0, 10),
        ('recovery_weight', 0, 10), ('prepare_fraction', .4, 1),
        ('emergency_fraction', .4, 1), ('recovery_fraction', .4, 1),
        ('relative_demand_rise', .001, 1), ('absolute_demand_rise_kW', 0, 1000),
        ('stress_trigger_minutes', 0, 15), ('disconnected_trigger_kW', 0, 1000),
    ):
        number = config[key]
        if (isinstance(number, bool) or not isinstance(number, (int, float)) or
                not math.isfinite(number) or not lower <= number <= upper):
            raise ValueError('Invalid state-dispatch parameter: ' + key)
        config[key] = float(number)
    if type(config['stable_decisions']) is not int or not 1 <= config['stable_decisions'] <= 8:
        raise ValueError('Stable decisions must be an integer in [1,8]')
    return config


def _vector(value, size, name):
    try:
        array = np.asarray(value, dtype=float)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError('Invalid ' + name) from error
    if array.shape != (size,) or not np.all(np.isfinite(array)) or np.any(array < 0):
        raise ValueError('Invalid ' + name)
    return array


def state_metrics(features):
    required = {f'availability_{d}' for d in DEVICES}
    required |= {f'{p}_{c}_demand_kW' for p in ('P0', 'P1', 'P2', 'P3') for c in 'ehc'}
    required |= {f'overload_minutes_GT_{i}' for i in (1, 2, 3)}
    required |= {f'P0_{c}_disconnected_kW' for c in 'ehc'}
    if required - set(features):
        raise ValueError('Missing required current-state observations')
    availability = _vector([features[f'availability_{d}'] for d in DEVICES], len(DEVICES), 'availability')
    if np.any(availability > 1):
        raise ValueError('Observed availability outside [0,1]')
    loads = _vector([sum(float(features[f'{p}_{c}_demand_kW'])
                         for p in ('P0', 'P1', 'P2', 'P3')) for c in 'ehc'], 3, 'demand')
    stress = _vector([features[f'overload_minutes_GT_{i}'] for i in (1, 2, 3)], 3, 'protection clock')
    disconnected = _vector([features[f'P0_{c}_disconnected_kW'] for c in 'ehc'], 3, 'P0 disconnected')
    return {'loads_kW': loads, 'overload_minutes': stress,
            'damage_count': int(np.sum(availability < 1 - 1e-6)),
            'p0_disconnected_kW': disconnected}


class StateModeTracker:
    """A finite observation history, with an explicit exit from recovery."""
    def __init__(self, config):
        self.config = validate_config(config)
        self.previous_loads = None
        self.mode = 'prepare'
        self.quiet_decisions = 0

    def update(self, features):
        metrics = state_metrics(features)
        loads = metrics['loads_kW']
        change = np.zeros(3) if self.previous_loads is None else loads - self.previous_loads
        relative = np.zeros(3) if self.previous_loads is None else change / np.maximum(self.previous_loads, 1.0)
        reasons = []
        if np.any((relative > self.config['relative_demand_rise']) &
                  (change > self.config['absolute_demand_rise_kW'])):
            reasons.append('observed_demand_rise')
        if metrics['damage_count']:
            reasons.append('currently_unavailable_equipment')
        if np.max(metrics['overload_minutes']) > self.config['stress_trigger_minutes']:
            reasons.append('observed_protection_accumulation')
        if np.max(metrics['p0_disconnected_kW']) > self.config['disconnected_trigger_kW']:
            reasons.append('observed_P0_disconnection')
        if reasons:
            self.mode = 'emergency'
            self.quiet_decisions = 0
        elif self.mode in ('emergency', 'recovery'):
            self.quiet_decisions += 1
            self.mode = ('prepare' if self.quiet_decisions >= self.config['stable_decisions']
                         else 'recovery')
        else:
            self.quiet_decisions = 0
        self.previous_loads = loads.copy()
        return {'mode': self.mode, 'reasons': reasons or ['current_observations_quiet'],
                'demand_change_kW': change.tolist(), 'relative_demand_change': relative.tolist(),
                'quiet_decisions': self.quiet_decisions,
                'damage_count': metrics['damage_count'],
                'observed_overload_minutes_GT': metrics['overload_minutes'].tolist()}


def spread_targets(plan, capacities, hrsg_health, overload_minutes, *, fraction,
                   minimum_fraction, protection_hold_minutes=15.0):
    """Redistribute a current plan, displacing planned grid import if needed.

    GT total can increase. The proposal never creates an electrical sink,
    adds diesel, changes a service lock, or claims its targets have arrived.
    A GT whose HRSG is unavailable retains its electrical plan but is not
    counted as an additional thermal-ready unit.
    """
    original = _vector(plan, 6, 'six-port plan')
    caps = _vector(capacities, 3, 'GT capacities')
    health = _vector(hrsg_health, 3, 'HRSG health')
    stress = _vector(overload_minutes, 3, 'overload minutes')
    if (not .4 <= float(fraction) <= 1 or float(minimum_fraction) != .4 or
            np.any(health > 1) or not math.isfinite(protection_hold_minutes) or protection_hold_minutes <= 0):
        raise ValueError('Invalid spread preference or original stable minimum')
    eligible = np.flatnonzero((caps > 1e-8) & (health > 1e-8))
    result = original.copy()
    detail = {'eligible_gt_indices': (eligible + 1).tolist(),
              'request_is_not_execution': True, 'reason': 'fleet_spread_request'}
    if len(eligible) < 2:
        return result, {**detail, 'reason': 'fewer_than_two_thermal_available_units'}
    minimum = .4 * caps[eligible]
    electrical_budget = float(sum(original[eligible]) + original[3])
    if electrical_budget + 1e-7 < sum(minimum):
        return result, {**detail, 'reason': 'insufficient_electric_absorption_for_fleet'}
    target_total = min(electrical_budget, max(float(sum(minimum)),
                                           float(fraction) * float(sum(caps[eligible]))))
    target = minimum.copy()
    remaining = target_total - float(sum(target))
    weights = caps[eligible] * health[eligible] * (1 - .7 * np.minimum(stress[eligible] / protection_hold_minutes, 1))
    for _ in range(4):
        active = np.flatnonzero(target < caps[eligible] - 1e-9)
        if remaining <= 1e-9 or not len(active):
            break
        addition = np.minimum(caps[eligible][active] - target[active],
                              remaining * weights[active] / sum(weights[active]))
        target[active] += addition
        remaining -= float(sum(addition))
    if remaining > 1e-6:
        raise ValueError('Unable to allocate finite GT preference')
    result[eligible] = target
    result[3] = max(0.0, electrical_budget - float(sum(target)))
    if abs(float(sum(result[:4]) - sum(original[:4]))) > 1e-6:
        raise ValueError('Spread request changed aggregate GT-plus-grid power')
    detail.update(delta_GT_total_kW=float(sum(result[:3]) - sum(original[:3])),
                  delta_grid_request_kW=float(result[3] - original[3]),
                  minimum_GT_targets_kW=minimum.tolist())
    return result, detail


def candidate_specs():
    return [
        {'policy_id': 'state_weight_only', 'kind': 'state_dispatch',
         'config': {'spread_prepare': False, 'spread_emergency': False,
                    'spread_recovery': False}},
        {'policy_id': 'state_spread_service', 'kind': 'state_dispatch',
         'config': {'emergency_weight': 0.0, 'recovery_weight': 0.0,
                    'spread_recovery': True}},
        {'policy_id': 'state_spread_protect', 'kind': 'state_dispatch',
         'config': {'emergency_weight': .35, 'emergency_fraction': .45}},
    ]


class StateDispatchPolicy:
    def __init__(self, config, policy_id):
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError('Nonempty state-dispatch policy ID required')
        self.config = validate_config(config)
        self.policy_id = policy_id
        self.reset_policy_memory()

    def reset_policy_memory(self):
        self.current_problem = None
        self.last_time = None
        self.tracker = StateModeTracker(self.config)

    def bind_current(self, problem):
        self.current_problem = problem

    def decide(self, records):
        history = legal_history(records)
        now = float(history[-1]['decision_time_min'])
        if self.last_time is not None and now <= self.last_time:
            raise ValueError('State-dispatch decision clock did not advance')
        self.last_time = now
        diagnostic = {'model_id': self.policy_id, 'feedback_family': 'state_dispatch_v1',
                      'strategy_fallback': False, 'fallback_reason': None,
                      'future_rollouts_online': 0, 'request_is_not_execution': True,
                      'baseline_request_submitted_unchanged': False,
                      'current_planning_queries': [], 'registered_config': copy.deepcopy(self.config)}
        if self.config['identity'] is not None:
            base = self.config['identity'] + '_milp'
            diagnostic.update(strategy_status='exact_baseline_identity',
                              baseline_policy=base, baseline_request_submitted_unchanged=True)
            self.current_problem = None
            return copy.deepcopy(baseline_request(base)), diagnostic
        f = history[-1]['features']
        state = self.tracker.update(f)
        mode = state['mode']
        weight = self.config[mode + '_weight']
        request = (baseline_request('priority_milp') if weight == 0 else
                   {'security': {'gt_loading_penalty_USD_per_kWh': weight}})
        diagnostic.update(state, selected_weight_USD_per_kWh=weight,
                          strategy_status='state_weight_request', selected_candidate_id='state_' + mode)
        if self.current_problem is None:
            raise ValueError('State dispatch needs a bound current problem')
        problem = self.current_problem
        self.current_problem = None
        if float(problem.time_minute) != now:
            raise ValueError('Bound current problem clock differs')
        diagnostic.update(current_planned_arrival_minute=float(problem.arrival_minute),
                          response_window_active=bool(problem.delayed),
                          current_known_response_wait_minutes=max(0.0, float(problem.arrival_minute) - now),
                          protection_observation_assumption='original ideal current runtime observation')
        if self.config['spread_' + mode]:
            query = project_current(problem, request)
            diagnostic['current_planning_queries'] = [{
                'candidate_id': 'same_state_base_plan', 'status': query['status'],
                'projected_targets_kW': query.get('projected_targets_kW'),
                'solver_calls': query.get('solver_calls'),
                'elapsed_seconds': query.get('elapsed_seconds'),
            }]
            if query['status'] != 'success':
                diagnostic.update(strategy_fallback=True, strategy_status='same_state_base_fallback',
                                  fallback_reason='current_plan_' + str(query['status']))
                return copy.deepcopy(request), diagnostic
            plan = np.asarray(query['projected_targets_kW'], dtype=float)
            targets, detail = spread_targets(
                plan, [f[f'capacity_GT_{i}'] for i in (1, 2, 3)],
                [f[f'availability_HRSG_{i}'] for i in (1, 2, 3)],
                [f[f'overload_minutes_GT_{i}'] for i in (1, 2, 3)],
                fraction=self.config[mode + '_fraction'], minimum_fraction=min(problem.plant.levels),
                protection_hold_minutes=float(problem.config['gt_overload_hold_minutes']))
            diagnostic.update(spread=detail, same_state_base_plan_targets_kW=plan.tolist(),
                              requested_six_targets_kW=targets.tolist(),
                              delta_from_same_state_plan_kW=(targets - plan).tolist())
            if np.max(np.abs(targets - plan)) > 1e-7:
                request = copy.deepcopy(request)
                request['targets'] = dict(zip(ACTION_NAMES, map(float, targets)))
                diagnostic['strategy_status'] = 'state_spread_request'
        validate_action(request)
        diagnostic['baseline_request_submitted_unchanged'] = request in (
            baseline_request('secure_milp'), baseline_request('priority_milp'))
        return copy.deepcopy(request), diagnostic
