"""Prepare actual diff and portable review material; never edit old data."""
import difflib
import hashlib
import json
from pathlib import Path
import sys
import zipfile
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/'outputs/v43_p2a_20261013_120000'

def sha_bytes(raw):return hashlib.sha256(raw).hexdigest()
def write(path,x):path.write_text(json.dumps(x,indent=2,ensure_ascii=False),encoding='utf-8')

def source_files():
    files=[p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.py','.md','.txt')]
    for folder in ('src','tests','tools','scripts','configs','docs','taskP2'):
        files += [p for p in (ROOT/folder).rglob('*') if p.is_file() and
            not any(x in p.parts for x in ('__pycache__','.pytest_cache')) and p.suffix not in ('.pyc','.zip')]
    return sorted(set(files))

def prepare():
    files=source_files();diff=[];changes=[];snapshot=[]
    for p in files:
        rel=p.relative_to(ROOT);old=BASE/'.before'/rel;raw=p.read_bytes()
        snapshot.append(dict(path=rel.as_posix(),bytes=len(raw),sha256=sha_bytes(raw)))
        # taskP2 was already part of the baseline but intentionally not copied.
        if rel.parts[0]=='taskP2':continue
        previous=old.read_bytes() if old.exists() else b''
        if previous==raw:continue
        changes.append(dict(path=rel.as_posix(),kind='modified' if old.exists() else 'added',
            before_sha256=sha_bytes(previous) if old.exists() else None,after_sha256=sha_bytes(raw)))
        try:
            a=previous.decode('utf-8').splitlines(keepends=True);b=raw.decode('utf-8').splitlines(keepends=True)
        except UnicodeDecodeError:continue
        diff.extend(difflib.unified_diff(a,b,fromfile='a/'+rel.as_posix() if old.exists() else '/dev/null',
                                       tofile='b/'+rel.as_posix()))
    (BASE/'P2A_changes.diff').write_text(''.join(diff),encoding='utf-8')
    write(BASE/'actual_changes.json',changes);write(BASE/'final_source_snapshot.json',snapshot)
    before=json.loads((BASE/'manifest_before.json').read_text(encoding='utf-8'))
    write(BASE/'original_inputs_and_outputs_sha256.json',[x for x in before if x['path'].split('/')[0] in ('inputs','configs','outputs')])
    # Parse coverage proof emitted by the independently executed pure-spec command.
    coverage=json.loads((BASE/'logs/12_sampling_coverage.stdout.log').read_text(encoding='utf-8'))
    write(BASE/'SPECIFICATION_COVERAGE.json',coverage)
    (BASE/'REVIEW_START_HERE.md').write_text('''# P2A portable review

Start with root P2A_REPORT.md and MODEL_SCOPE.md, then docs/v43/P2A_DATA_INTERFACE.md.
Full source/tests/configs, actual before/after diff, taskP2 specifications, raw logs,
baseline rerun, pre-registered diagnostic plan, all 46 checkpoints and all error
prefixes are included. No task ZIP was extracted/read during this phase.

Inputs needed by the simulator (loads, IDF and small reference/boundary files)
are included. Unmodified literature PDFs under inputs/literature, historical
bulk outputs and duplicate inputs inside temporary pytest copies are omitted;
their complete original hashes are in original_inputs_and_outputs_sha256.json.
The archive is self-contained for reviewing the results and running current
tests with the supplied computational inputs. Exact resume of the original
manifest additionally requires the omitted unchanged references in place;
do not bypass its fingerprint guard. A fresh new run has a new manifest.

final_pytest_artifacts contains actual current checkpoints, timeout/error
injections and dependency-mutation test evidence. Temporary copies of raw
inputs are not duplicated in this archive. No failed trajectory was removed.

Package member integrity: PACKAGE_MEMBERS.json. Archive SHA256/CRC and member
verification is external in package_verification.json and the root .sha256,
because an archive cannot contain its own final hash. Recheck with:
python tools/p2a_audit/verify_package.py P2A_REVIEW_PACKAGE.zip

Simulation results are development-only, uncalibrated. Do not train or start
P2B/P3 from this review package without separate authorization.
''',encoding='utf-8')
    print(json.dumps(dict(changed_or_added=len(changes),source_files=len(snapshot),diff_bytes=(BASE/'P2A_changes.diff').stat().st_size)))

def package():
    dest=ROOT/'P2A_REVIEW_PACKAGE.zip'
    if dest.exists():raise FileExistsError('Refuse overwriting prior package attempt')
    files=source_files()
    files += [p for p in (ROOT/'inputs').rglob('*') if p.is_file() and 'literature' not in p.relative_to(ROOT/'inputs').parts]
    files += [p for p in BASE.rglob('*') if p.is_file() and p.name not in ('PACKAGE_MEMBERS.json','package_verification.json')
        and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts
        and not ('final_pytest_artifacts' in p.parts and 'inputs' in p.relative_to(BASE).parts)]
    files=sorted(set(files))
    # Confirm diagnostic manifests still describe current production bytes.
    for mpath in (BASE/'diagnostic_attempt1').glob('*/manifest_p2a.json'):
        m=json.loads(mpath.read_text(encoding='utf-8'))
        for item in m['files']:assert sha_bytes((ROOT/item['path']).read_bytes())==item['sha256'],item['path']
    members=[dict(path=p.relative_to(ROOT).as_posix(),bytes=p.stat().st_size,
                  sha256=sha_bytes(p.read_bytes())) for p in files]
    write(BASE/'PACKAGE_MEMBERS.json',members)
    with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in files:z.write(p,p.relative_to(ROOT).as_posix())
        z.write(BASE/'PACKAGE_MEMBERS.json','PACKAGE_MEMBERS.json')
    with zipfile.ZipFile(dest) as z:
        assert z.testzip() is None
        for item in members:
            raw=z.read(item['path']);assert len(raw)==item['bytes'] and sha_bytes(raw)==item['sha256'],item['path']
        assert len(z.namelist())==len(members)+1
    sha=sha_bytes(dest.read_bytes())
    (ROOT/'P2A_REVIEW_PACKAGE.sha256').write_text(sha+'  P2A_REVIEW_PACKAGE.zip\n',encoding='ascii')
    report=dict(package=dest.name,bytes=dest.stat().st_size,sha256=sha,
        members=len(members)+1,crc_ok=True,all_member_hashes_ok=True,
        verified_utc=datetime.now(timezone.utc).isoformat(),exit_code=0)
    write(BASE/'package_verification.json',report)
    print(json.dumps(report))

if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    elif sys.argv[1]=='package':package()
    else:raise ValueError('prepare or package required')
