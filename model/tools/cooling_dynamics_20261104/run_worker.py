"""One fresh finite continuous trajectory with small actual engineering logs."""
from __future__ import annotations
import argparse
from copy import deepcopy
from dataclasses import asdict,is_dataclass
from datetime import datetime,timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

SOURCE=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(SOURCE),str(SOURCE/'src')]
for _name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[_name]='1'

from tools.cooling_dynamics_20261104.extract_results import sha,read,write,audit_segments


def clean(value,path=()):
    if is_dataclass(value):return clean(asdict(value),path)
    if isinstance(value,dict):return {str(k):clean(v,path+(str(k),)) for k,v in value.items()}
    if isinstance(value,(tuple,list,set)):return [clean(v,path+(str(i),)) for i,v in enumerate(value)]
    if hasattr(value,'tolist'):return clean(value.tolist(),path)
    if isinstance(value,Path):return str(value)
    if isinstance(value,(str,int,bool)) or value is None:return value
    if isinstance(value,float):
        if not math.isfinite(value):
            # Reproduced legacy metadata sentinels; never an imputed quantity.
            thermal=path[-2:]==('temperature','P3') and value==-math.inf
            diagnostic=any(scope in ('diagnostic','solver_backend_layers','attempts','validation')
                or scope.endswith('_validation') for scope in path[:-1])
            bound=(bool(path) and (path[-1] in ('lower','upper') or path[-1].endswith('residual'))
                and any(scope=='validation' or scope.endswith('_validation') for scope in path[:-1])
                and ('rows' in path or 'worst_rows' in path) and not math.isnan(value))
            if thermal or diagnostic:return dict(nonfinite_sentinel='nan' if math.isnan(value) else 'negative_infinity' if value<0 else 'positive_infinity',
                meaning='unsupported_P3_temperature_no_zone_observations' if thermal else
                    'unbounded_native_constraint_metadata' if bound else 'nonfinite_solver_diagnostic_metadata',
                original_json_pointer='/'+'/'.join(path),original_numeric_type=type(value).__name__,
                not_physical_measurement=True,accepted_as_physical_quantity=False,physical_quantity_imputed=False)
            raise ValueError('Nonfinite actual log quantity at /'+'/'.join(path))
        return value
    if hasattr(value,'isoformat'):return value.isoformat()
    raise TypeError('Unsupported actual log value: '+type(value).__name__)


