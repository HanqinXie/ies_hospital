"""Build request/projection/arrival/execution diagnostics from a matched batch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.policy_redesign.action_effect import diagnose_pair


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def diagnose_batch(results_dir, output_path):
    folder = Path(results_dir)
    summaries = sorted(folder.glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("No actual matched summary")
    summary = _read(summaries[-1])
    by = {(r["scenario_id"], r["policy_id"]): r for r in summary["results"]}
    plans = []
    for (sid, pid), result in by.items():
        if pid == "secure_milp":
            continue
        secure = by.get((sid, "secure_milp"))
        if secure is None:
            raise ValueError("Matched secure pair missing")
        pair = {}
        for name, row in (("candidate", result), ("secure", secure)):
            local = folder / "pairs" / digest([sid, row["policy_id"]])
            attempts = sorted(local.glob("attempt_*/result.json"))
            chosen = next((p for p in reversed(attempts) if _read(p).get("trajectory_sha256") ==
                           row.get("trajectory_sha256")), None)
            if chosen is None:
                pair = None
                break
            pair[name] = chosen.parent / row["trajectory_path"]
        if pair is None:
            plans.append({"scenario_id": sid, "policy_id": pid,
                          "status": "unknown_or_missing_raw", "candidate_status": result["status"]})
            continue
        diagnostic = diagnose_pair(pair["candidate"], pair["secure"])
        plans.append({"scenario_id": sid, "policy_id": pid,
                      "candidate_raw": str(pair["candidate"]),
                      "secure_raw": str(pair["secure"]),
                      "candidate_raw_sha256": result["trajectory_sha256"],
                      "secure_raw_sha256": secure["trajectory_sha256"],
                      "primary_score": result["primary_score"],
                      "secure_primary_score": secure["primary_score"],
                      "diagnostic": diagnostic})
    record = {"schema": "policy_redesign.v6.batch_action_effect.v1",
              "source_summary": str(summaries[-1]),
              "registered_pair_count": summary["registered_pair_count"],
              "terminal_pair_count": summary["terminal_pair_count"],
              "cases": plans,
              "request_plan_arrival_execution_are_distinct": True}
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("output_path", type=Path)
    args = parser.parse_args()
    result = diagnose_batch(args.results_dir, args.output_path)
    print(json.dumps({"registered_pair_count": result["registered_pair_count"],
                      "diagnosed_candidate_pairs": len(result["cases"])}))
