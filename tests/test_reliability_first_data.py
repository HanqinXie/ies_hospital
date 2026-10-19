"""V4 reliability data planning and label semantics, without a simulator run."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json

import pytest


def data():
    return importlib.import_module("tools.reliability_first.data")


def diagnostics():
    return importlib.import_module("tools.reliability_first.diagnostics")


def scenario(root: str, role: str, pressure: int = 3) -> dict:
    return {
        "id": f"{root}:{pressure}", "root_family_id": root,
        "time_block_id": root, "experiment_role": role,
        "pressure_point_index": pressure, "start_hour": 100,
        "duration_steps": 40, "step_minutes": 15,
        "stage": 4, "event_class": "L4a", "event_schema_version": "p2a.events.v1",
        "enable_propagation": True, "shock": {"e": 0.5, "h": 0.2, "c": 0.1},
        "shock_start_minute": 90.0, "shock_duration_minutes": 180.0,
        "exogenous_events": [{"origin": "exogenous", "kind": "load_shock",
                              "start_minute": 90.0, "end_minute": 270.0,
                              "shock": {"e": 0.5, "h": 0.2, "c": 0.1}}],
        "full_source_support": {"raw_start_minute": 10000 + 1440 * int(root[-1]),
                                "raw_end_minute": 10660 + 1440 * int(root[-1])},
    }


def active(s: dict, t: float) -> tuple:
    shock_start = s["shock_start_minute"]
    shock_end = shock_start + s["shock_duration_minutes"]
    fault_start = s.get("fault_start_minute", 0.0)
    fault_end = fault_start + s.get("fault_duration_minutes", 0.0)
    return (
        s["shock"] if shock_start <= t < shock_end else {},
        s.get("faults", {}) if fault_start <= t < fault_end else {},
    )


def test_snapshot_plan_uses_varied_predeclared_clocks_and_root_roles():
    specs = [scenario("root1", "pilot_train", i) for i in range(5)]
    specs += [scenario("root2", "pilot_validation", i) for i in range(5)]
    specs += [scenario("root3", "development_boundary", i) for i in range(5)]
    plan = data().snapshot_plan({"fixed_scenarios": specs}, max_snapshots=20)
    assert len(plan) == 12
    assert {r["experiment_role"] for r in plan} == {
        "pilot_train", "pilot_validation", "development_boundary"
    }
    assert len({r["snapshot_minute"] for r in plan}) >= 6
    assert {r["snapshot_minute"] for r in plan} != {75.0, 105.0, 150.0, 420.0}
    assert all(r["snapshot_minute"] <= 255 for r in plan)
    assert all(r["conditional_futures"] == 4 for r in plan)
    assert all(r["selection_uses_future_event_clock"] is False for r in plan)
    assert all(r["continuation_policy_id"] == "secure_milp" for r in plan)
    assert {r["root_family_id"] for r in plan} == {"root1", "root2", "root3"}


def test_snapshot_plan_does_not_read_future_event_clock_or_labels():
    source = scenario("root1", "pilot_train")
    before = data().snapshot_plan({"fixed_scenarios": [source]})
    changed = copy.deepcopy(source)
    changed["shock_start_minute"] = 180.0
    changed["shock_duration_minutes"] = 210.0
    changed["future_failure_label"] = 1
    after = data().snapshot_plan({"fixed_scenarios": [changed]})
    assert before == after


def test_proposal_snapshot_group_matches_existing_frozen_manifest_contract():
    module = data()
    from hospital_ies.dataset_manifest import digest
    s = scenario("root1", "pilot_train")
    proposal = {"sampling_seed": 20261015, "fixed_scenarios": [s]}
    planned = module.snapshot_plan(proposal)
    expected = "control_background:" + digest([20261015, s["time_block_id"]])
    assert all(row["group_id"] == expected for row in planned)


def test_snapshot_plan_rejects_cross_role_root_and_overlapping_support():
    a = scenario("root1", "pilot_train")
    b = scenario("root1", "pilot_validation", pressure=2)
    with pytest.raises(ValueError, match="role"):
        data().snapshot_plan({"fixed_scenarios": [a, b]})
    b = scenario("root2", "pilot_validation")
    b["full_source_support"] = copy.deepcopy(a["full_source_support"])
    with pytest.raises(ValueError, match="support|overlap"):
        data().snapshot_plan({"fixed_scenarios": [a, b]})


def test_prepare_destination_allows_intake_root_but_refuses_partial_prepare(tmp_path):
    run = tmp_path / "v4_run"
    run.mkdir()
    (run / "logs").mkdir()
    assert data().check_prepare_destination(run) is None
    (run / "design").mkdir()
    (run / "design" / "source_proposal.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Existing|partial"):
        data().check_prepare_destination(run)


def test_correct_plan_writes_new_version_bound_to_unchanged_inputs(tmp_path):
    module = data()
    run = tmp_path / "v4_run"
    design = run / "design"
    source = run / "new_data"
    design.mkdir(parents=True)
    source.mkdir()
    s = scenario("root1", "pilot_train")
    proposal = {"sampling_seed": 20261015, "fixed_scenarios": [s]}
    proposal_bytes = json.dumps(proposal).encode("utf-8")
    (design / "source_proposal.json").write_bytes(proposal_bytes)
    new_plan = module.snapshot_plan(proposal)
    old_plan = copy.deepcopy(new_plan)
    for row in old_plan:
        row["group_id"] = s["root_family_id"]
    old_bytes = json.dumps(old_plan).encode("utf-8")
    (design / "snapshot_plan.json").write_bytes(old_bytes)
    manifest = {"signature": "frozen-signature", "scenarios": [s],
                "splits": {s["id"]: {"experiment_role": "pilot_train",
                                      "group_id": new_plan[0]["group_id"]}}}
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    (source / "global_manifest.json").write_bytes(manifest_bytes)
    (design / "V4_SOURCE_PREPARE.json").write_text(json.dumps({
        "proposal_sha256": hashlib.sha256(proposal_bytes).hexdigest(),
        "manifest_signature": manifest["signature"]}), encoding="utf-8")
    receipt = module.correct_plan(run)
    assert (design / "snapshot_plan.json").read_bytes() == old_bytes
    assert (source / "global_manifest.json").read_bytes() == manifest_bytes
    assert json.loads((design / "snapshot_plan_v2.json").read_text(encoding="utf-8")) == new_plan
    assert receipt["old_plan_sha256"] == hashlib.sha256(old_bytes).hexdigest()
    assert receipt["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert receipt["changed_field_paths"] == ["[*].group_id"]
    with pytest.raises(FileExistsError, match="v2|correction"):
        module.correct_plan(run)


def test_conditional_future_stress_preserves_every_prefix_state():
    original = scenario("root1", "pilot_train")
    original["event_class"] = "L4b"
    original["faults"] = {"GRID": 0.0}
    original["fault_start_minute"] = 120.0
    original["fault_duration_minutes"] = 180.0
    original["exogenous_events"].insert(0, {
        "origin": "exogenous", "kind": "device_fault", "device": "GRID",
        "availability": 0.0, "start_minute": 120.0, "end_minute": 300.0,
    })
    preserved = copy.deepcopy(original)
    variants = data().conditional_futures(original, 225, count=4)
    assert variants[0] == preserved
    assert original == preserved
    assert len(variants) == 4
    assert len({(s["shock_duration_minutes"], s["fault_duration_minutes"])
                for s in variants}) == 4
    for v in variants:
        for t in range(0, 226):
            assert active(v, t) == active(original, t)
        assert v["faults"] == original["faults"]
        assert v["shock"] == original["shock"]
        assert v["start_hour"] == original["start_hour"]
        for event in v["exogenous_events"]:
            kind = "fault" if event["kind"] == "device_fault" else "shock"
            assert event["end_minute"] == v[f"{kind}_start_minute"] + v[f"{kind}_duration_minutes"]


def test_conditional_futures_rejects_rewriting_recovered_prefix():
    with pytest.raises(ValueError, match="future|prefix|ended"):
        data().conditional_futures(scenario("root1", "pilot_train"), 300, count=4)


def segment(start: float, end: float, failed: bool, ens: float = 0.0) -> dict:
    loads = [[0.0, 0.0, 0.0] for _ in range(4)]
    loads[0][0] = ens
    return {"start_minute": start, "end_minute": end,
            "classification": {"p0_guarantee_failure": failed},
            "unserved_kW": loads, "temperature": {"P0": 24.0}}


def payload() -> dict:
    return {"summary": {"status": "completed", "observed_until_minute": 75},
            "segments": [segment(0, 15, False), segment(15, 30, True, 10),
                         segment(30, 45, True, 20), segment(45, 60, False),
                         segment(60, 75, True, 5)]}


def test_snapshot_labels_separate_first_failure_ongoing_and_refailure():
    p = payload()
    before = diagnostics().snapshot_state_labels(p, 0, window=30)
    assert before["ever_failed_before_or_at_snapshot"] is False
    assert before["current_failed_at_snapshot"] is False
    assert before["future_first_failure_binary"] == 1
    assert before["future_failure_minutes"] == 15
    assert before["future_ENS_kWh_by_priority_carrier"][0][0] == pytest.approx(2.5)
    during = diagnostics().snapshot_state_labels(p, 30, window=30)
    assert during["ever_failed_before_or_at_snapshot"] is True
    assert during["current_failed_at_snapshot"] is True
    assert during["recovered_at_snapshot"] is False
    assert during["future_first_failure_binary"] is None
    recovered = diagnostics().snapshot_state_labels(p, 60, window=15)
    assert recovered["recovered_at_snapshot"] is True
    assert recovered["post_recovery_refailure_binary"] == 1


def test_censored_future_is_unknown_not_zero():
    row = diagnostics().snapshot_state_labels(payload(), 75, window=15)
    assert row["future_window_complete"] is False
    assert row["future_failure_minutes"] is None
    assert row["future_ENS_kWh_by_priority_carrier"] is None


def test_opportunity_rows_retain_severe_worse_and_unknown_future():
    rows = []
    for fid in range(4):
        rows.append({"candidate_id": "secure", "future_id": fid,
                     "P0_failure": 1, "P0_failure_minutes": 20,
                     "P0_e_ENS_kWh": 5, "P0_h_ENS_kWh": 0,
                     "P0_c_ENS_kWh": 0, "status": "completed"})
        rows.append({"candidate_id": "residual", "future_id": fid,
                     "P0_failure": 1, "P0_failure_minutes": 15 if fid < 3 else 60,
                     "P0_e_ENS_kWh": 4 if fid < 3 else 20,
                     "P0_h_ENS_kWh": 0, "P0_c_ENS_kWh": 0,
                     "status": "completed"})
    result = diagnostics().opportunity_rows(rows, baseline_candidate_id="secure")
    residual = next(r for r in result if r["candidate_id"] == "residual")
    assert residual["better_future_count"] == 3
    assert residual["worse_future_count"] == 1
    assert residual["all_stress_futures_nonworse"] is False
    rows[-1]["status"] = "model_constraint_infeasible"
    result = diagnostics().opportunity_rows(rows, baseline_candidate_id="secure")
    residual = next(r for r in result if r["candidate_id"] == "residual")
    assert residual["unknown_future_count"] == 1
    assert residual["all_stress_futures_nonworse"] is None
