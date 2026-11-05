"""Read explicit V23 runs into compact numeric review tables; no simulator use.

Usage: python -m tools.model_correctness_v23.review --runs RUN1 RUN2 --output NEW
RAW and PROCESS are hashed as evidence but never replayed or re-integrated.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
import sys

from .comparison import compare
from .metrics import CARRIERS, PRIORITIES, build_reliability_metrics

CONTRACT_ID = 'hospital_ies.explicit_run_review.v23.v1'
PREREGISTRATION = Path(__file__).resolve().parents[2]/'configs/model_correctness_v23/COMPARISON_PREREGISTRATION.json'
STORES = ('ups', 'cold_store', 'DG_shaft_budget')
SERVICE_FIELDS = ('requested_kWh', 'delivered_kWh', 'ENS_kWh', 'disconnected_ENS_kWh',
                  'connected_unserved_ENS_kWh', 'material_minutes', 'longest_material_minutes',
                  'interruption_count', 'repeat_interruption_count')
PAIR_FIELDS = ('pair_id', 'left_run', 'right_run', 'left_policy', 'right_policy',
    'scenario_sha256', 'physical_config_sha256', 'formal_comparison', 'eligibility_reason',
    'choice', 'decisive_tier', 'comparison_reason', 'dimension_group', 'metric', 'unit',
    'left_value', 'right_value', 'left_minus_right', 'band', 'engineering_direction')


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _sha(path):
    if not path.is_file():
        return None
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def _read(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('JSON root must be an object')
        return value, _sha(path), None
    except (OSError, ValueError) as exc:
        return {}, _sha(path), type(exc).__name__+': '+str(exc)


def _run_path(value):
    path = Path(value).resolve()
    if path.name.lower() == 'result.json':
        return path.parent
    if path.suffix.lower() == '.json':
        raise ValueError('Supply a run directory or RESULT.json explicitly')
    return path


def _artifact(run, recorded_path, expected_sha, fallback):
    path = Path(recorded_path) if isinstance(recorded_path, str) else run/fallback
    if not path.is_absolute():
        path = run/path
    actual = _sha(path)
    status = 'missing' if actual is None else 'missing_binding' if not expected_sha else 'matched' if actual == expected_sha else 'mismatch'
    return dict(path=str(path), expected_sha256=expected_sha, actual_sha256=actual, status=status)


def _support(record, metrics):
    values = []
    consistent = True
    for key in ('requested_end_minute', 'observed_until_minute', 'unknown_tail_minutes'):
        top, inner = _number(record.get(key)), _number(metrics.get(key))
        if top is not None and inner is not None and abs(top-inner) > 1e-7:
            consistent = False
        values.append(top if top is not None else inner)
    requested, observed, unknown = values
    full = (record.get('status') == 'completed' and metrics.get('complete') is True and consistent
        and all(v is not None and v >= 0. for v in values)
        and abs(requested-observed) <= 1e-7 and unknown <= 1e-7)
    return requested, observed, unknown, bool(full)


def _load(run, preregistration):
    record, result_sha, error = _read(run/'RESULT.json')
    resolved, resolved_sha, resolved_error = _read(run/'RESOLVED_CASE.json')
    case_file, case_sha, case_error = _read(run/'CASE.json')
    case = _mapping(resolved.get('case')) or _mapping(case_file.get('case'))
    scenario = _mapping(resolved.get('scenario')) or _mapping(case.get('scenario'))
    physical = (_mapping(record.get('resolved_model_config')) or _mapping(resolved.get('model_config'))
        or _mapping(record.get('model_config')))
    scenario_sha = _digest(scenario) if scenario else None
    config_sha = _digest(physical) if physical else None
    metrics = _mapping(record.get('metrics'))
    reliability = _mapping(record.get('reliability_v23')) or _mapping(metrics.get('reliability_v23'))
    if not reliability:
        reliability = build_reliability_metrics({})
    comparison = _mapping(record.get('comparison_inputs')) or _mapping(metrics.get('comparison_inputs'))
    policy = _mapping(record.get('policy')) or _mapping(case.get('policy'))
    policy_key = _digest({key: policy.get(key) for key in ('alias', 'policy_id', 'config')}) if policy else None
    requested, observed, unknown, full = _support(record, metrics)
    raw = _artifact(run, record.get('raw_path'), record.get('raw_sha256'), 'trajectory.json.gz')
    process_binding = _mapping(record.get('process'))
    process = _artifact(run, process_binding.get('path'), process_binding.get('sha256'), 'PROCESS.csv')
    reasons = []
    if not full:
        reasons.append('not_complete')
    if record.get('source_and_inputs_unchanged') is not True or record.get('source_drift_error') or record.get('source_check_error'):
        reasons.append('source_or_input_unchanged_not_verified')
    for key in ('source_sha256', 'input_sha256', 'model_id'):
        if not record.get(key):
            reasons.append('missing_'+key)
    scenario_expected = record.get('scenario_sha256') or _mapping(record.get('case_binding')).get('scenario_after_sha256')
    if scenario_sha is None or scenario_expected != scenario_sha:
        reasons.append('scenario_binding_missing_or_mismatch')
    config_expected = record.get('resolved_model_config_sha256') or record.get('model_config_sha256')
    if config_sha is None or config_expected != config_sha:
        reasons.append('physical_config_binding_missing_or_mismatch')
    for label, binding in (('RAW', raw), ('PROCESS', process)):
        if binding['status'] != 'matched':
            reasons.append(label+'_'+binding['status'])
    confirmation = _mapping(reliability.get('definition')).get('confirmation_seconds')
    if confirmation != _mapping(preregistration.get('recovery')).get('confirmation_seconds'):
        reasons.append('recovery_definition_not_preregistered_main_definition')
    if comparison and (comparison.get('reliability_v23') != reliability or comparison.get('economics') != metrics.get('economics')):
        reasons.append('comparison_snapshot_mismatch')
    if error:
        reasons.append('unreadable_RESULT')
    status = record.get('status') or ('missing_result' if not (run/'RESULT.json').is_file() else 'unreadable_result')
    identity = dict(run_id=run.name, result_path=str(run/'RESULT.json'), status=status,
        case_id=record.get('case_id', case.get('id')), policy_alias=policy.get('alias'), policy_id=policy.get('policy_id'),
        configuration_id=record.get('configuration', case.get('configuration', resolved.get('configuration'))),
        model_id=record.get('model_id'), source_sha256=record.get('source_sha256'),
        input_binding_sha256=_digest(record['input_sha256']) if record.get('input_sha256') else None,
        scenario_sha256=scenario_sha, physical_config_sha256=config_sha,
        source_and_inputs_unchanged=record.get('source_and_inputs_unchanged'),
        formal_pair_eligible=not reasons, formal_pair_ineligibility=';'.join(reasons) or None,
        requested_end_minute=requested, observed_until_minute=observed, unknown_tail_minutes=unknown,
        full_episode_valid=full, metrics_scope='full_episode' if full else 'observed_prefix' if metrics and observed is not None else 'missing_metrics',
        actual_episode_attempts=record.get('actual_episode_attempts'),
        error_type=_mapping(record.get('error')).get('type'), error_message=_mapping(record.get('error')).get('message'),
        source_drift_error=record.get('source_drift_error'), RESULT_read_error=error,
        RAW_binding_status=raw['status'], PROCESS_binding_status=process['status'])
    return dict(run=run, record=record, metrics=metrics, reliability=reliability, comparison=comparison,
        identity=identity, full=full, policy_key=policy_key, reasons=reasons,
        extra_binding={key: _mapping(value).get('sha256') for key, value in _mapping(record.get('extra_input_bindings')).items()},
        evidence=dict(run_id=run.name, RESULT_sha256=result_sha, CASE_sha256=case_sha,
            RESOLVED_CASE_sha256=resolved_sha, read_errors=dict(RESULT=error, CASE=case_error, RESOLVED_CASE=resolved_error),
            RAW=raw, PROCESS=process))


def _quantities(row, values, fields, full, prefix=''):
    for field in fields:
        value = _number(values.get(field))
        row[prefix+field] = value if full else None
        row['observed_'+prefix+field] = value


def _sum_known(values):
    return sum(values) if all(_number(value) is not None for value in values) else None


def _tables(item):
    identity, record, metrics, rel, full = [item[key] for key in ('identity', 'record', 'metrics', 'reliability', 'full')]
    run = dict(identity)
    economy = _mapping(metrics.get('economics'))
    emissions = _mapping(economy.get('emissions_conditional'))
    _quantities(run, dict(economy, total_CO2_kg_conditional=emissions.get('total_CO2_kg_conditional')),
        ('energy_cost_USD', 'total_CO2_kg_conditional'), full)
    for key in ('price_year', 'price_currency'):
        run[key] = economy.get(key)
    run['emissions_factor_sha256'] = emissions.get('emissions_factor_sha256')
    run['CO2_boundary'] = emissions.get('CO2_boundary')
    service = _mapping(metrics.get('carrier_service'))
    for p in PRIORITIES:
        for k in CARRIERS:
            cell = _mapping(_mapping(service.get(p)).get(k))
            _quantities(run, cell, SERVICE_FIELDS, full, p+'_'+k+'_')
            new = _mapping(_mapping(_mapping(rel.get('carrier_service')).get(p)).get(k))
            _quantities(run, new, ('engineering_interruption_count', 'engineering_minutes', 'longest_engineering_minutes'), full, p+'_'+k+'_')
    for k in CARRIERS:
        for field in ('requested_kWh', 'delivered_kWh', 'ENS_kWh'):
            values = [_mapping(_mapping(service.get(p)).get(k)).get(field) for p in PRIORITIES[2:]]
            value = _sum_known(values)
            run['general_'+k+'_'+field] = value if full else None
            run['observed_general_'+k+'_'+field] = value
    stores = _mapping(record.get('inventories')) or _mapping(item['comparison'].get('comparison_inventory'))
    for name in dict.fromkeys((*STORES, *stores)):
        _quantities(run, _mapping(stores.get(name)), ('initial_kWh', 'scale_kWh', 'final_kWh'), full, name+'_')
    critical = _mapping(rel.get('critical_union'))
    for key, value in critical.items():
        if isinstance(value, (list, dict)):
            continue
        numeric = _number(value)
        run['critical_union_'+key] = value if full or numeric is None else None
        run['observed_critical_union_'+key] = value
    recoveries = []
    targets = [('carrier_service', p, k, _mapping(_mapping(_mapping(rel.get('carrier_service')).get(p)).get(k))) for p in PRIORITIES for k in CARRIERS]
    targets += [('priority_union', p, None, _mapping(_mapping(rel.get('priority_union')).get(p))) for p in PRIORITIES]
    targets += [('critical_union', None, None, critical)]
    for scope, priority, carrier, values in targets:
        recoveries.append(dict(identity, scope=scope, priority=priority, carrier=carrier,
            recovery_contract_id=rel.get('contract_id'),
            **{key: value for key, value in values.items() if not isinstance(value, (list, dict))}))
    return run, recoveries


def _unit(metric):
    return 'min' if 'minute' in metric else 'count' if 'count' in metric else 'USD2001' if 'USD' in metric else 'kg_CO2_conditional' if 'CO2' in metric else 'kWh'


def _pairwise(items):
    rows, count = [], 0
    for left, right in combinations(items, 2):
        a, b = left['identity'], right['identity']
        if (not a['scenario_sha256'] or not a['physical_config_sha256'] or
                a['scenario_sha256'] != b['scenario_sha256'] or a['physical_config_sha256'] != b['physical_config_sha256']
                or not left['policy_key'] or not right['policy_key'] or left['policy_key'] == right['policy_key']):
            continue
        count += 1
        reasons = list(dict.fromkeys(left['reasons']+right['reasons']))
        for key in ('model_id', 'source_sha256', 'input_binding_sha256'):
            if a[key] != b[key] or not a[key]:
                reasons.append('different_or_missing_'+key)
        if left['extra_binding'] != right['extra_binding']:
            reasons.append('different_extra_input_bindings')
        if not left['comparison'] or not right['comparison']:
            reasons.append('missing_comparison_snapshot')
        result = (dict(choice='unknown', decisive_tier=None, reason='Ineligible evidence: '+';'.join(reasons))
            if reasons else compare(left['comparison'], right['comparison']))
        formal = not reasons and result['choice'] != 'unknown'
        base = dict(pair_id=f'pair_{count:04d}', left_run=a['run_id'], right_run=b['run_id'],
            left_policy=a['policy_alias'] or a['policy_id'], right_policy=b['policy_alias'] or b['policy_id'],
            scenario_sha256=a['scenario_sha256'], physical_config_sha256=a['physical_config_sha256'],
            formal_comparison=formal, eligibility_reason=';'.join(reasons) or 'eligible',
            choice=result['choice'], decisive_tier=result.get('decisive_tier'), comparison_reason=result.get('reason'))
        dimensions = [(group, name, value) for group in ('critical_dimensions', 'general_dimensions', 'economics_dimensions', 'inventory_dimensions')
            for name, value in result.get(group, {}).items()]
        if not dimensions:
            rows.append(dict(base, dimension_group=None, metric=None, unit=None, left_value=None, right_value=None,
                left_minus_right=None, band=None, engineering_direction=None))
        for group, name, value in dimensions:
            rows.append(dict(base, dimension_group=group, metric=name, unit=_unit(name),
                left_value=value['left'], right_value=value['right'], left_minus_right=value['left_minus_right'],
                band=value['band'], engineering_direction=value['engineering_direction']))
    return rows, count


def _write(path, rows, minimum_fields):
    fields = list(dict.fromkeys((*minimum_fields, *(key for row in rows for key in row))))
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def review_runs(runs, output, *, preregistration=PREREGISTRATION, command=None):
    """No recursive discovery, source rebinding, metric recomputation or ranking of drifted runs."""
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Review output must be a fresh directory: '+str(output))
    paths = [_run_path(value) for value in runs]
    if not paths:
        raise ValueError('At least one explicit run is required')
    if len({str(path).casefold() for path in paths}) != len(paths):
        raise ValueError('Duplicate explicit run inputs')
    preregistration = Path(preregistration).resolve()
    protocol, protocol_sha, protocol_error = _read(preregistration)
    if protocol_error:
        raise ValueError('Preregistered comparison definition unavailable: '+protocol_error)
    items = [_load(path, protocol) for path in paths]
    tables = [_tables(item) for item in items]
    run_rows = [table[0] for table in tables]
    recovery_rows = [row for table in tables for row in table[1]]
    pair_rows, pair_count = _pairwise(items)
    counts = [row.get('actual_episode_attempts') for row in run_rows]
    known = [value for value in counts if isinstance(value, int) and not isinstance(value, bool) and value >= 0]
    unknown = len(counts)-len(known)
    receipt = dict(contract_id=CONTRACT_ID, command=command, output_directory=str(output),
        preregistration=dict(path=str(preregistration), sha256=protocol_sha), inputs=[item['evidence'] for item in items],
        read_only_inputs=True, recursive_search=False, raw_trajectories_replayed=0, solver_calls=0, environment_runs=0,
        semantics='All raw numeric values retained; blank unknown; observed_ columns preserve failed prefixes; no carrier, fee or inventory compensation',
        pair_scope='identical full scenario and effective physical configuration, distinct policies; source/input/evidence drift excluded from formal comparison',
        counts=dict(input_records=len(items), status_counts=dict(Counter(row['status'] for row in run_rows)),
            complete_records=sum(row['full_episode_valid'] for row in run_rows),
            formal_pair_eligible_records=sum(row['formal_pair_eligible'] for row in run_rows),
            candidate_policy_pairs=pair_count,
            formal_policy_pairs=len({row['pair_id'] for row in pair_rows if row['formal_comparison']}),
            actual_episode_attempts_known_sum=sum(known), actual_episode_attempts_unknown_records=unknown,
            actual_episode_attempts_total=sum(known) if not unknown else None),
        table_rows=dict(RUNS=len(run_rows), RECOVERY=len(recovery_rows), PAIRWISE=len(pair_rows)))
    output.mkdir(parents=True, exist_ok=False)
    _write(output/'RUNS.csv', run_rows, ('run_id', 'status', 'metrics_scope'))
    _write(output/'RECOVERY.csv', recovery_rows, ('run_id', 'scope', 'priority', 'carrier', 'recovery_status'))
    _write(output/'PAIRWISE.csv', pair_rows, PAIR_FIELDS)
    (output/'COMPARISON_PREREGISTRATION.json').write_bytes(preregistration.read_bytes())
    (output/'RECEIPT.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='+', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--preregistration', type=Path, default=PREREGISTRATION)
    args = parser.parse_args(argv)
    receipt = review_runs(args.runs, args.output, preregistration=args.preregistration,
        command=[sys.executable, '-m', 'tools.model_correctness_v23.review', *(sys.argv[1:] if argv is None else argv)])
    print(json.dumps(dict(counts=receipt['counts'], table_rows=receipt['table_rows'])))


if __name__ == '__main__':
    main()
