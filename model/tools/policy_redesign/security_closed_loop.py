"""Immutable matched episodes for current-state GT-loading soft objectives.

This is a separate v6 branch. The only changed action field is an original
security objective coefficient in [0,10]; the unchanged MILP and execution
queue remain authoritative. All result attempts, raw and error receipts are
kept, including incomplete and unknown episodes.
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
from tools.policy_redesign import regime_closed_loop as runner_support
from tools.policy_redesign.security_policy import SecurityWeightPolicy
from tools.reliability_first.closed_loop import (_command_counts, _payload_binding,
                                                   _save_gzip_new)


SCHEMA = "policy_redesign.security_matched_full_feedback.v1"
PHASES = original_runner.PHASES
KINDS = {"secure", "priority", "security_feedback"}
ADDED_SOURCES = ("tools/policy_redesign/regime_policy.py",
                 "tools/policy_redesign/regime_closed_loop.py",
                 "tools/policy_redesign/security_policy.py",
                 "tools/policy_redesign/security_closed_loop.py")


def _sha(path):
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
    names = set(original_runner.BASE_SOURCE_FILES) | set(ADDED_SOURCES)
    return {name: _sha(ROOT / name) for name in sorted(names)}


def _check_specs(policy_specs):
    specs = copy.deepcopy(list(policy_specs))
    if not specs or len(specs) > 80:
        raise ValueError("Security policy configuration count outside v6 budget")
    ids = [spec.get("policy_id") for spec in specs]
    if len(ids) != len(set(ids)) or any(not isinstance(pid, str) or not pid for pid in ids):
        raise ValueError("Security policy IDs must be unique and nonempty")
    if {"policy_id": "secure_milp", "kind": "secure"} not in specs:
        raise ValueError("Exact secure=.35 baseline is required")
    for spec in specs:
        kind = spec.get("kind")
        if kind not in KINDS:
            raise ValueError("Unregistered security policy kind")
        fields = {"policy_id", "kind"}
        if kind == "secure" and spec["policy_id"] != "secure_milp":
            raise ValueError("Original secure ID differs")
        if kind == "priority" and spec["policy_id"] != "priority_milp":
            raise ValueError("Original priority ID differs")
        if kind == "security_feedback":
            fields.add("weights")
            SecurityWeightPolicy(spec.get("weights", []), spec["policy_id"])
        if set(spec) != fields:
            raise ValueError("Security policy specification fields differ")
    return specs


def register_security_batch(manifest_path, plan_path, *, scenario_ids, policy_specs,
                            preference_path, phase):
    if phase not in PHASES:
        raise ValueError("Unknown security comparison phase")
    mp, pp = Path(manifest_path).resolve(), Path(plan_path)
    if pp.exists():
        raise FileExistsError("Use a fresh immutable security batch plan")
    manifest = _read(mp)
    inventory = original_runner._verify_manifest(manifest)
    ids = list(scenario_ids)
    if not ids or len(ids) != len(set(ids)) or len(ids) > 1500:
        raise ValueError("Missing, repeated or over-budget scenario set")
    for sid in ids:
        original_runner._check_scenario(manifest, sid, phase)
    specs = _check_specs(policy_specs)
    if len(ids) * len(specs) * 2 > 30000:
        raise ValueError("Security batch exceeds actual-attempt upper bound")
    pref = Path(preference_path).resolve()
    if not pref.is_file():
        raise FileNotFoundError("Research preference absent")
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
        "max_runner_attempts_per_pair": 2,
        "final_blind_opened": False,
    }
    plan["signature"] = digest(plan)
    _new_json(pp, plan)
    return plan


def verify_security_batch(plan_path):
    plan = _read(plan_path)
    if (plan.get("schema") != SCHEMA or
            plan.get("signature") != digest({key: value for key, value in plan.items()
                                             if key != "signature"})):
        raise ValueError("Security plan signature differs")
    if plan.get("final_blind_opened") is not False:
        raise ValueError("Sealed final declaration changed")
    specs = _check_specs(plan["policy_specs"])
    if _source_binding() != plan.get("source_sha256"):
        raise ValueError("Security policy/runner/source bytes changed")
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
        raise ValueError("Security physical/manifest binding changed")
    ids = plan["scenario_ids"]
    if (not ids or len(ids) != len(set(ids)) or len(ids) > 1500 or
            plan["registered_pair_count"] != len(ids) * len(specs) or
            plan["max_runner_attempts_per_pair"] != 2 or
            len(ids) * len(specs) * 2 > 30000):
        raise ValueError("Security pair/attempt denominator changed")
    if (plan["phase"] not in PHASES or
            plan["score_windows"] != {"primary": [60, 420], "full": [0, 600]}):
        raise ValueError("Security comparison phase or score windows changed")
    for sid in ids:
        original_runner._check_scenario(manifest, sid, plan["phase"])
    return plan, manifest


def _factory(spec):
    if spec["kind"] == "secure":
        return BaselinePolicy("secure_milp")
    if spec["kind"] == "priority":
        return BaselinePolicy("priority_milp")
    return SecurityWeightPolicy(spec["weights"], spec["policy_id"])


def _recover_interrupted(pair, journal, *, sid, pid, phase, signature):
    for attempt in runner_support._attempts(pair):
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
        runner_support._journal(journal, event="recovered", scenario_id=sid,
                                policy_id=pid, attempt=attempt.name,
                                status="runner_interrupted", trajectory_sha256=raw_sha)


def _latest(pair, *, sid, pid, signature):
    row, count = runner_support._last(pair)
    if row is not None and (row.get("scenario_id") != sid or row.get("policy_id") != pid
                            or row.get("plan_signature") != signature):
        raise ValueError("Existing security result identity/plan changed")
    return row, count


def _summary(plan, out, attempted):
    rows = []
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            row, _ = _latest(runner_support._pair_path(out, sid, spec["policy_id"]),
                             sid=sid, pid=spec["policy_id"], signature=plan["signature"])
            if row is not None:
                rows.append(row)
    complete = (len(rows) == plan["registered_pair_count"] and
                all(row.get("terminal") is True for row in rows))
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


def run_security_batch(plan_path, out_dir, *, max_new_pairs=None):
    plan, manifest = verify_security_batch(plan_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    copy_path = out / "registered_plan.json"
    if copy_path.exists():
        if _read(copy_path) != plan:
            raise ValueError("Security runner plan copy changed")
    else:
        if any(out.iterdir()):
            raise FileExistsError("Nonempty security output has no registered plan")
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
            pair = runner_support._pair_path(out, sid, pid)
            pair.mkdir(parents=True, exist_ok=True)
            _recover_interrupted(pair, journal, sid=sid, pid=pid,
                                 phase=plan["phase"], signature=plan["signature"])
            previous, count = _latest(pair, sid=sid, pid=pid,
                                      signature=plan["signature"])
            if previous and previous.get("terminal") is True:
                continue
            if count >= plan["max_runner_attempts_per_pair"]:
                continue
            attempt = pair / f"attempt_{count + 1:04d}"
            attempt.mkdir(exist_ok=False)
            runner_support._journal(journal, event="started", scenario_id=sid,
                                    policy_id=pid, attempt=attempt.name,
                                    phase=plan["phase"])
            attempted += 1
            begin = time.perf_counter()
            try:
                policy = _factory(copy.deepcopy(spec))
                if policy.policy_id != pid:
                    raise ValueError("Security factory identity differs")
                payload = run_policy_episode(env, scenes[sid], policy,
                                             manifest["options"]["seed"], manifest,
                                             horizons=(15, 30, 60, 120))
                raw = attempt / "trajectory.json.gz"
                raw_sha = _save_gzip_new(raw, payload)
                primary = window_targets(payload, 60.0, 360.0, env)
                full = window_targets(payload, 0.0, 600.0, env)
                binding = _payload_binding(payload, scenes[sid], manifest)
                counts = _command_counts(payload)
                counts["security_weight_requests"] = sum(
                    "gt_loading_penalty_USD_per_kWh" in
                    (decision.get("requested_command") or {}).get("security", {})
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
                runner_support._journal(journal, event="finished", scenario_id=sid,
                                        policy_id=pid, attempt=attempt.name,
                                        status=row["status"], trajectory_sha256=raw_sha,
                                        binding_passed=binding["passed"])
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
                runner_support._journal(journal, event="finished", scenario_id=sid,
                                        policy_id=pid, attempt=attempt.name,
                                        status="runner_exception",
                                        error_type=type(error).__name__,
                                        trajectory_sha256=raw_sha)
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
    register.add_argument("--scenario-ids", type=Path, required=True)
    register.add_argument("--policy-specs", type=Path, required=True)
    register.add_argument("--preference", type=Path, required=True)
    register.add_argument("--phase", choices=sorted(PHASES), required=True)
    run = sub.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--max-new-pairs", type=int)
    args = parser.parse_args(argv)
    if args.operation == "register":
        value = register_security_batch(
            args.manifest, args.plan, scenario_ids=_read(args.scenario_ids),
            policy_specs=_read(args.policy_specs), preference_path=args.preference,
            phase=args.phase)
        print(json.dumps({"plan_signature": value["signature"],
                          "registered_pair_count": value["registered_pair_count"]}))
    else:
        value = run_security_batch(args.plan, args.out, max_new_pairs=args.max_new_pairs)
        print(json.dumps({"terminal_pair_count": value["terminal_pair_count"],
                          "registered_pair_count": value["registered_pair_count"],
                          "unknown_primary_pair_count": value["unknown_primary_pair_count"],
                          "status_counts": value["status_counts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
