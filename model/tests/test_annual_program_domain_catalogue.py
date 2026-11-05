"""Bounded metadata catalogue, independent of blob durability and simulation."""
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys
from collections.abc import Mapping

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

from tools.model_correctness_v23.evidence import DomainEvidenceStore, REFERENCE_SCHEMA, ALL_REFERENCE_SCHEMA

IDENTITY={'case_id':'synthetic-catalogue-test','source_sha256':'a'*64,'input_sha256':'b'*64}


def implementation():
    try:return importlib.import_module('tools.annual_program.domain_catalogue')
    except ModuleNotFoundError as exc:
        if exc.name!='tools.annual_program.domain_catalogue':raise
        pytest.fail('Bounded append-only native domain catalogue is missing',pytrace=False)


def reference(number):
    sha=hashlib.sha256(('synthetic-row-table-'+str(number)).encode()).hexdigest()
    return sha,dict(schema=REFERENCE_SCHEMA if number%2 else ALL_REFERENCE_SCHEMA,
        directory='domain_evidence',file=sha+'.json.gz',rows_sha256=sha,
        row_count=number,canonical_bytes=number+2,gzip_bytes=number+22,
        canonicalization='UTF8 sorted compact JSON; nonfinite floats -> null')


def catalogue(tmp_path,limit=3):
    return implementation().DomainCatalogue(tmp_path/'catalogue.sqlite',identity=IDENTITY,cache_limit=limit)


def test_many_pending_and_committed_refs_preserve_native_order_and_bounded_cache(tmp_path):
    cat=catalogue(tmp_path)
    pairs=[reference(n) for n in range(80)]
    for key,value in pairs:
        cat[key]=value
        assert key in cat and cat[key]==value
        assert cat.cache_info()['decoded_ref_count']<=3
    assert len(cat)==80 and list(cat)==[key for key,_ in pairs]
    assert list(cat.values())==[value for _,value in pairs]
    assert list(cat.items())==pairs
    assert cat.cache_info()['decoded_ref_count']<=3
    with pytest.raises((ValueError,RuntimeError),match='committed|pending|quiescent'):
        cat.to_state()
    receipt=cat.commit()
    assert receipt['count']==80 and receipt['version']==1
    assert receipt['raw_blob_durability_verified'] is False
    assert receipt['raw_blob_authentication_verified'] is False
    assert receipt['requires_external_blob_proof_before_journal_ack'] is True
    state=cat.to_state()
    assert len(json.dumps(state))<2000
    assert 'references' not in state and 'files' not in state
    cat.close()


def test_native_domain_store_lookup_dedup_and_receipt_work_without_source_changes(tmp_path):
    cat=catalogue(tmp_path,limit=1)
    store=DomainEvidenceStore(tmp_path/'domain_evidence',enable_all_validation_rows=True)
    store._known=cat  # A local test instance only; production integration is not enabled.
    rows=[{'name':'one','activity':1.}]
    first=store.store(rows)
    first['row_count']=999
    actual=store.store(rows)
    second=store.store([{'name':'two','activity':2.}])
    assert actual['row_count']==1 and len(cat)==2
    assert store.receipt()['closed_blobs']==2
    assert store.receipt()['gzip_bytes']==actual['gzip_bytes']+second['gzip_bytes']
    assert list(cat)==[actual['rows_sha256'],second['rows_sha256']]
    # Rollback affects metadata only; the helper does not delete/qualify raw blobs.
    rolled=cat.rollback()
    assert rolled['rolled_back_additions']==2 and len(cat)==0
    assert len(list((tmp_path/'domain_evidence').glob('*.json.gz')))==2
    cat.close()


def test_reads_and_insertion_inputs_are_detached_and_conflicts_never_overwrite(tmp_path):
    cat=catalogue(tmp_path)
    key,value=reference(1)
    original=copy.deepcopy(value)
    cat[key]=value
    value['row_count']=700
    returned=cat[key]
    returned['gzip_bytes']=900
    next(iter(cat.values()))['canonical_bytes']=800
    assert cat[key]==original
    cat.commit()
    before=cat.to_state()
    cat[key]=copy.deepcopy(original)
    assert cat.to_state()==before
    conflict=dict(original,gzip_bytes=original['gzip_bytes']+1)
    with pytest.raises(ValueError,match='conflict|overwrite'):cat[key]=conflict
    assert cat[key]==original and cat.to_state()==before
    with pytest.raises(TypeError):del cat[key]
    for function in (copy.copy,copy.deepcopy):
        with pytest.raises(TypeError,match='snapshot|descriptor|whole'):function(cat)
    cat.close()


