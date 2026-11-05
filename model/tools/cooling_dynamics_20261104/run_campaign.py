"""Prepare new input dependencies, freeze source bytes, then finite admission.

The prepare/freeze subcommands never construct a simulator or start workers.
Only the explicit run subcommand performs the authorized native trajectories.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

SOURCE=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(SOURCE),str(SOURCE/'src')]
from tools.cooling_dynamics_20261104.extract_results import read,write,sha

DEFAULT_ROOT=Path('E:/hospital_ies_cooling_dynamics_20261104_run01')
OLD_ROOT=Path('E:/hospital_ies_minute1_20261103_run01')


def utc():return datetime.now(timezone.utc).isoformat()


def append(path,value):
    with Path(path).open('a',encoding='utf8',newline='\n') as stream:
        stream.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n');stream.flush();os.fsync(stream.fileno())


def safe_new_root(value):
    root=Path(value)
    if not root.is_absolute() or root.drive.upper()!='E:' or '..' in root.parts:raise ValueError('Explicit absolute E result root required')
    root=root.resolve()
    if root==OLD_ROOT.resolve() or root.is_relative_to(OLD_ROOT.resolve()):raise ValueError('New results must stay outside deletion target')
    if root==Path('E:/') or not root.name.startswith('hospital_ies_cooling_dynamics_20261104_'):raise ValueError('Named new cooling-dynamics result root required')
    return root


def prepare(root):
    root=safe_new_root(root)
    if root.exists() and any(p.name!='component_checks' for p in root.iterdir()):raise FileExistsError('Fresh registration/input/run directories required: '+str(root))
    if shutil.disk_usage(root.parent).free<40*1024**3:raise RuntimeError('Insufficient E space for a fresh finite run')
    inventory=read(SOURCE/'docs/cooling_dynamics_20261104/RUN_AND_CLEANUP_INVENTORY.json')
    root.mkdir(exist_ok=True);(root/'inputs').mkdir();(root/'cases').mkdir();(root/'runs').mkdir();(root/'logs').mkdir()
    migration=[];minute_files={}
    aliases={'minute_loads.csv':'loads','minute_critical_zones.csv':'critical_zones','minute_thermal_components.csv':'thermal_components','MINUTE_SOURCE_METADATA.json':'metadata'}
    for row in inventory['required_core_inputs']:
        old=Path(row['source_path']);new=root/'inputs/native_minute'/old.name;new.parent.mkdir(exist_ok=True)
        if sha(old)!=row['sha256']:raise ValueError('Historical minute input changed before migration')
        shutil.copyfile(old,new)
        if sha(new)!=row['sha256']:raise ValueError('Minute input migration byte mismatch')
        value=dict(path=str(new),sha256=row['sha256'])
        migration.append(dict(role='reused_input_bytes',old_path=str(old),new_path=str(new),sha256=row['sha256'],bytes=new.stat().st_size))
        minute_files[aliases[old.name]]=value
    for row in inventory['recommended_small_input_provenance']:
        old=Path(row['source_path']);new=root/'inputs/origin_input_provenance'/row['relative_path'];new.parent.mkdir(parents=True,exist_ok=True)
        if sha(old)!=row['sha256']:raise ValueError('Historical input provenance changed')
        shutil.copyfile(old,new)
        if sha(new)!=row['sha256']:raise ValueError('Input metadata migration byte mismatch')
        migration.append(dict(role='historical_input_provenance_only',old_path=str(old),new_path=str(new),sha256=row['sha256'],bytes=new.stat().st_size))
    # Recipe source is a new dependency, not an archived old raw trajectory.
    for row in inventory['optional_source_generation_recipes']:
        old=Path(row['source_path']);new=root/'inputs/source_generation_recipes'/old.name;new.parent.mkdir(parents=True,exist_ok=True)
        if sha(old)!=row['sha256']:raise ValueError('Input generation recipe changed')
        shutil.copyfile(old,new)
        migration.append(dict(role='input_source_recipe',old_path=str(old),new_path=str(new),sha256=sha(new),bytes=new.stat().st_size))
    minute_spec=dict(schema='hospital_ies.ep_minute_source_manifest.v1',files=minute_files)
    write(root/'inputs/MINUTE_SOURCE_MANIFEST.json',minute_spec)
    from tools.annual_readiness.registration import load_registry
    from tools.minute1_20261103.registration import make_case,validate_case
    from tools.cooling_dynamics_20261104 import default_config
    oldreg=read(OLD_ROOT/'design/MINUTE_REGISTRATION01.json');registry=load_registry();producers=[]
    for olditem in oldreg['unique_main_producers']:
        oldcase=read(olditem['case_file']);parent=oldcase['minute1']['parent']
        parent_path=root/'inputs/parent_cases'/(olditem['label']+'.json');parent_path.parent.mkdir(exist_ok=True)
        write(parent_path,parent)
        case=make_case(parent,registry,minute_source=minute_spec,variant=olditem['variant'],
            parent_binding=dict(path=str(parent_path),sha256=sha(parent_path)))
        validate_case(case,registry);case_path=root/'cases'/(olditem['label']+'.json');write(case_path,case)
        producers.append(dict(label=olditem['label'],variant=olditem['variant'],start_hour=olditem['start_hour'],
            duration_minutes=olditem['duration_minutes'],fault_target=olditem['fault_target'],case_file=str(case_path),case_sha256=sha(case_path),
            historical_parent_design_sha256=sha(olditem['case_file']),historical_outcome_reused=False))
    registration=dict(schema='hospital_ies.cooling_dynamics.finite_registration.v1',created_utc=utc(),source_root=str(SOURCE),data_root=str(root),
        producers=producers,topics=deepcopy(oldreg['topics']),cooling_config=default_config(),unique_main_producers=19,logical_condition_rows=24,
        T08_reuses_T05_same_continuous_event=True,sampling_minutes=dict(load=1.,physical_max=1.,control=1.),
        old_results_reused_as_new_outcomes=False,original_physical_and_information_boundaries_retained=True,
        numeric_engineering_scope='actual recorded ledger arithmetic; no old primal witness, annual, OEM, hydraulic or clinical qualification')
    if len(producers)!=19 or registration['topics'][7]['producers']!=registration['topics'][4]['producers']:raise ValueError('Original19/24 topic design changed')
    write(root/'REGISTRATION.json',registration)
    write(root/'INPUT_MIGRATION.json',dict(schema='hospital_ies.cooling_dynamics.input_migration.v1',created_utc=utc(),files=migration,
        total_bytes=sum(x['bytes'] for x in migration),old_IES_outcome_files_copied=0,old_raw_outputs_copied=0,
        historical_absolute_paths_in_origin_metadata_are_provenance_only=True,
        original_EnergyPlus_SQL_and_ESO_not_preserved=True,raw_reextraction_requires_regenerating_from_frozen_IDF_weather_and_recipe=True))
    write(root/'RESOURCE_POLICY.json',dict(max_workers=8,initial_workers=4,solver_threads_per_process=1,
        minimum_available_GiB_before_start=10.,pause_admission_below_GiB=8.,boundary_stop_below_GiB=6.,minimum_E_free_GiB=30.,
        heavy_native_primal_audits_used=False,worker_output_temp_stopfile_isolated=True))
    print(json.dumps(dict(status='prepared_only',root=str(root),unique_producers=len(producers),input_bytes=sum(x['bytes'] for x in migration),formal_experiments_started=0)),flush=True)
    return 0


def freeze(root):
    root=safe_new_root(root)
    from tools.sci_engineering.runner import source_binding,input_binding
    registration=read(root/'REGISTRATION.json')
    from tools.cooling_dynamics_20261104 import default_config
    if registration['cooling_config']!=default_config():raise ValueError('Cooling configuration changed since prepare; create a fresh registration')
    paths=[p for folder in ('inputs','cases') for p in (root/folder).rglob('*') if p.is_file()]
    paths += [root/'REGISTRATION.json',root/'RESOURCE_POLICY.json',root/'INPUT_MIGRATION.json']
    git=subprocess.run(['git','rev-parse','HEAD'],cwd=SOURCE,capture_output=True,text=True,check=True).stdout.strip()
    binding=dict(schema='hospital_ies.cooling_dynamics.source_binding.v1',created_utc=utc(),source_root=str(SOURCE),git_base_commit=git,
        exact_source_bytes_are_authoritative=True,source_files=source_binding(SOURCE),runtime_inputs=input_binding(SOURCE),
        new_input_files=[dict(path=str(p),sha256=sha(p),bytes=p.stat().st_size) for p in sorted(paths)],
        old_source_admission_reused=False,scope='Frozen actual code/input identity; engineering ledger output only')
    write(root/'SOURCE_BINDING.json',binding)
    print(json.dumps(dict(status='source_frozen_only',root=str(root),source_files=len(binding['source_files']),sha256=sha(root/'SOURCE_BINDING.json'),formal_experiments_started=0)),flush=True)
    return 0


def run_campaign(root,max_workers=4,labels=None):
    import psutil
    from tools.cooling_dynamics_20261104.run_worker import verify_binding
    root=safe_new_root(root)
    if max_workers<1 or max_workers>8:raise ValueError('Authorized concurrency range is1..8')
    verify_binding(root);registration=read(root/'REGISTRATION.json');known={p['label'] for p in registration['producers']}
    pending=[p['label'] for p in registration['producers'] if labels is None or p['label'] in labels]
    if labels is not None and set(labels)-known:raise ValueError('Unknown producer label')
    selection_path=root/'SELECTION.json';selection=read(selection_path) if selection_path.exists() else dict(schema='hospital_ies.cooling_dynamics.selection.v1',producers={})
    pending=[label for label in pending if label not in selection['producers']]
    stage=root/'logs'/('campaign_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'));stage.mkdir()
    active={};failures=[];stopping=False;last_status=0.;began=time.perf_counter();requested_limit=max_workers;current_limit=min(max_workers,4)
    def save_selection():
        temp=root/'SELECTION.tmp';temp.write_text(json.dumps(selection,indent=2)+'\n',encoding='utf8');os.replace(temp,selection_path)
    try:
        while pending or active:
            available=psutil.virtual_memory().available/1024**3;free=shutil.disk_usage(root).free/1024**3
            if (root/'STOP.request').exists() or available<6. or free<30.:
                stopping=True
                if not (root/'STOP.request').exists():(root/'STOP.request').write_text('Resource guard boundary stop '+utc(),encoding='utf8')
            for label,task in list(active.items()):
                rc=task['process'].poll()
                if rc is None:continue
                task['stdout'].close();task['stderr'].close();producer=Path(task['producer'])
                result=read(producer/'RESULT.json') if (producer/'RESULT.json').is_file() else dict(status='failed',error='Worker exited withoutRESULT')
                event=dict(label=label,attempt=task['attempt'],producer=str(producer),pid=task['process'].pid,create_time=task['create_time'],exit_code=rc,status=result.get('status'),error=result.get('error'),utc=utc())
                append(root/'ATTEMPTS.jsonl',event)
                if rc==0 and result.get('status')=='completed':
                    selection['producers'][label]=dict(producer=str(producer),result_sha256=sha(producer/'RESULT.json'),attempt=task['attempt']);save_selection()
                else:
                    failures.append(event);stopping=True
                    # A known failed producer cancels dependency work at owned
                    # boundaries. No unrelated user process is touched.
                    if not (root/'STOP.request').exists():
                        (root/'STOP.request').write_text(json.dumps(dict(reason='peer_producer_failed',failure=event),ensure_ascii=False)+'\n',encoding='utf8')
                print(json.dumps(event),flush=True);del active[label]
            # Increase only after actual workers have progressed without failure,
            # not from a nominal CPU count or a solver license assumption.
            if current_limit<requested_limit and len(active)>=min(4,requested_limit) and available>=12.:
                progress=[read(Path(v['producer'])/'PROGRESS.json').get('observed_until_minute',0.) if (Path(v['producer'])/'PROGRESS.json').exists() else 0. for v in active.values()]
                if progress and min(progress)>=5.:current_limit=requested_limit
            while pending and not stopping and len(active)<current_limit and psutil.virtual_memory().available>=10*1024**3:
                label=pending.pop(0);attempt=1
                while (root/'runs'/(label+'_attempt'+str(attempt).zfill(2))).exists():attempt+=1
                producer=root/'runs'/(label+'_attempt'+str(attempt).zfill(2))
                command=[sys.executable,'-m','tools.cooling_dynamics_20261104.run_worker','--root',str(root),'--label',label,'--attempt',str(attempt)]
                stdout=(stage/(label+'.stdout.log')).open('xb');stderr=(stage/(label+'.stderr.log')).open('xb')
                environ=dict(os.environ,TEMP=str(root/'temp'),TMP=str(root/'temp'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
                (root/'temp').mkdir(exist_ok=True)
                process=subprocess.Popen(command,cwd=SOURCE,env=environ,stdout=stdout,stderr=stderr,creationflags=0x08000000 if os.name=='nt' else 0)
                created=psutil.Process(process.pid).create_time()
                active[label]=dict(process=process,create_time=created,producer=str(producer),attempt=attempt,stdout=stdout,stderr=stderr)
                append(stage/'STARTS.jsonl',dict(label=label,attempt=attempt,pid=process.pid,create_time=created,command=command,utc=utc()))
                # Avoid admitting the entire wave against one stale RAM sample.
                time.sleep(.5)
            now=time.perf_counter()
            if now-last_status>=30.:
                print(json.dumps(dict(status='running' if not stopping else 'boundary_stopping',active=list(active),pending=len(pending),available_GiB=available,current_limit=current_limit)),flush=True);last_status=now
            if stopping and not active:break
            if not active and pending and not stopping and available<10.:
                # Wait for external memory pressure to ease; never force-close
                # unrelated user applications to create experiment headroom.
                append(stage/'ADMISSION_WAITS.jsonl',dict(available_GiB=available,utc=utc()))
            time.sleep(2.)
    except BaseException:
        (root/'STOP.request').write_text('Owned campaign exception boundary stop '+utc(),encoding='utf8')
        deadline=time.time()+60.
        for task in active.values():
            try:task['process'].wait(timeout=max(.1,deadline-time.time()))
            except subprocess.TimeoutExpired:
                proc=psutil.Process(task['process'].pid)
                if proc.create_time()==task['create_time']:proc.terminate()
            finally:task['stdout'].close();task['stderr'].close()
        raise
    value=dict(status='completed' if not failures and not pending and not stopping else 'incomplete',elapsed_seconds=time.perf_counter()-began,
        selected_producers=len(selection['producers']),failures=failures,pending=pending,all_owned_workers_exited=not active,
        max_workers_requested=max_workers,source_binding_sha256=sha(root/'SOURCE_BINDING.json'))
    write(stage/'RESULT.json',value);print(json.dumps(value),flush=True)
    return 0 if value['status']=='completed' else 1


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('prepare','freeze','run'));parser.add_argument('--root',default=str(DEFAULT_ROOT));parser.add_argument('--max-workers',type=int,default=4);parser.add_argument('--labels',nargs='+');args=parser.parse_args()
    if args.command=='prepare':return prepare(args.root)
    if args.command=='freeze':return freeze(args.root)
    return run_campaign(args.root,args.max_workers,args.labels)


if __name__=='__main__':raise SystemExit(main())
