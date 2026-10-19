"""Small CLI for already-registered v5 matched stages and mode alternatives.

Each operation is explicit and writes an immutable artifact. An audit failure
returns a nonzero exit code and prevents a preference analysis from being used
as a stage input. The matching runner, scoring and policy sources are unchanged.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from tools.policy_search.audit import audit_batch
from tools.policy_search.closed_loop import run_batch
from tools.policy_search.preference_v2 import analyze_audited_batch


def _paths(run: Path, batch: str):
    relative = {"alternative_modes": "search/alternative_modes",
                "selection": "selection", "calibration": "calibration",
                "development": "development"}
    if batch not in relative:
        raise ValueError("Unsupported registered stage batch")
    folder = run / relative[batch]
    plan = folder / "registered_plan.json"
    if not plan.is_file():
        raise FileNotFoundError("Registered stage plan missing")
    return folder, plan


def _summary(folder: Path):
    summaries = sorted((folder / "results").glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("No terminal stage summary")
    return summaries[-1]


def _audit_ready(plan_path: Path, summary_path: Path):
    """Keep a partial/resuming batch from consuming its once-only AUDIT.json."""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    expected = plan.get("registered_pair_count")
    counts = (summary.get("registered_pair_count"),
              summary.get("recorded_pair_count"),
              summary.get("terminal_pair_count"))
    if (not isinstance(expected, int) or isinstance(expected, bool) or expected < 1 or
            summary.get("plan_signature") != plan.get("signature") or
            any(type(value) is not int or value != expected for value in counts)):
        raise ValueError("Audit requires terminal, recorded, registered pair counts all equal")
    journal = summary_path.parent / "attempts.jsonl"
    if not journal.is_file():
        raise ValueError("Audit requires the runner attempt journal")
    started, finished = Counter(), Counter()
    for lineno, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Malformed runner journal line {lineno}") from error
        key = (event.get("scenario_id"), event.get("policy_id"), event.get("attempt"))
        if (event.get("event") not in ("started", "finished") or
                any(not isinstance(value, str) or not value for value in key)):
            raise ValueError(f"Invalid runner journal event on line {lineno}")
        (started if event["event"] == "started" else finished)[key] += 1
    if not started or started != finished or any(count != 1 for count in started.values()):
        raise ValueError("Audit refuses in-flight or duplicated runner attempt records")


def operate(run_dir: Path, batch: str, step: str):
    run = Path(run_dir)
    folder, plan = _paths(run, batch)
    if step == "run":
        if batch == "development":
            from tools.policy_search.sensitivity import verify_preregistration
            verify_preregistration(run)
        return run_batch(plan, folder / "results")
    summary = _summary(folder)
    audit = folder / "AUDIT.json"
    freeze = run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"
    if step == "audit":
        _audit_ready(plan, summary)
        receipt = audit_batch(plan, summary, freeze, audit)
        if not receipt["passed"]:
            raise SystemExit(2)
        return {"audit_passed": True,
                "registered_pair_count": receipt["registered_pair_count"],
                "unknown_primary_pair_count": len(receipt["unknown_primary_pair_ids"])}
    if step == "analyze":
        analysis = analyze_audited_batch(
            plan, summary, audit,
            run / "design/PREFERENCE_V2_SOURCE_FREEZE.json", freeze)
        output = folder / "ANALYSIS_PREFERENCE_V2.json"
        with output.open("x", encoding="utf-8") as stream:
            json.dump(analysis, stream, indent=2, ensure_ascii=False, allow_nan=False)
        return {"analysis": str(output), "p0_pareto_ids": analysis["p0_pareto_ids"]}
    raise ValueError("Unknown stage operation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("batch", choices=("alternative_modes", "selection",
                                          "calibration", "development"))
    parser.add_argument("step", choices=("run", "audit", "analyze"))
    args = parser.parse_args()
    value = operate(args.run_dir, args.batch, args.step)
    if isinstance(value, dict):
        print(json.dumps(value, ensure_ascii=False, allow_nan=False,
                         default=str))
    else:
        print(str(value))


if __name__ == "__main__":
    main()
