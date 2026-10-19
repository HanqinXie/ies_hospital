"""Pre-register v9 local perturbations on twelve already-seen v8 backgrounds.

Coordinates are guided by disclosed v8 evidence. Generation reads only signed
source definitions, never saved outcomes or the original final backgrounds.
Development, boundary, and confirmation denominators remain explicit.
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


STUDY_NAME = "hospital_ies_state_dispatch_v9"
VERSION = "state_dispatch.v9.local_design.v1"
SEED = 20261016
V8_MANIFEST = Path("outputs/v8_four_scenarios_deepening_20261016_run01/new_data/global_manifest.json")
V8_PROPOSAL = Path("outputs/v8_four_scenarios_deepening_20261016_run01/design/SOURCE_PROPOSAL.json")
LOAD_FILE = Path("inputs/loads/hospital_ies_demand_8760.csv")
CONTRACT_FILE = Path("configs/p1b_execution_research.json")
SEEN_DAYS = (3, 4, 51, 99, 123, 180, 187, 222, 228, 298, 341, 363)
COHORT_COUNTS = {"development": 32, "boundary": 59, "holdout": 28}
HEAT_DAYS = (99, 341, 363, 187, 298)
HEAT_VALUES = (.35, .4125, .475, .5375, .60)
WINDOW_VALUES = (15., 18.75, 22.5, 26.25, 30.)
SHOCK_LEVELS = {
    "elevated": ({"e": .75, "h": .35, "c": .5}, .1),
    "boundary_candidate": ({"e": 1.4, "h": .6, "c": .9}, .35),
}
SEVERITY = {0.: "full_outage", .25: "severe", .5: "moderate"}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _signed(value):
    return isinstance(value, dict) and value.get("signature") == digest(
        {k: v for k, v in value.items() if k != "signature"})


def _write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _source(root):
    source, proposal = _read(root / V8_MANIFEST), _read(root / V8_PROPOSAL)
    if (not _signed(source) or not _signed(proposal)
            or len(source.get("scenarios", [])) != 528
            or source.get("four_scenario_roots") != 12
            or source.get("policy_search_final_12_backgrounds_unopened") is not True
            or source.get("four_scenario_proposal_file_sha256") != _sha(root / V8_PROPOSAL)
            or source.get("four_scenario_source_signature") != proposal["signature"]):
        raise ValueError("Frozen v8 source definition, signature or sealed declaration differs")
    templates = {}
    for scene in source["scenarios"]:
        if scene.get("is_final_blind") or scene.get("experiment_role") != "train":
            raise ValueError("V8 source contains a non-development or final scenario")
        if scene.get("design_axis") != "main":
            continue
        name = scene["analysis_class"]
        if name in ("L2", "L4a", "L4b") and not (
                scene.get("shock_mode") == "h" and scene.get("stratum") == "elevated"):
            continue
        templates[(int(scene["source_day_index"]), name)] = scene
    expected = {(day, name) for day in SEEN_DAYS
                for name in ("N2", "N4", "L1", "L2", "L3", "L4a", "L4b")}
    if set(templates) != expected or len({s["root_family_id"] for s in templates.values()}) != 12:
        raise ValueError("Frozen v8 seen-root/template inventory differs")
    return source, templates


def _coordinates():
    """Explicit registered rows; no adaptive outcome read occurs here."""
    rows = []
    def add(cohort, day, name, axis, value, **options):
        rows.append(dict(cohort=cohort, day=day, name=name, axis=axis,
                         value=value, **options))
    for day in SEEN_DAYS:
        add("development", day, "N2", "normal_cost", 0., delay=0.)
    for day, device, availability in ((51, "GRID", 0.), (123, "GRID", 0.),
                                      (363, "GRID", 0.), (228, "GT_1", .25)):
        for name in ("L1", "L3"):
            add("development", day, name, "fault_context", f"{device}={availability}",
                fault=(device, availability), delay=20.)
    for day in (99, 341, 363, 180):
        for name in ("L2", "L4a"):
            add("development", day, name, "heat_readiness", .475,
                mode="h", amplitude=.475, delay=22.5)
    compound = ((51, "GRID", 0., "e", "elevated"),
                (187, "ABS", .25, "mixed", "elevated"),
                (298, "DIESEL", .5, "mixed", "boundary_candidate"),
                (341, "GT_2", .25, "h", None))
    for day, device, availability, mode, level in compound:
        add("development", day, "L4b", "compound_response", mode,
            fault=(device, availability), mode=mode, level=level,
            amplitude=.475 if level is None else None, delay=22.5)
    for day in HEAT_DAYS:
        for value in HEAT_VALUES:
            add("boundary", day, "L2", "heat_amplitude", value,
                mode="h", amplitude=value, delay=15.)
    for day in (99, 341, 363):
        for value in HEAT_VALUES[1:-1]:
            add("boundary", day, "L4a", "heat_amplitude", value,
                mode="h", amplitude=value, delay=15.)
    for value in WINDOW_VALUES:
        for day, level in ((187, "elevated"), (298, "boundary_candidate")):
            add("boundary", day, "L4a", "response_window", value,
                mode="mixed", level=level, delay=value)
        add("boundary", 51, "L3", "response_window", value,
            fault=("GRID", 0.), delay=value)
    for phase in (5., 10.):
        add("boundary", 298, "L4a", "event_phase", phase, phase=phase,
            mode="mixed", level="boundary_candidate", delay=22.5)
        for name in ("L1", "L3"):
            add("boundary", 123, name, "event_phase", phase,
                phase=phase, fault=("GRID", 0.), delay=22.5)
    for day, device in ((51, "GRID"), (363, "TOWER_1")):
        for duration in (240., 300.):
            add("boundary", day, "L4b", "cool_duration", duration,
                fault=(device, 0.), mode="c", amplitude=.9,
                duration=duration, delay=22.5)
    for day in HEAT_DAYS:
        for name, value in (("L2", .4625), ("L4a", .525)):
            add("holdout", day, name, "new_heat_timing", value,
                mode="h", amplitude=value, phase=7.5, delay=25.)
    for day, device, availability in ((51, "GRID", 0.), (123, "GRID", 0.),
                                      (363, "GRID", 0.), (228, "GT_1", .25),
                                      (51, "HRSG_1", .25)):
        for name in ("L1", "L3"):
            add("holdout", day, name, "new_fault_timing", f"{device}={availability}",
                fault=(device, availability), phase=7.5, delay=25.)
    for day, device, availability, mode, level in compound:
        add("holdout", day, "L4b", "new_compound_timing", mode,
            fault=(device, availability), mode="mixed" if day == 51 else mode,
            level=level, amplitude=.525 if level is None else None,
            phase=7.5, delay=25.)
    for day, device in ((51, "GRID"), (363, "TOWER_1")):
        add("holdout", day, "L4b", "new_cool_timing", 270., fault=(device, 0.),
            mode="c", amplitude=.9, duration=270., phase=7.5, delay=25.)
    for day in (187, 228):
        add("holdout", day, "L2", "new_cool_timing", 270., mode="c",
            amplitude=.75, duration=270., phase=7.5, delay=25.)
    return rows


def _shock(row):
    mode, level = row.get("mode"), row.get("level")
    if mode is None:
        return {}, {}
    if level is None:
        values, p0_e = {carrier: float(row["amplitude"]) for carrier in ("e", "h", "c")}, 0.
    else:
        values, p0_e = SHOCK_LEVELS[level]
    shock = {carrier: float(value) if mode in (carrier, "mixed") else 0.
             for carrier, value in values.items()}
    priority = {"P0": {"e": p0_e}} if p0_e and mode in ("e", "mixed") else {}
    return shock, priority


def _scene(row, templates):
    old = templates[(row["day"], row["name"])]
    scene = copy.deepcopy(old)
    name, cohort = row["name"], row["cohort"]
    phase = float(row.get("phase", 0.))
    scene["response_delay_minutes"] = float(row["delay"])
    scene["shock"], scene["shock_by_priority"] = _shock(row)
    scene["faults"] = {}
    events = []
    if name in ("L1", "L3", "L4b"):
        device, availability = row["fault"]
        scene["initiating_device"] = device
        scene["initiating_domain"] = (
            "backup" if device == "DIESEL" else
            "electric" if device == "GRID" or device.startswith("GT_") else
            "steam" if device.startswith(("HRSG_", "BOILER_")) else "cooling")
        scene["faults"] = {device: float(availability)}
        scene["fault_severity_layer"] = SEVERITY[availability]
        scene["fault_start_minute"] = float(old["fault_start_minute"]) + phase
        scene["fault_duration_minutes"] = float(row.get("duration", old["fault_duration_minutes"]))
        events.append(dict(origin="exogenous", kind="device_fault", device=device,
                           availability=float(availability), start_minute=scene["fault_start_minute"],
                           end_minute=scene["fault_start_minute"] + scene["fault_duration_minutes"]))
    else:
        for key in ("initiating_device", "initiating_domain", "fault_severity_layer", "fault_start_minute", "fault_duration_minutes"):
            scene.pop(key, None)
    if name in ("L2", "L4a", "L4b"):
        scene["shock_start_minute"] = float(old["shock_start_minute"]) + phase
        scene["shock_duration_minutes"] = float(row.get("duration", old["shock_duration_minutes"]))
        events.append(dict(origin="exogenous", kind="load_shock",
                           start_minute=scene["shock_start_minute"],
                           end_minute=scene["shock_start_minute"] + scene["shock_duration_minutes"],
                           shock=copy.deepcopy(scene["shock"]),
                           shock_by_priority=copy.deepcopy(scene["shock_by_priority"])))
    else:
        for key in ("shock_start_minute", "shock_duration_minutes", "shock_mode", "stratum"):
            scene.pop(key, None)
    scene["exogenous_events"] = events
    physical = {key: scene[key] for key in ("root_family_id", "base_state_id", "start_hour",
                "step_minutes", "duration_steps", "grid_limit_kW", "pre_event_grid_limit_kW",
                "enable_propagation", "response_delay_minutes", "exogenous_events")}
    signature = digest(physical)
    group = "v9_match:" + digest([cohort, row["axis"], scene["root_family_id"],
                                  events, row["delay"]])[:24]
    holdout = cohort == "holdout"
    binding = ("previously_binding_GRID_context" if name in ("L1", "L3") and
               scene["initiating_device"] == "GRID" else
               "previously_nonbinding_fault_context" if name in ("L1", "L3") else
               "not_preclassified")
    scene.update(
        id="v9_state_" + signature[:24], sample_schema_version=VERSION,
        physical_scenario_signature=signature, source_template_id=old["id"],
        study_name=STUDY_NAME, analysis_cohort=cohort, sampling_cohort=cohort,
        design_axis=row["axis"], design_axis_value=row["value"],
        design_level=str(row["value"]), design_cell_id=group, matched_group_id=group,
        purpose=f"v9_state_dispatch_{cohort}_{row['axis']}",
        sampling_role="same_seen_background_local_mechanism_study",
        sampling_method="v8_evidence_guided_explicit_local_preregistration",
        distribution_id=VERSION + ".q_design", experiment_role="confirmation" if holdout else "development",
        selection_allowed=not holdout, fit_allowed=False, is_final_blind=False,
        same_background_new_perturbation=name != "N2",
        binding_expectation_from_v8=binding,
        sampling_weight_is_failure_probability=False,
        exogenous_stream_id="v9_exogenous:" + digest([scene["root_family_id"], events]),
        actual_p0_load_multiplier={carrier: (1 + scene["shock"].get(carrier, 0.)) *
            (1 + scene["shock_by_priority"].get("P0", {}).get(carrier, 0.))
            for carrier in ("e", "h", "c")},
    )
    if row.get("mode") is not None:
        scene["shock_mode"] = row["mode"]
        scene["stratum"] = row.get("level") or "registered_local_coordinate"
    if validate_observation_support(scene) != old["full_source_support"]:
        raise ValueError("Local design changes frozen source support")
    if any(not 60 <= event["start_minute"] < event["end_minute"] <= 450 for event in events):
        raise ValueError("Local event lacks supported history or recovery tail")
    validate_scenario(scene)
    return scene


def build_proposal(workspace_root=ROOT, *, seed=SEED):
    if type(seed) is not int or seed < 0:
        raise ValueError("Design seed must be a nonnegative integer")
    root = Path(workspace_root).resolve()
    source, templates = _source(root)
    scenes = [_scene(row, templates) for row in _coordinates()]
    if (dict(Counter(s["analysis_cohort"] for s in scenes)) != COHORT_COUNTS
            or len({s["physical_scenario_signature"] for s in scenes}) != len(scenes)):
        raise ValueError("Local cohort count or unique physical coordinate differs")
    groups = defaultdict(dict)
    for scene in scenes:
        if scene["analysis_class"] in ("L1", "L3"):
            groups[scene["matched_group_id"]][scene["analysis_class"]] = scene
    matched = []
    for group, pair in sorted(groups.items()):
        if set(pair) != {"L1", "L3"}:
            continue  # Single-axis response-window L3 is deliberately not a L1/L3 pair.
        a, b = pair["L1"], pair["L3"]
        if a["exogenous_events"] != b["exogenous_events"] or a["base_state_id"] != b["base_state_id"]:
            raise ValueError("L1/L3 fault pair does not share exogenous prefix")
        matched.append(dict(matched_group_id=group, analysis_cohort=a["analysis_cohort"],
                            source_day_index=a["source_day_index"], root_family_id=a["root_family_id"],
                            binding_expectation_from_v8=a["binding_expectation_from_v8"],
                            scene_ids={name: pair[name]["id"] for name in ("L1", "L3")}))
    proposal = dict(
        version=VERSION + ".proposal", study_name=STUDY_NAME, seed=seed,
        source_v8_manifest_signature=source["signature"], source_v8_seen_root_count=12,
        new_independent_background_count=0, final_12_backgrounds_unopened=True,
        holdout_semantics="same_seen_background_new_perturbation_not_cross_background",
        holdout_execution_requires_policy_freeze=True,
        selection_semantics="development and boundary outcomes may guide development; confirmation outcomes may not",
        source_outcomes_read_during_generation=False,
        design_evidence_semantics="explicit coordinates guided by disclosed v8 outcomes; not outcome-free sampling",
        scenarios=scenes, matched_fault_groups=matched,
        ids_by_cohort={cohort: [s["id"] for s in scenes if s["analysis_cohort"] == cohort]
                       for cohort in COHORT_COUNTS},
        analysis_cohort_counts=dict(COHORT_COUNTS),
        analysis_class_counts=dict(Counter(s["analysis_class"] for s in scenes)),
        design_coverage=dict(independent_seen_background_count=12, new_independent_background_count=0,
            scenario_counts_by_cohort_axis=dict(Counter(f"{s['analysis_cohort']}|{s['design_axis']}" for s in scenes)),
            roots_by_cohort={cohort: sorted({s["source_day_index"] for s in scenes if s["analysis_cohort"] == cohort})
                             for cohort in COHORT_COUNTS},
            heat_L2_boundary_days=list(HEAT_DAYS), heat_L2_boundary_values=list(HEAT_VALUES),
            response_window_boundary_values=list(WINDOW_VALUES),
            diagnostic_specs_in_this_manifest=0),
        missing_combinations=[
            dict(scope="heat", missing="other roots and all untested amplitudes", reason="targeted local bracket; no continuous safety claim"),
            dict(scope="response_window", missing="other contexts and intermediate windows", reason="three fixed mechanisms only; no interpolation"),
            dict(scope="event_phase", missing="other event phases and roots", reason="limited two-context phase diagnostic"),
            dict(scope="cooling", missing="other sustained deficits and thermal calibration", reason="existing model support only; no manufactured labels"),
            dict(scope="background", missing="unseen backgrounds and original final twelve", reason="sealed final backgrounds remain inaccessible"),
        ],
        probability_semantics=dict(p_reference=None, importance_weight=None, not_hospital_failure_probability=True),
        source_sha256={path.as_posix(): _sha(root / path)
                       for path in (V8_MANIFEST, V8_PROPOSAL, LOAD_FILE, CONTRACT_FILE)},
    )
    proposal["source_sha256"]["tools/state_dispatch/data_design.py"] = _sha(Path(__file__))
    proposal["signature"] = digest(proposal)
    return proposal


def verify_proposal(proposal, workspace_root=ROOT):
    if not _signed(proposal) or proposal != build_proposal(workspace_root, seed=proposal.get("seed")):
        raise ValueError("V9 local proposal differs from immutable source registration")
    return True


def build_state_dispatch_manifest(proposal, workspace_root=ROOT, *, proposal_path=None):
    root = Path(workspace_root).resolve()
    verify_proposal(proposal, root)
    source, _ = _source(root)
    splits = {}
    for scene in proposal["scenarios"]:
        support = scene["full_source_support"]
        splits[scene["id"]] = dict(
            group_id=scene["root_family_id"], split=scene["experiment_role"],
            experiment_role=scene["experiment_role"], fit_allowed=False,
            selection_allowed=scene["selection_allowed"],
            development_only=scene["analysis_cohort"] != "holdout", split_version=VERSION,
            embargo_minutes_each_side=60, raw_start_minute=support["raw_start_minute"],
            raw_end_minute=support["raw_end_minute"], time_block_id=scene["time_block_id"],
            purpose=scene["purpose"], is_final_blind=False,
            support_independence_claim=False, split_semantics="execution_role_only_same_background_support_shared")
    manifest = build_manifest(root, proposal["scenarios"], ("priority_milp", "secure_milp"),
                              source["execution_contract"], source["schemas"], source["options"], splits)
    manifest.pop("signature")
    manifest.update(
        manifest_version=VERSION + ".manifest", study_name=STUDY_NAME,
        state_dispatch_source_signature=proposal["signature"],
        state_dispatch_proposal_file_sha256=_sha(proposal_path) if proposal_path else None,
        source_v8_manifest_signature=source["signature"], source_sha256=proposal["source_sha256"],
        ids_by_cohort=proposal["ids_by_cohort"], analysis_cohort_counts=proposal["analysis_cohort_counts"],
        matched_fault_groups=proposal["matched_fault_groups"], design_coverage=proposal["design_coverage"],
        missing_combinations=proposal["missing_combinations"],
        new_independent_background_count=0, holdout_semantics=proposal["holdout_semantics"],
        holdout_execution_requires_policy_freeze=True,
        policy_search_final_12_backgrounds_unopened=True,
    )
    manifest["signature"] = digest(manifest)
    return manifest


_COVERAGE_FIELDS = ("id", "analysis_cohort", "analysis_class", "root_family_id", "source_day_index",
    "season_quarter", "source_template_id", "design_axis", "design_axis_value", "matched_group_id",
    "initiating_device", "faults", "shock", "shock_by_priority", "response_delay_minutes",
    "fault_start_minute", "fault_duration_minutes", "shock_start_minute", "shock_duration_minutes",
    "experiment_role", "selection_allowed", "fit_allowed", "binding_expectation_from_v8",
    "physical_scenario_signature", "same_background_new_perturbation", "is_final_blind")


def freeze(workspace_root, run_dir, *, seed=SEED):
    """Write definitions and byte-bound manifest once; never start an episode."""
    root, run = Path(workspace_root).resolve(), Path(run_dir).resolve()
    paths = dict(proposal_path=run / "design/SOURCE_PROPOSAL.json",
                 manifest_path=run / "new_data/global_manifest.json",
                 coverage_path=run / "design/DESIGN_COVERAGE.csv",
                 receipt_path=run / "design/FREEZE_RECEIPT.json")
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("V9 local design is already frozen or has retained partial files")
    proposal = build_proposal(root, seed=seed)
    _write_new(paths["proposal_path"], proposal)
    manifest = build_state_dispatch_manifest(proposal, root, proposal_path=paths["proposal_path"])
    _write_new(paths["manifest_path"], manifest)
    with paths["coverage_path"].open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_COVERAGE_FIELDS)
        writer.writeheader()
        for scene in proposal["scenarios"]:
            writer.writerow({key: json.dumps(scene[key], sort_keys=True) if isinstance(scene.get(key), dict)
                              else scene.get(key) for key in _COVERAGE_FIELDS})
    receipt = {key: str(path) for key, path in paths.items()}
    receipt.update({key.replace("_path", "_sha256"): _sha(path)
                    for key, path in paths.items() if key != "receipt_path"})
    receipt.update(study_name=STUDY_NAME, version=VERSION, roots=12, scenarios=119,
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
