"""CUDA GRU imitation of visited v5 full-feedback teacher actions.

Training consumes only registered train-root trajectories produced by complete
feedback rollouts. Labels are the teacher's current six-port request relative
to the current secure plan, never counterfactual returns. At deployment the
same current-only projection supplies context and the unchanged environment
owns command generation, queues, protection and physical execution.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time
import traceback

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.data_contract import validate_action
from hospital_ies.dataset_manifest import digest
from tools.control_improvement.candidates import HISTORY_KEYS, validate_context
from tools.control_improvement.runtime import project_current
from tools.integrated_learning.policies import ACTION_NAMES, baseline_request, legal_history


SCHEMA = "policy_search.v5.cuda_gru_feedback.v1"
FRAME_DIM = len(HISTORY_KEYS) + 1
CONTEXT_DIM = 58
PORT_COUNT = len(ACTION_NAMES)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _new_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _vec(value, shape, name, *, nonnegative=False):
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f"Invalid finite {name}")
    if nonnegative and np.any(array < 0):
        raise ValueError(f"Negative {name}")
    return array


def encode_observation(records, context):
    """Encode four legal past/current frames and only current derived context."""
    history = validate_context(records, context)
    frames = np.zeros((4, FRAME_DIM), dtype=np.float64)
    selected = history[-4:]
    for slot, row in enumerate(selected, start=4 - len(selected)):
        features = row["features"]
        if any(key not in features for key in HISTORY_KEYS):
            raise ValueError("Present frame lacks registered numeric observation")
        values = _vec([features[key] for key in HISTORY_KEYS], (len(HISTORY_KEYS),),
                      "legal observation")
        frames[slot, 0] = 1.0
        frames[slot, 1:] = values
    pieces = []
    for key, shape in (
        ("execution_basis_targets_kW", (6,)), ("current_plan_targets_kW", (6,)),
        ("capacity_GT_kW", (3,)), ("availability_HRSG", (3,)),
        ("overload_minutes_GT", (3,)), ("loads_kW", (4, 3)),
    ):
        pieces.extend(_vec(context[key], shape, key, nonnegative=True).ravel())
    now = float(context["time_minute"])
    pieces.extend((float(context["capacity_ABS_kW"]),
                   float(context["grid_import_limit_kW"]),
                   float(context["arrival_minute"]) - now,
                   float(bool(context["delayed"]))))
    ledger = context.get("pending_command_ledger")
    if ledger and ledger["available"]:
        pieces.extend((1.0, now - float(ledger["generated_minute"]),
                       float(ledger["planned_arrival_minute"]) - now))
        for key in ("planned_targets_kW", "requested_targets_kW", "request_mask"):
            pieces.extend(_vec(ledger[key], (6,), key, nonnegative=True))
    else:
        pieces.extend([0.0] * 21)
    current = _vec(pieces, (CONTEXT_DIM,), "current context")
    return frames, current


def _verify_signed(value, label):
    unsigned = copy.deepcopy(value)
    signature = unsigned.pop("signature", None)
    if signature != digest(unsigned):
        raise ValueError(f"{label} signature mismatch")
    return signature


def collect_training_examples(manifest_path, result_paths, *, teacher_policy_id):
    """Audit registered raw results and extract visited action-imitation rows."""
    mp = Path(manifest_path).resolve()
    manifest = _json(mp)
    manifest_signature = _verify_signed(manifest, "Manifest")
    scenes = {s["id"]: s for s in manifest["scenarios"]}
    if not isinstance(teacher_policy_id, str) or not teacher_policy_id:
        raise ValueError("Nonempty feedback teacher_policy_id required")
    paths = [Path(p).resolve() for p in result_paths]
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("Nonempty unique teacher result_paths required")
    examples = []
    source_rows = []
    seen_pairs = set()
    unusable = 0
    for rp in paths:
        if rp.name != "result.json" or len(rp.parents) < 4:
            raise ValueError("Expected registered attempt result.json path")
        registered_plan = rp.parents[3] / "registered_plan.json"
        plan = _json(registered_plan)
        plan_signature = _verify_signed(plan, "Batch plan")
        if (Path(plan["manifest_path"]).resolve() != mp
                or plan["manifest_sha256"] != _sha(mp)
                or plan["manifest_signature"] != manifest_signature):
            raise ValueError("Teacher plan differs from registered manifest bytes")
        if plan.get("phase") != "train":
            raise ValueError("Only v5 train-root feedback rollouts may fit NN")
        specs = [s for s in plan["policy_specs"] if s["policy_id"] == teacher_policy_id]
        if len(specs) != 1 or specs[0].get("kind") != "feedback":
            raise ValueError("Teacher must be a registered complete feedback policy")
        result = _json(rp)
        sid = result.get("scenario_id")
        key = (sid, teacher_policy_id)
        if key in seen_pairs:
            raise ValueError("Duplicate teacher scenario result")
        seen_pairs.add(key)
        if (sid not in scenes or sid not in plan["scenario_ids"]
                or result.get("policy_id") != teacher_policy_id
                or result.get("kind") != "feedback"
                or result.get("phase") != "train"
                or result.get("plan_signature") != plan_signature
                or not result.get("terminal")
                or not result.get("binding", {}).get("passed")):
            raise ValueError("Teacher result identity/physical binding invalid")
        scene = scenes[sid]
        split = manifest["splits"][sid]
        if (scene.get("is_final_blind") or split.get("is_final_blind")
                or scene.get("experiment_role") != "train"
                or split.get("experiment_role") != "train"):
            raise ValueError("Only v5 train roots may supply NN examples")
        name = result.get("trajectory_path")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("Missing local raw trajectory")
        raw = rp.parent / name
        actual_sha = _sha(raw)
        if result.get("trajectory_sha256") != actual_sha:
            raise ValueError("Raw trajectory SHA mismatch")
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        if (payload.get("scenario") != scene
                or payload.get("summary", {}).get("status") != result.get("status")):
            raise ValueError("Raw trajectory scenario/status differs from result")
        decisions = payload.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("Raw full-feedback trajectory lacks decisions")
        accepted = 0
        for index, row in enumerate(decisions):
            history = legal_history(decisions[:index + 1])
            diag = row.get("policy_diagnostic")
            request = row.get("requested_command")
            if not isinstance(diag, dict) or not isinstance(request, dict):
                unusable += 1
                continue
            if diag.get("strategy_fallback") or diag.get("future_rollouts_online") != 0:
                unusable += 1
                continue
            context = diag.get("current_context")
            queries = diag.get("current_planning_queries")
            if not isinstance(context, dict) or not isinstance(queries, list) or not queries:
                unusable += 1
                continue
            if queries[0].get("candidate_id") != "secure_baseline" or queries[0].get("status") != "success":
                unusable += 1
                continue
            secure = _vec(queries[0].get("projected_targets_kW"), (6,), "secure projection",
                          nonnegative=True)
            frame, current = encode_observation(history, context)
            if not np.allclose(secure, context["current_plan_targets_kW"], atol=1e-6, rtol=0):
                raise ValueError("Current context plan differs from secure projection")
            if diag.get("decision_time_min") is not None and abs(
                    float(diag["decision_time_min"]) - float(row["decision_time_min"])) > 1e-9:
                raise ValueError("Teacher diagnostic observation clock mismatch")
            if request.get("security") != baseline_request("secure_milp")["security"]:
                raise ValueError("Teacher request lost original secure identity")
            intervention = "targets" in request
            if intervention:
                if set(request["targets"]) != set(ACTION_NAMES):
                    raise ValueError("Teacher action exceeds/omits original six ports")
                requested = _vec([request["targets"][k] for k in ACTION_NAMES], (6,),
                                 "teacher targets", nonnegative=True)
            else:
                requested = secure.copy()
            delta = requested - secure
            if not np.allclose(_vec(diag.get("requested_six_targets_kW"), (6,),
                                    "teacher diagnostic request"), requested, atol=1e-6, rtol=0):
                raise ValueError("Teacher diagnostic differs from submitted request")
            if not np.allclose(_vec(diag.get("delta_from_secure_plan_kW"), (6,),
                                    "teacher diagnostic delta"), delta, atol=1e-6, rtol=0):
                raise ValueError("Teacher diagnostic delta mismatch")
            examples.append({
                "scenario_id": sid, "root_family_id": scene["root_family_id"],
                "group_id": split["group_id"], "decision_time_min": float(row["decision_time_min"]),
                "history_frame_count": len(history), "frame": frame, "context": current,
                "secure_targets_kW": secure, "requested_targets_kW": requested,
                "delta_kW": delta.tolist(), "intervention": intervention,
                "source_trajectory_sha256": actual_sha,
            })
            accepted += 1
        source_rows.append({
            "scenario_id": sid, "root_family_id": scene["root_family_id"],
            "group_id": split["group_id"], "role": "train", "teacher_policy_id": teacher_policy_id,
            "result_path": str(rp), "result_sha256": _sha(rp),
            "raw_path": str(raw), "sha256": actual_sha, "status": result["status"],
            "observed_decisions": len(decisions), "usable_imitation_decisions": accepted,
            "registered_plan_signature": plan_signature,
        })
    if not examples:
        raise ValueError("No usable full-feedback teacher actions in train roots")
    if not any(x["intervention"] for x in examples):
        raise ValueError("No actual nonidentity six-port teacher action to learn")
    audit = {
        "schema": SCHEMA + ".source_audit", "manifest_path": str(mp),
        "manifest_sha256": _sha(mp), "manifest_signature": manifest_signature,
        "teacher_policy_id": teacher_policy_id, "teacher_kind": "feedback",
        "role": "train", "source_trajectories": source_rows,
        "source_trajectory_count": len(source_rows),
        "root_family_ids": sorted({x["root_family_id"] for x in examples}),
        "group_ids": sorted({x["group_id"] for x in examples}),
        "usable_imitation_decisions": len(examples),
        "intervention_decisions": sum(x["intervention"] for x in examples),
        "secure_identity_decisions": sum(not x["intervention"] for x in examples),
        "unknown_or_unusable_decisions": unusable,
        "label_semantics": "Visited full-feedback teacher current request minus current secure projection; no counterfactual value label",
        "final_12_backgrounds_opened": False,
    }
    return examples, audit


def _weight_sha(model):
    checksum = hashlib.sha256()
    for name, parameter in model.state_dict().items():
        checksum.update(name.encode("utf-8"))
        checksum.update(parameter.detach().cpu().contiguous().numpy().tobytes())
    return checksum.hexdigest()


class _GRUNet(nn.Module):
    def __init__(self, frame_dim, context_dim, hidden):
        super().__init__()
        self.gru = nn.GRU(frame_dim, hidden, batch_first=True)
        self.context = nn.Sequential(nn.Linear(context_dim, hidden), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(hidden * 2, hidden), nn.ReLU(),
                                  nn.Linear(hidden, PORT_COUNT + 1))

    def forward(self, frames, context):
        _, state = self.gru(frames)
        fused = torch.cat([state[-1], self.context(context)], dim=1)
        output = self.head(fused)
        return torch.tanh(output[:, :PORT_COUNT]), output[:, PORT_COUNT]


def _normalization(rows):
    frame = np.asarray([r["frame"] for r in rows], dtype=np.float64)
    context = np.asarray([r["context"] for r in rows], dtype=np.float64)
    present = frame[:, :, 0] > 0.5
    observed = frame[:, :, 1:][present]
    frame_mean = observed.mean(axis=0)
    frame_std = np.maximum(observed.std(axis=0), 1.0)
    context_mean = context.mean(axis=0)
    context_std = np.maximum(context.std(axis=0), 1.0)
    deltas = np.asarray([r["delta_kW"] for r in rows], dtype=np.float64)
    target_scale = np.maximum(np.max(np.abs(deltas), axis=0), 1.0)
    return dict(frame_mean=frame_mean.tolist(), frame_std=frame_std.tolist(),
                context_mean=context_mean.tolist(), context_std=context_std.tolist(),
                target_scale_kW=target_scale.tolist())


def _inputs(rows, norm):
    frames = np.asarray([r["frame"] for r in rows], dtype=np.float64)
    context = np.asarray([r["context"] for r in rows], dtype=np.float64)
    mask = frames[:, :, :1]
    normalized = (frames[:, :, 1:] - np.asarray(norm["frame_mean"])) / np.asarray(norm["frame_std"])
    frames = np.concatenate((mask, normalized * mask), axis=2).astype(np.float32)
    context = ((context - np.asarray(norm["context_mean"])) / np.asarray(norm["context_std"])).astype(np.float32)
    delta = (np.asarray([r["delta_kW"] for r in rows]) / np.asarray(norm["target_scale_kW"])).astype(np.float32)
    intervention = np.asarray([r["intervention"] for r in rows], dtype=np.float32)
    return frames, context, delta, intervention


def _split_roots(examples, seed):
    roots = sorted({r["root_family_id"] for r in examples})
    if len(roots) < 2:
        return roots, []
    rng = np.random.default_rng(seed)
    permuted = list(rng.permutation(roots))
    holdout_count = max(1, round(len(roots) * 0.2))
    return sorted(permuted[holdout_count:]), sorted(permuted[:holdout_count])


def train_from_rollouts(manifest_path, result_paths, model_dir, *, teacher_policy_id,
                        seed=5101, hidden=64, epochs=16, batch_size=128, learning_rate=1e-3):
    """Fit an actual CUDA GRU while preserving source and optimizer evidence."""
    out = Path(model_dir)
    if out.exists():
        raise FileExistsError("Keep every v5 NN fit attempt in a fresh model directory")
    out.mkdir(parents=True)
    started = time.perf_counter()
    _new_json(out / "fit_started.json", {
        "schema": SCHEMA + ".fit_started", "manifest_path": str(Path(manifest_path).resolve()),
        "teacher_policy_id": teacher_policy_id, "seed": seed, "hidden": hidden,
        "epochs": epochs, "batch_size": batch_size, "learning_rate": learning_rate,
        "requested_device": "cuda:0", "architecture": "4-frame GRU plus current-context branch",
        "gate_mode": "none", "source_module_sha256": _sha(__file__),
    })
    try:
        if (type(seed) is not int or type(hidden) is not int or type(epochs) is not int
                or type(batch_size) is not int or hidden < 4 or epochs < 1 or batch_size < 1
                or not math.isfinite(float(learning_rate)) or learning_rate <= 0):
            raise ValueError("Invalid v5 GRU fit hyperparameters")
        examples, audit = collect_training_examples(
            manifest_path, result_paths, teacher_policy_id=teacher_policy_id)
        _new_json(out / "source_audit.json", audit)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; v5 NN has no CPU training fallback")
        train_roots, holdout_roots = _split_roots(examples, seed)
        train = [r for r in examples if r["root_family_id"] in train_roots]
        holdout = [r for r in examples if r["root_family_id"] in holdout_roots]
        if not any(r["intervention"] for r in train):
            raise ValueError("Fit roots lack actual nonidentity teacher requests")
        norm = _normalization(train)
        tf, tc, td, ti = _inputs(train, norm)
        if holdout:
            vf, vc, vd, vi = _inputs(holdout, norm)
        else:
            vf = np.empty((0, 4, FRAME_DIM), dtype=np.float32)
            vc = np.empty((0, CONTEXT_DIM), dtype=np.float32)
            vd = np.empty((0, 6), dtype=np.float32)
            vi = np.empty((0,), dtype=np.float32)
        np.savez_compressed(
            out / "fit_examples.npz", train_frame=tf, train_context=tc,
            train_delta=td, train_intervention=ti, holdout_frame=vf,
            holdout_context=vc, holdout_delta=vd, holdout_intervention=vi,
            train_scenario_id=np.asarray([r["scenario_id"] for r in train]),
            holdout_scenario_id=np.asarray([r["scenario_id"] for r in holdout]),
            train_root_family_id=np.asarray([r["root_family_id"] for r in train]),
            holdout_root_family_id=np.asarray([r["root_family_id"] for r in holdout]),
        )
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        device = torch.device("cuda:0")
        torch.cuda.init()
        torch.cuda.reset_peak_memory_stats(device)
        model = _GRUNet(FRAME_DIM, CONTEXT_DIM, hidden).to(device)
        initial_hash = _weight_sha(model)
        parameters = sum(p.numel() for p in model.parameters())
        tensors = tuple(torch.as_tensor(x, device=device) for x in (tf, tc, td, ti))
        validation = tuple(torch.as_tensor(x, device=device) for x in (vf, vc, vd, vi))
        devices = {str(p.device) for p in model.parameters()} | {str(x.device) for x in tensors}
        if devices != {"cuda:0"}:
            raise RuntimeError("GRU model and fit tensors did not reside on actual cuda:0")
        optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
        curves = []
        updates = 0

        def losses(chunk):
            pred, logit = model(chunk[0], chunk[1])
            regression = torch.mean((pred - chunk[2]) ** 2)
            gate = nn.functional.binary_cross_entropy_with_logits(logit, chunk[3])
            return regression + 0.2 * gate, regression, gate

        for epoch in range(epochs):
            model.train()
            order = torch.randperm(len(train), device=device)
            for indices in order.split(batch_size):
                chunk = tuple(x[indices] for x in tensors)
                optimizer.zero_grad(set_to_none=True)
                total, _, _ = losses(chunk)
                if not bool(torch.isfinite(total)):
                    raise FloatingPointError("Nonfinite v5 GRU loss")
                total.backward()
                optimizer.step()
                updates += 1
            model.eval()
            with torch.inference_mode():
                train_total, train_mse, train_bce = losses(tensors)
                record = {"epoch": epoch + 1, "fit_total": float(train_total.item()),
                          "fit_delta_mse": float(train_mse.item()),
                          "fit_intervention_bce": float(train_bce.item()),
                          "optimizer_updates_cumulative": updates}
                if holdout:
                    val_total, val_mse, val_bce = losses(validation)
                    record.update(holdout_total=float(val_total.item()),
                                  holdout_delta_mse=float(val_mse.item()),
                                  holdout_intervention_bce=float(val_bce.item()))
                curves.append(record)
        torch.cuda.synchronize(device)
        trained_hash = _weight_sha(model)
        if updates < 1 or initial_hash == trained_hash:
            raise RuntimeError("CUDA GRU optimizer made no weight update")
        torch.save(model.state_dict(), out / "model.pt")
        report = {
            "schema": SCHEMA + ".fit_report", "actual_train_device": "cuda:0",
            "actual_holdout_device": "cuda:0" if holdout else None,
            "torch_version": torch.__version__, "torch_cuda_version": torch.version.cuda,
            "cuda_device_name": torch.cuda.get_device_name(device),
            "peak_cuda_memory_allocated_bytes": int(torch.cuda.max_memory_allocated(device)),
            "architecture": "4-frame GRU plus current-context branch",
            "frame_dimension": FRAME_DIM, "context_dimension": CONTEXT_DIM,
            "hidden": hidden, "parameter_count": parameters, "seed": seed,
            "epochs": epochs, "batch_size": batch_size, "optimizer": "AdamW",
            "learning_rate": learning_rate, "optimizer_updates": updates,
            "initial_weight_sha256": initial_hash, "trained_weight_sha256": trained_hash,
            "training_roots": train_roots, "internal_holdout_roots": holdout_roots,
            "training_decisions": len(train), "internal_holdout_decisions": len(holdout),
            "source_trajectory_sha256": sorted({r["source_trajectory_sha256"] for r in examples}),
            "source_audit_sha256": _sha(out / "source_audit.json"),
            "loss_curve": curves, "elapsed_seconds": time.perf_counter() - started,
            "label_semantics": audit["label_semantics"],
            "model_selection_note": "Internal train-root holdout monitors only; selection/calibration/development roots never fitted",
        }
        _new_json(out / "fit_report.json", report)
        metadata = {
            "schema": SCHEMA, "architecture": "GRU", "frame_dim": FRAME_DIM,
            "context_dim": CONTEXT_DIM, "hidden": hidden, "normalization": norm,
            "teacher_policy_id": teacher_policy_id,
            "source_module_sha256": _sha(__file__), "source_audit_sha256": _sha(out / "source_audit.json"),
            "trained_weight_sha256": trained_hash, "gate_mode": "none",
            "gate_threshold": None, "calibration_source": None,
            "original_secure_identity": baseline_request("secure_milp"),
            "future_rollouts_online": 0, "artifacts": {name: _sha(out / name) for name in (
                "fit_started.json", "source_audit.json", "fit_examples.npz", "fit_report.json", "model.pt")},
        }
        _new_json(out / "metadata.json", metadata)
        return report
    except Exception as error:
        _new_json(out / "fit_error.json", {
            "schema": SCHEMA + ".fit_error", "error_type": type(error).__name__,
            "message": str(error), "traceback": traceback.format_exc(),
            "elapsed_seconds": time.perf_counter() - started,
        })
        raise


def _load_artifacts(directory):
    path = Path(directory)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    metadata = _json(path / "metadata.json")
    if metadata.get("schema") != SCHEMA or metadata.get("architecture") != "GRU":
        raise ValueError("Unexpected v5 neural model schema/architecture")
    if metadata.get("source_module_sha256") != _sha(__file__):
        raise ValueError("Neural implementation SHA changed after model fitting")
    for name, expected in metadata["artifacts"].items():
        if Path(name).name != name or _sha(path / name) != expected:
            raise ValueError("Neural artifact SHA mismatch: " + name)
    if metadata["source_audit_sha256"] != _sha(path / "source_audit.json"):
        raise ValueError("NN source audit bytes changed")
    if metadata["gate_mode"] not in ("none", "confidence"):
        raise ValueError("Unknown NN gate mode")
    if metadata["gate_mode"] == "confidence":
        threshold = metadata.get("gate_threshold")
        source = metadata.get("calibration_source")
        if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            raise ValueError("Invalid calibrated confidence gate threshold")
        if not isinstance(source, dict) or source.get("role") != "calibration":
            raise ValueError("Confidence gate lacks independent calibration provenance")
    return path, metadata


class NeuralFeedbackPolicy:
    """Pure current request policy; the original environment executes the action."""

    def __init__(self, model, metadata, policy_id):
        self.model = model
        self.metadata = metadata
        self.policy_id = policy_id
        self.current_problem = None

    def bind_current(self, problem):
        self.current_problem = problem

    def decide(self, records):
        history = legal_history(records)
        if self.current_problem is None:
            raise ValueError("Neural feedback requires a bound current problem")
        problem = self.current_problem
        self.current_problem = None
        secure_request = baseline_request("secure_milp")
        projected = project_current(problem, secure_request)
        context = problem.policy_context(history, projected["command"])
        validate_context(history, context)
        secure = _vec(projected["projected_targets_kW"], (6,), "secure projection",
                      nonnegative=True)
        diagnostic = {
            "model_id": self.policy_id, "strategy_fallback": False,
            "fallback_reason": None, "baseline_policy": "secure_milp",
            "baseline_request_submitted_unchanged": True,
            "future_rollouts_online": 0, "gate_mode": self.metadata["gate_mode"],
            "gate_threshold": self.metadata["gate_threshold"],
            "current_planning_queries": [{
                "candidate_id": "secure_baseline", "status": projected["status"],
                "projected_targets_kW": secure.tolist(),
                "elapsed_seconds": projected.get("elapsed_seconds"),
                "solver_calls": copy.deepcopy(projected.get("solver_calls", []))}],
            "current_context": copy.deepcopy(context),
        }
        try:
            frame, current = encode_observation(history, context)
            norm = self.metadata["normalization"]
            frame_norm, context_norm, _, _ = _inputs(
                [{"frame": frame, "context": current, "delta_kW": [0.0] * 6,
                  "intervention": False}], norm)
            with torch.inference_mode():
                x = torch.as_tensor(frame_norm, device="cuda:0")
                c = torch.as_tensor(context_norm, device="cuda:0")
                devices = {str(p.device) for p in self.model.parameters()} | {str(x.device), str(c.device)}
                if devices != {"cuda:0"}:
                    raise RuntimeError("NN inference tensors/model are not on cuda:0")
                residual_norm, logit = self.model(x, c)
                residual = residual_norm[0].cpu().numpy().astype(np.float64)
                probability = float(torch.sigmoid(logit[0]).item())
            residual *= np.asarray(norm["target_scale_kW"], dtype=np.float64)
            if not np.all(np.isfinite(residual)) or not math.isfinite(probability):
                raise FloatingPointError("Nonfinite NN request")
            requested = np.maximum(0.0, secure + residual)
            requested[:3] = np.minimum(requested[:3], context["capacity_GT_kW"])
            requested[3] = min(requested[3], context["grid_import_limit_kW"])
            requested[5] = min(requested[5], context["capacity_ABS_kW"])
            diagnostic.update(
                intervention_probability=probability,
                predicted_delta_from_secure_kW=residual.tolist(),
                requested_six_targets_kW=requested.tolist(),
                delta_from_secure_plan_kW=(requested - secure).tolist(),
            )
            if self.metadata["gate_mode"] == "confidence" and probability < self.metadata["gate_threshold"]:
                diagnostic.update(strategy_status="confidence_gate_secure_identity",
                                  gate_rejected=True)
                return secure_request, diagnostic
            if np.max(np.abs(requested - secure)) <= 1e-7:
                diagnostic.update(strategy_status="neural_secure_identity", gate_rejected=False)
                return secure_request, diagnostic
            request = copy.deepcopy(secure_request)
            request["targets"] = dict(zip(ACTION_NAMES, map(float, requested)))
            validate_action(request)
            diagnostic.update(strategy_status="neural_feedback_six_port_request",
                              gate_rejected=False,
                              selected_candidate_id="neural_feedback",
                              baseline_request_submitted_unchanged=False)
            return request, diagnostic
        except (ValueError, FloatingPointError) as error:
            diagnostic.update(strategy_fallback=True,
                              strategy_status="neural_input_or_prediction_fallback",
                              fallback_reason=str(error), fallback_error_type=type(error).__name__)
            return secure_request, diagnostic


def load_neural_policy(model_dir, policy_id):
    """Resolve workspace-relative artifact paths and verify code/model bytes."""
    path, metadata = _load_artifacts(model_dir)
    if not isinstance(policy_id, str) or not policy_id:
        raise ValueError("Nonempty neural feedback policy_id required")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required for v5 neural feedback inference")
    model = _GRUNet(metadata["frame_dim"], metadata["context_dim"], metadata["hidden"])
    model.load_state_dict(torch.load(path / "model.pt", map_location="cpu", weights_only=True))
    if _weight_sha(model) != metadata["trained_weight_sha256"]:
        raise ValueError("Loaded GRU weights differ from fitted weight SHA")
    model.to("cuda:0").eval()
    return NeuralFeedbackPolicy(model, metadata, policy_id)


def create_gate_variant(base_model_dir, variant_dir, *, gate_mode="confidence",
                        threshold=None, calibration_source=None):
    """Freeze a separate model artifact after calibration-root gate selection."""
    if gate_mode != "confidence" or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise ValueError("Gate variant requires confidence mode and threshold in [0,1]")
    if (not isinstance(calibration_source, dict)
            or calibration_source.get("role") != "calibration"
            or not calibration_source.get("root_family_ids")
            or not calibration_source.get("source_sha256")):
        raise ValueError("Gate variant requires independent calibration-root source SHA provenance")
    source, base = _load_artifacts(base_model_dir)
    if base["gate_mode"] != "none":
        raise ValueError("Gate variant source must be an ungated fitted model")
    out = Path(variant_dir)
    if out.exists():
        raise FileExistsError("Use a fresh immutable gate-variant directory")
    out.mkdir(parents=True)
    for name in base["artifacts"]:
        shutil.copy2(source / name, out / name)
    variant = copy.deepcopy(base)
    variant.update(gate_mode="confidence", gate_threshold=float(threshold),
                   calibration_source=copy.deepcopy(calibration_source),
                   ungated_model_metadata_sha256=_sha(source / "metadata.json"),
                   artifacts={name: _sha(out / name) for name in base["artifacts"]})
    _new_json(out / "metadata.json", variant)
    return variant


def result_paths_for_policy(results_dir, teacher_policy_id, *, expected_count=None):
    """Find terminal teacher result receipts in one registered full-feedback batch."""
    directory = Path(results_dir)
    paths = []
    for path in sorted(directory.glob("pairs/*/attempt_*/result.json")):
        record = _json(path)
        if record.get("policy_id") == teacher_policy_id and record.get("kind") == "feedback":
            if record.get("terminal") and record.get("trajectory_path"):
                paths.append(path)
    if expected_count is not None and len(paths) != expected_count:
        raise ValueError(f"Expected {expected_count} terminal teacher results, found {len(paths)}")
    if not paths:
        raise ValueError("No terminal full-feedback teacher results found")
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fit = commands.add_parser("train")
    fit.add_argument("--manifest", required=True)
    fit.add_argument("--results-dir", required=True)
    fit.add_argument("--teacher", required=True)
    fit.add_argument("--out", required=True)
    fit.add_argument("--expect-results", type=int)
    fit.add_argument("--seed", type=int, default=5101)
    fit.add_argument("--hidden", type=int, default=64)
    fit.add_argument("--epochs", type=int, default=16)
    fit.add_argument("--batch-size", type=int, default=128)
    fit.add_argument("--learning-rate", type=float, default=1e-3)
    args = parser.parse_args(argv)
    if args.command == "train":
        paths = result_paths_for_policy(
            args.results_dir, args.teacher, expected_count=args.expect_results)
        report = train_from_rollouts(
            args.manifest, paths, args.out, teacher_policy_id=args.teacher,
            seed=args.seed, hidden=args.hidden, epochs=args.epochs,
            batch_size=args.batch_size, learning_rate=args.learning_rate)
        print(json.dumps({
            "model_dir": str(Path(args.out).resolve()),
            "source_trajectory_count": len(paths), "actual_train_device": report["actual_train_device"],
            "optimizer_updates": report["optimizer_updates"],
            "initial_weight_sha256": report["initial_weight_sha256"],
            "trained_weight_sha256": report["trained_weight_sha256"],
            "training_decisions": report["training_decisions"],
            "internal_holdout_decisions": report["internal_holdout_decisions"],
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