def test_uncommitted_visibility_explicit_rollback_and_close_never_commit(tmp_path):
    cat=catalogue(tmp_path)
    empty=cat.to_state()
    key,value=reference(1)
    cat[key]=value
    assert len(cat)==1 and cat[key]==value
    with sqlite3.connect(cat.path.as_uri()+'?mode=ro',uri=True) as external:
        assert external.execute('SELECT COUNT(*) FROM entries').fetchone()==(0,)
    receipt=cat.rollback()
    assert receipt['rolled_back_additions']==1 and key not in cat
    assert cat.to_state()==empty
    cat[key]=value
    receipt=cat.close()
    assert receipt['rolled_back_additions']==1
    restored=implementation().DomainCatalogue.from_state(empty,identity=IDENTITY,cache_limit=2,read_only=True)
    assert len(restored)==0
    restored.close()


def test_failed_commit_requires_explicit_rollback_and_does_not_fabricate_ack(tmp_path):
    cat=catalogue(tmp_path)
    empty=cat.to_state()
    key,value=reference(1)
    cat[key]=value
    connection=cat._connection
    class FailCommit:
        def commit(self):raise OSError('injected failure before SQLite commit')
        def __getattr__(self,name):return getattr(connection,name)
    cat._connection=FailCommit()
    with pytest.raises(OSError,match='injected'):cat.commit()
    with pytest.raises(RuntimeError,match='failure|failed'):cat.to_state()
    assert cat.rollback()['rolled_back_additions']==1
    assert cat.to_state()==empty and len(cat)==0
    cat.close()


def test_descriptor_restore_is_exact_and_writable_continuation_appends(tmp_path):
    cat=catalogue(tmp_path)
    pairs=[reference(n) for n in range(12)]
    for key,value in pairs:cat[key]=value
    cat.commit()
    state=cat.snapshot()
    cat.close()
    restored=implementation().DomainCatalogue.from_state(state,identity=IDENTITY,cache_limit=2)
    assert restored.to_state()==state and list(restored.items())==pairs
    key,value=reference(12)
    restored[key]=value
    assert restored[key]==value
    second=restored.commit()
    assert second['count']==13 and second['version']==2
    restored.close()


@pytest.mark.parametrize('damage',['state_checksum','wrong_identity','wrong_path','missing','newer',
    'payload','orphan_row','schema','extra_table'])
def test_missing_newer_or_corrupt_catalogue_never_accepts_old_prefix_or_recreates(tmp_path,damage):
    api=implementation()
    cat=catalogue(tmp_path)
    key,value=reference(1)
    cat[key]=value
    cat.commit()
    state=cat.to_state()
    identity=IDENTITY
    override=None
    if damage=='newer':
        other,ref=reference(2)
        cat[other]=ref
        cat.commit()
    cat.close()
    if damage=='state_checksum':state=dict(state,count=9)
    elif damage=='wrong_identity':identity=dict(IDENTITY,case_id='different-case')
    elif damage=='wrong_path':override=tmp_path/'other.sqlite'
    elif damage=='missing':Path(state['path']).unlink()
    elif damage in ('payload','orphan_row','schema','extra_table'):
        with sqlite3.connect(state['path']) as db:
            if damage=='payload':db.execute("UPDATE entries SET payload=x'7b7d' WHERE seq=0")
            elif damage=='orphan_row':
                other,_=reference(2)
                db.execute('INSERT INTO entries SELECT 1, ?, payload, payload_sha, previous_digest, digest FROM entries WHERE seq=0',(other,))
            elif damage=='schema':db.execute("UPDATE header SET schema='unsupported-schema'")
            else:db.execute('CREATE TABLE orphan_extra (unknown TEXT)')
    path=Path(state['path'])
    before=path.read_bytes() if path.exists() else None
    with pytest.raises((ValueError,RuntimeError)):
        api.DomainCatalogue.from_state(state,identity=identity,cache_limit=2,path=override)
    assert (path.read_bytes() if path.exists() else None)==before
    assert not (tmp_path/'other.sqlite').exists()


