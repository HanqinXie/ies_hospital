"""Offline actual-command lifecycle from an already-open saved payload.

This module never reads files, runs a solver, or changes an outcome. Intervals
use actual segment command IDs and executed events, never generation windows
or a planned ETA. Service integrals are descriptive executed intervals; an
issued reference or allowance is not a hard constraint on later execution.
"""
from __future__ import annotations

import copy
import math
from numbers import Real

SCHEMA = 'executed_service.actual_command_timeline.v1'
TIME_EPS = 1e-9
PORT_REPORT_TOL_KW = 1e-6  # Existing reporting scale, not a physical threshold.


def _number(value, label, *, nonnegative=True):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(label + ' must be finite numeric')
    result = float(value)
    if nonnegative and result < 0.:
        raise ValueError(label + ' must be nonnegative')
    return result


def _cid(value):
    if type(value) is not int or value <= 0:
        raise ValueError('command_id must be a positive integer')
    return value


def _ports(dispatch):
    if not isinstance(dispatch, dict):
        return None
    result = {}
    gt = dispatch.get('gt_electric_kW')
    if isinstance(gt, (list, tuple)) and len(gt) == 3:
        result.update({f'GT_{i+1}': _number(x, 'GT port') for i, x in enumerate(gt)})
    for original, label in [('grid_import_kW', 'GRID'), ('diesel_kW', 'DIESEL'),
                            ('absorption_cooling_kW', 'ABS')]:
        if original in dispatch:
            result[label] = _number(dispatch[original], label + ' port')
    return result


def _ens(segments):
    total = [[0., 0., 0.], [0., 0., 0.]]
    for segment in segments:
        matrix = segment.get('unserved_kW')
        if not isinstance(matrix, (list, tuple)) or len(matrix) != 4:
            return None
        if any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in matrix):
            raise ValueError('unserved_kW must be 4-by-3')
        dt = (segment['end_minute'] - segment['start_minute']) / 60.
        for p in range(2):
            for k in range(3):
                total[p][k] += _number(matrix[p+2][k], 'saved unserved power', nonnegative=False) * dt
    return total


def _holes(segments, start, stop):
    result = []; cursor = start
    for segment in segments:
        if segment['start_minute'] > cursor + TIME_EPS:
            result.append([cursor, segment['start_minute']])
        cursor = segment['end_minute']
    if cursor < stop - TIME_EPS:
        result.append([cursor, stop])
    return result


