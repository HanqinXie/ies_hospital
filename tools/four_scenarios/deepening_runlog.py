"""Run one v8 command with durable start/finish receipts and raw stream logs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _append(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A command must follow --")
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=True)
    logs = run / "logs"
    logs.mkdir(exist_ok=True)
    ledger = run / "RUN_LEDGER.jsonl"
    key = args.name + "_" + uuid.uuid4().hex[:10]
    stdout = logs / (key + ".stdout.log")
    stderr = logs / (key + ".stderr.log")
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    started = datetime.now(timezone.utc).isoformat()
    _append(ledger, {"event": "started", "run_id": key, "name": args.name,
                     "command": command, "cwd": str(ROOT), "started_utc": started,
                     "stdout": str(stdout), "stderr": str(stderr),
                     "environment_overrides": {"PYTHONDONTWRITEBYTECODE": "1",
                                               "PYTHONIOENCODING": "utf-8"}})
    began = time.monotonic()
    try:
        with stdout.open("wb") as out_stream, stderr.open("wb") as err_stream:
            process = subprocess.Popen(command, cwd=ROOT, env=environment,
                                       stdout=out_stream, stderr=err_stream)
            code = process.wait()
    except BaseException as error:
        _append(ledger, {"event": "launcher_exception", "run_id": key,
                         "finished_utc": datetime.now(timezone.utc).isoformat(),
                         "error_type": type(error).__name__, "error": str(error)})
        raise
    finished = {"event": "finished", "run_id": key, "name": args.name,
                "finished_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": time.monotonic() - began, "exit_code": code,
                "stdout_sha256": _sha(stdout), "stderr_sha256": _sha(stderr),
                "stdout_bytes": stdout.stat().st_size, "stderr_bytes": stderr.stat().st_size}
    _append(ledger, finished)
    print(json.dumps(finished, ensure_ascii=False))
    if stdout.stat().st_size:
        sys.stdout.buffer.write(stdout.read_bytes()[-5000:])
        sys.stdout.buffer.flush()
    if stderr.stat().st_size:
        sys.stderr.buffer.write(stderr.read_bytes()[-5000:])
        sys.stderr.buffer.flush()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
