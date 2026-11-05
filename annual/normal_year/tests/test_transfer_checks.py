"""Handoff-only tests: tiny files and Git; never import the scientific source."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

PACKAGE = Path(__file__).resolve().parents[1]


def load_checks():
    spec = importlib.util.spec_from_file_location('transfer_checks_under_test', PACKAGE / 'transfer_checks.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), 'utf8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def inventory(package):
    write(package / 'MANIFEST.json', {'files': [dict(path=p.relative_to(package).as_posix(), sha256=sha(p), size_bytes=p.stat().st_size)
        for p in sorted(package.rglob('*')) if p.is_file() and '.git' not in p.parts and p.name != 'MANIFEST.json']})


@pytest.fixture
def sample(tmp_path, monkeypatch):
    checks = load_checks()
    package = tmp_path / '包'
    source = package / 'source'
    source.mkdir(parents=True)
    (source / 'inputs').mkdir()
    for name in ('one.py', 'two.py', 'three.py', 'inputs/input1.csv', 'inputs/input2.csv'):
        (source / name).write_text('tiny fixture ' + name, 'utf8')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()
    git('init', '--quiet')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', 'tiny fixture')
    commit = git('rev-parse', 'HEAD')
    scientific = {n: sha(source / n) for n in ('one.py', 'two.py', 'three.py')}
    inputs = {n: sha(source / n) for n in ('inputs/input1.csv', 'inputs/input2.csv')}
    monkeypatch.setattr(checks, 'SOURCE_COUNT', 3)
    monkeypatch.setattr(checks, 'INPUT_COUNT', 2)
    monkeypatch.setattr(checks, 'PINNED_COMMIT', commit)
    monkeypatch.setattr(checks, 'SCIENTIFIC_SHA256', canonical(scientific))
    monkeypatch.setattr(checks, 'INPUT_SHA256', canonical(inputs))
    # Tiny synthetic gate receipts test orchestration, never a real scientific acceptance.
    monkeypatch.setattr(checks, 'verify_engineering_account', lambda *args: True)
    write(package / 'evidence/SOURCE_FREEZE.json', dict(status='passed', git_commit=commit,
        source=scientific, inputs=inputs, source_sha256=canonical(scientific), input_sha256=canonical(inputs)))
    write(package / 'evidence/DATA_APPROVAL.json', dict(source_input_sha256=inputs, approval_answer='allowed'))
    cases = []
    for method in ('M_available', 'M_ready', 'C_ready'):
        case = dict(execution_revision=dict(model_id='annual_responsive_dynamic_bridge_20261104.v1',owner_contract='hospital_ies.annual_dynamic.accepted_engineering_owner.v1',load_cadence='hourly_original_held',dispatch_minutes=15,physical_max_segment_minutes=1,declared_calendar=[]),configuration='H2', annual_program=dict(parameters=dict(method=method, mode='N', configuration='H2',
            start_hour=1, duration_minutes=525600.0, calendar=[])), scenario=dict(canonical_class='N',
            start_hour=1, duration_steps=35040, step_minutes=15.0, model_closure_events=[],
            exogenous_stream_id='shared', base_state_id='shared-initial'))
        path = package / 'cases' / (method + '.json')
        write(path, case)
        cases.append(dict(method=method, path='cases/' + method + '.json', file_sha256=sha(path), canonical_sha256=canonical(case)))
    write(package / 'cases/INDEX.json', dict(source_commit=commit, source_scientific_sha256=canonical(scientific),
        input_sha256=canonical(inputs), cases=cases))
    inventory(package)
    return checks, package, tmp_path / 'work'


def fake_host():
    return dict(hostname='server', system='Windows', release='fixture', machine='AMD64',
        cpu_model='AMD EPYC 9654 96-Core Processor', ram_total_bytes=256_000_000_000,
        ram_available_bytes=240_000_000_000, disk_free_bytes=2_000_000_000_000,
        cpu_count_logical=192, cpu_affinity=list(range(192)))


def make_validation(checks, package, work):
    binding = checks.identity_binding(package, work, host=fake_host())
    stages = {}
    for name in ('suite', 'license', 'short'):
        artifact = work / 'validation' / name / 'RECEIPT.json'
        payload = dict(status='passed', binding=binding, kind=name, elapsed_seconds=1.0,
            created_utc=datetime.now(timezone.utc).isoformat())
        if name == 'suite':
            payload.update(exit_code=0)
        elif name == 'license':
            payload.update(full_size_model_verified=True, workers=3, all_exit_codes=[0, 0, 0], concurrent=True)
        else:
            payload.update(execution_profile=checks.execution_contract(), actual_interrupted_cases=['normal', 'fault'], independent_carrier_accounts_passed=True,
                exact_recovery_comparisons_passed=True, accepted_result_cache=False, reset_count_one=True,
                physical_elapsed_fields_retained=True, normal_minutes=60.0, fault_minutes=180.0,
                producer_concurrency=2, max_total_processes=2, threads_per_producer=1,
                process_tree_stop_verified=True, normal_priority=True)
        payload['artifacts'] = []
        if name == 'license':
            concurrency = artifact.parent / 'CONCURRENT_MODELS.json'
            write(concurrency, dict(workers=3, ready=[dict(status='passed', variables=2101, constraints=2101, actual_threads=1)] * 3))
            payload['artifacts'].append(dict(role='license_concurrency', path=concurrency.relative_to(work).as_posix(), sha256=sha(concurrency)))
        if name == 'short':
            for kind in ('normal', 'fault'):
                match = artifact.parent / ('MATCH_' + kind + '.json')
                write(match, dict(schema='hospital_ies.annual_dynamic.strict_recovery_comparison.v1',execution_model_id='annual_responsive_dynamic_bridge_20261104.v1',annual_dynamic_owner_contract='hospital_ies.annual_dynamic.accepted_engineering_owner.v1',dynamic_state_and_queues_compared=True,status='exact_match', maximum_absolute_numeric_difference=0., difference_count=0,
                    physical_elapsed_fields_retained=True))
                payload['artifacts'].append(dict(role='match_' + kind, path=match.relative_to(work).as_posix(), sha256=sha(match)))
                for stage in ('full', 'recovered'):
                    account = artifact.parent / ('account_' + kind + '_' + stage) / 'RESULT.json'
                    write(account, dict(status='passed', counts={k: {'passed': 1} for k in ('e', 'c', 'steam')},
                        original_bytes_unchanged=True, source_bytes_unchanged=True))
                    payload['artifacts'].append(dict(role='account_' + kind + '_' + stage,
                        path=account.relative_to(work).as_posix(), sha256=sha(account)))
        command_dir = artifact.parent / 'commands' / 'command'
        command_dir.mkdir(parents=True)
        for stream in ('stdout', 'stderr'):
            (command_dir / (stream + '.log')).write_text('tiny unit fixture, no scientific execution', 'utf8')
        command_receipt = command_dir / 'EXIT.json'
        closure = command_dir / 'CLOSURE.json'
        write(closure, dict(tree_closed=True, gate_acceptable=True, remaining=[], errors=[],
            observed_identities=[dict(pid=9000, create_time=1.0)]))
        write(command_receipt, dict(exit_code=0, command=['fixture-only'], cwd=str(package / 'source'),
            closure=dict(path='CLOSURE.json', sha256=sha(closure)),
            elapsed_seconds=1.0, stdout_sha256=sha(command_dir / 'stdout.log'), stderr_sha256=sha(command_dir / 'stderr.log')))
        payload['commands'] = [dict(path=command_receipt.relative_to(work).as_posix(), sha256=sha(command_receipt))]
        write(artifact, payload)
        stages[name] = dict(path=artifact.relative_to(work).as_posix(), sha256=sha(artifact))
    validation = work / 'validation/VALIDATION.json'
    write(validation, dict(kind='normal_year_server_validation', status='passed', binding=binding,
        created_utc=datetime.now(timezone.utc).isoformat(),
        stages=stages, year_completed=False, annual_autostart=False))
    return binding, validation


def limits():
    return dict(max_producers=2, max_accounts=1, max_total_processes=3, max_wall_seconds=604800,
        grace_seconds=30, rss_limit_bytes=200_000_000_000, min_available_ram_bytes=10_000_000_000,
        min_free_disk_bytes=100_000_000_000, initial_free_disk_required_bytes=600_000_000_000,
        max_recoveries_global=1, threads_per_producer=1, restore_timeout_seconds=1800, poll_seconds=1)


def test_verify_portable_and_import_does_not_load_science(sample):
    checks, package, work = sample
    result = checks.verify_package(package)
    assert result['status'] == 'passed'
    assert result['source_commit'] == checks.PINNED_COMMIT
    moved = package.parent / 'moved 包'
    shutil.copytree(package, moved)
    assert checks.verify_package(moved)['cases'] == result['cases']
    assert not any(k.startswith(('hospital_ies', 'gurobipy')) for k in sys.modules)


@pytest.mark.parametrize('kind', ['source', 'input', 'case', 'extra', 'head', 'manifest_path'])
def test_verify_rejects_tampering(sample, kind):
    checks, package, work = sample
    if kind in ('source', 'input', 'case'):
        target = {'source': 'source/one.py', 'input': 'source/inputs/input1.csv', 'case': 'cases/M_ready.json'}[kind]
        (package / target).write_text('altered', 'utf8')
    elif kind == 'extra':
        (package / 'unlisted.py').write_text('unknown tool', 'utf8')
    elif kind == 'head':
        (package / 'source/.git/HEAD').write_text('0' * 40 + '\n', 'ascii')
    else:
        manifest = json.loads((package / 'MANIFEST.json').read_text('utf8'))
        manifest['files'][0]['path'] = '../escape'
        write(package / 'MANIFEST.json', manifest)
    with pytest.raises(ValueError):
        checks.verify_package(package)


def test_workroot_must_be_external(sample):
    checks, package, _ = sample
    with pytest.raises(ValueError, match='outside'):
        checks.host_probe(package, package / 'work')


def test_probe_is_readonly_and_solver_free(sample):
    checks, package, work = sample
    result = checks.host_probe(package, work)
    assert result['solver_probe_performed'] is False
    assert result['scientific_execution_performed'] is False
    assert result['host']['hostname']
    assert not work.exists()


def test_missing_admission_is_not_historical_permission(sample):
    checks, package, work = sample
    with pytest.raises(ValueError, match='admission'):
        checks.require_admission(package, work)


def test_admission_binds_current_host_source_work_and_real_validation(sample, monkeypatch):
    checks, package, work = sample
    monkeypatch.setattr(checks, 'current_host', lambda *_: fake_host())
    _, validation = make_validation(checks, package, work)
    receipt = checks.create_admission(package, work, limits(), valid_hours=24)
    assert checks.require_admission(package, work) == receipt
    validation.write_text('{}', 'utf8')
    with pytest.raises(ValueError, match='validation'):
        checks.require_admission(package, work)


@pytest.mark.parametrize('bad', ['hostname', 'expired', 'work_root', 'missing_short', 'unknown_carrier', 'license', 'resource', 'hardware'])
def test_admission_fails_closed(sample, monkeypatch, bad):
    checks, package, work = sample
    host = fake_host()
    monkeypatch.setattr(checks, 'current_host', lambda *_: host)
    binding, validation_path = make_validation(checks, package, work)
    if bad == 'hostname':
        host['hostname'] = 'other-host'
    elif bad == 'work_root':
        validation = json.loads(validation_path.read_text('utf8'))
        validation['binding']['work_root'] = '/old-server/path'
        write(validation_path, validation)
    elif bad in ('missing_short', 'unknown_carrier', 'license'):
        validation = json.loads(validation_path.read_text('utf8'))
        if bad == 'missing_short':
            del validation['stages']['short']
        else:
            name = 'short' if bad == 'unknown_carrier' else 'license'
            path = work / validation['stages'][name]['path']
            payload = json.loads(path.read_text('utf8'))
            payload['independent_carrier_accounts_passed' if name == 'short' else 'full_size_model_verified'] = False
            write(path, payload)
            validation['stages'][name]['sha256'] = sha(path)
        write(validation_path, validation)
    elif bad == 'resource':
        host['disk_free_bytes'] = 100
    elif bad == 'hardware':
        host['cpu_model'] = 'Intel fixture'
    if bad == 'expired':
        checks.create_admission(package, work, limits(), valid_hours=24)
        path = work / 'admission/ADMISSION.json'
        receipt = json.loads(path.read_text('utf8'))
        receipt['expires_utc'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        write(path, receipt)
        with pytest.raises(ValueError):
            checks.require_admission(package, work)
    else:
        with pytest.raises(ValueError):
            checks.create_admission(package, work, limits(), valid_hours=24)


def test_limits_accept_week_but_refuse_unbounded_or_permission_expansion(sample):
    checks, package, work = sample
    assert checks.validate_limits(limits())['max_wall_seconds'] > 72 * 3600
    for key, bad in [('max_wall_seconds', float('inf')), ('max_accounts', 0), ('max_total_processes', 0),
                     ('threads_per_producer', 2), ('max_recoveries_global', 2), ('initial_free_disk_required_bytes', 0)]:
        value = limits()
        value[key] = bad
        with pytest.raises(ValueError):
            checks.validate_limits(value)


def test_server_validator_plan_is_explicit_and_has_no_annual(sample):
    spec = importlib.util.spec_from_file_location('server_validate_under_test', PACKAGE / 'scripts/server_validate.py')
    validator = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PACKAGE))
    try:
        spec.loader.exec_module(validator)
        plan = validator.validation_plan(PACKAGE, Path('/external/work'))
        assert [(p['kind'], p['minutes'], p['kill_after_blocks']) for p in plan] == [('normal', 60.0, 3), ('fault', 180.0, 7)]
        assert all(p['accepted_result_cache'] is False for p in plan)
        assert all(p['case_file'].is_relative_to(PACKAGE) for p in plan)
    finally:
        sys.path.pop(0)


def test_short_measurement_is_retained_without_becoming_a_runtime_ceiling(sample, monkeypatch):
    checks, package, work = sample
    monkeypatch.setattr(checks, 'current_host', lambda *_: fake_host())
    _,validation=make_validation(checks, package, work)
    short=work/'validation/short/RECEIPT.json'
    stage=json.loads(short.read_text('utf8'));stage['producer_concurrency']=1
    write(short,stage)
    all_stages=json.loads(validation.read_text('utf8'))
    all_stages['stages']['short']['sha256']=sha(short)
    write(validation,all_stages)
    requested = limits()
    requested['max_producers'] = 2
    receipt=checks.create_admission(package, work, requested)
    assert receipt['limits']['max_producers']==2
    assert json.loads(short.read_text('utf8'))['producer_concurrency']==1


def test_recovery_command_preserves_registered_vault(sample):
    spec = importlib.util.spec_from_file_location('server_validate_command_test', PACKAGE / 'scripts/server_validate.py')
    validator = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PACKAGE))
    try:
        spec.loader.exec_module(validator)
        paths = validator.case_paths(Path('gate'), 'normal', 'interrupted')
        resumed = validator.case_paths(Path('gate'), 'normal', 'recovered')
        assert paths['stop'] == resumed['stop']
        assert paths['vault'] == resumed['vault']
        assert paths['output'] != resumed['output']
        command = validator.producer_command(Path('source'), Path('case'), Path('output'), Path('approval'),
            Path('shared.stop'), Path('shared_vault'), Path('checkpoint'))
        assert command[command.index('--recovery-vault') + 1] == 'shared_vault'
        assert '--accepted-result-cache' not in command
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize('target', ['audit', 'stdout', 'match', 'license'])
def test_admission_rechecks_original_validation_artifacts(sample, monkeypatch, target):
    checks, package, work = sample
    monkeypatch.setattr(checks, 'current_host', lambda *_: fake_host())
    make_validation(checks, package, work)
    path = {'audit': 'short/account_normal_full/RESULT.json', 'stdout': 'short/commands/command/stdout.log',
        'match': 'short/MATCH_fault.json', 'license': 'license/CONCURRENT_MODELS.json'}[target]
    (work / 'validation' / path).write_text('{}', 'utf8')
    with pytest.raises(ValueError):
        checks.create_admission(package, work, limits())


def load_validator():
    spec = importlib.util.spec_from_file_location('server_validate_fake_process_test', PACKAGE / 'scripts/server_validate.py')
    validator = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PACKAGE))
    sys.path.insert(0, str(PACKAGE / 'scripts'))
    spec.loader.exec_module(validator)
    return validator


def test_server_validation_owns_only_fake_process_and_retains_exit_logs(tmp_path):
    validator = load_validator()
    folder = tmp_path / 'fake-validation'
    folder.mkdir()
    runs = validator.Runs(tmp_path, folder, 10, 1_000_000_000, 1, 1)
    try:
        result = runs.command('fake', [sys.executable, '-c', 'import time;print("fake unit child",flush=True);time.sleep(.2)'])
        assert result['exit_code'] == 0
        assert result['normal_priority'] is True
        assert (folder / 'commands/fake/stdout.log').read_text('utf8').strip() == 'fake unit child'
        assert (folder / 'commands/fake/EXIT.json').is_file()
    finally:
        runs.close()


def test_server_launch_failure_closes_owned_fake_tree(tmp_path, monkeypatch):
    validator = load_validator()
    import vendor.owned_process_hidden as vendor
    import psutil
    trees = []
    class FakeOwned:
        def __init__(self, *args, **kwargs):
            self.proc = type('P', (), {'pid': 12345})()
            self.terminated = self.closed = False
            trees.append(self)
        def terminate_tree(self):
            self.terminated = True
        def close(self):
            self.closed = True
    monkeypatch.setattr(validator, 'create_owned_tree', FakeOwned)
    def fail(*args, **kwargs):
        raise RuntimeError('fake affinity setup failed')
    monkeypatch.setattr(psutil, 'Process', fail)
    runs = validator.Runs(tmp_path, tmp_path / 'stage', 10, 1_000_000_000, 1, 1)
    with pytest.raises(RuntimeError, match='fake affinity'):
        runs.launch('fake', ['unexecuted-fake'])
    assert trees[0].terminated is True
    assert trees[0].closed is True


def test_three_total_processes_can_include_two_producers_and_one_helper(sample, monkeypatch):
    checks, package, work = sample
    monkeypatch.setattr(checks, 'current_host', lambda *_: fake_host())
    _, validation = make_validation(checks, package, work)
    stage_path = work / 'validation/short/RECEIPT.json'
    stage = json.loads(stage_path.read_text('utf8'))
    stage['max_total_processes'] = 3
    write(stage_path, stage)
    combined = json.loads(validation.read_text('utf8'))
    combined['stages']['short']['sha256'] = sha(stage_path)
    write(validation, combined)
    assert checks.create_admission(package, work, limits())['status'] == 'passed'


@pytest.mark.parametrize('relative', ['source/unfrozen.py', 'source/inputs/extra.csv'])
def test_manifest_cannot_admit_unfrozen_scientific_or_input_file(sample, relative):
    checks, package, work = sample
    (package / relative).write_text('unapproved', 'utf8')
    inventory(package)
    with pytest.raises(ValueError, match='file.set'):
        checks.verify_package(package)


def test_owned_constructor_failure_closes_raw_log_handles(tmp_path, monkeypatch):
    validator = load_validator()
    import vendor.owned_process_hidden as vendor
    handles = []
    def fail(*args, **kwargs):
        handles.extend([kwargs['stdout'], kwargs['stderr']])
        raise RuntimeError('fake Job assignment failure')
    monkeypatch.setattr(validator, 'create_owned_tree', fail)
    runs = validator.Runs(tmp_path, tmp_path / 'stage', 10, 1_000_000_000, 1, 1)
    with pytest.raises(RuntimeError, match='fake Job assignment'):
        runs.launch('fake', ['unexecuted-fake'])
    assert all(handle.closed for handle in handles)


def closure_job(validator, tmp_path, owner):
    folder = tmp_path / 'closure-job'
    folder.mkdir()
    handles = [(folder / (name + '.log')).open('w', encoding='utf8') for name in ('stdout', 'stderr')]
    return dict(label='toy', folder=folder, owned=owner, handles=handles, start=validator.time.monotonic(),
        metadata=dict(command=['unexecuted-toy'], cwd=str(tmp_path), pid=9001, create_time=1.0))


def test_successful_root_exit_with_live_owned_child_is_failed_gate(tmp_path):
    validator = load_validator()
    class Owner:
        known = {9001: dict(pid=9001, create_time=1.0), 9002: dict(pid=9002, create_time=2.0)}
        native = None
        closed = False
        def poll(self): return 0
        def refresh(self): return [] if self.closed else [self.known[9002]]
        def terminate_tree(self): self.closed = True
        def close(self): self.closed = True
    runs = validator.Runs(tmp_path, tmp_path, 10, 1_000_000_000, 1, 1)
    job = closure_job(validator, tmp_path, Owner())
    runs.jobs.append(job)
    with pytest.raises(ValueError, match='descendant'):
        runs.finish(job)
    receipt = json.loads((job['folder'] / 'CLOSURE.json').read_text('utf8'))
    assert receipt['residual_tree_terminated'] is True
    assert receipt['tree_closed'] is True
    assert receipt['gate_acceptable'] is False


def test_unknown_owned_job_membership_cannot_pass_gate(tmp_path):
    validator = load_validator()
    class Owner:
        known = {9001: dict(pid=9001, create_time=1.0)}
        native = None
        closed = False
        def poll(self): return 0
        def refresh(self): raise PermissionError('toy Job membership denied')
        def terminate_tree(self): self.closed = True
        def close(self): self.closed = True
    runs = validator.Runs(tmp_path, tmp_path, 10, 1_000_000_000, 1, 1)
    owner = Owner()
    job = closure_job(validator, tmp_path, owner)
    runs.jobs.append(job)
    with pytest.raises(ValueError, match='closure'):
        runs.finish(job)
    assert owner.closed is True
    receipt = json.loads((job['folder'] / 'CLOSURE.json').read_text('utf8'))
    assert receipt['tree_closed'] is False
    assert receipt['gate_acceptable'] is False


@pytest.mark.parametrize('field,value', [('system', 'Linux'), ('cpu_model', 'Other CPU'), ('ram_total_bytes', 239_000_000_000)])
def test_scientific_host_gate_requires_full_declared_hardware(sample, field, value):
    checks, package, work = sample
    host = fake_host()
    host[field] = value
    with pytest.raises(ValueError):
        checks.require_server_host(host)


@pytest.mark.parametrize('bad', ['bytes', 'remaining', 'unknown'])
def test_admission_rechecks_actual_process_tree_closure(sample, monkeypatch, bad):
    checks, package, work = sample
    monkeypatch.setattr(checks, 'current_host', lambda *_: fake_host())
    _, validation = make_validation(checks, package, work)
    closure_path = work / 'validation/short/commands/command/CLOSURE.json'
    payload = json.loads(closure_path.read_text('utf8'))
    if bad == 'remaining':
        payload['remaining'] = [dict(pid=99, create_time=1.0)]
    elif bad == 'unknown':
        payload['errors'] = ['membership denied']
    else:
        payload['tree_closed'] = False
    write(closure_path, payload)
    # Even rebinding nested hashes must not turn a failed/unknown closure into pass.
    command_path = closure_path.parent / 'EXIT.json'
    command = json.loads(command_path.read_text('utf8'))
    command['closure']['sha256'] = sha(closure_path)
    write(command_path, command)
    stage_path = work / 'validation/short/RECEIPT.json'
    stage = json.loads(stage_path.read_text('utf8'))
    stage['commands'][0]['sha256'] = sha(command_path)
    write(stage_path, stage)
    combined = json.loads(validation.read_text('utf8'))
    combined['stages']['short']['sha256'] = sha(stage_path)
    write(validation, combined)
    with pytest.raises(ValueError, match='closure'):
        checks.create_admission(package, work, limits())


def test_real_toy_root_exit_cannot_hide_living_descendant(tmp_path):
    validator = load_validator()
    folder = tmp_path / 'real-toy-closure'
    folder.mkdir()
    runs = validator.Runs(tmp_path, folder, 10, 1_000_000_000, 1, 1)
    command = [sys.executable, '-B', '-c',
        'import subprocess,sys,time;child=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(15)"]);'
        'print(child.pid,flush=True);time.sleep(.3)']
    job = runs.launch('toy_parent', command)
    try:
        with pytest.raises(ValueError, match='descendant'):
            runs.wait([job])
        closure = json.loads((job['folder'] / 'CLOSURE.json').read_text('utf8'))
        child_pid = int((job['folder'] / 'stdout.log').read_text('utf8').strip())
        assert child_pid in {i['pid'] for i in closure['observed_identities']}
        assert closure['tree_closed'] is True
        assert closure['remaining'] == []
        assert closure['gate_acceptable'] is False
        assert closure['residual_tree_terminated'] is True
    finally:
        runs.close()


@pytest.mark.parametrize('field,value', [('passed', True), ('passed', 1.0), ('unknown', None),
    ('unknown', False), ('unknown', 0.0), ('failed', None), ('failed', False), ('failed', 0.0), ('extra', 0)])
def test_rebound_original_carrier_counter_must_have_strict_integer_schema(sample, monkeypatch, field, value):
    checks, package, work = sample
    monkeypatch.setattr(checks, 'current_host', lambda *_: fake_host())
    _, validation = make_validation(checks, package, work)
    account_path = work / 'validation/short/account_normal_full/RESULT.json'
    account = json.loads(account_path.read_text('utf8'))
    account['counts']['e'][field] = value
    write(account_path, account)
    stage_path = work / 'validation/short/RECEIPT.json'
    stage = json.loads(stage_path.read_text('utf8'))
    next(a for a in stage['artifacts'] if a['role'] == 'account_normal_full')['sha256'] = sha(account_path)
    write(stage_path, stage)
    combined = json.loads(validation.read_text('utf8'))
    combined['stages']['short']['sha256'] = sha(stage_path)
    write(validation, combined)
    with pytest.raises(ValueError, match='carrier account'):
        checks.create_admission(package, work, limits())
