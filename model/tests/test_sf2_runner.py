"""SF2 source/metadata/orchestration doubles; never a formal trajectory."""
from copy import deepcopy
from pathlib import Path
from contextlib import contextmanager
import importlib,json
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]

def api():
    assert (ROOT/'tools/single_fault_fuel_20261102/runner.py').is_file(), 'F3 SF2 runner missing'
    return importlib.import_module('tools.single_fault_fuel_20261102.runner')

def test_runner_prepares_unchanged_case_and_distinct_double_model_binding(monkeypatch,tmp_path):
    module=api();calls=[];case={'id':'canonical','scenario':{'step_minutes':15.,'faults':{},'auxiliary_faults':{},'model_closure_events':[]}}
    before=deepcopy(case);out=tmp_path/'attempt'
    def prepare(value,path,**kwargs):
        calls.append(('prepare',deepcopy(value)));path.mkdir();(path/'temp').mkdir()
        return deepcopy(value),path,{'source':{'actual':'bytes'}}
    monkeypatch.setattr(module.native,'_prepare_attempt',prepare)
    monkeypatch.setattr(module,'_execute_attempt',lambda *a,**k:{'status':'completed','case_id':'canonical'})
    result=module.run_case(case,out,physical_max_segment_minutes=.5)
    binding=json.loads((out/'PHYSICAL_MODEL_BINDING.json').read_text('utf8'))
    invocation=json.loads((out/'INVOCATION.json').read_text('utf8'))
    assert case==before and calls==[('prepare',before)] and result['status']=='completed'
    assert binding['schema']=='single_fault.sf2.physical_model_binding.v1'
    assert binding['physical_model_spec']==invocation['single_fault_fuel_model']
    assert binding['storage_model_spec']==invocation['single_fault_model']
    assert binding['physical_model_spec']['physical_model_id'].startswith('SF2_')
    assert binding['storage_model_spec']['physical_model_id'].startswith('SF1_')
    assert binding['physical_model_spec']['physical_max_segment_minutes']==.5
    assert binding['old_source_byte_equivalence_claimed'] is False
    assert invocation['annual_numeric_grant'] is False

def test_native_loop_code_is_reused_with_actual_sf2_snapshot():
    module=api()
    from tools.single_fault_fuel_20261102.environment import decision_snapshot
    assert module._execute_loop.__code__ is module.native._execute_loop.__code__
    assert module._execute_loop.__globals__['_snapshot'] is decision_snapshot
    assert module._native_execute_attempt.__code__ is module.native._execute_attempt.__code__

def test_file_cli_help_is_importable_without_any_trajectory():
    import subprocess,sys
    executed=subprocess.run([sys.executable,'-B',str(ROOT/'tools/single_fault_fuel_20261102/runner.py'),'--help'],
        cwd=ROOT,capture_output=True,text=True)
    assert executed.returncode==0,executed.stderr
    assert '--case-file' in executed.stdout and '--physical-max-segment-minutes' in executed.stdout

def test_invalid_scope_is_rejected_before_creating_any_attempt(monkeypatch,tmp_path):
    module=api()
    monkeypatch.setattr(module.native,'_prepare_attempt',lambda *a,**k:pytest.fail('native attempt must not be created'))
    with pytest.raises(ValueError):module.run_case({'scenario':{'faults':{'GT_1':0.,'ABS':0.}}},tmp_path/'bad')
    with pytest.raises(ValueError):module.run_case({'scenario':{}},tmp_path/'bad2',physical_max_segment_minutes=2.)

def test_final_result_is_written_only_after_scope_and_session_close(monkeypatch,tmp_path):
    module=api();order=[];out=tmp_path/'attempt';out.mkdir()
    module.native._write_new(out/'BINDINGS.json',{'source':{}})
    module.native._write_new(out/'CASE.json',{'case':{'id':'unit'}})
    @contextmanager
    def scope(sink):
        order.append('scope_enter');value={'status':'open'}
        yield value
        value['status']='closed_completed';order.append('scope_closed')
    class Store:
        def __init__(self,*a,**k):pass
        def __call__(self,payload):pass
        def finish(self,session):
            assert session['status']=='closed_completed' and not (out/'RESULT.json').exists()
            order.append('session_closed')
            return {'required':True,'call_count':1,'completed_call_count':1,'session':{},'index':{}}
    def native_attempt(output,*,runtime_builder):
        order.append('native_reset_and_loop')
        # This isolated native function writes through its replaced global.
        _write_new(output/'RESULT.json',{'status':'completed','case_id':'unit'})
        _write_new(output/'SUMMARY.json',{'status':'completed'})
        assert not (out/'RESULT.json').exists()
        return {'status':'completed','case_id':'unit'}
    monkeypatch.setattr(module,'nonlinear_evidence_scope',scope)
    monkeypatch.setattr(module,'NonlinearStore',Store)
    monkeypatch.setattr(module,'_native_execute_attempt',native_attempt)
    result=module._execute_attempt(out,model_spec=module.ModelSpec())
    assert order==['scope_enter','native_reset_and_loop','scope_closed','session_closed']
    assert result['status']=='completed' and result['sf2_nonlinear_evidence']['required'] is True
    assert json.loads((out/'RESULT.json').read_text('utf8'))==result

