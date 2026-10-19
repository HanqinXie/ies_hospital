"""Publication-only resource admission for the twelve frozen normal jobs.

No registration, scenario, production code or solver setting is changed here.
--check-only never starts physics. --run must be explicitly requested.
"""
from __future__ import annotations
import argparse
import csv
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
PROFILE_DIR=RUN/'provenance/normal_resource_profile'
PROFILE_PATH=PROFILE_DIR/'V19_R2_PROFILE.json'
SOURCE_SUMMARY_PATH=PROFILE_DIR/'V19_R2_SOURCE_SUMMARY.json'
R2_SUMMARY_SHA256='193d9ad2bbe8a67359e07200a64530d761f181eaf62b9fb16b5423a9a8734c77'
MODEL='M20_load_accounting'
GIB=1024**3
GROWTH=1.2
RESERVE_BYTES=6*GIB
STREAM_FIELDS={'start_hour','duration_steps','step_minutes','faults','shock','shock_by_priority',
    'exogenous_events','enable_propagation','propagation_enabled','grid_limit_kW','pre_event_grid_limit_kW',
    'pre_event_warmup_steps','response_delay_minutes','automatic_fast_shedding','initialization_seed'}
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def now():return datetime.now(timezone.utc).isoformat()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()
def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):value.update(block)
    return value.hexdigest()
def require(value,message):
    if not value:raise ValueError(message)
def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
def journal(path,**value):
    row=dict(value)
    # Resource samples already carry their observation time. Event rows without
    # one receive a write-time timestamp; never overwrite the observed sample.
    row.setdefault('utc',now())
    with Path(path).open('a',encoding='utf8') as stream:
        stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n');stream.flush()
def is_sha(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None
def stream_hash(scene):
    return digest({k:v for k,v in scene.items() if k in STREAM_FIELDS or k.startswith(('fault_','shock_'))})


def resource_admission(profile,memory):
    require(profile.get('source_model_id')=='M19_model_closeout','Unproved historical resource model')
    fields=('OS_lifetime_peak_working_set_bytes','sampled_private_peak_bytes')
    require(all(type(profile.get(k)) is int and profile[k]>0 for k in fields),'Unobserved resource profile')
    required_physical=math.ceil(GROWTH*profile[fields[0]])+RESERVE_BYTES
    required_commit=math.ceil(GROWTH*profile[fields[1]])+RESERVE_BYTES
    physical=memory.get('available_physical_bytes');commit=memory.get('commit_headroom_bytes')
    physical_ok=type(physical) is int and physical>=required_physical
    commit_ok=type(commit) is int and commit>=required_commit
    return dict(admitted=physical_ok and commit_ok,physical_ok=physical_ok,commit_ok=commit_ok,
        required_physical_bytes=required_physical,required_commit_headroom_bytes=required_commit,
        observed_available_physical_bytes=physical,observed_commit_headroom_bytes=commit,
        historical_model_id='M19_model_closeout',is_new_M20_peak_measurement=False,
        growth_factor=GROWTH,host_reserve_bytes=RESERVE_BYTES)


def select_admitted_job(jobs,memory):
    decisions={job['name']:resource_admission(job['profile'],memory) for job in jobs}
    ordered=sorted(jobs,key=lambda j:(j['profile']['sampled_private_peak_bytes'],j['profile']['OS_lifetime_peak_working_set_bytes'],j['name']))
    selected=next((job for job in ordered if decisions[job['name']]['admitted']),None)
    return selected,decisions


def dispatch_decision(*,comparisons_qualified,active_science,enumeration_issues,admission):
    if enumeration_issues:return dict(start=False,state='waiting_process_enumeration_unknown')
    if active_science:return dict(start=False,state='waiting_other_scientific_child')
    if not comparisons_qualified:return dict(start=False,state='waiting_comparisons_terminal_qualification')
    if not admission.get('admitted'):return dict(start=False,state='waiting_physical_or_commit_resources')
    return dict(start=True,state='admitted')


def validate_registration(directory,job,freeze,freeze_sha256):
    plan=read(directory/'plan.json');manifest=read(directory/'manifest.json')
    require(job['cohort']=='normal' and re.fullmatch(r'v20_normal_\d{3}',job['name']) is not None,'Only existing normal jobs are authorized')
    require(len(job['policies'])==1,'Only one registered policy per normal process')
    alias=job['policies'][0];spec=freeze['policies'][alias]
    require(plan.get('model_id')==manifest.get('execution_model_id')==freeze.get('model_id')==MODEL,'Registered model differs')
    require(plan.get('source_sha256')==manifest.get('source_sha256')==freeze['source_sha256'],'Registered source differs')
    require(plan.get('model_config')==manifest.get('model_config')==freeze['model_config'],'Registered configuration differs')
    require(manifest.get('model_config_signature')==digest(freeze['model_config']),'Registered configuration signature differs')
    require(manifest.get('files')==freeze['runtime_inputs'],'Registered input inventory differs')
    require(manifest.get('signature')==digest({k:v for k,v in manifest.items() if k!='signature'}),'Registered manifest signature differs')
    require(plan.get('manifest_sha256')==sha(directory/'manifest.json') and plan.get('manifest_signature')==manifest['signature'],
            'Actual plan/manifest binding differs')
    require(plan.get('phase')=='normal' and plan.get('registered_pairs')==1,'Normal registration scope differs')
    require(plan.get('policy_specs')==[spec] and manifest.get('policies')==[spec['policy_id']]
        and manifest.get('policy_specs_sha256')==digest([spec]),'Registered frozen policy specification differs')
    require(manifest.get('freeze_signature')==freeze['signature']
        and manifest.get('source_freeze_artifact',{}).get('sha256')==freeze_sha256,'Registered freeze binding differs')
    scenes=manifest.get('scenarios');require(isinstance(scenes,list) and len(scenes)==1,'Single normal specification required')
    scene=scenes[0];expected=job['scene']
    require(scene.get('id')==expected.get('id') and plan.get('scenario_ids')==[scene['id']]
        and stream_hash(scene)==stream_hash(expected)
        and scene.get('physical_scenario_signature')==expected.get('physical_scenario_signature')
        and scene.get('full_source_support')==expected.get('full_source_support')
        and scene.get('source_day_index')==expected.get('source_day_index')
        and scene.get('normal_duration_hours')==expected.get('normal_duration_hours'),
        'Registered physical specification differs from frozen source proposal')
    require(scene.get('is_final_blind') is not True and scene.get('original_final12_evaluated') is not True
        and manifest.get('original_final12_evaluated') is not True,'Sealed final backgrounds are not authorized')
    return plan,manifest


def _attempts(path):
    require(path.is_file(),'Actual terminal lacks attempt journal')
    rows=[]
    for number,line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(),1):
        if not line.strip():continue
        row=json.loads(line);require(isinstance(row,dict),'Malformed attempt journal row '+str(number));rows.append(row)
    return rows


