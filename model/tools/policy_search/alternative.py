"""Second structural route: isolate preparation, fault response and recovery.

This pre-outcome mode library is a follow-up to generation 1's adverse repeated
GT redistribution. Each member is still a single observation-only feedback
rule used over whole episodes; all original execution limits remain intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.policy_search.closed_loop import register_batch, run_batch


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def alternative_specs():
    """Mechanism-separated, bounded mode feedback; no forced intervention."""
    return [
        {"policy_id": "secure_milp", "kind": "secure"},
        {"policy_id": "prepare_only", "kind": "feedback", "family": "mode",
         "theta": [.65, 1.0, .7, -.2, 0, 0, 0, 0, 0, 0, 0, 0]},
        {"policy_id": "recovery_only", "kind": "feedback", "family": "mode",
         "theta": [0, 0, 0, 0, 0, 0, 0, 0, .65, 1.0, .7, -.2]},
        {"policy_id": "damaged_grid_only", "kind": "feedback", "family": "mode",
         "theta": [0, 0, 0, 0, .7, 0, 1.0, 0, 0, 0, 0, 0]},
        {"policy_id": "damaged_abs_only", "kind": "feedback", "family": "mode",
         "theta": [0, 0, 0, 0, .7, 0, 0, -.8, 0, 0, 0, 0]},
        {"policy_id": "current_rule", "kind": "current_rule"},
    ]


def _scenes(manifest):
    chosen = []
    for condition in sorted({s["condition_id"] for s in manifest["scenarios"]}):
        rows = sorted((s for s in manifest["scenarios"]
                       if s["experiment_role"] == "train" and s["condition_id"] == condition),
                      key=lambda s: (s["time_block_id"], s["id"]))
        moderate = next((s for s in rows if s["pressure_point_index"] == 2), None)
        boundary = next((s for s in rows if s["pressure_point_index"] == 3 and
                         s["root_family_id"] != moderate["root_family_id"]), None) if moderate else None
        if moderate is None or boundary is None:
            raise ValueError("Alternative requires two distinct train roots per condition")
        chosen.extend((moderate, boundary))
    if len(chosen) != 12 or len({s["root_family_id"] for s in chosen}) != 12:
        raise ValueError("Alternative design did not isolate twelve roots")
    return chosen


def register(run_dir):
    run = Path(run_dir)
    manifest_path = run / "new_data/global_manifest.json"
    manifest = _read(manifest_path)
    scenes = _scenes(manifest)
    folder = run / "search/alternative_modes"
    specs = alternative_specs()
    plan = register_batch(manifest_path, folder / "registered_plan.json",
                          scenario_ids=[s["id"] for s in scenes], policy_specs=specs,
                          preference_path=run / "design/RESEARCH_PREFERENCE_v1.json", phase="train")
    _new(folder / "ALTERNATIVE_REGISTRATION.json",
         {"schema": "policy_search.alternative_modes.v1", "source_sha256": _sha(__file__),
          "generation_1_analysis_sha256": _sha(run / "search/generation_1/ANALYSIS.json"),
          "manifest_sha256": _sha(manifest_path), "plan_signature": plan["signature"],
          "scenario_ids": plan["scenario_ids"], "policy_specs": specs,
          "mode_decisions": "normal only / recovery only / faulted grid only / faulted absorption only",
          "full_episode_feedback": True, "future_event_input_online": False})
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("register", "run"))
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    if args.operation == "register":
        plan = register(args.run_dir)
        print(json.dumps({"registered_pair_count": plan["registered_pair_count"],
                          "plan_signature": plan["signature"]}))
    else:
        folder = args.run_dir / "search/alternative_modes"
        summary = run_batch(folder / "registered_plan.json", folder / "results")
        print(json.dumps({"terminal_pair_count": summary["terminal_pair_count"],
                          "registered_pair_count": summary["registered_pair_count"],
                          "status_counts": summary["status_counts"]}))


if __name__ == "__main__":
    main()
