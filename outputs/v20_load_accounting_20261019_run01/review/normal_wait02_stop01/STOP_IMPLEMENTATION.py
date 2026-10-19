"""Withdraw only the identified, non-scientific read-only analysis waiter."""
from pathlib import Path
from datetime import datetime, timezone
import ast, ctypes, hashlib, json, os, psutil, time

ROOT=Path(r'D:\hospital_ies_v20_20261019').resolve()
RUN=ROOT/'outputs/v20_load_accounting_20261019_run01'
ANA=RUN/'review/normal_window_analysis02'
OUT=RUN/'review/normal_wait02_stop01'
OUT.mkdir(exist_ok=True)
assert not (OUT/'RESULT.json').exists() and not (OUT/'INTENT.json').exists()
def now():return datetime.now(timezone.utc).isoformat()
def write(name,value):
    with (OUT/name).open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def require(condition,message):
    if not condition:raise RuntimeError(message)

# Retain the failed, pre-action counter-name lookup; no process was touched.
write('FAILED_PREPARATION_ATTEMPT01.json',dict(error="AttributeError: resource_state_reader has no attribute memory_snapshot",
    actual_command_exit_code=1,source='prior root stdin command',process_identity_guard_reached=False,
    process_termination_attempted=False,utc=now()))

# Use only the reviewed pure Windows-counter function, avoiding its unrelated
# numerical imports and their committed-memory footprint in this short utility.
counter_source=ROOT/'publication/run_normal_serial.py'
require(sha(counter_source)=='50ab72194abf63dda9c1893595629f57d0b3ceb488eb2e64ff709fe273f8701b','Counter source changed')
tree=ast.parse(counter_source.read_text(encoding='utf-8-sig'))
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='windows_memory')
exec(compile(ast.Module(body=[node],type_ignores=[]),str(counter_source),'exec'))
before=windows_memory()
helper=ANA/'normal_window_analysis.py'
require(sha(helper)=='ff04ccf7c5d93ce98bfeac6455c43e140ce3447cf1e84d5b61b9dadf53cd2b62','Analysis source changed')

def verify_identity():
    process=psutil.Process(82524);parent=psutil.Process(19424)
    require(abs(process.create_time()-1792404054.6227684)<0.00001,'Waiter PID was reused')
    require(abs(parent.create_time()-1792404054.5880444)<0.00001,'Capture PID was reused')
    require(Path(process.exe()).resolve()==Path(r'C:\Users\HanqinXie\AppData\Local\Programs\Python\Python310\python.exe').resolve(),'Wrong interpreter')
    require(Path(process.cwd()).resolve()==ROOT and process.ppid()==19424,'Wrong project or parent')
    command=process.cmdline()
    require(command[1:]==['-B','outputs/v20_load_accounting_20261019_run01/review/normal_window_analysis02/normal_window_analysis.py','--wait','--poll-seconds','30','--max-wait-hours','48'],'Wrong waiter command')
    parent_command=parent.cmdline()
    require('--name' in parent_command and parent_command[parent_command.index('--name')+1]=='normal_analysis_wait02','Wrong capture command')
    require(not process.children(),'Waiter unexpectedly has children')
    require(not list(ANA.glob('*.csv')) and not (ANA/'RESULT.json').exists() and not (ANA/'COLLECTION_RECEIPT.json').exists(),'Analysis may have begun')
    terminals=list((RUN/'experiments').glob('v20_normal_*/BATCH_RESULT.json'))
    require(len(terminals)<12,'Normal results are now ready; do not interrupt collection')
    return process,dict(pid=process.pid,create_time=process.create_time(),exe=process.exe(),command=command,
        cwd=process.cwd(),parent_pid=process.ppid(),parent_create_time=parent.create_time(),children=[],
        private_bytes=process.memory_info().private,normal_terminal_count=len(terminals))

process,identity=verify_identity();snapshots=[]
for source in [ANA/'READONLY_OWNER.json',ANA/'PREPARATION.json',ANA/'PREPARATION_VALIDATION_RECEIPT01.json',
               ANA/'STATE_CURRENT.json',ANA/'EVENTS.jsonl',RUN/'logs/normal_analysis_wait02.log']:
    data=source.read_bytes();destination=OUT/('BEFORE_'+source.name)
    with destination.open('xb') as stream:stream.write(data)
    snapshots.append(dict(source=source.relative_to(ROOT).as_posix(),snapshot=destination.name,
        bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),snapshot_of_mutable_wait_metadata=True))
write('INTENT.json',dict(utc=now(),action='Terminate only owned readonly analysis waiter82524; let capture19424 finish naturally',
    reason='Release unnecessary committed memory; invoke the same prepared collect() once after S3 succeeds',
    identity=identity,before_memory=before,snapshot_files=snapshots,implementation_sha256=sha(Path(__file__)),
    scientific_or_user_app_termination=False,old_owner_and_evidence_preserved=True))

process,identity2=verify_identity()
process.terminate()
exit_code=process.wait(timeout=15)
command_file=RUN/'logs/normal_analysis_wait02.command.json'
for _ in range(50):
    if command_file.is_file():break
    time.sleep(.1)
capture=json.loads(command_file.read_text(encoding='utf8')) if command_file.is_file() else None
after=windows_memory()
for source in [command_file,RUN/'logs/normal_analysis_wait02.log']:
    if source.is_file():
        with (OUT/('AFTER_'+source.name)).open('xb') as stream:stream.write(source.read_bytes())
result=dict(utc=now(),status='owned_readonly_waiter_stopped_preserve_actual_exit',identity_rechecked=identity2,
    actual_terminated_pid=82524,observed_OS_process_exit_code=exit_code,capture_record=capture,
    before_memory=before,after_memory=after,commit_headroom_observed_delta_bytes=after['commit_headroom_bytes']-before['commit_headroom_bytes'],
    memory_delta_not_attributed_exclusively_to_this_stop=True,scientific_execution_failed=False,
    normal12_complete_claimed=False,numeric_analysis_generated=bool(list(ANA.glob('*.csv'))),
    READONLY_OWNER_preserved=(ANA/'READONLY_OWNER.json').is_file(),new_oneshot_collection_required_after_S3=True)
write('RESULT.json',result)
print(json.dumps(result,ensure_ascii=True))
