"""A genuinely different masked non-neural current-action comparator."""
from __future__ import annotations

import json

import numpy as np
import pytest

from tools.reliability_first.tree import fit_masked_trees, load_masked_trees


def _rows(role, group, snapshot, offset):
    rows = []
    for candidate in range(3):
        y = [float(candidate)] * 19
        mask = [True] * 19
        if candidate == 2:
            y[3], mask[3] = None, False
        if candidate:
            y[18], mask[18] = None, False
        rows.append(dict(row_id=f"{snapshot}:{candidate}", role=role,
                         group_id=group, snapshot_id=snapshot,
                         candidate_index=candidate, x=[offset, float(candidate)],
                         delta_y=y, target_mask=mask))
    return rows


def test_masked_trees_keep_unknown_targets_and_secure_identity(tmp_path):
    train = _rows("train", "g1", "s1", 0) + _rows("train", "g2", "s2", 2)
    validation = _rows("validation", "g3", "s3", 1)
    out = tmp_path / "tree_fit"
    report = fit_masked_trees(train, validation, out,
                              source_binding={"manifest": "registered"},
                              scale_kWh=np.ones((4, 3)), seed=7, n_estimators=8)
    assert report["actual_fits"] == 18
    assert report["training_observed_counts"][3] == 2
    assert report["training_observed_counts"][18] == 0
    assert report["validation_observed_counts"][3] == 1
    saved = np.load(out / "fit_inputs.npz")
    assert np.isnan(saved["train_y"][2, 3])
    assert not bool(saved["train_mask"][2, 3])
    model = load_masked_trees(out)
    predicted = model.predict([r["x"] for r in validation])
    assert predicted.shape == (3, 19)
    assert np.array_equal(predicted[0], np.zeros(19))
    assert np.array_equal(predicted[:, 18], np.zeros(3))
    support = model.predict_with_support([r["x"] for r in validation])
    assert not support["target_trained"][18]
    assert json.loads((out / "metadata.json").read_text(encoding="utf-8"))["scale_kWh"] == np.ones((4, 3)).tolist()


def test_masked_trees_reject_group_leakage_and_changed_weight(tmp_path):
    train = _rows("train", "shared", "s1", 0)
    validation = _rows("validation", "shared", "s2", 1)
    with pytest.raises(ValueError, match="overlap"):
        fit_masked_trees(train, validation, tmp_path / "overlap",
                         source_binding={"source": "x"}, scale_kWh=np.ones((4, 3)))
    validation = _rows("validation", "separate", "s2", 1)
    out = tmp_path / "fit"
    fit_masked_trees(train, validation, out, source_binding={"source": "x"},
                     scale_kWh=np.ones((4, 3)), n_estimators=4)
    with (out / "models.joblib").open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="hash"):
        load_masked_trees(out)
