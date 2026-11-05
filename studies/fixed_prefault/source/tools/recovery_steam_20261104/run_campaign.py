"""Prepare/freeze one R2 T05 trajectory after completed audited R1 evidence."""
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
from tools.recovery_dispatch_20261104.run_campaign import append, check_pin, copy_case_exact, pin, utc

DEFAULT_ROOT = Path('E:/hospital_ies_recovery_steam_20261104_run01')
R0_ROOT = Path('E:/hospital_ies_cooling_dynamics_20261104_run01')
R1_ROOT = Path('E:/hospital_ies_recovery_dispatch_20261104_run01')
R1_SOURCE = Path('D:/hospital_ies_recovery_dispatch_20261104')
BASE_COMMIT = '4d255c92c4a01fc0bf22956d6b343766ff02dbf3'


def safe_new_root(value):
    root = Path(value)
    if not root.is_absolute() or root.drive.upper() != 'E:' or '..' in root.parts:
        raise ValueError('Explicit absolute E result root required')
    root = root.resolve()
    if not root.name.startswith('hospital_ies_recovery_steam_20261104_'):
        raise ValueError('Named new R2 result root required')
    for old in (R0_ROOT, R1_ROOT, Path('E:/hospital_ies_cooling_dynamics_20261104_u_fix01'), Path('E:/hospital_ies_minute1_20261103_run01')):
        if root == old.resolve() or root.is_relative_to(old.resolve()):
            raise ValueError('R2 output must stay outside historical roots')
    return root


def validate_registration(value):
    items = value['producers']
    if len(items) != 1 or items[0]['label'] != 'T05_R2' or items[0]['variant'] != 'responsive' or items[0]['duration_minutes'] != 180:
        raise ValueError('Only one original responsive T05_R2 180-minute trajectory is authorized')
    if value['sampling_minutes'] != dict(load=1., physical_max=1., control=1.) or not value['intervention'].get('policy_id'):
        raise ValueError('Named R2 intervention and original one-minute sampling required')


def unchanged_r1_core():
    from tools.sci_engineering.runner import source_binding, input_binding
    before, after = source_binding(R1_SOURCE), source_binding(SOURCE)
    changed = [name for name, digest in before.items() if after.get(name) != digest]
    if changed or input_binding(R1_SOURCE) != input_binding(SOURCE):
        raise ValueError('Frozen R1 source/runtime input bytes changed: ' + ', '.join(changed))
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, check=True, capture_output=True, text=True).stdout.strip()
    if subprocess.run(['git', 'merge-base', '--is-ancestor', BASE_COMMIT, head], cwd=SOURCE, capture_output=True).returncode:
        raise ValueError('R2 checkout does not descend from the registered R1 commit')
    return dict(base_source_root=str(R1_SOURCE), base_commit=BASE_COMMIT, actual_head_commit=head,
                original_source_files=before, original_runtime_inputs=input_binding(R1_SOURCE),
                all_original_source_and_input_bytes_unchanged=True)


