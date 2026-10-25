"""Explicit hypothetical stress branch; no solver, reset, trajectory or OEM claim.

The real GT output stays 125.75 kW, below its original 320 kW rating. Larger
connected demand makes the *unmet-demand request proxy* exceed 1.05. These
tests must not be described as an actual electrical overcurrent experiment.
"""
import copy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from hospital_ies.model import load_plant
from hospital_ies.resilience import RuntimeState, empty_faults, pre_control_cascade
from hospital_ies.state_transition import clean
from tools.model_closure_v22.environment import EngineeringCore
from tools.model_closure_v22.station import StationState
from tools.sci_engineering.storage import FiniteStore


class CurrentOnlyEnvironment(SimpleNamespace):
    @property
    def scenario(self):
        raise AssertionError('The stress method must not inspect any scenario or future script')

    @property
    def model_closure_events(self):
        raise AssertionError('The stress method must not inspect future event schedules')


@pytest.fixture
def current_proxy():
    plant = load_plant(ROOT / 'configs/plant_configs.json', 'doe_capacity_matched_exploratory')
    config = json.loads((ROOT / 'configs/resilience_assumptions.json').read_text(encoding='utf-8'))
    assert plant.gt_rated_kW == 320.
    assert config['gt_protection_trip_multiple'] == 1.05
    assert config['gt_overload_hold_minutes'] == 15.
    state = RuntimeState(np.array([125.75, 0., 0.]), 600., set(), np.zeros(3), {'unit_room': .25},
        auxiliary_request_kW=0., cooling_reference_kW=0.)
    core = EngineeringCore.__new__(EngineeringCore)
    core.env = CurrentOnlyEnvironment(plant=plant, state=state, cascade_config=copy.deepcopy(config),
        config=copy.deepcopy(config), closure_config={'research_stress_proxy': True})
    core.time = 120.
    core.ups = FiniteStore(dict(capacity_kWh=40., power_kW=160., initial_soc=1.))
    core.cold_store = FiniteStore(dict(capacity_kWh=60., power_kW=120., initial_soc=1.))
    core.station = StationState()
    core._m22_gt_progress = {1: 2.5}
    core.diesel_rotor = SimpleNamespace(marker='unchanged; no rotor integration in this test')
    core.events = []; core.segments = []; core.solver_calls = []
    queried = []

    def data_at(minute):
        assert minute == core.time  # No future timestamp query is permitted.
        queried.append(minute)
        availability = empty_faults()
        availability.update(GRID=0., GT_2=0., GT_3=0.)
        for device in state.extra_faulted:
            availability[device] = 0.
        return 1, None, None, availability

    core.data_at = data_at
    return core, queried


def connected_request(total_electric_kW=340.):
    loads = np.zeros((4, 3))
    loads[0, 0] = 100.
    loads[3, 0] = total_electric_kW - 100.
    return loads


def non_timer_state(core):
    state = clean(asdict(core.state))
    state.pop('overload_minutes')
    return dict(state=state, time=core.time, ups=core.ups.observe(), cold=core.cold_store.observe(),
        station=core.station.observe(), startup=copy.deepcopy(core._m22_gt_progress),
        rotor=vars(core.diesel_rotor).copy(), config=copy.deepcopy(core.env.cascade_config),
        closure=copy.deepcopy(core.env.closure_config), events=copy.deepcopy(core.events),
        segments=copy.deepcopy(core.segments), solver_calls=copy.deepcopy(core.solver_calls))


def test_enabled_proxy_triggers_only_after_original_hold_while_actual_GT_is_below_rating(current_proxy):
    core, queried = current_proxy
    loads = connected_request()
    original_loads = loads.copy(); before = non_timer_state(core)
    trips, stress, response = core._protection(loads, 0.)
    assert trips == [] and stress['GT_1'] == pytest.approx(340. / 320.)
    assert response['precontrol_GT_transfer_kW'] == pytest.approx(340. - 125.75)
    assert core.state.overload_minutes.tolist() == [0., 0., 0.]
    assert core._protection(loads, 14.)[0] == []
    assert core.state.overload_minutes.tolist() == [14., 0., 0.]
    assert core._protection(loads, 1.)[0] == ['GT_1']
    assert core.state.overload_minutes.tolist() == [15., 0., 0.]
    # The method proposes a trip; the actual execution hook commits it later.
    assert core.state.extra_faulted == set()
    assert core.state.gt_prev[0] == 125.75 < core.env.plant.gt_rated_kW
    assert non_timer_state(core) == before
    np.testing.assert_array_equal(loads, original_loads)
    assert queried == [core.time] * 3


