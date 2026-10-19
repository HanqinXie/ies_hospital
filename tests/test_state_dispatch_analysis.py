"""Small audited synthetic raws verify v9 bookkeeping, never physical benefit."""
from __future__ import annotations

import copy
import gzip
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.dataset_manifest import digest
from tools.control_improvement.ranking import TARGET_NAMES
from tools.four_scenarios import analysis as old


def _module():
    return importlib.import_module("tools.state_dispatch.analysis")


def test_state_dispatch_analysis_entrypoint_exists():
    try:
        module = _module()
    except ModuleNotFoundError:
        module = None
    assert module is not None, "State-dispatch saved-raw analysis is not implemented"
    assert callable(module.analyze_batch)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _action(gt=100.):
    return {"gt_electric_kW": [gt, 100., 0.], "grid_import_kW": 400., "diesel_kW": 0.,
            "absorption_cooling_kW": 120., "electric_chiller_cooling_kW": [25., 0., 0.],
            "boiler_steam_lbph": 50., "hrsg_steam_lbph": 200.,
            "tower_rejection_kW": 100., "gt_fuel_MMBtu_h": 6.}


def _segment(start, end, failed=False):
    return {"start_minute": start, "end_minute": end,
            "unserved_kW": [[6. if failed else 0., 0., 0.]] + [[0.] * 3 for _ in range(3)],
            "classification": {"p0_guarantee_failure": failed,
                "p0_temperature_violation": False, "p0_energy_loss": failed,
                "material_unserved_by_carrier": {"e": failed, "h": False, "c": False}},
            "action": _action(), "cost_USD_step": 5.,
            "temperature": {"P0": 24., "P1": 25., "P2": 26.},
            "violation": {"P0": False, "P1": False, "P2": False},
            "availability": {"GT_1": 1., "GT_2": 1., "GT_3": 0.},
            "post_state": {"gt_prev": [100., 100., 0.], "overload_minutes": [0., 0., 0.],
                "extra_faulted": ["GT_3"], "diesel_status": "OFF", "diesel_energy_remaining_kWh": 2400.}}


def _decision(pid, now):
    requested = {"security": {"gt_loading_penalty_USD_per_kWh": .35}}
    diagnostic = {"strategy_status": "original_baseline", "strategy_fallback": False}
    if pid.startswith("state_"):
        requested["targets"] = dict(zip(("gt_1", "gt_2", "gt_3", "grid_import", "diesel",
                                         "absorption_cooling_kW"), [200., 100., 0., 400., 0., 120.]))
        diagnostic.update(mode="emergency", reasons=["observed_demand_rise"],
                          same_state_base_plan_targets_kW=[100., 100., 0., 400., 0., 120.],
                          requested_six_targets_kW=[200., 100., 0., 400., 0., 120.],
                          analysis_class="hidden_case_must_not_be_copied",
                          future_events=[{"time_minute": 330}])
    return {"decision_time_min": now, "features": {"P0_h_demand_kW": 100.},
            "feature_metadata": {"P0_h_demand_kW": {"available_time_min": now, "valid_time_min": now}},
            "labels": {"60": {"value": 1}}, "future_script": {"shock_end_minute": 330.},
            "requested_command": requested,
            "command_generated": {"id": int(now + 1), "generated_minute": now,
                                  "arrival_minute": 30., "dispatch": _action()},
            "actual_execution": _action(80. if pid.startswith("state_") else 100.),
            "policy_diagnostic": diagnostic, "arrived_commands": []}


