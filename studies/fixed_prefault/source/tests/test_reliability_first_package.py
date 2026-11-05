"""Portable v4 evidence package: real raw samples, external hashes, no overwrite."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from tools.reliability_first.audit import audit_review_package
from tools.reliability_first.package import build_review_package


def _json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _gzip_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(value, stream)


def _fixture(tmp_path):
    root = tmp_path
    run = root / "outputs" / "v4_run"
    _json(run / "new_data/global_manifest.json", {"signature": "frozen", "scenarios": []})
    _json(run / "design/source_proposal.json", {"source": "new"})
    _json(run / "design/snapshot_plan_v2.json", [])
    report = root / "RELIABILITY_FIRST_REPORT.md"
    report.write_text("actual negative or positive finding\n", encoding="utf-8")
    paired = run / "closed_loop" / "final_report" / "PAIRED_SUMMARY.json"
    sensitivity = run / "closed_loop" / "final_report" / "SENSITIVITY_BY_CONDITION.json"
    _json(paired, {"case_denominator": 1})
    _json(sensitivity, {"grid": "observed"})
    _json(run / "learning" / "nn01" / "metadata.json",
          {"schema": "reliability_first.gpu_paired_action_value.v1"})
    _json(run / "learning" / "nn01" / "FIT_REPORT.json",
          {"actual_fits": 1, "actual_train_device": "cuda:0", "optimizer_updates": 4})
    (run / "learning" / "nn01" / "fit_inputs.npz").write_bytes(b"inputs")
    (run / "learning" / "nn01" / "model.pt").write_bytes(b"weights")
    branch = run / "branches" / "snapshot_000" / "attempt_0001"
    _json(branch / "D0_comparison.json", {"passed": True})
    _json(branch / "BRANCH_RESULT.json", {"status": "complete", "D0_passed": True})
    _gzip_json(branch / "D0_secure.json.gz", {"summary": {"scenario_id": "s"}, "rows": [1]})
    _gzip_json(branch / "future_00_candidate_01.json.gz", {"bulk": True})
    development = run / "closed_loop" / "development" / "runs"
    _json(development / "registered_plan.json",
          {"phase": "development", "manifest_signature": "frozen", "final_blind_opened": False,
           "scenario_ids": ["s"], "policy_specs": [{"policy_id": "secure_milp"}, {"policy_id": "nn"}]})
    for index, policy in enumerate(("secure_milp", "nn")):
        attempt = development / "pairs" / f"pair_{index}" / "attempt_0001"
        _gzip_json(attempt / "trajectory.json.gz", {"summary": {"scenario_id": "s", "policy_id": policy},
                                                    "rows": [1]})
        _json(attempt / "result.json", {"scenario_id": "s", "policy_id": policy,
                                        "status": "completed", "terminal": True,
                                        "trajectory_path": "trajectory.json.gz"})
    _json(root / "tools" / "reliability_first" / "policy.py", {"source": "test"})
    return root, run, report, paired, sensitivity, development


def _build(parts):
    root, run, report, paired, sensitivity, development = parts
    return build_review_package(root, run, report_path=report, paired_summary_path=paired,
                                sensitivity_path=sensitivity, development_dir=development)


def test_package_has_relative_paths_verified_members_and_real_raw_samples(tmp_path):
    parts = _fixture(tmp_path)
    receipt = _build(parts)
    root, run, *_ = parts
    zip_path = run / "review/RELIABILITY_FIRST_REVIEW_PACKAGE.zip"
    receipt_path = run / "review/PACKAGE_VERIFICATION.json"
    assert receipt["crc_passed"] and receipt["included_byte_hashes_checked"] == receipt["files"]
    assert audit_review_package(root, zip_path, receipt_path)["status"] == "passed"
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert all(not name.startswith("/") and ":" not in name for name in names)
        assert any(name.endswith("D0_secure.json.gz") for name in names)
        assert sum(name.endswith("trajectory.json.gz") for name in names) == 2
        assert not any(name.endswith("future_00_candidate_01.json.gz") for name in names)
        index = next(name for name in names if name.endswith("EXTERNAL_FILE_SHA256.jsonl"))
        rows = [json.loads(line) for line in archive.read(index).decode().splitlines()]
        assert any(row["path"].endswith("future_00_candidate_01.json.gz") for row in rows)
        assert all(not row["path"].startswith("/") for row in rows)


def test_required_raw_or_report_absence_rejects_publication(tmp_path):
    parts = _fixture(tmp_path)
    root, run, report, *_ = parts
    report.unlink()
    with pytest.raises(FileNotFoundError):
        _build(parts)
    assert not (run / "review/RELIABILITY_FIRST_REVIEW_PACKAGE.zip").exists()


def test_package_never_overwrites(tmp_path):
    parts = _fixture(tmp_path)
    _build(parts)
    with pytest.raises(FileExistsError):
        _build(parts)


def test_optional_soft_objective_probe_records_are_included_and_raw_is_indexed(tmp_path):
    parts = _fixture(tmp_path)
    root, run, *_ = parts
    probes = [run / "soft_objective_probe", run / "soft_objective_probe_v2"]
    evidence = {
        "PROBE_SUMMARY.json": {"registered": 24, "unknown": 1},
        "registered_plan.json": {"source": "train_only"},
        "status.json": {"phase": "complete"},
        "attempt_0001/receipt.json": {"exit_code": 0},
    }
    for probe in probes:
        for name, value in evidence.items():
            _json(probe / name, value)
    raw = probes[1] / "attempt_0001/future_00_candidate_01.json.gz"
    _gzip_json(raw, {"summary": {"scenario_id": "train_01"}, "rows": [1]})
    launcher = run / "run_reliability_first_unattended.ps1"
    launcher.write_bytes(b"\xef\xbb\xbf# frozen launcher\n")
    launcher_receipt = run / "unattended_launcher/attempt_0001/PROBE_DECISION.json"
    _json(launcher_receipt, {"route": "continue_separately_registered_v1_nn_tree_closed_loop"})
    receipt = _build(parts)
    assert receipt["excluded_external_files_indexed"] >= 1
    archive_path = run / "review/RELIABILITY_FIRST_REVIEW_PACKAGE.zip"
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        for probe in probes:
            for name in evidence:
                relative = (probe / name).relative_to(root).as_posix()
                assert relative in names
                assert json.loads(archive.read(relative)) == evidence[name]
        assert launcher.relative_to(root).as_posix() in names
        assert launcher_receipt.relative_to(root).as_posix() in names
        raw_relative = raw.relative_to(root).as_posix()
        assert raw_relative not in names
        index_path = next(name for name in names if name.endswith("EXTERNAL_FILE_SHA256.jsonl"))
        index = [json.loads(line) for line in archive.read(index_path).decode().splitlines()]
        raw_entries = [row for row in index if row["path"] == raw_relative]
        assert len(raw_entries) == 1
        assert raw_entries[0]["sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()
        assert raw_entries[0]["bytes"] == raw.stat().st_size
