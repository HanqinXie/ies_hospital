"""Register fixed V20 rechecks and run immutable scenario batches with RAM guards."""
from __future__ import annotations
import argparse
import copy
import ctypes
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'outputs/v20_load_accounting_20261019_run01'
OLD_REL='outputs/v19_model_closeout_20261018_run01'
DESIGN=RUN/'provenance/v19_design'
FREEZE='MODEL_FREEZE_ACCOUNTING01.json'
PANELS=[('main','MAIN_FINAL_R1_PROPOSAL.json',['E','K1','C19']),
    ('long','LONG_FINAL_R1_PROPOSAL.json',['E','K1','C19']),
    ('normal','NORMAL_E_FINAL_R1_PROPOSAL.json',['E','K1','C19']),
    ('compat','COMPAT_FINAL_R1_PROPOSAL.json',['C18_M20']),
    ('compat','COMPAT_LONG_FINAL_R1_PROPOSAL.json',['C18_M20']),
    ('fixed8','CONFIRMATION_FINAL_R1_PROPOSAL.json',['E','K1','C19']),
    ('propagation','PROPAGATION_FINAL_R1_PROPOSAL.json',['E','K1','C19']),
    ('open1','SENSITIVITY_OPEN1_R1_PROPOSAL.json',['E','K1','C19']),
    ('contact025','SENSITIVITY_CONTACT025_R1_PROPOSAL.json',['E','K1','C19'])]

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def write_new(p,value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
def journal(p,**v):
    with Path(p).open('a',encoding='utf-8') as f:f.write(json.dumps(dict(utc=now(),**v),ensure_ascii=False,allow_nan=False)+'\n')

def campaign_jobs(folder=DESIGN):
    folder=Path(folder);jobs=[];indices={}
    for cohort,name,policies in PANELS:
        original=read(folder/name)
        for scene in original['scenarios']:
            panels=[[p] for p in policies] if cohort=='normal' else [policies]
            for selected in panels:
                index=indices.get(cohort,0);indices[cohort]=index+1
                jobs.append(dict(name=f'v20_{cohort}_{index:03d}',cohort=cohort,source_proposal=name,
                    scene=copy.deepcopy(scene),policies=list(selected),normal_serial=cohort=='normal',
                    config_variant=cohort if cohort in ('open1','contact025') else 'base'))
    return jobs

def bootstrap(source):
    source=Path(source).resolve(strict=True);old=source/OLD_REL
    if source==ROOT:raise ValueError('Bootstrap uses the separate complete V19 evidence root')
    names=sorted({p[1] for p in PANELS}|{'NORMAL_K1_FINAL_R1_PROPOSAL.json','NORMAL_C19_FINAL_R1_PROPOSAL.json'})
    copies=[(old/'design'/n,DESIGN/n) for n in names]
    copies += [(old/'M19_MODEL_CONFIG.json',RUN/'M20_MODEL_CONFIG.json'),
        (old/'references/EMISSIONS_FACTOR_REGISTRY.json',RUN/'references/EMISSIONS_FACTOR_REGISTRY.json'),
        (old/'SENSITIVITY_OPEN1_R1_CONFIG.json',RUN/'design/M20_OPEN1_CONFIG.json'),
        (old/'SENSITIVITY_CONTACT025_R1_CONFIG.json',RUN/'design/M20_CONTACT025_CONFIG.json')]
    for src,dst in copies:
        if not src.is_file():raise FileNotFoundError(src)
        if dst.exists() and sha(dst)!=sha(src):raise FileExistsError(dst)
    rows=[]
    for src,dst in copies:
        dst.parent.mkdir(parents=True,exist_ok=True)
        if not dst.exists():shutil.copyfile(src,dst)
        rows.append(dict(source_path=str(src),repository_path=dst.relative_to(ROOT).as_posix(),bytes=dst.stat().st_size,sha256=sha(dst)))
    # All three normal panels must share the exact original physical definitions.
    normal=[read(DESIGN/n)['scenarios'] for n in names if n.startswith('NORMAL_')]
    assert len(normal)==3 and normal[0]==normal[1]==normal[2]
    input_rows=[]
    for p in sorted((ROOT/'inputs').rglob('*')):
        if p.is_file():input_rows.append(dict(path=p.relative_to(ROOT).as_posix(),bytes=p.stat().st_size,sha256=sha(p)))
    record=dict(schema='hospital_ies.v20.starting_provenance.v1',utc=now(),
        starting_commit='01baf68c3e12d0336a5d9d54026ca479a7023b6d',v19_tag='v19-model-closeout',
        source_root=str(source),new_root=str(ROOT),source_files=copies and rows,
        original_input_files=input_rows,raw_input_bytes_changed=False,original_outputs_modified=False,
        expected_comparison_pairs=406,expected_priority_pairs=379,
        fixed_eight_backgrounds_are_seen=True,original_final12_unopened=True)
    write_new(RUN/'provenance/STARTING_POINT.json',record)
    print(json.dumps(dict(bootstrap='complete',copied_metadata_files=len(rows),input_files=len(input_rows))))

def register_campaign(label,freeze_name):
    sys.path[:0]=[str(ROOT),str(ROOT/'src')]
    from tools.load_accounting import runner as r,data_design as d
    frozen=read(RUN/freeze_name);jobs=campaign_jobs();registered=[]
    assert sum(len(j['policies']) for j in jobs)==406
    for job in jobs:
        specs=[r.policy_specs()[alias] for alias in job['policies']]
        path=DESIGN/job['source_proposal']
        proposal=d.panel(job['name'],[job['scene']],
            bindings={path.relative_to(ROOT).as_posix():dict(sha256=sha(path))},
            freeze_signature=frozen['signature'],evaluation_role='seen_fixed_background' if job['cohort']=='fixed8' else 'seen_regression')
        config=copy.deepcopy(frozen['model_config'])
        if job['config_variant']!='base':
            config=read(RUN/'design'/('M20_OPEN1_CONFIG.json' if job['cohort']=='open1' else 'M20_CONTACT025_CONFIG.json'))
            config=r.model_contract(r.MODEL_ID,config)[1]
            sensitivity=dict(schema='hospital_ies.v20.assumption_preregistration.v1',source_freeze_signature=frozen['signature'],
                source_sha256=frozen['source_sha256'],model_id=r.MODEL_ID,model_config=config,policy_specs=specs,
                reason='Repeat the originally registered '+job['cohort']+' execution-clock assumption under corrected V20 loads; no new parameter search')
            sensitivity['signature']=d.digest(sensitivity)
            proposal.pop('signature',None);proposal['sensitivity_registration']=sensitivity;proposal['signature']=d.digest(proposal)
        directory=RUN/'experiments'/job['name']
        r.register(proposal,directory,specs,model_id=r.MODEL_ID,model_config=config,
            phase=job['cohort'],freeze_path=freeze_name)
        registered.append({k:v for k,v in job.items() if k!='scene'})
    result=dict(schema='hospital_ies.v20.campaign_registration.v1',utc=now(),label=label,
        freeze=freeze_name,freeze_sha256=sha(RUN/freeze_name),jobs=registered,
        registered_pairs=sum(len(j['policies']) for j in jobs),registered_batches=len(jobs),
        no_policy_search=True,original_final12_unopened=True)
    write_new(RUN/'design'/('CAMPAIGN_'+label+'.json'),result)
    print(json.dumps(dict(registered_pairs=result['registered_pairs'],registered_batches=len(jobs))))

def available_memory():
    if os.name!='nt':
        pages=os.sysconf('SC_AVPHYS_PAGES');return int(pages*os.sysconf('SC_PAGE_SIZE'))
    class Status(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(k,ctypes.c_ulonglong) for k in
            ('total_phys','avail_phys','total_page','avail_page','total_virtual','avail_virtual','avail_extended')]
    value=Status();value.length=ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):raise OSError('Memory status unavailable')
    return int(value.avail_phys)

