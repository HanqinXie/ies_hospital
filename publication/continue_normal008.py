"""Publication-only last normal008 continuation; heavy validation is child-only."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime,timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
RUN_REL='outputs/v20_load_accounting_20261019_run01'
RUN=ROOT/RUN_REL
HERE=RUN/'review/normal008_continuation01'
PROFILE=RUN/'provenance/normal_resource_profile'
TARGET='v20_normal_008'
FREEZE_SHA='48baed7a128d2f921d36323330ae4d22e2e6dc83214e8a505b6ba89273439b7b'
OLD_HELPER_SHA='50ab72194abf63dda9c1893595629f57d0b3ceb488eb2e64ff709fe273f8701b'
S3_SHA='7cdc0eb91c2d52b1996f478478cf6b9762c73861bee6035257e6529b178bc117'
GIB=1024**3

def require(value,message):
    if not value:raise ValueError(message)

def now():return datetime.now(timezone.utc).isoformat()

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024**2),b''):h.update(b)
    return h.hexdigest()

def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')

def fingerprint(path):
    path=Path(path).resolve(strict=True)
    require(path.is_relative_to(ROOT),'Evidence escaped scientific root')
    return dict(path=path.relative_to(ROOT).as_posix(),bytes=path.stat().st_size,sha256=sha(path))

def require_unstarted(directory):
    require(not any((Path(directory)/n).exists() for n in ('ATTEMPTS.jsonl','pairs','BATCH_RESULT.json')),
        '008 has prior started/partial/terminal evidence; never overwrite or retry')

def validate_pending_target(job):
    require(job.get('name')==TARGET and job.get('cohort')=='normal' and job.get('policies')==['C19'],
        'Only canonical single-policy normal008 C19 is authorized')

def require_no_science(active,issues):
    require(not active and not issues,'Scientific child active or process enumeration unknown')

def parent_footprint():
    """Actual resident-module and OS memory facts, without loading project code."""
    forbidden=('tools.','hospital_ies','scripts.v20','numpy','pandas','scipy','torch','gurobipy')
    heavy=[n for n in sys.modules if n.startswith(forbidden)]
    require(not heavy,'Heavy project/scientific module resident in lightweight parent: '+str(heavy))
    import psutil
    info=psutil.Process(os.getpid()).memory_info()
    return dict(utc=now(),pid=os.getpid(),actual_cwd=str(Path.cwd()),heavy_modules=heavy,
        private_bytes=int(info.private),working_set_bytes=int(info.rss),OS_lifetime_peak_working_set_bytes=int(info.peak_wset),
        project_modules_imported=False,physical_episodes_started_by_observation=0)

def active_science():
    import psutil
    active=[];issues=[]
    for pid in psutil.pids():
        try:
            p=psutil.Process(pid)
            if re.fullmatch(r'python(?:w|\d+(?:\.\d+)*)?(?:\.exe)?',p.name(),re.I) is None:continue
            args=p.cmdline();require(isinstance(args,list) and args,'Unknown Python command line')
            if any(args[i:i+3]==['-m','tools.load_accounting.runner','run'] for i in range(len(args)-2)):
                active.append(dict(pid=pid,create_time=p.create_time(),command=args))
        except psutil.NoSuchProcess:continue
        except (psutil.Error,OSError,ValueError) as e:issues.append(dict(pid=pid,type=type(e).__name__,error=str(e)))
    return active,issues

def qualify405():
    """Short-lived explicit CLI only; never imported by the persistent monitor."""
    require_no_science(*active_science())
    own=sha(Path(__file__))
    require(sha(ROOT/'publication/run_normal_serial.py')==OLD_HELPER_SHA,'Original reviewed helper changed')
    require(sha(RUN/'MODEL_FREEZE_ACCOUNTING01.json')==FREEZE_SHA,'Scientific freeze changed')
    sys.path[:0]=[str(ROOT),str(ROOT/'src')]
    from publication import release_gate as gate
    from publication.run_normal_serial import verify_frozen_project
    frozen,freeze_sha=verify_frozen_project();api=gate.load_api(ROOT)
    jobs=api.campaign.campaign_jobs();census=gate.qualify_census(jobs)
    require(len(jobs)==148 and sum(len(j['policies']) for j in jobs)==406,'148/406 canonical census differs')
    registration=read(RUN/'design/CAMPAIGN_final01.json')
    require(registration.get('registered_batches')==148 and registration.get('registered_pairs')==406
        and registration.get('freeze_sha256')==freeze_sha
        and registration.get('jobs')==[{k:v for k,v in j.items() if k!='scene'} for j in jobs],
        'Actual canonical registration differs')
    pending=[j for j in jobs if j['name']==TARGET];require(len(pending)==1,'Unique canonical008 missing')
    job=pending[0];validate_pending_target(job);directory=RUN/'experiments'/TARGET;require_unstarted(directory)
    proposal,config=gate.expected_registration(ROOT,job,frozen,api)
    gate.qualify_registration(read(directory/'plan.json'),read(directory/'manifest.json'),job,frozen,freeze_sha,
        proposal,config,sha(directory/'manifest.json'),api)
    proofs=[gate.qualify_registered_batch(ROOT,j,frozen,freeze_sha,api=api) for j in jobs if j['name']!=TARGET]
    require(len(proofs)==147 and sum(p['pairs'] for p in proofs)==405 and all(p['qualified'] for p in proofs),
        '405 actual completed pairs not strictly qualified')
    rows={r['path']:r for p in proofs for r in p['evidence_files']}
    for path in (directory/'plan.json',directory/'manifest.json',RUN/'design/CAMPAIGN_final01.json',RUN/'MODEL_FREEZE_ACCOUNTING01.json',ROOT/'publication/release_gate.py',ROOT/'publication/run_normal_serial.py'):
        row=fingerprint(path);rows[row['path']]=row
    for row in rows.values():
        p=ROOT/row['path'];require(p.stat().st_size==row['bytes'] and sha(p)==row['sha256'],'Evidence changed during405 qualification')
    source=api.runner.source_binding();tests=api.gate.test_binding();inputs=api.design.runtime_input_inventory(ROOT)
    require(source==frozen['source_sha256'] and tests==frozen['source_bound_test_gate']['test_files_sha256']
        and inputs==frozen['runtime_inputs'] and (len(source),len(tests),len(inputs))==(366,241,28),
        'Scientific366/241/28 changed during405 qualification')
    require_unstarted(directory);require_no_science(*active_science());require(sha(Path(__file__))==own,'Qualifier source changed while running')
    result=dict(schema='hospital_ies.v20.normal008_405_qualification.v1',status='passed',utc=now(),
        script_sha256=own,scientific_root=str(ROOT),actual_cwd=str(Path.cwd()),freeze_sha256=freeze_sha,
        canonical_batches=148,canonical_pairs=406,actual_complete_batches=147,actual_complete_pairs=405,
        only_not_started=TARGET,source_files=366,test_files=241,runtime_inputs=28,
        source_binding_sha256=digest(source),test_binding_sha256=digest(tests),runtime_inputs_sha256=digest(inputs),
        census=census,batch_proofs=proofs,evidence_files=list(rows.values()),old_stop_required=False,
        physical_episodes_started=0,raw_files_opened=0,annual_values_parsed=0)
    result['signature']=digest(result);return result

def journal(path,**value):
    row=dict(value);row.setdefault('utc',now())
    with Path(path).open('a',encoding='utf8') as f:f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n');f.flush()

def wait_next_sample(deadline):
    """Target2s including work; overruns do not add another fixed2s wait."""
    deadline+=2.
    remaining=deadline-time.monotonic()
    if remaining>0:time.sleep(remaining)
    return deadline

def resource_admission(profile,memory,reserve_gib=2):
    require(reserve_gib in (2,3),'Only preregistered2/3GiB reserve allowed')
    require(profile.get('source_model_id')=='M19_model_closeout' and profile.get('is_M20_measurement') is False
        and profile.get('alias')=='C19' and profile.get('duration_minutes')==4320.
        and profile.get('original_batch')=='normal_C19_final_r1_002'
        and profile.get('OS_lifetime_peak_working_set_bytes')==24352612352
        and profile.get('sampled_private_peak_bytes')==26497581056,'Exact historical same72h C19 profile differs')
    physical=math.ceil(1.1*24352612352)+reserve_gib*GIB
    commit=math.ceil(1.1*26497581056)+reserve_gib*GIB
    def sufficient(key,minimum):
        v=memory.get(key);return type(v) is int and v>=minimum
    return dict(admitted=sufficient('available_physical_bytes',physical) and sufficient('commit_headroom_bytes',commit),
        required_physical_bytes=physical,required_commit_headroom_bytes=commit,growth_factor=1.1,
        host_reserve_bytes=reserve_gib*GIB,reserve_gib=reserve_gib,is_M20_peak_measurement=False,
        historical_peaks_are_empirical_not_upper_bounds=True)

def stable_admission(samples,profile,window_seconds=30,reserve_gib=2):
    require(window_seconds>=30,'Observed resource window must be at least30 seconds')
    failed=dict(admitted=False,reason='stable_observed_window_not_met')
    if not samples:return failed
    times=[s.get('monotonic') for s in samples]
    if not all(type(v) in (int,float) and math.isfinite(v) for v in times):return failed
    if any(b<=a for a,b in zip(times,times[1:])):return failed
    boundary=times[-1]-window_seconds
    start=next((i for i,t in enumerate(times) if t>=boundary),len(times)-1)
    if start and times[start]>boundary:start-=1
    # Preserve both original guards: >=30s and >=16 observations. A legitimate
    # 2.25s overrun needs33.75s for16 points; truncating to30s made it impossible.
    start=min(start,max(0,len(samples)-16))
    rows=samples[start:]
    if rows[-1]['monotonic']-rows[0]['monotonic']<window_seconds or len(rows)<16:return failed
    if any(b['monotonic']-a['monotonic']>3. for a,b in zip(rows,rows[1:])):return failed
    for reserve in ((3,2) if reserve_gib==2 else (3,)):
        checks=[resource_admission(profile,s,reserve) for s in rows]
        if all(c['admitted'] for c in checks):
            return dict(checks[-1],admitted=True,selected_reserve_gib=reserve,stable_observed_seconds=rows[-1]['monotonic']-rows[0]['monotonic'],
                observations=len(rows),maximum_observation_gap_seconds=max(b['monotonic']-a['monotonic'] for a,b in zip(rows,rows[1:])),
                minimum_available_physical_bytes=min(s['available_physical_bytes'] for s in rows),
                minimum_commit_headroom_bytes=min(s['commit_headroom_bytes'] for s in rows),
                resource_window_is_sampled_not_continuous_mathematical_bound=True)
    return failed

def validate_stop_chain(summary,command,s3):
    names={f'v20_normal_{i:03d}' for i in range(12) if i not in (8,9)}
    done=summary.get('completed') or []
    require(summary.get('exit_code')==125 and summary.get('state')=='explicit_stop_new_launches'
        and summary.get('pending_not_started')==[TARGET] and summary.get('actual_scientific_children_started')==10
        and summary.get('existing_qualified_skipped')==['v20_normal_009']
        and summary.get('started_without_completed_supervisor_result')==[],'Original normal03 intentional125 continuation differs')
    require(len(done)==10 and {r.get('batch') for r in done}==names
        and all(r.get('passed') is True and r.get('exit_code')==0 for r in done),'Original ten completed resource results differ')
    require(command.get('name')=='normal03' and command.get('exit_code')==125
        and Path(command.get('cwd','')).resolve()==ROOT,'Actual normal03 command did not stop125')
    cp=s3.get('checkpoint') or {}
    require(s3.get('status')=='failed_unknown' and s3.get('source_modified') is False
        and cp.get('registered')==406 and cp.get('actual_started')==cp.get('actual_finished')==405
        and cp.get('active')==0 and cp.get('not_started')==1 and cp.get('failed_terminal_journals')==[],
        'Original S3 failed stop is not preserved at405/406')
    return dict(qualified=True,normal03_intentional_exit_code=125,S3_original_status='failed_unknown',
        old_measured_resource_batches=sorted(names),normal009_resource_peak='unknown',only_remaining=TARGET)

def windows_memory():
    require(os.name=='nt','Windows counters required')
    class Memory(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(k,ctypes.c_ulonglong) for k in
            ('total_phys','avail_phys','total_page','avail_page','total_virtual','avail_virtual','avail_extended')]
    class Performance(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong)]+[(k,ctypes.c_size_t) for k in
            ('CommitTotal','CommitLimit','CommitPeak','PhysicalTotal','PhysicalAvailable','SystemCache','KernelTotal','KernelPaged','KernelNonpaged','PageSize')]+[(k,ctypes.c_ulong) for k in ('HandleCount','ProcessCount','ThreadCount')]
    m=Memory();m.length=ctypes.sizeof(m);require(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)),'GlobalMemoryStatusEx failed')
    p=Performance();p.cb=ctypes.sizeof(p);require(ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(p),p.cb),'GetPerformanceInfo failed')
    return dict(utc=now(),monotonic=time.monotonic(),total_physical_bytes=int(m.total_phys),available_physical_bytes=int(m.avail_phys),
        committed_bytes=int(p.CommitTotal)*int(p.PageSize),commit_limit_bytes=int(p.CommitLimit)*int(p.PageSize),
        commit_headroom_bytes=int(p.CommitLimit-p.CommitTotal)*int(p.PageSize),
        counter_basis='GlobalMemoryStatusEx physical; GetPerformanceInfo commit pages times page size')

def process_sample(process):
    import psutil
    row=windows_memory();row.update(batch=TARGET,pid=process.pid)
    try:
        info=psutil.Process(process.pid).memory_info()
        row.update(working_set_bytes=int(info.rss),private_bytes=int(info.private),OS_lifetime_peak_working_set_bytes=int(info.peak_wset),process_sample_status='measured')
    except (psutil.Error,OSError,AttributeError) as e:
        row.update(working_set_bytes=None,private_bytes=None,OS_lifetime_peak_working_set_bytes=None,process_sample_status='unknown',error_type=type(e).__name__,error=str(e))
    return row

@contextmanager
def serial_lease():
    import msvcrt
    with (PROFILE/'normal_serial.lock').open('a+b') as f:
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as e:raise ValueError('Existing normal serial lease is still held') from e
        try:yield
        finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)

def monitor_child(command,out,env,launches):
    """No kill/terminate path: every successful Popen is unconditionally drained."""
    samples=[];fact=dict(batch=TARGET,pid=None,successful_Popen=False,owned_child_drained=False,exit_code=None)
    launches.append(fact);began=time.monotonic();error=None;diagnostic_errors=[]
    with (out/(TARGET+'.log')).open('xb') as log:
        process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        fact.update(successful_Popen=True,pid=process.pid)
        try:
            journal(out/'EVENTS.jsonl',event='scientific_child_started',**fact,command=command)
            deadline=time.monotonic()
            while process.poll() is None:
                row=process_sample(process);samples.append(row);journal(out/'SAMPLES.jsonl',**row)
                # Resource pressure is observed only; it never triggers child termination.
                others,issues=active_science();others=[p for p in others if p['pid']!=process.pid]
                if others or issues:
                    raise ValueError('Unexpected science/enumeration uncertainty; drain owned child without retry')
                deadline=wait_next_sample(deadline)
        except BaseException as e:
            error=dict(type=type(e).__name__,message=str(e))
            try:journal(out/'EVENTS.jsonl',event='monitor_failed_drain_owned_child',error=error)
            except BaseException as d:diagnostic_errors.append(dict(type=type(d).__name__,message=str(d)))
            raise
        finally:
            while True:
                try:process.wait();break
                except BaseException as d:diagnostic_errors.append(dict(type=type(d).__name__,message=str(d)))
            fact.update(owned_child_drained=True,exit_code=process.returncode)
            if error:
                try:write_new(out/(TARGET+'.interrupted.json'),dict(**fact,error=error,diagnostic_errors=diagnostic_errors,resources_unknown=True,no_retry=True))
                except BaseException as d:fact['receipt_error']=str(d)
    good=[r for r in samples if r.get('process_sample_status')=='measured']
    maximum=lambda key:max((r[key] for r in good),default=None)
    result=dict(batch=TARGET,exit_code=process.returncode,elapsed_seconds=time.monotonic()-began,
        resource_samples=len(samples),unknown_process_samples=len(samples)-len(good),
        sampled_private_peak_bytes=maximum('private_bytes'),sampled_working_set_peak_bytes=maximum('working_set_bytes'),
        observed_OS_lifetime_peak_working_set_bytes=maximum('OS_lifetime_peak_working_set_bytes'),
        minimum_observed_available_physical_bytes=min((r['available_physical_bytes'] for r in samples),default=None),
        minimum_observed_commit_headroom_bytes=min((r['commit_headroom_bytes'] for r in samples),default=None),
        measurements_are_M20=True,original_V19_admission_profile_not_overwritten=True,
        last_child=dict(fact),pressure_policy='record only; never terminate science',qualification=None)
    write_new(out/'RESOURCE_OBSERVATIONS.json',result);return result

def capture(command,log_path,receipt_path,*,name,env):
    require(not log_path.exists() and not receipt_path.exists(),'Prior command evidence exists; never overwrite')
    start=now();began=time.monotonic();code=None;error=None
    try:
        with log_path.open('xb') as log:
            done=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=False);code=done.returncode
    except BaseException as e:error=dict(type=type(e).__name__,message=str(e));raise
    finally:
        write_new(receipt_path,dict(name=name,command=command,cwd=str(ROOT),started_utc=start,finished_utc=now(),
            exit_code=code,error=error,elapsed_seconds=time.monotonic()-began,
            environment_overrides={k:env[k] for k in ('TEMP','TMP','PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING','PYTHONPATH') if k in env}))
    return code

def child_environment(temp):
    temp.mkdir(parents=True,exist_ok=False);env=os.environ.copy()
    env.update(TEMP=str(temp),TMP=str(temp),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT))
    return env

def short_qualification(out,name):
    target=out/('FINAL_406_PROOF.json' if name=='final_qualification' else name+'.json')
    flag='--qualify405' if name=='preflight' else '--qualify406'
    command=[sys.executable,'-B',str(Path(__file__).resolve()),flag,'--receipt',str(target)]
    code=capture(command,out/(name+'.log'),out/(name+'.command.json'),name=name,env=child_environment(out/'temp'/name))
    require(code==0,'Short-lived qualification child exit '+str(code))
    result=read(target);require(result.get('status')=='passed','Short-lived qualification did not pass')
    return result

def recheck_rows(rows):
    for row in rows:
        p=(ROOT/row['path']).resolve(strict=True)
        require(p.is_relative_to(ROOT) and p.stat().st_size==row['bytes'] and sha(p)==row['sha256'],
            'Pinned evidence changed: '+row['path'])

def stop_evidence(stop_sha,plan_sha):
    require(re.fullmatch('[0-9a-f]{64}',stop_sha or '') and re.fullmatch('[0-9a-f]{64}',plan_sha or ''),'Explicit approved STOP/plan SHA required')
    stop=PROFILE/'STOP_NORMAL_SERIAL.json';plan=ROOT/'docs/v20/RESOURCE_CONTINUATION_PLAN.md'
    require(sha(stop)==stop_sha and sha(plan)==plan_sha,'Authorized STOP/resource plan SHA differs')
    require(sha(ROOT/'publication/run_normal_serial.py')==OLD_HELPER_SHA
        and sha(RUN/'review/supervision03/SUPERVISOR_IMPLEMENTATION.py')==S3_SHA,'Preserved helper/S3 implementation differs')
    summary=PROFILE/'supervision_canonical02/SUMMARY.json';command=RUN/'logs/normal03.command.json';s3=RUN/'review/supervision03/RESULT.json'
    proof=validate_stop_chain(read(summary),read(command),read(s3))
    files=[stop,plan,summary,command,s3,RUN/'review/supervision03/SUPERVISOR_IMPLEMENTATION.py',
        ROOT/'publication/run_normal_serial.py',ROOT/'publication/release_gate.py',Path(__file__),
        RUN/'MODEL_FREEZE_ACCOUNTING01.json',PROFILE/'V19_R2_PROFILE.json',PROFILE/'V19_R2_SOURCE_SUMMARY.json']
    proof['evidence_files']=[fingerprint(p) for p in files];return proof

def historical_profile():
    data=read(PROFILE/'V19_R2_PROFILE.json')
    require(data.get('signature')==digest({k:v for k,v in data.items() if k!='signature'}),'Historical profile signature changed')
    require(sha(PROFILE/'V19_R2_SOURCE_SUMMARY.json')==data.get('source_summary_sha256')
        =='193d9ad2bbe8a67359e07200a64530d761f181eaf62b9fb16b5423a9a8734c77','Historical R2 source summary changed')
    matches=[r for r in data['profiles'] if r['original_batch']=='normal_C19_final_r1_002'];require(len(matches)==1,'Unique same72h historical C19 absent')
    p=matches[0];resource_admission(p,{},2)
    scene=read(RUN/'experiments'/TARGET/'manifest.json')['scenarios'][0]
    require(p['scenario_id']==scene['id'] and p['physical_scenario_signature']==scene['physical_scenario_signature']
        and p['duration_minutes']==scene['duration_steps']*scene.get('step_minutes',15.),'Historical profile is a different physical specification')
    return p

def final_launch_guard(directory,profile,admission,pins):
    require_unstarted(directory);require_no_science(*active_science())
    recheck_rows(pins.get('evidence_files',[]))
    current=windows_memory();decision=resource_admission(profile,current,admission['selected_reserve_gib'])
    require(decision['admitted'],'Resource counters fell after stable window; no launch')
    return current

def new_run_directory(out):
    require(not out.exists(),'Prior continuation directory exists; never doublelaunch or overwrite')
    out.mkdir(parents=True,exist_ok=False)

def qualify406():
    require_no_science(*active_science());sys.path[:0]=[str(ROOT),str(ROOT/'src')]
    from publication.release_gate import qualify_final_campaign
    return qualify_final_campaign(ROOT)

def full_report(out,baseline_root):
    require(Path(baseline_root).resolve(strict=True)!=ROOT,'Full V19 baseline must be separate')
    command=[sys.executable,'-B','scripts/v20_report.py','--label','FINAL01','--baseline-root',str(Path(baseline_root).resolve())]
    name='final_readback01';env=child_environment(RUN/'temp'/name)
    code=capture(command,RUN/'logs'/f'{name}.log',RUN/'logs'/f'{name}.command.json',name=name,env=env)
    require(code==0,'FINAL01 saved report command failed')
    folder=RUN/'paper_results/FINAL01';summary=read(folder/'SUMMARY.json');receipt=read(folder/'RECEIPT.json')
    require(summary.get('expected_pairs')==summary.get('actual_started_pairs')==summary.get('actual_finished_pairs')
        ==summary.get('complete_registered_full_pairs')==summary.get('eligible_terminal_pairs')==406
        and summary.get('unknown_full_pairs')==0 and summary.get('status_counts')=={'completed':406},'FINAL01 actual denominator not qualified406')
    frozen=read(RUN/'MODEL_FREEZE_ACCOUNTING01.json')
    require(receipt.get('script_sha256')==frozen['source_sha256']['scripts/v20_report.py']
        and receipt.get('raw_opened') is False and receipt.get('annual_values_read') is False,'FINAL01 report source/scope differs')
    for n,s in receipt['output_files'].items():require(sha(folder/n)==s,'FINAL01 output receipt SHA differs')
    return dict(command_receipt=fingerprint(RUN/'logs'/f'{name}.command.json'),report_receipt=fingerprint(folder/'RECEIPT.json'),summary=fingerprint(folder/'SUMMARY.json'))

def continuation(name,*,run,baseline_root,stop_sha,plan_sha,window_seconds=30.,reserve_gib=2,max_wait_seconds=0.,counter_probe_seconds=0.):
    require(re.fullmatch('[A-Za-z0-9_]+',name or '') is not None,'New simple continuation name required')
    require(window_seconds>=30 and reserve_gib in (2,3) and max_wait_seconds>=0,'Registered bounded resource admission required')
    require(counter_probe_seconds==0 or (not run and counter_probe_seconds==window_seconds),
        'Counter probe is check-only and uses the unchanged registered resource window')
    out=HERE/name;new_run_directory(out);launches=[];resource=None;pins=None;final=None
    try:
        pins=stop_evidence(stop_sha,plan_sha);profile=historical_profile()
        write_new(out/'START.json',dict(schema='hospital_ies.v20.normal008_continuation_start.v1',utc=now(),pid=os.getpid(),
            command=sys.argv,cwd=str(Path.cwd()),scientific_root=str(ROOT),script_sha256=sha(Path(__file__)),
            mode='single_registered008' if run else 'check_only_no_physics',target=TARGET,
            parent_footprint=parent_footprint(),
            predecessor=pins,historical_profile=profile,growth_factor=1.1,minimum_reserve_gib=reserve_gib,prefer_three_gib=True,
            required_observed_window_seconds=window_seconds,max_wait_seconds=max_wait_seconds,
            counter_probe_seconds=counter_probe_seconds,sampling_target_seconds=2.,sampling_schedule='deadline_minus_work',
            no_old_STOP_deleted=True,no_frozen_science_modified=True,pressure_policy='record only; no forced termination'))
        with serial_lease():
            require_no_science(*active_science());pre=short_qualification(out,'preflight')
            require(pre.get('actual_complete_pairs')==405 and pre.get('actual_complete_batches')==147
                and pre.get('only_not_started')==TARGET,'Preflight actual405/only008 differs')
            # The qualification process has completely exited here. Persistent
            # parent has not imported any project/numpy/pandas/torch module.
            recheck_rows(pre['evidence_files']);recheck_rows(pins['evidence_files'])
            write_new(out/'PARENT_AFTER_PREFLIGHT.json',parent_footprint())
            if not run and not counter_probe_seconds:
                memory=windows_memory();final=dict(status='check_only_no_physics',exit_code=0,
                    resource_decision=resource_admission(profile,memory,reserve_gib),memory=memory)
            else:
                rows=[];began=time.monotonic();deadline=began;admission=None
                while admission is None:
                    require_no_science(*active_science());recheck_rows(pins['evidence_files'])
                    row=windows_memory();rows.append(row);rows=rows[-100:]
                    journal(out/'ADMISSION_SAMPLES.jsonl',**row)
                    candidate=stable_admission(rows,profile,window_seconds,reserve_gib)
                    if candidate['admitted']:
                        try:latest=final_launch_guard(RUN/'experiments'/TARGET,profile,candidate,pins)
                        except ValueError as e:
                            # A resource-only fall may be sampled again, but any
                            # partial source/process ambiguity remains a hard stop.
                            if not str(e).startswith('Resource counters fell'):raise
                            rows=[];journal(out/'EVENTS.jsonl',event='resource_admission_fell_without_launch',error=str(e))
                        else:admission=dict(candidate,last_counter_recheck=latest)
                    if admission is None:
                        if time.monotonic()-began>=max_wait_seconds:
                            final=dict(status='waiting_resources_not_started',exit_code=3,only_not_started=TARGET);break
                        deadline=wait_next_sample(deadline)
                if admission is not None and not run:
                    probe=dict(status='passed_no_physics',observed_admission=admission,
                        samples=fingerprint(out/'ADMISSION_SAMPLES.jsonl'),parent_footprint=parent_footprint(),
                        sampling_target_seconds=2.,sampling_schedule='deadline_minus_work',physical_episodes_started=0)
                    write_new(out/'COUNTER_PROBE.json',probe)
                    final=dict(status='check_only_stable_resources',exit_code=0,
                        counter_probe=fingerprint(out/'COUNTER_PROBE.json'),resource_decision=admission)
                if admission is not None and run:
                    admission.update(historical_profile=profile,empirical_resource_budget_not_scientific_threshold=True,
                        admission_samples=fingerprint(out/'ADMISSION_SAMPLES.jsonl'))
                    write_new(out/'ADMISSION.json',admission)
                    env=child_environment(out/'temp'/TARGET)
                    # Exact inherited runner command and environment; no new
                    # registration, altered solver, model or shortened window.
                    command=[sys.executable,'-B','-m','tools.load_accounting.runner','run','--directory',f'{RUN_REL}/experiments/{TARGET}']
                    require_unstarted(RUN/'experiments'/TARGET);require_no_science(*active_science())
                    write_new(out/(TARGET+'.command.json'),dict(command=command,cwd=str(ROOT),utc=now(),admission=admission,
                        environment_overrides={k:env[k] for k in ('TEMP','TMP','PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING','PYTHONPATH')},new_registration_created=False))
                    resource=monitor_child(command,out,env,launches)
                    require(resource['exit_code']==0,'Registered008 scientific child failed; retain original attempt and stop')
                    post=short_qualification(out,'postflight')
                    require(post.get('actual_complete_pairs')==406 and post.get('actual_complete_batches')==148,'Strict postflight406 failed')
                    directory=RUN/'experiments'/TARGET;terminal=read(directory/'BATCH_RESULT.json');record_path=next((directory/'pairs').glob('*/RESULT.json'));record=read(record_path)
                    proof=next(p for p in post['batch_proofs'] if p['batch']==TARGET)
                    resource.update(passed=True,error=None,admission=admission,admission_receipt=fingerprint(out/'ADMISSION.json'),
                        qualification=dict(qualified=True,batch=TARGET,scenario_id=record['scenario_id'],policy_id=record['policy_id'],
                            window_minutes=proof['window_minutes'],result_sha256=sha(record_path),terminal_sha256=sha(directory/'BATCH_RESULT.json'),raw_sha256=record['raw_sha256']))
                    write_new(out/(TARGET+'.result.json'),resource)
                    launches[-1]['resource_result_sha']=sha(out/(TARGET+'.result.json'))
                    report=full_report(out,baseline_root);proof=short_qualification(out,'final_qualification')
                    require(proof.get('actual_complete_pairs')==406 and proof.get('actual_complete_batches')==148,'Final strict406 proof failed')
                    recheck_rows(pins['evidence_files'])
                    final=dict(status='passed',exit_code=0,strict_batches=148,qualified_pairs=406,
                        predecessor=pins,resource_result=fingerprint(out/(TARGET+'.result.json')),
                        preflight=fingerprint(out/'preflight.json'),postflight=fingerprint(out/'postflight.json'),
                        final_406_proof=fingerprint(out/'FINAL_406_PROOF.json'),final_report=report,
                        admission=fingerprint(out/'ADMISSION.json'),normal009_resource_peak_unknown=True,
                        old_measured_resource_batches=10,new_measured_resource_batches=1)
    except BaseException as e:
        final=dict(status='failed_preserve_evidence',exit_code=2,error_type=type(e).__name__,error=str(e),no_automatic_retry=True)
    final.update(schema='hospital_ies.v20.normal008_continuation_result.v1',utc=now(),pid=os.getpid(),cwd=str(Path.cwd()),scientific_root=str(ROOT),
        script_sha256=sha(Path(__file__)),actual_scientific_children_started=sum(x['successful_Popen'] for x in launches),
        successful_Popen_proofs=launches,last_child=launches[-1] if launches else None,
        physical_attempt_status='inspect actual immutable008 terminal' if launches else 'not_started_by_this_controller',
        old_STOP_preserved=True,no_frozen_science_modified=True,global_serial_science=True)
    write_new(out/'SUMMARY.json',final);print(json.dumps(final,ensure_ascii=True));return final['exit_code']

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);mode=p.add_mutually_exclusive_group(required=True)
    for name in ('qualify405','qualify406','check-only','run'):mode.add_argument('--'+name,action='store_true')
    p.add_argument('--receipt',type=Path);p.add_argument('--name');p.add_argument('--baseline-root',type=Path)
    p.add_argument('--stop-sha256');p.add_argument('--plan-sha256');p.add_argument('--resource-window-seconds',type=float,default=30.)
    p.add_argument('--reserve-gib',type=int,choices=(2,3),default=2);p.add_argument('--max-wait-seconds',type=float,default=0.)
    p.add_argument('--counter-probe-seconds',type=float,default=0.)
    a=p.parse_args(argv)
    if a.qualify405 or a.qualify406:
        require(a.receipt is not None,'Exclusive qualification receipt path required')
        try:
            result=qualify405() if a.qualify405 else qualify406();write_new(a.receipt,result)
            print(json.dumps({k:v for k,v in result.items() if k not in ('batch_proofs','evidence_files')},ensure_ascii=True));return 0
        except BaseException as e:
            if not a.receipt.exists():write_new(a.receipt,dict(status='failed_preserve_evidence',utc=now(),error_type=type(e).__name__,error=str(e),physical_episodes_started=0))
            raise
    require(a.name and a.baseline_root is not None,'Continuation name and complete V19 baseline root required')
    return continuation(a.name,run=a.run,baseline_root=a.baseline_root,stop_sha=a.stop_sha256,plan_sha=a.plan_sha256,
        window_seconds=a.resource_window_seconds,reserve_gib=a.reserve_gib,max_wait_seconds=a.max_wait_seconds,counter_probe_seconds=a.counter_probe_seconds)

if __name__=='__main__':raise SystemExit(main())
