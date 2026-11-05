"""Read-only delivery preflight fixtures; no fixture is a v6 result."""
from __future__ import annotations

import json
import hashlib

import pytest

from tools.policy_redesign import delivery_preflight
from tools.policy_redesign import select_develop
from hospital_ies.dataset_manifest import digest
from tools.control_improvement.ranking import TARGET_NAMES


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_final_chain_rejects_development_reselection_and_stale_review_sha(
        tmp_path, monkeypatch):
    run = tmp_path / "run"
    basis_path = run / "selection/SELECTION_BASIS.json"
    freeze_path = run / "selection/FROZEN_SELECTION.json"
    selection_plan_path = run / "selection/registered_plan.json"
    development_plan_path = run / "development/registered_plan.json"
    train_audit_path = run / "train/AUDIT_V6.json"
    selection_audit_path = run / "selection/AUDIT_V6.json"
    development_audit_path = run / "development/AUDIT_V6.json"
    manifest_path = run / "new_data/global_manifest.json"
    for path in (selection_plan_path, development_plan_path, train_audit_path,
                 selection_audit_path, development_audit_path, manifest_path):
        _write(path, {"fixture": path.name})
    basis = select_develop.signed({
        "schema": select_develop.BASIS_SCHEMA,
        "train_audit_sha256": _sha(train_audit_path),
        "new_manifest_sha256": _sha(manifest_path),
        "selection_registered_before_outcomes": True,
        "final_blind_opened": False,
    })
    _write(basis_path, basis)
    frozen = select_develop.signed({
        "schema": select_develop.FREEZE_SCHEMA,
        "selection_basis_path": str(basis_path.resolve()),
        "selection_basis_sha256": _sha(basis_path),
        "selection_plan_path": str(selection_plan_path.resolve()),
        "selection_plan_sha256": _sha(selection_plan_path),
        "selection_audit_path": str(selection_audit_path.resolve()),
        "selection_audit_sha256": _sha(selection_audit_path),
        "selection_raw_sha_index_digest": "selection-raw-index",
        "manifest_sha256": _sha(manifest_path),
        "selected_policy_id": "secure_milp",
        "ranked_policies": [{"policy_id": "secure_milp"}],
        "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"}],
        "source_sha256": {"policy.py": "bound"}, "model_bindings": {},
        "development_outcomes_read": False, "final_blind_opened": False,
    })
    _write(freeze_path, frozen)
    development_plan = {
        "phase": "development", "scenario_ids": ["development_0"],
        "policy_specs": frozen["policy_specs"],
        "manifest_sha256": frozen["manifest_sha256"],
        "preference_sha256": "preference-bound",
        "model_bindings": {}, "source_sha256": frozen["source_sha256"],
    }
    frozen["preference_sha256"] = "preference-bound"
    _write(freeze_path, select_develop.signed({k: v for k, v in frozen.items()
                                               if k != "signature"}))
    monkeypatch.setattr(select_develop, "_verify_freeze",
                        lambda path: (select_develop._checked(
                            path, select_develop.FREEZE_SCHEMA),
                            {"scenario_ids": ["selection_0"]},
                            {"scenarios": [
                                {"id": "selection_0", "experiment_role": "selection",
                                 "root_family_id": "selection_root"},
                                {"id": "development_0", "experiment_role": "development",
                                 "root_family_id": "development_root"}]}))
    monkeypatch.setattr(delivery_preflight, "verify_batch",
                        lambda path: (development_plan, {}))
    monkeypatch.setattr(select_develop, "phase_panel",
                        lambda manifest, phase: [phase + "_0"])
    audits = {
        "train": {"audit_path": str(train_audit_path.resolve()),
                  "audit_sha256": _sha(train_audit_path)},
        "selection": {"audit_path": str(selection_audit_path.resolve()),
                      "audit_sha256": _sha(selection_audit_path),
                      "plan_path": str(selection_plan_path.resolve()),
                      "plan_sha256": _sha(selection_plan_path),
                      "raw_sha_index_digest": "selection-raw-index"},
        "development": {"audit_path": str(development_audit_path.resolve()),
                        "audit_sha256": _sha(development_audit_path),
                        "plan_path": str(development_plan_path.resolve()),
                        "plan_sha256": _sha(development_plan_path),
                        "raw_sha_index_digest": "development-raw-index"},
    }
    review_path = run / "development/DEVELOPMENT_REVIEW.json"
    review = select_develop.signed({
        "schema": select_develop.DEVELOPMENT_SCHEMA,
        "selected_policy_id_before_development": "secure_milp",
        "selection_freeze_path": str(freeze_path.resolve()),
        "selection_freeze_sha256": _sha(freeze_path),
        "development_plan_path": str(development_plan_path.resolve()),
        "development_plan_sha256": _sha(development_plan_path),
        "development_audit_path": str(development_audit_path.resolve()),
        "development_audit_sha256": _sha(development_audit_path),
        "development_raw_sha_index_digest": "development-raw-index",
        "selection_not_recomputed_from_development": True,
        "final_blind_opened": False,
    })
    _write(review_path, review)
    assert delivery_preflight.verify_final_chain(run, audits)["selected_policy_id"] == "secure_milp"
    review["selected_policy_id_before_development"] = "v6_outcome_nn_best"
    _write(review_path, select_develop.signed({k: v for k, v in review.items()
                                               if k != "signature"}))
    with pytest.raises(ValueError, match="selected|reselection"):
        delivery_preflight.verify_final_chain(run, audits)
    review["selected_policy_id_before_development"] = "secure_milp"
    review["development_audit_sha256"] = "stale"
    _write(review_path, select_develop.signed({k: v for k, v in review.items()
                                               if k != "signature"}))
    with pytest.raises(ValueError, match="audit SHA"):
        delivery_preflight.verify_final_chain(run, audits)


