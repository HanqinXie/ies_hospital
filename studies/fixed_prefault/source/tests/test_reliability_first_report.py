import json
from pathlib import Path

import pytest

from tools.reliability_first.report import (paired_policy_summary, _recommend,
                                            _verified_closed_loop, _soft_probe_inventory,
                                            _first_probe_failure, _identity_order_audit)
from hospital_ies.dataset_manifest import digest


def _result(sid, policy, *, failure=0, minutes=0, p0e=0, fee=10,
            status="completed", full=True):
    targets = [failure, minutes, p0e] + [0.0] * 11 + [21.0, 22.0, 23.0, 0.0, fee]
    return {"scenario_id": sid, "policy_id": policy, "status": status,
            "primary_score": {"full_horizon": full, "target_mask": [full] * 19,
                              "targets": targets},
            "command_counts": {"intervention_steps": 1 if policy != "secure_milp" else 0}}


def test_paired_summary_preserves_new_failure_and_unknown_denominator():
    plan = {"scenario_ids": ["a", "b"], "policy_specs": [
        {"policy_id": "secure_milp"}, {"policy_id": "nn_gated"}]}
    summary = {"matched_initial_state_passed": True, "results": [
        _result("a", "secure_milp", failure=0),
        _result("a", "nn_gated", failure=1, minutes=15, p0e=2, fee=8),
        _result("b", "secure_milp", failure=1, minutes=30),
        _result("b", "nn_gated", status="model_constraint_infeasible", full=False)]}
    result, rows = paired_policy_summary(plan, summary, "nn_gated")
    assert result["registered_case_count"] == 2
    assert result["matched_known_case_count"] == 1
    assert result["unknown_or_unmatched_case_count"] == 1
    assert result["new_P0_failure_cases"] == 1
    assert result["P0_failure_minutes_delta"] == 15
    assert result["ENS_kWh_delta_by_priority_carrier"][0][0] == 2
    assert result["actual_energy_fee_delta_USD"] == -2
    assert len(rows) == 2 and rows[1]["paired_known"] is False


def test_partial_window_confirmed_failure_is_counted_but_losses_stay_unknown():
    plan = {"scenario_ids": ["a"], "policy_specs": [
        {"policy_id": "secure_milp"}, {"policy_id": "nn_gated"}]}
    secure = _result("a", "secure_milp", failure=0)
    candidate = _result("a", "nn_gated", failure=1, status="model_constraint_infeasible", full=False)
    candidate["primary_score"]["target_mask"][0] = True
    candidate["primary_score"]["targets"][1:] = [None] * 18
    result, rows = paired_policy_summary(plan, {"matched_initial_state_passed": True,
                                                "results": [secure, candidate]}, "nn_gated")
    assert result["matched_known_case_count"] == 0
    assert result["known_target_case_counts"][0] == 1
    assert result["known_target_case_counts"][1] == 0
    assert result["new_P0_failure_cases"] == 1
    assert result["confirmed_new_P0_failure_cases_on_partial_window"] == 1
    assert result["P0_failure_minutes_delta"] is None
    assert result["ENS_kWh_delta_by_priority_carrier"][0][0] is None
    assert rows[0]["paired_delta"][0] == 1
    assert rows[0]["paired_delta"][1] is None


def test_recommendation_rejects_lower_priority_service_harm():
    base = {"policy_id": "nn_gated", "registered_case_count": 2,
            "matched_known_case_count": 2, "unknown_or_unmatched_case_count": 0,
            "known_target_case_counts": [2] * 19,
            "new_P0_failure_cases": 0, "reduced_P0_failure_cases": 1,
            "worst_case_P0_duration_increase_minutes": 0,
            "P0_failure_minutes_delta": -10,
            "ENS_kWh_delta_by_priority_carrier": [[0, 0, 0], [1000, 0, 0],
                                                  [0, 0, 0], [0, 0, 0]],
            "worst_case_ENS_kWh_increase_by_priority_carrier": [[0, 0, 0],
                                                               [1000, 0, 0],
                                                               [0, 0, 0], [0, 0, 0]],
            "worst_case_temperature_increase_C_by_P0_P1_P2": [0, 0, 0],
            "secondary_GT_trip_delta": 0, "worst_case_secondary_GT_trip_increase": 0,
            "actual_energy_fee_delta_USD": -500}
    policy, reason = _recommend([base])
    assert policy == "secure_milp"
    assert "P1" in reason


