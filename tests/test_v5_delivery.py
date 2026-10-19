"""Focused evidence tests for the v5 portable delivery builder."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from tools.policy_search.delivery import build_delivery, compile_ledger


def _json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _digest(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(data).hexdigest()


def _score(failure, minutes, *, full=True):
    targets = [failure, minutes] + [0.0] * 17
    if failure is None:
        targets[2:] = [None] * 17
    return {"full_horizon": full, "targets": targets,
            "observed_minutes": 360.0 if full else 55.0}


def _fixture(tmp_path):
    run = tmp_path / "v5"
    batch = run / "search" / "generation_1"
    plan = {"schema": "policy_search.matched_full_feedback.v1", "signature": "plan-a",
            "phase": "development", "scenario_ids": ["case-a", "case-b"],
            "policy_specs": [{"policy_id": "secure_milp", "kind": "secure"},
                             {"policy_id": "candidate", "kind": "feedback"},
                             {"policy_id": "never_run", "kind": "feedback"}],
            "registered_pair_count": 6, "source_sha256": {},
            "model_bindings": {}}
    _json(batch / "registered_plan.json", plan)
    results = batch / "results"
    journal = []
    for sid, pid, score in [
        ("case-a", "secure_milp", _score(0, 0.0)),
        ("case-a", "candidate", _score(1, 20.0)),
        ("case-b", "secure_milp", _score(0, 0.0)),
        ("case-b", "candidate", _score(None, None, full=False)),
    ]:
        pair = results / "pairs" / _digest([sid, pid])
        if sid == "case-a" and pid == "candidate":
            first = pair / "attempt_0001"
            _json(first / "result.json", {"scenario_id": sid, "policy_id": pid,
                                          "status": "runner_exception", "terminal": False,
                                          "error_path": "error.json", "plan_signature": "plan-a"})
            _json(first / "error.json", {"error_type": "RuntimeError",
                                         "message": "first try failed"})
            journal += [{"event": "started", "scenario_id": sid, "policy_id": pid,
                         "attempt": "attempt_0001"},
                        {"event": "finished", "scenario_id": sid, "policy_id": pid,
                         "attempt": "attempt_0001", "status": "runner_exception"}]
            attempt_name = "attempt_0002"
        else:
            attempt_name = "attempt_0001"
        attempt = pair / attempt_name
        raw = attempt / "trajectory.json.gz"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes((sid + pid).encode())
        row = {"scenario_id": sid, "policy_id": pid,
               "status": "completed" if score["full_horizon"] else "model_constraint_infeasible",
               "terminal": True, "plan_signature": "plan-a",
               "trajectory_path": raw.name,
               "trajectory_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
               "primary_score": score, "full_observation": score,
               "binding": {"passed": True}, "command_counts": {"requested": 1}}
        _json(attempt / "result.json", row)
        journal += [{"event": "started", "scenario_id": sid, "policy_id": pid,
                     "attempt": attempt_name},
                    {"event": "finished", "scenario_id": sid, "policy_id": pid,
                     "attempt": attempt_name, "status": row["status"]}]
    # An interrupted attempt has neither result nor terminal journal event.
    (results / "pairs" / _digest(["case-a", "never_run"]) /
     "attempt_0001").mkdir(parents=True)
    journal.append({"event": "started", "scenario_id": "case-a",
                    "policy_id": "never_run", "attempt": "attempt_0001"})
    (results / "attempts.jsonl").write_text(
        "\n".join(json.dumps(v) for v in journal) + "\n", encoding="utf-8")
    _json(run / "logs" / "failed.command.json",
          {"name": "failed", "command": ["python", "bad.py"], "exit_code": 1})
    _json(run / "logs" / "passed.command.json",
          {"name": "passed", "command": ["python", "good.py"], "exit_code": 0})
    (run / "logs" / "failed.stderr.log").write_text("bad\n", encoding="utf-8")
    _json(run / "new_data" / "global_manifest.json", {"schema": "fixture"})
    return run


def test_ledger_keeps_unknown_retry_pending_and_p0_case(tmp_path):
    run = _fixture(tmp_path)
    delivery = compile_ledger(run)
    batch = delivery["pair_ledger"]["batches"][0]
    assert batch["registered_pair_count"] == 6
    assert len(batch["pairs"]) == 6
    rows = {(p["scenario_id"], p["policy_id"]): p for p in batch["pairs"]}
    assert len(rows[("case-a", "candidate")]["attempts"]) == 2
    assert rows[("case-a", "candidate")]["attempts"][0]["status"] == "runner_exception"
    assert rows[("case-a", "never_run")]["status"] == "attempt_started_no_result"
    assert rows[("case-b", "never_run")]["status"] == "registered_not_attempted"
    observed = delivery["observability"]["batches"][0]["policies"]["candidate"]
    primary = observed["primary_score"]
    assert primary["registered_case_count"] == 2
    assert primary["complete_paired_case_count"] == 1
    assert primary["observable_paired_count_by_target"]["P0_failure"] == 1
    assert primary["new_P0_failure_case_ids"] == ["case-a"]
    assert primary["worst_P0_failure_minute_cases"][0]["scenario_id"] == "case-a"
    assert {r["exit_code"] for r in delivery["command_receipts"]["receipts"]} == {0, 1}
    assert len(delivery["raw_trajectory_index"]["files"]) == 4


def test_package_is_relative_compact_and_hash_verified(tmp_path):
    run = _fixture(tmp_path)
    report = run / "POLICY_SEARCH_REPORT.md"
    report.write_text("# Evidence, including unknown cases\n", encoding="utf-8")
    result = build_delivery(run, report)
    archive = Path(result["review_zip_path"])
    assert archive.is_file()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["review_zip_sha256"]
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert "POLICY_SEARCH_REPORT.md" in names
        assert "review/RAW_TRAJECTORY_SHA256.json" in names
        assert "review/REVIEW_ZIP_MANIFEST.json" in names
        assert "source/tools/policy_search/finalize.py" in names
        assert "source/tools/policy_search/final_report.py" in names
        assert "source/tests/test_v5_finalize.py" in names
        assert not any(name.endswith("trajectory.json.gz") for name in names)
        assert all(not Path(name).is_absolute() and ".." not in Path(name).parts
                   for name in names)
        manifest = json.loads(zf.read("review/REVIEW_ZIP_MANIFEST.json"))
        for entry in manifest["entries"]:
            info = zf.getinfo(entry["archive_path"])
            data = zf.read(entry["archive_path"])
            assert info.CRC == entry["crc32"]
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]


def test_package_carries_compact_audit_stage_and_sensitivity_receipts(tmp_path):
    run = _fixture(tmp_path)
    batch = run / "search" / "generation_1"
    expected = {
        "search/generation_1/AUDIT.json",
        "search/generation_1/ANALYSIS_PREFERENCE_V2.json",
        "search/generation_1/SCORE_RECHECK.json",
        "search/generation_1/STAGE_REGISTRATION.json",
        "search/alternative_modes/ALTERNATIVE_REGISTRATION.json",
        "sensitivity/SENSITIVITY_BY_CONDITION_PRESSURE.json",
    }
    for name in expected:
        _json(run / name, {"receipt": name})
    report = run / "POLICY_SEARCH_REPORT.md"
    report.write_text("# Audited v5 delivery\n", encoding="utf-8")
    archive = Path(build_delivery(run, report)["review_zip_path"])
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert expected <= names
        for name in expected:
            assert json.loads(zf.read(name)) == {"receipt": name}


def test_no_report_or_changed_raw_cannot_be_silently_packaged(tmp_path):
    run = _fixture(tmp_path)
    with pytest.raises(FileNotFoundError):
        build_delivery(run)
    raw = next((run / "search").rglob("trajectory.json.gz"))
    raw.write_bytes(b"changed")
    with pytest.raises(ValueError, match="trajectory.*SHA|SHA.*trajectory"):
        compile_ledger(run)
