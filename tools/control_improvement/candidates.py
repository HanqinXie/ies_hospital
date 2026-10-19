"""Finite current six-target requests shared by rule and learned ranking."""
from __future__ import annotations
import copy
import numpy as np
from tools.integrated_learning.policies import ACTION_NAMES,legal_history,DEVICES

CONTEXT_KEYS={'time_minute','arrival_minute','delayed','execution_basis_targets_kW','current_plan_targets_kW',
    'capacity_GT_kW','availability_HRSG','overload_minutes_GT','capacity_ABS_kW','grid_import_limit_kW','loads_kW'}
OPTIONAL_CONTEXT_KEYS={'pending_command_ledger'}
from hospital_ies.resilience import ZONE_PRIORITY
HISTORY_KEYS=('time_minute','grid_outage_elapsed_minutes','outdoor_temperature_C','grid_import_limit_kW',
    'arrived_grid_import_limit_kW','grid_import_prev_kW','diesel_prev_kW','diesel_energy_kWh','control_available')
HISTORY_KEYS=HISTORY_KEYS+tuple(f'pre_GT_{i}_kW' for i in (1,2,3))+tuple(f'overload_minutes_GT_{i}' for i in (1,2,3))+tuple(
    'availability_'+d for d in DEVICES)+tuple(f'{p}_{c}_{kind}_kW' for p in ('P0','P1','P2','P3') for c in ('e','h','c') for kind in ('demand','disconnected'))
HISTORY_KEYS=HISTORY_KEYS+tuple(prefix+z for prefix in ('zone_delta_','zone_margin_') for z in sorted(ZONE_PRIORITY))


def validate_context(history,context):
    legal=legal_history(history)
    if not CONTEXT_KEYS<=set(context) or set(context)-CONTEXT_KEYS-OPTIONAL_CONTEXT_KEYS:raise ValueError('Unregistered or incomplete current context')
    t=float(legal[-1]['decision_time_min'])
    if float(context['time_minute'])!=t or float(context['arrival_minute'])<t:
        raise ValueError('Invalid current context clock')
    if not context['delayed'] and float(context['arrival_minute'])!=t:
        raise ValueError('Undelayed context cannot expose future release')
    sizes={'execution_basis_targets_kW':(6,),'current_plan_targets_kW':(6,),'capacity_GT_kW':(3,),
           'availability_HRSG':(3,),'overload_minutes_GT':(3,),'loads_kW':(4,3)}
    for key,shape in sizes.items():
        a=np.asarray(context[key],float)
        if a.shape!=shape or not np.all(np.isfinite(a)) or np.any(a<0):raise ValueError('Invalid current context '+key)
    if any(not np.isfinite(float(context[k])) or float(context[k])<0 for k in ('capacity_ABS_kW','grid_import_limit_kW')):
        raise ValueError('Invalid current context capacity')
    ledger=context.get('pending_command_ledger')
    if ledger is not None:
        if set(ledger)!={'available','generated_minute','planned_arrival_minute','planned_targets_kW','requested_targets_kW','request_mask'}:
            raise ValueError('Unregistered pending generated command ledger')
        if ledger['available']:
            generated=float(ledger['generated_minute']);arrival=float(ledger['planned_arrival_minute'])
            if not np.isfinite(generated) or not np.isfinite(arrival) or generated>t or generated<0 or arrival<t:
                raise ValueError('Pending generated/arrival command clock unavailable')
            for key in ('planned_targets_kW','requested_targets_kW'):
                a=np.asarray(ledger[key],float)
                if a.shape!=(6,) or not np.all(np.isfinite(a)) or np.any(a<0):raise ValueError('Invalid pending generated targets')
            if len(ledger['request_mask'])!=6 or any(type(v) is not bool for v in ledger['request_mask']):raise ValueError('Invalid pending request mask')
        elif any(ledger[k] is not None for k in ('generated_minute','planned_arrival_minute','planned_targets_kW','requested_targets_kW','request_mask')):
            raise ValueError('Absent pending command must remain null, not fabricated zeros')
    return legal


def request(ports):
    v=np.asarray(ports,float)
    if v.shape!=(6,) or not np.all(np.isfinite(v)) or np.any(v<0):raise ValueError('Invalid six current targets')
    return {'targets':dict(zip(ACTION_NAMES,map(float,v)))}


def _allocate(total,capacity,weights):
    """Request allocation only; original projection enforces min/ramp/physics."""
    cap=np.asarray(capacity,float);w=np.maximum(0.,np.asarray(weights,float))
    result=np.zeros(3);remaining=min(float(total),float(cap.sum()))
    for _ in range(3):
        room=np.maximum(0.,cap-result);valid=room>1e-12;use=np.where(valid,w,0.)
        if use.sum()<=0:use=room
        if use.sum()<=0:break
        addition=np.minimum(room,remaining*use/use.sum());result+=addition;remaining-=float(addition.sum())
        if remaining<=1e-12:break
    return result


