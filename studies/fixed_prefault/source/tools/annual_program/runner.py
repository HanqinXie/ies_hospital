"""Opt-in continuous annual execution and controlled, authenticated resume.

The same queued physical executor and frozen M/C controller perform each real
step. This module changes storage/orchestration only. A suspended case has an
open journal and a published quiescent checkpoint; it is never called complete.
Unexpected newer evidence is retained and rejected, never silently truncated.
An optional frozen E-drive stop-file path is external orchestration only. Its
existence is checked before startup and at acknowledged post-step boundaries;
the runner never reads its contents or deletes the request.

Resource admission is deliberately not implied: execution lookup maps retain
only future support and failure records reference exact disk prefixes, but domain
catalogues/checkpoint manifests and result metadata still need measured bounds.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, nullcontext
import copy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from contextlib import nullcontext

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

from tools.annual_readiness import runner as legacy
from tools.annual_readiness.journal import BlockJournal,_record_digest
from tools.sci_engineering.design import digest,SEALED_DAYS
from tools.sci_engineering.solver_log import normalize_json,SolverRowSidecar
from . import MODEL_ID
from .access import create_runtime_freeze,validate_annual_access
from .checkpoint import capture_checkpoint,save_checkpoint,load_checkpoint,restore_checkpoint,_decode
from .disk_history import DiskHistory
from .journal import checkpoint_receipt,reopen_journal,publish_checkpoint
from .policy_history import ExactPolicyHistory
from .registration import validate_case,required_hours,load_registry
from .service_history import ServiceHistoryAccumulator
from .source_runtime import AnnualSourceMixin
from .runtime_history import compact_execution_maps
from .domain_store import DomainStore

SCHEMA=MODEL_ID+'.runner.v1'
_snapshot=legacy._snapshot
_terminal=legacy._terminal
_block_evaluation=legacy._block_evaluation
_seal_solver_evidence=legacy._seal_solver_evidence
_write_new=legacy._write_new
_read=legacy._read
_append=legacy._append
_bindings=legacy._bindings
_HISTORY_LOCATIONS=('core.segments','core.events','core.errors','env.history',
    'env.zone_history','env.trip_events','env.planning_queries')
_REBUILD_ENV=frozenset(('root','hourly','zones','groups','plant','thermal_cache',
    '_v27_original_profiles','_v27_profile_frames','engineering_core_type','reset'))
_RECORDING_MODES=('full_v1','engineering_v1')


def _error(exc):
    value=legacy._error(exc)
    for name in ('annual_program_access','input_access_receipt'):
        if hasattr(exc,name):value[name]=normalize_json(copy.deepcopy(getattr(exc,name)))
    return value


def _limit(value,name,*,optional=True):
    if optional and value is None:return
    if type(value) is not int or value<=0:raise ValueError(name+' must be a positive integer')


class StopRequestedBeforeStart(RuntimeError):
    """A registered external request prevented any new physical invocation."""
    def __init__(self,path):
        self.stop_file=str(path);self.status='not_started';self.actual_episode_attempts=0
        super().__init__('Stop requested before runtime start: '+self.stop_file)


def _stop_file_path(value):
    if value is None:return None
    path=Path(value).absolute()
    if path.is_symlink():raise ValueError('Registered stop file cannot be a symlink')
    path=path.resolve()
    if os.name=='nt' and path.drive.upper()!='E:':raise ValueError('Registered stop file must remain on E')
    if path.exists() and not path.is_file():raise ValueError('Registered stop path must be a regular file')
    return path


def _stop_requested(path):
    if path is None:return False
    if _stop_file_path(path)!=path:raise ValueError('Registered stop file path now resolves differently')
    return path.exists()


def _refuse_preexisting_stop(path):
    if _stop_requested(path):raise StopRequestedBeforeStart(path)


def _recording_mode(value):
    if type(value) is not str or value not in _RECORDING_MODES:
        raise ValueError('recording_mode must be full_v1 or engineering_v1')
    return value


def _check_recording_mode(state,recording_mode):
    # Published pre-mode checkpoints/ledgers belong to the old full contract.
    inherited=_recording_mode(state.get('recording_mode','full_v1'))
    if inherited!=recording_mode:raise ValueError('Resumed recording_mode differs from its frozen state')


def _apply_recording_mode(env,recording_mode):
    store=getattr(env,'domain_evidence',None)
    if store is not None:store.engineering_summary=recording_mode=='engineering_v1'


def _recovery_options(every,vault):
    if every is None and vault is None:return None,None
    _limit(every,'recovery_every_blocks',optional=False)
    if vault is None:raise ValueError('An explicit recovery vault is required')
    path=Path(vault).absolute()
    if path.is_symlink() or path!=path.resolve():raise ValueError('Recovery vault path cannot be an alias')
    if os.name=='nt' and path.drive.upper()!='E:':raise ValueError('Recovery vault must remain on E')
    return every,path


def _orchestration_freeze(stop_file,checkpoint_every_blocks=96,recording_mode='full_v1',*,
                          recovery_every_blocks=None,recovery_vault=None,accepted_result_cache=False):
    _limit(checkpoint_every_blocks,'checkpoint_every_blocks',optional=False)
    _recording_mode(recording_mode)
    receipt=dict(schema=MODEL_ID+'.external_orchestration_freeze.v2',
        stop_file=str(stop_file) if stop_file is not None else None,
        checkpoint_every_blocks=checkpoint_every_blocks,
        stop_file_existence_only=True,external_orchestration_only=True,policy_input=False)
    if recording_mode=='engineering_v1':
        receipt.update(schema=MODEL_ID+'.external_orchestration_freeze.v3',recording_mode=recording_mode)
    every,vault=_recovery_options(recovery_every_blocks,recovery_vault)
    if every is not None:
        receipt.update(schema=MODEL_ID+'.external_orchestration_freeze.v4',
            recording_mode=recording_mode,recovery_every_blocks=every,recovery_vault=str(vault))
    if type(accepted_result_cache) is not bool:raise ValueError('Cache setting must be an explicit boolean')
    if accepted_result_cache:
        receipt.update(schema=MODEL_ID+'.external_orchestration_freeze.v6',recording_mode=recording_mode,
            accepted_result_cache=dict(mode='exact_verified_problem',max_entries=2048,max_bytes=33554432))
    receipt['signature']=digest(receipt)
    return receipt


def _sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


class _PrefixPart:
    def __init__(self,output,index):
        self.output=Path(output);self.path=self.output/'actual_prefix'/f'part_{index:06d}.jsonl.gz'
        self.path.parent.mkdir(exist_ok=True)
        self.raw=self.path.open('xb')
        self.zipped=gzip.GzipFile(filename='',fileobj=self.raw,mode='wb',mtime=0)
        self.closed=False;self.count=0

    def append(self,segment):
        if self.closed:raise RuntimeError('Actual prefix part is closed')
        self.zipped.write(legacy._bytes(segment));self.zipped.flush()
        self.raw.flush();os.fsync(self.raw.fileno());self.count+=1

    def close(self):
        if self.closed:raise RuntimeError('Actual prefix part already closed')
        try:
            self.zipped.close();self.raw.flush();os.fsync(self.raw.fileno())
        finally:
            self.raw.close();self.closed=True
        return dict(file=self.path.relative_to(self.output).as_posix(),sha256=_sha(self.path),
            bytes=self.path.stat().st_size,segment_count=self.count,complete_gzip=True)


def _verify_prefix_parts(output,parts):
    output=Path(output).resolve();expected=set()
    for index,row in enumerate(parts):
        name=f'actual_prefix/part_{index:06d}.jsonl.gz'
        if row.get('file')!=name or row.get('complete_gzip') is not True:
            raise ValueError('Incomplete or unordered actual prefix part receipt')
        path=(output/name).resolve()
        if not path.is_relative_to(output/'actual_prefix') or path.is_symlink() or not path.is_file():
            raise ValueError('Missing or substituted actual prefix part')
        if path.stat().st_size!=row.get('bytes') or _sha(path)!=row.get('sha256'):
            raise ValueError('Actual prefix is newer or changed after checkpoint')
        expected.add(name)
    directory=output/'actual_prefix'
    actual={p.relative_to(output).as_posix() for p in directory.iterdir()} if directory.exists() else set()
    if actual!=expected:raise ValueError('Unacknowledged extra actual prefix evidence prevents resume')


def _known_domain_files(known):
    from tools.model_correctness_v23.evidence import REFERENCE_SCHEMA,ALL_REFERENCE_SCHEMA
    if type(known) is not dict:raise ValueError('Explicit native domain known catalog required')
    files={}
    for sha,ref in known.items():
        if (not isinstance(sha,str) or re.fullmatch('[0-9a-f]{64}',sha) is None
                or not isinstance(ref,dict) or ref.get('rows_sha256')!=sha
                or ref.get('schema') not in (REFERENCE_SCHEMA,ALL_REFERENCE_SCHEMA)
                or ref.get('directory')!='domain_evidence' or ref.get('file')!=sha+'.json.gz'
                or any(type(ref.get(key)) is not int or ref[key]<0
                    for key in ('row_count','canonical_bytes','gzip_bytes'))):
            raise ValueError('Invalid native domain known catalog reference')
        name='domain_evidence/'+ref['file']
        if name in files:raise ValueError('Duplicate native domain catalog filename')
        files[name]=ref
    return files


def _domain_manifest(output,known):
    """Bind every known blob, including probes outside final block references.

    Validate canonical row count/SHA/size one blob at a time and fsync the
    previously closed native files before publishing a typed checkpoint.
    """
    from tools.model_correctness_v23.evidence import _row_chunks
    output=Path(output).resolve();directory=output/'domain_evidence'
    if directory.is_symlink():raise ValueError('Native domain directory cannot be substituted')
    references=_known_domain_files(known);files={}
    def nonfinite(value):raise ValueError('Nonfinite canonical domain row value: '+value)
    for name,ref in references.items():
        path=output/name
        if path.is_symlink() or not path.is_file():raise ValueError('Missing native known domain file')
        with path.open('r+b') as stream:os.fsync(stream.fileno())
        if path.stat().st_size!=ref['gzip_bytes']:raise ValueError('Native domain gzip byte size changed')
        with gzip.open(path,'rt',encoding='utf8') as stream:rows=json.load(stream,parse_constant=nonfinite)
        if type(rows) is not list or len(rows)!=ref['row_count']:
            raise ValueError('Native domain canonical row count mismatch')
        canonical=hashlib.sha256();size=0
        for chunk in _row_chunks(rows):canonical.update(chunk);size+=len(chunk)
        if canonical.hexdigest()!=ref['rows_sha256'] or size!=ref['canonical_bytes']:
            raise ValueError('Native domain canonical rows SHA/size mismatch')
        files[name]=dict(gzip_sha256=_sha(path),gzip_bytes=path.stat().st_size,
            rows_sha256=ref['rows_sha256'],row_count=ref['row_count'],canonical_bytes=size,
            canonical_rows_verified_before_checkpoint=True,durable_before_checkpoint=True)
        del rows
    actual={p.relative_to(output).as_posix() for p in directory.rglob('*') if p.is_file()} if directory.exists() else set()
    if actual!=set(files):raise ValueError('Unregistered extra/orphan domain file outside native known catalog')
    manifest=dict(schema=MODEL_ID+'.complete_domain_manifest.v1',files=files,
        known_catalog_sha256=digest(known),known_file_count=len(references))
    manifest['content_sha256']=digest(manifest)
    return manifest


def _checked_domain_manifest(manifest,known):
    if (not isinstance(manifest,dict) or set(manifest)!={'schema','files','known_catalog_sha256',
            'known_file_count','content_sha256'} or manifest.get('schema')!=MODEL_ID+'.complete_domain_manifest.v1'
            or manifest['content_sha256']!=digest({k:v for k,v in manifest.items() if k!='content_sha256'})):
        raise ValueError('Invalid complete domain manifest integrity/schema')
    files=manifest['files']
    if type(files) is not dict or type(manifest['known_file_count']) is not int or manifest['known_file_count']!=len(files):
        raise ValueError('Domain manifest known file count mismatch')
    if known is not None:
        references=_known_domain_files(known)
        if digest(known)!=manifest['known_catalog_sha256'] or set(references)!=set(files):
            raise ValueError('Domain manifest differs from complete native known catalog')
        for name,ref in references.items():
            if any(files[name].get(key)!=ref[key] for key in ('rows_sha256','row_count','canonical_bytes','gzip_bytes')):
                raise ValueError('Domain manifest canonical rows differ from known catalog')
    for name,ref in files.items():
        sha=ref.get('rows_sha256') if isinstance(ref,dict) else None
        if (not isinstance(sha,str) or re.fullmatch('[0-9a-f]{64}',sha) is None
                or name!='domain_evidence/'+sha+'.json.gz'
                or ref.get('canonical_rows_verified_before_checkpoint') is not True
                or ref.get('durable_before_checkpoint') is not True
                or not isinstance(ref.get('gzip_sha256'),str)
                or re.fullmatch('[0-9a-f]{64}',ref['gzip_sha256']) is None
                or any(type(ref.get(key)) is not int or ref[key]<0 for key in ('gzip_bytes','row_count','canonical_bytes'))):
            raise ValueError('Invalid complete domain manifest file proof')
    return files


def _verify_external_evidence(output,*,manifest=None,domain_known=None):
    """Reauthenticate old native witness bytes before opening any new writer."""
    output=Path(output).resolve();sidecars=set();domain={}
    if manifest is None and domain_known is not None:raise ValueError('Known domain catalog requires its bound complete manifest')
    complete=_checked_domain_manifest(manifest,domain_known) if manifest is not None else {}
    with (output/'evidence/blocks.jsonl').open('r',encoding='utf8') as index:
        for line in index:
            record=json.loads(line)
            with gzip.open(output/'evidence'/record['file'],'rt',encoding='utf8') as raw:
                block=json.load(raw)
            receipt=block.get('solver_row_sidecar')
            if receipt is not None:
                path=Path(receipt['path']).resolve()
                if (path.parent!=output or path.is_symlink() or receipt.get('status')!='completed'
                        or receipt.get('durable_and_stream_verified_before_block_ack') is not True
                        or not path.is_file() or _sha(path)!=receipt.get('gzip_sha256')):
                    raise ValueError('Native solver witness file/status/SHA changed')
                sidecars.add(path.name)
            references=block.get('domain_evidence_files')
            if not isinstance(references,dict):raise ValueError('Missing acknowledged domain witness receipts')
            for name,receipt in references.items():
                if name in domain:
                    if domain[name]!=receipt:raise ValueError('Conflicting domain witness receipts')
                    continue
                path=(output/name).resolve()
                if (not path.is_relative_to(output/'domain_evidence') or path.is_symlink()
                        or receipt.get('durable_and_verified_before_block_ack') is not True
                        or not path.is_file() or path.stat().st_size!=receipt.get('gzip_bytes')
                        or _sha(path)!=receipt.get('gzip_sha256')):
                    raise ValueError('Native domain witness bytes or SHA changed')
                domain[name]=receipt
    actual_sidecars={p.name for p in output.glob('solver_rows_block_*.jsonl.gz')}
    if actual_sidecars!=sidecars:raise ValueError('Unacknowledged extra/orphan native solver sidecar')
    for name,ref in complete.items():
        if name in domain:
            if any(domain[name].get(key)!=ref[key] for key in ('gzip_sha256','gzip_bytes','rows_sha256','row_count')):
                raise ValueError('Block and checkpoint domain witness proofs conflict')
            continue
        path=(output/name).resolve()
        if (not path.is_relative_to(output/'domain_evidence') or path.is_symlink() or not path.is_file()
                or path.stat().st_size!=ref['gzip_bytes'] or _sha(path)!=ref['gzip_sha256']):
            raise ValueError('Checkpoint known domain witness bytes or SHA changed')
    directory=output/'domain_evidence'
    actual_domain={p.relative_to(output).as_posix() for p in directory.rglob('*') if p.is_file()} if directory.exists() else set()
    if actual_domain!=set(domain)|set(complete):raise ValueError('Unacknowledged extra/orphan domain witness file')
    return dict(verified_solver_sidecars=len(sidecars),verified_domain_files=len(actual_domain),
        checkpoint_only_domain_files=len(set(complete)-set(domain)))


def _domain_identity(binding,case_id):
    return dict(case_id=case_id,source_sha256=digest(binding['source']),
        input_sha256=digest(binding['inputs']),checkpoint_binding_sha256=digest(binding))


def _domain_source_pin(binding):
    relative='tools/model_correctness_v23/evidence.py'
    return dict(path=str((ROOT/relative).resolve()),sha256=binding['source'][relative])


def _attach_indexed_domains(env,output,binding,case_id):
    original=env.domain_evidence
    if len(original._known)!=0:
        raise ValueError('Fresh indexed domain installation requires no earlier native blobs')
    if Path(original.directory).resolve()!=(Path(output)/'domain_evidence').resolve():
        raise ValueError('Native domain directory differs from the frozen producer')
    store=DomainStore.create(Path(output)/'domain_evidence',index_directory=Path(output)/'domain_index',
        identity=_domain_identity(binding,case_id),native_source_binding=_domain_source_pin(binding),
        cache_limit=64,enable_all_validation_rows=original.enable_all_validation_rows)
    env.domain_evidence=store
    log=getattr(getattr(env,'core',None),'solver_calls',None)
    if hasattr(log,'store'):log.store=store
    return store


def _restore_indexed_domains(output,state,binding,case_id):
    """Read back complete raw/proof ownership before a new solver-row writer.

    Native journal authentication is performed by reopen_journal first. This
    branch retains one decoded block and uses the external index for membership;
    it never constructs the full historical domain-reference dictionary.
    """
    output=Path(output).resolve()
    store=DomainStore.from_state(state['domain_store'],identity=_domain_identity(binding,case_id),
        native_source_binding=_domain_source_pin(binding),directory=output/'domain_evidence',
        index_directory=output/'domain_index',cache_limit=64,read_only=False,full=True)
    sidecars=set()
    try:
        with (output/'evidence/blocks.jsonl').open('r',encoding='utf8') as index:
            for line in index:
                record=json.loads(line)
                with gzip.open(output/'evidence'/record['file'],'rt',encoding='utf8') as stream:
                    block=json.load(stream)
                receipt=block.get('solver_row_sidecar')
                if receipt is not None:
                    path=Path(receipt['path']).resolve()
                    if (path.parent!=output or path.is_symlink() or receipt.get('status')!='completed'
                            or receipt.get('durable_and_stream_verified_before_block_ack') is not True
                            or not path.is_file() or path.stat().st_size!=receipt.get('gzip_bytes')
                            or _sha(path)!=receipt.get('gzip_sha256')):
                        raise ValueError('Native solver witness file/status/SHA changed')
                    sidecars.add(path.name)
                references=block.get('domain_evidence_files')
                if not isinstance(references,dict):raise ValueError('Missing acknowledged domain witness receipts')
                for name,receipt in references.items():
                    key=receipt.get('rows_sha256') if isinstance(receipt,dict) else None
                    if (not isinstance(key,str) or name!='domain_evidence/'+key+'.json.gz'
                            or key not in store._known):
                        raise ValueError('Acknowledged native domain reference is absent from its indexed proof')
                    ref=store._known[key];path=output/name
                    if (any(receipt.get(field)!=ref[field] for field in ('rows_sha256','row_count','gzip_bytes'))
                            or receipt.get('durable_and_verified_before_block_ack') is not True
                            or path.is_symlink() or not path.is_file()
                            or _sha(path)!=receipt.get('gzip_sha256')):
                        raise ValueError('Indexed and acknowledged raw domain witness proofs conflict')
                del block
        if {p.name for p in output.glob('solver_rows_block_*.jsonl.gz')}!=sidecars:
            raise ValueError('Unacknowledged extra/orphan native solver sidecar')
        return store
    except Exception:
        store.close();raise


def _get_location(env,location):
    owner,key=location.split('.')
    return getattr(env if owner=='env' else env.core,key)


def _set_location(env,location,value):
    owner,key=location.split('.')
    setattr(env if owner=='env' else env.core,key,value)


def _attach_histories(env,output,identity):
    directory=Path(output)/'histories';directory.mkdir(exist_ok=True)
    result={}
    for location in _HISTORY_LOCATIONS:
        owner,key=location.split('.')
        target=env if owner=='env' else env.core
        if not hasattr(target,key):continue
        existing=getattr(target,key)
        if type(existing) is not list:
            raise TypeError('Expected original plain history list at '+location)
        history=DiskHistory(directory/(location.replace('.','_')+'.sqlite'),
            identity=dict(identity,stream=location))
        history.extend(existing);setattr(target,key,history);result[location]=history
    for name in ('observations','operating_fees','sidecars'):
        result[name]=DiskHistory(directory/(name+'.sqlite'),identity=dict(identity,stream=name))
    for alias,target in (('segment_history','segments'),('event_history','events'),('solver_errors','errors')):
        setattr(env,alias,getattr(env.core,target))
    return result


def _commit_histories(histories):
    for history in histories.values():history.commit(keep_tail=1)


def _materialize_sidecars(history,output):
    """Keep original SQL rows; relocate only registered result I/O receipts."""
    values=[copy.deepcopy(value) for value in history]
    output=Path(output).resolve();path=output/'RECOVERY_ORIGIN.json'
    if not path.exists():return values
    from .recovery import _check
    origin=_check(_read(path))
    if origin.get('output')!=str(output):raise ValueError('Recovery result belongs to another branch')
    sources=set(origin['origin_outputs'])
    for receipt in values:
        original=Path(receipt['path'])
        if original.parent==output:continue
        if str(original.parent) not in sources or re.fullmatch(r'solver_rows_block_[0-9]{6}\.jsonl\.gz',original.name) is None:
            raise ValueError('Historical sidecar has no registered recovery I/O mapping')
        actual=output/original.name
        if (actual.is_symlink() or not actual.is_file() or actual.stat().st_size!=receipt['gzip_bytes']
                or _sha(actual)!=receipt['gzip_sha256']):raise ValueError('Relocated original sidecar bytes differ')
        receipt['path']=str(actual)
    return values


def _sum_fees(history):
    count=0
    for value in history:
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):return None
        count+=1
    return math.fsum(history) if count else None


def _new_sidecar(env,output,index):
    writer=getattr(env.core.solver_calls,'writer',None)
    if not isinstance(writer,SolverRowSidecar):return
    fresh=SolverRowSidecar(Path(output)/f'solver_rows_block_{index:06d}.jsonl.gz')
    env.core.solver_calls.writer=fresh;env._solver_row_sidecar=fresh


def _execute_loop(env,controller,scenario,journal,output,*,service_history,policy_history,
                  history_identity,histories=None,reset=True,loop_state=None,
                  accounting_parameters=None,tariffs_by_hour=None,on_reset=None,
                  stop_after_blocks=None,checkpoint_every_blocks=96,checkpoint_callback=None,stop_file=None,
                  compiled_capture_origin=None,recording_mode='full_v1',recovery_every_blocks=None):
    """Run the real reset/step interface; dependency arguments allow pure tests."""
    from tools.model_correctness_v23.policy import validate_action
    _recording_mode(recording_mode)
    if loop_state is not None:_check_recording_mode(loop_state,recording_mode)
    _limit(stop_after_blocks,'stop_after_blocks');_limit(checkpoint_every_blocks,'checkpoint_every_blocks',optional=False)
    _limit(recovery_every_blocks,'recovery_every_blocks')
    if loop_state is not None and loop_state.get('recovery_every_blocks')!=recovery_every_blocks:
        raise ValueError('Resumed recovery interval differs from its frozen loop state')
    inherited_stop=(loop_state or {}).get('stop_file')
    stop_file=_stop_file_path(stop_file if stop_file is not None else inherited_stop)
    if loop_state is not None and 'stop_file' in loop_state and (
            str(stop_file) if stop_file is not None else None)!=inherited_stop:
        raise ValueError('Resumed stop file differs from its registered checkpoint path')
    if loop_state is not None:
        inherited_frequency=loop_state.get('checkpoint_every_blocks')
        _limit(inherited_frequency,'restored checkpoint_every_blocks',optional=False)
        if inherited_frequency!=checkpoint_every_blocks:
            raise ValueError('Resumed checkpoint frequency differs from its registered loop state')
    _refuse_preexisting_stop(stop_file)
    output=Path(output);requested=float(scenario['duration_steps'])*float(scenario.get('step_minutes',15.))
    state=copy.deepcopy(loop_state or dict(committed_end_minute=0.,block_count=0,solver_base=0,
        event_cursor=0,error_cursor=0,reset_count=0,preparation_fee=None,initial_preparation=None,
        actual_prefix_parts=[]))
    state.update(stop_file=str(stop_file) if stop_file is not None else None,
        checkpoint_every_blocks=checkpoint_every_blocks,
        recording_mode=recording_mode,
        controlled_suspension_reason=None)
    state.setdefault('execution_map_retention',dict(acknowledged_block_count=0,last_receipt=None))
    if recovery_every_blocks is not None:state['recovery_every_blocks']=recovery_every_blocks
    committed=float(state['committed_end_minute']);failure=None;summary=None;status='failed'
    current=request=diagnostic=None;part=None;partial=None;processed=0;marker=None
    env._annual_service_history=service_history
    _apply_recording_mode(env,recording_mode)
    try:
        if reset:
            if committed!=0.:raise ValueError('Fresh reset cannot start after an observed prefix')
            env.reset(scenario=copy.deepcopy(scenario),seed=int(scenario.get('initialization_seed',20261023)))
            _apply_recording_mode(env,recording_mode)
            state['reset_count']+=1
            if float(env.core.time)!=0.:raise ValueError('Original initialization must end at actual minute zero')
            if on_reset is not None:on_reset(env)
            state['initial_preparation']={name:getattr(getattr(env.core,name,None),
                'initial_charged_input_kWh',None) for name in ('ups','cold_store')}
            attached=_attach_histories(env,output,history_identity)
            if histories is None:histories=attached
            else:histories.update(attached)
        else:
            if histories is None:raise ValueError('Resume requires verified complete disk histories')
            if float(env.core.time)!=committed:raise ValueError('Restored physical and committed clocks differ')
            _verify_prefix_parts(output,state['actual_prefix_parts'])
        def actual_segment(segment):
            if part is None:raise RuntimeError('Actual prefix sink is unavailable')
            service_history.append(segment);part.append(segment)
        env.actual_segment_observer=actual_segment
        while committed<requested:
            if part is None:part=_PrefixPart(output,len(state['actual_prefix_parts']))
            start=committed;segment_cursor=len(env.core.segments);step_error=None;info={};done=truncated=False
            current=request=diagnostic=None
            try:
                current=normalize_json(_snapshot(env))
                if float(current['decision_time_min'])!=start:raise ValueError('Current legal observation clock differs from committed boundary')
                histories['observations'].append(copy.deepcopy(current))
                request,diagnostic=policy_history.decide(copy.deepcopy(current));validate_action(request)
                from .compiled_capture import capture_scope
                with capture_scope(compiled_capture_origin) if compiled_capture_origin is not None else nullcontext():
                    returned=env.step(request)
                if not isinstance(returned,tuple) or len(returned)!=5:raise ValueError('Original five-field step interface required')
                _,_,done,truncated,info=returned;segments=info['segments']
            except Exception as exc:
                step_error=_error(exc);segments=env.core.segments[segment_cursor:]
            calls=list(env.core.solver_calls)
            events=env.core.events[state['event_cursor']:]
            errors=env.core.errors[state['error_cursor']:]
            try:terminal=_terminal(env,float(env.core.time))
            except Exception as exc:
                terminal=dict(completeness='unknown_terminal_export_error',error=_error(exc))
                step_error=step_error or terminal['error']
            generated=[row.get('command') for row in events if row.get('kind')=='command_generated']
            partial=dict(start_minute=start,end_minute=segments[-1]['end_minute'] if segments else start,
                segments=segments,solver_calls=calls,events=events,solver_errors=errors,
                solver_index_base=state['solver_base'],solver_call_local_start_index=0,
                solver_index_semantics='block local; combine with solver_index_base',
                decision=dict(current_observation=current,requested_command=request,
                    command_generated=generated[-1] if generated else None,diagnostic=diagnostic),
                terminal=terminal,error=step_error,initial_preparation=(
                    copy.deepcopy(state['initial_preparation']) if start==0. else None),
                initial_preparation_added_again=False)
            if not segments:
                failure=step_error or dict(type='MissingActualSegments',message='No actual interval after a decision')
                partial['error']=failure
                _write_new(output/f'FAILURE_CONTEXT_{state["block_count"]:06d}.json.gz',partial,compressed=True)
                break
            end=float(segments[-1]['end_minute'])
            if end!=float(env.core.time):raise ValueError('Actual segment endpoint differs from live physical clock')
            try:
                partial.update(_block_evaluation(normalize_json(segments),start,end,
                    preparation=state['initial_preparation'] if start==0. else {'ups':0.,'cold_store':0.},
                    parameters=accounting_parameters or {},tariffs=tariffs_by_hour or {}))
            except Exception as exc:
                step_error=step_error or _error(exc);partial.update(error=step_error,economics=None,
                    native_evaluation_error=_error(exc),native_material_intervals=None,native_recovery_metrics=None)
            try:
                sidecar,domain=_seal_solver_evidence(env,partial,output,call_cursor=0)
                partial.update(solver_row_sidecar=sidecar,domain_evidence_files=domain)
                if isinstance(getattr(env,'domain_evidence',None),DomainStore):
                    # Proof/index durability is established even for a failed
                    # physical prefix before that prefix can be acknowledged.
                    partial['domain_store_acknowledgement']=env.domain_evidence.commit()
                receipt=journal.append(normalize_json(partial))
            except Exception as exc:
                partial['journal_append_error']=_error(exc)
                _write_new(output/f'REJECTED_BLOCK_{state["block_count"]:06d}.json.gz',partial,compressed=True)
                failure=partial['journal_append_error'];break
            committed=end;state['committed_end_minute']=end;state['block_count']=receipt['index']+1
            state['solver_base']+=len(calls);state['event_cursor']=len(env.core.events)
            state['error_cursor']=len(env.core.errors)
            economics=partial.get('economics') or {}
            histories['operating_fees'].append(economics.get('observed_operating_fee_USD2001'))
            if start==0.:state['preparation_fee']=economics.get('initial_preparation_fee_USD2001')
            if sidecar is not None:histories['sidecars'].append(sidecar)
            # No evidence is released until the original raw/index acknowledgement.
            env.core.solver_calls.clear()
            # Native error records bind exact disk-prefix versions and contain
            # complete uncommitted tails. A failed acknowledged block must not
            # advance those databases; the raw block retains the error itself.
            if step_error is None:
                _commit_histories(histories)
                retention=compact_execution_maps(env.core,committed_end_minute=committed)
                state['execution_map_retention']['acknowledged_block_count']+=1
                state['execution_map_retention']['last_receipt']=retention
            processed+=1;partial=None
            _append(output/'PROGRESS.jsonl',dict(block_count=state['block_count'],
                recording_mode=recording_mode,
                observed_until_minute=committed,requested_end_minute=requested,
                retained_segments=env.core.segments.retained_count,
                legal_observation_count=len(histories['observations']),
                execution_map_compaction=(retention if step_error is None else None)))
            if step_error is not None:failure=step_error;break
            if (done or truncated) and committed!=requested:
                failure=dict(type='EarlyTermination',message='Executor stopped before registered horizon');break
            manual_stop=_stop_requested(stop_file)
            if committed==requested and not manual_stop:status='completed';break
            registered_stop=stop_after_blocks is not None and processed>=stop_after_blocks
            stop=manual_stop or registered_stop
            periodic=state['block_count']%checkpoint_every_blocks==0
            recovery_due=(recovery_every_blocks is not None and state['block_count']%recovery_every_blocks==0)
            if stop or periodic or recovery_due:
                reason=('manual_stop_file' if manual_stop else 'registered_block_limit'
                    if registered_stop else 'periodic_recovery_snapshot' if recovery_due else 'periodic_checkpoint')
                state.update(checkpoint_reason=reason,controlled_suspension_reason=reason if stop else None)
                state['actual_prefix_parts'].append(part.close());part=None
                if checkpoint_callback is None:raise ValueError('A real checkpoint callback is required for controlled suspension/checkpointing')
                marker=checkpoint_callback(env=env,controller=controller,loop_state=copy.deepcopy(state),
                    histories=histories,service_history=service_history,policy_history=policy_history,journal=journal)
                if not isinstance(marker,dict) or not marker.get('publication_path'):
                    raise ValueError('Checkpoint callback did not return a published marker')
                if stop:status='suspended';break
            _new_sidecar(env,output,state['block_count'])
    except Exception as exc:
        failure=_error(exc)
        if getattr(env,'core',None) is not None:
            context=partial or dict(start_minute=committed,end_minute=float(env.core.time),
                segments=env.core.segments[len(env.core.segments):],solver_calls=list(env.core.solver_calls),
                decision=dict(current_observation=current,requested_command=request,diagnostic=diagnostic),error=failure)
            _write_new(output/f'FAILURE_CONTEXT_OUTER_{state["block_count"]:06d}.json.gz',context,compressed=True)
    finally:
        env.actual_segment_observer=None
        if part is not None:
            try:state['actual_prefix_parts'].append(part.close())
            except Exception as exc:failure=failure or _error(exc)
        writer=getattr(getattr(getattr(env,'core',None),'solver_calls',None),'writer',None)
        if isinstance(writer,SolverRowSidecar) and not writer.closed:
            try:
                if failure is not None:writer.body_exception=dict(type='AnnualRunFailed',message=str(failure)[:1000])
                writer.close()
            except Exception as exc:failure=failure or _error(exc)
    if failure is not None:status='failed'
    elif committed==requested and status!='suspended':status='completed'
    if status!='suspended':
        try:summary=journal.close(status=status,expected_end_minute=requested,error=failure)
        except Exception as exc:
            failure=failure or _error(exc);status='failed'
            _write_new(output/f'JOURNAL_CLOSE_ERROR_{state["block_count"]:06d}.json',_error(exc))
    fees=_sum_fees(histories['operating_fees']) if histories is not None and 'operating_fees' in histories else None
    preparation=state['preparation_fee']
    # Explicit one-time result metadata materialization. Native steam readers
    # bind each exact block receipt against this complete producer list.
    sidecars=_materialize_sidecars(histories['sidecars'],output) if histories is not None and 'sidecars' in histories else []
    if isinstance(writer,SolverRowSidecar) and writer.closed:
        active=writer.receipt()
        if not any(row.get('path')==active.get('path') for row in sidecars):sidecars.append(active)
    return dict(status=status,error=failure,summary=summary,loop_state=state,recording_mode=recording_mode,
        stop_file=state['stop_file'],controlled_suspension_reason=state['controlled_suspension_reason'],
        checkpoint_every_blocks=state['checkpoint_every_blocks'],
        checkpoint_reason=state.get('checkpoint_reason'),
        execution_map_retention=copy.deepcopy(state['execution_map_retention']),
        log_migrations=dict(final_actual_primal_witness='hospital_ies.annual_program.final_actual_primal_witness.v1',
            failure_history='annual_native_failure_inline_history_to_exact_disk_reference.v1',
            failure_history_references_are_inline_lists=False,
            execution_maps='hospital_ies.annual_program.execution_map_compaction.v1'),
        solver_row_sidecars=sidecars,
        sidecar_receipt_materialization='complete prefix plus current closed writer; transient result metadata',
        checkpoint=marker,observed_until_minute=committed,actual_core_until_minute=float(getattr(getattr(env,'core',None),'time',0.)),
        requested_end_minute=requested,unknown_tail_minutes=requested-committed,
        reset_count=state['reset_count'],actual_episode_attempts=int(reset),
        legal_observation_count=len(histories['observations']) if histories is not None and 'observations' in histories else 0,
        economics=dict(observed_operating_fee_USD2001=fees,initial_preparation_fee_USD2001=preparation,
            actual_fee_USD2001=(fees+preparation if fees is not None and preparation is not None else None),
            unobserved_tail_fee_USD2001=None,price_year=2001,currency='USD'),
        native_cross_block_recovery=None,chemical_diesel_fuel_liters=None,DC_energy_kWh=None,
        domain_store_adapter_receipt=(env.domain_evidence.adapter_receipt()
            if isinstance(getattr(env,'domain_evidence',None),DomainStore) else None),
        bounded_all_live_state=False,unbounded_state_remaining=[
            'result.solver_row_sidecars','journal_and_checkpoint_prefix_metadata']+(
            [] if isinstance(getattr(env,'domain_evidence',None),DomainStore) else
            ['domain_evidence._known','checkpoint.domain_manifest']),
        annual_resource_admission_qualified=False)


def _configuration(case,registry):
    from tools.model_correctness_v23 import policy
    from tools.model_closure_v22.validation import runtime_policy
    from tools.model_correction_v25.validation import resolve_operating_rule
    config=copy.deepcopy(registry['hardware_profiles'][case['configuration']])
    for key in ('model_closure_v22','model_correctness_v23','engineering_completion_v24','model_correction_v25'):
        config[key]=copy.deepcopy(case.get(key,registry[key]))
    config['model_closure_v22']['station']['boiler_restart_minutes']=case['annual_readiness']['parameters']['boiler_restart_minutes']
    rule_id=case.get('operating_rule_id',registry.get('default_operating_rule'))
    if case['annual_readiness']['method'].endswith('_ready'):rule=dict(name='sustained_critical',design_minutes=60.)
    else:rule_id,rule=resolve_operating_rule(registry,case)
    return dict(model_config=config,control_rule_id=rule_id,operating_rule=rule,
        policy=runtime_policy(case,registry,policy))


def _build_runtime(case,configuration,context,output,writer):
    from tools.annual_readiness.environment import with_readiness
    from tools.model_correction_v25.environment import MODEL_ID as CONSTRUCTOR_MODEL_ID
    meta=case['annual_readiness'];spec=configuration['policy']
    adapter=with_readiness(meta['method'],requalification_minutes=meta['parameters']['requalification_minutes'],
        overlay_spec=case.get('v27_load_overlay'),thermal_profile=case['v28_thermal_profile'])
    env=adapter.OperatingHospitalIESEnv(ROOT,allowed_hours=required_hours(case),sealed_days=SEALED_DAYS,
        model_id=CONSTRUCTOR_MODEL_ID,model_config=copy.deepcopy(configuration['model_config']),
        operating_rule=copy.deepcopy(configuration['operating_rule']),solver_row_sidecar=writer,
        solver_evidence_directory=Path(output)/'domain_evidence',enable_all_validation_rows=True,
        annual_program_data_context=context)
    env.engineering_core_type=type('AnnualProgramCore',(AnnualSourceMixin,env.engineering_core_type),{})
    if env.control_rule_id!=configuration['control_rule_id']:raise ValueError('Actual annual operating rule differs from registration')
    controller=adapter.controller_factory(spec['alias'],config=spec['config'],plant=env.plant,
        policy_id=meta['method']+'_annual_program_20261030')
    return env,controller


def _activate_restored_inputs(env):
    from tools.model_review_v27.environment import apply_cooling_profile
    from tools.model_review_v28.thermal_adapter import apply_thermal_profile
    from tools.model_review_v28.environment import activate_boundary
    if env._v27_profile_frames is not None:apply_cooling_profile(env,**env._v27_profile_frames)
    if env.v28_thermal_profile is not None:apply_thermal_profile(env,env.v28_thermal_profile)
    activate_boundary(env)


def _partition(env,controller,histories):
    objects=dict(env=env,core=env.core,controller=controller)
    plan={owner:{key:'state' for key in vars(value)} for owner,value in objects.items()}
    for key in _REBUILD_ENV:
        if key in vars(env):plan['env'][key]='rebuild'
    for key in ('_solver_row_sidecar','domain_evidence','actual_segment_observer','_annual_service_history'):
        if key in vars(env):plan['env'][key]='external'
    plan['env']['core']='alias:core';plan['core']['env']='alias:env'
    plan['core']['solver_calls']='external'
    for location in histories:
        if location in _HISTORY_LOCATIONS:
            owner,key=location.split('.');plan[owner][key]='external'
    for key,target in (('segment_history','segments'),('event_history','events'),('solver_errors','errors')):
        if key in vars(env):plan['env'][key]='alias:core.'+target
    if 'mailbox' in vars(controller):plan['controller']['mailbox']='inplace'
    if 'plant' in vars(controller):plan['controller']['plant']='rebuild'
    return plan


def _solver_log(name,writer,store):
    from tools.sci_engineering.solver_log import SolverCallLog
    from tools.model_correctness_v23.evidence import _AllRowsOrderedLog,_AllRowsLog
    classes={c.__module__+'.'+c.__qualname__:c for c in (list,SolverCallLog,_AllRowsOrderedLog,_AllRowsLog)}
    if name not in classes:raise ValueError('Unregistered solver log behavior during annual restore')
    cls=classes[name];value=list.__new__(cls);list.__init__(value)
    if issubclass(cls,SolverCallLog):value.writer=writer
    if cls in (_AllRowsOrderedLog,_AllRowsLog):value.store=store
    return value


def _checkpoint_callback(output,binding,*,recovery_archive=None):
    def publish(**context):
        env=context['env'];state=copy.deepcopy(context['loop_state'])
        if recovery_archive is None:receipt=checkpoint_receipt(context['journal'])
        else:
            from .journal import recovery_receipt
            receipt=recovery_receipt(context['journal'])
        if not isinstance(env.domain_evidence,DomainStore):
            raise ValueError('Indexed domain store required for new annual snapshots')
        domains=env.domain_evidence.to_state()
        state.update(history_states={name:value.to_state() for name,value in context['histories'].items()},
            service_history=context['service_history'].to_state(),policy_history=context['policy_history'].to_state(),
            journal_receipt=receipt,domain_store=domains,
            solver_log_type=type(env.core.solver_calls).__module__+'.'+type(env.core.solver_calls).__qualname__)
        document=capture_checkpoint(env,context['controller'],binding=binding,
            field_plan=_partition(env,context['controller'],context['histories']),runner_state=state)
        if recovery_archive is not None:return recovery_archive.capture(document,context['journal'])
        directory=Path(output)/'checkpoints';directory.mkdir(exist_ok=True)
        path=directory/f'state_{state["block_count"]:06d}.json'
        save_checkpoint(document,path)
        return publish_checkpoint(path,receipt,directory/f'CHECKPOINT_{state["block_count"]:06d}.json')
    return publish


def _load_publication(path,output,binding):
    path=Path(path).resolve();output=Path(output).resolve()
    if not path.is_relative_to(output/'checkpoints') or path.is_symlink():raise ValueError('Checkpoint marker must belong to this producer')
    marker=_read(path)
    if marker.get('publication_path')!=str(path) or marker.get('digest')!=_record_digest(marker):
        raise ValueError('Checkpoint marker path or integrity mismatch')
    state_path=Path(marker['checkpoint_path']).resolve()
    if not state_path.is_relative_to(output/'checkpoints') or _sha(state_path)!=marker.get('checkpoint_sha256'):
        raise ValueError('Published checkpoint bytes differ')
    if state_path.stat().st_size!=marker.get('checkpoint_bytes'):raise ValueError('Published checkpoint size differs')
    document=load_checkpoint(state_path,expected_binding=binding)
    state=_decode(document['runner_state'])
    if document['content_sha256']!=marker.get('checkpoint_content_sha256') or state['journal_receipt']!=marker['journal_receipt']:
        raise ValueError('Published checkpoint and journal receipts differ')
    return document,state


def _restore_runtime(document,state,env,controller,writer,output,identity,domain_store=None):
    if not isinstance(domain_store,DomainStore):raise ValueError('Verified indexed domain store required for resume')
    env.domain_evidence=domain_store
    _activate_restored_inputs(env)
    histories={}
    try:
        for name,saved in state['history_states'].items():
            if name not in set(_HISTORY_LOCATIONS)|{'observations','operating_fees','sidecars'}:
                raise ValueError('Unexpected restored history stream')
            expected=Path(output)/'histories'/(name.replace('.','_')+'.sqlite')
            if Path(saved['path']).resolve()!=expected.resolve():raise ValueError('History path escaped the bound producer')
            histories[name]=DiskHistory.from_state(saved,path=expected,identity=dict(identity,stream=name))
        fields=_decode(document['state']['env'])
        service=ServiceHistoryAccumulator.from_state(state['service_history'],config=fields['config'])
        resources={'env._solver_row_sidecar':writer,'env.domain_evidence':env.domain_evidence,
            'env.actual_segment_observer':lambda segment:None,'env._annual_service_history':service,
            'core.solver_calls':_solver_log(state['solver_log_type'],writer,env.domain_evidence)}
        resources.update({name:value for name,value in histories.items() if name in _HISTORY_LOCATIONS})
        rebuilt={}
        for owner,entries in document['coverage'].items():
            target=env if owner=='env' else controller if owner=='controller' else None
            for key,entry in entries.items():
                if entry['kind']=='rebuild':
                    if target is None:raise ValueError('No supported core rebuilt data field')
                    rebuilt[owner+'.'+key]=getattr(target,key)
        restored=restore_checkpoint(document,template_env=env,template_controller=controller,
            expected_binding=document['binding'],rebuilt_fields=rebuilt,external_fields=resources)
        env,controller=restored['env'],restored['controller']
        policy=ExactPolicyHistory.from_state(state['policy_history'],controller=controller)
        return env,controller,histories,service,policy
    except Exception:
        for history in histories.values():history.close()
        raise


def run_case(case,output,*,approval_receipt,stop_after_blocks=None,resume_checkpoint=None,checkpoint_every_blocks=96,stop_file=None,
             recording_mode='full_v1',recovery_every_blocks=None,recovery_vault=None,accepted_result_cache=False):
    """Run one registered case; resume only an exact published quiescent state."""
    _recording_mode(recording_mode)
    if type(accepted_result_cache) is not bool:raise ValueError('Cache setting must be an explicit boolean')
    _limit(stop_after_blocks,'stop_after_blocks');_limit(checkpoint_every_blocks,'checkpoint_every_blocks',optional=False)
    recovery_every_blocks,recovery_vault=_recovery_options(recovery_every_blocks,recovery_vault)
    stop_file=_stop_file_path(stop_file);_refuse_preexisting_stop(stop_file)
    case=validate_case(case);registry=load_registry();configuration=_configuration(case,registry)
    output=Path(output).resolve()
    if os.name=='nt' and output.drive.upper()!='E:':raise ValueError('Annual scientific output must remain on E')
    approval=_read(approval_receipt) if isinstance(approval_receipt,(str,Path)) else copy.deepcopy(approval_receipt)
    invocation=0;fresh=resume_checkpoint is None
    if fresh:
        output=legacy._checked_output(output);output.mkdir(parents=True,exist_ok=False)
        (output/'temp').mkdir()
        freeze=create_runtime_freeze(ROOT,approval_receipt=approval,case=case,configuration=configuration)
        orchestration=_orchestration_freeze(stop_file,checkpoint_every_blocks,recording_mode,
            recovery_every_blocks=recovery_every_blocks,recovery_vault=recovery_vault,accepted_result_cache=accepted_result_cache)
        context=dict(approval_receipt=approval,runtime_freeze_receipt=freeze,case=case,configuration=configuration)
        bindings=_bindings()
        _write_new(output/'CASE.json',dict(case=case,configuration=configuration))
        _write_new(output/'BINDINGS.json',bindings);_write_new(output/'ANNUAL_ACCESS_CONTEXT.json',context)
        _write_new(output/'ORCHESTRATION_FREEZE.json',orchestration)
    else:
        saved=_read(output/'CASE.json');bindings=_read(output/'BINDINGS.json')
        context=_read(output/'ANNUAL_ACCESS_CONTEXT.json')
        if saved!=dict(case=case,configuration=configuration) or context['approval_receipt']!=approval:
            raise ValueError('Resumed case/configuration/approval differs from frozen producer')
        orchestration=_read(output/'ORCHESTRATION_FREEZE.json')
        inherited=_stop_file_path(orchestration.get('stop_file'))
        if stop_file is None:stop_file=inherited
        if _orchestration_freeze(stop_file,checkpoint_every_blocks,recording_mode,
                recovery_every_blocks=recovery_every_blocks,recovery_vault=recovery_vault,
                accepted_result_cache=accepted_result_cache)!=orchestration:
            raise ValueError('Resumed recording_mode/stop-file/checkpoint-frequency orchestration differs from its frozen registration')
        _refuse_preexisting_stop(stop_file)
        last_invocation=-1
        with (output/'ATTEMPTS.jsonl').open('r',encoding='utf8') as attempts:
            for line in attempts:
                if not line.endswith('\n'):raise ValueError('Incomplete annual invocation ledger')
                row=json.loads(line);number=row.get('invocation')
                if type(number) is not int or number<0:raise ValueError('Invalid actual invocation identity')
                _check_recording_mode(row,recording_mode)
                last_invocation=max(last_invocation,number)
        invocation=last_invocation+1
    validate_annual_access(ROOT,required_hours(case),SEALED_DAYS,context)
    if bindings!=_bindings():raise ValueError('Source/input/HEAD changed before annual construction')
    binding=dict(source=bindings['source'],inputs=bindings['inputs'],
        config=dict(case=case,configuration=configuration,runtime_freeze=context['runtime_freeze_receipt']['signature'],
            orchestration_freeze=orchestration['signature']),
        git_commit=bindings['git_commit'])
    identity=dict(case_id=case['id'],model_id=MODEL_ID,git_commit=bindings['git_commit'],
        source_sha256=digest(bindings['source']),input_sha256=digest(bindings['inputs']),case_sha256=digest(case))
    history_identity=dict(case_id=case['id'],checkpoint_binding_sha256=digest(binding))
    # Verify the published loop's mode before any new invocation record, writer,
    # runtime or indexed-store handle. Old mode-less states mean full_v1 only.
    if fresh:state=None;document=None;index=0
    else:
        document,state=_load_publication(resume_checkpoint,output,binding);index=state['block_count']
        _check_recording_mode(state,recording_mode)
    writer=env=journal=domain_store=recovery_archive=None;histories={} if fresh else None;result=None;started=time.perf_counter();loop_entered=False
    _append(output/'ATTEMPTS.jsonl',dict(event='started' if fresh else 'resume_started',
        recording_mode=recording_mode,
        invocation=invocation,case_id=case['id'],resume_checkpoint=str(resume_checkpoint) if resume_checkpoint else None,
        stop_file=str(stop_file) if stop_file is not None else None,stop_after_blocks=stop_after_blocks,
        checkpoint_every_blocks=checkpoint_every_blocks))
    try:
        if not fresh:
            journal=reopen_journal(output/'evidence',state['journal_receipt'],identity)
            domain_store=_restore_indexed_domains(output,state,binding,case['id'])
            domain_store.engineering_summary=recording_mode=='engineering_v1'
            _verify_prefix_parts(output,state['actual_prefix_parts'])
        _refuse_preexisting_stop(stop_file)
        writer=SolverRowSidecar(output/f'solver_rows_block_{index:06d}.jsonl.gz')
        env,controller=_build_runtime(case,configuration,context,output,writer)
        _apply_recording_mode(env,recording_mode)
        if fresh:
            domain_store=_attach_indexed_domains(env,output,binding,case['id'])
            service=ServiceHistoryAccumulator(env.config);policy=ExactPolicyHistory(controller)
            journal=BlockJournal(output/'evidence',identity=identity)
        else:
            env,controller,histories,service,policy=_restore_runtime(document,state,env,controller,
                writer,output,history_identity,domain_store=domain_store)
        _apply_recording_mode(env,recording_mode)
        from tools.model_review_v30.evaluation import _load_frozen_accounting
        support=sorted(required_hours(case))
        parameters,tariffs,accounting_sources=_load_frozen_accounting(ROOT,bindings,[{'hour_of_year':h} for h in support])
        parameters['cold_pump_kW_per_kW']=env.sci_config.get('cold_pump_kW_per_kW')
        def resolved(actual):
            _write_new(output/'RESOLVED_CASE.json',dict(case=case,model_config=copy.deepcopy(actual.operating_model_config),
                control_rule=copy.deepcopy(actual.control_rule),allowed_hours=support,
                policy=dict(policy_id=controller.policy_id,config=copy.deepcopy(controller.config)),
                input_access_receipt=copy.deepcopy(actual.input_access_receipt)))
        from . import flow_source_contracts as flow_contracts
        from .witness import compiled_origin
        native_sources={name:bindings['source'].get(name) for name in flow_contracts.LEGACY_NATIVE_SOURCE_CONTRACT}
        capture_origin=(compiled_origin(ROOT,bindings['source'])
            if flow_contracts.match_native_source_profile(native_sources)==flow_contracts.CAPTURE_PROFILE else None)
        if recovery_every_blocks is not None:
            from .recovery import RecoveryArchive
            recovery_archive=RecoveryArchive(recovery_vault,output,binding)
        with legacy._local_temp(output):
            loop_entered=True
            with ExitStack() as numerical_context:
                cache_holder=[];reset_callback=resolved if fresh else None
                if accepted_result_cache:
                    from hospital_ies.solver_backend import accepted_result_cache_scope
                    def start_numerical_cache():
                        cache_holder.append(numerical_context.enter_context(
                            accepted_result_cache_scope(max_entries=2048,max_bytes=33554432)))
                    if fresh:
                        def reset_and_start_cache(value):
                            resolved(value)
                            start_numerical_cache()
                        reset_callback=reset_and_start_cache
                    else:start_numerical_cache()
                result=_execute_loop(env,controller,case['scenario'],journal,output,service_history=service,
                    policy_history=policy,history_identity=history_identity,histories=histories,reset=fresh,loop_state=state,
                    accounting_parameters=parameters,tariffs_by_hour=tariffs,on_reset=reset_callback,
                    stop_after_blocks=stop_after_blocks,checkpoint_every_blocks=checkpoint_every_blocks,stop_file=stop_file,
                    checkpoint_callback=_checkpoint_callback(output,binding,recovery_archive=recovery_archive),compiled_capture_origin=capture_origin,
                    recording_mode=recording_mode,recovery_every_blocks=recovery_every_blocks)
            if cache_holder:result['accepted_result_cache']=cache_holder[-1].stats()
        result.update(accounting_input_bindings=accounting_sources)
    except Exception as exc:
        not_started=isinstance(exc,StopRequestedBeforeStart)
        result=dict(status='not_started' if not_started else 'failed',error=_error(exc),
            actual_episode_attempts=0 if not_started else int(fresh and getattr(env,'core',None) is not None),
            observed_until_minute=float(getattr(journal,'_end',0.)),requested_end_minute=case['scenario']['duration_steps']*15.)
        result['unknown_tail_minutes']=result['requested_end_minute']-result['observed_until_minute']
        if not not_started and journal is not None and not journal._closed and (fresh or loop_entered):
            try:result['summary']=journal.close(status='failed',expected_end_minute=result['requested_end_minute'],error=result['error'])
            except Exception as close:result['journal_close_error']=_error(close)
    finally:
        if recovery_archive is not None:recovery_archive.close()
        # The real constructor has no physical core until reset. If admission
        # fails just after store attachment, retain/close that attached resource
        # even though the installing helper did not return to its assignment.
        if domain_store is None and isinstance(getattr(env,'domain_evidence',None),DomainStore):
            domain_store=env.domain_evidence
        if domain_store is not None:
            try:
                close_receipt=domain_store.close()
                close_path=output/f'DOMAIN_STORE_CLOSE_{invocation:06d}.json.gz'
                _write_new(close_path,close_receipt,compressed=True)
                if result is not None:
                    result['domain_store_close_receipt']=dict(file=close_path.name,
                        sha256=_sha(close_path),bytes=close_path.stat().st_size,
                        pending_evidence_preserved_outside_result=True)
            except Exception as exc:
                if result is not None:result.update(status='failed',domain_store_close_error=_error(exc))
        if writer is not None and not writer.closed:
            try:writer.close()
            except Exception as exc:
                if result is not None:result.update(status='failed',writer_close_error=_error(exc))
        # Fresh histories were installed inside the loop and remain reachable
        # through the returned loop's explicitly tracked history locations.
        if env is not None:
            for location in _HISTORY_LOCATIONS:
                try:value=_get_location(env,location)
                except AttributeError:continue
                if isinstance(value,DiskHistory):value.close()
        if histories is not None:
            for value in histories.values():value.close()
        if result is not None:
            result.update(configuration=case.get('configuration'),
                recording_mode=recording_mode,
                stop_file=str(stop_file) if stop_file is not None else None,
                orchestration_freeze_receipt=copy.deepcopy(orchestration),
                annual_readiness=copy.deepcopy(case.get('annual_readiness')),
                annual_program=copy.deepcopy(case.get('annual_program')),
                resolved_model_config=copy.deepcopy(getattr(env,'operating_model_config',None)))
            if writer is not None and writer.closed:
                receipt=writer.receipt();sidecars=result.setdefault('solver_row_sidecars',[])
                if not any(row.get('path')==receipt.get('path') for row in sidecars):sidecars.append(receipt)
            unchanged=bindings==_bindings()
            if not unchanged:result.update(status='failed',source_drift_error='Source/input/HEAD changed during actual invocation')
            result.update(schema=SCHEMA,case_id=case['id'],model_id=MODEL_ID,git_commit=bindings['git_commit'],
                source_sha256=digest(bindings['source']),input_sha256=bindings['inputs'],case_sha256=digest(case),
                source_and_inputs_unchanged=unchanged,elapsed_seconds=time.perf_counter()-started,
                invocation=invocation,actual_invocation_count=1,annual_study_is_blind=False)
            _write_new(output/f'RESULT_part_{invocation:06d}.json',result)
            if result['status']!='suspended' and (fresh or loop_entered):_write_new(output/'RESULT.json',result)
            _append(output/'ATTEMPTS.jsonl',dict(event='finished',invocation=invocation,status=result['status'],
                recording_mode=recording_mode,
                observed_until_minute=result['observed_until_minute'],stop_file=result['stop_file'],
                controlled_suspension_reason=result.get('controlled_suspension_reason')))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--approval-receipt',type=Path,required=True)
    parser.add_argument('--stop-after-blocks',type=int)
    parser.add_argument('--stop-file',type=Path,help='Registered E-drive external stop flag; existence only, never removed by the producer')
    parser.add_argument('--checkpoint-every-blocks',type=int,default=96)
    parser.add_argument('--resume-checkpoint',type=Path)
    parser.add_argument('--recording-mode',choices=_RECORDING_MODES,default='full_v1')
    parser.add_argument('--recovery-every-blocks',type=int,help='Explicit incremental post-ACK recovery interval')
    parser.add_argument('--recovery-vault',type=Path,help='Separate E-drive immutable recovery archive')
    parser.add_argument('--accepted-result-cache',action='store_true',help='Reuse accepted identical numerical problems with fresh original-constraint validation')
    args=parser.parse_args(argv)
    result=run_case(_read(args.case_file),args.output,approval_receipt=args.approval_receipt,
        stop_after_blocks=args.stop_after_blocks,resume_checkpoint=args.resume_checkpoint,
        checkpoint_every_blocks=args.checkpoint_every_blocks,stop_file=args.stop_file,recording_mode=args.recording_mode,
        recovery_every_blocks=args.recovery_every_blocks,recovery_vault=args.recovery_vault,
        accepted_result_cache=args.accepted_result_cache)
    print(json.dumps({key:result.get(key) for key in ('status','observed_until_minute','unknown_tail_minutes','checkpoint')}))
    return 0 if result['status'] in ('completed','suspended') else 1


if __name__=='__main__':raise SystemExit(main())
