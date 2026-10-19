"""M20 thin saved-analysis layer; no simulation, training, raw or annual reads.

The inherited actual-service classifiers and episode audit are called without
changing their thresholds. Compatibility table/schema names from the delegated
exporter identify a format, not reuse of an old physical result.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import csv
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs/v20_load_accounting_20261019_run01"
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from tools.execution_recovery import analysis as inherited
from tools.execution_recovery import summarize as inherited_summary
from tools.execution_recovery.review_saved import audit_payload
from tools.model_closeout.emissions import evaluate_emissions

MODEL_ID = "M20_load_accounting"
SCHEMA = "hospital_ies.load_accounting_saved_analysis.v20.v1"
AUDIT_FIELDS = ("steam_kWh_per_lb", "exchanger_eff", "abs_steam_lb_per_rt",
                "abs_electric_kW_per_rt", "ec_electric_kW_per_rt")
DEFAULT_TIMELINE_SCENARIOS = ("main_final_r1_001", "main_final_r1_003", "main_final_r1_007",
                              "long_final_r1_000", "long_final_r1_005")
RESOURCE_FIELDS = ("GT_gas_MMBtu", "boiler_gas_MMBtu", "natural_gas_total_MMBtu", "grid_purchase_kWh",
    "grid_fee_USD", "GT_gas_fee_USD", "boiler_gas_fee_USD", "actual_energy_fee_USD", "accounting_cost_USD",
    "DG_electric_work_kWh", "DG_shaft_work_kWh", "DG_loss_work_kWh", "DG_rotor_delta_kWh",
    "DG_fuel_budget_debit_kWh_equivalent", "DG_fuel_budget_debit_shaft_equivalent_kWh",
    "DG_original_electric_fee_USD", "DG_shaft_equivalent_fee_USD", "partial_DG_receipt_not_interpolated")
IDENTITY_FIELDS = ("batch", "cohort", "scenario_id", "policy_id", "model_id", "source_day_index",
    "status", "analysis_error", "error_type", "result_path", "result_sha256", "raw_sha256",
    "source_binding_sha256", "model_config_sha256", "policy_panel_sha256", "registered_scene_sha256", "canonical_class",
    "core_four_class_denominator", "window", "window_start_minute", "window_end_minute",
    "complete", "registered_full_known", "unknown_tail_minutes", "observed_until_minute",
    "observation_value_scope", "comparison_matching_eligible", "comparison_matching_reason",
    "comparison_key", "comparison_duplicate_in_cohort", "panel_complete_intersection", "episode_audit_status")


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _digest(value):
    return inherited_summary.digest(value)


def _write_csv(path, rows):
    inherited.engineering._write_csv(Path(path), rows)


def _registry(path=None):
    path = Path(path) if path is not None else RUN / "references/EMISSIONS_FACTOR_REGISTRY.json"
    registry = _read(path)
    for key, case in registry["cases"].items():
        canonical = json.dumps(case, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != registry["case_canonical_sha256"][key]:
            raise ValueError("Changed registered emissions factor case: " + key)
    return registry, path


def analyze_payload(payload, env):
    """Analyze the caller's M20 in-memory episode and retain inherited metrics."""
    if payload.get("execution_model_id") != MODEL_ID:
        raise ValueError("M20 analysis requires an explicit M20_load_accounting payload")
    result = inherited.analyze_payload(payload, env)
    plant = getattr(env, "plant", None) if env is not None else None
    parameters = ({key: getattr(plant, key, None) for key in AUDIT_FIELDS} if plant is not None
                  else dict(payload.get("plant_balance_parameters") or {}))
    missing = [key for key in AUDIT_FIELDS if parameters.get(key) is None]
    if missing:
        audit = dict(status="unknown_parameters", missing_parameters=missing,
                     issues=None, no_tail_imputation=True)
    else:
        try:
            audit = audit_payload(payload, parameters)
            audit["status"] = "passed" if not audit["issues"] else "issues_recorded"
        except Exception as error:
            audit = dict(status="audit_error", issues=None, error_type=type(error).__name__,
                         message=str(error), no_tail_imputation=True)
    result["schema"] = SCHEMA
    result["v20"] = dict(schema=SCHEMA, execution_model_id=MODEL_ID, episode_audit=audit,
        metrics_delegate="tools.execution_recovery.analysis; unchanged classifiers and thresholds",
        audit_delegate="tools.execution_recovery.review_saved.audit_payload",
        compatibility_v18_key_is_format_only=True, duplicated_physical_timeline=False,
        actual_M20_payload_required=True, physical_episodes_started=0, additional_raw_opens=0)
    from .service_ledger import service_ledger
    result["v20"]["service_ledgers"] = {
        name: service_ledger(payload, *window["window_minutes"])
        for name, window in result.get("v18", {}).get("windows", {}).items()
        if window.get("window_minutes") is not None
        and len(window["window_minutes"]) == 2
        and window["window_minutes"][1] >= window["window_minutes"][0]}
    return result


