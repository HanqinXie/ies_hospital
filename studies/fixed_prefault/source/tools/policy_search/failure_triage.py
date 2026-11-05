"""Read-only failure evidence from v5 policy batches and frozen v4 runs.

The exported ``model_constraint_infeasible`` label is not itself an IIS or an
independent proof. This tool records native solver status, numerical witnesses,
time, layer, original raw paths and hashes, and keeps unresolved causes unknown.
Only failing compressed trajectories are opened; successful raw traces stay shut.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCHES = ("search/generation_1", "search/generation_2",
                   "search/alternative_modes")
SCHEMA = "policy_search.failure_triage.v1"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _label(path: Path) -> str:
    path = path.resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _raw(path: Path, declared: str | None = None) -> tuple[dict, str]:
    if not path.is_file():
        raise FileNotFoundError(f"Registered failure raw absent: {path}")
    actual = _sha(path)
    if declared is not None and actual != declared:
        raise ValueError(f"Raw SHA mismatch: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream), actual


def _v5_pair_key(scenario_id: str, policy_id: str) -> str:
    encoded = json.dumps([scenario_id, policy_id], sort_keys=True,
                         ensure_ascii=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _v4_shard_key(scenario_id: str, policy_id: str) -> str:
    # Frozen p2b_experiment.runner.payload_path uses json.dumps defaults.
    return hashlib.sha256(json.dumps([scenario_id, policy_id]).encode()).hexdigest()


def _violation_rows(validation: dict | None) -> list[dict]:
    if not isinstance(validation, dict):
        return []
    rows = []
    for row in validation.get("rows", []):
        if not isinstance(row, dict):
            continue
        amount = row.get("violation")
        if isinstance(amount, (int, float)) and amount > 0:
            rows.append({"name": row.get("name"), "violation": amount,
                         "kind": row.get("kind"), "unit": row.get("unit")})
    return sorted(rows, key=lambda row: (-row["violation"], str(row["name"])))[:10]


def _solver_evidence(payload: dict) -> tuple[dict, str]:
    error = payload.get("error") or {}
    diagnostics = [x for x in payload.get("solver_errors", []) if isinstance(x, dict)]
    diagnostic = error.get("diagnostic") or (diagnostics[-1] if diagnostics else {})
    if not isinstance(diagnostic, dict):
        diagnostic = {}
    layers = diagnostic.get("solver_backend_attempts") or []
    failed = next((layer for layer in layers if layer.get("status") != 0),
                  layers[-1] if layers else None)
    rank = failed.get("rank") if failed else None
    if rank is None:
        match = re.search(r"rank=(\d+)", str(diagnostic.get("message") or error.get("message")))
        rank = int(match.group(1)) if match else None
    attempts = []
    witnessed = []
    for layer in layers:
        for attempt in layer.get("attempts", []):
            validation = attempt.get("validation")
            violations = _violation_rows(validation)
            witnessed.extend(violations)
            attempts.append({"rank": layer.get("rank"), "attempt": attempt.get("attempt"),
                             "status": attempt.get("status"),
                             "native_status": attempt.get("native_status"),
                             "accepted": attempt.get("accepted"),
                             "elapsed_seconds": attempt.get("elapsed_seconds"),
                             "error_type": attempt.get("error_type"),
                             "error": attempt.get("error"),
                             "max_original_violation":
                                 validation.get("max_original_violation") if isinstance(validation, dict) else None,
                             "max_lock_violation":
                                 validation.get("max_lock_violation") if isinstance(validation, dict) else None})
    errored_calls = [call for call in payload.get("solver_calls", [])
                     if isinstance(call, dict) and call.get("status") == "error"]
    failed_rank_attempts = [a for a in attempts if a["rank"] == rank]
    native_infeasible = (bool(failed_rank_attempts) and failed is not None and
                         failed.get("status") == 2 and
                         all(a["status"] == 2 and a["native_status"] == 3
                             for a in failed_rank_attempts))
    numeric_rejected = any(a["status"] == 0 and a["accepted"] is False and
                           a["max_original_violation"] is not None and
                           a["max_original_violation"] > 1e-7 for a in attempts)
    timeout = (bool(attempts) and all(a["status"] == 1 for a in attempts))
    if numeric_rejected:
        classification = "numerical_validation_rejection"
    elif native_infeasible:
        classification = "native_infeasible_on_finite_recoveries"
    elif timeout:
        classification = "solver_time_limit"
    elif error.get("classification") == "runner_exception":
        classification = "runner_flow_exception"
    else:
        classification = "unknown_solver_or_flow_cause"
    detail = {"time_minute": diagnostic.get("time_minute"),
              "phase": diagnostic.get("phase"),
              "category": diagnostic.get("category", error.get("underlying_category")),
              "error_type": diagnostic.get("error_type", error.get("type")),
              "message": diagnostic.get("message", error.get("message")),
              "failed_rank": rank,
              "failed_layer_status": failed.get("status") if failed else None,
              "offending_constraint": diagnostic.get("offending_constraint"),
              "witnessed_violation_rows": witnessed[:10],
              "backend_attempts": attempts,
              "errored_solver_calls": [{"time_minute": call.get("time_minute"),
                                         "phase": call.get("phase"),
                                         "elapsed_seconds": call.get("elapsed_seconds"),
                                         "message": call.get("message")}
                                        for call in errored_calls]}
    # Solver native INFEASIBLE is strong status evidence, but no IIS, algebraic
    # contradiction certificate or independent model proof exists in this raw.
    return detail, classification


def _batch(v5: Path, relative: str) -> dict:
    folder = v5 / relative
    plan_path = folder / "registered_plan.json"
    if not plan_path.is_file():
        raise FileNotFoundError(plan_path)
    plan = _read(plan_path)
    if plan.get("schema") != "policy_search.matched_full_feedback.v1":
        raise ValueError(f"Unsupported v5 plan: {plan_path}")
    expected = len(plan["scenario_ids"]) * len(plan["policy_specs"])
    if expected != plan["registered_pair_count"]:
        raise ValueError("Registered v5 pair denominator changed")
    statuses = Counter()
    failures = []
    nonterminal = []
    terminal = 0
    attempts_total = 0
    for sid in plan["scenario_ids"]:
        for spec in plan["policy_specs"]:
            pid = spec["policy_id"]
            pair = folder / "results/pairs" / _v5_pair_key(sid, pid)
            attempts = sorted(path for path in pair.glob("attempt_*") if path.is_dir())
            attempts_total += len(attempts)
            latest = None
            for attempt in attempts:
                result_path = attempt / "result.json"
                result = _read(result_path) if result_path.is_file() else None
                if result and (result.get("scenario_id") != sid or result.get("policy_id") != pid or
                               result.get("plan_signature") != plan["signature"]):
                    raise ValueError(f"V5 pair result identity mismatch: {result_path}")
                if not result or not result.get("terminal"):
                    error_path = attempt / "error.json"
                    nonterminal.append({"scenario_id": sid, "policy_id": pid,
                                        "attempt": attempt.name,
                                        "result_path": _label(result_path) if result else None,
                                        "status": (result or {}).get("status", "attempt_started_no_result"),
                                        "error_path": _label(error_path) if error_path.is_file() else None,
                                        "error": _read(error_path) if error_path.is_file() else None})
                latest = (attempt, result)
            if latest is None:
                statuses["registered_not_attempted"] += 1
                continue
            attempt, result = latest
            status = result.get("status") if result else "attempt_started_no_result"
            statuses[status] += 1
            if not result or not result.get("terminal"):
                continue
            terminal += 1
            if status != "model_constraint_infeasible":
                continue
            raw_name = result.get("trajectory_path")
            if not raw_name or Path(raw_name).is_absolute() or ".." in Path(raw_name).parts:
                raise ValueError("Terminal model failure lacks local raw path")
            raw_path = attempt / raw_name
            payload, raw_sha = _raw(raw_path, result.get("trajectory_sha256"))
            if payload.get("identity", {}).get("scenario_id") != sid or\
                    payload.get("identity", {}).get("policy_id") != pid or\
                    payload.get("summary", {}).get("status") != status:
                raise ValueError(f"V5 failing raw identity/status mismatch: {raw_path}")
            evidence, classification = _solver_evidence(payload)
            failures.append({"scenario_id": sid, "policy_id": pid,
                             "attempt": attempt.name, "status": status,
                             "observed_until_minute": result.get("observed_until_minute"),
                             "result_path": _label(attempt / "result.json"),
                             "result_sha256": _sha(attempt / "result.json"),
                             "raw_path": _label(raw_path), "raw_sha256": raw_sha,
                             "raw_sha256_verified": True,
                             "solver_error": evidence,
                             "classification": classification,
                             "independent_infeasibility_proof": False})
    return {"batch": relative, "plan_path": _label(plan_path),
            "plan_sha256": _sha(plan_path), "plan_signature": plan["signature"],
            "phase": plan["phase"], "registered_pair_count": expected,
            "terminal_pair_count": terminal, "actual_attempt_count": attempts_total,
            "status_counts": dict(statuses),
            "terminal_model_constraint_failures": failures,
            "nonterminal_attempts": nonterminal,
            "independent_infeasibility_proof_available": False}


def _v4_baseline(v4: Path) -> tuple[list[dict], dict]:
    manifest_path = v4 / "new_data/global_manifest.json"
    manifest = _read(manifest_path)
    owners = {sid: shard["id"] for shard in manifest["p2b_shards"]
              for sid in shard["scenario_ids"]}
    failures = []
    terminal = set()
    journals = []
    for journal in sorted((v4 / "new_data/shards").glob("shard_*/attempts.jsonl")):
        journals.append({"path": _label(journal), "sha256": _sha(journal)})
        for line in journal.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("event") not in ("finished", "reconciled_checkpoint",
                                          "adjudicated_interrupted_unknown"):
                continue
            sid, pid = event["scenario_id"], event["policy_id"]
            if owners.get(sid) != journal.parent.name or pid not in manifest["policies"] or\
                    event.get("manifest_signature") != manifest["signature"]:
                raise ValueError(f"V4 shard journal identity mismatch: {journal}")
            key = (sid, pid)
            if key in terminal:
                raise ValueError(f"Duplicate V4 baseline terminal receipt: {key}")
            terminal.add(key)
            if event.get("status") != "model_constraint_infeasible":
                continue
            path = journal.parent / "checkpoints" / (f"{_v4_shard_key(sid, pid)}.json.gz")
            sidecar = path.with_suffix(".sha256")
            if not sidecar.is_file():
                raise FileNotFoundError(sidecar)
            declared = sidecar.read_text(encoding="ascii").strip()
            wrapper, actual = _raw(path, declared)
            if (wrapper.get("scenario_id"), wrapper.get("policy_id"),
                wrapper.get("manifest_signature")) != (sid, pid, manifest["signature"]):
                raise ValueError(f"V4 baseline raw binding mismatch: {path}")
            payload = wrapper.get("payload") or {}
            if payload.get("summary", {}).get("status") != "model_constraint_infeasible":
                raise ValueError(f"V4 baseline raw status mismatch: {path}")
            evidence, classification = _solver_evidence(payload)
            failures.append({"scenario_id": sid, "policy_id": pid,
                             "shard": journal.parent.name,
                             "journal_path": _label(journal),
                             "raw_path": _label(path), "raw_sha256": actual,
                             "sidecar_path": _label(sidecar),
                             "raw_sha256_verified": True,
                             "solver_error": evidence,
                             "classification": classification,
                             "independent_infeasibility_proof": False})
    return failures, {"manifest_path": _label(manifest_path),
                      "manifest_sha256": _sha(manifest_path),
                      "registered_pair_count": len(manifest["scenarios"]) * len(manifest["policies"]),
                      "terminal_pair_count": len(terminal), "journal_files": journals}


def _v4_development(v4: Path) -> tuple[list[dict], dict]:
    out = v4 / "closed_loop/development60_v1_runs"
    summaries = sorted(out.glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError(f"V4 development summary absent: {out}")
    summary_path = summaries[-1]
    summary = _read(summary_path)
    failures = []
    for row in summary["results"]:
        if row.get("status") != "model_constraint_infeasible":
            continue
        sid, pid = row["scenario_id"], row["policy_id"]
        pair = out / "pairs" / _v5_pair_key(sid, pid)
        attempts = sorted(path for path in pair.glob("attempt_*") if path.is_dir())
        matches = []
        for attempt in attempts:
            result_path = attempt / "result.json"
            if result_path.is_file() and _read(result_path) == row:
                matches.append(attempt)
        if len(matches) != 1:
            raise ValueError(f"Cannot uniquely bind V4 development failure to raw attempt: {sid}/{pid}")
        attempt = matches[0]
        raw_name = row.get("trajectory_path")
        if not raw_name or Path(raw_name).is_absolute() or ".." in Path(raw_name).parts:
            raise ValueError("V4 development failure has nonlocal raw path")
        raw_path = attempt / raw_name
        payload, actual = _raw(raw_path, row.get("trajectory_sha256"))
        if payload.get("identity", {}).get("scenario_id") != sid or\
                payload.get("identity", {}).get("policy_id") != pid or\
                payload.get("summary", {}).get("status") != "model_constraint_infeasible":
            raise ValueError(f"V4 development raw identity/status mismatch: {raw_path}")
        evidence, classification = _solver_evidence(payload)
        failures.append({"scenario_id": sid, "policy_id": pid,
                         "attempt": attempt.name,
                         "result_path": _label(attempt / "result.json"),
                         "result_sha256": _sha(attempt / "result.json"),
                         "raw_path": _label(raw_path), "raw_sha256": actual,
                         "raw_sha256_verified": True,
                         "solver_error": evidence,
                         "classification": classification,
                         "independent_infeasibility_proof": False})
    return failures, {"summary_path": _label(summary_path),
                      "summary_sha256": _sha(summary_path),
                      "registered_pair_count": summary.get("registered_pair_count"),
                      "terminal_pair_count": summary.get("terminal_pair_count")}


def collect_failure_triage(v5_run: str | Path, v4_run: str | Path,
                           *, batches: tuple[str, ...] | None = None) -> dict:
    """Read registered v5 batches and only failing historical v4 raw cases."""
    v5, v4 = Path(v5_run).resolve(), Path(v4_run).resolve()
    if batches is None:
        batches = tuple(name for name in DEFAULT_BATCHES
                        if (v5 / name / "registered_plan.json").is_file())
    if not batches:
        raise ValueError("No registered v5 search batch selected")
    if len(set(batches)) != len(batches) or any(Path(name).is_absolute() or
                                                  ".." in Path(name).parts for name in batches):
        raise ValueError("Invalid or duplicate batch path")
    selected = [_batch(v5, name) for name in batches]
    baseline, baseline_binding = _v4_baseline(v4)
    development, dev_binding = _v4_development(v4)
    return {"schema": SCHEMA, "v5_batches": selected,
            "v4_historical": {"baseline_binding": baseline_binding,
                              "baseline_failures": baseline,
                              "development_binding": dev_binding,
                              "development_failures": development},
            "classification_limit": "Native solver status and raw validation only; no IIS or independent infeasibility proof",
            "old_26_case_proof_reused": False,
            "successful_raw_trajectories_opened": False}


def write_failure_triage(v5_run: str | Path, v4_run: str | Path,
                         output_path: str | Path, *, batches: tuple[str, ...] | None = None,
                         require_terminal: bool = True) -> dict:
    """Write compact evidence only after selected batches reach terminal status."""
    value = collect_failure_triage(v5_run, v4_run, batches=batches)
    if require_terminal and any(batch["terminal_pair_count"] != batch["registered_pair_count"]
                                for batch in value["v5_batches"]):
        raise ValueError("Selected v5 batch has nonterminal registered pairs")
    output = Path(output_path).resolve()
    output.relative_to(Path(v5_run).resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"path": _label(output), "sha256": _sha(output),
            "bytes": output.stat().st_size,
            "v5_failure_count": sum(len(batch["terminal_model_constraint_failures"])
                                    for batch in value["v5_batches"]),
            "v4_baseline_failure_count": len(value["v4_historical"]["baseline_failures"]),
            "v4_development_failure_count": len(value["v4_historical"]["development_failures"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("v5_run", type=Path)
    parser.add_argument("v4_run", type=Path)
    parser.add_argument("--batch", action="append", dest="batches")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_failure_triage(args.v5_run, args.v4_run, args.output,
                                          batches=tuple(args.batches) if args.batches else None),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
