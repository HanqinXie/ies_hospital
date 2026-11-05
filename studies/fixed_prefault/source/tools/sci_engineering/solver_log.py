"""Lossless externalization of successful solver-row *logging* evidence only.

The incoming numerical result is never changed. Failed/unknown calls, layers,
and attempts keep their complete rows inline. JSON normalization matches the
existing dataset_export contract (nonfinite floats become null).
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

SCHEMA='hospital_ies.solver_validation_rows.sidecar.v1'
REF_SCHEMA='hospital_ies.solver_validation_rows.reference.v1'
JSON_POLICY='UTF8 compact JSON; nonfinite floats -> null, matching dataset_export.json_clean'


def normalize_json(value):
    if isinstance(value,dict):return {str(k):normalize_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [normalize_json(v) for v in value]
    if hasattr(value,'tolist'):return normalize_json(value.tolist())
    if isinstance(value,float) and not math.isfinite(value):return None
    return value


def _bytes(value):
    return json.dumps(normalize_json(value),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf8')


def _sha_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def _successful_status(status):return isinstance(status,int) and not isinstance(status,bool) and status==0


class SolverRowSidecar:
    """One exclusive gzip JSONL file per actual trajectory attempt.

    Closing produces a hash/size/count receipt even when the surrounding
    episode raises. A write error marks the file incomplete and must propagate
    to the caller; the writer never substitutes missing rows with zeros.
    """
    def __init__(self,path,*,flush_bytes=1024*1024):
        if isinstance(flush_bytes,bool) or int(flush_bytes)<=0:raise ValueError('Positive flush_bytes required')
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.flush_bytes=int(flush_bytes);self._buffer=bytearray()
        self._binary=self.path.open('xb')
        try:self._gzip=gzip.GzipFile(filename='',fileobj=self._binary,mode='wb',mtime=0)
        except BaseException:self._binary.close();raise
        self.closed=False;self.entry_count=0;self.row_count=0;self.attempted_entry_count=0
        self.write_calls=0;self.write_error=None;self.close_error=None;self.body_exception=None
        self._receipt=None

    def __enter__(self):
        if self.closed:raise RuntimeError('Solver row sidecar is closed')
        return self

    def __exit__(self,kind,error,tb):
        if error is not None:self.body_exception=dict(type=kind.__name__,message=str(error))
        try:self.close()
        except BaseException:
            if error is None:raise
            # Retain the original physical/body exception; close error remains
            # explicit in receipt rather than replacing its causal identity.
        return False

    def _flush_block(self,data):
        self._gzip.write(data);self.write_calls+=1

    def _write(self,data):
        view=memoryview(data);start=0
        while start<len(view):
            n=min(self.flush_bytes-len(self._buffer),len(view)-start)
            self._buffer.extend(view[start:start+n]);start+=n
            if len(self._buffer)>=self.flush_bytes:
                self._flush_block(self._buffer);self._buffer.clear()

    def store(self,rows,*,location):
        if self.closed:raise RuntimeError('Solver row sidecar is closed')
        if self.write_error is not None:raise RuntimeError('Solver row sidecar previously failed')
        if not isinstance(rows,list):raise ValueError('Validation rows must be a list')
        self.attempted_entry_count+=1
        record_id='rows_'+str(self.attempted_entry_count).zfill(9)
        row_bytes=_bytes(rows);row_sha=hashlib.sha256(row_bytes).hexdigest()
        header=_bytes(dict(schema=SCHEMA,record_id=record_id,location=location))
        tail=_bytes(dict(rows_sha256=row_sha,row_count=len(rows)))
        try:
            self._write(header[:-1]+b',"rows":')
            self._write(row_bytes)
            self._write(b','+tail[1:]+b'\n')
        except BaseException as error:
            self.write_error=dict(type=type(error).__name__,message=str(error),record_id=record_id,location=copy.deepcopy(location))
            raise
        self.entry_count+=1;self.row_count+=len(rows)
        return dict(schema=REF_SCHEMA,file=self.path.name,record_id=record_id,rows_sha256=row_sha,
                    row_count=len(rows),json_policy=JSON_POLICY)

    def close(self):
        if self.closed:return self.receipt()
        failure=None
        try:
            if self._buffer and self.write_error is None:self._flush_block(self._buffer)
        except BaseException as error:
            self.close_error=dict(type=type(error).__name__,message=str(error));failure=error
        finally:
            self._buffer.clear()
            try:self._gzip.close()
            except BaseException as error:
                self.close_error=dict(type=type(error).__name__,message=str(error));failure=failure or error
            finally:
                try:self._binary.close()
                except BaseException as error:
                    self.close_error=dict(type=type(error).__name__,message=str(error));failure=failure or error
            self.closed=True
        self._receipt=dict(schema=SCHEMA,path=str(self.path.resolve()),closed=True,
            status='failed' if self.write_error or self.close_error else 'aborted' if self.body_exception else 'completed',
            entry_count=self.entry_count,row_count=self.row_count,attempted_entry_count=self.attempted_entry_count,
            gzip_bytes=self.path.stat().st_size,gzip_sha256=_sha_file(self.path),write_calls=self.write_calls,
            json_policy=JSON_POLICY,body_exception=copy.deepcopy(self.body_exception),
            write_error=copy.deepcopy(self.write_error),close_error=copy.deepcopy(self.close_error),
            partial_tail_possible=bool(self.write_error or self.close_error))
        if failure is not None:raise failure
        return self.receipt()

    def receipt(self):
        if self.closed:return copy.deepcopy(self._receipt)
        return dict(schema=SCHEMA,path=str(self.path.resolve()),closed=False,status='open',
            entry_count=self.entry_count,row_count=self.row_count,attempted_entry_count=self.attempted_entry_count,
            gzip_bytes=None,gzip_sha256=None,write_calls=self.write_calls,json_policy=JSON_POLICY,
            body_exception=copy.deepcopy(self.body_exception),write_error=copy.deepcopy(self.write_error),close_error=copy.deepcopy(self.close_error))


class SolverCallLog(list):
    """List-compatible solver-call sink, copying only logging structures."""
    def __init__(self,iterable:Iterable=(),*,writer:SolverRowSidecar):
        super().__init__();self.writer=writer;self.extend(iterable)

    def _validation(self,value,location,eligible):
        if not isinstance(value,dict):return copy.deepcopy(value)
        result={}
        for key,item in value.items():
            if key=='rows' and eligible and isinstance(item,list):
                if 'rows_ref' in value:raise ValueError('Validation has both rows and rows_ref')
                result['rows_ref']=self.writer.store(item,location=location)
            else:result[key]=copy.deepcopy(item)
        return result

    def _attempt(self,value,location,eligible):
        if not isinstance(value,dict):return copy.deepcopy(value)
        accepted=eligible and _successful_status(value.get('status')) and value.get('accepted') is True
        return {key:self._validation(item,location+[key],accepted) if key in ('validation','solver_validation')
                else copy.deepcopy(item) for key,item in value.items()}

    def _layer(self,value,location,eligible):
        if not isinstance(value,dict):return copy.deepcopy(value)
        success=eligible and _successful_status(value.get('status'))
        result={}
        for key,item in value.items():
            if key in ('validation','solver_validation'):result[key]=self._validation(item,location+[key],success)
            elif key=='attempts' and isinstance(item,list):
                result[key]=[self._attempt(a,location+[key,i],success) for i,a in enumerate(item)]
            else:result[key]=copy.deepcopy(item)
        return result

    def append(self,value):
        if self.writer.closed:raise RuntimeError('Solver row sidecar is closed')
        if not isinstance(value,dict):raise TypeError('Solver call must be a dictionary')
        success=value.get('status')=='success'
        copied={}
        for key,item in value.items():
            if key=='solver_backend_layers' and isinstance(item,list):
                copied[key]=[self._layer(layer,[len(self),key,i],success) for i,layer in enumerate(item)]
            else:copied[key]=copy.deepcopy(item)
        super().append(copied)

    def extend(self,values):
        for value in values:self.append(value)

    def __iadd__(self,values):self.extend(values);return self


def iter_sidecar_entries(path):
    """Streaming integrity reader; fails on malformed/truncated rows or SHA."""
    seen=set()
    with gzip.open(path,'rt',encoding='utf8') as f:
        for number,line in enumerate(f,start=1):
            if not line.endswith('\n'):raise ValueError('Incomplete sidecar JSONL tail')
            entry=json.loads(line)
            if entry.get('schema')!=SCHEMA or entry.get('record_id') in seen:raise ValueError('Invalid sidecar record identity')
            seen.add(entry['record_id'])
            if len(entry['rows'])!=entry['row_count']:raise ValueError('Sidecar row count mismatch')
            if hashlib.sha256(_bytes(entry['rows'])).hexdigest()!=entry['rows_sha256']:raise ValueError('Sidecar rows SHA mismatch')
            yield entry


def restore_solver_calls(calls,path):
    """Restore original exported JSON semantics. Reader streams sidecar rows.

    The returned full payload naturally occupies memory; consumers that only
    audit rows should use iter_sidecar_entries instead.
    """
    entries=iter(iter_sidecar_entries(path))
    def restore(value,location):
        if isinstance(value,list):return [restore(x,location+[i]) for i,x in enumerate(value)]
        if not isinstance(value,dict):return copy.deepcopy(value)
        result={}
        for key,item in value.items():
            if key=='rows_ref' and isinstance(item,dict) and item.get('schema')==REF_SCHEMA:
                try:entry=next(entries)
                except StopIteration:raise ValueError('Missing sidecar reference record') from None
                if any(entry[k]!=item[k] for k in ('record_id','rows_sha256','row_count')):raise ValueError('Sidecar reference SHA/identity mismatch')
                if entry.get('location')!=location:raise ValueError('Sidecar record location mismatch')
                result['rows']=entry['rows']
            else:result[key]=restore(item,location+[key])
        return result
    restored=restore(list(calls),[])
    if next(entries,None) is not None:raise ValueError('Unreferenced sidecar record')
    return restored