def _metric(complete, unknown):
    return {"registered_case_count": 30, "complete_paired_case_count": complete,
            "unknown_case_ids": unknown,
            "target_names": list(TARGET_NAMES),
            "observable_paired_count_by_target": {name: complete for name in TARGET_NAMES},
            "new_P0_failure_cases": None if complete == 0 else 0,
            "resolved_P0_failure_cases": None if complete == 0 else 0,
            "P0_failure_minutes_total_delta": None if complete == 0 else -2.0,
            "P0_e_ENS_kWh_delta": None if complete == 0 else -0.1,
            "P0_h_ENS_kWh_delta": None if complete == 0 else 0.0,
            "P0_c_ENS_kWh_delta": None if complete == 0 else 0.0,
            "total_delta_by_target": [None] * 19 if complete == 0 else
            [0.0, -2.0, -0.1] + [0.0] * 15 + [8.0]}


def test_metric_tables_cover_19_primary_full_and_preserve_null_unknown():
    worst = {name: None for name in TARGET_NAMES}
    worst[TARGET_NAMES[1]] = {"scenario_id": "case_7", "candidate_minus_secure": 0.5,
                              "candidate_value": 7.0, "secure_value": 6.5}
    audit = {"target_names": list(TARGET_NAMES), "registered_pair_count": 60,
             "policy_comparisons": {"secure_milp": {"kind": "secure",
                                                   "primary": _metric(30, []),
                                                   "full": _metric(30, []),
                                                   "worst_case_by_target": worst,
                                                   "full_worst_case_by_target": worst},
                                    "candidate": {"kind": "v6_feedback",
                                                  "primary": _metric(29, ["case_29"]),
                                                  "full": _metric(0, [f"case_{i}" for i in range(30)]),
                                                  "worst_case_by_target": worst,
                                                  "full_worst_case_by_target":
                                                  {name: None for name in TARGET_NAMES}}},
             "subgroups": {"by_condition": [], "by_root": [], "by_pressure_point": []}}
    metrics, subgroups = delivery_preflight.compact_audit_tables(audit, "selection")
    assert len(metrics) == 2 * 2 * 19
    assert subgroups == []
    p0 = next(r for r in metrics if r["policy_id"] == "candidate" and
              r["window"] == "primary" and r["target_name"] == TARGET_NAMES[1])
    assert p0["total_delta"] == -2.0
    assert p0["worst_case_id"] == "case_7"
    assert p0["unknown_case_count"] == 1
    full = next(r for r in metrics if r["policy_id"] == "candidate" and
                r["window"] == "full" and r["target_name"] == TARGET_NAMES[1])
    assert full["total_delta"] is None and full["worst_case_delta"] is None
    assert full["unknown_case_count"] == 30
    csv = delivery_preflight._csv([full])
    assert ",null," in csv


