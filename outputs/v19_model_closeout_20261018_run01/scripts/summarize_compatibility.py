"""Read-only, fixed-case M19 compatibility comparisons; never reads trajectories.

The embedded self-test is intentionally separate from the frozen production tree.
"""
from __future__ import annotations
import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import unittest

SCRIPT = Path(__file__).resolve()
RUN = SCRIPT.parents[1]
MODEL = 'M19_model_closeout'
POLICIES = {'E': 'endurance_restore_p0_ready_v14r6', 'K1': 'readiness_handoff_K1_v15',
            'C19': 'C19_critical_reserve_v19r1', 'C18_M19': 'C18_service_rich_M19compat'}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)

def fixture(directory, *, policies=('C19', 'C18_M19'), missing_initial=False,
            audit='passed', partial=False, source=None, state='reset', stream='actual',
            scene_change=None, error=False):
    directory.mkdir(parents=True)
    binding = source or {'src/example.py': digest('frozen-source')}
    specs = [dict(policy_id=POLICIES[p], kind='fixture', config={}) for p in policies]
    config = {'resolved_fixture': 1}
    scene = dict(id='fixed_scene', start_hour=961, duration_steps=40, step_minutes=15.,
        faults={}, shock={}, exogenous_events=[], initialization_seed=1,
        full_source_support={'raw_start_minute': 57600, 'raw_end_minute': 58200})
    scene.update(scene_change or {})
    manifest = dict(execution_model_id=MODEL, source_sha256=binding, model_config=config,
        model_config_signature=digest(config), policy_specs_sha256=digest(specs),
        scenarios=[scene], files=[dict(path='inputs/fixture.csv', sha256=digest('input'))],
        evaluation_role='seen_regression')
    manifest['signature'] = digest(manifest)
    write_json(directory/'manifest.json', manifest)
    plan = dict(model_id=MODEL, model_config=config, source_sha256=binding, policy_specs=specs,
        scenario_ids=[scene['id']], registered_pairs=len(specs),
        manifest_sha256=sha(directory/'manifest.json'), manifest_signature=manifest['signature'])
    write_json(directory/'plan.json', plan)
    records = []
    for number, spec in enumerate(specs):
        economics = dict(grid_purchase_kWh=10.+number, actual_energy_fee_USD=20.+number,
            DG_fuel_budget_debit_kWh_equivalent=3., GT_gas_MMBtu=1., boiler_gas_MMBtu=.5)
        carrier = {f'P{i}': {c: dict(ENS_kWh=float(i+number), disconnected_ENS_kWh=0.,
            connected_unserved_ENS_kWh=float(i+number), material_minutes=2.,
            recovery_status='known') for c in ('e','h','c')} for i in range(4)}
        prefix = dict(carrier_service=carrier, economics=economics, temperature={},
            GT_unit_online_hours={'GT_1': 1.}, original_P0_classifier={'failed': False})
        window = dict(window_minutes=[0.,600.], complete=not partial, observed_prefix=prefix,
            all_registered=prefix if not partial else None,
            unknown_tail_minutes=[300.,600.] if partial else None)
        analysis = dict(execution_model_id=MODEL, requested_end_minute=600.,
            observed_until_minute=300. if partial else 600.,
            v18=dict(windows={'registered_full': window, 'full': copy.deepcopy(window)}),
            windows={'registered_full': dict(window_minutes=[0.,600.], complete=not partial,
                observed_prefix={'source_electric_energy_kWh': {'GRID':10., 'DIESEL':3.}})},
            v19=dict(episode_audit=dict(status=audit, issues=[] if audit=='passed' else ['fixture'],
                complete_registered_interval=not partial)))
        record = dict(scenario_id=scene['id'], policy_id=spec['policy_id'], model_id=MODEL,
            status='completed', raw_sha256=digest('never-open-raw'), raw_path=str(directory/'trajectory.json.gz'),
            error={'type':'fixture_error'} if error else None, analysis_error=None,
            manifest_signature=manifest['signature'], model_config_signature=digest(config), analysis=analysis)
        write_json(directory/'pairs'/(scene['id']+'__'+spec['policy_id'])/'RESULT.json', record)
        records.append({k:v for k,v in record.items() if k!='analysis'})
    exog = {k:v for k,v in scene.items() if k in STREAM_FIELDS or k.startswith(('fault_','shock_'))}
    terminal = dict(source_unchanged=True, plan_sha256=sha(directory/'plan.json'),
        manifest_sha256=sha(directory/'manifest.json'), pair_index=records,
        matching_evidence={scene['id']:dict(initial_state_sha256=[None if missing_initial else digest(state)]*len(specs),
            applied_exogenous_definition_sha256=[digest(exog) if stream=='actual' else digest(stream)]*len(specs),
            same_initial_state=not missing_initial, same_exogenous_definition=True)})
    write_json(directory/'BATCH_RESULT.json', terminal)
    (directory/'trajectory.json.gz').write_bytes(b'RAW MUST NEVER BE READ')
    frozen = dict(kind='v19_final_shared_model_freeze', model_id=MODEL,
        source_sha256={'src/example.py':digest('frozen-source')}, model_config=config,
        policies={p:dict(policy_id=pid,kind='fixture',config={}) for p,pid in POLICIES.items()})
    frozen['signature'] = digest(frozen)
    return frozen

