"""Completed actual service telemetry above the unchanged M0 physical core.

Only the current clock, completed segment ledger and three existing material
thresholds are read. No scenario/event script, raw output or outcome label is
available to the policy through this adapter. The original feature whitelist
is unchanged. Service metering remains an explicit ideal-observation assumption.
"""
from __future__ import annotations

import copy
import math
from numbers import Real


EXECUTED_SERVICE_HISTORY_CONTRACT_ID = 'executed_service.completed_prefix.v1'
GROUPS = ('P2', 'P3')
CARRIERS = ('e', 'h', 'c')
TIME_EPS = 1e-9  # Clock/coverage arithmetic only, never a service tolerance.
ACCUMULATORS = ('cumulative_ENS_kWh', 'cumulative_equivalent_loss_minutes',
                'cumulative_material_minutes', 'trailing_material_minutes')
FRACTION_SEMANTICS = 'U/D for positive D; zero for D=0; no numerical clamp; ENS integrates original U'
HISTORY_FIELDS = frozenset(('contract_id', 'available_time_min', 'valid_time_min',
    'history_start_min', 'history_end_min', 'groups', 'carriers', 'material_thresholds',
    'fraction_semantics', 'last_executed_segment', *ACCUMULATORS))
LAST_FIELDS = frozenset(('start_minute', 'end_minute', 'command_id', 'loads_kW', 'unserved_kW'))
FORBIDDEN_SNAPSHOT_FIELDS = frozenset(('raw', 'segments', 'events', 'labels',
    'actual_execution', 'post_outcome', 'first_failure_observation_interval',
    'first_failure_observed_minute', 'executed_service_history'))


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError('Invalid executed-service number: ' + name)
    value = float(value)
    if not math.isfinite(value) or value < 0.:
        raise ValueError('Invalid executed-service number: ' + name)
    return value


def _matrix(value, name, rows):
    if not isinstance(value, (list, tuple)) or len(value) != rows:
        raise ValueError('Invalid executed-service matrix: ' + name)
    if any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in value):
        raise ValueError('Invalid executed-service matrix: ' + name)
    return [[_number(v, name) for v in row] for row in value]


def _thresholds(value):
    if not isinstance(value, dict) or set(value) != {'solver_tol_kW', 'absolute_kW', 'relative'}:
        raise ValueError('Invalid executed-service material thresholds')
    return {key: _number(v, key) for key, v in value.items()}


def _command_id(value):
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError('Invalid last executed command ID')
    return value


def validate_history(history, decision_time_min):
    """Validate and copy the sole extra block a v13 policy is allowed to read.

    The validator checks local values and clocks. Monotonicity across successive
    decisions is additionally a policy-history responsibility; make_snapshot
    derives every block from a nonnegative, continuously completed ledger.
    """
    t = _number(decision_time_min, 'decision clock')
    if not isinstance(history, dict) or set(history) != HISTORY_FIELDS:
        raise ValueError('Missing or unsupported executed-service observation fields')
    if history['contract_id'] != EXECUTED_SERVICE_HISTORY_CONTRACT_ID:
        raise ValueError('Unsupported executed-service observation contract')
    if history['groups'] != list(GROUPS) or history['carriers'] != list(CARRIERS):
        raise ValueError('Unsupported executed-service matrix order')
    if history['fraction_semantics'] != FRACTION_SEMANTICS:
        raise ValueError('Unsupported executed-service fraction semantics')
    for key in ('available_time_min', 'valid_time_min', 'history_end_min'):
        if abs(_number(history[key], key) - t) > TIME_EPS:
            raise ValueError('Executed-service observation clock differs from decision')
    if _number(history['history_start_min'], 'history start') != 0.:
        raise ValueError('Executed-service history must retain the episode prefix')
    for key in ACCUMULATORS:
        _matrix(history[key], key, 2)
    _thresholds(history['material_thresholds'])
    total = history['cumulative_material_minutes']
    tail = history['trailing_material_minutes']
    for p in range(2):
        for k in range(3):
            if tail[p][k] > total[p][k] + TIME_EPS or total[p][k] > t + TIME_EPS:
                raise ValueError('Executed-service material duration exceeds observed history')
    last = history['last_executed_segment']
    if last is None:
        if t != 0. or any(v != 0. for key in ACCUMULATORS for row in history[key] for v in row):
            raise ValueError('Only the initial empty prefix has no executed segment')
    else:
        if not isinstance(last, dict) or set(last) != LAST_FIELDS:
            raise ValueError('Invalid last executed segment fields')
        start = _number(last['start_minute'], 'last start')
        end = _number(last['end_minute'], 'last end')
        if end <= start or abs(end - t) > TIME_EPS:
            raise ValueError('Last executed segment does not end at this observation')
        _command_id(last['command_id'])
        _matrix(last['loads_kW'], 'last loads', 2)
        _matrix(last['unserved_kW'], 'last unserved', 2)
    return copy.deepcopy(history)


