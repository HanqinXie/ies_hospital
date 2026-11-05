"""D0 restoration gate and finite same-future current-action branches.

Candidates are predetermined variations of a feasible CURRENT plan. The full
future script belongs only to the forked simulator and ex-post labels.
"""
from __future__ import annotations
import copy
import math
import numpy as np
from .policies import legal_history,baseline_request,six_targets,ACTION_NAMES

NUMERIC_ABS_TOL=1e-7
TIME_ABS_TOL=1e-10
WALL_CLOCK={'decision_ms','inference_ms','elapsed_seconds','solve_time_seconds'}
DECISION_FIELDS=('decision_time_min','requested_command','permitted_command','command_generated',
                 'arrived_commands','actual_execution','post_outcome','labels','first_failure_observation_interval',
                 'features','feature_metadata','graph')


def _strip(value):
    if isinstance(value,dict):return {k:_strip(v) for k,v in value.items() if k not in WALL_CLOCK}
    if isinstance(value,list):return [_strip(v) for v in value]
    return value


def _differences(a,b,path,differences):
    # RuntimeState.extra_faulted is a set. The frozen legacy exporter writes
    # that known state field as an unsorted JSON list, so process hash order
    # is not physical device order. No other list receives set semantics.
    fault_set_path=path.endswith('.pre_state.extra_faulted') or path.endswith('.post_state.extra_faulted')
    if fault_set_path and isinstance(a,list) and isinstance(b,list):
        valid=lambda values:all(isinstance(v,str) and bool(v) for v in values) and len(values)==len(set(values))
        if not valid(a) or not valid(b) or set(a)!=set(b):
            differences.append({'path':path,'reason':'fault-set members mismatch or invalid/duplicate device IDs',
                'reference':a,'restored':b})
    elif isinstance(a,dict) and isinstance(b,dict):
        if set(a)!=set(b):differences.append({'path':path,'reason':'dictionary key mismatch','reference_keys':sorted(a),'restored_keys':sorted(b)});return
        for k in a:_differences(a[k],b[k],path+'.'+k,differences)
    elif isinstance(a,list) and isinstance(b,list):
        if len(a)!=len(b):differences.append({'path':path,'reason':'list length mismatch','reference_length':len(a),'restored_length':len(b)});return
        for i,(x,y) in enumerate(zip(a,b)):_differences(x,y,f'{path}[{i}]',differences)
    elif isinstance(a,(int,float)) and not isinstance(a,bool) and isinstance(b,(int,float)) and not isinstance(b,bool):
        tol=TIME_ABS_TOL if 'minute' in path else NUMERIC_ABS_TOL
        if not (math.isfinite(float(a)) and math.isfinite(float(b)) and abs(a-b)<=tol):
            if a!=b:differences.append({'path':path,'reference':a,'restored':b,'absolute_tolerance':tol})
    elif a!=b:differences.append({'path':path,'reference':a,'restored':b})


def compare_continuation(reference,restored,start_minute,*,reference_final=None,restored_final=None):
    def select(p):
        return {'segments':[s for s in p['segments'] if s['start_minute']>=start_minute],
            'events':[e for e in p['events'] if e['time_minute']>=start_minute],
            'decisions':[{k:d[k] for k in DECISION_FIELDS if k in d} for d in p['decisions'] if d['decision_time_min']>=start_minute],
            'status':p['summary']['status']}
    differences=[];_differences(_strip(select(reference)),_strip(select(restored)),'trajectory',differences)
    if (reference_final is None)!=(restored_final is None):raise ValueError('Both final execution states are required together')
    if reference_final is not None:
        def state(s):
            document=s.to_dict();core=copy.deepcopy(s.core)
            # Calls retain their raw timing/backend diagnostics in artifacts;
            # physical core fields, events, executed/pending plans are tested.
            core.pop('solver_calls',None)
            from .snapshot import encode
            return {'physical_state':document['physical_state'],'core_state':encode(_strip(core)),
                    'rng':document['rng'],'steps_done':s.dynamic['steps_done'],'terminated':s.dynamic['terminated']}
        _differences(_strip(state(reference_final)),_strip(state(restored_final)),'final_execution',differences)
    return {'passed':not differences,'numeric_absolute_tolerance':NUMERIC_ABS_TOL,
        'time_absolute_tolerance':TIME_ABS_TOL,'excluded_wall_clock_keys':sorted(WALL_CLOCK),
        'unordered_fields':['RuntimeState pre_state.extra_faulted','RuntimeState post_state.extra_faulted'],
        'comparison_scope':'suffix physical segments, all suffix events, command lifecycle, labels and final state/core if supplied',
        'differences':differences}


def current_candidates(history,current_dispatch,baseline='secure_milp'):
    legal_history(history)  # No exogenous script, label or future input.
    base=six_targets(current_dispatch)
    if not np.all(np.isfinite(base)) or np.any(base<0):raise ValueError('Feasible current plan ports required')
    first=baseline_request(baseline)
    lower=base.copy();lower[:3]*=.9;lower[3]+=.1*sum(base[:3])
    cooling=base.copy();cooling[5]*=.9
    result=[{'candidate_id':'original_current_policy','request':first}]
    for name,ports in [('GT_minus10pct_grid_request',lower),('ABS_minus10pct_request',cooling)]:
        request=baseline_request(baseline);request['targets']=dict(zip(ACTION_NAMES,map(float,ports)))
        result.append({'candidate_id':name,'request':request})
    return result


class FirstRequestPolicy:
    def __init__(self,request,baseline,policy_id):
        self.request=copy.deepcopy(request);self.baseline=baseline;self.policy_id=policy_id;self.first=True
        baseline_request(baseline)
    def decide(self,records):
        legal_history(records)
        request=copy.deepcopy(self.request) if self.first else baseline_request(self.baseline)
        self.first=False
        return request,{'policy_id':self.policy_id,'strategy_fallback':False,'model_id':None,
            'strategy_status':'fixed_current_candidate_then_original_continuation'}
