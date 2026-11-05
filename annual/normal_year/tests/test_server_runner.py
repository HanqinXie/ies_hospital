"""Supervisor tests use tiny fake payloads, never the IES/Gurobi runtime."""
import importlib.util
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_runner():
    path = ROOT / "server_runner.py"
    assert path.is_file(), "The portable bounded supervisor has not been implemented"
    spec = importlib.util.spec_from_file_location("normal_year_server_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf8")


@pytest.fixture
def runner():
    return load_runner()


@pytest.fixture
def example(tmp_path):
    package = tmp_path / "moved package"
    work = tmp_path / "external work"
    (package / "source/src").mkdir(parents=True)
    (package / "source/tools/annual_program").mkdir(parents=True)
    write(package / "evidence/DATA_APPROVAL.json", {"status": "approved"})
    records = []
    for method in ("M_available", "M_ready", "C_ready"):
        path = package / "cases" / (method + ".json")
        case = {"id": method, "execution_revision": {"model_id":"annual_responsive_dynamic_bridge_20261104.v1","owner_contract":"hospital_ies.annual_dynamic.accepted_engineering_owner.v1","load_cadence":"hourly_original_held","dispatch_minutes":15,"physical_max_segment_minutes":1,"declared_calendar":[]}, "annual_program": {"parameters": {
            "method": method, "mode": "N", "configuration": "H2",
            "duration_minutes": 525600., "start_hour": 1, "calendar": []}}}
        write(path, case)
        records.append({"method": method, "path": "cases/" + path.name})
    write(package / "cases/INDEX.json", {"cases": records})
    work.mkdir()
    admission = {"binding": {"source_commit": "fake-only", "input_sha256": "i"}, "limits": {
        "max_producers": 2, "max_accounts": 1, "max_total_processes": 3,
        "max_wall_seconds": 7 * 86400, "grace_seconds": 0,
        "rss_limit_bytes": 10**12, "min_available_ram_bytes": 1,
        "min_free_disk_bytes": 1, "initial_free_disk_required_bytes": 1, "max_recoveries_global": 1,
        "threads_per_producer": 1, "restore_timeout_seconds": 60,
        "poll_seconds": .01}}
    return package, work, admission


def test_module_has_no_launch_side_effect(runner):
    assert callable(runner.main)


def test_finite_budget_can_exceed_72_hours(runner, example):
    assert runner.validate_limits(example[2]["limits"])["max_wall_seconds"] == 604800
    for value in (float("inf"), float("nan"), 0, -1):
        limits = dict(example[2]["limits"], max_wall_seconds=value)
        with pytest.raises(ValueError):
            runner.validate_limits(limits)


def test_rejects_unsupported_concurrency_and_recovery_budget(runner, example):
    # Fixed2/1/3 ceilings were removed by the2026-11-05 user instruction.
    assert runner.validate_limits(dict(example[2]["limits"],max_producers=3,max_accounts=2,max_total_processes=4))
    for change in ({"max_producers": 0}, {"max_accounts": 0},
                   {"max_total_processes": 0}, {"max_recoveries_global": 2},
                   {"threads_per_producer": 2}):
        with pytest.raises(ValueError):
            runner.validate_limits(dict(example[2]["limits"], **change))


def test_only_three_continuous_normal_h2_cases(runner, example):
    package, _, _ = example
    assert len(runner.load_cases(package)) == 3
    path = package / "cases/M_ready.json"
    case = json.loads(path.read_text())
    case["annual_program"]["parameters"]["start_hour"] = 5041
    write(path, case)
    with pytest.raises(ValueError, match="normal|8760|hour"):
        runner.load_cases(package)


def test_case_paths_cannot_escape_package(runner, example):
    package, _, _ = example
    index = json.loads((package / "cases/INDEX.json").read_text())
    index["cases"][0]["path"] = "../outside.json"
    write(package / "cases/INDEX.json", index)
    with pytest.raises(ValueError):
        runner.load_cases(package)


def test_external_work_root_required(runner, example):
    package, _, _ = example
    for path in (package, package / "source/run", package / "new_output"):
        with pytest.raises(ValueError):
            runner.validate_work_root(package, path)


