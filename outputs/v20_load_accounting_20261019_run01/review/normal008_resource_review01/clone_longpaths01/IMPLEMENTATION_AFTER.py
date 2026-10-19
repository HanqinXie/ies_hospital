"""Create a new GitHub main clone and retain the actual clone command receipt."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
URL='https://github.com/HanqinXie/ies_hospital.git'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_new(path,value):
    with Path(path).open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)

def git_value(directory,*args):
    return subprocess.check_output(['git',*args],cwd=directory).decode('utf8').strip()

def clone_environment(inherited):
    """Append one command-scoped override; never disclose inherited Git values."""
    env=dict(inherited);raw=env.get('GIT_CONFIG_COUNT','0')
    if not isinstance(raw,str) or not raw.isascii() or not raw.isdecimal():
        raise ValueError('Inherited Git config count is invalid')
    count=int(raw)
    if count>len(env)//2 or any('GIT_CONFIG_'+part+'_'+str(i) not in env
            for i in range(count) for part in ('KEY','VALUE')):
        raise ValueError('Inherited Git config pairs are incomplete')
    env.update(GIT_TERMINAL_PROMPT='0',GIT_CONFIG_COUNT=str(count+1))
    env['GIT_CONFIG_KEY_'+str(count)]='core.longpaths'
    env['GIT_CONFIG_VALUE_'+str(count)]='true'
    return env,dict(name='core.longpaths',value='true',inherited_count=count,
        appended_index=count,preserved_existing_pairs=True)

def clone(destination):
    target=Path(destination)
    if not target.is_absolute():raise ValueError('Use a new absolute D/E clone destination')
    target=target.resolve()
    if target.exists():raise FileExistsError('Clone destination already exists; do not overwrite evidence')
    if os.name=='nt' and target.drive.lower() not in ('d:','e:'):raise ValueError('Clone data uses D/E')
    if target.is_relative_to(ROOT) or ROOT.is_relative_to(target):raise ValueError('Use an independent clone root')
    marker_path=ROOT/'publication/V20_RELEASE_ROOT.json'
    if not marker_path.is_file():raise ValueError('Launch cloning from the prepared/published release tree')
    marker=json.loads(marker_path.read_text(encoding='utf-8-sig'))
    if marker.get('status')!='final':raise ValueError('Wait for the final publication tree')
    for name in ('source_root','original_scientific_root','original_research_root'):
        original=Path(marker[name]).resolve()
        if target.is_relative_to(original) or original.is_relative_to(target):
            raise ValueError('Clone overlaps an original scientific root')
    env,scoped_config=clone_environment(os.environ)
    target.parent.mkdir(parents=True,exist_ok=True)
    evidence=target.parent/(target.name+'.clone_evidence')
    evidence.mkdir(exist_ok=False)
    command=['git','clone','--branch','main','--single-branch',URL,str(target)]
    started=datetime.now(timezone.utc).isoformat()
    write_new(evidence/'START.json',dict(command=command,cwd=str(target.parent),
        started_utc=started,starting_destination_existed=False,clone_destination=str(target),
        source_launcher_root=str(ROOT),source_launcher_sha256=sha(Path(__file__)),
        clone_scoped_git_config=scoped_config))
    with (evidence/'command.log').open('xb') as log:
        done=subprocess.run(command,cwd=target.parent,env=env,stdout=log,stderr=subprocess.STDOUT,check=False)
    record=dict(command=command,cwd=str(target.parent),started_utc=started,
        finished_utc=datetime.now(timezone.utc).isoformat(),exit_code=done.returncode,
        starting_destination_existed=False,clone_destination=str(target),
        source_launcher_root=str(ROOT),source_launcher_sha256=sha(Path(__file__)),
        source_launcher_commit=None,
        command_log_sha256=sha(evidence/'command.log'),git_commit=None,origin_url=None,branch=None,
        helper_validation_status='failed_unknown',validation_error=None,
        clone_scoped_git_config=scoped_config,local_longpaths_config=None)
    if done.returncode==0:
        try:
            local=dict(command=['git','config','--local','core.longpaths','true'],cwd=str(target),
                started_utc=datetime.now(timezone.utc).isoformat(),finished_utc=None,
                exit_code=None,command_log_sha256=None,error=None)
            record['local_longpaths_config']=local
            try:
                with (evidence/'local_config.log').open('xb') as log:
                    configured=subprocess.run(local['command'],cwd=target,env=env,
                        stdout=log,stderr=subprocess.STDOUT,check=False)
                local['exit_code']=configured.returncode
            except Exception as exc:
                local['error']=dict(type=type(exc).__name__,message=str(exc))
                raise
            finally:
                local['finished_utc']=datetime.now(timezone.utc).isoformat()
                if (evidence/'local_config.log').is_file():local['command_log_sha256']=sha(evidence/'local_config.log')
            if local['exit_code']!=0:raise ValueError('Local Git longpaths configuration exited with code '+str(local['exit_code']))
            # Query failures are helper-validation failures, not clone exits.
            # Keep all successfully observed fields and a terminal COMMAND
            # receipt even when a later Git query or selection check fails.
            record['source_launcher_commit']=git_value(ROOT,'rev-parse','HEAD')
            record['git_commit']=git_value(target,'rev-parse','HEAD')
            record['origin_url']=git_value(target,'remote','get-url','origin')
            record['branch']=git_value(target,'branch','--show-current')
            if record['origin_url']!=URL or record['branch']!='main' or record['git_commit']!=record['source_launcher_commit']:
                raise ValueError('Remote clone does not match the prepared ordinary main commit')
            local=ROOT/'publication/V20_FILE_SELECTION.json';fresh=target/'publication/V20_FILE_SELECTION.json'
            if sha(local)!=sha(fresh):raise ValueError('Remote selection differs from local publication')
            record['helper_validation_status']='passed'
        except Exception as exc:
            record['validation_error']=dict(type=type(exc).__name__,message=str(exc))
    else:
        record['validation_error']=dict(type='CloneCommandFailed',
            message='Clone exited with code '+str(done.returncode)+'; Git queries and helper validation were not attempted')
    write_new(evidence/'COMMAND.json',record)
    if target.is_dir():
        copied=target/'publication_checks/clone01';copied.mkdir(parents=True,exist_ok=False)
        names=['START.json','COMMAND.json','command.log']
        if (evidence/'local_config.log').is_file():names.append('local_config.log')
        for name in names:
            shutil.copyfile(evidence/name,copied/name)
            if sha(evidence/name)!=sha(copied/name):raise ValueError('Clone command receipt copy differs')
    print(json.dumps(record,ensure_ascii=True))
    return 0 if record['helper_validation_status']=='passed' else (done.returncode or 2)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--destination',required=True)
    args=parser.parse_args();return clone(args.destination)

if __name__=='__main__':raise SystemExit(main())
