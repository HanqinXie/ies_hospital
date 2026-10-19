"""Read-only same-state chain diagnosis from frozen v4 projections and traces."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path

REPRESENTATIVE_INDICES = (2, 92, 127, 139, 152, 164, 176)
WEIGHTS = (0.7, 2.0, 6.0, 10.0)
COMPLETE = {"completed", "bounded_observation_completed"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _recorded_path(root: Path, name: str) -> Path:
    relative = Path(name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Frozen artifact path escapes run: {name}")
    return root / relative


def _accepted(layer: dict) -> dict:
    attempts = [item for item in layer["attempts"] if item.get("accepted")]
    if len(attempts) != 1:
        raise ValueError("Each lexicographic rank needs one accepted solver attempt")
    return attempts[0]


def _layers(candidate: dict, secure_query: dict) -> list[dict]:
    query = candidate.get("current_planning_query") or secure_query
    calls = query["solver_calls"]
    if len(calls) != 1 or calls[0]["phase"] != "command_generation":
        raise ValueError("Saved query is not an isolated current command projection")
    layers = calls[0]["solver_backend_layers"]
    if [layer["rank"] for layer in layers] != [0, 1, 2, 3, 4]:
        raise ValueError("Expected four unchanged service ranks and one cost rank")
    return layers


def _objectives(layers: list[dict]) -> list[float]:
    return [float(_accepted(layer)["raw_objective"]) for layer in layers]


def _active_rows(layers: list[dict]) -> dict:
    active = {}
    for row in _accepted(layers[4])["validation"]["rows"]:
        value, lower, upper = float(row["activity"]), row["lower"], row["upper"]
        if ((lower is not None and abs(value - float(lower)) <= 1e-7)
                or (upper is not None and abs(value - float(upper)) <= 1e-7)):
            active[row["name"]] = {"activity": value, "lower": lower,
                                    "upper": upper, "kind": row["kind"]}
    return active


def _ports(dispatch: dict) -> list[float]:
    return [float(v) for v in dispatch["gt_electric_kW"]] + [
        float(dispatch["grid_import_kW"]), float(dispatch["diesel_kW"]),
        float(dispatch["absorption_cooling_kW"])]


def _outcome(result: dict, candidate_id: str) -> dict:
    rows = [row for row in result["outcomes"]
            if row["future_id"] == "original" and row["candidate_id"] == candidate_id]
    if len(rows) != 1:
        raise ValueError(f"Expected one original-future outcome for {candidate_id}")
    return rows[0]


def _label(result: dict, candidate_id: str) -> str:
    rows = [row for row in result["opportunities"] if row["candidate_id"] == candidate_id]
    if len(rows) != 1:
        raise ValueError(f"Expected one saved opportunity comparison for {candidate_id}")
    futures = [row for row in rows[0]["per_future"] if row["future_id"] == "original"]
    if len(futures) != 1:
        raise ValueError("Expected one original-future comparison")
    return futures[0]["comparison"]


def _trace(root: Path, outcome: dict, hashes: dict) -> dict | None:
    if outcome["status"] not in COMPLETE:
        return None
    path = _recorded_path(root, outcome["trajectory_file"])
    name, digest = path.relative_to(root).as_posix(), _sha(path)
    if digest != outcome["trajectory_sha256"]:
        raise ValueError(f"Frozen trajectory SHA mismatch: {name}")
    hashes[name] = digest
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        raw = json.load(stream)
    if not raw["decisions"]:
        raise ValueError("Saved continuation lacks current decision")
    decision = raw["decisions"][0]
    command = decision["command_generated"]
    if decision["requested_command"] != command["request"]:
        raise ValueError("Current request differs from generated command")
    command_id = command["id"]
    arrivals = [e for e in raw["events"] if e["kind"] == "command_arrived" and e["command_id"] == command_id]
    executions = [e for e in raw["events"] if e["kind"] == "command_executed"]
    first = next((e for e in executions if e["command_id"] == command_id), None)
    later = next((e for e in executions if first is not None
                  and e["time_minute"] > first["time_minute"] and e["command_id"] != command_id), None)
    superseded = any(e["kind"] == "command_superseded" and e["command_id"] == command_id for e in raw["events"])
    return {
        "trajectory_file": name, "trajectory_sha256": digest,
        "decision_minute": float(decision["decision_time_min"]),
        "requested_command": decision["requested_command"],
        "command_id": command_id,
        "planned_arrival_minute": float(command["arrival_minute"]),
        "planned_six_ports_kW": _ports(command["dispatch"]),
        "command_arrived_minute": None if not arrivals else float(arrivals[0]["time_minute"]),
        "command_executed_minute": None if first is None else float(first["time_minute"]),
        "executed_six_ports_kW": None if first is None else _ports(first["executed_dispatch"]),
        "superseded_before_execution": superseded and first is None,
        "next_command_executed_minute": None if later is None else float(later["time_minute"]),
        "effective_minutes_before_next_command": None if first is None or later is None else
            float(later["time_minute"] - first["time_minute"]),
        "first_step_interval_average_six_ports_kW": _ports(decision["actual_execution"]),
        "first_step_average_is_not_arrived_setpoint": True,
    }


def _snapshot(root: Path, result: dict, *, detailed: bool, hashes: dict) -> dict:
    index = int(result["index"])
    current_path = root / f"branches/snapshot_{index:03d}/attempt_0001/CURRENT_CANDIDATES.json"
    current = _read(current_path)
    hashes[current_path.relative_to(root).as_posix()] = _sha(current_path)
    candidates = result["candidates"]
    if not candidates or candidates[0]["candidate_id"] != "secure_baseline":
        raise ValueError(f"Snapshot {index} lacks original secure candidate zero")
    secure = candidates[0]
    secure_query = current["queries"][0]
    if secure_query["command"] != secure["current_command"]:
        raise ValueError(f"Snapshot {index} secure current projection differs")
    base_layers = _layers(secure, secure_query)
    base_objectives = _objectives(base_layers)
    dt_h = float(secure_query["solver_calls"][0]["dt_h"])
    features = current["history"][-1]["features"]
    effective_grid = float(current["context"]["grid_import_limit_kW"]) * float(features["availability_GRID"])
    weights, residuals = [], []
    for candidate in candidates[1:]:
        cid = candidate["candidate_id"]
        projected = candidate["projected_targets_kW"]
        max_delta = max(abs(float(a) - float(b)) for a, b in zip(projected, secure["projected_targets_kW"]))
        outcome = _outcome(result, cid)
        row = {"candidate_id": cid, "request": candidate["request"],
               "projected_six_ports_kW": projected,
               "max_six_port_plan_delta_kW": max_delta,
               "full_dispatch_equal_secure": candidate["current_command"]["dispatch"] == secure["current_command"]["dispatch"],
               "disconnected_equal_secure": candidate["current_command"]["disconnected"] == secure["current_command"]["disconnected"],
               "planned_arrival_minute": float(candidate["current_command"]["arrival_minute"]),
               "original_future_label": _label(result, cid),
               "original_future_status": outcome["status"]}
        if detailed:
            row["original_future_execution"] = _trace(root, outcome, hashes)
        if cid.startswith("security_weight_"):
            weight = float(candidate["request"]["security"]["gt_loading_penalty_USD_per_kWh"])
            objectives = _objectives(_layers(candidate, secure_query))
            delta = objectives[4] - base_objectives[4]
            expected = (weight - .35) * dt_h * sum(float(x) for x in secure["projected_targets_kW"][:3])
            row.update(weight_USD_per_kWh=weight,
                       objective_coefficient_delta_USD_per_GT_kW_step=(weight - .35) * dt_h,
                       rank0_to3_objectives=objectives[:4],
                       rank0_to3_equal_secure=all(math.isclose(a, b, abs_tol=1e-7)
                                                  for a, b in zip(objectives[:4], base_objectives[:4])),
                       rank4_raw_objective_USD=objectives[4],
                       rank4_objective_delta_USD=delta,
                       rank4_objective_delta_at_unchanged_plan_USD=expected,
                       rank4_objective_delta_matches_coefficient=(row["full_dispatch_equal_secure"]
                           and math.isclose(delta, expected, rel_tol=1e-8, abs_tol=1e-6)),
                       command_weight_USD_per_kWh=float(candidate["current_command"]["weight"]))
            weights.append(row)
        elif cid.startswith("residual_"):
            requested_grid = candidate["request"].get("targets", {}).get("grid_import")
            row.update(requested_grid_kW=requested_grid,
                       projected_grid_kW=float(projected[3]),
                       requested_grid_above_current_effective_limit=(requested_grid is not None
                           and float(requested_grid) > effective_grid + 1e-7))
            residuals.append(row)
        else:
            raise ValueError(f"Unexpected frozen probe candidate: {cid}")
    if sorted(row["weight_USD_per_kWh"] for row in weights) != list(WEIGHTS):
        raise ValueError(f"Snapshot {index} lacks registered weight candidates")
    output = {
        "index": index, "official_status": result["status"],
        "unknown_outcome_count": sum(o["status"] not in COMPLETE for o in result["outcomes"]),
        "snapshot_minute": float(result["snapshot_minute"]),
        "current_grid_availability": float(features["availability_GRID"]),
        "configured_grid_limit_kW": float(current["context"]["grid_import_limit_kW"]),
        "effective_current_grid_limit_kW": effective_grid,
        "secure_six_ports_kW": secure["projected_targets_kW"],
        "secure_rank0_to3_objectives": base_objectives[:4],
        "secure_rank4_raw_objective_USD": base_objectives[4],
        "active_lock_rows": _active_rows(base_layers),
        "weight_candidates": weights, "residual_candidates": residuals,
        "weight_plan_changed_count": sum(w["max_six_port_plan_delta_kW"] > 1e-6 for w in weights),
        "residual_plan_changed_count": sum(r["max_six_port_plan_delta_kW"] > 1e-6 for r in residuals),
        "six_port_equal_is_not_proven_execution_equivalent": True,
    }
    if detailed:
        output["secure_original_future_execution"] = _trace(root, _outcome(result, "secure_baseline"), hashes)
    return output


def build_action_diagnostic(v4_run_dir, *, snapshot_indices=REPRESENTATIVE_INDICES) -> dict:
    """Verify old result hashes and trace parameter, plan, queue, execution."""
    root = Path(v4_run_dir).resolve()
    summary_path = root / "soft_objective_probe_v2/PROBE_SUMMARY.json"
    summary = _read(summary_path)
    if summary["schema"] != "reliability_first.soft_objective_probe_summary.v1":
        raise ValueError("Unexpected frozen v4 probe summary")
    selected = tuple(int(i) for i in snapshot_indices)
    if len(selected) != len(set(selected)):
        raise ValueError("Representative indices must be unique")
    hashes = {summary_path.relative_to(root).as_posix(): _sha(summary_path)}
    results = {}
    for receipt in summary["results"]:
        path = _recorded_path(root, receipt["result_path"])
        digest = _sha(path)
        if digest != receipt["result_sha256"]:
            raise ValueError(f"Frozen v4 result SHA mismatch: {path}")
        result = _read(path)
        index = int(receipt["index"])
        if index in results or result["index"] != index or result["status"] != receipt["status"]:
            raise ValueError("Frozen result identity/status mismatch")
        results[index] = result
        hashes[path.relative_to(root).as_posix()] = digest
    if len(results) != summary["registered_snapshots"] or not set(selected) <= set(results):
        raise ValueError("Frozen snapshot inventory differs from summary")
    statuses = dict(sorted(Counter(result["status"] for result in results.values()).items()))
    if (statuses.get("complete", 0) != summary["completed_snapshots"]
            or statuses.get("unknown", 0) != summary["unknown_snapshots"]):
        raise ValueError("Frozen official statuses differ from summary")
    rows = [_snapshot(root, results[index], detailed=index in selected, hashes=hashes)
            for index in sorted(results)]
    weights = [w for row in rows for w in row["weight_candidates"]]
    residuals = [r for row in rows for r in row["residual_candidates"]]
    grid_categories = Counter()
    for row in rows:
        gt, grid = sum(row["secure_six_ports_kW"][:3]), row["secure_six_ports_kW"][3]
        cap = row["effective_current_grid_limit_kW"]
        key = ("grid_unavailable" if cap <= 1e-7 else "GT_off" if gt <= 1e-7
               else "grid_at_current_capacity" if abs(grid - cap) <= 1e-6
               else "GT_on_grid_headroom")
        grid_categories[key] += 1
    return {
        "schema": "policy_search.action_chain_diagnostic.v1",
        "evidence_semantics": "Saved current projections and original-future one-shot traces; no new simulation or full-policy benefit claim",
        "source_v4_run": str(root),
        "source_sha256": dict(sorted(hashes.items())),
        "registered_snapshot_count": len(results),
        "official_status_counts": statuses,
        "representative_indices": list(selected),
        "weight_probe_summary": {
            "weight_candidate_count": len(weights),
            "tested_weights_USD_per_kWh": list(WEIGHTS),
            "weight_plan_changed_count": sum(w["max_six_port_plan_delta_kW"] > 1e-6 for w in weights),
            "weight_full_dispatch_changed_count": sum(not w["full_dispatch_equal_secure"] for w in weights),
            "objective_coefficient_identity_count": sum(w["rank4_objective_delta_matches_coefficient"] for w in weights),
            "all_weight_dispatches_equal_secure": all(w["full_dispatch_equal_secure"] for w in weights),
            "grid_and_GT_status_counts": dict(sorted(grid_categories.items())),
            "formula": "c_cost[GT_i] += security_weight_USD_per_kWh * dt_h; four service ranks precede cost",
        },
        "residual_probe_summary": {
            "candidate_count": len(residuals),
            "six_port_plan_changed_count": sum(r["max_six_port_plan_delta_kW"] > 1e-6 for r in residuals),
            "requested_grid_above_current_effective_limit_count": sum(bool(r["requested_grid_above_current_effective_limit"]) for r in residuals),
            "no_freedom_conclusion_from_weight_probe": True,
        },
        "representative_snapshots": [row for row in rows if row["index"] in selected],
        "official_unknowns_preserved": True,
    }


def write_action_diagnostic(v4_run_dir, output_dir, *, snapshot_indices=REPRESENTATIVE_INDICES) -> Path:
    """Write only a fresh v5 artifact, never to the frozen v4 run."""
    root, destination = Path(v4_run_dir).resolve(), Path(output_dir).resolve()
    if destination == root or root in destination.parents:
        raise ValueError("Diagnostic output must be outside frozen v4 run")
    path = destination / "ACTION_CHAIN_DIAGNOSTIC.json"
    if path.exists():
        raise FileExistsError(path)
    report = build_action_diagnostic(root, snapshot_indices=snapshot_indices)
    destination.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("v4_run_dir", type=Path)
    parser.add_argument("v5_output_dir", type=Path)
    parser.add_argument("--snapshot-indices", default=",".join(map(str, REPRESENTATIVE_INDICES)))
    args = parser.parse_args()
    indices = tuple(int(value) for value in args.snapshot_indices.split(","))
    path = write_action_diagnostic(args.v4_run_dir, args.v5_output_dir, snapshot_indices=indices)
    print(json.dumps({"path": str(path), "sha256": _sha(path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
