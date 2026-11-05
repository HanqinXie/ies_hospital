"""Native blob bytes plus two append-only indexes; no physical execution."""
import copy
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

from tools.model_correctness_v23 import evidence as native
from tools.annual_program.disk_history import DiskHistory
from tools.annual_program import failure_history

IDENTITY={'case_id':'synthetic-store-test','source_sha256':'a'*64,'input_sha256':'b'*64}


def implementation():
    try:return importlib.import_module('tools.annual_program.domain_store')
    except ModuleNotFoundError as exc:
        if exc.name!='tools.annual_program.domain_store':raise
        pytest.fail('Bounded native domain store/proof adapter is missing',pytrace=False)


def binding():
    path=Path(native.__file__).resolve()
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def create(tmp_path,**kwargs):
    return implementation().create(tmp_path/'domain_evidence',identity=IDENTITY,
        index_directory=tmp_path/'indexes',native_source_binding=binding(),cache_limit=2,**kwargs)


def checksum(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        allow_nan=False,separators=(',',':')).encode()).hexdigest()


@pytest.mark.parametrize('all_rows',[False,True])
@pytest.mark.parametrize('phase',['pending','restored'])
def test_native_reference_json_bytes_match_after_duplicate_lookup_and_restore_cache_eviction(tmp_path,all_rows,phase):
    api=implementation()
    store=create(tmp_path/'annual',enable_all_validation_rows=all_rows)
    original=native.DomainEvidenceStore(tmp_path/'legacy/domain_evidence',enable_all_validation_rows=all_rows)
    inputs=[[{'lower':float('-inf'),'upper':float('inf'),'actual':n}] for n in range(4)]
    expected=[]
    def encoded(ref):
        return json.dumps(native.normalize_json(ref),ensure_ascii=False,allow_nan=False,
            separators=(',',':')).encode('utf8')
    for rows in inputs:
        wanted=original.store(rows)
        actual=store.store(rows)
        assert tuple(wanted)==('schema','directory','file','rows_sha256','row_count',
            'canonical_bytes','gzip_bytes','canonicalization')
        assert encoded(actual)==encoded(wanted)
        assert (store.directory/actual['file']).read_bytes()==(original.directory/wanted['file']).read_bytes()
        expected.append(wanted)
    proof_before=checksum(store._proof_history[0])
    if phase=='restored':
        store.commit()
        state=store.to_state()
        store.close()
        # A public read-only view remains unable to call store(), even for a
        # duplicate. Exercise the authenticated cold-cache read path first.
        readonly=api.from_state(state,identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)
        for ref in expected:assert readonly._known[ref['rows_sha256']]==ref
        assert readonly._known.cache_info()['decoded_ref_count']<=1
        with pytest.raises(TypeError,match='read.only'):readonly.store(inputs[0])
        assert readonly.to_state()==state
        readonly.close()
        store=api.from_state(state,identity=IDENTITY,native_source_binding=binding(),cache_limit=1)
    try:
        # Ensure the duplicate is decoded from SQLite, not a warm returned ref.
        for ref in expected[1:]:assert store._known[ref['rows_sha256']]==ref
        actual=store.store(inputs[0])
        assert actual==original.store(inputs[0])
        assert encoded(actual)==encoded(original.store(inputs[0]))
        actual['row_count']=999
        actual['unexpected']='detached mutation'
        assert encoded(store.store(inputs[0]))==encoded(original.store(inputs[0]))
        assert checksum(store._proof_history[0])==proof_before
        assert store.adapter_receipt()['proof_count']==len(inputs)
        assert store._known.cache_info()['decoded_ref_count']<=2
        if phase=='restored':assert store.to_state()==state
    finally:store.close()


@pytest.mark.parametrize('all_rows',[False,True])
@pytest.mark.parametrize('damage',['extra','missing'])
def test_unexpected_native_reference_fields_reject_instead_of_being_dropped(tmp_path,monkeypatch,all_rows,damage):
    store=create(tmp_path,enable_all_validation_rows=all_rows)
    rows=[{'actual':1.}]
    store.store(rows)
    catalogue_type=type(store._known)
    getitem=catalogue_type.__getitem__
    def damaged(catalogue,key):
        ref=getitem(catalogue,key)
        if damage=='extra':ref['unexpected']='preserve the failure'
        else:ref.pop('canonicalization')
        return ref
    monkeypatch.setattr(catalogue_type,'__getitem__',damaged)
    try:
        with pytest.raises(ValueError,match='reference.*field|field.*reference'):store.store(rows)
        with pytest.raises(RuntimeError,match='failed|ambiguous'):store.to_state()
    finally:store.close()