class CompatibilityTests(unittest.TestCase):
    fixture_root = None
    def setUp(self):
        self.path = self.fixture_root/self._testMethodName
        self.path.mkdir()
    def evaluate(self, **kwargs):
        frozen = fixture(self.path/'main', policies=('E','K1','C19'))
        fixture(self.path/'compat', policies=('C18_M19',), **kwargs)
        return compare_batches(frozen, [self.path/'main',self.path/'compat'], {'key001':'fixed_scene'}, [('C19','C18_M19')])
    def test_actual_registered_normal_scene_fields_are_reviewed(self):
        directory=RUN/'experiments/normal_E_final_r1_000'
        frozen=json.loads((RUN/'MODEL_FREEZE_CLOSEOUT01.json').read_text(encoding='utf8'))
        scene=json.loads((directory/'manifest.json').read_text(encoding='utf8'))['scenarios'][0]
        self.assertEqual(scene['normal_duration_hours']*60.,scene['duration_steps']*scene['step_minutes'])
        rows=collect_batch(directory,frozen,{'actual_normal':scene['id']},[])
        full=[r for r in rows if r['window']=='registered_full']
        self.assertEqual(len(full),1)
        self.assertTrue(full[0]['eligible'],full[0]['reasons'])
    def test_normal_hours_must_equal_registered_step_duration(self):
        scene=dict(normal_duration_hours=24.,duration_steps=96,step_minutes=15.)
        self.assertTrue(normal_duration_consistent(scene))
        scene['normal_duration_hours']=25.
        self.assertFalse(normal_duration_consistent(scene))
        scene['normal_duration_hours']=True
        self.assertFalse(normal_duration_consistent(scene))
    def test_actual_confirmation_template_provenance_fields_are_reviewed(self):
        directory=RUN/'experiments/confirmation_final_r1_000'
        frozen=json.loads((RUN/'MODEL_FREEZE_CLOSEOUT01.json').read_text(encoding='utf8'))
        scene=json.loads((directory/'manifest.json').read_text(encoding='utf8'))['scenarios'][0]
        self.assertIsInstance(scene['source_v18_scenario_id'],str)
        self.assertIsInstance(scene['source_v18_template_index'],int)
        rows=collect_batch(directory,frozen,{'actual_confirmation':scene['id']},[])
        full=[r for r in rows if r['window']=='registered_full']
        self.assertEqual(len(full),3)
        self.assertTrue(all(r['eligible'] for r in full),[r['reasons'] for r in full])
    def test_cross_panel_equal_actual_evidence_joins(self):
        report = self.evaluate()
        row = report['comparisons'][0]
        self.assertEqual(row['status'], 'matched')
        self.assertEqual(row['panel_relation'], 'cross_panel')
        self.assertNotEqual(row['left_policy_panel_sha256'],row['right_policy_panel_sha256'])
        cost=next(r for r in report['economics_energy'] if r['metric']=='economics.actual_energy_fee_USD')
        self.assertEqual(cost['delta_right_minus_left'], -2.)
    def test_missing_initial_is_unknown(self):
        report=self.evaluate(missing_initial=True)
        self.assertTrue(all(x['status']=='unknown' for x in report['comparisons']))
        self.assertIn('actual_initial_state_unknown',str(report['candidates']))
    def test_foreign_source_is_unknown(self):
        report=self.evaluate(source={'src/example.py':digest('other')})
        self.assertTrue(all(x['status']=='unknown' for x in report['comparisons']))
    def test_partial_and_failed_audit_never_join(self):
        report=self.evaluate(partial=True,audit='issues_recorded')
        self.assertTrue(all(x['status']=='unknown' for x in report['comparisons']))
        self.assertFalse(report['carrier_service'])
    def test_same_display_id_different_physics_does_not_join(self):
        report=self.evaluate(scene_change={'start_hour':962})
        self.assertTrue(all(x['status']=='unknown' for x in report['comparisons']))
    def test_terminal_stream_must_match_registered_definition(self):
        report=self.evaluate(stream='forged-stream')
        self.assertIn('terminal_applied_stream_differs_from_manifest',str(report['candidates']))
    def test_different_actual_reset_is_unknown(self):
        report=self.evaluate(state='different-real-reset')
        self.assertTrue(all(x['status']=='unknown' for x in report['comparisons']))
    def test_same_panel_development_pair_preserved(self):
        frozen=fixture(self.path/'same')
        report=compare_batches(frozen,[self.path/'same'],{'long000':'fixed_scene'},[('C19','C18_M19')])
        self.assertTrue(all(x['status']=='matched' and x['panel_relation']=='same_panel' for x in report['comparisons']))
    def test_duplicate_eligible_attempts_ambiguous(self):
        frozen=fixture(self.path/'one');fixture(self.path/'two')
        report=compare_batches(frozen,[self.path/'one',self.path/'two'],{'key003':'fixed_scene'},[('C19','C18_M19')])
        self.assertTrue(all(x['status']=='unknown' and 'ambiguous' in x['reason'] for x in report['comparisons']))
    def test_error_is_preserved_and_ineligible(self):
        report=self.evaluate(error=True)
        self.assertIn('saved_error_present',str(report['candidates']))
        self.assertTrue(all(x['status']=='unknown' for x in report['comparisons']))
    def test_missing_terminal_is_preserved_unknown(self):
        frozen=fixture(self.path/'missing');(self.path/'missing'/'BATCH_RESULT.json').unlink()
        report=compare_batches(frozen,[self.path/'missing'],{'key007':'fixed_scene'},[('C19','C18_M19')])
        self.assertTrue(report['candidates'])
        self.assertTrue(all(x['status']=='unknown' for x in report['comparisons']))
    def test_changed_freeze_rejected(self):
        frozen=fixture(self.path/'one');frozen['model_config']['resolved_fixture']=2
        with self.assertRaisesRegex(ValueError,'freeze'):
            compare_batches(frozen,[self.path/'one'],{'key043':'fixed_scene'},[('C19','C18_M19')])
    def test_saved_complete_flag_cannot_shorten_registered_episode(self):
        frozen=fixture(self.path/'wrong_end',scene_change={'duration_steps':80})
        report=compare_batches(frozen,[self.path/'wrong_end'],{'long005':'fixed_scene'},[('C19','C18_M19')])
        self.assertTrue(all(x['status']=='unknown' for x in report['comparisons']))
        self.assertIn('registered_end',str(report['candidates']))
    def test_unreviewed_scene_key_is_unknown_even_if_equal(self):
        frozen=fixture(self.path/'unknown',scene_change={'unreviewed_dynamics_knob':0.})
        report=compare_batches(frozen,[self.path/'unknown'],{'key067':'fixed_scene'},[('C19','C18_M19')])
        self.assertIn('unreviewed_scene_fields',str(report['candidates']))
        self.assertTrue(all(x['status']=='unknown' for x in report['comparisons']))
    def test_metadata_difference_preserved_without_changing_applied_stream(self):
        report=self.evaluate(scene_change={'v19_seen_regression':True})
        self.assertTrue(all(x['status']=='matched' for x in report['comparisons']))
        self.assertIn('v19_seen_regression',str(report['proofs'][0]['full_scene_metadata_diff']))
    def test_missing_metric_is_unknown_not_zero(self):
        frozen=fixture(self.path/'metrics')
        result_path=self.path/'metrics'/'pairs'/('fixed_scene__'+POLICIES['C18_M19'])/'RESULT.json'
        record=json.loads(result_path.read_text(encoding='utf8'))
        for window in record['analysis']['v18']['windows'].values():
            del window['all_registered']['economics']['actual_energy_fee_USD']
        result_path.write_text(json.dumps(record,ensure_ascii=False,allow_nan=False),encoding='utf8')
        report=compare_batches(frozen,[self.path/'metrics'],{'key001':'fixed_scene'},[('C19','C18_M19')])
        cost=next(x for x in report['economics_energy'] if x['metric']=='economics.actual_energy_fee_USD')
        self.assertIsNone(cost['right_value']);self.assertIsNone(cost['delta_right_minus_left'])
    def test_report_output_preserves_proof_hashes_and_never_opens_raw(self):
        from unittest.mock import patch
        frozen=fixture(self.path/'output')
        freeze_path=self.path/'frozen.json';write_json(freeze_path,frozen)
        original=Path.read_bytes
        def guarded(path):
            if path.name=='trajectory.json.gz':raise AssertionError('Forbidden raw read')
            return original(path)
        with patch.object(Path,'read_bytes',guarded):
            report=compare_batches(frozen,[self.path/'output'],{'long000':'fixed_scene'},[('C19','C18_M19')])
            save_report(self.path/'report',report,freeze_path)
        summary=json.loads((self.path/'report'/'SUMMARY.json').read_text(encoding='utf8'))
        self.assertEqual(summary['comparison_counts'],{'matched':2,'unknown':0})
        self.assertIs(summary['main_three_policy_denominator_modified'],False)
        self.assertEqual(summary['output_sha256']['MATCH_PROOFS.json'],sha(self.path/'report'/'MATCH_PROOFS.json'))
        with (self.path/'report'/'CARRIER_SERVICE.csv').open(encoding='utf-8-sig',newline='') as stream:
            self.assertEqual({r['carrier'] for r in csv.DictReader(stream)},{'e','h','c'})

