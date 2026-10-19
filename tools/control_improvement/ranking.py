"""Explicit multi-unit targets and research preference, never clinical labels."""
from __future__ import annotations
import numpy as np

TARGET_NAMES=('P0_failure','P0_failure_minutes')+tuple(f'{p}_{c}_ENS_kWh' for p in ('P0','P1','P2','P3') for c in ('e','h','c'))+('P0_max_temperature_C','P1_max_temperature_C','P2_max_temperature_C','secondary_GT_trips','actual_energy_fee_USD')
RESEARCH_RESOLUTION={'schema':'control_improvement.engineering_research_lex.v2','P0_failure':None,
    'P0_failure_minutes':1e-6,'ENS_kWh_each_carrier':0.01,'actual_energy_fee_USD':1e-6,
    'rounding':'nearest quantum, numpy rint ties-to-even','clinical_failure_standard_changed':False}


def fit_demand_scale(rows,horizon=120.):
    if not rows or any(r['role']!='train' for r in rows):raise ValueError('Demand scales must be train-only')
    values=np.asarray([r['loads_kW'] for r in rows],float)
    if values.shape[1:]!=(4,3) or not np.all(np.isfinite(values)) or np.any(values<0):raise ValueError('Invalid training current demand')
    return np.maximum(1.,values.mean(axis=0)*float(horizon)/60.)


def paired_delta(actual,reference):
    if len(actual)!=len(reference):raise ValueError('Paired target dimension mismatch')
    delta=[];mask=[]
    for a,b in zip(actual,reference):
        valid=a is not None and b is not None and np.isfinite(float(a)) and np.isfinite(float(b))
        mask.append(bool(valid));delta.append(float(a)-float(b) if valid else None)
    return delta,mask


def preference_key(y,scale,*,resolution=None):
    a=np.asarray(y,float);scale=np.asarray(scale,float)
    if a.shape!=(len(TARGET_NAMES),) or scale.shape!=(4,3) or not np.all(np.isfinite(a)) or not np.all(np.isfinite(scale)) or np.any(scale<=0):
        raise ValueError('Unavailable research preference target or scale')
    ranked=a.copy()
    if resolution is not None:
        if resolution!=RESEARCH_RESOLUTION:raise ValueError('Unregistered research decision resolution')
        ranked[1]=np.rint(ranked[1]/resolution['P0_failure_minutes'])*resolution['P0_failure_minutes']
        ranked[2:14]=np.rint(ranked[2:14]/resolution['ENS_kWh_each_carrier'])*resolution['ENS_kWh_each_carrier']
        ranked[-1]=np.rint(ranked[-1]/resolution['actual_energy_fee_USD'])*resolution['actual_energy_fee_USD']
    ens=ranked[2:14].reshape(4,3)
    return (float(ranked[0]),float(ranked[1]),*(float(np.sum(ens[p]/scale[p])) for p in range(4)),float(ranked[-1]))


def ranking_metrics(y,prediction,scale,*,reference_index=0,rule_index=0,resolution=None):
    actual=np.asarray(y,float);pred=np.asarray(prediction,float)
    if actual.ndim!=2 or actual.shape!=pred.shape or actual.shape[1]!=len(TARGET_NAMES) or not len(actual):raise ValueError('Complete same-state difference arrays required')
    pk=[preference_key(v,scale,resolution=resolution) for v in pred];ak=[preference_key(v,scale,resolution=resolution) for v in actual]
    best_prediction=min(pk);ties=[i for i,k in enumerate(pk) if k==best_prediction]
    selected=rule_index if rule_index in ties else ties[0]
    actual_best=min(range(len(actual)),key=lambda i:(ak[i],i))
    compared=[]
    for i in range(len(actual)):
        for j in range(i):
            truth=np.sign((ak[i]>ak[j])-(ak[i]<ak[j]));estimate=np.sign((pk[i]>pk[j])-(pk[i]<pk[j]))
            compared.append(bool(truth==estimate))
    legacy=[preference_key(v,scale) for v in actual];legacy_best=min(range(len(actual)),key=lambda i:(legacy[i],i))
    return {'difference_MAE_by_target':np.mean(np.abs(actual-pred),axis=0).tolist(),
        'zero_effect_difference_MAE_by_target':np.mean(np.abs(actual),axis=0).tolist(),
        'selected_action_index':selected,'reference_action_index':reference_index,'rule_action_index':rule_index,
        'actual_best_action_index':actual_best,'predicted_tie_count':len(ties),
        'pairwise_order_accuracy':float(np.mean(compared)) if compared else None,
        'selected_regret_by_target':(actual[selected]-actual[actual_best]).tolist(),
        'rule_regret_by_target':(actual[rule_index]-actual[actual_best]).tolist(),
        'reference_regret_by_target':(actual[reference_index]-actual[actual_best]).tolist(),
        'research_preference_resolution':resolution,'legacy_exact_best_action_index':legacy_best,
        'legacy_exact_selected_regret_by_target':(actual[selected]-actual[legacy_best]).tolist(),
        'regret_semantics':'coordinate difference from the research-lex best; individual coordinates may be negative',
        'preference_not_clinical_threshold':True}
