"""Saved-only subprocess must succeed with simulator and Gurobi imports blocked."""
from __future__ import annotations

import copy
import csv
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from tools.control_improvement.branches import window_targets
from tools.four_scenarios.package import create_archive


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def fixture_package(tmp_path, *, score_change=False, table_change=False):
    cfg = json.loads((ROOT / "configs/plant_configs.json").read_text(encoding="utf-8"))
    price = cfg["historical_2001_prices_USD"]
    plant = SimpleNamespace(tariff=price, gas_price_usd_per_mmbtu=price["gas_per_MMBtu"],
        steam_kWh_per_lb=cfg["engineering_assumptions_not_from_source"]["kWh_thermal_per_lb_steam"],
        boiler_eff=cfg["historical_pnnl_performance"]["boiler_thermal_efficiency"])
    env = SimpleNamespace(plant=plant, hourly=pd.DataFrame({"timestamp": [datetime(2026, 1, 1, 13)]}))
    scene = dict(id="scene", analysis_class="L1", root_family_id="seen", is_final_blind=False)
    segments = []
    for left, right, failed in ((0., 90., False), (90., 600., True)):
        segments.append(dict(start_minute=left, end_minute=right, hour_of_year=1, cost_USD_step=0.,
            action=dict(gt_fuel_MMBtu_h=1., boiler_steam_lbph=100., grid_import_kW=100., diesel_kW=10.),
            unserved_kW=[[2. if failed else 0., 0., 0.], [0., 0., 0.], [0., 0., 0.], [0., 0., 4.]],
            temperature=dict(P0=21.5, P1=24.1, P2=24.2), classification=dict(p0_guarantee_failure=failed)))
    payload = dict(scenario=scene, identity=dict(scenario_id="scene", policy_id="secure_milp"),
        summary=dict(status="completed", observed_until_minute=600., requested_end_minute=600.),
        segments=segments, events=[dict(kind="protection_trip", device="GT_1", time_minute=135.,
                                      cause_class="residual_stress_protection"),
                                  dict(kind="no_export_interlock", device="GT_2", time_minute=225.,
                                       cause_class="no_export_isolation")])
    raw = tmp_path / "trajectory.json.gz"
    with gzip.open(raw, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    primary = window_targets(payload, 60., 360., env)
    full = window_targets(payload, 0., 600., env)
    result = dict(scenario_id="scene", policy_id="secure_milp", status="completed", terminal=True,
        plan_signature="plan", trajectory_sha256=sha(raw), primary_score=primary,
        full_observation=copy.deepcopy(full))
    if score_change:
        result["full_observation"]["targets"][2] += 1.
    result_bytes = encoded(result)
    result_sha = hashlib.sha256(result_bytes).hexdigest()
    ledger = [dict(scenario_id="scene", policy_id="secure_milp", latest_result_sha256=result_sha,
        attempts=[dict(attempt="attempt_0001", raw_sha256=sha(raw), result_sha256=result_sha)],
        recomputed_primary_score=primary, recomputed_full_observation=result["full_observation"])]
    manifest_bytes = encoded(dict(scenarios=[scene]))
    plan_bytes = encoded(dict(signature="plan", registered_pair_count=1))
    audit_bytes = encoded(dict(passed=True, registered_pair_count=1, pair_ledger=ledger,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(), plan_sha256=hashlib.sha256(plan_bytes).hexdigest()))
    row = dict(scenario_id="scene", policy_id="secure_milp", gt_protection_trip_count=1)
    for prefix, score in (("primary", primary), ("full", result["full_observation"])):
        row.update({prefix + "_" + name: value for name, value in zip(score["target_names"], score["targets"])})
    if table_change:
        row["full_P0_e_ENS_kWh"] = 999.
    table = tmp_path / "TRAJECTORY_RESULTS.csv"
    with table.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
    entries = {
        "configs/plant_configs.json": encoded(cfg),
        "inputs/loads/hospital_ies_demand_8760.csv": b"timestamp\n2026-01-01 13:00:00\n",
        "batches/development/evidence/global_manifest.json": manifest_bytes,
        "batches/development/evidence/registered_plan.json": plan_bytes,
        "batches/development/evidence/AUDIT.json": audit_bytes,
        "batches/development/analysis/TRAJECTORY_RESULTS.csv": table,
        "examples/raw/trajectory.json.gz": raw,
        "batches/development/attempts/result.json": result_bytes,
    }
    evidence = {key: dict(path=f"batches/development/evidence/{name}",
                         sha256=hashlib.sha256(value).hexdigest())
                for key, name, value in (("manifest", "global_manifest.json", manifest_bytes),
                    ("plan", "registered_plan.json", plan_bytes), ("audit", "AUDIT.json", audit_bytes))}
    archive = tmp_path / "saved_fixture.zip"
    create_archive(archive, entries, dict(kind="v9_state_dispatch_selected_raw", registered_pair_count=1,
        batches=[dict(name="development", evidence=evidence,
                      analysis_file="batches/development/analysis/TRAJECTORY_RESULTS.csv", analysis_sha256=sha(table))],
        examples=[dict(batch="development", scenario_id="scene", policy_id="secure_milp",
                       raw_path="examples/raw/trajectory.json.gz", raw_sha256=sha(raw),
                       result_path="batches/development/attempts/result.json", result_sha256=result_sha)]))
    root = tmp_path / "extracted"
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(root)
    return root


def run_blocked(root, output):
    code = """import importlib.abc, runpy, sys
class NoSolver(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname == 'gurobipy' or fullname.startswith(('gurobipy.', 'hospital_ies', 'tools.control_improvement')):
   raise AssertionError('Forbidden solver/simulator import: ' + fullname)
sys.meta_path.insert(0, NoSolver())
sys.argv=['saved_recheck','--root',sys.argv[1],'--output',sys.argv[2]]
runpy.run_module('tools.state_dispatch.saved_recheck',run_name='__main__')
"""
    return subprocess.run([sys.executable, "-c", code, str(root), str(output)], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8")


def test_saved_only_19_target_recheck_succeeds_with_all_solver_imports_blocked(tmp_path):
    root = fixture_package(tmp_path)
    output = tmp_path / "recheck"
    result = run_blocked(root, output)
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads((output / "LOCAL_RECHECK.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["selected_raw_count"] == 1 and report["raw_windows_checked"] == 2
    assert report["raw_target_cells_checked"] == 38
    assert report["table_score_cells_checked"] == 38
    assert report["full_physical_simulator_rerun"] is False
    assert report["classification_source"] == "saved_segment_classification_not_independently_calibrated"


@pytest.mark.parametrize("score_change,table_change", [(True, False), (False, True)])
def test_saved_only_recheck_detects_raw_score_and_table_mismatch(tmp_path, score_change, table_change):
    root = fixture_package(tmp_path, score_change=score_change, table_change=table_change)
    output = tmp_path / "recheck"
    result = run_blocked(root, output)
    assert result.returncode == 1
    report = json.loads((output / "LOCAL_RECHECK.json").read_text(encoding="utf-8"))
    assert report["passed"] is False and report["errors"]
