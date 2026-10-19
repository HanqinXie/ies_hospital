"""Independent saved-table readback and explicit external-code version mapping.

No table regeneration, simulation, raw, annual or scientific evidence edit.
"""
from datetime import datetime,timezone
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path

B=Path(__file__).resolve().parents[1];ROOT=B.parents[1]
def read(p):return json.loads(Path(p).read_text(encoding='utf8'))
def rows(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def write(p,value):
    with Path(p).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
def output_check(directory,receipt_name):
    receipt=read(directory/receipt_name)
    for name,value in receipt['output_sha256'].items():
        p=(directory/name).resolve()
        if not p.is_relative_to(directory.resolve()) or sha(p)!=value:raise ValueError('Changed final table '+str(p))
    return dict(directory=str(directory.relative_to(B)),receipt=receipt_name,receipt_sha256=sha(directory/receipt_name),
        output_files_verified=len(receipt['output_sha256']))
def main():
    spec=importlib.util.spec_from_file_location('final_table_readback_guards',B/'scripts/finish_final_expost_r4.py')
    guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    source=guard.verify_frozen_sources(ROOT,read(B/'MODEL_FREEZE_CLOSEOUT01.json'))
    paper=B/'paper_results/FINAL02';normal=B/'review/compatibility_normal4_final_r3'
    outputs=[output_check(B/'review/summary_ALL_FINAL02','SUMMARY_RECEIPT.json'),
        output_check(B/'review/compatibility_key7_final','SUMMARY.json'),
        output_check(B/'review/energy_tradeoff/long_final_complete01','RECEIPT.json'),
        output_check(normal,'SUMMARY.json'),output_check(paper,'PAPER_RESULTS_RECEIPT.json')]
    collector=B/'scripts/summarize_compatibility.py'
    if read(normal/'SUMMARY.json')['script_sha256']!=sha(collector):raise ValueError('Normal proof collector version differs')
    coverage=rows(paper/'COHORT_COVERAGE.csv');guard.verify_coverage(coverage)
    background=guard.verify_backgrounds(coverage,rows(paper/'CONFIRMATION_BACKGROUND_COVERAGE.csv'))
    status=rows(paper/'OUTCOME_STATUS.csv')
    if len(status)!=348 or any(r['eligible']!='True' for r in status):raise ValueError('Final case denominator is incomplete')
    key=read(B/'review/compatibility_key7_final/SUMMARY.json')
    guard.require_matches(key['comparisons'],set(key['cases'].values()),{tuple(p) for p in key['policy_pairs']})
    long=read(B/'review/energy_tradeoff/long_final_complete01/RECEIPT.json')
    if (long['registered_policy_outcomes'],long['eligible_complete_policy_outcomes'],long['matched_policy_case_comparisons'],long['issues'])!=(24,24,16,[]):
        raise ValueError('Final long evidence incomplete')
    numbers={};directions={}
    for cohort in ('MAIN','CONFIRMATION'):
        own=[r for r in rows(paper/(cohort+'_COST_EMISSIONS.csv')) if r['canonical_class']=='ALL']
        for r in own:
            values=[float(r[k]) for k in ('grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional')]
            if not all(math.isfinite(v) for v in values) or abs(sum(values[:2])-values[2])>1e-6:
                raise ValueError('CO2 component identity failed')
            if r['DG_exact_CO2_unknown']!='True' or r['emissions_is_dispatch_objective']!='False':raise ValueError('DG/dispatch scope changed')
        main={r['policy']:{k:float(r[k]) for k in ('actual_energy_fee_USD','grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional')}
            for r in own if r['main_registered_factor_case']=='True'}
        numbers[cohort.lower()]=main
        directions[cohort.lower()]={p:dict(C19_minus_comparator_CO2_kg=main['C19']['total_CO2_kg_conditional']-main[p]['total_CO2_kg_conditional'],
            C19_minus_comparator_fee_USD=main['C19']['actual_energy_fee_USD']-main[p]['actual_energy_fee_USD']) for p in ('E','K1')}
    normal_cost=rows(paper/'NORMAL_CASE_COST_EMISSIONS.csv')
    if len(normal_cost)!=48 or any(r['annualized']!='False' or r['cross_window_total_permitted']!='False' for r in normal_cost):
        raise ValueError('Normal windows were pooled/annualized')
    versions=B/'review/external_analysis_versions';versions.mkdir(exist_ok=True)
    before=B/'review/normal_compatibility_repair_r2/BEFORE_summarize_compatibility.py'
    files=[before,versions/'summarize_compatibility_e877c7429aaf6ccd06b6a772e71a888745d4ece289052d242993a8e6afba0ff5.py',collector]
    entries=[]
    for p in files:
        value=sha(p);dest=versions/('summarize_compatibility_'+value+'.py')
        if not dest.exists():dest.write_bytes(p.read_bytes())
        if sha(dest)!=value:raise ValueError('Code snapshot mismatch')
        entries.append(dict(original_logical_path='scripts/summarize_compatibility.py',sha256=value,
            immutable_snapshot=str(dest.relative_to(B))))
    version_map=dict(schema='hospital_ies.v19.external_analysis_versions.v1',utc=datetime.now(timezone.utc).isoformat(),versions=entries,
        interpretation='Original report references keep their recorded SHA. Resolve each to its immutable snapshot, not the current logical path bytes. Storage/reference mapping only; no old evidence is rewritten.',
        reported_428d_consumers=['review/event_layers/final_main_confirmation_propagation01','review/finite_clocks/final_finite_clocks01'],
        e877_consumers=['review/compatibility_normal4_final_r2','review/compatibility_key7_final','review/energy_tradeoff/long_final_complete01','paper_results/FINAL01'],
        current_consumers=['review/compatibility_normal4_final_r3','paper_results/FINAL02'])
    write(versions/'VERSION_MAP.json',version_map)
    receipt=dict(schema='hospital_ies.v19.final_expost_completion.v1',utc=datetime.now(timezone.utc).isoformat(),all_succeeded=True,
        source=source,verified_outputs=outputs,strict_key_registered_full_pairs=35,strict_normal_registered_full_pairs=12,
        final_long_policy_outcomes=24,final_long_C19_E_or_K1_pairs=16,qualified_cases_per_policy=dict(main=80,confirmation=32,normal=4),
        actual_confirmation_background_verification=background,main_factor_case_numbers=numbers,C19_directional_differences=directions,
        emissions_scope='factor-based operational grid+gas subtotal plus explicitly conditional DG/total; exact DG chemical emissions unknown; no lifecycle/CO2e or emissions objective',
        negative_CO2_result_preserved='C19 conditional total exceeds E in both the main and confirmation case ensembles; lower DG alone is not a carbon-reduction result.',
        normal_windows_not_pooled_or_annualized=True,version_map_sha256=sha(versions/'VERSION_MAP.json'),
        previous_failed_or_unknown_results_retained=['FINAL_EXPOST_AUTORUN_R2_RESULT.json','FINAL_EXPOST_AUTORUN_R3_RESULT.json','FINAL_EXPOST_AUTORUN_R4_RESULT.json','paper_results/FINAL01','review/summary_ALL_FINAL01'],
        physical_episodes_started=0,raw_files_opened=0,annual_inputs_loaded=0,verifier_sha256=sha(Path(__file__)))
    source_after=guard.verify_frozen_sources(ROOT,read(B/'MODEL_FREEZE_CLOSEOUT01.json'))
    if source_after!=source:raise ValueError('Source changed during final verification')
    write(B/'FINAL_EXPOST_COMPLETION01.json',receipt)
    print(json.dumps(receipt,ensure_ascii=False))
if __name__=='__main__':main()
