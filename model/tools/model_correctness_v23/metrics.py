"""Offline delivered-service events; no controller or physical-model inputs.

V22 materiality and all raw energies/intervals remain unchanged. Electricity
confirms on any positive-duration restoration. Heat/cold require one continuous
60-second delivery cycle, a research reporting definition rather than a clinical
or temperature standard. Actual resumption and confirmation are separate times.
"""
from __future__ import annotations

import math

CONTRACT_ID = 'hospital_ies.delivered_service_recovery.v23.v1'
PRIORITIES = ('P0', 'P1', 'P2', 'P3')
CARRIERS = ('e', 'h', 'c')
CONFIRMATION_SECONDS = dict(e=0., h=60., c=60.)
# Retained V22 interval-joining precision, in minutes, not recovery debounce.
INTERVAL_JOIN_MINUTES = 1e-9


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _merge(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]+INTERVAL_JOIN_MINUTES:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def _raw_times(intervals):
    intervals = _merge(intervals)
    return dict(intervals=intervals, material_minutes=sum(b-a for a, b in intervals),
        longest_material_minutes=max((b-a for a, b in intervals), default=0.),
        interruption_count=len(intervals), repeat_interruption_count=max(0, len(intervals)-1),
        first_material_minute=intervals[0][0] if intervals else None)


def _cell_episodes(intervals, horizon, seconds):
    """Observe persistence without deleting raw deficits or moving a clock."""
    episodes, candidates = [], []
    start = intervals[0][0] if intervals else None
    for index, (_, end) in enumerate(intervals):
        next_start = intervals[index+1][0] if index+1 < len(intervals) else horizon
        gap = next_start-end
        candidate = None
        confirmed = gap > 0. and gap*60. >= seconds
        if gap > 0.:
            candidate = dict(candidate_minute=end,
                confirmation_minute=end+seconds/60. if confirmed else None,
                observed_service_minutes=gap,
                status='confirmed' if confirmed else 'rejected' if index+1 < len(intervals) else 'observed_unconfirmed')
            candidates.append(candidate)
        if confirmed or index+1 == len(intervals):
            episodes.append(dict(start_minute=start,
                candidate_recovery_minute=end if candidate else None,
                confirmed_recovery_minute=candidate['confirmation_minute'] if confirmed else None,
                status='observed' if confirmed else 'censored', observation_end_minute=horizon))
            start = intervals[index+1][0] if index+1 < len(intervals) else None
    return episodes, candidates


def _union_episodes(rows, horizon):
    """A union restores only once all currently affected cells have confirmed.

    Pending confirmation windows that overlap belong to one engineering event.
    The physical-service candidate remains distinct from the later confirmation.
    """
    source = sorted((e for row in rows for e in row['engineering_episodes']), key=lambda e: e['start_minute'])
    groups = []
    for episode in source:
        start = episode['start_minute']
        if groups and start <= groups[-1][1]+INTERVAL_JOIN_MINUTES:
            groups[-1][0].append(episode)
            groups[-1][1] = max(groups[-1][1], episode['confirmed_recovery_minute'] or horizon)
        else:
            groups.append([[episode], episode['confirmed_recovery_minute'] or horizon])
    output = []
    for members, _ in groups:
        observed = all(e['status'] == 'observed' for e in members)
        candidates = [e['candidate_recovery_minute'] for e in members]
        output.append(dict(start_minute=min(e['start_minute'] for e in members),
            candidate_recovery_minute=max(candidates) if all(v is not None for v in candidates) else None,
            confirmed_recovery_minute=max(e['confirmed_recovery_minute'] for e in members) if observed else None,
            status='observed' if observed else 'censored', observation_end_minute=horizon))
    candidates = [dict(candidate_minute=e['candidate_recovery_minute'],
        confirmation_minute=e['confirmed_recovery_minute'], status='confirmed' if e['status'] == 'observed' else 'censored')
        for e in output if e['candidate_recovery_minute'] is not None]
    return output, candidates


