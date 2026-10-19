"""Post-run, fail-closed review of the separately bound mode-v2 train batch.

The generic v5 evidence audit expects the original offline-search registration
schema and its frozen search.py source hash. A clearly marked compatibility
registration supplies only that legacy adapter field. The actual controller
registration and source hashes remain in MODE_V2_REGISTRATION.json and are
verified before every stage. No solver or old batch is run by this module.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.policy_search.audit import audit_batch
from tools.policy_search.mode_v2 import (MODE_DETECTOR_ID,
                                         P0_SOLVER_RESIDUAL_TOL_KW,
                                         observed_mode_v2)
from tools.policy_search.mode_v2_batch import V2_IDS, verify_registration
from tools.policy_search.policy import _observed_mode
from tools.policy_search.preference_v2 import analyze_audited_batch
from tools.policy_search.score_recheck import recheck_batch


SCHEMA = "policy_search.mode_v2_review.v1"
COMPAT_NAME = "SEARCH_REGISTRATION_AUDIT_COMPAT.json"
REVIEW_SOURCES = (
    "tools/policy_search/mode_v2_review.py",
    "tools/policy_search/audit.py",
    "tools/policy_search/score_recheck.py",
    "tools/policy_search/preference_v2.py",
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


def _new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def _paths(run_dir):
    run = Path(run_dir).resolve()
    folder = run / "search/mode_v2_train"
    return run, folder


def build_compat_registration(run_dir):
    """Construct, but do not write, the explicit generic-audit adapter."""
    run, folder = _paths(run_dir)
    sidecar, plan = verify_registration(run)
    frozen = _read(run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json")
    search_hash = _sha(ROOT / "tools/policy_search/search.py")
    if frozen.get("source_sha256", {}).get("tools/policy_search/search.py") != search_hash:
        raise ValueError("Legacy audit search source freeze changed")
    value = {
        "schema": "policy_search.offline_generation.v1",
        "generation": "mode_v2_train_generic_audit_adapter",
        "audit_compatibility_only": True,
        "source_sha256": search_hash,
        "source_sha256_role": "legacy generic audit check only; NOT the mode-v2 policy source",
        "actual_registration_source": "tools/policy_search/mode_v2_batch.py",
        "actual_registration_source_sha256": sidecar["source_sha256"]["tools/policy_search/mode_v2_batch.py"],
        "actual_mode_v2_registration_sha256": _sha(folder / "MODE_V2_REGISTRATION.json"),
        "actual_mode_v2_registration_signature": sidecar["signature"],
        "review_source_sha256": {name: _sha(ROOT / name) for name in REVIEW_SOURCES},
        "source_freeze_sha256": _sha(run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"),
        "preference_v2_revision_sha256": _sha(run / "design/PREFERENCE_V2_SOURCE_FREEZE.json"),
        "plan_sha256": _sha(folder / "registered_plan.json"),
        "plan_signature": plan["signature"],
        "scenario_ids": plan["scenario_ids"],
        "policy_specs": plan["policy_specs"],
        "manifest_sha256": plan["manifest_sha256"],
        "preference_sha256": plan["preference_sha256"],
        "registered_pair_count": 30,
        "comparison_phase": "train",
        "future_event_input_online": False,
        "final_blind_opened": False,
    }
    if plan["phase"] != "train" or plan["registered_pair_count"] != 30:
        raise ValueError("Mode v2 review expects exact 30-pair train plan")
    value["signature"] = digest(value)
    return value


def verify_compat_registration(run_dir, compat):
    """Reject changed sidecar, registered plan, review source or adapter."""
    if compat.get("schema") != "policy_search.offline_generation.v1" or not compat.get("audit_compatibility_only"):
        raise ValueError("Compatibility registration schema differs")
    if compat.get("signature") != digest({k: v for k, v in compat.items() if k != "signature"}):
        raise ValueError("Compatibility registration signature changed")
    expected = build_compat_registration(run_dir)
    if compat != expected:
        raise ValueError("Compatibility registration differs from bound sidecar/source/plan")
    return expected


def prepare(run_dir):
    """Freeze the generic-audit adapter before mode-v2 outcome generation."""
    _, folder = _paths(run_dir)
    value = build_compat_registration(run_dir)
    _new_json(folder / COMPAT_NAME, value)
    return value


def precheck_terminal(plan, summary, journal_path):
    """Keep incomplete or in-flight batches from consuming immutable receipts."""
    ids = plan.get("scenario_ids", [])
    policies = [spec["policy_id"] for spec in plan.get("policy_specs", [])]
    expected = {(sid, pid) for sid in ids for pid in policies}
    if (len(ids) != 6 or len(policies) != 5 or len(expected) != 30 or
            plan.get("registered_pair_count") != 30):
        raise ValueError("Mode v2 terminal precheck requires exact 30 registered pairs")
    rows = summary.get("results", [])
    actual = [(row.get("scenario_id"), row.get("policy_id")) for row in rows]
    if (summary.get("plan_signature") != plan.get("signature") or
            summary.get("registered_pair_count") != 30 or
            summary.get("recorded_pair_count") != 30 or
            summary.get("terminal_pair_count") != 30 or
            len(rows) != 30 or len(set(actual)) != 30 or set(actual) != expected or
            any(row.get("terminal") is not True for row in rows)):
        raise ValueError("Mode v2 audit requires 30 exact terminal pairs")
    journal_path = Path(journal_path)
    if not journal_path.is_file():
        raise ValueError("Mode v2 audit requires runner attempt journal")
    started, finished = Counter(), Counter()
    for lineno, line in enumerate(journal_path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Malformed runner journal line {lineno}") from error
        key = (event.get("scenario_id"), event.get("policy_id"), event.get("attempt"))
        if (event.get("event") not in ("started", "finished") or
                (key[0], key[1]) not in expected or
                any(not isinstance(item, str) or not item for item in key)):
            raise ValueError(f"Unexpected runner journal event at line {lineno}")
        (started if event["event"] == "started" else finished)[key] += 1
    if not started or started != finished or any(count != 1 for count in started.values()):
        raise ValueError("Mode v2 runner journal is in-flight or duplicated")
    if {(sid, pid) for sid, pid, _ in started} != expected:
        raise ValueError("Mode v2 runner journal lacks a registered pair")
    return {"registered_pair_count": 30, "terminal_pair_count": 30,
            "attempt_count": sum(started.values())}


def audit_mode_decisions(policy_id, decisions):
    """Recompute mode from only current legal features and past mode memory."""
    if policy_id not in ("prepare_only", "recovery_only", *V2_IDS):
        raise ValueError("Unsupported mode-policy trace identity")
    revised = policy_id in V2_IDS
    previous = False
    counts = Counter()
    for decision in decisions:
        features = decision["features"]
        diag = decision["policy_diagnostic"]
        mode = observed_mode_v2(features, previous) if revised else _observed_mode(features, previous)
        if diag.get("feedback_mode") != mode:
            raise ValueError("Logged feedback mode differs from policy mode detector")
        if revised:
            if diag.get("mode_detector_id") != MODE_DETECTOR_ID:
                raise ValueError("v2 mode_detector_id missing or changed")
            if diag.get("mode_p0_solver_residual_tol_kW") != P0_SOLVER_RESIDUAL_TOL_KW:
                raise ValueError("v2 mode threshold diagnostic differs")
        elif "mode_detector_id" in diag:
            raise ValueError("Frozen v1 control carries v2 mode_detector_id")
        previous |= mode == "disturbed"
        counts[mode] += 1
    return {mode: counts[mode] for mode in ("normal", "disturbed", "recovery")
            if counts[mode]}


def _verified_context(run_dir):
    run, folder = _paths(run_dir)
    sidecar, plan = verify_registration(run)
    compat = _read(folder / COMPAT_NAME)
    verify_compat_registration(run, compat)
    if compat["actual_mode_v2_registration_signature"] != sidecar["signature"]:
        raise ValueError("Mode v2 sidecar/compat signature differs")
    return run, folder, sidecar, plan, compat


def _latest_summary(folder):
    paths = sorted((folder / "results").glob("summary_*.json"))
    if not paths:
        raise FileNotFoundError("Mode v2 train batch has no result summary")
    return paths[-1]


def audit(run_dir):
    run, folder, _, plan, _ = _verified_context(run_dir)
    summary_path = _latest_summary(folder)
    precheck_terminal(plan, _read(summary_path), folder / "results/attempts.jsonl")
    receipt = audit_batch(folder / "registered_plan.json", summary_path,
                          run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json",
                          folder / "AUDIT.json",
                          registration_path=folder / COMPAT_NAME)
    if not receipt["passed"]:
        raise ValueError("Original evidence audit failed; receipt retained")
    return {"audit_passed": True, "registered_pair_count": 30,
            "unknown_primary_pair_count": len(receipt["unknown_primary_pair_ids"])}


def recheck(run_dir):
    _, folder, _, plan, _ = _verified_context(run_dir)
    summary_path = _latest_summary(folder)
    precheck_terminal(plan, _read(summary_path), folder / "results/attempts.jsonl")
    receipt = recheck_batch(folder)
    if not receipt["passed"]:
        raise ValueError("Original score recheck failed; receipt retained")
    return {"score_recheck_passed": True,
            "recomputed_pair_count": receipt["recomputed_pair_count"],
            "unknown_primary_pair_count": len(receipt["unknown_primary_pair_ids"])}


def _review_mode_traces(folder, plan, summary):
    traces = []
    for row in summary["results"]:
        pid = row["policy_id"]
        if pid not in ("prepare_only", "recovery_only", *V2_IDS):
            continue
        pair_dir = folder / "results/pairs" / digest([row["scenario_id"], pid])
        attempts = sorted(p for p in pair_dir.glob("attempt_*") if p.is_dir())
        if not attempts:
            raise ValueError("Mode trace attempt missing")
        latest = attempts[-1]
        if _read(latest / "result.json") != row:
            raise ValueError("Mode trace result differs from audited summary")
        raw_path = latest / row["trajectory_path"]
        if _sha(raw_path) != row["trajectory_sha256"]:
            raise ValueError("Mode trace raw SHA differs")
        with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
            raw = json.load(stream)
        counts = audit_mode_decisions(pid, raw["decisions"])
        traces.append({"scenario_id": row["scenario_id"], "policy_id": pid,
                       "status": row["status"], "raw_sha256": row["trajectory_sha256"],
                       "result_sha256": _sha(latest / "result.json"),
                       "decision_count": len(raw["decisions"]),
                       "mode_counts": counts})
    if len(traces) != 4 * len(plan["scenario_ids"]):
        raise ValueError("Mode v1/v2 control trace denominator differs")
    return traces


def analyze(run_dir):
    run, folder, sidecar, plan, compat = _verified_context(run_dir)
    summary_path = _latest_summary(folder)
    summary = _read(summary_path)
    precheck_terminal(plan, summary, folder / "results/attempts.jsonl")
    audit_path = folder / "AUDIT.json"
    recheck_path = folder / "SCORE_RECHECK.json"
    audit_receipt, recheck_receipt = _read(audit_path), _read(recheck_path)
    if (audit_receipt.get("passed") is not True or audit_receipt.get("errors") or
            audit_receipt.get("registration_sha256") != _sha(folder / COMPAT_NAME) or
            audit_receipt.get("plan_sha256") != _sha(folder / "registered_plan.json") or
            audit_receipt.get("summary_sha256") != _sha(summary_path) or
            recheck_receipt.get("passed") is not True or recheck_receipt.get("errors") or
            recheck_receipt.get("audit_sha256") != _sha(audit_path) or
            recheck_receipt.get("recomputed_pair_count") != 30):
        raise ValueError("Mode v2 analysis requires passing bound audit and 30-pair recheck")
    traces = _review_mode_traces(folder, plan, summary)
    analysis = analyze_audited_batch(
        folder / "registered_plan.json", summary_path, audit_path,
        run / "design/PREFERENCE_V2_SOURCE_FREEZE.json",
        run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json")
    analysis_path = folder / "ANALYSIS_PREFERENCE_V2.json"
    review_path = folder / "MODE_V2_REVIEW.json"
    if analysis_path.exists() or review_path.exists():
        raise FileExistsError("Mode v2 analysis/review receipt is immutable")
    _new_json(analysis_path, analysis)
    value = {
        "schema": SCHEMA, "passed": True,
        "interpretation": "Observed matched train-root policy comparison; no clinical calibration or final-blind claim",
        "actual_registration_sidecar_sha256": _sha(folder / "MODE_V2_REGISTRATION.json"),
        "actual_registration_sidecar_signature": sidecar["signature"],
        "audit_compat_registration_sha256": _sha(folder / COMPAT_NAME),
        "audit_compat_registration_signature": compat["signature"],
        "plan_signature": plan["signature"],
        "plan_sha256": _sha(folder / "registered_plan.json"),
        "summary_sha256": _sha(summary_path),
        "audit_sha256": _sha(audit_path),
        "score_recheck_sha256": _sha(recheck_path),
        "preference_analysis_sha256": _sha(analysis_path),
        "review_source_sha256": _sha(__file__),
        "registered_pair_count": 30,
        "status_counts": dict(Counter(row["status"] for row in summary["results"])),
        "unknown_primary_pair_ids": audit_receipt["unknown_primary_pair_ids"],
        "unknown_full_pair_ids": audit_receipt["unknown_full_pair_ids"],
        "v1_v2_mode_traces": traces,
        "p0_pareto_ids": analysis["p0_pareto_ids"],
        "incomparable_due_to_support": analysis["incomparable_due_to_support"],
        "final_blind_opened": False,
    }
    _new_json(review_path, value)
    return {"review_passed": True, "analysis": str(analysis_path),
            "review": str(review_path), "p0_pareto_ids": analysis["p0_pareto_ids"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "verify", "audit", "recheck", "analyze"))
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    if args.operation == "prepare":
        result = prepare(args.run_dir)
        output = {"compat_signature": result["signature"],
                  "registered_pair_count": result["registered_pair_count"]}
    elif args.operation == "verify":
        _, _, sidecar, plan, compat = _verified_context(args.run_dir)
        output = {"verified": True, "plan_signature": plan["signature"],
                  "sidecar_signature": sidecar["signature"],
                  "compat_signature": compat["signature"]}
    elif args.operation == "audit":
        output = audit(args.run_dir)
    elif args.operation == "recheck":
        output = recheck(args.run_dir)
    else:
        output = analyze(args.run_dir)
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
