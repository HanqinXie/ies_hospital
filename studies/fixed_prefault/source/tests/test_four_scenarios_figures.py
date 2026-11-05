"""Traceable four-scenario figures from tiny synthetic audited raw fixtures."""
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


CLASSES = ("L1", "L3", "L2", "L4b")


def _json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _segment(start, end, *, failed):
    return {
        "start_minute": start, "end_minute": end,
        "action": {"gt_electric_kW": [1.0, 2.0, 3.0],
                   "grid_import_kW": 4.0, "diesel_kW": 5.0,
                   "hrsg_steam_lbph": 50.0, "boiler_steam_lbph": 10.0,
                   "absorption_cooling_kW": 40.0,
                   "electric_chiller_cooling_kW": [10.0, 20.0, 30.0]},
        "connected_loads_kW": [[10.0, 20.0, 30.0]] * 4,
        "unserved_kW": [[6.0 if failed else 0.0, 0.0, 0.0],
                        [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 7.0]],
        "classification": {"p0_guarantee_failure": failed,
                           "p0_temperature_violation": False},
        "temperature": {"P0": 23.0},
    }


def _fixture(tmp_path: Path):
    scenes = [{"id": f"s_{name}", "analysis_class": name,
               "root_family_id": f"root_{name}", "is_final_blind": False}
              for name in CLASSES]
    manifest = tmp_path / "manifest.json"
    _json(manifest, {"scenarios": scenes, "signature": "synthetic_fixture"})
    plan = tmp_path / "plan.json"
    _json(plan, {"manifest_sha256": _sha(manifest),
                 "scenario_ids": [s["id"] for s in scenes],
                 "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"}],
                 "registered_pair_count": 4, "signature": "plan_fixture"})
    results = tmp_path / "results"
    ledger, analysis_rows, raw_paths = [], [], {}
    for scene in scenes:
        sid, name = scene["id"], scene["analysis_class"]
        truncated = name == "L4b"
        status = "model_constraint_infeasible" if truncated else "completed"
        end = 20.0 if truncated else 30.0
        events = [
            {"event_id": "e1", "kind": "exogenous_fault_start", "time_minute": 5.0,
             "device": "GT_1"},
            {"event_id": "e2", "kind": "command_generated", "time_minute": 6.0,
             "command": {"id": 3, "request": {"targets": {"diesel": 9.0}},
                         "dispatch": {"diesel_kW": 5.0}}},
            {"event_id": "e3", "kind": "command_arrived", "time_minute": 7.0,
             "command_id": 3},
            {"event_id": "e4", "kind": "command_executed", "time_minute": 8.0,
             "command_id": 3, "executed_dispatch": {"diesel_kW": 5.0}},
            {"event_id": "e5", "kind": "protection_trip", "time_minute": 11.0,
             "device": "GT_2"},
            {"event_id": "future", "kind": "exogenous_fault_end", "time_minute": 25.0},
        ]
        payload = {
            "identity": {"scenario_id": sid, "policy_id": "secure_milp"},
            "summary": {"status": status, "observed_until_minute": end,
                        "requested_end_minute": 30.0},
            "segments": [_segment(0.0, 10.0, failed=False),
                         _segment(10.0, end, failed=name == "L1")],
            "events": events,
        }
        attempt = results / "pairs" / digest([sid, "secure_milp"]) / "attempt_0001"
        attempt.mkdir(parents=True)
        raw = attempt / "trajectory.json.gz"
        with gzip.open(raw, "wt", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
        result = attempt / "result.json"
        _json(result, {"scenario_id": sid, "policy_id": "secure_milp",
                       "status": status, "terminal": True,
                       "trajectory_path": "trajectory.json.gz",
                       "trajectory_sha256": _sha(raw)})
        ledger.append({"scenario_id": sid, "policy_id": "secure_milp",
                       "status": status, "terminal": True,
                       "attempts": [{"attempt": "attempt_0001",
                                     "result_path": str(result), "result_sha256": _sha(result),
                                     "raw_path": str(raw), "raw_sha256": _sha(raw)}],
                       "latest_result_sha256": _sha(result)})
        analysis_rows.append({"analysis_class": name, "scenario_id": sid,
                              "root_family_id": scene["root_family_id"],
                              "policy_id": "secure_milp", "status": status,
                              "raw_path": str(raw), "raw_sha256": _sha(raw),
                              "full_P0_failure_minutes": "10" if name == "L1" else "",
                              "full_full_horizon": str(not truncated),
                              "full_observation_complete": str(not truncated),
                              "observed_until_minute": end})
        raw_paths[name] = raw
    audit = tmp_path / "audit.json"
    _json(audit, {"passed": True, "plan_sha256": _sha(plan),
                  "manifest_sha256": _sha(manifest),
                  "registered_pair_count": 4, "terminal_pair_count": 4,
                  "matched_initial_state_and_stream": True,
                  "pair_ledger": ledger})
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    with (analysis / "SCENARIO_RESULTS.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(analysis_rows[0]))
        writer.writeheader()
        writer.writerows(analysis_rows)
    _json(analysis / "ANALYSIS_SUMMARY.json",
          {"schema": "four_scenarios.readonly_analysis.v1",
           "manifest_sha256": _sha(manifest), "plan_sha256": _sha(plan),
           "audit_sha256": _sha(audit), "registered_scenario_count": 4,
           "registered_pair_count": 4, "final_blind_opened": False,
           "scenario_results": analysis_rows})
    return manifest, plan, results, audit, analysis, raw_paths


def test_figures_retain_segment_values_command_chain_and_unknown_tail(tmp_path):
    from tools.four_scenarios.figures import build_figures

    manifest, plan, results, audit, analysis, _ = _fixture(tmp_path)
    output = build_figures(manifest, plan, results, audit, analysis, tmp_path / "figures")
    assert len(output["figures"]) == 4
    by_class = {row["analysis_class"]: row for row in output["figures"]}
    assert set(by_class) == set(CLASSES)
    assert (tmp_path / "figures/FIGURE_INDEX.csv").is_file()
    for entry in output["figures"]:
        assert Path(entry["png_path"]).is_file()
        assert Path(entry["svg_path"]).is_file()
    with Path(by_class["L1"]["csv_path"]).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert [(r["start_minute"], r["end_minute"]) for r in rows] == [
        ("0.0", "10.0"), ("10.0", "30.0")]
    assert rows[1]["GT_2_kW"] == "2.0"
    assert rows[1]["P0_e_unserved_kW"] == "6.0"
    assert rows[1]["P3_c_unserved_kW"] == "7.0"
    assert rows[1]["P0_guarantee_failure"] == "1"
    with Path(by_class["L4b"]["csv_path"]).open(newline="", encoding="utf-8") as stream:
        unknown_rows = list(csv.DictReader(stream))
    assert [(r["start_minute"], r["end_minute"]) for r in unknown_rows] == [
        ("0.0", "10.0"), ("10.0", "20.0"), ("20.0", "30.0")]
    assert unknown_rows[-1]["observation_status"] == "unknown_tail"
    assert unknown_rows[-1]["diesel_kW"] == ""
    assert unknown_rows[-1]["P0_e_unserved_kW"] == ""
    with Path(by_class["L4b"]["events_csv_path"]).open(newline="", encoding="utf-8") as stream:
        events = list(csv.DictReader(stream))
    assert "future" not in {e["event_id"] for e in events}
    assert [(e["kind"], e["command_id"], e["diesel_requested_kW"],
             e["diesel_planned_kW"], e["diesel_executed_kW"]) for e in events
            if e["kind"] in ("command_generated", "command_arrived", "command_executed")] == [
                ("command_generated", "3", "9.0", "5.0", ""),
                ("command_arrived", "3", "", "", ""),
                ("command_executed", "3", "", "", "5.0")]


def test_figures_refuse_raw_changed_after_audit(tmp_path):
    from tools.four_scenarios.figures import build_figures

    manifest, plan, results, audit, analysis, raw_paths = _fixture(tmp_path)
    raw_paths["L1"].write_bytes(b"changed after audit")
    with pytest.raises(ValueError, match="raw SHA"):
        build_figures(manifest, plan, results, audit, analysis, tmp_path / "figures")


def test_figures_refuse_analysis_csv_failure_label_tamper(tmp_path):
    from tools.four_scenarios.figures import build_figures

    manifest, plan, results, audit, analysis, _ = _fixture(tmp_path)
    csv_path = analysis / "SCENARIO_RESULTS.csv"
    with csv_path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    next(row for row in rows if row["analysis_class"] == "L1")["full_P0_failure_minutes"] = "0"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="Analysis CSV.*summary"):
        build_figures(manifest, plan, results, audit, analysis, tmp_path / "figures")


def test_figures_refuse_mixed_version_analysis_summary(tmp_path):
    from tools.four_scenarios.figures import build_figures

    manifest, plan, results, audit, analysis, _ = _fixture(tmp_path)
    summary_path = analysis / "ANALYSIS_SUMMARY.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["audit_sha256"] = "0" * 64
    _json(summary_path, summary)
    with pytest.raises(ValueError, match="Analysis summary.*SHA"):
        build_figures(manifest, plan, results, audit, analysis, tmp_path / "figures")


def test_representative_contrast_shows_available_protection_mechanism():
    from tools.four_scenarios.figures import _select

    rows = [{"analysis_class": name, "scenario_id": f"{name}_clear",
             "policy_id": "secure_milp", "full_full_horizon": "True",
             "full_P0_failure_minutes": "0", "protection_event_count": "0"}
            for name in CLASSES]
    rows += [
        {"analysis_class": "L4b", "scenario_id": "L4b_failure",
         "policy_id": "secure_milp", "full_full_horizon": "True",
         "full_P0_failure_minutes": "15", "protection_event_count": "0"},
        {"analysis_class": "L4b", "scenario_id": "L4b_protected",
         "policy_id": "secure_milp", "full_full_horizon": "True",
         "full_P0_failure_minutes": "0", "protection_event_count": "1"},
    ]
    selected = _select(rows)
    contrast = next((row, reason) for row, role, reason in selected
                    if row["analysis_class"] == "L4b" and role == "no_failure_case")
    assert contrast[0]["scenario_id"] == "L4b_protected"
    assert "protection" in contrast[1]


def test_policy_request_is_distinct_from_generated_and_executed_command():
    from tools.four_scenarios.figures import _event_rows

    requested = {"security": {"gt_loading_penalty_USD_per_kWh": 0.35}}
    payload = {
        "summary": {"observed_until_minute": 10.0},
        "decisions": [{"decision_time_min": 5.0, "requested_command": requested}],
        "events": [
            {"event_id": "generated:1", "kind": "command_generated", "time_minute": 5.0,
             "command": {"id": 1, "request": requested,
                         "dispatch": {"diesel_kW": 0.0}}},
            {"event_id": "executed:1", "kind": "command_executed", "time_minute": 6.0,
             "command_id": 1, "executed_dispatch": {"diesel_kW": 0.0}},
        ],
    }
    rows = _event_rows(payload, "s", "secure_milp", "rawhash")
    assert [(r["kind"], r["source_collection"], r["time_minute"]) for r in rows] == [
        ("policy_request", "decisions", 5.0),
        ("command_generated", "events", 5.0),
        ("command_executed", "events", 6.0)]
    assert json.loads(rows[0]["request_json"]) == requested
    assert json.loads(rows[1]["request_json"]) == requested
    assert rows[0]["command_id"] is None


def test_no_observed_segment_keeps_entire_plot_horizon_unknown(tmp_path):
    from tools.four_scenarios.figures import (_segment_rows, _write_csv, _draw,
                                              SEGMENT_FIELDS, EVENT_FIELDS)

    payload = {"summary": {"status": "model_constraint_infeasible",
                           "observed_until_minute": 0.0,
                           "requested_end_minute": 30.0},
               "segments": [], "events": []}
    rows = _segment_rows(payload, "s", "secure_milp", "rawhash")
    assert len(rows) == 1
    assert rows[0]["observation_status"] == "unknown_tail"
    assert rows[0]["start_minute"] == 0.0
    assert rows[0]["end_minute"] == 30.0
    assert rows[0]["diesel_kW"] is None
    source = tmp_path / "empty.csv"
    events = tmp_path / "empty.events.csv"
    _write_csv(source, SEGMENT_FIELDS, rows)
    _write_csv(events, EVENT_FIELDS, [])
    _draw(source, events, tmp_path / "empty.png", tmp_path / "empty.svg",
          analysis_class="L4b", scenario_id="s", raw_sha="rawhash",
          role="unknown_truncated")
    assert (tmp_path / "empty.png").is_file()
    svg = (tmp_path / "empty.svg").read_text(encoding="utf-8")
    assert "Request" in svg and "Generated" in svg
