"""Focused isolated recovery orchestration tests; never fit or simulate."""
import copy
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

HELPER = Path(__file__).resolve().parents[1] / 'outputs/v43_control_improvement_20261014_run01/learning/recovery_continue_v8.py'


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def module():
    assert HELPER.is_file(), 'isolated v8 continuation helper has not been implemented'
    spec = importlib.util.spec_from_file_location('recovery_downstream_v8_test', HELPER)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def case(tmp_path, module, monkeypatch):
    root = tmp_path
    base = root / 'outputs/run'
    learning = base / 'learning'
    main = learning / 'MAIN_BRANCHES_06'
    coverage = learning / 'TRAIN_COVERAGE_RECOVERED_08'
    actual = learning / 'MISSING3_ACTUAL_08'
    recovery = learning / 'RECOVERY_08'
    helper = recovery / 'coverage_recovery_v8.py'
    dump(helper, {})
    ref = lambda path: {'path': path.relative_to(root).as_posix(), 'sha256': sha(path)}
    snapshots = [{'scenario_id': f's{i:03d}', 'snapshot_minute': 75.0,
                  'experiment_role': 'pilot_validation' if 72 <= i < 96 else 'pilot_train',
                  'group_id': f'g{i:03d}'} for i in range(168)]
    source = {'frozen.py': 'abc'}
    plans = []
    reports = []
    for directory, ids, name in [(main, range(96), 'MAIN_BRANCH_PLAN_06.json'),
                                  (coverage, range(96, 168), 'TRAIN_COVERAGE_BRANCH_PLAN_07.json')]:
        plan = {'signature': name, 'snapshots': [snapshots[i] for i in ids],
                'source_sha256': source, 'prototype_train_only': False}
        dump(learning / name, plan)
        dump(directory / 'registered_plan.json', plan)
        records = []
        for index, i in enumerate(ids):
            folder = (directory / f'snapshot_{index:03d}' if directory == main else
                      (learning / 'TRAIN_COVERAGE_BRANCHES_07' / f'snapshot_{index:03d}' if index < 69
                       else actual / f'snapshot_{index-69:03d}'))
            dump(folder / 'raw.json', {'unchanged': i})
            unknown = i == 0
            result = dict(scenario_id=snapshots[i]['scenario_id'], snapshot_minute=75.0,
                          role=snapshots[i]['experiment_role'], group_id=snapshots[i]['group_id'],
                          source_sha256=source, passed=not unknown, D0_passed=not unknown, capture_vs_registered_passed=True,
                          truth_integrity_passed=True, capture_attempts=1,
                          continuation_attempts=0 if unknown else 9,
                          current_planning_queries=0 if unknown else 9,
                          unknown_outcomes=0, unknown_snapshot=unknown,
                          reason='snapshot_not_reached' if unknown else None,
                          input_artifacts={'raw.json': sha(folder / 'raw.json')}, outcomes=[], fit_rows=[])
            dump(folder / 'BRANCH_RESULT.json', result)
            records.append(dict(directory=Path(os.path.relpath(folder, directory)).as_posix(), result_sha256=sha(folder / 'BRANCH_RESULT.json'),
                                **{k: result[k] for k in ('scenario_id', 'snapshot_minute', 'passed',
                                   'capture_attempts', 'continuation_attempts', 'current_planning_queries',
                                   'unknown_outcomes', 'reason', 'truth_integrity_passed')},
                                unreached_snapshot=unknown))
        report = dict(plan_signature=plan['signature'], snapshots_registered=len(records),
                      snapshots_attempted=len(records), results=records, truth_integrity_passed=True,
                      all_reached_D0_passed=True, all_D0_passed=not any(r['unreached_snapshot'] for r in records),
                      unreached_snapshots=sum(r['unreached_snapshot'] for r in records),
                      prototype_train_only=False,
                      **{k: sum(r[k] for r in records) for k in ('capture_attempts', 'continuation_attempts', 'current_planning_queries')})
        dump(directory / 'ACTION_BRANCH_REPORT.json', report)
        plans.append(plan)
        reports.append(report)
    combined = dict(plans[0], signature='combined', snapshots=snapshots)
    dump(learning / 'COMBINED_BRANCH_PLAN_07.json', combined)
    missing_plan = recovery / 'MISSING_BRANCH_PLAN_08.json'
    dump(missing_plan, dict(plans[1], signature='missing3', snapshots=snapshots[-3:]))
    dump(actual / 'registered_plan.json', json.loads(missing_plan.read_text()))
    actual_report = copy.deepcopy(reports[1])
    actual_report.update(snapshots_registered=3, snapshots_attempted=3, results=copy.deepcopy(reports[1]['results'][-3:]))
    for i, item in enumerate(actual_report['results']): item['directory'] = f'snapshot_{i:03d}'
    dump(actual / 'ACTION_BRANCH_REPORT.json', actual_report)
    registration = recovery / 'RECOVERY_REGISTRATION.json'
    ledger = recovery / 'ledger.json'
    interrupted = recovery / 'interrupted.json'
    for path in (registration, ledger, interrupted):
        dump(path, {'old_started_D0_is_unknown': True})
    branch_command = base / 'logs/34_v8_actual_missing3_branches.command.json'
    branch_args = ['-m', 'tools.control_improvement.run', 'branches',
                   missing_plan.relative_to(root).as_posix(), actual.relative_to(root).as_posix()]
    dump(branch_command, dict(name='34_v8_actual_missing3_branches', command=[sys.executable] + branch_args,
                             cwd=str(root), exit_code=0, finished_utc='2026-10-14T12:00:00+00:00'))
    provenance = dict(schema='control_improvement.coverage_recovery_view.v1',
                      registration=ref(registration), actual_branch_command=ref(branch_command),
                      actual_branches={'path': actual.relative_to(root).as_posix(),
                                       'registered_plan_sha256': sha(actual / 'registered_plan.json'),
                                       'report_sha256': sha(actual / 'ACTION_BRANCH_REPORT.json')},
                      original_parent_directory=(learning / 'TRAIN_COVERAGE_BRANCHES_07').relative_to(root).as_posix(),
                      original_plan=ref(learning / 'TRAIN_COVERAGE_BRANCH_PLAN_07.json'),
                      original_ledger=ref(ledger), interrupted_attempts=ref(interrupted),
                      result_mapping=[dict(original_index=i,
                                           source_directory=(coverage / r['directory']).resolve().relative_to(root).as_posix(),
                                           result_sha256=r['result_sha256'], is_reused=i < 69)
                                      for i, r in enumerate(reports[1]['results'])])
    dump(coverage / 'RECOVERY_PROVENANCE.json', provenance)
    reports[1].update(recovery_provenance_file='RECOVERY_PROVENANCE.json',
                      recovery_provenance_sha256=sha(coverage / 'RECOVERY_PROVENANCE.json'))
    dump(coverage / 'ACTION_BRANCH_REPORT.json', reports[1])
    stitch_command = base / 'logs/35_v8_stitch_coverage72.command.json'
    stitch_args = [helper.relative_to(root).as_posix(), 'stitch', '--registration', registration.relative_to(root).as_posix(),
                   '--actual-branches', actual.relative_to(root).as_posix(), '--command-record', branch_command.relative_to(root).as_posix(),
                   '--out', coverage.relative_to(root).as_posix()]
    dump(stitch_command, dict(name='35_v8_stitch_coverage72', command=[sys.executable] + stitch_args,
                             cwd=str(root), exit_code=0, finished_utc='2026-10-14T12:00:01+00:00'))
    dump(learning / 'PROTOTYPE_CLOSED_PLAN_05.json', {'bc_directory': 'frozen_bc'})
    monkeypatch.setattr(module, '_validated_plan', lambda path: json.loads(Path(path).read_text()))
    # Actual artifact hash verification remains exercised; physical-manifest verification is the only stub.
    return dict(root=root, base=base, main=main, coverage=coverage, command=stitch_command,
                branch_command=branch_command, provenance=provenance, reports=reports)


