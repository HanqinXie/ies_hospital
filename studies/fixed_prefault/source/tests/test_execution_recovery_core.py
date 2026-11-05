"""M18 common permission, topology and real-contact execution regressions."""
import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def implementation():
    try:
        return importlib.import_module('tools.execution_recovery.execution')
    except ModuleNotFoundError as exc:
        pytest.fail('M18 execution recovery is missing: '+str(exc), pytrace=False)


def small_core(*, gt=200., grid=400., diesel=500., loads=None):
    from test_autonomous_dispatch_model_order import make_core
    from tools.engineering_model.execution import DEFAULT_DYNAMICS
    old = make_core(gt_kW=gt, load_kW=400.)
    env = old.env
    env.engineering_config = copy.deepcopy(DEFAULT_DYNAMICS)
    env.local_response_config = {}
    env.gt_floor_correction_config = {'enabled': True}
    env.recovery_config = dict(grid_return_prepare_minutes=.25,
        grid_open_transfer_minutes=.25, local_contact_restore_delay_minutes=0.)
    env.execution_contract['grid_import_mode'] = 'arrived_ceiling'
    env.execution_contract['gt_local_down_rate_kW_per_hour'] = 900.
    env.execution_contract['gt_local_down_delay_minutes'] = .25
    env.operating_execution_contract = dict(env.execution_contract,
        diesel_dispatch_mode='arrived_ceiling')
    env.config.update(solver_time_limit_sec=6., gt_protection_trip_multiple=1.05,
        gt_overload_hold_minutes=15.)
    env.hrsg_thermal_config = {}
    initial = copy.deepcopy(old.executed)
    initial.update(grid_import_kW=grid, diesel_kW=diesel)
    core = implementation().ExecutionRecoveryCore(env, initial)
    row = SimpleNamespace(outdoor_temperature_C=12.416666666666666,
                          timestamp=pd.Timestamp('2020-01-01'))
    _, _, default_loads, f = old.data_at(0.)
    current = dict(f)
    actual_loads = default_loads if loads is None else np.asarray(loads, float)
    core._hardware_data_at = lambda minute: (1, row, actual_loads.copy(), dict(current))
    return core, actual_loads, current, row


def arrive(core, *, grid, diesel, identifier=1):
    dispatch = copy.deepcopy(core.arrived_dispatch)
    dispatch.update(grid_import_kW=grid, diesel_kW=diesel)
    core.pending_commands.append(dict(id=identifier, generated_minute=core.time,
        arrival_minute=core.time, request={}, dispatch=dispatch,
        disconnected=core.state.disconnected_loads.tolist(),
        reference_cooling_kW=0., weight=0.))


def test_grid_permission_survives_failure_and_activates_only_after_open_transfer():
    c, _, f, _ = small_core(diesel=0.)
    f['GRID'] = 0.
    c.boundary()
    assert c.arrived_dispatch['grid_import_kW'] == 400.
    assert c.executed['grid_import_kW'] == 0.
    f['GRID'] = 1.
    c.time = 2.
    c.boundary()
    assert c.grid_bus_state == 'RETURN_PREP'
    assert c.executed['grid_import_kW'] == 0.
    c.time = 2.25
    c.boundary()
    assert c.grid_bus_state == 'TRANSFER_OPEN'
    assert c.executed['grid_import_kW'] == c.executed['diesel_kW'] == 0.
    c.time = 2.5
    c.boundary()
    assert c.grid_bus_state == 'GRID'
    assert c.executed['grid_import_kW'] == 400.
    assert c.arrived_dispatch['grid_import_kW'] == 400.


def test_starting_diesel_keeps_arrived_permission_without_early_energy():
    c, loads, f, _ = small_core(gt=0.)
    f['GRID'] = 0.
    c.boundary()
    assert c.state.diesel_status == 'STARTING'
    assert c.arrived_dispatch['diesel_kW'] == 500.
    assert c.executed['diesel_kW'] == c.state.last_diesel_kW == 0.
    c.time = 15.
    c.boundary()
    assert c.state.diesel_status == 'CONNECTED'
    assert c.executed['diesel_kW'] == 500.
    assert c.state.last_diesel_kW == 0.  # permission is still not metered energy
    *_, effective = c.data_at(c.time)
    sol = c._solve(loads, effective, .25, 'test_conditional_diesel', {},
                   execution_action=c.executed)
    assert 0. < sol['action']['diesel_kW'] <= 500.
    assert max(map(abs, sol['energy_balance_residuals'])) < 1e-4


def test_new_zero_permission_revokes_and_is_not_resurrected_by_recovery():
    c, _, f, _ = small_core()
    f['GRID'] = 0.
    c.boundary()
    c.time = 5.
    arrive(c, grid=0., diesel=0.)
    c.boundary()
    f['GRID'] = 1.
    c.time = 20.
    c.boundary()
    assert c.arrived_dispatch['grid_import_kW'] == 0.
    assert c.arrived_dispatch['diesel_kW'] == 0.
    assert c.executed['grid_import_kW'] == c.executed['diesel_kW'] == 0.


