"""Synthetic local adapter contracts; no native constructor or physical solve."""
from copy import deepcopy
import importlib
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from tools.annual_program.source_runtime import AnnualSourceMixin
from tools.operating_rules_study.environment import StudyClockMixin
from tools.sci_engineering.design import SEALED_DAYS, allowed_hours


def api():
    assert (ROOT/'tools/eight_fault_study/runtime.py').is_file(), 'local actual-witness adapter is missing'
    return importlib.import_module('tools.eight_fault_study.runtime')


class Native:
    def _next_boundary(self, end): return end
    def _after_actual_segment(self, segment, result):
        segment['native_commit'] = result
        return 'committed'
    def data_at(self, minute): return ('native row', minute)
    def boundary(self): return 'native boundary'
    def _record_error(self, *args, **kwargs): return ('native error', args, kwargs)
    def _annual_error_row_context(self): return None


def clock(cls, events):
    core = cls()
    core.time = 60.25
    core.env = SimpleNamespace(scenario={'stage': 0, 'response_delay_minutes': 15.},
        study_clock_contract='event_triggered', model_closure_events=events)
    return core


def test_detached_calendar_counterexample_and_local_release():
    events = [{'time_minute':60.25, 'availability':{'EC_1':0.}}]
    old = clock(type('Old', (StudyClockMixin, Native), {}), events)
    assert old.delayed_at(60.25) == (False, 60.25)
    core = clock(type('Local', (api().LocalActualCoreMixin, StudyClockMixin, Native), {}), events)
    assert core.delayed_at(60.25) == (True, 75.25)
    assert core._next_boundary(90.) == 75.25
    assert core.delayed_at(75.25) == (False, 75.25)
    assert isinstance(core, AnnualSourceMixin)


def test_future_onset_only_splits_physics_and_cannot_extend_current_wait():
    events = [{'time_minute':60.25, 'availability':{'EC_1':0.}},
        {'time_minute':70., 'availability':{'ABS':0.}}]
    core = clock(type('Local', (api().LocalActualCoreMixin, StudyClockMixin, Native), {}), events)
    assert core.delayed_at(60.25) == (True, 75.25)
    assert core._next_boundary(90.) == 70.
    assert core.delayed_at(70.) == (True, 85.)


def test_native_short_window_source_and_error_methods_are_not_annualized():
    core = clock(type('Local', (api().LocalActualCoreMixin, Native), {}), [])
    assert core.delayed_at(60.25) == (False, 60.25)
    assert core._next_boundary(90.) == 90.
    assert core.data_at(525600.) == ('native row', 525600.)
    assert core.boundary() == 'native boundary'
    assert core._record_error('failed', detail={'unknown':None}) == ('native error', ('failed',), {'detail':{'unknown':None}})
    assert core._annual_error_row_context() is None


@pytest.fixture
def registered():
    from tools.annual_readiness.registration import load_registry, make_case, worker_registry
    from tools.annual_readiness.runner import _bindings
    registry = load_registry()
    case = make_case(registry, 'b07_L4_GT_ER_disturbed_M', 'M_available', mode='N',
        start_hour=33, calendar=[{'time_minute':60.25, 'availability':{'EC_1':0.}},
                                {'time_minute':90.25, 'availability':{'EC_1':1.}}])
    return SimpleNamespace(case=case, registry=worker_registry(case,registry), bindings=_bindings())


def context(registered):
    config = api().runtime_configuration(registered.case, registered.registry)
    value = api().create_source_context(ROOT, case=registered.case,
        configuration=config, bindings=registered.bindings)
    return value, config


def fake_adapter(calls):
    def create(root, **kw):
        # Exercise the real constructor's strict rule parser before this
        # synthetic environment does anything, without constructing a model.
        from tools.model_correction_v25.reserve import operating_rule_config
        rule=operating_rule_config(kw['operating_rule'])
        calls.append(('construct', deepcopy(kw)))
        env = SimpleNamespace(root=Path(root), allowed_hours=set(kw['allowed_hours']),
            sealed_days=set(kw['sealed_days']), annual_program_data_context=None,
            sci_final_release_receipt=None, operating_model_config=deepcopy(kw['model_config']),
            config={'diesel_initial_energy_kWh':3., 'p0_solver_feasibility_tol_kW':1e-7},
            sci_config={'service_budget':0.35},
            operating_execution_contract={'diesel_dispatch_mode':'instantaneous_current'},
            operating_rule=rule, control_rule=deepcopy(rule), control_rule_id=rule['rule_id'], plant=None,
            study_clock_contract='event_triggered', engineering_core_type=Native)
        def reset(*, scenario, seed=None):
            calls.append(('reset', deepcopy(scenario)))
            env.config['diesel_initial_energy_kWh']=float(scenario.get('initial_reserve_kWh',env.config['diesel_initial_energy_kWh']))
            env.scenario=deepcopy(scenario)
            env.model_closure_events=env.scenario.pop('model_closure_events', [])
            env.core=env.engineering_core_type()
            env.core.env=env
            return {}, {}
        env.reset=reset
        return env
    return SimpleNamespace(OperatingHospitalIESEnv=create)


