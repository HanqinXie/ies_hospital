"""Only non-scientific metadata fixtures; no MATLAB/physics/generated figures."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

HERE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('hw18_count_bridge',HERE/'canonical_hw18_receipt_v2.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
scan_spec=importlib.util.spec_from_file_location('original_artifact_scan',Path('E:/hospital_ies_v21_20261023/reviews/matlab_artifact_qa03/artifact_scan.py'))
scan=importlib.util.module_from_spec(scan_spec);scan_spec.loader.exec_module(scan)
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False),encoding='utf-8');return sha(p)

@pytest.fixture
def metadata(tmp_path):
    cases=[];details=[]
    for n in range(1,19):
        name=f'NON_SCIENTIFIC_INTERFACE_FIXTURE_{n:02d}'
        folder=tmp_path/f'data/{n:02d}';folder.mkdir(parents=True)
        for ext in ['csv','mat']:(folder/f'PROCESS.{ext}').write_bytes(b'NOT SCIENTIFIC DATA; SCHEMA REFUSAL TEST ONLY\n')
        formats={}
        for ext in ['fig','pdf','svg','png']:
            p=tmp_path/(name+'.'+ext);p.write_bytes(b'NOT A FIGURE; INTERFACE FIXTURE ONLY\n')
            formats[ext]=dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p))
        cases.append(dict(scenario_id=f'fixture_{n}',policy_id='C_fixture',configuration_id='H0',output_name=name,physical_result_status='completed',folder=f'data/{n:02d}',csv_sha256=sha(folder/'PROCESS.csv'),mat_sha256=sha(folder/'PROCESS.mat')))
        details.append(dict(file=str(tmp_path/(name+'.fig')),status='passed',figure_size_cm=[30,25],formats=formats,source_csv_sha256=cases[-1]['csv_sha256'],native_series_checked=18,request_power_inventory_interpolated=False))
    m=dict(schema='hospital_ies.hardware18_actual_process_manifest.v1',source_binding_sha256='4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add',whole_hardware_pool_complete=True,source_CASE_count=126,cases=cases,science_gate=False)
    r=dict(schema='hospital_ies.matlab_object_audit.v2',auditor='hardware_process_v1 source-vector reopen; not qa_sci_artifacts',figure_count=18,matched_figure_count=18,details=details,object_checks_passed=True,all_expected_matched=True,failures=[],scientific_gate=False,physics_started=0)
    mp=tmp_path/'NON_SCIENTIFIC_MANIFEST.json';mh=write(mp,m);r['manifest_sha256']=mh
    rp=tmp_path/'NON_SCIENTIFIC_RECEIPT.json';rh=write(rp,r)
    return tmp_path,rp,rh,mp,mh,r,m

def call(meta):
    _,rp,rh,mp,mh,_,_=meta
    return bridge.adapt(rp,rh,mp,mh)

def test_frozen_scanner_explicitly_refuses_original_frozen_schema(metadata):
    tmp,rp,rh,_,_,_,_=metadata
    with pytest.raises(ValueError,match='MATLAB artifact count does not close'):
        scan.run(SimpleNamespace(matlab_receipt=str(rp),matlab_sha=rh,output=str(tmp/'must_not_scan_artifacts')))
    assert not (tmp/'must_not_scan_artifacts').exists()

def test_only_complete_18_receipt_gets_exact_expected_count(metadata):
    original=copy.deepcopy(metadata[5]);r=call(metadata)
    assert r['expected_figure_count']==18
    assert {k:v for k,v in r.items() if k!='expected_figure_count'}==original

@pytest.mark.parametrize('change',['17_details','17_count','duplicate_name','object_failed','detail_failed','fig_SHA_drift','manifest_name_mismatch','native_series_missing'])
def test_incomplete_or_unbound_native_audits_are_refused(metadata,change):
    tmp,rp,_,mp,mh,r,m=metadata
    if change=='17_details':r['details']=r['details'][:17]
    elif change=='17_count':r['figure_count']=r['matched_figure_count']=17
    elif change=='duplicate_name':r['details'][17]=copy.deepcopy(r['details'][0])
    elif change=='object_failed':r['object_checks_passed']=False
    elif change=='detail_failed':r['details'][0]['status']='failed'
    elif change=='fig_SHA_drift':Path(r['details'][0]['formats']['fig']['path']).write_bytes(b'drift')
    elif change=='manifest_name_mismatch':m['cases'][0]['output_name']='WRONG';mh=write(mp,m);r['manifest_sha256']=mh
    elif change=='native_series_missing':r['details'][0]['native_series_checked']=17
    rh=write(rp,r)
    with pytest.raises(ValueError):bridge.adapt(rp,rh,mp,mh)

@pytest.mark.parametrize('representation',['MATLAB_exact_integer_float','fractional_float','boolean','string','NaN','infinity'])
def test_MATLAB_JSON_number_bytes_without_weakening_exact_size(metadata,representation):
    _,rp,_,mp,mh,r,_=metadata
    v=r['details'][0]['formats']['svg'];size=v['bytes']
    if representation=='MATLAB_exact_integer_float':
        # Reproduce the actual first large-SVG value encoded by MATLAB as1.25956E+6.
        prefix=b'NOT A SCIENTIFIC FIGURE; MATLAB E+6 JSON BYTE-SIZE INTERFACE FIXTURE\n'
        Path(v['path']).write_bytes(prefix+b' '*(1259560-len(prefix)))
        size=1259560;v['bytes']=1.25956e6;v['sha256']=sha(v['path'])
    elif representation=='fractional_float':v['bytes']=float(size)+.5
    elif representation=='boolean':v['bytes']=True
    elif representation=='string':v['bytes']=str(size)
    elif representation=='NaN':v['bytes']=float('nan')
    else:v['bytes']=float('inf')
    rh=write(rp,r)
    if representation=='MATLAB_exact_integer_float':
        actual=bridge.adapt(rp,rh,mp,mh)
        assert actual['expected_figure_count']==18 and actual['details'][0]['formats']['svg']['bytes']==float(size)
    else:
        with pytest.raises(ValueError):bridge.adapt(rp,rh,mp,mh)