def qualify_complete(directory,job,freeze,freeze_sha256):
    plan,manifest=validate_registration(directory,job,freeze,freeze_sha256)
    terminal=read(directory/'BATCH_RESULT.json');spec=plan['policy_specs'][0];scene=manifest['scenarios'][0]
    require(terminal.get('plan_sha256')==sha(directory/'plan.json') and terminal.get('manifest_sha256')==sha(directory/'manifest.json')
        and terminal.get('source_unchanged') is True,'Actual terminal plan/source binding differs')
    require(terminal.get('registered_pairs')==terminal.get('actual_attempts')==terminal.get('complete_full')==1
        and terminal.get('unknown_full')==0 and terminal.get('analysis_failure_or_unavailable_count')==0
        and terminal.get('status_counts')=={'completed':1},'Actual terminal counts/status are not complete')
    path=directory/'pairs'/(scene['id']+'__'+spec['policy_id'])/'RESULT.json'
    record=read(path);attempts=_attempts(directory/'ATTEMPTS.jsonl')
    require(len(attempts)==2 and {r.get('event') for r in attempts}=={'started','finished'},'Actual attempts are not unique')
    require(all(r.get('scenario_id')==scene['id'] and r.get('policy_id')==spec['policy_id'] for r in attempts),'Actual attempt identity differs')
    from scripts.v20_report import execution_qualification
    qualified,reasons=execution_qualification(record,attempts,path,audit_key='v20')
    require(qualified,'Actual terminal execution qualification failed: '+str(reasons))
    require(record.get('manifest_signature')==manifest['signature'] and record.get('model_config_signature')==manifest['model_config_signature'],
            'Actual terminal RESULT binding differs')
    require(record.get('model_id')==MODEL and record.get('scenario_id')==scene['id'] and record.get('policy_id')==spec['policy_id']
        and is_sha(record.get('raw_sha256')),'Actual terminal RESULT/raw identity differs')
    end=float(scene['duration_steps'])*float(scene.get('step_minutes',15.));analysis=record['analysis']
    require(analysis.get('observed_until_minute')==end,'Actual terminal observed coverage differs')
    window=analysis['v18']['windows']['registered_full'];ledger=analysis['v20']['service_ledgers']['registered_full']
    for value in (window,ledger):
        require(value.get('complete') is True and value.get('window_minutes')==[0.,end]
            and value.get('unknown_tail_minutes') is None and value.get('all_registered') is not None,
            'Actual terminal complete accounting window is unavailable')
    require(ledger.get('observed_finish_minute')==end,'Actual terminal ledger end differs')
    for priority in ('P0','P1','P2','P3'):
        for carrier in ('e','h','c'):
            values=ledger['all_registered'].get(priority,{}).get(carrier,{})
            require(all(type(values.get(field)) in (int,float) and math.isfinite(values[field])
                for field in ('requested_kWh','delivered_kWh','ENS_kWh','disconnected_ENS_kWh','connected_unserved_ENS_kWh')),
                'Actual terminal service accounting is not completely observed')
    evidence=terminal.get('matching_evidence',{}).get(scene['id']) or {}
    require(evidence.get('same_initial_state') is True and evidence.get('same_exogenous_definition') is True,'Actual terminal matching failed')
    for field in ('initial_state_sha256','applied_exogenous_definition_sha256'):
        require(isinstance(evidence.get(field),list) and len(evidence[field])==1 and is_sha(evidence[field][0]),'Actual terminal matching fingerprint unavailable')
    require(evidence['applied_exogenous_definition_sha256'][0]==stream_hash(scene),'Actual terminal exogenous definition differs')
    return dict(qualified=True,batch=job['name'],scenario_id=scene['id'],policy_id=spec['policy_id'],window_minutes=[0.,end],
        terminal_sha256=sha(directory/'BATCH_RESULT.json'),result_sha256=sha(path),raw_sha256=record['raw_sha256'],
        injection_state_sha256=evidence.get('injection_state_sha256'),
        fault_injection_not_applicable_to_normal=scene.get('first_event_minute') is None)


