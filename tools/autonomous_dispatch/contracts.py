"""Versioned current joint-planning requests, without extra execution events."""
from __future__ import annotations
import copy
import numpy as np
from hospital_ies.data_contract import validate_action as validate_original_action
from tools.integrated_learning.policies import legal_history

VARIANTS = frozenset({'economic', 'endurance', 'diverse', 'coupled'})


def validate_options(value):
    from .planner import _options
    return _options(value)


def validate_action(action):
    if not isinstance(action, dict):
        raise ValueError('Action must be a mapping')
    if 'joint' not in action:
        return validate_original_action(action)
    if set(action) != {'joint'}:
        raise ValueError('Joint dispatch cannot be combined with the legacy target projector')
    joint = action['joint']
    if not isinstance(joint, dict) or set(joint) - {'options', 'risk_loads_kW'}:
        raise ValueError('Unsupported joint request field')
    validate_options(joint.get('options', {}))
    if 'risk_loads_kW' in joint:
        loads = np.asarray(joint['risk_loads_kW'], float)
        if loads.shape != (4, 3) or not np.isfinite(loads).all() or (loads < 0).any():
            raise ValueError('History envelope must be a finite nonnegative 4x3 demand array')
    return copy.deepcopy(action)


class JointDispatchPolicy:
    def __init__(self, config, policy_id):
        self.config = validate_options(config)
        self.policy_id = policy_id
        self.current_problem = None

    def bind_current(self, problem):
        self.current_problem = problem

    def decide(self, records):
        history = legal_history(records, max_length=len(records))
        problem = self.current_problem
        self.current_problem = None
        if problem is None or float(history[-1]['decision_time_min']) != float(problem.time_minute):
            raise ValueError('Joint dispatch requires the same-clock pure current problem')
        observed = []
        for row in history:
            values = np.asarray([[row['features'][f'{g}_{c}_demand_kW']
                                  for c in ('e', 'h', 'c')] for g in ('P0', 'P1', 'P2', 'P3')], float)
            if not np.isfinite(values).all() or (values < 0).any():
                raise ValueError('Invalid observed demand')
            observed.append(values)
        envelope = np.minimum(np.min(observed, axis=0), np.asarray(problem.loads, float))
        request = {'joint': {'options': copy.deepcopy(self.config), 'risk_loads_kW': envelope.tolist()}}
        validate_action(request)
        return request, {'strategy_status': 'current_joint_dispatch', 'strategy_fallback': False,
            'model_id': None, 'policy_id': self.policy_id, 'mode': 'current_joint_dispatch',
            'history_observation_count': len(history), 'history_envelope_semantics':
            'Componentwise minimum of this episode legal observed loads; pressure assumption, not forecast',
            'future_rollouts_online': 0, 'planning_interface': 'joint_command.v1',
            'source_total_equal_to_priority_required': False, 'requested_joint_options': copy.deepcopy(self.config)}
