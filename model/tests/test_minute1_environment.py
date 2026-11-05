"""Minute environment binding doubles; no numerical initialization or solve."""
from copy import deepcopy
import importlib
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))


def api():
    assert (ROOT/'tools/minute1_20261103/environment.py').is_file(), 'Explicit minute environment is missing'
    return importlib.import_module('tools.minute1_20261103.environment')


class FakeSource:pass


class FakeCurve:
    @classmethod
    def from_source(cls,root,plant):return cls()
    def as_dict(self):return {'curve':'unchanged actual SF2 curve fixture'}


class FakeCore:
    def __init__(self):
        self.time=0.;self.pending_commands=[{'arrival_minute':75.,'id':'old'}]
        self.generated=[];self.checks=[]
    def _check_single_fault_model(self):self.checks.append('storage')
    def _check_sf2_model(self):self.checks.append('fuel')
    def _check_minute_model(self):self.checks.append('minute')
    def generate_command(self,action):
        self.generated.append(deepcopy(action))
        return {'id':len(self.generated),'optimizer_warning':''}
    def observe(self):return {'minute1_sampling':{'control_interval_minutes':1.}}


class FakeEnvironment:
    def __init__(self,root,**kwargs):
        self.root=Path(root);self.plant=object();self.engineering_core_type=FakeCore
        self.operating_model_config=deepcopy(kwargs.get('model_config',{}));self.config={}
        self.core=None;self.model_id='old';self.history=[];self.reset_calls=[]
    def reset(self,*,scenario,seed=None):
        self.reset_calls.append((deepcopy(scenario),seed));self.dt_minutes=scenario['step_minutes']
        self.scenario=deepcopy(scenario);self.core=FakeCore()
        return self._observation(),{'native_reset':True}
    def _observation(self):return {'minute1_sampling':{'time':getattr(self.core,'time',0.)}}
    def step(self,action=None):
        issued=self._issue_upper_command({} if action is None else action)
        info={'command_id':issued['id'],'requested_action':deepcopy(action),'planned_action':deepcopy(action),'projection_status':'single_step_priority'}
        self.history.append({'command_id':issued['id'],'projection_status':'single_step_priority'})
        self.core.time+=1.
        return self._observation(),0.,False,False,info


def create(monkeypatch,variant='responsive'):
    module=api();calls=[]
    def readiness(method,**kwargs):
        calls.append((method,deepcopy(kwargs)))
        return SimpleNamespace(OperatingHospitalIESEnv=FakeEnvironment,controller_factory='kept',validate_scenario='kept')
    monkeypatch.setattr(module,'with_readiness',readiness)
    monkeypatch.setattr(module,'MinuteSource',FakeSource)
    monkeypatch.setattr(module,'FuelCurve',FakeCurve)
    source=FakeSource();ref={'path':'E:/external/admission.json','sha256':'a'*64};freeze={'runtime.py':'b'*64}
    factory=module.with_minute1('M_ready',minute_source=source,source_admission=ref,source_freeze=freeze,variant=variant)
    env=factory.OperatingHospitalIESEnv(ROOT,model_config={'hardware':'original'})
    return module,env,source,ref,freeze,calls


def test_factory_installs_unchanged_physics_and_distinct_minute_sampling_before_reset(monkeypatch):
    module,env,source,ref,freeze,calls=create(monkeypatch)
    assert calls[0][0]=='M_ready' and env.core is None
    mro=env.engineering_core_type.__mro__
    assert mro.index(module.MinuteActualCoreMixin)<mro.index(module.FuelCoreMixin)<mro.index(module.SingleFaultCoreMixin)<mro.index(FakeCore)
    assert env.single_fault_model_spec.physical_model_id.startswith('SF1_')
    assert env.single_fault_fuel_spec.physical_model_id.startswith('SF2_')
    assert env.minute1_sampling_spec.model_id==module.MODEL_ID
    assert env.minute_source is env._minute1_bound_source is source
    assert env.minute1_source_admission==ref and env.minute1_source_admission is not ref
    assert env.minute1_source_freeze==freeze and env.minute1_source_freeze is not freeze
    assert env.reference_initialization_solver.func is module.solve_hour
    assert env.reference_initialization_solver.keywords=={'sf2_fuel_config':env.single_fault_fuel_curve.as_dict()}


def test_minute_reset_goes_directly_to_readiness_without_the_old_15minute_guard(monkeypatch):
    module,env,*_=create(monkeypatch)
    scene={'step_minutes':1.,'duration_steps':180,'faults':{},'auxiliary_faults':{}}
    result=env.reset(scenario=scene,seed=7)
    assert env.reset_calls==[(scene,7)] and result[1]=={'native_reset':True}
    assert env.model_id==module.MODEL_ID and env.core.checks==['storage','fuel','minute']


@pytest.mark.parametrize('change',['source','sampling','curve','storage_config','variant','admission','reference'])
def test_mixed_minute_bindings_fail_before_native_reset(monkeypatch,change):
    module,env,*_=create(monkeypatch)
    if change=='source':env.minute_source=FakeSource()
    elif change=='sampling':env.minute1_sampling_spec=module.MinuteSamplingSpec()
    elif change=='curve':env.single_fault_fuel_curve=FakeCurve()
    elif change=='storage_config':env.operating_model_config['single_fault_model']={}
    elif change=='variant':env.minute1_variant='unattended'
    elif change=='admission':env.minute1_source_admission['sha256']='0'*64
    else:env.reference_initialization_solver=lambda *a,**k:None
    with pytest.raises(ValueError):env.reset(scenario={'step_minutes':1.,'duration_steps':180},seed=7)
    assert env.reset_calls==[]


def test_unattended_boundary_refuses_legacy_lower_optimization_and_retains_state(monkeypatch):
    # The old upper-only definition permitted post60 lower optimization. Under
    # strict U that successful-step assumption is invalid. Preserve preboundary
    # command/queue coverage and require an explicit, nonmutating refusal.
    module,env,*_=create(monkeypatch,'unattended')
    env.reset(scenario={'step_minutes':1.,'duration_steps':180},seed=7)
    env.core.time=58.;request={'targets':{'grid_import':1.}}
    env.step(request);queue=deepcopy(env.core.pending_commands)
    history=deepcopy(env.history)
    with pytest.raises(module.StrictUnattendedForwardUnavailable):env.step(None)
    assert env.core.generated==[request] and env.core.pending_commands==queue
    assert env.core.time==59. and env.history==history
    env.core.time=60.
    with pytest.raises(module.StrictUnattendedForwardUnavailable):env.step(None)
    with pytest.raises(ValueError):env.step(request)


def test_snapshot_retains_sf2_physical_identity_and_names_minute_execution(monkeypatch):
    module,env,*_=create(monkeypatch)
    env.reset(scenario={'step_minutes':1.,'duration_steps':180},seed=7)
    monkeypatch.setattr(module,'sf2_decision_snapshot',lambda value:{'physical_execution_model_id':'SF2_original'})
    snapshot=module.decision_snapshot(env)
    assert snapshot['physical_execution_model_id']=='SF2_original'
    assert snapshot['control_execution_model_id']==module.MODEL_ID
    assert snapshot['minute1_sampling']['control_interval_minutes']==1.
