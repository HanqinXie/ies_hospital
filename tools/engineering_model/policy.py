"""Separate lawful engineering requests; no simulator or future-script access."""
from __future__ import annotations

import copy
import math

import numpy as np

from tools.integrated_learning.policies import legal_history
from tools.coordinated_dispatch.policy import DEFAULT_PLANNING_OPTIONS
from . import planner

PLANNING_INTERFACE = 'engineering_model.ordinary_joint_command.v14'
ACTION_SCHEMA = 'engineering_model.engineering_joint.v14'
FORBIDDEN_OUTCOMES = frozenset(('raw', 'segments', 'events', 'labels', 'actual_execution',
    'post_outcome', 'first_failure_observation_interval', 'first_failure_observed_minute'))


def validate_action(action):
    if not isinstance(action, dict) or set(action) != {'engineering_joint'}:
        raise ValueError('Engineering request must use its separate engineering_joint route')
    joint = action['engineering_joint']
    if not isinstance(joint, dict) or set(joint)-{'options', 'engineering_options', 'risk_loads_kW'}:
        raise ValueError('Unsupported engineering request fields')
    result = copy.deepcopy(action)
    joint = result['engineering_joint']
    joint['options'] = planner._options(joint.get('options', {}))
    if not joint['options']['features_enabled']:
        raise ValueError('Engineering contract cannot be disabled')
    joint['engineering_options'] = planner.engineering_options(joint.get('engineering_options', {}))
    if 'risk_loads_kW' in joint:
        loads = np.asarray(joint['risk_loads_kW'], dtype=float)
        if loads.shape != (4, 3) or not np.isfinite(loads).all() or np.any(loads < 0.):
            raise ValueError('Invalid observed load envelope')
        joint['risk_loads_kW'] = loads.tolist()
    return result


class EngineeringPolicy:
    def __init__(self, config, policy_id):
        if not isinstance(config, dict) or not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError('Engineering policy requires a configuration and explicit policy_id')
        supplied = copy.deepcopy(config)
        engineering = {key: supplied.pop(key) for key in tuple(supplied) if key in planner.ENGINEERING_DEFAULTS}
        self.engineering_options = planner.engineering_options(engineering)
        self.options = planner._options({**DEFAULT_PLANNING_OPTIONS, **supplied})
        self.policy_id = policy_id

    def decide(self, records):
        if any(FORBIDDEN_OUTCOMES.intersection(record) for record in records):
            raise ValueError('Hidden outcomes are not current observations')
        history = legal_history(records, max_length=len(records))
        features = history[-1]['features']
        required = {'availability_GRID', 'diesel_prev_kW', 'diesel_energy_kWh'}
        required.update(f'{g}_{c}_demand_kW' for g in ('P0', 'P1', 'P2', 'P3') for c in ('e', 'h', 'c'))
        if not required <= set(features):
            raise ValueError('Engineering current observation is incomplete')
        for key in required:
            value = features[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0.:
                raise ValueError('Invalid current engineering observation '+key)
        if features['availability_GRID'] > 1.:
            raise ValueError('Invalid current GRID availability')
        demands = []
        for view in history:
            f = view['features']
            matrix = np.asarray([[f[f'{g}_{c}_demand_kW'] for c in ('e', 'h', 'c')]
                for g in ('P0', 'P1', 'P2', 'P3')], dtype=float)
            if not np.isfinite(matrix).all() or np.any(matrix < 0.):
                raise ValueError('Invalid observed demand history')
            demands.append(matrix)
        fuel_hours = features['diesel_energy_kWh']/features['diesel_prev_kW'] if features['diesel_prev_kW'] > 1e-9 else None
        requested = (self.engineering_options['mode'] == 'endurance_restore' and features['availability_GRID'] < 1.-1e-9
            and fuel_hours is not None and fuel_hours < self.options['support_hours'])
        request = validate_action({'engineering_joint': dict(options=copy.deepcopy(self.options),
            engineering_options=copy.deepcopy(self.engineering_options), risk_loads_kW=np.min(demands, axis=0).tolist())})
        return request, dict(policy_id=self.policy_id, strategy_status='engineering_current_service_request',
            strategy_fallback=False, model_id=None, planning_interface=PLANNING_INTERFACE,
            requested_joint_options=copy.deepcopy(self.options), engineering_options=copy.deepcopy(self.engineering_options),
            restoration_request=not requested, endurance_preparation_requested=requested,
            actual_fuel_support_hours=fuel_hours, future_rollouts_online=0,
            envelope_semantics='minimum previously observed current demand; static pressure, not forecast',
            command_is_immediate_execution=False)


# Descriptive alias for call sites preferring a dispatch-specific class name.
EngineeringDispatchPolicy = EngineeringPolicy