def test_command_uses_native_frozen_cli_and_separate_attempt_paths(runner, example):
    package, work, _ = example
    case = runner.load_cases(package)[0]
    paths = runner.attempt_paths(work / "runtime", case["method"], 0)
    cmd = runner.producer_command(package, case, paths)
    assert cmd[1:4] == ["-B", "-m", "tools.annual_program.runner"]
    assert cmd[cmd.index("--stop-after-blocks") + 1] == "35041"
    assert cmd[cmd.index("--checkpoint-every-blocks") + 1] == "35041"
    assert cmd[cmd.index("--recovery-every-blocks") + 1] == "4"
    assert "--accepted-result-cache" not in cmd
    assert cmd[cmd.index("--approval-receipt") + 1] == str(package / "evidence/DATA_APPROVAL.json")
    assert len(set(map(str, paths.values()))) == len(paths)
    env = runner.child_environment(package, paths)
    assert all(env[name] == "1" for name in (
        "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"))
    assert env["TMPDIR"] == str(paths["temp"])
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_default_no_admission_refuses_before_any_launch(runner, example, monkeypatch):
    package, work, _ = example
    def refuse(*args):
        raise ValueError("ADMISSION missing")
    monkeypatch.setattr(runner, "require_admission", refuse)
    with pytest.raises(ValueError, match="ADMISSION"):
        runner.run_campaign(package, work)
    assert not (work / "runtime").exists()


def test_unknown_account_counts_never_pass(runner):
    assert not runner.account_passed({"status": "passed", "counts": {"unknown": None}}, 0)
    assert not runner.account_passed({"status": "passed"}, 0)
    assert not runner.account_passed({"status": "passed", "counts": {"unknown": 1}}, 0)
    counts = {carrier: {"passed": 7} for carrier in ("e", "c", "steam")}
    good = {"status": "passed", "counts": counts, "original_bytes_unchanged": True,
            "source_bytes_unchanged": True}
    assert runner.account_passed(good, 0)
    assert counts == {carrier: {"passed": 7} for carrier in ("e", "c", "steam")}
    counts["steam"]["unknown"] = 1
    assert not runner.account_passed(good, 0)


def test_pid_reuse_is_not_owned(runner):
    import psutil
    process = psutil.Process()
    assert runner.identity_alive({"pid": process.pid, "create_time": process.create_time()})
    assert not runner.identity_alive({"pid": process.pid, "create_time": process.create_time() - 1})


class FakeOwner:
    """Completes native-shaped payloads immediately; can inject one unclean exit."""
    launched = []
    failures_left = 0
    restore_fails = False

    def __init__(self, command, *, cwd, env, stdout, stderr):
        self.command = command
        self.proc = type("FakeProcess", (), {"pid": -1})()
        self.code = 0
        self.closed = False
        self.launched.append(list(command))
        def arg(name):
            return Path(command[command.index(name) + 1])
        output = arg("--output")
        output.mkdir(parents=True, exist_ok=True)
        if "restore" in command:
            self.code = 1 if self.restore_fails else 0
            if not self.code:
                checkpoint = output / "resume.json"
                write(checkpoint, {})
                stdout.write(json.dumps({"resume_checkpoint": str(checkpoint)}) + "\n")
                stdout.flush()
        elif "tools.annual_program.runner" in command:
            if type(self).failures_left:
                type(self).failures_left -= 1
                self.code = 7
            else:
                write(output / "RESULT.json", {"execution_model_id":"annual_responsive_dynamic_bridge_20261104.v1","annual_dynamic_owner_contract":"hospital_ies.annual_dynamic.accepted_engineering_owner.v1","files":{},"status": "completed", "observed_until_minute": 525600.,
                    "reset_count": 1, "source_and_inputs_unchanged": True, "unknown_tail_minutes": 0.})
        else:
            write(output / "RESULT.json", {"status": "passed", "original_bytes_unchanged": True,
                "source_bytes_unchanged": True, "counts": {k: {"passed": 7} for k in ("e", "c", "steam")}})

    def poll(self):
        return self.code

    def terminate_tree(self):
        self.code = 1

    def close(self):
        self.closed = True


def fake_campaign(runner, example, monkeypatch, failures=0, restore_fails=False):
    package, work, admission = example
    monkeypatch.setattr(runner, "require_admission", lambda *a: admission)
    FakeOwner.launched = []
    FakeOwner.failures_left = failures
    FakeOwner.restore_fails = restore_fails
    return runner.run_campaign(package, work, owner_factory=FakeOwner,
        resource_probe=lambda *a: {"available_ram_bytes": 10**12, "free_disk_bytes": 10**12, "rss_bytes": 0},
        sleeper=lambda seconds: None)


