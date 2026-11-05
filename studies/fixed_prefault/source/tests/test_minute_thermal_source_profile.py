"""Separate thermal-native revision protocol; never admit or execute a model."""
from copy import deepcopy
import ast
import hashlib
import importlib
import json
from pathlib import Path
import subprocess

import pytest

from tools.annual_program import flow_source_contracts as contracts
from tools.annual_program import compiled_flow_input as dto
from tools.minute1_20261103 import profile

ROOT=Path(__file__).resolve().parents[1]
ESSENTIAL='tools/essential_fixes/execution.py'
REVIEWED_ESSENTIAL_SHA='8eb3b210c6615a7bd72bac2c5e7c6fe495164e92f850dc78cd7d70e8e69a2309'


def wire(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
def digest(value):return hashlib.sha256(wire(value)).hexdigest()


def descriptor():
    assert hasattr(contracts,'MINUTE_THERMAL_CAPTURE_PROFILE'),'Explicit thermal-native V2 profile is missing'
    files={name:'a'*64 for name in contracts.MINUTE1_REQUIRED_SOURCE_FILES}
    files.update(contracts.SCI_FUEL_NATIVE_SOURCE_CONTRACT);files[ESSENTIAL]=REVIEWED_ESSENTIAL_SHA
    files['tools/annual_program/witness.py']='b'*64
    revision=dict(schema='hospital_ies.minute1.thermal_native_revision.v2',
        revision_id='essential_actual_native_minute_thermal_reference_v2',source_files={ESSENTIAL:REVIEWED_ESSENTIAL_SHA})
    return dict(schema=contracts.MINUTE_THERMAL_ADMISSION_SCHEMA,source_profile=contracts.MINUTE_THERMAL_CAPTURE_PROFILE,
        base_scientific_profile=contracts.SCI_FUEL_CAPTURE_PROFILE,source_files=files,recorder_sha256='b'*64,
        review_receipt={'path':'E:/synthetic_protocol/thermal_review.json','sha256':'c'*64},native_revision_contract=revision)


def test_explicit_reviewed_revision_changes_only_essential_native_and_preserves_old_registries():
    value=descriptor();old=deepcopy(contracts.SCI_FUEL_NATIVE_SOURCE_CONTRACT)
    assert contracts.minute_thermal_source_contract(value['source_files'],value)==value['source_files']
    assert contracts.MINUTE_THERMAL_NATIVE_OVERRIDES=={ESSENTIAL:REVIEWED_ESSENTIAL_SHA}
    assert hashlib.sha256((ROOT/ESSENTIAL).read_bytes()).hexdigest()==REVIEWED_ESSENTIAL_SHA
    assert [key for key,pin in old.items() if pin!=value['source_files'][key]]==[ESSENTIAL]
    assert contracts.minute1_source_contract(value['source_files'],value) is None
    assert contracts.match_native_source_profile(value['source_files']) is None
    assert contracts.match_native_source_profile(value['source_files'],source_admission=value)==contracts.MINUTE_THERMAL_CAPTURE_PROFILE
    pair=contracts.classify_recorder_source_pair('b'*64,value['source_files'],source_admission=value)
    assert pair['supported'] is pair['compiled_source_profile_allowed'] is True
    assert pair['source_authentication_performed'] is pair['actual_execution_bound'] is False
    assert contracts.SCI_FUEL_NATIVE_SOURCE_CONTRACT==old
    assert contracts.match_native_source_profile(old)==contracts.SCI_FUEL_CAPTURE_PROFILE


@pytest.mark.parametrize('change',['old_essential','other_native','extra_override','missing_override','unknown_revision','partial_source',
    'wrong_recorder','old_schema','old_profile','missing_runtime','unknown_field'])
def test_partial_or_mixed_revision_never_borrows_old_or_new_profile(change):
    value=descriptor();observed=deepcopy(value['source_files'])
    if change=='old_essential':value['source_files'][ESSENTIAL]=contracts.SCI_FUEL_NATIVE_SOURCE_CONTRACT[ESSENTIAL]
    elif change=='other_native':value['source_files']['src/hospital_ies/solver_backend.py']='0'*64
    elif change=='extra_override':value['native_revision_contract']['source_files']['src/hospital_ies/solver_backend.py']='0'*64
    elif change=='missing_override':value.pop('native_revision_contract')
    elif change=='unknown_revision':value['native_revision_contract']['revision_id']='unreviewed'
    elif change=='partial_source':observed.pop('tools/minute1_20261103/source.py')
    elif change=='wrong_recorder':value['recorder_sha256']='0'*64
    elif change=='old_schema':value['schema']=contracts.MINUTE1_ADMISSION_SCHEMA
    elif change=='old_profile':value['source_profile']=contracts.MINUTE1_CAPTURE_PROFILE
    elif change=='missing_runtime':value['source_files'].pop('tools/minute1_20261103/runtime.py')
    else:value['self_approved']=True
    assert contracts.match_native_source_profile(observed,source_admission=value) is None
    assert contracts.classify_recorder_source_pair('b'*64,observed,source_admission=value)['supported'] is False


def test_native_only_new_map_and_a_v1_label_cannot_admit_the_thermal_revision():
    value=descriptor();native={name:value['source_files'][name] for name in contracts.SCI_FUEL_NATIVE_SOURCE_CONTRACT}
    assert contracts.match_native_source_profile(native) is None
    old=deepcopy(value);old.pop('native_revision_contract');old['schema']=contracts.MINUTE1_ADMISSION_SCHEMA;old['source_profile']=contracts.MINUTE1_CAPTURE_PROFILE
    assert contracts.minute1_source_contract(old['source_files'],old) is None
    assert contracts.classify_recorder_source_pair('b'*64,old['source_files'],source_admission=old)['supported'] is False


def pinned_protocol(tmp_path):
    value=descriptor();review=tmp_path/'review.json'
    review.write_bytes(wire(dict(schema='hospital_ies.minute1.thermal_source_review.v2',status='accepted',
        source_files_sha256=digest(value['source_files']),recorder_sha256=value['recorder_sha256'],
        native_revision_contract_sha256=digest(value['native_revision_contract']))))
    value['review_receipt']=dict(path=str(review.resolve()),sha256=hashlib.sha256(review.read_bytes()).hexdigest())
    path=tmp_path/'admission.json';path.write_bytes(wire(value))
    return value,dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest()),review


