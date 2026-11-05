"""Actual no-Git diff, complete old-file integrity audit, and review archive."""
from __future__ import annotations
import difflib
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[2]
B=ROOT/'outputs/v43_p2c_20261013_run01'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def source_files():
    paths={p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.md','.py','.txt','.toml')}
    for folder in ('src','tests','tools','configs','docs','Hospital_P2C_Next_Steps'):
        paths.update(p for p in (ROOT/folder).rglob('*') if p.is_file() and
                     not any(x in p.parts for x in ('__pycache__','.pytest_cache')) and p.suffix != '.zip')
    return sorted(paths)


def inventory():
    old=json.loads((B/'manifest_before.json').read_text(encoding='utf-8'))['files']
    entries=[]
    for r in old:
        p=ROOT/r['path'];current=sha(p) if p.is_file() else None
        entries.append(dict(**r,current_sha256=current,unchanged=current==r['sha256']))
    changed=[r for r in entries if not r['unchanged']]
    permitted={'src/hospital_ies/resilience.py'}
    forbidden=[r['path'] for r in changed if r['path'] not in permitted]
    result=dict(utc=datetime.now(timezone.utc).isoformat(),baseline_files=len(entries),
        baseline_bytes=sum(r['bytes'] for r in entries),unchanged=sum(r['unchanged'] for r in entries),
        changed=changed,forbidden_changes=forbidden,passed=not forbidden,files=entries,
        scope='Only the approved numerical implementation may alter an original production file; original inputs, capacities, configs, results, weights and archives retained')
    (B/'original_integrity.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    originals={r['path']:r for r in old};changes=[];patch=[]
    for p in source_files():
        rel=p.relative_to(ROOT).as_posix();oldr=originals.get(rel)
        if oldr and sha(p)==oldr['sha256']:
            continue
        changes.append(dict(path=rel,status='modified' if oldr else 'added',bytes=p.stat().st_size,sha256=sha(p)))
        baseline=B/'.before'/rel
        try:
            a=baseline.read_text(encoding='utf-8').splitlines(True) if baseline.is_file() else []
            b=p.read_text(encoding='utf-8').splitlines(True)
        except UnicodeDecodeError:
            continue
        patch.extend(difflib.unified_diff(a,b,fromfile='a/'+rel if oldr else '/dev/null',tofile='b/'+rel))
    (B/'P2C_ACTUAL.diff').write_text(''.join(patch),encoding='utf-8')
    (B/'changed_files.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('files','changed')}))
    if forbidden:
        raise RuntimeError('Unauthorized changes to frozen baseline: '+str(forbidden))


def package():
    dest=ROOT/'P2C_REVIEW_PACKAGE.zip'
    if dest.exists():
        raise FileExistsError('Do not overwrite a review package')
    integrity=json.loads((B/'original_integrity.json').read_text(encoding='utf-8'))
    if integrity['passed'] is not True:
        raise ValueError('Original file integrity gate not passed')
    paths=set(source_files())
    for base in (ROOT/'inputs',B,
                 ROOT/'outputs/v43_p2b_20261013_run01/experiment',
                 ROOT/'outputs/v43_p2b_20261013_run01/learning',
                 ROOT/'outputs/v43_p2b_20261013_run01/methods'):
        paths.update(p for p in base.rglob('*') if p.is_file() and p.suffix not in ('.zip','.pyc')
                     and not any(x in p.parts for x in ('__pycache__','.pytest_cache')))
    # Original independent pytest cases and frozen logs are required for the
    # 259-case collection and historical comparisons after unpacking.
    old=ROOT/'outputs/v43_p2b_20261013_run01'
    for directory in ('logs','review','design'):
        paths.update(p for p in (old/directory).rglob('*') if p.is_file() and p.suffix not in ('.zip','.pyc'))
    paths.update(p for p in old.iterdir() if p.is_file() and p.suffix in ('.py','.json','.md','.csv','.xml','.txt'))
    entries=[dict(path=p.relative_to(ROOT).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(paths)]
    manifest=dict(version='p2c.review.v1',utc=datetime.now(timezone.utc).isoformat(),files=entries,
        excludes='Older unmodified v4/P0/P1/P2A bulk outputs and old ZIPs are covered by complete original_integrity.json, not duplicated.',
        includes='Current source/config/inputs, task documents, all P2C attempts/evidence, all original P2B800 checkpoints/audits and mathematical/learning evidence. PACKAGE_MANIFEST cannot hash itself.')
    raw=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
    with zipfile.ZipFile(dest,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(paths):
            z.write(p,p.relative_to(ROOT).as_posix())
        z.writestr('PACKAGE_MANIFEST.json',raw)
    with zipfile.ZipFile(dest) as z:
        if z.testzip() is not None:
            raise ValueError('Review package CRC failed')
        for r in entries:
            data=z.read(r['path'])
            if len(data)!=r['bytes'] or hashlib.sha256(data).hexdigest()!=r['sha256']:
                raise ValueError('Package member hash failed: '+r['path'])
    check=dict(path=dest.name,bytes=dest.stat().st_size,sha256=sha(dest),verified_members=len(entries),
        crc_pass=True,all_members_sha256_pass=True,manifest_sha256=hashlib.sha256(raw).hexdigest(),utc=datetime.now(timezone.utc).isoformat())
    (ROOT/'P2C_REVIEW_PACKAGE.sha256').write_text(check['sha256']+'  P2C_REVIEW_PACKAGE.zip\n',encoding='ascii')
    (ROOT/'P2C_PACKAGE_VERIFICATION.json').write_text(json.dumps(check,indent=2),encoding='utf-8')
    print(json.dumps(check,indent=2))


if __name__=='__main__':
    {'inventory':inventory,'package':package}[sys.argv[1]]()