@pytest.mark.parametrize('all_rows',[False,True])
def test_native_refs_and_deterministic_gzip_bytes_are_unchanged_including_infinite_bound_normalization(tmp_path,all_rows):
    api=implementation()
    store=create(tmp_path/'annual',enable_all_validation_rows=all_rows)
    original=native.DomainEvidenceStore(tmp_path/'legacy/domain_evidence',enable_all_validation_rows=all_rows)
    inputs=[[],[{'name':'bounds','lower':float('-inf'),'upper':float('inf'),'activity':2.}],
            [{'name':'finite','lower':0.,'upper':3.,'activity':1.}]]
    for rows in inputs:
        expected=original.store(rows)
        actual=store.store(rows)
        assert actual==expected
        assert (store.directory/actual['file']).read_bytes()==(original.directory/expected['file']).read_bytes()
    before=store.adapter_receipt()
    assert before['pending_proof_rows']==3 and before['maximum_pending_proof_rows']==3
    assert before['pending_proof_canonical_bytes']>0
    assert store._known.cache_info()['decoded_ref_count']<=2
    duplicate=store.store(inputs[1]);duplicate['row_count']=99
    assert store.store(inputs[1])['row_count']==1
    assert store.adapter_receipt()['proof_count']==3
    assert {k:v for k,v in store.receipt().items() if k!='directory'}=={k:v for k,v in original.receipt().items() if k!='directory'}
    with pytest.raises((RuntimeError,ValueError),match='pending|committed|quiescent'):store.to_state()
    committed=store.commit()
    assert committed['committed_count']==3 and committed['pending_proof_rows']==0
    assert committed['maximum_pending_proof_rows']==3
    assert committed['numerical_or_physical_validation_performed'] is False
    state=store.to_state()
    assert len(json.dumps(state))<12000
    assert state['proof_readback_adapter']['usage']=='neutral_proof_state_readback_only'
    assert state['proof_readback_adapter']['native_failure_occurred'] is False
    assert state['proof_readback_adapter']['error_record_fabricated'] is False
    assert state['proof_readback_adapter']['schema']==failure_history.SCHEMA
    assert state['proof_readback_adapter']['migration_id']==failure_history.LOG_MIGRATION_ID
    store.close()
    restored=api.from_state(state,identity=IDENTITY,native_source_binding=binding(),cache_limit=1)
    assert restored.to_state()==state
    assert restored.verify_blobs()['canonical_verified_blob_count']==3
    new=restored.store([{'new':4.}])
    assert new['row_count']==1
    assert restored.commit()['committed_count']==4
    restored.close()


def prepared(tmp_path,count=2):
    store=create(tmp_path)
    refs=[store.store([{'actual':n,'lower':0.,'upper':float('inf')}]) for n in range(count)]
    store.commit()
    state=store.to_state()
    store.close()
    return state,refs


@pytest.mark.parametrize('damage',['gzip_header','canonical_rows','extra_blob','missing_blob','extra_index','missing_index',
    'missing_blob_directory','missing_index_directory'])
def test_restore_rejects_changed_missing_or_orphan_evidence_without_repair(tmp_path,damage):
    state,refs=prepared(tmp_path)
    directory=Path(state['directory'])
    path=directory/refs[0]['file']
    if damage=='gzip_header':
        data=bytearray(path.read_bytes());data[9]^=1;path.write_bytes(data)
    elif damage=='canonical_rows':path.write_bytes(gzip.compress(b'[{"different":1}]',mtime=0))
    elif damage=='extra_blob':(directory/('0'*64+'.json.gz')).write_bytes(gzip.compress(b'[]',mtime=0))
    elif damage=='missing_blob':path.unlink()
    elif damage=='extra_index':(Path(state['index_directory'])/'orphan.txt').write_text('retained',encoding='utf8')
    elif damage=='missing_index':Path(state['proof_history_state']['path']).unlink()
    elif damage=='missing_blob_directory':directory.rename(tmp_path/'preserved_original_blobs')
    else:Path(state['index_directory']).rename(tmp_path/'preserved_original_indexes')
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises((RuntimeError,ValueError)):
        implementation().from_state(state,identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)
    assert {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}==before


