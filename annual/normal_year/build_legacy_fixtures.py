"""Verify retained genuine historical fixtures; never copy or rebuild history."""
import json
from pathlib import Path
from verify_source_copy import ROOT,read_json,safe_path,sha


def verify_fixtures(package_root=ROOT):
    root=Path(package_root).resolve()
    manifest=read_json(root/'evidence/LEGACY_FIXTURE_MANIFEST.json')
    rows=manifest.get('files');listed=set();total=0
    if not isinstance(rows,list) or not rows:raise ValueError('Missing genuine historical fixture inventory')
    for row in rows:
        relative=row['path'];path=safe_path(root,relative)
        if not relative.startswith('legacy_fixtures/') or relative in listed:
            raise ValueError('Invalid or duplicate historical fixture path')
        listed.add(relative)
        if not path.is_file() or path.stat().st_size!=row['size_bytes'] or sha(path)!=row['sha256']:
            raise ValueError('Genuine historical fixture bytes differ: '+relative)
        total+=path.stat().st_size
    actual={p.relative_to(root).as_posix() for p in (root/'legacy_fixtures').rglob('*') if p.is_file()}
    if actual!=listed or len(rows)!=manifest['total_files'] or total!=manifest['total_bytes']:
        raise ValueError('Genuine historical fixture inventory coverage differs')
    return dict(status='passed',scope='Retained historical fixture byte inventory only',
        total_files=len(rows),total_bytes=total,source_and_input_files=manifest['source_and_input_files'],
        scientific_or_optimizer_runs_started=0,original_fixture_manifest_modified=False,
        historical_test_pass_inferred=False)


def main():print(json.dumps(verify_fixtures(),ensure_ascii=False))


if __name__=='__main__':main()
