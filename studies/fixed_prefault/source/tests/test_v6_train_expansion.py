"""The expanded fit panel is grouped by the old train roots only."""

import hashlib
import json
from pathlib import Path

import pytest

from tools.policy_redesign import train_expansion
from tools.policy_redesign.train_expansion import choose_train_panel
from tools.policy_redesign.search import _signed
from tools.control_improvement.ranking import TARGET_NAMES
from hospital_ies.dataset_manifest import digest


ROOT = Path(__file__).resolve().parents[1]


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _search_fixture(tmp_path):
    search = tmp_path / "search"
    generation = search / "seed_6101/generation_0001"
    scenario_ids = [f"train_{i}" for i in range(4)]
    candidates = []
    specs = [{"policy_id": "secure_milp", "kind": "secure"}]
    for i in range(10):
        family = "port_residual" if i < 5 else "source_allocation"
        theta = [0.1 * (i + 1)] * (6 if i < 5 else 7)
        pid = f"candidate_{i:02d}"
        specs.append({"policy_id": pid, "kind": "v6_feedback",
                      "family": family, "theta": theta})
        candidates.append({"policy_id": pid, "family": family, "theta": theta,
                           "priority_key": [0, i],
                           "complete_paired_case_count": 4, "unknown_case_ids": []})
    _write(search / "search_plan.json", _signed({
        "schema": "policy_redesign.adaptive_feedback_search.v1.plan",
        "seeds": [6101], "min_generations": 1, "max_generations": 1,
        "scenario_ids": scenario_ids,
        "families": ["port_residual", "source_allocation"]}))
    _write(search / "seed_6101/stop.json", _signed({
        "schema": "policy_redesign.adaptive_feedback_search.v1.seed_stop",
        "seed": 6101, "completed_generations": 1, "reason": "max_generations"}))
    runner = {"schema": "policy_redesign.matched_full_feedback.v1",
              "phase": "train", "scenario_ids": scenario_ids,
              "policy_specs": specs, "registered_pair_count": 44,
              "source_sha256": {"tools/control_improvement/branches.py":
                                _sha(ROOT / "tools/control_improvement/branches.py")}}
    runner["signature"] = digest(runner)
    plan_path = generation / "registered_plan.json"
    _write(plan_path, runner)
    summary = {"schema": runner["schema"] + ".summary",
               "plan_signature": runner["signature"],
               "registered_pair_count": 44, "recorded_pair_count": 44,
               "terminal_pair_count": 44,
               "matched_initial_state_and_stream": True}
    summary_path = generation / "results/summary_0001.json"
    copy_path = generation / "summary_for_search.json"
    _write(summary_path, summary)
    _write(copy_path, summary)
    evaluation = _signed({
        "schema": "policy_redesign.adaptive_feedback_search.v1.evaluation",
        "seed": 6101, "generation": 1, "scenario_ids": scenario_ids,
        "registered_pair_count": 44, "terminal_pair_count": 44,
        "matched_initial_state_and_stream": True, "candidates": candidates,
        "runner_plan_sha256": _sha(plan_path),
        "runner_summary_sha256": _sha(copy_path)})
    _write(generation / "evaluation.json", evaluation)
    rows = []
    first_raw = None
    score = {"full_horizon": True, "targets": [0.0] * 19,
             "target_names": list(TARGET_NAMES), "target_mask": [True] * 19}
    for sid in scenario_ids:
        for spec in specs:
            pid = spec["policy_id"]
            folder = generation / "results/pairs" / sid / pid / "attempt_0001"
            folder.mkdir(parents=True, exist_ok=True)
            raw_path = folder / "trajectory.json.gz"
            raw_path.write_bytes(f"fixture:{sid}/{pid}".encode())
            first_raw = first_raw or raw_path
            result_path = folder / "result.json"
            _write(result_path, {"scenario_id": sid, "policy_id": pid,
                                 "terminal": True, "status": "completed",
                                 "trajectory_sha256": _sha(raw_path)})
            rows.append({"scenario_id": sid, "policy_id": pid,
                         "terminal": True, "score_recomputed": True,
                         "binding_passed": True, "primary_full_horizon": True,
                         "full_observation_horizon": True,
                         "recomputed_binding": {"passed": True},
                         "recomputed_primary_score": score,
                         "recomputed_full_observation": score,
                         "latest_result_sha256": _sha(result_path),
                         "attempts": [{"result_path": str(result_path),
                                       "result_sha256": _sha(result_path),
                                       "raw_path": str(raw_path),
                                       "raw_sha256": _sha(raw_path),
                                       "declared_raw_sha256": _sha(raw_path)}]})
    audit = {"schema": "policy_redesign.v6.matched_batch_audit.v1",
             "passed": True, "errors": [], "phase": "train",
             "plan_path": str(plan_path.resolve()), "plan_sha256": _sha(plan_path),
             "plan_signature": runner["signature"],
             "summary_path": str(summary_path.resolve()),
             "summary_sha256": _sha(summary_path),
             "scoring_source_sha256": runner["source_sha256"][
                 "tools/control_improvement/branches.py"],
             "target_names": list(TARGET_NAMES),
             "registered_pair_count": 44, "recorded_pair_count": 44,
             "terminal_pair_count": 44, "recomputed_pair_count": 44,
             "unknown_primary_pair_ids": [], "unknown_full_pair_ids": [],
             "matched_initial_state_and_stream": True, "pair_ledger": rows}
    audit_path = generation / "AUDIT.json"
    _write(audit_path, audit)
    return search, audit_path, first_raw


