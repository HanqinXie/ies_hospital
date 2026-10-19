"""Bounded external-wrapper tests; no dispatch, raw or annual input opens."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
try:
    spec=importlib.util.spec_from_file_location('external_reproduce_v20',HERE/'reproduce_v20.py')
    M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
except FileNotFoundError:
    M=None


def fixture():
    scene=dict(id='scene_000',duration_steps=40,step_minutes=15.,start_hour=12,
        faults=[],shock={},physical_scenario_signature='physical',evaluation_role='seen_regression')
    policy=dict(policy_id='E_M20',display_alias='E',kind='engineering',config={})
    freeze=dict(model_id='M20_load_accounting',source_sha256={'src/core.py':'a'*64},
        runtime_inputs=[dict(path='inputs/x.csv',bytes=1,sha256='b'*64)],model_config={'x':1},
        policies={'E':policy},signature='c'*64)
    manifest=dict(execution_model_id=freeze['model_id'],source_sha256=freeze['source_sha256'],
        files=freeze['runtime_inputs'],model_config=freeze['model_config'],
        model_config_signature=M.digest(freeze['model_config']),scenarios=[scene],policies=['E_M20'],
        policy_specs_sha256=M.digest([policy]),freeze_signature=freeze['signature'],
        source_freeze_artifact={'sha256':'f'*64},evaluation_role='seen_regression')
    manifest['signature']=M.digest(manifest)
    plan=dict(model_id=freeze['model_id'],source_sha256=freeze['source_sha256'],
        model_config=freeze['model_config'],policy_specs=[policy],scenario_ids=[scene['id']],
        registered_pairs=1,manifest_signature=manifest['signature'],manifest_sha256='1'*64)
    exog=M.applied_stream_hash(scene)
    evidence=dict(initial_state_sha256=['2'*64],applied_exogenous_definition_sha256=[exog],
        same_initial_state=True,same_exogenous_definition=True,injection_state_sha256=['3'*64])
    terminal=dict(registered_pairs=1,actual_attempts=1,complete_full=1,unknown_full=0,
        analysis_failure_or_unavailable_count=0,status_counts={'completed':1},source_unchanged=True,
        plan_sha256='4'*64,manifest_sha256='1'*64,matching_evidence={scene['id']:evidence})
    analysis=dict(execution_model_id=freeze['model_id'],observed_until_minute=600.,
        v18={'windows':{'registered_full':dict(complete=True,window_minutes=[0.,600.],
            unknown_tail_minutes=None,all_registered={})}},
        v20={'episode_audit':{'status':'passed','issues':[]},'service_ledgers':{
            'registered_full':dict(complete=True,window_minutes=[0.,600.],observed_finish_minute=600.,
                unknown_tail_minutes=None,all_registered={})}})
    record=dict(status='completed',error=None,analysis_error=None,model_id=freeze['model_id'],
        scenario_id=scene['id'],policy_id=policy['policy_id'],manifest_signature=manifest['signature'],
        model_config_signature=manifest['model_config_signature'],raw_sha256='5'*64,analysis=analysis)
    journal=[dict(event='started',scenario_id=scene['id'],policy_id=policy['policy_id'],model_id=freeze['model_id']),
        dict(event='finished',scenario_id=scene['id'],policy_id=policy['policy_id'],status='completed',
            analysis_status='saved',result_sha256='6'*64,raw_sha256='5'*64)]
    bundle=dict(plan=plan,manifest=manifest,terminal=terminal,record=record,journal=journal,
        journal_issues=[],file_sha256={'manifest.json':'1'*64,'plan.json':'4'*64,'RESULT.json':'6'*64},
        snapshot_metrics={'energy.x'+str(i):float(i) for i in range(94)},
        execution_qualified=True,execution_reasons=[])
    return bundle,freeze


class WrapperTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(M,'independent wrapper is not implemented')
    def valid(self):
        b,f=fixture();return b,f,M.qualify_evidence(b,f,'f'*64)
    def test_valid_full_qualified_pair(self):
        b,f,p=self.valid();self.assertEqual(p['metric_count'],94)
    def test_rejects_unknown_metrics(self):
        b,f=fixture();b['snapshot_metrics']['energy.x0']=None
        with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_rejects_ledger_incomplete(self):
        b,f=fixture();b['record']['analysis']['v20']['service_ledgers']['registered_full']['complete']=False
        with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_rejects_wrong_window(self):
        b,f=fixture();b['record']['analysis']['v18']['windows']['registered_full']['window_minutes']=[0.,300.]
        with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_rejects_failed_attempt_qualification(self):
        b,f=fixture();b['execution_qualified']=False;b['execution_reasons']=['actual_finish_event_not_unique']
        with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_rejects_broken_journal(self):
        b,f=fixture();b['journal_issues']=['bad line']
        with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_rejects_duplicate_attempt(self):
        b,f=fixture();b['journal'].append(copy.deepcopy(b['journal'][0]))
        with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_rejects_unbound_terminal(self):
        b,f=fixture();b['terminal']['plan_sha256']='9'*64
        with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_rejects_changed_input_inventory(self):
        b,f=fixture();b['manifest']['files']=[]
        with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_rejects_missing_or_false_actual_matching(self):
        for change in (None,False):
            b,f=fixture();e=b['terminal']['matching_evidence']['scene_000']
            if change is None:e['initial_state_sha256']=[None]
            else:e['same_initial_state']=False
            with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_rejects_applied_stream_not_matching_registered_physics(self):
        b,f=fixture();b['terminal']['matching_evidence']['scene_000']['applied_exogenous_definition_sha256']=['a'*64]
        with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_pair_matching_rejects_true_reset_difference(self):
        b,f,p=self.valid();q=copy.deepcopy(p);q['actual_initial_state_sha256']='7'*64
        with self.assertRaises(ValueError):M.match_qualified_pairs(p,q)
    def test_rejects_missing_actual_E_injection_fingerprint(self):
        b,f=fixture();b['terminal']['matching_evidence']['scene_000']['injection_state_sha256']=[None]
        with self.assertRaises(ValueError):M.qualify_evidence(b,f,'f'*64)
    def test_pair_matching_rejects_same_E_injection_difference(self):
        b,f,p=self.valid();q=copy.deepcopy(p);q['actual_injection_state_sha256']='7'*64
        with self.assertRaises(ValueError):M.match_qualified_pairs(p,q)
    def test_pair_matching_rejects_different_evaluation_role(self):
        b,f,p=self.valid();q=copy.deepcopy(p);q['evaluation_role']='development'
        with self.assertRaises(ValueError):M.match_qualified_pairs(p,q)
    def test_pair_matching_retains_panel_difference(self):
        b,f,p=self.valid();q=copy.deepcopy(p);q['policy_panel_sha256']='7'*64
        proof=M.match_qualified_pairs(p,q)
        self.assertTrue(proof['matched']);self.assertNotEqual(proof['reference_policy_panel_sha256'],proof['replay_policy_panel_sha256'])
    def test_release_refuses_original_or_nested_root(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            base=Path(temp);source=base/'source';source.mkdir()
            marker=dict(source_root=str(source),original_scientific_root=str(base/'science'))
            with self.assertRaises(ValueError):M.validate_root_isolation(source,marker)
            child=source/'clone';child.mkdir()
            with self.assertRaises(ValueError):M.validate_root_isolation(child,marker)
    def test_release_requires_original_research_root(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            root=Path(temp)
            with self.assertRaises(ValueError):M.validate_root_isolation(root,dict(
                source_root=str(root.parent/'source'),original_scientific_root=str(root.parent/'science')))
    def test_parent_guard_blocks_original_read_and_then_deactivates(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            root=Path(temp);original=root/'original';original.mkdir();path=original/'x.txt'
            path.write_text('fixture',encoding='utf8')
            self.assertTrue(hasattr(M,'PythonReadGuard'),'parent guard not implemented')
            with M.PythonReadGuard(root/'stage',[original]) as guard:
                with self.assertRaises(RuntimeError):path.read_text(encoding='utf8')
                self.assertEqual(len(guard.summary()['violations']),1)
            self.assertEqual(path.read_text(encoding='utf8'),'fixture')
    def test_guard_blocks_original_read_and_records_import_path(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            base=Path(temp);stage=base/'stage';stage.mkdir();original=base/'original';original.mkdir()
            (original/'secret.txt').write_text('not read',encoding='utf8')
            guard=stage/'guard';guard.mkdir();receipt=stage/'guard.json'
            M.write_child_guard(guard,stage,[original],receipt)
            env=M.guarded_environment({},guard)
            script="from pathlib import Path; Path("+repr(str(original/'secret.txt'))+").read_text()"
            done=subprocess.run([sys.executable,'-B','-c',script],cwd=stage,env=env,capture_output=True)
            self.assertNotEqual(done.returncode,0)
            proof=json.loads(receipt.read_text(encoding='utf8'))
            self.assertEqual(proof['status'],'failed');self.assertTrue(proof['violations'])
    def test_guard_passes_clean_child(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            base=Path(temp);stage=base/'stage';stage.mkdir();guard=stage/'guard';guard.mkdir()
            receipt=stage/'guard.json';M.write_child_guard(guard,stage,[base/'original'],receipt)
            done=subprocess.run([sys.executable,'-B','-c','print(1)'],cwd=stage,
                env=M.guarded_environment({},guard),capture_output=True)
            self.assertEqual(done.returncode,0,done.stderr)
            self.assertEqual(json.loads(receipt.read_text(encoding='utf8'))['status'],'passed')
    def test_guard_detects_project_import_outside_clone(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            base=Path(temp);stage=base/'stage';stage.mkdir();guard=stage/'guard';guard.mkdir()
            outside=base/'external';package=outside/'hospital_ies';package.mkdir(parents=True)
            (package/'__init__.py').write_text('x=1',encoding='utf8')
            receipt=stage/'guard.json';M.write_child_guard(guard,stage,[base/'original'],receipt)
            done=subprocess.run([sys.executable,'-B','-c','import hospital_ies'],cwd=stage,
                env=M.guarded_environment({'PYTHONPATH':str(outside)},guard),capture_output=True)
            self.assertEqual(done.returncode,0,done.stderr)
            self.assertEqual(json.loads(receipt.read_text(encoding='utf8'))['status'],'failed')
    def test_release_requires_marker_and_selection(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            with self.assertRaises(ValueError):M.validate_release(Path(temp))
    def test_release_verifies_self_marker_and_all_selected_bytes(self):
        with tempfile.TemporaryDirectory(dir=HERE) as temp:
            root=Path(temp);pub=root/'publication';pub.mkdir();run=root/M.RUN_REL;run.mkdir(parents=True)
            (run/'MODEL_FREEZE_ACCOUNTING01.json').write_text('{}',encoding='utf8')
            for name in ('build_v20_release.py','reproduce_v20.py'):(pub/name).write_text('# fixture',encoding='utf8')
            marker=dict(source_root=str(root.parent/'source'),original_scientific_root=str(root.parent/'science'),
                original_research_root=str(root.parent/'research'),status='candidate',supplemental_closure=[],
                freeze_sha256=M.sha(root/M.FREEZE_REL),wrapper_sha256=M.sha(pub/'build_v20_release.py'),
                reproduction_wrapper_sha256=M.sha(pub/'reproduce_v20.py'),staged_root='historical stage location')
            (run/'provenance').mkdir()
            M.write_new(run/'provenance/STARTING_POINT.json',dict(source_root=marker['original_research_root'],
                new_root=marker['original_scientific_root']))
            M.write_new(pub/'V20_RELEASE_ROOT.json',marker)
            rows=[dict(path=p.relative_to(root).as_posix(),bytes=p.stat().st_size,sha256=M.sha(p))
                for p in root.rglob('*') if p.is_file()]
            M.write_new(pub/'V20_FILE_SELECTION.json',{'files':rows})
            self.assertEqual(M.validate_release(root)['marker']['status'],'candidate')
            (pub/'reproduce_v20.py').write_text('# changed',encoding='utf8')
            with self.assertRaises(ValueError):M.validate_release(root)


if __name__=='__main__':unittest.main(verbosity=2)
