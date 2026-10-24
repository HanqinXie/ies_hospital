"""Independent final240 small-summary resource/event review; no model imports or raw opens."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from statistics import fmean
import csv
import gzip
import hashlib
import io
import json
import math

ROOT = Path('E:/hospital_ies_v21_20261023/final_blind01')
ANALYSIS = ROOT / 'analysis01'
OUT = Path(__file__).resolve().parent / 'actual01'
PARENT_SHA = '994a42eb6b9382e5a2645c470e2af0655a6d06227b2a0d22aa9045fc4c92dc59'
TABLES_SHA = 'f70141272d4f62a20bd82d51991d36b33aa86cdeee201e3c05a02c7f877facd9'
SOURCE = '4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add'
METHOD = Path('E:/hospital_ies_v21_20261023/reviews/q1_scientific_independent01/audit_small_tables.py')
LOGGER = Path('D:/hospital_ies_v21_20261023/tools/p0_audit/run_logged.py')
BOUND = {}
EVENTS = ('protection_trip', 'no_export_interlock', 'minimum_output_interlock', 'gt_floor_local_correction_rejected')
COUNTS = ('strategy_fallback_steps', 'projection_fallback_steps', 'projection_changed_steps',
          'solver_backend_rejected_attempt_count', 'solver_timeout_attempt_count')
TIMINGS = ('resource_online_full_step_max_seconds', 'resource_online_full_step_seconds',
           'resource_online_upper_max_seconds', 'resource_online_upper_seconds',
           'resource_online_solver_max_seconds', 'resource_online_solver_seconds',
           'resource_online_upper_decision_count', 'resource_online_upper_period_exceedance_count',
           'resource_scheduling_period_seconds', 'elapsed_seconds')
KEYS = ('phase', 'scenario_id', 'policy_id', 'configuration_id', 'attempt_id')


def utc(): return datetime.now(timezone.utc).isoformat()


def digest_bytes(value): return hashlib.sha256(value).hexdigest()


def digest(value):
    return digest_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode())


def pairs(values):
    result = {}
    for key, value in values:
        if key in result: raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def strict_json(blob):
    return json.loads(blob, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON ' + value)))


def bind(path, expected=None):
    path = Path(path).resolve()
    blob = path.read_bytes()
    actual = digest_bytes(blob)
    if expected is not None and actual != expected: raise ValueError('Known SHA mismatch: ' + str(path))
    prior = BOUND.get(str(path))
    record = dict(path=str(path), bytes=len(blob), sha256=actual)
    if prior is not None and prior != record: raise ValueError('Changed during read: ' + str(path))
    BOUND[str(path)] = record
    return blob


def write(name, value):
    path = OUT / name
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def write_csv(name, rows):
    fields = sorted({key for row in rows for key in row})
    with (OUT / name).open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def csv_value(value):
    if value is None: return ''
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list, tuple)) else str(value)


def canonical_csv(name, tables, parent):
    reference = next(row for row in parent['export_receipt']['files'] if row['path'] == name + '.csv')
    blob = bind(ANALYSIS / 'tables' / reference['path'], reference['sha256'])
    reader = csv.reader(io.StringIO(blob.decode('utf-8-sig'), newline=''))
    header = next(reader)
    expected_fields = sorted({key for row in tables[name] for key in row})
    if header != expected_fields: raise ValueError('CSV canonical header differs: ' + name)
    rows = list(reader)
    expected = [[csv_value(row.get(key)) for key in header] for row in tables[name]]
    if rows != expected: raise ValueError('CSV canonical values/order differ: ' + name)
    if reference['rows'] != len(rows): raise ValueError('CSV row declaration differs: ' + name)
    return dict(table=name, rows=len(rows), columns=len(header), exact_csv_canonical_match=True)


def key(row): return tuple(row.get(field) for field in KEYS)


def finite(value): return type(value) in (int, float) and math.isfinite(value)


def count(value): return value if type(value) is int and value >= 0 else None


def quantile(values, q):
    if not values: return None
    values = sorted(values)
    place = (len(values)-1)*q
    lo = int(math.floor(place)); hi = int(math.ceil(place))
    return values[lo] + (values[hi]-values[lo])*(place-lo)


def describe(rows, field):
    values = [row[field] for row in rows if finite(row.get(field))]
    return dict(registered_cases=len(rows), known_cases=len(values), unknown_cases=len(rows)-len(values),
                minimum=min(values) if values else None, median=quantile(values, .5),
                p90=quantile(values, .9), maximum=max(values) if values else None,
                mean=fmean(values) if len(values) == len(rows) and values else None,
                known_values_only_sum=sum(values) if values else None)


def main():
    if OUT.exists(): raise FileExistsError('Fresh actual review prefix required')
    OUT.mkdir(parents=True)
    started = utc()
    parent = strict_json(bind(ANALYSIS / 'PARALLEL_ANALYSIS_RECEIPT.json', PARENT_SHA))
    compressed = bind(ANALYSIS / 'TABLES.json.gz', TABLES_SHA)
    tables = strict_json(gzip.decompress(compressed))
    bind(METHOD); bind(LOGGER); bind(Path(__file__))
    if parent['schema'] != 'hospital_ies.external_parallel_analysis.v1' or parent['source_binding_sha256'] != SOURCE:
        raise ValueError('Wrong native parent schema/source')
    if parent['original_record_count'] != 240: raise ValueError('Native parent count differs')
    for name in ('CASE_METRICS', 'RESOURCE_MEASUREMENTS'):
        if digest(tables[name]) != parent['table_sha256'][name] or len(tables[name]) != parent['table_row_counts'][name]:
            raise ValueError('Canonical table parent hash/count mismatch: ' + name)
    csv_checks = [canonical_csv(name, tables, parent) for name in ('CASE_METRICS', 'RESOURCE_MEASUREMENTS')]
    cases = tables['CASE_METRICS']; resources = tables['RESOURCE_MEASUREMENTS']
    if len(cases) != 240 or len(resources) != 240: raise ValueError('Missing final240 CASE/resources')
    resource_by_key = {key(row): row for row in resources}
    if len(resource_by_key) != len(resources) or {key(row) for row in cases} != set(resource_by_key):
        raise ValueError('CASE/resource identity join differs')
    event_rows = []; cross_checks = []; field_inventory = None
    for row in cases:
        if row['source_binding_sha256'] != SOURCE or row['model_id'] != 'M21_sci_engineering' or row['phase'] != 'final_blind':
            raise ValueError('CASE source/model/phase differs')
        path = Path(row['result_path']).resolve()
        if path.name != 'RESULT.json' or not path.is_relative_to((ROOT / 'experiments').resolve()):
            raise ValueError('Only CASE-bound small RESULT files may be read')
        result = strict_json(bind(path, row['result_sha256']))
        for field in ('scenario_id', 'policy_id', 'configuration_id', 'source_block_id', 'scenario_sha256', 'policy_sha256', 'configuration_sha256'):
            if result.get(field) != row.get(field): raise ValueError('CASE/RESULT identity mismatch: ' + field)
        if result.get('source_binding_sha256') != SOURCE: raise ValueError('RESULT source differs')
        metrics = result.get('metrics') if isinstance(result.get('metrics'), dict) else {}
        complete = (result.get('status') == 'completed' and result.get('unknown_tail_minutes') == 0.
                    and result.get('error') is None and result.get('analysis_error') is None)
        counter = metrics.get('event_counts')
        counter_valid = isinstance(counter, dict) and all(type(value) is int and value >= 0 for value in counter.values())
        resource = resource_by_key[key(row)]
        entry = {field: row[field] for field in KEYS}
        entry.update(canonical_class=row['canonical_class'], source_block_id=row['source_block_id'],
                     result_path=str(path), result_sha256=row['result_sha256'], complete_summary=complete,
                     event_counter_known=counter_valid and complete,
                     sparse_counter_missing_kind_means_zero=counter_valid and complete)
        for name in EVENTS:
            entry[name + '_events'] = counter.get(name, 0) if counter_valid and complete else None
        for name in COUNTS:
            entry[name] = count(metrics.get(name)) if complete else None
        for name in TIMINGS:
            entry[name] = resource.get(name) if finite(resource.get(name)) else None
        for name in COUNTS[3:] + tuple(t for t in TIMINGS if t.startswith('resource_online_')):
            metric_name = name.removeprefix('resource_')
            resource_name = name if name.startswith('resource_') else 'resource_' + name
            mv = metrics.get(metric_name); rv = resource.get(resource_name)
            known = finite(mv) and finite(rv)
            cross_checks.append(dict(**{field: row[field] for field in KEYS}, metric=metric_name,
                                     RESULT_metric=mv if finite(mv) else None,
                                     RESOURCE_value=rv if finite(rv) else None,
                                     both_known=known, exact_equal=(mv == rv) if known else None))
            if known and mv != rv: raise ValueError('RESULT/resource scalar mismatch: ' + metric_name)
        if field_inventory is None:
            field_inventory = dict(result_root_fields=sorted(result),
                                   relevant_metric_fields=sorted(k for k in metrics if any(t in k for t in ('fallback', 'projection', 'solver', 'upper', 'step', 'event'))),
                                   resource_fields=sorted(resource))
        event_rows.append(entry)
    summaries = []; timing_summaries = []
    policies = sorted({row['policy_id'] for row in event_rows})
    for policy in ['ALL_POLICIES'] + policies:
        for cls in ['ALL_CLASSES'] + sorted({row['canonical_class'] for row in event_rows}):
            group = [row for row in event_rows if (policy == 'ALL_POLICIES' or row['policy_id'] == policy)
                     and (cls == 'ALL_CLASSES' or row['canonical_class'] == cls)]
            for field in tuple(name + '_events' for name in EVENTS) + COUNTS:
                values = [row[field] for row in group if row[field] is not None]
                summaries.append(dict(policy=policy, canonical_class=cls, metric=field, registered_cases=len(group),
                                      known_cases=len(values), unknown_cases=len(group)-len(values),
                                      recorded_sum=sum(values) if len(values) == len(group) else None,
                                      known_values_only_sum=sum(values) if values else None,
                                      positive_cases=sum(value > 0 for value in values), zero_cases=sum(value == 0 for value in values)))
            for field in TIMINGS:
                timing_summaries.append(dict(policy=policy, canonical_class=cls, metric=field, **describe(group, field)))
    write_csv('CASE_EVENTS_AND_RESOURCES.csv', event_rows)
    write_csv('EVENT_COUNT_SUMMARY.csv', summaries)
    write_csv('TIMING_SUMMARY.csv', timing_summaries)
    write_csv('RESULT_RESOURCE_SCALAR_CROSSCHECK.csv', cross_checks)
    write_csv('PROTECTION_TRIP_POSITIVE_CASES.csv', [row for row in event_rows if (row['protection_trip_events'] or 0) > 0])
    write('FIELD_INVENTORY.json', field_inventory)
    global_events = [row for row in summaries if row['policy'] == 'ALL_POLICIES' and row['canonical_class'] == 'ALL_CLASSES']
    global_timings = [row for row in timing_summaries if row['policy'] == 'ALL_POLICIES' and row['canonical_class'] == 'ALL_CLASSES']
    before = sorted(BOUND.values(), key=lambda row: row['path']); after = []
    for prior in before:
        blob = Path(prior['path']).read_bytes()
        current = dict(path=prior['path'], bytes=len(blob), sha256=digest_bytes(blob))
        after.append(current)
        if current != prior: raise ValueError('Read source changed before closure: ' + prior['path'])
    result = dict(schema='hospital_ies.final240_independent_small_summary_resources.v1',
                  started_utc=started, finished_utc=utc(), native_parent_sha256=PARENT_SHA,
                  canonical_TABLES_sha256=TABLES_SHA, source_binding_sha256=SOURCE,
                  cases=len(cases), small_RESULTs_checked=len(event_rows), complete_summaries=sum(row['complete_summary'] for row in event_rows),
                  status_counts=dict(Counter(row['status'] for row in cases)),
                  canonical_csv_checks=csv_checks, global_event_and_fallback_summary=global_events,
                  global_timing_summary=global_timings,
                  result_resource_crosscheck=dict(rows=len(cross_checks), both_known=sum(row['both_known'] for row in cross_checks),
                                                  unknown=sum(not row['both_known'] for row in cross_checks), mismatches=0),
                  read_sources_before=before, read_sources_after=after, read_source_drift=[],
                  actual_trip_is_not_L2_label=True,
                  method='Q1 audit_small_tables.py event/fallback fields, reproduced by independent stdlib-only reader; original program not executed',
                  interpretation='Recorded modeled protection_trip counters over complete windows; no inference from L2 and no accident-probability or service-failure interpretation',
                  timing_limit='Timing distributions describe per-case recorded maxima/totals, not unrecorded individual-step samples; host measured, no hard-real-time certification',
                  raw_or_sidecar_reads=0, model_imports=0, physics=0, solver_calls=0, cohort_or_native_analysis_runs=0,
                  scientific_gate=False, whole_physical_audit=False, NOT_GATE=True)
    write('RESOURCE_AND_EVENT_REVIEW.json', result)
    print(json.dumps(dict(cases=len(cases), read_sources=len(before), read_source_drift=0,
                          event_totals={row['metric']: row['recorded_sum'] for row in global_events},
                          positive_cases={row['metric']: row['positive_cases'] for row in global_events},
                          timing_ranges={row['metric']: [row['minimum'], row['maximum']] for row in global_timings},
                          result_resource_crosscheck=result['result_resource_crosscheck'], NOT_GATE=True), ensure_ascii=False))


if __name__ == '__main__': main()
