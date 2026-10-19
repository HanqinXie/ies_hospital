"""Secure-relative selection must not turn tiny prediction noise into action."""
import json

import numpy as np

from tools.reliability_first.selection import (
    ConservativeServiceGate,
    SupportAwarePredictor,
    build_current_policy,
)


class _Model:
    def __init__(self, prediction, *, support=None, q90=None, counts=None):
        self.prediction = np.asarray(prediction, dtype=float)
        self.support = [True] * len(prediction) if support is None else support
        self.q90 = [0.0] * 19 if q90 is None else q90
        self.counts = [20] * 19 if counts is None else counts
        self.metadata = {"scale_kWh": np.ones((4, 3)).tolist()}

    def predict_with_support(self, x):
        assert len(x) == len(self.prediction)
        return {
            "delta": self.prediction.copy(),
            "feature_range_supported": self.support,
            "training_observed_counts": self.counts,
            "validation_observed_counts": self.counts,
            "validation_abs_error_q90": self.q90,
        }


def _predictions(*changes):
    matrix = np.zeros((len(changes) + 1, 19))
    for index, changes_for_candidate in enumerate(changes, 1):
        for target, value in changes_for_candidate.items():
            matrix[index, target] = value
    return matrix


def _decide(model):
    predictor = SupportAwarePredictor(model)
    predicted = predictor.predict(np.zeros((len(model.prediction), 2)))
    candidates = [{"candidate_id": "secure_baseline"}] + [
        {"candidate_id": f"alternative_{i}"} for i in range(1, len(predicted))
    ]
    gate = ConservativeServiceGate(predictor)
    index, reason = gate(predicted, {}, candidates)
    return index, json.loads(reason), gate


def test_supported_material_p0_ens_gain_accepts_with_auditable_error_margin():
    q90 = [0.0] * 19
    q90[2] = 2.0
    index, reason, gate = _decide(_Model(_predictions({2: -10.0}), q90=q90))
    assert index == 1
    assert reason["code"] == "service_improvement_above_validation_error"
    assert reason["selected_candidate_id"] == "alternative_1"
    assert reason["first_material_level"] == "P0_ENS"
    assert reason["selected_level_prediction"] == -10.0
    assert reason["selected_level_q90_margin"] == 2.0
    assert reason == gate.last_decision
    assert reason["joint_safety_guarantee"] is False


def test_tiny_p0_prediction_and_large_fee_gain_cannot_cause_switch():
    index, reason, _ = _decide(_Model(_predictions({0: -0.001, 18: -10000.0})))
    assert index == 0
    assert reason["code"] == "no_reliable_service_improvement"
    assert reason["candidate_assessments"][0]["code"] == "cost_only_or_sub_error_service_difference"


def test_out_of_training_range_candidate_is_refused_but_supported_peer_can_win():
    model = _Model(_predictions({2: -100.0}, {2: -10.0}), support=[True, False, True])
    index, reason, _ = _decide(model)
    assert index == 2
    assert reason["candidate_assessments"][0]["code"] == "feature_range_unsupported"


def test_unknown_validation_target_refuses_instead_of_treating_zero_as_known():
    q90 = [0.0] * 19
    q90[0] = None
    counts = [20] * 19
    counts[0] = 0
    index, reason, _ = _decide(_Model(_predictions({2: -10.0}), q90=q90, counts=counts))
    assert index == 0
    assert reason["code"] == "missing_service_validation_support"
    assert 0 in reason["missing_target_indices"]


def test_malformed_observation_count_is_an_audited_refusal():
    counts = [20] * 19
    counts[1] = None
    index, reason, _ = _decide(_Model(_predictions({2: -10.0}), counts=counts))
    assert index == 0
    assert reason["code"] == "missing_service_validation_support"
    assert 1 in reason["missing_target_indices"]


def test_critical_service_harm_vetoes_other_service_gain():
    index, reason, _ = _decide(_Model(_predictions({2: 3.0, 5: -100.0})))
    assert index == 0
    assert reason["candidate_assessments"][0]["code"] == "material_critical_service_worsening"


def test_lexicographic_choice_prefers_p0_duration_to_larger_p1_gain():
    index, reason, _ = _decide(_Model(_predictions({1: -4.0}, {5: -100.0})))
    assert index == 1
    assert reason["first_material_level"] == "P0_duration"


def test_gate_consumes_exact_inference_once_so_stale_support_cannot_be_reused():
    model = _Model(_predictions({2: -10.0}))
    predictor = SupportAwarePredictor(model)
    predicted = predictor.predict(np.zeros((2, 2)))
    gate = ConservativeServiceGate(predictor)
    candidates = [{"candidate_id": "secure_baseline"}, {"candidate_id": "alternative_1"}]
    assert gate(predicted, {}, candidates)[0] == 1
    index, reason = gate(predicted, {}, candidates)
    assert index == 0
    assert json.loads(reason)["code"] == "missing_current_inference_support"


def test_policy_builder_connects_gated_and_ungated_to_same_model_family():
    model = _Model(_predictions({2: -10.0}))
    gated = build_current_policy("gated", model)
    ungated = build_current_policy("ungated", model)
    rule = build_current_policy("rule")
    identity = build_current_policy("identity")
    assert gated.mode == "gated" and isinstance(gated.gate, ConservativeServiceGate)
    assert isinstance(gated.predictor, SupportAwarePredictor)
    assert ungated.mode == "ungated" and ungated.predictor is model
    assert rule.mode == "rule" and identity.mode == "identity"


def test_registered_factory_builds_exact_original_baseline_identity():
    baseline = build_current_policy({"policy_id": "secure_milp", "kind": "baseline"})
    identity = build_current_policy({"policy_id": "secure_identity_new", "kind": "identity"})
    assert baseline.policy_id == "secure_milp"
    assert identity.policy_id == "secure_identity_new" and identity.mode == "identity"


def test_registered_factory_loads_tree_and_nn_by_bound_metadata_schema(tmp_path, monkeypatch):
    from tools.reliability_first import neural, tree

    model = _Model(_predictions({2: -10.0}))
    monkeypatch.setattr(tree, "load_masked_trees", lambda path: model)
    monkeypatch.setattr(neural, "load_action_value", lambda path: model)
    (tmp_path / "metadata.json").write_text(
        json.dumps({"schema": "reliability_first.masked_extra_trees.v1"}), encoding="utf-8"
    )
    tree_policy = build_current_policy({"policy_id": "tree_gate", "kind": "gated",
                                        "model_dir": str(tmp_path),
                                        "gate": {"error_multiplier": 1.5}})
    assert tree_policy.policy_id == "tree_gate"
    assert tree_policy.gate.error_multiplier == 1.5
    (tmp_path / "metadata.json").write_text(
        json.dumps({"schema": "reliability_first.gpu_paired_action_value.v1"}), encoding="utf-8"
    )
    nn_policy = build_current_policy({"policy_id": "nn_ungated", "kind": "ungated",
                                      "model_dir": str(tmp_path)})
    assert nn_policy.policy_id == "nn_ungated" and nn_policy.predictor is model


def test_registered_factory_rejects_unrecognized_model_instead_of_falling_back(tmp_path):
    import pytest

    (tmp_path / "metadata.json").write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        build_current_policy({"policy_id": "unknown_model", "kind": "ungated",
                              "model_dir": str(tmp_path)})
