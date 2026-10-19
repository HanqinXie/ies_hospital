"""Immutable matched full-feedback runner for the separate regime-policy route.

The registered source map includes this runner, the regime policy and all
existing planner, scorer, simulator and physical-input dependencies. Attempts
are append-only; solver errors, interrupted raws and truncated outcomes remain
explicitly unknown, never synthetic zero outcomes.
"""
from __future__ import annotations

import argparse
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
from tools.policy_redesign import closed_loop as original_runner
from tools.policy_redesign.regime_policy import RegimeFeedbackPolicy
from tools.reliability_first.closed_loop import (_command_counts, _payload_binding,
                                                   _save_gzip_new)


SCHEMA = "policy_redesign.regime_matched_full_feedback.v1"
KINDS = {"secure", "priority", "regime_feedback"}
PHASES = original_runner.PHASES
ADDED_SOURCE_FILES = ("tools/policy_redesign/regime_policy.py",
                      "tools/policy_redesign/regime_closed_loop.py")


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _source_binding():
    names = set(original_runner.BASE_SOURCE_FILES) | set(ADDED_SOURCE_FILES)
    return {name: _sha(ROOT / name) for name in sorted(names)}


def _check_specs(policy_specs):
    specs = copy.deepcopy(list(policy_specs))
    if not specs or len(specs) > 80:
        raise ValueError("Regime configuration count outside v6 budget")
    ids = [spec.get("policy_id") for spec in specs]
    if len(ids) != len(set(ids)) or any(not isinstance(pid, str) or not pid for pid in ids):
        raise ValueError("Regime policy IDs must be distinct and nonempty")
    if {"policy_id": "secure_milp", "kind": "secure"} not in specs:
        raise ValueError("Exact original secure baseline required")
    for spec in specs:
        kind = spec.get("kind")
        if kind not in KINDS:
            raise ValueError("Unregistered regime policy kind")
        required = {"policy_id", "kind"}
        if kind == "secure" and spec["policy_id"] != "secure_milp":
            raise ValueError("Secure baseline policy ID differs")
        if kind == "priority" and spec["policy_id"] != "priority_milp":
            raise ValueError("Priority baseline policy ID differs")
        if kind == "regime_feedback":
            required.add("theta")
            RegimeFeedbackPolicy(spec.get("theta", []), spec["policy_id"])
        if set(spec) != required:
            raise ValueError("Regime policy specification fields differ")
    return specs


def register_regime_batch(manifest_path, plan_path, *, scenario_ids, policy_specs,
                          preference_path, phase):
    """Freeze one complete-policy comparison before evaluating any pair."""
    if phase not in PHASES:
        raise ValueError("Unknown regime comparison phase")
    mp, pp = Path(manifest_path).resolve(), Path(plan_path)
    if pp.exists():
        raise FileExistsError("Use a fresh immutable regime batch plan")
    manifest = _read(mp)
    inventory = original_runner._verify_manifest(manifest)
    ids = list(scenario_ids)
    if not ids or len(ids) != len(set(ids)) or len(ids) > 1500:
        raise ValueError("Missing, repeated or over-budget scenario set")
    for sid in ids:
        original_runner._check_scenario(manifest, sid, phase)
    specs = _check_specs(policy_specs)
    if len(ids) * len(specs) * 2 > 30000:
        raise ValueError("Regime batch exceeds global actual-attempt upper bound")
    pref = Path(preference_path).resolve()
    if not pref.is_file():
        raise FileNotFoundError("Preregistered research preference absent")
    plan = {
        "schema": SCHEMA, "manifest_path": str(mp), "manifest_sha256": _sha(mp),
        "manifest_signature": manifest["signature"],
        "physical_input_inventory_sha256": inventory,
        "simulator_hash": manifest["simulator_hash"],
        "execution_contract_id": manifest["execution_contract_id"],
        "preference_path": str(pref), "preference_sha256": _sha(pref),
        "phase": phase, "scenario_ids": ids, "policy_specs": specs,
        "source_sha256": _source_binding(),
        "score_windows": {"primary": [60, 420], "full": [0, 600]},
        "registered_pair_count": len(ids) * len(specs),
        "max_runner_attempts_per_pair": 2, "final_blind_opened": False,
    }
    plan["signature"] = digest(plan)
    _new_json(pp, plan)
    return plan


