"""Explicit-selection light package; no raw decoding, scoring or simulation.

Runtime files retain workspace-relative names. Historic batch evidence is under
evidence/ so a fresh register/run in the canonical RUN has a fresh episode
budget. Only root's explicit matched compressed raws enter the archive.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import platform
import shutil
import sys
import zipfile

# This is the same source inventory reused by the v13 light delivery helper.
# Only actual static-import closure plus registered source files are retained.
from tools.four_scenarios.package import PHYSICAL_INPUTS, _source_entries

SCHEMA = 'hospital_ies.engineering_light_package.v14.v1'
SELECTION_SCHEMA = 'hospital_ies.engineering_light_selection.v14.v1'
METADATA_DEPENDENCIES = (
    'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json',
    'outputs/v11_autonomous_dispatch_20261016_run01/new_data/standby_manifest.json',
    'outputs/v13_executed_service_20261017_run01/design/DEVELOPMENT_POLICY_SPECS.json',
    'outputs/v13_executed_service_20261017_run01/design/DEVELOPMENT_PROPOSAL.json',
    'outputs/v13_executed_service_20261017_run01/design/CONFIRMATION_PROPOSAL.json',
    'outputs/v12_service_switch_20261017_run01/design/EXPOSED_V11_POOL.json',
    'outputs/v12_service_switch_20261017_run01/design/COMBINED_FROZEN_PROPOSAL.json',
)
SKIP = {'temp', 'tmp', '__pycache__', '.pytest_cache', '.git', 'fixtures', 'helperfixtures'}
SUFFIXES = {'.py', '.md', '.txt', '.json', '.jsonl', '.csv', '.log', '.diff', '.xml', '.sha256'}
CHUNK = 1024*1024


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            value.update(block)
    return value.hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf8')


def _name(value):
    parts = str(value).replace('\\', '/').split('/')
    if any(part in ('', '.', '..') or ':' in part for part in parts):
        raise ValueError('Unsafe package-relative path: '+str(value))
    return '/'.join(parts)


def _within(value, root):
    path = Path(value)
    if not path.is_absolute():
        path = Path(root)/path
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(Path(root).resolve())
    except ValueError as error:
        raise ValueError('Path outside approved workspace: '+str(value)) from error
    # Reject links in every component, not merely a final-file symlink.
    cursor = Path(root).resolve()
    for part in relative.parts:
        cursor = cursor/part
        if cursor.is_symlink():
            raise ValueError('Symlinked package source: '+str(value))
    return resolved


def source_closure(root, seeds):
    """Filter the mature v13 source inventory by static local imports."""
    root = Path(root).resolve(); inventory = _source_entries(root)
    found = {}; pending = list(seeds)
    def resolve(parts):
        if not parts:
            return []
        stem = '/'.join(parts)
        candidates = [stem+'.py', stem+'/__init__.py', 'src/'+stem+'.py', 'src/'+stem+'/__init__.py']
        return [name for name in candidates if name in inventory]
    while pending:
        name = _name(pending.pop())
        if name in found:
            continue
        path = inventory.get(name)
        if path is None:
            raise FileNotFoundError(root/name)
        found[name] = path
        relative = name[:-3].split('/')
        if relative[0] == 'src':
            relative = relative[1:]
        package = relative[:-1]
        for length in range(1, len(package)+1):
            pending.extend(resolve(package[:length]))
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pending.extend(resolve(alias.name.split('.')))
            elif isinstance(node, ast.ImportFrom):
                base = package[:len(package)-node.level+1] if node.level else []
                base += node.module.split('.') if node.module else []
                pending.extend(resolve(base))
                for alias in node.names:
                    if alias.name != '*':
                        pending.extend(resolve(base+alias.name.split('.')))
    return found


def _put(entries, name, source, role, expected=None):
    name = _name(name)
    size = len(source) if isinstance(source, bytes) else source.stat().st_size
    digest = hashlib.sha256(source).hexdigest() if isinstance(source, bytes) else sha(source)
    if expected and digest != expected:
        raise ValueError('Declared dependency/raw SHA differs: '+name)
    if name in entries:
        if entries[name]['sha256'] != digest:
            raise ValueError('Package path collision: '+name)
        return
    entries[name] = dict(source=source, sha256=digest, bytes=size, role=role)


def _dependencies(sources):
    external = set()
    for path in sources.values():
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                external.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                external.add(node.module.split('.')[0])
    external -= set(getattr(sys, 'stdlib_module_names', ()))
    external -= {'hospital_ies', 'tools', '__future__', 'scenario_sampler', 'generate_nonmpc_training'}
    mapping = metadata.packages_distributions(); distributions = {}; unresolved = []
    for module in sorted(external):
        packages = mapping.get(module)
        if not packages:
            unresolved.append(module); continue
        for package in packages:
            try: distributions[package] = metadata.version(package)
            except metadata.PackageNotFoundError: distributions[package] = None
    return dict(python=sys.version, platform=platform.platform(),
        external_import_candidates=sorted(external), installed_distribution_versions=distributions,
        unresolved_import_candidates=unresolved, static_closure_includes_conditional_imports=True,
        replay_core=['numpy', 'pandas', 'scipy', 'gurobipy'],
        gurobi_license_included=False, credentials_included=False,
        note='Installed versions are evidence, not an automatic solver/license installation or a physical replay certification')


def _readme(run_relative, proposal, selected):
    first = selected[0]['scenario_id']
    return f'''# Engineering model package

Extract into a D/E workspace and open PowerShell at this package root.
This package contains model/config/input bytes and saved evidence. Final12
remains not_verified; copying input bytes does not authorize evaluating it.

Install the Python dependencies listed in DEPENDENCIES.json and the supplied
requirements.txt. The experiment used Gurobi: gurobipy and a separately valid
Gurobi license are required for that backend. No license, credentials, or system
settings are included. No neural training, paper, figure, or MPC step is needed.

Use the existing register/run commands with a directory that does not exist:

```powershell
$env:TEMP = Join-Path $PWD '{run_relative}/temp'
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$env:TMP = $env:TEMP
python -m tools.engineering_model.runner register --proposal "{proposal}" --directory "{run_relative}/experiments/replay_M0_F_E" --policies F,E --model M0 --phase replay_development --scenario-id "{first}"
python -m tools.engineering_model.runner run --directory "{run_relative}/experiments/replay_M0_F_E"
```

For the finite-start/minimum-time model, register a separate fresh directory
with `--model M14_engineering` using the same proposal, scene and F/E policies.
The default research dynamics and the frozen model configurations are included;
they are not OEM calibration. Existing saved evidence is never overwritten.

The historical batches are under `evidence/{run_relative}/experiments` so the
canonical RUN experiment directory and its registration budget start fresh.
All runtime dependencies, including earlier frozen metadata required by the
runner, retain their original workspace-relative names. Run source snapshots
remain with the corresponding historical evidence batch. Existing absolute
paths inside old results are historical provenance; use PACKAGE_INDEX.json
and EXTERNAL_RAW_INDEX.json for the packaged relative locations.

The selected raw list is an explicit root selection, not an automatic ranking.
Unselected compressed trajectories remain external with their saved SHA index.
Missing/failed/unknown cases remain in the complete registered index. ZIP/member
checks establish byte integrity only. They do not certify solver installation,
end-to-end physical replay, OEM calibration, or clinical reliability.

Verify archive bytes without running a simulation:

```powershell
python -m tools.engineering_reporting.package verify --archive "<archive.zip>"
```
'''.encode('utf8')


def prepare(workspace, run, selection_path, *, reports):
    """Build a byte inventory only after an explicit final root raw selection."""
    root = Path(workspace).resolve(); run = _within(run, root)
    relative_run = run.relative_to(root).as_posix(); selection_path = _within(selection_path, run)
    selection = _read(selection_path)
    if (selection.get('schema') != SELECTION_SCHEMA or selection.get('status') != 'final' or
            selection.get('selected_by') != 'root' or selection.get('all_batches_stopped') is not True):
        raise ValueError('Explicit final root selection with all batches stopped is required')
    examples = selection.get('raw_examples')
    if not isinstance(examples, list) or not 2 <= len(examples) <= 24:
        raise ValueError('Selection must retain 2 to 24 explicit matched raw examples')
    entries = {}; input_index = []; raw_index = []; selected_records = []; registered = []; seed_paths = set()
    for folder in ('tools/engineering_model', 'tools/engineering_reporting'):
        seed_paths.update(p.relative_to(root).as_posix() for p in (root/folder).glob('*.py'))
    snapshots = []
    for plan_path in sorted((run/'experiments').glob('*/plan.json')):
        folder = plan_path.parent; plan = _read(plan_path); manifest = _read(folder/'manifest.json')
        scenes = {s['id']: s for s in manifest['scenarios']}
        expected = {(sid, s['policy_id']) for sid in plan['scenario_ids'] for s in plan['policy_specs']}
        if len(expected) != plan['registered_pairs']:
            raise ValueError('Registered package denominator differs')
        path = folder/'analysis/RESULTS.json'; records = _read(path)['records'] if path.exists() else []
        actual = {}
        for record in records:
            key = (record['scenario_id'], record['policy_id'])
            if key in actual or key not in expected:
                raise ValueError('Saved result differs from registered package denominator')
            actual[key] = record
        for sid, pid in sorted(expected):
            record = actual.get((sid, pid)); scene = scenes[sid]
            registered.append(dict(batch=folder.name, scenario_id=sid, policy_id=pid,
                model_id=plan['model_id'], phase=plan['phase'], analysis_class=scene.get('analysis_class'),
                status=record.get('status') if record else 'missing_saved_analysis'))
            if record and record.get('raw_path'):
                raw = _within(record['raw_path'], run)
                raw_index.append(dict(batch=folder.name, scenario_id=sid, policy_id=pid, model_id=plan['model_id'],
                    original_workspace_path=raw.relative_to(root).as_posix(), raw_sha256=record.get('raw_sha256'),
                    packaged_path=None, included=False, sha_semantics='producer saved index; unselected raw not reread'))
        for name, digest in plan.get('source_sha256', {}).items():
            source = _within(name, root)
            if source.is_file() and sha(source) == digest:
                if source.suffix == '.py': seed_paths.add(source.relative_to(root).as_posix())
                else: _put(entries, source.relative_to(root).as_posix(), source, 'registered runtime dependency', digest)
            else:
                snapshot = _within(folder/'source_snapshot'/_name(name), root)
                if not snapshot.is_file() or sha(snapshot) != digest:
                    raise FileNotFoundError('Missing registered source/version: '+name)
                snapshots.append(dict(batch=folder.name, original_path=name,
                    packaged_path='evidence/'+snapshot.relative_to(root).as_posix(), sha256=digest))
        for example in examples:
            if example.get('batch') != folder.name:
                continue
            _name(example['batch'])
            record = actual.get((example.get('scenario_id'), example.get('policy_id')))
            if not record:
                raise ValueError('Selected raw has no registered saved result')
            raw = _within(record['raw_path'], run)
            name = 'evidence/'+raw.relative_to(root).as_posix()
            _put(entries, name, raw, 'explicit root matched raw', record['raw_sha256'])
            selected_records.append(dict(example, model_id=plan['model_id'], packaged_path=name))
            result_path = raw.with_name('RESULT.json')
            if result_path.is_file():
                _put(entries, 'evidence/'+result_path.relative_to(root).as_posix(), result_path, 'selected raw result')
            for index in raw_index:
                if (index['batch'], index['scenario_id'], index['policy_id']) == (folder.name, example['scenario_id'], example['policy_id']):
                    index.update(packaged_path=name, included=True, sha_semantics='compressed bytes verified while packaging')
    if len(selected_records) != len(examples) or len({(s['batch'], s['scenario_id'], s['policy_id']) for s in selected_records}) != len(examples):
        raise ValueError('Invalid/duplicate selected raw batch or identity')
    matched = defaultdict(set)
    for record in selected_records:
        matched[record['scenario_id']].add((record['model_id'], record['policy_id']))
    if any(len(values) < 2 for values in matched.values()):
        raise ValueError('Selected raw must contain matched policies or models for every scenario')
    sources = source_closure(root, sorted(seed_paths))
    for name, path in sources.items():
        _put(entries, name, path, 'runtime/reporting source')
    for relative in (*PHYSICAL_INPUTS, *METADATA_DEPENDENCIES):
        path = _within(relative, root)
        if not path.is_file():
            raise FileNotFoundError('Required replay input/metadata: '+relative)
        _put(entries, relative, path, 'physical input/config' if relative in PHYSICAL_INPUTS else 'required frozen metadata')
        input_index.append(dict(path=relative, sha256=entries[relative]['sha256'], bytes=entries[relative]['bytes'],
                                physical_values_parsed=False))
    for pattern in ('configs/*.json', 'requirements*.txt', 'tests/test_engineering*.py', 'docs/v14/*.md',
                    'docs/superpowers/*/*engineering-model*.md', 'Hospital_IES_V14_Engineering_Model_Task/*.md',
                    'Hospital_IES_V14_Engineering_Model_Task/*.csv', 'Hospital_IES_V14_Engineering_Model_Task/*.json'):
        for source in root.glob(pattern):
            _put(entries, source.relative_to(root).as_posix(), source, 'config/test/engineering context')
    for source in run.rglob('*'):
        if not source.is_file(): continue
        relative = source.relative_to(run)
        if any(part.casefold() in SKIP or part.casefold().startswith(('pytest-', 'pytest_')) for part in relative.parts): continue
        if source.suffix.lower() not in SUFFIXES or 'PAPER' in source.name.upper(): continue
        if 'pairs' in relative.parts and source.name not in ('analysis_error.json', 'error.json'): continue
        archive_name = ('evidence/' if relative.parts[0] == 'experiments' else '') + source.relative_to(root).as_posix()
        _put(entries, archive_name, _within(source, root), 'saved engineering evidence')
    for report in reports:
        path = _within(report, run)
        if not path.is_file(): raise FileNotFoundError(path)
        if path.suffix.lower() not in ('.md', '.json', '.csv', '.txt') or 'PAPER' in path.name.upper():
            raise ValueError('This delivery requires engineering text/data, no paper or image')
        _put(entries, path.relative_to(root).as_posix(), path, 'root engineering report')
    if not reports: raise ValueError('Actual engineering report is required')
    candidates = [path for path in sorted((run/'design_v2').glob('*_PROPOSAL.json'))
        if selected_records[0]['scenario_id'] in {s['id'] for s in _read(path).get('scenarios', [])}]
    if len(candidates) != 1:
        raise ValueError('Selected replay scenario must occur in one packaged proposal')
    proposal_path = candidates[0]
    dependencies = _dependencies(sources)
    _put(entries, 'README_REPLAY.md', _readme(relative_run, proposal_path.relative_to(root).as_posix(), selected_records), 'replay instructions')
    _put(entries, 'DEPENDENCIES.json', _bytes(dependencies), 'runtime distribution evidence')
    _put(entries, 'ALL_REGISTERED_PAIR_INDEX.json', _bytes(registered), 'all-denominator index')
    _put(entries, 'EXTERNAL_RAW_INDEX.json', _bytes(raw_index), 'selected/external raw metadata index')
    _put(entries, 'RUNTIME_INPUT_INDEX.json', _bytes(input_index), 'replay input byte inventory')
    _put(entries, 'SOURCE_VERSION_INDEX.json', _bytes(snapshots), 'historical source versions')
    return dict(entries=entries, registered_pairs=len(registered), selected_raw_count=len(selected_records),
        original_run=str(run), selection_sha256=sha(selection_path), selected_examples=selected_records,
        dependencies=dependencies, all_unselected_raw_external=True, physical_reproduction_performed=False,
        raw_values_decoded=False, original_final12='not_verified; unopened',
        estimated_uncompressed_bytes=sum(row['bytes'] for row in entries.values()))


def build(workspace, run, selection_path, output, *, reports):
    output = Path(output).resolve(); receipt_path = output.with_suffix(output.suffix+'.receipt.json')
    sidecar = output.with_suffix(output.suffix+'.sha256')
    if output.drive.lower() == 'c:': raise ValueError('Use D/E for project delivery')
    if any(path.exists() for path in (output, receipt_path, sidecar)):
        raise FileExistsError('Keep earlier packages immutable; use a fresh archive filename')
    prepared = prepare(workspace, run, selection_path, reports=reports)
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < prepared['estimated_uncompressed_bytes']+16*CHUNK:
        raise OSError('Insufficient destination space for estimated archive input bytes')
    index = []
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
        for name, item in sorted(prepared['entries'].items()):
            source = item['source']; digest = hashlib.sha256()
            mode = zipfile.ZIP_STORED if name.endswith('.gz') else zipfile.ZIP_DEFLATED
            info = zipfile.ZipInfo(name); info.compress_type = mode
            with bundle.open(info, 'w', force_zip64=True) as target:
                if isinstance(source, bytes): target.write(source); digest.update(source)
                else:
                    with source.open('rb') as stream:
                        for block in iter(lambda: stream.read(CHUNK), b''):
                            target.write(block); digest.update(block)
            if digest.hexdigest() != item['sha256']: raise ValueError('Source changed during archive copy: '+name)
            index.append(dict(path=name, sha256=item['sha256'], bytes=item['bytes'], role=item['role']))
        bundle.writestr('PACKAGE_INDEX.json', _bytes(dict(schema=SCHEMA, entries=index,
            original_run=prepared['original_run'], selection_sha256=prepared['selection_sha256'],
            registered_pairs=prepared['registered_pairs'], selected_raw_count=prepared['selected_raw_count'],
            physical_reproduction_performed=False, raw_values_decoded=False, final12='not_verified; unopened')))
    checked = verify(output)
    sidecar.write_text(checked['archive_sha256']+'  '+output.name+'\n', encoding='ascii')
    receipt = dict(checked, schema=SCHEMA+'.receipt', selected_raw_count=prepared['selected_raw_count'],
        registered_pairs=prepared['registered_pairs'], archive_bytes=output.stat().st_size,
        created_utc=datetime.now(timezone.utc).isoformat(), physical_reproduction_performed=False)
    with receipt_path.open('x', encoding='utf8') as stream: json.dump(receipt, stream, ensure_ascii=False, indent=2)
    return receipt


def verify(archive):
    archive = Path(archive)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None: raise ValueError('ZIP CRC failed')
        index = json.loads(bundle.read('PACKAGE_INDEX.json'))
        expected = {_name(row['path']) for row in index['entries']} | {'PACKAGE_INDEX.json'}
        names = bundle.namelist()
        if len(names) != len(set(names)) or set(names) != expected: raise ValueError('ZIP member denominator differs')
        for row in index['entries']:
            digest = hashlib.sha256(); size = 0
            with bundle.open(row['path']) as stream:
                for block in iter(lambda: stream.read(CHUNK), b''): digest.update(block); size += len(block)
            if digest.hexdigest() != row['sha256'] or size != row['bytes']: raise ValueError('ZIP SHA/size mismatch: '+row['path'])
    return dict(verified=True, archive_path=str(archive.resolve()), archive_sha256=sha(archive),
        member_count=len(expected), physical_reproduction_performed=False, raw_values_decoded=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('build'); create.add_argument('--workspace', type=Path, required=True)
    create.add_argument('--run', type=Path, required=True); create.add_argument('--selection', type=Path, required=True)
    create.add_argument('--output', type=Path, required=True); create.add_argument('--report', type=Path, action='append', required=True)
    check = sub.add_parser('verify'); check.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args(argv)
    result = verify(args.archive) if args.command == 'verify' else build(args.workspace, args.run, args.selection, args.output, reports=args.report)
    print(json.dumps(result, ensure_ascii=False)); return 0


if __name__ == '__main__': raise SystemExit(main())
