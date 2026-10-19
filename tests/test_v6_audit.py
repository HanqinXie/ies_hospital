"""Focused v6 audit tests; fixture scores are not physical experiment results."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from tools.policy_redesign import audit
from hospital_ies.dataset_manifest import digest
from tools.control_improvement.ranking import TARGET_NAMES
from tools.reliability_first.closed_loop import _payload_binding


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _score(minutes=0.0, electric=0.0, *, full=True, observed_p0_failure=False):
    values = [0.0] * len(TARGET_NAMES)
    values[1], values[2] = minutes, electric
    if not full:
        values = [1 if observed_p0_failure else None] + [None] * (len(values) - 1)
    return {"full_horizon": full, "targets": values,
            "target_mask": [value is not None for value in values]}


def _batch_fixture(tmp_path, monkeypatch, *, observed_p0_failure=False):
    scenes = [{"id": f"scene_{i}", "condition_id": "GRID0_grid850_delay15",
               "root_family_id": "root_a", "pressure_point_index": i,
               "pressure_coordinate": i / 4, "experiment_role": "selection",
               "is_final_blind": False} for i in (0, 1)]
    manifest = {"scenarios": scenes,
                "splits": {s["id"]: {"group_id": "group_a"} for s in scenes},
                "options": {"seed": 19}, "signature": "manifest_fixture",
                "simulator_hash": "sim_fixture", "execution_contract_id": "contract_fixture",
                "execution_contract": {"name": "fixture"}}
    manifest_path = tmp_path / "manifest.json"
    _json(manifest_path, manifest)
    specs = [{"policy_id": "secure_milp", "kind": "secure"},
             {"policy_id": "candidate", "kind": "v6_feedback",
              "family": "port_residual", "theta": [0.0] * 6}]
    plan = {"signature": "plan_fixture", "manifest_path": str(manifest_path),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "scenario_ids": [s["id"] for s in scenes], "policy_specs": specs,
            "registered_pair_count": 4, "phase": "selection",
            "source_sha256": {"tools/control_improvement/branches.py":
                hashlib.sha256((audit.ROOT / "tools/control_improvement/branches.py").read_bytes()).hexdigest()}}
    plan_path = tmp_path / "registered_plan.json"
    _json(plan_path, plan)
    result_dir = tmp_path / "results"
    rows = []
    paths = {}
    for scene in scenes:
        for spec in specs:
            sid, pid = scene["id"], spec["policy_id"]
            unknown = sid == "scene_1" and pid == "candidate"
            primary = (_score(-3.0, -0.1) if pid == "candidate" and not unknown
                       else _score(full=not unknown,
                                   observed_p0_failure=unknown and observed_p0_failure))
            full = (_score(-4.0, -0.2) if pid == "candidate" and not unknown
                    else _score(full=not unknown,
                                observed_p0_failure=unknown and observed_p0_failure))
            payload = {
                "scenario": scene, "seed": 19, "initial_state": {"state": sid},
                "identity": {"scenario_id": sid, "policy_id": pid,
                             "manifest_signature": manifest["signature"],
                             "simulator_hash": manifest["simulator_hash"],
                             "execution_contract_id": manifest["execution_contract_id"],
                             "group_id": "group_a", "exogenous_stream_id": f"stream_{sid}"},
                "summary": {"status": "truncated" if unknown else "complete"},
                "score_primary": primary, "score_full": full,
                "decisions": [],
            }
            attempt = result_dir / "pairs" / digest([sid, pid]) / "attempt_0001"
            attempt.mkdir(parents=True)
            raw = attempt / "trajectory.json.gz"
            with gzip.open(raw, "wt", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            row = {"scenario_id": sid, "policy_id": pid, "kind": spec["kind"],
                   "status": payload["summary"]["status"], "terminal": True,
                   "phase": "selection", "plan_signature": plan["signature"],
                   "trajectory_path": raw.name,
                   "trajectory_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                   "primary_score": primary, "full_observation": full,
                   "binding": _payload_binding(payload, scene, manifest),
                   "command_counts": {"generated": 0}}
            _json(attempt / "result.json", row)
            rows.append(row)
            paths[(sid, pid)] = raw
    summary = {"plan_signature": plan["signature"], "registered_pair_count": 4,
               "recorded_pair_count": 4, "terminal_pair_count": 4,
               "matched_initial_state_and_stream": True,
               "results": rows}
    _json(result_dir / "summary_0001.json", summary)
    monkeypatch.setattr(audit, "verify_batch", lambda path: (plan, manifest))
    monkeypatch.setattr(audit, "HospitalIESEnv", lambda *args, **kwargs: object())
    monkeypatch.setattr(audit, "window_targets", lambda payload, start, horizon, env:
                        payload["score_primary"] if start == 60.0 else payload["score_full"])
    return plan_path, result_dir, paths


def test_audit_rechecks_raw_and_keeps_unknown_in_denominator(tmp_path, monkeypatch):
    plan, results, paths = _batch_fixture(tmp_path, monkeypatch)
    output = tmp_path / "AUDIT.json"
    markdown = tmp_path / "AUDIT.md"
    report = audit.audit_batch(plan, results, output_json=output,
                               output_markdown=markdown, include_action_effect=False)
    assert report["passed"] is True
    assert report["registered_pair_count"] == report["recomputed_pair_count"] == 4
    assert report["unknown_primary_pair_ids"] == ["scene_1/candidate"]
    metric = report["policy_comparisons"]["candidate"]["primary"]
    assert metric["registered_case_count"] == 2
    assert metric["complete_paired_case_count"] == 1
    assert metric["unknown_case_ids"] == ["scene_1"]
    assert metric["P0_failure_minutes_total_delta"] == -3.0
    assert report["policy_comparisons"]["candidate"]["worst_case_by_target"]["P0_failure_minutes"]["scenario_id"] == "scene_0"
    assert report["policy_comparisons"]["candidate"]["full_worst_case_by_target"]["P0_failure_minutes"]["candidate_minus_secure"] == -4.0
    subgroup = report["subgroups"]["by_root"][0]
    assert subgroup["registered_pressure_count"] == 2
    assert subgroup["complete_five_pressure_panel"] is False
    assert output.is_file() and markdown.is_file()
    assert report["pair_ledger"][0]["attempts"][0]["raw_sha256"] == hashlib.sha256(
        paths[("scene_0", "secure_milp")].read_bytes()).hexdigest()


def test_audit_keeps_observed_p0_failure_known_in_truncated_window(tmp_path, monkeypatch):
    plan, results, _ = _batch_fixture(tmp_path, monkeypatch,
                                      observed_p0_failure=True)
    report = audit.audit_batch(plan, results, include_action_effect=False)
    assert report["passed"] is True
    assert report["registered_pair_count"] == report["recomputed_pair_count"] == 4
    assert report["unknown_primary_pair_ids"] == ["scene_1/candidate"]
    row = next(item for item in report["pair_ledger"]
               if item["scenario_id"] == "scene_1" and item["policy_id"] == "candidate")
    assert row["recomputed_primary_score"]["targets"] == [1] + [None] * 18
    assert row["recomputed_primary_score"]["target_mask"] == [True] + [False] * 18


def test_audit_reports_tampered_raw_without_substituting_zero(tmp_path, monkeypatch):
    plan, results, paths = _batch_fixture(tmp_path, monkeypatch)
    paths[("scene_0", "candidate")].write_bytes(b"tampered raw")
    report = audit.audit_batch(plan, results, include_action_effect=False)
    assert report["passed"] is False
    assert any("raw SHA" in error for error in report["errors"])
    assert "scene_0" in report["policy_comparisons"]["candidate"]["primary"]["unknown_case_ids"]


def test_audit_detects_unmatched_binding(tmp_path, monkeypatch):
    plan, results, paths = _batch_fixture(tmp_path, monkeypatch)
    summary_path = results / "summary_0001.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["matched_initial_state_and_stream"] = False
    _json(summary_path, summary)
    report = audit.audit_batch(plan, results, include_action_effect=False)
    assert report["passed"] is False
    assert any("matched" in error for error in report["errors"])


def test_audit_rederives_match_from_raw_even_if_summary_claims_match(tmp_path, monkeypatch):
    plan_path, results, paths = _batch_fixture(tmp_path, monkeypatch)
    raw = paths[("scene_0", "candidate")]
    with gzip.open(raw, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    payload["initial_state"] = {"state": "different_candidate_initial_state"}
    with gzip.open(raw, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    result_path = raw.parent / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["trajectory_sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    result["binding"] = _payload_binding(payload, manifest["scenarios"][0], manifest)
    _json(result_path, result)
    summary_path = results / "summary_0001.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["results"] = [result if row["scenario_id"] == "scene_0" and
                          row["policy_id"] == "candidate" else row
                          for row in summary["results"]]
    _json(summary_path, summary)
    report = audit.audit_batch(plan_path, results, include_action_effect=False)
    assert report["passed"] is False
    assert report["matched_initial_state_and_stream"] is False
    assert any("matched" in error for error in report["errors"])
