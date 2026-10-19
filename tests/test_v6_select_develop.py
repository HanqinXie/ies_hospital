"""Selection/development stage gates; fixture scores are not experiment results."""
from __future__ import annotations

import hashlib
import json

import pytest

from tools.control_improvement.ranking import TARGET_NAMES
from tools.policy_redesign import select_develop
from tools.policy_search.preference import paired_metrics


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest():
    scenes = []
    for phase in ("selection", "development"):
        for condition in range(6):
            for pressure in range(5):
                scenes.append({"id": f"{phase}_{condition}_{pressure}",
                               "experiment_role": phase,
                               "condition_id": f"condition_{condition}",
                               "root_family_id": f"{phase}_root_{condition}",
                               "pressure_point_index": pressure,
                               "is_final_blind": False})
    return {"scenarios": scenes}


def _score(*, cases=0.0, minutes=0.0, cost=0.0, complete=True):
    values = [0.0] * len(TARGET_NAMES)
    values[0], values[1], values[-1] = cases, minutes, cost
    return {"full_horizon": complete,
            "targets": values if complete else [None] * len(values)}


def _metric_rows(scenarios, policies):
    rows = []
    for sid in scenarios:
        for pid, effect in policies.items():
            score = _score(**effect)
            rows.append({"scenario_id": sid, "policy_id": pid,
                         "status": "complete", "terminal": True,
                         "primary_score": score, "full_observation": score})
    return rows


def test_new_panels_are_exact_independent_six_by_five():
    manifest = _manifest()
    selection = select_develop.phase_panel(manifest, "selection")
    development = select_develop.phase_panel(manifest, "development")
    assert len(selection) == len(development) == 30
    assert set(selection).isdisjoint(development)
    bad = _manifest()
    bad["scenarios"][0]["pressure_point_index"] = 1
    with pytest.raises(ValueError, match="five-pressure"):
        select_develop.phase_panel(bad, "selection")
    bad = _manifest()
    bad["scenarios"][0]["is_final_blind"] = True
    with pytest.raises(ValueError, match="blind"):
        select_develop.phase_panel(bad, "selection")


def test_training_feedback_choice_is_p0_first_over_cost_and_unknown():
    scenes = [f"train_{i}" for i in range(60)]
    specs = [{"policy_id": "secure_milp", "kind": "secure"},
             {"policy_id": "port_safe", "kind": "v6_feedback",
              "family": "port_residual", "theta": [0.2] * 6},
             {"policy_id": "port_cheap_harm", "kind": "v6_feedback",
              "family": "port_residual", "theta": [-0.2] * 6},
             {"policy_id": "source_unknown", "kind": "v6_feedback",
              "family": "source_allocation", "theta": [0.1] * 7}]
    rows = _metric_rows(scenes, {"secure_milp": {}, "port_safe": {"cost": 100.0},
                                "port_cheap_harm": {"cases": 1, "minutes": 15,
                                                    "cost": -1000.0},
                                "source_unknown": {"complete": False}})
    audit = {"policy_comparisons": {spec["policy_id"]: {
        "primary": paired_metrics(rows, spec["policy_id"])} for spec in specs}}
    picked = select_develop.choose_training_feedback(
        {"scenario_ids": scenes, "policy_specs": specs}, audit)
    assert [row["source_policy_id"] for row in picked] == ["port_safe", "source_unknown"]
    assert picked[0]["priority_key"] < list(select_develop.priority_key(
        audit["policy_comparisons"]["port_cheap_harm"]["primary"]))
    assert sorted(picked[1]["unknown_case_ids"]) == sorted(scenes)


