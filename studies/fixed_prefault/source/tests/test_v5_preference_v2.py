"""Audited v5 priority vector, including recovered P0 cases and temperature tails."""
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


def _row(sid, pid, *, failure=0, minutes=0, p0_e=0, temp=25, cost=10,
         complete=True, signature="plan"):
    targets = [float(failure), float(minutes)] + [0.0] * 12 + [float(temp)] * 3 + [0.0, float(cost)]
    targets[2] = float(p0_e)
    if not complete:
        targets = [None] * 19
    return {"scenario_id": sid, "policy_id": pid, "plan_signature": signature,
            "phase": "train", "terminal": True,
            "status": "completed" if complete else "model_constraint_infeasible",
            "binding": {"passed": True, "checks": {"scenario_id": True},
                        "initial_state_sha256": "initial_" + sid,
                        "exogenous_stream_id": "stream_" + sid},
            "primary_score": {"full_horizon": complete, "targets": targets,
                              "target_mask": [v is not None for v in targets]}}


def _fixture(tmp_path, *, different_support=False):
    from tools.policy_search.preference_v2 import SOURCE_NAMES

    pref = tmp_path / "RESEARCH_PREFERENCE_v1.json"
    _write(pref, {"priority_0_vector": ["resolved_P0_failure_cases", "P0_max_temperature_C_case_delta"]})
    plan_path = tmp_path / "registered_plan.json"
    policy_ids = ["secure_milp", "cooler", "recoverer"]
    plan = {"schema": "policy_search.matched_full_feedback.v1", "phase": "train",
            "scenario_ids": ["a", "b"],
            "policy_specs": [{"policy_id": p, "kind": "secure" if p == "secure_milp" else "feedback"}
                             for p in policy_ids],
            "registered_pair_count": 6, "preference_path": str(pref),
            "preference_sha256": _sha(pref)}
    plan["signature"] = _digest(plan)
    _write(plan_path, plan)
    rows = []
    for sid in ("a", "b"):
        rows.append(_row(sid, "secure_milp", failure=int(sid == "a"),
                         minutes=20 if sid == "a" else 0, p0_e=1,
                         signature=plan["signature"]))
        rows.append(_row(sid, "cooler", failure=int(sid == "a"),
                         minutes=20 if sid == "a" else 0, temp=24,
                         p0_e=0, signature=plan["signature"]))
        rows.append(_row(sid, "recoverer", failure=0, minutes=0,
                         temp=26, cost=0,
                         complete=not (different_support and sid == "b"),
                         signature=plan["signature"]))
    summary_path = tmp_path / "summary.json"
    _write(summary_path, {"schema": "policy_search.matched_full_feedback.v1.summary",
                          "plan_signature": plan["signature"], "results": rows,
                          "registered_pair_count": 6, "recorded_pair_count": 6,
                          "terminal_pair_count": 6,
                          "matched_initial_state_and_stream": True})
    old_freeze = tmp_path / "SEARCH_ALGORITHM_SOURCE_FREEZE.json"
    _write(old_freeze, {"schema": "policy_search.algorithm_source_freeze.v1",
                        "source_sha256": {name: _sha(ROOT / name) for name in SOURCE_NAMES[:-1]},
                        "preference_sha256": _sha(pref)})
    audit_path = tmp_path / "AUDIT.json"
    _write(audit_path, {"schema": "policy_search.evidence_audit.v1", "passed": True,
                        "errors": [], "plan_sha256": _sha(plan_path),
                        "plan_signature": plan["signature"], "summary_sha256": _sha(summary_path),
                        "source_freeze_sha256": _sha(old_freeze),
                        "audit_source_sha256": _sha(ROOT / "tools/policy_search/audit.py"),
                        "registered_pair_count": 6,
                        "matched_initial_state_and_stream": True})
    revision = tmp_path / "PREFERENCE_V2_SOURCE_FREEZE.json"
    _write(revision, {"schema": "policy_search.preference_v2_source_freeze.v1",
                      "registered_before_selection_development_outcomes": True,
                      "post_generation_1_outcome_correction": True,
                      "source_sha256": {name: _sha(ROOT / name) for name in SOURCE_NAMES},
                      "audit_source_sha256": _sha(ROOT / "tools/policy_search/audit.py"),
                      "research_preference_sha256": _sha(pref),
                      "v1_source_freeze_sha256": _sha(old_freeze)})
    return plan_path, summary_path, audit_path, revision, old_freeze


