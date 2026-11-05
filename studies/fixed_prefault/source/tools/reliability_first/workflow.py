"""Bounded, resumable v4 hospital development workflow.

Nothing starts on import or ``plan``. Explicit ``run`` waits for the two
existing background producers, then advances through frozen CLI contracts.
Every subprocess has immutable command/stdout/stderr/exit receipts. A failed
gate stops the dependent chain; no original output is overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable

from tools.reliability_first.audit import (BUDGET_CAPS, audit_branch_attempts,
                                           audit_closed_loop, audit_cuda_fits,
                                           fit_instance_evidence)


class WorkflowError(RuntimeError):
    """A stage, immutable source or research budget cannot safely advance."""


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]
    complete: Callable[[], bool]
    fresh_paths: tuple[Path, ...] = ()
    projected_counts: dict[str, int] = field(default_factory=dict)
    max_calls: int = 1


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write_once(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def _append(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    normalized = re.sub(r"(\.\d{6})\d+(?=[+-]\d{2}:\d{2}$)", r"\1", normalized)
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("Wall-clock budget start needs timezone")
    return parsed.astimezone(timezone.utc)


def _default_source_paths(root: Path, run: Path) -> list[Path]:
    relative = ["tools/reliability_first/workflow.py", "tools/reliability_first/collect.py",
                "tools/reliability_first/branches.py", "tools/reliability_first/data.py",
                "tools/reliability_first/diagnostics.py",
                "tools/reliability_first/learning.py", "tools/reliability_first/neural.py",
                "tools/reliability_first/tree.py", "tools/reliability_first/calibration.py",
                "tools/reliability_first/run_eval.py", "tools/reliability_first/closed_loop.py",
                "tools/reliability_first/selection.py", "tools/reliability_first/policy.py",
                "tools/reliability_first/report.py", "tools/reliability_first/package.py",
                "tools/reliability_first/change_inventory.py",
                "tools/reliability_first/audit.py", "tools/reliability_first/soft_objective_probe.py",
                "tools/reliability_first/soft_objective_probe_audit.py",
                "tools/control_improvement/data_runner.py",
                "tools/control_improvement/candidates.py", "tools/control_improvement/ranking.py",
                "tools/control_improvement/runtime.py", "tools/control_improvement/episode.py",
                "tools/control_improvement/branches.py", "tools/integrated_learning/policies.py",
                "tools/integrated_learning/branches.py", "tools/integrated_learning/snapshot.py",
                "tools/integrated_learning/truth.py", "tools/integrated_experiment/energy_metrics.py",
                "src/hospital_ies/control_env.py", "src/hospital_ies/dataset_export.py",
                "src/hospital_ies/dataset_manifest.py", "src/hospital_ies/resilience.py",
                "src/hospital_ies/model.py"]
    fixed = [root / "pytest.ini", run / "new_data/global_manifest.json", run / "design/source_proposal.json",
             run / "design/snapshot_plan_v2.json", run / "design/SNAPSHOT_PLAN_V2_CORRECTION.json",
             run / "design/branch_collection_v1.json",
             run / "design/soft_objective_probe_v1.json",
             run / "soft_objective_probe/PROBE_SUMMARY.json",
             run / "soft_objective_probe/SOURCE_SNAPSHOT.py",
             run / "design/soft_objective_probe_v2.json",
             run / "soft_objective_probe_v2/PROBE_SUMMARY.json",
             run / "soft_objective_probe_v2/IDENTITY_ORDER_AUDIT_v1.json"]
    return [root / name for name in relative] + fixed


def _baseline_started(run: Path) -> int:
    count = 0
    for journal in (run / "new_data/shards").glob("shard_*/attempts.jsonl"):
        count += sum(_load_line(line).get("event") == "started"
                     for line in journal.read_text(encoding="utf-8").splitlines() if line.strip())
    return count


def _load_line(line: str) -> dict:
    return json.loads(line)


def _probe_action_future_started(run: Path) -> int:
    """Charge every v1/v2 probe continuation start, including failed attempts."""
    total = 0
    for folder in ("soft_objective_probe", "soft_objective_probe_v2"):
        for journal in (Path(run) / folder).glob("snapshot_*/attempt_*/attempts.jsonl"):
            for line in journal.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = _load_line(line)
                if row.get("event") == "started" and row.get("attempt_type") == "current_action_future_continuation":
                    total += 1
    return total


def _probe_identity_audit_complete(run: Path, design_path: Path, summary_path: Path,
                                   summary: dict, probe_binding: dict) -> bool:
    """Require a separate, raw-bound adjudication of every terminal v2 unknown."""
    run = Path(run).resolve()
    audit_path = run / "soft_objective_probe_v2/IDENTITY_ORDER_AUDIT_v1.json"
    if not audit_path.is_file():
        return False
    audit = _load(audit_path)
    unknowns = [row for row in summary["results"] if row["status"] == "unknown"]
    indices = [row["index"] for row in unknowns]
    if (audit.get("schema") != "reliability_first.soft_probe_identity_order_audit.v1"
            or audit.get("status") != "complete"
            or audit.get("derived_from_saved_raw_only") is not True
            or audit.get("new_rollout_attempts") != 0
            or audit.get("unknown_snapshot_indices_examined") != indices
            or audit.get("affected_snapshot_indices") != indices
            or audit.get("official_v2_status_preserved") != {
                "completed_snapshots": summary["completed_snapshots"],
                "unknown_snapshots": summary["unknown_snapshots"]}
            or audit.get("unresolved_pair_count") != 0):
        return False
    result_hashes, plan_hashes, source_hashes, raw_hashes = {}, {}, {}, {}
    for row in unknowns:
        index = str(row["index"])
        result_path = (run / row["result_path"]).resolve()
        result = _load(result_path)
        plan_path = result_path.parent / "plan.json"
        if not plan_path.is_file() or not isinstance(result.get("raw_trajectory_hashes"), dict):
            return False
        result_hashes[index] = row["result_sha256"]
        plan_hashes[index] = _sha(plan_path)
        source_hashes[index] = result.get("source_result_sha256")
        for relative, digest in result["raw_trajectory_hashes"].items():
            if relative in raw_hashes and raw_hashes[relative] != digest:
                return False
            raw_hashes[relative] = digest
    source_root = Path(__file__).resolve().parents[2]
    sidecar_sources = {
        "audit_source_sha256": "tools/reliability_first/soft_objective_probe_audit.py",
        "window_targets_source_sha256": "tools/control_improvement/branches.py",
        "window_outcome_source_sha256": "tools/integrated_learning/truth.py",
        "diagnostics_source_sha256": "tools/reliability_first/diagnostics.py",
        "energy_fee_source_sha256": "tools/integrated_experiment/energy_metrics.py",
        "target_names_source_sha256": "tools/control_improvement/ranking.py",
        "physical_state_source_sha256": "src/hospital_ies/resilience.py",
        "initial_state_serialization_source_sha256": "src/hospital_ies/dataset_export.py",
        "energy_tariff_source_sha256": "src/hospital_ies/model.py",
    }
    binding = (probe_binding | {key: _sha(source_root / relative)
                                for key, relative in sidecar_sources.items()} | {
        "probe_summary_sha256": _sha(summary_path),
        "result_sha256_by_index": result_hashes,
        "attempt_plan_sha256_by_index": plan_hashes,
        "source_result_sha256_by_index": source_hashes,
    })
    if audit.get("source_binding") != binding or audit.get("raw_trajectory_hashes") != raw_hashes:
        return False
    for relative, digest in raw_hashes.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            return False
        path = (run / relative).resolve()
        if not path.is_relative_to(run) or not path.is_file() or _sha(path) != digest:
            return False
        if summary.get("raw_trajectory_hashes", {}).get(relative) != digest:
            return False
    new = audit.get("verified_new_raw_count")
    secure = audit.get("verified_secure_raw_count")
    return (isinstance(new, int) and isinstance(secure, int) and new >= 0 and secure >= 0
            and new == audit.get("order_only_verified_pair_count")
            and new + secure == len(raw_hashes))


def _probe_complete(run: Path) -> bool:
    """Do not begin dependent learning before the train-only probe is terminal."""
    design_path = Path(run) / "design/soft_objective_probe_v2.json"
    summary_path = Path(run) / "soft_objective_probe_v2/PROBE_SUMMARY.json"
    if not all(path.is_file() for path in (design_path, summary_path)):
        return False
    design, summary = _load(design_path), _load(summary_path)
    selected = design.get("selected_snapshot_indices", [])
    registered = len(selected)
    if design.get("pilot_train_indices", selected) != selected:
        return False
    binding = summary.get("source_binding", {})
    selection_path = Path(run) / "design/branch_collection_v1.json"
    manifest_path = Path(run) / "new_data/global_manifest.json"
    plan_path = Path(run) / "design/snapshot_plan_v2.json"
    if not all(path.is_file() for path in (selection_path, manifest_path, plan_path)):
        return False
    selection = _load(selection_path)
    expected_binding = {
        "probe_source_sha256": _sha(Path(__file__).with_name("soft_objective_probe.py")),
        "design_sha256": _sha(design_path),
        "manifest_sha256": _sha(manifest_path),
        "snapshot_plan_sha256": _sha(plan_path),
        "branch_collection_design_sha256": _sha(selection_path),
        "branch_source_sha256": selection.get("branch_source_sha256"),
    }
    results = summary.get("results", [])
    if not isinstance(results, list) or len(results) != registered:
        return False
    result_indices = set()
    for row in results:
        if not isinstance(row, dict) or row.get("index") not in selected:
            return False
        result_indices.add(row["index"])
        relative = row.get("result_path")
        if not isinstance(relative, str):
            return False
        result_path = (Path(run) / relative).resolve()
        if not result_path.is_relative_to(Path(run).resolve()) or not result_path.is_file():
            return False
        if row.get("result_sha256") != _sha(result_path):
            return False
        result = _load(result_path)
        if (result.get("index") != row["index"] or result.get("status") != row.get("status")
                or result.get("source_binding") != expected_binding):
            return False
    return (registered > 0 and design.get("registered_snapshots") == registered
            and len(set(selected)) == registered
            and result_indices == set(selected)
            and set(selected) <= set(selection.get("early_indices", []))
            and design.get("manifest_sha256") == expected_binding["manifest_sha256"]
            and design.get("snapshot_plan_sha256") == expected_binding["snapshot_plan_sha256"]
            and design.get("branch_collection_design_sha256") == expected_binding["branch_collection_design_sha256"]
            and design.get("branch_source_sha256") == expected_binding["branch_source_sha256"]
            and summary.get("status") == "complete"
            and binding == expected_binding
            and summary.get("registered_snapshots") == registered
            and summary.get("completed_snapshots", 0) + summary.get("unknown_snapshots", 0) == registered
            and summary.get("not_attempted_snapshots") == 0
            and summary.get("interrupted_snapshots") == 0
            and _probe_identity_audit_complete(run, design_path, summary_path, summary, expected_binding))


def compute_budget_counts(run: Path) -> dict[str, int]:
    """Count actual starts from source journals; failures/retries stay included."""
    run = Path(run)
    manifest = _load(run / "new_data/global_manifest.json")
    baseline = _baseline_started(run)
    branches = audit_branch_attempts(run)
    probe_starts = _probe_action_future_started(run)
    closed_loop = 0
    for journal in (run / "closed_loop").rglob("attempts.jsonl") if (run / "closed_loop").is_dir() else ():
        closed_loop += sum(_load_line(line).get("event") == "started"
                           for line in journal.read_text(encoding="utf-8").splitlines() if line.strip())
    identity = sum(1 for name in ("secure_baseline.json.gz", "secure_identity.json.gz")
                   for _ in (run / "closed_loop").rglob(name)) if (run / "closed_loop").is_dir() else 0
    starts = sorted((run / "learning").rglob("fit_started.json")) if (run / "learning").is_dir() else []
    fit_evidence = fit_instance_evidence(run / "learning")
    methods, configurations = set(), set()
    for path in starts:
        record = _load(path)
        method = "cuda_nn" if record.get("requested_device") == "cuda:0" else "masked_tree"
        methods.add(method)
        hyper = {key: value for key, value in record.items() if key not in {"seed", "source_binding"}}
        stage = record.get("source_binding", {}).get("stage")
        configurations.add((method, stage, json.dumps(hyper, sort_keys=True, ensure_ascii=False)))
    rounds = len(list((run / "learning").glob("aggregation_round_*"))) if (run / "learning").is_dir() else 0
    return {"new_specifications": len(manifest.get("scenarios", [])),
            "baseline_started": baseline,
            "snapshots_started": branches["snapshots_started"],
            "action_future_started": branches["action_future_started"] + probe_starts,
            "soft_objective_probe_continuations_started": probe_starts,
            "other_full_started": baseline + closed_loop + identity,
            "method_classes": len(methods), "configurations": len(configurations),
            "fit_started": fit_evidence["charged_instances"], "aggregation_rounds": rounds}


def write_budget_ledger(run: Path) -> dict:
    run = Path(run)
    counts = compute_budget_counts(run)
    exceeded = {key: (value, BUDGET_CAPS[key]) for key, value in counts.items()
                if value > BUDGET_CAPS[key]}
    if exceeded:
        raise WorkflowError(f"Research attempt budget exceeded: {exceeded}")
    result = {"schema": "reliability_first.actual_started_budget.v1",
              "started_counts": counts, "caps": BUDGET_CAPS,
              "fit_evidence": fit_instance_evidence(run / "learning"),
              "counting_rule": "Raw journal starts, including interrupted/error/retries; tree target estimator fits charged by FIT_REPORT.actual_fits, unfinished fits conservatively by model target count; branch reference, D0 and both historical v1 and current v2 train-only soft-objective probe continuations counted in action-future total; original baselines also count in other full trajectories",
              "source_paths": ["new_data/shards/*/attempts.jsonl", "branches/**/attempts.jsonl",
                               "branches/collection_attempts.jsonl", "closed_loop/**/attempts.jsonl",
                               "soft_objective_probe/snapshot_*/attempt_*/attempts.jsonl",
                               "soft_objective_probe_v2/snapshot_*/attempt_*/attempts.jsonl",
                               "learning/**/fit_started.json"], "written_utc": _utc()}
    _write_once(run / "BUDGET_LEDGER.json", result)
    return result


def build_commands(root: Path, run: Path, *, python_executable: str = sys.executable) -> dict[str, tuple[str, ...]]:
    """Actual CLI argument vector; no shell quoting or command substitution."""
    root, run = Path(root).resolve(), Path(run).resolve()
    manifest = run / "new_data/global_manifest.json"
    plan = run / "design/snapshot_plan_v2.json"
    input_early = run / "learning/early_v1"
    input_full = run / "learning/full_v1"
    nn_early, tree_early = run / "learning/early_nn_fit01", run / "learning/early_tree_fit01"
    nn_full, tree_full = run / "learning/full_nn_fit01", run / "learning/full_tree_fit01"
    dev_plan = run / "closed_loop/development60_v1_plan.json"
    dev_runs = run / "closed_loop/development60_v1_runs"
    report = run / "report/RELIABILITY_FIRST_REPORT.md"
    paired = report.parent / "PAIRED_SUMMARY.json"
    sensitivity = report.parent / "SENSITIVITY_BY_CONDITION.json"
    package = run / "review/RELIABILITY_FIRST_REVIEW_PACKAGE.zip"
    receipt = run / "review/PACKAGE_VERIFICATION.json"
    audit = run / "review/RELIABILITY_AUDIT_FINAL.json"
    p = (str(python_executable), "-B", "-m")
    commands = {}
    for index, _ in enumerate(_load(manifest).get("p2b_shards", [])):
        commands[f"baseline_shard_{index:02d}"] = p + ("tools.control_improvement.data_runner", "run",
                                                     str(manifest), str(index))
    commands.update({
        "collect_early": p + ("tools.reliability_first.collect", "run", str(run), "--stage", "early"),
        "learning_early": p + ("tools.reliability_first.learning", str(run), str(input_early), "--stage", "early"),
        "nn_early": p + ("tools.reliability_first.neural", str(input_early / "fit_input.json"), str(nn_early)),
        "tree_early": p + ("tools.reliability_first.tree", str(input_early / "fit_input.json"), str(tree_early)),
        "collect_remaining": p + ("tools.reliability_first.collect", "run", str(run), "--stage", "remaining"),
        "learning_full": p + ("tools.reliability_first.learning", str(run), str(input_full), "--stage", "full"),
        "nn_full": p + ("tools.reliability_first.neural", str(input_full / "fit_input.json"), str(nn_full)),
        "tree_full": p + ("tools.reliability_first.tree", str(input_full / "fit_input.json"), str(tree_full)),
        "calibrate_nn": p + ("tools.reliability_first.calibration", str(input_full / "calibration_input.json"), str(nn_full)),
        "calibrate_tree": p + ("tools.reliability_first.calibration", str(input_full / "calibration_input.json"), str(tree_full)),
        "register_development": p + ("tools.reliability_first.run_eval", "register", str(manifest), str(dev_plan),
                                       "--nn-dir", str(nn_full), "--tree-dir", str(tree_full),
                                       "--calibration-file", "gate_calibration.json"),
        "run_development": p + ("tools.reliability_first.run_eval", "run", str(dev_plan), str(dev_runs)),
        "render_report": p + ("tools.reliability_first.report", str(run), str(dev_runs), str(report)),
        "budget_ledger": p + ("tools.reliability_first.workflow", "ledger", str(run)),
        "package": p + ("tools.reliability_first.package", str(run), "--workspace-root", str(root),
                          "--report", str(report), "--paired-summary", str(paired),
                          "--sensitivity", str(sensitivity), "--development-dir", str(dev_runs)),
        "audit": p + ("tools.reliability_first.audit", str(run), "--workspace-root", str(root),
                        "--closed-loop-dir", str(dev_runs), "--package-zip", str(package),
                        "--package-receipt", str(receipt), "--out", str(audit),
                        "--required-policies", "secure_milp", "secure_current_rule",
                        "nn_ungated", "nn_gated", "tree_gated"),
    })
    return commands


def _default_process_alive(pid: int, launched: datetime) -> bool:
    try:
        import psutil
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        created = datetime.fromtimestamp(process.create_time(), timezone.utc)
        return abs((created - launched).total_seconds()) < 120
    except (ImportError, ValueError):
        raise WorkflowError("Cannot safely identify existing background PID; install psutil or inspect manually")
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied as error:
        raise WorkflowError(f"Cannot inspect active background PID {pid}; stop before duplicate launch") from error


def _default_command_runner(argv: tuple[str, ...], stdout, stderr, *, cwd: Path,
                            timeout_seconds: float) -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    try:
        return subprocess.run(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                              check=False, timeout=timeout_seconds).returncode
    except subprocess.TimeoutExpired:
        stderr.write("Workflow 72-hour wall deadline reached during this command; subprocess stopped.\n")
        return 124


class Workflow:
    def __init__(self, workspace_root: Path, run_dir: Path, *,
                 source_paths: list[Path] | None = None,
                 start_utc: datetime | None = None,
                 python_executable: str = sys.executable,
                 command_runner=None, process_alive=None, sleep=None, poll_seconds: int = 30,
                 clock=None):
        self.root, self.run = Path(workspace_root).resolve(), Path(run_dir).resolve()
        if not self.run.is_relative_to(self.root):
            raise ValueError("Run directory must be inside workspace")
        self.source_paths = source_paths if source_paths is not None else _default_source_paths(self.root, self.run)
        self.start_utc = start_utc or self._actual_start()
        if self.start_utc.tzinfo is None:
            raise ValueError("Budget start must be timezone aware")
        self.start_utc = self.start_utc.astimezone(timezone.utc)
        self.python_executable = python_executable
        self.command_runner = command_runner or _default_command_runner
        self.process_alive = process_alive or _default_process_alive
        self.sleep = sleep or time.sleep
        self.poll_seconds = poll_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state_path = self.run / "workflow/WORKFLOW_STATE.json"
        self.attempt_log = self.run / "workflow/attempts.jsonl"

    def _actual_start(self) -> datetime:
        launch = self.run / "baseline_runner_launch.json"
        if not launch.is_file():
            raise WorkflowError("Original baseline start receipt absent; 72-hour budget cannot be reset")
        return _parse_utc(_load(launch)["started_utc"])

    def _binding(self) -> dict[str, str]:
        binding = {}
        for path in self.source_paths:
            path = Path(path).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise WorkflowError(f"source binding input unavailable/outside workspace: {path}")
            binding[path.relative_to(self.root).as_posix()] = _sha(path)
        return binding

    def _check_wall(self) -> None:
        remaining = self.start_utc + timedelta(hours=72) - self.clock().astimezone(timezone.utc)
        if remaining.total_seconds() <= 0:
            raise WorkflowError("72-hour actual-start wall budget exhausted; no new stage launched")

    def _state(self) -> dict:
        self._check_wall()
        current = self._binding()
        if self.state_path.is_file():
            state = _load(self.state_path)
            if state.get("source_binding") != current or state.get("actual_start_utc") != self.start_utc.isoformat():
                raise WorkflowError("Frozen workflow source binding/start changed; stop before new command")
            return state
        state = {"schema": "reliability_first.unattended_workflow.v1",
                 "actual_start_utc": self.start_utc.isoformat(),
                 "deadline_utc": (self.start_utc + timedelta(hours=72)).isoformat(),
                 "source_binding": current, "steps": {}, "created_utc": _utc()}
        _write_once(self.state_path, state)
        return state

    def _save_state(self, state: dict) -> None:
        state["updated_utc"] = _utc()
        temporary = self.state_path.with_name("WORKFLOW_STATE.json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def _wait_existing(self) -> None:
        launches = sorted(self.run.glob("baseline_runner*launch.json"))
        launches += [self.run / "branches/early_runner_launch.json"]
        pending = {}
        for path in launches:
            if not path.is_file():
                continue
            row = _load(path)
            launched = _parse_utc(row.get("launched_utc", row.get("started_utc")))
            pending[int(row["pid"])] = launched
        while pending:
            self._check_wall()
            pending = {pid: launched for pid, launched in pending.items()
                       if self.process_alive(pid, launched)}
            if pending:
                self.sleep(self.poll_seconds)

    def _budget_guard(self, projected: dict[str, int]) -> None:
        counts = compute_budget_counts(self.run)
        exceeded = {key: {"current": counts[key], "projected": projected.get(key, 0), "cap": cap}
                    for key, cap in BUDGET_CAPS.items()
                    if counts[key] + projected.get(key, 0) > cap}
        if exceeded:
            raise WorkflowError(f"Attempt budget stop before command: {exceeded}")

    def _execute(self, step: Step, attempt_number: int) -> int:
        folder = self.run / "workflow"
        folder.mkdir(parents=True, exist_ok=True)
        prefix = f"{step.name}.attempt_{attempt_number:04d}"
        stdout_path, stderr_path = folder / f"{prefix}.stdout.log", folder / f"{prefix}.stderr.log"
        for path in (stdout_path, stderr_path):
            if path.exists():
                raise WorkflowError(f"Prior command log would be overwritten: {path}")
        command = list(step.command)
        started = _utc()
        _append(self.attempt_log, {"event": "started", "step": step.name, "attempt": attempt_number,
                                   "command": command,
                                   "stdout_path": stdout_path.relative_to(self.root).as_posix(),
                                   "stderr_path": stderr_path.relative_to(self.root).as_posix(),
                                   "source_binding_sha256": hashlib.sha256(
                                       json.dumps(self._binding(), sort_keys=True).encode()).hexdigest(),
                                   "utc": started})
        with stdout_path.open("x", encoding="utf-8") as stdout, stderr_path.open("x", encoding="utf-8") as stderr:
            try:
                timeout = max(0.001, (self.start_utc + timedelta(hours=72) -
                                      self.clock().astimezone(timezone.utc)).total_seconds())
                code = self.command_runner(step.command, stdout, stderr, cwd=self.root,
                                           timeout_seconds=timeout)
            except Exception as error:
                stderr.write(f"Workflow command invocation error: {type(error).__name__}: {error}\n")
                code = 127
        _append(self.attempt_log, {"event": "finished", "step": step.name, "attempt": attempt_number,
                                   "exit_code": int(code), "utc": _utc(),
                                   "stdout_sha256": _sha(stdout_path), "stderr_sha256": _sha(stderr_path)})
        return int(code)

    def run_plan(self, steps: list[Step], *, max_new_steps: int | None = None,
                 wait_existing: bool = True) -> dict:
        if max_new_steps is not None and max_new_steps < 0:
            raise ValueError("max_new_steps must be nonnegative")
        state = self._state()
        if wait_existing:
            self._wait_existing()
        executed, skipped = [], []
        for step in steps:
            self._check_wall()
            if self._binding() != state["source_binding"]:
                raise WorkflowError("Frozen workflow source binding changed during execution")
            if step.complete():
                skipped.append(step.name)
                state["steps"][step.name] = {"status": "complete", "evidence": "artifact gate", "utc": _utc()}
                self._save_state(state)
                continue
            while not step.complete():
                self._check_wall()
                if self._binding() != state["source_binding"]:
                    raise WorkflowError("Frozen workflow source binding changed during execution")
                if max_new_steps is not None and len(executed) >= max_new_steps:
                    return {"schema": "reliability_first.workflow_progress.v1",
                            "executed_steps": executed, "skipped_steps": skipped,
                            "remaining_step_names": [item.name for item in steps
                                                     if item.name not in executed + skipped or item.name == step.name],
                            "deadline_utc": (self.start_utc + timedelta(hours=72)).isoformat()}
                for path in step.fresh_paths:
                    if path.exists():
                        raise WorkflowError(f"Partial or invalid fresh output blocks {step.name}: {path}")
                self._budget_guard(step.projected_counts)
                prior = sum(1 for line in self.attempt_log.read_text(encoding="utf-8").splitlines()
                            if line.strip() and (_load_line(line).get("event") == "started")
                            and _load_line(line).get("step") == step.name) if self.attempt_log.is_file() else 0
                if prior >= step.max_calls:
                    raise WorkflowError(f"Finite retry limit reached for {step.name}; preserve failed attempts")
                state["steps"][step.name] = {"status": "running", "attempt": prior + 1, "utc": _utc()}
                self._save_state(state)
                code = self._execute(step, prior + 1)
                executed.append(step.name)
                if code != 0:
                    state["steps"][step.name] = {"status": "failed", "exit_code": code, "utc": _utc()}
                    self._save_state(state)
                    raise WorkflowError(f"{step.name} exited {code}; original logs/attempt retained")
                if step.complete():
                    state["steps"][step.name] = {"status": "complete", "exit_code": code, "utc": _utc()}
                    self._save_state(state)
                else:
                    state["steps"][step.name] = {"status": "incomplete", "exit_code": code, "utc": _utc()}
                    self._save_state(state)
                    if prior + 1 >= step.max_calls:
                        raise WorkflowError(f"{step.name} exited zero but artifact gate remains incomplete")
        return {"schema": "reliability_first.workflow_progress.v1",
                "executed_steps": executed, "skipped_steps": skipped,
                "remaining_step_names": [step.name for step in steps if step.name not in executed + skipped],
                "deadline_utc": (self.start_utc + timedelta(hours=72)).isoformat()}


def _baseline_shard_complete(run: Path, manifest: dict, index: int) -> bool:
    shard = manifest["p2b_shards"][index]
    directory = run / "new_data/shards" / shard["id"]
    journal, status_path = directory / "attempts.jsonl", directory / "shard_status.json"
    if not journal.is_file() or not status_path.is_file():
        return False
    expected = {(sid, policy) for sid in shard["scenario_ids"] for policy in manifest["policies"]}
    started, terminal = Counter(), Counter()
    for line in journal.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = _load_line(line)
        key = (row.get("scenario_id"), row.get("policy_id"))
        if key not in expected or row.get("manifest_signature") != manifest["signature"]:
            raise WorkflowError(f"Baseline shard {index} journal source/owner mismatch")
        if row.get("event") == "started":
            started[key] += 1
        elif row.get("event") in {"finished", "reconciled_checkpoint", "adjudicated_interrupted_unknown"}:
            terminal[key] += 1
        else:
            raise WorkflowError(f"Baseline shard {index} has unknown attempt event")
    if any(started[key] != 1 or terminal[key] != 1 for key in expected):
        return False
    status = _load(status_path)
    return (status.get("checkpoints") == len(expected) and
            status.get("manifest_signature") == manifest["signature"])


def _collection_complete(run: Path, stage: str) -> bool:
    from tools.reliability_first.collect import _result_ok, registered_attempts
    selection_path = run / "design/branch_collection_v1.json"
    if not selection_path.is_file():
        return False
    selection = _load(selection_path)
    plan = _load(run / "design/snapshot_plan_v2.json")
    manifest_signature = _load(run / "new_data/global_manifest.json")["signature"]
    for index in selection[f"{stage}_indices"]:
        row = plan[index]
        options = []
        if index == selection.get("pilot_reuse", {}).get("index"):
            options.append(run / selection["pilot_reuse"]["path"] / "BRANCH_RESULT.json")
        options.extend(path / "BRANCH_RESULT.json" for path in registered_attempts(run, index, stage))
        if not any(_result_ok(path, row, selection["branch_source_sha256"],
                              manifest_signature=manifest_signature)[0] for path in options):
            return False
    return True


def _fit_input_complete(run: Path, stage: str) -> bool:
    folder = run / "learning" / f"{stage}_v1"
    fit_path, calibration_path, audit_path = (folder / name for name in
                                               ("fit_input.json", "calibration_input.json", "SOURCE_DATA_AUDIT.json"))
    if not all(path.is_file() for path in (fit_path, calibration_path, audit_path)):
        return False
    fit, calibration, audit = _load(fit_path), _load(calibration_path), _load(audit_path)
    binding = fit.get("source_binding", {})
    manifest = _load(run / "new_data/global_manifest.json")
    selection_path = run / "design/branch_collection_v1.json"
    if not selection_path.is_file():
        return False
    selection = _load(selection_path)
    return (binding.get("stage") == stage and binding.get("manifest_sha256") == _sha(run / "new_data/global_manifest.json")
            and binding.get("manifest_signature") == manifest.get("signature")
            and binding.get("snapshot_plan_sha256") == _sha(run / "design/snapshot_plan_v2.json")
            and binding.get("collection_design_sha256") == _sha(selection_path)
            and binding.get("branch_source_sha256") == selection.get("branch_source_sha256")
            and calibration.get("source_binding") == binding
            and audit.get("final_12_backgrounds_excluded") is True
            and audit.get("rows_by_partition", {}).get("train", 0) > 0
            and audit.get("rows_by_partition", {}).get("validation", 0) > 0)


def _model_complete(run: Path, stage: str, kind: str, manifest_signature: str) -> bool:
    folder = run / "learning" / f"{stage}_{kind}_fit01"
    metadata_path, report_path = folder / "metadata.json", folder / "FIT_REPORT.json"
    if not metadata_path.is_file() or not report_path.is_file():
        return False
    metadata, report = _load(metadata_path), _load(report_path)
    binding = _load(run / "learning" / f"{stage}_v1" / "fit_input.json")["source_binding"]
    if metadata.get("source_binding") != binding or report.get("source_binding") != binding:
        raise WorkflowError(f"{stage} {kind} fit source differs from frozen input")
    if kind == "nn":
        result = audit_cuda_fits(folder, expected_manifest_signature=manifest_signature)
        if result["status"] == "failed":
            raise WorkflowError(f"{stage} CUDA NN fit integrity/device failure: {result['issues']}")
        return result["status"] == "passed" and result["successful_new_cuda_fits"] >= 1
    if metadata.get("schema") != "reliability_first.masked_extra_trees.v1":
        raise WorkflowError(f"{stage} tree model schema differs")
    for name, expected in metadata.get("artifacts", {}).items():
        artifact = folder / name
        if not artifact.is_file() or _sha(artifact) != expected:
            raise WorkflowError(f"{stage} tree artifact changed: {name}")
    return (report.get("actual_fits", 0) > 0 and report.get("training_device") == "cpu"
            and report.get("reload_max_abs_difference") == 0.0
            and report.get("metadata_sha256") == _sha(metadata_path))


def _calibration_complete(run: Path, kind: str) -> bool:
    folder = run / "learning" / f"full_{kind}_fit01"
    path, residual = folder / "gate_calibration.json", folder / "gate_calibration_rows.json"
    if not path.is_file() or not residual.is_file():
        return False
    record = _load(path)
    return (record.get("model_metadata_sha256") == _sha(folder / "metadata.json")
            and record.get("calibration_input_sha256") == _sha(run / "learning/full_v1/calibration_input.json")
            and record.get("residual_audit_sha256") == _sha(residual)
            and record.get("online_future_information") is False)


def _development_plan_complete(run: Path, manifest: dict) -> bool:
    path = run / "closed_loop/development60_v1_plan.json"
    binding = path.with_suffix(".algorithm_source_sha256.json")
    if not path.is_file() or not binding.is_file():
        return False
    plan = _load(path)
    expected = {"secure_milp", "secure_current_rule", "nn_ungated", "nn_gated", "tree_gated"}
    return (plan.get("manifest_signature") == manifest["signature"]
            and plan.get("phase") == "development" and plan.get("final_blind_opened") is False
            and len(plan.get("scenario_ids", [])) == 60
            and {row.get("policy_id") for row in plan.get("policy_specs", [])} == expected
            and _load(binding).get("plan_signature") == plan.get("signature"))


def _development_complete(run: Path) -> bool:
    folder = run / "closed_loop/development60_v1_runs"
    result = audit_closed_loop(folder)
    if result["status"] == "failed":
        raise WorkflowError(f"Matched development physical/source binding failed: {result['issues']}")
    return result["status"] == "passed" and result.get("registered_pairs") == 300


def _report_complete(run: Path) -> bool:
    folder = run / "report"
    names = ("RELIABILITY_FIRST_REPORT.md", "PAIRED_SUMMARY.json", "PAIRED_CASES.json",
             "SENSITIVITY_BY_CONDITION.json", "REPORT_PROVENANCE.json",
             "ACTION_COVERAGE.json", "BRANCH_INVENTORY.json")
    if not all((folder / name).is_file() for name in names):
        return False
    return _load(folder / "REPORT_PROVENANCE.json").get("complete_60_case_development") is True


def _ledger_complete(run: Path) -> bool:
    path = run / "BUDGET_LEDGER.json"
    if not path.is_file():
        return False
    observed = _load(path).get("started_counts")
    if observed != compute_budget_counts(run):
        raise WorkflowError("Frozen budget ledger differs from current raw attempt journals")
    return True


def _package_complete(root: Path, run: Path) -> bool:
    zip_path, receipt_path = (run / "review" / name for name in
                              ("RELIABILITY_FIRST_REVIEW_PACKAGE.zip", "PACKAGE_VERIFICATION.json"))
    if not zip_path.is_file() or not receipt_path.is_file():
        return False
    receipt = _load(receipt_path)
    index, manifest = (run / "review" / name for name in
                       ("EXTERNAL_FILE_SHA256.jsonl", "PACKAGE_MANIFEST.json"))
    if not index.is_file() or not manifest.is_file():
        return False
    relative = lambda path: path.resolve().relative_to(root.resolve()).as_posix()
    raw = receipt.get("sampled_raw_paths", [])
    return (receipt.get("schema") == "reliability_first.package_verification.v1"
            and receipt.get("crc_passed") is True
            and isinstance(receipt.get("files"), int) and receipt["files"] > 0
            and receipt.get("files") == receipt.get("included_byte_hashes_checked")
            and receipt.get("zip_path") == relative(zip_path)
            and receipt.get("zip_bytes") == zip_path.stat().st_size
            and isinstance(receipt.get("zip_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", receipt["zip_sha256"]) is not None
            and receipt.get("external_index_path") == relative(index)
            and receipt.get("external_index_sha256") == _sha(index)
            and receipt.get("package_manifest_path") == relative(manifest)
            and receipt.get("package_manifest_sha256") == _sha(manifest)
            and isinstance(raw, list) and len(raw) >= 2)


def _audit_complete(run: Path) -> bool:
    path = run / "review/RELIABILITY_AUDIT_FINAL.json"
    if not path.is_file():
        return False
    record = _load(path)
    if record.get("status") != "passed":
        raise WorkflowError(f"Final audit is {record.get('status')}; preserve result and stop")
    return True


def build_steps(workflow: Workflow) -> list[Step]:
    """Frozen dependency order with artifact gates, not success-by-exit-code."""
    root, run = workflow.root, workflow.run
    manifest = _load(run / "new_data/global_manifest.json")
    commands = build_commands(root, run, python_executable=workflow.python_executable)
    selection = _load(run / "design/branch_collection_v1.json")
    if not _probe_complete(run):
        raise WorkflowError("Train-only v2 soft-objective probe is absent, partial, or source-unbound; stop before dependent learning")
    result = []
    for index, shard in enumerate(manifest["p2b_shards"]):
        name = f"baseline_shard_{index:02d}"
        result.append(Step(name, commands[name], lambda i=index: _baseline_shard_complete(run, manifest, i),
                           projected_counts={"baseline_started": 2 * len(shard["scenario_ids"]),
                                             "other_full_started": 2 * len(shard["scenario_ids"]) }))
    for stage in ("early", "remaining"):
        if stage == "early":
            prefix = [Step("collect_early", commands["collect_early"],
                           lambda: _collection_complete(run, "early"),
                           projected_counts={"snapshots_started": len(selection["early_indices"]),
                                             "action_future_started": 34 * len(selection["early_indices"])})]
            prefix += [Step("learning_early", commands["learning_early"], lambda: _fit_input_complete(run, "early"),
                            fresh_paths=(run / "learning/early_v1",)),
                       Step("nn_early", commands["nn_early"],
                            lambda: _model_complete(run, "early", "nn", manifest["signature"]),
                            fresh_paths=(run / "learning/early_nn_fit01",),
                            projected_counts={"fit_started": 1, "method_classes": 1, "configurations": 1}),
                       Step("tree_early", commands["tree_early"],
                            lambda: _model_complete(run, "early", "tree", manifest["signature"]),
                            fresh_paths=(run / "learning/early_tree_fit01",),
                            projected_counts={"fit_started": 19, "method_classes": 1, "configurations": 1})]
            result.extend(prefix)
        else:
            result.append(Step("collect_remaining", commands["collect_remaining"],
                               lambda: _collection_complete(run, "remaining"),
                               projected_counts={"snapshots_started": len(selection["remaining_indices"]),
                                                 "action_future_started": 34 * len(selection["remaining_indices"])}))
    result.extend([
        Step("learning_full", commands["learning_full"], lambda: _fit_input_complete(run, "full"),
             fresh_paths=(run / "learning/full_v1",)),
        Step("nn_full", commands["nn_full"], lambda: _model_complete(run, "full", "nn", manifest["signature"]),
             fresh_paths=(run / "learning/full_nn_fit01",),
             projected_counts={"fit_started": 1, "method_classes": 1, "configurations": 1}),
        Step("tree_full", commands["tree_full"], lambda: _model_complete(run, "full", "tree", manifest["signature"]),
             fresh_paths=(run / "learning/full_tree_fit01",),
             projected_counts={"fit_started": 19, "method_classes": 1, "configurations": 1}),
        Step("calibrate_nn", commands["calibrate_nn"], lambda: _calibration_complete(run, "nn"),
             fresh_paths=(run / "learning/full_nn_fit01/gate_calibration.json",
                          run / "learning/full_nn_fit01/gate_calibration_rows.json")),
        Step("calibrate_tree", commands["calibrate_tree"], lambda: _calibration_complete(run, "tree"),
             fresh_paths=(run / "learning/full_tree_fit01/gate_calibration.json",
                          run / "learning/full_tree_fit01/gate_calibration_rows.json")),
        Step("register_development", commands["register_development"],
             lambda: _development_plan_complete(run, manifest),
             fresh_paths=(run / "closed_loop/development60_v1_plan.json",
                          run / "closed_loop/development60_v1_plan.algorithm_source_sha256.json")),
        Step("run_development", commands["run_development"], lambda: _development_complete(run),
             projected_counts={"other_full_started": 600}, max_calls=2),
        Step("render_report", commands["render_report"], lambda: _report_complete(run),
             fresh_paths=(run / "report/RELIABILITY_FIRST_REPORT.md", run / "report/PAIRED_SUMMARY.json",
                          run / "report/PAIRED_CASES.json", run / "report/SENSITIVITY_BY_CONDITION.json",
                          run / "report/REPORT_PROVENANCE.json", run / "report/ACTION_COVERAGE.json",
                          run / "report/BRANCH_INVENTORY.json")),
        Step("budget_ledger", commands["budget_ledger"], lambda: _ledger_complete(run),
             fresh_paths=(run / "BUDGET_LEDGER.json",)),
        Step("package", commands["package"], lambda: _package_complete(root, run),
             fresh_paths=(run / "review/RELIABILITY_FIRST_REVIEW_PACKAGE.zip",
                          run / "review/RELIABILITY_FIRST_REVIEW_PACKAGE.zip.building",
                          run / "review/PACKAGE_VERIFICATION.json",
                          run / "review/EXTERNAL_FILE_SHA256.jsonl",
                          run / "review/PACKAGE_MANIFEST.json")),
        Step("audit", commands["audit"], lambda: _audit_complete(run),
             fresh_paths=(run / "review/RELIABILITY_AUDIT_FINAL.json",)),
    ])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="Read-only command/skip inventory; never starts computation")
    run = sub.add_parser("run", help="Explicitly start/resume the bounded sequence")
    ledger = sub.add_parser("ledger", help="Write new raw-attempt budget ledger once")
    for item in (plan, run, ledger):
        item.add_argument("run_dir", type=Path)
        item.add_argument("--workspace-root", type=Path, default=Path.cwd())
    run.add_argument("--max-new-steps", type=int, help="One for a single stage; omit for all dependencies")
    args = parser.parse_args()
    if args.command == "ledger":
        print(json.dumps(write_budget_ledger(args.run_dir), ensure_ascii=False))
        return
    workflow = Workflow(args.workspace_root, args.run_dir)
    steps = build_steps(workflow)
    if args.command == "plan":
        print(json.dumps({"actual_start_utc": workflow.start_utc.isoformat(),
                          "deadline_utc": (workflow.start_utc + timedelta(hours=72)).isoformat(),
                          "steps": [{"name": step.name, "complete": step.complete(),
                                     "command": list(step.command)} for step in steps]},
                         ensure_ascii=False, indent=2))
        return
    print(json.dumps(workflow.run_plan(steps, max_new_steps=args.max_new_steps,
                                       wait_existing=True), ensure_ascii=False))


if __name__ == "__main__":
    main()
