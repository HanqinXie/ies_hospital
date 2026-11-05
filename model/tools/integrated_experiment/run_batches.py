"""Finite local shard waves with raw logs, audits and resumable positions."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def unknown_statuses(status_counts):
    """Even native infeasibility needs actual witness review, never relabeling."""
    return {status: count for status, count in status_counts.items()
            if status != 'completed' and count}


def run(manifest, evidence_root, *, prefix, workers=2, wave_size=2, start=0, stop=None):
    manifest = Path(manifest).resolve(); evidence_root = Path(evidence_root).resolve()
    m = json.loads(manifest.read_text(encoding='utf-8'))
    indices = list(range(start, min(len(m['p2b_shards']), stop or len(m['p2b_shards']))))
    journal = evidence_root / f'{prefix}_waves.jsonl'

    def logged(name, args):
        record = evidence_root / 'logs' / (name+'.command.json')
        if record.exists():
            previous = json.loads(record.read_text(encoding='utf-8'))
            raise FileExistsError(f'Existing command {name}, exit={previous["exit_code"]}; use a new attempt prefix')
        result = subprocess.run([sys.executable, str(ROOT/'tools/p0_audit/run_logged.py'),
            '--run-dir', str(evidence_root), '--name', name, '--', sys.executable, *args], cwd=ROOT, check=False)
        return result.returncode

    for left in range(0, len(indices), wave_size):
        wave = indices[left:left+wave_size]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            codes = list(pool.map(lambda index: logged(f'{prefix}_shard_{index:02d}',
                ['-m','tools.integrated_experiment.runner','run',str(manifest),str(index)]), wave))
        if any(codes):
            raise RuntimeError(f'Infrastructure/dependency wave exits {codes}; retained raw commands')
        audit_name = f'{prefix}_through_{wave[-1]:02d}'
        code = logged(audit_name, ['-m','tools.integrated_experiment.data_audit', str(manifest), audit_name])
        if code:
            raise RuntimeError('Actual data audit failed; inspect retained raw checkpoint inputs')
        audit = json.loads((manifest.parent/'audits'/audit_name/'audit.json').read_text(encoding='utf-8'))
        record = {'utc': datetime.now(timezone.utc).isoformat(), 'manifest_signature': m['signature'],
                  'wave_indices': wave, 'audit': audit_name,
                  'attempted_trajectories': audit['attempted_trajectories'],
                  'status_counts': audit['status_counts'], 'complete_budget': audit['complete_budget']}
        with journal.open('a', encoding='utf-8') as file:
            file.write(json.dumps(record)+'\n')
        print(json.dumps(record), flush=True)
        # Every numerical error is reviewed before this frozen version advances.
        # True modeled infeasibility is not erased; its unknown outcomes remain.
        pending = unknown_statuses(audit['status_counts'])
        if pending:
            raise RuntimeError(f'Unknown/numerical/infeasible attempts need actual investigation: {pending}; wave retained')
    return {'waves_completed': (len(indices)+wave_size-1)//wave_size}


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('manifest');p.add_argument('evidence_root');p.add_argument('--prefix',required=True)
    p.add_argument('--workers',type=int,default=2);p.add_argument('--wave-size',type=int,default=2)
    p.add_argument('--start',type=int,default=0);p.add_argument('--stop',type=int)
    a=p.parse_args();print(json.dumps(run(a.manifest,a.evidence_root,prefix=a.prefix,workers=a.workers,
                                       wave_size=a.wave_size,start=a.start,stop=a.stop)))