def _window_rows(record, batch, result_path, context, scene=None):
    """Use old flat metrics, adding explicit M20 identity and prefix scope."""
    analysis = record.get("analysis")
    if analysis is not None:
        rows = list(inherited_summary.rows(record, batch, result_path, context))
        registered = bool(analysis["v18"]["windows"]["registered_full"]["complete"])
    else:
        scene = scene or {}
        requested = float(scene.get("duration_steps", 0)) * float(scene.get("step_minutes", 15.))
        windows = [("full", 0., 600.), ("registered_full", 0., requested)]
        rows = [dict(context, batch=batch, scenario_id=record["scenario_id"], policy_id=record["policy_id"],
            model_id=record.get("model_id"), status=record.get("status"), analysis_error=bool(record.get("analysis_error")),
            result_path=result_path, raw_sha256=record.get("raw_sha256"),
            error_type=(record.get("error") or {}).get("type"), window=window,
            window_start_minute=start, window_end_minute=end, complete=False,
            unknown_tail_minutes=[start, end], observed_until_minute=None) for window, start, end in windows]
        registered = False
    audit = (analysis or {}).get("v20", {}).get("episode_audit", {})
    for row in rows:
        row.update(registered_full_known=registered,
            observation_value_scope=("unavailable" if analysis is None else
                "registered_window" if row["complete"] else "observed_prefix"),
            episode_audit_status=audit.get("status", "not_available"),
            DG_fuel_budget_debit_shaft_equivalent_kWh=row.get("DG_fuel_budget_debit_kWh_equivalent"))
    return rows


def _resource_rows(rows):
    return [{key: row.get(key) for key in (*IDENTITY_FIELDS, *RESOURCE_FIELDS)} for row in rows]


def _emission_rows(resources, registry):
    rows = []
    for resource in resources:
        for key, case in registry["cases"].items():
            try:
                value = evaluate_emissions([resource], case)[0]
            except Exception as error:
                value = dict(resource, emissions_factor_case_id=key,
                    emissions_factor_sha256=registry["case_canonical_sha256"][key],
                    emissions_status="input_or_accounting_error", emissions_error_type=type(error).__name__,
                    emissions_error=str(error), DG_exact_CO2_unknown=True,
                    grid_plus_gas_CO2_kg=None, DG_CO2_kg_conditional=None, total_CO2_kg_conditional=None,
                    emissions_is_dispatch_objective=False)
            value["emissions_value_scope"] = resource.get("observation_value_scope")
            rows.append(value)
    return rows


def export_rows(records, out):
    """Runner-compatible in-memory export, plus small M20 resource/emission tables."""
    records = list(records)
    out = Path(out)
    if out.exists():
        raise FileExistsError("M20 exports are immutable; use a fresh output")
    registry, registry_path = _registry()
    flat = []
    audits = []
    for record in records:
        analysis = record["analysis"]
        if analysis.get("execution_model_id") != MODEL_ID:
            raise ValueError("Cannot export another model's physical result as M20")
        normalized = dict(record)
        normalized.setdefault("scenario_id", analysis.get("scenario_id"))
        normalized.setdefault("policy_id", analysis.get("identity", {}).get("policy_id"))
        normalized.setdefault("model_id", MODEL_ID)
        normalized.setdefault("status", analysis.get("model_domain", {}).get("status"))
        if normalized["model_id"] != MODEL_ID:
            raise ValueError("M20 record model binding mismatch")
        flat.extend(_window_rows(normalized, out.parent.name, None, {}))
        audits.append(dict(scenario_id=normalized["scenario_id"], policy_id=normalized["policy_id"],
            model_id=MODEL_ID, audit=analysis.get("v20", {}).get("episode_audit")))
    resources = _resource_rows(flat)
    emissions = _emission_rows(resources, registry)
    delegated = inherited.export_rows(records, out)
    _write_csv(out / "V20_RESOURCES.csv", resources)
    _write_csv(out / "V20_EMISSIONS.csv", emissions)
    _write(out / "V20_EPISODE_AUDIT.json", audits)
    names = ("V20_RESOURCES.csv", "V20_EMISSIONS.csv", "V20_EPISODE_AUDIT.json")
    receipt = dict(schema=SCHEMA + ".receipt", registered_trajectory_count=len(records),
        compatibility_export_receipt=delegated, compatibility_filenames_are_new_M20_data=True,
        output_sha256={name: _sha(out / name) for name in names},
        emissions_registry_path=str(registry_path), emissions_registry_sha256=_sha(registry_path),
        analysis_program_sha256=_sha(Path(__file__)), physical_episodes_started=0,
        additional_raw_opens=0, annual_inputs_loaded=0, old_outputs_modified=False)
    _write(out / "V20_ANALYSIS_RECEIPT.json", receipt)
    return receipt