def construct(registered, *, changes=None):
    value, config = context(registered)
    calls=[]
    adapter=api().with_local_runtime(fake_adapter(calls), value)
    kw=dict(allowed_hours=allowed_hours({'scenarios':[registered.case['scenario']]}),
        sealed_days=SEALED_DAYS, model_config=config['model_config'], operating_rule=config['operating_rule'])
    kw.update(changes or {})
    env=adapter.OperatingHospitalIESEnv(ROOT, **kw)
    return env, calls, value


def test_source_only_context_preserves_native_numeric_grant_and_binds_actual_reset(registered):
    env, calls, value=construct(registered)
    assert env.annual_program_data_context is None
    assert 'annual_program_data_context' not in calls[0][1]
    assert 'local_actual_witness_source_context' not in calls[0][1]
    assert value['numeric_access_granted'] is False
    env.reset(scenario=registered.case['scenario'])
    assert api().resolve_local_source_freeze(env)==registered.bindings['source']
    assert env.core.delayed_at(60.25)==(True,75.25)


@pytest.mark.parametrize('change', ['annual','final','sealed','support','config','rule'])
def test_invalid_constructor_rejected_before_native_numeric_parsing(registered,change):
    value, config=context(registered)
    calls=[]
    adapter=api().with_local_runtime(fake_adapter(calls),value)
    kw=dict(allowed_hours=allowed_hours({'scenarios':[registered.case['scenario']]}),
        sealed_days=SEALED_DAYS, model_config=deepcopy(config['model_config']), operating_rule=deepcopy(config['operating_rule']))
    if change=='annual':kw['annual_program_data_context']={'fake':'grant'}
    elif change=='final':kw['sci_final_release_receipt']={'phase':'final_blind'}
    elif change=='sealed':kw['sealed_days']=[]
    elif change=='support':kw['allowed_hours']={505}
    elif change=='config':kw['model_config']['invented']=True
    else:kw['operating_rule']['design_minutes']=999.
    with pytest.raises(ValueError):adapter.OperatingHospitalIESEnv(ROOT,**kw)
    assert calls==[]


@pytest.mark.parametrize('change', ['scene','calendar','config','physical_config','sci_config','support','sealed','annual','final','root'])
def test_actual_environment_drift_cannot_supply_source_freeze(registered,change):
    env, calls, value=construct(registered)
    env.reset(scenario=registered.case['scenario'])
    if change=='scene':env.scenario['response_delay_minutes']=0.
    elif change=='calendar':env.model_closure_events=[]
    elif change=='config':env.operating_model_config['invented']=True
    elif change=='physical_config':env.config['p0_solver_feasibility_tol_kW']=999.
    elif change=='sci_config':env.sci_config['service_budget']=1.
    elif change=='support':env.allowed_hours={1}
    elif change=='sealed':env.sealed_days=set()
    elif change=='annual':env.annual_program_data_context={}
    elif change=='final':env.sci_final_release_receipt={'phase':'final_blind'}
    else:env.root=ROOT.parent
    with pytest.raises(ValueError):api().resolve_local_source_freeze(env)


def test_noncanonical_case_and_wrong_bindings_rejected(registered):
    value, config=context(registered)
    bad=deepcopy(registered.case);bad['scenario']['response_delay_minutes']=0.
    with pytest.raises(ValueError):api().create_source_context(ROOT,case=bad,configuration=config,bindings=registered.bindings)
    bad_bindings=deepcopy(registered.bindings)
    bad_bindings['source']['tools/annual_program/source_runtime.py']='a'*64
    with pytest.raises(ValueError):api().create_source_context(ROOT,case=registered.case,configuration=config,bindings=bad_bindings)


def test_reset_scene_checked_before_native_reset(registered):
    env, calls, value=construct(registered)
    scene=deepcopy(registered.case['scenario']);scene['model_closure_events']=[]
    with pytest.raises(ValueError):env.reset(scenario=scene)
    assert len(calls)==1


