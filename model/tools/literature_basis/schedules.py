"""Hourly Bernoulli source calendars; no native execution, solver or controller."""
from __future__ import annotations

import copy
import hashlib
import math
from numbers import Integral, Real
import random

from .parameters import SUPPORTED_PORTS, _validated_profile

ANNUAL_HOURS = 8760
STREAM_VERSION = 'ECM2026-hourly-v1'


def _number(value, name, *, minimum=0., maximum=None, strictly_positive=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError('Expected finite numeric ' + name)
    value = float(value)
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum) or (strictly_positive and value <= 0):
        raise ValueError('Invalid finite range for ' + name)
    return value


def _merge(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def unavailable_intervals(draws, probability, duration_hours):
    """Keep every strict hourly trigger and union half-open downtime intervals.

    Source hour k (1-based) maps to start k-1. Down-state draws are retained;
    duration is fixed for each event. Endpoints beyond the sample are preserved.
    ``None`` probability is rejected, never interpreted as zero.
    """
    probability = _number(probability, 'probability', maximum=1.)
    duration = _number(duration_hours, 'duration', strictly_positive=True)
    try:
        samples = list(draws)
    except TypeError as exc:
        raise ValueError('draws must be an iterable of hourly uniform samples') from exc
    triggers = []
    for hour, draw in enumerate(samples):
        value = _number(draw, 'uniform draw', maximum=1.)
        if value >= 1.:
            raise ValueError('Uniform draws must be in [0,1)')
        if value < probability:
            triggers.append(hour)
    raw = [[hour, hour + duration] for hour in triggers]
    union = _merge(raw)
    horizon = len(samples)
    in_sample = sum(max(0., min(end, horizon) - start) for start, end in union)
    carryout = sum(max(0., end - max(start, horizon)) for start, end in union)
    return dict(raw_trigger_hours=triggers, raw_trigger_count=len(triggers),
                raw_intervals_hours=raw, unavailable_intervals_hours=union,
                draw_count=horizon, sample_hours=horizon,
                in_sample_down_hours=in_sample, carryout_hours=carryout)


def calendar_events(intervals, observation_hours):
    """Convert port interval unions to the existing external-event schema.

    Availability is initially up. No event at or after the observation endpoint
    is emitted, so a crossing outage does not receive an artificial restoration.
    Intervals supplied by the caller are never truncated or mutated.
    """
    horizon = _number(observation_hours, 'observation hours', strictly_positive=True)
    if not isinstance(intervals, dict):
        raise ValueError('Expected a mapping of supported ports to intervals')
    transitions = {}
    for port, asset_intervals in intervals.items():
        if not isinstance(port, str) or port not in SUPPORTED_PORTS:
            raise ValueError('Unsupported availability port')
        if not isinstance(asset_intervals, (list, tuple)):
            raise ValueError('Expected interval pairs')
        checked = []
        for interval in asset_intervals:
            if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                raise ValueError('Expected interval start/end pair')
            start = _number(interval[0], 'interval start')
            end = _number(interval[1], 'interval end')
            if end <= start:
                raise ValueError('Interval end must exceed its start')
            checked.append([start, end])
        for start, end in _merge(checked):
            if start < horizon:
                transitions.setdefault(start * 60., {})[port] = 0.
            if end < horizon:
                transitions.setdefault(end * 60., {})[port] = 1.
    return [dict(time_minute=time, availability=dict(sorted(changes.items())))
            for time, changes in sorted(transitions.items())]


def annual_schedule(profile, seed):
    """Return exactly 8760 independent hourly draws per registered asset.

    A SHA-256 seed derived from version, integer seed and asset port initializes
    each asset's Python MT19937 stream. Adding proxy assets or reordering assets
    cannot shift existing streams. This generates scenarios, not hospital risks.
    """
    registered = _validated_profile(profile)
    if isinstance(seed, bool) or not isinstance(seed, Integral) or seed < 0:
        raise ValueError('Seed must be an explicit nonnegative integer')
    seed = int(seed)
    assets = {}
    for asset in sorted(registered['assets'], key=lambda value: value['port']):
        port = asset['port']
        stream_id = hashlib.sha256(f'{STREAM_VERSION}|{seed}|{port}'.encode('ascii')).hexdigest()
        rng = random.Random(int(stream_id, 16))
        draws = [rng.random() for _ in range(ANNUAL_HOURS)]
        result = unavailable_intervals(draws, asset['probability_per_hour'], asset['duration_hours'])
        result.update(port=port, source_parameter_id=asset['source_parameter_id'],
                      evidence_group=asset['evidence_group'],
                      probability_per_hour=asset['probability_per_hour'],
                      duration_hours=asset['duration_hours'], uniform_draws=draws,
                      stream_seed_sha256=stream_id,
                      source_time_indexing='0-based hour start; source k maps to k-1')
        assets[port] = result
    events = calendar_events({port: asset['unavailable_intervals_hours']
                              for port, asset in assets.items()}, ANNUAL_HOURS)
    return dict(
        schema_version='literature_basis_schedule_v1', profile_name=registered['name'], seed=seed,
        observation_hours=ANNUAL_HOURS, initial_availability={port: 1. for port in assets},
        initial_condition='all up before the hour-0 draw; no pre-year carry-in',
        interval_convention='half-open union; touching intervals merged; uncropped carryout retained',
        rng=dict(stream_version=STREAM_VERSION, generator='Python random.Random MT19937',
                 seed_derivation='SHA256(version|nonnegative_integer_seed|asset_port)'),
        source=copy.deepcopy(registered['source']),
        unknown_probabilities=copy.deepcopy(registered['unknown_probabilities']),
        warnings=list(registered['warnings']), assets=assets, events=events,
        summary=dict(raw_trigger_count=sum(a['raw_trigger_count'] for a in assets.values()),
                     in_year_asset_down_hours=sum(a['in_sample_down_hours'] for a in assets.values()),
                     carryout_asset_hours=sum(a['carryout_hours'] for a in assets.values())),
        hospital_reliability_estimate=False, physical_parameters_changed=False,
    )