def _read_batch(directory):
    plan_path = directory / "plan.json"
    manifest_path = directory / "manifest.json"
    plan, manifest = _read(plan_path), _read(manifest_path)
    problems = []
    if _sha(manifest_path) != plan.get("manifest_sha256"):
        problems.append("registered_manifest_sha_mismatch")
    if plan.get("model_id") != MODEL_ID or manifest.get("execution_model_id", MODEL_ID) != MODEL_ID:
        problems.append("registered_model_is_not_M20")
    if plan.get("manifest_signature") not in (None, manifest.get("signature")):
        problems.append("registered_manifest_signature_mismatch")
    for key in ("model_config", "source_sha256"):
        if key in manifest and manifest[key] != plan.get(key):
            problems.append("registered_" + key + "_mismatch")
    terminal_path = directory / "BATCH_RESULT.json"
    terminal = _read(terminal_path) if terminal_path.is_file() else {}
    terminal_bound = (terminal.get("source_unchanged") is True
        and terminal.get("manifest_sha256") == _sha(manifest_path)
        and terminal.get("plan_sha256") == _sha(plan_path))
    policies = [p["policy_id"] for p in plan["policy_specs"]]
    if len(policies) != len(set(policies)) or not policies:
        problems.append("nonunique_or_empty_policy_panel")
    context = dict(cohort=plan.get("cohort_id", directory.name.rsplit("_", 1)[0]),
        source_binding_sha256=_digest(plan["source_sha256"]),
        model_config_sha256=_digest(plan["model_config"]), policy_panel_sha256=_digest(plan["policy_specs"]))
    return plan, manifest, terminal, terminal_bound, policies, context, problems