def existing_state(directory,job,freeze,freeze_sha256):
    validate_registration(directory,job,freeze,freeze_sha256)
    if (directory/'BATCH_RESULT.json').exists():
        try:return dict(state='skip_strictly_qualified_complete',proof=qualify_complete(directory,job,freeze,freeze_sha256))
        except Exception as exc:raise ValueError('Prior terminal is not strictly qualified; never overwrite: '+str(exc)) from exc
    if any((directory/name).exists() for name in ('ATTEMPTS.jsonl','pairs')):
        raise ValueError('Prior partial attempt must be retained; never overwrite '+job['name'])
    return dict(state='not_started',proof=None)


def prepare_historical_profile(source_root):
    source=Path(source_root).resolve(strict=True)/'outputs/v19_model_closeout_20261018_run01'
    summary_path=source/'review/resources_r2/SUMMARY.json';summary=read(summary_path)
    require(sha(summary_path)==R2_SUMMARY_SHA256,'Historical R2 summary differs from the reviewed evidence')
    require(summary.get('schema')=='hospital_ies.v19.resource_monitor.r2' and summary.get('exit_code')==0,'Unknown V19 resource monitor evidence')
    profiles=[]
    for peak in summary['peaks']:
        match=re.fullmatch(r'normal_(E|K1|C19)_final_r1_(\d{3})',peak['batch'])
        if not match:continue
        manifest_path=source/'experiments'/peak['batch']/'manifest.json';manifest=read(manifest_path)
        scene=manifest['scenarios'][0]
        require(manifest.get('execution_model_id')=='M19_model_closeout','Historical resource identity differs')
        profile=dict(alias=match.group(1),source_model_id='M19_model_closeout',is_M20_measurement=False,
            original_batch=peak['batch'],scenario_id=scene['id'],source_day_index=scene['source_day_index'],
            duration_minutes=float(scene['duration_steps'])*float(scene.get('step_minutes',15.)),
            physical_scenario_signature=scene.get('physical_scenario_signature'),samples=peak['samples'],
            OS_lifetime_peak_working_set_bytes=peak['OS_lifetime_peak_working_set_bytes'],
            sampled_working_set_peak_bytes=peak['sampled_working_set_peak_bytes'],sampled_private_peak_bytes=peak['sampled_private_peak_bytes'],
            first_sample_utc=peak['first_R2_sample_utc'],last_sample_utc=peak['last_R2_sample_utc'],
            original_manifest_sha256=sha(manifest_path))
        resource_admission(profile,{});profiles.append(profile)
    require(len(profiles)==12 and len({(p['alias'],p['scenario_id']) for p in profiles})==12,'Historical evidence must cover twelve distinct normal pairs')
    result=dict(schema='hospital_ies.v20.external_normal_resource_profile.v1',source_model_id='M19_model_closeout',
        is_M20_measurement=False,source_summary_path=str(summary_path),source_summary_sha256=sha(summary_path),
        profiles=profiles,private_is_committed_process_memory_not_RSS=True,
        peaks_are_observations_not_new_upper_bounds=True,growth_factor=GROWTH,host_reserve_bytes=RESERVE_BYTES)
    result['signature']=digest(result)
    if SOURCE_SUMMARY_PATH.exists():
        require(sha(SOURCE_SUMMARY_PATH)==R2_SUMMARY_SHA256,'Existing immutable historical source receipt differs')
    else:
        SOURCE_SUMMARY_PATH.parent.mkdir(parents=True,exist_ok=True)
        with SOURCE_SUMMARY_PATH.open('xb') as stream:stream.write(summary_path.read_bytes())
    if PROFILE_PATH.exists():
        require(read(PROFILE_PATH)==result,'Existing immutable historical profile differs')
    else:write_new(PROFILE_PATH,result)
    return result


