"""Independent-group residual calibration for secure-relative action gates.

Only frozen branch labels are used here. The output is a screening margin for
future *current-state* inference, never a future scenario input to a policy.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np

from tools.control_improvement.ranking import TARGET_NAMES


SCHEMA = "reliability_first.independent_group_gate_calibration.v1"
MODEL_ARTIFACT = {
    "reliability_first.gpu_paired_action_value.v1": "model.pt",
    "reliability_first.masked_extra_trees.v1": "models.joblib",
}
SUPPORT_SCHEMA = "reliability_first.group_calibrated_feature_support.v1"


def _categorical_indices(dimension):
    """Only explicit booleans in the registered 428-value feature layout."""
    from tools.control_improvement.candidates import HISTORY_KEYS

    frame_width = 1 + len(HISTORY_KEYS)
    if dimension != 76 + 4 * frame_width:
        return []
    history_base = 76
    return sorted(set([36, *range(43, 49), 55, *range(70, 76)]
                      + [history_base + k * frame_width for k in range(4)]
                      + [history_base + k * frame_width + 1
                         + HISTORY_KEYS.index("control_available") for k in range(4)]))


def _training_support_reference(metadata):
    from tools.control_improvement import candidates

    dimension = int(metadata["input_dimension"])
    low = np.asarray(metadata["training_feature_min"], dtype=float)
    high = np.asarray(metadata["training_feature_max"], dtype=float)
    if (low.shape != (dimension,) or high.shape != (dimension,)
            or not np.all(np.isfinite(low)) or not np.all(np.isfinite(high))
            or np.any(low > high)):
        raise ValueError("Model training feature bounds are unavailable")
    scale = np.maximum.reduce((high - low,
                               .05 * np.maximum.reduce((np.abs(low), np.abs(high),
                                                        np.ones(dimension))),
                               np.full(dimension, 1e-6)))
    return {"schema": SUPPORT_SCHEMA, "input_dimension": dimension,
            "feature_encoder_sha256": _sha(candidates.__file__),
            "training_feature_min": low.tolist(),
            "training_feature_max": high.tolist(),
            "normalization_scale": scale.tolist(),
            "categorical_indices": _categorical_indices(dimension),
            "categorical_contract": "explicit_delay_request_pending_history_masks_only",
            "basis": "train_bounds_and_independent_calibration_features_only"}


def _raw_support_scores(current_matrix, profile):
    x = np.asarray(current_matrix, dtype=float)
    dimension = int(profile["input_dimension"])
    low = np.asarray(profile["training_feature_min"], dtype=float)
    high = np.asarray(profile["training_feature_max"], dtype=float)
    scale = np.asarray(profile["normalization_scale"], dtype=float)
    if (x.ndim != 2 or x.shape[1] != dimension or not np.all(np.isfinite(x))
            or low.shape != (dimension,) or high.shape != (dimension,)
            or scale.shape != (dimension,) or not np.all(np.isfinite(scale))
            or np.any(scale <= 0)):
        raise ValueError("Malformed current feature support matrix or bound profile")
    exceedance = np.maximum.reduce((low - x, x - high, np.zeros_like(x))) / scale
    strict = np.all((x >= low) & (x <= high), axis=1)
    rms = np.sqrt(np.mean(exceedance ** 2, axis=1))
    maximum = np.max(exceedance, axis=1)
    categories = list(profile["categorical_indices"])
    if len(set(categories)) != len(categories) or any(type(j) is not int or not 0 <= j < dimension
                                                      for j in categories):
        raise ValueError("Malformed categorical feature contract")
    novelty = []
    for row in x:
        novelty.append([j for j in categories if
                        min(abs(row[j]), abs(row[j] - 1.0)) > 1e-6
                        or row[j] < low[j] - 1e-6 or row[j] > high[j] + 1e-6])
    return strict, rms, maximum, novelty


def support_diagnostics(current_matrix, profile):
    """Evaluate preregistered feature support without any future/label input."""
    strict, rms, maximum, novelty = _raw_support_scores(current_matrix, profile)
    rms_limit = float(profile["rms_threshold"])
    extreme_limit = float(profile["extreme_component_threshold"])
    if (not np.isfinite(rms_limit) or rms_limit < 0
            or not np.isfinite(extreme_limit) or extreme_limit < 0):
        raise ValueError("Malformed calibrated support thresholds")
    reasons = []
    for score, peak, unknown_categories in zip(rms, maximum, novelty):
        if unknown_categories:
            reasons.append("feature_support_unseen_category")
        elif peak > extreme_limit:
            reasons.append("feature_support_extreme_exceedance")
        elif score > rms_limit:
            reasons.append("feature_support_rms_exceedance")
        else:
            reasons.append("calibrated_supported")
    return {"strict_range_supported": strict.astype(bool).tolist(),
            "calibrated_supported": [reason == "calibrated_supported" for reason in reasons],
            "rms_normalized_exceedance": rms.tolist(),
            "max_normalized_exceedance": maximum.tolist(),
            "novel_category_indices": novelty,
            "support_reason": reasons,
            "rms_threshold": rms_limit,
            "extreme_component_threshold": extreme_limit}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _load_model(directory, metadata):
    schema = metadata["schema"]
    if schema == "reliability_first.gpu_paired_action_value.v1":
        from tools.reliability_first.neural import load_action_value
        return load_action_value(directory)
    if schema == "reliability_first.masked_extra_trees.v1":
        from tools.reliability_first.tree import load_masked_trees
        return load_masked_trees(directory)
    raise ValueError("Unrecognized model schema for gate calibration")


def _validate_rows(rows, dimension):
    if not rows or len({r["row_id"] for r in rows}) != len(rows):
        raise ValueError("Independent calibration needs nonempty unique rows")
    snapshots = defaultdict(list)
    groups = set()
    for row in rows:
        if row.get("role") != "validation" or not all(isinstance(row.get(k), str) and row[k]
                                                       for k in ("row_id", "snapshot_id", "group_id")):
            raise ValueError("Malformed independent calibration row identity")
        if type(row.get("candidate_index")) is not int or row["candidate_index"] < 0:
            raise ValueError("Malformed calibration candidate index")
        x = np.asarray(row["x"], dtype=float)
        if x.shape != (dimension,) or not np.all(np.isfinite(x)):
            raise ValueError("Malformed current-only calibration feature row")
        if len(row["delta_y"]) != len(TARGET_NAMES) or len(row["target_mask"]) != len(TARGET_NAMES):
            raise ValueError("Malformed calibration target dimension")
        for value, observed in zip(row["delta_y"], row["target_mask"]):
            if type(observed) is not bool:
                raise ValueError("Calibration target mask must be boolean")
            if observed and (value is None or not np.isfinite(float(value))):
                raise ValueError("Known calibration target must be finite")
            if not observed and value is not None:
                raise ValueError("Unknown calibration target must stay null")
        snapshots[row["snapshot_id"]].append(row)
        groups.add(row["group_id"])
    ordered = []
    for snapshot_id in sorted(snapshots):
        sample = sorted(snapshots[snapshot_id], key=lambda r: r["candidate_index"])
        if ([r["candidate_index"] for r in sample] != list(range(len(sample)))
                or len({r["group_id"] for r in sample}) != 1
                or sample[0].get("candidate_id") != "secure_baseline"):
            raise ValueError("Calibration snapshot lost candidate-zero identity")
        if any(value != 0.0 for value, observed in zip(sample[0]["delta_y"], sample[0]["target_mask"])
               if observed):
            raise ValueError("Secure self-difference must be zero")
        ordered.append(sample)
    return ordered, groups


def calibrate_gate(calibration_input_path, model_directory):
    """Write fresh gate_calibration.json plus row residual audit beside model."""
    source_path = Path(calibration_input_path).resolve()
    directory = Path(model_directory).resolve()
    output_path = directory / "gate_calibration.json"
    residual_path = directory / "gate_calibration_rows.json"
    if output_path.exists() or residual_path.exists():
        raise FileExistsError("Keep prior gate calibration; use a fresh model directory")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if (source.get("schema") != "reliability_first.calibration_input.v1"
            or source.get("excluded_from_fit") is not True
            or source.get("source_binding", {}).get("stage") != "full"):
        raise ValueError("Gate needs excluded full-stage independent calibration input")
    metadata_path = directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    schema = metadata.get("schema")
    if schema not in MODEL_ARTIFACT or metadata.get("targets") != list(TARGET_NAMES):
        raise ValueError("Gate model schema or target order changed")
    if metadata.get("source_binding") != source["source_binding"]:
        raise ValueError("Calibration and fitted model source bindings differ")
    artifact_name = MODEL_ARTIFACT[schema]
    artifact_sha = _sha(directory / artifact_name)
    if metadata.get("artifacts", {}).get(artifact_name) != artifact_sha:
        raise ValueError("Model artifact changed before independent calibration")
    fit_inputs_path = directory / "fit_inputs.npz"
    with np.load(fit_inputs_path, allow_pickle=False) as inputs:
        train_groups = {str(value) for value in inputs["train_group_id"]}
        validation_groups = {str(value) for value in inputs["validation_group_id"]}
    if train_groups & validation_groups:
        raise ValueError("Model fit inputs overlap training and validation groups")
    snapshots, calibration_groups = _validate_rows(source["rows"], int(metadata["input_dimension"]))
    if calibration_groups & (train_groups | validation_groups):
        raise ValueError("Independent calibration groups overlap fitted groups")
    support_profile = _training_support_reference(metadata)
    support_rows = [row for snapshot in snapshots for row in snapshot]
    _, support_rms, support_peak, support_novelty = _raw_support_scores(
        np.asarray([row["x"] for row in support_rows], dtype=float), support_profile)
    group_rms_max = {group: 0.0 for group in calibration_groups}
    group_peak_max = {group: 0.0 for group in calibration_groups}
    group_novel_category_count = {group: 0 for group in calibration_groups}
    for row, rms, peak, novelty in zip(support_rows, support_rms, support_peak, support_novelty):
        group = row["group_id"]
        group_rms_max[group] = max(group_rms_max[group], float(rms))
        group_peak_max[group] = max(group_peak_max[group], float(peak))
        group_novel_category_count[group] += bool(novelty)
    rms_q90 = float(np.quantile(list(group_rms_max.values()), .9))
    peak_q90 = float(np.quantile(list(group_peak_max.values()), .9))
    support_profile.update(
        rms_q90_group_max=rms_q90,
        max_component_q90_group_max=peak_q90,
        rms_threshold=max(.01, 1.25 * rms_q90),
        extreme_component_threshold=max(8.0, 1.25 * peak_q90),
        group_max_rms_exceedance={group: group_rms_max[group] for group in sorted(group_rms_max)},
        group_max_coordinate_exceedance={group: group_peak_max[group] for group in sorted(group_peak_max)},
        group_novel_category_row_count={group: group_novel_category_count[group]
                                        for group in sorted(group_novel_category_count)},
        threshold_semantics="registered 1.25 times independent-group q90, RMS floor .01; coordinate extreme floor 8; no safety guarantee",
        labels_used_for_feature_support=False)
    model = _load_model(directory, metadata)
    rows_audit = []
    group_max = {group: [None] * len(TARGET_NAMES) for group in sorted(calibration_groups)}
    observed_rows = [0] * len(TARGET_NAMES)
    unknown_rows = [0] * len(TARGET_NAMES)
    unknown_groups = {j: set() for j in range(len(TARGET_NAMES))}
    nonbaseline = 0
    for snapshot in snapshots:
        x = np.asarray([row["x"] for row in snapshot], dtype=float)
        predicted = np.asarray(model.predict(x), dtype=float)
        if (predicted.shape != (len(snapshot), len(TARGET_NAMES))
                or not np.all(np.isfinite(predicted))
                or not np.array_equal(predicted[0], np.zeros(len(TARGET_NAMES)))):
            raise ValueError("Calibrated model violates secure-relative prediction contract")
        for row, estimate in zip(snapshot, predicted):
            residual = []
            group = row["group_id"]
            if row["candidate_index"] != 0:
                nonbaseline += 1
            for j, (label, known) in enumerate(zip(row["delta_y"], row["target_mask"])):
                if row["candidate_index"] == 0:
                    residual.append(0.0 if known else None)
                    continue
                if not known:
                    unknown_rows[j] += 1
                    unknown_groups[j].add(group)
                    residual.append(None)
                    continue
                error = abs(float(label) - float(estimate[j]))
                residual.append(error)
                observed_rows[j] += 1
                previous = group_max[group][j]
                group_max[group][j] = error if previous is None else max(previous, error)
            rows_audit.append({"row_id": row["row_id"], "snapshot_id": row["snapshot_id"],
                               "group_id": group, "candidate_index": row["candidate_index"],
                               "target_mask": row["target_mask"],
                               "predicted_delta": estimate.tolist(), "absolute_residual": residual})
    observed_groups = [sum(group_max[group][j] is not None for group in group_max)
                       for j in range(len(TARGET_NAMES))]
    q90 = [float(np.quantile([group_max[group][j] for group in group_max
                              if group_max[group][j] is not None], .9))
           if observed_groups[j] else None for j in range(len(TARGET_NAMES))]
    max_error = [max(group_max[group][j] for group in group_max
                     if group_max[group][j] is not None) if observed_groups[j] else None
                 for j in range(len(TARGET_NAMES))]
    _write_new(residual_path, {"schema": SCHEMA + ".rows", "rows": rows_audit,
                               "group_max_abs_error": group_max,
                               "unknowns_preserved": True})
    result = {"schema": SCHEMA, "model_schema": schema,
              "model_metadata_sha256": _sha(metadata_path),
              "model_artifact_filename": artifact_name,
              "model_artifact_sha256": artifact_sha,
              "fit_inputs_sha256": _sha(fit_inputs_path),
              "calibration_input_sha256": _sha(source_path),
              "source_binding": source["source_binding"],
              "target_names": list(TARGET_NAMES),
              "training_group_count": len(train_groups),
              "validation_group_count": len(validation_groups),
              "calibration_group_count": len(calibration_groups),
              "snapshot_count": len(snapshots), "row_count": len(source["rows"]),
              "nonbaseline_row_count": nonbaseline,
              "observed_row_counts": observed_rows,
              "unknown_row_counts": unknown_rows,
              "observed_group_counts": observed_groups,
              "unknown_group_counts": [len(unknown_groups[j]) for j in range(len(TARGET_NAMES))],
              "q90_group_max_abs_error": q90,
              "max_group_abs_error": max_error,
              "residual_audit_file": residual_path.name,
              "residual_audit_sha256": _sha(residual_path),
              "feature_support": support_profile,
              "quantile_semantics": "q90 of each independent background group's maximum known nonbaseline absolute residual; unknowns excluded from quantile and counted",
              "joint_safety_guarantee": False,
              "online_future_information": False}
    _write_new(output_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calibration_input_json", type=Path)
    parser.add_argument("model_directory", type=Path)
    args = parser.parse_args()
    result = calibrate_gate(args.calibration_input_json, args.model_directory)
    print(json.dumps({key: result[key] for key in
                      ("schema", "calibration_group_count", "observed_group_counts",
                       "calibration_input_sha256", "model_artifact_sha256")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
