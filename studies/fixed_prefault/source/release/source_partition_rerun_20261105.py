"""U-only source-partition correction with immutable run03 R/N references.

The common 0--60 minute preparation may optimize. Only the new arithmetic U
branch runs after minute60. Historical R/N values and producer metadata remain
exactly those in run03; their archived producer source is verified separately.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import importlib.util
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
for _name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[_name]='1'


def load_module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


base=load_module(SOURCE/'release/no_priority_rerun_20261105.py','source_partition_legacy_worker')
read,write,pin,sha=base.read,base.write,base.pin,base.sha
SCHEMA='hospital_ies.source_partition_fix.plot_data.v1'
EXPECTED_CONTRACT='hospital_ies.fixed_prefault_branches_arithmetic.v3'
PREVIOUS=Path('E:/hospital_ies_fixed_branch_replot_20261105_run03')
BUNDLE=base.BUNDLE
GiB=1024**3
AUTHORIZED_U_MODULES={f'tools/noopt_unattended_20261104/{name}.py'
    for name in ('forward','fixed_branches','environment','runtime')}


def utc():return datetime.now(timezone.utc).isoformat()


def check_pin(item,*,inside=None):
    path=Path(item['path']).resolve(strict=True)
    if inside is not None and not path.is_relative_to(Path(inside).resolve()):
        raise ValueError('Evidence escapes its producer: '+str(path))
    if sha(path)!=item['sha256'] or ('bytes' in item and path.stat().st_size!=item['bytes']):
        raise ValueError('Pinned evidence changed: '+str(path))
    return pin(path)


def same_pin(first,second):
    return (Path(first['path']).resolve()==Path(second['path']).resolve()
        and first['sha256']==second['sha256'])


def admit_source_delta(previous,current):
    removed=sorted(set(previous)-set(current))
    added=sorted(set(current)-set(previous))
    changed=sorted(k for k in set(previous)&set(current) if previous[k]!=current[k])
    unauthorized=[p for p in changed+added if p not in AUTHORIZED_U_MODULES and not p.startswith('tests/')]
    if removed or unauthorized:
        raise ValueError('Source change outside authorized U modules/tests: '+repr(dict(removed=removed,unauthorized=unauthorized)))
    return dict(changed=changed,added=added,removed=removed,
        authorized_physical_modules=sorted(AUTHORIZED_U_MODULES))


def common_physical_comparison(value,path=(),excluded=None):
    """Exclude only recorded CPU wall timers, never physical state timers."""
    excluded=[] if excluded is None else excluded
    if isinstance(value,dict):
        result={}
        for key,item in value.items():
            here=path+(str(key),)
            if ('solver_backend_layers' in path and
                    (key=='capture_seconds' and 'nonlinear_domain_evidence' in path
                     or 'attempts' in path and (key=='elapsed_seconds' or key=='TimeLimit' and 'parameters' in path))):
                excluded.append(dict(path='/'+('/'.join(here)),value=item,
                    reason='Computational solver backend wall time or remaining wall-time budget; not simulated physical time'))
            else:result[key]=common_physical_comparison(item,here,excluded)
        return result
    if isinstance(value,list):
        return [common_physical_comparison(item,path+(str(index),),excluded) for index,item in enumerate(value)]
    return value


def compare_common_origin(current_rows,current_state,old_rows,old_state):
    current_exclusions=[];old_exclusions=[]
    current_prefix=common_physical_comparison(current_rows,('segments',),current_exclusions)
    old_prefix=common_physical_comparison(old_rows,('segments',),old_exclusions)
    current_physical=common_physical_comparison(current_state,('physical_state',),current_exclusions)
    old_physical=common_physical_comparison(old_state,('physical_state',),old_exclusions)
    if [item['path'] for item in current_exclusions]!=[item['path'] for item in old_exclusions]:
        raise ValueError('Common computational timer structure changed')
    if current_prefix!=old_prefix:
        raise ValueError('Recomputed common native prefix differs from the frozen run03 prefix')
    if current_physical!=old_physical:
        raise ValueError('Recomputed complete physical state differs from the frozen run03 physical state')
    return dict(status='passed',native_prefix_exact=True,complete_physical_state_exact=True,
        common_native_intervals=len(current_rows),floating_tolerance=0.,partial_state_restore_used=False,
        equality_scope='All values after explicitly listed computational-wall-timer exclusions; physical timers and command IDs retained',
        excluded_computational_timers=dict(current=current_exclusions,baseline=old_exclusions))


def admit_worker(*,available_bytes,active_count,max_workers,worker_budget_bytes,reserve_bytes,
                 reserved_unconsumed_bytes=0):
    if not 1<=max_workers<=4 or worker_budget_bytes<=0 or reserve_bytes<6*GiB:
        raise ValueError('One to four workers and at least 6GiB RAM reserve are required')
    return (active_count<max_workers and
        available_bytes-reserved_unconsumed_bytes>=reserve_bytes+worker_budget_bytes)


def assert_preserved_conditions(new,old):
    if [t['id'] for t in new['topics']]!=[t['id'] for t in old['topics']]:
        raise ValueError('Frozen topic identities changed')
    for current,original in zip(new['topics'],old['topics']):
        for role in ('normal','response'):
            if current['conditions'][role]!=original['conditions'][role]:
                raise ValueError('Frozen '+role+' values or original producer metadata changed: '+original['id'])
        for key in ('start_minute','end_minute','fault_start_minute','fault_end_minute','title'):
            if current.get(key)!=original.get(key):raise ValueError('Frozen topic metadata changed: '+key)
    return dict(status='passed',normal_conditions_exact=True,response_conditions_exact=True,
        original_producer_metadata_exact=True,normal_or_response_recomputed=False)


def merge_plot_data(old,replacements,source_binding):
    result=deepcopy(old)
    if set(replacements)!={t['id'] for t in old['topics']}:raise ValueError('Exactly eight U replacements required')
    for topic in result['topics']:topic['conditions']['unattended']=deepcopy(replacements[topic['id']])
    result.update(schema=SCHEMA,source_binding=source_binding,only_unattended_new_execution=True,
        normal_reference_reused=True,response_reference_reused=True,response_new_execution=False,
        all_fault_U_R_newly_executed=False,new_fault_trajectories=8,
        physical_qualification=False,native_primal_certificate_reconstructed=False,
        condition_execution_roles=dict(normal='frozen_original_reference',unattended='new_U_only_execution',
            response='frozen_run03_response'),common_preparation_may_use_optimization=True)
    assert_preserved_conditions(result,old)
    return result


def compact_rows(rows):
    return [dict(start_minute=r['start_minute'],end_minute=r['end_minute'],
        demand_kW=r['service_request_ledger']['original_demand_kW'],
        served_kW=r['service_request_ledger']['delivered_kW']) for r in rows]


def verify_condition(condition,*,expected_result=None,cache=None):
    """Check original raw bytes and exact plot extraction without a simulator."""
    source=condition['source'];result_ref=source['result'];folder=Path(result_ref['path']).resolve().parent
    if expected_result is not None and not same_pin(result_ref,expected_result):
        raise ValueError('Frozen reference does not select the accepted run03 producer')
    key=(str(folder),result_ref['sha256'])
    if cache is not None and key in cache:return cache[key]
    result_pin=check_pin(result_ref,inside=folder);result=read(result_pin['path'])
    if result.get('status')!='completed' or not same_pin(result['source_binding'],source['source_binding']):
        raise ValueError('Historical producer completion/source identity mismatch: '+str(folder))
    binding_pin=check_pin(source['source_binding'])
    files=[check_pin(item,inside=folder) for item in result['files']]
    segment_pin=check_pin(source['segments'],inside=folder)
    if str(Path(segment_pin['path'])) not in {item['path'] for item in files}:
        raise ValueError('Historical native segments are absent from original RESULT pins')
    count=0
    with Path(segment_pin['path']).open(encoding='utf8') as stream:
        for line in stream:
            if not line.strip():continue
            row=json.loads(line);expected=condition['segments'][count] if count<len(condition['segments']) else None
            if compact_rows([row])[0]!=expected:raise ValueError('Frozen plot values differ from original native ledger')
            count+=1
    if count!=len(condition['segments']):raise ValueError('Frozen plot/native interval count differs')
    evidence=dict(status='passed',new_execution=False,result=result_pin,segments=segment_pin,
        original_source_binding=binding_pin,files=files,native_segments=count,
        exact_original_plot_extraction=True)
    if cache is not None:cache[key]=evidence
    return evidence


def verify_old_snapshot(previous,binding):
    receipt=read(previous/'CODE_SNAPSHOT_RECEIPT.json')
    snapshot=check_pin(receipt,inside=previous);checked=0
    with zipfile.ZipFile(snapshot['path']) as archive:
        expected={'source/'+relative:digest for relative,digest in binding['source_files'].items()}
        for item in binding['execution_tools']:
            member='execution_tools/'+Path(item['path']).name
            if member in expected:raise ValueError('Duplicate archived execution tool member: '+member)
            expected[member]=item['sha256']
        for member,digest in expected.items():
            if hashlib.sha256(archive.read(member)).hexdigest()!=digest:
                raise ValueError('Archived historical producer source differs: '+member)
            checked+=1
    return dict(status='passed',archive=snapshot,source_files_verified=checked,
        comparison='archived producer bytes against original source binding; current modified code is not old producer code')


def case_dependencies(cases):
    found={}
    def walk(value):
        if isinstance(value,dict):
            if isinstance(value.get('path'),str) and isinstance(value.get('sha256'),str):
                raw=Path(value['path']);actual=(raw if raw.is_absolute() else SOURCE/raw).resolve()
                item=dict(value,path=str(actual));checked=check_pin(item)
                if str(actual) in found and found[str(actual)]['sha256']!=checked['sha256']:
                    raise ValueError('Conflicting external CASE dependency')
                found[str(actual)]=checked
            for item in value.values():walk(item)
        elif isinstance(value,list):
            for item in value:walk(item)
    for case in cases:walk(case)
    return [found[key] for key in sorted(found)]


def execution_tools():
    names=('source_partition_rerun_20261105.py','source_partition_figures_20261105.py',
        'test_source_partition_pipeline_20261105.py','no_priority_rerun_20261105.py',
        'no_priority_figures_20261105.py','test_no_priority_rerun_20261105.py','test_no_priority_figures_20261105.py')
    return [pin(SOURCE/'release'/name) for name in names]


def verify_binding(root):
    root=Path(root);binding=read(root/'SOURCE_BINDING.json')
    if binding['source_root']!=str(SOURCE) or binding['source_files']!=base.source_binding(SOURCE):
        raise ValueError('Current source differs from this new execution freeze')
    if binding['runtime_inputs']!=base.input_binding(SOURCE):raise ValueError('Original runtime inputs changed')
    for item in binding['execution_tools']+binding['external_files']:check_pin(item)
    return binding


def prepare(root,previous=PREVIOUS):
    root=Path(root).resolve();previous=Path(previous).resolve(strict=True)
    if root.exists():raise FileExistsError('Fresh campaign child directory required: '+str(root))
    if root.drive.upper()!='E:':raise ValueError('Explicit E evidence workspace required')
    if base.CONTRACT!=EXPECTED_CONTRACT:raise ValueError('The source-partition v3 correction must pass tests before preparation')
    old_binding=read(previous/'SOURCE_BINDING.json');old_registration=read(previous/'REGISTRATION.json')
    old_data=read(previous/'data/PLOT_DATA.json');acceptance=read(previous/'ACCEPTANCE.json')
    if (acceptance.get('status')!='passed' or acceptance.get('partial') is not False
            or acceptance.get('complete_pair_count')!=8 or acceptance.get('plot_data_verified') is not True
            or acceptance.get('errors')!=[] or not same_pin(old_data['source_binding'],pin(previous/'SOURCE_BINDING.json'))):
        raise ValueError('A complete accepted run03 baseline is required')
    old_snapshot=verify_old_snapshot(previous,old_binding)
    source_delta=admit_source_delta(old_binding['source_files'],base.source_binding(SOURCE))
    if old_binding['runtime_inputs']!=base.input_binding(SOURCE):raise ValueError('Original input bytes/inventory changed')
    if [t['id'] for t in old_data['topics']]!=[f'T{i:02d}' for i in range(1,9)]:raise ValueError('Eight ordered topics required')
    root.mkdir(parents=True);(root/'inputs').mkdir();(root/'logs').mkdir()
    external={};cache={};topics=[];cases=[];references={}
    def add(item):
        checked=check_pin(item);external[checked['path']]=checked
    for name in ('SOURCE_BINDING.json','REGISTRATION.json','ACCEPTANCE.json','CODE_SNAPSHOT_RECEIPT.json',
                 'CODE_SNAPSHOT.zip','PUBLISH_RECEIPT.json','POST_PUBLICATION_VERIFICATION.json','data/PLOT_DATA.json'):
        add(pin(previous/name))
    add(acceptance['acceptance_script'])
    for topic,registered in zip(old_data['topics'],old_registration['topics']):
        if topic['id']!=registered['id']:raise ValueError('Baseline registration order changed')
        topic_id=topic['id'];accepted=acceptance['checked_topics'][topic_id]
        role_evidence={}
        for role in ('normal','response'):
            expected=(accepted['normal_reference']['result'] if role=='normal' else accepted['branches']['R']['result'])
            evidence=verify_condition(topic['conditions'][role],expected_result=expected,cache=cache)
            role_evidence[role]=evidence
            for item in [evidence['result'],evidence['segments'],evidence['original_source_binding'],*evidence['files']]:add(item)
        references[topic_id]=role_evidence
        original=check_pin(registered['case']);add(original)
        destination=root/'inputs'/(topic_id+'_COMMON_CASE.json');shutil.copy2(original['path'],destination)
        case=read(destination);cases.append(case);add(pin(destination))
        common=previous/'runs'/(topic_id+'_attempt01')
        baseline=dict(common_segments=pin(common/'common/SEGMENTS.jsonl'),checkpoint=pin(common/'COMMON_CHECKPOINT.json'),
            initial=pin(common/'common/INITIAL_STATE.json'),resolved=pin(common/'common/RESOLVED_CASE.json'))
        for item in baseline.values():add(item)
        topics.append(dict(id=topic_id,title=topic['title'],duration_minutes=topic['end_minute'],
            fault_start_minute=60.,fault_end_minute=120.,case=pin(destination),original_case=original,
            single_unit_adapter='unit_study' in case,baseline=baseline,
            historical_response=deepcopy(topic['conditions']['response']['source']),
            historical_normal=deepcopy(topic['conditions']['normal']['source'])))
    dependencies=case_dependencies(cases)
    write(root/'EXTERNAL_CASE_CLOSURE_START.json',dict(status='checked_at_campaign_start',files=dependencies,
        unresolved=[],created_utc=utc()))
    for item in dependencies:add(item)
    add(pin(root/'EXTERNAL_CASE_CLOSURE_START.json'))
    # Bind and recover the currently displayed run03 figures, not the older
    # pre-run03 images in its first-generation recovery ZIP.
    old_publish=read(previous/'PUBLISH_RECEIPT.json');display_files=[]
    for item in old_publish['files']:
        path=BUNDLE/item['relative']
        if sha(path)!=item['new_sha256']:raise ValueError('Current display differs from published run03: '+str(path))
        display_files.append(dict(relative=item['relative'],**pin(path)))
    if len(display_files)!=245:raise ValueError('Exactly 245 display files must be recoverable')
    backup=root/'ORIGINAL_POWER_FIGURES.zip'
    with zipfile.ZipFile(backup,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for item in display_files:archive.write(item['path'],item['relative'])
    with zipfile.ZipFile(backup) as archive:
        if archive.testzip() is not None:raise ValueError('Recovery ZIP CRC failed')
        for item in display_files:
            if hashlib.sha256(archive.read(item['relative'])).hexdigest()!=item['sha256']:
                raise ValueError('Recovery member SHA differs')
    write(root/'ORIGINAL_DISPLAY_MANIFEST.json',dict(bundle=str(BUNDLE),files=display_files,backup=pin(backup)))
    write(root/'HISTORICAL_REFERENCES.json',dict(status='passed',previous_root=str(previous),
        plot_data=pin(previous/'data/PLOT_DATA.json'),acceptance=pin(previous/'ACCEPTANCE.json'),
        archived_source=old_snapshot,conditions=references,original_metadata_preserved=True))
    registration=dict(schema='hospital_ies.source_partition_fix.registration.v1',created_utc=utc(),topics=topics,
        previous_root=str(previous),baseline_plot_data=pin(previous/'data/PLOT_DATA.json'),
        cooling_config=old_registration['cooling_config'],U_contract=base.CONTRACT,new_fault_trajectories=8,
        response_new_execution=False,normal_reference_not_new_execution=True,max_concurrent_workers=4,
        worker_budget_GiB=4.,RAM_reserve_GiB=6.,output_estimate_bytes=1500000000,
        source_delta=source_delta,common_preparation_optimization_disclosed=True,
        qualification='Finite engineering ledger correction only; no new OEM, hydraulic or clinical qualification')
    write(root/'REGISTRATION.json',registration)
    for name in ('REGISTRATION.json','HISTORICAL_REFERENCES.json','ORIGINAL_DISPLAY_MANIFEST.json','ORIGINAL_POWER_FIGURES.zip'):
        add(pin(root/name))
    figure_api=load_module(SOURCE/'release/no_priority_figures_20261105.py','source_partition_legacy_figure_pins')
    for path in figure_api.HELPERS.values():add(pin(path))
    write(root/'SOURCE_BINDING.json',dict(schema='hospital_ies.source_partition_fix.source_binding.v1',
        source_root=str(SOURCE),source_files=base.source_binding(SOURCE),runtime_inputs=base.input_binding(SOURCE),
        execution_tools=execution_tools(),external_files=list(external.values()),created_utc=utc()))
    verify_binding(root)
    print(json.dumps(dict(status='prepared',root=str(root),new_U_trajectories=8,new_R_trajectories=0,
        source_delta=source_delta,recovery_files=245)),flush=True)


def memory_record():
    import psutil
    info=psutil.Process().memory_info()
    return dict(RSS_bytes=info.rss,peak_working_set_bytes=getattr(info,'peak_wset',None),
        available_RAM_bytes=psutil.virtual_memory().available,utc=utc())


def run_u(root,topic_id,attempt=1):
    root=Path(root).resolve();registration=read(root/'REGISTRATION.json')
    topic=next(item for item in registration['topics'] if item['id']==topic_id)
    folder=root/'runs'/(topic_id+'_attempt'+str(attempt).zfill(2));folder.mkdir(parents=True)
    os.environ['TEMP']=os.environ['TMP']=str(folder)
    began=time.perf_counter();capture=None;env=None
    result=dict(schema='hospital_ies.source_partition_fix.U_run.v1',status='failed',topic=topic_id,
        attempt=attempt,new_response_execution=False,new_normal_execution=False,created_utc=utc())
    try:
        binding=verify_binding(root);case=read(topic['case']['path']);builder=base.build_runtime
        if topic['single_unit_adapter']:
            from tools.single_unit_20261104.runtime import build_runtime as builder
        env,controller,resolved=builder(case,binding['source_files'],registration['cooling_config'])
        env.reset(scenario=deepcopy(case['scenario']),seed=int(case['scenario'].get('initialization_seed',20261023)))
        initial=base.physical_state(env)
        if initial!=read(topic['baseline']['initial']['path']):raise ValueError('Initial physical state differs from frozen run03')
        resolved.update(storage_state_receipts={name:deepcopy(initial[name]) for name in ('ups','cold_store')},
            station_config=deepcopy(env.core.station.config),
            reset_physical_context=deepcopy(env.core._current_context(env.core._hardware_data_at(0.)[-1])))
        resolved=base.compact_solver(resolved)
        old_resolved=read(topic['baseline']['resolved']['path'])
        for key in ('plant','physical_constants','storage_state_receipts','station_config','reset_physical_context','policy','control_rule','cooling_config'):
            if resolved.get(key)!=old_resolved.get(key):raise ValueError('Physical common configuration changed: '+key)
        capture=base.Capture(folder/'common',env,initial=initial,resolved=resolved);history=[]
        while env.core.time<60.:
            base.resources(root,folder);observation=base.record_observation(env,history)
            request,diagnostic=controller.decide(base.controller_records(controller,history))
            capture.decision(time_minute=env.core.time,request=request,diagnostic=diagnostic,
                observation=observation,role='common_preparation')
            base.execute_step(capture,request,finish_common=env.core.time==59.)
            base.progress(folder,'common_preparation',env,topic['duration_minutes'])
        checkpoint=read(topic['baseline']['checkpoint']['path']);before=base.physical_state(env)
        old_rows=base.load_rows(topic['baseline']['common_segments']['path'])
        origin=compare_common_origin(capture.rows,before,old_rows,checkpoint['physical_state']);del old_rows
        u=base.fork_fixed_unattended(env);prefix=list(capture.rows)
        write(folder/'COMMON_CHECKPOINT.json',dict(physical_state=before,U_frozen_reference=u.fixed_branch_checkpoint,
            prefix_segments_sha256=sha(folder/'common/SEGMENTS.jsonl'),prefix_segments=len(prefix),
            common_preparation_optimizer_calls_disclosed=True,baseline=topic['baseline'],origin_check=origin))
        capture.close();capture=None
        u_result=base.run_branch(root,folder/'U',u,None,None,prefix,initial,resolved,topic,'unattended')
        if base.physical_state(env)!=before:raise ValueError('U mutated the independent common checkpoint')
        result['U']=pin(folder/'U/RESULT.json')
        if u_result['status']!='completed':raise ValueError('U branch failed; completed status refused')
        result.update(status='completed',source_binding=pin(root/'SOURCE_BINDING.json'),
            expected_end_minute=topic['duration_minutes'],common_origin=origin,
            common_checkpoint_unchanged_after_U=True,historical_response=topic['historical_response'],
            historical_normal=topic['historical_normal'])
        verify_binding(root)
    except BaseException as exc:
        result['error']=base.failure_record(exc,env.core.time if env is not None and getattr(env,'core',None) else 0.)
    finally:
        if capture is not None:capture.close()
        result.update(elapsed_seconds=time.perf_counter()-began,completed_utc=utc(),memory=memory_record())
        write(folder/'RUN_RESULT.json',result)
    print(json.dumps(dict(topic=topic_id,status=result['status'],elapsed_seconds=result['elapsed_seconds'],memory=result['memory'])),flush=True)
    return 0 if result['status']=='completed' else 1


def process_memory(process):
    import psutil
    try:
        info=psutil.Process(process.pid).memory_info()
        return dict(RSS_bytes=info.rss,peak_working_set_bytes=getattr(info,'peak_wset',None))
    except (psutil.NoSuchProcess,psutil.AccessDenied):return dict(RSS_bytes=0,peak_working_set_bytes=None)


def campaign(root,max_workers=4,worker_budget_GiB=4.,RAM_reserve_GiB=6.):
    import psutil
    root=Path(root).resolve();verify_binding(root);registration=read(root/'REGISTRATION.json')
    budget=int(worker_budget_GiB*GiB);reserve=int(RAM_reserve_GiB*GiB)
    admit_worker(available_bytes=0,active_count=0,max_workers=max_workers,worker_budget_bytes=budget,reserve_bytes=reserve)
    queue=[t['id'] for t in registration['topics']];active={};finished={};failed=False;peaks={};began=utc();admission_error=None
    with (root/'CAMPAIGN_LEDGER.jsonl').open('x',encoding='utf8') as ledger:
        while queue or active:
            snapshots={topic:process_memory(item[0]) for topic,item in active.items()}
            unconsumed=sum(max(0,budget-value['RSS_bytes']) for value in snapshots.values())
            available=psutil.virtual_memory().available
            while queue and not failed and admit_worker(available_bytes=available,active_count=len(active),
                    max_workers=max_workers,worker_budget_bytes=budget,reserve_bytes=reserve,
                    reserved_unconsumed_bytes=unconsumed):
                topic=queue.pop(0);log=(root/'logs'/(topic+'_attempt01.log')).open('x',encoding='utf8')
                command=[sys.executable,'-B',str(Path(__file__)),'u','--root',str(root),'--topic',topic]
                process=subprocess.Popen(command,cwd=SOURCE,stdout=log,stderr=subprocess.STDOUT)
                active[topic]=(process,log,command);unconsumed+=budget;available=psutil.virtual_memory().available
                base.emit(ledger,dict(event='started',topic=topic,pid=process.pid,command=command,utc=utc(),
                    admission=dict(available_RAM_bytes=available,reserved_unconsumed_bytes=unconsumed,
                        worker_budget_bytes=budget,RAM_reserve_bytes=reserve,max_workers=max_workers)))
            for topic,(process,log,command) in list(active.items()):
                sample=process_memory(process);peaks[topic]=max(peaks.get(topic,0),sample['RSS_bytes'],sample.get('peak_working_set_bytes') or 0)
                code=process.poll()
                if code is not None:
                    log.close();finished[topic]=code;failed=failed or code!=0
                    base.emit(ledger,dict(event='finished',topic=topic,pid=process.pid,exit_code=code,
                        peak_observed_RSS_or_OS_working_set_bytes=peaks[topic],utc=utc()));del active[topic]
                    if code!=0 and not (root/'STOP.request').exists():
                        (root/'STOP.request').write_text('A U worker failed; stop remaining workers at their next recorded boundary.\n',encoding='utf8')
            if (root/'STOP.request').exists():failed=True
            state=dict(status='running',finished=finished,queued=queue,active={topic:dict(pid=item[0].pid,
                    memory=process_memory(item[0])) for topic,item in active.items()},
                peak_observed_bytes=peaks,available_RAM_bytes=psutil.virtual_memory().available,
                worker_budget_bytes=budget,RAM_reserve_bytes=reserve,max_workers=max_workers,utc=utc())
            temporary=root/'CAMPAIGN_PROGRESS.tmp';temporary.write_text(json.dumps(state,indent=2),encoding='utf8')
            os.replace(temporary,root/'CAMPAIGN_PROGRESS.json')
            if failed and not active:break
            if queue and not active and not failed:
                failed=True;admission_error='Insufficient RAM for one admitted U worker; no further trajectory started'
                break
            if active:time.sleep(1.)
    result=dict(status='completed' if len(finished)==8 and not failed else 'failed',finished=finished,
        not_started=queue,peak_observed_bytes=peaks,max_workers=max_workers,worker_budget_bytes=budget,
        RAM_reserve_bytes=reserve,new_fault_trajectories_started=len(finished),new_R_trajectories=0,
        admission_error=admission_error,started_utc=began,utc=utc())
    write(root/'CAMPAIGN_RESULT.json',result);print(json.dumps(result),flush=True)
    return 0 if result['status']=='completed' else 1


def selected_u(root,topic):
    folder=root/'runs'/(topic['id']+'_attempt01');run=read(folder/'RUN_RESULT.json')
    if (run.get('status')!='completed' or run.get('topic')!=topic['id']
            or run.get('expected_end_minute')!=topic['duration_minutes']
            or run.get('source_binding')!=pin(root/'SOURCE_BINDING.json')
            or run.get('common_checkpoint_unchanged_after_U') is not True):raise ValueError('Invalid U run completion/source identity')
    result_pin=check_pin(run['U'],inside=folder/'U');result=read(result_pin['path'])
    if (result.get('status')!='completed' or result.get('role')!='unattended' or result.get('topic')!=topic['id']
            or result.get('case')!=topic['case'] or result.get('source_binding')!=pin(root/'SOURCE_BINDING.json')
            or result.get('controller_calls_post60')!=0 or result.get('optimizer_entry_attempts')!=[]
            or result.get('observed_until_minute')!=topic['duration_minutes']):raise ValueError('Invalid U producer identity/activity')
    checked_files=[check_pin(item,inside=folder/'U') for item in result['files']]
    required={str((folder/'U'/name).resolve()) for name in ('SEGMENTS.jsonl','INITIAL_STATE.json',
        'RESOLVED_CASE.json','ENGINEERING_AUDIT.json','STATES.jsonl','EVENTS.jsonl','DECISIONS.jsonl','SOLVER_LOG.jsonl')}
    if not required<={item['path'] for item in checked_files}:raise ValueError('Required U raw evidence is not pinned')
    rows=base.load_rows(folder/'U/SEGMENTS.jsonl')
    if result.get('actual_segment_count')!=len(rows):raise ValueError('U native interval count changed')
    condition=dict(source=dict(role='new_source_partition_arithmetic_execution',segments=pin(folder/'U/SEGMENTS.jsonl'),
        result=result_pin,source_binding=result['source_binding']),segments=compact_rows(rows))
    return folder,run,result,rows,condition


def extract(root):
    root=Path(root).resolve();verify_binding(root);registration=read(root/'REGISTRATION.json')
    if read(root/'CAMPAIGN_RESULT.json')['status']!='completed':raise ValueError('All eight new U trajectories must complete')
    old=read(registration['baseline_plot_data']['path']);replacements={}
    for topic in registration['topics']:
        folder,run,result,rows,condition=selected_u(root,topic);replacements[topic['id']]=condition
        base.audit_segments(rows,topic['duration_minutes']);del rows
    data=merge_plot_data(old,replacements,pin(root/'SOURCE_BINDING.json'))
    data['baseline_plot_data']=registration['baseline_plot_data']
    destination=root/'data';destination.mkdir()
    write(destination/'PLOT_DATA.json',data)
    print(str(destination/'PLOT_DATA.json'),flush=True)


def accept(root):
    root=Path(root).resolve();receipt=dict(schema='hospital_ies.source_partition_fix.acceptance.v1',status='failed',
        root=str(root),partial=False,created_utc=utc(),acceptance_script=pin(__file__),errors=[],checked_topics={},
        complete_topic_count=0,plot_data_verified=False,new_fault_trajectories=8,new_R_trajectories=0,
        normal_reference_new_execution=False,response_reference_new_execution=False,
        full_test_suite_qualification=False,native_primal_certificate_reconstructed=False)
    try:
        if (root/'STOP.request').exists():raise ValueError('Stopped campaign cannot be accepted')
        binding=verify_binding(root);before=pin(root/'SOURCE_BINDING.json')
        registration=read(root/'REGISTRATION.json');old=read(registration['baseline_plot_data']['path']);data=read(root/'data/PLOT_DATA.json')
        if data.get('source_binding')!=before or data.get('schema')!=SCHEMA:raise ValueError('Plot source identity/schema differs')
        receipt['preserved_conditions']=assert_preserved_conditions(data,old)
        old_api=load_module(Path(registration['previous_root'])/'accept_results.py','source_partition_historical_acceptance_utilities')
        original_refs=read(root/'HISTORICAL_REFERENCES.json');cache={}
        for topic,old_topic,new_topic in zip(registration['topics'],old['topics'],data['topics']):
            folder,run,result,rows,condition=selected_u(root,topic)
            if new_topic['conditions']['unattended']!=condition:raise ValueError('U plot extraction changed')
            common=base.load_rows(folder/'common/SEGMENTS.jsonl');old_common=base.load_rows(topic['baseline']['common_segments']['path'])
            checkpoint=read(folder/'COMMON_CHECKPOINT.json');baseline_checkpoint=read(topic['baseline']['checkpoint']['path'])
            origin=compare_common_origin(common,checkpoint['physical_state'],old_common,baseline_checkpoint['physical_state'])
            if rows[:len(common)]!=common:raise ValueError('U original prefix changed after the fork')
            if (sha(folder/'common/SEGMENTS.jsonl')!=checkpoint['prefix_segments_sha256']
                    or checkpoint['prefix_segments']!=len(common)):raise ValueError('Common prefix hash/count changed')
            if read(folder/'U/INITIAL_STATE.json')!=read(topic['baseline']['initial']['path']):raise ValueError('U initial physical state changed')
            first=old_api.first_json_line(folder/'U/STATES.jsonl')
            if first!=checkpoint['physical_state']:raise ValueError('U first complete state differs from the matched common checkpoint')
            resolved=read(folder/'U/RESOLVED_CASE.json');reference=checkpoint['U_frozen_reference']['branch_reference']
            last=common[-1];plant=resolved['plant'];action=last['action']
            from tools.noopt_unattended_20261104.fixed_branches import freeze_branches
            from types import SimpleNamespace
            import numpy as np
            expected=freeze_branches(SimpleNamespace(**plant),np.asarray(last['loads_kW'])-np.asarray(last['unserved_kW']),action,
                source=dict(start_minute=last['start_minute'],end_minute=last['end_minute'],
                    role='actual_common_prefault_interval',future_information_used=False))
            if expected!=reference:raise ValueError('New U branch shares differ from the actual unchanged common ports')
            absolute=action['absorption_cooling_kW']/resolved['physical_constants']['RT_TO_KW']*plant['abs_steam_lb_per_rt']
            heat=float(np.asarray(last['service_request_ledger']['delivered_kW'])[:,1].sum())/(plant['steam_kWh_per_lb']*plant['exchanger_eff'])
            share=absolute/(absolute+heat) if absolute+heat>1e-9 else 0.
            audit=base.independent_audit(rows,resolved,topic['duration_minutes'],role='unattended',reference=expected,steam_share=share)
            if audit!=read(folder/'U/ENGINEERING_AUDIT.json'):raise ValueError('Recomputed U audit differs')
            for event in base.load_rows(folder/'U/EVENTS.jsonl'):
                if event.get('time_minute',-1)>=60. and event.get('kind') in ('automatic_load_shed','automatic_shed_requested','command_generated'):
                    raise ValueError('U generated a post60 control/shedding command')
            historical={}
            for role in ('normal','response'):
                expected_result=original_refs['conditions'][topic['id']][role]['result']
                historical[role]=verify_condition(old_topic['conditions'][role],expected_result=expected_result,cache=cache)
            demand=old_api.compare_native_demands({role:new_topic['conditions'][role]['segments']
                for role in ('normal','unattended','response')},topic['duration_minutes'])
            receipt['checked_topics'][topic['id']]=dict(status='passed',new_U=pin(folder/'U/RESULT.json'),
                run=pin(folder/'RUN_RESULT.json'),common_origin=origin,checkpoint=pin(folder/'COMMON_CHECKPOINT.json'),
                independent_U_audit=audit,original_demands=demand,historical_references=historical,
                exact_U_plot_extraction=True,original_R_N_values_and_producer_metadata_preserved=True)
            del rows,common,old_common
        campaign_result=read(root/'CAMPAIGN_RESULT.json')
        if (campaign_result.get('status')!='completed' or len(campaign_result.get('finished',{}))!=8
                or any(campaign_result['finished'].values())):raise ValueError('Campaign exit ledger is incomplete')
        if len(receipt['checked_topics'])!=8:raise ValueError('Eight complete accepted topics required')
        if before!=pin(root/'SOURCE_BINDING.json'):raise ValueError('Execution freeze changed during acceptance')
        verify_binding(root)
        receipt.update(status='passed',complete_topic_count=8,plot_data_verified=True,plot_data=pin(root/'data/PLOT_DATA.json'),
            source_binding=before,campaign_result=pin(root/'CAMPAIGN_RESULT.json'))
    except BaseException as exc:
        receipt['errors'].append(dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc()))
    receipt['completed_utc']=utc();write(root/'ACCEPTANCE.json',receipt)
    print(json.dumps(dict(status=receipt['status'],complete_topic_count=receipt['complete_topic_count'],errors=receipt['errors'])),flush=True)
    return 0 if receipt['status']=='passed' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','u','run','extract','accept'))
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--previous',type=Path,default=PREVIOUS)
    parser.add_argument('--topic');parser.add_argument('--attempt',type=int,default=1)
    parser.add_argument('--max-workers',type=int,default=4);parser.add_argument('--worker-budget-gib',type=float,default=4.)
    parser.add_argument('--ram-reserve-gib',type=float,default=6.)
    args=parser.parse_args()
    if args.command=='prepare':prepare(args.root,args.previous);return 0
    if args.command=='u':return run_u(args.root,args.topic,args.attempt)
    if args.command=='run':return campaign(args.root,args.max_workers,args.worker_budget_gib,args.ram_reserve_gib)
    if args.command=='extract':extract(args.root);return 0
    return accept(args.root)


if __name__=='__main__':raise SystemExit(main())
