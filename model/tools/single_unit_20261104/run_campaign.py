"""Prepare/freeze separately from explicitly started six-producer execution."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(SOURCE), str(SOURCE / 'src')]
from tools.cooling_dynamics_20261104.extract_results import read, write, sha

DEFAULT_ROOT = Path('E:/hospital_ies_single_unit_20261104_run01')
FAMILY_ROOT = Path('E:/hospital_ies_cooling_dynamics_20261104_run01')


def utc():
    return datetime.now(timezone.utc).isoformat()


def append(path, value):
    with Path(path).open('a', encoding='utf8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def pin(path, expected=None):
    path = Path(path)
    value = dict(path=str(path), sha256=sha(path), bytes=path.stat().st_size)
    if expected is not None and value['sha256'] != expected:
        raise ValueError('Authenticated original file changed: ' + str(path))
    return value


def safe_new_root(value):
    root = Path(value)
    if not root.is_absolute() or root.drive.upper() != 'E:' or '..' in root.parts:
        raise ValueError('Explicit absolute E result root required')
    root = root.resolve()
    if not root.name.startswith('hospital_ies_single_unit_20261104_'):
        raise ValueError('Named new single-unit result root required')
    return root


def admitted_limit(requested, current, available_GiB, observed_minutes):
    """Four initially; grow to six only after an actual healthy initial wave."""
    if type(requested) is not int or not 1 <= requested <= 6:
        raise ValueError('Authorized concurrency range is 1..6')
    initial = min(requested, 4)
    if current < requested and len(observed_minutes) >= initial and available_GiB >= 12.:
        if min(observed_minutes) >= 5.:
            return requested
    return min(current, requested)


def normal_reuse_proof(family):
    """Authenticate original identities plus the accepted native call closure.

    Current 1075 native files must match the accepted fixed source. N/R never
    enter the sole changed arithmetic forward implementation. The prior family
    certificate is retained as the original source identity, never replaced by
    this study's new source hash. Original N and its U/R base have exactly equal
    INITIAL_STATE and RESOLVED_CASE JSON values (checked below).
    """
    from tools.sci_engineering.runner import source_binding, input_binding
    family_pin = pin(FAMILY_ROOT / 'FAMILY_BINDING.json', family['source_family']['sha256'])
    envelope = read(family_pin['path'])
    if envelope['status'] != 'accepted_exact_native_reuse_envelope':
        raise ValueError('Accepted original native closure envelope required')
    static = envelope['static_proof']
    if static['status'] != 'passed_static_closure_only' or static['sole_changed_file'] != 'tools/noopt_unattended_20261104/forward.py':
        raise ValueError('Unexpected old/native source closure')
    fixed_pin = pin(static['new_source_binding']['path'], static['new_source_binding']['sha256'])
    fixed = read(fixed_pin['path'])
    current = source_binding(SOURCE)
    changed = [key for key, value in fixed['source_files'].items() if current.get(key) != value]
    if changed or len(fixed['source_files']) != 1075:
        raise ValueError('Native 1075-file source changed: ' + repr(changed))
    if input_binding(SOURCE) != fixed['runtime_inputs']:
        raise ValueError('Original native runtime inputs changed')
    external = [family_pin, fixed_pin, pin(static['old_source_binding']['path'], static['old_source_binding']['sha256'])]
    rows = []
    for alias, normal, bases in [('TOWER_N', 'T03_N', ('T05_U', 'T05_R')), ('PUMP_N', 'T06_N', ('T06_U', 'T06_R'))]:
        item = family['producers'][normal]
        producer = Path(item['producer'])
        result_pin = pin(item['result']['path'], item['result']['sha256'])
        result = read(result_pin['path'])
        if result['status'] != 'completed' or item['variant'] != 'normal':
            raise ValueError('Only original completed healthy normal can be reused')
        original_source = pin(item['original_source_binding']['path'], item['original_source_binding']['sha256'])
        if result['source_binding']['sha256'] != original_source['sha256']:
            raise ValueError('Normal RESULT original source identity mismatch')
        own_files = [pin(row['path'], row['sha256']) for row in result['files']]
        normal_initial = pin(producer / 'INITIAL_STATE.json')
        normal_resolved = pin(producer / 'RESOLVED_CASE.json')
        comparisons = []
        normal_case = read(item['case_file'])
        for base_label in bases:
            base = family['producers'][base_label]
            base_initial = pin(Path(base['producer']) / 'INITIAL_STATE.json')
            base_resolved = pin(Path(base['producer']) / 'RESOLVED_CASE.json')
            base_case_pin = pin(base['case_file'], base['case_sha256'])
            base_case = read(base_case_pin['path'])
            if read(base_initial['path']) != read(normal_initial['path']) or read(base_resolved['path']) != read(normal_resolved['path']):
                raise ValueError('Original normal/base physical initial or resolved case mismatch')
            if normal_case['configuration'] != base_case['configuration']:
                raise ValueError('Original normal/base configuration mismatch')
            if normal_case['scenario']['start_hour'] != base_case['scenario']['start_hour']:
                raise ValueError('Original normal/base exogenous background mismatch')
            for role in normal_case['minute1']['minute_source']['files']:
                if normal_case['minute1']['minute_source']['files'][role]['sha256'] != base_case['minute1']['minute_source']['files'][role]['sha256']:
                    raise ValueError('Original normal/base minute input bytes mismatch')
            comparisons.append(dict(base_label=base_label, original_base_case=base_case_pin,
                original_base_initial=base_initial, original_base_resolved=base_resolved,
                exact_initial_state_equal=True, exact_resolved_case_equal=True,
                unchanged_initial_effect_of_new_scene='Only minute60..120 lowering; original reset and pre60 preserved'))
            external += [base_initial, base_resolved, base_case_pin]
        row = dict(alias=alias, original_label=normal, producer=str(producer), result=result_pin,
            original_source_binding=original_source, original_case=pin(item['case_file'], item['case_sha256']),
            original_initial=normal_initial, original_resolved=normal_resolved,
            original_result_files=own_files, base_comparisons=comparisons,
            current_new_source_SHA_assigned_to_old_normal=False)
        rows.append(row)
        external += [result_pin, original_source, row['original_case'], normal_initial, normal_resolved] + own_files
    return dict(schema='hospital_ies.single_unit.normal_reuse_proof.v1', status='passed_static_native_closure_input_initial_binding',
        created_utc=utc(), accepted_original_family=family_pin, accepted_fixed_source=fixed_pin,
        native_source_files_checked=1075, native_source_differences=[], runtime_inputs_identical=True,
        static_gate=static['evaluate_forward_production_gate'], normal_rows=rows,
        no_single_source_SHA_claim=True, no_new_actual_normal_trajectory_claim=True,
        scope='Accepted original static native-call closure plus exact original input/config/initial state; not new measured normal outcomes'), external


def prepare(root):
    root = safe_new_root(root)
    if root.exists() and any(p.name not in ('component_checks', 'control') for p in root.iterdir()):
        raise FileExistsError('Fresh registration/run directories required: ' + str(root))
    if shutil.disk_usage(root.parent).free < 30 * 1024**3:
        raise RuntimeError('Insufficient E space for six finite new trajectories')
    family_pin = pin(FAMILY_ROOT / 'FAMILY_SELECTION.json')
    family = read(family_pin['path'])
    proof, external = normal_reuse_proof(family)
    from tools.single_unit_20261104.registration import make_case, validate_case
    from tools.cooling_dynamics_20261104 import default_config
    root.mkdir(exist_ok=True)
    for name in ('inputs', 'cases', 'runs', 'logs', 'temp'):
        (root / name).mkdir()
    (root / 'inputs/base_cases').mkdir()
    producers = []
    for kind, topic, prefix, base_prefix in [('tower_unit', 'SU_TOWER', 'TOWER', 'T05'), ('primary_pump', 'SU_PUMP', 'PUMP', 'T06')]:
        for study_variant, suffix, native_variant in [('U', 'U', 'unattended'), ('R', 'R', 'responsive'), ('U0', 'U', 'unattended')]:
            label = prefix + '_' + study_variant
            original = family['producers'][base_prefix + '_' + suffix]
            base_pin = pin(original['case_file'], original['case_sha256'])
            destination = root / 'inputs/base_cases' / (label + '.json')
            shutil.copyfile(base_pin['path'], destination)
            copied_pin = pin(destination, base_pin['sha256'])
            base = read(destination)
            case = make_case(base, kind='healthy_no_dispatch' if study_variant == 'U0' else kind,
                variant=native_variant, base_binding=dict(path=copied_pin['path'], sha256=copied_pin['sha256']))
            validate_case(case)
            case_path = root / 'cases' / (label + '.json')
            write(case_path, case)
            producers.append(dict(label=label, variant=native_variant, study_variant=study_variant,
                topic_id=topic, kind=kind, start_hour=case['scenario']['start_hour'], duration_minutes=180,
                case_file=str(case_path), case_sha256=sha(case_path), original_base_case=base_pin,
                historical_outcome_reused=False))
            external.append(base_pin)
            for row in base['minute1']['minute_source']['files'].values():
                external.append(pin(row['path'], row['sha256']))
            parent = base['minute1']['parent_binding']
            external.append(pin(parent['path'], parent['sha256']))
    write(root / 'NORMAL_REUSE_PROOF.json', proof)
    topics = []
    for topic, prefix, title in [('SU_TOWER', 'TOWER', 'Single tower-unit outage'), ('SU_PUMP', 'PUMP', 'Single primary-pump branch outage')]:
        topics.append(dict(id=topic, title=title, duration_minutes=180,
            fault_start_minute=60., fault_end_minute=120., producers=dict(normal=prefix+'_N',
                unattended=prefix+'_U', responsive=prefix+'_R', unattended_healthy=prefix+'_U0')))
    registration = dict(schema='hospital_ies.single_unit.finite_registration.v1', created_utc=utc(),
        source_root=str(SOURCE), data_root=str(root), producers=producers, topics=topics,
        cooling_config=default_config(), unique_new_producers=6, reused_normal_producers=2,
        normal_reuse_proof=dict(path=str(root/'NORMAL_REUSE_PROOF.json'), sha256=sha(root/'NORMAL_REUSE_PROOF.json')),
        family_selection=family_pin, sampling_minutes=dict(load=1., physical_max=1., control=1.),
        old_common_fault_cases_unchanged=True, baseline_canonical_cases_copied_byte_exact=True,
        original_E_minute_input_paths_retained_with_byte_pins=True,
        qualification='Research aggregate slot / functional interlock; no OEM mapping or measured hydraulic flow')
    write(root / 'REGISTRATION.json', registration)
    resource = dict(max_workers=6, initial_workers=4, solver_threads_per_process=1, BLAS_threads_per_process=1,
        minimum_available_GiB_before_start=10., increase_to_maximum_requires_available_GiB=12.,
        increase_to_maximum_requires_all_initial_workers_progress_minutes=5., boundary_stop_below_GiB=6.,
        minimum_E_free_GiB_at_prepare=30., native_worker_minimum_E_free_GiB=30.,
        worker_output_temp_stopfile_isolated=True, temp_root=str(root/'temp'))
    write(root / 'RESOURCE_POLICY.json', resource)
    unique = {row['path']: row for row in [family_pin]+external}
    write(root / 'EXTERNAL_DEPENDENCIES.json', dict(schema='hospital_ies.single_unit.external_dependencies.v1',
        files=sorted(unique.values(), key=lambda row:row['path']), raw_old_trajectories_copied=0,
        explanation='Canonical base CASE copied only; original normal and original minute inputs remain explicitly pinned dependencies'))
    selected = {row['alias']: dict(producer=row['producer'], result=row['result'],
        result_sha256=row['result']['sha256'], original_label=row['original_label'],
        origin='reused_original_normal_with_exact_accepted_native_closure',
        original_source_binding=row['original_source_binding']) for row in proof['normal_rows']}
    write(root / 'SELECTION.json', dict(schema='hospital_ies.single_unit.selection.v1', producers=selected))
    print(json.dumps(dict(status='prepared_only', root=str(root), new_producers=6, original_normal_reused=2,
        formal_experiments_started=0)), flush=True)
    return 0


def freeze(root):
    root = safe_new_root(root)
    if (root/'SOURCE_BINDING.json').exists():
        raise FileExistsError('Source binding is immutable; retain old freeze and use a fresh root')
    if any((root/'runs').iterdir()):
        raise ValueError('Source freeze must precede every actual trajectory')
    from tools.sci_engineering.runner import source_binding, input_binding
    from tools.cooling_dynamics_20261104 import default_config
    registration = read(root/'REGISTRATION.json')
    if registration['cooling_config'] != default_config():
        raise ValueError('Cooling configuration changed after registration')
    paths = [p for folder in ('inputs', 'cases') for p in (root/folder).rglob('*') if p.is_file()]
    paths += [root/name for name in ('REGISTRATION.json', 'RESOURCE_POLICY.json', 'NORMAL_REUSE_PROOF.json', 'EXTERNAL_DEPENDENCIES.json')]
    paths += [SOURCE/'docs/single_unit_20261104'/name for name in ('IMPLEMENTATION_PLAN.md', 'RUNNER.md')]
    external = read(root/'EXTERNAL_DEPENDENCIES.json')['files']
    for row in external:
        pin(row['path'], row['sha256'])
    all_pins = {row['path']: row for row in external + [pin(p) for p in paths]}
    git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, capture_output=True, text=True, check=True).stdout.strip()
    binding = dict(schema='hospital_ies.single_unit.source_binding.v1', created_utc=utc(), source_root=str(SOURCE),
        git_base_commit=git, exact_source_bytes_are_authoritative=True, source_files=source_binding(SOURCE),
        runtime_inputs=input_binding(SOURCE), new_input_files=sorted(all_pins.values(), key=lambda row:row['path']),
        native_RESULT_exclusive_owner='tools.cooling_dynamics_20261104.run_worker.run',
        adapter='tools.single_unit_20261104.runtime.build_runtime', frozen_before_actual_runs=True,
        original_normal_retains_original_source_identity=True)
    write(root/'SOURCE_BINDING.json', binding)
    print(json.dumps(dict(status='source_frozen_only', root=str(root), source_files=len(binding['source_files']),
        sha256=sha(root/'SOURCE_BINDING.json'), formal_experiments_started=0)), flush=True)
    return 0


def run_campaign(root, max_workers=6, labels=None):
    import psutil
    from tools.single_unit_20261104.run_worker import verify_binding
    root = safe_new_root(root)
    admitted_limit(max_workers, min(max_workers, 4), 0., [])
    verify_binding(root)
    registration = read(root/'REGISTRATION.json')
    known = {p['label'] for p in registration['producers']}
    if labels is not None and set(labels)-known:
        raise ValueError('Unknown new producer label')
    selection_path = root/'SELECTION.json'
    selection = read(selection_path)
    pending = [p['label'] for p in registration['producers'] if (labels is None or p['label'] in labels) and p['label'] not in selection['producers']]
    stage = root/'logs'/('campaign_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    stage.mkdir()
    active = {}
    failures = []
    stopping = False
    began = time.perf_counter()
    last_status = 0.
    current_limit = min(max_workers, 4)

    def stop(reason):
        nonlocal stopping
        stopping = True
        if not (root/'STOP.request').exists():
            write(root/'STOP.request', dict(reason=reason, utc=utc()))

    def save_selection():
        temporary = root/'SELECTION.tmp'
        temporary.write_text(json.dumps(selection, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
        os.replace(temporary, selection_path)

    try:
        while pending or active:
            available = psutil.virtual_memory().available / 1024**3
            free = shutil.disk_usage(root).free / 1024**3
            if (root/'STOP.request').exists() or available < 6. or free < 30.:
                stop('Owned boundary resource/user stop')
            for label, task in list(active.items()):
                rc = task['process'].poll()
                if rc is None:
                    continue
                task['stdout'].close()
                task['stderr'].close()
                producer = Path(task['producer'])
                completion = read(producer/'COMPLETION.json') if (producer/'COMPLETION.json').is_file() else dict(status='failed', error='No completion sidecar')
                result = read(producer/'RESULT.json') if (producer/'RESULT.json').is_file() else dict(status='failed', error='No native RESULT')
                event = dict(label=label, attempt=task['attempt'], producer=str(producer), pid=task['process'].pid,
                    create_time=task['create_time'], exit_code=rc, status=completion.get('status'),
                    error=result.get('error', completion.get('error')), utc=utc())
                append(root/'ATTEMPTS.jsonl', event)
                if rc == 0 and completion.get('status') == 'completed' and result.get('status') == 'completed':
                    result_pin = pin(producer/'RESULT.json')
                    if completion['native_result']['sha256'] != result_pin['sha256']:
                        raise ValueError('Native RESULT disagrees with completion receipt')
                    selection['producers'][label] = dict(producer=str(producer), result=result_pin,
                        result_sha256=result_pin['sha256'], completion=pin(producer/'COMPLETION.json'),
                        attempt=task['attempt'], origin='new_single_unit_continuous_native_run')
                    save_selection()
                else:
                    failures.append(event)
                    stop('Peer producer failed; preserve all owned boundary receipts')
                print(json.dumps(event, ensure_ascii=False), flush=True)
                del active[label]
            progress = [read(Path(task['producer'])/'PROGRESS.json').get('observed_until_minute', 0.)
                if (Path(task['producer'])/'PROGRESS.json').exists() else 0. for task in active.values()]
            current_limit = admitted_limit(max_workers, current_limit, available, progress)
            while pending and not stopping and len(active) < current_limit and psutil.virtual_memory().available >= 10*1024**3:
                label = pending.pop(0)
                attempt = 1
                while (root/'runs'/(label+'_attempt'+str(attempt).zfill(2))).exists():
                    attempt += 1
                producer = root/'runs'/(label+'_attempt'+str(attempt).zfill(2))
                command = [sys.executable, '-B', '-m', 'tools.single_unit_20261104.run_worker', '--root', str(root), '--label', label, '--attempt', str(attempt)]
                stdout = (stage/(label+'.stdout.log')).open('xb')
                stderr = (stage/(label+'.stderr.log')).open('xb')
                environ = dict(os.environ, TEMP=str(root/'temp'), TMP=str(root/'temp'),
                    OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
                process = subprocess.Popen(command, cwd=SOURCE, env=environ, stdout=stdout, stderr=stderr,
                    creationflags=0x08000000 if os.name == 'nt' else 0)
                created = psutil.Process(process.pid).create_time()
                active[label] = dict(process=process, create_time=created, producer=str(producer), attempt=attempt, stdout=stdout, stderr=stderr)
                append(stage/'STARTS.jsonl', dict(label=label, attempt=attempt, pid=process.pid, create_time=created,
                    command=command, cwd=str(SOURCE), utc=utc(), available_GiB=psutil.virtual_memory().available/1024**3))
                time.sleep(.5)
            now = time.perf_counter()
            if now-last_status >= 30.:
                print(json.dumps(dict(status='boundary_stopping' if stopping else 'running', active=list(active),
                    pending=len(pending), available_GiB=available, current_limit=current_limit)), flush=True)
                last_status = now
            if stopping and not active:
                break
            if not active and pending and not stopping and available < 10.:
                append(stage/'ADMISSION_WAITS.jsonl', dict(available_GiB=available, utc=utc()))
            time.sleep(2.)
    except BaseException:
        stop('Owned campaign exception boundary stop')
        deadline = time.time()+60.
        for label, task in active.items():
            forced = False
            try:
                task['process'].wait(timeout=max(.1, deadline-time.time()))
            except subprocess.TimeoutExpired:
                proc = psutil.Process(task['process'].pid)
                if proc.create_time() == task['create_time']:
                    proc.terminate()
                    forced = True
                    task['process'].wait(timeout=30.)
            finally:
                task['stdout'].close()
                task['stderr'].close()
                append(stage/'EXCEPTION_STOPS.jsonl', dict(label=label, pid=task['process'].pid,
                    create_time=task['create_time'], forced_termination=forced, exit_code=task['process'].poll(), utc=utc()))
        raise
    value = dict(status='completed' if not failures and not pending and not stopping else 'incomplete',
        elapsed_seconds=time.perf_counter()-began, selected_producers=len(selection['producers']),
        selected_new_producers=len(set(selection['producers']) & known), failures=failures, pending=pending,
        all_owned_workers_exited=not active, max_workers_requested=max_workers,
        source_binding_sha256=sha(root/'SOURCE_BINDING.json'))
    write(stage/'RESULT.json', value)
    print(json.dumps(value, ensure_ascii=False), flush=True)
    return 0 if value['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare', 'freeze', 'run'))
    parser.add_argument('--root', default=str(DEFAULT_ROOT))
    parser.add_argument('--max-workers', type=int, default=6)
    parser.add_argument('--labels', nargs='+')
    args = parser.parse_args()
    if args.command == 'prepare':
        return prepare(args.root)
    if args.command == 'freeze':
        return freeze(args.root)
    return run_campaign(args.root, args.max_workers, args.labels)


if __name__ == '__main__':
    raise SystemExit(main())
