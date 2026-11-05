"""Admitted, bounded supervisor for three continuous normal-year trajectories.

Importing this module never starts a solver. The immutable science lives in
source/; this file only owns processes, paths, resource guards and receipts.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

import psutil
from scripts.execution_profile import require_case, MODEL_ID, OWNER_SCHEMA

PACKAGE = Path(__file__).resolve().parent
METHODS = ("M_available", "M_ready", "C_ready")


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("w", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def require_admission(package, work_root):
    """Load the sibling verifier lazily; failure is always fail-closed."""
    path = Path(package) / "transfer_checks.py"
    spec = importlib.util.spec_from_file_location("normal_year_transfer_checks_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.require_admission(package, work_root)


def validate_work_root(package, work_root):
    package, work_root = Path(package).resolve(), Path(work_root).resolve()
    if work_root == package or package in work_root.parents:
        raise ValueError("Explicit work-root must be outside the entire package")
    if work_root in package.parents:
        raise ValueError("Work-root must not contain the immutable package")
    return work_root


def validate_limits(limits):
    limits = dict(limits)
    from scripts.concurrency_policy import validate_counts
    validate_counts(limits)
    for key, lower, upper in (("threads_per_producer", 1, 1),
                              ("max_recoveries_global", 1, 1)):
        value = limits.get(key)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError("Invalid registered limit: " + key)
    for key in ("max_wall_seconds", "rss_limit_bytes", "min_available_ram_bytes", "min_free_disk_bytes",
                "initial_free_disk_required_bytes"):
        value = limits.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("A positive finite limit is required: " + key)
    for key, default, lower, upper in (("grace_seconds", None, 0, 120),
                                      ("restore_timeout_seconds", 1800., .001, 1800.),
                                      ("poll_seconds", 1., .001, 30.)):
        value = limits.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError("Invalid registered finite limit: " + key)
        limits[key] = value
    return limits


def load_cases(package):
    package = Path(package).resolve()
    records = read(package / "cases/INDEX.json").get("cases", [])
    if len(records) != 3 or {record.get("method") for record in records} != set(METHODS):
        raise ValueError("Exactly three registered normal H2 methods are required")
    cases = []
    identities = set()
    for method in METHODS:
        record = next(row for row in records if row["method"] == method)
        relative = Path(record["path"])
        path = (package / relative).resolve()
        if relative.is_absolute() or package / "cases" not in path.parents or path.is_symlink():
            raise ValueError("Case path must stay inside package cases/")
        case = read(path)
        require_case(case)
        parameters = case.get("annual_program", {}).get("parameters", {})
        expected = {"method": method, "mode": "N", "configuration": "H2",
                    "duration_minutes": 525600., "start_hour": 1, "calendar": []}
        if any(parameters.get(key) != value for key, value in expected.items()):
            raise ValueError("Only continuous normal H2 8760-hour cases starting at source hour 1 are admitted")
        if not case.get("id") or case["id"] in identities:
            raise ValueError("Missing or duplicate case identity")
        identities.add(case["id"])
        if record.get("file_sha256") and sha(path) != record["file_sha256"]:
            raise ValueError("Registered case bytes changed")
        cases.append(dict(record, method=method, file=str(path), case_id=case["id"]))
    return cases


def attempt_paths(runtime, method, attempt):
    case = Path(runtime) / "cases" / method
    base = case / ("attempt_%03d" % attempt)
    paths = {name: base / name for name in ("output", "temp", "sqlite", "logs")}
    # Native ORCHESTRATION_FREEZE binds these two paths across every invocation.
    # Each case has its own vault and ordinary stop flag, including after restore.
    paths.update(recovery=case / "recovery", stop=case / "stop")
    return paths


def child_environment(package, paths):
    source = Path(package) / "source"
    inherited = dict(os.environ)
    inherited.pop('SF2_NATIVE_SAMPLE_RUN', None)
    return dict(inherited, TEMP=str(paths["temp"]), TMP=str(paths["temp"]),
        TMPDIR=str(paths["temp"]), SQLITE_TMPDIR=str(paths["sqlite"]),
        PYTHONPATH=os.pathsep.join((str(source / "src"), str(source))),
        PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONHASHSEED="0",
        OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
        NUMEXPR_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1", BLIS_NUM_THREADS="1")


def producer_command(package, case, paths, resume_checkpoint=None):
    command = [sys.executable, "-B", "-m", "tools.annual_program.runner", "--case-file", case["file"],
        "--output", str(paths["output"]), "--approval-receipt", str(Path(package) / "evidence/DATA_APPROVAL.json"),
        "--stop-file", str(paths["stop"] / "STOP"), "--recording-mode", "engineering_v1",
        "--stop-after-blocks", "35041", "--checkpoint-every-blocks", "35041",
        "--recovery-every-blocks", "4", "--recovery-vault", str(paths["recovery"])]
    if resume_checkpoint is not None:
        command += ["--resume-checkpoint", str(resume_checkpoint)]
    return command


def identity_alive(identity):
    try:
        process = psutil.Process(identity["pid"])
        return process.create_time() == identity["create_time"] and process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess, KeyError, TypeError, ValueError):
        return False
    except psutil.AccessDenied:
        # Lack of permission is unknown and must never become tree_closed=True.
        return True


def process_identity(pid):
    if pid < 1:
        return None
    return {"pid": pid, "create_time": psutil.Process(pid).create_time()}


class OwnedTree:
    """Windows uses the reviewed Job adapter; POSIX creates a private session."""
    def __init__(self, command, *, cwd, env, stdout, stderr):
        self.native = None
        self.known = {}
        if os.name == "nt":
            path = PACKAGE / "scripts/vendor/owned_process_hidden.py"
            spec = importlib.util.spec_from_file_location("normal_year_owned_job", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.native = module.OwnedProcess(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
            self.proc = self.native.proc
        else:
            self.proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                         start_new_session=True)
        self.root = process_identity(self.proc.pid)
        self.known[self.root["pid"]] = self.root
        self.refresh()

    def refresh(self):
        if self.native is not None and self.native.job:
            # Query the job itself, including descendants whose parent exited.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            query = kernel.QueryInformationJobObject
            query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            buffer = ctypes.create_string_buffer(1024 * 1024)
            needed = wintypes.DWORD()
            if not query(self.native.job, 3, buffer, len(buffer), ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            count = ctypes.c_uint32.from_buffer(buffer, 4).value
            pids = (ctypes.c_size_t * count).from_buffer(buffer, 8)
            for pid in pids:
                try:
                    identity = process_identity(pid)
                    if identity:
                        self.known[pid] = identity
                except psutil.NoSuchProcess:
                    pass
        elif os.name != "nt":
            for process in psutil.process_iter(["pid", "create_time"]):
                try:
                    if (os.getsid(process.pid) == self.root["pid"] and
                            process.info["create_time"] >= self.root["create_time"]):
                        self.known[process.pid] = dict(pid=process.pid, create_time=process.info["create_time"])
                except (psutil.NoSuchProcess, ProcessLookupError):
                    pass
        return [identity for identity in self.known.values() if identity_alive(identity)]

    def poll(self):
        self.refresh()
        return self.proc.poll()

    def terminate_tree(self):
        living = self.refresh()
        if self.native is not None:
            self.native.terminate_tree()
        else:
            # Each target has a verified creation time and belongs to our session.
            for identity in reversed(living):
                if identity_alive(identity):
                    try:
                        psutil.Process(identity["pid"]).kill()
                    except psutil.NoSuchProcess:
                        pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def close(self):
        if self.refresh():
            self.terminate_tree()
        if self.native is not None:
            self.native.close()


def resource_snapshot(work_root, active):
    rss = 0
    seen = set()
    for job in active:
        owner = job["owner"]
        identities = owner.refresh() if hasattr(owner, "refresh") else []
        for identity in identities:
            if identity["pid"] not in seen and identity_alive(identity):
                rss += psutil.Process(identity["pid"]).memory_info().rss
                seen.add(identity["pid"])
    return dict(available_ram_bytes=psutil.virtual_memory().available,
                free_disk_bytes=psutil.disk_usage(str(work_root)).free, rss_bytes=rss)


def resource_failure(snapshot, limits):
    for field, threshold, below in (("available_ram_bytes", "min_available_ram_bytes", True),
                                    ("free_disk_bytes", "min_free_disk_bytes", True),
                                    ("rss_bytes", "rss_limit_bytes", False)):
        value = snapshot.get(field)
        if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
            return "resource measurement unknown: " + field
        if (value < limits[threshold]) if below else (value > limits[threshold]):
            return "registered resource bound: " + field
    return None


def latest_result(output):
    output = Path(output)
    path = output / "RESULT.json"
    if path.is_file():
        return read(path)
    parts = sorted(output.glob("RESULT_part_*.json"))
    return read(parts[-1]) if parts else None


def producer_passed(result, code):
    return bool(code == 0 and result and result.get("execution_model_id") == MODEL_ID
        and result.get("annual_dynamic_owner_contract") == OWNER_SCHEMA
        and isinstance(result.get("files"), dict) and result.get("status") == "completed"
        and result.get("observed_until_minute") == 525600.
        and result.get("unknown_tail_minutes") == 0.
        and result.get("reset_count") == 1 and result.get("source_and_inputs_unchanged") is True)


def account_passed(result, code):
    if code != 0 or not result or result.get("status") != "passed":
        return False
    if result.get("original_bytes_unchanged") is not True or result.get("source_bytes_unchanged") is not True:
        return False
    counts = result.get("counts")
    if not isinstance(counts, dict) or set(counts) != {"e", "c", "steam"}:
        return False
    for carrier in counts.values():
        if not isinstance(carrier, dict) or type(carrier.get("passed")) is not int or carrier["passed"] <= 0:
            return False
        # Raw Counter dictionaries omit categories with zero occurrences. Do not
        # add missing keys or turn a real unknown, null or malformed count to zero.
        if set(carrier) - {"passed", "unknown", "failed"}:
            return False
        for category in ("unknown", "failed"):
            if category in carrier and (type(carrier[category]) is not int or carrier[category] != 0):
                return False
    return True


def run_campaign(package, work_root, *, owner_factory=None, resource_probe=None, sleeper=time.sleep):
    package = Path(package).resolve()
    work_root = validate_work_root(package, work_root)
    admission = require_admission(package, work_root)
    limits = validate_limits(admission["limits"])
    cases = load_cases(package)
    if owner_factory is None and os.name == "nt" and work_root.drive.upper() != "E:":
        raise ValueError("Frozen native core requires Windows work-root and stop paths on E:; no drive mapping is performed")
    owner_factory = owner_factory or OwnedTree
    resource_probe = resource_probe or resource_snapshot
    runtime = work_root / "runtime"
    runtime.mkdir(exist_ok=True)
    registration_path = runtime / "REGISTRATION.json"
    with registration_path.open("x", encoding="utf8") as stream:
        identity = process_identity(os.getpid())
        affinity = psutil.Process().cpu_affinity() if hasattr(psutil.Process(), "cpu_affinity") else None
        registration = dict(schema="hospital_ies.normal_year.runtime.v1", utc=now(), owner=identity,
            package=str(package), work_root=str(work_root), host=dict(hostname=platform.node(), system=platform.system(),
                release=platform.release(), machine=platform.machine(), visible_cpu_count=os.cpu_count(), affinity=affinity),
            admission=admission, admission_path=str(work_root / "admission/ADMISSION.json"),
            admission_sha256=sha(work_root / "admission/ADMISSION.json") if (work_root / "admission/ADMISSION.json").is_file() else None,
            tools={path.name: sha(path) for path in (package / "server_runner.py", package / "transfer_checks.py") if path.is_file()},
            cases=cases, accepted_result_cache=False, limits=limits,
            planned_initial_trajectories=3, scientific_source_modified=False)
        json.dump(registration, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    active, pending, waiting_accounts, waiting_recovery = [], list(cases), [], []
    waiting_resume = []
    completed, failed, not_started, unaccounted, closure_receipts = [], [], [], [], []
    recoveries_used = 0
    stopping = False
    stop_reason = None
    stop_started = None
    started = time.monotonic()
    last_resource = None
    initial_reserve_checked = False

    def event(kind, **values):
        with (runtime / "EVENTS.jsonl").open("a", encoding="utf8") as stream:
            stream.write(json.dumps(dict(utc=now(), event=kind, **values), ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def paths_for(case, attempt):
        paths = attempt_paths(runtime, case["method"], attempt)
        for name in ("temp", "sqlite", "recovery", "stop", "logs"):
            paths[name].mkdir(parents=True, exist_ok=attempt > 0 and name in ("recovery", "stop"))
        return paths

    def request_cooperative_stop(reason):
        nonlocal stopping, stop_reason, stop_started
        if stopping:
            return
        stopping, stop_reason, stop_started = True, reason, time.monotonic()
        flag = runtime / "STOP"
        if not flag.exists():
            flag.write_text(reason + "\n", encoding="utf8")
        for job in active:
            paths = job["paths"]
            (paths["stop"] / "STOP").write_text(reason + "\n", encoding="utf8")
        event("cooperative_stop_requested", reason=reason, grace_seconds=limits["grace_seconds"])

    def launch(case, phase, attempt=0, paths=None, resume=None, producer=None, vault=None):
        if len(active) >= limits["max_total_processes"]:
            raise RuntimeError("Registered total process allowance is full")
        if phase == "producer" and sum(job["phase"] == "producer" for job in active) >= limits["max_producers"]:
            raise RuntimeError("Registered producer allowance is full")
        if phase == "account" and sum(job["phase"] == "account" for job in active) >= limits["max_accounts"]:
            raise RuntimeError("Registered account allowance is full")
        paths = paths if paths is not None else paths_for(case, attempt)
        if phase == "producer":
            command = producer_command(package, case, paths, resume)
        elif phase == "account":
            command = [sys.executable, "-B", str(package / "scripts/audit_engineering_year.py"),
                       "--source", str(package / "source"), "--producer", producer,
                       "--output", str(paths["output"])]
        else:
            command = [sys.executable, "-B", "-m", "tools.annual_program.recovery", "restore",
                       "--vault", vault, "--snapshot", "latest", "--output", str(paths["output"])]
        handles = [(paths["logs"] / (phase + "_" + name + ".log")).open("x", encoding="utf8")
                   for name in ("stdout", "stderr")]
        try:
            owner = owner_factory(command, cwd=str(package / "source"), env=child_environment(package, paths),
                                  stdout=handles[0], stderr=handles[1])
        except BaseException:
            for handle in handles:
                handle.close()
            raise
        state = dict(method=case["method"], case_id=case["case_id"], phase=phase, attempt=attempt,
            pid=owner.proc.pid, identity=process_identity(owner.proc.pid), command=command, started_utc=now(),
            paths={name: str(path) for name, path in paths.items()}, producer=producer,
            resumed_from=str(resume) if resume else None)
        atomic(paths["logs"] / (phase + "_LAUNCH.json"), state)
        event("process_started", **state)
        job = dict(case=case, phase=phase, attempt=attempt, paths=paths, owner=owner,
                   handles=handles, state=state, started=time.monotonic(), producer=producer)
        active.append(job)
        return job

    def close_job(job, forced=False):
        owner = job["owner"]
        before = owner.refresh() if hasattr(owner, "refresh") else []
        residual = bool(before and not forced)
        if forced:
            owner.terminate_tree()
        owner.close()
        remaining = owner.refresh() if hasattr(owner, "refresh") else []
        receipt = dict(utc=now(), method=job["case"]["method"], phase=job["phase"], attempt=job["attempt"],
            forced=forced or residual, residual_tree_terminated=residual,
            observed_before=before, remaining=remaining, tree_closed=not remaining,
            measurement="PID+create_time and owned Job/session" if hasattr(owner, "refresh") else "fake_test_owner")
        for handle in job["handles"]:
            handle.close()
        atomic(job["paths"]["logs"] / (job["phase"] + "_CLOSURE.json"), receipt)
        closure_receipts.append(receipt)
        active.remove(job)
        if remaining:
            raise RuntimeError("Owned process tree remains open; inspect closure receipt")
        if residual:
            failed.append(dict(method=job["case"]["method"], phase=job["phase"],
                               status="root_exited_with_live_owned_descendants", closure=receipt))
            request_cooperative_stop("root exited with live owned descendants requiring termination")
        return receipt

    def heartbeat():
        for job in active:
            owner = job["owner"]
            job["state"]["owned_members"] = owner.refresh() if hasattr(owner, "refresh") else []
        atomic(runtime / "STATUS.json", dict(utc=now(), owner=identity, status="stopping" if stopping else "running",
            stop_reason=stop_reason, elapsed_seconds=time.monotonic() - started, limits=limits,
            active=[job["state"] for job in active], pending=[case["method"] for case in pending],
            waiting_accounts=[job["case"]["method"] for job in waiting_accounts],
            waiting_recovery=[job["case"]["method"] for job in waiting_recovery],
            waiting_resume=[item["case"]["method"] for item in waiting_resume],
            completed=completed, failed=failed, not_started=not_started,
            recoveries_used=recoveries_used, resources=last_resource))

    try:
        while pending or active or waiting_accounts or waiting_recovery or waiting_resume:
            last_resource = resource_probe(work_root, active)
            violation = resource_failure(last_resource, limits)
            if violation:
                request_cooperative_stop(violation)
            if not initial_reserve_checked and not stopping:
                # Admission may be hours old. Re-measure before the first model
                # launch; subsequent output growth uses the registered floor.
                if last_resource.get("free_disk_bytes", -1) < limits["initial_free_disk_required_bytes"]:
                    request_cooperative_stop("registered resource bound: initial_free_disk_required_bytes")
                initial_reserve_checked = True
            if time.monotonic() - started >= limits["max_wall_seconds"]:
                request_cooperative_stop("registered campaign wallclock exhausted")
            if (runtime / "STOP").exists():
                request_cooperative_stop("external stop requested")
            if stopping:
                not_started.extend(case["method"] for case in pending)
                pending.clear()
                unaccounted.extend(job["state"] for job in waiting_accounts)
                waiting_accounts.clear()
                failed.extend(dict(method=job["case"]["method"], phase="recovery", status="not_started_due_to_stop")
                              for job in waiting_recovery)
                waiting_recovery.clear()
                failed.extend(dict(method=item["case"]["method"], phase="resume", status="not_started_due_to_stop",
                    output=str(item["paths"]["output"]), checkpoint=str(item["checkpoint"]))
                    for item in waiting_resume)
                waiting_resume.clear()
            while not stopping and len(active) < limits["max_total_processes"]:
                producers = sum(job["phase"] == "producer" for job in active)
                accounts = sum(job["phase"] == "account" for job in active)
                if waiting_recovery:
                    old = waiting_recovery.pop(0)
                    paths = paths_for(old["case"], 1)
                    launch(old["case"], "restore", 1, paths=paths, vault=str(old["paths"]["recovery"]))
                elif waiting_resume and producers < limits["max_producers"]:
                    restored = waiting_resume.pop(0)
                    launch(restored["case"], "producer", 1, paths=restored["paths"], resume=restored["checkpoint"])
                elif pending and producers < limits["max_producers"]:
                    launch(pending.pop(0), "producer")
                elif waiting_accounts and accounts < limits["max_accounts"]:
                    producer = waiting_accounts.pop(0)
                    account_paths = {name: runtime / "accounts" / producer["case"]["method"] / name
                                     for name in ("output", "temp", "sqlite", "recovery", "stop", "logs")}
                    for name, path in account_paths.items():
                        if name != "output":
                            path.mkdir(parents=True, exist_ok=False)
                    launch(producer["case"], "account", paths=account_paths,
                           producer=str(producer["paths"]["output"]))
                else:
                    break
            for job in list(active):
                code = job["owner"].poll()
                if code is None and job["phase"] == "restore" and time.monotonic() - job["started"] >= limits["restore_timeout_seconds"]:
                    request_cooperative_stop("registered restore timeout")
                forced = bool(stopping and code is None and time.monotonic() - stop_started >= limits["grace_seconds"])
                if code is None and not forced:
                    continue
                close_job(job, forced=forced)
                code = job["owner"].poll()
                phase = job["phase"]
                result = latest_result(job["paths"]["output"]) if phase != "restore" else None
                event("process_exited", method=job["case"]["method"], phase=phase, attempt=job["attempt"],
                      exit_code=code, result_status=(result or {}).get("status"), forced=forced)
                if phase == "producer" and producer_passed(result, code):
                    if stopping:
                        unaccounted.append(job["state"])
                    else:
                        waiting_accounts.append(job)
                elif phase == "producer" and not stopping and result is None and recoveries_used < limits["max_recoveries_global"]:
                    recoveries_used += 1  # Charged before attempting restore, including a failed attempt.
                    event("global_recovery_charged", used=recoveries_used, method=job["case"]["method"])
                    waiting_recovery.append(job)
                elif phase == "restore" and code == 0 and not stopping:
                    payload = json.loads((job["paths"]["logs"] / "restore_stdout.log").read_text("utf8").strip().splitlines()[-1])
                    checkpoint = Path(payload["resume_checkpoint"]).resolve()
                    if job["paths"]["output"].resolve() not in checkpoint.parents or not checkpoint.is_file():
                        raise ValueError("Restore checkpoint is not inside its new attempt")
                    # Restore itself is a bounded child, not a producer slot.
                    # A resumed producer must reenter both normal launch gates.
                    waiting_resume.append(dict(case=job["case"], paths=job["paths"], checkpoint=checkpoint))
                    event("resume_ready", method=job["case"]["method"], checkpoint=str(checkpoint))
                elif phase == "account" and account_passed(result, code):
                    completed.append(dict(method=job["case"]["method"], producer=job["producer"],
                        account=str(job["paths"]["output"]), counts=result["counts"], result=result))
                else:
                    failed.append(dict(method=job["case"]["method"], phase=phase, attempt=job["attempt"],
                        exit_code=code, result=result, output=str(job["paths"]["output"]), forced=forced))
                    request_cooperative_stop("producer/account/recovery incomplete or failed")
            heartbeat()
            if active or pending or waiting_accounts or waiting_recovery or waiting_resume:
                sleeper(limits["poll_seconds"])
    except BaseException as error:
        failed.append(dict(phase="supervisor", type=type(error).__name__, message=str(error)))
        request_cooperative_stop("supervisor exception: " + type(error).__name__)
        event("supervisor_error", type=type(error).__name__, message=str(error))
        # Preserve the ordinary stop/ACK opportunity even on supervisor errors.
        deadline = stop_started + limits["grace_seconds"]
        while active and time.monotonic() < deadline:
            for job in list(active):
                if job["owner"].poll() is not None:
                    close_job(job)
            if active:
                sleeper(min(.25, max(0., deadline - time.monotonic())))
        for job in list(active):
            try:
                close_job(job, forced=True)
            except BaseException as close_error:
                failed.append(dict(phase="tree_closure", message=str(close_error)))
        not_started.extend(case["method"] for case in pending)
        failed.extend(dict(method=item["case"]["method"], phase="resume", status="not_started_due_to_stop",
            output=str(item["paths"]["output"]), checkpoint=str(item["checkpoint"])) for item in waiting_resume)
        waiting_resume.clear()
    remaining = []
    for receipt in closure_receipts:
        remaining.extend(identity for identity in receipt["remaining"] if identity_alive(identity))
    for job in active:
        if hasattr(job["owner"], "refresh"):
            remaining.extend(job["owner"].refresh())
    tree_closed = not remaining and not active
    result = dict(schema="hospital_ies.normal_year.campaign_result.v1", utc=now(),
        status="passed" if len(completed) == 3 and not failed and not stopping and tree_closed else "incomplete_or_failed",
        completed=completed, failed=failed, not_started=sorted(set(not_started)),
        unaccounted_producers=unaccounted, stopping=stopping, stop_reason=stop_reason,
        recoveries_used=recoveries_used, elapsed_seconds=time.monotonic() - started,
        remaining=remaining, tree_closed=tree_closed, closures=closure_receipts,
        selected_cases=3, scientific_source_modified=False, accepted_result_cache=False)
    result["producer_processes_closed"] = sum(row["phase"] == "producer" for row in closure_receipts)
    result["initial_producer_processes_closed"] = sum(row["phase"] == "producer" and row["attempt"] == 0
                                                       for row in closure_receipts)
    atomic(runtime / "RESULT.json", result)
    atomic(runtime / "STATUS.json", result)
    return result


def status(work_root):
    runtime = Path(work_root).resolve() / "runtime"
    if not (runtime / "REGISTRATION.json").exists():
        return dict(status="not_started", work_root=str(Path(work_root).resolve()), remaining=[], tree_closed=True)
    registration = read(runtime / "REGISTRATION.json")
    receipt = read(runtime / "STATUS.json") if (runtime / "STATUS.json").exists() else {"status": "registered_no_heartbeat"}
    identities = [row.get("identity") for row in receipt.get("active", []) if row.get("identity")]
    for row in receipt.get("active", []):
        identities.extend(row.get("owned_members", []))
    identities.extend(receipt.get("remaining", []))
    remaining = [identity for identity in identities if identity_alive(identity)]
    return dict(receipt, supervisor_alive=identity_alive(registration["owner"]),
                measured_remaining=remaining,
                tree_closed=receipt.get("tree_closed") is True and not remaining)


def stop(work_root, reason="operator requested stop"):
    work_root = Path(work_root).resolve()
    runtime = work_root / "runtime"
    if not (runtime / "REGISTRATION.json").is_file():
        return dict(status="not_started", tree_closed=True, remaining=[])
    registration = read(runtime / "REGISTRATION.json")
    limits = validate_limits(registration["limits"])
    atomic(runtime / "STOP_REQUEST.json", dict(utc=now(), reason=reason, requester=process_identity(os.getpid())))
    (runtime / "STOP").write_text(reason + "\n", encoding="utf8")
    # Native producer stop files are written by the live supervisor. Also write
    # registered active flags if its heartbeat is available after an interruption.
    receipt = status(work_root)
    for row in receipt.get("active", []):
        path = Path(row["paths"]["stop"]) / "STOP"
        if runtime in path.resolve().parents:
            path.write_text(reason + "\n", encoding="utf8")
    grace_deadline = time.monotonic() + limits["grace_seconds"]
    deadline = grace_deadline + 10
    while time.monotonic() < deadline:
        receipt = status(work_root)
        if receipt.get("tree_closed") and not receipt.get("supervisor_alive"):
            break
        if not receipt.get("supervisor_alive"):
            if receipt.get("measured_remaining") and time.monotonic() < grace_deadline:
                time.sleep(min(.1, grace_deadline - time.monotonic()))
                continue
            # A crashed supervisor's Job closes on Windows. POSIX orphan closure
            # requires actual PID+birth-bound targets, never a generic name kill.
            for identity in receipt.get("measured_remaining", []):
                if identity_alive(identity):
                    try:
                        psutil.Process(identity["pid"]).kill()
                    except psutil.NoSuchProcess:
                        pass
            break
        time.sleep(.1)
    receipt = status(work_root)
    result = dict(utc=now(), status="closed" if receipt.get("tree_closed") and not receipt.get("supervisor_alive") else "stop_requested_not_confirmed",
                  supervisor_alive=receipt.get("supervisor_alive"), remaining=receipt.get("measured_remaining", []),
                  tree_closed=receipt.get("tree_closed") is True and not receipt.get("supervisor_alive"), receipt=receipt)
    atomic(runtime / "STOP_RECEIPT.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "stop", "status"):
        command = sub.add_parser(name)
        command.add_argument("--work-root", type=Path, required=True)
        if name == "stop":
            command.add_argument("--reason", default="operator requested stop")
    args = parser.parse_args(argv)
    work_root = validate_work_root(PACKAGE, args.work_root)
    if args.command == "run":
        result = run_campaign(PACKAGE, work_root)
    elif args.command == "stop":
        result = stop(work_root, args.reason)
    else:
        result = status(work_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.command == "status" or result["status"] in ("passed", "closed", "not_started") else 1


if __name__ == "__main__":
    raise SystemExit(main())