def test_session_failure_cannot_leave_a_completed_result(monkeypatch,tmp_path):
    module=api();out=tmp_path/'attempt';out.mkdir()
    module.native._write_new(out/'BINDINGS.json',{'source':{}});module.native._write_new(out/'CASE.json',{'case':{'id':'unit'}})
    @contextmanager
    def scope(sink):
        value={'status':'open'};yield value;value['status']='closed_completed'
    class Store:
        def __init__(self,*a,**k):pass
        def __call__(self,payload):pass
        def finish(self,session):raise OSError('session fsync failed')
        def receipt(self,session):raise OSError('no session file')
    monkeypatch.setattr(module,'nonlinear_evidence_scope',scope);monkeypatch.setattr(module,'NonlinearStore',Store)
    monkeypatch.setattr(module,'_native_execute_attempt',lambda *a,**k:{'status':'completed','case_id':'unit'})
    result=module._execute_attempt(out,model_spec=module.ModelSpec())
    assert result['status']=='failed' and 'session fsync failed' in result['error']['message']
    assert json.loads((out/'RESULT.json').read_text('utf8'))['status']=='failed'


def test_sticky_failed_core_scope_never_exposes_native_completed_result(monkeypatch,tmp_path):
    module=api();out=tmp_path/'attempt';out.mkdir()
    module.native._write_new(out/'BINDINGS.json',{'source':{}});module.native._write_new(out/'CASE.json',{'case':{'id':'unit'}})
    @contextmanager
    def scope(sink):
        value={'status':'open'};yield value;value.update(status='closed_failed',error={'message':'callback failed earlier'})
    class Store:
        def __init__(self,*a,**k):pass
        def finish(self,session):return {'required':True,'call_count':1,'completed_call_count':0,'session':{},'index':{}}
        def receipt(self,session):return {'required':True,'call_count':1,'completed_call_count':0,'session':{},'index':{}}
    monkeypatch.setattr(module,'nonlinear_evidence_scope',scope);monkeypatch.setattr(module,'NonlinearStore',Store)
    monkeypatch.setattr(module,'_native_execute_attempt',lambda *a,**k:{'status':'completed','case_id':'unit'})
    result=module._execute_attempt(out,model_spec=module.ModelSpec())
    assert result['status']=='failed'
    assert json.loads((out/'RESULT.json').read_text('utf8'))['status']=='failed'
    assert result['sf2_nonlinear_evidence']['completed_call_count']==0


@pytest.mark.parametrize('change',['mode_off','bound_spec','bound_curve','sf2_config','storage_config','curve_config','point_role','free_role'])
def test_current_source_cannot_select_sf2_with_disabled_or_mixed_runtime_metadata(change):
    from hospital_ies.model import load_plant
    module=api();spec=module.ModelSpec();plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    curve=module.FuelCurve.from_source(ROOT,plant)
    class ComponentCore(module.FuelCoreMixin):pass
    env=SimpleNamespace(single_fault_fuel_spec=spec,_single_fault_fuel_bound_spec=spec,
        single_fault_fuel_curve=curve,_single_fault_fuel_bound_curve=curve,plant=plant,
        model_id=module.MODEL_ID,engineering_core_type=ComponentCore,
        operating_model_config={'single_fault_fuel_model':spec.as_dict(),'single_fault_fuel_curve':curve.as_dict(),'single_fault_model':module.StorageSpec().as_dict()},
        config={'_sf2_fuel_curve':curve.as_dict(),'_sf2_nonactual_fuel_role':'point_probe','_sf2_free_fuel_role':'free_reference_probe'})
    module._require_sf2_binding(env,spec)
    if change=='mode_off':env.engineering_core_type=object
    elif change=='bound_spec':env._single_fault_fuel_bound_spec=module.ModelSpec()
    elif change=='bound_curve':env._single_fault_fuel_bound_curve=module.FuelCurve.from_source(ROOT,plant)
    elif change=='sf2_config':env.operating_model_config['single_fault_fuel_model']={}
    elif change=='storage_config':env.operating_model_config['single_fault_model']={}
    elif change=='curve_config':env.config['_sf2_fuel_curve']={}
    elif change=='point_role':env.config['_sf2_nonactual_fuel_role']='actual_interval'
    else:env.config['_sf2_free_fuel_role']=None
    with pytest.raises(ValueError,match='actual immutable|immutable spec|SF2 runner'):
        module._require_sf2_binding(env,spec)
