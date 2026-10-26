"""Finite prefix capture and ordinary-command continuation on the public M25 step."""
from __future__ import annotations
import copy,time,traceback
from dataclasses import dataclass,asdict,is_dataclass
import numpy as np
from tools.model_correctness_v23.policy import validate_action
from tools.model_correctness_v23.export import json_clean
from .objective_snapshot import capture_execution,_decode,validate_legal_history

@dataclass
class ExperimentResult:
    env:object
    payload:dict
    snapshot:object=None
    legal_history:list=None

def _boundary_observations(env):
    env.core.boundary()
    fn=env.episode_snapshot_function
    outer=fn(env)
    # Original exporter reads before, then its PolicyEnvironment takes its
    # own second legal snapshot before decide. Preserve that call ordering.
    env.core.observe()
    current=fn(env)
    return outer,current

def _take_step(env,controller,history,current,outer,exports,diagnostics):
    validate_legal_history(history,current,float(env.core.time))
    history.append(copy.deepcopy(current))
    started=time.perf_counter();request,diagnostic=controller.decide(copy.deepcopy(history));validate_action(request)
    diagnostic=dict(diagnostic,decision_time_min=float(env.core.time),requested_command=copy.deepcopy(request),inference_ms=1000*(time.perf_counter()-started))
    record=copy.deepcopy(outer);record.update(requested_command=copy.deepcopy(request),permitted_command=None,command_generated=None,arrived_commands=[],actual_execution=None,post_outcome=None)
    exports.append(record);diagnostics.append(diagnostic)
    _,_,done,truncated,info=env.step(request)
    record['decision_ms']=1000*(time.perf_counter()-started)
    command=next(e['command'] for e in env.event_history if e['kind']=='command_generated' and e['command']['id']==info['command_id'])
    if command['request']!=request:raise AssertionError('Generated command differs from actual controller request')
    record.update(command_generated=copy.deepcopy(command),permitted_command=copy.deepcopy(command['dispatch']),actual_execution=copy.deepcopy(info['executed_action']),
        arrived_commands=[copy.deepcopy(e) for e in env.event_history if e['kind'] in ('command_arrived','command_executed','command_superseded') and float(current['decision_time_min'])<=e['time_minute']<env.core.time],
        post_outcome=dict(end_minute=env.core.time,p0_guarantee_failure=info['p0_guarantee_failure'],unserved_kW=info['unserved_kW'],cost_USD_step=info['cost_USD_step'],secondary_faults=sorted(env.state.extra_faulted) if hasattr(env.state,'extra_faulted') else []),policy_diagnostic=copy.deepcopy(diagnostic))
    return done or truncated

def native_payload(env,controller,manifest,*,seed,initial,decisions,diagnostics,status,error=None,start_minute=0.):
    """Reuse original identities/events/labels; labels are attached only to copies."""
    from hospital_ies.dataset_export import _identities,observed_events,attach_labels
    from tools.model_closure_v22.environment import observed_model_events
    scene=copy.deepcopy(env.scenario);identity=_identities(scene,controller.policy_id,manifest)
    segments=copy.deepcopy(env.core.segments);observed=float(segments[-1]['end_minute']) if segments else 0.
    complete=status=='completed' and abs(observed-scene['duration_steps']*scene.get('step_minutes',15.))<=1e-8
    rows=copy.deepcopy(env.history);decisions=copy.deepcopy(decisions)
    for row in decisions:row.update(identity)
    first,interval=attach_labels(decisions,segments,'completed' if complete else 'solver_error',observed)
    events=observed_model_events(scene,observed,has_segments=bool(segments),events=observed_events(env,scene,observed),research_stress_proxy=getattr(env,'closure_config',{}).get('research_stress_proxy',False))
    energy=sum((np.asarray(s['unserved_kW'])*(s['end_minute']-s['start_minute'])/60 for s in segments),np.zeros((4,3)))
    summary=dict(**identity,episode_family=scene['id'],policy=controller.policy_id,stage=scene['stage'],event_class=scene['event_class'],design_family=scene['design_family'],sampling_cohort=scene['sampling_cohort'],
        status=status,attempted=True,completed=complete,steps=len(rows),physical_service_failure=first is not None,p0_failed_any=1 if first is not None else 0 if complete else None,
        first_failure_observed_minute=first,first_failure_observation_interval=interval,observed_until_minute=observed,requested_end_minute=scene['duration_steps']*scene.get('step_minutes',15.),
        outcome_unknown_after_minute=None if complete else observed,full_trajectory_outcome_valid=complete,cost_USD_observed=sum(s['cost_USD_step'] for s in segments),
        max_balance_residual_kW=max((max(map(abs,s['energy_balance_residuals'])) for s in segments),default=None),max_steam_residual_lbph=max((abs(s['steam_balance_residual_lbph']) for s in segments),default=None),
        strategy_fallback_steps=sum(d.get('strategy_fallback',False) for d in diagnostics),projection_fallback_steps=0,
        ENS_semantics='Actual integrated prefix, never zero-filled missing tail',continuation_semantics='No reset; full original scenario and observed prefix retained')
    for i in range(4):
        for j,carrier in enumerate('ehc'):summary[f'P{i}_{carrier}_ENS_kWh']=float(energy[i,j])
    return json_clean(dict(identity=identity,scenario=scene,seed=seed,initial_state=copy.deepcopy(initial),summary=summary,rows=rows,decisions=decisions,zones=copy.deepcopy(env.zone_history),events=events,segments=segments,
        solver_calls=list(env.core.solver_calls),solver_errors=list(env.core.errors),error=error,policy_diagnostics=copy.deepcopy(diagnostics),continuation_start_minute=start_minute,
        execution_model_id=getattr(env,'model_id',None),model_config=copy.deepcopy(getattr(env,'operating_model_config',{})),operating_execution_contract=copy.deepcopy(getattr(env,'operating_execution_contract',{})),
        input_access_receipt=copy.deepcopy(getattr(env,'input_access_receipt',None)),m21_initial_storage={k:getattr(env.core,k).initial_charged_input_kWh for k in ('ups','cold_store')} if hasattr(env.core,'ups') else None))

