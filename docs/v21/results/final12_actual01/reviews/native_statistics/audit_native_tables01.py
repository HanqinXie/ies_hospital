"""Authorized final240 native-table arithmetic review. No native builders or raw I/O."""
from __future__ import annotations
import ast
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean

HERE = Path(__file__).resolve().parent
OUT = HERE / 'actual01'
E = Path('E:/hospital_ies_v21_20261023')
D = Path('D:/hospital_ies_v21_20261023')
SOURCE = '4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add'
PINS = {
    E/'final_blind01/analysis01/PARALLEL_ANALYSIS_RECEIPT.json': '994a42eb6b9382e5a2645c470e2af0655a6d06227b2a0d22aa9045fc4c92dc59',
    E/'final_blind01/analysis01/TABLES.json.gz': 'f70141272d4f62a20bd82d51991d36b33aa86cdeee201e3c05a02c7f877facd9',
    E/'final_blind01/analysis01/tables/CASE_METRICS.csv': '3cab466b53b0080f666dce46f003a92fe3ad3c478e353033bc634d604011ac93',
    E/'final_blind01/analysis_guard01/CASE_IDENTITY_GUARD.json': 'c740a8a199ed74bb8077fc01b51b088fb4e0f53d6d04db167014b9ec3dbec950',
    E/'final_blind01/JOBS_ANALYSIS_ONLY.json': 'ea4a3c81687334074669535b92d5b24e3c8f95b7a91812b98703e903f501bf46',
    D/'tools/sci_engineering/analysis.py': '8b2aaee6de03705d03b3f0b2e8c2ff70ddcf46cce05de77559913c33ab5b1ae2',
}
BOUND = {}
ISSUES = []
EPS = 1e-8


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def bind(p, expected=None):
    p = Path(p)
    if '/raw/' in p.as_posix().lower() or 'sidecar' in p.name.lower():
        raise ValueError('No raw/sidecar reads authorized in this arithmetic review')
    actual = sha(p)
    if expected is not None and actual != expected:
        raise ValueError('Known SHA differs: ' + str(p))
    BOUND[str(p)] = dict(path=str(p), sha256=actual, bytes=p.stat().st_size)
    return p


def read(p, expected=None):
    return json.loads(bind(p, expected).read_text('utf-8-sig'))


