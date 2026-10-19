"""Focused checks for the calibration gate coverage evidence extractor."""

import pytest

from tools.policy_search.gate_coverage import (
    summarize_gate_raw, same_support_outcome, validate_common_support)


PORTS = ("gt_1", "gt_2", "gt_3", "grid_import", "diesel", "absorption_cooling_kW")


def _decision(probability, gate_rejected, targets, decision_time=15.0, feature_available=15.0):
    request = {"security": {"gt_loading_penalty_USD_per_kWh": .35}}
    if targets:
        request["targets"] = dict(zip(PORTS, [0.0] * 6))
    return {"decision_time_min": decision_time, "observation_time_min": decision_time,
            "history_end_min": decision_time,
            "feature_metadata": {"now": {"available_time_min": feature_available}},
            "requested_command": request, "permitted_command": {"gt_electric_kW": [0, 0, 0]},
            "command_generated": {"request": request},
            "policy_diagnostic": {"gate_mode": "confidence", "gate_threshold": .5,
                                  "intervention_probability": probability,
                                  "gate_rejected": gate_rejected,
                                  "strategy_fallback": False,
                                  "strategy_status": "confidence_gate_secure_identity" if gate_rejected
                                  else "neural_feedback_six_port_request"}}


def test_gate_counts_separate_model_score_reject_request_and_execution():
    raw = {"decisions": [_decision(.4, True, False), _decision(.7, False, True)],
           "events": [{"kind": "command_generated"}, {"kind": "command_generated"},
                      {"kind": "command_arrived"}, {"kind": "command_executed"},
                      {"kind": "command_superseded"}]}
    out = summarize_gate_raw(raw, "nn_gate_t050", .5)
    assert out["decision_count"] == 2
    assert out["saved_legal_observation_decision_count"] == 2
    assert out["model_score_available_count"] == 2
    assert out["score_below_own_threshold_count"] == 1
    assert out["gate_rejected_count"] == 1
    assert out["explicit_six_port_request_count"] == 1
    assert out["command_arrived_count"] == 1
    assert out["command_executed_count"] == 1
    assert out["command_superseded_count"] == 1
    raw["decisions"][0]["feature_metadata"]["now"]["available_time_min"] = 16.0
    assert summarize_gate_raw(raw, "nn_gate_t050", .5)["saved_legal_observation_decision_count"] == 1


def test_threshold_rejection_must_agree_with_logged_score():
    raw = {"decisions": [_decision(.4, False, True)], "events": []}
    with pytest.raises(ValueError, match="gate rejection"):
        summarize_gate_raw(raw, "nn_gate_t050", .5)


def test_same_support_outcome_keeps_unknown_null():
    names = ["P0_failure", "P0_failure_minutes", "P0_e_ENS_kWh",
             "P0_h_ENS_kWh", "P0_c_ENS_kWh", "actual_energy_fee_USD"]
    metric = {"target_names": names,
              "individual_cases": {
                  "a": {"complete_paired_window": True, "candidate_status": "completed",
                        "baseline_status": "completed", "delta_targets": [0, 2, 1, 3, 4, -5]},
                  "b": {"complete_paired_window": False, "candidate_status": "infeasible",
                        "baseline_status": "completed", "delta_targets": None}}}
    out = same_support_outcome(metric, ["a"], ["a", "b"])
    assert out["same_support_case_ids"] == ["a"]
    assert out["unknown_case_ids"] == ["b"]
    assert out["delta_on_same_support"]["P0_failure_minutes"] == 2
    assert out["delta_on_same_support"]["actual_energy_fee_USD"] == -5
    assert out["per_case"]["b"]["delta"] is None


def test_audited_common_support_order_does_not_change_case_membership():
    validate_common_support(["b", "a"], ["a", "b"])
    with pytest.raises(ValueError, match="common support"):
        validate_common_support(["a"], ["a", "b"])
