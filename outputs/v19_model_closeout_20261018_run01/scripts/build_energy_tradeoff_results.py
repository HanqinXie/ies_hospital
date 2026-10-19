"""Paper-ready, source-separated energy trade-offs from compact M19 evidence.

No simulation, trajectory raw, annual input, policy or physical parameter edits.
Development and frozen-final reports require separate explicit invocations.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import copy
import csv
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

SCRIPT=Path(__file__).resolve();RUN=SCRIPT.parents[1];MODEL='M19_model_closeout'
POLICY={'E':'endurance_restore_p0_ready_v14r6','K1':'readiness_handoff_K1_v15',
    'C19':'C19_critical_reserve_v19r1','C18_M19':'C18_service_rich_M19compat'}
DEFAULT_BATCHES=tuple('dev_energy_r1_d'+day+'_'+label+'_'+str(duration)
    for day in ('040','100') for label,duration in (('short',120),('medium',360),('long',780)))

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load_module(name):
    path=SCRIPT.with_name(name+'.py');spec=importlib.util.spec_from_file_location('external_energy_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def number(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def saved_failure(value):return value is True or (type(value) is int and value==1)
def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
def write_csv(path,rows):
    fields=sorted({k for row in rows for k in row})
    with Path(path).open('x',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False,allow_nan=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})

class EnergyTests(unittest.TestCase):
    def test_saved_recovery_and_both_recurrence_definitions_preserved(self):
        metrics=dict(first_clean_minute=5.,confirmed_30min_interval=[30.,60.],recurrence_after_30min_count=1,
            loss_runs=[dict(start_minute=0.,end_minute=5.),dict(start_minute=20.,end_minute=30.),dict(start_minute=70.,end_minute=80.)])
        result=recovery_fields(metrics)
        self.assertEqual(result['first_clean_minute'],5.)
        self.assertEqual(result['first_saved_material_recurrence_after_first_clean_minute'],20.)
        self.assertEqual(result['first_saved_material_recurrence_after_30min_confirmation_minute'],70.)
        self.assertEqual(result['recurrence_after_30min_count'],1)
    def test_missing_service_is_unknown_not_zero(self):
        self.assertIsNone(recovery_fields({})['first_clean_minute'])
        row=lower_service(dict(complete_registered_window=True,timeline_status='unknown_CSV_coverage_hole_or_overlap'),
            dict(demand_kWh=100.,served_kWh=80.,ENS_kWh=20.,CSV_service_integral_status='observed_prefix_only'))
        self.assertIsNone(row['demand_kWh']);self.assertIsNone(row['served_kWh'])
    def test_bound_complete_lower_priority_service_not_inferred_from_source(self):
        row=lower_service(dict(complete_registered_window=True,timeline_status='available',CSV_vs_RESULT_max_ENS_difference_kWh=0.),
            dict(demand_kWh=100.,served_kWh=37.,ENS_kWh=63.,served_fraction=.37,CSV_service_integral_status='complete_registered_window'))
        self.assertEqual(row['served_kWh'],37.);self.assertEqual(row['demand_kWh'],100.)
    def test_duration_family_changes_only_common_fault_ends(self):
        a=dict(fault_duration_minutes=120.,exogenous_events=[dict(kind='device_fault',device='GRID',start_minute=32.5,end_minute=152.5)],shock={'e':.9},start_hour=2401)
        b=copy.deepcopy(a);b['fault_duration_minutes']=780.;b['exogenous_events'][0]['end_minute']=812.5
        self.assertEqual(duration_family(a),duration_family(b))
        b['shock']['e']=1.0;self.assertNotEqual(duration_family(a),duration_family(b))
        b['exogenous_events'][0]['end_minute']=900.
        with self.assertRaises(ValueError):duration_family(b)
    def test_component_emissions_binding_rejects_foreign_raw(self):
        context=dict(scenario_id='s',policy_id='p',raw_sha256='a',window_start_minute=0.,window_end_minute=1065.)
        row=dict(scenario_id='s',policy_id='p',raw_sha256='b',model_id=MODEL,window='registered_full',
            window_start_minute=0.,window_end_minute=1065.,complete=True,emissions_value_scope='registered_window',DG_exact_CO2_unknown=True)
        self.assertFalse(emission_bound(row,context));row['raw_sha256']='a';self.assertTrue(emission_bound(row,context))
    def test_development_does_not_become_final(self):
        self.assertFalse(role_allowed('development','frozen_final',None,None))
        self.assertTrue(role_allowed('development','development',None,None))
        self.assertFalse(role_allowed('seen_regression','frozen_final','registered','different'))
        self.assertTrue(role_allowed('seen_regression','frozen_final','same','same'))
    def test_saved_integer_failure_is_reported_without_unknown_coercion(self):
        self.assertTrue(saved_failure(1));self.assertTrue(saved_failure(True))
        for value in (0,False,None,'unknown','1'):
            self.assertFalse(saved_failure(value))
    def test_final_long_does_not_claim_development_duration_sensitivity(self):
        cases=[dict(source_day_index=40,scenario_id=s,outage_duration_minutes=780.) for s in ('three_GT_fault','two_GT_fault')]
        proof=duration_proofs(cases,{},'frozen_final',[])[0]
        self.assertIsNone(proof['same_background_duration_design_valid'])
        self.assertEqual(proof['registered_cases'],2)
        self.assertEqual(proof['duration_proof_status'],'not_applicable_final_cases')
    def test_missing_diagnostic_carriers_keep_all_six_unknown_rows(self):
        identity=dict(batch='b',scenario_id='s',policy_id='p',eligible=False)
        rows=list(project_lower_service([],{},identity))
        self.assertEqual(len(rows),6)
        self.assertEqual({(r['priority'],r['carrier']) for r in rows},{(p,c) for p in ('P2','P3') for c in ('e','h','c')})
        self.assertTrue(all(r['served_kWh'] is None and r['demand_kWh'] is None for r in rows))

def recovery_fields(metrics):
    fields=('ENS_kWh','disconnected_ENS_kWh','connected_unserved_ENS_kWh','material_minutes','longest_material_minutes',
        'loss_runs','first_loss_minute','first_clean_minute','confirmed_30min_interval','recurrence_after_30min_count','recovery_status')
    result={k:metrics.get(k) for k in fields};runs=metrics.get('loss_runs')
    first=metrics.get('first_clean_minute');confirmed=metrics.get('confirmed_30min_interval')
    for label,threshold in (('first_clean',first),('30min_confirmation',confirmed[1] if isinstance(confirmed,list) and len(confirmed)==2 else None)):
        starts=[r['start_minute'] for r in (runs or []) if number(r.get('start_minute')) and number(threshold) and r['start_minute']>threshold+1e-8]
        result['first_saved_material_recurrence_after_'+label+'_minute']=min(starts) if starts else None
    return result

def lower_service(record,carrier):
    valid=(record.get('eligible',True) and record.get('complete_registered_window') is True
        and record.get('timeline_status')=='available'
        and number(record.get('CSV_vs_RESULT_max_ENS_difference_kWh'))
        and record['CSV_vs_RESULT_max_ENS_difference_kWh']<=1e-6
        and carrier.get('CSV_service_integral_status')=='complete_registered_window')
    return dict(demand_kWh=carrier.get('demand_kWh') if valid else None,
        served_kWh=carrier.get('served_kWh') if valid else None,
        served_fraction=carrier.get('served_fraction') if valid else None,
        CSV_integrated_ENS_kWh=carrier.get('CSV_integrated_ENS_kWh') if valid else None,
        saved_ENS_kWh=carrier.get('ENS_kWh'),material_minutes=carrier.get('material_minutes'),
        service_integral_status='complete_bound_registered_window' if valid else 'unknown_incomplete_or_unbound_CSV',
        service_integral_formula='sum(segment_hours * loads/unserved arrays); served=demand-unserved including actual cuts')

def project_lower_service(carriers,record,identity):
    for priority in ('P2','P3'):
        for carrier in ('e','h','c'):
            saved=next((r for r in carriers if all(r.get(k)==identity[k] for k in ('batch','scenario_id','policy_id'))
                and r['priority']==priority and r['carrier']==carrier),{})
            yield dict(identity,priority=priority,carrier=carrier,
                **lower_service(dict(record,eligible=identity['eligible']),saved))

def duration_family(scene):
    cp=load_module('summarize_compatibility')
    physical={k:copy.deepcopy(v) for k,v in scene.items() if k in cp.PHYSICAL_FIELDS or k.startswith(('fault_','shock_'))}
    duration=physical.pop('fault_duration_minutes',None)
    for event in physical.get('exogenous_events',[]):
        if event.get('kind')=='device_fault' and event.get('device') in ('GRID','GT_1','GT_2','GT_3'):
            if not number(duration) or not number(event.get('start_minute')) or event.get('end_minute')!=event['start_minute']+duration:
                raise ValueError('Common fault endpoint is not the registered outage duration')
            event['end_minute']='registered_common_outage_endpoint'
    return physical

def duration_proofs(cases,scenes,cohort,issues):
    families=[]
    for day in sorted({r['source_day_index'] for r in cases}):
        own=[r for r in cases if r['source_day_index']==day]
        base=dict(source_day_index=day,outage_durations_minutes=sorted({r['outage_duration_minutes'] for r in own}),
            registered_cases=len({r['scenario_id'] for r in own}),policy_outcomes=len(own))
        if cohort=='frozen_final':
            families.append(dict(base,same_background_duration_design_valid=None,
                duration_proof_status='not_applicable_final_cases',
                claim='Final registered cases are reported individually; no duration-sensitivity or pooled effect claim.'))
            continue
        family_hashes=[]
        for r in own:
            try:family_hashes.append(digest(duration_family(scenes[(r['batch'],r['scenario_id'])])))
            except ValueError as exc:family_hashes.append(None);issues.append(dict(batch=r['batch'],issue=str(exc)))
        fields=('source_binding_sha256','model_config_sha256','policy_panel_sha256','input_inventory_sha256','initial_state_sha256','window_start_minute','window_end_minute')
        valid=all(r['eligible'] for r in own) and None not in family_hashes and len(set(family_hashes))==1 and all(len({r.get(f) for r in own})==1 and own[0].get(f) is not None for f in fields)
        families.append(dict(base,same_background_duration_design_valid=valid,duration_proof_status='verified' if valid else 'unknown_or_mismatched',
            unchanged_physical_projection_except_common_fault_endpoint_sha256=family_hashes[0] if valid else None,
            source_and_reset_inputs_match_across_durations=valid,exogenous_streams_intentionally_different=True,
            claim='Predeclared duration scenario sensitivity, not a same-exogenous-stream policy comparison or independent population sample'))
    return families

def emission_bound(row,context):
    return (all(row.get(k)==context.get(k) for k in ('scenario_id','policy_id','raw_sha256','window_start_minute','window_end_minute'))
        and row.get('model_id')==MODEL and row.get('window')=='registered_full' and row.get('complete') is True
        and row.get('emissions_value_scope')=='registered_window' and row.get('DG_exact_CO2_unknown') is True)

def role_allowed(actual,requested,registered_freeze,frozen_signature):
    if requested=='development':return actual=='development'
    return actual=='seen_regression' and bool(registered_freeze) and registered_freeze==frozen_signature

def read_csv(path):
    def parse(key,value):
        if value=='':return None
        if key.endswith('_sha256'):return value
        if value in ('True','False'):return value=='True'
        try:return json.loads(value)
        except (ValueError,TypeError):return value
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return [{k:parse(k,v) for k,v in r.items()} for r in csv.DictReader(f)]

def build_report(args):
    diag=load_module('diagnose_compact_trajectories');cp=load_module('summarize_compatibility')
    names=list(dict.fromkeys(args.batch or DEFAULT_BATCHES))
    if args.cohort=='frozen_final' and not args.batch:raise ValueError('Frozen final long reports require explicit final --batch names')
    reader=diag.CompactReader()
    data=diag.run_snapshot(SimpleNamespace(all_results=False,batch=names,timeline_batch=names,allow_partial=True),reader)
    metadata_inputs=[]
    def metadata(path):
        path=Path(path);content=path.read_bytes()
        metadata_inputs.append(dict(path=str(path),bytes=len(content),sha256=hashlib.sha256(content).hexdigest()))
        return json.loads(content)
    frozen=metadata(args.freeze) if args.freeze else None
    if args.cohort=='frozen_final':
        if not frozen:raise ValueError('Frozen final scope requires --freeze')
        cp.compare_batches(frozen,[],{},[])
    registry_path=RUN/'references/EMISSIONS_FACTOR_REGISTRY.json';registry=metadata(registry_path)
    registry_sha=sha(registry_path);factors=registry['case_canonical_sha256']
    factor_rows=registry['cases'];main_factor=registry['main_case_id']
    prereg=metadata(RUN/'design/ENERGY_TRADEOFF_PREREGISTRATION.json') if args.cohort=='development' else None
    if prereg and prereg.get('signature')!=digest({k:v for k,v in prereg.items() if k!='signature'}):raise ValueError('Changed energy preregistration signature')
    cases=[];critical=[];lower=[];economics=[];emissions=[];proofs=[];scenes={};issues=list(data['issues'])
    for name in names:
        directory=RUN/'experiments'/name
        plan=reader.json(directory/'plan.json');manifest=reader.json(directory/'manifest.json')
        terminal=reader.json(directory/'BATCH_RESULT.json') if (directory/'BATCH_RESULT.json').exists() else {}
        registered=dict(source_sha256=plan['source_sha256'],model_config=plan['model_config'],
            policies={p['policy_id']:p for p in plan['policy_specs']})
        cp_inputs=[];candidates=cp.collect_batch(directory,frozen or registered,{s['id']:s['id'] for s in manifest['scenarios']},cp_inputs)
        metadata_inputs.extend(cp_inputs)
        receipt_path=directory/'analysis/V19_ANALYSIS_RECEIPT.json';receipt=metadata(receipt_path) if receipt_path.exists() else {}
        receipt_ok=receipt==terminal.get('analysis_receipt') and receipt.get('emissions_registry_sha256')==registry_sha
        csv_sha={**receipt.get('compatibility_export_receipt',{}).get('output_sha256',{}),**receipt.get('output_sha256',{})}
        for csv_name in ('ENGINEERING_INTERVALS.csv','EVENT_TIMELINE.csv','COMMAND_LIFECYCLE.csv','V19_EMISSIONS.csv'):
            path=directory/'analysis'/csv_name
            if not path.is_file() or csv_sha.get(csv_name)!=sha(path):receipt_ok=False;issues.append(dict(batch=name,issue='producer_receipt_CSV_SHA_mismatch:'+csv_name))
        emission_path=directory/'analysis/V19_EMISSIONS.csv'
        producer_emissions=read_csv(emission_path) if emission_path.exists() and receipt_ok else []
        metadata_inputs.append(dict(path=str(emission_path),sha256=sha(emission_path) if emission_path.exists() else None))
        for candidate in candidates:
            if candidate['window']!='registered_full':continue
            scene=candidate['scene'];scenes[(name,candidate['scenario_id'])]=scene
            record=next((r for r in data['trajectories'] if (r['batch'],r['scenario_id'],r['policy_id'])==(name,candidate['scenario_id'],candidate['policy_id'])),{})
            alias=next((k for k,v in POLICY.items() if v==candidate['policy_id']),candidate['policy_id'])
            reasons=list(candidate['reasons'])
            if not role_allowed(manifest.get('evaluation_role'),args.cohort,manifest.get('freeze_signature'),frozen.get('signature') if frozen else None):reasons.append('development_final_role_or_freeze_mismatch')
            if not receipt_ok:reasons.append('inline_producer_CSV_receipt_unbound')
            if plan['source_sha256'].get('outputs/v19_model_closeout_20261018_run01/references/EMISSIONS_FACTOR_REGISTRY.json')!=registry_sha:
                reasons.append('registered_factor_registry_differs')
            design=None
            if prereg:
                design=next((r for r in prereg['comparisons'] if r['new_scene_id']==scene['id']),None)
                if (not design or design.get('new_scene_sha256')!=digest(scene) or plan['model_config']!=prereg['model_config']
                        or plan['policy_specs']!=prereg['policy_specs'] or candidate['window_start_minute']!=0.
                        or candidate['window_end_minute']!=1065.):reasons.append('preregistered_energy_design_or_1065_window_mismatch')
            identity=dict(batch=name,scenario_id=scene['id'],policy_id=candidate['policy_id'],policy=alias,cohort=args.cohort,
                evaluation_role=manifest.get('evaluation_role'),source_day_index=scene.get('source_day_index'),
                outage_duration_minutes=scene.get('fault_duration_minutes'),duration_label=design.get('duration_label') if design else 'registered_final_long',
                window_start_minute=candidate['window_start_minute'],window_end_minute=candidate['window_end_minute'],
                window='registered_full',eligible=not reasons,reasons=reasons,status=candidate['status'],audit_status=candidate['episode_audit_status'],
                result_sha256=candidate['result_sha256'],raw_sha256=record.get('raw_sha256'),
                source_binding_sha256=candidate['source_binding_sha256'],model_config_sha256=candidate['model_config_sha256'],
                policy_panel_sha256=candidate['policy_panel_sha256'],input_inventory_sha256=candidate['input_inventory_sha256'],
                physical_scene_sha256=candidate['physical_scene_sha256'],initial_state_sha256=candidate['initial_state_sha256'],
                applied_exogenous_definition_sha256=candidate['applied_exogenous_definition_sha256'])
            cases.append(dict(identity,original_P0_failure_in_window=(record.get('original_P0_classifier') or {}).get('P0_failure_in_window'),
                DG_first_positive_interval_start_minute=record.get('DG_first_positive_interval_start_minute'),
                DG_exhaustion_event_minutes=record.get('DG_exhaustion_event_minutes'),OPEN_minutes=record.get('OPEN_minutes'),
                timeline_status=record.get('timeline_status'),CSV_vs_RESULT_max_ENS_difference_kWh=record.get('CSV_vs_RESULT_max_ENS_difference_kWh')))
            saved=candidate.get('saved_metrics') or {}
            for priority in ('P0','P1'):
                for carrier in ('e','h','c'):
                    metrics=saved.get('carrier_service',{}).get(priority,{}).get(carrier,{}) if not reasons else {}
                    critical.append(dict(identity,priority=priority,carrier=carrier,**recovery_fields(metrics)))
            lower.extend(project_lower_service(data['carrier_service'],record,identity))
            econ=saved.get('economics',{}) if not reasons else {}
            economics.append(dict(identity,**{k:econ.get(k) for k in ('DG_electric_work_kWh','DG_shaft_work_kWh','DG_loss_work_kWh',
                'DG_rotor_delta_kWh','DG_fuel_budget_debit_kWh_equivalent','DG_original_electric_fee_USD','DG_shaft_equivalent_fee_USD',
                'grid_purchase_kWh','GT_gas_MMBtu','boiler_gas_MMBtu','natural_gas_total_MMBtu','actual_energy_fee_USD','accounting_cost_USD')}))
            for factor,expected in factors.items():
                own=[r for r in producer_emissions if r.get('emissions_factor_case_id')==factor and emission_bound(r,identity)
                    and r.get('emissions_factor_sha256')==expected]
                valid=not reasons and len(own)==1;source=own[0] if valid else {}
                emissions.append(dict(identity,emissions_factor_case_id=factor,emissions_factor_sha256=expected,main_factor_case=factor==main_factor,
                    emissions_status=source.get('emissions_status','unknown_unbound_or_missing'),DG_exact_CO2_unknown=True,
                    emissions_is_dispatch_objective=False,**{k:source.get(k) for k in ('grid_CO2_kg','natural_gas_CO2_kg','grid_plus_gas_CO2_kg',
                        'DG_CO2_kg_conditional','total_CO2_kg_conditional','DG_fuel_liters_conditional','DG_chemical_energy_MMBtu_HHV_conditional')}))
            proofs.append(dict(identity,terminal_matching_evidence=candidate['terminal_matching_evidence'],
                CSV_binding_chain='terminal.analysis_receipt == saved V19_ANALYSIS_RECEIPT; producer output SHA + scenario/policy/raw SHA/window; inline CSV missing source/result SHA columns are not fabricated',
                CSV_receipt_sha256=sha(receipt_path) if receipt_path.exists() else None,CSV_receipt_bound=receipt_ok))
    comparisons=[];energy_pairs=[]
    for key in sorted({(r['batch'],r['scenario_id']) for r in cases}):
        own=[r for r in cases if (r['batch'],r['scenario_id'])==key]
        left=next((r for r in own if r['policy']=='C19'),None)
        if left is None:continue
        for right in [r for r in own if r['policy']!='C19']:
            matched=left['eligible'] and right['eligible'] and all(left.get(f)==right.get(f) and left.get(f) is not None for f in cp.MATCH_FIELDS)
            base=dict(batch=key[0],scenario_id=key[1],source_day_index=left['source_day_index'],outage_duration_minutes=left['outage_duration_minutes'],
                duration_label=left['duration_label'],left_policy='C19',right_policy=right['policy'],matched=matched,
                comparison_scope='same registered scenario/window/source/config/input/reset/applied stream, complete audit pass',
                cohort=args.cohort,source_binding_sha256=left['source_binding_sha256'],model_config_sha256=left['model_config_sha256'])
            for priority in ('P0','P1'):
                for carrier in ('e','h','c'):
                    a=next(r for r in critical if (r['batch'],r['scenario_id'],r['policy'],r['priority'],r['carrier'])==(*key,'C19',priority,carrier))
                    b=next(r for r in critical if (r['batch'],r['scenario_id'],r['policy'],r['priority'],r['carrier'])==(*key,right['policy'],priority,carrier))
                    item=dict(base,priority=priority,carrier=carrier)
                    for field in recovery_fields({}):
                        x,y=a.get(field),b.get(field);item['C19_'+field]=x if matched else None;item[right['policy']+'_'+field]=y if matched else None
                        if field in ('ENS_kWh','material_minutes','longest_material_minutes'):item[field+'_delta_right_minus_C19']=y-x if matched and number(x) and number(y) else None
                    comparisons.append(item)
            for factor in factors:
                item=dict(base,emissions_factor_case_id=factor,main_factor_case=factor==main_factor,DG_exact_CO2_unknown=True)
                for table,fields in ((economics,('DG_shaft_work_kWh','DG_electric_work_kWh','DG_loss_work_kWh','DG_rotor_delta_kWh','DG_original_electric_fee_USD','DG_shaft_equivalent_fee_USD','actual_energy_fee_USD')),
                        (emissions,('grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional'))):
                    select=lambda policy:next(r for r in table if (r['batch'],r['scenario_id'],r['policy'])==(*key,policy) and (table is economics or r['emissions_factor_case_id']==factor))
                    a,b=select('C19'),select(right['policy'])
                    for field in fields:
                        x,y=a.get(field),b.get(field);item['C19_'+field]=x if matched else None;item[right['policy']+'_'+field]=y if matched else None
                        item[field+'_delta_right_minus_C19']=y-x if matched and number(x) and number(y) else None
                energy_pairs.append(item)
    families=duration_proofs(cases,scenes,args.cohort,issues)
    if reader.unstable:raise ValueError('Canonical input changed during read')
    out=RUN/'review/energy_tradeoff'/args.name;out.mkdir(parents=True,exist_ok=False)
    tables=dict(CASES=cases,CRITICAL_SERVICE=critical,CRITICAL_COMPARISON=comparisons,LOWER_PRIORITY_SERVICE=lower,
        ECONOMICS_ENERGY=economics,EMISSIONS=emissions,ENERGY_COMPARISONS=energy_pairs)
    for name,rows in tables.items():write_csv(out/(name+'.csv'),rows)
    write_json(out/'SAME_BACKGROUND_DURATION_PROOF.json',families);write_json(out/'MATCH_PROOFS.json',proofs)
    render_report(out,args.cohort,cases,critical,lower,economics,emissions,main_factor,families)
    receipt=dict(schema='hospital_ies.v19.saved_energy_tradeoff_results.v1',cohort=args.cohort,batches=names,
        registered_policy_outcomes=len(cases),eligible_complete_policy_outcomes=sum(r['eligible'] for r in cases),
        matched_policy_case_comparisons=sum(r['matched'] for r in comparisons)//6,table_rows={k:len(v) for k,v in tables.items()},
        sources=list(reader.inputs.values())+metadata_inputs,issues=issues,raw_files_opened=0,annual_inputs_loaded=0,
        physical_episodes_started=0,development_is_not_final=True,DG_exact_CO2_unknown=True,
        no_across_background_or_duration_totals=True,script_sha256=sha(SCRIPT),
        diagnostic_program_sha256=sha(SCRIPT.with_name('diagnose_compact_trajectories.py')),
        compatibility_program_sha256=sha(SCRIPT.with_name('summarize_compatibility.py')),
        main_factor_case_id=main_factor,emissions_registry_sha256=registry_sha,
        output_sha256={p.name:sha(p) for p in out.iterdir() if p.is_file()})
    write_json(out/'RECEIPT.json',receipt)
    print(json.dumps(dict(output=str(out),registered_policy_outcomes=len(cases),eligible_complete_policy_outcomes=receipt['eligible_complete_policy_outcomes'],
        issues=len(issues),raw_files_opened=0,physical_episodes_started=0),ensure_ascii=False))
    return 0

def render_report(out,cohort,cases,critical,lower,economics,emissions,main_factor,families):
    def fmt(value):return ('unknown' if value is None else str(round(value,3)) if number(value) else str(value))
    def locate(table,row,**extra):return next(r for r in table if all(r.get(k)==v for k,v in {**{k:row[k] for k in ('batch','scenario_id','policy')},**extra}.items()))
    lines=['# Same-background short/medium/long energy trade-off\n\n',
        'Scope: **'+cohort+'**. These are actual saved M19 outcomes; development observations are not final or independent confirmation. '
        'Each row uses its complete registered window, not legacy full600. Source-day indices '+str(sorted({r['source_day_index'] for r in cases}))+' are separate disclosed backgrounds; no cross-duration/background totals or population superiority claim.\n\n',
        '|day|fault min|policy|P0 ENS [e,h,c] kWh|P1 ENS [e,h,c] kWh|P2 served fractions [e,h,c]|P3 served fractions [e,h,c]|DG shaft kWh|energy fee USD|grid+gas CO2 kg|conditional total CO2 kg|saved P0 failure|\n',
        '|---|---:|---|---|---|---|---|---:|---:|---:|---:|---|\n']
    for row in sorted(cases,key=lambda r:(r['source_day_index'],r['outage_duration_minutes'],r['policy'])):
        vectors=[]
        for priority in ('P0','P1'):vectors.append('['+', '.join(fmt(locate(critical,row,priority=priority,carrier=c)['ENS_kWh']) for c in ('e','h','c'))+']')
        for priority in ('P2','P3'):vectors.append('['+', '.join(fmt(locate(lower,row,priority=priority,carrier=c)['served_fraction']) for c in ('e','h','c'))+']')
        econ=locate(economics,row);em=locate(emissions,row,emissions_factor_case_id=main_factor)
        values=[row['source_day_index'],row['outage_duration_minutes'],row['policy'],*vectors,econ['DG_shaft_work_kWh'],econ['actual_energy_fee_USD'],
            em['grid_plus_gas_CO2_kg'],em['total_CO2_kg_conditional'],row['original_P0_failure_in_window']]
        lines.append('|'+ '|'.join(fmt(v) for v in values)+'|\n')
    lines.append('\nFirst clean time, saved 30-minute confirmation, first subsequent saved material-loss start and full saved loss runs appear by carrier in CRITICAL_SERVICE/CRITICAL_COMPARISON. '
        'These projections preserve the inherited material thresholds and recurrence count; they do not redefine recovery. P2/P3 demand and delivered kWh come from bound ENGINEERING_INTERVALS arrays, '
        'with complete coverage and CSV-versus-RESULT ENS checks. A zero-demand carrier has an undefined served fraction; its zero demand and delivery remain explicit in the CSV. Zero actual source output alone does not classify load delivery.\n\n'
        'DG shaft/electrical/loss/rotor ledgers and original/shaft-equivalent fees remain separate. Grid plus gas CO2 is a factor-based operational subtotal. '
        'DG and total CO2 are conditional constant-specific-fuel scenarios, HHV, with exact startup/idle/low-load chemical emissions unknown. '
        'The main factor case is '+main_factor+'; all four registered cases remain in EMISSIONS/ENERGY_COMPARISONS. No emissions dispatch objective is introduced.\n\n')
    failed=[r for r in cases if saved_failure(r['original_P0_failure_in_window'])]
    lines.append('Saved P0-failure outcomes retained: '+('; '.join('day'+str(r['source_day_index'])+'/'+str(r['outage_duration_minutes'])+'min/'+r['policy'] for r in failed) or 'none in the selected complete records')+'. '
        'A completed numerical episode and passed energy audit do not mean critical service was protected.\n\n')
    lines.append('Duration design proof: '+json.dumps(families,ensure_ascii=False)+'\n')
    (out/'REPORT.md').write_text(''.join(lines),encoding='utf8')

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('self-test')
    b=sub.add_parser('build');b.add_argument('--name',required=True);b.add_argument('--cohort',choices=('development','frozen_final'),required=True)
    b.add_argument('--batch',action='append');b.add_argument('--freeze',type=Path)
    a=p.parse_args()
    if a.command=='build':
        if not re.fullmatch('[A-Za-z0-9_]+',a.name):raise ValueError('Fresh simple output name required')
        return build_report(a)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(EnergyTests))
    return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
