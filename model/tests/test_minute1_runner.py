"""Minute runner control-flow doubles; never a native physical trajectory."""
import importlib
from pathlib import Path
from copy import deepcopy
from contextlib import contextmanager
import gzip
import json
from types import SimpleNamespace

import pytest

from test_annual_readiness_runner import FakeEnv,CallLog,ORDINARY_SCI_REQUEST,segment,blocks


# Exact unchanged synthetic accounting values from test_annual_readiness_runner.
# Its attempt double mutates the exported dict; keep an independent baseline
# even when this module is first imported after that old test has executed.
PARAMETERS=deepcopy(dict(gas_price_usd_per_mmbtu=5.,dg_shaft_fee_USD_per_kWh=.3,
    steam_kWh_per_lb=.3,boiler_eff=.8,kWh_per_MMBtu=300.,ec_electric_kW_per_rt=.8,
    RT_TO_KW=4.,cold_pump_kW_per_kW=.02))


def api():
    assert (Path(__file__).parents[1]/'tools/minute1_20261103/runner.py').is_file(), 'Explicit minute runner is missing'
    return importlib.import_module('tools.minute1_20261103.runner')


def test_runner_functions_have_their_actual_new_module_owner():
    module=api()
    for name in ('_prepare_attempt','_execute_attempt','_execute_loop','run_case'):
        value=getattr(module,name)
        assert value.__module__==module.__name__ and value.__globals__ is vars(module)
        assert Path(value.__code__.co_filename).resolve()==Path(module.__file__).resolve()


class MinuteEnv(FakeEnv):
    def step(self,request):
        self.core.solver_calls.extend([{'status':'failed','unknown':None},{'status':'success','minute':self.core.time}])
        if request is not None:
            self.core.events.append(dict(kind='command_generated',time_minute=self.core.time,
                command={'id':self.steps_done,'request':deepcopy(request)}))
        failed=self.steps_done==self.fail_step
        if failed and self.fail_before_segment:raise RuntimeError('failure before minute segment')
        end=self.core.time+(.5 if failed else 1.)
        actual=segment(self.core.time,end);self.core.segments.append(actual)
        if self.actual_segment_observer:self.actual_segment_observer(actual)
        self.core.time=end
        if failed:raise RuntimeError('failure after partial minute')
        self.steps_done+=1
        return {},0.,self.steps_done>=self.scenario['duration_steps'],False,dict(segments=[deepcopy(actual)],
            command_id=self.steps_done-1 if request is not None else None)


class MinuteController:
    def __init__(self):self.observed=[]
    def decide(self,records):
        assert [r['decision_time_min'] for r in records]==list(range(len(records)))
        self.observed.append(records[-1]['decision_time_min'])
        return deepcopy(ORDINARY_SCI_REQUEST),{'unknown':None}


def execute(tmp_path,monkeypatch,*,variant='responsive',duration=180,fail_step=None,fail_before_segment=False):
    module=api()
    monkeypatch.setattr(module,'_snapshot',lambda env:dict(decision_time_min=env.core.time))
    monkeypatch.setattr(module,'_terminal',lambda env,end:dict(snapshot_time_minute=end,observed_until_minute=end))
    journal=module.BlockJournal(tmp_path/'evidence',identity={'case_id':'synthetic_minute','source':'protocol_only'})
    env=MinuteEnv(fail_step=fail_step,fail_before_segment=fail_before_segment);controller=MinuteController()
    result=module._execute_loop(env,controller,{'id':'synthetic_minute','step_minutes':1.,'duration_steps':duration},
        journal,tmp_path,variant=variant,accounting_parameters=deepcopy(PARAMETERS),tariffs_by_hour={1:.2,2:.2,3:.2,4:.2})
    return result,env,controller


