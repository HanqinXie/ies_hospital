"""Pure evidence checks for an external post-processing task; no model imports."""
from copy import deepcopy
import json
import math
import re

TIME_EPS = 1e-8  # Frozen report interval-merging arithmetic, not service tolerance.
PROP_METADATA = {'id','physical_scenario_signature','design_variant','matched_group_id',
                 'design_cell_id','v17_matched_propagation_ablation','v18_source_scene_id','v19_source_scene_id'}
TRIP_CAUSES = ('residual_stress_protection','no_export_isolation','minimum_output_interlock')

def require(value, message):
    if not value:raise ValueError(message)

def finite(value, label):
    require(value not in ('',None) and not isinstance(value,bool),'Unknown/bool '+label)
    number=float(value);require(math.isfinite(number),'Nonfinite '+label)
    return number

def is_sha(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None

def saved_equal(cell,value):
    if value is None:return cell in ('',None)
    if isinstance(value,bool):return cell==str(value)
    if isinstance(value,(int,float)):return cell not in ('',None) and float(cell)==value
    if isinstance(value,(dict,list)):return json.loads(cell)==value
    return cell==value

def unique_index(rows,key,expected,label):
    result={}
    for row in rows:
        k=key(row);require(k not in result,'Duplicate '+label+': '+str(k));result[k]=row
    require(set(result)==set(expected),'Wrong '+label+' denominator/identity')
    return result

def config_differences(left,right,path=()):
    if isinstance(left,dict) and isinstance(right,dict):
        return [v for k in sorted(left.keys()|right.keys()) for v in config_differences(left.get(k),right.get(k),path+(k,))]
    return [] if left==right else [(path,left,right)]

def same_facts(left,right,keys,label):
    facts={k:is_sha(left.get(k)) and left[k]==right.get(k) for k in keys}
    require(all(facts.values()),'Actual evidence differs/unknown in '+label+': '+str(facts))
    return facts

def validate_clock_pair(*,left_scene,right_scene,left_config,right_config,variant,left_facts,right_facts,left_policies,right_policies,left_injection,right_injection):
    require(left_scene==right_scene,'Clock pair full scene differs')
    require(left_policies==right_policies,'Clock pair policy panel differs')
    allowed={'open1':(('execution_recovery','grid_open_transfer_minutes'),.25,1.),
             'contact025':(('execution_recovery','local_contact_restore_delay_minutes'),0.,.25)}
    require(variant in allowed,'Unregistered clock variant')
    delta=config_differences(left_config,right_config)
    require(delta==[allowed[variant]],'Clock configuration has wrong/additional changes: '+str(delta))
    facts=same_facts(left_facts,right_facts,('actual_initial_state_sha256','actual_exogenous_sha256','source_binding_sha256','runtime_inputs_sha256'),'clock pair')
    require(is_sha(left_injection) and left_injection==right_injection,'Clock same-policy injection differs/unknown')
    return dict(validated=True,full_scene_equal=True,policy_panel_equal=True,only_config_change=delta,matching=facts,
                same_policy_injection_equal=True,model_config_identity_intentionally_different=True)

def validate_propagation_pair(on,off,on_facts,off_facts):
    require(on.get('enable_propagation') is True and off.get('enable_propagation') is False,'Propagation ON/OFF flags differ from registration')
    require(on.get('propagation_pair_id') and on.get('propagation_pair_id')==off.get('propagation_pair_id'),'Wrong propagation pair')
    left=deepcopy(on);right=deepcopy(off)
    for k in PROP_METADATA|{'enable_propagation'}:left.pop(k,None);right.pop(k,None)
    require(left==right,'Propagation physical/exogenous definition differs beyond flag and registered identity metadata')
    facts=same_facts(on_facts,off_facts,('actual_initial_state_sha256','source_binding_sha256','model_config_sha256','runtime_inputs_sha256'),'propagation pair')
    require(is_sha(on_facts.get('actual_exogenous_sha256')) and is_sha(off_facts.get('actual_exogenous_sha256'))
            and on_facts['actual_exogenous_sha256']!=off_facts['actual_exogenous_sha256'],
            'Flag-bearing applied streams should each bind their distinct registration')
    return dict(validated=True,physical_and_exogenous_projection_equal=True,matching=facts,
                applied_stream_byte_equality_not_required=True,reason='Applied stream hash includes the intended propagation flag')

def merge_runs(runs,bounds):
    lo,hi=map(float,bounds);require(0<=lo<hi,'Invalid window bounds')
    spans=[]
    for run in runs:
        start=finite(run.get('start_minute'),'run start');end=finite(run.get('end_minute'),'run end')
        require(lo<=start<=end<=hi,'Saved interruption escapes requested window')
        if end>start:spans.append([start,end])
    out=[]
    for start,end in sorted(spans):
        if out and start<=out[-1][1]+TIME_EPS:out[-1][1]=max(out[-1][1],end)
        else:out.append([start,end])
    return out

def event_counts(events,bounds):
    fields=('actual_protection_trip','actual_interlock_isolation','actual_device_isolation_events',*TRIP_CAUSES,'unknown_protection_cause')
    if events is None:return {k:None for k in fields}
    lo,hi=map(float,bounds);out={k:0 for k in fields};seen={}
    for event in events:
        kind=event.get('kind')
        interlocks={'no_export_interlock':'no_export_isolation','minimum_output_interlock':'minimum_output_interlock'}
        if kind!='protection_trip' and kind not in interlocks:continue
        t=finite(event.get('time_minute'),'actual trip time')
        if not lo<=t<hi:continue
        identity=event.get('event_id')
        if not identity:return {k:None for k in fields}
        if identity in seen:
            require(seen[identity]==event,'Conflicting duplicate actual trip event')
            continue
        seen[identity]=event;out['actual_device_isolation_events']+=1
        if kind=='protection_trip':
            out['actual_protection_trip']+=1;cause=event.get('cause_class')
        else:
            out['actual_interlock_isolation']+=1;cause=interlocks[kind]
            require(event.get('cause_class') in ('',None,cause),'Interlock kind/cause disagree')
        out[cause if cause in TRIP_CAUSES else 'unknown_protection_cause']+=1
    return out
