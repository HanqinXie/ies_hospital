"""Export only explicitly listed RESULT/CASE files without opening trajectories.

Full-episode columns require a completed record and a consistent known horizon.
Recorded failed prefixes have separate ``observed_`` columns. Blank is unknown;
no missing result, cost, CO2, inventory or service quantity becomes zero. Explicit
pair differences are raw ``after - before`` values, with no winner selection.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

CONTRACT_ID = 'hospital_ies.explicit_result_export.v22.v1'
PRIORITIES = ('P0', 'P1', 'P2', 'P3')
CARRIERS = ('e', 'h', 'c')
SERVICE_FIELDS = ('requested_kWh', 'delivered_kWh', 'ENS_kWh', 'disconnected_ENS_kWh',
    'connected_unserved_ENS_kWh', 'material_minutes', 'longest_material_minutes',
    'interruption_count', 'repeat_interruption_count', 'first_material_minute', 'recovery_minutes')
TIME_FIELDS = ('material_minutes', 'longest_material_minutes', 'interruption_count',
               'repeat_interruption_count', 'first_material_minute', 'recovery_minutes')
STORAGE_FIELDS = ('initial_kWh', 'final_kWh', 'charged_input_kWh', 'discharged_kWh',
                  'standing_loss_kWh', 'conversion_loss_kWh', 'balance_residual_kWh')
STORAGE_TOTAL_FIELDS = ('charged_input_kWh', 'discharged_kWh', 'standing_loss_kWh',
                       'conversion_loss_kWh')
ECONOMIC_FIELDS = ('energy_cost_USD', 'accounting_cost_USD_sum_segments', 'DG_fee_correction_USD',
    'initial_inventory_grid_input_kWh_conditional', 'initial_inventory_fee_USD_conditional',
    'total_CO2_kg_conditional')
ENERGY_FIELDS = ('grid_import_kWh', 'diesel_kWh', 'gt_electric_kWh',
    'electric_chiller_cooling_kWh', 'absorption_cooling_kWh', 'gt_fuel_MMBtu', 'boiler_steam_lb',
    'DG_mechanical_work_kWh', 'DG_electrical_work_kWh', 'DG_loss_work_kWh',
    'DG_rotor_delta_kWh', 'DG_fuel_budget_debit_kWh_equivalent')


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    if not path.is_file():
        return {}, None, 'missing'
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {}, None, type(exc).__name__ + ': ' + str(exc)
    try:
        value = json.loads(data.decode('utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('JSON root is not an object')
        return value, _sha(data), None
    except (UnicodeError, ValueError) as exc:
        return {}, _sha(data), type(exc).__name__ + ': ' + str(exc)


def _result_path(value):
    path = Path(value).resolve()
    if path.name.lower() == 'result.json':
        return path
    if path.suffix.lower() == '.json' and not path.is_dir():
        raise ValueError('Supply a run directory or its RESULT.json explicitly')
    return path/'RESULT.json'


def _key(path):
    return str(path.resolve()).casefold()


def _support(record, metrics):
    def quantity(name):
        top = _number(record.get(name))
        return top if top is not None else _number(metrics.get(name))
    requested, observed, unknown = [quantity(name) for name in
        ('requested_end_minute', 'observed_until_minute', 'unknown_tail_minutes')]
    consistent = all(value is not None and value >= 0. for value in (requested, observed, unknown))
    for name in ('requested_end_minute', 'observed_until_minute', 'unknown_tail_minutes'):
        top, inner = _number(record.get(name)), _number(metrics.get(name))
        if top is not None and inner is not None and abs(top-inner) > 1e-7:
            consistent = False
    full = (record.get('status') == 'completed' and metrics.get('complete') is True
            and consistent and abs(requested-observed) <= 1e-7 and unknown <= 1e-7)
    scope = ('full_episode' if full else 'observed_prefix' if metrics and observed is not None
             else 'recorded_metrics_unverified' if metrics else 'no_recorded_metrics')
    return requested, observed, unknown, bool(full), scope


def _load(path):
    record, result_sha, error = _read(path)
    case_path = path.with_name('CASE.json')
    case_file, case_sha, case_error = _read(case_path)
    case = _mapping(case_file.get('case'))
    metrics = _mapping(record.get('metrics'))
    requested, observed, unknown, full, scope = _support(record, metrics)
    status = record.get('status')
    if not isinstance(status, str):
        status = 'missing_result' if error == 'missing' else 'unreadable_result' if error else 'unknown_status'
    policy = _mapping(record.get('policy')) or _mapping(case.get('policy'))
    configuration = record.get('configuration_id', case.get('configuration'))
    if configuration is None:
        configuration = _mapping(_mapping(record.get('model_config')).get('sci_engineering')).get('configuration_id')
    identity = dict(run_id=path.parent.name, result_path=str(path), case_id=record.get('case_id', case.get('id')),
        scenario_id=_mapping(case.get('scenario')).get('id'), configuration_id=configuration,
        policy_id=policy.get('policy_id'), policy_alias=policy.get('alias'), model_id=record.get('model_id'),
        source_sha256=record.get('source_sha256'), git_commit=record.get('git_commit'),
        model_config_sha256=record.get('model_config_sha256'),
        resolved_model_config_sha256=record.get('resolved_model_config_sha256'),
        status=status, metrics_scope=scope, full_episode_valid=full,
        requested_end_minute=requested, observed_until_minute=observed, unknown_tail_minutes=unknown)
    return dict(record=record, metrics=metrics, full=full, identity=identity,
        input=dict(RESULT_path=str(path), RESULT_sha256=result_sha, RESULT_read_error=error,
                   CASE_path=str(case_path), CASE_sha256=case_sha,
                   CASE_read_error=None if case_error == 'missing' else case_error))


def _quantities(row, values, names, full):
    for name in names:
        observed = _number(values.get(name))
        row[name] = observed if full else None
        row['observed_'+name] = observed


def _tables(item):
    record, metrics, full, identity = [item[name] for name in ('record', 'metrics', 'full', 'identity')]
    economics = _mapping(metrics.get('economics'))
    emissions = _mapping(economics.get('emissions_conditional'))
    economy = dict(economics, total_CO2_kg_conditional=emissions.get('total_CO2_kg_conditional'))
    error = _mapping(record.get('error'))
    run = dict(identity, actual_episode_attempts=_count(record.get('actual_episode_attempts')),
        historical_attempts_added=_count(record.get('historical_attempts_added')),
        legacy_runtime=record.get('legacy_runtime'), evaluation_role=record.get('evaluation_role'),
        independent_validation=record.get('independent_validation'),
        started_utc=record.get('started_utc'), completed_utc=record.get('completed_utc'),
        elapsed_seconds=_number(record.get('elapsed_seconds')),
        error_type=error.get('type'), error_message=error.get('message'),
        RESULT_sha256=item['input']['RESULT_sha256'], CASE_sha256=item['input']['CASE_sha256'],
        RESULT_read_error=item['input']['RESULT_read_error'], CASE_read_error=item['input']['CASE_read_error'],
        emissions_factor_sha256=emissions.get('emissions_factor_sha256'), CO2_boundary=emissions.get('CO2_boundary'),
        price_year=economics.get('price_year'), price_currency=economics.get('price_currency'))
    _quantities(run, economy, ECONOMIC_FIELDS, full)
    energy = _mapping(metrics.get('actual_energy'))
    for name in ENERGY_FIELDS:
        observed = _number(energy.get(name))
        run['actual_energy_'+name] = observed if full else None
        run['observed_actual_energy_'+name] = observed
    critical = _mapping(metrics.get('critical_union'))
    for name in TIME_FIELDS:
        run['critical_union_'+name] = _number(critical.get(name)) if full else None
        run['observed_critical_union_'+name] = _number(critical.get(name))
    service = []
    for priority in PRIORITIES:
        union = _mapping(_mapping(metrics.get('priority_union')).get(priority))
        for carrier in CARRIERS:
            values = _mapping(_mapping(_mapping(metrics.get('carrier_service')).get(priority)).get(carrier))
            row = dict(identity, priority=priority, carrier=carrier, energy_unit='kWh_'+carrier)
            _quantities(row, values, SERVICE_FIELDS, full)
            for name in TIME_FIELDS:
                row['priority_union_'+name] = _number(union.get(name)) if full else None
                row['observed_priority_union_'+name] = _number(union.get(name))
            service.append(row)
            run[priority+'_'+carrier+'_ENS_kWh'] = row['ENS_kWh']
            run[priority+'_'+carrier+'_observed_ENS_kWh'] = row['observed_ENS_kWh']
    storage = []
    stores, initial = _mapping(metrics.get('storage')), _mapping(metrics.get('initial_storage'))
    for name in dict.fromkeys(('ups', 'cold_store', *stores, *initial)):
        saved = _mapping(stores.get(name))
        paid = initial.get(name)
        if isinstance(paid, dict):
            paid = paid.get('initial_charged_input_kWh')
        row = dict(identity, storage=name, storage_unit='kWh_e' if name == 'ups' else 'kWh_c' if name == 'cold_store' else 'kWh',
            paid_initial_charged_input_kWh=_number(paid),
            initial_inventory_cost_basis=economics.get('initial_inventory_method'))
        for prefix, source, names in (
                ('first_', _mapping(saved.get('first_receipt')), STORAGE_FIELDS),
                ('last_', _mapping(saved.get('last_receipt')), STORAGE_FIELDS),
                ('total_', _mapping(saved.get('totals')), STORAGE_TOTAL_FIELDS)):
            for field in names:
                value = _number(source.get(field))
                row[prefix+field] = value if full else None
                row['observed_'+prefix+field] = value
        storage.append(row)
    return run, service, storage


def _unit(name, carrier=None):
    if 'minute' in name:
        return 'min'
    if 'count' in name:
        return 'count'
    if 'USD' in name:
        return 'USD'
    if 'CO2' in name:
        return 'kg_CO2_conditional'
    if 'MMBtu' in name:
        return 'MMBtu'
    if name.endswith('_lb'):
        return 'lb'
    return 'kWh_'+carrier if carrier else 'kWh'


PAIR_FIELDS = ('pair_id', 'before_result_path', 'after_result_path', 'before_status', 'after_status',
    'before_scope', 'after_scope', 'table', 'priority', 'carrier', 'storage', 'metric', 'unit',
    'before_value', 'after_value', 'after_minus_before')


def _pair_rows(pairs, tables):
    output = []
    for index, (before_path, after_path) in enumerate(pairs, 1):
        before, after = tables[_key(before_path)], tables[_key(after_path)]
        a, b = before[0], after[0]
        base = dict(pair_id=f'pair_{index:04d}', before_result_path=str(before_path), after_result_path=str(after_path),
            before_status=a['status'], after_status=b['status'], before_scope=a['metrics_scope'], after_scope=b['metrics_scope'])
        def add(table, metric, x, y, *, priority=None, carrier=None, storage=None):
            x, y = _number(x), _number(y)
            output.append(dict(base, table=table, priority=priority, carrier=carrier, storage=storage,
                metric=metric, unit=_unit(metric, carrier), before_value=x, after_value=y,
                after_minus_before=y-x if x is not None and y is not None else None))
        for name in (*ECONOMIC_FIELDS, *('actual_energy_'+n for n in ENERGY_FIELDS),
                     *('critical_union_'+n for n in TIME_FIELDS)):
            add('RUNS', name, a.get(name), b.get(name))
        for left, right in zip(before[1], after[1]):
            for name in (*SERVICE_FIELDS, *('priority_union_'+n for n in TIME_FIELDS)):
                add('SERVICE', name, left.get(name), right.get(name),
                    priority=left['priority'], carrier=left['carrier'])
        first = {row['storage']: row for row in before[2]}
        last = {row['storage']: row for row in after[2]}
        for store in dict.fromkeys((*first, *last)):
            left, right = first.get(store, {}), last.get(store, {})
            for name in ('paid_initial_charged_input_kWh', *('first_'+n for n in STORAGE_FIELDS),
                         *('last_'+n for n in STORAGE_FIELDS), *('total_'+n for n in STORAGE_TOTAL_FIELDS)):
                add('STORAGE', name, left.get(name), right.get(name), storage=store,
                    carrier='e' if store == 'ups' else 'c' if store == 'cold_store' else None)
    return output


def _write_csv(path, rows, minimum_fields):
    fields = list(dict.fromkeys((*minimum_fields, *(key for row in rows for key in row))))
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def export_runs(runs, output, *, pairs=(), command=None):
    """Write four compact tables and one receipt from explicit inputs only.

    Each path is one directory or one RESULT.json. Pair paths must also occur
    in ``runs``. Actual episode counts use the recorded integer field; a missing
    field contributes an unknown count, never an inferred zero or one.
    """
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Output must be a fresh directory: '+str(output))
    paths = [_result_path(path) for path in runs]
    if not paths:
        raise ValueError('At least one explicit run path is required')
    if len({_key(path) for path in paths}) != len(paths):
        raise ValueError('Duplicate explicit RESULT inputs would double-count attempts')
    registered = {_key(path) for path in paths}
    explicit_pairs = [tuple(_result_path(path) for path in pair) for pair in pairs]
    if any(len(pair) != 2 or any(_key(path) not in registered for path in pair) for pair in explicit_pairs):
        raise ValueError('Each explicit before/after pair must name two registered runs')
    items = [_load(path) for path in paths]
    tables = {_key(path): _tables(item) for path, item in zip(paths, items)}
    run_rows = [value[0] for value in tables.values()]
    service_rows = [row for value in tables.values() for row in value[1]]
    storage_rows = [row for value in tables.values() for row in value[2]]
    differences = _pair_rows(explicit_pairs, tables)
    counts = [row['actual_episode_attempts'] for row in run_rows]
    unknown_counts = sum(value is None for value in counts)
    known_sum = sum(value for value in counts if value is not None)
    receipt = dict(contract_id=CONTRACT_ID, command=command, inputs=[item['input'] for item in items],
        output_directory=str(output), explicit_pair_count=len(explicit_pairs),
        semantics='Blank is unknown; observed_ columns retain recorded prefixes; full columns require known complete support; after-minus-before pairs never select a winner',
        read_only_inputs=True, raw_trajectories_opened=0, recursive_search=False,
        counts=dict(input_records=len(items), status_counts=dict(Counter(row['status'] for row in run_rows)),
            full_episode_records=sum(row['full_episode_valid'] for row in run_rows),
            failed_or_unknown_records=sum(not row['full_episode_valid'] for row in run_rows),
            actual_episode_attempts_known_sum=known_sum,
            actual_episode_attempts_unknown_records=unknown_counts,
            actual_episode_attempts_total=known_sum if unknown_counts == 0 else None,
            failed_before_episode_attempt_records=sum(not row['full_episode_valid'] and row['actual_episode_attempts'] == 0 for row in run_rows),
            failed_after_episode_started_records=sum(not row['full_episode_valid'] and row['actual_episode_attempts'] is not None and row['actual_episode_attempts'] > 0 for row in run_rows),
            historical_attempts_added_known_sum=sum(row['historical_attempts_added'] for row in run_rows if row['historical_attempts_added'] is not None)),
        table_rows=dict(RUNS=len(run_rows), SERVICE=len(service_rows), STORAGE=len(storage_rows), PAIR_DIFF=len(differences)))
    output.mkdir(parents=True, exist_ok=False)
    _write_csv(output/'RUNS.csv', run_rows, ('run_id', 'result_path', 'status', 'metrics_scope'))
    _write_csv(output/'SERVICE.csv', service_rows, ('run_id', 'priority', 'carrier', 'metrics_scope'))
    _write_csv(output/'STORAGE.csv', storage_rows, ('run_id', 'storage', 'metrics_scope'))
    _write_csv(output/'PAIR_DIFF.csv', differences, PAIR_FIELDS)
    with (output/'RECEIPT.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, allow_nan=False, indent=2)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='+', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--pair', action='append', nargs=2, type=Path, default=[], metavar=('BEFORE', 'AFTER'))
    args = parser.parse_args(argv)
    command = [sys.executable, '-m', 'tools.model_closure_v22.analysis', *(sys.argv[1:] if argv is None else argv)]
    receipt = export_runs(args.runs, args.output, pairs=args.pair, command=command)
    print(json.dumps(dict(output_directory=receipt['output_directory'], counts=receipt['counts'],
                          table_rows=receipt['table_rows']), ensure_ascii=False))


if __name__ == '__main__':
    main()