STREAM_FIELDS = {'start_hour','duration_steps','step_minutes','faults','shock','shock_by_priority',
    'exogenous_events','enable_propagation','propagation_enabled','grid_limit_kW',
    'pre_event_grid_limit_kW','pre_event_warmup_steps','response_delay_minutes',
    'automatic_fast_shedding','initialization_seed'}

PHYSICAL_FIELDS = STREAM_FIELDS | set('root_family_id base_state_id stage event_class '
    'fault_start_minute fault_duration_minutes shock_start_minute shock_duration_minutes '
    'full_source_support actual_p0_load_multiplier thermal_disturbance diesel_on_grid_allowed '
    'precontrol_sheddable_fraction_by_priority first_event_minute normal_duration_hours'.split())
# Finite, reviewed scene schema. Every unknown key is ineligible, even if values match.
# This list acknowledges existing provenance fields; it never drops an arbitrary new key.
METADATA_FIELDS = set('id source_day_index season_quarter family_id parent_template_id '
    'parent_instance_id design_family event_schema_version sample_schema_version distribution_id '
    'sampling_method sampling_weight_is_failure_probability p_reference q_design_density importance_weight '
    'history_interval_minutes score_interval_minutes tail_interval_minutes label_horizons_minutes '
    'prehistory_mode initialization_semantics state_inheritance analysis_class normal_history_minutes '
    'initial_binding_status experiment_role selection_allowed requires_policy_freeze fit_allowed '
    'is_final_blind purpose sampling_cohort analysis_cohort sampling_role new_independent_background '
    'old_results_read_only demand_background diesel_design core_four_class_denominator '
    'coordinate_design_version source_outcomes_used_for_selection original_final12_evaluated '
    'time_block_id exogenous_stream_id physical_scenario_signature matched_group_id design_cell_id '
    'canonical_class legacy_class design_variant development_background background_status '
    'historical_exposure_status long_outage_mechanism exogenous_multi_fault_is_endogenous_propagation '
    'continuous_recovery_tail_minutes source_scenario_id source_coordinate_design_version '
    'v18_source_scene_id v18_seen_regression v19_source_scene_id v19_seen_regression comparison_scope '
    'demand_carrier_design propagation_design propagation_pair_id v17_matched_propagation_ablation '
    'target_operating_role target_service_attribution normal_design_scope '
    'source_v18_scenario_id source_v18_template_index'.split())

