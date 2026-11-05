"""Preregister v6 selection, freeze its outcome, then open development.

Only saved, raw-rechecked *train* comparisons choose feedback candidates.
The six-by-five new selection panel is then registered before any of its
outcomes. Development registration accepts only a signed selection freeze and
reuses its complete comparison roster on disjoint six-by-five roots. This
module never changes the physical runner or online policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.policy_redesign.closed_loop import (register_batch, verify_batch,
                                               _model_binding, _source_binding)
from tools.policy_redesign.neural import model_artifact_binding
from tools.policy_redesign.policy import zero_theta
from tools.policy_redesign.search import priority_key


AUDIT_SCHEMA = "policy_redesign.v6.matched_batch_audit.v1"
PREFERENCE_SCHEMA = "policy_redesign.v6.preregistered_preference.v1"
SELECTION_PREFERENCE_SCHEMA = "policy_redesign.v6.preregistered_preference.v2"
SELECTION_PRIORITY_ORDER_V2 = (
    "unknown scenario count and validity",
    "new P0 failure cases",
    "worst individual P0 failure-minute increase",
    "worst individual P0 electric ENS increase",
    "worst individual P0 heating ENS increase",
    "worst individual P0 cooling ENS increase",
    "total P0 electric, heating and cooling ENS deltas separately",
    "new minus resolved P0 failure cases",
    "total P0 failure-minute delta",
    "P0 maximum-temperature and other original P0 targets",
    "P1, P2 and P3 original targets",
    "modeled cost",
)
BASIS_SCHEMA = "policy_redesign.v6.selection_basis.v1"
FREEZE_SCHEMA = "policy_redesign.v6.selection_freeze.v1"
DEVELOPMENT_SCHEMA = "policy_redesign.v6.development_review.v1"
DEFAULT_OLD_GUARD = "outputs/v43_reliability_first_20261015_run01/learning/full_nn_fit01"
DEFAULT_OLD_NN = "outputs/v5_policy_search_20261015_run01/models/gru_agg1_blend_overload_seed5102"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def signed(body):
    """Return a tamper-evident JSON body using the project's canonical digest."""
    return dict(body, signature=digest(body))


def _checked(path, schema):
    value = _read(path)
    if (value.get("schema") != schema or
            value.get("signature") != digest({k: v for k, v in value.items()
                                              if k != "signature"})):
        raise ValueError("Signed selection/development artifact differs")
    return value


def _selection_preference(path):
    path = Path(path).resolve()
    pref = _read(path)
    if (pref.get("schema") != SELECTION_PREFERENCE_SCHEMA or
            pref.get("registered_after_train_before_selection_outcomes") is not True or
            pref.get("selection_rule_id") != "v2_p0_noncompensation" or
            pref.get("priority_order") != list(SELECTION_PRIORITY_ORDER_V2) or
            pref.get("final_12_backgrounds_unopened") is not True or
            not pref.get("reference_preference_v1_path") or
            not pref.get("reference_preference_v1_sha256")):
        raise ValueError("Registered v2 P0 noncompensation preference is absent or changed")
    v1_path = (path.parent / pref["reference_preference_v1_path"]).resolve()
    if (not v1_path.is_file() or _sha(v1_path) != pref["reference_preference_v1_sha256"] or
            _read(v1_path).get("schema") != PREFERENCE_SCHEMA):
        raise ValueError("Reference v1 research preference differs")
    return pref


def selection_priority_key_v2(metric):
    """Training-informed, preregistered selection order; no P0 event/ENS exchange."""
    n = metric["registered_case_count"]
    if n <= 0:
        raise ValueError("Cannot rank an empty paired panel")
    unknown = len(metric["unknown_case_ids"])
    if not metric["complete_paired_case_count"]:
        return (unknown, n + 1, n + 1)
    totals = metric["total_delta_by_target"]
    worst_temp = max(0.0, *(case["delta_targets"][14]
                           for case in metric["individual_cases"].values()
                           if case["complete_paired_window"]))
    return (
        unknown,
        metric["new_P0_failure_cases"],
        metric["largest_case_P0_failure_minutes_increase"],
        metric["largest_case_P0_e_ENS_increase"],
        metric["largest_case_P0_h_ENS_increase"],
        metric["largest_case_P0_c_ENS_increase"],
        metric["P0_e_ENS_kWh_delta"], metric["P0_h_ENS_kWh_delta"],
        metric["P0_c_ENS_kWh_delta"],
        metric["new_P0_failure_cases"] - metric["resolved_P0_failure_cases"],
        metric["P0_failure_minutes_total_delta"],
        worst_temp, totals[14],
        *(totals[i] for i in (*range(5, 14), 15, 16, 17)),
        totals[18],
    )


