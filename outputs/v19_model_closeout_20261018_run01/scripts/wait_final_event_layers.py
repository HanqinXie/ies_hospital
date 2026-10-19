"""Finite read-only wait for the fixed R2 terminal, then collect exactly once."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
B19 = ROOT / "outputs/v19_model_closeout_20261018_run01"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)


def terminal_gate(value, frozen_sha):
    reasons = []
    if value.get("label") != "non_normal_final01": reasons.append("wrong_queue_label")
    if value.get("registered_pairs") != 394: reasons.append("registered_pairs_not_394")
    if value.get("registered_batches") != 136: reasons.append("registered_batches_not_136")
    if value.get("freeze_sha256") != frozen_sha: reasons.append("freeze_sha_mismatch")
    if value.get("all_succeeded") is not True: reasons.append("queue_not_all_succeeded")
    rows = value.get("results")
    if not isinstance(rows, list) or len(rows) != 136: reasons.append("terminal_batch_count_not_136")
    if not isinstance(rows, list) or any(row.get("accepted") is not True for row in rows): reasons.append("batch_not_accepted")
    if value.get("unstarted_batches") != []: reasons.append("unstarted_batches_present_or_unknown")
    return reasons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="final_main_confirmation_propagation01")
    parser.add_argument("--wait-hours", type=float, default=24.)
    parser.add_argument("--poll-seconds", type=float, default=30.)
    args = parser.parse_args()
    if Path(args.name).name != args.name or args.name in (".", ".."):
        parser.error("invalid output name")
    if not 0 < args.wait_hours <= 24 or not 1 <= args.poll_seconds <= 60:
        parser.error("wait must be 0<hours<=24 and 1<=poll_seconds<=60")
    work = B19 / "review/event_layers" / (args.name + "_wait")
    work.mkdir(parents=True, exist_ok=False)
    collector = B19 / "scripts/collect_event_layers.py"
    frozen = B19 / "MODEL_FREEZE_CLOSEOUT01.json"
    queue = B19 / "FROZEN_QUEUE_NON_NORMAL_FINAL01_RESULT.json"
    process = B19 / "logs/frozen_non_normal_final01.command.json"
    command = [sys.executable, "-B", str(collector), "--name", args.name]
    initial = dict(schema="v19.finite_event_collector_wait.v1", utc_started=datetime.now(timezone.utc).isoformat(),
        waiter_sha256=sha(Path(__file__).resolve()), collector_sha256=sha(collector), freeze_sha256=sha(frozen),
        queue_path=str(queue), producer_process_receipt_path=str(process), command=command,
        wait_hours=args.wait_hours, poll_seconds=args.poll_seconds, physical_episodes_started=0,
        raw_files_opened=0, annual_files_opened=0, collector_attempts=0)
    write(work / "WAIT_START.json", initial)
    print(json.dumps(dict(status="waiting_fixed_R2_terminal", work=str(work), max_wait_hours=args.wait_hours)), flush=True)
    finish = dict(initial)
    code = 1
    started = time.monotonic()
    try:
        while True:
            if (B19 / "STOP_FROZEN_QUEUE.json").exists():
                raise RuntimeError("STOP_FROZEN_QUEUE exists; no collection launched")
            if queue.exists() and process.exists():
                # Producer command receipt follows closed queue output; allow a
                # short filesystem settlement before a single terminal read.
                if time.time() - max(queue.stat().st_mtime, process.stat().st_mtime) >= 2:
                    break
            if time.monotonic() - started >= args.wait_hours * 3600:
                raise TimeoutError("Finite wait expired before complete R2 terminal; no collection launched")
            time.sleep(args.poll_seconds)
        producer, terminal = read(process), read(queue)
        if producer.get("exit_code") != 0:
            raise RuntimeError("R2 producer command failed: " + str(producer.get("exit_code")))
        reasons = terminal_gate(terminal, initial["freeze_sha256"])
        if reasons:
            raise RuntimeError("R2 terminal refused: " + ",".join(reasons))
        if sha(collector) != initial["collector_sha256"] or sha(frozen) != initial["freeze_sha256"]:
            raise RuntimeError("Collector/freeze changed during wait; no retry")
        finish.update(queue_sha256=sha(queue), producer_process_receipt_sha256=sha(process), collector_attempts=1)
        write(work / "COLLECTOR_COMMAND.json", dict(command=command, cwd=str(ROOT),
            collector_sha256=initial["collector_sha256"], queue_sha256=finish["queue_sha256"]))
        # One attempt only. No simulator imports or physical execution.
        done = subprocess.run(command, cwd=ROOT, capture_output=True, check=False, timeout=1800)
        (work / "collector.stdout.log").write_bytes(done.stdout)
        (work / "collector.stderr.log").write_bytes(done.stderr)
        finish.update(collector_exit_code=done.returncode, collector_stdout=done.stdout.decode("utf-8", errors="replace"))
        if done.returncode != 0:
            raise RuntimeError("Formal event collector failed; preserved attempt, no retry")
        output = B19 / "review/event_layers" / args.name
        receipt, report = output / "RECEIPT.json", output / "EVENT_LAYER_REVIEW.json"
        result = read(report)
        if result.get("formal_terminal_complete") is not True:
            raise RuntimeError("Collector output is not formal terminal complete")
        finish.update(status="formal_collection_finished", output=str(output),
                      report_sha256=sha(report), receipt_sha256=sha(receipt))
        code = 0
    except Exception as error:
        finish.update(status="stopped_unknown_or_failed", error=dict(type=type(error).__name__, message=str(error)),
                      traceback=traceback.format_exc())
    finish.update(utc_finished=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic() - started)
    write(work / "WAIT_RESULT.json", finish)
    print(json.dumps({k: finish.get(k) for k in ("status", "collector_attempts", "collector_exit_code", "output", "report_sha256", "error")}), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
