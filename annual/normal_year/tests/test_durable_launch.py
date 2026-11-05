"""Handoff-only tests: no IES imports, solver calls or scientific trajectories."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

import pytest


PACKAGE = Path(__file__).resolve().parents[1]


def load_module(path, name):
    assert path.is_file(), "The explicitly admitted durable launcher has not been implemented"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def launch_spec(tmp_path, seconds):
    return dict(schema='hospital_ies.durable_launch.v1', command=[sys.executable, '-c', 'pass'],
        cwd=str(tmp_path.resolve()), run_directory='E:/handoff_toy/supervisor', max_seconds=seconds,
        stop_file='E:/handoff_toy/runtime/STOP', cooperative_stop_files=[], grace_seconds=60)


def test_moved_durable_launcher_binds_package_relative_unchanged_helper(tmp_path):
    original = PACKAGE / 'scripts/durable_launch.py'
    assert original.is_file(), "Portable durable supervisor is missing"
    moved = tmp_path / 'moved package/scripts'
    (moved / 'vendor').mkdir(parents=True)
    shutil.copy2(original, moved / 'durable_launch.py')
    shutil.copy2(PACKAGE / 'scripts/vendor/owned_process_hidden.py', moved / 'vendor/owned_process_hidden.py')
    module = load_module(moved / 'durable_launch.py', 'moved_durable')
    assert module.OWNED == (moved / 'vendor/owned_process_hidden.py').resolve()
    assert module.sha(module.OWNED) == module.OWNED_SHA == '715fd828b471b34f3187dbebaade9c52126d26cf6c9690c72d0ceac37e468dca'


@pytest.mark.parametrize('seconds', [259201, 1209600, 2592000])
def test_finite_supervisor_spec_supports_more_than_72_hours(tmp_path, seconds):
    module = load_module(PACKAGE / 'scripts/durable_launch.py', 'duration_durable')
    assert module.validate_spec(launch_spec(tmp_path, seconds))['max_seconds'] == seconds


@pytest.mark.parametrize('seconds', [2592001, float('inf'), float('nan'), 0, True])
def test_supervisor_spec_rejects_over_30_days_or_unknown_bounds(tmp_path, seconds):
    module = load_module(PACKAGE / 'scripts/durable_launch.py', 'bounds_durable')
    with pytest.raises(ValueError, match='max_seconds'):
        module.validate_spec(launch_spec(tmp_path, seconds))


def test_outer_ram_guard_keeps_16_gb_for_other_server_work():
    module = load_module(PACKAGE / 'scripts/durable_launch.py', 'guard_durable')
    assert module.guard_reason(1, 15_999_999_999, 40_000_000_000, 1, 2592000, False) == 'available_ram_limit'
    assert module.guard_reason(1, 16_000_000_000, 40_000_000_000, 1, 2592000, False) is None


def test_no_site_bootstrap_prefers_the_executable_venv_without_global_changes(tmp_path):
    module = load_module(PACKAGE / 'scripts/durable_launch.py', 'bootstrap_durable')
    venv = tmp_path / 'isolated environment'
    (venv / 'Scripts').mkdir(parents=True)
    (venv / 'pyvenv.cfg').write_text('home = C:/Python310\n', 'utf8')
    paths = module.bootstrap_paths(venv / 'Scripts/python.exe', tmp_path / 'base')
    assert paths == [str(venv / 'Lib/site-packages'), str(tmp_path / 'base/Lib/site-packages')]
    assert module.bootstrap_paths(tmp_path / 'base/python.exe', tmp_path / 'base') == [str(tmp_path / 'base/Lib/site-packages')]


def test_launch_refuses_missing_admission_before_writing_or_spawning(tmp_path, monkeypatch):
    module = load_module(PACKAGE / 'launch_server.py', 'admitted_launch')
    calls = []
    def refuse(*args):
        raise ValueError('Current server admission is missing')
    monkeypatch.setattr(module, 'require_admission', refuse)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: calls.append((a, k)))
    with pytest.raises(ValueError, match='admission is missing'):
        module.launch(tmp_path)
    assert not calls
    assert not list(tmp_path.iterdir())


def test_admitted_launch_uses_exact_inner_command_and_runtime_stop(tmp_path, monkeypatch):
    module = load_module(PACKAGE / 'launch_server.py', 'bound_launch')
    admission = dict(limits=dict(max_wall_seconds=1209600, grace_seconds=60), binding=dict(source_commit='test', manifest_sha256='test_manifest'))
    (tmp_path / 'admission').mkdir()
    (tmp_path / 'admission/ADMISSION.json').write_text(json.dumps(admission), 'utf8')
    monkeypatch.setattr(module, 'require_admission', lambda *args: admission)
    monkeypatch.setattr(module, 'require_launcher_probe', lambda *args: dict(status='passed'))
    captured = []
    class Result:
        returncode = 0
        stdout = '{"authenticated_start": true}'
        stderr = ''
    def run(command, **kwargs):
        captured.append((command, kwargs))
        return Result()
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.launch(tmp_path) == 0
    spec = json.loads((tmp_path / 'SUPERVISOR_SPEC.json').read_text('utf8'))
    assert spec['command'] == [sys.executable, '-B', str(PACKAGE / 'server_runner.py'), 'run', '--work-root', str(tmp_path.resolve())]
    assert spec['cwd'] == str(PACKAGE)
    assert spec['run_directory'] == str(tmp_path / 'supervisor')
    assert spec['stop_file'] == str(tmp_path / 'runtime/STOP')
    assert spec['max_seconds'] == 1209600
    assert set(spec['cooperative_stop_files']) == {str(tmp_path / ('runtime/cases/' + name + '/stop/STOP'))
        for name in ('M_available', 'M_ready', 'C_ready')}
    assert captured[0][0][:4] == [sys.executable, '-S', str(PACKAGE / 'scripts/durable_launch.py'), 'launch']
    assert captured[0][0][-2:] == ['--method', 'wmi']
    binding = json.loads((tmp_path / 'SUPERVISOR_BINDING.json').read_text('utf8'))
    assert binding['admission_binding'] == admission['binding']
    assert binding['helper_sha256'] == '715fd828b471b34f3187dbebaade9c52126d26cf6c9690c72d0ceac37e468dca'
    assert binding['spec_sha256'] == module.sha(tmp_path / 'SUPERVISOR_SPEC.json')


def test_failed_wmi_launch_is_preserved_without_automatic_fallback(tmp_path, monkeypatch):
    module = load_module(PACKAGE / 'launch_server.py', 'no_fallback_launch')
    admission = dict(limits=dict(max_wall_seconds=259201, grace_seconds=60), binding=dict(manifest_sha256='test'))
    (tmp_path / 'admission').mkdir()
    (tmp_path / 'admission/ADMISSION.json').write_text(json.dumps(admission), 'utf8')
    monkeypatch.setattr(module, 'require_admission', lambda *args: admission)
    monkeypatch.setattr(module, 'require_launcher_probe', lambda *args: dict(status='passed'))
    calls = []
    class Result:
        returncode = 7
        stdout = ''
        stderr = 'WMI launch rejected'
    def fail(*args, **kwargs):
        calls.append(args)
        return Result()
    monkeypatch.setattr(module.subprocess, 'run', fail)
    assert module.launch(tmp_path) == 7
    assert len(calls) == 1
    receipt = json.loads((tmp_path / 'SUPERVISOR_LAUNCH_EXIT.json').read_text('utf8'))
    assert receipt['exit_code'] == 7
    assert receipt['automatic_fallback'] is False


def test_missing_server_launcher_probe_blocks_admitted_science_before_spec(tmp_path, monkeypatch):
    module = load_module(PACKAGE / 'launch_server.py', 'probe_gated_launch')
    monkeypatch.setattr(module, 'require_admission', lambda *args: dict(limits={}))
    def missing(*args):
        raise ValueError('Current independent launcher probe is missing')
    monkeypatch.setattr(module, 'require_launcher_probe', missing)
    with pytest.raises(ValueError, match='launcher probe is missing'):
        module.launch(tmp_path)
    assert not list(tmp_path.iterdir())


def test_wmi_probe_cannot_authorize_explicit_breakaway_method(tmp_path, monkeypatch):
    module = load_module(PACKAGE / 'launch_server.py', 'method_bound_launch')
    monkeypatch.setattr(module, 'require_admission', lambda *args: dict(limits={}))
    monkeypatch.setattr(module, 'require_launcher_probe', lambda *args: dict(status='passed', same_user_wmi=True))
    with pytest.raises(ValueError, match='launch method'):
        module.launch(tmp_path, method='breakaway')
    assert not list(tmp_path.iterdir())


def test_finalization_queries_and_empties_job_before_closing_handle(tmp_path, monkeypatch):
    module = load_module(PACKAGE / 'scripts/durable_launch.py', 'queried_job_finalization')
    calls = []
    class Owned:
        job = object()
        def terminate_tree(self):
            calls.append('terminate')
        def close(self):
            calls.append('close')
    remaining = [[987654321], []]
    def members(owned):
        calls.append('query')
        return remaining.pop(0) if remaining else []
    monkeypatch.setattr(module, 'job_members', members, raising=False)
    result = dict(status='exited', exit_code=0)
    module.finalize_receipt(result, Owned(), {}, tmp_path, time.monotonic())
    assert calls[:3] == ['query', 'terminate', 'query']
    assert calls[-1] == 'close'
    assert result['job_membership_verified'] is True
    assert result['job_closed_after_empty'] is True
    assert result['owned_tree_closed'] is True


def test_unknown_job_membership_cannot_be_reported_closed(tmp_path, monkeypatch):
    module = load_module(PACKAGE / 'scripts/durable_launch.py', 'unknown_job_finalization')
    class Owned:
        job = object()
        def terminate_tree(self):
            pass
        def close(self):
            pass
    def unknown(owned):
        raise OSError('injected Job query denied')
    monkeypatch.setattr(module, 'job_members', unknown, raising=False)
    result = dict(status='exited', exit_code=0)
    module.finalize_receipt(result, Owned(), {}, tmp_path, time.monotonic())
    assert result['owned_tree_closed'] is False
    assert result['job_membership_verified'] is False
    assert result['status'] != 'exited'


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows Job membership test')
def test_actual_job_finds_and_closes_child_after_root_exits(tmp_path):
    """Only a toy Python parent and sleeper; no source/ or solver imports."""
    module = load_module(PACKAGE / 'scripts/durable_launch.py', 'actual_parent_exit_job')
    go, child_file = tmp_path / 'GO', tmp_path / 'CHILD.json'
    code = ("import json,os,subprocess,sys,time\nfrom pathlib import Path\nimport psutil\n"
        "while not Path(sys.argv[1]).exists(): time.sleep(.01)\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        "Path(sys.argv[2]).write_text(json.dumps(dict(pid=child.pid,create_time=psutil.Process(child.pid).create_time())))\n")
    owned = None
    try:
        with (tmp_path / 'root.stdout.log').open('w') as out, (tmp_path / 'root.stderr.log').open('w') as err:
            owned = module.owned_class()([sys.executable, '-c', code, str(go), str(child_file)],
                cwd=str(tmp_path), env=dict(os.environ), stdout=out, stderr=err)
            go.write_text('Job assignment has completed\n', 'utf8')
            assert owned.proc.wait(timeout=5) == 0
            child = json.loads(child_file.read_text('utf8'))
            assert module.alive(child)
            assert child['pid'] in module.job_members(owned)
            result = dict(status='exited', exit_code=0)
            module.finalize_receipt(result, owned, {}, tmp_path, time.monotonic())
            assert result['residual_job_terminated'] is True
            assert result['job_membership_verified'] is True
            assert result['job_closed_after_empty'] is True
            assert result['job_remaining_pids'] == []
            assert result['owned_tree_closed'] is True
            assert not module.alive(child)
    finally:
        if owned is not None and owned.job:
            owned.terminate_tree()
            owned.close()
