"""Reviewed S16/S17 cache releases remain distinct exact source pairs."""
from tools.annual_program import flow_source_contracts as contracts

BACKEND='src/hospital_ies/solver_backend.py'
HELPER='src/hospital_ies/solver_result_cache.py'
S16_BACKEND='6d6874146d368ace320fe471bd25f5107aa88d4db46495f6a2c6b3d416875576'
S16_HELPER='82a1f6ef5bb7c5d7b078f25092bca3306837efe5cbae8f03336df4a09f89002a'
S17_BACKEND='5c67375a12d9972862bc0fa24b98fef07da9309e7c4ad01770223d1c7dad8fdd'
S17_HELPER='0ecbf955523f8c7a8a2a45ab38931dbe9cf86aac5f17625e329cedd0701725e4'


def source(backend):
    return dict(contracts.CAPTURE_NATIVE_SOURCE_CONTRACT,**{BACKEND:backend})


def test_current_s17_and_frozen_s16_select_their_own_helper_only():
    assert contracts.EXACT_CACHE_BACKEND_SHA256==S17_BACKEND
    assert contracts.FROZEN16_EXACT_CACHE_BACKEND_SHA256==S16_BACKEND
    # Before the local resolver, current and frozen16 happened to have the
    # same recorder bytes. Distinct recorders must use their reviewed pair.
    for backend,helper,recorder in ((S16_BACKEND,S16_HELPER,contracts.FROZEN16_CAPTURE_RECORDER_SHA256),
            (S17_BACKEND,S17_HELPER,contracts.CAPTURE_RECORDER_SHA256)):
        observed=source(backend)
        assert contracts.recognized_capture_native_source_contract(observed)==observed
        assert contracts.match_native_source_profile(observed)==contracts.CAPTURE_PROFILE
        assert contracts.capture_source_extra_contract(observed)=={HELPER:helper}
        assert contracts.classify_recorder_source_pair(recorder,observed)['compiled_source_profile_allowed'] is True


def test_frozen16_recorder_pair_survives_advancing_current_recorder(monkeypatch):
    old='f38fc6e4f45886599f3fe1d6efa2b84013ba21ed3607f131293d4fbb25bb697e'
    assert contracts.FROZEN16_CAPTURE_RECORDER_SHA256==old
    monkeypatch.setattr(contracts,'CAPTURE_RECORDER_SHA256','b'*64)
    assert contracts.classify_recorder_source_pair(old,source(S16_BACKEND))['compiled_source_profile_allowed'] is True
    # A60602 already produced S17 with these exact old recorder bytes. Keep
    # that historical pair readable when the current recorder advances.
    assert contracts.FROZEN_A60602_CAPTURE_RECORDER_SHA256==old
    assert contracts.classify_recorder_source_pair(old,source(S17_BACKEND))['compiled_source_profile_allowed'] is True
    assert contracts.classify_recorder_source_pair('b'*64,source(S17_BACKEND))['compiled_source_profile_allowed'] is True
    assert contracts.classify_recorder_source_pair('b'*64,source(S16_BACKEND))['supported'] is False


def test_frozen12_cannot_attest_either_cache_release():
    for backend in (S16_BACKEND,S17_BACKEND):
        assert contracts.classify_recorder_source_pair(contracts.FROZEN12_CAPTURE_RECORDER_SHA256,source(backend))['supported'] is False


def test_unknown_backend_cannot_choose_any_helper():
    observed=source('e'*64)
    assert contracts.recognized_capture_native_source_contract(observed) is None
    assert contracts.capture_source_extra_contract(observed) is None


def test_conflicting_literal_helper_pairs_fail_closed(monkeypatch):
    monkeypatch.setattr(contracts,'EXACT_CACHE_BACKEND_SHA256',S16_BACKEND)
    monkeypatch.setattr(contracts,'EXACT_CACHE_HELPER_SOURCE_CONTRACT',{HELPER:S17_HELPER})
    assert contracts.recognized_capture_native_source_contract(source(S16_BACKEND)) is None
    assert contracts.capture_source_extra_contract(source(S16_BACKEND)) is None
