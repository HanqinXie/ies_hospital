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

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qualify405',action='store_true',required=True)
    p.add_argument('--receipt',type=Path,required=True);a=p.parse_args(argv)
    try:
        result=qualify405();write_new(a.receipt,result);print(json.dumps({k:v for k,v in result.items() if k not in ('batch_proofs','evidence_files')},ensure_ascii=True));return 0
    except BaseException as e:
        if not a.receipt.exists():write_new(a.receipt,dict(status='failed_preserve_evidence',utc=now(),error_type=type(e).__name__,error=str(e),physical_episodes_started=0))
        raise

if __name__=='__main__':raise SystemExit(main())
