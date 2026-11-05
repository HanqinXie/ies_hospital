"""Indexed public-driver protocol only: fake intervals, no plant/parser/LP.

The public runner, native domain blob writer, SQLite proof/catalogue commits,
solver sidecar sealing, journal and result/close files are real. Runtime inputs
and accounting are explicitly toy fixtures inherited from the runner tests.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]

from test_annual_program_runner import (
    FakeController,FakeCore,FakeEnv,FakePolicyHistory,SCENARIO,api,raw_blocks)
from tools.annual_program import failure_history
from tools.annual_program.domain_store import DomainStore,proof_readback_reference
from tools.annual_readiness.journal import BlockJournal
from tools.model_correctness_v23.evidence import DomainEvidenceStore
from tools.sci_engineering.solver_log import SolverCallLog


FAULT='fixture-only proof SQLite commit rejected before persistence'


def _read_gzip(path):
    with gzip.open(path,'rt',encoding='utf8') as stream:return json.load(stream)


class _IndexedFixtureEnv(FakeEnv):
    def step(self,request):
        ref=self.domain_evidence.store([{'fixture_only':True,'step':self.steps_done}])
        cursor=len(self.core.solver_calls)
        try:return super().step(request)
        finally:
            # Retain an actual native-format rows_ref even when the inherited
            # fake executor raises after its five-minute partial interval.
            assert len(self.core.solver_calls)==cursor+1
            self.core.solver_calls[-1]['fixture_validation']={'rows_ref':ref}


def _driver(tmp_path,monkeypatch,*,fail_native=False,fail_proof=False):
    module=api();order=[];runtimes=[]
    scenario=dict(copy.deepcopy(SCENARIO),duration_steps=1)
    case={'id':'indexed-protocol-fixture','scenario':scenario,'configuration':'H2',
        'annual_readiness':{'method':'M_ready','fixture_only':True},
        'annual_program':{'fixture_only':True}}
    configuration={'model_config':{'fixture_only':True},'policy':{'alias':'M','config':{}}}
    bindings={'source':{'fixture.py':'fixture-only',
        'tools/model_correctness_v23/evidence.py':module._sha(ROOT/'tools/model_correctness_v23/evidence.py')},
        'inputs':{'fixture.csv':'fixture-only-no-file-read'},'git_commit':'fixture-only-head'}
    monkeypatch.setattr(module,'validate_case',lambda value:copy.deepcopy(value))
    monkeypatch.setattr(module,'load_registry',lambda:{})
    monkeypatch.setattr(module,'_configuration',lambda *a:copy.deepcopy(configuration))
    monkeypatch.setattr(module,'required_hours',lambda value:{1})
    monkeypatch.setattr(module,'_bindings',lambda:copy.deepcopy(bindings))
    monkeypatch.setattr(module,'create_runtime_freeze',lambda *a,**k:{'signature':'fixture-only-freeze'})
    monkeypatch.setattr(module,'validate_annual_access',lambda *a:{'fixture_only':True})
    monkeypatch.setattr(module,'ExactPolicyHistory',FakePolicyHistory)
    monkeypatch.setattr(module,'_snapshot',lambda env:{'decision_time_min':env.core.time})
    monkeypatch.setattr(module,'_terminal',lambda env,end:{'snapshot_time_minute':end})
    monkeypatch.setattr(module,'_block_evaluation',lambda segments,start,end,**kw:{'economics':{
        'observed_operating_fee_USD2001':(end-start)/60.,
        'initial_preparation_fee_USD2001':sum(kw['preparation'].values())}})
    from tools.model_review_v30 import evaluation
    monkeypatch.setattr(evaluation,'_load_frozen_accounting',lambda *a:({}, {}, {'fixture_only':True}))

    def build(case,config,context,output,writer):
        env=_IndexedFixtureEnv(fail_step=0 if fail_native else None)
        env.engineering_core_type=FakeCore;env._solver_row_sidecar=writer
        env.core.solver_calls=SolverCallLog(writer=writer)
        env.domain_evidence=DomainEvidenceStore(output/'domain_evidence',enable_all_validation_rows=True)
        env.sci_config={};env.operating_model_config={'fixture_only':True};env.control_rule={}
        env.input_access_receipt={'fixture_only':True};runtimes.append(env)
        return env,FakeController()
    monkeypatch.setattr(module,'_build_runtime',build)

    native_commit=DomainStore.commit
    def commit(store):
        order.append('store_commit_attempt')
        if fail_proof:
            history=store._proof_history;connection=history._connection
            class RejectCommit:
                def commit(self):raise OSError(FAULT)
                def __getattr__(self,name):return getattr(connection,name)
            history._connection=RejectCommit()
            try:return native_commit(store)
            except OSError:
                # The original DiskHistory.commit body, not this spy, must
                # establish the native ambiguous-write guard.
                assert isinstance(history._write_error,OSError)
                with pytest.raises(RuntimeError,match='unacknowledged write failure'):history.to_state()
                with pytest.raises(RuntimeError,match='unacknowledged write failure'):_=history.retained_count
                order.append('native_write_guard_observed')
                raise
        receipt=native_commit(store)
        order.append('store_committed')
        return receipt
    monkeypatch.setattr(DomainStore,'commit',commit)

    native_append=BlockJournal.append
    def append(journal,block):
        assert order[-1]=='store_committed'
        store=runtimes[0].domain_evidence
        ack=block['domain_store_acknowledgement'];count=ack['committed_count']
        assert count==store._known.to_state()['count']==store._proof_history.to_state()['prefix_count']==2
        assert store._proof_history.retained_count==0
        assert ack['failed_stage'] is None and ack['pending_proof_rows']==0
        order.append('journal_append')
        receipt=native_append(journal,block)
        order.append('journal_ack')
        return receipt
    monkeypatch.setattr(BlockJournal,'append',append)
    return module,case,tmp_path/'producer',runtimes,order


def _close_receipt(module,result,output):
    pointer=result['domain_store_close_receipt'];path=output/pointer['file']
    assert pointer['file']=='DOMAIN_STORE_CLOSE_000000.json.gz'
    assert pointer['sha256']==module._sha(path) and pointer['bytes']==path.stat().st_size
    assert pointer['pending_evidence_preserved_outside_result'] is True
    assert module._read(output/'RESULT.json')['domain_store_close_receipt']==pointer
    assert module._read(output/'RESULT_part_000000.json')['domain_store_close_receipt']==pointer
    return _read_gzip(path)


def test_real_proof_commit_failure_prevents_journal_ack_and_persists_pending_preimage(tmp_path,monkeypatch):
    module,case,output,runtimes,order=_driver(tmp_path,monkeypatch,fail_proof=True)
    result=module.run_case(case,output,approval_receipt={'fixture_only':True})
    assert result['status']=='failed' and result['error']['type']=='OSError'
    assert result['error']['message']==FAULT
    assert order==['store_commit_attempt','native_write_guard_observed']
    assert result['observed_until_minute']==0. and result['actual_core_until_minute']==15.
    assert result['unknown_tail_minutes']==15. and result['checkpoint'] is None
    assert runtimes[0].reset_count==result['reset_count']==result['actual_episode_attempts']==1
    assert runtimes[0].steps_done==1 and len(runtimes)==1
    assert raw_blocks(output)==[] and (output/'evidence/blocks.jsonl').read_bytes()==b''
    summary=module._read(output/'evidence/SUMMARY.json')
    assert summary['status']=='failed' and summary['observation']['complete'] is False
    assert summary['block_count']==summary['segment_count']==0

    rejected=_read_gzip(output/'REJECTED_BLOCK_000000.json.gz')
    assert rejected['journal_append_error']['message']==FAULT
    assert rejected['start_minute']==0. and rejected['end_minute']==15.
    assert len(rejected['segments'])==1 and rejected['segments'][0]['end_minute']==15.
    assert rejected['initial_preparation']=={'ups':4.,'cold_store':5.}
    assert 'domain_store_acknowledgement' not in rejected
    part=result['loop_state']['actual_prefix_parts'][0]
    assert part['segment_count']==1 and part['sha256']==module._sha(output/part['file'])
    with gzip.open(output/part['file'],'rt',encoding='utf8') as stream:
        assert [json.loads(line) for line in stream]==rejected['segments']

    closed=_close_receipt(module,result,output)
    assert closed['failed_stage']['stage']=='proof_commit' and closed['committed_count']==0
    assert closed['uncommitted_proof_state'] is None
    retained=closed['retained_pre_proof_commit'];preimage=retained['proof_history_state']
    assert retained['captured_before_native_proof_commit'] is True and retained['tail_rows']==2
    assert retained['is_store_or_journal_acknowledgement'] is False
    assert retained['automatic_restoration_allowed'] is False and retained['database_may_have_advanced'] is True
    assert retained['prefix_rows_materialized'] is False
    assert preimage['prefix_count']==0 and preimage['global_count']==2
    with sqlite3.connect(preimage['path']) as db:
        assert db.execute('SELECT prefix_count FROM header').fetchone()==(0,)
    with sqlite3.connect(output/'domain_index/catalogue.sqlite') as db:
        assert db.execute('SELECT count FROM header').fetchone()==(0,)
    reader=failure_history.resolve_history_reference(proof_readback_reference(preimage),preimage['identity'])
    try:
        proofs=list(reader)
    finally:reader.close()
    assert len(proofs)==2 and [proof['sequence'] for proof in proofs]==[0,1]
    for proof in proofs:
        assert proof['schema']=='hospital_ies.annual_program.domain_store.v1.native_blob_proof.v1'
        raw=output/'domain_evidence'/proof['ref']['file']
        assert raw.is_file() and hashlib.sha256(raw.read_bytes()).hexdigest()==proof['gzip_sha256']
    assert len(list((output/'domain_evidence').glob('*.json.gz')))==2


@pytest.mark.parametrize('fail_native',[False,True])
def test_store_commits_before_real_journal_ack_for_healthy_and_failed_native_prefix(tmp_path,monkeypatch,fail_native):
    module,case,output,runtimes,order=_driver(tmp_path,monkeypatch,fail_native=fail_native)
    result=module.run_case(case,output,approval_receipt={'fixture_only':True})
    assert order==['store_commit_attempt','store_committed','journal_append','journal_ack']
    assert result['status']==('failed' if fail_native else 'completed')
    if fail_native:assert result['error']['message']=='failed after an actual partial segment'
    else:assert result['error'] is None
    end=5. if fail_native else 15.
    assert result['observed_until_minute']==result['actual_core_until_minute']==end
    assert result['unknown_tail_minutes']==15.-end
    assert runtimes[0].reset_count==result['reset_count']==result['actual_episode_attempts']==1
    blocks=raw_blocks(output)
    assert len(blocks)==1 and blocks[0]['end_minute']==end
    assert blocks[0]['domain_store_acknowledgement']['committed_count']==2
    assert sum(block['initial_preparation'] is not None for block in blocks)==1
    assert result['economics']['initial_preparation_fee_USD2001']==9.
    summary=module._read(output/'evidence/SUMMARY.json')
    assert summary['status']==result['status'] and summary['observation']['complete'] is (not fail_native)
    closed=_close_receipt(module,result,output)
    assert closed['failed_stage'] is None and closed['committed_count']==2
    assert closed['pending_proof_rows']==0 and closed['retained_pre_proof_commit'] is None
    assert closed['uncommitted_proof_state'] is None and closed['close_errors']==[]
