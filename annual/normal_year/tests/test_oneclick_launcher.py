"""Pure launcher tests: never import an IES environment or run science."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]

def load():
    spec = importlib.util.spec_from_file_location("oneclick_entry", ROOT / "start_three_parallel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_plan_uses_python_and_keeps_all_gates(tmp_path):
    m = load()
    plan = m.build_plan(tmp_path / "work", tmp_path / "frozen", tmp_path / "normal")
    joined = [" ".join(command) for _, command in plan]
    assert all(command[:2] == [sys.executable, "-B"] for _, command in plan)
    assert "launch_server.py" in joined[-1]
    assert any("verify_launcher.py" in text for text in joined)
    if m.KIND == "normal_year":
        assert any("suite" in command for _, command in plan)
        assert any("license" in command and command[command.index("--workers")+1] == "3"
                   for _, command in plan)
        assert any("short" in command for _, command in plan)
        assert any("admit" in command for _, command in plan)
    else:
        assert "freeze_calendar.py" in joined[1]
        assert any("validate" in command and "--limits-file" in command for _, command in plan)
        assert any("check-admission" in command for _, command in plan)
        assert not any("license" in command for _, command in plan)

def test_limits_preserve_guards_and_request_three_producers():
    m = load()
    limits = m.three_limits()
    assert (limits["max_producers"], limits["max_accounts"], limits["max_total_processes"]) == (3, 1, 4)
    assert limits["threads_per_producer"] == 1
    assert limits["initial_free_disk_required_bytes"] == 600000000000
    assert limits["max_recoveries_global"] == 1

def test_dry_run_never_starts_process_or_creates_output(tmp_path, monkeypatch, capsys):
    m = load()
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: pytest.fail("dry run launched process"))
    work = tmp_path / "new_work"
    assert m.main(["--dry-run", "--work-root", str(work), "--no-pause"]) == 0
    assert not work.exists()
    assert "launch_server.py" in capsys.readouterr().out

def test_existing_work_is_never_overwritten(tmp_path):
    m = load()
    work = tmp_path / "existing"
    work.mkdir()
    with pytest.raises(FileExistsError):
        m.execute(work, tmp_path / "frozen", tmp_path / "normal")

def test_failed_gate_stops_before_launch_and_records_real_exit(tmp_path, monkeypatch):
    m = load()
    monkeypatch.setattr(m, "validate_locations", lambda *a: None)
    calls = []
    def failed(command, **kwargs):
        calls.append(command)
        assert kwargs.get("shell", False) is False
        assert kwargs["cwd"] == str(ROOT)
        assert kwargs["env"]["OMP_NUM_THREADS"] == "1"
        kwargs["stdout"].write("actual fake gate failure\n")
        return subprocess.CompletedProcess(command, 17)
    monkeypatch.setattr(m.subprocess, "run", failed)
    work = tmp_path / "new_work"
    assert m.execute(work, tmp_path / "frozen", tmp_path / "normal") == 17
    assert len(calls) == 1
    receipt = json.loads((work / "oneclick/RESULT.json").read_text(encoding="utf-8"))
    assert receipt["exit_code"] == 17 and receipt["status"] == "failed"
    assert receipt["annual_completion_claimed"] is False
    assert (work / "oneclick/01_verify.log").read_text(encoding="utf-8").strip() == "actual fake gate failure"

def test_busy_requires_reference_before_any_write(tmp_path):
    m = load()
    if m.KIND != "busy_year":
        pytest.skip("busy-specific requirement")
    with pytest.raises(ValueError, match="reference"):
        m.execute(tmp_path / "work", tmp_path / "frozen", None)
    assert not (tmp_path / "work").exists()

def test_success_records_three_limits_and_only_launch_success(tmp_path, monkeypatch):
    m = load()
    monkeypatch.setattr(m, "validate_locations", lambda *a: None)
    commands = []
    def success(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(m.subprocess, "run", success)
    work = tmp_path / "work"
    assert m.execute(work, tmp_path / "frozen", tmp_path / "normal") == 0
    limits = json.loads((work / "limits.json").read_text(encoding="utf-8"))
    assert (limits["max_producers"], limits["max_accounts"], limits["max_total_processes"]) == (3, 1, 4)
    assert Path(commands[-1][2]).name == "launch_server.py"
    receipt = json.loads((work / "oneclick/RESULT.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "launch_command_succeeded"
    assert receipt["annual_completion_claimed"] is False
    events = (work / "oneclick/COMMANDS.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == len(commands)

def test_work_inside_package_is_rejected_before_scientific_steps(monkeypatch):
    m = load()
    monkeypatch.setattr(m.sys, "platform", "win32")
    with pytest.raises(ValueError):
        m.validate_locations(ROOT / "unsafe_work", ROOT / "calendar", ROOT)