def digest(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def check(condition, label):
    if not condition:
        ISSUES.append(label)


def number(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError('Invalid canonical numeric scalar')
    return v


def save_json(name, value):
    with (OUT/name).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=True, indent=2, allow_nan=False)
        f.write('\n')


def save_csv(name, rows):
    fields = sorted({k for row in rows for k in row})
    with (OUT/name).open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def classify(x):
    return 'unknown' if x is None else 'improved' if x < -EPS else 'worsened' if x > EPS else 'numeric_tied'


def mean_block(rows, field):
    groups = defaultdict(list)
    for row in rows:
        groups[row['source_block_id']].append(number(row[field]))
    if any(any(v is None for v in values) for values in groups.values()):
        return None
    return fmean(fmean(values) for values in groups.values()) if groups else None


def stratum_rows(rows, kind, value):
    if kind == 'all':
        return rows
    return [r for r in rows if r[kind] == value]


def main():
    started = datetime.now(timezone.utc).isoformat()
    if OUT.exists():
        raise FileExistsError('Preserve old audit prefix')
    OUT.mkdir(parents=True)
    for p, h in PINS.items():
        bind(p, h)
    bind(__file__)
    scope_main = E/'reviews/main02_primary19_stat_independent01/audit_primary19.py'
    scope_q1 = E/'reviews/q1_scientific_independent01/audit_small_tables.py'
    bind(scope_main)
    bind(scope_q1)
    # The same constant-only AST extraction used by the prior Q1 audit.
    names = {'PRIORITIES', 'CARRIERS', 'SERVICE_FIELDS', 'PHASES', 'GAP_METRICS', 'PHASE_METRICS', 'METRICS', 'COMPARE_METRICS'}
    constants = {'__builtins__': {'tuple': tuple}}
    analysis = D/'tools/sci_engineering/analysis.py'
    for node in ast.parse(analysis.read_text('utf-8')).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names:
            constants[node.targets[0].id] = eval(compile(ast.Expression(node.value), str(analysis), 'eval'), constants, constants)
    service = [m for m in constants['METRICS'] if m not in ('energy_cost_USD', 'total_CO2_kg_conditional', 'online_solver_seconds', 'online_solver_max_seconds')]
    material = [m for m in service if 'material_minutes' in m or 'interruption_count' in m or m == 'critical_union_minutes']
    check(len(service) == 176 and len(material) == 65, 'Prior176/65 field counts')
    save_csv('FIELD_SCOPE176_65.csv', [dict(metric=m, in_service176=True, in_material65=m in material, numerical_epsilon=EPS,
        original_scope_script=str(scope_main), original_scope_script_sha256=BOUND[str(scope_main)]['sha256'],
        native_constants_source_sha256=PINS[analysis]) for m in service])
    parent = read(E/'final_blind01/analysis01/PARALLEL_ANALYSIS_RECEIPT.json')
    canonical = E/'final_blind01/analysis01/TABLES.json.gz'
    tables = json.loads(gzip.decompress(canonical.read_bytes()))
    for name, rows in tables.items():
        check(digest(rows) == parent['table_sha256'][name], 'Canonical table digest ' + name)
    exports = parent['export_receipt']
    receipt = read(canonical.parent/'tables/ANALYSIS_RECEIPT.json')
    check(receipt == exports, 'Embedded/native export receipt exact')
    for item in exports['files']:
        p = canonical.parent/'tables'/item['path']
        bind(p, item['sha256'])
        if 'bytes' in item:
            check(p.stat().st_size == item['bytes'], 'Export size ' + item['path'])
    cases, pairs, summary = (tables[k] for k in ('CASE_METRICS', 'PAIRED_DIFFERENCES', 'PAIRED_SUMMARY'))
    jobs = read(E/'final_blind01/JOBS_ANALYSIS_ONLY.json')
    plans = []
    planned = {}
    for directory in jobs['campaign_directories']:
        plan = read(Path(directory)/'PLAN.json')
        plans.append(plan)
        for s in plan['panel']['scenarios']:
            if s['id'] in planned:
                raise ValueError('Repeated planned scene')
            planned[s['id']] = s
    scene_map = defaultdict(dict)
    for row in cases:
        key = (row['scenario_id'], row['policy_id'])
        check(row['policy_id'] not in scene_map[row['scenario_id']], 'Unique CASE identity ' + str(key))
        scene_map[row['scenario_id']][row['policy_id']] = row
    block_quarter = {r['source_block_id']: r['season_quarter'] for r in cases}
    check(len(cases) == 240 and len(scene_map) == len(planned) == 48 and len(block_quarter) == 12, '240/48/12 identity')
    check(Counter(block_quarter.values()) == Counter({1: 4, 2: 3, 3: 2, 4: 3}), 'Quarter4/3/2/3')
    check(set(scene_map) == set(planned), 'Exact registered scene membership')
    policies = {a+'_sci_v21' for a in 'RMFCA'}
    check(all(set(rows) == policies and len(rows) == 5 for rows in scene_map.values()), 'Five policies every scene')
    check(all(r['phase'] == 'final_blind' and r['configuration_id'] == 'H0' and r['window_minutes'] == 720.
              and r['eligible'] is True and r['evidence_complete'] is True and r['status'] == 'completed'
              and r['source_binding_sha256'] == SOURCE for r in cases), 'Completed eligible known H0 final_blind720')
    check(all(r['source_block_id'] == planned[r['scenario_id']]['source_block_id'] and
              r['canonical_class'] == planned[r['scenario_id']]['canonical_class'] and
              r['season_quarter'] == planned[r['scenario_id']]['season_quarter'] for r in cases), 'Registered block/class/quarter join')
    check(Counter(s['canonical_class'] for s in planned.values()) == Counter({c: 12 for c in ('L1','L2','L3','L4')}), '12 scenes per L')
    check(len(tables['CARRIER_SERVICE']) == 2880 and len(tables['RESOURCE_MEASUREMENTS']) == len(tables['ATTEMPT_STATUS']) == 240, 'Long-table denominators')
    carriers_seen = set()
    carrier_residuals = []
    for r in tables['CARRIER_SERVICE']:
        key = tuple(r[k] for k in ('scenario_id','policy_id','priority','carrier'))
        check(key not in carriers_seen, 'Unique carrier cell')
        carriers_seen.add(key)
        case = scene_map[r['scenario_id']][r['policy_id']]
        check(all(r[f] == case[f'{r["priority"]}_{r["carrier"]}_{f}'] for f in constants['SERVICE_FIELDS']), 'Carrier/CASE exact projection')
        carrier_residuals.append(dict(request_balance=r['requested_kWh']-r['delivered_kWh']-r['ENS_kWh'],
                                      ENS_partition=r['ENS_kWh']-r['disconnected_ENS_kWh']-r['connected_unserved_ENS_kWh']))
    # All numeric differences are checked against the current canonical CASE,
    # without invoking the native analysis builder or any bootstrap.
    pair_groups = defaultdict(list)
    seen = set()
    for r in pairs:
        key = (r['scenario_id'], r['candidate_policy_id'], r['metric'])
        check(key not in seen, 'Unique native pair')
        seen.add(key)
        a = scene_map[r['scenario_id']][r['candidate_policy_id']][r['metric']]
        c = scene_map[r['scenario_id']]['C_sci_v21'][r['metric']]
        expected = None if a is None or c is None else a-c
        check(r['candidate_value'] == a and r['reference_value'] == c and r['delta'] == expected, 'Native pair/CASE exact delta')
        check(r['paired'] is (expected is not None) and r['comparison_status'] == 'paired_complete', 'Known native pair status')
        for typ, v in (('all','all'),('canonical_class',r['canonical_class']),('season_quarter',r['season_quarter'])):
            pair_groups[(r['candidate_policy_id'],r['metric'],typ,v)].append(r)
    check(len(pairs) == 48*4*len(constants['COMPARE_METRICS']) == 29952, 'Full paired denominator')
    mean_differences = []
    for r in summary:
        rows = pair_groups[(r['candidate_policy_id'],r['metric'],r['stratum_type'],r['stratum'])]
        known = [x for x in rows if x['delta'] is not None]
        n = len({x['source_block_id'] for x in known})
        counts = Counter(classify(x['delta']) for x in rows)
        check(r['registered_comparisons'] == len(rows) and r['paired_scenarios'] == len(known)
              and r['unknown_comparisons'] == len(rows)-len(known) and r['source_blocks'] == r['source_block_count'] == n, 'Native summary denominator')
        check(r['improved'] == counts['improved'] and r['tied'] == counts['numeric_tied'] and r['worsened'] == counts['worsened'], 'Native summary sign counts')
        check(r['numerical_zero_threshold'] == EPS and r['bootstrap_repetitions'] == 2000 and r['bootstrap_seed'] == 20261023
              and r['service_failure_threshold_unchanged'] is True and r['phase'] == 'final_blind' and r['window_minutes'] == 720.
              and r['reference_policy_id'] == 'C_sci_v21' and r['configuration_id'] == 'H0', 'Native inference protocol')
        check(r['status'] == 'cluster_bootstrap' and n >= 2 and r['lower_95'] is not None and r['upper_95'] is not None
              and r['lower_95'] <= r['upper_95'], 'Native interval present')
        avg = mean_block(known, 'delta')
        mean_differences.append(abs(avg-r['mean_block_delta']) if avg is not None else None)
    check(len(summary) == 4*len(constants['COMPARE_METRICS'])*9 == 5616, 'All/class/quarter summary inventory')
    selected = list(dict.fromkeys(material + [f'{p}_{c}_ENS_kWh' for p in ('P0','P1','P2','P3') for c in ('e','h','c')]
        + list(constants['GAP_METRICS']) + ['energy_cost_USD','total_CO2_kg_conditional']
        + [f'phase_recovery_{p}_{c}_ENS_kWh' for p in ('P0','P1') for c in ('e','h','c')]))
    ac = []
    ac_nonzero = []
    for metric in service:
        values = []
        for sid, rows in scene_map.items():
            a, c = rows['A_sci_v21'][metric], rows['C_sci_v21'][metric]
            delta = None if a is None or c is None else a-c
            values.append(delta)
            if delta is not None and delta != 0:
                ac_nonzero.append(dict(scenario_id=sid,canonical_class=rows['A_sci_v21']['canonical_class'],
                    source_block_id=rows['A_sci_v21']['source_block_id'],metric=metric,in_material65=metric in material,
                    candidate_value=a,reference_value=c,delta=delta,numerical_class=classify(delta)))
        known = [x for x in values if x is not None]
        counts = Counter(classify(x) for x in values)
        ac.append(dict(metric=metric,in_material65=metric in material,known_pairs=len(known),unknown_pairs=len(values)-len(known),
            exact_equal_pairs=sum(x == 0 for x in known),numeric_improved=counts['improved'],numeric_tied=counts['numeric_tied'],numeric_worsened=counts['worsened'],
            minimum_raw_delta=min(known) if known else None,maximum_raw_delta=max(known) if known else None,
            maximum_absolute_raw_delta=max(map(abs,known)) if known else None))
    selected_summaries = []
    for r in summary:
        if r['metric'] in selected and r['stratum_type'] in ('all','canonical_class'):
            rows = pair_groups[(r['candidate_policy_id'],r['metric'],r['stratum_type'],r['stratum'])]
            values = [x['delta'] for x in rows if x['delta'] is not None]
            selected_summaries.append(dict(r,raw_minimum_delta=min(values) if values else None,
                raw_maximum_delta=max(values) if values else None,exact_equal_pairs=sum(v == 0 for v in values),
                native_interval_recomputed=False))
    nonzero = [r for r in pairs if r['metric'] in selected and r['delta'] is not None and r['delta'] != 0]
    absolute = []
    for cl in ('L1','L2','L3','L4'):
        for policy in sorted(policies):
            rows = [r for r in cases if r['canonical_class'] == cl and r['policy_id'] == policy]
            for metric in selected:
                vals = [number(r[metric]) for r in rows]
                known = [v for v in vals if v is not None]
                absolute.append(dict(canonical_class=cl,policy_id=policy,metric=metric,registered_scenarios=len(rows),
                    source_blocks=len({r['source_block_id'] for r in rows}),known_cases=len(known),unknown_cases=len(rows)-len(known),
                    equal_source_block_mean=mean_block(rows,metric),minimum_case_value=min(known) if known else None,
                    maximum_case_value=max(known) if known else None,CI_computed=False))
    critical_material = [m for m in material if m.startswith(('P0_','P1_')) or m == 'critical_union_minutes'] + list(constants['GAP_METRICS'])
    scenario_rows = []
    for sid, rows in scene_map.items():
        c = rows['C_sci_v21']
        for alias in 'ARMF':
            a = rows[alias+'_sci_v21']
            deltas = {m: None if a[m] is None or c[m] is None else a[m]-c[m] for m in critical_material}
            out = dict(scenario_id=sid,canonical_class=a['canonical_class'],season_quarter=a['season_quarter'],source_block_id=a['source_block_id'],
                candidate_policy_id=a['policy_id'],reference_policy_id=c['policy_id'],fault_target=a['fault_target'],
                critical_material_worse=any(v is not None and v > EPS for v in deltas.values()),
                critical_material_improved=any(v is not None and v < -EPS for v in deltas.values()),
                reference_CASE_result_path=c['result_path'],reference_CASE_result_sha256=c['result_sha256'],
                candidate_CASE_result_path=a['result_path'],candidate_CASE_result_sha256=a['result_sha256'])
            for metric in ['critical_union_minutes',*constants['GAP_METRICS'],'P0_union_longest_material_minutes','P1_union_longest_material_minutes',
                           'P0_union_interruption_count','P1_union_interruption_count','P2_union_material_minutes','P3_union_material_minutes',
                           'energy_cost_USD','total_CO2_kg_conditional']:
                out[metric+'_C'] = c[metric]
                out[metric+'_candidate'] = a[metric]
                out[metric+'_delta'] = None if a[metric] is None or c[metric] is None else a[metric]-c[metric]
            scenario_rows.append(out)
    save_csv('A_MINUS_C_SERVICE176.csv', ac)
    save_csv('A_MINUS_C_NONZERO_SERVICE_DELTAS.csv', ac_nonzero)
    save_csv('NATIVE_SELECTED_PAIRED_SUMMARY.csv', selected_summaries)
    save_csv('SELECTED_NONZERO_CASE_PAIRS.csv', nonzero)
    save_csv('ABSOLUTE_CLASS_POLICY_SELECTED.csv', absolute)
    save_csv('SCENARIO_COMPARISON_INDEX192.csv', scenario_rows)
    source_after = []
    for p, before in BOUND.items():
        after = dict(path=p,sha256=sha(p),bytes=Path(p).stat().st_size)
        source_after.append(dict(before=before,after=after,exact=before == after))
    check(all(r['exact'] for r in source_after), 'All source bytes unchanged')
    save_json('SOURCE_CLOSURE.json', dict(files=source_after,raw_or_sidecar_read=False))
    review = dict(schema='hospital_ies.final12_native_independent_arithmetic_review.v1',started_utc=started,
        finished_utc=datetime.now(timezone.utc).isoformat(),scientific_acceptance=False,NOT_GATE=True,NOT_NEW_INDEPENDENT_POOL=True,
        source_binding_sha256=SOURCE,case_count=len(cases),scene_count=len(scene_map),source_block_count=len(block_quarter),
        quarter_blocks=dict(Counter(block_quarter.values())),cases_by_policy=dict(Counter(r['policy_alias'] for r in cases)),
        scenes_by_class=dict(Counter(s['canonical_class'] for s in planned.values())),
        eligibility=dict(Counter(str(r['eligible']) for r in cases)),evidence_complete=dict(Counter(str(r['evidence_complete']) for r in cases)),
        case_status=dict(Counter(r['status'] for r in cases)),case_evidence_status=dict(Counter(r['evidence_status'] for r in cases)),
        native_pair_rows_checked=len(pairs),native_summary_rows_checked=len(summary),native_summary_interval_method='Existing2000 source-block bootstrap/seed20261023; no endpoints recomputed',
        source_block_mean_float_roundoff_max=max(v for v in mean_differences if v is not None),
        service176_scope=list(service),material65_scope=list(material),selected_metrics=list(selected),
        A_C_all176_numeric_tied=all(r['numeric_tied'] == 48 and not r['unknown_pairs'] for r in ac),
        A_C_all176_exact=all(r['exact_equal_pairs'] == 48 for r in ac),
        A_C_material65_exact=all(r['exact_equal_pairs'] == 48 for r in ac if r['in_material65']),
        A_C_max_absolute_service_delta=max(r['maximum_absolute_raw_delta'] for r in ac),
        A_C_nonzero_service_cells=len(ac_nonzero),
        carrier_request_balance_max_absolute=max(abs(r['request_balance']) for r in carrier_residuals),
        carrier_ENS_partition_max_absolute=max(abs(r['ENS_partition']) for r in carrier_residuals),
        critical_material_worse_cases=[r for r in scenario_rows if r['critical_material_worse']],
        source_files_before_after=len(source_after),issues=ISSUES,
        limitations=['No production analysis/cohort/simulator/optimizer/LP/bootstrap/pytest/MATLAB call.',
                    'No raw or solver-sidecar file opened; prior native evidence/physical validation is not repeated.',
                    'Numerical tie1e-8 is distinct from original physical material predicate; no ENS zeroing.',
                    'All comparisons are fixed12 background descriptive/clustered results, not clinical probabilities or cross-hospital effects.',
                    'Costs use original2001 conditional USD; CO2 remains post-run conditional accounting.',
                    'Deterministic block mean roundoff is recorded, not used to alter native CI or any tolerance.'])
    save_json('NATIVE_STATISTICAL_REVIEW.json',review)
    print(json.dumps({k:review[k] for k in ('case_count','scene_count','source_block_count','quarter_blocks','A_C_all176_numeric_tied','A_C_all176_exact',
        'A_C_material65_exact','A_C_max_absolute_service_delta','A_C_nonzero_service_cells','native_pair_rows_checked','native_summary_rows_checked',
        'source_block_mean_float_roundoff_max','source_files_before_after','issues','NOT_GATE')},allow_nan=False))
    return 0 if not ISSUES else 2


if __name__ == '__main__':
    raise SystemExit(main())
