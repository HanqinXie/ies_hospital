"""Lossless content-addressed logging rows; default success-only, opt-in all."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import gzip
import hashlib
import json
from pathlib import Path
from tools.sci_engineering.solver_log import normalize_json,SolverCallLog

REFERENCE_SCHEMA='hospital_ies.m23.domain_success_rows.reference.v1'
ALL_REFERENCE_SCHEMA='hospital_ies.m23.validation_rows.reference.v1'
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
    """Per-run logging rows saved once by canonical content SHA.

    Each blob closes immediately. Failure/rejection rows stay inline by
    default. All-row mode retains their metadata inline and externalizes
    only row tables, independently of ordered SolverRowSidecar records.
    """
    def __init__(self,directory,*,enable_all_validation_rows=False):
        if type(enable_all_validation_rows) is not bool:raise TypeError('All-row logging switch must be boolean')
        self.enable_all_validation_rows=enable_all_validation_rows
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
        ref=dict(schema=ALL_REFERENCE_SCHEMA if self.enable_all_validation_rows else REFERENCE_SCHEMA,directory=self.directory.name,file=path.name,rows_sha256=sha,
            row_count=len(rows),canonical_bytes=size,gzip_bytes=path.stat().st_size,
            canonicalization='UTF8 sorted compact JSON; nonfinite floats -> null')
        self._known[sha]=ref
        return copy.deepcopy(ref)

    def receipt(self):
        return dict(schema='hospital_ies.m23.domain_success_rows.store.v1',directory=str(self.directory.resolve()),
            closed_blobs=len(self._known),gzip_bytes=sum(x['gzip_bytes'] for x in self._known.values()),
            numerical_or_physical_changes=False,ordered_solver_sidecar_shared=False,
            enable_all_validation_rows=self.enable_all_validation_rows)

def successful_backend_evidence(layers):
    store=_CURRENT.get()
    if store is None:return copy.deepcopy(layers)
    if getattr(store,'enable_all_validation_rows',False):return _all_validation_evidence(layers,store)
    def walk(value,eligible):
        if isinstance(value,list):return [walk(item,eligible) for item in value]
        if not isinstance(value,dict):return copy.deepcopy(value)
        if 'status' in value and isinstance(value['status'],int):eligible=eligible and value['status']==0
        if 'accepted' in value:eligible=eligible and value['accepted'] is True
        return {('rows_ref' if key=='rows' and eligible and isinstance(item,list) else key):
            (store.store(item) if key=='rows' and eligible and isinstance(item,list) else walk(item,eligible))
            for key,item in value.items()}
    return walk(layers,True)


def _all_validation_evidence(value,store,*,validation=False):
    """Only validation.rows is externalized; status and raw vectors stay put."""
    if isinstance(value,list):return [_all_validation_evidence(item,store,validation=validation) for item in value]
    if not isinstance(value,dict):return copy.deepcopy(value)
    result={}
    for key,item in value.items():
        if validation and key=='rows' and isinstance(item,list):
            if 'rows_ref' in value:raise ValueError('Validation has both rows and rows_ref')
            result['rows_ref']=store.store(item)
        else:result[key]=_all_validation_evidence(item,store,validation=key=='validation' or key.endswith('_validation'))
    return result


def retained_backend_evidence(layers):
    """Failure/rejection stays inline unless the explicit all-row mode is on."""
    store=_CURRENT.get()
    return (_all_validation_evidence(layers,store) if store is not None and getattr(store,'enable_all_validation_rows',False)
            else copy.deepcopy(layers))


class _AllRowsOrderedLog(SolverCallLog):
    def __init__(self,values,*,writer,store):
        self.store=store
        super().__init__(values,writer=writer)

    def _validation(self,value,location,eligible):
        if eligible:return super()._validation(value,location,eligible)
        return _all_validation_evidence(value,self.store,validation=True)

    def _layer(self,value,location,eligible):
        # Keep the inherited accepted-row ordering, then externalize prior
        # incumbent/refinement validation aliases left in this logging copy.
        return _all_validation_evidence(super()._layer(value,location,eligible),self.store)

    def append(self,value):
        if self.writer.closed:raise RuntimeError('Solver row sidecar is closed')
        if not isinstance(value,dict):raise TypeError('Solver call must be a dictionary')
        success=value.get('status')=='success';copied={}
        for key,item in value.items():
            if key=='solver_backend_layers' and isinstance(item,list):
                copied[key]=[self._layer(layer,[len(self),key,i],success) for i,layer in enumerate(item)]
            else:copied[key]=_all_validation_evidence(item,self.store,validation=key=='validation' or key.endswith('_validation'))
        list.append(self,copied)


class _AllRowsLog(list):
    def __init__(self,values,*,store):
        super().__init__();self.store=store;self.extend(values)

    def append(self,value):super().append(_all_validation_evidence(value,self.store))
    def extend(self,values):
        for value in values:self.append(value)
    def __iadd__(self,values):self.extend(values);return self


def all_rows_solver_log(values,store):
    """Accepted calls keep their ordered writer; other rows use separate CAS."""
    if isinstance(values,SolverCallLog):return _AllRowsOrderedLog(values,writer=values.writer,store=store)
    return _AllRowsLog(values,store=store)

def restore_domain_evidence(value,directory):
    directory=Path(directory)
    if isinstance(value,list):return [restore_domain_evidence(item,directory) for item in value]
    if not isinstance(value,dict):return copy.deepcopy(value)
    restored={}
    for key,item in value.items():
        if key=='rows_ref' and isinstance(item,dict) and item.get('schema') in (REFERENCE_SCHEMA,ALL_REFERENCE_SCHEMA):
            with gzip.open(directory/item['file'],'rt',encoding='utf8') as stream:rows=json.load(stream)
            digest=hashlib.sha256()
            for chunk in _row_chunks(rows):digest.update(chunk)
            if digest.hexdigest()!=item['rows_sha256'] or len(rows)!=item['row_count']:raise ValueError('Domain evidence SHA/row count mismatch')
            restored['rows']=rows
        else:restored[key]=restore_domain_evidence(item,directory)
    return restored
