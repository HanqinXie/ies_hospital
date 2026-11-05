"""GPU action-value learning: paired identity and unknown-outcome contracts."""
from __future__ import annotations

import json

import numpy as np
import pytest
import torch

from tools.reliability_first.neural import fit_action_value, load_action_value


def _rows(role: str, group: str, snapshot: str, offset: float = 0.0):
    rows = []
    for index in range(3):
        target = [float(index)] * 19
        mask = [True] * 19
        if index == 2:
            target[3] = None
            mask[3] = False
        rows.append(
            dict(
                row_id=f"{snapshot}:{index}", role=role, group_id=group,
                snapshot_id=snapshot, candidate_index=index,
                x=[offset, float(index), offset + index / 3],
                delta_y=target, target_mask=mask,
            )
        )
    return rows


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA required for real NN fitting")
def test_cuda_fit_and_reload_preserve_exact_baseline_identity_and_unknown_mask(tmp_path):
    train = _rows("train", "train_a", "a") + _rows("train", "train_b", "b", 2.0)
    validation = _rows("validation", "valid_c", "c", 1.0)
    out = tmp_path / "fit"
    report = fit_action_value(train, validation, out, source_binding={"source": "hospital_test"},
                              scale_kWh=np.ones((4, 3)),
                              hidden=16, epochs=3, batch_size=4, seed=7)
    assert report["optimizer_updates"] > 0
    assert report["initial_weight_sha256"] != report["trained_weight_sha256"]
    assert report["actual_train_device"] == report["actual_validation_device"] == "cuda:0"
    assert report["reload_max_abs_difference"] == 0.0
    assert report["validation_observed_counts"][3] == 1
    assert report["validation_unknown_counts"][3] == 1
    saved = np.load(out / "fit_inputs.npz", allow_pickle=False)
    assert np.isnan(saved["validation_y"][2, 3])
    assert not bool(saved["validation_mask"][2, 3])
    model = load_action_value(out)
    x = np.asarray([r["x"] for r in validation], dtype=np.float32)
    prediction = model.predict(x)
    assert prediction.shape == (3, 19)
    assert np.array_equal(prediction[0], np.zeros(19))
    diagnostic = model.predict_with_support(x)
    assert diagnostic["validation_abs_error_q90"][3] is not None
    assert diagnostic["feature_range_supported"].shape == (3,)
    assert json.loads((out / "metadata.json").read_text(encoding="utf-8"))["artifacts"]["model.pt"]


def test_rejects_overlapping_groups_and_inconsistent_unknown_labels(tmp_path):
    train = _rows("train", "same", "a")
    validation = _rows("validation", "same", "b")
    with pytest.raises(ValueError, match="overlap"):
        fit_action_value(train, validation, tmp_path / "overlap", source_binding={"source": "x"}, scale_kWh=np.ones((4, 3)))
    validation = _rows("validation", "other", "b")
    train[2]["delta_y"][3] = 0.0
    with pytest.raises(ValueError, match="Unknown target"):
        fit_action_value(train, validation, tmp_path / "unknown", source_binding={"source": "x"}, scale_kWh=np.ones((4, 3)))


def test_no_cpu_training_fallback_and_failed_attempt_is_retained(tmp_path, monkeypatch):
    train = _rows("train", "train", "a")
    validation = _rows("validation", "valid", "b")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    out = tmp_path / "missing_cuda"
    with pytest.raises(RuntimeError, match="CUDA"):
        fit_action_value(train, validation, out, source_binding={"source": "x"}, scale_kWh=np.ones((4, 3)))
    error = json.loads((out / "fit_error.json").read_text(encoding="utf-8"))
    assert error["error_type"] == "RuntimeError"
    assert "CUDA" in error["message"]
