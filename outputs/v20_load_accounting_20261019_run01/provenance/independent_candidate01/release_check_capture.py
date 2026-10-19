"""Capture one actual command in a D/E independent release root."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, json, os, subprocess, sys, time

p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--name',required=True)
p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
root=Path(a.root).resolve()
if not root.is_dir() or root.drive.lower() not in ('d:','e:'):raise ValueError('Existing D/E release root required')
if not a.name.replace('_','').isalnum():raise ValueError('Fresh simple command name required')
command=a.command[1:] if a.command[:1]==['--'] else a.command
if command[0]=='python':command[0]=sys.executable
folder=root/'publication_checks'/a.name;folder.mkdir(parents=True,exist_ok=False)
temporary=folder/'temp';temporary.mkdir()
env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',
    PYTHONPATH=str(root/'src')+os.pathsep+str(root),TEMP=str(temporary),TMP=str(temporary))
start=datetime.now(timezone.utc).isoformat();began=time.perf_counter()
with (folder/'command.log').open('xb') as log:
    done=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,check=False)
record=dict(name=a.name,command=command,cwd=str(root),started_utc=start,
    finished_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.perf_counter()-began,
    exit_code=done.returncode,environment_overrides={k:env[k] for k in
    ('PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING','PYTHONPATH','TEMP','TMP')})
with (folder/'COMMAND.json').open('x',encoding='utf8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
print(json.dumps(record,ensure_ascii=True));print((folder/'command.log').read_text(encoding='utf8',errors='replace')[-5000:])
raise SystemExit(done.returncode)