def phase_panel(manifest, phase):
    """Exact independent six-condition, six-root, five-pressure new-data panel."""
    if phase not in {"selection", "development"}:
        raise ValueError("Only independent selection/development panels are accepted")
    scenes = [s for s in manifest["scenarios"] if s.get("experiment_role") == phase]
    if len(scenes) != 30:
        raise ValueError("New phase requires exactly 30 registered scenarios")
    if any(s.get("is_final_blind") is not False for s in scenes):
        raise ValueError("New phase includes sealed final-blind data")
    roots = {}
    conditions = {}
    for scene in scenes:
        root, condition = scene.get("root_family_id"), scene.get("condition_id")
        if not root or not condition or not scene.get("id"):
            raise ValueError("New phase has missing root, condition or scenario identity")
        roots.setdefault(root, []).append(scene)
        conditions.setdefault(condition, set()).add(root)
    if len(roots) != 6 or len(conditions) != 6 or any(len(v) != 1 for v in conditions.values()):
        raise ValueError("New phase must contain six conditions and six separate roots")
    if any(len(rows) != 5 or
           {s.get("pressure_point_index") for s in rows} != set(range(5)) or
           len({s["condition_id"] for s in rows}) != 1 for rows in roots.values()):
        raise ValueError("Each new root needs one complete five-pressure panel")
    ids = [s["id"] for s in sorted(scenes, key=lambda s:
        (s["condition_id"], s["root_family_id"], s["pressure_point_index"]))]
    if len(set(ids)) != 30:
        raise ValueError("New phase has repeated scenario IDs")
    return ids


def _broad_training_panel(plan, manifest):
    ids = plan.get("scenario_ids", [])
    if (plan.get("phase") != "train" or len(ids) != 150 or
            len(set(ids)) != 150):
        raise ValueError("Feedback candidates require exactly 150 audited broad train scenes")
    scenes = {s["id"]: s for s in manifest["scenarios"]}
    roots = {}
    conditions = {}
    for sid in ids:
        scene = scenes.get(sid)
        if (scene is None or scene.get("experiment_role") != "train" or
                scene.get("is_final_blind")):
            raise ValueError("Broad training includes nontrain or blind scenario")
        roots.setdefault(scene["root_family_id"], []).append(scene)
        conditions.setdefault(scene["condition_id"], set()).add(scene["root_family_id"])
    if (len(roots) != 30 or len(conditions) != 6 or
            any(len(root_ids) != 5 for root_ids in conditions.values()) or any(
            len(rows) != 5 or
            {s.get("pressure_point_index") for s in rows} != set(range(5)) or
            len({s["condition_id"] for s in rows}) != 1
            for rows in roots.values())):
        raise ValueError("Broad training needs 30 roots in six conditions, each complete five-pressure")


def verify_saved_audit(plan_path, audit_path, phase):
    """Check a saved raw audit against current plan, summary and raw bytes.

    This validates that the existing full ``audit_batch`` recheck was passed;
    it does not claim an independent physical score implementation.
    """
    plan_path = Path(plan_path).resolve()
    audit_path = Path(audit_path).resolve()
    plan, _ = verify_batch(plan_path)
    audit = _read(audit_path)
    expected = {(sid, spec["policy_id"]) for sid in plan["scenario_ids"]
                for spec in plan["policy_specs"]}
    if (plan.get("phase") != phase or audit.get("schema") != AUDIT_SCHEMA or
            audit.get("phase") != phase or audit.get("passed") is not True or
            audit.get("errors") not in (None, []) or
            audit.get("matched_initial_state_and_stream") is not True or
            audit.get("plan_signature") != plan["signature"] or
            Path(audit.get("plan_path", "")).resolve() != plan_path or
            audit.get("plan_sha256") != _sha(plan_path) or
            audit.get("registered_pair_count") != len(expected) or
            audit.get("terminal_pair_count") != len(expected) or
            audit.get("recomputed_pair_count") != len(expected)):
        raise ValueError("Saved raw audit/plan integrity or denominator differs")
    summary = Path(audit.get("summary_path", ""))
    if not summary.is_file() or audit.get("summary_sha256") != _sha(summary):
        raise ValueError("Saved raw audit summary SHA differs")
    indexed = {}
    raw_index = []
    for row in audit.get("pair_ledger", []):
        key = (row.get("scenario_id"), row.get("policy_id"))
        if key in indexed or key not in expected:
            raise ValueError("Saved raw audit pair ledger differs")
        indexed[key] = row
        if (row.get("terminal") is not True or row.get("score_recomputed") is not True or
                row.get("binding_passed") is not True):
            raise ValueError("Saved raw audit pair ledger lacks terminal checked raw")
        attempts = row.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ValueError("Saved raw audit pair ledger lacks attempts")
        latest = attempts[-1]
        if not latest.get("raw_path") or not latest.get("raw_sha256"):
            raise ValueError("Saved raw audit pair ledger lacks latest raw SHA")
        for attempt in attempts:
            path, sha = attempt.get("raw_path"), attempt.get("raw_sha256")
            if path is None:
                continue
            if not sha or not Path(path).is_file() or _sha(path) != sha:
                raise ValueError("Saved raw audit raw SHA differs")
            raw_index.append({"scenario_id": key[0], "policy_id": key[1],
                              "attempt": attempt.get("attempt"), "raw_sha256": sha,
                              "raw_path": path})
    if set(indexed) != expected:
        raise ValueError("Saved raw audit pair ledger denominator differs")
    return raw_index


