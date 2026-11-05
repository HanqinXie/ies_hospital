"""Pure source-release admission tests; no model or optimizer is executed."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from tools.annual_program import flow_source_contracts as contracts
from tools.annual_program import steam
from reviewed_source_fixtures import reviewed_source_bytes


BACKEND='src/hospital_ies/solver_backend.py'


def register_cache_release(monkeypatch):
    monkeypatch.setattr(contracts,'EXACT_CACHE_BACKEND_SHA256','a'*64,raising=False)
    monkeypatch.setattr(contracts,'EXACT_CACHE_HELPER_SOURCE_CONTRACT',
        {'src/hospital_ies/solver_result_cache.py':'b'*64},raising=False)
    result=dict(contracts.CAPTURE_NATIVE_SOURCE_CONTRACT)
    result[BACKEND]='a'*64
    return result


def test_reviewed_cache_release_matches_exact_map_and_returns_detached_contract(monkeypatch):
    observed=register_cache_release(monkeypatch)
    assert contracts.match_native_source_profile(observed)==contracts.CAPTURE_PROFILE
    recognized=contracts.recognized_capture_native_source_contract(observed)
    assert recognized==observed and recognized is not observed
    recognized[BACKEND]='c'*64
    assert contracts.recognized_capture_native_source_contract(observed)==observed
    pair=contracts.classify_recorder_source_pair(contracts.CAPTURE_RECORDER_SHA256,observed)
    assert pair['supported'] is pair['compiled_source_profile_allowed'] is True
    assert pair['source_authentication_performed'] is False


def test_old_capture_profile_remains_registered_and_has_no_cache_extra():
    old=deepcopy(contracts.CAPTURE_NATIVE_SOURCE_CONTRACT)
    assert contracts.recognized_capture_native_source_contract(old)==old
    assert contracts.capture_source_extra_contract(old)=={}
    assert contracts.match_native_source_profile(old)==contracts.CAPTURE_PROFILE


@pytest.mark.parametrize('change',['backend','other_native','helper_missing','helper_extra','helper_invalid'])
def test_unknown_or_incomplete_cache_release_does_not_register(monkeypatch,change):
    observed=register_cache_release(monkeypatch)
    if change=='backend':observed[BACKEND]='c'*64
    elif change=='other_native':observed['tools/model_closeout/numerics.py']='c'*64
    elif change=='helper_missing':monkeypatch.setattr(contracts,'EXACT_CACHE_HELPER_SOURCE_CONTRACT',None)
    elif change=='helper_extra':monkeypatch.setattr(contracts,'EXACT_CACHE_HELPER_SOURCE_CONTRACT',{'foreign.py':'b'*64})
    else:monkeypatch.setattr(contracts,'EXACT_CACHE_HELPER_SOURCE_CONTRACT',{'src/hospital_ies/solver_result_cache.py':None})
    assert contracts.recognized_capture_native_source_contract(observed) is None
    assert contracts.match_native_source_profile(observed) is None
    assert contracts.classify_recorder_source_pair(contracts.CAPTURE_RECORDER_SHA256,observed)['supported'] is False


def test_frozen12_recorder_does_not_gain_new_cache_backend_approval(monkeypatch):
    observed=register_cache_release(monkeypatch)
    pair=contracts.classify_recorder_source_pair(contracts.FROZEN12_CAPTURE_RECORDER_SHA256,observed)
    assert pair['supported'] is pair['compiled_source_profile_allowed'] is False


def cache_context(tmp_path,monkeypatch):
    source=Path(__file__).parents[1]
    expected=dict(contracts.CAPTURE_NATIVE_SOURCE_CONTRACT,**contracts.CAPTURE_AUXILIARY_SOURCE_CONTRACT)
    expected[BACKEND]=hashlib.sha256(b'reviewed exact-cache backend fixture').hexdigest()
    helper='src/hospital_ies/solver_result_cache.py'
    expected[helper]=hashlib.sha256(b'reviewed pure cache helper fixture').hexdigest()
    monkeypatch.setattr(contracts,'EXACT_CACHE_BACKEND_SHA256',expected[BACKEND],raising=False)
    monkeypatch.setattr(contracts,'EXACT_CACHE_HELPER_SOURCE_CONTRACT',{helper:expected[helper]},raising=False)
    for relative in expected:
        path=tmp_path/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'reviewed exact-cache backend fixture' if relative==BACKEND
            else b'reviewed pure cache helper fixture' if relative==helper else reviewed_source_bytes(relative))
    return dict(source_root=str(tmp_path),source_hashes=expected),helper


def test_observer_authenticates_new_native_map_and_cache_helper_bytes(tmp_path,monkeypatch):
    context,_=cache_context(tmp_path,monkeypatch)
    assert steam._physical_formula_profile(context)==contracts.CAPTURE_PROFILE


@pytest.mark.parametrize('change',['helper_bytes','helper_binding','native_bytes'])
def test_observer_cache_profile_rejects_source_or_binding_drift(tmp_path,monkeypatch,change):
    context,helper=cache_context(tmp_path,monkeypatch)
    if change=='helper_binding':context['source_hashes'][helper]='c'*64
    else:
        path=tmp_path/(helper if change=='helper_bytes' else BACKEND)
        path.write_bytes(path.read_bytes()+b'\n# drift')
    with pytest.raises(steam.native._Issue) as error:steam._physical_formula_profile(context)
    assert error.value.status=='failed'
    assert 'compiled_flow_source_bytes_or_binding_mismatch:' in error.value.reason


def test_warm_formula_memo_never_hides_a_changed_helper_binding(tmp_path,monkeypatch):
    context,helper=cache_context(tmp_path,monkeypatch)
    assert steam._physical_formula_profile(context)==contracts.CAPTURE_PROFILE
    context['source_hashes'][helper]='c'*64
    with pytest.raises(steam.native._Issue) as error:steam._physical_formula_profile(context)
    assert error.value.status=='failed'
    assert error.value.reason=='compiled_flow_source_bytes_or_binding_mismatch:'+helper
