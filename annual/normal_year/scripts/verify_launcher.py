"""Windows toy-process gate only. No scientific imports, solver or annual work."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from transfer_checks import current_host, external_work_root, HOST_KEYS, read_json, safe_relative, write_new
import psutil

TOOLS = ('launch_server.py', 'server_runner.py', 'scripts/durable_launch.py', 'scripts/verify_launcher.py',
    'scripts/parent_job_probe.py', 'scripts/console_probe.py', 'scripts/vendor/owned_process_hidden.py')
RAW_ROLES = {'process', 'parent', 'launch', 'start', 'driver', 'leaf', 'spec', 'request_spec',
    'supervisor_heartbeat', 'driver_heartbeat', 'leaf_heartbeat', 'command_exit', 'wmi'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def binding(package):
    host = current_host()
    return dict(host={key: host.get(key) for key in HOST_KEYS},
        tools={name: sha(Path(package) / name) for name in TOOLS})


def command_sha(command):
    return hashlib.sha256(json.dumps(command, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def valid_identity(item):
    return (isinstance(item, dict) and type(item.get('pid')) is int and item['pid'] > 0
        and type(item.get('create_time')) in (int, float) and math.isfinite(item['create_time'])
        and item['create_time'] > 0)


def inspect_raw_case(package, work, name, case):
    artifacts, roles = case.get('artifacts'), case.get('raw_roles')
    if not isinstance(artifacts, list) or not artifacts or not isinstance(roles, dict) or set(roles) != RAW_ROLES:
        raise ValueError('Independent launcher probe requires every minimum raw role')
    listed = {}
    for raw in artifacts:
        artifact = safe_relative(work, raw.get('path'))
        if raw.get('path') in listed or not artifact.is_file() or sha(artifact) != raw.get('sha256'):
            raise ValueError('Independent launcher probe raw artifact differs')
        listed[raw['path']] = raw
    paths = {}
    for role, row in roles.items():
        if not isinstance(row, dict) or listed.get(row.get('path')) != row:
            raise ValueError('Independent launcher raw role is not bound to an artifact')
        paths[role] = safe_relative(work, row['path'])
    if len(set(paths.values())) != len(paths):
        raise ValueError('Independent launcher raw roles must be distinct')
    raw = {role: read_json(path) for role, path in paths.items() if not role.endswith('heartbeat')}
    process, parent, launch, start = (raw[key] for key in ('process', 'parent', 'launch', 'start'))
    supervisor = process.get('supervisor_identity')
    driver, leaf = raw['driver'], raw['leaf']
    if (not all(valid_identity(item) for item in (supervisor, driver, leaf, parent.get('launcher_identity')))
        or any(alive(item) for item in (supervisor, driver, leaf, parent['launcher_identity']))):
        raise ValueError('Independent launcher raw identity is invalid or remains alive')
    if (parent.get('schema') != 'hospital_ies.known_parent_job_close.v1' or parent.get('method') != 'wmi'
        or parent.get('owned_job_closed') is not True or type(parent.get('launcher_exit_code')) is not int
        or parent['launcher_exit_code'] != 0 or parent.get('launcher_identity_still_alive') is not False):
        raise ValueError('Independent launcher raw parent closure failed')
    if (launch.get('schema') != 'hospital_ies.durable_launch_receipt.v1' or launch.get('method') != 'wmi'
        or launch.get('same_windows_user') is not True or launch.get('supervisor_identity') != supervisor
        or start.get('supervisor_identity') != supervisor or process.get('driver_identity') != driver):
        raise ValueError('Independent launcher raw process identities differ')
    spec = raw['spec']
    if spec != raw['request_spec'] or not isinstance(spec.get('command'), list) or not spec['command']:
        raise ValueError('Independent launcher raw spec binding differs')
    expected_supervisor = [sys.executable, '-S', str(package / 'scripts/durable_launch.py'),
        'supervise', '--spec', str(paths['spec'])]
    expected_launcher = [sys.executable, '-S', str(package / 'scripts/durable_launch.py'),
        'launch', '--method', 'wmi', '--spec', str(paths['request_spec'])]
    expected_parent = [sys.executable, '-B', str(package / 'scripts/parent_job_probe.py'),
        '--spec', str(paths['request_spec']), '--method', 'wmi']
    if (launch.get('command') != expected_supervisor or raw['command_exit'].get('command') != expected_parent
        or type(raw['command_exit'].get('exit_code')) is not int or raw['command_exit']['exit_code'] != 0):
        raise ValueError('Independent launcher raw command or exit differs')
    for item, command in ((supervisor, expected_supervisor), (driver, spec['command']),
                          (parent['launcher_identity'], expected_launcher)):
        digest = command_sha(command)
        if item.get('command_sha256') != digest or item.get('observed_command_sha256') != digest:
            raise ValueError('Independent launcher raw command identity differs')
    expected = dict(command_sha256=command_sha(spec['command']), source_sha256=sha(package / 'scripts/durable_launch.py'),
        owned_process_sha256=sha(package / 'scripts/vendor/owned_process_hidden.py'), specfile_sha256=sha(paths['spec']))
    if any(value.get(key) != digest for value in (start, process) for key, digest in expected.items()):
        raise ValueError('Independent launcher raw source/spec binding differs')
    wmi = raw['wmi']
    try:
        response = json.loads(wmi['stdout'])
        wmi_ok = (type(wmi.get('exit_code')) is int and wmi['exit_code'] == 0
            and type(response.get('ReturnValue')) is int and response['ReturnValue'] == 0
            and response.get('ProcessId') == supervisor['pid'])
    except (KeyError, TypeError, ValueError):
        wmi_ok = False
    if not wmi_ok:
        raise ValueError('Independent launcher raw WMI response failed')
    expected_reason = {'natural': None, 'cooperative': 'explicit_stop', 'forced': 'wall_limit'}[name]
    samples = process.get('job_query_samples')
    if (process.get('schema') != 'hospital_ies.durable_process_receipt.v1' or process.get('status') != 'exited'
        or type(process.get('exit_code')) is not int or (process['exit_code'] == 0) != (name != 'forced')
        or process.get('stop_reason') != expected_reason or process.get('force_killed') is not (name == 'forced')
        or process.get('owned_tree_closed') is not True or process.get('remaining_owned_identities') != []
        or process.get('cleanup_errors') or process.get('job_membership_verified') is not True
        or process.get('job_closed_after_empty') is not True or process.get('job_remaining_pids') != []
        or not isinstance(samples, list) or not samples or samples[-1].get('pids') != []):
        raise ValueError('Independent launcher raw process closure or exit failed')
    epochs = [process.get('started_epoch'), parent.get('epoch'), process.get('finished_epoch')]
    if (any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in epochs)
        or epochs != sorted(epochs)):
        raise ValueError('Independent launcher raw lifecycle timestamps differ')
    counts = []
    for role in ('supervisor_heartbeat', 'driver_heartbeat', 'leaf_heartbeat'):
        rows = heartbeats(paths[role], parent['epoch'])
        rows = [row for row in rows if row['epoch'] <= process['finished_epoch']]
        if role != 'supervisor_heartbeat':
            expected_pid = driver['pid'] if role == 'driver_heartbeat' else leaf['pid']
            rows = [row for row in rows if row.get('pid') == expected_pid]
        counts.append(len(rows))
    if min(counts) < 2 or case['post_parent_close_heartbeats'] > min(counts):
        raise ValueError('Independent launcher raw post-parent-close heartbeats are incomplete')


def require_launcher_probe(package, work_root):
    package = Path(package).resolve()
    work = Path(work_root).resolve()
    path = work / 'validation/LAUNCHER_PROBE.json'
    if not path.is_file():
        raise ValueError('Current independent launcher probe is missing')
    receipt = read_json(path)
    if receipt.get('launch_method') != 'wmi' or receipt.get('same_user_wmi') is not True:
        raise ValueError('Independent launcher probe requires explicit wmi launch method')
    if (receipt.get('kind') != 'normal_year_independent_launcher_probe' or receipt.get('status') != 'passed'
            or receipt.get('binding') != binding(package)):
        raise ValueError('Independent launcher probe binding or status differs')
    try:
        created = datetime.fromisoformat(receipt['created_utc'])
        if not created.tzinfo or not created <= datetime.now(timezone.utc) < created + timedelta(days=30):
            raise ValueError('Independent launcher probe expired or future-dated')
    except (KeyError, TypeError) as error:
        raise ValueError('Independent launcher probe expired or invalid timestamp') from error
    if set(receipt.get('cases', {})) != {'natural', 'cooperative', 'forced'}:
        raise ValueError('Independent launcher probe lacks required lifecycle cases')
    for name, row in receipt['cases'].items():
        case_path = safe_relative(work, row.get('path'))
        if not case_path.is_file() or sha(case_path) != row.get('sha256'):
            raise ValueError('Independent launcher probe artifact differs')
        case = read_json(case_path)
        if (case.get('status') != 'passed' or case.get('case') != name
                or case.get('parent_job_closed') is not True or case.get('post_parent_close_heartbeats', 0) < 2
                or case.get('owned_tree_closed') is not True or case.get('force_killed') is not (name == 'forced')):
            raise ValueError('Independent launcher probe lifecycle evidence is incomplete')
        inspect_raw_case(package, work, name, case)
    return receipt


def alive(identity):
    try:
        process = psutil.Process(identity['pid'])
        return process.create_time() == identity['create_time'] and process.is_running()
    except psutil.NoSuchProcess:
        return False


def heartbeats(path, epoch):
    if not path.is_file():
        return []
    values = []
    for line in path.read_text('utf8').splitlines():
        try:
            row = json.loads(line)
            if row.get('epoch', 0) > epoch:
                values.append(row)
        except ValueError:
            continue
    return values


def run_case(package, work, folder, name):
    directory = folder / name
    outer, inner = folder / (name + '.stop'), folder / (name + '_inner.stop')
    command = [sys.executable, '-S', str(package / 'scripts/console_probe.py'), '--directory', str(directory),
        '--seconds', '5' if name == 'natural' else '120']
    if name == 'cooperative':
        command += ['--stop', str(inner)]
    spec = dict(schema='hospital_ies.durable_launch.v1', command=command, cwd=str(package),
        run_directory=str(directory), max_seconds=5 if name == 'forced' else 20,
        grace_seconds=1 if name == 'forced' else 2, stop_file=str(outer),
        cooperative_stop_files=[str(inner)] if name == 'cooperative' else [])
    spec_path = folder / (name + '.SPEC.json')
    write_new(spec_path, spec)
    launch = [sys.executable, '-B', str(package / 'scripts/parent_job_probe.py'), '--spec', str(spec_path), '--method', 'wmi']
    started = time.monotonic()
    result = subprocess.run(launch, cwd=str(package), capture_output=True, text=True, encoding='utf8', timeout=28,
        creationflags=subprocess.CREATE_NO_WINDOW)
    (folder / (name + '.stdout.log')).write_text(result.stdout, 'utf8')
    (folder / (name + '.stderr.log')).write_text(result.stderr, 'utf8')
    write_new(folder / (name + '.COMMAND_EXIT.json'), dict(command=launch, exit_code=result.returncode))
    if result.returncode:
        raise ValueError('WMI known-parent-Job toy launch failed; no automatic fallback')
    parent = read_json(directory / 'PARENT_JOB_CLOSED.json')
    if (parent.get('owned_job_closed') is not True or parent.get('launcher_exit_code') != 0
            or parent.get('launcher_identity_still_alive') is not False or alive(parent['launcher_identity'])):
        raise ValueError('Known launcher-only parent Job closure was not proven')
    observed = 0
    while time.monotonic() - started < 30:
        counts = [len(heartbeats(directory / path, parent['epoch'])) for path in
            ('HEARTBEAT.jsonl', 'PROBE_HEARTBEAT.jsonl', 'LEAF_HEARTBEAT.jsonl')]
        observed = min(counts)
        if name == 'cooperative' and observed >= 2 and not outer.exists():
            outer.write_text('Synthetic cooperative stop after known parent Job closure\n', 'utf8')
        receipt_path = directory / 'PROCESS_RECEIPT.json'
        if receipt_path.is_file():
            process = read_json(receipt_path)
            if not alive(process['supervisor_identity']):
                break
        time.sleep(.1)
    else:
        outer.write_text('Toy lifecycle deadline exceeded\n', 'utf8')
        raise ValueError('Toy lifecycle did not close within its finite 30-second bound')
    process = read_json(directory / 'PROCESS_RECEIPT.json')
    if (observed < 2 or process.get('owned_tree_closed') is not True
            or process.get('remaining_owned_identities') or process.get('force_killed') is not (name == 'forced')):
        raise ValueError('Post-parent-close heartbeat or owned tree closure failed')
    expected_reason = {'natural': None, 'cooperative': 'explicit_stop', 'forced': 'wall_limit'}[name]
    if process.get('status') != 'exited' or process.get('stop_reason') != expected_reason:
        raise ValueError('Toy lifecycle ended for an unexpected reason')
    if name != 'forced' and process.get('exit_code') != 0:
        raise ValueError('Cooperative/natural toy process failed')
    sources = list(directory.rglob('*')) + [p for p in folder.iterdir() if p.is_file() and p.name.startswith(name + '.')]
    artifacts = [dict(path=p.relative_to(work).as_posix(), sha256=sha(p)) for p in sources if p.is_file()]
    role_paths = dict(process=directory / 'PROCESS_RECEIPT.json', parent=directory / 'PARENT_JOB_CLOSED.json',
        launch=directory / 'LAUNCH.json', start=directory / 'START.json', driver=directory / 'DRIVER_START.json',
        leaf=directory / 'LEAF_START.json', spec=directory / 'SPEC.json', request_spec=spec_path,
        supervisor_heartbeat=directory / 'HEARTBEAT.jsonl', driver_heartbeat=directory / 'PROBE_HEARTBEAT.jsonl',
        leaf_heartbeat=directory / 'LEAF_HEARTBEAT.jsonl', command_exit=folder / (name + '.COMMAND_EXIT.json'),
        wmi=directory / 'WMI_RESPONSE.json')
    roles = {role: dict(path=path.relative_to(work).as_posix(), sha256=sha(path)) for role, path in role_paths.items()}
    case = dict(case=name, status='passed', parent_job_closed=True, post_parent_close_heartbeats=observed,
        owned_tree_closed=True, force_killed=process['force_killed'], artifacts=artifacts, raw_roles=roles,
        scientific_work_started=False, elapsed_seconds=time.monotonic() - started)
    case_path = folder / (name + '.CASE.json')
    write_new(case_path, case)
    return dict(path=case_path.relative_to(work).as_posix(), sha256=sha(case_path))


def run_probes(package, work_root):
    if os.name != 'nt':
        raise ValueError('Windows-only toy launcher gate')
    package = Path(package).resolve()
    work = external_work_root(package, work_root)
    if work.drive.upper() != 'E:':
        raise ValueError('Toy launcher output and stop paths must use the actual E drive')
    folder = work / 'validation/launcher'
    folder.mkdir(parents=True, exist_ok=False)
    initial = binding(package)
    try:
        cases = {name: run_case(package, work, folder, name) for name in ('natural', 'cooperative', 'forced')}
        if binding(package) != initial:
            raise ValueError('Launcher source/host changed during toy gate')
        receipt = dict(kind='normal_year_independent_launcher_probe', status='passed',
            created_utc=datetime.now(timezone.utc).isoformat(), binding=initial, cases=cases,
            scientific_work_started=False, same_user_wmi=True, launch_method='wmi', automatic_fallback=False)
        write_new(work / 'validation/LAUNCHER_PROBE.json', receipt)
        return require_launcher_probe(package, work)
    except BaseException as error:
        write_new(folder / 'FAILURE.json', dict(status='failed', type=type(error).__name__, message=str(error),
            binding=initial, scientific_work_started=False, automatic_fallback=False))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true', help='Check prior same-host toy evidence without starting processes')
    args = parser.parse_args(argv)
    result = require_launcher_probe(PACKAGE, args.work_root) if args.check_only else run_probes(PACKAGE, args.work_root)
    print(json.dumps(dict(status=result['status'], scientific_work_started=False)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
