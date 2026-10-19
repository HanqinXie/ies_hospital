"""Pressure-crossing summaries keep paired support and unknown explicit."""
import hashlib
import json
from pathlib import Path

import pytest

from tools.policy_search.sensitivity import summarize_pressure_crossing


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _prereg_fixture(tmp_path):
    from tools.policy_search.stages import ROOT

    run = tmp_path / "run"
    scenes = [{"id": f"p{i}", "root_family_id": "r", "condition_id": "EC0",
               "pressure_point_index": i, "experiment_role": "development",
               "is_final_blind": False, "time_block_id": "b",
               "base_state_id": "base", "initialization_seed": 7,
               "exogenous_stream_id": "stream", "exogenous_events": []} for i in range(5)]
    manifest_path = run / "new_data/global_manifest.json"
    _write(manifest_path, {"scenarios": scenes})
    freeze_path = run / "design/STAGES_SOURCE_FREEZE.json"
    _write(freeze_path, {"stage_source_sha256":
                         _sha(ROOT / "tools/policy_search/stages.py")})
    plan_path = run / "development/registered_plan.json"
    plan = {"phase": "development", "manifest_path": str(manifest_path),
            "manifest_sha256": _sha(manifest_path),
            "scenario_ids": [s["id"] for s in scenes],
            "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"}]}
    plan["signature"] = _digest(plan)
    _write(plan_path, plan)
    _write(run / "development/STAGE_REGISTRATION.json",
           {"schema": "policy_search.development_stage_registration.v1",
            "plan_sha256": _sha(plan_path), "plan_signature": plan["signature"],
            "stage_source_sha256": _sha(ROOT / "tools/policy_search/stages.py"),
            "stages_source_freeze_sha256": _sha(freeze_path),
            "manifest_sha256": _sha(manifest_path),
            "scenario_ids": plan["scenario_ids"], "policy_specs": plan["policy_specs"],
            "pressure_sensitivity_indices": [0, 1, 2, 3, 4],
            "sensitivity_root_family_ids": ["r"],
            "same_policy_set_at_every_registered_pressure": True})
    return run


def _score(minutes, *, complete=True):
    values = [0.0] * 19
    values[1] = minutes
    return {"targets": values if complete else [None] * 19,
            "target_mask": [complete] * 19, "full_horizon": complete}


def test_crossing_keeps_unknown_and_same_root_delta():
    scenarios = []
    rows = []
    for root in ("r1", "r2"):
        for point in (1, 3):
            sid = f"{root}_p{point}"
            scenarios.append({"id": sid, "root_family_id": root,
                              "condition_id": "EC0", "pressure_point_index": point,
                              "pressure_coordinate": point / 4,
                              "time_block_id": "block_" + root,
                              "base_state_id": "base_" + root,
                              "initialization_seed": 7,
                              "exogenous_stream_id": "stream_" + root,
                              "exogenous_events": [],
                              "experiment_role": "development", "is_final_blind": False})
            rows.append({"scenario_id": sid, "policy_id": "secure_milp",
                         "primary_score": _score(10), "status": "completed",
                         "binding": {"passed": True, "initial_state_sha256": "init_" + root,
                                     "exogenous_stream_id": "stream_" + root}})
            complete = not (root == "r2" and point == 3)
            rows.append({"scenario_id": sid, "policy_id": "feedback",
                         "primary_score": _score(10 + point, complete=complete),
                         "status": "completed" if complete else "model_constraint_infeasible",
                         "binding": {"passed": True, "initial_state_sha256": "init_" + root,
                                     "exogenous_stream_id": "stream_" + root}})
    plan = {"scenario_ids": [s["id"] for s in scenarios],
            "policy_specs": [{"policy_id": "secure_milp"},
                             {"policy_id": "feedback"}]}
    result = summarize_pressure_crossing(plan, {"scenarios": scenarios},
                                         {"results": rows}, window="primary_score",
                                         required_points=(1, 3), anchor_point=1)
    group = result["by_condition_and_pressure"]["EC0"]
    assert group["1"]["feedback"]["registered_case_count"] == 2
    assert group["1"]["feedback"]["complete_paired_case_count"] == 2
    assert group["1"]["feedback"]["sum_delta_on_observed"][1] == 2
    assert group["3"]["feedback"]["registered_case_count"] == 2
    assert group["3"]["feedback"]["complete_paired_case_count"] == 1
    assert group["3"]["feedback"]["unknown_case_ids"] == ["r2_p3"]
    crossing = result["within_root_effect_change_from_anchor"]["feedback"]["EC0"]["3"]
    assert crossing["paired_root_count"] == 1
    assert crossing["unknown_root_ids"] == ["r2"]
    assert crossing["sum_effect_change_on_observed"][1] == 2


