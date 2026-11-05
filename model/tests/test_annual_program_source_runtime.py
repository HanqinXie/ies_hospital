"""No endpoint solve/data clamp and no unsigned scene substitution at reset."""
import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from tools.annual_program.registration import make_case
from tools.sci_engineering.design import SEALED_DAYS


def implementation():
    try:return importlib.import_module('tools.annual_program.source_runtime')
    except ModuleNotFoundError as exc:
        if exc.name!='tools.annual_program.source_runtime':raise
        pytest.fail('Explicit annual runtime source boundary is missing',pytrace=False)


def test_reset_matches_actual_scene_calendar_and_explicit_support():
    m=implementation();case=make_case('M_ready')
    scene=copy.deepcopy(case['scenario']);scene.pop('model_closure_events')
    env=NS(annual_program_data_context=dict(case=case),allowed_hours=frozenset(range(1,8761)),
        model_closure_events=[],input_access_receipt={'annual_program_access':{'case_sha256':m.digest(case)}})
    assert m.validate_reset_scene(env,scene)==set(range(1,8761))
    bad=copy.deepcopy(scene);bad['response_delay_minutes']=0.
    with pytest.raises(ValueError):m.validate_reset_scene(env,bad)
    env.model_closure_events=[dict(time_minute=60.,availability={'GRID':0.})]
    with pytest.raises(ValueError):m.validate_reset_scene(env,scene)


def test_only_exact_source_end_skips_terminal_boundary():
    m=implementation()
    class Parent:
        def boundary(self):self.calls+=1;return 'actual boundary'
        def data_at(self,t):return ('actual source',t)
    class Core(m.AnnualSourceMixin,Parent):pass
    core=Core();core.calls=0;core.env=NS(scenario={'start_hour':8760});core.time=59.75
    assert core.boundary()=='actual boundary' and core.calls==1
    assert core.data_at(59.75)==('actual source',59.75)
    core.time=60.
    assert core.boundary() is None and core.calls==1
    with pytest.raises(ValueError):core.data_at(60.)
    core.time=60.25
    with pytest.raises(ValueError):core.boundary()


def test_normal_short_window_keeps_original_endpoint_boundary():
    m=implementation()
    class Parent:
        def boundary(self):self.calls+=1
    class Core(m.AnnualSourceMixin,Parent):pass
    core=Core();core.calls=0;core.env=NS(scenario={'start_hour':1});core.time=180.
    core.boundary();assert core.calls==1


def test_terminal_diagnostic_row_context_names_the_exact_last_actual_interval():
    m=implementation()
    class Parent:pass
    class Core(m.AnnualSourceMixin,Parent):pass
    core=Core();core.env=NS(scenario={'start_hour':8760});core.time=60.
    exact_start=59.7499999
    core.segments=[dict(start_minute=exact_start,end_minute=60.)]
    context=core._annual_error_row_context()
    assert context['query_minute']==exact_start
    assert context['actual_error_time_minute']==60.
    assert context['source_interval_end_absolute_minute']==m.YEAR_MINUTES
    assert context['source']=='last_completed_actual_interval'
    core.env.scenario['start_hour']=1
    assert core._annual_error_row_context() is None


def test_original_sci_reset_accepts_full_year_only_under_checked_annual_context(monkeypatch):
    import tools.sci_engineering.environment as legacy
    case=make_case('M_ready');scene=copy.deepcopy(case['scenario']);scene.pop('model_closure_events')
    env=NS(annual_program_data_context=dict(case=case),allowed_hours=set(range(1,8761)),
        sealed_days=set(SEALED_DAYS),model_closure_events=[],root=Path('.'),
        input_access_receipt={'annual_program_access':{'case_sha256':implementation().digest(case)}},
        default_diesel_initial_energy_kWh=2400.,config={})
    def native_reset(self,*,scenario,seed=None):return ('actual original initialization',scenario['duration_steps'])
    monkeypatch.setattr(legacy.NativeEnvironment,'reset',native_reset)
    assert legacy.OperatingHospitalIESEnv.reset(env,scenario=scene)==('actual original initialization',35040)