def load_profile():
    value=read(PROFILE_PATH)
    require(value.get('signature')==digest({k:v for k,v in value.items() if k!='signature'}),'Historical profile signature differs')
    require(value.get('source_model_id')=='M19_model_closeout' and value.get('is_M20_measurement') is False,'Historical profile identity differs')
    require(value.get('growth_factor')==GROWTH and value.get('host_reserve_bytes')==RESERVE_BYTES,'Historical admission margin differs')
    require(len(value.get('profiles',[]))==12,'Historical profile coverage differs')
    require(value.get('source_summary_sha256')==R2_SUMMARY_SHA256,'Historical R2 evidence fingerprint is not reviewed')
    require(SOURCE_SUMMARY_PATH.is_file() and sha(SOURCE_SUMMARY_PATH)==R2_SUMMARY_SHA256,'Historical R2 source receipt unavailable or changed')
    peaks={p['batch']:p for p in read(SOURCE_SUMMARY_PATH)['peaks']}
    require(len({(p.get('alias'),p.get('scenario_id')) for p in value['profiles']})==12,'Historical profile pairs are not unique')
    for profile in value['profiles']:
        match=re.fullmatch(r'normal_(E|K1|C19)_final_r1_(\d{3})',profile.get('original_batch',''))
        require(match is not None and match.group(1)==profile.get('alias'),'Historical policy association differs')
        peak=peaks.get(profile['original_batch']) or {}
        for field in ('OS_lifetime_peak_working_set_bytes','sampled_working_set_peak_bytes','sampled_private_peak_bytes','samples'):
            require(profile.get(field)==peak.get(field) and type(profile.get(field)) is int and profile[field]>0,
                    'Historical measured resource value differs: '+field)
    return value


def windows_memory():
    require(os.name=='nt','Windows resource counters are required; commit is not guessed')
    class Memory(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(k,ctypes.c_ulonglong) for k in
            ('total_phys','avail_phys','total_page','avail_page','total_virtual','avail_virtual','avail_extended')]
    class Performance(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong)]+[(k,ctypes.c_size_t) for k in
            ('CommitTotal','CommitLimit','CommitPeak','PhysicalTotal','PhysicalAvailable','SystemCache',
             'KernelTotal','KernelPaged','KernelNonpaged','PageSize')]+[(k,ctypes.c_ulong) for k in ('HandleCount','ProcessCount','ThreadCount')]
    memory=Memory();memory.length=ctypes.sizeof(memory)
    require(bool(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))),'GlobalMemoryStatusEx failed')
    performance=Performance();performance.cb=ctypes.sizeof(performance)
    require(bool(ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(performance),performance.cb)),'GetPerformanceInfo failed')
    page=int(performance.PageSize);committed=int(performance.CommitTotal)*page;limit=int(performance.CommitLimit)*page
    return dict(utc=now(),total_physical_bytes=int(memory.total_phys),available_physical_bytes=int(memory.avail_phys),
        committed_bytes=committed,commit_limit_bytes=limit,commit_headroom_bytes=limit-committed,
        counter_basis='GlobalMemoryStatusEx physical; GetPerformanceInfo commit pages times page size')


def active_scientific_children():
    import psutil
    active=[];issues=[]
    for pid in psutil.pids():
        try:
            process=psutil.Process(pid)
            if re.fullmatch(r'python(?:w|\d+(?:\.\d+)*)?(?:\.exe)?',process.name(),re.I) is None:continue
            args=process.cmdline()
            require(isinstance(args,list) and bool(args),'Unknown Python command line')
            found=any(args[i:i+3]==['-m','tools.load_accounting.runner','run'] for i in range(len(args)-2))
            if found:active.append(dict(pid=pid,create_time=process.create_time(),command=args))
        except psutil.NoSuchProcess:continue
        except (psutil.Error,OSError,ValueError) as exc:
            issues.append(dict(pid=pid,error_type=type(exc).__name__,error=str(exc)))
    return active,issues