def test_selection_v2_keeps_large_p0_electric_harm_ahead_of_tiny_resolved_event():
    def score(event, minutes, electric, heating):
        values = [0.0] * len(TARGET_NAMES)
        values[:4] = [event, minutes, electric, heating]
        return {"full_horizon": True, "targets": values}

    rows = [
        {"scenario_id": "normal_p2", "policy_id": "secure_milp",
         "primary_score": score(1, 2, 0.001, 0.000722)},
        {"scenario_id": "normal_p2", "policy_id": "abs_up",
         "primary_score": score(0, 0, 0.001, 0.000047)},
        {"scenario_id": "normal_p4", "policy_id": "secure_milp",
         "primary_score": score(1, 15, 0.001988, 0.415534)},
        {"scenario_id": "normal_p4", "policy_id": "abs_up",
         "primary_score": score(1, 15, 7.155447, 0.000040)},
    ]
    secure = paired_metrics(rows, "secure_milp")
    candidate = paired_metrics(rows, "abs_up")
    assert select_develop.priority_key(candidate) < select_develop.priority_key(secure)
    assert (select_develop.selection_priority_key_v2(candidate) >
            select_develop.selection_priority_key_v2(secure))


def test_selection_preference_v2_binds_immutable_v1_comparator(tmp_path):
    v1 = tmp_path / "RESEARCH_PREFERENCE_v1.json"
    _write(v1, {"schema": select_develop.PREFERENCE_SCHEMA,
                "priority_order": ["new minus resolved P0 failure cases"],
                "selection_rule": "P0 first", "final_12_backgrounds_unopened": True})
    v2 = tmp_path / "RESEARCH_PREFERENCE_v2.json"
    _write(v2, {"schema": select_develop.SELECTION_PREFERENCE_SCHEMA,
                "registered_after_train_before_selection_outcomes": True,
                "selection_rule_id": "v2_p0_noncompensation",
                "priority_order": list(select_develop.SELECTION_PRIORITY_ORDER_V2),
                "reference_preference_v1_path": v1.name,
                "reference_preference_v1_sha256": _sha(v1),
                "final_12_backgrounds_unopened": True})
    assert select_develop._selection_preference(v2)["selection_rule_id"] == "v2_p0_noncompensation"
    with pytest.raises(ValueError, match="v2"):
        select_develop._selection_preference(v1)
    v1.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="v1"):
        select_develop._selection_preference(v2)


def test_broad_train_gate_requires_exact_150_scene_five_pressure_roots():
    scenes = []
    for condition in range(6):
        for root in range(5):
            for pressure in range(5):
                scenes.append({"id": f"t_{condition}_{root}_{pressure}",
                               "experiment_role": "train", "is_final_blind": False,
                               "condition_id": f"condition_{condition}",
                               "root_family_id": f"root_{condition}_{root}",
                               "pressure_point_index": pressure})
    plan = {"phase": "train", "scenario_ids": [s["id"] for s in scenes]}
    assert select_develop._broad_training_panel(plan, {"scenarios": scenes}) is None
    partial = {"phase": "train", "scenario_ids": [s["id"] for s in scenes
                                           if s["pressure_point_index"] in (2, 3)]}
    with pytest.raises(ValueError, match="150|five-pressure"):
        select_develop._broad_training_panel(partial, {"scenarios": scenes})
    scenes[0]["pressure_point_index"] = 0
    scenes[1]["pressure_point_index"] = 0
    with pytest.raises(ValueError, match="five-pressure"):
        select_develop._broad_training_panel(plan, {"scenarios": scenes})


