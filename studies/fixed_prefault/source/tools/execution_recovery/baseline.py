"""Read-only SHA audit of original protected files and V17 delivered evidence."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'outputs/v18_execution_recovery_20261018_run01'
OLD = ROOT / 'outputs/v17_state_coordinated_20261018_run01'

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--name', required=True)
    a = p.parse_args()
    destination = RUN / 'baseline' / (a.name + '.json')
    if destination.exists():
        raise FileExistsError(destination)
    t0 = time.perf_counter()
    protected = json.loads((OLD / 'baseline/PROTECTED_BASELINE.json').read_text(encoding='utf-8'))['files']
    selected = json.loads((OLD / 'PACKAGE_FILE_SELECTION.json').read_text(encoding='utf-8'))
    for row in selected['entries']:
        protected[row['path']] = row['sha256']
    for row in json.loads((OLD / 'GLOBAL_RAW_INDEX.json').read_text(encoding='utf-8'))['records']:
        protected[Path(row['raw_path']).relative_to(ROOT).as_posix()] = row['raw_sha256']
    for f in [OLD / 'PACKAGE_FILE_SELECTION.json', OLD / 'PACKAGE_VERIFICATION.json',
              *sorted((ROOT / 'deliverables').glob('*V17*'))]:
        if f.is_file():
            protected.setdefault(f.relative_to(ROOT).as_posix(), sha(f))
    prior = RUN / 'baseline' / 'initial.json'
    if a.name != 'initial':
        protected = json.loads(prior.read_text(encoding='utf-8'))['files']
    issues = []
    total = 0
    for name, expected in sorted(protected.items()):
        f = ROOT / name
        if not f.is_file():
            issues.append(dict(path=name, issue='missing'))
            continue
        total += f.stat().st_size
        actual = sha(f)
        if actual != expected:
            issues.append(dict(path=name, issue='sha256_mismatch', expected=expected, actual=actual))
    record = dict(schema='hospital_ies.v18.protected_baseline.v1',
        utc=datetime.now(timezone.utc).isoformat(), files=protected, issues=issues,
        checked_files=len(protected), checked_bytes=total, elapsed_seconds=time.perf_counter()-t0,
        storage={d:dict(zip(('total','used','free'),shutil.disk_usage(d))) for d in ('D:/','E:/')},
        new_run_storage_estimate_bytes=12000000000, no_full_portable_copy_planned=True,
        final_archive_hard_limit_bytes=200000000, source='V17 preserved SHA manifests and current archive sidecars')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in record.items() if k!='files'}, ensure_ascii=True))
    return bool(issues)

if __name__ == '__main__':
    sys.exit(main())
