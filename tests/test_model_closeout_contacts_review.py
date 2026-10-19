"""Independent common-boundary review: explicit cuts and physical shortage differ.

The M19 common core is exercised directly; the planner fork does not authorize
an executor-wide override of deliberately issued P0/P1 isolation.
No annual data or archived trajectories are read.
"""
import copy

import numpy as np
import pytest

from test_execution_recovery_core import small_core
from tools.model_closeout.execution import ModelCloseoutCore


def model_core(**kwargs):
    old, loads, availability, row = small_core(**kwargs)
    core = ModelCloseoutCore(old.env, copy.deepcopy(old.arrived_dispatch))
    core._hardware_data_at = old._hardware_data_at
    return core, loads, availability, row


def issue(core, cut, *, arrival):
    core.pending_commands.append(dict(
        id=1, generated_minute=core.time, arrival_minute=arrival, request={},
        dispatch=copy.deepcopy(core.arrived_dispatch), disconnected=cut.tolist(),
        reference_cooling_kW=0., weight=0.))


@pytest.mark.parametrize('priority', [0, 1])
def test_deliberate_three_carrier_critical_cut_survives_common_boundary(priority):
    loads = np.zeros((4, 3))
    loads[0] = [40., 12., 15.]
    loads[1] = [50., 10., 20.]
    core, _, _, _ = model_core(gt=0., grid=400., diesel=0., loads=loads)
    cut = np.zeros((4, 3)); cut[priority] = loads[priority] / 2.
    issue(core, cut, arrival=0.)
    core.boundary()
    np.testing.assert_array_equal(core.state.disconnected_loads, cut)
    event = next(e for e in core.events if e['kind'] == 'command_executed')
    assert event['arrival_minute'] == 0.
    # A real source does not grant an unconditional reconnection of an
    # explicitly isolated critical service: zero fixed GT surplus here.
    connected = loads - cut
    core._surplus_interlock(connected, core.data_at(core.time)[-1], core.data_at(core.time)[1])
    np.testing.assert_array_equal(core.state.disconnected_loads, cut)


def test_queued_explicit_critical_cut_cannot_precede_its_arrival():
    loads = np.zeros((4, 3)); loads[0] = [40., 12., 15.]
    core, _, _, _ = model_core(gt=0., grid=400., diesel=0., loads=loads)
    cut = loads.copy() / 2.
    issue(core, cut, arrival=2.)
    core.boundary()
    assert np.count_nonzero(core.state.disconnected_loads) == 0
    assert len(core.pending_commands) == 1
    core.time = 2.
    core.boundary()
    np.testing.assert_array_equal(core.state.disconnected_loads, cut)
    assert not core.pending_commands


def test_physically_unserved_critical_request_stays_connected_without_cut_command():
    loads = np.zeros((4, 3))
    loads[0] = [40., 12., 15.]
    loads[1] = [50., 10., 20.]
    core, _, availability, _ = model_core(gt=0., grid=0., diesel=0., loads=loads)
    availability['GRID'] = 0.
    availability['DIESEL'] = 0.
    for i in range(1, 4):
        availability[f'GT_{i}'] = 0.
        availability[f'BOILER_{i}'] = 0.
    core.boundary()
    result = core._solve(loads, core.data_at(core.time)[-1], .25,
        'independent_M19_contact_review_no_resources', {}, execution_action=core.executed)
    np.testing.assert_allclose(result['lost'], loads, atol=1e-7, rtol=0.)
    assert np.count_nonzero(core.state.disconnected_loads) == 0
    assert max(map(abs, result['energy_balance_residuals'])) <= 1e-7
    assert sum(result['action']['gt_electric_kW']) == 0.
    assert result['action']['grid_import_kW'] == result['action']['diesel_kW'] == 0.