def extract_command_timeline(payload, scenario=None):
    """Return JSON-native lifecycle rows and real command-ID execution intervals.

    The caller owns all file access. Unknown tails and coverage holes retain
    observed integrals but have null complete-interval integrals. A successful
    execution event without a following saved segment is explicitly retained.
    Clamp attribution follows saved event order, since at one boundary the old
    command can be clamped before a new command executes at the same timestamp.
    """
    summary = payload.get('summary', {})
    segments = copy.deepcopy(payload.get('segments', []))
    for segment in segments:
        a = _number(segment['start_minute'], 'segment start')
        b = _number(segment['end_minute'], 'segment end')
        if b <= a:
            raise ValueError('segment end must follow start')
        segment.update(start_minute=a, end_minute=b)
    segments.sort(key=lambda s: s['start_minute'])
    if any(a['end_minute'] > b['start_minute'] + TIME_EPS for a, b in zip(segments, segments[1:])):
        raise ValueError('Saved execution segments overlap')
    observed = _number(summary.get('observed_until_minute', max((s['end_minute'] for s in segments), default=0.)), 'observed time')
    if any(s['end_minute'] > observed + TIME_EPS for s in segments):
        raise ValueError('Saved segment extends after observed time')
    specification = scenario if scenario is not None else payload.get('scenario', {})
    if not isinstance(specification, dict):
        specification = vars(specification)
    duration = (float(specification['duration_steps']) * float(specification['step_minutes'])
                if {'duration_steps', 'step_minutes'} <= set(specification) else observed)
    requested = _number(summary.get('requested_end_minute', duration), 'requested end')
    if observed > requested + TIME_EPS:
        raise ValueError('Observed time exceeds requested horizon')
    unknown_tail = observed < requested - TIME_EPS

    commands = {}
    for source, records in [('decision', payload.get('decisions', [])), ('event', payload.get('events', []))]:
        seen = set()
        for record in records:
            command = record.get('command_generated') if source == 'decision' else (
                record.get('command') if record.get('kind') == 'command_generated' else None)
            if not isinstance(command, dict):
                continue
            cid = _cid(command['id'])
            if cid in seen:
                raise ValueError('Duplicated saved generation within ' + source)
            seen.add(cid)
            generated = _number(command['generated_minute'], 'generation time')
            eta = _number(command['arrival_minute'], 'planned arrival time')
            if generated > observed + TIME_EPS or eta < generated - TIME_EPS:
                raise ValueError('Command generated outside observed causal interval')
            if cid in commands and commands[cid] != command:
                raise ValueError('Saved decision/event generation disagree')
            commands[cid] = copy.deepcopy(command)

    lifecycle = {cid: dict(command_id=cid, generated_minute=float(c['generated_minute']),
        planned_arrival_minute=float(c['arrival_minute']), actual_arrival_minute=None,
        actual_execution_minute=None, superseded_by_command_id=None,
        arrival_revalidation_changed=None, arrival_source_delta_kW=None,
        requested_dispatch=copy.deepcopy(c.get('dispatch')),
        executed_dispatch_at_arrival=None, clamp_events=[],
        observed_execution_minutes=0., execution_interval_indices=[]) for cid, c in commands.items()}
    executions = []; clamp_events = []; previous_active = None
    for index, event in enumerate(payload.get('events', [])):
        kind = event.get('kind')
        if kind not in {'command_arrived', 'command_executed', 'command_superseded',
                        'forced_capacity_or_permission_clamp'}:
            continue
        time = _number(event['time_minute'], 'event time')
        if time > observed + TIME_EPS:
            raise ValueError('Lifecycle event after observed time')
        if kind == 'forced_capacity_or_permission_clamp':
            clamp = copy.deepcopy(event)
            clamp.update(saved_event_index=index, affected_command_id=previous_active,
                         command_id_basis='preceding_execution_event_in_saved_event_order' if previous_active is not None else 'not_known_from_saved_execution_events')
            clamp_events.append(clamp)
            if previous_active is not None:
                lifecycle[previous_active]['clamp_events'].append(clamp)
            continue
        cid = _cid(event['command_id'])
        if cid not in lifecycle:
            raise ValueError('Lifecycle refers to unknown generated command')
        row = lifecycle[cid]
        if time < row['generated_minute'] - TIME_EPS:
            raise ValueError('Lifecycle event precedes generation')
        if kind == 'command_arrived':
            if row['actual_arrival_minute'] is not None:
                raise ValueError('Duplicate arrival')
            row['actual_arrival_minute'] = time
        elif kind == 'command_superseded':
            if row['superseded_by_command_id'] is not None:
                raise ValueError('Duplicate supersession')
            row['superseded_by_command_id'] = _cid(event['by'])
        else:
            if row['actual_execution_minute'] is not None:
                raise ValueError('Duplicate actual execution')
            row['actual_execution_minute'] = time
            row['executed_dispatch_at_arrival'] = copy.deepcopy(event.get('executed_dispatch'))
            requested_ports = _ports(event.get('requested_dispatch', commands[cid].get('dispatch')))
            actual_ports = _ports(event.get('executed_dispatch'))
            if requested_ports is not None and actual_ports is not None:
                delta = {k: actual_ports[k] - v for k, v in requested_ports.items() if k in actual_ports}
                row['arrival_source_delta_kW'] = delta
                row['arrival_revalidation_changed'] = any(abs(v) > PORT_REPORT_TOL_KW for v in delta.values())
            executions.append((time, index, cid)); previous_active = cid

    for segment in segments:
        cid = segment.get('command_id')
        if cid is not None and _cid(cid) not in commands:
            raise ValueError('Segment refers to unknown command_id')
    executions.sort()
    intervals = []
    for position, (start, _, cid) in enumerate(executions):
        next_execution = executions[position+1][0] if position+1 < len(executions) else None
        end = next_execution if next_execution is not None else None if unknown_tail else requested
        stop = min(observed, end if end is not None else observed)
        owned = [s for s in segments if s.get('command_id') == cid]
        if any(s['start_minute'] < start - TIME_EPS or s['end_minute'] > stop + TIME_EPS for s in owned):
            raise ValueError('Segment command_id disagrees with executed lifecycle interval')
        holes = _holes(owned, start, stop)
        integral = _ens(owned)
        interval = dict(command_id=cid, start_minute=start, end_minute=end,
            observed_end_minute=stop, observed_execution_minutes=sum(s['end_minute']-s['start_minute'] for s in owned),
            right_censored=end is None, coverage_holes_minutes=holes,
            coverage_complete=not holes, segment_count=len(owned),
            end_reason='replaced_by_executed_command' if next_execution is not None else 'unknown_tail' if unknown_tail else 'requested_observation_horizon',
            replaced_by_command_id=executions[position+1][2] if next_execution is not None else None,
            observed_P2_P3_ENS_kWh=integral,
            complete_interval_P2_P3_ENS_kWh=integral if end is not None and not holes else None,
            service_integral_semantics='Observed segments with this actual command ID; no generated-interval reassignment or hard execution budget claim')
        lifecycle[cid]['execution_interval_indices'].append(len(intervals))
        lifecycle[cid]['observed_execution_minutes'] += interval['observed_execution_minutes']
        intervals.append(interval)
    for cid, row in lifecycle.items():
        if row['superseded_by_command_id'] is not None and row['actual_execution_minute'] is not None:
            raise ValueError('A before-execution superseded command also executed')
        if row['actual_execution_minute'] is not None:
            status = 'executed' if row['observed_execution_minutes'] > TIME_EPS else 'executed_without_observed_segment'
        elif row['superseded_by_command_id'] is not None:
            status = 'superseded_without_execution'
        elif row['actual_arrival_minute'] is not None:
            status = 'arrived_without_observed_execution'
        else:
            status = 'queued_not_arrived_in_observed_prefix'
        row['lifecycle_status'] = status
        row['eventual_execution_status'] = ('observed_executed' if row['actual_execution_minute'] is not None else
            'superseded' if row['superseded_by_command_id'] is not None else 'unknown' if unknown_tail else 'not_executed_by_requested_horizon')
        row['planned_ETA_is_actual_execution'] = False
        if any(s.get('command_id') == cid for s in segments) and row['actual_execution_minute'] is None:
            raise ValueError('Segment command has no saved execution event')

    return dict(schema=SCHEMA, offline_only=True, hard_execution_budget_claim=False,
        original_outcomes_changed=False, time_units='minutes', power_units='kW', energy_units='kWh',
        matrix_rows=['P2', 'P3'], matrix_columns=['e', 'h', 'c'],
        observed_until_minute=observed, requested_end_minute=requested,
        unknown_tail_minutes=[observed, requested] if unknown_tail else None,
        command_rows=sorted(lifecycle.values(), key=lambda r: (r['generated_minute'], r['command_id'])),
        execution_intervals=intervals, clamp_events=clamp_events,
        initial_unattributed_segments=[dict(interval_minutes=[s['start_minute'], s['end_minute']], command_id=None,
            status='initial_state_without_generated_command') for s in segments if s.get('command_id') is None],
        attribution='Actual segment command_id with saved command_executed events; generation/ETA alone never establish execution',
        observation_horizon_is_command_replacement=False,
        port_reporting_tolerance_kW=PORT_REPORT_TOL_KW)
