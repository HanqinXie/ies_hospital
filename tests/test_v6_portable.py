"""Portable v6 evidence packages must work outside the original workspace."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from tools.policy_redesign.portable import (
    build_global_review_bundle,
    build_reproduction_bundle,
    select_v5_examples,
    select_v6_matched_pair,
)
from tools.policy_redesign import portable
from hospital_ies.dataset_manifest import digest


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "outputs/v5_policy_search_20261015_run01"


def test_fixed_v5_examples_cover_real_review_counterexamples():
    examples = select_v5_examples(V5)
    assert len(examples) == 10
    assert {item["category"] for item in examples} == {
        "strict_guard_microdifference", "nn_p0_harm", "diesel_and_recovery",
        "normal", "unknown_grid0",
    }
    assert {item["status"] for item in examples if item["category"] == "unknown_grid0"} == {
        "model_constraint_infeasible"
    }
    assert all(item["is_final_blind"] is False for item in examples)
    assert all(item["raw_path"].is_file() and item["result_path"].is_file()
               for item in examples)


def test_twelve_raw_design_keeps_eight_historical_mechanism_cases():
    examples = portable.select_v5_core_examples(V5)
    assert len(examples) == 8
    assert {item["category"] for item in examples} == {
        "strict_guard_microdifference", "nn_p0_harm",
        "diesel_and_recovery", "normal",
    }
    for category in {item["category"] for item in examples}:
        assert len([item for item in examples if item["category"] == category]) == 2


def test_global_review_has_all_nonraw_evidence_and_external_raw_index(tmp_path):
    run = tmp_path / "v6_run"
    (run / "review").mkdir(parents=True)
    (run / "results/pairs/a/attempt_0001").mkdir(parents=True)
    (run / "POLICY_REDESIGN_REPORT.md").write_text("# Result\n", encoding="utf-8")
    (run / "review/PAIR_LEDGER.json").write_text('{"unknown":true}\n', encoding="utf-8")
    (run / "results/pairs/a/attempt_0001/result.json").write_text(
        '{"status":"model_constraint_infeasible"}\n', encoding="utf-8")
    (run / "results/pairs/a/attempt_0001/trajectory.json.gz").write_bytes(b"actual raw")
    archive = tmp_path / "global.zip"
    summary = build_global_review_bundle(run, archive, workspace_root=ROOT)
    assert summary["raw_count"] == 1
    with zipfile.ZipFile(archive) as zf:
        assert zf.testzip() is None
        names = set(zf.namelist())
        assert "POLICY_REDESIGN_REPORT.md" in names
        assert "review/PAIR_LEDGER.json" in names
        assert "results/pairs/a/attempt_0001/result.json" in names
        assert "review/RAW_SHA256_INDEX.json" in names
        assert "review/GLOBAL_ZIP_MANIFEST.json" in names
        assert not any(name.endswith("trajectory.json.gz") for name in names)
        index = json.loads(zf.read("review/RAW_SHA256_INDEX.json"))
        assert index["files"][0]["path"] == "results/pairs/a/attempt_0001/trajectory.json.gz"


def test_selected_raw_and_model_recheck_after_independent_extraction(tmp_path):
    chosen = [item for item in select_v5_examples(V5)
              if item["category"] in {"strict_guard_microdifference", "unknown_grid0"}]
    assert len(chosen) == 4
    archive = tmp_path / "reproduce.zip"
    build_reproduction_bundle(archive, examples=chosen, workspace_root=ROOT,
                              v5_run=V5)
    extracted = tmp_path / "independent" / "bundle"
    extracted.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        assert zf.testzip() is None
        zf.extractall(extracted)
        assert all(not name.startswith("/") and ".." not in Path(name).parts
                   for name in zf.namelist())
    assert (extracted / "scenario_sampler.py").is_file()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(extracted)
    completed = subprocess.run(
        [sys.executable, "-m", "tools.policy_redesign.portable", "verify-all",
         "--root", str(extracted)], cwd=extracted, env=environment,
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["manifest_valid"] is True
    assert result["rechecked_raw"] == 4
    assert result["unknown_raw"] == 2
    assert result["model_cpu_inference"] is True


def test_real_truncated_v6_raw_retains_known_p0_failure_without_imputing_tail():
    """The original scorer may know a P0 event before a truncated tail."""
    run = ROOT / "outputs/v6_policy_redesign_20261016_run01"
    batch = run / "training/expanded_30roots_p01234"
    scenario_id, policy_id = "control_case_472b98e03cef29ae950d", "secure_milp"
    result_path = (batch / "results/pairs" / digest([scenario_id, policy_id]) /
                   "attempt_0001/result.json")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    raw_path = result_path.parent / "trajectory.json.gz"
    plan_path = batch / "registered_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert result["status"] == "model_constraint_infeasible"
    assert result["primary_score"]["full_horizon"] is False
    assert result["primary_score"]["targets"][0] == 1
    assert result["primary_score"]["targets"][1:18] == [None] * 17

    def relative(path):
        return Path(path).resolve().relative_to(ROOT).as_posix()

    item = {"origin_run": "v6", "evidence_role": "new_v6_train_unknown",
            "source_phase": "train", "external_raw_audit_sha256": "direct_score_test_only",
            "category": "current_truncated",
            "scenario_id": scenario_id, "policy_id": policy_id,
            "status": result["status"], "is_final_blind": False,
            "raw_path": relative(raw_path), "result_path": relative(result_path),
            "plan_path": relative(plan_path),
            "data_manifest_path": relative(plan["manifest_path"]),
            "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest()}
    report = portable.recheck_selected_raw(ROOT, {"examples": [item]})
    assert report["rechecked_raw"] == 1
    assert report["unknown_raw"] == 1
    assert report["rows"][0]["score_match"] is True


def _v6_pair_fixture(tmp_path, *, phase="development", terminal=True,
                     declared_sha=True, status="completed"):
    run = tmp_path / "v6_run"
    batch = run / ("training/expanded_30roots_p01234" if phase == "train" else phase)
    (run / "new_data").mkdir(parents=True)
    manifest = {"scenarios": [{"id": "new-case", "is_final_blind": False,
                                "experiment_role": phase}]}
    manifest_path = run / "new_data/global_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    batch.mkdir(parents=True)
    scenario_ids = (["new-case"] + [f"other-{index}" for index in range(149)]
                    if phase == "train" else ["new-case"])
    policies = ([{"policy_id": "secure_milp"}, {"policy_id": "v6_neural"}]
                + ([{"policy_id": f"train-control-{index}"} for index in range(3)]
                   if phase == "train" else []))
    (batch / "registered_plan.json").write_text(json.dumps({
        "signature": "new-plan", "manifest_path": str(manifest_path),
        "phase": phase, "scenario_ids": scenario_ids,
        "policy_specs": policies,
    }), encoding="utf-8")
    ledger = []
    for name in ("secure_milp", "v6_neural"):
        folder = batch / "results/pairs" / name / "attempt_0001"
        folder.mkdir(parents=True)
        raw = folder / "trajectory.json.gz"
        raw.write_bytes(name.encode())
        raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        result = {
            "scenario_id": "new-case", "policy_id": name,
            "status": status, "plan_signature": "new-plan",
            "terminal": terminal, "trajectory_path": "trajectory.json.gz",
        }
        if declared_sha:
            result["trajectory_sha256"] = raw_sha
        result_path = folder / "result.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        ledger.append({"scenario_id": "new-case", "policy_id": name,
                       "terminal": terminal, "score_recomputed": True,
                       "binding_passed": True,
                       "primary_full_horizon": status == "completed",
                       "full_observation_horizon": status == "completed",
                       "status": status,
                       "attempts": [{"attempt": "attempt_0001",
                                     "terminal": terminal,
                                     "result_path": str(result_path),
                                     "result_sha256": hashlib.sha256(
                                         result_path.read_bytes()).hexdigest(),
                                     "raw_path": str(raw), "raw_sha256": raw_sha}]})
    audit_name = "AUDIT_rechecked_v2.json" if phase == "train" else "AUDIT_V6.json"
    (batch / audit_name).write_text(json.dumps({
        "phase": phase, "passed": True,
        "registered_pair_count": 750 if phase == "train" else 2,
        "recomputed_pair_count": 750 if phase == "train" else 2,
        "pair_ledger": ledger}), encoding="utf-8")
    return run, batch, manifest


def test_v6_pair_selector_keeps_new_validation_separate_from_v5(tmp_path,
                                                                  monkeypatch):
    run, batch, manifest = _v6_pair_fixture(tmp_path)
    plan = json.loads((batch / "registered_plan.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(portable, "verify_batch", lambda path: (plan, manifest),
                        raising=False)
    monkeypatch.setattr(portable, "verify_saved_audit", lambda *args: [],
                        raising=False)
    rows = select_v6_matched_pair(run, batch, "new-case", "v6_neural")
    assert len(rows) == 2
    assert {row["policy_id"] for row in rows} == {"secure_milp", "v6_neural"}
    assert all(row["origin_run"] == "v6" and
               row["evidence_role"] == "new_v6_matched_validation" for row in rows)


def test_v6_pair_selector_rejects_train_raw_mislabeled_as_new_validation(tmp_path,
                                                                          monkeypatch):
    run, batch, manifest = _v6_pair_fixture(tmp_path, phase="train")
    plan = json.loads((batch / "registered_plan.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(portable, "verify_batch", lambda path: (plan, manifest),
                        raising=False)
    import pytest
    with pytest.raises(ValueError, match="selection|development"):
        select_v6_matched_pair(run, batch, "new-case", "v6_neural")


def test_current_train_unknown_pair_has_distinct_role_and_requires_unknown_mask(
        tmp_path, monkeypatch):
    import pytest
    run, batch, manifest = _v6_pair_fixture(
        tmp_path, phase="train", status="model_constraint_infeasible")
    plan = json.loads((batch / "registered_plan.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(portable, "verify_batch", lambda path: (plan, manifest),
                        raising=False)
    monkeypatch.setattr(portable, "verify_saved_audit", lambda *args: [],
                        raising=False)
    rows = portable.select_v6_train_unknown_pair(
        run, batch, "new-case", "v6_neural")
    assert len(rows) == 2
    assert {row["evidence_role"] for row in rows} == {"new_v6_train_unknown"}
    assert {row["status"] for row in rows} == {"model_constraint_infeasible"}

    audit_path = batch / "AUDIT_rechecked_v2.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["pair_ledger"][0]["primary_full_horizon"] = True
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown|horizon"):
        portable.select_v6_train_unknown_pair(
            run, batch, "new-case", "v6_neural")


def test_two_v6_pair_roster_requires_train_unknown_and_new_validation(
        tmp_path, monkeypatch):
    import pytest
    run = tmp_path / "v6_run"
    train = run / "training/expanded_30roots_p01234"
    selection = run / "selection"
    for batch, phase in ((train, "train"), (selection, "selection")):
        batch.mkdir(parents=True)
        (batch / "registered_plan.json").write_text(
            json.dumps({"phase": phase}), encoding="utf-8")

    def pair(batch, role, sid, candidate):
        return [{"origin_run": "v6", "evidence_role": role,
                 "source_phase": "train" if role == "new_v6_train_unknown" else "selection",
                 "scenario_id": sid, "policy_id": pid, "category": role,
                 "plan_path": batch / "registered_plan.json",
                 "raw_path": batch / f"{pid}.gz", "raw_sha256": pid,
                 "result_path": batch / f"{pid}.json", "result_sha256": pid,
                 "manifest_path": batch / "manifest.json",
                 "status": "model_constraint_infeasible" if role ==
                 "new_v6_train_unknown" else "completed",
                 "audit_path": batch / "audit.json", "audit_sha256": "bound"}
                for pid in ("secure_milp", candidate)]

    unknown = pair(train, "new_v6_train_unknown", "train-unknown", "v6_gt_up_030")
    validation = pair(selection, "new_v6_matched_validation", "new-selection", "v6_nn")
    monkeypatch.setattr(portable, "select_v6_train_unknown_pair",
                        lambda *args, **kwargs: unknown)
    monkeypatch.setattr(portable, "select_v6_matched_pair",
                        lambda *args, **kwargs: validation)
    assert portable._checked_v6_examples(unknown + validation, v6_run=run) == (
        unknown + validation)
    with pytest.raises(ValueError, match="train|validation|role"):
        portable._checked_v6_examples(validation + validation, v6_run=run)


def test_two_pair_package_indexes_eight_old_and_four_new_raw(tmp_path, monkeypatch):
    import pytest
    run = tmp_path / "v6_run"

    def make_pair(phase, scenario_id, candidate, role):
        batch = (run / "training/expanded_30roots_p01234" if phase == "train"
                 else run / phase)
        batch.mkdir(parents=True)
        manifest_path = batch / "manifest.json"
        manifest_path.write_text('{"scenarios":[]}', encoding="utf-8")
        plan_path = batch / "registered_plan.json"
        plan_path.write_text(json.dumps({"phase": phase, "signature": scenario_id}),
                             encoding="utf-8")
        audit_path = batch / "AUDIT_V6.json"
        audit_path.write_text('{"passed":true}', encoding="utf-8")
        rows = []
        for policy_id in ("secure_milp", candidate):
            folder = batch / "results" / policy_id
            folder.mkdir(parents=True)
            raw_path = folder / "trajectory.json.gz"
            raw_path.write_bytes((scenario_id + policy_id).encode())
            result_path = folder / "result.json"
            result_path.write_text(json.dumps({
                "scenario_id": scenario_id, "policy_id": policy_id,
                "plan_signature": scenario_id,
                "status": "model_constraint_infeasible" if phase == "train"
                else "completed", "trajectory_path": "trajectory.json.gz",
                "trajectory_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            }), encoding="utf-8")
            item = portable._result_example(
                origin="v6", role=role, category=role,
                scenario_id=scenario_id, policy_id=policy_id,
                result_path=result_path, plan_path=plan_path,
                manifest_path=manifest_path, is_final_blind=False)
            item.update(source_phase=phase, audit_path=audit_path,
                        audit_sha256=hashlib.sha256(audit_path.read_bytes()).hexdigest())
            rows.append(item)
        return rows

    unknown = make_pair("train", "train-unknown", "v6_gt_up_030",
                        "new_v6_train_unknown")
    validation = make_pair("selection", "new-validation", "v6_nn",
                           "new_v6_matched_validation")
    monkeypatch.setattr(portable, "select_v6_train_unknown_pair",
                        lambda *args, **kwargs: unknown)
    monkeypatch.setattr(portable, "select_v6_matched_pair",
                        lambda *args, **kwargs: validation)
    with pytest.raises(ValueError, match="formal v6 best"):
        build_reproduction_bundle(tmp_path / "old_model_not_allowed.zip",
                                  v6_examples=unknown + validation,
                                  v6_run=run, workspace_root=ROOT, v5_run=V5)
    model = tmp_path / "formal_model"
    model.mkdir()
    (model / "metadata.json").write_text("{}", encoding="utf-8")
    (model / "best.pt").write_bytes(b"test checkpoint")
    monkeypatch.setattr(portable, "_model_spec", lambda *args: {
        "kind": "v6_best", "role": "new_v6_best_checkpoint",
        "relative_dir": "models/selected", "checkpoint": "best.pt",
        "metadata_sha256": hashlib.sha256((model / "metadata.json").read_bytes()).hexdigest(),
        "checkpoint_sha256": hashlib.sha256((model / "best.pt").read_bytes()).hexdigest(),
    })
    archive = tmp_path / "twelve_raw.zip"
    build_reproduction_bundle(archive, v6_examples=unknown + validation,
                              v6_run=run, workspace_root=ROOT, v5_run=V5,
                              model_dir=model)
    with zipfile.ZipFile(archive) as opened:
        manifest = json.loads(opened.read("REPRO_MANIFEST.json"))
    assert manifest["historical_v5_count"] == 8
    assert manifest["new_v6_count"] == 4
    assert sum(item["evidence_role"] == "new_v6_train_unknown"
               for item in manifest["examples"]) == 2
    assert sum(item["evidence_role"] == "new_v6_matched_validation"
               for item in manifest["examples"]) == 2


def test_explicit_eight_historical_raw_cannot_swap_a_fixed_counterexample(
        tmp_path, monkeypatch):
    import pytest
    historical = portable.select_v5_core_examples(V5)
    changed = [dict(item) for item in historical]
    changed[0]["raw_path"] = historical[1]["raw_path"]
    monkeypatch.setattr(portable, "_checked_v6_examples",
                        lambda *args, **kwargs: [{} for _ in range(4)])
    with pytest.raises(ValueError, match="historical.*raw"):
        build_reproduction_bundle(tmp_path / "changed_old.zip",
                                  examples=changed, v6_examples=[{}] * 4,
                                  workspace_root=ROOT, v5_run=V5)


def test_v6_pair_selector_rejects_missing_result_raw_sha(tmp_path, monkeypatch):
    run, batch, manifest = _v6_pair_fixture(tmp_path, declared_sha=False)
    plan = json.loads((batch / "registered_plan.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(portable, "verify_batch", lambda path: (plan, manifest),
                        raising=False)
    monkeypatch.setattr(portable, "verify_saved_audit", lambda *args: [],
                        raising=False)
    import pytest
    with pytest.raises(ValueError, match="SHA"):
        select_v6_matched_pair(run, batch, "new-case", "v6_neural")


def test_reproduction_builder_rejects_single_unmatched_v6_raw(tmp_path):
    import pytest
    run, batch, _ = _v6_pair_fixture(tmp_path)
    item = portable._result_example(
        origin="v6", role="new_v6_matched_validation", category="unmatched",
        scenario_id="new-case", policy_id="v6_neural",
        result_path=batch / "results/pairs/v6_neural/attempt_0001/result.json",
        plan_path=batch / "registered_plan.json",
        manifest_path=run / "new_data/global_manifest.json", is_final_blind=False)
    with pytest.raises(ValueError, match="matched|pair"):
        build_reproduction_bundle(tmp_path / "unmatched.zip", examples=[],
                                  v6_examples=[item], workspace_root=ROOT,
                                  v5_run=V5)


def test_portable_v6_model_requires_recorded_last_checkpoint(tmp_path):
    import pytest
    model = tmp_path / "models/formal"
    model.mkdir(parents=True)
    (model / "best.pt").write_bytes(b"best only")
    (model / "metadata.json").write_text(json.dumps({"best_epoch": 2,
        "deployed_checkpoint": "best.pt",
        "artifacts": {"best.pt": hashlib.sha256(b"best only").hexdigest()}}),
        encoding="utf-8")
    with pytest.raises(ValueError, match="last.pt"):
        portable._model_spec(model, tmp_path / "old", tmp_path)
