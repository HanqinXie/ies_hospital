"""V16 engineering counterexamples: real balance, shared ramp, depletion tail."""
import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def implementation():
    try:
        return importlib.import_module('tools.essential_fixes.environment')
    except ModuleNotFoundError as exc:
        pytest.fail('V16 essential execution environment is missing: '+str(exc), pytrace=False)


def small_core(*, gt=130., load=200., budget=1., anchor=None):
    from test_autonomous_dispatch_model_order import make_core
    from tools.engineering_model.execution import DEFAULT_DYNAMICS
    old = make_core(gt_kW=gt, load_kW=load)
    env = old.env
    env.engineering_config = copy.deepcopy(DEFAULT_DYNAMICS)
    env.local_response_config = {}
    env.gt_floor_correction_config = {'enabled': True}
    # The inherited bare boundary helper defaults to a fixed GRID port. V16
    # uses the already-arrived importer ceiling, which can actually turn down.
    env.execution_contract['grid_import_mode'] = 'arrived_ceiling'
    env.operating_execution_contract = dict(env.execution_contract, diesel_dispatch_mode='arrived_ceiling')
    env.config['solver_time_limit_sec'] = 6.
    core = implementation().EssentialOperatingCore(env, old.executed)
    core.time = 60.
    core.ramp_time = 60.
    core.ramp_anchor = np.array([gt if anchor is None else anchor, 0., 0.])
    core.ramp_budget = budget
    _, _, loads, f = old.data_at(0.)
    row = SimpleNamespace(outdoor_temperature_C=12.416666666666666,
                          timestamp=__import__('pandas').Timestamp('2020-01-01'))
    core.data_at = lambda minute: (1, row, loads.copy(), dict(f))
    core.executed['grid_import_kW'] = 100.
    return core, loads, f, row


def test_hourly_floor_adjusts_running_gt_with_a_real_importer_and_unchanged_boiler():
    core, loads, f, row = small_core()
    old_boiler = core.executed['boiler_steam_lbph']
    core._surplus_interlock(loads, f, row)
    assert not core.interlock_latched
    assert core.executed['gt_electric_kW'][0] == pytest.approx(130.404608, abs=1e-8)
    assert core.executed['boiler_steam_lbph'] == old_boiler
    event = next(e for e in core.events if e['kind'] == 'gt_floor_local_correction')
    assert event['boundary_execution_abstraction'] is True
    assert event['feasible_actual_action']['grid_import_kW'] < 100.
    assert event['feasible_actual_action']['hrsg_steam_lbph'] > 0.
    assert max(map(abs, event['energy_balance_residuals'])) < 1e-4
    assert core.ramp_anchor.tolist() == [130., 0., 0.]
    assert core.ramp_budget == 1.


def test_floor_correction_cannot_recenter_an_already_consumed_ramp_window():
    core, loads, f, row = small_core(gt=130., anchor=129., budget=1.)
    core._surplus_interlock(loads, f, row)
    assert 'GT_1' in core.interlock_latched
    rejected = next(e for e in core.events if e['kind'] == 'gt_floor_local_correction_rejected')
    assert rejected['reason'] == 'shared_ramp_window_cannot_reach_floor'
    assert core.ramp_anchor.tolist() == [129., 0., 0.]
    assert core.ramp_budget == 1.


def test_rejected_real_sink_probe_is_transactional_and_does_not_grant_dispatch():
    core, loads, f, row = small_core(load=100.)
    before = copy.deepcopy(core.executed)
    assert not core._correct_gt_floor(loads, f, row)
    assert core.executed == before
    assert core.state.gt_prev.tolist() == before['gt_electric_kW']
    assert not core.failed and not core.errors
    event = next(e for e in core.events if e['kind'] == 'gt_floor_local_correction_rejected')
    assert event['reason'] == 'held_ports_cannot_balance_candidate'
    assert event['candidate_dispatch']['boiler_steam_lbph'] == 0.


def test_no_ramp_budget_preserves_explicit_minimum_interlock():
    core, loads, f, row = small_core(budget=0.)
    core._surplus_interlock(loads, f, row)
    assert core.executed['gt_electric_kW'][0] == 0.
    assert core.interlock_latched['GT_1']['reason'] == 'minimum_output_interlock'
    assert not any(e['kind'] == 'gt_floor_local_correction' for e in core.events)