def test_formal_model_gate_requires_actual_cuda_and_broad_source(tmp_path, monkeypatch):
    model = tmp_path / "formal"
    source_ids = [f"s{i}" for i in range(150)]
    plan_path = tmp_path / "broad_train" / "registered_plan.json"
    broad_plan = {"signature": "broad_signed", "phase": "train",
                  "scenario_ids": source_ids, "manifest_sha256": "manifest_sha",
                  "manifest_signature": "manifest_signed"}
    _write(plan_path, broad_plan)
    _write(model / "fit_report.json", {"actual_train_device": "cuda:0",
                                       "actual_validation_device": "cuda:0",
                                       "peak_cuda_memory_allocated_bytes": 1234,
                                       "optimizer_updates": 20, "best_epoch": 3,
                                       "last_epoch": 6})
    sources = []
    for sid in source_ids:
        raw_path = tmp_path / "broad_train" / "results" / sid / "trajectory.json.gz"
        result_path = raw_path.parent / "result.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(sid.encode())
        _write(result_path, {"scenario_id": sid, "policy_id": "secure_milp",
                             "phase": "train", "terminal": True,
                             "plan_signature": "broad_signed", "status": "complete",
                             "trajectory_sha256": _sha(raw_path),
                             "trajectory_path": "trajectory.json.gz"})
        sources.append({"scenario_id": sid, "policy_id": "secure_milp",
                        "status": "complete", "result_path": str(result_path),
                        "result_sha256": _sha(result_path), "raw_path": str(raw_path),
                        "raw_sha256": _sha(raw_path),
                        "score_recheck_difference_count": 0})
    source_audit = {"role": "train", "manifest_sha256": "manifest_sha",
                    "manifest_signature": "manifest_signed",
                    "registered_train_panel_scenario_ids": sorted(source_ids),
                    "source_registered_plans": [{"plan_path": str(plan_path.resolve()),
                                                 "plan_sha256": _sha(plan_path),
                                                 "plan_signature": "broad_signed",
                                                 "phase": "train",
                                                 "scenario_ids": source_ids}],
                    "source_trajectories": sources,
                    "final_12_backgrounds_opened": False}
    _write(model / "source_audit.json", source_audit)
    (model / "best.pt").write_bytes(b"best checkpoint fixture")
    (model / "last.pt").write_bytes(b"last checkpoint fixture")
    _write(model / "metadata.json", {"deployed_checkpoint": "best.pt",
                                      "best_epoch": 3, "last_epoch": 6,
                                      "source_audit_sha256": _sha(model / "source_audit.json"),
                                      "artifacts": {
                                          "best.pt": _sha(model / "best.pt"),
                                          "last.pt": _sha(model / "last.pt"),
                                          "fit_report.json": _sha(model / "fit_report.json")}})
    monkeypatch.setattr(select_develop, "model_artifact_binding",
                        lambda path: {"best_checkpoint_sha256": "frozen"})
    path, binding, lineage = select_develop._formal_cuda_model(
        model, broad_plan, plan_path)
    assert path == model and binding["best_checkpoint_sha256"] == "frozen"
    assert lineage["broad_train_source_overlap"] == 150
    assert lineage["source_result_count"] == 150
    runner_plan_path = plan_path.parent / "results" / "registered_plan.json"
    runner_plan_path.write_bytes(plan_path.read_bytes())
    source_audit["source_registered_plans"][0]["plan_path"] = str(runner_plan_path.resolve())
    _write(model / "source_audit.json", source_audit)
    metadata = json.loads((model / "metadata.json").read_text(encoding="utf-8"))
    metadata["source_audit_sha256"] = _sha(model / "source_audit.json")
    _write(model / "metadata.json", metadata)
    copied_path, _, copied_lineage = select_develop._formal_cuda_model(
        model, broad_plan, plan_path)
    assert copied_path == model and copied_lineage["source_result_count"] == 150
    arbitrary_plan_path = tmp_path / "other" / "registered_plan.json"
    arbitrary_plan_path.parent.mkdir()
    arbitrary_plan_path.write_bytes(plan_path.read_bytes())
    source_audit["source_registered_plans"][0]["plan_path"] = str(arbitrary_plan_path.resolve())
    _write(model / "source_audit.json", source_audit)
    metadata["source_audit_sha256"] = _sha(model / "source_audit.json")
    _write(model / "metadata.json", metadata)
    with pytest.raises(ValueError, match="broad-train lineage"):
        select_develop._formal_cuda_model(model, broad_plan, plan_path)
    source_audit["source_registered_plans"][0]["plan_path"] = str(plan_path.resolve())
    _write(model / "source_audit.json", source_audit)
    metadata["source_audit_sha256"] = _sha(model / "source_audit.json")
    _write(model / "metadata.json", metadata)
    (model / "last.pt").unlink()
    with pytest.raises(ValueError, match="last.pt"):
        select_develop._formal_cuda_model(model, broad_plan, plan_path)
    (model / "last.pt").write_bytes(b"last checkpoint fixture")
    partial_plan = dict(broad_plan, scenario_ids=source_ids[:60])
    _write(plan_path, partial_plan)
    source_audit["registered_train_panel_scenario_ids"] = sorted(source_ids[:60])
    source_audit["source_registered_plans"][0]["scenario_ids"] = source_ids[:60]
    source_audit["source_registered_plans"][0]["plan_sha256"] = _sha(plan_path)
    source_audit["source_trajectories"] = sources[:60]
    _write(model / "source_audit.json", source_audit)
    metadata = json.loads((model / "metadata.json").read_text(encoding="utf-8"))
    metadata["source_audit_sha256"] = _sha(model / "source_audit.json")
    _write(model / "metadata.json", metadata)
    with pytest.raises(ValueError, match="150"):
        select_develop._formal_cuda_model(model, partial_plan, plan_path)
    _write(plan_path, broad_plan)
    source_audit["registered_train_panel_scenario_ids"] = sorted(source_ids)
    source_audit["source_registered_plans"][0]["scenario_ids"] = source_ids
    source_audit["source_registered_plans"][0]["plan_sha256"] = _sha(plan_path)
    source_audit["source_trajectories"] = sources
    _write(model / "source_audit.json", source_audit)
    metadata["source_audit_sha256"] = _sha(model / "source_audit.json")
    _write(model / "metadata.json", metadata)
    report = json.loads((model / "fit_report.json").read_text(encoding="utf-8"))
    report["actual_train_device"] = "cpu"
    _write(model / "fit_report.json", report)
    metadata["artifacts"]["fit_report.json"] = _sha(model / "fit_report.json")
    _write(model / "metadata.json", metadata)
    with pytest.raises(ValueError, match="CUDA fit"):
        select_develop._formal_cuda_model(model, broad_plan, plan_path)
    report["actual_train_device"] = "cuda:0"
    _write(model / "fit_report.json", report)
    metadata["artifacts"]["fit_report.json"] = _sha(model / "fit_report.json")
    _write(model / "metadata.json", metadata)
    source_audit["source_registered_plans"][0]["plan_signature"] = "wrong"
    _write(model / "source_audit.json", source_audit)
    metadata = json.loads((model / "metadata.json").read_text(encoding="utf-8"))
    metadata["source_audit_sha256"] = _sha(model / "source_audit.json")
    _write(model / "metadata.json", metadata)
    with pytest.raises(ValueError, match="broad-train lineage"):
        select_develop._formal_cuda_model(model, broad_plan, plan_path)
    source_audit["source_registered_plans"][0]["plan_signature"] = "broad_signed"
    _write(model / "source_audit.json", source_audit)
    metadata["source_audit_sha256"] = _sha(model / "source_audit.json")
    metadata["last_epoch"] = 5
    _write(model / "metadata.json", metadata)
    with pytest.raises(ValueError, match="valid checkpoint"):
        select_develop._formal_cuda_model(model, broad_plan, plan_path)
    metadata["last_epoch"] = 6
    _write(model / "metadata.json", metadata)
    sources[0]["raw_sha256"] = "tampered"
    source_audit["source_trajectories"] = sources
    _write(model / "source_audit.json", source_audit)
    metadata["source_audit_sha256"] = _sha(model / "source_audit.json")
    _write(model / "metadata.json", metadata)
    with pytest.raises(ValueError, match="source raw/result SHA"):
        select_develop._formal_cuda_model(model, broad_plan, plan_path)