def test_source_binding_and_exact_identity_or_path_drift_reject(tmp_path):
    api=implementation()
    with pytest.raises(ValueError):
        api.create(tmp_path/'bad/domain_evidence',identity=IDENTITY,index_directory=tmp_path/'bad/indexes',
            cache_limit=1,native_source_binding=dict(binding(),sha256='0'*64))
    state,_=prepared(tmp_path/'valid')
    for arguments in ({'identity':dict(IDENTITY,case_id='different')},
        {'native_source_binding':dict(binding(),sha256='0'*64)},
        {'directory':tmp_path/'elsewhere/domain_evidence'}, {'index_directory':tmp_path/'elsewhere/indexes'}):
        params=dict(identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)
        params.update(arguments)
        with pytest.raises(ValueError):api.from_state(state,**params)


def test_readonly_restore_uses_public_neutral_resolver_and_never_opens_a_writer(tmp_path,monkeypatch):
    state,_=prepared(tmp_path)
    api=implementation()
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    connect=sqlite3.connect
    calls=[]
    def readonly(database,*args,**kwargs):
        assert database.endswith('?mode=ro&immutable=1') and kwargs.get('uri') is True
        calls.append(database)
        return connect(database,*args,**kwargs)
    public=failure_history.resolve_history_reference
    usages=[]
    def observe(reference,expected_identity):
        usages.append(reference)
        return public(reference,expected_identity)
    monkeypatch.setattr(sqlite3,'connect',readonly)
    monkeypatch.setattr(failure_history,'resolve_history_reference',observe)
    restored=api.from_state(state,identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)
    assert restored.verify_blobs(full=False)['canonical_verified_blob_count'] is None
    with pytest.raises(TypeError,match='read.only'):restored.store([{'new':4.}])
    with pytest.raises(TypeError,match='read.only'):restored.commit()
    restored.close()
    assert calls and usages
    assert {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}==before


def test_pending_close_retains_raw_orphans_and_complete_pending_proof_state(tmp_path):
    store=create(tmp_path)
    initial=store.to_state()
    ref=store.store([{'actual':1.}])
    closed=store.close()
    assert closed['pending_blob_evidence_retained'] is True
    assert closed['uncommitted_proof_state']['global_count']==1
    assert (Path(initial['directory'])/ref['file']).is_file()
    with pytest.raises((RuntimeError,ValueError)):
        implementation().from_state(initial,identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)


def test_failure_between_commits_retains_the_proof_commit_and_rejects_old_snapshot(tmp_path,monkeypatch):
    store=create(tmp_path)
    initial=store.to_state()
    ref=store.store([{'actual':1.}])
    def reject():raise OSError('injected failure between proof and catalogue commits')
    monkeypatch.setattr(store._known,'commit',reject)
    with pytest.raises(OSError,match='between'):store.commit()
    with pytest.raises(RuntimeError,match='failed|ambiguous'):store.to_state()
    closed=store.close()
    assert closed['failed_stage']['stage']=='catalogue_commit'
    assert (Path(initial['directory'])/ref['file']).is_file()
    with sqlite3.connect(initial['proof_history_state']['path']) as db:
        assert db.execute('SELECT prefix_count FROM header').fetchone()==(1,)
    with sqlite3.connect(initial['catalogue_state']['path']) as db:
        assert db.execute('SELECT count FROM header').fetchone()==(0,)
    with pytest.raises((ValueError,RuntimeError)):
        implementation().from_state(initial,identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)


def test_changed_new_blob_before_commit_is_not_acknowledged(tmp_path):
    store=create(tmp_path)
    ref=store.store([{'actual':1.}])
    path=store.directory/ref['file']
    data=bytearray(path.read_bytes());data[9]^=1;path.write_bytes(data)
    with pytest.raises(ValueError):store.commit()
    with pytest.raises(RuntimeError):store.to_state()
    assert path.read_bytes()==bytes(data)
    store.close()


def test_consistent_but_wrong_order_proof_database_cannot_match_catalogue(tmp_path):
    state,refs=prepared(tmp_path)
    api=implementation()
    reader=failure_history.resolve_history_reference(api.proof_readback_reference(state['proof_history_state']),
        state['proof_history_state']['identity'])
    try:proofs=[copy.deepcopy(row) for row in reader]
    finally:reader.close()
    path=Path(state['proof_history_state']['path'])
    path.rename(tmp_path/'preserved_original_proofs.sqlite')
    history=DiskHistory(path,identity=state['proof_history_state']['identity'])
    for index,proof in enumerate(reversed(proofs)):
        proof['sequence']=index
        history.append(proof)
    history.commit(keep_tail=0)
    state['proof_history_state']=history.to_state()
    history.close()
    state['content_sha256']=checksum({key:value for key,value in state.items() if key!='content_sha256'})
    with pytest.raises(ValueError,match='pair|order|correspondence'):
        api.from_state(state,identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)