def memory_admission(free_bytes,active_count,reserve_gib,job_reserve_gib):
    # Each already-started child retains its full estimated headroom, including
    # the interval after spawn and before it has populated its working set.
    required=(reserve_gib+(active_count+1)*job_reserve_gib)*(1024**3)
    return free_bytes>=required

def terminal_issues(directory,terminal):
    problems=[];directory=Path(directory);count=terminal.get('registered_pairs')
    if not isinstance(count,int) or count<=0:return ['invalid registered count']
    if (terminal.get('actual_attempts')!=count or terminal.get('complete_full')!=count
            or terminal.get('unknown_full')!=0 or terminal.get('analysis_failure_or_unavailable_count')!=0
            or terminal.get('status_counts')!={'completed':count}):problems.append('terminal counts/status are not complete actual attempts')
    try:
        plan=read(directory/'plan.json');manifest=read(directory/'manifest.json')
        if terminal.get('plan_sha256')!=sha(directory/'plan.json') or terminal.get('manifest_sha256')!=sha(directory/'manifest.json'):
            problems.append('terminal plan/manifest SHA differs')
        if count!=len(manifest['scenarios'])*len(plan['policy_specs']):problems.append('registered denominator differs')
        for scene in manifest['scenarios']:
            for spec in plan['policy_specs']:
                p=directory/'pairs'/(scene['id']+'__'+spec['policy_id'])/'RESULT.json'
                record=read(p);analysis=record.get('analysis') or {};audit=(analysis.get('v20') or {}).get('episode_audit') or {}
                if (record.get('status')!='completed' or record.get('error') is not None or record.get('analysis_error') is not None
                        or record.get('scenario_id')!=scene['id'] or record.get('policy_id')!=spec['policy_id']
                        or record.get('model_id')!='M20_load_accounting' or analysis.get('execution_model_id')!='M20_load_accounting'
                        or audit.get('status')!='passed' or audit.get('issues')!=[]
                        or not analysis['v18']['windows']['registered_full']['complete']):
                    problems.append('incomplete, failed, mismatched or unaudited actual pair: '+p.parent.name)
    except Exception as exc:problems.append('actual completion evidence unavailable: '+type(exc).__name__+': '+str(exc))
    return problems

