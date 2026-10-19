"""Complete-window accounting and same-prefix finite action continuations."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import traceback
import numpy as np
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.dataset_manifest import digest
from hospital_ies.data_contract import decision_snapshot
from tools.integrated_learning.snapshot import capture_execution,ExecutionSnapshot
from tools.integrated_learning.branches import compare_continuation,FirstRequestPolicy
from tools.integrated_learning.policies import BaselinePolicy
from tools.integrated_learning.truth import window_outcome,save,journal
from .episode import run_policy_episode
from .runtime import extract_current_problem,prepare_current_candidates,project_current,context_provenance
from .runtime import ExecutionAwarePolicy
from .candidates import candidate_numeric_features,choose_current_rule
from .ranking import paired_delta,TARGET_NAMES
from .models import sha

OWN_SOURCE=('__init__.py','candidates.py','ranking.py','models.py','runtime.py','episode.py','branches.py','run.py','policies.py','closed_loop.py')


class FirstCandidatePolicy:
    """One registered current request then a fixed causal continuation recipe."""
    def __init__(self,request,continuation,policy_id):
        self.first=copy.deepcopy(request);self.continuation=continuation;self.policy_id=policy_id
    def bind_current(self,problem):
        if self.first is None and hasattr(self.continuation,'bind_current'):self.continuation.bind_current(problem)
    def decide(self,records):
        if self.first is None:return self.continuation.decide(records)
        request=self.first;self.first=None
        return request,{'model_id':self.policy_id,'strategy_fallback':False,'strategy_status':'registered_first_current_action',
            'projection_policy':'priority_milp','projection_weight':0.,'raw_prediction':None}


def source_hashes():
    folder=Path(__file__).parent
    return {name:sha(folder/name) for name in OWN_SOURCE if (folder/name).exists()}


def compare_control_continuation(reference,restored,start_minute,*,reference_final=None,restored_final=None):
    """Known RuntimeState set fields keep semantics inside typed final core.

    The old comparison already handles raw trajectory pre/post fault lists.
    Typed core encoding obscures those dictionary-key paths. Normalize ONLY
    the copied core.segments[*].pre/post_state.extra_faulted before encoding.
    """
    def canonical_final(snapshot):
        if snapshot is None:return None
        clone=copy.copy(snapshot);clone.core=copy.copy(snapshot.core)
        clone.core['segments']=copy.deepcopy(snapshot.core.get('segments',[]))
        for s in clone.core['segments']:
            for key in ('pre_state','post_state'):
                state=s.get(key,{})
                if 'extra_faulted' not in state:continue
                values=state['extra_faulted']
                if not isinstance(values,list) or not all(isinstance(v,str) and v for v in values) or len(values)!=len(set(values)):
                    raise ValueError('Known fault-set field has invalid or duplicate device members')
                state['extra_faulted']=sorted(values)
        clone.core['errors']=copy.deepcopy(snapshot.core.get('errors',[]))
        for error in clone.core['errors']:
            for layer in error.get('solver_backend_attempts',[]):
                for attempt in layer.get('attempts',[]):
                    # Native finite recovery recomputes the remaining wall
                    # time from elapsed solver time. Preserve declared config
                    # budget, statuses, primal and every other solver option.
                    parameters=attempt.get('parameters',{})
                    parameters.pop('TimeLimit',None)
        return clone
    result=compare_continuation(reference,restored,start_minute,
        reference_final=canonical_final(reference_final),restored_final=canonical_final(restored_final))
    result['additional_unordered_final_core_fields']=['core.segments[*].pre_state.extra_faulted','core.segments[*].post_state.extra_faulted']
    result['additional_excluded_final_core_wall_clock_fields']=['core.errors[*].solver_backend_attempts[*].attempts[*].parameters.TimeLimit']
    result['raw_artifacts_preserved']=True
    return result


def window_targets(payload,start,horizon,env):
    previous=0.
    for s in payload['segments']:
        a=float(s['start_minute']);b=float(s['end_minute'])
        if abs(a-previous)>1e-8 or b<=a:raise ValueError('Outcome requires contiguous original subsegments')
        previous=b
    if abs(float(payload['summary'].get('observed_until_minute',previous))-previous)>1e-8:
        raise ValueError('Outcome observation endpoint differs from original subsegments')
    w=window_outcome(payload,float(start),float(horizon),env);end=start+horizon;duration=0.;temperatures=[]
    for s in payload['segments']:
        overlap=max(0.,min(end,s['end_minute'])-max(start,s['start_minute']))
        if not overlap:continue
        if s['classification']['p0_guarantee_failure']:duration+=overlap
        temperatures.append([float(s['temperature'][p]) for p in ('P0','P1','P2')])
    full=w['full_horizon'];temp=np.max(temperatures,axis=0).tolist() if temperatures else [None]*3
    trips=[e for e in payload['events'] if start<=float(e['time_minute'])<end and
        e.get('cause_class') in ('residual_stress_protection','no_export_isolation','minimum_output_interlock') and
        str(e.get('device','')).startswith('GT_')]
    y=[w['P0_failure_in_window'],float(duration) if full else None]
    y.extend(np.asarray(w['ENS_kWh_by_priority_carrier']).ravel().tolist() if full else [None]*12)
    y.extend(temp if full else [None]*3);y.extend([float(len(trips)) if full else None,w['actual_energy_fee_USD']])
    return dict(w,targets=y,target_names=list(TARGET_NAMES),target_mask=[v is not None for v in y],
        observed_prefix_failure_minutes=float(duration),observed_prefix_max_temperature_C=temp,
        observed_prefix_secondary_GT_trips=len(trips),
        temporal_semantics='Original overlap-integrated subinterval classification; GT events in [start,end); no future labels in policy')


def run_branch_snapshot(env,scenario,seed,manifest,out,*,snapshot_step,baseline_payload,role,group_id,
                        continuation_policy='priority_milp',bounded_horizon=False):
    """Full original restoration first; all eight original requests retained."""
    out=Path(out)
    if out.exists():raise FileExistsError('Every snapshot attempt needs fresh output')
    if role not in ('pilot_train','pilot_validation'):raise ValueError('Only training/development-validation branch labels authorized')
    out.mkdir(parents=True);log=out/'attempts.jsonl';snapshots={};source=source_hashes();horizons=(15,30,60,120)
    save(out/'plan.json',{'scenario_id':scenario['id'],'snapshot_step':snapshot_step,'role':role,'group_id':group_id,
        'source_sha256':source,'manifest_signature':manifest['signature'],'continuation_policy':continuation_policy,
        'conditional_futures':1,'candidate_count':8,'maximum_continuations':9 if bounded_horizon else 8,'full_outcome_horizon':120,
        'D0_continuation_policy':'priority_milp','candidate_observation_horizon_minute':120 if bounded_horizon else None})
    journal(log,event='started',attempt_type='snapshot_capture',scenario_id=scenario['id'])
    reference=run_policy_episode(env,scenario,BaselinePolicy('priority_milp'),seed,manifest,horizons=horizons,
        snapshot_steps=(snapshot_step,),snapshots=snapshots)
    save(out/'reference_capture.json',reference);journal(log,event='finished',attempt_type='snapshot_capture',status=reference['summary']['status'])
    original=compare_continuation(baseline_payload,reference,0.);save(out/'capture_vs_registered.json',original)
    if not original['passed']:raise AssertionError('New policy adapter changed registered original priority trajectory')
    if len(snapshots)!=1:
        result={'passed':False,'truth_integrity_passed':True,'reason':'snapshot_not_reached','capture_attempts':1,'continuation_attempts':0,
            'reference_status':reference['summary']['status'],'capture_vs_registered_passed':True,'fit_rows':[],
            'scenario_id':scenario['id'],'snapshot_minute':snapshot_step*float(scenario['step_minutes']),
            'role':role,'group_id':group_id,'unknown_snapshot':True,'source_sha256':source}
        result['input_artifacts']={name:sha(out/name) for name in ('plan.json','reference_capture.json','capture_vs_registered.json')}
        save(out/'BRANCH_RESULT.json',result);return result
    t,item=next(iter(snapshots.items()));snapshot=item['execution'];snapshot.save(out/'execution_snapshot.json')
    final=capture_execution(env,binding=snapshot.binding)
    restored=ExecutionSnapshot.load(out/'execution_snapshot.json',template=env,expected_binding=snapshot.binding)
    fork=restored.fork();journal(log,event='started',attempt_type='D0_reference',time_minute=t)
    continuation=run_policy_episode(fork,scenario,BaselinePolicy('priority_milp'),seed,manifest,reset=False,
        prefix_decisions=item['prefix_decisions'],horizons=horizons)
    save(out/'priority_reference.json',continuation)
    d0=compare_control_continuation(reference,continuation,t,reference_final=final,restored_final=capture_execution(fork,binding=snapshot.binding))
    save(out/'D0_comparison.json',d0);journal(log,event='finished',attempt_type='D0_reference',status=continuation['summary']['status'],passed=d0['passed'])
    if not d0['passed']:raise AssertionError('Original portable continuation D0 differs')
    current=restored.fork();current.core.boundary();observation=decision_snapshot(current)
    history=copy.deepcopy(item['prefix_decisions'])+[observation];problem=extract_current_problem(current)
    before=capture_execution(current,binding=snapshot.binding).content_sha256
    journal(log,event='started',attempt_type='pure_current_candidate_projection',time_minute=t)
    try:
        context,candidates,queries,groups=prepare_current_candidates(problem,history,
            attempt_callback=lambda **r:journal(log,time_minute=t,**r))
    except Exception as error:
        save(out/'planning_error.json',{'error_type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc(),
            'diagnostic':getattr(error,'current_projection_diagnostic',None),
            'all_current_projection_attempts':getattr(error,'current_projection_attempts',None),'source_sha256':source})
        journal(log,event='finished',attempt_type='pure_current_candidate_projection',status='error');raise
    after=capture_execution(current,binding=snapshot.binding).content_sha256
    if before!=after:raise AssertionError('Current planning queries contaminated actual execution/RNG')
    # Independent actual original generate_command only in a disposable fork.
    rawfork=restored.fork();rawfork.core.boundary();journal(log,event='started',attempt_type='original_current_query_comparison',time_minute=t)
    rawcommand=rawfork.core.generate_command({});difference=[]
    from tools.integrated_learning.branches import _differences
    _differences(rawcommand,candidates[0]['current_command'],'current_command',difference)
    save(out/'current_projection_vs_original.json',{'passed':not difference,'differences':difference,
        'raw_original_command':rawcommand,'pure_current_command':candidates[0]['current_command'],
        'raw_original_solver_calls':copy.deepcopy(rawfork.core.solver_calls[-1:]),
        'live_before_sha256':before,'live_after_sha256':after,'numeric_absolute_tolerance':1e-7})
    journal(log,event='finished',attempt_type='original_current_query_comparison',passed=not difference)
    if difference:raise AssertionError('Restricted current projection differs from original fork command')
    rule=choose_current_rule(history,context,candidates)
    save(out/'CURRENT_CANDIDATES.json',{'context':context,'context_metadata':context_provenance(context),
        'candidates':candidates,'queries':queries,
        'history':history,'rule_candidate_index':rule,'equal_dispatch_groups_descriptive_only':groups,
        'future_read_by_policy':False,'current_query_count':len(queries)+1,'conditional_futures':1})
    outcomes=[] if bounded_horizon else [{'candidate_id':candidates[0]['candidate_id'],'windows':[window_targets(continuation,t,h,fork) for h in (60,120)],
               'trajectory_file':'priority_reference.json','trajectory_sha256':sha(out/'priority_reference.json')}]
    for c in candidates if bounded_horizon else candidates[1:]:
        fork=restored.fork();name=c['candidate_id']
        continuing=ExecutionAwarePolicy() if continuation_policy=='current_rule_projection0' else BaselinePolicy('priority_milp')
        policy=FirstCandidatePolicy(c['request'],continuing,name) if bounded_horizon else FirstRequestPolicy(c['request'],'priority_milp',name)
        journal(log,event='started',attempt_type='current_action_continuation',candidate_id=name,time_minute=t)
        options={'observation_end_minute':t+120.} if bounded_horizon else {}
        payload=run_policy_episode(fork,scenario,policy,seed,manifest,reset=False,prefix_decisions=item['prefix_decisions'],horizons=horizons,**options)
        filename='candidate_'+name+'.json' if bounded_horizon else name+'.json'
        save(out/filename,payload);journal(log,event='finished',attempt_type='current_action_continuation',candidate_id=name,status=payload['summary']['status'])
        outcomes.append({'candidate_id':name,'windows':[window_targets(payload,t,h,fork) for h in (60,120)],
            'trajectory_file':filename,'trajectory_sha256':sha(out/filename),'status':payload['summary']['status']})
    ref=outcomes[0]['windows'][1]['targets'];rows=[]
    for i,(c,o) in enumerate(zip(candidates,outcomes)):
        delta,mask=paired_delta(o['windows'][1]['targets'],ref)
        rows.append({'row_id':scenario['id']+f':t{t:g}:'+c['candidate_id'],'scenario_id':scenario['id'],'snapshot_minute':t,
            'role':'train' if role=='pilot_train' else 'validation','group_id':group_id,'candidate_index':i,'rule_index':rule,
            'x':candidate_numeric_features(history,context,c),'delta_y':delta,'target_mask':mask,
            'loads_kW':context['loads_kW'],'outcome_sha256':o['trajectory_sha256'],'snapshot_sha256':sha(out/'execution_snapshot.json')})
    result={'passed':True,'D0_passed':True,'capture_vs_registered_passed':True,'capture_attempts':1,'continuation_attempts':9 if bounded_horizon else 8,
        'current_planning_queries':9,'scenario_id':scenario['id'],'snapshot_minute':t,'role':role,'group_id':group_id,
        'conditional_futures':1,'outcomes':outcomes,'fit_rows':rows,'all_requests_retained':True,'source_sha256':source,
        'D0_trajectories':1,'candidate_trajectories':8,'continuation_policy':continuation_policy,
        'candidate_observation_horizon_minute':120 if bounded_horizon else None,
        'unknown_outcomes':sum(not all(r['target_mask']) for r in rows)}
    result['input_artifacts']={name:sha(out/name) for name in ('plan.json','reference_capture.json','capture_vs_registered.json',
        'execution_snapshot.json','D0_comparison.json','current_projection_vs_original.json','CURRENT_CANDIDATES.json')}
    if source_hashes()!=source:raise AssertionError('Learning branch source changed during actual continuation')
    save(out/'BRANCH_RESULT.json',result);return result
