"""Unattended, fail-closed handoff for the registered v5 development run.

The command waits for the already-running development process; it never starts
another simulator. Every subsequent one-shot artifact is produced through the
existing evidence logger. A successful receipt plus the expected artifact is
required before a step can be skipped on restart.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_DEVELOPMENT_PAIRS = 480
DEVELOPMENT_RUN_RECEIPT = "v5_development_run_actual"
FINALIZATION_SCHEMA = "policy_search.finalization_status.v1"
TRIAGE_BATCHES = (
    "identity", "learning/gru_g1_aggregation_round1", "learning/gru_g1_smoke",
    "search/alternative_modes", "search/generation_1", "search/generation_2",
    "search/mode_v2_selection", "search/mode_v2_train", "selection",
    "calibration", "development",
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normal_command(command) -> list[str]:
    value = list(command)
    if value and value[0] == "python":
        value[0] = sys.executable
    return value


def check_terminal_development(run_dir: str | Path,
                               required_pairs: int = EXPECTED_DEVELOPMENT_PAIRS) -> Path | None:
    """Return the final summary only after the logged solver exits successfully.

    A missing completion receipt means the solver may still be working. A
    present but failed receipt, incomplete summary or unpaired journal is an
    error, never evidence of a finished 480-pair experiment.
    """
    run = Path(run_dir).resolve()
    folder = run / "development"
    plan_path = folder / "registered_plan.json"
    plan = _read(plan_path)
    scenarios = plan.get("scenario_ids")
    policies = plan.get("policy_specs")
    if (not isinstance(scenarios, list) or not isinstance(policies, list) or
            len(scenarios) * len(policies) != required_pairs or
            plan.get("registered_pair_count") != required_pairs):
        raise ValueError("Development registration is not the expected paired denominator")
    expected_keys = {(sid, spec["policy_id"]) for sid in scenarios for spec in policies}
    if len(expected_keys) != required_pairs:
        raise ValueError("Duplicate development pair identity")

    receipt_path = run / "logs" / (DEVELOPMENT_RUN_RECEIPT + ".command.json")
    if not receipt_path.is_file():
        return None
    receipt = _read(receipt_path)
    if receipt.get("name") != DEVELOPMENT_RUN_RECEIPT:
        raise ValueError("Development run receipt name mismatch")
    command = receipt.get("command") or []
    if (len(command) < 7 or command[-4] != "tools.policy_search.stage_ops" or
            command[-2:] != ["development", "run"]):
        raise ValueError("Development run receipt command mismatch")
    logged_cwd = Path(receipt.get("cwd", ROOT))
    logged_run = Path(command[-3])
    if not logged_run.is_absolute():
        logged_run = logged_cwd / logged_run
    if logged_run.resolve() != run:
        raise ValueError("Development run receipt points to another run directory")
    exit_code = receipt.get("exit_code")
    if type(exit_code) is not int or exit_code != 0:
        raise ValueError(f"Development run exit code {exit_code}; no finalization")

    summaries = sorted((folder / "results").glob("summary_*.json"))
    if not summaries:
        raise ValueError("Development run exited without a terminal summary")
    summary_path = summaries[-1]
    summary = _read(summary_path)
    if summary.get("plan_signature") != plan.get("signature"):
        raise ValueError("Development terminal summary has a different plan signature")
    for field in ("registered_pair_count", "recorded_pair_count", "terminal_pair_count"):
        if type(summary.get(field)) is not int or summary[field] != required_pairs:
            raise ValueError(f"Development {field} is not terminal {required_pairs}")
    rows = summary.get("results")
    if not isinstance(rows, list) or len(rows) != required_pairs:
        raise ValueError("Development terminal summary has missing result rows")
    actual_keys = [(row.get("scenario_id"), row.get("policy_id")) for row in rows]
    if (set(actual_keys) != expected_keys or len(set(actual_keys)) != required_pairs or
            any(row.get("terminal") is not True or
                row.get("plan_signature") != plan["signature"] for row in rows)):
        raise ValueError("Development result identity or terminal flag differs")

    journal = folder / "results/attempts.jsonl"
    if not journal.is_file():
        raise ValueError("Development runner journal missing")
    started, finished = Counter(), Counter()
    for number, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Development runner journal malformed line {number}") from error
        key = (event.get("scenario_id"), event.get("policy_id"), event.get("attempt"))
        if (event.get("event") not in ("started", "finished") or
                any(not isinstance(value, str) or not value for value in key) or
                (key[0], key[1]) not in expected_keys):
            raise ValueError(f"Development runner journal invalid line {number}")
        (started if event["event"] == "started" else finished)[key] += 1
    if not started or started != finished or any(n != 1 for n in started.values()):
        raise ValueError("Development runner journal has in-flight or duplicate attempts")
    if {(sid, pid) for sid, pid, _ in finished} != expected_keys:
        raise ValueError("Development runner journal lacks registered pairs")
    return summary_path


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]
    artifact: str | None
    kind: str


def _artifact_path(run: Path, step: Step) -> Path | None:
    return run / step.artifact if step.artifact else None


def _verify_artifact(run: Path, step: Step, required_pairs: int,
                     plan_signature: str) -> None:
    path = _artifact_path(run, step)
    if path is None:
        return
    if not path.is_file():
        raise ValueError(f"Step {step.name} has no required artifact: {path}")
    if step.kind == "report":
        if path.stat().st_size < 100 or not path.read_text(encoding="utf-8").startswith("# "):
            raise ValueError("Final policy report is empty or malformed")
        return
    if step.kind == "delivery":
        sidecar = path.with_name(path.name + ".sha256")
        manifest = run / "review/REVIEW_ZIP_MANIFEST.json"
        if not sidecar.is_file() or not manifest.is_file():
            raise ValueError("Review ZIP lacks SHA sidecar or manifest")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if sidecar.read_text(encoding="ascii").split()[0] != actual:
            raise ValueError("Review ZIP SHA sidecar differs")
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError("Review ZIP CRC failed")
        return
    data = _read(path)
    if step.kind in ("audit", "score"):
        if data.get("passed") is not True:
            raise ValueError(f"{step.name} did not pass")
        if data.get("registered_pair_count") != required_pairs:
            raise ValueError(f"{step.name} pair denominator differs")
    if step.kind == "score" and data.get("recomputed_pair_count") != required_pairs:
        raise ValueError("Development raw score recheck omitted registered pairs")
    if step.kind == "accounting":
        if not data.get("finalizable") or not data.get("all_registered_pairs_terminal"):
            raise ValueError("Final accounting is not finalizable")
    if step.kind == "triage":
        batches = {batch.get("batch"): batch for batch in data.get("v5_batches", [])}
        if set(batches) != set(TRIAGE_BATCHES):
            raise ValueError("Final failure triage omitted registered batches")
        if any(batch.get("terminal_pair_count") != batch.get("registered_pair_count")
               for batch in batches.values()):
            raise ValueError("Final failure triage contains nonterminal batch")
        if batches["development"].get("terminal_pair_count") != required_pairs:
            raise ValueError("Final failure triage development denominator differs")
    if step.kind in ("audit", "score", "analysis", "full_window", "sensitivity"):
        if data.get("plan_signature") != plan_signature:
            raise ValueError(f"{step.name} plan signature differs")


def run_logged_step(run_dir: str | Path, step: Step,
                    required_pairs: int = EXPECTED_DEVELOPMENT_PAIRS,
                    plan_signature: str | None = None) -> str:
    """Run once, or verify an already logged successful artifact on restart."""
    run = Path(run_dir).resolve()
    if plan_signature is None:
        plan_signature = _read(run / "development/registered_plan.json")["signature"]
    receipt_path = run / "logs" / (step.name + ".command.json")
    artifact = _artifact_path(run, step)
    if receipt_path.is_file():
        receipt = _read(receipt_path)
        if (receipt.get("name") != step.name or
                _normal_command(receipt.get("command") or []) != _normal_command(step.command) or
                receipt.get("exit_code") != 0):
            raise ValueError(f"Step {step.name} has a failed or mismatched command receipt")
        _verify_artifact(run, step, required_pairs, plan_signature)
        return "already_complete"
    if artifact is not None and artifact.exists():
        raise ValueError(f"Step {step.name} has an artifact without a successful command receipt")

    wrapper = [sys.executable, "-B", str(ROOT / "tools/p0_audit/run_logged.py"),
               "--run-dir", str(run), "--name", step.name, "--", *step.command]
    completed = subprocess.run(wrapper, cwd=ROOT, text=True, capture_output=True,
                               check=False)
    if completed.stdout:
        print(completed.stdout.strip(), flush=True)
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr, flush=True)
    if completed.returncode != 0:
        raise RuntimeError(f"Step {step.name} exited {completed.returncode}; see logs")
    if not receipt_path.is_file():
        raise ValueError(f"Step {step.name} exited without a command receipt")
    receipt = _read(receipt_path)
    if receipt.get("exit_code") != 0:
        raise ValueError(f"Step {step.name} command receipt records failure")
    _verify_artifact(run, step, required_pairs, plan_signature)
    return "executed"


def final_steps(run_dir: str | Path, v4_dir: str | Path) -> tuple[Step, ...]:
    run = Path(run_dir).resolve()
    v4 = Path(v4_dir).resolve()
    base = ("python", "-B", "-m")
    triage = (base + ("tools.policy_search.failure_triage", str(run), str(v4)) +
              tuple(arg for name in TRIAGE_BATCHES for arg in ("--batch", name)) +
              ("--output", str(run / "diagnostics/FINAL_ALL_FAILURE_TRIAGE.json")))
    return (
        Step("v5_development_audit_actual",
             base + ("tools.policy_search.stage_ops", str(run), "development", "audit"),
             "development/AUDIT.json", "audit"),
        Step("v5_development_score_recheck_actual",
             base + ("tools.policy_search.score_recheck", str(run / "development")),
             "development/SCORE_RECHECK.json", "score"),
        Step("v5_development_preference_v2_actual",
             base + ("tools.policy_search.stage_ops", str(run), "development", "analyze"),
             "development/ANALYSIS_PREFERENCE_V2.json", "analysis"),
        Step("v5_development_full_window_actual",
             base + ("tools.policy_search.full_window", str(run), "--batch", "development"),
             "development/ANALYSIS_FULL_WINDOW.json", "full_window"),
        Step("v5_development_sensitivity_actual",
             base + ("tools.policy_search.sensitivity", str(run)),
             "development/SENSITIVITY_BY_CONDITION_PRESSURE.json", "sensitivity"),
        Step("v5_final_all_failure_triage_actual", triage,
             "diagnostics/FINAL_ALL_FAILURE_TRIAGE.json", "triage"),
        Step("v5_final_full_pytest_actual", ("python", "-m", "pytest", "-q"),
             None, "pytest"),
        Step("v5_final_run_accounting_actual",
             base + ("tools.policy_search.run_accounting", str(run), "--write-final"),
             "review/RUN_ACCOUNTING.json", "accounting"),
        Step("v5_final_report_actual",
             base + ("tools.policy_search.final_report", str(run), "--write"),
             "POLICY_SEARCH_REPORT.md", "report"),
        Step("v5_final_delivery_actual",
             base + ("tools.policy_search.delivery", str(run), "--report",
                     str(run / "POLICY_SEARCH_REPORT.md")),
             "review/POLICY_SEARCH_REVIEW.zip", "delivery"),
    )


def _write_status(run: Path, state: str, current_step: str | None,
                  completed_steps: list[str], last_error: str | None = None) -> None:
    review = run / "review"
    review.mkdir(parents=True, exist_ok=True)
    path = review / "FINALIZATION_STATUS.json"
    content = {"schema": FINALIZATION_SCHEMA, "state": state,
               "current_step": current_step, "updated_utc": _now(),
               "last_error": last_error, "completed_steps": completed_steps,
               "development_registered_pair_count_required": EXPECTED_DEVELOPMENT_PAIRS,
               "evidence_note": "A completed state is written only after every logged step and artifact verifies"}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".tmp",
                                         prefix="FINALIZATION_STATUS.", dir=review,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(content, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def finalize(run_dir: str | Path, v4_dir: str | Path,
             *, poll_seconds: float = 30.0, max_wait_hours: float = 96.0) -> None:
    """Wait for the current solver, then finish the registered review package."""
    run, v4 = Path(run_dir).resolve(), Path(v4_dir).resolve()
    if not run.is_dir() or not v4.is_dir():
        raise FileNotFoundError("Expected v5 run and frozen v4 reference directories")
    if poll_seconds <= 0 or max_wait_hours <= 0:
        raise ValueError("Polling and maximum wait must be positive")
    completed: list[str] = []
    current = "v5_development_run_actual"
    try:
        _write_status(run, "waiting", current, completed)
        if not (ROOT / "tools/policy_search/final_report.py").is_file():
            raise FileNotFoundError("The final report generator must be available first")
        deadline = time.monotonic() + max_wait_hours * 3600
        while True:
            summary = check_terminal_development(run)
            if summary is not None:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Development solver did not produce a terminal receipt in time")
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
            _write_status(run, "waiting", current, completed)
        plan_signature = _read(run / "development/registered_plan.json")["signature"]
        completed.append(current)
        print(f"Development verified: {summary}", flush=True)
        for step in final_steps(run, v4):
            current = step.name
            _write_status(run, "running", current, completed)
            result = run_logged_step(run, step, plan_signature=plan_signature)
            completed.append(current)
            print(f"{current}: {result}", flush=True)
        _write_status(run, "completed", None, completed)
    except Exception as error:
        _write_status(run, "failed", current, completed,
                      f"{type(error).__name__}: {error}")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--v4-dir", type=Path,
                        default=ROOT / "outputs/v43_reliability_first_20261015_run01")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--max-wait-hours", type=float, default=96.0)
    args = parser.parse_args()
    finalize(args.run_dir, args.v4_dir, poll_seconds=args.poll_seconds,
             max_wait_hours=args.max_wait_hours)


if __name__ == "__main__":
    main()