def _collect(directory):
    plan, manifest, terminal, bound, policies, context, problems = _read_batch(directory)
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    rows, attempts, inputs = [], [], []
    expected = {(scene_id, policy): directory / "pairs" / (scene_id + "__" + policy) / "RESULT.json"
                for scene_id in scenes for policy in policies}
    paths = [(key, path, True) for key, path in expected.items()]
    paths.extend(((None, path, False) for path in sorted((directory / "pairs").glob("*/RESULT.json"))
                  if path not in set(expected.values())))
    for key, path, registered in paths:
        exists = path.is_file()
        if exists:
            inputs.append(dict(path=str(path), sha256=_sha(path), kind="compact_result"))
            try:
                record = _read(path)
            except Exception as error:
                record = dict(scenario_id=key[0] if key else path.parent.name,
                    policy_id=key[1] if key else "unknown", model_id=plan["model_id"],
                    status="result_read_error", error=dict(type=type(error).__name__, message=str(error)), analysis=None)
        else:
            record = dict(scenario_id=key[0], policy_id=key[1], model_id=plan["model_id"],
                status="missing_result", analysis=None, error=dict(type="MissingRegisteredResult"))
        scene = scenes.get(record.get("scenario_id"), {})
        scene_sha = _digest(scene) if scene else None
        reasons = list(problems)
        if not registered:
            reasons.append("unregistered_result_path")
        if key is not None and (record.get("scenario_id"), record.get("policy_id")) != key:
            reasons.append("registered_scenario_policy_identity_mismatch")
        if record.get("model_id") != MODEL_ID:
            reasons.append("result_model_is_not_M20")
        analysis = record.get("analysis")
        if analysis and analysis.get("execution_model_id") != MODEL_ID:
            reasons.append("analysis_model_is_not_M20")
        if record.get("manifest_signature") != manifest.get("signature"):
            reasons.append("result_manifest_signature_mismatch")
        if record.get("model_config_signature") not in (None, _digest(plan["model_config"])):
            reasons.append("result_model_config_signature_mismatch")
        evidence = terminal.get("matching_evidence", {}).get(record.get("scenario_id"))
        if not bound or not inherited_summary.valid_matching_evidence(evidence, len(policies)):
            reasons.append("terminal_binding_or_actual_reset_exogenous_matching_missing_or_false")
        eligible = not reasons
        current = dict(context, registered_scene_sha256=scene_sha,
            canonical_class=scene.get("canonical_class"),
            core_four_class_denominator=bool(scene.get("core_four_class_denominator", False)),
            result_sha256=_sha(path) if exists else None,
            comparison_matching_eligible=eligible,
            comparison_matching_reason="bound_actual_reset_and_exogenous_hashes_match" if eligible else ";".join(reasons),
            comparison_key=_digest([record["scenario_id"], scene_sha, evidence["initial_state_sha256"][0],
                evidence["applied_exogenous_definition_sha256"][0]]) if eligible else None)
        try:
            rows.extend(_window_rows(record, directory.name, str(path) if exists else None, current, scene))
        except Exception as error:
            record = dict(record, analysis=None, analysis_error=dict(type=type(error).__name__, message=str(error)))
            current.update(comparison_matching_eligible=False,
                comparison_matching_reason=current["comparison_matching_reason"] + ";saved_analysis_read_error")
            rows.extend(_window_rows(record, directory.name, str(path), current, scene))
        attempts.append(dict(batch=directory.name, **{k:record.get(k) for k in
            ("scenario_id", "policy_id", "model_id", "status", "raw_path", "raw_sha256", "elapsed_seconds")},
            registered=registered, result_file_present=exists, result_path=str(path),
            result_sha256=current["result_sha256"], error_json=record.get("error"),
            analysis_error_json=record.get("analysis_error"), analysis_available=record.get("analysis") is not None,
            comparison_matching_eligible=current["comparison_matching_eligible"],
            comparison_matching_reason=current["comparison_matching_reason"]))
    for name in ("plan.json", "manifest.json", "BATCH_RESULT.json"):
        path = directory / name
        if path.is_file():
            inputs.append(dict(path=str(path), sha256=_sha(path), kind="registration_or_terminal"))
    return rows, attempts, inputs, policies, problems


def _metrics(rows):
    candidates = {key for row in rows for key in row if key.endswith(("_ENS_kWh", "_material_minutes",
        "_material_union_minutes", "_temperature_violation_minutes"))}
    return sorted(candidates | {"actual_energy_fee_USD", "natural_gas_total_MMBtu", "grid_purchase_kWh",
        "DG_electric_work_kWh", "DG_fuel_budget_debit_shaft_equivalent_kWh", "original_P0_classifier_failure"})


