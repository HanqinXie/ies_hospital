from types import SimpleNamespace
import numpy as np
import pytest
from hospital_ies import state_transition as transition


class Source:
    def __init__(self): self.calls=[]
    def at_elapsed(self, minute, start_hour):
        self.calls.append((minute,start_hour))
        groups={f'P{g}_{c}_kW': float(100*g+10*j+int(minute))
                for g in range(4) for j,c in enumerate('ehc')}
        return start_hour, SimpleNamespace(timestamp='current minute',outdoor_temperature_C=21.), groups
    def thermal_at_elapsed(self, minute, start_hour):
        self.calls.append(('thermal',minute,start_hour))
        return {'zone':SimpleNamespace(zone_multiplier=2.,minute=minute)}


def test_actual_data_uses_current_native_minute_without_touching_hourly_table(monkeypatch):
    source=Source()
    env=SimpleNamespace(scenario={'start_hour':33,'step_minutes':1.,'duration_steps':180,'stage':1,
                                 'shock_start_minute':0.,'shock_duration_minutes':0.},
                        minute_source=source,hourly=object(),groups=object(),dt_minutes=1.,state=object(),
                        execution_contract={'thermal_mode':'none'})
    core=transition.TransitionCore.__new__(transition.TransitionCore);core.env=env
    monkeypatch.setattr(transition,'scenario_faults',lambda *a,**k:{'GRID':1.})
    hour,row,loads,fault=core.data_at(1.25)
    assert source.calls==[(1.25,33)] and hour==33 and row.timestamp=='current minute'
    assert loads.shape==(4,3) and loads[2,2]==221.


def test_minute_thermal_reference_uses_same_current_clock():
    source=Source();env=SimpleNamespace(minute_source=source,scenario={'start_hour':33},
                                      thermal_cache=({'fixed':'unchanged'},{33:{'old':'hourly'}}))
    assert hasattr(transition,'thermal_source_at'),'Explicit thermal sampling interface missing'
    params,zones=transition.thermal_source_at(env,1.25,33)
    assert params=={'fixed':'unchanged'} and zones['zone'].minute==1.25
    assert source.calls==[('thermal',1.25,33)]


def test_hourly_thermal_default_is_unchanged():
    env=SimpleNamespace(thermal_cache=({'a':1},{33:{'old':True}}))
    assert hasattr(transition,'thermal_source_at'),'Explicit thermal sampling interface missing'
    assert transition.thermal_source_at(env,.5,33)==({'a':1},{'old':True})


@pytest.mark.parametrize('now,wanted',[(0.,1.),(.25,1.),(.999,1.),(1.,2.),(59.5,60.),(60.,61.)])
def test_input_boundary_prevents_one_minute_step_from_crossing_next_minute(now,wanted):
    env=SimpleNamespace(minute_source=Source())
    assert hasattr(transition,'next_input_boundary'),'Minute boundary interface missing'
    assert transition.next_input_boundary(env,now)==wanted


def test_no_input_boundary_added_for_old_hourly_source():
    assert hasattr(transition,'next_input_boundary'),'Minute boundary interface missing'
    assert transition.next_input_boundary(SimpleNamespace(),.25) is None
