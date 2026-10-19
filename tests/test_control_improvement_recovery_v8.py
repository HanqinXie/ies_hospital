"""Small synthetic artifact tests; no hospital simulation, fit, or solve."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / 'outputs/v43_control_improvement_20261014_run01/learning/RECOVERY_08/coverage_recovery_v8.py'
SOURCE_NAMES = ('__init__.py', 'candidates.py', 'ranking.py', 'models.py', 'runtime.py', 'episode.py', 'branches.py', 'run.py', 'policies.py', 'closed_loop.py')


def module():
    assert HELPER.is_file(), 'Missing isolated recovery implementation'
    spec = importlib.util.spec_from_file_location('coverage_recovery_v8_test', HELPER)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sign(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def events(reached=True):
    result = [{'event': 'started', 'attempt_type': 'snapshot_capture'}, {'event': 'finished', 'attempt_type': 'snapshot_capture', 'status': 'completed'}]
    if reached:
        result += [{'event': e, 'attempt_type': 'D0_reference'} for e in ('started', 'finished')]
        result += [{'event': 'started', 'attempt_type': 'pure_current_candidate_projection'}]
        for kind, count in [('isolated_current_projection', 8), ('original_current_query_comparison', 1), ('current_action_continuation', 8)]:
            for i in range(count):
                result += [{'event': e, 'attempt_type': kind, 'candidate_id': str(i)} for e in ('started', 'finished')]
    return result


def branch(directory, state, sources, reached=True):
    directory.mkdir(parents=True)
    names = ['plan.json', 'reference_capture.json', 'capture_vs_registered.json']
    if reached:
        names += ['execution_snapshot.json', 'D0_comparison.json', 'current_projection_vs_original.json', 'CURRENT_CANDIDATES.json']
    for name in names:
        write(directory / name, {'synthetic': True, 'state': state['scenario_id'], 'file': name})
    outcomes = []
    if reached:
        for i in range(8):
            p = directory / f'candidate_{i}.json'
            write(p, {'synthetic': True, 'candidate': i})
            outcomes.append({'candidate_id': str(i), 'trajectory_file': p.name, 'trajectory_sha256': sha(p), 'status': 'bounded_observation_completed'})
    rows = [{'role': 'train', 'group_id': state['group_id'], 'scenario_id': state['scenario_id'], 'snapshot_minute': state['snapshot_minute'], 'candidate_index': i, 'target_mask': [True] * 19} for i in range(8)] if reached else []
    result = dict(scenario_id=state['scenario_id'], snapshot_minute=state['snapshot_minute'], role=state['experiment_role'], group_id=state['group_id'], source_sha256=sources,
                  passed=reached, truth_integrity_passed=True, unknown_snapshot=not reached, reason=None if reached else 'snapshot_not_reached', capture_attempts=1,
                  continuation_attempts=9 if reached else 0, current_planning_queries=9 if reached else 0, unknown_outcomes=0, D0_passed=reached,
                  capture_vs_registered_passed=True, fit_rows=rows, outcomes=outcomes, input_artifacts={name: sha(directory / name) for name in names})
    write(directory / 'BRANCH_RESULT.json', result)
    (directory / 'attempts.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events(reached)), encoding='utf-8')
    return result


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path
    source = root / 'tools/control_improvement'
    source.mkdir(parents=True)
    for name in SOURCE_NAMES:
        (source / name).write_text('# synthetic source\n', encoding='utf-8')
    sources = {name: sha(source / name) for name in SOURCE_NAMES}
    manifest = root / 'manifest.json'
    write(manifest, {'signature': 'manifest-test'})
    proposal = root / 'proposal.json'
    write(proposal, {'synthetic': True})
    snapshots = [dict(scenario_id=f's{i}', snapshot_minute=75.0 if i % 2 == 0 else 105.0, snapshot_step=5 if i % 2 == 0 else 7, experiment_role='pilot_train', group_id=f'g{i//4}') for i in range(72)]
    plan = dict(schema='control_improvement.branch_plan.v1', manifest_path='manifest.json', manifest_sha256=sha(manifest), manifest_signature='manifest-test', snapshot_plan_path='proposal.json', snapshot_plan_sha256=sha(proposal), source_sha256=sources, snapshots=snapshots,
                maximum_capture_attempts=72, maximum_continuation_attempts=648, maximum_current_planning_queries=648, prototype_train_only=False, continuation_policy='current_rule_projection0', bounded_candidate_horizon_minutes=120, conditional_futures=1, unknowns_remain_null=True)
    plan['signature'] = sign(plan)
    plan_path = root / 'learning/parent_plan.json'
    write(plan_path, plan)
    parent = root / 'learning/old72'
    write(parent / 'registered_plan.json', plan)
    records = []
    for i, state in enumerate(snapshots):
        d = parent / f'snapshot_{i:03d}'
        if i < 69:
            branch(d, state, sources)
        elif i == 69:
            d.mkdir()
            for name in ('plan.json', 'reference_capture.json', 'capture_vs_registered.json', 'execution_snapshot.json'):
                write(d / name, {'synthetic': True})
            partial = events(False) + [{'event': 'started', 'attempt_type': 'D0_reference', 'time_minute': 105.0}]
            (d / 'attempts.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in partial), encoding='utf-8')
        rec = dict(index=i, registered=state, directory_exists=d.exists(), branch_result_exists=(d / 'BRANCH_RESULT.json').exists())
        if d.exists():
            rec['files'] = [dict(path=p.relative_to(root).as_posix(), bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(d.iterdir())]
        if i < 69:
            rec['result_sha256'] = sha(d / 'BRANCH_RESULT.json')
        records.append(rec)
    ledger_path = root / 'review/ledger.json'
    write(ledger_path, dict(passed_persisted_binding_checks=True, errors=[], registered_states=72, plan_signature=plan['signature'], plan_sha256=sha(plan_path), manifest_sha256=sha(manifest), states=records))
    return dict(root=root, parent_plan=plan_path, parent_directory=parent, ledger=ledger_path, out=root / 'learning/registration', plan=plan)


def register(m, f):
    return m.register_recovery(f['parent_plan'], f['parent_directory'], f['ledger'], f['out'], root=f['root'])


def actual_three(f, registration):
    root = f['root']
    missing = root / registration['missing_plan']['path']
    plan = json.loads(missing.read_text(encoding='utf-8'))
    base = root / 'learning/new3'
    write(base / 'registered_plan.json', plan)
    results = []
    for i, state in enumerate(plan['snapshots']):
        d = base / f'snapshot_{i:03d}'
        r = branch(d, state, plan['source_sha256'])
        results.append(dict(directory=d.name, result_sha256=sha(d / 'BRANCH_RESULT.json'), scenario_id=r['scenario_id'], snapshot_minute=r['snapshot_minute'], passed=True, capture_attempts=1, continuation_attempts=9, current_planning_queries=9, unknown_outcomes=0, reason=None, truth_integrity_passed=True, unreached_snapshot=False))
    report = dict(plan_signature=plan['signature'], snapshots_registered=3, snapshots_attempted=3, results=results, capture_attempts=3, continuation_attempts=27, current_planning_queries=27, all_reached_D0_passed=True, all_D0_passed=True, truth_integrity_passed=True, unreached_snapshots=0, prototype_train_only=False)
    write(base / 'ACTION_BRANCH_REPORT.json', report)
    command = root / 'logs/fresh3.command.json'
    write(command, dict(exit_code=0, cwd=str(root), command=['python', '-m', 'tools.control_improvement.run', 'branches', str(missing), str(base)]))
    return base, command


def test_register_only_original_missing_rows_preserves_partial_and_parent_bytes(fixture):
    m = module(); f = fixture
    before = {str(p): sha(p) for p in f['parent_directory'].rglob('*') if p.is_file()}
    r = register(m, f)
    missing = json.loads((f['root'] / r['missing_plan']['path']).read_text())
    assert missing['snapshots'] == f['plan']['snapshots'][69:72]
    assert r['original_indices'] == [69, 70, 71]
    assert r['new_attempt_budget'] == {'capture_attempts': 3, 'continuation_attempts': 27, 'current_planning_queries': 27}
    partial = json.loads((f['root'] / r['interrupted_attempts']['path']).read_text())
    assert partial['completed_capture_attempts'] == 1
    assert partial['unknown_D0_started_attempts'] == 1
    assert partial['original_journal_sha256'] == sha(f['parent_directory'] / 'snapshot_069/attempts.jsonl')
    assert partial['unknown_reason'] == 'process_interrupted_no_terminal_record'
    assert {str(p): sha(p) for p in f['parent_directory'].rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('kind', ['raw', 'lineage', 'gap', 'source', 'plan', 'partial', 'new_result'])
def test_register_rejects_parent_drift_before_writing(fixture, kind):
    m = module(); f = fixture; parent = f['parent_directory']
    if kind == 'raw': (parent / 'snapshot_005/candidate_0.json').write_text('tampered')
    if kind == 'lineage':
        p = parent / 'snapshot_005/BRANCH_RESULT.json'; r = json.loads(p.read_text()); r['group_id'] = 'wrong'; write(p, r)
    if kind == 'gap': (parent / 'snapshot_005/BRANCH_RESULT.json').unlink()
    if kind == 'source': (f['root'] / 'tools/control_improvement/run.py').write_text('changed')
    if kind == 'plan':
        p = parent / 'registered_plan.json'; r = json.loads(p.read_text()); r['snapshots'][0]['scenario_id'] = 'wrong'; write(p, r)
    if kind == 'partial': (parent / 'snapshot_069/attempts.jsonl').write_text('{}\n')
    if kind == 'new_result': write(parent / 'snapshot_069/BRANCH_RESULT.json', {})
    with pytest.raises(ValueError): register(m, f)
    assert not f['out'].exists()


def test_register_refuses_existing_destination(fixture):
    m = module(); f = fixture; f['out'].mkdir()
    with pytest.raises(FileExistsError): register(m, f)


def test_stitch_references_69_plus_3_in_original_order_and_counts_unknown_attempt(fixture):
    m = module(); f = fixture; r = register(m, f); base, command = actual_three(f, r)
    out = f['root'] / 'learning/view'
    report = m.stitch_recovery(f['out'] / 'RECOVERY_REGISTRATION.json', base, command, out, root=f['root'])
    assert (out / 'registered_plan.json').read_bytes() == f['parent_plan'].read_bytes()
    assert [x['scenario_id'] for x in report['results']] == [x['scenario_id'] for x in f['plan']['snapshots']]
    assert len(report['results']) == 72
    assert report['capture_attempts'] == 72 and report['continuation_attempts'] == 648
    for i, entry in enumerate(report['results']):
        expected = f['parent_directory'] / f'snapshot_{i:03d}' if i < 69 else base / f'snapshot_{i-69:03d}'
        assert (out / entry['directory']).resolve() == expected.resolve()
        assert entry['result_sha256'] == sha(expected / 'BRANCH_RESULT.json')
    provenance = json.loads((out / 'RECOVERY_PROVENANCE.json').read_text())
    assert provenance['schema'] == 'control_improvement.coverage_recovery_view.v1'
    assert provenance['actual_branch_command']['sha256'] == sha(command)
    assert provenance['actual_attempt_totals_including_interrupted']['capture_started'] == 73
    assert provenance['actual_attempt_totals_including_interrupted']['continuation_started'] == 649
    assert provenance['actual_attempt_totals_including_interrupted']['continuation_finished'] == 648
    assert provenance['actual_attempt_totals_including_interrupted']['unknown_interrupted_D0'] == 1


@pytest.mark.parametrize('kind', ['command_exit', 'command_output', 'command_plan', 'report_missing', 'new_hash', 'new_role', 'new_journal', 'registration', 'original_after_registration'])
def test_stitch_rejects_missing_or_mismatched_evidence_before_writing(fixture, kind):
    m = module(); f = fixture; r = register(m, f); base, command = actual_three(f, r)
    if kind.startswith('command'):
        j = json.loads(command.read_text())
        if kind == 'command_exit': j['exit_code'] = 1
        if kind == 'command_output': j['command'][-1] = str(f['root'] / 'other')
        if kind == 'command_plan': j['command'][-2] = str(f['parent_plan'])
        write(command, j)
    if kind == 'report_missing': (base / 'ACTION_BRANCH_REPORT.json').unlink()
    if kind == 'new_hash': (base / 'snapshot_001/candidate_0.json').write_text('changed')
    if kind == 'new_role':
        p = base / 'snapshot_001/BRANCH_RESULT.json'; j = json.loads(p.read_text()); j['role'] = 'pilot_validation'; write(p, j)
    if kind == 'new_journal':
        p = base / 'snapshot_001/attempts.jsonl'; p.write_text(p.read_text() + json.dumps({'event': 'started', 'attempt_type': 'unregistered_solver_query'}) + '\n')
    if kind == 'registration':
        p = f['out'] / 'RECOVERY_REGISTRATION.json'; j = json.loads(p.read_text()); j['original_indices'] = [68, 70, 71]; write(p, j)
    if kind == 'original_after_registration': (f['parent_directory'] / 'snapshot_000/BRANCH_RESULT.json').write_text('{}')
    out = f['root'] / 'learning/view'
    with pytest.raises((ValueError, FileNotFoundError)):
        m.stitch_recovery(f['out'] / 'RECOVERY_REGISTRATION.json', base, command, out, root=f['root'])
    assert not out.exists()


@pytest.mark.parametrize('phase,expected_reads', [('register', 1), ('stitch', 0)])
def test_no_redundant_old_large_raw_hash_pass(fixture, monkeypatch, phase, expected_reads):
    m = module(); f = fixture
    if phase == 'stitch':
        registration = register(m, f)
        base, command = actual_three(f, registration)
    original_sha = m.sha
    tracked = (f['parent_directory'] / 'snapshot_000/candidate_0.json').resolve()
    reads = []
    def counted(path):
        if Path(path).resolve() == tracked:
            reads.append(str(path))
        return original_sha(path)
    monkeypatch.setattr(m, 'sha', counted)
    if phase == 'register':
        register(m, f)
    else:
        m.stitch_recovery(f['out'] / 'RECOVERY_REGISTRATION.json', base, command,
                          f['root'] / 'learning/view', root=f['root'])
    assert len(reads) == expected_reads