@pytest.fixture
def batch(tmp_path):
    policies = ("secure_milp", "priority_milp", "state_a", "state_b")
    scenes = []
    for sid, cohort, value in (("s1", "boundary", .4), ("s2", "boundary", .5),
                               ("s3", "development", .4)):
        scenes.append({"id": sid, "root_family_id": "root1", "base_state_id": "base",
            "analysis_class": "L2", "analysis_cohort": cohort,
            "design_axis": "heat_amplitude", "design_axis_value": value,
            "design_level": str(value), "design_cell_id": sid,
            "start_hour": 120, "step_minutes": 15., "duration_steps": 40,
            "experiment_role": "development", "is_final_blind": False,
            "response_delay_minutes": 22.5, "enable_propagation": False,
            "shock": {"e": 0., "h": value, "c": 0.}, "shock_by_priority": {},
            "exogenous_stream_id": "stream_" + sid,
            "exogenous_events": [{"kind": "load_shock", "start_minute": 90.,
                                   "end_minute": 330., "shock": {"h": value}}]})
    manifest = {"scenarios": scenes, "signature": "fixture_manifest",
                "probability_semantics": "Design quotas are not failure probabilities"}
    mp, pp, ap = (tmp_path / name for name in ("manifest.json", "plan.json", "audit.json"))
    _write(mp, manifest)
    plan = {"manifest_sha256": old._sha(mp), "manifest_signature": manifest["signature"],
            "scenario_ids": [s["id"] for s in scenes], "policy_specs": [
                {"policy_id": p, "kind": "state_dispatch" if p.startswith("state_") else
                 "secure" if p == "secure_milp" else "priority"} for p in policies],
            "registered_pair_count": len(scenes) * len(policies), "phase": "development"}
    _write(pp, plan)
    results, ledger = tmp_path / "results", []
    for scene in scenes:
        for pid in policies:
            truncated = scene["id"] == "s1" and pid == "state_b"
            failed = truncated or (scene["id"] == "s2" and pid == "state_a")
            observed = 300. if truncated else 600.
            segments = ([_segment(0, 290), _segment(290, 300, True)] if truncated else
                        [_segment(0, 90), _segment(90, 105, failed), _segment(105, 600)])
            status = "solver_error" if truncated else "completed"
            score = [0.] * len(TARGET_NAMES)
            score[0] = int(failed)
            score[1] = 15. if failed else 0.
            score[14:17] = [24., 25., 26.]
            score[-1] = {"secure_milp": 100., "priority_milp": 120., "state_a": 110., "state_b": 130.}[pid]
            if truncated:
                score = [1] + [None] * (len(TARGET_NAMES) - 1)
            score = {"full_horizon": not truncated, "targets": score}
            payload = {"scenario": scene, "identity": {"scenario_id": scene["id"],
                "policy_id": pid, "exogenous_stream_id": scene["exogenous_stream_id"]},
                "initial_state": {"same": True}, "segments": segments,
                "summary": {"status": status, "observed_until_minute": observed,
                            "requested_end_minute": 600.},
                "decisions": [_decision(pid, 0.), _decision(pid, 15.)],
                "events": [{"kind": "protection_trip", "device": "GT_1", "time_minute": 105.},
                           {"kind": "no_export_interlock", "device": "GT_3", "time_minute": 120.}],
                "error": {"classification": "solver_error"} if truncated else None}
            attempt = results / "pairs" / digest([scene["id"], pid]) / "attempt_0001"
            attempt.mkdir(parents=True)
            raw = attempt / "trajectory.json.gz"
            with gzip.open(raw, "wt", encoding="utf-8") as stream:
                json.dump(payload, stream)
            result = {"scenario_id": scene["id"], "policy_id": pid, "terminal": True,
                "status": status, "trajectory_path": raw.name, "trajectory_sha256": old._sha(raw),
                "primary_score": score, "full_observation": score, "binding": {"passed": True}}
            rp = attempt / "result.json"
            _write(rp, result)
            ledger.append({"scenario_id": scene["id"], "policy_id": pid, "terminal": True,
                           "status": status, "latest_result_sha256": old._sha(rp)})
    _write(ap, {"passed": True, "matched_initial_state_and_stream": True,
        "plan_sha256": old._sha(pp), "manifest_sha256": old._sha(mp),
        "registered_pair_count": 12, "terminal_pair_count": 12, "recomputed_pair_count": 12,
        "pair_ledger": ledger})
    return mp, pp, results, ap


def test_all_policies_compare_absolute_values_against_both_baselines(batch, tmp_path):
    report = _module().analyze_batch(*batch, tmp_path / "analysis")
    rows = report["trajectory_results"]
    assert len(rows) == 12
    assert {r["analysis_cohort"] for r in rows} == {"boundary", "development"}
    assert all("fixed_context_json" in r and "axis_value" in r for r in rows)
    deltas = [r for r in report["policy_deltas"] if r["scenario_id"] == "s2" and
              r["candidate_policy_id"] == "state_a"]
    assert {r["reference_policy_id"] for r in deltas} == {"secure_milp", "priority_milp"}
    fee = "full_actual_energy_fee_USD"
    assert {r["reference_policy_id"]: r[fee + "_delta"] for r in deltas} == {
        "secure_milp": 10., "priority_milp": -10.}
    assert all(r[fee + "_candidate"] == 110. for r in deltas)
    assert all(r["full_P0_failure_known_delta"] == 1 for r in deltas)


def test_truncated_window_retains_known_failure_but_no_invented_zero_totals(batch, tmp_path):
    report = _module().analyze_batch(*batch, tmp_path / "analysis")
    unknown = next(r for r in report["trajectory_results"] if r["scenario_id"] == "s1"
                   and r["policy_id"] == "state_b")
    assert unknown["full_P0_failure"] == 1
    assert unknown["full_P0_failure_minutes"] is None
    assert unknown["full_observed_prefix_P0_failure_minutes"] == 10.
    group = next(r for r in report["cohort_class_policy_summary"] if
                 r["analysis_cohort"] == "boundary" and r["policy_id"] == "state_b")
    assert group["registered_scenario_count"] == 2
    assert group["unknown_full_count"] == 1
    assert group["full_actual_energy_fee_USD_all_registered"] is None
    assert len(report["unknown_and_failures"]) >= 2