def choose_training_feedback(plan, audit):
    """One consequence-ranked feedback candidate per legal representation."""
    expected_n = len(plan["scenario_ids"])
    choices = []
    for family in ("port_residual", "source_allocation"):
        candidates = []
        for spec in plan["policy_specs"]:
            if spec.get("kind") != "v6_feedback" or spec.get("family") != family:
                continue
            pid = spec["policy_id"]
            metric = audit["policy_comparisons"][pid]["primary"]
            if metric["registered_case_count"] != expected_n:
                raise ValueError("Training candidate lacks common registered denominator")
            candidates.append((tuple(priority_key(metric)), pid, spec, metric))
        if candidates:
            key, pid, spec, metric = min(candidates, key=lambda row: (row[0], row[1]))
            choices.append({"source_policy_id": pid, "family": family,
                            "theta": spec["theta"], "priority_key": list(key),
                            "complete_paired_case_count": metric["complete_paired_case_count"],
                            "unknown_case_ids": metric["unknown_case_ids"]})
    if not choices:
        raise ValueError("Audited broad train batch has no v6 feedback candidate")
    return choices


def _model_path(directory):
    path = Path(directory)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_dir():
        raise FileNotFoundError("Frozen comparison model directory missing: " + str(path))
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _formal_cuda_model(directory, broad_plan, broad_plan_path):
    path = Path(directory)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    report = _read(path / "fit_report.json")
    source = _read(path / "source_audit.json")
    metadata = _read(path / "metadata.json")
    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("Formal model best.pt/last.pt/fit_report.json artifact binding absent")
    for name in ("best.pt", "last.pt", "fit_report.json"):
        file = path / name
        if not file.is_file() or artifacts.get(name) != _sha(file):
            raise ValueError("Formal model artifact SHA differs: " + name)
    binding = model_artifact_binding(path)
    broad_plan_path = Path(broad_plan_path).resolve()
    source_ids = source.get("registered_train_panel_scenario_ids")
    broad_ids = broad_plan["scenario_ids"]
    if (broad_plan.get("phase") != "train" or len(broad_ids) != 150 or
            len(set(broad_ids)) != 150):
        raise ValueError("Formal CUDA model requires exact 150-scene broad train source plan")
    source_plans = source.get("source_registered_plans")
    source_plan_path = (source_plans[0].get("plan_path")
                        if isinstance(source_plans, list) and len(source_plans) == 1
                        and isinstance(source_plans[0], dict) else None)
    runner_plan_path = broad_plan_path.parent / "results" / "registered_plan.json"
    source_plan_is_runner_copy = (
        source_plan_path == str(runner_plan_path) and runner_plan_path.is_file()
        and _sha(runner_plan_path) == _sha(broad_plan_path))
    source_plan_location_valid = (source_plan_path == str(broad_plan_path)
                                  or source_plan_is_runner_copy)
    expected_plan = {"plan_path": source_plan_path,
                     "plan_sha256": _sha(broad_plan_path),
                     "plan_signature": broad_plan["signature"],
                     "phase": "train", "scenario_ids": broad_ids}
    if (report.get("actual_train_device") != "cuda:0" or
            report.get("actual_validation_device") != "cuda:0" or
            report.get("peak_cuda_memory_allocated_bytes", 0) <= 0 or
            report.get("optimizer_updates", 0) <= 0 or
            metadata.get("deployed_checkpoint") != "best.pt" or
            metadata.get("best_epoch") != report.get("best_epoch") or
            metadata.get("last_epoch") != report.get("last_epoch") or
            metadata.get("source_audit_sha256") != _sha(path / "source_audit.json") or
            source_ids != sorted(broad_ids) or
            not source_plan_location_valid or
            source_plans != [expected_plan] or
            source.get("manifest_sha256") != broad_plan.get("manifest_sha256") or
            source.get("manifest_signature") != broad_plan.get("manifest_signature") or
            source.get("role") != "train" or
            source.get("final_12_backgrounds_opened") is not False):
        raise ValueError("Formal best model lacks CUDA fit, broad-train lineage or valid checkpoint")
    trajectories = source.get("source_trajectories")
    if not isinstance(trajectories, list) or not trajectories:
        raise ValueError("Formal model source raw/result ledger is absent")
    seen = set()
    for row in trajectories:
        sid, pid = row.get("scenario_id"), row.get("policy_id")
        if (sid not in broad_ids or not pid or (sid, pid) in seen or
                row.get("score_recheck_difference_count") != 0):
            raise ValueError("Formal model source raw/result identity differs")
        seen.add((sid, pid))
        result_path, raw_path = Path(row["result_path"]), Path(row["raw_path"])
        if (not result_path.is_file() or _sha(result_path) != row.get("result_sha256") or
                not raw_path.is_file() or _sha(raw_path) != row.get("raw_sha256")):
            raise ValueError("Formal model source raw/result SHA differs")
        result = _read(result_path)
        if (result.get("scenario_id") != sid or result.get("policy_id") != pid or
                result.get("phase") != "train" or result.get("terminal") is not True or
                result.get("plan_signature") != broad_plan["signature"] or
                result.get("trajectory_sha256") != row["raw_sha256"] or
                result_path.parent / result.get("trajectory_path", "") != raw_path or
                result.get("status") != row.get("status")):
            raise ValueError("Formal model source result receipt differs")
    if {sid for sid, _ in seen} != set(broad_ids):
        raise ValueError("Formal model source raw has incomplete broad-train scene coverage")
    return path, binding, {"source_audit_sha256": _sha(path / "source_audit.json"),
                           "fit_report_sha256": _sha(path / "fit_report.json"),
                           "broad_train_source_overlap": len(set(source_ids) & set(broad_ids)),
                           "source_result_count": len(trajectories)}