def test_recommendation_no_benefit_reason_is_accurate():
    base = {"policy_id": "nn_gated", "registered_case_count": 1,
            "matched_known_case_count": 1, "unknown_or_unmatched_case_count": 0,
            "known_target_case_counts": [1] * 19,
            "new_P0_failure_cases": 0, "reduced_P0_failure_cases": 0,
            "worst_case_P0_duration_increase_minutes": 0,
            "P0_failure_minutes_delta": 0,
            "ENS_kWh_delta_by_priority_carrier": [[0, 0, 0]] * 4,
            "worst_case_ENS_kWh_increase_by_priority_carrier": [[0, 0, 0]] * 4,
            "worst_case_temperature_increase_C_by_P0_P1_P2": [0, 0, 0],
            "secondary_GT_trip_delta": 0, "worst_case_secondary_GT_trip_increase": 0,
            "actual_energy_fee_delta_USD": -500}
    policy, reason = _recommend([base])
    assert policy == "secure_milp"
    assert "未证明关键服务" in reason


def test_report_refuses_summary_not_bound_to_latest_raw_attempt(tmp_path, monkeypatch):
    from tools.reliability_first import closed_loop

    plan = {"signature": "frozen", "scenario_ids": ["a"],
            "policy_specs": [{"policy_id": "secure_milp"}]}
    monkeypatch.setattr(closed_loop, "_validated_plan", lambda _: (plan, {}))
    pair = tmp_path / "pairs" / digest(["a", "secure_milp"]) / "attempt_0001"
    pair.mkdir(parents=True)
    raw = pair / "trajectory.json.gz"
    raw.write_bytes(b"real raw")
    from tools.reliability_first.report import _sha
    result = {"scenario_id": "a", "policy_id": "secure_milp", "status": "completed",
              "terminal": True, "trajectory_path": raw.name, "trajectory_sha256": _sha(raw),
              "binding": {"passed": True, "initial_state_sha256": "s",
                          "exogenous_stream_id": "e"}}
    (pair / "result.json").write_text(json.dumps(result), encoding="utf-8")
    summary = {"plan_signature": "frozen", "results": [result], "registered_pair_count": 1,
               "terminal_pair_count": 1, "status_counts": {"completed": 1},
               "matched_initial_state_passed": True}
    (tmp_path / "summary_0001.json").write_text(json.dumps(summary), encoding="utf-8")
    assert _verified_closed_loop(tmp_path)[2] == summary
    raw.write_bytes(b"changed raw")
    with pytest.raises(ValueError, match="raw hash mismatch"):
        _verified_closed_loop(tmp_path)


def test_optional_soft_probe_summary_requires_frozen_source_and_raw(tmp_path):
    from tools.reliability_first.report import _sha

    probe_source = tmp_path / "soft_objective_probe.py"
    probe_source.write_text("# frozen probe\n", encoding="utf-8")
    paths = {
        "design_sha256": tmp_path / "design/soft_objective_probe_v2.json",
        "manifest_sha256": tmp_path / "new_data/global_manifest.json",
        "snapshot_plan_sha256": tmp_path / "design/snapshot_plan_v2.json",
        "branch_collection_design_sha256": tmp_path / "design/branch_collection_v1.json",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    paths["branch_collection_design_sha256"].write_text(
        json.dumps({"branch_source_sha256": "original-core"}), encoding="utf-8")
    raw = tmp_path / "soft_objective_probe_v2/snapshot_073/attempt_0001/continuation.json.gz"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"raw trajectory")
    summary_path = tmp_path / "soft_objective_probe_v2/PROBE_SUMMARY.json"
    binding = {"probe_source_sha256": _sha(probe_source),
               **{name: _sha(path) for name, path in paths.items()},
               "branch_source_sha256": "original-core"}
    result_path = raw.parent / "PROBE_RESULT.json"
    result_path.write_text(json.dumps({
        "schema": "reliability_first.soft_objective_probe_result.v1",
        "index": 73, "status": "complete", "source_binding": binding,
        "raw_trajectory_hashes": {raw.relative_to(tmp_path).as_posix(): _sha(raw)},
    }), encoding="utf-8")
    record = {
        "schema": "reliability_first.soft_objective_probe_summary.v1",
        "registered_snapshots": 1,
        "completed_snapshots": 1,
        "unknown_snapshots": 0,
        "not_attempted_snapshots": 0,
        "attempts": {"new_continuations_started": 1, "new_continuations_finished": 1},
        "source_binding": binding,
        "raw_trajectory_hashes": {raw.relative_to(tmp_path).as_posix(): _sha(raw)},
        "results": [{"index": 73, "status": "complete",
                     "result_path": result_path.relative_to(tmp_path).as_posix(),
                     "result_sha256": _sha(result_path)}],
        "per_candidate": {"security_weight_2": {"better": 1, "tie": 2, "worse": 0,
                                                   "unknown": 1}},
        "per_future": {}, "strict_opportunity_candidate_count": 1,
        "strict_opportunity_snapshot_count": 1,
    }
    summary_path.write_text(json.dumps(record), encoding="utf-8")
    assert _soft_probe_inventory(tmp_path, probe_source=probe_source)["verified_raw_count"] == 1
    raw.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw hash mismatch"):
        _soft_probe_inventory(tmp_path, probe_source=probe_source)


