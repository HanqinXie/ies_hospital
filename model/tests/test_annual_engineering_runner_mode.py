"""Explicit recording admission/resume protocol, using only fake plant steps."""
from copy import deepcopy
import json

import pytest

from test_annual_program_runner import api,setup,run
from test_annual_program_indexed_integration import _driver,_IndexedFixtureEnv


def test_default_full_orchestration_keeps_exact_v2_bytes():
    module=api()
    expected=dict(schema=module.MODEL_ID+'.external_orchestration_freeze.v2',stop_file=None,
        checkpoint_every_blocks=96,stop_file_existence_only=True,
        external_orchestration_only=True,policy_input=False)
    expected['signature']=module.digest(expected)
    assert module.legacy._bytes(module._orchestration_freeze(None))==module.legacy._bytes(expected)
    assert module._orchestration_freeze(None,recording_mode='full_v1')==expected


def test_engineering_orchestration_freezes_explicit_v3_mode_and_signature():
    module=api();value=module._orchestration_freeze(None,recording_mode='engineering_v1')
    assert value['schema']==module.MODEL_ID+'.external_orchestration_freeze.v3'
    assert value['recording_mode']=='engineering_v1'
    assert value['signature']==module.digest({key:item for key,item in value.items() if key!='signature'})
    assert value['signature']!=module._orchestration_freeze(None)['signature']


@pytest.mark.parametrize('mode',[None,True,1,'engineering','unknown'])
def test_invalid_public_mode_rejected_before_any_output_or_admission(tmp_path,monkeypatch,mode):
    module=api();output=tmp_path/'never-opened'
    monkeypatch.setattr(module,'validate_case',lambda *a:pytest.fail('invalid mode reached case admission'))
    with pytest.raises(ValueError,match='recording.mode'):
        module.run_case({},output,approval_receipt={},recording_mode=mode)
    assert not output.exists()


@pytest.mark.parametrize('mode',['full_v1','engineering_v1'])
def test_pure_loop_sets_live_store_flag_before_reset_and_on_reset(tmp_path,monkeypatch,mode):
    from types import SimpleNamespace
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    env.domain_evidence=SimpleNamespace();observed=[];original=env.reset
    # Fake reset writes an initialization marker only if store.store exists.
    env.domain_evidence.store=lambda rows:None
    def reset(**kwargs):
        observed.append(('reset',env.domain_evidence.engineering_summary));return original(**kwargs)
    monkeypatch.setattr(env,'reset',reset)
    result=run(module,env,service,policy,journal,tmp_path,recording_mode=mode,
        on_reset=lambda actual:observed.append(('on_reset',actual.domain_evidence.engineering_summary)))
    assert result['status']=='completed' and result['recording_mode']==mode
    assert result['loop_state']['recording_mode']==mode
    assert observed==[('reset',mode=='engineering_v1'),('on_reset',mode=='engineering_v1')]


