from pathlib import Path
import copy
import gzip
import hashlib
import json
import math
import pytest

import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.sci_engineering.solver_log import SolverRowSidecar, SolverCallLog, restore_solver_calls, normalize_json


def call():
    rows=[{'index':0,'name':'临床电平衡','lower':-math.inf,'upper':0.,'activity':0.,'violation':0.},
          {'index':1,'name':'P0 cold lock','lower':0.,'upper':math.inf,'activity':2.5,'violation':0.}]
    return {'time_minute':15.,'phase':'sci_upper_planning','status':'success',
        'solver_backend_layers':[{'rank':0,'status':0,'validation':{'max_original_violation':0.,'rows':rows},
            'solver_validation':{'max_original_violation':0.,'rows':copy.deepcopy(rows)},
            'attempts':[{'status':0,'accepted':True,'validation':{'max_original_violation':0.,'rows':copy.deepcopy(rows)}}]}]}


def canonical(x):return json.dumps(normalize_json(x),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf8')


def test_success_rows_reconstruct_byte_identically_and_original_unchanged(tmp_path):
    original=call();before=copy.deepcopy(original);path=tmp_path/'solver_rows.jsonl.gz'
    with SolverRowSidecar(path) as writer:
        log=SolverCallLog(writer=writer);log.append(original)
        assert original==before
        assert 'rows' not in log[0]['solver_backend_layers'][0]['attempts'][0]['validation']
        assert log[0]['solver_backend_layers'][0]['validation']['max_original_violation']==0.
        assert writer.receipt()['entry_count']==3
    restored=restore_solver_calls(log,path)
    assert canonical(restored)==canonical([original])
    assert writer.receipt()['status']=='completed'
    assert writer.receipt()['closed'] is True
    assert writer.receipt()['entry_count']==3
    assert writer.receipt()['row_count']==6
    assert writer.receipt()['gzip_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert original==before
    for entry in map(json.loads,gzip.open(path,'rt',encoding='utf8')):
        assert hashlib.sha256(canonical(entry['rows'])).hexdigest()==entry['rows_sha256']


@pytest.mark.parametrize('change', ['call_failed','layer_failed','attempt_rejected','attempt_failed','attempt_unknown'])
def test_non_success_evidence_stays_inline(tmp_path,change):
    original=call();layer=original['solver_backend_layers'][0]
    # Isolate the attempt path; direct layer validations have separate success status.
    layer.pop('validation');layer.pop('solver_validation')
    if change=='call_failed':original['status']='error'
    if change=='layer_failed':layer['status']=2
    if change=='attempt_rejected':layer['attempts'][0]['accepted']=False
    if change=='attempt_failed':layer['attempts'][0]['status']=4
    if change=='attempt_unknown':layer['attempts'][0].pop('accepted')
    before=copy.deepcopy(original)
    with SolverRowSidecar(tmp_path/'rows.gz') as writer:
        log=SolverCallLog(writer=writer);log.append(original)
        assert 'rows' in log[0]['solver_backend_layers'][0]['attempts'][0]['validation']
        assert log[0]==original and original==before
        assert writer.receipt()['entry_count']==0
        # Logged branches are independent even when failure remains inline.
        log[0]['solver_backend_layers'][0]['attempts'][0]['validation']['rows'][0]['name']='logged copy'
        assert original==before


def test_body_exception_closes_with_aborted_receipt_and_preserves_entries(tmp_path):
    path=tmp_path/'rows.gz';writer=SolverRowSidecar(path)
    with pytest.raises(RuntimeError,match='physical failure retained'):
        with writer:
            log=SolverCallLog(writer=writer);log.append(call())
            raise RuntimeError('physical failure retained')
    receipt=writer.receipt()
    assert receipt['closed'] and receipt['status']=='aborted'
    assert receipt['body_exception']['type']=='RuntimeError'
    assert receipt['entry_count']==3 and receipt['gzip_bytes']==path.stat().st_size
    assert canonical(restore_solver_calls(log,path))==canonical([call()])
    with pytest.raises(RuntimeError,match='closed'):log.append(call())
    assert len(log)==1


def test_entry_sha_tampering_is_detected(tmp_path):
    path=tmp_path/'rows.gz'
    with SolverRowSidecar(path) as writer:
        log=SolverCallLog(writer=writer);log.append(call())
    entries=list(map(json.loads,gzip.open(path,'rt',encoding='utf8')))
    entries[0]['rows'][0]['activity']=99.
    tampered=tmp_path/'tampered.gz'
    with gzip.open(tampered,'wt',encoding='utf8') as f:
        for entry in entries:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
    with pytest.raises(ValueError,match='SHA'):restore_solver_calls(log,tampered)


def test_write_failure_is_recorded_and_call_append_is_atomic(tmp_path,monkeypatch):
    writer=SolverRowSidecar(tmp_path/'rows.gz',flush_bytes=1)
    def fail(data):raise OSError('disk full injected')
    monkeypatch.setattr(writer,'_flush_block',fail)
    with writer:
        log=SolverCallLog(writer=writer)
        with pytest.raises(OSError,match='disk full'):log.append(call())
        assert len(log)==0
    receipt=writer.receipt()
    assert receipt['closed'] and receipt['status']=='failed'
    assert receipt['write_error']['type']=='OSError'
    assert receipt['entry_count']==0 and receipt['attempted_entry_count']==1


def test_empty_context_valid_gzip_and_exclusive_path(tmp_path):
    path=tmp_path/'empty.gz'
    with SolverRowSidecar(path) as writer:pass
    assert gzip.decompress(path.read_bytes())==b''
    assert writer.receipt()['entry_count']==0
    with pytest.raises(FileExistsError):SolverRowSidecar(path)


def test_existing_calls_are_wrapped_and_extend_uses_same_contract(tmp_path):
    source=call()
    with SolverRowSidecar(tmp_path/'rows.gz') as writer:
        log=SolverCallLog([source],writer=writer);log.extend([source])
    assert len(log)==2
    assert canonical(restore_solver_calls(log,tmp_path/'rows.gz'))==canonical([source,source])
    assert writer.receipt()['entry_count']==6


def test_record_location_cannot_be_relabelled_as_another_validation(tmp_path):
    path=tmp_path/'rows.gz'
    with SolverRowSidecar(path) as writer:
        log=SolverCallLog(writer=writer);log.append(call())
    entries=list(map(json.loads,gzip.open(path,'rt',encoding='utf8')))
    entries[0]['location']=[99,'solver_backend_layers',0,'validation']
    tampered=tmp_path/'relabeled.gz'
    with gzip.open(tampered,'wt',encoding='utf8') as f:
        for entry in entries:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
    with pytest.raises(ValueError,match='location'):restore_solver_calls(log,tampered)
