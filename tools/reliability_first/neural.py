"""Masked CUDA action-value learning; candidate zero is the secure baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

import numpy as np
import torch
from torch import nn

from tools.control_improvement.ranking import TARGET_NAMES

N_TARGETS = len(TARGET_NAMES)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _weight_sha(model):
    h = hashlib.sha256()
    for key, value in model.state_dict().items():
        h.update(key.encode())
        h.update(value.detach().cpu().contiguous().numpy().tobytes())
    return h.hexdigest()


class _Net(nn.Module):
    def __init__(self, dimension, hidden):
        super().__init__()
        self.layers = nn.Sequential(nn.Linear(dimension, hidden), nn.ReLU(),
                                    nn.Linear(hidden, hidden), nn.ReLU(),
                                    nn.Linear(hidden, N_TARGETS))

    def forward(self, x):
        return self.layers(x)


def _prepare(rows, role, dimension=None):
    rows = list(rows)
    if not rows or any(r.get("role") != role for r in rows):
        raise ValueError("Nonempty explicit train/validation roles required")
    required = {"row_id", "snapshot_id", "group_id", "candidate_index", "x", "delta_y", "target_mask"}
    if any(not required <= set(r) for r in rows):
        raise ValueError("Action-value row missing required field")
    if len({r["row_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate action-value row_id")
    snapshots = {}
    for row in rows:
        if not isinstance(row["snapshot_id"], str) or not row["snapshot_id"]:
            raise ValueError("Missing snapshot_id")
        if not isinstance(row["group_id"], str) or not row["group_id"]:
            raise ValueError("Missing group_id")
        if type(row["candidate_index"]) is not int or row["candidate_index"] < 0:
            raise ValueError("Invalid candidate_index")
        if len(row["delta_y"]) != N_TARGETS or len(row["target_mask"]) != N_TARGETS:
            raise ValueError("Paired target dimension differs from 19")
        for value, observed in zip(row["delta_y"], row["target_mask"]):
            if type(observed) is not bool:
                raise ValueError("target_mask must contain booleans")
            if observed and (value is None or not np.isfinite(float(value))):
                raise ValueError("Observed target must be finite")
            if not observed and value is not None:
                raise ValueError("Unknown target must remain None, not a zero label")
        if row["candidate_index"] == 0 and any(value != 0.0 for value, observed in zip(row["delta_y"], row["target_mask"]) if observed):
            raise ValueError("Secure baseline known deltas must be exactly zero")
        snapshots.setdefault(row["snapshot_id"], []).append(row)
    ordered = []
    for group in snapshots.values():
        indices = sorted(r["candidate_index"] for r in group)
        if len(group) < 2 or indices != list(range(len(group))):
            raise ValueError("Snapshot needs baseline zero and contiguous candidate indices")
        if len({r["group_id"] for r in group}) != 1:
            raise ValueError("One snapshot spans multiple groups")
        ordered.extend(sorted(group, key=lambda r: r["candidate_index"]))
    x = np.asarray([r["x"] for r in ordered], dtype=np.float64)
    if x.ndim != 2 or not x.shape[1] or not np.all(np.isfinite(x)):
        raise ValueError("Current candidate features must be a finite matrix")
    if dimension is not None and x.shape[1] != dimension:
        raise ValueError("Train/validation feature dimensions differ")
    y = np.asarray([[np.nan if v is None else float(v) for v in r["delta_y"]] for r in ordered])
    mask = np.asarray([r["target_mask"] for r in ordered], dtype=bool)
    index = np.asarray([r["candidate_index"] for r in ordered], dtype=np.int32)
    baseline_by_snapshot = {r["snapshot_id"]: x[i] for i, r in enumerate(ordered) if index[i] == 0}
    base = np.asarray([baseline_by_snapshot[r["snapshot_id"]] for r in ordered])
    return dict(rows=ordered, x=x, base=base, y=y, mask=mask, index=index,
                groups={r["group_id"] for r in ordered}, snapshots=set(snapshots))


def _device_check(model, tensors, phase):
    devices = {str(p.device) for p in model.parameters()} | {str(t.device) for t in tensors}
    if devices != {"cuda:0"}:
        raise RuntimeError(f"{phase} NN model/tensors require actual cuda:0; got {sorted(devices)}")
    return "cuda:0"


class ActionValuePredictor:
    def __init__(self, model, metadata):
        self.model = model
        self.metadata = metadata
        _device_check(model, [], "inference")

    def predict(self, candidate_matrix):
        x = np.asarray(candidate_matrix, dtype=np.float64)
        if x.ndim != 2 or len(x) < 1 or x.shape[1] != self.metadata["input_dimension"] or not np.all(np.isfinite(x)):
            raise ValueError("Malformed current candidate matrix")
        mean = np.asarray(self.metadata["training_feature_mean"])
        std = np.asarray(self.metadata["training_feature_std"])
        tensor = torch.as_tensor(((x - mean) / std).astype(np.float32), device="cuda:0")
        scale = torch.as_tensor(self.metadata["training_target_scale"], dtype=torch.float32, device="cuda:0")
        _device_check(self.model, [tensor, scale], "inference")
        with torch.inference_mode():
            result = ((self.model(tensor) - self.model(tensor[:1])) * scale).cpu().numpy().astype(np.float64)
        result[0] = 0.0
        return result

    def predict_with_support(self, candidate_matrix):
        x = np.asarray(candidate_matrix, dtype=np.float64)
        result = self.predict(x)
        lower = np.asarray(self.metadata["training_feature_min"])
        upper = np.asarray(self.metadata["training_feature_max"])
        return dict(delta=result, feature_range_supported=np.all((x >= lower) & (x <= upper), axis=1),
                    validation_abs_error_q90=self.metadata["validation_abs_error_q90"],
                    validation_observed_counts=self.metadata["validation_observed_counts"],
                    training_observed_counts=self.metadata["training_observed_counts"])


def load_action_value(directory):
    directory = Path(directory)
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if metadata["schema"] != "reliability_first.gpu_paired_action_value.v1":
        raise ValueError("Unexpected action-value model schema")
    for name, expected in metadata["artifacts"].items():
        if _sha(directory / name) != expected:
            raise ValueError("Action-value artifact byte hash changed: " + name)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required to load this NN predictor")
    model = _Net(metadata["input_dimension"], metadata["hidden"]).to("cuda:0")
    model.load_state_dict(torch.load(directory / "model.pt", map_location="cpu", weights_only=True))
    model.eval()
    return ActionValuePredictor(model, metadata)


def fit_action_value(train_rows, validation_rows, out, *, source_binding, scale_kWh,
                     hidden=128, epochs=24, batch_size=256, learning_rate=1e-3, seed=4101):
    """Fit masked paired deltas on CUDA, retaining unknowns as NaN in fit inputs."""
    out = Path(out)
    if out.exists():
        raise FileExistsError("Retain every action-value fit attempt; fresh output required")
    out.mkdir(parents=True)
    started = time.perf_counter()
    _write(out / "fit_started.json", dict(source_binding=source_binding, seed=seed, hidden=hidden,
                                           epochs=epochs, batch_size=batch_size, learning_rate=learning_rate,
                                           optimizer="AdamW", requested_device="cuda:0"))
    try:
        if not isinstance(source_binding, dict) or not source_binding:
            raise ValueError("Nonempty source_binding required")
        if hidden < 1 or epochs < 1 or batch_size < 1 or learning_rate <= 0:
            raise ValueError("Invalid NN fit hyperparameters")
        service_scale = np.asarray(scale_kWh, dtype=np.float64)
        if service_scale.shape != (4, 3) or not np.all(np.isfinite(service_scale)) or np.any(service_scale <= 0):
            raise ValueError("scale_kWh must be a positive finite 4x3 train-only array")
        train = _prepare(train_rows, "train")
        valid = _prepare(validation_rows, "validation", train["x"].shape[1])
        if train["groups"] & valid["groups"]:
            raise ValueError("Train/validation group overlap")
        if train["snapshots"] & valid["snapshots"]:
            raise ValueError("Train/validation snapshot overlap")
        if {r["row_id"] for r in train["rows"]} & {r["row_id"] for r in valid["rows"]}:
            raise ValueError("Train/validation row overlap")
        train_nonbase = train["index"] != 0
        valid_nonbase = valid["index"] != 0
        observed = train["mask"] & train_nonbase[:, None]
        if not np.any(observed):
            raise ValueError("No observed nonbaseline training target")
        mean = train["x"].mean(axis=0)
        std = np.maximum(1.0, train["x"].std(axis=0))
        target_scale = np.asarray([
            max(1.0, float(np.std(train["y"][observed[:, j], j]))) if np.any(observed[:, j]) else 1.0
            for j in range(N_TARGETS)])
        arrays = {}
        for prefix, data in (("train", train), ("validation", valid)):
            arrays.update({prefix + "_x": data["x"], prefix + "_baseline_x": data["base"],
                           prefix + "_y": data["y"], prefix + "_mask": data["mask"],
                           prefix + "_candidate_index": data["index"]})
            for key in ("row_id", "snapshot_id", "group_id"):
                arrays[prefix + "_" + key] = np.asarray([r[key] for r in data["rows"]])
        np.savez_compressed(out / "fit_inputs.npz", **arrays, service_scale_kWh=service_scale,
                            training_feature_mean=mean, training_feature_std=std, training_target_scale=target_scale)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; no CPU NN training fallback")
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        device = torch.device("cuda:0")
        torch.cuda.init()
        torch.cuda.reset_peak_memory_stats(device)
        model = _Net(train["x"].shape[1], int(hidden)).to(device)
        initial_sha = _weight_sha(model)

        def tensors(data):
            x = torch.as_tensor(((data["x"] - mean) / std).astype(np.float32), device=device)
            base = torch.as_tensor(((data["base"] - mean) / std).astype(np.float32), device=device)
            y = torch.as_tensor(np.nan_to_num(data["y"] / target_scale, nan=0.0).astype(np.float32), device=device)
            mask = torch.as_tensor(data["mask"] & (data["index"] != 0)[:, None], device=device)
            return x, base, y, mask

        tx, tb, ty, tm = tensors(train)
        vx, vb, vy, vm = tensors(valid)
        training_device = _device_check(model, [tx, tb, ty, tm], "training")
        validation_device = _device_check(model, [vx, vb, vy, vm], "validation")
        optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
        selected = torch.as_tensor(np.flatnonzero(train_nonbase), device=device)
        updates, curves = 0, []

        def loss_function(pred, truth, mask):
            counts = mask.sum(dim=0)
            per_target = (((pred - truth) ** 2) * mask).sum(dim=0) / counts.clamp_min(1)
            return per_target[counts > 0].mean() if bool(torch.any(counts > 0)) else per_target.sum() * 0

        for epoch in range(epochs):
            model.train()
            for indices in selected[torch.randperm(len(selected), device=device)].split(batch_size):
                if not bool(torch.any(tm[indices])):
                    continue
                optimizer.zero_grad(set_to_none=True)
                prediction = model(tx[indices]) - model(tb[indices])
                loss = loss_function(prediction, ty[indices], tm[indices])
                if not bool(torch.isfinite(loss)):
                    raise FloatingPointError("Nonfinite masked training loss")
                loss.backward()
                optimizer.step()
                updates += 1
            model.eval()
            with torch.inference_mode():
                train_loss = float(loss_function(model(tx) - model(tb), ty, tm).item())
                valid_loss = float(loss_function(model(vx) - model(vb), vy, vm).item())
            curves.append(dict(epoch=epoch + 1, training_masked_mse=train_loss,
                               validation_masked_mse=valid_loss, optimizer_updates_cumulative=updates))
        torch.cuda.synchronize(device)
        trained_sha = _weight_sha(model)
        if updates < 1 or initial_sha == trained_sha:
            raise RuntimeError("CUDA optimizer made no model-weight update")
        prediction = np.zeros_like(valid["y"])
        scale_tensor = torch.as_tensor(target_scale, dtype=torch.float32, device=device)
        with torch.inference_mode():
            for snapshot_id in sorted(valid["snapshots"]):
                indices = [i for i, row in enumerate(valid["rows"]) if row["snapshot_id"] == snapshot_id]
                chunk = vx[indices]
                prediction[indices] = ((model(chunk) - model(chunk[:1])) * scale_tensor).cpu().numpy()
        prediction[valid["index"] == 0] = 0.0
        np.save(out / "validation_predictions.npy", prediction)
        torch.save(model.state_dict(), out / "model.pt")
        valid_observed = valid["mask"] & valid_nonbase[:, None]
        train_counts = observed.sum(axis=0).astype(int).tolist()
        valid_counts = valid_observed.sum(axis=0).astype(int).tolist()
        unknown_counts = ((~valid["mask"]) & valid_nonbase[:, None]).sum(axis=0).astype(int).tolist()
        residual = np.abs(prediction - np.nan_to_num(valid["y"], nan=0.0))
        q90 = [float(np.quantile(residual[valid_observed[:, j], j], .9)) if valid_counts[j] else None
               for j in range(N_TARGETS)]
        mae = [float(np.mean(residual[valid_observed[:, j], j])) if valid_counts[j] else None
               for j in range(N_TARGETS)]
        metadata = dict(schema="reliability_first.gpu_paired_action_value.v1", hidden=int(hidden),
                        input_dimension=int(train["x"].shape[1]), targets=list(TARGET_NAMES),
                        scale_kWh=service_scale.tolist(), source_binding=source_binding,
                        training_feature_mean=mean.tolist(), training_feature_std=std.tolist(),
                        training_feature_min=train["x"].min(axis=0).tolist(),
                        training_feature_max=train["x"].max(axis=0).tolist(),
                        training_target_scale=target_scale.tolist(), training_observed_counts=train_counts,
                        validation_observed_counts=valid_counts, validation_unknown_counts=unknown_counts,
                        validation_abs_error_q90=q90, validation_abs_error_mae=mae,
                        calibration_note="Disjoint-group per-target 90th absolute residual; no joint safety guarantee",
                        actual_train_device=training_device, actual_validation_device=validation_device,
                        future_input=False, baseline_identity="g(candidate)-g(candidate_0)",
                        torch_version=torch.__version__, torch_cuda_version=torch.version.cuda,
                        artifacts={name: _sha(out / name) for name in
                                   ("fit_started.json", "fit_inputs.npz", "validation_predictions.npy", "model.pt")})
        _write(out / "metadata.json", metadata)
        loaded = load_action_value(out)
        reloaded = np.zeros_like(prediction)
        for snapshot_id in valid["snapshots"]:
            indices = [i for i, row in enumerate(valid["rows"]) if row["snapshot_id"] == snapshot_id]
            reloaded[indices] = loaded.predict(valid["x"][indices])
        reload_diff = float(np.max(np.abs(reloaded - prediction)))
        if reload_diff != 0.0:
            raise AssertionError("CUDA reloaded model differs from saved validation predictions")
        torch.cuda.synchronize(device)
        report = dict(actual_fits=1, train_rows=len(train["rows"]), validation_rows=len(valid["rows"]),
                      train_snapshots=len(train["snapshots"]), validation_snapshots=len(valid["snapshots"]),
                      train_groups=len(train["groups"]), validation_groups=len(valid["groups"]),
                      optimizer_updates=updates, epoch_losses=curves,
                      initial_weight_sha256=initial_sha, trained_weight_sha256=trained_sha,
                      actual_train_device=training_device, actual_validation_device=validation_device,
                      peak_cuda_allocated_bytes=int(torch.cuda.max_memory_allocated(device)),
                      elapsed_seconds=time.perf_counter() - started,
                      reload_max_abs_difference=reload_diff,
                      validation_observed_counts=valid_counts, validation_unknown_counts=unknown_counts,
                      metadata_sha256=_sha(out / "metadata.json"), source_binding=source_binding)
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
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--epochs", type=int, default=24)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--seed", type=int, default=4101)
    args = parser.parse_args()
    data = json.loads(args.input_json.read_text(encoding="utf-8"))
    report = fit_action_value(data["train"], data["validation"], args.output_directory,
                              source_binding=data["source_binding"], scale_kWh=data["scale_kWh"],
                              hidden=args.hidden, epochs=args.epochs, batch_size=args.batch_size, seed=args.seed)
    print(json.dumps({key: report[key] for key in
                      ("actual_fits", "optimizer_updates", "actual_train_device", "elapsed_seconds")}))


if __name__ == "__main__":
    main()
