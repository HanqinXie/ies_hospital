"""Hash pre-existing files, materialize real diffs, package auditable P1A files.

Does not modify original inputs, historical outputs, or old archives.
"""
import argparse,difflib,hashlib,json,sys,zipfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
ALLOWED={'src/hospital_ies/control_env.py','src/hospital_ies/resilience.py',
         'tests/test_resilience.py','tests/test_v43_p0_regressions.py',
         'docs/v43/STATE_TRANSITION_DESIGN.md','docs/v43/ASSUMPTIONS_REGISTER.md'}
ADDED=['src/hospital_ies/state_transition.py','tests/test_v43_p1a.py',
       'docs/v43/P1A_EXECUTION_PLAN.md','P1A_REPORT.md']

def sha(data):return hashlib.sha256(data).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def prepare(run):
    before=json.loads((run/'manifest_before.json').read_text(encoding='utf-8'))
    prefix=run.relative_to(ROOT).as_posix()+'/'
    original=[x for x in before['files'] if not x['path'].startswith(prefix)]
    self_entries=[x for x in before['files'] if x['path'].startswith(prefix)]
    save(run/'baseline_original_files.json',dict(created_utc=before['created_utc'],
        note='Original freeze manifest retained. Exclude newly-created self snapshot entries, not original files.',
        original_count=len(original),self_generated_count=len(self_entries),files=original))
    changed=[];missing=[];unchanged=[];diff=[]
    for x in original:
        p=ROOT/x['path']
        if not p.is_file():missing.append(x['path']);continue
        after=sha(p.read_bytes())
        if after!=x['sha256']:
            changed.append(dict(path=x['path'],before_sha256=x['sha256'],after_sha256=after,
                                authorized=x['path'] in ALLOWED))
            old=run/'.before'/x['path']
            if old.is_file():
                assert sha(old.read_bytes())==x['sha256'],old
                diff.extend(difflib.unified_diff(old.read_text(encoding='utf-8-sig').splitlines(True),
                    p.read_text(encoding='utf-8-sig').splitlines(True),fromfile='before/'+x['path'],tofile='after/'+x['path']))
        else:unchanged.append(x['path'])
    added=ADDED+[p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'tools/p1a_audit').glob('*.py'))]
    for name in added:
        data=(ROOT/name).read_text(encoding='utf-8')
        diff.extend(difflib.unified_diff([],data.splitlines(True),fromfile='/dev/null',tofile='after/'+name))
    (run/'changes.diff').write_text(''.join(diff),encoding='utf-8')
    report=dict(checked_utc=datetime.now(timezone.utc).isoformat(),original_files=len(original),
        original_bytes=sum(x['bytes'] for x in original),unchanged=len(unchanged),changed=changed,
        missing=missing,unexpected_changes=[x for x in changed if not x['authorized']],
        added=[dict(path=n,bytes=(ROOT/n).stat().st_size,sha256=sha((ROOT/n).read_bytes())) for n in added],
        preserved_groups={d:sum(1 for x in unchanged if x.startswith(d+'/')) for d in ('inputs','configs','outputs')})
    save(run/'integrity.json',report)
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return int(bool(missing or report['unexpected_changes']))

def package(run,archive):
    integrity=json.loads((run/'integrity.json').read_text(encoding='utf-8'))
    if integrity['missing'] or integrity['unexpected_changes']:raise RuntimeError('Integrity gate failed')
    if archive.exists():raise FileExistsError('Use a new archive path; do not overwrite review material')
    paths=set()
    for folder in ('src','tests','configs','docs/v43','tools/p0_audit','tools/p1a_audit','tools/v42_audit','scripts'):
        paths.update(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    for name in ('README.md','AGENTS.md','P0_BASELINE_REPORT.md','P1A_REPORT.md',
                 'scenario_sampler.py','generate_nonmpc_training.py','train_nonmpc_baselines.py'):
        if (ROOT/name).exists():paths.add(ROOT/name)
    paths.update(ROOT.glob('requirements*.txt'))
    paths.update(p for p in run.rglob('*') if p.is_file() and '.before' not in p.parts
        and '__pycache__' not in p.parts and '_package.' not in p.name
        and p.name!='package_verification.json')
    for name in sorted(ALLOWED):paths.add(run/'.before'/name)
    p0=ROOT/'outputs/v43_p0_20261012_212909'
    paths.update((p0/'logs').glob('*'))
    paths.add(p0/'manifest.json')
    # Include original code/test evidence, not another copy of original data.
    for name in ('src/hospital_ies/control_env.py','src/hospital_ies/resilience.py',
                 'tests/test_v43_p0_regressions.py'):
        paths.add(run/'.before'/name)
    members={p.relative_to(ROOT).as_posix():p.read_bytes() for p in sorted(paths)}
    members['REVIEW_README.md']=(
        '# P1A review package\n\n'
        'Start with P1A_REPORT.md. Scope: R01-R06, not all P1.\n'
        'Final full pytest: 51 passed, 2 failed, 6 strict xfailed; exit 1. Core: 21 passed.\n'
        'All failures retained. No new fallback or physics assumption approval.\n\n'
        'Contains current small source/config/tests, before copies, real diffs, current and historical P0 logs, '
        'minimal scenarios, event/subinterval traces, original-data SHA-256 inventory.\n'
        'Original DOE/IDF/CSV data and historical checkpoints stay in the original workspace; '
        'they are intentionally not duplicated. For execution, overlay the source on an isolated copy '
        'of that workspace after verifying baseline_original_files.json. Do not mix new runs into old outputs.\n'
        'R09 reproduces an omitted hash using the preserved P0 snapshot. Package manifest checks every member '
        'except itself. ZIP SHA-256 and external packaging metadata are beside the package/in the run directory.\n'
    ).encode('utf-8')
    inventory=[dict(path=k,bytes=len(v),sha256=sha(v)) for k,v in sorted(members.items())]
    members['PACKAGE_MANIFEST.json']=json.dumps(dict(files=inventory),indent=2).encode('utf-8')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,data in sorted(members.items()):z.writestr(name,data)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert set(z.namelist())==set(members)
        for item in inventory:assert sha(z.read(item['path']))==item['sha256'],item['path']
    result=dict(archive=str(archive.relative_to(ROOT)),bytes=archive.stat().st_size,
        sha256=sha(archive.read_bytes()),members=len(members),hashed_members=len(inventory),
        crc_verified=True,every_member_hash_verified=True,exit_code=0,
        note='Packaging command metadata is generated after ZIP closure and stays outside; every test log is included.')
    save(run/'package_verification.json',result)
    print(json.dumps(result,indent=2));return 0

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','package']);p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--archive',type=Path,default=Path('P1A_REVIEW_PACKAGE.zip'));a=p.parse_args()
    run=a.run_dir.resolve();archive=a.archive.resolve()
    sys.exit(prepare(run) if a.phase=='prepare' else package(run,archive))
