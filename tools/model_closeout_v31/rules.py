"""Pure current-observation permission rules; no environment or event access."""
from __future__ import annotations
import copy,math
from . import METHODS

def finite(value,name,minimum=0.,maximum=None):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<minimum or (maximum is not None and value>maximum):
        raise ValueError('Invalid current '+name)
    return float(value)

def expanded_method_config(base,method):
    from tools.model_correctness_v23.policy import policy_config
    guard=dict(policy_mode='current',reserve_enabled=False,critical_reserve=False,reserve_rule='none',
        critical_preparation_first=False,legacy_C19_order=False,time_energy_preparation=False,
        execution_recovery=True,online_GT_count_order='after_cost',cooling_preparation=False)
    if method not in METHODS:raise ValueError('Unregistered V31 method')
    if not isinstance(base,dict) or any(base.get(k)!=v for k,v in guard.items()) or set(base)!=set(policy_config('M',base)):
        raise ValueError('An explicitly complete frozen M after_cost configuration is required')
    result=copy.deepcopy(base)
    if method=='M_cooling_permission':result['cooling_preparation']=True
    if policy_config('M',result)!=result:raise ValueError('Factory changed registered preparation configuration')
    return result

def observe_boiler_capacity(record,total_rating):
    now=finite(record['decision_time_min'],'decision clock');total=finite(total_rating,'boiler rating')
    if total<=0.:raise ValueError('Positive fixed boiler rating required')
    available={};clocks={}
    for i in (1,2,3):
        name=f'availability_BOILER_{i}'
        try:
            available[name]=finite(record['features'][name],name,maximum=1.)
            metadata=record['feature_metadata'][name]
            clocks[name]={k:finite(metadata[k],'feature clock',maximum=now) for k in ('available_time_min','valid_time_min')}
        except (KeyError,TypeError) as exc:raise ValueError('Missing current boiler feature/clock') from exc
    return dict(schema='hospital_ies.v31.current_boiler_permission_observation.v1',decision_time_minute=now,
        boiler_total_rating_lbph=total,observable_availability=available,feature_clocks=clocks,
        healthy_capacity_lbph=total/3.*sum(available.values()),
        basis='three equal original boiler ratings multiplied by current policy-observable availabilities',
        availability_is_actual_ready=False,future_event_or_repair_information_used=False)

def prepare_steam_permission(dispatch,diagnostic,receipt,now):
    now=finite(now,'generation clock')
    if not receipt or now!=receipt.get('decision_time_minute'):raise ValueError('Preparation observation/generation clock mismatch')
    total=finite(receipt['boiler_total_rating_lbph'],'total boiler rating')
    healthy=finite(receipt['healthy_capacity_lbph'],'healthy capacity',maximum=total)
    original=finite(dispatch['boiler_steam_lbph'],'common boiler permission')
    if original>total:raise ValueError('Common permission exceeds original boiler rating; cannot silently reduce it')
    value=copy.deepcopy(dispatch);trace=copy.deepcopy(diagnostic)
    value['boiler_steam_lbph']=max(original,healthy)
    trace['v31_steam_permission']=dict(receipt,enabled=True,method='M_steam_permission',
        original_planner_permission_lbph=diagnostic.get('m23_preparation',{}).get('original_boiler_target_lbph'),
        common_permission_lbph=original,new_permission_lbph=value['boiler_steam_lbph'],generated_minute=now,
        arrival_minute_source='original command_generated event joined by command id; no future arrival read by this rule',
        conditional_permission_is_actual_supply=False,ordinary_queue_and_actual_start_constraints_unchanged=True)
    return value,trace
