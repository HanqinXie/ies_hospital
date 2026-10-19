"""Lightweight freeze/holdout protocol over existing verifier and registration."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from hospital_ies.dataset_manifest import digest


def _module():
    return importlib.import_module("tools.state_dispatch.freeze")


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_freeze_protocol_entrypoints_exist():
    try:
        module = _module()
    except ModuleNotFoundError:
        module = None
    assert module is not None, "Policy-freeze protocol is not implemented"
    assert callable(module.freeze_policy) and callable(module.register_confirmation)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    module = _module()
    source = tmp_path / "policy_v2.py"
    source.write_text("frozen controller", encoding="utf-8")
    pref = tmp_path / "preference.json"
    _write(pref, {"P0_first": True})
    manifest = {"signature": "manifest_sig", "policy_search_final_12_backgrounds_unopened": True,
        "ids_by_cohort": {"development": ["dev"], "holdout": ["h2", "h1"]},
        "scenarios": [{"id": sid, "analysis_cohort": "development" if sid == "dev" else "holdout",
            "experiment_role": "development" if sid == "dev" else "confirmation", "is_final_blind": False}
            for sid in ("dev", "h1", "h2")]}
    mp, pp, ap = (tmp_path / name for name in ("manifest.json", "dev_plan.json", "dev_audit.json"))
    _write(mp, manifest)
    selected = {"policy_id": "state_guarded_spread", "kind": "state_dispatch",
                "config": {"cooling_guard_ratio": .8}}
    specs = [{"policy_id": "secure_milp", "kind": "secure"},
             {"policy_id": "priority_milp", "kind": "priority"}, selected]
    source_map = lambda: {"tools/state_dispatch/policy_v2.py": module._sha(source)}
    plan = {"schema": module.runner.SCHEMA, "signature": "plan_sig", "phase": "development",
        "manifest_path": str(mp), "manifest_sha256": module._sha(mp),
        "manifest_signature": manifest["signature"], "preference_path": str(pref),
        "preference_sha256": module._sha(pref), "scenario_ids": ["dev"], "policy_specs": specs,
        "registered_pair_count": 3, "source_sha256": source_map(),
        "simulator_hash": "physics", "execution_contract_id": "contract",
        "physical_input_inventory_sha256": "inventory"}
    _write(pp, plan)
    summary = tmp_path / "summary.json"
    _write(summary, {"plan_signature": plan["signature"]})
    ledger = []
    for spec in specs:
        raw = tmp_path / spec["policy_id"] / "trajectory.json.gz"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b"existing saved raw fixture, no physics")
        ledger.append({"scenario_id": "dev", "policy_id": spec["policy_id"], "terminal": True,
            "score_recomputed": True, "binding_passed": True, "primary_full_horizon": True,
            "full_observation_horizon": True, "attempts": [{"attempt": "attempt_0001",
                "raw_path": str(raw), "raw_sha256": module._sha(raw)}]})
    audit = {"schema": module.runner.AUDIT_SCHEMA, "phase": "development", "passed": True,
        "errors": [], "matched_initial_state_and_stream": True,
        "plan_path": str(pp), "plan_sha256": module._sha(pp), "plan_signature": plan["signature"],
        "manifest_sha256": module._sha(mp), "summary_path": str(summary),
        "summary_sha256": module._sha(summary), "registered_pair_count": 3,
        "terminal_pair_count": 3, "recomputed_pair_count": 3,
        "unknown_primary_pair_ids": [], "unknown_full_pair_ids": [], "pair_ledger": ledger}
    _write(ap, audit)
    monkeypatch.setattr(module.runner, "verify_batch", lambda path: (
        json.loads(Path(path).read_text()), json.loads(mp.read_text())))
    monkeypatch.setattr(module.runner, "_source_binding", source_map)
    calls = []

    def register(manifest_path, plan_path, **kwargs):
        calls.append((manifest_path, plan_path, kwargs))
        # The real runner serializes paths in its frozen JSON plan.
        result = {"signature": "confirmation_sig", **{
            key: str(value) if isinstance(value, Path) else value for key, value in kwargs.items()}}
        _write(Path(plan_path), result)
        return result

    monkeypatch.setattr(module.runner, "register_batch", register)
    return dict(module=module, root=tmp_path, manifest=mp, plan=pp, audit=ap,
                preference=pref, selected=selected, source=source, calls=calls)


def _freeze(f):
    return f["module"].freeze_policy(f["root"], f["selected"], f["plan"], f["audit"],
                                      "current-state benefit and measured tradeoffs", f["root"] / "freeze.json")


def test_complete_development_freezes_once_and_confirmation_uses_only_manifest_holdout(fixture):
    f = fixture
    receipt = _freeze(f)
    assert receipt["signature"] == digest({k: v for k, v in receipt.items() if k != "signature"})
    assert receipt["selected_spec"] == f["selected"]
    with pytest.raises(FileExistsError):
        _freeze(f)
    plan = f["module"].register_confirmation(f["root"] / "freeze.json", f["root"] / "confirm.json",
                                            f["manifest"], f["preference"])
    args = f["calls"][0][2]
    assert args["scenario_ids"] == ["h2", "h1"]
    assert args["phase"] == "confirmation"
    assert args["policy_specs"] == [
        {"policy_id": "secure_milp", "kind": "secure"},
        {"policy_id": "priority_milp", "kind": "priority"}, f["selected"]]
    assert plan["signature"] == "confirmation_sig"
    assert not (f["root"] / "holdout_results").exists()


def test_incomplete_audit_and_unregistered_selected_spec_do_not_create_receipt(fixture):
    f = fixture
    original = json.loads(f["audit"].read_text())
    for change in ({"passed": False}, {"recomputed_pair_count": 2},
                   {"unknown_full_pair_ids": ["dev/state_guarded_spread"]}):
        _write(f["audit"], {**original, **change})
        with pytest.raises(ValueError):
            _freeze(f)
        assert not (f["root"] / "freeze.json").exists()
    _write(f["audit"], original)
    f["selected"]["config"]["cooling_guard_ratio"] = .9
    with pytest.raises(ValueError, match="selected|Selected"):
        _freeze(f)
    assert not (f["root"] / "freeze.json").exists()


def test_missing_receipt_source_or_selected_drift_refuses_confirmation(fixture):
    f = fixture
    with pytest.raises((FileNotFoundError, ValueError)):
        f["module"].register_confirmation(f["root"] / "missing.json", f["root"] / "confirm.json",
                                          f["manifest"], f["preference"])
    receipt = _freeze(f)
    path = f["root"] / "freeze.json"
    altered = copy.deepcopy(receipt)
    altered["selected_spec"]["config"]["cooling_guard_ratio"] = .9
    altered["signature"] = digest({k: v for k, v in altered.items() if k != "signature"})
    _write(path, altered)
    with pytest.raises(ValueError, match="selected|Selected"):
        f["module"].register_confirmation(path, f["root"] / "confirm.json", f["manifest"], f["preference"])
    _write(path, receipt)
    f["source"].write_text("source drift", encoding="utf-8")
    with pytest.raises(ValueError, match="source|Source"):
        f["module"].register_confirmation(path, f["root"] / "confirm.json", f["manifest"], f["preference"])
    assert not f["calls"]


def test_cli_selects_exact_development_spec_without_reading_holdout_results(fixture, monkeypatch, capsys):
    f = fixture
    assert f["module"].main(["freeze", "--run", str(f["root"]),
        "--selected-policy-id", f["selected"]["policy_id"], "--development-plan", str(f["plan"]),
        "--development-audit", str(f["audit"]), "--rationale", "observed tradeoff",
        "--out", str(f["root"] / "freeze.json")]) == 0
    assert json.loads(capsys.readouterr().out)["selected_policy_id"] == f["selected"]["policy_id"]
    assert f["module"].main(["register-confirmation", "--receipt", str(f["root"] / "freeze.json"),
        "--plan", str(f["root"] / "confirm.json"), "--manifest", str(f["manifest"]),
        "--preference", str(f["preference"])]) == 0
    assert len(f["calls"]) == 1