def _selection_roster(feedback, formal_model, old_guard, old_nn):
    specs = [{"policy_id": "secure_milp", "kind": "secure"},
             {"policy_id": "priority_milp", "kind": "priority"},
             {"policy_id": "current_rule", "kind": "current_rule"},
             {"policy_id": "strict_guard", "kind": "strict_guard",
              "model_dir": _model_path(old_guard)},
             {"policy_id": "old_nn_v5_aggregate", "kind": "neural_feedback",
              "model_dir": _model_path(old_nn)},
             {"policy_id": "v6_fixed_gt_port_030", "kind": "v6_feedback",
              "family": "port_residual", "theta": [0.3, 0.3, 0.3, 0.0, 0.0, 0.0]},
             {"policy_id": "v6_fixed_gt_source_050", "kind": "v6_feedback",
              "family": "source_allocation", "theta": [0.5, 0.0, 0.0, 0.0,
                                                    0.0, 0.0, 0.0]},
             {"policy_id": "v6_identity_port", "kind": "v6_feedback",
              "family": "port_residual", "theta": zero_theta("port_residual")}]
    names = {"port_residual": "v6_train_best_port",
             "source_allocation": "v6_train_best_source"}
    for row in feedback:
        specs.append({"policy_id": names[row["family"]], "kind": "v6_feedback",
                      "family": row["family"], "theta": row["theta"]})
    specs.append({"policy_id": "v6_outcome_nn_best", "kind": "v6_neural",
                  "model_dir": _model_path(formal_model)})
    return specs


