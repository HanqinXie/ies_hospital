"""Verify the curated closure; restore separately obtained exact literature bytes."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
RUN = 'outputs/v19_model_closeout_20261018_run01'

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()

def local(root, name):
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ('.','..') for x in p.parts) or ':' in name or '\\' in name:
        raise ValueError('Unsafe manifest path: '+name)
    result = (root / name).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('Path escaped root: '+name)
    return result

def matches(root, row):
    p = local(root, row['path'])
    return p.is_file() and p.stat().st_size==row['bytes'] and sha(p)==row['sha256']

def verify(require_full=False):
    selection = read(ROOT/'publication/ORIGINAL_FILE_SELECTION.json')['files']
    errors = [r['path'] for r in selection if not matches(ROOT,r)]
    external = read(ROOT/'publication/EXTERNAL_ASSETS.json')['files']
    absent = [r['path'] for r in external if not matches(ROOT,r)]
    sys.path[:0] = [str(ROOT),str(ROOT/'src')]
    from tools.model_closeout.runner import source_binding
    from tools.model_closeout.gate import test_binding
    freeze = read(ROOT/RUN/'MODEL_FREEZE_CLOSEOUT01.json')
    actual_source = source_binding()
    actual_tests = test_binding()
    if actual_source != freeze['source_sha256']:
        errors.append('actual production binding differs from original final freeze')
    if actual_tests != freeze['source_bound_test_gate']['test_files_sha256']:
        errors.append('actual test binding differs from original final freeze')
    if require_full:
        inventory=read(ROOT/'publication/REGISTERED_INPUT_INVENTORY.json')['files']
        errors.extend(r['path'] for r in inventory if not matches(ROOT,r))
    result=dict(status='passed' if not errors else 'failed',selected_original_files=len(selection),
        frozen_production_files=len(actual_source),original_test_files=len(actual_tests),
        missing_or_changed_external_assets=absent,strict_registered_inventory_required=require_full,
        errors=sorted(set(errors)))
    print(json.dumps(result,ensure_ascii=True,indent=2))
    return 0 if not errors else 1

def restore(source):
    source=Path(source).resolve(strict=True)
    if source==ROOT: raise ValueError('Use a separate local source root')
    rows=read(ROOT/'publication/EXTERNAL_ASSETS.json')['files']
    # Validate every incoming byte before writing anything; never replace existing files.
    for row in rows:
        if not matches(source,row): raise ValueError('Missing/changed incoming asset: '+row['path'])
        target=local(ROOT,row['path'])
        if target.exists() and not matches(ROOT,row):
            raise FileExistsError('Refusing to overwrite different bytes: '+row['path'])
    count=0
    copied_bytes=0
    for row in rows:
        target=local(ROOT,row['path'])
        if target.exists(): continue
        target.parent.mkdir(parents=True,exist_ok=True)
        with local(source,row['path']).open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src,dst,1024*1024)
        if not matches(ROOT,row): raise ValueError('Copied asset SHA differs: '+row['path'])
        count+=1
        copied_bytes+=row['bytes']
    print(json.dumps(dict(restored_files=count,copied_bytes=copied_bytes,
        external_inventory_bytes=sum(r['bytes'] for r in rows),
        external_assets_gitignored=True)))
    return verify(True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    v=commands.add_parser('verify');v.add_argument('--require-full',action='store_true')
    r=commands.add_parser('restore');r.add_argument('--source-root',required=True)
    args=parser.parse_args()
    return restore(args.source_root) if args.command=='restore' else verify(args.require_full)

if __name__=='__main__':
    raise SystemExit(main())
