"""All five arrangements share the same causal M23 preparation interface."""
from tools.model_closure_v22.policy import (SciPolicy as BasePolicy,validate_action,
    policy_config,coordination_options,reserve_reference,ACTION_SCHEMA)
PLANNING_INTERFACE='model_correctness_v23.queued_conditional_permissions.v1'

class SciPolicy(BasePolicy):
    def __init__(self,alias,config=None,policy_id=None,plant=None):
        super().__init__(alias,config,policy_id or alias+'_model_correctness_v23',plant)
    def decide(self,records):
        request,diagnostic=super().decide(records)
        diagnostic['planning_interface']=PLANNING_INTERFACE
        return request,diagnostic

def policy_specs():
    return {alias:dict(policy_id=alias+'_model_correctness_v23',kind='model_correctness_v23',display_alias=alias,config=policy_config(alias)) for alias in ('R','M','F','C','A')}

def factory(spec,plant=None):
    if not isinstance(spec,dict) or spec.get('kind')!='model_correctness_v23':raise ValueError('Explicit M23 policy specification required')
    return SciPolicy(spec.get('display_alias'),spec.get('config'),spec.get('policy_id'),plant)
