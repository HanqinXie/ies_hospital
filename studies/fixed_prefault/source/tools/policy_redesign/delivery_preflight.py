"""Read-only v6 evidence tables and two-archive delivery preflight.

This inventories actual saved raw audits, search generations, CUDA fit
receipts, and the current attempt ledger. Missing work stays incomplete. The
module can write compact derived review tables, but never runs the simulator,
fits a model, opens final-blind cases, or constructs a finished report/ZIP.
"""
from __future__ import annotations

import argparse
import ast
import csv
import difflib
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.control_improvement.ranking import TARGET_NAMES
from tools.policy_redesign import portable, select_develop
from tools.policy_redesign.ledger import build_ledger
from tools.policy_redesign.neural import model_artifact_binding
from tools.policy_redesign.search import _digest as search_digest
from tools.policy_redesign.select_develop import (verify_saved_audit,
                                                  _broad_training_panel,
                                                  _formal_cuda_model)
from tools.policy_redesign.closed_loop import verify_batch
from tools.policy_redesign.closed_loop import PHYSICS_FILES
from tools.policy_search.preference import paired_metrics


SCHEMA = "policy_redesign.v6.delivery_preflight.v1"
AUDIT_SCHEMA = "policy_redesign.v6.matched_batch_audit.v1"


def _sha(path):
    stream = hashlib.sha256()
    with Path(path).open("rb") as opened:
        for block in iter(lambda: opened.read(1024 * 1024), b""):
            stream.update(block)
    return stream.hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _csv(rows):
    if not rows:
        return ""
    fields = list(rows[0])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n",
                            extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False, allow_nan=False)
                         if value is None or isinstance(value, (dict, list)) else value
                         for key, value in row.items()})
    return stream.getvalue()


def _write_tables(out_dir, report, tables):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, rows in tables.items():
        path = out / (key + ".csv")
        path.write_text(_csv(rows), encoding="utf-8", newline="")
        paths[key] = str(path.resolve())
    report["table_paths"] = paths
    (out / "DELIVERY_PREFLIGHT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")


def compact_audit_tables(audit, phase):
    """Return all 19 target deltas and each named condition/root/pressure row.

    Totals cover only complete matched windows. Unknown case IDs and null
    totals are retained; worst positive cases are kept separate from totals.
    """
    if audit.get("target_names") != list(TARGET_NAMES):
        raise ValueError("Raw audit does not use the original 19 targets")
    metrics = []
    for pid, comparison in sorted(audit["policy_comparisons"].items()):
        for window, worst_field in (("primary", "worst_case_by_target"),
                                    ("full", "full_worst_case_by_target")):
            metric = comparison[window]
            totals = metric["total_delta_by_target"]
            if len(totals) != 19 or metric["target_names"] != list(TARGET_NAMES):
                raise ValueError("Paired metric target schema differs from original 19")
            worst = comparison[worst_field]
            for index, name in enumerate(TARGET_NAMES):
                case = worst.get(name)
                metrics.append({"phase": phase, "policy_id": pid,
                    "kind": comparison["kind"], "window": window,
                    "target_index": index, "target_name": name,
                    "registered_case_count": metric["registered_case_count"],
                    "complete_paired_case_count": metric["complete_paired_case_count"],
                    "unknown_case_count": len(metric["unknown_case_ids"]),
                    "unknown_case_ids": metric["unknown_case_ids"],
                    "observable_paired_target_count": metric[
                        "observable_paired_count_by_target"].get(name),
                    "new_P0_failure_cases": metric["new_P0_failure_cases"],
                    "resolved_P0_failure_cases": metric["resolved_P0_failure_cases"],
                    "total_delta": totals[index],
                    "worst_case_id": None if case is None else case["scenario_id"],
                    "worst_case_delta": None if case is None else case["candidate_minus_secure"],
                    "worst_candidate_value": None if case is None else case["candidate_value"],
                    "worst_secure_value": None if case is None else case["secure_value"]})
    subgroups = []
    group_fields = (("condition", "by_condition", "condition_id"),
                    ("root", "by_root", "root_family_id"),
                    ("pressure", "by_pressure_point", "pressure_point_index"))
    for group_type, source, id_field in group_fields:
        for group in audit["subgroups"].get(source, []):
            for pid, comparison in sorted(group["policy_comparisons"].items()):
                for window in ("primary", "full"):
                    metric = comparison[window]
                    subgroups.append({"phase": phase, "group_type": group_type,
                        "group_id": group[id_field], "policy_id": pid,
                        "window": window,
                        "scenario_ids": group["scenario_ids"],
                        "registered_case_count": metric["registered_case_count"],
                        "complete_paired_case_count": metric["complete_paired_case_count"],
                        "unknown_case_ids": metric["unknown_case_ids"],
                        "new_P0_failure_cases": metric["new_P0_failure_cases"],
                        "resolved_P0_failure_cases": metric["resolved_P0_failure_cases"],
                        "P0_failure_minutes_total_delta": metric["P0_failure_minutes_total_delta"],
                        "P0_e_ENS_kWh_delta": metric["P0_e_ENS_kWh_delta"],
                        "P0_h_ENS_kWh_delta": metric["P0_h_ENS_kWh_delta"],
                        "P0_c_ENS_kWh_delta": metric["P0_c_ENS_kWh_delta"]})
    return metrics, subgroups


def compact_case_rows(audit, phase):
    """Retain every paired case, including null truncated target components."""
    rows = []
    for pid, comparison in sorted(audit["policy_comparisons"].items()):
        for window in ("primary", "full"):
            metric = comparison[window]
            for sid, case in sorted(metric["individual_cases"].items()):
                delta = case["delta_targets"]
                if len(delta) != len(TARGET_NAMES):
                    raise ValueError("Individual case lacks original 19 target components")
                rows.append({"phase": phase, "policy_id": pid, "window": window,
                    "scenario_id": sid,
                    "candidate_status": case["candidate_status"],
                    "secure_status": case["baseline_status"],
                    "complete_paired_window": case["complete_paired_window"],
                    "P0_failure_delta": delta[0],
                    "P0_failure_minutes_delta": delta[1],
                    "P0_e_ENS_kWh_delta": delta[2],
                    "P0_h_ENS_kWh_delta": delta[3],
                    "P0_c_ENS_kWh_delta": delta[4],
                    "all_19_target_deltas": delta,
                    "candidate_19_targets": case["candidate_targets"],
                    "secure_19_targets": case["baseline_targets"]})
    return rows


