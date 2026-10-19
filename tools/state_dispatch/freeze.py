"""Freeze one audited v2 policy before registering same-background holdout.

This protocol only reads development evidence and manifest holdout IDs. It
does not execute a controller or inspect any holdout outcome.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.state_dispatch import closed_loop_v2 as runner

SCHEMA = "state_dispatch.preconfirmation_policy_freeze.v1"
BASELINES = [{"policy_id": "secure_milp", "kind": "secure"},
             {"policy_id": "priority_milp", "kind": "priority"}]
_sha, _read, _new_json = runner._sha, runner._read, runner._new_json


def _saved_audit_verifier():
    from tools.policy_redesign import select_develop
    function = select_develop.verify_saved_audit
    namespace = dict(function.__globals__)
    namespace.update(verify_batch=runner.verify_batch, AUDIT_SCHEMA=runner.AUDIT_SCHEMA)
    return runner._isolated(function, namespace), Path(select_develop.__file__).resolve()


def _holdout_ids(manifest, development_ids):
    ids = manifest.get("ids_by_cohort", {}).get("holdout")
    if (not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
            or any(not isinstance(sid, str) or not sid for sid in ids)
            or set(ids) & set(development_ids)):
        raise ValueError("Manifest holdout IDs missing, repeated or used in development")
    scenes = {scene["id"]: scene for scene in manifest.get("scenarios", [])}
    if any(sid not in scenes or scenes[sid].get("analysis_cohort") != "holdout"
           or scenes[sid].get("experiment_role") != "confirmation"
           or scenes[sid].get("is_final_blind") is not False for sid in ids):
        raise ValueError("Holdout must be declared same-background confirmation, never sealed final")
    return list(ids)


def _development_evidence(development_plan, development_audit, selected_spec):
    plan_path, audit_path = Path(development_plan).resolve(), Path(development_audit).resolve()
    plan, manifest = runner.verify_batch(plan_path)
    if plan.get("schema") != runner.SCHEMA or plan.get("phase") != "development":
        raise ValueError("Current v2 development plan required")
    if (not isinstance(selected_spec, dict) or selected_spec.get("kind") != "state_dispatch"
            or selected_spec.get("policy_id") in {"secure_milp", "priority_milp"}
            or selected_spec not in plan["policy_specs"]):
        raise ValueError("Selected specification differs from the registered development policy")
    if any(baseline not in plan["policy_specs"] for baseline in BASELINES):
        raise ValueError("Both exact frozen baselines required in development")
    current_sources = runner._source_binding()
    if current_sources != plan.get("source_sha256"):
        raise ValueError("Current v2 source bytes differ from development")
    mp, pref = Path(plan["manifest_path"]).resolve(), Path(plan["preference_path"]).resolve()
    if (_sha(mp) != plan.get("manifest_sha256") or _sha(pref) != plan.get("preference_sha256")
            or manifest.get("signature") != plan.get("manifest_signature")
            or manifest.get("policy_search_final_12_backgrounds_unopened") is not True):
        raise ValueError("Manifest, preference or final-background boundary changed")
    holdout_ids = _holdout_ids(manifest, plan["scenario_ids"])
    audit = _read(audit_path)
    if (audit.get("unknown_primary_pair_ids") != [] or audit.get("unknown_full_pair_ids") != []
            or audit.get("manifest_sha256") != plan["manifest_sha256"]
            or any(row.get("primary_full_horizon") is not True
                   or row.get("full_observation_horizon") is not True
                   for row in audit.get("pair_ledger", []))):
        raise ValueError("Complete development audit required; unknown windows remain unresolved")
    verify_saved, verifier_path = _saved_audit_verifier()
    # Existing implementation checks passed/schema/counts/summary SHA, every
    # registered pair and saved raw bytes. It does not rerun physical dynamics.
    verify_saved(plan_path, audit_path, "development")
    binding = {
        "development_plan_path": str(plan_path), "development_plan_sha256": _sha(plan_path),
        "development_plan_signature": plan["signature"],
        "development_audit_path": str(audit_path), "development_audit_sha256": _sha(audit_path),
        "development_summary_path": audit["summary_path"],
        "development_summary_sha256": audit["summary_sha256"],
        "development_scenario_ids": list(plan["scenario_ids"]),
        "registered_development_pair_count": plan["registered_pair_count"],
        "manifest_path": str(mp), "manifest_sha256": plan["manifest_sha256"],
        "manifest_signature": plan["manifest_signature"],
        "preference_path": str(pref), "preference_sha256": plan["preference_sha256"],
        "source_sha256": current_sources,
        "simulator_hash": plan["simulator_hash"],
        "execution_contract_id": plan["execution_contract_id"],
        "physical_input_inventory_sha256": plan["physical_input_inventory_sha256"],
        "holdout_scenario_ids": holdout_ids,
        "freeze_protocol_source_sha256": _sha(Path(__file__)),
        "saved_audit_verifier_source_sha256": _sha(verifier_path),
    }
    return binding, manifest


def freeze_policy(run, selected_spec, development_plan, development_audit, rationale, output_path):
    """Write once after complete current v2 development evidence is verified."""
    output = Path(output_path)
    if output.exists():
        raise FileExistsError("Use a new immutable policy-freeze receipt")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("A concrete development selection rationale is required")
    binding, _ = _development_evidence(development_plan, development_audit, selected_spec)
    receipt = {"schema": SCHEMA, "run": str(Path(run).resolve()),
        "selected_spec": copy.deepcopy(selected_spec), "rationale": rationale.strip(), **binding,
        "holdout_semantics": "Same seen backgrounds, newly registered perturbations; not new independent backgrounds",
        "final_blind_opened": False, "holdout_outcomes_read": False}
    receipt["signature"] = digest(receipt)
    _new_json(output, receipt)
    return receipt


def _verify_receipt(receipt_path):
    path = Path(receipt_path).resolve()
    receipt = _read(path)
    if (receipt.get("schema") != SCHEMA or receipt.get("signature") != digest(
            {key: value for key, value in receipt.items() if key != "signature"})
            or receipt.get("final_blind_opened") is not False
            or receipt.get("holdout_outcomes_read") is not False):
        raise ValueError("Policy-freeze receipt signature or boundary differs")
    if receipt.get("source_sha256") != runner._source_binding():
        raise ValueError("Frozen v2 source bytes changed")
    binding, manifest = _development_evidence(receipt["development_plan_path"],
        receipt["development_audit_path"], receipt["selected_spec"])
    if any(receipt.get(key) != value for key, value in binding.items()):
        raise ValueError("Frozen development/source/input evidence changed")
    return receipt, manifest


def register_confirmation(receipt, plan_path, manifest_path, preference_path):
    """Register exact baselines plus frozen candidate, reading no holdout raw."""
    frozen, manifest = _verify_receipt(receipt)
    mp, pref = Path(manifest_path).resolve(), Path(preference_path).resolve()
    if (str(mp) != frozen["manifest_path"] or _sha(mp) != frozen["manifest_sha256"]
            or str(pref) != frozen["preference_path"] or _sha(pref) != frozen["preference_sha256"]):
        raise ValueError("Confirmation manifest or preference differs from frozen development")
    ids = _holdout_ids(manifest, frozen["development_scenario_ids"])
    return runner.register_batch(mp, plan_path, scenario_ids=ids,
        policy_specs=copy.deepcopy(BASELINES) + [copy.deepcopy(frozen["selected_spec"])],
        preference_path=pref, phase="confirmation")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--run", type=Path, required=True)
    freeze.add_argument("--selected-policy-id", required=True)
    freeze.add_argument("--development-plan", "--plan", dest="development_plan", type=Path, required=True)
    freeze.add_argument("--development-audit", "--audit", dest="development_audit", type=Path, required=True)
    freeze.add_argument("--rationale", required=True)
    freeze.add_argument("--out", "--output-path", dest="out", type=Path, required=True)
    confirm = sub.add_parser("register-confirmation")
    confirm.add_argument("--receipt", type=Path, required=True)
    confirm.add_argument("--plan", type=Path, required=True)
    confirm.add_argument("--manifest", type=Path, required=True)
    confirm.add_argument("--preference", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == "freeze":
        specs = [spec for spec in _read(args.development_plan).get("policy_specs", [])
                 if spec.get("policy_id") == args.selected_policy_id]
        if len(specs) != 1:
            raise ValueError("Selected policy ID is not unique in development")
        result = freeze_policy(args.run, specs[0], args.development_plan,
            args.development_audit, args.rationale, args.out)
        value = {"signature": result["signature"], "selected_policy_id": result["selected_spec"]["policy_id"]}
    else:
        result = register_confirmation(args.receipt, args.plan, args.manifest, args.preference)
        value = {"plan_signature": result["signature"], "registered_pair_count":
                 result.get("registered_pair_count", len(result["scenario_ids"]) * len(result["policy_specs"]))}
    print(json.dumps(value, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