def refresh_provenance(c):
    dump(c['coverage'] / 'RECOVERY_PROVENANCE.json', c['provenance'])
    report = json.loads((c['coverage'] / 'ACTION_BRANCH_REPORT.json').read_text())
    report['recovery_provenance_sha256'] = sha(c['coverage'] / 'RECOVERY_PROVENANCE.json')
    dump(c['coverage'] / 'ACTION_BRANCH_REPORT.json', report)


def test_union_references_all_168_and_retains_unknown_and_provenance(module, case):
    before = sha(case['main'] / 'snapshot_000/BRANCH_RESULT.json')
    out = module.prepare_combined(case['root'], case['base'], case['coverage'], case['command'])
    report = json.loads((out / 'ACTION_BRANCH_REPORT.json').read_text())
    assert len(report['results']) == 168
    assert report['unreached_snapshots'] == 1 and report['all_D0_passed'] is False
    assert report['results'][0]['unreached_snapshot'] is True
    assert report['recovery_provenance']['sha256'] == sha(case['coverage'] / 'RECOVERY_PROVENANCE.json')
    assert (out / report['results'][0]['directory']).resolve() == case['main'] / 'snapshot_000'
    assert before == sha(case['main'] / 'snapshot_000/BRANCH_RESULT.json')


@pytest.mark.parametrize('damage', ['missing_row', 'duplicate_row', 'bad_role', 'bad_raw', 'false_truth', 'wrong_mapping'])
def test_rejects_incomplete_or_changed_evidence_before_writing(module, case, damage):
    p = case['coverage'] / 'ACTION_BRANCH_REPORT.json'
    report = json.loads(p.read_text())
    if damage == 'missing_row': report['results'].pop()
    if damage == 'duplicate_row': report['results'][1] = copy.deepcopy(report['results'][0])
    if damage == 'bad_role':
        raw = case['coverage'] / report['results'][0]['directory'] / 'BRANCH_RESULT.json'
        value = json.loads(raw.read_text()); value['role'] = 'pilot_validation'; dump(raw, value)
        report['results'][0]['result_sha256'] = sha(raw)
    if damage == 'bad_raw': dump(case['coverage'] / report['results'][0]['directory'] / 'raw.json', {'tampered': 1})
    if damage == 'false_truth': report['truth_integrity_passed'] = False
    if damage == 'wrong_mapping':
        case['provenance']['result_mapping'][0]['original_index'] = 1
        refresh_provenance(case)
        report = json.loads(p.read_text())
    dump(p, report)
    with pytest.raises((ValueError, FileNotFoundError)):
        module.prepare_combined(case['root'], case['base'], case['coverage'], case['command'])
    assert not (case['base'] / 'learning/COMBINED_BRANCHES_08').exists()


