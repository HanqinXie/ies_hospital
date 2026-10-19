"""Protect original source/input identities and SOURCE05; do not rehash old raw."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time
from tools.execution_recovery.data_design import digest

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'outputs/v19_model_closeout_20261018_run01'
OLD=ROOT/'outputs/v18_execution_recovery_20261018_run01'


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def protected_files(root=ROOT):
    root=Path(root)
    old=root/'outputs/v18_execution_recovery_20261018_run01'
    baseline=json.loads((old/'baseline/initial.json').read_text(encoding='utf8'))
    protected={name:value for name,value in baseline['files'].items()
        if Path(name).parts[0] in ('src','configs','inputs')}
    frozen_path=old/'MODEL_FREEZE_RECOVERY01.json'
    frozen=json.loads(frozen_path.read_text(encoding='utf8'))
    if (frozen.get('kind')!='v18_final_shared_model_freeze'
            or frozen.get('signature')!=digest({k:v for k,v in frozen.items() if k!='signature'})):
        raise ValueError('V19 handoff requires signed SOURCE05 MODEL_FREEZE_RECOVERY01')
    protected.update(frozen['source_sha256'])
    protected[frozen_path.relative_to(root).as_posix()]=sha(frozen_path)
    selection=root/'deliverables/Hospital_IES_V18_Execution_Recovery_20261018_Compact.FILE_SELECTION.json'
    if selection.is_file():
        selected=json.loads(selection.read_text(encoding='utf8'))
        for row in selected['entries']:
            member=Path(row['member'])
            if member.name=='trajectory.json.gz':
                path=(root/member).resolve()
                if not path.is_relative_to((old/'experiments').resolve()):
                    raise ValueError('Representative raw path leaves the preserved V18 experiments')
                protected[member.as_posix()]=row['sha256']
    # The delivered archive and sidecars bind the handoff without loading its
    # members or making another full copy of historical trajectory payloads.
    for p in sorted((root/'deliverables').glob('Hospital_IES_V18_Execution_Recovery_20261018_Compact*')):
        if p.is_file():protected[p.relative_to(root).as_posix()]=sha(p)
    return dict(sorted(protected.items()))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--name',required=True)
    a=p.parse_args(argv)
    if not a.name.replace('_','').isalnum():raise ValueError('Use a fresh simple baseline name')
    destination=RUN/'baseline'/(a.name+'.json')
    if destination.exists():raise FileExistsError(destination)
    start=time.perf_counter()
    protected=(protected_files() if a.name=='initial' else
        json.loads((RUN/'baseline/initial.json').read_text(encoding='utf8'))['files'])
    issues=[];total=0
    for name,expected in protected.items():
        path=ROOT/name
        if not path.is_file():issues.append(dict(path=name,issue='missing'));continue
        total+=path.stat().st_size;actual=sha(path)
        if actual!=expected:issues.append(dict(path=name,issue='sha256_mismatch',expected=expected,actual=actual))
    record=dict(schema='hospital_ies.v19.protected_baseline.v1',utc=datetime.now(timezone.utc).isoformat(),
        files=protected,issues=issues,checked_files=len(protected),checked_bytes=total,
        elapsed_seconds=time.perf_counter()-start,all_historical_raw_payloads_rehashed=False,
        representative_raw_only=True,
        storage={d:dict(zip(('total','used','free'),shutil.disk_usage(d))) for d in ('D:/','E:/') if Path(d).exists()},
        no_full_portable_copy_planned=True,final_archive_hard_limit_bytes=200000000,
        source='Original source/input SHA manifest plus signed V18 SOURCE05 and delivered archive sidecars')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('x',encoding='utf8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in record.items() if k!='files'},ensure_ascii=True))
    return 2 if issues else 0


if __name__=='__main__':raise SystemExit(main())