def test_three_cases_run_once_and_counts_are_preserved(runner, example, monkeypatch):
    result = fake_campaign(runner, example, monkeypatch)
    assert result["status"] == "passed"
    assert len(result["completed"]) == 3
    assert len(FakeOwner.launched) == 6
    assert all(row["counts"] == {k: {"passed": 7} for k in ("e", "c", "steam")} for row in result["completed"])
    with pytest.raises(FileExistsError):
        fake_campaign(runner, example, monkeypatch)


def test_unclean_exit_has_one_global_restore_and_new_attempt(runner, example, monkeypatch):
    result = fake_campaign(runner, example, monkeypatch, failures=1)
    assert result["status"] == "passed"
    assert result["recoveries_used"] == 1
    commands = FakeOwner.launched
    assert sum("restore" in cmd for cmd in commands) == 1
    resumed = [cmd for cmd in commands if "--resume-checkpoint" in cmd]
    assert len(resumed) == 1
    assert "attempt_001" in resumed[0][resumed[0].index("--output") + 1]
    assert (example[1] / "runtime/cases/M_available/attempt_000/output").exists()


def test_failed_restore_is_charged_and_stops_batch(runner, example, monkeypatch):
    result = fake_campaign(runner, example, monkeypatch, failures=1, restore_fails=True)
    assert result["status"] != "passed"
    assert result["recoveries_used"] == 1
    assert sum("restore" in cmd for cmd in FakeOwner.launched) == 1
    assert not any("--resume-checkpoint" in cmd for cmd in FakeOwner.launched)


def test_second_unclean_exit_stops_whole_batch(runner, example, monkeypatch):
    # Serialize this fixture so the first restore is exercised before fault 2.
    # Simultaneous faults legitimately stop before any pending restore launches.
    example[2]["limits"]["max_producers"] = 1
    result = fake_campaign(runner, example, monkeypatch, failures=2)
    assert result["status"] != "passed"
    assert result["recoveries_used"] == 1
    assert sum("restore" in cmd for cmd in FakeOwner.launched) == 1


def test_resource_failure_writes_stop_and_reports_not_started(runner, example, monkeypatch):
    package, work, admission = example
    monkeypatch.setattr(runner, "require_admission", lambda *a: admission)
    result = runner.run_campaign(package, work, owner_factory=FakeOwner,
        resource_probe=lambda *a: {"available_ram_bytes": 0, "free_disk_bytes": 0, "rss_bytes": 0})
    assert result["status"] != "passed"
    assert len(result["not_started"]) == 3
    assert (work / "runtime/STOP").exists()
    assert result["tree_closed"] is True


def test_recovery_keeps_frozen_stop_and_vault_paths(runner, example):
    runtime = example[1] / "runtime"
    initial = runner.attempt_paths(runtime, "M_ready", 0)
    resumed = runner.attempt_paths(runtime, "M_ready", 1)
    assert initial["output"] != resumed["output"]
    assert initial["temp"] != resumed["temp"]
    assert initial["stop"] == resumed["stop"]
    assert initial["recovery"] == resumed["recovery"]


def test_status_never_labels_unstarted_as_a_pass(runner, example):
    receipt = runner.status(example[1])
    assert receipt["status"] == "not_started"
    assert receipt["tree_closed"] is True


def install_tiny_payload(package, ignore_stop):
    path = package / "source/tools/annual_program/runner.py"
    path.write_text('''import json, pathlib, subprocess, sys, time
def arg(name): return pathlib.Path(sys.argv[sys.argv.index(name)+1])
out=arg('--output');out.mkdir(parents=True)
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
(out/'child.json').write_text(json.dumps({'pid':child.pid}))
stop=arg('--stop-file')
while True:
    if stop.exists() and not IGNORE_STOP:
        child.terminate();child.wait(timeout=5)
        (out/'STOP_ACK.json').write_text(json.dumps({'status':'acknowledged'}))
        (out/'RESULT.json').write_text(json.dumps({'status':'suspended'}))
        break
    time.sleep(.01)
'''.replace("IGNORE_STOP", repr(ignore_stop)), encoding="utf8")