def _pair_and_aggregate(rows, policies_by_batch):
    unique = defaultdict(list)
    for row in rows:
        row["comparison_duplicate_in_cohort"] = False
        if row["complete"] and row["comparison_matching_eligible"] and row["status"] == "completed":
            key = tuple(row.get(k) for k in ("cohort", "source_binding_sha256", "model_config_sha256",
                "policy_panel_sha256", "window", "window_start_minute", "window_end_minute",
                "policy_id", "comparison_key"))
            unique[key].append(row)
    for duplicate in unique.values():
        if len(duplicate) > 1:
            for row in duplicate:
                row["comparison_duplicate_in_cohort"] = True
    groups = defaultdict(list)
    for row in rows:
        key = (row["batch"], row["scenario_id"], row["window"], row.get("window_start_minute"), row.get("window_end_minute"))
        groups[key].append(row)
    pairs = []
    fields = _metrics(rows)
    for _, values in groups.items():
        policies = policies_by_batch[values[0]["batch"]]
        panel = {p: [r for r in values if r["policy_id"] == p] for p in policies}
        complete = all(len(own) == 1 and own[0]["complete"] and own[0]["comparison_matching_eligible"]
            and own[0]["status"] == "completed" and not own[0].get("analysis_error")
            and not own[0]["comparison_duplicate_in_cohort"] for own in panel.values())
        keys = {own[0]["comparison_key"] for own in panel.values() if len(own) == 1}
        complete = complete and len(keys) == 1
        for row in values:
            row["panel_complete_intersection"] = bool(complete)
        for left_id, right_id in itertools.combinations(sorted(policies), 2):
            left = panel[left_id][0] if len(panel[left_id]) == 1 else None
            right = panel[right_id][0] if len(panel[right_id]) == 1 else None
            row = {k: values[0].get(k) for k in ("batch", "cohort", "scenario_id", "window",
                "window_start_minute", "window_end_minute", "canonical_class", "source_binding_sha256", "model_config_sha256")}
            row.update(model_id=MODEL_ID, left_policy_id=left_id, right_policy_id=right_id,
                pair_eligible=bool(complete), eligibility_scope="complete intersection of entire registered policy panel",
                policy_panel_sha256=values[0].get("policy_panel_sha256"),
                pair_ineligible_reason=None if complete else "incomplete, unmatched, duplicated or failed member of registered panel",
                left_status=left.get("status") if left else "missing_or_duplicate",
                right_status=right.get("status") if right else "missing_or_duplicate")
            for field in fields:
                a = left.get(field) if left else None
                b = right.get(field) if right else None
                row[field + "_left"] = a if complete else None
                row[field + "_right"] = b if complete else None
                row[field + "_delta_right_minus_left"] = b-a if complete and a is not None and b is not None else None
            pairs.append(row)
    aggregates = []
    panels = defaultdict(list)
    for row in rows:
        key = (row.get("cohort"), row.get("source_binding_sha256"), row.get("model_config_sha256"), row.get("policy_panel_sha256"),
               row["window"], row.get("window_start_minute"), row.get("window_end_minute"), row["policy_id"])
        panels[key].append(row)
    for key, values in sorted(panels.items(), key=lambda item: str(item[0])):
        cohort, source, config, policy_panel, window, start, end, policy = key
        classes = {"ALL": values}
        for canonical in sorted({r.get("canonical_class") for r in values if r.get("canonical_class") is not None}):
            classes[canonical] = [r for r in values if r.get("canonical_class") == canonical]
        for canonical, own in classes.items():
            matched = [r for r in own if r.get("panel_complete_intersection")]
            result = dict(cohort=cohort, model_id=MODEL_ID, source_binding_sha256=source,
                model_config_sha256=config, policy_panel_sha256=policy_panel,
                window=window, window_start_minute=start, window_end_minute=end,
                policy_id=policy, canonical_class=canonical, attempts=len(own),
                own_complete=sum(bool(r["complete"]) for r in own), unknown=sum(not r["complete"] for r in own),
                matched_complete=len(matched), matched_scenario_ids=sorted({r["scenario_id"] for r in matched}),
                totals_scope="exact source/model/config/window plus actual reset/exogenous hashes; full panel complete intersection")
            for field in fields:
                known = [r[field] for r in matched if r.get(field) is not None]
                result[field] = sum(known) if matched and len(known) == len(matched) else None
                result[field + "_known_sum"] = sum(known) if known else None
                result[field + "_unknown_records"] = len(matched)-len(known)
            aggregates.append(result)
    return pairs, aggregates


def _timelines(directories, selected, out):
    rows, inputs, found = [], [], set()
    names = ("EVENT_TIMELINE.csv", "COMMAND_LIFECYCLE.csv", "COMMAND_EXECUTION_INTERVALS.csv",
             "ENGINEERING_INTERVALS.csv", "V18_RECOVERY_STAGES.csv")
    for directory in directories:
        for name in names:
            path = directory / "analysis" / name
            if not path.is_file():
                continue
            inputs.append(dict(path=str(path), sha256=_sha(path), kind="producer_csv"))
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    if row.get("scenario_id") in selected and row.get("model_id", row.get("execution_model_id")) == MODEL_ID:
                        found.add(row["scenario_id"])
                        rows.append(dict(row, batch=directory.name, timeline_source_table=name))
    _write_csv(out / "KEY_TIMELINE.csv", rows)
    return inputs, dict(selected_scenario_ids=sorted(selected), found_scenario_ids=sorted(found),
        missing_or_unexported_scenario_ids=sorted(set(selected)-found), exported_rows=len(rows),
        source="already exported producer CSV; no raw reconstruction")


