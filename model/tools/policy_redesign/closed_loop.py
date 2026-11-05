"""Immutable v6 matched full-feedback runs on the unchanged hospital IES.

Every scenario/policy pair has its own attempt directory.  A completed or
physically truncated trajectory is terminal; an exception keeps its raw/error
receipt and permits one fresh retry.  Unknown outcomes are never zero-filled.
"""
from __future__ import annotations

import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.dataset_manifest import digest, runtime_versions
from tools.control_improvement.branches import window_targets
from tools.control_improvement.episode import run_policy_episode
from tools.integrated_learning.policies import BaselinePolicy
from tools.policy_redesign.policy import ResidualFeedbackPolicy
from tools.reliability_first.closed_loop import (_save_gzip_new, _payload_binding,
                                                   _command_counts)
from tools.reliability_first.selection import build_current_policy


SCHEMA = "policy_redesign.matched_full_feedback.v1"
KINDS = {"secure", "priority", "v6_feedback", "v6_neural", "strict_guard",
         "current_rule", "neural_feedback"}
PHASES = {"train", "selection", "calibration", "development", "confirmation"}
PHYSICS_FILES = frozenset({
    "src/hospital_ies/model.py", "src/hospital_ies/resilience.py",
    "src/hospital_ies/control_env.py", "src/hospital_ies/state_transition.py",
    "src/hospital_ies/execution.py", "src/hospital_ies/thermal_inertia.py",
    "src/hospital_ies/idf_parameters.py", "configs/plant_configs.json",
    "configs/resilience_assumptions.json", "src/hospital_ies/solver_backend.py",
    "src/hospital_ies/solver_diagnostics.py",
})
BASE_SOURCE_FILES = (
    "tools/policy_redesign/closed_loop.py",
    "tools/policy_redesign/policy.py",
    "tools/control_improvement/runtime.py",
    "tools/control_improvement/candidates.py",
    "tools/control_improvement/episode.py",
    "tools/control_improvement/branches.py",
    "tools/integrated_learning/policies.py",
    "tools/reliability_first/closed_loop.py",
    "tools/reliability_first/policy.py",
    "tools/reliability_first/selection.py",
    "src/hospital_ies/data_contract.py",
    "src/hospital_ies/dataset_export.py",
    "src/hospital_ies/dataset_manifest.py",
)


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _source_files(specs):
    files = set(BASE_SOURCE_FILES)
    kinds = {spec["kind"] for spec in specs}
    if "v6_neural" in kinds:
        files.add("tools/policy_redesign/neural.py")
    if "neural_feedback" in kinds:
        files.add("tools/policy_search/neural.py")
        files.add("tools/policy_search/policy.py")
    return tuple(sorted(files))


def _source_binding(specs):
    return {name: _sha(ROOT / name) for name in _source_files(specs)}


