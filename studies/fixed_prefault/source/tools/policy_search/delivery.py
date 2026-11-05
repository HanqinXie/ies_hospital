"""Audit-first, portable delivery of registered v5 policy comparisons.

Only registered complete-feedback batches are counted. Missing pairs, interrupted
attempts, failed commands and truncated targets remain visible. Raw trajectories
are SHA indexed outside the compact review ZIP, never silently represented by a
zero score. This module neither runs policies nor writes the scientific report.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import zlib
import zipfile

from tools.control_improvement.ranking import TARGET_NAMES


ROOT = Path(__file__).resolve().parents[2]
PAIR_SCHEMA = "policy_search.matched_full_feedback.v1"
DELIVERY_SCHEMA = "policy_search.delivery.v1"
SCORE_FIELDS = ("primary_score", "full_observation")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _crc(path: Path) -> int:
    value = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value = zlib.crc32(block, value)
    return value & 0xffffffff


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Delivery path must stay within v5 run: {path}") from error


def _pair_digest(scenario_id: str, policy_id: str) -> str:
    encoded = json.dumps([scenario_id, policy_id], sort_keys=True,
                         ensure_ascii=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _score(row: dict | None, field: str) -> dict:
    score = (row or {}).get(field) or {}
    targets = score.get("targets")
    if targets is None:
        values = [None] * len(TARGET_NAMES)
    else:
        if len(targets) != len(TARGET_NAMES):
            raise ValueError(f"Wrong target count in {field}: {len(targets)}")
        values = []
        for target in targets:
            if target is None:
                values.append(None)
            elif isinstance(target, (int, float)) and math.isfinite(float(target)):
                values.append(float(target))
            else:
                raise ValueError("Non-finite or nonnumeric v5 target")
    return {"full_horizon": bool(score.get("full_horizon")),
            "observed_minutes": score.get("observed_minutes"), "targets": values}


def _journal(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    entries = []
    with path.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(f"Malformed attempt journal {path}:{number}") from error
    return entries


def _collect_batch(run: Path, plan_path: Path, raw_files: dict) -> dict:
    plan = _read(plan_path)
    if plan.get("schema") != PAIR_SCHEMA:
        raise ValueError(f"Unsupported registered plan: {plan_path}")
    scenarios = list(plan["scenario_ids"])
    specs = list(plan["policy_specs"])
    ids = [spec["policy_id"] for spec in specs]
    if len(scenarios) != len(set(scenarios)) or len(ids) != len(set(ids)):
        raise ValueError("Duplicate registered scenario or policy")
    if plan["registered_pair_count"] != len(scenarios) * len(specs):
        raise ValueError("Registered pair count differs from plan cartesian product")
    out = plan_path.parent / "results"
    journal_path = out / "attempts.jsonl"
    events = _journal(journal_path)
    events_by_attempt = {}
    for event in events:
        key = (event.get("scenario_id"), event.get("policy_id"), event.get("attempt"))
        events_by_attempt.setdefault(key, []).append(event)
    consumed = set()
    pairs = []
    for sid in scenarios:
        for spec in specs:
            pid = spec["policy_id"]
            folder = out / "pairs" / _pair_digest(sid, pid)
            attempts = []
            for attempt in sorted(folder.glob("attempt_*")):
                if not attempt.is_dir():
                    continue
                result_path = attempt / "result.json"
                error_path = attempt / "error.json"
                result = _read(result_path) if result_path.is_file() else None
                if result is not None and (result.get("scenario_id") != sid or
                                           result.get("policy_id") != pid or
                                           result.get("plan_signature") != plan["signature"]):
                    raise ValueError(f"Result identity or registered plan differs: {result_path}")
                key = (sid, pid, attempt.name)
                consumed.add(key)
                attempt_events = events_by_attempt.get(key, [])
                item = {"attempt": attempt.name, "path": _relative(attempt, run),
                        "status": (result or {}).get("status", "attempt_started_no_result"),
                        "terminal": bool((result or {}).get("terminal")),
                        "result_path": _relative(result_path, run) if result_path.is_file() else None,
                        "result_sha256": _sha(result_path) if result_path.is_file() else None,
                        "error_path": _relative(error_path, run) if error_path.is_file() else None,
                        "error": _read(error_path) if error_path.is_file() else None,
                        "journal_events": attempt_events}
                raw_name = (result or {}).get("trajectory_path", "trajectory.json.gz")
                if Path(raw_name).is_absolute() or ".." in Path(raw_name).parts:
                    raise ValueError(f"Nonlocal raw trajectory reference: {raw_name}")
                raw_path = attempt / raw_name
                if raw_path.is_file():
                    actual = _sha(raw_path)
                    declared = (result or {}).get("trajectory_sha256")
                    if declared is not None and actual != declared:
                        raise ValueError(f"Raw trajectory SHA mismatch: {raw_path}")
                    raw_rel = _relative(raw_path, run)
                    item["raw_trajectory_path"] = raw_rel
                    item["raw_trajectory_sha256"] = actual
                    raw_files[raw_rel] = {"path": raw_rel, "sha256": actual,
                                          "bytes": raw_path.stat().st_size,
                                          "result_declared_sha256": declared,
                                          "registered_pair": True}
                elif result and result.get("trajectory_path"):
                    raise ValueError(f"Registered raw trajectory missing: {raw_path}")
                attempts.append(item)
            latest = attempts[-1] if attempts else None
            latest_result = (_read(Path(run / latest["result_path"]))
                             if latest and latest["result_path"] else None)
            pairs.append({"scenario_id": sid, "policy_id": pid, "kind": spec["kind"],
                          "status": latest["status"] if latest else "registered_not_attempted",
                          "terminal": bool(latest and latest["terminal"]),
                          "attempts": attempts,
                          "observed_until_minute": (latest_result or {}).get("observed_until_minute"),
                          "primary_score": _score(latest_result, "primary_score"),
                          "full_observation": _score(latest_result, "full_observation"),
                          "binding": (latest_result or {}).get("binding"),
                          "command_counts": (latest_result or {}).get("command_counts"),
                          "end_to_end_seconds": (latest_result or {}).get("end_to_end_seconds")})
    unmatched_events = [event for key, group in events_by_attempt.items()
                        if key not in consumed for event in group]
    summaries = []
    for path in sorted(out.glob("summary_*.json")):
        summary = _read(path)
        summaries.append({"path": _relative(path, run), "sha256": _sha(path),
                          "registered_pair_count": summary.get("registered_pair_count"),
                          "recorded_pair_count": summary.get("recorded_pair_count"),
                          "terminal_pair_count": summary.get("terminal_pair_count"),
                          "status_counts": summary.get("status_counts")})
    return {"plan_path": _relative(plan_path, run), "plan_sha256": _sha(plan_path),
            "plan_signature": plan["signature"], "phase": plan["phase"],
            "scenario_ids": scenarios, "policy_specs": specs,
            "registered_pair_count": plan["registered_pair_count"],
            "recorded_pair_count": sum(bool(row["attempts"]) for row in pairs),
            "terminal_pair_count": sum(row["terminal"] for row in pairs),
            "status_counts": dict(Counter(row["status"] for row in pairs)),
            "journal_path": _relative(journal_path, run) if journal_path.is_file() else None,
            "journal_sha256": _sha(journal_path) if journal_path.is_file() else None,
            "unmatched_journal_events": unmatched_events,
            "published_summaries": summaries, "pairs": pairs}


def _observability(batch: dict) -> dict:
    by_pair = {(row["scenario_id"], row["policy_id"]): row for row in batch["pairs"]}
    policies = {}
    for spec in batch["policy_specs"]:
        pid = spec["policy_id"]
        windows = {}
        for field in SCORE_FIELDS:
            observed = [0] * len(TARGET_NAMES)
            paired_observed = [0] * len(TARGET_NAMES)
            complete_ids = []
            unknown_ids = []
            new_ids = []
            resolved_ids = []
            worst_minutes = []
            worst_ens = {c: [] for c in ("e", "h", "c")}
            individual = {}
            for sid in batch["scenario_ids"]:
                candidate = by_pair[(sid, pid)]
                baseline = by_pair.get((sid, "secure_milp"))
                score = candidate[field]
                ref = baseline[field] if baseline else {"full_horizon": False,
                                                       "targets": [None] * len(TARGET_NAMES)}
                a, b = score["targets"], ref["targets"]
                delta = [x - y if x is not None and y is not None else None
                         for x, y in zip(a, b)]
                observed = [n + int(x is not None) for n, x in zip(observed, a)]
                paired_observed = [n + int(x is not None) for n, x in zip(paired_observed, delta)]
                complete = (score["full_horizon"] and ref["full_horizon"] and
                            all(x is not None for x in delta))
                (complete_ids if complete else unknown_ids).append(sid)
                if complete:
                    if b[0] == 0 and a[0] == 1:
                        new_ids.append(sid)
                    if b[0] == 1 and a[0] == 0:
                        resolved_ids.append(sid)
                    if delta[1] > 0:
                        worst_minutes.append({"scenario_id": sid,
                                              "increase_minutes": delta[1],
                                              "candidate_minutes": a[1],
                                              "secure_minutes": b[1]})
                    for index, carrier in enumerate(("e", "h", "c"), start=2):
                        if delta[index] > 0:
                            worst_ens[carrier].append({"scenario_id": sid,
                                                       "increase_kWh": delta[index],
                                                       "candidate_kWh": a[index],
                                                       "secure_kWh": b[index]})
                individual[sid] = {"candidate_status": candidate["status"],
                                   "baseline_status": baseline["status"] if baseline else None,
                                   "complete_paired_window": bool(complete),
                                   "candidate_targets": a, "secure_targets": b,
                                   "delta_targets": delta}
            worst_minutes.sort(key=lambda row: (-row["increase_minutes"], row["scenario_id"]))
            for carrier in worst_ens:
                worst_ens[carrier].sort(key=lambda row: (-row["increase_kWh"],
                                                     row["scenario_id"]))
            windows[field] = {"registered_case_count": len(batch["scenario_ids"]),
                              "full_horizon_case_count": sum(by_pair[(sid, pid)][field]["full_horizon"]
                                                             for sid in batch["scenario_ids"]),
                              "complete_paired_case_count": len(complete_ids),
                              "complete_paired_case_ids": complete_ids,
                              "unknown_case_ids": unknown_ids,
                              "observable_count_by_target": dict(zip(TARGET_NAMES, observed)),
                              "observable_paired_count_by_target": dict(zip(TARGET_NAMES, paired_observed)),
                              "new_P0_failure_case_ids": new_ids,
                              "resolved_P0_failure_case_ids": resolved_ids,
                              "worst_P0_failure_minute_cases": worst_minutes[:10],
                              "worst_P0_ENS_cases_by_carrier": {c: rows[:10]
                                                                 for c, rows in worst_ens.items()},
                              "individual_cases": individual}
        policies[pid] = windows
    return {"plan_path": batch["plan_path"], "phase": batch["phase"],
            "target_names": list(TARGET_NAMES), "policies": policies}


def _receipts(run: Path) -> dict:
    folder = run / "logs"
    receipts = []
    for path in sorted(folder.glob("*.command.json")):
        try:
            body = _read(path)
        except (UnicodeError, json.JSONDecodeError) as error:
            body = {"parse_error": str(error)}
        stem = path.name[:-len(".command.json")]
        logs = {}
        for kind in ("stdout", "stderr"):
            log = folder / f"{stem}.{kind}.log"
            logs[kind] = ({"path": _relative(log, run), "sha256": _sha(log),
                           "bytes": log.stat().st_size} if log.is_file() else None)
        receipts.append({"path": _relative(path, run), "sha256": _sha(path),
                         "name": body.get("name"), "command": body.get("command"),
                         "started_utc": body.get("started_utc"),
                         "finished_utc": body.get("finished_utc"),
                         "elapsed_seconds": body.get("elapsed_seconds"),
                         "exit_code": body.get("exit_code"),
                         "parse_error": body.get("parse_error"), "logs": logs})
    ledger = run / "commands_and_exit_codes.txt"
    return {"schema": DELIVERY_SCHEMA + ".command_receipts",
            "receipts": receipts,
            "exit_code_counts": dict(Counter(str(r["exit_code"]) for r in receipts)),
            "command_ledger_path": _relative(ledger, run) if ledger.is_file() else None,
            "command_ledger_sha256": _sha(ledger) if ledger.is_file() else None,
            "unpaired_log_files": [_relative(p, run) for p in sorted(folder.glob("*.log"))
                                   if not (folder / (p.name.rsplit(".", 2)[0] + ".command.json")).is_file()]}


def _inventory(run: Path, plans: list[Path]) -> dict:
    registered_source = {}
    registered_data = {}
    model_files = {}
    for path in plans:
        plan = _read(path)
        for name, digest in plan.get("source_sha256", {}).items():
            registered_source.setdefault(name, set()).add(digest)
        if plan.get("manifest_path"):
            registered_data.setdefault(str(Path(plan["manifest_path"]).resolve()), set()).add(
                plan.get("manifest_sha256"))
        if plan.get("preference_path"):
            registered_data.setdefault(str(Path(plan["preference_path"]).resolve()), set()).add(
                plan.get("preference_sha256"))
        for pid, binding in plan.get("model_bindings", {}).items():
            if binding is None:
                continue
            directory = Path(binding["directory"])
            for relative, digest in binding["files"].items():
                model_files.setdefault(str((directory / relative).resolve()), []).append(
                    {"policy_id": pid, "registered_sha256": digest})
    source_paths = {p.resolve() for p in (ROOT / "tools/policy_search").glob("*.py")}
    source_paths.update(p.resolve() for p in (ROOT / "tests").glob("test_v5_*.py"))
    source_paths.update((ROOT / name).resolve() for name in registered_source)
    data_paths = {p.resolve() for directory in ("new_data", "design")
                  for p in (run / directory).rglob("*") if p.is_file()}
    data_paths.update(Path(name) for name in registered_data)
    model_paths = {Path(name) for name in model_files}
    for p in run.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".pt", ".pth", ".onnx", ".safetensors", ".joblib"):
            model_paths.add(p.resolve())

    def entries(paths, registered):
        rows = []
        for path in sorted(paths):
            try:
                label = path.relative_to(run).as_posix()
                location = "v5_run"
            except ValueError:
                try:
                    label = path.relative_to(ROOT).as_posix()
                    location = "workspace"
                except ValueError:
                    label = str(path)
                    location = "external"
            refs = registered.get(str(path), []) if isinstance(registered, dict) else []
            if isinstance(refs, set):
                refs = sorted(refs)
            if path.is_file():
                actual = _sha(path)
                rows.append({"path": label, "location": location, "bytes": path.stat().st_size,
                             "sha256": actual, "registered_bindings": refs,
                             "matches_all_registered_bindings":
                                 all(actual == (v if isinstance(v, str) else v["registered_sha256"])
                                     for v in refs)})
            else:
                rows.append({"path": label, "location": location, "bytes": None,
                             "sha256": None, "registered_bindings": refs,
                             "matches_all_registered_bindings": False,
                             "status": "registered_file_missing"})
        return rows

    source_refs = {str((ROOT / name).resolve()): values
                   for name, values in registered_source.items()}
    return {"schema": DELIVERY_SCHEMA + ".source_data_model_inventory",
            "source": entries(source_paths, source_refs),
            "data": entries(data_paths, registered_data),
            "model": entries(model_paths, model_files)}


def compile_ledger(run_dir: str | Path) -> dict:
    """Read existing v5 artifacts; report all registered pairs without writing."""
    run = Path(run_dir).resolve()
    if not run.is_dir():
        raise FileNotFoundError(run)
    plans = sorted(path for path in run.rglob("registered_plan.json")
                   if path.parent.name != "results")
    if not plans:
        raise ValueError("No registered v5 complete-feedback batches")
    raw_files = {}
    batches = [_collect_batch(run, path, raw_files) for path in plans]
    for path in run.rglob("trajectory.json.gz"):
        label = _relative(path, run)
        if label not in raw_files:
            raw_files[label] = {"path": label, "sha256": _sha(path),
                                "bytes": path.stat().st_size,
                                "result_declared_sha256": None,
                                "registered_pair": False}
    return {"pair_ledger": {"schema": DELIVERY_SCHEMA + ".all_pairs",
                            "batches": batches,
                            "totals": {"registered_pair_count": sum(b["registered_pair_count"] for b in batches),
                                       "terminal_pair_count": sum(b["terminal_pair_count"] for b in batches),
                                       "attempt_count": sum(len(p["attempts"]) for b in batches
                                                            for p in b["pairs"])},
                            "missing_and_unknown_never_imputed": True},
            "observability": {"schema": DELIVERY_SCHEMA + ".observability",
                              "batches": [_observability(batch) for batch in batches]},
            "raw_trajectory_index": {"schema": DELIVERY_SCHEMA + ".external_raw_sha256",
                                     "files": [raw_files[key] for key in sorted(raw_files)],
                                     "raw_trajectories_in_review_zip": False},
            "command_receipts": _receipts(run),
            "source_data_model_inventory": _inventory(run, plans)}


def _archive_candidates(run: Path, report: Path, generated: list[Path], inventory: dict) -> list[tuple[Path, str]]:
    selected = {}

    def add(path: Path, arcname: str | None = None):
        if not path.is_file():
            return
        name = arcname or _relative(path, run)
        pure = Path(name)
        if pure.is_absolute() or ".." in pure.parts or ":" in name:
            raise ValueError(f"Nonportable ZIP entry: {name}")
        if name in selected and selected[name] != path:
            raise ValueError(f"Review ZIP path collision: {name}")
        selected[name] = path

    add(report)
    add(run / "review/RUN_ACCOUNTING.json")
    for path in generated:
        add(path)
    for directory in ("design", "new_data", "diagnostics", "logs"):
        for path in (run / directory).rglob("*"):
            if path.is_file():
                add(path)
    add(run / "commands_and_exit_codes.txt")
    for plan in run.rglob("registered_plan.json"):
        if plan.parent.name != "results":
            add(plan)
    # Keep the compact audit and comparison evidence beside each registered
    # plan. Raw per-step trajectories remain outside the portable archive.
    for name in ("SEARCH_REGISTRATION.json", "ALTERNATIVE_REGISTRATION.json",
                 "MODE_V2_REGISTRATION.json", "MODE_V2_SELECTION_REGISTRATION.json",
                 "SEARCH_REGISTRATION_AUDIT_COMPAT.json", "MODE_V2_REVIEW.json",
                 "MODE_V2_SELECTION_REVIEW.json", "EXPOSURE_CLASSIFICATION.json",
                 "STAGE_REGISTRATION.json",
                 "SENSITIVITY_PREREGISTRATION.json", "AUDIT.json", "SCORE_RECHECK.json",
                 "ANALYSIS.json", "ANALYSIS_PREFERENCE_V2.json",
                 "ANALYSIS_FULL_WINDOW.json", "GATE_COVERAGE_P0_TRADEOFF.json",
                 "ACTION_EXECUTION_COMPARISON.json",
                 "ACTION_EXECUTION_COMPARISON_v1_result_bound.json",
                 "SENSITIVITY_BY_CONDITION_PRESSURE.json"):
        for path in run.rglob(name):
            add(path)
    for row in inventory["source"] + inventory["model"]:
        if row["sha256"] is None:
            continue
        if row["location"] == "v5_run":
            add(run / row["path"])
        elif row["location"] == "workspace":
            add(ROOT / row["path"], "source/" + row["path"])
        else:
            path = Path(row["path"])
            add(path, "external_model/" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
                + "/" + path.name)
    # The post-run verifier and source-bound report generator are introduced
    # after policy/source registration. Keep their exact reviewable bytes in
    # the portable package without pretending they were frozen policy inputs.
    for name in ("tools/policy_search/finalize.py",
                 "tools/policy_search/final_report.py",
                 "tests/test_v5_finalize.py",
                 "tests/test_v5_final_report.py"):
        add(ROOT / name, "source/" + name)
    return [(path, name) for name, path in sorted(selected.items())]


def build_delivery(run_dir: str | Path, report_path: str | Path | None = None,
                   review_zip_path: str | Path | None = None) -> dict:
    """Write one final review bundle after the actual report exists.

    The caller owns the scientific conclusion. This function verifies raw SHA,
    writes the complete evidence ledger, and packages compact review inputs.
    Existing delivery files are never overwritten.
    """
    run = Path(run_dir).resolve()
    report = Path(report_path).resolve() if report_path is not None else run / "POLICY_SEARCH_REPORT.md"
    archive = (Path(review_zip_path).resolve() if review_zip_path is not None
               else run / "review/POLICY_SEARCH_REVIEW.zip")
    _relative(report, run)
    _relative(archive, run)
    if not report.is_file():
        raise FileNotFoundError(f"Actual POLICY_SEARCH_REPORT.md required before delivery: {report}")
    sidecar = archive.with_name(archive.name + ".sha256")
    manifest_path = run / "review/REVIEW_ZIP_MANIFEST.json"
    for path in (archive, sidecar, manifest_path):
        if path.exists():
            raise FileExistsError(path)
    evidence = compile_ledger(run)
    review = run / "review"
    paths = {
        "pair_ledger": review / "POLICY_PAIR_LEDGER.json",
        "observability": review / "OBSERVABILITY.json",
        "raw_trajectory_index": review / "RAW_TRAJECTORY_SHA256.json",
        "command_receipts": review / "COMMAND_RECEIPTS.json",
        "source_data_model_inventory": review / "SOURCE_DATA_MODEL_INVENTORY.json",
    }
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("Existing review ledger; use a new run/version for a changed outcome")
    for key, path in paths.items():
        _write_new(path, evidence[key])
    candidates = _archive_candidates(run, report, list(paths.values()),
                                     evidence["source_data_model_inventory"])
    manifest = {"schema": DELIVERY_SCHEMA + ".archive_manifest",
                "entry_root": "v5_run_or_source",
                "entries": [{"archive_path": name, "bytes": path.stat().st_size,
                             "sha256": _sha(path), "crc32": _crc(path)}
                            for path, name in candidates],
                "manifest_self_excluded_from_entries": True,
                "raw_trajectories_excluded": True}
    _write_new(manifest_path, manifest)
    candidates.append((manifest_path, _relative(manifest_path, run)))
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for path, name in candidates:
            zf.write(path, arcname=name)
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise ValueError(f"Review ZIP CRC failure: {bad}")
        for row in manifest["entries"]:
            if hashlib.sha256(zf.read(row["archive_path"])).hexdigest() != row["sha256"]:
                raise ValueError(f"Review ZIP SHA mismatch: {row['archive_path']}")
    digest = _sha(archive)
    with sidecar.open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {archive.name}\n")
    return {"review_zip_path": str(archive), "review_zip_sha256": digest,
            "review_zip_bytes": archive.stat().st_size,
            "manifest_path": str(manifest_path),
            "ledger_paths": {key: str(path) for key, path in paths.items()},
            "registered_pair_count": evidence["pair_ledger"]["totals"]["registered_pair_count"],
            "terminal_pair_count": evidence["pair_ledger"]["totals"]["terminal_pair_count"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--review-zip", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_delivery(args.run_dir, args.report, args.review_zip),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
