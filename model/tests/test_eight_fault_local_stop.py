"""Synthetic stop boundaries; no native environment or physical solver."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from test_eight_fault_local_runner import api
from test_annual_readiness_runner import FakeEnv, FakeController, PARAMETERS, blocks


def synthetic_attempt(output,monkeypatch,*,stop_when=None):
    from tools.annual_readiness import runner as native, registration
    from tools.annual_program import witness, compiled_capture
    from tools.model_review_v30 import evaluation
    from tools.sci_engineering import design
    from test_annual_compiled_flow_input import fixture
    marker=output/'control.stop'
    case={'id':'fake','configuration':'H0','scenario':{'id':'fake','duration_steps':3,'step_minutes':15.},
        'annual_readiness':{'method':'M_ready'},'v28_thermal_profile':{'files':{}}}
    registry={'frozen':True};bindings={'source':{},'inputs':{},'git_commit':'frozen'}
    native._write_new(output/'CASE.json',dict(case=case,registry=registry,retain_history=False,case_input_binding=None))
    native._write_new(output/'BINDINGS.json',bindings)
    monkeypatch.setattr(registration,'load_registry',lambda:registry)
    monkeypatch.setattr(registration,'validate_case',lambda supplied,reg:deepcopy(supplied))
    monkeypatch.setattr(registration,'worker_registry',lambda supplied,reg:deepcopy(reg))
    monkeypatch.setattr(native,'_extra_inputs',lambda *args:{})
    monkeypatch.setattr(native,'_bindings',lambda:deepcopy(bindings))
    monkeypatch.setattr(design,'allowed_hours',lambda panel:{1})
    monkeypatch.setattr(evaluation,'_load_frozen_accounting',lambda *args:(PARAMETERS,{1:.2},{}))
    monkeypatch.setattr(witness,'compiled_origin',lambda *args:fixture()['source_origin'])
    state=SimpleNamespace(constructions=0,steps=0,capture_entries=0,env=None,controller=None)
    original_capture=compiled_capture.capture_scope
    @contextmanager
    def capture(origin):
        assert not marker.exists(),'capture began before checking the stop marker'
        state.capture_entries+=1
        with original_capture(origin):yield
    monkeypatch.setattr(compiled_capture,'capture_scope',capture)
    class Env(FakeEnv):
        def reset(self,*,scenario,seed):
            returned=super().reset(scenario=scenario,seed=seed)
            if stop_when=='first_step':marker.write_text('stop requested',encoding='utf8')
            return returned
        def step(self,request):
            state.steps+=1
            returned=super().step(request)
            if stop_when=='after_one_step':marker.write_text('stop requested',encoding='utf8')
            return returned
    class Controller(FakeController):
        def decide(self,records):
            assert 'stopfile' not in json.dumps(records)
            return super().decide(records)
    def build(*args):
        state.constructions+=1
        state.env=Env();state.controller=Controller()
        return state.env,state.controller,{'policy':{}}
    def snapshot(env):
        assert not compiled_capture.capture_enabled()
        return dict(decision_time_min=env.core.time)
    monkeypatch.setattr(api(),'_build_runtime',build)
    monkeypatch.setattr(native,'_snapshot',snapshot)
    monkeypatch.setattr(native,'_terminal',lambda env,end:dict(snapshot_time_minute=end))
    if stop_when=='preexisting':marker.write_text('stop requested',encoding='utf8')
    return marker,state


def test_preexisting_stop_fails_with_receipts_before_native_construction(tmp_path,monkeypatch):
    marker,state=synthetic_attempt(tmp_path,monkeypatch,stop_when='preexisting')
    result=api()._execute_attempt(tmp_path,stopfile=marker)
    assert state.constructions==state.steps==state.capture_entries==0
    assert result['status']=='failed' and result['actual_episode_attempts']==0
    assert result['error']['type']=='ControlledStopRequested'
    assert result['error']['underlying_category']=='controlled_stop_requested'
    assert str(marker.resolve()) in result['error']['message']
    assert result['observed_until_minute']==0. and result['unknown_tail_minutes']==45.
    assert json.loads((tmp_path/'RESULT.json').read_text('utf8'))['status']=='failed'
    assert (tmp_path/'SOLVER_ROWS_RECEIPT.json').is_file()
    assert marker.read_text('utf8')=='stop requested'


@pytest.mark.parametrize('stop_when,expected_steps,observed,tail',[
    ('first_step',0,0.,45.),('after_one_step',1,15.,30.)])
def test_boundary_stop_precedes_capture_and_actual_step_and_keeps_committed_prefix(
        tmp_path,monkeypatch,stop_when,expected_steps,observed,tail):
    marker,state=synthetic_attempt(tmp_path,monkeypatch,stop_when=stop_when)
    result=api()._execute_attempt(tmp_path,stopfile=marker)
    assert state.constructions==1 and state.env.reset_count==1
    assert state.steps==state.capture_entries==expected_steps
    assert len(state.env.core.segments)==len(state.env.core.events)==expected_steps
    assert result['status']=='failed' and result['error']['type']=='ControlledStopRequested'
    assert result['observed_until_minute']==result['actual_core_until_minute']==observed
    assert result['unknown_tail_minutes']==tail
    assert len((tmp_path/'ACTUAL_PREFIX.jsonl').read_text('utf8').splitlines())==expected_steps
    assert len(blocks(tmp_path))==expected_steps
    assert marker.read_text('utf8')=='stop requested'
    assert result['source_and_inputs_unchanged'] is True


@pytest.mark.parametrize('registered_marker',[False,True])
def test_no_stop_or_absent_registered_marker_keeps_original_complete_result(tmp_path,monkeypatch,registered_marker):
    marker,state=synthetic_attempt(tmp_path,monkeypatch)
    result=api()._execute_attempt(tmp_path,stopfile=marker if registered_marker else None)
    assert result['status']=='completed' and result['error'] is None
    assert state.constructions==1 and state.steps==state.capture_entries==3
    assert state.env.reset_count==1 and state.controller.history_lengths==[1,2,3]
    assert result['observed_until_minute']==45. and result['unknown_tail_minutes']==0.
    assert not marker.exists()


def test_run_case_registers_resolved_stop_path_in_invocation(tmp_path,monkeypatch):
    module=api();output=tmp_path/'attempt';output.mkdir();(output/'temp').mkdir()
    monkeypatch.chdir(tmp_path)
    marker=Path('control')/'stop.request'
    monkeypatch.setattr(module.native,'_prepare_attempt',lambda *args,**kw:({'id':'fake'},output,{}))
    seen={}
    def execute(path,**kwargs):
        seen.update(kwargs)
        assert path==output
        return {'status':'failed','case_id':'fake'}
    monkeypatch.setattr(module,'_execute_attempt',execute)
    result=module.run_case({'id':'fake'},output,stopfile=marker)
    invocation=json.loads((output/'INVOCATION.json').read_text('utf8'))
    assert invocation['stopfile']==str(marker.resolve())
    assert invocation['stopfile_existence_only'] is True
    assert invocation['stopfile_is_policy_input'] is False
    assert seen['stopfile']==marker.resolve() and result['status']=='failed'


def test_cli_stopfile_binding_preserves_native_failed_exit(tmp_path,monkeypatch,capsys):
    module=api();monkeypatch.chdir(tmp_path)
    case_file=tmp_path/'case.json';case_file.write_text('{"id":"fake"}',encoding='utf8')
    seen={}
    def run(case,output,**kwargs):
        seen.update(kwargs)
        return {'case_id':'fake','status':'failed'}
    monkeypatch.setattr(module,'run_case',run)
    code=module.main(['--case-file',str(case_file),'--output',str(tmp_path/'attempt'),
        '--stopfile','control/stop.request'])
    assert code==1
    assert seen['stopfile']==(tmp_path/'control'/'stop.request').resolve()
    assert json.loads(capsys.readouterr().out)['status']=='failed'
