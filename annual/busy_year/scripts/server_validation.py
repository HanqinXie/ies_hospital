"""Explicit target-server short qualification; never starts an annual case."""
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
from scripts.execution_profile import require_result, require_match, execution_contract, MODEL_ID, MATCH_SCHEMA, verify_account as verify_engineering_account
from runtime_checks import (CheckError, canonical_sha256, carrier_account_passed, current_host,
    external_work_root, now, read_json, require_server_host, sha256, verify_package, write_new,
    DEFAULT_LIMITS)


def deployment_check_scope():
    """Record the explicitly skipped probes without claiming qualification.

    Python/package identities are still recorded for deterministic recovery.
    Physical execution, accounting, recovery and ownership checks remain gates.
    """
    return dict(license='skipped_by_user_request',
        environment_dependencies='skipped_by_user_request')


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
            producer_labels = {kind + '_' + stage for kind in ('normal', 'fault')
                for stage in ('full', 'interrupted', 'recovered')}
            producer_labels.add('c_ready_full')
            producer_labels.update('mechanism_' + name for name in ('ec_healthy', 'ec_fault', 'boiler_healthy', 'boiler_fault'))
            if root_poll(job['owned']) is None and job['label'] in producer_labels:
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
        if producers > 1:
            raise CheckError('Scientific qualification permits only one producer at a time')
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


def _require_target():
    require_server_host(current_host())
    if os.name != 'nt':
        raise CheckError('Scientific server qualification requires Windows')


def license_worker(output):
    """A real above-restricted-size solve, in a separately owned process."""
    _require_target()
    import gurobipy as gp
    with gp.Env(empty=True) as environment:
        environment.setParam('LogFile', str(Path(output).parent / 'license_solver.log'))
        environment.setParam('Threads', 1)
        environment.start()
        with gp.Model('busy_year_full_size_license', env=environment) as model:
            variables = model.addVars(2101, lb=0., ub=1.)
            model.addConstrs((variables[i] <= .5 for i in range(2101)))
            model.setObjective(gp.quicksum(variables.values()), gp.GRB.MAXIMIZE)
            model.optimize()
            if model.Status != gp.GRB.OPTIMAL:
                raise CheckError('The full-size license probe did not solve optimally')
            write_new(output, dict(status='passed', variables=model.NumVars, constraints=model.NumConstrs,
                objective=model.ObjVal, actual_threads=model.Params.Threads,
                solver_version=list(gp.gurobi.version()), created_utc=now()))


def validation_case():
    """A bounded actual window crossing demand onset, two faults and demand end."""
    sys.path[:0] = [str(PACKAGE / 'source'), str(PACKAGE / 'source' / 'src')]
    from tools.annual_program.registration import make_case
    events = [dict(time_minute=60.25, availability={'TOWER': .01}),
        dict(time_minute=90.25, availability={'TOWER': 1.}),
        dict(time_minute=95.25, availability={'TOWER': .01}),
        dict(time_minute=140.25, availability={'TOWER': 1.})]
    return make_case('M_ready', mode='FD', duration_minutes=180., calendar=events,
        demand_start_minute=45.25, demand_duration_minutes=60.5, execution_revision=MODEL_ID,numerical_profile='engineering_fast_v1')


def case_worker(output):
    _require_target()
    write_new(output, validation_case())


def c_ready_validation_case():
    """Reproduce the seen summer C_ready service-acceptance background."""
    sys.path[:0] = [str(PACKAGE / 'source'), str(PACKAGE / 'source' / 'src')]
    from tools.annual_program.registration import make_case
    events = [dict(time_minute=60.25, availability={'TOWER': .01}),
        dict(time_minute=90.25, availability={'TOWER': 1.}),
        dict(time_minute=95.25, availability={'TOWER': .01}),
        dict(time_minute=140.25, availability={'TOWER': 1.})]
    return make_case('C_ready', mode='FD', configuration='H2', start_hour=4137,
        duration_minutes=180., calendar=events, demand_amplitude=.25,
        demand_start_minute=30.25, demand_duration_minutes=115., execution_revision=MODEL_ID,numerical_profile='engineering_fast_v1')