def _checked_signature(row, path, label, *, search=False):
    body = {k: v for k, v in row.items() if k != "signature"}
    expected = search_digest(body) if search else digest(body)
    if row.get("signature") != expected:
        raise ValueError(f"{label} signature differs: {path}")


def inventory_search(search_dir):
    """Include evaluated and interrupted/unevaluated registered generations."""
    root = Path(search_dir).resolve()
    errors, rows = [], []
    path = root / "search_plan.json"
    if not path.is_file():
        return rows, ["Adaptive search plan missing: " + str(path)]
    try:
        design = _read(path)
        _checked_signature(design, path, "Search plan", search=True)
        if design.get("schema") != "policy_redesign.adaptive_feedback_search.v1.plan":
            raise ValueError("Search plan schema differs")
    except (OSError, ValueError, TypeError, KeyError) as error:
        return rows, [str(error)]
    for folder in sorted(root.glob("seed_*/generation_*")):
        if not folder.is_dir():
            continue
        plan_path, eval_path = folder / "registered_plan.json", folder / "evaluation.json"
        item = {"search_design_path": str(path), "search_design_sha256": _sha(path),
                "seed": None, "generation": None,
                "generation_dir": str(folder), "status": "proposal_without_plan",
                "plan_path": str(plan_path) if plan_path.is_file() else None,
                "plan_sha256": _sha(plan_path) if plan_path.is_file() else None,
                "evaluation_path": str(eval_path) if eval_path.is_file() else None,
                "evaluation_sha256": _sha(eval_path) if eval_path.is_file() else None,
                "candidate_count": None, "best_policy_id": None,
                "best_priority_key": None, "best_unknown_case_ids": None,
                "best_complete_paired_case_count": None,
                "best_new_P0_failure_cases": None,
                "best_P0_failure_minutes_total_delta": None,
                "raw_audit_path": None, "raw_audit_sha256": None,
                "raw_audit_passed": None, "raw_sha_index_digest": None}
        try:
            item["seed"] = int(folder.parent.name.removeprefix("seed_"))
            item["generation"] = int(folder.name.removeprefix("generation_"))
            if item["seed"] not in design["seeds"]:
                raise ValueError("Generation seed absent from registered search plan")
            if not plan_path.is_file():
                rows.append(item)
                continue
            plan = _read(plan_path)
            _checked_signature(plan, plan_path, "Generation batch")
            if (plan.get("scenario_ids") != design["scenario_ids"] or
                    plan.get("phase") != "train"):
                raise ValueError("Generation batch differs from common train panel")
            item["status"] = "registered_without_evaluation"
            if eval_path.is_file():
                evaluation = _read(eval_path)
                _checked_signature(evaluation, eval_path, "Generation evaluation",
                                   search=True)
                if (evaluation.get("schema") !=
                    "policy_redesign.adaptive_feedback_search.v1.evaluation" or
                    evaluation.get("seed") != item["seed"] or
                    evaluation.get("generation") != item["generation"] or
                    evaluation.get("scenario_ids") != design["scenario_ids"] or
                    evaluation.get("registered_pair_count") !=
                    plan.get("registered_pair_count")):
                    raise ValueError("Generation evaluation/plan identity differs")
                candidates = evaluation["candidates"]
                order = sorted(candidates,
                               key=lambda row: (tuple(row["priority_key"]), row["policy_id"]))
                if evaluation["candidate_order"] != [r["policy_id"] for r in order]:
                    raise ValueError("Generation candidate ranking differs")
                item["status"] = "evaluated"
                item["candidate_count"] = len(candidates)
                if order:
                    best = order[0]
                    metric = best.get("primary_metrics") or {}
                    item.update(best_policy_id=best["policy_id"],
                        best_priority_key=best["priority_key"],
                        best_unknown_case_ids=best["unknown_case_ids"],
                        best_complete_paired_case_count=best["complete_paired_case_count"],
                        best_new_P0_failure_cases=metric.get("new_P0_failure_cases"),
                        best_P0_failure_minutes_total_delta=metric.get(
                            "P0_failure_minutes_total_delta"))
            audit_path = folder / "AUDIT_V6.json"
            if audit_path.is_file():
                raw_index = verify_saved_audit(plan_path, audit_path, "train")
                item["raw_audit_path"] = str(audit_path)
                item["raw_audit_sha256"] = _sha(audit_path)
                item["raw_audit_passed"] = True
                item["raw_sha_index_digest"] = digest(raw_index)
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"{folder}: {type(error).__name__}: {error}")
            item["status"] = "invalid_or_unknown"
        rows.append(item)
    return rows, errors


def _module_path(name):
    if name.startswith("hospital_ies"):
        return "src/" + name.replace(".", "/")
    return name.replace(".", "/")


def inspect_python_closure(workspace_root):
    """Check statically named local imports in the complete Python source set."""
    root = Path(workspace_root).resolve()
    try:
        sources = portable._source_entries(root)
    except (FileNotFoundError, ValueError) as error:
        return {"source_python_count": 0, "source_python_bytes": 0,
                "local_import_modules_checked": 0,
                "missing_local_modules": [], "errors": [str(error)],
                "dynamic_imports_not_proven": True}
    names = set(sources)
    local = set()
    errors = []
    for filename, path in sources.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=filename)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    candidates = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    candidates = [node.module]
                else:
                    continue
                local.update(name for name in candidates
                             if name.split(".")[0] in {"tools", "hospital_ies"})
        except (OSError, UnicodeError, SyntaxError) as error:
            errors.append(f"{filename}: source parse failed: {error}")
    def present(name):
        path = _module_path(name)
        # PEP 420 namespace packages legitimately have no __init__.py.
        return (path + ".py" in names or path + "/__init__.py" in names or
                any(filename.startswith(path + "/") for filename in names))

    missing = sorted(name for name in local if not present(name))
    return {"source_python_count": len(sources),
            "source_python_bytes": sum(path.stat().st_size for path in sources.values()),
            "local_import_modules_checked": len(local),
            "missing_local_modules": missing, "errors": errors,
            "dynamic_imports_not_proven": True}


