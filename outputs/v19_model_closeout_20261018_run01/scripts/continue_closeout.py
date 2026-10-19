"""Continue the authorized finite closeout after all fixed development runs.

Wait for the two existing development lanes, verify every current-source
terminal, freeze once, register once, select confirmation once, then run the
non-normal frozen panels. Normal windows remain a separate serial resource
admission after this queue. No repair, retry, reseeding or policy search here.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess
import sys
import time

B = Path(__file__).resolve().parent.parent
ROOT = B.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.model_closeout import runner as r
from tools.model_closeout.gate import test_binding
from tools.model_closeout.freeze import validate_test_gate

SOURCE_DIGEST = 'f88ebed9d52dfe7d5c62d1ea994a70245063e365fb52ba80d478fc3104202823'
DEVELOPMENT = ('dev_key_r2','dev_contact_r1','dev_long_r3',
    'dev_conversion_r1','dev_prop_r1','dev_energy_r1')
FINALS = ('long_final_r1','propagation_final_r1','sensitivity_open1_r1',
    'sensitivity_contact025_r1','main_final_r1','compat_final_r1',
    'compat_long_final_r1','confirmation_final_r1')


def stop_check():
    if (B/'STOP_CLOSEOUT_AUTORUN.json').exists():
        raise RuntimeError('Explicit closeout continuation stop')


def logged(name, argv):
    stop_check()
    command = [sys.executable,'-B',str(ROOT/'tools/p0_audit/run_logged.py'),
        '--run-dir',str(B),'--name',name,'--',*argv]
    done = subprocess.run(command,cwd=ROOT,check=False)
    if done.returncode:
        raise RuntimeError('Recorded child failed: '+name+' exit='+str(done.returncode))


def main():
    destination = B/'CLOSEOUT_AUTORUN_RESULT.json'
    if destination.exists() or (B/'MODEL_FREEZE_CLOSEOUT01.json').exists():
        raise FileExistsError('One-shot continuation already has evidence')
    record = dict(utc_started=datetime.now(timezone.utc).isoformat(),
        finite_development_panels=DEVELOPMENT, non_normal_panels=FINALS,
        normal_windows_not_launched_by_this_script=True)
    try:
        while True:
            stop_check()
            paths=[B/('DEVELOPMENT_QUEUE_'+name+'_RESULT.json') for name in ('ENERGY','CONVERSION')]
            if all(p.exists() for p in paths):
                break
            time.sleep(5)
        if not all(r.read(p).get('all_succeeded') is True for p in paths):
            raise ValueError('Development serial lane did not complete')
        binding = r.source_binding()
        if r.digest(binding) != SOURCE_DIGEST:
            raise ValueError('Current source is not the reviewed development epoch')
        gate = r.read(B/'test_gates/development_gate04.json')
        validate_test_gate(gate,binding,test_binding())
        # Import only the external terminal verifier, never its main/launcher.
        import importlib.util
        location=B/'scripts/run_frozen_panels.py'
        spec=importlib.util.spec_from_file_location('v19_external_terminal_guard',location)
        guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
        checks=[]
        for name in DEVELOPMENT:
            for row in r.read(B/(name.upper()+'_REGISTRATION.json')):
                directory = r._experiment_path(Path(row['directory']))
                plan = r.read(directory/'plan.json')
                if plan['source_sha256'] != binding:
                    raise ValueError('Development source differs: '+directory.name)
                checks.append(dict(batch=directory.name,**guard.terminal_check(directory)))
        if len(checks)!=23 or sum(x['registered_pairs'] for x in checks)!=46:
            raise ValueError('Fixed development coverage is incomplete')
        record['current_source_development_checks']=checks
        record['passing_test_gate_sha256']=r.sha(B/'test_gates/development_gate04.json')
        record['older_long000_development_epoch_retained_separately']=dict(
            batch='dev_long_r2_000',not_relabelled_as_current_source=True,
            final_long000_all_three_policies_must_run_on_the_frozen_source=True)
        with (B/'review/DEVELOPMENT_CLOSEOUT_READY.json').open('x',encoding='utf-8') as stream:
            json.dump(record,stream,ensure_ascii=False,indent=2,allow_nan=False)
        logged('freeze_closeout01',[sys.executable,'-B','-m','tools.model_closeout.freeze',
            '--name','MODEL_FREEZE_CLOSEOUT01','--tests','development_gate04','--main',
            '--evaluation-config','design/EVALUATION_CONTRACT_CLOSEOUT01.json'])
        logged('register_final_panels_r1',[sys.executable,'-B',str(B/'scripts/register_final_panels.py')])
        logged('select_confirmation_final01',[sys.executable,'-B',str(B/'scripts/select_confirmation.py'),
            '--freeze','MODEL_FREEZE_CLOSEOUT01.json'])
        logged('register_confirmation_final_r1',[sys.executable,'-B','-m','tools.model_closeout.campaign',
            'register','--name','confirmation_final_r1','--design','confirmation',
            '--policies','E,K1,C19','--freeze','MODEL_FREEZE_CLOSEOUT01.json',
            '--proposal',str(B/'design/confirmation_selection_final01/CONFIRMATION_PROPOSAL.json')])
        argv=[sys.executable,'-B',str(B/'scripts/run_frozen_panels.py'),
            '--label','non_normal_final01','--workers','2']
        for name in FINALS:
            argv += ['--panel',name]
        logged('preflight_frozen_non_normal_final01',[*argv,'--plan-only'])
        logged('frozen_non_normal_final01',argv)
        record['all_succeeded']=True
    except Exception as error:
        import traceback
        record['all_succeeded']=False
        record['error']=dict(type=type(error).__name__,message=str(error),traceback=traceback.format_exc())
    record['utc_finished']=datetime.now(timezone.utc).isoformat()
    with destination.open('x',encoding='utf-8') as stream:
        json.dump(record,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return 0 if record['all_succeeded'] else 1


if __name__=='__main__':
    raise SystemExit(main())