def make_snapshot(env, base_snapshot):
    """Return an additive snapshot from an already-created base snapshot.

    Both policy-time and exporter-time call sites should invoke this same pure
    adapter after the existing boundary call. The last executed segment is a
    completed interval measurement, never a zero-time solve of current service.
    Future, overlapping or missing segments are rejected, not clipped or filled.
    """
    if not isinstance(base_snapshot, dict) or FORBIDDEN_SNAPSHOT_FIELDS.intersection(base_snapshot):
        raise ValueError('Expected a fresh pre-action snapshot without outcomes')
    t = _number(env.core.time, 'execution clock')
    for key in ('decision_time_min', 'observation_time_min'):
        if key not in base_snapshot or abs(_number(base_snapshot[key], key) - t) > TIME_EPS:
            raise ValueError('Base snapshot clock differs from completed execution')
    config = env.config
    thresholds = _thresholds(dict(
        solver_tol_kW=config['p0_solver_feasibility_tol_kW'],
        absolute_kW=config['p0_unserved_abs_tol_kW'], relative=config['p0_unserved_rel_tol']))
    history = dict(contract_id=EXECUTED_SERVICE_HISTORY_CONTRACT_ID,
        available_time_min=t, valid_time_min=t, history_start_min=0., history_end_min=t,
        groups=list(GROUPS), carriers=list(CARRIERS), material_thresholds=thresholds,
        fraction_semantics=FRACTION_SEMANTICS, last_executed_segment=None,
        **{key: [[0.] * 3 for _ in range(2)] for key in ACCUMULATORS})
    previous = 0.
    segments = env.core.segments
    if not isinstance(segments, (list, tuple)):
        raise ValueError('Completed executed-service ledger is missing')
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError('Invalid completed executed-service segment')
        start = _number(segment['start_minute'], 'segment start')
        end = _number(segment['end_minute'], 'segment end')
        if abs(start - previous) > TIME_EPS or end <= start or end > t + TIME_EPS:
            raise ValueError('Executed-service segments must be completed and contiguous from zero')
        loads = _matrix(segment['loads_kW'], 'segment loads', 4)[2:]
        unserved = _matrix(segment['unserved_kW'], 'segment unserved', 4)[2:]
        duration = end - start
        for p in range(2):
            for k in range(3):
                loss = unserved[p][k]
                demand = loads[p][k]
                fraction = loss / demand if demand > 0. else 0.
                history['cumulative_ENS_kWh'][p][k] += duration * loss / 60.
                history['cumulative_equivalent_loss_minutes'][p][k] += duration * fraction
                material_fraction = loss / demand if demand > thresholds['solver_tol_kW'] else 0.
                material = (loss > thresholds['solver_tol_kW'] and
                    (loss >= thresholds['absolute_kW'] or material_fraction >= thresholds['relative']))
                if material:
                    history['cumulative_material_minutes'][p][k] += duration
                    history['trailing_material_minutes'][p][k] += duration
                else:
                    history['trailing_material_minutes'][p][k] = 0.
        history['last_executed_segment'] = dict(start_minute=start, end_minute=end,
            command_id=_command_id(segment['command_id']), loads_kW=loads, unserved_kW=unserved)
        previous = end
    if abs(previous - t) > TIME_EPS or (t != 0. and not segments):
        raise ValueError('Executed-service ledger does not cover the decision clock')
    result = copy.deepcopy(base_snapshot)
    result['executed_service_history'] = validate_history(history, t)
    return result