def test_case_rows_keep_each_worst_and_unknown_delta_visible():
    metric = _metric(1, ["case_2"])
    metric["individual_cases"] = {
        "case_1": {"candidate_status": "complete", "baseline_status": "complete",
                   "complete_paired_window": True,
                   "delta_targets": [0.0, 1.5] + [0.0] * 17,
                   "candidate_targets": [0.0, 3.0] + [0.0] * 17,
                   "baseline_targets": [0.0, 1.5] + [0.0] * 17},
        "case_2": {"candidate_status": "truncated", "baseline_status": "complete",
                   "complete_paired_window": False,
                   "delta_targets": [None] * 19,
                   "candidate_targets": [None] * 19,
                   "baseline_targets": [0.0] * 19}}
    audit = {"policy_comparisons": {"candidate": {"primary": metric, "full": metric}}}
    rows = delivery_preflight.compact_case_rows(audit, "development")
    assert len(rows) == 4
    complete = next(r for r in rows if r["window"] == "primary" and
                    r["scenario_id"] == "case_1")
    unknown = next(r for r in rows if r["window"] == "primary" and
                   r["scenario_id"] == "case_2")
    assert complete["P0_failure_minutes_delta"] == 1.5
    assert unknown["P0_failure_minutes_delta"] is None
    assert unknown["complete_paired_window"] is False


def test_search_inventory_keeps_unfinished_generation_explicit(tmp_path):
    root = tmp_path / "search"
    plan = {"schema": "policy_redesign.adaptive_feedback_search.v1.plan",
            "scenario_ids": ["train_1"], "seeds": [6101], "min_generations": 4,
            "max_generations": 6, "search_source_sha256": "source_sha"}
    plan["signature"] = delivery_preflight.search_digest(plan)
    _write(root / "search_plan.json", plan)
    gen1 = root / "seed_6101" / "generation_0001"
    gen2 = root / "seed_6101" / "generation_0002"
    registered = {"schema": "policy_redesign.matched_full_feedback.v1",
                  "phase": "train", "scenario_ids": ["train_1"],
                  "registered_pair_count": 2,
                  "policy_specs": [{"policy_id": "secure_milp"},
                                   {"policy_id": "candidate"}]}
    registered["signature"] = digest(registered)
    _write(gen1 / "registered_plan.json", registered)
    _write(gen2 / "registered_plan.json", registered)
    evaluation = {"schema": "policy_redesign.adaptive_feedback_search.v1.evaluation",
                  "seed": 6101, "generation": 1, "scenario_ids": ["train_1"],
                  "registered_pair_count": 2,
                  "candidate_order": ["candidate"],
                  "candidates": [{"policy_id": "candidate", "family": "port_residual",
                                  "priority_key": [0, 0, 0],
                                  "complete_paired_case_count": 1,
                                  "unknown_case_ids": [],
                                  "primary_metrics": {"new_P0_failure_cases": 0,
                                                      "P0_failure_minutes_total_delta": -1.0}}]}
    evaluation["signature"] = delivery_preflight.search_digest(evaluation)
    _write(gen1 / "evaluation.json", evaluation)
    generations, errors = delivery_preflight.inventory_search(root)
    assert not errors
    assert [r["status"] for r in generations] == ["evaluated", "registered_without_evaluation"]
    assert generations[0]["best_policy_id"] == "candidate"
    assert generations[1]["best_policy_id"] is None
    evaluation["signature"] = "wrong"
    _write(gen1 / "evaluation.json", evaluation)
    _, errors = delivery_preflight.inventory_search(root)
    assert any("signature" in x for x in errors)


