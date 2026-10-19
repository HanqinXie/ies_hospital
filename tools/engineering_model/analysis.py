"""One-pass analysis of the exporter's already-open actual payload.

No environment is constructed, no solver is called and no trajectory is read
here. Original nineteen targets retain their original window/null semantics;
engineering operating statistics have separately declared definitions.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import numpy as np
from hospital_ies.dataset_export import json_clean
from tools.control_improvement.branches import window_targets
from tools.executed_service.timeline import extract_command_timeline
from tools.coordinated_dispatch.recovery import extract_execution_recovery

SCHEMA = 'hospital_ies.engineering_saved_analysis.v14.v1'
GT_POSITIVE_KW = 1e-4  # Operating count only, never a service/failure threshold.
TIME_EPS = 1e-8
MATRIX_EPS = 1e-6  # Accounting identity roundoff; original service scoring is unchanged.
GT_DEVICES = ('GT_1', 'GT_2', 'GT_3')


def _number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Nonfinite saved engineering value')
    return value


def _matrix(value, label):
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (4, 3) or not np.isfinite(matrix).all():
        raise ValueError(label + ' must be a finite 4-by-3 matrix')
    return matrix


def _actual_matrices(segment):
    loads = _matrix(segment['loads_kW'], 'loads')
    total = _matrix(segment['unserved_kW'], 'unserved')
    if 'disconnected_loads_kW' not in segment or 'connected_loads_kW' not in segment:
        raise ValueError('Actual disconnected/connected matrices are required, not inferred from planned loss')
    cuts = _matrix(segment['disconnected_loads_kW'], 'disconnected')
    connected = _matrix(segment['connected_loads_kW'], 'connected')
    if (np.min(loads) < -MATRIX_EPS or np.min(cuts) < -MATRIX_EPS or
            np.min(connected) < -MATRIX_EPS or np.max(cuts-loads) > MATRIX_EPS or
            np.max(np.abs(loads-connected-cuts)) > MATRIX_EPS):
        raise ValueError('Actual disconnected/connected demand identity is inconsistent')
    remaining = total-cuts
    if np.min(remaining) < -MATRIX_EPS or np.max(total-loads) > MATRIX_EPS:
        raise ValueError('Total unserved must include actual disconnected demand')
    # Retain the signed roundoff residual separately; only this derived
    # nonnegative decomposition is clipped, never original total ENS/classifier.
    return loads, total, cuts, connected, np.maximum(0., remaining), float(np.min(remaining))


def _initial_gt(payload):
    initial = payload.get('initial_state') or {}
    state = initial.get('state', initial)
    values = state.get('gt_prev')
    return None if values is None else [_number(x) for x in values]


def _gt_operation(payload, observed):
    initial = _initial_gt(payload)
    previous = initial[:] if initial is not None else None
    runs = {name: [] for name in GT_DEVICES}; active = {}; transitions = []
    if initial is not None:
        for i, name in enumerate(GT_DEVICES):
            if initial[i] > GT_POSITIVE_KW:
                active[name] = dict(start_minute=0., left_censored=True)

    def edge(i, time, before, after, basis, command_id):
        name = GT_DEVICES[i]
        before_on = before is not None and before > GT_POSITIVE_KW
        after_on = after > GT_POSITIVE_KW
        if before is None:
            if after_on:
                active[name] = dict(start_minute=time, left_censored=True)
            return
        if before_on == after_on:
            return
        transitions.append(dict(device=name, time_minute=float(time),
            kind='activated' if after_on else 'deactivated', power_before_kW=before,
            power_after_kW=after, basis=basis, command_id=command_id))
        if after_on:
            active[name] = dict(start_minute=float(time), left_censored=False)
        elif name in active:
            run = active.pop(name)
            runs[name].append(dict(run, end_minute=float(time), right_censored=False,
                observed_until_minute=float(time), observed_duration_minutes=float(time)-run['start_minute']))

    for segment in payload['segments']:
        a, b = map(_number, (segment['start_minute'], segment['end_minute']))
        start = [_number(x) for x in segment.get('source_start_gt_kW', segment['action']['gt_electric_kW'])]
        end = [_number(x) for x in segment.get('source_end_gt_kW', start)]
        if len(start) != 3 or len(end) != 3:
            raise ValueError('Exactly three actual GT ports required')
        for i in range(3):
            edge(i, a, None if previous is None else previous[i], start[i], 'actual_segment_boundary', segment.get('command_id'))
            if (start[i] > GT_POSITIVE_KW) != (end[i] > GT_POSITIVE_KW):
                crossing = a+(GT_POSITIVE_KW-start[i])/(end[i]-start[i])*(b-a)
                edge(i, crossing, start[i], end[i], 'linear_segment_threshold_crossing', segment.get('command_id'))
        previous = end
        terminal = segment.get('terminal_action')
        if terminal is not None:
            terminal_gt = [_number(x) for x in terminal['gt_electric_kW']]
            for i in range(3):
                edge(i, b, previous[i], terminal_gt[i], 'actual_terminal_action', segment.get('command_id'))
            previous = terminal_gt
    for name, run in active.items():
        runs[name].append(dict(run, end_minute=None, right_censored=True,
            observed_until_minute=observed, observed_duration_minutes=observed-run['start_minute']))
    answer = {}
    for name in GT_DEVICES:
        events = [e for e in payload.get('events', []) if e.get('device') == name and
                  _number(e.get('time_minute', 0.)) <= observed+TIME_EPS]
        completed = [e for e in events if e.get('kind') == 'gt_start_completed']
        answer[name] = dict(
            zero_to_positive_count=sum(t['device'] == name and t['kind'] == 'activated' for t in transitions),
            positive_to_zero_count=sum(t['device'] == name and t['kind'] == 'deactivated' for t in transitions),
            initial_online=None if initial is None else initial[GT_DEVICES.index(name)] > GT_POSITIVE_KW,
            start_requested_count=sum(e.get('kind') == 'gt_start_requested' for e in events),
            start_process_begun_count=sum(e.get('kind') == 'gt_start_process_begun' for e in events),
            start_completed_event_count=len(completed),
            start_completed_positive_count=sum(_number(e.get('actual_kW', 0.)) > GT_POSITIVE_KW for e in completed),
            observed_running_minutes=sum(run['observed_duration_minutes'] for run in runs[name]), runs=runs[name])
    return answer, sorted(transitions, key=lambda r: (r['time_minute'], r['device']))


def _window(payload, env, start, end, matrices, gt, transitions):
    original = window_targets(payload, start, end-start, env)
    observed = _number(payload['summary']['observed_until_minute'])
    full = bool(original['full_horizon'])
    total = np.zeros((4, 3)); cuts = total.copy(); connected_loss = total.copy()
    equivalent = total.copy(); material = total.copy(); longest = total.copy(); trailing = total.copy()
    cfg = getattr(env, 'config', {}) if env is not None else {}
    tol = float(cfg.get('p0_solver_feasibility_tol_kW', 1e-3))
    absolute = float(cfg.get('p0_unserved_abs_tol_kW', .01))
    relative = float(cfg.get('p0_unserved_rel_tol', .001))
    source_energy = dict(GT_1=0., GT_2=0., GT_3=0., GRID=0., DIESEL=0.)
    p0_minutes = 0.; balance = None; steam = None; electrical = None
    recovery_runs = []; failing = None; clean_after_failure = []
    for s, (loads, loss, disconnected, connected, unmet, residual) in zip(payload['segments'], matrices):
        a = max(start, float(s['start_minute'])); b = min(end, float(s['end_minute']))
        if b <= a:
            continue
        minutes = b-a; dt = minutes/60.
        total += loss*dt; cuts += disconnected*dt; connected_loss += unmet*dt
        ratio = np.divide(loss, loads, out=np.zeros_like(loss), where=loads > 0.)
        equivalent += ratio*minutes
        flag = (loss > tol) & ((loss >= absolute) | (ratio >= relative))
        material += flag*minutes; trailing = np.where(flag, trailing+minutes, 0.)
        longest = np.maximum(longest, trailing)
        if s['classification']['p0_guarantee_failure']:
            p0_minutes += minutes
            if failing is None:
                failing = dict(start_minute=a, end_minute=b)
            else:
                failing['end_minute'] = b
        elif failing is not None:
            recovery_runs.append(failing); failing = None
        action = s['action']
        mean_gt = np.asarray(action['gt_electric_kW'], dtype=float)
        if 'source_start_gt_kW' in s and 'source_end_gt_kW' in s:
            left = np.asarray(s['source_start_gt_kW'], dtype=float)
            right = np.asarray(s['source_end_gt_kW'], dtype=float)
            length = float(s['end_minute'])-float(s['start_minute'])
            mean_fraction = ((a+b)/2.-float(s['start_minute']))/length
            mean_gt = left+(right-left)*mean_fraction
        powers = [*mean_gt, action['grid_import_kW'], action['diesel_kW']]
        for name, power in zip(source_energy, powers):
            source_energy[name] += _number(power)*dt
        if 'energy_balance_residuals' in s:
            value = max(map(abs, map(_number, s['energy_balance_residuals'])), default=0.)
            balance = value if balance is None else max(balance, value)
        if 'steam_balance_residual_lbph' in s:
            value = abs(_number(s['steam_balance_residual_lbph']))
            steam = value if steam is None else max(steam, value)
        value = abs(sum(powers)-float((loads-loss)[:, 0].sum())-_number(s.get('auxiliary_electric_kW', 0.)))
        electrical = value if electrical is None else max(electrical, value)
    if failing is not None:
        recovery_runs.append(failing)
    finish = min(end, max(start, observed))
    for i, failure in enumerate(recovery_runs):
        clean_end = recovery_runs[i+1]['start_minute'] if i+1 < len(recovery_runs) else finish
        if clean_end > failure['end_minute']+TIME_EPS:
            clean_after_failure.append([failure['end_minute'], clean_end])
    sustained = next(([a, a+30.] for a, b in clean_after_failure if b-a >= 30.-TIME_EPS), None)
    gt_rows = {}
    for name, state in gt.items():
        running = sum(max(0., min(end, run['observed_until_minute'])-max(start, run['start_minute'])) for run in state['runs'])
        gt_rows[name] = dict(observed_running_minutes=running,
            zero_to_positive_count=sum(t['device'] == name and t['kind'] == 'activated' and start <= t['time_minute'] < end for t in transitions),
            positive_to_zero_count=sum(t['device'] == name and t['kind'] == 'deactivated' and start <= t['time_minute'] < end for t in transitions))
    prefix = dict(total_ENS_kWh=total.tolist(), disconnected_ENS_kWh=cuts.tolist(),
        connected_unserved_ENS_kWh=connected_loss.tolist(), equivalent_loss_minutes=equivalent.tolist(),
        material_minutes=material.tolist(), longest_material_minutes=longest.tolist(),
        trailing_material_minutes=trailing.tolist(), P0_failure_minutes=p0_minutes,
        source_electric_energy_kWh=source_energy, gt_actual=gt_rows,
        max_saved_balance_residual_kW=balance, max_saved_steam_residual_lbph=steam,
        max_recomputed_electric_balance_residual_kW=electrical,
        service_recovery=dict(first_failure_minute=recovery_runs[0]['start_minute'] if recovery_runs else None,
            first_clean_minute=clean_after_failure[0][0] if clean_after_failure else None,
            confirmed_30min_interval=sustained,
            recurrence_after_30min_count=sum(r['start_minute'] >= sustained[1]-TIME_EPS for r in recovery_runs) if sustained else 0),
        material_thresholds=dict(solver_tol_kW=tol, absolute_kW=absolute, relative=relative))
    return dict(window_minutes=[start, end], complete=full, observed_minutes=original['observed_minutes'],
        original_19=original, observed_prefix=prefix, all_registered=copy.deepcopy(prefix) if full else None)


def analyze_payload(payload, env):
    """Consume precisely this in-memory payload; preserve old scoring verbatim."""
    summary = payload['summary']; observed = _number(summary['observed_until_minute'])
    requested = _number(summary['requested_end_minute'])
    if observed > requested+TIME_EPS:
        raise ValueError('Observed prefix exceeds requested trajectory')
    matrices = [_actual_matrices(s) for s in payload['segments']]
    gt, transitions = _gt_operation(payload, observed)
    definitions = [('primary', 60., 420.), ('full', 0., 600.)]
    if requested >= 1440.-TIME_EPS:
        definitions += [('long1440', 0., 1440.), ('extension_600_1440', 600., 1440.)]
    windows = {name: _window(payload, env, start, end, matrices, gt, transitions)
               for name, start, end in definitions}
    timeline = extract_command_timeline(payload, payload.get('scenario', {}))
    interval_rows = []
    for s, (_, _, _, _, unmet, residual) in zip(payload['segments'], matrices):
        row = {key: copy.deepcopy(s.get(key)) for key in
            ('start_minute', 'end_minute', 'command_id', 'availability', 'action', 'arrived_source_setpoints',
             'loads_kW', 'connected_loads_kW', 'disconnected_loads_kW', 'unserved_kW',
             'source_start_gt_kW', 'source_end_gt_kW', 'auxiliary_electric_kW',
             'energy_balance_residuals', 'steam_balance_residual_lbph', 'pre_state', 'post_state')}
        row.update(connected_unserved_kW=unmet.tolist(), signed_connected_unserved_min_kW=residual,
            p0_guarantee_failure=bool(s['classification']['p0_guarantee_failure']))
        interval_rows.append(row)
    events = [copy.deepcopy(e) for e in payload.get('events', []) if _number(e['time_minute']) <= observed+TIME_EPS]
    result = dict(schema=SCHEMA, identity=copy.deepcopy(payload.get('identity', {})),
        scenario_id=payload.get('scenario', {}).get('id'), analysis_class=payload.get('scenario', {}).get('analysis_class'),
        source_day_index=payload.get('scenario', {}).get('source_day_index'),
        execution_model_id=payload.get('execution_model_id', getattr(env, 'model_id', None)),
        observed_until_minute=observed, requested_end_minute=requested,
        unknown_tail_minutes=[observed, requested] if observed < requested-TIME_EPS else None,
        windows=windows, engineering=dict(actual_positive_threshold_kW=GT_POSITIVE_KW, gt=gt,
            gt_transitions=transitions, execution_recovery=extract_execution_recovery(payload),
            operating_edges_are_not_OEM_cold_starts=True, original_19_targets_unchanged=True,
            original_initial_online_not_counted_as_new_start=True, ENS_is_never_repaid=True,
            counts_include_observed_prefix_only=True, censored_run_end_is_not_a_shutdown=True),
        timelines=dict(command_lifecycle=timeline['command_rows'], execution_intervals=timeline['execution_intervals'],
            clamp_events=timeline['clamp_events'], events=events, actual_intervals=interval_rows),
        definitions=dict(connection='original demand = connected + physically disconnected; connected unmet = total unmet - disconnected',
            service='Original classifier/19 targets unchanged; added material-duration statistics are descriptive',
            startup='Requested, completion event and actual positive output are separately counted',
            recovery='30 continuous clean observed minutes is descriptive, not a clinical threshold',
            long_windows='Separate named windows, never pooled with original full600 or incident counts'),
        physical_episodes_started=0, additional_raw_opens=0, no_figures_generated=True)
    return json_clean(result)


def _write_csv(path, rows):
    columns = sorted({key for row in rows for key in row})
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, allow_nan=False)
                if isinstance(value, (dict, list, tuple)) else value for key, value in row.items()})


def export_rows(records, out):
    """Write caller-supplied analyses/metadata only; no raw-file reload."""
    records = json_clean(copy.deepcopy(list(records)))
    tables = {name: [] for name in ('TRAJECTORY_WINDOWS', 'GT_RUNS', 'GT_TRANSITIONS',
        'COMMAND_LIFECYCLE', 'COMMAND_EXECUTION_INTERVALS', 'EVENT_TIMELINE', 'ENGINEERING_INTERVALS')}
    unknown = 0
    for record in records:
        analysis = record.get('analysis', record)
        identity = {k: v for k, v in record.items() if k != 'analysis' and not isinstance(v, (dict, list))}
        identity.update({k: analysis.get(k) for k in ('scenario_id', 'analysis_class', 'source_day_index', 'execution_model_id')})
        identity.update(analysis.get('identity', {}))
        unknown += not analysis['windows']['full']['complete']
        for name, window in analysis['windows'].items():
            row = dict(identity, window=name, complete=window['complete'], observed_minutes=window['observed_minutes'],
                window_minutes=window['window_minutes'], all_registered_json=window['all_registered'],
                observed_prefix_json=window['observed_prefix'])
            original = window['original_19']
            row.update(dict(zip(original['target_names'], original['targets'])))
            row['target_mask_json'] = original['target_mask']
            tables['TRAJECTORY_WINDOWS'].append(row)
        for device, state in analysis['engineering']['gt'].items():
            tables['GT_RUNS'].extend(dict(identity, device=device, **run) for run in state['runs'])
        tables['GT_TRANSITIONS'].extend(dict(identity, **row) for row in analysis['engineering']['gt_transitions'])
        for key, name in (('command_lifecycle', 'COMMAND_LIFECYCLE'), ('execution_intervals', 'COMMAND_EXECUTION_INTERVALS'),
                          ('events', 'EVENT_TIMELINE'), ('actual_intervals', 'ENGINEERING_INTERVALS')):
            tables[name].extend(dict(identity, **row) for row in analysis['timelines'][key])
    destination = Path(out)
    if destination.resolve().drive.lower() == 'c:':
        raise ValueError('Use the D/E project output workspace')
    names = [*(name+'.csv' for name in tables), 'RESULTS.json', 'ANALYSIS_RECEIPT.json']
    if any((destination/name).exists() for name in names):
        raise FileExistsError('Keep prior analysis outputs immutable; use a fresh directory')
    destination.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        _write_csv(destination/(name+'.csv'), rows)
    with (destination/'RESULTS.json').open('x', encoding='utf8') as stream:
        json.dump(dict(schema=SCHEMA, records=records), stream, ensure_ascii=False, indent=2, allow_nan=False)
    receipt = dict(schema=SCHEMA+'.receipt', registered_trajectory_count=len(records),
        unknown_full_trajectory_count=unknown, table_row_counts={k: len(v) for k, v in tables.items()},
        physical_episodes_started=0, additional_raw_opens=0, original_19_targets_unchanged=True,
        original_final12_gate='not_verified', no_figures_generated=True,
        output_sha256={name: hashlib.sha256((destination/name).read_bytes()).hexdigest() for name in names[:-1]})
    with (destination/'ANALYSIS_RECEIPT.json').open('x', encoding='utf8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return receipt
