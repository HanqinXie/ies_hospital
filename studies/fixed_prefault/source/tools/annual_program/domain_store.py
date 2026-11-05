"""Annual-only logging adapter: native blobs plus two external append indexes.

Native DomainEvidenceStore.store runs unchanged and is bound to the caller's
reviewed original evidence.py path/SHA. No model, solver or numerical acceptance
rule is replaced. New blobs are independently verified and fsynced before proof
enqueue. Proof commit precedes catalogue commit; partial outcomes are retained
and make this adapter unusable, without automatic repair or committed rollback.
Returned references retain the reviewed native eight-key insertion order even
when the catalogue decodes its sorted canonical payload. Catalogue/proof hashing
and raw gzip bytes remain unchanged; unexpected return fields are rejected.

The catalogue decoded cache is bounded. The DiskHistory mutable proof tail is
bounded by the caller's acknowledgement batch, NOT by cache_limit; integration
must commit once per acknowledged block. Maximum pending rows/canonical JSON
bytes are exposed, without claiming those bytes measure Python memory or RSS.

Immediately before proof commit, a public DiskHistory.to_state preimage retains
only the committed-prefix descriptor and this acknowledgement batch's tail. On
failure it survives the native write-error guard and is returned by close(). It
is never an acknowledgement or permission to restore an advanced database; the
normal exact-version resolver must still reject any newer committed prefix.

Read-only proof recovery reuses the PUBLIC failure_history resolver format as a
neutral DiskHistory state compatibility wrapper. Its schema/migration label is
disclosed; no native failure happened and no error record is fabricated by it.
This adapter alone does not qualify a full annual runtime or journal admission.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
from itertools import zip_longest
import json
import math
import os
from pathlib import Path
import re

from tools.model_correctness_v23 import evidence as native
from .domain_catalogue import DomainCatalogue
from .disk_history import DiskHistory
from . import failure_history


SCHEMA='hospital_ies.annual_program.domain_store.v1'
PROOF_SCHEMA=SCHEMA+'.native_blob_proof.v1'
DESCRIPTOR_SCHEMA=SCHEMA+'.descriptor.v1'
_NATIVE_STORE=native.DomainEvidenceStore.store
_NATIVE_CODE=_NATIVE_STORE.__code__
_PIN_CACHE={}
_PIN_CACHE_LIMIT=8
_CATALOGUE_FILE='catalogue.sqlite'
_PROOF_FILE='blob_proofs.sqlite'
# Reviewed native DomainEvidenceStore.store's ref=dict(...) order. The caller's
# original-file path/SHA and unchanged method are checked before each store.
_NATIVE_REF_ORDER=('schema','directory','file','rows_sha256','row_count',
    'canonical_bytes','gzip_bytes','canonicalization')
_PROOF_FIELDS=frozenset(('schema','sequence','key','ref','gzip_sha256','gzip_bytes',
    'canonical_rows_sha256','row_count','canonical_bytes',
    'canonical_rows_verified_before_enqueue','raw_file_fsynced_before_enqueue'))
_STATE_FIELDS=frozenset(('schema','identity','directory','index_directory','native_source_binding',
    'enable_all_validation_rows','catalogue_state','proof_history_state','committed_count',
    'maximum_pending_proof_rows','maximum_pending_proof_canonical_bytes','proof_readback_adapter','content_sha256'))


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode('utf8')


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _native_ref_order(ref):
    if type(ref) is not dict or set(ref)!=set(_NATIVE_REF_ORDER):
        raise ValueError('Unexpected native reference field set')
    return {key:ref[key] for key in _NATIVE_REF_ORDER}


def _sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):result.update(chunk)
    return result.hexdigest()


def _hex(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def _guard(path,*,production=True):
    original=Path(path)
    if original.is_symlink():raise ValueError('Supplied domain-store path is a symlink')
    resolved=original.resolve()
    if original.absolute()!=resolved:raise ValueError('Domain-store parent path resolves through a substituted link')
    if production and os.name=='nt' and resolved.drive.upper()!='E:':
        raise ValueError('Domain-store data/index paths must remain on E')
    return resolved


def _identity(value):
    if (type(value) is not dict or type(value.get('case_id')) is not str or not value['case_id']
            or not _hex(value.get('source_sha256'))):
        raise ValueError('Explicit case/source-bound domain-store identity required')
    def primitive(item):
        if item is None or type(item) in (str,bool,int):return
        if type(item) is float and math.isfinite(item):return
        if type(item) is dict and all(type(k) is str for k in item):
            for child in item.values():primitive(child)
        elif type(item) is list:
            for child in item:primitive(child)
        else:raise ValueError('Domain-store identity requires finite primitive JSON types')
    primitive(value)
    return json.loads(_json(value))


def _pin(binding,*,force_hash=False):
    if (type(binding) is not dict or set(binding)!={'path','sha256'}
            or type(binding.get('path')) is not str or not _hex(binding.get('sha256'))):
        raise ValueError('Caller-supplied reviewed original native source path/SHA binding required')
    if type(force_hash) is not bool:raise ValueError('Explicit full source-pin Boolean required')
    if native.DomainEvidenceStore.store is not _NATIVE_STORE or _NATIVE_STORE.__code__ is not _NATIVE_CODE:
        raise ValueError('Original native evidence method or code changed')
    code=_NATIVE_STORE.__code__
    source=Path(code.co_filename)
    supplied=Path(binding['path'])
    key=(binding['path'],binding['sha256'],_NATIVE_STORE,code)
    # Local integrity only: stat caching cannot authenticate hostile same-stamp
    # edits. Full hashes bracket durable commits and precede snapshots/restores.
    if not force_hash and source.is_absolute() and key in _PIN_CACHE:
        if supplied.is_symlink():raise ValueError('Original native evidence source became a symlink')
        if _stat(supplied)==_PIN_CACHE[key]:return dict(binding)
    _PIN_CACHE.pop(key,None)
    actual=source.resolve()
    if (not supplied.is_absolute() or supplied.is_symlink() or supplied.resolve()!=actual
            or str(actual)!=binding['path'] or native.DomainEvidenceStore.store is not _NATIVE_STORE
            ):
        raise ValueError('Original native evidence source path, code or SHA binding changed')
    before=_stat(actual)
    if _sha(actual)!=binding['sha256'] or _stat(actual)!=before:
        raise ValueError('Original native evidence source SHA changed during verification')
    if source.is_absolute():
        if len(_PIN_CACHE)>=_PIN_CACHE_LIMIT:_PIN_CACHE.pop(next(iter(_PIN_CACHE)))
        _PIN_CACHE[key]=before
    return dict(binding)


def _adapter():
    return dict(name='public_failure_history_readonly_DiskHistory_state_compatibility.v1',
        schema=failure_history.SCHEMA,migration_id=failure_history.LOG_MIGRATION_ID,
        usage='neutral_proof_state_readback_only',native_failure_occurred=False,error_record_fabricated=False)


def proof_readback_reference(history_state):
    """Public-resolver compatibility only; this is not native failure provenance."""
    value=dict(schema=failure_history.SCHEMA,log_migration_id=failure_history.LOG_MIGRATION_ID,
        native_source_sha256=failure_history.NATIVE_SOURCE_SHA256,
        disk_history_source_sha256=failure_history.DISK_HISTORY_SOURCE_SHA256,
        history_state=copy.deepcopy(history_state),reference_is_commit_acknowledgement=False,
        reference_is_independent_archive=False)
    return dict(value,content_sha256=_digest(value))


def _derived(identity,stream):
    return dict(identity,domain_store_identity_sha256=_digest(identity),stream=stream)


def _stat(path):
    value=path.stat()
    return value.st_size,value.st_mtime_ns,value.st_ino


class DomainStore(native.DomainEvidenceStore):
    """Native ref/byte semantics with explicit externally indexed blob proofs."""
    @classmethod
    def create(cls,directory,*,identity,index_directory,native_source_binding,cache_limit=64,
               enable_all_validation_rows=True):
        binding=_pin(native_source_binding,force_hash=True)
        identity=_identity(identity)
        if type(enable_all_validation_rows) is not bool:raise ValueError('Explicit all-row logging Boolean required')
        directory,index=_guard(directory),_guard(index_directory)
        cls._paths(directory,index)
        for path in (directory,index):
            if path.exists():
                if not path.is_dir():raise ValueError('Domain-store directories must be regular directories')
                with os.scandir(path) as entries:
                    if next(entries,None) is not None:raise ValueError('Domain-store creation requires fresh empty directories')
            path.mkdir(parents=True,exist_ok=True)
        result=object.__new__(cls)
        native.DomainEvidenceStore.__init__(result,directory,enable_all_validation_rows=enable_all_validation_rows)
        result._initialize(identity,index,binding,False,0,0,0)
        result._known=None
        result._proof_history=None
        try:
            result._known=DomainCatalogue(index/_CATALOGUE_FILE,
                identity=_derived(identity,'domain_catalogue'),cache_limit=cache_limit)
            result._proof_history=DiskHistory(index/_PROOF_FILE,identity=_derived(identity,'domain_blob_proofs'))
        except Exception:
            if result._known is not None:result._known.close()
            if result._proof_history is not None:result._proof_history.close()
            result._closed=True
            raise
        return result

    @staticmethod
    def _paths(directory,index):
        if directory.name!='domain_evidence':raise ValueError('The native approved blob-directory name must be domain_evidence')
        if directory==index or index.is_relative_to(directory) or directory.is_relative_to(index):
            raise ValueError('Dedicated index directory must be separate from the blob directory')

    def _initialize(self,identity,index,binding,read_only,count,max_rows,max_bytes):
        self.identity=identity
        self.index_directory=index
        self.native_source_binding=binding
        self._read_only=read_only
        self._committed_count=count
        self._paired_count=count
        self._max_pending_rows=max_rows
        self._max_pending_bytes=max_bytes
        self._pending_bytes=0
        self._failed=None
        self._pre_proof_commit=None
        self._pre_proof_commit_bytes=0
        self._closed=False
        self._last_close=None

    def _check(self,*,write=False):
        if self._closed:raise RuntimeError('Domain store is closed')
        if self._failed is not None:raise RuntimeError('Domain store is failed/ambiguous; no snapshot or further writes are allowed')
        if write and self._read_only:raise TypeError('Domain store view is read-only')
        _pin(self.native_source_binding)

    def _fail(self,stage,error):
        if stage=='proof_commit':
            try:
                if self._proof_history.retained_count==0:self._pending_bytes=0
            except (RuntimeError,ValueError,OSError):self._pending_bytes=None
        self._failed=dict(stage=stage,type=type(error).__name__,message=str(error),
            automatic_repair_performed=False,committed_evidence_erased=False)

    def _indexes(self):
        index=_guard(self.index_directory)
        if not index.is_dir():raise ValueError('Missing domain-store index directory')
        expected=(_CATALOGUE_FILE,_PROOF_FILE)
        count=0
        with os.scandir(index) as entries:
            for entry in entries:
                if (entry.name not in expected or entry.is_symlink() or Path(entry.path).is_symlink()
                        or not entry.is_file(follow_symlinks=False)):
                    raise ValueError('Extra, missing or symlinked domain-store index evidence')
                count+=1
        if count!=2:raise ValueError('Missing domain-store index database')

    def _verify_file(self,ref,*,proof=None,full=True,durable=False):
        directory=_guard(self.directory)
        key=ref.get('rows_sha256')
        if not _hex(key) or ref.get('directory')!='domain_evidence' or ref.get('file')!=key+'.json.gz':
            raise ValueError('Unapproved native blob reference path')
        path=directory/ref['file']
        if path.is_symlink() or not path.is_file():raise ValueError('Missing or symlinked native domain blob')
        before=_stat(path)
        raw_sha=_sha(path)
        if before[0]!=ref.get('gzip_bytes'):raise ValueError('Native gzip byte size differs from reference')
        if proof is not None and (raw_sha!=proof['gzip_sha256'] or before[0]!=proof['gzip_bytes']):
            raise ValueError('Native raw gzip SHA/size changed after proof')
        if full:
            def nonfinite(value):raise ValueError('Nonfinite JSON token in canonical native blob: '+value)
            with gzip.open(path,'rt',encoding='utf8') as stream:rows=json.load(stream,parse_constant=nonfinite)
            if type(rows) is not list or len(rows)!=ref.get('row_count'):
                raise ValueError('Native canonical row count differs from reference')
            digest=hashlib.sha256();size=0
            for chunk in native._row_chunks(rows):digest.update(chunk);size+=len(chunk)
            del rows
            if digest.hexdigest()!=key or size!=ref.get('canonical_bytes'):
                raise ValueError('Native decoded canonical row SHA/size differs from reference')
        if durable:
            with path.open('r+b') as stream:os.fsync(stream.fileno())
        if _stat(path)!=before:raise ValueError('Native blob changed during verification/fsync')
        return dict(gzip_sha256=raw_sha,gzip_bytes=before[0],canonical_rows_sha256=key,
            row_count=ref['row_count'],canonical_bytes=ref['canonical_bytes'],
            canonical_rows_verified_before_enqueue=full,raw_file_fsynced_before_enqueue=durable)

    def store(self,rows):
        self._check(write=True)
        stage='native_store'
        try:
            before=len(self._known)
            if len(self._proof_history)!=before or before!=self._paired_count:
                raise ValueError('Catalogue/proof visible counts differ before native store')
            ref=_native_ref_order(_NATIVE_STORE(self,rows))
            after=len(self._known)
            if after==before:return ref
            if after!=before+1:raise ValueError('Native insertion did not append exactly one domain reference')
            stage='new_blob_verification'
            verified=self._verify_file(ref,full=True,durable=True)
            stage='proof_enqueue'
            proof=dict(schema=PROOF_SCHEMA,sequence=before,key=ref['rows_sha256'],ref=copy.deepcopy(ref),**verified)
            self._proof_history.append(proof)
            self._paired_count=after
            self._pending_bytes+=len(_json(proof))
            self._max_pending_rows=max(self._max_pending_rows,self._proof_history.retained_count)
            self._max_pending_bytes=max(self._max_pending_bytes,self._pending_bytes)
            return ref
        except Exception as exc:
            self._fail(stage,exc)
            raise

    def _pair(self,sequence,key,ref,proof):
        if (not isinstance(proof,dict) or set(proof)!=_PROOF_FIELDS or proof.get('schema')!=PROOF_SCHEMA
                or type(proof.get('sequence')) is not int or proof['sequence']!=sequence
                or proof.get('key')!=key or _json(proof.get('ref'))!=_json(ref)
                or not _hex(proof.get('gzip_sha256')) or proof.get('canonical_rows_sha256')!=key
                or any(type(proof.get(field)) is not int or proof[field]!=ref[field]
                    for field in ('row_count','canonical_bytes','gzip_bytes'))
                or proof.get('canonical_rows_verified_before_enqueue') is not True
                or proof.get('raw_file_fsynced_before_enqueue') is not True):
            raise ValueError('Catalogue/proof pair order or exact reference correspondence differs')

    def verify_blobs(self,*,full=True):
        self._check()
        if type(full) is not bool:raise ValueError('Explicit decoded verification Boolean required')
        count=0
        for pair,proof in zip_longest(self._known.items(),iter(self._proof_history)):
            if pair is None or proof is None:raise ValueError('Catalogue/proof pair counts differ')
            key,ref=pair
            self._pair(count,key,ref,proof)
            self._verify_file(ref,proof=proof,full=full,durable=False)
            count+=1
        files=0
        with os.scandir(_guard(self.directory)) as entries:
            for entry in entries:
                if (entry.is_symlink() or Path(entry.path).is_symlink() or not entry.is_file(follow_symlinks=False)
                        or re.fullmatch('[0-9a-f]{64}\\.json\\.gz',entry.name) is None
                        or entry.name[:-8] not in self._known):
                    raise ValueError('Extra, orphan or symlinked native domain blob')
                files+=1
        if files!=count or count!=len(self._known):raise ValueError('Missing or mismatched native blob/proof inventory')
        return dict(schema=SCHEMA+'.verification.v1',verified_blob_count=count,
            raw_gzip_verified_blob_count=count,canonical_verified_blob_count=count if full else None,
            inventory_verified=True,maximum_decoded_blobs=1,full_raw_inventory_materialized=False,
            cryptographic_producer_authentication=False)

    def adapter_receipt(self):
        def safe(function):
            try:return function()
            except (RuntimeError,ValueError,OSError):return None
        return dict(schema=SCHEMA+'.adapter_receipt.v1',committed_count=self._committed_count,
            catalogue_count=safe(lambda:len(self._known)),proof_count=safe(lambda:len(self._proof_history)),
            pending_proof_rows=safe(lambda:self._proof_history.retained_count),
            pending_proof_canonical_bytes=self._pending_bytes,
            maximum_pending_proof_rows=self._max_pending_rows,
            maximum_pending_proof_canonical_bytes=self._max_pending_bytes,
            pending_size_measure='canonical proof JSON bytes, not Python memory or RSS',
            proof_readback_adapter=_adapter(),native_source_binding=dict(self.native_source_binding),
            failed_stage=copy.deepcopy(self._failed),
            retained_pre_proof_commit_tail_rows=(self._pre_proof_commit['tail_rows']
                if self._pre_proof_commit is not None else 0),
            retained_pre_proof_commit_canonical_bytes=self._pre_proof_commit_bytes,
            existing_committed_blobs_reverified_on_every_duplicate_store=False,
            native_source_pin=dict(mode='bounded_stat_cache_with_forced_durable_boundary_hashes.v1',
                maximum_cached_sources=_PIN_CACHE_LIMIT,stamp_fields=['size','mtime_ns','inode'],
                full_hash_before_and_after_commit=True,full_hash_before_snapshot_and_restore=True,
                method_and_code_identity_checked_each_use=True,
                hostile_same_stamp_edit_authenticated_between_boundaries=False),
            numerical_or_physical_validation_performed=False,annual_resource_admission_qualified=False,
            caller_must_commit_each_acknowledged_block=True)

    def commit(self):
        self._check(write=True)
        stage='precommit_pair_and_blob_verification'
        try:
            _pin(self.native_source_binding,force_hash=True)
            count=len(self._known)
            if count!=len(self._proof_history) or count!=self._paired_count:
                raise ValueError('Catalogue/proof counts are inconsistent before commit')
            for sequence in range(self._committed_count,count):
                proof=self._proof_history[sequence]
                key=proof.get('key')
                ref=self._known[key]
                self._pair(sequence,key,ref,proof)
                self._verify_file(ref,proof=proof,full=False,durable=False)
            if count==self._committed_count:
                _pin(self.native_source_binding,force_hash=True)
                return self.adapter_receipt()
            stage='pre_proof_commit_capture'
            preimage=self._proof_history.to_state()
            retained=dict(schema=SCHEMA+'.pre_proof_commit_retention.v1',
                proof_history_state=preimage,captured_before_native_proof_commit=True,
                is_store_or_journal_acknowledgement=False,automatic_restoration_allowed=False,
                database_may_have_advanced=True,current_database_version_verified_after_failure=False,
                tail_scope='current_acknowledgement_batch',
                tail_rows=preimage['global_count']-preimage['prefix_count'],prefix_rows_materialized=False)
            self._pre_proof_commit=dict(retained,content_sha256=_digest(retained))
            self._pre_proof_commit_bytes=len(_json(self._pre_proof_commit))
            stage='proof_commit'
            self._proof_history.commit(keep_tail=0)
            self._pending_bytes=0
            stage='catalogue_commit'
            self._known.commit()
            stage='cross_database_acknowledgement'
            if self._known.to_state()['count']!=count or self._proof_history.to_state()['prefix_count']!=count:
                raise ValueError('Committed cross-database acknowledgement counts differ')
            stage='postcommit_source_pin'
            _pin(self.native_source_binding,force_hash=True)
            self._committed_count=count
            self._pre_proof_commit=None
            self._pre_proof_commit_bytes=0
            return self.adapter_receipt()
        except Exception as exc:
            self._fail(stage,exc)
            raise

    def to_state(self):
        self._check()
        _pin(self.native_source_binding,force_hash=True)
        catalogue=self._known.to_state()
        proof=self._proof_history.to_state()
        if (catalogue['count']!=self._committed_count or proof['prefix_count']!=self._committed_count
                or proof['global_count']!=proof['prefix_count'] or self._proof_history.retained_count):
            raise ValueError('Domain-store snapshot requires equal quiescent committed ordered counts; pending proofs remain')
        self._indexes()
        state=dict(schema=DESCRIPTOR_SCHEMA,identity=copy.deepcopy(self.identity),directory=str(self.directory),
            index_directory=str(self.index_directory),native_source_binding=dict(self.native_source_binding),
            enable_all_validation_rows=self.enable_all_validation_rows,catalogue_state=catalogue,
            proof_history_state=proof,committed_count=self._committed_count,
            maximum_pending_proof_rows=self._max_pending_rows,
            maximum_pending_proof_canonical_bytes=self._max_pending_bytes,proof_readback_adapter=_adapter())
        return dict(state,content_sha256=_digest(state))

    @classmethod
    def from_state(cls,state,*,identity,native_source_binding,cache_limit=64,directory=None,
                   index_directory=None,read_only=False,full=True):
        binding=_pin(native_source_binding,force_hash=True)
        identity=_identity(identity)
        if type(read_only) is not bool or type(full) is not bool:raise ValueError('Explicit restore Booleans required')
        if (type(state) is not dict or set(state)!=_STATE_FIELDS or state.get('schema')!=DESCRIPTOR_SCHEMA
                or _json(state.get('identity'))!=_json(identity) or state.get('native_source_binding')!=binding
                or _json(state.get('proof_readback_adapter'))!=_json(_adapter())
                or state.get('content_sha256')!=_digest({k:v for k,v in state.items() if k!='content_sha256'})
                or type(state.get('enable_all_validation_rows')) is not bool
                or any(type(state.get(key)) is not int or state[key]<0 for key in
                    ('committed_count','maximum_pending_proof_rows','maximum_pending_proof_canonical_bytes'))):
            raise ValueError('Invalid domain-store descriptor identity/schema/source binding')
        blob,index=_guard(state['directory']),_guard(state['index_directory'])
        cls._paths(blob,index)
        if not blob.is_dir() or not index.is_dir():raise ValueError('Missing domain-store blob or index directory')
        if (str(blob)!=state['directory'] or str(index)!=state['index_directory']
                or (directory is not None and _guard(directory)!=blob)
                or (index_directory is not None and _guard(index_directory)!=index)):
            raise ValueError('Domain-store descriptor path relocation is forbidden')
        if (state['catalogue_state'].get('path')!=str(index/_CATALOGUE_FILE)
                or state['proof_history_state'].get('path')!=str(index/_PROOF_FILE)
                or state['proof_history_state'].get('global_count')!=state['committed_count']
                or state['proof_history_state'].get('prefix_count')!=state['committed_count']
                or state['catalogue_state'].get('count')!=state['committed_count']):
            raise ValueError('Domain-store index path/count descriptors are inconsistent')
        result=object.__new__(cls)
        result.directory=blob
        result.enable_all_validation_rows=state['enable_all_validation_rows']
        result._initialize(identity,index,binding,True,state['committed_count'],
            state['maximum_pending_proof_rows'],state['maximum_pending_proof_canonical_bytes'])
        result._known=None
        result._proof_history=None
        try:
            result._indexes()
            result._known=DomainCatalogue.from_state(state['catalogue_state'],identity=_derived(identity,'domain_catalogue'),
                cache_limit=cache_limit,read_only=True)
            result._proof_history=failure_history.resolve_history_reference(
                proof_readback_reference(state['proof_history_state']),_derived(identity,'domain_blob_proofs'))
            result.verify_blobs(full=full)
            if not read_only:
                result._known.close();result._proof_history.close()
                result._known=DomainCatalogue.from_state(state['catalogue_state'],identity=_derived(identity,'domain_catalogue'),
                    cache_limit=cache_limit,read_only=False)
                result._proof_history=DiskHistory.from_state(state['proof_history_state'],identity=_derived(identity,'domain_blob_proofs'))
                result._read_only=False
            return result
        except Exception:
            if result._known is not None:result._known.close()
            if result._proof_history is not None:result._proof_history.close()
            result._closed=True
            raise

    def close(self):
        if self._closed:return self._last_close
        receipt=self.adapter_receipt()
        receipt.update(closed=True,pending_blob_evidence_retained=True,raw_blobs_deleted=False,
            uncommitted_proof_state=None,retained_pre_proof_commit=self._pre_proof_commit,close_errors=[])
        try:
            if self._proof_history.retained_count:
                receipt['uncommitted_proof_state']=self._proof_history.to_state()
        except (RuntimeError,ValueError,OSError) as exc:
            receipt['close_errors'].append(dict(type=type(exc).__name__,message=str(exc),stage='proof_tail_capture'))
        for name,value in (('catalogue',self._known),('proof_history',self._proof_history)):
            try:value.close()
            except (RuntimeError,ValueError,OSError) as exc:
                receipt['close_errors'].append(dict(type=type(exc).__name__,message=str(exc),stage=name+'_close'))
        self._closed=True
        self._last_close=receipt
        return receipt


create=DomainStore.create
from_state=DomainStore.from_state
