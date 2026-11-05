"""Explicit, volatile, process-local cache of certified exact solver results.

No state/control/input is rounded. Entries are performance state, never a
checkpoint or a replacement for current original-domain validation. The cache
retains only a result and its origin, not complete LP/history matrices.
"""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
import threading
from types import CodeType
import uuid

import numpy as np

SCHEMA = 'hospital_ies.accepted_solver_result_cache.v1'
MAX_ENTRIES = 2048
MAX_BYTES = 32 * 1024 * 1024
_ACTIVE = ContextVar('hospital_ies_accepted_result_cache', default=None)


def json_sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode('ascii')).hexdigest()


def _feed(h,value):
    def raw(kind,data):
        h.update(kind);h.update(struct.pack('!Q',len(data)));h.update(data)
    if value is None: raw(b'n',b'')
    elif type(value) is bool: raw(b'b',b'1' if value else b'0')
    elif isinstance(value,np.ndarray):
        if value.dtype.kind not in 'biuf' or (value.dtype.kind=='f' and np.isnan(value).any()):
            raise ValueError('Unsupported/nonfinite-NaN cache array')
        raw(b'a',value.dtype.str.encode('ascii'));_feed(h,tuple(value.shape));raw(b'd',value.tobytes(order='C'))
    elif isinstance(value,np.generic): _feed(h,value.item())
    elif type(value) is int: raw(b'i',str(value).encode('ascii'))
    elif type(value) is float:
        if math.isnan(value):raise ValueError('NaN cache key')
        raw(b'f',struct.pack('!d',value))
    elif type(value) is str:raw(b's',value.encode('utf8'))
    elif type(value) is bytes:raw(b'y',value)
    elif isinstance(value,(list,tuple)):
        raw(b'l' if isinstance(value,list) else b't',str(len(value)).encode('ascii'))
        for item in value:_feed(h,item)
    elif type(value) is dict:
        if any(type(k) is not str for k in value):raise TypeError('String cache dictionary keys required')
        raw(b'm',str(len(value)).encode('ascii'))
        for key in sorted(value):_feed(h,key);_feed(h,value[key])
    else:raise TypeError('Unsupported cache key/payload type: '+type(value).__name__)


def typed_sha(value):
    h=hashlib.sha256();_feed(h,value);return h.hexdigest()


def code_sha(code):
    """Stable code certificate, independent of marshal's mutable intern flags.

    Compile the pinned backend source without executing it, extract the named
    CodeType, and call this same function to independently reproduce the hash.
    File/line locations are bound by source SHA, not bytecode intern identity.
    """
    def constant(value):
        if isinstance(value,CodeType):return content(value)
        if isinstance(value,tuple):return tuple(constant(v) for v in value)
        if isinstance(value,frozenset):return dict(frozenset=sorted(typed_sha(constant(v)) for v in value))
        if isinstance(value,complex):return dict(complex_parts=(value.real,value.imag))
        if value is Ellipsis:return dict(ellipsis=True)
        return value
    def content(value):
        if not isinstance(value,CodeType):raise TypeError('Python CodeType required for source certificate')
        return dict(code=value.co_code,constants=tuple(constant(v) for v in value.co_consts),
            names=value.co_names,varnames=value.co_varnames,freevars=value.co_freevars,cellvars=value.co_cellvars,
            flags=value.co_flags,argcount=value.co_argcount,posonlyargcount=value.co_posonlyargcount,
            kwonlyargcount=value.co_kwonlyargcount,nlocals=value.co_nlocals,stacksize=value.co_stacksize)
    return typed_sha(content(code))


def _row_metadata_signature(rows):
    # Native original metadata is a list of flat dictionaries. C JSON preserves
    # exact strings and distinguishes int/float/bool/-0. No tuple/ndarray or
    # nested/complex representation is coerced into this fast path.
    if type(rows) is list and all(type(row) is dict and all(type(k) is str and
            (type(v) in (str,int,bool,type(None)) or type(v) is float and math.isfinite(v))
            for k,v in row.items()) for row in rows):
        return ('flat_json_v1',json_sha(rows))
    return ('typed_v1',typed_sha(rows))


