"""Masked per-target ExtraTrees comparator for secure-relative current actions.

Each target is fitted only on known branch outcomes.  A paired current-state
feature (candidate, secure baseline) lets tree models handle the same candidate
contract as the GPU network without assuming every target shares a split.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor

from tools.control_improvement.ranking import TARGET_NAMES

N_TARGETS = len(TARGET_NAMES)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _prepare(rows, role, dimension=None):
    rows = list(rows)
    required = {"row_id", "role", "snapshot_id", "group_id", "candidate_index", "x", "delta_y", "target_mask"}
    if not rows or any(not required <= set(r) or r["role"] != role for r in rows):
        raise ValueError("Nonempty explicit train/validation action rows required")
    if len({r["row_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate action row_id")
    snapshots = {}
    for row in rows:
        if not all(isinstance(row[k], str) and row[k] for k in ("row_id", "snapshot_id", "group_id")):
            raise ValueError("Invalid row, snapshot, or group identity")
        if type(row["candidate_index"]) is not int or row["candidate_index"] < 0:
            raise ValueError("Invalid candidate_index")
        if len(row["delta_y"]) != N_TARGETS or len(row["target_mask"]) != N_TARGETS:
            raise ValueError("Paired targets must have 19 coordinates")
        for value, observed in zip(row["delta_y"], row["target_mask"]):
            if type(observed) is not bool:
                raise ValueError("target_mask must be boolean")
            if observed and (value is None or not np.isfinite(float(value))):
                raise ValueError("Observed target must be finite")
            if not observed and value is not None:
                raise ValueError("Unknown target must remain None")
        if row["candidate_index"] == 0 and any(v != 0.0 for v, m in zip(row["delta_y"], row["target_mask"]) if m):
            raise ValueError("Secure baseline known delta must equal zero")
        snapshots.setdefault(row["snapshot_id"], []).append(row)
    ordered = []
    for group in snapshots.values():
        if len(group) < 2 or sorted(r["candidate_index"] for r in group) != list(range(len(group))):
            raise ValueError("Each snapshot needs baseline zero and contiguous candidates")
        if len({r["group_id"] for r in group}) != 1:
            raise ValueError("Snapshot crosses groups")
        ordered.extend(sorted(group, key=lambda r: r["candidate_index"]))
    x = np.asarray([r["x"] for r in ordered], dtype=np.float64)
    if x.ndim != 2 or not x.shape[1] or not np.all(np.isfinite(x)):
        raise ValueError("Current candidate x must be finite matrix")
    if dimension is not None and x.shape[1] != dimension:
        raise ValueError("Train/validation x dimensions differ")
    index = np.asarray([r["candidate_index"] for r in ordered], dtype=np.int32)
    baseline = {r["snapshot_id"]: x[i] for i, r in enumerate(ordered) if index[i] == 0}
    base = np.asarray([baseline[r["snapshot_id"]] for r in ordered])
    y = np.asarray([[np.nan if value is None else float(value) for value in r["delta_y"]]
                    for r in ordered])
    mask = np.asarray([r["target_mask"] for r in ordered], dtype=bool)
    return dict(rows=ordered, x=x, base=base, y=y, mask=mask, index=index,
                groups={r["group_id"] for r in ordered}, snapshots=set(snapshots))


class MaskedTreePredictor:
    def __init__(self, estimators, metadata):
        self.estimators = estimators
        self.metadata = metadata

    def predict(self, candidate_matrix):
        x = np.asarray(candidate_matrix, dtype=np.float64)
        if x.ndim != 2 or len(x) < 1 or x.shape[1] != self.metadata["input_dimension"] or not np.all(np.isfinite(x)):
            raise ValueError("Malformed current candidate matrix")
        features = np.concatenate((x, np.broadcast_to(x[:1], x.shape)), axis=1)
        base_feature = np.concatenate((x[:1], x[:1]), axis=1)
        prediction = np.zeros((len(x), N_TARGETS), dtype=np.float64)
        for j, model in enumerate(self.estimators):
            if model is not None:
                prediction[:, j] = model.predict(features) - model.predict(base_feature)[0]
        prediction[0] = 0.0
        return prediction

    def predict_with_support(self, candidate_matrix):
        x = np.asarray(candidate_matrix, dtype=np.float64)
        prediction = self.predict(x)
        low = np.asarray(self.metadata["training_feature_min"])
        high = np.asarray(self.metadata["training_feature_max"])
        return dict(delta=prediction, feature_range_supported=np.all((x >= low) & (x <= high), axis=1),
                    target_trained=[n > 0 for n in self.metadata["training_observed_counts"]],
                    training_observed_counts=self.metadata["training_observed_counts"],
                    validation_observed_counts=self.metadata["validation_observed_counts"],
                    validation_abs_error_q90=self.metadata["validation_abs_error_q90"])


def load_masked_trees(directory):
    directory = Path(directory)
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if metadata["schema"] != "reliability_first.masked_extra_trees.v1":
        raise ValueError("Unexpected masked tree schema")
    for name, expected in metadata["artifacts"].items():
        if _sha(directory / name) != expected:
            raise ValueError("Masked tree artifact hash changed: " + name)
    estimators = joblib.load(directory / "models.joblib")
    if len(estimators) != N_TARGETS:
        raise ValueError("Masked tree artifact target count changed")
    return MaskedTreePredictor(estimators, metadata)


def fit_masked_trees(train_rows, validation_rows, out, *, source_binding, scale_kWh,
                     seed=4201, n_estimators=96, max_depth=10, min_samples_leaf=2):
    """Fit 19 independent target models on observed nonbaseline rows only."""
    out = Path(out)
    if out.exists():
        raise FileExistsError("Retain every masked tree fit attempt; fresh output required")
    out.mkdir(parents=True)
    started = time.perf_counter()
    _write(out / "fit_started.json", dict(source_binding=source_binding, seed=seed,
                                           n_estimators=n_estimators, max_depth=max_depth,
                                           min_samples_leaf=min_samples_leaf,
                                           model="per-target ExtraTreesRegressor", device="cpu"))
    try:
        if not isinstance(source_binding, dict) or not source_binding:
            raise ValueError("Nonempty source binding required")
        if n_estimators < 1 or max_depth < 1 or min_samples_leaf < 1:
            raise ValueError("Invalid ExtraTrees configuration")
        service_scale = np.asarray(scale_kWh, dtype=np.float64)
        if service_scale.shape != (4, 3) or not np.all(np.isfinite(service_scale)) or np.any(service_scale <= 0):
            raise ValueError("scale_kWh must be positive finite 4x3")
        train = _prepare(train_rows, "train")
        valid = _prepare(validation_rows, "validation", train["x"].shape[1])
        if train["groups"] & valid["groups"]:
            raise ValueError("Train/validation group overlap")
        if train["snapshots"] & valid["snapshots"]:
            raise ValueError("Train/validation snapshot overlap")
        if {r["row_id"] for r in train["rows"]} & {r["row_id"] for r in valid["rows"]}:
            raise ValueError("Train/validation row overlap")
        arrays = {}
        for prefix, data in (("train", train), ("validation", valid)):
            arrays.update({prefix + "_x": data["x"], prefix + "_baseline_x": data["base"],
                           prefix + "_y": data["y"], prefix + "_mask": data["mask"],
                           prefix + "_candidate_index": data["index"]})
            for key in ("row_id", "snapshot_id", "group_id"):
                arrays[prefix + "_" + key] = np.asarray([r[key] for r in data["rows"]])
        np.savez_compressed(out / "fit_inputs.npz", **arrays, service_scale_kWh=service_scale)
        train_features = np.concatenate((train["x"], train["base"]), axis=1)
        estimators = []
        nonbase = train["index"] != 0
        for j in range(N_TARGETS):
            observed = nonbase & train["mask"][:, j]
            if not np.any(observed):
                estimators.append(None)
                continue
            model = ExtraTreesRegressor(n_estimators=int(n_estimators), max_depth=int(max_depth),
                                        min_samples_leaf=int(min_samples_leaf), random_state=int(seed) + j,
                                        n_jobs=1)
            model.fit(train_features[observed], train["y"][observed, j])
            estimators.append(model)
        joblib.dump(estimators, out / "models.joblib", compress=3)
        prototype = MaskedTreePredictor(estimators, {"input_dimension": train["x"].shape[1]})
        prediction = np.zeros_like(valid["y"])
        for snapshot_id in sorted(valid["snapshots"]):
            indices = [i for i, row in enumerate(valid["rows"]) if row["snapshot_id"] == snapshot_id]
            prediction[indices] = prototype.predict(valid["x"][indices])
        np.save(out / "validation_predictions.npy", prediction)
        train_counts = (train["mask"] & nonbase[:, None]).sum(axis=0).astype(int).tolist()
        valid_observed = valid["mask"] & (valid["index"] != 0)[:, None]
        valid_counts = valid_observed.sum(axis=0).astype(int).tolist()
        unknown_counts = ((~valid["mask"]) & (valid["index"] != 0)[:, None]).sum(axis=0).astype(int).tolist()
        residual = np.abs(prediction - np.nan_to_num(valid["y"], nan=0.0))
        q90 = [float(np.quantile(residual[valid_observed[:, j], j], .9)) if valid_counts[j] else None
               for j in range(N_TARGETS)]
        mae = [float(np.mean(residual[valid_observed[:, j], j])) if valid_counts[j] else None
               for j in range(N_TARGETS)]
        metadata = dict(schema="reliability_first.masked_extra_trees.v1",
                        input_dimension=int(train["x"].shape[1]), targets=list(TARGET_NAMES),
                        scale_kWh=service_scale.tolist(), source_binding=source_binding,
                        config=dict(seed=int(seed), n_estimators=int(n_estimators),
                                    max_depth=int(max_depth), min_samples_leaf=int(min_samples_leaf)),
                        baseline_identity="prediction(candidate,base)-prediction(base,base)",
                        training_feature_min=train["x"].min(axis=0).tolist(),
                        training_feature_max=train["x"].max(axis=0).tolist(),
                        training_observed_counts=train_counts,
                        validation_observed_counts=valid_counts, validation_unknown_counts=unknown_counts,
                        validation_abs_error_q90=q90, validation_abs_error_mae=mae,
                        calibration_note="Disjoint-group per-target 90th absolute residual; not a joint guarantee",
                        zero_observation_targets_are_untrained=True, future_input=False,
                        training_device="cpu", artifacts={name: _sha(out / name) for name in
                                                        ("fit_started.json", "fit_inputs.npz",
                                                         "models.joblib", "validation_predictions.npy")})
        _write(out / "metadata.json", metadata)
        reloaded = load_masked_trees(out)
        reloaded_prediction = np.zeros_like(prediction)
        for snapshot_id in sorted(valid["snapshots"]):
            indices = [i for i, row in enumerate(valid["rows"]) if row["snapshot_id"] == snapshot_id]
            reloaded_prediction[indices] = reloaded.predict(valid["x"][indices])
        if not np.array_equal(prediction, reloaded_prediction):
            raise AssertionError("Masked tree reload changed validation predictions")
        report = dict(actual_fits=sum(m is not None for m in estimators), training_device="cpu",
                      train_rows=len(train["rows"]), validation_rows=len(valid["rows"]),
                      train_groups=len(train["groups"]), validation_groups=len(valid["groups"]),
                      train_snapshots=len(train["snapshots"]), validation_snapshots=len(valid["snapshots"]),
                      training_observed_counts=train_counts, validation_observed_counts=valid_counts,
                      validation_unknown_counts=unknown_counts, validation_abs_error_q90=q90,
                      reload_max_abs_difference=0.0, elapsed_seconds=time.perf_counter() - started,
                      source_binding=source_binding, metadata_sha256=_sha(out / "metadata.json"))
        _write(out / "FIT_REPORT.json", report)
        return report
    except Exception as error:
        _write(out / "fit_error.json", dict(error_type=type(error).__name__, message=str(error),
                                            traceback=traceback.format_exc(),
                                            elapsed_seconds=time.perf_counter() - started))
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path, help="JSON with train, validation, source_binding, scale_kWh")
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--seed", type=int, default=4201)
    parser.add_argument("--n-estimators", type=int, default=96)
    args = parser.parse_args()
    data = json.loads(args.input_json.read_text(encoding="utf-8"))
    report = fit_masked_trees(data["train"], data["validation"], args.output_directory,
                              source_binding=data["source_binding"], scale_kWh=data["scale_kWh"],
                              seed=args.seed, n_estimators=args.n_estimators)
    print(json.dumps({k: report[k] for k in ("actual_fits", "train_rows", "validation_rows", "elapsed_seconds")}))


if __name__ == "__main__":
    main()