def test_actual_detached_calendar_keeps_registered_wait_and_release_boundary():
    from tools.operating_rules_study.environment import StudyClockMixin
    m=implementation()
    class Parent:
        def _next_boundary(self,end):return end
    class OldClock(StudyClockMixin,Parent):pass
    class Clock(m.AnnualSourceMixin,StudyClockMixin,Parent):pass
    scene=dict(start_hour=1,stage=1,response_delay_minutes=15.,faults={},auxiliary_faults={},
        shock={},shock_by_priority={},shock_by_zone={})
    events=[dict(time_minute=60.25,availability={'GRID':0.}),
        dict(time_minute=120.25,availability={'GRID':1.}),
        dict(time_minute=180.,availability={'GRID':0.})]
    env=NS(scenario=scene,model_closure_events=events,study_clock_contract='event_triggered')
    old=OldClock();old.env=env;old.time=60.25
    assert old.delayed_at(60.25)==(False,60.25) # reproduced historical omission
    core=Clock();core.env=env;core.time=60.25
    assert core.delayed_at(60.25)==(True,75.25)
    assert core._next_boundary(90.)==75.25
    assert core.delayed_at(75.25)==(False,75.25)
    assert core.delayed_at(180.)==(True,195.)
    assert 'model_closure_events' not in env.scenario


def test_future_calendar_onset_cannot_extend_current_response_wait():
    from tools.operating_rules_study.environment import StudyClockMixin
    m=implementation()
    class Parent:pass
    class Clock(m.AnnualSourceMixin,StudyClockMixin,Parent):pass
    core=Clock();core.env=NS(scenario=dict(start_hour=1,stage=1,response_delay_minutes=15.,
        faults={},auxiliary_faults={},shock={},shock_by_priority={},shock_by_zone={}),
        model_closure_events=[dict(time_minute=60.,availability={'GRID':0.}),
            dict(time_minute=65.,availability={'GRID':.5}),
            dict(time_minute=66.,availability={'GRID':0.})],study_clock_contract='event_triggered')
    assert core.delayed_at(61.)==(True,75.)
    assert core.delayed_at(66.)==(True,81.)


def test_final_actual_witness_runs_after_native_commit_without_requery(monkeypatch):
    from tools.annual_program import witness
    m=implementation();order=[];actual={'cost':object()};segment={'end_minute':1.}
    class Parent:
        def _after_actual_segment(self,row,result):
            assert row is segment and result is actual
            order.append('native');row['native_commit']=True
            return 'unchanged native return'
        def observe(self):pytest.fail('A final-primal witness must not request another observation')
        def fresh_current_context(self):pytest.fail('A final-primal witness must not solve again')
    class Core(m.AnnualSourceMixin,Parent):pass
    core=Core()
    def record(owner,row,result):
        assert owner is core and row is segment and result is actual and row['native_commit']
        order.append('witness')
    monkeypatch.setattr(witness,'record_actual_witness',record)
    assert core._after_actual_segment(segment,actual)=='unchanged native return'
    assert order==['native','witness']


def test_annual_error_hook_routes_exact_native_arguments_without_copying(monkeypatch):
    from tools.annual_program import failure_history
    m=implementation();args=(RuntimeError('native failure'),object(),object(),.25,'actual',object(),2.)
    class Parent:
        def _record_error(self,*args,**kwargs):pytest.fail('Annual error migration must use the pinned native adapter')
    class Core(m.AnnualSourceMixin,Parent):pass
    core=Core();seen=[]
    def record(owner,*received,**kwargs):
        assert owner is core and all(x is y for x,y in zip(received,args))
        seen.append(kwargs);return 'native record result'
    monkeypatch.setattr(failure_history,'record_error_with_history_refs',record)
    assert core._record_error(*args,marker='original')=='native record result'
    assert seen==[{'marker':'original'}]