def test_dg_stays_until_transfer_open_and_never_parallels_grid():
    c, _, f, _ = small_core(gt=0.)
    f['GRID'] = 0.
    c.boundary()
    c.time = 15.
    c.boundary()
    assert c.executed['diesel_kW'] == 500.
    f['GRID'] = 1.
    c.time = 16.
    c.boundary()
    assert c.grid_bus_state == 'RETURN_PREP'
    assert c.executed['diesel_kW'] == 500. and c.executed['grid_import_kW'] == 0.
    assert c.data_at(c.time)[-1]['GRID'] == 0.
    assert c.execution_limits()['hardware_grid_available_fraction'] == 1.
    c.time = 16.25
    c.boundary()
    assert c.state.diesel_status == 'READY'
    assert c.executed['diesel_kW'] == c.executed['grid_import_kW'] == 0.
    c.time = 16.5
    c.boundary()
    assert c.executed['grid_import_kW'] == 400. and c.executed['diesel_kW'] == 0.


def test_positive_but_inadequate_import_permission_does_not_withdraw_working_diesel():
    c, loads, f, _ = small_core(gt=0., grid=1.)
    f['GRID'] = 0.; c.boundary(); c.time = 15.; c.boundary()
    c.segments.append(dict(loads_kW=loads.tolist(), unserved_kW=np.zeros((4, 3)).tolist()))
    f['GRID'] = 1.; c.time = 16.; c.boundary()
    assert c.grid_bus_state == 'ISLAND'
    assert c.executed['diesel_kW'] == 500. and c.executed['grid_import_kW'] == 0.
    assert any(e['kind'] == 'grid_return_not_ready' for e in c.events)
    arrive(c, grid=400., diesel=500.)
    c.boundary()
    assert c.grid_bus_state == 'RETURN_PREP'


def test_faulted_grid_saved_permission_cannot_create_local_down_headroom():
    c, _, f, _ = small_core(diesel=0.)
    f['GRID'] = 0.
    c.boundary()
    c._request_local_down(dict(action=dict(grid_import_kW=0.), lost=np.zeros((4, 3))))
    assert c.local_down is None
    assert c.arrived_dispatch['grid_import_kW'] == 400.


def test_real_disconnected_electric_contacts_restore_before_surplus_isolation():
    loads = np.zeros((4, 3))
    loads[:, 0] = [50., 100., 100., 400.]
    c, _, f, row = small_core(gt=324., grid=0., diesel=0., loads=loads)
    f['GRID'] = 0.
    c.state.disconnected_loads[:, 0] = [0., 90., 90., 400.]
    c.disconnected_fraction = c.state.disconnected_loads / np.where(loads > 0., loads, 1.)
    c.connection_basis_loads = loads.copy()
    c.boundary()
    connected = loads - c.state.disconnected_loads
    c._surplus_interlock(connected, c.data_at(c.time)[-1], row)
    assert not c.interlock_latched
    assert c.executed['gt_electric_kW'][0] == 324.
    assert connected[:, 0].tolist() == pytest.approx([50., 100., 100., 74.])
    assert c.state.disconnected_loads[:, 0].tolist() == pytest.approx([0., 0., 0., 326.])
    ev = next(e for e in c.events if e['kind'] == 'local_real_load_restored')
    assert ev['restored_kW'] == pytest.approx([0., 90., 90., 74.])
    assert max(map(abs, ev['energy_balance_residuals'])) < 1e-4


def test_insufficient_real_load_retains_no_export_interlock():
    loads = np.zeros((4, 3)); loads[0, 0] = 200.
    c, _, f, row = small_core(gt=324., grid=0., diesel=0., loads=loads)
    f['GRID'] = 0.; c.boundary()
    connected = loads.copy()
    c._surplus_interlock(connected, c.data_at(c.time)[-1], row)
    assert c.executed['gt_electric_kW'][0] == 0.
    assert c.interlock_latched['GT_1']['reason'] == 'no_export_interlock'
    assert not any(e['kind'] == 'local_real_load_restored' for e in c.events)


