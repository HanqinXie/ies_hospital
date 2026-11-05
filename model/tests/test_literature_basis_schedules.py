"""Hourly source semantics and calendar bookkeeping, independent of a solver."""
import copy
import importlib
import json
import math
import warnings

import pytest


def api():
    return importlib.import_module('tools.literature_basis.schedules')


def main_profile():
    return importlib.import_module('tools.literature_basis.parameters').profile('main')


def test_strict_threshold_draws_while_down_and_overlap_union():
    p = 0.0039 / 100
    result = api().unavailable_intervals([0, p / 2, p, .5], p, 24)
    assert result['raw_trigger_hours'] == [0, 1]
    assert result['raw_intervals_hours'] == [[0, 24], [1, 25]]
    assert result['unavailable_intervals_hours'] == [[0, 25]]
    assert result['draw_count'] == result['sample_hours'] == 4
    assert result['in_sample_down_hours'] == 4
    assert result['carryout_hours'] == 21


def test_touching_intervals_have_no_spurious_restore():
    draws = [0] + [.5] * 23 + [0]
    result = api().unavailable_intervals(draws, 0.0039 / 100, 24)
    assert result['raw_trigger_hours'] == [0, 24]
    assert result['unavailable_intervals_hours'] == [[0, 48]]
    assert api().calendar_events({'BOILER_1': result['unavailable_intervals_hours']}, 50) == [
        {'time_minute': 0., 'availability': {'BOILER_1': 0.}},
        {'time_minute': 2880., 'availability': {'BOILER_1': 1.}},
    ]


def test_last_source_hour_keeps_full_tail():
    draws = [.5] * 8759 + [0]
    result = api().unavailable_intervals(draws, 0.0039 / 100, 24)
    assert result['raw_trigger_hours'] == [8759]
    assert result['raw_intervals_hours'] == [[8759, 8783]]
    assert result['unavailable_intervals_hours'] == [[8759, 8783]]
    assert result['in_sample_down_hours'] == 1
    assert result['carryout_hours'] == 23
    assert api().calendar_events({'BOILER_1': result['unavailable_intervals_hours']}, 8760) == [
        {'time_minute': 8759. * 60, 'availability': {'BOILER_1': 0.}},
    ]


def test_empty_and_probability_boundaries():
    assert api().unavailable_intervals([], .1, 1)['unavailable_intervals_hours'] == []
    assert api().unavailable_intervals([0, .5], 0, 1)['raw_trigger_hours'] == []
    assert api().unavailable_intervals([0, .5], 1, 1)['unavailable_intervals_hours'] == [[0, 2]]


@pytest.mark.parametrize('probability', [None, True, -.1, 1.1, math.nan, math.inf, '0.1'])
def test_invalid_or_unknown_probability_is_never_coerced_to_zero(probability):
    with pytest.raises(ValueError):
        api().unavailable_intervals([.5], probability, 24)


@pytest.mark.parametrize('duration', [None, True, 0, -1, math.nan, math.inf, '24'])
def test_duration_must_be_positive_finite_number(duration):
    with pytest.raises(ValueError):
        api().unavailable_intervals([.5], .1, duration)


@pytest.mark.parametrize('draw', [None, True, -.001, 1, math.nan, math.inf, '.2'])
def test_draws_must_be_finite_and_inside_half_open_unit_interval(draw):
    with pytest.raises(ValueError):
        api().unavailable_intervals([draw], .1, 24)


def test_calendar_groups_simultaneous_assets_and_uses_initial_up_default():
    intervals = {'ABS': [[1, 4]], 'BOILER_1': [[1, 2], [2, 3]], 'BOILER_2': []}
    original = copy.deepcopy(intervals)
    events = api().calendar_events(intervals, 5)
    assert events == [
        {'time_minute': 60., 'availability': {'ABS': 0., 'BOILER_1': 0.}},
        {'time_minute': 180., 'availability': {'BOILER_1': 1.}},
        {'time_minute': 240., 'availability': {'ABS': 1.}},
    ]
    assert original == intervals
    assert api().calendar_events({'ABS': []}, 5) == []


