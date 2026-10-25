"""Lossless content-addressed successful domain rows; numerical inputs unchanged."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import gzip
import hashlib
import json
from pathlib import Path
from tools.sci_engineering.solver_log import normalize_json

REFERENCE_SCHEMA='hospital_ies.m23.domain_success_rows.reference.v1'
_CURRENT=ContextVar('m23_domain_evidence',default=None)

@contextmanager
def evidence_scope(store):
    token=_CURRENT.set(store)
    try:yield
    finally:_CURRENT.reset(token)

def _row_chunks(rows):
    yield b'['
    for i,row in enumerate(rows):
        if i:yield b','
        yield json.dumps(normalize_json(row),ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf8')
    yield b']'

class DomainEvidenceStore:
    """Per-run successful rows saved once by canonical content SHA.

    Each blob closes immediately. Metadata and failure/rejection rows remain
    inline. This store is independent of ordered SolverRowSidecar records.
    """
    def __init__(self,directory):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self._known={}

    def store(self,rows):
        digest=hashlib.sha256();size=0
        for chunk in _row_chunks(rows):digest.update(chunk);size+=len(chunk)
        sha=digest.hexdigest()
        if sha in self._known:return copy.deepcopy(self._known[sha])
        path=self.directory/(sha+'.json.gz')
        if path.exists():raise FileExistsError('Unregistered existing domain evidence blob: '+str(path))
        with path.open('xb') as raw:
            with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as zipped:
                buffer=bytearray()
                for chunk in _row_chunks(rows):
                    buffer.extend(chunk)
                    if len(buffer)>=1024*1024:zipped.write(buffer);buffer.clear()
                if buffer:zipped.write(buffer)
        ref=dict(schema=REFERENCE_SCHEMA,directory=self.directory.name,file=path.name,rows_sha256=sha,
            row_count=len(rows),canonical_bytes=size,gzip_bytes=path.stat().st_size,
            canonicalization='UTF8 sorted compact JSON; nonfinite floats -> null')
        self._known[sha]=ref
        return copy.deepcopy(ref)

    def receipt(self):
        return dict(schema='hospital_ies.m23.domain_success_rows.store.v1',directory=str(self.directory.resolve()),
            closed_blobs=len(self._known),gzip_bytes=sum(x['gzip_bytes'] for x in self._known.values()),
            numerical_or_physical_changes=False,ordered_solver_sidecar_shared=False)

def successful_backend_evidence(layers):
    store=_CURRENT.get()
    if store is None:return copy.deepcopy(layers)
    def walk(value,eligible):
        if isinstance(value,list):return [walk(item,eligible) for item in value]
        if not isinstance(value,dict):return copy.deepcopy(value)
        if 'status' in value and isinstance(value['status'],int):eligible=eligible and value['status']==0
        if 'accepted' in value:eligible=eligible and value['accepted'] is True
        return {('rows_ref' if key=='rows' and eligible and isinstance(item,list) else key):
            (store.store(item) if key=='rows' and eligible and isinstance(item,list) else walk(item,eligible))
            for key,item in value.items()}
    return walk(layers,True)

def restore_domain_evidence(value,directory):
    directory=Path(directory)
    if isinstance(value,list):return [restore_domain_evidence(item,directory) for item in value]
    if not isinstance(value,dict):return copy.deepcopy(value)
    restored={}
    for key,item in value.items():
        if key=='rows_ref' and isinstance(item,dict) and item.get('schema')==REFERENCE_SCHEMA:
            with gzip.open(directory/item['file'],'rt',encoding='utf8') as stream:rows=json.load(stream)
            digest=hashlib.sha256()
            for chunk in _row_chunks(rows):digest.update(chunk)
            if digest.hexdigest()!=item['rows_sha256'] or len(rows)!=item['row_count']:raise ValueError('Domain evidence SHA/row count mismatch')
            restored['rows']=rows
        else:restored[key]=restore_domain_evidence(item,directory)
    return restored
