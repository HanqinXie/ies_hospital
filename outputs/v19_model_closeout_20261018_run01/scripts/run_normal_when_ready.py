"""One-shot authorized serial normals after successful non-normal closeout.

This external orchestration helper never registers, retries, edits frozen inputs,
or changes a physical parameter. Its memory floor is admission evidence, not a
bound on a long trajectory's future peak. The child scheduler owns its usual
exclusive cooperative lease; manual external processes are outside that lease.
"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid

B = Path(__file__).resolve().parent.parent
ROOT = B.parents[1]
PANELS = ('normal_E_final_r1','normal_K1_final_r1','normal_C19_final_r1')
FINALS = ('long_final_r1','propagation_final_r1','sensitivity_open1_r1',
    'sensitivity_contact025_r1','main_final_r1','compat_final_r1',
    'compat_long_final_r1','confirmation_final_r1')
FREEZE_SHA = '80815ef85196a05e496b48b3340a843a00079a7c0de425cbffd13c949e52e2f7'
CERT_SHA = '506da73d2f18cb689db43297de14f1cf9155d7d4c32ab77b75e167cd89e84ab2'
SCHEDULER_SHA = '3f20c63c5d16751877b856ff724b2db73e5fc133faa82d849648b2d7102ad9fc'


def now(): return datetime.now(timezone.utc).isoformat()


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): value.update(block)
    return value.hexdigest()


def write_new(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)


def stop_check(b):
    for name in ('STOP_NORMAL_FINAL_AUTORUN.json','STOP_FROZEN_QUEUE.json',
            'STOP_CLOSEOUT_AUTORUN_R2.json'):
        if (b/name).exists(): raise RuntimeError('Explicit stop: '+name)


def verify_non_normal(b):
    path=b/'FROZEN_QUEUE_NON_NORMAL_FINAL01_RESULT.json'
    value=read(path)
    results=value.get('results',[])
    if (value.get('all_succeeded') is not True or value.get('registered_pairs')!=394 or
        value.get('registered_batches')!=136 or len(results)!=136 or
        value.get('unstarted_batches')!=[] or value.get('freeze_sha256')!=FREEZE_SHA):
        raise ValueError('Expected exactly 394 trajectories / 136 batches, all accepted')
    expected={}
    for panel in FINALS:
        for row in read(b/(panel.upper()+'_REGISTRATION.json')):
            directory=Path(row['directory']).resolve()
            if directory.parent!=(b/'experiments').resolve() or directory in expected:
                raise ValueError('Noncanonical or duplicate non-normal batch')
            expected[directory]=row['registered_pairs']
    if len(expected)!=136 or sum(expected.values())!=394:
        raise ValueError('Fixed non-normal registration changed')
    seen=set()
    for result in results:
        directory=Path(result['directory']).resolve()
        verified=result.get('verified_terminal',{})
        if (directory not in expected or directory in seen or result.get('accepted') is not True or
            result.get('exit_code')!=0 or verified.get('source_unchanged') is not True or
            verified.get('registered_pairs')!=expected[directory] or
            verified.get('complete_full')!=expected[directory] or
            verified.get('batch_result_sha256')!=sha(directory/'BATCH_RESULT.json')):
            raise ValueError('Missing, changed, or unaccepted non-normal terminal')
        seen.add(directory)
    command=read(b/'logs/frozen_non_normal_final01.command.json')
    if command.get('exit_code')!=0: raise ValueError('Non-normal logged command failed')
    return dict(verified_pairs=394,verified_batches=136,queue_sha256=sha(path),
        command_receipt_sha256=sha(b/'logs/frozen_non_normal_final01.command.json'))


def readiness(b):
    stop_check(b)
    queue=b/'FROZEN_QUEUE_NON_NORMAL_FINAL01_RESULT.json'
    if queue.exists() and read(queue).get('all_succeeded') is not True:
        raise ValueError('Non-normal queue failed; no normal launch')
    result=b/'CLOSEOUT_AUTORUN_R2_RESULT.json'
    if not result.exists(): return dict(ready=False,reason='waiting_for_r2_terminal')
    if read(result).get('all_succeeded') is not True:
        raise ValueError('Closeout continuation failed; no normal launch')
    proof=verify_non_normal(b)
    if (b/'PHYSICAL_QUEUE_LEASE.json').exists():
        return dict(ready=False,reason='cooperative_scheduler_lease_present')
    return dict(ready=True,r2_sha256=sha(result),non_normal=proof)


def verify_certificate(b,root):
    final=b/'MODEL_FREEZE_CLOSEOUT01.json'
    cert=b/'review/FINAL_CONFIRMATION_INDEPENDENT_CERTIFICATE.json'
    if sha(final)!=FREEZE_SHA or sha(cert)!=CERT_SHA or read(cert).get('status')!='passed':
        raise ValueError('Reviewed final freeze or isolation certificate changed')
    frozen=read(final)
    source=frozen['source_sha256']
    if len(source)!=345: raise ValueError('Frozen source inventory changed')
    for name,wanted in source.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path)!=wanted:
            raise ValueError('Frozen source changed: '+name)
    if sha(b/'scripts/run_frozen_panels.py')!=SCHEDULER_SHA:
        raise ValueError('Reviewed external scheduler changed')
    return dict(freeze_sha256=FREEZE_SHA,certificate_sha256=CERT_SHA,
        scheduler_sha256=SCHEDULER_SHA,source_files_verified=len(source))


def normal_rows(b,root):
    result=[]
    for panel in PANELS:
        rows=read(b/(panel.upper()+'_REGISTRATION.json'))
        if len(rows)!=4: raise ValueError('Four normal scenes per policy required')
        for i,row in enumerate(rows):
            directory=Path(row['directory']).resolve()
            if directory!=(b/'experiments'/f'{panel}_{i:03d}').resolve():
                raise ValueError('Canonical normal batch sequence required')
            if row.get('registered_pairs')!=1 or read(directory/'plan.json').get('registered_pairs')!=1:
                raise ValueError('Exactly one policy trajectory per normal batch required')
            if any((directory/name).exists() for name in ('BATCH_RESULT.json','ATTEMPTS.jsonl','pairs')):
                raise FileExistsError('Existing attempt cannot be retried: '+directory.name)
            result.append(dict(directory=str(directory),registered_pairs=1,
                plan_sha256=sha(directory/'plan.json')))
    if len({row['directory'] for row in result})!=12: raise ValueError('Twelve distinct normals required')
    return result


def probe_host(psutil_module=None):
    if psutil_module is None:
        import psutil as psutil_module
    vm=psutil_module.virtual_memory()
    snapshot=dict(utc=now(),physical_total_bytes=vm.total,available_bytes=vm.available,
        physical_free_bytes=vm.free,python_processes=[],m19_workers=[],
        unreadable_python_cmdlines=[],process_probe_errors=[])
    for pid in psutil_module.pids():
        try:
            process=psutil_module.Process(pid)
            name=process.name()
        except (psutil_module.Error,OSError) as error:
            snapshot['process_probe_errors'].append(dict(pid=pid,type=type(error).__name__,message=str(error)))
            continue
        if not name.lower().startswith('python'): continue
        item=dict(pid=pid,name=name)
        try:
            memory=process.memory_info()
            item.update(rss_bytes=memory.rss,private_bytes=getattr(memory,'private',None))
        except (psutil_module.Error,OSError) as error:
            item['memory_read_error']=dict(type=type(error).__name__,message=str(error))
        snapshot['python_processes'].append(item)
        try: argv=process.cmdline() or []
        except (psutil_module.Error,OSError) as error:
            snapshot['unreadable_python_cmdlines'].append(dict(pid=pid,type=type(error).__name__,message=str(error)))
            continue
        for i in (1,2,3):
            if (i+2<len(argv) and argv[i:i+3]==['-m','tools.model_closeout.runner','run'] and
                all(v in ('-B','-u') for v in argv[1:i])):
                snapshot['m19_workers'].append(dict(pid=pid,argv=argv,**{k:v for k,v in item.items() if k!='pid'}))
                break
    values=[p['private_bytes'] for p in snapshot['python_processes'] if p.get('private_bytes') is not None]
    snapshot['readable_python_private_total_bytes']=sum(values)
    snapshot['private_metric_scope']='Readable Python processes only; not host committed memory'
    return snapshot


def admit_host(snapshot,min_available_bytes):
    if snapshot['m19_workers']: raise RuntimeError('Another M19 physical worker remains')
    if snapshot['unreadable_python_cmdlines']: raise RuntimeError('Python command line unreadable; cannot establish admission')
    if snapshot['available_bytes']<min_available_bytes:
        raise MemoryError('Host available memory below declared admission floor; no launch')


def scheduler_argv(b,plan_only=False):
    argv=[sys.executable,'-B',str(b/'scripts/run_frozen_panels.py'),
        '--label','normal_final01','--normal','--workers','1']
    for panel in PANELS: argv+=['--panel',panel]
    return argv+(['--plan-only'] if plan_only else [])


def logged(name,argv,record):
    command=[sys.executable,'-B',str(ROOT/'tools/p0_audit/run_logged.py'),
        '--run-dir',str(B),'--name',name,'--',*argv]
    stop_check(B)
    result=subprocess.run(command,cwd=ROOT,check=False)
    record['commands'].append(dict(name=name,command=command,exit_code=result.returncode))
    if result.returncode: raise RuntimeError('Recorded child failed: '+name)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--max-wait-hours',type=float,default=48.)
    parser.add_argument('--min-available-gib',type=float,default=16.)
    args=parser.parse_args()
    if not (0<args.max_wait_hours<=48 and args.min_available_gib>=16):
        raise ValueError('Finite wait <=48h and admission floor >=16GiB required')
    destination=B/'NORMAL_FINAL_AUTORUN_RESULT.json'
    if destination.exists(): raise FileExistsError(destination)
    owner=B/'NORMAL_ADMISSION_OWNER.json'
    token=dict(token=uuid.uuid4().hex,pid=os.getpid(),started_utc=now())
    write_new(owner,token)
    review=B/'review/normal_admission';review.mkdir(exist_ok=True)
    events=review/'NORMAL_ADMISSION_EVENTS.jsonl'
    record=dict(schema='hospital_ies.v19.normal_authorized_autorun.v1',utc_started=now(),
        script_sha256=sha(__file__),panels=PANELS,commands=[],
        independent_reproduction=False,normal_admission_floor_bytes=int(args.min_available_gib*2**30),
        admission_is_not_a_future_peak_bound=True,cooperative_lease_scope_only=True)
    try:
        record['reviewed_source']=verify_certificate(B,ROOT)
        deadline=time.monotonic()+args.max_wait_hours*3600
        last_event=-float('inf')
        while True:
            state=readiness(B)
            if time.monotonic()-last_event>=60 or state['ready']:
                with events.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(dict(utc=now(),**state))+'\n')
                last_event=time.monotonic()
            if state['ready']: break
            if time.monotonic()>=deadline: raise TimeoutError('Finite readiness wait elapsed; no launch')
            time.sleep(5)
        record['trigger']=state
        record['registered_normals']=normal_rows(B,ROOT)
        record['resource_before_preflight']=probe_host()
        admit_host(record['resource_before_preflight'],record['normal_admission_floor_bytes'])
        logged('preflight_frozen_normal_final01',scheduler_argv(B,True),record)
        record['reviewed_source_before_launch']=verify_certificate(B,ROOT)
        record['registered_normals_before_launch']=normal_rows(B,ROOT)
        record['trigger_before_launch']=readiness(B)
        if not record['trigger_before_launch']['ready']: raise RuntimeError('Readiness changed before launch')
        record['resource_before_launch']=probe_host()
        admit_host(record['resource_before_launch'],record['normal_admission_floor_bytes'])
        write_new(review/'NORMAL_ADMISSION_ACCEPTED.json',record)
        logged('frozen_normal_final01',scheduler_argv(B),record)
        output=B/'FROZEN_QUEUE_NORMAL_FINAL01_RESULT.json'
        value=read(output)
        if (value.get('all_succeeded') is not True or value.get('registered_batches')!=12 or
            value.get('registered_pairs')!=12 or value.get('normal_serial') is not True or
            value.get('workers')!=1 or len(value.get('results',[]))!=12 or
            any(v.get('accepted') is not True for v in value['results']) or value.get('unstarted_batches')!=[]):
            raise ValueError('Serial normal queue not completely successful')
        record.update(all_succeeded=True,normal_queue_sha256=sha(output))
    except Exception as error:
        import traceback
        record.update(all_succeeded=False,error=dict(type=type(error).__name__,message=str(error),traceback=traceback.format_exc()))
    finally:
        record['utc_finished']=now()
        write_new(destination,record)
        if read(owner)!=token: raise RuntimeError('Admission owner changed; preserve evidence')
        owner.unlink()
    return 0 if record.get('all_succeeded') is True else 1


if __name__=='__main__': raise SystemExit(main())
