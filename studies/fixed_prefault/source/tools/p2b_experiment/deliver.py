"""Hash-preserving P2B source diff and self-contained review packaging."""
import difflib
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'outputs/v43_p2b_20261013_run01'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def source_files():
    files=[p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.py','.md','.txt','.toml')]
    for folder in ('src','tests','tools','configs','docs'):
        files.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and not any(x in p.parts for x in ('__pycache__','.pytest_cache')))
    return sorted(set(files))


def inventory():
    before=json.loads((BASE/'manifest_before.json').read_text(encoding='utf-8'))
    old={x['path']:x for x in before['files']};entries=[]
    for rel,r in old.items():
        p=ROOT/rel;current=sha(p) if p.exists() else None
        entries.append(dict(path=rel,baseline_sha256=r['sha256'],current_sha256=current,unchanged=current==r['sha256'],bytes=r['bytes']))
    changed=[r for r in entries if not r['unchanged']]
    result=dict(utc=datetime.now(timezone.utc).isoformat(),baseline_files=len(entries),unchanged=sum(r['unchanged'] for r in entries),changed=changed,files=entries)
    (BASE/'original_integrity.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    changes=[];diff=[]
    for p in source_files():
        rel=p.relative_to(ROOT).as_posix();baseline=old.get(rel)
        if baseline and sha(p)==baseline['sha256']:continue
        changes.append(dict(path=rel,status='added' if baseline is None else 'modified',bytes=p.stat().st_size,sha256=sha(p)))
        original=BASE/'.before'/rel
        a=original.read_text(encoding='utf-8').splitlines(True) if original.exists() else []
        try:b=p.read_text(encoding='utf-8').splitlines(True)
        except UnicodeDecodeError:continue
        diff.extend(difflib.unified_diff(a,b,fromfile='a/'+rel if baseline else '/dev/null',tofile='b/'+rel))
    (BASE/'P2B_ACTUAL.diff').write_text(''.join(diff),encoding='utf-8')
    (BASE/'changed_files.json').write_text(json.dumps(changes,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(dict(baseline_files=len(entries),unchanged=len(entries)-len(changed),changed_baseline=len(changed),added_or_modified_source=len(changes))))
    if changed:raise RuntimeError('Original baseline files changed; inspect before delivery')


def package():
    dest=ROOT/'P2B_REVIEW_PACKAGE.zip'
    if dest.exists():raise FileExistsError('Do not overwrite an existing review package')
    paths=set(source_files())
    paths.update(p for p in (ROOT/'inputs').rglob('*') if p.is_file())
    paths.update(p for p in BASE.rglob('*') if p.is_file() and p.suffix not in ('.pyc','.zip') and '__pycache__' not in p.parts)
    entries=[dict(path=p.relative_to(ROOT).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(paths)]
    manifest=dict(version='p2b.review.v1',utc=datetime.now(timezone.utc).isoformat(),files=entries,excluded='Older output trees/archives are unchanged and covered by original_integrity.json. This package includes full current input/source and all current P2B results, original PDFs, weights, logs and attempts. PACKAGE_MANIFEST.json cannot hash itself.')
    raw=json.dumps(manifest,indent=2,ensure_ascii=False).encode('utf-8')
    with zipfile.ZipFile(dest,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(paths):z.write(p,p.relative_to(ROOT).as_posix())
        z.writestr('PACKAGE_MANIFEST.json',raw)
    with zipfile.ZipFile(dest) as z:
        assert z.testzip() is None
        for r in entries:
            data=z.read(r['path'])
            assert len(data)==r['bytes'] and hashlib.sha256(data).hexdigest()==r['sha256'],r['path']
    digest=sha(dest)
    (ROOT/'P2B_REVIEW_PACKAGE.sha256').write_text(digest+'  P2B_REVIEW_PACKAGE.zip\n',encoding='ascii')
    verification=dict(path=dest.name,sha256=digest,bytes=dest.stat().st_size,verified_members=len(entries),crc_pass=True,all_members_sha256_pass=True,manifest_sha256=hashlib.sha256(raw).hexdigest(),utc=datetime.now(timezone.utc).isoformat())
    (ROOT/'P2B_PACKAGE_VERIFICATION.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
    print(json.dumps(verification,indent=2))


if __name__=='__main__':
    {'inventory':inventory,'package':package}[sys.argv[1]]()