def register_selection(train_plan_path, train_audit_path, new_manifest_path,
                       formal_model_dir, preference_path, selection_dir, *,
                       old_guard_model_dir=DEFAULT_OLD_GUARD,
                       old_nn_model_dir=DEFAULT_OLD_NN):
    """Freeze full roster first, then register all 30 new selection scenes."""
    train_plan_path = Path(train_plan_path).resolve()
    train_audit_path = Path(train_audit_path).resolve()
    new_manifest_path = Path(new_manifest_path).resolve()
    preference_path = Path(preference_path).resolve()
    preference = _selection_preference(preference_path)
    reference_v1 = (preference_path.parent /
                    preference["reference_preference_v1_path"]).resolve()
    train_plan, train_manifest = verify_batch(train_plan_path)
    _broad_training_panel(train_plan, train_manifest)
    raw_index = verify_saved_audit(train_plan_path, train_audit_path, "train")
    audit = _read(train_audit_path)
    feedback = choose_training_feedback(train_plan, audit)
    formal_path, formal_binding, formal_lineage = _formal_cuda_model(
        formal_model_dir, train_plan, train_plan_path)
    manifest = _read(new_manifest_path)
    ids = phase_panel(manifest, "selection")
    if set(ids) & set(train_plan["scenario_ids"]):
        raise ValueError("New selection scenarios overlap training outcomes")
    specs = _selection_roster(feedback, formal_path, old_guard_model_dir,
                              old_nn_model_dir)
    model_bindings = {spec["policy_id"]: _model_binding(spec) for spec in specs
                      if spec.get("model_dir")}
    source_binding = _source_binding(specs)
    out = Path(selection_dir)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError("Selection needs a fresh output directory")
    out.mkdir(parents=True, exist_ok=True)
    basis = signed({"schema": BASIS_SCHEMA,
                    "selection_registered_before_outcomes": True,
                    "train_plan_path": str(train_plan_path),
                    "train_plan_sha256": _sha(train_plan_path),
                    "train_plan_signature": train_plan["signature"],
                    "train_audit_path": str(train_audit_path),
                    "train_audit_sha256": _sha(train_audit_path),
                    "train_raw_sha_index_digest": digest(raw_index),
                    "train_scenario_count": len(train_plan["scenario_ids"]),
                    "feedback_choice": feedback,
                    "formal_model_binding": formal_binding,
                    "formal_model_train_lineage": formal_lineage,
                    "new_manifest_path": str(new_manifest_path),
                    "new_manifest_sha256": _sha(new_manifest_path),
                    "selection_scenario_ids": ids,
                    "preference_sha256": _sha(preference_path),
                    "preference_schema": SELECTION_PREFERENCE_SCHEMA,
                    "reference_preference_v1_path": str(reference_v1),
                    "reference_preference_v1_sha256": _sha(reference_v1),
                    "selection_procedure_sha256": _sha(__file__),
                    "v1_priority_source_sha256": _sha(
                        ROOT / "tools/policy_redesign/search.py"),
                    "policy_specs": specs,
                    "model_bindings_before_registration": model_bindings,
                    "source_sha256_before_registration": source_binding,
                    "final_blind_opened": False})
    _new_json(out / "SELECTION_BASIS.json", basis)
    plan = register_batch(new_manifest_path, out / "registered_plan.json",
                          scenario_ids=ids, policy_specs=specs,
                          preference_path=preference_path, phase="selection")
    if (plan["policy_specs"] != specs or plan["scenario_ids"] != ids or
            plan["model_bindings"] != model_bindings or
            plan["source_sha256"] != source_binding):
        raise ValueError("Runner registration differs from frozen selection roster")
    return basis, plan


def _metric_for_rank(pid, comparison, scenario_count):
    metric = comparison["primary"]
    if metric["registered_case_count"] != scenario_count:
        raise ValueError("Selection policy lacks complete registered denominator")
    key_v1 = list(priority_key(metric))
    key_v2 = list(selection_priority_key_v2(metric))
    action = comparison.get("action_effect") or {}
    interventions = action.get("totals_over_available_cases_only", {}).get(
        "request_changed_decisions")
    return {"policy_id": pid, "priority_key": key_v2,
            "priority_key_v2": key_v2, "priority_key_v1": key_v1,
            "complete_paired_case_count": metric["complete_paired_case_count"],
            "unknown_case_ids": metric["unknown_case_ids"],
            "request_changed_decisions_observed": interventions,
            "primary_metrics": metric, "full_metrics": comparison["full"],
            "worst_case_by_target": comparison["worst_case_by_target"],
            "full_worst_case_by_target": comparison["full_worst_case_by_target"]}