def c_ready_case_worker(output):
    _require_target()
    write_new(output, c_ready_validation_case())


def producer_command(case, output, stop, vault, checkpoint=None):
    command = [sys.executable, '-B', '-m', 'tools.annual_program.runner', '--case-file', str(case),
        '--output', str(output), '--approval-receipt', str(PACKAGE / 'config' / 'DATA_APPROVAL.json'),
        '--stop-file', str(stop), '--stop-after-blocks', '1000', '--checkpoint-every-blocks', '35041',
        '--recording-mode', 'engineering_v1', '--recovery-every-blocks', '2', '--recovery-vault', str(vault)]
    if checkpoint is not None:
        command += ['--resume-checkpoint', str(checkpoint)]
    return command


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


def _completed(output, source_sha, case_sha):
    result = read_json(Path(output) / 'RESULT.json')
    require_result(result)
    if (result.get('status') != 'completed' or result.get('observed_until_minute') != 180.
            or result.get('unknown_tail_minutes') != 0. or type(result.get('reset_count')) is not int
            or result['reset_count'] != 1
            or result.get('source_and_inputs_unchanged') is not True
            or result.get('source_sha256') != source_sha or result.get('case_sha256') != case_sha):
        raise CheckError('Short producer did not complete its exact registered physical interval')
    return result


def _match_passed(value):
    return (value.get('status') == 'exact_match'
        and type(value.get('difference_count')) is int and value['difference_count'] == 0
        and type(value.get('maximum_absolute_numeric_difference')) in (int, float)
        and value['maximum_absolute_numeric_difference'] == 0.
        and value.get('physical_elapsed_fields_retained') is True)


def legacy_verify_comparison(value, left, right):
    left, right = Path(left).resolve(), Path(right).resolve()
    if (not _match_passed(value) or value.get('left') != str(left) or value.get('right') != str(right)
            or value.get('schema') != 'hospital_ies.strict_recovery_physical_comparison.v1'
            or value.get('original_preparation_counts') != [1, 1]
            or value.get('final_reset_counts') != [1, 1] or value.get('numerical_tolerance') != 0.):
        raise CheckError('Strict comparison is not bound to the registered producer pair')
    expected = {}
    from runtime_checks import safe_relative
    for producer in (left, right):
        with (producer / 'evidence' / 'blocks.jsonl').open('r', encoding='utf8') as stream:
            for line in stream:
                row = json.loads(line)
                path = safe_relative(producer / 'evidence', row.get('file'))
                if path.parent != producer / 'evidence' / 'blocks' or str(path) in expected:
                    raise CheckError('Comparison raw producer block path differs')
                expected[str(path)] = row.get('sha256')
    bindings = value.get('file_bindings')
    if not expected or not isinstance(bindings, list) or len(bindings) != len(expected):
        raise CheckError('Strict comparison raw file bindings are missing')
    actual = {}
    for item in bindings:
        name = item.get('path')
        if name in actual or name not in expected or item.get('sha256') != expected[name] or sha256(name) != expected[name]:
            raise CheckError('Strict comparison producer raw file linkage differs')
        actual[name] = item['sha256']
    if actual != expected:
        raise CheckError('Strict comparison raw producer coverage differs')
    return True