def run_campaign(label,phase,workers,reserve_gib,job_reserve_gib):
    if workers<1 or workers>4:raise ValueError('Bounded measured concurrency is 1..4')
    config=read(RUN/'design'/('CAMPAIGN_'+label+'.json'))
    candidates=[j for j in config['jobs'] if phase=='all' or
        (phase=='normal' and j['normal_serial']) or (phase=='comparisons' and not j['normal_serial']) or
        (phase=='pilot' and j['name']=='v20_main_000')]
    out=RUN/'orchestration'/f'{label}_{phase}_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}'
    out.mkdir(parents=True,exist_ok=False);events=out/'EVENTS.jsonl'
    pending=[];skipped=[]
    for job in candidates:
        directory=RUN/'experiments'/job['name']
        if (directory/'BATCH_RESULT.json').is_file():
            terminal=read(directory/'BATCH_RESULT.json')
            if terminal_issues(directory,terminal):
                raise ValueError('Retain and diagnose existing failed terminal batch: '+job['name'])
            skipped.append(job['name']);continue
        if (directory/'ATTEMPTS.jsonl').exists() or (directory/'pairs').exists():
            raise FileExistsError('Prior incomplete attempt retained; diagnose and explicitly re-register, never overwrite: '+job['name'])
        pending.append(job)
    active=[];done=[];failures=[];pending_saved=[];started=now()
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',
        PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT))
    temp=RUN/'temp'/out.name;temp.mkdir(parents=True);env.update(TEMP=str(temp),TMP=str(temp))
    while pending or active:
        # Large normal windows are globally isolated from every other child.
        limit=1 if active and active[0]['job']['normal_serial'] or pending and pending[0]['normal_serial'] else workers
        while pending and len(active)<limit:
            candidate=pending[0]
            if active and (candidate['normal_serial'] or any(x['job']['normal_serial'] for x in active)):break
            free=available_memory()
            if not memory_admission(free,len(active),reserve_gib,job_reserve_gib):break
            job=pending.pop(0);directory=RUN/'experiments'/job['name']
            cmd=[sys.executable,'-B','-m','tools.load_accounting.runner','run','--directory',directory.relative_to(ROOT).as_posix()]
            log=None
            try:
                log=(out/(job['name']+'.log')).open('xb')
                proc=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            except Exception as exc:
                if log is not None:log.close()
                pending_saved.append(job)
                row=dict(batch=job['name'],process_started=False,error_type=type(exc).__name__,message=str(exc))
                failures.append(row);journal(events,event='launch_failed',**row);break
            active.append(dict(job=job,process=proc,log=log,command=cmd,started=time.perf_counter()))
            journal(events,event='started',batch=job['name'],pid=proc.pid,command=cmd,available_bytes=free)
        for item in list(active):
            proc=item['process'];code=proc.poll()
            if code is None:continue
            item['log'].close();job=item['job'];record=RUN/'experiments'/job['name']/'BATCH_RESULT.json'
            terminal=None
            try:
                terminal=read(record) if record.is_file() else None
                issues=terminal_issues(record.parent,terminal) if terminal is not None else ['no terminal record']
            except Exception as exc:
                terminal=None
                issues=['malformed terminal: '+type(exc).__name__+': '+str(exc)]
            ok=code==0 and not issues
            row=dict(batch=job['name'],exit_code=code,passed=ok,elapsed_seconds=time.perf_counter()-item['started'],
                terminal=terminal and {k:terminal.get(k) for k in ('registered_pairs','actual_attempts','complete_full','unknown_full','status_counts')},validation_issues=issues)
            done.append(row);active.remove(item);journal(events,event='finished',**row)
            print(json.dumps(row),flush=True)
            if not ok:failures.append(row)
        if failures:
            # Finish already-started work, then retain the pending denominator.
            pending_saved.extend(pending);pending=[]
        if not active and not pending:break
        if not active and pending and not memory_admission(available_memory(),0,reserve_gib,job_reserve_gib):
            failures.append(dict(reason='insufficient available memory; no process started',next_batch=pending[0]['name']))
            pending_saved.extend(pending);pending=[];break
        journal(events,event='checkpoint',active=[x['job']['name'] for x in active],pending=len(pending),
            completed=len(done),available_bytes=available_memory())
        time.sleep(10)
    result=dict(schema='hospital_ies.v20.orchestration.v1',started_utc=started,finished_utc=now(),phase=phase,
        configured_workers=workers,reserve_gib=reserve_gib,job_reserve_gib=job_reserve_gib,completed=done,
        skipped_terminal_batches=skipped,failures=failures,pending_retained=[j['name'] for j in pending_saved],
        exit_code=0 if not failures else 2,no_existing_attempts_overwritten=True)
    write_new(out/'RESULT.json',result)
    return result['exit_code']

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    b=sub.add_parser('bootstrap');b.add_argument('--source-root',required=True)
    q=sub.add_parser('register');q.add_argument('--label',default='final01');q.add_argument('--freeze',default=FREEZE)
    r=sub.add_parser('run');r.add_argument('--label',default='final01');r.add_argument('--phase',choices=['pilot','comparisons','normal','all'],required=True)
    r.add_argument('--workers',type=int,default=2);r.add_argument('--reserve-gib',type=float,default=8.)
    r.add_argument('--job-reserve-gib',type=float,default=8.)
    a=p.parse_args()
    if a.mode=='bootstrap':bootstrap(a.source_root);return 0
    if a.mode=='register':register_campaign(a.label,a.freeze);return 0
    return run_campaign(a.label,a.phase,a.workers,a.reserve_gib,a.job_reserve_gib)

if __name__=='__main__':raise SystemExit(main())
