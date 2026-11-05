"""Read-only numerical recheck of v5 scores against stored raw trajectories.

This uses the *same* published ``window_targets`` accounting function as the
runner. It verifies serialization, pair bookkeeping and score arithmetic;
it is not an independent validation of the physical simulator or tariffs.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.dataset_manifest import digest
from tools.control_improvement.branches import window_targets
from tools.control_improvement.ranking import TARGET_NAMES


SCHEMA = "policy_search.raw_score_recheck.v1"
SCORING_SOURCES = (
    "tools/control_improvement/branches.py",
    "tools/integrated_learning/truth.py",
    "tools/integrated_experiment/energy_metrics.py",
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


def _hash_object(value):
    data = json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def score_differences(stored, recomputed, field):
    """Describe exact, zero-tolerance differences, including null vs zero."""
    if not isinstance(stored, dict) or not isinstance(recomputed, dict):
        return [field + ": missing score record"]
    differences = []
    old_keys, new_keys = set(stored), set(recomputed)
    for key in sorted(new_keys - old_keys):
        differences.append(f"{field}: missing field {key}")
    for key in sorted(old_keys - new_keys):
        differences.append(f"{field}: unexpected field {key}")
    old_targets = stored.get("targets")
    new_targets = recomputed.get("targets")
    if (not isinstance(old_targets, list) or not isinstance(new_targets, list) or
            len(old_targets) != len(TARGET_NAMES) or len(new_targets) != len(TARGET_NAMES)):
        differences.append(f"{field}: expected {len(TARGET_NAMES)} recorded/recomputed targets")
    else:
        for index, (old, new) in enumerate(zip(old_targets, new_targets)):
            if old != new or (old is None) != (new is None):
                differences.append(
                    f"{field}.target_{index}({TARGET_NAMES[index]}): recorded={old!r}, "
                    f"recomputed={new!r}")
    for key in sorted(old_keys & new_keys - {"targets"}):
        if stored[key] != recomputed[key]:
            differences.append(f"{field}.{key}: recorded={stored[key]!r}, "
                               f"recomputed={recomputed[key]!r}")
    return differences


def _latest_result(pair_dir):
    attempts = sorted(p for p in pair_dir.glob("attempt_*") if p.is_dir())
    for attempt in reversed(attempts):
        path = attempt / "result.json"
        if path.is_file():
            return path
    raise FileNotFoundError("No result.json for registered pair: " + str(pair_dir))


def recheck_batch(batch_dir, *, receipt_path=None):
    """Recompute both 19-target windows for every registered terminal pair."""
    batch = Path(batch_dir).resolve()
    plan_path = batch / "registered_plan.json"
    audit_path = batch / "AUDIT.json"
    summaries = sorted((batch / "results").glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("Batch has no summary")
    summary_path = summaries[-1]
    receipt = Path(receipt_path).resolve() if receipt_path else batch / "SCORE_RECHECK.json"
    if receipt.exists():
        raise FileExistsError("Score recheck receipt is immutable")
    plan, audit, summary = (_read(path) for path in
                            (plan_path, audit_path, summary_path))
    if (audit.get("passed") is not True or audit.get("errors") or
            audit.get("plan_sha256") != _sha(plan_path) or
            audit.get("summary_sha256") != _sha(summary_path) or
            audit.get("plan_signature") != plan.get("signature") or
            audit.get("registered_pair_count") != plan.get("registered_pair_count")):
        raise ValueError("Score recheck requires an exact passing audit for this plan/summary")
    if (plan.get("source_sha256", {}).get(SCORING_SOURCES[0]) !=
            _sha(ROOT / SCORING_SOURCES[0])):
        raise ValueError("Registered window_targets implementation changed")
    if summary.get("plan_signature") != plan["signature"]:
        raise ValueError("Summary plan signature differs")
    manifest_path = Path(plan["manifest_path"])
    if _sha(manifest_path) != plan["manifest_sha256"]:
        raise ValueError("Registered manifest bytes changed")
    manifest = _read(manifest_path)
    expected = {(sid, spec["policy_id"]) for sid in plan["scenario_ids"]
                for spec in plan["policy_specs"]}
    rows = summary.get("results", [])
    row_keys = [(row.get("scenario_id"), row.get("policy_id")) for row in rows]
    if (len(rows) != len(expected) or len(set(row_keys)) != len(rows) or
            set(row_keys) != expected):
        raise ValueError("Summary pair set differs from registered cross-product")
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    checked = []
    errors = []
    unknown_primary = []
    unknown_full = []
    for row in rows:
        sid, pid = row["scenario_id"], row["policy_id"]
        tag = f"{sid}/{pid}"
        try:
            result_path = _latest_result(batch / "results/pairs" / digest([sid, pid]))
            if _read(result_path) != row:
                raise ValueError("Summary row differs from raw attempt result.json")
            raw_path = result_path.parent / row["trajectory_path"]
            if raw_path.name != "trajectory.json.gz" or _sha(raw_path) != row["trajectory_sha256"]:
                raise ValueError("Registered raw trajectory SHA differs")
            with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
                payload = json.load(
                    stream, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
            if (payload.get("scenario", {}).get("id") != sid or
                    payload.get("summary", {}).get("status") != row.get("status")):
                raise ValueError("Raw trajectory scenario/status differs from result")
            primary = window_targets(payload, 60.0, 360.0, env)
            full = window_targets(payload, 0.0, 600.0, env)
            differences = (score_differences(row.get("primary_score"), primary, "primary_score") +
                           score_differences(row.get("full_observation"), full, "full_observation"))
            if differences:
                errors.extend(f"{tag}: {difference}" for difference in differences)
            if not primary["full_horizon"]:
                unknown_primary.append(tag)
            if not full["full_horizon"]:
                unknown_full.append(tag)
            checked.append({
                "scenario_id": sid, "policy_id": pid,
                "result_sha256": _sha(result_path),
                "trajectory_sha256": _sha(raw_path),
                "recomputed_primary_score_sha256": _hash_object(primary),
                "recomputed_full_observation_sha256": _hash_object(full),
                "primary_full_horizon": primary["full_horizon"],
                "full_full_horizon": full["full_horizon"],
                "difference_count": len(differences),
            })
        except Exception as error:
            errors.append(f"{tag}: recheck_exception {type(error).__name__}: {error}")
    if sorted(unknown_primary) != sorted(audit.get("unknown_primary_pair_ids", [])):
        errors.append("Recomputed primary unknown set differs from passing audit")
    if sorted(unknown_full) != sorted(audit.get("unknown_full_pair_ids", [])):
        errors.append("Recomputed full unknown set differs from passing audit")
    value = {
        "schema": SCHEMA, "passed": not errors, "errors": errors,
        "method": "Recompute original window_targets on stored gzip raw payload with exact equality; "
                  "same score code/physical model, not independent physical or tariff validation",
        "plan_signature": plan["signature"], "plan_sha256": _sha(plan_path),
        "summary_sha256": _sha(summary_path), "audit_sha256": _sha(audit_path),
        "manifest_sha256": _sha(manifest_path), "source_sha256": _sha(__file__),
        "scoring_source_sha256": {name: _sha(ROOT / name) for name in SCORING_SOURCES},
        "registered_pair_count": len(expected), "recomputed_pair_count": len(checked),
        "unknown_primary_pair_ids": sorted(unknown_primary),
        "unknown_full_pair_ids": sorted(unknown_full),
        "unknown_is_null_not_zero": True, "target_count_per_window": len(TARGET_NAMES),
        "pairs": checked,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    result = recheck_batch(args.batch_dir, receipt_path=args.receipt)
    print(json.dumps({key: result[key] for key in
                      ("passed", "registered_pair_count", "recomputed_pair_count",
                       "unknown_primary_pair_ids", "errors")}, ensure_ascii=False))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