def legacy_verify_account(package, account_folder, producer, result):
    package, account_folder, producer = Path(package).resolve(), Path(account_folder).resolve(), Path(producer).resolve()
    source = package / 'source'
    script = package / 'scripts' / 'vendor' / 'audit_carriers.py'
    if (not carrier_account_passed(result) or result.get('producer') != str(producer)
            or result.get('source') != str(source) or result.get('script_sha256') != sha256(script)):
        raise CheckError('Independent audit is not bound to the registered producer/source/script')
    before, after = (read_json(account_folder / (name + '.json')) for name in ('BEFORE', 'AFTER'))
    if before != after or set(before) != {'producer', 'source'}:
        raise CheckError('Independent audit preservation inventories differ')
    from runtime_checks import safe_relative
    for label, root in (('producer', producer), ('source', source)):
        inventory = before[label]
        if not isinstance(inventory, dict) or not inventory:
            raise CheckError('Independent audit preservation inventory is empty')
        required = ({'CASE.json', 'BINDINGS.json', 'RESULT.json', 'evidence/blocks.jsonl', 'evidence/SUMMARY.json'}
            if label == 'producer' else {'tools/annual_program/registration.py', 'src/hospital_ies/model.py', '.git/HEAD'})
        if not required <= set(inventory):
            raise CheckError('Independent audit preservation inventory is incomplete')
        actual = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}
        if actual != set(inventory):
            raise CheckError('Independent audit preservation inventory coverage differs')
        if label == 'producer' and result.get('producer_file_count') != len(inventory):
            raise CheckError('Independent audit producer inventory count differs')
        for name, item in inventory.items():
            path = safe_relative(root, name)
            if (not path.is_file() or type(item.get('bytes')) is not int or item['bytes'] != path.stat().st_size
                    or item.get('sha256') != sha256(path)):
                raise CheckError('Independent audit producer/source original bytes differ')
    return True


def verify_comparison(value, left, right):
    require_match(value)
    adapted = dict(value, schema='hospital_ies.strict_recovery_physical_comparison.v1')
    return legacy_verify_comparison(adapted, left, right)


def verify_account(package, account_folder, producer, result):
    return verify_engineering_account(package, account_folder, producer, result)