def _reference(value, carriers):
    """Return a scalar reference and fact status; N/A is never time zero.

    A union uses the latest applicable affected-carrier reference. Explicitly
    inapplicable cells impose no reference; an absent applicable fact remains
    missing rather than being dropped from that union.
    """
    if value == 'not_applicable':
        return None, 'not_applicable'
    if isinstance(value, dict):
        selected = [value.get(k) for k in carriers]
        if not selected or any(v != 'not_applicable' and not (_number(v) and v >= 0.) for v in selected):
            return None, 'missing'
        applicable = [float(v) for v in selected if _number(v)]
        return (max(applicable), 'observed') if applicable else (None, 'not_applicable')
    return (float(value), 'observed') if _number(value) and value >= 0. else (None, 'missing')


def _finish(row, episodes, candidates, horizon, support, references, carriers):
    row.update(engineering_episodes=episodes, candidate_recoveries=candidates,
        observation_end_minute=horizon, engineering_intervals=[
            [e['start_minute'], e['candidate_recovery_minute'] if e['status'] == 'observed' else horizon]
            for e in episodes])
    status = ('missing' if not support else 'not_applicable' if not episodes else episodes[-1]['status'])
    row['recovery_status'] = status
    row['engineering_interruption_count'] = len(episodes) if support else None
    row['engineering_repeat_interruption_count'] = max(0, len(episodes)-1) if support else None
    durations = [b-a for a, b in row['engineering_intervals']]
    row['engineering_minutes'] = sum(durations) if support else None
    row['longest_engineering_minutes'] = max(durations, default=0.) if support else None
    row['candidate_recovery_absolute_minute'] = episodes[-1]['candidate_recovery_minute'] if episodes and support else None
    row['candidate_recovery_status'] = ('missing' if not support else 'not_applicable' if not episodes
        else 'confirmed' if status == 'observed' else 'observed_unconfirmed'
        if row['candidate_recovery_absolute_minute'] is not None else 'not_observed')
    row['recovery_absolute_minute'] = episodes[-1]['candidate_recovery_minute'] if status == 'observed' else None
    row['recovery_confirmation_minute'] = episodes[-1]['confirmed_recovery_minute'] if status == 'observed' else None
    for name, key in (('external_fault_clear', 'external_fault_clear_minute'),
                      ('actual_source_restore', 'actual_source_restore_minute')):
        reference, fact_status = _reference(references.get(key), carriers)
        row[key] = reference
        reference_status = ('not_applicable' if status == 'not_applicable' or fact_status == 'not_applicable'
            else status if status != 'observed' else fact_status)
        row['recovery_'+('external' if name == 'external_fault_clear' else 'source')+'_reference_status'] = reference_status
        offset = row['recovery_absolute_minute']-reference if reference_status == 'observed' else None
        row['recovery_relative_to_'+name+'_minutes'] = offset
        row['recovery_wait_after_'+name+'_minutes'] = max(0., offset) if offset is not None else None
    # V22's empty recovery_minutes export meant wait after declared repair.
    # Keep that intended interpretation, with explicit status and absolute time.
    row['recovery_minutes'] = row['recovery_wait_after_external_fault_clear_minutes']
    return row


