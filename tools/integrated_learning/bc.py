"""Current-command behavior cloning, separate from ex-post risk/consequence.

All six labels are actual generated feasible current command ports in kW.
Unknown commands are counted unavailable rather than filled with zero. The
frozen development evaluation role is never used for fitting or selection.
"""
from __future__ import annotations
import copy
from collections import Counter
import numpy as np
from .policies import legal_history, six_targets, ACTION_NAMES


def sequence_record(history):
    selected=legal_history(history);keys=set(selected[-1]['features'])
    if any(set(r['features'])!=keys for r in selected):
        raise ValueError('Changing legal feature schema within history')
    pad=4-len(selected)
    return {'sequence':[{k:None for k in keys} for _ in range(pad)]+
        [copy.deepcopy(r['features']) for r in selected], 'padding_mask':[0]*pad+[1]*len(selected)}


def build_teacher_records(payloads):
    records=[];seen=set();stats=Counter()
    for p in payloads:
        s=p['scenario'];sid=s['id'];ident=p['identity'];policy=ident['policy_id']
        if (sid,policy) in seen:raise ValueError('Duplicate behavior-cloning trajectory')
        seen.add((sid,policy));history=[];last=-float('inf')
        for d in p.get('decisions',[]):
            t=float(d['decision_time_min'])
            if t<=last:raise ValueError('Repeated or reversed teacher decision time')
            last=t;history.append(d);seq=sequence_record(history)
            stats['observed_decisions']+=1;stats['observed_'+s['experiment_role']]+=1
            command=d.get('command_generated')
            if command is None:
                stats['excluded_no_current_teacher']+=1;continue
            if float(command['generated_minute'])!=t:
                raise ValueError('Teacher command is not generated at current observation')
            ports=six_targets(command['dispatch'])
            if not np.all(np.isfinite(ports)) or np.any(ports<0):
                raise ValueError('Unavailable or negative current teacher port')
            if d.get('permitted_command') is not None and d['permitted_command']!=command['dispatch']:
                raise ValueError('Teacher permitted command differs from generated dispatch')
            row=dict(seq,row_id=f'{sid}__{policy}__{t:g}',scenario_id=sid,policy_id=policy,
                root_family_id=s.get('root_family_id',s.get('family_id',sid)),group_id=ident['group_id'],
                role=s['experiment_role'],decision_time_min=t,targets_kW=ports.tolist())
            records.append(row);stats['usable_'+s['experiment_role']]+=1
    if len({r['row_id'] for r in records})!=len(records):raise ValueError('Duplicate teacher row ID')
    return records,dict(stats)


def split_teacher_records(records):
    roles=('pilot_train','pilot_validation','development_boundary')
    if any(r['role'] not in roles for r in records):raise ValueError('Unregistered BC role')
    groups={};families={}
    for r in records:
        for lookup,key in ((groups,r['group_id']),(families,r['root_family_id'])):
            if key in lookup and lookup[key]!=r['role']:
                raise ValueError('BC group or root family crosses training/validation/evaluation')
            lookup[key]=r['role']
    return tuple([r for r in records if r['role']==role] for role in roles)


def cap_training_rows(records,limit=6000):
    if limit<1:raise ValueError('Positive fixed training row cap required')
    if len(records)<=limit:return copy.deepcopy(records)
    units={}
    for r in records:units.setdefault((r['scenario_id'],r['policy_id']),[]).append(r)
    for rows in units.values():rows.sort(key=lambda r:r['decision_time_min'])
    counts={k:0 for k in sorted(units)};left=limit
    while left:
        for key in counts:
            if counts[key]<len(units[key]):counts[key]+=1;left-=1
            if not left:break
    result=[]
    for key,n in counts.items():
        if not n:continue
        indices=np.linspace(0,len(units[key])-1,n,dtype=int)
        result.extend(copy.deepcopy(units[key][i]) for i in indices)
    return result


def port_metrics(target,pred,scale):
    target=np.asarray(target,float);pred=np.asarray(pred,float);scale=np.asarray(scale,float)
    if target.shape!=pred.shape or target.ndim!=2 or target.shape[1]!=6:
        raise ValueError('Six-port targets/predictions shape mismatch')
    if not len(target) or not np.all(np.isfinite(pred)) or np.any(scale<1):
        raise ValueError('Nonempty finite metrics and preregistered training scale required')
    error=pred-target
    return {'N':len(target),'ports':list(ACTION_NAMES),'units':'kW',
        'MAE_kW':np.mean(np.abs(error),axis=0).tolist(),
        'RMSE_kW':np.sqrt(np.mean(error**2,axis=0)).tolist(),
        'normalized_MAE_by_port':np.mean(np.abs(error)/scale,axis=0).tolist(),
        'normalized_six_port_MAE':float(np.mean(np.abs(error)/scale)),
        'training_scale_kW':scale.tolist(),'negative_prediction_count':int(np.sum(pred<0)),
        'max_absolute_error_kW':np.max(np.abs(error),axis=0).tolist()}
