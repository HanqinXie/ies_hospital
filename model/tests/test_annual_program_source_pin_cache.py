"""Bound repeated source checks, retaining full durable-boundary verification."""
import hashlib
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from tools.annual_program import domain_store as api
from tools.model_correctness_v23 import evidence as native


@pytest.fixture
def pinned(tmp_path,monkeypatch):
    path=tmp_path/'native_fixture.py';path.write_text('def store(self, rows):\n    return "ORIGINAL"\n',encoding='utf8')
    namespace={};exec(compile(path.read_text(encoding='utf8'),str(path),'exec'),namespace)
    function=namespace['store']
    monkeypatch.setattr(api,'_NATIVE_STORE',function)
    if hasattr(api,'_NATIVE_CODE'):monkeypatch.setattr(api,'_NATIVE_CODE',function.__code__)
    monkeypatch.setattr(native.DomainEvidenceStore,'store',function)
    if hasattr(api,'_PIN_CACHE'):api._PIN_CACHE.clear()
    binding=dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    yield path,binding,function
    if hasattr(api,'_PIN_CACHE'):api._PIN_CACHE.clear()


def test_unchanged_source_is_hashed_once_between_forced_boundaries(pinned,monkeypatch):
    _,binding,_=pinned;calls=[];original=api._sha
    def sha(path):calls.append(str(path));return original(path)
    monkeypatch.setattr(api,'_sha',sha)
    for _ in range(20):assert api._pin(binding)==binding
    assert len(calls)==1
    assert api._pin(binding,force_hash=True)==binding
    assert len(calls)==2


def test_same_size_restored_mtime_tamper_is_rejected_at_forced_boundary(pinned):
    path,binding,_=pinned;api._pin(binding);stamp=path.stat()
    path.write_bytes(path.read_bytes().replace(b'ORIGINAL',b'ORIGIXAL'))
    os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    assert path.stat().st_size==stamp.st_size
    with pytest.raises(ValueError,match='source|SHA|binding'):
        api._pin(binding,force_hash=True)


def test_changed_metadata_invalidates_the_cached_hash(pinned):
    path,binding,_=pinned;api._pin(binding)
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError):api._pin(binding)


def test_changed_method_is_rejected_even_with_a_warm_cache(pinned,monkeypatch):
    _,binding,_=pinned;api._pin(binding)
    monkeypatch.setattr(native.DomainEvidenceStore,'store',lambda self,rows:None)
    with pytest.raises(ValueError):api._pin(binding)


def test_changed_code_on_the_same_method_cannot_borrow_a_warm_pin(pinned):
    path,binding,function=pinned;api._pin(binding)
    replacement={};exec(compile('def store(self, rows):\n    return "CHANGED"\n',str(path),'exec'),replacement)
    function.__code__=replacement['store'].__code__
    with pytest.raises(ValueError,match='code|method'):api._pin(binding)


def test_cache_stays_bounded_across_distinct_source_functions(pinned,tmp_path,monkeypatch):
    for i in range(20):
        path=tmp_path/f'native_{i}.py';path.write_text(f'def store(self, rows):\n    return {i}\n',encoding='utf8')
        namespace={};exec(compile(path.read_text(encoding='utf8'),str(path),'exec'),namespace)
        function=namespace['store'];monkeypatch.setattr(api,'_NATIVE_STORE',function)
        if hasattr(api,'_NATIVE_CODE'):monkeypatch.setattr(api,'_NATIVE_CODE',function.__code__)
        monkeypatch.setattr(native.DomainEvidenceStore,'store',function)
        binding=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        assert api._pin(binding)==binding
        assert len(api._PIN_CACHE)<=api._PIN_CACHE_LIMIT


def test_changed_binding_or_another_path_cannot_borrow_a_cached_pin(pinned,tmp_path):
    path,binding,_=pinned;api._pin(binding)
    other=tmp_path/'another.py';other.write_bytes(path.read_bytes())
    for changed in (dict(binding,sha256='0'*64),dict(binding,path=str(other))):
        with pytest.raises(ValueError):api._pin(changed)
    detached=api._pin(binding);detached['path']='changed'
    assert api._pin(binding)==binding


def test_create_commit_snapshot_and_restore_force_full_pins(tmp_path,monkeypatch):
    original=api._pin;forced=[]
    def pin(binding,**kwargs):forced.append(kwargs.get('force_hash',False));return original(binding,**kwargs)
    monkeypatch.setattr(api,'_pin',pin)
    path=Path(native.__file__).resolve()
    binding=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    identity=dict(case_id='source-boundary-test',source_sha256='a'*64)
    store=api.create(tmp_path/'domain_evidence',index_directory=tmp_path/'index',identity=identity,native_source_binding=binding)
    try:
        assert forced[-1] is True
        store.store([dict(value=1.)]);before=sum(forced);store.commit()
        assert sum(forced)>=before+2  # Full hash before and after durable commits.
        before=sum(forced);state=store.to_state();assert sum(forced)>=before+1
    finally:store.close()
    before=sum(forced)
    restored=api.from_state(state,identity=identity,native_source_binding=binding,read_only=True)
    try:assert sum(forced)>=before+1
    finally:restored.close()


def test_source_failure_after_both_database_commits_retains_unacknowledged_preimage(tmp_path,monkeypatch):
    path=Path(native.__file__).resolve()
    binding=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    store=api.create(tmp_path/'domain_evidence',index_directory=tmp_path/'index',
        identity=dict(case_id='postcommit-source-failure',source_sha256='a'*64),native_source_binding=binding)
    store.store([dict(value=1.)]);original_commit=store._known.commit;original_sha=api._sha
    def commit_then_source_drift():
        original_commit()
        monkeypatch.setattr(api,'_sha',lambda supplied:'0'*64 if Path(supplied)==path else original_sha(supplied))
    monkeypatch.setattr(store._known,'commit',commit_then_source_drift)
    try:
        with pytest.raises(ValueError,match='source|SHA'):store.commit()
        assert store._failed['stage']=='postcommit_source_pin'
        assert store._committed_count==0
        assert len(store._known)==store._proof_history.to_state()['prefix_count']==1
        with pytest.raises(RuntimeError,match='failed|ambiguous'):store.to_state()
    finally:closed=store.close()
    assert closed['retained_pre_proof_commit']['tail_rows']==1
    assert closed['retained_pre_proof_commit']['is_store_or_journal_acknowledgement'] is False
    assert closed['retained_pre_proof_commit']['automatic_restoration_allowed'] is False
