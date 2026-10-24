"""Require actual ZIP/extraction/native365/default-pytest receipts before closing."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,importlib.util,json,re
E=Path('E:/hospital_ies_v21_20261023');HERE=Path(__file__).resolve().parent
SOURCE='4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add'
INPUTS='659ecc488bda87c9338afde283cbb74135ca2ebdf2769ce463d0e61f0c6288d8'
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
def read(p):return json.loads(Path(p).read_text('utf-8-sig'))
def ref(p):return dict(path=str(Path(p).resolve()),bytes=Path(p).stat().st_size,sha256=sha(p))
def write(p,x):
    with Path(p).open('x',encoding='utf-8',newline='\n') as f:json.dump(x,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
p=argparse.ArgumentParser();p.add_argument('--archive',required=True,type=Path);p.add_argument('--archive-sha',required=True)
p.add_argument('--extraction',required=True,type=Path);p.add_argument('--extraction-sha',required=True);p.add_argument('--census',required=True,type=Path)
p.add_argument('--output',required=True,type=Path);a=p.parse_args()
assert not a.output.exists() and 0<a.archive.stat().st_size<400000000 and sha(a.archive)==a.archive_sha
assert sha(a.extraction)==a.extraction_sha;extraction=read(a.extraction);assert extraction['status']=='verified_extraction'
assert extraction['archive_sha256']==a.archive_sha and extraction['externally_trusted_archive_sha256']==a.archive_sha
project=E/'archive_clean_v21_final02/project';checker=project.parent/'delivery_tools/independent_delivery_check.py'
assert sha(checker)=='d78d1960f8c7683ce0306aa8d3f09e572a2aecdbe17a2511d9fd5f8757c65e2e'
spec=importlib.util.spec_from_file_location('actual_bundled_final_checker',checker);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
members=m.check_extraction_authority(project.parent,a.extraction,a.extraction_sha,a.archive_sha)
current=m.inspect_clean(project)
assert current['source_binding_sha256']==SOURCE and current['input_binding_sha256']==INPUTS
steps=[]
for step,status in [('verify','verify_passed_no_physics'),('prepare','prepare_passed_no_physics'),('run','passed_new_full_replay')]:
    p=project/'independent/outer_audit'/step/'OUTER_RECEIPT.json';r=read(p)
    assert r['step']==step and r['status']==status and r['exit_code']==0 and r['error'] is None
    assert r['guard']['violations']==[] and r['guard']['audited_operations']>0
    steps.append(dict(step=step,receipt=ref(p),guard_operations=r['guard']['audited_operations'],guard_violations=0,started_utc=r['started_utc'],finished_utc=r['finished_utc']))
run=read(project/'independent/outer_audit/run/OUTER_RECEIPT.json')
inventory=run['native_attempt_inventory'];assert inventory['started']==inventory['finished']==1
assert inventory['native_status_counts']=={'completed':1} and inventory['issues']==[] and len(inventory['native_results'])==1
assert inventory['native_results'][0]['status']=='completed' and inventory['native_results'][0]['unknown_tail_minutes']==0
assert not inventory['native_results'][0]['error_present'] and not inventory['native_results'][0]['analysis_error_present']
proofpath=project/'independent/INDEPENDENT_REPLAY_PROOF.json';proof=read(proofpath)
assert proof['independent_gate_passed'] is True and proof['actual_attempts']==1 and proof['observed_minutes']==360
assert proof['source_binding_sha256']==SOURCE and proof['input_binding_sha256']==INPUTS
scalarpath=project/'independent/outer_audit/run/ALL365_SCALAR_COMPARISONS.json';scalar=read(scalarpath)
assert scalar['passed'] and scalar['count']==365 and scalar['unexpected_actual_fields']==[]
assert len(scalar['comparisons'])==365 and all(r['passed'] for r in scalar['comparisons'])
assert run['native_evidence_verification']['evidence_complete'] is True
testpath=HERE/'logs/fresh_archive_default_pytest01.command.json';test=read(testpath)
assert test['exit_code']==0 and Path(test['cwd']).resolve()==project.resolve()
argv=test['command'];assert argv[1:5]==['-m','pytest','-q','-rs'] and argv[5]=='--basetemp' and len(argv)==7
assert Path(argv[6]).resolve().is_relative_to(E.resolve())
stdout=HERE/'logs/fresh_archive_default_pytest01.stdout.log';text=stdout.read_text('utf-8',errors='replace')
matches=re.findall(r'(?m)^.*\b(\d+) passed\b.*$',text);assert len(matches)==1
summary=[line for line in text.splitlines() if re.search(r'\b\d+ passed\b',line)][-1]
passed=int(matches[0]);assert passed>0
def count(label):
    x=re.search(r'\b(\d+) '+label+r'\b',summary);return int(x.group(1)) if x else 0
assert count('failed')==count('errors')==count('xfailed')==count('xpassed')==count('deselected')==0
tests=dict(passed=passed,skipped=count('skipped'),failed=0,xfailed=0,xpassed=0,deselected=0,actual_summary=summary,actual_command=ref(testpath),stdout=ref(stdout),stderr=ref(HERE/'logs/fresh_archive_default_pytest01.stderr.log'),elapsed_seconds=test['elapsed_seconds'],default_root_collection_no_test_filter=True)
census=read(a.census);assert census['hard_issues']==[] and census['overall_budget']['within_budget']
assert census['overall_budget']['epoch_utc']=='2026-10-23T06:06:02.282879+00:00'
assert census['overall_budget']['freeze_changes_reset_budget'] is False
assert set(census['terminal_status_counts'])<={'completed','failed_unknown'}
live=project/'independent';assert str(live.resolve()) in census['campaign_roots']
write(a.output,dict(schema='hospital_ies.actual_final_archive_reproduction_and_default_tests.v1',created_utc=datetime.now(timezone.utc).isoformat(),status='archive_members_fresh_current365_and_default_pytest_actually_passed',archive=ref(a.archive),extraction_receipt=ref(a.extraction),archive_packaged_member_count=extraction['member_count'],archive_member_manifest_SHA=extraction['member_manifest_sha256'],current_project_binding=current,guarded_steps=steps,full_seen_case_reproduction=dict(actual_attempts=1,observed_minutes=360,scalar_count=365,all365_passed=True,native_proof=ref(proofpath),all365_comparisons=ref(scalarpath),native_evidence=run['native_evidence_verification'],native_inventory=inventory,new_independent_backgrounds=0),fresh_default_pytest=tests,global_census=ref(a.census),overall_budget=census['overall_budget'],all_M21_terminal_counts=census['terminal_status_counts'],formal_unique_scientific_trajectories=2778,boundary112_already_within_supported252=True,git_commit_and_push_not_certified_by_this_receipt=True,future_proof_not_inside_verified_ZIP=True,scope='Actual byte/extraction and seen-case runtime/default-test verification. Not cross-hospital validation, clinical approval, OEM certification, or a repeat of all2778 simulations. CPython guards are not OS-level native-I/O sandboxes.'))
print(json.dumps(dict(status='actual_archive_runtime_and_default_tests_closed',archive_bytes=a.archive.stat().st_size,all365_passed=True,pytest=tests['actual_summary'],proof_sha256=sha(a.output)),ensure_ascii=False))