def test_just_below_original_threshold_does_not_trigger_and_resets_timer(current_proxy):
    core, _ = current_proxy
    core.state.overload_minutes[0] = 14.
    before = non_timer_state(core)
    # 335.68 / 320 = 1.049: no changed threshold, hold, capacity or tolerance.
    trips, stress, response = core._protection(connected_request(335.68), 15.)
    assert stress['GT_1'] == pytest.approx(1.049)
    assert stress['GT_1'] < core.env.cascade_config['gt_protection_trip_multiple']
    assert not response['GT_1_protection_threshold_exceeded']
    assert trips == [] and core.state.overload_minutes.tolist() == [0., 0., 0.]
    assert non_timer_state(core) == before


def test_existing_P3_contact_reduction_avoids_proxy_without_changing_critical_request_or_source(current_proxy):
    core, _ = current_proxy
    original = connected_request()
    # The registered JSON leaves this optional key absent; the original proxy
    # explicitly defaults P3/P2/P1 to one. Do not invent a new permission.
    shares = core.env.cascade_config.get('precontrol_sheddable_fraction_by_priority',
        {'P3': 1., 'P2': 1., 'P1': 1.})
    assert shares['P3'] == 1.
    assert core._protection(original, 0.)[1]['GT_1'] > 1.05
    # A permitted P3 contact reduction: 5 kW, below its 240 kW request.
    # This supplies the current connected-load input; it is not a new fast path
    # or a claim that an ordinary queued command has already arrived.
    core.state.disconnected_loads[3, 0] = 5.
    connected = original-core.state.disconnected_loads
    assert connected[0, 0] == original[0, 0] == 100.
    assert 0. < core.state.disconnected_loads[3, 0] < original[3, 0]
    before = non_timer_state(core)
    trips, stress, response = core._protection(connected, 15.)
    assert stress['GT_1'] == pytest.approx(335. / 320.)
    assert stress['GT_1'] < 1.05
    assert trips == [] and core.state.overload_minutes.tolist() == [0., 0., 0.]
    assert response['precontrol_load_shed_kW'] == 0.  # No hidden automatic shedding.
    assert core.state.gt_prev[0] == 125.75
    assert non_timer_state(core) == before
    np.testing.assert_array_equal(connected+core.state.disconnected_loads, original)


def test_interrupted_stress_resets_hold_and_committed_trip_cannot_auto_recover(current_proxy):
    core, _ = current_proxy
    high = connected_request(); low = connected_request(335.68)
    before = non_timer_state(core)
    assert core._protection(high, 14.)[0] == []
    assert core._protection(low, .5)[0] == []
    assert core.state.overload_minutes[0] == 0.
    assert core._protection(high, 14.)[0] == []
    assert core._protection(high, 1.)[0] == ['GT_1']
    assert non_timer_state(core) == before
    # The original pure kernel's explicit commit interface applies the proposed
    # IDs. Actual advance commits these IDs only after its valid energy segment.
    availability = core.data_at(core.time)[3]
    trips, _, _ = pre_control_cascade(core.state, availability, high, core.env.plant,
        dict(core.env.cascade_config, precontrol_load_shed_enabled=False), 0., False,
        grid_import_limit_kW=0., diesel_response_kW=0., apply_trips=True)
    assert trips == ['GT_1'] and core.state.extra_faulted == {'GT_1'}
    committed = non_timer_state(core)
    trips, stress, _ = core._protection(low, 60.)
    assert trips == [] and stress == {}
    assert core.state.overload_minutes.tolist() == [0., 0., 0.]
    assert core.state.extra_faulted == {'GT_1'}
    assert non_timer_state(core) == committed


def test_default_isolation_switch_bypasses_proxy_without_other_mutations(current_proxy):
    core, queried = current_proxy
    core.env.closure_config['research_stress_proxy'] = False
    core.state.overload_minutes[0] = 14.
    before = non_timer_state(core)
    trips, stress, debug = core._protection(connected_request(), 15.)
    assert trips == [] and stress == {} and debug['research_stress_proxy_enabled'] is False
    assert core.state.overload_minutes.tolist() == [0., 0., 0.]
    assert non_timer_state(core) == before
    assert queried == []
