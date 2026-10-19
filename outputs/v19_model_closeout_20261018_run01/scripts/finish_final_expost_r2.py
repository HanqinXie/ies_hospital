"""R2 authorized, finite, fail-closed ex-post pass after all final terminals.

No registration, physical runner, retry, production edit, raw or annual read.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
import unittest
from unittest.mock import patch
import tempfile

SCRIPT=Path(__file__).resolve(); B=SCRIPT.parents[1]; ROOT=B.parents[1]
FREEZE_SHA='80815ef85196a05e496b48b3340a843a00079a7c0de425cbffd13c949e52e2f7'
NON_NORMAL=('long_final_r1','propagation_final_r1','sensitivity_open1_r1',
    'sensitivity_contact025_r1','main_final_r1','compat_final_r1','compat_long_final_r1','confirmation_final_r1')
NORMAL=('normal_E_final_r1','normal_K1_final_r1','normal_C19_final_r1')
MAIN_KEYS=(1,3,7,43,67); LONG_KEYS=(0,5)

def now():return datetime.now(timezone.utc).isoformat()
def read(path):return json.loads(Path(path).read_text(encoding='utf8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_new(path,value):
    with Path(path).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
def csv_rows(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def verify_frozen_sources(root,frozen,expected_count=345):
    root=Path(root).resolve();inventory=frozen.get('source_sha256')
    if not isinstance(inventory,dict) or len(inventory)!=expected_count:
        raise ValueError('Frozen source inventory count differs')
    for name,wanted in inventory.items():
        path=(root/name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(root):raise ValueError('Frozen source path outside root')
        if not path.is_file() or sha(path)!=wanted:raise ValueError('Frozen source changed or missing: '+name)
    binding=hashlib.sha256(json.dumps(inventory,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return dict(source_files_verified=len(inventory),source_binding_sha256=binding)

def external_dependency_paths(b,root):
    return (SCRIPT,b/'MODEL_FREEZE_CLOSEOUT01.json',b/'scripts/run_normal_when_ready.py',
        b/'scripts/summarize_compatibility.py',b/'scripts/build_energy_tradeoff_results.py',
        b/'scripts/build_paper_results.py',b/'scripts/diagnose_compact_trajectories.py',
        root/'tools/p0_audit/run_logged.py',root/'tools/model_closeout/analysis.py',
        root/'tools/model_closeout/emissions.py',root/'tools/execution_recovery/analysis.py',
        root/'tools/execution_recovery/summarize.py',root/'tools/execution_recovery/review_saved.py',
        b/'references/EMISSIONS_FACTOR_REGISTRY.json')

class FinalTests(unittest.TestCase):
    def setUp(self):
        parent=B/'review/test_tmp';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='final_expost_',dir=parent);self.b=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def put(self,name,value):
        path=self.b/name;path.parent.mkdir(parents=True,exist_ok=True);write_new(path,value)
    def test_pending_r2_never_opens_later_gates(self):
        self.assertEqual(readiness(self.b)['reason'],'waiting_for_r2_terminal')
    def test_failed_r2_is_terminal_not_a_retry(self):
        self.put('CLOSEOUT_AUTORUN_R2_RESULT.json',dict(all_succeeded=False))
        with self.assertRaisesRegex(ValueError,'R2 failed'):readiness(self.b)
    def test_failed_queue_is_seen_before_parent_r2_result_exists(self):
        self.put('FROZEN_QUEUE_NON_NORMAL_FINAL01_RESULT.json',dict(all_succeeded=False))
        with self.assertRaisesRegex(ValueError,'Non-normal queue failed'):readiness(self.b)
    def test_normal_failure_is_terminal_even_before_proof(self):
        self.put('CLOSEOUT_AUTORUN_R2_RESULT.json',dict(all_succeeded=True))
        self.put('NORMAL_FINAL_AUTORUN_RESULT.json',dict(all_succeeded=False))
        with patch(__name__+'.verify_non_normal',return_value={}):
            with self.assertRaisesRegex(ValueError,'Normal autorun failed'):readiness(self.b)
    def test_successful_queue_still_waits_for_normal_proof(self):
        self.put('CLOSEOUT_AUTORUN_R2_RESULT.json',dict(all_succeeded=True))
        self.put('NORMAL_FINAL_AUTORUN_RESULT.json',dict(all_succeeded=True))
        self.put('NORMAL_COMPATIBILITY_AUTORUN_RESULT.json',dict(all_succeeded=True))
        with patch(__name__+'.verify_non_normal',return_value={}),patch(__name__+'.verify_normal',return_value={}):
            self.assertEqual(readiness(self.b)['reason'],'waiting_for_normal_cross_panel_proof')
    def test_failed_normal_proof_marker_is_terminal_even_if_cli_succeeded(self):
        self.put('CLOSEOUT_AUTORUN_R2_RESULT.json',dict(all_succeeded=True))
        self.put('NORMAL_FINAL_AUTORUN_RESULT.json',dict(all_succeeded=True))
        self.put('NORMAL_COMPATIBILITY_AUTORUN_RESULT.json',dict(all_succeeded=False))
        with patch(__name__+'.verify_non_normal',return_value={}),patch(__name__+'.verify_normal',return_value={}):
            with self.assertRaisesRegex(ValueError,'Normal compatibility autorun failed'):readiness(self.b)
    def test_explicit_stop_never_launches(self):
        self.put('STOP_FINAL_EXPOST_AUTORUN_R2.json',{})
        with self.assertRaisesRegex(RuntimeError,'Explicit stop'):readiness(self.b)
    def test_proof_requires_all_registered_cases_and_pairs(self):
        good=[dict(scenario_id=s,window='registered_full',left_policy=a,right_policy=c,status='matched')
            for s in ('a','b','c','d') for a,c in (('C19','E'),('C19','K1'),('E','K1'))]
        require_matches(good,set('abcd'),{('C19','E'),('C19','K1'),('E','K1')})
        good[-1]['status']='unknown'
        with self.assertRaisesRegex(ValueError,'registered_full'):require_matches(good,set('abcd'),{('C19','E'),('C19','K1'),('E','K1')})
    def test_main_key_and_compat_key_scene_ids_must_agree(self):
        with self.assertRaisesRegex(ValueError,'compatibility scene'):
            match_compat([dict(scenario_id='a')],[dict(scenario_id='b')])
    def test_existing_paper_unknowns_are_not_called_complete(self):
        rows=[dict(group=g,policy=p,target_specifications=str(n),registered_specifications=str(n),
            qualified_complete_specifications=str(n),unknown_specifications='0')
            for g,n in (('main',80),('confirmation',32),('normal',4)) for p in ('E','K1','C19')]
        verify_coverage(rows)
        rows[-1]['qualified_complete_specifications']='3';rows[-1]['unknown_specifications']='1'
        with self.assertRaisesRegex(ValueError,'paper coverage'):verify_coverage(rows)

def stop_check(b):
    # The retained ex-post01 stop belongs to that superseded version only.
    for name in ('STOP_FINAL_EXPOST_AUTORUN_R2.json','STOP_CLOSEOUT_AUTORUN_R2.json','STOP_NORMAL_FINAL_AUTORUN.json','STOP_FROZEN_QUEUE.json'):
        if (b/name).exists():raise RuntimeError('Explicit stop: '+name)

def verify_non_normal(b):
    # The already-reviewed normal admission verifier only reads JSON/SHA here.
    path=b/'scripts/run_normal_when_ready.py'
    spec=importlib.util.spec_from_file_location('final_expost_admission_verifier',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.verify_non_normal(b)

def registration(b,panel):
    rows=read(b/(panel.upper()+'_REGISTRATION.json'))
    for row in rows:
        path=Path(row['directory']).resolve()
        if path.parent!=(b/'experiments').resolve():raise ValueError('Noncanonical final registration: '+panel)
    return rows

def verify_normal(b):
    path=b/'FROZEN_QUEUE_NORMAL_FINAL01_RESULT.json';value=read(path)
    if (value.get('all_succeeded') is not True or value.get('registered_pairs')!=12 or value.get('registered_batches')!=12
        or value.get('normal_serial') is not True or value.get('workers')!=1 or value.get('unstarted_batches')!=[]
        or value.get('freeze_sha256')!=FREEZE_SHA or len(value.get('results',[]))!=12):
        raise ValueError('Normal queue is not exactly twelve accepted serial terminals')
    expected={Path(r['directory']).resolve():r['registered_pairs'] for p in NORMAL for r in registration(b,p)}
    if len(expected)!=12 or set(expected.values())!={1}:raise ValueError('Normal registration changed')
    seen=set()
    for row in value['results']:
        directory=Path(row['directory']).resolve();proof=row.get('verified_terminal',{})
        if (directory not in expected or directory in seen or row.get('accepted') is not True or row.get('exit_code')!=0
            or proof.get('registered_pairs')!=1 or proof.get('complete_full')!=1 or proof.get('source_unchanged') is not True
            or proof.get('batch_result_sha256')!=sha(directory/'BATCH_RESULT.json')):
            raise ValueError('Changed/unaccepted normal terminal')
        seen.add(directory)
    command=b/'logs/frozen_normal_final01.command.json'
    if read(command).get('exit_code')!=0:raise ValueError('Normal logged queue command failed')
    return dict(verified_pairs=12,queue_sha256=sha(path),command_sha256=sha(command))

def require_matches(rows,scenes,pairs):
    selected=[r for r in rows if r.get('window')=='registered_full']
    expected={(s,*p) for s in scenes for p in pairs}
    keys=[(r.get('scenario_id'),r.get('left_policy'),r.get('right_policy')) for r in selected]
    if len(keys)!=len(expected) or set(keys)!=expected or any(r.get('status')!='matched' for r in selected):
        raise ValueError('Incomplete or unknown strict registered_full matching evidence')

def verify_output_hashes(directory,receipt):
    wanted=receipt.get('output_sha256')
    if not isinstance(wanted,dict) or not wanted:raise ValueError('Missing output SHA receipt')
    for name,wanted_sha in wanted.items():
        path=(directory/name).resolve()
        if not path.is_relative_to(directory.resolve()) or sha(path)!=wanted_sha:raise ValueError('Changed output: '+str(path))

def normal_proof(b):
    directory=b/'review/compatibility_normal4_final';proof=read(directory/'SUMMARY.json')
    freeze=read(b/'MODEL_FREEZE_CLOSEOUT01.json')
    if proof.get('freeze_signature')!=freeze['signature']:raise ValueError('Normal proof bound to another freeze')
    verify_output_hashes(directory,proof)
    scenes={r['scenario_id'] for p in NORMAL for r in registration(b,p)}
    if len(scenes)!=4:raise ValueError('Four distinct normal scenes required')
    require_matches(proof.get('comparisons',[]),scenes,{('C19','E'),('C19','K1'),('E','K1')})
    return dict(summary_sha256=sha(directory/'SUMMARY.json'),registered_full_matched=12)

def readiness(b):
    stop_check(b)
    queue=b/'FROZEN_QUEUE_NON_NORMAL_FINAL01_RESULT.json'
    if queue.exists() and read(queue).get('all_succeeded') is not True:raise ValueError('Non-normal queue failed; no ex-post launch')
    r2=b/'CLOSEOUT_AUTORUN_R2_RESULT.json'
    if not r2.exists():return dict(ready=False,reason='waiting_for_r2_terminal')
    if read(r2).get('all_succeeded') is not True:raise ValueError('R2 failed; no ex-post launch')
    non=verify_non_normal(b)
    normal=b/'NORMAL_FINAL_AUTORUN_RESULT.json'
    if not normal.exists():return dict(ready=False,reason='waiting_for_twelve_normals')
    if read(normal).get('all_succeeded') is not True:raise ValueError('Normal autorun failed; no ex-post launch')
    normal_queue=verify_normal(b)
    compatibility=b/'NORMAL_COMPATIBILITY_AUTORUN_RESULT.json'
    if not compatibility.exists():return dict(ready=False,reason='waiting_for_normal_cross_panel_proof_autorun')
    if read(compatibility).get('all_succeeded') is not True:raise ValueError('Normal compatibility autorun failed; no ex-post launch')
    if not (b/'review/compatibility_normal4_final/SUMMARY.json').exists():
        return dict(ready=False,reason='waiting_for_normal_cross_panel_proof')
    proof=normal_proof(b)
    if (b/'PHYSICAL_QUEUE_LEASE.json').exists():return dict(ready=False,reason='waiting_for_final_queue_lease_release')
    return dict(ready=True,r2_sha256=sha(r2),normal_autorun_sha256=sha(normal),
        normal_compatibility_autorun_sha256=sha(compatibility),non_normal=non,normal=normal_queue,normal_proof=proof)

def match_compat(selected,compat):
    if {r['scenario_id'] for r in selected}!={r['scenario_id'] for r in compat} or len(selected)!=len(compat):
        raise ValueError('Selected final and compatibility scene IDs differ')

def build_plan(b):
    freeze=b/'MODEL_FREEZE_CLOSEOUT01.json'
    if sha(freeze)!=FREEZE_SHA:raise ValueError('Reviewed final freeze changed')
    main=registration(b,'main_final_r1');long=registration(b,'long_final_r1')
    compat=registration(b,'compat_final_r1');compat_long=registration(b,'compat_long_final_r1')
    selected_main=[main[i] for i in MAIN_KEYS];selected_long=[long[i] for i in LONG_KEYS]
    if (len(main),len(long),len(compat),len(compat_long))!=(80,8,5,2):raise ValueError('Fixed final cohort sizes changed')
    match_compat(selected_main,compat);match_compat(selected_long,compat_long)
    targets=[dict(label='main'+str(i).zfill(3),**main[i]) for i in MAIN_KEYS]
    targets += [dict(label='long'+str(i).zfill(3),**long[i]) for i in LONG_KEYS]
    prop=registration(b,'propagation_final_r1');contact=registration(b,'sensitivity_contact025_r1')
    targets += [dict(label='prop'+str(i),**prop[i]) for i in (0,1)]
    targets += [dict(label='contact025',**contact[0])]
    timeline_ids=list(dict.fromkeys(r['scenario_id'] for r in targets))
    common=[sys.executable,'-B']
    summary=[*common,'-m','tools.model_closeout.analysis','summarize','--label','ALL_FINAL01','--batch-glob','*']
    for scene in timeline_ids:summary += ['--timeline-scenario',scene]
    keys=[*common,str(b/'scripts/summarize_compatibility.py'),'compare','--label','key7_final','--freeze',str(freeze)]
    for row in selected_main+compat+selected_long+compat_long:keys += ['--batch',row['directory']]
    for i,row in zip(MAIN_KEYS,selected_main):keys += ['--case','main'+str(i).zfill(3)+'='+row['scenario_id']]
    for i,row in zip(LONG_KEYS,selected_long):keys += ['--case','long'+str(i).zfill(3)+'='+row['scenario_id']]
    energy=[*common,str(b/'scripts/build_energy_tradeoff_results.py'),'build','--cohort','frozen_final',
        '--name','long_final_complete01','--freeze',str(freeze)]
    for row in long:energy += ['--batch',Path(row['directory']).name]
    paper=[*common,str(b/'scripts/build_paper_results.py'),'build','--label','FINAL01','--summary',str(b/'review/summary_ALL_FINAL01'),
        '--freeze',str(freeze),'--confirmation-prefix','confirmation_final_r1','--normal-proof',str(b/'review/compatibility_normal4_final')]
    stages=[dict(name='B19_expost_summary_ALL_FINAL01',argv=summary,output=str(b/'review/summary_ALL_FINAL01')),
        dict(name='B19_expost_compatibility_key7_final',argv=keys,output=str(b/'review/compatibility_key7_final')),
        dict(name='B19_expost_long_energy_final01',argv=energy,output=str(b/'review/energy_tradeoff/long_final_complete01')),
        dict(name='B19_expost_paper_FINAL01',argv=paper,output=str(b/'paper_results/FINAL01'))]
    return dict(timeline_targets=targets,timeline_unique_ids=timeline_ids,key_cases={r['label']:r['scenario_id'] for r in targets[:7]},
        key_registered_batches=14,key_pairs=[['C19','C18_M19'],['C19','E'],['C19','K1'],['C18_M19','E'],['C18_M19','K1']],
        final_long_batches=8,final_long_policy_outcomes=24,stages=stages)

def verify_coverage(rows):
    expected={(g,p):n for g,n in (('main',80),('confirmation',32),('normal',4)) for p in ('E','K1','C19')}
    if len(rows)!=len(expected):raise ValueError('Incomplete paper coverage rows')
    for row in rows:
        key=(row.get('group'),row.get('policy'));n=expected.pop(key,None)
        if n is None or any(int(row[k])!=n for k in ('target_specifications','registered_specifications','qualified_complete_specifications')) or int(row['unknown_specifications'])!=0:
            raise ValueError('Unqualified or unknown final paper coverage')

def verify_backgrounds(coverage,background_rows):
    policies={'E','K1','C19'};classes=dict.fromkeys(('L1','L2','L3','L4'),1)
    contexts=[r for r in coverage if r.get('group')=='confirmation']
    if len(contexts)!=3 or {r.get('policy') for r in contexts}!=policies:
        raise ValueError('Missing confirmation background cohort rows')
    for row in contexts:
        if (int(row['registered_backgrounds'])!=8 or int(row['target_backgrounds'])!=8
            or int(row['expected_mechanisms_per_background'])!=4):
            raise ValueError('Actual confirmation cohort background count differs')
    backgrounds={r.get('background_id') for r in background_rows}
    if len(backgrounds)!=8 or any(not isinstance(b,str) or not b for b in backgrounds) or len(background_rows)!=24:
        raise ValueError('Actual confirmation background table is not eight by three policies')
    expected={(b,p) for b in backgrounds for p in policies};seen=set()
    for row in background_rows:
        key=(row.get('background_id'),row.get('policy'))
        if key not in expected or key in seen:raise ValueError('Duplicate or foreign confirmation background/policy')
        seen.add(key)
        if (int(row['target_backgrounds'])!=8 or int(row['target_mechanisms_in_background'])!=4
            or int(row['registered_mechanisms'])!=4 or int(row['qualified_complete_mechanisms'])!=4
            or str(row.get('background_all_four_complete')).lower()!='true'):
            raise ValueError('Incomplete actual confirmation background mechanisms')
        actual=row.get('canonical_class_counts')
        if isinstance(actual,str):actual=json.loads(actual)
        if actual!=classes:raise ValueError('Background does not have all four canonical classes exactly once')
    return dict(verified_registered_backgrounds=len(backgrounds),background_ids=sorted(backgrounds),
        complete_mechanisms_per_policy={p:sum(int(r['qualified_complete_mechanisms']) for r in background_rows if r['policy']==p) for p in sorted(policies)},actual_background_policy_rows=len(seen),
        independent_design_is_registered_not_inferred_from_row_count=True)

def validate_stage(index,b,plan):
    out=Path(plan['stages'][index]['output'])
    if index==0:
        proof=read(out/'SUMMARY_RECEIPT.json');verify_output_hashes(out,proof)
        return dict(receipt_sha256=sha(out/'SUMMARY_RECEIPT.json'),table_row_counts=proof['table_row_counts'])
    if index==1:
        proof=read(out/'SUMMARY.json');verify_output_hashes(out,proof)
        require_matches(proof['comparisons'],set(plan['key_cases'].values()),{tuple(p) for p in plan['key_pairs']})
        return dict(summary_sha256=sha(out/'SUMMARY.json'),registered_full_matched=35,all_comparison_counts=proof['comparison_counts'])
    if index==2:
        proof=read(out/'RECEIPT.json');verify_output_hashes(out,proof)
        if (proof.get('cohort')!='frozen_final' or proof.get('registered_policy_outcomes')!=24
            or proof.get('eligible_complete_policy_outcomes')!=24 or proof.get('matched_policy_case_comparisons')!=16 or proof.get('issues')!=[]):
            raise ValueError('Final long energy table is incomplete or unknown')
        return dict(receipt_sha256=sha(out/'RECEIPT.json'),registered_policy_outcomes=24,within_panel_C19_E_or_K1_pairs=16)
    proof=read(out/'PAPER_RESULTS_RECEIPT.json');verify_output_hashes(out,proof)
    coverage=csv_rows(out/'COHORT_COVERAGE.csv');verify_coverage(coverage)
    backgrounds=verify_backgrounds(coverage,csv_rows(out/'CONFIRMATION_BACKGROUND_COVERAGE.csv'))
    if (proof.get('main_specifications'),proof.get('confirmation_specifications'),proof.get('confirmation_backgrounds'))!=(80,32,8):
        raise ValueError('Fixed final paper denominators changed')
    return dict(receipt_sha256=sha(out/'PAPER_RESULTS_RECEIPT.json'),qualified_main=80,qualified_confirmation_mechanisms=32,
        registered_confirmation_background_verification=backgrounds,qualified_normal_per_policy=4)

def run(args):
    out=B/'review/final_expost_autorun_r2';out.mkdir(parents=True,exist_ok=True)
    result_path=B/'FINAL_EXPOST_AUTORUN_R2_RESULT.json'
    if result_path.exists():raise FileExistsError(result_path)
    write_new(out/'OWNER.json',dict(utc=now(),script_sha256=sha(SCRIPT),no_retry=True))
    record=dict(schema='hospital_ies.v19.final_expost_autorun.v2',utc_started=now(),script_sha256=sha(SCRIPT),
        finite_wait_hours=args.max_wait_hours,physical_episodes_started=0,raw_files_opened=0,annual_inputs_loaded=0,stages=[])
    try:
        plan=build_plan(B);write_new(out/'PLAN.json',plan)
        frozen=read(B/'MODEL_FREEZE_CLOSEOUT01.json')
        record['frozen_source_at_start']=verify_frozen_sources(ROOT,frozen)
        binding={str(p):sha(p) for p in external_dependency_paths(B,ROOT)}
        record['postprocessing_bindings']=binding
        for stage in plan['stages']:
            if Path(stage['output']).exists() or (B/'logs'/(stage['name']+'.command.json')).exists():
                raise FileExistsError('Existing output/command cannot be retried: '+stage['name'])
        deadline=time.monotonic()+args.max_wait_hours*3600;last_reason=None;last_log=-float('inf')
        while True:
            state=readiness(B)
            if state.get('reason')!=last_reason or time.monotonic()-last_log>=60 or state['ready']:
                with (out/'WAIT_EVENTS.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(dict(utc=now(),**state))+'\n')
                print(json.dumps(dict(utc=now(),**state)),flush=True);last_reason=state.get('reason');last_log=time.monotonic()
            if state['ready']:break
            if time.monotonic()>=deadline:raise TimeoutError('Finite final-table readiness wait elapsed')
            time.sleep(5)
        record['trigger']=state
        for index,stage in enumerate(plan['stages']):
            stop_check(B)
            if any(sha(Path(p))!=value for p,value in binding.items()):raise ValueError('Postprocessing source/input binding changed')
            frozen_before=verify_frozen_sources(ROOT,frozen)
            if not readiness(B)['ready']:raise ValueError('Final terminal readiness changed')
            argv=[sys.executable,str(ROOT/'tools/p0_audit/run_logged.py'),'--run-dir',str(B),'--name',stage['name'],'--',*stage['argv']]
            value=subprocess.run(argv,cwd=ROOT,check=False)
            receipt=dict(name=stage['name'],exit_code=value.returncode,command=argv,frozen_source_before=frozen_before)
            record['stages'].append(receipt)
            if value.returncode:raise RuntimeError('Recorded postprocessing command failed: '+stage['name'])
            receipt['verification']=validate_stage(index,B,plan)
            if any(sha(Path(p))!=value for p,value in binding.items()):raise ValueError('Postprocessing source/input binding changed during command')
            receipt['postprocessing_bindings_unchanged_after']=True
            receipt['frozen_source_after']=verify_frozen_sources(ROOT,frozen)
        record['all_succeeded']=True
    except Exception as error:
        record.update(all_succeeded=False,error=dict(type=type(error).__name__,message=str(error),traceback=traceback.format_exc()))
    record['utc_finished']=now();write_new(result_path,record)
    print(json.dumps(dict(all_succeeded=record['all_succeeded'],result=str(result_path))),flush=True)
    return 0 if record['all_succeeded'] else 1

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('self-test');sub.add_parser('plan');sub.add_parser('verify-current-source')
    wait=sub.add_parser('wait-and-run');wait.add_argument('--max-wait-hours',type=float,default=48.)
    a=p.parse_args()
    if a.command=='self-test':
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FinalTests))
        return 0 if result.wasSuccessful() else 1
    if a.command=='plan':print(json.dumps(build_plan(B),ensure_ascii=False,indent=2));return 0
    if a.command=='verify-current-source':
        if sha(B/'MODEL_FREEZE_CLOSEOUT01.json')!=FREEZE_SHA:raise ValueError('Reviewed final freeze changed')
        print(json.dumps(verify_frozen_sources(ROOT,read(B/'MODEL_FREEZE_CLOSEOUT01.json'))));return 0
    if not 0<a.max_wait_hours<=48:raise ValueError('Finite wait in (0,48] hours required')
    return run(a)

if __name__=='__main__':raise SystemExit(main())
