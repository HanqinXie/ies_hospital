"""Actual runner/native-journal directory lifecycle; no formal trajectories."""
from pathlib import Path
import json
import pytest


def nested_error_types(value):
    if not isinstance(value,dict):return []
    return [value.get('type')]+nested_error_types(value.get('native_error'))


def test_real_runner_reaches_native_journal_before_controlled_preconstruction_stop(tmp_path):
    from tools.annual_readiness.registration import load_registry,make_case
    from tools.single_fault_fuel_20261102 import runner
    case=make_case(load_registry(),'b07_L4_GT_ER_disturbed_M','M_available',
        mode='N',start_hour=1,configuration='H2')
    stop=tmp_path/'controlled_unit_stop';stop.write_text('unregistered unit; no environment construction',encoding='utf8')
    out=tmp_path/'unregistered_runner_entry'
    result=runner.run_case(case,out,stopfile=stop)
    errors=nested_error_types(result.get('error'))
    assert result['status']=='failed'
    assert 'FileExistsError' not in errors,result.get('error')
    assert 'ControlledStopRequested' in errors,result.get('error')
    assert (out/'evidence/IDENTITY.json').is_file()
    assert (out/'evidence/blocks.jsonl').read_bytes()==b''
    assert json.loads((out/'evidence/SUMMARY.json').read_text('utf8'))['status']=='failed'
    assert json.loads((out/'RESULT.json').read_text('utf8'))['status']=='failed'
    assert result['actual_episode_attempts']==0


def test_explicit_deferred_store_and_real_exclusive_journal_can_share_fresh_tree(tmp_path):
    from tools.annual_readiness.journal import BlockJournal
    from tools.single_fault_fuel_20261102.nl_store import NonlinearStore
    from test_sf2_nl_store import domain,result,session
    out=tmp_path/'component';out.mkdir()
    store=NonlinearStore(out,bindings={'synthetic':'directory protocol only'},case_id='unit',
        physical_model_spec={},storage_model_spec={},defer_directory_initialization=True)
    assert not (out/'evidence').exists()
    journal=BlockJournal(out/'evidence',identity={'unit':'real native journal, no physics'})
    original_identity=(out/'evidence/IDENTITY.json').read_bytes()
    d=store(domain());r=store(result(d));receipt=store.finish(session([(d,r)]))
    assert receipt['call_count']==receipt['completed_call_count']==1
    assert (out/'evidence/IDENTITY.json').read_bytes()==original_identity
    assert (out/'evidence/blocks.jsonl').read_bytes()==b''
    summary=journal.close(status='failed',expected_end_minute=1.,error={'type':'UnregisteredUnitComplete'})
    assert summary['integrity_verified'] is True and summary['observation']['complete'] is False


def deferred(tmp_path):
    from tools.single_fault_fuel_20261102.nl_store import NonlinearStore
    out=tmp_path/'component';out.mkdir()
    return NonlinearStore(out,bindings={},case_id='unit',physical_model_spec={},storage_model_spec={},
        defer_directory_initialization=True),out


def own_journal(out):
    from tools.annual_readiness.journal import BlockJournal
    return BlockJournal(out/'evidence',identity={'unit':'real original exclusive journal'})


@pytest.mark.parametrize('operation',['callback','finish'])
def test_deferred_initialization_requires_existing_native_journal_ownership(tmp_path,operation):
    from test_sf2_nl_store import domain,session
    store,out=deferred(tmp_path)
    with pytest.raises(ValueError,match='journal'):
        if operation=='callback':store(domain())
        else:store.finish(session([],'closed_failed'))
    assert not (out/'evidence').exists()


def test_deferred_zero_NL_failure_retains_both_real_journal_and_failed_session(tmp_path):
    from test_sf2_nl_store import session
    store,out=deferred(tmp_path);journal=own_journal(out)
    core=session([],'closed_failed');core['error']='controlled unregistered component failure'
    evidence=store.finish(core)
    saved=json.loads((out/evidence['session']['path']).read_text('utf8'))
    assert saved['core_session']==core and evidence['index']['event_count']==0
    assert evidence['call_count']==evidence['completed_call_count']==0
    assert journal.close(status='failed',expected_end_minute=1.)['observation']['complete'] is False


def test_deferred_empty_success_does_not_relax_original_nonzero_coverage(tmp_path):
    from test_sf2_nl_store import session
    store,out=deferred(tmp_path);journal=own_journal(out)
    with pytest.raises(ValueError,match='coverage'):store.finish(session([]))
    saved=json.loads((out/'evidence/nonlinear/SESSION.json').read_text('utf8'))
    assert 'storage_error' in saved
    journal.close(status='failed',expected_end_minute=1.)


def test_deferred_partial_index_is_preserved_and_cannot_be_reopened(tmp_path):
    from test_sf2_nl_store import domain
    store,out=deferred(tmp_path);journal=own_journal(out)
    index=out/'evidence/nonlinear/INDEX.jsonl';index.parent.mkdir();index.write_bytes(b'partial retained evidence')
    with pytest.raises(FileExistsError):store(domain())
    assert index.read_bytes()==b'partial retained evidence' and not store.records
    assert not (index.parent/'SESSION.json').exists()
    journal.close(status='failed',expected_end_minute=1.)


def test_deferred_index_fsync_failure_cannot_retry_over_partial_file(tmp_path,monkeypatch):
    from tools.single_fault_fuel_20261102 import nl_store
    from test_sf2_nl_store import domain
    store,out=deferred(tmp_path);journal=own_journal(out)
    def failed(*args):raise OSError('index fsync injected failure')
    with monkeypatch.context() as patch:
        patch.setattr(nl_store.os,'fsync',failed)
        with pytest.raises(OSError,match='index fsync'):store(domain())
    index=out/'evidence/nonlinear/INDEX.jsonl';assert index.exists() and not store.records
    with pytest.raises(FileExistsError):store(domain())
    assert index.read_bytes()==b''
    journal.close(status='failed',expected_end_minute=1.)
