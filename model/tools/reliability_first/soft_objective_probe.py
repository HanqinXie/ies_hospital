"""Versioned, train-only current-action opportunity probe around secure MILP.

This is an offline causal diagnostic. Every candidate changes one current
request, then the original secure policy resumes. Named future scenarios and
the saved execution snapshot are used only by this training-side runner.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

import numpy as np

from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.dataset_export import json_clean
from tools.control_improvement.branches import window_targets
from tools.control_improvement.episode import run_policy_episode
from tools.control_improvement.ranking import TARGET_NAMES
from tools.control_improvement.runtime import extract_current_problem, project_current
from tools.integrated_learning.policies import ACTION_NAMES, baseline_request, legal_history
from tools.integrated_learning.snapshot import ExecutionSnapshot, capture_execution, digest
from tools.reliability_first.branches import FirstSecureCandidatePolicy, _execution_class, save_raw
from tools.reliability_first.diagnostics import opportunity_rows


FUTURE_IDS = ("original", "stress1", "stress2", "stress3")
WEIGHTS = (("security_weight_0p7", .7), ("security_weight_2", 2.),
           ("security_weight_6", 6.), ("security_weight_10", 10.))
PHASES = ("preparation", "early_response", "later_response")


def sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _save_new(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(json_clean(payload), stream, ensure_ascii=False, indent=2, allow_nan=False)


def _journal(path: Path, **event) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(json_clean(event), ensure_ascii=False, allow_nan=False) + "\n")


def select_train_probe_indices(plan: list[dict], early_indices: list[int]) -> list[int]:
    """Two per registered train group; favor current-clock 105--135 minutes.

    Selection reads no trajectory, label, branch result, future event clock, or
    candidate performance. Other phases serve as predeclared controls.
    """
    by_group: dict[str, dict[str, int]] = defaultdict(dict)
    for index in early_indices:
        row = plan[index]
        if row["experiment_role"] != "pilot_train" or row["is_final_blind"]:
            continue
        phase = row["snapshot_phase_design"]
        if phase in PHASES:
            if phase in by_group[row["group_id"]]:
                raise ValueError("Repeated train phase within registered group")
            by_group[row["group_id"]][phase] = index
    groups = sorted(by_group)
    if len(groups) != 12 or any(not set(PHASES) <= set(by_group[group]) for group in groups):
        raise ValueError("Probe needs 12 registered train groups with three clock phases")
    near = [group for group in groups if 105. <= float(plan[by_group[group]["early_response"]]["snapshot_minute"]) <= 135.]
    other = [group for group in groups if group not in near]
    if not 8 <= len(near) <= 10:
        raise ValueError("Current-clock response quota changed; version the design")
    # Exactly two per group. The first four near groups get preparation controls;
    # remaining near groups get later controls. Non-near groups get both.
    selected = [by_group[group]["early_response"] for group in near]
    selected += [by_group[group]["preparation"] for group in other + near[:4]]
    selected += [by_group[group]["later_response"] for group in near[4:] + other]
    if len(selected) != 24 or len(set(selected)) != 24:
        raise AssertionError("Probe clock/group selection lost a snapshot")
    return sorted(selected)


def _validate_registration_count(selected: list[int], registered: int) -> None:
    if len(selected) != registered or registered != 24 or len(set(selected)) != 24:
        raise ValueError("Probe requires exactly 24 unique registered train snapshots")


def make_candidate_requests(context: dict, secure_candidate: dict) -> list[dict]:
    """Preserve exact secure and add four soft weights plus <=2 small residuals."""
    if secure_candidate.get("candidate_id") != "secure_baseline" or secure_candidate.get("request") != baseline_request("secure_milp"):
        raise ValueError("Original secure request must occupy candidate zero")
    base = np.asarray(secure_candidate["projected_targets_kW"], dtype=float)
    cap = np.asarray(context["capacity_GT_kW"], dtype=float)
    elapsed = np.asarray(context["overload_minutes_GT"], dtype=float)
    grid_limit = float(context["grid_import_limit_kW"])
    if base.shape != (6,) or cap.shape != (3,) or elapsed.shape != (3,) or not np.all(np.isfinite(base)) or np.any(base < 0) or not np.all(np.isfinite(cap)) or np.any(cap < 0) or not np.all(np.isfinite(elapsed)) or np.any(elapsed < 0) or not math.isfinite(grid_limit):
        raise ValueError("Invalid current legal dispatch or margins")
    result = [{"candidate_id": "secure_baseline", "request": copy.deepcopy(secure_candidate["request"]),
               "generation": "unchanged_original_secure_full_dispatch"}]
    result += [{"candidate_id": name, "request": {"security": {"gt_loading_penalty_USD_per_kWh": weight}},
                "generation": "same_security_milp_final_soft_objective_weight"} for name, weight in WEIGHTS]
    loading = np.divide(base[:3], cap, out=np.zeros(3), where=cap > 0)
    risk = loading * (1. + elapsed / 15.)
    donor = int(np.argmax(risk))
    if base[donor] > 1e-8:
        grid_delta = min(.08 * cap[donor], .2 * base[donor], max(0., grid_limit - base[3]))
        if grid_delta > 1e-6:
            ports = base.copy(); ports[donor] -= grid_delta; ports[3] += grid_delta
            result.append({"candidate_id": "residual_grid_relief",
                           "request": {"security": copy.deepcopy(baseline_request("secure_milp")["security"]),
                                       "targets": dict(zip(ACTION_NAMES, map(float, ports)))},
                           "generation": "current_overload_weighted_GT_to_known_grid_headroom",
                           "residual_kW": float(grid_delta), "donor_GT_index": donor})
        recipients = [i for i in range(3) if i != donor and cap[i] - base[i] > 1e-6]
        if recipients:
            recipient = min(recipients, key=lambda i: (risk[i], i))
            balance_delta = min(.08 * cap[donor], .2 * base[donor], max(0., cap[recipient] - base[recipient]))
            if balance_delta > 1e-6:
                ports = base.copy(); ports[donor] -= balance_delta; ports[recipient] += balance_delta
                result.append({"candidate_id": "residual_GT_balance",
                               "request": {"security": copy.deepcopy(baseline_request("secure_milp")["security"]),
                                           "targets": dict(zip(ACTION_NAMES, map(float, ports)))},
                               "generation": "current_overload_weighted_GT_headroom_shift",
                               "residual_kW": float(balance_delta), "donor_GT_index": donor,
                               "recipient_GT_index": int(recipient)})
    if len(result) > 7:
        raise AssertionError("Probe exceeded registered finite candidate set")
    return result


def verify_reused_baseline_raw(path: Path, expected_sha256: str, *, scenario_id: str,
                               future_id: str, manifest_signature: str,
                               snapshot_minute: float) -> dict:
    """Hash and identity-check an actual saved secure continuation."""
    if sha(path) != expected_sha256:
        raise ValueError("Reused secure raw SHA mismatch")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    summary = payload["summary"]
    expected_policy = "secure_milp" if future_id == "original" else f"v4_{future_id}_00"
    if (summary.get("scenario_id") != scenario_id or summary.get("manifest_signature") != manifest_signature
            or summary.get("policy_id") != expected_policy or payload.get("seed") is None
            or float(payload.get("continuation_start_minute", -1)) != float(snapshot_minute)
            or not payload.get("decisions") or float(payload["decisions"][0]["decision_time_min"]) != float(snapshot_minute)
            or payload["decisions"][0].get("requested_command") != baseline_request("secure_milp")):
        raise ValueError("Reused secure raw identity/request mismatch")
    return payload


def verify_saved_source(run_dir: Path, index: int, entry: dict, manifest: dict,
                        registered_source_sha256: dict) -> dict:
    """Require a registered, complete v1 training source and four actual raws."""
    run_dir = Path(run_dir)
    if entry["experiment_role"] != "pilot_train" or entry["is_final_blind"]:
        raise ValueError("Probe source is not registered train")
    root = run_dir / "branches" / f"snapshot_{index:03d}" / "attempt_0001"
    result_path = root / "BRANCH_RESULT.json"
    receipt_sha = []
    for line in (run_dir / "branches/collection_attempts.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # A concurrently appended trailing line is not a receipt.
        if record.get("event") == "finished" and record.get("stage") == "early" and record.get("index") == index and record.get("exit_code") == 0:
            receipt_sha.append(record.get("result_sha256"))
    if len(receipt_sha) != 1 or sha(result_path) != receipt_sha[0]:
        raise ValueError("Registered v1 branch result SHA/attempt receipt mismatch")
    branch = _load(result_path)
    branch_plan = _load(root / "plan.json")
    if (branch.get("status") != "complete" or branch.get("D0_passed") is not True
            or branch.get("source_sha256") != registered_source_sha256
            or branch_plan.get("source_sha256") != registered_source_sha256
            or branch_plan.get("entry") != entry
            or branch_plan.get("manifest_signature") != manifest["signature"]
            or branch.get("scenario_id") != entry["scenario_id"]
            or branch.get("group_id") != entry["group_id"] or branch.get("role") != "train"
            or branch.get("snapshot_minute") != entry["snapshot_minute"]):
        raise ValueError("Reused v1 branch source/role/plan mismatch")
    scenario = next(s for s in manifest["scenarios"] if s["id"] == entry["scenario_id"])
    if manifest["splits"][scenario["id"]]["group_id"] != entry["group_id"]:
        raise ValueError("Frozen manifest group mismatch")
    wrapper = _load(root / "execution_snapshot.json")
    saved = wrapper["payload"]
    if digest(saved) != wrapper.get("content_sha256") or saved["binding"].get("scenario_id") != scenario["id"] or saved["binding"].get("manifest_signature") != manifest["signature"]:
        raise ValueError("Saved execution snapshot SHA/identity mismatch")
    current = _load(root / "CURRENT_CANDIDATES.json")
    if current["candidates"][0]["candidate_id"] != "secure_baseline" or current["candidates"][0]["request"] != baseline_request("secure_milp") or float(current["context"]["time_minute"]) != float(entry["snapshot_minute"]):
        raise ValueError("Saved current baseline identity mismatch")
    legal_history(current["history"])
    futures = _load(root / "CONDITIONAL_FUTURES.json")
    if futures.get("ids") != list(FUTURE_IDS) or len(futures.get("scenarios", [])) != 4 or futures["scenarios"][0] != scenario or float(futures["same_known_prefix_minute"]) != float(entry["snapshot_minute"]):
        raise ValueError("Saved conditional-future identity mismatch")
    baseline_outcomes = {o["future_id"]: o for o in branch["outcomes"] if o["candidate_id"] == "secure_baseline"}
    if set(baseline_outcomes) != set(FUTURE_IDS):
        raise ValueError("Four secure baseline outcomes required")
    baseline_payloads = {}
    for future_id in FUTURE_IDS:
        outcome = baseline_outcomes[future_id]
        name = outcome["trajectory_file"]
        if branch["raw_trajectory_hashes"].get(name) != outcome["trajectory_sha256"]:
            raise ValueError("Registered v1 raw SHA table mismatch")
        payload = verify_reused_baseline_raw(root / name, outcome["trajectory_sha256"],
            scenario_id=scenario["id"], future_id=future_id,
            manifest_signature=manifest["signature"], snapshot_minute=entry["snapshot_minute"])
        if payload["scenario"] != futures["scenarios"][FUTURE_IDS.index(future_id)] or payload["summary"].get("group_id") != entry["group_id"] or payload["summary"].get("experiment_role") != "pilot_train":
            raise ValueError("Reused secure raw scenario/group mismatch")
        baseline_payloads[future_id] = payload
    ref_path = root / "reference_capture.json.gz"
    if sha(ref_path) != branch["raw_trajectory_hashes"].get(ref_path.name):
        raise ValueError("Reused reference capture SHA mismatch")
    with gzip.open(ref_path, "rt", encoding="utf-8") as stream:
        reference = json.load(stream)
    if reference["summary"]["scenario_id"] != scenario["id"] or reference["summary"]["manifest_signature"] != manifest["signature"] or reference["summary"]["policy_id"] != "secure_milp" or reference["scenario"] != scenario:
        raise ValueError("Reused reference capture identity mismatch")
    prefix = [copy.deepcopy(d) for d in reference["decisions"] if float(d["decision_time_min"]) < float(entry["snapshot_minute"])]
    if len(prefix) != int(entry["snapshot_step"]):
        raise ValueError("Reused legal decision prefix has wrong length")
    return {"root": root, "branch": branch, "scenario": scenario, "current": current,
            "futures": futures, "baseline_outcomes": baseline_outcomes,
            "baseline_payloads": baseline_payloads, "prefix_decisions": prefix,
            "snapshot_path": root / "execution_snapshot.json",
            "snapshot_content_sha256": wrapper["content_sha256"],
            "source_result_sha256": receipt_sha[0]}


def classify_paired_targets(actual: list, reference: list, *, actual_mask: list[bool] | None = None,
                            reference_mask: list[bool] | None = None) -> dict:
    """Compare P0--P3 in original units; preserve unknown coordinates."""
    n = len(TARGET_NAMES)
    if len(actual) != n or len(reference) != n:
        raise ValueError("Full 19-coordinate target rows required")
    am = [v is not None for v in actual] if actual_mask is None else list(actual_mask)
    rm = [v is not None for v in reference] if reference_mask is None else list(reference_mask)
    if len(am) != n or len(rm) != n or any(type(m) is not bool for m in am + rm):
        raise ValueError("Malformed target-known masks")
    delta = [float(a) - float(b) if ma and mb else None
             for a, b, ma, mb in zip(actual, reference, am, rm)]
    groups = {"P0": range(0, 5), "P1": range(5, 8),
              "P2": range(8, 11), "P3": range(11, 14)}
    by_priority = {}
    for priority, indices in groups.items():
        values = [delta[j] for j in indices]
        if any(v is None or not math.isfinite(v) for v in values):
            by_priority[priority] = "unknown"; continue
        sign = 0
        for j in indices:
            resolution = 0. if j == 0 else 1e-6 if j == 1 else .01
            if delta[j] < -resolution:
                sign = -1; break
            if delta[j] > resolution:
                sign = 1; break
        by_priority[priority] = {0: "tie", -1: "better", 1: "worse"}[sign]
    return {"target_names": list(TARGET_NAMES), "delta": delta,
            "by_priority": by_priority,
            "comparison_semantics": "P0 binary/minutes/e,h,c then per-priority e,h,c lex; resolutions 0, 1e-6 min, 0.01 kWh"}


def load_saved_execution_snapshot(source: dict, manifest: dict) -> ExecutionSnapshot:
    """Initialize only the template type/shared data, then restore saved state."""
    template = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    template.reset(scenario=source["scenario"], seed=int(manifest["options"]["seed"]))
    wrapper = _load(source["snapshot_path"])
    return ExecutionSnapshot.load(source["snapshot_path"], template=template,
                                  expected_binding=wrapper["payload"]["binding"])


def reconstruct_current_context(problem, history: list[dict], secure_command: dict) -> dict:
    """Only the pure current problem owns the legal planning context API."""
    return problem.policy_context(legal_history(history), secure_command)


def _validate_design(run_dir: Path) -> tuple[dict, dict, list[dict], dict]:
    design_path = run_dir / "design/soft_objective_probe_v2.json"
    design = _load(design_path)
    if design.get("schema") != "reliability_first.soft_objective_probe_design.v2" or design.get("output_directory") != "soft_objective_probe_v2":
        raise ValueError("Unregistered probe design schema")
    previous = design.get("supersedes", {})
    for name, hash_key in (("design_path", "design_sha256"),
                           ("output_summary_path", "output_summary_sha256"),
                           ("source_snapshot_path", "probe_source_sha256")):
        if not previous.get(name) or sha(run_dir / previous[name]) != previous.get(hash_key):
            raise ValueError("Prior v1 smoke provenance changed")
    if previous.get("old_new_continuation_attempts") != 0 or previous.get("preserve_old_attempts") is not True:
        raise ValueError("V2 transition must account for old smoke attempts")
    paths = {"manifest_sha256": run_dir / design["manifest_path"],
             "snapshot_plan_sha256": run_dir / design["snapshot_plan_path"],
             "branch_collection_design_sha256": run_dir / design["branch_collection_design_path"]}
    for key, path in paths.items():
        if sha(path) != design[key]:
            raise ValueError(f"Frozen probe {key} mismatch")
    manifest = _load(paths["manifest_sha256"])
    plan = _load(paths["snapshot_plan_sha256"])
    collection = _load(paths["branch_collection_design_sha256"])
    if collection["branch_source_sha256"] != design["branch_source_sha256"]:
        raise ValueError("Frozen branch source registry mismatch")
    for name, expected in design["branch_source_sha256"].items():
        if sha(Path(__file__).with_name(name)) != expected:
            raise ValueError(f"Frozen old branch source changed: {name}")
    if (select_train_probe_indices(plan, collection["early_indices"]) != design["selected_snapshot_indices"]
            or design.get("pilot_train_indices") != design["selected_snapshot_indices"]):
        raise ValueError("Frozen clock/group selection differs from registered plan")
    _validate_registration_count(design["selected_snapshot_indices"], design["registered_snapshots"])
    if (design["future_ids"] != list(FUTURE_IDS)
            or design["candidate_design"]["security_final_soft_weights_USD_per_kWh"] != [weight for _, weight in WEIGHTS]
            or design["budget"]["max_new_continuation_attempts"] != 576
            or manifest["signature"] != collection.get("manifest_signature", manifest["signature"])):
        raise ValueError("Probe design candidate/future/budget mismatch")
    binding = {"probe_source_sha256": sha(__file__), "design_sha256": sha(design_path),
               "manifest_sha256": sha(paths["manifest_sha256"]),
               "snapshot_plan_sha256": sha(paths["snapshot_plan_sha256"]),
               "branch_collection_design_sha256": sha(paths["branch_collection_design_sha256"]),
               "branch_source_sha256": design["branch_source_sha256"]}
    return design, manifest, plan, binding


def _unknown_outcome(candidate_id: str, candidate_index: int, future_id: str, status: str,
                     *, error: dict | None = None) -> dict:
    return {"candidate_id": candidate_id, "candidate_index": candidate_index,
            "future_id": future_id, "status": status, "target_names": list(TARGET_NAMES),
            "targets": [None] * len(TARGET_NAMES), "target_mask": [False] * len(TARGET_NAMES),
            "effective_execution_class": "request_not_generated" if status == "projection_error" else "unknown",
            "trajectory_file": None, "trajectory_sha256": None, "error": error}


def _run_snapshot(run_dir: Path, output_root: Path, index: int, entry: dict,
                  manifest: dict, design: dict, binding: dict) -> dict:
    attempt = output_root / f"snapshot_{index:03d}" / "attempt_0001"
    attempt.mkdir(parents=True, exist_ok=False)
    log = attempt / "attempts.jsonl"
    _save_new(attempt / "plan.json", {"schema": "reliability_first.soft_objective_probe_attempt.v1",
        "index": index, "entry": entry, "design_sha256": binding["design_sha256"],
        "source_binding": binding, "continuation_policy_id": "secure_milp",
        "observation_window_minutes": design["target_window_minutes"],
        "online_future_access": False})
    _journal(output_root / "attempts.jsonl", event="started", attempt_type="soft_probe_snapshot",
             index=index, attempt_directory=str(attempt))
    result = {"schema": "reliability_first.soft_objective_probe_result.v1", "index": index,
              "scenario_id": entry["scenario_id"], "group_id": entry["group_id"],
              "snapshot_minute": entry["snapshot_minute"], "role": "train",
              "source_binding": binding, "source_result_sha256": None,
              "status": "unknown", "candidates": [], "outcomes": [],
              "opportunities": [], "service_comparisons": [], "raw_trajectory_hashes": {},
              "errors": [], "reused_secure_trajectories_verified": 0}
    try:
        source = verify_saved_source(run_dir, index, entry, manifest, design["branch_source_sha256"])
        result["source_result_sha256"] = source["source_result_sha256"]
        result["saved_execution_snapshot_content_sha256"] = source["snapshot_content_sha256"]
        result["reused_secure_trajectories_verified"] = 4
        for future_id, old in source["baseline_outcomes"].items():
            path = source["root"] / old["trajectory_file"]
            relative = path.relative_to(run_dir).as_posix()
            result["raw_trajectory_hashes"][relative] = old["trajectory_sha256"]
            result["outcomes"].append({"future_id": future_id, "candidate_id": "secure_baseline",
                "candidate_index": 0, "status": old["status"], "target_names": list(TARGET_NAMES),
                "targets": old["targets"], "target_mask": old["window"]["target_mask"],
                "effective_execution_class": old["effective_execution_class"],
                "trajectory_file": relative, "trajectory_sha256": old["trajectory_sha256"],
                "reused_secure_raw": True})
        snapshot = load_saved_execution_snapshot(source, manifest)
        current_env = snapshot.fork()
        current_env.core.boundary()
        before = capture_execution(current_env, binding=snapshot.binding).content_sha256
        problem = extract_current_problem(current_env)
        secure_request = baseline_request("secure_milp")
        _journal(log, event="started", attempt_type="isolated_current_projection", index=index,
                 candidate_id="secure_baseline")
        try:
            projected_secure = project_current(problem, secure_request)
            _journal(log, event="finished", attempt_type="isolated_current_projection", index=index,
                     candidate_id="secure_baseline", status="success")
        except Exception as error:
            _journal(log, event="finished", attempt_type="isolated_current_projection", index=index,
                     candidate_id="secure_baseline", status="error", error_type=type(error).__name__, message=str(error))
            raise
        saved_secure = source["current"]["candidates"][0]
        if json_clean(projected_secure["command"]) != saved_secure["current_command"]:
            raise ValueError("Fresh secure current full dispatch differs from saved baseline")
        history = source["current"]["history"]
        context = reconstruct_current_context(problem, history, projected_secure["command"])
        if json_clean(context) != source["current"]["context"]:
            raise ValueError("Fresh legal current context differs from saved source")
        candidates = make_candidate_requests(context, saved_secure)
        result["candidates"].append({**candidates[0], "projection_status": "reused_and_reverified",
            "current_command": saved_secure["current_command"],
            "projected_targets_kW": saved_secure["projected_targets_kW"]})
        for candidate in candidates[1:]:
            row = copy.deepcopy(candidate)
            _journal(log, event="started", attempt_type="isolated_current_projection", index=index,
                     candidate_id=row["candidate_id"])
            try:
                projected = project_current(problem, row["request"])
                row.update(projection_status="success", current_command=projected["command"],
                           projected_targets_kW=projected["projected_targets_kW"],
                           current_planning_query={"solver_calls": projected["solver_calls"],
                                                   "events": projected["events"],
                                                   "elapsed_seconds": projected["elapsed_seconds"]})
                _journal(log, event="finished", attempt_type="isolated_current_projection", index=index,
                         candidate_id=row["candidate_id"], status="success")
            except Exception as error:
                row.update(projection_status="error", current_command=None,
                           projected_targets_kW=None,
                           current_planning_query=getattr(error, "current_projection_diagnostic", None),
                           error={"type": type(error).__name__, "message": str(error)})
                _journal(log, event="finished", attempt_type="isolated_current_projection", index=index,
                         candidate_id=row["candidate_id"], status="error",
                         error_type=type(error).__name__, message=str(error))
            result["candidates"].append(row)
        after = capture_execution(current_env, binding=snapshot.binding).content_sha256
        if before != after:
            raise AssertionError("Pure current planning changed original physical state")
        baseline_initial = source["baseline_payloads"]["original"]["initial_state"]
        for future_index, (future_id, future_scenario) in enumerate(zip(FUTURE_IDS, source["futures"]["scenarios"])):
            for candidate_index, candidate in enumerate(result["candidates"][1:], 1):
                cid = candidate["candidate_id"]
                if candidate["projection_status"] != "success":
                    result["outcomes"].append(_unknown_outcome(cid, candidate_index, future_id, "projection_error",
                        error=candidate.get("error")))
                    continue
                raw_path = attempt / f"future_{future_index:02d}_candidate_{candidate_index:02d}.json.gz"
                _journal(log, event="started", attempt_type="current_action_future_continuation",
                         index=index, future_id=future_id, candidate_id=cid,
                         trajectory_file=raw_path.name)
                try:
                    branch_env = snapshot.fork()
                    branch_env.scenario = copy.deepcopy(future_scenario)
                    policy = FirstSecureCandidatePolicy(candidate["request"],
                                                        f"v4_soft_probe_{future_id}_{candidate_index:02d}")
                    payload = run_policy_episode(branch_env, future_scenario, policy,
                        int(manifest["options"]["seed"]), manifest, reset=False,
                        prefix_decisions=source["prefix_decisions"],
                        horizons=(15, 30, 60, 120),
                        observation_end_minute=float(entry["snapshot_minute"]) + 120.)
                    save_raw(raw_path, payload)
                    raw_hash = sha(raw_path)
                    relative = raw_path.relative_to(run_dir).as_posix()
                    result["raw_trajectory_hashes"][relative] = raw_hash
                    if (payload["initial_state"] != baseline_initial
                            or payload["scenario"] != future_scenario
                            or payload["summary"]["scenario_id"] != entry["scenario_id"]
                            or payload["summary"]["group_id"] != entry["group_id"]
                            or payload["summary"]["manifest_signature"] != manifest["signature"]
                            or payload["decisions"][0]["requested_command"] != candidate["request"]):
                        raise ValueError("New current-action continuation identity mismatch")
                    window = window_targets(payload, entry["snapshot_minute"], 120., branch_env)
                    result["outcomes"].append({"future_id": future_id, "candidate_id": cid,
                        "candidate_index": candidate_index, "status": payload["summary"]["status"],
                        "target_names": list(TARGET_NAMES), "targets": window["targets"],
                        "target_mask": window["target_mask"],
                        "effective_execution_class": _execution_class(payload, entry["snapshot_minute"]),
                        "trajectory_file": relative, "trajectory_sha256": raw_hash,
                        "reused_secure_raw": False})
                    _journal(log, event="finished", attempt_type="current_action_future_continuation",
                             index=index, future_id=future_id, candidate_id=cid,
                             status=payload["summary"]["status"], raw_sha256=raw_hash)
                except Exception as error:
                    error_record = {"type": type(error).__name__, "message": str(error),
                                    "traceback": traceback.format_exc()}
                    result["errors"].append({"future_id": future_id, "candidate_id": cid, **error_record})
                    result["outcomes"].append(_unknown_outcome(cid, candidate_index, future_id,
                        "continuation_error", error=error_record))
                    _journal(log, event="finished", attempt_type="current_action_future_continuation",
                             index=index, future_id=future_id, candidate_id=cid, status="error",
                             error_type=type(error).__name__, message=str(error),
                             raw_sha256=sha(raw_path) if raw_path.is_file() else None)
        result["opportunities"] = opportunity_rows(result["outcomes"], baseline_candidate_id="secure_baseline")
        by_outcome = {(o["candidate_id"], o["future_id"]): o for o in result["outcomes"]}
        for candidate in result["candidates"][1:]:
            for future_id in FUTURE_IDS:
                row = by_outcome[(candidate["candidate_id"], future_id)]
                reference = by_outcome[("secure_baseline", future_id)]
                pair = classify_paired_targets(row["targets"], reference["targets"],
                    actual_mask=row["target_mask"], reference_mask=reference["target_mask"])
                result["service_comparisons"].append({"candidate_id": candidate["candidate_id"],
                    "future_id": future_id, "status": row["status"],
                    "effective_execution_class": row["effective_execution_class"], **pair})
        result["status"] = "complete" if all(
            row["status"] in ("completed", "bounded_observation_completed")
            and all(row["target_mask"]) for row in result["outcomes"]) else "unknown"
    except Exception as error:
        result["errors"].append({"type": type(error).__name__, "message": str(error),
                                 "traceback": traceback.format_exc()})
        result["status"] = "unknown"
    finally:
        result["attempt_counts"] = _count_journal(log)
        _save_new(attempt / "PROBE_RESULT.json", result)
        _journal(output_root / "attempts.jsonl", event="finished", attempt_type="soft_probe_snapshot",
                 index=index, status=result["status"],
                 result_sha256=sha(attempt / "PROBE_RESULT.json"))
    return result


def _count_journal(path: Path) -> dict:
    counts = Counter()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            counts[f"{row.get('attempt_type')}_{row.get('event')}"] += 1
    return dict(counts)


def _adjudicate_interrupted(run_dir: Path, output_root: Path, index: int,
                            entry: dict, binding: dict) -> dict:
    """Make a crashed write-once attempt terminal unknown without erasing bytes."""
    attempt = output_root / f"snapshot_{index:03d}" / "attempt_0001"
    if not attempt.is_dir() or (attempt / "PROBE_RESULT.json").exists():
        raise FileExistsError("Only an existing interrupted, unadjudicated attempt qualifies")
    raw = {path.relative_to(run_dir).as_posix(): sha(path)
           for path in sorted(attempt.glob("*.json.gz"))}
    result = {"schema": "reliability_first.soft_objective_probe_result.v1",
              "index": index, "scenario_id": entry["scenario_id"],
              "group_id": entry["group_id"], "snapshot_minute": entry["snapshot_minute"],
              "role": "train", "source_binding": binding,
              "source_result_sha256": None, "status": "unknown",
              "candidates": [], "outcomes": [], "opportunities": [],
              "service_comparisons": [], "raw_trajectory_hashes": raw,
              "errors": [{"type": "InterruptedAttempt",
                          "message": "Previous invocation left an attempt directory without a terminal result; preserved every original byte and counted started work as unknown"}],
              "reused_secure_trajectories_verified": 0,
              "attempt_counts": _count_journal(attempt / "attempts.jsonl")}
    result_path = attempt / "PROBE_RESULT.json"
    _save_new(result_path, result)
    _journal(output_root / "attempts.jsonl", event="finished", attempt_type="soft_probe_snapshot",
             index=index, status="unknown", reason="interrupted_attempt_adjudicated",
             result_sha256=sha(result_path))
    return result


def summarize_probe(run_dir: Path, design: dict, binding: dict) -> dict:
    """Count every registered case, including terminal unknowns and crashes."""
    output_root = Path(run_dir) / design["output_directory"]
    results = []
    raw_hashes = {}
    attempts = Counter()
    per_candidate = defaultdict(Counter)
    per_future = defaultdict(Counter)
    service_by_candidate = defaultdict(lambda: defaultdict(Counter))
    service_by_future = defaultdict(lambda: defaultdict(Counter))
    strict_snapshot_ids = set()
    strict_candidate_count = 0
    complete = unknown = interrupted = not_attempted = 0
    finished_receipts = {}
    global_journal = output_root / "attempts.jsonl"
    if global_journal.is_file():
        for line in global_journal.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") == "finished" and record.get("attempt_type") == "soft_probe_snapshot":
                index = record.get("index")
                if index in finished_receipts:
                    raise ValueError("Duplicate terminal probe attempt receipt")
                finished_receipts[index] = record
    for index in design["selected_snapshot_indices"]:
        attempt = output_root / f"snapshot_{index:03d}" / "attempt_0001"
        result_path = attempt / "PROBE_RESULT.json"
        if not attempt.is_dir():
            not_attempted += 1
            results.append({"index": index, "status": "not_attempted", "result_path": None,
                            "result_sha256": None})
            continue
        attempts.update(_count_journal(attempt / "attempts.jsonl"))
        if not result_path.is_file() or index not in finished_receipts:
            interrupted += 1
            results.append({"index": index, "status": "interrupted_no_terminal_receipt",
                            "result_path": result_path.relative_to(run_dir).as_posix() if result_path.is_file() else None,
                            "result_sha256": sha(result_path) if result_path.is_file() else None})
            continue
        result_sha = sha(result_path)
        if result_sha != finished_receipts[index].get("result_sha256"):
            raise ValueError("Probe terminal result SHA changed after registered receipt")
        result = _load(result_path)
        if result.get("index") != index or result.get("source_binding") != binding or result.get("schema") != "reliability_first.soft_objective_probe_result.v1":
            raise ValueError("Probe result source/identity differs from frozen run")
        if result["status"] == "complete":
            complete += 1
        else:
            unknown += 1
        results.append({"index": index, "status": result["status"],
                        "result_path": result_path.relative_to(run_dir).as_posix(),
                        "result_sha256": result_sha,
                        "source_result_sha256": result.get("source_result_sha256"),
                        "reused_secure_trajectories_verified": result.get("reused_secure_trajectories_verified", 0),
                        "new_raw_trajectory_count": sum(not o.get("reused_secure_raw") and o.get("trajectory_file") is not None
                                                        for o in result.get("outcomes", []))})
        attempts["reused_secure_trajectories_verified"] += result.get("reused_secure_trajectories_verified", 0)
        for path, digest_value in result.get("raw_trajectory_hashes", {}).items():
            if Path(path).is_absolute() or ".." in Path(path).parts or not (run_dir / path).is_file():
                raise ValueError("Probe raw path escapes run or is missing")
            if sha(run_dir / path) != digest_value:
                raise ValueError("Probe raw trajectory SHA mismatch")
            if path in raw_hashes and raw_hashes[path] != digest_value:
                raise ValueError("Probe raw map has conflicting SHA")
            raw_hashes[path] = digest_value
        for opportunity in result.get("opportunities", []):
            cid = opportunity["candidate_id"]
            if cid == "secure_baseline":
                continue
            if opportunity["strict_opportunity_across_stress_set"] is True:
                strict_candidate_count += 1
                strict_snapshot_ids.add(index)
            for future in opportunity["per_future"]:
                label = future["comparison"]
                per_candidate[cid][label] += 1
                per_future[future["future_id"]][label] += 1
        for pair in result.get("service_comparisons", []):
            for priority, label in pair["by_priority"].items():
                service_by_candidate[pair["candidate_id"]][priority][label] += 1
                service_by_future[pair["future_id"]][priority][label] += 1
    started = attempts["current_action_future_continuation_started"]
    finished = attempts["current_action_future_continuation_finished"]
    projected_started = attempts["isolated_current_projection_started"]
    projected_finished = attempts["isolated_current_projection_finished"]
    if (started > design["budget"]["max_new_continuation_attempts"]
            or projected_started > design["budget"]["max_pure_current_projection_queries"]):
        raise ValueError("Probe attempt budget exceeded")
    return {"schema": "reliability_first.soft_objective_probe_summary.v1",
            "status": "complete" if complete + unknown == design["registered_snapshots"] and interrupted == not_attempted == 0 else "partial",
            "registered_snapshots": design["registered_snapshots"],
            "completed_snapshots": complete, "unknown_snapshots": unknown,
            "not_attempted_snapshots": not_attempted, "interrupted_snapshots": interrupted,
            "attempts": {"reused_secure_trajectories_verified": attempts["reused_secure_trajectories_verified"],
                         "new_continuations_started": started,
                         "new_continuations_finished": finished,
                         "projection_queries_started": projected_started,
                         "projection_queries_finished": projected_finished},
            "budget": {"new_branch_attempts": started,
                       "max_new_branch_attempts": design["budget"]["max_new_continuation_attempts"],
                       "projection_query_ceiling": design["budget"]["max_pure_current_projection_queries"],
                       "global_new_branch_attempt_ceiling": design["budget"]["global_new_branch_attempt_ceiling"]},
            "source_binding": binding,
            "raw_trajectory_hashes": dict(sorted(raw_hashes.items())),
            "per_candidate": {cid: {label: int(counts[label]) for label in ("better", "tie", "worse", "unknown")}
                              for cid, counts in sorted(per_candidate.items())},
            "per_future": {fid: {label: int(per_future[fid][label]) for label in ("better", "tie", "worse", "unknown")}
                           for fid in FUTURE_IDS},
            "service_by_candidate": {cid: {priority: dict(counts) for priority, counts in by_priority.items()}
                                     for cid, by_priority in sorted(service_by_candidate.items())},
            "service_by_future": {fid: {priority: dict(counts) for priority, counts in by_priority.items()}
                                  for fid, by_priority in sorted(service_by_future.items())},
            "strict_opportunity_candidate_count": strict_candidate_count,
            "strict_opportunity_snapshot_count": len(strict_snapshot_ids),
            "results": results,
            "opportunity_semantics": "Ex post finite P0 candidate advantage across four named futures; unknowns retained, no online future access"}


def _save_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(json_clean(summary), stream, ensure_ascii=False, indent=2, allow_nan=False)
    temporary.replace(path)


def run_probe(run_dir: Path, *, limit: int | None = None) -> dict:
    run_dir = Path(run_dir).resolve()
    design, manifest, plan, binding = _validate_design(run_dir)
    output_root = run_dir / design["output_directory"]
    output_root.mkdir(parents=True, exist_ok=True)
    summary_path = output_root / "PROBE_SUMMARY.json"
    if summary_path.is_file() and _load(summary_path).get("source_binding") != binding:
        raise ValueError("Cannot resume probe with changed source binding")
    if limit is not None and limit < 0:
        raise ValueError("--limit must be nonnegative")
    finished = set()
    global_journal = output_root / "attempts.jsonl"
    if global_journal.is_file():
        for line in global_journal.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") == "finished" and row.get("attempt_type") == "soft_probe_snapshot":
                finished.add(row.get("index"))
    for index in design["selected_snapshot_indices"]:
        attempt = output_root / f"snapshot_{index:03d}" / "attempt_0001"
        if not attempt.is_dir():
            continue
        result_path = attempt / "PROBE_RESULT.json"
        if not result_path.is_file():
            if index in finished:
                raise ValueError("Finished probe receipt exists without terminal result")
            _adjudicate_interrupted(run_dir, output_root, index, plan[index], binding)
        elif index not in finished:
            result = _load(result_path)
            if result.get("source_binding") != binding or result.get("index") != index:
                raise ValueError("Unreceipted probe result has different source binding")
            _journal(global_journal, event="finished", attempt_type="soft_probe_snapshot",
                     index=index, status=result.get("status"),
                     reason="terminal_result_receipt_recovered_after_interruption",
                     result_sha256=sha(result_path))
    pending = [index for index in design["selected_snapshot_indices"]
               if not (output_root / f"snapshot_{index:03d}" / "attempt_0001").exists()]
    for index in pending[:limit]:
        _run_snapshot(run_dir, output_root, index, plan[index], manifest, design, binding)
        _save_summary(summary_path, summarize_probe(run_dir, design, binding))
    summary = summarize_probe(run_dir, design, binding)
    _save_summary(summary_path, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="run or resume the registered train-only probe")
    runner.add_argument("run_dir", type=Path)
    runner.add_argument("--limit", type=int, default=None,
                        help="maximum new snapshots this invocation; omitted runs all pending")
    args = parser.parse_args(argv)
    summary = run_probe(args.run_dir, limit=args.limit)
    print(json.dumps({key: summary[key] for key in (
        "status", "registered_snapshots", "completed_snapshots", "unknown_snapshots",
        "not_attempted_snapshots", "interrupted_snapshots", "attempts",
        "strict_opportunity_candidate_count", "strict_opportunity_snapshot_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