def _checked_inventory_path(name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError("Physical inventory path must be workspace-relative")
    root = ROOT.resolve()
    path = (root / name).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Physical inventory path escapes workspace")
    return path


def _verify_manifest(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("Invalid registered manifest")
    body = {k: v for k, v in manifest.items() if k != "signature"}
    if manifest.get("signature") != digest(body):
        raise ValueError("Manifest signature changed")
    if manifest.get("runtime") != runtime_versions():
        raise ValueError("Manifest runtime/backend dependency changed")
    if manifest.get("policy_search_final_12_backgrounds_unopened") is False:
        raise ValueError("Original final blind backgrounds were opened")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("Physical source/input inventory missing")
    names = [row.get("path") for row in files if isinstance(row, dict)]
    if len(names) != len(files) or len(names) != len(set(names)):
        raise ValueError("Physical inventory malformed or duplicate")
    for row in files:
        path = _checked_inventory_path(row["path"])
        if (not path.is_file() or path.stat().st_size != row.get("bytes")
                or _sha(path) != row.get("sha256")):
            raise ValueError("Physical source/input bytes changed: " + row["path"])
    if not PHYSICS_FILES <= set(names):
        raise ValueError("Physical simulator dependency inventory incomplete")
    physics = [row for row in files if row["path"] in PHYSICS_FILES]
    if digest(physics) != manifest.get("simulator_hash"):
        raise ValueError("Physical simulator hash differs from inventory")
    contract = manifest.get("execution_contract")
    if (not isinstance(contract, dict) or
            manifest.get("execution_contract_id") != str(contract.get("name")) + ":" + digest(contract)):
        raise ValueError("Execution contract binding changed")
    if not isinstance(manifest.get("options"), dict) or "seed" not in manifest["options"]:
        raise ValueError("Manifest random seed missing")
    scenarios = manifest.get("scenarios")
    splits = manifest.get("splits")
    if not isinstance(scenarios, list) or not isinstance(splits, dict):
        raise ValueError("Manifest scenarios/splits missing")
    ids = [scene.get("id") for scene in scenarios if isinstance(scene, dict)]
    if len(ids) != len(scenarios) or len(ids) != len(set(ids)):
        raise ValueError("Manifest scene identities malformed or duplicate")
    return digest(files)


def _check_scenario(manifest, scenario_id, phase):
    scene = next((s for s in manifest["scenarios"] if s["id"] == scenario_id), None)
    split = manifest["splits"].get(scenario_id)
    if scene is None or split is None:
        raise ValueError("Scenario missing from registered manifest")
    if scene.get("is_final_blind") or split.get("is_final_blind"):
        raise ValueError("sealed final scenario is inaccessible")
    if scene.get("experiment_role") != phase or split.get("experiment_role") != phase:
        raise ValueError("Scenario role differs from registered comparison phase")
    return scene


def _check_specs(policy_specs):
    specs = copy.deepcopy(list(policy_specs))
    if not specs or len(specs) > 80:
        raise ValueError("Policy configuration count outside v6 budget")
    ids = [spec.get("policy_id") for spec in specs]
    if len(ids) != len(set(ids)) or any(not isinstance(pid, str) or not pid for pid in ids):
        raise ValueError("Policy IDs must be unique and nonempty")
    if {"policy_id": "secure_milp", "kind": "secure"} not in specs:
        raise ValueError("Exact original secure baseline required")
    for spec in specs:
        kind = spec.get("kind")
        if kind not in KINDS:
            raise ValueError("Unknown v6 registered policy kind")
        allowed = {"policy_id", "kind"}
        if kind == "secure" and spec["policy_id"] != "secure_milp":
            raise ValueError("Original secure policy ID differs")
        if kind == "priority" and spec["policy_id"] != "priority_milp":
            raise ValueError("Original priority policy ID differs")
        if kind == "v6_feedback":
            allowed |= {"family", "theta"}
            ResidualFeedbackPolicy(spec.get("theta", []), spec["policy_id"],
                                   family=spec.get("family", "port_residual"))
        elif kind in ("v6_neural", "strict_guard", "neural_feedback"):
            allowed.add("model_dir")
            if not spec.get("model_dir"):
                raise ValueError("Learned comparison needs model_dir")
        if set(spec) - allowed:
            raise ValueError("Unregistered policy specification field")
    return specs


def _model_binding(spec):
    directory = spec.get("model_dir")
    if directory is None:
        return None
    folder = (ROOT / directory).resolve()
    if not folder.is_dir():
        raise FileNotFoundError("Registered model directory missing")
    files = sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    if not files:
        raise ValueError("Registered model directory is empty")
    return {"directory": str(folder),
            "files": {p.relative_to(folder).as_posix(): _sha(p) for p in files}}


def register_batch(manifest_path, plan_path, *, scenario_ids, policy_specs,
                   preference_path, phase):
    """Freeze a single matched batch before any of its outcome attempts."""
    if phase not in PHASES:
        raise ValueError("Unknown v6 comparison phase")
    mp = Path(manifest_path).resolve()
    pp = Path(plan_path)
    if pp.exists():
        raise FileExistsError("Use a fresh immutable v6 batch plan")
    manifest = _read(mp)
    inventory_digest = _verify_manifest(manifest)
    ids = list(scenario_ids)
    if not ids or len(ids) != len(set(ids)) or len(ids) > 1500:
        raise ValueError("Missing, repeated or over-budget scenario set")
    for sid in ids:
        _check_scenario(manifest, sid, phase)
    specs = _check_specs(policy_specs)
    pref = Path(preference_path).resolve()
    if not pref.is_file():
        raise FileNotFoundError("Preregistered research preference absent")
    plan = {
        "schema": SCHEMA,
        "manifest_path": str(mp), "manifest_sha256": _sha(mp),
        "manifest_signature": manifest["signature"],
        "physical_input_inventory_sha256": inventory_digest,
        "simulator_hash": manifest["simulator_hash"],
        "execution_contract_id": manifest["execution_contract_id"],
        "preference_path": str(pref), "preference_sha256": _sha(pref),
        "phase": phase, "scenario_ids": ids, "policy_specs": specs,
        "model_bindings": {s["policy_id"]: _model_binding(s) for s in specs if s.get("model_dir")},
        "source_sha256": _source_binding(specs),
        "score_windows": {"primary": [60, 420], "full": [0, 600]},
        "registered_pair_count": len(ids) * len(specs),
        "max_runner_attempts_per_pair": 2,
        "final_blind_opened": False,
    }
    plan["signature"] = digest(plan)
    _new_json(pp, plan)
    return plan


def verify_batch(plan_path):
    plan = _read(plan_path)
    signature = plan.get("signature")
    if plan.get("schema") != SCHEMA or signature != digest({k: v for k, v in plan.items()
                                                            if k != "signature"}):
        raise ValueError("Batch plan signature changed")
    if plan.get("final_blind_opened") is not False:
        raise ValueError("Sealed final declaration changed")
    specs = _check_specs(plan["policy_specs"])
    if _source_binding(specs) != plan["source_sha256"]:
        raise ValueError("Batch source bytes changed after registration")
    if _sha(plan["preference_path"]) != plan["preference_sha256"]:
        raise ValueError("Research preference bytes changed")
    if _sha(plan["manifest_path"]) != plan["manifest_sha256"]:
        raise ValueError("Manifest bytes changed")
    manifest = _read(plan["manifest_path"])
    inventory_digest = _verify_manifest(manifest)
    if (inventory_digest != plan["physical_input_inventory_sha256"] or
            manifest["signature"] != plan["manifest_signature"] or
            manifest["simulator_hash"] != plan["simulator_hash"] or
            manifest["execution_contract_id"] != plan["execution_contract_id"]):
        raise ValueError("Physical/manifest binding changed")
    ids = plan["scenario_ids"]
    if (not ids or len(ids) != len(set(ids)) or len(ids) > 1500 or
            plan["registered_pair_count"] != len(ids) * len(specs)):
        raise ValueError("Registered pair denominator changed")
    if plan["phase"] not in PHASES:
        raise ValueError("Registered phase changed")
    for sid in ids:
        _check_scenario(manifest, sid, plan["phase"])
    for spec in specs:
        if spec.get("model_dir") and _model_binding(spec) != plan["model_bindings"][spec["policy_id"]]:
            raise ValueError("Model bytes changed after registration")
    return plan, manifest


def _factory(spec):
    kind = spec["kind"]
    if kind == "secure":
        return BaselinePolicy("secure_milp")
    if kind == "priority":
        return BaselinePolicy("priority_milp")
    if kind == "v6_feedback":
        return ResidualFeedbackPolicy(spec["theta"], spec["policy_id"],
                                      family=spec.get("family", "port_residual"))
    if kind == "v6_neural":
        from tools.policy_redesign.neural import load_neural_policy
        return load_neural_policy(spec["model_dir"], policy_id=spec["policy_id"])
    if kind == "current_rule":
        return build_current_policy({"policy_id": spec["policy_id"], "kind": "rule"})
    if kind == "strict_guard":
        return build_current_policy({"policy_id": spec["policy_id"], "kind": "gated",
                                     "model_dir": spec["model_dir"],
                                     "gate": {"error_multiplier": 1.0,
                                              "calibration_file": "gate_calibration.json"}})
    if kind == "neural_feedback":
        from tools.policy_search.neural import load_neural_policy
        return load_neural_policy(spec["model_dir"], policy_id=spec["policy_id"])
    raise ValueError("Unknown policy kind")


def _journal(path, **record):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(time_unix=time.time(), **record), ensure_ascii=False,
                                allow_nan=False) + "\n")


