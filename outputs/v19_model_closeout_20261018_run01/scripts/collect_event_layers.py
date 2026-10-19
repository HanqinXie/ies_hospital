"""Read-only event layer tables from compact results and existing event CSVs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
B19 = ROOT / "outputs/v19_model_closeout_20261018_run01"
SECONDARY = ("residual_stress_protection", "no_export_isolation", "minimum_output_interlock")
CATEGORIES = ("exogenous_fault_start", "exogenous_fault_end") + SECONDARY
KIND_MAP = dict(protection_trip=SECONDARY[0], no_export_interlock=SECONDARY[1],
                minimum_output_interlock=SECONDARY[2], exogenous_fault_start=CATEGORIES[0],
                exogenous_fault_end=CATEGORIES[1])
SUMMARY_KEYS = dict(zip(SECONDARY, ("residual_stress_protection_events", "no_export_interlock_events",
                                  "minimum_output_interlock_events")))
PHYSICAL_KEYS = ("start_hour", "source_day_index", "duration_steps", "step_minutes", "initialization_seed",
                 "pre_event_warmup_steps", "base_state_id", "exogenous_events", "faults", "shock",
                 "shock_by_priority", "automatic_fast_shedding", "grid_limit_kW", "pre_event_grid_limit_kW",
                 "response_delay_minutes", "fault_start_minute", "fault_duration_minutes",
                 "shock_start_minute", "shock_duration_minutes", "full_source_support", "actual_p0_load_multiplier",
                 "thermal_disturbance", "diesel_on_grid_allowed", "precontrol_sheddable_fraction_by_priority",
                 "first_event_minute", "gt_initial_age_minutes", "diesel_initial_energy_kWh", "initial_state", "initialization_state")
STREAM_FIELDS = {"start_hour", "duration_steps", "step_minutes", "faults", "shock", "shock_by_priority",
                 "exogenous_events", "enable_propagation", "propagation_enabled", "grid_limit_kW",
                 "pre_event_grid_limit_kW", "pre_event_warmup_steps", "response_delay_minutes",
                 "automatic_fast_shedding", "initialization_seed"}
PROPAGATION_TOGGLES = {"enable_propagation", "propagation_enabled"}
DEFAULT_PREFIXES = ("main_final_r1_", "confirmation_final_r1_", "propagation_final_r1_")
EXPECTED_SOURCE = "f88ebed9d52dfe7d5c62d1ea994a70245063e365fb52ba80d478fc3104202823"


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def ref(path):
    return dict(path=path.relative_to(ROOT).as_posix(), sha256=sha(path), bytes=path.stat().st_size)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def decode(value):
    if value == "":
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def classify(event):
    kind, cause = event.get("kind"), event.get("cause_class")
    category = KIND_MAP.get(kind)
    explicit = {"exogenous_device_fault": category if category in CATEGORIES[:2] else None,
                **{name: name for name in SECONDARY}}.get(cause)
    if category and explicit and explicit != category:
        return dict(category="unresolved", basis="kind_cause_conflict", possible_categories=[category, explicit])
    if category:
        if cause and cause not in ("exogenous_device_fault", category, kind):
            return dict(category="unresolved", basis="unexpected_cause_for_kind", possible_categories=[category])
        return dict(category=category, basis="recorded_kind_and_cause" if cause else "documented_kind_mapping_missing_cause")
    if explicit:
        return dict(category="unresolved", basis="cause_without_required_recorded_kind", possible_categories=[explicit])
    return dict(category="other", basis="not_a_target_event")


def merge_events(*sources):
    merged, issues = {}, []
    for source_index, events in enumerate(sources):
        for event in events:
            row = dict(event)
            identity = row.get("event_id") or "missing:" + hashlib.sha256(encode(row).encode()).hexdigest()
            signature = (row.get("kind"), row.get("time_minute"), row.get("device"), classify(row)["category"])
            if identity in merged:
                if merged[identity][0] != signature:
                    issues.append(dict(event_id=identity, issue="conflicting_event_identity", before=merged[identity][0], after=signature))
                merged[identity][1]["export_sources"].append(source_index)
                for key, value in row.items():
                    if key not in merged[identity][1] or merged[identity][1][key] is None:
                        merged[identity][1][key] = value
            else:
                row["export_sources"] = [source_index]
                merged[identity] = (signature, row)
    return [x[1] for x in merged.values()], issues


def _minute(event):
    value = event.get("time_minute")
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def count_window(events, known, start, end, observed_until, canonical_class=None):
    known = {name: bool(known.get(name, False)) for name in CATEGORIES} if isinstance(known, dict) else dict.fromkeys(CATEGORIES, bool(known))
    counts, devices = dict.fromkeys(CATEGORIES, 0), set()
    exogenous_device_fields_known = True
    unresolved = []
    for event in events:
        classified = classify(event)
        category, minute = classified["category"], _minute(event)
        possible = classified.get("possible_categories", [category])
        if category in CATEGORIES or category == "unresolved":
            if minute is None:
                for key in possible:
                    known[key] = False
                unresolved.append(dict(event_id=event.get("event_id"), issue="missing_or_invalid_event_time"))
                continue
            if not start <= minute < end:
                continue
            if isinstance(observed_until, (int, float)) and minute > observed_until:
                continue
            if category == "unresolved":
                for key in possible:
                    known[key] = False
                unresolved.append(dict(event_id=event.get("event_id"), issue=classified["basis"]))
                continue
            counts[category] += 1
            if not event.get("event_id"):
                known[category] = False
                unresolved.append(dict(issue="missing_stable_event_id", kind=event.get("kind"), time_minute=minute))
            if category == "exogenous_fault_start":
                if event.get("device"):
                    devices.add((minute, event["device"]))
                elif isinstance(event.get("details"), dict) and event["details"]:
                    for device in event["details"]:
                        devices.add((minute, device))
                else:
                    exogenous_device_fields_known = False
    complete = isinstance(observed_until, (int, float)) and observed_until >= end
    observed = {k: counts[k] if known[k] else None for k in CATEGORIES}
    return dict(window_minutes=[start, end], complete=complete,
                unknown_tail_minutes=None if complete else [max(start, observed_until or start), end],
                observed_counts=observed, complete_counts={k: observed[k] if complete else None for k in CATEGORIES},
                observed_exogenous_device_onsets=len(devices) if known["exogenous_fault_start"] and exogenous_device_fields_known else None,
                healthy_functional_input_loss_count=None, healthy_functional_input_loss_status="not_directly_logged",
                unresolved=unresolved, canonical_class_is_not_actual_event=canonical_class)


def physical_scene_signature(scene):
    return hashlib.sha256(encode({k: scene.get(k) for k in PHYSICAL_KEYS}).encode("utf-8")).hexdigest()


def inherited_digest(value):
    """Exact producer/compatibility helper canonical JSON digest, without imports."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def is_sha(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def actual_matching_context(scene, terminal, manifest, policy_count):
    evidence = (terminal or {}).get("matching_evidence", {}).get(scene["id"], {})
    def actual(field, flag):
        values = evidence.get(field)
        return values[0] if (evidence.get(flag) is True and isinstance(values, list)
            and len(values) == policy_count and values and all(is_sha(x) for x in values)
            and len(set(values)) == 1) else None
    state = actual("initial_state_sha256", "same_initial_state")
    applied = actual("applied_exogenous_definition_sha256", "same_exogenous_definition")
    stream = {k: v for k, v in scene.items() if k in STREAM_FIELDS or k.startswith(("fault_", "shock_"))}
    own_manifest = inherited_digest(stream)
    own_bound = applied == own_manifest if applied is not None else None
    files = manifest.get("files")
    valid_inventory = (isinstance(files, list) and bool(files)
        and all(isinstance(x.get("path"), str) and x["path"] and is_sha(x.get("sha256")) for x in files)
        and len({x["path"] for x in files}) == len(files))
    return dict(input_inventory_sha256=inherited_digest(sorted((x["path"], x["sha256"]) for x in files)) if valid_inventory else None,
        actual_initial_state_sha256=state, actual_applied_definition_sha256=applied,
        own_manifest_applied_definition_sha256=own_manifest,
        own_applied_definition_matches_manifest=own_bound,
        applied_without_propagation_sha256=inherited_digest({k:v for k,v in stream.items() if k not in PROPAGATION_TOGGLES}) if own_bound else None,
        terminal_actual_matching_evidence=evidence)


