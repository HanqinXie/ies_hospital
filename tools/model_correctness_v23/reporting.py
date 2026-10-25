"""Compact, read-only recovery export from explicit native trajectory files."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path

from .metrics import CONTRACT_ID, CARRIERS, PRIORITIES, build_reliability_metrics


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _write(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def export_recovery_tables(runs, output, *, references=None, command=None):
    """Export RECOVERY (12 cells+4 unions+critical) and RUNS recovery columns.

    ``references`` maps explicit run IDs to evaluator-only reference facts.
    Existing RESULT metrics and native events are never modified. Missing or
    unreadable RAW is reported missing, with its error and digest in the receipt.
    """
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Output must be a fresh directory: '+str(output))
    paths = [Path(value).resolve() for value in runs]
    paths = [p.parent if p.name.lower() == 'result.json' else p for p in paths]
    if not paths:
        raise ValueError('At least one explicit run is required')
    if len({str(p).casefold() for p in paths}) != len(paths):
        raise ValueError('Duplicate explicit RAW inputs')
    table, run_rows, inputs = [], [], []
    for run in paths:
        result_path, raw_path = run/'RESULT.json', run/'trajectory.json.gz'
        record, payload, errors = {}, {}, []
        try:
            record = json.loads(result_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            errors.append('RESULT '+type(exc).__name__+': '+str(exc))
        try:
            with gzip.open(raw_path, 'rt', encoding='utf-8-sig') as stream:
                payload = json.load(stream)
        except (OSError, ValueError) as exc:
            errors.append('RAW '+type(exc).__name__+': '+str(exc))
        try:
            metrics = build_reliability_metrics(payload, references=(references or {}).get(run.name))
        except ValueError as exc:
            errors.append('RAW ledger ValueError: '+str(exc))
            metrics = build_reliability_metrics({})
        full = metrics['complete'] and record.get('status') == 'completed'
        base = dict(run_id=run.name, model_id=record.get('model_id'), source_sha256=record.get('source_sha256'),
            result_status=record.get('status'), recovery_contract_id=CONTRACT_ID,
            metrics_scope='full_episode' if full else 'observed_prefix' if metrics['support_complete'] else 'missing_support',
            observation_end_minute=metrics['observed_until_minute'])
        targets = [('carrier_service', p, k, metrics['carrier_service'][p][k]) for p in PRIORITIES for k in CARRIERS]
        targets += [('priority_union', p, None, metrics['priority_union'][p]) for p in PRIORITIES]
        targets += [('critical_union', None, None, metrics['critical_union'])]
        for scope, priority, carrier, values in targets:
            scalar = {key: value for key, value in values.items() if not isinstance(value, (list, dict))}
            table.append(dict(base, scope=scope, priority=priority, carrier=carrier, **scalar))
        union = metrics['critical_union']
        scalar = {key: value for key, value in union.items() if not isinstance(value, (list, dict))}
        columns = {'critical_union_'+key: value if full or not isinstance(value, (int, float)) else None
            for key, value in scalar.items()}
        columns.update({'observed_critical_union_'+key: value for key, value in scalar.items()})
        run_rows.append(dict(base, **columns))
        inputs.append(dict(run_id=run.name, RESULT_path=str(result_path), RESULT_sha256=_sha(result_path),
            RAW_path=str(raw_path), RAW_sha256=_sha(raw_path), read_errors=errors,
            references=(references or {}).get(run.name), recovery_status=union['recovery_status']))
    output.mkdir(parents=True, exist_ok=False)
    _write(output/'RECOVERY.csv', table)
    _write(output/'RUNS.csv', run_rows)
    receipt = dict(contract_id=CONTRACT_ID, inputs=inputs, command=command, read_only_inputs=True,
        original_RESULT_metrics_retained=True, raw_events_retained=True, environment_runs=0, solver_calls=0,
        table_rows=dict(RECOVERY=len(table), RUNS=len(run_rows)),
        recovery_minutes_definition='nonnegative wait after explicit external-fault-clear fact; absolute resumption and confirmation separately exported',
        status_definition='not_applicable: no interruption; censored: no confirmed restoration by observation end; missing: RAW/support/reference absent; observed: restoration confirmed')
    (output/'RECEIPT.json').write_text(json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='+', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--references', type=Path)
    args = parser.parse_args(argv)
    references = json.loads(args.references.read_text(encoding='utf-8-sig')) if args.references else None
    receipt = export_recovery_tables(args.runs, args.output, references=references, command=argv)
    print(json.dumps(receipt['table_rows']))


if __name__ == '__main__':
    main()
