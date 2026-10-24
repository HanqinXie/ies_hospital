"""Prepare complete native table mirrors and commands; never launch MATLAB/physics.

External known receipt/JOBS SHAs are mandatory. This is artifact preparation,
not a fresh row audit, policy selection, registration or scientific acceptance.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
from types import SimpleNamespace

SOURCE='4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add'
PLOTTER=Path('D:/hospital_ies_v21_20261023/docs/v21/figures/plot_sci_engineering_publication.m')
PLOTTER_SHA='e4d95dfef43b1b5b9bb19446098a1cc6e05f6e1a4df5af55bae2320e9deeb0c7'
QA=Path('E:/hospital_ies_v21_20261023/reviews/matlab_artifact_qa03/qa_expected.py')
QA_SHA='6ce7ee6ad422d0acda61d3332754e7ca0fd731feba82719291907e90c3514302'
QA_MEMBERS={'qa_expected.py':QA_SHA,
            'qa_sci_artifacts.m':'055a45e2557df742022b0fcfbe1c55adba6c86388c119309f0cc52681b39fe60',
            'artifact_scan.py':'6968f56762087ef4b9d82f666f8fc92ce02c10232161237a839f39ef9fb8bfd1'}
STAGES={'followup256':256,'hardware126':126,'pilot45_validation':45}


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def safe_relative(value):
    rel=Path(value)
    if rel.is_absolute() or rel.drive or rel.root or '..' in rel.parts or str(rel) in ('','.'):raise ValueError('Unsafe relative member')
    return rel


def checked(path, expected):
    p=Path(path).resolve()
    if sha(p)!=expected:raise ValueError('SHA mismatch: '+str(p))
    return p


def table(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def truth(v):
    return str(v).lower() in ('true','1')


def number(v):
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None


def validate_native(receipt_path, receipt_sha, jobs_path, jobs_sha, data_dir, count):
    if type(count) is not int or count<=0:raise ValueError('Positive canonical count required')
    rp=checked(receipt_path,receipt_sha);jp=checked(jobs_path,jobs_sha)
    r=read(rp);jobs=read(jp);directory=Path(data_dir).resolve()
    if r.get('schema')!='hospital_ies.external_parallel_analysis.v1':raise ValueError('Native parallel receipt required')
    if r.get('source_binding_sha256')!=SOURCE:raise ValueError('Native source mismatch')
    if Path(r.get('jobs_path','')).resolve()!=jp or r.get('jobs_sha256')!=jobs_sha:raise ValueError('Native JOBS binding mismatch')
    for value in (r.get('original_record_count'),jobs.get('registered_pairs'),r.get('table_row_counts',{}).get('CASE_METRICS')):
        if type(value) is not int or value!=count:raise ValueError('Actual complete pool count mismatch')
    for field,value in [('original_attempts_preserved',True),('global_source_model_and_case_preflight',True),('raw_or_result_files_modified',False),('actual_physics_started',0)]:
        if type(r.get(field)) is not type(value) or r[field]!=value:raise ValueError('Native audit flag mismatch: '+field)
    export=r['export_receipt'];an=directory/'ANALYSIS_RECEIPT.json'
    if canonical(read(an))!=canonical(export):raise ValueError('Actual native export receipt differs')
    members=[];names=set()
    for entry in export['files']:
        rel=safe_relative(entry['path'])
        if rel.as_posix() in names:raise ValueError('Unsafe or duplicate native member')
        names.add(rel.as_posix());p=checked(directory/rel,entry['sha256'])
        members.append(dict(path=str(p),relative=rel.as_posix(),sha256=entry['sha256'],bytes=p.stat().st_size))
        if p.suffix.lower()=='.csv' and ('rows' not in entry or len(table(p))!=entry['rows']):raise ValueError('Native CSV row count mismatch')
    required={'CASE_METRICS.csv','ATTEMPT_STATUS.csv','EVIDENCE_INTEGRITY_SUMMARY.csv','SCI_TABLES.mat'}
    if not required.issubset(names):raise ValueError('Incomplete native table inventory')
    members.append(dict(path=str(an),relative=an.name,sha256=sha(an),bytes=an.stat().st_size))
    case=table(directory/'CASE_METRICS.csv');attempts=table(directory/'ATTEMPT_STATUS.csv')
    if len(case)!=count:raise ValueError('CASE count mismatch')
    identities=set()
    for row in case:
        if row.get('source_binding_sha256')!=SOURCE:raise ValueError('CASE source mismatch')
        if row.get('status')!='completed' or not truth(row.get('eligible')):raise ValueError('CASE is not complete/eligible')
        if number(row.get('unknown_tail_minutes'))!=0:raise ValueError('CASE has unknown tail')
        if not truth(row.get('evidence_complete')):raise ValueError('CASE evidence incomplete')
        identity=tuple(row.get(k) for k in ('scenario_id','policy_id','configuration_id'))
        if any(not v for v in identity) or identity in identities:raise ValueError('Duplicate/unknown CASE identity')
        identities.add(identity)
    if len(attempts)!=count or any(a.get('status')!='completed' or a.get('source_binding_sha256')!=SOURCE for a in attempts):raise ValueError('Failed/unknown/additional attempt; preserve and review original ledger')
    if {tuple(a.get(k) for k in ('scenario_id','policy_id','configuration_id')) for a in attempts}!=identities:raise ValueError('CASE/attempt identities differ')
    integrity=table(directory/'EVIDENCE_INTEGRITY_SUMMARY.csv')
    if len(integrity)!=1:raise ValueError('Native integrity summary absent/ambiguous')
    for field,value in [('evidence_complete_cases',count),('latest_case_count',count),('evidence_drift_cases',0),('solver_sidecar_required_cases',count),('solver_sidecar_verified_cases',count)]:
        if number(integrity[0].get(field))!=value:raise ValueError('Native integrity mismatch: '+field)
    return dict(case=case,members=members,receipt=r,receipt_path=str(rp),jobs_path=str(jp))


def check_hardware_rows(rows):
    counts=Counter(r.get('policy_alias') for r in rows)
    if len(rows)!=126 or counts!=Counter(C=42,A=42,M=42):raise ValueError('Full126 hardware requires all42 C/A/M rows, including M')
    cells={}
    for r in rows:
        key=(r.get('scenario_id'),r.get('policy_alias'));config=r.get('configuration_id')
        if config in cells.setdefault(key,set()):raise ValueError('duplicate hardware member')
        cells[key].add(config)
    scenes={r.get('scenario_id') for r in rows}
    if len(scenes)!=14 or any({r.get('policy_alias') for r in rows if r.get('scenario_id')==s}!={'C','A','M'} for s in scenes):raise ValueError('Hardware policies must share the same14 scene identities')
    if len(cells)!=42 or any(v!={'H0','H1','H2'} for v in cells.values()):raise ValueError('Incomplete hardware H0/H1/H2 triples')
    return dict(counts)


def copy_members(members,destination):
    out=Path(destination)
    if out.exists():raise ValueError('Fresh mirror directory required')
    out.mkdir(parents=True)
    result=[]
    for member in members:
        source=checked(member['path'],member['sha256']);rel=safe_relative(member['relative'])
        target=out/rel
        if not target.resolve().is_relative_to(out.resolve()):raise ValueError('Unsafe resolved mirror path')
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():raise ValueError('Duplicate mirror destination')
        shutil.copyfile(source,target);checked(target,member['sha256']);checked(source,member['sha256'])
        result.append(dict(original_path=str(source),local_path=str(target.resolve()),relative=rel.as_posix(),sha256=member['sha256'],bytes=target.stat().st_size))
    return result


def write(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def prepare(args):
    count=STAGES[args.stage];out=Path(args.output).resolve()
    if out.exists():raise ValueError('Fresh output required; do not overwrite failures')
    verified=validate_native(args.parallel_receipt,args.parallel_sha,args.jobs,args.jobs_sha,args.data_dir,count)
    if args.stage=='hardware126':check_hardware_rows(verified['case'])
    checked(PLOTTER,PLOTTER_SHA);checked(QA,QA_SHA)
    out.mkdir(parents=True)
    spec=importlib.util.spec_from_file_location('approved_remaining_QA03',QA)
    qa=importlib.util.module_from_spec(spec);spec.loader.exec_module(qa)
    expected=qa.prepare(SimpleNamespace(parallel_receipt=args.parallel_receipt,parallel_sha=args.parallel_sha,cohort_receipt=None,cohort_sha=None,data_dir=args.data_dir,plotter=str(PLOTTER),output=str(out/'qa_source_expected'),process_index=None,process_index_sha=None))
    bundle=out/'bundle';bundle.mkdir();tables=bundle/'data'/args.stage/'tables'
    mapping=copy_members(verified['members'],tables)
    code=bundle/'code';code.mkdir();shutil.copyfile(PLOTTER,code/PLOTTER.name)
    qa_code=code/'qa_native';qa_code.mkdir()
    for name,known_sha in QA_MEMBERS.items():
        source=checked(QA.parent/name,known_sha);shutil.copyfile(source,qa_code/name)
    wrapper=Path(__file__).with_name('replay_remaining_figures.m');shutil.copyfile(wrapper,bundle/wrapper.name)
    evidence=bundle/'evidence';evidence.mkdir()
    for source,name in [(Path(args.parallel_receipt),'ORIGINAL_PARALLEL_ANALYSIS_RECEIPT.json'),(Path(args.jobs),'ORIGINAL_JOBS.json')]:shutil.copyfile(source,evidence/name)
    manifest=dict(schema='hospital_ies.local_matlab_data_mirror.v1',stage=args.stage,source_binding_sha256=SOURCE,original_receipt_path=str(Path(args.parallel_receipt).resolve()),original_receipt_sha256=args.parallel_sha,original_jobs_sha256=args.jobs_sha,source_table_mapping=mapping,all_native_tables_preserved=True,M_rows_removed=False,original_receipts_rewritten=False,raw_copied=False,scientific_gate=False,matlab_run='NOT_RUN',members=[])
    for p in sorted(bundle.rglob('*')):
        if p.is_file():manifest['members'].append(dict(path=p.relative_to(bundle).as_posix(),sha256=sha(p),bytes=p.stat().st_size))
    mp=bundle/'LOCAL_DATA_MANIFEST.json';write(mp,manifest)
    # Rehash source/mirror after all reads. The trusted receipt stays byte-exact.
    for entry in mapping:checked(entry['original_path'],entry['sha256']);checked(entry['local_path'],entry['sha256'])
    checked(args.parallel_receipt,args.parallel_sha);checked(args.jobs,args.jobs_sha);checked(PLOTTER,PLOTTER_SHA)
    for name,known_sha in QA_MEMBERS.items():checked(QA.parent/name,known_sha)
    receipt=dict(schema='hospital_ies.remaining_matlab_preparation.v1',created_utc=datetime.now(timezone.utc).isoformat(),stage=args.stage,actual_case_count=count,source_binding_sha256=SOURCE,original_expected=expected,bundle_path=str(bundle),local_manifest_path=str(mp),local_manifest_sha256=sha(mp),byte_exact_table_files=len(mapping),table_bytes=sum(e['bytes'] for e in mapping),native_full_row_audit_repeated=False,matlab_execution='NOT_RUN',visual_review='NOT_RUN',scientific_acceptance=False,actual_physics_started=0)
    write(out/'PREPARATION_RECEIPT.json',receipt)
    return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',required=True,choices=STAGES)
    p.add_argument('--parallel-receipt',required=True);p.add_argument('--parallel-sha',required=True)
    p.add_argument('--jobs',required=True);p.add_argument('--jobs-sha',required=True)
    p.add_argument('--data-dir',required=True);p.add_argument('--output',required=True)
    print(json.dumps(prepare(p.parse_args()),indent=2,ensure_ascii=False))


if __name__=='__main__':main()
