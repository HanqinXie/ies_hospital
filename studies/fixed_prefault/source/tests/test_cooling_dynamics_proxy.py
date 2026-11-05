"""Small physical-interface checks; no optimization or scientific episodes."""
import importlib
import math

import pytest


def api():
    try:
        return importlib.import_module('tools.cooling_dynamics_20261104.dynamics')
    except ModuleNotFoundError:
        pytest.fail('Cooling dynamics preview/commit interface has not been implemented')


def test_constant_target_energy_and_endpoint_independent_of_partition():
    cls = api().CoolingDynamics
    whole = cls(initial_cooling_kW=[0., 0., 0., 0.])
    split = cls(initial_cooling_kW=[0., 0., 0., 0.])
    target = [300., 600., 200., 0.]
    full = whole.preview(4., target, {})
    whole.commit_actual(full['mean_cooling_limits_kW'], 4., target, {})
    total = [0.] * 4
    for _ in range(8):
        part = split.preview(.5, target, {})
        split.commit_actual(part['mean_cooling_limits_kW'], .5, target, {})
        total = [a + b * .5 for a, b in zip(total, part['mean_cooling_limits_kW'])]
    assert total == pytest.approx([q * 4 for q in full['mean_cooling_limits_kW']], rel=1e-10)
    assert split.observe()['cooling_kW'] == pytest.approx(whole.observe()['cooling_kW'], rel=1e-10)


def test_hard_trip_zero_output_then_repair_rebuilds_without_reset():
    cls = api().CoolingDynamics
    state = cls(initial_cooling_kW=[300., 600., 200., 0.])
    faults = {'ABS': 0., 'TOWER': 0., 'AUX_CHW_PUMP': 0.}
    down = state.preview(1., [300., 600., 200., 0.], faults)
    assert down['mean_cooling_limits_kW'][0] == 0.
    assert down['tower_mean_factor'] == down['pump_mean_factor'] == 0.
    state.commit_actual([0., 0., 0., 0.], 1., [300., 600., 200., 0.], faults)
    restored = state.preview(1., [300., 600., 200., 0.], {})
    assert 0. < restored['mean_cooling_limits_kW'][0] < 300.
    assert 0. < restored['tower_mean_factor'] < 1.
    assert 0. < restored['pump_mean_factor'] < 1.


def test_trial_is_pure_and_unserved_devices_do_not_prewarm():
    cls = api().CoolingDynamics
    state = cls(initial_cooling_kW=[0., 0., 0., 0.])
    before = state.observe()
    trial = state.preview(1., [300., 600., 200., 0.], {})
    assert state.observe() == before
    state.commit_actual([0., 0., 0., 0.], 1., [300., 600., 200., 0.], {})
    assert state.observe()['cooling_kW'] == [0., 0., 0., 0.]
    assert state.observe()['tower_ready'] == state.observe()['pump_ready'] == 0.
    with pytest.raises(ValueError, match='dynamic'):
        state.commit_actual([1e9, 0., 0., 0.], 1., [300., 600., 200., 0.], {})


def test_input_validation_and_partial_availability_not_double_derated():
    cls = api().CoolingDynamics
    state = cls(initial_cooling_kW=[100., 0., 0., 0.])
    # Reference is already current-capacity feasible; raw availability is not
    # multiplied into it a second time by the dynamic capability wrapper.
    got = state.preview(1., [100., 0., 0., 0.], {'ABS': .5, 'TOWER': .5})
    assert got['mean_cooling_limits_kW'][0] == 100.
    assert got['tower_mean_factor'] == 1.
    for dt in (0., -1., math.inf):
        with pytest.raises(ValueError):
            state.preview(dt, [100., 0., 0., 0.], {})