def event_csv(path):
    grouped, counts, issues = defaultdict(list), defaultdict(int), []
    if not path.exists():
        return None, counts, issues
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for original in csv.DictReader(handle):
            key = (original.get("scenario_id"), original.get("policy_id"))
            counts[key] += 1
            if original.get("kind") not in KIND_MAP and original.get("cause_class") not in SECONDARY:
                continue
            row = {k: decode(v) for k, v in original.items() if v and k not in
                   ("raw_path", "raw_sha256", "schemas")}
            grouped[key].append(row)
    return grouped, counts, issues


def write_csv(path, rows):
    keys = sorted({k for row in rows for k in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows({k: encode(v) if isinstance(v, (dict, list)) else v for k, v in row.items()} for row in rows)


def _same(value, other, issue, issues):
    if value != other:
        issues.append(dict(issue=issue, expected=value, actual=other))


def collect_batch(directory):
    plan_path, manifest_path, terminal_path = (directory / x for x in ("plan.json", "manifest.json", "BATCH_RESULT.json"))
    plan, manifest = load(plan_path), load(manifest_path)
    terminal = load(terminal_path) if terminal_path.exists() else None
    issues, proof = [], [ref(plan_path), ref(manifest_path)]
    if terminal:
        proof.append(ref(terminal_path))
        _same(sha(plan_path), terminal.get("plan_sha256"), "terminal_plan_sha_mismatch", issues)
        _same(sha(manifest_path), terminal.get("manifest_sha256"), "terminal_manifest_sha_mismatch", issues)
        _same(True, terminal.get("source_unchanged"), "terminal_source_changed_or_unknown", issues)
    else:
        issues.append(dict(issue="terminal_missing"))
    csv_path = directory / "analysis/EVENT_TIMELINE.csv"
    csv_events, csv_counts, csv_issues = event_csv(csv_path)
    issues.extend(csv_issues)
    if csv_events is not None:
        proof.append(ref(csv_path))
    cases = []
    for scene in manifest["scenarios"]:
        for policy in plan["policy_specs"]:
            policy_id, scenario_id = policy["policy_id"], scene["id"]
            result_path = directory / "pairs" / (scenario_id + "__" + policy_id) / "RESULT.json"
            result = load(result_path) if result_path.exists() else None
            if result:
                proof.append(ref(result_path))
            analysis = (result or {}).get("analysis") or {}
            propagation = (analysis.get("v18") or {}).get("propagation")
            local_issues, sources, known = [], [], dict.fromkeys(CATEGORIES, False)
            if csv_events is not None and result:
                case_csv_events = csv_events.get((scenario_id, policy_id), [])
                sources.append(case_csv_events)
                for event in case_csv_events:
                    _same(manifest.get("simulator_hash"), event.get("simulator_hash"), "event_CSV_source_mismatch", local_issues)
                    _same(manifest.get("signature"), event.get("manifest_signature"), "event_CSV_manifest_mismatch", local_issues)
                known = dict.fromkeys(CATEGORIES, True)
                expected_rows = (analysis.get("timeline_row_counts") or {}).get("events")
                if expected_rows is not None and csv_counts.get((scenario_id, policy_id), 0) != expected_rows:
                    local_issues.append(dict(issue="event_CSV_row_count_mismatch", expected=expected_rows,
                                             actual=csv_counts.get((scenario_id, policy_id), 0)))
                    known = dict.fromkeys(CATEGORIES, False)
            timelines = analysis.get("timelines") or {}
            if isinstance(timelines.get("events"), list):
                sources.append(timelines["events"])
                known = dict.fromkeys(CATEGORIES, True)
            if isinstance(propagation, dict):
                for category, field in SUMMARY_KEYS.items():
                    if isinstance(propagation.get(field), list):
                        sources.append(propagation[field])
                        known[category] = True
            faults = ((analysis.get("v18") or {}).get("recovery_stages") or {}).get("observed_fault_events")
            if isinstance(faults, list):
                sources.append(faults)
                known["exogenous_fault_start"] = known["exogenous_fault_end"] = True
            events, conflicts = merge_events(*sources)
            if conflicts:
                local_issues.extend(conflicts)
                known = dict.fromkeys(CATEGORIES, False)
            identity = analysis.get("identity") or {}
            if result:
                _same(manifest.get("simulator_hash"), identity.get("simulator_hash"), "RESULT_source_mismatch", local_issues)
                _same(manifest.get("signature"), result.get("manifest_signature"), "RESULT_manifest_mismatch", local_issues)
                _same(manifest.get("execution_model_id"), result.get("model_id"), "RESULT_model_mismatch", local_issues)
            audit = (analysis.get("v19") or {}).get("episode_audit") or {}
            registered_end = float(scene["duration_steps"]) * float(scene.get("step_minutes", 15))
            observed = analysis.get("observed_until_minute")
            cohort = plan.get("phase")
            base = dict(batch=directory.name, cohort=cohort, scenario_id=scenario_id, policy_id=policy_id,
                canonical_class=scene.get("canonical_class"), legacy_class=scene.get("event_class"),
                source_binding_digest=manifest.get("simulator_hash"), freeze_signature=manifest.get("freeze_signature"),
                model_config_signature=manifest.get("model_config_signature"), policy_signature=hashlib.sha256(encode(policy).encode()).hexdigest(),
                execution_model_id=manifest.get("execution_model_id"),
                source_day_index=scene.get("source_day_index"), propagation_pair_id=scene.get("propagation_pair_id"),
                propagation_allowed=(propagation or {}).get("allowed"),
                registered_enable_propagation=scene.get("enable_propagation"),
                matched_OFF=(propagation or {}).get("matched_OFF"),
                registered_initial_fault_devices=sorted((scene.get("faults") or {}).keys()),
                registered_initial_fault_device_count=len(scene.get("faults") or {}),
                physical_scene_signature=physical_scene_signature(scene),
                status=(result or {}).get("status", "not_produced"), error=(result or {}).get("error"),
                analysis_error=(result or {}).get("analysis_error"), audit_status=audit.get("status", "unknown"),
                registered_end_minute=registered_end, observed_until_minute=observed,
                terminal_present=terminal is not None, result_sha256=sha(result_path) if result else None,
                event_source_known=known, issues=local_issues,
                **actual_matching_context(scene, terminal, manifest, len(plan["policy_specs"])))
            windows = {name: count_window(events, known, 0., end, observed, scene.get("canonical_class"))
                       for name, end in [("primary_0_600", 600.), ("registered_full", registered_end)]}
            details = []
            for event in events:
                category = classify(event)
                if category["category"] in CATEGORIES or category["category"] == "unresolved":
                    minute = _minute(event)
                    details.append(dict(event=event, classification=category,
                                        inside_primary_0_600=None if minute is None else 0 <= minute < 600,
                                        inside_registered_full=None if minute is None else 0 <= minute < registered_end))
            cases.append(dict(identity=base, windows=windows, actual_event_details=details,
                              functional_dependency_event_count=None, functional_dependency_status="not_directly_logged"))
    return cases, dict(batch=directory.name, terminal=terminal is not None, registered_pairs=plan.get("registered_pairs"),
                       observed_results=sum(x["identity"]["status"] != "not_produced" for x in cases),
                       terminal_complete_full=(terminal or {}).get("complete_full"),
                       terminal_unknown_full=(terminal or {}).get("unknown_full"), issues=issues, proof_files=proof)


def flat_counts(cases):
    rows = []
    for case in cases:
        for window, values in case["windows"].items():
            row = dict(case["identity"], window=window, complete=values["complete"], unknown_tail_minutes=values["unknown_tail_minutes"],
                       observed_exogenous_device_onsets=values["observed_exogenous_device_onsets"],
                       healthy_functional_input_loss_count=None, healthy_functional_input_loss_status="not_directly_logged")
            for category in CATEGORIES:
                row[category + "_observed_count"] = values["observed_counts"][category]
                row[category + "_complete_count"] = values["complete_counts"][category]
            row["unresolved_events"] = values["unresolved"]
            rows.append(row)
    return rows


def aggregate_L2(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["canonical_class"] == "L2":
            key = tuple(row[k] for k in ("cohort", "source_binding_digest", "freeze_signature", "model_config_signature", "policy_id", "window"))
            groups[key].append(row)
    output = []
    for key, values in sorted(groups.items(), key=lambda x: str(x[0])):
        row = dict(zip(("cohort", "source_binding_digest", "freeze_signature", "model_config_signature", "policy_id", "window"), key))
        row.update(registered_case_count=len(values), complete_case_count=sum(v["complete"] for v in values))
        for category in CATEGORIES:
            counts = [v[category + "_complete_count"] for v in values]
            row[category + "_known_case_count"] = sum(v is not None for v in counts)
            row[category + "_unknown_case_count"] = sum(v is None for v in counts)
            row[category + "_known_subtotal"] = sum(v for v in counts if v is not None)
            row[category + "_all_registered_count"] = sum(counts) if all(v is not None for v in counts) else None
            row[category + "_cases_with_event"] = sum(v > 0 for v in counts if v is not None)
        output.append(row)
    return output


def ON_OFF(rows):
    groups = defaultdict(list)
    for row in rows:
        if row.get("propagation_pair_id"):
            key = tuple(row[k] for k in ("cohort", "source_binding_digest", "freeze_signature", "model_config_signature", "policy_id", "propagation_pair_id", "window"))
            groups[key].append(row)
    output = []
    for key, values in sorted(groups.items(), key=lambda x: str(x[0])):
        row = dict(zip(("cohort", "source_binding_digest", "freeze_signature", "model_config_signature", "policy_id", "propagation_pair_id", "window"), key))
        on = [v for v in values if v["propagation_allowed"] is True]
        off = [v for v in values if v["propagation_allowed"] is False]
        row.update(ON_case_count=len(on), OFF_case_count=len(off), ON_scenarios=[v["scenario_id"] for v in on], OFF_scenarios=[v["scenario_id"] for v in off])
        row.update(ON_canonical_classes=[v["canonical_class"] for v in on], OFF_canonical_classes=[v["canonical_class"] for v in off])
        registered = len(on) == len(off) == 1 and on[0]["physical_scene_signature"] == off[0]["physical_scene_signature"] and on[0]["policy_signature"] == off[0]["policy_signature"]
        row["registered_definition_match"] = registered
        actual_evidence = {}
        if len(on) == len(off) == 1:
            for field in ("input_inventory_sha256", "actual_initial_state_sha256", "applied_without_propagation_sha256"):
                left, right = on[0].get(field), off[0].get(field)
                actual_evidence[field] = dict(ON=left, OFF=right, known_equal=is_sha(left) and is_sha(right) and left == right)
            actual_evidence["each_applied_definition_attested_and_bound_to_own_manifest"] = (
                on[0].get("own_applied_definition_matches_manifest") is True
                and off[0].get("own_applied_definition_matches_manifest") is True
                and is_sha(on[0].get("actual_applied_definition_sha256"))
                and is_sha(off[0].get("actual_applied_definition_sha256")))
            actual_evidence["full_applied_hash_ON"] = on[0].get("actual_applied_definition_sha256")
            actual_evidence["full_applied_hash_OFF"] = off[0].get("actual_applied_definition_sha256")
        matched = registered and all(actual_evidence.get(field, {}).get("known_equal") is True
                      for field in ("input_inventory_sha256", "actual_initial_state_sha256", "applied_without_propagation_sha256")) and actual_evidence.get("each_applied_definition_attested_and_bound_to_own_manifest") is True
        row["actual_match_evidence"] = actual_evidence
        row["actual_cross_ON_OFF_metadata_match"] = matched
        row["match_status"] = "actual_metadata_matched_except_propagation_toggle" if matched else "registered_definition_only_actual_evidence_unknown_or_mismatched" if registered else "missing_ambiguous_or_registered_definition_mismatch"
        for category in SECONDARY:
            a = on[0][category + "_complete_count"] if len(on) == 1 else None
            b = off[0][category + "_complete_count"] if len(off) == 1 else None
            row[category + "_ON"] = a
            row[category + "_OFF"] = b
            row[category + "_ON_minus_OFF"] = a - b if matched and a is not None and b is not None else None
        output.append(row)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-prefix", action="append", default=[])
    parser.add_argument("--batch", action="append", default=[])
    parser.add_argument("--name", required=True)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--freeze", type=Path, default=B19 / "MODEL_FREEZE_CLOSEOUT01.json")
    args = parser.parse_args()
    if Path(args.name).name != args.name or args.name in (".", ".."):
        parser.error("--name must be a single new output directory name")
    experiments = B19 / "experiments"
    prefixes = args.batch_prefix or ([] if args.batch else list(DEFAULT_PREFIXES))
    directories, missing = {}, []
    for prefix in prefixes:
        found = [p for p in experiments.iterdir() if p.is_dir() and p.name.startswith(prefix)]
        if not found:
            missing.append(prefix)
        directories.update((p.name, p) for p in found)
    for name in args.batch:
        if Path(name).name != name:
            parser.error("invalid batch name")
        path = experiments / name
        if not path.is_dir():
            missing.append(name)
        else:
            directories[name] = path
    cases, batches = [], []
    for directory in sorted(directories.values()):
        values, batch = collect_batch(directory)
        cases.extend(values)
        batches.append(batch)
    terminal_complete = not missing and bool(batches) and all(b["terminal"] and b["terminal_complete_full"] == b["registered_pairs"] and b["terminal_unknown_full"] == 0 and not b["issues"] for b in batches)
    pairs_complete = all(c["identity"]["status"] == "completed" and c["identity"]["audit_status"] == "passed" and c["identity"]["error"] is None and c["identity"]["analysis_error"] is None and c["windows"]["registered_full"]["complete"] and not c["identity"]["issues"] for c in cases)
    bindings = sorted({str(c["identity"]["source_binding_digest"]) for c in cases})
    freezes = sorted({str(c["identity"]["freeze_signature"]) for c in cases})
    frozen = load(args.freeze) if args.freeze.exists() else None
    final_bound = bindings == [EXPECTED_SOURCE] and len(freezes) == 1 and freezes != ["None"] and frozen is not None and freezes == [frozen.get("signature")]
    formal = terminal_complete and pairs_complete and final_bound
    if not args.allow_partial and not formal:
        print(encode(dict(status="not_ready", missing_prefixes=missing, batches=len(batches), cases=len(cases), terminal_complete=terminal_complete,
                          pairs_complete=pairs_complete, source_bindings=bindings, freezes=freezes)))
        return 2
    output = B19 / "review/event_layers" / args.name
    output.mkdir(parents=True, exist_ok=False)
    rows = flat_counts(cases)
    details = [dict(case["identity"], **entry) for case in cases for entry in case["actual_event_details"]]
    detail_rows = [dict(entry, **{key: entry["event"].get(key) for key in
                   ("event_id", "kind", "time_minute", "device", "origin", "cause_class", "trigger", "parent_event_id", "common_cause")},
                   actual_event_category=entry["classification"]["category"],
                   classification_basis=entry["classification"]["basis"]) for entry in details]
    aggregates, pairs = aggregate_L2(rows), ON_OFF(rows)
    write_csv(output / "CASE_EVENT_COUNTS.csv", rows)
    write_csv(output / "ACTUAL_EVENT_DETAILS.csv", detail_rows)
    write_csv(output / "L2_BY_POLICY.csv", aggregates)
    write_csv(output / "ON_OFF.csv", pairs)
    report = dict(schema="v19.actual_event_layer_review.v1", generated_utc=datetime.now(timezone.utc).isoformat(),
        status="formal_terminal_complete" if formal else "partial_or_development_snapshot", formal_terminal_complete=formal,
        collector=ref(Path(__file__).resolve()), selected_prefixes=prefixes, missing_prefixes=missing,
        source_bindings=bindings, freeze_signatures=freezes, registered_case_policy_count=len(cases),
        freeze_artifact=ref(args.freeze.resolve()) if frozen is not None else None,
        window_semantics="Half-open [0,600) and [0,registered_end); events at end are excluded, not rounded",
        classification_source="Recorded event kind plus src/hospital_ies/dataset_export.py:81-108 cause mapping; no scenario label inference",
        count_unit="Recorded distinct event per case/policy; exogenous multi-device start is one event, device-onsets reported separately",
        ON_OFF_actual_matching="Separate registered definitions from producer terminal actual reset and input inventory digests; bind each actual applied-definition digest to its own manifest before removing only enable_propagation/propagation_enabled for cross-ON/OFF comparison. Full applied hashes may differ. No raw re-audit or equal policy-dependent injection state claim.",
        functional_dependency=dict(count=None, status="not_directly_logged", zero_actual_output_is_failure=False, is_protection_trip=False),
        physical_episodes_started=0, raw_files_opened=0, annual_files_opened=0,
        cases=cases, batch_bindings=batches, L2_by_policy=aggregates, ON_OFF=pairs,
        source_anchors=[ref(ROOT / "src/hospital_ies/dataset_export.py"), ref(ROOT / "tools/execution_recovery/analysis.py"),
                        ref(ROOT / "tools/operating_response/runner.py"), ref(B19 / "scripts/summarize_compatibility.py")])
    representatives = B19 / "review/event_layers/FUNCTIONAL_DEPENDENCY_REPRESENTATIVES.json"
    if representatives.exists():
        report["functional_dependency_representatives"] = ref(representatives)
    report["event_unknown_case_window_count"] = sum(any(v is None for v in window["complete_counts"].values())
                                                       for case in cases for window in case["windows"].values())
    if formal and report["event_unknown_case_window_count"]:
        report["status"] = "formal_terminal_complete_with_event_unknowns"
    (output / "EVENT_LAYER_REVIEW.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt = dict(status=report["status"], files=[ref(p) for p in sorted(output.iterdir()) if p.is_file()],
                   cases=len(cases), L2_rows=len(aggregates), ON_OFF_rows=len(pairs), actual_event_details=len(details),
                   raw_files_opened=0, annual_files_opened=0, physical_episodes_started=0)
    (output / "RECEIPT.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(encode(dict(output=output.relative_to(ROOT).as_posix(), **{k:v for k,v in receipt.items() if k != "files"})))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