@pytest.mark.parametrize('target,damage', [('stitch', 'failed'), ('stitch', 'wrong_output'), ('branch', 'failed'), ('branch', 'wrong_output')])
def test_both_actual_terminal_commands_must_bind_inputs(module, case, target, damage):
    path = case['command'] if target == 'stitch' else case['branch_command']
    record = json.loads(path.read_text())
    if damage == 'failed': record['exit_code'] = 1
    else: record['command'][-1] = 'somewhere_else'
    dump(path, record)
    if target == 'branch':
        case['provenance']['actual_branch_command']['sha256'] = sha(path)
        refresh_provenance(case)
    with pytest.raises(ValueError):
        module.prepare_combined(case['root'], case['base'], case['coverage'], case['command'])


@pytest.mark.parametrize('collision', ['logs/36_v8_actual_combined_rank_fit.stderr.log', 'learning/COMBINED_RANK_FIT_08'])
def test_log_or_output_collision_blocks_every_child(module, case, monkeypatch, collision):
    calls = []
    monkeypatch.setattr(module, '_run_logged', lambda *args: calls.append(args))
    sentinel = case['base'] / collision
    sentinel.write_text('retain me')
    with pytest.raises(FileExistsError):
        module.continue_downstream(case['root'], case['base'], case['coverage'], case['command'])
    assert calls == [] and sentinel.read_text() == 'retain me'
    assert not (case['base'] / 'learning/COMBINED_BRANCHES_08').exists()


def test_fit_failure_stops_before_binding(module, case, monkeypatch):
    calls = []
    def fail(root, base, name, args):
        calls.append(name)
        raise RuntimeError('actual fit exit 7')
    monkeypatch.setattr(module, '_run_logged', fail)
    with pytest.raises(RuntimeError, match='exit 7'):
        module.continue_downstream(case['root'], case['base'], case['coverage'], case['command'])
    assert calls == ['36_v8_actual_combined_rank_fit']


def test_planned_commands_keep_fixed_seeds_protocols_and_v8_outputs(module, case):
    stages = module.command_stages(case['root'], case['base'])
    assert [name for name, args in stages] == [
        '36_v8_actual_combined_rank_fit', '37_v8_bind_original_dev60', '38_v8_bind_registered_sensitivity',
        '39_v8_actual_frozen_development480', '40_v8_actual_sensitivity192']
    assert stages[0][1][:3] == ['-m', 'tools.control_improvement.run', 'fit']
    assert 'NEW_DEV60_PROTOCOL_06.json' in ' '.join(stages[1][1])
    assert 'RF_seed4101' in ' '.join(stages[2][1])
    assert 'REGISTRATION_02' in ' '.join(stages[2][1])
    assert stages[3][1][-1].endswith('NEW_DEV60_CLOSED_08')
    assert stages[4][1][-1].endswith('ACTUAL_08')


def test_keep_awake_restores_after_failure_and_acquisition_failure_blocks_body(module):
    calls = []
    def setter(flags): calls.append(flags); return 1
    with pytest.raises(RuntimeError, match='body'):
        with module.keep_awake(True, setter=setter): raise RuntimeError('body')
    assert calls == [0x80000001, 0x80000000]
    entered = False
    with pytest.raises(RuntimeError, match='keep-awake'):
        with module.keep_awake(True, setter=lambda flags: 0): entered = True
    assert not entered


