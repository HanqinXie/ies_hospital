"""Read-only package checks and explicit Windows server qualification.

Imports, verify and preflight do not create a scientific environment or solver.
The validate subcommand alone runs the registered short scientific gate.
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
import re
import shutil
import socket
import subprocess
import sys
sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parent))
from scripts.execution_profile import require_case, execution_contract
from scripts import server_profile

# Lazy case construction and raw-proof verification also import scientific
# modules in this parent process. Preserve audited source bytes even when the
# caller uses ordinary Python without -B or PYTHONDONTWRITEBYTECODE.
sys.dont_write_bytecode = True

METHODS = ('M_available', 'M_ready', 'C_ready')
HOST_KEYS = ('hostname', 'system', 'release', 'machine', 'cpu_model', 'ram_total_bytes')
PACKAGE = Path(__file__).resolve().parent
DEFAULT_LIMITS = dict(max_producers=3, max_accounts=1, max_total_processes=4,
    threads_per_producer=1, max_recoveries_global=1, max_wall_seconds=2_592_000,
    rss_limit_bytes=10_000_000_000, min_available_ram_bytes=16_000_000_000,
    min_free_disk_bytes=30_000_000_000, initial_free_disk_required_bytes=600_000_000_000,
    grace_seconds=120, restore_timeout_seconds=1800, poll_seconds=1)


class CheckError(ValueError):
    """An explicit identity, evidence or resource boundary is unmet."""


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    def reject(value):
        raise CheckError('Nonfinite JSON constant: ' + value)
    try:
        return json.loads(Path(path).read_text('utf-8-sig'), parse_constant=reject)
    except (OSError, ValueError) as error:
        raise CheckError('Missing or invalid JSON: ' + str(path)) from error


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def safe_relative(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise CheckError('An ordinary relative path is required')
    raw = Path(relative)
    if raw.is_absolute() or any(piece in ('', '.', '..') for piece in raw.parts):
        raise CheckError('Relative path contains an escape')
    root = Path(root).resolve()
    result = root / raw
    if not result.resolve().is_relative_to(root):
        raise CheckError('Path escapes the declared root')
    for piece in (result, *result.parents):
        if piece == root.parent:
            break
        if piece.is_symlink():
            raise CheckError('Symbolic link is not an immutable input')
    return result


def external_work_root(package_root, work_root):
    package, work = Path(package_root).resolve(), Path(work_root).resolve()
    if package == work or work.is_relative_to(package) or package.is_relative_to(work):
        raise CheckError('Research output must be external to the entire package')
    return work


def _excluded(relative):
    return (relative.parts[:2] in (('source', '.git'), ('legacy_context_7fa', '.git')) or '__pycache__' in relative.parts
        or '.pytest_cache' in relative.parts or relative.as_posix() == 'MANIFEST.json')


def verify_package(package_root):
    root = Path(package_root).resolve()
    manifest = read_json(root / 'MANIFEST.json')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries:
        raise CheckError('A sealed nonempty package manifest is required')
    listed = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise CheckError('Malformed manifest entry')
        name = entry.get('path')
        path = safe_relative(root, name)
        if name in listed or _excluded(Path(name)):
            raise CheckError('Duplicate or excluded manifest member')
        listed.add(name)
        if (not path.is_file() or type(entry.get('size_bytes')) is not int
                or path.stat().st_size != entry['size_bytes'] or sha256(path) != entry.get('sha256')):
            raise CheckError('Package bytes differ: ' + name)
    actual = set()
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if _excluded(relative):
            continue
        if path.is_symlink():
            raise CheckError('Package contains a symbolic link')
        if path.is_file():
            actual.add(relative.as_posix())
    if actual != listed:
        raise CheckError('Package manifest coverage differs')
    provenance = read_json(root / 'config' / 'SOURCE_PROVENANCE.json')
    for label, field, digest_field in (('scientific source', 'source', 'source_sha256'),
                                        ('runtime inputs', 'inputs', 'input_sha256')):
        mapping = provenance.get(field)
        if not isinstance(mapping, dict) or not mapping or canonical_sha256(mapping) != provenance.get(digest_field):
            raise CheckError('Invalid provenance map: ' + label)
        for name, digest in mapping.items():
            path = safe_relative(root / 'source', name)
            if not path.is_file() or sha256(path) != digest:
                raise CheckError(label + ' differs: ' + name)
    head = subprocess.run(['git', '-C', str(root / 'source'), 'rev-parse', 'HEAD'],
        text=True, capture_output=True, check=False)
    if head.returncode or head.stdout.strip() != provenance.get('git_commit'):
        raise CheckError('Independent scientific Git HEAD differs')
    git = root / 'source' / '.git'
    if (not git.is_dir() or git.is_symlink() or (git / 'commondir').exists()
            or (git / 'objects' / 'info' / 'alternates').exists()):
        raise CheckError('Scientific checkout must have an independent private Git directory')
    return dict(manifest_sha256=sha256(root / 'MANIFEST.json'),
        source_tree_sha256=provenance['source_sha256'], input_sha256=provenance['input_sha256'],
        git_commit=provenance['git_commit'], file_count=len(listed))


def current_host(work_root=None):
    import psutil
    cpu = platform.processor()
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                cpu = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
        except OSError:
            pass
    ram = psutil.virtual_memory()
    result = dict(hostname=socket.gethostname(), system=platform.system(), release=platform.release(),
        machine=platform.machine(), cpu_model=cpu, ram_total_bytes=ram.total,
        available_ram_bytes=ram.available, visible_cpu_count=os.cpu_count(),
        python=str(Path(sys.executable).resolve()), python_version=platform.python_version())
    if work_root is not None:
        path = Path(work_root).resolve()
        ancestor = path
        while not ancestor.exists():
            ancestor = ancestor.parent
        result['free_disk_bytes'] = shutil.disk_usage(ancestor).free
    observed = server_profile.probe_host(work_root)['host']
    result.update(topology=observed['topology'], cpu_affinity=observed.get('cpu_affinity'))
    return result


def require_server_host(host):
    memory = host.get('ram_total_bytes')
    if (host.get('system') != 'Windows' or not re.search(r'\bEPYC\s+9654\b', str(host.get('cpu_model')), re.I)
            or type(memory) is not int or memory < 240_000_000_000):
        raise CheckError('Annual admission requires Windows / EPYC 9654 / at least 240 GB measured RAM')
    if host.get('cpu_affinity') == list(range(24)):
        raise CheckError('Legacy CPU0-23 affinity is not admitted on the target server')
    return True


def _python_binding():
    versions = {}
    for name in ('gurobipy', 'numpy', 'pandas', 'scipy', 'psutil'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return dict(executable=str(Path(sys.executable).resolve()), version=platform.python_version(), packages=versions)


def load_frozen_cases(package_root, frozen_root):
    if frozen_root is None:
        raise CheckError('An explicitly resolved external frozen case directory is required')
    package = Path(package_root).resolve()
    frozen = external_work_root(package, frozen_root)
    verified = verify_package(package)
    index = read_json(frozen / 'INDEX.json')
    expected = dict(package_manifest_sha256=verified['manifest_sha256'],
        source_sha256=verified['source_tree_sha256'], input_sha256=verified['input_sha256'],
        git_commit=verified['git_commit'], calendar_sha256=sha256(frozen / 'CALENDAR.json'))
    if index.get('binding') != expected or index.get('calendar_file') != 'CALENDAR.json':
        raise CheckError('Frozen case index is not bound to this package/calendar/source')
    if index.get('calendar_sha256') != expected['calendar_sha256']:
        raise CheckError('Frozen calendar file changed')
    from calendar_plan import validate_plan, availability_events, verify_reference_proof
    plan = read_json(frozen / 'CALENDAR.json')
    validate_plan(plan, require_frozen=True)
    verify_reference_proof(plan)
    if read_json(frozen / 'REFERENCE_PROOF.json') != plan.get('reference_proof'):
        raise CheckError('Detached reference proof differs from the frozen calendar')
    rows = index.get('cases')
    if not isinstance(rows, list) or len(rows) != 3 or {row.get('method') for row in rows} != set(METHODS):
        raise CheckError('Exactly three matched methods are required')
    sys.path[:0] = [str(package / 'source'), str(package / 'source' / 'src')]
    from tools.annual_program.registration import validate_case
    events = availability_events(plan)
    cases, identities = [], set()
    for method in METHODS:
        row = next(item for item in rows if item['method'] == method)
        path = safe_relative(frozen, row.get('path'))
        if path.parent != frozen / 'cases' or sha256(path) != row.get('file_sha256'):
            raise CheckError('Frozen case path or SHA differs')
        case = read_json(path)
        require_case(case)
        validate_case(case)
        if canonical_sha256(case) != row.get('canonical_sha256'):
            raise CheckError('Frozen canonical case identity differs')
        parameters = case['annual_program']['parameters']
        values = dict(method=method, mode='FD', configuration='H2', start_hour=1,
            duration_minutes=525600., calendar=events, demand_amplitude=.25,
            demand_start_minute=280800., demand_duration_minutes=40320.)
        if any(parameters.get(key) != value for key, value in values.items()):
            raise CheckError('Only the matched H2 continuous composite year is admitted')
        if (row.get('mode') != 'FD' or row.get('configuration') != 'H2'
                or row.get('hours') != 8760 or row.get('source_hour') != 1
                or not case.get('id') or case['id'] in identities):
            raise CheckError('Frozen case metadata differs')
        identities.add(case['id'])
        cases.append(dict(row, file=str(path), case_id=case['id']))
    return cases


def identity_binding(package_root, frozen_root, work_root):
    package = Path(package_root).resolve()
    work = external_work_root(package, work_root)
    frozen = external_work_root(package, frozen_root)
    if frozen == work or frozen.is_relative_to(work) or work.is_relative_to(frozen):
        raise CheckError('Runtime and frozen-case directories must not overlap')
    plan = read_json(frozen / 'CALENDAR.json')
    original = plan.get('reference_proof', {}).get('reference_output')
    if not isinstance(original, str) or not Path(original).is_absolute():
        raise CheckError('An explicit original normal reference path is required')
    reference = Path(original).resolve()
    for destination in (work, frozen):
        if (destination == reference or destination.is_relative_to(reference)
                or reference.is_relative_to(destination)):
            raise CheckError('Runtime/frozen outputs must not overlap the read-only original normal reference')
    verified = verify_package(package)
    host = current_host()
    return dict(package=str(package), frozen_root=str(frozen), work_root=str(work),
        package_identity=verified, frozen_index_sha256=sha256(frozen / 'INDEX.json'),
        calendar_sha256=sha256(frozen / 'CALENDAR.json'),
        host={key: host.get(key) for key in HOST_KEYS}, python=_python_binding())


def preflight(package_root, frozen_root, work_root):
    package = Path(package_root).resolve()
    work = external_work_root(package, work_root)
    require_server_host(current_host(work))
    if os.name != 'nt' or work.drive.upper() != 'E:':
        raise CheckError('Annual work and stop paths require an actual Windows E drive')
    load_frozen_cases(package, frozen_root)
    host = current_host(work)
    if host['available_ram_bytes'] < DEFAULT_LIMITS['min_available_ram_bytes']:
        raise CheckError('At least 16 GB currently available RAM is required')
    if host['free_disk_bytes'] < DEFAULT_LIMITS['initial_free_disk_required_bytes']:
        raise CheckError('At least 600 GB current free work space is required')
    return identity_binding(package, frozen_root, work)


def carrier_account_passed(result):
    from server_runner import account_passed
    return account_passed(result, 0)


def validate_limits(value):
    from server_runner import validate_limits as checked_limits
    limits=checked_limits(value)
    if limits['max_wall_seconds']>2_592_000:raise CheckError('Registered finite wall budget exceeded')
    return limits


def require_admission(package_root, work_root, frozen_root):
    binding = preflight(package_root, frozen_root, work_root)
    work = Path(work_root).resolve()
    path = work / 'admission' / 'ADMISSION.json'
    receipt = read_json(path)
    if receipt.get('status') != 'passed' or receipt.get('binding') != binding:
        raise CheckError('Fresh target validation/admission is required')
    stamp = datetime.fromisoformat(receipt.get('created_utc', '').replace('Z', '+00:00'))
    current = datetime.now(timezone.utc)
    if stamp.tzinfo is None or not timedelta(0) <= current - stamp <= timedelta(days=7):
        raise CheckError('Server admission is expired or from the future')
    limits=validate_limits(receipt.get('limits'))
    validation = work / 'validation' / 'SCIENTIFIC_GATE.json'
    if receipt.get('validation_sha256') != sha256(validation):
        raise CheckError('Scientific gate receipt changed')
    gate = read_json(validation)
    if gate.get('binding') != binding or gate.get('status') != 'passed' or gate.get('execution_profile') != execution_contract():
        raise CheckError('Scientific gate belongs to another source/host/case')
    from scripts.server_validation import verify_gate
    verify_gate(package_root, work_root, gate)
    if gate.get('runtime_limits')!=limits:raise CheckError('Configured resource limits differ from their gate binding')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('verify')
    for name in ('preflight', 'validate', 'check-admission'):
        command = sub.add_parser(name)
        command.add_argument('--frozen-root', type=Path, required=True)
        command.add_argument('--work-root', type=Path, required=True)
        if name=='validate':command.add_argument('--limits-file', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'verify':
        result = verify_package(PACKAGE)
    elif args.command == 'preflight':
        result = preflight(PACKAGE, args.frozen_root, args.work_root)
    elif args.command == 'validate':
        binding = preflight(PACKAGE, args.frozen_root, args.work_root)
        from scripts.server_validation import run_validation
        limits=validate_limits(read_json(args.limits_file) if args.limits_file else DEFAULT_LIMITS)
        result = run_validation(PACKAGE, args.work_root, binding, limits=limits)
    else:
        result = require_admission(PACKAGE, args.work_root, args.frozen_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
