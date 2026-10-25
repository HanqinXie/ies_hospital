from dataclasses import asdict
import importlib
import math

import numpy as np
import pandas as pd
import pytest


def api():
    try:
        return importlib.import_module('tools.model_closure_v22.temperature')
    except ModuleNotFoundError:
        pytest.fail('V22 actual net-sensible temperature module is missing')


def test_adiabatic_heat_and_actual_recovery_close_the_energy_ledger():
    m = api()
    state = m.ThermalState(delta_C=3., capacity_kWh_K=2., conductance_kW_K=0.)
    result = m.commit(state, actual_extra_cooling_kW=1., dt_h=.5,
                      sensible_delta_kW=4., ventilation_delta_kW=2.,
                      input_boundary='cooling_deficit_plus_explicit_ventilation')
    assert result.state.delta_C == pytest.approx(4.25)
    assert result.actual_extra_cooling_kWh == pytest.approx(.5)
    assert result.sensible_input_kWh == pytest.approx(3.)
    assert result.thermal_energy_change_kWh == pytest.approx(2.5)
    assert result.balance_residual_kWh == pytest.approx(0., abs=1e-12)
    assert state.delta_C == 3.


def test_recovery_request_is_pure_and_only_actual_delivery_cools():
    m = api()
    state = m.ThermalState(delta_C=4., capacity_kWh_K=2., conductance_kW_K=0.)
    before = asdict(state)
    request = m.recovery_request_kW(state, target_delta_C=0., known_horizon_h=.5,
                                   max_power_kW=20.)
    assert request == pytest.approx(16.)
    assert asdict(state) == before
    assert m.commit(state, 0., .5).state.delta_C == 4.
    assert m.commit(state, 4., .5).state.delta_C == 3.
    assert m.commit(state, request, .5).state.delta_C == pytest.approx(0.)
    assert m.recovery_request_kW(state, 0., .5, 3.) == 3.


def test_passive_recovery_is_explicit_energy_and_constant_inputs_split_consistently():
    m = api()
    state = m.ThermalState(delta_C=4., capacity_kWh_K=2., conductance_kW_K=1.)
    full = m.commit(state, 1., 1., sensible_delta_kW=2.)
    first = m.commit(state, 1., .25, sensible_delta_kW=2.)
    second = m.commit(first.state, 1., .75, sensible_delta_kW=2.)
    assert full.state.delta_C == pytest.approx(second.state.delta_C)
    assert full.passive_heat_rejection_kWh == pytest.approx(
        first.passive_heat_rejection_kWh + second.passive_heat_rejection_kWh)
    assert full.passive_heat_rejection_kWh > 0.
    assert full.balance_residual_kWh == pytest.approx(0., abs=1e-12)
    request = m.recovery_request_kW(state, 0., 1., 100.)
    assert 0. < request < 8.
    assert m.commit(state, request, 1.).state.delta_C == pytest.approx(0., abs=1e-12)


def test_signed_actual_air_system_cooling_is_not_clipped_or_double_counted():
    m = api()
    state = m.ThermalState(delta_C=1., capacity_kWh_K=2., conductance_kW_K=0.)
    result = m.commit(state, 0., 1., sensible_delta_kW=-4.,
                      input_boundary='net_air_system_sensible')
    assert result.state.delta_C == -1.
    with pytest.raises(ValueError, match='already includes ventilation'):
        m.commit(state, 0., 1., sensible_delta_kW=2., ventilation_delta_kW=1.,
                 input_boundary='net_air_system_sensible')


@pytest.mark.parametrize('name,value', [('capacity_kWh_K', 0.),
                                      ('conductance_kW_K', -1.),
                                      ('delta_C', float('nan'))])
def test_invalid_thermal_states_are_rejected(name, value):
    m = api()
    values = dict(delta_C=0., capacity_kWh_K=2., conductance_kW_K=1.)
    values[name] = value
    with pytest.raises(ValueError):
        m.ThermalState(**values)


def test_incomplete_hourly_net_heat_cannot_pass_ten_minute_acceptance():
    m = api()
    result = dict(event_rmse_C=.1, recovery_first6h_rmse_C=.1,
                  maximum_absolute_error_C=.2, first_crossing_error_minutes=0.,
                  recovery_time_error_minutes=0., crossing_status_matches=True,
                  complete_input_interval_minutes=60., inputs_complete=True)
    accepted = m.evaluate_acceptance(result, {'limits': {
        'event_rmse_C': 1., 'recovery_first6h_rmse_C': .5,
        'maximum_absolute_error_C': 2., 'first_crossing_error_minutes': 10.,
        'recovery_time_error_minutes': 20.,
        'maximum_complete_input_interval_minutes': 10.}})
    assert not accepted['passed']
    assert 'complete_input_interval_minutes' in accepted['failed_or_unassessed']


