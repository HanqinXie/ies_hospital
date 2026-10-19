"""Short-lived readback of the real graceful stop and current resource counters."""
import ast, ctypes, hashlib, json, os
from pathlib import Path
from datetime import datetime, timezone
import psutil
ROOT=Path(r'D:\hospital_ies_v20_20261019')
RUN=ROOT/'outputs/v20_load_accounting_20261019_run01'
HERE=RUN/'review/normal008_continuation01'
def now():return datetime.now(timezone.utc).isoformat()
def require(v,m):
    if not v:raise ValueError(m)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
helper=ROOT/'publication/run_normal_serial.py'
require(sha(helper)=='50ab72194abf63dda9c1893595629f57d0b3ceb488eb2e64ff709fe273f8701b','Old helper changed')
function=next(n for n in ast.parse(helper.read_text(encoding='utf8')).body if isinstance(n,ast.FunctionDef) and n.name=='windows_memory')
exec(compile(ast.Module(body=[function],type_ignores=[]),str(helper),'exec'),globals())
capture=RUN/'logs/normal03.command.json'
s3path=RUN/'review/supervision03/RESULT.json'
summary=RUN/'provenance/normal_resource_profile/supervision_canonical02/SUMMARY.json'
if not summary.exists():
    matches=list((RUN/'provenance/normal_resource_profile').rglob('SUMMARY.json'))
    matches=[p for p in matches if 'canonical02' in str(p)]
    require(len(matches)==1,'Unique old monitor summary missing')
    summary=matches[0]
command=read(capture);s3=read(s3path);old=read(summary)
require(command['exit_code']==125 and old['exit_code']==125 and s3['status']=='failed_unknown','Real old stop chain differs')
require(s3['checkpoint']['actual_finished']==405 and s3['checkpoint']['active']==0,'Stop chain checkpoint differs')
require(not any(psutil.pid_exists(pid) for pid in (9504,33752,12804)),'An old owned process remains')
rows=[dict(path=p.relative_to(ROOT).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in
    (capture,s3path,summary,RUN/'provenance/normal_resource_profile/STOP_NORMAL_SERIAL.json',HERE/'PRE_STOP_405_PROOF01.json',HERE/'PRE405_IMPLEMENTATION.py')]
record=dict(schema='hospital_ies.v20.normal008_old_handoff_observation.v1',status='old_monitor_stopped_not_final_scientific_success',
    utc=now(),evidence=rows, old_helper_actual_exit_code=125,old_supervisor_status='failed_unknown',
    outer_root_exec_observed_exit_code=1,outer_exit_observation_source='functions.write_stdin session66840',
    old_owned_processes_exited=[9504,33752,12804],checkpoint=s3['checkpoint'],memory_after_old_exit=windows_memory(),
    only_not_started='v20_normal_008',physical_episodes_started=0, global_settings_changed=False)
out=HERE/'OLD_HANDOFF_OUTCOME01.json'
with out.open('x',encoding='utf8') as f:json.dump(record,f,ensure_ascii=False,indent=2,allow_nan=False)
print(json.dumps(record,ensure_ascii=True))
