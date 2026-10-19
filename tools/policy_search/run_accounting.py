"""Read-only final accounting of registered v5 experiments and frozen inputs.

This counts recorded artifacts, not unlogged work or independent physical truth.
The sole write operation refuses an incomplete run and creates one new receipt.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "policy_search.v5.run_accounting.v1"
PLAN_SCHEMA = "policy_search.matched_full_feedback.v1"
REQUIRED_PHASES = frozenset({"train", "selection", "calibration", "development"})
BUDGETS = {"experimental_wall_hours": 96, "trajectory_attempts": 30000,
           "new_exogenous_specifications": 1500, "major_configurations": 80,
           "optimizer_fits": 240}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _digest(value) -> str:
    data = json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _signed(value: dict) -> bool:
    return value.get("signature") == _digest({k: v for k, v in value.items()
                                              if k != "signature"})


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _pair_folder(out: Path, scenario_id: str, policy_id: str) -> Path:
    return out / "pairs" / _digest([scenario_id, policy_id])


def _known_window(row: dict, name: str) -> bool:
    score = row.get(name)
    return (isinstance(score, dict) and score.get("full_horizon") is True
            and isinstance(score.get("targets"), list)
            and len(score["targets"]) == 19
            and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) for value in score["targets"])
            and ("target_mask" not in score or
                 score["target_mask"] == [True] * 19))


def collect_batch(run_dir: str | Path, plan_path: str | Path) -> dict:
    """Count latest results for every registered pair, including missing pairs."""
    run = Path(run_dir).resolve()
    path = Path(plan_path).resolve()
    plan = _read(path)
    if plan.get("schema") != PLAN_SCHEMA:
        raise ValueError(f"Unsupported registered plan: {path}")
    scenarios = plan["scenario_ids"]
    specs = plan["policy_specs"]
    policies = [spec["policy_id"] for spec in specs]
    expected = len(scenarios) * len(specs)
    if (len(scenarios) != len(set(scenarios)) or
            len(policies) != len(set(policies)) or
            plan.get("registered_pair_count") != expected):
        raise ValueError(f"Invalid registered pair denominator: {path}")

    out = path.parent / "results"
    counts = Counter()
    attempt_counts = Counter()
    attempted_scenes = set()
    known_folders = set()
    issues = []
    attempts = terminal = completed = unknown_primary = unknown_full = missing = 0
    terminal_attempts = completed_attempts = unknown_primary_attempts = unknown_full_attempts = 0
    for sid in scenarios:
        for pid in policies:
            folder = _pair_folder(out, sid, pid)
            known_folders.add(folder.name)
            dirs = sorted(p for p in folder.glob("attempt_*")
                          if p.is_dir() and p.name[8:].isdigit())
            attempts += len(dirs)
            if dirs:
                attempted_scenes.add(sid)
            attempt_rows = []
            for attempt in dirs:
                result_path = attempt / "result.json"
                if not result_path.is_file():
                    attempt_counts["attempt_started_no_result"] += 1
                    attempt_rows.append(None)
                    continue
                try:
                    row = _read(result_path)
                except (OSError, ValueError) as error:
                    issues.append(f"invalid result {result_path}: {error}")
                    attempt_counts["invalid_result"] += 1
                    attempt_rows.append(None)
                    continue
                attempt_rows.append(row)
                if (row.get("scenario_id") != sid or row.get("policy_id") != pid or
                        row.get("plan_signature") != plan.get("signature")):
                    issues.append(f"result identity differs from plan: {result_path}")
                attempt_status = row.get("status") or "missing_status"
                attempt_counts[attempt_status] += 1
                if row.get("terminal") is True:
                    terminal_attempts += 1
                    primary_known = _known_window(row, "primary_score")
                    full_known = _known_window(row, "full_observation")
                    unknown_primary_attempts += not primary_known
                    unknown_full_attempts += not full_known
                    if attempt_status == "completed" and primary_known and full_known:
                        completed_attempts += 1
                    elif attempt_status == "completed":
                        issues.append(f"completed result has unknown window: {result_path}")
                raw_name = row.get("trajectory_path")
                if raw_name:
                    raw_rel = Path(raw_name)
                    if (raw_rel.is_absolute() or ".." in raw_rel.parts or
                            not (attempt / raw_rel).is_file()):
                        issues.append(f"missing or nonlocal raw trajectory: {result_path}")
            row = attempt_rows[-1] if attempt_rows else None
            if row is None:
                counts["registered_not_attempted" if not dirs else
                       "attempt_started_no_result"] += 1
                missing += 1
                continue
            result_path = dirs[-1] / "result.json"
            status = row.get("status") or "missing_status"
            counts[status] += 1
            is_terminal = row.get("terminal") is True
            terminal += is_terminal
            if is_terminal:
                primary_known = _known_window(row, "primary_score")
                full_known = _known_window(row, "full_observation")
                unknown_primary += not primary_known
                unknown_full += not full_known
                if status == "completed" and primary_known and full_known:
                    completed += 1

    pair_root = out / "pairs"
    if pair_root.is_dir():
        for folder in pair_root.iterdir():
            if folder.is_dir() and folder.name not in known_folders:
                issues.append(f"unregistered pair folder: {folder}")
    summary_paths = sorted(out.glob("summary_*.json"))
    latest_summary = summary_paths[-1] if summary_paths else None
    if latest_summary is None:
        issues.append(f"missing published summary: {out}")
    else:
        try:
            summary = _read(latest_summary)
            if (summary.get("plan_signature") != plan.get("signature") or
                    summary.get("registered_pair_count") != expected or
                    summary.get("recorded_pair_count") != expected - missing or
                    summary.get("terminal_pair_count") != terminal or
                    summary.get("status_counts") != dict(counts)):
                issues.append(f"latest summary differs from pair results: {latest_summary}")
            rows = summary.get("results")
            if rows is not None:
                keys = [(r.get("scenario_id"), r.get("policy_id")) for r in rows]
                if (len(keys) != expected - missing or len(set(keys)) != len(keys) or
                        any(sid not in scenarios or pid not in policies for sid, pid in keys)):
                    issues.append(f"latest summary rows differ from plan: {latest_summary}")
        except (OSError, ValueError, TypeError) as error:
            issues.append(f"invalid published summary {latest_summary}: {error}")
    journal = out / "attempts.jsonl"
    journal_started = journal_finished = 0
    if journal.is_file():
        try:
            for line in journal.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    event = json.loads(line).get("event")
                    journal_started += event == "started"
                    journal_finished += event == "finished"
            if journal_started != journal_finished or journal_started != attempts:
                issues.append(f"attempt journal and directories differ: {journal}")
        except (OSError, ValueError) as error:
            issues.append(f"invalid attempt journal {journal}: {error}")
    return {"plan": _relative(path, run), "plan_sha256": _sha(path),
            "plan_signature": plan["signature"], "phase": plan["phase"],
            "registered_scenarios": len(scenarios), "registered_pairs": expected,
            "attempted_scenario_ids": sorted(attempted_scenes), "attempts": attempts,
            "terminal_attempts": terminal_attempts,
            "completed_attempts": completed_attempts,
            "unknown_primary_attempts": unknown_primary_attempts,
            "unknown_full_attempts": unknown_full_attempts,
            "attempt_status_counts": dict(sorted(attempt_counts.items())),
            "terminal_pairs": terminal, "completed_pairs": completed,
            "unknown_primary_pairs": unknown_primary,
            "unknown_full_pairs": unknown_full, "missing_pairs": missing,
            "latest_status_counts": dict(sorted(counts.items())),
            "latest_summary": _relative(latest_summary, run) if latest_summary else None,
            "journal_started": journal_started, "journal_finished": journal_finished,
            "issues": issues, "ready": terminal == expected and not issues}


def _integrity(run: Path, root: Path, plans: list[Path]) -> dict:
    """Compare signed proposal, manifest, plans, models and source inventory."""
    issues = []
    freeze_path = run / "design/V5_SOURCE_FREEZE.json"
    manifest_path = run / "new_data/global_manifest.json"
    proposal_path = run / "design/source_proposal.json"
    for path in (freeze_path, manifest_path, proposal_path):
        if not path.is_file():
            issues.append(f"missing frozen source artifact: {path}")
    if issues:
        return {"issues": issues, "manifest_scenarios": 0,
                "source_sha256": {}, "input_sha256": {}, "final12":
                {"artifact_seal_passed": False, "scope": "artifact evidence only"}}
    freeze, manifest, proposal = map(_read, (freeze_path, manifest_path, proposal_path))
    if not _signed(manifest) or not _signed(proposal):
        issues.append("manifest or proposal canonical signature differs")
    for name, path, field in (("manifest", manifest_path, "manifest_sha256"),
                              ("proposal", proposal_path, "proposal_sha256")):
        if _sha(path) != freeze.get(field):
            issues.append(f"{name} bytes differ from V5_SOURCE_FREEZE")
    if (freeze.get("manifest_signature") != manifest.get("signature") or
            freeze.get("proposal_signature") != proposal.get("signature")):
        issues.append("freeze signatures differ from source artifacts")
    ref = manifest.get("policy_search_proposal", {})
    if (ref.get("sha256") != _sha(proposal_path) or
            ref.get("signature") != proposal.get("signature")):
        issues.append("manifest proposal binding differs")

    sources = {}
    for mapping in (proposal.get("source_sha256", {}),
                    manifest.get("policy_search_source_sha256", {})):
        for name, digest in mapping.items():
            if name in sources and sources[name] != digest:
                issues.append(f"conflicting registered source SHA: {name}")
            sources[name] = digest
    for plan_path in plans:
        plan = _read(plan_path)
        if not _signed(plan):
            issues.append(f"plan canonical signature differs: {plan_path}")
        if (plan.get("manifest_sha256") != _sha(manifest_path) or
                plan.get("manifest_signature") != manifest.get("signature") or
                Path(plan.get("manifest_path", "")).resolve() != manifest_path):
            issues.append(f"plan manifest binding differs: {plan_path}")
        if plan.get("simulator_hash") != manifest.get("simulator_hash"):
            issues.append(f"plan physical simulator hash differs: {plan_path}")
        if plan.get("final_blind_opened") is not False:
            issues.append(f"plan marked final blind open: {plan_path}")
        if {"policy_id": "secure_milp", "kind": "secure"} not in plan["policy_specs"]:
            issues.append(f"original secure baseline absent: {plan_path}")
        preference = Path(plan.get("preference_path", ""))
        if not preference.is_file() or _sha(preference) != plan.get("preference_sha256"):
            issues.append(f"plan preference SHA differs: {plan_path}")
        for name, digest in plan.get("source_sha256", {}).items():
            if name in sources and sources[name] != digest:
                issues.append(f"conflicting registered source SHA: {name}")
            sources[name] = digest
        copy_path = plan_path.parent / "results/registered_plan.json"
        if copy_path.is_file() and _sha(copy_path) != _sha(plan_path):
            issues.append(f"results plan copy differs: {copy_path}")
        for binding in plan.get("model_bindings", {}).values():
            folder = Path(binding["directory"])
            expected_files = binding["files"]
            actual_files = {p.relative_to(folder).as_posix(): p for p in folder.rglob("*")
                            if p.is_file() and "__pycache__" not in p.parts}
            if set(actual_files) != set(expected_files):
                issues.append(f"bound model file set differs: {folder}")
            for name, digest in expected_files.items():
                if name in actual_files and _sha(actual_files[name]) != digest:
                    issues.append(f"bound model SHA differs: {folder / name}")
    for name, digest in sorted(sources.items()):
        path = (root / name).resolve()
        try:
            path.relative_to(root)
            if not path.is_file() or _sha(path) != digest:
                issues.append(f"frozen source SHA differs: {name}")
        except ValueError:
            issues.append(f"nonlocal frozen source path: {name}")

    input_sha = {}
    for item in manifest.get("files", []):
        name = item["path"]
        input_sha[name] = item["sha256"]
        path = (root / name).resolve()
        try:
            path.relative_to(root)
            if (not path.is_file() or path.stat().st_size != item["bytes"] or
                    _sha(path) != item["sha256"]):
                issues.append(f"frozen source/input file differs: {name}")
        except ValueError:
            issues.append(f"nonlocal frozen input path: {name}")

    scenes = {scene["id"]: scene for scene in manifest.get("scenarios", [])}
    sealed = proposal.get("sealed_final_days", [])
    seal_issues = []
    if (len(sealed) != 12 or len(set(sealed)) != 12 or
            not freeze.get("final_12_backgrounds_unopened") or
            not proposal.get("final_12_backgrounds_unopened") or
            not manifest.get("policy_search_final_12_backgrounds_unopened")):
        seal_issues.append("final12 seal declaration/count differs")
    for sid, scene in scenes.items():
        support = scene.get("full_source_support", {})
        start, end = support.get("raw_start_minute"), support.get("raw_end_minute")
        if (scene.get("is_final_blind") or start is None or end is None or
                any(start < (day + 1) * 1440 and day * 1440 < end for day in sealed)):
            seal_issues.append(f"scene uses or lacks sealed-day support: {sid}")
    for plan_path in plans:
        plan = _read(plan_path)
        for sid in plan["scenario_ids"]:
            scene = scenes.get(sid)
            split = manifest.get("splits", {}).get(sid)
            if (scene is None or split is None or
                    scene.get("experiment_role") != plan["phase"] or
                    split.get("experiment_role") != plan["phase"] or
                    split.get("is_final_blind")):
                seal_issues.append(f"plan scene outside registered nonblind role: {sid}")
    issues.extend(seal_issues)
    return {"issues": issues, "freeze_sha256": _sha(freeze_path),
            "proposal_sha256": _sha(proposal_path), "manifest_sha256": _sha(manifest_path),
            "manifest_signature": manifest.get("signature"),
            "manifest_scenarios": len(scenes), "source_sha256": dict(sorted(sources.items())),
            "input_sha256": dict(sorted(input_sha.items())),
            "final12": {"artifact_seal_passed": not seal_issues,
                        "sealed_original_day_count": len(sealed),
                        "scope": "Registered artifacts and file hashes; cannot prove no external read"}}


def _commands(run: Path) -> dict:
    entries, issues, intervals = [], [], []
    for path in sorted((run / "logs").rglob("*.command.json")):
        try:
            command = _read(path)
            start_text, end_text = command.get("started_utc"), command.get("finished_utc")
            if (start_text is None) != (end_text is None):
                raise ValueError("only one command interval endpoint recorded")
            start = datetime.fromisoformat(start_text) if start_text else None
            end = datetime.fromisoformat(end_text) if end_text else None
            elapsed = command.get("elapsed_seconds", command.get("elapsed_wall_seconds"))
            if ((start is not None and (start.tzinfo is None or end.tzinfo is None or end < start)) or
                    not isinstance(elapsed, (int, float)) or elapsed < 0 or
                    not isinstance(command.get("exit_code"), int)):
                raise ValueError("invalid command interval, duration or exit code")
            if start is not None:
                intervals.append((start, end))
            entries.append({"receipt": _relative(path, run),
                            "name": command.get("name"),
                            "exit_code": command["exit_code"],
                            "elapsed_seconds": elapsed,
                            "started_utc": start_text,
                            "finished_utc": end_text})
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues.append(f"invalid command receipt {path}: {error}")
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    union = sum((end - start).total_seconds() for start, end in merged)
    return {"receipts": entries, "receipt_count": len(entries),
            "nonzero_exit_count": sum(row["exit_code"] != 0 for row in entries),
            "elapsed_seconds_sum": sum(row["elapsed_seconds"] for row in entries),
            "interval_union_seconds": union,
            "without_interval_count": len(entries) - len(intervals),
            "first_started_utc": min((row["started_utc"] for row in entries
                                      if row["started_utc"] is not None), default=None),
            "last_finished_utc": max((row["finished_utc"] for row in entries
                                       if row["finished_utc"] is not None), default=None),
            "coverage_note": "Interval union excludes elapsed-only receipts; includes read-only commands and cannot cover unlogged time",
            "issues": issues}


def _fits(run: Path) -> dict:
    rows, gate_copies = [], []
    model_root = run / "models"
    for folder in sorted(model_root.iterdir()) if model_root.is_dir() else []:
        if not folder.is_dir() or not (folder / "fit_started.json").is_file():
            continue
        meta_path, report_path = folder / "metadata.json", folder / "fit_report.json"
        meta = _read(meta_path) if meta_path.is_file() else {}
        if meta.get("gate_mode") not in (None, "none"):
            gate_copies.append(_relative(folder, run))
            continue
        report = _read(report_path) if report_path.is_file() else {}
        artifacts = meta.get("artifacts") or {}
        artifact_match = bool(artifacts) and all(
            (folder / name).is_file() and _sha(folder / name) == digest
            for name, digest in artifacts.items())
        cuda_verified = (str(report.get("actual_train_device") or "").startswith("cuda") and
                         str(report.get("actual_holdout_device") or "").startswith("cuda") and
                         isinstance(report.get("optimizer_updates"), int) and
                         report["optimizer_updates"] > 0 and
                         report.get("initial_weight_sha256") != report.get("trained_weight_sha256") and
                         report.get("trained_weight_sha256") == meta.get("trained_weight_sha256") and
                         artifact_match)
        rows.append({"model_dir": _relative(folder, run),
                     "fit_started_sha256": _sha(folder / "fit_started.json"),
                     "fit_report_sha256": _sha(report_path) if report_path.is_file() else None,
                     "trained_weight_sha256": report.get("trained_weight_sha256"),
                     "actual_train_device": report.get("actual_train_device"),
                     "cuda_device_name": report.get("cuda_device_name"),
                     "optimizer_updates": report.get("optimizer_updates"),
                     "elapsed_seconds": report.get("elapsed_seconds"),
                     "cuda_fit_evidence_verified": bool(cuda_verified)})
    return {"attempted_optimizer_fit_directories": len(rows),
            "verified_cuda_fits": sum(row["cuda_fit_evidence_verified"] for row in rows),
            "gate_copy_directories_excluded": gate_copies, "fits": rows,
            "coverage_note": "Only v5 model directories with fit_started; failed commands without that marker remain in command receipts"}


def collect_accounting(run_dir: str | Path, *, workspace_root: str | Path = ROOT) -> dict:
    """Read current artifacts without writing; readiness is false until every plan ends."""
    run, root = Path(run_dir).resolve(), Path(workspace_root).resolve()
    discovered = list(run.rglob("registered_plan.json"))
    plans = sorted(path for path in discovered if path.parent.name != "results")
    if not plans:
        raise ValueError("No registered v5 batch plans found")
    discovery_issues = [f"orphan results plan without source registration: {path}"
                        for path in discovered if path.parent.name == "results" and
                        not (path.parent.parent / "registered_plan.json").is_file()]
    batches = [collect_batch(run, path) for path in plans]
    integrity = _integrity(run, root, plans)
    commands = _commands(run)
    fits = _fits(run)
    plan_objects = [_read(path) for path in plans]
    missing_phases = sorted(REQUIRED_PHASES - {plan["phase"] for plan in plan_objects})
    registered_scenes = {sid for plan in plan_objects for sid in plan["scenario_ids"]}
    attempted_scenes = {sid for batch in batches for sid in batch["attempted_scenario_ids"]}
    spec_bindings = {}
    for plan in plan_objects:
        for spec in plan["policy_specs"]:
            spec_bindings.setdefault(spec["policy_id"], set()).add(_digest(
                {"spec": spec, "model_binding": plan.get("model_bindings", {}).get(spec["policy_id"])}))
    config_count = len({digest for values in spec_bindings.values() for digest in values})
    totals = {"registered_batches": len(batches),
              "registered_pairs": sum(batch["registered_pairs"] for batch in batches),
              "terminal_pairs": sum(batch["terminal_pairs"] for batch in batches),
              "completed_pairs": sum(batch["completed_pairs"] for batch in batches),
              "unknown_primary_pairs": sum(batch["unknown_primary_pairs"] for batch in batches),
              "unknown_full_pairs": sum(batch["unknown_full_pairs"] for batch in batches),
              "missing_pairs": sum(batch["missing_pairs"] for batch in batches),
              "trajectory_attempts": sum(batch["attempts"] for batch in batches),
              "terminal_attempts": sum(batch["terminal_attempts"] for batch in batches),
              "completed_attempts": sum(batch["completed_attempts"] for batch in batches),
              "unknown_primary_attempts": sum(batch["unknown_primary_attempts"] for batch in batches),
              "unknown_full_attempts": sum(batch["unknown_full_attempts"] for batch in batches),
              "manifest_exogenous_specifications": integrity["manifest_scenarios"],
              "unique_registered_scene_specifications": len(registered_scenes),
              "unique_attempted_scene_specifications": len(attempted_scenes),
              "registered_major_policy_configurations": config_count,
              "registered_policy_ids": len(spec_bindings),
              "optimizer_fit_directories": fits["attempted_optimizer_fit_directories"]}
    budget_use = {"experimental_wall_hours": commands["elapsed_seconds_sum"] / 3600,
                  "trajectory_attempts": totals["trajectory_attempts"],
                  "new_exogenous_specifications": totals["manifest_exogenous_specifications"],
                  "major_configurations": config_count,
                  "optimizer_fits": totals["optimizer_fit_directories"]}
    budget_checks = {name: budget_use[name] <= limit for name, limit in BUDGETS.items()}
    batch_issues = [issue for batch in batches for issue in batch["issues"]]
    issues = discovery_issues + batch_issues + list(integrity["issues"]) + list(commands["issues"])
    if missing_phases:
        issues.append(f"required phase plans absent: {', '.join(missing_phases)}")
    for policy_id, values in spec_bindings.items():
        if len(values) > 1:
            issues.append(f"policy ID has conflicting registered configuration: {policy_id}")
    for name, passed in budget_checks.items():
        if not passed:
            issues.append(f"v5 budget exceeded: {name}")
    all_terminal = all(batch["terminal_pairs"] == batch["registered_pairs"]
                       for batch in batches)
    all_consistent = all(batch["ready"] for batch in batches)
    return {"schema": SCHEMA, "run_dir": str(run), "workspace_root": str(root),
            "batches": batches, "totals": totals, "source_input_integrity": integrity,
            "command_receipts": commands, "cuda_optimizer_fits": fits,
            "budgets": {"limits": BUDGETS, "observed_use": budget_use,
                        "within_observed_limits": budget_checks,
                        "wall_note": "Conservative sum of logged command durations, including elapsed-only receipts and overlaps; unlogged time is unknown"},
            "issues": issues,
            "all_registered_pairs_terminal": all_terminal,
            "all_batch_receipts_consistent": all_consistent,
            "missing_required_phase_plans": missing_phases,
            "finalizable": all_consistent and not issues}


def write_final_accounting(run_dir: str | Path, *, workspace_root: str | Path = ROOT) -> Path:
    """Create the one-shot final receipt only after every registered plan closes."""
    report = collect_accounting(run_dir, workspace_root=workspace_root)
    if not report["all_registered_pairs_terminal"]:
        raise ValueError("registered pairs are not terminal; no final accounting written")
    if not report["all_batch_receipts_consistent"]:
        raise ValueError("registered batch receipts differ; no final accounting written")
    if report["missing_required_phase_plans"]:
        raise ValueError("required phase plans absent; no final accounting written")
    if not report["finalizable"]:
        raise ValueError("frozen source, seal or observed budget checks failed; no final accounting written")
    report["generated_utc"] = datetime.now(timezone.utc).isoformat()
    path = Path(run_dir).resolve() / "review/RUN_ACCOUNTING.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--workspace-root", type=Path, default=ROOT)
    parser.add_argument("--write-final", action="store_true")
    args = parser.parse_args()
    if args.write_final:
        print(write_final_accounting(args.run_dir, workspace_root=args.workspace_root))
    else:
        report = collect_accounting(args.run_dir, workspace_root=args.workspace_root)
        print(json.dumps({"finalizable": report["finalizable"],
                          "totals": report["totals"], "issues": report["issues"]},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
