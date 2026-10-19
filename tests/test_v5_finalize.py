"""The unattended handoff must never turn a partial run into a final report."""

import json

import pytest

from tools.policy_search.finalize import (
    Step, check_terminal_development, finalize, run_logged_step,
)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(run, *, terminal=2, exit_code=None, journal_complete=True):
    plan = {"signature": "frozen-signature", "scenario_ids": ["a", "b"],
            "policy_specs": [{"policy_id": "secure_milp"}],
            "registered_pair_count": 2}
    _write(run / "development/registered_plan.json", plan)
    _write(run / "development/results/summary_0001.json",
           {"plan_signature": "frozen-signature", "registered_pair_count": 2,
            "recorded_pair_count": terminal, "terminal_pair_count": terminal,
            "results": [{"scenario_id": sid, "policy_id": "secure_milp",
                         "plan_signature": "frozen-signature", "terminal": True}
                        for sid in ("a", "b")[:terminal]]})
    events = []
    for sid in ("a", "b")[:terminal]:
        events.append({"event": "started", "scenario_id": sid,
                       "policy_id": "secure_milp", "attempt": "attempt_0001"})
        if journal_complete:
            events.append({"event": "finished", "scenario_id": sid,
                           "policy_id": "secure_milp", "attempt": "attempt_0001"})
    journal = run / "development/results/attempts.jsonl"
    journal.write_text("\n".join(json.dumps(event) for event in events) + "\n",
                       encoding="utf-8")
    if exit_code is not None:
        _write(run / "logs/v5_development_run_actual.command.json",
               {"name": "v5_development_run_actual", "exit_code": exit_code,
                "command": ["python", "-B", "-m",
                            "tools.policy_search.stage_ops", str(run),
                            "development", "run"]})


def test_development_handoff_waits_for_exit_receipt_and_all_registered_pairs(tmp_path):
    _fixture(tmp_path, terminal=2)
    assert check_terminal_development(tmp_path, required_pairs=2) is None

    _write(tmp_path / "logs/v5_development_run_actual.command.json",
           {"name": "v5_development_run_actual", "exit_code": 0,
            "command": ["python", "-B", "-m",
                        "tools.policy_search.stage_ops", str(tmp_path),
                        "development", "run"]})
    assert check_terminal_development(tmp_path, required_pairs=2).name == "summary_0001.json"


def test_development_handoff_rejects_partial_and_nonzero_run(tmp_path):
    _fixture(tmp_path, terminal=1, exit_code=0)
    with pytest.raises(ValueError, match="terminal"):
        check_terminal_development(tmp_path, required_pairs=2)

    _write(tmp_path / "logs/v5_development_run_actual.command.json",
           {"name": "v5_development_run_actual", "exit_code": 2,
            "command": ["python", "-B", "-m",
                        "tools.policy_search.stage_ops", str(tmp_path),
                        "development", "run"]})
    with pytest.raises(ValueError, match="exit code 2"):
        check_terminal_development(tmp_path, required_pairs=2)


def test_development_handoff_rejects_inflight_journal(tmp_path):
    _fixture(tmp_path, exit_code=0, journal_complete=False)
    with pytest.raises(ValueError, match="journal"):
        check_terminal_development(tmp_path, required_pairs=2)


def test_logged_step_refuses_unlogged_artifact_and_skips_valid_receipt(tmp_path, monkeypatch):
    artifact = tmp_path / "development/AUDIT.json"
    step = Step("v5_development_audit_actual", ("python", "-B", "-m", "audit"),
                "development/AUDIT.json", "audit")
    _write(artifact, {"passed": True, "registered_pair_count": 2,
                      "plan_signature": "frozen-signature"})
    with pytest.raises(ValueError, match="without a successful command receipt"):
        run_logged_step(tmp_path, step, required_pairs=2,
                        plan_signature="frozen-signature")

    _write(tmp_path / "logs/v5_development_audit_actual.command.json",
           {"name": step.name, "command": list(step.command), "exit_code": 0})
    monkeypatch.setattr("tools.policy_search.finalize.subprocess.run",
                        lambda *args, **kwargs: pytest.fail("step ran twice"))
    assert run_logged_step(tmp_path, step, required_pairs=2,
                           plan_signature="frozen-signature") == "already_complete"


def test_finalizer_records_failure_without_claiming_completion(tmp_path, monkeypatch):
    run, v4 = tmp_path / "run", tmp_path / "v4"
    run.mkdir()
    v4.mkdir()
    _write(run / "development/registered_plan.json", {"signature": "frozen-signature"})
    monkeypatch.setattr("tools.policy_search.finalize.check_terminal_development",
                        lambda run: run / "development/results/summary_0001.json")
    monkeypatch.setattr("tools.policy_search.finalize.final_steps",
                        lambda run, v4: (Step("failing_step", ("python",), None, "pytest"),))
    monkeypatch.setattr("tools.policy_search.finalize.run_logged_step",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("real exit 2")))
    monkeypatch.setattr("tools.policy_search.finalize.ROOT", tmp_path)
    report_module = tmp_path / "tools/policy_search/final_report.py"
    report_module.parent.mkdir(parents=True)
    report_module.write_text("# stub", encoding="utf-8")

    with pytest.raises(RuntimeError, match="real exit 2"):
        finalize(run, v4, poll_seconds=0.01, max_wait_hours=0.01)
    status = json.loads((run / "review/FINALIZATION_STATUS.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["current_step"] == "failing_step"
    assert "real exit 2" in status["last_error"]
    assert "failing_step" not in status["completed_steps"]
