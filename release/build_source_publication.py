"""Build a byte-preserving source distribution; never run hospital models."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import shutil
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args]).decode('utf-8').strip()


def build(workspace, destination):
    rows = []
    snapshots = []

    def copy(source, relative, category):
        if not source.is_file() or source.is_symlink():
            raise ValueError('Missing/non-regular source: ' + str(source))
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha(source) != sha(target):
            raise ValueError('Copy digest mismatch: ' + relative)
        rows.append(dict(path=relative, source=str(source), bytes=target.stat().st_size,
                         sha256=sha(target), category=category))

    sources = [
        ('model', workspace / 'transfers/normal_year/source', False),
        ('studies/fixed_prefault/source', Path('D:/hospital_ies_no_priority_20261105'), True),
    ]
    for prefix, source, include_untracked in sources:
        names = subprocess.check_output(['git', '-C', str(source), 'ls-files', '-z']).decode().split('\0')
        if include_untracked:
            names += subprocess.check_output(['git', '-C', str(source), 'ls-files', '--others', '--exclude-standard', '-z']).decode().split('\0')
        names = sorted(set(filter(None, names)))
        for name in names:
            copy(source / name, prefix + '/' + name, prefix)
        snapshots.append(dict(path=prefix, source_root=str(source), base_commit=git(source, 'rev-parse', 'HEAD'),
                              worktree_status=git(source, 'status', '--short'), files=len(names),
                              description='Current working bytes, including disclosed uncommitted changes' if include_untracked else 'Clean frozen annual source'))

    # Annual launchers remain unmodified reference templates. Full historical
    # fixture/domain stores and nested Git databases are deliberately external.
    for kind in ('normal_year', 'busy_year'):
        source = workspace / 'transfers' / kind
        for path in sorted(source.iterdir()):
            if path.is_file():
                copy(path, 'annual/' + kind + '/' + path.name, 'annual_reference')
        for directory in ('cases', 'config', 'models', 'scripts', 'tests'):
            if not (source / directory).exists():
                continue
            for path in sorted((source / directory).rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts:
                    copy(path, 'annual/' + kind + '/' + path.relative_to(source).as_posix(), 'annual_reference')
        for name in ('SOURCE_FREEZE.json', 'PAYLOAD_PROVENANCE.json', 'LEGACY_FIXTURE_MANIFEST.json'):
            path = source / 'evidence' / name
            if path.is_file():
                copy(path, 'annual/' + kind + '/evidence/' + name, 'original_provenance')

    evidence = Path('E:/hospital_ies_source_partition_fix_20261105_run01')
    for name in ('REPORT_ZH.md', 'FINAL_VERIFICATION.json', 'FULL_SUITE_QUALIFICATION.json'):
        copy(evidence / name, 'evidence/fixed_prefault/' + name, 'historical_execution_evidence')
    copy(evidence / 'campaign03/SOURCE_BINDING.json', 'evidence/fixed_prefault/SOURCE_BINDING.json', 'original_provenance')
    for name in ('REPORT_ZH.md', 'FINAL_VERIFICATION.json', 'COMMAND_RESULTS.json', 'INHERITED_FAILURE_IDENTITY.json'):
        copy(Path('E:/hospital_ies_no_priority_20261105_run01') / name,
             'evidence/no_priority_initial/' + name, 'historical_execution_evidence')

    config = json.loads((destination / 'model/configs/plant_configs.json').read_text('utf-8'))
    registry = json.loads((destination / 'model/configs/model_review_v30/cases.json').read_text('utf-8'))
    params = []

    def flatten(value, prefix, source):
        if isinstance(value, dict):
            for key, child in value.items():
                flatten(child, prefix + '.' + key if prefix else key, source)
        else:
            params.append(dict(parameter=prefix, value=json.dumps(value, ensure_ascii=False), source=source))

    flatten(config, 'plant', 'model/configs/plant_configs.json')
    flatten(registry['hardware_profiles']['H2'], 'H2', 'model/configs/model_review_v30/cases.json')
    flatten(registry['model_closure_v22'], 'closure', 'model/configs/model_review_v30/cases.json')
    for method in ('M_available', 'M_ready', 'C_ready'):
        case = json.loads((destination / 'annual/normal_year/cases' / (method + '.json')).read_text('utf-8'))
        flatten(case['policy'], method + '.policy', 'annual/normal_year/cases/' + method + '.json')
        flatten(case['execution_revision'], method + '.execution', 'annual/normal_year/cases/' + method + '.json')
    with (destination / 'docs/KEY_PARAMETERS.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['parameter', 'value', 'source'])
        writer.writeheader()
        writer.writerows(params)
    (destination / 'release/SOURCE_MANIFEST.json').write_text(json.dumps(dict(
        schema='hospital_ies.publication.source_manifest.v1', snapshots=snapshots, files=rows,
        copied_files=len(rows), copied_bytes=sum(r['bytes'] for r in rows),
        new_physical_or_training_runs=0, frozen_scientific_files_modified=False),
        ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(copied_files=len(rows), copied_bytes=sum(r['bytes'] for r in rows), parameter_rows=len(params))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    args = parser.parse_args()
    build(args.workspace.resolve(), Path(__file__).resolve().parents[1])
