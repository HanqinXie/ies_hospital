"""Bounded v9 evidence audit and real arrival-handler probes; no trajectory rerun.

Post-hoc saved scripts are read for diagnosis only. They never reach a policy.
The only optimization calls are two existing auxiliary diagnostic LPs.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import fields
import gzip
import hashlib
import inspect
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.execution import execution_contract, maximum_auxiliary_sink
from hospital_ies.model import Plant, load_plant
from hospital_ies.resilience import CARRIERS, GROUPS, RuntimeState
from hospital_ies.state_transition import TransitionCore, clean


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _raw(path, expected):
    assert sha(path) == expected, "Saved raw SHA mismatch"
    payload = json.loads(gzip.decompress(Path(path).read_bytes()))
    assert not payload["scenario"].get("is_final_blind", False)
    return payload


def _failure_minutes(raw):
    return sum(s["end_minute"] - s["start_minute"] for s in raw["segments"]
               if s["classification"]["p0_guarantee_failure"])


def _arrival_probe(root, raw):
    """Call unchanged production boundary() using a saved state and due commands.

    data_at is a fixed CURRENT snapshot, so this is an actuator/queue unit probe,
    not a reset, step, physical solve or downstream counterfactual simulation.
    """
    segment = next(s for s in raw["segments"] if s["start_minute"] == 182.5)
    prior = next(s for s in raw["segments"] if s["start_minute"] == 180)
    decision = next(d for d in raw["decisions"] if d["decision_time_min"] == 180)
    values = copy.deepcopy(segment["pre_state"])
    values["extra_faulted"] = set(values["extra_faulted"])
    for key in ("gt_prev", "overload_minutes", "disconnected_loads"):
        values[key] = np.asarray(values[key], dtype=float)
    state = RuntimeState(**{f.name: values[f.name] for f in fields(RuntimeState)})
    loads = np.asarray(segment["loads_kW"], dtype=float)
    availability = copy.deepcopy(segment["availability"])
    base = {f"{g}_{c}_kW": loads[gi, ci] for gi, g in enumerate(GROUPS)
            for ci, c in enumerate(CARRIERS)}
    contract = execution_contract(_read(root / "configs/p1b_execution_research.json"))
    env = SimpleNamespace(
        state=state, scenario=copy.deepcopy(raw["scenario"]), steps_done=12,
        dt_minutes=15.0, dt_h=.25, execution_contract=contract,
        config=_read(root / "configs/resilience_assumptions.json"),
        plant=load_plant(root / "configs/plant_configs.json", "doe_capacity_matched_exploratory"),
        groups=pd.DataFrame([base], index=[raw["scenario"]["start_hour"]]), trip_events=[])
    core = TransitionCore(env, copy.deepcopy(prior["arrived_source_setpoints"]))
    row = SimpleNamespace(outdoor_temperature_C=decision["features"]["outdoor_temperature_C"])
    core.data_at = lambda _t: (segment["hour_of_year"], row, loads.copy(), availability.copy())
    core.time = 182.5
    core.pending_commands = [copy.deepcopy(e["command"]) for e in raw["events"]
                             if e["kind"] == "command_generated" and e["command"]["id"] in (12, 13)]
    core.boundary()
    actual = next(e["executed_dispatch"] for e in raw["events"]
                  if e["kind"] == "command_executed" and e["time_minute"] == 182.5)
    for key in ("gt_electric_kW", "grid_import_kW", "diesel_kW", "boiler_steam_lbph"):
        assert core.executed[key] == actual[key], f"Arrival unit probe mismatch: {key}"
    return dict(policy_id=raw["identity"]["policy_id"], scenario_id=raw["scenario"]["id"],
                real_boundary_called=True, physical_solve_calls=0,
                superseded_command_ids=[e["command_id"] for e in core.events if e["kind"] == "command_superseded"],
                active_command_id=core.active_command["id"],
                old_command_boiler_lbph=next(e["command"]["dispatch"]["boiler_steam_lbph"]
                    for e in raw["events"] if e["kind"] == "command_generated" and e["command"]["id"] == 12),
                actual_gt_kW=core.executed["gt_electric_kW"], actual_boiler_lbph=core.executed["boiler_steam_lbph"],
                method_events=core.events)


def run_probe(root=ROOT):
    root = Path(root)
    run = root / "outputs/v9_state_dispatch_20261016_run01"
    review = run / "review"
    final = _read(review / "FINAL_BOUNDARY_AND_HOLDOUT_REVIEW.json")
    selection = _read(review / "V2_SELECTION_REVIEW.json")
    unknown = _read(review / "CONFIRMATION_UNKNOWN_REVIEW.json")
    plan = _read(run / "confirmation/registered_plan.json")
    manifest_path = run / "new_data/global_manifest.json"
    assert sha(manifest_path) == plan["manifest_sha256"]
    manifest = _read(manifest_path)
    inventory = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    core_paths = ("src/hospital_ies/control_env.py", "src/hospital_ies/state_transition.py",
                  "src/hospital_ies/execution.py", "src/hospital_ies/resilience.py")
    source_binding = {p: dict(current_sha256=sha(root / p), v9_registered_sha256=inventory.get(p))
                      for p in core_paths}
    for entry in source_binding.values():
        assert entry["current_sha256"] == entry["v9_registered_sha256"], "Physical core drift from v9"
    step_source = inspect.getsource(HospitalIESEnv.step)
    advance_source = inspect.getsource(TransitionCore.advance)
    assert step_source.index("self.core.boundary()") < step_source.index("self.core.generate_command(action)")
    assert step_source.index("self.core.generate_command(action)") < step_source.index("self.core.advance(")
    assert advance_source.index("self.boundary()") < advance_source.index("self._surplus_interlock(")
    result = dict(schema="dispatch_closeout.contract_probe.v1",
        scope="saved raw rescore, actual arrival handler on saved current state, 2 auxiliary LPs; no episode rerun",
        new_trajectory_attempts=0, new_MILP_calls=0, current_state_auxiliary_LP_queries=0,
        final_blind_opened=False, physical_core_changed=False, source_binding=source_binding,
        input_bindings={str(p.relative_to(root)): sha(p) for p in (
            review / "FINAL_BOUNDARY_AND_HOLDOUT_REVIEW.json", review / "V2_SELECTION_REVIEW.json",
            review / "CONFIRMATION_UNKNOWN_REVIEW.json", root / "docs/v43/STATE_TRANSITION_DESIGN.md",
            root / "docs/v43/P1B_EXECUTION_CONTRACT.md")},
        source_order_verified="boundary -> observe -> generate -> advance.boundary -> local interlock -> physical solve",
        day298=[], arrival_method_probes=[], day051_development=[], day051_confirmation=[], raw_bindings=[])
    for entry in final["new_holdout_day298_raw_chains"]:
        raw = _raw(entry["raw_path"], entry["raw_sha256"])
        result["raw_bindings"].append({"path": entry["raw_path"], "sha256": entry["raw_sha256"]})
        intervals = [[s["start_minute"], s["end_minute"]] for s in raw["segments"]
                     if s["classification"]["p0_guarantee_failure"]]
        h_ens = sum(s["unserved_kW"][0][1] * (s["end_minute"]-s["start_minute"])/60 for s in raw["segments"])
        events = [e for e in raw["events"] if e["time_minute"] == 180]
        interlock = next((e for e in events if e["kind"] == "minimum_output_interlock"), None)
        generated = next(e for e in events if e["kind"] == "command_generated")
        result["day298"].append(dict(scenario_id=entry["scenario_id"], policy_id=entry["policy_id"],
            analysis_class=entry["analysis_class"], P0_failure_minutes=_failure_minutes(raw),
            P0_h_ENS_kWh=h_ens, failure_intervals=intervals,
            generated_before_minimum_interlock=(events.index(generated) < events.index(interlock)) if interlock else None,
            minimum_deficit_kW=interlock["minimum_kW"]-interlock["actual_kW"] if interlock else None,
            key_clock=entry["key_clock"]))
        if interlock:
            result["arrival_method_probes"].append(_arrival_probe(root, raw))
    selected = [r for r in selection["scenario_policy_rows"] if r["source_day_index"] == 51
                and r["analysis_class"] == "L4b" and r["policy_id"] != "state_grid_bounded_spread"]
    for entry in selected:
        source = next(r for r in selection["raw_sources"] if r["scenario_id"] == entry["scenario_id"]
                      and r["policy_id"] == entry["policy_id"])
        raw = _raw(source["raw_path"], source["raw_sha256"])
        result["raw_bindings"].append({"path": source["raw_path"], "sha256": source["raw_sha256"]})
        at270 = next(s for s in raw["segments"] if s["start_minute"] == 270)
        result["day051_development"].append(dict(policy_id=entry["policy_id"], scenario_id=entry["scenario_id"],
            P0_failure_minutes=_failure_minutes(raw),
            failure_intervals=[[s["start_minute"],s["end_minute"]] for s in raw["segments"] if s["classification"]["p0_guarantee_failure"]],
            diesel_energy_at_270_kWh=at270["pre_state"]["diesel_energy_remaining_kWh"],
            diesel_action_at_270_kW=at270["action"]["diesel_kW"],
            protection_times=[e["time_minute"] for e in raw["events"] if e["kind"] == "protection_trip"]))
    for entry in unknown["policies"]:
        raw = _raw(entry["raw_path"], entry["raw_sha256"])
        result["raw_bindings"].append({"path": entry["raw_path"], "sha256": entry["raw_sha256"]})
        full = entry["full_observation"]
        diag = (raw.get("error") or {}).get("diagnostic")
        item = dict(policy_id=entry["policy_id"], status=entry["status"],
                    observed_until_minute=entry["observed_until_minute"],
                    full_tail_unknown=not full["full_horizon"],
                    full_failure_minutes=_failure_minutes(raw) if full["full_horizon"] else None,
                    observed_prefix_failure_minutes=_failure_minutes(raw),
                    full_ENS_kWh=full["ENS_kWh_by_priority_carrier"])
        if diag:
            plant = Plant(**diag["plant"])
            row = SimpleNamespace(outdoor_temperature_C=diag["row"]["outdoor_temperature_C"])
            loads = np.asarray(diag["loads_kW"])
            action = diag["executed_action"]
            aux = maximum_auxiliary_sink(plant, row, loads, diag["availability"], action)
            result["current_state_auxiliary_LP_queries"] += 1
            supply = sum(action["gt_electric_kW"]) + action["diesel_kW"]
            if diag["execution_contract"]["grid_import_mode"] == "fixed":
                supply += action["grid_import_kW"]
            item.update(fixed_source_kW=supply, connected_electric_kW=float(loads[:, 0].sum()),
                        maximum_real_auxiliary_kW=aux,
                        minimum_unabsorbable_surplus_kW=supply-float(loads[:,0].sum())-aux,
                        held_diesel_kW=action["diesel_kW"], held_boiler_lbph=action["boiler_steam_lbph"],
                        phase=diag["phase"], pending_command_count=len(diag["pending_commands"]))
        result["day051_confirmation"].append(item)
    return clean(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "outputs/v10_dispatch_closeout_20261016_run01/review")
    args = parser.parse_args()
    evidence = run_probe()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / "CONTRACT_PROBE_EVIDENCE.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"output": str(path), "verified_raws": len(evidence["raw_bindings"]),
                      "arrival_method_probes": len(evidence["arrival_method_probes"]),
                      "new_trajectory_attempts": 0, "new_MILP_calls": 0,
                      "current_state_auxiliary_LP_queries": evidence["current_state_auxiliary_LP_queries"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