def verify_regime_batch(plan_path):
    plan = _read(plan_path)
    signature = plan.get("signature")
    if (plan.get("schema") != SCHEMA or
            signature != digest({key: value for key, value in plan.items()
                                 if key != "signature"})):
        raise ValueError("Regime batch plan signature changed")
    if plan.get("final_blind_opened") is not False:
        raise ValueError("Sealed final declaration changed")
    specs = _check_specs(plan["policy_specs"])
    if _source_binding() != plan.get("source_sha256"):
        raise ValueError("Regime policy/runner/current source bytes changed")
    if _sha(plan["preference_path"]) != plan["preference_sha256"]:
        raise ValueError("Research preference bytes changed")
    if _sha(plan["manifest_path"]) != plan["manifest_sha256"]:
        raise ValueError("Manifest bytes changed")
    manifest = _read(plan["manifest_path"])
    inventory = original_runner._verify_manifest(manifest)
    if (inventory != plan["physical_input_inventory_sha256"] or
            manifest["signature"] != plan["manifest_signature"] or
            manifest["simulator_hash"] != plan["simulator_hash"] or
            manifest["execution_contract_id"] != plan["execution_contract_id"]):
        raise ValueError("Physical/manifest binding changed")
    ids = plan["scenario_ids"]
    if (not ids or len(ids) != len(set(ids)) or len(ids) > 1500 or
            plan["registered_pair_count"] != len(ids) * len(specs) or
            plan["max_runner_attempts_per_pair"] != 2 or
            len(ids) * len(specs) * 2 > 30000):
        raise ValueError("Registered regime pair/attempt denominator changed")
    if (plan["phase"] not in PHASES or
            plan["score_windows"] != {"primary": [60, 420], "full": [0, 600]}):
        raise ValueError("Regime phase or original score windows changed")
    for sid in ids:
        original_runner._check_scenario(manifest, sid, plan["phase"])
    return plan, manifest


def _factory(spec):
    if spec["kind"] == "secure":
        return BaselinePolicy("secure_milp")
    if spec["kind"] == "priority":
        return BaselinePolicy("priority_milp")
    return RegimeFeedbackPolicy(spec["theta"], spec["policy_id"])


def _journal(path, **record):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time_unix": time.time(), **record},
                                ensure_ascii=False, allow_nan=False) + "\n")


def _pair_path(out, sid, pid):
    return Path(out) / "pairs" / digest([sid, pid])


def _attempts(pair):
    return sorted(path for path in pair.glob("attempt_*")
                  if path.is_dir() and path.name[8:].isdigit())


def _recover_interrupted(pair, journal, *, sid, pid, phase, signature):
    for attempt in _attempts(pair):
        if (attempt / "result.json").is_file():
            continue
        raw = attempt / "trajectory.json.gz"
        raw_sha = _sha(raw) if raw.is_file() else None
        error_path = attempt / "error.json"
        if not error_path.is_file():
            _new_json(error_path, {
                "status": "runner_interrupted", "scenario_id": sid,
                "policy_id": pid, "phase": phase, "plan_signature": signature,
                "message": "Attempt ended without a result receipt; outcome unknown",
                "trajectory_sha256": raw_sha,
            })
        else:
            _read(error_path)
        _new_json(attempt / "result.json", {
            "schema": SCHEMA + ".pair", "scenario_id": sid,
            "policy_id": pid, "phase": phase,
            "status": "runner_interrupted", "terminal": False,
            "plan_signature": signature, "error_path": "error.json",
            "trajectory_path": raw.name if raw_sha else None,
            "trajectory_sha256": raw_sha,
        })
        _journal(journal, event="recovered", scenario_id=sid, policy_id=pid,
                 attempt=attempt.name, status="runner_interrupted",
                 trajectory_sha256=raw_sha)


