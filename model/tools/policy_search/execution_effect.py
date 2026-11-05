"""Align matched raw segments to diagnose actual execution and P0 worsening.

Only score-complete candidate/secure pairs with an observed positive P0 target
delta have both raw trajectories opened. Requests and generated commands are
not treated as execution. Unknown or unmatched pairs remain in the denominator.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SIX_PORTS = ("gt_1", "gt_2", "gt_3", "grid_import", "diesel", "absorption_cooling")
P0_TARGET_INDICES = (0, 1, 2, 3, 4, 14)
P0_TARGET_NAMES = ("P0_failure", "P0_failure_minutes", "P0_e_ENS_kWh",
                   "P0_h_ENS_kWh", "P0_c_ENS_kWh", "P0_max_temperature_C")
DISPATCH_TOLERANCE_KW = 1e-7
ENS_RATE_TOLERANCE_KW = 1e-7


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _label(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _pair_key(sid: str, pid: str) -> str:
    return hashlib.sha256(json.dumps([sid, pid], sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _six(action: dict) -> list[float]:
    values = action["gt_electric_kW"]
    if len(values) != 3:
        raise ValueError("Raw action has wrong GT port count")
    return [float(x) for x in values] + [float(action[name]) for name in
                                          ("grid_import_kW", "diesel_kW",
                                           "absorption_cooling_kW")]


def _payload(batch: Path, sid: str, pid: str, plan_signature: str) -> tuple[dict | None, dict | None]:
    pair = batch / "results/pairs" / _pair_key(sid, pid)
    attempts = sorted(path for path in pair.glob("attempt_*") if path.is_dir())
    for attempt in reversed(attempts):
        result_path = attempt / "result.json"
        if not result_path.is_file():
            continue
        result = _read(result_path)
        if (result.get("scenario_id"), result.get("policy_id"),
            result.get("plan_signature")) != (sid, pid, plan_signature):
            raise ValueError(f"Result identity/plan mismatch: {result_path}")
        return result, {"attempt": attempt, "result_path": result_path}
    return None, None


def _open_raw(result: dict, metadata: dict, sid: str, pid: str) -> tuple[dict, str, Path]:
    name = result.get("trajectory_path")
    if not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("Complete pair has nonlocal raw path")
    path = metadata["attempt"] / name
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = _sha(path)
    if digest != result.get("trajectory_sha256"):
        raise ValueError(f"Raw trajectory SHA mismatch: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if (payload.get("identity", {}).get("scenario_id"),
        payload.get("identity", {}).get("policy_id")) != (sid, pid):
        raise ValueError(f"Raw trajectory identity mismatch: {path}")
    return payload, digest, path


def _segment_at(segments: list[dict], mid: float) -> dict | None:
    for segment in segments:
        if float(segment["start_minute"]) <= mid < float(segment["end_minute"]):
            return segment
    return None


def _aligned_effect(candidate: list[dict], secure: list[dict], window: tuple[float, float]) -> dict:
    start, end = map(float, window)
    if not 0 <= start < end:
        raise ValueError("Invalid observation window")
    bounds = {start, end}
    for segment in candidate + secure:
        a = float(segment["start_minute"])
        b = float(segment["end_minute"])
        if b <= a:
            raise ValueError("Raw segment has nonpositive duration")
        if start < a < end:
            bounds.add(a)
        if start < b < end:
            bounds.add(b)
    edges = sorted(bounds)
    first_dispatch = None
    first_extra_failure = None
    first_raw_ens = None
    first_temp = None
    extra_failure = 0.0
    resolved_failure = 0.0
    candidate_failure = 0.0
    secure_failure = 0.0
    unknown_minutes = 0.0
    aligned_intervals = 0
    max_dispatch_difference = 0.0
    ens_delta = [0.0, 0.0, 0.0]
    max_temperature_delta = None
    for a, b in zip(edges, edges[1:]):
        if b <= a:
            continue
        mid = (a + b) / 2
        left = _segment_at(candidate, mid)
        right = _segment_at(secure, mid)
        if left is None or right is None:
            unknown_minutes += b - a
            continue
        aligned_intervals += 1
        ca, sa = _six(left["action"]), _six(right["action"])
        diff = [x - y for x, y in zip(ca, sa)]
        largest = max(abs(value) for value in diff)
        max_dispatch_difference = max(max_dispatch_difference, largest)
        if largest > DISPATCH_TOLERANCE_KW and first_dispatch is None:
            first_dispatch = {"time_minute": a,
                              "six_port_delta_kW": diff,
                              "candidate_six_port_kW": ca,
                              "secure_six_port_kW": sa,
                              "max_abs_difference_kW": largest}
        cf = bool(left["classification"]["p0_guarantee_failure"])
        sf = bool(right["classification"]["p0_guarantee_failure"])
        if cf:
            candidate_failure += b - a
        if sf:
            secure_failure += b - a
        if cf and not sf:
            extra_failure += b - a
            if first_extra_failure is None:
                first_extra_failure = a
        if sf and not cf:
            resolved_failure += b - a
        c_unserved = [float(x) for x in left["unserved_kW"][0]]
        s_unserved = [float(x) for x in right["unserved_kW"][0]]
        if len(c_unserved) != 3 or len(s_unserved) != 3:
            raise ValueError("Raw P0 carrier vector has wrong length")
        rate_delta = [x - y for x, y in zip(c_unserved, s_unserved)]
        ens_delta = [x + rate * (b - a) / 60 for x, rate in zip(ens_delta, rate_delta)]
        if any(rate > ENS_RATE_TOLERANCE_KW for rate in rate_delta) and first_raw_ens is None:
            first_raw_ens = a
        temp_delta = float(left["temperature"]["P0"]) - float(right["temperature"]["P0"])
        max_temperature_delta = temp_delta if max_temperature_delta is None else max(max_temperature_delta, temp_delta)
        if temp_delta > 1e-7 and first_temp is None:
            first_temp = a
    return {"aligned_interval_count": aligned_intervals,
            "unknown_alignment_minutes": unknown_minutes,
            "first_actual_dispatch_difference_minute":
                first_dispatch["time_minute"] if first_dispatch else None,
            "first_actual_dispatch_difference": first_dispatch,
            "max_actual_six_port_difference_kW": max_dispatch_difference,
            "first_extra_P0_failure_minute": first_extra_failure,
            "first_P0_raw_ENS_excess_minute": first_raw_ens,
            "first_P0_temperature_excess_minute": first_temp,
            "extra_P0_failure_minutes": extra_failure if unknown_minutes == 0 else None,
            "resolved_P0_failure_minutes": resolved_failure if unknown_minutes == 0 else None,
            "net_P0_failure_minutes_delta":
                candidate_failure - secure_failure if unknown_minutes == 0 else None,
            "candidate_P0_failure_minutes": candidate_failure if unknown_minutes == 0 else None,
            "secure_P0_failure_minutes": secure_failure if unknown_minutes == 0 else None,
            "P0_raw_ENS_kWh_delta_by_carrier": ens_delta if unknown_minutes == 0 else None,
            "maximum_aligned_P0_temperature_delta_C": max_temperature_delta,
            "dispatch_difference_threshold_kW": DISPATCH_TOLERANCE_KW,
            "raw_ENS_rate_difference_threshold_kW": ENS_RATE_TOLERANCE_KW}


def diagnose_execution_effects(v5_run: str | Path,
                               *, batches: tuple[str, ...] = ("search/generation_1",
                                                                   "search/generation_2"),
                               window: tuple[float, float] = (60, 420)) -> dict:
    """Read only raw pairs with positive P0 target deltas; retain all unknowns."""
    run = Path(v5_run).resolve()
    if len(set(batches)) != len(batches) or not batches:
        raise ValueError("Need distinct named batches")
    batch_outputs = []
    registered_sources = {}
    for name in batches:
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Batch path escapes v5 run")
        folder = run / name
        plan_path = folder / "registered_plan.json"
        plan = _read(plan_path)
        if plan.get("schema") != "policy_search.matched_full_feedback.v1":
            raise ValueError("Expected registered complete-feedback plan")
        if len(plan["scenario_ids"]) * len(plan["policy_specs"]) != plan["registered_pair_count"]:
            raise ValueError("Plan denominator differs")
        if "secure_milp" not in [spec["policy_id"] for spec in plan["policy_specs"]]:
            raise ValueError("Original secure comparison absent")
        for path, digest in plan.get("source_sha256", {}).items():
            registered_sources.setdefault(path, set()).add(digest)
        worse = []
        unknown = []
        complete_count = 0
        candidate_specs = [spec for spec in plan["policy_specs"] if spec["policy_id"] != "secure_milp"]
        for sid in plan["scenario_ids"]:
            baseline, bmeta = _payload(folder, sid, "secure_milp", plan["signature"])
            for spec in candidate_specs:
                pid = spec["policy_id"]
                candidate, cmeta = _payload(folder, sid, pid, plan["signature"])
                reason = None
                bscore = (baseline or {}).get("primary_score") or {}
                cscore = (candidate or {}).get("primary_score") or {}
                bt, ct = bscore.get("targets"), cscore.get("targets")
                if not baseline or not candidate:
                    reason = "missing_result"
                elif not baseline.get("terminal") or not candidate.get("terminal"):
                    reason = "nonterminal_result"
                elif not bscore.get("full_horizon") or not cscore.get("full_horizon"):
                    reason = "incomplete_primary_window"
                elif not bt or not ct or len(bt) != 19 or len(ct) != 19 or\
                        any(x is None or y is None for x, y in zip(bt, ct)):
                    reason = "unobserved_target_component"
                else:
                    bb, cb = baseline.get("binding") or {}, candidate.get("binding") or {}
                    if (bb.get("passed") is not True or cb.get("passed") is not True or
                        bb.get("initial_state_sha256") != cb.get("initial_state_sha256") or
                        bb.get("exogenous_stream_id") != cb.get("exogenous_stream_id")):
                        reason = "unmatched_initial_state_or_stream"
                if reason:
                    unknown.append({"scenario_id": sid, "policy_id": pid, "reason": reason,
                                    "candidate_status": (candidate or {}).get("status"),
                                    "secure_status": (baseline or {}).get("status")})
                    continue
                complete_count += 1
                positive = [name for index, name in zip(P0_TARGET_INDICES, P0_TARGET_NAMES)
                            if float(ct[index]) > float(bt[index])]
                if not positive:
                    continue
                cpayload, csha, cpath = _open_raw(candidate, cmeta, sid, pid)
                bpayload, bsha, bpath = _open_raw(baseline, bmeta, sid, "secure_milp")
                effect = _aligned_effect(cpayload["segments"], bpayload["segments"], window)
                score_delta = {label: float(ct[index]) - float(bt[index])
                               for index, label in zip(P0_TARGET_INDICES, P0_TARGET_NAMES)}
                duration_delta = effect["net_P0_failure_minutes_delta"]
                effect["score_P0_failure_minutes_delta"] = score_delta["P0_failure_minutes"]
                effect["P0_duration_delta_matches_score"] = (
                    abs(duration_delta - score_delta["P0_failure_minutes"]) <= 1e-6
                    if duration_delta is not None else None)
                worse.append({"scenario_id": sid, "policy_id": pid,
                              "candidate_status": candidate["status"],
                              "secure_status": baseline["status"],
                              "positive_P0_components": positive,
                              "P0_target_delta": score_delta,
                              "candidate_result_path": _label(cmeta["result_path"]),
                              "candidate_result_sha256": _sha(cmeta["result_path"]),
                              "secure_result_path": _label(bmeta["result_path"]),
                              "secure_result_sha256": _sha(bmeta["result_path"]),
                              "candidate_raw_path": _label(cpath),
                              "candidate_raw_sha256": csha,
                              "candidate_raw_sha256_verified": True,
                              "secure_raw_path": _label(bpath),
                              "secure_raw_sha256": bsha,
                              "secure_raw_sha256_verified": True,
                              **effect})
        batch_outputs.append({"batch": name, "plan_path": _label(plan_path),
                              "plan_sha256": _sha(plan_path),
                              "plan_signature": plan["signature"],
                              "manifest_sha256": plan.get("manifest_sha256"),
                              "simulator_hash": plan.get("simulator_hash"),
                              "registered_source_sha256": plan.get("source_sha256", {}),
                              "registered_candidate_pair_count": len(plan["scenario_ids"]) * len(candidate_specs),
                              "complete_matched_pair_count": complete_count,
                              "P0_worse_complete_pair_count": len(worse),
                              "unknown_pair_count": len(unknown),
                              "unknown_pairs": unknown,
                              "P0_worse_complete_pairs": worse})
    return {"schema": "policy_search.execution_effect.v1",
            "windows": list(window),
            "six_ports": list(SIX_PORTS),
            "positive_P0_components_use_exact_stored_values": True,
            "actual_dispatch_difference_threshold_kW": DISPATCH_TOLERANCE_KW,
            "registered_source_sha256": {key: next(iter(values)) if len(values) == 1 else sorted(values)
                                         for key, values in registered_sources.items()},
            "batches": batch_outputs,
            "requests_are_not_effects": True,
            "unknowns_not_imputed": True}


def write_execution_effects(v5_run: str | Path, output_path: str | Path,
                            *, batches: tuple[str, ...] = ("search/generation_1",
                                                                "search/generation_2"),
                            window: tuple[float, float] = (60, 420),
                            require_terminal: bool = True) -> dict:
    value = diagnose_execution_effects(v5_run, batches=batches, window=window)
    if require_terminal and any(batch["unknown_pair_count"] for batch in value["batches"]):
        # A terminal model failure is an unknown outcome and remains reportable;
        # only absent or nonterminal results block a final diagnostic snapshot.
        pending = [row for batch in value["batches"] for row in batch["unknown_pairs"]
                   if row["reason"] in ("missing_result", "nonterminal_result")]
        if pending:
            raise ValueError("Selected batch still has nonterminal registered pairs")
    output = Path(output_path).resolve()
    output.relative_to(Path(v5_run).resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"path": _label(output), "sha256": _sha(output), "bytes": output.stat().st_size,
            "P0_worse_complete_pair_count": sum(b["P0_worse_complete_pair_count"] for b in value["batches"]),
            "unknown_pair_count": sum(b["unknown_pair_count"] for b in value["batches"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("v5_run", type=Path)
    parser.add_argument("--batch", action="append", dest="batches")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_execution_effects(args.v5_run, args.output,
                                             batches=tuple(args.batches) if args.batches else
                                             ("search/generation_1", "search/generation_2")),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
