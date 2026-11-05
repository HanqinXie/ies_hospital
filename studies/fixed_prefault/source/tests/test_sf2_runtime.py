"""Pure constructor/path/receipt checks plus an explicitly short reset component."""
from copy import deepcopy
from dataclasses import asdict,replace
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_sf2_factory_installs_before_reset_without_weakening_sf1(monkeypatch):
    from hospital_ies.model import load_plant
    from tools.single_fault_fuel_20261102 import environment as e
    from tools.single_fault_model_20261102.runtime import ModelSpec as OldSpec
    calls=[]
    class Core:
        def _check_single_fault_model(self):pass
        def _check_sf2_model(self):pass
    class Env:
        def __init__(self,*a,**k):
            self.root=ROOT;self.plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
            self.operating_model_config={};self.config={};self.engineering_core_type=Core
            self.single_fault_model_spec=OldSpec();self.model_id='SF1';self.core=Core()
        def reset(self,**kwargs):
            calls.append(kwargs);self.model_id='SF1'
            if getattr(self,'tamper_during_reset',False):
                self.reference_initialization_solver.keywords['sf2_fuel_config']['partload_multiplier']=1.
            return {},{}
        def _observation(self):return {'model_id':self.model_id}
    monkeypatch.setattr(e,'with_single_fault_model',lambda *a,**k:SimpleNamespace(OperatingHospitalIESEnv=Env))
    factory=e.with_single_fault_fuel('M_available');env=factory.OperatingHospitalIESEnv(ROOT)
    assert calls==[]
    assert type(env.single_fault_model_spec) is OldSpec
    assert env.operating_model_config['single_fault_fuel_model']==env.single_fault_fuel_spec.as_dict()
    assert env.config['_sf2_fuel_curve']['contract'].startswith('single_fault.pnnl')
    assert env.engineering_core_type.__mro__[1] is e.FuelCoreMixin
    env.reset(scenario={'step_minutes':15})
    assert len(calls)==1 and env.model_id==e.MODEL_ID
    reference=env.reference_initialization_solver
    reference.keywords['sf2_fuel_config']['partload_multiplier']=1.
    with pytest.raises(ValueError,match='SF2'):
        env.reset(scenario={'step_minutes':15})
    assert len(calls)==1
    reference.keywords['sf2_fuel_config']=env.single_fault_fuel_curve.as_dict()
    from functools import partial
    env.reference_initialization_solver=partial(reference.func,**deepcopy(reference.keywords))
    with pytest.raises(ValueError,match='SF2'):
        env.reset(scenario={'step_minutes':15})
    assert len(calls)==1
    env.reference_initialization_solver=reference
    env.tamper_during_reset=True
    with pytest.raises(ValueError,match='SF2'):
        env.reset(scenario={'step_minutes':15})
    assert len(calls)==2
    env.tamper_during_reset=False
    reference.keywords['sf2_fuel_config']=env.single_fault_fuel_curve.as_dict()
    env.config['_sf2_fuel_curve']['partload_multiplier']=1.
    with pytest.raises(ValueError,match='SF2'):
        env.reset(scenario={'step_minutes':15})
    assert len(calls)==2, 'reject model mutation before any additional native reset/solve'


