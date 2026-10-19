"""Run the 13-file V19 targeted gate, retaining raw output and exit codes."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
TESTS=[
    'test_model_closeout_analysis.py','test_model_closeout_contacts_review.py',
    'test_model_closeout_contact_semantics.py','test_model_closeout_emissions.py',
    'test_model_closeout_environment.py','test_model_closeout_execution.py',
    'test_model_closeout_numerics.py','test_model_closeout_numerics_guards.py',
    'test_model_closeout_registration.py','test_model_closeout_reserve.py',
    'test_execution_recovery_numerics.py','test_execution_recovery_runner.py',
    'test_execution_recovery_freeze.py']

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',type=Path)
    a=parser.parse_args()
    out=(a.output_root or ROOT/'publication_checks'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')).resolve()
    if os.name=='nt' and out.drive.lower() not in ('d:','e:'):
        raise ValueError('Use a new D/E output directory')
    out.mkdir(parents=True,exist_ok=False)
    temp=out/'temp';temp.mkdir()
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',
        PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT),TEMP=str(temp),TMP=str(temp))
    commands=[
        [sys.executable,'-B',str(ROOT/'scripts/repository_assets.py'),'verify'],
        [sys.executable,'-B','-m','pytest','-q','--basetemp',str(out/'pytest_tmp'),
            *['tests/'+n for n in TESTS]],
        [sys.executable,'-B',str(ROOT/'scripts/repository_assets.py'),'verify']]
    receipts=[]
    for i,cmd in enumerate(commands):
        start=datetime.now(timezone.utc).isoformat()
        with (out/('command_'+str(i)+'.log')).open('wb') as log:
            done=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=False)
        receipt=dict(command=cmd,cwd=str(ROOT),started_utc=start,
            finished_utc=datetime.now(timezone.utc).isoformat(),exit_code=done.returncode,
            raw_log='command_'+str(i)+'.log')
        receipts.append(receipt)
        print((out/receipt['raw_log']).read_text(encoding='utf-8',errors='replace'),flush=True)
        if done.returncode: break
    code=next((r['exit_code'] for r in receipts if r['exit_code']),0)
    (out/'COMMANDS.json').write_text(json.dumps(dict(commands=receipts,exit_code=code,
        targeted_test_files=len(TESTS),full_historical_suite_executed=False),indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(output_root=str(out),exit_code=code)))
    return code

if __name__=='__main__': raise SystemExit(main())
