"""M19 saved analysis contracts; synthetic fixtures, no physical runs or raw reads."""
import copy
import csv
import hashlib
import importlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import pytest
import pandas as pd

from test_engineering_analysis import payload, segment

ROOT = Path(__file__).resolve().parents[1]
REAL_RUN = ROOT / "outputs/v19_model_closeout_20261018_run01"
MODEL = "M19_model_closeout"


def api():
    assert (ROOT / "tools/model_closeout/analysis.py").is_file(), "M19 saved analysis is not implemented"
    return importlib.import_module("tools.model_closeout.analysis")


@pytest.fixture
def work():
    path = REAL_RUN / "test_tmp" / ("analysis_" + uuid.uuid4().hex)
    path.mkdir(parents=True)
    return path


def sample(requested=600., observed=600., scenario="s1", policy="E"):
    s = segment(0., observed, gt=(0., 0., 0.))
    s["loads_kW"] = [[1., 1., 1.] for _ in range(4)]
    s["connected_loads_kW"] = copy.deepcopy(s["loads_kW"])
    s["auxiliary_electric_kW"] = 4 * .77 / 3.51685284
    s["action"].update(grid_import_kW=4 + s["auxiliary_electric_kW"],
        gt_fuel_MMBtu_h=0., boiler_steam_lbph=4 / (.30 * .95),
        hrsg_steam_lbph=0., absorption_cooling_kW=0.,
        electric_chiller_cooling_kW=[4., 0., 0.])
    s["grid_tariff_USD_kWh"] = .1
    s["hour_of_year"] = 1
    s["pre_state"] = {"diesel_energy_remaining_kWh": 100.}
    s["post_state"] = {"diesel_energy_remaining_kWh": 99.8}
    s["diesel_local_response"] = dict(mechanical_work_kWh=.2, electrical_work_kWh=0.,
        loss_work_kWh=.2, rotor_delta_kWh=0., fuel_budget_debit_kWh_equivalent=.2,
        original_electric_fee_USD=0., shaft_equivalent_fee_USD=.06,
        actual_seconds=observed*60., energy_residual_kWh=0.,
        state_before={"rotor_energy_kWs": 600.}, state_after={"rotor_energy_kWs": 600.})
    p = payload([s], requested)
    p["execution_model_id"] = MODEL
    p["scenario"].update(id=scenario, canonical_class="L1", legacy_class="L1",
        core_four_class_denominator=True)
    p["identity"].update(scenario_id=scenario, policy_id=policy)
    plant = SimpleNamespace(steam_kWh_per_lb=.30, exchanger_eff=.95, abs_steam_lb_per_rt=19.5,
        abs_electric_kW_per_rt=.14, ec_electric_kW_per_rt=.77,
        boiler_eff=.8, gas_price_usd_per_mmbtu=4.,
        tariff={"electricity_offpeak_per_kWh": .1, "electricity_midpeak_per_kWh": .1,
                "electricity_onpeak_per_kWh": .1})
    hourly = pd.DataFrame([dict(timestamp=pd.Timestamp("2017-01-01"), hour_of_year=1)])
    return p, SimpleNamespace(config={}, plant=plant, data=None, hourly=hourly)


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def csvrows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def campaign(a, run, *, terminal=True, omit=None, old_model=None):
    directory = run / "experiments/main_01"
    policies = ["E", "K1", "C19"]
    scenes = [dict(id=s, canonical_class="L1", analysis_class="L1", duration_steps=40,
                   step_minutes=15., initialization_seed=123) for s in ("s1", "s2")]
    manifest = dict(scenarios=scenes, execution_model_id=MODEL, model_config={}, source_sha256={"synthetic.py": "a"*64})
    manifest["signature"] = canonical(manifest)
    write(directory / "manifest.json", manifest)
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    plan = dict(model_id=MODEL, model_config={}, source_sha256=manifest["source_sha256"],
                policy_specs=[{"policy_id": p} for p in policies], scenario_ids=["s1", "s2"],
                registered_pairs=6, manifest_sha256=sha(directory / "manifest.json"),
                manifest_signature=manifest["signature"])
    write(directory / "plan.json", plan)
    for scene in scenes:
        for policy in policies:
            key = (scene["id"], policy)
            if key == omit:
                continue
            partial = key == ("s2", "K1")
            failed = key == ("s2", "C19")
            p, env = sample(observed=300. if partial else 600., scenario=scene["id"], policy=policy)
            analysis = None if failed else a.analyze_payload(p, env)
            record = dict(scenario_id=scene["id"], policy_id=policy,
                model_id="M18_execution_recovery" if key == old_model else MODEL,
                status="solver_error" if failed else "partial" if partial else "completed",
                source_day_index=10, manifest_signature=manifest["signature"],
                model_config_signature=canonical({}), raw_path="must_not_open_trajectory.json.gz",
                raw_sha256="d"*64, analysis=analysis,
                error={"type": "SyntheticFailure"} if failed else None, analysis_error=None)
            write(directory / "pairs" / (scene["id"] + "__" + policy) / "RESULT.json", record)
    if terminal:
        evidence = {s["id"]: dict(same_initial_state=True, same_exogenous_definition=True,
            initial_state_sha256=["a"*64]*3, applied_exogenous_definition_sha256=["b"*64]*3) for s in scenes}
        write(directory / "BATCH_RESULT.json", dict(source_unchanged=True,
            manifest_sha256=sha(directory / "manifest.json"), plan_sha256=sha(directory / "plan.json"),
            matching_evidence=evidence))
    refs = run / "references"
    refs.mkdir(parents=True)
    (refs / "EMISSIONS_FACTOR_REGISTRY.json").write_bytes(
        (REAL_RUN / "references/EMISSIONS_FACTOR_REGISTRY.json").read_bytes())
    return directory


