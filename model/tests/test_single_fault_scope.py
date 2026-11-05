"""SF1 scope restrictions are independent of the canonical physics validator."""
from copy import deepcopy

import pytest

from tools.single_fault_model_20261102.runtime import validate_single_fault_scenario


@pytest.mark.parametrize('minutes', [7.5, 30., 0., -15., True, False, '15', float('nan'), float('inf')])
def test_sf1_scope_rejects_changed_or_undefined_control_grid(minutes):
    with pytest.raises(ValueError, match='15'):
        validate_single_fault_scenario(dict(step_minutes=minutes))


@pytest.mark.parametrize('scene', [{}, {'step_minutes':15}, {'step_minutes':15.}])
def test_sf1_scope_retains_default_original_fifteen_minute_grid(scene):
    before = deepcopy(scene)
    assert validate_single_fault_scenario(scene) is None
    assert scene == before


@pytest.mark.parametrize('name,values', [
    ('shock', {'c':.2}),
    ('shock_by_priority', {'P0':{'e':.1, 'h':0.}}),
    ('shock_by_zone', {'OR':{'c':.3}, 'ER':{'c':0.}}),
    ('shock_by_priority', {'P0':{'e':0.}, 'P3':{'c':.01}}),
    ('demand_amplitude', .25),
])
@pytest.mark.parametrize('fault', [False, True])
def test_sf1_scope_rejects_positive_external_demand_shock_with_or_without_fault(name, values, fault):
    scene = dict(step_minutes=15., faults={'ABS':0.} if fault else {})
    scene[name] = values
    with pytest.raises(ValueError, match='shock|demand'):
        validate_single_fault_scenario(scene)


def test_sf1_scope_keeps_zero_placeholders_and_single_fault_calendar_unchanged():
    scene = dict(step_minutes=15., shock={'c':0., 'e':0.},
        shock_by_priority={'P0':{'c':0., 'e':0.}, 'P3':{'h':0.}},
        shock_by_zone={'OR':{'c':0.}, 'ER':{'h':0.}}, demand_amplitude=0.,
        faults={}, auxiliary_faults={}, model_closure_events=[
            {'time_minute':60., 'availability':{'ABS':0.}},
            {'time_minute':120., 'availability':{'ABS':1.}}])
    before = deepcopy(scene)
    assert validate_single_fault_scenario(scene) is None
    assert scene == before
