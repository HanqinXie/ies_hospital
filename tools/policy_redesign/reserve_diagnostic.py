"""Isolated, matched old-train diagnosis of an ordinary reserve feedback policy.

This is a new experiment namespace, not a change to the registered v6 runner.
It invokes the original environment/episode and the original 19-target scorer.
Saved raw, unknown outcomes, runner exceptions and source bindings are retained.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import gzip
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
from tools.control_improvement.ranking import TARGET_NAMES
from tools.integrated_learning.policies import BaselinePolicy
from tools.policy_redesign.closed_loop import _check_scenario, _verify_manifest
from tools.policy_search.preference import paired_metrics
from tools.policy_search.score_recheck import score_differences
from tools.reliability_first.closed_loop import (_command_counts, _payload_binding,
                                                   _save_gzip_new)


SCHEMA = "policy_redesign.reserve_diagnostic.v1"
MANIFEST = ROOT / "outputs/v5_policy_search_20261015_run01/new_data/global_manifest.json"
PREFERENCE = ROOT / "outputs/v6_policy_redesign_20261016_run01/design/RESEARCH_PREFERENCE_v1.json"
SCENARIOS = (
    ("control_case_aff69c7a098637665534", "EC0_grid350_delay30", 4),
    ("control_case_ce85057bf2097d63dc44", "GT50_grid350_delay30", 1),
    ("control_case_2e9cbda5558ba9339642", "normal_grid850_delay15", 2),
)
POLICY_SPECS = (
    {"policy_id": "secure_milp", "kind": "secure"},
    {"policy_id": "reserve_gt3", "kind": "reserve_feedback", "theta": [1.0]},
)
SOURCE_FILES = (
    "tools/policy_redesign/reserve_diagnostic.py",
    "tools/policy_redesign/reserve_policy.py",
    "tools/policy_redesign/closed_loop.py",
    "tools/control_improvement/episode.py",
    "tools/control_improvement/runtime.py",
    "tools/control_improvement/branches.py",
    "tools/integrated_learning/policies.py",
    "tools/reliability_first/closed_loop.py",
    "tools/policy_search/preference.py",
    "tools/policy_search/score_recheck.py",
    "src/hospital_ies/dataset_export.py",
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=lambda value:
                      (_ for _ in ()).throw(ValueError("Nonfinite JSON constant: " + value)))


def _new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _source_binding():
    return {name: _sha(ROOT / name) for name in SOURCE_FILES}


def _check_exact_scenes(manifest, scenario_ids):
    if tuple(scenario_ids) != tuple(item[0] for item in SCENARIOS):
        raise ValueError("Reserve diagnostic requires exact three preregistered old-train scenes")
    for sid, condition, pressure in SCENARIOS:
        scene = _check_scenario(manifest, sid, "train")
        if (scene.get("condition_id"), scene.get("pressure_point_index")) != (
                condition, pressure):
            raise ValueError("Reserve diagnostic scene condition/pressure changed")
        if not manifest["splits"][sid].get("fit_allowed", False):
            raise ValueError("Reserve diagnostic scene is not an approved train case")


def register_diagnostic(plan_path, *, manifest_path=MANIFEST,
                        preference_path=PREFERENCE, scenario_ids=None):
    """Freeze exactly three old-train roots and six comparisons, once."""
    path = Path(plan_path)
    if path.exists():
        raise FileExistsError("Reserve diagnostic plan already exists")
    ids = tuple(item[0] for item in SCENARIOS) if scenario_ids is None else tuple(scenario_ids)
    if ids != tuple(item[0] for item in SCENARIOS):
        raise ValueError("Reserve diagnostic requires exact three preregistered old-train scenes")
    manifest_path = Path(manifest_path).resolve()
    if _sha(manifest_path) != _sha(MANIFEST):
        raise ValueError("Reserve diagnostic requires the unchanged old-train manifest")
    manifest = _read(manifest_path)
    inventory_sha = _verify_manifest(manifest)
    _check_exact_scenes(manifest, ids)
    pref = Path(preference_path).resolve()
    if not pref.is_file():
        raise FileNotFoundError("Preregistered P0-first preference missing")
    sources = _source_binding()
    plan = {
        "schema": SCHEMA + ".plan", "phase": "train",
        "manifest_path": str(manifest_path), "manifest_sha256": _sha(manifest_path),
        "manifest_signature": manifest["signature"],
        "physical_input_inventory_sha256": inventory_sha,
        "simulator_hash": manifest["simulator_hash"],
        "execution_contract_id": manifest["execution_contract_id"],
        "preference_path": str(pref), "preference_sha256": _sha(pref),
        "source_sha256": sources, "scenario_ids": list(ids),
        "policy_specs": copy.deepcopy(list(POLICY_SPECS)),
        "score_windows": {"primary": [60, 420], "full": [0, 600]},
        "registered_pair_count": 6, "max_runner_attempts_per_pair": 2,
        "final_blind_opened": False,
    }
    plan["signature"] = digest(plan)
    _new_json(path, plan)
    return plan


def verify_diagnostic(plan_path):
    plan = _read(plan_path)
    if plan.get("schema") != SCHEMA + ".plan" or plan.get("signature") != digest(
            {key: value for key, value in plan.items() if key != "signature"}):
        raise ValueError("Reserve diagnostic plan signature changed")
    if (plan.get("phase") != "train" or plan.get("final_blind_opened") is not False
            or plan.get("registered_pair_count") != 6
            or plan.get("max_runner_attempts_per_pair") != 2
            or plan.get("policy_specs") != list(POLICY_SPECS)
            or plan.get("score_windows") != {"primary": [60, 420], "full": [0, 600]}):
        raise ValueError("Reserve diagnostic plan contract changed")
    if _source_binding() != plan.get("source_sha256"):
        raise ValueError("Reserve diagnostic source SHA changed after registration")
    if _sha(plan["preference_path"]) != plan.get("preference_sha256"):
        raise ValueError("Reserve diagnostic preference SHA changed")
    if (_sha(plan["manifest_path"]) != plan.get("manifest_sha256")
            or _sha(plan["manifest_path"]) != _sha(MANIFEST)):
        raise ValueError("Reserve diagnostic old-train manifest SHA changed")
    manifest = _read(plan["manifest_path"])
    inventory_sha = _verify_manifest(manifest)
    if (inventory_sha != plan.get("physical_input_inventory_sha256")
            or manifest["signature"] != plan.get("manifest_signature")
            or manifest["simulator_hash"] != plan.get("simulator_hash")
            or manifest["execution_contract_id"] != plan.get("execution_contract_id")):
        raise ValueError("Reserve diagnostic physical/manifest binding changed")
    _check_exact_scenes(manifest, plan.get("scenario_ids", []))
    return plan, manifest


def pair_id(scenario_id, policy_id):
    return digest([scenario_id, policy_id])


def _pair_dir(out, scenario_id, policy_id):
    return Path(out) / "pairs" / pair_id(scenario_id, policy_id)


def _journal(path, **row):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(time_unix=time.time(), **row),
                                ensure_ascii=False, allow_nan=False) + "\n")


def _factory(spec):
    if spec["kind"] == "secure":
        return BaselinePolicy("secure_milp")
    if spec["kind"] == "reserve_feedback":
        from tools.policy_redesign.reserve_policy import ReserveFeedbackPolicy

        return ReserveFeedbackPolicy(spec["theta"], spec["policy_id"])
    raise ValueError("Unknown reserve diagnostic policy kind")


def _null_score():
    return {"full_horizon": False, "targets": [None] * len(TARGET_NAMES)}


def _attempts(pair):
    return sorted(p for p in pair.glob("attempt_*") if p.is_dir()
                  and p.name[8:].isdigit())


def _latest(pair):
    attempts = _attempts(pair)
    for attempt in reversed(attempts):
        receipt = attempt / "result.json"
        if receipt.is_file():
            row = _read(receipt)
            if row.get("trajectory_path"):
                raw = attempt / row["trajectory_path"]
                if not raw.is_file() or _sha(raw) != row["trajectory_sha256"]:
                    raise ValueError("Existing reserve diagnostic raw SHA changed")
            return row, len(attempts)
    return None, len(attempts)


def _recover_interrupted(pair, journal, sid, pid, signature):
    for attempt in _attempts(pair):
        if (attempt / "result.json").exists():
            continue
        raw = attempt / "trajectory.json.gz"
        raw_sha = _sha(raw) if raw.is_file() else None
        if not (attempt / "error.json").exists():
            _new_json(attempt / "error.json", {
                "status": "runner_interrupted", "scenario_id": sid,
                "policy_id": pid, "plan_signature": signature,
                "message": "Attempt has no result receipt; outcome remains unknown",
                "trajectory_sha256": raw_sha,
            })
        _new_json(attempt / "result.json", {
            "schema": SCHEMA + ".pair", "scenario_id": sid,
            "policy_id": pid, "plan_signature": signature,
            "status": "runner_interrupted", "terminal": False,
            "trajectory_path": raw.name if raw_sha else None,
            "trajectory_sha256": raw_sha, "error_path": "error.json",
            "primary_score": _null_score(), "full_observation": _null_score(),
        })
        _journal(journal, event="recovered", scenario_id=sid, policy_id=pid,
                 attempt=attempt.name, status="runner_interrupted",
                 trajectory_sha256=raw_sha)


def _summary(plan, out, attempted):
    rows = []
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            row, _ = _latest(_pair_dir(out, sid, spec["policy_id"]))
            if row is not None:
                rows.append(row)
    complete = len(rows) == plan["registered_pair_count"] and all(
        row.get("terminal") is True and row.get("binding", {}).get("passed") is True
        for row in rows)
    matched = None
    if complete:
        matched = all(
            len({row["binding"]["initial_state_sha256"] for row in rows
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
        "matched_initial_state_and_stream": matched,
        "all_registered_denominator_includes_unknown": True,
        "results": rows,
    }


def run_diagnostic(plan_path, out_dir, *, max_new_pairs=None):
    """Execute or resume the six registered full episodes without changing physics."""
    plan, manifest = verify_diagnostic(plan_path)
    if max_new_pairs is not None and (type(max_new_pairs) is not int or max_new_pairs < 0):
        raise ValueError("Invalid new pair limit")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    copy_plan = out / "registered_plan.json"
    if copy_plan.exists():
        if _read(copy_plan) != plan:
            raise ValueError("Resume reserve plan differs")
    else:
        if any(out.iterdir()):
            raise FileExistsError("Nonempty reserve output has no registered plan")
        _new_json(copy_plan, plan)
    journal = out / "attempts.jsonl"
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    attempted = 0
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            if max_new_pairs is not None and attempted >= max_new_pairs:
                break
            pid = spec["policy_id"]
            pair = _pair_dir(out, sid, pid)
            pair.mkdir(parents=True, exist_ok=True)
            _recover_interrupted(pair, journal, sid, pid, plan["signature"])
            previous, count = _latest(pair)
            if previous and previous.get("terminal") is True:
                continue
            if count >= plan["max_runner_attempts_per_pair"]:
                continue
            attempt = pair / f"attempt_{count + 1:04d}"
            attempt.mkdir(exist_ok=False)
            _journal(journal, event="started", scenario_id=sid, policy_id=pid,
                     attempt=attempt.name, phase="train")
            attempted += 1
            began = time.perf_counter()
            try:
                policy = _factory(copy.deepcopy(spec))
                if policy.policy_id != pid:
                    raise ValueError("Reserve policy factory identity differs")
                payload = run_policy_episode(env, scenes[sid], policy,
                                             manifest["options"]["seed"], manifest,
                                             horizons=(15, 30, 60, 120))
                raw = attempt / "trajectory.json.gz"
                raw_sha = _save_gzip_new(raw, payload)
                primary = window_targets(payload, 60.0, 360.0, env)
                full = window_targets(payload, 0.0, 600.0, env)
                binding = _payload_binding(payload, scenes[sid], manifest)
                if not binding["passed"]:
                    raise ValueError("Reserve raw/manifest binding failed")
                counts = _command_counts(payload)
                counts["explicit_target_requests"] = sum(
                    "targets" in (decision.get("requested_command") or {})
                    for decision in payload.get("decisions", []))
                row = {
                    "schema": SCHEMA + ".pair", "scenario_id": sid,
                    "policy_id": pid, "kind": spec["kind"], "phase": "train",
                    "plan_signature": plan["signature"],
                    "status": payload["summary"]["status"], "terminal": True,
                    "trajectory_path": raw.name, "trajectory_sha256": raw_sha,
                    "observed_until_minute": payload["summary"].get("observed_until_minute"),
                    "primary_score": primary, "full_observation": full,
                    "binding": binding, "command_counts": counts,
                    "end_to_end_seconds": time.perf_counter() - began,
                }
                if row["status"] != "completed":
                    _new_json(attempt / "error.json", {
                        "status": row["status"], "scenario_id": sid, "policy_id": pid,
                        "plan_signature": plan["signature"],
                        "trajectory_sha256": raw_sha,
                        "raw_error": payload.get("error"),
                    })
                    row["error_path"] = "error.json"
                _new_json(attempt / "result.json", row)
                _journal(journal, event="finished", scenario_id=sid, policy_id=pid,
                         attempt=attempt.name, status=row["status"],
                         trajectory_sha256=raw_sha, binding_passed=True)
            except Exception as error:
                raw = attempt / "trajectory.json.gz"
                raw_sha = _sha(raw) if raw.is_file() else None
                _new_json(attempt / "error.json", {
                    "status": "runner_exception", "error_type": type(error).__name__,
                    "message": str(error), "traceback": traceback.format_exc(),
                    "scenario_id": sid, "policy_id": pid,
                    "plan_signature": plan["signature"],
                    "trajectory_sha256": raw_sha,
                })
                _new_json(attempt / "result.json", {
                    "schema": SCHEMA + ".pair", "scenario_id": sid,
                    "policy_id": pid, "kind": spec["kind"], "phase": "train",
                    "status": "runner_exception", "terminal": False,
                    "plan_signature": plan["signature"], "error_path": "error.json",
                    "trajectory_path": raw.name if raw_sha else None,
                    "trajectory_sha256": raw_sha,
                    "primary_score": _null_score(),
                    "full_observation": _null_score(),
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


def audit_diagnostic(plan_path, out_dir, *, output_json=None):
    """Read-only SHA/score/binding/paired audit; unknown stays null."""
    plan, manifest = verify_diagnostic(plan_path)
    out = Path(out_dir)
    summaries = sorted(out.glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("Reserve diagnostic summary absent")
    summary = _read(summaries[-1])
    if summary.get("plan_signature") != plan["signature"]:
        raise ValueError("Reserve summary/plan signature differs")
    if _read(out / "registered_plan.json") != plan:
        raise ValueError("Reserve runner plan copy differs")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    metric_rows, ledger, errors = [], [], []
    checked = 0
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            pid = spec["policy_id"]
            pair = _pair_dir(out, sid, pid)
            attempts = _attempts(pair)
            row, _ = _latest(pair)
            item = {"scenario_id": sid, "policy_id": pid,
                    "attempt_count": len(attempts), "status": None if row is None else row["status"],
                    "terminal": None if row is None else row.get("terminal"),
                    "raw_sha256": None if row is None else row.get("trajectory_sha256")}
            ledger.append(item)
            if row is None:
                errors.append(f"{sid}/{pid}: missing result receipt")
                metric_rows.append({"scenario_id": sid, "policy_id": pid,
                                    "status": "missing", "primary_score": _null_score(),
                                    "full_observation": _null_score()})
                continue
            if (row.get("plan_signature") != plan["signature"]
                    or row.get("scenario_id") != sid or row.get("policy_id") != pid):
                errors.append(f"{sid}/{pid}: result/plan identity differs")
            if row.get("terminal") is not True:
                errors.append(f"{sid}/{pid}: outcome still nonterminal")
                metric_rows.append({"scenario_id": sid, "policy_id": pid,
                                    "status": row["status"], "primary_score": _null_score(),
                                    "full_observation": _null_score()})
                continue
            latest_attempt = attempts[-1]
            if row.get("trajectory_path") != "trajectory.json.gz":
                raise ValueError(f"{sid}/{pid}: terminal raw path is not fixed relative name")
            raw = latest_attempt / row["trajectory_path"]
            if not raw.is_file() or _sha(raw) != row.get("trajectory_sha256"):
                raise ValueError(f"{sid}/{pid}: raw SHA differs from result")
            with gzip.open(raw, "rt", encoding="utf-8") as stream:
                payload = json.load(stream, parse_constant=lambda value:
                                    (_ for _ in ()).throw(ValueError(value)))
            if (payload.get("identity", {}).get("policy_id") != pid
                    or payload.get("scenario", {}).get("id") != sid
                    or payload.get("summary", {}).get("status") != row["status"]):
                errors.append(f"{sid}/{pid}: raw/result identity or status differs")
            binding = _payload_binding(payload, scenes[sid], manifest)
            if not binding["passed"] or binding != row.get("binding"):
                errors.append(f"{sid}/{pid}: raw/manifest binding differs")
            primary = window_targets(payload, 60.0, 360.0, env)
            full = window_targets(payload, 0.0, 600.0, env)
            differences = (score_differences(row.get("primary_score"), primary, "primary_score")
                           + score_differences(row.get("full_observation"), full,
                                               "full_observation"))
            if differences:
                errors.extend(f"{sid}/{pid}: {message}" for message in differences)
            if row["status"] != "completed":
                error_path = latest_attempt / row.get("error_path", "")
                if not error_path.is_file() or _read(error_path).get(
                        "trajectory_sha256") != row["trajectory_sha256"]:
                    errors.append(f"{sid}/{pid}: terminal error receipt absent or unbound")
            metric_rows.append({"scenario_id": sid, "policy_id": pid,
                                "status": row["status"],
                                "primary_score": primary, "full_observation": full})
            item.update(binding_passed=binding["passed"], raw_path=str(raw),
                        primary_full_horizon=primary["full_horizon"],
                        full_observation_horizon=full["full_horizon"])
            checked += 1
    if summary.get("registered_pair_count") != 6 or summary.get(
            "recorded_pair_count") != len(summary.get("results", [])):
        errors.append("Reserve summary count differs")
    for row in summary.get("results", []):
        saved, _ = _latest(_pair_dir(out, row["scenario_id"], row["policy_id"]))
        if saved != row:
            errors.append("Reserve summary differs from latest pair receipt")
    matched = None
    if checked == 6:
        bindings = {(row["scenario_id"], row["policy_id"]): row["binding"]
                    for row in summary["results"]}
        matched = all(
            len({bindings[sid, spec["policy_id"]][field]
                 for spec in plan["policy_specs"]}) == 1
            for sid in plan["scenario_ids"]
            for field in ("initial_state_sha256", "exogenous_stream_id"))
        if not matched:
            errors.append("Reserve matched initial state or stream differs")
    primary = paired_metrics(metric_rows, "reserve_gt3", score_field="primary_score")
    full = paired_metrics(metric_rows, "reserve_gt3", score_field="full_observation")
    report = {
        "schema": SCHEMA + ".audit", "passed": not errors and checked == 6
        and matched is True, "errors": errors,
        "method": "Original window_targets recomputed from saved gzip raw; not independent physical validation",
        "plan_path": str(Path(plan_path).resolve()), "plan_sha256": _sha(plan_path),
        "plan_signature": plan["signature"],
        "summary_path": str(summaries[-1].resolve()),
        "summary_sha256": _sha(summaries[-1]),
        "registered_pair_count": 6, "recomputed_pair_count": checked,
        "unknown_primary_pair_ids": sorted(
            row["scenario_id"] + "/" + row["policy_id"] for row in metric_rows
            if not row["primary_score"]["full_horizon"]),
        "unknown_full_pair_ids": sorted(
            row["scenario_id"] + "/" + row["policy_id"] for row in metric_rows
            if not row["full_observation"]["full_horizon"]),
        "unknown_is_null_not_zero": True,
        "matched_initial_state_and_stream": matched,
        "pair_ledger": ledger, "paired_primary": primary, "paired_full": full,
        "final_blind_opened": False,
    }
    if output_json is not None:
        _new_json(output_json, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("register", "run", "audit"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--audit-json", type=Path)
    parser.add_argument("--max-new-pairs", type=int)
    args = parser.parse_args(argv)
    if args.operation == "register":
        result = register_diagnostic(args.plan)
    elif args.operation == "run":
        if args.results is None:
            parser.error("--results is required for run")
        result = run_diagnostic(args.plan, args.results, max_new_pairs=args.max_new_pairs)
    else:
        if args.results is None:
            parser.error("--results is required for audit")
        result = audit_diagnostic(args.plan, args.results, output_json=args.audit_json)
    print(json.dumps({key: result.get(key) for key in (
        "schema", "signature", "passed", "registered_pair_count",
        "terminal_pair_count", "recomputed_pair_count", "status_counts")
        if key in result}, ensure_ascii=False))
    return 0 if args.operation != "audit" or result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