def verify_frozen_project():
    from tools.load_accounting import runner as producer,data_design
    from tools.load_accounting.gate import test_binding
    path=RUN/'MODEL_FREEZE_ACCOUNTING01.json';freeze=read(path)
    require(freeze.get('kind')=='v20_final_shared_model_freeze' and freeze.get('signature')==digest({k:v for k,v in freeze.items() if k!='signature'}),'Invalid final M20 freeze')
    require(producer.source_binding()==freeze['source_sha256'],'Frozen production bytes changed')
    require(test_binding()==freeze['source_bound_test_gate']['test_files_sha256'],'Frozen test bytes changed')
    require(data_design.runtime_input_inventory(ROOT)==freeze['runtime_inputs'],'Frozen runtime input bytes changed')
    provenance=freeze.get('original_provenance') or {}
    require(provenance.get('path') and sha(ROOT/provenance['path'])==provenance.get('sha256'),'Starting provenance bytes changed')
    for row in provenance['record']['source_files']:
        require(sha(ROOT/row['repository_path'])==row['sha256'],'Frozen source-proposal provenance changed: '+row['repository_path'])
    return freeze,sha(path)


def normal_jobs(profile):
    from scripts.v20_campaign import campaign_jobs
    jobs=[j for j in campaign_jobs() if j['cohort']=='normal']
    require(len(jobs)==12 and all(len(j['policies'])==1 for j in jobs),'Expected twelve registered single-policy normal jobs')
    for job in jobs:
        matches=[p for p in profile['profiles'] if p['alias']==job['policies'][0] and p['scenario_id']==job['scene']['id']
            and p['duration_minutes']==job['scene']['duration_steps']*job['scene'].get('step_minutes',15.)
            and p['physical_scenario_signature']==job['scene'].get('physical_scenario_signature')]
        require(len(matches)==1,'No unique observed historical profile for '+job['name'])
        original_index=int(matches[0]['original_batch'].rsplit('_',1)[1])
        original=read(RUN/'provenance/v19_design'/('NORMAL_'+job['policies'][0]+'_FINAL_R1_PROPOSAL.json'))['scenarios'][original_index]
        require(original['id']==job['scene']['id'] and stream_hash(original)==stream_hash(job['scene']),
                'Historical resource case is associated with a different registered specification')
        job['profile']=matches[0]
    return sorted(jobs,key=lambda j:(j['profile']['sampled_private_peak_bytes'],j['profile']['OS_lifetime_peak_working_set_bytes'],j['name']))


def comparison_gate():
    from scripts.v20_report import collect_campaign
    value=collect_campaign();rows=[r for r in value['status'] if r['cohort']!='normal']
    require(len(rows)==394,'Comparison denominator differs')
    pending=[r['batch']+'__'+str(r['alias']) for r in rows if not (r['registered_full_complete'] and r['comparison_eligible'])]
    failed=[dict(batch=r['batch'],alias=r['alias'],status=r['status'],reasons=r['execution_disqualification_reasons'])
        for r in rows if r['actual_finished'] and not r['execution_qualified']]
    return dict(qualified=not pending,expected_pairs=394,qualified_pairs=394-len(pending),pending=pending,failed=failed,
        report_issues=value['summary']['issues'])


@contextmanager
def serial_lease():
    import msvcrt
    PROFILE_DIR.mkdir(parents=True,exist_ok=True)
    with (PROFILE_DIR/'normal_serial.lock').open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as exc:raise ValueError('Another normal supervisor owns the serial lease') from exc
        try:yield
        finally:
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def _process_sample(process,psutil):
    memory=windows_memory();memory['pid']=process.pid
    try:
        info=psutil.Process(process.pid).memory_info()
        memory.update(working_set_bytes=int(info.rss),private_bytes=int(info.private),OS_lifetime_peak_working_set_bytes=int(info.peak_wset),
                      process_sample_status='measured')
    except (psutil.Error,OSError,AttributeError) as exc:
        memory.update(working_set_bytes=None,private_bytes=None,OS_lifetime_peak_working_set_bytes=None,
                      process_sample_status='unknown',sample_error_type=type(exc).__name__,sample_error=str(exc))
    return memory


