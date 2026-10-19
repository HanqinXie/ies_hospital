"""Audit legal v6 request -> current plan -> arrival -> executed port traces.

This is descriptive matched-episode evidence.  A parallel secure execution is
not the same state after the policies diverge, so it is never substituted for
the candidate's pure same-state secure projection.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import numpy as np

from tools.integrated_learning.policies import ACTION_NAMES, six_targets


def _read_raw(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _actual_six(decision):
    actual = decision.get("actual_execution")
    if not isinstance(actual, dict):
        return None
    return np.asarray([*actual["gt_electric_kW"], actual["grid_import_kW"],
                       actual["diesel_kW"], actual["absorption_cooling_kW"]], dtype=float)


def diagnose_pair(candidate_raw, secure_raw, *, tolerance_kW=1e-6):
    candidate = _read_raw(candidate_raw)
    secure = _read_raw(secure_raw)
    for key in ("scenario_id", "exogenous_stream_id"):
        if candidate["identity"].get(key) != secure["identity"].get(key):
            raise ValueError("Matched pair identity differs: " + key)
    if candidate["initial_state"] != secure["initial_state"]:
        raise ValueError("Matched pair initial state differs")
    sec_by_time = {float(d["decision_time_min"]): d for d in secure["decisions"]}
    rows = []
    for decision in candidate["decisions"]:
        now = float(decision["decision_time_min"])
        diagnostic = decision.get("policy_diagnostic") or {}
        anchor = diagnostic.get("secure_projected_six_targets_kW")
        ask = diagnostic.get("requested_six_targets_kW")
        generated = decision.get("command_generated")
        plan = None if generated is None else six_targets(generated["dispatch"])
        execution = _actual_six(decision)
        secure_same_clock = sec_by_time.get(now)
        secure_execution = None if secure_same_clock is None else _actual_six(secure_same_clock)
        anchor = None if anchor is None else np.asarray(anchor, dtype=float)
        ask = None if ask is None else np.asarray(ask, dtype=float)
        if any(value is not None and value.shape != (6,) for value in
               (anchor, ask, plan, execution, secure_execution)):
            raise ValueError("Six-port action vector malformed")
        request_delta = None if anchor is None or ask is None else ask - anchor
        projection_delta = None if anchor is None or plan is None else plan - anchor
        executed_parallel_delta = (None if execution is None or secure_execution is None
                                   else execution - secure_execution)
        rows.append({
            "time_minute": now,
            "requested_minus_same_state_secure_plan_kW": None if request_delta is None else request_delta.tolist(),
            "generated_plan_minus_same_state_secure_plan_kW": None if projection_delta is None else projection_delta.tolist(),
            "arrived_command_count": len(decision.get("arrived_commands") or []),
            "parallel_executed_minus_secure_kW": None if executed_parallel_delta is None
            else executed_parallel_delta.tolist(),
            "request_changed": None if request_delta is None else bool(np.max(np.abs(request_delta)) > tolerance_kW),
            "current_plan_changed": None if projection_delta is None else bool(np.max(np.abs(projection_delta)) > tolerance_kW),
            "parallel_execution_changed": None if executed_parallel_delta is None else bool(
                np.max(np.abs(executed_parallel_delta)) > tolerance_kW),
            "generated_command_id": None if generated is None else generated.get("id"),
            "generated_arrival_minute": None if generated is None else generated.get("arrival_minute"),
            "strategy_status": diagnostic.get("strategy_status"),
        })
    def count(name):
        return sum(row[name] is True for row in rows)
    def port_count(name):
        return {port: sum(row[name] is not None and abs(row[name][i]) > tolerance_kW
                          for row in rows) for i, port in enumerate(ACTION_NAMES)}
    return {
        "schema": "policy_redesign.v6.action_effect.v1",
        "scenario_id": candidate["identity"].get("scenario_id"),
        "candidate_policy_id": candidate["identity"].get("policy_id"),
        "secure_policy_id": secure["identity"].get("policy_id"),
        "candidate_status": candidate["summary"].get("status"),
        "secure_status": secure["summary"].get("status"),
        "decision_count": len(rows), "request_changed_decisions": count("request_changed"),
        "current_plan_changed_decisions": count("current_plan_changed"),
        "parallel_execution_changed_decisions": count("parallel_execution_changed"),
        "request_changed_by_port": port_count("requested_minus_same_state_secure_plan_kW"),
        "current_plan_changed_by_port": port_count("generated_plan_minus_same_state_secure_plan_kW"),
        "parallel_execution_changed_by_port": port_count("parallel_executed_minus_secure_kW"),
        "command_arrival_events": sum(row["arrived_command_count"] for row in rows),
        "same_state_projection_is_descriptive_not_arrival_execution": True,
        "parallel_execution_after_divergence_is_matched_not_same_state": True,
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_raw", type=Path)
    parser.add_argument("secure_raw", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = diagnose_pair(args.candidate_raw, args.secure_raw)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False))