def _new_v6_source_paths(root):
    files = (list((root / "tools/policy_redesign").glob("*.py")) +
             list((root / "tests").glob("test_v6_*.py")) +
             [p for p in (root / "docs/v6").rglob("*") if p.is_file()])
    return sorted({p.resolve() for p in files if p.is_file()},
                  key=lambda p: p.relative_to(root).as_posix())


def _physics_snapshot(run, root):
    path = run / "new_data/global_manifest.json"
    if not path.is_file():
        return {"status": "missing_manifest", "manifest_path": str(path),
                "physical_files": [], "mismatches": []}
    manifest = _read(path)
    frozen = {row["path"]: row for row in manifest.get("files", [])
              if isinstance(row, dict) and row.get("path") in PHYSICS_FILES}
    rows, mismatches = [], []
    for name in sorted(PHYSICS_FILES):
        source = root / name
        registered = frozen.get(name)
        actual = _sha(source) if source.is_file() else None
        row = {"path": name,
               "registered_sha256": None if registered is None else registered.get("sha256"),
               "current_sha256": actual,
               "unchanged_from_v6_manifest": registered is not None and
               actual == registered.get("sha256")}
        rows.append(row)
        if not row["unchanged_from_v6_manifest"]:
            mismatches.append(name)
    return {"status": "unchanged_from_v6_manifest" if not mismatches else "mismatch",
            "manifest_path": str(path), "manifest_sha256": _sha(path),
            "physical_files": rows, "mismatches": mismatches,
            "claim_scope": "Registered v6 physical/input snapshot versus current bytes"}


