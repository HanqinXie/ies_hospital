"""Run explicitly registered final panels with two workers, or normals serially.

No registration, source edits, policy selection, retry or outcome imputation.
Stop launching new batches after a failed run, audit or matching check.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from threading import Event
import argparse
import json
import os
import subprocess
import sys
import uuid

B = Path(__file__).resolve().parent.parent
ROOT = B.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.model_closeout import runner as r


def terminal_check(directory):
    plan = r.read(directory/'plan.json')
    manifest = r.read(directory/'manifest.json')
    terminal = r.read(directory/'BATCH_RESULT.json')
    expected = {(scene['id'], spec['policy_id'])
        for scene in manifest['scenarios'] for spec in plan['policy_specs']}
    count = len(expected)
    index = terminal.get('pair_index', [])
    if (not count or plan.get('registered_pairs') != count or
        terminal.get('registered_pairs') != count or terminal.get('actual_attempts') != count or
        terminal.get('plan_sha256') != r.sha(directory/'plan.json') or
        terminal.get('manifest_sha256') != r.sha(directory/'manifest.json') or
        plan.get('manifest_sha256') != r.sha(directory/'manifest.json') or
        not isinstance(index, list) or len(index) != count or
        {(item.get('scenario_id'),item.get('policy_id')) for item in index} != expected):
        raise ValueError('Terminal does not cover the complete bound registration')
    if (terminal.get('unknown_full') != 0 or
        terminal.get('analysis_failure_or_unavailable_count') != 0 or
        terminal.get('complete_full') != terminal.get('registered_pairs') or
        terminal.get('source_unchanged') is not True or
        terminal.get('same_initial_state_policy_matching') is not True or
        terminal.get('same_exogenous_stream_policy_matching') is not True):
        raise ValueError('Incomplete, unbound, or unmatched physical terminal')
    scenes = {s['id']: s for s in manifest['scenarios']}
    for item in index:
        record = r.read(directory/'pairs'/(item['scenario_id']+'__'+item['policy_id'])/'RESULT.json')
        audit = (record.get('analysis') or {}).get('v19', {}).get('episode_audit', {})
        analysis = record.get('analysis') or {}
        scene = scenes[item['scenario_id']]
        end = float(scene['duration_steps'])*float(scene.get('step_minutes',15.))
        window = analysis.get('windows', {}).get('registered_full', {})
        if ({k:v for k,v in record.items() if k != 'analysis'} != item or
            record.get('model_id') != r.MODEL_ID or
            record.get('manifest_signature') != manifest.get('signature') or
            record.get('model_config_signature') != manifest.get('model_config_signature') or
            analysis.get('execution_model_id') != r.MODEL_ID or
            window.get('complete') is not True or window.get('window_minutes') != [0.,end] or
            float(analysis.get('observed_until_minute',-1.)) < end-1e-7 or
            record.get('status') != 'completed' or record.get('error') or record.get('analysis_error') or
            audit.get('status') != 'passed' or audit.get('issues') != [] or
            audit.get('complete_registered_interval') is not True):
            raise ValueError('Physical, analysis, or original-domain audit failure')
    return dict(registered_pairs=terminal['registered_pairs'],
        complete_full=terminal['complete_full'], source_unchanged=True,
        batch_result_sha256=r.sha(directory/'BATCH_RESULT.json'))


def _main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--panel', action='append', required=True)
    parser.add_argument('--workers', type=int, choices=(1,2), default=2)
    parser.add_argument('--normal', action='store_true')
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if not args.label.replace('_','').isalnum():
        raise ValueError('Fresh simple label required')
    if args.normal and args.workers != 1:
        raise ValueError('Normal windows are globally serial')
    final = B/'MODEL_FREEZE_CLOSEOUT01.json'
    frozen = r.read(final)
    r.validate_final_freeze(frozen, r.source_binding(), r.MODEL_ID,
        frozen['model_config'], list(r.policy_specs().values()))
    rows = []
    for panel in args.panel:
        if not panel.replace('_','').isalnum():
            raise ValueError('Canonical panel name required')
        for row in r.read(B/(panel.upper()+'_REGISTRATION.json')):
            directory = r._experiment_path(Path(row['directory']))
            plan, manifest = r.read(directory/'plan.json'), r.read(directory/'manifest.json')
            r._validate_registered_freeze(plan, manifest)
            if (manifest.get('source_freeze_artifact', {}).get('sha256') != r.sha(final) or
                manifest.get('freeze_signature') != frozen['signature']):
                raise ValueError('Batch binds a different final freeze artifact')
            if plan['source_sha256'] != frozen['source_sha256']:
                raise ValueError('Final source mismatch')
            normal = any(float(s['duration_steps'])*float(s.get('step_minutes',15.))>=1440.
                for s in manifest['scenarios'])
            if normal != args.normal:
                raise ValueError('Do not mix normal and shorter panels')
            if any((directory/n).exists() for n in ('BATCH_RESULT.json','ATTEMPTS.jsonl','pairs')):
                raise FileExistsError('Existing attempt cannot be relaunched: '+directory.name)
            rows.append(dict(row, directory=str(directory)))
    if len({x['directory'] for x in rows}) != len(rows):
        raise ValueError('Duplicate batch')
    if args.plan_only:
        print(json.dumps(dict(batches=len(rows), registered_pairs=sum(x['registered_pairs'] for x in rows),
            workers=args.workers, normal_serial=args.normal)))
        return 0
    destination = B/('FROZEN_QUEUE_'+args.label.upper()+'_RESULT.json')
    if destination.exists():
        raise FileExistsError(destination)
    import psutil
    # These queues start only after development has stopped; check direct
    # module children, not the logging wrappers that contain their argv.
    # Windows can raise OSError from process_iter(attrs=...) itself while
    # fetching one process's command line. Isolate each read and inspect only
    # Python command lines; the exclusive lease remains scheduler-scoped.
    process_probe_errors = []
    for pid in psutil.pids():
        try:
            process = psutil.Process(pid)
            if not process.name().lower().startswith('python'):
                continue
            argv = process.cmdline() or []
        except (psutil.Error, OSError) as error:
            process_probe_errors.append(dict(pid=pid, type=type(error).__name__, message=str(error)))
            continue
        for i in (1,2,3):
            if i+2 < len(argv) and argv[i:i+3] == ['-m','tools.model_closeout.runner','run']:
                if all(v in ('-B','-u') for v in argv[1:i]):
                    raise RuntimeError('Another physical worker is active')
    record = dict(label=args.label, utc_started=datetime.now(timezone.utc).isoformat(),
        freeze_sha256=r.sha(final), workers=args.workers, normal_serial=args.normal,
        panels=args.panel, registered_batches=len(rows),
        registered_pairs=sum(x['registered_pairs'] for x in rows),
        available_memory_at_start_bytes=psutil.virtual_memory().available,
        process_probe_errors=process_probe_errors,
        results=[], unstarted_batches=[])
    halt_event = Event()

    def execute_one(row):
        directory = Path(row['directory'])
        if halt_event.is_set() or (B/'STOP_FROZEN_QUEUE.json').exists():
            return dict(directory=str(directory), accepted=False, not_started_cancelled=True,
                reason='Queue stop observed before physical launch')
        command = [sys.executable,'-B',str(ROOT/'tools/p0_audit/run_logged.py'),
            '--run-dir',str(B),'--name','run_'+directory.name,'--',
            sys.executable,'-B','-m','tools.model_closeout.runner','run',
            '--directory',str(directory)]
        done = subprocess.run(command, cwd=ROOT, capture_output=True, check=False)
        result = dict(directory=str(directory), command=command, exit_code=done.returncode,
            launcher_stdout=done.stdout.decode('utf-8', errors='replace'),
            launcher_stderr=done.stderr.decode('utf-8', errors='replace'))
        if done.returncode == 0:
            try:
                result['verified_terminal'] = terminal_check(directory)
            except Exception as error:
                result['verification_error'] = dict(type=type(error).__name__, message=str(error))
        result['accepted'] = done.returncode == 0 and 'verified_terminal' in result
        if not result['accepted']:
            halt_event.set()
        return result

    def one(row):
        try:
            return execute_one(row)
        except Exception:
            halt_event.set()
            raise

    remaining = iter(rows)
    halted = False
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        active = {}
        for _ in range(args.workers):
            row = next(remaining, None)
            if row:
                active[pool.submit(one,row)] = row
        while active:
            ready, _ = wait(active, return_when=FIRST_COMPLETED)
            # A second completion may occur after FIRST_COMPLETED's snapshot.
            # Inspect it before admitting more work, including a failed child.
            ready.update(f for f in active if f.done())
            for future in ready:
                row = active.pop(future)
                try:
                    result = future.result()
                except Exception as error:
                    result = dict(directory=row['directory'], accepted=False,
                        error=dict(type=type(error).__name__,message=str(error)))
                record['results'].append(result)
                halted |= not result['accepted'] or halt_event.is_set()
                halted |= (B/'STOP_FROZEN_QUEUE.json').exists()
                if halted:
                    halt_event.set()
                print(json.dumps(dict(finished=len(record['results']), total=len(rows),
                    batch=Path(row['directory']).name, accepted=result['accepted'],
                    halt_new_launches=halted)), flush=True)
            if not halted:
                while len(active)<args.workers and not halt_event.is_set():
                    row=next(remaining,None)
                    if row is None:
                        break
                    active[pool.submit(one,row)] = row
        record['unstarted_batches'] = [x['directory'] for x in remaining]
    record['all_succeeded'] = len(record['results']) == len(rows) and all(x['accepted'] for x in record['results'])
    record['utc_finished'] = datetime.now(timezone.utc).isoformat()
    record['attempt_accounting'] = r.attempt_accounting()
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return 0 if record['all_succeeded'] else 1


@contextmanager
def queue_lease():
    """Atomic lease for all invocations of this external final scheduler.

    A crashed owner leaves a fail-closed lease for explicit diagnosis. The
    original lease event is retained even after a normal release.
    """
    path = B/'PHYSICAL_QUEUE_LEASE.json'
    value = dict(token=uuid.uuid4().hex, pid=os.getpid(),
        acquired_utc=datetime.now(timezone.utc).isoformat())
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream)
    events = B/'PHYSICAL_QUEUE_LEASE_EVENTS.jsonl'
    with events.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(event='acquired',**value))+'\n')
    try:
        yield
    finally:
        owner = r.read(path)
        if owner != value:
            raise RuntimeError('Queue lease owner changed; preserve lease for diagnosis')
        with events.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event='released',**value,
                released_utc=datetime.now(timezone.utc).isoformat()))+'\n')
        path.unlink()


def main():
    if (B/'STOP_FROZEN_QUEUE.json').exists():
        raise RuntimeError('Explicit stop exists before any final launch')
    with queue_lease():
        return _main()


if __name__ == '__main__':
    raise SystemExit(main())