def read_json(path, inputs):
    path=Path(path)
    if path.name not in {'plan.json','manifest.json','BATCH_RESULT.json','RESULT.json'}:
        raise ValueError('Input is outside the compact evidence allowlist: '+str(path))
    content=path.read_bytes()
    inputs.append(dict(path=str(path),sha256=hashlib.sha256(content).hexdigest(),bytes=len(content)))
    return json.loads(content)

def is_sha(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None

def valid_number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def normal_duration_consistent(scene):
    if 'normal_duration_hours' not in scene:return True
    hours=scene['normal_duration_hours'];steps=scene.get('duration_steps');step=scene.get('step_minutes',15.)
    return all(valid_number(v) and v>0 for v in (hours,steps,step)) and hours*60.==steps*step

def inventory_signature(manifest):
    files=manifest.get('files')
    if not isinstance(files,list) or not files:return None
    paths=[f.get('path') for f in files]
    if (not all(isinstance(p,str) and p for p in paths) or len(paths)!=len(set(paths))
            or any(not is_sha(f.get('sha256')) for f in files)):return None
    return digest(sorted((f['path'],f['sha256']) for f in files))

def collect_batch(directory, frozen, cases, inputs):
    directory=Path(directory)
    plan=read_json(directory/'plan.json',inputs)
    manifest=read_json(directory/'manifest.json',inputs)
    terminal=read_json(directory/'BATCH_RESULT.json',inputs) if (directory/'BATCH_RESULT.json').is_file() else {}
    problems=[]
    def require(condition,reason):
        if not condition:problems.append(reason)
    require(manifest.get('signature')==digest({k:v for k,v in manifest.items() if k!='signature'}),
        'manifest_signature_invalid')
    require(plan.get('manifest_sha256')==sha(directory/'manifest.json')
        and plan.get('manifest_signature')==manifest.get('signature'),'plan_manifest_binding_invalid')
    require(plan.get('model_id')==MODEL and manifest.get('execution_model_id')==MODEL,'non_M19_model')
    require(plan.get('source_sha256')==manifest.get('source_sha256')==frozen.get('source_sha256'),
        'source_binding_differs_from_final_freeze')
    require(plan.get('model_config')==manifest.get('model_config')==frozen.get('model_config')
        and manifest.get('model_config_signature')==digest(plan.get('model_config')),
        'resolved_model_config_differs_from_final_freeze')
    policies=[s.get('policy_id') for s in plan.get('policy_specs',[])]
    require(bool(policies) and len(policies)==len(set(policies)),'policy_panel_invalid')
    require(manifest.get('policy_specs_sha256')==digest(plan.get('policy_specs')),'policy_specs_binding_invalid')
    frozen_specs={s.get('policy_id'):s for s in frozen['policies'].values()}
    require(all(s==frozen_specs.get(s.get('policy_id')) for s in plan.get('policy_specs',[])),
        'policy_specs_differ_from_final_freeze')
    scenes=manifest.get('scenarios',[])
    require(plan.get('scenario_ids')==[s.get('id') for s in scenes]
        and plan.get('registered_pairs')==len(scenes)*len(policies),'registered_panel_invalid')
    require(terminal.get('source_unchanged') is True
        and terminal.get('plan_sha256')==sha(directory/'plan.json')
        and terminal.get('manifest_sha256')==sha(directory/'manifest.json'),'terminal_binding_missing_or_invalid')
    inventory=inventory_signature(manifest)
    require(inventory is not None,'input_inventory_missing_or_invalid')
    rows=[]
    for scene in scenes:
        labels=[label for label,scene_id in cases.items() if scene_id==scene.get('id')]
        if not labels:continue
        extra_fields=set(scene)-PHYSICAL_FIELDS-METADATA_FIELDS
        extra_fields={k for k in extra_fields if not k.startswith(('fault_','shock_'))}
        stream={k:v for k,v in scene.items() if k in STREAM_FIELDS or k.startswith(('fault_','shock_'))}
        stream_sha=digest(stream)
        physical={k:v for k,v in scene.items() if k in PHYSICAL_FIELDS or k.startswith(('fault_','shock_'))}
        evidence=terminal.get('matching_evidence',{}).get(scene['id'],{})
        states=evidence.get('initial_state_sha256',[])
        streams=evidence.get('applied_exogenous_definition_sha256',[])
        evidence_reasons=[]
        if not normal_duration_consistent(scene):
            evidence_reasons.append('normal_duration_hours_inconsistent_with_registered_duration')
        if (evidence.get('same_initial_state') is not True or len(states)!=len(policies)
                or not states or not all(is_sha(s) for s in states) or len(set(states))!=1):
            evidence_reasons.append('actual_initial_state_unknown_or_not_panel_matched')
        if (evidence.get('same_exogenous_definition') is not True or len(streams)!=len(policies)
                or not streams or not all(is_sha(s) for s in streams) or len(set(streams))!=1):
            evidence_reasons.append('actual_applied_stream_unknown_or_not_panel_matched')
        if not streams or any(s!=stream_sha for s in streams):
            evidence_reasons.append('terminal_applied_stream_differs_from_manifest')
        if extra_fields:evidence_reasons.append('unreviewed_scene_fields:'+','.join(sorted(extra_fields)))
        for policy in policies:
            path=directory/'pairs'/(scene['id']+'__'+policy)/'RESULT.json'
            record=read_json(path,inputs) if path.is_file() else {}
            reasons=list(problems)+evidence_reasons
            if not record:reasons.append('missing_registered_result')
            if (record.get('scenario_id'),record.get('policy_id'),record.get('model_id'))!=(scene['id'],policy,MODEL):
                reasons.append('result_identity_mismatch')
            if (record.get('manifest_signature')!=manifest.get('signature')
                    or record.get('model_config_signature')!=digest(plan.get('model_config'))):
                reasons.append('result_binding_mismatch')
            indices=[x for x in terminal.get('pair_index',[]) if (x.get('scenario_id'),x.get('policy_id'))==(scene['id'],policy)]
            if len(indices)!=1 or indices[0]!={k:v for k,v in record.items() if k!='analysis'}:
                reasons.append('terminal_result_index_mismatch')
            if record.get('status')!='completed':reasons.append('status_not_completed')
            if record.get('error'):reasons.append('saved_error_present')
            if record.get('analysis_error'):reasons.append('saved_analysis_error_present')
            analysis=record.get('analysis') or {}
            if analysis.get('execution_model_id')!=MODEL:reasons.append('analysis_model_missing_or_not_M19')
            windows=analysis.get('v18',{}).get('windows',{})
            audit=analysis.get('v19',{}).get('episode_audit',{})
            if audit.get('status')!='passed' or audit.get('issues')!=[]:reasons.append('episode_audit_not_passed')
            if audit.get('complete_registered_interval') is not True:reasons.append('episode_audit_incomplete')
            if windows.get('registered_full',{}).get('complete') is not True:reasons.append('registered_episode_incomplete')
            duration=scene.get('duration_steps');step=scene.get('step_minutes',15.)
            registered_end=duration*step if valid_number(duration) and valid_number(step) else None
            full_interval=windows.get('registered_full',{}).get('window_minutes')
            if (not valid_number(registered_end) or registered_end<=0
                    or analysis.get('requested_end_minute')!=registered_end
                    or full_interval!=[0.,registered_end]
                    or not valid_number(analysis.get('observed_until_minute'))
                    or analysis['observed_until_minute']<registered_end-1e-7):
                reasons.append('registered_end_not_fully_observed_or_analysis_binding_invalid')
            for name in sorted(set(windows)|{'full','registered_full'}):
                w=windows.get(name,{})
                interval=w.get('window_minutes')
                window_reasons=list(reasons)
                if (not isinstance(interval,list) or len(interval)!=2 or not all(valid_number(x) for x in interval)
                        or not 0<=interval[0]<interval[1]):
                    window_reasons.append('window_interval_unknown_or_invalid');interval=[None,None]
                if w.get('complete') is not True or not isinstance(w.get('all_registered'),dict):
                    window_reasons.append('window_incomplete_or_saved_metrics_missing')
                if interval[1] is not None and (not valid_number(analysis.get('observed_until_minute'))
                        or analysis['observed_until_minute']<interval[1]-1e-7):
                    window_reasons.append('window_beyond_observed_time')
                for label in labels:
                    rows.append(dict(case=label,scenario_id=scene['id'],batch=directory.name,policy_id=policy,
                        window=name,window_start_minute=interval[0],window_end_minute=interval[1],
                        policy_panel_sha256=digest(plan.get('policy_specs')),source_binding_sha256=digest(plan.get('source_sha256')),
                        model_config_sha256=digest(plan.get('model_config')),input_inventory_sha256=inventory,
                        physical_scene_sha256=digest(physical),full_registered_scene_sha256=digest(scene),
                        initial_state_sha256=states[0] if states and is_sha(states[0]) else None,
                        applied_exogenous_definition_sha256=streams[0] if streams and is_sha(streams[0]) else None,
                        manifest_stream_sha256=stream_sha,evaluation_role=manifest.get('evaluation_role'),
                        eligible=not window_reasons,reasons=window_reasons,status=record.get('status','missing_result'),
                        episode_audit_status=audit.get('status','unknown'),complete=w.get('complete') is True,
                        result_path=str(path),result_sha256=sha(path) if path.is_file() else None,
                        error=record.get('error'),analysis_error=record.get('analysis_error'),
                        saved_metrics=w.get('all_registered') if w.get('complete') else None,
                        saved_engineering=analysis.get('windows',{}).get(name,{}).get('observed_prefix'),
                        scene=scene,terminal_matching_evidence=evidence))
    return rows

MATCH_FIELDS=('source_binding_sha256','model_config_sha256','input_inventory_sha256','physical_scene_sha256',
    'initial_state_sha256','applied_exogenous_definition_sha256','window_start_minute','window_end_minute')

def flatten(value,prefix=''):
    result={}
    for key,item in (value or {}).items():
        name=prefix+'.'+key if prefix else key
        if isinstance(item,dict):result.update(flatten(item,name))
        else:result[name]=item
    return result

def metric_rows(base,left,right):
    result=[]
    for key in sorted(set(left)|set(right)):
        a,b=left.get(key),right.get(key)
        result.append(dict(base,metric=key,left_value=a,right_value=b,
            delta_right_minus_left=b-a if valid_number(a) and valid_number(b) else None,
            value_status='both_saved' if a is not None and b is not None else 'unknown_component'))
    return result

def compare_batches(frozen, directories, cases, pairs):
    if (frozen.get('kind')!='v19_final_shared_model_freeze' or frozen.get('model_id')!=MODEL
            or frozen.get('signature')!=digest({k:v for k,v in frozen.items() if k!='signature'})
            or not frozen.get('source_sha256') or not frozen.get('policies')):
        raise ValueError('A signed final M19 freeze with source/config/policy binding is required')
    inputs=[];candidates=[]
    for directory in directories:candidates.extend(collect_batch(directory,frozen,cases,inputs))
    comparisons=[];carrier=[];temperature=[];economics=[];proofs=[]
    for label,scene_id in cases.items():
        case_rows=[r for r in candidates if r['case']==label]
        windows=sorted({r['window'] for r in case_rows}|{'full','registered_full'})
        for name in windows:
            for left_alias,right_alias in pairs:
                sides=[[r for r in case_rows if r['window']==name and r['policy_id']==POLICIES[alias]]
                    for alias in (left_alias,right_alias)]
                eligible=[[r for r in side if r['eligible']] for side in sides]
                reasons=[]
                for alias,side in zip((left_alias,right_alias),eligible):
                    if len(side)!=1:reasons.append(alias+('_ambiguous_eligible_attempts' if len(side)>1 else '_no_eligible_complete_evidence'))
                a=eligible[0][0] if len(eligible[0])==1 else None
                b=eligible[1][0] if len(eligible[1])==1 else None
                if a and b:
                    reasons.extend('different_'+key for key in MATCH_FIELDS if a[key]!=b[key])
                    if a['evaluation_role']!=b['evaluation_role']:reasons.append('different_evaluation_role')
                base=dict(case=label,scenario_id=scene_id,window=name,left_policy=left_alias,right_policy=right_alias,
                    comparison_id=digest([label,name,left_alias,right_alias])[:20],
                    status='unknown' if reasons else 'matched',reason=';'.join(reasons) if reasons else 'all_required_actual_evidence_equal',
                    left_batch=a['batch'] if a else None,right_batch=b['batch'] if b else None,
                    left_policy_panel_sha256=a['policy_panel_sha256'] if a else None,
                    right_policy_panel_sha256=b['policy_panel_sha256'] if b else None,
                    panel_relation=('same_panel' if a['policy_panel_sha256']==b['policy_panel_sha256'] else 'cross_panel') if a and b else 'unknown',
                    denominator_scope='fixed_case_explanation_only_not_main_algorithm_denominator')
                comparisons.append(base)
                scene_diff=({key:dict(left=a['scene'].get(key),right=b['scene'].get(key))
                    for key in sorted(set(a['scene'])|set(b['scene'])) if a['scene'].get(key)!=b['scene'].get(key)} if a and b else None)
                proofs.append(dict(base,required_match_fields=list(MATCH_FIELDS),
                    left_candidate_result_sha256=[r['result_sha256'] for r in sides[0]],
                    right_candidate_result_sha256=[r['result_sha256'] for r in sides[1]],
                    left_match_values={k:a[k] for k in MATCH_FIELDS} if a else None,
                    right_match_values={k:b[k] for k in MATCH_FIELDS} if b else None,
                    full_scene_metadata_diff=scene_diff,
                    left_terminal_matching_evidence=a['terminal_matching_evidence'] if a else None,
                    right_terminal_matching_evidence=b['terminal_matching_evidence'] if b else None))
                if reasons:continue
                for priority in ('P0','P1','P2','P3'):
                    for c in ('e','h','c'):
                        x=a['saved_metrics'].get('carrier_service',{}).get(priority,{}).get(c,{})
                        y=b['saved_metrics'].get('carrier_service',{}).get(priority,{}).get(c,{})
                        carrier.extend(metric_rows(dict(base,priority=priority,carrier=c),flatten(x),flatten(y)))
                    temperature.extend(metric_rows(dict(base,priority=priority),
                        flatten(a['saved_metrics'].get('temperature',{}).get(priority,{})),
                        flatten(b['saved_metrics'].get('temperature',{}).get(priority,{}))))
                left=flatten(a['saved_metrics'].get('economics',{}),'economics')
                right=flatten(b['saved_metrics'].get('economics',{}),'economics')
                for dest,row in ((left,a),(right,b)):
                    econ=row['saved_metrics'].get('economics',{})
                    if 'DG_fuel_budget_debit_kWh_equivalent' in econ:
                        dest['economics.DG_fuel_budget_debit_shaft_equivalent_kWh']=econ['DG_fuel_budget_debit_kWh_equivalent']
                    dest.update(flatten((row.get('saved_engineering') or {}).get('source_electric_energy_kWh',{}),'source_electric_energy_kWh'))
                    for field in ('GT_unit_online_hours','GT_actual_start_count','original_P0_classifier'):
                        val=row['saved_metrics'].get(field)
                        if isinstance(val,dict):dest.update(flatten(val,field))
                        elif val is not None:dest[field]=val
                economics.extend(metric_rows(base,left,right))
    return dict(schema='hospital_ies.v19.external_compatibility.v1',model_id=MODEL,
        freeze_signature=frozen['signature'],final_source_binding_sha256=digest(frozen['source_sha256']),
        cases=cases,policy_pairs=pairs,comparisons=comparisons,carrier_service=carrier,temperature=temperature,
        economics_energy=economics,proofs=proofs,candidates=candidates,inputs=inputs,
        physical_episodes_started=0,raw_files_opened=0,annual_inputs_loaded=0,
        main_three_policy_denominator_modified=False,
        interpretation='All policies share the M19 repairs. C19 vs C18_M19 isolates the registered reserve toggle only under matched evidence; archived C18/M18 is not this comparison. Fixed seen cases do not establish population superiority.')

def write_csv(path, rows):
    keys=sorted({k for r in rows for k in r})
    with Path(path).open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False,allow_nan=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})

