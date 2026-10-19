"""A partial runner summary must not consume the one-time audit receipt."""
from __future__ import annotations

import json

import pytest


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path, *, recorded=2, terminal=2, in_flight=False):
    run = tmp_path / "run"
    folder = run / "selection"
    _write(folder / "registered_plan.json",
           {"signature": "plan", "registered_pair_count": 2})
    _write(folder / "results/summary_0001.json",
           {"plan_signature": "plan", "registered_pair_count": 2,
            "recorded_pair_count": recorded, "terminal_pair_count": terminal})
    journal = folder / "results/attempts.jsonl"
    rows = [{"event": "started", "scenario_id": "s1", "policy_id": "p1",
             "attempt": "attempt_0001"}]
    if not in_flight:
        rows.append({"event": "finished", "scenario_id": "s1", "policy_id": "p1",
                     "attempt": "attempt_0001"})
    journal.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return run


def test_audit_refuses_partial_summary_before_one_time_receipt(tmp_path, monkeypatch):
    from tools.policy_search import stage_ops

    run = _fixture(tmp_path, recorded=1, terminal=1)
    monkeypatch.setattr(stage_ops, "audit_batch", lambda *args, **kwargs:
                        pytest.fail("partial batch must not enter audit_batch"))
    with pytest.raises(ValueError, match="terminal.*registered|counts"):
        stage_ops.operate(run, "selection", "audit")
    assert not (run / "selection/AUDIT.json").exists()


def test_audit_refuses_inflight_started_record_before_one_time_receipt(tmp_path, monkeypatch):
    from tools.policy_search import stage_ops

    run = _fixture(tmp_path, in_flight=True)
    monkeypatch.setattr(stage_ops, "audit_batch", lambda *args, **kwargs:
                        pytest.fail("in-flight batch must not enter audit_batch"))
    with pytest.raises(ValueError, match="in-flight"):
        stage_ops.operate(run, "selection", "audit")
    assert not (run / "selection/AUDIT.json").exists()


def test_terminal_balanced_journal_reaches_audit(tmp_path, monkeypatch):
    from tools.policy_search import stage_ops

    run = _fixture(tmp_path)
    calls = []
    def fake_audit(*args, **kwargs):
        calls.append(args)
        return {"passed": True, "registered_pair_count": 2,
                "unknown_primary_pair_ids": []}
    monkeypatch.setattr(stage_ops, "audit_batch", fake_audit)
    result = stage_ops.operate(run, "selection", "audit")
    assert result["audit_passed"] is True
    assert len(calls) == 1


def test_development_run_requires_sensitivity_preregistration(tmp_path, monkeypatch):
    from tools.policy_search import stage_ops

    run = tmp_path / "run"
    _write(run / "development/registered_plan.json", {"signature": "plan"})
    monkeypatch.setattr(stage_ops, "run_batch", lambda *args, **kwargs:
                        pytest.fail("runner must not begin without sensitivity preregistration"))
    with pytest.raises(FileNotFoundError):
        stage_ops.operate(run, "development", "run")


def test_development_run_checks_sensitivity_preregistration_first(tmp_path, monkeypatch):
    from tools.policy_search import stage_ops, sensitivity

    run = tmp_path / "run"
    _write(run / "development/registered_plan.json", {"signature": "plan"})
    calls = []
    monkeypatch.setattr(sensitivity, "verify_preregistration", lambda value:
                        calls.append(("verify", value)))
    monkeypatch.setattr(stage_ops, "run_batch", lambda *args, **kwargs:
                        calls.append(("run", args)) or {"status": "ok"})
    assert stage_ops.operate(run, "development", "run") == {"status": "ok"}
    assert [step for step, _ in calls] == ["verify", "run"]
