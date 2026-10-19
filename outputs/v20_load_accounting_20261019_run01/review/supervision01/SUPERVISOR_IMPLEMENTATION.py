"""Observe authorized comparisons, then serialize normal windows and readback."""
from datetime import datetime, timezone
from pathlib import Path
import ctypes, importlib.util, json, os, subprocess, sys, time

ROOT=Path(r'D:\hospital_ies_v20_20261019')
RUN=ROOT/'outputs/v20_load_accounting_20261019_run01'
OUT=RUN/'review/supervision01'
OUT.mkdir(parents=True,exist_ok=False)
spec=importlib.util.spec_from_file_location('frozen_campaign',ROOT/'scripts/v20_campaign.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
began=time.monotonic();phase='comparisons';normal=None;report=None

def now():return datetime.now(timezone.utc).isoformat()
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def save(name,value):
 with (OUT/name).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def emit(event,**values):
 row=dict(utc=now(),event=event,phase=phase,**values)
 with (OUT/'EVENTS.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
 print(json.dumps(row,ensure_ascii=True),flush=True)
def checkpoint():
 starts=ends=0;bad=[]
 for path in (RUN/'experiments').glob('v20_*/ATTEMPTS.jsonl'):
  for line in path.read_text(encoding='utf8').splitlines():
   row=json.loads(line)
   starts+=row.get('event')=='started';ends+=row.get('event')=='finished'
   if row.get('event')=='finished' and row.get('status')!='completed':bad.append(str(path))
 return dict(registered=406,actual_started=starts,actual_finished=ends,active=starts-ends,
             not_started=406-starts,failed_terminal_journals=bad,available_GiB=round(c.available_memory()/1024**3,2))
def launch(name,args):
 command=[sys.executable,'-B',r'D:\hospital_v20_capture_20261019.py','--name',name,'--',*args]
 log=(OUT/(name+'.capture.log')).open('xb')
 proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
 emit('launched',command=command,pid=proc.pid)
 return proc,log
def finish_failure(reason):
 emit('stopped',reason=reason,checkpoint=checkpoint())
 save('RESULT.json',dict(status='failed_unknown',reason=reason,finished_utc=now(),checkpoint=checkpoint(),
     all_attempts_preserved=True,source_modified=False));return 2

try:
 last=-1000.
 while True:
  if time.monotonic()-began>48*3600:raise RuntimeError('Supervisor wall-clock ceiling reached')
  if time.monotonic()-last>=45:
   emit('checkpoint',**checkpoint());last=time.monotonic()
  if phase=='comparisons':
   receipt=RUN/'logs/comparisons01.command.json'
   if receipt.is_file():
    result=read(receipt)
    if result['exit_code']!=0:raise RuntimeError('Comparison process failed; dependent normal stage retained')
    phase='waiting_normal_memory';emit('comparisons_finished',exit_code=result['exit_code'])
  elif phase=='waiting_normal_memory':
   state=checkpoint()
   if state['active']!=0:raise RuntimeError('Comparison journal has unfinished attempts')
   if state['available_GiB']>=32.:
    normal=launch('normal01',['python','-B','scripts/v20_campaign.py','run','--label','final01',
         '--phase','normal','--workers','1','--reserve-gib','6','--job-reserve-gib','26'])
    phase='normal'
  elif phase=='normal':
   code=normal[0].poll()
   if code is not None:
    normal[1].close()
    if code!=0:raise RuntimeError('Normal process failed; all actual records retained')
    phase='readback'
    report=launch('final_readback01',['python','-B','scripts/v20_report.py','--label','FINAL01',
       '--baseline-root',r'D:\云大文件\论文\医院ies\code_4_2'])
  elif phase=='readback':
   code=report[0].poll()
   if code is not None:
    report[1].close()
    if code!=0:raise RuntimeError('Final readback failed')
    state=checkpoint()
    if state['actual_finished']!=406 or state['active']!=0 or state['failed_terminal_journals']:
     raise RuntimeError('Canonical 406 terminal denominator not fulfilled')
    jobs=read(RUN/'design/CAMPAIGN_final01.json')['jobs'];issues={}
    for job in jobs:
     directory=RUN/'experiments'/job['name'];problem=c.terminal_issues(directory,read(directory/'BATCH_RESULT.json'))
     if problem:issues[job['name']]=problem
    if issues:raise RuntimeError('Strict final batch qualification failed: '+json.dumps(issues))
    result=dict(status='passed',finished_utc=now(),checkpoint=state,strict_batches=len(jobs),
       report_directory=str(RUN/'paper_results/FINAL01'),source_modified=False,
       final_report_review_and_git_push_not_performed_by_supervisor=True)
    save('RESULT.json',result);emit('completed',**result);break
  time.sleep(10)
except Exception as exc:
 raise SystemExit(finish_failure(type(exc).__name__+': '+str(exc)))
