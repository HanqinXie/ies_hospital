"""Build the delivery inventory; no scientific imports, solves or copies."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from verify_source_copy import verify

ROOT=Path(__file__).resolve().parent


def excluded(relative):
    parts=relative.parts
    return (parts[:2] in (('source','.git'),('legacy_context_7fa','.git')) or parts[0]=='verification'
            or '__pycache__' in parts or '.pytest_cache' in parts
            or relative.as_posix()=='MANIFEST.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reseal-reason')
    args=parser.parse_args()
    identity=verify(ROOT)
    target=ROOT/'MANIFEST.json'
    if target.exists():
        if not args.reseal_reason:
            raise SystemExit('Manifest exists; explicit --reseal-reason required during build')
        history=ROOT/'verification'
        history.mkdir(exist_ok=True)
        old=target.read_bytes()
        digest=hashlib.sha256(old).hexdigest()
        saved=history/('MANIFEST_before_'+digest[:16]+'.json')
        if not saved.exists():saved.write_bytes(old)
        with (history/'SEAL_HISTORY.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),
                                    'previous_sha256':digest,'reason':args.reseal_reason},ensure_ascii=False)+'\n')
    entries=[]
    for path in sorted(ROOT.rglob('*')):
        relative=path.relative_to(ROOT)
        if excluded(relative):continue
        if path.is_symlink():raise ValueError('No delivery symlinks: '+str(relative))
        if path.is_file():
            h=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1048576),b''):h.update(chunk)
            entries.append({'path':relative.as_posix(),'size_bytes':path.stat().st_size,'sha256':h.hexdigest()})
    manifest={'schema':'hospital_ies.normal_year_transfer_manifest.v1',
              'created_utc':datetime.now(timezone.utc).isoformat(),
              'source_commit':identity['source_commit'],
              'profile':identity['profile'],
              'source_freeze_sha256':identity['source_freeze_sha256'],
              'target':'Windows AMD EPYC 9654 / 256 GB RAM',
              'files':entries,'excludes':['source/.git/**','legacy_context_7fa/.git/**','verification/**','**/__pycache__/**','**/.pytest_cache/**','MANIFEST.json'],
              'git_identity':'independent source/.git and legacy_context_7fa/.git; validate each HEAD, tracked bytes and objects separately',
              'annual_started_locally':False,'server_admission_granted_by_build':False}
    target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'files':len(entries),'payload_bytes':sum(row['size_bytes'] for row in entries),
                      'manifest_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                      'annual_started_locally':False}))


if __name__=='__main__':main()
