"""A shared display ID cannot replace exogenous/reset matching evidence."""
import importlib
import pytest

def api():
    return importlib.import_module('tools.execution_recovery.summarize')

def test_same_identity_with_different_scene_is_rejected():
    seen={}; key=('cohort','source','config')
    first={'id':'same_id','faults':{'GRID':0},'initialization_seed':123}
    second={'id':'same_id','faults':{'GT_1':0},'initialization_seed':123}
    digest=api().bind_scene_definition(seen,key,first)
    assert api().bind_scene_definition(seen,key,dict(first))==digest
    with pytest.raises(ValueError,match='definition'):
        api().bind_scene_definition(seen,key,second)

def test_missing_or_false_matching_is_not_a_paired_comparison():
    assert api().valid_matching_evidence(None,2) is False
    assert api().valid_matching_evidence({'same_initial_state':True,'same_exogenous_definition':True},2) is False

def test_declared_matching_must_agree_with_actual_hash_lists():
    data=dict(same_initial_state=True,same_exogenous_definition=True,
        initial_state_sha256=['a'*64,'a'*64],applied_exogenous_definition_sha256=['b'*64,'b'*64])
    assert api().valid_matching_evidence(data,2) is True
    assert api().valid_matching_evidence(data,3) is False
    data['initial_state_sha256'][1]='c'*64
    assert api().valid_matching_evidence(data,2) is False
