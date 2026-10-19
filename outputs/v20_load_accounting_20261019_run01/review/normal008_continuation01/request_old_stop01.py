"""Request only the old monitor's documented graceful launch-boundary stop."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, sys
import psutil

ROOT=Path(r'D:\hospital_ies_v20_20261019')
RUN=ROOT/'outputs/v20_load_accounting_20261019_run01'
HERE=RUN/'review/normal008_continuation01'
STOP=RUN/'provenance/normal_resource_profile/STOP_NORMAL_SERIAL.json'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def require(value, message):
    if not value:raise ValueError(message)
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
proof_path=HERE/'PRE_STOP_405_PROOF01.json'
proof=read(proof_path)
require(proof['status']=='passed' and proof['actual_complete_pairs']==405
    and proof['actual_complete_batches']==147 and proof['only_not_started']=='v20_normal_008', 'Actual405 proof missing')
command_path=RUN/'logs/normal008_prestop405_01.command.json'
require(read(command_path)['exit_code']==0, 'Pre405 command not complete')
source=ROOT/'publication/run_normal_serial.py'
require(sha(source)=='50ab72194abf63dda9c1893595629f57d0b3ceb488eb2e64ff709fe273f8701b','Old monitor changed')
target=RUN/'experiments/v20_normal_008'
require(not any((target/n).exists() for n in ('ATTEMPTS.jsonl','pairs','BATCH_RESULT.json')), '008 already started')
active=[]
for p in psutil.process_iter(['name','cmdline']):
    if not (p.info['name'] or '').lower().startswith('python'):continue
    args=p.info['cmdline'] or []
    if any(args[i:i+3]==['-m','tools.load_accounting.runner','run'] for i in range(len(args)-2)):active.append(p.pid)
require(not active, 'An actual science runner is still active')
p=psutil.Process(9504)
args=p.cmdline()
require(p.name().lower().startswith('python') and 'publication/run_normal_serial.py' in args
    and '--run' in args and 'canonical02' in args, 'Old monitor PID identity differs')
require(not p.children(recursive=True), 'Old monitor has an owned active child')
record=dict(schema='hospital_ies.v20.normal008_graceful_monitor_handoff.v1',
    utc=datetime.now(timezone.utc).isoformat(),
    reason='405 strictly qualified; replace heavy idle monitor with preregistered lightweight resource-only continuation',
    authorization='USER_TASK_20261019: arrange batches by actual memory and solve time; continuous completion',
    action='request_existing_STOP_boundary_only_no_process_termination',
    only_not_started='v20_normal_008', expected_old_helper_exit_code=125,
    old_helper_pid=p.pid, old_helper_create_time=p.create_time(), old_helper_command=args,
    old_helper_sha256=sha(source), old_helper_children=[], active_scientific_children=[],
    pre_stop_405_proof=dict(path=proof_path.relative_to(ROOT).as_posix(),sha256=sha(proof_path)),
    pre_stop_actual_command=dict(path=command_path.relative_to(ROOT).as_posix(),sha256=sha(command_path)),
    resource_plan=dict(path='docs/v20/RESOURCE_CONTINUATION_PLAN.md',sha256=sha(ROOT/'docs/v20/RESOURCE_CONTINUATION_PLAN.md')),
    prior_success_not_claimed=True, scientific_episodes_started=0,
    no_user_application_or_system_setting_changed=True)
with STOP.open('x',encoding='utf8') as stream:json.dump(record,stream,ensure_ascii=False,indent=2,allow_nan=False)
print(json.dumps(dict(status='stop_requested_not_yet_terminal',stop=str(STOP),sha256=sha(STOP),**record),ensure_ascii=True))