def test_contact_proof_cannot_trade_critical_heat_or_cold_for_reconnection(monkeypatch):
    m = implementation()
    loads = np.zeros((4, 3)); loads[:, 0] = [100., 300., 0., 0.]
    loads[0, 1] = 10.
    c, _, f, row = small_core(gt=324., grid=0., diesel=0., loads=loads)
    f['GRID'] = 0.; c.state.disconnected_loads[1, 0] = 300.
    connected = loads-c.state.disconnected_loads
    baseline = dict(loads_kW=loads.tolist(), unserved_kW=np.zeros((4, 3)).tolist())
    c.segments.append(baseline)
    rejected_proof = dict(action=copy.deepcopy(c.executed), lost=np.zeros((4, 3)),
        energy_balance_residuals=[0., 0., 0.], steam_balance_residual_lbph=0.)
    rejected_proof['lost'][0, 1] = 1.
    monkeypatch.setattr(m, 'solve_physical', lambda *args, **kwargs: rejected_proof)
    before = c.state.disconnected_loads.copy()
    assert not c._restore_real_contacts(connected, f, row)
    assert np.array_equal(c.state.disconnected_loads, before)
    assert c.events[-1]['reason'] == 'critical_heat_cold_service_would_worsen'


def test_execution_limits_separates_saved_effective_and_fixed_protection_reference():
    c, _, f, _ = small_core()
    f['GRID'] = 0.; c.boundary()
    block = c.execution_limits()
    assert block['effective_grid_import_limit_kW'] == 0.
    assert block['effective_diesel_limit_kW'] == 0.
    assert block['gt_protection_reference_kW'] == 320.
    assert block['gt_trip_multiple'] == 1.05
    assert block['gt_overload_minutes'] == [0., 0., 0.]
    assert c.readiness_context()['arrived_dispatch']['grid_import_kW'] == 400.
    assert c.readiness_context()['diesel_arrived_cap_kW'] == 500.


def test_execution_limits_exposes_masked_boiler_effective_dispatch_separate_from_arrived_permission():
    c, _, f, _ = small_core(diesel=0.)
    arrive(c, grid=400., diesel=0.)
    c.pending_commands[-1]['dispatch']['boiler_steam_lbph'] = 300.
    for i in range(1, 4):
        f[f'BOILER_{i}'] = 0.
    c.boundary()
    assert c.readiness_context()['arrived_dispatch']['boiler_steam_lbph'] == 300.
    assert c.execution_context()['arrived_dispatch']['boiler_steam_lbph'] == 300.
    assert c.executed['boiler_steam_lbph'] == 0.
    limits = c.execution_limits()
    assert limits['effective_dispatch']['boiler_steam_lbph'] == 0.
    assert limits['schema'] == 'hospital_ies.execution_limits.v18.v2'
    assert limits['effective_dispatch'] == c.executed
    # The exported canonical effective state is a snapshot, not writable core
    # state and not a replacement for either persistent arrived context.
    limits['effective_dispatch']['gt_electric_kW'][0] = 0.
    assert c.executed['gt_electric_kW'][0] == 200.
    for i in range(1, 4):
        f[f'BOILER_{i}'] = 1.
    c.time = 1.
    c.boundary()
    assert c.arrived_dispatch['boiler_steam_lbph'] == 300.
    assert c.execution_limits()['effective_dispatch']['boiler_steam_lbph'] == 300.
    assert limits['effective_dispatch']['boiler_steam_lbph'] == 0.


def real_environment(*, budget=2400.):
    try:
        m = importlib.import_module('tools.execution_recovery.environment')
    except ModuleNotFoundError as exc:
        pytest.fail('M18 environment is missing: '+str(exc), pytrace=False)
    from tools.operating_response.data_design import normal_proposal, SEALED_DAYS
    scene = copy.deepcopy(normal_proposal()['scenarios'][0])
    scene.update(duration_steps=3, stage=1, faults={'GRID': 0., 'GT_1': 0.,
        'GT_2': 0., 'GT_3': 0.}, fault_start_minute=0., fault_duration_minutes=40.,
        response_delay_minutes=0., enable_propagation=False)
    first = scene['start_hour']
    env = m.OperatingHospitalIESEnv(ROOT, allowed_hours=range(first, first+2),
        sealed_days=SEALED_DAYS, model_id='M18_execution_recovery')
    env.reset(scenario=scene, seed=20261018)
    env.state.diesel_energy_remaining_kWh = budget
    arrive(env.core, grid=400., diesel=500.)
    return env, scene