def test_saved_raw_audit_requires_current_sha_and_all_registered_pairs(tmp_path,
                                                                       monkeypatch):
    plan_path = tmp_path / "plan.json"
    summary_path = tmp_path / "summary.json"
    raw_path = tmp_path / "results" / "pairs" / "one" / "attempt_0001" / "trajectory.json.gz"
    _write(plan_path, {"signature": "signed", "phase": "selection",
                       "scenario_ids": ["s0"],
                       "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"}]})
    _write(summary_path, {"plan_signature": "signed"})
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"real saved raw fixture")
    audit = {"schema": select_develop.AUDIT_SCHEMA, "passed": True,
             "phase": "selection", "plan_path": str(plan_path),
             "plan_sha256": _sha(plan_path), "plan_signature": "signed",
             "summary_path": str(summary_path), "summary_sha256": _sha(summary_path),
             "registered_pair_count": 1, "terminal_pair_count": 1,
             "recomputed_pair_count": 1, "matched_initial_state_and_stream": True,
             "pair_ledger": [{"scenario_id": "s0", "policy_id": "secure_milp",
                              "terminal": True, "score_recomputed": True,
                              "binding_passed": True,
                              "attempts": [{"raw_path": str(raw_path),
                                            "raw_sha256": _sha(raw_path)}]}]}
    audit_path = tmp_path / "audit.json"
    _write(audit_path, audit)
    monkeypatch.setattr(select_develop, "verify_batch", lambda path: (
        json.loads(plan_path.read_text(encoding="utf-8")), {}))
    assert len(select_develop.verify_saved_audit(plan_path, audit_path, "selection")) == 1
    raw_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw SHA"):
        select_develop.verify_saved_audit(plan_path, audit_path, "selection")
    raw_path.write_bytes(b"real saved raw fixture")
    audit["pair_ledger"] = []
    _write(audit_path, audit)
    with pytest.raises(ValueError, match="pair ledger"):
        select_develop.verify_saved_audit(plan_path, audit_path, "selection")


