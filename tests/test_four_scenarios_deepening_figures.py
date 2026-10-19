"""Audited paired mechanism figures from saved, synthetic raw trajectories."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.dataset_manifest import digest


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _segment(start: float, end: float, *, gt2: float, shortage: float) -> dict:
    return {
        "start_minute": start, "end_minute": end,
        "action": {"gt_electric_kW": [0.0, gt2, 0.0],
                   "grid_import_kW": 250.0, "diesel_kW": 0.0,
                   "hrsg_steam_lbph": 100.0, "boiler_steam_lbph": 200.0,
                   "absorption_cooling_kW": 10.0,
                   "electric_chiller_cooling_kW": [0.0, 0.0, 0.0]},
        "connected_loads_kW": [[100.0, 20.0, 10.0]] * 4,
        "unserved_kW": [[shortage, 0.0, 0.0], [0.0, 0.0, 0.0],
                        [0.0, 0.0, 0.0], [shortage * 2, 0.0, 0.0]],
        "classification": {"p0_guarantee_failure": shortage > 0},
        "temperature": {"P0": 23.0},
    }


def _fixture(tmp_path: Path, *, complete: bool = True):
    run = tmp_path / "outputs" / "v8_synthetic_run"
    scene = {"id": "case_l3", "analysis_class": "L3",
             "root_family_id": "root_one", "time_block_id": "day_7",
             "is_final_blind": False}
    manifest = run / "new_data" / "global_manifest.json"
    _write_json(manifest, {"scenarios": [scene]})
    plan = run / "main" / "registered_plan.json"
    _write_json(plan, {"manifest_sha256": _sha(manifest),
                       "scenario_ids": [scene["id"]],
                       "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"},
                                        {"policy_id": "priority_milp", "kind": "priority"}],
                       "registered_pair_count": 2, "final_blind_opened": False})
    ledger = []
    paths = {}
    for policy in ("secure_milp", "priority_milp"):
        pair = run / "main" / "results" / "pairs" / digest([scene["id"], policy]) / "attempt_0001"
        pair.mkdir(parents=True)
        raw = pair / "trajectory.json.gz"
        segments = [_segment(0, 15, gt2=130 if policy == "priority_milp" else 0,
                             shortage=2 if policy == "priority_milp" else 100)]
        if complete:
            segments.append(_segment(15, 30, gt2=0, shortage=0))
        payload = {"identity": {"scenario_id": scene["id"], "policy_id": policy,
                                "group_id": "root_one"},
                   "summary": {"status": "completed" if complete else "truncated",
                               "observed_until_minute": 30 if complete else 15,
                               "requested_end_minute": 30},
                   "segments": segments,
                   "events": [{"event_id": "fault", "kind": "exogenous_fault_start",
                               "time_minute": 5, "device": "GRID"}] +
                             ([{"event_id": "trip", "kind": "protection_trip",
                                "time_minute": 15, "device": "GT_2"}]
                              if policy == "priority_milp" and complete else [])}
        with gzip.open(raw, "wt", encoding="utf-8") as stream:
            json.dump(payload, stream)
        result = pair / "result.json"
        _write_json(result, {"scenario_id": scene["id"], "policy_id": policy,
                             "status": payload["summary"]["status"], "terminal": True,
                             "trajectory_path": raw.name, "trajectory_sha256": _sha(raw)})
        ledger.append({"scenario_id": scene["id"], "policy_id": policy,
                       "status": payload["summary"]["status"], "terminal": True,
                       "attempts": [{"attempt": "attempt_0001", "raw_sha256": _sha(raw)}],
                       "latest_result_sha256": _sha(result)})
        paths[policy] = raw
    audit = run / "main" / "AUDIT.json"
    _write_json(audit, {"passed": True, "matched_initial_state_and_stream": True,
                        "manifest_sha256": _sha(manifest), "plan_sha256": _sha(plan),
                        "registered_pair_count": 2, "terminal_pair_count": 2,
                        "pair_ledger": ledger})
    return run, paths


def _selection():
    return [{"figure_id": "day7_trip", "plot_kind": "gt_trip",
             "scenario_ids": ["case_l3"], "view_start": 0, "view_end": 30}]


def test_paired_figure_keeps_real_priority_trip_and_segment_values(tmp_path):
    from tools.four_scenarios.deepening_figures import build_figures

    run, paths = _fixture(tmp_path)
    out = tmp_path / "new_v8_figures"
    index = build_figures(run, out, _selection())
    assert len(index["figures"]) == 1
    figure = index["figures"][0]
    for key in ("png_path", "svg_path", "csv_path", "events_csv_path"):
        assert Path(figure[key]).is_file()
    with Path(figure["csv_path"]).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 4
    priority = next(x for x in rows if x["policy_id"] == "priority_milp"
                    and x["start_minute"] == "0.0")
    secure = next(x for x in rows if x["policy_id"] == "secure_milp"
                  and x["start_minute"] == "0.0")
    assert priority["GT_2_kW"] == "130"
    assert priority["P0_e_unserved_kW"] == "2"
    assert secure["P0_e_unserved_kW"] == "100"
    with Path(figure["events_csv_path"]).open(newline="", encoding="utf-8") as stream:
        trips = [x for x in csv.DictReader(stream) if x["kind"] == "protection_trip"]
    assert [(x["policy_id"], x["device"], x["time_minute"]) for x in trips] == [
        ("priority_milp", "GT_2", "15.0")]
    assert {x["raw_sha256"] for x in figure["raw_sources"]} == {
        _sha(paths["secure_milp"]), _sha(paths["priority_milp"])}


def test_paired_figure_rejects_raw_modified_after_audit(tmp_path):
    from tools.four_scenarios.deepening_figures import build_figures

    run, paths = _fixture(tmp_path)
    paths["priority_milp"].write_bytes(b"modified after audit")
    with pytest.raises(ValueError, match="raw SHA"):
        build_figures(run, tmp_path / "new_v8_figures", _selection())


def test_paired_figure_does_not_plot_unknown_tail_as_zero(tmp_path):
    from tools.four_scenarios.deepening_figures import build_figures

    run, _ = _fixture(tmp_path, complete=False)
    with pytest.raises(ValueError, match="complete observed trajectory"):
        build_figures(run, tmp_path / "new_v8_figures", _selection())


def _coverage_fixture():
    levels = ("ordinary", "elevated", "boundary_candidate")
    scenes, rows = [], []
    for index, level in enumerate(levels):
        scene = {"id": "e_" + level, "analysis_class": "L2",
                 "design_axis": "main", "design_level": "e|" + level,
                 "root_family_id": "root_1", "season_quarter": 1,
                 "shock_mode": "e", "shock": {"e": [0.3, 0.75, 1.4][index],
                                              "h": 0, "c": 0},
                 "is_final_blind": False}
        scenes.append(scene)
        for policy in ("secure_milp", "priority_milp"):
            secure = policy == "secure_milp"
            failed = secure and index != 1
            rows.append({"scenario_id": scene["id"], "policy_id": policy,
                         "raw_sha256": "a" * 64, "full_full_horizon": True,
                         "full_P0_failure": int(failed),
                         "full_P0_e_ENS_kWh": [2, 0, 3][index] if secure else 0,
                         "full_P0_h_ENS_kWh": 0, "full_P0_c_ENS_kWh": 0,
                         "GT_protection_trip_count_observed":
                             int(secure and index == 1)})
    return {"scenarios": scenes}, rows


def test_coverage_tables_keep_classes_separate_and_only_observed_intensity_points():
    from tools.four_scenarios.deepening_figures import coverage_tables

    manifest, rows = _coverage_fixture()
    detail, cells, reversals = coverage_tables(manifest, rows)
    assert len(detail) == 6
    assert len(cells) == 3 * 4 * 4 * 3 * 2
    cell = next(x for x in cells if x["analysis_class"] == "L2" and
                x["season_quarter"] == 1 and x["shock_mode"] == "e" and
                x["intensity_level"] == "ordinary" and x["policy_id"] == "secure_milp")
    assert cell["n_unique_roots"] == cell["n_registered_trajectories"] == 1
    assert cell["p0_observed_failed_count"] == 1
    assert cell["p0_max_single_carrier_ENS_kWh"] == 2
    assert cell["gt_trip_trajectory_count"] == 0
    absent = next(x for x in cells if x["analysis_class"] == "L4a" and
                  x["season_quarter"] == 2 and x["shock_mode"] == "h" and
                  x["intensity_level"] == "boundary_candidate" and
                  x["policy_id"] == "priority_milp")
    assert absent["n_registered_trajectories"] == 0
    assert absent["p0_max_single_carrier_ENS_kWh"] is None
    observed = {x["metric"]: x for x in reversals if x["analysis_class"] == "L2" and
                x["policy_id"] == "secure_milp"}
    assert observed["P0_failure"]["nonmonotonic"] is True
    assert observed["P0_e_ENS_kWh"]["nonmonotonic"] is True
    assert observed["GT_protection_trip_count"]["nonmonotonic"] is True
    assert all(x["raw_sha256"] == "a" * 64 for x in detail)


def test_coverage_unknown_tail_is_not_a_zero_or_nonfailure():
    from tools.four_scenarios.deepening_figures import coverage_tables

    manifest, rows = _coverage_fixture()
    row = rows[0]
    row.update({"full_full_horizon": False, "full_P0_failure": None,
                "full_P0_e_ENS_kWh": None})
    _, cells, reversals = coverage_tables(manifest, rows)
    cell = next(x for x in cells if x["analysis_class"] == "L2" and
                x["season_quarter"] == 1 and x["shock_mode"] == "e" and
                x["intensity_level"] == "ordinary" and x["policy_id"] == "secure_milp")
    assert cell["n_unknown_tail"] == 1
    assert cell["p0_observed_failed_count"] == 0
    assert cell["p0_complete_no_failure_count"] == 0
    assert cell["p0_max_single_carrier_ENS_kWh"] is None
    assert next(x for x in reversals if x["analysis_class"] == "L2" and
                x["policy_id"] == "secure_milp" and
                x["metric"] == "P0_failure")["nonmonotonic"] is None
    with pytest.raises(ValueError, match="Duplicate"):
        coverage_tables(manifest, rows + rows[:1])


def test_coverage_figures_write_class_specific_images_and_exact_source_csv(tmp_path):
    from tools.four_scenarios.deepening_figures import build_coverage_figures

    run = tmp_path / "outputs/v8_synthetic_coverage"
    manifest, rows = _coverage_fixture()
    manifest_path = run / "new_data/global_manifest.json"
    _write_json(manifest_path, manifest)
    plan_path = run / "main/registered_plan.json"
    _write_json(plan_path, {"manifest_sha256": _sha(manifest_path),
                            "scenario_ids": [scene["id"] for scene in manifest["scenarios"]],
                            "policy_specs": [{"policy_id": p} for p in
                                             ("secure_milp", "priority_milp")],
                            "registered_pair_count": 6, "final_blind_opened": False})
    audit_path = run / "main/AUDIT.json"
    _write_json(audit_path, {"passed": True,
                             "matched_initial_state_and_stream": True,
                             "manifest_sha256": _sha(manifest_path),
                             "plan_sha256": _sha(plan_path),
                             "registered_pair_count": 6, "terminal_pair_count": 6,
                             "recomputed_pair_count": 6,
                             "pair_ledger": [{"scenario_id": row["scenario_id"],
                                              "policy_id": row["policy_id"],
                                              "latest_result_sha256": "b" * 64,
                                              "attempts": [{"attempt": "attempt_0001",
                                                            "raw_sha256": row["raw_sha256"],
                                                            "result_sha256": "b" * 64}]}
                                             for row in rows]})
    analysis = run / "analysis/TRAJECTORY_RESULTS.csv"
    analysis.parent.mkdir(parents=True)
    with analysis.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    _write_json(run / "analysis/ANALYSIS_SUMMARY.json", {
        "manifest_sha256": _sha(manifest_path), "plan_sha256": _sha(plan_path),
        "audit_sha256": _sha(audit_path)})
    out = run / "figures/coverage"
    receipt = build_coverage_figures(run, out)
    assert len(receipt["figures"]) == 3
    assert receipt["coverage_cell_count"] == 288
    assert receipt["coverage_pair_count"] == 6
    assert [x["analysis_class"] for x in receipt["figures"]] == ["L2", "L4a", "L4b"]
    for item in receipt["figures"]:
        assert Path(item["png_path"]).is_file()
        assert Path(item["svg_path"]).is_file()
    with (out / "COVERAGE_CELLS.csv").open(newline="", encoding="utf-8") as stream:
        saved = list(csv.DictReader(stream))
    assert len(saved) == 288
    assert next(x for x in saved if x["analysis_class"] == "L4b")["n_registered_trajectories"] == "0"
    assert (out / "COVERAGE_PAIRS.csv").is_file()
    assert (out / "MATCHED_NONMONOTONIC.csv").is_file()
    analysis.write_text(analysis.read_text(encoding="utf-8").replace("a" * 64, "c" * 64, 1),
                        encoding="utf-8")
    with pytest.raises(ValueError, match="raw SHA"):
        build_coverage_figures(run, run / "figures/coverage_tampered")
