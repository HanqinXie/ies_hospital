"""Small canonical-only A/C case extract; not a process reconstruction or gate."""
from pathlib import Path
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json

HERE=Path(__file__).resolve().parent
CANON=Path('E:/hospital_ies_v21_20261023/final_blind01/analysis01/TABLES.json.gz')
EXPECTED='f70141272d4f62a20bd82d51991d36b33aa86cdeee201e3c05a02c7f877facd9'
SID='m21_L1_55e24889a6e1b237d15a'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


data=CANON.read_bytes()
if hashlib.sha256(data).hexdigest()!=EXPECTED:
    raise ValueError('Original canonical SHA mismatch')
tables=json.loads(gzip.decompress(data));cases=tables['CASE_METRICS']
scene={r['policy_alias']:r for r in cases if r['scenario_id']==SID}
a,c=scene['A'],scene['C']
fields=[f'P3_{carrier}_{field}' for carrier in 'ehc' for field in ('requested_kWh','delivered_kWh','ENS_kWh','disconnected_ENS_kWh',
        'connected_unserved_ENS_kWh','material_minutes','longest_material_minutes','interruption_count','repeat_interruption_count')]
fields += [f'P3_union_{field}' for field in ('material_minutes','longest_material_minutes','interruption_count','repeat_interruption_count')]
fields += ['critical_union_minutes','critical_initial_handoff_gap_minutes','critical_post_handoff_gap_minutes','critical_recovery_gap_minutes',
           'energy_cost_USD','total_CO2_kg_conditional']
fields += [f'phase_recovery_P3_{carrier}_ENS_kWh' for carrier in 'ehc']
fields += [f'{priority}_{carrier}_ENS_kWh' for priority in ('P0','P1','P2') for carrier in 'ehc']
values=[dict(metric=m,reference_C=c[m],candidate_A=a[m],A_minus_C=a[m]-c[m]) for m in fields]
with (HERE/'actual01/FIELD_SCOPE176_65.csv').open(encoding='utf-8-sig',newline='') as f:
    scope=list(csv.DictReader(f))
material=[r['metric'] for r in scope if r['in_material65']=='True']
critical=[m for m in material if m.startswith(('P0_','P1_')) or m=='critical_union_minutes']
gaps=['critical_initial_handoff_gap_minutes','critical_post_handoff_gap_minutes','critical_recovery_gap_minutes']
by_scene={}
for r in cases:
    by_scene.setdefault(r['scenario_id'],{})[r['policy_alias']]=r
exact=[]
for m in critical+gaps:
    exact.append(dict(metric=m,exact_equal_pairs=sum(rows['A'][m]==rows['C'][m] for rows in by_scene.values()),registered_pairs=48))
ens=[]
for p in ('P0','P1'):
    for carrier in 'ehc':
        m=f'{p}_{carrier}_ENS_kWh'
        delta=[dict(scenario_id=s,A_minus_C=rows['A'][m]-rows['C'][m]) for s,rows in by_scene.items() if rows['A'][m]!=rows['C'][m]]
        ens.append(dict(metric=m,nonzero_pairs=delta,exact_equal_pairs=48-len(delta)))
result=dict(schema='hospital_ies.final12_local_A_C_summary_extract.v1',utc=datetime.now(timezone.utc).isoformat(),
    canonical_path=str(CANON),canonical_sha256=EXPECTED,original_script_sha256=sha(__file__),
    scenario={k:a[k] for k in ('scenario_id','source_block_id','season_quarter','canonical_class','fault_target','fault_availability',
        'fault_duration_minutes','response_delay_minutes','phase','window_minutes','configuration_id')},
    reference=dict(policy_id=c['policy_id'],RESULT_path=c['result_path'],RESULT_sha256=c['result_sha256']),
    candidate=dict(policy_id=a['policy_id'],RESULT_path=a['result_path'],RESULT_sha256=a['result_sha256']),
    original_values_and_deltas=values,critical_material33_plus_gap3_global_exactness=exact,
    critical_ENS_global_exactness=ens,raw_or_sidecar_read=False,new_process_or_causal_reconstruction=False,
    NOT_GATE=True,NOT_NEW_INDEPENDENT_POOL=True,
    limits=['This is a post-hoc descriptive summary case, not a new representative raw selection.',
            'P3 phase-recovery ENS equality is not a measured general-service restoration-time certificate.',
            'Per-GT protection-trip timing/devices and the cause of this A/C tradeoff require separate authorized process evidence.'])
if sha(CANON)!=EXPECTED:
    raise ValueError('Canonical changed during read')
target=HERE/'actual01/LOCAL_A_C_TRADEOFF_CASE.json'
with target.open('x',encoding='utf-8') as f:
    json.dump(result,f,ensure_ascii=True,indent=2,allow_nan=False);f.write('\n')
print(json.dumps(dict(case=SID,source_sha256=EXPECTED,output=str(target),output_sha256=sha(target),canonical_unchanged=True,NOT_GATE=True)))
