"""Small receipt fixtures only; synthetic receipts never grant science admission."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest

PACKAGE = Path(__file__).resolve().parents[1]


def load():
    path = PACKAGE / 'scripts/verify_launcher.py'
    assert path.is_file(), 'The independent-launcher server gate is missing'
    spec = importlib.util.spec_from_file_location('launcher_probe_gate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_launcher_receipt_is_not_admission(tmp_path):
    module = load()
    with pytest.raises(ValueError, match='launcher probe'):
        module.require_launcher_probe(PACKAGE, tmp_path)


def receipt(module, tmp_path):
    """The old summary-only fixture was invalid: bind all actual raw proof roles."""
    module.alive = lambda item: False  # Fixture identities never refer to real processes.
    cases = {}
    for name in ('natural', 'cooperative', 'forced'):
        folder = tmp_path / (name + '_raw')
        folder.mkdir()
        paths = {role: folder / (role + ('.jsonl' if role.endswith('heartbeat') else '.json'))
            for role in module.RAW_ROLES}
        spec = dict(schema='hospital_ies.durable_launch.v1', command=[sys.executable, '-c', 'pass'],
            cwd=str(PACKAGE), run_directory=str(folder), max_seconds=5, grace_seconds=1,
            stop_file=str(folder / 'STOP'), cooperative_stop_files=[])
        supervisor_command = [sys.executable, '-S', str(PACKAGE / 'scripts/durable_launch.py'),
            'supervise', '--spec', str(paths['spec'])]
        launcher_command = [sys.executable, '-S', str(PACKAGE / 'scripts/durable_launch.py'),
            'launch', '--method', 'wmi', '--spec', str(paths['request_spec'])]
        def identity(pid, command):
            return dict(pid=pid, create_time=100., command_sha256=module.command_sha(command),
                observed_command_sha256=module.command_sha(command))
        supervisor, driver = identity(90000001, supervisor_command), identity(90000002, spec['command'])
        leaf = dict(pid=90000003, create_time=100.)
        anchor = time.time() - 10
        parent = dict(schema='hospital_ies.known_parent_job_close.v1', epoch=anchor + 1,
            launcher_identity=identity(90000004, launcher_command), launcher_exit_code=0,
            launcher_identity_still_alive=False, owned_job_closed=True, method='wmi')
        paths['spec'].write_text(json.dumps(spec), 'utf8')
        paths['request_spec'].write_text(json.dumps(spec), 'utf8')
        bindings = dict(supervisor_identity=supervisor, command_sha256=module.command_sha(spec['command']),
            source_sha256=module.sha(PACKAGE / 'scripts/durable_launch.py'),
            owned_process_sha256=module.sha(PACKAGE / 'scripts/vendor/owned_process_hidden.py'),
            specfile_sha256=module.sha(paths['spec']))
        process = dict(bindings, schema='hospital_ies.durable_process_receipt.v1', status='exited',
            driver_identity=driver, started_epoch=anchor, finished_epoch=anchor + 5,
            exit_code=1 if name == 'forced' else 0, stop_reason={'natural': None,
                'cooperative': 'explicit_stop', 'forced': 'wall_limit'}[name],
            owned_tree_closed=True, remaining_owned_identities=[], force_killed=name == 'forced',
            job_membership_verified=True, job_closed_after_empty=True, job_remaining_pids=[],
            job_query_samples=[dict(epoch=anchor + 4, pids=[])])
        values = dict(process=process, parent=parent, start=bindings, driver=driver, leaf=leaf,
            launch=dict(schema='hospital_ies.durable_launch_receipt.v1', method='wmi',
                same_windows_user=True, supervisor_identity=supervisor, command=supervisor_command),
            command_exit=dict(command=[sys.executable, '-B', str(PACKAGE / 'scripts/parent_job_probe.py'),
                '--spec', str(paths['request_spec']), '--method', 'wmi'], exit_code=0),
            wmi=dict(exit_code=0, stdout=json.dumps(dict(ReturnValue=0, ProcessId=supervisor['pid']))))
        for role, value in values.items():
            paths[role].write_text(json.dumps(value), 'utf8')
        for role, pid in (('supervisor_heartbeat', supervisor['pid']), ('driver_heartbeat', driver['pid']),
                          ('leaf_heartbeat', leaf['pid'])):
            paths[role].write_text(''.join(json.dumps(dict(epoch=anchor + offset, pid=pid)) + '\n'
                for offset in (2, 3)), 'utf8')
        roles = {role: dict(path=path.relative_to(tmp_path).as_posix(), sha256=module.sha(path))
            for role, path in paths.items()}
        artifact = tmp_path / (name + '.json')
        artifact.write_text(json.dumps(dict(case=name, status='passed', parent_job_closed=True,
            post_parent_close_heartbeats=2, owned_tree_closed=True,
            force_killed=name == 'forced', artifacts=list(roles.values()), raw_roles=roles)), 'utf8')
        cases[name] = dict(path=artifact.name, sha256=module.sha(artifact))
    value = dict(status='passed', kind='normal_year_independent_launcher_probe',
        created_utc=datetime.now(timezone.utc).isoformat(), binding=module.binding(PACKAGE), cases=cases,
        launch_method='wmi', same_user_wmi=True)
    folder = tmp_path / 'validation'
    folder.mkdir()
    (folder / 'LAUNCHER_PROBE.json').write_text(json.dumps(value), 'utf8')
    return value


def test_probe_is_bound_to_current_host_and_every_launcher_tool(tmp_path):
    module = load()
    value = receipt(module, tmp_path)
    assert module.require_launcher_probe(PACKAGE, tmp_path)['status'] == 'passed'
    value['binding']['host']['hostname'] = 'another-server'
    (tmp_path / 'validation/LAUNCHER_PROBE.json').write_text(json.dumps(value), 'utf8')
    with pytest.raises(ValueError, match='binding'):
        module.require_launcher_probe(PACKAGE, tmp_path)


def test_probe_refuses_changed_raw_case_receipt(tmp_path):
    module = load()
    receipt(module, tmp_path)
    (tmp_path / 'forced.json').write_text('{}', 'utf8')
    with pytest.raises(ValueError, match='artifact'):
        module.require_launcher_probe(PACKAGE, tmp_path)


def test_probe_expires_after_finite_30_day_window(tmp_path):
    module = load()
    value = receipt(module, tmp_path)
    value['created_utc'] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    (tmp_path / 'validation/LAUNCHER_PROBE.json').write_text(json.dumps(value), 'utf8')
    with pytest.raises(ValueError, match='expired'):
        module.require_launcher_probe(PACKAGE, tmp_path)


def test_summary_without_raw_roles_cannot_authorize_a_launcher(tmp_path):
    module = load()
    value = receipt(module, tmp_path)
    case_path = tmp_path / 'natural.json'
    case = json.loads(case_path.read_text('utf8'))
    case['artifacts'] = []
    case['raw_roles'] = {}
    case_path.write_text(json.dumps(case), 'utf8')
    value['cases']['natural']['sha256'] = module.sha(case_path)
    (tmp_path / 'validation/LAUNCHER_PROBE.json').write_text(json.dumps(value), 'utf8')
    with pytest.raises(ValueError, match='raw'):
        module.require_launcher_probe(PACKAGE, tmp_path)


@pytest.mark.parametrize('method', [None, 'breakaway'])
def test_top_level_launch_method_must_be_explicit_wmi(tmp_path, method):
    module = load()
    value = receipt(module, tmp_path)
    if method is None:
        value.pop('launch_method')
    else:
        value['launch_method'] = method
    (tmp_path / 'validation/LAUNCHER_PROBE.json').write_text(json.dumps(value), 'utf8')
    with pytest.raises(ValueError, match='method'):
        module.require_launcher_probe(PACKAGE, tmp_path)


@pytest.mark.parametrize('role,field,bad', [('process', 'job_membership_verified', False),
    ('process', 'job_remaining_pids', [90000003]), ('process', 'remaining_owned_identities', [{}]),
    ('process', 'exit_code', 7), ('parent', 'owned_job_closed', False),
    ('parent', 'launcher_identity_still_alive', True), ('parent', 'method', 'breakaway')])
def test_rehashed_raw_failure_is_rejected_even_when_summary_says_passed(tmp_path, role, field, bad):
    module = load()
    value = receipt(module, tmp_path)
    case_path = tmp_path / 'natural.json'
    case = json.loads(case_path.read_text('utf8'))
    row = case['raw_roles'][role]
    path = tmp_path / row['path']
    raw = json.loads(path.read_text('utf8'))
    raw[field] = bad
    path.write_text(json.dumps(raw), 'utf8')
    row['sha256'] = module.sha(path)
    for artifact in case['artifacts']:
        if artifact['path'] == row['path']:
            artifact['sha256'] = row['sha256']
    case_path.write_text(json.dumps(case), 'utf8')
    value['cases']['natural']['sha256'] = module.sha(case_path)
    (tmp_path / 'validation/LAUNCHER_PROBE.json').write_text(json.dumps(value), 'utf8')
    with pytest.raises(ValueError, match='raw'):
        module.require_launcher_probe(PACKAGE, tmp_path)