def test_pinned_review_must_accept_exact_revision_and_never_approves_actual_unmatched_bytes(tmp_path):
    value,ref,review=pinned_protocol(tmp_path)
    assert profile.read_source_admission(ref)==value
    with pytest.raises(ValueError,match='source bytes|inventory'):
        profile.verify_source_admission(tmp_path,ref,source_hashes=value['source_files'])
    body=json.loads(review.read_text());body['native_revision_contract_sha256']='0'*64;review.write_bytes(wire(body))
    value['review_receipt']['sha256']=hashlib.sha256(review.read_bytes()).hexdigest();Path(ref['path']).write_bytes(wire(value));ref['sha256']=hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='review|revision'):profile.read_source_admission(ref)


@pytest.mark.parametrize('nvars',[47,48])
def test_v2_origin_transports_revision_admission_and_exact_vector_without_claiming_execution(nvars):
    from test_annual_compiled_flow_input import fixture
    value=descriptor();values=fixture(nvars)
    origin=dict(schema=dto.MINUTE_THERMAL_SOURCE_SCHEMA,source_root='synthetic_protocol',source_files=value['source_files'],
        leaf_function='fixture.solve_physical',source_profile=value['source_profile'],source_admission={'path':'E:/synthetic/admission.json','sha256':'d'*64},
        native_revision_contract=deepcopy(value['native_revision_contract']))
    values['source_origin']=origin;packet=dto.build_compiled_flow_input(**values)
    assert dto.validate_compiled_flow_input(packet,raw_x=values['raw_x'])==packet
    assert packet['source_origin']==origin and packet['actual_execution_bound'] is packet['source_verification_performed'] is False
    changed=deepcopy(values);changed['source_origin']['schema']=dto.MINUTE1_SOURCE_SCHEMA
    with pytest.raises(ValueError):dto.build_compiled_flow_input(**changed)


