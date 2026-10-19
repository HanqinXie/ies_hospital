"""Four-scenario analysis semantics on small, explicitly synthetic raw fixtures."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.dataset_manifest import digest
from tools.control_improvement.ranking import TARGET_NAMES


CLASSES = ("N2", "N4", "L1", "L2", "L3", "L4a", "L4b")
POLICIES = ("secure_milp", "priority_milp")


def _segment(start, end, *, failed=False, cooling_kW=0.0, electric_kW=0.0):
    lost = [[electric_kW, 0.0, cooling_kW], [0.0] * 3, [0.0] * 3, [0.0] * 3]
    return {
        "start_minute": start, "end_minute": end,
        "unserved_kW": lost,
        "classification": {
            "p0_guarantee_failure": failed,
            "p0_energy_loss": bool(electric_kW or cooling_kW),
            "p0_temperature_violation": False,
            "material_unserved_by_carrier": {
                "e": electric_kW > 0, "h": False, "c": cooling_kW > 0,
            },
        },
        "action": {"gt_electric_kW": [100.0, 100.0, 100.0],
                   "grid_import_kW": 400.0, "diesel_kW": 0.0,
                   "absorption_cooling_kW": 120.0,
                   "electric_chiller_cooling_kW": [25.0, 0.0, 0.0],
                   "boiler_steam_lbph": 50.0, "hrsg_steam_lbph": 200.0,
                   "tower_rejection_kW": 100.0,
                   "gt_fuel_MMBtu_h": 6.0},
        "cost_USD_step": 5.0,
        "temperature": {"P0": 24.0, "P1": 25.0, "P2": 26.0},
        "violation": {"P0": False, "P1": False, "P2": False},
    }


def _score(*, complete=True, failed=False, minutes=0.0, cooling=0.0):
    targets = [0.0] * len(TARGET_NAMES)
    targets[0] = int(failed)
    targets[1] = minutes
    targets[4] = cooling
    targets[14:17] = [24.0, 25.0, 26.0]
    targets[-1] = 100.0
    if not complete:
        targets = [1 if failed else None] + [None] * (len(TARGET_NAMES) - 1)
    return {"full_horizon": complete, "targets": targets,
            "observed_minutes": 600.0 if complete else 300.0,
            "observed_prefix_ENS_kWh_by_priority_carrier":
                [[0.0, 0.0, cooling], [0.0] * 3, [0.0] * 3, [0.0] * 3],
            "observed_prefix_accounting_cost_USD": 10.0}


def test_cooling_ens_is_not_final_p0_service_failure():
    from tools.four_scenarios.analysis import summarize_trajectory

    scene = {"id": "c", "analysis_class": "L2", "root_family_id": "root_1"}
    payload = {"summary": {"status": "completed", "observed_until_minute": 600.0,
                           "requested_end_minute": 600.0},
               "segments": [_segment(0.0, 600.0, cooling_kW=1.0)],
               "events": [], "decisions": []}
    score = _score(cooling=10.0)
    row, _ = summarize_trajectory(scene, "secure_milp", payload,
                                  {"status": "completed", "primary_score": score,
                                   "full_observation": score})
    assert row["full_P0_failure"] == 0
    assert row["full_P0_c_ENS_kWh"] == 10.0
    assert row["full_P0_c_material_minutes"] == 600.0
    assert row["full_P0_temperature_trigger_minutes"] == 0.0


def test_truncation_retains_observed_failure_but_not_unknown_tail():
    from tools.four_scenarios.analysis import summarize_trajectory

    scene = {"id": "u", "analysis_class": "L4b", "root_family_id": "root_1"}
    payload = {"summary": {"status": "model_constraint_infeasible",
                           "observed_until_minute": 300.0,
                           "requested_end_minute": 600.0},
               "segments": [_segment(0.0, 290.0),
                            _segment(290.0, 300.0, failed=True, electric_kW=6.0)],
               "events": [{"kind": "exogenous_fault_end", "time_minute": 330.0}],
               "decisions": [], "error": {"classification": "model_constraint_infeasible"}}
    score = _score(complete=False, failed=True)
    row, _ = summarize_trajectory(scene, "secure_milp", payload,
                                  {"status": "model_constraint_infeasible",
                                   "primary_score": score,
                                   "full_observation": score})
    assert row["full_P0_failure"] == 1
    assert row["full_P0_failure_minutes"] is None
    assert row["full_P0_e_ENS_kWh"] is None
    assert row["full_observed_prefix_P0_failure_minutes"] == 10.0
    assert row["full_observed_prefix_P0_e_ENS_kWh"] == pytest.approx(1.0)
    assert row["recovery_30min"] is None


def test_recovery_requires_30_clean_minutes_and_reports_later_recurrence():
    from tools.four_scenarios.analysis import recovery_after_event

    segments = [_segment(0.0, 100.0, failed=True),
                _segment(100.0, 115.0, failed=True),
                _segment(115.0, 145.0),
                _segment(145.0, 160.0, failed=True),
                _segment(160.0, 600.0)]
    recovery = recovery_after_event(segments, 100.0, observed_until=600.0,
                                    requested_end=600.0)
    assert recovery["recovery_30min"] is True
    assert recovery["recovery_start_minute"] == 115.0
    assert recovery["recovery_confirmed_minute"] == 145.0
    assert recovery["sustained_to_observation_end"] is False
    assert recovery["later_p0_recurrence_minute"] == 145.0


def test_gt_protection_trip_excludes_interlock_events():
    from tools.four_scenarios.analysis import summarize_trajectory

    scene = {"id": "p", "analysis_class": "L4a", "root_family_id": "root_1"}
    payload = {"summary": {"status": "completed", "observed_until_minute": 600.0,
                           "requested_end_minute": 600.0},
               "segments": [_segment(0.0, 600.0)],
               "events": [{"kind": "no_export_interlock", "time_minute": 120.0,
                           "device": "GT_3"},
                          {"kind": "protection_trip", "time_minute": 135.0,
                           "device": "GT_1"}],
               "decisions": []}
    score = _score()
    row, _ = summarize_trajectory(scene, "secure_milp", payload,
                                  {"status": "completed", "primary_score": score,
                                   "full_observation": score})
    assert row["protection_event_count"] == 2
    assert row["GT_protection_trip_count_observed"] == 1


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _batch_fixture(tmp_path):
    scenes = [{"id": "root1_" + name, "analysis_class": name,
               "root_family_id": "root1", "start_hour": 120,
               "experiment_role": "train", "is_final_blind": False,
               "exogenous_stream_id": "stream_" + name} for name in CLASSES]
    manifest = {"scenarios": scenes, "splits": {s["id"]: {"group_id": "root1"}
                                               for s in scenes},
                "signature": "manifest_signature_fixture"}
    manifest_path = tmp_path / "manifest.json"
    _write_json(manifest_path, manifest)
    plan = {"manifest_path": str(manifest_path), "manifest_sha256":
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "scenario_ids": [s["id"] for s in scenes],
            "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"},
                             {"policy_id": "priority_milp", "kind": "priority"}],
            "registered_pair_count": 14, "phase": "train", "signature": "plan_fixture"}
    plan_path = tmp_path / "plan.json"
    _write_json(plan_path, plan)
    results = tmp_path / "results"
    ledger = []
    for scene in scenes:
        for policy in POLICIES:
            sid, name = scene["id"], scene["analysis_class"]
            truncated = name == "L4b" and policy == "priority_milp"
            status = "model_constraint_infeasible" if truncated else "completed"
            score = _score(complete=not truncated, failed=truncated)
            payload = {"identity": {"scenario_id": sid, "policy_id": policy,
                                    "exogenous_stream_id": scene["exogenous_stream_id"]},
                       "scenario": scene, "initial_state": {"root": "root1"},
                       "summary": {"status": status,
                                   "observed_until_minute": 300.0 if truncated else 600.0,
                                   "requested_end_minute": 600.0},
                       "segments": [_segment(0.0, 290.0 if truncated else 600.0)] +
                                   ([_segment(290.0, 300.0, failed=True,
                                              electric_kW=6.0)] if truncated else []),
                       "decisions": [], "events": ([{"kind": "exogenous_fault_end",
                                                      "time_minute": 330.0}]
                                                  if name in ("L1", "L3", "L4b") else []),
                       "error": {"classification": status} if truncated else None}
            attempt = results / "pairs" / digest([sid, policy]) / "attempt_0001"
            attempt.mkdir(parents=True)
            raw = attempt / "trajectory.json.gz"
            with gzip.open(raw, "wt", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            result = {"scenario_id": sid, "policy_id": policy, "status": status,
                      "terminal": True, "trajectory_path": "trajectory.json.gz",
                      "trajectory_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                      "primary_score": score, "full_observation": score,
                      "binding": {"passed": True,
                                  "initial_state_sha256": digest(payload["initial_state"]),
                                  "exogenous_stream_id": scene["exogenous_stream_id"]}}
            _write_json(attempt / "result.json", result)
            ledger.append({"scenario_id": sid, "policy_id": policy,
                           "terminal": True, "status": status,
                           "latest_result_sha256": hashlib.sha256(
                               (attempt / "result.json").read_bytes()).hexdigest()})
    audit = {"passed": True,
             "registered_pair_count": 14, "terminal_pair_count": 14,
             "recomputed_pair_count": 14,
             "matched_initial_state_and_stream": True,
             "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
             "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
             "pair_ledger": ledger,
             "unknown_primary_pair_ids": ["root1_L4b/priority_milp"],
             "unknown_full_pair_ids": ["root1_L4b/priority_milp"]}
    audit_path = tmp_path / "AUDIT.json"
    _write_json(audit_path, audit)
    return manifest_path, plan_path, results, audit_path


def test_batch_counts_roots_and_keeps_unknown_in_matching(tmp_path):
    from tools.four_scenarios.analysis import analyze_batch

    manifest, plan, results, audit = _batch_fixture(tmp_path)
    summary = analyze_batch(manifest, plan, results, audit, out_dir=tmp_path / "analysis")
    classes = {(r["analysis_class"], r["policy_id"]): r
               for r in summary["class_summary"]}
    assert classes[("L4b", "priority_milp")]["independent_root_count"] == 1
    assert classes[("L4b", "priority_milp")]["registered_scenario_count"] == 1
    assert classes[("L4b", "priority_milp")]["unknown_full_count"] == 1
    assert classes[("L4b", "priority_milp")]["full_P0_failure_minutes_all_registered"] is None
    assert classes[("L4b", "priority_milp")]["observed_prefix_P0_failure_minutes_total"] == 10.0
    assert classes[("L4b", "priority_milp")]["recovery_unknown_count"] == 1
    assert classes[("N2", "secure_milp")]["registered_scenario_count"] == 1
    assert classes[("N2", "secure_milp")]["full_actual_energy_fee_USD_all_registered"] == 100.0
    assert summary["independent_root_count"] == 1
    assert all("full_observation_complete" in row for row in summary["scenario_results"])
    pairs = summary["matched_comparisons"]
    assert any(r["comparison_id"] == "L1_minus_N2" and
               r["left_scenario_id"] == "root1_L1" and
               r["right_scenario_id"] == "root1_N2" for r in pairs)
    assert any(r["comparison_id"] == "L3_minus_N4" for r in pairs)
    assert any(r["comparison_id"] == "L4b_minus_N4" and
               r["policy_id"] == "priority_milp" and
               r["full_complete_pair"] is False for r in pairs)
    assert (tmp_path / "analysis/SCENARIO_RESULTS.csv").is_file()
    assert (tmp_path / "analysis/UNKNOWN_AND_FAILURES.csv").is_file()


def test_batch_refuses_raw_sha_change_after_audit(tmp_path):
    from tools.four_scenarios.analysis import analyze_batch

    manifest, plan, results, audit = _batch_fixture(tmp_path)
    raw = next(results.rglob("trajectory.json.gz"))
    raw.write_bytes(b"changed after audit")
    with pytest.raises(ValueError, match="raw SHA"):
        analyze_batch(manifest, plan, results, audit)


def test_batch_refuses_cross_class_initial_state_mismatch_after_audit(tmp_path):
    from tools.four_scenarios.analysis import analyze_batch

    manifest, plan, results, audit_path = _batch_fixture(tmp_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    sid = "root1_L2"
    for policy in POLICIES:
        attempt = results / "pairs" / digest([sid, policy]) / "attempt_0001"
        raw = attempt / "trajectory.json.gz"
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        payload["initial_state"] = {"root": "other_root_state"}
        with gzip.open(raw, "wt", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
        result_path = attempt / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["trajectory_sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
        result["binding"]["initial_state_sha256"] = digest(payload["initial_state"])
        _write_json(result_path, result)
        entry = next(row for row in audit["pair_ledger"]
                     if row["scenario_id"] == sid and row["policy_id"] == policy)
        entry["latest_result_sha256"] = hashlib.sha256(result_path.read_bytes()).hexdigest()
    _write_json(audit_path, audit)

    with pytest.raises(ValueError, match="[Rr]oot.*initial state"):
        analyze_batch(manifest, plan, results, audit_path)


def test_batch_refuses_missing_class_in_matched_root(tmp_path):
    from tools.four_scenarios.analysis import analyze_batch

    manifest, plan_path, results, audit_path = _batch_fixture(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["scenario_ids"].remove("root1_N4")
    plan["registered_pair_count"] -= len(POLICIES)
    _write_json(plan_path, plan)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["pair_ledger"] = [row for row in audit["pair_ledger"]
                            if row["scenario_id"] != "root1_N4"]
    for key in ("registered_pair_count", "terminal_pair_count", "recomputed_pair_count"):
        audit[key] -= len(POLICIES)
    audit["plan_sha256"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    _write_json(audit_path, audit)

    with pytest.raises(ValueError, match="[Rr]oot.*seven|[Rr]oot.*class"):
        analyze_batch(manifest, plan_path, results, audit_path)


def test_analysis_cli_imports_from_workspace_without_pythonpath():
    completed = subprocess.run([sys.executable, "-B", "-m",
                                "tools.four_scenarios.analysis", "--help"],
                               cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", check=False)
    assert completed.returncode == 0, completed.stderr
    assert "--manifest" in completed.stdout