def verify_command_exit(receipt, command, source):
    if (receipt.get('command') != command or receipt.get('cwd') != str(source)
            or type(receipt.get('pid')) is not int or receipt['pid'] < 1
            or type(receipt.get('create_time')) not in (int, float)
            or not math.isfinite(receipt['create_time']) or receipt['create_time'] <= 0):
        raise CheckError('Scientific gate process command/source identity differs')
    if any(receipt.get('environment', {}).get(name) != '1'
            for name in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS')):
        raise CheckError('Scientific gate native thread environment differs')
    return True


def verify_gate(package_root, work_root, gate):
    """Rehash all raw gate files, then deeply check producer and owned exits."""
    package, work = Path(package_root).resolve(), Path(work_root).resolve()
    if (gate.get('status') != 'passed' or gate.get('kind') != 'busy_year_target_scientific_gate'
            or gate.get('producer_concurrency') != 1 or gate.get('actual_producer_invocations') != 12
            or gate.get('accepted_result_cache') is not False):
        raise CheckError('Invalid or incomplete target scientific gate')
    if gate.get('execution_profile') != execution_contract():
        raise CheckError('Historical gate cannot admit the current execution profile')
    folder = work / 'validation' / 'scientific'
    artifacts = gate.get('artifacts')
    if not isinstance(artifacts, list) or not artifacts:
        raise CheckError('Scientific gate raw artifacts are missing')
    from runtime_checks import safe_relative
    listed = set()
    for item in artifacts:
        path = safe_relative(work, item.get('path'))
        if (not path.is_relative_to(folder) or item['path'] in listed
                or not path.is_file() or sha256(path) != item.get('sha256')):
            raise CheckError('Scientific gate raw evidence differs')
        listed.add(item['path'])
    actual = {path.relative_to(work).as_posix() for path in folder.rglob('*') if path.is_file()}
    if actual != listed:
        raise CheckError('Scientific gate raw artifact coverage differs')
    if gate.get('deployment_checks') != deployment_check_scope():
        raise CheckError('Scientific gate deployment check scope differs')
    case = read_json(folder / 'CASE.json')
    if case != validation_case():
        raise CheckError('Short scientific case is not the canonical finite-window gate')
    c_ready_case = read_json(folder / 'C_READY_CASE.json')
    if c_ready_case != c_ready_validation_case():
        raise CheckError('Short C_ready case differs from the registered inherited failure background')
    source_sha = verify_package(package)['source_tree_sha256']
    case_sha = canonical_sha256(case)
    for stage in ('full', 'recovered'):
        _completed(folder / ('fault_' + stage), source_sha, case_sha)
        account = folder / ('account_' + stage)
        verify_account(package, account, folder / ('fault_' + stage), read_json(account / 'RESULT.json'))
    _completed(folder / 'c_ready_full', source_sha, canonical_sha256(c_ready_case))
    verify_account(package, folder / 'account_C_ready', folder / 'c_ready_full',
        read_json(folder / 'account_C_ready/RESULT.json'))
    verify_comparison(read_json(folder / 'MATCH.json'), folder / 'fault_full', folder / 'fault_recovered')
    from scripts.mechanism_validation import mechanism_commands, verify_mechanism_checks
    verify_mechanism_checks(package, folder)
    required = {'default_suite','case_builder', 'fault_full', 'fault_interrupted', 'fault_restore',
        'fault_recovered', 'compare_fault', 'account_full', 'account_recovered',
        'case_builder_C_ready', 'c_ready_full', 'account_C_ready'}
    required.update(mechanism_commands(package, folder))
    commands = folder / 'commands'
    if {path.name for path in commands.iterdir() if path.is_dir()} != required:
        raise CheckError('Scientific gate is missing registered process stages')
    restore_stdout = commands / 'fault_restore' / 'stdout.log'
    restored = json.loads(restore_stdout.read_text('utf8').strip().splitlines()[-1])
    marker = Path(restored['resume_checkpoint']).resolve()
    recovered = folder / 'fault_recovered'
    if (not marker.is_relative_to(recovered) or not marker.is_file()
            or restored.get('observed_until_minute') != 90.):
        raise CheckError('Scientific gate recovered prefix identity differs')
    script = package / 'scripts' / 'server_validation.py'
    case_file = folder / 'CASE.json'
    expected_commands = {
        'default_suite':[sys.executable,'-B','-m','pytest','-q'],
        'case_builder': [sys.executable, '-B', str(script), 'case-worker', '--output', str(case_file)],
        'case_builder_C_ready': [sys.executable, '-B', str(script), 'c-ready-case-worker',
            '--output', str(folder / 'C_READY_CASE.json')],
        'c_ready_full': producer_command(folder / 'C_READY_CASE.json', folder / 'c_ready_full',
            folder / 'c_ready.stop', folder / 'c_ready_vault'),
        'account_C_ready': [sys.executable, '-B', str(package / 'scripts/audit_engineering_year.py'),
            '--source', str(package / 'source'), '--producer', str(folder / 'c_ready_full'),
            '--output', str(folder / 'account_C_ready')],
        'fault_full': producer_command(case_file, folder / 'fault_full', folder / 'full.stop', folder / 'full_vault'),
        'fault_interrupted': producer_command(case_file, folder / 'fault_interrupted', folder / 'split.stop', folder / 'split_vault'),
        'fault_restore': [sys.executable, '-B', '-m', 'tools.annual_program.recovery', 'restore',
            '--vault', str(folder / 'split_vault'), '--snapshot', 'latest', '--output', str(recovered)],
        'fault_recovered': producer_command(case_file, recovered, folder / 'split.stop', folder / 'split_vault', marker),
        'compare_fault': [sys.executable, '-B', str(package / 'scripts/compare_engineering_recovery.py'),
            '--source', str(package / 'source'), '--left', str(folder / 'fault_full'), '--right', str(recovered),
            '--output', str(folder / 'MATCH.json')]}
    for stage in ('full', 'recovered'):
        expected_commands['account_' + stage] = [sys.executable, '-B', str(package / 'scripts/audit_engineering_year.py'),
            '--source', str(package / 'source'), '--producer', str(folder / ('fault_' + stage)),
            '--output', str(folder / ('account_' + stage))]
    expected_commands.update(mechanism_commands(package, folder))
    for label in required:
        exit_result = read_json(commands / label / 'EXIT.json')
        verify_command_exit(exit_result, expected_commands[label], package / 'source')
        closure_path = commands / label / 'CLOSURE.json'
        closure = read_json(closure_path)
        if (closure.get('tree_closed') is not True or closure.get('gate_acceptable') is not True
                or closure.get('remaining') != [] or closure.get('errors') != []
                or closure.get('residual_tree_terminated') is not False
                or exit_result.get('closure', {}).get('sha256') != sha256(closure_path)
                or exit_result.get('stdout_sha256') != sha256(commands / label / 'stdout.log')
                or exit_result.get('stderr_sha256') != sha256(commands / label / 'stderr.log')):
            raise CheckError('Scientific gate owned process closure differs')
        root_identity = dict(pid=exit_result['pid'], create_time=exit_result['create_time'])
        if root_identity not in closure.get('observed_identities', []):
            raise CheckError('Scientific gate root PID/birth is absent from its owned closure')
        intentional = label == 'fault_interrupted'
        if (exit_result.get('intentional_termination') is not intentional
                or type(exit_result.get('exit_code')) is not int
                or (not intentional and exit_result['exit_code'] != 0)
                or (intentional and closure.get('forced') is not True)):
            raise CheckError('Scientific gate process exit differs')
    return True


def run_validation(package_root, work_root, binding, *, limits=None):
    """Run recovery, the seen C_ready failure, and active converter gates."""
    from runtime_checks import validate_limits
    limits=validate_limits(DEFAULT_LIMITS if limits is None else limits)
    _require_target()
    package = Path(package_root).resolve()
    work = external_work_root(package, work_root)
    if work.drive.upper() != 'E:':
        raise CheckError('Scientific validation outputs must use the actual E drive')
    folder = work / 'validation' / 'scientific'
    folder.mkdir(parents=True, exist_ok=False)
    write_new(work / 'validation' / 'REGISTRATION.json', dict(binding=binding,
        created_utc=now(), execution_profile=execution_contract(), producer_concurrency=1, actual_max_simulated_minutes_per_attempt=180.,
        max_wall_seconds=5400, annual_autostart=False, unknown_is_failure=True,
        actual_producer_invocations=12, eight_active_mechanism_cases=True,
        deployment_checks=deployment_check_scope()))
    runs = Runs(package / 'source', folder, 5400, 10_000_000_000, 30_000_000_000, 16_000_000_000)
    try:
        runs.command('default_suite',[sys.executable,'-B','-m','pytest','-q'],
            extra_environment=dict(PYTEST_PLUGINS='',PYTEST_ADDOPTS='',PYTHONPATH=os.pathsep.join(
                (str(package/'source'),str(package/'source/src'),str(package/'scripts')))))
        script = package / 'scripts' / 'server_validation.py'
        case_file = folder / 'CASE.json'
        runs.command('case_builder', [sys.executable, '-B', str(script), 'case-worker', '--output', str(case_file)])
        full, split, recovered = (folder / ('fault_' + stage) for stage in ('full', 'interrupted', 'recovered'))
        runs.command('fault_full', producer_command(case_file, full, folder / 'full.stop', folder / 'full_vault'))
        job = runs.launch('fault_interrupted', producer_command(case_file, split, folder / 'split.stop', folder / 'split_vault'))
        while True:
            runs.guard()
            if root_poll(job['owned']) is not None:
                runs.finish(job)
                raise CheckError('Deliberate interruption missed its registered prefix')
            progress = _progress(split / 'PROGRESS.jsonl')
            if progress and progress.get('block_count', 0) >= 7:
                write_new(job['folder'] / 'FORCED_TERMINATION.json', dict(created_utc=now(),
                    pid=job['metadata']['pid'], create_time=job['metadata']['create_time'], observed_progress=progress))
                job['owned'].terminate_tree()
                runs.finish(job, allow_nonzero=True)
                break
            time.sleep(.01)
        restore = runs.launch('fault_restore', [sys.executable, '-B', '-m', 'tools.annual_program.recovery',
            'restore', '--vault', str(folder / 'split_vault'), '--snapshot', 'latest', '--output', str(recovered)])
        runs.wait([restore])
        payload = json.loads((restore['folder'] / 'stdout.log').read_text('utf8').strip().splitlines()[-1])
        marker = Path(payload['resume_checkpoint']).resolve()
        if not marker.is_relative_to(recovered.resolve()) or not marker.is_file() or payload.get('observed_until_minute') != 90.:
            raise CheckError('Recovered snapshot does not preserve the registered physical prefix')
        runs.command('fault_recovered', producer_command(case_file, recovered, folder / 'split.stop', folder / 'split_vault', marker))
        runs.command('compare_fault', [sys.executable, '-B', str(package / 'scripts/compare_engineering_recovery.py'),
            '--source', str(package / 'source'), '--left', str(full), '--right', str(recovered),
            '--output', str(folder / 'MATCH.json')])
        for stage, producer in (('full', full), ('recovered', recovered)):
            runs.command('account_' + stage, [sys.executable, '-B', str(package / 'scripts/audit_engineering_year.py'),
                '--source', str(package / 'source'), '--producer', str(producer), '--output', str(folder / ('account_' + stage))])
        c_ready_case = folder / 'C_READY_CASE.json'
        runs.command('case_builder_C_ready', [sys.executable, '-B', str(script), 'c-ready-case-worker',
            '--output', str(c_ready_case)])
        c_ready_full = folder / 'c_ready_full'
        runs.command('c_ready_full', producer_command(c_ready_case, c_ready_full,
            folder / 'c_ready.stop', folder / 'c_ready_vault'))
        runs.command('account_C_ready', [sys.executable, '-B', str(package / 'scripts/audit_engineering_year.py'),
            '--source', str(package / 'source'), '--producer', str(c_ready_full), '--output', str(folder / 'account_C_ready')])
        from scripts.mechanism_validation import run_mechanism_checks
        run_mechanism_checks(package, work, folder, runs)
    except BaseException as error:
        write_new(work / 'validation' / 'FAILURE.json', dict(status='failed', binding=binding,
            created_utc=now(), error=dict(type=type(error).__name__, message=str(error))))
        raise
    finally:
        runs.close()
    # Every scientific, raw log and native proof file is bound. The gate itself
    # lives outside this directory to avoid a recursive content hash.
    artifacts = [dict(path=path.relative_to(work).as_posix(), sha256=sha256(path))
        for path in sorted(folder.rglob('*')) if path.is_file()]
    gate = dict(kind='busy_year_target_scientific_gate', status='passed', created_utc=now(), binding=binding,
        execution_profile=execution_contract(), producer_concurrency=1, actual_producer_invocations=12, accepted_result_cache=False,
        runtime_limits=limits, concurrency_scope='measured_short_physics_only; runtime_counts_configured_separately',
        deployment_checks=deployment_check_scope(),
        artifacts=artifacts, annual_autostart=False, clinical_or_OEM_qualification=False)
    verify_gate(package, work, gate)
    write_new(work / 'validation' / 'SCIENTIFIC_GATE.json', gate)
    from runtime_checks import identity_binding
    if identity_binding(package, binding['frozen_root'], work) != binding:
        raise CheckError('Package, host, Python or frozen case identity changed during qualification')
    admission = dict(status='passed', created_utc=now(), binding=binding,
        validation_sha256=sha256(work / 'validation' / 'SCIENTIFIC_GATE.json'), limits=limits,
        annual_autostart=False, measured_short_producer_concurrency=1,
        configured_producer_concurrency=limits['max_producers'])
    write_new(work / 'admission' / 'ADMISSION.json', admission)
    return admission


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('case-worker', 'c-ready-case-worker', 'license-worker'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    _require_target()
    if args.command == 'license-worker':
        license_worker(args.output)
    elif args.command == 'c-ready-case-worker':
        c_ready_case_worker(args.output)
    else:
        case_worker(args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

