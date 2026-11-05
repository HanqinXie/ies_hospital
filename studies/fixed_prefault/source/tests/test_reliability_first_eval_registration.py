"""Formal v4 development plans must bind new data, code, and calibration."""
import hashlib
import json

import pytest


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _new_design(tmp_path):
    from tools.reliability_first import run_eval

    manifest_path = tmp_path / "new_data/global_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    design = tmp_path / "design"
    design.mkdir()
    proposal = {"version": "control_improvement.fixed_mechanisms.v1",
                "proposal_sha256": "registered-proposal-content"}
    proposal_path = design / "source_proposal.json"
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
    plan_path = design / "snapshot_plan_v2.json"
    plan_path.write_text(json.dumps([
        {"scenario_id": "train_a", "experiment_role": "pilot_train"},
        {"scenario_id": "dev_a", "experiment_role": "development_boundary"},
    ]), encoding="utf-8")
    manifest = {"manifest_version": "control_improvement.fixed_new300.v1",
                "control_improvement_cohort": "new_fixed_development",
                "control_improvement_data_design": {
                    "proposal_sha256": proposal["proposal_sha256"]},
                "control_improvement_proposal": {"path": "design/source_proposal.json",
                                                 "sha256": _sha(proposal_path)},
                "scenarios": [{"id": "train_a", "experiment_role": "pilot_train"},
                              {"id": "dev_a", "experiment_role": "development_boundary"}],
                "splits": {"train_a": {"experiment_role": "pilot_train"},
                           "dev_a": {"experiment_role": "development_boundary"}}}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    collection_path = design / "branch_collection_v1.json"
    collection_path.write_text(json.dumps({
        "schema": "reliability_first.collection_design.v1",
        "manifest_sha256": _sha(manifest_path),
        "plan_sha256": _sha(plan_path),
        "early_indices": [0], "remaining_indices": [],
        "development_indices_excluded": [1]}), encoding="utf-8")
    return manifest_path, proposal_path, plan_path, collection_path


def test_new_development_identity_whitelists_frozen_design_and_manifest(tmp_path, monkeypatch):
    from tools.reliability_first import run_eval

    monkeypatch.setattr(run_eval, "ROOT", tmp_path)
    manifest_path, proposal, plan, collection = _new_design(tmp_path)
    binding = run_eval._new_design_binding(manifest_path)
    assert binding == {str(p.relative_to(tmp_path)).replace("\\", "/"): _sha(p)
                       for p in (proposal, plan, collection)}
    assert _sha(manifest_path) == json.loads(collection.read_text(encoding="utf-8"))["manifest_sha256"]


def test_older_development_manifest_is_rejected_even_with_a_development_role(tmp_path, monkeypatch):
    from tools.reliability_first import run_eval

    monkeypatch.setattr(run_eval, "ROOT", tmp_path)
    manifest_path, *_ = _new_design(tmp_path)
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    old["manifest_version"] = "control_improvement.legacy60.v1"
    manifest_path.write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises(ValueError, match="new.*design|manifest.*new|cohort"):
        run_eval._new_design_binding(manifest_path)


def test_frozen_collection_cannot_put_development_rows_into_training(tmp_path, monkeypatch):
    from tools.reliability_first import run_eval

    monkeypatch.setattr(run_eval, "ROOT", tmp_path)
    manifest_path, _, _, collection_path = _new_design(tmp_path)
    collection = json.loads(collection_path.read_text(encoding="utf-8"))
    collection["early_indices"] = [0, 1]
    collection_path.write_text(json.dumps(collection), encoding="utf-8")
    with pytest.raises(ValueError, match="development|collection"):
        run_eval._new_design_binding(manifest_path)


def test_formal_full_registration_requires_independent_calibration_but_one_scene_smoke_is_explicit(tmp_path, monkeypatch):
    from tools.reliability_first import run_eval

    monkeypatch.setattr(run_eval, "ROOT", tmp_path)
    monkeypatch.setattr(run_eval, "_logic_binding", lambda: {})
    manifest_path, *_ = _new_design(tmp_path)
    calls = []

    def register(*args, **kwargs):
        calls.append(kwargs)
        return {"signature": "registered-test", "scenario_ids": kwargs["scenario_ids"],
                "policy_specs": kwargs["policy_specs"]}

    monkeypatch.setattr(run_eval, "register_closed_loop", register)
    with pytest.raises(ValueError, match="calibration"):
        run_eval.register_development(manifest_path, tmp_path / "formal.json",
                                      nn_dir="nn", tree_dir="tree")
    assert calls == []
    smoke = run_eval.register_development(manifest_path, tmp_path / "smoke.json",
                                          nn_dir="nn", tree_dir="tree", limit=1)
    assert smoke["signature"] == "registered-test"
    assert calls[0]["registration_mode"] == "uncalibrated_exploration_smoke"
    assert calls[0]["design_binding"]


def test_calibration_must_match_full_new_manifest_and_frozen_collection(tmp_path, monkeypatch):
    from tools.reliability_first import run_eval

    monkeypatch.setattr(run_eval, "ROOT", tmp_path)
    manifest_path, *_ = _new_design(tmp_path)
    binding = run_eval._new_design_binding(manifest_path)
    model_dir = tmp_path / "nn"
    model_dir.mkdir()
    (model_dir / "metadata.json").write_text('{"schema":"test"}', encoding="utf-8")
    source = {"stage": "full", "manifest_sha256": _sha(manifest_path),
              "collection_design_sha256": binding["design/branch_collection_v1.json"],
              "snapshot_plan_sha256": binding["design/snapshot_plan_v2.json"]}
    monkeypatch.setattr(run_eval, "_registered_calibration",
                        lambda directory, metadata: {"source_binding": source})
    run_eval._validate_independent_calibrations(("nn",), manifest_path, binding)
    source["stage"] = "pilot8"
    with pytest.raises(ValueError, match="different new design"):
        run_eval._validate_independent_calibrations(("nn",), manifest_path, binding)


def test_run_entry_rejects_missing_algorithm_receipt_instead_of_bypassing_binding(tmp_path, monkeypatch):
    from tools.reliability_first import run_eval

    plan = tmp_path / "formal.json"
    plan.write_text(json.dumps({"signature": "registered-test"}), encoding="utf-8")
    monkeypatch.setattr(run_eval, "run_closed_loop", lambda *args, **kwargs: pytest.fail("run bypassed receipt"))
    with pytest.raises(FileNotFoundError, match="algorithm.*receipt"):
        run_eval.run_development(plan, tmp_path / "output")
