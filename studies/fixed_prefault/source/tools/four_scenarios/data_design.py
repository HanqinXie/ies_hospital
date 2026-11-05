"""Outcome-free, write-once source design for four hospital IES incident classes.

Old exposure registries and v5/v6 day reservations are read only.  The sealed
final twelve are excluded by metadata; no final trajectory or outcome is read.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
from itertools import product
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from hospital_ies.data_contract import SCHEMA_VERSIONS
from hospital_ies.dataset_manifest import build_manifest, digest
from hospital_ies.resilience import validate_scenario
from tools.control_improvement.data_design import validate_observation_support


VERSION = "four_scenarios.v7.source_design.v1"
SEED = 20261016
HORIZON_MINUTES = 600
ENDPOINT_MINUTES = 60
EMBARGO_MINUTES = 60
DAY_MINUTES = 1440
ANALYSIS_CLASSES = ("N2", "N4", "L1", "L2", "L3", "L4a", "L4b")
STAGES = {"N2": 2, "N4": 4, "L1": 1, "L2": 2, "L3": 3, "L4a": 4, "L4b": 4}
EVENT_CLASSES = {"N2": "L2", "N4": "L4", "L1": "L1", "L2": "L2",
                 "L3": "L3", "L4a": "L4a", "L4b": "L4b"}
DEVICE_CYCLE = (
    ("GRID", "HRSG_1", "EC_1", "DIESEL"),
    ("GT_1", "BOILER_1", "ABS", "DIESEL"),
    ("GT_2", "HRSG_2", "TOWER_1", "DIESEL"),
)
DEVICE_DOMAINS = ("electric", "steam", "cooling", "backup")
# Four mode x three load-level fractional design. Device domains are permuted
# within each level; fault severity is a different cyclic assignment.
DEVICE_DOMAIN_PERMUTATIONS = ((0, 1, 2, 3), (3, 0, 1, 2), (1, 2, 0, 3))
SEVERITY_CYCLE = (("full_outage", 0.), ("severe", .25), ("moderate", .5))
SHOCK_MODES = ("e", "h", "c", "mixed")
SHOCK_LEVELS = (
    ("ordinary", {"e": .3, "h": .15, "c": .2}, 0.),
    ("elevated", {"e": .75, "h": .35, "c": .5}, .1),
    ("boundary_candidate", {"e": 1.4, "h": .6, "c": .9}, .35),
)
V5_PROPOSAL = Path("outputs/v5_policy_search_20261015_run01/design/source_proposal.json")
V6_PROPOSAL = Path("outputs/v6_policy_redesign_20261016_run01/design/v6_source_proposal.json")
LOAD_FILE = Path("inputs/loads/hospital_ies_demand_8760.csv")
CONTRACT_FILE = Path("configs/p1b_execution_research.json")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _signed(value: dict) -> bool:
    return value.get("signature") == digest({k: v for k, v in value.items()
                                               if k != "signature"})


def prior_exposures(workspace_root: Path | str = ROOT) -> dict:
    """Read only old interval registries and sealed metadata, never outcomes."""
    root = Path(workspace_root).resolve()
    v5_path, v6_path = root / V5_PROPOSAL, root / V6_PROPOSAL
    v5, v6 = _read(v5_path), _read(v6_path)
    if not _signed(v5) or not _signed(v6):
        raise ValueError("Frozen v5/v6 source proposal signature changed")
    sealed = v5.get("sealed_final_days")
    if (len(sealed or []) != 12 or len(set(sealed)) != 12 or
            sorted(sealed) != sorted(v6.get("sealed_final_days", []))):
        raise ValueError("Original final twelve exclusion metadata differs")
    if (len(v5.get("blocks", [])) != 54 or len(v6.get("blocks", [])) != 12 or
            len({item["day_index"] for item in v5["blocks"] + v6["blocks"]}) != 66):
        raise ValueError("V5/V6 whole-day source reservations differ")
    intervals = []
    sealed_intervals = [{"raw_start_minute": day * DAY_MINUTES,
                         "raw_end_minute": (day + 1) * DAY_MINUTES}
                        for day in sealed]
    for row in v5["excluded_support"] + v5["blocks"] + v6["blocks"] + sealed_intervals:
        a, b = float(row["raw_start_minute"]), float(row["raw_end_minute"])
        if not 0 <= a < b <= 365 * DAY_MINUTES:
            raise ValueError("Historical exposure interval outside annual source")
        intervals.append({"raw_start_minute": a, "raw_end_minute": b})
    return {"intervals": intervals, "sealed_final_day_count": 12,
            "v5_reserved_day_count": 54, "v6_reserved_day_count": 12,
            "source_sha256": {V5_PROPOSAL.as_posix(): _sha(v5_path),
                              V6_PROPOSAL.as_posix(): _sha(v6_path)}}


def eligible_windows(exposures: list[dict], *, candidate_days=range(365),
                     start_hours=range(5, 11)) -> list[dict]:
    """Find 600+60 source minutes with a separate 60-minute embargo on each side."""
    occupied = [(float(row["raw_start_minute"]), float(row["raw_end_minute"]))
                for row in exposures]
    if any(not 0 <= a < b <= 365 * DAY_MINUTES for a, b in occupied):
        raise ValueError("Invalid occupied annual source interval")
    days = list(candidate_days)
    if len(days) != len(set(days)) or any(type(day) is not int or not 0 <= day < 365
                                         for day in days):
        raise ValueError("Candidate days must be distinct original calendar indices")
    hours = list(start_hours)
    if not hours or len(hours) != len(set(hours)) or any(type(h) is not int or not 0 <= h < 24
                                                        for h in hours):
        raise ValueError("Invalid within-day starting hours")
    result = []
    for day in sorted(days):
        for h in sorted(hours):
            raw_start = day * DAY_MINUTES + h * 60
            raw_end = raw_start + HORIZON_MINUTES + ENDPOINT_MINUTES
            low, high = raw_start - EMBARGO_MINUTES, raw_end + EMBARGO_MINUTES
            if low < 0 or high > 365 * DAY_MINUTES:
                continue
            if any(low < end and start < high for start, end in occupied):
                continue
            result.append({"day_index": day, "hour_in_day": h,
                           "start_hour": day * 24 + h + 1,
                           "raw_start_minute": raw_start, "raw_end_minute": raw_end,
                           "embargo_start_minute": low, "embargo_end_minute": high})
    return result


def _season(day: int) -> int:
    return min(4, 1 + day // 91)


def _roots(windows: list[dict], loads: pd.DataFrame, seed: int) -> tuple[list[dict], dict]:
    by_day = {}
    for window in windows:
        day = window["day_index"]
        if day not in by_day or (abs(window["hour_in_day"] - 8), window["hour_in_day"]) < (
                abs(by_day[day]["hour_in_day"] - 8), by_day[day]["hour_in_day"]):
            by_day[day] = window
    annual = loads.set_index("hour_of_year")
    selected = []
    denominator = {}
    for quarter in range(1, 5):
        choices = []
        for day, window in by_day.items():
            if _season(day) != quarter:
                continue
            row = annual.loc[window["start_hour"]]
            e = float(row["P_e_nonplant_kW"] + row["P_e_unresolved_kW"])
            h = float(row["Q_h_demand_kW"])
            c = float(row["Q_c_demand_kW"])
            score = e + (h + c) / 3
            choices.append((score, day, window, {"electric_kW": e, "heat_kW": h,
                                                "cold_kW": c, "design_load_index_kW": score,
                                                "outdoor_temperature_C": float(row["outdoor_temperature_C"])}))
        choices.sort(key=lambda item: (item[0], item[1]))
        denominator[str(quarter)] = len(choices)
        if len(choices) < 3:
            raise ValueError("Fewer than three unused daytime backgrounds in quarter " + str(quarter))
        pick = (0, (len(choices) - 1) // 2, len(choices) - 1)
        if len(set(pick)) != 3:
            raise ValueError("Distinct seasonal low/medium/high day roots unavailable")
        for rank, index in enumerate(pick):
            _, day, window, source = choices[index]
            selected.append({**window, "season_quarter": quarter,
                             "source_load_rank": ("low", "middle", "high")[rank],
                             "source_load": source,
                             "root_family_id": "four_root:" + digest([VERSION, seed, day,
                                                                      window["start_hour"]])})
    return selected, denominator


def _shock_for(root_index: int) -> tuple[str, str, dict, dict]:
    mode = SHOCK_MODES[root_index % 4]
    level, amplitudes, p0_e = SHOCK_LEVELS[root_index // 4]
    shock = {carrier: (value if mode in (carrier, "mixed") else 0.)
             for carrier, value in amplitudes.items()}
    priority = {"P0": {"e": p0_e}} if p0_e and mode in ("e", "mixed") else {}
    return mode, level, shock, priority


def _scene(root: dict, root_index: int, name: str, seed: int) -> dict:
    stage = STAGES[name]
    mode, level, shock, priority = _shock_for(root_index)
    level_index, mode_index = divmod(root_index, 4)
    domain_index = DEVICE_DOMAIN_PERMUTATIONS[level_index][mode_index]
    device = DEVICE_CYCLE[level_index][domain_index]
    domain = DEVICE_DOMAINS[domain_index]
    severity, availability = SEVERITY_CYCLE[(level_index + mode_index) % 3]
    # Absolute event clocks leave a genuine pre-event interval and a recovery tail.
    shock_start = float((90, 120, 150)[root_index % 3])
    fault_start = shock_start
    shock_duration = float((180, 240)[root_index % 2])
    fault_duration = float((240, 180)[root_index % 2])
    events = []
    scene = {
        "id": "four_case_" + digest([VERSION, seed, root["day_index"], name])[:20],
        "analysis_class": name, "reference_role": "normal_no_disturbance" if name in ("N2", "N4") else None,
        "stage": stage, "event_class": EVENT_CLASSES[name],
        "event_schema_version": "p2a.events.v1", "sample_schema_version": VERSION,
        "start_hour": root["start_hour"], "duration_steps": 40, "step_minutes": 15,
        "history_interval_minutes": [0, 60], "score_interval_minutes": [60, 420],
        "tail_interval_minutes": [420, 600], "label_horizons_minutes": [15, 30, 60, 120],
        "pre_event_warmup_steps": 6, "initialization_seed": seed,
        "automatic_fast_shedding": False, "enable_propagation": stage >= 3,
        "response_delay_minutes": 0. if name in ("N2", "N4") else 15.,
        "grid_limit_kW": 850., "pre_event_grid_limit_kW": 850.,
        "faults": {}, "shock": {}, "shock_by_priority": {},
        "exogenous_events": events,
        "root_family_id": root["root_family_id"], "family_id": root["root_family_id"],
        "parent_instance_id": root["root_family_id"],
        "parent_template_id": root["root_family_id"],
        "design_family": root["root_family_id"],
        "base_state_id": "four_initial:" + digest([seed, root["start_hour"], 850., 6]),
        "exogenous_stream_id": "four_stream:" + digest([seed, root["day_index"], name]),
        "time_block_id": "four_window_day_" + str(root["day_index"]),
        "experiment_role": "train", "purpose": "four_scenarios_seen_mechanism_study",
        "is_final_blind": False, "sampling_role": "matched_four_class_stress_design",
        "sampling_cohort": "main", "distribution_id": VERSION + ".q_design",
        "sampling_method": "source_load_ranked_seasonal_subday_support",
        "sampling_weight_is_failure_probability": False,
        "p_reference": None, "q_design_density": None, "importance_weight": None,
        "demand_background": root["source_load_rank"],
        "season_quarter": root["season_quarter"], "source_day_index": root["day_index"],
    }
    if name in ("L1", "L3", "L4b"):
        fstart = (shock_start + (-30, 0, 30)[root_index % 3]
                  if name == "L4b" else fault_start)
        scene.update(faults={device: availability}, initiating_device=device,
                     initiating_domain=domain,
                     fault_severity_layer=severity,
                     contingency_class="N-1", fault_start_minute=fstart,
                     fault_duration_minutes=fault_duration)
        events.append({"origin": "exogenous", "kind": "device_fault",
                       "device": device, "availability": availability,
                       "start_minute": fstart, "end_minute": fstart + fault_duration})
    if name in ("L2", "L4a", "L4b"):
        scene.update(shock=copy.deepcopy(shock), shock_by_priority=copy.deepcopy(priority),
                     shock_mode=mode, stratum=level,
                     shock_start_minute=shock_start,
                     shock_duration_minutes=shock_duration)
        events.append({"origin": "exogenous", "kind": "load_shock",
                       "start_minute": shock_start,
                       "end_minute": shock_start + shock_duration,
                       "shock": copy.deepcopy(shock),
                       "shock_by_priority": copy.deepcopy(priority)})
    scene["actual_p0_load_multiplier"] = {
        carrier: (1 + scene["shock"].get(carrier, 0.)) *
                 (1 + scene["shock_by_priority"].get("P0", {}).get(carrier, 0.))
        for carrier in ("e", "h", "c")}
    scene["full_source_support"] = validate_observation_support(scene)
    if (scene["full_source_support"]["raw_start_minute"] != root["raw_start_minute"] or
            scene["full_source_support"]["raw_end_minute"] != root["raw_end_minute"]):
        raise ValueError("Constructed scene source support differs from reserved root")
    validate_scenario(scene)
    return scene


def build_proposal(workspace_root: Path | str = ROOT, *, seed: int = SEED) -> dict:
    """Register seasonal roots and incidents from source data, with no simulation."""
    if type(seed) is not int or seed < 0:
        raise ValueError("Design seed must be a nonnegative integer")
    root = Path(workspace_root).resolve()
    previous = prior_exposures(root)
    windows = eligible_windows(previous["intervals"])
    loads = pd.read_csv(root / LOAD_FILE,
                        usecols=["hour_of_year", "P_e_nonplant_kW", "P_e_unresolved_kW",
                                 "Q_h_demand_kW", "Q_c_demand_kW", "outdoor_temperature_C"])
    if len(loads) != 8760 or loads["hour_of_year"].tolist() != list(range(1, 8761)):
        raise ValueError("Original 8760-hour source sequence changed")
    roots, denominator = _roots(windows, loads, seed)
    scenes = [_scene(item, i, name, seed) for i, item in enumerate(roots)
              for name in ANALYSIS_CLASSES]
    if len(roots) != 12 or len(scenes) != 84 or Counter(s["analysis_class"] for s in scenes) != {
            name: 12 for name in ANALYSIS_CLASSES}:
        raise ValueError("Matched four-class design denominator differs")
    combined = [scene for scene in scenes if scene["analysis_class"] == "L4b"]
    factor_table = {
        "shock_mode_by_level": {
            f"{mode}|{level}": sum(scene["shock_mode"] == mode and scene["stratum"] == level
                                   for scene in combined)
            for mode, level in product(SHOCK_MODES, (row[0] for row in SHOCK_LEVELS))},
        "shock_level_by_fault_severity": {
            f"{level}|{severity}": sum(scene["stratum"] == level and
                                       scene["fault_severity_layer"] == severity
                                       for scene in combined)
            for level, severity in product((row[0] for row in SHOCK_LEVELS),
                                           (row[0] for row in SEVERITY_CYCLE))},
        "fault_domain_by_severity": {
            f"{domain}|{severity}": sum(scene["initiating_domain"] == domain and
                                        scene["fault_severity_layer"] == severity
                                        for scene in combined)
            for domain, severity in product(DEVICE_DOMAINS,
                                            (row[0] for row in SEVERITY_CYCLE))},
    }
    proposal = {
        "version": VERSION + ".proposal", "seed": seed,
        "roots": roots, "scenarios": scenes,
        "available_daytime_roots_by_quarter": denominator,
        "eligible_daytime_window_count": len(windows),
        "blocked_interval_count": len(previous["intervals"]),
        "sealed_final_day_count": previous["sealed_final_day_count"],
        "final_12_backgrounds_unopened": True,
        "normal_reference_not_an_incident_class": True,
        "factor_cross_tabulation": factor_table,
        "probability_semantics": {"q_design": "Twelve matched seasonal source backgrounds",
                                  "p_reference": None, "importance_weight": None,
                                  "not_hospital_failure_probability": True},
        "observation_contract": {"history": [0, 60], "primary": [60, 420],
                                 "full": [0, 600], "endpoint_source_hour_reserved": True,
                                 "embargo_minutes_each_side": EMBARGO_MINUTES},
        "source_sha256": {**previous["source_sha256"],
                          LOAD_FILE.as_posix(): _sha(root / LOAD_FILE),
                          CONTRACT_FILE.as_posix(): _sha(root / CONTRACT_FILE),
                          "tools/four_scenarios/data_design.py": _sha(Path(__file__))},
    }
    proposal["signature"] = digest(proposal)
    return proposal


def verify_proposal(proposal: dict, workspace_root: Path | str = ROOT) -> bool:
    """Verify exact outcome-free regeneration, not only self-consistent hashes."""
    if not isinstance(proposal, dict) or not _signed(proposal):
        raise ValueError("Four-scenario proposal signature differs")
    if proposal != build_proposal(workspace_root, seed=proposal["seed"]):
        raise ValueError("Four-scenario proposal differs from current source registration")
    return True


def build_four_manifest(proposal: dict, workspace_root: Path | str = ROOT,
                        *, proposal_path: Path | None = None) -> dict:
    root = Path(workspace_root).resolve()
    verify_proposal(proposal, root)
    scenes = proposal["scenarios"]
    splits = {}
    for scene in scenes:
        support = scene["full_source_support"]
        splits[scene["id"]] = {
            "group_id": scene["root_family_id"], "split": "train",
            "experiment_role": "train", "fit_allowed": True,
            "selection_allowed": False, "development_only": False,
            "split_version": VERSION, "embargo_minutes_each_side": EMBARGO_MINUTES,
            "raw_start_minute": support["raw_start_minute"],
            "raw_end_minute": support["raw_end_minute"],
            "time_block_id": scene["time_block_id"],
            "purpose": scene["purpose"], "is_final_blind": False,
        }
    options = {"seed": proposal["seed"], "security_weight": .35,
               "label_horizons": [15, 30, 60, 120], "history_minutes": 60,
               "score_start_minute": 60, "score_end_minute": 420,
               "observed_end_minute": 600}
    contract = _read(root / CONTRACT_FILE)
    manifest = build_manifest(root, scenes, ("priority_milp", "secure_milp"),
                              contract, SCHEMA_VERSIONS, options, splits)
    manifest.pop("signature")
    manifest.update(manifest_version=VERSION + ".manifest",
                    four_scenario_source_signature=proposal["signature"],
                    four_scenario_source_sha256=proposal["source_sha256"],
                    four_scenario_proposal_file_sha256=(_sha(proposal_path)
                                                        if proposal_path else None),
                    four_scenario_roots=12, four_scenario_specifications=84,
                    four_scenario_analysis_class_counts=dict(Counter(
                        scene["analysis_class"] for scene in scenes)),
                    policy_search_final_12_backgrounds_unopened=True)
    manifest["signature"] = digest(manifest)
    return manifest


def freeze(workspace_root: Path | str, run_dir: Path | str, *, seed: int = SEED) -> dict:
    """Write proposal and runner-compatible manifest once; run no episode."""
    root, run = Path(workspace_root).resolve(), Path(run_dir).resolve()
    proposal_path = run / "design/SOURCE_PROPOSAL.json"
    manifest_path = run / "new_data/global_manifest.json"
    if proposal_path.exists() or manifest_path.exists():
        raise FileExistsError("Four-scenario source is already frozen")
    proposal = build_proposal(root, seed=seed)
    manifest = build_four_manifest(proposal, root)
    _write_new(proposal_path, proposal)
    manifest.pop("signature")
    manifest["four_scenario_proposal_file_sha256"] = _sha(proposal_path)
    manifest["signature"] = digest(manifest)
    _write_new(manifest_path, manifest)
    return {"proposal_path": str(proposal_path), "proposal_sha256": _sha(proposal_path),
            "manifest_path": str(manifest_path), "manifest_sha256": _sha(manifest_path),
            "roots": 12, "scenarios": 84, "episodes_started": 0,
            "final_12_backgrounds_unopened": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("freeze",))
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    print(json.dumps(freeze(args.workspace, args.run_dir, seed=args.seed), ensure_ascii=False))


if __name__ == "__main__":
    main()