def test_live_payload_analysis_delegates_original_scores_and_audit_without_mutation():
    a = api()
    p, env = sample()
    before = copy.deepcopy(p)
    inherited = importlib.import_module("tools.execution_recovery.analysis").analyze_payload(p, env)
    result = a.analyze_payload(p, env)
    assert result["windows"]["full"]["original_19"] == inherited["windows"]["full"]["original_19"]
    assert result["v18"]["windows"] == inherited["v18"]["windows"]
    assert result["v19"]["episode_audit"]["status"] == "passed"
    assert result["v19"]["episode_audit"]["issues"] == []
    assert result["physical_episodes_started"] == result["additional_raw_opens"] == 0
    assert p == before


def test_legacy_model_cannot_be_relabeled_as_m19():
    p, env = sample()
    p["execution_model_id"] = "M18_execution_recovery"
    with pytest.raises(ValueError, match="M19"):
        api().analyze_payload(p, env)


def test_missing_balance_parameters_are_unknown_not_audit_pass():
    p, _ = sample()
    result = api().analyze_payload(p, None)
    assert result["v19"]["episode_audit"]["status"] == "unknown_parameters"


def test_export_reuses_legacy_tables_and_adds_explicit_resources_emissions(work):
    a = api()
    p, env = sample()
    analysis = a.analyze_payload(p, env)
    out = work / "analysis"
    receipt = a.export_rows([dict(scenario_id="s1", policy_id="E", model_id=MODEL,
        status="completed", analysis=analysis)], out)
    resources = csvrows(out / "V19_RESOURCES.csv")
    registered = next(r for r in resources if r["window"] == "registered_full")
    assert float(registered["DG_fuel_budget_debit_shaft_equivalent_kWh"]) == .2
    assert (out / "V18_CARRIER_SERVICE.csv").is_file()
    assert len(csvrows(out / "V19_EMISSIONS.csv")) == 4 * len(resources)
    assert receipt["physical_episodes_started"] == receipt["additional_raw_opens"] == 0
    with pytest.raises(FileExistsError):
        a.export_rows([dict(analysis=analysis)], out)


