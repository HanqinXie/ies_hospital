"""Portable physical replay stages a new plan, never runs an old absolute path."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from hospital_ies.dataset_manifest import digest
from tools.four_scenarios.package import PHYSICAL_INPUTS, create_archive, verify_integrity
from tools.policy_redesign.closed_loop import PHYSICS_FILES


def module():
    return importlib.import_module("tools.state_dispatch.portable_replay")


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def package_root(tmp_path):
    scene = dict(id="selected_scene", analysis_class="L4b", root_family_id="seen_root",
                 is_final_blind=False, experiment_role="confirmation", start_hour=1232,
                 duration_steps=40, step_minutes=15)
    files, entries = [], {}
    for name in sorted(set(PHYSICAL_INPUTS) | set(PHYSICS_FILES)):
        content = b"# frozen fixture\n" if name.endswith(".py") else b"{}"
        entries[name] = content
        files.append(dict(path=name, bytes=len(content), sha256=sha_bytes(content)))
    policy_specs = [dict(policy_id="priority_milp", kind="priority"),
                    dict(policy_id="secure_milp", kind="secure"),
                    dict(policy_id="state_dispatch_v2", kind="state_dispatch", config={"gain": .5})]
    manifest = dict(files=files, scenarios=[scene], schemas={}, options=dict(seed=20261016),
        execution_contract=dict(name="portable_fixture"), splits={scene["id"]: dict(experiment_role="confirmation", is_final_blind=False)},
        simulator_hash=digest([row for row in files if row["path"] in PHYSICS_FILES]),
        policy_search_final_12_backgrounds_unopened=True)
    preference = b'{"schema":"frozen_preference"}'
    plan = dict(schema="state_dispatch.matched_full_feedback.v2", signature="old_plan",
        manifest_path="D:\\old_workspace\\new_data\\global_manifest.json",
        preference_path="D:\\old_workspace\\design\\RESEARCH_PREFERENCE.json",
        preference_sha256=sha_bytes(preference), scenario_ids=[scene["id"]],
        policy_specs=policy_specs, phase="confirmation", registered_pair_count=3,
        source_sha256={"src/hospital_ies/model.py": next(r["sha256"] for r in files if r["path"]=="src/hospital_ies/model.py")})
    manifest_bytes = json.dumps(manifest).encode()
    plan["manifest_sha256"] = sha_bytes(manifest_bytes)
    plan_bytes = json.dumps(plan).encode()
    entries.update({"batches/confirmation/evidence/global_manifest.json": manifest_bytes,
                    "batches/confirmation/evidence/registered_plan.json": plan_bytes,
                    "batches/confirmation/evidence/RESEARCH_PREFERENCE.json": preference,
                    "run/design/SOURCE_PROPOSAL.json": b'{"provenance_only":true}'})
    archive = tmp_path / "portable_fixture.zip"
    create_archive(archive, entries, dict(kind="v9_state_dispatch_selected_raw",
        examples=[dict(batch="confirmation", scenario_id=scene["id"], policy_id=spec["policy_id"]) for spec in policy_specs],
        batches=[dict(name="confirmation", evidence={
            "manifest":dict(path="batches/confirmation/evidence/global_manifest.json",sha256=sha_bytes(manifest_bytes)),
            "plan":dict(path="batches/confirmation/evidence/registered_plan.json",sha256=sha_bytes(plan_bytes))},
            preference_file="batches/confirmation/evidence/RESEARCH_PREFERENCE.json",
            preference_sha256=sha_bytes(preference))], external_registered_inputs=[]))
    root = tmp_path / "package"
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(root)
    return root, scene, plan


def test_replay_request_uses_frozen_selected_scene_specs_phase_and_packaged_preference(package_root):
    root, scene, plan = package_root
    request = module().replay_request(root, "confirmation")
    assert request["scenarios"] == [scene]
    assert request["policy_specs"] == plan["policy_specs"]
    assert request["phase"] == "confirmation"
    assert request["runner_module"] == "tools.state_dispatch.closed_loop_v2"
    assert Path(request["preference_path"]).is_relative_to(root)
    assert not request["preference_path"].startswith("D:\\old_workspace")


def test_replay_rejects_scene_not_in_portable_selected_examples(package_root):
    root, _, _ = package_root
    with pytest.raises(ValueError, match="selected"):
        module().replay_request(root, "confirmation", scenario_ids=["unseen_scene"])


def test_prepare_rebinds_to_current_package_and_fresh_output_without_sampling(package_root, tmp_path, monkeypatch):
    mod = module()
    root, scene, old_plan = package_root
    recorded = {}
    def register(manifest_path, plan_path, **kwargs):
        recorded.update(kwargs)
        recorded["manifest_path"] = str(manifest_path)
        new_plan = dict(manifest_path=str(manifest_path), preference_path=str(kwargs["preference_path"]),
                        scenario_ids=kwargs["scenario_ids"], policy_specs=kwargs["policy_specs"],
                        phase=kwargs["phase"], signature="new_registered_plan", registered_pair_count=3)
        Path(plan_path).write_text(json.dumps(new_plan), encoding="utf-8")
        return new_plan
    monkeypatch.setattr(mod, "_backend", lambda name, active_root: SimpleNamespace(register_batch=register))
    output = tmp_path / "new_run"
    receipt = mod.prepare_replay(root, output, "confirmation")
    manifest = json.loads(Path(receipt["manifest_path"]).read_text(encoding="utf-8"))
    new_plan = json.loads(Path(receipt["plan_path"]).read_text(encoding="utf-8"))
    assert manifest["scenarios"] == [scene]
    assert recorded["policy_specs"] == old_plan["policy_specs"] and recorded["phase"] == old_plan["phase"]
    assert Path(new_plan["manifest_path"]).is_relative_to(output)
    assert Path(new_plan["preference_path"]).is_relative_to(root)
    assert receipt["resampled_scenarios"] is False and receipt["episodes_started"] == 0
    assert receipt["parent_design_files_are_runtime_dependencies"] is False
    assert verify_integrity(root)["kind"] == "v9_state_dispatch_selected_raw"


def test_prepare_refuses_existing_or_package_internal_output(package_root, tmp_path):
    root, _, _ = package_root
    output = tmp_path / "existing"
    output.mkdir(); (output / "evidence.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="empty"):
        module().prepare_replay(root, output, "confirmation")
    with pytest.raises(ValueError, match="outside"):
        module().prepare_replay(root, root / "new_run", "confirmation")


def test_failed_replay_does_not_claim_a_completed_physical_rerun(tmp_path, monkeypatch):
    mod = module()
    receipt = dict(root=str(tmp_path), runner_module="frozen_runner",
                   plan_path=str(tmp_path / "new_plan.json"), results_path=str(tmp_path / "results"))
    monkeypatch.setattr(mod, "prepare_replay", lambda *args, **kwargs: receipt)
    backend = SimpleNamespace(
        run_batch=lambda *args: dict(registered_pair_count=3, terminal_pair_count=2, unknown_primary_pair_count=1),
        audit_batch=lambda *args, **kwargs: dict(passed=False, errors=["solver failure retained"]))
    monkeypatch.setattr(mod, "_backend", lambda *args: backend)
    result = mod.run_replay(tmp_path, tmp_path / "output", "confirmation")
    assert result["physical_simulation_attempted"] is True
    assert result["full_physical_simulator_rerun"] is False


@pytest.mark.parametrize("complete", [False, True])
def test_terminal_audited_prefix_is_not_a_full_physical_rerun(tmp_path, monkeypatch, complete):
    mod = module()
    receipt = dict(root=str(tmp_path), runner_module="frozen_runner",
                   plan_path=str(tmp_path / "new_plan.json"), results_path=str(tmp_path / "results"))
    monkeypatch.setattr(mod, "prepare_replay", lambda *args, **kwargs: receipt)
    pairs = [dict(primary_full_horizon=True, full_observation_horizon=True,
                  recomputed_full_observation=dict(full_horizon=True, observed_minutes=600.0))
             for _ in range(3)]
    if not complete:
        pairs[-1].update(primary_full_horizon=False, full_observation_horizon=False,
            recomputed_full_observation=dict(full_horizon=False, observed_minutes=277.5))
    backend = SimpleNamespace(
        run_batch=lambda *args: dict(registered_pair_count=3, terminal_pair_count=3,
                                    unknown_primary_pair_count=int(not complete)),
        audit_batch=lambda *args, **kwargs: dict(passed=True, errors=[],
            recomputed_pair_count=3, pair_ledger=pairs))
    monkeypatch.setattr(mod, "_backend", lambda *args: backend)
    result = mod.run_replay(tmp_path, tmp_path / "output", "confirmation")
    assert result["physical_simulation_attempted"] is True
    assert result["full_physical_simulator_rerun"] is complete
