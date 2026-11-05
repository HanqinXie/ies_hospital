"""Run actual checks and bind their complete source/test bytes before freezing."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
from . import runner as r

def test_binding():
    return {p.relative_to(r.ROOT).as_posix():r.sha(p) for p in sorted((r.ROOT/'tests').rglob('*.py'))
        if '__pycache__' not in p.parts}

def main():
    p=argparse.ArgumentParser();p.add_argument('--name',required=True)
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    if not command:raise ValueError('Actual check command is required')
    if command[0]=='python':command[0]=sys.executable
    path=r.RUN/'test_gates'/(a.name+'.json')
    if path.exists():raise FileExistsError(path)
    before=r.source_binding();tests_before=test_binding();start=datetime.now(timezone.utc).isoformat()
    done=subprocess.run(command,cwd=r.ROOT,check=False)
    after=r.source_binding();tests_after=test_binding()
    passed=done.returncode==0 and before==after and tests_before==tests_after
    record=dict(schema='hospital_ies.v19.source_bound_test_gate.v1',started_utc=start,
        finished_utc=datetime.now(timezone.utc).isoformat(),command=command,cwd=str(r.ROOT),
        exit_code=done.returncode,passed=passed,source_before=before,source_after=after,
        test_files_sha256=tests_before,test_files_after_sha256=tests_after,
        source_and_tests_unchanged=before==after and tests_before==tests_after)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(gate=a.name,passed=passed,exit_code=done.returncode,source_files=len(before))),flush=True)
    return 0 if passed else done.returncode or 1

if __name__=='__main__':raise SystemExit(main())