def emit(stream,value):
    stream.write(json.dumps(clean(value),ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n');stream.flush()


def forensic(value,path=()):
    """Failure evidence only; invalid values remain explicit, never accepted."""
    if is_dataclass(value):return forensic(asdict(value),path)
    if isinstance(value,dict):return {str(k):forensic(v,path+(str(k),)) for k,v in value.items()}
    if isinstance(value,(tuple,list,set)):return [forensic(v,path+(str(i),)) for i,v in enumerate(value)]
    if hasattr(value,'tolist'):return forensic(value.tolist(),path)
    if isinstance(value,float) and not math.isfinite(value):return dict(invalid_nonfinite='NaN' if math.isnan(value) else '+Infinity' if value>0 else '-Infinity',json_pointer='/'+'/'.join(path),accepted_as_physical_quantity=False)
    try:return clean(value,path)
    except TypeError:return dict(unexportable_type=type(value).__name__,repr=repr(value)[:1000],accepted_as_physical_quantity=False)


def compact_solver(value,path=()):
    """Retain statuses, warnings and residual scalars without full primal rows.

    This is an explicitly reduced engineering log. It cannot be relabeled as
    the old lossless native-primal certificate or full domain reconstruction.
    """
    if isinstance(value,(list,tuple)):return [compact_solver(x,path+(str(i),)) for i,x in enumerate(value)]
    if not isinstance(value,dict):return clean(value,path)
    result={}
    for key,item in value.items():
        if key in ('rows','all_rows','raw_x','points','cells','proof_cells','domain_cells','certificate_nodes'):
            shape=[];sample=item
            for _ in range(3):
                if not isinstance(sample,(list,tuple)):break
                shape.append(len(sample))
                if not sample:break
                sample=sample[0]
            result[key+'_engineering_omission']=dict(count=len(item) if hasattr(item,'__len__') else None,
                leading_shape=shape,reconstructable=False,
                omitted_reason='Large native proof/primal arrays are outside this small actual engineering log; no lossless or primal certificate claimed')
        else:result[key]=compact_solver(item,path+(key,))
    return result


def controller_records(controller,history):
    """Avoid only the redundant outer copy for two audited read-only policies.

    All historical records remain visible: the inherited SciPolicy computes
    risk_loads_kW from np.min over the full demand history. Its legal_history
    already copies features/provenance; M22 reads clocks and copies the current
    witness, M23 changes only diagnostics, StudySteamPolicy reads records[-1]
    and writes its private mailbox. Unknown controller types keep the old copy.
    """
    from tools.model_correctness_v23.policy import SciPolicy
    from tools.operating_rules_study.environment import StudySteamPolicy
    return history if type(controller) in (SciPolicy,StudySteamPolicy) else deepcopy(history)


def record_observation(env,history):
    """Preserve native algorithm inputs; serialize only detached log outputs."""
    snapshot=getattr(env,'episode_snapshot_function',None)
    if snapshot is None:
        from tools.minute1_20261103.environment import decision_snapshot
        snapshot=decision_snapshot
    current=snapshot(env)
    history.append(deepcopy(current))
    return current


def physical_state(env):
    core=env.core
    result=dict(time_minute=float(core.time),state=clean(env.state),executed=clean(core.executed),
        pending_commands=compact_solver(core.pending_commands),active_command=compact_solver(core.active_command),
        gt_start_pending=clean(core.gt_start_pending),disconnected_fraction=clean(core.disconnected_fraction),
        automatic_fraction=clean(core.automatic_fraction),grid_bus_state=getattr(core,'grid_bus_state',None))
    for name in ('ups','cold_store','station'):
        value=getattr(core,name,None)
        if value is not None and hasattr(value,'observe'):result[name]=clean(value.observe())
    dynamics=getattr(core,'cooling_dynamics',None)
    if dynamics is not None:
        for method in ('snapshot','observe','as_dict'):
            if hasattr(dynamics,method):result['cooling_dynamics']=clean(getattr(dynamics,method)());break
    return result


def build_runtime(case,source_freeze,cooling_config):
    from tools.annual_readiness.registration import load_registry
    from tools.minute1_20261103.registration import validate_case,worker_registry
    from tools.minute1_20261103.source import MinuteSource
    from tools.minute1_20261103.runtime import MinuteSamplingSpec
    from tools.eight_fault_study.runtime import runtime_configuration
    from tools.model_correction_v25.environment import MODEL_ID as CONSTRUCTOR_MODEL_ID
    from tools.sci_engineering.design import allowed_hours,SEALED_DAYS
    from tools.noopt_unattended_20261104.environment import with_forward_minute1
    trusted=load_registry();case=validate_case(case,trusted);meta=case['minute1'];parent=meta['parent']
    registry=worker_registry(case,trusted);configuration=runtime_configuration(parent,registry)
    minute=MinuteSource(SOURCE,meta['minute_source'])
    adapter=with_forward_minute1(meta['method'],minute_source=minute,variant=meta['variant'],
        cooling_config=cooling_config,sampling_spec=MinuteSamplingSpec(),source_freeze=source_freeze,
        requalification_minutes=meta['parameters']['requalification_minutes'],
        overlay_spec=parent.get('v27_load_overlay'),thermal_profile=parent['v28_thermal_profile'])
    env=adapter.OperatingHospitalIESEnv(SOURCE,allowed_hours=allowed_hours(dict(scenarios=[case['scenario']])),
        sealed_days=SEALED_DAYS,model_id=CONSTRUCTOR_MODEL_ID,model_config=deepcopy(configuration['model_config']),
        operating_rule=deepcopy(configuration['operating_rule']),enable_all_validation_rows=False)
    policy=configuration['policy']
    controller=adapter.controller_factory(policy['alias'],config=policy['config'],plant=env.plant,
        policy_id=meta['method']+'_cooling_dynamics_20261104')
    resolved=dict(model_config=clean(env.operating_model_config),plant=clean(env.plant),
        control_rule=clean(env.control_rule),policy=clean(policy),cooling_config=clean(cooling_config),
        source_profile='new_actual_engineering_ledger_only',native_primal_certificate_reconstructed=False)
    from hospital_ies.model import RT_TO_KW
    resolved['physical_constants']=dict(RT_TO_KW=RT_TO_KW)
    for name in ('sci_config','cascade_config','execution_contract','operating_execution_contract','recovery_config','closure_config'):
        resolved[name]=clean(getattr(env,name,None))
    return env,controller,resolved


def verify_binding(root):
    from tools.sci_engineering.runner import source_binding,input_binding
    binding=read(root/'SOURCE_BINDING.json')
    if binding['source_root']!=str(SOURCE) or binding['source_files']!=source_binding(SOURCE):raise ValueError('Frozen source changed before worker start')
    if binding['runtime_inputs']!=input_binding(SOURCE):raise ValueError('Original runtime inputs changed')
    for row in binding['new_input_files']:
        if sha(row['path'])!=row['sha256']:raise ValueError('New version input changed: '+row['path'])
    return binding


def run(root,label,attempt=1):
    root=Path(root).resolve();registration=read(root/'REGISTRATION.json')
    item=next(p for p in registration['producers'] if p['label']==label)
    if type(attempt) is not int or attempt<1:raise ValueError('Positive fresh attempt required')
    output=root/'runs'/(label+'_attempt'+str(attempt).zfill(2));output.mkdir(parents=True,exist_ok=False);(output/'temp').mkdir()
    os.environ['TEMP']=os.environ['TMP']=str(output/'temp')
    began=time.perf_counter();env=None;failure=None;rows=[];history=[];cursor_event=cursor_error=0;command_count=0;first=0
    source_ref=dict(path=str(root/'SOURCE_BINDING.json'),sha256=sha(root/'SOURCE_BINDING.json'))
    result=dict(schema='hospital_ies.cooling_dynamics.producer.v1',label=label,variant=item['variant'],status='failed',
        source_binding=source_ref,case_sha256=sha(item['case_file']),expected_end_minute=item['duration_minutes'],attempt=attempt,
        observed_until_minute=0.,native_primal_certificate_reconstructed=False,optimizer_threads_per_process=1)
    try:
        binding=verify_binding(root);case=read(item['case_file'])
        if sha(item['case_file'])!=item['case_sha256']:raise ValueError('Registered case changed')
        write(output/'CASE.json',case)
        env,controller,resolved=build_runtime(case,binding['source_files'],registration['cooling_config'])
        result.update(case_id=case['id'],execution_model_id='minute1_cooling_dynamics_fixed_noopt_20261104')
        with (output/'SEGMENTS.jsonl').open('x',encoding='utf8') as segments, \
             (output/'STATES.jsonl').open('x',encoding='utf8') as states, \
             (output/'DECISIONS.jsonl').open('x',encoding='utf8') as decisions, \
             (output/'EVENTS.jsonl').open('x',encoding='utf8') as events, \
             (output/'SOLVER_LOG.jsonl').open('x',encoding='utf8') as solverlog:
            env.reset(scenario=deepcopy(case['scenario']),seed=int(case['scenario'].get('initialization_seed',20261023)))
            initial=physical_state(env)
            resolved['storage_state_receipts']={name:deepcopy(initial[name]) for name in ('ups','cold_store')}
            resolved['station_config']=clean(env.core.station.config)
            resolved['reset_physical_context']=clean(env.core._current_context(env.core._hardware_data_at(env.core.time)[-1]))
            write(output/'RESOLVED_CASE.json',resolved)
            write(output/'INITIAL_STATE.json',initial);emit(states,initial)
            if env.core.time!=0.:raise ValueError('Continuous trajectory did not reset at minute zero')
            for event in env.core.events:emit(events,event)
            cursor_event=len(env.core.events)
            for call in env.core.solver_calls:emit(solverlog,compact_solver(call))
            env.core.solver_calls.clear()
            while env.core.time<float(item['duration_minutes']):
                start=float(env.core.time)
                if (root/'STOP.request').exists() or (output/'STOP.request').exists():raise RuntimeError('Controlled boundary stop requested')
                import psutil
                if psutil.virtual_memory().available<6*1024**3:raise RuntimeError('Controlled boundary stop: available RAM below6GiB')
                if psutil.disk_usage(str(root)).free<30*1024**3:raise RuntimeError('Controlled boundary stop: E free below30GiB')
                current=record_observation(env,history)
                request=diagnostic=None
                if item['variant']=='unattended' and start>=60.:
                    diagnostic=dict(upper_commands_suppressed=True,strict_arithmetic_forward=True)
                else:
                    request,diagnostic=controller.decide(controller_records(controller,history));command_count+=1
                first=len(env.core.segments)
                emit(decisions,compact_solver(dict(time_minute=start,observation=current,requested_action=request,diagnostic=diagnostic)))
                step_error=None
                try:
                    _,_,done,truncated,info=env.step(request)
                except Exception as exc:
                    done=truncated=False;step_error=exc
                for row in env.core.segments[first:]:
                    value=compact_solver(row);rows.append(value);emit(segments,value)
                for event in env.core.events[cursor_event:]:emit(events,compact_solver(event))
                cursor_event=len(env.core.events)
                for call in env.core.solver_calls:emit(solverlog,compact_solver(call))
                env.core.solver_calls.clear()
                errors=list(getattr(env.core,'errors',[])[cursor_error:]);cursor_error+=len(errors)
                for error in errors:emit(solverlog,dict(kind='actual_core_error',error=compact_solver(error)))
                emit(states,physical_state(env))
                result['observed_until_minute']=float(env.core.time)
                result['controller_call_count']=command_count
                progress=dict(label=label,observed_until_minute=result['observed_until_minute'],expected_end_minute=item['duration_minutes'],RSS_bytes=psutil.Process().memory_info().rss,utc=datetime.now(timezone.utc).isoformat())
                temp=output/'PROGRESS.tmp';temp.write_text(json.dumps(progress),encoding='utf8');os.replace(temp,output/'PROGRESS.json')
                if step_error:raise step_error
                if errors:raise RuntimeError('Actual core error retained; refusing to call episode complete')
                if abs(env.core.time-(start+1.))>1e-8:raise ValueError('Actual step did not preserve the one-minute clock')
                if (done or truncated) and env.core.time!=float(item['duration_minutes']):raise RuntimeError('Early environment termination')
            audit=audit_segments(rows,float(item['duration_minutes']));write(output/'ENGINEERING_AUDIT.json',audit)
            attempts=clean(getattr(env.core,'_noopt_optimizer_attempts',[]))
            post=[s for s in rows if s['start_minute']>=60.-1e-9]
            noopt_check=dict(applicable=item['variant']=='unattended',optimizer_entry_attempts=attempts,
                post60_segment_count=len(post),post60_owners=sorted({s.get('actual_result_owner','missing') for s in post}),
                all_post60_segments_arithmetic=bool(post) and all(s.get('forward_contract')=='hospital_ies.fixed_reference_arithmetic.v1' and s.get('dispatch_optimizer_calls')==0 for s in post))
            if item['variant']=='unattended' and (attempts or not noopt_check['all_post60_segments_arithmetic']):raise ValueError('Strict U no-optimization execution evidence failed')
            result.update(status='completed',engineering_audit=dict(path=str(output/'ENGINEERING_AUDIT.json'),sha256=sha(output/'ENGINEERING_AUDIT.json')),
                actual_segment_count=len(rows),controller_call_count=command_count,strict_noopt_check=noopt_check)
        # Verification at closure rejects accidental in-flight source edits.
        verify_binding(root)
    except Exception as exc:
        failure=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc())
        result.update(status='failed',error=failure)
        if env is not None and getattr(env,'core',None) is not None:
            result['observed_until_minute']=float(env.core.time)
            context=dict(status='unvalidated_failure_evidence',state=env.state,executed=env.core.executed,
                current_segments=env.core.segments[first:],pending_commands=env.core.pending_commands,
                unexported_events=env.core.events[cursor_event:],solver_calls=list(env.core.solver_calls),
                core_errors=getattr(env.core,'errors',[]),optimizer_entry_attempts=getattr(env.core,'_noopt_optimizer_attempts',[]))
            write(output/'FAILURE_CONTEXT.json',forensic(context))
        write(output/'FAILURE.json',failure)
    result.update(elapsed_seconds=time.perf_counter()-began,completed_utc=datetime.now(timezone.utc).isoformat())
    result['files']=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(output.iterdir()) if p.is_file() and p.name not in ('PROGRESS.json','PROGRESS.tmp')]
    write(output/'RESULT.json',result)
    print(json.dumps(dict(label=label,status=result['status'],observed_until_minute=result['observed_until_minute'],elapsed_seconds=result['elapsed_seconds']),ensure_ascii=False),flush=True)
    return 0 if result['status']=='completed' else 1


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--label',required=True);parser.add_argument('--attempt',type=int,default=1);args=parser.parse_args()
    return run(args.root,args.label,args.attempt)


if __name__=='__main__':raise SystemExit(main())