def test_summary_preserves_failures_prefixes_and_exact_matched_intersection(work, monkeypatch):
    a = api()
    campaign(a, work)
    monkeypatch.setattr(a, "RUN", work)
    receipt = a.summarize("synthetic01", timeline_scenarios=["s1"])
    out = work / "review/summary_synthetic01"
    summary = json.loads((out / "SUMMARY.json").read_text(encoding="utf-8"))
    assert summary["actual_result_records"] == summary["registered_pairs"] == 6
    assert summary["status_counts"]["solver_error"] == 1
    common = csvrows(out / "COMMON600.csv")
    assert len(common) == 6
    assert sum(row["panel_complete_intersection"] == "True" for row in common) == 3
    partial = next(row for row in common if row["scenario_id"] == "s2" and row["policy_id"] == "K1")
    assert partial["complete"] == "False"
    assert partial["registered_full_known"] == "False"
    assert partial["observation_value_scope"] == "observed_prefix"
    failed = next(row for row in common if row["scenario_id"] == "s2" and row["policy_id"] == "C19")
    assert failed["observation_value_scope"] == "unavailable"
    assert json.loads(failed["unknown_tail_minutes"]) == [0., 600.]
    paired = csvrows(out / "PAIRED_WINDOWS.csv")
    assert any(row["pair_eligible"] == "True" and row["scenario_id"] == "s1" for row in paired)
    assert all(row["pair_eligible"] == "False" for row in paired if row["scenario_id"] == "s2")
    assert len(csvrows(out / "EMISSIONS.csv")) == 4 * len(csvrows(out / "RESOURCES.csv"))
    assert receipt["additional_raw_opens"] == 0
    with pytest.raises(FileExistsError):
        a.summarize("synthetic01")


def test_missing_registered_pair_is_a_retained_unknown_attempt(work, monkeypatch):
    a = api()
    campaign(a, work, omit=("s2", "C19"))
    monkeypatch.setattr(a, "RUN", work)
    a.summarize("missing01")
    out = work / "review/summary_missing01"
    attempts = csvrows(out / "ATTEMPT_STATUS.csv")
    assert len(attempts) == 6
    assert sum(row["status"] == "missing_result" for row in attempts) == 1


def test_absent_terminal_evidence_never_becomes_paired_success(work, monkeypatch):
    a = api()
    campaign(a, work, terminal=False)
    monkeypatch.setattr(a, "RUN", work)
    a.summarize("unterminated01")
    paired = csvrows(work / "review/summary_unterminated01/PAIRED_WINDOWS.csv")
    assert paired and all(row["pair_eligible"] == "False" for row in paired)


def test_foreign_model_record_retained_but_ineligible(work, monkeypatch):
    a = api()
    campaign(a, work, old_model=("s1", "E"))
    monkeypatch.setattr(a, "RUN", work)
    a.summarize("foreign01")
    common = csvrows(work / "review/summary_foreign01/COMMON600.csv")
    foreign = next(row for row in common if row["scenario_id"] == "s1" and row["policy_id"] == "E")
    assert foreign["model_id"] == "M18_execution_recovery"
    assert foreign["comparison_matching_eligible"] == "False"
    assert "model" in foreign["comparison_matching_reason"]


def test_duplicate_same_cohort_results_are_retained_but_not_counted_twice(work, monkeypatch):
    a = api()
    directory = campaign(a, work)
    shutil.copytree(directory, work / "experiments/main_02")
    monkeypatch.setattr(a, "RUN", work)
    a.summarize("duplicate01")
    summary = json.loads((work / "review/summary_duplicate01/SUMMARY.json").read_text(encoding="utf-8"))
    assert summary["actual_result_records"] == 12
    assert summary["duplicate_comparison_rows"] > 0
    assert all(row["matched_complete"] == 0 for row in summary["aggregates"])


def test_policy_panel_identity_is_part_of_aggregate_binding(work, monkeypatch):
    a = api()
    campaign(a, work)
    monkeypatch.setattr(a, "RUN", work)
    a.summarize("panel01")
    aggregates = csvrows(work / "review/summary_panel01/AGGREGATES.csv")
    assert aggregates and all(len(row["policy_panel_sha256"]) == 64 for row in aggregates)
