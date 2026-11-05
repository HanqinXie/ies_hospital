"""Read-only, raw-bound analysis of registered four-scenario hospital IES runs.

The original v6 runner and scorer own trajectory generation and the 19-target
score.  This module only checks their saved evidence and describes service,
command/protection timing, recovery, and matched contrasts.  It never imports
or invokes the simulator.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.control_improvement.ranking import TARGET_NAMES


ANALYSIS_CLASSES = ("N2", "N4", "L1", "L2", "L3", "L4a", "L4b")
REFERENCE_CLASS = {"L1": "N2", "L2": "N2", "L3": "N4",
                   "L4a": "N4", "L4b": "N4"}
DISTURBANCE_COMPARISONS = (("L1", "N2"), ("L2", "N2"),
                           ("L3", "N4"), ("L3", "L1"),
                           ("L4a", "N4"), ("L4a", "L2"),
                           ("L4b", "N4"), ("L4b", "L2"),
                           ("L4b", "L3"))
WINDOWS = {"primary": (60.0, 420.0), "full": (0.0, 600.0)}
PROTECTION_KINDS = {"protection_trip", "no_export_interlock",
                    "minimum_output_interlock"}
RECOVERY_MINUTES = 30.0
EPS = 1e-8


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _open_raw(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream, parse_constant=lambda value:
                         (_ for _ in ()).throw(ValueError(value)))


def _ordered_segments(payload):
    segments = payload.get("segments", [])
    previous = 0.0
    for segment in segments:
        start = float(segment["start_minute"])
        end = float(segment["end_minute"])
        if abs(start - previous) > EPS or end <= start:
            raise ValueError("Raw subsegments are not contiguous from zero")
        previous = end
    observed = float(payload["summary"]["observed_until_minute"])
    if abs(previous - observed) > EPS:
        raise ValueError("Raw observation endpoint differs from subsegments")
    return segments


def _overlap(segment, start, end):
    return max(0.0, min(end, float(segment["end_minute"])) -
               max(start, float(segment["start_minute"])))


def _known_event_end(scene, payload):
    """Post-hoc event end, never a feature sent to an online controller."""
    planned = [float(event["end_minute"])
               for event in scene.get("exogenous_events", [])
               if event.get("end_minute") is not None]
    observed = [float(event["time_minute"])
                for event in payload.get("events", [])
                if event.get("kind") in ("exogenous_fault_end", "exogenous_shock_end")]
    return max(planned + observed) if planned or observed else None


def recovery_after_event(segments, event_end, *, observed_until, requested_end):
    """First 30-minute post-event clean interval and later recurrence.

    A missing interval after a truncated observation is unknown.  Recovery is
    observed if confirmed before truncation; sustaining the *requested* full
    horizon remains unknown when the physical trajectory ends early.
    """
    result = {"recovery_30min": None, "recovery_start_minute": None,
              "recovery_confirmed_minute": None,
              "sustained_to_observation_end": None,
              "sustained_to_requested_end": None,
              "later_p0_recurrence_minute": None,
              "recovery_observation_status": "no_exogenous_event"}
    if event_end is None:
        return result
    end = float(event_end)
    observed = float(observed_until)
    requested = float(requested_end)
    if end >= observed - EPS:
        result["recovery_observation_status"] = "event_end_not_followed_by_observation"
        return result
    clean_start = None
    confirmation = None
    for segment in segments:
        start = max(end, float(segment["start_minute"]))
        finish = min(observed, float(segment["end_minute"]))
        if finish <= start + EPS:
            continue
        failed = bool(segment["classification"]["p0_guarantee_failure"])
        if failed:
            if confirmation is not None:
                result["later_p0_recurrence_minute"] = start
                break
            clean_start = None
            continue
        if clean_start is None:
            clean_start = start
        if confirmation is None and finish - clean_start >= RECOVERY_MINUTES - EPS:
            confirmation = clean_start + RECOVERY_MINUTES
            result.update(recovery_30min=True,
                          recovery_start_minute=clean_start,
                          recovery_confirmed_minute=confirmation)
    if confirmation is None:
        if observed >= requested - EPS:
            result["recovery_30min"] = False
            result["recovery_observation_status"] = "not_recovered_in_complete_horizon"
        else:
            result["recovery_observation_status"] = "no_confirmed_recovery_before_truncation"
        return result
    sustained = result["later_p0_recurrence_minute"] is None
    result["sustained_to_observation_end"] = sustained
    result["sustained_to_requested_end"] = sustained if observed >= requested - EPS else None
    result["recovery_observation_status"] = ("confirmed_then_recurred" if not sustained
                                             else "confirmed_to_observed_end")
    return result


def _window_payload(segments, score, prefix, start, end):
    targets = score.get("targets")
    if not isinstance(targets, list) or len(targets) != len(TARGET_NAMES):
        raise ValueError(prefix + " target schema differs from original 19 targets")
    complete = score.get("full_horizon")
    if type(complete) is not bool:
        raise ValueError(prefix + " full_horizon must be boolean")
    observed_minutes = sum(_overlap(seg, start, end) for seg in segments)
    if complete != (abs(observed_minutes - (end - start)) <= EPS):
        raise ValueError(prefix + " complete-window mask conflicts with raw coverage")
    if complete and any(value is None for value in targets):
        raise ValueError(prefix + " complete score has null target")
    if not complete and any(value is not None for value in targets[1:]):
        raise ValueError(prefix + " unknown tail has non-null future target")
    trigger = {"P0_failure": 0.0, "P0_e": 0.0, "P0_h": 0.0,
               "P0_c_material": 0.0, "P0_temperature_trigger": 0.0}
    ens = [[0.0] * 3 for _ in range(4)]
    for seg in segments:
        dt = _overlap(seg, start, end)
        if dt <= 0:
            continue
        classification = seg["classification"]
        material = classification["material_unserved_by_carrier"]
        trigger["P0_failure"] += dt * bool(classification["p0_guarantee_failure"])
        for key in ("e", "h"):
            trigger["P0_" + key] += dt * bool(material[key])
        trigger["P0_c_material"] += dt * bool(material["c"])
        trigger["P0_temperature_trigger"] += dt * bool(
            classification["p0_temperature_violation"])
        for priority in range(4):
            for carrier in range(3):
                ens[priority][carrier] += float(seg["unserved_kW"][priority][carrier]) * dt / 60.0
    row = {prefix + "_full_horizon": complete,
           prefix + "_observed_minutes": observed_minutes}
    for name, value in zip(TARGET_NAMES, targets):
        row[prefix + "_" + name] = value
    for name, value in trigger.items():
        row[prefix + "_observed_prefix_" + name + "_minutes"] = value
        if name != "P0_failure":
            row[prefix + "_" + name + "_minutes"] = value if complete else None
    for priority in range(4):
        for carrier, letter in enumerate("ehc"):
            row[f"{prefix}_observed_prefix_P{priority}_{letter}_ENS_kWh"] = ens[priority][carrier]
    return row


def _event_rows(scene, policy_id, payload, recovery):
    sid = scene["id"]
    root = scene["root_family_id"]
    category = scene["analysis_class"]
    rows = []
    for event in payload.get("events", []):
        rows.append({"scenario_id": sid, "root_family_id": root,
                     "analysis_class": category, "policy_id": policy_id,
                     "time_minute": event.get("time_minute"),
                     "event_kind": event.get("kind"),
                     "origin": event.get("origin"),
                     "cause_class": event.get("cause_class"),
                     "device": event.get("device"),
                     "command_id": event.get("command_id") or
                                   (event.get("command") or {}).get("id"),
                     "raw_event_id": event.get("event_id"),
                     "event_details_json": json.dumps(event, ensure_ascii=False,
                                                      sort_keys=True)})
    previous = False
    for segment in payload.get("segments", []):
        failed = bool(segment["classification"]["p0_guarantee_failure"])
        if failed != previous:
            rows.append({"scenario_id": sid, "root_family_id": root,
                         "analysis_class": category, "policy_id": policy_id,
                         "time_minute": segment["start_minute"],
                         "event_kind": "p0_failure_start" if failed else "p0_failure_end",
                         "origin": "observed_service_classification",
                         "cause_class": None, "device": None, "command_id": None,
                         "raw_event_id": None, "event_details_json": None})
        previous = failed
    if recovery["recovery_confirmed_minute"] is not None:
        rows.append({"scenario_id": sid, "root_family_id": root,
                     "analysis_class": category, "policy_id": policy_id,
                     "time_minute": recovery["recovery_confirmed_minute"],
                     "event_kind": "p0_recovery_30min_confirmed",
                     "origin": "posthoc_analysis", "cause_class": None,
                     "device": None, "command_id": None,
                     "raw_event_id": None, "event_details_json": None})
    return sorted(rows, key=lambda row: (float(row["time_minute"]),
                                         str(row["event_kind"])))


def summarize_trajectory(scene, policy_id, payload, result, *, raw_path=None):
    """Summarize one already-audited raw; no physical dynamics are rerun."""
    category = scene["analysis_class"]
    if category not in ANALYSIS_CLASSES:
        raise ValueError("Unknown four-scenario analysis_class: " + str(category))
    segments = _ordered_segments(payload)
    status = payload["summary"]["status"]
    if result["status"] != status:
        raise ValueError("Raw/result status differs")
    observed = float(payload["summary"]["observed_until_minute"])
    requested = float(payload["summary"]["requested_end_minute"])
    event_end = _known_event_end(scene, payload)
    recovery = recovery_after_event(segments, event_end,
                                    observed_until=observed, requested_end=requested)
    events = payload.get("events", [])
    counts = Counter(event.get("kind") for event in events)
    generated = {str((event.get("command") or {}).get("id")) for event in events
                 if event.get("kind") == "command_generated" and
                 (event.get("command") or {}).get("id") is not None}
    arrived = {str(event.get("command_id")) for event in events
               if event.get("kind") == "command_arrived" and
               event.get("command_id") is not None}
    executed = {str(event.get("command_id")) for event in events
                if event.get("kind") == "command_executed" and
                event.get("command_id") is not None}
    protection = [event for event in events if event.get("kind") in PROTECTION_KINDS]
    decisions = payload.get("decisions", [])
    row = {"scenario_id": scene["id"], "analysis_class": category,
           "reference_class": REFERENCE_CLASS.get(category),
           "root_family_id": scene["root_family_id"], "policy_id": policy_id,
           "start_hour": scene.get("start_hour"), "stage": scene.get("stage"),
           "status": status, "observed_until_minute": observed,
           "requested_end_minute": requested,
           "raw_path": None if raw_path is None else str(raw_path),
           "raw_sha256": None if raw_path is None else _sha(raw_path),
           "event_end_minute": event_end,
           "requested_decision_count": sum(d.get("requested_command") is not None
                                            for d in decisions),
           "generated_command_count": counts["command_generated"],
           "arrived_command_count": counts["command_arrived"],
           "executed_command_count": counts["command_executed"],
           "superseded_command_count": counts["command_superseded"],
           "generated_arrived_same_id_count": len(generated & arrived),
           "arrived_executed_same_id_count": len(arrived & executed),
           "actual_execution_decision_count": sum(
               d.get("actual_execution") is not None for d in decisions),
           "protection_event_count": len(protection),
           "GT_protection_trip_count_observed": sum(
               event.get("kind") == "protection_trip" and
               str(event.get("device", "")).startswith("GT_") for event in protection),
           "fault_start_event_count": counts["exogenous_fault_start"],
           "fault_end_event_count": counts["exogenous_fault_end"],
           "shock_start_event_count": counts["exogenous_shock_start"],
           "shock_end_event_count": counts["exogenous_shock_end"],
           "error_type": (payload.get("error") or {}).get("type"),
           "error_classification": (payload.get("error") or {}).get("classification")}
    row.update(recovery)
    for prefix, (start, end) in WINDOWS.items():
        row.update(_window_payload(segments, result[prefix + "_score"] if prefix == "primary"
                                   else result["full_observation"], prefix, start, end))
    row["full_observation_complete"] = row["full_full_horizon"]
    return row, _event_rows(scene, policy_id, payload, recovery)


def _group_summary(rows, keys):
    grouped = defaultdict(list)
    for row in rows:
        grouped[tuple(row[key] for key in keys)].append(row)
    output = []
    for key, group in sorted(grouped.items()):
        item = dict(zip(keys, key))
        item.update(registered_scenario_count=len(group),
                    independent_root_count=len({row["root_family_id"] for row in group}),
                    root_family_ids_json=json.dumps(sorted({row["root_family_id"]
                                                           for row in group})),
                    completed_trajectory_count=sum(row["status"] == "completed"
                                                   for row in group))
        for field in ("requested_decision_count", "generated_command_count",
                      "arrived_command_count", "executed_command_count",
                      "superseded_command_count", "generated_arrived_same_id_count",
                      "arrived_executed_same_id_count", "actual_execution_decision_count",
                      "protection_event_count", "GT_protection_trip_count_observed",
                      "fault_start_event_count", "fault_end_event_count",
                      "shock_start_event_count", "shock_end_event_count"):
            item[field + "_observed_total"] = sum(row[field] for row in group)
        item["recovery_applicable_count"] = sum(row["event_end_minute"] is not None
                                                 for row in group)
        item["recovery_confirmed_count"] = sum(row["recovery_30min"] is True
                                                for row in group)
        item["recovery_not_confirmed_count"] = sum(row["recovery_30min"] is False
                                                    for row in group)
        item["recovery_unknown_count"] = sum(row["event_end_minute"] is not None
                                             and row["recovery_30min"] is None
                                             for row in group)
        item["recovery_sustained_to_requested_end_count"] = sum(
            row["sustained_to_requested_end"] is True for row in group)
        item["recovery_later_recurrence_count"] = sum(
            row["later_p0_recurrence_minute"] is not None for row in group)
        for window in WINDOWS:
            complete = [row for row in group if row[window + "_full_horizon"]]
            item[window + "_complete_count"] = len(complete)
            item["unknown_" + window + "_count"] = len(group) - len(complete)
            binary = [row[window + "_P0_failure"] for row in group]
            item[window + "_known_P0_failure_case_count"] = sum(x == 1 for x in binary)
            item[window + "_known_P0_no_failure_case_count"] = sum(x == 0 for x in binary)
            item[window + "_unknown_P0_binary_count"] = sum(x is None for x in binary)
            for trigger in ("P0_failure", "P0_e", "P0_h",
                            "P0_c_material", "P0_temperature_trigger"):
                key = window + "_observed_prefix_" + trigger + "_minutes"
                item[key + "_total"] = sum(row[key] for row in group)
                if trigger == "P0_failure":
                    item["observed_prefix_P0_failure_minutes_total"] = sum(
                        row["full_observed_prefix_P0_failure_minutes"] for row in group)
                else:
                    full_key = window + "_" + trigger + "_minutes"
                    subtotal = sum(row[full_key] for row in complete)
                    item[full_key + "_complete_case_total"] = subtotal
                    item[full_key + "_all_registered"] = (
                        subtotal if len(complete) == len(group) else None)
            for target in TARGET_NAMES:
                if target in ("P0_max_temperature_C", "P1_max_temperature_C",
                              "P2_max_temperature_C"):
                    # Maxima are not additive across scenarios.
                    continue
                name = window + "_" + target
                subtotal = sum(float(row[name]) for row in complete)
                item[name + "_complete_case_total"] = subtotal
                item[name + "_all_registered"] = subtotal if len(complete) == len(group) else None
        output.append(item)
    return output


def _comparison(comparison_id, left, right, policy_id, root):
    record = {"comparison_id": comparison_id,
              "root_family_id": root, "policy_id": policy_id,
              "left_scenario_id": None if left is None else left["scenario_id"],
              "right_scenario_id": None if right is None else right["scenario_id"]}
    for window in WINDOWS:
        full = bool(left and right and left[window + "_full_horizon"]
                    and right[window + "_full_horizon"])
        record[window + "_complete_pair"] = full
        a = None if left is None else left[window + "_P0_failure"]
        b = None if right is None else right[window + "_P0_failure"]
        record[window + "_known_P0_failure_delta"] = (
            int(a) - int(b) if a is not None and b is not None else None)
        for target in TARGET_NAMES:
            a = None if left is None else left[window + "_" + target]
            b = None if right is None else right[window + "_" + target]
            record[window + "_" + target + "_delta"] = (
                float(a) - float(b) if full else None)
    return record


def _matching(rows, policy_ids):
    by_scene = defaultdict(dict)
    by_root = defaultdict(dict)
    for row in rows:
        sid, pid = row["scenario_id"], row["policy_id"]
        if pid in by_scene[sid]:
            raise ValueError("Duplicate scenario/policy in analysis")
        by_scene[sid][pid] = row
        key = (row["analysis_class"], pid)
        root = row["root_family_id"]
        if key in by_root[root]:
            raise ValueError("Duplicate root/analysis class/policy in manifest")
        by_root[root][key] = row
    records = []
    for sid, policies in sorted(by_scene.items()):
        secure = policies.get("secure_milp")
        for pid in policy_ids:
            if pid == "secure_milp":
                continue
            records.append(_comparison(pid + "_minus_secure", policies.get(pid),
                                       secure, pid, secure["root_family_id"] if secure else None))
    for root, panel in sorted(by_root.items()):
        for pid in policy_ids:
            for left_name, right_name in DISTURBANCE_COMPARISONS:
                records.append(_comparison(left_name + "_minus_" + right_name,
                                           panel.get((left_name, pid)),
                                           panel.get((right_name, pid)), pid, root))
    return records


def _latest_result_path(results_dir, sid, pid):
    pair = results_dir / "pairs" / digest([sid, pid])
    attempts = sorted(path for path in pair.glob("attempt_*")
                      if path.is_dir() and path.name[8:].isdigit())
    if not attempts:
        raise ValueError("No saved attempt for " + sid + "/" + pid)
    path = attempts[-1] / "result.json"
    if not path.is_file():
        raise ValueError("Latest attempt has no result receipt")
    return path


def _read_verified_pair(results_dir, scene, pid, ledger):
    result_path = _latest_result_path(results_dir, scene["id"], pid)
    if _sha(result_path) != ledger["latest_result_sha256"]:
        raise ValueError("Result SHA differs from passed audit ledger")
    result = _read_json(result_path)
    if (result.get("scenario_id") != scene["id"] or result.get("policy_id") != pid
            or result.get("terminal") is not True or
            result.get("status") != ledger.get("status")):
        raise ValueError("Result identity, terminal status or audit status differs")
    raw_name = result.get("trajectory_path")
    if raw_name != "trajectory.json.gz":
        raise ValueError("Unexpected raw trajectory path")
    raw_path = result_path.parent / raw_name
    if not raw_path.is_file() or _sha(raw_path) != result.get("trajectory_sha256"):
        raise ValueError("raw SHA differs from registered result")
    payload = _open_raw(raw_path)
    if (payload.get("scenario") != scene or
            payload.get("identity", {}).get("scenario_id") != scene["id"] or
            payload.get("identity", {}).get("policy_id") != pid):
        raise ValueError("Raw scene/policy identity differs")
    if result.get("binding", {}).get("passed") is not True:
        raise ValueError("Original raw binding was not passed")
    for stored, audited in (("primary_score", "recomputed_primary_score"),
                            ("full_observation", "recomputed_full_observation")):
        if audited in ledger and result.get(stored) != ledger[audited]:
            raise ValueError("Raw audit recomputation differs from result score")
    return payload, result, raw_path


def _csv_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _write_csv_new(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


def analyze_batch(manifest_path, plan_path, results_dir, audit_path, *, out_dir=None):
    """Bind a passed v6 audit to read-only four-class CSV/JSON summaries."""
    manifest_path = Path(manifest_path).resolve()
    plan_path = Path(plan_path).resolve()
    results_dir = Path(results_dir).resolve()
    audit_path = Path(audit_path).resolve()
    manifest, plan, audit = map(_read_json, (manifest_path, plan_path, audit_path))
    ids = plan.get("scenario_ids", [])
    policy_ids = [spec["policy_id"] for spec in plan.get("policy_specs", [])]
    expected = {(sid, pid) for sid in ids for pid in policy_ids}
    if (audit.get("passed") is not True or
            audit.get("matched_initial_state_and_stream") is not True or
            audit.get("plan_sha256") != _sha(plan_path) or
            audit.get("manifest_sha256") != _sha(manifest_path) or
            plan.get("manifest_sha256") != _sha(manifest_path) or
            plan.get("registered_pair_count") != len(expected) or
            any(audit.get(key) != len(expected) for key in
                ("registered_pair_count", "terminal_pair_count", "recomputed_pair_count"))):
        raise ValueError("Passed raw audit and registered plan/manifest binding required")
    if len(ids) != len(set(ids)) or len(policy_ids) != len(set(policy_ids)):
        raise ValueError("Duplicate registered scene or policy")
    if "secure_milp" not in policy_ids:
        raise ValueError("Original secure baseline absent")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    if not set(ids) <= set(scenes):
        raise ValueError("Registered scene missing from manifest")
    root_classes = defaultdict(Counter)
    for sid in ids:
        scene = scenes[sid]
        root_classes[scene["root_family_id"]][scene["analysis_class"]] += 1
    expected_classes = Counter(ANALYSIS_CLASSES)
    if any(classes != expected_classes for classes in root_classes.values()):
        raise ValueError("Root must contain exactly one registered scene per seven analysis classes")
    ledger = {}
    for item in audit.get("pair_ledger", []):
        key = (item.get("scenario_id"), item.get("policy_id"))
        if key in ledger:
            raise ValueError("Duplicate audited pair")
        ledger[key] = item
    if set(ledger) != expected:
        raise ValueError("Audited pair denominator differs from registered plan")
    rows, timeline, raw_binding = [], [], {}
    for sid in ids:
        scene = scenes[sid]
        if scene.get("is_final_blind"):
            raise ValueError("Sealed final scene cannot enter four-scenario analysis")
        if scene.get("experiment_role") != plan.get("phase"):
            raise ValueError("Scenario role differs from registered phase")
        for pid in policy_ids:
            payload, result, raw = _read_verified_pair(results_dir, scene, pid,
                                                       ledger[(sid, pid)])
            raw_binding[(sid, pid)] = (payload.get("initial_state"),
                                       payload.get("identity", {}).get("exogenous_stream_id"))
            row, events = summarize_trajectory(scene, pid, payload, result, raw_path=raw)
            rows.append(row)
            timeline.extend(events)
    for sid in ids:
        bindings = {json.dumps(raw_binding[(sid, pid)], sort_keys=True, ensure_ascii=False)
                    for pid in policy_ids}
        if len(bindings) != 1:
            raise ValueError("Matched policies differ in initial state or exogenous stream")
    for root in root_classes:
        states = {json.dumps(raw_binding[(sid, pid)][0], sort_keys=True, ensure_ascii=False)
                  for sid in ids if scenes[sid]["root_family_id"] == root
                  for pid in policy_ids}
        if len(states) != 1:
            raise ValueError("Root scenes differ in raw initial state")
    class_summary = _group_summary(rows, ("analysis_class", "policy_id"))
    root_summary = _group_summary(rows, ("root_family_id", "analysis_class", "policy_id"))
    comparisons = _matching(rows, policy_ids)
    unknown = []
    for row in rows:
        flags = []
        if not row["primary_full_horizon"]:
            flags.append("primary_unknown")
        if not row["full_full_horizon"]:
            flags.append("full_unknown")
        if row["status"] != "completed":
            flags.append("trajectory_" + row["status"])
        if row["full_P0_failure"] == 1:
            flags.append("observed_P0_failure")
        if row["protection_event_count"]:
            flags.append("observed_protection")
        if flags:
            unknown.append({key: row[key] for key in
                            ("scenario_id", "root_family_id", "analysis_class",
                             "policy_id", "status", "observed_until_minute",
                             "requested_end_minute", "primary_full_horizon",
                             "full_full_horizon", "full_P0_failure",
                             "full_P0_failure_minutes", "error_type",
                             "error_classification", "raw_path", "raw_sha256")}
                           | {"flags": ";".join(flags)})
    bundle = {"schema": "four_scenarios.readonly_analysis.v1",
              "manifest_sha256": _sha(manifest_path), "plan_sha256": _sha(plan_path),
              "audit_sha256": _sha(audit_path),
              "registered_scenario_count": len(ids),
              "independent_root_count": len({scenes[sid]["root_family_id"] for sid in ids}),
              "registered_pair_count": len(expected),
              "analysis_classes": list(ANALYSIS_CLASSES),
              "final_blind_opened": False,
              "scenario_results": rows, "class_summary": class_summary,
              "root_summary": root_summary,
              "matched_comparisons": comparisons,
              "recovery_events": timeline,
              "unknown_and_failures": unknown,
              "interpretation": "Designed scenario counts, not hospital incident probabilities; "
                                "unknown tails remain null; post-hoc event clocks never enter policy."}
    if out_dir is not None:
        out = Path(out_dir)
        files = (("SCENARIO_RESULTS.csv", rows),
                 ("CLASS_SUMMARY.csv", class_summary),
                 ("ROOT_SUMMARY.csv", root_summary),
                 ("MATCHED_COMPARISONS.csv", comparisons),
                 ("RECOVERY_EVENTS.csv", timeline),
                 ("UNKNOWN_AND_FAILURES.csv", unknown))
        if out.exists() and any(out.iterdir()):
            raise FileExistsError("Analysis output directory must be new and empty")
        out.mkdir(parents=True, exist_ok=True)
        for name, table in files:
            _write_csv_new(out / name, table)
        with (out / "ANALYSIS_SUMMARY.json").open("x", encoding="utf-8") as stream:
            json.dump(bundle, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = analyze_batch(args.manifest, args.plan, args.results, args.audit,
                           out_dir=args.out_dir)
    print(json.dumps({key: result[key] for key in
                      ("schema", "registered_scenario_count",
                       "independent_root_count", "registered_pair_count")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
