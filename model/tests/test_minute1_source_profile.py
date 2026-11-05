"""Minute source admission protocols only; no model or source release approval."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))

from tools.annual_program import flow_source_contracts as contracts
from tools.annual_program import compiled_flow_input as dto


def wire(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def admission_api():
    assert hasattr(contracts,'MINUTE1_CAPTURE_PROFILE'), 'Explicit minute source profile is missing'
    return importlib.import_module('tools.minute1_20261103.profile')


@pytest.mark.parametrize('module_name,owner,name',[
    ('hospital_ies.data_contract',None,'decision_snapshot'),
    ('tools.sci_engineering.service',None,'fan_independent_heat_bound'),
    ('tools.sci_engineering.environment','EngineeringCore','_hardware_data_at'),
])
def test_loaded_minute_snapshot_and_dhw_functions_cannot_be_substituted(monkeypatch,module_name,owner,name):
    from tools.annual_program import witness
    from tools.sci_engineering.runner import source_binding
    module=importlib.import_module(module_name)
    target=module if owner is None else getattr(module,owner)
    monkeypatch.setattr(target,name,lambda *args,**kwargs:None)
    with pytest.raises(ValueError,match='loaded|global|code|owner|source'):
        witness._minute1_loaded_functions(ROOT,source_binding(ROOT))


def descriptor():
    admission_api()
    files={name:'a'*64 for name in contracts.MINUTE1_REQUIRED_SOURCE_FILES}
    files.update(contracts.SCI_FUEL_NATIVE_SOURCE_CONTRACT)
    files['tools/annual_program/witness.py']='b'*64
    return dict(schema=contracts.MINUTE1_ADMISSION_SCHEMA,
        source_profile=contracts.MINUTE1_CAPTURE_PROFILE,
        base_scientific_profile=contracts.SCI_FUEL_CAPTURE_PROFILE,
        source_files=files,recorder_sha256='b'*64,
        review_receipt=dict(path='E:/synthetic_protocol/source_review.json',sha256='c'*64))


def test_complete_external_descriptor_is_distinct_and_does_not_mutate_old_profiles():
    value=descriptor();original=deepcopy(value)
    old=deepcopy(contracts.SCI_FUEL_NATIVE_SOURCE_CONTRACT)
    assert contracts.match_native_source_profile(value['source_files']) is None
    assert contracts.match_native_source_profile(value['source_files'],source_admission=value)==contracts.MINUTE1_CAPTURE_PROFILE
    pair=contracts.classify_recorder_source_pair('b'*64,value['source_files'],source_admission=value)
    assert pair['supported'] is pair['compiled_source_profile_allowed'] is True
    assert pair['source_profile']==contracts.MINUTE1_CAPTURE_PROFILE
    assert pair['source_authentication_performed'] is pair['actual_execution_bound'] is False
    assert value==original and contracts.SCI_FUEL_NATIVE_SOURCE_CONTRACT==old
    assert contracts.match_native_source_profile(old)==contracts.SCI_FUEL_CAPTURE_PROFILE
    assert contracts.classify_recorder_source_pair(contracts.SCI_FUEL_CAPTURE_RECORDER_SHA256,old)['supported'] is True


@pytest.mark.parametrize('change',['missing_runtime','wrong_native','wrong_recorder','unknown_profile','missing_review','partial_map','unknown_field'])
def test_partial_mixed_or_unreviewed_descriptors_never_grant_minute(change):
    value=descriptor();observed=deepcopy(value['source_files'])
    if change=='missing_runtime':value['source_files'].pop('tools/minute1_20261103/runtime.py')
    elif change=='wrong_native':value['source_files']['src/hospital_ies/solver_backend.py']='0'*64
    elif change=='wrong_recorder':value['recorder_sha256']='0'*64
    elif change=='unknown_profile':value['source_profile']='unreviewed'
    elif change=='missing_review':value.pop('review_receipt')
    elif change=='partial_map':observed.pop('tools/minute1_20261103/source.py')
    else:value['runtime_self_approval']=True
    assert contracts.match_native_source_profile(observed,source_admission=value) is None
    assert contracts.classify_recorder_source_pair('b'*64,observed,source_admission=value)['supported'] is False


@pytest.mark.parametrize('nvars',[47,48])
def test_minute_origin_transports_exact_external_admission_without_qualification(nvars):
    from test_annual_compiled_flow_input import fixture
    admission_api();values=fixture(nvars)
    origin=dict(schema=dto.MINUTE1_SOURCE_SCHEMA,source_root='synthetic_protocol_only',
        source_files={'fixture.py':'a'*64},leaf_function='fixture.solve_physical',
        source_profile=contracts.MINUTE1_CAPTURE_PROFILE,
        source_admission=dict(path='E:/synthetic_protocol/admission.json',sha256='d'*64))
    values['source_origin']=origin
    packet=dto.build_compiled_flow_input(**values)
    assert dto.validate_compiled_flow_input(packet,raw_x=values['raw_x'])==packet
    assert packet['source_origin']==origin
    assert packet['source_verification_performed'] is packet['actual_execution_bound'] is False
    origin['source_admission']['sha256']='0'*64
    assert packet['source_origin']['source_admission']['sha256']=='d'*64


def write_synthetic_admission(tmp_path):
    api=admission_api();root=tmp_path/'producer';root.mkdir()
    value=descriptor()
    for name in value['source_files']:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name,encoding='utf8')
    # A protocol fixture uses the real immutable native map as declarations,
    # but does not claim its synthetic files are an admitted real producer.
    review=tmp_path/'review.json'
    review.write_bytes(wire(dict(schema=api.REVIEW_SCHEMA,status='accepted',
        source_files_sha256=hashlib.sha256(wire(value['source_files'])).hexdigest(),
        recorder_sha256=value['recorder_sha256'])))
    value['review_receipt']=dict(path=str(review.resolve()),sha256=hashlib.sha256(review.read_bytes()).hexdigest())
    path=tmp_path/'admission.json';path.write_bytes(wire(value))
    return api,root,value,dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def test_pinned_admission_read_checks_review_and_never_approves_wrong_actual_bytes(tmp_path):
    api,root,value,ref=write_synthetic_admission(tmp_path)
    assert api.read_source_admission(ref)==value
    with pytest.raises(ValueError,match='source bytes'):
        api.verify_source_admission(root,ref,source_hashes=value['source_files'])
    Path(ref['path']).write_bytes(wire(dict(value,source_profile='unreviewed')))
    with pytest.raises(ValueError,match='admission.*SHA|admission.*changed'):
        api.read_source_admission(ref)


def test_replaced_inventory_helper_cannot_approve_wrong_actual_source_bytes(tmp_path,monkeypatch):
    api,root,value,ref=write_synthetic_admission(tmp_path)
    from tools.sci_engineering import runner
    monkeypatch.setattr(runner,'source_binding',lambda root:deepcopy(value['source_files']))
    with pytest.raises(ValueError,match='source bytes'):
        api.verify_source_admission(root,ref,source_hashes=value['source_files'])


def test_review_receipt_must_bind_the_exact_complete_source_map(tmp_path):
    api,root,value,ref=write_synthetic_admission(tmp_path)
    review=Path(value['review_receipt']['path'])
    body=json.loads(review.read_text());body['source_files_sha256']='0'*64;review.write_bytes(wire(body))
    value['review_receipt']['sha256']=hashlib.sha256(review.read_bytes()).hexdigest()
    Path(ref['path']).write_bytes(wire(value));ref['sha256']=hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='review.*source'):
        api.read_source_admission(ref)


def test_no_admission_file_or_current_hashes_can_self_approve(tmp_path):
    api=admission_api()
    with pytest.raises(ValueError):api.read_source_admission(None)
    with pytest.raises(ValueError):api.read_source_admission(dict(path=str(tmp_path/'missing.json'),sha256='a'*64))


def test_native_binding_requires_a_real_pinned_minute_admission(tmp_path):
    from tools.annual_program import witness
    ref=dict(path=str(tmp_path/'missing_admission.json'),sha256='a'*64)
    with pytest.raises(ValueError,match='admission'):
        witness.native_source_binding(Path(__file__).resolve().parents[1],source_admission=ref)
    with pytest.raises(ValueError,match='admission'):
        witness.compiled_origin(Path(__file__).resolve().parents[1],{},source_admission=ref)


def test_minute_loaded_module_attestation_has_a_dedicated_complete_owner_map():
    from tools.annual_program import witness
    assert hasattr(witness,'_MINUTE1_LOADED_MODULES'), 'Minute loaded runtime ownership guard is missing'
    assert {'tools.minute1_20261103.source','tools.minute1_20261103.registration',
        'tools.minute1_20261103.runtime','tools.minute1_20261103.environment',
        'tools.minute1_20261103.runner','tools.minute1_20261103.profile'}<=set(witness._MINUTE1_LOADED_MODULES)
    assert 'tools.single_fault_fuel_20261102.runtime' in witness._FUEL_LOADED_MODULES


@pytest.mark.parametrize('change',['imported_alias','derived_tuple','shadow_builtin'])
def test_minute_loaded_globals_cannot_borrow_unchanged_source_bytes(monkeypatch,change):
    from tools.annual_program import witness
    from tools.minute1_20261103 import source
    assert hasattr(witness,'_minute1_module_globals'), 'Complete minute global binding guard is missing'
    if change=='imported_alias':monkeypatch.setattr(source,'csv',object())
    elif change=='derived_tuple':monkeypatch.setattr(source,'FAN_COLUMNS',())
    else:monkeypatch.setattr(source,'len',lambda value:0,raising=False)
    with pytest.raises(ValueError,match='global|builtin'):
        witness._minute1_module_globals(source,Path(source.__file__))