def test_thirty_old_train_roots_each_have_all_five_pressures():
    manifest = json.loads((ROOT / "outputs/v5_policy_search_20261015_run01"
                           / "new_data/global_manifest.json").read_text(encoding="utf-8"))
    scenes = choose_train_panel(manifest)
    assert len(scenes) == len({scene["id"] for scene in scenes}) == 150
    roots = {scene["root_family_id"] for scene in scenes}
    assert len(roots) == 30
    assert len({scene["condition_id"] for scene in scenes}) == 6
    assert all({scene["pressure_point_index"] for scene in scenes
                if scene["root_family_id"] == root} == set(range(5))
               for root in roots)
    assert all(scene["experiment_role"] == "train" and not scene.get("is_final_blind")
               for scene in scenes)
    damaged = json.loads(json.dumps(manifest))
    victim = next(scene for scene in damaged["scenarios"]
                  if scene.get("experiment_role") == "train" and
                  scene["pressure_point_index"] == 0)
    victim["pressure_point_index"] = 1
    with pytest.raises(ValueError, match="five-pressure"):
        choose_train_panel(damaged)


def test_expansion_registers_150_train_scenes_and_five_policy_roster(tmp_path,
                                                                     monkeypatch):
    search = tmp_path / "search"
    search.mkdir()
    (search / "search_plan.json").write_text("{}", encoding="utf-8")
    selected = [{"policy_id": "searched_port", "kind": "v6_feedback",
                 "family": "port_residual", "theta": [0.1] * 6},
                {"policy_id": "searched_source", "kind": "v6_feedback",
                 "family": "source_allocation", "theta": [0.1] * 7}]
    monkeypatch.setattr(train_expansion, "select_search_specs",
                        lambda path, per_family=1: (selected, []))
    captured = {}
    def register(manifest_path, plan_path, *, scenario_ids, policy_specs,
                 preference_path, phase):
        captured.update(scenario_ids=scenario_ids, policy_specs=policy_specs,
                        phase=phase)
        return {"signature": "fixture_signed"}
    monkeypatch.setattr(train_expansion, "register_batch", register)
    record = train_expansion.register_expansion(search, tmp_path / "expanded")
    assert record["registered_train_scenarios"] == 150
    assert record["pressure_indices"] == list(range(5))
    assert "panel_partial_pressure_support" not in record
    assert len(captured["scenario_ids"]) == 150
    assert len(captured["policy_specs"]) == 5
    assert captured["phase"] == "train"


def test_search_expansion_requires_raw_audit_for_every_generation(tmp_path):
    search, audit_path, _ = _search_fixture(tmp_path)
    specs, evidence = train_expansion.select_search_specs(search)
    assert len(specs) == 2
    assert all(row["search_raw_audit_sha256"] == _sha(audit_path)
               for row in evidence)
    audit_path.unlink()
    with pytest.raises(ValueError, match="AUDIT"):
        train_expansion.select_search_specs(search)


def test_search_expansion_rejects_audit_unknown_19_target_or_sha_tamper(tmp_path):
    search, audit_path, raw_path = _search_fixture(tmp_path)
    original = json.loads(audit_path.read_text(encoding="utf-8"))
    changes = (
        lambda audit: audit.update(passed=False),
        lambda audit: audit["unknown_full_pair_ids"].append("train_0/candidate_00"),
        lambda audit: audit["pair_ledger"][0]["recomputed_full_observation"].update(
            targets=[0.0] * 18),
        lambda audit: audit.update(plan_sha256="wrong"),
        lambda audit: audit.update(summary_sha256="wrong"),
        lambda audit: audit.update(scoring_source_sha256="wrong"),
    )
    for change in changes:
        audit = json.loads(json.dumps(original))
        change(audit)
        _write(audit_path, audit)
        with pytest.raises(ValueError, match="audit|AUDIT"):
            train_expansion.select_search_specs(search)
    _write(audit_path, original)
    raw_path.write_bytes(b"tampered after audit")
    with pytest.raises(ValueError, match="raw|audit|AUDIT"):
        train_expansion.select_search_specs(search)


def test_search_expansion_rejects_extra_registered_generation_after_seed_stop(tmp_path):
    search, _, _ = _search_fixture(tmp_path)
    source = search / "seed_6101/generation_0001/registered_plan.json"
    extra = search / "seed_6101/generation_0002/registered_plan.json"
    extra.parent.mkdir(parents=True)
    extra.write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="generation"):
        train_expansion.select_search_specs(search)
