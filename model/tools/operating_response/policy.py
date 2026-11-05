"""Separate v15 current-information request; immutable E6 stays in its namespace."""
from __future__ import annotations
import copy
import numpy as np
from tools.integrated_learning.policies import legal_history
from tools.engineering_model.policy import FORBIDDEN_OUTCOMES
from .readiness import operating_options

ACTION_SCHEMA = 'operating_response.operating_joint.v15'
PLANNING_INTERFACE = 'operating_response.ordinary_joint_command.v15'
DEFAULT_JOINT = dict(variant='coupled', support_hours=2., risk_tolerance=1e-7,
    connection_mode='retain_when_no_gt', regulator_consistent=True,
    critical_reserve_multiplier=1., preserve_one_gt_standby=True)


def validate_action(action):
    from .planner import _options
    if not isinstance(action,dict) or set(action) != {'operating_joint'}:
        raise ValueError('v15 requires its separate operating_joint route')
    joint = action['operating_joint']
    if not isinstance(joint,dict) or set(joint)-{'options','operating_options','risk_loads_kW'}:
        raise ValueError('Unsupported v15 current request fields')
    result = copy.deepcopy(action); joint = result['operating_joint']
    joint['options'] = _options(joint.get('options',DEFAULT_JOINT))
    joint['operating_options'] = operating_options(joint.get('operating_options'))
    if 'risk_loads_kW' in joint:
        demand = np.asarray(joint['risk_loads_kW'],float)
        if demand.shape != (4,3) or not np.isfinite(demand).all() or np.any(demand < 0.):
            raise ValueError('Invalid observed demand floor')
        joint['risk_loads_kW'] = demand.tolist()
    return result


class OperatingPolicy:
    def __init__(self,config,policy_id):
        if not isinstance(config,dict) or not isinstance(policy_id,str) or not policy_id.strip() or policy_id == 'endurance_restore_p0_ready_v14r6':
            raise ValueError('New dispatch requires an explicit independent policy identity')
        self.operating_options = operating_options(config)
        self.policy_id = policy_id

    def decide(self,records):
        if any(FORBIDDEN_OUTCOMES.intersection(record) for record in records):
            raise ValueError('Hidden outcomes are not current v15 observations')
        history = legal_history(records,max_length=len(records)); demands = []
        for observed in history:
            f = observed['features']
            try: demand = np.asarray([[f[f'{g}_{c}_demand_kW'] for c in ('e','h','c')]
                for g in ('P0','P1','P2','P3')],float)
            except KeyError as exc: raise ValueError('Missing current demand observation') from exc
            if not np.isfinite(demand).all() or np.any(demand < 0.):
                raise ValueError('Invalid current demand observation')
            demands.append(demand)
        request = validate_action({'operating_joint':dict(options=copy.deepcopy(DEFAULT_JOINT),
            operating_options=copy.deepcopy(self.operating_options),risk_loads_kW=np.min(demands,axis=0).tolist())})
        return request,dict(policy_id=self.policy_id,strategy_status='operating_current_readiness_request',
            strategy_fallback=False,planning_interface=PLANNING_INTERFACE,future_rollouts_online=0,
            command_is_immediate_execution=False,operating_options=copy.deepcopy(self.operating_options),
            envelope_semantics='registered preparation domain and observed history floor; no future exogenous script')


OperatingDispatchPolicy = OperatingPolicy