@pytest.mark.parametrize('target',['blob','catalogue_index','proof_index'])
def test_symlinked_blob_or_index_spelling_is_rejected_without_writes(tmp_path,monkeypatch,target):
    state,refs=prepared(tmp_path)
    path=(Path(state['directory'])/refs[0]['file'] if target=='blob' else
          Path(state['catalogue_state' if target=='catalogue_index' else 'proof_history_state']['path']))
    original=Path.is_symlink
    monkeypatch.setattr(Path,'is_symlink',lambda p:True if p==path else original(p))
    with pytest.raises(ValueError):
        implementation().from_state(state,identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)


def test_independent_new_blob_verifier_rejects_valid_gzip_with_wrong_canonical_rows_before_enqueue(tmp_path,monkeypatch):
    store=create(tmp_path)
    original=store._verify_file
    corrupted=[]
    def corrupt(ref,**kwargs):
        path=store.directory/ref['file']
        before=path.read_bytes()
        raw=gzip.decompress(before)
        changed=gzip.compress(raw.replace(b'1.0',b'2.0'),mtime=0)
        assert len(changed)==len(before) and gzip.decompress(changed)!=raw
        path.write_bytes(changed)
        corrupted.append(path)
        return original(ref,**kwargs)
    monkeypatch.setattr(store,'_verify_file',corrupt)
    with pytest.raises(ValueError,match='canonical'):store.store([{'actual':1.}])
    assert store.adapter_receipt()['proof_count']==0
    assert store.adapter_receipt()['failed_stage']['stage']=='new_blob_verification'
    closed=store.close()
    assert closed['pending_blob_evidence_retained'] is True and corrupted[0].is_file()


def test_larger_acknowledgement_batch_has_compact_snapshot_and_one_decoded_blob(tmp_path,monkeypatch):
    store=create(tmp_path)
    for n in range(50):store.store([{'actual':n,'lower':float('-inf'),'upper':float('inf')}])
    pending=store.adapter_receipt()
    assert pending['pending_proof_rows']==50 and pending['maximum_pending_proof_rows']==50
    assert pending['maximum_pending_proof_canonical_bytes']==pending['pending_proof_canonical_bytes']>0
    assert store._known.cache_info()['decoded_ref_count']<=2
    store.commit()
    state=store.to_state()
    assert len(json.dumps(state))<12000
    original=gzip.open
    live=maximum=0
    class BlobReader:
        def __init__(self,*args,**kwargs):self.stream=original(*args,**kwargs)
        def __enter__(self):
            nonlocal live,maximum
            live+=1;maximum=max(maximum,live)
            return self.stream.__enter__()
        def __exit__(self,*args):
            nonlocal live
            try:return self.stream.__exit__(*args)
            finally:live-=1
    monkeypatch.setattr(gzip,'open',BlobReader)
    verified=store.verify_blobs()
    assert verified['verified_blob_count']==50 and maximum==1 and live==0
    assert store._known.cache_info()['decoded_ref_count']<=2
    assert store.adapter_receipt()['pending_proof_rows']==0
    store.close()


def test_exception_after_real_catalogue_commit_still_blocks_ack_and_old_restore(tmp_path,monkeypatch):
    store=create(tmp_path)
    initial=store.to_state()
    store.store([{'actual':1.}])
    commit=store._known.commit
    def ambiguous():
        commit()
        raise OSError('injected exception after both real database commits')
    monkeypatch.setattr(store._known,'commit',ambiguous)
    with pytest.raises(OSError,match='after both'):store.commit()
    with pytest.raises(RuntimeError,match='failed|ambiguous'):store.to_state()
    closed=store.close()
    assert closed['committed_count']==0 and closed['failed_stage']['stage']=='catalogue_commit'
    with sqlite3.connect(initial['proof_history_state']['path']) as db:
        assert db.execute('SELECT prefix_count FROM header').fetchone()==(1,)
    with sqlite3.connect(initial['catalogue_state']['path']) as db:
        assert db.execute('SELECT count FROM header').fetchone()==(1,)
    with pytest.raises(ValueError):
        implementation().from_state(initial,identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)


