"""Figure evidence comes from audited raw; discrete boundaries stay discrete."""
import csv
import gzip
import hashlib
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def api():
    assert (ROOT / "tools/state_dispatch/figures.py").is_file(), "v9 figure implementation is missing"
    return importlib.import_module("tools.state_dispatch.figures")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(tmp_path, *, complete=True):
    from hospital_ies.dataset_manifest import digest
    run = tmp_path / "audited"
    ids = ["secure_milp", "priority_milp", "state_new"]
    scene = {"id": "s1", "analysis_class": "L4a", "root_family_id": "root",
             "base_state_id": "initial", "is_final_blind": False}
    manifest, plan, audit = (run / x for x in ("manifest.json", "plan.json", "AUDIT.json"))
    write_json(manifest, {"scenarios": [scene]})
    write_json(plan, {"manifest_sha256": sha(manifest), "scenario_ids": ["s1"],
                      "policy_specs": [{"policy_id": x} for x in ids],
                      "registered_pair_count": 3, "final_blind_opened": False})
    ledger, paths = [], {}
    for index, pid in enumerate(ids):
        raw = run / "results/pairs" / digest(["s1", pid]) / "attempt_0001/trajectory.json.gz"
        raw.parent.mkdir(parents=True)
        segments = []
        for start in (0, 15) if complete else (0,):
            segments.append({"start_minute": start, "end_minute": start + 15,
                "action": {"gt_electric_kW": [130 + index, 0, 0], "grid_import_kW": 200,
                           "diesel_kW": 0, "hrsg_steam_lbph": 120 + index,
                           "boiler_steam_lbph": 200 + index, "absorption_cooling_kW": 30,
                           "electric_chiller_cooling_kW": [0, 0, 0]},
                "loads_kW": [[100, 20, 30]] * 4, "connected_loads_kW": [[100, 20, 30]] * 4,
                "unserved_kW": [[index, index + .1, index + .2], [0, 0, 0], [0, 0, 0],
                                [index + 2, index + 3, index + 4]],
                "classification": {"p0_guarantee_failure": index > 0}, "temperature": {"P0": 23},
                "protection_stress": {"GT_1": 1.2}, "command_id": start // 15 + 1,
                "arrived_source_setpoints": {"hrsg_steam_lbph": 99999}})
        payload = {"identity": {"scenario_id": "s1", "policy_id": pid, "group_id": "root",
                                "base_state_id": "initial", "exogenous_stream_id": "stream"},
                   "summary": {"status": "completed" if complete else "solver_error",
                               "observed_until_minute": 30 if complete else 15, "requested_end_minute": 30},
                   "segments": segments,
                   "policy_diagnostics": ([{"decision_time_min": 0, "mode": "prepare"},
                                           {"decision_time_min": 15, "mode": "emergency"}]
                                          if pid == "state_new" else []),
                   "events": [{"kind": "command_arrived", "event_id": "c", "time_minute": 15,
                               "command_id": 2, "generated_minute": 0},
                              {"kind": "protection_trip", "event_id": "p", "time_minute": 15,
                               "device": "GT_1"}], "decisions": []}
        with gzip.open(raw, "wt", encoding="utf-8") as stream:
            json.dump(payload, stream)
        result = raw.with_name("result.json")
        write_json(result, {"scenario_id": "s1", "policy_id": pid,
                            "status": payload["summary"]["status"], "terminal": True,
                            "trajectory_path": raw.name, "trajectory_sha256": sha(raw)})
        ledger.append({"scenario_id": "s1", "policy_id": pid, "terminal": True,
                       "status": payload["summary"]["status"],
                       "attempts": [{"attempt": "attempt_0001", "raw_sha256": sha(raw)}],
                       "latest_result_sha256": sha(result)})
        paths[pid] = raw
    write_json(audit, {"passed": True, "matched_initial_state_and_stream": True,
                       "manifest_sha256": sha(manifest), "plan_sha256": sha(plan),
                       "registered_pair_count": 3, "terminal_pair_count": 3, "pair_ledger": ledger})
    selection = {"figure_id": "timing", "title": "Synthetic timing evidence",
                 "manifest": str(manifest), "plan": str(plan), "audit": str(audit),
                 "results": str(run / "results"), "scenario_id": "s1", "policy_ids": ids,
                 "view_start": 0, "view_end": 30}
    return selection, paths


