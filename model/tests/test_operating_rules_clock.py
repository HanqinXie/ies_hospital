"""Explicit communication clocks must not depend on research category labels."""
import copy, importlib, json, sys
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def api():
    try:return importlib.import_module('tools.operating_rules_study.environment')
    except ModuleNotFoundError as exc:pytest.fail('Explicit study communication clock is missing: '+str(exc))

def scene(**overrides):
    s=dict(stage=1,fault_start_minute=60.,fault_duration_minutes=0.,faults={},auxiliary_faults={},
           shock_start_minute=60.,shock_duration_minutes=0.,shock={},shock_by_priority={},shock_by_zone={},
           response_delay_minutes=15.,duration_steps=12,step_minutes=15.,model_closure_events=[],exogenous_events=[])
    s.update(overrides);return s

def test_no_physical_event_has_no_event_triggered_wait():
    mod=api();s=scene()
    assert mod.response_windows(s,'event_triggered')==[]
    assert mod.response_windows(s,'registered_window')==[(60.,75.)]

def test_fault_and_demand_each_trigger_their_observed_event_clock():
    mod=api();s=scene(faults={'GT_1':0.},fault_start_minute=52.5,fault_duration_minutes=30.,
                    shock_by_zone={'ICU_FLR_2':{'e':.25}},shock_start_minute=82.5,shock_duration_minutes=30.)
    assert mod.response_windows(s,'event_triggered')==[(52.5,67.5),(82.5,97.5)]
    for stage in (1,2,3,4):
        changed=dict(s,stage=stage)
        assert mod.response_windows(changed,'event_triggered')==mod.response_windows(s,'event_triggered')

def test_overlapping_windows_merge_and_zero_delay_is_instantaneous():
    mod=api();s=scene(faults={'GT_1':0.},fault_start_minute=60.,fault_duration_minutes=30.,
                    shock={'c':.25},shock_start_minute=67.5,shock_duration_minutes=30.)
    assert mod.response_windows(s,'event_triggered')==[(60.,82.5)]
    assert mod.response_windows(dict(s,response_delay_minutes=0.),'event_triggered')==[]

def test_clock_does_not_claim_full_second_delay_after_late_command():
    mod=api()
    class Base:
        def _next_boundary(self,end):return end
    class Core(mod.StudyClockMixin,Base):pass
    core=Core();core.env=SimpleNamespace(scenario=scene(faults={'GRID':0.},fault_duration_minutes=60.),study_clock_contract='event_triggered')
    core.time=67.5
    assert core.delayed_at(67.5)==(True,75.)
    assert core._next_boundary(90.)==75.
    assert core.delayed_at(75.)==(False,75.)

def test_future_second_event_cannot_change_current_command_release():
    mod=api()
    class Base:
        def _next_boundary(self,end):return end
    class Core(mod.StudyClockMixin,Base):pass
    s=scene(faults={'GT_1':0.},fault_duration_minutes=30.,shock={'c':.25},
            shock_start_minute=67.5,shock_duration_minutes=30.)
    core=Core();core.env=SimpleNamespace(scenario=s,study_clock_contract='event_triggered');core.time=60.
    assert core.delayed_at(60.)==(True,75.)
    assert core._next_boundary(90.)==67.5
    core.time=67.5
    assert core.delayed_at(67.5)==(True,82.5)
    core.env.scenario=dict(s,shock_start_minute=90.)
    assert core.delayed_at(60.)==(True,75.)

@pytest.mark.parametrize('bad',[-1.,float('nan'),True])
def test_invalid_wait_rejected(bad):
    with pytest.raises(ValueError):api().response_windows(scene(response_delay_minutes=bad),'event_triggered')

def test_historical_default_clock_remains_registered_even_without_fault():
    from hospital_ies.state_transition import TransitionCore
    core=object.__new__(TransitionCore)
    core.env=SimpleNamespace(scenario=scene(),dt_minutes=15.)
    assert core.delayed_at(60.)==(True,75.)
