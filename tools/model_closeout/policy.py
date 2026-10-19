"""Current observations produce one ordinary command, with no future script."""
from __future__ import annotations
import copy
import numpy as np

from tools.integrated_learning.policies import legal_history
from tools.engineering_model.policy import FORBIDDEN_OUTCOMES
from tools.operating_response.policy import DEFAULT_JOINT
from .readiness import coordination_options

ACTION_SCHEMA='model_closeout.joint.v19'
PLANNING_INTERFACE='model_closeout.ordinary_joint_command.v19'


def validate_action(action):
    from .planner import _options
    if not isinstance(action,dict) or set(action)!={'state_coordinated_joint'}:
        raise ValueError('V19 requires its versioned state_coordinated_joint route')
    joint=action['state_coordinated_joint']
    if not isinstance(joint,dict) or set(joint)-{'options','coordination','risk_loads_kW'}:
        raise ValueError('Unsupported current coordinated request fields')
    result=copy.deepcopy(action);joint=result['state_coordinated_joint']
    joint['options']=_options(joint.get('options',DEFAULT_JOINT))
    joint['coordination']=coordination_options(joint.get('coordination'))
    if 'risk_loads_kW' in joint:
        demand=np.asarray(joint['risk_loads_kW'],float)
        if demand.shape!=(4,3) or not np.isfinite(demand).all() or np.any(demand<0.):
            raise ValueError('Invalid observed demand floor')
        joint['risk_loads_kW']=demand.tolist()
    return result


class StateCoordinatedPolicy:
    def __init__(self,config=None,policy_id='C_model_closeout_v19'):
        if not isinstance(policy_id,str) or not policy_id.strip() or policy_id in ('E','K1','K2'):
            raise ValueError('C requires its own explicit policy identity')
        self.coordination=coordination_options(config);self.policy_id=policy_id

    def reset_policy_memory(self):
        pass

    def decide(self,records):
        if any(FORBIDDEN_OUTCOMES.intersection(record) for record in records):
            raise ValueError('Hidden outcomes are not current observations')
        history=legal_history(records,max_length=len(records));demands=[]
        for observed in history:
            f=observed['features']
            try:demand=np.asarray([[f[f'{g}_{c}_demand_kW'] for c in ('e','h','c')] for g in ('P0','P1','P2','P3')],float)
            except KeyError as exc:raise ValueError('Missing current carrier demand') from exc
            if not np.isfinite(demand).all() or np.any(demand<0.):raise ValueError('Invalid current demand')
            demands.append(demand)
        request=validate_action({'state_coordinated_joint':dict(options=copy.deepcopy(DEFAULT_JOINT),
            coordination=copy.deepcopy(self.coordination),risk_loads_kW=np.min(demands,axis=0).tolist())})
        return request,dict(policy_id=self.policy_id,strategy_status='execution_recovery_current_request',
            strategy_fallback=False,planning_interface=PLANNING_INTERFACE,future_rollouts_online=0,
            command_is_immediate_execution=False,coordination=copy.deepcopy(self.coordination),
            envelope_semantics='current conditional carrier witnesses; registered response and shaft support; no future script')


CoordinatedPolicy=StateCoordinatedPolicy