def test_neutral_adapter_flags_do_not_conflate_boolean_with_integer_metadata(tmp_path):
    state,_=prepared(tmp_path)
    state['proof_readback_adapter']['native_failure_occurred']=0
    state['content_sha256']=checksum({key:value for key,value in state.items() if key!='content_sha256'})
    with pytest.raises(ValueError):
        implementation().from_state(state,identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)


@pytest.mark.parametrize('failure_point',['before_proof_persist','after_real_proof_sqlite_commit'])
def test_native_proof_commit_failure_retains_bounded_preimage_even_when_public_state_is_blocked(
        tmp_path,monkeypatch,failure_point):
    api=implementation()
    store=create(tmp_path)
    prior=[store.store([{'committed_prefix':n}]) for n in range(4)]
    store.commit()
    acknowledged=store.to_state()
    pending=[store.store([{'pending_batch':n}]) for n in range(2)]
    raw_hashes={ref['rows_sha256']:hashlib.sha256((store.directory/ref['file']).read_bytes()).hexdigest()
                for ref in pending}
    proof_history=store._proof_history
    connection=proof_history._connection
    with monkeypatch.context() as patch:
        def reject_full_prefix_read(self):
            raise AssertionError('Pre-commit capture must not iterate the entire proof history')
        patch.setattr(DiskHistory,'__iter__',reject_full_prefix_read)
        if failure_point=='before_proof_persist':
            class RejectCommit:
                def commit(self):raise OSError('injected native proof commit before persistence')
                def __getattr__(self,name):return getattr(connection,name)
            proof_history._connection=RejectCommit()
        else:
            def reject_fsync():raise OSError('injected native proof fsync after actual SQLite commit')
            patch.setattr(proof_history,'_fsync',reject_fsync)
        with pytest.raises(OSError,match='injected native proof'):store.commit()
        # This is the real native DiskHistory failure guard, not an outer mock:
        # both public ways of obtaining the live pending tail are unavailable.
        with pytest.raises(RuntimeError,match='failure'):proof_history.to_state()
        with pytest.raises(RuntimeError,match='failure'):_=proof_history.retained_count
        with pytest.raises(RuntimeError,match='failed|ambiguous'):store.to_state()
        closed=store.close()
    retained=closed.get('retained_pre_proof_commit')
    assert retained is not None, 'Native failed commit must not erase the only in-memory batch proof tail'
    assert retained['is_store_or_journal_acknowledgement'] is False
    assert retained['automatic_restoration_allowed'] is False
    assert retained['database_may_have_advanced'] is True
    assert retained['tail_scope']=='current_acknowledgement_batch'
    assert retained['prefix_rows_materialized'] is False
    assert retained['tail_rows']==2
    preimage=retained['proof_history_state']
    assert preimage['prefix_count']==4 and preimage['global_count']==6
    assert all(ref['rows_sha256'] not in json.dumps(preimage) for ref in prior)
    assert closed['uncommitted_proof_state'] is None
    assert closed['failed_stage']['stage']=='proof_commit'
    assert closed['pending_blob_evidence_retained'] is True
    for ref in pending:
        assert hashlib.sha256((Path(acknowledged['directory'])/ref['file']).read_bytes()).hexdigest()==raw_hashes[ref['rows_sha256']]
    with sqlite3.connect(acknowledged['catalogue_state']['path']) as db:
        assert db.execute('SELECT count FROM header').fetchone()==(4,)
    with sqlite3.connect(acknowledged['proof_history_state']['path']) as db:
        expected=4 if failure_point=='before_proof_persist' else 6
        assert db.execute('SELECT prefix_count FROM header').fetchone()==(expected,)
    reference=api.proof_readback_reference(preimage)
    if failure_point=='before_proof_persist':
        reader=failure_history.resolve_history_reference(reference,preimage['identity'])
        try:
            for index,ref in enumerate(pending,start=4):
                proof=reader[index]
                assert proof['ref']==ref and proof['key']==ref['rows_sha256']
                assert proof['gzip_sha256']==raw_hashes[ref['rows_sha256']]
        finally:reader.close()
    else:
        with pytest.raises(ValueError,match='newer|version|prefix'):
            failure_history.resolve_history_reference(reference,preimage['identity'])
    # The old store acknowledgement still cannot silently adopt raw orphans or
    # an advanced proof DB in either case.
    with pytest.raises(ValueError):
        api.from_state(acknowledged,identity=IDENTITY,native_source_binding=binding(),cache_limit=1,read_only=True)
