"""Small registered v6 action-effect panel on old train-only scenarios."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.policy_redesign.closed_loop import register_batch, run_batch
from tools.policy_search.preference import paired_metrics

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs/v6_policy_redesign_20261016_run01"
V5_TRAIN_MANIFEST = ROOT / "outputs/v5_policy_search_20261015_run01/new_data/global_manifest.json"
PREFERENCE = RUN / "design/RESEARCH_PREFERENCE_v1.json"

SMOKE_SCENES = (
    "control_case_d35b71ee41ae6581f3bc",  # normal, old v5 train p3
    "control_case_225c926364ef6dde565f",  # GRID0, old v5 train p3, completed secure
    "control_case_b02981addd1666f5cf2a",  # EC0, old v5 train p3, P0 opportunity
)

SMOKE_POLICIES = (
    {"policy_id": "secure_milp", "kind": "secure"},
    {"policy_id": "v6_identity_port", "kind": "v6_feedback",
     "family": "port_residual", "theta": [0, 0, 0, 0, 0, 0]},
    {"policy_id": "v6_gt_up_030", "kind": "v6_feedback",
     "family": "port_residual", "theta": [.3, .3, .3, 0, 0, 0]},
    {"policy_id": "v6_diesel_up_100", "kind": "v6_feedback",
     "family": "port_residual", "theta": [0, 0, 0, 0, 1, 0]},
    {"policy_id": "v6_gt_total_up_050", "kind": "v6_feedback",
     "family": "source_allocation", "theta": [.5, 0, 0, 0, 0, 0, 0]},
    {"policy_id": "v6_grid_down_050", "kind": "v6_feedback",
     "family": "port_residual", "theta": [0, 0, 0, -.5, 0, 0]},
    {"policy_id": "v6_abs_up_050", "kind": "v6_feedback",
     "family": "port_residual", "theta": [0, 0, 0, 0, 0, .5]},
)


def register_smoke():
    target = RUN / "diagnostics/action_effect_smoke/registered_plan.json"
    return register_batch(V5_TRAIN_MANIFEST, target, scenario_ids=SMOKE_SCENES,
                          policy_specs=SMOKE_POLICIES,
                          preference_path=PREFERENCE, phase="train")


def run_smoke(max_new_pairs=None):
    folder = RUN / "diagnostics/action_effect_smoke"
    return run_batch(folder / "registered_plan.json", folder / "results",
                     max_new_pairs=max_new_pairs)


def analyze_smoke():
    folder = RUN / "diagnostics/action_effect_smoke"
    summaries = sorted((folder / "results").glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("Actual smoke results absent")
    summary = json.loads(summaries[-1].read_text(encoding="utf-8"))
    metrics = {spec["policy_id"]: paired_metrics(summary["results"], spec["policy_id"])
               for spec in SMOKE_POLICIES}
    record = {"schema": "policy_redesign.v6.action_effect_smoke.v1",
              "summary_path": str(summaries[-1]),
              "plan_path": str(folder / "registered_plan.json"),
              "registered_pairs": summary["registered_pair_count"],
              "terminal_pairs": summary["terminal_pair_count"],
              "status_counts": summary["status_counts"],
              "metrics": metrics,
              "physical_effect_requires_raw_chain_review": True}
    target = folder / "ANALYSIS.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("register-smoke", "run-smoke", "analyze-smoke"))
    parser.add_argument("--max-new-pairs", type=int)
    args = parser.parse_args()
    if args.operation == "register-smoke":
        result = register_smoke()
        print(json.dumps({"plan_signature": result["signature"],
                          "registered_pair_count": result["registered_pair_count"]}))
    elif args.operation == "run-smoke":
        result = run_smoke(args.max_new_pairs)
        print(json.dumps({"terminal_pair_count": result["terminal_pair_count"],
                          "registered_pair_count": result["registered_pair_count"],
                          "status_counts": result["status_counts"]}))
    else:
        result = analyze_smoke()
        print(json.dumps({"terminal_pair_count": result["terminal_pairs"],
                          "status_counts": result["status_counts"]}))