def freeze_selection(basis_path, selection_plan_path, selection_audit_path,
                     freeze_path):
    """Rank only the 30 raw-audited selection scenes; never inspect development."""
    basis_path = Path(basis_path).resolve()
    selection_plan_path = Path(selection_plan_path).resolve()
    selection_audit_path = Path(selection_audit_path).resolve()
    _preexisting = Path(freeze_path)
    if _preexisting.exists():
        raise FileExistsError("Selection freeze is immutable")
    basis = _checked(basis_path, BASIS_SCHEMA)
    plan, manifest = verify_batch(selection_plan_path)
    ids = phase_panel(manifest, "selection")
    if (plan["phase"] != "selection" or plan["scenario_ids"] != ids or
            plan["policy_specs"] != basis["policy_specs"] or
            plan["preference_sha256"] != basis["preference_sha256"] or
            basis.get("preference_schema") != SELECTION_PREFERENCE_SCHEMA or
            plan["manifest_sha256"] != basis["new_manifest_sha256"] or
            basis["selection_scenario_ids"] != ids or
            basis.get("final_blind_opened") is not False):
        raise ValueError("Selection plan differs from preregistered basis")
    if ("model_bindings_before_registration" in basis and
            plan["model_bindings"] != basis["model_bindings_before_registration"]):
        raise ValueError("Selection model bytes differ from preregistration")
    if ("source_sha256_before_registration" in basis and
            plan["source_sha256"] != basis["source_sha256_before_registration"]):
        raise ValueError("Selection source bytes differ from preregistration")
    if (basis.get("selection_procedure_sha256") != _sha(__file__) or
            basis.get("v1_priority_source_sha256") != _sha(
                ROOT / "tools/policy_redesign/search.py")):
        raise ValueError("Selection procedure differs from preregistration")
    preference = _selection_preference(plan["preference_path"])
    reference_v1 = (Path(plan["preference_path"]).parent /
                    preference["reference_preference_v1_path"]).resolve()
    if (basis.get("reference_preference_v1_path") != str(reference_v1) or
            basis.get("reference_preference_v1_sha256") != _sha(reference_v1)):
        raise ValueError("Selection v1 comparator differs from preregistered basis")
    raw_index = verify_saved_audit(selection_plan_path, selection_audit_path,
                                   "selection")
    audit = _read(selection_audit_path)
    comparisons = audit["policy_comparisons"]
    expected = {s["policy_id"] for s in plan["policy_specs"]}
    if set(comparisons) != expected:
        raise ValueError("Selection audit policy comparison roster differs")
    ranked = [_metric_for_rank(pid, comparisons[pid], 30) for pid in sorted(expected)]
    ranked.sort(key=lambda row: (tuple(row["priority_key"]),
                row["policy_id"] != "secure_milp",
                float("inf") if row["request_changed_decisions_observed"] is None
                else row["request_changed_decisions_observed"], row["policy_id"]))
    ranked_v1 = sorted(ranked, key=lambda row: (
        tuple(row["priority_key_v1"]), row["policy_id"] != "secure_milp",
        float("inf") if row["request_changed_decisions_observed"] is None
        else row["request_changed_decisions_observed"], row["policy_id"]))
    frozen = signed({"schema": FREEZE_SCHEMA,
                     "selected_policy_id": ranked[0]["policy_id"],
                     "selected_policy_id_v1_comparator": ranked_v1[0]["policy_id"],
                     "selection_rule": "RESEARCH_PREFERENCE_v2 P0 noncompensation; secure then fewer observed interventions on exact tie",
                     "ranked_policies": ranked,
                     "ranked_policies_v1_comparator": [
                         {"policy_id": row["policy_id"],
                          "priority_key_v1": row["priority_key_v1"]}
                         for row in ranked_v1],
                     "selection_scenario_ids": ids,
                     "selection_unknown_primary_pair_ids": audit["unknown_primary_pair_ids"],
                     "selection_unknown_full_pair_ids": audit["unknown_full_pair_ids"],
                     "selection_subgroups": audit["subgroups"],
                     "selection_basis_path": str(basis_path),
                     "selection_basis_sha256": _sha(basis_path),
                     "selection_plan_path": str(selection_plan_path),
                     "selection_plan_sha256": _sha(selection_plan_path),
                     "selection_plan_signature": plan["signature"],
                     "selection_audit_path": str(selection_audit_path),
                     "selection_audit_sha256": _sha(selection_audit_path),
                     "selection_summary_sha256": audit.get("summary_sha256"),
                     "selection_raw_sha_index_digest": digest(raw_index),
                     "manifest_sha256": plan["manifest_sha256"],
                     "preference_sha256": plan["preference_sha256"],
                     "preference_schema": SELECTION_PREFERENCE_SCHEMA,
                     "reference_preference_v1_path": str(reference_v1),
                     "reference_preference_v1_sha256": _sha(reference_v1),
                     "selection_procedure_sha256": basis["selection_procedure_sha256"],
                     "v1_priority_source_sha256": basis["v1_priority_source_sha256"],
                     "source_sha256": plan["source_sha256"],
                     "model_bindings": plan["model_bindings"],
                     "policy_specs": plan["policy_specs"],
                     "development_outcomes_read": False,
                     "final_blind_opened": False})
    _new_json(freeze_path, frozen)
    return frozen


