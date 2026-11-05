"""Frozen 4a SCI1 source declarations: bytes/hash/AST only; never loaded execution."""
from copy import deepcopy
import hashlib
from itertools import product
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.annual_program import flow_source_contracts as contracts
from tools.annual_program import witness
from tools.annual_program.compiled_flow_input import SOURCE_SCHEMA


PROFILE='single_fault_continuous_storage_capture_v1'
EXTRA_FILES=frozenset((
    'src/hospital_ies/solver_result_cache.py',
    'tools/sci_engineering/storage.py',
    'tools/single_fault_model_20261102/__init__.py',
    'tools/single_fault_model_20261102/storage.py',
    'tools/single_fault_model_20261102/runtime.py',
    'tools/single_fault_model_20261102/environment.py',
    'tools/single_fault_model_20261102/runner.py',
    'tools/integrated_learning/snapshot.py',
    'tools/model_review_v27/objective_snapshot.py',
    'tools/eight_fault_study/runtime.py',
    'tools/annual_program/source_runtime.py'))


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def current_native():return {name:sha(ROOT/name) for name in contracts.LEGACY_NATIVE_SOURCE_CONTRACT}


def current_freeze():
    return {name:sha(ROOT/name) for name in
        (*contracts.LEGACY_NATIVE_SOURCE_CONTRACT,*contracts.CAPTURE_AUXILIARY_SOURCE_CONTRACT,*EXTRA_FILES)}


def test_current_scientific_map_is_distinct_from_every_historical_profile():
    observed=current_native()
    assert contracts.match_native_source_profile(observed)==PROFILE
    assert contracts.SCIENTIFIC_CAPTURE_PROFILE==PROFILE
    assert contracts.SCIENTIFIC_NATIVE_SOURCE_CONTRACT==observed
    assert contracts.recognized_capture_native_source_contract(observed) is None
    assert contracts.capture_source_extra_contract(observed) is None
    assert contracts.recognized_scientific_native_source_contract(observed)==observed
    extra=contracts.scientific_capture_source_extra_contract(observed)
    assert set(extra)==EXTRA_FILES
    assert extra=={name:sha(ROOT/name) for name in EXTRA_FILES}


def test_new_leaf_is_reviewed_scientific_change_without_old_ast_equivalence():
    report=contracts.inspect_leaf_migration((ROOT/'tools/operating_response/physical_balance.py').read_bytes())
    assert report['shape_valid'] is True
    assert report['design_equivalent'] is False
    assert report['original_function_ast_equal'] is report['original_module_ast_equal'] is False
    assert report.get('reviewed_scientific_model_change') is True
    assert report['runtime_profile_approved'] is True
    assert report['source_authentication_performed'] is report['actual_execution_bound'] is False
    assert report['requires_external_source_authentication'] is report['requires_exact_final_result_binding'] is True


def test_scientific_pair_matches_only_the_exact_new_recorder():
    observed=current_native();recorder=sha(ROOT/'tools/annual_program/witness.py')
    pair=contracts.classify_recorder_source_pair(recorder,observed)
    assert pair['supported'] is pair['compiled_source_profile_allowed'] is True
    assert pair['source_profile']==PROFILE
    assert recorder==contracts.SCIENTIFIC_CAPTURE_RECORDER_SHA256
    assert pair['source_authentication_performed'] is pair['actual_execution_bound'] is False
    old=(contracts.LEGACY_RECORDER_SHA256,contracts.CAPTURE_RECORDER_SHA256,
        contracts.FROZEN12_CAPTURE_RECORDER_SHA256,contracts.FROZEN16_CAPTURE_RECORDER_SHA256,
        contracts.FROZEN_A60602_CAPTURE_RECORDER_SHA256)
    assert recorder not in old
    for value in (*old,'0'*64,None):
        result=contracts.classify_recorder_source_pair(value,observed)
        assert result['supported'] is result['compiled_source_profile_allowed'] is False


def test_new_recorder_exact_legacy14_pair_is_explicitly_basic_only():
    recorder=sha(ROOT/'tools/annual_program/witness.py')
    pair=contracts.classify_recorder_source_pair(recorder,dict(contracts.LEGACY_NATIVE_SOURCE_CONTRACT))
    assert pair['supported'] is pair['basic_witness_allowed'] is True
    assert pair['source_profile']==contracts.LEGACY_PROFILE
    assert pair['compiled_source_profile_allowed'] is False
    assert pair['source_authentication_performed'] is pair['actual_execution_bound'] is False
    for backend in (None,contracts.EXACT_CACHE_BACKEND_SHA256,contracts.FROZEN16_EXACT_CACHE_BACKEND_SHA256):
        observed=dict(contracts.CAPTURE_NATIVE_SOURCE_CONTRACT)
        if backend is not None:observed['src/hospital_ies/solver_backend.py']=backend
        rejected=contracts.classify_recorder_source_pair(recorder,observed)
        assert rejected['supported'] is rejected['compiled_source_profile_allowed'] is False


