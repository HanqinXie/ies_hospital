"""Actual whole-episode theta-zero identity check for the v5 feedback API."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from hospital_ies.dataset_manifest import digest
from tools.control_improvement.branches import compare_control_continuation
from tools.policy_search.closed_loop import register_batch, run_batch
from tools.policy_search.policy import zero_theta


def check(run_dir):
    run = Path(run_dir)
    manifest_path = run / "new_data/global_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scene = next(s for s in manifest["scenarios"]
                 if s["experiment_role"] == "train"
                 and s["condition_id"] == "normal_grid850_delay15"
                 and s["pressure_point_index"] == 3)
    folder = run / "identity"
    plan_path = folder / "registered_plan.json"
    if not plan_path.exists():
        register_batch(manifest_path, plan_path, scenario_ids=[scene["id"]],
                       policy_specs=[{"policy_id": "secure_milp", "kind": "secure"},
                                     {"policy_id": "secure_zero_identity", "kind": "feedback",
                                      "family": "mode", "theta": zero_theta("mode")}],
                       preference_path=run / "design/RESEARCH_PREFERENCE_v1.json", phase="train")
    summary = run_batch(plan_path, folder / "results")
    if summary["terminal_pair_count"] != 2:
        raise ValueError("Identity pair incomplete")
    pair = {r["policy_id"]: r for r in summary["results"]}
    payloads = {}
    for policy_id, row in pair.items():
        raw = folder / "results/pairs" / digest([scene["id"], policy_id]) / "attempt_0001/trajectory.json.gz"
        if hashlib.sha256(raw.read_bytes()).hexdigest() != row["trajectory_sha256"]:
            raise ValueError("Identity raw trace hash mismatch")
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            payloads[policy_id] = json.load(stream)
    trace = compare_control_continuation(payloads["secure_milp"],
                                         payloads["secure_zero_identity"], 0.0)
    score_equal = all(pair["secure_milp"][window]["targets"] ==
                      pair["secure_zero_identity"][window]["targets"]
                      for window in ("primary_score", "full_observation"))
    result = {"schema": "policy_search.zero_identity.v1", "scenario_id": scene["id"],
              "plan_signature": json.loads(plan_path.read_text(encoding="utf-8"))["signature"],
              "trace_comparison": trace, "score_equal": score_equal,
              "initial_state_and_stream_matched": summary["matched_initial_state_and_stream"],
              "passed": bool(trace["passed"] and score_equal and
                             summary["matched_initial_state_and_stream"])}
    path = folder / "IDENTITY_REPORT.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    result = check(args.run_dir)
    print(json.dumps({"passed": result["passed"], "scenario_id": result["scenario_id"]}))
    if not result["passed"]:
        raise SystemExit(1)
