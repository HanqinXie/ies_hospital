"""Local runner injects construction while reusing complete native evidence."""
import importlib
import inspect
from pathlib import Path
from copy import deepcopy
import json
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from test_annual_readiness_runner import api as native_api
from test_annual_readiness_runner import FakeEnv, FakeController, PARAMETERS, blocks


def api():
    root = Path(__file__).parents[1]
    assert (root/'tools/eight_fault_study/runner.py').is_file(), 'local single-case runner is missing'
    return importlib.import_module('tools.eight_fault_study.runner')


def test_native_attempt_has_opt_in_builder_without_changing_default():
    parameter = inspect.signature(native_api()._execute_attempt).parameters.get('runtime_builder')
    assert parameter is not None, 'private runtime builder injection is missing'
    assert parameter.default is None
    assert api()._execute_loop is native_api()._execute_loop


@pytest.mark.parametrize('method',['M_available','M_ready'])
def test_local_builder_preserves_strict_rule_gate_and_complete_native_logging(tmp_path,monkeypatch,method):
    from tools.annual_readiness import environment, registration
    from test_eight_fault_local_runtime import fake_adapter, ROOT
    registry=registration.load_registry()
    case=registration.make_case(registry,'b07_L4_GT_ER_disturbed_M',method,
        mode='N',start_hour=33,configuration='H2')
    worker=registration.worker_registry(case,registry)
    native_api()._write_new(tmp_path/'BINDINGS.json',native_api()._bindings())
    calls=[];adapter=fake_adapter(calls)
    adapter.controller_factory=lambda alias,**kw:SimpleNamespace(config=deepcopy(kw['config']),policy_id=kw['policy_id'])
    monkeypatch.setattr(environment,'with_readiness',lambda *args,**kw:adapter)
    env,controller,resolved=api()._build_runtime(case,worker,tmp_path,object())
    assert len(calls)==1 and calls[0][0]=='construct'
    kw=calls[0][1]
    assert kw['enable_all_validation_rows'] is True
    assert kw['solver_evidence_directory']==tmp_path/'domain_evidence'
    assert 'annual_program_data_context' not in kw
    assert 'local_actual_witness_source_context' not in kw
    assert resolved['control_rule_id']==case['operating_rule_id']
    assert resolved['annual_numeric_grant'] is False
    assert env.annual_program_data_context is None
    saved=json.loads((tmp_path/'LOCAL_ACTUAL_WITNESS_SOURCE_CONTEXT.json').read_text('utf8'))
    assert saved['case']==case and saved['numeric_access_granted'] is False


def test_local_capture_origin_failure_is_saved_by_native_attempt(tmp_path, monkeypatch):
    from tools.annual_readiness import registration
    from tools.annual_program import witness
    module = native_api()
    case = {'id':'fake', 'configuration':'H0', 'scenario':{'id':'fake','duration_steps':3,'step_minutes':15.},
        'annual_readiness':{'method':'M_ready'},'v28_thermal_profile':{'files':{}}}
    registry = {'frozen':True}
    bindings = {'source':{},'inputs':{},'git_commit':'frozen'}
    module._write_new(tmp_path/'CASE.json',dict(case=case,registry=registry,retain_history=False,case_input_binding=None))
    module._write_new(tmp_path/'BINDINGS.json',bindings)
    monkeypatch.setattr(registration,'load_registry',lambda:registry)
    monkeypatch.setattr(registration,'validate_case',lambda supplied, reg:deepcopy(supplied))
    monkeypatch.setattr(registration,'worker_registry',lambda supplied, reg:deepcopy(reg))
    monkeypatch.setattr(module,'_extra_inputs',lambda *args:{})
    monkeypatch.setattr(module,'_bindings',lambda:deepcopy(bindings))
    def fail(*args):raise ValueError('synthetic unsupported capture origin')
    monkeypatch.setattr(witness,'compiled_origin',fail)
    result = api()._execute_attempt(tmp_path)
    assert result['status']=='failed' and result['actual_episode_attempts']==0
    assert 'unsupported capture origin' in result['error']['message']
    assert result['unknown_tail_minutes']==45.
    assert json.loads((tmp_path/'RESULT.json').read_text('utf8'))['status']=='failed'


