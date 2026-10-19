"""Actual diffs, original integrity and self-contained small review package."""
import sys,json,hashlib,difflib,zipfile,platform
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];RUN=ROOT/'outputs/v43_p1b_20261012_235940'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')

def prepare():
    before=json.loads((RUN/'manifest_before.json').read_text(encoding='utf-8'))['files']
    changed=[];unchanged=[];missing=[]
    for item in before:
        p=ROOT/item['path']
        if not p.exists():missing.append(item['path'])
        elif sha(p)==item['sha256']:unchanged.append(item)
        else:changed.append(dict(path=item['path'],before_sha256=item['sha256'],after_sha256=sha(p)))
    counts={k:sum(x['path'].startswith(k+'/') for x in unchanged) for k in ('inputs','configs','outputs')}
    integrity=dict(original_count=len(before),unchanged_count=len(unchanged),changed=changed,missing=missing,unchanged_groups=counts)
    write(RUN/'original_integrity.json',integrity)
    assert not missing and counts==dict(inputs=34,configs=5,outputs=979)
    # Include full source/config/test/tool tree and reports; no unchanged data.
    files=[]
    for folder in ('src','tests','tools','configs','docs','scripts'):
        files.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.zip'))
    files.extend(p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.py','.md','.txt'))
    previous={x['path'] for x in before};diff=[];new=[]
    for p in sorted(set(files)):
        rel=p.relative_to(ROOT).as_posix();old=RUN/'.before'/rel
        if rel not in previous:new.append(rel)
        if old.exists():old_text=old.read_text(encoding='utf-8-sig').splitlines(keepends=True)
        elif rel not in previous:old_text=[]
        else:continue
        new_text=p.read_text(encoding='utf-8-sig').splitlines(keepends=True)
        diff.extend(difflib.unified_diff(old_text,new_text,fromfile='before/'+rel if old.exists() else '/dev/null',tofile='after/'+rel))
    (RUN/'P1B_changes.diff').write_text(''.join(diff),encoding='utf-8')
    write(RUN/'actual_changes.json',dict(changed=changed,added=new))
    import numpy,scipy,pandas,pytest
    write(RUN/'runtime_environment.json',dict(python=sys.version,platform=platform.platform(),numpy=numpy.__version__,
        scipy=scipy.__version__,pandas=pandas.__version__,pytest=pytest.__version__,solver='scipy.optimize.milp / bundled HiGHS',seed=20261012))
    print(json.dumps(integrity));return files

def package():
    files=prepare()
    excluded={'delivery_manifest.json','package_verification.json'}
    files+= [p for p in RUN.rglob('*') if p.is_file() and p.name not in excluded and '__pycache__' not in p.parts]
    files=sorted(set(files));entries=[dict(path=p.relative_to(ROOT).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in files]
    write(RUN/'delivery_manifest.json',dict(files=entries))
    files.append(RUN/'delivery_manifest.json')
    package=ROOT/'P1B_REVIEW_PACKAGE.zip'
    if package.exists():raise FileExistsError('Refuse to overwrite a review package')
    with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in files:z.write(p,p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(package) as z:
        assert z.testzip() is None
        for entry in entries:assert hashlib.sha256(z.read(entry['path'])).hexdigest()==entry['sha256']
        size=len(z.namelist())
    result=dict(package=package.name,sha256=sha(package),bytes=package.stat().st_size,members=size,crc_ok=True,all_member_hashes_match=True)
    write(RUN/'package_verification.json',result)
    (ROOT/'P1B_REVIEW_PACKAGE.sha256').write_text(result['sha256']+'  '+package.name+'\n',encoding='ascii')
    print(json.dumps(result))

if __name__=='__main__':package() if '--package' in sys.argv else prepare()