def build_reliability_metrics(payload, *, references=None, confirmation_seconds=None):
    """Read explicit RAW segments; references are post-event facts, never policy.

    References may be scalars or per-carrier ``e/h/c`` mappings. The exact
    string ``'not_applicable'`` means the reference event is known not to
    exist, globally or for that carrier. An absent reference makes only that
    relative measure missing, not known restoration. A union uses the latest
    numeric affected-carrier reference, skips explicit N/A, and propagates any
    missing affected-carrier fact.
    Missing RAW/support is not an empty interruption ledger.
    """
    references = dict(references or {})
    if set(references)-{'external_fault_clear_minute', 'actual_source_restore_minute'}:
        raise ValueError('Unknown recovery evaluation reference')
    confirmation = dict(CONFIRMATION_SECONDS if confirmation_seconds is None else confirmation_seconds)
    if (set(confirmation) != set(CARRIERS) or
            any(not _number(v) or v < 0. for v in confirmation.values())):
        raise ValueError('Explicit nonnegative e/h/c confirmation seconds required')
    if confirmation['e'] != 0.:
        raise ValueError('Electric confirmation remains instantaneous; short real interruptions stay visible')
    cells = {(p, k): dict(requested_kWh=0., delivered_kWh=0., ENS_kWh=0.,
        disconnected_ENS_kWh=0., connected_unserved_ENS_kWh=0.) for p in PRIORITIES for k in CARRIERS}
    intervals = {key: [] for key in cells}
    segments = payload.get('segments', [])
    origin = payload.get('continuation_start_minute', 0.) or 0.
    observed, support = float(origin), bool(segments)
    for segment in segments:
        a, b = segment.get('start_minute'), segment.get('end_minute')
        if not all(_number(v) for v in (a, b)) or b <= a or a < observed-1e-8:
            raise ValueError('Invalid or overlapping actual service segment')
        if a > observed+1e-8:
            support = False
        observed = float(b)
        dt_h = (b-a)/60.
        ledger = segment.get('service_request_ledger', {})
        arrays = [ledger.get(k) for k in ('original_demand_kW', 'active_cut_kW', 'connected_unserved_kW', 'delivered_kW')]
        if any(not isinstance(arr, list) or len(arr) != 4 or any(not isinstance(r, list) or len(r) != 3 for r in arr) for arr in arrays):
            raise ValueError('Missing or invalid actual service ledger matrix')
        for ip, priority in enumerate(PRIORITIES):
            for ik, carrier in enumerate(CARRIERS):
                values = [arr[ip][ik] for arr in arrays]
                if not all(_number(v) and v >= -1e-8 for v in values):
                    raise ValueError('Nonfinite or negative actual service ledger')
                request, cut, unserved, delivered = map(float, values)
                if abs(request-cut-unserved-delivered) > 1e-7:
                    raise ValueError('Inconsistent actual service ledger')
                short = cut+unserved
                row = cells[priority, carrier]
                for key, value in (('requested_kWh', request), ('delivered_kWh', delivered), ('ENS_kWh', short),
                                   ('disconnected_ENS_kWh', cut), ('connected_unserved_ENS_kWh', unserved)):
                    row[key] += value*dt_h
                if short > .001 and (short >= .01 or (request > .001 and short/request >= .001)):
                    intervals[priority, carrier].append([a, b])
    for (p, k), row in cells.items():
        row.update(_raw_times(intervals[p, k]), confirmation_seconds=confirmation[k])
        episodes, candidates = _cell_episodes(row['intervals'], observed, confirmation[k])
        _finish(row, episodes, candidates, observed, support, references, [k])
        if not support and not segments:
            for field in ('requested_kWh', 'delivered_kWh', 'ENS_kWh', 'disconnected_ENS_kWh',
                          'connected_unserved_ENS_kWh', 'material_minutes', 'longest_material_minutes',
                          'interruption_count', 'repeat_interruption_count'):
                row[field] = None

    def union(keys):
        selected = [cells[key] for key in keys]
        row = _raw_times([interval for cell in selected for interval in cell['intervals']])
        episodes, candidates = _union_episodes(selected, observed)
        affected = sorted({k for p, k in keys if cells[p, k]['engineering_episodes']})
        result = _finish(row, episodes, candidates, observed, support, references, affected)
        if not support and not segments:
            for field in ('material_minutes', 'longest_material_minutes', 'interruption_count', 'repeat_interruption_count'):
                result[field] = None
        return result

    requested = payload.get('summary', {}).get('requested_end_minute')
    return dict(contract_id=CONTRACT_ID,
        definition=dict(confirmation_seconds=confirmation,
            materiality='V22 original per-cell power rule, unchanged',
            engineering_duration='first shortage to confirmed candidate service-resumption; unconfirmed to observation end',
            union='overlapping constituent confirmation windows; each carrier retains its own rule',
            references_are_evaluation_only=True, clinical_standard=False),
        carrier_service={p: {k: cells[p, k] for k in CARRIERS} for p in PRIORITIES},
        priority_union={p: union([(p, k) for k in CARRIERS]) for p in PRIORITIES},
        critical_union=union([(p, k) for p in PRIORITIES[:2] for k in CARRIERS]),
        observed_from_minute=origin, observed_until_minute=observed if segments else None,
        requested_end_minute=requested, support_complete=support,
        complete=bool(support and _number(requested) and abs(requested-observed) <= 1e-7))
