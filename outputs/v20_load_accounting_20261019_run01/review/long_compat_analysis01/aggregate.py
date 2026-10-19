"""Summarize the already qualified compact long/compat analysis; no physics."""
from pathlib import Path
import csv
import json
import sys
ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from publication import release_gate as gate
from scripts.v20_report import csv_new

OUT=Path(__file__).resolve().parent
proof=gate.read(OUT/'PROOF.json')
assert proof['status']=='passed' and proof['qualified_target_pairs']==31
assert gate.sha(OUT/'ANALYSIS.json')==proof['analysis_sha256']
data=gate.read(OUT/'ANALYSIS.json');full=data['long_full'];tails=data['recovery_tails']
readback=ROOT/gate.RUN_REL/'paper_results/LONG_COMPAT_VERIFIED01'
receipt=gate.read(readback/'RECEIPT.json');assert gate.sha(readback/'EMISSIONS.csv')==receipt['output_files']['EMISSIONS.csv']
with (readback/'EMISSIONS.csv').open(encoding='utf-8-sig',newline='') as f:
    emissions=[r for r in csv.DictReader(f) if r['cohort']=='long' and r['window']=='registered_full' and r['emissions_factor_case_id']=='CAMX2023r2_DG75pct']
assert len(full)==len(tails)==len(emissions)==24
service=[];energy=[];cases=[]
for alias in ('E','K1','C19'):
    rows=[r for r in full if r['alias']==alias];ts=[r for r in tails if r['alias']==alias]
    ds=[r for r in data['diesel_and_recovery'] if r['cohort']=='long' and r['alias']==alias]
    es=[r for r in emissions if r['alias']==alias]
    assert len(rows)==len(ts)==len(ds)==len(es)==8
    for priority in ('P0','P1','P2','P3'):
        for carrier in ('e','h','c'):
            sums={f:sum(r[priority+'_'+carrier+'_'+f] for r in rows) for f in ('requested_kWh','delivered_kWh','ENS_kWh','disconnected_ENS_kWh','connected_unserved_ENS_kWh','material_minutes')}
            service.append(dict(alias=alias,qualified_scenarios=8,window_minutes=[0.,1065.],priority=priority,carrier=carrier,**sums))
    row=dict(alias=alias,qualified_scenarios=8,window_minutes=[0.,1065.],DG_exhausted_scenarios=sum(bool(r['observed_DG_exhaustion_minutes']) for r in ds),
        P0_material_union_scenario_minutes=sum(r['P0_material_union_minutes'] for r in rows),P1_material_union_scenario_minutes=sum(r['P1_material_union_minutes'] for r in rows),
        tail_P0_material_union_scenario_minutes=sum(r['P0_material_union_minutes'] for r in ts),tail_P1_material_union_scenario_minutes=sum(r['P1_material_union_minutes'] for r in ts),
        DG_minimum_remaining_kWh_equivalent=min(r['DG_budget_finish_kWh_equivalent'] for r in ds),DG_maximum_remaining_kWh_equivalent=max(r['DG_budget_finish_kWh_equivalent'] for r in ds))
    for key in ('actual_energy_fee_USD','accounting_cost_USD','DG_electric_work_kWh','DG_shaft_work_kWh','DG_loss_work_kWh','GT_gas_MMBtu','boiler_gas_MMBtu'):
        row[key]=sum(r[key] for r in rows)
    for key in rows[0]['actual_source_integrals']:
        row[key]=sum(r['actual_source_integrals'][key]['value'] for r in rows)
    for key in ('grid_CO2_kg','natural_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional'):
        row[key]=sum(float(r[key]) for r in es)
    energy.append(row)
    for r in rows:
        t=next(v for v in ts if v['batch']==r['batch']);d=next(v for v in ds if v['batch']==r['batch'])
        cases.append(dict(batch=r['batch'],alias=alias,source_day_index=r['source_day_index'],diesel_design=r['diesel_design'],window_minutes=[0.,1065.],
            P0_material_union_minutes=r['P0_material_union_minutes'],P1_material_union_minutes=r['P1_material_union_minutes'],tail_P0_material_union_minutes=t['P0_material_union_minutes'],tail_P1_material_union_minutes=t['P1_material_union_minutes'],
            P0_e_ENS_kWh=r['P0_e_ENS_kWh'],P0_h_ENS_kWh=r['P0_h_ENS_kWh'],P0_c_ENS_kWh=r['P0_c_ENS_kWh'],
            P1_e_ENS_kWh=r['P1_e_ENS_kWh'],P1_h_ENS_kWh=r['P1_h_ENS_kWh'],P1_c_ENS_kWh=r['P1_c_ENS_kWh'],
            P2_e_ENS_kWh=r['P2_e_ENS_kWh'],P2_c_ENS_kWh=r['P2_c_ENS_kWh'],P3_e_ENS_kWh=r['P3_e_ENS_kWh'],P3_c_ENS_kWh=r['P3_c_ENS_kWh'],
            DG_shaft_work_kWh=r['DG_shaft_work_kWh'],DG_budget_finish_kWh_equivalent=r['DG_budget_finish_kWh_equivalent'],observed_DG_exhaustion_minutes=d['observed_DG_exhaustion_minutes'],
            actual_energy_fee_USD=r['actual_energy_fee_USD'],conditional_CO2_kg=d['conditional_CO2_kg']))
compat=[]
for r in data['compat_comparisons']:
    keys=('C19_batch','C18_batch','window_minutes','C19_P0_material_union_minutes','C18_P0_material_union_minutes','C19_P1_material_union_minutes','C18_P1_material_union_minutes',
        'C18_minus_C19_P2_e_ENS_kWh','C18_minus_C19_P3_e_ENS_kWh','C18_minus_C19_actual_energy_fee_USD','C18_minus_C19_DG_shaft_work_kWh','C18_minus_C19_total_CO2_kg_conditional')
    compat.append({k:r[k] for k in keys})
tables=dict(LONG_AGGREGATE_SERVICE=service,LONG_AGGREGATE_ENERGY=energy,LONG_CASE_SUMMARY=cases,COMPAT_SUMMARY=compat)
for name,rows in tables.items():csv_new(OUT/(name+'.csv'),rows)
aggregate_proof=dict(status='passed',analysis_sha256=proof['analysis_sha256'],script_sha256=gate.sha(Path(__file__)),
    comparison_scope='Eight seen registered long cases per policy; scenario-minute sums, not outage probabilities',
    output_sha256={name+'.csv':gate.sha(OUT/(name+'.csv')) for name in tables},row_counts={k:len(v) for k,v in tables.items()},raw_opened=False,physical_episodes_started=0)
with (OUT/'AGGREGATE_PROOF.json').open('x',encoding='utf8') as f:json.dump(aggregate_proof,f,indent=2)
print(json.dumps(aggregate_proof))
