"""Actual-interval station qualifications, independent of plant dispatch."""
import copy

import pytest

from tools.annual_readiness.station import ContinuousStationState
from tools.model_closure_v22.station import StationState


def test_normal_source_support_crosses_seven_days_without_calendar_expiry():
    state = ContinuousStationState()
    for start in range(0, 7 * 1440, 15):
        state.commit_supply(start, start + 15, normal_source_supported=True)
    assert state.unsupported_minutes == 0.
    assert state.available(normal_source_now=True)
    assert state.control_available(7 * 1440)
    assert not StationState().control_available(7 * 1440)
    assert state.config['control_autonomy_minutes'] == 1440.
    assert state.config['dg_qualified_starts'] == 2


def test_outage_autonomy_is_cumulative_and_exhausts_at_exact_actual_boundary():
    state = ContinuousStationState()
    state.commit_supply(0., 2000., normal_source_supported=True)
    state.commit_supply(2000., 3439.5, normal_source_supported=False)
    assert state.available()
    state.commit_supply(3439.5, 3440., normal_source_supported=False)
    assert not state.available()
    assert state.observe()['control_autonomy_remaining_minutes'] == 0.
    state.commit_supply(3440., 5000., normal_source_supported=True)
    assert state.available(normal_source_now=True)
    assert not state.available(normal_source_now=False)
    assert state.unsupported_minutes == 1440.
    assert state.requalification_epoch == 0


def test_normal_source_return_does_not_restore_spent_autonomy_or_start_rights():
    state = ContinuousStationState()
    assert state.claim_diesel_start(0.)
    assert state.claim_diesel_start(1.)
    state.commit_supply(0., 100., normal_source_supported=False)
    state.commit_supply(100., 7 * 1440., normal_source_supported=True)
    assert state.unsupported_minutes == 100.
    assert not state.diesel_start_available(7 * 1440.)
    assert state.total_diesel_starts_used == state.diesel_starts_used == 2


def test_current_hardware_and_source_are_explicit_and_queries_are_pure():
    state = ContinuousStationState()
    state.commit_supply(0., 1440., normal_source_supported=False)
    before = copy.deepcopy(state.observe())
    for _ in range(5):
        assert state.available(normal_source_now=True)
        assert not state.available(normal_source_now=False)
        assert not state.available(hardware=False, normal_source_now=True)
        assert not state.control_available(1440., hardware=False)
    assert state.observe() == before


@pytest.mark.parametrize('tau', [30., 60., 120.])
def test_requalification_requires_continuous_actual_support_and_retains_lifetime_ledger(tau):
    state = ContinuousStationState(requalification_minutes=tau)
    assert state.claim_diesel_start(0., token='failed-actual-start')
    state.commit_supply(0., 100., normal_source_supported=False)
    state.commit_supply(100., 100. + tau - .5, normal_source_supported=True)
    assert state.requalification_epoch == 0
    state.commit_supply(100. + tau - .5, 101. + tau, normal_source_supported=False)
    state.commit_supply(101. + tau, 101. + 2 * tau - .5, normal_source_supported=True)
    assert state.requalification_epoch == 0
    state.commit_supply(101. + 2 * tau - .5, 101. + 2 * tau, normal_source_supported=True)
    assert state.requalification_epoch == 1
    assert state.unsupported_minutes == 0.
    assert state.diesel_starts_used == 0
    assert state.total_diesel_starts_used == 1
    assert state.claimed_actual_tokens == {'failed-actual-start'}
    assert state.continuous_supported_minutes == 0.
    state.commit_supply(101. + 2 * tau, 101. + 4 * tau, normal_source_supported=True)
    assert state.requalification_epoch == 1


def test_requalification_without_any_prior_debt_does_not_create_epochs():
    state = ContinuousStationState(requalification_minutes=60.)
    state.commit_supply(0., 180., normal_source_supported=True)
    assert state.requalification_epoch == 0
    assert state.claim_diesel_start(180., token='new-actual-start')
    state.commit_supply(180., 180.5, normal_source_supported=True)
    assert state.requalification_epoch == 0
    state.commit_supply(180.5, 240., normal_source_supported=True)
    assert state.requalification_epoch == 1


def test_repeated_actual_token_is_idempotent_even_after_requalification():
    state = ContinuousStationState(requalification_minutes=30.)
    assert state.claim_diesel_start(0., token='actual-1')
    assert state.claim_diesel_start(0., token='actual-1')
    assert state.claim_diesel_start(1., token='actual-2')
    assert not state.claim_diesel_start(2., token='actual-3')
    state.commit_supply(0., 30., normal_source_supported=True)
    assert state.claim_diesel_start(0., hardware=False, token='actual-1')
    assert state.diesel_starts_used == 0
    assert state.total_diesel_starts_used == 2
    assert state.claim_diesel_start(30., token='actual-3')
    assert state.diesel_starts_used == 1
    assert state.total_diesel_starts_used == 3


