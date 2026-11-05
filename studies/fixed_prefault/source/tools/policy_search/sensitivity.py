"""Read-only same-root crossed-pressure summaries for registered v5 batches.

Pressure points are supported *composite* assumption configurations, not a
single-parameter causal experiment or empirically weighted event distribution.
Every policy uses exactly the same scene at a point. Unknown targets remain
null; sums carry their own observed denominators and confer no advantage claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POINTS = (0, 1, 2, 3, 4)
ANCHOR = 2


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _event_schedule(scene):
    """Compare timing/device faults while allowing the registered pressure magnitude to vary."""
    events = scene.get("exogenous_events")
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ValueError("Missing or malformed exogenous event schedule")
    return [{key: value for key, value in event.items()
             if event.get("kind") != "load_shock" or
             key not in ("shock", "shock_by_priority")}
            for event in events]


def _development_contract(run):
    folder = run / "development"
    plan_path = folder / "registered_plan.json"
    stage_path = folder / "STAGE_REGISTRATION.json"
    freeze_path = run / "design/STAGES_SOURCE_FREEZE.json"
    plan, stage, freeze = (_read(path) for path in (plan_path, stage_path, freeze_path))
    unsigned = {key: value for key, value in plan.items() if key != "signature"}
    manifest_path = Path(plan["manifest_path"])
    if (plan.get("phase") != "development" or plan.get("signature") != _digest(unsigned) or
            stage.get("schema") != "policy_search.development_stage_registration.v1" or
            stage.get("plan_sha256") != _sha(plan_path) or
            stage.get("plan_signature") != plan["signature"] or
            stage.get("scenario_ids") != plan.get("scenario_ids") or
            stage.get("policy_specs") != plan.get("policy_specs") or
            stage.get("manifest_sha256") != plan.get("manifest_sha256") or
            stage.get("stages_source_freeze_sha256") != _sha(freeze_path) or
            stage.get("stage_source_sha256") != _sha(ROOT / "tools/policy_search/stages.py") or
            freeze.get("stage_source_sha256") != stage["stage_source_sha256"] or
            stage.get("pressure_sensitivity_indices") != list(POINTS) or
            stage.get("same_policy_set_at_every_registered_pressure") is not True or
            plan.get("manifest_sha256") != _sha(manifest_path)):
        raise ValueError("Development stage registration/source/manifest binding differs")
    manifest = _read(manifest_path)
    lookup = {scene["id"]: scene for scene in manifest["scenarios"]}
    roots = {}
    for sid in plan["scenario_ids"]:
        scene = lookup.get(sid)
        if (scene is None or scene.get("experiment_role") != "development" or
                scene.get("is_final_blind")):
            raise ValueError("Development sensitivity scene role or blind status differs")
        root = scene["root_family_id"]
        points = roots.setdefault(root, {})
        index = scene["pressure_point_index"]
        if index in points:
            raise ValueError("Duplicate same-root pressure point")
        points[index] = scene
    if (not roots or set(roots) != set(stage.get("sensitivity_root_family_ids", [])) or
            len(plan["scenario_ids"]) != 5 * len(roots)):
        raise ValueError("Development registered root set differs from stage")
    for scenes in roots.values():
        if set(scenes) != set(POINTS):
            raise ValueError("Development registered pressure cross is incomplete")
        reference = scenes[ANCHOR]
        for key in ("condition_id", "time_block_id", "base_state_id",
                    "exogenous_stream_id", "initialization_seed"):
            if reference.get(key) is None or any(s.get(key) != reference[key]
                                                 for s in scenes.values()):
                raise ValueError("Development same-root manifest identity differs: " + key)
        schedule = _event_schedule(reference)
        if any(_event_schedule(scene) != schedule for scene in scenes.values()):
            raise ValueError("Development same-root exogenous event schedule differs")
    policies = [spec["policy_id"] for spec in plan["policy_specs"]]
    if len(policies) != len(set(policies)) or "secure_milp" not in policies:
        raise ValueError("Development registered policy set is invalid")
    return plan_path, stage_path, freeze_path, manifest_path, sorted(roots), policies


def preregister_sensitivity(run_dir):
    """Write an immutable anchor/source receipt before any development attempt."""
    run = Path(run_dir)
    folder = run / "development"
    output = folder / "SENSITIVITY_PREREGISTRATION.json"
    if output.exists():
        raise FileExistsError("Sensitivity preregistration is immutable")
    plan_path, stage_path, freeze_path, manifest_path, roots, policies = (
        _development_contract(run))
    results = folder / "results"
    if results.exists() and any(results.rglob("*")):
        raise ValueError("Sensitivity must be preregistered before any development result")
    receipt = {"schema": "policy_search.development_sensitivity_preregistration.v1",
               "registered_unix": time.time(),
               "registered_before_any_development_attempt": True,
               "pressure_point_indices": list(POINTS),
               "anchor_pressure_point_index": ANCHOR,
               "sensitivity_source_sha256": _sha(__file__),
               "development_plan_sha256": _sha(plan_path),
               "stage_registration_sha256": _sha(stage_path),
               "stages_source_freeze_sha256": _sha(freeze_path),
               "manifest_sha256": _sha(manifest_path),
               "root_family_ids": roots, "policy_ids": policies,
               "same_root_raw_initial_and_stream_required": True,
               "final_blind_opened": False}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, ensure_ascii=False, allow_nan=False)
    return output


def verify_preregistration(run_dir):
    """Recheck the sensitivity receipt against immutable development inputs."""
    run = Path(run_dir)
    folder = run / "development"
    path = folder / "SENSITIVITY_PREREGISTRATION.json"
    receipt = _read(path)
    plan_path, stage_path, freeze_path, manifest_path, roots, policies = (
        _development_contract(run))
    expected = {"sensitivity_source_sha256": _sha(__file__),
                "development_plan_sha256": _sha(plan_path),
                "stage_registration_sha256": _sha(stage_path),
                "stages_source_freeze_sha256": _sha(freeze_path),
                "manifest_sha256": _sha(manifest_path)}
    if (receipt.get("schema") != "policy_search.development_sensitivity_preregistration.v1" or
            receipt.get("registered_before_any_development_attempt") is not True or
            receipt.get("pressure_point_indices") != list(POINTS) or
            receipt.get("anchor_pressure_point_index") != ANCHOR or
            receipt.get("root_family_ids") != roots or
            receipt.get("policy_ids") != policies or
            receipt.get("same_root_raw_initial_and_stream_required") is not True or
            receipt.get("final_blind_opened") is not False or
            not isinstance(receipt.get("registered_unix"), (int, float))):
        raise ValueError("Development sensitivity preregistration contract differs")
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ValueError("Development sensitivity preregistration source differs: " + key)
    journal = folder / "results/attempts.jsonl"
    if journal.exists():
        for lineno, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Malformed development journal line {lineno}") from error
            if (event.get("event") == "started" and
                    (not isinstance(event.get("time_unix"), (int, float)) or
                     event["time_unix"] < receipt["registered_unix"])):
                raise ValueError("Development started before sensitivity preregistration")
    return path


def _targets(row, window):
    score = row.get(window) or {}
    values = score.get("targets")
    if not isinstance(values, list) or len(values) != 19:
        raise ValueError("Missing 19-target score row")
    if any(value is not None and
           (not isinstance(value, (int, float)) or not math.isfinite(value))
           for value in values):
        raise ValueError("Nonfinite or nonnumeric score target")
    mask = score.get("target_mask")
    if mask is not None and mask != [value is not None for value in values]:
        raise ValueError("Target observation mask differs")
    return values, bool(score.get("full_horizon"))


def _case_delta(candidate, baseline, window):
    left, left_full = _targets(candidate, window)
    right, right_full = _targets(baseline, window)
    delta = [a - b if a is not None and b is not None else None
             for a, b in zip(left, right)]
    return delta, bool(left_full and right_full and all(v is not None for v in delta)), left, right


def _group_rows(items):
    sums = [0.0] * 19
    counts = [0] * 19
    complete, unknown, new_p0, resolved_p0 = [], [], [], []
    individual = {}
    for sid, (delta, full, candidate, baseline) in sorted(items.items()):
        individual[sid] = {"complete_paired_window": full,
                           "delta_targets": delta,
                           "candidate_targets": candidate,
                           "secure_targets": baseline}
        (complete if full else unknown).append(sid)
        if full:
            if baseline[0] == 0 and candidate[0] == 1:
                new_p0.append(sid)
            if baseline[0] == 1 and candidate[0] == 0:
                resolved_p0.append(sid)
        for index, value in enumerate(delta):
            if value is not None:
                sums[index] += value
                counts[index] += 1
    return {"registered_case_count": len(items),
            "complete_paired_case_count": len(complete),
            "complete_paired_case_ids": complete,
            "unknown_case_ids": unknown,
            "paired_observable_count_by_target": counts,
            "sum_delta_on_observed": [value if n else None
                                      for value, n in zip(sums, counts)],
            "new_P0_failure_case_ids": new_p0,
            "resolved_P0_failure_case_ids": resolved_p0,
            "individual_cases": individual}


def summarize_pressure_crossing(plan, manifest, summary, *, window="primary_score",
                                required_points=(0, 1, 2, 3, 4), anchor_point=2):
    """Summarize every crossed root, condition, pressure and policy pair.

    The supplied plan/summary must have already passed the raw audit. This
    function independently requires a complete registered Cartesian product,
    distinct root-pressure scenes and identical policy support at every point.
    """
    points = tuple(int(x) for x in required_points)
    if not points or len(set(points)) != len(points) or anchor_point not in points:
        raise ValueError("Invalid required pressure points or anchor")
    ids = list(plan.get("scenario_ids", []))
    policies = [spec["policy_id"] for spec in plan.get("policy_specs", [])]
    if (not ids or not policies or len(ids) != len(set(ids)) or
            len(policies) != len(set(policies)) or "secure_milp" not in policies):
        raise ValueError("Invalid registered scene or policy identifiers")
    lookup = {s["id"]: s for s in manifest["scenarios"]}
    if any(sid not in lookup for sid in ids):
        raise ValueError("Registered scene missing from manifest")
    roots = {}
    for sid in ids:
        scene = lookup[sid]
        if scene.get("is_final_blind"):
            raise ValueError("Final blind scene cannot enter sensitivity")
        root = scene["root_family_id"]
        condition = scene["condition_id"]
        point = scene["pressure_point_index"]
        if point not in points:
            raise ValueError("Unregistered pressure point in crossing")
        key = (condition, root)
        mapping = roots.setdefault(key, {})
        if point in mapping:
            raise ValueError("Duplicate root-pressure scene")
        mapping[point] = sid
    for mapping in roots.values():
        if set(mapping) != set(points):
            raise ValueError("Incomplete registered pressure crossing")
    rows = summary.get("results", [])
    expected = {(sid, pid) for sid in ids for pid in policies}
    actual = [(row.get("scenario_id"), row.get("policy_id")) for row in rows]
    if (len(rows) != len(expected) or len(set(actual)) != len(actual) or
            set(actual) != expected):
        raise ValueError("Duplicate or missing registered result row")
    by_pair = {(row["scenario_id"], row["policy_id"]): row for row in rows}
    for (_, root), mapping in roots.items():
        reference = lookup[mapping[anchor_point]]
        manifest_fields = ("condition_id", "time_block_id", "base_state_id",
                           "exogenous_stream_id", "initialization_seed")
        if any(reference.get(key) is None for key in manifest_fields):
            raise ValueError("Missing same-root manifest identity")
        schedule = _event_schedule(reference)
        initial_hashes, stream_ids = set(), set()
        for sid in mapping.values():
            scene = lookup[sid]
            if scene.get("experiment_role") != "development":
                raise ValueError("Sensitivity scene is not a development root")
            for key in manifest_fields:
                if scene.get(key) != reference[key]:
                    raise ValueError("Same-root manifest identity differs: " + key)
            if _event_schedule(scene) != schedule:
                raise ValueError("Same-root exogenous event schedule differs")
            baseline_binding = by_pair[(sid, "secure_milp")].get("binding") or {}
            initial = baseline_binding.get("initial_state_sha256")
            stream = baseline_binding.get("exogenous_stream_id")
            if (baseline_binding.get("passed") is not True or not initial or
                    stream != scene["exogenous_stream_id"]):
                raise ValueError("Raw baseline initial state or exogenous stream differs")
            initial_hashes.add(initial)
            stream_ids.add(stream)
            for pid in policies:
                binding = by_pair[(sid, pid)].get("binding") or {}
                if (binding.get("passed") is not True or
                        binding.get("initial_state_sha256") != initial or
                        binding.get("exogenous_stream_id") != stream):
                    raise ValueError("Raw policy initial state or exogenous stream differs")
        if len(initial_hashes) != 1 or len(stream_ids) != 1:
            raise ValueError("Same-root actual initial state or exogenous stream differs across pressure")
    by_group = {}
    case_deltas = {}
    for (condition, root), mapping in sorted(roots.items()):
        for point, sid in sorted(mapping.items()):
            baseline = by_pair[(sid, "secure_milp")]
            for pid in policies:
                delta = _case_delta(by_pair[(sid, pid)], baseline, window)
                case_deltas[(condition, root, point, pid)] = delta
                by_group.setdefault(condition, {}).setdefault(str(point), {}).setdefault(pid, {})[sid] = delta
    groups = {condition: {point: {pid: _group_rows(items)
                                  for pid, items in policies_map.items()}
                          for point, policies_map in points_map.items()}
              for condition, points_map in by_group.items()}
    crossings = {}
    conditions = sorted({condition for condition, _ in roots})
    for pid in policies:
        per_policy = crossings.setdefault(pid, {})
        for condition in conditions:
            per_condition = per_policy.setdefault(condition, {})
            condition_roots = sorted(root for c, root in roots if c == condition)
            for point in points:
                sums = [0.0] * 19
                counts = [0] * 19
                paired, unknown = [], []
                for root in condition_roots:
                    current, current_full, _, _ = case_deltas[(condition, root, point, pid)]
                    anchor, anchor_full, _, _ = case_deltas[(condition, root, anchor_point, pid)]
                    if not current_full or not anchor_full:
                        unknown.append(root)
                        continue
                    paired.append(root)
                    for index, (a, b) in enumerate(zip(current, anchor)):
                        sums[index] += a - b
                        counts[index] += 1
                per_condition[str(point)] = {
                    "registered_root_count": len(condition_roots),
                    "paired_root_count": len(paired),
                    "paired_root_ids": paired,
                    "unknown_root_ids": unknown,
                    "paired_observable_count_by_target": counts,
                    "sum_effect_change_on_observed": [x if n else None
                                                      for x, n in zip(sums, counts)]}
    return {"schema": "policy_search.crossed_pressure_sensitivity.v1",
            "window": window, "pressure_point_indices": list(points),
            "anchor_pressure_point_index": anchor_point,
            "pressure_points_are_composite_assumptions": True,
            "not_hospital_event_probabilities": True,
            "same_root_manifest_and_raw_initial_stream_checked": True,
            "same_root_event_schedule_checked_excluding_pressure_magnitude": True,
            "registered_scene_count": len(ids),
            "registered_root_count": len(roots),
            "policy_ids": policies,
            "by_condition_and_pressure": groups,
            "within_root_effect_change_from_anchor": crossings}


def analyze_audited_batch(run_dir, batch="development"):
    """Require a passing exact-byte audit and rescore only registered data."""
    from tools.policy_search.preference_v2 import _verified_inputs

    if batch != "development":
        raise ValueError("Crossed-pressure sensitivity applies to development only")
    run = Path(run_dir)
    preregistration = verify_preregistration(run)
    folder = run / batch
    plan_path = folder / "registered_plan.json"
    summaries = sorted((folder / "results").glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("Registered batch has no summary")
    summary_path = summaries[-1]
    audit_path = folder / "AUDIT.json"
    plan, summary, audit = _verified_inputs(
        plan_path, summary_path, audit_path,
        run / "design/PREFERENCE_V2_SOURCE_FREEZE.json",
        run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json")
    manifest_path = Path(plan["manifest_path"])
    if _sha(manifest_path) != plan["manifest_sha256"]:
        raise ValueError("Manifest bytes changed after sensitivity registration")
    manifest = _read(manifest_path)
    outputs = {"schema": "policy_search.audited_crossed_pressure.v1",
               "plan_signature": plan["signature"],
               "plan_sha256": _sha(plan_path),
               "summary_sha256": _sha(summary_path),
               "audit_sha256": _sha(audit_path),
               "audit_passed": audit["passed"],
               "manifest_sha256": _sha(manifest_path),
               "sensitivity_source_sha256": _sha(__file__),
               "sensitivity_preregistration_sha256": _sha(preregistration),
               "windows": {}}
    for window in ("primary_score", "full_observation"):
        outputs["windows"][window] = summarize_pressure_crossing(
            plan, manifest, summary, window=window)
    output = folder / "SENSITIVITY_BY_CONDITION_PRESSURE.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(outputs, stream, indent=2, ensure_ascii=False, allow_nan=False)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--batch", default="development")
    parser.add_argument("--preregister", action="store_true")
    args = parser.parse_args()
    print(preregister_sensitivity(args.run_dir) if args.preregister else
          analyze_audited_batch(args.run_dir, args.batch))


if __name__ == "__main__":
    main()