def test_arbitrary_three_policies_render_actual_raw_curves_events_modes_and_hashes(tmp_path):
    d = api()
    selection, raws = fixture(tmp_path)
    index = d.build_figures([selection], tmp_path / "figures", dpi=80)
    figure = index["figures"][0]
    for key in ("curves_csv", "events_csv", "png", "svg"):
        assert Path(figure[key]).is_file()
    rows = list(csv.DictReader(Path(figure["curves_csv"]).open(encoding="utf-8-sig")))
    assert {r["policy_id"] for r in rows} == set(selection["policy_ids"])
    state = [r for r in rows if r["policy_id"] == "state_new"]
    assert [r["mode"] for r in state] == ["prepare", "emergency"]
    assert [float(r["hrsg_steam_lbph"]) for r in state] == [122, 122]
    assert float(state[0]["P0_h_unserved_kW"]) == 2.1
    assert float(state[0]["P3_c_unserved_kW"]) == 6
    assert float(state[0]["total_h_demand_kW"]) == 80
    assert {x["raw_sha256"] for x in figure["raw_sources"]} == {sha(p) for p in raws.values()}
    events = list(csv.DictReader(Path(figure["events_csv"]).open(encoding="utf-8-sig")))
    assert any(r["kind"] == "command_arrived" and r["generated_minute"] == "0"
               for r in events)
    assert (tmp_path / "figures/RAW_SHA256_INDEX.json").is_file()


def test_tampered_raw_or_failed_audit_rejected_before_output(tmp_path):
    d = api()
    selection, raws = fixture(tmp_path)
    raws["state_new"].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw SHA"):
        d.build_figures([selection], tmp_path / "bad")
    assert not (tmp_path / "bad").exists()
    audit = Path(selection["audit"])
    value = json.loads(audit.read_text()); value["passed"] = False
    write_json(audit, value)
    with pytest.raises(ValueError, match="audit"):
        d.build_figures([selection], tmp_path / "bad_audit")


def test_unknown_tail_is_not_rendered_as_zero_and_policy_limit_is_explicit(tmp_path):
    d = api()
    selection, _ = fixture(tmp_path, complete=False)
    with pytest.raises(ValueError, match="complete observed"):
        d.build_figures([selection], tmp_path / "unknown")
    selection["policy_ids"] = ["a", "b", "c", "d", "e"]
    with pytest.raises(ValueError, match="one to four"):
        d.build_figures([selection], tmp_path / "too_many")


def test_boundary_export_preserves_only_measured_coordinates_and_unknown_rows(tmp_path):
    d = api()
    source = tmp_path / "points.csv"
    source.write_text("scenario_id,policy_id,raw_sha256,delay,heat_ENS,complete\n"
                      "a,state_new," + "a" * 64 + ",15,0,True\n"
                      "b,state_new," + "b" * 64 + ",22,3,True\n"
                      "c,state_new," + "c" * 64 + ",30,,False\n", encoding="utf-8")
    selection = {"kind": "boundary", "figure_id": "measured", "title": "Measured points",
                 "csv": str(source), "x_field": "delay", "y_field": "heat_ENS",
                 "complete_field": "complete", "x_label": "Response window (min)",
                 "y_label": "P0 heat ENS (kWh)", "policy_ids": ["state_new"]}
    result = d.build_figures([selection], tmp_path / "boundary", dpi=80)
    figure = result["figures"][0]
    rows = list(csv.DictReader(Path(figure["curves_csv"]).open(encoding="utf-8-sig")))
    assert [(float(r["x"]), r["y"], r["plotted"]) for r in rows] == [
        (15, "0.0", "True"), (22, "3.0", "True"), (30, "", "False")]
    assert figure["interpolation"] is False
    assert figure["n_measured_plotted_points"] == 2
    assert figure["source_csv_sha256"] == sha(source)
    assert "scatter" in Path(figure["svg"]).read_text(encoding="utf-8")


def test_boundary_rows_explicitly_marked_final_blind_are_rejected(tmp_path):
    d = api()
    source = tmp_path / "sealed.csv"
    source.write_text("scenario_id,policy_id,raw_sha256,x,y,complete,is_final_blind\n"
                      "sealed,state_new," + "a" * 64 + ",15,0,True,True\n", encoding="utf-8")
    selection = {"kind": "boundary", "figure_id": "sealed", "title": "Not eligible",
                 "csv": str(source), "x_field": "x", "y_field": "y", "complete_field": "complete",
                 "policy_ids": ["state_new"]}
    with pytest.raises(ValueError, match="sealed"):
        d.build_figures([selection], tmp_path / "refused")