def test_first_probe_interface_error_is_preserved_with_old_source(tmp_path):
    from tools.reliability_first.report import _sha

    folder = tmp_path / "soft_objective_probe"
    folder.mkdir()
    source = folder / "SOURCE_SNAPSHOT.py"
    source.write_text("# old interface\n", encoding="utf-8")
    result_path = folder / "snapshot_002/attempt_0001/PROBE_RESULT.json"
    result_path.parent.mkdir(parents=True)
    binding = {"probe_source_sha256": _sha(source)}
    result_path.write_text(json.dumps({"source_binding": binding, "status": "unknown",
                                       "errors": [{"type": "AttributeError",
                                                   "message": "policy_context absent"}]}), encoding="utf-8")
    (folder / "PROBE_SUMMARY.json").write_text(json.dumps({
        "source_binding": binding, "registered_snapshots": 24, "unknown_snapshots": 1,
        "attempts": {"new_continuations_started": 0},
        "results": [{"index": 2, "result_path": result_path.relative_to(tmp_path).as_posix(),
                     "result_sha256": _sha(result_path)}]}), encoding="utf-8")
    history = _first_probe_failure(tmp_path)
    assert history["new_continuations_started"] == 0
    assert history["failures"][0]["errors"][0]["type"] == "AttributeError"


def test_identity_order_sidecar_is_verified_without_overwriting_unknown(tmp_path):
    from tools.reliability_first.report import _sha

    audit_source = tmp_path / "soft_objective_probe_audit.py"
    probe_source = tmp_path / "soft_objective_probe.py"
    audit_source.write_text("# independent read-only audit\n", encoding="utf-8")
    probe_source.write_text("# frozen probe\n", encoding="utf-8")
    design = tmp_path / "design/soft_objective_probe_v2.json"
    design.parent.mkdir()
    design.write_text("{}", encoding="utf-8")
    folder = tmp_path / "soft_objective_probe_v2"
    pair = folder / "snapshot_139/attempt_0001"
    pair.mkdir(parents=True)
    plan = pair / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    result = pair / "PROBE_RESULT.json"
    result.write_text(json.dumps({"index": 139, "status": "unknown"}), encoding="utf-8")
    raw = pair / "future_00_candidate_01.json.gz"
    raw.write_bytes(b"saved raw")
    summary = folder / "PROBE_SUMMARY.json"
    summary.write_text(json.dumps({"completed_snapshots": 0, "unknown_snapshots": 1,
        "results": [{"index": 139, "status": "unknown",
                     "result_path": result.relative_to(tmp_path).as_posix(),
                     "result_sha256": _sha(result), "source_result_sha256": "old-source"}]}), encoding="utf-8")
    sidecar = folder / "IDENTITY_ORDER_AUDIT_v1.json"
    sidecar.write_text(json.dumps({
        "schema": "reliability_first.soft_probe_identity_order_audit.v1",
        "status": "complete", "derived_from_saved_raw_only": True,
        "new_rollout_attempts": 0,
        "official_v2_status_preserved": {"completed_snapshots": 0, "unknown_snapshots": 1},
        "unknown_snapshot_indices_examined": [139], "affected_snapshot_indices": [139],
        "verified_new_raw_count": 1, "verified_secure_raw_count": 0,
        "order_only_verified_pair_count": 1, "unresolved_pair_count": 0,
        "per_candidate": {"weight_2": {"better": 0, "tie": 1, "worse": 0, "unknown": 0}},
        "strict_opportunity_snapshot_count": 0,
        "source_binding": {"audit_source_sha256": _sha(audit_source),
                           "probe_source_sha256": _sha(probe_source),
                           "design_sha256": _sha(design),
                           "probe_summary_sha256": _sha(summary),
                           "result_sha256_by_index": {"139": _sha(result)},
                           "attempt_plan_sha256_by_index": {"139": _sha(plan)},
                           "source_result_sha256_by_index": {"139": "old-source"}},
        "raw_trajectory_hashes": {raw.relative_to(tmp_path).as_posix(): _sha(raw)},
    }), encoding="utf-8")
    record = _identity_order_audit(tmp_path, audit_source=audit_source, probe_source=probe_source)
    assert record["verified_raw_count"] == 1
    assert record["official_v2_status_preserved"]["unknown_snapshots"] == 1
    raw.write_bytes(b"changed")
    with pytest.raises(ValueError, match="raw hash mismatch"):
        _identity_order_audit(tmp_path, audit_source=audit_source, probe_source=probe_source)
