"""Focused contract tests for the read-only v5 action/execution comparison."""

import pytest

from tools.policy_search.action_execution_compare import (
    align_dispatch, summarize_chain, validate_audit_file_hashes)


PORTS = ("gt_1", "gt_2", "gt_3", "grid_import", "diesel", "absorption_cooling_kW")


def _action(gt1):
    return {"gt_electric_kW": [float(gt1), 0.0, 0.0],
            "grid_import_kW": 0.0, "diesel_kW": 0.0,
            "absorption_cooling_kW": 0.0}


def test_chain_separates_explicit_request_projection_execution_and_gate_refusal():
    raw = {
        "decisions": [
            {"requested_command": {"targets": dict(zip(PORTS, [3, 0, 0, 0, 0, 0]))},
             "command_generated": {"request": {"targets": dict(zip(PORTS, [3, 0, 0, 0, 0, 0]))},
                                   "dispatch": _action(2)},
             "policy_diagnostic": {"current_context": {"current_plan_targets_kW": [0] * 6},
                                   "strategy_fallback": False,
                                   "gate_rejected": False}},
            {"requested_command": {"security": {"gt_loading_penalty_USD_per_kWh": .35}},
             "command_generated": {"request": {"security": {"gt_loading_penalty_USD_per_kWh": .35}},
                                   "dispatch": _action(0)},
             "policy_diagnostic": {"current_context": {"current_plan_targets_kW": [0] * 6},
                                   "strategy_fallback": True,
                                   "strategy_status": "secure_gate_refusal",
                                   "gate_reason": "no_reliable_service_improvement"}},
        ],
        "events": [{"kind": "command_generated"}, {"kind": "command_generated"},
                   {"kind": "command_arrived"}, {"kind": "command_executed"},
                   {"kind": "command_superseded"}],
    }
    out = summarize_chain(raw)
    assert out["decision_count"] == 2
    assert out["explicit_target_request_count"] == 1
    assert out["request_vs_current_secure_gt1kW_count"] == 1
    assert out["projected_plan_vs_current_secure_gt1kW_count"] == 1
    assert out["current_secure_projection_available_count"] == 2
    assert out["command_arrived_count"] == 1
    assert out["command_executed_count"] == 1
    assert out["command_superseded_count"] == 1
    assert out["strategy_fallback_count"] == 1
    assert out["gate_rejected_count"] == 1


def test_aligned_dispatch_keeps_unobserved_tail_unknown():
    candidate = [{"start_minute": 0, "end_minute": 5, "action": _action(0)},
                 {"start_minute": 5, "end_minute": 8, "action": _action(2)}]
    secure = [{"start_minute": 0, "end_minute": 10, "action": _action(0)}]
    full = align_dispatch(candidate, secure, (0, 10))
    assert full["aligned_minutes"] == 8
    assert full["unobserved_minutes"] == 2
    assert full["divergence_minutes_gt1kW_on_aligned_prefix"] == 3
    assert full["first_divergence_minute_gt1kW"] == 5
    assert full["max_abs_six_port_difference_kW_on_aligned_prefix"] == 2
    assert full["complete_alignment"] is False
    main = align_dispatch(candidate, secure, (5, 8))
    assert main["complete_alignment"] is True
    assert main["divergence_minutes_gt1kW_on_aligned_prefix"] == 3


def test_audited_attempt_binds_both_result_and_raw_bytes():
    audit = {"attempts": [{"pair_id": "scene/policy", "attempt": "attempt_0001",
                           "file_sha256": {"result.json": "result-sha",
                                           "trajectory.json.gz": "raw-sha"}}]}
    validate_audit_file_hashes(audit, "scene", "policy", "attempt_0001",
                               "result-sha", "raw-sha")
    with pytest.raises(ValueError, match="audit.*SHA"):
        validate_audit_file_hashes(audit, "scene", "policy", "attempt_0001",
                                   "changed-result", "raw-sha")
