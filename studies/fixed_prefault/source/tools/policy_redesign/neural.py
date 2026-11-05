"""v6 CUDA residual actor distilled from complete, outcome-ranked train episodes.

The episode-level ranking is offline. Online inference only receives legal
history and the unchanged secure plan on the current problem. A requested
port target is never treated as an executed dispatch or a safety probability.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
import sys
import time
import traceback

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[2]
ENCODER_SOURCE = ROOT / "tools/policy_search/neural.py"
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.data_contract import validate_action
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.dataset_manifest import digest
from hospital_ies.model import RT_TO_KW, load_plant
from tools.control_improvement.branches import window_targets
from tools.control_improvement.candidates import HISTORY_KEYS, validate_context
from tools.control_improvement.runtime import project_current
from tools.integrated_learning.policies import ACTION_NAMES, baseline_request, legal_history
from tools.policy_search.neural import encode_observation
from tools.policy_search.score_recheck import score_differences
from tools.policy_redesign.policy import (
    current_port_limits, decode_residual, encode_residual,
)

SCHEMA = "policy_redesign.v6.outcome_ranked_cuda_actor.v1"
FRAME_DIM = len(HISTORY_KEYS) + 1
CONTEXT_DIM = 58
SCORE_DIM = 19


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _new_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _signed(value, shape, name):
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f"Invalid {name}")
    return array


def _complete_targets(row):
    if not (row.get("terminal") and row.get("status") == "completed"):
        return None
    values = []
    for field in ("primary_score", "full_observation"):
        score = row.get(field) or {}
        target = score.get("targets")
        mask = score.get("target_mask")
        if not (score.get("full_horizon") is True and isinstance(mask, list)
                and len(mask) == SCORE_DIM and all(x is True for x in mask)):
            return None
        try:
            values.append(_signed(target, (SCORE_DIM,), field))
        except (ValueError, TypeError):
            return None
    return tuple(values)


def _one_window_key(candidate, secure):
    """Match preregistered P0-first search order on paired case differences."""
    c, b = np.stack(candidate), np.stack(secure)
    delta = c - b
    new = int(np.sum((b[:, 0] == 0) & (c[:, 0] == 1)))
    resolved = int(np.sum((b[:, 0] == 1) & (c[:, 0] == 0)))

    def worst(index):
        return max(0.0, float(np.max(delta[:, index])))

    def total(index):
        return float(np.sum(delta[:, index]))

    p0 = (new - resolved, new, -resolved,
          worst(1), total(1),
          worst(2), worst(3), worst(4),
          total(2), total(3), total(4),
          worst(14), total(14))
    lower = tuple(total(i) for i in (*range(5, 14), 15, 16, 17, 18))
    return p0, lower


def _root_outcome_key(candidate, secure):
    main_p0, main_lower = _one_window_key(
        [pair[0] for pair in candidate], [pair[0] for pair in secure])
    full_p0, full_lower = _one_window_key(
        [pair[1] for pair in candidate], [pair[1] for pair in secure])
    return main_p0 + full_p0 + main_lower + full_lower


def rank_complete_root(rows, scenario_ids, policy_ids, *, secure_id="secure_milp"):
    """Select one unified policy across the registered shared train-root panel.

    Any missing, partial, or unknown episode excludes that policy for the root;
    its observed prefix remains in the returned audit and is never scored zero.
    """
    scenes = tuple(scenario_ids)
    policies = tuple(policy_ids)
    if (not scenes or len(set(scenes)) != len(scenes) or not policies
            or len(set(policies)) != len(policies) or secure_id not in policies):
        raise ValueError("Distinct shared scenes and policies including secure required")
    by_key = {}
    for row in rows:
        key = (row.get("scenario_id"), row.get("policy_id"))
        if key in by_key:
            raise ValueError("Duplicate outcome for a scenario and policy")
        by_key[key] = row
    complete = {}
    unknown = []
    for policy in policies:
        values = [_complete_targets(by_key.get((scene, policy), {})) for scene in scenes]
        if any(value is None for value in values):
            unknown.append(policy)
        else:
            complete[policy] = values
    if secure_id not in complete:
        eligible = {}
        winner = None
    else:
        eligible = {policy: _root_outcome_key(values, complete[secure_id])
                    for policy, values in complete.items()}
        winner = min(eligible, key=lambda policy: (
            eligible[policy], 0 if policy == secure_id else 1, policy))
    return {
        "winner_policy_id": winner,
        "eligible_policy_ids": sorted(complete),
        "ineligible_unknown_policy_ids": sorted(unknown),
        "outcome_keys": {name: list(key) for name, key in eligible.items()},
        "shared_scenario_ids": list(scenes),
        "ranking_semantics": "Registered shared panel complete; preregistered relative secure worst individual P0 increases before total deltas, then lower priorities and cost; primary and full P0 before any lower priority; exact ties favor secure",
    }


def stratified_root_split(scenes, *, seed, validation_per_condition=1,
                          validation_exclude_root_ids=()):
    """Whole roots and all their pressure scenes remain on one side of fit."""
    by_condition = defaultdict(set)
    role_by_root = {}
    for scene in scenes:
        root, condition = scene["root_family_id"], scene["condition_id"]
        if root in role_by_root and role_by_root[root] != condition:
            raise ValueError("One root may not span conditions")
        role_by_root[root] = condition
        by_condition[condition].add(root)
    if not by_condition or type(seed) is not int or type(validation_per_condition) is not int:
        raise ValueError("Valid split seed and conditions required")
    excluded = set(validation_exclude_root_ids)
    if any(not isinstance(root, str) or not root for root in excluded):
        raise ValueError("Validation exclusion must contain nonempty root IDs")
    rng = np.random.default_rng(seed)
    fit, validation = [], []
    for condition, roots in sorted(by_condition.items()):
        group = sorted(roots)
        available = sorted(roots - excluded)
        if (not 0 < validation_per_condition < len(group)
                or len(available) < validation_per_condition):
            raise ValueError(f"Insufficient roots in condition {condition} for grouped validation")
        permuted = [str(root) for root in rng.permutation(available)]
        validation.extend(permuted[:validation_per_condition])
        fit.extend(root for root in group
                   if root not in permuted[:validation_per_condition])
    return {"fit_roots": sorted(fit), "validation_roots": sorted(validation),
            "validation_conditions": sorted(by_condition),
            "validation_exclude_root_ids": sorted(excluded),
            "validation_exclusion_overlap": sorted(set(validation) & excluded),
            "split_semantics": "All registered observed pressure scenes for each root remain on one side; pressure coverage is audited separately; one root per condition validation"}


def _search_validation_exclusions(search_plan_path, manifest_path, manifest):
    """Freeze search-exposed train roots out of neural validation by signed IDs."""
    path = Path(search_plan_path).resolve()
    plan = _read(path)
    body = copy.deepcopy(plan)
    signature = body.pop("signature", None)
    calculated = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")).hexdigest()
    if (not isinstance(signature, str) or signature != calculated
            or plan.get("phase") != "train"
            or plan.get("final_blind_opened") is not False
            or Path(plan["manifest_path"]).resolve() != Path(manifest_path).resolve()
            or plan["manifest_sha256"] != _sha(manifest_path)
            or plan.get("search_source_sha256") !=
            _sha(ROOT / "tools/policy_redesign/search.py")):
        raise ValueError("Signed adaptive search plan is not bound to current train manifest")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    ids = plan.get("scenario_ids")
    if (not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
            or any(sid not in scenes or scenes[sid]["experiment_role"] != "train"
                   or scenes[sid].get("is_final_blind") for sid in ids)):
        raise ValueError("Search-exposed validation exclusion has invalid train scenes")
    roots = sorted({scenes[sid]["root_family_id"] for sid in ids})
    return {"search_plan_path": str(path), "search_plan_sha256": _sha(path),
            "search_plan_signature": signature, "search_scenario_ids": ids,
            "search_exposed_root_ids": roots,
            "selection_outcomes_used_to_choose_validation": False}


def physical_context_scales(plant, *, grid_reference_kW):
    """Fixed configured equipment scales; no zero-variance train fallback."""
    gt = float(plant.gt_rated_kW)
    diesel = float(plant.emergency_diesel_kW)
    absorption = float(plant.absorption_rt) * RT_TO_KW
    grid = float(grid_reference_kW)
    if not all(math.isfinite(x) and x > 0 for x in (gt, diesel, absorption, grid)):
        raise ValueError("Positive configured physical scales required")
    ports = [gt, gt, gt, grid, diesel, absorption]
    firm = 3 * gt + grid + diesel
    scales = [*ports, *ports, gt, gt, gt, 1, 1, 1, 60, 60, 60]
    scales += [firm] * 12
    scales += [absorption, grid, 60, 1, 1, 600, 600]
    scales += ports + ports + [1] * 6
    result = np.asarray(scales, dtype=np.float64)
    if result.shape != (CONTEXT_DIM,) or not np.all(result > 0):
        raise AssertionError("Incorrect current-context physical scaling layout")
    return result


def best_epoch_index(validation_losses, *, patience):
    """Return 1-based best epoch and actual early-stop epoch."""
    if type(patience) is not int or patience < 1 or not validation_losses:
        raise ValueError("Positive validation patience and losses required")
    best = math.inf
    best_epoch = 0
    for epoch, item in enumerate(validation_losses, start=1):
        value = float(item)
        if not math.isfinite(value):
            raise ValueError("Nonfinite validation loss")
        if value < best:
            best, best_epoch = value, epoch
        if epoch - best_epoch >= patience:
            return best_epoch, epoch
    return best_epoch, len(validation_losses)


def physical_frame_scales(plant, *, grid_reference_kW, diesel_initial_energy_kWh):
    """Fixed physical units for the registered legal numeric observation keys."""
    gt = float(plant.gt_rated_kW)
    diesel = float(plant.emergency_diesel_kW)
    grid = float(grid_reference_kW)
    fuel = float(diesel_initial_energy_kWh)
    firm = 3 * gt + diesel + grid
    if not all(math.isfinite(x) and x > 0 for x in (gt, diesel, grid, fuel)):
        raise ValueError("Positive configured frame scales required")
    scales = []
    for key in HISTORY_KEYS:
        if key == "diesel_energy_kWh":
            scale = fuel
        elif key.startswith("zone_delta_") or key.startswith("zone_margin_"):
            scale = 10.0
        elif "temperature_C" in key:
            scale = 40.0
        elif "minute" in key or key == "time_minute":
            scale = 600.0 if key in ("time_minute", "grid_outage_elapsed_minutes") else 60.0
        elif "diesel" in key and "kW" in key:
            scale = diesel
        elif "GT_" in key and "kW" in key:
            scale = gt
        elif "grid" in key and "kW" in key:
            scale = grid
        elif "_kW" in key:
            scale = firm
        else:
            scale = 1.0
        scales.append(scale)
    result = np.asarray(scales, dtype=np.float64)
    if result.shape != (FRAME_DIM - 1,) or not np.all(result > 0):
        raise AssertionError("Incorrect legal-frame physical scaling layout")
    return result


def _signed_record(value, label):
    record = copy.deepcopy(value)
    signature = record.pop("signature", None)
    if not isinstance(signature, str) or signature != digest(record):
        raise ValueError(f"{label} signature mismatch")
    return signature


def collect_outcome_ranked_examples(manifest_path, result_paths, *, policy_ids,
                                    allowed_scenario_ids=None):
    """Verify registered raw and choose root-level winners before making labels.

    No per-future/per-scene oracle policy is selected. An unknown complete
    outcome never receives a zero or a partially observed service score.
    """
    manifest_path = Path(manifest_path).resolve()
    manifest = _read(manifest_path)
    manifest_signature = _signed_record(manifest, "Manifest")
    score_source = "tools/control_improvement/branches.py"
    score_source_sha = _sha(ROOT / score_source)
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    policies = tuple(policy_ids)
    if (not policies or len(policies) != len(set(policies))
            or "secure_milp" not in policies):
        raise ValueError("Exact secure and distinct candidate IDs required")
    paths = [Path(path).resolve() for path in result_paths]
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("Nonempty distinct registered result paths required")
    verified = {}
    sources = []
    source_plans = {}
    registered_panel_ids = set()
    for rp in paths:
        if rp.name != "result.json" or len(rp.parents) < 4:
            raise ValueError("Expected registered result.json attempt path")
        plan_path = rp.parents[3] / "registered_plan.json"
        plan = _read(plan_path)
        plan_signature = _signed_record(plan, "Training batch plan")
        if (plan.get("phase") != "train"
                or Path(plan["manifest_path"]).resolve() != manifest_path
                or plan["manifest_sha256"] != _sha(manifest_path)
                or plan["manifest_signature"] != manifest_signature
                or plan.get("source_sha256", {}).get(score_source) != score_source_sha
                or plan.get("score_windows") != {"primary": [60, 420],
                                                  "full": [0, 600]}):
            raise ValueError("Result plan does not bind the train manifest")
        plan_record = {"plan_path": str(plan_path.resolve()),
                       "plan_sha256": _sha(plan_path),
                       "plan_signature": plan_signature,
                       "phase": "train", "scenario_ids": plan["scenario_ids"]}
        prior_plan = source_plans.get(plan_record["plan_path"])
        if prior_plan is not None and prior_plan != plan_record:
            raise ValueError("Registered plan changed while extracting train results")
        source_plans[plan_record["plan_path"]] = plan_record
        registered_panel_ids.update(plan["scenario_ids"])
        result = _read(rp)
        sid, pid = result.get("scenario_id"), result.get("policy_id")
        key = (sid, pid)
        if key in verified:
            raise ValueError("Duplicate scenario and policy result across attempts")
        if (sid not in scenes or sid not in plan["scenario_ids"] or pid not in policies
                or result.get("plan_signature") != plan_signature
                or result.get("phase") != "train" or not result.get("terminal")
                or not result.get("binding", {}).get("passed")):
            raise ValueError("Result identity or physical binding invalid")
        scene = scenes[sid]
        split = manifest["splits"][sid]
        if (scene.get("experiment_role") != "train"
                or split.get("experiment_role") != "train"
                or scene.get("is_final_blind") or split.get("is_final_blind")):
            raise ValueError("Outcome-ranked fit may consume train roots only")
        registered = [spec for spec in plan["policy_specs"] if spec["policy_id"] == pid]
        if len(registered) != 1 or registered[0].get("kind") != result.get("kind"):
            raise ValueError("Source policy differs from registered full-feedback kind")
        if pid != "secure_milp" and result["kind"] not in ("v6_feedback", "v6_neural"):
            raise ValueError("Only unified v6 feedback candidates may supply labels")
        if pid == "secure_milp" and result["kind"] != "secure":
            raise ValueError("Secure source must retain original baseline kind")
        name = result.get("trajectory_path")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("Missing local raw trajectory name")
        raw = rp.parent / name
        actual_sha = _sha(raw)
        if result.get("trajectory_sha256") != actual_sha:
            raise ValueError("Raw trajectory SHA differs from result")
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            payload = json.load(
                stream, parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError("Nonfinite raw JSON constant: " + value)))
        if (payload.get("scenario") != scene
                or payload.get("summary", {}).get("status") != result.get("status")):
            raise ValueError("Raw scenario/status differs from registered result")
        primary = window_targets(payload, 60.0, 360.0, env)
        full = window_targets(payload, 0.0, 600.0, env)
        score_errors = (score_differences(result.get("primary_score"), primary,
                                          "primary_score") +
                        score_differences(result.get("full_observation"), full,
                                          "full_observation"))
        if score_errors:
            raise ValueError("Stored score differs from raw recomputation for " +
                             f"{sid}/{pid}: " + "; ".join(score_errors[:3]))
        # Keep only the signed location after rechecking this raw. Retaining
        # hundreds of decompressed trajectory dictionaries can exhaust host RAM.
        verified[key] = (result, raw, actual_sha)
        sources.append({"scenario_id": sid, "root_family_id": scene["root_family_id"],
                        "policy_id": pid, "kind": result["kind"], "status": result["status"],
                        "result_path": str(rp), "result_sha256": _sha(rp),
                        "raw_path": str(raw), "raw_sha256": actual_sha,
                        "recomputed_primary_score_sha256": digest(primary),
                        "recomputed_full_observation_sha256": digest(full),
                        "score_recheck_difference_count": 0,
                        "primary_complete": _complete_targets(result) is not None})
        del payload
    panel_ids = (set(registered_panel_ids) if allowed_scenario_ids is None
                 else set(allowed_scenario_ids))
    if (not panel_ids or not panel_ids <= registered_panel_ids
            or any(sid not in scenes or scenes[sid].get("experiment_role") != "train"
                   for sid in panel_ids)):
        raise ValueError("Explicit scenario panel must be nonempty registered train support")
    if any(sid not in panel_ids for sid, _ in verified):
        raise ValueError("Supplied result lies outside explicit train scenario panel")
    by_root = defaultdict(list)
    for sid in panel_ids:
        by_root[scenes[sid]["root_family_id"]].append(sid)
    rows = []
    root_audit = []
    rejected_labels = []
    for root, all_sid in sorted(by_root.items()):
        scenario_ids = sorted(set(all_sid))
        root_results = [verified[(sid, pid)][0] for sid in scenario_ids
                        for pid in policies if (sid, pid) in verified]
        ranking = rank_complete_root(root_results, scenario_ids, policies)
        winner = ranking["winner_policy_id"]
        source_policies = ([] if winner is None else [winner] if winner != "secure_milp" else
                           [pid for pid in ranking["eligible_policy_ids"] if pid != "secure_milp"])
        before = len(rows)
        for sid in scenario_ids:
            for source_policy in source_policies:
                source = verified.get((sid, source_policy))
                if source is None:
                    continue
                result, raw, raw_sha = source
                if _sha(raw) != raw_sha:
                    raise ValueError("Selected raw trajectory changed after score recheck")
                with gzip.open(raw, "rt", encoding="utf-8") as stream:
                    payload = json.load(
                        stream, parse_constant=lambda value: (_ for _ in ()).throw(
                            ValueError("Nonfinite raw JSON constant: " + value)))
                decisions = payload.get("decisions")
                if not isinstance(decisions, list):
                    raise ValueError("Completed v6 source lacks decision sequence")
                for index, decision in enumerate(decisions):
                    diag = decision.get("policy_diagnostic") or {}
                    context = diag.get("current_context")
                    queries = diag.get("current_planning_queries")
                    if (diag.get("strategy_fallback") or diag.get("future_rollouts_online") != 0
                            or not isinstance(context, dict) or not isinstance(queries, list)
                            or not queries or queries[0].get("candidate_id") != "secure_baseline"
                            or queries[0].get("status") != "success"):
                        rejected_labels.append({"scenario_id": sid, "policy_id": source_policy,
                                                "decision_time_min": decision.get("decision_time_min"),
                                                "reason": "missing_same_state_secure_context_or_strategy_fallback"})
                        continue
                    history = legal_history(decisions[:index + 1])
                    validate_context(history, context)
                    features = history[-1]["features"]
                    secure = _signed(queries[0]["projected_targets_kW"], (6,), "secure targets")
                    if not np.allclose(secure, context["current_plan_targets_kW"], atol=1e-6, rtol=0):
                        raise ValueError("Current plan is not the same-state secure projection")
                    request = decision.get("requested_command") or {}
                    if "targets" in request:
                        if set(request["targets"]) != set(ACTION_NAMES):
                            raise ValueError("Source request differs from six original ports")
                        actual_request = _signed([request["targets"][key] for key in ACTION_NAMES],
                                                 (6,), "six-port request")
                    else:
                        actual_request = secure.copy()
                    if not np.allclose(diag.get("requested_six_targets_kW"), actual_request,
                                       atol=1e-6, rtol=0):
                        raise ValueError("Logged request differs from actual submitted request")
                    if (type(diag.get("diesel_available_current")) is not bool
                            or not isinstance(diag.get("current_step_hours"), (int, float))
                            or not isinstance(diag.get("diesel_rated_kW"), (int, float))):
                        raise ValueError("Missing original current diesel permission/scale audit")
                    kwargs = {"diesel_rated_kW": float(diag["diesel_rated_kW"]),
                              "diesel_available": diag["diesel_available_current"],
                              "step_hours": float(diag["current_step_hours"])}
                    upper = current_port_limits(context, features, **kwargs)
                    if not np.allclose(diag.get("request_capacity_scale_kW"), upper, atol=1e-8, rtol=0):
                        raise ValueError("Logged legal port scale differs from shared decoder")
                    chosen_request = actual_request if winner == source_policy else secure
                    try:
                        target = encode_residual(chosen_request, secure, context, features, **kwargs)
                    except ValueError as error:
                        rejected_labels.append({"scenario_id": sid, "policy_id": source_policy,
                                                "decision_time_min": decision.get("decision_time_min"),
                                                "reason": "unrepresentable_six_port_request",
                                                "error": str(error)})
                        continue
                    frame, current = encode_observation(history, context)
                    rows.append({"scenario_id": sid, "root_family_id": root,
                                 "condition_id": scenes[sid]["condition_id"],
                                 "decision_time_min": float(decision["decision_time_min"]),
                                 "winner_policy_id": winner, "source_state_policy_id": source_policy,
                                 "source_trajectory_sha256": raw_sha,
                                 "frame": frame, "context": current,
                                 "target_residual": np.asarray(target, dtype=np.float64),
                                 "legal_port_mask": upper > 0.0,
                                 "secure_targets_kW": secure,
                                 "source_request_targets_kW": actual_request,
                                 "legal_port_upper_kW": upper})
                del payload, decisions
        root_audit.append({"root_family_id": root,
                           "condition_id": scenes[scenario_ids[0]]["condition_id"],
                           "registered_manifest_scenes": sum(
                               scene["root_family_id"] == root for scene in scenes.values()),
                           "registered_panel_scenes": len(scenario_ids),
                           "observed_result_scenes": len({sid for sid in scenario_ids
                                                          if any((sid, pid) in verified for pid in policies)}),
                           "partial_pressure_support": len(scenario_ids) < sum(
                               scene["root_family_id"] == root for scene in scenes.values()),
                           "ranking": ranking, "labeled_decisions": len(rows) - before,
                           "source_state_policy_ids": source_policies})
    counts = defaultdict(int)
    for row in rows:
        counts[row["root_family_id"]] += 1
    for row in rows:
        row["root_weight"] = 1.0 / counts[row["root_family_id"]]
    audit = {"schema": SCHEMA + ".source_audit", "manifest_path": str(manifest_path),
             "manifest_sha256": _sha(manifest_path), "manifest_signature": manifest_signature,
             "role": "train", "policy_ids": list(policies), "source_trajectories": sources,
             "observation_encoder_source_path": str(ENCODER_SOURCE.resolve()),
             "observation_encoder_source_sha256": _sha(ENCODER_SOURCE),
             "source_registered_plans": sorted(source_plans.values(),
                                               key=lambda row: row["plan_path"]),
             "scoring_source_sha256": {score_source: score_source_sha},
             "score_recheck_method": "Original window_targets on signed raw gzip, exact 19-target and mask equality; same scoring implementation, not independent physical validation",
             "root_rankings": root_audit, "rejected_labels": rejected_labels,
             "registered_train_panel_scenario_ids": sorted(panel_ids),
             "panel_source": ("registered_plan_union" if allowed_scenario_ids is None
                              else "explicit_allowed_scenario_ids"),
             "usable_decisions": len(rows),
             "usable_root_count": len(counts),
             "grid0_diesel_nonzero_decisions": int(sum(
                 row["condition_id"].startswith("GRID0") and
                 row["context"][4] > 0 for row in rows)),
             "label_semantics": "One complete-outcome-ranked unified policy per registered shared train panel within each root; may cover fewer than five manifest pressure scenes; legal current request minus same-state secure projection; secure wins label zero on observed candidate states; not local counterfactual safety",
             "unknown_outcome_policy_roots_preserved": True,
             "final_12_backgrounds_opened": False}
    return rows, audit


def require_full_registered_train_panel(audit, manifest):
    """Reject any formal fit that omits a train root or one of five pressures."""
    train = [scene for scene in manifest["scenarios"]
             if scene.get("experiment_role") == "train"]
    expected_ids = {scene["id"] for scene in train}
    panel_ids = audit.get("registered_train_panel_scenario_ids", [])
    if (not expected_ids or len(expected_ids) != len(train)
            or len(panel_ids) != len(expected_ids)
            or set(panel_ids) != expected_ids):
        raise ValueError("Formal fit requires the full five-pressure train panel")
    roots = defaultdict(list)
    for scene in train:
        split = manifest["splits"].get(scene["id"], {})
        if (split.get("experiment_role") != "train" or
                scene.get("is_final_blind") or split.get("is_final_blind")):
            raise ValueError("Formal fit requires unblinded registered train roles")
        roots[scene["root_family_id"]].append(scene)
    expected_pressures = set(range(5))
    if any(len(scenes) != 5 or
           {scene.get("pressure_point_index") for scene in scenes} !=
           expected_pressures for scenes in roots.values()):
        raise ValueError("Formal fit requires all five-pressure train roots")
    ranked = {row["root_family_id"]: row for row in audit["root_rankings"]}
    if (set(ranked) != set(roots) or len(ranked) != len(audit["root_rankings"])
            or any(row.get("registered_panel_scenes") != 5 or
                   row.get("partial_pressure_support") is not False
                   for row in ranked.values())):
        raise ValueError("Formal fit requires each full five-pressure train root")
    return {"train_root_count": len(roots), "train_scenario_count": len(train),
            "pressure_point_indices_per_root": sorted(expected_pressures)}


class OutcomeRankedActor(nn.Module):
    """Small four-frame actor, without a teacher-intervention gate."""

    def __init__(self, hidden=64):
        super().__init__()
        self.hidden = int(hidden)
        self.net = nn.Sequential(
            nn.Linear(4 * FRAME_DIM + CONTEXT_DIM, self.hidden), nn.GELU(),
            nn.Linear(self.hidden, self.hidden), nn.GELU(),
            nn.Linear(self.hidden, 6), nn.Tanh(),
        )

    def forward(self, frames, context):
        return self.net(torch.cat((frames.flatten(start_dim=1), context), dim=1))


def _weight_sha(model):
    h = hashlib.sha256()
    for name, tensor in model.state_dict().items():
        h.update(name.encode("utf-8"))
        h.update(tensor.detach().cpu().numpy().tobytes())
    return h.hexdigest()


def _arrays(rows, frame_scales, context_scales):
    frames = np.asarray([row["frame"] for row in rows], dtype=np.float64)
    context = np.asarray([row["context"] for row in rows], dtype=np.float64)
    target = np.asarray([row["target_residual"] for row in rows], dtype=np.float64)
    legal_mask = np.asarray([row["legal_port_mask"] for row in rows], dtype=np.float64)
    root_weight = np.asarray([row["root_weight"] for row in rows], dtype=np.float64)
    if (frames.shape != (len(rows), 4, FRAME_DIM)
            or context.shape != (len(rows), CONTEXT_DIM)
            or target.shape != (len(rows), 6)
            or legal_mask.shape != (len(rows), 6)
            or not np.all(np.isfinite(frames)) or not np.all(np.isfinite(context))
            or not np.all(np.isfinite(target)) or np.any(np.abs(target) > 1.000001)):
        raise ValueError("Malformed finite legal outcome-ranked training arrays")
    frames[:, :, 1:] = (frames[:, :, 1:] / frame_scales) * frames[:, :, :1]
    context /= context_scales
    return tuple(x.astype(np.float32) for x in (frames, context, target, legal_mask, root_weight))


def _numpy_loss(pred, target, mask, weight):
    weighted = mask * weight[:, None]
    denom = weighted.sum(axis=0)
    per_port = np.divide((weighted * (pred - target) ** 2).sum(axis=0),
                         denom, out=np.zeros(6, dtype=float), where=denom > 0)
    active = denom > 0
    return {"total": float(per_port[active].mean()) if active.any() else 0.0,
            "per_port_mse": per_port.tolist(),
            "supported_port_weight": denom.tolist()}


def _fit_one(rows, fit_roots, validation_roots, model_dir, *, source_audit,
             frame_scales, context_scales, seed, hidden, epochs, batch_size,
             learning_rate, patience, named_ood_rows=None, fit_role="main"):
    out = Path(model_dir)
    out.mkdir(parents=True, exist_ok=False)
    _new_json(out / "source_audit.json", source_audit)
    fit = [row for row in rows if row["root_family_id"] in fit_roots]
    validation = [row for row in rows if row["root_family_id"] in validation_roots]
    if not fit or not validation:
        raise ValueError("Independent whole-root fit and validation examples required")
    if set(fit_roots) & set(validation_roots):
        raise ValueError("Root leakage across fit and validation")
    norm = {"frame_physical_scale": frame_scales.tolist(),
            "context_physical_scale": context_scales.tolist(),
            "zero_variance_training_statistics_used": False,
            "validation_or_development_statistics_used": False}
    arrays = _arrays(fit, frame_scales, context_scales)
    val_arrays = _arrays(validation, frame_scales, context_scales)
    ood_arrays = (_arrays(named_ood_rows, frame_scales, context_scales)
                  if named_ood_rows else None)
    with (out / "example_ledger.jsonl").open("x", encoding="utf-8") as stream:
        for split_name, group in (("fit", fit), ("validation", validation),
                                  ("named_GRID0_OOD", named_ood_rows or [])):
            for index, row in enumerate(group):
                record = {
                    "split": split_name, "array_index": index,
                    "scenario_id": row["scenario_id"],
                    "root_family_id": row["root_family_id"],
                    "condition_id": row["condition_id"],
                    "decision_time_min": row["decision_time_min"],
                    "winner_policy_id": row["winner_policy_id"],
                    "source_state_policy_id": row["source_state_policy_id"],
                    "source_trajectory_sha256": row["source_trajectory_sha256"],
                    "target_normalized_six_residual": row["target_residual"].tolist(),
                    "legal_port_mask": row["legal_port_mask"].tolist(),
                    "secure_targets_kW": row["secure_targets_kW"].tolist(),
                    "source_request_targets_kW": row["source_request_targets_kW"].tolist(),
                    "legal_port_upper_kW": row["legal_port_upper_kW"].tolist(),
                    "root_weight": float(row["root_weight"]),
                }
                stream.write(json.dumps(record, ensure_ascii=False,
                                        allow_nan=False) + "\n")
    np.savez_compressed(
        out / "fit_examples.npz",
        train_frame=arrays[0], train_context=arrays[1], train_target=arrays[2],
        train_legal_mask=arrays[3], train_root_weight=arrays[4],
        validation_frame=val_arrays[0], validation_context=val_arrays[1],
        validation_target=val_arrays[2], validation_legal_mask=val_arrays[3],
        validation_root_weight=val_arrays[4],
        train_root_family_id=np.asarray([row["root_family_id"] for row in fit]),
        validation_root_family_id=np.asarray([row["root_family_id"] for row in validation]),
        ood_frame=np.empty((0, 4, FRAME_DIM), dtype=np.float32) if ood_arrays is None else ood_arrays[0],
        ood_context=np.empty((0, CONTEXT_DIM), dtype=np.float32) if ood_arrays is None else ood_arrays[1],
        ood_target=np.empty((0, 6), dtype=np.float32) if ood_arrays is None else ood_arrays[2],
        ood_legal_mask=np.empty((0, 6), dtype=np.float32) if ood_arrays is None else ood_arrays[3],
        ood_root_weight=np.empty((0,), dtype=np.float32) if ood_arrays is None else ood_arrays[4],
    )
    _new_json(out / "fit_started.json", {
        "schema": SCHEMA + ".fit_started", "fit_role": fit_role,
        "requested_device": "cuda:0", "seed": seed, "hidden": hidden,
        "epochs": epochs, "batch_size": batch_size, "learning_rate": learning_rate,
        "patience": patience, "fit_roots": sorted(fit_roots),
        "validation_roots": sorted(validation_roots),
        "source_module_sha256": _sha(__file__)})
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable; v6 actor has no CPU fit fallback")
    started = time.perf_counter()
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    device = torch.device("cuda:0")
    torch.cuda.init()
    torch.cuda.reset_peak_memory_stats(device)
    model = OutcomeRankedActor(hidden).to(device)
    initial_weight_sha = _weight_sha(model)
    tensors = tuple(torch.as_tensor(x, device=device) for x in arrays)
    val_tensors = tuple(torch.as_tensor(x, device=device) for x in val_arrays)
    if ({str(param.device) for param in model.parameters()}
            | {str(tensor.device) for tensor in tensors}) != {"cuda:0"}:
        raise RuntimeError("v6 actor and optimizer tensors are not on actual cuda:0")
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)

    def loss_parts(group):
        prediction = model(group[0], group[1])
        weighted = group[3] * group[4][:, None]
        denom = weighted.sum(dim=0)
        per_port = (weighted * (prediction - group[2]).square()).sum(dim=0) / denom.clamp_min(1e-12)
        active = (denom > 0).to(per_port.dtype)
        total = (per_port * active).sum() / active.sum().clamp_min(1.0)
        return total, per_port, denom

    curve = []
    best_loss = math.inf
    best_epoch = 0
    updates = 0
    for epoch in range(1, epochs + 1):
        model.train()
        for indices in torch.randperm(len(fit), device=device).split(batch_size):
            subset = tuple(x[indices] for x in tensors)
            optimizer.zero_grad(set_to_none=True)
            total, _, _ = loss_parts(subset)
            if not bool(torch.isfinite(total)):
                raise FloatingPointError("Nonfinite CUDA outcome-ranked actor loss")
            total.backward()
            optimizer.step()
            updates += 1
        model.eval()
        with torch.inference_mode():
            fit_total, fit_per_port, fit_support = loss_parts(tensors)
            val_total, val_per_port, val_support = loss_parts(val_tensors)
        value = float(val_total.item())
        if not math.isfinite(value):
            raise FloatingPointError("Nonfinite whole-root validation loss")
        curve.append({"epoch": epoch, "fit_total": float(fit_total.item()),
                      "fit_port_mse": fit_per_port.tolist(),
                      "fit_port_support": fit_support.tolist(),
                      "validation_total": value,
                      "validation_port_mse": val_per_port.tolist(),
                      "validation_port_support": val_support.tolist(),
                      "optimizer_updates_cumulative": updates})
        if value < best_loss:
            best_loss, best_epoch = value, epoch
            torch.save(model.state_dict(), out / "best.pt")
        if epoch - best_epoch >= patience:
            break
    torch.cuda.synchronize(device)
    last_epoch = len(curve)
    last_weight_sha = _weight_sha(model)
    if updates < 1 or initial_weight_sha == last_weight_sha:
        raise RuntimeError("Actual CUDA optimizer made no model-weight update")
    torch.save(model.state_dict(), out / "last.pt")
    model.load_state_dict(torch.load(out / "best.pt", map_location=device, weights_only=True))
    best_weight_sha = _weight_sha(model)
    model.eval()
    ood_metrics = None
    if ood_arrays is not None:
        with torch.inference_mode():
            ood_tensors = tuple(torch.as_tensor(x, device=device) for x in ood_arrays)
            total, per_port, support = loss_parts(ood_tensors)
        ood_metrics = {"condition": "GRID0_grid850_delay15",
                       "fit_condition_excluded": True,
                       "used_for_checkpoint_selection": False,
                       "root_count": len({row["root_family_id"] for row in named_ood_rows}),
                       "decision_count": len(named_ood_rows),
                       "total": float(total.item()), "per_port_mse": per_port.tolist(),
                       "supported_port_weight": support.tolist()}
    train_zero = _numpy_loss(np.zeros_like(arrays[2]), arrays[2], arrays[3], arrays[4])
    val_zero = _numpy_loss(np.zeros_like(val_arrays[2]), val_arrays[2], val_arrays[3], val_arrays[4])
    weighted = arrays[3] * arrays[4][:, None]
    denom = weighted.sum(axis=0)
    constant = np.divide((weighted * arrays[2]).sum(axis=0), denom,
                         out=np.zeros(6, dtype=np.float32), where=denom > 0)
    val_constant = _numpy_loss(np.broadcast_to(constant, val_arrays[2].shape),
                               val_arrays[2], val_arrays[3], val_arrays[4])
    report = {"schema": SCHEMA + ".fit_report", "fit_role": fit_role,
              "actual_train_device": "cuda:0", "actual_validation_device": "cuda:0",
              "torch_version": torch.__version__, "torch_cuda_version": torch.version.cuda,
              "cuda_device_name": torch.cuda.get_device_name(device),
              "peak_cuda_memory_allocated_bytes": int(torch.cuda.max_memory_allocated(device)),
              "architecture": "Four-frame outcome-ranked MLP residual actor",
              "parameter_count": sum(p.numel() for p in model.parameters()),
              "seed": seed, "hidden": hidden, "requested_epochs": epochs,
              "last_epoch": last_epoch, "best_epoch": best_epoch,
              "stopped_early": last_epoch < epochs, "patience": patience,
              "optimizer_updates": updates, "optimizer": "AdamW",
              "learning_rate": learning_rate, "batch_size": batch_size,
              "initial_weight_sha256": initial_weight_sha,
              "best_weight_sha256": best_weight_sha,
              "last_weight_sha256": last_weight_sha,
              "fit_roots": sorted(fit_roots), "validation_roots": sorted(validation_roots),
              "fit_decisions": len(fit), "validation_decisions": len(validation),
              "loss_curve": curve, "zero_residual_train": train_zero,
              "zero_residual_validation": val_zero,
              "constant_residual_vector": constant.tolist(),
              "constant_residual_validation": val_constant,
              "named_grid0_ood_probe": ood_metrics,
              "loss_semantics": "Root-balanced six-port normalized residual MSE on legal current ports; outcome-ranked root winner supplies labels; no teacher-intervention or safety-probability head",
              "elapsed_seconds": time.perf_counter() - started}
    _new_json(out / "fit_report.json", report)
    metadata = {"schema": SCHEMA, "architecture": "OutcomeRankedActor",
                "hidden": hidden, "frame_dim": FRAME_DIM, "context_dim": CONTEXT_DIM,
                "normalization": norm, "fit_role": fit_role,
                "source_module_sha256": _sha(__file__),
                "observation_encoder_source_sha256":
                    source_audit["observation_encoder_source_sha256"],
                "source_audit_sha256": _sha(out / "source_audit.json"),
                "best_epoch": best_epoch, "last_epoch": last_epoch,
                "best_weight_sha256": best_weight_sha,
                "last_weight_sha256": last_weight_sha,
                "deployed_checkpoint": "best.pt", "teacher_intervention_gate": False,
                "future_rollouts_online": 0,
                "artifacts": {name: _sha(out / name) for name in (
                    "fit_started.json", "source_audit.json", "example_ledger.jsonl",
                    "fit_examples.npz",
                    "best.pt", "last.pt", "fit_report.json")}}
    _new_json(out / "metadata.json", metadata)
    return report


def _fit_outcome_ranked_inner(manifest_path, result_paths, model_dir, *, policy_ids,
                              search_plan_path,
                              seed=6101, hidden=64, epochs=80, batch_size=128,
                              learning_rate=3e-4, patience=8,
                              fit_grid0_ood_probe=True,
                              allowed_scenario_ids=None):
    """Train actual CUDA actor; choose best whole-root validation checkpoint."""
    out = Path(model_dir)
    if out.exists():
        raise FileExistsError("Keep every v6 fit attempt in a fresh model directory")
    if (type(seed) is not int or type(hidden) is not int or type(epochs) is not int
            or type(batch_size) is not int or type(patience) is not int
            or hidden < 4 or epochs < 1 or batch_size < 1 or patience < 1
            or not math.isfinite(float(learning_rate)) or learning_rate <= 0):
        raise ValueError("Invalid v6 CUDA fit configuration")
    manifest = _read(manifest_path)
    exclusion = _search_validation_exclusions(
        search_plan_path, manifest_path, manifest)
    rows, audit = collect_outcome_ranked_examples(
        manifest_path, result_paths, policy_ids=policy_ids,
        allowed_scenario_ids=allowed_scenario_ids)
    audit["formal_full_train_panel_gate"] = require_full_registered_train_panel(
        audit, manifest)
    if not rows:
        raise ValueError("No complete outcome-ranked train decisions")
    excluded = set(exclusion["search_exposed_root_ids"])
    split = stratified_root_split([
        {"root_family_id": root["root_family_id"],
         "condition_id": root["condition_id"]}
        for root in audit["root_rankings"]],
        seed=seed, validation_exclude_root_ids=excluded)
    if set(split["validation_roots"]) & excluded:
        raise AssertionError("Adaptive search-exposed root leaked into neural validation")
    plant = load_plant(ROOT / "configs/plant_configs.json", "doe_capacity_matched_exploratory")
    config = _read(ROOT / "configs/resilience_assumptions.json")
    grid = float(config["grid_import_limit_kW"])
    frame_scales = physical_frame_scales(
        plant, grid_reference_kW=grid,
        diesel_initial_energy_kWh=config["diesel_initial_energy_kWh"])
    context_scales = physical_context_scales(plant, grid_reference_kW=grid)
    audit["physical_scale_source"] = {
        "plant_config_sha256": _sha(ROOT / "configs/plant_configs.json"),
        "resilience_config_sha256": _sha(ROOT / "configs/resilience_assumptions.json"),
        "plant_case": plant.case, "diesel_rated_kW": plant.emergency_diesel_kW,
        "gt_rated_kW_per_unit": plant.gt_rated_kW,
        "absorption_capacity_kW": plant.absorption_rt * RT_TO_KW,
        "grid_reference_kW": grid}
    audit["grouped_main_split"] = split
    audit["search_validation_exclusion"] = exclusion
    audit["validation_root_choice_source"] = (
        "All registered train-panel root IDs and condition IDs, signed search-exposed "
        "root exclusion and fixed seed; outcome availability never changes the chosen roots")
    main_report = _fit_one(rows, set(split["fit_roots"]),
                           set(split["validation_roots"]), out,
                           source_audit=audit, frame_scales=frame_scales,
                           context_scales=context_scales, seed=seed, hidden=hidden,
                           epochs=epochs, batch_size=batch_size,
                           learning_rate=learning_rate, patience=patience)
    if fit_grid0_ood_probe:
        grid0 = [row for row in rows if row["condition_id"] == "GRID0_grid850_delay15"]
        non_grid = [row for row in rows if row["condition_id"] != "GRID0_grid850_delay15"]
        if not grid0:
            raise ValueError("Named GRID0 OOD probe requested but no legal GRID0 data")
        ood_split = stratified_root_split([
            {"root_family_id": root["root_family_id"],
             "condition_id": root["condition_id"]}
            for root in audit["root_rankings"]
            if root["condition_id"] != "GRID0_grid850_delay15"],
            seed=seed + 1,
            validation_exclude_root_ids=excluded)
        if set(ood_split["validation_roots"]) & excluded:
            raise AssertionError("Adaptive search-exposed root leaked into OOD fit validation")
        ood_audit = dict(audit)
        ood_audit["grouped_ood_split"] = ood_split
        ood_audit["grid0_ood_root_family_ids"] = sorted({row["root_family_id"] for row in grid0})
        _fit_one(non_grid, set(ood_split["fit_roots"]),
                 set(ood_split["validation_roots"]), out / "grid0_ood_probe",
                 source_audit=ood_audit, frame_scales=frame_scales,
                 context_scales=context_scales, seed=seed + 1, hidden=hidden,
                 epochs=epochs, batch_size=batch_size,
                 learning_rate=learning_rate, patience=patience,
                 named_ood_rows=grid0, fit_role="GRID0_leave_condition_out_probe")
    return main_report


def fit_outcome_ranked(manifest_path, result_paths, model_dir, *, policy_ids,
                       search_plan_path,
                       seed=6101, hidden=64, epochs=80, batch_size=128,
                       learning_rate=3e-4, patience=8, fit_grid0_ood_probe=True,
                       allowed_scenario_ids=None):
    """Run a new CUDA fit and retain an exception receipt for every failed attempt."""
    out = Path(model_dir)
    if out.exists():
        raise FileExistsError("Keep every v6 fit attempt in a fresh model directory")
    try:
        return _fit_outcome_ranked_inner(
            manifest_path, result_paths, model_dir, policy_ids=policy_ids,
            search_plan_path=search_plan_path,
            seed=seed, hidden=hidden, epochs=epochs, batch_size=batch_size,
            learning_rate=learning_rate, patience=patience,
            fit_grid0_ood_probe=fit_grid0_ood_probe,
            allowed_scenario_ids=allowed_scenario_ids)
    except Exception as error:
        out.mkdir(parents=True, exist_ok=True)
        _new_json(out / "fit_error.json", {
            "schema": SCHEMA + ".fit_error",
            "requested_device": "cuda:0",
            "error_type": type(error).__name__, "error": str(error),
            "traceback": traceback.format_exc(),
            "source_module_sha256": _sha(__file__),
            "manifest_path": str(Path(manifest_path).resolve()),
            "search_plan_path": str(Path(search_plan_path).resolve()),
            "result_path_count": len(result_paths),
            "policy_ids": list(policy_ids)})
        raise


def _load_artifacts(model_dir):
    directory = Path(model_dir)
    if not directory.is_absolute():
        directory = ROOT / directory
    directory = directory.resolve()
    metadata = _read(directory / "metadata.json")
    if (metadata.get("schema") != SCHEMA
            or metadata.get("architecture") != "OutcomeRankedActor"
            or metadata.get("deployed_checkpoint") != "best.pt"
            or metadata.get("teacher_intervention_gate") is not False):
        raise ValueError("Unexpected v6 outcome-ranked model metadata")
    if metadata.get("source_module_sha256") != _sha(__file__):
        raise ValueError("v6 actor source SHA changed after fit")
    if metadata.get("observation_encoder_source_sha256") != _sha(ENCODER_SOURCE):
        raise ValueError("v6 observation encoder source SHA changed after fit")
    for name, expected in metadata.get("artifacts", {}).items():
        if Path(name).name != name or _sha(directory / name) != expected:
            raise ValueError("v6 model artifact SHA mismatch: " + name)
    if not ("best.pt" in metadata["artifacts"]
            and "fit_examples.npz" in metadata["artifacts"]):
        raise ValueError("v6 model lacks best checkpoint or independent example")
    return directory, metadata


def model_artifact_binding(model_dir):
    directory, metadata = _load_artifacts(model_dir)
    return {"schema": metadata["schema"], "model_dir": str(directory),
            "metadata_sha256": _sha(directory / "metadata.json"),
            "best_checkpoint_sha256": _sha(directory / "best.pt"),
            "best_epoch": metadata.get("best_epoch"),
            "source_module_sha256": metadata["source_module_sha256"]}


def load_cpu_for_repro(model_dir):
    """Portable, checksum-checked CPU forward example of deployed best model."""
    directory, metadata = _load_artifacts(model_dir)
    model = OutcomeRankedActor(metadata["hidden"])
    model.load_state_dict(torch.load(directory / "best.pt", map_location="cpu",
                                     weights_only=True))
    if _weight_sha(model) != metadata["best_weight_sha256"]:
        raise ValueError("Best model-weight SHA mismatch after CPU reload")
    model.eval()
    with np.load(directory / "fit_examples.npz", allow_pickle=False) as data:
        if "validation_frame" in data and len(data["validation_frame"]):
            frames, context = data["validation_frame"][0], data["validation_context"][0]
        elif "train_frame" in data and len(data["train_frame"]):
            frames, context = data["train_frame"][0], data["train_context"][0]
        else:
            raise ValueError("No real stored fit example for CPU reproduction")
    example = {"frames": torch.as_tensor(frames[None], dtype=torch.float32),
               "context": torch.as_tensor(context[None], dtype=torch.float32)}
    if (example["frames"].shape != (1, 4, FRAME_DIM)
            or example["context"].shape != (1, CONTEXT_DIM)):
        raise ValueError("Stored CPU example shape mismatch")
    return model, example


class NeuralResidualPolicy:
    """Current-only v6 actor using the same six-port decoder as fixed policies."""

    def __init__(self, model, metadata, policy_id):
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("Nonempty neural policy_id required")
        self.model = model
        self.metadata = metadata
        self.policy_id = policy_id
        self.current_problem = None
        self._last_time = None

    def bind_current(self, problem):
        self.current_problem = problem

    def reset_policy_memory(self):
        self.current_problem = None
        self._last_time = None

    def decide(self, records):
        history = legal_history(records)
        if self.current_problem is None:
            raise ValueError("v6 neural policy needs a bound current problem")
        problem = self.current_problem
        self.current_problem = None
        now = float(history[-1]["decision_time_min"])
        if self._last_time is not None and now <= self._last_time and now != 0.0:
            raise ValueError("v6 neural decision clock did not advance")
        self._last_time = now
        secure_request = baseline_request("secure_milp")
        projected = project_current(problem, secure_request)
        context = problem.policy_context(history, projected["command"])
        validate_context(history, context)
        secure = _signed(projected["projected_targets_kW"], (6,), "secure projection")
        features = history[-1]["features"]
        kwargs = {"diesel_rated_kW": float(problem.plant.emergency_diesel_kW),
                  "diesel_available": bool(problem.diesel_available),
                  "step_hours": float(problem.dt_h)}
        upper = current_port_limits(context, features, **kwargs)
        diagnostic = {
            "model_id": self.policy_id, "model_family": "v6_outcome_ranked_actor",
            "strategy_fallback": False, "fallback_reason": None,
            "selected_candidate_id": "secure_baseline", "selected_candidate_index": 0,
            "baseline_policy": "secure_milp", "baseline_request_submitted_unchanged": True,
            "future_rollouts_online": 0, "teacher_intervention_gate": False,
            "best_epoch": self.metadata.get("best_epoch"),
            "best_weight_sha256": self.metadata["best_weight_sha256"],
            "current_planning_queries": [{
                "candidate_id": "secure_baseline", "status": projected["status"],
                "projected_targets_kW": secure.tolist(),
                "elapsed_seconds": projected.get("elapsed_seconds"),
                "solver_calls": copy.deepcopy(projected.get("solver_calls", []))}],
            "current_context": copy.deepcopy(context),
            "secure_projected_six_targets_kW": secure.tolist(),
            "request_capacity_scale_kW": upper.tolist(),
            "request_legal_mask": [bool(value > 0) for value in upper],
            "diesel_available_current": kwargs["diesel_available"],
            "current_step_hours": kwargs["step_hours"],
            "diesel_rated_kW": kwargs["diesel_rated_kW"],
            "request_is_not_execution": True,
        }
        try:
            frame, current = encode_observation(history, context)
            norm = self.metadata["normalization"]
            frame_scale = np.asarray(norm["frame_physical_scale"], dtype=np.float64)
            context_scale = np.asarray(norm["context_physical_scale"], dtype=np.float64)
            frame = frame[None]
            frame[:, :, 1:] = (frame[:, :, 1:] / frame_scale) * frame[:, :, :1]
            current = (current / context_scale)[None]
            with torch.inference_mode():
                tensor_frame = torch.as_tensor(frame, dtype=torch.float32, device="cuda:0")
                tensor_context = torch.as_tensor(current, dtype=torch.float32, device="cuda:0")
                if ({str(p.device) for p in self.model.parameters()}
                        | {str(tensor_frame.device), str(tensor_context.device)}) != {"cuda:0"}:
                    raise RuntimeError("v6 NN inference model/tensors not on actual cuda:0")
                residual = self.model(tensor_frame, tensor_context)[0].cpu().numpy().astype(float)
            if not np.all(np.isfinite(residual)):
                raise FloatingPointError("Nonfinite v6 actor residual")
            requested = decode_residual(secure, context, features, residual, **kwargs)
            diagnostic.update(predicted_normalized_six_residual=residual.tolist(),
                              requested_six_targets_kW=requested.tolist(),
                              delta_from_secure_plan_kW=(requested - secure).tolist())
            if np.max(np.abs(requested - secure)) <= 1e-7:
                diagnostic["strategy_status"] = "neural_secure_identity"
                return secure_request, diagnostic
            request = copy.deepcopy(secure_request)
            request["targets"] = dict(zip(ACTION_NAMES, map(float, requested)))
            validate_action(request)
            diagnostic.update(strategy_status="v6_neural_six_port_request",
                              selected_candidate_id="v6_neural_residual",
                              selected_candidate_index=1,
                              baseline_request_submitted_unchanged=False)
            return request, diagnostic
        except (ValueError, FloatingPointError) as error:
            diagnostic.update(strategy_fallback=True,
                              strategy_status="v6_neural_input_or_prediction_fallback",
                              fallback_reason=str(error), fallback_error_type=type(error).__name__)
            return secure_request, diagnostic


def load_neural_policy(model_dir, policy_id):
    directory, metadata = _load_artifacts(model_dir)
    if not torch.cuda.is_available():
        raise RuntimeError("Actual CUDA required for v6 neural feedback inference")
    model = OutcomeRankedActor(metadata["hidden"])
    model.load_state_dict(torch.load(directory / "best.pt", map_location="cpu",
                                     weights_only=True))
    if _weight_sha(model) != metadata["best_weight_sha256"]:
        raise ValueError("Deployed best model-weight SHA mismatch")
    model.to("cuda:0").eval()
    return NeuralResidualPolicy(model, metadata, policy_id)


def result_paths_for_policy_ids(results_dirs, policy_ids):
    """Select latest actual terminal receipts; attempts remain in raw directories."""
    wanted = set(policy_ids)
    selected = {}
    for directory in map(Path, results_dirs):
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        for path in directory.glob("pairs/*/attempt_*/result.json"):
            result = _read(path)
            if result.get("policy_id") not in wanted:
                continue
            key = (result.get("scenario_id"), result.get("policy_id"))
            attempt = int(path.parent.name.removeprefix("attempt_"))
            previous = selected.get(key)
            if previous is not None and previous[0].parents[3] != path.parents[3]:
                raise ValueError("Duplicate same scenario/policy across train batches")
            if previous is None or attempt > previous[1]:
                selected[key] = (path, attempt)
    if not selected:
        raise ValueError("No terminal registered results for requested policy IDs")
    return [record[0] for _, record in sorted(selected.items())]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--search-plan", required=True)
    parser.add_argument("--results-dir", action="append", required=True)
    parser.add_argument("--policy-id", action="append", required=True)
    parser.add_argument("--allowed-scenario-id", action="append")
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=6101)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--no-grid0-ood-probe", action="store_true")
    args = parser.parse_args(argv)
    paths = result_paths_for_policy_ids(args.results_dir, args.policy_id)
    report = fit_outcome_ranked(
        args.manifest, paths, args.out, policy_ids=args.policy_id,
        search_plan_path=args.search_plan,
        seed=args.seed, hidden=args.hidden, epochs=args.epochs,
        batch_size=args.batch_size, learning_rate=args.learning_rate,
        patience=args.patience, fit_grid0_ood_probe=not args.no_grid0_ood_probe,
        allowed_scenario_ids=args.allowed_scenario_id)
    print(json.dumps({"fit_decisions": report["fit_decisions"],
                      "validation_decisions": report["validation_decisions"],
                      "best_epoch": report["best_epoch"],
                      "last_epoch": report["last_epoch"],
                      "actual_train_device": report["actual_train_device"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