@pytest.mark.parametrize('duration',[180,240])
def test_minute_loop_observes_and_dispatches_each_actual_minute_once(tmp_path,monkeypatch,duration):
    result,env,controller=execute(tmp_path,monkeypatch,duration=duration)
    assert result['status']=='completed' and result['observed_until_minute']==duration
    assert result['legal_observation_count']==result['controller_call_count']==duration
    assert controller.observed==list(range(duration)) and env.reset_count==1
    assert len(env.core.segments)==duration and isinstance(env.core.solver_calls,CallLog)
    assert len((tmp_path/'ACTUAL_PREFIX.jsonl').read_text().splitlines())==duration
    assert len(blocks(tmp_path))==duration


def test_unattended_loop_keeps_observations_but_never_calls_policy_after60(tmp_path,monkeypatch):
    result,env,controller=execute(tmp_path,monkeypatch,variant='unattended')
    assert result['status']=='completed' and result['legal_observation_count']==180
    assert result['controller_call_count']==60 and controller.observed==list(range(60))
    assert len(env.core.events)==60
    raw=blocks(tmp_path)
    assert raw[59]['decision']['requested_command']==ORDINARY_SCI_REQUEST
    assert raw[60]['decision']['requested_command'] is None
    assert raw[60]['decision']['command_generated'] is None
    assert raw[60]['decision']['diagnostic']['pending_commands_retained'] is True


def test_failed_partial_minute_is_committed_without_zero_filling_unknown_tail(tmp_path,monkeypatch):
    result,env,controller=execute(tmp_path,monkeypatch,fail_step=1)
    assert result['status']=='failed' and result['observed_until_minute']==1.5
    assert result['unknown_tail_minutes']==178.5 and result['error']['type']=='RuntimeError'
    raw=blocks(tmp_path)
    assert len(raw)==2 and raw[-1]['end_minute']==1.5
    assert raw[-1]['solver_calls'][0]['status']=='failed'
    assert raw[-1]['solver_calls'][0]['unknown'] is None


def test_no_actual_minute_failure_retains_original_context_and_zero_observed_duration(tmp_path,monkeypatch):
    result,env,controller=execute(tmp_path,monkeypatch,fail_step=0,fail_before_segment=True)
    assert result['status']=='failed' and result['observed_until_minute']==0.
    assert result['unknown_tail_minutes']==180.
    path=next(tmp_path.glob('FAILURE_CONTEXT_*.json.gz'))
    with gzip.open(path,'rt',encoding='utf8') as stream:value=json.load(stream)
    assert value['segments']==[] and value['solver_calls'][0]['status']=='failed'


