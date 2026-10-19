"""Frozen, resumable collection of v4 secure-relative hospital snapshots."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_once(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def _append(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def plan_collection(plan_path: Path, *, early_train_groups: int = 12,
                    early_validation_groups: int = 4) -> dict:
    """Select whole source groups by frozen IDs, without consulting outcomes."""
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    allowed = {"pilot_train", "pilot_validation"}
    groups = {role: set() for role in allowed}
    for row in plan:
        if row["experiment_role"] in groups:
            groups[row["experiment_role"]].add(row["group_id"])
    n = {"pilot_train": early_train_groups,
         "pilot_validation": early_validation_groups}
    if any(count < 1 or count > len(groups[role]) for role, count in n.items()):
        raise ValueError("Early collection needs available whole train/validation groups")
    selected = {role: set(sorted(groups[role])[:n[role]]) for role in allowed}
    early = [i for i, row in enumerate(plan)
             if row["experiment_role"] in allowed
             and row["group_id"] in selected[row["experiment_role"]]]
    remaining = [i for i, row in enumerate(plan)
                 if row["experiment_role"] in allowed
                 and row["group_id"] not in selected[row["experiment_role"]]]
    assert len(set(early + remaining)) == len(early) + len(remaining)
    return {"schema": "reliability_first.collection_design.v1",
            "plan_sha256": _sha(Path(plan_path)),
            "early_indices": early, "remaining_indices": remaining,
            "early_group_counts": n,
            "all_group_counts": {role: len(groups[role]) for role in sorted(allowed)},
            "selection_rule": "sorted frozen group ID, all four fixed time phases, no outcome lookup",
            "development_indices_excluded": [i for i, row in enumerate(plan)
                                            if row["experiment_role"] == "development_boundary"]}


def register(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    manifest = run_dir / "new_data/global_manifest.json"
    plan = run_dir / "design/snapshot_plan_v2.json"
    if not manifest.is_file() or not plan.is_file():
        raise FileNotFoundError("Frozen manifest and corrected plan are required")
    receipt = plan_collection(plan)
    receipt["manifest_sha256"] = _sha(manifest)
    module_dir = Path(__file__).parent
    receipt["branch_source_sha256"] = {
        name: _sha(module_dir / name)
        for name in ("branches.py", "data.py", "diagnostics.py", "policy.py")}
    receipt["registered_utc"] = _now()
    receipt["pilot_reuse"] = {"index": 120,
                              "path": "branches/pilot_snapshot_0120_01"}
    _save_once(run_dir / "design/branch_collection_v1.json", receipt)
    return receipt


def _legacy_unreached_provenance(result_path: Path, result: dict, row: dict,
                                 source_binding: dict, manifest_signature: str | None):
    """Bind an old unknown receipt without altering its immutable producer file."""
    attempt = result_path.parent
    raw = attempt / "reference_capture.json.gz"
    plan_path = attempt / "plan.json"
    journal_path = attempt / "attempts.jsonl"
    if not manifest_signature or not all(p.is_file() for p in (raw, plan_path, journal_path)):
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        events = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            trajectory = json.load(stream)
        started = [e for e in events if e.get("event") == "started"
                   and e.get("attempt_type") == "snapshot_capture"]
        finished = [e for e in events if e.get("event") == "finished"
                    and e.get("attempt_type") == "snapshot_capture"]
        identity = trajectory.get("identity", {})
        summary = trajectory.get("summary", {})
        t = float(row["snapshot_minute"])
        observed = float(summary["observed_until_minute"])
        started_minute = float(started[0].get("minute")) if len(started) == 1 else math.nan
    except (OSError, EOFError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if (plan.get("schema") != "reliability_first.branch.v1"
            or plan.get("entry") != row
            or plan.get("manifest_signature") != manifest_signature
            or plan.get("source_sha256") != source_binding
            or len(started) != 1 or len(finished) != 1
            or started[0].get("scenario_id") != row["scenario_id"]
            or started_minute != t
            or finished[0].get("status") != result.get("reference_status")
            or finished[0].get("raw_sha256") != _sha(raw)
            or identity.get("scenario_id") != row["scenario_id"]
            or identity.get("group_id") != row["group_id"]
            or identity.get("manifest_signature") != manifest_signature
            or summary.get("status") != result.get("reference_status")
            or not math.isfinite(observed) or observed >= t
            or result.get("fit_rows") != [] or result.get("unknown") is not True):
        return None
    expected_role = "train" if row["experiment_role"] == "pilot_train" else "validation"
    for key, expected in (("schema", "reliability_first.branch_result.v1"),
                          ("group_id", row["group_id"]), ("role", expected_role),
                          ("source_sha256", source_binding)):
        if key in result and result[key] != expected:
            return None
    sidecar = {"schema": "reliability_first.legacy_terminal_provenance.v1",
               "status": "verified_snapshot_not_reached", "scenario_id": row["scenario_id"],
               "group_id": row["group_id"], "role": expected_role,
               "snapshot_minute": t, "manifest_signature": manifest_signature,
               "source_sha256": source_binding, "branch_result_sha256": _sha(result_path),
               "attempt_plan_sha256": _sha(plan_path),
               "attempt_journal_sha256": _sha(journal_path),
               "reference_capture_sha256": _sha(raw),
               "reference_status": summary["status"],
               "observed_until_minute": observed,
               "legacy_result_preserved": True}
    sidecar_path = attempt / "TERMINAL_PROVENANCE.json"
    if sidecar_path.exists():
        if json.loads(sidecar_path.read_text(encoding="utf-8")) != sidecar:
            raise ValueError("Legacy terminal provenance sidecar changed")
    else:
        try:
            _save_once(sidecar_path, sidecar)
        except FileExistsError:
            if json.loads(sidecar_path.read_text(encoding="utf-8")) != sidecar:
                raise ValueError("Legacy terminal provenance sidecar changed")
    return {**result, "schema": "reliability_first.branch_result.v1",
            "group_id": row["group_id"], "role": expected_role,
            "source_sha256": source_binding,
            "legacy_provenance_verified": True,
            "legacy_provenance_sidecar_sha256": _sha(sidecar_path)}


def _result_ok(result_path: Path, row: dict, source_binding: dict, *,
               manifest_signature: str) -> tuple[bool, dict | None]:
    if not result_path.is_file():
        return False, None
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False, None
    status = result.get("status")
    if status not in {"complete", "snapshot_not_reached", "D0_mismatch"}:
        return False, result
    if status == "D0_mismatch":
        raise RuntimeError("D0 identity mismatch: stop collection, preserve attempt")
    if (result.get("scenario_id") != row["scenario_id"]
            or result.get("snapshot_minute") != row["snapshot_minute"]):
        raise ValueError("Branch has different frozen scenario/clock")
    expected_role = "train" if row["experiment_role"] == "pilot_train" else "validation"
    if status == "snapshot_not_reached":
        normalized = _legacy_unreached_provenance(result_path, result, row,
                                                   source_binding, manifest_signature)
        return (normalized is not None), normalized or result
    plan_path = result_path.parent / "plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("Completed branch lacks registered attempt plan") from None
    if (plan.get("schema") != "reliability_first.branch.v1"
            or plan.get("entry") != row
            or plan.get("manifest_signature") != manifest_signature
            or plan.get("source_sha256") != source_binding):
        raise ValueError("Completed branch attempt plan differs from frozen manifest/source")
    if (result.get("schema") != "reliability_first.branch_result.v1"
            or result.get("group_id") != row["group_id"]
            or result.get("role") != expected_role
            or result.get("source_sha256") != source_binding):
        raise ValueError("Completed branch has different frozen role/group/source binding")
    if result.get("D0_passed") is not True:
        raise ValueError("Completed branch lacks passing D0 identity")
    return True, result


def registered_attempts(run_dir: Path, index: int, stage: str) -> list[Path]:
    """Return only successful, hash-bound collection journal attempts."""
    run_dir = Path(run_dir).resolve()
    base = (run_dir / "branches" / f"snapshot_{index:03d}").resolve()
    journal = run_dir / "branches/collection_attempts.jsonl"
    if not journal.is_file():
        return []
    starts = set()
    receipts = {}
    for line in journal.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("index") != index or record.get("stage") != stage:
            continue
        try:
            attempt = Path(record["attempt_directory"]).resolve()
        except (KeyError, TypeError):
            continue
        if (attempt.parent != base or not attempt.name.startswith("attempt_")
                or not attempt.name[8:].isdigit()):
            continue
        if record.get("event") == "started":
            starts.add(attempt)
        elif record.get("event") == "finished":
            if (attempt in starts and record.get("exit_code") == 0
                    and record.get("result_status") in {"complete", "snapshot_not_reached"}):
                result_path = attempt / "BRANCH_RESULT.json"
                if not result_path.is_file() or _sha(result_path) != record.get("result_sha256"):
                    raise ValueError(f"Registered branch result bytes changed: {attempt}")
                if _read_status(result_path) != record["result_status"]:
                    raise ValueError(f"Registered branch result status differs from journal: {attempt}")
                receipts[attempt] = record
    return sorted(receipts)


def _read_status(path: Path) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("status")
    except (OSError, json.JSONDecodeError):
        return None


def run(run_dir: Path, stage: str, *, max_new: int | None = None) -> dict:
    run_dir = Path(run_dir).resolve()
    selection = json.loads((run_dir / "design/branch_collection_v1.json").read_text(encoding="utf-8"))
    plan_path = run_dir / "design/snapshot_plan_v2.json"
    manifest_path = run_dir / "new_data/global_manifest.json"
    if (_sha(plan_path) != selection["plan_sha256"]
            or _sha(manifest_path) != selection["manifest_sha256"]):
        raise ValueError("Frozen collection source changed")
    manifest_signature = json.loads(manifest_path.read_text(encoding="utf-8"))["signature"]
    module_dir = Path(__file__).parent
    if selection["branch_source_sha256"] != {
        name: _sha(module_dir / name) for name in selection["branch_source_sha256"]}:
        raise ValueError("Frozen branch implementation changed")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if stage not in {"early", "remaining"}:
        raise ValueError("stage must be early or remaining")
    indices = selection[f"{stage}_indices"]
    journal = run_dir / "branches/collection_attempts.jsonl"
    status_path = run_dir / "branches/collection_status.json"
    complete = unknown = new = 0
    for ordinal, index in enumerate(indices):
        row = plan[index]
        if row["experiment_role"] not in {"pilot_train", "pilot_validation"}:
            raise ValueError("Development or blind background entered training collection")
        base = run_dir / "branches" / f"snapshot_{index:03d}"
        prior = ([run_dir / selection["pilot_reuse"]["path"]]
                 if index == selection["pilot_reuse"]["index"] else [])
        prior += registered_attempts(run_dir, index, stage)
        if len(prior) > 1:
            raise ValueError(f"Multiple registered successful branch attempts for {index}")
        found = None
        for attempt in prior:
            ok, result = _result_ok(attempt / "BRANCH_RESULT.json", row,
                                    selection["branch_source_sha256"],
                                    manifest_signature=manifest_signature)
            if ok:
                found = (attempt, result)
                break
        if found is not None:
            complete += 1
            unknown += int(found[1]["status"] != "complete")
            continue
        if max_new is not None and new >= max_new:
            break
        attempt_number = 1
        while (base / f"attempt_{attempt_number:04d}").exists():
            attempt_number += 1
        out = base / f"attempt_{attempt_number:04d}"
        base.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-B", "-m", "tools.reliability_first.branches",
               str(manifest_path), str(plan_path), str(index), str(out)]
        state = {"stage": stage, "state": "running", "index": index,
                 "ordinal": ordinal, "total": len(indices), "pid": os.getpid(),
                 "updated_utc": _now()}
        status_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        _append(journal, {"event": "started", "index": index, "stage": stage,
                          "attempt_directory": str(out), "command": cmd, "utc": _now()})
        started = time.monotonic()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        completed = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[2],
                                   env=env, text=True, encoding="utf-8",
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (base / f"attempt_{attempt_number:04d}.stdout.log").write_text(
            completed.stdout, encoding="utf-8")
        (base / f"attempt_{attempt_number:04d}.stderr.log").write_text(
            completed.stderr, encoding="utf-8")
        ok, result = _result_ok(out / "BRANCH_RESULT.json", row,
                                selection["branch_source_sha256"],
                                manifest_signature=manifest_signature)
        finish = {"event": "finished", "index": index, "stage": stage,
                  "attempt_directory": str(out), "exit_code": completed.returncode,
                  "elapsed_seconds": time.monotonic() - started,
                  "result_status": result.get("status") if result else None,
                  "result_sha256": _sha(out / "BRANCH_RESULT.json") if result else None,
                  "utc": _now()}
        _append(journal, finish)
        if completed.returncode != 0 or not ok:
            status_path.write_text(json.dumps(dict(state, state="failed", **finish),
                                              ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(f"Branch {index} failed; see {out} and preserved logs")
        new += 1
        complete += 1
        unknown += int(result["status"] != "complete")
    status = {"stage": stage, "state": "complete" if complete == len(indices) else "partial",
              "completed_indices": complete, "new_attempts": new, "unknown": unknown,
              "total": len(indices), "pid": os.getpid(), "updated_utc": _now()}
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register").add_argument("run_dir", type=Path)
    collect = sub.add_parser("run")
    collect.add_argument("run_dir", type=Path)
    collect.add_argument("--stage", required=True, choices=("early", "remaining"))
    collect.add_argument("--max-new", type=int)
    args = parser.parse_args()
    result = register(args.run_dir) if args.command == "register" else run(
        args.run_dir, args.stage, max_new=args.max_new)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
