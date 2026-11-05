"""Verify current model, input and saved-result bytes; never runs a model."""
import argparse,hashlib,json
from pathlib import Path,PurePosixPath

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def safe_member(root,name):
    rel=PurePosixPath(name)
    if rel.is_absolute() or '..' in rel.parts or ':' in name or '\\' in name:raise ValueError('Unsafe relative member')
    p=(Path(root)/name).resolve();p.relative_to(Path(root).resolve());return p
def verify_files(root,mapping):
    for name,digest in mapping.items():
        if sha(safe_member(root,name))!=digest:raise ValueError('Frozen bytes changed: '+name)
def source_binding(root):
    # Same enumeration as frozen tools/sci_engineering/runner.py:61-69.
    root=Path(root);paths=set()
    for folder in ('src','tools','scripts','tests'):paths.update((root/folder).rglob('*.py'))
    paths.update((root/'configs').rglob('*.json'));paths.update(root.glob('*.py'))
    if (root/'pytest.ini').is_file():paths.add(root/'pytest.ini')
    return {p.relative_to(root).as_posix():sha(p) for p in sorted(paths) if '__pycache__' not in p.parts and not p.is_symlink()}
def input_binding(root):
    # Same suffixes/exclusions as frozen runner.py:72-78.
    root=Path(root);suffixes={'.csv','.json','.idf','.epw','.txt'}
    paths=[p for p in (root/'inputs').rglob('*') if p.is_file() and p.suffix.lower() in suffixes and 'literature' not in p.parts and not p.is_symlink()]
    paths+=list(root.glob('requirements*.txt'))
    return {p.relative_to(root).as_posix():sha(p) for p in sorted(paths)}
def verify_results(root):
    folder=Path(root)/'results';catalog=read(folder/'CATALOG.json')
    for row in catalog['copied_artifacts']:
        p=safe_member(folder,row['staged_path'])
        if p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise ValueError('Saved result changed: '+row['staged_path'])
    return len(catalog['copied_artifacts'])
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);args=parser.parse_args()
    root=args.root.resolve();freeze=read(root/'release/FROZEN_SOURCE_V32_P01.json')
    if len(freeze['source_files'])!=873 or len(freeze['input_files'])!=36:raise ValueError('Wrong accepted scientific version')
    if source_binding(root)!=freeze['source_files']:raise ValueError('Current scientific source set or bytes changed')
    if input_binding(root)!=freeze['input_files']:raise ValueError('Current original input set or bytes changed')
    count=verify_results(root);current=read(root/'release/CURRENT.json')
    verify_files(root,current['publication_metadata_sha256'])
    print(json.dumps(dict(status='passed',scientific_producer=freeze['producer'],source_files=873,input_files=36,
        saved_result_files_verified=count,publication_metadata_verified=len(current['publication_metadata_sha256']),
        new_hospital_simulations=0,new_solver_calls=0),ensure_ascii=True))
    return 0
if __name__=='__main__':raise SystemExit(main())