def run_one(job,out,freeze,freeze_sha256,admission,sample_seconds,*,launches=None):
    import psutil
    # Recheck registration and serial exclusion immediately before Popen.
    directory=RUN/'experiments'/job['name'];state=existing_state(directory,job,freeze,freeze_sha256)
    require(state['state']=='not_started','Existing evidence cannot be executed again')
    active,issues=active_scientific_children()
    if active or issues:
        return dict(batch=job['name'],not_started=True,
            state='waiting_process_enumeration_unknown' if issues else 'waiting_other_scientific_child',
            active_science=active,enumeration_issues=issues)
    last_memory=windows_memory();last_admission=resource_admission(job['profile'],last_memory)
    if not last_admission['admitted']:
        return dict(batch=job['name'],not_started=True,state='waiting_physical_or_commit_resources',admission=last_admission)
    env=os.environ.copy();temp=out/'temp'/job['name'];temp.mkdir(parents=True,exist_ok=False)
    env.update(TEMP=str(temp),TMP=str(temp),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',
               PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT))
    command=[sys.executable,'-B','-m','tools.load_accounting.runner','run','--directory',directory.relative_to(ROOT).as_posix()]
    write_new(out/(job['name']+'.command.json'),dict(command=command,cwd=str(ROOT),utc=now(),admission=last_admission,
        prior_checkpoint_admission=admission,
        environment_overrides={k:env[k] for k in ('TEMP','TMP','PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING','PYTHONPATH')},
        prior_registration_unchanged=True,new_registration_created=False))
    samples=[];global_serial_issues=[];began=time.monotonic()
    launch=dict(batch=job['name'],pid=None,successful_Popen=False,owned_child_drained=False)
    interrupted=None;diagnostic_errors=[];drain_errors=[]
    with (out/(job['name']+'.log')).open('xb') as log:
        if launches is not None:launches.append(launch)
        process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        # Every operation after successful Popen, including the first journal
        # write, is covered by drain. Logging must never release the serial
        # lease while an owned scientific child is still running.
        try:
            launch['successful_Popen']=True
            launch['pid']=process.pid
            journal(out/'EVENTS.jsonl',event='scientific_child_started',batch=job['name'],pid=process.pid,command=command)
            while process.poll() is None:
                sample=_process_sample(process,psutil);sample['batch']=job['name'];samples.append(sample)
                journal(out/'SAMPLES.jsonl',**sample)
                others,unknown=active_scientific_children()
                others=[p for p in others if p['pid']!=process.pid]
                if others or unknown:
                    item=dict(other_science=others,enumeration_unknown=unknown,action='drain already-started owned child; no more launch')
                    global_serial_issues.append(item);journal(out/'EVENTS.jsonl',event='global_serial_conflict',batch=job['name'],**item)
                time.sleep(sample_seconds)
        except BaseException as exc:
            interrupted=dict(error_type=type(exc).__name__,error=str(exc))
            try:
                journal(out/'EVENTS.jsonl',event='supervisor_interrupted',batch=job['name'],**interrupted,
                    action='drain owned scientific process; never terminate or overwrite its evidence')
            except BaseException as diagnostic_exc:
                diagnostic_errors.append(dict(error_type=type(diagnostic_exc).__name__,error=str(diagnostic_exc)))
            raise
        finally:
            # Retry interrupted OS waits without terminating the child. No
            # diagnostic I/O is allowed to bypass this ownership boundary.
            while True:
                try:
                    process.wait();break
                except BaseException as drain_exc:
                    drain_errors.append(dict(error_type=type(drain_exc).__name__,error=str(drain_exc)))
                    try:time.sleep(.5)
                    except BaseException:pass
            launch.update(owned_child_drained=True,exit_code=process.returncode,drain_wait_errors=drain_errors)
            if interrupted is not None:
                receipt=dict(utc=now(),**launch,**interrupted,diagnostic_write_errors=diagnostic_errors,
                    qualification='unknown; inspect original registered scientific evidence; never overwrite')
                try:write_new(out/(job['name']+'.interrupted.json'),receipt)
                except BaseException as diagnostic_exc:
                    # A full/unwritable disk can also prevent this independent
                    # receipt. Preserve that fact in the outer in-memory proof.
                    diagnostic_errors.append(dict(error_type=type(diagnostic_exc).__name__,error=str(diagnostic_exc)))
                launch['interruption']=receipt
    code=process.returncode;qualification=None;error=None
    try:
        require(code==0,'Scientific child exit code '+str(code))
        qualification=qualify_complete(directory,job,freeze,freeze_sha256)
        require(not global_serial_issues,'Other scientific process appeared during normal window')
    except Exception as exc:error=dict(type=type(exc).__name__,message=str(exc))
    measured=[s for s in samples if s['process_sample_status']=='measured']
    result=dict(batch=job['name'],exit_code=code,elapsed_seconds=time.monotonic()-began,qualification=qualification,error=error,
        passed=error is None,resource_samples=len(samples),unknown_process_samples=len(samples)-len(measured),
        sampled_private_peak_bytes=max((s['private_bytes'] for s in measured),default=None),
        sampled_working_set_peak_bytes=max((s['working_set_bytes'] for s in measured),default=None),
        observed_OS_lifetime_peak_working_set_bytes=max((s['OS_lifetime_peak_working_set_bytes'] for s in measured),default=None),
        minimum_observed_available_physical_bytes=min((s['available_physical_bytes'] for s in samples),default=None),
        minimum_observed_commit_headroom_bytes=min((s['commit_headroom_bytes'] for s in samples),default=None),
        measurements_are_M20=True,original_V19_admission_profile_not_overwritten=True,global_serial_issues=global_serial_issues)
    write_new(out/(job['name']+'.result.json'),result)
    journal(out/'EVENTS.jsonl',event='scientific_child_finished',**result)
    return result