def _verify_freeze(freeze_path):
    freeze_path = Path(freeze_path).resolve()
    frozen = _checked(freeze_path, FREEZE_SCHEMA)
    for field in ("selection_basis", "selection_plan", "selection_audit"):
        path = Path(frozen[field + "_path"])
        if not path.is_file() or _sha(path) != frozen[field + "_sha256"]:
            raise ValueError("Frozen selection lineage changed: " + field)
    if (frozen.get("development_outcomes_read") is not False or
            frozen.get("final_blind_opened") is not False):
        raise ValueError("Frozen selection violates development/blind boundary")
    plan, manifest = verify_batch(frozen["selection_plan_path"])
    preference = _selection_preference(plan["preference_path"])
    reference_v1 = (Path(plan["preference_path"]).parent /
                    preference["reference_preference_v1_path"]).resolve()
    if (plan["signature"] != frozen["selection_plan_signature"] or
            plan["policy_specs"] != frozen["policy_specs"] or
            plan["model_bindings"] != frozen["model_bindings"] or
            plan["source_sha256"] != frozen["source_sha256"] or
            phase_panel(manifest, "selection") != frozen["selection_scenario_ids"] or
            frozen.get("preference_schema") != SELECTION_PREFERENCE_SCHEMA or
            plan["preference_sha256"] != frozen["preference_sha256"] or
            frozen.get("reference_preference_v1_path") != str(reference_v1) or
            frozen.get("reference_preference_v1_sha256") != _sha(reference_v1) or
            frozen.get("selection_procedure_sha256") != _sha(__file__) or
            frozen.get("v1_priority_source_sha256") != _sha(
                ROOT / "tools/policy_redesign/search.py") or
            frozen.get("selected_policy_id") != frozen["ranked_policies"][0]["policy_id"] or
            frozen.get("selected_policy_id_v1_comparator") !=
            frozen["ranked_policies_v1_comparator"][0]["policy_id"]):
        raise ValueError("Frozen selection roster/source/model binding changed")
    return frozen, plan, manifest


def register_development(freeze_path, new_manifest_path, preference_path,
                         development_plan_path):
    """Open disjoint 30-scene development only after selection is frozen."""
    frozen, selection_plan, _ = _verify_freeze(freeze_path)
    raw_index = verify_saved_audit(frozen["selection_plan_path"],
                                   frozen["selection_audit_path"], "selection")
    if digest(raw_index) != frozen["selection_raw_sha_index_digest"]:
        raise ValueError("Selection raw SHA index changed after freeze")
    new_manifest_path = Path(new_manifest_path).resolve()
    preference_path = Path(preference_path).resolve()
    if (_sha(new_manifest_path) != frozen["manifest_sha256"] or
            _sha(preference_path) != frozen["preference_sha256"]):
        raise ValueError("Development manifest or preference differs from frozen selection")
    _selection_preference(preference_path)
    manifest = _read(new_manifest_path)
    ids = phase_panel(manifest, "development")
    if set(ids) & set(selection_plan["scenario_ids"]):
        raise ValueError("Development scenes overlap selection")
    if {s["root_family_id"] for s in manifest["scenarios"] if s["id"] in ids} & {
            s["root_family_id"] for s in manifest["scenarios"]
            if s["id"] in selection_plan["scenario_ids"]}:
        raise ValueError("Development roots overlap selection")
    plan = register_batch(new_manifest_path, development_plan_path,
                          scenario_ids=ids, policy_specs=frozen["policy_specs"],
                          preference_path=preference_path, phase="development")
    if (plan["policy_specs"] != frozen["policy_specs"] or
            plan["model_bindings"] != frozen["model_bindings"] or
            plan["source_sha256"] != frozen["source_sha256"]):
        raise ValueError("Development runner binding differs from selection freeze")
    return plan