@pytest.mark.parametrize("ignore_stop", [False, True])
def test_real_tiny_owned_tree_stop_ack_or_forced_closure(runner, example, monkeypatch, ignore_stop):
    package, work, admission = example
    install_tiny_payload(package, ignore_stop)
    admission["limits"].update(max_producers=1, max_wall_seconds=.8, grace_seconds=.3)
    monkeypatch.setattr(runner, "require_admission", lambda *a: admission)
    # Explicit injected factory bypasses only the native E:-drive path gate for
    # this fake source directory. It still uses the real Windows Job lifecycle.
    result = runner.run_campaign(package, work, owner_factory=runner.OwnedTree,
        resource_probe=lambda *a: {"available_ram_bytes": 10**12, "free_disk_bytes": 10**12, "rss_bytes": 0})
    assert result["status"] != "passed"
    assert result["tree_closed"] is True
    assert result["remaining"] == []
    assert len(result["not_started"]) == 2
    output = work / "runtime/cases/M_available/attempt_000/output"
    assert (work / "runtime/cases/M_available/stop/STOP").is_file()
    assert (output / "STOP_ACK.json").is_file() is (not ignore_stop)
    assert result["closures"][0]["forced"] is ignore_stop


def test_heartbeat_binds_observed_owned_members(runner, example, monkeypatch):
    package, work, admission = example
    FakeOwner.launched = []
    FakeOwner.failures_left = 0
    monkeypatch.setattr(runner, "require_admission", lambda *a: admission)
    observed = []
    class MemberOwner(FakeOwner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.polls = 0

        def refresh(self):
            return []

        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else self.code
    runner.run_campaign(package, work, owner_factory=MemberOwner,
        resource_probe=lambda *a: {"available_ram_bytes": 10**12, "free_disk_bytes": 10**12, "rss_bytes": 0},
        sleeper=lambda seconds: observed.append(runner.read(work / "runtime/STATUS.json")))
    assert observed
    # Even an empty measured tree is explicit, never silently absent.
    assert any(receipt["active"] for receipt in observed)
    assert all("owned_members" in row for receipt in observed for row in receipt["active"])


def test_orphan_stop_observes_grace_before_pid_bound_kill(runner, example):
    import psutil
    _, work, admission = example
    admission["limits"]["grace_seconds"] = .2
    child = subprocess.Popen([sys.executable, "-B", "-c", "import time;time.sleep(30)"],
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    identity = {"pid": child.pid, "create_time": psutil.Process(child.pid).create_time()}
    runtime = work / "runtime"
    paths = runner.attempt_paths(runtime, "M_available", 0)
    paths["stop"].mkdir(parents=True)
    write(runtime / "REGISTRATION.json", {"owner": {"pid": os.getpid(), "create_time": -1},
        "limits": admission["limits"]})
    write(runtime / "STATUS.json", {"status": "running", "active": [{"identity": identity,
        "owned_members": [identity], "paths": {name: str(path) for name, path in paths.items()}}]})
    started = time.monotonic()
    try:
        receipt = runner.stop(work, "tiny orphan test")
        assert time.monotonic() - started >= .18
        assert receipt["status"] == "stop_requested_not_confirmed"
        assert receipt["tree_closed"] is False  # no native Job/session close receipt
        assert (runtime / "STOP").exists()
        assert (paths["stop"] / "STOP").exists()
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_orphan_grace_is_not_replaced_by_file_io_elapsed_time(runner, example, monkeypatch):
    _, work, admission = example
    admission["limits"]["grace_seconds"] = 2
    runtime = work / "runtime"
    write(runtime / "REGISTRATION.json", {"owner": {"pid": 999999, "create_time": -1},
        "limits": admission["limits"]})
    clock = [0.]
    killed_at = []
    def pause(seconds):
        clock[0] += seconds
    def fake_status(_):
        return {"supervisor_alive": False, "active": [], "tree_closed": False,
            "measured_remaining": [] if killed_at else [{"pid": 999999, "create_time": -1}]}
    class FakeProcess:
        def __init__(self, pid):
            pass
        def kill(self):
            killed_at.append(clock[0])
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=pause))
    monkeypatch.setattr(runner, "status", fake_status)
    monkeypatch.setattr(runner, "identity_alive", lambda identity: True)
    monkeypatch.setattr(runner, "process_identity", lambda pid: {"pid": pid, "create_time": 1})
    monkeypatch.setattr(runner, "psutil", SimpleNamespace(Process=FakeProcess))
    runner.stop(work)
    assert killed_at and killed_at[0] >= 2