def test_readonly_authentication_streams_rows_and_never_opens_a_writer(tmp_path,monkeypatch):
    cat=catalogue(tmp_path)
    pairs=[reference(n) for n in range(50)]
    for key,value in pairs:cat[key]=value
    cat.commit()
    state=cat.to_state()
    cat.close()
    api=implementation()
    original=sqlite3.connect
    opened=[]
    def connect(database,*args,**kwargs):
        opened.append(database)
        assert database.endswith('?mode=ro&immutable=1') and kwargs.get('uri') is True
        return original(database,*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',connect)
    before=Path(state['path']).read_bytes()
    view=api.DomainCatalogue.from_state(state,identity=IDENTITY,cache_limit=2,read_only=True)
    assert list(view.items())==pairs and view.cache_info()['decoded_ref_count']<=2
    with pytest.raises(TypeError,match='read.only'):view[reference(99)[0]]=reference(99)[1]
    with pytest.raises(sqlite3.OperationalError,match='readonly'):
        view._connection.execute('UPDATE header SET version=version+1')
    view.close()
    assert opened and Path(state['path']).read_bytes()==before


@pytest.mark.parametrize('suffix',['-journal','-wal','-shm'])
def test_ambiguous_sqlite_sidecars_block_immutable_restore(tmp_path,suffix):
    cat=catalogue(tmp_path)
    state=cat.to_state()
    cat.close()
    sidecar=Path(state['path']+suffix)
    sidecar.write_bytes(b'preserved ambiguous producer sidecar')
    with pytest.raises(ValueError,match='sidecar'):
        implementation().DomainCatalogue.from_state(state,identity=IDENTITY,cache_limit=1,read_only=True)
    assert sidecar.read_bytes()==b'preserved ambiguous producer sidecar'


@pytest.mark.parametrize('damage',['key','uppercase_key','schema','directory','filename','row_sha',
    'bool_count','float_count','negative_count','nonfinite_size','extra_field','canonicalization'])
def test_only_approved_native_reference_metadata_and_finite_primitive_types_are_accepted(tmp_path,damage):
    cat=catalogue(tmp_path)
    key,value=reference(1)
    if damage=='key':key='not-sha'
    elif damage=='uppercase_key':key=key.upper()
    elif damage=='schema':value['schema']='unregistered'
    elif damage=='directory':value['directory']='../outside'
    elif damage=='filename':value['file']='different.json.gz'
    elif damage=='row_sha':value['rows_sha256']='0'*64
    elif damage=='bool_count':value['row_count']=True
    elif damage=='float_count':value['row_count']=1.
    elif damage=='negative_count':value['row_count']=-1
    elif damage=='nonfinite_size':value['gzip_bytes']=float('nan')
    elif damage=='extra_field':value['unreviewed']=1
    else:value['canonicalization']='different-byte-contract'
    with pytest.raises(ValueError):cat[key]=value
    assert len(cat)==0
    cat.close()


@pytest.mark.parametrize('limit',[True,-1,1.5])
def test_cache_limit_is_explicit_finite_nonnegative_integer(tmp_path,limit):
    with pytest.raises(ValueError):catalogue(tmp_path,limit=limit)


def test_identity_is_case_and_source_bound_and_creation_never_overwrites(tmp_path):
    api=implementation()
    with pytest.raises(ValueError):
        api.DomainCatalogue(tmp_path/'invalid.sqlite',identity={'case_id':'missing-source'},cache_limit=1)
    cat=catalogue(tmp_path)
    before=cat.path.read_bytes()
    with pytest.raises(FileExistsError):catalogue(tmp_path)
    assert cat.path.read_bytes()==before
    cat.close()


def test_descriptor_identity_comparison_preserves_boolean_versus_integer_types(tmp_path):
    api=implementation()
    identity=dict(IDENTITY,run_number=1)
    cat=api.DomainCatalogue(tmp_path/'typed.sqlite',identity=identity,cache_limit=1)
    state=cat.to_state()
    cat.close()
    state['identity']['run_number']=True  # Python dict equality alone conflates True and1.
    state['content_sha256']=hashlib.sha256(json.dumps({key:value for key,value in state.items()
        if key!='content_sha256'},ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()
    with pytest.raises(ValueError,match='identity|descriptor'):
        api.DomainCatalogue.from_state(state,identity=identity,cache_limit=1,read_only=True)


def test_cache_bound_and_database_path_cannot_change_after_binding(tmp_path):
    cat=catalogue(tmp_path)
    original_path=cat.path
    with pytest.raises(AttributeError):cat.cache_limit=100000
    with pytest.raises(AttributeError):cat.path=tmp_path/'different.sqlite'
    assert cat.cache_info()['cache_limit']==3 and cat.path==original_path
    cat.close()


def test_reference_strings_require_plain_primitive_types(tmp_path):
    class UnreviewedString(str):pass
    cat=catalogue(tmp_path)
    key,value=reference(1)
    value['canonicalization']=UnreviewedString(value['canonicalization'])
    with pytest.raises(ValueError):cat[key]=value
    assert len(cat)==0
    cat.close()


def test_failure_after_sqlite_commit_is_ambiguous_and_never_erased_by_rollback(tmp_path,monkeypatch):
    cat=catalogue(tmp_path)
    before=cat.to_state()
    key,value=reference(1)
    cat[key]=value
    def fail_fsync():raise OSError('injected fsync failure after real SQLite commit')
    monkeypatch.setattr(cat,'_fsync',fail_fsync)
    with pytest.raises(OSError,match='injected fsync'):cat.commit()
    with pytest.raises(RuntimeError,match='failure'):cat.to_state()
    with pytest.raises(ValueError,match='ambiguous|newer'):cat.rollback()
    with pytest.raises(ValueError,match='ambiguous|newer'):cat.close()
    with sqlite3.connect(before['path']) as connection:
        assert connection.execute('SELECT count,version FROM header').fetchone()==(1,1)
        assert connection.execute('SELECT COUNT(*) FROM entries').fetchone()==(1,)
    with pytest.raises(ValueError):
        implementation().DomainCatalogue.from_state(before,identity=IDENTITY,cache_limit=1,read_only=True)


def test_mapping_equality_does_not_inherit_a_hidden_full_dictionary_copy(tmp_path):
    cat=catalogue(tmp_path,limit=2)
    expected=dict(reference(n) for n in range(12))
    for key,value in expected.items():cat[key]=value
    class LookupOnlyMapping(Mapping):
        def __len__(self):return len(expected)
        def __getitem__(self,key):return expected[key]
        def __iter__(self):raise AssertionError('Equality attempted to materialize the whole other mapping')
    assert cat==LookupOnlyMapping()
    assert cat.cache_info()['decoded_ref_count']<=2
    cat.rollback()
    cat.close()


def test_dangling_supplied_symlink_is_rejected_before_resolve_or_target_creation(tmp_path,monkeypatch,record_property):
    api=implementation()
    supplied=tmp_path/'dangling-catalogue-link.sqlite'
    target=tmp_path/'must-not-be-created.sqlite'
    try:
        supplied.symlink_to(target)
        record_property('dangling_symlink_fixture','actual_filesystem_symlink')
    except (OSError,NotImplementedError) as exc:
        if isinstance(exc,OSError) and getattr(exc,'winerror',None) not in (5,1314):raise
        # Windows can deny symlink creation without developer mode/SeCreateSymbolicLinkPrivilege.
        # Precisely reproduce the two different paths seen by the constructor:
        # supplied spelling is a link; resolution produces a non-link absent target.
        original_resolve,original_is_symlink=Path.resolve,Path.is_symlink
        def resolve(path,*args,**kwargs):
            return target if path==supplied else original_resolve(path,*args,**kwargs)
        def is_symlink(path):
            return True if path==supplied else original_is_symlink(path)
        monkeypatch.setattr(Path,'resolve',resolve)
        monkeypatch.setattr(Path,'is_symlink',is_symlink)
        record_property('dangling_symlink_fixture','precise_path_fixture_windows_privilege_unavailable')
    assert supplied.is_symlink() and not target.exists()
    with pytest.raises(ValueError,match='link|symlink'):
        api.DomainCatalogue(supplied,identity=IDENTITY,cache_limit=1)
    assert not target.exists()
