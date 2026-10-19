"""New pre-formal-failure evaluation; original raw ENS/labels remain untouched."""
from __future__ import annotations
from collections import Counter
import copy,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.data_contract import first_failure_label

VERSION='integrated.first_formal_warning.v1'

def causal_feature_view(payload,decision_index,history_minutes=60):
    if history_minutes<0:raise ValueError('Negative history')
    current=payload['decisions'][decision_index];t=float(current['decision_time_min']);history=[]
    for d in payload['decisions'][:decision_index+1]:
        at=float(d['decision_time_min'])
        if at<t-history_minutes-1e-9:continue
        if at>t+1e-9:raise ValueError('Future decision time in history')
        if set(d['features'])!=set(d['feature_metadata']):raise ValueError('Missing feature timing metadata')
        for key,m in d['feature_metadata'].items():
            if not math.isfinite(float(m['available_time_min'])) or not math.isfinite(float(m['valid_time_min'])):raise ValueError('Nonfinite feature information time: '+key)
            if m['available_time_min']>at+1e-9 or m['valid_time_min']>at+1e-9:raise ValueError('Future feature information time: '+key)
            if not m.get('unit') or not m.get('source') or not m.get('measurement'):raise ValueError('Feature provenance missing')
        history.append(dict(decision_time_min=at,features=copy.deepcopy(d['features']),feature_metadata=copy.deepcopy(d['feature_metadata'])))
    return dict(version=VERSION,decision_time_min=t,history_start_min=t-history_minutes,history_end_min=t,
        features=copy.deepcopy(current['features']),feature_metadata=copy.deepcopy(current['feature_metadata']),history=history,
        information_semantics='Past/current declared observations only; idealized P1B internal-state measurements remain research assumptions')

def warning_view(payload,horizon=60,score_interval=(60,420),history_minutes=60):
    if horizon<=0:raise ValueError('Positive warning horizon required')
    status=payload['summary']['status'];segments=payload['segments'];previous=0.;failed=[]
    for s in segments:
        if abs(s['start_minute']-previous)>1e-8 or s['end_minute']<=previous:raise ValueError('Noncontiguous observed intervals')
        previous=float(s['end_minute'])
        if s['classification']['p0_guarantee_failure']:failed.append(s)
    first=float(failed[0]['end_minute']) if failed else None;interval=[float(failed[0]['start_minute']),first] if failed else None
    if abs(previous-payload['summary'].get('observed_until_minute',previous))>1e-8:raise ValueError('Observed summary does not match actual prefix')
    rows=[];counts=Counter()
    for i,d in enumerate(payload['decisions']):
        t=float(d['decision_time_min']);features=causal_feature_view(payload,i,history_minutes)
        past=[s for s in segments if s['end_minute']<=t+1e-9];last=past[-1] if past else None
        raw=None if last is None else [float(x) for x in last['unserved_kW'][0]]
        thermal=bool(last and last.get('violation',{}).get('P0',False));tiny=bool(raw is not None and max(raw)>1e-8 or thermal)
        # At-risk formal-task eligibility is distinct from any raw abnormality.
        eligible=first is None or t<first
        relation='no_formal_failure_observed' if first is None else 'before_entire_first_failure_interval' if t<interval[0] else 'within_first_failure_interval' if t<first else 'at_or_after_first_classification'
        label=first_failure_label(t,float(horizon),first,previous,status);inside=score_interval[0]<=t<score_interval[1]
        row=dict(decision_time_min=t,feature_view=features,label=label,formal_warning_eligible=eligible,
            legacy_any_tiny_abnormal=tiny,prior_raw_P0_unserved_kW=raw,prior_P0_temperature_violation=thermal,
            first_classification_minute=first,first_failure_interval=interval,timing_relation=relation,in_score_interval=inside,
            evaluation_semantics='Before first formal classified guarantee failure; not before every raw physical abnormality',
            ex_post_fields_not_predictors=['label','first_classification_minute','first_failure_interval','timing_relation'])
        rows.append(row)
        if inside:
            counts['decision_windows']+=1;counts['formal_at_risk_windows']+=eligible;counts['legacy_any_tiny_abnormal_windows']+=tiny
            counts['formal_valid_windows']+=label['valid'];counts['formal_positive_windows']+=label['value']==1;counts['formal_complete_negative_windows']+=label['value']==0
            counts['formal_valid_without_legacy_tiny_abnormal_windows']+=label['valid'] and not tiny
            counts['formal_valid_before_entire_first_interval_windows']+=label['valid'] and relation=='before_entire_first_failure_interval'
    return dict(version=VERSION,status=status,observed_until_minute=previous,first_classification_minute=first,first_failure_interval=interval,
        score_interval=list(score_interval),horizon_minutes=float(horizon),counts=dict(counts),rows=rows,
        unchanged_failure_standard=True,raw_ENS_not_zeroed=True,old_label_task_not_modified=True,
        label_semantics='Same frozen classifier and first-observed event in (t,t+H]; new formal prewarning evaluation eligibility only')