@pytest.mark.parametrize('failed_close',[False,True])
def test_actual_result_is_published_only_after_nonlinear_session_closes(tmp_path,monkeypatch,failed_close):
    module=api();out=tmp_path/'attempt';out.mkdir();order=[];sampling=module.MinuteSamplingSpec()
    case={'id':'synthetic_minute','configuration':'H2','scenario':{'duration_steps':180,'step_minutes':1.},
        'minute1':{'variant':'responsive'}}
    module._write_new(out/'CASE.json',{'case':case,'registry':{},'retain_history':False,
        'source_admission':{'path':'synthetic_not_an_admission','sha256':'a'*64},'sampling_spec':sampling.as_dict()})
    module._write_new(out/'BINDINGS.json',{'source':{},'inputs':{},'git_commit':'synthetic'})
    monkeypatch.setattr(module,'load_registry',lambda:{})
    monkeypatch.setattr(module,'validate_case',lambda value,registry:deepcopy(value))
    monkeypatch.setattr(module,'worker_registry',lambda *a:{})
    monkeypatch.setattr(module,'_extra_inputs',lambda *a:{})
    monkeypatch.setattr(module,'_bindings_unchanged',lambda *a:True)
    monkeypatch.setattr(module,'verify_source_admission',lambda *a,**k:{})
    monkeypatch.setattr(module,'compiled_origin',lambda *a,**k:{})
    monkeypatch.setattr(module,'allowed_hours',lambda *a:{1})
    monkeypatch.setattr(module,'_load_frozen_accounting',lambda *a:({}, {}, {}))
    class Writer:
        def __init__(self,*a):self.body_exception=None
        def receipt(self):return {'schema':'synthetic_sidecar'}
        def close(self):order.append('writer_closed')
    class Store:
        def __init__(self,*a,**k):pass
        def finish(self,session):
            assert session['status']=='closed_completed' and not (out/'RESULT.json').exists()
            order.append('NL_finished')
            if failed_close:raise OSError('NL final fsync failed')
            return {'required':True}
        def receipt(self,session):return {'required':True,'failure_preserved':True}
    @contextmanager
    def scope(store):
        session={'status':'open'};yield session
        session['status']='closed_completed';order.append('NL_scope_closed')
    @contextmanager
    def capture(origin):yield
    monkeypatch.setattr(module,'SolverRowSidecar',Writer)
    monkeypatch.setattr(module,'NonlinearStore',Store)
    monkeypatch.setattr(module,'nonlinear_evidence_scope',scope)
    monkeypatch.setattr(module,'capture_scope',capture)
    initialization=dict(reference_minute_of_year=1,static_solve_dt_hours=.25,
        source='native_current_minute_zero',warmup_step_count_is_unchanged=True,
        real_elapsed_warmup_claimed=False)
    fake=SimpleNamespace(sci_config={},operating_model_config={},minute1_initialization_reference=initialization)
    monkeypatch.setattr(module,'_build_runtime',lambda *a,**k:(fake,None,{}))
    def execute(*a,**kwargs):
        kwargs['on_reset'](fake)
        return {'status':'completed','observed_until_minute':180.,'unknown_tail_minutes':0.}
    monkeypatch.setattr(module,'_execute_loop',execute)
    result=module._execute_attempt(out,sampling_spec=sampling)
    assert order.index('NL_scope_closed')<order.index('NL_finished')
    assert result['status']==('failed' if failed_close else 'completed')
    assert json.loads((out/'RESULT.json').read_text(encoding='utf8'))==result
    assert json.loads((out/'RESOLVED_CASE.json').read_text(encoding='utf8'))['minute1_initialization_reference']==initialization
    if failed_close:assert 'fsync failed' in result['error']['message']


