"""Register exact R0 cases, freeze R1 source/input bytes, and admit two runs.

prepare and freeze never create a simulator. Existing bound E: minute inputs
are referenced and independently checked; historical raw trajectories are not
copied. Only the explicit run subcommand can start physical trajectories.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

SOURCE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(SOURCE), str(SOURCE / 'src')]
from tools.cooling_dynamics_20261104.extract_results import read, write, sha

DEFAULT_ROOT = Path('E:/hospital_ies_recovery_dispatch_20261104_run01')
BASE_SOURCE = Path('D:/hospital_ies_noopt_boilerfix_20261104')
BASE_ROOT = Path('E:/hospital_ies_cooling_dynamics_20261104_run01')
BASE_COMMIT = '5d6c21d28f04890d0c1d673774775bf5cd686a43'
BASE_LABELS = ('T05_R', 'T06_R')
LABELS = ('T05_R1', 'T06_R1')


def utc():
    return datetime.now(timezone.utc).isoformat()


def append(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def safe_new_root(value):
    root = Path(value)
    if not root.is_absolute() or root.drive.upper() != 'E:' or '..' in root.parts:
        raise ValueError('Explicit absolute E result root without traversal required')
    root = root.resolve()
    if root == Path('E:/') or not root.name.startswith('hospital_ies_recovery_dispatch_20261104_'):
        raise ValueError('Named new recovery-dispatch E result root required')
    for protected in (BASE_ROOT, Path('E:/hospital_ies_cooling_dynamics_20261104_u_fix01'),
                      Path('E:/hospital_ies_minute1_20261103_run01')):
        if root == protected.resolve() or root.is_relative_to(protected.resolve()):
            raise ValueError('Recovery output must stay outside historical result/input roots')
    return root


def pin(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=sha(path), bytes=path.stat().st_size)


def check_pin(row):
    path = Path(row['path'])
    if not path.is_file() or sha(path) != row['sha256']:
        raise ValueError('Bound file changed or missing: ' + str(path))
    if 'bytes' in row and path.stat().st_size != row['bytes']:
        raise ValueError('Bound file size changed: ' + str(path))


def copy_case_exact(source, destination, expected_sha256):
    source, destination = Path(source), Path(destination)
    if sha(source) != expected_sha256:
        raise ValueError('Baseline case changed before exact copy')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as incoming, destination.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing)
    if sha(destination) != expected_sha256:
        raise ValueError('New case bytes differ from the baseline case')


def validate_registration(value):
    items = value['producers']
    if [item['label'] for item in items] != list(LABELS):
        raise ValueError('Only the registered T05_R1/T06_R1 producers are authorized')
    if any(item['variant'] != 'responsive' or item['duration_minutes'] != 180 for item in items):
        raise ValueError('Both candidates must preserve responsive continuous 180-minute cases')
    if value['sampling_minutes'] != dict(load=1., physical_max=1., control=1.):
        raise ValueError('Original one-minute sampling must be retained')
    if not value.get('intervention', {}).get('policy_id'):
        raise ValueError('A separately named recovery command-selection intervention is required')


def verify_base_core_unchanged():
    """Every pre-existing frozen source byte must match; additions are explicit."""
    from tools.sci_engineering.runner import source_binding, input_binding
    frozen = source_binding(BASE_SOURCE)
    current = source_binding(SOURCE)
    changed = [name for name, digest in frozen.items() if current.get(name) != digest]
    if changed:
        raise ValueError('Frozen source modified in recovery worktree: ' + ', '.join(changed))
    frozen_inputs, current_inputs = input_binding(BASE_SOURCE), input_binding(SOURCE)
    if frozen_inputs != current_inputs:
        raise ValueError('Original runtime input identity differs in recovery worktree')
    actual_base = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=SOURCE,
                                 capture_output=True, text=True, check=True).stdout.strip()
    ancestry = subprocess.run(['git', 'merge-base', '--is-ancestor', BASE_COMMIT, actual_base], cwd=SOURCE,
                              capture_output=True, text=True)
    if ancestry.returncode != 0:
        raise ValueError('Recovery checkout does not descend from the registered frozen commit')
    return dict(source_root=str(BASE_SOURCE.resolve()), base_commit=BASE_COMMIT,
                actual_recovery_head_commit=actual_base,
                base_source_files=frozen, all_original_source_bytes_unchanged=True,
                base_runtime_inputs=frozen_inputs, all_original_runtime_input_bytes_unchanged=True,
                added_source_files={name: digest for name, digest in current.items() if name not in frozen})


def prepare(root):
    root = safe_new_root(root)
    if any((root / name).exists() for name in ('REGISTRATION.json', 'SOURCE_BINDING.json', 'inputs', 'cases', 'runs')):
        raise FileExistsError('Fresh registration/input/case/run directories required: ' + str(root))
    if shutil.disk_usage(root.parent).free < 30 * 1024**3:
        raise RuntimeError('E free space below the 30 GiB finite-run guard')
    from tools.recovery_dispatch_20261104.policy import R1_CONFIG
    from tools.cooling_dynamics_20261104 import default_config
    core_receipt = verify_base_core_unchanged()
    selection_path = BASE_ROOT / 'FAMILY_SELECTION.json'
    family = read(selection_path)
    check_pin(family['source_family'])
    original = read(BASE_ROOT / 'REGISTRATION.json')
    if original['cooling_config'] != default_config():
        raise ValueError('Original cooling configuration differs from unchanged native configuration')
    dependencies = {str(selection_path.resolve()): pin(selection_path),
                    str((BASE_ROOT / 'REGISTRATION.json').resolve()): pin(BASE_ROOT / 'REGISTRATION.json'),
                    family['source_family']['path']: pin(family['source_family']['path'])}
    minute_files = {}
    producers, baselines = [], {}
    for base_label, label in zip(BASE_LABELS, LABELS):
        entry = family['producers'][base_label]
        if entry['variant'] != 'responsive' or entry['duration_minutes'] != 180:
            raise ValueError('Selected baseline is not the original responsive 180-minute producer')
        check_pin(entry['result'])
        check_pin(entry['original_source_binding'])
        result = read(entry['result']['path'])
        if result['status'] != 'completed' or result['label'] != base_label:
            raise ValueError('Baseline source producer is not complete under its original identity')
        case_path = Path(entry['producer']) / 'CASE.json'
        if sha(case_path) != entry['case_sha256'] or sha(entry['case_file']) != entry['case_sha256']:
            raise ValueError('Selected original case and producer case differ')
        case = read(case_path)
        if case['minute1']['variant'] != 'responsive' or case['scenario']['duration_steps'] != 180 or case['scenario']['step_minutes'] != 1.:
            raise ValueError('Original case sampling/variant/duration contract differs')
        for role, row in case['minute1']['minute_source']['files'].items():
            path = Path(row['path'])
            if not path.is_absolute() or path.drive.upper() != 'E:':
                raise ValueError('An existing absolute E minute input is required')
            check_pin(row)
            if role in minute_files and minute_files[role] != row:
                raise ValueError('The two baseline cases do not share exact minute source inputs')
            minute_files[role] = deepcopy(row)
            dependencies[str(path.resolve())] = pin(path)
        baseline_files = [Path(entry['result']['path']), Path(entry['original_source_binding']['path']),
                          Path(entry['case_file']), case_path]
        baseline_files += [Path(entry['producer']) / name for name in
                           ('RESOLVED_CASE.json', 'INITIAL_STATE.json', 'DECISIONS.jsonl', 'STATES.jsonl', 'SEGMENTS.jsonl', 'EVENTS.jsonl')]
        baseline_pins = [pin(path) for path in baseline_files]
        dependencies.update({row['path']: row for row in baseline_pins})
        target = root / 'cases' / (label + '.json')
        copy_case_exact(case_path, target, entry['case_sha256'])
        original_item = next(item for item in original['producers'] if item['label'] == base_label)
        producers.append(dict(label=label, variant='responsive', baseline_label=base_label,
                              start_hour=original_item['start_hour'], duration_minutes=180,
                              fault_target=original_item['fault_target'], case_file=str(target),
                              case_sha256=sha(target), baseline_case_bytes_unchanged=True,
                              intervention_policy_id=R1_CONFIG['policy_id'], historical_outcome_reused=False))
        baselines[base_label] = dict(selection=deepcopy(entry), files=baseline_pins,
                                    role='R0 original actual outcome; not a new R1 result')
    for folder in ('inputs', 'runs', 'logs', 'temp'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    minute_manifest = dict(schema='hospital_ies.ep_minute_source_manifest.v1', files=minute_files)
    write(root / 'inputs/MINUTE_SOURCE_MANIFEST.json', minute_manifest)
    write(root / 'inputs/DEPENDENCY_PINS.json', dict(files=list(dependencies.values()),
                                                   reused_external_minute_input_bytes=True,
                                                   baseline_raw_files_copied=0, minute_input_bytes_copied=0))
    write(root / 'BASELINE_BINDING.json', dict(selection=pin(selection_path), producers=baselines,
                                             original_source_identity_preserved=True))
    write(root / 'BASE_CORE_UNCHANGED.json', core_receipt)
    registration = dict(schema='hospital_ies.recovery_dispatch.finite_registration.v1', created_utc=utc(),
                        source_root=str(SOURCE), data_root=str(root), producers=producers,
                        cooling_config=default_config(), intervention=deepcopy(R1_CONFIG),
                        baselines=str(root / 'BASELINE_BINDING.json'),
                        sampling_minutes=dict(load=1., physical_max=1., control=1.),
                        T08_alias=dict(candidate='T05_R1', baseline='T05_R', separate_simulation=False),
                        trajectories_start_at_minute=0., checkpoint_reset_at_repair=False,
                        old_results_reused_as_new_outcomes=False,
                        native_physics_and_control_execution_retained=True,
                        case_bytes_do_not_encode_new_intervention=True)
    validate_registration(registration)
    write(root / 'REGISTRATION.json', registration)
    write(root / 'RESOURCE_POLICY.json', dict(max_workers=2, solver_threads_per_process=1,
                                             minimum_available_GiB_before_start=10., boundary_stop_below_GiB=6.,
                                             minimum_E_free_GiB=30., worker_temp_stopfile_isolated=True))
    print(json.dumps(dict(status='prepared_only', root=str(root), producers=list(LABELS),
                          exact_baseline_case_bytes=True, minute_input_bytes_copied=0,
                          formal_experiments_started=0)), flush=True)
    return 0


def freeze(root):
    root = safe_new_root(root)
    if (root / 'SOURCE_BINDING.json').exists():
        raise FileExistsError('Source freeze is immutable; retain it and register a new root for changes')
    from tools.sci_engineering.runner import source_binding, input_binding
    from tools.recovery_dispatch_20261104.policy import R1_CONFIG
    registration = read(root / 'REGISTRATION.json')
    validate_registration(registration)
    if registration['source_root'] != str(SOURCE) or registration['intervention'] != R1_CONFIG:
        raise ValueError('Runtime source/intervention changed after preparation')
    core_receipt = verify_base_core_unchanged()
    if read(root / 'BASE_CORE_UNCHANGED.json') != core_receipt:
        # Added source files may legitimately have been completed since prepare.
        # Only original bytes are invariant; actual completed additions are frozen below.
        if read(root / 'BASE_CORE_UNCHANGED.json')['base_source_files'] != core_receipt['base_source_files']:
            raise ValueError('Original frozen source identity changed')
    dependencies = read(root / 'inputs/DEPENDENCY_PINS.json')['files']
    for row in dependencies:
        check_pin(row)
    paths = [path for folder in ('inputs', 'cases') for path in (root / folder).rglob('*') if path.is_file()]
    paths += [root / name for name in ('REGISTRATION.json', 'RESOURCE_POLICY.json', 'BASELINE_BINDING.json', 'BASE_CORE_UNCHANGED.json')]
    binding = dict(schema='hospital_ies.recovery_dispatch.source_binding.v1', created_utc=utc(),
                   source_root=str(SOURCE), git_base_commit=BASE_COMMIT,
                   git_actual_head_commit=core_receipt['actual_recovery_head_commit'],
                   exact_source_bytes_are_authoritative=True, source_files=source_binding(SOURCE),
                   runtime_inputs=input_binding(SOURCE), new_input_files=[pin(path) for path in sorted(paths)],
                   external_dependency_files=dependencies, intervention=deepcopy(R1_CONFIG),
                   original_base_source=core_receipt, old_source_admission_reused=False,
                   scope='New R1 dispatch intervention; unchanged native physical lifecycle; independent engineering audit required')
    write(root / 'SOURCE_BINDING.json', binding)
    from tools.recovery_dispatch_20261104.run_worker import verify_binding
    verify_binding(root)
    print(json.dumps(dict(status='source_frozen_only', root=str(root), sha256=sha(root / 'SOURCE_BINDING.json'),
                          source_files=len(binding['source_files']), formal_experiments_started=0)), flush=True)
    return 0


def run_campaign(root, max_workers=2, labels=None):
    import psutil
    from tools.recovery_dispatch_20261104.run_worker import verify_binding
    root = safe_new_root(root)
    if type(max_workers) is not int or not 1 <= max_workers <= 2:
        raise ValueError('Authorized recovery concurrency is one or two workers')
    verify_binding(root)
    registration = read(root / 'REGISTRATION.json')
    validate_registration(registration)
    if labels is not None and (not labels or set(labels) - set(LABELS) or len(labels) != len(set(labels))):
        raise ValueError('Only unique registered R1 producer labels may be requested')
    pending = [label for label in LABELS if labels is None or label in labels]
    selection_path = root / 'SELECTION.json'
    selection = read(selection_path) if selection_path.exists() else dict(schema='hospital_ies.recovery_dispatch.selection.v1', producers={})
    for label, selected in selection['producers'].items():
        check_pin(dict(path=str(Path(selected['producer']) / 'RESULT.json'), sha256=selected['result_sha256']))
        if label not in LABELS or read(Path(selected['producer']) / 'RESULT.json')['status'] != 'completed':
            raise ValueError('Existing R1 selection is not a complete registered result')
    pending = [label for label in pending if label not in selection['producers']]
    stage = root / 'logs' / ('campaign_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%f'))
    stage.mkdir(parents=True)
    active, failures = {}, []
    began, last_status, stopping = time.perf_counter(), 0., False

    def save_selection():
        temp = root / 'SELECTION.tmp'
        temp.write_text(json.dumps(selection, indent=2) + '\n', encoding='utf8')
        os.replace(temp, selection_path)

    try:
        while pending or active:
            available = psutil.virtual_memory().available / 1024**3
            free = shutil.disk_usage(root).free / 1024**3
            if (root / 'STOP.request').exists() or available < 6. or free < 30.:
                stopping = True
                if not (root / 'STOP.request').exists():
                    (root / 'STOP.request').write_text('Resource guard boundary stop ' + utc(), encoding='utf8')
            for label, task in list(active.items()):
                rc = task['process'].poll()
                if rc is None:
                    continue
                task['stdout'].close()
                task['stderr'].close()
                producer = Path(task['producer'])
                result = read(producer / 'RESULT.json') if (producer / 'RESULT.json').is_file() else dict(status='failed', error='Worker exited without RESULT')
                event = dict(label=label, attempt=task['attempt'], producer=str(producer), pid=task['process'].pid,
                             create_time=task['create_time'], exit_code=rc, status=result.get('status'), error=result.get('error'), utc=utc())
                append(root / 'ATTEMPTS.jsonl', event)
                if rc == 0 and result.get('status') == 'completed':
                    selection['producers'][label] = dict(producer=str(producer), result_sha256=sha(producer / 'RESULT.json'), attempt=task['attempt'])
                    save_selection()
                else:
                    failures.append(event)
                    stopping = True
                    if not (root / 'STOP.request').exists():
                        (root / 'STOP.request').write_text(json.dumps(dict(reason='peer_producer_failed', failure=event)), encoding='utf8')
                print(json.dumps(event), flush=True)
                del active[label]
            while pending and not stopping and len(active) < max_workers and psutil.virtual_memory().available >= 10 * 1024**3:
                label, attempt = pending.pop(0), 1
                while any((root / folder / (label + '_attempt' + str(attempt).zfill(2))).exists()
                          for folder in ('runs', 'temp')):
                    attempt += 1
                producer = root / 'runs' / (label + '_attempt' + str(attempt).zfill(2))
                command = [sys.executable, '-m', 'tools.recovery_dispatch_20261104.run_worker', '--root', str(root), '--label', label, '--attempt', str(attempt)]
                stdout = (stage / (label + '.stdout.log')).open('xb')
                stderr = (stage / (label + '.stderr.log')).open('xb')
                worker_temp = root / 'temp' / (label + '_attempt' + str(attempt).zfill(2))
                worker_temp.mkdir(parents=True, exist_ok=False)
                environ = dict(os.environ, TEMP=str(worker_temp), TMP=str(worker_temp), OMP_NUM_THREADS='1',
                               OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
                try:
                    process = subprocess.Popen(command, cwd=SOURCE, env=environ, stdout=stdout, stderr=stderr,
                                               creationflags=0x08000000 if os.name == 'nt' else 0)
                except BaseException:
                    stdout.close()
                    stderr.close()
                    raise
                created = psutil.Process(process.pid).create_time()
                active[label] = dict(process=process, create_time=created, producer=str(producer), attempt=attempt, stdout=stdout, stderr=stderr)
                append(stage / 'STARTS.jsonl', dict(label=label, attempt=attempt, pid=process.pid, create_time=created,
                                                   command=command, cwd=str(SOURCE), initial_temp=str(worker_temp), utc=utc()))
                time.sleep(.5)
            now = time.perf_counter()
            if now - last_status >= 30.:
                print(json.dumps(dict(status='boundary_stopping' if stopping else 'running', active=list(active), pending=pending,
                                      available_GiB=available, max_workers=max_workers)), flush=True)
                last_status = now
            if stopping and not active:
                break
            if not active and pending and not stopping and available < 10.:
                append(stage / 'ADMISSION_WAITS.jsonl', dict(available_GiB=available, utc=utc()))
            time.sleep(2.)
    except BaseException:
        (root / 'STOP.request').write_text('Owned campaign exception boundary stop ' + utc(), encoding='utf8')
        deadline = time.time() + 60.
        for task in active.values():
            try:
                task['process'].wait(timeout=max(.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                process = psutil.Process(task['process'].pid)
                if process.create_time() == task['create_time']:
                    process.terminate()
                    process.wait(timeout=10.)
            finally:
                task['stdout'].close()
                task['stderr'].close()
        raise
    value = dict(status='completed' if not failures and not pending and not stopping else 'incomplete',
                 elapsed_seconds=time.perf_counter() - began, selected_producers=len(selection['producers']), failures=failures,
                 pending=pending, all_owned_workers_exited=not active, max_workers_requested=max_workers,
                 source_binding_sha256=sha(root / 'SOURCE_BINDING.json'))
    write(stage / 'RESULT.json', value)
    print(json.dumps(value), flush=True)
    return 0 if value['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'freeze', 'run'))
    parser.add_argument('--root', default=str(DEFAULT_ROOT))
    parser.add_argument('--max-workers', type=int, default=2)
    parser.add_argument('--labels', nargs='+')
    args = parser.parse_args()
    root = safe_new_root(args.root)
    invocation = dict(command=[sys.executable, '-m', 'tools.recovery_dispatch_20261104.run_campaign'] + sys.argv[1:],
                      cwd=str(Path.cwd()), started_utc=utc(), formal_run_requested=args.command == 'run')
    try:
        rc = prepare(root) if args.command == 'prepare' else freeze(root) if args.command == 'freeze' else run_campaign(root, args.max_workers, args.labels)
    except Exception as exc:
        invocation.update(error=dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc()))
        traceback.print_exc()
        rc = 1
    invocation.update(exit_code=rc, ended_utc=utc())
    append(root / 'logs/COMMANDS.jsonl', invocation)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