def review_development(freeze_path, development_plan_path, development_audit_path,
                       review_path):
    """Attach all development outcomes without reopening selection choices."""
    frozen, _, manifest = _verify_freeze(freeze_path)
    selection_raw_index = verify_saved_audit(
        frozen["selection_plan_path"], frozen["selection_audit_path"], "selection")
    if digest(selection_raw_index) != frozen["selection_raw_sha_index_digest"]:
        raise ValueError("Selection raw SHA index changed before development review")
    plan, _ = verify_batch(development_plan_path)
    if (plan["phase"] != "development" or
            plan["scenario_ids"] != phase_panel(manifest, "development") or
            plan["policy_specs"] != frozen["policy_specs"] or
            plan["manifest_sha256"] != frozen["manifest_sha256"] or
            plan["preference_sha256"] != frozen["preference_sha256"] or
            plan["model_bindings"] != frozen["model_bindings"] or
            plan["source_sha256"] != frozen["source_sha256"]):
        raise ValueError("Development result uses a different frozen comparison design")
    raw_index = verify_saved_audit(development_plan_path, development_audit_path,
                                   "development")
    audit = _read(development_audit_path)
    report = signed({"schema": DEVELOPMENT_SCHEMA,
                     "selected_policy_id_before_development": frozen["selected_policy_id"],
                     "selection_freeze_path": str(Path(freeze_path).resolve()),
                     "selection_freeze_sha256": _sha(freeze_path),
                     "development_plan_path": str(Path(development_plan_path).resolve()),
                     "development_plan_sha256": _sha(development_plan_path),
                     "development_audit_path": str(Path(development_audit_path).resolve()),
                     "development_audit_sha256": _sha(development_audit_path),
                     "development_raw_sha_index_digest": digest(raw_index),
                     "policy_comparisons": audit["policy_comparisons"],
                     "subgroups": audit["subgroups"],
                     "unknown_primary_pair_ids": audit["unknown_primary_pair_ids"],
                     "unknown_full_pair_ids": audit["unknown_full_pair_ids"],
                     "selection_not_recomputed_from_development": True,
                     "final_blind_opened": False})
    _new_json(review_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    register = actions.add_parser("register-selection")
    for name in ("train-plan", "train-audit", "manifest", "formal-model",
                 "preference", "out-dir"):
        register.add_argument("--" + name, required=True)
    register.add_argument("--old-guard-model", default=DEFAULT_OLD_GUARD)
    register.add_argument("--old-nn-model", default=DEFAULT_OLD_NN)
    freeze = actions.add_parser("freeze-selection")
    for name in ("basis", "plan", "audit", "out"):
        freeze.add_argument("--" + name, required=True)
    develop = actions.add_parser("register-development")
    for name in ("freeze", "manifest", "preference", "plan"):
        develop.add_argument("--" + name, required=True)
    review = actions.add_parser("review-development")
    for name in ("freeze", "plan", "audit", "out"):
        review.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    if args.action == "register-selection":
        basis, plan = register_selection(args.train_plan, args.train_audit,
            args.manifest, args.formal_model, args.preference, args.out_dir,
            old_guard_model_dir=args.old_guard_model,
            old_nn_model_dir=args.old_nn_model)
        result = {"selection_scenarios": len(plan["scenario_ids"]),
                  "policies": len(plan["policy_specs"]),
                  "feedback_choice": basis["feedback_choice"]}
    elif args.action == "freeze-selection":
        frozen = freeze_selection(args.basis, args.plan, args.audit, args.out)
        result = {"selected_policy_id": frozen["selected_policy_id"],
                  "selection_unknown_pairs": len(frozen["selection_unknown_primary_pair_ids"])}
    elif args.action == "register-development":
        plan = register_development(args.freeze, args.manifest, args.preference,
                                    args.plan)
        result = {"development_scenarios": len(plan["scenario_ids"]),
                  "policies": len(plan["policy_specs"])}
    else:
        review = review_development(args.freeze, args.plan, args.audit, args.out)
        result = {"selected_policy_id_before_development":
                  review["selected_policy_id_before_development"],
                  "development_unknown_pairs": len(review["unknown_primary_pair_ids"])}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