def build_source_review(run_dir, *, workspace_root=ROOT, write_outputs=False):
    """SHA/size list and unified diff of new v6 files against empty files.

    There is no Git repository or prior tracked version of these new files.
    The separate physical snapshot records that registered simulator bytes did
    not change during v6; this diff never presents physics as a new addition.
    """
    run, root = Path(run_dir).resolve(), Path(workspace_root).resolve()
    rows, parts = [], []
    for path in _new_v6_source_paths(root):
        rel = path.relative_to(root).as_posix()
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        header = f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n"
        patch = "".join(difflib.unified_diff([], lines,
            fromfile="/dev/null", tofile="b/" + rel, lineterm="\n"))
        parts.append(header + patch)
        rows.append({"path": rel, "bytes": path.stat().st_size,
                     "sha256": _sha(path),
                     "role": "v6_implementation" if rel.startswith("tools/") else
                     "v6_test" if rel.startswith("tests/") else "v6_documentation",
                     "diff_basis": "new_file_against_empty"})
    diff = "".join(parts)
    inventory = {"schema": SCHEMA + ".source_review",
                 "workspace_root": str(root),
                 "new_v6_files": rows,
                 "new_v6_file_count": len(rows),
                 "new_v6_total_bytes": sum(row["bytes"] for row in rows),
                 "unified_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
                 "unified_diff_bytes": len(diff.encode("utf-8")),
                 "unified_diff_basis": "All new v6 files compared with empty files; no Git repo",
                 "physical_core_snapshot_check": _physics_snapshot(run, root),
                 "old_v5_and_v4_outputs_not_diffed_or_modified": True}
    if write_outputs:
        review = run / "review"
        review.mkdir(parents=True, exist_ok=True)
        (review / "V6_SOURCE_UNIFIED_DIFF.patch").write_text(diff, encoding="utf-8")
        (review / "V6_SOURCE_INVENTORY.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
    return inventory


def inventory_command_receipts(run_dir, *, write_outputs=False):
    """Index every local command receipt and preserve nonzero exits/log links."""
    run = Path(run_dir).resolve()
    collected, errors = [], []
    for path in sorted(run.rglob("commands_and_exit_codes.txt")):
        try:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                collected.append((path.relative_to(run).as_posix() + f":{number}",
                                  _read_line(line)))
        except (OSError, ValueError, TypeError) as error:
            errors.append(f"{path}: malformed command lines: {error}")
    for path in sorted(run.rglob("*.command.json")):
        try:
            collected.append((path.relative_to(run).as_posix(), _read(path)))
        except (OSError, ValueError, TypeError) as error:
            errors.append(f"{path}: malformed command receipt: {error}")
    by_key = {}
    for source, receipt in collected:
        key = (receipt.get("name"), receipt.get("started_utc"),
               receipt.get("finished_utc"),
               json.dumps(receipt.get("command"), sort_keys=True, ensure_ascii=False))
        if key not in by_key:
            by_key[key] = {"name": receipt.get("name"),
                           "command": receipt.get("command"),
                           "started_utc": receipt.get("started_utc"),
                           "finished_utc": receipt.get("finished_utc"),
                           "elapsed_seconds": receipt.get("elapsed_seconds"),
                           "exit_code": receipt.get("exit_code"),
                           "receipt_sources": [], "raw_logs": []}
        row = by_key[key]
        if (row["exit_code"] != receipt.get("exit_code") or
                row["command"] != receipt.get("command")):
            errors.append(f"{source}: duplicate command identity has conflicting exit/command")
        row["receipt_sources"].append(source)
        receipt_path = run / source.split(":", 1)[0]
        stems = []
        if receipt_path.name.endswith(".command.json"):
            stems.append(receipt_path.with_name(
                receipt_path.name.removesuffix(".command.json")))
        if isinstance(row["name"], str):
            stems.append(run / "logs" / row["name"])
        for stem in stems:
            for stream in ("stdout", "stderr"):
                log = stem.with_name(stem.name + f".{stream}.log")
                if log.is_file():
                    rel = log.relative_to(run).as_posix()
                    if not any(item["path"] == rel for item in row["raw_logs"]):
                        row["raw_logs"].append({"stream": stream, "path": rel,
                                                "bytes": log.stat().st_size,
                                                "sha256": _sha(log)})
    commands = sorted(by_key.values(), key=lambda row:
                      (str(row["started_utc"]), str(row["name"])))
    failed = [row for row in commands if row["exit_code"] not in (0, None)]
    no_logs = [row["name"] for row in failed if not row["raw_logs"]]
    record = {"schema": SCHEMA + ".commands",
              "unique_command_count": len(commands),
              "failed_command_count": len(failed),
              "running_or_unknown_exit_count": sum(row["exit_code"] is None
                                                   for row in commands),
              "commands": commands, "failed_commands": failed,
              "failed_commands_without_raw_log": no_logs,
              "parse_errors": errors,
              "failed_exits_are_preserved_not_omitted": True}
    if write_outputs:
        review = run / "review"
        review.mkdir(parents=True, exist_ok=True)
        (review / "COMMAND_RECEIPTS_INDEX.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
        (review / "FAILED_COMMANDS.csv").write_text(_csv(failed), encoding="utf-8")
    return record


def _read_line(line):
    return json.loads(line,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def build_historical_v5_controls_index(run_dir, *, v5_run=None,
                                       workspace_root=ROOT,
                                       expected_case_count=60,
                                       write_outputs=False):
    """Index old blend teacher and NN as already-seen development evidence.

    This uses the previously published v5 pair ledger and checks its external
    raw SHA references. Nothing here is counted as new v6 selection support.
    """
    run, root = Path(run_dir).resolve(), Path(workspace_root).resolve()
    old = (Path(v5_run).resolve() if v5_run is not None else
           root / "outputs/v5_policy_search_20261015_run01")
    plan_path = old / "development/registered_plan.json"
    ledger_path = old / "review/POLICY_PAIR_LEDGER.json"
    plan, ledger = _read(plan_path), _read(ledger_path)
    batch = next((row for row in ledger["batches"] if row["plan_path"] ==
                  "development/registered_plan.json"), None)
    if batch is None or len(plan["scenario_ids"]) != expected_case_count or batch[
            "scenario_ids"] != plan["scenario_ids"]:
        raise ValueError("Historical v5 development denominator differs")
    wanted = ("secure_milp", "blend_overload", "nn_ungated",
              "strict_guard", "current_rule")
    registered = {spec["policy_id"]: spec for spec in plan["policy_specs"]}
    if not set(wanted[:3]) <= set(registered):
        raise ValueError("Historical secure, weak blend teacher or old NN absent")
    policies = [pid for pid in wanted if pid in registered]
    pairs = [row for row in batch["pairs"] if row["policy_id"] in policies]
    expected = {(sid, pid) for sid in plan["scenario_ids"] for pid in policies}
    if {(row["scenario_id"], row["policy_id"]) for row in pairs} != expected or len(
            pairs) != len(expected):
        raise ValueError("Historical v5 pair ledger lacks registered comparison support")
    raw_index = []
    for row in pairs:
        for attempt in row["attempts"]:
            name, sha = attempt.get("raw_trajectory_path"), attempt.get(
                "raw_trajectory_sha256")
            if name is None:
                raw_index.append({"scenario_id": row["scenario_id"],
                                  "policy_id": row["policy_id"],
                                  "raw_relative_path": None, "raw_sha256": None,
                                  "unknown_or_failed_attempt": True})
                continue
            path = (old / name).resolve()
            if not path.is_relative_to(old) or not path.is_file() or _sha(path) != sha:
                raise ValueError("Historical v5 raw SHA/path differs: " + name)
            raw_index.append({"scenario_id": row["scenario_id"],
                              "policy_id": row["policy_id"],
                              "raw_relative_path": name, "raw_sha256": sha,
                              "unknown_or_failed_attempt": False})
    comparison_rows = []
    for pid in policies:
        spec = registered[pid]
        windows = {}
        for window, field in (("primary", "primary_score"),
                              ("full", "full_observation")):
            metric = paired_metrics(pairs, pid, score_field=field)
            if metric["registered_case_count"] != expected_case_count:
                raise ValueError("Historical v5 paired denominator differs")
            complete = [(sid, case) for sid, case in metric["individual_cases"].items()
                        if case["complete_paired_window"]]
            worst = (max(complete, key=lambda item: (item[1]["delta_targets"][1], item[0]))
                     if complete else None)
            windows[window] = {
                "registered_case_count": metric["registered_case_count"],
                "complete_paired_case_count": metric["complete_paired_case_count"],
                "unknown_case_ids": metric["unknown_case_ids"],
                "new_P0_failure_cases": metric["new_P0_failure_cases"],
                "resolved_P0_failure_cases": metric["resolved_P0_failure_cases"],
                "P0_failure_minutes_total_delta": metric["P0_failure_minutes_total_delta"],
                "total_delta_by_original_19_targets": metric["total_delta_by_target"],
                "worst_P0_failure_minutes_case": None if worst is None else {
                    "scenario_id": worst[0],
                    "candidate_minus_secure": worst[1]["delta_targets"][1]}}
        comparison_rows.append({"policy_id": pid, "kind": spec["kind"],
                                "specification": spec, "windows": windows})
    model_dir = old / "models/gru_agg1_blend_overload_seed5102"
    model_files = [{"path": p.relative_to(model_dir).as_posix(), "bytes": p.stat().st_size,
                    "sha256": _sha(p)} for p in sorted(model_dir.rglob("*")) if p.is_file()]
    if not any(row["path"] == "model.pt" for row in model_files):
        raise FileNotFoundError("Historical aggregate GRU model.pt absent")
    metadata = _read(model_dir / "metadata.json")
    for name, expected_sha in metadata.get("artifacts", {}).items():
        if (Path(name).name != name or not (model_dir / name).is_file() or
                _sha(model_dir / name) != expected_sha):
            raise ValueError("Historical v5 model artifact SHA differs: " + name)
    source_files = []
    for name in ("tools/policy_search/closed_loop.py",
                 "tools/policy_search/policy.py", "tools/policy_search/neural.py",
                 "tools/policy_search/neural_aggregate.py"):
        path = root / name
        if path.is_file():
            current_sha = _sha(path)
            registered_sha = plan.get("source_sha256", {}).get(name)
            if registered_sha is not None and registered_sha != current_sha:
                raise ValueError("Historical v5 registered source SHA differs: " + name)
            if (name.endswith("/neural.py") and metadata.get("source_module_sha256")
                    not in (None, current_sha)):
                raise ValueError("Historical NN source SHA differs from model metadata")
            if (name.endswith("/neural_aggregate.py") and
                    metadata.get("aggregation_module_sha256") not in (None, current_sha)):
                raise ValueError("Historical NN aggregation source SHA differs")
            source_files.append({"path": name, "sha256": current_sha,
                                 "bytes": path.stat().st_size,
                                 "registered_v5_sha256": registered_sha})
    record = {"schema": SCHEMA + ".historical_v5_controls",
              "evidence_role": "historical_seen_development_regression_only",
              "not_new_v6_selection_or_development": True,
              "v5_run": str(old),
              "v5_development_plan_path": str(plan_path),
              "v5_development_plan_sha256": _sha(plan_path),
              "v5_pair_ledger_path": str(ledger_path),
              "v5_pair_ledger_sha256": _sha(ledger_path),
              "policies": comparison_rows,
              "external_raw_sha_index": raw_index,
              "external_raw_not_copied_into_global_v6_archive": True,
              "historical_model_files": model_files,
              "historical_policy_source_files": source_files,
              "v5_development_reused_only_for_seen_mechanism_regression": True}
    if write_outputs:
        review = run / "review"
        review.mkdir(parents=True, exist_ok=True)
        (review / "HISTORICAL_V5_CONTROLS_INDEX.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
    return record


def inspect_package_materials(run_dir, *, workspace_root=ROOT, v5_run=None,
                              formal_model_dir=None, v6_pair=None,
                              v6_train_unknown_pair=None, train_audit_path=None):
    """Check sources for both ZIPs without writing either ZIP."""
    run, root = Path(run_dir).resolve(), Path(workspace_root).resolve()
    old = (Path(v5_run).resolve() if v5_run is not None else
           root / "outputs/v5_policy_search_20261015_run01")
    closure = inspect_python_closure(root)
    global_missing = []
    for name in ("POLICY_REDESIGN_REPORT.md", "review/ALL_ATTEMPTS_LEDGER.json",
                 "review/ALL_ATTEMPTS_INDEX.csv", "new_data/global_manifest.json",
                 "design/RESEARCH_PREFERENCE_v1.json", "selection/SELECTION_BASIS.json",
                 "selection/FROZEN_SELECTION.json", "development/DEVELOPMENT_REVIEW.json",
                 "review/V6_SOURCE_INVENTORY.json",
                 "review/V6_SOURCE_UNIFIED_DIFF.patch",
                 "review/COMMAND_RECEIPTS_INDEX.json",
                 "review/FAILED_COMMANDS.csv",
                 "review/HISTORICAL_V5_CONTROLS_INDEX.json"):
        if not (run / name).is_file():
            global_missing.append(name)
    if closure["source_python_count"] == 0 or closure["missing_local_modules"] or closure["errors"]:
        global_missing.append("complete local Python source/import closure")
    all_files = [p for p in run.rglob("*") if p.is_file()] if run.is_dir() else []
    raw_files = [p for p in all_files if p.name == "trajectory.json.gz"]
    derived = [p for p in all_files if p not in raw_files and
               p.suffix.lower() not in (".zip", ".sha256")]
    global_info = {"ready": not global_missing, "missing": global_missing,
                   "derived_file_count": len(derived),
                   "derived_uncompressed_bytes": sum(p.stat().st_size for p in derived),
                   "raw_external_file_count": len(raw_files),
                   "raw_external_bytes": sum(p.stat().st_size for p in raw_files),
                   "source_python_count": closure["source_python_count"],
                   "raw_excluded_from_global_archive": True}
    repro_missing = []
    for name in (*portable.PHYSICAL_INPUTS, "pytest.ini", "requirements.txt",
                 "requirements-training.txt", "docs/v6/REPRODUCTION_README.md"):
        if not (root / name).is_file():
            repro_missing.append(name)
    if closure["source_python_count"] == 0 or closure["missing_local_modules"] or closure["errors"]:
        repro_missing.append("complete local Python source/import closure")
    historical = []
    try:
        historical = portable.select_v5_core_examples(old)
        if len(historical) != 8:
            raise ValueError("four fixed historical mechanism pairs are not eight raw")
    except (OSError, ValueError, KeyError, TypeError) as error:
        repro_missing.append("eight fixed real v5 historical raw: " + str(error))
    def resolve_batch(path):
        batch = Path(path)
        if not batch.is_absolute():
            batch = (run / batch if (run / batch).is_dir() else root / batch)
        return batch

    train_unknown = []
    if v6_train_unknown_pair is None:
        repro_missing.append("v6 current broad train unknown pair not chosen")
    else:
        try:
            batch_dir, scenario_id, candidate_policy_id = v6_train_unknown_pair
            train_unknown = portable.select_v6_train_unknown_pair(
                run, resolve_batch(batch_dir), scenario_id, candidate_policy_id,
                audit_path=train_audit_path)
            if len(train_unknown) != 2:
                raise ValueError("current broad train unknown secure/candidate count differs")
        except (OSError, ValueError, KeyError, TypeError) as error:
            repro_missing.append("v6 current broad train unknown pair: " + str(error))
    new = []
    if v6_pair is None:
        repro_missing.append("v6 matched raw pair not chosen")
    else:
        try:
            batch_dir, scenario_id, candidate_policy_id = v6_pair
            new = portable.select_v6_matched_pair(
                run, resolve_batch(batch_dir), scenario_id, candidate_policy_id)
            if len(new) != 2:
                raise ValueError("new secure/candidate pair count differs")
        except (OSError, ValueError, KeyError, TypeError) as error:
            repro_missing.append("v6 matched raw pair: " + str(error))
    model_binding = None
    if formal_model_dir is None:
        repro_missing.append("formal v6 CUDA best model not designated")
    else:
        try:
            model_path = Path(formal_model_dir)
            if not model_path.is_absolute():
                model_path = root / model_path
            model_binding = model_artifact_binding(model_path)
            spec = portable._model_spec(model_path.resolve(), old, root)
            if spec["kind"] != "v6_best":
                raise ValueError("selected reproduction model is not formal v6 best")
        except (OSError, ValueError, KeyError, TypeError) as error:
            repro_missing.append("formal v6 best model: " + str(error))
    examples = historical + train_unknown + new
    repro_info = {"ready": not repro_missing, "missing": repro_missing,
                  "historical_v5_raw_count": len(historical),
                  "new_v6_train_unknown_raw_count": len(train_unknown),
                  "new_v6_matched_raw_count": len(new),
                  "selected_raw_uncompressed_bytes": sum(
                      Path(item["raw_path"]).stat().st_size for item in examples),
                  "physical_input_count": sum((root / name).is_file()
                                              for name in portable.PHYSICAL_INPUTS),
                  "model_binding": model_binding,
                  "no_license_or_credential_file_selected": True,
                  "selected_raw_scope_only": True}
    return {"global_review": global_info,
            "selected_raw_reproduction": repro_info,
            "python_dependency_closure": closure}


def _audit_evidence(path, phase):
    if path is None or not Path(path).is_file():
        return None, None, [f"{phase} raw audit absent"]
    try:
        audit = _read(path)
        if audit.get("schema") != AUDIT_SCHEMA or audit.get("phase") != phase:
            raise ValueError("audit schema or phase differs")
        raw_index = verify_saved_audit(audit["plan_path"], path, phase)
        if phase == "train":
            train_plan, train_manifest = verify_batch(audit["plan_path"])
            _broad_training_panel(train_plan, train_manifest)
            if (len(train_plan["policy_specs"]) != 5 or
                    audit["registered_pair_count"] != 750):
                raise ValueError("broad train audit requires 150 scenes times five policies")
        metrics, groups = compact_audit_tables(audit, phase)
        subgroup = audit["subgroups"]
        if phase == "train":
            if (len(subgroup["by_condition"]) != 6 or
                    len(subgroup["by_root"]) != 30 or
                    len(subgroup["by_pressure_point"]) != 5 or
                    any(row.get("complete_five_pressure_panel") is not True or
                        row.get("registered_pressure_count") != 5
                        for row in subgroup["by_root"]) or
                    sorted(sid for row in subgroup["by_root"]
                           for sid in row.get("scenario_ids", [])) !=
                    sorted(train_plan["scenario_ids"])):
                raise ValueError("broad train audit lacks exact 30-root five-pressure subgroup support")
        if phase in ("selection", "development"):
            if (len(subgroup["by_condition"]) != 6 or
                    len(subgroup["by_root"]) != 6 or
                    len(subgroup["by_pressure_point"]) != 5 or
                    not all(row["complete_five_pressure_panel"]
                            for row in subgroup["by_root"])):
                raise ValueError("new phase lacks exact six-root five-pressure subgroup support")
        record = {"phase": phase, "audit_path": str(Path(path).resolve()),
                  "audit_sha256": _sha(path),
                  "plan_path": audit["plan_path"], "plan_sha256": audit["plan_sha256"],
                  "summary_path": audit["summary_path"],
                  "summary_sha256": audit["summary_sha256"],
                  "registered_pair_count": audit["registered_pair_count"],
                  "recomputed_pair_count": audit["recomputed_pair_count"],
                  "raw_sha_index_digest": digest(raw_index),
                  "unknown_primary_pair_ids": audit["unknown_primary_pair_ids"],
                  "unknown_full_pair_ids": audit["unknown_full_pair_ids"],
                  "condition_count": len(audit["subgroups"]["by_condition"]),
                  "root_count": len(audit["subgroups"]["by_root"]),
                  "complete_five_pressure_root_count": sum(
                      row.get("complete_five_pressure_panel") is True
                      for row in audit["subgroups"]["by_root"]),
                  "pressure_point_count": len(audit["subgroups"]["by_pressure_point"]),
                  "score_recheck_is_original_implementation_not_independent_physics": True}
        return record, (metrics, groups, compact_case_rows(audit, phase)), []
    except (OSError, ValueError, KeyError, TypeError) as error:
        return None, None, [f"{phase} raw audit invalid: {type(error).__name__}: {error}"]


def _fit_rows(run, ledger):
    rows = []
    for fit in ledger["neural_fit_attempts"]:
        directory = run / fit["directory"]
        report_path, metadata_path = directory / "fit_report.json", directory / "metadata.json"
        report = _read(report_path) if report_path.is_file() else {}
        metadata = _read(metadata_path) if metadata_path.is_file() else {}
        curve = report.get("loss_curve") or []
        rows.append({"fit_dir": fit["directory"], "status": fit["status"],
                     "fit_role": fit["fit_role"],
                     "source_stale_marker": fit["source_stale_marker"],
                     "actual_train_device": fit["actual_train_device"],
                     "optimizer_updates": fit["optimizer_updates"],
                     "best_epoch": report.get("best_epoch"),
                     "last_epoch": report.get("last_epoch"),
                     "metadata_best_epoch": metadata.get("best_epoch"),
                     "metadata_last_epoch": metadata.get("last_epoch"),
                     "best_checkpoint_sha256": metadata.get("artifacts", {}).get("best.pt"),
                     "last_checkpoint_sha256": metadata.get("artifacts", {}).get("last.pt"),
                     "fit_report_sha256": fit["fit_report_sha256"],
                     "fit_error_sha256": fit["fit_error_sha256"],
                     "metadata_sha256": fit["metadata_sha256"],
                     "epoch_curve_count": len(curve),
                     "fit_decisions": report.get("fit_decisions"),
                     "validation_decisions": report.get("validation_decisions")})
    return rows


def verify_final_chain(run_dir, audits):
    """Bind the signed selection freeze and development review to audited raw."""
    run = Path(run_dir).resolve()
    for phase in ("train", "selection", "development"):
        if audits.get(phase) is None:
            raise ValueError("Final chain lacks checked " + phase + " raw audit")
    basis_path = run / "selection/SELECTION_BASIS.json"
    freeze_path = run / "selection/FROZEN_SELECTION.json"
    review_path = run / "development/DEVELOPMENT_REVIEW.json"
    basis = select_develop._checked(basis_path, select_develop.BASIS_SCHEMA)
    frozen, selection_plan, manifest = select_develop._verify_freeze(freeze_path)
    review = select_develop._checked(review_path, select_develop.DEVELOPMENT_SCHEMA)

    def bound_path(record, field, expected):
        return (Path(record.get(field + "_path", "")).resolve() == expected.resolve()
                and record.get(field + "_sha256") == _sha(expected))

    if (basis.get("selection_registered_before_outcomes") is not True or
            basis.get("final_blind_opened") is not False or
            basis.get("train_audit_sha256") != audits["train"]["audit_sha256"] or
            basis.get("new_manifest_sha256") != _sha(run / "new_data/global_manifest.json") or
            not bound_path(frozen, "selection_basis", basis_path) or
            not bound_path(frozen, "selection_plan", run / "selection/registered_plan.json") or
            not bound_path(frozen, "selection_audit",
                           Path(audits["selection"]["audit_path"])) or
            frozen.get("selection_raw_sha_index_digest") != audits["selection"][
                "raw_sha_index_digest"] or
            frozen.get("manifest_sha256") != basis["new_manifest_sha256"] or
            frozen.get("selection_plan_sha256") != audits["selection"]["plan_sha256"] or
            frozen.get("selection_audit_sha256") != audits["selection"]["audit_sha256"]):
        raise ValueError("Signed selection basis/freeze differs from audited source or raw")
    ranked = frozen.get("ranked_policies")
    if (not isinstance(ranked, list) or not ranked or
            frozen.get("selected_policy_id") != ranked[0].get("policy_id") or
            frozen["selected_policy_id"] not in {p["policy_id"] for p in
                                                  frozen["policy_specs"]}):
        raise ValueError("Frozen selected policy differs from ranked selection result")
    development_plan_path = run / "development/registered_plan.json"
    development_plan, _ = verify_batch(development_plan_path)
    development_ids = select_develop.phase_panel(manifest, "development")
    selection_ids = select_develop.phase_panel(manifest, "selection")
    roots = {scene["id"]: scene["root_family_id"] for scene in manifest["scenarios"]}
    if (development_plan.get("phase") != "development" or
            development_plan.get("scenario_ids") != development_ids or
            set(development_ids) & set(selection_ids) or
            {roots[sid] for sid in development_ids} & {roots[sid] for sid in selection_ids} or
            development_plan.get("policy_specs") != frozen["policy_specs"] or
            development_plan.get("manifest_sha256") != frozen["manifest_sha256"] or
            development_plan.get("preference_sha256") != frozen["preference_sha256"] or
            development_plan.get("source_sha256") != frozen["source_sha256"] or
            development_plan.get("model_bindings") != frozen["model_bindings"] or
            audits["development"]["plan_sha256"] != _sha(development_plan_path)):
        raise ValueError("Development plan differs from frozen roster or disjoint panel")
    if (review.get("selected_policy_id_before_development") !=
            frozen["selected_policy_id"] or
            review.get("selection_not_recomputed_from_development") is not True or
            review.get("final_blind_opened") is not False or
            not bound_path(review, "selection_freeze", freeze_path) or
            not bound_path(review, "development_plan", development_plan_path) or
            not bound_path(review, "development_audit",
                           Path(audits["development"]["audit_path"])) or
            review.get("development_audit_sha256") != audits["development"]["audit_sha256"] or
            review.get("development_raw_sha_index_digest") != audits["development"][
                "raw_sha_index_digest"]):
        raise ValueError("Development review selected policy or audit SHA differs from freeze")
    return {"selected_policy_id": frozen["selected_policy_id"],
            "selection_freeze_sha256": _sha(freeze_path),
            "development_review_sha256": _sha(review_path),
            "selection_scenario_count": len(selection_ids),
            "development_scenario_count": len(development_ids),
            "selection_development_roots_disjoint": True,
            "development_reselection": False}


def build_delivery_preflight(run_dir, *, train_audit_path=None,
                             selection_audit_path=None, development_audit_path=None,
                             formal_model_dir=None, v6_pair=None,
                             v6_train_unknown_pair=None,
                             v5_run=None, workspace_root=ROOT,
                             write_outputs=False, output_dir=None):
    """Create evidence inventory; ``ready`` is a strict material gate only."""
    run = Path(run_dir).resolve()
    if not run.is_dir():
        raise FileNotFoundError(run)
    audits = {}
    tables = {"POLICY_METRICS_19": [], "SUBGROUP_METRICS": [],
              "INDIVIDUAL_CASES": [], "SEARCH_GENERATIONS": [],
              "NEURAL_FITS": []}
    errors = []
    research_unknowns = []
    for phase, path in (("train", train_audit_path),
                        ("selection", selection_audit_path),
                        ("development", development_audit_path)):
        record, phase_tables, problems = _audit_evidence(path, phase)
        audits[phase] = record
        errors.extend(problems)
        if phase_tables is not None:
            tables["POLICY_METRICS_19"].extend(phase_tables[0])
            tables["SUBGROUP_METRICS"].extend(phase_tables[1])
            tables["INDIVIDUAL_CASES"].extend(phase_tables[2])
    search_root = run / "search/adaptive_main"
    generations, search_errors = inventory_search(search_root)
    tables["SEARCH_GENERATIONS"] = generations
    errors.extend(search_errors)
    incomplete_generations = [r["generation_dir"] for r in generations
                              if r["status"] != "evaluated"]
    if incomplete_generations:
        research_unknowns.append(
            "Search has registered/proposed generations without checked evaluation")
    search_plan_path = search_root / "search_plan.json"
    if search_plan_path.is_file() and not search_errors:
        design = _read(search_plan_path)
        for seed in design["seeds"]:
            completed = sum(r["seed"] == seed and r["status"] == "evaluated"
                            for r in generations)
            if completed < design["min_generations"]:
                research_unknowns.append(
                    f"Search seed {seed} has only {completed} evaluated generations; "
                    f"registered minimum is {design['min_generations']}")
    formal_lineage = None
    if audits["train"] is not None and formal_model_dir is not None:
        try:
            train_plan, _ = verify_batch(audits["train"]["plan_path"])
            _, _, formal_lineage = _formal_cuda_model(
                formal_model_dir, train_plan, audits["train"]["plan_path"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append("Formal CUDA model/train source lineage invalid: " + str(error))
    elif audits["train"] is not None:
        errors.append("Formal CUDA model absent for broad-train source binding")
    final_chain = None
    try:
        final_chain = verify_final_chain(run, audits)
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append("Signed selection/development chain invalid: " + str(error))
    ledger = build_ledger(run, write_outputs=False)
    tables["NEURAL_FITS"] = _fit_rows(run, ledger)
    errors.extend(ledger["integrity_errors"])
    saved_ledger_path = run / "review/ALL_ATTEMPTS_LEDGER.json"
    ledger_fresh = False
    if saved_ledger_path.is_file():
        saved = _read(saved_ledger_path)
        ledger_fresh = (
            saved.get("registered_plan_count") == ledger["registered_plan_count"] and
            saved.get("budget", {}).get("actual_environment_attempts", {}).get("used") ==
            ledger["budget"]["actual_environment_attempts"]["used"] and
            {(r["path"], r["sha256"]) for r in saved.get("raw_sha_index", [])} ==
            {(r["path"], r["sha256"]) for r in ledger["raw_sha_index"]})
    if not ledger_fresh:
        errors.append("Saved ALL_ATTEMPTS_LEDGER.json absent or stale against current attempts/raw")
    if not ledger["all_registered_batches_terminal"]:
        research_unknowns.append("At least one exploratory registered batch lacks "
                                 "terminal latest receipts; preserved in attempt ledger")
    source_review = build_source_review(
        run, workspace_root=workspace_root, write_outputs=write_outputs)
    if source_review["physical_core_snapshot_check"]["status"] != "unchanged_from_v6_manifest":
        errors.append("Registered v6 physical/input source bytes differ or snapshot absent")
    command_index = inventory_command_receipts(run, write_outputs=write_outputs)
    errors.extend(command_index["parse_errors"])
    if command_index["failed_commands_without_raw_log"]:
        errors.append("Failed command receipts lack original stdout/stderr log references")
    historical = None
    try:
        historical = build_historical_v5_controls_index(
            run, v5_run=v5_run, workspace_root=workspace_root,
            write_outputs=write_outputs)
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append("Historical v5 blend/NN seen-regression index invalid: " + str(error))
    package = inspect_package_materials(
        run, workspace_root=workspace_root, v5_run=v5_run,
        formal_model_dir=formal_model_dir, v6_pair=v6_pair,
        v6_train_unknown_pair=v6_train_unknown_pair,
        train_audit_path=train_audit_path)
    if not package["global_review"]["ready"]:
        errors.append("Global review ZIP source materials incomplete")
    if not package["selected_raw_reproduction"]["ready"]:
        errors.append("Selected-raw reproduction ZIP source materials incomplete")
    report = {"schema": SCHEMA,
              "ready": not errors,
              "completion_claim": False,
              "errors_or_missing": errors,
              "research_unknowns_and_incomplete_branches": research_unknowns,
              "audits": audits,
              "search": {"registered_generation_dirs": len(generations),
                         "evaluated_generations": sum(r["status"] == "evaluated"
                                                      for r in generations),
                         "unevaluated_or_invalid": [r["generation_dir"] for r in generations
                                                    if r["status"] != "evaluated"],
                         "errors": search_errors},
              "formal_model_broad_train_lineage": formal_lineage,
              "signed_selection_development_chain": final_chain,
              "source_review": {"new_v6_file_count": source_review["new_v6_file_count"],
                                "new_v6_total_bytes": source_review["new_v6_total_bytes"],
                                "unified_diff_sha256": source_review["unified_diff_sha256"],
                                "physical_core_snapshot_check": source_review[
                                    "physical_core_snapshot_check"]},
              "commands": {"unique_command_count": command_index["unique_command_count"],
                           "failed_command_count": command_index["failed_command_count"],
                           "failed_command_names": [row["name"] for row in
                                                    command_index["failed_commands"]],
                           "failed_commands_without_raw_log": command_index[
                               "failed_commands_without_raw_log"]},
              "historical_seen_v5_controls": None if historical is None else {
                  "evidence_role": historical["evidence_role"],
                  "policy_ids": [row["policy_id"] for row in historical["policies"]],
                  "external_raw_sha_count": len(historical["external_raw_sha_index"]),
                  "v5_development_plan_sha256": historical[
                      "v5_development_plan_sha256"],
                  "v5_pair_ledger_sha256": historical["v5_pair_ledger_sha256"]},
              "ledger": {"current_registered_plans": ledger["registered_plan_count"],
                         "actual_environment_attempts": ledger["budget"][
                             "actual_environment_attempts"]["used"],
                         "new_exogenous_specs": ledger["budget"][
                             "new_exogenous_specs"]["used"],
                         "unknown_record_count": len(ledger["unknowns"]),
                         "failed_attempt_count": len(ledger["failed_attempts"]),
                         "integrity_error_count": len(ledger["integrity_errors"]),
                         "saved_ledger_fresh": ledger_fresh,
                         "budget": ledger["budget"]},
              "packages": package,
              "table_row_counts": {key: len(value) for key, value in tables.items()},
              "old_v5_mechanism_examples_are_not_new_v6_validation": True,
              "final_12_backgrounds_unopened": True,
              "unknown_and_failure_records_retained": True,
              "physical_validation_claim": False,
              "zip_archives_not_built_by_preflight": True}
    if write_outputs:
        _write_tables(output_dir or run / "review", report, tables)
    return report, tables


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--train-audit")
    parser.add_argument("--selection-audit")
    parser.add_argument("--development-audit")
    parser.add_argument("--formal-model")
    parser.add_argument("--v5-run")
    parser.add_argument("--v6-batch")
    parser.add_argument("--v6-scenario")
    parser.add_argument("--v6-candidate")
    parser.add_argument("--v6-train-batch")
    parser.add_argument("--v6-train-scenario")
    parser.add_argument("--v6-train-candidate")
    parser.add_argument("--out-dir")
    args = parser.parse_args(argv)
    pair = None
    if any((args.v6_batch, args.v6_scenario, args.v6_candidate)):
        if not all((args.v6_batch, args.v6_scenario, args.v6_candidate)):
            parser.error("Specify all three v6 matched-pair fields together")
        pair = (args.v6_batch, args.v6_scenario, args.v6_candidate)
    train_unknown_pair = None
    if any((args.v6_train_batch, args.v6_train_scenario, args.v6_train_candidate)):
        if not all((args.v6_train_batch, args.v6_train_scenario,
                    args.v6_train_candidate)):
            parser.error("Specify all three v6 current train unknown fields together")
        train_unknown_pair = (args.v6_train_batch, args.v6_train_scenario,
                              args.v6_train_candidate)
    report, _ = build_delivery_preflight(
        args.run, train_audit_path=args.train_audit,
        selection_audit_path=args.selection_audit,
        development_audit_path=args.development_audit,
        formal_model_dir=args.formal_model, v5_run=args.v5_run,
        v6_pair=pair, v6_train_unknown_pair=train_unknown_pair,
        write_outputs=bool(args.out_dir), output_dir=args.out_dir)
    print(json.dumps({"ready": report["ready"],
                      "errors_or_missing": report["errors_or_missing"],
                      "table_row_counts": report["table_row_counts"]},
                     ensure_ascii=False))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
