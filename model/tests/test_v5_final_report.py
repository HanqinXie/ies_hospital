"""No-solver contract tests for the gated v5 final report composer."""

from tools.policy_search.final_report import (
    missing_required_artifacts, recommendation_tier, format_sensitivity_cell,
    compose_report, validate_full_common_metrics, STATIC_HEADINGS)


NAMES = ["P0_failure", "P0_failure_minutes", "P0_e_ENS_kWh",
         "P0_h_ENS_kWh", "P0_c_ENS_kWh", *[f"other_{i}" for i in range(9)],
         "P0_max_temperature_C", "P1_max_temperature_C", "P2_max_temperature_C",
         "secondary_GT_trips", "actual_energy_fee_USD"]


def _metric(rows):
    return {"target_names": NAMES, "individual_cases": {
        sid: {"complete_paired_window": delta is not None,
              "delta_targets": delta,
              "candidate_targets": [0.0] * 19 if delta is not None else None,
              "baseline_targets": [0.0] * 19 if delta is not None else None}
        for sid, delta in rows.items()}}


def _delta(*, minutes=0.0, heat=0.0, fee=0.0):
    values = [0.0] * 19
    values[1], values[3], values[18] = minutes, heat, fee
    return values


def test_missing_development_artifacts_blocks_report(tmp_path):
    blockers = missing_required_artifacts(tmp_path)
    assert any("development/AUDIT.json" in x for x in blockers)
    assert any("review/RUN_ACCOUNTING.json" in x for x in blockers)
    assert not (tmp_path / "POLICY_SEARCH_REPORT.md").exists()


def test_positive_p0_case_is_only_manual_candidate():
    metric = _metric({"a": _delta(minutes=-10, fee=50),
                      "b": _delta(minutes=0, fee=0)})
    tier = recommendation_tier("new_policy", metric, metric, ["a", "b"])
    assert tier["code"] == "p0_improvement_candidate"
    assert "人工" in tier["label"]


def test_p0_harm_overrides_fee_saving():
    metric = _metric({"a": _delta(heat=2, fee=-1000)})
    tier = recommendation_tier("new_policy", metric, metric, ["a"])
    assert tier["code"] == "observed_p0_harm"
    assert "P0_h_ENS_kWh" in tier["reason"]


def test_unknown_prevents_positive_recommendation_and_stays_null():
    metric = _metric({"a": _delta(minutes=-10), "b": None})
    tier = recommendation_tier("new_policy", metric, metric, ["a", "b"])
    assert tier["code"] == "unknown_blocks_recommendation"
    group = {"registered_case_count": 2, "complete_paired_case_count": 1,
             "unknown_case_ids": ["b"],
             "paired_observable_count_by_target": [1] * 19,
             "sum_delta_on_observed": [-10.0] * 19}
    cell = format_sensitivity_cell(group, NAMES)
    assert "1/2" in cell and "未知1" in cell and "n=1/2" in cell


def test_partial_observation_not_rendered_as_zero():
    group = {"registered_case_count": 1, "complete_paired_case_count": 0,
             "unknown_case_ids": ["a"],
             "paired_observable_count_by_target": [0] * 19,
             "sum_delta_on_observed": [None] * 19}
    cell = format_sensitivity_cell(group, NAMES)
    assert "?" in cell
    assert "0/1" in cell


def test_full_common_metrics_may_omit_registered_unknown_case():
    own = {"secure_milp": _metric({"a": _delta(), "b": None}),
           "new_policy": _metric({"a": _delta(minutes=-1), "b": None})}
    common = {pid: {**metric,
                    "individual_cases": {"a": metric["individual_cases"]["a"]}}
              for pid, metric in own.items()}
    ids = validate_full_common_metrics(own, common,
                                       ["secure_milp", "new_policy"], ["a", "b"],
                                       ["a"])
    assert ids == ["a"]


def test_composer_renders_common_support_pressure_and_review_limit(tmp_path):
    design = tmp_path / "design"
    design.mkdir()
    (design / "POLICY_REPORT_PREDEV_DRAFT.md").write_text(
        "\n\n".join(f"## {heading}\n\n静态证据。" for heading in STATIC_HEADINGS),
        encoding="utf-8")
    names = NAMES
    secure = _metric({"a": _delta(), "b": _delta()})
    candidate = _metric({"a": _delta(minutes=-10), "b": _delta()})
    policies = ["secure_milp", "new_policy"]
    metrics = {"secure_milp": secure, "new_policy": candidate}
    group = {"registered_case_count": 1, "complete_paired_case_count": 1,
             "unknown_case_ids": [], "paired_observable_count_by_target": [1] * 19,
             "sum_delta_on_observed": [0.0] * 19}
    pressure = {f"C{i}": {str(p): {pid: group for pid in policies}
                           for p in range(5)} for i in range(6)}
    summary_rows = [{"scenario_id": sid, "policy_id": pid, "terminal": True,
                     "status": "completed", "primary_score": {"full_horizon": True},
                     "full_observation": {"full_horizon": True}}
                    for sid in ("a", "b") for pid in policies]
    evidence = {"run": tmp_path, "plan": {"registered_pair_count": 4},
                "policy_ids": policies, "registered_ids": ["a", "b"],
                "primary_common": ["a", "b"], "full_common": ["a", "b"],
                "target_names": names, "unknown_primary": [], "unknown_full": [],
                "summary": {"results": summary_rows},
                "v2": {"metrics": metrics},
                "full": {"metrics": metrics,
                         "metrics_on_all_policy_common_cases": metrics},
                "sensitivity": {"windows": {
                    "primary_score": {"by_condition_and_pressure": pressure},
                    "full_observation": {"by_condition_and_pressure": pressure}}},
                "fresh_accounting": {"totals": {
                    "registered_batches": 1, "registered_pairs": 4, "terminal_pairs": 4,
                    "completed_pairs": 4, "unknown_primary_pairs": 0,
                    "unknown_full_pairs": 0, "trajectory_attempts": 4,
                    "manifest_exogenous_specifications": 2,
                    "registered_major_policy_configurations": 2,
                    "optimizer_fit_directories": 1},
                    "cuda_optimizer_fits": {"verified_cuda_fits": 1},
                    "budgets": {"observed_use": {
                        "experimental_wall_hours": 1, "trajectory_attempts": 4,
                        "new_exogenous_specifications": 2,
                        "major_configurations": 2, "optimizer_fits": 1},
                        "limits": {"experimental_wall_hours": 96,
                                   "trajectory_attempts": 30000,
                                   "new_exogenous_specifications": 1500,
                                   "major_configurations": 80,
                                   "optimizer_fits": 240},
                        "within_observed_limits": {"experimental_wall_hours": True,
                                                   "trajectory_attempts": True,
                                                   "new_exogenous_specifications": True,
                                                   "major_configurations": True,
                                                   "optimizer_fits": True}},
                    "command_receipts": {"receipts": []}},
                "binding_sha256": {"plan": "abc"}}
    report = compose_report(evidence)
    assert "P0 改善研究候选（待人工复核）" in report
    assert "同根五压力" in report and "`C5`" in report
    assert "未核到无路径过滤" in report
    assert not (tmp_path / "POLICY_SEARCH_REPORT.md").exists()
