"""Stop only our verified idle monitor; never stop a scientific child or user app."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,psutil
ROOT=Path(r'D:\hospital_ies_v20_20261019')
RUN=ROOT/'outputs/v20_load_accounting_20261019_run01'
HERE=RUN/'review/normal008_continuation01'
OUT=HERE/'L72_RUN01'
def now():return datetime.now(timezone.utc).isoformat()
def require(v,m):
    if not v:raise ValueError(m)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def write_new(p,v):
    with p.open('x',encoding='utf8') as f:json.dump(v,f,ensure_ascii=False,indent=2,allow_nan=False)
source=ROOT/'publication/continue_normal008.py'
require(sha(source)=='68701e844ffca923e91f82fff52116d4b18a4357f1239d5c4a416135c8ed95a3','Running source changed')
start=read(OUT/'START.json')
require(start['pid']==62496 and start['script_sha256']==sha(source),'Owned monitor identity differs')
require(not (OUT/'ADMISSION.json').exists() and not (OUT/'EVENTS.jsonl').exists(),'Launch may have started')
target=RUN/'experiments/v20_normal_008'
def no_science():
    require(not any((target/n).exists() for n in ('ATTEMPTS.jsonl','pairs','BATCH_RESULT.json')),'008 has execution evidence')
    for x in psutil.process_iter(['name','cmdline']):
        if not (x.info['name'] or '').lower().startswith('python'):continue
        args=x.info['cmdline'] or []
        require(not any(args[i:i+3]==['-m','tools.load_accounting.runner','run'] for i in range(len(args)-2)),'A science runner is active')
no_science()
p=psutil.Process(start['pid']);args=p.cmdline();created=p.create_time()
require(p.cwd()==str(ROOT) and 'publication/continue_normal008.py' in args
    and '--run' in args and 'L72_RUN01' in args and not p.children(recursive=True),'Owned monitor command/children differ')
samples=read_samples=[json.loads(line) for line in (OUT/'ADMISSION_SAMPLES.jsonl').read_text(encoding='utf8').splitlines()]
gaps=[b['monotonic']-a['monotonic'] for a,b in zip(samples,samples[1:])]
require(len(gaps)>10 and min(gaps)>3,'Unexpected actual admission cadence; re-evaluate before stopping')
intent=dict(schema='hospital_ies.v20.idle_cadence_monitor_stop.v1',utc=now(),pid=p.pid,create_time=created,
    command=args,reason='Measured per-loop process enumeration plus fixed sleep violates required <=3s observation gap; no science admitted',
    action='suspend owned idle monitor; recheck no child/attempt; terminate only this monitor; preserve source and all records',
    source_sha256=sha(source),sample_count=len(samples),minimum_gap_seconds=min(gaps),maximum_gap_seconds=max(gaps),
    scientific_episodes_started=0,no_user_application_or_system_setting_changed=True)
write_new(OUT/'IDLE_STOP_INTENT.json',intent)
suspended=False;terminated=False
try:
    p.suspend();suspended=True
    require(p.create_time()==created and p.cmdline()==args and not p.children(recursive=True),'Owned suspended process changed')
    require(not (OUT/'ADMISSION.json').exists() and not (OUT/'EVENTS.jsonl').exists(),'Launch evidence appeared')
    no_science()
    with (OUT/'IMPLEMENTATION_AT_RUN01.py').open('xb') as f:f.write(source.read_bytes())
    with (OUT/'TESTS_AT_RUN01.py').open('xb') as f:f.write((ROOT/'publication/test_continue_normal008.py').read_bytes())
    p.terminate();terminated=True
    code=p.wait(timeout=30)
finally:
    if suspended and not terminated:
        try:p.resume()
        except psutil.NoSuchProcess:pass
no_science()
result=dict(intent,finished_utc=now(),status='owned_idle_monitor_stopped_no_science',
    actual_wait_exit_code=code,pid_no_longer_exists=not psutil.pid_exists(p.pid),
    preserved_implementation_sha256=sha(OUT/'IMPLEMENTATION_AT_RUN01.py'),
    no_008_attempt_after_stop=True,old_S3_and_normal03_records_not_modified=True)
write_new(OUT/'IDLE_STOP_OUTCOME.json',result)
print(json.dumps(result,ensure_ascii=True))
