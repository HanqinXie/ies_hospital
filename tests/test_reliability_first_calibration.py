"""Independent groups calibrate the current-only secure-relative gate."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools.reliability_first.calibration import calibrate_gate, support_diagnostics
from tools.reliability_first.selection import build_current_policy


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Model:
    def __init__(self, metadata):
        self.metadata = metadata

    def predict(self, x):
        x = np.asarray(x, dtype=float)
        out = np.zeros((len(x), 19))
        out[:, 2] = (x[:, 0] - x[0, 0]) * -3.0
        return out

    def predict_with_support(self, x):
        prediction = self.predict(x)
        x = np.asarray(x, dtype=float)
        low = np.asarray(self.metadata["training_feature_min"])
        high = np.asarray(self.metadata["training_feature_max"])
        return {"delta": prediction,
                "feature_range_supported": np.all((x >= low) & (x <= high), axis=1),
                "training_observed_counts": [5] * 19,
                "validation_observed_counts": [5] * 19,
                "validation_abs_error_q90": [0.5] * 19}


def _row(group, index, *, truth, known=True):
    delta = [0.0] * 19
    mask = [True] * 19
    delta[2] = truth if known else None
    mask[2] = known
    return {"row_id": f"{group}:{index}", "snapshot_id": f"snapshot_{group}",
            "group_id": group, "candidate_index": index,
            "candidate_id": "secure_baseline" if index == 0 else f"other_{index}",
            "role": "validation", "x": [float(index), float(group[-1])],
            "delta_y": delta, "target_mask": mask}


def _sources(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    artifact = model_dir / "models.joblib"
    artifact.write_bytes(b"unit-model")
    binding = {"stage": "full", "manifest_sha256": "source-manifest"}
    metadata = {"schema": "reliability_first.masked_extra_trees.v1",
                "source_binding": binding, "input_dimension": 2,
                "training_feature_min": [0.0, 0.0],
                "training_feature_max": [1.0, 1.0],
                "scale_kWh": np.ones((4, 3)).tolist(),
                "targets": list(__import__("tools.control_improvement.ranking", fromlist=["TARGET_NAMES"]).TARGET_NAMES),
                "training_observed_counts": [5] * 19,
                "validation_observed_counts": [5] * 19,
                "validation_abs_error_q90": [0.5] * 19,
                "artifacts": {"models.joblib": _sha(artifact)}}
    (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    np.savez_compressed(model_dir / "fit_inputs.npz",
                        train_group_id=np.asarray(["train_a"]),
                        validation_group_id=np.asarray(["valid_b"]))
    rows = [_row("cal1", 0, truth=0), _row("cal1", 1, truth=-5),
            _row("cal1", 2, truth=-8), _row("cal2", 0, truth=0),
            _row("cal2", 1, truth=-4), _row("cal2", 2, truth=0, known=False)]
    source = tmp_path / "calibration_input.json"
    source.write_text(json.dumps({"schema": "reliability_first.calibration_input.v1",
                                  "rows": rows, "source_binding": binding,
                                  "excluded_from_fit": True}), encoding="utf-8")
    return model_dir, source, metadata


def test_group_max_residual_quantile_unknowns_and_hash_provenance(tmp_path, monkeypatch):
    from tools.reliability_first import calibration
    from tools.control_improvement import candidates

    model_dir, source, metadata = _sources(tmp_path)
    monkeypatch.setattr(calibration, "_load_model", lambda directory, meta: _Model(meta))
    result = calibrate_gate(source, model_dir)
    assert result["schema"] == "reliability_first.independent_group_gate_calibration.v1"
    assert result["calibration_group_count"] == 2
    assert result["observed_row_counts"][2] == 3
    assert result["unknown_row_counts"][2] == 1
    assert result["observed_group_counts"][2] == 2
    assert result["q90_group_max_abs_error"][2] == pytest.approx(1.9)
    assert result["calibration_input_sha256"] == _sha(source)
    assert result["model_artifact_sha256"] == metadata["artifacts"]["models.joblib"]
    assert result["feature_support"]["feature_encoder_sha256"] == _sha(Path(candidates.__file__))
    assert (model_dir / "gate_calibration.json").is_file()
    assert result["joint_safety_guarantee"] is False


def test_calibration_rejects_overlap_with_fitted_group(tmp_path, monkeypatch):
    from tools.reliability_first import calibration

    model_dir, source, _ = _sources(tmp_path)
    data = json.loads(source.read_text(encoding="utf-8"))
    data["rows"][0]["group_id"] = "train_a"
    data["rows"][1]["group_id"] = "train_a"
    data["rows"][2]["group_id"] = "train_a"
    source.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(calibration, "_load_model", lambda directory, meta: _Model(meta))
    with pytest.raises(ValueError, match="overlap"):
        calibrate_gate(source, model_dir)
    assert not (model_dir / "gate_calibration.json").exists()


def test_registered_gate_uses_max_of_independent_and_validation_q90(tmp_path, monkeypatch):
    from tools.reliability_first import calibration, tree

    model_dir, source, metadata = _sources(tmp_path)
    monkeypatch.setattr(calibration, "_load_model", lambda directory, meta: _Model(meta))
    result = calibrate_gate(source, model_dir)
    monkeypatch.setattr(tree, "load_masked_trees", lambda directory: _Model(metadata))
    policy = build_current_policy({"policy_id": "tree_independent_gate", "kind": "gated",
                                   "model_dir": str(model_dir),
                                   "gate": {"calibration_file": "gate_calibration.json"}})
    predicted = policy.predictor.predict(np.asarray([[0., 0.], [1., 0.]]))
    index, reason_text = policy.gate(predicted, {},
                                     [{"candidate_id": "secure_baseline"},
                                      {"candidate_id": "other_1"}])
    reason = json.loads(reason_text)
    assert index == 1
    assert reason["selected_level_q90_margin"] == pytest.approx(result["q90_group_max_abs_error"][2] + 1.0)
    assert reason["error_evidence_source"] == "max_independent_group_calibration_and_model_validation_q90"
    assert reason["calibration_input_sha256"] == _sha(source)
    assert policy.predictor.metadata["validation_abs_error_q90"][2] == 0.5


def test_registered_gate_rejects_calibration_bound_to_old_model(tmp_path, monkeypatch):
    from tools.reliability_first import calibration, tree

    model_dir, source, metadata = _sources(tmp_path)
    monkeypatch.setattr(calibration, "_load_model", lambda directory, meta: _Model(meta))
    calibrate_gate(source, model_dir)
    monkeypatch.setattr(tree, "load_masked_trees", lambda directory: _Model(metadata))
    (model_dir / "models.joblib").write_bytes(b"changed-model")
    with pytest.raises(ValueError, match="calibration.*model|model.*calibration"):
        build_current_policy({"policy_id": "tree_gate", "kind": "gated",
                              "model_dir": str(model_dir),
                              "gate": {"calibration_file": "gate_calibration.json"}})


def test_calibrated_multidimensional_support_admits_registered_mild_exceedance(tmp_path, monkeypatch):
    from tools.reliability_first import calibration, tree

    model_dir, source, metadata = _sources(tmp_path)
    monkeypatch.setattr(calibration, "_load_model", lambda directory, meta: _Model(meta))
    result = calibrate_gate(source, model_dir)
    assert result["feature_support"]["basis"] == "train_bounds_and_independent_calibration_features_only"
    monkeypatch.setattr(tree, "load_masked_trees", lambda directory: _Model(metadata))
    policy = build_current_policy({"policy_id": "tree_calibrated_support", "kind": "gated",
                                   "model_dir": str(model_dir),
                                   "gate": {"calibration_file": "gate_calibration.json"}})
    prediction = policy.predictor.predict(np.asarray([[1.1, 0.0], [2.2, 0.0]]))
    index, reason_text = policy.gate(prediction, {},
                                     [{"candidate_id": "secure_baseline"},
                                      {"candidate_id": "other_1"}])
    reason = json.loads(reason_text)
    assert index == 1
    assert reason["strict_range_supported_count"] == 0
    assert reason["calibrated_supported_count"] == 2
    assert reason["candidate_assessments"][0]["strict_range_supported"] is False
    assert reason["candidate_assessments"][0]["calibrated_support"] is True
    assert reason["support_evidence_source"] == "train_bounds_independent_group_feature_q90"


def test_extreme_exceedance_still_refused_with_calibrated_support(tmp_path, monkeypatch):
    from tools.reliability_first import calibration, tree

    model_dir, source, metadata = _sources(tmp_path)
    monkeypatch.setattr(calibration, "_load_model", lambda directory, meta: _Model(meta))
    calibrate_gate(source, model_dir)
    monkeypatch.setattr(tree, "load_masked_trees", lambda directory: _Model(metadata))
    policy = build_current_policy({"policy_id": "tree_extreme", "kind": "gated",
                                   "model_dir": str(model_dir),
                                   "gate": {"calibration_file": "gate_calibration.json"}})
    prediction = policy.predictor.predict(np.asarray([[1.1, 0.0], [100.0, 0.0]]))
    index, reason_text = policy.gate(prediction, {},
                                     [{"candidate_id": "secure_baseline"},
                                      {"candidate_id": "other_1"}])
    reason = json.loads(reason_text)
    assert index == 0
    assert reason["candidate_assessments"][0]["code"] == "feature_support_extreme_exceedance"


def test_unseen_explicit_boolean_category_is_never_admitted_by_small_rms_score():
    dimension = 428
    profile = {"input_dimension": dimension,
               "training_feature_min": [0.0] * dimension,
               "training_feature_max": [1.0] * dimension,
               "normalization_scale": [1.0] * dimension,
               "categorical_indices": [36],
               "rms_threshold": 5.0, "extreme_component_threshold": 10.0,
               "basis": "train_bounds_and_independent_calibration_features_only"}
    profile["training_feature_max"][36] = 0.0
    current = np.zeros((1, dimension))
    current[0, 36] = 1.0
    diagnosis = support_diagnostics(current, profile)
    assert diagnosis["rms_normalized_exceedance"][0] < profile["rms_threshold"]
    assert diagnosis["novel_category_indices"][0] == [36]
    assert diagnosis["calibrated_supported"] == [False]


def test_feature_support_threshold_does_not_change_when_calibration_labels_change(tmp_path, monkeypatch):
    from tools.reliability_first import calibration

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir(); second.mkdir()
    model1, source1, _ = _sources(first)
    model2, source2, _ = _sources(second)
    data2 = json.loads(source2.read_text(encoding="utf-8"))
    data2["rows"][1]["delta_y"][2] = -50.0
    source2.write_text(json.dumps(data2), encoding="utf-8")
    monkeypatch.setattr(calibration, "_load_model", lambda directory, meta: _Model(meta))
    result1 = calibrate_gate(source1, model1)
    result2 = calibrate_gate(source2, model2)
    assert result1["feature_support"] == result2["feature_support"]
    assert result1["q90_group_max_abs_error"][2] != result2["q90_group_max_abs_error"][2]
