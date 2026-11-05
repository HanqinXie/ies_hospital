"""Outcome-free v4 snapshot design and training-only exogenous stress variants.

The source proposal/manifest remains the v8 registered hospital-data schema;
this module never writes or changes an existing proposal, manifest, or result.
Conditional futures are study stress implementations, not probabilities and
never inputs to a live policy. A branch runner must fork the full current state
and apply a variant only after the snapshot boundary.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from hospital_ies.resilience import validate_scenario
from hospital_ies.dataset_manifest import digest


ROLES = ("pilot_train", "pilot_validation", "development_boundary")
CLOCK_MENUS = (
    (0.0, 15.0, 30.0, 45.0),
    (105.0, 120.0, 135.0, 150.0),
    (165.0, 180.0, 195.0, 210.0),
    (225.0, 240.0, 255.0),
)
FUTURE_DESIGN = "v4.named_persistence_stress.v1"


def _specs_and_splits(proposal_or_manifest: dict) -> tuple[list[dict], dict]:
    if "fixed_scenarios" in proposal_or_manifest:
        return proposal_or_manifest["fixed_scenarios"], {}
    if "scenarios" in proposal_or_manifest:
        return proposal_or_manifest["scenarios"], proposal_or_manifest.get("splits", {})
    raise ValueError("Registered proposal or manifest scenarios required")


def snapshot_plan(proposal_or_manifest: dict, max_snapshots: int = 600) -> list[dict]:
    """Choose diverse absolute clocks without reading outcomes or event times.

    The four clock families span preparation, early response, later response,
    and persistence/recovery observation. They are rotated by root order, not
    by fault/shock onset, failure label, future recovery, or model prediction.
    A selected snapshot may later be unreached or have unknown outcomes; such
    attempts stay in the downstream ledger and are never silently replaced.
    """
    if not isinstance(max_snapshots, int) or max_snapshots < 1 or max_snapshots > 600:
        raise ValueError("Snapshot budget must be an integer in 1..600")
    specs, splits = _specs_and_splits(proposal_or_manifest)
    roots: dict[str, list[dict]] = defaultdict(list)
    ids = set()
    for s in specs:
        sid = s["id"]
        if sid in ids:
            raise ValueError("Duplicate scenario identity in snapshot plan")
        ids.add(sid)
        root = s["root_family_id"]
        role = s["experiment_role"]
        if role not in ROLES or (sid in splits and splits[sid]["experiment_role"] != role):
            raise ValueError("Unregistered or inconsistent root role")
        roots[root].append(s)
    if not roots:
        raise ValueError("No registered source roots")

    chosen = []
    supports = []
    for root in sorted(roots):
        members = roots[root]
        if len({s["experiment_role"] for s in members}) != 1:
            raise ValueError("Connected root crosses experiment roles")
        source = next((s for s in members if s.get("pressure_point_index") == 3),
                      min(members, key=lambda s: s["id"]))
        source_support = source.get("full_source_support")
        if source_support is None and source["id"] in splits:
            source_support = splits[source["id"]]
        if source_support is None:
            raise ValueError("Complete original source support required")
        bounds = (float(source_support["raw_start_minute"]),
                  float(source_support["raw_end_minute"]))
        if not 0 <= bounds[0] < bounds[1] <= 8760 * 60:
            raise ValueError("Invalid 8760-hour source support")
        if any(s.get("full_source_support", source_support) != source_support for s in members):
            raise ValueError("One root has inconsistent source support")
        supports.append((bounds, root))
        split = splits.get(source["id"], {})
        chosen.append((root, source, split, bounds))
    supports.sort()
    for ((left, left_root), (right, right_root)) in zip(supports, supports[1:]):
        # Keep a 60-minute embargo on both sides of distinct original blocks.
        if left[1] + 60 > right[0] - 60:
            raise ValueError(f"Original source support/embargo overlap: {left_root}, {right_root}")

    rows = []
    for slot, menu in enumerate(CLOCK_MENUS):
        for index, (root, source, split, bounds) in enumerate(chosen):
            t = menu[index % len(menu)]
            step_minutes = float(source.get("step_minutes", 15))
            end = float(source["duration_steps"]) * step_minutes
            if not math.isclose(t / step_minutes, round(t / step_minutes), abs_tol=1e-9):
                raise ValueError("Predeclared clock is not a decision boundary")
            if t + 120 > end + 1e-9:
                raise ValueError("Source support does not include H120 observation")
            rows.append({
                "scenario_id": source["id"], "root_family_id": root,
                "time_block_id": source["time_block_id"],
                "group_id": split.get("group_id", (
                    "control_background:" + digest([proposal_or_manifest["sampling_seed"], source["time_block_id"]])
                    if "sampling_seed" in proposal_or_manifest else root)),
                "experiment_role": source["experiment_role"],
                "snapshot_minute": t, "snapshot_step": int(round(t / step_minutes)),
                "snapshot_phase_design": ("preparation", "early_response", "later_response", "persistence_or_recovery")[slot],
                "selection_rule": "Predeclared absolute clock rotated by sorted source root, no outcome or event lookup",
                "selection_uses_future_event_clock": False,
                "conditional_futures": 4,
                "future_design": FUTURE_DESIGN,
                "continuation_policy_id": "secure_milp",
                "horizons_minutes": [60, 120],
                "source_raw_start_minute": bounds[0],
                "source_raw_end_minute": bounds[1],
                "is_final_blind": False,
            })
            if len(rows) == max_snapshots:
                return rows
    return rows


def _clock(scenario: dict, kind: str) -> tuple[float, float]:
    step = float(scenario.get("step_minutes", 15))
    start = float(scenario.get(f"{kind}_start_minute",
                               float(scenario.get(f"{kind}_start_step", 0)) * step))
    duration = float(scenario.get(f"{kind}_duration_minutes",
                                  float(scenario.get(f"{kind}_duration_steps", scenario["duration_steps"])) * step))
    return start, start + duration


def _active(scenario: dict, kind: str, minute: float) -> dict:
    start, end = _clock(scenario, kind)
    values = scenario.get("faults", {}) if kind == "fault" else {
        "shock": scenario.get("shock", {}), "shock_by_priority": scenario.get("shock_by_priority", {})
    }
    return values if start <= minute < end else {}


def _prefix_same(original: dict, variant: dict, minute: float) -> bool:
    checkpoints = {0.0, minute}
    for s in (original, variant):
        for kind in ("fault", "shock"):
            start, end = _clock(s, kind)
            checkpoints.update(x for x in (start, end) if 0 <= x <= minute)
    ordered = sorted(checkpoints)
    probes = ordered + [(a + b) / 2 for a, b in zip(ordered, ordered[1:])]
    return all(_active(original, kind, t) == _active(variant, kind, t)
               for kind in ("fault", "shock") for t in probes)


def conditional_futures(scenario: dict, snapshot_minute: float, count: int = 4) -> list[dict]:
    """Return original plus distinct, causal persistence stresses.

    Only an event whose original end is *strictly after* the snapshot may be
    extended. The known onset, active severity, full prefix and all equipment
    permissions remain identical. No probability is assigned. When the schema
    cannot represent enough distinct legal futures, fail for explicit unknown
    accounting instead of returning duplicates or rewriting observed history.
    """
    validate_scenario(scenario)
    if not isinstance(count, int) or count < 1 or count > 8:
        raise ValueError("Conditional future count must be in 1..8")
    t = float(snapshot_minute)
    horizon = float(scenario["duration_steps"]) * float(scenario.get("step_minutes", 15))
    if not math.isfinite(t) or t < 0 or t >= horizon:
        raise ValueError("Snapshot minute outside scenario")
    step = float(scenario.get("step_minutes", 15))
    if not math.isclose(t / step, round(t / step), abs_tol=1e-9):
        raise ValueError("Snapshot must be a decision boundary")
    live = []
    if (scenario.get("shock") or scenario.get("shock_by_priority")) and _clock(scenario, "shock")[1] > t:
        live.append("shock")
    if scenario.get("faults") and _clock(scenario, "fault")[1] > t:
        live.append("fault")
    if count > 1 and not live:
        raise ValueError("All exogenous events ended in known prefix; distinct future stress unavailable")
    variants = [copy.deepcopy(scenario)]
    seen = {(_clock(scenario, "shock")[1], _clock(scenario, "fault")[1])}
    for index in range(1, count):
        variant = copy.deepcopy(scenario)
        if len(live) == 2 and index % 3 == 2:
            selected = ("fault",)
        elif len(live) == 2 and index % 3 == 0:
            selected = ("shock", "fault")
        else:
            selected = ("shock",) if "shock" in live else ("fault",)
        extension = 15.0 * index
        for kind in selected:
            start, end = _clock(scenario, kind)
            new_end = end + extension
            if new_end > horizon + 1e-9:
                raise ValueError("Distinct future persistence exceeds observation support")
            variant[f"{kind}_duration_minutes"] = new_end - start
            for event in variant.get("exogenous_events", []):
                if event.get("kind") == ("device_fault" if kind == "fault" else "load_shock"):
                    if not math.isclose(float(event["start_minute"]), start, abs_tol=1e-9):
                        raise ValueError("Exogenous event ledger differs from scenario clock")
                    event["end_minute"] = new_end
        variant["conditional_future_id"] = f"{FUTURE_DESIGN}:stress{index}"
        variant["conditional_future_probability"] = None
        variant["conditional_future_known_prefix_minute"] = t
        validate_scenario(variant)
        if not _prefix_same(scenario, variant, t):
            raise ValueError("Conditional future changed known prefix or current state")
        key = (_clock(variant, "shock")[1], _clock(variant, "fault")[1])
        if key in seen:
            raise ValueError("Conditional future is a duplicate stress implementation")
        seen.add(key)
        variants.append(variant)
    return variants


def check_prepare_destination(run_dir: str | Path) -> None:
    """An intake run may exist; no partially prepared source may be reused."""
    out = Path(run_dir)
    owned = (
        out / "design/source_proposal.json",
        out / "design/snapshot_plan.json",
        out / "design/V4_SOURCE_PREPARE.json",
        out / "design/.prepare.lock",
        out / "new_data",
    )
    existing = [str(path) for path in owned if path.exists()]
    if existing:
        raise FileExistsError("Existing or partial v4 prepare output; use a new run: " + ", ".join(existing))


def prepare(run_dir: str | Path, *, seed: int = 20261015,
            core_gate: str | Path | None = None) -> dict:
    """Register new isolated real-data support and freeze two original baselines.

    This reuses the existing audited v8 source proposal and producer unchanged.
    All v4 artifacts are new. A failed freeze remains visible for diagnosis and
    must be retried under a fresh run directory, never overwritten in place.
    """
    from tools.control_improvement.data_design import build_proposal, validate_proposal
    from tools.control_improvement.data_runner import freeze_new

    out = Path(run_dir).resolve()
    check_prepare_destination(out)
    if core_gate is None:
        core_gate = ROOT / "outputs/v43_integrated_20261014_run01/core_gate_v2.json"
    gate = Path(core_gate).resolve()
    if not gate.is_file():
        raise FileNotFoundError(f"Registered physical core gate missing: {gate}")
    proposal = build_proposal(ROOT, seed=int(seed))
    validate_proposal(proposal)
    snapshots = snapshot_plan(proposal)
    # New root ownership is established only after pure source validation.
    check_prepare_destination(out)
    design = out / "design"
    design.mkdir(parents=True, exist_ok=True)
    with (design / ".prepare.lock").open("x", encoding="utf-8") as lock:
        lock.write("v4 source prepare started; preserve this attempt if it fails\n")
    proposal_path = design / "source_proposal.json"
    proposal_path.write_text(json.dumps(proposal, indent=2, ensure_ascii=False), encoding="utf-8")
    (design / "snapshot_plan.json").write_text(
        json.dumps(snapshots, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = freeze_new(out / "new_data", gate, proposal_path)
    receipt = {
        "version": "reliability_first.v4.source_support.v1",
        "run_directory": str(out),
        "proposal_path": str(proposal_path),
        "proposal_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
        "registered_specifications": len(proposal["fixed_scenarios"]),
        "registered_source_roots": len(proposal["blocks"]),
        "registered_snapshot_attempts": len(snapshots),
        "baseline_policies": manifest["policies"],
        "manifest_path": str(out / "new_data/global_manifest.json"),
        "manifest_signature": manifest["signature"],
        "final_12_backgrounds_unopened": True,
        "conditional_future_design": FUTURE_DESIGN,
    }
    (design / "V4_SOURCE_PREPARE.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    return receipt


def correct_plan(run_dir: str | Path) -> dict:
    """Version only the pre-branch group ID error in an already frozen plan.

    The original proposal, v1 plan and baseline manifest remain byte-identical.
    Every other field in every plan row must match the newly computed plan;
    changes beyond group ID require a separate versioned design decision.
    """
    out = Path(run_dir).resolve()
    design = out / "design"
    old_path = design / "snapshot_plan.json"
    new_path = design / "snapshot_plan_v2.json"
    correction_path = design / "SNAPSHOT_PLAN_V2_CORRECTION.json"
    proposal_path = design / "source_proposal.json"
    manifest_path = out / "new_data/global_manifest.json"
    prepare_path = design / "V4_SOURCE_PREPARE.json"
    if new_path.exists() or correction_path.exists():
        raise FileExistsError("Existing v2 snapshot plan/correction; refuse overwrite")
    for path in (old_path, proposal_path, manifest_path, prepare_path):
        if not path.is_file():
            raise FileNotFoundError("Frozen v4 source artifact missing: " + str(path))
    raw = {name: path.read_bytes() for name, path in (
        ("old_plan", old_path), ("proposal", proposal_path),
        ("manifest", manifest_path), ("prepare", prepare_path))}
    old = json.loads(raw["old_plan"])
    proposal = json.loads(raw["proposal"])
    manifest = json.loads(raw["manifest"])
    prepared = json.loads(raw["prepare"])
    sha = lambda content: hashlib.sha256(content).hexdigest()
    if sha(raw["proposal"]) != prepared["proposal_sha256"]:
        raise ValueError("Frozen proposal bytes differ from original prepare receipt")
    if manifest["signature"] != prepared["manifest_signature"]:
        raise ValueError("Frozen manifest signature differs from original prepare receipt")
    expected_ids = {s["id"] for s in proposal["fixed_scenarios"]}
    if {s["id"] for s in manifest["scenarios"]} != expected_ids:
        raise ValueError("Frozen manifest scenarios differ from original proposal")
    new = snapshot_plan(proposal)
    if len(old) != len(new):
        raise ValueError("Original snapshot plan row count differs")
    for index, (before, after) in enumerate(zip(old, new)):
        if not isinstance(before, dict) or "group_id" not in before:
            raise ValueError("Original snapshot plan row is invalid")
        same_except_group = dict(after, group_id=before["group_id"])
        if before != same_except_group:
            raise ValueError(f"Original snapshot plan differs beyond group_id at row {index}")
        sid = after["scenario_id"]
        if after["group_id"] != manifest["splits"][sid]["group_id"]:
            raise ValueError("Corrected group_id does not match frozen manifest split")
        if after["experiment_role"] != manifest["splits"][sid]["experiment_role"]:
            raise ValueError("Corrected plan role does not match frozen manifest split")
    # Both new artifacts are write-once. A partial correction is retained for
    # diagnosis and a subsequent call refuses it rather than overwriting.
    new_bytes = json.dumps(new, indent=2, ensure_ascii=False).encode("utf-8")
    with new_path.open("xb") as stream:
        stream.write(new_bytes)
    receipt = {
        "version": "reliability_first.v4.snapshot_plan_group_correction.v2",
        "old_plan_path": str(old_path), "old_plan_sha256": sha(raw["old_plan"]),
        "new_plan_path": str(new_path), "new_plan_sha256": sha(new_bytes),
        "proposal_path": str(proposal_path), "proposal_sha256": sha(raw["proposal"]),
        "manifest_path": str(manifest_path), "manifest_sha256": sha(raw["manifest"]),
        "manifest_signature": manifest["signature"],
        "changed_field_paths": ["[*].group_id"],
        "rows_checked": len(new),
        "original_artifacts_mutated": False,
        "branch_results_under_v1_observed": len(list(out.rglob("BRANCH_RESULT.json"))),
        "reason": "Initial proposal-based plan used root_family_id where frozen manifest registered group_id",
    }
    with correction_path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, ensure_ascii=False)
    return receipt


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare", help="freeze a fresh v4 hospital-data run")
    command.add_argument("run_dir")
    command.add_argument("--seed", type=int, default=20261015)
    command.add_argument("--core-gate")
    correction = sub.add_parser("correct-plan", help="write a new plan with frozen manifest group IDs")
    correction.add_argument("run_dir")
    args = parser.parse_args(argv)
    result = (prepare(args.run_dir, seed=args.seed, core_gate=args.core_gate)
              if args.command == "prepare" else correct_plan(args.run_dir))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
