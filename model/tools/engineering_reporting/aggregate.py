"""Aggregate v14 saved analyses and registered denominators, without raw access.

Run: python -m tools.engineering_reporting.aggregate --run <v14 run directory>
Each call writes a fresh summary/review_NNNN directory. This is descriptive
reporting, not a replacement for the producer's audit or source registration.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path

SCHEMA = 'hospital_ies.engineering_campaign_reporting.v14.v1'
DEFAULT_REFERENCE = 'fixed_standby_constant_v12b'
TARGET_NAMES = ('P0_failure', 'P0_failure_minutes') + tuple(
    f'{p}_{c}_ENS_kWh' for p in ('P0', 'P1', 'P2', 'P3') for c in 'ehc') + (
    'P0_max_temperature_C', 'P1_max_temperature_C', 'P2_max_temperature_C',
    'secondary_GT_trips', 'actual_energy_fee_USD')
GROUP_FIELDS = ('phase', 'runtime_source_signature', 'model_id', 'model_config_signature', 'policy_id', 'analysis_class')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


def _read(path, inputs):
    raw = path.read_bytes()
    inputs[str(path)] = dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    return json.loads(raw.decode('utf8'))


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _targets(window):
    if window is None:
        return dict.fromkeys(TARGET_NAMES)
    score = window['original_19']
    if tuple(score['target_names']) != TARGET_NAMES or len(score['targets']) != 19:
        raise ValueError('Unsupported saved nineteen-target names or dimensions')
    if any(value is not None and not _finite(value) for value in score['targets']):
        raise ValueError('Nonfinite saved target must remain explicit unknown, not a fabricated number')
    return dict(zip(TARGET_NAMES, score['targets']))


def _engineering(window, analysis):
    if window is None:
        return {}
    prefix = window.get('observed_prefix') or {}; values = {}
    for field in ('total_ENS_kWh', 'disconnected_ENS_kWh', 'connected_unserved_ENS_kWh',
                  'equivalent_loss_minutes', 'material_minutes', 'longest_material_minutes', 'trailing_material_minutes'):
        matrix = prefix.get(field)
        if matrix is not None:
            if len(matrix) != 4 or any(len(row) != 3 for row in matrix):
                raise ValueError('Saved engineering service matrix must be 4-by-3')
            values.update({f'P{p}_{carrier}_{field}': matrix[p][k]
                           for p in range(4) for k, carrier in enumerate('ehc')})
    for field in ('P0_failure_minutes', 'max_saved_balance_residual_kW',
                  'max_saved_steam_residual_lbph', 'max_recomputed_electric_balance_residual_kW'):
        if field in prefix:
            values[field] = prefix[field]
    for device, state in (prefix.get('gt_actual') or {}).items():
        for key, value in state.items():
            values[device+'_'+key] = value
    for device, value in (prefix.get('source_electric_energy_kWh') or {}).items():
        values[device+'_electric_energy_kWh'] = value
    start, end = window['window_minutes']
    events = (analysis.get('timelines') or {}).get('events')
    if events is not None:
        for device in ('GT_1', 'GT_2', 'GT_3'):
            for event_kind in ('gt_start_requested', 'gt_start_process_begun', 'gt_start_completed',
                               'no_export_interlock', 'minimum_output_interlock'):
                values[device+'_'+event_kind+'_events'] = sum(e.get('device') == device and
                    e.get('kind') == event_kind and start <= e.get('time_minute', -1) < end for e in events)
    return values


def _stat(values, *, maximum=False):
    known = [float(v) for v in values if _finite(v)]
    statistic = max(known) if maximum and known else sum(known) if known else None
    return dict(known_count=len(known), unknown_count=len(values)-len(known),
        known_sum=sum(known) if known else None, known_max=max(known) if known else None,
        known_mean=sum(known)/len(known) if known else None,
        all_registered=statistic if len(known) == len(values) else None,
        aggregation='maximum' if maximum else 'sum')


def _maximum(name):
    return any(part in name for part in ('max_temperature', 'longest_', 'trailing_', 'max_saved_', 'max_recomputed_'))


def _window_rows(ledger):
    rows = []
    for item in ledger:
        analysis = item.get('analysis'); windows = analysis['windows'] if analysis is not None else {}
        names = list(windows) or ['primary', 'full']
        if analysis is None and item.get('requested_end_minute', 0.) >= 1440.:
            names += ['long1440', 'extension_600_1440']
        for name in names:
            window = windows.get(name)
            rows.append(dict({k: v for k, v in item.items() if k != 'analysis'}, window=name,
                complete=bool(window and window['complete']),
                observed_minutes=window.get('observed_minutes') if window else None,
                targets=_targets(window), engineering=_engineering(window, analysis or {})))
    return rows


def _groups(rows):
    by = defaultdict(list)
    for row in rows:
        by[tuple(row.get(k) for k in (*GROUP_FIELDS, 'window'))].append(row)
    answer = []
    for key, group in sorted(by.items(), key=lambda pair: tuple(str(x) for x in pair[0])):
        identity = dict(zip((*GROUP_FIELDS, 'window'), key))
        fields = sorted({name for row in group for name in row['engineering']})
        engineering = {}
        for name in fields:
            vals = [row['engineering'].get(name) for row in group]
            stat = _stat(vals, maximum=_maximum(name))
            # Prefix values describe the observed cases only. A missing saved
            # analysis has no observed zero and never becomes a full total.
            stat.update(observed_prefix_count=stat['known_count'],
                observed_prefix_sum=stat['known_sum'], observed_prefix_max=stat['known_max'])
            if not all(row['complete'] for row in group):
                stat['all_registered'] = None
            engineering[name] = stat
        answer.append(dict(identity, registered_pairs=len(group),
            complete_pairs=sum(row['complete'] for row in group),
            unknown_pairs=sum(not row['complete'] for row in group),
            missing_analysis_pairs=sum(row['analysis_status'] == 'missing_saved_analysis' for row in group),
            batches=sorted({row['batch'] for row in group}),
            metrics={name: _stat([row['targets'].get(name) for row in group], maximum=_maximum(name)) for name in TARGET_NAMES},
            engineering=engineering,
            status_counts=dict(Counter(row['status'] for row in group))))
    return answer


def _contrast(reference, candidate, *, kind):
    answer = {key: candidate.get(key) for key in ('phase', 'runtime_source_signature', 'analysis_class', 'scenario_id', 'window')}
    answer.update(comparison=kind, reference_batch=reference['batch'], candidate_batch=candidate['batch'],
        reference_complete=reference['complete'], candidate_complete=candidate['complete'],
        both_windows_complete=reference['complete'] and candidate['complete'],
        delta_semantics='candidate minus reference; null if either saved target is unknown',
        deltas={name: candidate['targets'][name]-reference['targets'][name]
            if _finite(candidate['targets'][name]) and _finite(reference['targets'][name]) else None for name in TARGET_NAMES},
        engineering_deltas={name: candidate['engineering'][name]-reference['engineering'][name]
            if _finite(candidate['engineering'].get(name)) and _finite(reference['engineering'].get(name)) else None
            for name in sorted(set(reference['engineering']) | set(candidate['engineering']))})
    if kind == 'policy':
        answer.update(model_id=candidate['model_id'], model_config_signature=candidate['model_config_signature'],
            reference_policy_id=reference['policy_id'], candidate_policy_id=candidate['policy_id'])
    else:
        answer.update(policy_id=candidate['policy_id'], reference_model_id=reference['model_id'],
            candidate_model_id=candidate['model_id'], reference_model_config_signature=reference['model_config_signature'],
            candidate_model_config_signature=candidate['model_config_signature'])
    return answer


def _comparisons(rows, reference_policy):
    policy_groups = defaultdict(lambda: defaultdict(list)); model_groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        key = tuple(row.get(k) for k in ('phase', 'runtime_source_signature', 'analysis_class', 'scenario_id', 'window'))
        policy_groups[key+(row['model_id'], row['model_config_signature'])][row['policy_id']].append(row)
        model_groups[key+(row['policy_id'],)][(row['model_id'], row['model_config_signature'])].append(row)
    policies, models, ambiguous = [], [], []
    for key, candidates in policy_groups.items():
        references = candidates.get(reference_policy, [])
        for pid, actual in candidates.items():
            if pid == reference_policy:
                continue
            if len(references) == len(actual) == 1:
                policies.append(_contrast(references[0], actual[0], kind='policy'))
            elif references:
                ambiguous.append(dict(comparison='policy', key=list(key), reference_policy_id=reference_policy,
                    candidate_policy_id=pid, reference_rows=len(references), candidate_rows=len(actual),
                    status='repeated_case_ambiguous; no arbitrary choice or cartesian pairing'))
    for key, candidates in model_groups.items():
        references = [(pair, values) for pair, values in candidates.items() if pair[0] == 'M0']
        for (model, config), actual in candidates.items():
            if model == 'M0':
                continue
            for pair, references_for_config in references:
                if len(references_for_config) == len(actual) == 1:
                    models.append(_contrast(references_for_config[0], actual[0], kind='model'))
                else:
                    ambiguous.append(dict(comparison='model', key=list(key), reference_model_id=pair[0],
                        candidate_model_id=model, reference_rows=len(references_for_config), candidate_rows=len(actual),
                        status='repeated_case_ambiguous; no arbitrary choice or cartesian pairing'))
    return policies, models, ambiguous


def collect(run, *, reference_policy=DEFAULT_REFERENCE):
    """Read saved analyses plus plan/manifest metadata; include every plan pair."""
    run = Path(run).resolve(); inputs = {}; ledger = []; batches = []
    plans = sorted((run/'experiments').glob('*/plan.json'))
    if not plans:
        raise ValueError('No registered experiment plans exist; no result is manufactured')
    for plan_path in plans:
        folder = plan_path.parent; plan = _read(plan_path, inputs)
        manifest = _read(folder/'manifest.json', inputs)
        scenarios = {s['id']: s for s in manifest['scenarios']}
        sids = plan['scenario_ids']; pids = [s['policy_id'] for s in plan['policy_specs']]
        expected = set(itertools.product(sids, pids))
        if len(expected) != plan['registered_pairs'] or len(sids) != len(set(sids)) or len(pids) != len(set(pids)):
            raise ValueError('Duplicate or inconsistent registered panel')
        path = folder/'analysis/RESULTS.json'
        values = _read(path, inputs)['records'] if path.exists() else []
        actual = {}
        for record in values:
            key = (record['scenario_id'], record['policy_id'])
            if key not in expected or key in actual or record.get('model_id', plan['model_id']) != plan['model_id']:
                raise ValueError('Saved analysis row differs from registered panel/model')
            actual[key] = record
        runtime = _digest(plan.get('source_sha256', {})); config = _digest(plan.get('model_config', {}))
        for sid, pid in sorted(expected):
            if sid not in scenarios:
                raise ValueError('Registered scenario missing from manifest')
            scene = scenarios[sid]; record = actual.get((sid, pid)); analysis = record.get('analysis') if record else None
            if record and record.get('model_config_signature', config) != config:
                raise ValueError('Saved model configuration differs from registered batch')
            ledger.append(dict(batch=folder.name, phase=plan['phase'], model_id=plan['model_id'],
                model_config_signature=config, runtime_source_signature=runtime, policy_id=pid, scenario_id=sid,
                analysis_class=scene.get('analysis_class', 'not_recorded'), source_day_index=scene.get('source_day_index'),
                source_v13_scenario_id=scene.get('source_v13_scenario_id'),
                requested_end_minute=analysis.get('requested_end_minute') if analysis else
                    float(scene.get('duration_steps', 0))*float(scene.get('step_minutes', 15.)),
                status=record.get('status', 'not_recorded') if record else 'missing_saved_analysis',
                analysis_status='saved_analysis' if analysis is not None else 'missing_saved_analysis',
                analysis=analysis))
        batches.append(dict(batch=folder.name, phase=plan['phase'], model_id=plan['model_id'],
            registered_pairs=len(expected), saved_analysis_pairs=sum(row.get('analysis') is not None for row in actual.values()),
            runtime_source_signature=runtime, model_config_signature=config,
            source_plan_path=str(plan_path), saved_results_present=path.exists()))
    rows = _window_rows(ledger)
    policies, models, ambiguous = _comparisons(rows, reference_policy)
    repeats = Counter(tuple(row.get(k) for k in (*GROUP_FIELDS, 'scenario_id')) for row in ledger)
    full = [row for row in rows if row['window'] == 'full']
    compact = [{k: v for k, v in row.items() if k != 'analysis'} for row in ledger]
    return dict(schema=SCHEMA, run_directory=str(run), registered_pairs=len(ledger),
        saved_analysis_pairs=sum(row['analysis'] is not None for row in ledger),
        missing_analysis_pairs=sum(row['analysis'] is None for row in ledger),
        complete_full_pairs=sum(row['complete'] for row in full), unknown_full_pairs=sum(not row['complete'] for row in full),
        repeated_case_policy_count=sum(n-1 for n in repeats.values()), reference_policy_id=reference_policy,
        batches=batches, registered_pair_ledger=compact, trajectory_windows=rows,
        groups=_groups(rows), policy_contrasts=policies, model_contrasts=models, ambiguous_comparisons=ambiguous,
        input_bindings=inputs, raw_opens=0, physical_episodes_started=0,
        original_19_unchanged=True, final12_gate='not_verified; unopened',
        definitions=dict(unknown='Incomplete or missing saved analysis; never zero-filled; missing is separately counted',
            denominator='All scenario-policy pairs in every registered batch, including pending/missing analyses',
            repeats='Registered repeat attempts retained; ambiguous cross-batch pairs are not arbitrarily selected',
            groups='Phase/source version/model/config/policy/class/window kept separate; N2 and L4a never in four-core denominator',
            comparisons='Exact scenario IDs, same phase/runtime; model effects compare same policy; policy effects same model/config',
            GT='Actual zero/positive edges at producer threshold 1e-4 kW; process/completion events separate; initial online excluded',
            service='Material-duration prefix statistics are descriptive; original P0 and carrier ENS remain unchanged',
            fees='Illustrative actual energy fees reported separately; they do not offset critical service harm',
            frequency='Designed/repeated same-hospital-year cases, not hospital failure probabilities'))


def _csv(path, rows):
    columns = sorted({key for row in rows for key in row})
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns); writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, allow_nan=False)
                if isinstance(value, (dict, list, tuple)) else value for key, value in row.items()})


def _flat_group(group):
    row = {key: value for key, value in group.items() if key not in ('metrics', 'engineering')}
    for prefix, collection in (('', group['metrics']), ('engineering_', group['engineering'])):
        for name, statistics in collection.items():
            for key, value in statistics.items():
                row[prefix+name+'_'+key] = value
    return row


def aggregate(run, out=None, *, reference_policy=DEFAULT_REFERENCE):
    """Create a fresh version under out (default RUN/summary) and return receipt."""
    report = collect(run, reference_policy=reference_policy)
    base = Path(out).resolve() if out is not None else Path(run).resolve()/'summary'
    if base.drive.lower() == 'c:':
        raise ValueError('Use D/E for project result outputs')
    base.mkdir(parents=True, exist_ok=True)
    for index in itertools.count(1):
        destination = base/f'review_{index:04d}'
        try:
            destination.mkdir()
            break
        except FileExistsError:
            continue
    tables = dict(REGISTERED_PAIRS=report['registered_pair_ledger'],
        CLASS_MODEL_POLICY_SUMMARY=[_flat_group(g) for g in report['groups']],
        TRAJECTORY_WINDOWS=[{**{k: v for k, v in row.items() if k != 'targets'}, **row['targets']} for row in report['trajectory_windows']],
        PAIRED_POLICY_WINDOWS=[{**{k: v for k, v in row.items() if k != 'deltas'}, **{k+'_delta': v for k, v in row['deltas'].items()}} for row in report['policy_contrasts']],
        PAIRED_MODEL_WINDOWS=[{**{k: v for k, v in row.items() if k != 'deltas'}, **{k+'_delta': v for k, v in row['deltas'].items()}} for row in report['model_contrasts']],
        AMBIGUOUS_COMPARISONS=report['ambiguous_comparisons'])
    for name, rows in tables.items():
        _csv(destination/(name+'.csv'), rows)
    with (destination/'CAMPAIGN_SUMMARY.json').open('x', encoding='utf8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    receipt = dict(schema=SCHEMA+'.receipt', generated_utc=datetime.now(timezone.utc).isoformat(),
        output_directory=str(destination), registered_pairs=report['registered_pairs'],
        saved_analysis_pairs=report['saved_analysis_pairs'], missing_analysis_pairs=report['missing_analysis_pairs'],
        unknown_full_pairs=report['unknown_full_pairs'], raw_opens=0, physical_episodes_started=0,
        input_bindings=report['input_bindings'],
        output_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(destination.iterdir()) if p.is_file()})
    with (destination/'REPORTING_RECEIPT.json').open('x', encoding='utf8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, help='Version-directory parent; default RUN/summary')
    parser.add_argument('--reference-policy', default=DEFAULT_REFERENCE)
    args = parser.parse_args(argv)
    receipt = aggregate(args.run, args.out, reference_policy=args.reference_policy)
    print(json.dumps({key: receipt[key] for key in ('output_directory', 'registered_pairs',
        'saved_analysis_pairs', 'missing_analysis_pairs', 'unknown_full_pairs')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
