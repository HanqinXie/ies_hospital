"""Fail-closed review for the separately registered mode-v2 train batch."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "outputs/v5_policy_search_20261015_run01"


def _batch(tmp_path, *, missing_result=False, unfinished=False):
    from tools.policy_search.mode_v2_review import precheck_terminal

    ids = [f"train_{i}" for i in range(6)]
    policies = ["secure_milp", "prepare_only", "recovery_only",
                "prepare_only_mode_v2", "recovery_only_mode_v2"]
    plan = {"signature": "frozen-plan", "scenario_ids": ids,
            "policy_specs": [{"policy_id": pid} for pid in policies],
            "registered_pair_count": 30}
    rows = [{"scenario_id": sid, "policy_id": pid, "terminal": True}
            for sid in ids for pid in policies]
    if missing_result:
        rows.pop()
    summary = {"plan_signature": "frozen-plan", "registered_pair_count": 30,
               "recorded_pair_count": len(rows), "terminal_pair_count": len(rows),
               "results": rows}
    journal = tmp_path / "attempts.jsonl"
    with journal.open("w", encoding="utf-8") as stream:
        for row in rows:
            for event in ("started", "finished"):
                if unfinished and row is rows[-1] and event == "finished":
                    continue
                stream.write(json.dumps({"event": event,
                                         "scenario_id": row["scenario_id"],
                                         "policy_id": row["policy_id"],
                                         "attempt": "attempt_0001"}) + "\n")
    return precheck_terminal, plan, summary, journal


def test_terminal_precheck_refuses_partial_or_inflight_batch(tmp_path):
    check, plan, summary, journal = _batch(tmp_path, missing_result=True)
    with pytest.raises(ValueError, match="30|terminal|pair"):
        check(plan, summary, journal)
    check, plan, summary, journal = _batch(tmp_path, unfinished=True)
    with pytest.raises(ValueError, match="journal|in-flight"):
        check(plan, summary, journal)
    check, plan, summary, journal = _batch(tmp_path)
    assert check(plan, summary, journal)["terminal_pair_count"] == 30


def test_audit_compat_registration_discloses_actual_v2_binding():
    from tools.policy_search.mode_v2_review import (build_compat_registration,
                                                    verify_compat_registration)

    compat = build_compat_registration(RUN)
    assert compat["schema"] == "policy_search.offline_generation.v1"
    assert compat["audit_compatibility_only"] is True
    assert compat["actual_mode_v2_registration_sha256"]
    assert compat["actual_registration_source"] == "tools/policy_search/mode_v2_batch.py"
    verify_compat_registration(RUN, compat)
    altered = copy.deepcopy(compat)
    altered["actual_mode_v2_registration_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="signature|sidecar"):
        verify_compat_registration(RUN, altered)


def test_v1_and_v2_trace_identities_are_not_confused():
    from tools.policy_search.mode_v2_review import audit_mode_decisions

    base = {"P0_e_disconnected_kW": 0.00004,
            "P0_h_disconnected_kW": 0.0, "P0_c_disconnected_kW": 0.0,
            "availability_GT_1": 1.0, "grid_outage_elapsed_minutes": 0.0}
    broken = {**base, "availability_GT_1": 0.0}
    old = [{"features": base, "policy_diagnostic": {"feedback_mode": "disturbed"}}]
    revised = [
        {"features": base, "policy_diagnostic": {"feedback_mode": "normal",
                                                 "mode_detector_id": "policy_search.observed_mode.v2",
                                                 "mode_p0_solver_residual_tol_kW": 0.001}},
        {"features": broken, "policy_diagnostic": {"feedback_mode": "disturbed",
                                                   "mode_detector_id": "policy_search.observed_mode.v2",
                                                   "mode_p0_solver_residual_tol_kW": 0.001}},
        {"features": base, "policy_diagnostic": {"feedback_mode": "recovery",
                                                 "mode_detector_id": "policy_search.observed_mode.v2",
                                                 "mode_p0_solver_residual_tol_kW": 0.001}},
    ]
    assert audit_mode_decisions("prepare_only", old)["disturbed"] == 1
    assert audit_mode_decisions("prepare_only_mode_v2", revised) == {
        "normal": 1, "disturbed": 1, "recovery": 1}
    revised[0]["policy_diagnostic"].pop("mode_detector_id")
    with pytest.raises(ValueError, match="mode_detector_id"):
        audit_mode_decisions("prepare_only_mode_v2", revised)