def prepare(root, r1_audit_path):
    root = safe_new_root(root)
    if r1_audit_path is None:
        raise ValueError('Completed passed R1 independent audit path is required before R2 registration')
    if any((root / name).exists() for name in ('REGISTRATION.json', 'SOURCE_BINDING.json', 'inputs', 'cases', 'runs')):
        raise FileExistsError('Fresh R2 registration directories required')
    if shutil.disk_usage(root.parent).free < 30 * 1024**3:
        raise RuntimeError('E free below 30 GiB')
    from tools.recovery_steam_20261104.policy import R2_CONFIG
    from tools.cooling_dynamics_20261104 import default_config
    core = unchanged_r1_core()
    audit = read(r1_audit_path)
    if audit.get('status') != 'passed' or audit.get('comparisons', {}).get('T05_R1', {}).get('status') != 'passed':
        raise ValueError('Full R1 independent T05 comparison has not passed')
    selection = read(R1_ROOT / 'SELECTION.json')
    r1 = selection['producers']['T05_R1']
    r1_path = Path(r1['producer'])
    check_pin(dict(path=str(r1_path / 'RESULT.json'), sha256=r1['result_sha256']))
    check_pin(r1['wrapper_completion'])
    result = read(r1_path / 'RESULT.json')
    if result.get('status') != 'completed' or result.get('observed_until_minute') != 180. or result.get('label') != 'T05_R1':
        raise ValueError('Full R1 T05 actual completion is required')
    audited_result_pins = audit['comparisons']['T05_R1']['pins']['R1']
    if not any(Path(row['path']).resolve() == (r1_path / 'RESULT.json').resolve()
               and row['sha256'] == r1['result_sha256'] for row in audited_result_pins):
        raise ValueError('Passed R1 audit does not bind this exact completed T05 producer')
    completion = read(r1['wrapper_completion']['path'])
    check_pin(completion['native_result'])
    if completion['native_result']['sha256'] != r1['result_sha256']:
        raise ValueError('R1 administrative completion and native result pins differ')
    family = read(R0_ROOT / 'FAMILY_SELECTION.json')
    r0 = family['producers']['T05_R']
    check_pin(r0['result'])
    check_pin(r0['original_source_binding'])
    r0_path = Path(r0['producer'])
    case = read(r0_path / 'CASE.json')
    if sha(r0_path / 'CASE.json') != r0['case_sha256'] or sha(r1_path / 'CASE.json') != r0['case_sha256']:
        raise ValueError('Original R0 and completed R1 case bytes differ')
    r0_config = read(R0_ROOT / 'REGISTRATION.json')['cooling_config']
    if r0_config != default_config():
        raise ValueError('Original cooling physics config changed')
    deps = {}
    def add(path):
        row = pin(path)
        deps[row['path']] = row
        return row
    for path in (r1_audit_path, R1_ROOT / 'SELECTION.json', R1_ROOT / 'REGISTRATION.json', R1_ROOT / 'SOURCE_BINDING.json',
                 R0_ROOT / 'FAMILY_SELECTION.json', R0_ROOT / 'REGISTRATION.json', r0['original_source_binding']['path'], r1['wrapper_completion']['path']):
        add(path)
    if 'launcher_admission' in r1:
        add(r1['launcher_admission']['path'])
    for producer in (r0_path, r1_path):
        for name in ('CASE.json', 'RESULT.json', 'RESOLVED_CASE.json', 'INITIAL_STATE.json', 'DECISIONS.jsonl', 'STATES.jsonl', 'SEGMENTS.jsonl', 'EVENTS.jsonl'):
            add(producer / name)
    for row in case['minute1']['minute_source']['files'].values():
        check_pin(row)
        add(row['path'])
    retained_t06 = selection['producers']['T06_R1']
    check_pin(dict(path=str(Path(retained_t06['producer']) / 'RESULT.json'), sha256=retained_t06['result_sha256']))
    check_pin(retained_t06['wrapper_completion'])
    add(Path(retained_t06['producer']) / 'RESULT.json')
    add(retained_t06['wrapper_completion']['path'])
    for folder in ('inputs', 'cases', 'runs', 'logs', 'temp'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    target = root / 'cases/T05_R2.json'
    copy_case_exact(r0_path / 'CASE.json', target, r0['case_sha256'])
    write(root / 'inputs/MINUTE_SOURCE_MANIFEST.json', case['minute1']['minute_source'])
    write(root / 'inputs/DEPENDENCY_PINS.json', dict(files=list(deps.values()), old_raw_and_minute_bytes_copied=0))
    write(root / 'BASELINE_BINDING.json', dict(R0=deepcopy(r0), R1=deepcopy(r1), R1_audit=pin(r1_audit_path),
        retained_T06_R1=deepcopy(retained_t06), R0_and_R1_case_bytes_identical=True,
        full_R1_T05_actual_evidence_is_separate_from_R0=True))
    write(root / 'BASE_CORE_UNCHANGED.json', core)
    registration = dict(schema='hospital_ies.recovery_steam.finite_registration.v1', created_utc=utc(), source_root=str(SOURCE),
        data_root=str(root), producers=[dict(label='T05_R2', variant='responsive', baseline_label='T05_R',
            R1_comparator_label='T05_R1', duration_minutes=180, start_hour=case['scenario']['start_hour'],
            fault_target=case['scenario']['fault_target'], case_file=str(target), case_sha256=sha(target),
            baseline_case_bytes_unchanged=True, historical_outcome_reused=False)],
        cooling_config=default_config(), intervention=deepcopy(R2_CONFIG),
        sampling_minutes=dict(load=1., physical_max=1., control=1.),
        R1_full_T05_comparison=deepcopy(r1), R1_passed_independent_audit=pin(r1_audit_path),
        retained_T06_R1=deepcopy(retained_t06), T06_rerun_authorized=False,
        T08_alias=dict(candidate='T05_R2', baseline='T05_R', separate_simulation=False),
        trajectories_start_at_minute=0., checkpoint_reset_at_repair=False,
        case_bytes_do_not_encode_new_intervention=True, old_results_reused_as_new_outcomes=False)
    validate_registration(registration)
    write(root / 'REGISTRATION.json', registration)
    write(root / 'RESOURCE_POLICY.json', dict(max_workers=1, solver_threads_per_process=1,
        minimum_available_GiB_before_start=10., boundary_stop_below_GiB=6., minimum_E_free_GiB=30.))
    print(json.dumps(dict(status='prepared_only', producers=['T05_R2'], exact_R0_case_bytes=True,
                          R1_actual_T05_completed_and_audited=True, simulations_started=0)), flush=True)
    return 0


def freeze(root):
    root = safe_new_root(root)
    if (root / 'SOURCE_BINDING.json').exists():
        raise FileExistsError('Source freeze is immutable')
    from tools.sci_engineering.runner import source_binding, input_binding
    from tools.recovery_steam_20261104.policy import R2_CONFIG
    registration = read(root / 'REGISTRATION.json')
    validate_registration(registration)
    if registration['intervention'] != R2_CONFIG or registration['source_root'] != str(SOURCE):
        raise ValueError('R2 config/source changed after prepare')
    core = unchanged_r1_core()
    deps = read(root / 'inputs/DEPENDENCY_PINS.json')['files']
    for row in deps:
        check_pin(row)
    paths = [path for folder in ('inputs', 'cases') for path in (root / folder).rglob('*') if path.is_file()]
    paths += [root / name for name in ('REGISTRATION.json', 'RESOURCE_POLICY.json', 'BASELINE_BINDING.json', 'BASE_CORE_UNCHANGED.json')]
    write(root / 'SOURCE_BINDING.json', dict(schema='hospital_ies.recovery_steam.source_binding.v1', created_utc=utc(),
        source_root=str(SOURCE), git_base_commit=BASE_COMMIT, git_actual_head_commit=core['actual_head_commit'],
        source_files=source_binding(SOURCE), runtime_inputs=input_binding(SOURCE), new_input_files=[pin(path) for path in sorted(paths)],
        external_dependency_files=deps, intervention=deepcopy(R2_CONFIG), original_base_source=core,
        exact_source_bytes_are_authoritative=True, original_R1_source_and_results_unchanged=True))
    from tools.recovery_steam_20261104.run_worker import verify_binding
    verify_binding(root)
    print(json.dumps(dict(status='source_frozen_only', sha256=sha(root / 'SOURCE_BINDING.json'), simulations_started=0)), flush=True)
    return 0


def run_campaign(root):
    import psutil
    from tools.recovery_steam_20261104.run_worker import verify_binding
    root = safe_new_root(root)
    verify_binding(root)
    if (root / 'STOP.request').exists():
        raise RuntimeError('Preserved STOP request requires supervised review before a fresh launch')
    if (root / 'SELECTION.json').exists():
        raise FileExistsError('R2 selection already exists; no automatic rerun')
    if psutil.virtual_memory().available < 10 * 1024**3 or shutil.disk_usage(root).free < 30 * 1024**3:
        raise RuntimeError('R2 RAM/disk admission guard failed; no trajectory started')
    attempt = 1
    while any((root / folder / ('T05_R2_attempt' + str(attempt).zfill(2))).exists() for folder in ('runs', 'temp')):
        attempt += 1
    output = root / 'runs' / ('T05_R2_attempt' + str(attempt).zfill(2))
    temporary = root / 'temp' / output.name
    temporary.mkdir(parents=True)
    stage = root / 'logs' / ('campaign_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%f'))
    stage.mkdir()
    command = [sys.executable, '-m', 'tools.recovery_steam_20261104.run_worker', '--root', str(root), '--label', 'T05_R2', '--attempt', str(attempt)]
    environment = dict(os.environ, TEMP=str(temporary), TMP=str(temporary), OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
    with (stage / 'T05_R2.stdout.log').open('xb') as stdout, (stage / 'T05_R2.stderr.log').open('xb') as stderr:
        process = subprocess.Popen(command, cwd=SOURCE, env=environment, stdout=stdout, stderr=stderr,
                                   creationflags=0x08000000 if os.name == 'nt' else 0)
        create_time = psutil.Process(process.pid).create_time()
        write(stage / 'START.json', dict(command=command, pid=process.pid, create_time=create_time, utc=utc()))
        try:
            while process.poll() is None:
                if psutil.virtual_memory().available < 6 * 1024**3 or shutil.disk_usage(root).free < 30 * 1024**3:
                    if not (root / 'STOP.request').exists():
                        (root / 'STOP.request').write_text('Owned resource boundary stop ' + utc(), encoding='utf8')
                time.sleep(2.)
        except BaseException:
            if not (root / 'STOP.request').exists():
                (root / 'STOP.request').write_text('Owned supervisor boundary stop ' + utc(), encoding='utf8')
            try:
                process.wait(timeout=60.)
            except subprocess.TimeoutExpired:
                owned = psutil.Process(process.pid)
                if owned.create_time() == create_time:
                    owned.terminate()
                    owned.wait(timeout=10.)
            raise
    result = read(output / 'RESULT.json') if (output / 'RESULT.json').exists() else dict(status='failed', error='No native RESULT')
    receipt = dict(label='T05_R2', attempt=attempt, producer=str(output), pid=process.pid, create_time=create_time,
                   exit_code=process.returncode, status=result.get('status'), error=result.get('error'), utc=utc())
    append(root / 'ATTEMPTS.jsonl', receipt)
    if process.returncode == 0 and result.get('status') == 'completed':
        sidecar = output / 'WRAPPER_COMPLETION.json'
        completion = read(sidecar)
        check_pin(completion['native_result'])
        write(root / 'SELECTION.json', dict(schema='hospital_ies.recovery_steam.selection.v1', producers={
            'T05_R2': dict(producer=str(output), result_sha256=sha(output / 'RESULT.json'), attempt=attempt,
                           wrapper_completion=pin(sidecar), actual_process_exit_code=process.returncode)}))
        rc = 0
    else:
        rc = 1
    write(stage / 'RESULT.json', dict(status='completed' if rc == 0 else 'incomplete', actual_attempt=receipt,
        source_binding=pin(root / 'SOURCE_BINDING.json'), all_owned_workers_exited=True))
    print(json.dumps(receipt), flush=True)
    return rc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'freeze', 'run'))
    parser.add_argument('--root', default=str(DEFAULT_ROOT))
    parser.add_argument('--r1-audit')
    args = parser.parse_args()
    root = safe_new_root(args.root)
    record = dict(command=[sys.executable, '-m', 'tools.recovery_steam_20261104.run_campaign'] + sys.argv[1:], started_utc=utc())
    try:
        rc = prepare(root, args.r1_audit) if args.command == 'prepare' else freeze(root) if args.command == 'freeze' else run_campaign(root)
    except Exception as exc:
        record['error'] = dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
        traceback.print_exc()
        rc = 1
    record.update(exit_code=rc, ended_utc=utc())
    append(root / 'logs/COMMANDS.jsonl', record)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