@pytest.mark.parametrize('when',['before_reset','during_reset','after_reset'])
def test_actual_operating_execution_contract_drift_rejected(registered,when):
    value, config=context(registered)
    calls=[];original=fake_adapter(calls)
    if when=='during_reset':
        create=original.OperatingHospitalIESEnv
        def changing_create(root,**kwargs):
            env=create(root,**kwargs)
            reset=env.reset
            def changed_reset(**kw):
                returned=reset(**kw)
                env.operating_execution_contract['diesel_dispatch_mode']='unregistered-mode'
                return returned
            env.reset=changed_reset
            return env
        original.OperatingHospitalIESEnv=changing_create
    adapter=api().with_local_runtime(original,value)
    env=adapter.OperatingHospitalIESEnv(ROOT,allowed_hours=value['allowed_hours'],
        sealed_days=SEALED_DAYS,model_config=config['model_config'],operating_rule=config['operating_rule'])
    if when=='before_reset':
        env.operating_execution_contract['diesel_dispatch_mode']='unregistered-mode'
        with pytest.raises(ValueError,match='execution parameters'):env.reset(scenario=registered.case['scenario'])
        assert len(calls)==1
    elif when=='during_reset':
        with pytest.raises(ValueError,match='execution parameters'):env.reset(scenario=registered.case['scenario'])
    else:
        env.reset(scenario=registered.case['scenario'])
        env.operating_execution_contract['diesel_dispatch_mode']='unregistered-mode'
        with pytest.raises(ValueError,match='execution parameters'):api().resolve_local_source_freeze(env)


@pytest.mark.parametrize('source_id,hour,device', [
    ('b03_L2_tower_disturbed_M',4137,None), ('b03_L2_tower_disturbed_M',4137,'TOWER'),
    ('b07_L4_GT_ER_disturbed_M',33,None), ('b07_L4_GT_ER_disturbed_M',33,'GT_3')])
