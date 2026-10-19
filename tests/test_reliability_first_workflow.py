"""A bounded, resumable v4 workflow must not launch an unreviewed long run."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tools.reliability_first.workflow import (Step, Workflow, WorkflowError,
                                              _fit_input_complete, _package_complete,
                                              _probe_action_future_started,
                                              _probe_complete, _report_complete, build_commands,
                                              compute_budget_counts)


def _setup(tmp_path):
    root = tmp_path
    run = root / "outputs" / "v4"
    (run / "new_data").mkdir(parents=True)
    (run / "new_data/global_manifest.json").write_text(json.dumps({
        "signature": "sig", "scenarios": [{"id": "a"}], "policies": ["priority_milp", "secure_milp"],
        "p2b_shards": [{"id": "shard_00", "scenario_ids": ["a"]}]}), encoding="utf-8")
    source = root / "source.py"
    source.write_text("x = 1\n", encoding="utf-8")
    return root, run, source


def test_registered_command_order_uses_actual_clis_and_full_60(tmp_path):
    root, run, _ = _setup(tmp_path)
    commands = build_commands(root, run, python_executable="python")
    names = list(commands)
    assert names.index("collect_early") < names.index("nn_early")
    assert names.index("nn_early") < names.index("collect_remaining")
    assert names.index("collect_remaining") < names.index("nn_full")
    assert names.index("calibrate_nn") < names.index("register_development")
    assert names.index("run_development") < names.index("render_report") < names.index("package") < names.index("audit")
    assert commands["nn_full"][3] == "tools.reliability_first.neural"
    assert "--limit" not in commands["register_development"]
    assert "--calibration-file" in commands["register_development"]
    assert "--closed-loop-dir" in commands["audit"]
    required = commands["audit"][commands["audit"].index("--required-policies") + 1:]
    assert required == ("secure_milp", "secure_current_rule", "nn_ungated", "nn_gated", "tree_gated")


def test_step_receipts_skip_completed_and_resume_without_overwrite(tmp_path):
    root, run, source = _setup(tmp_path)
    marker = run / "done.json"
    calls = []

    def fake_command(argv, stdout, stderr, *, cwd, timeout_seconds):
        calls.append(list(argv))
        marker.write_text("{}", encoding="utf-8")
        stdout.write("done\n")
        return 0

    workflow = Workflow(root, run, source_paths=[source],
                        start_utc=datetime.now(timezone.utc), command_runner=fake_command)
    steps = [Step("already", ("not-run",), lambda: True),
             Step("create", ("fake", "create"), marker.is_file, fresh_paths=(marker,))]
    first = workflow.run_plan(steps, wait_existing=False)
    assert first["executed_steps"] == ["create"] and calls == [["fake", "create"]]
    second = workflow.run_plan(steps, wait_existing=False)
    assert second["executed_steps"] == [] and len(calls) == 1
    journal = (run / "workflow/attempts.jsonl").read_text(encoding="utf-8")
    assert '"exit_code": 0' in journal


def test_source_drift_and_wall_budget_stop_before_command(tmp_path):
    root, run, source = _setup(tmp_path)
    calls = []
    workflow = Workflow(root, run, source_paths=[source], start_utc=datetime.now(timezone.utc),
                        command_runner=lambda *args: calls.append(args) or 0)
    workflow.run_plan([], wait_existing=False)
    source.write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(WorkflowError, match="source binding"):
        workflow.run_plan([Step("bad", ("fake",), lambda: False)], wait_existing=False)
    assert not calls
    expired = Workflow(root, run, source_paths=[source],
                       start_utc=datetime.now(timezone.utc) - timedelta(hours=73),
                       command_runner=lambda *args: calls.append(args) or 0)
    with pytest.raises(WorkflowError, match="72-hour"):
        expired.run_plan([Step("bad", ("fake",), lambda: False)], wait_existing=False)
    assert not calls


def test_existing_background_process_is_waited_without_duplicate_launch(tmp_path):
    root, run, source = _setup(tmp_path)
    (run / "branches").mkdir()
    launch = {"pid": 123, "launched_utc": datetime.now(timezone.utc).isoformat()}
    (run / "branches/early_runner_launch.json").write_text(json.dumps(launch), encoding="utf-8")
    alive = iter((True, False))
    sleeps = []
    workflow = Workflow(root, run, source_paths=[source], start_utc=datetime.now(timezone.utc),
                        process_alive=lambda pid, launched: next(alive),
                        sleep=lambda seconds: sleeps.append(seconds), poll_seconds=1)
    workflow.run_plan([], wait_existing=True)
    assert sleeps == [1]


def test_finite_resumable_stage_retries_then_stops_on_budget(tmp_path):
    root, run, source = _setup(tmp_path)
    calls = []

    def fake_command(argv, stdout, stderr, *, cwd, timeout_seconds):
        calls.append(list(argv))
        return 0

    workflow = Workflow(root, run, source_paths=[source], start_utc=datetime.now(timezone.utc),
                        command_runner=fake_command)
    result = workflow.run_plan([Step("retry", ("fake",), lambda: len(calls) == 2, max_calls=2)],
                               wait_existing=False)
    assert result["executed_steps"] == ["retry", "retry"]
    assert (run / "workflow/retry.attempt_0002.stdout.log").is_file()
    with pytest.raises(WorkflowError, match="Attempt budget stop"):
        workflow.run_plan([Step("over", ("fake",), lambda: False,
                                projected_counts={"fit_started": 181})], wait_existing=False)
    assert len(calls) == 2


def test_fit_input_gate_binds_frozen_collection_design_and_branch_source(tmp_path):
    root, run, _ = _setup(tmp_path)
    (run / "design").mkdir()
    (run / "learning/full_v1").mkdir(parents=True)
    manifest = run / "new_data/global_manifest.json"
    plan = run / "design/snapshot_plan_v2.json"
    selection = run / "design/branch_collection_v1.json"
    plan.write_text("[]", encoding="utf-8")
    selection.write_text(json.dumps({"branch_source_sha256": {"branches.py": "abc"}}), encoding="utf-8")
    import hashlib
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    binding = {"stage": "full", "manifest_sha256": digest(manifest), "manifest_signature": "sig",
               "snapshot_plan_sha256": digest(plan), "collection_design_sha256": digest(selection),
               "branch_source_sha256": {"branches.py": "abc"}}
    folder = run / "learning/full_v1"
    (folder / "fit_input.json").write_text(json.dumps({"source_binding": binding}), encoding="utf-8")
    (folder / "calibration_input.json").write_text(json.dumps({"source_binding": binding}), encoding="utf-8")
    (folder / "SOURCE_DATA_AUDIT.json").write_text(json.dumps({
        "final_12_backgrounds_excluded": True, "rows_by_partition": {"train": 1, "validation": 1}}),
        encoding="utf-8")
    assert _fit_input_complete(run, "full")
    binding["branch_source_sha256"] = {"branches.py": "wrong"}
    (folder / "fit_input.json").write_text(json.dumps({"source_binding": binding}), encoding="utf-8")
    (folder / "calibration_input.json").write_text(json.dumps({"source_binding": binding}), encoding="utf-8")
    assert not _fit_input_complete(run, "full")


def test_report_and_package_gates_require_new_artifacts_and_bound_receipts(tmp_path):
    root, run, _ = _setup(tmp_path)
    folder = run / "report"
    folder.mkdir()
    for name in ("RELIABILITY_FIRST_REPORT.md", "PAIRED_SUMMARY.json", "PAIRED_CASES.json",
                 "SENSITIVITY_BY_CONDITION.json", "REPORT_PROVENANCE.json"):
        (folder / name).write_text('{"complete_60_case_development":true}', encoding="utf-8")
    assert not _report_complete(run)
    for name in ("ACTION_COVERAGE.json", "BRANCH_INVENTORY.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    assert _report_complete(run)
    review = run / "review"
    review.mkdir()
    zip_path = review / "RELIABILITY_FIRST_REVIEW_PACKAGE.zip"
    zip_path.write_bytes(b"zip")
    index = review / "EXTERNAL_FILE_SHA256.jsonl"
    index.write_text("{}\n", encoding="utf-8")
    manifest = review / "PACKAGE_MANIFEST.json"
    manifest.write_text("{}", encoding="utf-8")
    import hashlib
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = {"schema": "reliability_first.package_verification.v1",
               "zip_path": zip_path.relative_to(root).as_posix(), "zip_bytes": 3,
               "zip_sha256": digest(zip_path), "crc_passed": True, "files": 2,
               "included_byte_hashes_checked": 2,
               "external_index_path": index.relative_to(root).as_posix(),
               "external_index_sha256": digest(index),
               "package_manifest_path": manifest.relative_to(root).as_posix(),
               "package_manifest_sha256": digest(manifest), "sampled_raw_paths": ["a", "b"]}
    receipt_path = review / "PACKAGE_VERIFICATION.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert _package_complete(root, run)
    receipt["external_index_sha256"] = "wrong"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert not _package_complete(root, run)


def test_fit_budget_counts_actual_tree_estimators_not_one_invocation(tmp_path):
    _, run, _ = _setup(tmp_path)
    folder = run / "learning/early_tree_fit01"
    folder.mkdir(parents=True)
    (folder / "fit_started.json").write_text(json.dumps({"requested_device": "cpu"}), encoding="utf-8")
    (folder / "FIT_REPORT.json").write_text(json.dumps({"actual_fits": 19}), encoding="utf-8")
    counts = compute_budget_counts(run)
    assert counts["fit_started"] == 19


def test_probe_continuation_starts_are_charged_even_if_unfinished_or_unknown(tmp_path):
    _, run, _ = _setup(tmp_path)
    folder = run / "soft_objective_probe/snapshot_003/attempt_0001"
    folder.mkdir(parents=True)
    (folder / "attempts.jsonl").write_text("\n".join(json.dumps(row) for row in (
        {"event": "started", "attempt_type": "current_action_future_continuation"},
        {"event": "finished", "attempt_type": "current_action_future_continuation", "status": "unknown"},
        {"event": "started", "attempt_type": "current_action_future_continuation"},
        {"event": "started", "attempt_type": "projection_query"},
    )) + "\n", encoding="utf-8")
    v2 = run / "soft_objective_probe_v2/snapshot_003/attempt_0001"
    v2.mkdir(parents=True)
    (v2 / "attempts.jsonl").write_text(json.dumps({
        "event": "started", "attempt_type": "current_action_future_continuation"}) + "\n",
        encoding="utf-8")
    assert _probe_action_future_started(run) == 3
    assert compute_budget_counts(run)["action_future_started"] == 3


def test_probe_gate_retains_explicit_terminal_unknown_but_rejects_unattempted(tmp_path):
    _, run, _ = _setup(tmp_path)
    design = run / "design/soft_objective_probe_v2.json"
    design.parent.mkdir()
    selection = run / "design/branch_collection_v1.json"
    selection.write_text('{"early_indices":[1,2],"branch_source_sha256":{"branches.py":"abc"}}', encoding="utf-8")
    snapshot_plan = run / "design/snapshot_plan_v2.json"
    snapshot_plan.write_text("[]", encoding="utf-8")
    import hashlib
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    from tools.reliability_first import workflow as workflow_module
    source = Path(workflow_module.__file__).with_name("soft_objective_probe.py")
    design.write_text(json.dumps({"selected_snapshot_indices": [1, 2],
                                  "registered_snapshots": 2,
                                  "manifest_sha256": digest(run / "new_data/global_manifest.json"),
                                  "snapshot_plan_sha256": digest(snapshot_plan),
                                  "branch_collection_design_sha256": digest(selection),
                                  "branch_source_sha256": {"branches.py": "abc"}}), encoding="utf-8")
    summary = run / "soft_objective_probe_v2/PROBE_SUMMARY.json"
    summary.parent.mkdir()
    complete = {"status": "complete", "registered_snapshots": 2, "completed_snapshots": 1,
                "unknown_snapshots": 1, "not_attempted_snapshots": 0, "interrupted_snapshots": 0,
                "source_binding": {"probe_source_sha256": digest(source),
                                   "design_sha256": digest(design),
                                   "manifest_sha256": digest(run / "new_data/global_manifest.json"),
                                   "snapshot_plan_sha256": digest(snapshot_plan),
                                   "branch_collection_design_sha256": digest(selection),
                                   "branch_source_sha256": {"branches.py": "abc"}}}
    complete["results"] = []
    for index, status in ((1, "complete"), (2, "unknown")):
        folder = run / f"soft_objective_probe_v2/snapshot_{index:03d}/attempt_0001"
        folder.mkdir(parents=True)
        result = folder / "PROBE_RESULT.json"
        result.write_text(json.dumps({"index": index, "status": status,
                                      "source_binding": complete["source_binding"]}), encoding="utf-8")
        complete["results"].append({"index": index, "status": status,
                                    "result_path": result.relative_to(run).as_posix(),
                                    "result_sha256": digest(result)})
    summary.write_text(json.dumps(complete), encoding="utf-8")
    # An official terminal unknown is retained, but its saved raw needs a
    # separate source-bound adjudication before dependent learning can start.
    assert not _probe_complete(run)
    raw_hashes = {}
    for name in ("secure.json.gz", "candidate.json.gz"):
        raw = result.parent / name
        raw.write_bytes(name.encode("ascii"))
        raw_hashes[raw.relative_to(run).as_posix()] = digest(raw)
    unknown = json.loads(result.read_text(encoding="utf-8"))
    unknown["raw_trajectory_hashes"] = raw_hashes
    unknown["source_result_sha256"] = "registered-source-sha"
    result.write_text(json.dumps(unknown), encoding="utf-8")
    complete["results"][-1]["result_sha256"] = digest(result)
    complete["raw_trajectory_hashes"] = raw_hashes
    summary.write_text(json.dumps(complete), encoding="utf-8")
    plan = result.parent / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    repo = Path(workflow_module.__file__).resolve().parents[2]
    bound_sources = {
        "audit_source_sha256": digest(repo / "tools/reliability_first/soft_objective_probe_audit.py"),
        "window_targets_source_sha256": digest(repo / "tools/control_improvement/branches.py"),
        "window_outcome_source_sha256": digest(repo / "tools/integrated_learning/truth.py"),
        "diagnostics_source_sha256": digest(repo / "tools/reliability_first/diagnostics.py"),
        "energy_fee_source_sha256": digest(repo / "tools/integrated_experiment/energy_metrics.py"),
        "target_names_source_sha256": digest(repo / "tools/control_improvement/ranking.py"),
        "physical_state_source_sha256": digest(repo / "src/hospital_ies/resilience.py"),
        "initial_state_serialization_source_sha256": digest(repo / "src/hospital_ies/dataset_export.py"),
        "energy_tariff_source_sha256": digest(repo / "src/hospital_ies/model.py"),
    }
    sidecar = run / "soft_objective_probe_v2/IDENTITY_ORDER_AUDIT_v1.json"
    adjudication = {
        "schema": "reliability_first.soft_probe_identity_order_audit.v1",
        "status": "complete", "derived_from_saved_raw_only": True, "new_rollout_attempts": 0,
        "official_v2_status_preserved": {"completed_snapshots": 1, "unknown_snapshots": 1},
        "unknown_snapshot_indices_examined": [2], "affected_snapshot_indices": [2],
        "verified_new_raw_count": 1, "verified_secure_raw_count": 1,
        "order_only_verified_pair_count": 1, "unresolved_pair_count": 0,
        "raw_trajectory_hashes": dict(raw_hashes),
        "source_binding": complete["source_binding"] | bound_sources | {
            "probe_summary_sha256": digest(summary),
            "result_sha256_by_index": {"2": digest(result)},
            "attempt_plan_sha256_by_index": {"2": digest(plan)},
            "source_result_sha256_by_index": {"2": "registered-source-sha"},
        },
    }
    sidecar.write_text(json.dumps(adjudication), encoding="utf-8")
    assert _probe_complete(run)
    adjudication["raw_trajectory_hashes"][next(iter(raw_hashes))] = "wrong"
    sidecar.write_text(json.dumps(adjudication), encoding="utf-8")
    assert not _probe_complete(run)
    adjudication["raw_trajectory_hashes"] = dict(raw_hashes)
    sidecar.write_text(json.dumps(adjudication), encoding="utf-8")
    assert _probe_complete(run)
    result.write_text("{}", encoding="utf-8")
    assert not _probe_complete(run)
    result.write_text(json.dumps({"index": 2, "status": "unknown",
                                  "source_binding": complete["source_binding"]}), encoding="utf-8")
    complete["not_attempted_snapshots"] = 1
    summary.write_text(json.dumps(complete), encoding="utf-8")
    assert not _probe_complete(run)
    complete["not_attempted_snapshots"] = 0
    complete["source_binding"]["design_sha256"] = "wrong"
    summary.write_text(json.dumps(complete), encoding="utf-8")
    assert not _probe_complete(run)