def problem_key(problem,original,options,policy,source,native_identity,*,policy_signature=None):
    def domain(value):
        a=value['matrix']
        return dict(matrix=dict(shape=tuple(a.shape),data=a.data,indices=a.indices,indptr=a.indptr),
            lower=value['lower'],upper=value['upper'],bounds_lower=value['bounds_lower'],
            bounds_upper=value['bounds_upper'],integrality=value['integrality'])
    return typed_sha(dict(scaled=domain(problem),objective=problem['c'],original=domain(original),
        original_rows=_row_metadata_signature(original['rows']),options=options,
        numeric_policy=('typed_sha256_v1',typed_sha(policy) if policy_signature is None else policy_signature),source=source,
        native_identity=native_identity))


def _size(value,seen=None):
    seen=set() if seen is None else seen
    if id(value) in seen:return 0
    seen.add(id(value));size=sys.getsizeof(value)
    if isinstance(value,np.ndarray):size+=value.nbytes
    elif isinstance(value,dict):size+=sum(_size(k,seen)+_size(v,seen) for k,v in value.items())
    elif isinstance(value,(list,tuple)):size+=sum(_size(v,seen) for v in value)
    return size


class _AcceptedCache:
    def __init__(self,max_entries,max_bytes,scope_id):
        for value,maximum,name in ((max_entries,MAX_ENTRIES,'max_entries'),(max_bytes,MAX_BYTES,'max_bytes')):
            if type(value) is not int or not 1<=value<=maximum:raise ValueError('Invalid '+name)
        if scope_id is not None and (type(scope_id) is not str or not scope_id or len(scope_id)>128):
            raise ValueError('scope_id must be a nonempty bounded string')
        self.scope_id=scope_id or uuid.uuid4().hex
        self.pid=os.getpid();self.thread=threading.get_ident()
        self.max_entries=max_entries;self.max_bytes=max_bytes;self._entries=OrderedDict()
        self._bytes=0;self._peak=0
        self._code_certificate=None;self._policy_certificate=None
        self.source=dict(backend_sha256=hashlib.sha256(Path(__file__).with_name('solver_backend.py').read_bytes()).hexdigest(),
            helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        self.counts={k:0 for k in ('calls','lookups','misses','candidate_hits','hits','rejected_hits',
            'native_calls','puts','evictions','oversize_skips','key_unavailable','code_certificate_refreshes','policy_certificate_refreshes')}

    def _owner(self):
        if os.getpid()!=self.pid or threading.get_ident()!=self.thread:
            raise RuntimeError('Accepted solver cache cannot cross a process/thread owner')

    def begin_call(self):
        self._owner();self.counts['calls']+=1;return self.counts['calls']

    def certificates(self,native,policy_supplier,policy_tokens):
        """One bounded code/policy record; compare exact immutable tokens first.

        Function and CodeType references are retained, so reused Python ids
        cannot authenticate a different callable. Unsupported policy token
        types deliberately force the complete descriptor/hash path every call.
        """
        self._owner();code=getattr(native,'__code__',None)
        old=self._code_certificate
        if old is None or old[0] is not native or old[1] is not code:
            old=(native,code,code_sha(code));self._code_certificate=old
            self.counts['code_certificate_refreshes']+=1
        policy_code=getattr(policy_supplier,'__code__',None);prior=self._policy_certificate
        if (policy_tokens is None or prior is None or prior[0] is not policy_supplier
                or prior[1] is not policy_code or prior[2]!=policy_tokens):
            policy=policy_supplier()
            prior=(policy_supplier,policy_code,policy_tokens,policy,json_sha(policy),typed_sha(policy))
            self._policy_certificate=prior;self.counts['policy_certificate_refreshes']+=1
        return prior[3],prior[4],dict(self.source,native_code_sha256=old[2]),prior[5]

    def lookup(self,key):
        self._owner();self.counts['lookups']+=1
        entry=self._entries.get(key)
        if entry is None:self.counts['misses']+=1;return None,None
        self.counts['candidate_hits']+=1
        try:
            if typed_sha(dict(result=entry['result'],origin=entry['origin']))!=entry['payload_sha256']:
                return None,dict(reason='payload_integrity_mismatch')
            result,origin=copy.deepcopy(entry['result']),copy.deepcopy(entry['origin'])
        except (ValueError,TypeError,KeyError):return None,dict(reason='payload_integrity_mismatch')
        self._entries.move_to_end(key)
        return (result,origin),None

    def reject(self,key):
        self._owner();self.counts['rejected_hits']+=1
        old=self._entries.pop(key,None)
        if old is not None:self._bytes-=old['estimated_bytes']

    def put(self,key,result,origin):
        self._owner()
        fields=('status','x','fun','mip_dual_bound','mip_gap','mip_node_count','native_status','message','backend')
        payload={name:copy.deepcopy(result.get(name)) for name in fields if name in result}
        entry=dict(result=payload,origin=copy.deepcopy(origin))
        entry['payload_sha256']=typed_sha(entry)
        # Double all recursive sizes (including ndarray storage twice) and add
        # 1024 bytes per entry for OrderedDict/allocation/accounting overhead.
        estimated=2*(_size(entry)+_size(key))+1024
        if estimated>self.max_bytes:self.counts['oversize_skips']+=1;return
        old=self._entries.pop(key,None)
        if old is not None:self._bytes-=old['estimated_bytes']
        while self._entries and (len(self._entries)>=self.max_entries or self._bytes+estimated>self.max_bytes):
            _,removed=self._entries.popitem(last=False);self._bytes-=removed['estimated_bytes'];self.counts['evictions']+=1
        entry['estimated_bytes']=estimated;self._entries[key]=entry;self._bytes+=estimated
        self._peak=max(self._peak,self._bytes);self.counts['puts']+=1

    def stats(self):
        self._owner()
        return dict(schema=SCHEMA,enabled=True,scope_id=self.scope_id,process_id=self.pid,
            max_entries=self.max_entries,max_bytes=self.max_bytes,entries=len(self._entries),
            estimated_bytes=self._bytes,peak_estimated_bytes=self._peak,**self.counts,
            source=dict(self.source),estimate='2x recursive object/array bytes plus 1024 per entry; not process RSS',
            checkpointed=False,cache_mode='exact_complete_scaled_and_original_problem')


def active_cache():
    value=_ACTIVE.get()
    if value is not None:value._owner()
    return value


@contextmanager
def accepted_result_cache_scope(*,max_entries=MAX_ENTRIES,max_bytes=MAX_BYTES,scope_id=None):
    value=_AcceptedCache(max_entries,max_bytes,scope_id);token=_ACTIVE.set(value)
    try:yield value
    finally:_ACTIVE.reset(token)


def accepted_result_cache_stats():
    value=active_cache()
    return dict(schema=SCHEMA,enabled=False,entries=0,estimated_bytes=0,checkpointed=False) if value is None else value.stats()


def provenance(cache,call_id,key,source,policy_sha,record,*,origin=None):
    raw=record['raw_x']
    try:raw_sha=None if raw is None else json_sha(raw)
    except (ValueError,TypeError):raw_sha=None  # Preserve original failed/nonfinite raw diagnostics.
    value=dict(schema=SCHEMA,mode='native_solve',native_optimize_performed=True,
        scope_id=cache.scope_id,process_id=cache.pid,call_id=call_id,
        origin_scope_id=cache.scope_id,origin_process_id=cache.pid,origin_call_id=call_id,
        origin_attempt=record['attempt'],origin_accepted=record['accepted'],problem_sha256=key,
        raw_x_sha256=raw_sha,source=dict(source),numeric_policy_sha256=policy_sha,
        fresh_original_validation=record['validation'] is not None)
    if origin is not None:
        for name in ('origin_scope_id','origin_process_id','origin_call_id','origin_attempt','origin_accepted'):
            value[name]=origin[name]
        value.update(mode='exact_verified_reuse',native_optimize_performed=False)
    excluded=('mode','native_optimize_performed','call_id','fresh_original_validation','certificate_sha256')
    value['certificate_sha256']=json_sha({k:v for k,v in value.items() if k not in excluded})
    return value
