"""Focused semantics for the four-scenario portable evidence bundle."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def _sample(category, *, origin="v7", complete=True, no_disturbance=False):
    return {"origin": origin, "analysis_class": category,
            "full_horizon": complete,
            "no_external_disturbance": no_disturbance,
            "is_final_blind": False}


def test_selection_requires_current_four_classes_and_a_new_normal_example():
    from tools.four_scenarios.package import classify_selected_examples

    selected = [_sample(name) for name in ("L1", "L2", "L3", "L4b")]
    with pytest.raises(ValueError, match="normal"):
        classify_selected_examples(selected)
    selected.append(_sample("N2", no_disturbance=True))
    with pytest.raises(ValueError, match="unknown"):
        classify_selected_examples(selected)
    selected.append(_sample("L4b", origin="v6_historical_unknown",
                            complete=False))
    roles = classify_selected_examples(selected)
    assert roles["new_class_examples"] == {"L1": 1, "L2": 1, "L3": 1, "L4b": 1}
    assert roles["new_no_disturbance_count"] == 1
    assert roles["historical_unknown_count"] == 1
    assert roles["new_unknown_count"] == 0


def test_historical_unknown_cannot_replace_current_class_or_become_new():
    from tools.four_scenarios.package import classify_selected_examples

    selected = [_sample(name) for name in ("L1", "L2", "L3")]
    selected += [_sample("N4", no_disturbance=True),
                 _sample("L4b", origin="v6_historical_unknown", complete=False)]
    with pytest.raises(ValueError, match="L4b"):
        classify_selected_examples(selected)
    selected.append(_sample("L4b"))
    selected.append(_sample("N4", origin="v7", complete=False))
    roles = classify_selected_examples(selected)
    assert roles["new_unknown_count"] == 1
    assert roles["historical_unknown_count"] == 1
    assert roles["new_class_examples"]["L4b"] == 1


def test_exact_two_window_19_target_recheck_preserves_unknown_nulls():
    from tools.four_scenarios.package import compare_scored_windows

    complete = {"full_horizon": True, "targets": [0] + [0.0] * 18,
                "target_mask": [True] * 19}
    truncated = {"full_horizon": False, "targets": [1] + [None] * 18,
                 "target_mask": [True] + [False] * 18}
    stored = {"primary_score": complete, "full_observation": truncated}
    receipt = compare_scored_windows(stored, complete, truncated)
    assert receipt["targets_per_window"] == 19
    assert receipt["unknown_windows"] == ["full_observation"]
    assert receipt["score_match"] is True
    damaged = json.loads(json.dumps(stored))
    damaged["full_observation"]["targets"][7] = 0.0
    damaged["full_observation"]["target_mask"][7] = True
    with pytest.raises(ValueError, match="full_observation"):
        compare_scored_windows(damaged, complete, truncated)


def test_archive_is_write_once_and_extracted_hashes_are_checked(tmp_path):
    from tools.four_scenarios.package import create_archive, verify_integrity

    archive = tmp_path / "repro.zip"
    create_archive(archive, {"evidence/one.csv": b"a,b\n1,2\n"},
                   {"kind": "four_scenarios_selected_raw"})
    with pytest.raises(FileExistsError):
        create_archive(archive, {"evidence/one.csv": b"replacement"},
                       {"kind": "four_scenarios_selected_raw"})
    extracted = tmp_path / "unpacked"
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(extracted)
    manifest = verify_integrity(extracted)
    assert manifest["kind"] == "four_scenarios_selected_raw"
    (extracted / "evidence/one.csv").write_bytes(b"modified")
    with pytest.raises(ValueError, match="differs"):
        verify_integrity(extracted)


def test_archive_rejects_unsafe_member_names(tmp_path):
    from tools.four_scenarios.package import create_archive

    with pytest.raises(ValueError, match="Unsafe"):
        create_archive(tmp_path / "bad.zip", {"../escape.txt": b"no"}, {})
    assert not (tmp_path / "bad.zip").exists()


def test_extracted_example_path_must_be_safe_and_listed(tmp_path):
    from tools.four_scenarios.package import _member

    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/data.json").write_text("{}", encoding="utf-8")
    assert _member(tmp_path, {"raw/data.json"}, "raw/data.json").is_file()
    with pytest.raises(ValueError, match="Unsafe"):
        _member(tmp_path, {"raw/data.json"}, "../outside.json")
    with pytest.raises(ValueError, match="unlisted"):
        _member(tmp_path, {"raw/data.json"}, "raw/other.json")


def test_historical_grid0_unknown_follows_its_registered_source_manifest():
    """The audited v6 training batch legitimately references the prior v5 source."""
    from tools.four_scenarios.package import _historical_unknown

    run = ROOT / "outputs/v6_policy_redesign_20261016_run01"
    batch = run / "training/expanded_30roots_p01234"
    if not (batch / "AUDIT_rechecked_v2.json").is_file():
        pytest.skip("Historical audited GRID0 raw is absent in this checkout")
    entries = {}
    example = _historical_unknown({
        "run_dir": str(run), "batch_dir": str(batch),
        "audit_path": str(batch / "AUDIT_rechecked_v2.json"),
        "scenario_id": "control_case_472b98e03cef29ae950d",
        "policy_id": "secure_milp",
    }, ROOT, entries)
    assert example["origin"] == "v6_historical_unknown"
    assert example["full_horizon"] is False
    assert example["raw_path"] in entries
    assert example["data_manifest_path"] in entries


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_build_indexes_all_registered_raw_but_embeds_only_selected(tmp_path):
    from hospital_ies.dataset_manifest import digest
    from tools.four_scenarios.package import (ANALYSIS_FILES, PHYSICAL_INPUTS,
                                               build_bundle, verify_integrity)

    root = tmp_path / "workspace"
    (root / "src/hospital_ies").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "src/hospital_ies/fixture.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tools/fixture.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tools/four_scenarios").mkdir()
    design_source = root / "tools/four_scenarios/data_design.py"
    design_source.write_text("x = 1\n", encoding="utf-8")
    files = []
    for name in PHYSICAL_INPUTS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        files.append({"path": name, "bytes": path.stat().st_size,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    literature = root / "inputs/literature/study.pdf"
    literature.parent.mkdir(parents=True)
    literature.write_bytes(b"historical literature, not a runtime input")
    files.append({"path": "inputs/literature/study.pdf",
                  "bytes": literature.stat().st_size,
                  "sha256": hashlib.sha256(literature.read_bytes()).hexdigest()})
    for name in ("requirements.txt", "requirements-training.txt"):
        path = root / name
        path.write_text("numpy>=1.24\n", encoding="utf-8")
        files.append({"path": name, "bytes": path.stat().st_size,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    run = root / "outputs/v7_four_scenarios_fixture"
    scenes = [{"id": "scene_" + name, "analysis_class": name,
               "root_family_id": "root_one", "is_final_blind": False,
               "experiment_role": "train", "exogenous_events": [] if name == "N2" else
               [{"kind": "fault"}]}
              for name in ("N2", "L1", "L2", "L3", "L4b")]
    manifest_path = run / "new_data/global_manifest.json"
    _write_json(manifest_path, {"manifest_version": "four_scenarios.v7.fixture",
                                "files": files, "execution_contract": {},
                                "four_scenario_source_sha256": {
                                    "tools/four_scenarios/data_design.py":
                                    hashlib.sha256(design_source.read_bytes()).hexdigest()},
                                "runtime": {"python": "3.10.6",
                                            "packages": {"gurobipy": "13.0.2"}},
                                "scenarios": scenes})
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    policies = ("secure_milp", "priority_milp")
    plan = {"manifest_path": str(manifest_path), "manifest_sha256": manifest_sha,
            "scenario_ids": [scene["id"] for scene in scenes],
            "policy_specs": [{"policy_id": pid} for pid in policies],
            "source_sha256": {"tools/fixture.py": hashlib.sha256(
                (root / "tools/fixture.py").read_bytes()).hexdigest()},
            "registered_pair_count": 10, "signature": "fixture_plan", "phase": "train"}
    plan_path = run / "main/registered_plan.json"
    _write_json(plan_path, plan)
    ledger = []
    for scene in scenes:
        for pid in policies:
            sid = scene["id"]
            unknown = sid == "scene_L4b" and pid == "priority_milp"
            status = "model_constraint_infeasible" if unknown else "completed"
            folder = run / "main/results/pairs" / digest([sid, pid]) / "attempt_0001"
            folder.mkdir(parents=True)
            raw = folder / "trajectory.json.gz"
            payload = {"scenario": scene, "identity": {"scenario_id": sid,
                      "policy_id": pid}, "summary": {"status": status}}
            with gzip.open(raw, "wt", encoding="utf-8") as stream:
                json.dump(payload, stream)
            score = {"full_horizon": not unknown,
                     "targets": [None] * 19 if unknown else [0] * 19}
            result = {"scenario_id": sid, "policy_id": pid, "status": status,
                      "terminal": True, "trajectory_path": "trajectory.json.gz",
                      "trajectory_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                      "binding": {"passed": True}, "plan_signature": "fixture_plan",
                      "primary_score": score, "full_observation": score}
            result_path = folder / "result.json"
            _write_json(result_path, result)
            ledger.append({"scenario_id": sid, "policy_id": pid,
                           "status": status, "latest_result_sha256":
                           hashlib.sha256(result_path.read_bytes()).hexdigest(),
                           "attempts": [{"attempt": "attempt_0001",
                                         "raw_sha256": result["trajectory_sha256"],
                                         "result_sha256": hashlib.sha256(
                                             result_path.read_bytes()).hexdigest()}]})
    audit_path = run / "main/AUDIT.json"
    _write_json(audit_path, {"passed": True,
                            "matched_initial_state_and_stream": True,
                            "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                            "manifest_sha256": manifest_sha,
                            "registered_pair_count": 10,
                            "terminal_pair_count": 10, "recomputed_pair_count": 10,
                            "pair_ledger": ledger})
    # A superseded but real raw attempt must remain visible in the external
    # index; it cannot be silently reduced to the audited latest 10 pairs.
    pair_dir = run / "main/results/pairs" / digest(["scene_L1", "secure_milp"])
    prior = pair_dir / "attempt_0000"
    prior.mkdir()
    prior_raw = prior / "trajectory.json.gz"
    with gzip.open(prior_raw, "wt", encoding="utf-8") as stream:
        json.dump({"older_attempt": True}, stream)
    prior_result = json.loads((pair_dir / "attempt_0001/result.json").read_text(
        encoding="utf-8"))
    prior_result["trajectory_sha256"] = hashlib.sha256(prior_raw.read_bytes()).hexdigest()
    _write_json(prior / "result.json", prior_result)
    for name in ANALYSIS_FILES:
        path = run / "analysis" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if path.suffix == ".json" else "a,b\n1,2\n",
                        encoding="utf-8")
    (run / "figures/final").mkdir(parents=True)
    (run / "figures/attempt_01").mkdir()
    (run / "figures/final/source.csv").write_text("t,y\n0,1\n", encoding="utf-8")
    (run / "figures/final/FIGURE_QA.json").write_text("{}\n", encoding="utf-8")
    (run / "figures/attempt_01/stale.csv").write_text(
        "t,y\n0,2\n", encoding="utf-8")
    (run / "FOUR_SCENARIOS_REPORT.md").write_text("fixture\n", encoding="utf-8")
    selected = [("scene_" + name, "secure_milp") for name in
                ("N2", "L1", "L2", "L3", "L4b")]
    selected.append(("scene_L4b", "priority_milp"))
    with pytest.raises(ValueError, match="Raw index.*audit attempt"):
        build_bundle(run, tmp_path / "unregistered.zip", selected,
                     workspace_root=root)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audited_prior = next(row for row in audit["pair_ledger"] if
                         row["scenario_id"] == "scene_L1" and
                         row["policy_id"] == "secure_milp")
    audited_prior["attempts"].insert(0, {
        "attempt": "attempt_0000",
        "raw_sha256": prior_result["trajectory_sha256"],
        "result_sha256": hashlib.sha256((prior / "result.json").read_bytes()).hexdigest(),
    })
    _write_json(audit_path, audit)
    archive = tmp_path / "fixture.zip"
    receipt = build_bundle(run, archive, selected, workspace_root=root)
    assert receipt["external_raw_indexed"] == 11
    assert receipt["selected_raw_count"] == 6
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(extracted)
    package = verify_integrity(extracted)
    index = json.loads((extracted / "EXTERNAL_RAW_SHA256_INDEX.json").read_text(
        encoding="utf-8"))
    assert len(index["rows"]) == 11
    assert index["registered_pair_count"] == 10
    assert sum(row["included_in_package"] for row in index["rows"]) == 6
    l1_secure = [row for row in index["rows"] if row["scenario_id"] == "scene_L1"
                 and row["policy_id"] == "secure_milp"]
    assert len(l1_secure) == 2
    assert all(row["included_in_package"] is row["latest_audited"]
               for row in l1_secure)
    assert len(package["examples"]) == 6
    assert (extracted / "figures/final/source.csv").is_file()
    assert (extracted / "figures/final/FIGURE_QA.json").is_file()
    assert not (extracted / "figures/attempt_01/stale.csv").exists()
    assert (extracted / "analysis/SCENARIO_RESULTS.csv").is_file()
    assert not (extracted / "inputs/literature/study.pdf").exists()
    assert package["external_literature_provenance"][0]["path"] == \
           "inputs/literature/study.pdf"
    assert (extracted / "requirements.txt").is_file()
    assert (extracted / "requirements-training.txt").is_file()
    runtime = json.loads((extracted / "evidence/RECORDED_RUNTIME.json").read_text(
        encoding="utf-8"))
    assert runtime["manifest_runtime"]["packages"]["gurobipy"] == "13.0.2"
    assert runtime["build_gurobipy_version"]
    readme = (extracted / "README_REPRODUCTION.md").read_text(encoding="utf-8")
    assert "pip install -r requirements.txt" in readme
    assert "gurobipy" in readme and "does not solve" in readme
    (root / "tools/fixture.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Registered source SHA"):
        build_bundle(run, tmp_path / "drift.zip", selected, workspace_root=root)
    (root / "tools/fixture.py").write_text("x = 1\n", encoding="utf-8")
    design_source.write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Four-scenario source SHA"):
        build_bundle(run, tmp_path / "design_drift.zip", selected,
                     workspace_root=root)


def test_package_cli_imports_from_workspace_without_pythonpath():
    completed = subprocess.run([sys.executable, "-B", "-m",
                                "tools.four_scenarios.package", "--help"],
                               cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", check=False)
    assert completed.returncode == 0, completed.stderr
    assert "build" in completed.stdout and "verify" in completed.stdout
