"""V6 pressure analysis fixtures are synthetic, never experimental outcomes."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from tools.policy_redesign import sensitivity


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _panel():
    scenes = []
    for condition in range(6):
        for point in range(5):
            scenes.append({
                "id": f"s{condition}_{point}",
                "experiment_role": "development", "is_final_blind": False,
                "condition_id": f"condition_{condition}",
                "root_family_id": f"root_{condition}",
                "pressure_point_index": point,
                "time_block_id": f"day_{condition}",
                "base_state_id": f"base_{condition}",
                "initialization_seed": 42,
                "exogenous_stream_id": f"stream_{condition}",
                "exogenous_events": [
                    {"kind": "device_fault", "device": "GT_2",
                     "start_minute": 100, "end_minute": 200},
                    {"kind": "load_shock", "start_minute": 120,
                     "end_minute": 240, "shock": {"e": 1 + point / 10}},
                ],
            })
    manifest = {"scenarios": scenes}
    specs = [{"policy_id": "secure_milp", "kind": "secure"},
             {"policy_id": "candidate", "kind": "v6_feedback"}]
    plan = {"phase": "development", "scenario_ids": [s["id"] for s in scenes],
            "policy_specs": specs, "registered_pair_count": 60,
            "score_windows": {"primary": [60, 420], "full": [0, 600]},
            "final_blind_opened": False, "signature": "fixture-signature",
            "model_bindings": {}, "source_sha256": {},
            "preference_sha256": "fixture-preference"}
    return plan, manifest


def _score(minutes, *, complete=True, observed_p0=None):
    targets = [0.0] * 19
    targets[1] = minutes
    if not complete:
        targets = [observed_p0] + [None] * 18
    return {"targets": targets, "full_horizon": complete,
            "target_mask": [value is not None for value in targets]}


def _summary(manifest):
    rows = []
    for scene in manifest["scenarios"]:
        sid, point = scene["id"], scene["pressure_point_index"]
        root = scene["root_family_id"]
        for pid in ("secure_milp", "candidate"):
            incomplete_primary = pid == "candidate" and sid == "s0_4"
            incomplete_full = pid == "candidate" and sid in {"s0_4", "s1_3"}
            minutes = 10 + (point if pid == "candidate" else 0)
            rows.append({
                "scenario_id": sid, "policy_id": pid,
                "primary_score": _score(minutes, complete=not incomplete_primary,
                                        observed_p0=1 if incomplete_primary else None),
                "full_observation": _score(minutes, complete=not incomplete_full,
                                           observed_p0=1 if incomplete_full else None),
                "binding": {"passed": True,
                            "initial_state_sha256": "initial_" + root,
                            "exogenous_stream_id": scene["exogenous_stream_id"]},
            })
    return {"results": rows}


def test_design_requires_exact_development_pressure_cross_and_schedule():
    plan, manifest = _panel()
    design = sensitivity.development_design(plan, manifest)
    assert len(design["roots"]) == 6
    assert design["pressure_point_indices"] == [0, 1, 2, 3, 4]
    assert design["anchor_pressure_point_index"] == 2
    assert design["policy_ids"] == ["secure_milp", "candidate"]
    assert design["roots"][0]["scenario_ids_by_pressure"]["4"] == "s0_4"

    wrong = copy.deepcopy(manifest)
    wrong["scenarios"][4]["exogenous_events"][0]["start_minute"] = 101
    with pytest.raises(ValueError, match="schedule"):
        sensitivity.development_design(plan, wrong)

    blind = copy.deepcopy(manifest)
    blind["scenarios"][0]["is_final_blind"] = True
    with pytest.raises(ValueError, match="blind"):
        sensitivity.development_design(plan, blind)

    missing = copy.deepcopy(plan)
    missing["scenario_ids"].remove("s0_4")
    with pytest.raises(ValueError, match="30|plan|pressure"):
        sensitivity.development_design(missing, manifest)


def test_both_windows_keep_unknown_and_same_root_effect_crossing():
    plan, manifest = _panel()
    result = sensitivity.summarize_windows(plan, manifest, _summary(manifest))
    primary = result["primary_score"]
    full = result["full_observation"]
    p4 = primary["by_condition_and_pressure"]["condition_0"]["4"]["candidate"]
    assert p4["registered_case_count"] == 1
    assert p4["complete_paired_case_count"] == 0
    assert p4["unknown_case_ids"] == ["s0_4"]
    assert p4["paired_observable_count_by_target"][0] == 1
    assert p4["paired_observable_count_by_target"][1] == 0
    assert p4["sum_delta_on_observed"][1] is None
    crossing = primary["within_root_effect_change_from_anchor"]["candidate"]["condition_0"]["4"]
    assert crossing["paired_root_count"] == 0
    assert crossing["unknown_root_ids"] == ["root_0"]
    assert crossing["sum_effect_change_on_observed"][1] is None
    p3_primary = primary["by_condition_and_pressure"]["condition_1"]["3"]["candidate"]
    p3_full = full["by_condition_and_pressure"]["condition_1"]["3"]["candidate"]
    assert p3_primary["complete_paired_case_count"] == 1
    assert p3_full["complete_paired_case_count"] == 0
    assert p3_full["unknown_case_ids"] == ["s1_3"]
    assert primary["within_root_effect_change_from_anchor"]["candidate"]["condition_2"]["4"]["sum_effect_change_on_observed"][1] == 2


def test_preregistration_precedes_results_and_binds_source_plan_manifest(tmp_path, monkeypatch):
    plan, manifest = _panel()
    run = tmp_path / "v6"
    folder = run / "development"
    plan_path = folder / "registered_plan.json"
    manifest_path = run / "new_data/global_manifest.json"
    freeze_path = run / "selection/FROZEN_SELECTION.json"
    _write(manifest_path, manifest)
    _write(plan_path, plan)
    _write(freeze_path, {"schema": "fixture"})
    plan["manifest_path"] = str(manifest_path)
    plan["manifest_sha256"] = _sha(manifest_path)
    frozen = {"policy_specs": plan["policy_specs"],
              "model_bindings": {}, "source_sha256": {},
              "manifest_sha256": plan["manifest_sha256"],
              "preference_sha256": plan["preference_sha256"],
              "final_blind_opened": False}
    _write(plan_path, plan)
    monkeypatch.setattr(sensitivity, "verify_batch", lambda _: (plan, manifest))
    monkeypatch.setattr(sensitivity, "_verify_freeze",
                        lambda _: (frozen, {}, manifest))

    result = folder / "results/attempts.jsonl"
    result.parent.mkdir(parents=True)
    result.write_text('{"event":"started"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="before.*result|before.*attempt"):
        sensitivity.preregister_sensitivity(run)
    assert not (folder / "SENSITIVITY_PREREGISTRATION.json").exists()
    result.unlink()

    receipt_path = sensitivity.preregister_sensitivity(run)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["development_plan_sha256"] == _sha(plan_path)
    assert receipt["manifest_sha256"] == _sha(manifest_path)
    assert receipt["selection_freeze_sha256"] == _sha(freeze_path)
    assert receipt["sensitivity_source_sha256"] == _sha(
        sensitivity.Path(sensitivity.__file__))
    assert sensitivity.verify_preregistration(run) == receipt_path
    with pytest.raises(FileExistsError):
        sensitivity.preregister_sensitivity(run)

    result.write_text(json.dumps({"event": "started", "time_unix": 0}) + "\n",
                      encoding="utf-8")
    with pytest.raises(ValueError, match="before sensitivity"):
        sensitivity.verify_preregistration(run)
    result.unlink()

    plan_path.write_text(plan_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="plan|source"):
        sensitivity.verify_preregistration(run)


def test_analysis_refuses_failed_raw_audit_before_writing_result(tmp_path, monkeypatch):
    run = tmp_path / "v6"
    folder = run / "development"
    prereg = folder / "SENSITIVITY_PREREGISTRATION.json"
    audit = folder / "AUDIT_V6.json"
    _write(prereg, {"schema": "fixture"})
    _write(audit, {"passed": False, "errors": ["raw score differs"]})
    monkeypatch.setattr(sensitivity, "verify_preregistration", lambda _: prereg)
    monkeypatch.setattr(sensitivity, "verify_saved_audit", lambda *args: [])
    with pytest.raises(ValueError, match="raw audit"):
        sensitivity.analyze_audited_batch(run)
    assert not (folder / "SENSITIVITY_BY_CONDITION_PRESSURE.json").exists()
