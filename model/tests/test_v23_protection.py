"""Causal dependency loss and continuous, actually paid restoration."""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def test_actual_supply_loss_latches_running_device_without_claiming_damage():
    from tools.model_correctness_v23.protection import RestartState
    running = RestartState(ready=True)
    lost = running.advance(source_available=False)
    assert running.ready and not lost.ready
    assert lost.protective_latch and lost.status == 'functional_unavailable'
    assert not lost.permanent_damage


def test_dispatch_stop_is_not_supply_fault_or_permanent_damage():
    from tools.model_correctness_v23.protection import RestartState
    running = RestartState(ready=True)
    request = running.request(permission_arrived=False, source_available=True)
    stopped = running.commit(1., request, restart_power_supplied=False)
    assert not stopped.ready and not stopped.protective_latch
    assert stopped.status == 'dispatch_stopped' and not stopped.permanent_damage


def test_observed_restoration_requires_arrived_permission_and_actual_start_power():
    from tools.model_correctness_v23.protection import RestartState
    state = RestartState(ready=True).advance(source_available=False)
    no_permission = state.request(permission_arrived=False, source_available=True)
    assert not no_permission.can_restart and no_permission.requested_start_power_kW == 0.
    assert not state.commit(1., no_permission, restart_power_supplied=True).ready
    requested = state.request(permission_arrived=True, source_available=True)
    assert requested.can_restart and requested.requested_start_power_kW == .5
    unpowered = state.commit(1., requested, restart_power_supplied=False)
    assert not unpowered.ready and unpowered.continuous_powered_minutes == 0.
    restored = state.commit(1., requested, restart_power_supplied=True)
    assert restored.ready and not restored.protective_latch
    assert restored.status == 'ready' and state.continuous_powered_minutes == 0.


def test_restart_interruption_resets_continuous_paid_clock():
    from tools.model_correctness_v23.protection import RestartState
    state = RestartState().advance(source_available=True)
    request = state.request(permission_arrived=True, source_available=True)
    half = state.commit(.5, request, restart_power_supplied=True)
    lost = half.advance(source_available=False)
    assert lost.continuous_powered_minutes == 0.
    request = lost.request(permission_arrived=True, source_available=True)
    short = lost.commit(.75, request, restart_power_supplied=True)
    assert not short.ready
    restored = short.commit(.25, short.request(permission_arrived=True, source_available=True), restart_power_supplied=True)
    assert restored.ready


@pytest.mark.parametrize('missing', ['device_available', 'flow_available', 'source_available'])
def test_missing_necessary_path_cannot_start_or_advance(missing):
    from tools.model_correctness_v23.protection import RestartState
    state = RestartState()
    request = state.request(permission_arrived=True, **{missing: False})
    assert not request.can_restart and request.requested_start_power_kW == 0.
    result = state.commit(2., request, restart_power_supplied=True)
    assert not result.ready and result.continuous_powered_minutes == 0.


def test_legal_arrived_dispatch_stop_avoids_running_device_trip():
    from tools.model_correctness_v23.protection import RestartState
    running = RestartState(ready=True)
    stopped = running.commit(.1, running.request(permission_arrived=False), restart_power_supplied=False)
    lost_while_stopped = stopped.advance(source_available=False)
    assert not lost_while_stopped.protective_latch
    assert lost_while_stopped.status == 'functional_unavailable'


@pytest.mark.parametrize('restart_minutes', [0., 1., 5.])
def test_registered_restart_duration_is_not_an_implicit_one_minute_constant(restart_minutes):
    from tools.model_correctness_v23.protection import RestartState
    state = RestartState(restart_minutes=restart_minutes)
    request = state.request(permission_arrived=True)
    result = state.commit(.25, request, restart_power_supplied=True)
    assert result.ready is (restart_minutes <= .25)
    if restart_minutes > .25:
        result = result.commit(restart_minutes-.25, result.request(permission_arrived=True), restart_power_supplied=True)
        assert result.ready
