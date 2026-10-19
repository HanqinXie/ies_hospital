"""Finite, read-only post-wait for the already authorized normal comparison.

No physical execution, registration, policy selection, raw or annual reads.
The normal physical waiter is separate and is never modified or restarted.
"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid

B=Path(__file__).resolve().parent.parent
ROOT=B.parents[1]
PANELS=('normal_E_final_r1','normal_K1_final_r1','normal_C19_final_r1')
CASES=dict(normal000='v17_N2_b4ceb5196ab287f531271081',normal001='v17_N2_e0d9a833029c50478833febd',
    normal002='v17_N2_d7ae489a9d9b2341333e4104',normal003='v17_N2_68c28ce356bdd0d883b76642')
PAIRS=(('C19','E'),('C19','K1'),('E','K1'))
END_MINUTES=dict(normal000=1440.,normal001=2880.,normal002=4320.,normal003=1440.)
MATCH_FIELDS=('source_binding_sha256','model_config_sha256','input_inventory_sha256','physical_scene_sha256',
    'initial_state_sha256','applied_exogenous_definition_sha256','window_start_minute','window_end_minute')
FREEZE_SHA='80815ef85196a05e496b48b3340a843a00079a7c0de425cbffd13c949e52e2f7'
COMPARE_SHA='428d9e950912e38ebcca21f2a49c49c4bc26f45eecc93cbfe588326d8e0134aa'


def now(): return datetime.now(timezone.utc).isoformat()


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)


def stop_check(b):
    for name in ('STOP_NORMAL_COMPATIBILITY_AUTORUN.json','STOP_NORMAL_FINAL_AUTORUN.json',
            'STOP_FROZEN_QUEUE.json','STOP_CLOSEOUT_AUTORUN_R2.json'):
        if (b/name).exists(): raise RuntimeError('Explicit stop: '+name)


def normal_ready(b):
    stop_check(b)
    result=b/'NORMAL_FINAL_AUTORUN_RESULT.json'
    if not result.exists(): return dict(ready=False,reason='waiting_for_normal_autorun_terminal')
    marker=read(result)
    if marker.get('all_succeeded') is not True: raise ValueError('Normal autorun failed; no comparison')
    queue_path=b/'FROZEN_QUEUE_NORMAL_FINAL01_RESULT.json'
    queue=read(queue_path)
    values=queue.get('results',[])
    if (queue.get('all_succeeded') is not True or queue.get('registered_pairs')!=12 or
        queue.get('registered_batches')!=12 or queue.get('normal_serial') is not True or
        queue.get('workers')!=1 or len(values)!=12 or queue.get('unstarted_batches')!=[] or
        queue.get('freeze_sha256')!=FREEZE_SHA or marker.get('normal_queue_sha256')!=sha(queue_path)):
        raise ValueError('Twelve serial normal trajectories must all be accepted and bound')
    expected={(b/'experiments'/f'{panel}_{i:03d}').resolve() for panel in PANELS for i in range(4)}
    seen=set()
    for item in values:
        directory=Path(item['directory']).resolve()
        verified=item.get('verified_terminal',{})
        if (directory not in expected or directory in seen or item.get('accepted') is not True or
            item.get('exit_code')!=0 or verified.get('registered_pairs')!=1 or
            verified.get('complete_full')!=1 or verified.get('source_unchanged') is not True or
            verified.get('batch_result_sha256')!=sha(directory/'BATCH_RESULT.json')):
            raise ValueError('Twelve serial normal terminal coverage changed or failed')
        seen.add(directory)
    if (b/'PHYSICAL_QUEUE_LEASE.json').exists():
        return dict(ready=False,reason='waiting_for_normal_scheduler_lease_release')
    return dict(ready=True,normal_autorun_sha256=sha(result),normal_queue_sha256=sha(queue_path),
        complete_normal_trajectories=12)


def comparison_argv(b):
    argv=[sys.executable,'-B',str(b/'scripts/summarize_compatibility.py'),'compare',
        '--label','normal4_final','--freeze',str(b/'MODEL_FREEZE_CLOSEOUT01.json')]
    for panel in PANELS:
        for i in range(4): argv+=['--batch',str(b/'experiments'/f'{panel}_{i:03d}')]
    for label,scene in CASES.items(): argv+=['--case',label+'='+scene]
    for left,right in PAIRS: argv+=['--pair',left+':'+right]
    return argv


def verify_proofs(report):
    expected={(case,scene,left,right) for case,scene in CASES.items() for left,right in PAIRS}
    selected=[p for p in report.get('proofs',[]) if p.get('window')=='registered_full']
    keys=[(p.get('case'),p.get('scenario_id'),p.get('left_policy'),p.get('right_policy')) for p in selected]
    if len(selected)!=12 or set(keys)!=expected: raise ValueError('Exactly twelve unique full normal proofs required')
    for proof in selected:
        a,b=proof.get('left_match_values') or {},proof.get('right_match_values') or {}
        if (proof.get('status')!='matched' or set(proof.get('required_match_fields',[]))!=set(MATCH_FIELDS) or
            any(a.get(k) is None or a.get(k)!=b.get(k) for k in MATCH_FIELDS) or
            a.get('window_start_minute')!=0 or a.get('window_end_minute')!=END_MINUTES[proof['case']] or
            len(proof.get('left_candidate_result_sha256',[]))!=1 or
            len(proof.get('right_candidate_result_sha256',[]))!=1 or
            not proof.get('left_policy_panel_sha256') or not proof.get('right_policy_panel_sha256')):
            raise ValueError('Full normal actual matching evidence missing, unknown, or unequal')
    return dict(matched_registered_full=12,unique_backgrounds=4,policy_pairs=PAIRS,
        required_actual_match_fields=MATCH_FIELDS,window='registered_full')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--max-wait-hours',type=float,default=48.)
    args=parser.parse_args()
    if not 0<args.max_wait_hours<=48: raise ValueError('Finite wait must be within 48h')
    destination=B/'NORMAL_COMPATIBILITY_AUTORUN_RESULT.json'
    output=B/'review/compatibility_normal4_final'
    command_path=B/'logs/compatibility_normal4_final.command.json'
    if destination.exists() or output.exists() or command_path.exists():
        raise FileExistsError('Existing comparison evidence cannot be overwritten or rerun')
    owner=B/'NORMAL_COMPATIBILITY_OWNER.json'
    token=dict(token=uuid.uuid4().hex,pid=os.getpid(),utc=now());write_new(owner,token)
    record=dict(schema='hospital_ies.v19.normal_compatibility_autorun.v1',utc_started=now(),
        script_sha256=sha(__file__),physical_episodes_started=0,raw_files_opened=0,
        annual_inputs_loaded=0,cases=CASES,policy_pairs=PAIRS,commands=[])
    events=B/'review/normal_admission/NORMAL_COMPATIBILITY_WAIT_EVENTS.jsonl'
    try:
        deadline=time.monotonic()+args.max_wait_hours*3600;last_event=-float('inf')
        while True:
            state=normal_ready(B)
            if time.monotonic()-last_event>=60 or state['ready']:
                with events.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(dict(utc=now(),**state))+'\n')
                last_event=time.monotonic()
            if state['ready']: break
            if time.monotonic()>=deadline: raise TimeoutError('Finite post-wait elapsed; no comparison')
            time.sleep(5)
        record['normal_trigger']=state
        if output.exists() or command_path.exists(): raise FileExistsError('Comparison appeared while waiting; preserve it')
        if sha(B/'MODEL_FREEZE_CLOSEOUT01.json')!=FREEZE_SHA or sha(B/'scripts/summarize_compatibility.py')!=COMPARE_SHA:
            raise ValueError('Reviewed freeze or read-only comparison script changed')
        for panel in PANELS:
            for i,(case,scene_id) in enumerate(CASES.items()):
                directory=B/'experiments'/f'{panel}_{i:03d}'
                plan,manifest=read(directory/'plan.json'),read(directory/'manifest.json')
                if (plan.get('registered_pairs')!=1 or plan.get('scenario_ids')!=[scene_id] or
                    len(manifest.get('scenarios',[]))!=1 or manifest['scenarios'][0]['id']!=scene_id or
                    float(manifest['scenarios'][0]['duration_steps'])*float(manifest['scenarios'][0].get('step_minutes',15.))!=END_MINUTES[case]):
                    raise ValueError('Explicit four normal registrations changed')
        argv=comparison_argv(B)
        command=[sys.executable,'-B',str(ROOT/'tools/p0_audit/run_logged.py'),
            '--run-dir',str(B),'--name','compatibility_normal4_final','--',*argv]
        stop_check(B)
        done=subprocess.run(command,cwd=ROOT,check=False)
        record['commands'].append(dict(command=command,exit_code=done.returncode))
        if done.returncode: raise RuntimeError('Read-only normal comparison failed; retain output')
        proof_path=output/'MATCH_PROOFS.json'
        record['strict_registered_full']=verify_proofs(read(proof_path))
        summary=read(output/'SUMMARY.json')
        if summary.get('freeze_file_sha256')!=FREEZE_SHA or summary.get('script_sha256')!=COMPARE_SHA:
            raise ValueError('Normal comparison summary source binding mismatch')
        record.update(all_succeeded=True,summary_path=str(output/'SUMMARY.json'),
            summary_sha256=sha(output/'SUMMARY.json'),match_proofs_path=str(proof_path),
            match_proofs_sha256=sha(proof_path),logged_command_receipt_sha256=sha(command_path))
    except Exception as error:
        import traceback
        record.update(all_succeeded=False,error=dict(type=type(error).__name__,message=str(error),traceback=traceback.format_exc()))
    finally:
        record['utc_finished']=now();write_new(destination,record)
        if read(owner)!=token: raise RuntimeError('Post-wait owner changed; preserve evidence')
        owner.unlink()
    return 0 if record.get('all_succeeded') is True else 1


if __name__=='__main__': raise SystemExit(main())