def test_legacy_minute_token_and_disabled_start_hardware():
    state = ContinuousStationState()
    assert not state.claim_diesel_start(0., hardware=False)
    assert state.total_diesel_starts_used == 0
    assert state.claim_diesel_start(0.)
    assert state.claim_diesel_start(0.)
    assert state.claim_diesel_start(1.)
    assert state.total_diesel_starts_used == 2
    assert not ContinuousStationState({'dg_start_dc_available': False}).claim_diesel_start(0.)


def test_partitioning_and_day_boundaries_do_not_create_autonomy():
    whole, split = ContinuousStationState(), ContinuousStationState()
    whole.commit_supply(0., 1445., normal_source_supported=False)
    for start, end in [(0., 1439.), (1439., 1440.), (1440., 1445.)]:
        split.commit_supply(start, end, normal_source_supported=False)
    assert whole.observe() == split.observe()
    assert split.unsupported_minutes == 1445.


def test_old_boiler_commit_contract_remains_available():
    state = ContinuousStationState({'boiler_restart_minutes': 1.})
    state.commit(.5, boiler_requested=True, boiler_restart_supplied=True, station_live=True)
    assert not state.boiler_ready
    state.commit(.5, boiler_requested=True, boiler_restart_supplied=True, station_live=True)
    assert state.boiler_ready
    assert state.last_supply_end == 0.
    assert state.unsupported_minutes == 0.


def test_observation_discloses_functional_only_unknown_dc_energy():
    state = ContinuousStationState(requalification_minutes=60., warm_boiler=True)
    observation = state.observe()
    assert observation['boiler_ready']
    assert observation['qualification_is_research_assumption']
    assert observation['functional_requalification_only']
    assert observation['DC_energy_kWh'] is None
    assert observation['DC_charge_power_kW'] is None
    assert not observation['recharge_model_present']
    assert not observation['physical_charge_energy_credited']
    assert observation['requalification_minutes'] == 60.


@pytest.mark.parametrize('start,end', [(False, 1.), (0., True), ('0', 1.), (0., '1'),
    (float('nan'), 1.), (0., float('inf')), (-1., 1.), (0., 0.), (1., 0.)])
def test_invalid_actual_interval_rejected_without_mutation(start, end):
    state = ContinuousStationState()
    before = state.observe()
    with pytest.raises(ValueError):
        state.commit_supply(start, end, normal_source_supported=False)
    assert state.observe() == before


@pytest.mark.parametrize('start,end', [(0., 1.), (.5, 2.), (2., 3.)])
def test_duplicate_overlap_or_gap_rejected(start, end):
    state = ContinuousStationState()
    state.commit_supply(0., 1., normal_source_supported=False)
    before = state.observe()
    with pytest.raises(ValueError, match='contiguous'):
        state.commit_supply(start, end, normal_source_supported=True)
    assert state.observe() == before


@pytest.mark.parametrize('value', [0, 1, 'true', None])
def test_supply_and_query_flags_require_booleans(value):
    state = ContinuousStationState()
    for operation in [lambda: state.commit_supply(0., 1., normal_source_supported=value),
                      lambda: state.available(hardware=value),
                      lambda: state.available(normal_source_now=value),
                      lambda: state.control_available(0., hardware=value),
                      lambda: state.claim_diesel_start(0., hardware=value)]:
        with pytest.raises(ValueError):
            operation()
    assert state.last_supply_end == 0.


@pytest.mark.parametrize('value', [True, '60', -1., 0., float('inf'), float('nan')])
def test_invalid_requalification_parameter_rejected(value):
    with pytest.raises(ValueError):
        ContinuousStationState(requalification_minutes=value)


@pytest.mark.parametrize('value', [True, '1', -1., float('inf'), float('nan')])
def test_invalid_query_or_start_minute_rejected(value):
    state = ContinuousStationState()
    with pytest.raises(ValueError):
        state.control_available(value)
    with pytest.raises(ValueError):
        state.claim_diesel_start(value)


@pytest.mark.parametrize('token', ['', False, 1])
def test_invalid_actual_start_token_rejected(token):
    state = ContinuousStationState()
    with pytest.raises(ValueError):
        state.claim_diesel_start(0., token=token)
    assert state.total_diesel_starts_used == 0


def test_new_start_cannot_precede_settled_supply_time():
    state = ContinuousStationState()
    state.commit_supply(0., 10., normal_source_supported=True)
    with pytest.raises(ValueError, match='settled'):
        state.claim_diesel_start(9., token='late-backfill')


@pytest.mark.parametrize('config', [False, [], {'control_autonomy_minutes': '1440'},
    {'control_autonomy_minutes': False}, {'dg_qualified_starts': 1.5}])
def test_malformed_config_is_rejected(config):
    with pytest.raises(ValueError):
        ContinuousStationState(config)
