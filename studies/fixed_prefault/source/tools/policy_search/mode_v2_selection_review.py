"""Fail-closed review of the independent corrected-mode selection batch.

The generic v5 audit's search.py registration check is satisfied by a marked
compatibility adapter only. The actual mode-v2 selection sidecar and all new
source bytes are verified separately before audit, recheck and analysis.
This module neither runs a solver nor changes existing selection artifacts.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.policy_search.audit import audit_batch
from tools.policy_search.mode_v2_review import (COMPAT_NAME, _review_mode_traces,
                                                precheck_terminal)
from tools.policy_search.mode_v2_selection import (FOLDER,
                                                   verify_registration as verify_selection)
from tools.policy_search.preference_v2 import analyze_audited_batch
from tools.policy_search.score_recheck import recheck_batch


SCHEMA = "policy_search.mode_v2_selection_review.v1"
REVIEW_SOURCES = (
    "tools/policy_search/mode_v2_selection_review.py",
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
    return run, run / FOLDER


def build_compat_registration(run_dir):
    run, folder = _paths(run_dir)
    sidecar, plan = verify_selection(run)
    freeze_path = run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"
    frozen = _read(freeze_path)
    search_sha = _sha(ROOT / "tools/policy_search/search.py")
    if frozen.get("source_sha256", {}).get("tools/policy_search/search.py") != search_sha:
        raise ValueError("Legacy audit search source freeze changed")
    value = {
        "schema": "policy_search.offline_generation.v1",
        "generation": "mode_v2_selection_generic_audit_adapter",
        "audit_compatibility_only": True,
        "source_sha256": search_sha,
        "source_sha256_role": "legacy generic audit check only; NOT the corrected-mode policy source",
        "actual_registration_source": "tools/policy_search/mode_v2_selection.py",
        "actual_registration_source_sha256": sidecar["source_sha256"]["tools/policy_search/mode_v2_selection.py"],
        "actual_mode_v2_registration_sha256": _sha(folder / "MODE_V2_SELECTION_REGISTRATION.json"),
        "actual_mode_v2_registration_signature": sidecar["signature"],
        "review_source_sha256": {name: _sha(ROOT / name) for name in REVIEW_SOURCES},
        "source_freeze_sha256": _sha(freeze_path),
        "preference_v2_revision_sha256": _sha(run / "design/PREFERENCE_V2_SOURCE_FREEZE.json"),
        "plan_sha256": _sha(folder / "registered_plan.json"),
        "plan_signature": plan["signature"],
        "scenario_ids": plan["scenario_ids"],
        "main_selection_scenario_ids": sidecar["main_selection_scenario_ids"],
        "policy_specs": plan["policy_specs"],
        "manifest_sha256": plan["manifest_sha256"],
        "preference_sha256": plan["preference_sha256"],
        "registered_pair_count": plan["registered_pair_count"],
        "comparison_phase": "selection",
        "final_blind_opened": False,
    }
    if (plan["phase"] != "selection" or plan["registered_pair_count"] != 30 or
            plan["scenario_ids"] != sidecar["main_selection_scenario_ids"]):
        raise ValueError("Corrected-mode selection comparison plan differs")
    value["signature"] = digest(value)
    return value


def verify_compat_registration(run_dir, compat):
    if compat.get("schema") != "policy_search.offline_generation.v1" or not compat.get("audit_compatibility_only"):
        raise ValueError("Selection compatibility registration schema differs")
    if compat.get("signature") != digest({k: v for k, v in compat.items() if k != "signature"}):
        raise ValueError("Selection compatibility registration signature changed")
    if compat != build_compat_registration(run_dir):
        raise ValueError("Selection compatibility registration differs from sidecar/source/plan")
    return compat


def prepare(run_dir):
    _, folder = _paths(run_dir)
    value = build_compat_registration(run_dir)
    _new_json(folder / COMPAT_NAME, value)
    return value


def _verified_context(run_dir):
    run, folder = _paths(run_dir)
    sidecar, plan = verify_selection(run)
    compat = _read(folder / COMPAT_NAME)
    verify_compat_registration(run, compat)
    if compat["actual_mode_v2_registration_signature"] != sidecar["signature"]:
        raise ValueError("Selection sidecar/compat signature differs")
    return run, folder, sidecar, plan, compat


def _latest_summary(folder):
    paths = sorted((folder / "results").glob("summary_*.json"))
    if not paths:
        raise FileNotFoundError("Corrected-mode selection has no result summary")
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
        raise ValueError("Corrected-mode selection evidence audit failed; receipt retained")
    return {"audit_passed": True, "registered_pair_count": 30,
            "unknown_primary_pair_count": len(receipt["unknown_primary_pair_ids"])}


def recheck(run_dir):
    _, folder, _, plan, _ = _verified_context(run_dir)
    summary_path = _latest_summary(folder)
    precheck_terminal(plan, _read(summary_path), folder / "results/attempts.jsonl")
    receipt = recheck_batch(folder)
    if not receipt["passed"]:
        raise ValueError("Corrected-mode selection score recheck failed; receipt retained")
    return {"score_recheck_passed": True,
            "recomputed_pair_count": receipt["recomputed_pair_count"],
            "unknown_primary_pair_count": len(receipt["unknown_primary_pair_ids"])}


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
        raise ValueError("Corrected-mode selection analysis requires exact passing audit/recheck")
    traces = _review_mode_traces(folder, plan, summary)
    analysis = analyze_audited_batch(
        folder / "registered_plan.json", summary_path, audit_path,
        run / "design/PREFERENCE_V2_SOURCE_FREEZE.json",
        run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json")
    analysis_path = folder / "ANALYSIS_PREFERENCE_V2.json"
    review_path = folder / "MODE_V2_SELECTION_REVIEW.json"
    if analysis_path.exists() or review_path.exists():
        raise FileExistsError("Corrected-mode selection analysis/review is immutable")
    _new_json(analysis_path, analysis)
    value = {
        "schema": SCHEMA, "passed": True,
        "interpretation": "Observed matched held-out selection-root policy comparison; no final-blind claim",
        "actual_registration_sidecar_sha256": _sha(folder / "MODE_V2_SELECTION_REGISTRATION.json"),
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
        output = {"compat_signature": prepare(args.run_dir)["signature"],
                  "registered_pair_count": 30}
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
