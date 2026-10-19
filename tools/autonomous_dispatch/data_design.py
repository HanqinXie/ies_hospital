"""Metadata-only v11 development registration; original final twelve stay shut."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from hospital_ies.dataset_manifest import build_manifest as _build_manifest, digest
from hospital_ies.resilience import validate_scenario
from tools.control_improvement.data_design import validate_observation_support

VERSION = "autonomous_dispatch.v11.source_design.v1"
STUDY_NAME = "hospital_ies_autonomous_dispatch_v11"
SEED = 20261016
AUTHORITY = Path("outputs/v43_p2b_20261013_run01/experiment/global_manifest.json")
V5 = Path("outputs/v5_policy_search_20261015_run01/design/source_proposal.json")
V6 = Path("outputs/v6_policy_redesign_20261016_run01/design/v6_source_proposal.json")
V7 = Path("outputs/v7_four_scenarios_20261016_run01/design/SOURCE_PROPOSAL.json")
V10 = Path("outputs/v10_dispatch_closeout_20261016_run01/design/SOURCE_PROPOSAL.json")
V10_MANIFEST = Path("outputs/v10_dispatch_closeout_20261016_run01/new_data/global_manifest.json")
FUEL = Path("outputs/v10_dispatch_closeout_20261016_run01/development_fuel/global_manifest.json")
AUDIT_SOURCES = (AUTHORITY, V5, V6, V7,
    Path("outputs/v8_four_scenarios_deepening_20261016_run01/design/SOURCE_PROPOSAL.json"),
    Path("outputs/v8_four_scenarios_clockmatched_20261016_run01/design/SOURCE_PROPOSAL.json"),
    Path("outputs/v8_four_scenarios_fault_cross_20261016_run01/design/SOURCE_PROPOSAL.json"),
    Path("outputs/v9_state_dispatch_20261016_run01/design/SOURCE_PROPOSAL.json"), V10, FUEL)
PHYSICAL_FIELDS = ("root_family_id", "base_state_id", "start_hour", "step_minutes",
    "duration_steps", "grid_limit_kW", "pre_event_grid_limit_kW", "pre_event_warmup_steps",
    "initialization_seed", "automatic_fast_shedding", "stage", "event_class",
    "enable_propagation", "response_delay_minutes", "exogenous_events", "faults",
    "shock", "shock_by_priority", "fault_start_minute", "fault_duration_minutes",
    "shock_start_minute", "shock_duration_minutes", "full_source_support")
DEVICES = ("GRID", *(f"GT_{i}" for i in range(1, 4)), *(f"HRSG_{i}" for i in range(1, 4)),
    *(f"BOILER_{i}" for i in range(1, 4)), *(f"EC_{i}" for i in range(1, 4)),
    "ABS", *(f"TOWER_{i}" for i in range(1, 5)), "DIESEL")
SHOCK_LEVELS = (("ordinary", {"e": .3, "h": .15, "c": .2}, 0.),
    ("elevated", {"e": .75, "h": .35, "c": .5}, .1),
    ("boundary_candidate", {"e": 1.4, "h": .6, "c": .9}, .35))
CLASSES = ("L1", "L2", "L3", "L4a", "L4b")
COHORTS = ("development", "normal", "development_regression", "new_perturbation")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _signed(path):
    value = _read(path)
    if value.get("signature") != digest({k: v for k, v in value.items() if k != "signature"}):
        raise ValueError("Source metadata signature differs: " + str(path))
    return value


def _sealed(root):
    authority = _signed(root / AUTHORITY)
    days = sorted(int(row["time_block_id"].rsplit("_", 1)[1])
                  for row in authority["p2b_time_blocks"] if row["reserved_only"])
    if len(days) != 12 or len(set(days)) != 12:
        raise ValueError("Original sealed final twelve identity differs")
    return days


def _reject_sealed(scene, sealed):
    support = validate_observation_support(scene)
    if support != scene["full_source_support"]:
        raise ValueError("Source full support metadata differs from its physical clock")
    if scene.get("is_final_blind") or any(
            support["raw_start_minute"] < (day + 1) * 1440
            and day * 1440 < support["raw_end_minute"] for day in sealed):
        raise ValueError("Source support intersects sealed final backgrounds")


def _inventory(root, sealed):
    roots, templates = [], {}
    for path, provenance, role in ((V5, "v5_train_reused_in_v6", "train"),
                                    (V6, "v6_new_seen", None), (V7, "v7_v10_seen", None)):
        proposal = _signed(root / path)
        if path in (V5, V6) and proposal.get("sealed_final_days") != sealed:
            raise ValueError("Historical final identity registry differs")
        scenes = proposal.get("scenarios", proposal.get("fixed_scenarios", []))
        if path == V7:
            chosen = [s for s in scenes if s["analysis_class"] == "N2"]
        else:
            chosen = [s for s in scenes if s["pressure_point_index"] == 0
                      and (role is None or s["experiment_role"] == role)]
        if len(chosen) != (30 if path == V5 else 12):
            raise ValueError("Historical development root inventory differs")
        for scene in chosen:
            _reject_sealed(scene, sealed)
            day = (int(scene["start_hour"]) - 1) // 24
            rid = scene["root_family_id"]
            if rid in templates:
                raise ValueError("Duplicate source root")
            templates[rid] = copy.deepcopy(scene)
            roots.append(dict(root_family_id=rid, source_day_index=day,
                start_hour=scene["start_hour"], base_state_id=scene["base_state_id"],
                full_source_support=copy.deepcopy(scene["full_source_support"]),
                season_quarter=min(4, 1 + day // 91), background_status="previously_seen_development",
                provenance=provenance, source_metadata_path=path.as_posix(),
                source_metadata_sha256=_sha(root / path), source_scenario_id=scene["id"]))
    roots.sort(key=lambda row: row["source_day_index"])
    if len(roots) != 54 or len({r["source_day_index"] for r in roots}) != 54:
        raise ValueError("Expected 54 distinct seen calendar roots")
    return roots, templates


def _domain(device):
    return ("backup" if device == "DIESEL" else "electric" if device == "GRID" or device.startswith("GT_")
            else "steam" if device.startswith(("HRSG_", "BOILER_")) else "cooling")


def _physical_signature(scene):
    return digest({key: scene.get(key) for key in PHYSICAL_FIELDS})


def _scene(old, record, index, cell, name):
    scene = copy.deepcopy(old)
    for key in list(scene):
        if key.startswith(("fault_", "shock_", "initiating_", "pressure_")) or key in (
                "faults", "shock", "shock_by_priority", "exogenous_events", "contingency_class",
                "response_clock_anchor", "control_capability_note", "reference_role"):
            scene.pop(key, None)
    normal = name == "N2"
    stage = {"N2": 2, "L1": 1, "L2": 2, "L3": 3, "L4a": 4, "L4b": 4}[name]
    start = float(90 + 30 * (index % 3 if cell == 0 else index % 2))
    duration = float(180 if cell == 0 else 300)
    device = DEVICES[(index + cell * 7) % len(DEVICES)]
    availability = (0., .25, .5)[(index // len(DEVICES) + cell) % 3]
    mode = ("e", "h", "c", "mixed")[(index + cell) % 4]
    level, amplitudes, p0 = SHOCK_LEVELS[0 if cell == 0 else 1 + index % 2]
    group = "v11_cell:" + digest([record["root_family_id"], cell])[:24]
    scene.update(stage=stage, event_class="L2" if normal else name,
        analysis_class=name, event_schema_version="p2a.events.v1", sample_schema_version=VERSION,
        condition_id="v11_" + name, template_type="L2" if normal else name,
        study_name=STUDY_NAME, source_day_index=record["source_day_index"],
        season_quarter=record["season_quarter"], enable_propagation=stage >= 3,
        response_delay_minutes=0. if normal else (15. if cell == 0 else 22.5),
        faults={}, shock={}, shock_by_priority={}, exogenous_events=[],
        analysis_cohort="normal" if normal else "development", experiment_role="development",
        purpose="v11_seen_development", sampling_cohort="normal" if normal else "development",
        selection_allowed=True, fit_allowed=False, requires_policy_freeze=False, is_final_blind=False,
        source_metadata_path=record["source_metadata_path"], source_scenario_id=old["id"],
        root_source_provenance=record["provenance"], design_axis="normal" if normal else "main_mechanism",
        design_axis_value=cell, design_level=str(cell), design_cell_id=group, matched_group_id=group,
        distribution_id=VERSION + ".q_design", sampling_method="registered_seen_source_crosscoverage",
        sampling_role="seen_background_development", sampling_weight_is_failure_probability=False,
        same_background_new_perturbation=False, new_independent_background=False,
        p_reference=None, importance_weight=None,
        normal_reference_type="ordinary_no_event_cost_reference" if normal else None)
    if name in ("L1", "L3", "L4b"):
        scene.update(faults={device: availability}, initiating_device=device,
            initiating_domain=_domain(device), fault_start_minute=start, fault_duration_minutes=duration,
            fault_severity_layer={0.: "full_outage", .25: "severe", .5: "moderate"}[availability], contingency_class="N-1")
        scene["exogenous_events"].append(dict(origin="exogenous", kind="device_fault", device=device,
            availability=availability, start_minute=start, end_minute=start + duration))
    if name in ("L2", "L4a", "L4b"):
        scene.update(shock={k: v if mode in (k, "mixed") else 0. for k, v in amplitudes.items()},
            shock_by_priority={"P0": {"e": p0}} if p0 and mode in ("e", "mixed") else {},
            shock_start_minute=start, shock_duration_minutes=duration, shock_mode=mode, stratum=level)
        scene["exogenous_events"].append(dict(origin="exogenous", kind="load_shock", start_minute=start,
            end_minute=start + duration, shock=copy.deepcopy(scene["shock"]),
            shock_by_priority=copy.deepcopy(scene["shock_by_priority"])))
    scene["actual_p0_load_multiplier"] = {k: (1 + scene["shock"].get(k, 0.)) *
        (1 + scene["shock_by_priority"].get("P0", {}).get(k, 0.)) for k in ("e", "h", "c")}
    scene["physical_scenario_signature"] = _physical_signature(scene)
    scene["id"] = "v11_case_" + digest([scene["analysis_cohort"], name, scene["physical_scenario_signature"]])[:24]
    scene["exogenous_stream_id"] = "v11_exogenous:" + digest([record["root_family_id"], scene["exogenous_events"]])
    validate_scenario(scene)
    return scene


def _perturb(scene, index):
    new = copy.deepcopy(scene)
    phase = 3.25 * (1 + index % 2)
    for event in new["exogenous_events"]:
        event["start_minute"] += phase
        event["end_minute"] += phase
    for key in ("fault_start_minute", "shock_start_minute"):
        if key in new:
            new[key] += phase
    new.update(analysis_cohort="new_perturbation", sampling_cohort="new_perturbation",
        experiment_role="confirmation", selection_allowed=False, fit_allowed=False,
        requires_policy_freeze=True, same_background_new_perturbation=True,
        response_delay_minutes=27.5, source_development_scenario_id=scene["id"],
        design_axis="new_timing", design_axis_value=phase, purpose="v11_frozen_new_perturbation")
    new["physical_scenario_signature"] = _physical_signature(new)
    new["id"] = "v11_confirm_" + digest([new["analysis_class"], new["physical_scenario_signature"]])[:24]
    new["exogenous_stream_id"] = "v11_exogenous:" + digest([new["root_family_id"], new["exogenous_events"]])
    new["design_cell_id"] = new["matched_group_id"] = "v11_confirm_cell:" + digest([new["root_family_id"], phase])[:24]
    validate_scenario(new)
    return new


def _regressions(root):
    requested = ((FUEL, "v10_closeout_2bab9f6a1b7a042800afb9e4", "day051_fuel_recurrence"),
        (V10, "v10_closeout_d0acf561b69a34146022f81e", "day051_demand_drop_overproduction"),
        (V10, "v10_closeout_c1646f1920376a2ab5f9dbcc", "day187_compound"),
        (V10, "v10_closeout_0459ac360e5c57af0bdb0b49", "day298_compound"))
    result = []
    for path, sid, mechanism in requested:
        source = _signed(root / path)
        old = next(s for s in source["scenarios"] if s["id"] == sid)
        new = copy.deepcopy(old)
        new.update(id="v11_regression_" + digest([path.as_posix(), sid])[:24],
            source_metadata_path=path.as_posix(), source_metadata_sha256=_sha(root / path),
            source_scenario_id=sid, regression_mechanism=mechanism, study_name=STUDY_NAME,
            analysis_cohort="development_regression", sampling_cohort="development_regression",
            experiment_role="development", selection_allowed=True, fit_allowed=False,
            requires_policy_freeze=False, is_final_blind=False, purpose="unchanged_v10_physical_regression",
            sample_schema_version=VERSION, same_background_new_perturbation=False)
        if {k: new.get(k) for k in PHYSICAL_FIELDS} != {k: old.get(k) for k in PHYSICAL_FIELDS}:
            raise ValueError("Regression physical contract changed")
        result.append(new)
    return result


def build_source_proposal(workspace_root=ROOT, *, seed=SEED):
    """Build 760 definitions from signed metadata; never parse annual values."""
    if type(seed) is not int or seed < 0:
        raise ValueError("Seed must be a nonnegative integer")
    root = Path(workspace_root).resolve()
    sealed = _sealed(root)
    roots, templates = _inventory(root, sealed)
    scenes = []
    for index, record in enumerate(roots):
        old = templates[record["root_family_id"]]
        for cell in (0, 1):
            current = {name: _scene(old, record, index, cell, name) for name in CLASSES}
            scenes.extend(current.values())
            if cell == 1:
                confirm_classes = ("L1", "L2", "L3", "L4b")
                scenes.extend(_perturb(current[name], index) for i, name in enumerate(confirm_classes) if i != index % 4)
        scenes.append(_scene(old, record, index, 0, "N2"))
    scenes.extend(_regressions(root))
    for scene in scenes:
        _reject_sealed(scene, sealed)
    if len(scenes) != 760 or len({s["id"] for s in scenes}) != 760:
        raise ValueError("V11 registered denominator or identity uniqueness differs")
    source_paths = (AUTHORITY, V5, V6, V7, V10, FUEL,
                    Path("tools/autonomous_dispatch/data_design.py"),
                    Path("tools/autonomous_dispatch/selective_data.py"))
    proposal = dict(version=VERSION, study_name=STUDY_NAME, seed=seed,
        roots=roots, scenarios=scenes, sealed_final_days=sealed,
        final_12_backgrounds_unopened=True, final_evaluation_generated=False,
        strict_final_gate="not_verified", new_independent_background_count=0,
        source_outcomes_read_during_generation=False, source_load_values_read_during_generation=False,
        ids_by_cohort={cohort: [s["id"] for s in scenes if s["analysis_cohort"] == cohort] for cohort in COHORTS},
        analysis_cohort_counts=dict(Counter(s["analysis_cohort"] for s in scenes)),
        analysis_class_counts=dict(Counter(s["analysis_class"] for s in scenes)),
        design_coverage=dict(seen_background_windows=54, new_background_windows=0,
            roots_by_quarter=dict(Counter(str(r["season_quarter"]) for r in roots)),
            roots_by_provenance=dict(Counter(r["provenance"] for r in roots)),
            main_devices=dict(Counter(s["initiating_device"] for s in scenes
                if s["analysis_cohort"] == "development" and s["analysis_class"] == "L1")),
            interaction_claim="ordinary normal reference has no shadow delay; no pure four-cell interaction claim"),
        new_perturbation_execution_requires_policy_freeze=True,
        new_perturbation_semantics="same seen background; new timing; not cross-background validation",
        original_final_semantics="metadata exclusion only; no final scene or load content generated",
        probability_semantics=dict(p_reference=None, importance_weight=None, not_hospital_failure_probability=True),
        observation_contract=dict(history=[0, 60], primary=[60, 420], tail=[420, 600], full=[0, 600],
            endpoint_source_hour_reserved=True, embargo_minutes_each_side=60,
            initialization_history="six algebraic repeats of source start hour, not six earlier observations"),
        source_sha256={path.as_posix(): _sha(root / path) for path in source_paths})
    proposal["signature"] = digest(proposal)
    return proposal


def build_final_seal_audit(workspace_root=ROOT):
    """Audit registered support, disclose historical reads, authorize no release."""
    root = Path(workspace_root).resolve()
    sealed = _sealed(root)
    sources, errors = [], []
    for path in AUDIT_SOURCES:
        value = _signed(root / path)
        scenes = value.get("scenarios", value.get("fixed_scenarios", []))
        violations = []
        for scene in scenes:
            try:
                _reject_sealed(scene, sealed)
            except ValueError as error:
                violations.append(dict(scenario_id=scene["id"], error=str(error)))
        errors.extend(violations)
        sources.append(dict(path=path.as_posix(), sha256=_sha(root / path),
            signature=value["signature"], scenario_count=len(scenes), support_violations=violations))
    result = dict(version=VERSION + ".seal_audit", sealed_final_days=sealed,
        authority_path=AUTHORITY.as_posix(), authority_sha256=_sha(root / AUTHORITY), sources=sources,
        registered_support_isolation_verified=not errors, errors=errors,
        historical_sealed_contents_never_read=False,
        historical_numeric_input_validation_used_sealed_rows=True,
        historical_statistical_isolation_status="registered code paths reviewed; complete dynamic historical proof unavailable",
        historical_evidence=["pipeline.read_data parses annual physical values and computes allclose/max validation",
            "HospitalIESEnv constructs annual per-hour groups and thermal cache",
            "v7 source ranking reads eligible unsealed start_hours only",
            "registered trajectory support excludes sealed days; reviewed training normalizes registered train rows"],
        raw_input_values_read_during_audit=False, final_evaluation_generated=False,
        strict_final_gate="not_verified", strict_final_release_authorized=False,
        scope="Signed registered definitions and reviewed code; does not prove no external or unregistered historical reads")
    result["signature"] = digest(result)
    return result


def build_manifest(proposal, workspace_root=ROOT, *, proposal_path=None,
                   policies=("priority_milp", "secure_milp")):
    """Bind current production inventory only after the final source is ready."""
    root = Path(workspace_root).resolve()
    if proposal != build_source_proposal(root, seed=proposal["seed"]):
        raise ValueError("Proposal differs from signed source registration")
    old = _signed(root / V10_MANIFEST)
    splits = {}
    for scene in proposal["scenarios"]:
        support = scene["full_source_support"]
        splits[scene["id"]] = dict(group_id=scene["root_family_id"], split=scene["experiment_role"],
            experiment_role=scene["experiment_role"], fit_allowed=False,
            selection_allowed=scene["selection_allowed"], development_only=not scene["requires_policy_freeze"],
            requires_policy_freeze=scene["requires_policy_freeze"], is_final_blind=False,
            raw_start_minute=support["raw_start_minute"], raw_end_minute=support["raw_end_minute"],
            time_block_id=scene["time_block_id"], embargo_minutes_each_side=60,
            split_version=VERSION, purpose=scene["purpose"], support_independence_claim=False)
    manifest = _build_manifest(root, proposal["scenarios"], policies, old["execution_contract"],
                              old["schemas"], old["options"], splits)
    manifest.pop("signature")
    manifest.update(manifest_version=VERSION + ".manifest", study_name=STUDY_NAME,
        source_proposal_signature=proposal["signature"],
        source_proposal_file_sha256=_sha(proposal_path) if proposal_path else None,
        source_sha256=proposal["source_sha256"], ids_by_cohort=proposal["ids_by_cohort"],
        analysis_cohort_counts=proposal["analysis_cohort_counts"], design_coverage=proposal["design_coverage"],
        sealed_final_days=proposal["sealed_final_days"], policy_search_final_12_backgrounds_unopened=True,
        strict_final_gate="not_verified", final_evaluation_generated=False,
        new_perturbation_execution_requires_policy_freeze=True, new_independent_background_count=0,
        data_loading_contract="selective physical parse; unauthorized rows NaN; final release unavailable")
    manifest["signature"] = digest(manifest)
    return manifest


def _write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def freeze_proposal(workspace_root, run_dir, *, seed=SEED):
    """Write source metadata and audit only; no execution manifest or episode."""
    root, run = Path(workspace_root).resolve(), Path(run_dir).resolve()
    paths = dict(proposal=run / "design/SOURCE_PROPOSAL.json",
                 seal_audit=run / "design/FINAL_SEAL_AUDIT.json",
                 receipt=run / "design/DATA_PROPOSAL_RECEIPT.json")
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("V11 source registration already exists or retained partial files")
    proposal = build_source_proposal(root, seed=seed)
    _write_new(paths["proposal"], proposal)
    _write_new(paths["seal_audit"], build_final_seal_audit(root))
    receipt = dict(version=VERSION, files={key: dict(path=str(path), sha256=_sha(path))
        for key, path in paths.items() if key != "receipt"}, signature=proposal["signature"],
        roots=54, scenarios=760, analysis_cohort_counts=proposal["analysis_cohort_counts"],
        episodes_started=0, final_evaluation_generated=False, strict_final_gate="not_verified")
    _write_new(paths["receipt"], receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("freeze-proposal",))
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze_proposal(args.workspace, args.run_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
