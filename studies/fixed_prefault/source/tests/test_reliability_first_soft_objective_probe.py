"""Small, source-bound second-round opportunity probe."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from tools.control_improvement.ranking import TARGET_NAMES
from tools.integrated_learning.policies import baseline_request
from tools.reliability_first.soft_objective_probe import (
    _adjudicate_interrupted,
    _validate_registration_count,
    classify_paired_targets,
    make_candidate_requests,
    load_saved_execution_snapshot,
    reconstruct_current_context,
    select_train_probe_indices,
    verify_reused_baseline_raw,
    verify_saved_source,
)


RUN = Path(__file__).resolve().parents[1] / "outputs/v43_reliability_first_20261015_run01"


def test_selection_uses_registered_clock_and_group_only():
    plan = json.loads((RUN / "design/snapshot_plan_v2.json").read_text(encoding="utf-8"))
    collection = json.loads((RUN / "design/branch_collection_v1.json").read_text(encoding="utf-8"))
    indices = select_train_probe_indices(plan, collection["early_indices"])
    selected = [plan[i] for i in indices]
    assert len(indices) == len(set(indices)) == 24
    assert all(row["experiment_role"] == "pilot_train" and not row["is_final_blind"] for row in selected)
    assert len(Counter(row["group_id"] for row in selected)) == 12
    assert set(Counter(row["group_id"] for row in selected).values()) == {2}
    assert sum(105 <= row["snapshot_minute"] <= 135 for row in selected) == 9
    assert {row["snapshot_phase_design"] for row in selected} == {
        "preparation", "early_response", "later_response"
    }


def test_secure_baseline_and_six_port_residuals_are_current_requests():
    context = {
        "current_plan_targets_kW": [120., 60., 80., 100., 0., 100.],
        "capacity_GT_kW": [200., 200., 200.],
        "overload_minutes_GT": [10., 0., 0.],
        "grid_import_limit_kW": 350.,
    }
    secure = {"candidate_id": "secure_baseline", "request": baseline_request("secure_milp"),
              "projected_targets_kW": [120., 60., 80., 100., 0., 100.]}
    candidates = make_candidate_requests(context, secure)
    assert candidates[0]["request"] == baseline_request("secure_milp")
    assert [c["candidate_id"] for c in candidates[1:5]] == [
        "security_weight_0p7", "security_weight_2", "security_weight_6", "security_weight_10"
    ]
    assert len(candidates) == 7
    for candidate in candidates[5:]:
        assert candidate["request"]["security"] == baseline_request("secure_milp")["security"]
        assert set(candidate["request"]["targets"]) == {
            "gt_1", "gt_2", "gt_3", "grid_import", "diesel", "absorption_cooling_kW"
        }
        assert all(value >= 0 for value in candidate["request"]["targets"].values())
        assert candidate["request"]["targets"] != dict(zip(candidate["request"]["targets"], secure["projected_targets_kW"]))


def test_reused_baseline_identity_and_hash_are_required():
    manifest = json.loads((RUN / "new_data/global_manifest.json").read_text(encoding="utf-8"))
    plan = json.loads((RUN / "design/snapshot_plan_v2.json").read_text(encoding="utf-8"))
    collection = json.loads((RUN / "design/branch_collection_v1.json").read_text(encoding="utf-8"))
    source = verify_saved_source(RUN, 73, plan[73], manifest, collection["branch_source_sha256"])
    assert len(source["baseline_outcomes"]) == 4
    original = source["baseline_outcomes"]["original"]
    assert original["candidate_id"] == "secure_baseline"
    with pytest.raises(ValueError, match="SHA"):
        verify_reused_baseline_raw(
            source["root"] / original["trajectory_file"], "0" * 64,
            scenario_id=plan[73]["scenario_id"], future_id="original",
            manifest_signature=manifest["signature"], snapshot_minute=plan[73]["snapshot_minute"],
        )


def test_saved_execution_template_is_reset_before_restore():
    manifest = json.loads((RUN / "new_data/global_manifest.json").read_text(encoding="utf-8"))
    plan = json.loads((RUN / "design/snapshot_plan_v2.json").read_text(encoding="utf-8"))
    collection = json.loads((RUN / "design/branch_collection_v1.json").read_text(encoding="utf-8"))
    source = verify_saved_source(RUN, 73, plan[73], manifest, collection["branch_source_sha256"])
    restored = load_saved_execution_snapshot(source, manifest)
    assert restored.binding["scenario_id"] == plan[73]["scenario_id"]
    assert restored.fork().core.time == plan[73]["snapshot_minute"]


def test_current_context_comes_from_current_problem_not_environment():
    from tools.control_improvement.runtime import extract_current_problem, project_current
    manifest = json.loads((RUN / "new_data/global_manifest.json").read_text(encoding="utf-8"))
    plan = json.loads((RUN / "design/snapshot_plan_v2.json").read_text(encoding="utf-8"))
    collection = json.loads((RUN / "design/branch_collection_v1.json").read_text(encoding="utf-8"))
    source = verify_saved_source(RUN, 73, plan[73], manifest, collection["branch_source_sha256"])
    env = load_saved_execution_snapshot(source, manifest).fork()
    env.core.boundary()
    problem = extract_current_problem(env)
    secure = project_current(problem, baseline_request("secure_milp"))
    context = reconstruct_current_context(problem, source["current"]["history"], secure["command"])
    assert context == source["current"]["context"]


def test_priority_deltas_keep_physical_units_and_unknown_masks():
    reference = [0.] * len(TARGET_NAMES)
    actual = reference.copy()
    actual[TARGET_NAMES.index("P1_e_ENS_kWh")] = 2.25
    actual[TARGET_NAMES.index("P2_c_ENS_kWh")] = -1.5
    result = classify_paired_targets(actual, reference)
    assert result["by_priority"] == {"P0": "tie", "P1": "worse", "P2": "better", "P3": "tie"}
    assert result["delta"][TARGET_NAMES.index("P1_e_ENS_kWh")] == 2.25
    mask = [True] * len(TARGET_NAMES)
    mask[TARGET_NAMES.index("P0_h_ENS_kWh")] = False
    actual[TARGET_NAMES.index("P0_h_ENS_kWh")] = None
    result = classify_paired_targets(actual, reference, actual_mask=mask)
    assert result["by_priority"]["P0"] == "unknown"
    assert result["delta"][TARGET_NAMES.index("P0_h_ENS_kWh")] is None


def test_registration_count_rejects_short_selection_even_when_registered_is_24():
    with pytest.raises(ValueError, match="24"):
        _validate_registration_count(list(range(23)), 24)


def test_interrupted_attempt_is_adjudicated_unknown_without_overwrite(tmp_path):
    attempt = tmp_path / "soft_objective_probe/snapshot_002/attempt_0001"
    attempt.mkdir(parents=True)
    raw = attempt / "future_00_candidate_01.json.gz"
    raw.write_bytes(b"partial original bytes")
    (attempt / "attempts.jsonl").write_text(json.dumps({
        "event": "started", "attempt_type": "current_action_future_continuation",
        "index": 2, "candidate_id": "security_weight_0p7", "future_id": "original"
    }) + "\n", encoding="utf-8")
    entry = {"scenario_id": "case", "group_id": "train_group", "snapshot_minute": 105.}
    _adjudicate_interrupted(tmp_path, tmp_path / "soft_objective_probe", 2, entry,
                            {"probe_source_sha256": "bound"})
    result = json.loads((attempt / "PROBE_RESULT.json").read_text(encoding="utf-8"))
    assert result["status"] == "unknown"
    assert result["raw_trajectory_hashes"][raw.relative_to(tmp_path).as_posix()]
    assert raw.read_bytes() == b"partial original bytes"
    assert "current_action_future_continuation_started" in result["attempt_counts"]
    with pytest.raises(FileExistsError):
        _adjudicate_interrupted(tmp_path, tmp_path / "soft_objective_probe", 2, entry,
                                {"probe_source_sha256": "bound"})