def test_true_fault_precedes_local_floor_correction_and_is_never_rearmed():
    core, loads, f, row = small_core()
    core.state.extra_faulted.add('GT_1')
    f['GT_1'] = 0.
    core.data_at = lambda minute: (1, row, loads.copy(), dict(f))
    core.boundary()
    core._surplus_interlock(loads, f, row)
    assert core.executed['gt_electric_kW'][0] == 0.
    assert 'GT_1' in core.state.extra_faulted
    assert not any(e['kind'] == 'gt_floor_local_correction' for e in core.events)


def test_diesel_active_correction_never_claims_unresolved_rotor_energy():
    core, loads, f, row = small_core()
    core.state.diesel_status = 'CONNECTED'
    core.executed['diesel_kW'] = 100.
    core.env.scenario['diesel_on_grid_allowed'] = True
    assert not core._correct_gt_floor(loads, f, row)
    event = next(e for e in core.events if e['kind'] == 'gt_floor_local_correction_rejected')
    assert event['reason'] == 'diesel_coupled_boundary_requires_resolved_path'


def make_environment(*, budget, end=60., recovery=40.):
    m = implementation()
    from tools.operating_response.data_design import normal_proposal, SEALED_DAYS
    scene = copy.deepcopy(normal_proposal()['scenarios'][0])
    scene.update(duration_steps=int(end/15), stage=1, faults={'GRID': 0., 'GT_1': 0.,
        'GT_2': 0., 'GT_3': 0.}, fault_start_minute=0., fault_duration_minutes=recovery,
        response_delay_minutes=0., enable_propagation=False)
    first = scene['start_hour']
    env = m.OperatingHospitalIESEnv(ROOT, allowed_hours=range(first, first+int(end/60)+1),
        sealed_days=SEALED_DAYS, model_id='M16_essential')
    env.reset(scenario=scene, seed=20261017)
    env.state.diesel_energy_remaining_kWh = budget
    return env


@pytest.mark.parametrize('budget,expect_loaded', [(1., False), (5., True)])
def test_fuel_depletion_continues_through_normal_commands_and_grid_recovery(budget, expect_loaded):
    env = make_environment(budget=budget)
    while not env.terminated:
        env.step({'targets': {}, 'security': {}})
    assert env.core.time == 60.
    assert len(env.history) == 4 and not env.solver_errors
    assert env.state.diesel_energy_remaining_kWh == 0.
    events = [e for e in env.event_history if e['kind'] == 'diesel_fuel_exhausted']
    assert len(events) == 1 and 0. < events[0]['time_minute'] < 40.
    assert (any(s['action']['diesel_kW'] > 0. for s in env.segment_history)) == expect_loaded
    after = [s for s in env.segment_history if s['start_minute'] >= events[0]['time_minute']-1e-8]
    assert after and all(s['action']['diesel_kW'] == 0. for s in after)
    assert any(s['action']['grid_import_kW'] > 0. for s in after if s['start_minute'] >= 40.)
    receipts = [s['diesel_local_response'] for s in env.segment_history]
    spent = sum(r['mechanical_work_kWh'] for r in receipts)
    assert spent <= budget+1e-8
    assert spent+events[0]['budget_roundoff_remainder_kWh'] == pytest.approx(budget, abs=1e-7)
    assert all(abs(r['energy_residual_kWh']) < 1e-6 for r in receipts)
    assert all(max(map(abs, s['energy_balance_residuals'])) < 1e-4 for s in env.segment_history)
    assert all(s['post_state']['diesel_energy_remaining_kWh'] >= 0. for s in env.segment_history)
    assert any(e['kind'] == 'command_executed' and e['time_minute'] > events[0]['time_minute']
               for e in env.event_history)


def test_empty_diesel_cannot_restart_during_a_continuing_outage():
    env = make_environment(budget=5., recovery=120.)
    while not env.terminated:
        env.step({'targets': {}, 'security': {}})
    assert env.state.diesel_status == 'EMPTY'
    assert env.core.diesel_rotor.fuel_exhausted is True
    assert env.state.last_diesel_kW == 0.
    assert sum(e['kind'] == 'diesel_fuel_exhausted' for e in env.event_history) == 1
    assert env.segment_history[-1]['unserved_kW'][0][0] > 0.


