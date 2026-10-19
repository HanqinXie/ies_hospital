"""Limited gate diagnostics preserve the physical source and causal timing."""
import copy
import csv
import gzip
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    assert (ROOT / "tools/state_dispatch/diagnostics.py").exists(), "diagnostic module is not implemented"
    return importlib.import_module("tools.state_dispatch.diagnostics")


def test_only_seven_registered_assumption_diagnostics_preserve_exogenous_physics():
    d = module()
    proposal = d.build_proposal(ROOT)
    scenes = proposal["scenarios"]
    assert len(scenes) == len({s["id"] for s in scenes}) == 7
    assert {s["diagnostic_variant"] for s in scenes} == {
        "day298_delay30", "day298_delay15", "day298_delay0", "day341_shadow",
        "day341_gate_t0", "day341_delay0", "day341_no_propagation"}
    assert proposal["policies"] == ["priority_milp", "secure_milp"]
    assert proposal["final_12_backgrounds_unopened"] is True
    sources = {s["id"]: s for p in d.SOURCE_MANIFESTS for s in
               json.loads((ROOT / p).read_text(encoding="utf-8"))["scenarios"]}
    for scene in scenes:
        parent = sources[scene["diagnostic_parent_scenario_id"]]
        for key in ("start_hour", "pre_event_warmup_steps", "initialization_seed", "faults", "shock",
                    "shock_by_priority", "exogenous_events", "exogenous_stream_id", "base_state_id",
                    "grid_limit_kW", "pre_event_grid_limit_kW", "full_source_support", "duration_steps"):
            assert scene[key] == parent[key], key
        assert not scene["is_final_blind"]
        assert scene["diagnostic_semantics"] == "execution_assumption_diagnostic_not_policy_improvement"
        assert scene["diagnostic_common_prefix_end_minute"] == (
            0 if scene["diagnostic_variant"] == "day341_gate_t0" else
            150 if scene["source_day_index"] == 298 else 120)
        if scene["diagnostic_variant"] == "day341_no_propagation":
            assert scene["stage"] == 2 and scene["event_class"] == "L2"
            assert scene["enable_propagation"] is False
            assert scene["shock_start_minute"] == 120


def test_manifest_is_signed_runner_compatible_no_fit_and_freeze_does_not_start_episodes(tmp_path):
    d = module()
    proposal = d.build_proposal(ROOT)
    manifest = d.build_diagnostic_manifest(proposal, ROOT)
    from tools.policy_redesign.closed_loop import _verify_manifest
    assert _verify_manifest(manifest)
    assert len(manifest["scenarios"]) == len(manifest["splits"]) == 7
    assert all(not s["fit_allowed"] and not s["selection_allowed"] for s in manifest["splits"].values())
    assert manifest["execution_contract"] == json.loads(
        (ROOT / d.SOURCE_MANIFESTS[0]).read_text(encoding="utf-8"))["execution_contract"]
    assert manifest["policy_search_final_12_backgrounds_unopened"] is True
    receipt = d.freeze_diagnostics(ROOT, tmp_path / "diag")
    assert receipt["episodes_started"] == 0
    assert Path(receipt["manifest_path"]).is_file()
    assert not (tmp_path / "diag/main/results").exists()
    with pytest.raises(FileExistsError):
        d.freeze_diagnostics(ROOT, tmp_path / "diag")
    changed = copy.deepcopy(proposal)
    changed["scenarios"][0]["grid_limit_kW"] = 999999
    with pytest.raises(ValueError):
        d.build_diagnostic_manifest(changed, ROOT)


def test_response_window_coalesces_and_ends_at_release_not_per_message_latency():
    module()
    from hospital_ies.state_transition import TransitionCore
    core = TransitionCore.__new__(TransitionCore)
    core.env = SimpleNamespace(dt_minutes=15, scenario={"stage": 4, "duration_steps": 40,
                                  "shock_start_minute": 150, "response_delay_minutes": 30})
    assert core.delayed_at(149) == (False, 180)
    assert core.delayed_at(150) == (True, 180)
    assert core.delayed_at(165) == (True, 180)
    assert core.delayed_at(180) == (False, 180)
    assert core.delayed_at(195) == (False, 180)


def test_saved_timing_csv_preserves_arrival_overwrite_and_actual_boiler_heat(tmp_path):
    d = module()
    path = ROOT / d.SAVED_PRIORITY_RAWS["day298"][0]
    sha = d.SAVED_PRIORITY_RAWS["day298"][1]
    receipt = d.extract_timing(path, tmp_path / "timing.csv", expected_sha256=sha)
    assert receipt["raw_sha256"] == sha
    rows = list(csv.DictReader((tmp_path / "timing.csv").open(encoding="utf-8-sig")))
    at180 = [r for r in rows if float(r["time_minute"]) == 180 and r["record_type"] == "event"]
    assert any(r["event_kind"] == "command_superseded" and r["command_id"] == "11"
               and r["superseded_by"] == "12" for r in at180)
    executed = [r for r in at180 if r["event_kind"] == "command_executed"]
    assert [r["command_id"] for r in executed] == ["12", "13"]
    segment = next(r for r in rows if r["record_type"] == "segment" and float(r["time_minute"]) == 180)
    assert float(segment["boiler_steam_lbph"]) == pytest.approx(1870.1539991279487)
    assert float(segment["GT_1_kW"]) == 225
    lost = next(r for r in rows if r["record_type"] == "segment" and float(r["time_minute"]) == 165)
    assert float(lost["hrsg_steam_lbph"]) == 0
    assert float(lost["P0_h_unserved_kW"]) == pytest.approx(176.21374561133595)
    with pytest.raises(FileExistsError):
        d.extract_timing(path, tmp_path / "timing.csv", expected_sha256=sha)
    with pytest.raises(ValueError):
        d.extract_timing(path, tmp_path / "bad.csv", expected_sha256="0" * 64)


def test_prefix_comparison_reports_physical_and_observation_changes_separately():
    d = module()
    with gzip.open(ROOT / d.SAVED_PRIORITY_RAWS["day341"][0], "rt", encoding="utf-8") as stream:
        raw = json.load(stream)
    same = d.compare_prefix(raw, copy.deepcopy(raw), 120)
    assert same["physical_prefix_equal"] and same["observation_prefix_equal"]
    changed = copy.deepcopy(raw)
    changed["segments"][0]["action"]["boiler_steam_lbph"] += 1
    assert not d.compare_prefix(raw, changed, 120)["physical_prefix_equal"]
    changed = copy.deepcopy(raw)
    changed["decisions"][0]["features"]["control_available"] = not changed["decisions"][0]["features"]["control_available"]
    result = d.compare_prefix(raw, changed, 120)
    assert result["physical_prefix_equal"] and not result["observation_prefix_equal"]
    changed["identity"]["exogenous_stream_id"] = "wrong"
    with pytest.raises(ValueError):
        d.compare_prefix(raw, changed, 120)
