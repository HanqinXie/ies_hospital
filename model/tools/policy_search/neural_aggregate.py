"""One train-only DAgger-style relabel and fresh CUDA fit for v5 GRU feedback.

NN-visited states are relabeled with the frozen teacher's current feedback rule
using the NN's own current secure projection/context. No future script or
counterfactual return becomes a training label. The original GRU code/model
remain byte-frozen and are used only as a registered starting policy.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import gzip
import json
import math
from pathlib import Path
import sys
import time
import traceback

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.data_contract import validate_action
from tools.integrated_learning.policies import ACTION_NAMES, baseline_request, legal_history
from tools.policy_search.policy import FeedbackPolicy, _observed_mode, _request_targets
from tools.policy_search.neural import (
    SCHEMA, CONTEXT_DIM, FRAME_DIM, _GRUNet, _inputs, _new_json, _normalization,
    _sha, _split_roots, _vec, _verify_signed, _weight_sha, collect_training_examples,
    encode_observation, load_neural_policy, result_paths_for_policy,
)


AGGREGATION_SCHEMA = "policy_search.v5.cuda_gru_feedback.aggregate_round1.v1"


def _teacher_spec(spec):
    if not isinstance(spec, dict) or set(spec) != {"policy_id", "kind", "family", "theta"}:
        raise ValueError("Complete registered teacher feedback spec required")
    if spec["kind"] != "feedback":
        raise ValueError("Aggregation teacher must be full-feedback policy")
    return FeedbackPolicy(spec["theta"], spec["policy_id"], family=spec["family"])


def teacher_request_at_current(history, context, secure_targets, teacher, previously_disturbed):
    """Reproduce frozen teacher current request, updating observed mode memory."""
    current = legal_history(history)
    secure = _vec(secure_targets, (6,), "current secure projection", nonnegative=True)
    frame, encoded_context = encode_observation(current, context)
    if not np.allclose(secure, context["current_plan_targets_kW"], atol=1e-6, rtol=0):
        raise ValueError("Reused current secure projection/context disagree")
    features = current[-1]["features"]
    mode = ("all_states" if teacher.family == "blend" else
            _observed_mode(features, previously_disturbed))
    disturbed = previously_disturbed or mode == "disturbed"
    request = baseline_request("secure_milp")
    requested = secure.copy()
    if not all(value == 0.0 for value in teacher.theta):
        gains = teacher._gains(mode)
        if gains[0] != 0.0:
            requested = _request_targets(secure, context, features, gains)
            if np.max(np.abs(requested - secure)) > 1e-7:
                request["targets"] = dict(zip(ACTION_NAMES, map(float, requested)))
    validate_action(request)
    return request, requested, frame, encoded_context, mode, disturbed


def relabel_nn_rollouts(manifest_path, nn_result_paths, *, teacher_spec, neural_policy_id):
    """Read registered train-root NN trajectories and relabel visited states."""
    teacher = _teacher_spec(teacher_spec)
    if not isinstance(neural_policy_id, str) or not neural_policy_id:
        raise ValueError("Nonempty registered neural policy ID required")
    mp = Path(manifest_path).resolve()
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest_signature = _verify_signed(manifest, "Manifest")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    paths = [Path(p).resolve() for p in nn_result_paths]
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("Nonempty unique neural result paths required")
    rows = []
    sources = []
    seen = set()
    unusable = Counter()
    for rp in paths:
        if rp.name != "result.json" or len(rp.parents) < 4:
            raise ValueError("Expected registered NN attempt result.json")
        plan = json.loads((rp.parents[3] / "registered_plan.json").read_text(encoding="utf-8"))
        plan_signature = _verify_signed(plan, "NN batch plan")
        if (Path(plan["manifest_path"]).resolve() != mp
                or plan["manifest_sha256"] != _sha(mp)
                or plan["manifest_signature"] != manifest_signature
                or plan.get("phase") != "train"):
            raise ValueError("NN trajectory is not registered to v5 train manifest")
        registered_teacher = [x for x in plan["policy_specs"] if x["policy_id"] == teacher.policy_id]
        registered_nn = [x for x in plan["policy_specs"] if x["policy_id"] == neural_policy_id]
        if (registered_teacher != [teacher_spec] or len(registered_nn) != 1
                or registered_nn[0].get("kind") != "neural_feedback"):
            raise ValueError("NN batch lacks exact frozen feedback teacher/model")
        result = json.loads(rp.read_text(encoding="utf-8"))
        sid = result.get("scenario_id")
        if sid in seen:
            raise ValueError("Duplicate NN visited scenario")
        seen.add(sid)
        if (sid not in scenes or sid not in plan["scenario_ids"]
                or result.get("phase") != "train"
                or result.get("policy_id") != neural_policy_id
                or result.get("kind") != "neural_feedback"
                or result.get("plan_signature") != plan_signature
                or not result.get("terminal")
                or not result.get("binding", {}).get("passed")):
            raise ValueError("NN result identity/binding invalid")
        scene = scenes[sid]
        split = manifest["splits"][sid]
        if (scene.get("experiment_role") != "train"
                or split.get("experiment_role") != "train"
                or scene.get("is_final_blind") or split.get("is_final_blind")):
            raise ValueError("Aggregation cannot read nontrain/final roots")
        name = result.get("trajectory_path")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("Missing registered NN raw trajectory")
        raw = rp.parent / name
        raw_sha = _sha(raw)
        if raw_sha != result.get("trajectory_sha256"):
            raise ValueError("NN raw trajectory SHA mismatch")
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        if (payload.get("scenario") != scene
                or payload.get("summary", {}).get("status") != result.get("status")):
            raise ValueError("NN raw trajectory differs from registered result")
        decisions = payload.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("NN full-feedback trajectory lacks decision history")
        previously_disturbed = False
        accepted = 0
        for index, decision in enumerate(decisions):
            history = legal_history(decisions[:index + 1])
            features = history[-1]["features"]
            # Mode is updated even if a current context is unavailable.
            if teacher.family == "mode":
                observed_mode = _observed_mode(features, previously_disturbed)
                if observed_mode == "disturbed":
                    previously_disturbed = True
            diagnostic = decision.get("policy_diagnostic")
            if not isinstance(diagnostic, dict):
                unusable["missing_policy_diagnostic"] += 1
                continue
            context = diagnostic.get("current_context")
            queries = diagnostic.get("current_planning_queries")
            if (not isinstance(context, dict) or not isinstance(queries, list)
                    or not queries or queries[0].get("candidate_id") != "secure_baseline"
                    or queries[0].get("status") != "success"):
                unusable["missing_current_context_or_projection"] += 1
                continue
            if (diagnostic.get("model_id") != neural_policy_id
                    or diagnostic.get("future_rollouts_online") != 0):
                unusable["invalid_neural_diagnostic_identity"] += 1
                continue
            secure = _vec(queries[0].get("projected_targets_kW"), (6,),
                          "NN current secure projection", nonnegative=True)
            request, requested, frame, current, mode, previously_disturbed = teacher_request_at_current(
                history, context, secure, teacher, previously_disturbed)
            rows.append({
                "scenario_id": sid, "root_family_id": scene["root_family_id"],
                "group_id": split["group_id"], "decision_time_min": float(decision["decision_time_min"]),
                "history_frame_count": len(history), "frame": frame, "context": current,
                "secure_targets_kW": secure, "requested_targets_kW": requested,
                "delta_kW": (requested - secure).tolist(), "intervention": "targets" in request,
                "source_role": "nn_visited_relabel", "source_trajectory_sha256": raw_sha,
                "teacher_mode": mode,
            })
            accepted += 1
        sources.append({
            "scenario_id": sid, "root_family_id": scene["root_family_id"],
            "group_id": split["group_id"], "role": "train",
            "nn_policy_id": neural_policy_id, "teacher_policy_id": teacher.policy_id,
            "result_path": str(rp), "result_sha256": _sha(rp),
            "raw_path": str(raw), "sha256": raw_sha, "status": result["status"],
            "observed_decisions": len(decisions), "usable_relabels": accepted,
            "registered_plan_signature": plan_signature,
        })
    if not rows:
        raise ValueError("No legal NN-visited current state can be relabeled")
    audit = {
        "schema": AGGREGATION_SCHEMA + ".relabel_audit",
        "manifest_path": str(mp), "manifest_sha256": _sha(mp),
        "manifest_signature": manifest_signature,
        "teacher_spec": copy.deepcopy(teacher_spec),
        "neural_policy_id": neural_policy_id, "source_trajectories": sources,
        "source_trajectory_count": len(sources),
        "root_family_ids": sorted({r["root_family_id"] for r in rows}),
        "usable_relabels": len(rows), "teacher_interventions": sum(r["intervention"] for r in rows),
        "teacher_secure_identities": sum(not r["intervention"] for r in rows),
        "unusable_decisions": sum(unusable.values()), "unusable_reasons": dict(unusable),
        "label_semantics": "Frozen feedback teacher current request on NN-visited state; no future script or counterfactual outcome label",
        "final_12_backgrounds_opened": False,
    }
    return rows, audit


def combine_visited_examples(teacher_rows, nn_rows):
    """Join original teacher and NN-visited states without crossing root splits."""
    original = {(r["scenario_id"], r["decision_time_min"]): r for r in teacher_rows}
    if len(original) != len(teacher_rows):
        raise ValueError("Duplicate original teacher state key")
    same_initial_checked = 0
    novel = 0
    same = 0
    for row in nn_rows:
        reference = original.get((row["scenario_id"], row["decision_time_min"]))
        if reference is None:
            novel += 1
            continue
        if row["root_family_id"] != reference["root_family_id"]:
            raise ValueError("Paired teacher/NN scenario crosses train root")
        if row["decision_time_min"] == 0.0:
            if not np.allclose(row["requested_targets_kW"],
                               reference["requested_targets_kW"], atol=1e-6, rtol=0):
                raise ValueError("Reconstructed teacher request differs at matched initial state")
            same_initial_checked += 1
        if (np.allclose(row["frame"], reference["frame"], atol=1e-7, rtol=0)
                and np.allclose(row["context"], reference["context"], atol=1e-7, rtol=0)):
            same += 1
        else:
            novel += 1
    if novel == 0:
        raise ValueError("No new NN-visited legal observation for aggregation")
    combined = [{**r, "source_role": "original_teacher_visited"} for r in teacher_rows] + list(nn_rows)
    audit = {
        "original_teacher_rows": len(teacher_rows),
        "nn_visited_relabel_rows": len(nn_rows),
        "combined_rows": len(combined),
        "matched_initial_teacher_request_checks": same_initial_checked,
        "novel_nn_visited_observations": novel,
        "same_teacher_and_nn_observations": same,
        "distinct_train_roots": len({r["root_family_id"] for r in combined}),
        "overlap_policy": "Same root remains in same internal fit/holdout split",
    }
    return combined, audit


def train_aggregate_round(manifest_path, teacher_result_paths, nn_result_paths, out_dir, *,
                          teacher_spec, neural_policy_id, seed=5102, hidden=64,
                          epochs=16, batch_size=128, learning_rate=1e-3, min_roots=6):
    """Fit one fresh CUDA GRU from original teacher plus relabeled NN states."""
    teacher = _teacher_spec(teacher_spec)
    out = Path(out_dir)
    if out.exists():
        raise FileExistsError("Use a fresh aggregate fit directory; retain every attempt")
    out.mkdir(parents=True)
    started = time.perf_counter()
    _new_json(out / "fit_started.json", {
        "schema": AGGREGATION_SCHEMA + ".fit_started",
        "round": 1, "manifest_path": str(Path(manifest_path).resolve()),
        "teacher_spec": copy.deepcopy(teacher_spec), "neural_policy_id": neural_policy_id,
        "seed": seed, "hidden": hidden, "epochs": epochs,
        "batch_size": batch_size, "learning_rate": learning_rate,
        "requested_device": "cuda:0", "aggregation_module_sha256": _sha(__file__),
        "frozen_neural_module_sha256": _sha(ROOT / "tools/policy_search/neural.py"),
        "frozen_feedback_policy_sha256": _sha(ROOT / "tools/policy_search/policy.py"),
    })
    try:
        if (type(seed) is not int or type(hidden) is not int or type(epochs) is not int
                or type(batch_size) is not int or hidden < 4 or epochs < 1 or batch_size < 1
                or not math.isfinite(float(learning_rate)) or learning_rate <= 0
                or type(min_roots) is not int or min_roots < 1):
            raise ValueError("Invalid aggregate GRU fit configuration")
        teacher_rows, teacher_audit = collect_training_examples(
            manifest_path, teacher_result_paths, teacher_policy_id=teacher.policy_id)
        nn_rows, nn_audit = relabel_nn_rollouts(
            manifest_path, nn_result_paths, teacher_spec=teacher_spec,
            neural_policy_id=neural_policy_id)
        if (len(teacher_audit["root_family_ids"]) < min_roots
                or len(nn_audit["root_family_ids"]) < min_roots):
            raise ValueError("Too few independent train roots for registered aggregate fit")
        if set(teacher_audit["root_family_ids"]) != set(nn_audit["root_family_ids"]):
            raise ValueError("Teacher and NN aggregate roots do not match")
        rows, join_audit = combine_visited_examples(teacher_rows, nn_rows)
        source_audit = {
            "schema": AGGREGATION_SCHEMA + ".source_audit",
            "round": 1, "manifest_sha256": teacher_audit["manifest_sha256"],
            "original_teacher": teacher_audit, "nn_visited_relabel": nn_audit,
            "join": join_audit, "role": "train",
            "label_semantics": nn_audit["label_semantics"],
            "final_12_backgrounds_opened": False,
        }
        _new_json(out / "source_audit.json", source_audit)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; aggregate NN has no CPU fit fallback")
        fit_roots, holdout_roots = _split_roots(rows, seed)
        fit = [r for r in rows if r["root_family_id"] in fit_roots]
        holdout = [r for r in rows if r["root_family_id"] in holdout_roots]
        if not any(r["intervention"] for r in fit):
            raise ValueError("Aggregate fit roots contain no nonidentity teacher request")
        norm = _normalization(fit)
        tf, tc, td, ti = _inputs(fit, norm)
        vf, vc, vd, vi = _inputs(holdout, norm) if holdout else (
            np.empty((0, 4, FRAME_DIM), dtype=np.float32),
            np.empty((0, CONTEXT_DIM), dtype=np.float32),
            np.empty((0, 6), dtype=np.float32),
            np.empty((0,), dtype=np.float32),
        )
        np.savez_compressed(
            out / "fit_examples.npz", train_frame=tf, train_context=tc,
            train_delta=td, train_intervention=ti,
            holdout_frame=vf, holdout_context=vc,
            holdout_delta=vd, holdout_intervention=vi,
            train_root_family_id=np.asarray([r["root_family_id"] for r in fit]),
            holdout_root_family_id=np.asarray([r["root_family_id"] for r in holdout]),
            train_source_role=np.asarray([r["source_role"] for r in fit]),
            holdout_source_role=np.asarray([r["source_role"] for r in holdout]),
            train_scenario_id=np.asarray([r["scenario_id"] for r in fit]),
            holdout_scenario_id=np.asarray([r["scenario_id"] for r in holdout]),
        )
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        device = torch.device("cuda:0")
        torch.cuda.init()
        torch.cuda.reset_peak_memory_stats(device)
        model = _GRUNet(FRAME_DIM, CONTEXT_DIM, hidden).to(device)
        initial_weight = _weight_sha(model)
        tensors = tuple(torch.as_tensor(x, device=device) for x in (tf, tc, td, ti))
        validation = tuple(torch.as_tensor(x, device=device) for x in (vf, vc, vd, vi))
        if {str(p.device) for p in model.parameters()} | {str(x.device) for x in tensors} != {"cuda:0"}:
            raise RuntimeError("Aggregate model/tensors are not on actual cuda:0")
        optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
        curves = []
        updates = 0

        def losses(chunk):
            delta, logit = model(chunk[0], chunk[1])
            mse = torch.mean((delta - chunk[2]) ** 2)
            bce = nn.functional.binary_cross_entropy_with_logits(logit, chunk[3])
            return mse + 0.2 * bce, mse, bce

        for epoch in range(epochs):
            model.train()
            for indices in torch.randperm(len(fit), device=device).split(batch_size):
                subset = tuple(x[indices] for x in tensors)
                optimizer.zero_grad(set_to_none=True)
                total, _, _ = losses(subset)
                if not bool(torch.isfinite(total)):
                    raise FloatingPointError("Nonfinite aggregate GRU loss")
                total.backward()
                optimizer.step()
                updates += 1
            model.eval()
            with torch.inference_mode():
                loss, mse, bce = losses(tensors)
                record = {"epoch": epoch + 1, "fit_total": float(loss.item()),
                          "fit_delta_mse": float(mse.item()),
                          "fit_intervention_bce": float(bce.item()),
                          "optimizer_updates_cumulative": updates}
                if holdout:
                    vloss, vmse, vbce = losses(validation)
                    record.update(holdout_total=float(vloss.item()),
                                  holdout_delta_mse=float(vmse.item()),
                                  holdout_intervention_bce=float(vbce.item()))
                curves.append(record)
        torch.cuda.synchronize(device)
        trained_weight = _weight_sha(model)
        if updates < 1 or initial_weight == trained_weight:
            raise RuntimeError("Aggregate CUDA fit made no model-weight update")
        torch.save(model.state_dict(), out / "model.pt")
        binding = {
            "aggregation_module_sha256": _sha(__file__),
            "frozen_neural_module_sha256": _sha(ROOT / "tools/policy_search/neural.py"),
            "frozen_feedback_policy_sha256": _sha(ROOT / "tools/policy_search/policy.py"),
            "source_audit_sha256": _sha(out / "source_audit.json"),
            "round": 1,
        }
        _new_json(out / "aggregate_source_binding.json", binding)
        report = {
            "schema": AGGREGATION_SCHEMA + ".fit_report", "round": 1,
            "actual_train_device": "cuda:0",
            "actual_holdout_device": "cuda:0" if holdout else None,
            "torch_version": torch.__version__, "torch_cuda_version": torch.version.cuda,
            "cuda_device_name": torch.cuda.get_device_name(device),
            "peak_cuda_memory_allocated_bytes": int(torch.cuda.max_memory_allocated(device)),
            "architecture": "4-frame GRU plus current-context branch",
            "frame_dimension": FRAME_DIM, "context_dimension": CONTEXT_DIM,
            "hidden": hidden, "parameter_count": sum(p.numel() for p in model.parameters()),
            "seed": seed, "epochs": epochs, "batch_size": batch_size,
            "learning_rate": learning_rate, "optimizer": "AdamW",
            "optimizer_updates": updates,
            "initial_weight_sha256": initial_weight,
            "trained_weight_sha256": trained_weight,
            "training_roots": fit_roots, "internal_holdout_roots": holdout_roots,
            "training_decisions": len(fit), "internal_holdout_decisions": len(holdout),
            "original_teacher_decisions": len(teacher_rows),
            "nn_visited_relabel_decisions": len(nn_rows),
            "novel_nn_visited_observations": join_audit["novel_nn_visited_observations"],
            "source_trajectory_sha256": sorted({
                r["source_trajectory_sha256"] for r in rows}),
            "source_audit_sha256": _sha(out / "source_audit.json"),
            "loss_curve": curves, "elapsed_seconds": time.perf_counter() - started,
            "label_semantics": nn_audit["label_semantics"],
        }
        _new_json(out / "fit_report.json", report)
        metadata = {
            "schema": SCHEMA, "architecture": "GRU", "frame_dim": FRAME_DIM,
            "context_dim": CONTEXT_DIM, "hidden": hidden, "normalization": norm,
            "teacher_policy_id": teacher.policy_id,
            "source_module_sha256": binding["frozen_neural_module_sha256"],
            "aggregation_module_sha256": binding["aggregation_module_sha256"],
            "source_audit_sha256": binding["source_audit_sha256"],
            "trained_weight_sha256": trained_weight,
            "gate_mode": "none", "gate_threshold": None, "calibration_source": None,
            "original_secure_identity": baseline_request("secure_milp"),
            "future_rollouts_online": 0, "aggregation_round": 1,
            "artifacts": {name: _sha(out / name) for name in (
                "fit_started.json", "source_audit.json", "fit_examples.npz",
                "aggregate_source_binding.json", "fit_report.json", "model.pt")},
        }
        _new_json(out / "metadata.json", metadata)
        loaded = load_neural_policy(out, "aggregate_round1_reload_check")
        if loaded.metadata["trained_weight_sha256"] != trained_weight:
            raise AssertionError("Aggregate saved model reload differs")
        return report
    except Exception as error:
        _new_json(out / "fit_error.json", {
            "schema": AGGREGATION_SCHEMA + ".fit_error",
            "error_type": type(error).__name__, "message": str(error),
            "traceback": traceback.format_exc(),
            "elapsed_seconds": time.perf_counter() - started,
        })
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--teacher-results-dir", required=True)
    parser.add_argument("--nn-results-dir", required=True)
    parser.add_argument("--nn-policy-id", required=True)
    parser.add_argument("--teacher-policy-id", default="blend_overload")
    parser.add_argument("--out", required=True)
    parser.add_argument("--expect-results", type=int, default=6)
    parser.add_argument("--seed", type=int, default=5102)
    parser.add_argument("--epochs", type=int, default=16)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=128)
    args = parser.parse_args(argv)
    registered = json.loads((Path(args.nn_results_dir) / "registered_plan.json").read_text(encoding="utf-8"))
    teacher_spec = next(x for x in registered["policy_specs"] if x["policy_id"] == args.teacher_policy_id)
    original_paths = result_paths_for_policy(
        args.teacher_results_dir, args.teacher_policy_id, expected_count=args.expect_results)
    nn_paths = []
    for path in sorted(Path(args.nn_results_dir).glob("pairs/*/attempt_*/result.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("policy_id") == args.nn_policy_id and record.get("terminal"):
            nn_paths.append(path)
    if len(nn_paths) != args.expect_results:
        raise ValueError(f"Expected {args.expect_results} terminal NN results, found {len(nn_paths)}")
    report = train_aggregate_round(
        args.manifest, original_paths, nn_paths, args.out,
        teacher_spec=teacher_spec, neural_policy_id=args.nn_policy_id,
        seed=args.seed, hidden=args.hidden, epochs=args.epochs,
        batch_size=args.batch_size, min_roots=args.expect_results)
    print(json.dumps({
        "model_dir": str(Path(args.out).resolve()),
        "round": 1, "actual_train_device": report["actual_train_device"],
        "optimizer_updates": report["optimizer_updates"],
        "original_teacher_decisions": report["original_teacher_decisions"],
        "nn_visited_relabel_decisions": report["nn_visited_relabel_decisions"],
        "novel_nn_visited_observations": report["novel_nn_visited_observations"],
        "initial_weight_sha256": report["initial_weight_sha256"],
        "trained_weight_sha256": report["trained_weight_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