def _pair_path(out, scenario_id, policy_id):
    return Path(out) / "pairs" / digest([scenario_id, policy_id])


def _recover_interrupted(pair, journal, *, scenario_id, policy_id, phase,
                         plan_signature):
    """Give a crash-left attempt an explicit unknown receipt before retrying.

    An orphan may contain a completed or partial raw trajectory.  Its bytes
    remain untouched and are hashed into the receipt; there is no basis to
    infer the missing score or treat it as a completed episode.
    """
    for attempt in sorted(p for p in pair.glob("attempt_*") if p.is_dir()
                          and p.name[8:].isdigit()):
        if (attempt / "result.json").is_file():
            continue
        raw = attempt / "trajectory.json.gz"
        raw_sha = _sha(raw) if raw.is_file() else None
        error_path = attempt / "error.json"
        if not error_path.is_file():
            _new_json(error_path, {
                "status": "runner_interrupted", "scenario_id": scenario_id,
                "policy_id": policy_id, "phase": phase,
                "plan_signature": plan_signature,
                "message": "Attempt ended without a result receipt; outcome unknown",
                "raw_trajectory_exists": raw_sha is not None,
                "trajectory_sha256": raw_sha,
            })
        else:
            _read(error_path)
        _new_json(attempt / "result.json", {
            "schema": SCHEMA + ".pair", "scenario_id": scenario_id,
            "policy_id": policy_id, "phase": phase,
            "status": "runner_interrupted", "terminal": False,
            "plan_signature": plan_signature, "error_path": "error.json",
            "trajectory_path": raw.name if raw_sha else None,
            "trajectory_sha256": raw_sha,
        })
        _journal(journal, event="recovered", scenario_id=scenario_id,
                 policy_id=policy_id, attempt=attempt.name,
                 status="runner_interrupted", trajectory_sha256=raw_sha)