def test_reset_warmup_hook_has_no_capture_and_only_step_has_scope(tmp_path,monkeypatch):
    from tools.annual_readiness import registration
    from tools.annual_program import witness
    from tools.annual_program.compiled_capture import capture_enabled
    from test_annual_compiled_flow_input import fixture
    from tools.model_review_v30 import evaluation
    from tools.sci_engineering import design
    module=native_api()
    case={'id':'fake','configuration':'H0','scenario':{'id':'fake','duration_steps':3,'step_minutes':15.},
        'annual_readiness':{'method':'M_ready'},'v28_thermal_profile':{'files':{}}}
    registry={'frozen':True};bindings={'source':{},'inputs':{},'git_commit':'frozen'}
    module._write_new(tmp_path/'CASE.json',dict(case=case,registry=registry,retain_history=False,case_input_binding=None))
    module._write_new(tmp_path/'BINDINGS.json',bindings)
    monkeypatch.setattr(registration,'load_registry',lambda:registry)
    monkeypatch.setattr(registration,'validate_case',lambda supplied,reg:deepcopy(supplied))
    monkeypatch.setattr(registration,'worker_registry',lambda supplied,reg:deepcopy(reg))
    monkeypatch.setattr(module,'_extra_inputs',lambda *args:{})
    monkeypatch.setattr(module,'_bindings',lambda:deepcopy(bindings))
    monkeypatch.setattr(design,'allowed_hours',lambda panel:{1})
    monkeypatch.setattr(evaluation,'_load_frozen_accounting',lambda *args:(PARAMETERS,{1:.2},{}))
    monkeypatch.setattr(witness,'compiled_origin',lambda *args:fixture()['source_origin'])
    phases=[]
    class ProbeEnv(FakeEnv):
        def reset(self,*,scenario,seed):
            # A synthetic initialization callback stands in for a warmup
            # actual hook; it must not receive a compiled capture token before
            # local reset_complete can be authenticated.
            phases.append(('warmup_hook',capture_enabled()))
            if capture_enabled():raise ValueError('synthetic warmup capture before checked reset')
            return super().reset(scenario=scenario,seed=seed)
        def step(self,request):
            phases.append(('step',capture_enabled()))
            assert capture_enabled()
            return super().step(request)
    def snapshot(env):
        phases.append(('snapshot',capture_enabled()))
        assert not capture_enabled()
        return dict(decision_time_min=env.core.time)
    monkeypatch.setattr(module,'_snapshot',snapshot)
    monkeypatch.setattr(module,'_terminal',lambda env,end:dict(snapshot_time_minute=end))
    env=ProbeEnv();controller=FakeController()
    monkeypatch.setattr(api(),'_build_runtime',lambda *args:(env,controller,{'policy':{}}))
    result=api()._execute_attempt(tmp_path)
    assert result['status']=='completed',result.get('error')
    assert phases[0]==('warmup_hook',False)
    assert [token for phase,token in phases if phase=='step']==[True,True,True]
    assert not capture_enabled()


@pytest.mark.parametrize('fail_step',[None,1])
def test_injected_builder_uses_complete_native_history_and_failure_receipts(tmp_path,monkeypatch,fail_step):
    from tools.annual_readiness import registration
    from tools.model_review_v30 import evaluation
    from tools.sci_engineering import design
    module = native_api()
    case = {'id':'fake','configuration':'H0','scenario':{'id':'fake','duration_steps':3,'step_minutes':15.},
        'annual_readiness':{'method':'M_ready'},'v28_thermal_profile':{'files':{}}}
    registry={'frozen':True};bindings={'source':{},'inputs':{},'git_commit':'frozen'}
    module._write_new(tmp_path/'CASE.json',dict(case=case,registry=registry,retain_history=False,case_input_binding=None))
    module._write_new(tmp_path/'BINDINGS.json',bindings)
    monkeypatch.setattr(registration,'load_registry',lambda:registry)
    monkeypatch.setattr(registration,'validate_case',lambda supplied, reg:deepcopy(supplied))
    monkeypatch.setattr(registration,'worker_registry',lambda supplied, reg:deepcopy(reg))
    monkeypatch.setattr(module,'_extra_inputs',lambda *args:{})
    monkeypatch.setattr(module,'_bindings',lambda:deepcopy(bindings))
    monkeypatch.setattr(design,'allowed_hours',lambda panel:{1})
    monkeypatch.setattr(evaluation,'_load_frozen_accounting',lambda *args:(PARAMETERS,{1:.2},{}))
    monkeypatch.setattr(module,'_snapshot',lambda env:dict(decision_time_min=env.core.time))
    monkeypatch.setattr(module,'_terminal',lambda env,end:dict(snapshot_time_minute=end))
    env=FakeEnv(fail_step=fail_step);controller=FakeController()
    def forbidden(*args):raise AssertionError('default builder was called')
    monkeypatch.setattr(module,'_build_runtime',forbidden)
    result=module._execute_attempt(tmp_path,runtime_builder=lambda *args:(env,controller,{'policy':{}}))
    assert env.reset_count==1
    assert env.segment_history is env.core.segments
    if fail_step is None:
        assert result['status']=='completed' and len(env.core.segments)==3
        assert controller.history_lengths==[1,2,3]
    else:
        assert result['status']=='failed' and len(env.core.segments)==2
        assert result['observed_until_minute']==20. and result['unknown_tail_minutes']==25.
        assert 'partial interval' in result['error']['message']
    assert blocks(tmp_path)[0]['solver_calls'][0]['full_error']=={'unknown':None}