def test_calendar_observation_is_half_open_without_artificial_year_end_restore():
    assert api().calendar_events({'ABS': [[1, 3], [4, 5]]}, 3) == [
        {'time_minute': 60., 'availability': {'ABS': 0.}}
    ]


@pytest.mark.parametrize('intervals', [
    {'UNKNOWN': [[0, 1]]}, {'ABS': [[-1, 1]]}, {'ABS': [[1, 1]]},
    {'ABS': [[2, 1]]}, {'ABS': [[0, math.inf]]}, {'ABS': [[False, 1]]},
    {'ABS': [[0, '2']]}, {'ABS': [[0, 1, 2]]}, [], None,
])
def test_invalid_calendar_intervals_rejected(intervals):
    with pytest.raises(ValueError):
        api().calendar_events(intervals, 3)


@pytest.mark.parametrize('horizon', [0, -1, True, None, math.nan, math.inf, '8760'])
def test_invalid_calendar_observation_horizon_rejected(horizon):
    with pytest.raises(ValueError):
        api().calendar_events({'ABS': []}, horizon)


def test_annual_schedule_has_8760_trace_per_asset_and_is_serializable():
    value = main_profile()
    original = copy.deepcopy(value)
    result = api().annual_schedule(value, 2026103001)
    assert value == original
    assert result['observation_hours'] == 8760
    assert result['initial_availability'] == {port: 1. for port in ['ABS', 'BOILER_1', 'BOILER_2', 'BOILER_3']}
    assert set(result['assets']) == {'BOILER_1', 'BOILER_2', 'BOILER_3', 'ABS'}
    for asset in result['assets'].values():
        assert asset['draw_count'] == len(asset['uniform_draws']) == 8760
        assert len(asset['raw_trigger_hours']) == asset['raw_trigger_count']
        assert asset['source_time_indexing'] == '0-based hour start; source k maps to k-1'
    assert result['unknown_probabilities']['hospital_common_cause'] is None
    assert result['hospital_reliability_estimate'] is False
    assert json.loads(json.dumps(result)) == result
    assert api().annual_schedule(value, 2026103001) == result


def test_asset_streams_do_not_shift_with_order_or_explicit_proxy_addition():
    value = main_profile()
    first = api().annual_schedule(value, 2026103002)
    reordered = copy.deepcopy(value)
    reordered['assets'].reverse()
    assert api().annual_schedule(reordered, 2026103002)['assets'] == first['assets']
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        proxy = importlib.import_module('tools.literature_basis.parameters').profile('gt_proxy', allow_gt_proxy=True)
    second = api().annual_schedule(proxy, 2026103002)
    assert {k: second['assets'][k] for k in first['assets']} == first['assets']
    assert first['assets']['BOILER_1']['uniform_draws'] != first['assets']['BOILER_2']['uniform_draws']
    assert api().annual_schedule(value, 2026103003)['assets']['ABS']['uniform_draws'] != first['assets']['ABS']['uniform_draws']


@pytest.mark.parametrize('seed', [True, 1., '1', None, -1])
def test_seed_is_explicit_nonnegative_integer(seed):
    with pytest.raises(ValueError):
        api().annual_schedule(main_profile(), seed)


@pytest.mark.parametrize('mutation', ['duplicate', 'unknown', 'probability', 'duration', 'source', 'proxy_permission'])
def test_annual_rejects_profile_tampering_or_implicit_proxy(mutation):
    value = main_profile()
    if mutation == 'duplicate':
        value['assets'].append(copy.deepcopy(value['assets'][0]))
    elif mutation == 'unknown':
        value['assets'][0]['port'] = 'MADE_UP'
    elif mutation == 'probability':
        value['assets'][0]['probability_per_hour'] = None
    elif mutation == 'duration':
        value['assets'][0]['duration_hours'] = 0
    elif mutation == 'source':
        value['source']['pdf_sha256'] = 'changed'
    else:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            value = importlib.import_module('tools.literature_basis.parameters').profile('gt_proxy', allow_gt_proxy=True)
        value['gt_proxy_opt_in'] = False
    with pytest.raises(ValueError):
        api().annual_schedule(value, 1)