def summarize(label, batch_glob="*", *, timeline_scenarios=None):
    """Summarize fixed RUN's registered saved results, retaining absent attempts."""
    if not label or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in label):
        raise ValueError("Use a simple fresh summary label")
    out = RUN / "review" / ("summary_" + label)
    if out.exists():
        raise FileExistsError("M20 summaries are immutable; use a fresh label")
    registry, registry_path = _registry()
    directories = sorted(p for p in (RUN / "experiments").glob(batch_glob) if p.is_dir())
    rows, attempts, inputs, issues = [], [], [], []
    policies_by_batch = {}
    registered_pairs = 0
    for directory in directories:
        try:
            own, ledger, sources, policies, problems = _collect(directory)
        except Exception as error:
            issues.append(dict(batch=directory.name, type=type(error).__name__, message=str(error),
                physical_outcome="unknown; batch could not be read, no zero outcome generated"))
            continue
        rows.extend(own)
        attempts.extend(ledger)
        inputs.extend(sources)
        registered_pairs += sum(r["registered"] for r in ledger)
        policies_by_batch[directory.name] = policies
        issues.extend(dict(batch=directory.name, kind=problem) for problem in problems)
    paired, aggregates = _pair_and_aggregate(rows, policies_by_batch)
    resources = _resource_rows(rows)
    emissions = _emission_rows(resources, registry)
    out.mkdir(parents=True)
    tables = dict(RESULT_WINDOWS=rows, PAIRED_WINDOWS=paired,
        COMMON600=[r for r in rows if r["window"] == "full" and r.get("window_start_minute") == 0.
                   and r.get("window_end_minute") == 600.],
        RESOURCES=resources, EMISSIONS=emissions, ATTEMPT_STATUS=attempts, AGGREGATES=aggregates)
    for name, table in tables.items():
        _write_csv(out / (name + ".csv"), table)
    timeline_inputs, selection = _timelines(directories,
        set(DEFAULT_TIMELINE_SCENARIOS if timeline_scenarios is None else timeline_scenarios), out)
    inputs.extend(timeline_inputs)
    actual = sum(r["result_file_present"] for r in attempts)
    summary = dict(schema=SCHEMA + ".summary", execution_model_id=MODEL_ID,
        registered_pairs=registered_pairs, actual_result_records=actual,
        missing_registered_results=sum(r["status"] == "missing_result" for r in attempts),
        unregistered_result_records=sum(not r["registered"] for r in attempts),
        status_counts=dict(Counter(r["status"] for r in attempts)),
        duplicate_comparison_rows=sum(r["comparison_duplicate_in_cohort"] for r in rows),
        attempts=attempts, aggregates=aggregates, batch_read_or_binding_issues=issues,
        timeline_selection=selection, emissions_registry_sha256=_sha(registry_path),
        factor_case_ids=list(registry["cases"]), unknown_records_are_not_zero_service=True,
        costs_not_pooled_across_windows_or_bindings=True, legacy80_seen_regression=True,
        fixed_eight_backgrounds_are_seen_rechecks=True, independent_confirmation_claim=False,
        original_final12_unopened=True, physical_episodes_started=0, additional_raw_opens=0,
        annual_inputs_loaded=0, old_outputs_modified=False,
        different_models_are_never_comparable=True)
    _write(out / "SUMMARY.json", summary)
    names = [*(name + ".csv" for name in tables), "KEY_TIMELINE.csv", "SUMMARY.json"]
    receipt = dict(schema=SCHEMA + ".summary_receipt", output_directory=str(out),
        table_row_counts={key: len(value) for key, value in tables.items()},
        output_sha256={name: _sha(out / name) for name in names}, input_files=inputs,
        emissions_registry_path=str(registry_path), emissions_registry_sha256=_sha(registry_path),
        analysis_program_sha256=_sha(Path(__file__)), physical_episodes_started=0,
        additional_raw_opens=0, annual_inputs_loaded=0)
    _write(out / "SUMMARY_RECEIPT.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("summarize")
    command.add_argument("--label", required=True)
    command.add_argument("--batch-glob", default="*")
    command.add_argument("--timeline-scenario", action="append")
    args = parser.parse_args(argv)
    receipt = summarize(args.label, args.batch_glob, timeline_scenarios=args.timeline_scenario)
    print(json.dumps({key: receipt[key] for key in ("output_directory", "table_row_counts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
