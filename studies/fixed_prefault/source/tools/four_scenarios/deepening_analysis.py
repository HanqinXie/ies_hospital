"""Audited, read-only v8 four-scenario service and matched-cell analysis.

This module reads saved feedback trajectories. It does not call a simulator or
make any exogenous event available to an online policy. All new files go into
an explicitly new analysis directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from tools.four_scenarios import analysis as previous


CELL_NAMES = ("N4", "L3", "L4a", "L4b")
SERVICE_NAMES = tuple(f"P{p}_{c}_ENS_kWh" for p in range(4) for c in "ehc")
INTERACTION_NAMES = ("P0_failure_minutes",
                     "P0_temperature_trigger_minutes") + SERVICE_NAMES
LEGAL_OBSERVATION_FIELDS = ("version", "decision_time_min",
                            "observation_time_min", "history_start_min",
                            "history_end_min", "features", "feature_metadata",
                            "known_command_queue", "arrived_command_id", "graph")
CONTRAST_WINDOWS = ("primary", "full")
SENSITIVITY_AXES = {"delay": "response_delay_minutes",
                    "duration": "shock_duration_minutes",
                    "availability": "fault_availability"}
SENSITIVITY_CONTEXT_FIELDS = ("response_delay_minutes",
                              "shock_duration_minutes",
                              "fault_duration_minutes", "fault_availability")
EPS = previous.EPS


def first_failure_rescue(segments, *, observed_until, requested_end):
    """Measure rescue only after an actual observed P0 service failure.

    First reach and the first subsequent 30-minute clean confirmation are
    separate. A clean interval before any failure never enters this denominator.
    """
    answer = {"rescue_eligible": False, "first_p0_failure_minute": None,
              "first_reach_minute": None, "minutes_to_first_reach": None,
              "rescue_confirmed_minute": None, "minutes_to_rescue_confirmation": None,
              "rescue_confirmed": None, "rescue_status": "no_observed_failure",
              "rescue_later_recurrence_minute": None,
              "rescue_sustained_to_observation_end": None,
              "rescue_sustained_to_requested_end": None}
    first = None
    clean_start = None
    for segment in segments:
        start = float(segment["start_minute"])
        end = float(segment["end_minute"])
        failed = bool(segment["classification"]["p0_guarantee_failure"])
        if failed:
            if first is None:
                first = start
                answer["rescue_eligible"] = True
                answer["first_p0_failure_minute"] = first
            if (answer["rescue_confirmed"] is True and
                    answer["rescue_later_recurrence_minute"] is None):
                answer["rescue_later_recurrence_minute"] = start
            clean_start = None
            continue
        if first is None:
            continue
        if answer["first_reach_minute"] is None:
            answer["first_reach_minute"] = start
            answer["minutes_to_first_reach"] = start - first
        if clean_start is None:
            clean_start = start
        if (answer["rescue_confirmed"] is not True and
                end - clean_start >= previous.RECOVERY_MINUTES - EPS):
            confirmed = clean_start + previous.RECOVERY_MINUTES
            answer["rescue_confirmed_minute"] = confirmed
            answer["minutes_to_rescue_confirmation"] = confirmed - first
            answer["rescue_confirmed"] = True
    if answer["rescue_confirmed"] is True:
        sustained = answer["rescue_later_recurrence_minute"] is None
        answer["rescue_sustained_to_observation_end"] = sustained
        answer["rescue_sustained_to_requested_end"] = (
            sustained if observed_until >= requested_end - EPS else None)
        answer["rescue_status"] = ("confirmed_to_observed_end" if sustained
                                   else "confirmed_then_recurred")
        return answer
    if first is not None:
        if observed_until < requested_end - EPS:
            answer["rescue_status"] = "unknown_after_truncation"
        else:
            answer["rescue_confirmed"] = False
            answer["rescue_status"] = "not_confirmed_in_complete_horizon"
    return answer


def event_detail(events):
    counts = Counter(event.get("kind") for event in events)
    trips = [event for event in events if event.get("kind") == "protection_trip"
             and str(event.get("device", "")).startswith("GT_")]
    return {"gt_protection_trip_count": len(trips),
            "first_gt_protection_trip_minute":
                min((float(event["time_minute"]) for event in trips), default=None),
            "minimum_output_interlock_count": counts["minimum_output_interlock"],
            "no_export_interlock_count": counts["no_export_interlock"],
            "forced_capacity_or_permission_clamp_count":
                counts["forced_capacity_or_permission_clamp"],
            "first_forced_clamp_minute": min((float(event["time_minute"])
                for event in events if event.get("kind") ==
                "forced_capacity_or_permission_clamp"), default=None)}


def post_event_service(segments, *, event_end, observed_until, requested_end):
    """Expose lower-priority service after events without inventing a new pass gate."""
    answer = {"post_event_observation_complete": None}
    for name in SERVICE_NAMES:
        answer["post_event_observed_" + name] = None
        answer["post_event_terminal_" + name.replace("ENS_kWh", "unserved_kW")] = None
    if event_end is None:
        return answer
    answer["post_event_observation_complete"] = observed_until >= requested_end - EPS
    if event_end >= observed_until - EPS:
        return answer
    ens = [[0.0] * 3 for _ in range(4)]
    for segment in segments:
        dt = previous._overlap(segment, event_end, observed_until)
        if dt <= 0:
            continue
        for p in range(4):
            for c in range(3):
                ens[p][c] += float(segment["unserved_kW"][p][c]) * dt / 60.0
    terminal = segments[-1]["unserved_kW"]
    for p in range(4):
        for c, letter in enumerate("ehc"):
            name = f"P{p}_{letter}_ENS_kWh"
            answer["post_event_observed_" + name] = ens[p][c]
            answer[f"post_event_terminal_P{p}_{letter}_unserved_kW"] = float(terminal[p][c])
    return answer


def execution_detail(scene, payload):
    """Summarize physically executed sources and the command time chain."""
    segments = payload.get("segments", [])
    starts = [float(event["start_minute"])
              for event in scene.get("exogenous_events", [])
              if event.get("start_minute") is not None]
    first_event = min(starts, default=None)

    def source(action):
        if action is None:
            return None
        return {"gt_kW": sum(float(x) for x in action.get("gt_electric_kW", [])),
                "grid_kW": float(action.get("grid_import_kW", 0.0)),
                "diesel_kW": float(action.get("diesel_kW", 0.0)),
                "boiler_steam_lbph": float(action.get("boiler_steam_lbph", 0.0)),
                "hrsg_steam_lbph": float(action.get("hrsg_steam_lbph", 0.0)),
                "absorption_cooling_kW": float(action.get("absorption_cooling_kW", 0.0))}

    pre = None if first_event is None else next(
        (seg for seg in reversed(segments)
         if float(seg["end_minute"]) <= first_event + EPS), None)
    at_event = None if first_event is None else next(
        (seg for seg in segments if float(seg["start_minute"]) <= first_event + EPS
         and float(seg["end_minute"]) > first_event + EPS), None)
    answer = {"first_exogenous_start_minute": first_event}
    for prefix, segment in (("pre_event", pre), ("first_event", at_event),
                            ("last_observed", segments[-1] if segments else None)):
        dispatch = source(None if segment is None else segment.get("action"))
        for field in ("gt_kW", "grid_kW", "diesel_kW", "boiler_steam_lbph",
                      "hrsg_steam_lbph", "absorption_cooling_kW"):
            answer[prefix + "_executed_" + field] = (
                None if dispatch is None else dispatch[field])
    for field in ("gt_kW", "grid_kW", "diesel_kW", "boiler_steam_lbph",
                  "hrsg_steam_lbph", "absorption_cooling_kW"):
        values = [source(seg.get("action"))[field] for seg in segments
                  if seg.get("action") is not None]
        answer["max_executed_" + field] = max(values, default=None)
    for kind in ("command_generated", "command_arrived", "command_executed"):
        answer["first_post_event_" + kind + "_minute"] = (
            min((float(event["time_minute"]) for event in payload.get("events", [])
                 if event.get("kind") == kind and
                 first_event is not None and
                 float(event["time_minute"]) >= first_event - EPS), default=None))
    summary = payload.get("summary", {})
    answer["projection_changed_steps"] = summary.get("projection_changed_steps")
    answer["strategy_fallback_steps"] = summary.get("strategy_fallback_steps")
    final_state = segments[-1].get("post_state", {}) if segments else {}
    final_segment = segments[-1] if segments else {}
    availability = final_segment.get("availability", {})
    device = scene.get("initiating_device") or next(
        (event.get("device") for event in scene.get("exogenous_events", [])
         if event.get("kind") == "device_fault"), None)
    extra_faulted = final_state.get("extra_faulted")
    answer["final_observed_at_minute"] = final_segment.get("end_minute")
    answer["final_availability_json"] = availability
    answer["final_GT_availability_json"] = {
        key: value for key, value in availability.items() if key.startswith("GT_")}
    answer["final_GT_previous_output_kW_json"] = final_state.get("gt_prev")
    answer["final_GT_overload_minutes_json"] = final_state.get("overload_minutes")
    answer["final_diesel_status"] = final_state.get("diesel_status")
    answer["final_initiating_device"] = device
    answer["final_initiating_device_availability"] = (
        None if device is None else availability.get(device))
    answer["final_initiating_device_extra_faulted"] = (
        None if device is None or extra_faulted is None else device in extra_faulted)
    answer["final_diesel_energy_remaining_kWh"] = final_state.get(
        "diesel_energy_remaining_kWh")
    answer["final_extra_faulted_devices_json"] = extra_faulted
    return answer


def _scene_parameters(scene):
    faults = [e for e in scene.get("exogenous_events", [])
              if e.get("kind") == "device_fault"]
    shocks = [e for e in scene.get("exogenous_events", [])
              if e.get("kind") in ("load_shock", "demand_shock")]
    fault = faults[0] if len(faults) == 1 else {}
    shock = shocks[0] if len(shocks) == 1 else {}
    return {"design_cell_id": scene.get("design_cell_id"),
            "design_axis": scene.get("design_axis"),
            "design_level": scene.get("design_level"),
            "source_day_index": scene.get("source_day_index"),
            "season_quarter": scene.get("season_quarter"),
            "demand_background": scene.get("demand_background"),
            "shock_mode": scene.get("shock_mode"),
            "stratum": scene.get("stratum"),
            "response_delay_minutes": scene.get("response_delay_minutes"),
            "fault_device": fault.get("device", scene.get("initiating_device")),
            "fault_availability": fault.get("availability"),
            "fault_start_minute": fault.get("start_minute"),
            "fault_end_minute": fault.get("end_minute"),
            "fault_duration_minutes": (float(fault["end_minute"]) -
                                        float(fault["start_minute"]))
                if fault else None,
            "shock_start_minute": shock.get("start_minute"),
            "shock_end_minute": shock.get("end_minute"),
            "shock_duration_minutes": (float(shock["end_minute"]) -
                                        float(shock["start_minute"]))
                if shock else None,
            "shock_json": scene.get("shock", {}),
            "shock_by_priority_json": scene.get("shock_by_priority", {}),
            "enable_propagation": scene.get("enable_propagation"),
            "base_state_id": scene.get("base_state_id")}


def _observed_fault_events(payload):
    return [{"kind": event.get("kind"),
             "time_minute": event.get("time_minute"),
             "details": event.get("details")}
            for event in payload.get("events", [])
            if event.get("kind") in ("exogenous_fault_start",
                                       "exogenous_fault_end")]


def trajectory_row(scene, policy_id, payload, result, raw_path):
    # The v7 summarizer enumerates N4/L3/L4a/L4b only. A shadow is physically
    # a normal N4 trajectory; adapt only its analysis label, not its raw scene.
    if scene.get("analysis_class") == "N4_clockmatched":
        summary_scene = {**scene, "analysis_class": "N4"}
    else:
        summary_scene = scene
    row, _ = previous.summarize_trajectory(summary_scene, policy_id, payload, result,
                                           raw_path=raw_path)
    row["analysis_class"] = scene["analysis_class"]
    if scene["analysis_class"] == "N4_clockmatched":
        row["reference_class"] = None
        row["shadow_event_clock_only"] = scene.get("shadow_event_clock_only")
        row["scenario_shock_clock_start_minute"] = scene.get("shock_start_minute")
        row["scenario_fault_clock_start_minute"] = scene.get("fault_start_minute")
    row.update(_scene_parameters(scene))
    row["observed_fault_events_json"] = _observed_fault_events(payload)
    row.update(event_detail(payload.get("events", [])))
    row.update(first_failure_rescue(previous._ordered_segments(payload),
                                    observed_until=row["observed_until_minute"],
                                    requested_end=row["requested_end_minute"]))
    row.update(post_event_service(payload["segments"],
                                  event_end=row["event_end_minute"],
                                  observed_until=row["observed_until_minute"],
                                  requested_end=row["requested_end_minute"]))
    row.update(execution_detail(scene, payload))
    return row


def _event_signature(scene, kinds):
    return sorted(json.dumps(event, ensure_ascii=False, sort_keys=True)
                  for event in scene.get("exogenous_events", [])
                  if event.get("kind") in kinds)


def _assert_group(group, scenes, states, policy_ids):
    ids = group.get("scene_ids")
    if not isinstance(ids, dict) or set(ids) != set(CELL_NAMES):
        raise ValueError("Four-cell group requires N4/L3/L4a/L4b scene_ids")
    if len(set(ids.values())) != 4 or any(sid not in scenes for sid in ids.values()):
        raise ValueError("Four-cell group references missing or duplicate scene")
    cells = {name: scenes[sid] for name, sid in ids.items()}
    for name, scene in cells.items():
        if scene.get("analysis_class") != name:
            raise ValueError("Four-cell class differs from scene_ids key")
        if scene.get("root_family_id") != group.get("root_family_id"):
            raise ValueError("Four-cell root differs")
        if scene.get("base_state_id") != group.get("base_state_id"):
            raise ValueError("Four-cell initial state differs")
        if scene.get("enable_propagation") is not True:
            raise ValueError("Four-cell propagation differs")
        if scene.get("response_delay_minutes") != group.get("response_delay_minutes"):
            raise ValueError("Four-cell response delay differs")
    for field in ("start_hour", "grid_limit_kW", "pre_event_grid_limit_kW"):
        if len({json.dumps(scene.get(field), sort_keys=True) for scene in cells.values()}) != 1:
            raise ValueError("Four-cell " + field + " differs")
    fault = {name: _event_signature(scene, {"device_fault"})
             for name, scene in cells.items()}
    shock = {name: _event_signature(scene, {"load_shock", "demand_shock"})
             for name, scene in cells.items()}
    if fault["N4"] or fault["L4a"] or len(fault["L3"]) != 1 or fault["L3"] != fault["L4b"]:
        raise ValueError("Four-cell fault event or absolute clock differs")
    if shock["N4"] or shock["L3"] or len(shock["L4a"]) != 1 or shock["L4a"] != shock["L4b"]:
        raise ValueError("Four-cell shock event or absolute clock differs")
    f = next(e for e in cells["L3"].get("exogenous_events", [])
             if e.get("kind") == "device_fault")
    s = next(e for e in cells["L4a"].get("exogenous_events", [])
             if e.get("kind") in ("load_shock", "demand_shock"))
    for field, observed in (("fault_start_minute", f["start_minute"]),
                            ("shock_start_minute", s["start_minute"]),
                            ("fault_duration_minutes", float(f["end_minute"]) - float(f["start_minute"])),
                            ("shock_duration_minutes", float(s["end_minute"]) - float(s["start_minute"])),
                            ("fault_availability", f["availability"])):
        if field in group and group[field] != observed:
            raise ValueError("Four-cell registered " + field + " differs")
    for policy_id in policy_ids:
        state_values = [states[(ids[name], policy_id)] for name in CELL_NAMES]
        if len(set(state_values)) != 1:
            raise ValueError("Four-cell raw initial state differs")
    return ids


def _compact_profile(payload):
    """Retain only pre-event comparability evidence, not entire raw trajectories."""
    segments = []
    for seg in payload.get("segments", []):
        pre, post = seg.get("pre_state", {}), seg.get("post_state", {})
        segments.append({"start_minute": seg["start_minute"],
                         "end_minute": seg["end_minute"],
                         "action": seg.get("action"),
                         "loads_kW": seg.get("loads_kW"),
                         "p0_unserved_kW": (seg.get("unserved_kW") or [None])[0],
                         "p0_failed": seg.get("classification", {}).get(
                             "p0_guarantee_failure"),
                         "temperature": seg.get("temperature"),
                         "gt_availability": {k: v for k, v in
                             seg.get("availability", {}).items() if k.startswith("GT_")},
                         "pre_gt_overload_minutes": pre.get("overload_minutes"),
                         "post_gt_overload_minutes": post.get("overload_minutes"),
                         "pre_extra_faulted": pre.get("extra_faulted"),
                         "post_extra_faulted": post.get("extra_faulted"),
                         "pre_diesel_energy_kWh": pre.get("diesel_energy_remaining_kWh"),
                         "post_diesel_energy_kWh": post.get("diesel_energy_remaining_kWh"),
                         "protection_stress": seg.get("protection_stress")})
    events = []
    relevant = {"command_generated", "command_arrived", "command_executed",
                "protection_trip", "minimum_output_interlock",
                "no_export_interlock", "forced_capacity_or_permission_clamp"}
    for event in payload.get("events", []):
        if event.get("kind") not in relevant:
            continue
        command = event.get("command") or {}
        events.append({"kind": event.get("kind"),
                       "time_minute": event.get("time_minute"),
                       "device": event.get("device"),
                       "cause_class": event.get("cause_class"),
                       "generated_dispatch": command.get("dispatch"),
                       "generated_arrival_minute": command.get("arrival_minute"),
                       "executed_dispatch": event.get("executed_dispatch"),
                       "requested_dispatch": event.get("requested_dispatch"),
                       "reason": event.get("reason")})
    decisions = [{key: item[key] for key in LEGAL_OBSERVATION_FIELDS
                  if key in item} for item in payload.get("decisions", [])]
    return {"segments": segments, "events": events, "decisions": decisions}


def _rounded(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_rounded(x) for x in value]
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    return value


def _pre_event_signature(profile, cutoff):
    if profile is None:
        return None, 0
    if isinstance(profile, list):
        profile = {"segments": profile, "events": []}
    segments = [s for s in profile["segments"]
                if float(s["end_minute"]) <= cutoff + EPS]
    if (not segments or abs(float(segments[0]["start_minute"])) > EPS or
            abs(float(segments[-1]["end_minute"]) - cutoff) > EPS or
            any(abs(float(a["end_minute"]) - float(b["start_minute"])) > EPS
                for a, b in zip(segments, segments[1:]))):
        return None, len(segments)
    events = [e for e in profile["events"]
              if float(e["time_minute"]) < cutoff - EPS]
    signature = json.dumps(_rounded({"segments": segments, "events": events}),
                           ensure_ascii=False, sort_keys=True)
    return signature, len(segments)


def _pre_event_legal_signature(profile, cutoff, step_minutes=15.0):
    """Compare only the legal observation passed to policy, never labels."""
    if not isinstance(profile, dict) or "decisions" not in profile:
        return None, 0
    decisions = [item for item in profile["decisions"]
                 if item.get("decision_time_min") is not None and
                 float(item["decision_time_min"]) < cutoff - EPS]
    expected = [float(i * step_minutes) for i in
                range(int(round(cutoff / step_minutes)))]
    observed = [float(item["decision_time_min"]) for item in decisions]
    if (abs(len(expected) * step_minutes - cutoff) > EPS or
            len(observed) != len(expected) or
            any(abs(a - b) > EPS for a, b in zip(observed, expected)) or
            any(set(item) != set(LEGAL_OBSERVATION_FIELDS) for item in decisions)):
        return None, len(decisions)
    return json.dumps(_rounded(decisions), ensure_ascii=False,
                      sort_keys=True), len(decisions)


def _propagation_gate(scene):
    """Mirror the clock used by state_transition for delay and protection."""
    stage = scene.get("stage")
    if stage not in (3, 4):
        return None
    kind = "fault" if stage == 3 else "shock"
    start = float(scene.get(kind + "_start_minute", 0.0))
    delay = float(scene.get("response_delay_minutes", 0.0))
    return start, start + delay


def _clockmatched_lookup(clockmatched):
    if clockmatched is None:
        return {}
    groups = clockmatched.get("groups", [])
    lookup = {}
    for group in groups:
        key = group.get("design_cell_id")
        if not key or key in lookup:
            raise ValueError("Duplicate or missing clockmatched design_cell_id")
        lookup[key] = group
    return lookup


def _clockmatched_cell(group, ids, primary_scenes, primary_states,
                       policy_id, clockmatched, lookup):
    """Validate supplemental registration and every effective model gate."""
    registered = lookup.get(group["design_cell_id"])
    if registered is None:
        return None, "clockmatched_N4_missing", None
    sid = registered.get("clockmatched_scene_id")
    scene = (clockmatched.get("scenes") or {}).get(sid)
    if scene is None or (sid, policy_id) not in (clockmatched.get("rows") or {}):
        return sid, "clockmatched_N4_missing", None
    if (registered.get("original_scene_ids") != ids or
            registered.get("root_family_id") != group.get("root_family_id") or
            any(registered.get(k) != group.get(k) for k in
                ("design_axis", "design_level") if k in group)):
        return sid, "clockmatched_registration_differs", None
    normal = primary_scenes[ids["N4"]]
    if (scene.get("analysis_class") != "N4_clockmatched" or
            scene.get("reference_role") != "no_load_no_fault_clockmatched_control" or
            scene.get("shadow_event_clock_only") is not True or
            scene.get("exogenous_events") != [] or scene.get("faults") != {} or
            scene.get("shock") != {} or scene.get("shock_by_priority") != {} or
            scene.get("enable_propagation") is not True or
            any(scene.get(field) != normal.get(field) for field in
                ("root_family_id", "base_state_id", "start_hour", "stage",
                 "step_minutes", "duration_steps", "grid_limit_kW",
                 "pre_event_grid_limit_kW", "response_delay_minutes"))):
        return sid, "clockmatched_scene_differs", None
    if ((sid, policy_id) not in (clockmatched.get("states") or {}) or
            clockmatched["states"][(sid, policy_id)] !=
            primary_states[(ids["N4"], policy_id)]):
        return sid, "clockmatched_initial_state_differs", None
    for name in ("shock_start_minute", "fault_start_minute",
                 "shock_duration_minutes", "fault_duration_minutes",
                 "response_delay_minutes"):
        if (name not in scene or name not in registered or
                scene[name] != registered[name] or
                (name in group and group[name] != registered[name])):
            return sid, "clockmatched_time_registration_differs", None
    gates = {name: _propagation_gate(primary_scenes[ids[name]])
             for name in ("L3", "L4a", "L4b")}
    gates["N4"] = _propagation_gate(scene)
    if (any(gate is None for gate in gates.values()) or
            len(set(gates.values())) != 1):
        return sid, "propagation_gate_clock_differs", gates
    return sid, None, gates


def four_cell_interactions(groups, scenes, rows, states, policy_ids, *,
                           profiles=None, clockmatched=None):
    """Use an audited, clock-matched normal shadow for pure four-cell arithmetic.

    The original N4 remains in the ordinary trajectory and policy tables. The
    optional clockmatched context must be loaded from a separately passed raw
    audit by the entrypoint; without it every pure interaction stays unknown.
    """
    output = []
    seen = set()
    lookup = _clockmatched_lookup(clockmatched)
    for group in groups:
        cell_id = group.get("design_cell_id")
        if not cell_id or cell_id in seen:
            raise ValueError("Four-cell design_cell_id missing or duplicate")
        seen.add(cell_id)
        ids = _assert_group(group, scenes, states, policy_ids)
        fault_start = next(float(e["start_minute"]) for e in
                           scenes[ids["L3"]]["exogenous_events"]
                           if e.get("kind") == "device_fault")
        shock_start = next(float(e["start_minute"]) for e in
                           scenes[ids["L4a"]]["exogenous_events"]
                           if e.get("kind") in ("load_shock", "demand_shock"))
        cutoff = min(fault_start, shock_start)
        for policy_id in policy_ids:
            shadow_id, invalid, gates = _clockmatched_cell(
                group, ids, scenes, states, policy_id, clockmatched, lookup)
            used_ids = dict(ids)
            shadow_available = (shadow_id is not None and clockmatched is not None
                                and (shadow_id, policy_id) in
                                clockmatched.get("rows", {}))
            if shadow_available:
                used_ids["N4"] = shadow_id
            cells = {name: ((clockmatched.get("rows") or {}).get((sid, policy_id))
                            if name == "N4" and sid != ids["N4"] else
                            rows.get((sid, policy_id)))
                     for name, sid in used_ids.items()}
            if not shadow_available:
                cells["N4"] = None
            result = {key: value for key, value in group.items() if key != "scene_ids"}
            result.update(policy_id=policy_id, matching_status="matched",
                          **{name + "_scenario_id": sid for name, sid in used_ids.items()})
            result["N4_scenario_id"] = shadow_id if shadow_available else None
            result["normal_N4_scenario_id"] = ids["N4"]
            result["clockmatched_N4_scenario_id"] = shadow_id
            original_gate = _propagation_gate(scenes[ids["N4"]])
            result["original_N4_gate_semantics_mismatch"] = (
                original_gate is None or abs(original_gate[0] - cutoff) > EPS)
            result["gate_semantics_mismatch"] = (
                None if gates is None else len(set(gates.values())) != 1)
            for name in CELL_NAMES:
                gate = None if gates is None else gates.get(name)
                result[name + "_propagation_gate_start_minute"] = (
                    None if gate is None else gate[0])
                result[name + "_response_release_minute"] = (
                    None if gate is None else gate[1])
            result["pre_event_cutoff_minute"] = cutoff
            signatures = []
            counts = []
            for name in CELL_NAMES:
                source = (clockmatched.get("profiles") or {}) if (
                    clockmatched is not None and name == "N4" and
                    used_ids[name] != ids["N4"]) else profiles
                value, count = _pre_event_signature(
                    None if source is None else source.get((used_ids[name], policy_id)),
                    cutoff)
                signatures.append(value)
                counts.append(count)
            result["pre_event_segment_count"] = min(counts)
            result["pre_event_raw_profile_equal"] = (
                all(value is not None for value in signatures) and
                len(set(signatures)) == 1)
            legal_signatures, legal_counts = [], []
            for name in CELL_NAMES:
                source = (clockmatched.get("profiles") or {}) if (
                    clockmatched is not None and name == "N4" and
                    used_ids[name] != ids["N4"]) else profiles
                value, count = _pre_event_legal_signature(
                    None if source is None else source.get((used_ids[name], policy_id)),
                    cutoff, scenes[ids[name]].get("step_minutes", 15.0))
                legal_signatures.append(value)
                legal_counts.append(count)
            result["pre_event_legal_decision_count"] = min(legal_counts)
            result["pre_event_legal_observation_equal"] = (
                all(value is not None for value in legal_signatures) and
                len(set(legal_signatures)) == 1)
            invalid = (invalid or
                       ("pre_event_raw_profile_missing" if any(value is None for value in signatures)
                        else "pre_event_raw_profile_differs" if len(set(signatures)) != 1
                        else "pre_event_legal_observation_missing" if
                        any(value is None for value in legal_signatures)
                        else "pre_event_legal_observation_differs" if
                        len(set(legal_signatures)) != 1
                        else None))
            result["invalid_reason"] = invalid
            if invalid:
                result["matching_status"] = "invalid"
            result["causal_interpretation"] = (
                "model-factorial arithmetic with clockmatched N4; pre-event physical, legal observation and model gates verified"
                if invalid is None else "no pure interaction: " + invalid)
            for window in CONTRAST_WINDOWS:
                complete = all(cells[name] is not None and
                               cells[name][window + "_full_horizon"]
                               for name in CELL_NAMES)
                result[window + "_complete_four_cell"] = complete
                result[window + "_unknown_cell_count"] = sum(
                    cells[name] is None or not cells[name][window + "_full_horizon"]
                    for name in CELL_NAMES)
                for metric in INTERACTION_NAMES:
                    field = window + "_" + metric
                    for name in CELL_NAMES:
                        result[field + "_" + name] = (None if cells[name] is None
                                                       else cells[name].get(field))
                    values = [None if cells[name] is None else cells[name].get(field)
                              for name in CELL_NAMES]
                    result[field + "_interaction"] = (
                        float(values[3]) - float(values[2]) -
                        float(values[1]) + float(values[0])
                        if invalid is None and complete and
                        all(v is not None for v in values) else None)
            output.append(result)
    return output


def policy_deltas(rows, policy_ids):
    by_scene = defaultdict(dict)
    for row in rows:
        sid, pid = row["scenario_id"], row["policy_id"]
        if pid in by_scene[sid]:
            raise ValueError("Duplicate scenario/policy row")
        by_scene[sid][pid] = row
    out = []
    for sid, policies in sorted(by_scene.items()):
        secure = policies.get("secure_milp")
        if secure is None:
            raise ValueError("Secure baseline missing for " + sid)
        for pid in policy_ids:
            if pid == "secure_milp":
                continue
            candidate = policies.get(pid)
            if candidate is None:
                raise ValueError("Policy missing for " + sid + "/" + pid)
            record = {"scenario_id": sid, "root_family_id": secure["root_family_id"],
                      "analysis_class": secure["analysis_class"],
                      "design_cell_id": secure.get("design_cell_id"),
                      "candidate_policy_id": pid, "reference_policy_id": "secure_milp",
                      "candidate_observed_until_minute": candidate["observed_until_minute"],
                      "secure_observed_until_minute": secure["observed_until_minute"]}
            count_comparable = (abs(candidate["observed_until_minute"] -
                                    secure["observed_until_minute"]) <= EPS)
            record["event_count_delta_comparable"] = count_comparable
            for window in CONTRAST_WINDOWS:
                complete = bool(secure[window + "_full_horizon"] and
                                candidate[window + "_full_horizon"])
                record[window + "_complete_pair"] = complete
                for metric in previous.TARGET_NAMES:
                    if metric == "P0_failure":
                        continue
                    key = window + "_" + metric
                    a, b = candidate.get(key), secure.get(key)
                    record[key + "_candidate"] = a
                    record[key + "_secure"] = b
                    record[key + "_delta"] = float(a) - float(b) if complete and a is not None and b is not None else None
                key = window + "_P0_failure"
                a, b = candidate.get(key), secure.get(key)
                record[key + "_candidate"] = a
                record[key + "_secure"] = b
                record[key + "_known_delta"] = int(a) - int(b) if a is not None and b is not None else None
            for key in ("gt_protection_trip_count", "minimum_output_interlock_count",
                        "no_export_interlock_count", "requested_decision_count",
                        "arrived_command_count", "executed_command_count"):
                record[key + "_candidate"] = candidate[key]
                record[key + "_secure"] = secure[key]
                record[key + "_delta"] = (candidate[key] - secure[key]
                                           if count_comparable else None)
            out.append(record)
    return out


def _assert_fault_cross_group(group, scenes, states, policy_ids):
    ids = group.get("scene_ids")
    if not isinstance(ids, dict) or set(ids) != {"L1", "L3"} or len(set(ids.values())) != 2:
        raise ValueError("Fault-cross group needs distinct L1/L3 scene_ids")
    if any(sid not in scenes for sid in ids.values()):
        raise ValueError("Fault-cross scene missing")
    l1, l3 = (scenes[ids[name]] for name in ("L1", "L3"))
    for name, scene in (("L1", l1), ("L3", l3)):
        if (scene.get("analysis_class") != name or
                scene.get("root_family_id") != group.get("root_family_id") or
                scene.get("base_state_id") != group.get("base_state_id") or
                scene.get("stage") != (1 if name == "L1" else 3) or
                scene.get("enable_propagation") is not (name == "L3")):
            raise ValueError("Fault-cross class, root, initial state or propagation differs")
    for field in ("start_hour", "step_minutes", "duration_steps",
                  "response_delay_minutes", "grid_limit_kW",
                  "pre_event_grid_limit_kW", "faults", "shock",
                  "shock_by_priority"):
        if l1.get(field) != l3.get(field):
            raise ValueError("Fault-cross " + field + " differs")
    event_l1 = _event_signature(l1, {"device_fault"})
    event_l3 = _event_signature(l3, {"device_fault"})
    if len(event_l1) != 1 or event_l1 != event_l3:
        raise ValueError("Fault-cross device, severity or fault clock differs")
    event = next(e for e in l1["exogenous_events"]
                 if e.get("kind") == "device_fault")
    expected = {"initiating_device": event.get("device"),
                "availability": event.get("availability"),
                "fault_start_minute": event.get("start_minute"),
                "fault_duration_minutes": (float(event["end_minute"]) -
                                           float(event["start_minute"]))}
    if any(group.get(field) != value for field, value in expected.items()):
        raise ValueError("Fault-cross registered device, availability or clock differs")
    for pid in policy_ids:
        if states[(ids["L1"], pid)] != states[(ids["L3"], pid)]:
            raise ValueError("Fault-cross raw initial states differ")
    return ids


def fault_cross_deltas(groups, scenes, rows, states, policy_ids, *, profiles=None):
    """Describe observed L3 minus L1 on identical faults and seen roots."""
    out, seen = [], set()
    for group in groups:
        cell_id = group.get("design_cell_id")
        if not cell_id or cell_id in seen:
            raise ValueError("Fault-cross design_cell_id missing or duplicate")
        seen.add(cell_id)
        ids = _assert_fault_cross_group(group, scenes, states, policy_ids)
        for pid in policy_ids:
            l1, l3 = (rows[(ids[name], pid)] for name in ("L1", "L3"))
            record = {k: v for k, v in group.items() if k != "scene_ids"}
            record.update(policy_id=pid, L1_scenario_id=ids["L1"],
                          L3_scenario_id=ids["L3"],
                          contrast_interpretation="descriptive_propagation_mode_difference",
                          L1_observed_until_minute=l1["observed_until_minute"],
                          L3_observed_until_minute=l3["observed_until_minute"])
            expected_fault = [
                {"kind": kind, "time_minute": minute,
                 "details": {group["initiating_device"]: group["availability"]}}
                for kind, minute in (("exogenous_fault_start",
                                      group["fault_start_minute"]),
                                     ("exogenous_fault_end",
                                      group["fault_start_minute"] +
                                      group["fault_duration_minutes"]))]
            raw_l1 = l1.get("observed_fault_events_json")
            raw_l3 = l3.get("observed_fault_events_json")
            trace_matched = raw_l1 == expected_fault and raw_l3 == expected_fault
            trace_status = ("matched" if trace_matched else
                            "unknown_truncated" if not (
                                l1["full_full_horizon"] and l3["full_full_horizon"])
                            else "invalid")
            record["raw_fault_event_trace_status"] = trace_status
            record["raw_fault_events_L1_json"] = raw_l1
            record["raw_fault_events_L3_json"] = raw_l3
            cutoff = float(group["fault_start_minute"])
            p1 = None if profiles is None else profiles.get((ids["L1"], pid))
            p3 = None if profiles is None else profiles.get((ids["L3"], pid))
            physical_l1, physical_count_l1 = _pre_event_signature(p1, cutoff)
            physical_l3, physical_count_l3 = _pre_event_signature(p3, cutoff)
            legal_l1, legal_count_l1 = _pre_event_legal_signature(
                p1, cutoff, scenes[ids["L1"]].get("step_minutes", 15.0))
            legal_l3, legal_count_l3 = _pre_event_legal_signature(
                p3, cutoff, scenes[ids["L3"]].get("step_minutes", 15.0))
            record["pre_event_segment_count"] = min(physical_count_l1,
                                                    physical_count_l3)
            record["pre_event_legal_decision_count"] = min(legal_count_l1,
                                                            legal_count_l3)
            record["pre_event_raw_profile_equal"] = (physical_l1 is not None
                and physical_l3 is not None and physical_l1 == physical_l3)
            record["pre_event_legal_observation_equal"] = (legal_l1 is not None
                and legal_l3 is not None and legal_l1 == legal_l3)
            reason = ("raw_fault_event_trace_" + trace_status
                      if not trace_matched else
                      "pre_event_raw_profile_missing" if
                      physical_l1 is None or physical_l3 is None else
                      "pre_event_raw_profile_differs" if
                      physical_l1 != physical_l3 else
                      "pre_event_legal_observation_missing" if
                      legal_l1 is None or legal_l3 is None else
                      "pre_event_legal_observation_differs" if
                      legal_l1 != legal_l3 else None)
            record["matching_status"] = "matched" if reason is None else "invalid"
            record["invalid_reason"] = reason
            comparable = (reason is None and
                          abs(l1["observed_until_minute"] -
                              l3["observed_until_minute"]) <= EPS)
            record["event_count_delta_comparable"] = comparable
            for field, alias in (("gt_protection_trip_count", "real_GT_trip_count"),
                                 ("minimum_output_interlock_count", "minimum_output_interlock_count"),
                                 ("no_export_interlock_count", "no_export_interlock_count"),
                                 ("forced_capacity_or_permission_clamp_count", "forced_clamp_count")):
                a, b = l1.get(field), l3.get(field)
                record[alias + "_L1"] = a
                record[alias + "_L3"] = b
                record[alias + "_L3_minus_L1"] = (
                    b - a if comparable and a is not None and b is not None else None)
            for field in ("pre_event_executed_gt_kW", "first_event_executed_gt_kW",
                          "first_event_executed_diesel_kW", "max_executed_diesel_kW",
                          "pre_event_executed_boiler_steam_lbph",
                          "pre_event_executed_hrsg_steam_lbph"):
                a, b = l1.get(field), l3.get(field)
                record[field + "_L1"] = a
                record[field + "_L3"] = b
                record[field + "_L3_minus_L1"] = (
                    b - a if comparable and a is not None and b is not None
                    else None)
            for window in CONTRAST_WINDOWS:
                complete = bool(l1[window + "_full_horizon"] and
                                l3[window + "_full_horizon"])
                record[window + "_complete_pair"] = complete
                record[window + "_valid_complete_pair"] = (
                    complete and reason is None)
                for metric in previous.TARGET_NAMES:
                    if metric == "P0_failure":
                        continue
                    field = window + "_" + metric
                    a, b = l1.get(field), l3.get(field)
                    record[field + "_L1"] = a
                    record[field + "_L3"] = b
                    record[field + "_L3_minus_L1"] = (
                        float(b) - float(a) if complete and reason is None and
                        a is not None
                        and b is not None else None)
                field = window + "_P0_failure"
                a, b = l1.get(field), l3.get(field)
                record[field + "_L1"] = a
                record[field + "_L3"] = b
                record[field + "_known_L3_minus_L1"] = (
                    int(b) - int(a) if reason is None and a is not None and
                    b is not None else None)
            out.append(record)
    return out


def sensitivity_paired_deltas(groups, scenes, rows, states, policy_ids):
    """Pair each one-axis design point to its registered main design point."""
    baseline = defaultdict(list)
    for group in groups:
        if group.get("design_axis") == "main":
            key = (group.get("root_family_id"), group.get("shock_mode"),
                   group.get("stratum"))
            baseline[key].append(group)
    out = []
    for variant in groups:
        axis = variant.get("design_axis")
        if axis == "main":
            continue
        key = (variant.get("root_family_id"), variant.get("shock_mode"),
               variant.get("stratum"))
        main_candidates = baseline.get(key, [])
        main = main_candidates[0] if len(main_candidates) == 1 else None
        axis_field = SENSITIVITY_AXES.get(axis)
        changed = ([] if main is None else [field for field in
                   SENSITIVITY_CONTEXT_FIELDS
                   if main.get(field) != variant.get(field)])
        # The four registered dimensions include fault duration as a fixed
        # context; exactly one approved axis may change in a sensitivity pair.
        design_reason = ("main_match_missing_or_duplicate" if main is None else
                         "unregistered_sensitivity_axis" if axis_field is None else
                         "not_single_axis_change" if changed != [axis_field] else
                         "initial_state_registration_differs" if
                         main.get("base_state_id") != variant.get("base_state_id") else
                         None)
        for name in CELL_NAMES:
            sid_main = None if main is None else main.get("scene_ids", {}).get(name)
            sid_variant = variant.get("scene_ids", {}).get(name)
            for pid in policy_ids:
                a = rows.get((sid_main, pid)) if sid_main else None
                b = rows.get((sid_variant, pid)) if sid_variant else None
                reason = design_reason
                if reason is None and (sid_main not in scenes or sid_variant not in scenes or
                                       a is None or b is None):
                    reason = "registered_sensitivity_scene_or_raw_missing"
                if reason is None:
                    first, second = scenes[sid_main], scenes[sid_variant]
                    if (first.get("analysis_class") != name or
                            second.get("analysis_class") != name or
                            any(first.get(field) != second.get(field) for field in
                                ("root_family_id", "base_state_id", "start_hour",
                                 "step_minutes", "duration_steps", "grid_limit_kW",
                                 "pre_event_grid_limit_kW", "source_day_index")) or
                            states.get((sid_main, pid)) != states.get((sid_variant, pid))):
                        reason = "sensitivity_scene_or_raw_initial_state_differs"
                record = {"root_family_id": variant.get("root_family_id"),
                          "season_quarter": variant.get("season_quarter"),
                          "shock_mode": variant.get("shock_mode"),
                          "stratum": variant.get("stratum"),
                          "design_axis": axis,
                          "design_level": variant.get("design_level"),
                          "main_design_cell_id": None if main is None else
                              main.get("design_cell_id"),
                          "sensitivity_design_cell_id": variant.get("design_cell_id"),
                          "analysis_class": name, "policy_id": pid,
                          "main_scene_id": sid_main,
                          "sensitivity_scene_id": sid_variant,
                          "parameter_name": axis_field,
                          "parameter_main_value": None if main is None or
                              axis_field is None else main.get(axis_field),
                          "parameter_sensitivity_value": None if axis_field is None
                              else variant.get(axis_field),
                          "matching_status": "matched" if reason is None else "invalid",
                          "invalid_reason": reason,
                          "interpretation": "two tested model points only; not a continuous threshold"}
                base_value, variant_value = (record["parameter_main_value"],
                                             record["parameter_sensitivity_value"])
                record["parameter_change"] = (
                    float(variant_value) - float(base_value)
                    if base_value is not None and variant_value is not None else None)
                record["axis_has_matching_physical_event"] = (
                    True if axis == "delay" else
                    name in ("L4a", "L4b") if axis == "duration" else
                    name in ("L3", "L4b") if axis == "availability" else None)
                record["main_observed_until_minute"] = (
                    None if a is None else a.get("observed_until_minute"))
                record["sensitivity_observed_until_minute"] = (
                    None if b is None else b.get("observed_until_minute"))
                equal_observation = (a is not None and b is not None and
                    a.get("observed_until_minute") is not None and
                    b.get("observed_until_minute") is not None and
                    abs(a["observed_until_minute"] -
                        b["observed_until_minute"]) <= EPS)
                record["event_count_delta_comparable"] = bool(
                    reason is None and equal_observation)
                for window in CONTRAST_WINDOWS:
                    complete = bool(a is not None and b is not None and
                                    a.get(window + "_full_horizon") and
                                    b.get(window + "_full_horizon"))
                    record[window + "_complete_pair"] = complete
                    record[window + "_valid_complete_pair"] = bool(
                        complete and reason is None)
                    for metric in previous.TARGET_NAMES:
                        field = window + "_" + metric
                        left = None if a is None else a.get(field)
                        right = None if b is None else b.get(field)
                        record[field + "_main"] = left
                        record[field + "_sensitivity"] = right
                        suffix = ("_known_sensitivity_minus_main" if metric ==
                                  "P0_failure" else "_sensitivity_minus_main")
                        record[field + suffix] = (
                            float(right) - float(left)
                            if reason is None and left is not None and
                            right is not None and (complete or metric == "P0_failure")
                            else None)
                for field in ("gt_protection_trip_count",
                              "minimum_output_interlock_count",
                              "no_export_interlock_count",
                              "forced_capacity_or_permission_clamp_count",
                              "requested_decision_count", "generated_command_count",
                              "arrived_command_count", "executed_command_count",
                              "max_executed_gt_kW", "max_executed_grid_kW",
                              "max_executed_diesel_kW",
                              "pre_event_executed_gt_kW",
                              "first_event_executed_gt_kW",
                              "first_event_executed_diesel_kW",
                              "last_observed_executed_gt_kW",
                              "last_observed_executed_grid_kW",
                              "last_observed_executed_diesel_kW",
                              "final_diesel_energy_remaining_kWh"):
                    left = None if a is None else a.get(field)
                    right = None if b is None else b.get(field)
                    record[field + "_main"] = left
                    record[field + "_sensitivity"] = right
                    record[field + "_sensitivity_minus_main"] = (
                        float(right) - float(left) if reason is None and
                        equal_observation and left is not None and right is not None
                        else None)
                for field in ("final_diesel_status", "final_extra_faulted_devices_json",
                              "final_initiating_device_availability",
                              "final_initiating_device_extra_faulted"):
                    record[field + "_main"] = None if a is None else a.get(field)
                    record[field + "_sensitivity"] = None if b is None else b.get(field)
                out.append(record)
    return out


def _boundary_coordinate(scene, axis):
    params = _scene_parameters(scene)
    if axis == "main":
        return max((float(x) for x in scene.get("shock", {}).values()), default=None)
    if axis == "delay":
        return params["response_delay_minutes"]
    if axis == "duration":
        return params["shock_duration_minutes"]
    if axis == "availability":
        return params["fault_availability"]
    return None


def _boundary_context(scene, policy_id):
    params = _scene_parameters(scene)
    axis = params["design_axis"] or "unregistered"
    fixed = {key: params[key] for key in
             ("shock_mode", "response_delay_minutes", "fault_device",
              "fault_availability", "fault_start_minute", "fault_duration_minutes",
              "shock_start_minute", "shock_duration_minutes", "shock_json",
              "shock_by_priority_json")}
    if axis == "main":
        fixed.pop("shock_json", None)
    elif axis == "delay":
        fixed.pop("response_delay_minutes", None)
    elif axis == "duration":
        fixed.pop("shock_duration_minutes", None)
    elif axis == "availability":
        fixed.pop("fault_availability", None)
    return (scene["root_family_id"], scene["analysis_class"], policy_id,
            axis, json.dumps(fixed, ensure_ascii=False, sort_keys=True))


def boundary_tables(scenes, rows):
    """Report observed discrete points and adjacent changes, never an inferred threshold."""
    points = []
    grouped = defaultdict(list)
    for row in rows:
        scene = scenes[row["scenario_id"]]
        binary = row["full_P0_failure"]
        state = ("observed_failure" if binary == 1 else
                 "tested_no_failure" if binary == 0 and row["full_full_horizon"]
                 else "unknown")
        point = {"scenario_id": row["scenario_id"],
                 "root_family_id": row["root_family_id"],
                 "policy_id": row["policy_id"],
                 "analysis_class": row["analysis_class"],
                 "design_cell_id": row.get("design_cell_id"),
                 "design_axis": row.get("design_axis"),
                 "design_level": row.get("design_level"),
                 "axis_value": _boundary_coordinate(scene, row.get("design_axis")),
                 "p0_status": state,
                 "full_P0_failure_minutes": row["full_P0_failure_minutes"],
                 "observed_prefix_P0_failure_minutes":
                     row["full_observed_prefix_P0_failure_minutes"],
                 "full_complete": row["full_full_horizon"],
                 **_scene_parameters(scene)}
        points.append(point)
        grouped[_boundary_context(scene, row["policy_id"])].append(point)
    intervals = []
    for (root, category, policy, axis, fixed), subset in sorted(grouped.items()):
        known = sorted((p for p in subset if p["axis_value"] is not None),
                       key=lambda p: (p["axis_value"], p["scenario_id"]))
        adjacent = []
        for a, b in zip(known, known[1:]):
            if a["p0_status"] != b["p0_status"] and "unknown" not in (a["p0_status"], b["p0_status"]):
                adjacent.append({"left": a["axis_value"], "left_status": a["p0_status"],
                                 "right": b["axis_value"], "right_status": b["p0_status"]})
        intervals.append({"root_family_id": root, "analysis_class": category,
                          "policy_id": policy, "design_axis": axis,
                          "fixed_context_json": fixed,
                          "registered_point_count": len(subset),
                          "tested_values_json": [p["axis_value"] for p in known],
                          "tested_no_failure_values_json": [p["axis_value"] for p in known
                                                            if p["p0_status"] == "tested_no_failure"],
                          "observed_failure_values_json": [p["axis_value"] for p in known
                                                          if p["p0_status"] == "observed_failure"],
                          "unknown_values_json": [p["axis_value"] for p in known
                                                  if p["p0_status"] == "unknown"],
                          "adjacent_status_changes_json": adjacent,
                          "interval_semantics": "tested discrete coordinates only; gaps and untested values unknown"})
    return points, intervals


def _denominators(rows, groups, interactions):
    result = {"registered_trajectory_count": len(rows),
              "registered_scenario_count": len({r["scenario_id"] for r in rows}),
              "independent_root_count": len({r["root_family_id"] for r in rows}),
              "full_complete_count": sum(r["full_full_horizon"] for r in rows),
              "full_unknown_count": sum(not r["full_full_horizon"] for r in rows),
              "observed_p0_failure_count": sum(r["full_P0_failure"] == 1 for r in rows),
              "known_p0_no_failure_count": sum(r["full_P0_failure"] == 0 and
                                               r["full_full_horizon"] for r in rows),
              "unknown_p0_binary_count": sum(r["full_P0_failure"] is None for r in rows),
              "rescue_eligible_count": sum(r["rescue_eligible"] for r in rows),
              "rescue_confirmed_count": sum(r["rescue_confirmed"] is True for r in rows),
              "rescue_unknown_count": sum(r["rescue_eligible"] and
                                          r["rescue_confirmed"] is None for r in rows),
              "post_event_clean_confirmed_count": sum(r["recovery_30min"] is True for r in rows),
              "post_event_clean_unknown_count": sum(r["event_end_minute"] is not None
                                                  and r["recovery_30min"] is None for r in rows),
              "matched_four_cell_group_count": len(groups),
              "four_cell_policy_record_count": len(interactions),
              "full_complete_four_cell_policy_count": sum(r["full_complete_four_cell"]
                                                          for r in interactions),
              "full_valid_four_cell_policy_count": sum(
                  r["full_complete_four_cell"] and r["matching_status"] == "matched"
                  for r in interactions),
              "invalid_four_cell_policy_count": sum(
                  r["matching_status"] == "invalid" for r in interactions),
              "gate_semantics_mismatch_count": sum(
                  r["gate_semantics_mismatch"] is True for r in interactions),
              "unverified_gate_semantics_count": sum(
                  r["gate_semantics_mismatch"] is None for r in interactions)}
    by_class = defaultdict(list)
    for row in rows:
        by_class[(row["analysis_class"], row["policy_id"])].append(row)
    result["by_class_policy"] = [{"analysis_class": category, "policy_id": policy,
        "trajectory_count": len(group),
        "independent_root_count": len({r["root_family_id"] for r in group}),
        "complete_count": sum(r["full_full_horizon"] for r in group),
        "p0_failed_observed_count": sum(r["full_P0_failure"] == 1 for r in group),
        "p0_unknown_count": sum(r["full_P0_failure"] is None for r in group),
        "real_gt_trip_count": sum(r["gt_protection_trip_count"] for r in group),
        "interlock_count": sum(r["minimum_output_interlock_count"] +
                               r["no_export_interlock_count"] for r in group),
        "rescue_eligible_count": sum(r["rescue_eligible"] for r in group),
        "rescue_confirmed_count": sum(r["rescue_confirmed"] is True for r in group),
        "post_event_clean_confirmed_count": sum(r["recovery_30min"] is True for r in group)}
        for (category, policy), group in sorted(by_class.items())]
    return result


def _load_audited_batch(manifest_path, plan_path, results_dir, audit_path):
    """Read one complete, separately audited batch without running physics."""
    manifest_path, plan_path, results_dir, audit_path = (
        Path(p).resolve() for p in (manifest_path, plan_path, results_dir, audit_path))
    manifest, plan, audit = map(previous._read_json,
                                (manifest_path, plan_path, audit_path))
    ids = plan.get("scenario_ids", [])
    policy_ids = [spec["policy_id"] for spec in plan.get("policy_specs", [])]
    expected = {(sid, pid) for sid in ids for pid in policy_ids}
    if (audit.get("passed") is not True or
            audit.get("matched_initial_state_and_stream") is not True or
            audit.get("plan_sha256") != previous._sha(plan_path) or
            audit.get("manifest_sha256") != previous._sha(manifest_path) or
            plan.get("manifest_sha256") != previous._sha(manifest_path) or
            plan.get("manifest_signature") != manifest.get("signature") or
            plan.get("registered_pair_count") != len(expected) or
            any(audit.get(key) != len(expected) for key in
                ("registered_pair_count", "terminal_pair_count", "recomputed_pair_count"))):
        raise ValueError("Passed raw audit and registered plan/manifest binding required")
    if len(ids) != len(set(ids)) or len(policy_ids) != len(set(policy_ids)):
        raise ValueError("Duplicate registered scene or policy")
    if "secure_milp" not in policy_ids:
        raise ValueError("Secure baseline absent")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    if not set(ids) <= set(scenes):
        raise ValueError("Registered scene missing from manifest")
    ledger = {}
    for item in audit.get("pair_ledger", []):
        key = (item.get("scenario_id"), item.get("policy_id"))
        if key in ledger:
            raise ValueError("Duplicate audited pair")
        ledger[key] = item
    if set(ledger) != expected:
        raise ValueError("Audited pair denominator differs from registered plan")
    rows, states, streams, profiles = [], {}, {}, {}
    for sid in ids:
        scene = scenes[sid]
        if scene.get("is_final_blind") or scene.get("experiment_role") != plan.get("phase"):
            raise ValueError("Sealed final scene or registered phase mismatch")
        for pid in policy_ids:
            payload, result, raw = previous._read_verified_pair(
                results_dir, scene, pid, ledger[(sid, pid)])
            states[(sid, pid)] = json.dumps(payload.get("initial_state"),
                                            ensure_ascii=False, sort_keys=True)
            streams[(sid, pid)] = payload.get("identity", {}).get("exogenous_stream_id")
            profiles[(sid, pid)] = _compact_profile(payload)
            rows.append(trajectory_row(scene, pid, payload, result, raw))
    for sid in ids:
        if len({states[(sid, pid)] for pid in policy_ids}) != 1 or len(
                {streams[(sid, pid)] for pid in policy_ids}) != 1:
            raise ValueError("Matched policies differ in initial state or exogenous stream")
    return {"manifest": manifest, "plan": plan, "audit": audit,
            "manifest_path": manifest_path, "plan_path": plan_path,
            "audit_path": audit_path, "policy_ids": policy_ids,
            "scenes": scenes, "scenario_ids": ids, "row_list": rows,
            "rows": {(row["scenario_id"], row["policy_id"]): row for row in rows},
            "states": states, "profiles": profiles}


def _analysis_scope_status(primary, clockmatched):
    """Distinguish all 528+24 registered raw from an audited subset."""
    reasons = []
    expected_classes = {"N2": 12, "N4": 24, "L1": 12, "L2": 144,
                        "L3": 24, "L4a": 156, "L4b": 156}
    scenes = primary["scenes"]
    if (len(scenes) != 528 or
            dict(Counter(s["analysis_class"] for s in scenes.values())) !=
            expected_classes or
            len(primary["scenario_ids"]) != 528 or
            set(primary["scenario_ids"]) != set(scenes)):
        reasons.append("primary_plan_does_not_cover_528_scenes")
    expected_policies = [{"policy_id": "secure_milp", "kind": "secure"},
                         {"policy_id": "priority_milp", "kind": "priority"}]
    if (primary["policy_ids"] != ["secure_milp", "priority_milp"] or
            primary["plan"].get("policy_specs") != expected_policies or
            primary["plan"].get("phase") != "train"):
        reasons.append("primary_policy_or_phase_differs")
    groups = primary["manifest"].get("matched_four_cell_groups", [])
    if (len(groups) != 156 or
            Counter(group.get("design_axis") for group in groups) !=
            Counter({"main": 144, "delay": 4, "duration": 4,
                     "availability": 4}) or
            len({group.get("design_cell_id") for group in groups}) != 156):
        reasons.append("primary_156_group_design_differs")
    if clockmatched is None:
        reasons.append("clockmatched_batch_missing")
    else:
        cm_scenes = clockmatched["scenes"]
        cm_groups = clockmatched.get("groups", [])
        if (len(cm_scenes) != 24 or
                any(scene.get("analysis_class") != "N4_clockmatched"
                    for scene in cm_scenes.values()) or
                len(clockmatched["scenario_ids"]) != 24 or
                set(clockmatched["scenario_ids"]) != set(cm_scenes) or
                len(cm_groups) != 156 or
                {group.get("design_cell_id") for group in cm_groups} !=
                {group.get("design_cell_id") for group in groups}):
            reasons.append("clockmatched_24_scene_156_group_coverage_differs")
        if (clockmatched["plan"].get("phase") != "train" or
                clockmatched["plan"].get("policy_specs") != expected_policies):
            reasons.append("clockmatched_policy_or_phase_differs")
    return ("full_grade" if not reasons else "partial"), reasons


def analyze_deepening(manifest_path, plan_path, results_dir, audit_path, *,
                      clockmatched_manifest_path=None, clockmatched_plan_path=None,
                      clockmatched_results_dir=None, clockmatched_audit_path=None,
                      out_dir=None):
    """Analyze v8 raw and optional independent clockmatched normal controls."""
    primary = _load_audited_batch(manifest_path, plan_path, results_dir, audit_path)
    manifest, plan, audit = (primary[k] for k in ("manifest", "plan", "audit"))
    manifest_path, plan_path, audit_path = (primary[k] for k in
                                            ("manifest_path", "plan_path", "audit_path"))
    scenes, ids, rows, by_row, states, profiles, policy_ids = (
        primary[k] for k in ("scenes", "scenario_ids", "row_list", "rows",
                             "states", "profiles", "policy_ids"))
    supplied = (clockmatched_manifest_path, clockmatched_plan_path,
                clockmatched_results_dir, clockmatched_audit_path)
    if any(value is not None for value in supplied) and not all(
            value is not None for value in supplied):
        raise ValueError("All four clockmatched manifest/plan/results/audit paths required")
    clockmatched = None
    if all(value is not None for value in supplied):
        clockmatched = _load_audited_batch(*supplied)
        cm_manifest = clockmatched["manifest"]
        if (cm_manifest.get("source_v8_manifest_sha256") !=
                previous._sha(manifest_path) or
                cm_manifest.get("source_v8_manifest_signature") !=
                manifest.get("signature") or
                clockmatched["plan"].get("policy_specs") != plan.get("policy_specs") or
                set(clockmatched["scenes"]) & set(scenes)):
            raise ValueError("Clockmatched source or policies differ from audited v8")
        clockmatched["groups"] = cm_manifest.get("matched_clock_control_groups", [])
    scope_status, scope_reasons = _analysis_scope_status(primary, clockmatched)
    groups = manifest.get("matched_four_cell_groups", [])
    interactions = four_cell_interactions(groups, scenes, by_row, states,
                                          policy_ids, profiles=profiles,
                                          clockmatched=clockmatched)
    sensitivity = sensitivity_paired_deltas(groups, scenes, by_row, states,
                                            policy_ids)
    deltas = policy_deltas(rows, policy_ids)
    boundaries, intervals = boundary_tables(scenes, rows)
    coverage = []
    for sid in ids:
        scene = scenes[sid]
        record = {"scenario_id": sid, "root_family_id": scene["root_family_id"],
                  "analysis_class": scene["analysis_class"], **_scene_parameters(scene)}
        for pid in policy_ids:
            row = by_row[(sid, pid)]
            record[pid + "_status"] = row["status"]
            record[pid + "_full_complete"] = row["full_full_horizon"]
            record[pid + "_p0_failed_observed"] = row["full_P0_failure"]
        coverage.append(record)
    alerts = [row for row in rows if (not row["full_full_horizon"] or
              row["full_P0_failure"] == 1 or row["gt_protection_trip_count"] or
              row["minimum_output_interlock_count"] or
              row["no_export_interlock_count"] or row["error_type"])]
    denominator = _denominators(rows, groups, interactions)
    denominator["registered_sensitivity_group_count"] = sum(
        group.get("design_axis") != "main" for group in groups)
    denominator["sensitivity_policy_comparison_count"] = len(sensitivity)
    denominator["full_complete_sensitivity_policy_comparison_count"] = sum(
        row["full_complete_pair"] for row in sensitivity)
    denominator["full_valid_sensitivity_policy_comparison_count"] = sum(
        row["full_valid_complete_pair"] for row in sensitivity)
    denominator["invalid_sensitivity_match_count"] = sum(
        row["matching_status"] == "invalid" for row in sensitivity)
    shadow_rows = [] if clockmatched is None else clockmatched["row_list"]
    shadow_alerts = [row for row in shadow_rows if (
        not row["full_full_horizon"] or row["full_P0_failure"] == 1 or
        row["gt_protection_trip_count"] or row["minimum_output_interlock_count"] or
        row["no_export_interlock_count"] or row["error_type"])]
    denominator["clockmatched_registered_scenario_count"] = (
        0 if clockmatched is None else len(clockmatched["scenario_ids"]))
    denominator["clockmatched_registered_trajectory_count"] = len(shadow_rows)
    denominator["clockmatched_full_complete_count"] = sum(
        row["full_full_horizon"] for row in shadow_rows)
    denominator["clockmatched_full_unknown_count"] = sum(
        not row["full_full_horizon"] for row in shadow_rows)
    summary = {"schema": "four_scenarios.deepening_analysis.v3",
               "analysis_status": scope_status,
               "full_grade_eligible": scope_status == "full_grade",
               "scope_reasons": scope_reasons,
               "manifest_sha256": previous._sha(manifest_path),
               "manifest_signature": manifest.get("signature"),
               "plan_sha256": previous._sha(plan_path),
               "audit_sha256": previous._sha(audit_path),
               "clockmatched_manifest_sha256": None if clockmatched is None else
                   previous._sha(clockmatched["manifest_path"]),
               "clockmatched_manifest_signature": None if clockmatched is None
                   else clockmatched["manifest"].get("signature"),
               "clockmatched_plan_sha256": None if clockmatched is None else
                   previous._sha(clockmatched["plan_path"]),
               "clockmatched_audit_sha256": None if clockmatched is None else
                   previous._sha(clockmatched["audit_path"]),
               "final_blind_opened": False,
               "denominators": denominator,
               "missing_combinations": manifest.get("missing_combinations", []),
               "interpretation": "Designed model scenarios, not hospital incident probabilities; "
                                 "discrete tested points do not establish continuous safety bounds; "
                                 "unknown tails and failures are retained; "
                                 "four-cell interaction uses only separately audited clockmatched N4."}
    bundle = {"summary": summary, "trajectory_results": rows,
              "policy_deltas": deltas, "four_cell_interactions": interactions,
              "sensitivity_paired_deltas": sensitivity,
              "parameter_coverage": coverage, "boundary_points": boundaries,
              "boundary_intervals": intervals, "unknown_and_failures": alerts,
              "clockmatched_trajectory_results": shadow_rows,
              "clockmatched_policy_deltas": (policy_deltas(shadow_rows, policy_ids)
                                             if shadow_rows else []),
              "clockmatched_unknown_and_failures": shadow_alerts}
    if out_dir is not None:
        out = Path(out_dir)
        if out.exists() and any(out.iterdir()):
            raise FileExistsError("Analysis output directory must be new and empty")
        out.mkdir(parents=True, exist_ok=True)
        for name, table in (("TRAJECTORY_RESULTS.csv", rows),
                            ("POLICY_DELTAS.csv", deltas),
                            ("FOUR_CELL_INTERACTIONS.csv", interactions),
                            ("SENSITIVITY_PAIRED_DELTAS.csv", sensitivity),
                            ("PARAMETER_COVERAGE.csv", coverage),
                            ("MODEL_BOUNDARY_POINTS.csv", boundaries),
                            ("MODEL_BOUNDARY_INTERVALS.csv", intervals),
                            ("UNKNOWN_AND_FAILURES.csv", alerts),
                            ("CLOCKMATCHED_N4_TRAJECTORY_RESULTS.csv", shadow_rows),
                            ("CLOCKMATCHED_N4_POLICY_DELTAS.csv",
                             bundle["clockmatched_policy_deltas"]),
                            ("CLOCKMATCHED_N4_UNKNOWN_AND_FAILURES.csv", shadow_alerts)):
            previous._write_csv_new(out / name, table)
        with (out / "ANALYSIS_SUMMARY.json").open("x", encoding="utf-8") as stream:
            json.dump(summary, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return bundle


def analyze_fault_cross(manifest_path, plan_path, results_dir, audit_path, *,
                        source_v8_manifest_path, out_dir=None):
    """Audit and describe a separate seen-root L1/L3 equipment cross batch."""
    source_v8_manifest_path = Path(source_v8_manifest_path).resolve()
    source = previous._read_json(source_v8_manifest_path)
    batch = _load_audited_batch(manifest_path, plan_path, results_dir, audit_path)
    manifest = batch["manifest"]
    if (manifest.get("source_v8_manifest_sha256") !=
            previous._sha(source_v8_manifest_path) or
            manifest.get("source_v8_manifest_signature") != source.get("signature")):
        raise ValueError("Fault-cross source v8 manifest SHA/signature differs")
    groups = manifest.get("matched_fault_groups", [])
    if not groups:
        raise ValueError("No registered fault-cross groups")
    registered = [sid for group in groups for sid in
                  group.get("scene_ids", {}).values()]
    if (len(registered) != len(set(registered)) or
            set(registered) != set(batch["scenario_ids"])):
        raise ValueError("Fault-cross group-to-scene coverage differs from plan")
    rows = batch["row_list"]
    deltas = fault_cross_deltas(groups, batch["scenes"], batch["rows"],
                                batch["states"], batch["policy_ids"],
                                profiles=batch["profiles"])
    pair_issues = [row for row in deltas if (
        row["matching_status"] != "matched" or
        not row["full_complete_pair"])]
    policy = policy_deltas(rows, batch["policy_ids"])
    alerts = [row for row in rows if (
        not row["full_full_horizon"] or row["full_P0_failure"] == 1 or
        row["gt_protection_trip_count"] or row["minimum_output_interlock_count"] or
        row["no_export_interlock_count"] or row["error_type"])]
    summary = {"schema": "four_scenarios.fault_cross_analysis.v1",
               "source_v8_manifest_sha256": previous._sha(source_v8_manifest_path),
               "manifest_sha256": previous._sha(batch["manifest_path"]),
               "plan_sha256": previous._sha(batch["plan_path"]),
               "audit_sha256": previous._sha(batch["audit_path"]),
               "final_blind_opened": False,
               "denominators": {
                   "registered_root_count": len({g["root_family_id"] for g in groups}),
                   "registered_group_count": len(groups),
                   "registered_scenario_count": len(batch["scenario_ids"]),
                   "registered_trajectory_count": len(rows),
                   "registered_L3_minus_L1_policy_record_count": len(deltas),
                   "full_complete_trajectory_count": sum(r["full_full_horizon"] for r in rows),
                   "full_unknown_trajectory_count": sum(not r["full_full_horizon"] for r in rows),
                   "full_complete_L3_minus_L1_policy_count": sum(
                       r["full_complete_pair"] for r in deltas),
                   "full_valid_L3_minus_L1_policy_count": sum(
                       r["full_valid_complete_pair"] for r in deltas),
                   "full_unknown_L3_minus_L1_policy_count": sum(
                       not r["full_complete_pair"] for r in deltas),
                   "invalid_pre_event_or_fault_match_policy_count": sum(
                       r["matching_status"] == "invalid" for r in deltas),
                   "invalid_raw_fault_trace_policy_count": sum(
                       r["raw_fault_event_trace_status"] == "invalid" for r in deltas),
                   "unknown_truncated_fault_trace_policy_count": sum(
                       r["raw_fault_event_trace_status"] == "unknown_truncated"
                       for r in deltas),
                   "observed_P0_failure_trajectory_count": sum(
                       r["full_P0_failure"] == 1 for r in rows),
                   "unknown_P0_binary_trajectory_count": sum(
                       r["full_P0_failure"] is None for r in rows),
                   "real_GT_protection_trip_event_count": sum(
                       r["gt_protection_trip_count"] for r in rows),
                   "minimum_output_interlock_event_count": sum(
                       r["minimum_output_interlock_count"] for r in rows),
                   "no_export_interlock_event_count": sum(
                       r["no_export_interlock_count"] for r in rows),
                   "alert_trajectory_count": len(alerts),
                   "pair_issue_count": len(pair_issues)},
               "interpretation": "L3-L1 is a descriptive model propagation-mode contrast "
                                 "on matched seen roots and identical registered faults; "
                                 "a fault does not imply a GT protection trip. "
                                 "Unknown tails and all actual failures remain in the ledger."}
    bundle = {"summary": summary, "trajectory_results": rows,
              "L3_minus_L1": deltas, "policy_deltas": policy,
              "unknown_and_failures": alerts, "pair_issues": pair_issues}
    if out_dir is not None:
        out = Path(out_dir)
        if out.exists() and any(out.iterdir()):
            raise FileExistsError("Fault-cross output directory must be new and empty")
        out.mkdir(parents=True, exist_ok=True)
        for name, table in (("FAULT_CROSS_TRAJECTORY_RESULTS.csv", rows),
                            ("FAULT_CROSS_L3_MINUS_L1.csv", deltas),
                            ("FAULT_CROSS_POLICY_DELTAS.csv", policy),
                            ("FAULT_CROSS_UNKNOWN_AND_FAILURES.csv", alerts),
                            ("FAULT_CROSS_PAIR_ISSUES.csv", pair_issues)):
            previous._write_csv_new(out / name, table)
        with (out / "FAULT_CROSS_SUMMARY.json").open("x", encoding="utf-8") as stream:
            json.dump(summary, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return bundle


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "fault-cross":
        parser = argparse.ArgumentParser(description="Audited fault-cross L3-L1 analysis")
        parser.add_argument("--manifest", type=Path, required=True)
        parser.add_argument("--plan", type=Path, required=True)
        parser.add_argument("--results", type=Path, required=True)
        parser.add_argument("--audit", type=Path, required=True)
        parser.add_argument("--source-v8-manifest", type=Path, required=True)
        parser.add_argument("--out-dir", type=Path, required=True)
        args = parser.parse_args(argv[1:])
        bundle = analyze_fault_cross(
            args.manifest, args.plan, args.results, args.audit,
            source_v8_manifest_path=args.source_v8_manifest,
            out_dir=args.out_dir)
        print(json.dumps(bundle["summary"]["denominators"], ensure_ascii=False))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--clockmatched-manifest", type=Path)
    parser.add_argument("--clockmatched-plan", type=Path)
    parser.add_argument("--clockmatched-results", type=Path)
    parser.add_argument("--clockmatched-audit", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    bundle = analyze_deepening(
        args.manifest, args.plan, args.results, args.audit,
        clockmatched_manifest_path=args.clockmatched_manifest,
        clockmatched_plan_path=args.clockmatched_plan,
        clockmatched_results_dir=args.clockmatched_results,
        clockmatched_audit_path=args.clockmatched_audit,
        out_dir=args.out_dir)
    print(json.dumps(bundle["summary"]["denominators"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
