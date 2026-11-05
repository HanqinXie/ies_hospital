"""Portable package, server identity and admission checks.

Import, verify and preflight never load the scientific package or a solver.
Admission is an explicit server action, independent from starting a year.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import re
from scripts.execution_profile import require_case, require_match, verify_account as verify_engineering_account, execution_contract
from scripts import server_profile

SOURCE_COUNT = None  # Runtime identity is read from the sealed SOURCE_FREEZE map.
INPUT_COUNT = None  # Runtime identity is read from the sealed SOURCE_FREEZE map.
PINNED_COMMIT = None  # Runtime identity is read from the sealed SOURCE_FREEZE map.
SCIENTIFIC_SHA256 = None  # Runtime identity is read from the sealed SOURCE_FREEZE map.
INPUT_SHA256 = None  # Runtime identity is read from the sealed SOURCE_FREEZE map.
METHODS = ('M_available', 'M_ready', 'C_ready')
HOST_KEYS = ('hostname', 'system', 'release', 'machine', 'cpu_model', 'ram_total_bytes')


class CheckError(ValueError):
    """An explicit gate failed or evidence is missing."""


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    def reject_constant(value):
        raise CheckError('Nonfinite JSON constant: ' + value)
    try:
        return json.loads(Path(path).read_text('utf-8-sig'), parse_constant=reject_constant)
    except (OSError, ValueError) as error:
        raise CheckError('Missing or invalid JSON: ' + str(path)) from error


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def safe_relative(root, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise CheckError('Invalid relative manifest path')
    raw = Path(relative)
    if raw.is_absolute() or not raw.parts or any(x in ('.', '..') for x in raw.parts):
        raise CheckError('Invalid relative manifest path')
    path = root / raw
    if not path.resolve().is_relative_to(root.resolve()):
        raise CheckError('Path escapes declared root')
    for piece in (path, *path.parents):
        if piece == root.parent:
            break
        if piece.is_symlink():
            raise CheckError('Symlinks are not admitted: ' + str(piece))
    return path


def external_work_root(package_root, work_root):
    package, work = Path(package_root).resolve(), Path(work_root).resolve()
    if work == package or work.is_relative_to(package):
        raise CheckError('work-root must be outside the entire package')
    if package.is_relative_to(work):
        raise CheckError('work-root must not contain the package')
    return work


def ignored(relative):
    parts = relative.parts
    return (parts[:2] in (('source', '.git'), ('legacy_context_7fa', '.git')) or parts[0] == 'verification'
        or '__pycache__' in parts or '.pytest_cache' in parts or relative.as_posix() == 'MANIFEST.json')


def _verify_hashes(source, pins, label, expected_count, expected_digest):
    if not isinstance(pins, dict) or len(pins) != expected_count or canonical_sha256(pins) != expected_digest:
        raise CheckError(label + ' frozen inventory identity/count differs')
    for relative, digest in pins.items():
        path = safe_relative(source, relative)
        if not path.is_file() or sha256(path) != digest:
            raise CheckError(label + ' bytes differ: ' + relative)

def verify_package(package_root):
    root = Path(package_root).resolve()
    freeze = read_json(root / 'evidence/SOURCE_FREEZE.json')
    pinned_commit = freeze.get('git_commit')
    source_map, input_map = freeze.get('source'), freeze.get('inputs')
    if (not isinstance(pinned_commit,str) or re.fullmatch('[0-9a-f]{40}',pinned_commit) is None
            or not isinstance(source_map,dict) or not source_map or not isinstance(input_map,dict) or not input_map):
        raise CheckError('Canonical source/input maps and exact source commit are required')
    source_count, input_count = len(source_map), len(input_map)
    scientific_sha256, input_sha256 = freeze.get('source_sha256'), freeze.get('input_sha256')
    manifest_path = root / 'MANIFEST.json'
    manifest = read_json(manifest_path)
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries:
        raise CheckError('Empty package manifest')
    listed = set()
    for entry in entries:
        relative = entry.get('path')
        path = safe_relative(root, relative)
        if relative in listed or ignored(Path(relative)):
            raise CheckError('Duplicate or excluded manifest entry: ' + str(relative))
        listed.add(relative)
        if not path.is_file() or path.stat().st_size != entry.get('size_bytes') or sha256(path) != entry.get('sha256'):
            raise CheckError('Package bytes differ: ' + relative)
    actual = set()
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if ignored(relative):
            continue
        if path.is_symlink():
            raise CheckError('Package symlink not admitted: ' + str(relative))
        if path.is_file():
            actual.add(relative.as_posix())
    if listed != actual:
        raise CheckError('Package manifest coverage differs: ' + repr(sorted(listed ^ actual)[:10]))
    source = root / 'source'
    if not (source / '.git').is_dir() or (source / '.git').is_symlink():
        raise CheckError('source must have an independent .git directory')
    if (source / '.git/commondir').exists() or (source / '.git/objects').is_symlink():
        raise CheckError('Shared Git metadata/objects not admitted')
    alternates = source / '.git/objects/info/alternates'
    if alternates.exists() and alternates.read_text('utf8').strip():
        raise CheckError('Shared Git alternates not admitted')
    head_result = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'], capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if head_result.returncode or head_result.stdout.strip() != pinned_commit:
        raise CheckError('source HEAD differs from frozen commit')
    freeze = read_json(root / 'evidence/SOURCE_FREEZE.json')
    if freeze.get('status') != 'passed' or freeze.get('git_commit') != pinned_commit:
        raise CheckError('Unknown source freeze')
    _verify_hashes(source, freeze.get('source'), 'Science source', source_count, scientific_sha256)
    _verify_hashes(source, freeze.get('inputs'), 'Science input', input_count, input_sha256)
    scientific_files = set()
    for folder in ('src', 'tools', 'scripts', 'tests'):
        scientific_files.update(p.relative_to(source).as_posix() for p in (source / folder).rglob('*.py')
            if '__pycache__' not in p.parts)
    scientific_files.update(p.relative_to(source).as_posix() for p in (source / 'configs').rglob('*.json'))
    scientific_files.update(p.name for p in source.glob('*.py'))
    if (source / 'pytest.ini').is_file():
        scientific_files.add('pytest.ini')
    if scientific_files != set(freeze['source']):
        raise CheckError('Scientific source file-set differs from the sealed source freeze')
    input_files = {p.relative_to(source).as_posix() for p in (source / 'inputs').rglob('*') if p.is_file()}
    input_files.update(p.name for p in source.glob('requirements*.txt'))
    if input_files != set(freeze['inputs']):
        raise CheckError('Input file-set differs from the sealed input approval')
    if freeze.get('source_sha256') != scientific_sha256 or freeze.get('input_sha256') != input_sha256:
        raise CheckError('Source freeze digests differ')
    approval = read_json(root / 'evidence/DATA_APPROVAL.json')
    if approval.get('source_input_sha256') != freeze['inputs'] or not approval.get('approval_answer'):
        raise CheckError('Data approval does not bind these exact inputs')
    index = read_json(root / 'cases/INDEX.json')
    if (index.get('source_commit') != pinned_commit or index.get('source_scientific_sha256') != scientific_sha256
        or index.get('input_sha256') != input_sha256):
        raise CheckError('Case index source/input identity differs')
    rows = index.get('cases')
    if not isinstance(rows, list) or len(rows) != 3:
        raise CheckError('Exactly three normal cases required')
    cases, shared = {}, []
    for row in rows:
        method = row.get('method')
        if method not in METHODS or method in cases or row.get('path') != 'cases/' + method + '.json':
            raise CheckError('Unregistered normal case')
        path = safe_relative(root, row['path'])
        case = read_json(path)
        require_case(case)
        if sha256(path) != row.get('file_sha256') or canonical_sha256(case) != row.get('canonical_sha256'):
            raise CheckError('Case file/canonical bytes differ')
        params = case.get('annual_program', {}).get('parameters', {})
        scene = case.get('scenario', {})
        if (params.get('method') != method or params.get('mode') != 'N' or params.get('configuration') != 'H2'
            or params.get('start_hour') != 1 or params.get('duration_minutes') != 525600.0 or params.get('calendar') != []
            or case.get('configuration') != 'H2' or scene.get('canonical_class') != 'N'
            or scene.get('start_hour') != 1 or scene.get('duration_steps') != 35040
            or scene.get('step_minutes') != 15.0 or scene.get('model_closure_events') != []):
            raise CheckError('Only registered H2 N 8760-hour cases are allowed')
        shared.append((scene.get('exogenous_stream_id'), scene.get('base_state_id')))
        if not all(shared[-1]):
            raise CheckError('Cases lack shared exogenous/initial identity')
        cases[method] = dict(row)
    if len(set(shared)) != 1:
        raise CheckError('Normal cases must share exogenous stream and initial state')
    return dict(status='passed', package_root=str(root), source_commit=pinned_commit,
        source_tree_sha256=scientific_sha256, input_sha256=input_sha256,
        source_count=source_count, input_count=input_count, manifest_sha256=sha256(manifest_path),
        data_approval_sha256=sha256(root / 'evidence/DATA_APPROVAL.json'), cases=cases)

def host_probe(package_root, work_root):
    verified = verify_package(package_root)
    work = external_work_root(package_root, work_root)
    if os.name == 'nt' and work.drive.upper() != 'E:':
        raise CheckError('Frozen Windows science stop-file contract requires an E: work-root')
    host = current_host(work)
    versions = {}
    for name in ('psutil', 'gurobipy', 'numpy', 'scipy', 'pytest'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return dict(status='observed', created_utc=now(), work_root=str(work), host=host,
        package=verified, distributions=versions, solver_probe_performed=False,
        scientific_execution_performed=False, admission_granted=False)

def current_host(work_root=None):
    model = platform.processor()
    if platform.system() == 'Linux':
        try:
            model = next(line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                if line.lower().startswith('model name'))
        except (OSError, StopIteration):
            pass
    elif os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                model = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
        except OSError:
            pass
    result = dict(hostname=socket.gethostname(), system=platform.system(), release=platform.release(),
        machine=platform.machine(), cpu_model=model, cpu_count_logical=os.cpu_count(),
        ram_total_bytes=None, ram_available_bytes=None, cpu_affinity=None, disk_free_bytes=None)
    try:
        import psutil
        memory = psutil.virtual_memory()
        result.update(ram_total_bytes=memory.total, ram_available_bytes=memory.available,
            cpu_affinity=psutil.Process().cpu_affinity())
    except (ImportError, AttributeError, OSError):
        pass
    existing = Path(work_root).resolve() if work_root is not None else Path.cwd()
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    result['disk_free_bytes'] = shutil.disk_usage(existing).free
    result['disk_probe_path'] = str(existing)
    result['topology'] = server_profile.probe_host(work_root)['host']['topology']
    return result

def identity_binding(package_root, work_root, host=None):
    package = verify_package(package_root)
    work = external_work_root(package_root, work_root)
    observed = host if host is not None else current_host(work)
    distributions = {}
    for name in ('psutil', 'gurobipy', 'numpy', 'scipy', 'pytest'):
        try:
            distributions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            distributions[name] = None
    return dict(host={key: observed.get(key) for key in HOST_KEYS}, work_root=str(work),
        runtime=dict(python_executable=str(Path(sys.executable).resolve()), python_version=platform.python_version(),
            distributions=distributions),
        source_commit=package['source_commit'], source_tree_sha256=package['source_tree_sha256'],
        input_sha256=package['input_sha256'], manifest_sha256=package['manifest_sha256'],
        data_approval_sha256=package['data_approval_sha256'],
        cases={method: row['canonical_sha256'] for method, row in package['cases'].items()})


def validate_limits(limits):
    if not isinstance(limits, dict):
        raise CheckError('Resource limits must be explicit JSON object')
    bounds = {'max_producers': (1, None), 'max_accounts': (1, None), 'max_total_processes': (1, None),
        'grace_seconds': (0, 120), 'max_recoveries_global': (1, 1), 'threads_per_producer': (1, 1),
        'restore_timeout_seconds': (1, 1800), 'poll_seconds': (0.01, 30), 'max_wall_seconds': (1, 2592000),
        'rss_limit_bytes': (1, None), 'min_available_ram_bytes': (1, None),
        'min_free_disk_bytes': (1, None), 'initial_free_disk_required_bytes': (1, None)}
    for key, (low, high) in bounds.items():
        value = limits.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < low or (high is not None and value > high):
            raise CheckError('Missing/invalid resource limit: ' + key)
        if key in ('max_producers', 'max_accounts', 'max_total_processes', 'max_recoveries_global', 'threads_per_producer') and type(value) is not int:
            raise CheckError('Integer process limit required: ' + key)
    from scripts.concurrency_policy import validate_counts
    try:validate_counts(limits)
    except ValueError as exc:raise CheckError(str(exc)) from exc
    return dict(limits)


def carrier_account_passed(value):
    """Original Counter zero categories may be absent; malformed values may not."""
    if (not isinstance(value, dict) or value.get('status') != 'passed'
        or value.get('original_bytes_unchanged') is not True or value.get('source_bytes_unchanged') is not True):
        return False
    counts = value.get('counts')
    if not isinstance(counts, dict) or set(counts) != {'e', 'c', 'steam'}:
        return False
    for carrier in counts.values():
        if (not isinstance(carrier, dict) or set(carrier) - {'passed', 'unknown', 'failed'}
            or type(carrier.get('passed')) is not int or carrier['passed'] <= 0):
            return False
        for category in ('unknown', 'failed'):
            if category in carrier and (type(carrier[category]) is not int or carrier[category] != 0):
                return False
    return True


def require_server_host(host):
    if host.get('system') != 'Windows':
        raise CheckError('This handoff admits the requested Windows server only')
    if 'EPYC' not in str(host.get('cpu_model')).upper() or '9654' not in str(host.get('cpu_model')):
        raise CheckError('Admission requires the declared AMD EPYC 9654 server')
    if (host.get('ram_total_bytes') or 0) < 240_000_000_000:
        raise CheckError('Admission requires the declared 256 GB RAM server')
    if host.get('cpu_affinity') == list(range(24)):
        raise CheckError('Legacy CPU0-23 affinity is not admitted on the target server')
    return host


def _resources(host, limits, initial=False):
    require_server_host(host)
    if (host.get('ram_available_bytes') or 0) < limits['min_available_ram_bytes']:
        raise CheckError('Insufficient currently available RAM')
    if limits['rss_limit_bytes'] >= host['ram_total_bytes']:
        raise CheckError('RSS limit must leave server memory headroom')
    required = max(limits['min_free_disk_bytes'], limits['initial_free_disk_required_bytes']) if initial else limits['min_free_disk_bytes']
    if (host.get('disk_free_bytes') or 0) < required:
        raise CheckError('Insufficient free disk for registered resource limits')
    if not host.get('cpu_affinity'):
        raise CheckError('CPU affinity could not be measured')


def validate_server_evidence(package_root, work_root, binding=None, limits=None):
    work = external_work_root(package_root, work_root)
    expected = binding if binding is not None else identity_binding(package_root, work)
    path = work / 'validation/VALIDATION.json'
    value = read_json(path)
    if (value.get('kind') != 'normal_year_server_validation' or value.get('status') != 'passed'
        or value.get('binding') != expected or value.get('annual_autostart') is not False):
        raise CheckError('Current server validation identity/status missing or differs')
    _fresh(value.get('created_utc'))
    for name in ('suite', 'license', 'short'):
        row = value.get('stages', {}).get(name)
        if not isinstance(row, dict):
            raise CheckError('Missing current server validation stage: ' + name)
        artifact = safe_relative(work, row.get('path'))
        if not artifact.is_file() or sha256(artifact) != row.get('sha256'):
            raise CheckError('Server validation artifact bytes differ: ' + name)
        stage = read_json(artifact)
        if stage.get('status') != 'passed' or stage.get('kind') != name or stage.get('binding') != expected:
            raise CheckError('Server validation stage identity/status differs: ' + name)
        _fresh(stage.get('created_utc'))
        commands = stage.get('commands')
        if not isinstance(commands, list) or not commands:
            raise CheckError('Missing actual command/exit/raw-log validation evidence')
        for reference in commands:
            command_path, command = _artifact(work, reference)
            if (not isinstance(command.get('command'), list) or not command['command']
                or command.get('cwd') != str((Path(package_root) / 'source').resolve())
                or type(command.get('elapsed_seconds')) not in (int, float)
                or command['elapsed_seconds'] < 0):
                raise CheckError('Invalid validation command provenance')
            if command.get('exit_code') != 0 and command.get('intentional_termination') is not True:
                raise CheckError('Unaccepted validation command failure')
            for stream in ('stdout', 'stderr'):
                log = command_path.parent / (stream + '.log')
                if not log.is_file() or sha256(log) != command.get(stream + '_sha256'):
                    raise CheckError('Original validation raw log bytes differ')
            closure_ref = command.get('closure')
            if not isinstance(closure_ref, dict) or closure_ref.get('path') != 'CLOSURE.json':
                raise CheckError('Actual validation process closure reference missing')
            closure_path = command_path.parent / 'CLOSURE.json'
            if not closure_path.is_file() or sha256(closure_path) != closure_ref.get('sha256'):
                raise CheckError('Original validation closure bytes differ')
            closure = read_json(closure_path)
            if (closure.get('tree_closed') is not True or closure.get('gate_acceptable') is not True
                or closure.get('remaining') != [] or closure.get('errors') != []
                or not isinstance(closure.get('observed_identities'), list) or not closure['observed_identities']):
                raise CheckError('Actual validation process closure failed or remains unknown')
        artifacts = {}
        for reference in stage.get('artifacts', []):
            role = reference.get('role')
            if role in artifacts:
                raise CheckError('Duplicate validation artifact role')
            _, artifacts[role] = _artifact(work, reference)
        if name == 'suite' and stage.get('exit_code') != 0:
            raise CheckError('Full existing test suite did not pass')
        if name == 'license':
            workers = stage.get('workers', 0)
            needed = limits['max_producers'] if limits is not None else 1
            if (stage.get('full_size_model_verified') is not True or type(workers) is not int or workers < needed
                or stage.get('all_exit_codes') != [0] * workers or stage.get('concurrent') is not True):
                raise CheckError('Full-size concurrent solver/license validation missing')
            proof = artifacts.get('license_concurrency', {})
            ready = proof.get('ready', [])
            if (proof.get('workers') != workers or len(ready) != workers
                or any(p.get('status') != 'passed' or p.get('variables', 0) <= 2000
                    or p.get('constraints', 0) <= 2000 or p.get('actual_threads') != 1 for p in ready)):
                raise CheckError('Full-size concurrent live model artifact missing or incomplete')
        if name == 'short' and stage.get('execution_profile') != execution_contract():
            raise CheckError('Short gate belongs to a historical execution profile')
        if name == 'short' and not (stage.get('actual_interrupted_cases') == ['normal', 'fault']
            and stage.get('independent_carrier_accounts_passed') is True
            and stage.get('exact_recovery_comparisons_passed') is True
            and stage.get('physical_elapsed_fields_retained') is True
            and stage.get('reset_count_one') is True and stage.get('accepted_result_cache') is False
            and stage.get('normal_minutes') == 60.0 and stage.get('fault_minutes') == 180.0):
            raise CheckError('Genuine short recovery/independent carrier validation missing')
        if name == 'short':
            actual = stage.get('producer_concurrency')
            total = stage.get('max_total_processes')
            # Physical/recovery evidence reports its measured short-run count.
            # It does not impose a permanent runtime count ceiling.
            if (type(actual) is not int or actual < 1 or type(total) is not int or total < actual
                or stage.get('threads_per_producer') != 1 or stage.get('normal_priority') is not True
                or stage.get('process_tree_stop_verified') is not True):
                raise CheckError('Scientific concurrency/thread/process-tree validation is insufficient')
            for kind in ('normal', 'fault'):
                comparison = artifacts.get('match_' + kind, {})
                require_match(comparison)
                if (comparison.get('status') != 'exact_match' or comparison.get('difference_count') != 0
                    or comparison.get('maximum_absolute_numeric_difference') != 0.0
                    or comparison.get('physical_elapsed_fields_retained') is not True):
                    raise CheckError('Original strict recovery comparison missing or failed')
                for phase in ('full', 'recovered'):
                    account = artifacts.get('account_' + kind + '_' + phase, {})
                    expected_producer = work / 'validation' / 'short' / (kind + '_' + phase)
                    verify_engineering_account(package_root, work / 'validation' / 'short' / ('account_' + kind + '_' + phase), expected_producer, account)
                    if not carrier_account_passed(account):
                        raise CheckError('Original independent carrier account missing, failed or unknown')
    return dict(path='validation/VALIDATION.json', sha256=sha256(path))


def _artifact(work, reference):
    if not isinstance(reference, dict):
        raise CheckError('Invalid validation artifact reference')
    path = safe_relative(work, reference.get('path'))
    if not path.is_file() or sha256(path) != reference.get('sha256'):
        raise CheckError('Original validation artifact bytes differ')
    return path, read_json(path)


def _fresh(value):
    try:
        timestamp = datetime.fromisoformat(value)
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    except (TypeError, ValueError) as error:
        raise CheckError('Current server validation timestamp missing') from error
    if not -300 <= age <= 7 * 24 * 3600:
        raise CheckError('Current server validation is stale or future-dated')

def require_admission(package_root, work_root):
    work = external_work_root(package_root, work_root)
    path = work / 'admission/ADMISSION.json'
    if not path.is_file():
        raise CheckError('Current server admission is missing')
    value = read_json(path)
    if value.get('kind') != 'normal_year_server_admission' or value.get('status') != 'passed':
        raise CheckError('Invalid current server admission')
    try:
        expires = datetime.fromisoformat(value['expires_utc'])
        created = datetime.fromisoformat(value['created_utc'])
        if not expires.tzinfo or not created.tzinfo or not created <= datetime.now(timezone.utc) < expires:
            raise CheckError('Expired or future-dated admission')
    except (KeyError, TypeError, ValueError) as error:
        raise CheckError('Invalid/expired admission dates') from error
    host = current_host(work)
    expected = identity_binding(package_root, work, host)
    if value.get('binding') != expected:
        raise CheckError('Admission host/source/package/case/work-root binding differs')
    limits = validate_limits(value.get('limits'))
    _resources(host, limits)
    validation = validate_server_evidence(package_root, work, expected, limits)
    if value.get('validation') != validation:
        raise CheckError('Admission server validation digest differs')
    return value

def create_admission(package_root, work_root, limits, valid_hours=168):
    if type(valid_hours) not in (int, float) or not math.isfinite(valid_hours) or not 0 < valid_hours <= 168:
        raise CheckError('Admission validity must be finite and at most 168 hours')
    work = external_work_root(package_root, work_root)
    if os.name == 'nt' and work.drive.upper() != 'E:':
        raise CheckError('Frozen Windows science stop-file contract requires E: work-root')
    host = current_host(work)
    limits = validate_limits(limits)
    _resources(host, limits, initial=True)
    binding = identity_binding(package_root, work, host)
    validation = validate_server_evidence(package_root, work, binding, limits)
    started = datetime.now(timezone.utc)
    value = dict(schema_version=1, kind='normal_year_server_admission', status='passed',
        created_utc=started.isoformat(), expires_utc=(started + timedelta(hours=valid_hours)).isoformat(),
        binding=binding, validation=validation, limits=limits, annual_autostart=False)
    write_new(work / 'admission/ADMISSION.json', value)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('verify', 'preflight', 'admit'))
    parser.add_argument('--package-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--work-root', type=Path)
    parser.add_argument('--limits-file', type=Path)
    parser.add_argument('--valid-hours', type=float, default=168)
    args = parser.parse_args(argv)
    if args.command == 'verify':
        result = verify_package(args.package_root)
    else:
        if args.work_root is None:
            parser.error('--work-root is required')
        if args.command == 'preflight':
            result = host_probe(args.package_root, args.work_root)
        else:
            if args.limits_file is None:
                parser.error('--limits-file is required for explicit admission')
            result = create_admission(args.package_root, args.work_root, read_json(args.limits_file), args.valid_hours)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == '__main__':
    main()
