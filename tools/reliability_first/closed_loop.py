"""Immutable, resumable matched closed-loop runs on registered hospital scenes.

Each scenario/policy attempt has its own directory. A completed or physically
truncated raw trajectory is terminal for that pair; a runner exception keeps
its error receipt and may be retried once in a new directory. No result is
filled with zero on failure.
"""
from __future__ import annotations

import copy
from collections import Counter
import gzip
import hashlib
import inspect
import json
from pathlib import Path
import time
import traceback

from tools.control_improvement.data_runner import verify_manifest
from tools.control_improvement.branches import window_targets
from tools.control_improvement.episode import run_policy_episode
from tools.integrated_learning.branches import compare_continuation
from tools.integrated_learning.policies import BaselinePolicy
from tools.integrated_learning.snapshot import capture_execution
from tools.reliability_first.policy import SecureAnchoredPolicy
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.dataset_export import json_clean
from hospital_ies.dataset_manifest import digest


SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = SOURCE_ROOT
SCHEMA = "reliability_first.matched_closed_loop.v1"
KINDS = {"baseline", "identity", "rule", "gated", "ungated"}
MAX_RUNNER_ATTEMPTS_PER_PAIR = 2
SOURCE_FILES = (
    "tools/reliability_first/closed_loop.py",
    "tools/reliability_first/run_eval.py",
    "tools/reliability_first/policy.py",
    "tools/reliability_first/selection.py",
    "tools/reliability_first/calibration.py",
    "tools/reliability_first/neural.py",
    "tools/reliability_first/tree.py",
    "tools/control_improvement/candidates.py",
    "tools/control_improvement/ranking.py",
    "tools/control_improvement/runtime.py",
    "tools/control_improvement/episode.py",
    "tools/control_improvement/branches.py",
    "tools/control_improvement/data_runner.py",
    "tools/integrated_learning/policies.py",
    "tools/integrated_learning/branches.py",
    "tools/integrated_learning/snapshot.py",
    "src/hospital_ies/control_env.py",
    "src/hospital_ies/dataset_export.py",
    "src/hospital_ies/dataset_manifest.py",
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def _reference(path):
    path = Path(path).resolve()
    return path.relative_to(ROOT.resolve()).as_posix()


def _model_binding(model_dir):
    directory = (ROOT / model_dir).resolve()
    if not directory.is_dir():
        raise FileNotFoundError("Registered model directory is absent")
    relative = directory.relative_to(ROOT.resolve()).as_posix()
    files = sorted(p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    if not files:
        raise ValueError("Registered model directory is empty")
    return {"directory": relative,
            "files": [{"path": p.relative_to(directory).as_posix(), "sha256": sha(p)} for p in files]}


def _source_binding():
    return {name: sha(SOURCE_ROOT / name) for name in SOURCE_FILES}


def _validated_design_binding(binding):
    if not isinstance(binding, dict):
        raise ValueError("Registered design binding must be a path/hash map")
    for name, expected_sha in binding.items():
        if not isinstance(name, str) or not isinstance(expected_sha, str):
            raise ValueError("Registered design path or hash is invalid")
        path = (ROOT / name).resolve()
        if path == ROOT.resolve() or not path.is_relative_to(ROOT.resolve()):
            raise ValueError("Registered design path escapes the workspace")
        if not path.is_file() or sha(path) != expected_sha:
            raise ValueError("Registered design bytes changed")
    return binding


def _manifest(path):
    manifest = _read(path)
    verify_manifest(manifest)
    body = copy.deepcopy(manifest)
    signature = body.pop("signature")
    if digest(body) != signature:
        raise ValueError("Manifest content/signature changed")
    return manifest


def _registered_scenario(manifest, scenario_id, *, phase):
    scenario = next((s for s in manifest["scenarios"] if s["id"] == scenario_id), None)
    if scenario is None:
        raise ValueError("Scenario absent from registered manifest")
    split = manifest["splits"][scenario_id]
    if scenario.get("is_final_blind") or split.get("is_final_blind"):
        raise ValueError("Sealed final scenario is not authorized")
    expected = "pilot_train" if phase == "training" else "development_boundary"
    if scenario["experiment_role"] != expected or split["experiment_role"] != expected:
        raise ValueError("Scenario role differs from this registered phase")
    return scenario


def register_closed_loop(manifest_path, plan_path, *, scenario_ids, policy_specs,
                         phase="development", design_binding=None,
                         registration_mode=None):
    """Freeze a new development/training/sensitivity comparison before rollout."""
    if phase not in ("development", "training", "sensitivity"):
        raise ValueError("Unknown registered comparison phase")
    mp = Path(manifest_path).resolve()
    plan_path = Path(plan_path)
    if plan_path.exists():
        raise FileExistsError("Use a fresh closed-loop plan path")
    manifest = _manifest(mp)
    ids = list(scenario_ids)
    if not ids or len(ids) != len(set(ids)) or len(ids) > 1200:
        raise ValueError("Duplicate, absent or over-budget scenario list")
    for sid in ids:
        _registered_scenario(manifest, sid, phase=phase)
    if not policy_specs:
        raise ValueError("At least one policy must be registered")
    specs = copy.deepcopy(list(policy_specs))
    names = [s.get("policy_id") for s in specs]
    if len(names) != len(set(names)) or any(not isinstance(name, str) or not name for name in names):
        raise ValueError("Policy IDs must be unique nonempty strings")
    if not any(s.get("kind") == "baseline" and s["policy_id"] == "secure_milp" for s in specs):
        raise ValueError("The original secure_milp baseline is required for matching")
    model_bindings = {}
    for spec in specs:
        if set(spec) - {"policy_id", "kind", "model_dir", "gate"} or spec.get("kind") not in KINDS:
            raise ValueError("Unregistered policy kind or field")
        if spec["kind"] in ("gated", "ungated") and not spec.get("model_dir"):
            raise ValueError("Learned policy needs a registered model directory")
        if spec.get("model_dir"):
            model_bindings[spec["policy_id"]] = _model_binding(spec["model_dir"])
    if registration_mode not in (None, "formal_development", "calibrated_smoke",
                                 "uncalibrated_exploration_smoke"):
        raise ValueError("Unknown closed-loop registration mode")
    if registration_mode is not None and phase != "development":
        raise ValueError("Named development registration requires development phase")
    if registration_mode in ("calibrated_smoke", "uncalibrated_exploration_smoke") and len(ids) != 1:
        raise ValueError("Exploration smoke must contain exactly one scenario")
    if registration_mode in ("formal_development", "calibrated_smoke"):
        for spec in specs:
            if spec["kind"] != "gated":
                continue
            if spec.get("gate", {}).get("calibration_file") != "gate_calibration.json":
                raise ValueError("Formal gate requires independent calibration")
            files = {item["path"] for item in model_bindings[spec["policy_id"]]["files"]}
            if not {"gate_calibration.json", "gate_calibration_rows.json"} <= files:
                raise ValueError("Registered model lacks independent calibration evidence")
    if registration_mode == "uncalibrated_exploration_smoke" and any(
            spec.get("gate", {}).get("calibration_file") for spec in specs):
        raise ValueError("Uncalibrated smoke cannot claim registered calibration")
    if registration_mode is not None and not design_binding:
        raise ValueError("Named development registration requires frozen new design binding")
    design = _validated_design_binding(copy.deepcopy(design_binding or {}))
    plan = {"schema": SCHEMA, "manifest_path": _reference(mp),
            "manifest_sha256": sha(mp), "manifest_signature": manifest["signature"],
            "simulator_hash": manifest["simulator_hash"],
            "execution_contract_id": manifest["execution_contract_id"],
            "phase": phase, "scenario_ids": ids, "policy_specs": specs,
            "model_bindings": model_bindings, "source_sha256": _source_binding(),
            "design_source_sha256": design, "registration_mode": registration_mode,
            "score_windows": {"primary": [60.0, 420.0], "full": [0.0, 600.0]},
            "maximum_trajectory_attempts": len(ids) * len(specs),
            "max_runner_attempts_per_pair": MAX_RUNNER_ATTEMPTS_PER_PAIR,
            "final_blind_opened": False, "training_outcome_selection": phase == "training"}
    plan["signature"] = digest(plan)
    _write_new(plan_path, plan)
    return plan


def _validated_plan(path):
    plan = _read(path)
    body = copy.deepcopy(plan)
    signature = body.pop("signature")
    if plan.get("schema") != SCHEMA or digest(body) != signature:
        raise ValueError("Closed-loop plan changed")
    if plan["source_sha256"] != _source_binding():
        raise ValueError("Closed-loop source differs from registered plan")
    _validated_design_binding(plan.get("design_source_sha256", {}))
    mp = ROOT / plan["manifest_path"]
    if sha(mp) != plan["manifest_sha256"]:
        raise ValueError("Registered manifest bytes changed")
    manifest = _manifest(mp)
    if (manifest["signature"] != plan["manifest_signature"] or
            manifest["simulator_hash"] != plan["simulator_hash"] or
            manifest["execution_contract_id"] != plan["execution_contract_id"]):
        raise ValueError("Registered physical/manifest binding changed")
    for sid in plan["scenario_ids"]:
        _registered_scenario(manifest, sid, phase=plan["phase"])
    for binding in plan["model_bindings"].values():
        if _model_binding(binding["directory"]) != binding:
            raise ValueError("Registered model directory bytes changed")
    return plan, manifest


def _factory_binding(factory):
    try:
        source = inspect.getsource(factory)
    except (OSError, TypeError):
        source = None
    return {"module": getattr(factory, "__module__", None),
            "qualname": getattr(factory, "__qualname__", None),
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest() if source else None}


def _default_policy_factory(spec):
    kind = spec["kind"]
    if kind == "baseline":
        policy = BaselinePolicy("secure_milp")
    elif kind in ("identity", "rule"):
        policy = SecureAnchoredPolicy(mode=kind)
    else:
        raise ValueError("Learned policy requires an explicit bound policy_factory")
    policy.policy_id = spec["policy_id"]
    return policy


def _journal(path, **record):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(time_unix=time.time(), **record), ensure_ascii=False,
                                allow_nan=False) + "\n")


