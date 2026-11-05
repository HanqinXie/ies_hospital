import json
import gzip

import pytest

from tools.reliability_first.collect import _sha
from tools.reliability_first.learning import _find_result, assemble_fit_input


def _fixture(tmp_path):
    run = tmp_path / "run"
    (run / "design").mkdir(parents=True)
    (run / "new_data").mkdir()
    (run / "branches").mkdir()
    plan = [
        {"scenario_id": "a", "group_id": "train_a", "snapshot_minute": 0.0,
         "experiment_role": "pilot_train"},
        {"scenario_id": "b", "group_id": "valid_b", "snapshot_minute": 0.0,
         "experiment_role": "pilot_validation"},
        {"scenario_id": "c", "group_id": "cal_c", "snapshot_minute": 0.0,
         "experiment_role": "pilot_validation"},
        {"scenario_id": "d", "group_id": "train_d", "snapshot_minute": 15.0,
         "experiment_role": "pilot_train"},
    ]
    (run / "design/snapshot_plan_v2.json").write_text(json.dumps(plan), encoding="utf-8")
    (run / "new_data/global_manifest.json").write_text(
        json.dumps({"signature": "registered_manifest"}), encoding="utf-8")
    binding = {"branches.py": "hash"}
    selection = {"plan_sha256": _sha(run / "design/snapshot_plan_v2.json"),
                 "manifest_sha256": _sha(run / "new_data/global_manifest.json"),
                 "branch_source_sha256": binding,
                 "early_indices": [0, 1], "remaining_indices": [2, 3],
                 "pilot_reuse": {"index": 120, "path": "branches/pilot"}}
    (run / "design/branch_collection_v1.json").write_text(json.dumps(selection), encoding="utf-8")
    paths = {}
    receipts = []
    for i in (0, 1, 2):
        row = plan[i]
        path = run / "branches" / f"snapshot_{i:03d}" / "attempt_0001"
        path.mkdir(parents=True)
        paths[i] = path
        (path / "plan.json").write_text(json.dumps({
            "schema": "reliability_first.branch.v1", "entry": row,
            "manifest_signature": "registered_manifest", "source_sha256": binding}),
            encoding="utf-8")
        rows = []
        for j in (0, 1):
            rows.append({"row_id": f"{i}:{j}", "snapshot_id": f"{i}",
                         "scenario_id": row["scenario_id"],
                         "candidate_index": j, "candidate_id": "secure_baseline" if j == 0 else "alternative",
                         "group_id": row["group_id"],
                         "role": "train" if i == 0 else "validation",
                         "loads_kW": [[1.0, 2.0, 3.0]] * 4,
                         "x": [1.0, float(j)], "delta_y": [0.0] * 19 if j == 0 else [None] * 19,
                         "target_mask": [True] * 19 if j == 0 else [False] * 19})
        for name in ("reference_capture.json.gz", "D0_secure.json.gz"):
            with gzip.open(path / name, "wt", encoding="utf-8") as stream:
                json.dump({"summary": {"status": "completed"}}, stream)
        result = dict(schema="reliability_first.branch_result.v1",
                      status="complete", scenario_id=row["scenario_id"],
                      group_id=row["group_id"], snapshot_minute=0.0,
                      role="train" if i == 0 else "validation",
                      D0_passed=True, source_sha256=binding, fit_rows=rows,
                      outcomes=[{"trajectory_file": "D0_secure.json.gz",
                                 "trajectory_sha256": _sha(path / "D0_secure.json.gz")}],
                      raw_trajectory_hashes={name: _sha(path / name) for name in
                          ("reference_capture.json.gz", "D0_secure.json.gz")})
        result_path = path / "BRANCH_RESULT.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        receipts += _receipt(i, path, result_path, result["status"])
    row = plan[3]
    path = run / "branches/snapshot_003/attempt_0001"
    path.mkdir(parents=True)
    paths[3] = path
    (path / "plan.json").write_text(json.dumps({"schema": "reliability_first.branch.v1",
        "entry": row, "manifest_signature": "registered_manifest",
        "source_sha256": binding}), encoding="utf-8")
    raw = path / "reference_capture.json.gz"
    with gzip.open(raw, "wt", encoding="utf-8") as stream:
        json.dump({"identity": {"scenario_id": row["scenario_id"],
                                "group_id": row["group_id"],
                                "manifest_signature": "registered_manifest"},
                   "summary": {"status": "model_constraint_infeasible",
                               "observed_until_minute": 0.0}}, stream)
    (path / "attempts.jsonl").write_text("\n".join([
        json.dumps({"event": "started", "attempt_type": "snapshot_capture",
                    "scenario_id": row["scenario_id"], "minute": 15.0}),
        json.dumps({"event": "finished", "attempt_type": "snapshot_capture",
                    "status": "model_constraint_infeasible", "raw_sha256": _sha(raw)})]) + "\n",
        encoding="utf-8")
    result_path = path / "BRANCH_RESULT.json"
    result_path.write_text(json.dumps(
        dict(status="snapshot_not_reached", scenario_id=row["scenario_id"],
             snapshot_minute=15.0, reference_status="model_constraint_infeasible",
             fit_rows=[], unknown=True)), encoding="utf-8")
    receipts += _receipt(3, path, result_path, "snapshot_not_reached")
    (run / "branches/collection_attempts.jsonl").write_text(
        "\n".join(receipts) + "\n", encoding="utf-8")
    return run, plan, selection, paths


def _receipt(index, attempt, result_path, status):
    stage = "early" if index < 2 else "remaining"
    common = {"index": index, "stage": stage, "attempt_directory": str(attempt)}
    return [json.dumps({"event": "started", **common}),
            json.dumps({"event": "finished", **common, "exit_code": 0,
                        "result_status": status, "result_sha256": _sha(result_path)})]


def test_assembly_keeps_groups_roles_unknown_and_train_only_scale(tmp_path):
    run, _, _, paths = _fixture(tmp_path)
    fit, calibration, audit = assemble_fit_input(run, stage="full")
    assert {r["group_id"] for r in fit["train"]} == {"train_a"}
    assert {r["group_id"] for r in fit["validation"]} == {"valid_b"}
    assert {r["group_id"] for r in calibration["rows"]} == {"cal_c"}
    assert fit["scale_kWh"] == [[2.0, 4.0, 6.0]] * 4
    assert audit["status_counts"]["snapshot_not_reached"] == 1
    assert audit["unknown_target_values_by_partition"]["train"] == 19
    assert (paths[3] / "TERMINAL_PROVENANCE.json").is_file()


def test_learning_ignores_unregistered_attempt_even_when_sorted_first(tmp_path):
    run, plan, selection, paths = _fixture(tmp_path)
    fake = paths[0].parent / "attempt_0000"
    fake.mkdir()
    result = json.loads((paths[0] / "BRANCH_RESULT.json").read_text(encoding="utf-8"))
    result["fit_rows"][0]["x"] = [99.0, 99.0]
    (fake / "BRANCH_RESULT.json").write_text(json.dumps(result), encoding="utf-8")
    path, result = _find_result(run, 0, plan[0], selection,
                                manifest_signature="registered_manifest")
    assert path.parent == paths[0]
    assert result["fit_rows"][0]["x"] == [1.0, 0.0]


def test_learning_rejects_corrupt_raw_trajectory(tmp_path):
    run, _, _, paths = _fixture(tmp_path)
    with (paths[0] / "D0_secure.json.gz").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="raw trajectory"):
        assemble_fit_input(run, stage="full")