@pytest.mark.parametrize('change',['advance','imported_thermal_selector'])
def test_actual_essential_live_function_or_import_cannot_be_substituted(monkeypatch,change):
    from tools.annual_program import witness
    from tools.sci_engineering.runner import source_binding
    from tools.essential_fixes import execution
    assert hasattr(witness,'_minute_thermal_loaded_functions'),'Thermal V2 live attestation is missing'
    assert witness._MINUTE_THERMAL_LOADED_MODULES['tools.essential_fixes.execution']==ESSENTIAL
    if change=='advance':monkeypatch.setattr(execution.EssentialResponseMixin,'advance',lambda *args,**kwargs:None)
    else:monkeypatch.setattr(execution,'thermal_source_at',lambda *args:None)
    with pytest.raises(ValueError,match='loaded|global|code|source'):
        witness._minute_thermal_loaded_functions(ROOT,source_binding(ROOT))


def test_old_minute_and_fuel_literal_registries_remain_identical_to_initfix_commit():
    names={'SCI_FUEL_NATIVE_SOURCE_CONTRACT','SCI_FUEL_CAPTURE_EXTRA_SOURCE_CONTRACT','SCI_FUEL_CAPTURE_RECORDER_SHA256',
        'MINUTE1_CAPTURE_PROFILE','MINUTE1_ADMISSION_SCHEMA'}
    path='tools/annual_program/flow_source_contracts.py'
    old=subprocess.run(['git','show','8c63f3698e4197723ca8c939b258ff74e5c4f66e:'+path],cwd=ROOT,capture_output=True,check=True).stdout
    def selected(raw):
        return {target.id:ast.literal_eval(node.value) for node in ast.parse(raw).body if isinstance(node,ast.Assign)
            for target in node.targets if isinstance(target,ast.Name) and target.id in names}
    assert selected(old)==selected((ROOT/path).read_bytes())


def test_actual_essential_whole_ast_has_only_the_three_reviewed_minute_selector_edits():
    original=subprocess.run(['git','show','8c63f3698e4197723ca8c939b258ff74e5c4f66e:'+ESSENTIAL],cwd=ROOT,capture_output=True,check=True).stdout
    before=ast.parse(original);after=ast.parse((ROOT/ESSENTIAL).read_bytes());edits=[]
    for node in ast.walk(after):
        if isinstance(node,ast.ImportFrom) and node.module=='hospital_ies.state_transition':
            additions=[item for item in node.names if item.name=='thermal_source_at']
            assert len(additions)==1 and additions[0].asname is None
            node.names=[item for item in node.names if item.name!='thermal_source_at'];edits.append('import')
        elif isinstance(node,ast.Assign) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='thermal_source_at':
            assert ast.dump(node.value,include_attributes=False)==ast.dump(ast.parse('thermal_source_at(e,self.time,h)',mode='eval').body,include_attributes=False)
            assert ast.dump(node.targets[0],include_attributes=False)==ast.dump(ast.parse('params,zones=0').body[0].targets[0],include_attributes=False)
            node.targets[0]=ast.parse('params,byhour=0').body[0].targets[0];node.value=ast.parse('e.thermal_cache',mode='eval').body;edits.append('selector')
        elif isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='update_thermal':
            assert isinstance(node.args[2],ast.Name) and node.args[2].id=='zones'
            node.args[2]=ast.parse('byhour[h]',mode='eval').body;edits.append('argument')
    assert sorted(edits)==['argument','import','selector']
    assert ast.dump(after,include_attributes=False)==ast.dump(before,include_attributes=False)
