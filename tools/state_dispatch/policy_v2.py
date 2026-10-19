"""Versioned refinement after v1's real request/arrival/GT-trip diagnosis.

The old policy is retained byte-for-byte. This version bounds planned grid
targets by current legal capacity, distinguishes new from persistent damage,
and uses a current cooling/electric demand guard for source preparation.
All changes are policy preferences; no physical mechanism is added.
"""
from __future__ import annotations

import copy
import math
from types import FunctionType

import numpy as np

from tools.state_dispatch import policy as previous
from tools.integrated_learning.policies import DEVICES, legal_history


EXTRA_DEFAULTS = {'cooling_guard_ratio': .8, 'guard_weight': .35}


def validate_config(value):
    if not isinstance(value, dict) or set(value) - set(previous.DEFAULT_CONFIG) - set(EXTRA_DEFAULTS):
        raise ValueError('Unsupported v2 state-dispatch configuration')
    base = previous.validate_config({k: v for k, v in value.items() if k in previous.DEFAULT_CONFIG})
    extras = {**EXTRA_DEFAULTS, **{k: v for k, v in value.items() if k in EXTRA_DEFAULTS}}
    for key, lower, upper in (('cooling_guard_ratio', .1, 10), ('guard_weight', 0, 10)):
        number = extras[key]
        if (isinstance(number, bool) or not isinstance(number, (int, float)) or
                not math.isfinite(number) or not lower <= number <= upper):
            raise ValueError('Invalid v2 parameter: ' + key)
        extras[key] = float(number)
    return {**base, **extras}


class StateModeTracker(previous.StateModeTracker):
    """Persistent equipment loss remains visible but does not forbid recovery."""
    def __init__(self, config):
        super().__init__({k: v for k, v in config.items() if k in previous.DEFAULT_CONFIG})
        self.previous_availability = None

    def update(self, features):
        metrics = previous.state_metrics(features)
        loads = metrics['loads_kW']
        availability = np.asarray([features[f'availability_{d}'] for d in DEVICES], dtype=float)
        before = np.ones_like(availability) if self.previous_availability is None else self.previous_availability
        new_damage = bool(np.any(availability < before - 1e-6))
        change = np.zeros(3) if self.previous_loads is None else loads - self.previous_loads
        relative = np.zeros(3) if self.previous_loads is None else change / np.maximum(self.previous_loads, 1.)
        reasons = []
        if np.any((relative > self.config['relative_demand_rise']) &
                  (change > self.config['absolute_demand_rise_kW'])):
            reasons.append('observed_demand_rise')
        if new_damage:
            reasons.append('new_observed_equipment_loss')
        if np.max(metrics['overload_minutes']) > self.config['stress_trigger_minutes']:
            reasons.append('observed_protection_accumulation')
        if np.max(metrics['p0_disconnected_kW']) > self.config['disconnected_trigger_kW']:
            reasons.append('observed_P0_disconnection')
        if reasons:
            self.mode, self.quiet_decisions = 'emergency', 0
        elif self.mode in ('emergency', 'recovery'):
            self.quiet_decisions += 1
            self.mode = 'prepare' if self.quiet_decisions >= self.config['stable_decisions'] else 'recovery'
        else:
            self.quiet_decisions = 0
        self.previous_loads = loads.copy()
        self.previous_availability = availability.copy()
        return {'mode': self.mode, 'reasons': reasons or ['current_observations_quiet'],
                'demand_change_kW': change.tolist(), 'relative_demand_change': relative.tolist(),
                'quiet_decisions': self.quiet_decisions, 'damage_count': metrics['damage_count'],
                'new_damage_observed': new_damage,
                'persistent_damage_is_not_repaired': metrics['damage_count'] > 0,
                'observed_overload_minutes_GT': metrics['overload_minutes'].tolist()}


