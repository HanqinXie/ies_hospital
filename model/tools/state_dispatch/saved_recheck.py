"""Solver-free v9 saved-evidence recheck using recorded segment classifications.

Requires only numpy/pandas beyond the standard library. No hospital simulator,
optimizer, Gurobi import, or dependency stub is used. Saved classification and
illustrative tariffs are reproduced, not physically or clinically calibrated.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from tools.four_scenarios.package import _archive_name, _read, _sha, _within, verify_integrity

TARGET_NAMES = ("P0_failure", "P0_failure_minutes") + tuple(
    f"{p}_{c}_ENS_kWh" for p in ("P0", "P1", "P2", "P3") for c in ("e", "h", "c")) + (
    "P0_max_temperature_C", "P1_max_temperature_C", "P2_max_temperature_C",
    "secondary_GT_trips", "actual_energy_fee_USD")
CLASSIFICATION_SOURCE = "saved_segment_classification_not_independently_calibrated"


def _same(left, right):
    if left is None or right is None:
        return left is None and right is None
    return (math.isfinite(float(left)) and math.isfinite(float(right)) and
            math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-8))


def _number(value):
    if value is None or str(value).strip().lower() in ("", "none", "nan", "null"):
        return None
    return float(value)


class SavedIntegrator:
    """Original expense formula from recorded actions, with no model imports."""
    def __init__(self, root):
        cfg = _read(root / "configs/plant_configs.json")
        self.price = cfg["historical_2001_prices_USD"]
        self.steam = float(cfg["engineering_assumptions_not_from_source"]["kWh_thermal_per_lb_steam"])
        self.boiler_eff = float(cfg["historical_pnnl_performance"]["boiler_thermal_efficiency"])
        self.timestamps = pd.to_datetime(pd.read_csv(root / "inputs/loads/hospital_ies_demand_8760.csv",
                                                     usecols=["timestamp"])["timestamp"])

    def cost_rate(self, segment):
        raw_hour = segment["hour_of_year"]
        hour = int(raw_hour)
        if hour != raw_hour or not 1 <= hour <= len(self.timestamps):
            raise ValueError("Saved segment hour lies outside actual annual source")
        stamp = self.timestamps.iloc[hour - 1]
        tariff = self.price["electricity_offpeak_per_kWh"]
        if stamp.weekday() < 5:
            if 12 <= stamp.hour < 18:
                tariff = self.price["electricity_onpeak_per_kWh"]
            elif 8 <= stamp.hour < 12 or 18 <= stamp.hour < 22:
                tariff = self.price["electricity_midpeak_per_kWh"]
        action = segment["action"]
        fuel, boiler, grid, diesel = (float(action[key]) for key in
            ("gt_fuel_MMBtu_h", "boiler_steam_lbph", "grid_import_kW", "diesel_kW"))
        if not all(math.isfinite(value) for value in (fuel, boiler, grid, diesel)):
            raise ValueError("Nonfinite saved source output")
        boiler_fuel = boiler * self.steam / self.boiler_eff / 293.0710702
        return ((fuel + boiler_fuel) * self.price["gas_per_MMBtu"] + grid * tariff + diesel * .30)

    def integrate(self, payload, start, end):
        cursor = covered = failure_minutes = fee = 0.
        ens, temperatures = np.zeros((4, 3)), np.full(3, -np.inf)
        for segment in payload["segments"]:
            left, right = float(segment["start_minute"]), float(segment["end_minute"])
            if abs(left - cursor) > 1e-8 or right <= left:
                raise ValueError("Saved raw intervals are not contiguous")
            cursor = right
            minutes = max(0., min(right, end) - max(left, start))
            if not minutes:
                continue
            losses = np.asarray(segment["unserved_kW"], dtype=float)
            temp = np.asarray([segment["temperature"][f"P{i}"] for i in range(3)], dtype=float)
            if losses.shape != (4, 3) or not np.all(np.isfinite(losses)) or not np.all(np.isfinite(temp)):
                raise ValueError("Invalid saved ENS/temperature values")
            covered += minutes
            failure_minutes += minutes * bool(segment["classification"]["p0_guarantee_failure"])
            ens += losses * minutes / 60.
            temperatures = np.maximum(temperatures, temp)
            fee += self.cost_rate(segment) * minutes / 60.
        if abs(cursor - float(payload["summary"]["observed_until_minute"])) > 1e-8:
            raise ValueError("Saved observation endpoint differs from actual segments")
        full = abs(covered - (end - start)) < 1e-8
        trips = sum(start <= float(event["time_minute"]) < end and
                    str(event.get("device", "")).startswith("GT_") and
                    event.get("cause_class") in ("residual_stress_protection", "no_export_isolation", "minimum_output_interlock")
                    for event in payload.get("events", []))
        targets = [1. if failure_minutes else 0. if full else None]
        targets += ([failure_minutes, *ens.ravel().tolist(), *temperatures.tolist(), float(trips), fee]
                    if full else [None] * 18)
        return dict(targets=targets, target_names=list(TARGET_NAMES), full_horizon=full,
                    target_mask=[value is not None for value in targets])


def _compare_score(stored, actual, context, errors):
    if (not isinstance(stored, dict) or stored.get("target_names") != list(TARGET_NAMES) or
            len(stored.get("targets", [])) != 19 or
            stored.get("target_mask") != [v is not None for v in stored["targets"]] or
            stored.get("full_horizon") != actual["full_horizon"]):
        errors.append(dict(context=context, error="19-target schema/mask/window completeness differs"))
        return 0, None
    differences = []
    for name, left, right in zip(TARGET_NAMES, actual["targets"], stored["targets"]):
        if not _same(left, right):
            errors.append(dict(context=context, target=name, recomputed=left, stored=right))
        if left is not None and right is not None:
            differences.append(abs(float(left) - float(right)))
    return 19, max(differences, default=0.)


def recheck_bundle(extracted_root):
    root = Path(extracted_root).resolve()
    package = verify_integrity(root)
    if package.get("kind") != "v9_state_dispatch_selected_raw":
        raise ValueError("Not a v9 multi-batch selected-raw package")
    integrator = SavedIntegrator(root)
    errors, batches, batch_checks = [], {}, []
    table_cells = 0
    for batch in package["batches"]:
        paths = {key: _within(root / _archive_name(item["path"]), root)
                 for key, item in batch["evidence"].items()}
        for key, path in paths.items():
            if _sha(path) != batch["evidence"][key]["sha256"]:
                raise ValueError("Bundled batch evidence SHA differs")
        manifest, plan, audit = (_read(paths[key]) for key in ("manifest", "plan", "audit"))
        if (audit.get("manifest_sha256") != _sha(paths["manifest"]) or
                audit.get("plan_sha256") != _sha(paths["plan"])):
            raise ValueError("Saved audit source binding differs")
        ledger = {(r["scenario_id"], r["policy_id"]): r for r in audit["pair_ledger"]}
        if len(ledger) != len(audit["pair_ledger"]) or len(ledger) != plan["registered_pair_count"]:
            raise ValueError("Saved audit pair denominator differs")
        if plan.get("scenario_ids") and plan.get("policy_specs"):
            expected = {(sid, spec["policy_id"]) for sid in plan["scenario_ids"] for spec in plan["policy_specs"]}
            if set(ledger) != expected:
                raise ValueError("Saved plan/audit Cartesian denominator differs")
        table = {}
        analysis_file = batch.get("analysis_file")
        cells, local_errors = 0, len(errors)
        if analysis_file:
            path = _within(root / _archive_name(analysis_file), root)
            if _sha(path) != batch["analysis_sha256"]:
                raise ValueError("Saved analysis file SHA differs")
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            table = {(r["scenario_id"], r["policy_id"]): r for r in rows}
            if len(table) != len(rows) or set(table) != set(ledger):
                raise ValueError("Saved table omits registered pair denominator")
            for key, row in table.items():
                for prefix, score_key in (("primary", "recomputed_primary_score"), ("full", "recomputed_full_observation")):
                    score = ledger[key].get(score_key)
                    values = score["targets"] if score else [None] * 19
                    for target, expected in zip(TARGET_NAMES, values):
                        column = prefix + "_" + target
                        if column not in row:
                            errors.append(dict(batch=batch["name"], pair=list(key), missing_table_column=column))
                            continue
                        observed = _number(row[column])
                        if not _same(observed, expected):
                            errors.append(dict(batch=batch["name"], pair=list(key), column=column,
                                               table=observed, audit=expected))
                        cells += 1
        table_cells += cells
        batches[batch["name"]] = dict(manifest=manifest, plan=plan, audit=audit, ledger=ledger, table=table)
        batch_checks.append(dict(batch=batch["name"], registered_pair_count=len(ledger),
            audit_reports_passed=audit.get("passed") is True, table_provided=analysis_file is not None,
            table_score_cells_checked=cells, mismatch_count=len(errors) - local_errors))
    raw_checks, raw_cells = [], 0
    for item in package["examples"]:
        batch = batches[item["batch"]]
        key = item["scenario_id"], item["policy_id"]
        pair = batch["ledger"][key]
        raw = _within(root / _archive_name(item["raw_path"]), root)
        result_path = _within(root / _archive_name(item["result_path"]), root)
        if (_sha(raw) != item["raw_sha256"] or _sha(result_path) != item["result_sha256"] or
                pair["latest_result_sha256"] != item["result_sha256"]):
            raise ValueError("Selected raw/result/audit SHA differs")
        result = _read(result_path)
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            payload = json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        scene = next(s for s in batch["manifest"]["scenarios"] if s["id"] == key[0])
        if (payload.get("scenario") != scene or scene.get("is_final_blind") is not False or
                payload.get("identity", {}).get("scenario_id") != key[0] or
                payload.get("identity", {}).get("policy_id") != key[1] or
                result.get("scenario_id") != key[0] or result.get("policy_id") != key[1] or
                result.get("trajectory_sha256") != item["raw_sha256"] or
                result.get("plan_signature") != batch["plan"]["signature"]):
            raise ValueError("Selected raw/result identity differs")
        windows = []
        for score_key, audit_key, start, end in (("primary_score", "recomputed_primary_score", 60., 420.),
                                                ("full_observation", "recomputed_full_observation", 0., 600.)):
            actual = integrator.integrate(payload, start, end)
            context = dict(batch=item["batch"], scenario_id=key[0], policy_id=key[1], window=score_key)
            count, maximum = _compare_score(result[score_key], actual, context, errors)
            raw_cells += count
            _compare_score(pair.get(audit_key), actual, {**context, "against": "audit"}, errors)
            windows.append(dict(name=score_key, targets=count, complete=actual["full_horizon"],
                                max_abs_difference=maximum))
        true_trips = sum(event.get("kind") == "protection_trip" and
                         str(event.get("device", "")).startswith("GT_") and
                         0 <= float(event["time_minute"]) < 600 for event in payload.get("events", []))
        table_row = batch["table"].get(key)
        if table_row:
            for column in ("gt_protection_trip_count", "GT_protection_trip_count_observed"):
                if column in table_row and not _same(_number(table_row[column]), true_trips):
                    errors.append(dict(batch=item["batch"], pair=list(key), column=column,
                                       table=_number(table_row[column]), saved_raw=true_trips))
        raw_checks.append(dict(batch=item["batch"], scenario_id=key[0], policy_id=key[1],
                               raw_path=item["raw_path"], windows=windows, actual_gt_protection_trip_count=true_trips))
    registered = sum(len(b["ledger"]) for b in batches.values())
    if registered != package["registered_pair_count"]:
        raise ValueError("Saved multi-batch registered denominator differs")
    return dict(passed=not errors, errors=errors, package_entries_checked=len(package["entries"]),
        registered_pair_count=registered, selected_raw_count=len(raw_checks),
        raw_windows_checked=sum(len(r["windows"]) for r in raw_checks), raw_target_cells_checked=raw_cells,
        table_score_cells_checked=table_cells, saved_audit_vs_result_tables=batch_checks,
        raw_metric_recalculation=raw_checks, classification_source=CLASSIFICATION_SOURCE,
        full_physical_simulator_rerun=False, original_optimizer_scorer_executed=False,
        solver_imports_required=False, implementation_sha256=_sha(Path(__file__)),
        executed_from_package_source=Path(__file__).resolve().is_relative_to(root),
        claim_scope="SHA and saved classifications/actions/tariffs only; no physical or clinical recalibration")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root, output = args.root.resolve(), args.output.resolve()
    if output.is_relative_to(root):
        raise ValueError("Write recheck output outside the immutable extracted package")
    output.mkdir(parents=True, exist_ok=True)
    try:
        report = recheck_bundle(root)
    except Exception as error:
        report = dict(passed=False, errors=[dict(error_type=type(error).__name__, message=str(error))],
                      full_physical_simulator_rerun=False, classification_source=CLASSIFICATION_SOURCE)
    path = output / "LOCAL_RECHECK.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "raw_metric_recalculation"},
                     ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
