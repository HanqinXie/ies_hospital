"""Audited 0–600-minute comparisons keep common support and unknown explicit."""
from __future__ import annotations

import copy

import pytest


def _targets(minutes, *, temperature=20.0):
    values = [0.0] * 19
    values[1] = minutes
    values[14] = temperature
    return values


def _row(scene, policy, targets, *, full=True):
    if not full:
        targets = [None] * 19
        targets[0] = 1.0
    return {"scenario_id": scene, "policy_id": policy,
            "status": "completed" if full else "model_constraint_infeasible",
            "full_observation": {"targets": targets,
                                 "target_mask": [value is not None for value in targets],
                                 "full_horizon": full}}


def _inputs():
    plan = {"scenario_ids": ["s1", "s2"], "score_windows": {"full": [0.0, 600.0]},
            "policy_specs": [{"policy_id": name} for name in
                             ("secure_milp", "candidate_a", "candidate_b")]}
    rows = [_row("s1", "secure_milp", _targets(10)),
            _row("s1", "candidate_a", _targets(9)),
            _row("s1", "candidate_b", _targets(11, temperature=21)),
            _row("s2", "secure_milp", _targets(10)),
            _row("s2", "candidate_a", _targets(0), full=False),
            _row("s2", "candidate_b", _targets(0))]
    audit = {"unknown_full_pair_ids": ["s2/candidate_a"]}
    return plan, {"results": rows}, audit


def test_full_window_common_support_p0_and_unknown_null():
    from tools.policy_search.full_window import compare_full_window

    plan, summary, audit = _inputs()
    result = compare_full_window(plan, summary, audit)
    assert result["score_field"] == "full_observation"
    assert result["registered_case_ids"] == ["s1", "s2"]
    assert result["all_policy_common_complete_case_ids"] == ["s1"]
    assert result["all_policy_common_unknown_case_ids"] == ["s2"]
    assert result["complete_paired_case_ids_by_policy"]["candidate_a"] == ["s1"]
    assert result["complete_paired_case_ids_by_policy"]["candidate_b"] == ["s1", "s2"]
    assert result["metrics"]["candidate_a"]["individual_cases"]["s2"]["delta_targets"][1] is None
    assert result["metrics"]["candidate_a"]["individual_cases"]["s2"]["complete_paired_window"] is False
    assert result["metrics_on_all_policy_common_cases"]["candidate_b"]["registered_case_count"] == 1
    assert result["p0_pareto_ids_on_all_policy_common_cases"] == ["candidate_a"]
    assert result["cost_cannot_offset_P0"] is True
    assert result["clinical_exchange_rate"] is None


def test_full_window_refuses_mask_or_audit_unknown_mismatch():
    from tools.policy_search.full_window import compare_full_window

    plan, summary, audit = _inputs()
    bad = copy.deepcopy(summary)
    bad["results"][4]["full_observation"]["target_mask"][0] = False
    with pytest.raises(ValueError, match="mask"):
        compare_full_window(plan, bad, audit)
    with pytest.raises(ValueError, match="unknown_full_pair_ids"):
        compare_full_window(plan, summary, {"unknown_full_pair_ids": []})
    wrong_window = copy.deepcopy(plan)
    wrong_window["score_windows"]["full"] = [60.0, 600.0]
    with pytest.raises(ValueError, match="0.*600"):
        compare_full_window(wrong_window, summary, audit)


def test_batch_path_and_run_path_resolve_same_registered_batch(tmp_path):
    from tools.policy_search.full_window import resolve_batch

    run = tmp_path / "run"
    folder = run / "search/generation_1"
    folder.mkdir(parents=True)
    (folder / "registered_plan.json").write_text("{}", encoding="utf-8")
    assert resolve_batch(folder) == (run, folder)
    assert resolve_batch(run, "generation_1") == (run, folder)
    with pytest.raises(ValueError, match="batch"):
        resolve_batch(run)
