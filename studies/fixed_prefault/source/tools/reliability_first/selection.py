"""Auditable, current-only selection around the original secure request.

The gate uses held-out per-target error as a *screening margin*. It is not a
joint confidence interval, trajectory safety certificate, or clinical rule.
Future-conditioned training branches never enter this module at decision time.
"""
from __future__ import annotations

import json
import math
import hashlib
from pathlib import Path

import numpy as np

from tools.control_improvement.ranking import TARGET_NAMES
from tools.reliability_first.policy import SecureAnchoredPolicy
from tools.integrated_learning.policies import BaselinePolicy


GATE_SCHEMA = "reliability_first.conservative_service_evidence.v1"
SERVICE_INDICES = tuple(range(18))
CRITICAL_GUARD_INDICES = (0, 1, 2, 3, 4, 5, 6, 7, 14, 15)
# These are minimum research decision signals, never changed failure limits.
RESEARCH_FLOORS = (0.5, 1.0) + (0.01,) * 12 + (0.1,) * 3 + (0.5,)
LEVELS = (
    ("P0_failure", (0,)),
    ("P0_duration", (1,)),
    ("P0_ENS", (2, 3, 4)),
    ("P0_temperature", (14,)),
    ("P1_ENS", (5, 6, 7)),
    ("P1_temperature", (15,)),
    ("P2_ENS", (8, 9, 10)),
    ("P2_temperature", (16,)),
    ("P3_ENS", (11, 12, 13)),
    ("secondary_GT_trips", (17,)),
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _registered_calibration(directory, metadata):
    from tools.reliability_first.calibration import (
        MODEL_ARTIFACT, SCHEMA, SUPPORT_SCHEMA, _training_support_reference)

    path = directory / "gate_calibration.json"
    calibration = json.loads(path.read_text(encoding="utf-8"))
    artifact = MODEL_ARTIFACT.get(metadata.get("schema"))
    if artifact is None:
        raise ValueError("Gate calibration has unknown model schema")
    if (calibration.get("schema") != SCHEMA
            or calibration.get("model_schema") != metadata.get("schema")
            or calibration.get("target_names") != list(TARGET_NAMES)
            or calibration.get("source_binding") != metadata.get("source_binding")
            or calibration.get("model_metadata_sha256") != _sha(directory / "metadata.json")
            or calibration.get("model_artifact_filename") != artifact
            or calibration.get("model_artifact_sha256") != _sha(directory / artifact)
            or calibration.get("fit_inputs_sha256") != _sha(directory / "fit_inputs.npz")
            or calibration.get("residual_audit_file") != "gate_calibration_rows.json"
            or calibration.get("residual_audit_sha256") != _sha(directory / "gate_calibration_rows.json")
            or calibration.get("joint_safety_guarantee") is not False):
        raise ValueError("Gate calibration does not match registered model bytes and provenance")
    for key in ("q90_group_max_abs_error", "observed_group_counts"):
        if len(calibration.get(key, [])) != len(TARGET_NAMES):
            raise ValueError("Gate calibration target evidence shape changed")
    profile = calibration.get("feature_support", {})
    reference = _training_support_reference(metadata)
    if (profile.get("schema") != SUPPORT_SCHEMA
            or any(profile.get(key) != value for key, value in reference.items())
            or profile.get("labels_used_for_feature_support") is not False):
        raise ValueError("Gate calibration feature support is not train-bound")
    group_rms = profile.get("group_max_rms_exceedance", {})
    group_peak = profile.get("group_max_coordinate_exceedance", {})
    if (not group_rms or set(group_rms) != set(group_peak)
            or len(group_rms) != calibration.get("calibration_group_count")):
        raise ValueError("Gate calibration feature groups changed")
    rms_q90 = float(np.quantile(list(group_rms.values()), .9))
    peak_q90 = float(np.quantile(list(group_peak.values()), .9))
    if (not np.isclose(profile.get("rms_q90_group_max"), rms_q90, atol=1e-12)
            or not np.isclose(profile.get("max_component_q90_group_max"), peak_q90,
                              atol=1e-12)
            or not np.isclose(profile.get("rms_threshold"), max(.01, 1.25 * rms_q90),
                              atol=1e-12)
            or not np.isclose(profile.get("extreme_component_threshold"),
                              max(8.0, 1.25 * peak_q90), atol=1e-12)):
        raise ValueError("Gate calibration feature thresholds changed")
    return calibration


def _valid_observation_count(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return (not isinstance(value, bool) and math.isfinite(number)
            and number > 0 and number.is_integer())


def _valid_error_quantile(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and number >= 0


class SupportAwarePredictor:
    """Run one real model inference and hand its exact support to the gate.

    ``SecureAnchoredPolicy`` calls ``predict`` before its gate callback but
    does not pass the feature matrix to that callback. The wrapper is scoped
    to one policy instance and its evidence can be consumed only once.
    """

    def __init__(self, predictor, *, calibration=None):
        if not hasattr(predictor, "predict_with_support"):
            raise TypeError("Gated predictor requires predict_with_support")
        self.predictor = predictor
        self.metadata = predictor.metadata
        self.calibration = calibration
        self._current_evidence = None

    def predict(self, candidate_matrix):
        self._current_evidence = None
        evidence = self.predictor.predict_with_support(candidate_matrix)
        result = np.asarray(evidence["delta"], dtype=float)
        self._current_evidence = dict(evidence)
        self._current_evidence["delta"] = result.copy()
        if self.calibration is not None:
            from tools.reliability_first.calibration import support_diagnostics

            diagnosis = support_diagnostics(candidate_matrix, self.calibration["feature_support"])
            strict = np.asarray(evidence["feature_range_supported"], dtype=bool)
            expected_strict = np.asarray(diagnosis["strict_range_supported"], dtype=bool)
            self._current_evidence["strict_feature_range_supported"] = strict.copy()
            self._current_evidence["feature_support_evidence_consistent"] = (
                strict.shape == expected_strict.shape and np.array_equal(strict, expected_strict))
            self._current_evidence["feature_range_supported"] = np.asarray(
                diagnosis["calibrated_supported"], dtype=bool)
            self._current_evidence["support_diagnostics"] = diagnosis
            self._current_evidence["support_evidence_source"] = (
                "train_bounds_independent_group_feature_q90")
            held_out = evidence["validation_abs_error_q90"]
            independent = self.calibration["q90_group_max_abs_error"]
            self._current_evidence["decision_error_q90"] = [
                max(float(v), float(c)) if _valid_error_quantile(v) and _valid_error_quantile(c)
                else None for v, c in zip(held_out, independent)
            ]
            self._current_evidence["calibration_observed_group_counts"] = (
                self.calibration["observed_group_counts"])
            self._current_evidence["calibration_input_sha256"] = (
                self.calibration["calibration_input_sha256"])
            self._current_evidence["error_evidence_source"] = (
                "max_independent_group_calibration_and_model_validation_q90")
        return result

    def pop_current_evidence(self):
        evidence = self._current_evidence
        self._current_evidence = None
        return evidence


class ConservativeServiceGate:
    """Select material service improvement, otherwise retain secure identity.

    A target difference must exceed its group-validation 90th absolute error
    plus a registered research floor. P0/P1 material worsening vetoes a
    candidate even when another coordinate improves. Among eligible actions,
    P0/P1/P2/P3 service levels are compared lexicographically; cost alone can
    never trigger a switch. These marginal screens are not safety guarantees.
    """

    def __init__(self, predictor: SupportAwarePredictor, *, error_multiplier=1.0):
        if not isinstance(predictor, SupportAwarePredictor):
            raise TypeError("Gate requires its own support-aware predictor")
        if not math.isfinite(error_multiplier) or error_multiplier < 1.0:
            raise ValueError("Registered error multiplier must be finite and >= 1")
        self.predictor = predictor
        self.error_multiplier = float(error_multiplier)
        self.last_decision = None
        self._decision_provenance = {}

    def _finish(self, decision):
        decision = dict(schema=GATE_SCHEMA, **decision)
        decision["joint_safety_guarantee"] = False
        decision["calibration_semantics"] = "disjoint_group_per_target_q90_not_simultaneous"
        decision["error_multiplier"] = self.error_multiplier
        decision.update(self._decision_provenance)
        self.last_decision = decision
        return int(decision["selected_candidate_index"]), json.dumps(
            decision, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )

    def _refuse(self, code, **details):
        return self._finish(dict(result="refuse", code=code,
                                 selected_candidate_index=0,
                                 selected_candidate_id="secure_baseline", **details))

    @staticmethod
    def _level_weights(name, targets, scale):
        if name.endswith("_ENS"):
            priority = int(name[1])
            return np.reciprocal(scale[priority])
        return np.ones(len(targets), dtype=float)

    def __call__(self, prediction, context, candidates):
        self._decision_provenance = {}
        evidence = self.predictor.pop_current_evidence()
        if evidence is None:
            return self._refuse("missing_current_inference_support")
        self._decision_provenance = {
            "error_evidence_source": evidence.get("error_evidence_source", "model_validation_q90")
        }
        if "calibration_input_sha256" in evidence:
            self._decision_provenance["calibration_input_sha256"] = (
                evidence["calibration_input_sha256"])
        strict = np.asarray(evidence.get("strict_feature_range_supported",
                                         evidence.get("feature_range_supported", [])))
        supported = np.asarray(evidence.get("feature_range_supported", []))
        diagnosis = evidence.get("support_diagnostics")
        self._decision_provenance["support_evidence_source"] = evidence.get(
            "support_evidence_source", "strict_train_feature_minmax")
        if strict.shape == supported.shape and strict.dtype == bool and supported.dtype == bool:
            self._decision_provenance["strict_range_supported_count"] = int(strict.sum())
            self._decision_provenance["calibrated_supported_count"] = int(supported.sum())
        if diagnosis is not None:
            self._decision_provenance["support_rms_threshold"] = diagnosis["rms_threshold"]
            self._decision_provenance["support_extreme_component_threshold"] = (
                diagnosis["extreme_component_threshold"])
        if evidence.get("feature_support_evidence_consistent") is False:
            return self._refuse("feature_support_evidence_mismatch")
        prediction = np.asarray(prediction, dtype=float)
        if (prediction.ndim != 2 or prediction.shape[1] != len(TARGET_NAMES)
                or len(candidates) != len(prediction) or not len(candidates)
                or candidates[0].get("candidate_id") != "secure_baseline"
                or not np.array_equal(prediction, evidence["delta"])):
            return self._refuse("candidate_or_prediction_binding_error")
        if supported.shape != (len(prediction),) or supported.dtype != bool:
            return self._refuse("missing_feature_range_evidence")
        if not supported[0]:
            return self._refuse("secure_reference_outside_calibrated_support"
                                if diagnosis is not None else "secure_reference_outside_training_range",
                                baseline_support_reason=(diagnosis["support_reason"][0]
                                                         if diagnosis is not None else None))
        try:
            scale = np.asarray(self.predictor.metadata["scale_kWh"], dtype=float)
            if scale.shape != (4, 3) or not np.all(np.isfinite(scale)) or np.any(scale <= 0):
                return self._refuse("invalid_train_only_service_scale")
            q90_raw = evidence.get("decision_error_q90", evidence["validation_abs_error_q90"])
            train_raw = evidence["training_observed_counts"]
            valid_raw = evidence["validation_observed_counts"]
            if any(len(raw) != len(TARGET_NAMES) for raw in (q90_raw, train_raw, valid_raw)):
                return self._refuse("invalid_target_evidence_shape")
            calibration_counts = evidence.get("calibration_observed_group_counts")
            if calibration_counts is not None and len(calibration_counts) != len(TARGET_NAMES):
                return self._refuse("invalid_calibration_evidence_shape")
        except (KeyError, TypeError, ValueError):
            return self._refuse("missing_target_validation_evidence")
        missing = [j for j in SERVICE_INDICES if
                   not _valid_error_quantile(q90_raw[j])
                   or not _valid_observation_count(train_raw[j])
                   or not _valid_observation_count(valid_raw[j])
                   or (calibration_counts is not None
                       and not _valid_observation_count(calibration_counts[j]))]
        if missing:
            return self._refuse("missing_service_validation_support",
                                missing_target_indices=missing,
                                missing_target_names=[TARGET_NAMES[j] for j in missing])
        q90 = np.asarray([float(v) if v is not None else math.nan for v in q90_raw], dtype=float)
        floors = np.asarray(RESEARCH_FLOORS, dtype=float)
        assessments = []
        eligible = []
        for i in range(1, len(prediction)):
            candidate_id = str(candidates[i].get("candidate_id", ""))
            support_audit = {"strict_range_supported": bool(strict[i]),
                             "calibrated_support": bool(supported[i])}
            if diagnosis is not None:
                support_audit.update(
                    rms_normalized_exceedance=diagnosis["rms_normalized_exceedance"][i],
                    max_normalized_exceedance=diagnosis["max_normalized_exceedance"][i],
                    novel_category_indices=diagnosis["novel_category_indices"][i])
            if not supported[i]:
                assessments.append(dict(index=i, candidate_id=candidate_id,
                                        code=(diagnosis["support_reason"][i] if diagnosis is not None
                                              else "feature_range_unsupported"), **support_audit))
                continue
            critical_harm = [j for j in CRITICAL_GUARD_INDICES if
                             prediction[i, j] > self.error_multiplier * q90[j] + floors[j]]
            if critical_harm:
                assessments.append(dict(index=i, candidate_id=candidate_id,
                                        code="material_critical_service_worsening",
                                        target_indices=critical_harm,
                                        target_names=[TARGET_NAMES[j] for j in critical_harm],
                                        **support_audit))
                continue
            first_material = None
            for rank, (name, targets) in enumerate(LEVELS):
                weights = self._level_weights(name, targets, scale)
                score = float(np.dot(prediction[i, list(targets)], weights))
                margin = float(np.dot(q90[list(targets)], np.abs(weights))) * self.error_multiplier
                floor = float(np.dot(floors[list(targets)], np.abs(weights)))
                if score < -(margin + floor):
                    first_material = ("improvement", rank, name, score, margin, floor)
                    break
                if score > margin + floor:
                    first_material = ("worsening", rank, name, score, margin, floor)
                    break
            if first_material is None:
                assessments.append(dict(index=i, candidate_id=candidate_id,
                                        code="cost_only_or_sub_error_service_difference",
                                        **support_audit))
            elif first_material[0] == "worsening":
                assessments.append(dict(index=i, candidate_id=candidate_id,
                                        code="first_material_service_effect_is_worse",
                                        first_material_level=first_material[2],
                                        **support_audit))
            else:
                eligible.append((first_material[1], first_material[3], i, first_material))
                assessments.append(dict(index=i, candidate_id=candidate_id,
                                        code="eligible_material_service_improvement",
                                        first_material_level=first_material[2],
                                        **support_audit))
        if not eligible:
            return self._refuse("no_reliable_service_improvement",
                                candidate_assessments=assessments)
        _, _, selected, first = min(eligible)
        return self._finish(dict(result="accept",
                                 code="service_improvement_above_validation_error",
                                 selected_candidate_index=selected,
                                 selected_candidate_id=candidates[selected]["candidate_id"],
                                 first_material_level=first[2],
                                 selected_level_prediction=first[3],
                                 selected_level_q90_margin=first[4],
                                 selected_level_research_floor=first[5],
                                 candidate_assessments=assessments))


def build_current_policy(mode, predictor=None, *, candidate_limit=8, error_multiplier=1.0,
                         calibration=None):
    """Construct a policy from mode/model or a frozen closed-loop spec.

    The spec form is the stable factory for ``closed_loop.run_closed_loop``.
    Model metadata decides whether the bound artifact is the real CUDA NN or
    the per-target tree comparator. Artifact hashes are verified by loaders.
    """
    if isinstance(mode, dict):
        spec = dict(mode)
        if (set(spec) - {"policy_id", "kind", "model_dir", "gate"}
                or not isinstance(spec.get("policy_id"), str)
                or not spec["policy_id"]):
            raise ValueError("Invalid registered policy spec")
        kind = spec.get("kind")
        if kind == "baseline":
            if spec["policy_id"] != "secure_milp" or spec.get("model_dir") or spec.get("gate"):
                raise ValueError("Original baseline spec must be exact secure_milp")
            return BaselinePolicy("secure_milp")
        if kind in ("identity", "rule"):
            if spec.get("model_dir") or spec.get("gate"):
                raise ValueError("Nonlearning policy cannot claim a model or gate")
            policy = build_current_policy(kind, candidate_limit=candidate_limit)
        elif kind in ("gated", "ungated"):
            if not isinstance(spec.get("model_dir"), str) or not spec["model_dir"]:
                raise ValueError("Learned policy requires registered model_dir")
            directory = (Path(__file__).resolve().parents[2] / spec["model_dir"]).resolve()
            metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            schema = metadata.get("schema")
            gate_spec = spec.get("gate", {})
            if kind == "gated":
                if (not isinstance(gate_spec, dict)
                        or set(gate_spec) - {"error_multiplier", "calibration_file"}):
                    raise ValueError("Unregistered gate setting")
                multiplier = gate_spec.get("error_multiplier", 1.0)
                calibration_file = gate_spec.get("calibration_file")
                if calibration_file is not None and calibration_file != "gate_calibration.json":
                    raise ValueError("Gate calibration must be same-model gate_calibration.json")
                bound_calibration = (_registered_calibration(directory, metadata)
                                     if calibration_file is not None else None)
            else:
                if gate_spec:
                    raise ValueError("Ungated policy cannot claim gate settings")
                multiplier = 1.0
                bound_calibration = None
            if schema == "reliability_first.gpu_paired_action_value.v1":
                from tools.reliability_first.neural import load_action_value
                model = load_action_value(directory)
            elif schema == "reliability_first.masked_extra_trees.v1":
                from tools.reliability_first.tree import load_masked_trees
                model = load_masked_trees(directory)
            else:
                raise ValueError("Unrecognized registered model metadata schema")
            policy = build_current_policy(kind, model, candidate_limit=candidate_limit,
                                          error_multiplier=multiplier,
                                          calibration=bound_calibration)
        else:
            raise ValueError("Unknown registered policy kind")
        policy.policy_id = spec["policy_id"]
        return policy
    if mode == "gated":
        wrapped = SupportAwarePredictor(predictor, calibration=calibration)
        return SecureAnchoredPolicy(mode=mode, predictor=wrapped,
                                    gate=ConservativeServiceGate(
                                        wrapped, error_multiplier=error_multiplier),
                                    candidate_limit=candidate_limit)
    if mode == "ungated":
        if predictor is None:
            raise ValueError("Ungated comparator needs an actual trained predictor")
        return SecureAnchoredPolicy(mode=mode, predictor=predictor,
                                    candidate_limit=candidate_limit)
    if mode in ("rule", "identity"):
        return SecureAnchoredPolicy(mode=mode, candidate_limit=candidate_limit)
    raise ValueError("Unknown secure-anchored mode")
