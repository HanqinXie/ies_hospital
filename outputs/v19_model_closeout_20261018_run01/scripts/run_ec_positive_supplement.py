"""Run the unchanged original EC_2 scene only after the propagation lane ends."""
from pathlib import Path
from datetime import datetime,timezone
import json
import subprocess
import sys
import time

B=Path(__file__).resolve().parent.parent
ROOT=B.parents[1]


def main():
    path=B/'DEVELOPMENT_QUEUE_CONVERSION_RESULT.json'
    while not path.exists():
        if (B/'STOP_EC_POSITIVE_SUPPLEMENT.json').exists():
            raise RuntimeError('Explicit supplement stop')
        time.sleep(5)
    record=dict(utc_started=datetime.now(timezone.utc).isoformat(),
        dependency_sha256=__import__('hashlib').sha256(path.read_bytes()).hexdigest(),
        scope='Same original EC_2 target/clock with E/K1; source and physical configuration unchanged')
    if json.loads(path.read_text(encoding='utf-8')).get('all_succeeded') is not True:
        record.update(all_succeeded=False,reason='Propagation lane failed; no supplement launched')
    else:
        if (B/'STOP_EC_POSITIVE_SUPPLEMENT.json').exists():
            raise RuntimeError('Explicit supplement stop before physical launch')
        command=[sys.executable,'-B',str(ROOT/'tools/p0_audit/run_logged.py'),
            '--run-dir',str(B),'--name','campaign_dev_ec_positive_r1','--',
            sys.executable,'-B','-m','tools.model_closeout.campaign','run',
            '--name','dev_ec_positive_r1','--workers','1']
        result=subprocess.run(command,cwd=ROOT,check=False)
        record.update(command=command,exit_code=result.returncode,all_succeeded=result.returncode==0)
    record['utc_finished']=datetime.now(timezone.utc).isoformat()
    with (B/'EC_POSITIVE_SUPPLEMENT_RESULT.json').open('x',encoding='utf-8') as stream:
        json.dump(record,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return 0 if record['all_succeeded'] else 1


if __name__=='__main__':raise SystemExit(main())
