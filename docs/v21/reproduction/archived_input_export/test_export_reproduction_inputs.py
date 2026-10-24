"""Metadata-only tests: no freeze/register/reset/step/RESULT/raw access."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest

HERE=Path(__file__).resolve().parent
ROOT=Path('D:/hospital_ies_v21_20261023')
PLAN=Path('E:/hospital_ies_v21_20261023/main02/experiments/002_main_m21_L2_b691f2fe78052e5574ca_H0/PLAN.json')
FREEZE=Path('E:/hospital_ies_v21_20261023/main02/FINAL_MAIN_FREEZE.json')
PSHA='ed3c2e5a5e224ec413a9edac41d5bfe805c22a554a59eac4939ac66734e617b2'
FSHA='a868f610fa593b4e8b0184ef72f35e1ca76b16693da44cfd516ab9c8e0f22e43'


def module():
    path=HERE/'export_reproduction_inputs.py'
    assert path.is_file(), 'Missing safe metadata exporter'
    spec=importlib.util.spec_from_file_location('metadata_export_fixture',path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def original():
    assert hashlib.sha256(PLAN.read_bytes()).hexdigest()==PSHA
    assert hashlib.sha256(FREEZE.read_bytes()).hexdigest()==FSHA
    return json.loads(PLAN.read_text('utf-8-sig')),json.loads(FREEZE.read_text('utf-8-sig'))


def native_design():
    sys.path[:0]=[str(ROOT),str(ROOT/'src')]
    from tools.sci_engineering import design
    assert Path(design.__file__).resolve()==ROOT/'tools/sci_engineering/design.py'
    return design


def resign(value,m):
    value.pop('signature',None);value['signature']=m.digest(value)


def validate(plan,freeze,m):
    design=native_design()
    return m.validate_archive(plan,freeze,FSHA,design.source_support,design.SEALED_DAYS)


def test_existing_nonsealed_archive_preserves_original_objects():
    m=module();plan,freeze=original()
    assert validate(plan,freeze,m)==5


def test_field_json_bytes_are_exact_not_reserialized():
    m=module()
    body=b'{"panel": {\n "z":"\\u4e2d\\u6587", "a": [1.0, false, null]\n}, "policy_specs": [ {"x":2} ], "configurations": { "H0" : {} }}'
    fields=m.extract_fields(body)
    assert fields['panel']==b'{\n "z":"\\u4e2d\\u6587", "a": [1.0, false, null]\n}'
    assert fields['policy_specs']==b'[ {"x":2} ]'
    assert fields['configurations']==b'{ "H0" : {} }'


@pytest.mark.parametrize('change',['spec','config','wrong_source','final_phase','final_flag','sealed_event','sealed_initialization','bool_hour'])
def test_changed_or_sealed_archive_is_refused(change):
    m=module();plan,freeze=original()
    if change=='spec':plan['policy_specs'][0]['config']['response_minutes']=999.
    if change=='config':plan['configurations']['H0']['sci_engineering']['ups']={'capacity_kWh':1.}
    if change=='wrong_source':freeze['source_sha256'][next(iter(freeze['source_sha256']))]='0'*64;resign(freeze,m)
    if change=='final_phase':plan['phase']='final_blind'
    scene=plan['panel']['scenarios'][0]
    if change=='final_flag':scene['is_final_blind']=True
    if change=='sealed_event':scene['start_hour']=21*24+1;scene.pop('full_source_support',None)
    if change=='sealed_initialization':scene['initialization_source_hours']=[21*24+1]
    if change=='bool_hour':scene['start_hour']=True
    resign(plan['panel'],m);resign(plan,m)
    with pytest.raises(ValueError):validate(plan,freeze,m)


@pytest.mark.parametrize('path',['../escape.py','C:/outside.py','C:outside.py','/absolute.py','inputs/../../outside.csv'])
def test_frozen_mapping_path_escape_is_refused(path):
    with pytest.raises(ValueError):module().validate_relative_paths({path:'0'*64})


def test_duplicate_json_keys_are_not_exported():
    m=module()
    with pytest.raises(ValueError):m.extract_fields(b'{"panel":{},"panel":{},"policy_specs":[],"configurations":{}}')


def test_wrong_external_SHA_creates_no_output(tmp_path):
    m=module();out=tmp_path/'new'
    with pytest.raises(ValueError):
        m.export(ROOT,PLAN,'0'*64,FREEZE,FSHA,out,Path('E:/hospital_ies_user_reproduction/unit_future01'),4.,None)
    assert not out.exists()


def test_existing_output_is_not_overwritten(tmp_path):
    m=module();out=tmp_path/'existing';out.mkdir();sentinel=out/'keep';sentinel.write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        m.export(ROOT,PLAN,PSHA,FREEZE,FSHA,out,Path('E:/hospital_ies_user_reproduction/unit_future02'),4.,None)
    assert sentinel.read_bytes()==b'keep'


@pytest.mark.parametrize('hours',[0.,-1.,float('nan'),float('inf')])
def test_future_budget_must_be_positive_and_finite(tmp_path,hours):
    m=module()
    with pytest.raises(ValueError):
        m.export(ROOT,PLAN,PSHA,FREEZE,FSHA,tmp_path/'new',Path('E:/hospital_ies_user_reproduction/unit_future03'),hours,None)
