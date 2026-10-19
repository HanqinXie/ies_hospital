"""Archive a clean published Git tree and qualified post-push proof below 200 MB.

This external layer only reads existing evidence and never starts a replay.
Failed partial archives are retained; existing destinations are never replaced.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile
import zlib

ROOT=Path(__file__).resolve().parents[1]
RUN_REL='outputs/v20_load_accounting_20261019_run01'
CAP=200_000_000
URL='https://github.com/HanqinXie/ies_hospital.git'
PREFIX='Hospital_IES_V20/'
MANIFEST_NAME=PREFIX+'DELIVERY_MANIFEST.json'


def require(value,message):
    if not value:raise ValueError(message)
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def same_path(left,right):
    return isinstance(left,(str,Path)) and Path(left).is_absolute() and Path(left).resolve()==Path(right).resolve()
def time_value(value):
    require(isinstance(value,str),'Missing actual UTC timestamp')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    require(parsed.utcoffset() is not None,'Timestamp lacks timezone')
    return parsed.astimezone(timezone.utc)
def root_set(values):
    require(isinstance(values,list) and values,'Missing forbidden original roots')
    require(all(isinstance(v,str) and Path(v).is_absolute() for v in values),'Invalid original root')
    return {Path(v).resolve() for v in values}


def validate_clone_receipt(root,marker,record,*,head,origin,branch,origin_main,launcher_sha256,log_sha256):
    root=Path(root).resolve()
    require(not same_path(marker.get('staged_root'),root),'Final staging directory is not a remote fresh clone')
    require(origin==record.get('origin_url')==URL and branch==record.get('branch')=='main'
        and head==origin_main==record.get('git_commit')==record.get('source_launcher_commit'),
        'Clone origin/main/HEAD/launcher commit mismatch')
    require(record.get('exit_code')==0 and record.get('helper_validation_status')=='passed'
        and record.get('validation_error') is None,'Actual clone/helper validation failed or unknown')
    require(record.get('starting_destination_existed') is False
        and same_path(record.get('clone_destination'),root) and same_path(record.get('cwd'),root.parent),
        'Clone destination/cwd/overwrite provenance mismatch')
    launcher=record.get('source_launcher_root')
    require(not same_path(launcher,root) and same_path(launcher,marker.get('staged_root')),
        'Clone launcher is not the distinct final staging root')
    require(record.get('source_launcher_sha256')==launcher_sha256
        and record.get('command_log_sha256')==log_sha256,'Clone launcher/log bytes mismatch')
    require(record.get('command')==['git','clone','--branch','main','--single-branch',URL,str(root)],
        'Unexpected actual clone command')
    started=time_value(record.get('started_utc'));finished=time_value(record.get('finished_utc'))
    require(finished>=started,'Reversed clone clock')
    return dict(git_commit=head,origin_url=origin,branch=branch,finished_utc=record['finished_utc'])


def validate_core_receipt(root,record,log,clone_finished):
    root=Path(root).resolve()
    require(record.get('exit_code')==0 and same_path(record.get('cwd'),root)
        and record.get('command')==[sys.executable,'-B','-m','pytest','-q'],
        'Core command/cwd/exit is not the actual default clone gate')
    require(record.get('environment_overrides',{}).get('PYTHONPATH')==str(root/'src')+os.pathsep+str(root),
        'Core PYTHONPATH escapes/current clone differs')
    require(re.search(r'(?m)^463 passed in [^\r\n]+$',log) is not None,
        'Core lacks the actual 463-pass-only terminal summary')
    started=time_value(record.get('started_utc'));finished=time_value(record.get('finished_utc'))
    require(finished>=started>=time_value(clone_finished),'Core predates remote clone or has reversed clocks')


def check_import_paths(rows,root):
    require(isinstance(rows,list) and rows,'Project import evidence missing')
    for row in rows:
        require(isinstance(row,dict) and isinstance(row.get('path'),str)
            and Path(row['path']).is_absolute() and Path(row['path']).resolve().is_relative_to(root),
            'Project import escaped the fresh clone')


def validate_saved_proof(*,root,name,release,proof,reference,replay,comparisons,matching,
                         guard,guard_config,scalar,hashes):
    root=Path(root).resolve();target=RUN_REL+'/experiments/reproduce_'+name
    review=root/RUN_REL/'review'/('independent_repository_'+name)
    scalar_review=root/RUN_REL/'review'/('reproduce_'+name)
    expected_command=[sys.executable,'-B','-m','tools.load_accounting.runner','run','--directory',target]
    require(proof.get('status')=='passed' and proof.get('release_status')==release['marker']['status']=='final'
        and proof.get('error') is None and same_path(proof.get('current_root'),root),
        'Completed final fresh-clone strict proof required')
    require(proof.get('release_marker_sha256')==release['marker_sha256']
        and proof.get('release_selection_sha256')==release['selection_sha256']
        and proof.get('wrapper_sha256')==hashes['reproduction_wrapper']==release['reproduction_wrapper_sha256']
        and proof.get('frozen_scalar_helper_sha256')==hashes['scalar_helper'],
        'Strict proof is not bound to current release/helper bytes')
    require(proof.get('reference')==reference and proof.get('replay')==replay,
        'Saved actual reference/replay differs from independently requalified evidence')
    require(len(comparisons)==94 and all(row.get('passed') is True for row in comparisons)
        and proof.get('strict_scalar_comparisons')==comparisons,'Actual 94 known scalar comparisons differ/fail')
    require(matching.get('matched') is True and len(matching.get('fields',{}))==9
        and all(v is True for v in matching['fields'].values()) and proof.get('matching')==matching,
        'Actual nine-field matching missing, false or stale')
    launches=proof.get('launches')
    require(isinstance(launches,list) and len(launches)==1 and launches[0].get('command')==expected_command
        and same_path(launches[0].get('cwd'),root) and proof.get('actual_registration_directory')==target,
        'Unexpected scientific launch identity/count/cwd')
    forbidden=root_set(release['forbidden_roots']);parent=proof.get('parent_python_read_guard') or {}
    require(parent.get('active_during_parent_execution') is True and same_path(parent.get('root'),root)
        and parent.get('violations')==[] and type(parent.get('audited_python_operations')) is int
        and parent['audited_python_operations']>0 and root_set(parent.get('forbidden_roots'))==forbidden,
        'Parent Python guard failed, unknown or covered different roots')
    require(guard.get('status')=='passed' and guard.get('violations')==[] and same_path(guard.get('root'),root)
        and root_set(guard.get('forbidden_roots'))==forbidden
        and isinstance(guard.get('token'),str) and bool(guard['token'])
        and guard['token']==guard_config.get('token')==launches[0].get('guard_token')
        and same_path(guard_config.get('root'),root)
        and root_set(guard_config.get('forbidden_roots'))==forbidden
        and same_path(guard_config.get('receipt'),review/'CHILD_GUARD.json')
        and same_path(proof.get('child_guard_path'),review/'CHILD_GUARD.json')
        and proof.get('child_guard_sha256')==hashes['child_guard'],
        'Child guard bytes/token/root/violations mismatch')
    check_import_paths(guard.get('project_imports'),root)
    check_import_paths(proof.get('postflight_project_imports'),root)
    require(same_path(proof.get('scalar_comparison_proof'),scalar_review/'REPRODUCTION_PROOF.json')
        and proof.get('frozen_scalar_helper_returncode')==0
        and scalar.get('status')=='passed' and scalar.get('exit_code')==0 and scalar.get('metric_count')==94
        and scalar.get('registered_duration_minutes')==600. and scalar.get('command')==expected_command
        and same_path(scalar.get('current_root'),root)
        and same_path(scalar.get('actual_registration_directory'),root/target)
        and scalar.get('comparisons')==comparisons,'Frozen scalar proof differs from actual requalification')


def validate_git_entries(tree,index,expected,working_hashes):
    require(set(tree)==set(index)==set(working_hashes)==set(expected),'Git HEAD/index/selection census differs')
    for name,entry in tree.items():
        require(entry['mode'] in ('100644','100755') and index[name]==entry,'Index differs from HEAD or contains link/submodule')
        require(working_hashes[name]==entry['oid'],'Working bytes differ from committed blob: '+name)


def git_entries(data,index=False):
    result={}
    for row in data.split(b'\0'):
        if not row:continue
        header,name=row.split(b'\t',1);parts=header.decode('ascii').split();name=name.decode('utf8')
        require(name not in result,'Duplicate Git member')
        if index:require(parts[2]=='0','Unmerged index conflict entry')
        else:require(parts[1]=='blob','Non-blob Git tree entry')
        result[name]={'mode':parts[0],'oid':parts[1] if index else parts[2]}
    return result


def git_blob_hash(path,object_format):
    require(object_format in ('sha1','sha256'),'Unsupported Git object format')
    h=hashlib.new(object_format);h.update(('blob '+str(path.stat().st_size)+'\0').encode('ascii'))
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()


def prepare(name):
    require(re.fullmatch('[A-Za-z0-9_]+',name or '') is not None,'Use existing fresh-clone replay name')
    spec=importlib.util.spec_from_file_location('release_proof_validator',ROOT/'publication/reproduce_v20.py')
    validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
    release=validator.validate_release(ROOT)
    require(release['marker']['status']=='final','Only final publication tree may be delivered')
    git=lambda *args:subprocess.check_output(['git',*args],cwd=ROOT)
    value=lambda *args:git(*args).decode('utf8').strip()
    head=value('rev-parse','HEAD');origin=value('remote','get-url','origin');branch=value('branch','--show-current')
    clone_dir=ROOT/'publication_checks/clone01';clone=read(clone_dir/'COMMAND.json')
    clone_facts=validate_clone_receipt(ROOT,release['marker'],clone,head=head,origin=origin,branch=branch,
        origin_main=value('rev-parse','refs/remotes/origin/main'),launcher_sha256=sha(ROOT/'publication/clone_release.py'),
        log_sha256=sha(clone_dir/'command.log'))
    core=ROOT/'publication_checks/core01'
    validate_core_receipt(ROOT,read(core/'COMMAND.json'),(core/'command.log').read_text(encoding='utf-8-sig'),clone['finished_utc'])
    proof_dir=ROOT/RUN_REL/'review'/('independent_repository_'+name)
    proof=read(proof_dir/'INDEPENDENT_REPRODUCTION_PROOF.json')
    require(time_value(proof.get('utc'))>=time_value(clone['finished_utc']),'Scientific proof predates remote clone')
    with validator.PythonReadGuard(ROOT,release['forbidden_roots']) as audit:
        again,freeze,repository,report,campaign,reference,_=validator.prepare(ROOT)
        require(again['marker_sha256']==release['marker_sha256'] and again['selection_sha256']==release['selection_sha256'],
            'Release changed during delivery validation')
        bundle=validator.load_bundle(ROOT,RUN_REL+'/experiments/reproduce_'+name,freeze,repository,report,campaign)
        replay=validator.qualify_evidence(bundle,freeze,release['marker']['freeze_sha256'])
        matching=validator.match_qualified_pairs(reference,replay)
        comparisons=repository.compare_metrics(replay['metrics'],reference['metrics'])
        guard=read(proof_dir/'CHILD_GUARD.json');config=read(proof_dir/'child_guard/guard_config.json')
        scalar=read(ROOT/RUN_REL/'review'/('reproduce_'+name)/'REPRODUCTION_PROOF.json')
        validate_saved_proof(root=ROOT,name=name,release=release,proof=proof,reference=reference,replay=replay,
            comparisons=comparisons,matching=matching,guard=guard,guard_config=config,scalar=scalar,
            hashes=dict(reproduction_wrapper=sha(ROOT/'publication/reproduce_v20.py'),
                scalar_helper=sha(ROOT/'scripts/v20_repository.py'),child_guard=sha(proof_dir/'CHILD_GUARD.json')))
        raw=validator.safe(ROOT,RUN_REL+'/experiments/reproduce_'+name+'/pairs/'+replay['scenario_id']+'__'+replay['policy_id']+'/trajectory.json.gz')
        require(raw.is_file() and sha(raw)==replay['actual_raw_sha256'],'Actual fresh raw bytes SHA differs (not archived)')
        require(not audit.violations,'Requalification attempted original-tree reads')
    selection=read(ROOT/'publication/V20_FILE_SELECTION.json')
    expected={row['path'] for row in selection['files']}|{'publication/V20_FILE_SELECTION.json'}
    require(not git('diff','--no-ext-diff','--name-only','-z','HEAD','--')
        and not git('diff','--no-ext-diff','--cached','--name-only','-z','--'),'Tracked working tree/index dirty')
    tree=git_entries(git('ls-tree','-r','-z','--full-tree','HEAD'))
    index=git_entries(git('ls-files','--stage','-z'),index=True)
    object_format=value('rev-parse','--show-object-format')
    working={name:git_blob_hash(validator.safe(ROOT,name),object_format) for name in tree}
    validate_git_entries(tree,index,expected,working)
    names=set(tree)
    # Keep compact post-push check/failure logs; temporary tests and raw stay local.
    for folder in (proof_dir,ROOT/RUN_REL/'review'/('reproduce_'+name),ROOT/'publication_checks'):
        for path in folder.rglob('*'):
            if path.is_file() and 'temp' not in path.relative_to(folder).parts:
                require(path.suffix.lower() in ('.json','.jsonl','.log','.txt','.md','.py','.csv'),
                    'Unexpected noncompact check evidence: '+str(path))
                names.add(path.relative_to(ROOT).as_posix())
    actual=ROOT/RUN_REL/'experiments'/('reproduce_'+name)
    names.update(p.relative_to(ROOT).as_posix() for p in actual.rglob('*') if p.is_file()
        and p.name in ('plan.json','manifest.json','BATCH_RESULT.json','ATTEMPTS.jsonl','RESULT.json'))
    rows=[]
    for name in sorted(names):
        path=validator.safe(ROOT,name)
        require(path.is_file() and not path.is_symlink(),'Missing/linked delivery member: '+name)
        require(path.suffix.lower() not in ('.zip','.gz','.7z','.sql','.sqlite','.lic','.pdf','.xlsx'),
            'Excluded raw/archive/source original/license: '+name)
        rows.append(dict(path=name,bytes=path.stat().st_size,sha256=sha(path)))
    facts=dict(clone=clone_facts,clone_receipt_sha256=sha(clone_dir/'COMMAND.json'),
        core_command_sha256=sha(core/'COMMAND.json'),strict_proof_sha256=sha(proof_dir/'INDEPENDENT_REPRODUCTION_PROOF.json'),
        release_marker_sha256=release['marker_sha256'],release_selection_sha256=release['selection_sha256'],
        scientific_attempts_started_by_archive_builder=0,core_proof_scope='Actual core command/cwd/PYTHONPATH; not OS sandbox',
        replay_guard_scope='Recorded CPython auditing and project paths; native I/O outside audit-hook guarantee')
    return rows,head,proof_dir,facts


def validate_archive_size(size):require(type(size) is int and 0<=size<CAP,'Archive must be below 200,000,000 bytes')


def verify_zip(archive,rows,manifest_bytes):
    expected={PREFIX+row['path']:row for row in rows}
    require(len(expected)==len(rows) and MANIFEST_NAME not in expected,'Duplicate/reserved payload member')
    expected[MANIFEST_NAME]=dict(path='DELIVERY_MANIFEST.json',bytes=len(manifest_bytes),
        sha256=hashlib.sha256(manifest_bytes).hexdigest())
    info=archive.infolist();names=[item.filename for item in info]
    require(len(names)==len(set(names))==len(expected) and set(names)==set(expected),
        'ZIP census differs or contains duplicates')
    checked=[]
    for item in info:
        row=expected[item.filename];h=hashlib.sha256();crc=0;size=0
        with archive.open(item) as stream:
            for chunk in iter(lambda:stream.read(1024**2),b''):
                h.update(chunk);crc=zlib.crc32(chunk,crc);size+=len(chunk)
        require(size==row['bytes']==item.file_size and h.hexdigest()==row['sha256'] and (crc&0xffffffff)==item.CRC,
            'ZIP member SHA/CRC/size differs: '+item.filename)
        checked.append(dict(member=item.filename,bytes=size,compressed_bytes=item.compress_size,
            sha256=h.hexdigest(),crc32=format(item.CRC,'08x')))
    return checked


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',required=True);parser.add_argument('--proof-name',required=True)
    args=parser.parse_args();destination=Path(args.destination)
    require(destination.is_absolute() and destination.suffix.lower()=='.zip','Use a new absolute D/E ZIP path')
    destination=destination.resolve()
    require(not destination.exists() and not destination.is_relative_to(ROOT),'Use a new destination outside clone')
    if os.name=='nt':require(destination.drive.lower() in ('d:','e:'),'Archive data uses D/E')
    partial=destination.with_name(destination.name+'.incomplete');receipt=destination.with_name(destination.name+'.receipt.json')
    require(not partial.exists() and not receipt.exists(),'Previous evidence exists; use new name')
    rows,commit,proof_dir,facts=prepare(args.proof_name)
    manifest=dict(schema='hospital_ies.v20.delivery_manifest.v2',git_commit=commit,
        created_utc=datetime.now(timezone.utc).isoformat(),hard_cap_exclusive_bytes=CAP,files=rows,
        raw_trajectories_included=False,post_push_proof_directory=str(proof_dir),qualification=facts,
        manifest_self_hash_excluded=True,generated_manifest_sha256_recorded_in_external_receipt=True)
    manifest_bytes=json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False).encode('utf8')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(partial,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9,allowZip64=False) as archive:
        for row in rows:
            path=ROOT/row['path'];require(sha(path)==row['sha256'],'Member changed during archive')
            archive.write(path,PREFIX+row['path']);validate_archive_size(partial.stat().st_size)
        archive.writestr(MANIFEST_NAME,manifest_bytes)
    validate_archive_size(partial.stat().st_size)
    with zipfile.ZipFile(partial) as archive:members=verify_zip(archive,rows,manifest_bytes)
    partial.rename(destination)
    result=dict(schema='hospital_ies.v20.delivery_verification.v2',status='passed',archive=str(destination),
        bytes=destination.stat().st_size,sha256=sha(destination),hard_cap_exclusive_bytes=CAP,
        checked_members=len(members),members=members,git_commit=commit,qualification=facts,
        all_member_sha256_crc32_size_verified=True,exact_member_census_verified=True,
        raw_trajectories_included=False,generated_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest())
    with receipt.open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({k:v for k,v in result.items() if k not in ('members','qualification')},ensure_ascii=True));return 0


if __name__=='__main__':raise SystemExit(main())
