"""Explicit v10 development and confirmation coordinates on 12 seen roots.

Only signed v9 definitions are read. The old confirmation is disclosed
development evidence; new confirmation changes timing or response windows and
does not claim independent backgrounds. No episode runs during registration.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import build_manifest, digest
from hospital_ies.resilience import validate_scenario
from tools.control_improvement.data_design import validate_observation_support

STUDY_NAME = "hospital_ies_dispatch_closeout_v10"
VERSION = "dispatch_closeout.v10.local_design.v1"
SEED = 20261016
V9_MANIFEST = Path("outputs/v9_state_dispatch_20261016_run01/new_data/global_manifest.json")
SEEN_DAYS = (3, 4, 51, 99, 123, 180, 187, 222, 228, 298, 341, 363)
COHORT_COUNTS = {"development": 24, "holdout": 16}
PHYSICAL_FIELDS = ("root_family_id", "base_state_id", "start_hour", "step_minutes",
    "duration_steps", "grid_limit_kW", "pre_event_grid_limit_kW", "enable_propagation",
    "response_delay_minutes", "exogenous_events")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _source(root):
    source = _read(root / V9_MANIFEST)
    if (source.get("signature") != digest({k: v for k, v in source.items() if k != "signature"})
            or source.get("study_name") != "hospital_ies_state_dispatch_v9"
            or source.get("policy_search_final_12_backgrounds_unopened") is not True
            or len(source.get("scenarios", [])) != 119
            or {int(s["source_day_index"]) for s in source["scenarios"]} != set(SEEN_DAYS)
            or len({s["root_family_id"] for s in source["scenarios"]}) != 12
            or any(s.get("is_final_blind") for s in source["scenarios"])):
        raise ValueError("Frozen v9 seen-source definition or sealed declaration differs")
    return source


def _pick(source, day, name, cohort, axis=None):
    rows = [s for s in source["scenarios"] if s["source_day_index"] == day
            and s["analysis_class"] == name and s["analysis_cohort"] == cohort
            and (axis is None or s["design_axis"] == axis)]
    if len(rows) != 1:
        raise ValueError(f"Expected unique v9 template: {(day, name, cohort, axis)}")
    return rows[0]


def _coordinates(source):
    rows = []
    def add(day, name, source_cohort, axis=None, *, cohort="development", delay=None, phase=0.):
        rows.append((copy.deepcopy(_pick(source, day, name, source_cohort, axis)),
                     cohort, delay, float(phase)))
    for day in SEEN_DAYS:
        add(day, "N2", "development")
    for day in (51, 123, 363):
        for name in ("L1", "L3"):
            add(day, name, "development")
    for name in ("L2", "L4a"):
        add(298, name, "holdout", "new_heat_timing")
    for day in (51, 187, 298):
        add(day, "L4b", "holdout", "new_compound_timing")
    add(228, "L1", "development")
    for day in (51, 123, 363):
        for name in ("L1", "L3"):
            add(day, name, "development", cohort="holdout", delay=23.75, phase=3.25)
    for day in (298, 99):
        add(day, "L2", "holdout", "new_heat_timing", cohort="holdout", delay=27.5, phase=-2.5)
    for day in (187, 228):
        add(day, "L2", "holdout", "new_cool_timing", cohort="holdout", delay=27.5, phase=-2.5)
    for day in (51, 187, 298, 341):
        add(day, "L4b", "holdout", "new_compound_timing", cohort="holdout", delay=27.5, phase=-2.5)
    for day in (298, 99):
        add(day, "L4a", "holdout", "new_heat_timing", cohort="holdout", delay=27.5, phase=3.25)
    return rows


def _scene(old, cohort, delay, phase):
    scene = copy.deepcopy(old)
    if delay is not None:
        scene["response_delay_minutes"] = float(delay)
    if phase:
        for event in scene["exogenous_events"]:
            event["start_minute"] += phase
            event["end_minute"] += phase
        for key in ("fault_start_minute", "shock_start_minute"):
            if key in scene:
                scene[key] += phase
    signature = digest({key: scene[key] for key in PHYSICAL_FIELDS})
    holdout = cohort == "holdout"
    group = "v10_match:" + digest([cohort, scene["base_state_id"],
        scene["response_delay_minutes"], scene["exogenous_events"]])[:24]
    change = {"source_response_delay_minutes": old["response_delay_minutes"],
        "response_delay_minutes": scene["response_delay_minutes"],
        "event_phase_delta_minutes": phase,
        "source_exogenous_events": copy.deepcopy(old["exogenous_events"]),
        "exogenous_events": copy.deepcopy(scene["exogenous_events"]),
        "semantics": "new perturbation" if holdout else "disclosed v9 physical regression coordinate"}
    scene.update(id="v10_closeout_" + digest([cohort, signature])[:24],
        sample_schema_version=VERSION, study_name=STUDY_NAME,
        physical_scenario_signature=signature, source_v9_scenario_id=old["id"],
        source_v9_analysis_cohort=old["analysis_cohort"], source_template_id=old["id"],
        analysis_cohort=cohort, sampling_cohort=cohort,
        experiment_role="confirmation" if holdout else "development",
        selection_allowed=not holdout, fit_allowed=False, is_final_blind=False,
        design_axis="closeout_new_timing" if holdout else "closeout_regression",
        design_axis_value={"response_delay_minutes": scene["response_delay_minutes"],
                           "event_phase_delta_minutes": phase},
        design_level="new_timing" if holdout else "disclosed_regression",
        design_cell_id=group, matched_group_id=group,
        registered_coordinate_change=change,
        purpose="v10_dispatch_closeout_" + cohort,
        distribution_id=VERSION + ".q_design", sampling_method="explicit_disclosed_v9_coordinates",
        sampling_role="same_seen_background_local_mechanism_study",
        same_background_new_perturbation=holdout,
        sampling_weight_is_failure_probability=False,
        exogenous_stream_id="v10_exogenous:" + digest([scene["root_family_id"], scene["exogenous_events"]]))
    if validate_observation_support(scene) != old["full_source_support"]:
        raise ValueError("Closeout coordinate changes original source support")
    if any(not 60 <= e["start_minute"] < e["end_minute"] <= 450 for e in scene["exogenous_events"]):
        raise ValueError("Closeout event lacks supported history or tail")
    validate_scenario(scene)
    return scene


def build_proposal(workspace_root=ROOT, *, seed=SEED):
    if type(seed) is not int or seed < 0:
        raise ValueError("Design seed must be a nonnegative integer")
    root = Path(workspace_root).resolve()
    source = _source(root)
    scenes = [_scene(*row) for row in _coordinates(source)]
    if (dict(Counter(s["analysis_cohort"] for s in scenes)) != COHORT_COUNTS
            or len({s["physical_scenario_signature"] for s in scenes}) != len(scenes)):
        raise ValueError("Closeout denominator or unique physical coordinates differ")
    old_physical = {s["physical_scenario_signature"] for s in source["scenarios"]}
    if any(s["physical_scenario_signature"] in old_physical for s in scenes if s["analysis_cohort"] == "holdout"):
        raise ValueError("Confirmation repeats an exposed v9 physical coordinate")
    groups = defaultdict(dict)
    for scene in scenes:
        if scene["analysis_class"] in ("L1", "L3"):
            groups[scene["matched_group_id"]][scene["analysis_class"]] = scene
    matched = [dict(matched_group_id=key, analysis_cohort=pair["L1"]["analysis_cohort"],
        source_day_index=pair["L1"]["source_day_index"],
        root_family_id=pair["L1"]["root_family_id"],
        scene_ids={name: pair[name]["id"] for name in ("L1", "L3")})
        for key, pair in sorted(groups.items()) if set(pair) == {"L1", "L3"}]
    proposal = dict(version=VERSION + ".proposal", study_name=STUDY_NAME, seed=seed,
        source_v9_manifest_signature=source["signature"], source_seen_root_count=12,
        new_independent_background_count=0, final_12_backgrounds_unopened=True,
        source_outcomes_read_during_generation=False,
        design_evidence_semantics="disclosed v9 development, boundary and old confirmation guide development",
        holdout_semantics="same_seen_background_new_perturbation_not_cross_background",
        holdout_execution_requires_policy_freeze=True,
        scenarios=scenes, matched_fault_groups=matched,
        ids_by_cohort={cohort: [s["id"] for s in scenes if s["analysis_cohort"] == cohort]
                       for cohort in COHORT_COUNTS},
        analysis_cohort_counts=COHORT_COUNTS,
        analysis_class_counts=dict(Counter(s["analysis_class"] for s in scenes)),
        design_coverage={"independent_seen_background_count": 12,
            "new_independent_background_count": 0,
            "roots_by_cohort": {cohort: sorted({s["source_day_index"] for s in scenes
                if s["analysis_cohort"] == cohort}) for cohort in COHORT_COUNTS},
            "source_v9_cohort_counts": dict(Counter(s["source_v9_analysis_cohort"] for s in scenes)),
            "confirmation_class_counts": dict(Counter(s["analysis_class"] for s in scenes
                if s["analysis_cohort"] == "holdout"))},
        missing_combinations=[{"scope": "background", "missing": "unseen and original final twelve",
                               "reason": "sealed; same-root perturbations are not new backgrounds"},
            {"scope": "coordinates", "missing": "all untested faults, phases, amplitudes and windows",
             "reason": "targeted discrete mechanism study; no continuous safety claim"}],
        probability_semantics={"p_reference": None, "importance_weight": None,
                               "not_hospital_failure_probability": True},
        source_sha256={V9_MANIFEST.as_posix(): _sha(root / V9_MANIFEST),
            "tools/dispatch_closeout/data_design.py": _sha(Path(__file__))})
    proposal["signature"] = digest(proposal)
    return proposal


def verify_proposal(proposal, workspace_root=ROOT):
    if proposal != build_proposal(workspace_root, seed=proposal.get("seed")):
        raise ValueError("Closeout proposal differs from signed source registration")
    return True


def build_dispatch_closeout_manifest(proposal, workspace_root=ROOT, *, proposal_path=None):
    root = Path(workspace_root).resolve()
    verify_proposal(proposal, root)
    source = _source(root)
    splits = {}
    for scene in proposal["scenarios"]:
        support = scene["full_source_support"]
        splits[scene["id"]] = dict(group_id=scene["root_family_id"], split=scene["experiment_role"],
            experiment_role=scene["experiment_role"], fit_allowed=False,
            selection_allowed=scene["selection_allowed"], development_only=scene["selection_allowed"],
            split_version=VERSION, embargo_minutes_each_side=60,
            raw_start_minute=support["raw_start_minute"], raw_end_minute=support["raw_end_minute"],
            time_block_id=scene["time_block_id"], purpose=scene["purpose"], is_final_blind=False,
            support_independence_claim=False,
            split_semantics="execution_role_only_same_background_support_shared")
    manifest = build_manifest(root, proposal["scenarios"], ("priority_milp", "secure_milp"),
        source["execution_contract"], source["schemas"], source["options"], splits)
    manifest.pop("signature")
    manifest.update(manifest_version=VERSION + ".manifest", study_name=STUDY_NAME,
        dispatch_closeout_source_signature=proposal["signature"],
        dispatch_closeout_proposal_file_sha256=_sha(proposal_path) if proposal_path else None,
        source_v9_manifest_signature=source["signature"], source_sha256=proposal["source_sha256"],
        ids_by_cohort=proposal["ids_by_cohort"], analysis_cohort_counts=COHORT_COUNTS,
        matched_fault_groups=proposal["matched_fault_groups"], design_coverage=proposal["design_coverage"],
        missing_combinations=proposal["missing_combinations"], new_independent_background_count=0,
        holdout_semantics=proposal["holdout_semantics"], holdout_execution_requires_policy_freeze=True,
        policy_search_final_12_backgrounds_unopened=True)
    manifest["signature"] = digest(manifest)
    return manifest


def freeze(workspace_root, run_dir, *, seed=SEED):
    root, run = Path(workspace_root).resolve(), Path(run_dir).resolve()
    paths = dict(proposal_path=run / "design/SOURCE_PROPOSAL.json",
        manifest_path=run / "new_data/global_manifest.json",
        coverage_path=run / "design/DESIGN_COVERAGE.csv",
        receipt_path=run / "design/FREEZE_RECEIPT.json")
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("Closeout design already frozen or retains partial files")
    proposal = build_proposal(root, seed=seed)
    _write_new(paths["proposal_path"], proposal)
    manifest = build_dispatch_closeout_manifest(proposal, root, proposal_path=paths["proposal_path"])
    _write_new(paths["manifest_path"], manifest)
    fields = ("id", "analysis_cohort", "analysis_class", "root_family_id", "source_day_index",
        "source_v9_scenario_id", "source_v9_analysis_cohort", "response_delay_minutes",
        "registered_coordinate_change", "physical_scenario_signature", "full_source_support",
        "selection_allowed", "fit_allowed", "is_final_blind")
    with paths["coverage_path"].open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for scene in proposal["scenarios"]:
            writer.writerow({key: json.dumps(scene[key], ensure_ascii=False, sort_keys=True)
                if isinstance(scene.get(key), dict) else scene.get(key) for key in fields})
    receipt = {key: str(path) for key, path in paths.items()}
    receipt.update({key.replace("_path", "_sha256"): _sha(path)
                    for key, path in paths.items() if key != "receipt_path"})
    receipt.update(study_name=STUDY_NAME, version=VERSION, roots=12, scenarios=40,
        analysis_cohort_counts=COHORT_COUNTS, episodes_started=0,
        final_12_backgrounds_unopened=True, signature=manifest["signature"])
    _write_new(paths["receipt_path"], receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("freeze",))
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    print(json.dumps(freeze(args.workspace, args.run_dir, seed=args.seed), ensure_ascii=False))


if __name__ == "__main__":
    main()
