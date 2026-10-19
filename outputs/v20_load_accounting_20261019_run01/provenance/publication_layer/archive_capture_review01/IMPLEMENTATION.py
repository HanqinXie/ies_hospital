"""Capture final archive command outside the clone and its selected check logs."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, os, subprocess, sys, time

ROOT=Path(r'D:\hospital_ies_v20_github_fresh_20261019')
OUT=Path(r'D:\hospital_ies_v20_delivery_20261019')
TARGET=OUT/'Hospital_IES_V20_20261019.zip'
EVIDENCE=OUT/'archive01_evidence'
assert ROOT.is_dir() and (ROOT/'.git').is_dir()
assert json.loads((ROOT/'publication/V20_RELEASE_ROOT.json').read_text(encoding='utf-8-sig'))['status']=='final'
assert not TARGET.exists() and not EVIDENCE.exists()
helper=ROOT/'publication/build_delivery_archive.py'
helper_sha=hashlib.sha256(helper.read_bytes()).hexdigest()
assert helper_sha=='005624fc0e0e0510668a7e7848365c1eba8015c5c63d01e1b0cca3c299bc81a7'
EVIDENCE.mkdir(parents=True)
temporary=EVIDENCE/'temp';temporary.mkdir()
env=os.environ.copy()
env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',
    PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT),TEMP=str(temporary),TMP=str(temporary))
command=[sys.executable,'-B','publication/build_delivery_archive.py','--destination',str(TARGET),'--proof-name','fresh01']
start=datetime.now(timezone.utc).isoformat();began=time.perf_counter()
intent=dict(command=command,cwd=str(ROOT),started_utc=start,archive_destination=str(TARGET),
    helper_sha256=helper_sha,capture_implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    capture_evidence_outside_clone=True,no_inflight_archive_log_in_payload=True,
    environment_overrides={k:env[k] for k in ('PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING','PYTHONPATH','TEMP','TMP')})
with (EVIDENCE/'START.json').open('x',encoding='utf8') as stream:json.dump(intent,stream,ensure_ascii=False,indent=2)
with (EVIDENCE/'command.log').open('xb') as stream:
    result=subprocess.run(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,check=False)
record=dict(intent,finished_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.perf_counter()-began,
    exit_code=result.returncode,command_log_sha256=hashlib.sha256((EVIDENCE/'command.log').read_bytes()).hexdigest(),
    target_exists=TARGET.is_file(),target_bytes=TARGET.stat().st_size if TARGET.is_file() else None)
with (EVIDENCE/'COMMAND.json').open('x',encoding='utf8') as stream:json.dump(record,stream,ensure_ascii=False,indent=2)
print(json.dumps(record,ensure_ascii=True))
print((EVIDENCE/'command.log').read_text(encoding='utf8',errors='replace')[-5000:])
raise SystemExit(result.returncode)