def test_boundaries_keep_only_discrete_coordinates_and_fixed_cohort_context(batch, tmp_path):
    report = _module().analyze_batch(*batch, tmp_path / "analysis")
    interval = next(r for r in report["boundary_intervals"] if
                    r["policy_id"] == "state_a" and r["analysis_cohort"] == "boundary")
    assert interval["tested_values_json"] == [.4, .5]
    assert interval["adjacent_status_changes_json"] == [
        {"left": .4, "left_status": "tested_no_failure", "right": .5,
         "right_status": "observed_failure"}]
    assert "safe" not in interval
    assert len([r for r in report["boundary_intervals"] if r["policy_id"] == "state_a"]) == 2


def test_decision_rows_distinguish_request_plan_execution_and_filter_future(batch, tmp_path):
    report = _module().analyze_batch(*batch, tmp_path / "analysis")
    row = next(r for r in report["decision_effects"] if r["policy_id"] == "state_a")
    assert row["requested_targets_kW_json"][0] == 200.
    assert row["projected_targets_kW_json"][0] == 100.
    assert row["actual_targets_kW_json"][0] == 80.
    assert row["request_changed_from_same_state_plan"] is True
    assert row["projected_plan_changed_from_same_state_plan"] is False
    assert row["mode"] == "emergency" and row["strategy_fallback"] is False
    assert row["parallel_execution_difference_is_not_same_state_causal_effect"] is True
    assert "future_events" not in json.dumps(row)
    assert "hidden_case_must_not_be_copied" not in json.dumps(row)
    result = report["trajectory_results"][0]
    assert result["gt_protection_trip_count"] == 1
    assert result["no_export_interlock_count"] == 1
    for filename in ("TRAJECTORY_RESULTS.csv", "POLICY_DELTAS.csv", "COHORT_CLASS_POLICY_SUMMARY.csv",
                     "ROOT_RESULTS.csv", "BOUNDARY_POINTS.csv", "BOUNDARY_INTERVALS.csv",
                     "UNKNOWN_AND_FAILURES.csv", "DECISION_EFFECTS.csv", "SUMMARY.json"):
        assert (tmp_path / "analysis" / filename).is_file()


def test_submitted_request_change_is_separate_from_projection_and_parallel_execution(batch, tmp_path):
    report = _module().analyze_batch(*batch, tmp_path / "analysis")
    row = next(r for r in report["decision_effects"] if r["policy_id"] == "state_a")
    assert row["submitted_request_differs_from_parallel_secure_milp"] is True
    assert row["projected_plan_differs_from_parallel_secure_milp"] is False
    assert row["actual_execution_differs_from_parallel_secure_milp"] is True
    assert row["submitted_request_json"]["targets"]["gt_1"] == 200.


def test_missing_priority_reference_has_unknown_difference_not_zero(batch, tmp_path):
    mp, pp, results, ap = batch
    plan = json.loads(pp.read_text())
    plan["policy_specs"] = [s for s in plan["policy_specs"] if s["policy_id"] != "priority_milp"]
    plan["registered_pair_count"] = 9
    _write(pp, plan)
    audit = json.loads(ap.read_text())
    audit.update(plan_sha256=old._sha(pp), registered_pair_count=9,
                 terminal_pair_count=9, recomputed_pair_count=9)
    audit["pair_ledger"] = [r for r in audit["pair_ledger"] if r["policy_id"] != "priority_milp"]
    _write(ap, audit)
    report = _module().analyze_batch(mp, pp, results, ap, tmp_path / "analysis")
    record = next(r for r in report["policy_deltas"] if r["candidate_policy_id"] == "state_a"
                  and r["reference_policy_id"] == "priority_milp")
    assert record["reference_available"] is False
    assert record["full_actual_energy_fee_USD_candidate"] == 110.
    assert record["full_actual_energy_fee_USD_reference"] is None
    assert record["full_actual_energy_fee_USD_delta"] is None


