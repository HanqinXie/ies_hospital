"""Unknown-aware high-priority paired comparisons."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _result(scene, policy, *, failure=0, minutes=0, p0_e=0, fee=10, observed=True):
    targets = [float(failure), float(minutes)] + [0.0] * 12 + [24.0] * 3 + [0.0, float(fee)]
    targets[2] = float(p0_e)
    if not observed:
        targets = [None] * 19
    return {"scenario_id": scene, "policy_id": policy,
            "primary_score": {"full_horizon": observed, "targets": targets,
                              "target_mask": [observed] * 19},
            "status": "completed" if observed else "model_constraint_infeasible"}


def test_p0_regression_not_offset_by_cost():
    from tools.policy_search.preference import paired_metrics, search_key

    rows = [_result("a", "secure_milp"),
            _result("a", "cheap", failure=1, minutes=15, p0_e=4, fee=0)]
    candidate = paired_metrics(rows, "cheap")
    secure = paired_metrics(rows, "secure_milp")
    assert candidate["new_P0_failure_cases"] == 1
    assert candidate["P0_e_ENS_kWh_delta"] == 4
    assert search_key(candidate) > search_key(secure)


def test_unknown_kept_null_and_denominators_distinct():
    from tools.policy_search.preference import paired_metrics

    rows = [_result("a", "secure_milp"), _result("a", "candidate"),
            _result("b", "secure_milp"), _result("b", "candidate", observed=False)]
    value = paired_metrics(rows, "candidate")
    assert value["registered_case_count"] == 2
    assert value["complete_paired_case_count"] == 1
    assert value["unknown_case_ids"] == ["b"]
    assert value["individual_cases"]["b"]["delta_targets"] == [None] * 19
