"""Current-information six-target requests; physical projection stays in env.step."""
from __future__ import annotations
import copy
import math
import numpy as np

ACTION_NAMES = ('gt_1', 'gt_2', 'gt_3', 'grid_import', 'diesel', 'absorption_cooling_kW')
DEVICES = tuple(f'{kind}_{i}' for kind in ('GT', 'HRSG', 'EC', 'BOILER') for i in (1, 2, 3)) + ('GRID', 'ABS', 'TOWER', 'DIESEL') + tuple(f'TOWER_{i}' for i in (1, 2, 3, 4))


def feature_whitelist():
    keys = {'time_minute', 'grid_outage_elapsed_minutes', 'hour_of_year', 'outdoor_temperature_C',
        'grid_import_limit_kW', 'arrived_grid_import_limit_kW', 'grid_import_prev_kW', 'diesel_prev_kW',
        'diesel_energy_kWh', 'diesel_status', 'topology_mode', 'control_available'}
    keys.update(prefix + d for prefix in ('availability_', 'capacity_', 'state_') for d in DEVICES)
    keys.update(f'pre_GT_{i}_kW' for i in (1, 2, 3)); keys.update(f'overload_minutes_GT_{i}' for i in (1, 2, 3))
    keys.update(f'{p}_{c}_{kind}_kW' for p in ('P0', 'P1', 'P2', 'P3') for c in ('e', 'h', 'c') for kind in ('demand', 'disconnected'))
    from hospital_ies.resilience import ZONE_PRIORITY
    keys.update(prefix + zone for prefix in ('zone_delta_', 'zone_margin_') for zone in ZONE_PRIORITY)
    return keys


def legal_history(records, max_length=4):
    if not records:
        raise ValueError('No current observation history')
    selected = records[-max_length:]; result = []; previous = -float('inf')
    for row in selected:
        t = float(row['decision_time_min']); features = row['features']; metadata = row['feature_metadata']
        if not math.isfinite(t) or t <= previous:
            raise ValueError('Noncontinuous or repeated decision ordering')
        if set(features) != set(metadata):
            raise ValueError('Missing feature provenance')
        if not set(features) <= feature_whitelist():
            raise ValueError('Feature outside current-observation whitelist')
        for m in metadata.values():
            if not {'available_time_min','valid_time_min'}<=set(m):
                raise ValueError('Missing feature provenance clock')
            if any(float(m[k])>t or not math.isfinite(float(m[k])) for k in ('available_time_min','valid_time_min')):
                raise ValueError('Unavailable or future-valid feature clock')
        result.append({'decision_time_min': t, 'features': copy.deepcopy(features), 'feature_metadata': copy.deepcopy(metadata)})
        previous = t
    return result


def baseline_request(policy_id):
    if policy_id == 'priority_milp':
        return {}
    if policy_id == 'secure_milp':
        return {'security': {'gt_loading_penalty_USD_per_kWh': .35}}
    raise ValueError('Unsupported original baseline policy')


def six_targets(dispatch):
    gt = dispatch['gt_electric_kW']
    return np.asarray([gt[0], gt[1], gt[2], dispatch['grid_import_kW'], dispatch['diesel_kW'], dispatch['absorption_cooling_kW']], float)


class BaselinePolicy:
    def __init__(self, policy_id):
        self.policy_id = policy_id; baseline_request(policy_id)

    def decide(self, records):
        legal_history(records)
        return baseline_request(self.policy_id), {'policy_id': self.policy_id, 'strategy_fallback': False,
            'strategy_status': 'original_baseline', 'model_id': None}


class PolicyController:
    """Inference fallback generates a baseline request, never actual port power.

    Model and support check receive only deep-copied legal history. Numerical
    solver exceptions are explicitly propagated rather than disguised as a
    model-output fallback; env.step remains outside this exception handler.
    """
    def __init__(self, model, *, model_id, fallback_policy='priority_milp',projection_policy='priority_milp'):
        self.model = model; self.model_id = model_id; self.policy_id = model_id; self.fallback_policy = fallback_policy
        self.projection_policy=projection_policy
        baseline_request(fallback_policy);baseline_request(projection_policy)

    def decide(self, records):
        history = legal_history(records)
        metadata = {'model_id': self.model_id, 'strategy_fallback': False, 'strategy_status': 'learned_request',
            'fallback_policy': self.fallback_policy, 'raw_prediction': None,'projection_policy':self.projection_policy}
        try:
            if hasattr(self.model, 'in_support') and not self.model.in_support(copy.deepcopy(history)):
                raise ValueError('Outside registered training support')
            output = np.asarray(self.model.predict_targets(copy.deepcopy(history)), float)
            metadata['raw_prediction'] = output.tolist()
            if output.shape != (6,) or not np.all(np.isfinite(output)) or np.any(output < 0):
                raise ValueError('Unavailable, nonfinite, negative or wrong-sized six-target output')
            request = baseline_request(self.projection_policy)
            request['targets']=dict(zip(ACTION_NAMES,map(float,output)))
        except Exception as error:
            if getattr(error, 'category', None) in ('solver_exception', 'model_constraint_infeasible'):
                raise
            request = baseline_request(self.fallback_policy)
            metadata.update(strategy_fallback=True, strategy_status='baseline_policy_fallback', fallback_reason=str(error),
                fallback_error_type=type(error).__name__, fallback_execution_semantics='ordinary_baseline_request_same_MILP_and_queue')
        return request, metadata
