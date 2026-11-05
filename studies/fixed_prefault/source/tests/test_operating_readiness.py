"""Legal current promises and physical readiness, without simulator futures."""
import copy
import importlib
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]


def api():
    try:
        return importlib.import_module('tools.operating_response.readiness')
    except ModuleNotFoundError:
        pytest.fail('The versioned v15 readiness implementation is missing')


def dispatch(gt=(0., 132., 0.), grid=350., diesel=0.):
    return dict(gt_electric_kW=list(gt), grid_import_kW=grid, diesel_kW=diesel,
        boiler_steam_lbph=300., absorption_cooling_kW=100.,
        electric_chiller_cooling_kW=[20., 20., 20.], hrsg_steam_lbph=0.,
        tower_rejection_kW=0., gt_fuel_MMBtu_h=0.)


def current(gt=(0., 132., 0.), **updates):
    value = dict(schema='operating_response.readiness.v15.ramp_window_v2', observation_time_minute=0.,
        actual_gt_kW=list(gt), gt_physical_status=['RUNNING' if p else 'OFF' for p in gt],
        earliest_start_minute=[0.]*3, pending_starts=[None]*3, gt_can_stop=[True]*3,
        arrived_dispatch=dispatch(gt), actual_disconnected_kW=np.zeros((4, 3)).tolist(),
        observed_critical_unserved_kW=np.zeros((2, 3)).tolist(), known_command_queue=[],
        # This small fixture is a fresh ordinary window: its executed before
        # equals these actual ports, with the original 15min*900kW/h budget.
        gt_ramp_anchor_kW=list(gt), gt_ramp_budget_kW=225., diesel_connected=False, actual_diesel_kW=0.,
        diesel_arrived_cap_kW=0.)
    value.update(updates)
    return value


def test_preregistered_profiles_are_not_a_fit_to_the_early45_event():
    r = api()
    assert [r.preparation_profile(k)['critical_multiplier'] for k in ('K0', 'K1', 'K2')] == [1., 1.5, 2.]
    assert r.preparation_profile('K1')['arrival_delay_bounds_minutes'] == [0., 30.]
    with pytest.raises(ValueError): r.preparation_profile('1.925')


def test_pending_and_off_sources_are_zero_before_ready_with_their_hrsg():
    r = api(); c = current(gt_physical_status=['STARTING', 'RUNNING', 'OFF'],
        pending_starts=[dict(started=0., ready=20., requested=150.), None, None])
    phases = r.phase_sources(c, dispatch((150., 132., 0.)), gt_start_delay_minutes=10.,
        gt_ramp_kW_per_h=900.)
    pre = phases['arrived_before_new_ready']; post = phases['committed_ready']
    assert pre['gt_electric_kW'] == [0., 132., 0.]
    assert pre['hrsg_gt_basis_kW'] == pre['gt_electric_kW']
    assert post['gt_electric_kW'][0] == 150.
    assert post['earliest_possible_effective_minute'][0] == 20.
    assert post['assured_by_minute'][0] is None


def test_minimum_off_wait_and_queued_promise_do_not_become_actual_supply():
    r = api(); c = current(earliest_start_minute=[40., 0., 0.],
        known_command_queue=[dict(command_id=1, generated_minute=0., planned_arrival_minute=5.,
            permitted_dispatch=dispatch((150., 132., 0.)))])
    phases = r.phase_sources(c, dispatch((150., 132., 0.)), gt_start_delay_minutes=10.,
        gt_ramp_kW_per_h=900.)
    assert phases['held_before_arrival']['gt_electric_kW'] == [0., 132., 0.]
    assert phases['committed_ready']['earliest_possible_effective_minute'][0] == 50.
    assert phases['committed_ready']['assured_by_minute'] == [None]*3
    assert phases['queue_promises'][0]['planned_arrival_is_guarantee'] is False


def test_queue_without_an_acknowledged_arrival_time_retains_unknown_readiness():
    r = api(); c = current(known_command_queue=[dict(command_id=1,generated_minute=0.,
        planned_arrival_minute=None,permitted_dispatch=dispatch((150.,132.,0.)))])
    validated = r.validate_context(c)
    assert validated['known_command_queue'][0]['planned_arrival_minute'] is None
    phases = r.phase_sources(c,dispatch((150.,132.,0.)),gt_start_delay_minutes=10.,
        gt_ramp_kW_per_h=900.)
    queued = phases['queue_promises'][0]
    assert queued['planned_arrival_minute'] is None
    assert queued['planned_arrival_is_guarantee'] is False
    assert queued['arrival_acknowledged'] is False
    assert phases['committed_ready']['assured_by_minute'] == [None]*3
    assert phases['actual_arrival_known'] is False
    assert r.preparation_profile('K1')['arrival_delay_bounds_minutes'] == [0.,30.]


def test_connected_zero_diesel_is_not_an_actual_bridge_and_grid_zero_permission_is_zero():
    c = current(diesel_connected=True, diesel_arrived_cap_kW=300.,
        arrived_dispatch=dispatch(grid=0., diesel=300.))
    phases = api().phase_sources(c, dispatch(diesel=300.), gt_start_delay_minutes=10.,
        gt_ramp_kW_per_h=900.)
    assert phases['held_before_arrival']['actual_diesel_kW'] == 0.
    assert phases['held_before_arrival']['grid_import_limit_kW'] == 0.
    assert phases['held_before_arrival']['diesel_permitted_cap_kW'] == 300.


@pytest.mark.parametrize('update', [dict(scenario_id='x'), dict(future_events=[]),
    dict(actual_arrival_minute=5.), dict(observation_time_minute=float('inf')),
    # Keep rejection of the legacy approximation, and separately cover the
    # new shared-window anchor and scalar budget domain.
    dict(gt_ramp_available_kW=[-1., 0., 0.]),
    dict(gt_ramp_anchor_kW=[-1., 0., 0.]), dict(gt_ramp_budget_kW=-1.),
    dict(gt_ramp_budget_kW=True), dict(gt_ramp_budget_kW=float('inf')), dict(diesel_connected=1),
    dict(known_command_queue=[dict(command_id=1, generated_minute=1., planned_arrival_minute=5.,
        permitted_dispatch=dispatch())])])
def test_strict_context_rejects_hidden_or_invalid_information(update):
    with pytest.raises(ValueError): api().validate_context(current(**update))


def test_validation_is_pure_and_mismatching_running_state_is_rejected():
    c = current(); old = copy.deepcopy(c)
    assert api().validate_context(c) == c and c == old
    with pytest.raises(ValueError): api().validate_context(current(gt_physical_status=['OFF']*3))