def _error(exc):return dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc(),underlying_category=getattr(exc,'category','experiment_exception'))

def capture_prefix(env,scenario,controller,seed,*,stop_minute,binding,manifest):
    requested=float(scenario['duration_steps'])*float(scenario.get('step_minutes',15.))
    if not 0<=stop_minute<requested or stop_minute%float(scenario.get('step_minutes',15.))!=0:raise ValueError('Capture point must be an existing nonterminal controller boundary')
    history=[];exports=[];diagnostics=[];initial=None;snapshot=None;error=None;status='failed_unknown';codec_preflight=None;first_boundary=True
    try:
        env.reset(scenario=scenario,seed=seed)
        initial=dict(state=copy.deepcopy(asdict(env.state) if is_dataclass(env.state) else vars(env.state)),executed=copy.deepcopy(env.core.executed))
        while True:
            outer,current=_boundary_observations(env)
            if first_boundary and env.core.time<stop_minute:
                codec_preflight=dict(time_minute=float(env.core.time),status='failed_unknown',extra_reset_step_or_observe_calls=0)
                # Read/encode the same current state; no reset, step,
                # observation, RNG restoration or future input is added.
                capture_execution(env,binding=binding,legal_history=history,current_observation=current).content_sha256
                codec_preflight['status']='passed'
            first_boundary=False
            if env.core.time==stop_minute:
                snapshot=capture_execution(env,binding=binding,legal_history=history,current_observation=current,
                    export_prefix=dict(initial=initial,decisions=exports,diagnostics=diagnostics,seed=seed))
                status='captured_prefix';break
            if env.core.time>stop_minute:raise RuntimeError('Capture boundary was missed')
            if _take_step(env,controller,history,current,outer,exports,diagnostics):raise RuntimeError('Episode ended before registered capture point')
    except Exception as exc:error=_error(exc)
    if getattr(env,'core',None) is None or not getattr(env,'scenario',None):
        payload=dict(scenario=copy.deepcopy(scenario),initial_state=initial,segments=[],decisions=[],rows=[],events=[],solver_errors=copy.deepcopy(getattr(env,'solver_errors',[])),error=error,
            summary=dict(status='failed_unknown',completed=False,full_trajectory_outcome_valid=False,observed_until_minute=0.,requested_end_minute=requested,outcome_unknown_after_minute=0.))
    else:payload=native_payload(env,controller,manifest,seed=seed,initial=initial,decisions=exports,diagnostics=diagnostics,status=status,error=error)
    payload['snapshot_codec_preflight']=codec_preflight
    return ExperimentResult(env,payload,snapshot,history)

def continue_from_snapshot(snapshot,controller,*,end_minute,manifest,io_resources=None):
    env=snapshot.fork(io_resources=io_resources);history=snapshot.legal_history;prefix=_decode(snapshot.document['export_prefix'])
    initial=prefix.get('initial');exports=prefix.get('decisions',[]);diagnostics=prefix.get('diagnostics',[]);seed=prefix.get('seed');status='failed_unknown';error=None;first=True
    requested=float(env.scenario['duration_steps'])*float(env.scenario.get('step_minutes',15.))
    if not snapshot.minute<end_minute<=requested:raise ValueError('Continuation end outside original scenario')
    try:
        while env.core.time<end_minute-1e-9:
            if first:current=snapshot.current_observation;outer=copy.deepcopy(current);first=False
            else:outer,current=_boundary_observations(env)
            done=_take_step(env,controller,history,current,outer,exports,diagnostics)
            if done:break
        if abs(env.core.time-end_minute)>1e-8:raise RuntimeError('Continuation ended at a different boundary')
        status='completed' if abs(end_minute-requested)<=1e-8 else 'completed_limited_continuation'
    except Exception as exc:error=_error(exc)
    payload=native_payload(env,controller,manifest,seed=seed,initial=initial,decisions=exports,diagnostics=diagnostics,status=status,error=error,start_minute=snapshot.minute)
    payload['snapshot_evidence_sources']=dict(prefix=copy.deepcopy(snapshot.document['external_resources']),continuation={k:dict(path=str(getattr(v,'path','')),directory=str(getattr(v,'directory',''))) for k,v in (io_resources or {}).items() if k!='actual_segment_observer'},semantics='Historical file-qualified row references unchanged; new writes use independent resources')
    return ExperimentResult(env,payload,None,history)