def _last(pair):
    attempts = _attempts(pair)
    for attempt in reversed(attempts):
        path = attempt / "result.json"
        if not path.is_file():
            continue
        row = _read(path)
        if row.get("trajectory_path"):
            if row["trajectory_path"] != "trajectory.json.gz":
                raise ValueError("Existing raw trajectory path changed")
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
    complete = (len(rows) == plan["registered_pair_count"]
                and all(row.get("terminal") is True for row in rows))
    matched = None
    if complete:
        matched = all(
            all(row.get("binding", {}).get("passed") is True for row in rows
                if row["scenario_id"] == sid)
            and len({row["binding"]["initial_state_sha256"] for row in rows
                     if row["scenario_id"] == sid}) == 1
            and len({row["binding"]["exogenous_stream_id"] for row in rows
                     if row["scenario_id"] == sid}) == 1
            for sid in plan["scenario_ids"])
    return {
        "schema": SCHEMA + ".summary", "plan_signature": plan["signature"],
        "registered_pair_count": plan["registered_pair_count"],
        "recorded_pair_count": len(rows),
        "terminal_pair_count": sum(row.get("terminal") is True for row in rows),
        "attempted_this_call": attempted,
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "unknown_primary_pair_count": sum(
            row.get("terminal") is True and not row["primary_score"]["full_horizon"]
            for row in rows),
        "matched_initial_state_and_stream": matched, "results": rows,
        "all_registered_denominator_includes_unknown": True,
    }


