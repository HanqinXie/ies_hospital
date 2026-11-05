"""Close a known owned launcher Job; never operate on another process tree."""
import argparse
import json
from pathlib import Path
import os
import sys
import time

import durable_launch as d

p=argparse.ArgumentParser()
p.add_argument('--spec',required=True)
p.add_argument('--method',choices=['breakaway','wmi','shell'],required=True)
args=p.parse_args()
s=d.validate_spec(json.loads(Path(args.spec).read_text(encoding='utf-8-sig')))
directory=Path(s['run_directory'])
command=[sys.executable,'-S',str(Path(d.__file__).resolve()),'launch','--method',args.method,'--spec',args.spec]
parent=Path(args.spec).parent
with (parent/(directory.name+'.parent.stdout.log')).open('x') as out, \
     (parent/(directory.name+'.parent.stderr.log')).open('x') as err:
    owned=d.owned_class()(command,cwd=s['cwd'],env=dict(os.environ),stdout=out,stderr=err)
    item=d.identity(owned.proc.pid,command)
    code=owned.proc.wait(timeout=25)
    owned.close()  # explicit KILL_ON_JOB_CLOSE of this known launcher-only Job
    proof=dict(schema='hospital_ies.known_parent_job_close.v1',epoch=time.time(),
        launcher_identity=item,launcher_exit_code=code,launcher_identity_still_alive=d.alive(item),
        owned_job_closed=True,parent_pid=os.getpid(),method=args.method)
    d.write_new(directory/'PARENT_JOB_CLOSED.json',proof)
    print(json.dumps(proof))
raise SystemExit(code)