def test_decision_future_clock_rejected_even_when_raw_hashes_are_rebound(batch, tmp_path):
    mp, pp, results, ap = batch
    audit = json.loads(ap.read_text())
    record = audit["pair_ledger"][0]
    attempt = results / "pairs" / digest([record["scenario_id"], record["policy_id"]]) / "attempt_0001"
    raw = attempt / "trajectory.json.gz"
    with gzip.open(raw, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    payload["decisions"][0]["feature_metadata"]["P0_h_demand_kW"]["valid_time_min"] = 330.
    with gzip.open(raw, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    rp = attempt / "result.json"
    result = json.loads(rp.read_text())
    result["trajectory_sha256"] = old._sha(raw)
    _write(rp, result)
    record["latest_result_sha256"] = old._sha(rp)
    _write(ap, audit)
    destination = tmp_path / "rejected"
    with pytest.raises(ValueError, match="future-valid"):
        _module().analyze_batch(mp, pp, results, ap, destination)
    assert not destination.exists()


def test_fixed_context_keeps_other_delay_and_distinguishes_clock_phase(batch):
    module = _module()
    scene = json.loads(batch[0].read_text())["scenarios"][0]
    alternate = copy.deepcopy(scene)
    alternate["response_delay_minutes"] = 30.
    assert module._fixed_context(scene) != module._fixed_context(alternate)
    phase = copy.deepcopy(scene)
    phase.update(design_axis="event_phase", design_axis_value=7.5)
    phase["exogenous_events"][0].update(start_minute=97.5, end_minute=337.5)
    phase0 = copy.deepcopy(scene)
    phase0.update(design_axis="event_phase", design_axis_value=0.)
    assert module._fixed_context(phase) == module._fixed_context(phase0)


def test_categorical_fault_coordinates_are_points_without_ordered_interval_claim():
    rows = []
    for sid, value, failed in (("a", "GRID=0.0", 0), ("b", "GT_1=0.5", 1)):
        rows.append({"scenario_id": sid, "root_family_id": "root", "analysis_class": "L3",
            "policy_id": "state_a", "analysis_cohort": "development",
            "design_axis": "fault_context", "axis_value": value, "fixed_context_json": "{}",
            "full_P0_failure": failed, "full_full_horizon": True,
            "full_P0_failure_minutes": 15. * failed,
            "full_observed_prefix_P0_failure_minutes": 15. * failed})
    _, intervals = _module()._boundaries(rows)
    assert intervals[0]["tested_values_json"] == ["GRID=0.0", "GT_1=0.5"]
    assert intervals[0]["adjacent_status_changes_json"] == []


def test_v2_current_guard_and_new_damage_reason_remain_visible_without_extra_diagnostics():
    decision = _decision("state_a", 0.)
    decision["policy_diagnostic"].update(guard_active=True,
        observed_cooling_to_electric_ratio=1.2, current_legal_grid_capacity_kW=850.,
        arrived_grid_port_kW=260., reasons=["new_observed_equipment_loss"],
        route="cooling_dominant_source_diversification", new_damage_observed=True,
        persistent_damage_is_not_repaired=True)
    scene = {"id": "s", "root_family_id": "r", "analysis_class": "L4b"}
    row = _module()._decisions(scene, "state_a", {
        "summary": {"observed_until_minute": 15.}, "decisions": [decision]})[0]
    assert row["guard_active"] is True
    assert row["mode_reasons_json"] == ["new_observed_equipment_loss"]
    assert row["observed_cooling_to_electric_ratio"] == 1.2
    assert row["current_legal_grid_capacity_kW"] == 850.
    assert row["arrived_grid_port_kW"] == 260.
    assert "future_events" not in json.dumps(row)


def test_failed_audit_and_changed_raw_are_rejected_before_output(batch, tmp_path):
    mp, pp, results, ap = batch
    audit = json.loads(ap.read_text())
    audit["passed"] = False
    _write(ap, audit)
    destination = tmp_path / "rejected"
    with pytest.raises(ValueError, match="audit"):
        _module().analyze_batch(mp, pp, results, ap, destination)
    assert not destination.exists()
    audit["passed"] = True
    _write(ap, audit)
    raw = next(results.glob("pairs/*/attempt_0001/trajectory.json.gz"))
    raw.write_bytes(raw.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="SHA"):
        _module().analyze_batch(mp, pp, results, ap, destination)
    assert not destination.exists()


def test_cli_uses_requested_paths_without_physics(batch, tmp_path, monkeypatch, capsys):
    module = _module()
    called = []
    monkeypatch.setattr(module, "analyze_batch", lambda *args: called.append(args) or {
        "registered_trajectory_count": 12, "unknown_full_trajectory_count": 1})
    assert module.main(["--manifest", str(batch[0]), "--plan", str(batch[1]),
                        "--results", str(batch[2]), "--audit", str(batch[3]),
                        "--out-dir", str(tmp_path / "analysis")]) == 0
    assert len(called[0]) == 5
    assert json.loads(capsys.readouterr().out)["registered_trajectory_count"] == 12
