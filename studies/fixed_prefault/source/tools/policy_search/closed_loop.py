"""Registered, resumable full-feedback policy comparisons on unchanged IES.

Every registered scenario/policy pair has its own attempted raw trajectory.
Physical truncation is a terminal *unknown*, whereas a runner exception keeps
an error receipt and may be retried once. No result is replaced by zeros.
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
from hospital_ies.dataset_manifest import digest
from tools.control_improvement.branches import window_targets
from tools.control_improvement.episode import run_policy_episode
from tools.integrated_learning.policies import BaselinePolicy
from tools.reliability_first.closed_loop import (_save_gzip_new, _payload_binding,
                                                  _command_counts)
from tools.reliability_first.selection import build_current_policy
from tools.policy_search.policy import FeedbackPolicy, parameter_bounds


SCHEMA = "policy_search.matched_full_feedback.v1"
KINDS = {"secure", "feedback", "strict_guard", "current_rule", "neural_feedback"}
SOURCE_FILES = (
    "tools/policy_search/closed_loop.py",
    "tools/policy_search/policy.py",
    "tools/control_improvement/runtime.py",
    "tools/control_improvement/episode.py",
    "tools/control_improvement/branches.py",
    "tools/reliability_first/policy.py",
    "tools/reliability_first/selection.py",
    "tools/reliability_first/closed_loop.py",
    "tools/integrated_learning/policies.py",
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _source_binding():
    return {name: _sha(ROOT / name) for name in SOURCE_FILES}


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


def _check_scenario(manifest, scenario_id, phase):
    scene = next((s for s in manifest["scenarios"] if s["id"] == scenario_id), None)
    if scene is None:
        raise ValueError("Scenario missing from registered manifest")
    split = manifest["splits"][scenario_id]
    if scene.get("is_final_blind") or split.get("is_final_blind"):
        raise ValueError("sealed final scenario is inaccessible")
    if scene["experiment_role"] != phase or split["experiment_role"] != phase:
        raise ValueError("Scenario role differs from registered comparison phase")
    return scene


def _check_specs(policy_specs):
    specs = copy.deepcopy(list(policy_specs))
    if not specs or len(specs) > 80:
        raise ValueError("Policy configuration count outside v5 budget")
    ids = [s.get("policy_id") for s in specs]
    if len(ids) != len(set(ids)) or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError("Policy IDs must be unique and nonempty")
    if not any(s == {"policy_id": "secure_milp", "kind": "secure"} for s in specs):
        raise ValueError("An exact original secure baseline must be registered")
    for s in specs:
        if s.get("kind") not in KINDS:
            raise ValueError("Unknown registered v5 policy kind")
        kind = s["kind"]
        allowed = {"policy_id", "kind"}
        if kind == "feedback":
            allowed |= {"family", "theta"}
            FeedbackPolicy(s.get("theta", []), s["policy_id"], family=s.get("family", "blend"))
        elif kind in ("strict_guard", "neural_feedback"):
            allowed |= {"model_dir"}
            if not s.get("model_dir"):
                raise ValueError("Learned comparison needs model_dir")
        if set(s) - allowed:
            raise ValueError("Unregistered policy specification field")
    return specs


def register_batch(manifest_path, plan_path, *, scenario_ids, policy_specs,
                   preference_path, phase):
    """Freeze data, preference, source and policy vector before outcomes."""
    if phase not in ("train", "selection", "calibration", "development", "confirmation"):
        raise ValueError("Unknown v5 comparison phase")
    mp = Path(manifest_path).resolve()
    pp = Path(plan_path)
    if pp.exists():
        raise FileExistsError("Use a fresh immutable v5 batch plan")
    manifest = _read(mp)
    body = copy.deepcopy(manifest)
    signature = body.pop("signature", None)
    if signature != digest(body):
        raise ValueError("Manifest signature differs from registered bytes")
    ids = list(scenario_ids)
    if not ids or len(ids) != len(set(ids)) or len(ids) > 1500:
        raise ValueError("Missing, repeated or over-budget scenario set")
    for sid in ids:
        _check_scenario(manifest, sid, phase)
    specs = _check_specs(policy_specs)
    pref = Path(preference_path).resolve()
    if not pref.is_file():
        raise FileNotFoundError("Preregistered research preference absent")
    plan = {"schema": SCHEMA, "manifest_path": str(mp), "manifest_sha256": _sha(mp),
            "manifest_signature": signature, "simulator_hash": manifest["simulator_hash"],
            "execution_contract_id": manifest["execution_contract_id"],
            "preference_path": str(pref), "preference_sha256": _sha(pref),
            "phase": phase, "scenario_ids": ids, "policy_specs": specs,
            "model_bindings": {s["policy_id"]: _model_binding(s) for s in specs if s.get("model_dir")},
            "source_sha256": _source_binding(), "score_windows": {"primary": [60, 420], "full": [0, 600]},
            "registered_pair_count": len(ids) * len(specs), "max_runner_attempts_per_pair": 2,
            "final_blind_opened": False}
    plan["signature"] = digest(plan)
    _new_json(pp, plan)
    return plan


def verify_batch(plan_path):
    plan = _read(plan_path)
    body = copy.deepcopy(plan)
    signature = body.pop("signature", None)
    if plan.get("schema") != SCHEMA or signature != digest(body):
        raise ValueError("Batch plan signature changed")
    if plan["source_sha256"] != _source_binding():
        raise ValueError("Batch source changed after registration")
    if _sha(plan["preference_path"]) != plan["preference_sha256"]:
        raise ValueError("Research preference changed after registration")
    if _sha(plan["manifest_path"]) != plan["manifest_sha256"]:
        raise ValueError("Manifest bytes changed after registration")
    manifest = _read(plan["manifest_path"])
    body = copy.deepcopy(manifest)
    if digest({k: v for k, v in body.items() if k != "signature"}) != manifest.get("signature"):
        raise ValueError("Manifest signature changed")
    if (manifest["signature"] != plan["manifest_signature"] or
            manifest["simulator_hash"] != plan["simulator_hash"] or
            manifest["execution_contract_id"] != plan["execution_contract_id"]):
        raise ValueError("Physical source binding changed")
    for sid in plan["scenario_ids"]:
        _check_scenario(manifest, sid, plan["phase"])
    _check_specs(plan["policy_specs"])
    for spec in plan["policy_specs"]:
        if spec.get("model_dir") and _model_binding(spec) != plan["model_bindings"][spec["policy_id"]]:
            raise ValueError("Model bytes changed after registration")
    return plan, manifest


def _factory(spec):
    kind = spec["kind"]
    if kind == "secure":
        return BaselinePolicy("secure_milp")
    if kind == "feedback":
        return FeedbackPolicy(spec["theta"], spec["policy_id"], family=spec.get("family", "blend"))
    if kind in ("strict_guard", "current_rule"):
        old = ({"policy_id": spec["policy_id"], "kind": "gated",
                "model_dir": spec["model_dir"],
                "gate": {"error_multiplier": 1.0, "calibration_file": "gate_calibration.json"}}
               if kind == "strict_guard" else
               {"policy_id": spec["policy_id"], "kind": "rule"})
        return build_current_policy(old)
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


def _last(pair):
    attempts = sorted(p for p in pair.glob("attempt_*" ) if p.is_dir() and p.name[8:].isdigit())
    for p in reversed(attempts):
        f = p / "result.json"
        if not f.is_file():
            continue
        row = _read(f)
        if row.get("trajectory_path"):
            raw = p / row["trajectory_path"]
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
    counts = Counter(r["status"] for r in rows)
    complete = len(rows) == plan["registered_pair_count"] and all(r.get("terminal") for r in rows)
    matched = None
    if complete:
        matched = all(len({r["binding"]["initial_state_sha256"] for r in rows if r["scenario_id"] == sid}) == 1
                      and len({r["binding"]["exogenous_stream_id"] for r in rows if r["scenario_id"] == sid}) == 1
                      for sid in plan["scenario_ids"])
    return {"schema": SCHEMA + ".summary", "plan_signature": plan["signature"],
            "registered_pair_count": plan["registered_pair_count"], "recorded_pair_count": len(rows),
            "terminal_pair_count": sum(bool(r.get("terminal")) for r in rows),
            "attempted_this_call": attempted, "status_counts": dict(counts),
            "unknown_primary_pair_count": sum(r.get("terminal") and
                                               not r["primary_score"]["full_horizon"] for r in rows),
            "matched_initial_state_and_stream": matched, "results": rows,
            "all_registered_denominator_includes_unknown": True}


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
    if max_new_pairs is not None and (isinstance(max_new_pairs, bool) or int(max_new_pairs) < 0):
        raise ValueError("Invalid new pair limit")
    scenarios = {s["id"]: s for s in manifest["scenarios"]}
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    journal = out / "attempts.jsonl"
    attempted = 0
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            if max_new_pairs is not None and attempted >= int(max_new_pairs):
                break
            pair = _pair_path(out, sid, spec["policy_id"])
            pair.mkdir(parents=True, exist_ok=True)
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
                payload = run_policy_episode(env, scenarios[sid], policy, manifest["options"]["seed"],
                                             manifest, horizons=(15, 30, 60, 120))
                raw = attempt / "trajectory.json.gz"
                raw_sha = _save_gzip_new(raw, payload)
                primary = window_targets(payload, 60.0, 360.0, env)
                full = window_targets(payload, 0.0, 600.0, env)
                binding = _payload_binding(payload, scenarios[sid], manifest)
                counts = _command_counts(payload)
                counts["explicit_target_requests"] = sum("targets" in (d.get("requested_command") or {})
                                                          for d in payload["decisions"])
                row = {"schema": SCHEMA + ".pair", "scenario_id": sid,
                       "policy_id": spec["policy_id"], "kind": spec["kind"],
                       "phase": plan["phase"], "plan_signature": plan["signature"],
                       "status": payload["summary"]["status"], "terminal": True,
                       "trajectory_path": raw.name, "trajectory_sha256": raw_sha,
                       "observed_until_minute": payload["summary"].get("observed_until_minute"),
                       "primary_score": primary, "full_observation": full,
                       "binding": binding, "command_counts": counts,
                       "end_to_end_seconds": time.perf_counter() - begin}
                _new_json(attempt / "result.json", row)
                _journal(journal, event="finished", scenario_id=sid, policy_id=spec["policy_id"],
                         attempt=attempt.name, status=row["status"], trajectory_sha256=raw_sha,
                         binding_passed=binding["passed"])
            except Exception as error:
                receipt = {"status": "runner_exception", "error_type": type(error).__name__,
                           "message": str(error), "traceback": traceback.format_exc(),
                           "scenario_id": sid, "policy_id": spec["policy_id"],
                           "plan_signature": plan["signature"],
                           "raw_trajectory_exists": (attempt / "trajectory.json.gz").exists()}
                _new_json(attempt / "error.json", receipt)
                _new_json(attempt / "result.json", {"schema": SCHEMA + ".pair",
                                                   "scenario_id": sid, "policy_id": spec["policy_id"],
                                                   "status": "runner_exception", "terminal": False,
                                                   "plan_signature": plan["signature"],
                                                   "error_path": "error.json"})
                _journal(journal, event="finished", scenario_id=sid, policy_id=spec["policy_id"],
                         attempt=attempt.name, status="runner_exception", error_type=type(error).__name__)
        if max_new_pairs is not None and attempted >= int(max_new_pairs):
            break
    summary = _summary(plan, out, attempted)
    n = 1
    while (out / f"summary_{n:04d}.json").exists():
        n += 1
    _new_json(out / f"summary_{n:04d}.json", summary)
    return summary
