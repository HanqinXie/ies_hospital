"""Append-only disk index for native domain-reference metadata only.

The native DomainEvidenceStore source is unchanged. The annual-only adapter
replaces its _known mapping with this index. Pending rows are visible to that
one writer but are not a snapshot or acknowledgement. Only explicit commit()
commits metadata. rollback()/close() never commit; close reports rolled-back rows.

No raw blob is read, fsynced, authenticated, deleted or qualified here. The caller
must separately prove every native blob before acknowledging a journal block.
Descriptors bind the exact committed database/version; later or orphan rows are
rejected, never truncated. Full restore verification streams one row at a time.
The producer must be quiescent and have one owner. This local integrity protocol
is not arbitrary-crash recovery or cryptographic producer authentication.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, MutableMapping
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3

from tools.model_correctness_v23.evidence import REFERENCE_SCHEMA, ALL_REFERENCE_SCHEMA


SCHEMA='hospital_ies.annual_program.domain_catalogue.sqlite.v1'
DESCRIPTOR_SCHEMA=SCHEMA+'.descriptor.v1'
MIGRATION_ID='annual_native_known_dictionary_to_exact_external_index.v1'
CANONICALIZATION='UTF8 sorted compact JSON; nonfinite floats -> null'
_REF_FIELDS=frozenset(('schema','directory','file','rows_sha256','row_count',
    'canonical_bytes','gzip_bytes','canonicalization'))
_STATE_FIELDS=frozenset(('schema','database_schema','migration_id','path','identity',
    'identity_sha256','version','count','head_digest','raw_blob_proof_included',
    'requires_external_blob_proof_before_journal_ack','content_sha256'))
_HEADER_SQL=('CREATE TABLE header (id INTEGER PRIMARY KEY CHECK(id=1), schema TEXT NOT NULL, '
    'identity BLOB NOT NULL, identity_sha TEXT NOT NULL, version INTEGER NOT NULL, '
    'count INTEGER NOT NULL, head_digest TEXT)')
_ENTRIES_SQL=('CREATE TABLE entries (seq INTEGER PRIMARY KEY, sha TEXT NOT NULL UNIQUE, '
    'payload BLOB NOT NULL, payload_sha TEXT NOT NULL, previous_digest TEXT, digest TEXT NOT NULL)')


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,
        separators=(',',':')).encode('utf8')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _key(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise ValueError('A native lowercase SHA256 catalogue key is required')
    return value


def _primitive(value,active=None):
    if value is None or type(value) in (str,bool,int):return
    if type(value) is float:
        if not math.isfinite(value):raise ValueError('Nonfinite catalogue identity value')
        return
    if type(value) not in (dict,list):raise ValueError('Catalogue identity requires finite primitive JSON types')
    active=set() if active is None else active
    if id(value) in active:raise ValueError('Cyclic catalogue identity')
    active.add(id(value))
    try:
        if type(value) is dict:
            if any(type(key) is not str for key in value):raise ValueError('Identity keys must be strings')
            values=value.values()
        else:values=value
        for item in values:_primitive(item,active)
    finally:active.remove(id(value))


def _identity(value):
    if (type(value) is not dict or type(value.get('case_id')) is not str or not value['case_id']
            or 'source_sha256' not in value):
        raise ValueError('Explicit case_id and source_sha256 catalogue identity required')
    _key(value['source_sha256'])
    _primitive(value)
    encoded=_json(value)
    return json.loads(encoded),encoded,_sha(encoded)


def _limit(value):
    if type(value) is not int or value<0:raise ValueError('cache_limit must be an explicit nonnegative integer')
    return value


def _reference(key,value):
    _key(key)
    if (type(value) is not dict or set(value)!=_REF_FIELDS
            or any(type(value.get(field)) is not str for field in
                ('schema','directory','file','rows_sha256','canonicalization'))
            or type(value.get('schema')) is not str or value['schema'] not in (REFERENCE_SCHEMA,ALL_REFERENCE_SCHEMA)
            or value.get('directory')!='domain_evidence' or value.get('file')!=key+'.json.gz'
            or value.get('rows_sha256')!=key or value.get('canonicalization')!=CANONICALIZATION
            or any(type(value.get(field)) is not int or value[field]<0
                for field in ('row_count','canonical_bytes','gzip_bytes'))):
        raise ValueError('Unapproved native domain reference schema, types or SHA binding')
    return _json(value)


def _entry_digest(sequence,key,previous,payload_sha,identity_sha):
    return _sha(_json(dict(sequence=sequence,sha=key,previous_digest=previous,
        payload_sha256=payload_sha,identity_sha256=identity_sha)))


def _path(value):
    try:
        supplied=Path(value)
        is_link=supplied.is_symlink()
    except (OSError,TypeError,ValueError) as exc:raise ValueError('Invalid catalogue path') from exc
    if is_link:
        raise ValueError('Catalogue supplied path cannot be a symlink, including a dangling link')
    try:path=supplied.resolve()
    except (OSError,TypeError,ValueError) as exc:raise ValueError('Invalid catalogue path') from exc
    if os.name=='nt' and path.drive.upper()!='E:':
        raise ValueError('Production catalogue databases must remain on E')
    return path


def _sidecars(path):
    if any(Path(str(path)+suffix).exists() for suffix in ('-journal','-wal','-shm')):
        raise ValueError('Ambiguous SQLite sidecar prevents exact catalogue restoration')


def _stamp(path):
    stat=path.stat()
    return stat.st_size,stat.st_mtime_ns,stat.st_ino


def _schema(connection):
    expected={'header':_HEADER_SQL,'entries':_ENTRIES_SQL}
    seen=set()
    for kind,name,table,sql in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"):
        if kind!='table' or name!=table or name not in expected or sql!=expected[name] or name in seen:
            raise ValueError('Unrecognized or extra catalogue database schema object')
        seen.add(name)
    if seen!=set(expected):raise ValueError('Missing catalogue database schema objects')


def _header(connection,identity_bytes,identity_sha):
    cursor=connection.execute('SELECT id,schema,identity,identity_sha,version,count,head_digest FROM header')
    row=cursor.fetchone()
    if row is None or cursor.fetchone() is not None:raise ValueError('Catalogue header is missing or nonunique')
    number,schema,raw,sha,version,count,head=row
    if (number!=1 or schema!=SCHEMA or type(raw) is not bytes or raw!=identity_bytes or sha!=identity_sha
            or type(version) is not int or type(count) is not int or version<0 or count<0
            or (count==0 and (version!=0 or head is not None))
            or (count>0 and (not 1<=version<=count or type(head) is not str))):
        raise ValueError('Catalogue header identity, schema or counters differ')
    if count:_key(head)
    return version,count,head


def _decode_row(row,identity_sha,cache=None,limit=0):
    sequence,key,payload,payload_sha,previous,digest=row
    if type(sequence) is not int or sequence<0 or type(payload) is not bytes:
        raise ValueError('Invalid catalogue sequence or typed payload')
    _key(key);_key(payload_sha);_key(digest)
    if previous is not None:_key(previous)
    if (payload_sha!=_sha(payload)
            or digest!=_entry_digest(sequence,key,previous,payload_sha,identity_sha)):
        raise ValueError('Catalogue row checksum or digest corruption')
    cached=cache.get(key) if cache is not None else None
    if cached is not None and cached[0]==payload_sha:
        cache.move_to_end(key)
        return dict(cached[1])
    try:value=json.loads(payload)
    except (UnicodeError,ValueError,TypeError) as exc:raise ValueError('Invalid canonical reference JSON') from exc
    if _reference(key,value)!=payload:raise ValueError('Noncanonical typed catalogue reference')
    if cache is not None and limit:
        cache[key]=(payload_sha,value)
        cache.move_to_end(key)
        while len(cache)>limit:cache.popitem(last=False)
    return dict(value)


def _verify(connection,identity_bytes,identity_sha,expected):
    _schema(connection)
    cursor=connection.execute('PRAGMA quick_check')
    if cursor.fetchone()!=('ok',) or cursor.fetchone() is not None:
        raise ValueError('Catalogue SQLite integrity check failed')
    if _header(connection,identity_bytes,identity_sha)!=expected:
        raise ValueError('Catalogue has a different, newer or ambiguous committed version')
    count,previous=0,None
    for row in connection.execute('SELECT seq,sha,payload,payload_sha,previous_digest,digest FROM entries ORDER BY seq'):
        if count>=expected[1] or row[0]!=count or row[4]!=previous:
            raise ValueError('Catalogue contains missing, extra or orphan prefix rows')
        _decode_row(row,identity_sha)
        previous=row[5]
        count+=1
    if count!=expected[1] or previous!=expected[2]:
        raise ValueError('Catalogue prefix count or head differs from descriptor')


class DomainCatalogue(MutableMapping):
    """Insertion-ordered native reference mapping with an explicitly bounded LRU."""
    def _bind(self,path,identity,identity_bytes,identity_sha,cache_limit,connection,committed,read_only):
        self._path=path
        self.identity=identity
        self._identity_bytes,self._identity_sha=identity_bytes,identity_sha
        self._cache_limit=cache_limit
        self._cache=OrderedDict()
        self._connection=connection
        self._committed=committed
        self._pending_count=0
        self._pending_head=committed[2]
        self._read_only=read_only
        self._closed=False
        self._write_error=None
        self._epoch=0
        self.last_close_receipt=None

    @property
    def path(self):
        return self._path

    @property
    def cache_limit(self):
        return self._cache_limit

    def __init__(self,path,*,identity,cache_limit):
        cache_limit=_limit(cache_limit)
        identity,raw,sha=_identity(identity)
        path=_path(path)
        if Path(path).is_symlink():raise ValueError('Catalogue path cannot be a substituted link')
        _sidecars(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb'):pass
        connection=None
        try:
            connection=sqlite3.connect(str(path),isolation_level=None)
            self._bind(path,identity,raw,sha,cache_limit,connection,(0,0,None),False)
            self._configure()
            connection.execute('BEGIN IMMEDIATE')
            connection.execute(_HEADER_SQL)
            connection.execute(_ENTRIES_SQL)
            connection.execute('INSERT INTO header VALUES (1,?,?,?,?,?,NULL)',(SCHEMA,raw,sha,0,0))
            connection.commit()
            self._fsync()
        except Exception:
            if connection is not None:connection.close()
            self._closed=True
            raise

    def _configure(self):
        self._connection.execute('PRAGMA journal_mode=DELETE')
        self._connection.execute('PRAGMA synchronous=FULL')
        self._connection.execute('PRAGMA busy_timeout=3000')

    def _fsync(self):
        with self.path.open('r+b') as stream:os.fsync(stream.fileno())

    def _check(self,*,write=False):
        if self._closed:raise RuntimeError('Domain catalogue is closed')
        if self._write_error is not None:
            raise RuntimeError('Domain catalogue has an unacknowledged write failure; explicit rollback required') from self._write_error
        if write and self._read_only:raise TypeError('Domain catalogue view is read-only')
        if _header(self._connection,self._identity_bytes,self._identity_sha)!=self._committed:
            raise ValueError('Catalogue committed version changed outside this owner')

    def __len__(self):
        self._check()
        return self._committed[1]+self._pending_count

    def __contains__(self,key):
        self._check();_key(key)
        return self._connection.execute('SELECT 1 FROM entries WHERE sha=?',(key,)).fetchone() is not None

    def __getitem__(self,key):
        self._check();_key(key)
        row=self._connection.execute('SELECT seq,sha,payload,payload_sha,previous_digest,digest FROM entries WHERE sha=?',(key,)).fetchone()
        if row is None:raise KeyError(key)
        return _decode_row(row,self._identity_sha,self._cache,self.cache_limit)

    def entry_position(self,key):
        """Read one owner-visible insertion position and detached native ref.

        This includes this connection's pending rows. It neither scans keys nor
        commits, changes the schema or qualifies any external blob bytes.
        """
        self._check();_key(key)
        row=self._connection.execute('SELECT seq,sha,payload,payload_sha,previous_digest,digest FROM entries WHERE sha=?',(key,)).fetchone()
        if row is None:raise KeyError(key)
        reference=_decode_row(row,self._identity_sha,self._cache,self.cache_limit)
        return dict(sequence=row[0],key=row[1],reference=reference)

    def __setitem__(self,key,value):
        self._check(write=True)
        payload=_reference(key,value)
        existing=self._connection.execute('SELECT seq,sha,payload,payload_sha,previous_digest,digest FROM entries WHERE sha=?',(key,)).fetchone()
        if existing is not None:
            _decode_row(existing,self._identity_sha,self._cache,self.cache_limit)
            if existing[2]!=payload:raise ValueError('Conflicting catalogue overwrite is forbidden')
            return
        sequence=self._committed[1]+self._pending_count
        payload_sha=_sha(payload)
        digest=_entry_digest(sequence,key,self._pending_head,payload_sha,self._identity_sha)
        row=(sequence,key,payload,payload_sha,self._pending_head,digest)
        try:
            if not self._connection.in_transaction:
                self._connection.execute('BEGIN IMMEDIATE')
                self._check(write=True)
            self._connection.execute('INSERT INTO entries VALUES (?,?,?,?,?,?)',row)
            self._pending_count+=1
            self._pending_head=digest
            self._epoch+=1
            _decode_row(row,self._identity_sha,self._cache,self.cache_limit)
        except Exception as exc:
            self._write_error=exc
            raise

    def __delitem__(self,key):
        raise TypeError('Domain catalogue is append-only; committed entries cannot be deleted')

    def __iter__(self):
        self._check()
        epoch,count=self._epoch,len(self)
        observed=0
        for sequence,key in self._connection.execute('SELECT seq,sha FROM entries ORDER BY seq'):
            if epoch!=self._epoch:raise RuntimeError('Catalogue changed during streaming iteration')
            if sequence!=observed:raise ValueError('Catalogue insertion sequence gap')
            _key(key)
            yield key
            observed+=1
        if epoch!=self._epoch or observed!=count:raise ValueError('Catalogue changed or contains extra prefix rows')

    def items(self):
        self._check()
        epoch,count=self._epoch,len(self)
        observed=0
        for row in self._connection.execute('SELECT seq,sha,payload,payload_sha,previous_digest,digest FROM entries ORDER BY seq'):
            if epoch!=self._epoch:raise RuntimeError('Catalogue changed during streaming iteration')
            if row[0]!=observed:raise ValueError('Catalogue insertion sequence gap')
            yield row[1],_decode_row(row,self._identity_sha,self._cache,self.cache_limit)
            observed+=1
        if epoch!=self._epoch or observed!=count:raise ValueError('Catalogue changed or contains extra prefix rows')

    def values(self):
        for _,value in self.items():yield value

    def __eq__(self,other):
        # Mapping.__eq__ otherwise constructs two complete dictionaries.
        if self is other:return True
        if not isinstance(other,Mapping):return NotImplemented
        if len(self)!=len(other):return False
        for key,value in self.items():
            try:
                if _reference(key,value)!=_reference(key,other[key]):return False
            except (KeyError,ValueError,TypeError):return False
        return True

    def __copy__(self):
        raise TypeError('Do not copy the whole catalogue; use its compact snapshot descriptor')

    def __deepcopy__(self,memo):
        raise TypeError('Do not deepcopy the whole catalogue; use its compact snapshot descriptor')

    def cache_info(self):
        self._check()
        return dict(cache_limit=self.cache_limit,decoded_ref_count=len(self._cache),pending_additions=self._pending_count)

    def receipt(self):
        self._check()
        return dict(schema=SCHEMA+'.status.v1',identity_sha256=self._identity_sha,
            version=self._committed[0],count=len(self),committed_count=self._committed[1],
            head_digest=self._pending_head,pending_additions=self._pending_count,
            raw_blob_durability_verified=False,raw_blob_authentication_verified=False,
            requires_external_blob_proof_before_journal_ack=True)

    def commit(self):
        self._check(write=True)
        if not self._pending_count:return self.receipt()
        target=(self._committed[0]+1,self._committed[1]+self._pending_count,self._pending_head)
        try:
            if not self._connection.in_transaction:raise RuntimeError('Unacknowledged pending catalogue transaction disappeared')
            self._connection.execute('UPDATE header SET version=?,count=?,head_digest=? WHERE id=1',target)
            self._connection.commit()
            self._fsync()
            if _header(self._connection,self._identity_bytes,self._identity_sha)!=target:
                raise ValueError('Persisted catalogue commit acknowledgement differs')
        except Exception as exc:
            self._write_error=exc
            raise
        self._committed=target
        self._pending_count=0
        self._pending_head=target[2]
        self._epoch+=1
        return self.receipt()

    def rollback(self):
        if self._closed:raise RuntimeError('Domain catalogue is closed')
        pending=self._pending_count
        if self._connection.in_transaction:self._connection.rollback()
        if _header(self._connection,self._identity_bytes,self._identity_sha)!=self._committed:
            raise ValueError('Catalogue commit outcome is ambiguous/newer; rollback cannot erase committed evidence')
        self._pending_count=0
        self._pending_head=self._committed[2]
        self._cache.clear()
        self._write_error=None
        self._epoch+=1
        return dict(schema=SCHEMA+'.rollback.v1',rolled_back_additions=pending,
            count=self._committed[1],version=self._committed[0],raw_blobs_touched=False)

    def to_state(self):
        self._check()
        if self._pending_count or self._connection.in_transaction:
            raise ValueError('Catalogue snapshot requires quiescent committed state, without pending additions')
        version,count,head=self._committed
        state=dict(schema=DESCRIPTOR_SCHEMA,database_schema=SCHEMA,migration_id=MIGRATION_ID,
            path=str(self.path),identity=json.loads(self._identity_bytes),identity_sha256=self._identity_sha,
            version=version,count=count,head_digest=head,raw_blob_proof_included=False,
            requires_external_blob_proof_before_journal_ack=True)
        return dict(state,content_sha256=_sha(_json(state)))

    snapshot=to_state

    @classmethod
    def from_state(cls,state,*,identity,cache_limit,path=None,read_only=False):
        cache_limit=_limit(cache_limit)
        if type(read_only) is not bool:raise ValueError('Explicit Boolean read_only required')
        wanted,raw,sha=_identity(identity)
        if (type(state) is not dict or set(state)!=_STATE_FIELDS
                or state.get('schema')!=DESCRIPTOR_SCHEMA or state.get('database_schema')!=SCHEMA
                or state.get('migration_id')!=MIGRATION_ID or _json(state.get('identity'))!=raw
                or state.get('identity_sha256')!=sha or state.get('raw_blob_proof_included') is not False
                or state.get('requires_external_blob_proof_before_journal_ack') is not True
                or state.get('content_sha256')!=_sha(_json({k:v for k,v in state.items() if k!='content_sha256'}))):
            raise ValueError('Invalid catalogue descriptor identity, schema or checksum')
        if (type(state['version']) is not int or type(state['count']) is not int
                or state['version']<0 or state['count']<0 or type(state['path']) is not str):
            raise ValueError('Invalid catalogue descriptor counters or path')
        original=Path(state['path'])
        target=_path(original)
        if (not original.is_absolute() or original.is_symlink() or str(target)!=state['path']
                or (path is not None and _path(path)!=target) or not target.is_file()):
            raise ValueError('Catalogue descriptor requires its exact existing path; no relocation/recreation')
        _sidecars(target)
        before=_stamp(target)
        with target.open('rb') as stream:file_header=stream.read(100)
        if len(file_header)<100 or not file_header.startswith(b'SQLite format 3\x00') or file_header[18:20]!=b'\x01\x01':
            raise ValueError('Unsupported/corrupt SQLite catalogue format or WAL mode')
        expected=(state['version'],state['count'],state['head_digest'])
        connection=None
        try:
            connection=sqlite3.connect(target.as_uri()+'?mode=ro&immutable=1',uri=True,isolation_level=None)
            _verify(connection,raw,sha,expected)
            _sidecars(target)
            if _stamp(target)!=before:raise ValueError('Catalogue changed during read-only verification')
            if not read_only:
                connection.close()
                connection=sqlite3.connect(target.as_uri()+'?mode=rw',uri=True,isolation_level=None)
                _sidecars(target)
                if _stamp(target)!=before:raise ValueError('Catalogue changed before writable rebind')
                _schema(connection)
                if _header(connection,raw,sha)!=expected:raise ValueError('Catalogue changed before writable rebind')
            result=object.__new__(cls)
            result._bind(target,wanted,raw,sha,cache_limit,connection,expected,read_only)
            if not read_only:result._configure()
            return result
        except Exception as exc:
            if connection is not None:connection.close()
            if isinstance(exc,ValueError):raise
            raise ValueError('Cannot authenticate exact domain catalogue') from exc

    def close(self):
        if getattr(self,'_closed',True):return getattr(self,'last_close_receipt',None)
        try:
            receipt=self.rollback()
            receipt.update(schema=SCHEMA+'.close.v1',closed=True)
            self.last_close_receipt=receipt
        finally:
            self._connection.close()
            self._closed=True
        return self.last_close_receipt

    def __del__(self):
        try:self.close()
        except Exception:pass
