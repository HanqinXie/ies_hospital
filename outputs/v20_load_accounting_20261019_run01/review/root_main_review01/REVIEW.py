"""Independently check the two main-panel narrative inputs against saved tables."""
from pathlib import Path
import csv
import hashlib
import json
import math

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'outputs/v20_load_accounting_20261019_run01'
REPORT = RUN / 'paper_results/MAIN_VERIFIED02'
OUT = Path(__file__).parent


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def primary(data):
    return [r for r in data if r['cohort'] == 'main' and r['is_primary_window'] == 'True']


def number(row, field):
    value = float(row[field])
    assert math.isfinite(value), field
    return value


windows = primary(rows(REPORT / 'WINDOWS.csv'))
assert len(windows) == 240
assert all(r['comparison_eligible'] == r['complete'] == 'True' and r['window'] == 'full'
           and float(r['window_start_minute']) == 0. and float(r['window_end_minute']) == 600.
           for r in windows)
emissions = [r for r in primary(rows(REPORT / 'EMISSIONS.csv'))
             if r['emissions_factor_case_id'] == 'CAMX2023r2_DG75pct']
assert len(emissions) == 240
critical = rows(RUN / 'review/main_service_analysis01/CRITICAL_UNION.csv')
energy = rows(RUN / 'review/main_energy_analysis01/CLASS_POLICY.csv')
comparisons = primary(rows(REPORT / 'V19_V20_COMPARISON.csv'))
assert len(comparisons) == 240 and all(r['comparison_valid'] == 'True' for r in comparisons)
checks = 0
max_abs = 0.


def check(a, b):
    global checks, max_abs
    max_abs = max(max_abs, abs(a - b))
    assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-8), (a, b)
    checks += 1


for item in critical:
    group = [r for r in windows if r['canonical_class'] == item['canonical_class'] and r['alias'] == item['alias']]
    field = item['priority'] + '_material_union_minutes'
    assert len(group) == int(item['n']) == 20
    check(sum(number(r, field) for r in group), number(item, 'material_union_minutes_sum'))
    check(sum(number(r, field) > 0 for r in group), number(item, 'material_cases'))

metrics = ('actual_energy_fee_USD', 'accounting_cost_USD', 'grid_purchase_kWh', 'GT_gas_MMBtu',
           'boiler_gas_MMBtu', 'DG_electric_work_kWh', 'DG_fuel_budget_debit_kWh_equivalent')
totals = {}
for item in energy:
    group = [r for r in windows if r['alias'] == item['alias']
             and (item['canonical_class'] == 'ALL' or r['canonical_class'] == item['canonical_class'])]
    carbon = [r for r in emissions if r['alias'] == item['alias']
              and (item['canonical_class'] == 'ALL' or r['canonical_class'] == item['canonical_class'])]
    assert len(group) == len(carbon) == int(item['case_count'])
    for field in metrics:
        check(sum(number(r, field) for r in group), number(item, field))
    check(sum(number(r, 'total_CO2_kg_conditional') for r in carbon), number(item, 'total_CO2_kg_conditional'))
    if item['canonical_class'] == 'ALL':
        totals[item['alias']] = {f: number(item, f) for f in (*metrics, 'total_CO2_kg_conditional')}

version_counts = {}
for priority in ('P0', 'P1'):
    key = priority + '_material_union_minutes'
    delta = [number(r, 'delta_' + key) for r in comparisons]
    version_counts[priority] = dict(improved=sum(v < 0 for v in delta), worse=sum(v > 0 for v in delta), same=sum(v == 0 for v in delta))

inputs = [REPORT / name for name in ('WINDOWS.csv', 'EMISSIONS.csv', 'V19_V20_COMPARISON.csv')]
inputs += [RUN / 'review/main_service_analysis01/CRITICAL_UNION.csv',
           RUN / 'review/main_energy_analysis01/CLASS_POLICY.csv', Path(__file__)]
value = dict(status='passed', scope='Main80 primary600 only; no claim for other cohorts or full406',
             rows=240, scalar_checks=checks, max_absolute_difference=max_abs,
             union_version_direction_counts=version_counts, energy_totals=totals,
             inputs=[dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in inputs],
             physical_episodes_started=0, raw_opened=False, annual_values_read=False)
with (OUT / 'ROOT_REVIEW01.json').open('x', encoding='utf8') as stream:
    json.dump(value, stream, ensure_ascii=False, indent=2)
print(json.dumps(value, ensure_ascii=True))