def run_regime_batch(plan_path, out_dir, *, max_new_pairs=None):
    """Resume registered pairs without changing any prior attempt bytes."""
    plan, manifest = verify_regime_batch(plan_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    copy_path = out / "registered_plan.json"
    if copy_path.exists():
        if _read(copy_path) != plan:
            raise ValueError("Resume regime batch plan differs")
    else:
        if any(out.iterdir()):
            raise FileExistsError("Nonempty regime output has no registered plan")
        _new_json(copy_path, plan)
    if max_new_pairs is not None and (type(max_new_pairs) is not int or max_new_pairs < 0):
        raise ValueError("Invalid new pair limit")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    journal = out / "attempts.jsonl"
    attempted = 0
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            if max_new_pairs is not None and attempted >= max_new_pairs:
                break
            pid = spec["policy_id"]
            pair = _pair_path(out, sid, pid)
            pair.mkdir(parents=True, exist_ok=True)
            _recover_interrupted(pair, journal, sid=sid, pid=pid,
                                 phase=plan["phase"], signature=plan["signature"])
            previous, count = _last(pair)
            if previous and previous.get("terminal") is True:
                continue
            if count >= plan["max_runner_attempts_per_pair"]:
                continue
            attempt = pair / f"attempt_{count + 1:04d}"
            attempt.mkdir(exist_ok=False)
            _journal(journal, event="started", scenario_id=sid, policy_id=pid,
                     attempt=attempt.name, phase=plan["phase"])
            attempted += 1
            begin = time.perf_counter()
            try:
                policy = _factory(copy.deepcopy(spec))
                if policy.policy_id != pid:
                    raise ValueError("Regime policy factory identity differs")
                payload = run_policy_episode(env, scenes[sid], policy,
                                             manifest["options"]["seed"], manifest,
                                             horizons=(15, 30, 60, 120))
                raw = attempt / "trajectory.json.gz"
                raw_sha = _save_gzip_new(raw, payload)
                primary = window_targets(payload, 60.0, 360.0, env)
                full = window_targets(payload, 0.0, 600.0, env)
                binding = _payload_binding(payload, scenes[sid], manifest)
                counts = _command_counts(payload)
                counts["explicit_target_requests"] = sum(
                    "targets" in (decision.get("requested_command") or {})
                    for decision in payload["decisions"])
                row = {
                    "schema": SCHEMA + ".pair", "scenario_id": sid,
                    "policy_id": pid, "kind": spec["kind"],
                    "phase": plan["phase"], "plan_signature": plan["signature"],
                    "status": payload["summary"]["status"], "terminal": True,
                    "trajectory_path": raw.name, "trajectory_sha256": raw_sha,
                    "observed_until_minute": payload["summary"].get("observed_until_minute"),
                    "primary_score": primary, "full_observation": full,
                    "binding": binding, "command_counts": counts,
                    "end_to_end_seconds": time.perf_counter() - begin,
                }
                _new_json(attempt / "result.json", row)
                _journal(journal, event="finished", scenario_id=sid, policy_id=pid,
                         attempt=attempt.name, status=row["status"],
                         trajectory_sha256=raw_sha, binding_passed=binding["passed"])
            except Exception as error:
                raw = attempt / "trajectory.json.gz"
                raw_sha = _sha(raw) if raw.is_file() else None
                _new_json(attempt / "error.json", {
                    "status": "runner_exception", "error_type": type(error).__name__,
                    "message": str(error), "traceback": traceback.format_exc(),
                    "scenario_id": sid, "policy_id": pid,
                    "plan_signature": plan["signature"],
                    "trajectory_sha256": raw_sha,
                    "raw_trajectory_exists": raw_sha is not None,
                })
                _new_json(attempt / "result.json", {
                    "schema": SCHEMA + ".pair", "scenario_id": sid,
                    "policy_id": pid, "kind": spec["kind"],
                    "phase": plan["phase"], "status": "runner_exception",
                    "terminal": False, "plan_signature": plan["signature"],
                    "error_path": "error.json",
                    "trajectory_path": raw.name if raw_sha else None,
                    "trajectory_sha256": raw_sha,
                })
                _journal(journal, event="finished", scenario_id=sid, policy_id=pid,
                         attempt=attempt.name, status="runner_exception",
                         error_type=type(error).__name__, trajectory_sha256=raw_sha)
        if max_new_pairs is not None and attempted >= max_new_pairs:
            break
    summary = _summary(plan, out, attempted)
    index = 1
    while (out / f"summary_{index:04d}.json").exists():
        index += 1
    _new_json(out / f"summary_{index:04d}.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    register = sub.add_parser("register")
    register.add_argument("--manifest", type=Path, required=True)
    register.add_argument("--plan", type=Path, required=True)
    register.add_argument("--scenario-ids", type=Path, required=True,
                          help="JSON list of registered scenario IDs")
    register.add_argument("--policy-specs", type=Path, required=True,
                          help="JSON list containing secure and regime feedback specs")
    register.add_argument("--preference", type=Path, required=True)
    register.add_argument("--phase", choices=sorted(PHASES), required=True)
    run = sub.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--max-new-pairs", type=int)
    args = parser.parse_args(argv)
    if args.operation == "register":
        value = register_regime_batch(
            args.manifest, args.plan, scenario_ids=_read(args.scenario_ids),
            policy_specs=_read(args.policy_specs), preference_path=args.preference,
            phase=args.phase)
        print(json.dumps({"plan_signature": value["signature"],
                          "registered_pair_count": value["registered_pair_count"]}))
    else:
        value = run_regime_batch(args.plan, args.out, max_new_pairs=args.max_new_pairs)
        print(json.dumps({"terminal_pair_count": value["terminal_pair_count"],
                          "registered_pair_count": value["registered_pair_count"],
                          "unknown_primary_pair_count": value["unknown_primary_pair_count"],
                          "status_counts": value["status_counts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
