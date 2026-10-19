"""Read-only inventory and conservative budget ledger for the v6 run.

Directory-backed attempts, including exceptions and interrupted/orphaned
attempts, are the trajectory numerator. Runner terminal counts are receipts,
not proof of a full scored episode. Old v5 training specifications are reused
support and never counted as new v6 exogenous specifications.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest


SCHEMA = "policy_redesign.v6.all_attempts_ledger.v1"
REGISTERED_PLAN_SCHEMAS = frozenset({
    "policy_redesign.matched_full_feedback.v1",
    "policy_redesign.regime_matched_full_feedback.v1",
    "policy_redesign.security_matched_full_feedback.v1",
    "policy_redesign.reserve_diagnostic.v1.plan",
})
LIMITS = {"actual_environment_attempts": 30000,
          "new_exogenous_specs": 1500, "major_recipes": 80,
          "confirmed_optimizer_fits": 240, "local_experiment_wall_hours": 96}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _rel(path, run):
    return Path(path).resolve().relative_to(Path(run).resolve()).as_posix()


def _plan_paths(run):
    paths = []
    for path in sorted(Path(run).rglob("registered_plan.json")):
        if path.parent.name == "results" and (path.parent.parent / path.name).is_file():
            continue  # runner's identical copy, never a second registered batch
        paths.append(path)
    return paths


def _batch_for_plan(path):
    return path.parent.parent if path.parent.name == "results" else path.parent


def _inventory_plans(run, errors):
    plans = []
    pair_lookup = {}
    for path in _plan_paths(run):
        try:
            plan = _read(path)
            if plan.get("schema") not in REGISTERED_PLAN_SCHEMAS:
                errors.append(f"{_rel(path, run)}: unrecognized registered plan schema")
                continue
            signature = plan.get("signature")
            if signature != digest({k: v for k, v in plan.items() if k != "signature"}):
                errors.append(f"{_rel(path, run)}: plan signature differs")
            manifest_path = Path(plan["manifest_path"])
            if not manifest_path.is_file() or _sha(manifest_path) != plan["manifest_sha256"]:
                errors.append(f"{_rel(path, run)}: registered manifest SHA differs or is absent")
            ids = plan["scenario_ids"]
            specs = plan["policy_specs"]
            if (len(ids) != len(set(ids)) or
                    len({spec["policy_id"] for spec in specs}) != len(specs) or
                    plan.get("registered_pair_count") != len(ids) * len(specs)):
                errors.append(f"{_rel(path, run)}: registered pair denominator malformed")
            batch = _batch_for_plan(path)
            results = batch / "results"
            copied = results / "registered_plan.json"
            if copied.is_file() and _read(copied) != plan:
                errors.append(f"{_rel(path, run)}: runner plan copy differs")
            summaries = sorted(results.glob("summary_*.json"))
            summary = None
            if summaries:
                try:
                    summary = _read(summaries[-1])
                    if summary.get("plan_signature") != signature:
                        errors.append(f"{_rel(summaries[-1], run)}: summary plan signature differs")
                except (OSError, ValueError, TypeError) as error:
                    errors.append(f"{_rel(summaries[-1], run)}: malformed summary {error}")
            descriptor = {"plan_path": _rel(path, run), "plan_sha256": _sha(path),
                          "signature": signature, "manifest_path": str(manifest_path),
                          "manifest_sha256": plan["manifest_sha256"],
                          "phase": plan.get("phase"), "scenario_ids": ids,
                          "policy_specs": specs, "registered_pair_count": len(ids) * len(specs),
                          "latest_summary_path": None if not summaries else _rel(summaries[-1], run),
                          "latest_summary_sha256": None if not summaries else _sha(summaries[-1]),
                          "runner_terminal_receipt_count": None if summary is None else
                          summary.get("terminal_pair_count"),
                          "runner_recorded_receipt_count": None if summary is None else
                          summary.get("recorded_pair_count"),
                          "runner_matched_receipt": None if summary is None else
                          summary.get("matched_initial_state_and_stream")}
            plans.append(descriptor)
            for sid in ids:
                for spec in specs:
                    pid = spec["policy_id"]
                    pair = (results / "pairs" / digest([sid, pid])).resolve()
                    if pair in pair_lookup:
                        errors.append(f"{_rel(pair, run)}: pair directory registered twice")
                    pair_lookup[pair] = {"plan_path": descriptor["plan_path"],
                                         "phase": descriptor["phase"],
                                         "scenario_id": sid, "policy_id": pid,
                                         "kind": spec["kind"]}
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"{_rel(path, run)}: cannot inventory plan: {type(error).__name__}: {error}")
    return plans, pair_lookup


def _attempts(run, pair_lookup, errors):
    attempts = []
    raw_index = []
    raw_seen = set()
    for directory in sorted(Path(run).rglob("attempt_*")):
        if not directory.is_dir() or not directory.name[8:].isdigit():
            continue
        parent = directory.parent.resolve()
        expected = pair_lookup.get(parent)
        if expected is None:
            errors.append(f"{_rel(directory, run)}: unregistered environment attempt directory")
        result_path = directory / "result.json"
        error_path = directory / "error.json"
        raw_path = directory / "trajectory.json.gz"
        row = None
        if result_path.is_file():
            try:
                row = _read(result_path)
            except (OSError, ValueError, TypeError) as error:
                errors.append(f"{_rel(result_path, run)}: malformed result {error}")
        declared = None if row is None else row.get("trajectory_sha256")
        actual_raw_sha = _sha(raw_path) if raw_path.is_file() else None
        if raw_path.is_file():
            raw_seen.add(raw_path.resolve())
            raw_index.append({"path": _rel(raw_path, run), "sha256": actual_raw_sha,
                              "bytes": raw_path.stat().st_size,
                              "declared_sha256": declared,
                              "status": None if row is None else row.get("status")})
        if declared is not None and declared != actual_raw_sha:
            errors.append(f"{_rel(directory, run)}: raw SHA differs from result receipt")
        if row is not None and row.get("trajectory_path"):
            if row["trajectory_path"] != "trajectory.json.gz" or not raw_path.is_file():
                errors.append(f"{_rel(directory, run)}: declared raw path missing/malformed")
        if row is not None and expected is not None and (
                row.get("scenario_id") != expected["scenario_id"] or
                row.get("policy_id") != expected["policy_id"]):
            errors.append(f"{_rel(directory, run)}: result pair identity differs from registered plan")
        if row is not None and row.get("error_path") and not error_path.is_file():
            errors.append(f"{_rel(directory, run)}: declared error receipt absent")
        terminal = None if row is None else row.get("terminal")
        primary = None if row is None else (row.get("primary_score") or {}).get("full_horizon")
        full = None if row is None else (row.get("full_observation") or {}).get("full_horizon")
        if row is None:
            outcome_class = "unknown_no_receipt"
        elif terminal is not True:
            outcome_class = "nonterminal_unknown"
        elif primary is not True:
            outcome_class = "terminal_unknown_primary"
        elif full is not True:
            outcome_class = "terminal_unknown_full_observation"
        else:
            outcome_class = "terminal_full_both_windows"
        attempts.append({"attempt_path": _rel(directory, run),
                         **({"plan_path": None, "phase": None, "scenario_id": None,
                             "policy_id": None, "kind": None} if expected is None else expected),
                         "attempt_number": int(directory.name[8:]),
                         "result_path": _rel(result_path, run) if result_path.is_file() else None,
                         "result_sha256": _sha(result_path) if result_path.is_file() else None,
                         "status": None if row is None else row.get("status"),
                         "terminal": terminal, "primary_full_horizon": primary,
                         "full_observation_horizon": full,
                         "outcome_class": outcome_class,
                         "raw_path": _rel(raw_path, run) if raw_path.is_file() else None,
                         "raw_sha256": actual_raw_sha,
                         "declared_raw_sha256": declared,
                         "error_path": _rel(error_path, run) if error_path.is_file() else None,
                         "error_sha256": _sha(error_path) if error_path.is_file() else None})
    for path in sorted(Path(run).rglob("trajectory.json.gz")):
        if path.resolve() not in raw_seen:
            raw_index.append({"path": _rel(path, run), "sha256": _sha(path),
                              "bytes": path.stat().st_size, "declared_sha256": None,
                              "status": "unregistered_raw"})
            errors.append(f"{_rel(path, run)}: raw trajectory outside numbered environment attempt")
    return attempts, raw_index


def _registered_pair_status(plans, attempts):
    by_pair = {}
    for attempt in attempts:
        if attempt["plan_path"] is None:
            continue
        key = (attempt["plan_path"], attempt["scenario_id"], attempt["policy_id"])
        by_pair.setdefault(key, []).append(attempt)
    unknown_primary = []
    unknown_records = []
    latest_terminal = 0
    expected_count = 0
    for plan in plans:
        for sid in plan["scenario_ids"]:
            for spec in plan["policy_specs"]:
                expected_count += 1
                key = (plan["plan_path"], sid, spec["policy_id"])
                group = sorted(by_pair.get(key, []), key=lambda row: row["attempt_number"])
                latest = group[-1] if group else None
                if latest is not None and latest["terminal"] is True:
                    latest_terminal += 1
                if latest is None or latest["primary_full_horizon"] is not True:
                    unknown_primary.append(f"{sid}/{spec['policy_id']}")
                    unknown_records.append({
                        "plan_path": plan["plan_path"], "scenario_id": sid,
                        "policy_id": spec["policy_id"],
                        "attempt_path": None if latest is None else latest["attempt_path"],
                        "status": None if latest is None else latest["status"],
                        "reason": "registered_pair_unattempted" if latest is None else
                        "latest_nonterminal_or_no_result" if latest["terminal"] is not True else
                        "latest_terminal_primary_unknown",
                        "raw_sha256": None if latest is None else latest["raw_sha256"]})
    return {"registered_pair_count": expected_count,
            "latest_terminal_receipt_count": latest_terminal,
            "unknown_primary_pair_ids": sorted(unknown_primary),
            "unknown_pair_records": unknown_records,
            "all_registered_latest_receipts_terminal": bool(expected_count and latest_terminal == expected_count)}


def _manifest_counts(run, v5_manifest_path, plans, errors):
    new_path = Path(run) / "new_data/global_manifest.json"
    new_count = 0
    new_ids = set()
    new_sha = None
    if new_path.is_file():
        try:
            new = _read(new_path)
            ids = [scene["id"] for scene in new["scenarios"]]
            if len(ids) != len(set(ids)):
                errors.append("v6 new-data manifest has duplicate scenario IDs")
            new_ids = set(ids)
            new_count = len(new_ids)
            new_sha = _sha(new_path)
            if any(scene.get("is_final_blind") for scene in new["scenarios"]):
                errors.append("v6 new-data manifest contains sealed final background")
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"v6 new-data manifest malformed: {error}")
    else:
        errors.append("v6 new-data manifest absent")
    old_path = Path(v5_manifest_path)
    old_sha = None
    old_train = set()
    if old_path.is_file():
        try:
            old = _read(old_path)
            old_sha = _sha(old_path)
            old_train = {scene["id"] for scene in old["scenarios"]
                         if scene.get("experiment_role") == "train" and
                         not scene.get("is_final_blind")}
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"v5 reused manifest malformed: {error}")
    used = {sid for plan in plans if plan["manifest_sha256"] == old_sha
            for sid in plan["scenario_ids"] if sid in old_train}
    return {"v6_manifest_path": _rel(new_path, run) if new_path.is_file() else None,
            "v6_manifest_sha256": new_sha, "v6_new_spec_ids": sorted(new_ids),
            "v6_new_spec_count": new_count,
            "v5_manifest_path": str(old_path.resolve()), "v5_manifest_sha256": old_sha,
            "reused_v5_manifest_train_spec_count": len(old_train),
            "reused_v5_train_spec_ids_in_v6_plans": sorted(used)}


def _fits(run, errors):
    roots = set()
    for name in ("fit_started.json", "fit_error.json", "fit_report.json"):
        roots.update(path.parent for path in (Path(run) / "learning").rglob(name))
    fits = []
    for directory in sorted(roots):
        paths = {name: directory / name for name in
                 ("fit_started.json", "fit_error.json", "fit_report.json", "metadata.json")}
        objects = {}
        for name, path in paths.items():
            if path.is_file():
                try:
                    objects[name] = _read(path)
                except (OSError, ValueError, TypeError) as error:
                    errors.append(f"{_rel(path, run)}: malformed fit artifact {error}")
        started, failure, report = (objects.get(name) for name in
                                    ("fit_started.json", "fit_error.json", "fit_report.json"))
        confirmed = bool(report and report.get("optimizer_updates", 0) > 0)
        prefit_failure = bool(failure and "before CUDA initialization" in
                              str(failure.get("failure_stage", "")))
        stale = (directory / "SOURCE_STALE_AFTER_RANK_FIX.json").is_file()
        fits.append({"directory": _rel(directory, run),
                     "fit_started_sha256": _sha(paths["fit_started.json"])
                     if paths["fit_started.json"].is_file() else None,
                     "fit_error_sha256": _sha(paths["fit_error.json"])
                     if paths["fit_error.json"].is_file() else None,
                     "fit_report_sha256": _sha(paths["fit_report.json"])
                     if paths["fit_report.json"].is_file() else None,
                     "metadata_sha256": _sha(paths["metadata.json"])
                     if paths["metadata.json"].is_file() else None,
                     "status": "confirmed_optimizer_fit" if confirmed else
                     "prefit_failed" if prefit_failure else "fit_progress_unknown",
                     "actual_optimizer_fit_confirmed": confirmed,
                     "prefit_failure_without_cuda": prefit_failure,
                     "source_stale_marker": stale,
                     "architecture": None if report is None else report.get("architecture"),
                     "hidden": (report or objects.get("metadata.json") or {}).get("hidden"),
                     "fit_role": (report or started or {}).get("fit_role"),
                     "actual_train_device": None if report is None else
                     report.get("actual_train_device"),
                     "optimizer_updates": None if report is None else
                     report.get("optimizer_updates"),
                     "failure_stage": None if failure is None else failure.get("failure_stage"),
                     "error_type": None if failure is None else failure.get("error_type")})
    return fits


def _recipes(plans, fits):
    recipes = {}
    for plan in plans:
        for spec in plan["policy_specs"]:
            if spec["kind"] == "v6_feedback":
                key = "v6_feedback:" + spec["family"]
                recipes[key] = {"recipe_id": key,
                                "basis": "registered feedback action representation",
                                "candidate_theta_vectors_are_not_new_major_recipes": True}
            elif spec["kind"] in ("regime_feedback", "security_feedback",
                                  "reserve_feedback"):
                key = spec["kind"]
                recipes[key] = {"recipe_id": key,
                                "basis": "registered feedback action representation",
                                "candidate_theta_vectors_are_not_new_major_recipes": True}
    unclassified = []
    for fit in fits:
        if fit["architecture"]:
            key = f"v6_neural:{fit['architecture']}:hidden={fit['hidden']}"
            recipes[key] = {"recipe_id": key,
                            "basis": "actual neural fit architecture/width",
                            "source_stale_in_any_fit": fit["source_stale_marker"]}
        elif fit["status"] != "prefit_failed":
            unclassified.append(fit["directory"])
    return list(sorted(recipes.values(), key=lambda row: row["recipe_id"])), unclassified


def _parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _commands(run, errors):
    sources = []
    global_file = Path(run) / "commands_and_exit_codes.txt"
    if global_file.is_file():
        for number, line in enumerate(global_file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                sources.append((_rel(global_file, run) + f":{number}", json.loads(line)))
            except (ValueError, TypeError) as error:
                errors.append(f"{_rel(global_file, run)}:{number}: malformed command receipt {error}")
    for path in sorted((Path(run) / "logs").glob("*.command.json")):
        try:
            sources.append((_rel(path, run), _read(path)))
        except (OSError, ValueError, TypeError) as error:
            errors.append(f"{_rel(path, run)}: malformed command receipt {error}")
    by = {}
    for source, row in sources:
        key = (row.get("name"), row.get("started_utc"), row.get("finished_utc"),
               json.dumps(row.get("command"), sort_keys=True, ensure_ascii=False))
        if key not in by:
            by[key] = {"name": row.get("name"), "command": row.get("command"),
                       "started_utc": row.get("started_utc"),
                       "finished_utc": row.get("finished_utc"),
                       "elapsed_seconds": row.get("elapsed_seconds"),
                       "exit_code": row.get("exit_code"), "sources": []}
        by[key]["sources"].append(source)
    receipts = sorted(by.values(), key=lambda row: (str(row["started_utc"]), str(row["name"])))
    intervals = []
    for row in receipts:
        try:
            if row["finished_utc"] is None:
                continue
            start, end = _parse_time(row["started_utc"]), _parse_time(row["finished_utc"])
            if end < start:
                raise ValueError("finished before started")
            intervals.append((start, end))
        except (ValueError, TypeError, AttributeError) as error:
            errors.append(f"command {row['name']}: invalid time interval {error}")
    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    union_seconds = sum((end - start).total_seconds() for start, end in merged)
    return receipts, union_seconds


def _search_designs(run, errors):
    designs = []
    for path in sorted(Path(run).rglob("search_plan.json")):
        try:
            row = _read(path)
            if row.get("schema") != "policy_redesign.adaptive_feedback_search.v1.plan":
                errors.append(f"{_rel(path, run)}: unrecognized search plan schema")
            evaluations = sorted(path.parent.rglob("evaluation.json"))
            designs.append({"path": _rel(path, run), "sha256": _sha(path),
                            "seeds": row.get("seeds"),
                            "min_generations": row.get("min_generations"),
                            "max_generations": row.get("max_generations"),
                            "registered_common_case_count": len(row.get("scenario_ids", [])),
                            "saved_generation_evaluations": [
                                {"path": _rel(p, run), "sha256": _sha(p)} for p in evaluations]})
        except (OSError, ValueError, TypeError) as error:
            errors.append(f"{_rel(path, run)}: malformed search design {error}")
    return designs


def _atomic_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False,
                                     dir=path.parent, prefix=path.name + ".tmp_") as stream:
        temporary = Path(stream.name)
        stream.write(value)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _csv_text(attempts):
    import io
    stream = io.StringIO(newline="")
    fields = ("attempt_path", "plan_path", "phase", "scenario_id", "policy_id",
              "attempt_number", "status", "terminal", "primary_full_horizon",
              "full_observation_horizon", "outcome_class", "raw_path", "raw_sha256",
              "result_path", "result_sha256", "error_path", "error_sha256")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(attempts)
    return stream.getvalue()


def build_ledger(run_dir, *, v5_manifest_path=None, write_outputs=False,
                 output_json=None, output_csv=None):
    """Read saved v6 evidence; optionally atomically write refreshed review files."""
    run = Path(run_dir).resolve()
    if not run.is_dir():
        raise FileNotFoundError(run)
    old = (Path(v5_manifest_path).resolve() if v5_manifest_path is not None else
           ROOT / "outputs/v5_policy_search_20261015_run01/new_data/global_manifest.json")
    errors = []
    plans, pair_lookup = _inventory_plans(run, errors)
    attempts, raw_index = _attempts(run, pair_lookup, errors)
    pair_status = _registered_pair_status(plans, attempts)
    manifests = _manifest_counts(run, old, plans, errors)
    fits = _fits(run, errors)
    recipes, unclassified_recipes = _recipes(plans, fits)
    receipts, wall_seconds = _commands(run, errors)
    designs = _search_designs(run, errors)
    unknowns = []
    for attempt in attempts:
        if attempt["outcome_class"] != "terminal_full_both_windows":
            reason = ("attempt_without_result_receipt" if attempt["result_path"] is None else
                      attempt["outcome_class"])
            unknowns.append({"attempt_path": attempt["attempt_path"],
                             "scenario_id": attempt["scenario_id"],
                             "policy_id": attempt["policy_id"],
                             "status": attempt["status"], "reason": reason,
                             "raw_sha256": attempt["raw_sha256"]})
    unknowns.extend(pair_status["unknown_pair_records"])
    failed = [row for row in attempts if row["terminal"] is not True or
              row["status"] in ("runner_exception", "runner_interrupted")]
    failed_commands = [row for row in receipts if row["exit_code"] not in (0, None)]
    fit_errors = [row for row in fits if row["fit_error_sha256"] is not None]
    confirmed = sum(row["actual_optimizer_fit_confirmed"] for row in fits)
    uncertain_fits = sum(row["status"] == "fit_progress_unknown" for row in fits)
    attempt_count = len(attempts)
    new_count = manifests["v6_new_spec_count"]
    recipe_count = len(recipes)
    record = {"schema": SCHEMA, "snapshot_utc": datetime.now(timezone.utc).isoformat(),
              "run_dir": str(run),
              "inventory_is_live_snapshot_not_completion_claim": True,
              "registered_plan_count": len(plans), "plans": plans,
              "search_designs": designs, "attempts": attempts,
              "raw_sha_index": raw_index,
              "terminal_result_receipt_count": sum(row["terminal"] is True for row in attempts),
              "complete_primary_score_receipt_count": sum(
                  row["terminal"] is True and row["primary_full_horizon"] is True
                  for row in attempts),
              "complete_both_window_score_receipt_count": sum(
                  row["outcome_class"] == "terminal_full_both_windows" for row in attempts),
              "registered_pair_status": pair_status,
              "all_registered_batches_terminal": pair_status[
                  "all_registered_latest_receipts_terminal"],
              "unknown_primary_pair_ids": pair_status["unknown_primary_pair_ids"],
              "unknowns": unknowns, "failed_attempts": failed,
              "failed_command_receipts": failed_commands, "fit_error_records": fit_errors,
              "integrity_errors": errors,
              "new_and_reused_data": manifests,
              "major_recipe_inventory": recipes,
              "unclassified_recipe_attempts": unclassified_recipes,
              "neural_fit_attempts": fits,
              "command_receipts": receipts,
              "budget": {
                  "actual_environment_attempts": {"used": attempt_count,
                      "limit": LIMITS["actual_environment_attempts"],
                      "remaining": LIMITS["actual_environment_attempts"] - attempt_count,
                      "basis": "numbered environment attempt directories, including failed/interrupted"},
                  "new_exogenous_specs": {"used": new_count,
                      "limit": LIMITS["new_exogenous_specs"],
                      "remaining": LIMITS["new_exogenous_specs"] - new_count,
                      "basis": "unique IDs in v6 new_data/global_manifest.json only"},
                  "reused_v5_manifest_train_specs": manifests["reused_v5_manifest_train_spec_count"],
                  "reused_v5_train_spec_ids_in_v6_plans": len(
                      manifests["reused_v5_train_spec_ids_in_v6_plans"]),
                  "major_recipes": {"observed_distinct": recipe_count,
                      "unclassified_attempts": len(unclassified_recipes),
                      "limit": LIMITS["major_recipes"],
                      "remaining_upper_bound": LIMITS["major_recipes"] - recipe_count,
                      "basis": "distinct registered v6 action families and fitted neural architecture/width; theta trials are candidates, not major recipes"},
                  "neural_fit_invocations": {"used": len(fits),
                      "confirmed_optimizer_fits": confirmed,
                      "prefit_failures": sum(row["prefit_failure_without_cuda"] for row in fits),
                      "progress_unknown": uncertain_fits},
                  "confirmed_optimizer_fits": {"used": confirmed,
                      "conservative_used_upper_bound": confirmed + uncertain_fits,
                      "limit": LIMITS["confirmed_optimizer_fits"],
                      "remaining_lower_bound": LIMITS["confirmed_optimizer_fits"] -
                      confirmed - uncertain_fits},
                  "recorded_command_union_wall_seconds": wall_seconds,
                  "recorded_command_union_wall_hours": wall_seconds / 3600,
                  "local_experiment_wall_hours": {"recorded_completed_receipt_union":
                      wall_seconds / 3600, "limit": LIMITS["local_experiment_wall_hours"],
                      "remaining_before_unreceipted_running_time":
                      LIMITS["local_experiment_wall_hours"] - wall_seconds / 3600,
                      "running_or_unreceipted_time_not_inferred": True}},
              "terminal_is_not_full_score_or_research_completion": True,
              "old_v5_results_not_counted_as_v6_attempts": True,
              "final_12_backgrounds_unopened": True}
    if write_outputs:
        json_path = Path(output_json) if output_json is not None else run / "review/ALL_ATTEMPTS_LEDGER.json"
        csv_path = Path(output_csv) if output_csv is not None else run / "review/ALL_ATTEMPTS_INDEX.csv"
        _atomic_text(json_path, json.dumps(record, ensure_ascii=False, indent=2,
                                           allow_nan=False) + "\n")
        _atomic_text(csv_path, _csv_text(attempts))
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--v5-manifest", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args(argv)
    record = build_ledger(args.run_dir, v5_manifest_path=args.v5_manifest,
                          write_outputs=True, output_json=args.json, output_csv=args.csv)
    print(json.dumps({"plans": record["registered_plan_count"],
                      "actual_environment_attempts": record["budget"]["actual_environment_attempts"]["used"],
                      "new_exogenous_specs": record["budget"]["new_exogenous_specs"]["used"],
                      "unknown_attempts": len(record["unknowns"]),
                      "integrity_errors": len(record["integrity_errors"]),
                      "all_registered_batches_terminal": record["all_registered_batches_terminal"]},
                     ensure_ascii=False))
    return 0 if not record["integrity_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