def test_crossing_rejects_missing_pressure_or_duplicate_row():
    plan = {"scenario_ids": ["s1", "s3"],
            "policy_specs": [{"policy_id": "secure_milp"}]}
    scenes = {"scenarios": [{"id": "s1", "root_family_id": "r",
                             "condition_id": "normal", "pressure_point_index": 1,
                             "time_block_id": "block", "base_state_id": "base",
                             "initialization_seed": 7, "exogenous_stream_id": "stream",
                             "exogenous_events": [],
                             "experiment_role": "development", "is_final_blind": False},
                            {"id": "s3", "root_family_id": "r",
                             "condition_id": "normal", "pressure_point_index": 3,
                             "time_block_id": "block", "base_state_id": "base",
                             "initialization_seed": 7, "exogenous_stream_id": "stream",
                             "exogenous_events": [],
                             "experiment_role": "development", "is_final_blind": False}]}
    summary = {"results": [{"scenario_id": "s1", "policy_id": "secure_milp",
                            "primary_score": _score(0),
                            "binding": {"passed": True, "initial_state_sha256": "init",
                                        "exogenous_stream_id": "stream"}},
                           {"scenario_id": "s3", "policy_id": "secure_milp",
                            "primary_score": _score(0),
                            "binding": {"passed": True, "initial_state_sha256": "init",
                                        "exogenous_stream_id": "stream"}}]}
    try:
        summarize_pressure_crossing(plan, scenes, summary,
                                    required_points=(1, 2, 3), anchor_point=1)
    except ValueError as error:
        assert "pressure" in str(error)
    else:
        raise AssertionError("Missing registered pressure must fail")
    try:
        summarize_pressure_crossing(plan, scenes, {"results": summary["results"] * 2},
                                    required_points=(1, 3), anchor_point=1)
    except ValueError as error:
        assert "row" in str(error)
    else:
        raise AssertionError("Duplicate result row must fail")


def test_crossing_rejects_same_root_with_different_manifest_or_raw_identity():
    from copy import deepcopy

    scenes = {"scenarios": [
        {"id": "p0", "root_family_id": "r", "condition_id": "EC0",
         "pressure_point_index": 0, "experiment_role": "development", "is_final_blind": False,
         "time_block_id": "b", "base_state_id": "base", "initialization_seed": 7,
         "exogenous_stream_id": "stream", "exogenous_events": []},
        {"id": "p1", "root_family_id": "r", "condition_id": "EC0",
         "pressure_point_index": 1, "experiment_role": "development", "is_final_blind": False,
         "time_block_id": "b", "base_state_id": "base", "initialization_seed": 7,
         "exogenous_stream_id": "stream", "exogenous_events": []}]}
    plan = {"scenario_ids": ["p0", "p1"],
            "policy_specs": [{"policy_id": "secure_milp"}]}
    rows = [{"scenario_id": sid, "policy_id": "secure_milp", "primary_score": _score(0),
             "binding": {"passed": True, "initial_state_sha256": "init",
                         "exogenous_stream_id": "stream"}}
            for sid in ("p0", "p1")]
    changed_manifest = deepcopy(scenes)
    changed_manifest["scenarios"][1]["time_block_id"] = "other"
    try:
        summarize_pressure_crossing(plan, changed_manifest, {"results": rows},
                                    required_points=(0, 1), anchor_point=0)
    except ValueError as error:
        assert "time_block" in str(error)
    else:
        raise AssertionError("Different same-root time block must fail")
    changed_rows = deepcopy(rows)
    changed_rows[1]["binding"]["initial_state_sha256"] = "other"
    try:
        summarize_pressure_crossing(plan, scenes, {"results": changed_rows},
                                    required_points=(0, 1), anchor_point=0)
    except ValueError as error:
        assert "initial" in str(error)
    else:
        raise AssertionError("Different actual initial state must fail")
    changed_schedule = deepcopy(scenes)
    changed_schedule["scenarios"][0]["exogenous_events"] = [
        {"kind": "load_shock", "start_minute": 10, "end_minute": 20,
         "shock": {"e": 0.1}}]
    changed_schedule["scenarios"][1]["exogenous_events"] = [
        {"kind": "load_shock", "start_minute": 11, "end_minute": 20,
         "shock": {"e": 0.9}}]
    with pytest.raises(ValueError, match="schedule"):
        summarize_pressure_crossing(plan, changed_schedule, {"results": rows},
                                    required_points=(0, 1), anchor_point=0)


def test_sensitivity_preregistration_binds_stage_anchor_and_source(tmp_path):
    from tools.policy_search.sensitivity import preregister_sensitivity, verify_preregistration

    run = _prereg_fixture(tmp_path)
    path = preregister_sensitivity(run)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    assert receipt["anchor_pressure_point_index"] == 2
    assert receipt["pressure_point_indices"] == [0, 1, 2, 3, 4]
    assert receipt["sensitivity_source_sha256"] == _sha(
        __import__("tools.policy_search.sensitivity", fromlist=["x"]).__file__)
    assert receipt["stage_registration_sha256"] == _sha(
        run / "development/STAGE_REGISTRATION.json")
    verify_preregistration(run)
    with pytest.raises(FileExistsError):
        preregister_sensitivity(run)
    stage_path = run / "development/STAGE_REGISTRATION.json"
    stage = json.loads(stage_path.read_text(encoding="utf-8"))
    stage["same_policy_set_at_every_registered_pressure"] = False
    _write(stage_path, stage)
    with pytest.raises(ValueError, match="stage|registration"):
        verify_preregistration(run)


def test_sensitivity_preregistration_refuses_started_development(tmp_path):
    from tools.policy_search.sensitivity import preregister_sensitivity

    run = _prereg_fixture(tmp_path)
    journal = run / "development/results/attempts.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text('{"event":"started"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="before.*development|already"):
        preregister_sensitivity(run)
    assert not (run / "development/SENSITIVITY_PREREGISTRATION.json").exists()