def test_file_cli_help_requires_explicit_external_source_pin_and_runs_no_trajectory():
    import subprocess,sys
    module=api()
    result=subprocess.run([sys.executable,'-B',module.__file__,'--help'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert '--source-admission-sha256' in result.stdout and '--physical-max-segment-minutes' in result.stdout


def test_runtime_resolves_only_the_unchanged_parent_through_old_registration(monkeypatch,tmp_path):
    module=api();parent={'id':'original_parent','v28_thermal_profile':{'original':'bound'}}
    case={'id':'new_minute_case','scenario':{'start_hour':33,'duration_steps':180,'step_minutes':1.},
        'minute1':{'parent':parent,'method':'M_ready','minute_source':{'files':'bound'},
            'variant':'unattended','parameters':{'requalification_minutes':None}}}
    registry={'worker':'original'};calls=[]
    monkeypatch.setattr(module,'load_registry',lambda:{'trusted':True})
    monkeypatch.setattr(module,'validate_case',lambda value,registry:deepcopy(value))
    monkeypatch.setattr(module,'worker_registry',lambda *a:deepcopy(registry))
    monkeypatch.setattr(module,'verify_source_admission',lambda *a,**k:{})
    def configuration(value,worker):
        assert value==parent and 'minute1' not in value and worker==registry
        calls.append('original_parent')
        return {'model_config':{'hardware':'unchanged'},'operating_rule':{},'policy':{'alias':'M','config':{}}}
    monkeypatch.setattr(module,'runtime_configuration',configuration)
    monkeypatch.setattr(module,'MinuteSource',lambda root,spec:('source',spec))
    source_options={}
    def factory(method,**options):
        source_options.update(options)
        def env(*a,**k):
            spec=SimpleNamespace(as_dict=lambda:{'physical':'unchanged'})
            return SimpleNamespace(operating_model_config=k['model_config'],control_rule_id='original',control_rule={},
                plant=None,single_fault_fuel_spec=spec,single_fault_model_spec=spec)
        return SimpleNamespace(OperatingHospitalIESEnv=env,controller_factory=lambda *a,**k:SimpleNamespace(config={}))
    monkeypatch.setattr(module,'with_minute1',factory)
    ref={'path':'external','sha256':'a'*64};bindings={'source':{'all':'actual'}}
    env,controller,resolved=module._build_runtime(case,registry,tmp_path,None,sampling_spec=module.MinuteSamplingSpec(),
        source_admission=ref,bindings=bindings)
    assert calls==['original_parent']
    assert source_options['variant']=='unattended' and source_options['source_freeze']==bindings['source']
    assert source_options['thermal_profile']==parent['v28_thermal_profile']
    assert resolved['execution_model_id']==module.MODEL_ID and resolved['original_parent_case_id']==parent['id']


def test_invalid_minute_scope_does_not_create_an_attempt(monkeypatch,tmp_path):
    module=api()
    monkeypatch.setattr(module,'_prepare_attempt',lambda *a,**k:pytest.fail('An invalid minute attempt must not be prepared'))
    with pytest.raises(ValueError):module.run_case({'scenario':{'step_minutes':15.,'duration_steps':12}},tmp_path/'bad',source_admission={})
    with pytest.raises(ValueError):module.run_case({'scenario':{'step_minutes':1.,'duration_steps':180}},tmp_path/'bad',
        source_admission={},physical_max_segment_minutes=2.)


def test_saved_case_preflight_failure_is_preserved_without_any_physical_attempt(tmp_path,monkeypatch):
    module=api();out=tmp_path/'attempt';out.mkdir()
    module._write_new(out/'CASE.json',{'case':{'id':'tampered'},'registry':{}})
    module._write_new(out/'BINDINGS.json',{})
    monkeypatch.setattr(module,'load_registry',lambda:{})
    def reject(*args):raise ValueError('saved canonical case changed')
    monkeypatch.setattr(module,'validate_case',reject)
    result=module._execute_attempt(out,sampling_spec=module.MinuteSamplingSpec())
    assert result['status']=='failed' and result['actual_episode_attempts']==0
    assert result['unknown_tail_minutes'] is None and result['preflight_case_validated'] is False
    assert 'canonical case changed' in result['error']['message']
    assert json.loads((out/'RESULT.json').read_text(encoding='utf8'))==result


def test_existing_final_result_is_never_modified_by_direct_attempt_entry(tmp_path):
    module=api();out=tmp_path/'attempt';out.mkdir();path=out/'RESULT.json'
    path.write_text('{"immutable":true}',encoding='utf8');before=path.read_bytes()
    with pytest.raises(ValueError,match='fresh|Fresh'):
        module._execute_attempt(out,sampling_spec=module.MinuteSamplingSpec())
    assert path.read_bytes()==before


def test_file_entry_dispatches_the_canonical_module_that_the_witness_attests(monkeypatch):
    import runpy,sys
    module=api();calls=[]
    def canonical_main():calls.append('canonical_attested_owner');return 19
    monkeypatch.setattr(module,'main',canonical_main)
    monkeypatch.setattr(sys,'argv',[module.__file__])
    with pytest.raises(SystemExit) as error:runpy.run_path(module.__file__,run_name='__main__')
    assert error.value.code==19 and calls==['canonical_attested_owner']


def test_file_cli_inserts_repo_root_when_only_src_is_already_on_pythonpath(tmp_path):
    import os,subprocess,sys
    module=api();root=Path(module.__file__).resolve().parents[2]
    result=subprocess.run([sys.executable,'-B',module.__file__,'--help'],cwd=tmp_path,
        env=dict(os.environ,PYTHONPATH=str(root/'src')),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert '--source-admission-sha256' in result.stdout