def save_report(out, report, freeze_path):
    out.mkdir(parents=True,exist_ok=False)
    for filename,key in (('COMPATIBILITY_CASES.csv','comparisons'),('CARRIER_SERVICE.csv','carrier_service'),
            ('TEMPERATURE.csv','temperature'),('ECONOMICS_ENERGY.csv','economics_energy'),('MATCH_PROOFS.csv','proofs')):
        write_csv(out/filename,report[key])
    candidate_keys=[k for k in report['candidates'][0] if k not in {'saved_metrics','saved_engineering','scene','terminal_matching_evidence'}] if report['candidates'] else []
    write_csv(out/'CASE_STATUS.csv',[{k:r[k] for k in candidate_keys} for r in report['candidates']])
    write_json(out/'MATCH_PROOFS.json',dict(proofs=report['proofs'],candidates=report['candidates'],inputs=report['inputs']))
    summary={k:v for k,v in report.items() if k not in {'candidates','proofs','carrier_service','temperature','economics_energy','inputs'}}
    summary.update(created_utc=datetime.now(timezone.utc).isoformat(),script_sha256=sha(SCRIPT),
        freeze_path=str(freeze_path),freeze_file_sha256=sha(freeze_path),
        comparison_counts={status:sum(r['status']==status for r in report['comparisons']) for status in ('matched','unknown')},
        matched_rows_are_not_independent_case_counts=True,
        input_file_sha256=report['inputs'])
    summary['output_sha256']={p.name:sha(p) for p in out.iterdir() if p.is_file()}
    write_json(out/'SUMMARY.json',summary)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    test=sub.add_parser('self-test');test.add_argument('--label',required=True)
    compare=sub.add_parser('compare')
    compare.add_argument('--label',required=True);compare.add_argument('--freeze',type=Path,required=True)
    compare.add_argument('--batch',action='append',type=Path,required=True)
    compare.add_argument('--case',action='append',required=True,help='Explicit fixed-case label=scenario_id')
    compare.add_argument('--pair',action='append',help='Left:right aliases; default C19:C18_M19,C19:E,C19:K1,C18_M19:E,C18_M19:K1')
    args=parser.parse_args()
    if args.command=='self-test':
        if not re.fullmatch(r'[A-Za-z0-9_]+',args.label):raise ValueError('Use a fresh simple label')
        CompatibilityTests.fixture_root=RUN/'review'/'test_tmp'/('compatibility_selftest_'+args.label)
        CompatibilityTests.fixture_root.mkdir(parents=True)
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CompatibilityTests))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    if not re.fullmatch(r'[A-Za-z0-9_]+',args.label):raise ValueError('Use a fresh simple label')
    cases={}
    for entry in args.case:
        label,scene=entry.split('=',1)
        if label in cases or not label or not scene:raise ValueError('Distinct explicit nonempty case labels required')
        cases[label]=scene
    pairs=[tuple(entry.split(':')) for entry in (args.pair or ['C19:C18_M19','C19:E','C19:K1','C18_M19:E','C18_M19:K1'])]
    if any(len(pair)!=2 or pair[0]==pair[1] or any(p not in POLICIES for p in pair) for pair in pairs):
        raise ValueError('Pair must contain two different E/K1/C19/C18_M19 aliases')
    directories=[p.resolve() for p in args.batch]
    canonical=(RUN/'experiments').resolve()
    if len(directories)!=len(set(directories)) or any(p.parent!=canonical for p in directories):
        raise ValueError('Use distinct explicit canonical B19 experiment directories')
    freeze_path=args.freeze.resolve()
    if not freeze_path.is_relative_to(RUN.resolve()):raise ValueError('Freeze must be an explicit B19 artifact')
    frozen=json.loads(freeze_path.read_text(encoding='utf8'))
    report=compare_batches(frozen,directories,cases,pairs)
    out=RUN/'review'/('compatibility_'+args.label)
    save_report(out,report,freeze_path)
    print(json.dumps(dict(output=str(out),comparisons=len(report['comparisons']),
        matched=sum(r['status']=='matched' for r in report['comparisons']),raw_files_opened=0)))

if __name__=='__main__':main()