def path_core():
    from hospital_ies.model import load_plant
    from tools.single_fault_fuel_20261102.fuel import FuelCurve
    from tools.single_fault_fuel_20261102.runtime import FuelCoreMixin,ModelSpec
    curve=FuelCurve.from_source(ROOT,load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory'))
    class Base:
        def _local_interval_action(self,end,f,row):
            start=np.array([320.,0.,0.]);stop=np.array([320.-(end-self.time)*15.,0.,0.])
            return {'gt_electric_kW':((start+stop)/2).tolist()},start,stop
        def _solve(self,*args,**kwargs):return kwargs
        def _after_actual_segment(self,segment,result):segment['inherited_called']=True
    cls=type('TestFuelCore',(FuelCoreMixin,Base),{})
    core=object.__new__(cls);core.time=15.
    core.env=SimpleNamespace(single_fault_fuel_curve=curve,single_fault_fuel_spec=ModelSpec(),
        _single_fault_fuel_bound_spec=None,config={'_sf2_fuel_curve':curve.as_dict()},
        plant=SimpleNamespace(gas_price_usd_per_mmbtu=1.21))
    core._check_sf2_model=lambda:None
    core.data_at=lambda t:(1,SimpleNamespace(outdoor_temperature_C=15.),np.zeros((4,3)),{})
    return core,curve


def test_interval_context_is_pure_and_shortening_restarts_from_same_source():
    core,curve=path_core();before=deepcopy(core.__dict__)
    a,start,end=core._local_interval_action(16.,{},SimpleNamespace(outdoor_temperature_C=15.))
    b,_,short=core._local_interval_action(15.5,{},SimpleNamespace(outdoor_temperature_C=15.))
    assert core.time==15. and core.__dict__.keys()==before.keys()
    assert a['_sf2_fuel_path'].start_kW==(320.,0.,0.)
    assert b['_sf2_fuel_path'].start_kW==(320.,0.,0.)
    assert b['_sf2_fuel_path'].mean_kW==tuple((start+short)/2)
    passed=core._solve(np.zeros((4,3)),{},1/120.,'fixed_execution',{},execution_action=b)
    assert passed['sf2_fuel_path'] is b['_sf2_fuel_path']
    assert '_sf2_fuel_path' not in passed['execution_action']
    with pytest.raises(ValueError,match='actual.*path|path.*actual'):
        core._solve(np.zeros((4,3)),{},1/120.,'fixed_execution',{},execution_action={'gt_electric_kW':[0.,0.,0.]})
    with pytest.raises(ValueError,match='path'):
        core._solve(np.zeros((4,3)),{},1/120.,'fixed_execution',{},execution_action=a)


def test_only_verified_accepted_segment_commits_receipt():
    from tools.single_fault_fuel_20261102.leaf import receipt
    core,curve=path_core();a,start,end=core._local_interval_action(16.,{},SimpleNamespace(outdoor_temperature_C=15.))
    path=a['_sf2_fuel_path'];raw=np.r_[path.mean_kW,np.zeros(44)]
    meter=receipt(curve,path,'actual_interval',raw,path.hours,15.,1.21)
    segment={'start_minute':15.,'end_minute':16.,'source_start_gt_kW':start.tolist(),
        'source_end_gt_kW':end.tolist(),'action':{'gt_electric_kW':list(path.mean_kW),
        'gt_fuel_MMBtu_h':sum(meter['average_running_fuel_MMBtu_h'])}}
    core.single_fault_metadata=lambda:{'physical_model_id':'SF2'}
    result={'_sf2_fuel_receipt':meter,'sf2_physical_operating_fee_USD_step':1.,'sf2_artificial_unused_steam_tie_USD_step':0.}
    # Receipt ownership requires the supplied result's final native attempt,
    # rather than a self-hashed meter vector alone.
    result['solver_backend_layers']=[dict(layer='cost',backend='gurobipy',status=0,
        attempts=[dict(accepted=True,status=0,native_status=2,raw_x=raw.tolist())])]
    core._after_actual_segment(segment,result)
    assert segment['sf2_running_fuel']['accounting_committed'] is True
    assert meter['accounting_committed'] is False
    assert segment['inherited_called']
    bad=deepcopy(segment);bad['source_end_gt_kW'][0]+=1.
    bad.pop('sf2_running_fuel')
    with pytest.raises(ValueError,match='SF2'):
        core._after_actual_segment(bad,result)
    assert 'sf2_running_fuel' not in bad


def guarded_core():
    from hospital_ies.model import load_plant
    from tools.single_fault_fuel_20261102.runtime import FuelCoreMixin,ModelSpec
    from tools.single_fault_fuel_20261102.fuel import FuelCurve
    spec=ModelSpec();curve=FuelCurve.from_source(ROOT,load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory'))
    core=object.__new__(FuelCoreMixin)
    core._sf2_spec_value=spec.as_dict();core._sf2_curve_value=curve.as_dict()
    core.env=SimpleNamespace(single_fault_fuel_spec=spec,_single_fault_fuel_bound_spec=spec,
        single_fault_fuel_curve=curve,_single_fault_fuel_bound_curve=curve,
        operating_model_config={'single_fault_fuel_model':spec.as_dict(),'single_fault_fuel_curve':curve.as_dict()},
        config={'_sf2_fuel_curve':curve.as_dict(),'_sf2_nonactual_fuel_role':'point_probe','_sf2_free_fuel_role':'free_reference_probe'})
    from functools import partial
    from hospital_ies.model import solve_hour
    core.env.reference_initialization_solver=core.env._sf2_bound_reference_solver=partial(solve_hour,sf2_fuel_config=curve.as_dict())
    return core


@pytest.mark.parametrize('method',['build','identity','from_config'])
def test_loaded_path_builder_and_curve_properties_cannot_be_replaced(monkeypatch,method):
    from tools.single_fault_fuel_20261102.fuel import FuelCurve,FuelPath
    core=guarded_core();core._check_sf2_model()
    if method=='build':monkeypatch.setattr(FuelPath,'build',classmethod(lambda *a,**k:None))
    elif method=='from_config':monkeypatch.setattr(FuelCurve,'from_config',classmethod(lambda *a,**k:None))
    else:monkeypatch.setattr(FuelCurve,'identity',property(lambda self:'0'*64))
    with pytest.raises(ValueError,match='SF2.*loaded'):
        core._check_sf2_model()


@pytest.mark.parametrize('change',['spec','config','curve','instance_core_method','leaf_alias'])
def test_frozen_spec_source_config_core_and_alias_fail_closed(monkeypatch,change):
    from tools.single_fault_fuel_20261102 import leaf
    core=guarded_core();core._check_sf2_model()
    if change=='spec':core.env.single_fault_fuel_spec=replace(core.env.single_fault_fuel_spec)
    elif change=='curve':core.env.single_fault_fuel_curve=replace(core.env.single_fault_fuel_curve)
    elif change=='config':core.env.config['_sf2_fuel_curve']['partload_multiplier']=1.
    elif change=='instance_core_method':core._local_interval_action=lambda *a:None
    else:monkeypatch.setattr(leaf,'FuelCurve',object)
    with pytest.raises(ValueError,match='SF2'):
        core._check_sf2_model()
