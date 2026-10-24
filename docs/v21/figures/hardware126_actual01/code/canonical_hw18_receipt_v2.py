"""External count-only schema bridge; original HW18 receipts stay immutable."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

SOURCE='4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def require(condition,message):
    if not condition:raise ValueError(message)
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def adapt(source_receipt, source_sha, manifest_path, manifest_sha):
    rp=Path(source_receipt).resolve();mp=Path(manifest_path).resolve()
    require(sha(rp)==source_sha and sha(mp)==manifest_sha,'Externally known original receipt/manifest SHA differs')
    r=read(rp);m=read(mp);bound={str(rp):source_sha,str(mp):manifest_sha}
    require(r.get('schema')=='hospital_ies.matlab_object_audit.v2','Wrong original native receipt schema')
    require(r.get('auditor')=='hardware_process_v1 source-vector reopen; not qa_sci_artifacts','Not the original HW18 native-object auditor')
    require(type(r.get('figure_count')) is int and r['figure_count']==18 and type(r.get('matched_figure_count')) is int and r['matched_figure_count']==18,'The original18 native objects are not complete')
    require(r.get('object_checks_passed') is True and r.get('all_expected_matched') is True and r.get('failures')==[],'Original native object audit is not passed')
    require(r.get('manifest_sha256')==manifest_sha and r.get('scientific_gate') is False and type(r.get('physics_started')) is int and r['physics_started']==0,'Native manifest/gate binding differs')
    require(m.get('schema')=='hospital_ies.hardware18_actual_process_manifest.v1' and m.get('source_binding_sha256')==SOURCE,'Wrong actual manifest/source')
    require(m.get('whole_hardware_pool_complete') is True and type(m.get('source_CASE_count')) is int and m['source_CASE_count']==126 and m.get('science_gate') is False,'Actual full126 manifest is incomplete')
    cases=m.get('cases');details=r.get('details')
    require(isinstance(cases,list) and len(cases)==18 and isinstance(details,list) and len(details)==18,'All18 manifest cases and native details are required')
    names=[c.get('output_name') for c in cases]
    require(all(type(n) is str and n and Path(n).name==n for n in names) and len(set(names))==18,'Missing/duplicate actual-manifest output names')
    ids=[tuple(c.get(k) for k in ['scenario_id','policy_id','configuration_id']) for c in cases]
    require(all(all(type(v) is str and v for v in identity) for identity in ids) and len(set(ids))==18,'Missing/duplicate actual-manifest case identities')
    by_name=dict(zip(names,cases));seen=set()
    for d in details:
        require(d.get('status')=='passed' and type(d.get('native_series_checked')) is int and d['native_series_checked']==18 and d.get('request_power_inventory_interpolated') is False,'A detail lacks all18 passed native series')
        fig=Path(d.get('file','')).resolve();name=fig.stem
        require(fig.suffix.lower()=='.fig' and name in by_name and name not in seen,'Missing/duplicate/unbound actual FIG name')
        seen.add(name);c=by_name[name]
        require(c.get('physical_result_status')=='completed' and d.get('source_csv_sha256')==c.get('csv_sha256'),'Original FIG/source CASE binding differs')
        require(d.get('figure_size_cm')==[30,25],'Unexpected original HW18 figure size')
        folder=Path(c.get('folder',''))
        require(not folder.is_absolute() and not folder.drive and '..' not in folder.parts and str(folder) not in ('','.'),'Unsafe manifest source folder')
        for ext,key in [('csv','csv_sha256'),('mat','mat_sha256')]:
            p=mp.parent/folder/('PROCESS.'+ext)
            require(sha(p)==c.get(key),'Actual source CSV/MAT differs from pinned manifest')
            bound[str(p.resolve())]=c[key]
        formats=d.get('formats')
        require(isinstance(formats,dict) and set(formats)=={'fig','pdf','svg','png'},'All four original formats required')
        for ext,v in formats.items():
            p=Path(v.get('path','')).resolve()
            require(p==fig.with_suffix('.'+ext),'Format path/name differs from its actual FIG')
            size=v.get('bytes')
            integral_size=type(size) is int or (type(size) is float and math.isfinite(size) and size.is_integer())
            require(integral_size and size>0 and int(size)==p.stat().st_size and sha(p)==v.get('sha256'),'Actual format bytes/SHA differ from native audit')
            bound[str(p)]=v['sha256']
    require(seen==set(names),'Actual18 names did not all close')
    require('expected_figure_count' not in r or (type(r['expected_figure_count']) is int and r['expected_figure_count']==18),'Conflicting preexisting count')
    for p,h in bound.items():require(sha(p)==h,'An original receipt/manifest/artifact drifted during bridge')
    result=copy.deepcopy(r);result['expected_figure_count']=18
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for f in ['source-receipt','source-sha','manifest','manifest-sha','output']:ap.add_argument('--'+f,required=True)
    a=ap.parse_args();out=Path(a.output);require(not out.exists(),'Fresh bridge output required')
    r=adapt(a.source_receipt,a.source_sha,a.manifest,a.manifest_sha)
    out.mkdir(parents=True);canonical=out/'HW18_CANONICAL_MATLAB_OBJECT_QA.json'
    canonical.write_text(json.dumps(r,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    record=dict(schema='hospital_ies.hardware18_count_only_bridge.v1',original_receipt_path=str(Path(a.source_receipt).resolve()),original_receipt_sha256=a.source_sha,actual_manifest_path=str(Path(a.manifest).resolve()),actual_manifest_sha256=a.manifest_sha,canonical_receipt_path=str(canonical.resolve()),canonical_receipt_sha256=sha(canonical),changed_fields=['expected_figure_count'],all18_original_native_details_passed=True,all18_names_unique_and_match_actual_manifest=True,all_original_format_and_source_hashes_checked=True,original_receipt_unchanged_after=sha(a.source_receipt)==a.source_sha,scientific_gate=False)
    (out/'COUNT_BRIDGE_RECEIPT.json').write_text(json.dumps(record,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False))
if __name__=='__main__':main()