def test_pure_loop_mode_switch_rejected_before_new_step_or_history(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch);saved=[]
    first=run(module,env,service,policy,journal,tmp_path,recording_mode='engineering_v1',stop_after_blocks=1,
        checkpoint_callback=lambda **kw:saved.append(kw) or {'publication_path':'fake'})
    assert first['status']=='suspended'
    before={p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='recording.mode'):
        run(module,env,service,policy,journal,tmp_path,reset=False,loop_state=first['loop_state'],
            histories=saved[0]['histories'],recording_mode='full_v1')
    assert env.reset_count==1 and env.steps_done==1 and policy.clocks==[0.]
    assert before=={p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_legacy_mode_less_loop_state_can_only_continue_as_full(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch);saved=[]
    first=run(module,env,service,policy,journal,tmp_path,stop_after_blocks=1,
        checkpoint_callback=lambda **kw:saved.append(kw) or {'publication_path':'fake'})
    legacy_state=deepcopy(first['loop_state']);legacy_state.pop('recording_mode')
    with pytest.raises(ValueError,match='recording.mode'):
        run(module,env,service,policy,journal,tmp_path,reset=False,loop_state=legacy_state,
            histories=saved[0]['histories'],recording_mode='engineering_v1')
    result=run(module,env,service,policy,journal,tmp_path,reset=False,loop_state=legacy_state,
        histories=saved[0]['histories'])
    assert result['status']=='completed' and result['recording_mode']=='full_v1'
    assert env.reset_count==1 and env.steps_done==3


def public_fixture(tmp_path,monkeypatch,mode):
    from tools.annual_readiness.journal import BlockJournal
    native_append=BlockJournal.append
    module,case,output,runtimes,order=_driver(tmp_path,monkeypatch)
    # _driver's spy is deliberately bound to its sole first-step store/count.
    # This new multi-invocation fixture uses the original native journal body;
    # the old single-step spy and all its original tests remain unchanged.
    monkeypatch.setattr(BlockJournal,'append',native_append)
    case['scenario']['duration_steps']=3
    monkeypatch.setattr(module,'_activate_restored_inputs',lambda env:None)
    checked=[];original_reset=_IndexedFixtureEnv.reset;original_step=_IndexedFixtureEnv.step
    def reset(env,**kwargs):
        checked.append(('reset',env.domain_evidence.engineering_summary));return original_reset(env,**kwargs)
    def step(env,request):
        checked.append(('step',env.domain_evidence.engineering_summary));return original_step(env,request)
    monkeypatch.setattr(_IndexedFixtureEnv,'reset',reset)
    monkeypatch.setattr(_IndexedFixtureEnv,'step',step)
    contexts=[]
    def access(root,hours,days,context):
        assert set(context)=={'approval_receipt','runtime_freeze_receipt','case','configuration'}
        contexts.append(deepcopy(context));return {'fixture_only':True}
    monkeypatch.setattr(module,'validate_annual_access',access)
    result=module.run_case(case,output,approval_receipt={'fixture_only':True},
        recording_mode=mode,stop_after_blocks=1)
    assert result['status']=='suspended',result.get('error')
    return module,case,output,runtimes,result,checked,contexts


@pytest.mark.parametrize('mode',['full_v1','engineering_v1'])
def test_public_mode_frozen_before_reset_and_restored_without_store_schema_change(tmp_path,monkeypatch,mode):
    module,case,output,runtimes,first,checked,contexts=public_fixture(tmp_path,monkeypatch,mode)
    marker=first['checkpoint']['publication_path']
    publication=module._read(marker);document=module._read(publication['checkpoint_path'])
    state=module._decode(document['runner_state'])
    assert state['recording_mode']==first['recording_mode']==mode
    assert 'engineering_summary' not in state['domain_store'] and 'recording_mode' not in state['domain_store']
    freeze=module._read(output/'ORCHESTRATION_FREEZE.json')
    assert freeze['schema'].endswith('.v3' if mode=='engineering_v1' else '.v2')
    assert freeze.get('recording_mode','full_v1')==mode
    second=module.run_case(case,output,approval_receipt={'fixture_only':True},recording_mode=mode,resume_checkpoint=marker)
    assert second['status']=='completed',second.get('error')
    assert second['recording_mode']==second['loop_state']['recording_mode']==mode
    assert checked==[('reset',mode=='engineering_v1')]+[('step',mode=='engineering_v1')]*3
    assert runtimes[0].reset_count==1 and runtimes[1].reset_count==0
    assert second['economics']['initial_preparation_fee_USD2001']==9.
    assert module._read(output/'RESULT.json')['recording_mode']==mode
    attempts=[json.loads(line) for line in (output/'ATTEMPTS.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(attempts)==4 and all(row['recording_mode']==mode for row in attempts)
    assert len(contexts)==2


@pytest.mark.parametrize('mode',['full_v1','engineering_v1'])
def test_public_mode_switch_refused_before_ledger_writer_or_new_env(tmp_path,monkeypatch,mode):
    module,case,output,runtimes,first,_,_=public_fixture(tmp_path,monkeypatch,mode)
    before={p.relative_to(output).as_posix():p.read_bytes() for p in output.rglob('*') if p.is_file()}
    for name in ('_build_runtime','SolverRowSidecar','_restore_indexed_domains'):
        monkeypatch.setattr(module,name,lambda *a,**kw:pytest.fail('mode switch constructed a runtime or writer'))
    other='full_v1' if mode=='engineering_v1' else 'engineering_v1'
    with pytest.raises(ValueError,match='recording.mode|orchestration'):
        module.run_case(case,output,approval_receipt={'fixture_only':True},recording_mode=other,
            resume_checkpoint=first['checkpoint']['publication_path'])
    assert len(runtimes)==1
    assert before=={p.relative_to(output).as_posix():p.read_bytes() for p in output.rglob('*') if p.is_file()}


def test_checkpoint_loop_mode_mismatch_rejected_before_ATTEMPTS_append(tmp_path,monkeypatch):
    module,case,output,_,first,_,_=public_fixture(tmp_path,monkeypatch,'engineering_v1')
    original=module._load_publication
    def mismatch(*args):
        document,state=original(*args);state['recording_mode']='full_v1';return document,state
    monkeypatch.setattr(module,'_load_publication',mismatch)
    before=(output/'ATTEMPTS.jsonl').read_bytes()
    with pytest.raises(ValueError,match='recording.mode'):
        module.run_case(case,output,approval_receipt={'fixture_only':True},recording_mode='engineering_v1',
            resume_checkpoint=first['checkpoint']['publication_path'])
    assert (output/'ATTEMPTS.jsonl').read_bytes()==before


def test_cli_explicit_engineering_mode_passed_to_public_driver(tmp_path,monkeypatch):
    module=api();captured=[]
    monkeypatch.setattr(module,'_read',lambda path:{'fixture_only':True})
    monkeypatch.setattr(module,'run_case',lambda *a,**kw:captured.append(kw) or {'status':'suspended'})
    assert module.main(['--case-file','fixture.json','--output',str(tmp_path/'producer'),
        '--approval-receipt','approval.json','--recording-mode','engineering_v1'])==0
    assert captured[0]['recording_mode']=='engineering_v1'
