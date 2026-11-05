"""SF2 transport-only nonlinear CAS, ordered index and closed session.

No numerical acceptance or source approval is inferred by writing these files.
The independent reader must authenticate and recompute every original domain.
"""
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re

PREFIX='hospital_ies.sf2.'
BASE='evidence/nonlinear'

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')

def digest(value):return hashlib.sha256(canonical(value)).hexdigest()

def file_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def _sha(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


class NonlinearStore:
    """One fresh producer's explicit synchronous domain/result callback."""
    def __init__(self,producer,*,bindings,case_id,physical_model_spec,storage_model_spec,
                 defer_directory_initialization=False):
        given=Path(producer)
        if not given.is_dir() or given.is_symlink():raise ValueError('Existing exact producer directory required')
        self.root=given.resolve()
        self.bindings=deepcopy(bindings);self.case_id=case_id
        self.physical_model_spec=deepcopy(physical_model_spec);self.storage_model_spec=deepcopy(storage_model_spec)
        self.events=[];self.records=[];self.closed=False
        if type(defer_directory_initialization) is not bool:
            raise ValueError('Explicit boolean nonlinear directory initialization mode required')
        self._defer_directory_initialization=defer_directory_initialization
        self._directory_initialized=False
        if not defer_directory_initialization:self._initialize_directory()

    def _initialize_directory(self):
        if self._directory_initialized:return
        if self._defer_directory_initialization:
            if (not self._path('evidence').is_dir()
                    or any(not self._path('evidence/'+name).is_file()
                        for name in ('IDENTITY.json','blocks.jsonl'))):
                raise ValueError('Deferred nonlinear storage requires native journal ownership first')
        self._path(BASE).mkdir(parents=True,exist_ok=True)
        with self._path(BASE+'/INDEX.jsonl').open('xb') as stream:
            stream.flush();os.fsync(stream.fileno())
        self._directory_initialized=True

    def _path(self,relative):
        if (type(relative) is not str or not relative or '\\' in relative or ':' in relative
                or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts):
            raise ValueError('Unsafe producer-relative nonlinear artifact path')
        path=self.root.joinpath(*PurePosixPath(relative).parts)
        for part in (path,*path.parents):
            if part==self.root:break
            if part.is_symlink():raise ValueError('Nonlinear artifacts cannot use symlinks')
        if not path.resolve().is_relative_to(self.root):raise ValueError('Nonlinear artifact escaped producer')
        return path

    def _cas(self,kind,pin,body):
        if not _sha(pin) or digest(body)!=pin:raise ValueError('Nonlinear semantic digest mismatch')
        relative=f'{BASE}/{kind}s/{pin}.json.gz';path=self._path(relative)
        data=gzip.compress(canonical(body),mtime=0)
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            if not path.is_file() or path.read_bytes()!=data:raise ValueError('Existing nonlinear CAS bytes changed')
        else:
            with path.open('xb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
        return dict(path=relative,sha256=hashlib.sha256(data).hexdigest())

    def _append(self,kind,reference):
        event=dict(schema=PREFIX+'nonlinear_storage_event.v1',event=kind,
            scope_sequence=reference['scope_sequence'],record_id=reference['record_id'],reference=deepcopy(reference),
            previous_event_sha256=self.events[-1]['event_sha256'] if self.events else None)
        event['event_sha256']=digest(event)
        # Detect an earlier changed/partial index before adding another event.
        expected=b''.join(canonical(value)+b'\n' for value in self.events)
        path=self._path(BASE+'/INDEX.jsonl')
        if path.read_bytes()!=expected:raise ValueError('Nonlinear append index changed')
        with path.open('ab') as stream:
            stream.write(canonical(event)+b'\n');stream.flush();os.fsync(stream.fileno())
        self.events.append(event)

    def __call__(self,payload):
        if self.closed:raise ValueError('Nonlinear storage session already closed')
        if type(payload) is not dict:raise ValueError('Nonlinear event must be a detached JSON dictionary')
        value=deepcopy(payload);sequence=value.get('scope_sequence')
        if type(sequence) is not int or sequence<1:raise ValueError('Exact positive nonlinear scope sequence required')
        self._initialize_directory()
        schema=value.get('schema')
        if schema==PREFIX+'nonlinear_domain.v1':
            if (sequence!=len(self.records)+1 or self.records and self.records[-1]['result_reference'] is None):
                raise ValueError('Nonlinear domain sequence/order mismatch')
            body={k:v for k,v in value.items() if k not in ('domain_sha256','scope_sequence')}
            pin=value.get('domain_sha256')
            artifact=self._cas('domain',pin,body)
            ref=dict(schema=PREFIX+'nonlinear_domain_reference.v1',scope_sequence=sequence,
                record_id=f'nl_{sequence:08d}',domain_sha256=pin,artifacts=[artifact])
            self._append('domain',ref)
            self.records.append(dict(scope_sequence=sequence,domain_reference=deepcopy(ref),result_reference=None))
        elif schema==PREFIX+'nonlinear_result.v1':
            if (sequence!=len(self.records) or not self.records or self.records[-1]['result_reference'] is not None):
                raise ValueError('Nonlinear result sequence/order mismatch')
            original=self.records[-1]['domain_reference']
            if canonical(value.get('domain_reference'))!=canonical(original):raise ValueError('Nonlinear result domain reference mismatch')
            body={k:v for k,v in value.items() if k!='result_sha256'};pin=value.get('result_sha256')
            artifact=self._cas('result',pin,body)
            ref=dict(schema=PREFIX+'nonlinear_result_reference.v1',scope_sequence=sequence,
                record_id=original['record_id'],domain_sha256=original['domain_sha256'],result_sha256=pin,artifacts=[artifact])
            self._append('result',ref);self.records[-1]['result_reference']=deepcopy(ref)
        else:raise ValueError('Unsupported nonlinear event schema')
        return deepcopy(ref)

    def _verify(self):
        index=self._path(BASE+'/INDEX.jsonl')
        if index.read_bytes()!=b''.join(canonical(value)+b'\n' for value in self.events):
            raise ValueError('Nonlinear final index bytes changed')
        for event in self.events:
            ref=event['reference'];kind=event['event'];item=ref['artifacts'][0]
            path=self._path(item['path'])
            if not path.is_file() or file_sha(path)!=item['sha256']:raise ValueError('Nonlinear referenced artifact bytes changed')
            raw=gzip.decompress(path.read_bytes());body=json.loads(raw)
            if raw!=canonical(body) or hashlib.sha256(raw).hexdigest()!=ref[kind+'_sha256']:
                raise ValueError('Nonlinear decompressed semantic identity mismatch')

    def receipt(self,core_session):
        session_path=BASE+'/SESSION.json';index_path=BASE+'/INDEX.jsonl'
        return dict(required=True,session=dict(path=session_path,sha256=file_sha(self._path(session_path))),
            index=dict(path=index_path,sha256=file_sha(self._path(index_path)),event_count=len(self.events),
                last_event_sha256=self.events[-1]['event_sha256'] if self.events else None),
            call_count=core_session.get('call_count'),completed_call_count=core_session.get('completed_call_count'))

    def finish(self,core_session):
        if self.closed:raise ValueError('Nonlinear storage session already closed')
        self._initialize_directory()
        session=deepcopy(core_session);error=None
        try:
            if (type(session) is not dict or session.get('schema')!=PREFIX+'nonlinear_evidence_session.v1'
                    or session.get('status') not in ('closed_completed','closed_failed')):
                raise ValueError('A real closed nonlinear core session is required')
            self._verify()
            if session['status']=='closed_completed':
                count=session.get('call_count')
                if (type(count) is not int or count<1 or type(session.get('completed_call_count')) is not int
                        or session['completed_call_count']!=count or count!=len(self.records)
                        or canonical(session.get('records'))!=canonical(self.records)
                        or len(self.events)!=2*count or any(row['result_reference'] is None for row in self.records)):
                    raise ValueError('Nonlinear successful session lacks complete exact call/result coverage')
        except Exception as exc:error=exc
        value=dict(schema=PREFIX+'nonlinear_storage_session.v1',core_session=session,
            index=dict(path=BASE+'/INDEX.jsonl',sha256=file_sha(self._path(BASE+'/INDEX.jsonl')),
                event_count=len(self.events),last_event_sha256=self.events[-1]['event_sha256'] if self.events else None),
            source_identity=dict(bindings=deepcopy(self.bindings)),case_id=self.case_id,
            physical_model_spec=deepcopy(self.physical_model_spec),storage_model_spec=deepcopy(self.storage_model_spec),
            producer_root=str(self.root))
        if error is not None:value['storage_error']=dict(type=type(error).__name__,message=str(error))
        data=canonical(value)+b'\n'
        with self._path(BASE+'/SESSION.json').open('xb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        self.closed=True
        if error is not None:raise ValueError('Nonlinear storage finalization failed: '+str(error)) from error
        return self.receipt(session)
