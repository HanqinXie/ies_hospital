"""Outcome-free v6 selection/development roots on unused real annual load support.

The v5 thirty training roots remain the training source.  This module registers
new calendar days only for selection and development; the old twelve final
backgrounds and every previously exposed or reserved v5 day remain closed.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.data_contract import SCHEMA_VERSIONS
from hospital_ies.dataset_manifest import build_manifest, digest
from hospital_ies.resilience import validate_scenario
from tools.control_improvement.data_design import CONDITIONS, PRESSURES, _rng, _spec, exposure_registry

V5_RUN = ROOT / "outputs/v5_policy_search_20261015_run01"
VERSION = "policy_redesign.v6.new60.v1"
SEED = 20261016
EMBARGO_MINUTES = 60


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _overlap(a, b):
    return (float(a["raw_start_minute"]) < float(b["raw_end_minute"])
            and float(b["raw_start_minute"]) < float(a["raw_end_minute"]))


def reject_overlaps(blocks, exposures):
    for block in blocks:
        if any(_overlap(block, old) for old in exposures):
            raise ValueError("New annual source support overlap with an exposed/reserved interval")
    return True


def choose_new_blocks(*, available_days, excluded_days, sealed_days, seed):
    """Pick 1 selection + 1 development day per condition without outcomes."""
    if type(seed) is not int or seed < 0:
        raise ValueError("Seed must be nonnegative integer")
    candidates = list(available_days)
    if len(candidates) != len(set(candidates)) or any(type(day) is not int or not 0 <= day < 365
                                                     for day in candidates):
        raise ValueError("Available days must be unique original calendar indices")
    excluded = set(excluded_days) | set(sealed_days)
    clean = sorted(set(candidates) - excluded)
    if len(clean) < len(CONDITIONS) * 2:
        raise ValueError("Insufficient unused annual support")
    selected = [int(x) for x in _rng(seed, "v6_outcome_free_calendar").permutation(clean)[:12]]
    blocks = []
    for condition_index, condition_id in enumerate(CONDITIONS):
        config = CONDITIONS[condition_id]
        for within in range(2):
            day = selected[condition_index * 2 + within]
            timing = _rng(seed, "v6_absolute_event_timing", day)
            role = "selection" if within == 0 else "development"
            device = config["devices"][within % len(config["devices"])] if config["devices"] else None
            blocks.append({
                "time_block_id": f"policy_redesign_day_{day:03d}",
                "day_index": day, "condition_id": condition_id, "role": role,
                "device": device, "raw_start_minute": day * 1440,
                "raw_end_minute": (day + 1) * 1440,
                "start_hour": day * 24 + int(_rng(seed, "v6_within_day_hour", day).integers(5, 11)) + 1,
                "event_timing": {
                    "shock_start_minute": float(timing.integers(90, 151)),
                    "shock_duration_minutes": float(timing.choice([180, 240])),
                    "fault_offset_minutes": float(timing.choice([-30, 0, 30])),
                    "fault_duration_minutes": float(timing.choice([180, 240])),
                },
                "reserved_only": False,
                "selection": "Seeded calendar-only design before new outcomes",
            })
    return blocks


def _scene(block, seed, pressure):
    scene = _spec(block, seed, pressure)
    scene.update(sample_schema_version="policy_redesign.v6.fixed_mechanisms.v1",
                 distribution_id="q_design.policy_redesign.v6.fixed_mechanisms.v1",
                 sampling_method="registered_v6_unused_calendar_day_pressure_crosscoverage",
                 sampling_cohort="v6_policy_redesign", experiment_role=block["role"],
                 purpose="v6_independent_selection_development", is_final_blind=False)
    validate_scenario(scene)
    support = scene["full_source_support"]
    if (float(support["raw_start_minute"]) - EMBARGO_MINUTES < block["raw_start_minute"]
            or float(support["raw_end_minute"]) + EMBARGO_MINUTES > block["raw_end_minute"]):
        raise ValueError("Complete load/preheat/tail support and embargo leaves registered day")
    return scene


def build_proposal(*, workspace_root=ROOT, seed=SEED):
    root = Path(workspace_root).resolve()
    old, registry = exposure_registry(root)
    if len(registry["sealed_final_days"]) != 12:
        raise ValueError("Original final 12 seal absent")
    v5_path = root / V5_RUN.relative_to(ROOT) / "design/source_proposal.json"
    v5 = _read(v5_path)
    if len(v5["blocks"]) != 54 or len({b["day_index"] for b in v5["blocks"]}) != 54:
        raise ValueError("V5 day reservation changed")
    reserved = [dict(raw_start_minute=b["raw_start_minute"], raw_end_minute=b["raw_end_minute"],
                     source="v5_registered_root", day_index=b["day_index"]) for b in v5["blocks"]]
    exposures = copy.deepcopy(old) + reserved
    available = [day for day in range(365) if not any(_overlap(
        {"raw_start_minute": day * 1440, "raw_end_minute": (day + 1) * 1440}, item)
        for item in exposures)]
    blocks = choose_new_blocks(available_days=available, excluded_days=set(),
                               sealed_days=registry["sealed_final_days"], seed=seed)
    reject_overlaps(blocks, exposures)
    scenes = [_scene(block, seed, pressure) for block in blocks
              for pressure in range(len(PRESSURES))]
    proposal = {
        "version": VERSION + ".proposal", "seed": seed, "blocks": blocks,
        "scenarios": scenes, "available_days": available,
        "sealed_final_days": registry["sealed_final_days"],
        "historical_exposure_registry": registry,
        "historical_exposure_sha256": digest(old),
        "v5_reserved_all_54_days_sha256": _sha(v5_path),
        "v5_training_manifest_sha256": _sha(root / V5_RUN.relative_to(ROOT) / "new_data/global_manifest.json"),
        "new_specifications": len(scenes), "new_roots": len(blocks),
        "selection_roots": 6, "development_roots": 6,
        "old_train_roots_reused": 30, "old_train_specs_reused": 150,
        "final_12_backgrounds_unopened": True,
        "selection_uses_outcomes": False,
        "source_sha256": {name: _sha(root / name) for name in (
            "tools/policy_redesign/data_design.py", "tools/control_improvement/data_design.py",
            "src/hospital_ies/resilience.py", "scenario_sampler.py")},
    }
    proposal["signature"] = digest(proposal)
    return proposal


def build_v6_manifest(proposal, *, workspace_root=ROOT, proposal_path=None):
    root = Path(workspace_root).resolve()
    body = copy.deepcopy(proposal)
    if body.pop("signature", None) != digest(body):
        raise ValueError("Proposal signature changed")
    if len(proposal["scenarios"]) != 60 or len(proposal["blocks"]) != 12:
        raise ValueError("Registered new root/spec count changed")
    for name, expected in proposal["source_sha256"].items():
        if _sha(root / name) != expected:
            raise ValueError("Source changed after proposal")
    scenarios = proposal["scenarios"]
    splits = {}
    for scene in scenarios:
        role = scene["experiment_role"]
        support = scene["full_source_support"]
        splits[scene["id"]] = dict(
            group_id="policy_redesign_background:" + digest([proposal["seed"], scene["time_block_id"]]),
            split=role, experiment_role=role, fit_allowed=False,
            selection_allowed=role == "selection", development_only=role == "development",
            split_version="policy_redesign.v6.day_support.v1", embargo_minutes_each_side=60,
            raw_start_minute=support["raw_start_minute"],
            raw_end_minute=support["raw_end_minute"], time_block_id=scene["time_block_id"],
            purpose="v6_independent_selection_development", is_final_blind=False)
    options = dict(seed=proposal["seed"], security_weight=.35,
                   label_horizons=[15, 30, 60, 120], history_minutes=60,
                   score_start_minute=60, score_end_minute=420, observed_end_minute=600)
    contract = _read(root / "configs/p1b_execution_research.json")
    manifest = build_manifest(root, scenarios, ("priority_milp", "secure_milp"),
                              contract, SCHEMA_VERSIONS, options, splits)
    manifest.pop("signature")
    manifest.update(manifest_version=VERSION,
                    v6_proposal_sha256=_sha(proposal_path) if proposal_path else digest(proposal),
                    v6_proposal_signature=proposal["signature"],
                    v6_final_12_backgrounds_unopened=True,
                    v6_role_roots={"selection": 6, "development": 6},
                    v6_source_binding=proposal["source_sha256"])
    manifest["signature"] = digest(manifest)
    return manifest


def freeze(workspace_root, run_dir, *, seed=SEED):
    root = Path(workspace_root).resolve()
    out = Path(run_dir).resolve()
    proposal_path = out / "design/v6_source_proposal.json"
    manifest_path = out / "new_data/global_manifest.json"
    if proposal_path.exists() or manifest_path.exists():
        raise FileExistsError("New v6 source registration already exists")
    proposal = build_proposal(workspace_root=root, seed=seed)
    _write_new(proposal_path, proposal)
    manifest = build_v6_manifest(proposal, workspace_root=root, proposal_path=proposal_path)
    _write_new(manifest_path, manifest)
    receipt = dict(proposal_path=str(proposal_path), proposal_sha256=_sha(proposal_path),
                   manifest_path=str(manifest_path), manifest_sha256=_sha(manifest_path),
                   new_roots=12, new_specifications=60, simulations_started=0,
                   final_12_backgrounds_unopened=True)
    _write_new(out / "design/V6_SOURCE_FREEZE.json", receipt)
    return receipt


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace_root", type=Path)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    print(json.dumps(freeze(args.workspace_root, args.run_dir, seed=args.seed), ensure_ascii=False))
