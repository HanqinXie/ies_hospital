"""Small v8 bundle guards: selection, exact raw inventory and saved-raw proof."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import csv
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.dataset_manifest import digest


POLICIES = ("secure_milp", "priority_milp")
CLASSES = ("N2", "N4", "L1", "L2", "L3", "L4a", "L4b")
FOUR_CELLS = ("N4", "L3", "L4a", "L4b")


def test_readme_maps_workspace_supplement_report_links_to_portable_paths():
    from tools.four_scenarios.deepening_package import _readme

    supplement = {
        "supplement_id": "v8_four_scenarios_fault_cross_20261016_run01",
        "source_run_relative_to_workspace":
            "outputs/v8_four_scenarios_fault_cross_20261016_run01",
        "source_analysis_relative_to_workspace":
            "outputs/v8_four_scenarios_fault_cross_20261016_run01/"
            "analysis_v2_device_status/FAULT_CROSS_TRAJECTORY_RESULTS.csv",
        "final_analysis_file":
            "supplements/v8_four_scenarios_fault_cross_20261016_run01/run/"
            "analysis_v2_device_status/FAULT_CROSS_TRAJECTORY_RESULTS.csv",
    }
    contents = _readme([supplement]).decode("utf-8")
    assert "../v8_four_scenarios_fault_cross_20261016_run01/new_data/global_manifest.json" in contents
    assert "../supplements/v8_four_scenarios_fault_cross_20261016_run01/evidence/global_manifest.json" in contents
    assert "../v8_four_scenarios_fault_cross_20261016_run01/main/AUDIT.json" in contents
    assert "../supplements/v8_four_scenarios_fault_cross_20261016_run01/evidence/AUDIT.json" in contents
    assert "../v8_four_scenarios_fault_cross_20261016_run01/analysis_v2_device_status/" in contents
    assert "../supplements/v8_four_scenarios_fault_cross_20261016_run01/run/analysis_v2_device_status/" in contents


def test_readme_maps_external_clockmatched_analysis_into_supplement():
    from tools.four_scenarios.deepening_package import _readme

    supplement = {
        "supplement_id": "v8_four_scenarios_clockmatched_20261016_run01",
        "source_run_relative_to_workspace":
            "outputs/v8_four_scenarios_clockmatched_20261016_run01",
        "source_analysis_relative_to_workspace":
            "outputs/v8_four_scenarios_deepening_20261016_run01/analysis/"
            "CLOCKMATCHED_N4_TRAJECTORY_RESULTS.csv",
        "final_analysis_file":
            "supplements/v8_four_scenarios_clockmatched_20261016_run01/run/"
            "analysis/CLOCKMATCHED_N4_TRAJECTORY_RESULTS.csv",
    }
    contents = _readme([supplement]).decode("utf-8")
    assert "outputs/v8_four_scenarios_deepening_20261016_run01/analysis/" in contents
    assert "../supplements/v8_four_scenarios_clockmatched_20261016_run01/run/analysis/" in contents
    assert "Other saved analysis tables" not in contents


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _selection_fixture():
    scenes = [{"id": "cell_" + category, "analysis_class": category,
               "root_family_id": "root_1", "is_final_blind": False}
              for category in CLASSES]
    manifest = {"scenarios": scenes, "matched_four_cell_groups": [{
        "design_cell_id": "group_1", "root_family_id": "root_1",
        "scene_ids": {name: "cell_" + name for name in ("N4", "L3", "L4a", "L4b")}}]}
    plan = {"scenario_ids": [x["id"] for x in scenes],
            "policy_specs": [{"policy_id": p} for p in POLICIES]}
    selected = [{"scenario_id": scene["id"], "policy_id": p,
                 "expected_raw_sha256": "a" * 64}
                for scene in scenes for p in POLICIES]
    analysis = [{"scenario_id": scene["id"], "policy_id": p,
                 "full_P0_failure": "1" if scene["analysis_class"] == "L1"
                 and p == "secure_milp" else "0", "full_full_horizon": "True"}
                for scene in scenes for p in POLICIES]
    return manifest, plan, {"selected_pairs": selected}, analysis


def test_selection_requires_both_policies_each_class_and_one_four_cell_group():
    from tools.four_scenarios.deepening_package import validate_selection

    manifest, plan, selection, analysis = _selection_fixture()
    roles = validate_selection(selection, manifest, plan, analysis)
    assert roles["selected_raw_count"] == 14
    assert roles["four_cell_group_id"] == "group_1"
    assert roles["p0_failed_count"] == 1
    selection["selected_pairs"] = [x for x in selection["selected_pairs"]
                                   if not (x["scenario_id"] == "cell_L3" and
                                           x["policy_id"] == "priority_milp")]
    with pytest.raises(ValueError, match="both policies"):
        validate_selection(selection, manifest, plan, analysis)


def test_selection_rejects_missing_failure_or_unexpected_sha():
    from tools.four_scenarios.deepening_package import validate_selection

    manifest, plan, selection, analysis = _selection_fixture()
    analysis = [{**row, "full_P0_failure": "0"} for row in analysis]
    with pytest.raises(ValueError, match="failed and nonfailed"):
        validate_selection(selection, manifest, plan, analysis)
    analysis[0]["full_P0_failure"] = "1"
    selection["selected_pairs"][0]["expected_raw_sha256"] = "not_sha"
    with pytest.raises(ValueError, match="expected_raw_sha256"):
        validate_selection(selection, manifest, plan, analysis)


def test_external_raw_index_includes_retry_and_detects_changed_raw(tmp_path):
    from tools.four_scenarios.deepening_package import index_raw_attempts

    run = tmp_path / "v8_fake"
    sid, pid = "scene", "secure_milp"
    pair = run / "main/results/pairs" / digest([sid, pid])
    attempts = []
    for index in (1, 2):
        folder = pair / f"attempt_{index:04d}"
        folder.mkdir(parents=True)
        raw = folder / "trajectory.json.gz"
        with gzip.open(raw, "wt", encoding="utf-8") as stream:
            json.dump({"attempt": index}, stream)
        result = folder / "result.json"
        _write(result, {"scenario_id": sid, "policy_id": pid,
                        "trajectory_path": raw.name,
                        "trajectory_sha256": _sha(raw), "status": "completed"})
        attempts.append({"attempt": folder.name, "raw_sha256": _sha(raw),
                         "result_sha256": _sha(result)})
    ledger = {(sid, pid): {"scenario_id": sid, "policy_id": pid,
                          "latest_result_sha256": attempts[-1]["result_sha256"],
                          "attempts": attempts}}
    rows = index_raw_attempts(run, ledger)
    assert len(rows) == 2
    assert [x["latest_audited"] for x in rows] == [False, True]
    (pair / "attempt_0001/trajectory.json.gz").write_bytes(b"changed")
    with pytest.raises(ValueError, match="raw SHA"):
        index_raw_attempts(run, ledger)


def test_write_once_zip_and_extracted_manifest_detect_tamper(tmp_path):
    from tools.four_scenarios.deepening_package import create_archive, verify_integrity

    archive = tmp_path / "v8.zip"
    receipt = create_archive(archive, {"evidence/one.csv": b"a,b\n1,2\n"},
                             {"kind": "v8_selected_raw"})
    assert receipt["member_count"] == 2
    assert archive.with_name("v8.zip.sha256").is_file()
    with pytest.raises(FileExistsError):
        create_archive(archive, {"evidence/one.csv": b"replacement"}, {})
    extracted = tmp_path / "unpacked"
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(extracted)
    assert verify_integrity(extracted)["kind"] == "v8_selected_raw"
    (extracted / "evidence/one.csv").write_bytes(b"changed")
    with pytest.raises(ValueError, match="differs"):
        verify_integrity(extracted)


def test_analysis_subset_recomputed_from_saved_subsegments():
    from tools.four_scenarios.deepening_package import compare_analysis_subset

    payload = {"segments": [
        {"start_minute": 0, "end_minute": 15,
         "unserved_kW": [[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]],
         "classification": {"p0_guarantee_failure": False}},
        {"start_minute": 15, "end_minute": 30,
         "unserved_kW": [[2, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 4]],
         "classification": {"p0_guarantee_failure": True}},
    ], "events": [{"kind": "protection_trip", "time_minute": 20,
                   "device": "GT_2"}],
        "summary": {"observed_until_minute": 30, "requested_end_minute": 30}}
    row = {"full_P0_failure": "1", "full_P0_failure_minutes": "15",
           "full_P0_e_ENS_kWh": "0.5", "full_P3_c_ENS_kWh": "1.0",
           "GT_protection_trip_count_observed": "1"}
    assert compare_analysis_subset(payload, row)["analysis_subset_match"] is True
    row["full_P0_e_ENS_kWh"] = "0"
    with pytest.raises(ValueError, match="full_P0_e_ENS_kWh"):
        compare_analysis_subset(payload, row)


def test_build_embeds_selected_raw_but_indexes_all_pairs_and_inputs(tmp_path):
    from tools.four_scenarios.deepening_package import (
        build_bundle, verify_integrity, write_repro_selection)
    from tools.four_scenarios.package import PHYSICAL_INPUTS, ANALYSIS_FILES

    root = tmp_path / "workspace"
    source = root / "src/hospital_ies/fixture.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n", encoding="utf-8")
    design_source = root / "tools/four_scenarios/deepening_design.py"
    design_source.parent.mkdir(parents=True)
    design_source.write_text("x = 2\n", encoding="utf-8")
    (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    files = []
    for name in PHYSICAL_INPUTS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        files.append({"path": name, "bytes": path.stat().st_size, "sha256": _sha(path)})
    run = root / "outputs/v8_four_scenarios_deepening_fixture"
    scenes = [{"id": "cell_" + category, "analysis_class": category,
               "root_family_id": "root_1", "is_final_blind": False,
               "experiment_role": "train", "exogenous_events": [] if category in ("N2", "N4")
               else [{"kind": "device_fault"}]}
              for category in CLASSES]
    scenes.append({"id": "extra_l1", "analysis_class": "L1",
                   "root_family_id": "root_2", "is_final_blind": False,
                   "experiment_role": "train", "exogenous_events": [{"kind": "device_fault"}]})
    proposal = run / "design/SOURCE_PROPOSAL.json"
    _write(proposal, {"source": "synthetic"})
    manifest_path = run / "new_data/global_manifest.json"
    _write(manifest_path, {"manifest_version": "four_scenarios.v8.fixture",
                           "files": files, "runtime": {"python": "3.10"},
                           "execution_contract": {}, "scenarios": scenes,
                           "four_scenario_proposal_file_sha256": _sha(proposal),
                           "four_scenario_source_sha256": {
                               "tools/four_scenarios/deepening_design.py": _sha(design_source)},
                           "matched_four_cell_groups": [{"design_cell_id": "group_1",
                               "root_family_id": "root_1",
                               "scene_ids": {name: "cell_" + name for name in FOUR_CELLS}}]})
    plan_path = run / "main/registered_plan.json"
    plan = {"manifest_sha256": _sha(manifest_path), "manifest_path": str(manifest_path),
            "scenario_ids": [scene["id"] for scene in scenes],
            "policy_specs": [{"policy_id": p} for p in POLICIES],
            "source_sha256": {"src/hospital_ies/fixture.py": _sha(source)},
            "registered_pair_count": 16, "phase": "train", "signature": "fixture",
            "final_blind_opened": False}
    _write(plan_path, plan)
    rows, ledger, selected = [], [], []
    for scene in scenes:
        for pid in POLICIES:
            sid = scene["id"]
            failed = sid == "cell_L1" and pid == "secure_milp"
            folder = run / "main/results/pairs" / digest([sid, pid]) / "attempt_0001"
            folder.mkdir(parents=True)
            raw = folder / "trajectory.json.gz"
            p0 = 2 if failed else 0
            payload = {"scenario": scene,
                       "identity": {"scenario_id": sid, "policy_id": pid},
                       "summary": {"status": "completed", "observed_until_minute": 600,
                                   "requested_end_minute": 600},
                       "segments": [{"start_minute": 0, "end_minute": 600,
                                     "unserved_kW": [[p0, 0, 0], [0, 0, 0],
                                                     [0, 0, 0], [0, 0, 0]],
                                     "classification": {"p0_guarantee_failure": failed}}],
                       "events": []}
            with gzip.open(raw, "wt", encoding="utf-8") as stream:
                json.dump(payload, stream)
            result = folder / "result.json"
            score = {"full_horizon": True, "targets": [0] * 19}
            _write(result, {"scenario_id": sid, "policy_id": pid,
                            "status": "completed", "terminal": True,
                            "binding": {"passed": True}, "plan_signature": "fixture",
                            "trajectory_path": raw.name, "trajectory_sha256": _sha(raw),
                            "primary_score": score, "full_observation": score})
            ledger.append({"scenario_id": sid, "policy_id": pid,
                           "status": "completed", "terminal": True,
                           "latest_result_sha256": _sha(result),
                           "attempts": [{"attempt": "attempt_0001",
                                         "raw_sha256": _sha(raw),
                                         "result_sha256": _sha(result)}]})
            rows.append({"scenario_id": sid, "policy_id": pid,
                         "full_full_horizon": "True", "full_P0_failure": "1" if failed else "0",
                         "full_P0_failure_minutes": "600" if failed else "0",
                         "full_P0_e_ENS_kWh": "20" if failed else "0",
                         "GT_protection_trip_count_observed": "0"})
            if sid != "extra_l1":
                selected.append({"scenario_id": sid, "policy_id": pid,
                                 "expected_raw_sha256": _sha(raw)})
    audit_path = run / "main/AUDIT.json"
    _write(audit_path, {"passed": True, "matched_initial_state_and_stream": True,
                        "manifest_sha256": _sha(manifest_path), "plan_sha256": _sha(plan_path),
                        "registered_pair_count": 16, "terminal_pair_count": 16,
                        "recomputed_pair_count": 16, "pair_ledger": ledger})
    analysis_dir = run / "analysis"
    analysis_dir.mkdir()
    with (analysis_dir / "TRAJECTORY_RESULTS.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for name in ANALYSIS_FILES:
        path = analysis_dir / name
        if not path.exists():
            path.write_text("{}" if name.endswith(".json") else "column\n", encoding="utf-8")
    figure = run / "figures/final/one.csv"
    figure.parent.mkdir(parents=True)
    figure.write_text("minute,kW\n0,1\n", encoding="utf-8")
    report = run / "FOUR_SCENARIOS_DEEPENING_REPORT.md"
    report.write_text("# Synthetic report\n", encoding="utf-8")
    (run / "RUN_LEDGER.jsonl").write_text('{"event":"finished"}\n', encoding="utf-8")
    selection = run / "review/REPRO_SELECTION.json"
    selected_receipt = write_repro_selection(run, selection, workspace_root=root)
    assert selected_receipt["selected_raw_count"] == 14
    assert {(item["scenario_id"], item["policy_id"])
            for item in json.loads(selection.read_text(encoding="utf-8"))["selected_pairs"]} == {
            (item["scenario_id"], item["policy_id"]) for item in selected}
    archive = run / "delivery/V8_REPRO.zip"
    with pytest.raises(FileNotFoundError, match="command ledger"):
        build_bundle(run, archive, selection, report, workspace_root=root)
    (run / "review/COMMAND_LEDGER.md").write_text("# Synthetic command ledger\n",
                                                   encoding="utf-8")
    receipt = build_bundle(run, archive, selection, report, workspace_root=root)
    assert receipt["selected_raw_count"] == 14
    assert receipt["external_raw_indexed"] == 16
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(extracted)
    package = verify_integrity(extracted)
    assert package["registered_pair_count"] == 16
    assert package["selected_raw_count"] == 14
    assert (extracted / "run/analysis/TRAJECTORY_RESULTS.csv").is_file()
    assert (extracted / "run/FOUR_SCENARIOS_DEEPENING_REPORT.md").is_file()
    assert (extracted / "configs/p1b_execution_research.json").is_file()
    assert (extracted / "run/review/COMMAND_LEDGER.md").is_file()


def test_supplement_keeps_independent_audit_analysis_and_small_paired_raw(tmp_path):
    from tools.four_scenarios.deepening_package import (
        prepare_supplement, verify_supplement, write_supplement_selection)

    root = tmp_path / "workspace"
    source = root / "src/hospital_ies/fixture.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n", encoding="utf-8")
    run = root / "outputs/v8_four_scenarios_deepening_fixture"
    supplement = run / "supplements/clockmatched_n4"
    scenes = [{"id": sid, "analysis_class": "N4", "root_family_id": "root_1",
               "is_final_blind": False, "experiment_role": "train"}
              for sid in ("chosen", "external")]
    manifest_path = supplement / "new_data/global_manifest.json"
    _write(manifest_path, {"manifest_version": "four_scenarios.v8.supplement.fixture",
                           "scenarios": scenes, "files": []})
    plan_path = supplement / "main/registered_plan.json"
    _write(plan_path, {"manifest_sha256": _sha(manifest_path),
                       "scenario_ids": [x["id"] for x in scenes],
                       "policy_specs": [{"policy_id": p} for p in POLICIES],
                       "registered_pair_count": 4, "phase": "train",
                       "signature": "supplement-fixture",
                       "source_sha256": {"src/hospital_ies/fixture.py": _sha(source)}})
    ledger, analysis, selected = [], [], []
    for scene in scenes:
        for policy in POLICIES:
            sid = scene["id"]
            folder = supplement / "main/results/pairs" / digest([sid, policy]) / "attempt_0001"
            folder.mkdir(parents=True)
            raw = folder / "trajectory.json.gz"
            payload = {"scenario": scene,
                       "identity": {"scenario_id": sid, "policy_id": policy},
                       "summary": {"status": "completed", "observed_until_minute": 600,
                                   "requested_end_minute": 600},
                       "segments": [{"start_minute": 0, "end_minute": 600,
                                     "unserved_kW": [[0, 0, 0]] * 4,
                                     "classification": {"p0_guarantee_failure": False}}],
                       "events": []}
            with gzip.open(raw, "wt", encoding="utf-8") as stream:
                json.dump(payload, stream)
            result = folder / "result.json"
            _write(result, {"scenario_id": sid, "policy_id": policy,
                            "status": "completed", "terminal": True,
                            "binding": {"passed": True},
                            "plan_signature": "supplement-fixture",
                            "trajectory_path": raw.name, "trajectory_sha256": _sha(raw)})
            ledger.append({"scenario_id": sid, "policy_id": policy,
                           "status": "completed", "terminal": True,
                           "latest_result_sha256": _sha(result),
                           "attempts": [{"attempt": "attempt_0001",
                                         "raw_sha256": _sha(raw),
                                         "result_sha256": _sha(result)}]})
            analysis.append({"scenario_id": sid, "policy_id": policy,
                             "full_full_horizon": "True", "full_P0_failure": "0",
                             "full_P0_e_ENS_kWh": "0"})
            if sid == "chosen":
                selected.append({"scenario_id": sid, "policy_id": policy,
                                 "expected_raw_sha256": _sha(raw)})
    audit = supplement / "main/AUDIT.json"
    _write(audit, {"passed": True, "matched_initial_state_and_stream": True,
                   "manifest_sha256": _sha(manifest_path), "plan_sha256": _sha(plan_path),
                   "registered_pair_count": 4, "terminal_pair_count": 4,
                   "recomputed_pair_count": 4, "pair_ledger": ledger})
    analysis_path = supplement / "analysis_v2_device_status/FAULT_CROSS_TRAJECTORY_RESULTS.csv"
    analysis_path.parent.mkdir(parents=True)
    with analysis_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(analysis[0]))
        writer.writeheader()
        writer.writerows(analysis)
    _write(supplement / "analysis_v2_device_status/FAULT_CROSS_SUMMARY.json",
           {"final": True})
    _write(supplement / "analysis/INITIAL_SUMMARY.json", {"final": False})
    (supplement / "RUN_LEDGER.jsonl").write_text('{"event":"done"}\n', encoding="utf-8")
    selection_path = supplement / "review/REPRO_SELECTION.json"
    selection_receipt = write_supplement_selection(
        supplement, analysis_path, selection_path, workspace_root=root)
    assert selection_receipt["selected_raw_count"] == 2
    assert json.loads(selection_path.read_text(encoding="utf-8"))["selected_pairs"] == selected

    entries, receipt = prepare_supplement(supplement, workspace_root=root)
    assert receipt["supplement_id"] == "clockmatched_n4"
    assert receipt["registered_pair_count"] == 4
    assert receipt["selected_raw_count"] == 2
    assert receipt["external_raw_file_count"] == 4
    assert "supplements/clockmatched_n4/evidence/AUDIT.json" in entries
    assert receipt["final_analysis_file"] == (
        "supplements/clockmatched_n4/run/analysis_v2_device_status/"
        "FAULT_CROSS_TRAJECTORY_RESULTS.csv")
    assert receipt["final_analysis_file"] in entries
    assert "supplements/clockmatched_n4/run/analysis_v2_device_status/FAULT_CROSS_SUMMARY.json" in entries
    assert "supplements/clockmatched_n4/run/analysis/INITIAL_SUMMARY.json" in entries
    assert sum(name.endswith("trajectory.json.gz") for name in entries) == 2
    index = json.loads(entries["supplements/clockmatched_n4/EXTERNAL_RAW_SHA256_INDEX.json"])
    assert sum(row["included_in_package"] for row in index["rows"]) == 2
    extracted = tmp_path / "extracted"
    extracted_source = extracted / "src/hospital_ies/fixture.py"
    extracted_source.parent.mkdir(parents=True)
    extracted_source.write_bytes(source.read_bytes())
    for name, content in entries.items():
        target = extracted / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.read_bytes() if isinstance(content, Path) else content)
    checked = verify_supplement(extracted, receipt, env=None)
    assert checked["selected_raw_rechecked"] == 2
    (extracted / "supplements/clockmatched_n4/evidence/AUDIT.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA"):
        verify_supplement(extracted, receipt, env=None)
    folder = supplement / "main/results/pairs" / digest(["chosen", "secure_milp"])
    (folder / "attempt_0001/trajectory.json.gz").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw SHA"):
        prepare_supplement(supplement, workspace_root=root)


def test_command_ledger_keeps_failed_register_and_verifies_log_hashes(tmp_path):
    from tools.four_scenarios.deepening_package import write_command_ledger

    main = tmp_path / "outputs/v8_four_scenarios_deepening_fixture"
    clock = tmp_path / "outputs/v8_four_scenarios_clockmatched_fixture"
    for run, name, code, stderr_text in (
            (main, "register_main", 0, ""),
            (clock, "register_clockmatched_bad", 1,
             "missing required keyword-only arguments: scenario_ids\n"),
            (clock, "register_clockmatched_good", 0, "")):
        logs = run / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stdout = logs / f"{name}.stdout.log"
        stderr = logs / f"{name}.stderr.log"
        stdout.write_text("ok\n" if code == 0 else "", encoding="utf-8")
        stderr.write_text(stderr_text, encoding="utf-8")
        entries = [
            {"event": "started", "run_id": name, "name": "register_clockmatched",
             "command": ["python", "-B", "-c", "register_batch(...)"],
             "stdout": str(stdout), "stderr": str(stderr)},
            {"event": "finished", "run_id": name, "exit_code": code,
             "stdout_sha256": _sha(stdout), "stderr_sha256": _sha(stderr)}]
        with (run / "RUN_LEDGER.jsonl").open("a", encoding="utf-8") as stream:
            for entry in entries:
                stream.write(json.dumps(entry) + "\n")
    output = main / "review/COMMAND_LEDGER.md"
    receipt = write_command_ledger(
        {"main": main, "clockmatched": clock}, output,
        extra_records=[{"scope": "fault_cross", "stage": "selection",
                        "command": "deepening_package select-supplement ...",
                        "exit_code": 0,
                        "evidence": "review/REPRO_SELECTION.json SHA abc"}])
    content = output.read_text(encoding="utf-8")
    assert receipt["recorded_command_count"] == 4
    assert "register_clockmatched_bad" in content
    assert "exit 1" in content
    assert "missing required keyword-only arguments" in content
    assert "register_clockmatched_good" in content
    assert "fault_cross" in content
    with pytest.raises(FileExistsError):
        write_command_ledger({"main": main}, output)
    (clock / "logs/register_clockmatched_bad.stderr.log").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="log SHA"):
        write_command_ledger({"clockmatched": clock}, main / "review/SECOND.md")
