"""Exact-source paired rerun and finite engineering evidence for fixed branches.

Normal curves are explicitly frozen historical references. Both fault branches
are newly executed from one full common pre-fault live state, never old U data.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import zipfile

SOURCE=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(SOURCE),str(SOURCE/'src')]
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='1'
from tools.cooling_dynamics_20261104.extract_results import read,write,sha,audit_segments,load_rows
from tools.cooling_dynamics_20261104.run_worker import (build_runtime,record_observation,
    controller_records,physical_state,compact_solver,emit,forensic)
from tools.cooling_dynamics_20261104.audit_campaign import audit_row
from tools.cooling_dynamics_20261104 import default_config
from tools.sci_engineering.runner import source_binding,input_binding
from tools.noopt_unattended_20261104.environment import fork_fixed_unattended,finish_common_prefault_step
from tools.noopt_unattended_20261104.forward import CONTRACT
from tools.noopt_unattended_20261104.runtime import ForbiddenDispatchOptimization

OLD_DATA=Path('E:/hospital_ies_single_unit_20261104_run01/four_stage01/candidate/data/TIER_PLOT_DATA.json')
BUNDLE=Path('D:/云大文件/论文/医院ies/code_4_2/plotting/problems_8')


def utc():return datetime.now(timezone.utc).isoformat()


def pin(path):
    path=Path(path).resolve()
    return dict(path=str(path),sha256=sha(path),bytes=path.stat().st_size)


def check_pin(value):
    path=Path(value['path'])
    if path.stat().st_size!=value['bytes'] or sha(path)!=value['sha256']:
        raise ValueError('Pinned file changed: '+str(path))


def producer_path(value):return Path(value.get('result_path',value.get('path'))).parent


def failure_record(exc,minute):
    return dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc(),
        observed_until_minute=float(minute),complete_trajectory_claimed=False)


def check_u_segment(row,reference,steam_share):
    if (row.get('actual_result_owner')!='arithmetic_forward' or row.get('forward_contract')!=CONTRACT
            or row.get('dispatch_optimizer_calls')!=0):
        raise ValueError('U actual contract/optimizer ownership failed')
    receipt=row['strict_unattended_forward']
    if receipt.get('priority_order_used') is not False:
        raise ValueError('U priority-free allocation receipt missing')
    running=receipt['running_reference']
    if running.get('_fixed_branches')!=reference or running.get('_steam_abs_fraction')!=steam_share:
        raise ValueError('U frozen branch or steam shares changed')


def verify_binding(root):
    binding=read(Path(root)/'SOURCE_BINDING.json')
    if binding['source_root']!=str(SOURCE) or binding['source_files']!=source_binding(SOURCE):
        raise ValueError('Current physical source changed')
    if binding['runtime_inputs']!=input_binding(SOURCE):raise ValueError('Original runtime inputs changed')
    for item in binding['external_files']+binding['execution_tools']:check_pin(item)
    return binding


def prepare(root):
    root=Path(root).resolve()
    if root.exists():raise FileExistsError('Fresh run directory required: '+str(root))
    if root.drive.upper()!='E:':raise ValueError('Use the explicit E evidence workspace')
    root.mkdir();(root/'inputs').mkdir();(root/'logs').mkdir()
    data=read(OLD_DATA);topics=[];seen=set();external={str(OLD_DATA):pin(OLD_DATA)}
    for old in data['topics']:
        topic=old['base_topic_id']
        if topic in seen:continue
        seen.add(topic)
        response=producer_path(old['conditions']['response']['producer'])
        normal=producer_path(old['conditions']['normal']['producer'])
        case=response/'CASE.json'
        if not case.is_file():raise FileNotFoundError(str(case))
        target=root/'inputs'/(topic+'_COMMON_CASE.json');shutil.copy2(case,target)
        for path in [case,target,response/'RESULT.json',normal/'RESULT.json',normal/'SEGMENTS.jsonl',
                normal/'INITIAL_STATE.json',normal/'RESOLVED_CASE.json']:
            external[str(path)]=pin(path)
        case_value=read(case)
        minute_meta=case_value.get('minute1',case_value.get('unit_study',{}).get('base_case',{}).get('minute1',{}))
        if minute_meta.get('variant')!='responsive':raise ValueError('Authenticated R case required')
        normal_result=read(normal/'RESULT.json')
        if normal_result['status']!='completed':raise ValueError('Incomplete frozen normal reference')
        normal_binding=normal_result['source_binding'];external[normal_binding['path']]=pin(normal_binding['path'])
        if external[normal_binding['path']]['sha256']!=normal_binding['sha256']:
            raise ValueError('Normal reference original producer binding changed')
        for item in normal_result['files']:
            if item['path'].endswith(('SEGMENTS.jsonl','INITIAL_STATE.json','RESOLVED_CASE.json','CASE.json')):
                if sha(item['path'])!=item['sha256']:raise ValueError('Normal reference evidence changed')
        topics.append(dict(id=topic,title=old['title'],duration_minutes=old['end_minute'],
            fault_start_minute=60.,fault_end_minute=120.,case=pin(target),
            original_response=pin(response/'RESULT.json'),normal_reference=dict(producer=str(normal),
                result=pin(normal/'RESULT.json'),segments=pin(normal/'SEGMENTS.jsonl'),
                original_source_binding=normal_binding,role='unchanged_frozen_normal_reference_not_new_run'),
            single_unit_adapter='unit_study' in case_value))
    if [x['id'] for x in topics]!=[f'T{i:02d}' for i in range(1,9)]:raise ValueError('Eight ordered topics required')
    # The old display bundle is recoverable before any simulation/overwrite.
    files=sorted(p for folder in (BUNDLE/'power',BUNDLE/'four_stage/power') for p in folder.rglob('*') if p.is_file())
    backup=root/'ORIGINAL_POWER_FIGURES.zip'
    original=[dict(relative=p.relative_to(BUNDLE).as_posix(),**pin(p)) for p in files]
    with zipfile.ZipFile(backup,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for p in files:archive.write(p,p.relative_to(BUNDLE).as_posix())
    with zipfile.ZipFile(backup) as archive:
        if archive.testzip() is not None:raise ValueError('Original backup CRC failed')
        for item in original:
            if hashlib.sha256(archive.read(item['relative'])).hexdigest()!=item['sha256']:
                raise ValueError('Original backup content mismatch')
    write(root/'ORIGINAL_DISPLAY_MANIFEST.json',dict(bundle=str(BUNDLE),files=original,backup=pin(backup)))
    registration=dict(schema='hospital_ies.fixed_branch_rerun.registration.v1',created_utc=utc(),topics=topics,
        cooling_config=default_config(),U_contract=CONTRACT,requested_pair_count=8,new_fault_trajectories=16,
        normal_reference_reused=True,normal_reference_not_new_execution=True,
        max_concurrent_pairs=2,output_estimate_bytes=1500000000,
        authorization='User explicitly requested recomputation and replacement of original plots',
        qualification='Actual engineering ledgers; inherited legacy source-profile and missing-snapshot failures remain disclosed')
    write(root/'REGISTRATION.json',registration)
    tools=[Path(__file__),SOURCE/'release/test_no_priority_rerun_20261105.py',
        SOURCE/'release/no_priority_figures_20261105.py',SOURCE/'release/test_no_priority_figures_20261105.py']
    for p in tools:
        if not p.is_file():raise FileNotFoundError('Complete execution tools before freeze: '+str(p))
    external[str(root/'REGISTRATION.json')]=pin(root/'REGISTRATION.json')
    write(root/'SOURCE_BINDING.json',dict(schema='hospital_ies.fixed_branch_rerun.source_binding.v1',
        source_root=str(SOURCE),source_files=source_binding(SOURCE),runtime_inputs=input_binding(SOURCE),
        execution_tools=[pin(p) for p in tools],external_files=list(external.values()),created_utc=utc()))
    print(json.dumps(dict(status='prepared',root=str(root),backup_files=len(files),backup_bytes=backup.stat().st_size)),flush=True)


class Capture:
    def __init__(self,folder,env,*,prefix=None,initial=None,resolved=None):
        self.folder=Path(folder);self.folder.mkdir()
        self.env=env;self.rows=list(prefix or []);self.cursor=len(env.core.segments);self.events=0;self.errors=0
        self.streams={name:(self.folder/(name+'.jsonl')).open('x',encoding='utf8')
            for name in ('SEGMENTS','STATES','DECISIONS','EVENTS','SOLVER_LOG')}
        for row in self.rows:emit(self.streams['SEGMENTS'],row)
        if initial is not None:write(self.folder/'INITIAL_STATE.json',initial)
        if resolved is not None:write(self.folder/'RESOLVED_CASE.json',resolved)
        self.flush()

    def flush(self):
        core=self.env.core
        for row in core.segments[self.cursor:]:
            value=compact_solver(row);self.rows.append(value);emit(self.streams['SEGMENTS'],value)
        self.cursor=len(core.segments)
        for event in core.events[self.events:]:emit(self.streams['EVENTS'],compact_solver(event))
        self.events=len(core.events)
        for call in core.solver_calls:emit(self.streams['SOLVER_LOG'],compact_solver(call))
        core.solver_calls.clear()
        for error in core.errors[self.errors:]:emit(self.streams['SOLVER_LOG'],dict(kind='actual_core_error',error=compact_solver(error)))
        self.errors=len(core.errors)
        emit(self.streams['STATES'],physical_state(self.env))

    def decision(self,**value):emit(self.streams['DECISIONS'],compact_solver(value))
    def close(self):
        for stream in self.streams.values():stream.close()


def progress(folder,phase,env,end):
    import psutil
    value=dict(phase=phase,minute=float(env.core.time),end_minute=end,
        RSS_bytes=psutil.Process().memory_info().rss,utc=utc())
    temp=Path(folder)/'PROGRESS.tmp';temp.write_text(json.dumps(value),encoding='utf8')
    os.replace(temp,Path(folder)/'PROGRESS.json')


def resources(root,folder):
    import psutil
    if (Path(root)/'STOP.request').exists() or (Path(folder)/'STOP.request').exists():
        raise RuntimeError('Controlled boundary stop requested')
    if psutil.virtual_memory().available<6*1024**3:raise RuntimeError('Available RAM below existing 6GiB boundary')
    if psutil.disk_usage(str(root)).free<30*1024**3:raise RuntimeError('E free space below existing 30GiB boundary')


def execute_step(capture,request,*,finish_common=False):
    try:
        if finish_common:answer=finish_common_prefault_step(capture.env,request)
        else:answer=capture.env.step(request)
    finally:capture.flush()
    if capture.env.core.errors:raise RuntimeError('Actual core errors retained; refusing completed status')
    return answer


def normalized_audit_resolved(resolved):
    # The native physical solver consumes this exact current_context normalizer.
    # Raw observation context omits inactive optional ports; resolve the model's
    # declared defaults instead of inventing coefficients or relaxing checks.
    from tools.model_closure_v22.physical import current_context
    value=deepcopy(resolved)
    value['reset_physical_context']=current_context({'_model_closure_v22':resolved['reset_physical_context']})
    return value


def independent_audit(rows,resolved,end,*,role,reference=None,steam_share=None):
    resolved=normalized_audit_resolved(resolved)
    ledger=audit_segments(rows,end);stocks=dynamic=None;residuals={};count=0
    for row in rows:
        check=audit_row(row,resolved,previous_stocks=stocks,previous_dynamic=dynamic,residuals=residuals)
        stocks,dynamic=check['stocks'],check['dynamic']
        if role=='unattended' and row['start_minute']>=60.-1e-9:
            check_u_segment(row,reference,steam_share);count+=1
    return dict(status='passed',service_ledger=ledger,physical_residuals=residuals,
        final_stocks=stocks,post60_arithmetic_intervals=count,
        scope='Recorded actual energy/inventory/dynamic arithmetic; no OEM, hydraulic, clinical or native primal qualification')


def settle_initial_response_boundary(capture):
    env=capture.env
    if env.minute1_variant!='responsive' or abs(float(env.core.time)-60.)>1e-9:
        raise ValueError('Initial response boundary requires the matched responsive minute60 state')
    # The common checkpoint deliberately precedes this boundary. Native
    # decision_snapshot is read-only and requires the caller to process it.
    try:env.core.boundary()
    finally:capture.flush()


def run_branch(root,folder,env,controller,history,prefix,initial,resolved,topic,role):
    began=time.perf_counter();capture=Capture(folder,env,prefix=prefix,initial=initial,resolved=resolved)
    source_ref=pin(Path(root)/'SOURCE_BINDING.json')
    result=dict(schema='hospital_ies.fixed_branch_rerun.producer.v1',status='failed',role=role,topic=topic['id'],
        source_binding=source_ref,case=topic['case'],expected_end_minute=topic['duration_minutes'],
        native_primal_certificate_reconstructed=False,common_prefix_origin='shared preparation, not U optimization',
        controller_calls_post60=0)
    reference=deepcopy(env.core._noopt_reference) if role=='unattended' else None
    try:
        if role=='response':settle_initial_response_boundary(capture)
        while env.core.time<topic['duration_minutes']:
            resources(root,folder);start=env.core.time
            current=None
            if role=='unattended':request=None;diagnostic=dict(new_dispatch_suppressed=True)
            else:
                current=record_observation(env,history)
                request,diagnostic=controller.decide(controller_records(controller,history))
                result['controller_calls_post60']+=1
            capture.decision(time_minute=start,request=request,diagnostic=diagnostic,role=role,
                observation=current,first_response_boundary_processed_before_observation=role=='response' and start==60.)
            answer=execute_step(capture,request)
            if abs(env.core.time-start-1.)>1e-8:raise ValueError('Native minute clock changed')
            if (answer[2] or answer[3]) and env.core.time!=topic['duration_minutes']:
                raise RuntimeError('Early native termination; unknown tail retained')
            progress(folder,role,env,topic['duration_minutes'])
            if int(env.core.time)%20==0:print(json.dumps(dict(topic=topic['id'],role=role,minute=env.core.time)),flush=True)
        attempts=deepcopy(env.core._noopt_optimizer_attempts)
        if role=='unattended' and attempts:raise ValueError('Unattended optimization was attempted')
        audit=independent_audit(capture.rows,resolved,topic['duration_minutes'],role=role,
            reference=reference['_fixed_branches'] if reference else None,
            steam_share=reference['_steam_abs_fraction'] if reference else None)
        write(Path(folder)/'ENGINEERING_AUDIT.json',audit)
        if role=='unattended' and any(e['time_minute']>=60. and e['kind'] in
                ('automatic_load_shed','automatic_shed_requested') for e in env.core.events):
            raise ValueError('New priority shedding occurred in U')
        verify_binding(root)
        result.update(status='completed',optimizer_entry_attempts=attempts,actual_segment_count=len(capture.rows),
            engineering_audit=pin(Path(folder)/'ENGINEERING_AUDIT.json'))
    except BaseException as exc:
        result['error']=failure_record(exc,env.core.time)
        write(Path(folder)/'FAILURE_CONTEXT.json',forensic(dict(state=env.state,executed=env.core.executed,
            pending_commands=env.core.pending_commands,errors=env.core.errors,
            optimizer_entry_attempts=env.core._noopt_optimizer_attempts)))
    finally:
        capture.close();result.update(observed_until_minute=float(env.core.time),elapsed_seconds=time.perf_counter()-began,completed_utc=utc())
        result['files']=[pin(p) for p in sorted(Path(folder).iterdir()) if p.is_file() and p.name not in ('RESULT.json','PROGRESS.json')]
        write(Path(folder)/'RESULT.json',result)
    return result


def run_pair(root,topic_id,attempt=1):
    root=Path(root);registration=read(root/'REGISTRATION.json');topic=next(x for x in registration['topics'] if x['id']==topic_id)
    folder=root/'runs'/(topic_id+'_attempt'+str(attempt).zfill(2));folder.mkdir(parents=True)
    os.environ['TEMP']=os.environ['TMP']=str(folder)
    began=time.perf_counter();result=dict(status='failed',topic=topic_id,attempt=attempt);capture=None;env=None
    try:
        binding=verify_binding(root);case=read(topic['case']['path'])
        builder=build_runtime
        if topic['single_unit_adapter']:
            from tools.single_unit_20261104.runtime import build_runtime as builder
        env,controller,resolved=builder(case,binding['source_files'],registration['cooling_config'])
        env.reset(scenario=deepcopy(case['scenario']),seed=int(case['scenario'].get('initialization_seed',20261023)))
        initial=physical_state(env)
        resolved.update(storage_state_receipts={n:deepcopy(initial[n]) for n in ('ups','cold_store')},
            station_config=deepcopy(env.core.station.config),
            reset_physical_context=deepcopy(env.core._current_context(env.core._hardware_data_at(0.)[-1])))
        resolved=compact_solver(resolved)
        capture=Capture(folder/'common',env,initial=initial,resolved=resolved);history=[]
        while env.core.time<60.:
            resources(root,folder);current=record_observation(env,history)
            request,diagnostic=controller.decide(controller_records(controller,history))
            capture.decision(time_minute=env.core.time,request=request,diagnostic=diagnostic,
                observation=current,role='common_preparation')
            execute_step(capture,request,finish_common=env.core.time==59.)
            progress(folder,'common_preparation',env,topic['duration_minutes'])
        before=physical_state(env);u=fork_fixed_unattended(env);prefix=list(capture.rows)
        write(folder/'COMMON_CHECKPOINT.json',dict(physical_state=before,U_frozen_reference=u.fixed_branch_checkpoint,
            prefix_segments_sha256=sha(folder/'common/SEGMENTS.jsonl'),prefix_segments=len(prefix),
            common_preparation_optimizer_calls_disclosed=True))
        capture.close();capture=None
        progress(folder,'unattended',u,topic['duration_minutes'])
        u_result=run_branch(root,folder/'U',u,None,None,prefix,initial,resolved,topic,'unattended')
        if physical_state(env)!=before:raise ValueError('U changed the shared R checkpoint')
        result['U']=pin(folder/'U/RESULT.json')
        if u_result['status']!='completed':raise ValueError('U failed; dependent pair is not complete')
        del u
        r_result=run_branch(root,folder/'R',env,controller,history,prefix,initial,resolved,topic,'response')
        result['R']=pin(folder/'R/RESULT.json')
        if r_result['status']!='completed':raise ValueError('Response branch failed')
        verify_binding(root)
        result.update(status='completed',shared_prefault_prefix=True,common_checkpoint_unchanged_after_U=True,
            expected_end_minute=topic['duration_minutes'],source_binding=pin(root/'SOURCE_BINDING.json'))
    except BaseException as exc:
        result['error']=failure_record(exc,env.core.time if env is not None and getattr(env,'core',None) else 0.)
    finally:
        if capture is not None:capture.close()
        result.update(elapsed_seconds=time.perf_counter()-began,completed_utc=utc())
        write(folder/'PAIR_RESULT.json',result)
    print(json.dumps(dict(topic=topic_id,status=result['status'],elapsed_seconds=result['elapsed_seconds'])),flush=True)
    return 0 if result['status']=='completed' else 1


def campaign(root):
    import psutil
    root=Path(root);verify_binding(root);registration=read(root/'REGISTRATION.json')
    queue=[t['id'] for t in registration['topics']];active={};finished={};failed=False
    with (root/'CAMPAIGN_LEDGER.jsonl').open('x',encoding='utf8') as ledger:
        while queue or active:
            while queue and not failed and len(active)<2 and (not active or psutil.virtual_memory().available>12*1024**3):
                topic=queue.pop(0);log=(root/'logs'/(topic+'_attempt01.log')).open('x',encoding='utf8')
                command=[sys.executable,'-B',str(Path(__file__)),'pair','--root',str(root),'--topic',topic]
                process=subprocess.Popen(command,cwd=SOURCE,stdout=log,stderr=subprocess.STDOUT)
                active[topic]=(process,log,command);emit(ledger,dict(event='started',topic=topic,pid=process.pid,command=command,utc=utc()))
            for topic,(process,log,command) in list(active.items()):
                code=process.poll()
                if code is not None:
                    log.close();finished[topic]=code;failed=failed or code!=0
                    emit(ledger,dict(event='finished',topic=topic,exit_code=code,utc=utc()));del active[topic]
            state=dict(status='running',finished=finished,queued=queue,active={t:dict(pid=p.pid,
                progress=read(root/'runs'/(t+'_attempt01')/'PROGRESS.json') if (root/'runs'/(t+'_attempt01')/'PROGRESS.json').exists() else None)
                for t,(p,_,_) in active.items()},available_RAM_GiB=psutil.virtual_memory().available/1024**3,utc=utc())
            temp=root/'CAMPAIGN_PROGRESS.tmp';temp.write_text(json.dumps(state,indent=2),encoding='utf8');os.replace(temp,root/'CAMPAIGN_PROGRESS.json')
            if failed and not active:break
            if active:time.sleep(5.)
    result=dict(status='completed' if len(finished)==8 and not failed else 'failed',finished=finished,not_started=queue,utc=utc())
    write(root/'CAMPAIGN_RESULT.json',result);print(json.dumps(result),flush=True)
    return 0 if result['status']=='completed' else 1


def extract(root):
    root=Path(root);verify_binding(root);registration=read(root/'REGISTRATION.json');topics=[]
    if read(root/'CAMPAIGN_RESULT.json')['status']!='completed':raise ValueError('All eight actual pairs must complete')
    for topic in registration['topics']:
        folder=root/'runs'/(topic['id']+'_attempt01');pair=read(folder/'PAIR_RESULT.json')
        if pair['status']!='completed':raise ValueError('Incomplete selected pair')
        conditions={}
        roles={'normal':Path(topic['normal_reference']['producer']), 'unattended':folder/'U','response':folder/'R'}
        for role,path in roles.items():
            result=read(path/'RESULT.json')
            if result['status']!='completed':raise ValueError('Incomplete source producer')
            rows=load_rows(path/'SEGMENTS.jsonl');audit_segments(rows,topic['duration_minutes'])
            conditions[role]=dict(source=dict(role='unchanged_frozen_normal_reference' if role=='normal' else 'new_fixed_branch_pair_execution',
                segments=pin(path/'SEGMENTS.jsonl'),result=pin(path/'RESULT.json'),
                source_binding=result['source_binding']),segments=[dict(start_minute=r['start_minute'],end_minute=r['end_minute'],
                    demand_kW=r['service_request_ledger']['original_demand_kW'],served_kW=r['service_request_ledger']['delivered_kW']) for r in rows])
        topics.append(dict(id=topic['id'],title=topic['title'],start_minute=0.,end_minute=topic['duration_minutes'],
            fault_start_minute=60.,fault_end_minute=120.,conditions=conditions))
    output=root/'data';output.mkdir()
    write(output/'PLOT_DATA.json',dict(schema='hospital_ies.fixed_branch_rerun.plot_data.v1',topics=topics,
        source_binding=pin(root/'SOURCE_BINDING.json'),normal_reference_reused=True,all_fault_U_R_newly_executed=True,
        physical_qualification=False,native_primal_certificate_reconstructed=False))
    print(str(output/'PLOT_DATA.json'),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('prepare','pair','run','extract'))
    parser.add_argument('--root',required=True,type=Path);parser.add_argument('--topic');parser.add_argument('--attempt',type=int,default=1)
    args=parser.parse_args()
    if args.command=='prepare':prepare(args.root);return 0
    if args.command=='pair':return run_pair(args.root,args.topic,args.attempt)
    if args.command=='run':return campaign(args.root)
    extract(args.root);return 0


if __name__=='__main__':raise SystemExit(main())
