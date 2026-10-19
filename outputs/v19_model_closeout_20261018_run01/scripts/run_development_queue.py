"""Continue two existing serial development lanes, preserving every result."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import subprocess
import sys
import time

B = Path(__file__).resolve().parent.parent
ROOT = B.parents[1]
LANES = {
    'energy': ('dev_contact_r1', ('dev_energy_r1',)),
    'conversion': ('dev_key_r2', ('dev_conversion_r1', 'dev_prop_r1')),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('lane', choices=LANES)
    args = parser.parse_args()
    dependency, jobs = LANES[args.lane]
    receipt = B / ('DEVELOPMENT_QUEUE_' + args.lane.upper() + '_RESULT.json')
    if receipt.exists():
        raise FileExistsError(receipt)
    record = dict(lane=args.lane, dependency=dependency,
        utc_started=datetime.now(timezone.utc).isoformat(), jobs=[])
    dependency_result = B / (dependency.upper() + '_PROCESS_RESULTS.json')
    while not dependency_result.exists():
        if (B / 'STOP_DEVELOPMENT_QUEUE.json').exists():
            raise RuntimeError('Explicit stop while waiting for original serial lane')
        time.sleep(5)
    rows = json.loads(dependency_result.read_text(encoding='utf-8'))
    record['dependency_exit_codes'] = [row['exit_code'] for row in rows]
    if any(row['exit_code'] != 0 for row in rows):
        record['stopped'] = 'Dependency did not complete successfully'
    else:
        for name in jobs:
            if (B / 'STOP_DEVELOPMENT_QUEUE.json').exists():
                record['stopped'] = 'Explicit queue stop'
                break
            command = [sys.executable, '-B', str(ROOT/'tools/p0_audit/run_logged.py'),
                '--run-dir', str(B), '--name', 'campaign_' + name, '--',
                sys.executable, '-B', '-m', 'tools.model_closeout.campaign',
                'run', '--name', name, '--workers', '1']
            completed = subprocess.run(command, cwd=ROOT, check=False)
            record['jobs'].append(dict(name=name, command=command,
                exit_code=completed.returncode))
            if completed.returncode:
                record['stopped'] = 'Campaign failure; no dependent launch'
                break
    record['utc_finished'] = datetime.now(timezone.utc).isoformat()
    record['all_succeeded'] = len(record['jobs']) == len(jobs) and not record.get('stopped')
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    return 0 if record['all_succeeded'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
