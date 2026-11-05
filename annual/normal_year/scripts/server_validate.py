"""Explicit Windows-server gates. Never run these science commands on the source host.

Separate suite, full-size concurrent license and real short-recovery stages are
required. Each runs only when its subcommand is explicitly invoked. No annual
producer is ever started here, and no scientific implementation is modified.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from scripts.execution_profile import require_result, require_match, execution_contract, verify_account
from transfer_checks import (CheckError, canonical_sha256, carrier_account_passed, current_host, external_work_root,
    host_probe, identity_binding, now, read_json, require_server_host, sha256, verify_package, write_new)


def validation_plan(package_root, work_root):
    package, work = Path(package_root).resolve(), Path(work_root).resolve()
    return [dict(kind=kind, case_file=package / ('cases/validation_' + kind + '.json'),
        minutes=minutes, kill_after_blocks=blocks, expected_snapshot_minute=point,
        accepted_result_cache=False, output=work / 'validation/short' / kind)
        for kind, minutes, blocks, point in [('normal', 60.0, 3, 30.0), ('fault', 180.0, 7, 90.0)]]


class Runs:
    """Owned process trees with raw logs, identity, bounded wall/RSS/disk/RAM."""
    def __init__(self, source, folder, timeout, rss_limit, min_disk, min_ram):
        if not math.isfinite(timeout) or timeout <= 0:
            raise CheckError('A finite positive validation timeout is required')
        self.source, self.folder = source, folder
        self.deadline = time.monotonic() + timeout
        self.rss_limit, self.min_disk, self.min_ram = rss_limit, min_disk, min_ram
        self.jobs = []
        self.peak_owned_rss_bytes = 0
        self.peak_active_processes = 0
        self.peak_active_producers = 0

    def launch(self, label, command, extra_environment=None):
        import psutil
        folder = self.folder / 'commands' / label
        folder.mkdir(parents=True, exist_ok=False)
        temp = folder / 'temp'
        temp.mkdir()
        env = dict(os.environ, TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
            PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', PYTHONHASHSEED='0',
            PYTHONPATH=os.pathsep.join((str(self.source), str(self.source / 'src'))),
            OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
            NUMEXPR_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1')
        if extra_environment is not None:
            env.update(extra_environment)
        env.pop('SF2_NATIVE_SAMPLE_RUN', None)
        env['IES_HISTORICAL_WORK_ROOT']=str(self.folder)
        handles = [(folder / (name + '.log')).open('x', encoding='utf8') for name in ('stdout', 'stderr')]
        start = time.monotonic()
        try:
            owned = create_owned_tree([str(x) for x in command], cwd=str(self.source), env=env, stdout=handles[0], stderr=handles[1])
        except BaseException:
            for handle in handles:
                handle.close()
            raise
        try:
            process = psutil.Process(owned.proc.pid)
            born = process.create_time()
            if os.name == 'nt':
                process.nice(psutil.NORMAL_PRIORITY_CLASS)
            parent_observed = psutil.Process().cpu_affinity()
            actual = process.cpu_affinity()
        except BaseException:
            try:
                owned.terminate_tree()
            finally:
                owned.close()
                for handle in handles:
                    handle.close()
            raise
        job = dict(label=label, folder=folder, owned=owned, handles=handles, start=start,
            metadata=dict(command=[str(x) for x in command], cwd=str(self.source), pid=owned.proc.pid,
                create_time=born, started_utc=now(), requested_cpus=None, parent_observed_cpus=parent_observed,
                actual_cpus=actual, affinity_policy='inherit_observed_no_setter',
                normal_priority=True, environment={key: env[key] for key in ('TEMP', 'TMP', 'TMPDIR',
                    'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'PYTHONPATH', 'PYTEST_PLUGINS') if key in env}))
        self.jobs.append(job)
        write_new(folder / 'LAUNCH.json', job['metadata'])
        return job

    def guard(self):
        import psutil
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Registered validation wall-clock budget exhausted')
        memory = psutil.virtual_memory()
        if memory.available < self.min_ram:
            raise CheckError('Validation available RAM guard failed')
        import shutil
        if shutil.disk_usage(self.folder).free < self.min_disk:
            raise CheckError('Validation free disk guard failed')
        rss, active, producers = 0, 0, 0
        for job in self.jobs:
            if job.get('closure') is not None:
                continue
            living = job['owned'].refresh()
            active += len(living)
            if root_poll(job['owned']) is None and job['label'] in {kind + '_' + stage for kind in ('normal', 'fault')
                for stage in ('full', 'interrupted', 'recovered')}:
                producers += 1
            for identity in living:
                try:
                    process = psutil.Process(identity['pid'])
                    if process.create_time() != identity['create_time']:
                        raise CheckError('Validation process identity changed')
                    rss += process.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
        if rss > self.rss_limit:
            raise CheckError('Validation total owned RSS guard failed')
        if active > 3:
            raise CheckError('Validation exceeded its three-process total allowance')
        self.peak_owned_rss_bytes = max(self.peak_owned_rss_bytes, rss)
        self.peak_active_processes = max(self.peak_active_processes, active)
        self.peak_active_producers = max(self.peak_active_producers, producers)

    def finish(self, job, allow_nonzero=False):
        code = root_poll(job['owned'])
        if code is None:
            raise RuntimeError('Cannot finalize a running validation process')
        closure = self.close_job(job, forced=allow_nonzero)
        for handle in job['handles']:
            handle.close()
        receipt = dict(job['metadata'], finished_utc=now(), elapsed_seconds=time.monotonic() - job['start'], exit_code=code,
            intentional_termination=bool(allow_nonzero),
            closure=dict(path='CLOSURE.json', sha256=sha256(job['folder'] / 'CLOSURE.json')),
            stdout_sha256=sha256(job['folder'] / 'stdout.log'), stderr_sha256=sha256(job['folder'] / 'stderr.log'))
        write_new(job['folder'] / 'EXIT.json', receipt)
        if closure['residual_tree_terminated']:
            raise CheckError('Validation root exited with a live owned descendant; gate failed')
        if closure['gate_acceptable'] is not True:
            raise CheckError('Validation owned process closure is unknown or incomplete')
        if code and not allow_nonzero:
            raise CheckError('Validation command failed: ' + job['label'] + ' exit=' + str(code))
        return receipt

    def close_job(self, job, forced=False):
        if job.get('closure') is not None:
            return job['closure']
        owner = job['owned']
        before, remaining, errors = [], None, []
        try:
            before = owner.refresh()
        except BaseException as error:
            errors.append('membership before close: ' + repr(error))
        residual = bool(before and not forced and root_poll(owner) is not None)
        try:
            if before or forced or errors:
                owner.terminate_tree()
        except BaseException as error:
            errors.append('tree termination: ' + repr(error))
        try:
            owner.close()
        except BaseException as error:
            errors.append('owner close: ' + repr(error))
            # Unknown membership must still close the kill-on-close Job handle.
            native = getattr(owner, 'native', None)
            if native is not None:
                try:
                    native.close()
                except BaseException as native_error:
                    errors.append('native Job close: ' + repr(native_error))
        deadline = time.monotonic() + 5.0
        while True:
            try:
                remaining = owner.refresh()
            except BaseException as error:
                errors.append('membership after close: ' + repr(error))
                break
            if not remaining or time.monotonic() >= deadline:
                break
            time.sleep(.03)
        receipt = dict(created_utc=now(), label=job['label'], forced=bool(forced or residual),
            residual_tree_terminated=residual, observed_before=before,
            observed_identities=list(getattr(owner, 'known', {}).values()), remaining=remaining,
            errors=errors, tree_closed=bool(remaining == [] and not errors),
            gate_acceptable=bool(remaining == [] and not errors and not residual),
            measurement='Job/session membership plus PID and create_time; bounded wait after Job close')
        write_new(job['folder'] / 'CLOSURE.json', receipt)
        job['closure'] = receipt
        return receipt

    def wait(self, jobs):
        pending = list(jobs)
        receipts = []
        while pending:
            self.guard()
            for job in list(pending):
                if root_poll(job['owned']) is not None:
                    receipts.append(self.finish(job))
                    pending.remove(job)
            if pending:
                time.sleep(.03)
        return receipts

    def command(self, label, command, extra_environment=None):
        return self.wait([self.launch(label, command, extra_environment=extra_environment)])[0]

    def close(self):
        failures = []
        for job in self.jobs:
            try:
                receipt = self.close_job(job, forced=root_poll(job['owned']) is None)
                if receipt['tree_closed'] is not True:
                    failures.append('closure unknown or incomplete: ' + job['label'])
            except BaseException as error:
                failures.append(repr(error))
            for handle in job['handles']:
                handle.close()
        if failures:
            write_new(self.folder / 'CLEANUP_FAILURE.json', dict(failures=failures))
            raise RuntimeError('Owned validation process cleanup failed: ' + repr(failures))


def root_poll(owner):
    process = getattr(owner, 'proc', None)
    return process.poll() if process is not None else owner.poll()


def create_owned_tree(command, **kwargs):
    # server_runner imports only stdlib/psutil and guards its CLI under __main__.
    # Reuse its reviewed Job PID enumeration, never run its campaign here.
    from server_runner import OwnedTree
    owner = OwnedTree.__new__(OwnedTree)
    try:
        owner.__init__(command, **kwargs)
        return owner
    except BaseException:
        native = getattr(owner, 'native', None)
        if native is not None:
            try:
                native.terminate_tree()
            finally:
                native.close()
        raise


def run_license_worker(args):
    """Server-only >restricted-size LP; all live models hold until release."""
    folder = args.worker_folder.resolve()
    require_server_host(current_host(folder))
    import gurobipy as gp
    with gp.Env(empty=True) as env:
        env.setParam('LogFile', str(folder / 'gurobi.log'))
        env.setParam('Threads', 1)
        env.start()
        with gp.Model('full_size_license_probe', env=env) as model:
            variables = model.addVars(2101, lb=0, ub=1, name='x')
            model.addConstrs((variables[i] <= .5 for i in range(2101)), name='size_gate')
            model.setObjective(gp.quicksum(variables.values()), gp.GRB.MAXIMIZE)
            model.update()
            model.optimize()
            if model.Status != gp.GRB.OPTIMAL or model.NumVars != 2101 or model.NumConstrs != 2101:
                raise CheckError('Full-size solver model did not solve optimally')
            write_new(folder / 'READY.json', dict(status='passed', variables=model.NumVars,
                constraints=model.NumConstrs, objective=model.ObjVal, solver_version=list(gp.gurobi.version()),
                actual_threads=model.Params.Threads, created_utc=now()))
            deadline = time.monotonic() + args.timeout_seconds
            while not (folder.parent / 'RELEASE').exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError('Concurrent full-size license hold timed out')
                time.sleep(.05)


def producer_command(source, case, output, approval, stop, vault, checkpoint=None):
    command = [sys.executable, '-B', '-m', 'tools.annual_program.runner', '--case-file', str(case),
        '--output', str(output), '--approval-receipt', str(approval), '--stop-file', str(stop),
        '--stop-after-blocks', '1000', '--checkpoint-every-blocks', '35041',
        '--recording-mode', 'engineering_v1', '--recovery-every-blocks', '2', '--recovery-vault', str(vault)]
    if checkpoint is not None:
        command += ['--resume-checkpoint', str(checkpoint)]
    return command


def case_paths(folder, kind, stage):
    if kind not in ('normal', 'fault') or stage not in ('full', 'interrupted', 'recovered'):
        raise CheckError('Unregistered short-validation stage')
    return dict(output=folder / (kind + '_' + stage),
        stop=folder / (kind + ('_full.stop' if stage == 'full' else '_split.stop')),
        vault=folder / (kind + ('_full_vault' if stage == 'full' else '_vault')))


def _completed(output, minutes):
    result = read_json(output / 'RESULT.json')
    require_result(result)
    if not (result.get('status') == 'completed' and result.get('observed_until_minute') == minutes
        and result.get('reset_count') == 1 and result.get('source_and_inputs_unchanged') is True
        and result.get('source_sha256') == verify_package(PACKAGE)['source_tree_sha256']):
        raise CheckError('Real short scientific producer did not complete its exact interval')
    return result


def _progress(path):
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 16384))
        lines = stream.read().splitlines()
    for line in reversed(lines):
        try:
            return json.loads(line)
        except ValueError:
            pass
    return None


def run_short(args, source, work, folder, runs):
    plans = validation_plan(PACKAGE, work)
    approval = PACKAGE / 'evidence/DATA_APPROVAL.json'
    full = []
    for plan in plans:
        paths = case_paths(folder, plan['kind'], 'full')
        full.append(runs.launch(plan['kind'] + '_full', producer_command(source, plan['case_file'], paths['output'],
            approval, paths['stop'], paths['vault'])))
    runs.wait(full)
    for plan in plans:
        _completed(folder / (plan['kind'] + '_full'), plan['minutes'])
    interrupted = []
    for plan in plans:
        paths = case_paths(folder, plan['kind'], 'interrupted')
        output = paths['output']
        job = runs.launch(plan['kind'] + '_interrupted', producer_command(source, plan['case_file'], output,
            approval, paths['stop'], paths['vault']))
        interrupted.append((plan, job, output))
    pending = list(interrupted)
    while pending:
        runs.guard()
        for plan, job, output in list(pending):
            if job['owned'].poll() is not None:
                runs.finish(job)
                raise CheckError('Interrupted test exited before its registered unclean termination')
            progress = _progress(output / 'PROGRESS.jsonl')
            if progress and progress.get('block_count', 0) >= plan['kill_after_blocks']:
                write_new(job['folder'] / 'FORCED_TERMINATION.json', dict(created_utc=now(),
                    pid=job['metadata']['pid'], create_time=job['metadata']['create_time'],
                    observed_progress=progress, deliberate_scientific_test=True))
                job['owned'].terminate_tree()
                runs.finish(job, allow_nonzero=True)
                pending.remove((plan, job, output))
        if pending:
            time.sleep(.01)
    recovered = []
    points = {}
    for plan in plans:
        kind = plan['kind']
        paths = case_paths(folder, kind, 'recovered')
        output = paths['output']
        job = runs.launch(kind + '_restore', [sys.executable, '-B', '-m', 'tools.annual_program.recovery',
            'restore', '--vault', str(folder / (kind + '_vault')), '--snapshot', 'latest', '--output', str(output)])
        runs.wait([job])
        lines = (job['folder'] / 'stdout.log').read_text('utf8').strip().splitlines()
        restored = json.loads(lines[-1])
        marker = Path(restored['resume_checkpoint']).resolve()
        if (not marker.is_relative_to(output.resolve()) or not marker.is_file()
            or restored.get('observed_until_minute') != plan['expected_snapshot_minute']):
            raise CheckError('Recovery did not preserve the registered pending-state snapshot')
        points[kind] = restored['observed_until_minute']
        recovered.append(runs.launch(kind + '_recovered', producer_command(source, plan['case_file'], output,
            approval, paths['stop'], paths['vault'], marker)))
    runs.wait(recovered)
    artifacts = []
    for plan in plans:
        kind = plan['kind']
        _completed(folder / (kind + '_recovered'), plan['minutes'])
        match = folder / ('MATCH_' + kind + '.json')
        runs.command('compare_' + kind, [sys.executable, '-B', str(PACKAGE / 'scripts/compare_engineering_recovery.py'),
            '--source', str(source), '--left', str(folder / (kind + '_full')), '--right', str(folder / (kind + '_recovered')),
            '--output', str(match)])
        comparison = read_json(match)
        require_match(comparison)
        if (comparison.get('status') != 'exact_match' or comparison.get('maximum_absolute_numeric_difference') != 0.0
            or comparison.get('difference_count') != 0 or comparison.get('physical_elapsed_fields_retained') is not True):
            raise CheckError('Strict unclean recovery scientific comparison failed')
        artifacts.append(dict(role='match_' + kind, path=match.relative_to(work).as_posix(), sha256=sha256(match)))
        for stage in ('full', 'recovered'):
            account = folder / ('account_' + kind + '_' + stage)
            runs.command('account_' + kind + '_' + stage, [sys.executable, '-B',
                str(PACKAGE / 'scripts/audit_engineering_year.py'), '--source', str(source),
                '--producer', str(folder / (kind + '_' + stage)), '--output', str(account)])
            result = read_json(account / 'RESULT.json')
            verify_account(PACKAGE, account, folder / (kind + '_' + stage), result)
            if not carrier_account_passed(result):
                raise CheckError('Independent E/steam/cold account failed or remains unknown')
            artifacts.append(dict(role='account_' + kind + '_' + stage,
                path=(account / 'RESULT.json').relative_to(work).as_posix(), sha256=sha256(account / 'RESULT.json')))
    return dict(execution_profile=execution_contract(), actual_interrupted_cases=['normal', 'fault'], recovered_snapshot_minutes=points,
        independent_carrier_accounts_passed=True, exact_recovery_comparisons_passed=True,
        accepted_result_cache=False, reset_count_one=True, physical_elapsed_fields_retained=True,
        normal_minutes=60.0, fault_minutes=180.0, producer_concurrency=runs.peak_active_producers, artifacts=artifacts,
        max_total_processes=runs.peak_active_processes, threads_per_producer=1,
        process_tree_stop_verified=all(j.get('closure', {}).get('gate_acceptable') is True for j in runs.jobs),
        normal_priority=True,
        forced_termination_count=2, process_owner_backend='Windows Job Object kill-on-close',
        clinical_environment_validated=False, OEM_startup_qualified=False)


def combine(work, binding):
    stages = {}
    for name in ('suite', 'license', 'short'):
        path = work / 'validation' / name / 'RECEIPT.json'
        if not path.is_file():
            return False
        value = read_json(path)
        if value.get('status') != 'passed' or value.get('binding') != binding:
            raise CheckError('Server stage is failed or belongs to another source/host/package')
        stages[name] = dict(path=path.relative_to(work).as_posix(), sha256=sha256(path))
    write_new(work / 'validation/VALIDATION.json', dict(kind='normal_year_server_validation', status='passed',
        created_utc=now(), binding=binding, stages=stages, annual_autostart=False, year_completed=False))
    return True


def suite_environment(package_root, source_root):
    package, source = Path(package_root).resolve(), Path(source_root).resolve()
    return dict(PYTEST_PLUGINS='', PYTEST_ADDOPTS='',
        PYTHONPATH=os.pathsep.join((str(source), str(source / 'src'), str(package / 'scripts'))))


def legacy_migration_registration(package_root):
    package = Path(package_root).resolve()
    plugin = package / 'scripts/portable_historical_legacy_fixtures.py'
    return dict(kind='source_conftest_exact_historical_context_routing',parent_plugins='',
        child_plugin='portable_historical_legacy_fixtures',
        plugin_path='scripts/portable_historical_legacy_fixtures.py', plugin_sha256=sha256(plugin),
        source_conftest_sha256=sha256(package/'source/conftest.py'),
        routes_manifest_sha256=sha256(package/'source/tests/minute1_historical_routes.json'),
        fixture_root='legacy_fixtures', source_root='legacy_fixtures/source_epoch09',
        original_full_collection=True, original_assertions_preserved=True,
        scientific_functions_modified=False, skipped_tests=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('suite', 'license', 'short', 'license-worker'))
    parser.add_argument('--work-root', type=Path)
    parser.add_argument('--source', type=Path, default=PACKAGE / 'source')
    parser.add_argument('--timeout-seconds', type=float, required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--worker-folder', type=Path)
    parser.add_argument('--rss-limit-bytes', type=int, default=10_000_000_000)
    parser.add_argument('--min-free-disk-bytes', type=int, default=30_000_000_000)
    parser.add_argument('--min-available-ram-bytes', type=int, default=16_000_000_000)
    args = parser.parse_args(argv)
    if args.command == 'license-worker':
        if args.worker_folder is None:
            parser.error('--worker-folder is required')
        run_license_worker(args)
        return 0
    if args.work_root is None:
        parser.error('--work-root is required')
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error('--timeout-seconds must be finite and positive')
    if args.workers<1:
        parser.error('--workers must be a positive integer')
    if min(args.rss_limit_bytes, args.min_free_disk_bytes, args.min_available_ram_bytes) <= 0:
        parser.error('Resource limits must be positive')
    source = args.source.resolve()
    if source != (PACKAGE / 'source').resolve():
        raise CheckError('Only this package frozen source is accepted')
    probe = host_probe(PACKAGE, args.work_root)
    require_server_host(probe['host'])
    work = external_work_root(PACKAGE, args.work_root)
    binding = identity_binding(PACKAGE, work, probe['host'])
    folder = work / 'validation' / args.command
    if args.command == 'short':
        for prior in ('suite', 'license'):
            receipt = read_json(work / 'validation' / prior / 'RECEIPT.json')
            if receipt.get('status') != 'passed' or receipt.get('binding') != binding:
                raise CheckError('Current server suite/license must pass before short science')
    folder.mkdir(parents=True, exist_ok=False)
    migration = legacy_migration_registration(PACKAGE) if args.command == 'suite' else None
    write_new(folder / 'PLAN.json', dict(created_utc=now(), command=args.command, binding=binding,
        timeout_seconds=args.timeout_seconds, rss_limit_bytes=args.rss_limit_bytes,
        min_free_disk_bytes=args.min_free_disk_bytes, min_available_ram_bytes=args.min_available_ram_bytes,
        normal_priority=True, future_information=False, annual_autostart=False,
        dependency_path_migration=migration))
    started = time.monotonic()
    runs = Runs(source, folder, args.timeout_seconds, args.rss_limit_bytes,
        args.min_free_disk_bytes, args.min_available_ram_bytes)
    try:
        if args.command == 'suite':
            result = runs.command('default_suite', [sys.executable, '-B', '-m', 'pytest', '-q'],
                extra_environment=suite_environment(PACKAGE, source))
            extra = dict(exit_code=result['exit_code'], dependency_path_migration=migration)
        elif args.command == 'license':
            jobs = []
            for index in range(args.workers):
                worker = folder / ('worker_' + str(index))
                worker.mkdir()
                jobs.append((worker, runs.launch('license_' + str(index), [sys.executable, '-B', str(Path(__file__).resolve()),
                    'license-worker', '--worker-folder', str(worker), '--timeout-seconds', str(args.timeout_seconds)])))
            while not all((worker / 'READY.json').is_file() for worker, _ in jobs):
                runs.guard()
                if any(job['owned'].poll() is not None for _, job in jobs):
                    raise CheckError('Full-size concurrent license probe failed before all models were live')
                time.sleep(.03)
            write_new(folder / 'CONCURRENT_MODELS.json', dict(created_utc=now(),
                workers=args.workers, ready=[read_json(worker / 'READY.json') for worker, _ in jobs]))
            (folder / 'RELEASE').write_text(now(), 'ascii')
            receipts = runs.wait([job for _, job in jobs])
            extra = dict(full_size_model_verified=True, workers=args.workers,
                all_exit_codes=[r['exit_code'] for r in receipts], concurrent=True,
                artifacts=[dict(role='license_concurrency', path=(folder / 'CONCURRENT_MODELS.json').relative_to(work).as_posix(),
                    sha256=sha256(folder / 'CONCURRENT_MODELS.json'))])
        else:
            extra = run_short(args, source, work, folder, runs)
        runs.close()
        verify_package(PACKAGE)
        if identity_binding(PACKAGE, work) != binding:
            raise CheckError('Source/host/package identity changed during server validation')
        commands = [dict(path=(j['folder'] / 'EXIT.json').relative_to(work).as_posix(), sha256=sha256(j['folder'] / 'EXIT.json'))
            for j in runs.jobs]
        write_new(folder / 'RECEIPT.json', dict(kind=args.command, status='passed', created_utc=now(), commands=commands,
            peak_owned_rss_bytes=runs.peak_owned_rss_bytes, actual_peak_owned_processes=runs.peak_active_processes,
            elapsed_seconds=time.monotonic() - started, binding=binding, **extra))
        combined = combine(work, binding)
        print(json.dumps(dict(status='passed', stage=args.command, combined_validation_written=combined,
            annual_autostart=False, admission_granted=False)))
    except BaseException as error:
        write_new(folder / 'FAILURE.json', dict(status='failed', type=type(error).__name__, message=str(error),
            created_utc=now(), elapsed_seconds=time.monotonic() - started, binding=binding))
        raise
    finally:
        runs.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
