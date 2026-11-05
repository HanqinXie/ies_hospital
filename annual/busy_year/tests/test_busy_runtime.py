"""Runtime boundaries use fake processes; these tests never run a year."""
import importlib
from pathlib import Path
import sys

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))


def implementation(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        pytest.fail('Busy-year runtime implementation is missing: ' + name, pytrace=False)


def target():
    return dict(system='Windows', cpu_model='AMD EPYC 9654 96-Core Processor',
                ram_total_bytes=256_000_000_000)


def test_runtime_has_no_host_bypass_for_non_target_machine():
    module = implementation('runtime_checks')
    module.require_server_host(target())
    for change in (dict(system='Linux'), dict(cpu_model='Intel Core Ultra'),
                   dict(ram_total_bytes=239_999_999_999), dict(ram_total_bytes=True)):
        with pytest.raises(ValueError):
            module.require_server_host(dict(target(), **change))


def test_explicit_process_counts_replace_the_legacy_sequential_ceiling():
    module = implementation('server_runner')
    checks = implementation('runtime_checks')
    limits = module.validate_limits(checks.DEFAULT_LIMITS)
    assert limits['max_producers']==3 and limits['max_total_processes']==4
    assert module.validate_limits(dict(limits,max_producers=6,max_accounts=2,max_total_processes=7))['max_producers']==6
    for name in ('max_producers', 'max_accounts', 'max_total_processes'):
        with pytest.raises(ValueError):
            module.validate_limits(dict(limits, **{name: 0}))
    with pytest.raises(ValueError):module.validate_limits(dict(limits,threads_per_producer=2))


def test_producer_uses_external_case_and_original_finite_recovery_contract(tmp_path):
    module = implementation('server_runner')
    paths = module.attempt_paths(tmp_path, 'M_ready', 0)
    case = dict(file=str(tmp_path / 'frozen' / 'M_ready.json'))
    command = module.producer_command(PACKAGE, case, paths)
    assert command[command.index('--case-file') + 1] == case['file']
    assert command[command.index('--approval-receipt') + 1] == str(PACKAGE / 'config' / 'DATA_APPROVAL.json')
    assert command[command.index('--recovery-every-blocks') + 1] == '4'
    assert '--accepted-result-cache' not in command
    assert '--resume-checkpoint' not in command
    resumed = module.producer_command(PACKAGE, case, paths, tmp_path / 'checkpoint')
    assert resumed[-2:] == ['--resume-checkpoint', str(tmp_path / 'checkpoint')]


def test_per_case_state_is_isolated_and_vault_stable_across_attempts(tmp_path):
    module = implementation('server_runner')
    first = module.attempt_paths(tmp_path, 'M_ready', 0)
    resumed = module.attempt_paths(tmp_path, 'M_ready', 1)
    other = module.attempt_paths(tmp_path, 'C_ready', 0)
    assert first['stop'] == resumed['stop']
    assert first['recovery'] == resumed['recovery']
    assert first['output'] != resumed['output']
    for field in first:
        assert first[field] != other[field]


def test_physical_service_failure_is_not_solver_completion(tmp_path):
    module = implementation('server_runner')
    complete = dict(execution_model_id='annual_responsive_dynamic_bridge_20261104.v1',annual_dynamic_owner_contract='hospital_ies.annual_dynamic.accepted_engineering_owner.v1',files={},status='completed', observed_until_minute=525600., unknown_tail_minutes=0.,
                    reset_count=1, source_and_inputs_unchanged=True)
    assert module.producer_passed(complete, 0)
    assert not module.producer_passed(dict(complete, unknown_tail_minutes=15.), 0)
    assert not module.producer_passed(dict(complete, reset_count=2), 0)
    assert not module.producer_passed(dict(complete, reset_count=True), 0)
    assert not module.producer_passed(complete, 1)


def test_independent_counter_categories_are_strict():
    module = implementation('server_runner')
    passed = dict(status='passed', original_bytes_unchanged=True, source_bytes_unchanged=True,
                  counts={name: {'passed': 1} for name in ('e', 'c', 'steam')})
    assert module.account_passed(passed, 0)
    for value in (True, 0.0, None, -1, '0', 1):
        changed = dict(passed, counts={name: {'passed': 1, 'failed': value} for name in ('e', 'c', 'steam')})
        assert not module.account_passed(changed, 0)


def test_work_root_cannot_overlap_read_only_package(tmp_path):
    module = implementation('runtime_checks')
    package = tmp_path / 'package'
    with pytest.raises(ValueError):
        module.external_work_root(package, package / 'outputs')
    with pytest.raises(ValueError):
        module.external_work_root(package, tmp_path)
    assert module.external_work_root(package, tmp_path / 'external') == tmp_path / 'external'


@pytest.mark.parametrize('inside', ['runtime', 'frozen'])
def test_no_new_runtime_or_freeze_can_be_written_inside_original_reference(tmp_path, monkeypatch, inside):
    import json
    module = implementation('runtime_checks')
    package, reference = tmp_path / 'package', tmp_path / 'old_normal' / 'producer'
    frozen, work = tmp_path / 'frozen', tmp_path / 'work'
    if inside == 'runtime':
        work = reference / 'busy_work'
    else:
        frozen = reference / 'busy_freeze'
    frozen.mkdir(parents=True)
    (frozen / 'INDEX.json').write_text('{}', 'utf8')
    (frozen / 'CALENDAR.json').write_text(json.dumps({'reference_proof': {'reference_output': str(reference)}}), 'utf8')
    monkeypatch.setattr(module, 'verify_package', lambda package: {})
    with pytest.raises(ValueError, match='reference'):
        module.identity_binding(package, frozen, work)


def test_template_or_omitted_frozen_root_cannot_start_campaign(tmp_path):
    module = implementation('server_runner')
    with pytest.raises((TypeError, ValueError)):
        module.load_cases(PACKAGE, None)
    with pytest.raises((OSError, ValueError)):
        module.load_cases(PACKAGE, tmp_path)


def test_native_threads_are_fixed_at_one(tmp_path):
    module = implementation('server_runner')
    environment = module.child_environment(PACKAGE, module.attempt_paths(tmp_path, 'C_ready', 0))
    for name in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        assert environment[name] == '1'
    assert environment['PYTHONDONTWRITEBYTECODE'] == '1'


def test_imported_entries_do_not_launch_processes(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Import launched a process'))
    implementation('run_years')
    implementation('launch_server')


def test_plain_python_runtime_bootstrap_never_writes_scientific_bytecode(tmp_path):
    import os
    import subprocess
    toy = tmp_path / 'scientific_source'
    toy.mkdir()
    (toy / 'busy_bytecode_probe.py').write_text('value = 17\n', 'utf8')
    code = ('import runpy,sys; runpy.run_path(sys.argv[1],run_name="bootstrap_test"); '
        'sys.path.insert(0,sys.argv[2]); import busy_bytecode_probe; '
        'assert busy_bytecode_probe.value == 17')
    environment = dict(os.environ)
    environment.pop('PYTHONDONTWRITEBYTECODE', None)
    environment.pop('PYTHONPYCACHEPREFIX', None)
    result = subprocess.run([sys.executable, '-c', code, str(PACKAGE / 'runtime_checks.py'), str(toy)],
        env=environment, cwd=str(tmp_path), text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not (toy / '__pycache__').exists(), 'Ordinary runtime bootstrap wrote into the immutable scientific source'


def test_short_gate_uses_real_canonical_window_and_same_five_minute_gap():
    module = implementation('scripts.server_validation')
    case = module.validation_case()
    scene = case['scenario']
    assert scene['duration_steps'] == 12
    assert scene['shock_start_minute'] == 45.25
    assert scene['shock_duration_minutes'] == 60.5
    assert scene['shock_by_zone'] == {'ER_TRAUMA1_FLR_1': {'c': .25}}
    assert [item['time_minute'] for item in scene['model_closure_events']] == [60.25, 90.25, 95.25, 140.25]
    assert scene['enable_propagation'] is False


def test_copied_gate_summary_cannot_mint_target_qualification(tmp_path):
    module = implementation('scripts.server_validation')
    summary = dict(execution_profile=module.execution_contract(),kind='busy_year_target_scientific_gate', status='passed', producer_concurrency=1,
        actual_producer_invocations=12, accepted_result_cache=False, artifacts=[])
    with pytest.raises(ValueError, match='raw artifacts'):
        module.verify_gate(PACKAGE, tmp_path, summary)


def test_comparison_report_cannot_reference_another_producer(tmp_path):
    module = implementation('scripts.server_validation')
    left, right = tmp_path / 'left', tmp_path / 'right'
    report = dict(status='exact_match', difference_count=0, maximum_absolute_numeric_difference=0.,
        physical_elapsed_fields_retained=True, left=str(tmp_path / 'other'), right=str(right),
        file_bindings=[])
    with pytest.raises(ValueError, match='producer|comparison'):
        module.verify_comparison(report, left, right)


def test_account_report_cannot_reference_another_producer_or_script(tmp_path):
    module = implementation('scripts.server_validation')
    report = dict(status='passed', original_bytes_unchanged=True, source_bytes_unchanged=True,
        counts={name: {'passed': 1} for name in ('e', 'c', 'steam')},
        producer=str(tmp_path / 'other'), source=str(PACKAGE / 'source'), script_sha256='wrong')
    with pytest.raises(ValueError, match='producer|audit'):
        module.verify_account(PACKAGE, tmp_path / 'account', tmp_path / 'producer', report)


@pytest.mark.parametrize('field,value', [('command', ['other-script.py']), ('cwd', 'other-source')])
def test_success_exit_must_belong_to_registered_command(field, value):
    module = implementation('scripts.server_validation')
    expected = ['python.exe', '-B', 'registered.py']
    result = dict(exit_code=0, command=expected, cwd='registered-source', pid=123,
        create_time=100., environment={name: '1' for name in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS')})
    result[field] = value
    with pytest.raises(ValueError, match='command|source'):
        module.verify_command_exit(result, expected, 'registered-source')


def test_current_comparer_block_bindings_pass_then_changed_raw_bytes_refuse(tmp_path):
    import json
    module = implementation('scripts.server_validation')
    bindings = []
    producers = [tmp_path / name for name in ('left', 'right')]
    for producer in producers:
        raw = producer / 'evidence' / 'blocks' / 'block_000000.json.gz'
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b'unchanged-native-block')
        digest = module.sha256(raw)
        (producer / 'evidence' / 'blocks.jsonl').write_text(json.dumps({'file': 'blocks/' + raw.name, 'sha256': digest}) + '\n', 'utf8')
        bindings.append(dict(path=str(raw), sha256=digest))
    report = dict(schema='hospital_ies.annual_dynamic.strict_recovery_comparison.v1',
        execution_model_id='annual_responsive_dynamic_bridge_20261104.v1',
        annual_dynamic_owner_contract='hospital_ies.annual_dynamic.accepted_engineering_owner.v1',
        dynamic_state_and_queues_compared=True,status='exact_match',
        difference_count=0, maximum_absolute_numeric_difference=0., physical_elapsed_fields_retained=True,
        left=str(producers[0]), right=str(producers[1]), numerical_tolerance=0.,
        original_preparation_counts=[1, 1], final_reset_counts=[1, 1], file_bindings=bindings)
    assert module.verify_comparison(report, *producers)
    Path(bindings[0]['path']).write_bytes(b'changed-native-block')
    with pytest.raises(ValueError, match='linkage'):
        module.verify_comparison(report, *producers)


def test_current_audit_inventory_passes_then_unlisted_raw_file_refuses(tmp_path):
    import json
    module = implementation('scripts.server_validation')
    package, producer, account = (tmp_path / name for name in ('package', 'producer', 'account'))
    source = package / 'source'
    for root, names in ((source, ['tools/annual_program/registration.py', 'src/hospital_ies/model.py', '.git/HEAD']),
            (producer, ['CASE.json', 'BINDINGS.json', 'RESULT.json', 'evidence/blocks.jsonl', 'evidence/SUMMARY.json'])):
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'unchanged-test-witness')
    script = package / 'scripts/vendor/audit_carriers.py'
    script.parent.mkdir(parents=True)
    script.write_bytes(b'unchanged-auditor')
    inventory = {label: {path.relative_to(root).as_posix(): dict(bytes=path.stat().st_size, sha256=module.sha256(path))
        for path in root.rglob('*') if path.is_file()} for label, root in (('producer', producer), ('source', source))}
    account.mkdir()
    for stage in ('BEFORE', 'AFTER'):
        (account / (stage + '.json')).write_text(json.dumps(inventory), 'utf8')
    result = dict(status='passed', original_bytes_unchanged=True, source_bytes_unchanged=True,
        counts={name: {'passed': 1} for name in ('e', 'c', 'steam')}, producer=str(producer),
        source=str(source), script_sha256=module.sha256(script), producer_file_count=len(inventory['producer']))
    assert module.legacy_verify_account(package, account, producer, result)
    (producer / 'unlisted.bin').write_bytes(b'new-file')
    with pytest.raises(ValueError, match='coverage'):
        module.legacy_verify_account(package, account, producer, result)


def test_target_guard_precedes_any_license_or_case_worker(monkeypatch, tmp_path):
    module = implementation('scripts.server_validation')
    def refuse():
        raise ValueError('not the registered target')
    monkeypatch.setattr(module, '_require_target', refuse)
    with pytest.raises(ValueError, match='registered target'):
        module.license_worker(tmp_path / 'license.json')
    with pytest.raises(ValueError, match='registered target'):
        module.case_worker(tmp_path / 'case.json')
    assert not list(tmp_path.iterdir())


class FakeOwner:
    """Tiny completed records exercise scheduling and recovery, never science."""
    commands = []
    failures_left = 0
    restore_fails = False

    def __init__(self, command, *, cwd, env, stdout, stderr):
        import json
        self.proc = type('FakeProcess', (), {'pid': -1})()
        self.code = 0
        type(self).commands.append(command)
        output = Path(command[command.index('--output') + 1])
        # A recovered producer opens the directory the restore command made.
        # Fresh producers and restore targets still require a new directory.
        output.mkdir(parents=True, exist_ok='--resume-checkpoint' in command)
        if 'restore' in command:
            self.code = 1 if type(self).restore_fails else 0
            if not self.code:
                checkpoint = output / 'resume.json'
                checkpoint.write_text('{}', 'utf8')
                stdout.write(json.dumps({'resume_checkpoint': str(checkpoint)}) + '\n')
                stdout.flush()
        elif 'tools.annual_program.runner' in command:
            if type(self).failures_left:
                type(self).failures_left -= 1
                self.code = 7
                return
            result = dict(execution_model_id='annual_responsive_dynamic_bridge_20261104.v1',annual_dynamic_owner_contract='hospital_ies.annual_dynamic.accepted_engineering_owner.v1',files={},status='completed', observed_until_minute=525600., reset_count=1,
                source_and_inputs_unchanged=True, unknown_tail_minutes=0.)
            (output / 'RESULT.json').write_text(json.dumps(result), 'utf8')
        else:
            result = dict(status='passed', original_bytes_unchanged=True, source_bytes_unchanged=True,
                counts={name: {'passed': 1} for name in ('e', 'c', 'steam')})
            (output / 'RESULT.json').write_text(json.dumps(result), 'utf8')

    def poll(self):
        return self.code

    def terminate_tree(self):
        self.code = 1

    def close(self):
        pass


def fake_campaign(tmp_path, monkeypatch, failures=0, restore_fails=False):
    runner = implementation('server_runner')
    # Preserve the original serial crash/recovery fixture; new3-way dispatch is
    # independently exercised by test_configurable_concurrency.py.
    limits = dict(implementation('runtime_checks').DEFAULT_LIMITS,max_producers=1,max_accounts=1,
        max_total_processes=1,poll_seconds=.001,grace_seconds=0)
    package, work, frozen = (tmp_path / name for name in ('package', 'work', 'frozen'))
    package.mkdir()
    work.mkdir()
    cases = [dict(method=name, case_id=name, file=str(frozen / (name + '.json')))
        for name in runner.METHODS]
    monkeypatch.setattr(runner, 'require_admission', lambda *args: dict(binding={}, limits=limits))
    monkeypatch.setattr(runner, 'load_cases', lambda *args: cases)
    FakeOwner.commands, FakeOwner.failures_left, FakeOwner.restore_fails = [], failures, restore_fails
    result = runner.run_campaign(package, work, frozen_root=frozen, owner_factory=FakeOwner,
        resource_probe=lambda *args: dict(available_ram_bytes=10**12, free_disk_bytes=10**12, rss_bytes=0),
        sleeper=lambda seconds: None)
    return result, work


def test_three_methods_have_three_initial_producers_and_three_accounts(tmp_path, monkeypatch):
    result, work = fake_campaign(tmp_path, monkeypatch)
    assert result['status'] == 'passed'
    assert result['selected_cases'] == 3
    assert result['initial_producer_processes_closed'] == 3
    assert len(FakeOwner.commands) == 6
    assert all(row['tree_closed'] for row in result['closures'])


def test_one_crash_uses_one_restore_and_never_reinitializes(tmp_path, monkeypatch):
    result, work = fake_campaign(tmp_path, monkeypatch, failures=1)
    assert result['status'] == 'passed'
    assert result['recoveries_used'] == 1
    assert sum('restore' in command for command in FakeOwner.commands) == 1
    resumed = [command for command in FakeOwner.commands if '--resume-checkpoint' in command]
    assert len(resumed) == 1
    assert 'attempt_001' in resumed[0][resumed[0].index('--output') + 1]
    assert (work / 'runtime/cases/M_available/attempt_000/output').is_dir()


def test_failed_restore_is_counted_and_blocks_remaining_methods(tmp_path, monkeypatch):
    result, work = fake_campaign(tmp_path, monkeypatch, failures=1, restore_fails=True)
    assert result['status'] == 'incomplete_or_failed'
    assert result['recoveries_used'] == 1
    assert sum('restore' in command for command in FakeOwner.commands) == 1
    assert not any('--resume-checkpoint' in command for command in FakeOwner.commands)
    assert result['tree_closed'] is True
