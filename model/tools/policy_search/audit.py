"""Independent, fail-closed evidence audit before v5 policy scoring.

This module never runs or changes a controller. It binds a registered plan,
source freeze, manifest inventory, per-attempt records and raw trajectories to
one immutable AUDIT.json receipt. A physical early stop remains an unknown
outcome; it is not treated as a zero or as a runner exception.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "policy_search.evidence_audit.v1"
RUNNER_SCHEMA = "policy_search.matched_full_feedback.v1"
FROZEN_ANALYSIS_SOURCES = (
    "tools/policy_search/search.py",
    "tools/policy_search/preference.py",
    "tools/policy_search/closed_loop.py",
    "tools/policy_search/policy.py",
)
PHYSICS_FILES = {
    "src/hospital_ies/model.py", "src/hospital_ies/resilience.py",
    "src/hospital_ies/control_env.py", "src/hospital_ies/state_transition.py",
    "src/hospital_ies/execution.py", "src/hospital_ies/thermal_inertia.py",
    "src/hospital_ies/idf_parameters.py", "configs/plant_configs.json",
    "configs/resilience_assumptions.json", "src/hospital_ies/solver_backend.py",
    "src/hospital_ies/solver_diagnostics.py",
}
TARGET_COUNT = 19


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


def _error(errors, code, detail=None):
    errors.append(code if detail is None else f"{code}: {detail}")


def _safe_inventory_path(name):
    path = (ROOT / name).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("Manifest inventory path escapes project root") from exc
    return path


def _audit_plan(plan_path, errors):
    plan = _read(plan_path)
    if plan.get("schema") != RUNNER_SCHEMA:
        _error(errors, "plan_schema")
    body = {k: v for k, v in plan.items() if k != "signature"}
    if plan.get("signature") != _digest(body):
        _error(errors, "plan_signature")
    if plan.get("final_blind_opened") is not False:
        _error(errors, "final_blind_opened")
    ids = plan.get("scenario_ids", [])
    specs = plan.get("policy_specs", [])
    policy_ids = [s.get("policy_id") for s in specs]
    if len(ids) != len(set(ids)) or len(policy_ids) != len(set(policy_ids)):
        _error(errors, "duplicate_registered_identity")
    if not ids or not specs or plan.get("registered_pair_count") != len(ids) * len(specs):
        _error(errors, "registered_pair_count")
    if not any(s == {"policy_id": "secure_milp", "kind": "secure"} for s in specs):
        _error(errors, "secure_baseline_missing")
    if _sha(plan["preference_path"]) != plan.get("preference_sha256"):
        _error(errors, "preference_sha256")
    if _sha(plan["manifest_path"]) != plan.get("manifest_sha256"):
        _error(errors, "manifest_sha256")
    for name, expected in plan.get("source_sha256", {}).items():
        if _sha(_safe_inventory_path(name)) != expected:
            _error(errors, "runner_source_sha256", name)
    manifest = _read(plan["manifest_path"])
    if manifest.get("signature") != _digest({k: v for k, v in manifest.items()
                                              if k != "signature"}):
        _error(errors, "manifest_signature")
    for key in ("signature", "simulator_hash", "execution_contract_id"):
        planned = plan.get("manifest_signature" if key == "signature" else key)
        if manifest.get(key) != planned:
            _error(errors, "manifest_plan_binding", key)
    contract = manifest.get("execution_contract", {})
    expected_contract = str(contract.get("name")) + ":" + _digest(contract)
    if manifest.get("execution_contract_id") != expected_contract:
        _error(errors, "execution_contract_hash")
    inventory = manifest.get("files")
    if not isinstance(inventory, list) or not inventory:
        _error(errors, "physical_inventory_absent")
        inventory = []
    file_names = [item.get("path") for item in inventory]
    if len(file_names) != len(set(file_names)):
        _error(errors, "physical_inventory_duplicate")
    for item in inventory:
        name = item["path"]
        try:
            path = _safe_inventory_path(name)
            if not path.is_file() or path.stat().st_size != item["bytes"] or _sha(path) != item["sha256"]:
                _error(errors, "physical_inventory_sha256", name)
        except (ValueError, OSError) as exc:
            _error(errors, "physical_inventory_path", f"{name}: {exc}")
    physics = [item for item in inventory if item["path"] in PHYSICS_FILES]
    if not physics or _digest(physics) != manifest.get("simulator_hash"):
        _error(errors, "simulator_hash_from_inventory")
    scenes = {s["id"]: s for s in manifest["scenarios"]}
    for sid in ids:
        scene = scenes.get(sid)
        split = manifest.get("splits", {}).get(sid)
        if scene is None or split is None:
            _error(errors, "scenario_not_in_manifest", sid)
        elif (scene.get("experiment_role") != plan.get("phase") or
              split.get("experiment_role") != plan.get("phase") or
              scene.get("is_final_blind") or split.get("is_final_blind")):
            _error(errors, "scenario_role_or_final_leakage", sid)
    return plan, manifest, scenes, len(inventory)


def _audit_freeze(plan, freeze_path, registration_path, errors):
    freeze = _read(freeze_path)
    registration = _read(registration_path)
    if freeze.get("schema") != "policy_search.algorithm_source_freeze.v1":
        _error(errors, "source_freeze_schema")
    frozen = freeze.get("source_sha256", {})
    for name in FROZEN_ANALYSIS_SOURCES:
        expected = frozen.get(name)
        if expected is None or _sha(ROOT / name) != expected:
            _error(errors, "source_freeze_sha256", name)
    if freeze.get("preference_sha256") != plan.get("preference_sha256"):
        _error(errors, "source_freeze_preference")
    if freeze.get("v5_new_manifest_sha256") != plan.get("manifest_sha256"):
        _error(errors, "source_freeze_manifest")
    if registration.get("schema") != "policy_search.offline_generation.v1":
        _error(errors, "generation_registration_schema")
    for key, expected in (("plan_signature", plan.get("signature")),
                          ("scenario_ids", plan.get("scenario_ids")),
                          ("policy_specs", plan.get("policy_specs")),
                          ("manifest_sha256", plan.get("manifest_sha256")),
                          ("preference_sha256", plan.get("preference_sha256"))):
        if registration.get(key) != expected:
            _error(errors, "generation_registration_binding", key)
    if registration.get("source_sha256") != frozen.get(FROZEN_ANALYSIS_SOURCES[0]):
        _error(errors, "generation_search_source_binding")
    return freeze


def _check_score(score, row, field, start, horizon, errors, observed_counts):
    tag = f"{row['scenario_id']}/{row['policy_id']}/{field}"
    if not isinstance(score, dict):
        _error(errors, "score_missing", tag)
        return False
    if (score.get("start_minute") != start or score.get("horizon_minutes") != horizon):
        _error(errors, "score_window", tag)
    full = score.get("full_horizon")
    if not isinstance(full, bool):
        _error(errors, "score_full_horizon_type", tag)
        full = False
    if score.get("unknown_after_window_prefix") is not (not full):
        _error(errors, "unknown_after_prefix", tag)
    targets = score.get("targets")
    mask = score.get("target_mask")
    if (not isinstance(targets, list) or not isinstance(mask, list) or
            len(targets) != TARGET_COUNT or len(mask) != TARGET_COUNT):
        _error(errors, "score_target_schema", tag)
        return full
    if any(not isinstance(value, bool) for value in mask):
        _error(errors, "score_mask_type", tag)
    if mask != [value is not None for value in targets]:
        _error(errors, "unknown_mask_value_mismatch", tag)
    for j, value in enumerate(targets):
        if value is not None and (isinstance(value, bool) or
                                  not isinstance(value, (int, float)) or
                                  not math.isfinite(value)):
            _error(errors, "score_nonfinite", f"{tag}/target_{j}")
        if value is not None:
            observed_counts[field][j] += 1
    if full and not all(mask):
        _error(errors, "unknown_mask_full_window", tag)
    if not full and (any(value is not None for value in targets[1:]) or
                     targets[0] not in (None, 1, 1.0)):
        _error(errors, "unknown_mask_truncated_future", tag)
    observed = score.get("observed_minutes")
    until = row.get("observed_until_minute")
    if not isinstance(observed, (int, float)) or not isinstance(until, (int, float)):
        _error(errors, "observed_window_missing", tag)
    else:
        expected = max(0.0, min(start + horizon, until) - start)
        if abs(observed - expected) > 1e-8 or full != (abs(expected - horizon) <= 1e-8):
            _error(errors, "observed_window_binding", tag)
    return full


def _audit_raw(row, attempt, plan, manifest, scene, spec, errors, observed_counts):
    tag = f"{row['scenario_id']}/{row['policy_id']}"
    if row.get("schema") != RUNNER_SCHEMA + ".pair":
        _error(errors, "pair_schema", tag)
    for key, expected in (("scenario_id", scene["id"]), ("policy_id", spec["policy_id"]),
                          ("kind", spec["kind"]), ("phase", plan["phase"]),
                          ("plan_signature", plan["signature"])):
        if row.get(key) != expected:
            _error(errors, "pair_identity", f"{tag}/{key}")
    if row.get("terminal") is not True:
        _error(errors, "nonterminal_pair", tag)
        return None
    raw_name = row.get("trajectory_path")
    if raw_name != "trajectory.json.gz":
        _error(errors, "raw_path", tag)
        return None
    raw = attempt / raw_name
    if not raw.is_file() or _sha(raw) != row.get("trajectory_sha256"):
        _error(errors, "raw_sha256", tag)
        return None
    try:
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            payload = json.load(stream,
                                parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _error(errors, "raw_decode", f"{tag}: {exc}")
        return None
    identity = payload.get("identity", {})
    required = {"scenario_id": scene["id"], "manifest_signature": manifest["signature"],
                "simulator_hash": manifest["simulator_hash"],
                "execution_contract_id": manifest["execution_contract_id"],
                "group_id": manifest["splits"][scene["id"]]["group_id"]}
    checks = {key: identity.get(key) == value for key, value in required.items()}
    checks["scenario_equal"] = payload.get("scenario") == scene
    checks["seed_equal"] = payload.get("seed") == manifest["options"]["seed"]
    checks["initial_state_present"] = payload.get("initial_state") is not None
    binding = row.get("binding", {})
    initial_hash = (_digest(payload["initial_state"])
                    if checks["initial_state_present"] else None)
    stream = identity.get("exogenous_stream_id")
    if (not all(checks.values()) or binding.get("passed") is not True or
            binding.get("checks") != checks or
            binding.get("initial_state_sha256") != initial_hash or
            binding.get("exogenous_stream_id") != stream or
            not stream or stream != scene.get("exogenous_stream_id")):
        _error(errors, "binding_passed_or_raw_mismatch", tag)
    if (row.get("status") != payload.get("summary", {}).get("status") or
            row.get("observed_until_minute") !=
            payload.get("summary", {}).get("observed_until_minute")):
        _error(errors, "raw_summary_mismatch", tag)
    windows = plan.get("score_windows", {})
    for field, key in (("primary_score", "primary"), ("full_observation", "full")):
        bounds = windows.get(key)
        if not isinstance(bounds, list) or len(bounds) != 2:
            _error(errors, "registered_score_window", key)
            continue
        _check_score(row.get(field), row, field, bounds[0], bounds[1] - bounds[0],
                     errors, observed_counts)
    return {"initial_state_sha256": initial_hash, "exogenous_stream_id": stream}


def audit_batch(plan_path, summary_path, freeze_path, receipt_path, *, registration_path=None):
    """Write a once-only receipt. `passed` must be true before any scoring."""
    plan_path = Path(plan_path)
    summary_path = Path(summary_path)
    freeze_path = Path(freeze_path)
    receipt_path = Path(receipt_path)
    registration_path = (Path(registration_path) if registration_path is not None
                         else plan_path.parent / "SEARCH_REGISTRATION.json")
    if receipt_path.exists():
        raise FileExistsError("Audit receipt is immutable; use a new versioned path")
    errors = []
    plan, manifest, scenes, inventory_count = _audit_plan(plan_path, errors)
    _audit_freeze(plan, freeze_path, registration_path, errors)
    summary = _read(summary_path)
    if summary.get("schema") != RUNNER_SCHEMA + ".summary":
        _error(errors, "summary_schema")
    if summary.get("plan_signature") != plan.get("signature"):
        _error(errors, "summary_plan_signature")
    specs = {s["policy_id"]: s for s in plan["policy_specs"]}
    expected = {(sid, pid) for sid in plan["scenario_ids"] for pid in specs}
    rows = summary.get("results", [])
    row_keys = [(r.get("scenario_id"), r.get("policy_id")) for r in rows]
    if (len(rows) != len(expected) or len(set(row_keys)) != len(row_keys) or
            set(row_keys) != expected):
        _error(errors, "pair_set", f"expected={len(expected)}, observed={len(rows)}")
    if (summary.get("registered_pair_count") != len(expected) or
            summary.get("recorded_pair_count") != len(rows) or
            summary.get("terminal_pair_count") != sum(r.get("terminal") is True for r in rows)):
        _error(errors, "summary_pair_counts")
    if summary.get("terminal_pair_count") != len(expected):
        _error(errors, "nonterminal_batch")
    result_dir = summary_path.parent
    pair_root = result_dir / "pairs"
    expected_directories = {_digest([sid, pid]) for sid, pid in expected}
    actual_directories = {p.name for p in pair_root.iterdir() if p.is_dir()} if pair_root.is_dir() else set()
    if actual_directories != expected_directories:
        _error(errors, "pair_directories", f"extra={len(actual_directories - expected_directories)}, "
               f"missing={len(expected_directories - actual_directories)}")
    observed_counts = {"primary_score": [0] * TARGET_COUNT,
                       "full_observation": [0] * TARGET_COUNT}
    unknown_primary = []
    unknown_full = []
    identities = defaultdict(list)
    attempts_inventory = []
    for row in rows:
        sid, pid = row.get("scenario_id"), row.get("policy_id")
        if (sid, pid) not in expected:
            continue
        pair_dir = pair_root / _digest([sid, pid])
        attempts = sorted(p for p in pair_dir.glob("attempt_*") if p.is_dir()
                          and p.name[8:].isdigit())
        if not attempts or len(attempts) > plan.get("max_runner_attempts_per_pair", 2):
            _error(errors, "attempt_count", f"{sid}/{pid}")
            continue
        for attempt in attempts:
            files = {p.name: _sha(p) for p in sorted(attempt.iterdir()) if p.is_file()}
            attempts_inventory.append({"pair_id": f"{sid}/{pid}", "attempt": attempt.name,
                                       "file_sha256": files})
            if "result.json" not in files:
                _error(errors, "orphan_attempt", f"{sid}/{pid}/{attempt.name}")
        latest = next((p for p in reversed(attempts) if (p / "result.json").is_file()), None)
        if latest is None:
            _error(errors, "missing_pair_result", f"{sid}/{pid}")
            continue
        disk_row = _read(latest / "result.json")
        if disk_row != row:
            _error(errors, "summary_result_mismatch", f"{sid}/{pid}")
        identity = _audit_raw(row, latest, plan, manifest, scenes[sid], specs[pid],
                              errors, observed_counts)
        if identity:
            identities[sid].append(identity)
        if not row.get("primary_score", {}).get("full_horizon"):
            unknown_primary.append(f"{sid}/{pid}")
        if not row.get("full_observation", {}).get("full_horizon"):
            unknown_full.append(f"{sid}/{pid}")
    matched = all(len(identities[sid]) == len(specs) and
                  len({item["initial_state_sha256"] for item in identities[sid]}) == 1 and
                  len({item["exogenous_stream_id"] for item in identities[sid]}) == 1
                  for sid in plan["scenario_ids"])
    if not matched or summary.get("matched_initial_state_and_stream") is not True:
        _error(errors, "matched_initial_state_and_stream")
    if summary.get("unknown_primary_pair_count") != len(unknown_primary):
        _error(errors, "unknown_primary_count")
    receipt = {"schema": SCHEMA, "passed": not errors, "errors": errors,
               "plan_sha256": _sha(plan_path), "plan_signature": plan.get("signature"),
               "summary_sha256": _sha(summary_path),
               "manifest_sha256": _sha(plan["manifest_path"]),
               "preference_sha256": _sha(plan["preference_path"]),
               "source_freeze_sha256": _sha(freeze_path),
               "registration_sha256": _sha(registration_path),
               "audit_source_sha256": _sha(__file__),
               "physical_inventory_checked_files": inventory_count,
               "registered_pair_count": len(expected), "observed_pair_count": len(rows),
               "matched_initial_state_and_stream": matched,
               "unknown_primary_pair_ids": sorted(unknown_primary),
               "unknown_full_pair_ids": sorted(unknown_full),
               "target_observed_count_by_window": observed_counts,
               "attempts": attempts_inventory,
               "final_blind_opened": False}
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with receipt_path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return receipt


def audit_generation(run_dir, generation):
    """Convenience entry point for an actually finished search generation."""
    run = Path(run_dir)
    folder = run / f"search/generation_{generation}"
    summaries = sorted((folder / "results").glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("No generation summary exists")
    return audit_batch(folder / "registered_plan.json", summaries[-1],
                       run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json",
                       folder / "AUDIT.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--generation", type=int, required=True)
    args = parser.parse_args()
    receipt = audit_generation(args.run_dir, args.generation)
    print(json.dumps({"passed": receipt["passed"], "errors": receipt["errors"],
                      "registered_pair_count": receipt["registered_pair_count"],
                      "unknown_primary_pair_ids": receipt["unknown_primary_pair_ids"]},
                     ensure_ascii=False))
    if not receipt["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