def test_tiny_positive_loaded_budget_is_integrated_before_the_empty_event():
    env = make_environment(budget=2400., recovery=120.)
    env.step({'targets': {}, 'security': {}})
    assert env.state.diesel_status == 'CONNECTED'
    # A valid instantaneous engine state near its mechanical ceiling needs a
    # short positive prefix even when only a few nano-kWh remain. The old
    # 1e-8-second domain guard must not erase a >1e-9-kWh fuel remainder.
    env.core.diesel_rotor.mechanical_kW = 900.
    env.state.diesel_energy_remaining_kWh = 2e-9
    before = env.core.time
    env.step({'targets': {}, 'security': {}})
    event = next(e for e in env.event_history if e['kind'] == 'diesel_fuel_exhausted')
    assert event['time_minute'] > before
    assert (event['time_minute']-before)*60. < 1e-8
    assert event['last_valid_interval_receipt']['mechanical_work_kWh'] == pytest.approx(2e-9, abs=1e-13)
    assert event['budget_roundoff_remainder_kWh'] <= 1e-9
    assert env.core.time == 30. and env.state.diesel_energy_remaining_kWh == 0.
    assert not env.solver_errors


def test_tiny_positive_startup_budget_also_continues_after_exhaustion():
    env = make_environment(budget=2e-9, recovery=120.)
    env.step({'targets': {}, 'security': {}})
    event = next(e for e in env.event_history if e['kind'] == 'diesel_fuel_exhausted')
    assert event['time_minute'] > 0.
    assert event['budget_roundoff_remainder_kWh'] <= 1e-9
    assert env.core.time == 15. and not env.solver_errors


def test_true_speed_domain_exit_remains_unknown_after_depletion_fix():
    from tools.operating_response.execution import LocalResponseDomainError
    env = make_environment(budget=2400., recovery=120.)
    env.step({'targets': {}, 'security': {}})
    # A declared slow governor sensitivity cannot be turned into a harmless
    # EMPTY event merely because ordinary fuel depletion is now supported.
    env.core.diesel_rotor.config['governor_tau_seconds'] = 2.
    with pytest.raises(LocalResponseDomainError):
        env.step({'targets': {}, 'security': {}})
    assert env.solver_errors[-1]['category'] == 'local_response_domain_exceeded'
    assert env.state.diesel_energy_remaining_kWh > 2000.
    assert not any(e['kind'] == 'diesel_fuel_exhausted' for e in env.event_history)


def test_coincident_tiny_speed_and_fuel_boundary_commits_prefix_but_remains_unknown():
    from tools.operating_response.execution import LocalResponseDomainError
    env = make_environment(budget=2400., recovery=120.)
    env.step({'targets': {}, 'security': {}})
    core = env.core
    core.generate_command({'targets': {}, 'security': {}})
    core.boundary()
    _, _, loads, f = core.data_at(core.time)
    solution = core._solve(loads, f, .25, 'test_current_terminal', {}, execution_action=core.executed)
    electrical = solution['action']['diesel_kW']
    core.diesel_rotor.mechanical_kW = 900.
    maximum = core.diesel_rotor.nominal_rotor_energy_kWs*core.diesel_rotor.config['speed_max_pu']**2
    core.diesel_rotor.rotor_energy_kWs = maximum-5e-6
    # Match the registered interval passed to advance; the rotor locates a
    # domain crossing within its finite integration presentation.
    _, probe = core.preview_diesel(900., electrical, connected=True)
    assert probe['domain_exceeded'] is True
    assert 0. < probe['actual_seconds'] < 1e-8
    budget = probe['mechanical_work_kWh']
    assert budget > 1e-9
    env.state.diesel_energy_remaining_kWh = budget
    safe, _, coincident = core._limit_by_diesel(900., electrical, True)
    assert coincident['domain_exceeded'] and coincident['fuel_budget_exhausted']
    before = core.time
    with pytest.raises(LocalResponseDomainError):
        core.advance(30.)
    assert core.time > before
    receipt = core.segments[-1]['diesel_local_response']
    assert receipt['mechanical_work_kWh'] == pytest.approx(budget, abs=1e-13)
    assert receipt['domain_exceeded'] and receipt['fuel_budget_exhausted']
    assert env.solver_errors[-1]['category'] == 'local_response_domain_exceeded'