def test_parallel_waits_for_both_even_when_first_failed(module, tmp_path, monkeypatch):
    waited = []
    class Fake:
        def __init__(self, code): self.code = code
        def wait(self): waited.append(self.code); return self.code
    processes = iter([Fake(9), Fake(0)])
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **kw: next(processes))
    monkeypatch.setattr(module, '_verify_finished_command', lambda *a: None)
    with pytest.raises(RuntimeError, match='9'):
        module._run_parallel(tmp_path, tmp_path, [('a', []), ('b', [])])
    assert waited == [9, 0]


def synthetic_fit(c):
    rank = c['base'] / 'learning/COMBINED_RANK_FIT_08'
    binding = {'branch_report_sha256': sha(c['base'] / 'learning/COMBINED_BRANCHES_08/ACTION_BRANCH_REPORT.json'),
               'prototype_train_only': False}
    models = []
    for seed in (4101, 4102, 4103):
        directory = rank / f'RF_seed{seed}'
        dump(directory / 'synthetic_artifact.json', {'synthetic': seed})
        metadata = {'seed': seed, 'source_binding': binding,
                    'artifacts': {'synthetic_artifact.json': sha(directory / 'synthetic_artifact.json')}}
        dump(directory / 'metadata.json', metadata)
        models.append({'seed': seed, 'directory': directory.name, 'metadata_sha256': sha(directory / 'metadata.json')})
    dump(rank / 'FIT_REPORT.json', {'actual_fits': 3, 'models': models, 'source_binding': binding})


@pytest.mark.parametrize('damage', ['wrong_seed', 'changed_artifact', 'wrong_combined_report'])
def test_fit_gate_requires_all_three_bound_seed_artifacts(module, case, damage):
    combined = module.prepare_combined(case['root'], case['base'], case['coverage'], case['command'])
    synthetic_fit(case)
    rank = case['base'] / 'learning/COMBINED_RANK_FIT_08'
    if damage == 'changed_artifact':
        dump(rank / 'RF_seed4103/synthetic_artifact.json', {'tampered': True})
    elif damage == 'wrong_seed':
        path = rank / 'RF_seed4103/metadata.json'; value = json.loads(path.read_text()); value['seed'] = 4101; dump(path, value)
        report = json.loads((rank / 'FIT_REPORT.json').read_text()); report['models'][2]['metadata_sha256'] = sha(path); dump(rank / 'FIT_REPORT.json', report)
    else:
        report = json.loads((rank / 'FIT_REPORT.json').read_text()); report['source_binding']['branch_report_sha256'] = 'other'; dump(rank / 'FIT_REPORT.json', report)
    with pytest.raises(ValueError): module._verify_fit(case['base'], combined)


def test_successful_fake_children_follow_gates_and_awake_only_around_children(module, case, monkeypatch):
    calls = []
    @contextmanager
    def awake(enabled):
        assert (case['base'] / 'learning/COMBINED_BRANCHES_08/ACTION_BRANCH_REPORT.json').is_file()
        calls.append(('awake', enabled))
        try: yield
        finally: calls.append(('released', True))
    def execute(root, base, name, args):
        calls.append(name)
        if name.startswith('36_'): synthetic_fit(case)
    def parallel(root, base, jobs):
        calls.extend(name for name, args in jobs)
        dump(base / 'learning/NEW_DEV60_CLOSED_08/CLOSED_LOOP_REPORT.json',
             {'registered_specs': 60, 'trajectory_attempts': 480, 'scores': [{}] * 480})
        dump(base / 'sensitivity/ACTUAL_08/SENSITIVITY_REPORT.json',
             {'actual_episodes': 192, 'registered_episodes': 192, 'scores': [{}] * 192})
    monkeypatch.setattr(module, 'keep_awake', awake)
    monkeypatch.setattr(module, '_run_logged', execute)
    monkeypatch.setattr(module, '_run_parallel', parallel)
    module.continue_downstream(case['root'], case['base'], case['coverage'], case['command'], keep_awake_enabled=True)
    assert calls == [('awake', True)] + [name for name, args in module.command_stages(case['root'], case['base'])] + [('released', True)]


def test_awake_acquisition_failure_launches_no_child(module, case, monkeypatch):
    calls = []
    original = module.keep_awake
    monkeypatch.setattr(module, 'keep_awake', lambda enabled: original(enabled, setter=lambda flags: 0))
    monkeypatch.setattr(module, '_run_logged', lambda *args: calls.append(args))
    with pytest.raises(RuntimeError, match='keep-awake'):
        module.continue_downstream(case['root'], case['base'], case['coverage'], case['command'], keep_awake_enabled=True)
    assert calls == []