def test_selection_freeze_precedes_development_and_carries_fixed_roster(tmp_path,
                                                                         monkeypatch):
    manifest = _manifest()
    manifest_path = tmp_path / "manifest.json"
    _write(manifest_path, manifest)
    v1_path = tmp_path / "RESEARCH_PREFERENCE_v1.json"
    _write(v1_path, {"schema": select_develop.PREFERENCE_SCHEMA,
                     "priority_order": ["unknown scenario count and validity",
                                        "new minus resolved P0 failure cases"],
                     "selection_rule": "P0 first", "final_12_backgrounds_unopened": True})
    pref_path = tmp_path / "RESEARCH_PREFERENCE_v2.json"
    _write(pref_path, {"schema": select_develop.SELECTION_PREFERENCE_SCHEMA,
                       "registered_after_train_before_selection_outcomes": True,
                       "selection_rule_id": "v2_p0_noncompensation",
                       "priority_order": list(select_develop.SELECTION_PRIORITY_ORDER_V2),
                       "reference_preference_v1_path": v1_path.name,
                       "reference_preference_v1_sha256": _sha(v1_path),
                       "final_12_backgrounds_unopened": True})
    specs = [{"policy_id": "secure_milp", "kind": "secure"},
             {"policy_id": "priority_milp", "kind": "priority"},
             {"policy_id": "current_rule", "kind": "current_rule"},
             {"policy_id": "strict_guard", "kind": "strict_guard", "model_dir": "old_guard"},
             {"policy_id": "old_nn", "kind": "neural_feedback", "model_dir": "old_nn"},
             {"policy_id": "fixed_port", "kind": "v6_feedback",
              "family": "port_residual", "theta": [0.3, 0.3, 0.3, 0, 0, 0]},
             {"policy_id": "learned_port", "kind": "v6_feedback",
              "family": "port_residual", "theta": [0.1, 0, 0, 0, 0, 0]},
             {"policy_id": "v6_nn", "kind": "v6_neural", "model_dir": "formal"}]
    selection_ids = select_develop.phase_panel(manifest, "selection")
    plan_path = tmp_path / "selection" / "registered_plan.json"
    plan = {"signature": "selection_signature", "phase": "selection",
            "manifest_path": str(manifest_path), "manifest_sha256": _sha(manifest_path),
            "preference_path": str(pref_path), "preference_sha256": _sha(pref_path),
            "scenario_ids": selection_ids, "policy_specs": specs,
            "model_bindings": {x["policy_id"]: {"files": {"best.pt": "bound"}}
                               for x in specs if x.get("model_dir")},
            "source_sha256": {"tools/policy_redesign/policy.py": "bound"}}
    _write(plan_path, plan)
    basis_path = tmp_path / "selection" / "SELECTION_BASIS.json"
    basis = select_develop.signed({"schema": select_develop.BASIS_SCHEMA,
                                  "policy_specs": specs,
                                  "preference_sha256": _sha(pref_path),
                                  "preference_schema": select_develop.SELECTION_PREFERENCE_SCHEMA,
                                  "reference_preference_v1_path": str(v1_path),
                                  "reference_preference_v1_sha256": _sha(v1_path),
                                  "selection_procedure_sha256": _sha(
                                      select_develop.Path(select_develop.__file__)),
                                  "v1_priority_source_sha256": _sha(
                                      select_develop.ROOT / "tools/policy_redesign/search.py"),
                                  "train_audit_sha256": "train_bound",
                                  "new_manifest_sha256": _sha(manifest_path),
                                  "selection_scenario_ids": selection_ids,
                                  "final_blind_opened": False})
    _write(basis_path, basis)
    rows = _metric_rows(selection_ids, {pid: ({} if pid == "secure_milp"
                                           else {"cost": 100.0} if pid == "learned_port"
                                           else {"cases": 1, "minutes": 15,
                                                 "cost": -1000.0})
                                       for pid in [x["policy_id"] for x in specs]})
    for row in rows:
        if row["scenario_id"] == selection_ids[0] and row["policy_id"] == "secure_milp":
            row["primary_score"]["targets"][:4] = [1, 2, 0, 0.000722]
        if row["scenario_id"] == selection_ids[1] and row["policy_id"] == "learned_port":
            row["primary_score"]["targets"][2] = 7.153
    comparisons = {pid: {"primary": paired_metrics(rows, pid),
                         "full": paired_metrics(rows, pid, score_field="full_observation"),
                         "worst_case_by_target": {"P0_failure_minutes": None},
                         "full_worst_case_by_target": {"P0_failure_minutes": None}}
                   for pid in [x["policy_id"] for x in specs]}
    audit_path = tmp_path / "selection" / "AUDIT.json"
    _write(audit_path, {"policy_comparisons": comparisons,
                        "unknown_primary_pair_ids": [], "unknown_full_pair_ids": [],
                        "subgroups": {"by_root": [], "by_condition": []}})
    monkeypatch.setattr(select_develop, "verify_batch", lambda path: (plan, manifest))
    monkeypatch.setattr(select_develop, "verify_saved_audit", lambda *args: [])
    frozen_path = tmp_path / "selection" / "FROZEN_SELECTION.json"
    frozen = select_develop.freeze_selection(basis_path, plan_path, audit_path,
                                             frozen_path)
    assert frozen["selected_policy_id"] == "secure_milp"
    assert frozen["selected_policy_id_v1_comparator"] == "learned_port"
    assert frozen["reference_preference_v1_sha256"] == _sha(v1_path)
    assert frozen["selection_procedure_sha256"] == _sha(
        select_develop.Path(select_develop.__file__))
    assert frozen["development_outcomes_read"] is False
    assert len(frozen["ranked_policies"]) == len(specs)
    assert len(frozen["ranked_policies_v1_comparator"]) == len(specs)
    assert all("priority_key_v1" in row and "priority_key_v2" in row
               for row in frozen["ranked_policies"])
    assert frozen["selection_audit_sha256"] == _sha(audit_path)
    calls = []

    def register(manifest_path, plan_path, **kwargs):
        calls.append(kwargs)
        return {"policy_specs": kwargs["policy_specs"],
                "scenario_ids": kwargs["scenario_ids"],
                "model_bindings": plan["model_bindings"],
                "source_sha256": plan["source_sha256"]}

    monkeypatch.setattr(select_develop, "register_batch", register)
    development = select_develop.register_development(
        frozen_path, manifest_path, pref_path,
        tmp_path / "development" / "registered_plan.json")
    assert development["policy_specs"] == specs
    assert len(development["scenario_ids"]) == 30
    assert calls[0]["phase"] == "development"
    assert set(development["scenario_ids"]).isdisjoint(selection_ids)
    bad_basis = dict(basis)
    bad_basis["selection_procedure_sha256"] = "tampered"
    bad_basis = select_develop.signed({k: v for k, v in bad_basis.items()
                                       if k != "signature"})
    bad_basis_path = tmp_path / "selection" / "BAD_BASIS.json"
    _write(bad_basis_path, bad_basis)
    with pytest.raises(ValueError, match="procedure"):
        select_develop.freeze_selection(
            bad_basis_path, plan_path, audit_path,
            tmp_path / "selection" / "BAD_FROZEN.json")