def spread_targets(plan, capacities, hrsg_health, overload_minutes, *, fraction,
                   minimum_fraction, grid_limit_kW, protection_hold_minutes=15.):
    limit = float(grid_limit_kW)
    if not math.isfinite(limit) or limit < 0:
        raise ValueError('Invalid current legal grid-import capacity')
    original = previous._vector(plan, 6, 'six-port plan')
    caps = previous._vector(capacities, 3, 'GT capacities')
    health = previous._vector(hrsg_health, 3, 'HRSG health')
    eligible = np.flatnonzero((caps > 1e-8) & (health > 1e-8))
    effective_fraction = float(fraction)
    if len(eligible) >= 2:
        budget = float(sum(original[eligible]) + original[3])
        required_gt = max(0., budget - limit)
        if required_gt > sum(caps[eligible]) + 1e-6:
            raise ValueError('Current plan cannot fit observed grid and GT capacities')
        effective_fraction = max(effective_fraction, min(1., required_gt / sum(caps[eligible])))
    targets, detail = previous.spread_targets(
        original, caps, health, overload_minutes, fraction=effective_fraction,
        minimum_fraction=minimum_fraction, protection_hold_minutes=protection_hold_minutes)
    if targets[3] > limit + 1e-6:
        raise ValueError('Spread target exceeds current legal grid-import capacity')
    return targets, {**detail, 'grid_limit_kW': limit,
                     'registered_fraction': float(fraction),
                     'grid_bounded_effective_fraction': effective_fraction}


class StateDispatchPolicy(previous.StateDispatchPolicy):
    def __init__(self, config, policy_id):
        registered = validate_config(config)
        self.v2_config = {k: registered[k] for k in EXTRA_DEFAULTS}
        base = {k: registered[k] for k in previous.DEFAULT_CONFIG}
        super().__init__(base, policy_id)
        self.base_config = copy.deepcopy(base)
        self.registered_v2_config = registered

    def reset_policy_memory(self):
        super().reset_policy_memory()
        self.tracker = StateModeTracker(self.config)

    def select_route(self, features):
        metrics = previous.state_metrics(features)
        electric, heat, cooling = metrics['loads_kW']
        ratio = float(cooling / max(electric, 1.))
        grid_available = float(features['availability_GRID']) > 1e-8
        guard = grid_available and ratio > self.v2_config['cooling_guard_ratio']
        return {'guard_active': bool(guard), 'observed_cooling_to_electric_ratio': ratio,
                'current_heat_demand_kW': float(heat),
                'route': 'cooling_dominant_source_diversification' if guard else 'current_fleet_preparation',
                'route_uses_scenario_identity': False}

    def decide(self, records):
        history = legal_history(records)
        features = history[-1]['features']
        route = self.select_route(features)
        selected = copy.deepcopy(self.base_config)
        if route['guard_active']:
            for mode in ('prepare', 'emergency', 'recovery'):
                selected[mode + '_weight'] = self.v2_config['guard_weight']
                selected['spread_' + mode] = False
        self.config = selected
        limit = float(features['grid_import_limit_kW']) * float(features['availability_GRID'])
        arrived = float(features['arrived_grid_import_limit_kW'])

        def bounded_spread(*args, **kwargs):
            return spread_targets(*args, grid_limit_kW=limit, **kwargs)

        # Isolated dependency binding reuses v1's exact planner/queue path;
        # it never changes module globals or another policy instance.
        namespace = dict(previous.StateDispatchPolicy.decide.__globals__)
        namespace['spread_targets'] = bounded_spread
        decide = FunctionType(previous.StateDispatchPolicy.decide.__code__, namespace,
                              previous.StateDispatchPolicy.decide.__name__)
        try:
            request, diagnostic = decide(self, records)
        finally:
            self.config = copy.deepcopy(self.base_config)
        diagnostic.update(route, feedback_family='state_dispatch_v2',
                          registered_config=copy.deepcopy(self.registered_v2_config),
                          selected_current_config=selected,
                          current_legal_grid_capacity_kW=limit,
                          arrived_grid_port_kW=arrived,
                          current_capacity_is_not_already_arrived_supply=True)
        return request, diagnostic


def candidate_specs():
    service = {'emergency_weight': 0., 'recovery_weight': 0., 'spread_recovery': True}
    return [
        {'policy_id': 'state_guarded_spread', 'kind': 'state_dispatch',
         'config': {**service, 'cooling_guard_ratio': .8}},
        {'policy_id': 'state_grid_bounded_spread', 'kind': 'state_dispatch',
         'config': {**service, 'cooling_guard_ratio': 10.}},
    ]