@pytest.mark.parametrize('old_bits',list(product((False,True),repeat=3)))
def test_all_partial_scientific_legacy_mixes_fail_without_new_recorder_qualification(old_bits):
    observed=current_native()
    changes=('tools/operating_response/physical_balance.py','tools/model_correction_v25/reserve.py',
        'tools/model_correctness_v23/environment.py')
    for old,relative in zip(old_bits,changes):
        if old:observed[relative]=contracts.CAPTURE_NATIVE_SOURCE_CONTRACT[relative]
    pair=contracts.classify_recorder_source_pair(sha(ROOT/'tools/annual_program/witness.py'),observed)
    if not any(old_bits):
        assert pair['source_profile']==PROFILE and pair['compiled_source_profile_allowed'] is True
    else:
        # Restoring all three files yields an exact readable old cache release;
        # every partial restoration is unknown. Neither is this new pair.
        assert pair['source_profile']==(contracts.CAPTURE_PROFILE if all(old_bits) else None)
        assert pair['supported'] is pair['compiled_source_profile_allowed'] is False


@pytest.mark.parametrize('relative',(
    'tools/operating_response/physical_balance.py','tools/model_correction_v25/reserve.py',
    'tools/model_correctness_v23/environment.py'))
def test_mixed_scientific_and_historical_native_maps_fail_closed(relative):
    observed=current_native();observed[relative]=contracts.LEGACY_NATIVE_SOURCE_CONTRACT[relative]
    assert contracts.match_native_source_profile(observed) is None
    assert contracts.classify_recorder_source_pair(sha(witness.__file__),observed)['supported'] is False


def test_scientific_map_results_are_detached_and_have_no_caller_approval_api():
    observed=current_native()
    resolver=getattr(contracts,'recognized_scientific_native_source_contract',None)
    assert callable(resolver)
    result=resolver(observed);result[next(iter(result))]='0'*64
    assert resolver(observed)==observed
    extra=contracts.scientific_capture_source_extra_contract(observed)
    extra[next(iter(extra))]='0'*64
    assert contracts.scientific_capture_source_extra_contract(observed)=={
        name:sha(ROOT/name) for name in EXTRA_FILES}
    with pytest.raises(TypeError):
        contracts.classify_recorder_source_pair(sha(ROOT/'tools/annual_program/witness.py'),observed,approved_new_sha=sha(ROOT/'tools/annual_program/witness.py'))


def test_scientific_origin_binds_complete_extras_and_only_source_metadata():
    frozen=current_freeze()
    origin=dict(schema=SOURCE_SCHEMA,source_root=str(ROOT.resolve()),source_files=frozen,leaf_function='tools.operating_response.physical_balance.solve_physical')
    assert origin['schema']==SOURCE_SCHEMA
    assert origin['source_files']==frozen
    assert set(origin)=={'schema','source_root','source_files','leaf_function'}
    assert len(origin['source_files'])==14+2+len(EXTRA_FILES)
    binding=witness.native_source_binding(ROOT)
    assert binding['supported'] is True and binding['source_profile']==PROFILE












def test_comment_only_scientific_revision_is_not_registered():
    report=contracts.inspect_leaf_migration((ROOT/'tools/operating_response/physical_balance.py').read_bytes()+b'\n# unregistered science bytes\n')
    assert report['design_equivalent'] is False
    assert report['runtime_profile_approved'] is False


@pytest.mark.parametrize('change',['missing','extra','wrong_type','wrong_ast'])
def test_incomplete_scientific_literal_release_does_not_grant_capture(monkeypatch,change):
    assert hasattr(contracts,'SCIENTIFIC_CAPTURE_EXTRA_SOURCE_CONTRACT')
    if change=='wrong_ast':monkeypatch.setattr(contracts,'SCIENTIFIC_PHYSICAL_MODULE_AST_SHA256',None)
    else:
        value=deepcopy(contracts.SCIENTIFIC_CAPTURE_EXTRA_SOURCE_CONTRACT)
        if change=='missing':value.pop(next(iter(value)))
        elif change=='extra':value['tools/unreviewed.py']='0'*64
        else:value[next(iter(value))]=None
        monkeypatch.setattr(contracts,'SCIENTIFIC_CAPTURE_EXTRA_SOURCE_CONTRACT',value)
    assert contracts.classify_recorder_source_pair(sha(witness.__file__),current_native())['compiled_source_profile_allowed'] is False


@pytest.fixture(autouse=True)
def frozen_scientific_source(tmp_path,monkeypatch):
    """No fixture source is imported, compiled or executed."""
    from reviewed_sci1_source_fixtures import _fixture,reviewed_source_bytes
    historical=tmp_path/'sci1_parse_only';historical.mkdir()
    for relative in _fixture()['files']:
        path=historical/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(reviewed_source_bytes(relative))
    monkeypatch.setattr(sys.modules[__name__],'ROOT',historical)
