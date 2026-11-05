"""Stage registration and alternative-audit compatibility are outcome isolated."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _alternative_fixture(tmp_path):
    run = tmp_path / "run"
    manifest = run / "new_data/global_manifest.json"
    preference = run / "design/RESEARCH_PREFERENCE_v1.json"
    analysis = run / "search/generation_1/ANALYSIS.json"
    freeze = run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"
    _write(manifest, {"signature": "manifest_signature"})
    _write(preference, {"clinical_standard": False})
    _write(analysis, {"p0_pareto_ids": ["feedback_a"]})
    _write(freeze, {"schema": "policy_search.algorithm_source_freeze.v1",
                    "source_sha256": {"tools/policy_search/search.py":
                                      _sha(ROOT / "tools/policy_search/search.py")},
                    "preference_sha256": _sha(preference),
                    "v5_new_manifest_sha256": _sha(manifest)})
    folder = run / "search/alternative_modes"
    plan_path = folder / "registered_plan.json"
    specs = [{"policy_id": "secure_milp", "kind": "secure"},
             {"policy_id": "feedback_a", "kind": "feedback", "family": "blend",
              "theta": [0.5, 1.0, 0, 0, 0]}]
    plan = {"schema": "policy_search.matched_full_feedback.v1", "phase": "train",
            "scenario_ids": ["train_a"], "policy_specs": specs,
            "manifest_path": str(manifest), "manifest_sha256": _sha(manifest),
            "preference_path": str(preference), "preference_sha256": _sha(preference),
            "source_sha256": {"tools/policy_search/closed_loop.py":
                              _sha(ROOT / "tools/policy_search/closed_loop.py")}}
    plan["signature"] = _digest(plan)
    _write(plan_path, plan)
    original = folder / "ALTERNATIVE_REGISTRATION.json"
    _write(original, {"schema": "policy_search.alternative_modes.v1",
                      "source_sha256": _sha(ROOT / "tools/policy_search/alternative.py"),
                      "generation_1_analysis_sha256": _sha(analysis),
                      "manifest_sha256": _sha(manifest),
                      "plan_signature": plan["signature"],
                      "scenario_ids": plan["scenario_ids"],
                      "policy_specs": specs})
    return run, plan_path, original, freeze


def test_alternative_compatibility_receipt_binds_original_registration(tmp_path):
    from tools.policy_search.stages import prepare_alternative_audit_compat

    run, plan, original, _ = _alternative_fixture(tmp_path)
    path = prepare_alternative_audit_compat(run)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    assert receipt["schema"] == "policy_search.offline_generation.v1"
    assert receipt["origin_registration_sha256"] == _sha(original)
    assert receipt["plan_signature"] == json.loads(plan.read_text())["signature"]
    assert receipt["source_sha256"] == _sha(ROOT / "tools/policy_search/search.py")
    with pytest.raises(FileExistsError):
        prepare_alternative_audit_compat(run)


def test_alternative_source_tamper_is_rejected_before_compat_receipt(tmp_path):
    from tools.policy_search.stages import prepare_alternative_audit_compat

    run, _, original, _ = _alternative_fixture(tmp_path)
    value = json.loads(original.read_text(encoding="utf-8"))
    value["source_sha256"] = "0" * 64
    _write(original, value)
    with pytest.raises(ValueError, match="alternative_source_sha256"):
        prepare_alternative_audit_compat(run)
    assert not (run / "search/alternative_modes/SEARCH_REGISTRATION.json").exists()


def test_alternative_g1_analysis_tamper_is_rejected(tmp_path):
    from tools.policy_search.stages import prepare_alternative_audit_compat

    run, _, _, _ = _alternative_fixture(tmp_path)
    _write(run / "search/generation_1/ANALYSIS.json", {"p0_pareto_ids": []})
    with pytest.raises(ValueError, match="generation_1_analysis_sha256"):
        prepare_alternative_audit_compat(run)


def _selection_fixture(tmp_path):
    from tools.policy_search.closed_loop import register_batch
    from tools.policy_search.preference_v2 import SOURCE_NAMES, analyze_audited_batch
    from tools.policy_search.stages import prepare_alternative_audit_compat, freeze_stages

    run = tmp_path / "run"
    pref = run / "design/RESEARCH_PREFERENCE_v1.json"
    _write(pref, {"priority_0_vector": ["resolved_P0_failure_cases",
                                         "P0_max_temperature_C_case_delta"]})
    scenarios, splits = [], {}
    conditions = ("c0", "c1", "c2", "c3", "c4", "c5")
    for role in ("train", "selection", "calibration", "development"):
        for condition in conditions:
            count = 2 if role == "development" else 1
            for number in range(count):
                root = f"root_{role}_{condition}_{number}"
                for pressure in (range(5) if role == "development" else (3,)):
                    sid = (f"{role}_{condition}_{number}_p{pressure}"
                           if role == "development" else f"{role}_{condition}_{number}")
                    scenarios.append({"id": sid, "experiment_role": role,
                                      "condition_id": condition,
                                      "pressure_point_index": pressure,
                                      "root_family_id": root,
                                      "time_block_id": root,
                                      "exogenous_stream_id": "stream_" + root,
                                      "is_final_blind": False})
                    splits[sid] = {"experiment_role": role, "group_id": "group_" + root,
                                   "is_final_blind": False}
    body = {"scenarios": scenarios, "splits": splits, "options": {"seed": 5},
            "simulator_hash": "physics", "execution_contract_id": "contract",
            "execution_contract": {"name": "fixture"}}
    body["signature"] = _digest(body)
    manifest = run / "new_data/global_manifest.json"
    _write(manifest, body)
    old_freeze = run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"
    _write(old_freeze, {"schema": "policy_search.algorithm_source_freeze.v1",
                        "source_sha256": {name: _sha(ROOT / name) for name in SOURCE_NAMES[:-1]},
                        "preference_sha256": _sha(pref),
                        "v5_new_manifest_sha256": _sha(manifest)})
    revision = run / "design/PREFERENCE_V2_SOURCE_FREEZE.json"
    _write(revision, {"schema": "policy_search.preference_v2_source_freeze.v1",
                      "registered_before_selection_development_outcomes": True,
                      "post_generation_1_outcome_correction": True,
                      "source_sha256": {name: _sha(ROOT / name) for name in SOURCE_NAMES},
                      "audit_source_sha256": _sha(ROOT / "tools/policy_search/audit.py"),
                      "research_preference_sha256": _sha(pref),
                      "v1_source_freeze_sha256": _sha(old_freeze)})
    train_ids = [f"train_{condition}_0" for condition in conditions]
    theta_by_batch = {
        "generation_1": {"policy_id": "g1_blend", "kind": "feedback", "family": "blend",
                         "theta": [0.5, 1.0, 0, 0, 0]},
        "generation_2": {"policy_id": "g2_blend", "kind": "feedback", "family": "blend",
                         "theta": [0.6, 1.0, 0, 0, 0]},
        "alternative_modes": {"policy_id": "alt_mode", "kind": "feedback", "family": "mode",
                              "theta": [0, 0, 0, 0, .7, 1, 0, 0, 0, 0, 0, 0]},
    }
    _write(run / "search/generation_1/ANALYSIS.json", {"p0_pareto_ids": ["g1_blend"]})
    for batch, feedback in theta_by_batch.items():
        folder = run / "search" / batch
        specs = [{"policy_id": "secure_milp", "kind": "secure"}, feedback]
        plan_path = folder / "registered_plan.json"
        plan = register_batch(manifest, plan_path, scenario_ids=train_ids,
                              policy_specs=specs, preference_path=pref, phase="train")
        if batch == "alternative_modes":
            _write(folder / "ALTERNATIVE_REGISTRATION.json",
                   {"schema": "policy_search.alternative_modes.v1",
                    "source_sha256": _sha(ROOT / "tools/policy_search/alternative.py"),
                    "generation_1_analysis_sha256": _sha(run / "search/generation_1/ANALYSIS.json"),
                    "manifest_sha256": _sha(manifest), "plan_signature": plan["signature"],
                    "scenario_ids": train_ids, "policy_specs": specs})
            prepare_alternative_audit_compat(run)
        else:
            _write(folder / "SEARCH_REGISTRATION.json",
                   {"schema": "policy_search.offline_generation.v1",
                    "source_sha256": _sha(ROOT / "tools/policy_search/search.py"),
                    "manifest_sha256": _sha(manifest),
                    "preference_sha256": _sha(pref), "plan_signature": plan["signature"],
                    "scenario_ids": train_ids, "policy_specs": specs})
        rows = []
        for sid in train_ids:
            for spec in specs:
                targets = [0.0] * 19
                targets[14:17] = [25.0] * 3
                rows.append({"scenario_id": sid, "policy_id": spec["policy_id"],
                             "plan_signature": plan["signature"], "phase": "train",
                             "terminal": True, "status": "completed",
                             "binding": {"passed": True, "checks": {"scenario_id": True},
                                         "initial_state_sha256": "init_" + sid,
                                         "exogenous_stream_id": "stream_" + sid},
                             "primary_score": {"full_horizon": True, "targets": targets,
                                               "target_mask": [True] * 19}})
        summary = folder / "results/summary_0001.json"
        _write(summary, {"schema": "policy_search.matched_full_feedback.v1.summary",
                         "plan_signature": plan["signature"], "registered_pair_count": len(rows),
                         "recorded_pair_count": len(rows), "terminal_pair_count": len(rows),
                         "matched_initial_state_and_stream": True, "results": rows})
        audit = folder / "AUDIT.json"
        _write(audit, {"schema": "policy_search.evidence_audit.v1", "passed": True,
                       "errors": [], "plan_sha256": _sha(plan_path),
                       "plan_signature": plan["signature"], "summary_sha256": _sha(summary),
                       "source_freeze_sha256": _sha(old_freeze),
                       "registration_sha256": _sha(folder / "SEARCH_REGISTRATION.json"),
                       "audit_source_sha256": _sha(ROOT / "tools/policy_search/audit.py"),
                       "registered_pair_count": len(rows),
                       "matched_initial_state_and_stream": True})
        analysis = analyze_audited_batch(plan_path, summary, audit, revision, old_freeze)
        _write(folder / "ANALYSIS_PREFERENCE_V2.json", analysis)
    for folder_name, aggregate in (("gru_g1_blend_overload_seed5101", False),
                                   ("gru_agg1_blend_overload_seed5102", True)):
        model_dir = run / "models" / folder_name
        train_source = {"role": "train", "manifest_sha256": _sha(manifest),
                        "teacher_policy_id": "g1_blend",
                        "root_family_ids": ["root_train_c0_0"],
                        "intervention_decisions": 1,
                        "final_12_backgrounds_opened": False}
        if aggregate:
            source = {"role": "train", "manifest_sha256": _sha(manifest),
                      "original_teacher": train_source,
                      "nn_visited_relabel": {
                          "manifest_sha256": _sha(manifest),
                          "teacher_spec": theta_by_batch["generation_1"],
                          "root_family_ids": ["root_train_c0_0"],
                          "teacher_interventions": 1,
                          "final_12_backgrounds_opened": False},
                      "join": {"distinct_train_roots": 1},
                      "final_12_backgrounds_opened": False}
        else:
            source = train_source
        _write(model_dir / "source_audit.json", source)
        (model_dir / "model.pt").write_bytes(folder_name.encode())
        _write(model_dir / "metadata.json",
               {"schema": "policy_search.v5.cuda_gru_feedback.v1", "architecture": "GRU",
                "source_module_sha256": _sha(ROOT / "tools/policy_search/neural.py"),
                "source_audit_sha256": _sha(model_dir / "source_audit.json"),
                "trained_weight_sha256": _sha(model_dir / "model.pt"),
                "teacher_policy_id": "g1_blend", "gate_mode": "none",
                "gate_threshold": None, "calibration_source": None,
                "artifacts": {"source_audit.json": _sha(model_dir / "source_audit.json"),
                              "model.pt": _sha(model_dir / "model.pt")}})
    freeze_stages(run)
    return run


def test_selection_registers_six_new_roots_and_distinct_feedback(tmp_path):
    from tools.policy_search.stages import register_selection

    run = _selection_fixture(tmp_path)
    plan = register_selection(run)
    assert plan["phase"] == "selection"
    assert len(plan["scenario_ids"]) == 6
    assert len(plan["policy_specs"]) == 8  # 3 controls + 3 feedback + 2 actual NN
    assert [s["policy_id"] for s in plan["policy_specs"][:3]] == [
        "secure_milp", "strict_guard", "current_rule"]
    assert [s["policy_id"] for s in plan["policy_specs"][-2:]] == [
        "nn_gru_prototype", "nn_gru_aggregate_r1"]
    assert set(plan["model_bindings"]) == {
        "strict_guard", "nn_gru_prototype", "nn_gru_aggregate_r1"}
    assert not (run / "selection/results").exists()
    stage = json.loads((run / "selection/STAGE_REGISTRATION.json").read_text(encoding="utf-8"))
    assert len(stage["prerequisite_batches"]) == 3
    assert all(v["audit_sha256"] for v in stage["prerequisite_batches"].values())


def test_selection_requires_immutable_stage_source_freeze(tmp_path):
    from tools.policy_search.stages import register_selection

    run = _selection_fixture(tmp_path)
    freeze = run / "design/STAGES_SOURCE_FREEZE.json"
    receipt = json.loads(freeze.read_text(encoding="utf-8"))
    assert receipt["stage_source_sha256"] == _sha(ROOT / "tools/policy_search/stages.py")
    assert len(receipt["selection_neural_model_sources"]) == 2
    freeze.unlink()
    with pytest.raises(FileNotFoundError):
        register_selection(run)
    assert not (run / "selection/registered_plan.json").exists()


def test_selection_refuses_missing_alternative_audit(tmp_path):
    from tools.policy_search.stages import register_selection

    run = _selection_fixture(tmp_path)
    (run / "search/alternative_modes/AUDIT.json").unlink()
    with pytest.raises(FileNotFoundError):
        register_selection(run)
    assert not (run / "selection/registered_plan.json").exists()


def test_selection_refuses_neural_source_from_selection_roots(tmp_path):
    from tools.policy_search.stages import register_selection

    run = _selection_fixture(tmp_path)
    folder = run / "models/gru_agg1_blend_overload_seed5102"
    audit_path = folder / "source_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["nn_visited_relabel"]["root_family_ids"] = ["root_selection_c0_0"]
    _write(audit_path, audit)
    metadata_path = folder / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source_audit_sha256"] = _sha(audit_path)
    metadata["artifacts"]["source_audit.json"] = _sha(audit_path)
    _write(metadata_path, metadata)
    with pytest.raises(ValueError, match="Stage source freeze NN artifact"):
        register_selection(run)
    assert not (run / "selection/registered_plan.json").exists()


def test_selection_refuses_modified_g2_summary_after_audit(tmp_path):
    from tools.policy_search.stages import register_selection

    run = _selection_fixture(tmp_path)
    path = run / "search/generation_2/results/summary_0001.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["results"][0]["primary_score"]["targets"][1] = 100.0
    _write(path, summary)
    with pytest.raises(ValueError, match="summary_sha256"):
        register_selection(run)
    assert not (run / "selection/registered_plan.json").exists()


def test_phase_scene_choice_rejects_reused_root():
    from tools.policy_search.stages import choose_phase_scenes

    scenes = [{"id": f"s{i}", "condition_id": f"c{i}", "experiment_role": "selection",
               "pressure_point_index": 3, "root_family_id": f"root{i}",
               "time_block_id": f"block{i}", "is_final_blind": False}
              for i in range(6)]
    scenes[0]["root_family_id"] = "prior_root"
    with pytest.raises(ValueError, match="disjoint"):
        choose_phase_scenes({"scenarios": scenes}, role="selection",
                            roots_per_condition=1, forbidden_roots={"prior_root"})


def _attach_stage_result(run, role, *, feedback_worse=False,
                         gate_failure_minutes=None):
    from tools.policy_search.preference_v2 import analyze_audited_batch

    folder = run / role
    plan_path = folder / "registered_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rows = []
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            targets = [0.0] * 19
            targets[14:17] = [25.0] * 3
            if feedback_worse and spec["kind"] == "feedback":
                targets[1] = 1.0
            if gate_failure_minutes and spec["policy_id"] in gate_failure_minutes:
                targets[1] = gate_failure_minutes[spec["policy_id"]]
            rows.append({"scenario_id": sid, "policy_id": spec["policy_id"],
                         "plan_signature": plan["signature"], "phase": role,
                         "terminal": True, "status": "completed",
                         "binding": {"passed": True, "checks": {"scenario_id": True},
                                     "initial_state_sha256": "init_" + sid,
                                     "exogenous_stream_id": "stream_" + sid},
                         "primary_score": {"full_horizon": True, "targets": targets,
                                           "target_mask": [True] * 19}})
    summary = folder / "results/summary_0001.json"
    _write(summary, {"schema": "policy_search.matched_full_feedback.v1.summary",
                     "plan_signature": plan["signature"], "registered_pair_count": len(rows),
                     "recorded_pair_count": len(rows), "terminal_pair_count": len(rows),
                     "matched_initial_state_and_stream": True, "results": rows})
    old_freeze = run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"
    audit = folder / "AUDIT.json"
    _write(audit, {"schema": "policy_search.evidence_audit.v1", "passed": True,
                   "errors": [], "plan_sha256": _sha(plan_path),
                   "plan_signature": plan["signature"], "summary_sha256": _sha(summary),
                   "source_freeze_sha256": _sha(old_freeze),
                   "registration_sha256": _sha(folder / "SEARCH_REGISTRATION.json"),
                   "audit_source_sha256": _sha(ROOT / "tools/policy_search/audit.py"),
                   "registered_pair_count": len(rows),
                   "matched_initial_state_and_stream": True})
    analysis = analyze_audited_batch(
        plan_path, summary, audit, run / "design/PREFERENCE_V2_SOURCE_FREEZE.json",
        old_freeze)
    _write(folder / "ANALYSIS_PREFERENCE_V2.json", analysis)


def _fake_ungated_nn(tmp_path, run, teacher="g1_blend"):
    from tools.policy_search.neural import SCHEMA

    folder = tmp_path / "nn_ungated"
    folder.mkdir()
    manifest = run / "new_data/global_manifest.json"
    _write(folder / "source_audit.json",
           {"role": "train", "manifest_sha256": _sha(manifest),
            "teacher_policy_id": teacher,
            "root_family_ids": ["root_train_c0_0"],
            "intervention_decisions": 1,
            "final_12_backgrounds_opened": False})
    (folder / "model.pt").write_bytes(b"test-only-model-bytes")
    _write(folder / "metadata.json",
           {"schema": SCHEMA, "architecture": "GRU",
            "source_module_sha256": _sha(ROOT / "tools/policy_search/neural.py"),
            "source_audit_sha256": _sha(folder / "source_audit.json"),
            "trained_weight_sha256": _sha(folder / "model.pt"),
            "teacher_policy_id": teacher,
            "gate_mode": "none", "gate_threshold": None,
            "calibration_source": None,
            "artifacts": {"source_audit.json": _sha(folder / "source_audit.json"),
                          "model.pt": _sha(folder / "model.pt")}})
    return folder


def test_calibration_then_development_registers_fixed_and_neural_controls(tmp_path):
    from tools.policy_search.stages import register_selection, register_calibration, register_development

    run = _selection_fixture(tmp_path)
    register_selection(run)
    _attach_stage_result(run, "selection")
    base = _fake_ungated_nn(tmp_path, run)
    calibration = register_calibration(run, fixed_feedback_id="g1_blend",
                                       nn_ungated_dir=base)
    assert calibration["phase"] == "calibration"
    assert len(calibration["scenario_ids"]) == 6
    assert [s["policy_id"] for s in calibration["policy_specs"]] == [
        "secure_milp", "strict_guard", "current_rule", "g1_blend", "nn_ungated",
        "nn_gate_t025", "nn_gate_t050", "nn_gate_t075"]
    cal_stage = json.loads((run / "calibration/STAGE_REGISTRATION.json").read_text())
    assert cal_stage["predeclared_gate_thresholds"] == [0.25, 0.5, 0.75]
    _attach_stage_result(run, "calibration")
    development = register_development(run, finalist_ids=["g1_blend", "alt_mode"])
    assert development["phase"] == "development"
    assert len(development["scenario_ids"]) == 60
    assert [s["policy_id"] for s in development["policy_specs"]] == [
        "secure_milp", "strict_guard", "current_rule", "g1_blend", "alt_mode",
        "nn_ungated", "nn_gate_t025", "nn_gate_t050", "nn_gate_t075"]
    assert not (run / "development/results").exists()
    stage = json.loads((run / "development/STAGE_REGISTRATION.json").read_text(encoding="utf-8"))
    assert stage["fixed_nonlearning_candidate_policy_id"] == "g1_blend"
    assert stage["pressure_sensitivity_indices"] == [0, 1, 2, 3, 4]
    assert len(stage["sensitivity_root_family_ids"]) == 12
    assert stage["selection_analysis_v2_sha256"] == _sha(
        run / "selection/ANALYSIS_PREFERENCE_V2.json")


def test_calibration_accepts_train_only_aggregate_nn(tmp_path):
    from tools.policy_search.stages import register_selection, register_calibration

    run = _selection_fixture(tmp_path)
    register_selection(run)
    _attach_stage_result(run, "selection")
    plan = register_calibration(
        run, fixed_feedback_id="g1_blend",
        nn_ungated_dir=run / "models/gru_agg1_blend_overload_seed5102")
    assert plan["phase"] == "calibration"
    assert [s["policy_id"] for s in plan["policy_specs"][-4:]] == [
        "nn_ungated", "nn_gate_t025", "nn_gate_t050", "nn_gate_t075"]


def test_dominated_audited_feedback_can_still_enter_exploratory_development(tmp_path):
    from tools.policy_search.stages import register_selection, register_calibration, register_development

    run = _selection_fixture(tmp_path)
    register_selection(run)
    _attach_stage_result(run, "selection", feedback_worse=True)
    selection_analysis = json.loads((run / "selection/ANALYSIS_PREFERENCE_V2.json").read_text())
    assert "g1_blend" not in selection_analysis["p0_pareto_ids"]
    base = run / "models/gru_agg1_blend_overload_seed5102"
    calibration = register_calibration(run, fixed_feedback_id="g1_blend",
                                       nn_ungated_dir=base)
    stage = json.loads((run / "calibration/STAGE_REGISTRATION.json").read_text())
    assert stage["fixed_candidate_on_selection_P0_frontier"] is False
    _attach_stage_result(run, "calibration")
    development = register_development(run, finalist_ids=["g1_blend"])
    assert development["phase"] == "development"
    dev_stage = json.loads((run / "development/STAGE_REGISTRATION.json").read_text())
    assert dev_stage["finalist_on_selection_P0_frontier"] == {"g1_blend": False}


def test_development_gate_selection_uses_calibration_p0_results(tmp_path):
    from tools.policy_search.stages import register_selection, register_calibration, register_development

    run = _selection_fixture(tmp_path)
    register_selection(run)
    _attach_stage_result(run, "selection")
    register_calibration(
        run, fixed_feedback_id="g1_blend",
        nn_ungated_dir=run / "models/gru_agg1_blend_overload_seed5102")
    _attach_stage_result(run, "calibration", gate_failure_minutes={
        "nn_gate_t025": 0.0, "nn_gate_t050": 1.0, "nn_gate_t075": 2.0})
    development = register_development(run, finalist_ids=["g1_blend"])
    assert [s["policy_id"] for s in development["policy_specs"] if
            s["policy_id"].startswith("nn_gate_")] == ["nn_gate_t025"]
    stage = json.loads((run / "development/STAGE_REGISTRATION.json").read_text())
    assert stage["calibration_selected_gate_policy_ids"] == ["nn_gate_t025"]


def test_development_rejects_unrelated_gate_calibration_source(tmp_path):
    from tools.policy_search.stages import register_selection, register_calibration, register_development

    run = _selection_fixture(tmp_path)
    register_selection(run)
    _attach_stage_result(run, "selection")
    base = _fake_ungated_nn(tmp_path, run)
    register_calibration(run, fixed_feedback_id="g1_blend", nn_ungated_dir=base)
    _attach_stage_result(run, "calibration")
    gate_meta = run / "models/calibration_gate_t025/metadata.json"
    metadata = json.loads(gate_meta.read_text(encoding="utf-8"))
    metadata["calibration_source"]["source_sha256"] = "0" * 64
    _write(gate_meta, metadata)
    with pytest.raises(ValueError, match="gate|Model bytes"):
        register_development(run, finalist_ids=["g1_blend"])
    assert not (run / "development/registered_plan.json").exists()


def test_development_rejects_more_than_three_feedback_finalists(tmp_path):
    from tools.policy_search.stages import register_selection, register_calibration, register_development

    run = _selection_fixture(tmp_path)
    register_selection(run)
    _attach_stage_result(run, "selection")
    base = _fake_ungated_nn(tmp_path, run)
    register_calibration(run, fixed_feedback_id="g1_blend", nn_ungated_dir=base)
    _attach_stage_result(run, "calibration")
    with pytest.raises(ValueError, match="finalist"):
        register_development(run,
                             finalist_ids=["g1_blend", "g2_blend", "alt_mode", "extra"])


def test_fixed_feedback_may_differ_from_neural_teacher(tmp_path):
    from tools.policy_search.stages import register_selection, register_calibration, register_development

    run = _selection_fixture(tmp_path)
    register_selection(run)
    _attach_stage_result(run, "selection")
    calibration = register_calibration(
        run, fixed_feedback_id="g2_blend",
        nn_ungated_dir=run / "models/gru_agg1_blend_overload_seed5102")
    ids = [s["policy_id"] for s in calibration["policy_specs"]]
    assert ids[:6] == ["secure_milp", "strict_guard", "current_rule",
                       "g2_blend", "g1_blend", "nn_ungated"]
    _attach_stage_result(run, "calibration")
    development = register_development(run, finalist_ids=["g2_blend"])
    ids = [s["policy_id"] for s in development["policy_specs"]]
    assert ids[:6] == ["secure_milp", "strict_guard", "current_rule",
                       "g2_blend", "g1_blend", "nn_ungated"]


def test_development_refuses_incomplete_registered_pressure_cross(tmp_path):
    from tools.policy_search.stages import choose_phase_scenes, expand_development_pressure_cross

    run = _selection_fixture(tmp_path)
    manifest_path = run / "new_data/global_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scenarios"] = [s for s in manifest["scenarios"]
                             if s["id"] != "development_c0_0_p4"]
    manifest["splits"].pop("development_c0_0_p4")
    anchors = choose_phase_scenes(manifest, role="development", roots_per_condition=2)
    with pytest.raises(ValueError, match="pressure cross"):
        expand_development_pressure_cross(manifest, anchors)