def test_bounded_fit_uses_only_calibration_inputs():
    m = api()
    times = np.arange(24.) / 6.
    q = np.where(times < 2., 4., -2.)
    truth = []
    state = m.ThermalState(0., .4, .2)
    for power in q:
        state = m.commit(state, 0., 1/6, sensible_delta_kW=power).state
        truth.append(state.delta_C)
    cases = [dict(case_id='cal_a', split='calibration', dt_h=1/6,
                  forcing_kW=q, actual_delta_C=np.array(truth)),
             dict(case_id='old_val', split='seen_regression', dt_h=1/6,
                  forcing_kW=q, actual_delta_C=np.array(truth) + 100.)]
    fitted = m.fit_one_state(cases, capacity_bounds=(.1, 1.), conductance_bounds=(.05, .5),
                            initial=(.6, .3))
    assert fitted['fit_case_ids'] == ['cal_a']
    assert fitted['capacity_kWh_K'] == pytest.approx(.4, rel=1e-4)
    assert fitted['conductance_kW_K'] == pytest.approx(.2, rel=1e-4)
    assert fitted['fit_attempts'] == 1


def test_hourly_net_sensible_keeps_real_energy_and_end_state_without_inventing_ten_minute_heat():
    m = api()
    stamps = pd.date_range('2006-08-15 12:10', periods=12, freq='10min')
    raw = pd.DataFrame(dict(baseline_temperature_C=20.,
                            independent_temperature_C=np.arange(12.) + 20.,
                            baseline_sensible_cooling_kW=6.,
                            independent_sensible_cooling_kW=[0.]*6 + [12.]*6,
                            cooling_setpoint_C=20.), index=stamps)
    hours = pd.DatetimeIndex([stamps[5], stamps[11]])
    baseline_heat = pd.Series([0., 0.], index=hours)
    actual_heat = pd.Series([2., 0.], index=hours)
    hourly = m.aggregate_net_sensible_hourly(raw, baseline_heat, actual_heat)
    assert hourly.forcing_kW.tolist() == [8., -6.]
    assert hourly.independent_temperature_C.tolist() == [25., 31.]
    assert len(hourly) == 2
    assert hourly.forcing_kW.sum() == pytest.approx(2.)
    with pytest.raises(ValueError, match='Hourly heating clock'):
        m.aggregate_net_sensible_hourly(raw, baseline_heat.iloc[:1], actual_heat)


def test_metrics_keep_native_crossing_and_censored_recovery():
    m = api()
    stamps = pd.date_range('2006-08-15 12:00', periods=13, freq='10min')
    actual = pd.DataFrame(dict(baseline_temperature_C=20., cooling_setpoint_C=20.,
                               independent_temperature_C=[20., 21., 23., 24., 24., 24.,
                                                          24., 21., 20.1, 20., 20., 20., 20.]),
                          index=stamps)
    case = dict(case_id='seen', month=8, day=15, outage_hours=1.)
    predicted = pd.Series([0.] * 13, index=stamps)
    metrics = m.paired_metrics(case, actual, predicted,
                               complete_input_interval_minutes=10., inputs_complete=True)
    assert metrics['independent_first_crossing'] == '2006-08-15T12:20:00'
    assert metrics['modeled_first_crossing'] is None
    assert metrics['first_crossing_error_minutes'] is None
    assert not metrics['crossing_status_matches']
    assert metrics['independent_recovery_minutes'] == 20.
    coarse = predicted.iloc[[0, 6, 12]]
    metrics = m.paired_metrics(case, actual, coarse,
                               complete_input_interval_minutes=60., inputs_complete=True)
    assert metrics['modeled_recovery_minutes'] is None
    assert metrics['recovery_time_error_minutes'] is None


def test_acceptance_rejects_impossible_negative_error_metrics():
    m = api()
    names = ['event_rmse_C', 'recovery_first6h_rmse_C', 'maximum_absolute_error_C',
             'first_crossing_error_minutes', 'recovery_time_error_minutes']
    metrics = {name: -1. for name in names}
    metrics.update(complete_input_interval_minutes=10., inputs_complete=True,
                   crossing_status_matches=True)
    limits = {name: 1. for name in names}
    limits['maximum_complete_input_interval_minutes'] = 10.
    verdict = m.evaluate_acceptance(metrics, {'limits': limits})
    assert not verdict['passed']
    assert set(names).issubset(verdict['failed_or_unassessed'])


def test_replay_refuses_a_config_that_changes_its_fixed_research_measurement_contract():
    m = api()
    config = dict(research_threshold_above_setpoint_C=3., recovery_band_C=.2,
                  recovery_consecutive_native_samples=3, recovery_native_sample_minutes=10.)
    with pytest.raises(ValueError, match='Unsupported research measurement contract'):
        m.validate_measurement_contract(config)


def test_passive_heat_integral_has_an_independent_physical_value_and_sign():
    m = api()
    for delta in (4., -4.):
        state = m.ThermalState(delta, 2., 1.)
        result = m.commit(state, 0., 1.)
        expected_heat = 2. * delta * (1. - math.exp(-.5))
        assert result.passive_heat_rejection_kWh == pytest.approx(expected_heat)
        assert result.balance_residual_kWh == pytest.approx(0., abs=1e-12)