def _last(pair):
    attempts = sorted(p for p in pair.glob("attempt_*") if p.is_dir()
                      and p.name[8:].isdigit())
    for attempt in reversed(attempts):
        result_path = attempt / "result.json"
        if not result_path.is_file():
            continue
        row = _read(result_path)
        if row.get("trajectory_path"):
            raw = attempt / row["trajectory_path"]
            if not raw.is_file() or _sha(raw) != row["trajectory_sha256"]:
                raise ValueError("Existing raw trajectory bytes changed")
        return row, len(attempts)
    return None, len(attempts)


def _summary(plan, out, attempted):
    rows = []
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            row, _ = _last(_pair_path(out, sid, spec["policy_id"]))
            if row is not None:
                rows.append(row)
    counts = Counter(row["status"] for row in rows)
    complete = len(rows) == plan["registered_pair_count"] and all(row.get("terminal") for row in rows)
    matched = None
    if complete:
        matched = all(
            len({row["binding"]["initial_state_sha256"] for row in rows
                 if row["scenario_id"] == sid}) == 1
            and len({row["binding"]["exogenous_stream_id"] for row in rows
                     if row["scenario_id"] == sid}) == 1
            for sid in plan["scenario_ids"]
        )
    return {
        "schema": SCHEMA + ".summary", "plan_signature": plan["signature"],
        "registered_pair_count": plan["registered_pair_count"],
        "recorded_pair_count": len(rows),
        "terminal_pair_count": sum(bool(row.get("terminal")) for row in rows),
        "attempted_this_call": attempted, "status_counts": dict(counts),
        "unknown_primary_pair_count": sum(bool(row.get("terminal")) and
                                          not row["primary_score"]["full_horizon"]
                                          for row in rows),
        "matched_initial_state_and_stream": matched, "results": rows,
        "all_registered_denominator_includes_unknown": True,
    }