def test_actual_open_transition_has_finite_gap_no_parallel_and_paid_idle_shaft_work():
    env, _ = real_environment()
    env.core.advance(45.)
    segments = env.core.segments
    assert env.core.time == 45. and not env.solver_errors
    assert all(s['action']['grid_import_kW'] <= 1e-8 or s['action']['diesel_kW'] <= 1e-8
               for s in segments)
    assert any(s['action']['diesel_kW'] > 0. for s in segments)
    gap = [s for s in segments if s['start_minute'] >= 40.25-1e-9
           and s['end_minute'] <= 40.5+1e-9]
    assert sum(s['end_minute']-s['start_minute'] for s in gap) == pytest.approx(.25)
    assert all(s['action']['grid_import_kW'] == s['action']['diesel_kW'] == 0. for s in gap)
    assert sum(s['diesel_local_response']['mechanical_work_kWh'] for s in gap) > 0.
    spent = sum(s['diesel_local_response']['mechanical_work_kWh'] for s in segments)
    assert 2400.-env.state.diesel_energy_remaining_kWh == pytest.approx(spent, abs=1e-7)
    assert all(max(map(abs, s['energy_balance_residuals'])) < 1e-4 for s in segments)
    assert all(abs(s['diesel_local_response']['energy_residual_kWh']) < 1e-6 for s in segments)
    assert all('arrived_source_permissions' in s and 'effective_source_setpoints' in s for s in segments)
    assert all('arrived_source_setpoints' in s and s['arrived_source_setpoints_semantics'] ==
               'legacy alias of effective_source_setpoints; not persistent permissions' for s in segments)
    assert all(s['hardware_availability']['GRID'] == (0. if s['start_minute'] < 40. else 1.)
               for s in segments)


def test_exhausted_energy_masks_but_does_not_delete_permission_and_grid_still_returns():
    env, _ = real_environment(budget=5.)
    env.core.advance(45.)
    assert env.core.time == 45. and not env.solver_errors
    assert env.state.diesel_energy_remaining_kWh == 0.
    assert env.core.arrived_dispatch['diesel_kW'] == 500.
    assert env.core.executed['diesel_kW'] == 0.
    assert len([e for e in env.event_history if e['kind'] == 'diesel_fuel_exhausted']) == 1
    assert any(s['action']['grid_import_kW'] > 0. for s in env.core.segments
               if s['start_minute'] >= 40.5)


def test_reset_clears_transfer_timers_and_prior_arrived_command():
    env, scene = real_environment()
    previous = env.core
    env.core.advance(40.25)
    assert env.core.grid_bus_state == 'TRANSFER_OPEN'
    env.reset(scenario=scene, seed=20261018)
    assert env.core is not previous
    assert env.core.grid_bus_state == 'GRID'
    assert env.core.grid_return_started_minute is env.core.grid_open_started_minute is None
    assert env.core.active_command is None and env.core.pending_commands == []
    assert env.core.arrived_dispatch['diesel_kW'] == 0.


def test_real_rotor_exception_retains_telemetry_on_actual_positive_prefix():
    from tools.operating_response.execution import LocalResponseDomainError
    env, _ = real_environment()
    core = env.core
    core.advance(15.)  # Actual paid startup; no fabricated initial rotor energy.
    start = core.time
    prior = len(core.segments)
    # A deliberately slow but finite governor sensitivity forces this lower
    # load case to leave its real rotor validity domain after a positive
    # electrical interval. The original exception and unknown tail must stay.
    core.diesel_rotor.config['governor_tau_seconds'] = 20.
    with pytest.raises(LocalResponseDomainError) as captured:
        core.advance(30.)
    prefix = core.segments[prior:]
    assert prefix and start < core.time < 30.
    assert all(s['end_minute'] > s['start_minute'] for s in prefix)
    assert core.errors[-1]['category'] == 'local_response_domain_exceeded'
    assert str(captured.value) == core.errors[-1]['message']
    assert captured.value.simulation_diagnostic['category'] == 'local_response_domain_exceeded'
    assert all(s['execution_model_id'] == 'M18_execution_recovery' for s in prefix)
    assert all(s['arrived_source_permissions']['diesel_kW'] == 500. for s in prefix)
    assert all(s['hardware_availability']['GRID'] == 0. for s in prefix)
    assert all(s['execution_limits']['grid_bus_state'] == 'ISLAND' for s in prefix)
    assert all(s['effective_source_setpoints'] == s['arrived_source_setpoints'] for s in prefix)
    assert all(abs(s['diesel_local_response']['energy_residual_kWh']) < 1e-6 for s in prefix)


@pytest.mark.parametrize('name', ['E', 'K1', 'C', 'C4compat'])
def test_all_ordinary_policy_adapters_run_on_one_common_m18_model(name):
    from tools.execution_recovery import environment as m, runner
    from tools.operating_response.data_design import normal_proposal, SEALED_DAYS
    scene = copy.deepcopy(normal_proposal()['scenarios'][0]); scene['duration_steps'] = 1
    first = scene['start_hour']
    env = m.OperatingHospitalIESEnv(ROOT, allowed_hours=[first, first+1], sealed_days=SEALED_DAYS)
    env.reset(scenario=scene, seed=20261018)
    env.core.boundary()
    controller = runner.factory(runner.policy_specs()[name])
    request, _ = controller.decide([m.decision_snapshot(env)])
    env.step(request)
    assert env.model_id == 'M18_execution_recovery'
    assert env.core.time == 15. and not env.solver_errors
    assert env.core.active_command['execution_model_id'] == env.model_id
    assert all(s['execution_model_id'] == env.model_id for s in env.core.segments)