def _save_gzip_new(path, payload):
    with Path(path).open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped:
            zipped.write(json.dumps(json_clean(payload), ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8"))
    return sha(path)


def _command_counts(payload):
    events = Counter(e.get("kind") for e in payload.get("events", []))
    decisions = payload.get("decisions", [])
    diagnostics = payload.get("policy_diagnostics") or [d.get("policy_diagnostic") for d in decisions]
    return {"requested_decisions": sum(d.get("requested_command") is not None for d in decisions),
            "planned_commands": events["command_generated"],
            "arrived_commands": events["command_arrived"],
            "executed_commands": events["command_executed"],
            "superseded_commands": events["command_superseded"],
            "actual_execution_decisions": sum(d.get("actual_execution") is not None for d in decisions),
            "strategy_fallback_steps": sum(bool(d and d.get("strategy_fallback")) for d in diagnostics),
            "intervention_steps": sum(bool(d and d.get("selected_candidate_index") not in (None, 0))
                                      for d in diagnostics)}


def _payload_binding(payload, scenario, manifest):
    identity = payload.get("identity", {})
    required = {"scenario_id": scenario["id"], "manifest_signature": manifest["signature"],
                "simulator_hash": manifest["simulator_hash"],
                "execution_contract_id": manifest["execution_contract_id"],
                "group_id": manifest["splits"][scenario["id"]]["group_id"]}
    checks = {k: identity.get(k) == value for k, value in required.items()}
    checks["scenario_equal"] = payload.get("scenario") == scenario
    checks["seed_equal"] = payload.get("seed") == manifest["options"]["seed"]
    checks["initial_state_present"] = payload.get("initial_state") is not None
    return {"passed": all(checks.values()), "checks": checks,
            "initial_state_sha256": digest(payload["initial_state"]) if checks["initial_state_present"] else None,
            "exogenous_stream_id": identity.get("exogenous_stream_id")}


def _attempt_directory(out, scenario_id, policy_id):
    pair = Path(out) / "pairs" / digest([scenario_id, policy_id])
    pair.mkdir(parents=True, exist_ok=True)
    # Only exact numeric attempt directories count toward the retry budget.
    attempts = sorted(p for p in pair.iterdir() if p.is_dir() and
                      p.name.startswith("attempt_") and p.name[8:].isdigit())
    return pair, attempts


def _latest_result(attempts):
    for attempt in reversed(attempts):
        path = attempt / "result.json"
        if path.exists():
            result = _read(path)
            if result.get("trajectory_path"):
                raw = attempt / result["trajectory_path"]
                if not raw.is_file() or sha(raw) != result["trajectory_sha256"]:
                    raise ValueError("Previously completed raw trajectory changed")
            return result
    return None


def _summary(plan, out, attempted_this_call):
    results = []
    by_scenario = {}
    for sid in plan["scenario_ids"]:
        by_scenario[sid] = []
        for spec in plan["policy_specs"]:
            pair, attempts = _attempt_directory(out, sid, spec["policy_id"])
            result = _latest_result(attempts)
            if result is None:
                continue
            results.append(result)
            by_scenario[sid].append(result)
    matched = None
    if len(results) == plan["maximum_trajectory_attempts"] and all(r.get("terminal") for r in results):
        matched = all(len({r["binding"]["initial_state_sha256"] for r in rows}) == 1 and
                      len({r["binding"]["exogenous_stream_id"] for r in rows}) == 1 and
                      all(r["binding"]["passed"] for r in rows)
                      for rows in by_scenario.values())
    statuses = Counter(r["status"] for r in results)
    summary = {"schema": SCHEMA + ".summary", "plan_signature": plan["signature"],
               "registered_pair_count": plan["maximum_trajectory_attempts"],
               "attempted_this_call": attempted_this_call, "terminal_pair_count": sum(bool(r.get("terminal")) for r in results),
               "completed_pair_count": statuses["completed"],
               "model_constraint_infeasible_count": statuses["model_constraint_infeasible"],
               "runner_exception_count": statuses["runner_exception"],
               "unknown_primary_pair_count": sum(bool(r.get("terminal") and not r["primary_score"]["full_horizon"])
                                                 for r in results),
               "matched_initial_state_passed": matched,
               "status_counts": dict(statuses), "results": results,
               "case_denominator_includes_unknown": True}
    return summary


def run_closed_loop(plan_path, out_dir, *, policy_factory=None, max_new_pairs=None):
    """Run missing registered pairs; preserve every raw/failed attempt."""
    plan, manifest = _validated_plan(plan_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    copy_path = out / "registered_plan.json"
    if copy_path.exists():
        if _read(copy_path) != plan:
            raise ValueError("Resume plan differs from original output")
    else:
        if any(out.iterdir()):
            raise FileExistsError("Nonempty output has no matching registered plan")
        _write_new(copy_path, plan)
    factory = policy_factory or _default_policy_factory
    factory_path = out / "policy_factory_binding.json"
    binding = _factory_binding(factory)
    if factory_path.exists():
        if _read(factory_path) != binding:
            raise ValueError("Policy factory changed during resumable comparison")
    else:
        _write_new(factory_path, binding)
    if max_new_pairs is not None and int(max_new_pairs) < 0:
        raise ValueError("Negative new-pair limit")
    specs = {s["id"]: s for s in manifest["scenarios"]}
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    journal = out / "attempts.jsonl"
    attempted = 0
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            if max_new_pairs is not None and attempted >= int(max_new_pairs):
                break
            pair, previous = _attempt_directory(out, sid, spec["policy_id"])
            result = _latest_result(previous)
            if result and result.get("terminal"):
                continue
            if len(previous) >= plan["max_runner_attempts_per_pair"]:
                continue
            attempt = pair / f"attempt_{len(previous)+1:04d}"
            attempt.mkdir(exist_ok=False)
            _journal(journal, event="started", scenario_id=sid, policy_id=spec["policy_id"],
                     attempt=attempt.name, phase=plan["phase"])
            attempted += 1
            started = time.perf_counter()
            try:
                policy = factory(copy.deepcopy(spec))
                if policy.policy_id != spec["policy_id"]:
                    raise ValueError("Factory policy ID differs from registered policy")
                payload = run_policy_episode(env, specs[sid], policy, manifest["options"]["seed"],
                                             manifest, horizons=(15, 30, 60, 120))
                raw = attempt / "trajectory.json.gz"
                raw_sha = _save_gzip_new(raw, payload)
                primary = window_targets(payload, 60.0, 360.0, env)
                full = window_targets(payload, 0.0, 600.0, env)
                physical = _payload_binding(payload, specs[sid], manifest)
                result = {"schema": SCHEMA + ".pair", "scenario_id": sid,
                          "policy_id": spec["policy_id"], "kind": spec["kind"],
                          "phase": plan["phase"], "plan_signature": plan["signature"],
                          "status": payload["summary"]["status"], "terminal": True,
                          "trajectory_path": raw.name, "trajectory_sha256": raw_sha,
                          "observed_until_minute": payload["summary"].get("observed_until_minute"),
                          "primary_score": primary, "full_observation": full,
                          "binding": physical, "command_counts": _command_counts(payload),
                          "end_to_end_seconds": time.perf_counter() - started}
                _write_new(attempt / "result.json", result)
                _journal(journal, event="finished", scenario_id=sid, policy_id=spec["policy_id"],
                         attempt=attempt.name, status=result["status"], trajectory_sha256=raw_sha,
                         binding_passed=physical["passed"])
            except Exception as error:
                error_record = {"status": "runner_exception", "error_type": type(error).__name__,
                                "message": str(error), "traceback": traceback.format_exc(),
                                "scenario_id": sid, "policy_id": spec["policy_id"],
                                "plan_signature": plan["signature"],
                                "raw_trajectory_exists": (attempt / "trajectory.json.gz").exists()}
                _write_new(attempt / "error.json", error_record)
                _write_new(attempt / "result.json", {"schema": SCHEMA + ".pair",
                                                   "scenario_id": sid, "policy_id": spec["policy_id"],
                                                   "status": "runner_exception", "terminal": False,
                                                   "plan_signature": plan["signature"],
                                                   "error_path": "error.json"})
                _journal(journal, event="finished", scenario_id=sid, policy_id=spec["policy_id"],
                         attempt=attempt.name, status="runner_exception", error_type=type(error).__name__)
        if max_new_pairs is not None and attempted >= int(max_new_pairs):
            break
    summary = _summary(plan, out, attempted)
    serial = 1
    while (out / f"summary_{serial:04d}.json").exists():
        serial += 1
    _write_new(out / f"summary_{serial:04d}.json", summary)
    return summary


def run_identity_pilot(manifest_path, scenario_id, out_dir):
    """One actual secure-vs-identity trajectory pair and physical trace check."""
    manifest = _manifest(manifest_path)
    scenario = next((s for s in manifest["scenarios"] if s["id"] == scenario_id), None)
    if scenario is None or scenario.get("is_final_blind") or manifest["splits"][scenario_id].get("is_final_blind"):
        raise ValueError("Unknown or sealed identity pilot scene")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    binding = {"scenario_id": scenario_id, "manifest_signature": manifest["signature"],
               "simulator_hash": manifest["simulator_hash"],
               "execution_contract_id": manifest["execution_contract_id"]}
    secure = run_policy_episode(env, scenario, BaselinePolicy("secure_milp"),
                                manifest["options"]["seed"], manifest, horizons=(15, 30, 60, 120))
    secure_final = capture_execution(env, binding=binding)
    identity = run_policy_episode(env, scenario, SecureAnchoredPolicy(mode="identity"),
                                  manifest["options"]["seed"], manifest, horizons=(15, 30, 60, 120))
    identity_final = capture_execution(env, binding=binding)
    secure_sha = _save_gzip_new(out / "secure_baseline.json.gz", secure)
    identity_sha = _save_gzip_new(out / "secure_identity.json.gz", identity)
    comparison = compare_continuation(secure, identity, 0.0,
                                      reference_final=secure_final,
                                      restored_final=identity_final)
    initial_equal = digest(secure["initial_state"]) == digest(identity["initial_state"])
    report = {"schema": SCHEMA + ".identity_pilot", "scenario_id": scenario_id,
              "manifest_sha256": sha(manifest_path), "manifest_signature": manifest["signature"],
              "secure_trajectory_sha256": secure_sha, "identity_trajectory_sha256": identity_sha,
              "initial_state_equal": initial_equal, "trace_comparison": comparison,
              "passed": initial_equal and comparison["passed"]}
    _write_new(out / "PILOT_IDENTITY.json", report)
    return report