def run_batch(plan_path, out_dir, *, max_new_pairs=None):
    plan, manifest = verify_batch(plan_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    copy_path = out / "registered_plan.json"
    if copy_path.exists():
        if _read(copy_path) != plan:
            raise ValueError("Resume batch plan differs")
    else:
        if any(out.iterdir()):
            raise FileExistsError("Nonempty batch output has no registered plan")
        _new_json(copy_path, plan)
    if max_new_pairs is not None and (type(max_new_pairs) is not int or max_new_pairs < 0):
        raise ValueError("Invalid new pair limit")
    scenarios = {s["id"]: s for s in manifest["scenarios"]}
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    journal = out / "attempts.jsonl"
    attempted = 0
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            if max_new_pairs is not None and attempted >= max_new_pairs:
                break
            pair = _pair_path(out, sid, spec["policy_id"])
            pair.mkdir(parents=True, exist_ok=True)
            _recover_interrupted(pair, journal, scenario_id=sid,
                                 policy_id=spec["policy_id"], phase=plan["phase"],
                                 plan_signature=plan["signature"])
            previous, count = _last(pair)
            if previous and previous.get("terminal"):
                continue
            if count >= plan["max_runner_attempts_per_pair"]:
                continue
            attempt = pair / f"attempt_{count+1:04d}"
            attempt.mkdir(exist_ok=False)
            _journal(journal, event="started", scenario_id=sid, policy_id=spec["policy_id"],
                     attempt=attempt.name, phase=plan["phase"])
            attempted += 1
            begin = time.perf_counter()
            try:
                policy = _factory(copy.deepcopy(spec))
                if policy.policy_id != spec["policy_id"]:
                    raise ValueError("Policy factory identity differs")
                payload = run_policy_episode(env, scenarios[sid], policy,
                                             manifest["options"]["seed"], manifest,
                                             horizons=(15, 30, 60, 120))
                raw = attempt / "trajectory.json.gz"
                raw_sha = _save_gzip_new(raw, payload)
                primary = window_targets(payload, 60.0, 360.0, env)
                full = window_targets(payload, 0.0, 600.0, env)
                binding = _payload_binding(payload, scenarios[sid], manifest)
                counts = _command_counts(payload)
                counts["explicit_target_requests"] = sum(
                    "targets" in (decision.get("requested_command") or {})
                    for decision in payload["decisions"])
                row = {
                    "schema": SCHEMA + ".pair", "scenario_id": sid,
                    "policy_id": spec["policy_id"], "kind": spec["kind"],
                    "phase": plan["phase"], "plan_signature": plan["signature"],
                    "status": payload["summary"]["status"], "terminal": True,
                    "trajectory_path": raw.name, "trajectory_sha256": raw_sha,
                    "observed_until_minute": payload["summary"].get("observed_until_minute"),
                    "primary_score": primary, "full_observation": full,
                    "binding": binding, "command_counts": counts,
                    "end_to_end_seconds": time.perf_counter() - begin,
                }
                _new_json(attempt / "result.json", row)
                _journal(journal, event="finished", scenario_id=sid,
                         policy_id=spec["policy_id"], attempt=attempt.name,
                         status=row["status"], trajectory_sha256=raw_sha,
                         binding_passed=binding["passed"])
            except Exception as error:
                receipt = {
                    "status": "runner_exception", "error_type": type(error).__name__,
                    "message": str(error), "traceback": traceback.format_exc(),
                    "scenario_id": sid, "policy_id": spec["policy_id"],
                    "plan_signature": plan["signature"],
                    "raw_trajectory_exists": (attempt / "trajectory.json.gz").exists(),
                }
                _new_json(attempt / "error.json", receipt)
                _new_json(attempt / "result.json", {
                    "schema": SCHEMA + ".pair", "scenario_id": sid,
                    "policy_id": spec["policy_id"], "status": "runner_exception",
                    "terminal": False, "plan_signature": plan["signature"],
                    "error_path": "error.json",
                })
                _journal(journal, event="finished", scenario_id=sid,
                         policy_id=spec["policy_id"], attempt=attempt.name,
                         status="runner_exception", error_type=type(error).__name__)
        if max_new_pairs is not None and attempted >= max_new_pairs:
            break
    summary = _summary(plan, out, attempted)
    n = 1
    while (out / f"summary_{n:04d}.json").exists():
        n += 1
    _new_json(out / f"summary_{n:04d}.json", summary)
    return summary