def supervise(name,*,run=False,baseline_root=None,poll_seconds=15.,sample_seconds=2.,max_wait_seconds=0.):
    require(re.fullmatch('[A-Za-z0-9_]+',name) is not None,'Use a fresh simple supervisor name')
    require(1<=poll_seconds<=60 and .5<=sample_seconds<=5 and max_wait_seconds>=0,'Bounded polling/sampling intervals required')
    out=PROFILE_DIR/('supervision_'+name);out.mkdir(parents=True,exist_ok=False)
    write_new(out/'START.json',dict(utc=now(),command=sys.argv,mode='run_existing_registered_normals' if run else 'check_only_no_physics',
        supervisor_sha256=sha(Path(__file__)),growth_factor=GROWTH,host_reserve_bytes=RESERVE_BYTES,
        max_wait_seconds=max_wait_seconds,original_final12_unopened=True))
    final=None;pending=[];completed=[];skipped=[];launches=[]
    try:
        profile=prepare_historical_profile(baseline_root) if baseline_root else load_profile()
        freeze,freeze_sha256=verify_frozen_project();jobs=normal_jobs(profile)
        existing={j['name']:existing_state(RUN/'experiments'/j['name'],j,freeze,freeze_sha256) for j in jobs}
        pending=[j for j in jobs if existing[j['name']]['state']=='not_started'];skipped=[j['name'] for j in jobs if j not in pending]
        memory=windows_memory();active,enumeration_issues=active_scientific_children();comparisons=comparison_gate()
        selected,admissions=select_admitted_job(pending,memory)
        plan=[dict(batch=j['name'],alias=j['policies'][0],duration_minutes=j['profile']['duration_minutes'],
            historical_profile=j['profile'],existing=existing[j['name']],admission=admissions.get(j['name'])) for j in jobs]
        write_new(out/'PLAN.json',dict(memory=memory,active_science=active,enumeration_issues=enumeration_issues,
            comparisons=comparisons,jobs=plan,profile_sha256=sha(PROFILE_PATH),freeze_sha256=freeze_sha256))
        if not run:
            final=dict(mode='check_only_no_physics',exit_code=0,actual_scientific_children_started=0,
                existing_qualified_skipped=skipped,pending_not_started=[j['name'] for j in pending],
                currently_admissible_by_resources=[j['name'] for j in pending if admissions[j['name']]['admitted']],
                dispatch=dispatch_decision(comparisons_qualified=comparisons['qualified'],active_science=active,
                    enumeration_issues=enumeration_issues,admission=admissions[selected['name']] if selected else {'admitted':False}))
        else:
            completed=[];wait_started=time.monotonic()
            with serial_lease():
                while pending:
                    freeze,freeze_sha256=verify_frozen_project()
                    # A different owner may have completed a registered normal
                    # before this supervisor acquired the lease; never rerun it.
                    refreshed={j['name']:existing_state(RUN/'experiments'/j['name'],j,freeze,freeze_sha256) for j in pending}
                    newly_skipped=[j['name'] for j in pending if refreshed[j['name']]['state']=='skip_strictly_qualified_complete']
                    skipped.extend(newly_skipped);pending=[j for j in pending if j['name'] not in newly_skipped]
                    if not pending:break
                    if (PROFILE_DIR/'STOP_NORMAL_SERIAL.json').exists():
                        final=dict(mode='run',exit_code=125,state='explicit_stop_new_launches',completed=completed,
                            actual_scientific_children_started=len(completed),existing_qualified_skipped=skipped,
                            pending_not_started=[j['name'] for j in pending]);break
                    active,enumeration_issues=active_scientific_children();comparisons=comparison_gate();memory=windows_memory()
                    selected,admissions=select_admitted_job(pending,memory)
                    decision=dispatch_decision(comparisons_qualified=comparisons['qualified'],active_science=active,
                        enumeration_issues=enumeration_issues,admission=admissions[selected['name']] if selected else {'admitted':False})
                    journal(out/'EVENTS.jsonl',event='admission_checkpoint',memory=memory,comparisons=comparisons,
                        active_science=active,enumeration_issues=enumeration_issues,decision=decision,
                        admissions=admissions,pending_not_started=[j['name'] for j in pending])
                    if comparisons['failed'] and not active:
                        raise ValueError('Comparison failure prevents dependent normal launch; all evidence retained')
                    if not decision['start']:
                        if max_wait_seconds>0 and time.monotonic()-wait_started>=max_wait_seconds:
                            final=dict(mode='run',exit_code=3,state='waiting_without_start_not_failed',completed=completed,
                                actual_scientific_children_started=len(completed),existing_qualified_skipped=skipped,
                                pending_not_started=[j['name'] for j in pending],last_checkpoint=decision);break
                        time.sleep(poll_seconds);continue
                    # Admission is case-specific. A smaller admitted case may
                    # proceed while a larger one remains explicitly waiting.
                    result=run_one(selected,out,freeze,freeze_sha256,admissions[selected['name']],sample_seconds,launches=launches)
                    if result.get('not_started'):
                        journal(out/'EVENTS.jsonl',event='launch_deferred_without_physics',**result)
                        time.sleep(poll_seconds);continue
                    completed.append(result);pending=[j for j in pending if j['name']!=selected['name']]
                    require(result['passed'],'Normal scientific child failed qualification; stop remaining dependent launches')
                    wait_started=time.monotonic()
                if final is None:
                    final=dict(mode='run',exit_code=0,state='all_twelve_strictly_qualified',completed=completed,
                        actual_scientific_children_started=len(completed),existing_qualified_skipped=skipped,pending_not_started=[])
    except Exception as exc:
        final=dict(mode='run' if run else 'check_only_no_physics',exit_code=2,state='blocked_preserve_evidence',
            error_type=type(exc).__name__,error=str(exc),no_partial_evidence_overwritten=True,
            completed=completed,existing_qualified_skipped=skipped,pending_not_started=[j['name'] for j in pending])
    except BaseException as exc:
        final=dict(mode='run' if run else 'check_only_no_physics',exit_code=130,state='supervisor_interrupted_owned_child_drained',
            error_type=type(exc).__name__,error=str(exc),completed=completed,existing_qualified_skipped=skipped,
            pending_not_started=[j['name'] for j in pending],no_partial_evidence_overwritten=True)
    event_path=out/'EVENTS.jsonl'
    try:
        events=[json.loads(line) for line in event_path.read_text(encoding='utf8').splitlines()] if event_path.exists() else []
        journal_starts=sum(r.get('event')=='scientific_child_started' for r in events)
    except (ValueError,OSError):
        journal_starts=None
    # A failed/missing start journal is not evidence that Popen did not happen.
    # This supervisor's successful Popen facts survive into its final summary.
    successful_launches=[item for item in launches if item['successful_Popen']]
    final['actual_scientific_children_started']=len(successful_launches)
    final['scientific_start_count_basis']='successful_Popen_in_memory'
    final['journal_scientific_children_started']=journal_starts
    final['scientific_start_count_status']='journal_agrees' if journal_starts==len(successful_launches) else 'journal_unknown_or_disagrees; use successful_Popen proof'
    final['successful_Popen_proofs']=successful_launches
    final['Popen_not_successful']=[item for item in launches if not item['successful_Popen']]
    started_names={item['batch'] for item in successful_launches};completed_names={item['batch'] for item in completed}
    final['pending_not_started']=[name for name in final.get('pending_not_started',[]) if name not in started_names]
    final['started_without_completed_supervisor_result']=sorted(started_names-completed_names)
    final['finished_utc']=now();final['original_final12_unopened']=True
    write_new(out/'SUMMARY.json',final);print(json.dumps(dict(output_directory=str(out),**final),ensure_ascii=True),flush=True)
    return final['exit_code']


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--check-only',action='store_true');mode.add_argument('--run',action='store_true')
    parser.add_argument('--name',required=True);parser.add_argument('--baseline-root',type=Path)
    parser.add_argument('--poll-seconds',type=float,default=15.);parser.add_argument('--sample-seconds',type=float,default=2.)
    parser.add_argument('--max-wait-seconds',type=float,default=0.)
    a=parser.parse_args(argv)
    return supervise(a.name,run=a.run,baseline_root=a.baseline_root,poll_seconds=a.poll_seconds,
                     sample_seconds=a.sample_seconds,max_wait_seconds=a.max_wait_seconds)


if __name__=='__main__':raise SystemExit(main())
