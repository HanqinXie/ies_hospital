"""Outcome-free v5 real-load support, root splits, and write-once manifest.

This module registers source days and existing pressure mechanisms only. It
does not run the simulator, inspect outcome labels, or open final trajectories.
The historical v4 producer and its outputs remain untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path

from tools.control_improvement.data_design import (
    CONDITIONS, DOMAIN_SOURCE, PRESSURES, _rng, _spec, exposure_registry,
)
from hospital_ies.data_contract import SCHEMA_VERSIONS
from hospital_ies.dataset_manifest import build_manifest, digest
from hospital_ies.resilience import validate_scenario


ROOT = Path(__file__).resolve().parents[2]
PROPOSAL_VERSION = "policy_search.v5.source_proposal.v1"
MANIFEST_VERSION = "policy_search.v5.new270.v1"
SPEC_VERSION = "policy_search.v5.fixed_mechanisms.v1"
DISTRIBUTION = "q_design.policy_search.v5.fixed_mechanisms.v1"
ROLE_COUNTS = {"train": 5, "selection": 1, "calibration": 1, "development": 2}
ROOT_COUNT = len(CONDITIONS) * sum(ROLE_COUNTS.values())
PRESSURE_COUNT = len(PRESSURES)
SPEC_COUNT = ROOT_COUNT * PRESSURE_COUNT
EMBARGO_MINUTES = 60
BASELINE_POLICIES = ("priority_milp", "secure_milp")
OBSERVATION_CONTRACT = {
    "history_interval_minutes": [0, 60],
    "primary_score_interval_minutes": [60, 420],
    "tail_interval_minutes": [420, 600],
    "full_horizon_minutes": [0, 600],
    "pre_event_warmup_steps": 6,
    "embargo_minutes_each_side": EMBARGO_MINUTES,
    "endpoint_source_hour_reserved": True,
}
ROLE_SEMANTICS = {
    "train": "Policy search, outcome learning, and on-policy aggregation",
    "selection": "Model/strategy/seed selection only; no fit or gate calibration",
    "calibration": "Gate and support calibration only; no model selection",
    "development": "Held-out matched feedback-policy comparison only",
}
SOURCE_PATHS = (
    "tools/policy_search/data_design.py",
    "tools/control_improvement/data_design.py",
    "tools/p2b_experiment/design.py",
    "src/hospital_ies/dataset_manifest.py",
    "src/hospital_ies/resilience.py",
    "scenario_sampler.py",
)


def _sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_new(path: str | Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _source_binding(root: Path) -> dict[str, str]:
    root = Path(root).resolve()
    return {name: _sha(root / name) for name in SOURCE_PATHS}


def _overlaps(left: dict, right: dict) -> bool:
    return (float(left["raw_start_minute"]) < float(right["raw_end_minute"])
            and float(right["raw_start_minute"]) < float(left["raw_end_minute"]))


def _filtered_days(candidate_days, sealed_final_days, excluded_support):
    days = list(candidate_days)
    if (len(days) != len(set(days)) or any(type(day) is not int or not 0 <= day < 365
                                           for day in days)):
        raise ValueError("Candidate calendar days must be unique integers in 0..364")
    sealed = list(sealed_final_days)
    if (len(sealed) != len(set(sealed)) or any(type(day) is not int or not 0 <= day < 365
                                               for day in sealed)):
        raise ValueError("Sealed final days must be unique valid calendar days")
    frozen_exposure = []
    for item in excluded_support:
        if not isinstance(item, dict):
            raise ValueError("Exposure interval must be a record")
        a = float(item["raw_start_minute"])
        b = float(item["raw_end_minute"])
        if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b <= 365 * 1440):
            raise ValueError("Exposure interval must lie in the original 8760 hours")
        frozen_exposure.append(item)
    clean = []
    for day in sorted(days):
        if day in sealed:
            continue
        block = {"raw_start_minute": day * 1440,
                 "raw_end_minute": (day + 1) * 1440}
        if not any(_overlaps(block, interval) for interval in frozen_exposure):
            clean.append(day)
    return clean


def _planned_blocks(available_days: list[int], seed: int) -> tuple[list[dict], list[int]]:
    if len(available_days) < ROOT_COUNT:
        raise ValueError(f"Only {len(available_days)} unexposed days for {ROOT_COUNT} v5 roots")
    ordered_days = [int(day) for day in _rng(seed, "v5_calendar_support_only")
                    .permutation(available_days)]
    blocks = []
    cursor = 0
    for condition_id, config in CONDITIONS.items():
        for role_index, (role, count) in enumerate(ROLE_COUNTS.items()):
            for within_role in range(count):
                day = ordered_days[cursor]
                cursor += 1
                random_timing = _rng(seed, "v5_absolute_timing_independent_of_outcomes", day)
                device = (config["devices"][(within_role + role_index) % len(config["devices"])]
                          if config["devices"] else None)
                blocks.append({
                    "time_block_id": f"policy_search_day_{day:03d}",
                    "day_index": day,
                    "condition_id": condition_id,
                    "role": role,
                    "device": device,
                    "raw_start_minute": day * 1440,
                    "raw_end_minute": (day + 1) * 1440,
                    "start_hour": day * 24 + int(_rng(seed, "v5_within_day_hour", day)
                                                .integers(5, 11)) + 1,
                    "event_timing": {
                        "shock_start_minute": float(random_timing.integers(90, 151)),
                        "shock_duration_minutes": float(random_timing.choice([180, 240])),
                        "fault_offset_minutes": float(random_timing.choice([-30, 0, 30])),
                        "fault_duration_minutes": float(random_timing.choice([180, 240])),
                    },
                    "reserved_only": False,
                    "selection": "Calendar-only seeded permutation and absolute clocks before outcomes",
                })
    return blocks, sorted(set(available_days) - {b["day_index"] for b in blocks})


def _scenario(block: dict, seed: int, pressure: int) -> dict:
    # The old constructor is reused only to preserve its registered physical
    # scenario construction. V5 changes provenance/role metadata, not physics.
    scene = _spec(block, seed, pressure)
    scene.update(sample_schema_version=SPEC_VERSION, distribution_id=DISTRIBUTION,
                 sampling_method="preregistered_v5_fixed_mechanism_pressure_crosscoverage",
                 sampling_cohort="v5_policy_search", experiment_role=block["role"],
                 purpose="policy_search_development", is_final_blind=False)
    validate_scenario(scene)
    return scene


def build_proposal_from_days(candidate_days, *, seed: int, sealed_final_days,
                             excluded_support, workspace_root: str | Path = ROOT,
                             exposure_registry_metadata: dict | None = None) -> dict:
    """Pure deterministic design over registered day/exposure inputs.

    `candidate_days` is a proposed annual support list, never outcomes. All
    already exposed and sealed days are removed before the seeded permutation.
    """
    if type(seed) is not int or seed < 0:
        raise ValueError("Design seed must be a nonnegative integer")
    root = Path(workspace_root).resolve()
    source_days = sorted(list(candidate_days))
    sealed = sorted(list(sealed_final_days))
    exposure = copy.deepcopy(list(excluded_support))
    available = _filtered_days(source_days, sealed, exposure)
    blocks, reserve = _planned_blocks(available, seed)
    scenes = [_scenario(block, seed, pressure)
              for block in blocks for pressure in range(PRESSURE_COUNT)]
    proposal = {
        "version": PROPOSAL_VERSION,
        "sampling_seed": seed,
        "distribution_id": DISTRIBUTION,
        "candidate_days": source_days,
        "available_days": available,
        "sealed_final_days": sealed,
        "excluded_support": exposure,
        "exposure_registry_metadata": copy.deepcopy(exposure_registry_metadata or {}),
        "blocks": blocks,
        "fixed_scenarios": scenes,
        "reserved_unselected_days": reserve,
        "conditions": copy.deepcopy(CONDITIONS),
        "joint_pressure_points": [list(point) for point in PRESSURES],
        "approved_domain_source": dict(**DOMAIN_SOURCE,
                                       source_sha256=_sha(root / DOMAIN_SOURCE["path"])),
        "role_root_counts": {role: count * len(CONDITIONS)
                             for role, count in ROLE_COUNTS.items()},
        "role_semantics": copy.deepcopy(ROLE_SEMANTICS),
        "observation_contract": copy.deepcopy(OBSERVATION_CONTRACT),
        "source_sha256": _source_binding(root),
        "selection_uses_outcomes": False,
        "final_12_backgrounds_unopened": True,
        "probability_semantics": {
            "q_design": "Named fixed pressure/mechanism research coverage",
            "p_reference": None,
            "importance_weight": None,
            "not_hospital_failure_probability": True,
        },
    }
    proposal["signature"] = digest(proposal)
    validate_proposal(proposal, workspace_root=root)
    return proposal


def build_proposal(workspace_root: str | Path = ROOT, *, seed: int = 20261016) -> dict:
    """Read the actual attempted-source registry; create no hospital outcome."""
    root = Path(workspace_root).resolve()
    excluded, metadata = exposure_registry(root)
    if len(metadata["sealed_final_days"]) != 12:
        raise ValueError("Original twelve sealed final days are not registered")
    return build_proposal_from_days(
        range(365), seed=seed, sealed_final_days=metadata["sealed_final_days"],
        excluded_support=excluded, workspace_root=root,
        exposure_registry_metadata=metadata,
    )


def validate_proposal(proposal: dict, *, workspace_root: str | Path = ROOT) -> bool:
    root = Path(workspace_root).resolve()
    if not isinstance(proposal, dict):
        raise ValueError("V5 proposal must be a record")
    body = copy.deepcopy(proposal)
    signature = body.pop("signature", None)
    if signature != digest(body) or proposal.get("version") != PROPOSAL_VERSION:
        raise ValueError("V5 proposal signature/version changed")
    if proposal.get("source_sha256") != _source_binding(root):
        raise ValueError("V5 proposal source dependency changed")
    if (proposal.get("selection_uses_outcomes") is not False
            or proposal.get("final_12_backgrounds_unopened") is not True):
        raise ValueError("Outcome-free selection/final seal changed")
    if (proposal.get("observation_contract") != OBSERVATION_CONTRACT
            or proposal.get("role_semantics") != ROLE_SEMANTICS
            or proposal.get("distribution_id") != DISTRIBUTION):
        raise ValueError("V5 source support/role contract changed")
    if (json.loads(json.dumps(proposal.get("conditions"))) != json.loads(json.dumps(CONDITIONS))
            or proposal.get("joint_pressure_points") != [list(point) for point in PRESSURES]):
        raise ValueError("Original approved condition/pressure coverage changed")
    if proposal.get("approved_domain_source", {}).get("source_sha256") != _sha(root / DOMAIN_SOURCE["path"]):
        raise ValueError("Approved pressure source changed")
    seed = proposal["sampling_seed"]
    available = _filtered_days(proposal["candidate_days"], proposal["sealed_final_days"],
                               proposal["excluded_support"])
    if proposal.get("available_days") != available:
        raise ValueError("Available source support differs from frozen exposure and final seal")
    blocks, reserve = _planned_blocks(available, seed)
    if proposal.get("blocks") != blocks or proposal.get("reserved_unselected_days") != reserve:
        raise ValueError("V5 block roles or calendar selection changed")
    if proposal.get("role_root_counts") != {role: count * len(CONDITIONS)
                                            for role, count in ROLE_COUNTS.items()}:
        raise ValueError("V5 role counts changed")
    scenes = proposal.get("fixed_scenarios")
    if not isinstance(scenes, list) or len(scenes) != SPEC_COUNT:
        raise ValueError("V5 specification count changed")
    expected = [_scenario(block, seed, pressure)
                for block in blocks for pressure in range(PRESSURE_COUNT)]
    if scenes != expected or len({s["id"] for s in scenes}) != SPEC_COUNT:
        raise ValueError("V5 scenarios differ from registered root/pressure design")
    for block, rows in zip(blocks, (scenes[i:i + PRESSURE_COUNT]
                                    for i in range(0, SPEC_COUNT, PRESSURE_COUNT))):
        for scene in rows:
            support = scene["full_source_support"]
            if (support["raw_start_minute"] - EMBARGO_MINUTES < block["raw_start_minute"]
                    or support["raw_end_minute"] + EMBARGO_MINUTES > block["raw_end_minute"]):
                raise ValueError("Full original support/preheat/tail/embargo leaves its day root")
    return True


def _proposal_reference(root: Path, proposal: dict, proposal_path: str | Path | None) -> dict:
    if proposal_path is None:
        return {"embedded": copy.deepcopy(proposal), "sha256": digest(proposal),
                "signature": proposal["signature"]}
    path = Path(proposal_path).resolve()
    if not path.is_file() or digest(json.loads(path.read_text(encoding="utf-8"))) != digest(proposal):
        raise ValueError("Proposal file bytes/content must match registered proposal")
    try:
        name = path.relative_to(root).as_posix()
    except ValueError:
        name = str(path)
    return {"path": name, "sha256": _sha(path),
            "signature": proposal["signature"]}


def build_manifest_from_proposal(workspace_root: str | Path, proposal: dict, *,
                                 proposal_path: str | Path | None = None) -> dict:
    """Bind immutable v5 roles and physical/source inventory, without a run."""
    root = Path(workspace_root).resolve()
    validate_proposal(proposal, workspace_root=root)
    registry = proposal.get("exposure_registry_metadata", {})
    if (proposal.get("candidate_days") != list(range(365))
            or len(proposal.get("sealed_final_days", [])) != 12
            or registry.get("sealed_final_days") != proposal.get("sealed_final_days")
            or registry.get("no_outcome_or_success_filter") is not True):
        raise ValueError("V5 manifest needs the full workspace exposure registry and sealed final 12")
    scenarios = sorted(proposal["fixed_scenarios"], key=lambda scene: scene["id"])
    splits = {}
    for scene in scenarios:
        role = scene["experiment_role"]
        support = scene["full_source_support"]
        splits[scene["id"]] = {
            "group_id": "policy_search_background:" + digest(
                [proposal["sampling_seed"], scene["time_block_id"]]),
            "split": role,
            "experiment_role": role,
            "fit_allowed": role == "train",
            "selection_allowed": role == "selection",
            "calibration_allowed": role == "calibration",
            "development_only": role == "development",
            "split_version": "policy_search.support_roots.v1",
            "embargo_minutes_each_side": EMBARGO_MINUTES,
            "raw_start_minute": support["raw_start_minute"],
            "raw_end_minute": support["raw_end_minute"],
            "time_block_id": scene["time_block_id"],
            "purpose": "policy_search_development",
            "is_final_blind": False,
        }
    options = {
        "seed": proposal["sampling_seed"],
        "security_weight": .35,
        "label_horizons": [15, 30, 60, 120],
        "history_minutes": 60,
        "score_start_minute": 60,
        "score_end_minute": 420,
        "observed_end_minute": 600,
    }
    contract = json.loads((root / "configs/p1b_execution_research.json").read_text(encoding="utf-8"))
    manifest = build_manifest(root, scenarios, BASELINE_POLICIES,
                              contract, SCHEMA_VERSIONS, options, splits)
    manifest.pop("signature")
    manifest.update({
        "manifest_version": MANIFEST_VERSION,
        "policy_search_proposal": _proposal_reference(root, proposal, proposal_path),
        "policy_search_source_sha256": _source_binding(root),
        "policy_search_distribution": DISTRIBUTION,
        "policy_search_role_counts": copy.deepcopy(proposal["role_root_counts"]),
        "policy_search_final_12_backgrounds_unopened": True,
        "p2b_time_blocks": copy.deepcopy(proposal["blocks"]),
        "p2b_support": [dict(scenario_id=s["id"], root_family_id=s["root_family_id"],
                              time_block_id=s["time_block_id"], **s["full_source_support"])
                        for s in scenarios],
        "p2b_shards": [dict(id=f"shard_{i // 25:02d}",
                             scenario_ids=[scene["id"] for scene in scenarios[i:i + 25]])
                        for i in range(0, len(scenarios), 25)],
        "p2b_study_distribution": {
            "id": DISTRIBUTION,
            "p_reference": None,
            "importance_weight": None,
            "interpretation": "Named development coverage, not hospital failure probability",
        },
    })
    manifest["signature"] = digest(manifest)
    return manifest


def verify_manifest(workspace_root: str | Path, manifest: dict) -> bool:
    root = Path(workspace_root).resolve()
    if not isinstance(manifest, dict):
        raise ValueError("V5 manifest must be a record")
    body = copy.deepcopy(manifest)
    signature = body.pop("signature", None)
    if signature != digest(body) or manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError("V5 manifest signature/version changed")
    reference = manifest.get("policy_search_proposal", {})
    if "embedded" in reference:
        proposal = reference["embedded"]
        if reference.get("sha256") != digest(proposal):
            raise ValueError("Embedded proposal hash changed")
        proposal_path = None
    else:
        name = reference.get("path")
        if not isinstance(name, str) or not name:
            raise ValueError("V5 manifest lacks proposal path")
        proposal_path = Path(name)
        if not proposal_path.is_absolute():
            proposal_path = root / proposal_path
        if not proposal_path.is_file() or _sha(proposal_path) != reference.get("sha256"):
            raise ValueError("V5 manifest proposal byte hash changed")
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    if proposal.get("signature") != reference.get("signature"):
        raise ValueError("V5 manifest proposal signature changed")
    expected = build_manifest_from_proposal(root, proposal, proposal_path=proposal_path)
    if manifest != expected:
        raise ValueError("V5 manifest differs from frozen proposal, source or physical inventory")
    return True


def freeze(workspace_root: str | Path, run_dir: str | Path, *, seed: int = 20261016) -> dict:
    """Write a new source proposal and manifest once; preserve partial errors."""
    root = Path(workspace_root).resolve()
    out = Path(run_dir).resolve()
    proposal_path = out / "design/source_proposal.json"
    manifest_path = out / "new_data/global_manifest.json"
    receipt_path = out / "design/V5_SOURCE_FREEZE.json"
    if any(path.exists() for path in (proposal_path, manifest_path, receipt_path)):
        raise FileExistsError("V5 source freeze already started; preserve and use a new versioned run")
    proposal = build_proposal(root, seed=seed)
    _write_new(proposal_path, proposal)
    manifest = build_manifest_from_proposal(root, proposal, proposal_path=proposal_path)
    verify_manifest(root, manifest)
    _write_new(manifest_path, manifest)
    receipt = {
        "schema": "policy_search.v5.source_freeze.v1",
        "proposal_path": str(proposal_path),
        "proposal_sha256": _sha(proposal_path),
        "proposal_signature": proposal["signature"],
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha(manifest_path),
        "manifest_signature": manifest["signature"],
        "registered_source_roots": ROOT_COUNT,
        "registered_specifications": SPEC_COUNT,
        "reserved_unselected_day_count": len(proposal["reserved_unselected_days"]),
        "final_12_backgrounds_unopened": True,
        "simulations_started": 0,
    }
    _write_new(receipt_path, receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("freeze", help="write a new outcome-free v5 source design")
    create.add_argument("workspace_root", type=Path)
    create.add_argument("run_dir", type=Path)
    create.add_argument("--seed", type=int, default=20261016)
    check = sub.add_parser("verify", help="verify the frozen v5 manifest")
    check.add_argument("workspace_root", type=Path)
    check.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    if args.command == "freeze":
        result = freeze(args.workspace_root, args.run_dir, seed=args.seed)
    else:
        value = json.loads(args.manifest.read_text(encoding="utf-8"))
        result = {"verified": verify_manifest(args.workspace_root, value),
                  "manifest_signature": value["signature"]}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
