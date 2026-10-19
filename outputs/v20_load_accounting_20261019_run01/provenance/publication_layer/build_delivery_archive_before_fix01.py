"""Archive the published Git tree plus a completed fresh-clone proof, under 200 MB."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
RUN_REL='outputs/v20_load_accounting_20261019_run01'
CAP=200_000_000

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def prepare(name):
    if not name or not name.replace('_','').isascii() or not name.replace('_','').isalnum():
        raise ValueError('Use the existing fresh-clone replay name')
    spec=importlib.util.spec_from_file_location('release_proof_validator',ROOT/'publication/reproduce_v20.py')
    proof_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(proof_module)
    release=proof_module.validate_release(ROOT)
    if release['marker']['status']!='final':raise ValueError('Only the final publication tree may be delivered')
    proof_dir=ROOT/RUN_REL/'review'/('independent_repository_'+name)
    proof=read(proof_dir/'INDEPENDENT_REPRODUCTION_PROOF.json')
    if (proof.get('status')!='passed' or Path(proof.get('current_root','')).resolve()!=ROOT
            or proof.get('release_status')!='final' or proof.get('error') is not None
            or len(proof.get('strict_scalar_comparisons',[]))!=94
            or not all(x.get('passed') is True for x in proof['strict_scalar_comparisons'])):
        raise ValueError('A completed final fresh-clone proof is required')
    core=ROOT/'publication_checks/core01'
    if read(core/'COMMAND.json')['exit_code']!=0 or '463 passed' not in (core/'command.log').read_text(encoding='utf8'):
        raise ValueError('Actual fresh-clone default core gate is required')
    git=lambda *args:subprocess.check_output(['git',*args],cwd=ROOT)
    names=set(git('ls-files','-z').decode('utf8').rstrip('\0').split('\0'))
    selection=read(ROOT/'publication/V20_FILE_SELECTION.json')
    expected={row['path'] for row in selection['files']}|{'publication/V20_FILE_SELECTION.json'}
    if names!=expected:raise ValueError('Published Git tree and selection differ')
    # These extra records document the post-push run; its large raw stays local.
    for folder in (proof_dir,ROOT/RUN_REL/'review'/('reproduce_'+name),ROOT/'publication_checks'):
        names.update(p.relative_to(ROOT).as_posix() for p in folder.rglob('*')
                     if p.is_file() and 'temp' not in p.relative_to(folder).parts)
    actual=ROOT/RUN_REL/'experiments'/('reproduce_'+name)
    names.update(p.relative_to(ROOT).as_posix() for p in actual.rglob('*')
                 if p.is_file() and p.name in ('plan.json','manifest.json','BATCH_RESULT.json','ATTEMPTS.jsonl','RESULT.json'))
    rows=[]
    for name in sorted(names):
        path=proof_module.safe(ROOT,name)
        if not path.is_file() or path.is_symlink():raise ValueError('Missing/linked delivery member: '+name)
        if path.suffix.lower() in ('.zip','.gz','.7z','.sql','.sqlite','.lic','.pdf','.xlsx'):
            raise ValueError('Excluded raw, archive, source original or license: '+name)
        rows.append(dict(path=name,bytes=path.stat().st_size,sha256=sha(path)))
    return rows,git('rev-parse','HEAD').decode('ascii').strip(),proof_dir


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',required=True);parser.add_argument('--proof-name',required=True)
    args=parser.parse_args();destination=Path(args.destination)
    if not destination.is_absolute():raise ValueError('Use a new absolute D/E archive path')
    destination=destination.resolve()
    if destination.exists() or destination.is_relative_to(ROOT):raise ValueError('Use a new destination outside the clone')
    if os.name=='nt' and destination.drive.lower() not in ('d:','e:'):raise ValueError('Archive data uses D/E')
    partial=destination.with_name(destination.name+'.incomplete')
    receipt=destination.with_name(destination.name+'.receipt.json')
    if partial.exists() or receipt.exists():raise FileExistsError('Previous delivery evidence exists; use a new name')
    rows,commit,proof_dir=prepare(args.proof_name)
    manifest=dict(schema='hospital_ies.v20.delivery_manifest.v1',git_commit=commit,
        created_utc=datetime.now(timezone.utc).isoformat(),hard_cap_exclusive_bytes=CAP,
        files=rows,raw_trajectories_included=False,post_push_proof_directory=str(proof_dir),
        manifest_self_hash_excluded=True)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(partial,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9,allowZip64=False) as archive:
        for row in rows:
            path=ROOT/row['path']
            if sha(path)!=row['sha256']:raise ValueError('Delivery member changed during archive')
            archive.write(path,'Hospital_IES_V20/'+row['path'])
            if partial.stat().st_size>=CAP:raise ValueError('Delivery archive reached the 200,000,000-byte cap')
        archive.writestr('Hospital_IES_V20/DELIVERY_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    if partial.stat().st_size>=CAP:raise ValueError('Finished delivery archive exceeds the cap')
    with zipfile.ZipFile(partial) as archive:
        bad=archive.testzip()
        if bad is not None:raise ValueError('Archive integrity failure: '+bad)
        for row in rows:
            member=archive.read('Hospital_IES_V20/'+row['path'])
            if len(member)!=row['bytes'] or hashlib.sha256(member).hexdigest()!=row['sha256']:
                raise ValueError('Archive member SHA differs: '+row['path'])
    partial.rename(destination)
    result=dict(status='passed',archive=str(destination),bytes=destination.stat().st_size,
        sha256=sha(destination),hard_cap_exclusive_bytes=CAP,checked_members=len(rows)+1,
        git_commit=commit,all_member_sha256_verified=True,raw_trajectories_included=False)
    with receipt.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=True));return 0

if __name__=='__main__':raise SystemExit(main())
