"""External, saved-evidence-only M19 paper tables; no raw/annual/simulation reads.

Use ``self-test --label FRESH`` for small synthetic layout and boundary checks.
Final ``build`` requires an explicit summary, final freeze and final cohort names.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import copy
import csv
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import unittest

SCRIPT=Path(__file__).resolve()
RUN=SCRIPT.parents[1]
MODEL='M19_model_closeout'
POLICIES={'E':'endurance_restore_p0_ready_v14r6','K1':'readiness_handoff_K1_v15',
          'C19':'C19_critical_reserve_v19r1'}
CLASSES=('L1','L2','L3','L4')

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,
        allow_nan=False).encode('utf8')).hexdigest()

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)

def synthetic_case(scenario_id,canonical,policy,*,group='main',eligible=True,metric_missing=False,
                   panel='same-main-panel',background='background0',duration=600.):
    metrics={f'P{p}_{c}_ENS_kWh':float(p+1) for p in (0,1) for c in ('e','h','c')}
    metrics.update({f'P{p}_{c}_material_minutes':3. for p in (0,1) for c in ('e','h','c')})
    metrics.update(P0_material_union_minutes=4.,P1_material_union_minutes=5.,actual_energy_fee_USD=12.)
    if metric_missing:metrics['P0_e_ENS_kWh']=None
    return dict(group=group,scenario_id=scenario_id,canonical_class=canonical,policy=policy,
        eligible=eligible,reasons=[] if eligible else ['synthetic_unknown'],metrics=metrics,
        window='registered_full' if group=='normal' else 'full',window_start_minute=0.,window_end_minute=duration,
        background_id=background,source_binding_sha256=digest('source'),model_config_sha256=digest('config'),
        policy_panel_sha256=digest(panel),result_sha256=digest([scenario_id,policy]),
        emissions={'factor_main':dict(grid_plus_gas_CO2_kg=8.,DG_CO2_kg_conditional=2.,
            total_CO2_kg_conditional=10.,DG_exact_CO2_unknown=True,emissions_status='conditional_DG_scenario',
            emissions_factor_sha256=digest('factor_main'))})

def synthetic_panel(*,group='main',per_class=2,backgrounds=1):
    rows=[]
    for cls in CLASSES:
        for i in range(per_class):
            for policy in POLICIES:
                rows.append(synthetic_case(cls+'_'+str(i),cls,policy,group=group,
                    background='background'+str(i%backgrounds)))
    return rows

class PaperTests(unittest.TestCase):
    root=None
    def setUp(self):
        self.path=self.root/self._testMethodName;self.path.mkdir()
    def test_main_carriers_and_union_are_separate(self):
        report=make_tables(synthetic_panel(),main_per_class=2,confirmation_per_class=1,
            confirmation_backgrounds=1,factors={'factor_main':digest('factor_main')})
        carrier=next(x for x in report['MAIN_SERVICE_BY_CARRIER'] if x['canonical_class']=='L1' and x['policy']=='E' and x['priority']=='P0' and x['carrier']=='e')
        union=next(x for x in report['MAIN_PRIORITY_UNION'] if x['canonical_class']=='L1' and x['policy']=='E' and x['priority']=='P0')
        self.assertEqual(carrier['ENS_kWh'],2.)
        self.assertEqual(union['material_union_minutes'],8.)
        self.assertEqual(union['material_specifications'],2)
    def test_unknown_does_not_shrink_denominator_or_become_zero(self):
        rows=synthetic_panel();rows[0]['eligible']=False;rows[0]['reasons']=['fixture_missing']
        report=make_tables(rows,main_per_class=2,confirmation_per_class=1,
            confirmation_backgrounds=1,factors={'factor_main':digest('factor_main')})
        row=next(x for x in report['MAIN_SERVICE_BY_CARRIER'] if x['canonical_class']=='L1' and x['policy']=='E' and x['priority']=='P0' and x['carrier']=='e')
        self.assertEqual(row['target_specifications'],2);self.assertEqual(row['unknown_specifications'],1)
        self.assertIsNone(row['ENS_kWh']);self.assertEqual(row['ENS_kWh_qualified_known_sum'],1.)
    def test_missing_metric_propagates_unknown(self):
        rows=synthetic_panel();rows[0]['metrics']['P0_e_ENS_kWh']=None
        report=make_tables(rows,main_per_class=2,confirmation_per_class=1,
            confirmation_backgrounds=1,factors={'factor_main':digest('factor_main')})
        row=next(x for x in report['MAIN_SERVICE_BY_CARRIER'] if x['canonical_class']=='L1' and x['policy']=='E' and x['priority']=='P0' and x['carrier']=='e')
        self.assertIsNone(row['ENS_kWh']);self.assertEqual(row['ENS_kWh_unknown_records'],1)
    def test_confirmation_mechanisms_and_backgrounds_are_not_equated(self):
        report=make_tables(synthetic_panel(group='confirmation',per_class=2,backgrounds=2),
            main_per_class=2,confirmation_per_class=2,confirmation_backgrounds=2,factors={'factor_main':digest('factor_main')})
        row=next(x for x in report['CONFIRMATION_PRIORITY_UNION'] if x['canonical_class']=='ALL' and x['policy']=='E' and x['priority']=='P0')
        self.assertEqual(row['target_specifications'],8)
        self.assertEqual(row['target_backgrounds'],2);self.assertEqual(row['registered_backgrounds'],2)
    def test_normal_windows_never_pool(self):
        rows=[synthetic_case('normal'+str(i),'N',p,group='normal',duration=d)
            for i,d in enumerate((1440.,2880.,4320.,1440.)) for p in POLICIES]
        report=make_tables(rows,main_per_class=2,confirmation_per_class=1,
            confirmation_backgrounds=1,factors={'factor_main':digest('factor_main')})
        own=report['NORMAL_CASE_COST_EMISSIONS']
        self.assertEqual(len(own),12);self.assertEqual({x['scenario_id'] for x in own},{'normal0','normal1','normal2','normal3'})
        self.assertFalse(any(x.get('scenario_id')=='ALL' for x in own))
        self.assertTrue(all(x['annualized'] is False for x in own))
    def test_normal_missing_cross_panel_proof_blocks_pairing(self):
        proof={'proofs':[]}
        own=[dict(synthetic_case('normal','N',p,group='normal'),
            initial_state_sha256=digest('reset'),applied_exogenous_definition_sha256=digest('exog'),
            physical_scene_sha256=digest('scene'),input_inventory_sha256=digest('inputs')) for p in POLICIES]
        checked=check_normal_proof(own,proof)
        self.assertTrue(all(not x['eligible'] for x in checked))
        self.assertIn('cross_panel_proof',str(checked))
    def test_development_or_retry_batch_names_are_not_selected(self):
        self.assertTrue(cohort_name('main_final_r1_001','main_final_r1'))
        self.assertFalse(cohort_name('main_final_r1_retry_001','main_final_r1'))
        self.assertFalse(cohort_name('dev_main_final_r1_001','main_final_r1'))
    def test_expected_missing_slots_remain_unknown(self):
        report=make_tables([],main_per_class=20,confirmation_per_class=8,
            confirmation_backgrounds=8,factors={'factor_main':digest('factor_main')})
        row=next(x for x in report['MAIN_PRIORITY_UNION'] if x['canonical_class']=='L1' and x['policy']=='E' and x['priority']=='P0')
        self.assertEqual(row['target_specifications'],20);self.assertEqual(row['unknown_specifications'],20)
        self.assertIsNone(row['material_union_minutes'])
    def test_DG_exact_is_unknown_even_when_conditional_total_known(self):
        report=make_tables(synthetic_panel(),main_per_class=2,confirmation_per_class=1,
            confirmation_backgrounds=1,factors={'factor_main':digest('factor_main')})
        row=next(x for x in report['MAIN_COST_EMISSIONS'] if x['canonical_class']=='L1' and x['policy']=='E')
        self.assertEqual(row['grid_plus_gas_CO2_kg'],16.)
        self.assertEqual(row['total_CO2_kg_conditional'],20.)
        self.assertIs(row['DG_exact_CO2_unknown'],True)
    def test_duplicate_case_policy_cannot_double_count(self):
        rows=synthetic_panel();rows.append(copy.deepcopy(rows[0]))
        with self.assertRaisesRegex(ValueError,'duplicate'):
            make_tables(rows,main_per_class=2,confirmation_per_class=1,confirmation_backgrounds=1,
                factors={'factor_main':digest('factor_main')})
    def test_summary_and_compact_IO_use_bound_final_values(self):
        frozen,summary=io_fixture(self.path)
        rows,receipt=load_final_evidence(summary,self.path/'FINAL.json',main_prefix='main_final_r1',
            confirmation_prefix='confirmation_final_r1',normal_batches={},normal_proof_path=None,run=self.path)
        self.assertEqual(len(rows),3);self.assertTrue(all(r['eligible'] for r in rows))
        self.assertEqual(receipt['raw_files_opened'],0);self.assertEqual(receipt['annual_inputs_loaded'],0)
        self.assertEqual(rows[0]['metrics']['P0_e_ENS_kWh'],1.)
    def test_changed_summary_table_is_rejected(self):
        frozen,summary=io_fixture(self.path)
        with (summary/'RESULT_WINDOWS.csv').open('a',encoding='utf8') as stream:stream.write('\nchanged')
        with self.assertRaisesRegex(ValueError,'SHA|hash'):
            load_final_evidence(summary,self.path/'FINAL.json',main_prefix='main_final_r1',
                confirmation_prefix='confirmation_final_r1',normal_batches={},normal_proof_path=None,run=self.path)
    def test_missing_summary_row_is_unknown_not_compact_fallback(self):
        frozen,summary=io_fixture(self.path,missing_summary_row=True)
        rows,receipt=load_final_evidence(summary,self.path/'FINAL.json',main_prefix='main_final_r1',
            confirmation_prefix='confirmation_final_r1',normal_batches={},normal_proof_path=None,run=self.path)
        self.assertTrue(all(not r['eligible'] for r in rows))
        self.assertIn('summary_row',str(rows))
    def test_output_layout_and_SHA_are_verifiable(self):
        report=make_tables(synthetic_panel(),main_per_class=2,confirmation_per_class=1,
            confirmation_backgrounds=1,factors={'factor_main':digest('factor_main')})
        save_tables(self.path/'layout',report,dict(synthetic_only=True,main_specifications=8,
            confirmation_specifications=4,confirmation_backgrounds=1,raw_files_opened=0,annual_inputs_loaded=0))
        receipt=json.loads((self.path/'layout'/'PAPER_RESULTS_RECEIPT.json').read_text(encoding='utf8'))
        self.assertEqual(receipt['output_sha256']['MAIN_PRIORITY_UNION.csv'],sha(self.path/'layout'/'MAIN_PRIORITY_UNION.csv'))
        self.assertIn('SYNTHETIC',(self.path/'layout'/'README.md').read_text(encoding='utf8'))
    def test_mixed_panels_cannot_form_one_class_total(self):
        rows=synthetic_panel();rows[0]['policy_panel_sha256']=digest('different-panel')
        report=make_tables(rows,main_per_class=2,confirmation_per_class=1,
            confirmation_backgrounds=1,factors={'factor_main':digest('factor_main')})
        row=next(r for r in report['MAIN_PRIORITY_UNION'] if r['canonical_class']=='L1' and r['policy']=='E' and r['priority']=='P0')
        self.assertIsNone(row['material_union_minutes']);self.assertFalse(row['binding_scope_valid'])
        coverage=next(r for r in report['COHORT_COVERAGE'] if r['group']=='main' and r['policy']=='E')
        self.assertEqual(coverage['qualified_complete_specifications'],0)
    def test_valid_normal_proof_qualifies_different_single_policy_panels(self):
        required=('source_binding_sha256','model_config_sha256','input_inventory_sha256','physical_scene_sha256',
            'initial_state_sha256','applied_exogenous_definition_sha256','window_start_minute','window_end_minute')
        own=[dict(synthetic_case('normal','N',p,group='normal',panel=p),
            initial_state_sha256=digest('reset'),applied_exogenous_definition_sha256=digest('exog'),
            physical_scene_sha256=digest('scene'),input_inventory_sha256=digest('inputs')) for p in POLICIES]
        by={r['policy']:r for r in own};proof={'proofs':[]}
        for left,right in (('C19','E'),('C19','K1')):
            a,b=by[left],by[right]
            proof['proofs'].append(dict(scenario_id='normal',window='registered_full',left_policy=left,right_policy=right,
                status='matched',left_candidate_result_sha256=[a['result_sha256']],right_candidate_result_sha256=[b['result_sha256']],
                left_policy_panel_sha256=a['policy_panel_sha256'],right_policy_panel_sha256=b['policy_panel_sha256'],
                left_match_values={k:a[k] for k in required},right_match_values={k:b[k] for k in required}))
        self.assertTrue(all(r['eligible'] for r in check_normal_proof(own,proof)))
        proof['proofs'][0]['right_match_values']['initial_state_sha256']=digest('other-reset')
        self.assertTrue(all(not r['eligible'] for r in check_normal_proof(own,proof)))
    def test_IO_does_not_open_dummy_raw(self):
        from unittest.mock import patch
        io_fixture(self.path)
        original=Path.read_bytes
        def guarded(path):
            if path.name=='trajectory.json.gz':raise AssertionError('raw read forbidden')
            return original(path)
        with patch.object(Path,'read_bytes',guarded):
            rows,_=load_final_evidence(self.path/'summary_synthetic',self.path/'FINAL.json',main_prefix='main_final_r1',
                confirmation_prefix='confirmation_final_r1',normal_batches={},normal_proof_path=None,run=self.path)
        self.assertEqual(len(rows),3)
    def test_twelve_single_policy_normal_batches_are_selected_and_fully_qualified(self):
        summary,normal,proof=normal_io_fixture(self.path,split=True)
        rows,receipt=load_final_evidence(summary,self.path/'FINAL.json',main_prefix='main_final_r1',
            confirmation_prefix='confirmation_final_r1',normal_batches=normal,normal_proof_path=proof,run=self.path)
        own=[r for r in rows if r['group']=='normal']
        self.assertEqual(len(own),12)
        self.assertTrue(all(r['eligible'] for r in own),str([(r['batch'],r['reasons']) for r in own]))
        self.assertEqual(Counter(r['policy'] for r in own),Counter(dict.fromkeys(POLICIES,4)))
        self.assertEqual({r['window_end_minute']/60 for r in own},{24.,48.,72.})
        report=make_tables(rows,factors=receipt['factors'])
        coverage=[r for r in report['COHORT_COVERAGE'] if r['group']=='normal']
        self.assertTrue(all(r['target_specifications']==4 and r['qualified_complete_specifications']==4 for r in coverage))
        self.assertTrue(all(r['actual_energy_fee_USD'] is not None for r in report['NORMAL_CASE_COST_EMISSIONS']))
    def test_original_three_multi_scene_normal_batches_remain_legal(self):
        summary,normal,proof=normal_io_fixture(self.path,split=False)
        rows,_=load_final_evidence(summary,self.path/'FINAL.json',main_prefix='main_final_r1',
            confirmation_prefix='confirmation_final_r1',normal_batches=normal,normal_proof_path=proof,run=self.path)
        own=[r for r in rows if r['group']=='normal']
        self.assertEqual(len(own),12);self.assertTrue(all(r['eligible'] for r in own))
        self.assertEqual(len({r['batch'] for r in own}),3)

def compatibility_module():
    path=SCRIPT.with_name('summarize_compatibility.py')
    spec=importlib.util.spec_from_file_location('v19_paper_external_compatibility',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def io_fixture(root,*,missing_summary_row=False):
    compat=compatibility_module()
    directory=root/'experiments/main_final_r1_000'
    frozen=compat.fixture(directory,policies=('E','K1','C19'),scene_change={
        'canonical_class':'L1','root_family_id':'synthetic_background','v19_seen_regression':True,
        'core_four_class_denominator':True})
    factors={'factor_main':digest('factor_main')}
    frozen['evaluation_contract']={'emissions':dict(registry_sha256=digest('registry'),main_case_id='factor_main',case_canonical_sha256=factors)}
    frozen.pop('signature');frozen['signature']=digest(frozen);write_json(root/'FINAL.json',frozen)
    inputs=[]
    candidates=compat.collect_batch(directory,frozen,{'fixture':'fixed_scene'},inputs)
    result=[];emissions=[]
    for candidate in candidates:
        if candidate['window']!='full':continue
        alias=next(p for p,v in POLICIES.items() if v==candidate['policy_id'])
        sample=synthetic_case('fixed_scene','L1',alias)
        row=dict(sample['metrics'],batch=directory.name,scenario_id='fixed_scene',policy_id=candidate['policy_id'],
            model_id=MODEL,window='full',window_start_minute=0.,window_end_minute=600.,complete=True,
            registered_full_known=True,comparison_matching_eligible=True,panel_complete_intersection=True,
            comparison_duplicate_in_cohort=False,episode_audit_status='passed',status='completed',analysis_error=False,
            source_binding_sha256=candidate['source_binding_sha256'],model_config_sha256=candidate['model_config_sha256'],
            policy_panel_sha256=candidate['policy_panel_sha256'],result_sha256=candidate['result_sha256'],
            registered_scene_sha256=candidate['full_registered_scene_sha256'])
        result.append(row)
        emissions.append(dict(row,emissions_factor_case_id='factor_main',
            emissions_value_scope='registered_window',**sample['emissions']['factor_main']))
    if missing_summary_row:result.pop(0)
    summary=root/'summary_synthetic';summary.mkdir()
    csv_write(summary/'RESULT_WINDOWS.csv',result);csv_write(summary/'EMISSIONS.csv',emissions)
    write_json(summary/'SUMMARY.json',dict(execution_model_id=MODEL,emissions_registry_sha256=digest('registry')))
    write_json(summary/'SUMMARY_RECEIPT.json',dict(output_sha256={p.name:sha(p) for p in summary.iterdir()},
        input_files=inputs,emissions_registry_sha256=digest('registry')))
    return frozen,summary

def normal_io_fixture(root,*,split):
    frozen,summary=io_fixture(root);compat=compatibility_module();directories=[]
    normal={p:'normal_'+p+'_final_r1' for p in POLICIES}
    factors=frozen['evaluation_contract']['emissions']['case_canonical_sha256']
    for policy,prefix in normal.items():
        parts=[]
        for index,hours in enumerate((24.,48.,72.,24.)):
            name=prefix+'_'+str(index).zfill(3)
            path=root/('experiments' if split else 'normal_fixture_sources')/name
            compat.fixture(path,policies=(policy,),scene_change={'id':'normal_'+str(index),
                'duration_steps':int(hours*4),'canonical_class':'N','root_family_id':'normal_background_'+str(index)})
            result_path=next((path/'pairs').glob('*/RESULT.json'))
            record=json.loads(result_path.read_text(encoding='utf8'));analysis=record['analysis'];end=hours*60.
            analysis['requested_end_minute']=end;analysis['observed_until_minute']=end
            analysis['v18']['windows']['registered_full']['window_minutes']=[0.,end]
            analysis['windows']['registered_full']['window_minutes']=[0.,end]
            result_path.write_text(json.dumps(record,ensure_ascii=False,allow_nan=False),encoding='utf8')
            parts.append(path)
        if split:directories.extend(parts);continue
        # The old layout has four scenes in one single-policy batch. Preserve it.
        target=root/'experiments'/prefix;target.mkdir()
        manifests=[json.loads((p/'manifest.json').read_text(encoding='utf8')) for p in parts]
        manifest=copy.deepcopy(manifests[0]);manifest['scenarios']=[m['scenarios'][0] for m in manifests]
        manifest.pop('signature');manifest['signature']=digest(manifest);write_json(target/'manifest.json',manifest)
        plan=json.loads((parts[0]/'plan.json').read_text(encoding='utf8'))
        plan.update(scenario_ids=[s['id'] for s in manifest['scenarios']],registered_pairs=4,
            manifest_sha256=sha(target/'manifest.json'),manifest_signature=manifest['signature'])
        write_json(target/'plan.json',plan)
        terminal=json.loads((parts[0]/'BATCH_RESULT.json').read_text(encoding='utf8'))
        terminal.update(plan_sha256=sha(target/'plan.json'),manifest_sha256=sha(target/'manifest.json'),
            pair_index=[],matching_evidence={})
        for part in parts:
            record=json.loads(next((part/'pairs').glob('*/RESULT.json')).read_text(encoding='utf8'))
            record['manifest_signature']=manifest['signature']
            write_json(target/'pairs'/(record['scenario_id']+'__'+record['policy_id'])/'RESULT.json',record)
            terminal['pair_index'].append({k:v for k,v in record.items() if k!='analysis'})
            terminal['matching_evidence'].update(json.loads((part/'BATCH_RESULT.json').read_text(encoding='utf8'))['matching_evidence'])
        write_json(target/'BATCH_RESULT.json',terminal);directories.append(target)
    inputs=[];flat=read_csv(summary/'RESULT_WINDOWS.csv',[]);emissions=read_csv(summary/'EMISSIONS.csv',[])
    original_receipt=json.loads((summary/'SUMMARY_RECEIPT.json').read_text(encoding='utf8'))
    for directory in directories:
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf8'))
        candidates=compat.collect_batch(directory,frozen,{s['id']:s['id'] for s in manifest['scenarios']},inputs)
        for candidate in candidates:
            alias=next(p for p,v in POLICIES.items() if v==candidate['policy_id'])
            sample=synthetic_case(candidate['scenario_id'],'N',alias,group='normal')
            row=dict(sample['metrics'],batch=directory.name,scenario_id=candidate['scenario_id'],policy_id=candidate['policy_id'],
                model_id=MODEL,window=candidate['window'],window_start_minute=candidate['window_start_minute'],
                window_end_minute=candidate['window_end_minute'],complete=True,registered_full_known=True,
                comparison_matching_eligible=True,panel_complete_intersection=True,comparison_duplicate_in_cohort=False,
                episode_audit_status='passed',status='completed',analysis_error=False,
                source_binding_sha256=candidate['source_binding_sha256'],model_config_sha256=candidate['model_config_sha256'],
                policy_panel_sha256=candidate['policy_panel_sha256'],result_sha256=candidate['result_sha256'],
                registered_scene_sha256=candidate['full_registered_scene_sha256'])
            flat.append(row)
            emissions.append(dict(row,emissions_factor_case_id='factor_main',emissions_value_scope='registered_window',
                **sample['emissions']['factor_main']))
    for filename,table in (('RESULT_WINDOWS.csv',flat),('EMISSIONS.csv',emissions)):
        path=summary/filename;path.unlink();csv_write(path,table)
    original_receipt['input_files'].extend(inputs)
    original_receipt['output_sha256']={p.name:sha(p) for p in summary.iterdir() if p.name!='SUMMARY_RECEIPT.json'}
    (summary/'SUMMARY_RECEIPT.json').write_text(json.dumps(original_receipt,ensure_ascii=False,allow_nan=False),encoding='utf8')
    report=compat.compare_batches(frozen,directories,{str(i):'normal_'+str(i) for i in range(4)},
        [('C19','E'),('C19','K1'),('E','K1')])
    proof=root/'normal_proof';compat.save_report(proof,report,root/'FINAL.json')
    return summary,normal,proof

def csv_write(path,rows):
    fields=sorted({k for r in rows for k in r})
    with Path(path).open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False,allow_nan=False) if isinstance(v,(dict,list,Counter)) else v for k,v in row.items()})

def load_final_evidence(summary_path,freeze_path,*,main_prefix,confirmation_prefix,normal_batches,normal_proof_path,run=RUN):
    run=Path(run).resolve();summary_path=Path(summary_path).resolve()
    if summary_path.name=='SUMMARY.json':summary_path=summary_path.parent
    provenance=[]
    def read(path):
        path=Path(path)
        if path.suffix.lower() not in ('.json','.csv'):raise ValueError('Only saved JSON/CSV evidence is allowed')
        content=path.read_bytes();provenance.append(dict(path=str(path),sha256=hashlib.sha256(content).hexdigest(),bytes=len(content)))
        return json.loads(content)
    frozen=read(freeze_path);compat=compatibility_module()
    compat.compare_batches(frozen,[],{},[])
    contract=frozen.get('evaluation_contract',{}).get('emissions',{})
    factors=contract.get('case_canonical_sha256')
    if not factors or contract.get('main_case_id') not in factors:raise ValueError('Final freeze lacks emissions factor contract')
    meta=read(summary_path/'SUMMARY.json');summary_receipt=read(summary_path/'SUMMARY_RECEIPT.json')
    if (meta.get('execution_model_id')!=MODEL
            or meta.get('emissions_registry_sha256')!=contract.get('registry_sha256')
            or summary_receipt.get('emissions_registry_sha256')!=contract.get('registry_sha256')):
        raise ValueError('Summary model or emissions registry differs from final freeze')
    for name in ('SUMMARY.json','RESULT_WINDOWS.csv','EMISSIONS.csv'):
        if summary_receipt.get('output_sha256',{}).get(name)!=sha(summary_path/name):
            raise ValueError('Saved summary table SHA mismatch: '+name)
    flat=read_csv(summary_path/'RESULT_WINDOWS.csv',provenance)
    emission_rows=read_csv(summary_path/'EMISSIONS.csv',provenance)
    flat_index=defaultdict(list);em_index=defaultdict(list)
    for row in flat:flat_index[row_key(row)].append(row)
    for row in emission_rows:em_index[(*row_key(row),row.get('emissions_factor_case_id'))].append(row)
    recorded_inputs={canonical_saved_path(x['path'],run):x['sha256']
        for x in summary_receipt.get('input_files',[]) if x.get('path') and x.get('sha256')}
    selected=[];excluded=[]
    for directory in sorted((run/'experiments').glob('*')):
        if not directory.is_dir():continue
        group=('main' if cohort_name(directory.name,main_prefix) else
            'confirmation' if cohort_name(directory.name,confirmation_prefix) else
            'normal' if any(cohort_name(directory.name,prefix) for prefix in normal_batches.values()) else None)
        if group:selected.append((group,directory))
        else:excluded.append(dict(batch=directory.name,reason='outside_explicit_final_cohorts; no results read'))
    rows=[];batch_issues=[];registration=[]
    for group,directory in selected:
        try:
            manifest=read(directory/'manifest.json');plan=read(directory/'plan.json')
            cases={s['id']:s['id'] for s in manifest['scenarios']}
            inputs=[];candidates=compat.collect_batch(directory,frozen,cases,inputs)
        except Exception as exc:
            batch_issues.append(dict(batch=directory.name,group=group,type=type(exc).__name__,message=str(exc),
                outcome='unknown; fixed target slots retained'))
            continue
        provenance.extend(inputs)
        binding_reasons=[]
        if any(recorded_inputs.get(canonical_saved_path(p['path'],run))!=p['sha256'] for p in inputs):
            binding_reasons.append('canonical_input_not_bound_by_selected_summary_receipt')
        expected_policies=set(POLICIES.values()) if group!='normal' else {
            POLICIES[p] for p,prefix in normal_batches.items() if cohort_name(directory.name,prefix)}
        if {s.get('policy_id') for s in plan.get('policy_specs',[])}!=expected_policies:
            binding_reasons.append('registered_policy_panel_differs_from_target')
        role=manifest.get('evaluation_role')
        if role!=('independent_confirmation' if group=='confirmation' else 'seen_regression'):
            binding_reasons.append('wrong_final_evaluation_role:'+str(role))
        if group=='confirmation':
            if (manifest.get('confirmation_background_count')!=8
                    or manifest.get('new_independent_background_count')!=8
                    or not isinstance(manifest.get('confirmation_isolation_registration'),dict)):
                binding_reasons.append('confirmation_eight_background_registration_or_isolation_missing')
        for scene in manifest['scenarios']:
            registration.append(dict(group=group,batch=directory.name,scenario_id=scene['id'],
                canonical_class=scene.get('canonical_class'),background_id=scene.get('root_family_id'),
                evaluation_role=role))
        for candidate in candidates:
            if candidate['window']!=('registered_full' if group=='normal' else 'full'):continue
            aliases=[p for p,v in POLICIES.items() if v==candidate['policy_id']]
            if len(aliases)!=1:
                batch_issues.append(dict(batch=directory.name,policy_id=candidate['policy_id'],reason='unexpected policy retained in canonical evidence, excluded from fixed three-policy paper slots'))
                continue
            reasons=list(candidate['reasons'])+binding_reasons
            scene=candidate['scene'];canonical=scene.get('canonical_class')
            if group!='normal' and (canonical not in CLASSES or scene.get('core_four_class_denominator') is not True):
                reasons.append('not_a_registered_canonical_four_class_specification')
            if group=='confirmation' and (scene.get('new_independent_background') is not True
                    or scene.get('requires_policy_freeze') is not True):
                reasons.append('confirmation_scene_not_independently_registered_after_freeze')
            if group!='normal' and (candidate['window_start_minute'],candidate['window_end_minute'])!=(0.,600.):
                reasons.append('not_exact_common600_window')
            key=(directory.name,candidate['scenario_id'],candidate['policy_id'],candidate['window'],
                candidate['window_start_minute'],candidate['window_end_minute'])
            matched=flat_index.get(key,[])
            if len(matched)!=1:reasons.append('summary_row_missing_or_duplicated')
            saved=matched[0] if len(matched)==1 else {}
            for field in ('source_binding_sha256','model_config_sha256','policy_panel_sha256','result_sha256'):
                if saved.get(field)!=candidate.get(field):reasons.append('summary_row_binding_mismatch:'+field)
            if saved.get('registered_scene_sha256')!=candidate['full_registered_scene_sha256']:
                reasons.append('summary_row_scene_binding_mismatch')
            if (saved.get('model_id')!=MODEL or saved.get('status')!='completed'
                    or any(saved.get(k) is not True for k in ('complete','registered_full_known','comparison_matching_eligible','panel_complete_intersection'))
                    or saved.get('comparison_duplicate_in_cohort') is not False
                    or saved.get('analysis_error') is not False or saved.get('episode_audit_status')!='passed'):
                reasons.append('summary_row_not_complete_audited_actual_matching_intersection')
            emissions={};emission_issues=[]
            for factor_id,factor_sha in factors.items():
                own=em_index.get((*key,factor_id),[])
                if len(own)!=1:
                    emission_issues.append(factor_id+':missing_or_duplicate');continue
                item=own[0]
                if (item.get('emissions_factor_sha256')!=factor_sha
                        or item.get('emissions_value_scope')!='registered_window'
                        or any(item.get(k)!=candidate.get(k) for k in ('source_binding_sha256','model_config_sha256','policy_panel_sha256','result_sha256'))
                        or item.get('DG_exact_CO2_unknown') is not True):
                    emission_issues.append(factor_id+':binding_or_scope_invalid');continue
                emissions[factor_id]=item
            rows.append(dict(group=group,scenario_id=candidate['scenario_id'],canonical_class=canonical,
                policy=aliases[0],batch=directory.name,background_id=scene.get('root_family_id'),
                window=candidate['window'],window_start_minute=candidate['window_start_minute'],window_end_minute=candidate['window_end_minute'],
                eligible=not reasons,reasons=sorted(set(reasons)),metrics=saved,emissions=emissions,
                emissions_unknown_reasons=emission_issues,evaluation_role=role,
                result_path=candidate['result_path'],result_sha256=candidate['result_sha256'],
                status=candidate['status'],episode_audit_status=candidate['episode_audit_status'],
                source_binding_sha256=candidate['source_binding_sha256'],model_config_sha256=candidate['model_config_sha256'],
                policy_panel_sha256=candidate['policy_panel_sha256'],input_inventory_sha256=candidate['input_inventory_sha256'],
                physical_scene_sha256=candidate['physical_scene_sha256'],
                initial_state_sha256=candidate['initial_state_sha256'],
                applied_exogenous_definition_sha256=candidate['applied_exogenous_definition_sha256']))
    # Every main/confirmation case uses the full registered three-policy panel,
    # including audit pass and exact actual matching; no per-policy survivor total.
    panel_groups=defaultdict(list)
    for row in rows:
        if row['group']!='normal':panel_groups[(row['group'],row['scenario_id'])].append(row)
    match_fields=('source_binding_sha256','model_config_sha256','policy_panel_sha256','input_inventory_sha256',
        'physical_scene_sha256','initial_state_sha256','applied_exogenous_definition_sha256','window_start_minute','window_end_minute')
    for own in panel_groups.values():
        if (Counter(r['policy'] for r in own)!=Counter(dict.fromkeys(POLICIES,1))
                or any(not r['eligible'] for r in own)
                or any(len({r.get(field) for r in own})!=1 or own[0].get(field) is None for field in match_fields)):
            for row in own:row['eligible']=False;row['reasons'].append('full_final_three_policy_intersection_not_qualified')
    normal=[r for r in rows if r['group']=='normal']
    proof={}
    if normal_proof_path is not None:
        proof_path=Path(normal_proof_path)
        if proof_path.is_dir():proof_path=proof_path/'MATCH_PROOFS.json'
        proof_summary=read(proof_path.parent/'SUMMARY.json')
        if (proof_summary.get('freeze_signature')!=frozen['signature']
                or proof_summary.get('script_sha256')!=sha(SCRIPT.with_name('summarize_compatibility.py'))
                or proof_summary.get('output_sha256',{}).get('MATCH_PROOFS.json')!=sha(proof_path)):
            raise ValueError('Normal cross-panel proof SHA/final freeze/program binding invalid')
        proof=read(proof_path)
    checked=check_normal_proof(normal,proof)
    rows=[r for r in rows if r['group']!='normal']+checked
    receipt=dict(schema='hospital_ies.v19.paper_results.external.v1',execution_model_id=MODEL,
        synthetic_only=False,summary_path=str(summary_path),freeze_path=str(Path(freeze_path).resolve()),
        freeze_signature=frozen['signature'],final_source_binding_sha256=digest(frozen['source_sha256']),
        final_model_config_sha256=digest(frozen['model_config']),factors=factors,main_factor_case_id=contract['main_case_id'],
        emissions_registry_sha256=contract['registry_sha256'],
        main_prefix=main_prefix,confirmation_prefix=confirmation_prefix,normal_batches=normal_batches,
        normal_batch_argument_semantics='Per-policy cohort prefix: exact batch or prefix plus underscore and digits; original multi-scene single batch remains supported.',
        normal_cross_panel_proof_path=str(normal_proof_path) if normal_proof_path else None,
        main_specifications=80,main_specifications_per_class=20,confirmation_specifications=32,
        confirmation_specifications_per_class=8,confirmation_backgrounds=8,
        normal_specifications_target=4,normal_registered_specifications=len({r['scenario_id'] for r in checked}),
        registrations=registration,batch_read_issues=batch_issues,excluded_batches=excluded,
        main_three_policy_denominator_modified=False,unknowns_never_removed_from_target_denominators=True,
        raw_files_opened=0,annual_inputs_loaded=0,physical_episodes_started=0,
        input_files=provenance,external_program_sha256=sha(SCRIPT),
        compatibility_program_sha256=sha(SCRIPT.with_name('summarize_compatibility.py')))
    return rows,receipt

def canonical_saved_path(path,run):
    text=str(path).replace('\\','/')
    marker='outputs/v19_model_closeout_20261018_run01/'
    if marker in text:return str((Path(run)/text.split(marker,1)[1]).resolve())
    return str(Path(path).resolve())

def row_key(row):
    return tuple(row.get(k) for k in ('batch','scenario_id','policy_id','window','window_start_minute','window_end_minute'))

def read_csv(path,provenance):
    path=Path(path)
    provenance.append(dict(path=str(path),sha256=sha(path),bytes=path.stat().st_size))
    def parse(key,value):
        if value=='':return None
        if key.endswith('_sha256'):return value
        if value in ('True','true'):return True
        if value in ('False','false'):return False
        if value in ('None','null'):return None
        if value.startswith(('[','{')):
            try:return json.loads(value)
            except ValueError:return value
        if re.fullmatch(r'-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?',value):
            return float(value) if any(c in value for c in '.eE') else int(value)
        return value
    with path.open(encoding='utf-8-sig',newline='') as stream:
        return [{k:parse(k,v) for k,v in row.items()} for row in csv.DictReader(stream)]

def save_tables(out,tables,receipt):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    for name,rows in tables.items():csv_write(out/(name+'.csv'),rows)
    synthetic=receipt.get('synthetic_only') is True
    text=('SYNTHETIC FIXTURE LAYOUT ONLY — no actual V19 results.\n\n' if synthetic else
        'M19 final saved-evidence paper tables.\n\n')
    text+=('Main: '+str(receipt['main_specifications'])+' seen mechanism specifications, three matched policies; four canonical classes. '
        'Independent confirmation: '+str(receipt['confirmation_specifications'])+' mechanism specifications from '
        +str(receipt['confirmation_backgrounds'])+' backgrounds; mechanism rows are not independent background counts.\n\n'
        'P0/P1 carrier ENS and carrier material durations are separate from the per-priority union of material interruption intervals. '
        'Material specification counts use the inherited positive material-duration convention. Unknowns retain the full target denominator; '
        'a full-group total is blank unless every target component is qualified and known. Qualified known subtotals are explicitly labelled.\n\n'
        'Normal cases retain each actual registered 24/48/72-hour window separately. Costs and emissions are never annualized or pooled across windows. '
        'Their paired values require the external actual-reset/exogenous cross-panel proof. Every source/config/panel/role/window, '
        'episode audit and compact-to-summary binding is checked. A normal per-policy cohort prefix accepts its exact batch name or a numeric per-scene suffix. '
        'Development and retry cohorts are excluded; three policies and four normal cases retain their fixed denominators.\n\n'
        'Emissions contain grid plus gas CO2 and conditional DG/total CO2 for the source-bound factor cases. '
        'DG exact chemical emissions remain unknown. These are ex-post estimates, not a dispatch objective or complete lifecycle CO2e.\n\n'
        'All policies share the M19 model repairs. C19-specific reserve effects cannot be credited with all common model repairs. '
        'Long-energy trade-offs are reported separately. No simulation, raw trajectory, annual load or sealed final12 data is opened here.\n')
    (out/'README.md').write_text(text,encoding='utf8')
    receipt=dict(receipt,created_utc=datetime.now(timezone.utc).isoformat(),
        output_row_counts={k:len(v) for k,v in tables.items()},
        output_sha256={p.name:sha(p) for p in out.iterdir() if p.is_file()})
    write_json(out/'PAPER_RESULTS_RECEIPT.json',receipt)
    return receipt

def make_tables(rows,*,main_per_class=20,confirmation_per_class=8,confirmation_backgrounds=8,factors):
    seen=set()
    for row in rows:
        key=tuple(row.get(k) for k in ('group','scenario_id','policy','window'))
        if key in seen:raise ValueError('duplicate registered case/policy/window; split final cohorts')
        seen.add(key)
    tables={name:[] for name in ('MAIN_SERVICE_BY_CARRIER','MAIN_PRIORITY_UNION','MAIN_COST_EMISSIONS',
        'CONFIRMATION_SERVICE_BY_CARRIER','CONFIRMATION_PRIORITY_UNION','CONFIRMATION_COST_EMISSIONS',
        'CONFIRMATION_BACKGROUND_COVERAGE','NORMAL_CASE_COST_EMISSIONS','NORMAL_PAIRED_DELTAS',
        'CASE_SERVICE','OUTCOME_STATUS','COHORT_COVERAGE')}
    for row in rows:
        tables['OUTCOME_STATUS'].append({k:v for k,v in row.items() if k not in ('metrics','emissions')})
        for priority in ('P0','P1'):
            for carrier in ('e','h','c'):
                tables['CASE_SERVICE'].append({**identity(row), 'priority':priority,'carrier':carrier,
                    'qualified_complete':row['eligible'],'unknown_reasons':row.get('reasons'),
                    'ENS_kWh':row['metrics'].get(priority+'_'+carrier+'_ENS_kWh') if row['eligible'] else None,
                    'material_minutes':row['metrics'].get(priority+'_'+carrier+'_material_minutes') if row['eligible'] else None,
                    'priority_material_union_minutes':row['metrics'].get(priority+'_material_union_minutes') if row['eligible'] else None})
    for group,target in (('main',main_per_class),('confirmation',confirmation_per_class)):
        group_rows=[r for r in rows if r['group']==group]
        for canonical in (*CLASSES,'ALL'):
            for policy in POLICIES:
                own=[r for r in group_rows if r['policy']==policy and (canonical=='ALL' or r['canonical_class']==canonical)]
                expected=target*(4 if canonical=='ALL' else 1)
                if len(own)>expected:raise ValueError('Registered final cohort exceeds fixed denominator: '+group+' '+canonical+' '+policy)
                binding_values={field:{r.get(field) for r in own if r.get(field) is not None}
                    for field in ('source_binding_sha256','model_config_sha256','policy_panel_sha256','input_inventory_sha256')}
                binding_valid=all(len(values)<=1 for values in binding_values.values())
                if not binding_valid:own=[dict(r,eligible=False) for r in own]
                qualified=[r for r in own if r['eligible']]
                base=dict(group=group,canonical_class=canonical,policy=policy,model_id=MODEL,
                    target_specifications=expected,registered_specifications=len(own),
                    qualified_complete_specifications=len(qualified),unknown_specifications=expected-len(qualified),
                    unregistered_target_slots=expected-len(own),window='full',window_start_minute=0.,window_end_minute=600.,
                    numerator_scope='qualified complete actual-matched full three-policy intersection',
                    denominator_scope='fixed registered target; unknowns never removed',binding_scope_valid=binding_valid,
                    **{field:next(iter(values)) if len(values)==1 else None for field,values in binding_values.items()})
                if group=='confirmation':
                    base.update(target_backgrounds=confirmation_backgrounds,
                        registered_backgrounds=len({r['background_id'] for r in group_rows if r.get('background_id')}),
                        mechanisms_per_background=4,statistical_iid_claim=False)
                for priority in ('P0','P1'):
                    union=dict(base,priority=priority)
                    union.update(metric_total(own,expected,priority+'_material_union_minutes','material_union_minutes'))
                    union.update(material_count(own,expected,priority+'_material_union_minutes'))
                    tables[group.upper()+'_PRIORITY_UNION'].append(union)
                    for carrier in ('e','h','c'):
                        item=dict(base,priority=priority,carrier=carrier)
                        item.update(metric_total(own,expected,priority+'_'+carrier+'_ENS_kWh','ENS_kWh'))
                        item.update(metric_total(own,expected,priority+'_'+carrier+'_material_minutes','material_minutes'))
                        item.update(material_count(own,expected,priority+'_'+carrier+'_material_minutes'))
                        tables[group.upper()+'_SERVICE_BY_CARRIER'].append(item)
                for factor_id,factor_sha in factors.items():
                    item=dict(base,emissions_factor_case_id=factor_id,emissions_factor_sha256=factor_sha,
                        DG_exact_CO2_unknown=True,chemical_DG_is_not_measured=True,emissions_is_dispatch_objective=False)
                    item.update(metric_total(own,expected,'actual_energy_fee_USD','actual_energy_fee_USD'))
                    for field in ('grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional'):
                        temp=[dict(r,metrics=r.get('emissions',{}).get(factor_id,{})) for r in own]
                        item.update(metric_total(temp,expected,field,field))
                    tables[group.upper()+'_COST_EMISSIONS'].append(item)
        if group=='confirmation':
            backgrounds=sorted({r['background_id'] for r in group_rows if r.get('background_id')})
            for background in backgrounds:
                for policy in POLICIES:
                    own=[r for r in group_rows if r.get('background_id')==background and r['policy']==policy]
                    classes=Counter(r['canonical_class'] for r in own)
                    qualified=sum(r['eligible'] for r in own)
                    tables['CONFIRMATION_BACKGROUND_COVERAGE'].append(dict(background_id=background,policy=policy,
                        target_backgrounds=confirmation_backgrounds,target_mechanisms_in_background=4,
                        registered_mechanisms=len(own),qualified_complete_mechanisms=qualified,
                        canonical_class_counts=classes,
                        background_all_four_complete=classes==Counter(dict.fromkeys(CLASSES,1)) and qualified==4))
    normal=[r for r in rows if r['group']=='normal']
    for group,target,backgrounds in (('main',main_per_class*4,None),
            ('confirmation',confirmation_per_class*4,confirmation_backgrounds),('normal',4,None)):
        for policy in POLICIES:
            own=[r for r in rows if r['group']==group and r['policy']==policy]
            known=sum(r['eligible'] for r in own)
            if group!='normal':
                scope=next(r for r in tables[group.upper()+'_PRIORITY_UNION']
                    if r['canonical_class']=='ALL' and r['policy']==policy and r['priority']=='P0')
                known=scope['qualified_complete_specifications']
            elif len(own)>target:
                raise ValueError('Normal registered cases exceed four separately registered windows')
            registered_backgrounds={r['background_id'] for r in own if r.get('background_id')}
            tables['COHORT_COVERAGE'].append(dict(group=group,policy=policy,
                target_specifications=target,registered_specifications=len(own),
                qualified_complete_specifications=known,unknown_specifications=target-known,
                target_backgrounds=backgrounds,
                registered_backgrounds=len(registered_backgrounds) if group=='confirmation' else None,
                expected_mechanisms_per_background=4 if group=='confirmation' else None,
                normal_windows_not_pooled=group=='normal'))
    for row in normal:
        base=dict(identity(row),target_specifications=1,qualified_complete_specifications=int(row['eligible']),
            unknown_specifications=int(not row['eligible']),paired_eligible=row['eligible'],
            unknown_reasons=row.get('reasons'),annualized=False,cross_window_total_permitted=False,
            actual_window_hours=(row['window_end_minute']-row['window_start_minute'])/60.)
        for factor_id,factor_sha in factors.items():
            item=dict(base,emissions_factor_case_id=factor_id,emissions_factor_sha256=factor_sha,
                DG_exact_CO2_unknown=True,emissions_is_dispatch_objective=False)
            item.update(metric_total([row],1,'actual_energy_fee_USD','actual_energy_fee_USD'))
            for field in ('grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional'):
                item.update(metric_total([dict(row,metrics=row.get('emissions',{}).get(factor_id,{}))],1,field,field))
            tables['NORMAL_CASE_COST_EMISSIONS'].append(item)
    for scene in sorted({r['scenario_id'] for r in normal}):
        members={r['policy']:r for r in normal if r['scenario_id']==scene}
        for left,right in (('C19','E'),('C19','K1'),('E','K1')):
            a,b=members.get(left),members.get(right)
            for factor_id in factors:
                common=dict(scenario_id=scene,left_policy=left,right_policy=right,
                    window='registered_full',window_start_minute=a.get('window_start_minute') if a else None,
                    window_end_minute=a.get('window_end_minute') if a else None,
                    paired_eligible=bool(a and b and a['eligible'] and b['eligible']),
                    emissions_factor_case_id=factor_id,delta_convention='right minus left',annualized=False)
                for field in ('actual_energy_fee_USD','grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional'):
                    x=(a.get('metrics',{}).get(field) if field.endswith('_USD') else a.get('emissions',{}).get(factor_id,{}).get(field)) if a else None
                    y=(b.get('metrics',{}).get(field) if field.endswith('_USD') else b.get('emissions',{}).get(factor_id,{}).get(field)) if b else None
                    tables['NORMAL_PAIRED_DELTAS'].append(dict(common,metric=field,
                        left_value=x if common['paired_eligible'] else None,right_value=y if common['paired_eligible'] else None,
                        delta_right_minus_left=y-x if common['paired_eligible'] and number(x) and number(y) else None))
    return tables

def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def identity(row):
    return {k:row.get(k) for k in ('group','scenario_id','canonical_class','policy','batch','background_id',
        'window','window_start_minute','window_end_minute','source_binding_sha256','model_config_sha256',
        'policy_panel_sha256','result_sha256')}

def metric_total(rows,target,source,name):
    known=[r['metrics'].get(source) for r in rows if r['eligible'] and number(r['metrics'].get(source))]
    return {name:sum(known) if len(known)==target and target>0 else None,
        name+'_qualified_known_sum':sum(known) if known else None,
        name+'_unknown_records':target-len(known)}

def material_count(rows,target,field):
    known=[r['metrics'].get(field) for r in rows if r['eligible'] and number(r['metrics'].get(field))]
    count=sum(x>1e-8 for x in known)
    return dict(material_specifications=count if len(known)==target and target>0 else None,
        material_specifications_qualified_known_count=count if known else None,
        material_specifications_unknown_records=target-len(known))

def check_normal_proof(rows,proof):
    result=copy.deepcopy(rows)
    for scene in {r['scenario_id'] for r in result}:
        own=[r for r in result if r['scenario_id']==scene]
        by={p:[r for r in own if r['policy']==p] for p in POLICIES}
        reasons=[]
        if any(len(x)!=1 for x in by.values()):reasons.append('normal_cross_panel_proof_missing_registered_policy')
        if not reasons:
            values={p:by[p][0] for p in POLICIES}
            for left,right in (('C19','E'),('C19','K1')):
                a,b=values[left],values[right]
                candidates=[p for p in proof.get('proofs',[]) if p.get('scenario_id')==scene
                    and p.get('window')=='registered_full' and p.get('left_policy')==left and p.get('right_policy')==right]
                required=('source_binding_sha256','model_config_sha256','input_inventory_sha256','physical_scene_sha256',
                    'initial_state_sha256','applied_exogenous_definition_sha256','window_start_minute','window_end_minute')
                valid=[]
                for p in candidates:
                    if (p.get('status')=='matched' and p.get('left_candidate_result_sha256')==[a.get('result_sha256')]
                            and p.get('right_candidate_result_sha256')==[b.get('result_sha256')]
                            and p.get('left_policy_panel_sha256')==a.get('policy_panel_sha256')
                            and p.get('right_policy_panel_sha256')==b.get('policy_panel_sha256')
                            and all(p.get('left_match_values',{}).get(k)==a.get(k)==b.get(k)==p.get('right_match_values',{}).get(k)
                                and a.get(k) is not None for k in required)):
                        valid.append(p)
                if len(valid)!=1:reasons.append('normal_cross_panel_proof_missing_invalid_or_ambiguous:'+left+':'+right)
        if any(not x['eligible'] for x in own):reasons.append('normal_three_policy_intersection_incomplete')
        if reasons:
            for row in own:row['eligible']=False;row['reasons']=list(row.get('reasons',[]))+reasons
    return result

def cohort_name(name,prefix):
    return name==prefix or re.fullmatch(re.escape(prefix)+r'_\d+',name) is not None

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    test=sub.add_parser('self-test');test.add_argument('--label',required=True)
    build=sub.add_parser('build')
    build.add_argument('--label',required=True)
    build.add_argument('--summary',type=Path,required=True,help='Explicit final summary directory or SUMMARY.json')
    build.add_argument('--freeze',type=Path,required=True)
    build.add_argument('--main-prefix',default='main_final_r1')
    build.add_argument('--confirmation-prefix',required=True,help='Exact registered final independent-confirmation cohort prefix')
    build.add_argument('--normal-proof',type=Path,help='External compatibility directory or MATCH_PROOFS.json; absent proof leaves paired normal values unknown')
    build.add_argument('--normal-batch',action='append',help='Alias=cohort prefix, matching exact name or _digits per-scene suffix; default E=normal_E_final_r1,K1=normal_K1_final_r1,C19=normal_C19_final_r1')
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_]+',args.label):raise ValueError('Use a fresh simple label')
    if args.command=='self-test':
        PaperTests.root=RUN/'review/test_tmp'/('paper_results_selftest_'+args.label)
        PaperTests.root.mkdir(parents=True,exist_ok=False)
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PaperTests))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    for path in (args.summary,args.freeze,args.normal_proof):
        if path is not None and not path.resolve().is_relative_to(RUN.resolve()):
            raise ValueError('Final inputs must be explicit saved artifacts inside canonical B19')
    for prefix in (args.main_prefix,args.confirmation_prefix):
        if not re.fullmatch('[A-Za-z0-9_]+',prefix):raise ValueError('Use a simple explicit final cohort prefix')
    if args.main_prefix==args.confirmation_prefix:raise ValueError('Main and confirmation cohorts must be distinct')
    normal={}
    for entry in args.normal_batch or ['E=normal_E_final_r1','K1=normal_K1_final_r1','C19=normal_C19_final_r1']:
        alias,batch=entry.split('=',1)
        if alias not in POLICIES or alias in normal or not re.fullmatch('[A-Za-z0-9_]+',batch):
            raise ValueError('Normal cohort requires a distinct E/K1/C19 alias and simple directory prefix')
        normal[alias]=batch
    if set(normal)!=set(POLICIES) or len(set(normal.values()))!=3:
        raise ValueError('Normal comparison requires three distinct single-policy final cohort prefixes')
    rows,receipt=load_final_evidence(args.summary,args.freeze,main_prefix=args.main_prefix,
        confirmation_prefix=args.confirmation_prefix,normal_batches=normal,normal_proof_path=args.normal_proof)
    tables=make_tables(rows,factors=receipt['factors'])
    for table in tables.values():
        for row in table:
            if 'emissions_factor_case_id' in row:
                row['main_registered_factor_case']=row['emissions_factor_case_id']==receipt['main_factor_case_id']
    out=RUN/'paper_results'/args.label
    saved=save_tables(out,tables,receipt)
    print(json.dumps(dict(output=str(out),row_counts=saved['output_row_counts'],raw_files_opened=0,
        physical_episodes_started=0),ensure_ascii=False))

if __name__=='__main__':main()