def current_candidates(history,context):
    validate_context(history,context)
    actual=np.asarray(context['execution_basis_targets_kW'],float);base=np.asarray(context['current_plan_targets_kW'],float)
    cap=np.asarray(context['capacity_GT_kW'],float);hrsg=np.asarray(context['availability_HRSG'],float)
    balanced=base.copy();balanced[:3]=_allocate(base[:3].sum(),cap,cap)
    health=base.copy();health[:3]=_allocate(base[:3].sum(),cap,cap*hrsg)
    grid=base.copy();shift=max(0.,min(float(context['grid_import_limit_kW'])-base[3],base[:3].sum()))
    grid[3]+=shift;grid[:3]=_allocate(max(0.,base[:3].sum()-shift),cap,np.maximum(actual[:3],1.)*hrsg)
    lower=base.copy();lower[5]*=.5
    higher=base.copy();higher[5]=float(context['capacity_ABS_kW'])
    ledger=context.get('pending_command_ledger');pending=ledger['planned_targets_kW'] if ledger and ledger['available'] else base
    pending_name='latest_generated_pending_targets' if ledger is not None else 'current_plan'
    values=[('priority_reference',None),('hold_execution_basis',actual),('current_plan',base),
        ('GT_capacity_balanced',balanced),('GT_HRSG_health',health),('grid_replace_GT',grid),
        ('ABS_half',lower),('ABS_capacity',higher)]
    values[2]=(pending_name,pending)
    return [{'candidate_id':name,'request':{} if v is None else request(v)} for name,v in values]


def choose_current_rule(history,context,candidates):
    validate_context(history,context)
    cap=np.asarray(context['capacity_GT_kW'],float);hrsg=np.asarray(context['availability_HRSG'],float)
    elapsed=np.asarray(context['overload_minutes_GT'],float);actual=np.asarray(context['execution_basis_targets_kW'],float)
    def key(i):
        c=candidates[i]
        v=np.asarray([c['request']['targets'][k] for k in ACTION_NAMES],float) if c['request'].get('targets') else np.asarray(context['current_plan_targets_kW'],float)
        loading=np.divide(v[:3],cap,out=np.zeros(3),where=cap>0)
        stress=float(np.max(loading*(1.+elapsed/15.)))
        weak_heat=float(np.sum(v[:3]*(1.-hrsg))/max(1.,cap.sum()))
        switching=float(np.sum(np.abs(v[:3]-actual[:3]))/max(1.,cap.sum()))
        return stress,weak_heat,switching,i
    # Score projected dispatch where available, while retain original request.
    enriched=copy.deepcopy(candidates)
    for c in enriched:
        if c.get('projected_targets_kW') is not None:
            c['request']=request(c['projected_targets_kW'])
    original=candidates;candidates=enriched
    return min(range(len(original)),key=key)


def candidate_numeric_features(history,context,candidate):
    """No IDs/labels/future script; separate requested and projected ports."""
    legal=validate_context(history,context)
    request_values=[float(candidate['request'].get('targets',{}).get(k,0.)) for k in ACTION_NAMES]
    mask=[float(k in candidate['request'].get('targets',{})) for k in ACTION_NAMES]
    projected=candidate.get('projected_targets_kW')
    if projected is None:raise ValueError('Current projected candidate required')
    x=[]
    for key in ('execution_basis_targets_kW','current_plan_targets_kW','capacity_GT_kW','availability_HRSG','overload_minutes_GT','loads_kW'):
        x.extend(np.asarray(context[key],float).ravel().tolist())
    x.extend([float(context['capacity_ABS_kW']),float(context['grid_import_limit_kW']),
        float(context['arrival_minute'])-float(context['time_minute']),float(context['delayed'])])
    x.extend(request_values);x.extend(mask);x.extend(map(float,projected))
    ledger=context.get('pending_command_ledger')
    if ledger is not None:
        if ledger['available']:
            x.extend([1.,float(context['time_minute'])-ledger['generated_minute'],ledger['planned_arrival_minute']-float(context['time_minute'])])
            x.extend(ledger['planned_targets_kW']);x.extend(ledger['requested_targets_kW']);x.extend(map(float,ledger['request_mask']))
        else:x.extend([0.]*21) # Explicit absent-ledger mask0; raw fields null.
    # Padding zeros are explicitly accompanied by mask0, and only represent
    # absent HISTORY frames. Missing values in a present frame are rejected.
    frames=[]
    for record in legal:
        f=record['features']
        if any(k not in f for k in HISTORY_KEYS):raise ValueError('Present history frame has missing numeric observation')
        values=np.asarray([f[k] for k in HISTORY_KEYS],float)
        if not np.all(np.isfinite(values)):raise ValueError('Present history frame has missing/nonfinite numeric observation')
        frames.append([1.,*values.tolist()])
    frames=[[0.]*(len(HISTORY_KEYS)+1)]*(4-len(frames))+frames
    x.extend(np.asarray(frames).ravel().tolist())
    if not np.all(np.isfinite(x)):raise ValueError('Nonfinite current candidate features')
    return x
