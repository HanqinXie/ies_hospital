"""P0 evidence runner: preserve stdout, stderr, timestamps and real exit code."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

p = argparse.ArgumentParser()
p.add_argument('--run-dir', type=Path, required=True)
p.add_argument('--name', required=True)
p.add_argument('command', nargs=argparse.REMAINDER)
a = p.parse_args()
command = a.command[1:] if a.command[:1] == ['--'] else a.command
if not command:
    p.error('command required')
if command[0] == 'python':
    command[0] = sys.executable
dest = a.run_dir / 'logs'
dest.mkdir(parents=True, exist_ok=True)
meta_path = dest / (a.name + '.command.json')
if meta_path.exists():
    p.error('refuse overwriting existing command evidence')
env = os.environ.copy()
env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
start = datetime.now(timezone.utc).isoformat()
t0 = time.perf_counter()
with (dest / (a.name + '.stdout.log')).open('wb') as stdout, (dest / (a.name + '.stderr.log')).open('wb') as stderr:
    result = subprocess.run(command, cwd=Path.cwd(), env=env, stdout=stdout, stderr=stderr, check=False)
record = dict(name=a.name, command=command, cwd=str(Path.cwd()), started_utc=start,
              finished_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.perf_counter()-t0,
              exit_code=result.returncode,
              environment_overrides={k:env[k] for k in ('PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING')})
meta_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding='utf-8')
with (a.run_dir / 'commands_and_exit_codes.txt').open('a', encoding='utf-8') as f:
    f.write(json.dumps(record, ensure_ascii=False) + '\n')
print(json.dumps(record, ensure_ascii=True))
sys.exit(result.returncode)