def test_first_batch_ready_h2_metadata_matches_frozen_local_configuration(source_id,hour,device):
    from tools.annual_readiness.registration import load_registry, make_case, worker_registry
    registry=load_registry()
    events=[] if device is None else [dict(time_minute=60.,availability={device:0.}),
                                      dict(time_minute=120.,availability={device:1.})]
    case=make_case(registry,source_id,'M_ready',mode='N',start_hour=hour,
        configuration='H2',calendar=events,duration_minutes=180.)
    configuration=api().runtime_configuration(case,worker_registry(case,registry))
    # Constructor input remains the original strict name/design_minutes pair;
    # the actual environment derives rule_id after its unchanged parser.
    assert configuration['operating_rule']==dict(name='sustained_critical',design_minutes=60.)
    assert configuration['control_rule_id']=='sustained_critical_60min'
    assert configuration['model_config']['model_closure_v22']['station']['boiler_restart_minutes']==1.
    assert case['scenario']['response_delay_minutes']==15.
    assert case['annual_readiness']['parameters']['requalification_minutes'] is None
    assert not any((h-1)//24 in SEALED_DAYS for h in allowed_hours({'scenarios':[case['scenario']]}))


@pytest.fixture
def source_faithful_reserve(monkeypatch):
    """Real M25/M22 configuration path with every solver edge replaced."""
    import math
    import numpy as np
    from tools.model_correction_v25 import reserve
    from tools.model_closure_v22.environment import EngineeringCore as ClosureCore
    from tools.sci_engineering.environment import EngineeringCore as SciCore
    calls=[]
    def forbidden(*args,**kwargs):raise AssertionError('Synthetic configuration check called a solver/observer')
    def generated(self,action):
        calls.append(('native_generate',action))
        return {'id':'synthetic-command'}
    monkeypatch.setattr(SciCore,'generate_command',generated)
    monkeypatch.setattr(reserve,'reserve_status',lambda core,loads,f:
        {'ups':{'uncapped_target_kWh':10.},'cold':{'uncapped_target_kWh':40.}})
    class Core(reserve.SustainedReserveMixin,ClosureCore):
        def data_at(self,minute):return 1,None,np.ones((4,3)),{'GRID':1.}
        def _select_current_ports(self,loads,f):calls.append(('select_ports',self.time))
        def _storage_ports(self,dt_h,f):return {'synthetic_dt_h':dt_h}
        def _current_context(self,f):return {'synthetic_current_clock':self.time}
        _solve=observe=_storage_solver=forbidden
    # Bypass native constructors: this object only exercises the two original
    # methods named above and real solver_context's stock/duration arithmetic.
    core=object.__new__(Core);core.time=0.
    core.env=SimpleNamespace(config={'fixed_capacity_kWh':60.},dt_h=.25,
        operating_rule={'name':'sustained_critical','design_minutes':60.,'rule_id':'sustained_critical_60min'},
        model_correction_config={'finite_source_return':True},
        operating_execution_contract={'diesel_dispatch_mode':'arrived_ceiling'})
    parameters={'charge_efficiency':.95,'discharge_efficiency':.95,'loss_per_hour':.01}
    core.ups=SimpleNamespace(energy_kWh=0.,config=deepcopy(parameters))
    core.cold_store=SimpleNamespace(energy_kWh=60.,config=deepcopy(parameters))
    return SimpleNamespace(core=core,calls=calls,math=math)


def test_real_sustained_command_context_is_not_static_parameter_drift(source_faithful_reserve):
    fixture=source_faithful_reserve;core=fixture.core
    before=api()._execution_configuration(core.env)
    action={'synthetic_action':True}
    command=core.generate_command(action)
    context=core.env.config['_model_correction_v25_reserve']
    assert command['id']=='synthetic-command'
    assert fixture.calls==[('select_ports',0.),('native_generate',action)]
    assert context['cold']['initial_energy_kWh']==60.
    assert context['cold']['retained_energy_kWh']==60.*fixture.math.exp(-.01*.25)
    assert api()._execution_configuration(core.env)==before


def test_actual_subsegment_context_can_differ_from_older_command_cache(source_faithful_reserve):
    fixture=source_faithful_reserve;core=fixture.core
    before=api()._execution_configuration(core.env)
    core.generate_command({'synthetic_action':True})
    command_cache=deepcopy(core.env.config['_model_correction_v25_reserve'])
    core.time=.25;core.cold_store.energy_kWh=59.99750005208261
    actual=core._physical_solver_config(.25/60.,{'GRID':1.})['_model_correction_v25_reserve']
    assert actual['cold']['initial_energy_kWh']==core.cold_store.energy_kWh
    assert actual['cold']['retained_energy_kWh']==core.cold_store.energy_kWh*fixture.math.exp(-.01*.25/60.)
    assert actual!=command_cache
    assert core.env.config['_model_correction_v25_reserve']==command_cache
    assert core.env.dt_h==.25  # The original 15 minute decision grid stays fixed.
    assert api()._execution_configuration(core.env)==before


def _ready_registration(registered):
    from tools.annual_readiness.registration import load_registry,make_case,worker_registry
    registry=load_registry()
    case=make_case(registry,'b07_L4_GT_ER_disturbed_M','M_ready',mode='N',start_hour=33,configuration='H2')
    return SimpleNamespace(case=case,registry=worker_registry(case,registry),bindings=registered.bindings)


def test_verified_sustained_runtime_accepts_only_the_named_derived_context(registered,source_faithful_reserve):
    selected=_ready_registration(registered)
    env,calls,value=construct(selected)
    env.reset(scenario=selected.case['scenario'])
    source_faithful_reserve.core.generate_command({'synthetic_action':True})
    env.config['_model_correction_v25_reserve']=deepcopy(source_faithful_reserve.core.env.config['_model_correction_v25_reserve'])
    assert api().resolve_local_source_freeze(env)==registered.bindings['source']


@pytest.mark.parametrize('change',['capacity','efficiency','rule','static_same_name','unknown_private','operating_contract','non_sustained'])
def test_derived_reserve_context_cannot_hide_static_or_unreviewed_drift(registered,source_faithful_reserve,change):
    selected=registered if change=='non_sustained' else _ready_registration(registered)
    env,calls,value=construct(selected)
    env.reset(scenario=selected.case['scenario'])
    source_faithful_reserve.core.generate_command({'synthetic_action':True})
    context=deepcopy(source_faithful_reserve.core.env.config['_model_correction_v25_reserve'])
    env.config['_model_correction_v25_reserve']=context
    if change=='capacity':env.operating_model_config['sci_engineering']['cold_store']['capacity_kWh']+=1.
    elif change=='efficiency':env.operating_model_config['sci_engineering']['cold_store']['discharge_efficiency']=.5
    elif change=='rule':env.operating_rule['design_minutes']=30.
    elif change=='static_same_name':env.model_correction_config={'finite_source_return':True,'_model_correction_v25_reserve':context}
    elif change=='unknown_private':env.config['_unreviewed_runtime_context']={}
    elif change=='operating_contract':env.operating_execution_contract['diesel_dispatch_mode']='unregistered-mode'
    with pytest.raises(ValueError):api().resolve_local_source_freeze(env)
