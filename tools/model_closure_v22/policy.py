"""M22 ordinary policy arrangements, with actual-current release evidence."""
from __future__ import annotations
import copy
import math
from tools.sci_engineering import policy as legacy

ACTION_SCHEMA=legacy.ACTION_SCHEMA
PLANNING_INTERFACE='model_closure_v22.ordinary_joint_or_rule_current_witness.v1'
validate_action=legacy.validate_action
def policy_config(alias,overrides=None):
    selected=dict(release_after_minutes=240.) if alias=='A' else {}
    selected.update(copy.deepcopy(overrides or {}))
    return legacy.policy_config(alias,selected)
coordination_options=legacy.coordination_options
reserve_reference=legacy.reserve_reference

class SciPolicy(legacy.SciPolicy):
    def __init__(self,alias,config=None,policy_id=None,plant=None):
        super().__init__(alias,policy_config(alias,config),policy_id or alias+'_model_closure_v22',plant)

    def decide(self,records):
        for record in records:
            current=record.get('model_closure_v22')
            if not isinstance(current,dict):raise ValueError('M22 current physical observation required')
            now=record['decision_time_min']
            for key in ('observation_time_minute','available_time_minute'):
                value=current.get(key)
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0.<=value<=now:raise ValueError('Unavailable or future M22 observation')
            witness=current.get('current_non_diesel_feasibility')
            if not isinstance(witness,dict) or type(witness.get('ready')) is not bool:raise ValueError('Explicit current release witness required')
        request,diagnostic=super().decide(records)
        witness=copy.deepcopy(records[-1]['model_closure_v22']['current_non_diesel_feasibility'])
        if 'state_coordinated_joint' in request:
            request['state_coordinated_joint']['coordination']['critical_non_diesel_ready']=witness['ready']
            request=validate_action(request)
            diagnostic['coordination']=copy.deepcopy(request['state_coordinated_joint']['coordination'])
        diagnostic.update(planning_interface=PLANNING_INTERFACE,release_witness=witness,
            mechanism_definition='extra critical reserve preference x preparation objective ordering',
            preparation_false_means='preparation objectives after current general service; preparation remains enabled',
            common_local_optimizer=True,current_witness_is_future_guarantee=False)
        return request,diagnostic

def policy_specs():
    return {alias:dict(policy_id=alias+'_model_closure_v22',kind='model_closure_v22',display_alias=alias,config=policy_config(alias)) for alias in ('R','M','F','C','A')}

def mechanism_specs():
    result=legacy.mechanism_specs()
    for key,spec in result.items():spec.update(kind='model_closure_v22',policy_id=key+'_model_closure_v22',mechanism_definition='extra critical reserve preference x preparation objective ordering')
    return result

def factory(spec,plant=None):
    if not isinstance(spec,dict) or spec.get('kind')!='model_closure_v22':raise ValueError('Explicit M22 policy specification required')
    return SciPolicy(spec.get('display_alias'),spec.get('config'),spec.get('policy_id'),plant)