def test_audited_p0_pareto_keeps_recovery_and_temperature_tradeoffs(tmp_path):
    from tools.policy_search.preference_v2 import analyze_audited_batch

    analysis = analyze_audited_batch(*_fixture(tmp_path))
    assert analysis["metrics"]["recoverer"]["resolved_P0_failure_cases"] == 1
    assert analysis["metrics"]["recoverer"]["largest_case_P0_temperature_increase_C"] == 1
    assert analysis["metrics"]["cooler"]["largest_case_P0_temperature_delta_C"] == -1
    assert set(analysis["p0_pareto_ids"]) == {"cooler", "recoverer"}
    assert analysis["clinical_exchange_rate"] is None


def test_unmatched_complete_support_is_not_ranked_as_dominance(tmp_path):
    from tools.policy_search.preference_v2 import analyze_audited_batch

    analysis = analyze_audited_batch(*_fixture(tmp_path, different_support=True))
    assert analysis["metrics"]["recoverer"]["complete_paired_case_ids"] == ["a"]
    assert analysis["metrics"]["cooler"]["complete_paired_case_ids"] == ["a", "b"]
    assert analysis["p0_pareto_ids"] == ["cooler", "recoverer"]
    assert analysis["incomparable_due_to_support"] == [["cooler", "recoverer"]]


def test_resolved_failure_count_is_an_independent_pareto_dimension():
    from tools.policy_search.preference_v2 import p0_dominates_v2

    a = {"complete_paired_case_ids": ["a"], "new_P0_failure_cases": 0,
         "resolved_P0_failure_cases": 1, "P0_failure_minutes_total_delta": 0,
         "largest_case_P0_failure_minutes_increase": 1,
         "P0_e_ENS_kWh_delta": 0, "P0_h_ENS_kWh_delta": 0, "P0_c_ENS_kWh_delta": 0,
         "largest_case_P0_e_ENS_increase": 0, "largest_case_P0_h_ENS_increase": 0,
         "largest_case_P0_c_ENS_increase": 0,
         "largest_case_P0_temperature_delta_C": 0}
    b = dict(a, resolved_P0_failure_cases=0,
             largest_case_P0_failure_minutes_increase=0)
    assert not p0_dominates_v2(a, b)
    assert not p0_dominates_v2(b, a)


def test_summary_modified_after_audit_is_rejected(tmp_path):
    from tools.policy_search.preference_v2 import analyze_audited_batch

    paths = _fixture(tmp_path)
    summary = json.loads(paths[1].read_text(encoding="utf-8"))
    summary["results"][1]["primary_score"]["targets"][14] = 100.0
    _write(paths[1], summary)
    with pytest.raises(ValueError, match="summary_sha256"):
        analyze_audited_batch(*paths)


def test_unpassed_binding_is_rejected_even_with_updated_summary_hash(tmp_path):
    from tools.policy_search.preference_v2 import analyze_audited_batch

    paths = _fixture(tmp_path)
    summary = json.loads(paths[1].read_text(encoding="utf-8"))
    summary["results"][1]["binding"]["passed"] = False
    _write(paths[1], summary)
    audit = json.loads(paths[2].read_text(encoding="utf-8"))
    audit["summary_sha256"] = _sha(paths[1])
    _write(paths[2], audit)
    with pytest.raises(ValueError, match="binding.passed"):
        analyze_audited_batch(*paths)


def test_v2_source_change_is_rejected(tmp_path):
    from tools.policy_search.preference_v2 import analyze_audited_batch

    paths = _fixture(tmp_path)
    revision = json.loads(paths[3].read_text(encoding="utf-8"))
    revision["source_sha256"]["tools/policy_search/preference_v2.py"] = "0" * 64
    _write(paths[3], revision)
    with pytest.raises(ValueError, match="v2_source_sha256"):
        analyze_audited_batch(*paths)