def test_completed_root_cannot_hide_owned_descendant_cleanup(runner, example, monkeypatch):
    package, work, admission = example
    monkeypatch.setattr(runner, "require_admission", lambda *a: admission)
    FakeOwner.launched, FakeOwner.failures_left = [], 0
    class ResidualOwner(FakeOwner):
        def refresh(self):
            return [] if self.closed else [{"pid": -1, "create_time": 1}]
    result = runner.run_campaign(package, work, owner_factory=ResidualOwner,
        resource_probe=lambda *a: {"available_ram_bytes": 10**12, "free_disk_bytes": 10**12, "rss_bytes": 0},
        sleeper=lambda seconds: None)
    assert result["status"] != "passed"
    assert result["closures"][0]["residual_tree_terminated"] is True
    assert result["closures"][0]["forced"] is True


def test_initial_disk_reserve_rechecked_after_admission_before_any_model_start(runner, example, monkeypatch):
    package, work, admission = example
    admission["limits"]["initial_free_disk_required_bytes"] = 600_000_000_000
    monkeypatch.setattr(runner, "require_admission", lambda *a: admission)
    FakeOwner.launched, FakeOwner.failures_left = [], 0
    result = runner.run_campaign(package, work, owner_factory=FakeOwner,
        resource_probe=lambda *a: {"available_ram_bytes": 10**12, "free_disk_bytes": 500_000_000_000, "rss_bytes": 0})
    assert result["status"] != "passed"
    assert FakeOwner.launched == []
    assert len(result["not_started"]) == 3
    assert result["initial_producer_processes_closed"] == 0
    assert "initial_free_disk_required_bytes" in result["stop_reason"]


def test_initial_disk_reserve_is_a_positive_finite_registered_value(runner, example):
    for invalid in (None, 0, -1, float("inf"), float("nan"), True):
        limits = dict(example[2]["limits"], initial_free_disk_required_bytes=invalid)
        with pytest.raises(ValueError):
            runner.validate_limits(limits)


def delayed_recovery_campaign(runner, example, monkeypatch, stop_when_resume_ready=False):
    package, work, admission = example
    monkeypatch.setattr(runner, "require_admission", lambda *a: admission)
    FakeOwner.failures_left, FakeOwner.restore_fails, FakeOwner.launched = 1, False, []
    owners, timeline = [], []

    class DelayedOwner(FakeOwner):
        def __init__(self, command, **kwargs):
            super().__init__(command, **kwargs)
            self.phase = ("restore" if "restore" in command else
                          "producer" if "tools.annual_program.runner" in command else "account")
            self.delay = 6 if self.phase == "producer" and self.code == 0 else 0
            owners.append(self)
            living = [owner for owner in owners if not owner.closed]
            timeline.append({"phase": self.phase, "resumed": "--resume-checkpoint" in command,
                "producers": sum(owner.phase == "producer" for owner in living),
                "accounts": sum(owner.phase == "account" for owner in living), "total": len(living)})

        def poll(self):
            if self.closed:
                return self.code
            self.delay -= 1
            return None if self.delay >= 0 else self.code

    def tick(_):
        status = runner.read(work / "runtime/STATUS.json")
        if stop_when_resume_ready and status.get("waiting_resume"):
            (work / "runtime/STOP").write_text("fake stop after successful restore", encoding="utf8")

    result = runner.run_campaign(package, work, owner_factory=DelayedOwner,
        resource_probe=lambda *a: {"available_ram_bytes": 10**12, "free_disk_bytes": 10**12, "rss_bytes": 0},
        sleeper=tick)
    return result, timeline


@pytest.mark.parametrize("max_producers,max_total", [(1, 1), (1, 2), (2, 3)])
def test_restored_producer_reenters_registered_concurrency_gate(runner, example, monkeypatch, max_producers, max_total):
    example[2]["limits"].update(max_producers=max_producers, max_total_processes=max_total)
    result, timeline = delayed_recovery_campaign(runner, example, monkeypatch)
    assert result["status"] == "passed"
    assert result["recoveries_used"] == 1
    assert sum(row["phase"] == "producer" and row["resumed"] for row in timeline) == 1
    assert max(row["producers"] for row in timeline) <= max_producers
    assert max(row["accounts"] for row in timeline) <= 1
    assert max(row["total"] for row in timeline) <= max_total


def test_stop_after_restore_records_ready_resume_without_launching_it(runner, example, monkeypatch):
    example[2]["limits"].update(max_producers=2, max_total_processes=3)
    result, timeline = delayed_recovery_campaign(runner, example, monkeypatch, stop_when_resume_ready=True)
    assert result["status"] != "passed"
    assert result["recoveries_used"] == 1
    assert not any(row["resumed"] for row in timeline)
    assert any(row.get("phase") == "resume" and row.get("status") == "not_started_due_to_stop"
               for row in result["failed"])