def test_python_closure_reports_missing_local_import(tmp_path):
    package = tmp_path / "tools"
    (package / "__init__.py").parent.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "present.py").write_text(
        "from tools.missing import thing\n", encoding="utf-8")
    src = tmp_path / "src" / "hospital_ies"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    closure = delivery_preflight.inspect_python_closure(tmp_path)
    assert closure["source_python_count"] == 3
    assert "tools.missing" in closure["missing_local_modules"]
    (package / "missing.py").write_text("thing = 1\n", encoding="utf-8")
    closure = delivery_preflight.inspect_python_closure(tmp_path)
    assert closure["missing_local_modules"] == []


def test_missing_package_materials_are_not_marked_ready(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    materials = delivery_preflight.inspect_package_materials(
        run, workspace_root=tmp_path, v5_run=tmp_path / "v5",
        formal_model_dir=tmp_path / "missing_model", v6_pair=None)
    assert materials["global_review"]["ready"] is False
    assert materials["selected_raw_reproduction"]["ready"] is False
    assert any("POLICY_REDESIGN_REPORT.md" in x for x in
               materials["global_review"]["missing"])
    assert any("v6 matched raw pair" in x for x in
               materials["selected_raw_reproduction"]["missing"])
    assert any("v6 current broad train unknown pair" in x for x in
               materials["selected_raw_reproduction"]["missing"])


def test_train_audit_preflight_requires_all_30_complete_five_pressure_roots(
        tmp_path, monkeypatch):
    scenes = []
    for condition in range(6):
        for root in range(5):
            for pressure in range(5):
                scenes.append({"id": f"t_{condition}_{root}_{pressure}",
                               "experiment_role": "train", "is_final_blind": False,
                               "condition_id": f"condition_{condition}",
                               "root_family_id": f"root_{condition}_{root}",
                               "pressure_point_index": pressure})
    plan = {"phase": "train", "scenario_ids": [s["id"] for s in scenes],
            "policy_specs": [{"policy_id": f"policy_{i}"} for i in range(5)]}
    manifest = {"scenarios": scenes}
    monkeypatch.setattr(delivery_preflight, "verify_saved_audit",
                        lambda *args: [])
    monkeypatch.setattr(delivery_preflight, "verify_batch",
                        lambda *args: (plan, manifest))
    monkeypatch.setattr(delivery_preflight, "compact_audit_tables",
                        lambda *args: ([], []))
    monkeypatch.setattr(delivery_preflight, "compact_case_rows",
                        lambda *args: [])
    audit = {"schema": delivery_preflight.AUDIT_SCHEMA, "phase": "train",
             "plan_path": str(tmp_path / "train/registered_plan.json"),
             "plan_sha256": "fixture_plan_sha", "summary_path": "fixture_summary",
             "summary_sha256": "fixture_summary_sha", "registered_pair_count": 750,
             "recomputed_pair_count": 750, "unknown_primary_pair_ids": [],
             "unknown_full_pair_ids": [],
             "subgroups": {
                 "by_condition": [{} for _ in range(6)],
                 "by_root": [{"complete_five_pressure_panel": True,
                              "registered_pressure_count": 5,
                              "scenario_ids": [s["id"] for s in scenes
                                               if s["root_family_id"] == root]}
                             for root in sorted({s["root_family_id"] for s in scenes})],
                 "by_pressure_point": [{} for _ in range(5)]}}
    audit_path = tmp_path / "audit.json"
    _write(audit_path, audit)
    record, _, problems = delivery_preflight._audit_evidence(audit_path, "train")
    assert not problems and record["root_count"] == 30
    audit["subgroups"]["by_root"][0]["complete_five_pressure_panel"] = False
    _write(audit_path, audit)
    record, _, problems = delivery_preflight._audit_evidence(audit_path, "train")
    assert record is None
    assert any("five-pressure" in problem for problem in problems)


def test_source_review_writes_sha_inventory_and_new_file_unified_diff(tmp_path):
    root = tmp_path / "workspace"
    run = root / "outputs" / "v6"
    source = root / "tools" / "policy_redesign" / "example.py"
    source.parent.mkdir(parents=True)
    source.write_text("answer = 42\n", encoding="utf-8")
    test = root / "tests" / "test_v6_example.py"
    test.parent.mkdir(parents=True)
    test.write_text("assert True\n", encoding="utf-8")
    run.mkdir(parents=True)
    inventory = delivery_preflight.build_source_review(run, workspace_root=root,
        write_outputs=True)
    assert len(inventory["new_v6_files"]) == 2
    assert all(len(row["sha256"]) == 64 for row in inventory["new_v6_files"])
    diff = (run / "review/V6_SOURCE_UNIFIED_DIFF.patch").read_text(encoding="utf-8")
    assert "--- /dev/null" in diff
    assert "+++ b/tools/policy_redesign/example.py" in diff
    assert "+answer = 42" in diff
    assert inventory["physical_core_snapshot_check"]["status"] == "missing_manifest"


def test_command_inventory_keeps_failed_exit_and_raw_log_refs(tmp_path):
    run = tmp_path / "run"
    logs = run / "logs"
    logs.mkdir(parents=True)
    receipt = {"name": "failed_fit", "command": ["python", "fit.py"],
               "started_utc": "2026-10-16T00:00:00+00:00",
               "finished_utc": "2026-10-16T00:00:05+00:00",
               "elapsed_seconds": 5.0, "exit_code": 1}
    _write(logs / "failed_fit.command.json", receipt)
    (logs / "failed_fit.stdout.log").write_text("starting\n", encoding="utf-8")
    (logs / "failed_fit.stderr.log").write_text("error\n", encoding="utf-8")
    (run / "commands_and_exit_codes.txt").write_text(
        json.dumps(receipt) + "\n", encoding="utf-8")
    record = delivery_preflight.inventory_command_receipts(run, write_outputs=True)
    assert record["unique_command_count"] == 1
    assert record["failed_command_count"] == 1
    failure = record["failed_commands"][0]
    assert failure["exit_code"] == 1
    assert len(failure["receipt_sources"]) == 2
    assert {row["stream"] for row in failure["raw_logs"]} == {"stdout", "stderr"}
    assert (run / "review/FAILED_COMMANDS.csv").is_file()


def test_historical_v5_weak_teacher_index_is_seen_only(tmp_path):
    root = tmp_path / "workspace"
    old = root / "outputs" / "v5"
    run = root / "outputs" / "v6"
    run.mkdir(parents=True)
    plan = {"signature": "v5_signed", "scenario_ids": ["old_1", "old_2"],
            "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"},
                             {"policy_id": "blend_overload", "kind": "feedback"},
                             {"policy_id": "nn_ungated", "kind": "neural_feedback"}]}
    _write(old / "development/registered_plan.json", plan)
    pairs = []
    for sid in plan["scenario_ids"]:
        for pid in ("secure_milp", "blend_overload", "nn_ungated"):
            raw = old / "development/results" / sid / pid / "trajectory.json.gz"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes((sid + pid).encode())
            score = {"full_horizon": True, "targets": [0.0] * 19}
            pairs.append({"scenario_id": sid, "policy_id": pid,
                          "status": "completed", "primary_score": score,
                          "full_observation": score,
                          "attempts": [{"raw_trajectory_path":
                                        raw.relative_to(old).as_posix(),
                                        "raw_trajectory_sha256":
                                        delivery_preflight._sha(raw)}]})
    ledger = {"batches": [{"plan_path": "development/registered_plan.json",
                            "pairs": pairs, "scenario_ids": plan["scenario_ids"]}]}
    _write(old / "review/POLICY_PAIR_LEDGER.json", ledger)
    for name in ("policy.py", "neural.py"):
        path = root / "tools/policy_search" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# historical source\n", encoding="utf-8")
    model = old / "models/gru_agg1_blend_overload_seed5102"
    _write(model / "metadata.json", {"old": True})
    (model / "model.pt").write_bytes(b"old weights")
    index = delivery_preflight.build_historical_v5_controls_index(
        run, v5_run=old, workspace_root=root, expected_case_count=2,
        write_outputs=True)
    assert index["evidence_role"] == "historical_seen_development_regression_only"
    assert index["not_new_v6_selection_or_development"] is True
    assert {x["policy_id"] for x in index["policies"]} == {
        "secure_milp", "blend_overload", "nn_ungated"}
    assert len(index["external_raw_sha_index"]) == 6
    assert (run / "review/HISTORICAL_V5_CONTROLS_INDEX.json").is_file()
