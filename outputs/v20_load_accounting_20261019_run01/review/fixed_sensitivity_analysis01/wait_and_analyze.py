"""Bounded terminal waiter; launches only frozen saved-output reporting/analysis."""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import json
import os
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
RUN=ROOT/'outputs/v20_load_accounting_20261019_run01'
CAPTURE=Path('D:/hospital_v20_capture_20261019.py')
BASELINE=Path('D:/\u4e91\u5927\u6587\u4ef6/\u8bba\u6587/\u533b\u9662ies/code_4_2')
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from scripts.v20_campaign import campaign_jobs

def now():return datetime.now(timezone.utc).isoformat()

def write_new(path,value):
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,indent=2,allow_nan=False)

def command(name,argv):
    start=now();began=time.perf_counter()
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT))
    with (HERE/(name+'.log')).open('xb') as log:
        process=subprocess.run(argv,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=False)
    receipt=dict(name=name,command=argv,cwd=str(ROOT),started_utc=start,finished_utc=now(),exit_code=process.returncode,elapsed_seconds=time.perf_counter()-began)
    write_new(HERE/(name+'.command.json'),receipt)
    print(json.dumps(receipt),flush=True)
    return process.returncode

def main(max_hours):
    require_targets={'fixed8':32,'propagation':6,'open1':2,'contact025':1}
    targets=[j for j in campaign_jobs() if j['cohort'] in require_targets]
    if len(targets)!=41 or sum(len(j['policies']) for j in targets)!=123:raise ValueError('Wrong waiter denominator')
    if not CAPTURE.is_file() or not BASELINE.is_dir():raise FileNotFoundError('Required capture/original baseline unavailable')
    for path in (HERE/'WAIT_STARTED.json',HERE/'WAIT_RESULT.json',HERE/'PROOF.json',RUN/'paper_results/FIXED_SENS_VERIFIED01',RUN/'logs/fixed_sensitivity_readback01.command.json',RUN/'temp/fixed_sensitivity_readback01'):
        if path.exists():raise FileExistsError('Prior attempt/evidence retained; do not silently restart: '+str(path))
    started=now();began=time.monotonic();last=None
    write_new(HERE/'WAIT_STARTED.json',dict(started_utc=started,max_hours=max_hours,targets=[j['name'] for j in targets],physical_processes_allowed=0,baseline_root=str(BASELINE),capture_path=str(CAPTURE)))
    while True:
        finished=[j for j in targets if (RUN/'experiments'/j['name']/'BATCH_RESULT.json').is_file()]
        state={c:sum(j['cohort']==c for j in finished) for c in require_targets}
        if state!=last:
            print(json.dumps(dict(event='terminal_count_changed',utc=now(),terminal_batches=len(finished),expected_batches=41,cohorts=state)),flush=True)
            last=state
        if len(finished)==41:break
        if time.monotonic()-began>=max_hours*3600:
            write_new(HERE/'WAIT_RESULT.json',dict(status='timeout_unknown_not_complete',started_utc=started,finished_utc=now(),cohorts=state,remaining=[j['name'] for j in targets if j not in finished],physical_episodes_started=0))
            return 2
        time.sleep(30)
    readback=[sys.executable,'-B',str(CAPTURE),'--name','fixed_sensitivity_readback01','--',sys.executable,'-B','scripts/v20_report.py','--label','FIXED_SENS_VERIFIED01','--baseline-root',str(BASELINE)]
    code=command('readback01',readback)
    if code==0:code=command('analysis01',[sys.executable,'-B',str(HERE/'analyze.py')])
    accepted=False
    if code==0:
        proof=json.loads((HERE/'PROOF.json').read_text(encoding='utf-8'))
        accepted=proof.get('status')=='passed' and proof.get('target_pairs')==123 and proof.get('target_batches')==41
    write_new(HERE/'WAIT_RESULT.json',dict(status='completed_readonly_collection' if accepted else 'failed_stop_no_retry',started_utc=started,finished_utc=now(),terminal_batches=41,target_pairs=123,exit_code=code,accepted=accepted,physical_episodes_started=0,raw_opened=0,annual_values_read=0))
    return 0 if accepted else 2

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--max-hours',type=float,default=3.);args=parser.parse_args()
    if not 0<args.max_hours<=4:raise ValueError('Bounded wait must be 0 to 4 hours')
    raise SystemExit(main(args.max_hours))
