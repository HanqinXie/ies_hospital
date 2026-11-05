"""Multi-batch portability retains all attempts and reuses the saved-raw scorer."""
from __future__ import annotations

import copy
import csv
from datetime import datetime
import gzip
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import zipfile

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from hospital_ies.dataset_manifest import digest
from tools.four_scenarios.package import PHYSICAL_INPUTS
from tools.control_improvement.branches import window_targets

POLICIES = ("priority_milp", "secure_milp", "state_dispatch_v9")


def module():
    return importlib.import_module("tools.state_dispatch.package")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def score_env():
    plant = SimpleNamespace(gas_price_usd_per_mmbtu=1., steam_kWh_per_lb=1.,
                            boiler_eff=1., tariff={f"electricity_{v}_per_kWh": 0.
                                                   for v in ("offpeak", "midpeak", "onpeak")})
    return SimpleNamespace(plant=plant, hourly=pd.DataFrame({"timestamp": [datetime(2026, 1, 1)]}))


@pytest.fixture
def evidence(tmp_path):
    root = tmp_path / "workspace"
    source = root / "src/hospital_ies/fixture.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    tool = root / "tools/fixture.py"
    tool.parent.mkdir()
    tool.write_text("value = 2\n", encoding="utf-8")
    (root / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    files = []
    for name in PHYSICAL_INPUTS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        files.append(dict(path=name, bytes=path.stat().st_size, sha256=sha(path)))
    literature = root / "inputs/literature/reference.pdf"
    literature.parent.mkdir(parents=True)
    literature.write_bytes(b"not a runtime input")
    files.append(dict(path=literature.relative_to(root).as_posix(),
                      bytes=literature.stat().st_size, sha256=sha(literature)))
    history = root / "outputs/old_nn/weights.pt"
    history.parent.mkdir(parents=True)
    history.write_bytes(b"old neural weights must not be copied")
    run = root / "outputs/v9_fixture"
    batches, selected = [], []
    payloads = []
    for name, classes, phase in (("development", ("L1", "L2"), "development"),
                                  ("confirmation", ("L3", "L4b"), "confirmation"),
                                  ("diagnostic", ("unknown", "not_started"), "development")):
        policies = POLICIES if name != "diagnostic" else ("secure_milp",)
        scenes = [dict(id=f"{name}_{category}", analysis_class=category,
                       root_family_id="seen_root", is_final_blind=False,
                       experiment_role=phase, analysis_cohort=name,
                       exogenous_events=[]) for category in classes]
        manifest = run / name / "manifest.json"
        write(manifest, dict(files=files, scenarios=scenes, execution_contract={},
                             policy_search_final_12_backgrounds_unopened=True))
        plan = run / name / "registered_plan.json"
        write(plan, dict(scenario_ids=[s["id"] for s in scenes],
                         policy_specs=[dict(policy_id=p) for p in policies],
                         manifest_sha256=sha(manifest), phase=phase,
                         registered_pair_count=len(scenes)*len(policies), signature=f"plan_{name}",
                         source_sha256={"src/hospital_ies/fixture.py": sha(source)},
                         score_windows={"primary": [60, 420], "full": [0, 600]},
                         final_blind_opened=False))
        results = run / name / "results"
        results.mkdir(parents=True)
        ledger, rows = [], []
        for scene in scenes:
            for policy in policies:
                sid = scene["id"]
                if name == "diagnostic":
                    attempts = []
                    result_sha = None
                    status = None
                    if scene["analysis_class"] == "unknown":
                        attempt = results / "pairs" / digest([sid, policy]) / "attempt_0001"
                        result = attempt / "result.json"
                        write(result, dict(scenario_id=sid, policy_id=policy, status="exception",
                                           terminal=False, plan_signature=f"plan_{name}", error_path="error.json"))
                        write(attempt / "error.json", dict(error_type="RuntimeError", message="retained"))
                        result_sha, status = sha(result), "exception"
                        attempts = [dict(attempt="attempt_0001", raw_sha256=None,
                                         result_sha256=result_sha, error_sha256=sha(attempt / "error.json"),
                                         status=status, terminal=False)]
                    ledger.append(dict(scenario_id=sid, policy_id=policy, attempts=attempts,
                                       status=status, terminal=False, latest_result_sha256=result_sha,
                                       score_recomputed=False, binding_passed=None))
                    continue
                failed = scene["analysis_class"] == "L1" and policy == "secure_milp"
                loss = 2. if failed else 0.
                payload = dict(scenario=scene, identity=dict(scenario_id=sid, policy_id=policy),
                    summary=dict(status="completed", observed_until_minute=600., requested_end_minute=600.),
                    segments=[dict(start_minute=0., end_minute=600., hour_of_year=1,
                        cost_USD_step=0., temperature=dict(P0=21., P1=24., P2=24.),
                        action=dict(gt_fuel_MMBtu_h=0., boiler_steam_lbph=0., grid_import_kW=0., diesel_kW=0.),
                        unserved_kW=[[loss, 0., 0.], [0., 0., 0.], [0., 0., 0.], [0., 0., 0.]],
                        classification=dict(p0_guarantee_failure=failed))], events=[])
                folder = results / "pairs" / digest([sid, policy]) / "attempt_0001"
                folder.mkdir(parents=True)
                raw = folder / "trajectory.json.gz"
                with gzip.open(raw, "wt", encoding="utf-8") as stream:
                    json.dump(payload, stream)
                result = folder / "result.json"
                primary = window_targets(payload, 60., 360., score_env())
                full = window_targets(payload, 0., 600., score_env())
                result_value = dict(scenario_id=sid, policy_id=policy, status="completed", terminal=True,
                    binding=dict(passed=True), plan_signature=f"plan_{name}", trajectory_path=raw.name,
                    trajectory_sha256=sha(raw), primary_score=primary, full_observation=full)
                write(result, result_value)
                ledger.append(dict(scenario_id=sid, policy_id=policy, status="completed", terminal=True,
                    latest_result_sha256=sha(result), binding_passed=True, score_recomputed=True,
                    full_observation_horizon=True, recomputed_primary_score=primary,
                    recomputed_full_observation=full,
                    attempts=[dict(attempt="attempt_0001", raw_sha256=sha(raw),
                                   result_sha256=sha(result), status="completed", terminal=True)]))
                row = dict(scenario_id=sid, policy_id=policy, full_full_horizon="True",
                           full_P0_failure=int(failed), full_P0_failure_minutes=600. if failed else 0.,
                           full_P0_e_ENS_kWh=loss*10., gt_protection_trip_count=0)
                rows.append(row)
                selected.append(dict(batch=name, scenario_id=sid, policy_id=policy,
                                     expected_raw_sha256=sha(raw)))
                payloads.append((payload, result_value, row))
        audit = run / name / "AUDIT.json"
        write(audit, dict(passed=name != "diagnostic", matched_initial_state_and_stream=name != "diagnostic",
                          registered_pair_count=len(scenes)*len(policies),
                          terminal_pair_count=0 if name == "diagnostic" else len(ledger),
                          recomputed_pair_count=0 if name == "diagnostic" else len(ledger),
                          manifest_sha256=sha(manifest), plan_sha256=sha(plan), pair_ledger=ledger,
                          final_blind_opened=False, errors=["unresolved"] if name == "diagnostic" else []))
        analysis = None
        if rows:
            analysis = run / name / "analysis"
            analysis.mkdir()
            with (analysis / "TRAJECTORY_RESULTS.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
        batches.append(dict(name=name, manifest=str(manifest), plan=str(plan), results=str(results),
                            audit=str(audit), analysis=str(analysis) if analysis else None))
    batches_path = run / "review/batches.json"
    write(batches_path, batches)
    selection = run / "review/selection.json"
    write(selection, dict(selected_pairs=selected, required_policies=list(POLICIES),
                           require_failure_and_nonfailure=True))
    report = run / "STATE_DISPATCH_REPORT.md"
    report.write_text("# Fixture report\n", encoding="utf-8")
    for folder in ("design", "review", "analysis", "figures", "logs"):
        (run / folder).mkdir(exist_ok=True)
    (run / "figures/source.csv").write_text("minute,kW\n0,1\n", encoding="utf-8")
    (run / "RUN_LEDGER.jsonl").write_text('{"event":"fixture"}\n', encoding="utf-8")
    return dict(root=root, run=run, batches=batches_path, selection=selection, report=report,
                definitions=batches, payloads=payloads)


def test_multiple_batches_keep_registered_unknown_and_not_started_denominators(evidence):
    batches = module().load_batches(evidence["batches"], workspace_root=evidence["root"])
    assert len(batches) == 3
    diagnostic = batches[-1]
    assert diagnostic["denominators"]["registered_pairs"] == 2
    assert diagnostic["denominators"]["attempts_started"] == 1
    assert diagnostic["denominators"]["unknown_pairs"] == 2
    assert diagnostic["denominators"]["not_started_pairs"] == 1
    assert diagnostic["audit"]["passed"] is False


def test_attempt_index_retains_no_raw_exception_and_rejects_unindexed_raw(evidence):
    mod = module()
    batches = mod.load_batches(evidence["batches"], workspace_root=evidence["root"])
    rows = mod.index_batch_attempts(batches[-1], evidence["root"])
    assert len(rows) == 1 and rows[0]["raw_sha256"] is None
    assert rows[0]["status"] == "exception" and rows[0]["error_sha256"]
    rogue = Path(evidence["definitions"][-1]["results"]) / "rogue/trajectory.json.gz"
    rogue.parent.mkdir(); rogue.write_bytes(b"unindexed")
    with pytest.raises(ValueError, match="Unindexed"):
        mod.index_batch_attempts(batches[-1], evidence["root"])


def test_selection_requires_complete_matching_and_frozen_strategy_across_four_classes(evidence):
    mod = module()
    batches = mod.load_batches(evidence["batches"], workspace_root=evidence["root"])
    selection = json.loads(evidence["selection"].read_text(encoding="utf-8"))
    assert mod.validate_selection(selection, batches)["selected_raw_count"] == 12
    selection["selected_pairs"].pop()
    with pytest.raises(ValueError, match="matched"):
        mod.validate_selection(selection, batches)


def test_selection_rejects_wrong_raw_sha_or_missing_failure(evidence):
    mod = module()
    batches = mod.load_batches(evidence["batches"], workspace_root=evidence["root"])
    selection = json.loads(evidence["selection"].read_text(encoding="utf-8"))
    selection["selected_pairs"][0]["expected_raw_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="SHA"):
        mod.validate_selection(selection, batches)
    selection = json.loads(evidence["selection"].read_text(encoding="utf-8"))
    for batch in batches:
        for row in batch["ledger"].values():
            if row.get("recomputed_full_observation"):
                row["recomputed_full_observation"]["targets"][0] = 0
    with pytest.raises(ValueError, match="failure"):
        mod.validate_selection(selection, batches)


def test_build_copies_selected_raw_and_full_audits_without_history_weights(evidence, tmp_path):
    mod = module()
    archive = tmp_path / "fixture.zip"
    receipt = mod.build_bundle(evidence["run"], archive, evidence["batches"],
                              evidence["selection"], evidence["report"],
                              workspace_root=evidence["root"])
    assert receipt["registered_pair_count"] == 14
    assert receipt["selected_raw_count"] == 12
    assert receipt["raw_file_count"] == 12 and receipt["attempt_count"] == 13
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(extracted)
    package = mod.verify_integrity(extracted)
    assert package["kind"] == "v9_state_dispatch_selected_raw"
    assert (extracted / "run/STATE_DISPATCH_REPORT.md").is_file()
    assert (extracted / "batches/diagnostic/evidence/AUDIT.json").is_file()
    assert not (extracted / "inputs/literature/reference.pdf").exists()
    assert not list(extracted.rglob("*.pt"))
    index = json.loads((extracted / mod.INDEX_NAME).read_text(encoding="utf-8"))
    assert index["registered_pair_count"] == 14
    assert len(index["attempts"]) == 13 and len(index["pairs"]) == 14
    assert sum(item["raw_sha256"] is None for item in index["attempts"]) == 1
    assert package["batches"][-1]["audit_passed"] is False
    readme = (extracted / "README_REPRODUCTION.md").read_text(encoding="utf-8")
    assert "--batch confirmation --scene confirmation_L3" in readme
    assert "requirements-training.txt gurobipy" in readme
    with pytest.raises(FileExistsError):
        mod.build_bundle(evidence["run"], archive, evidence["batches"],
                         evidence["selection"], evidence["report"], workspace_root=evidence["root"])
    (extracted / "run/STATE_DISPATCH_REPORT.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        mod.verify_integrity(extracted)


def test_original_19_target_scorer_and_analysis_fields_detect_saved_score_tamper(evidence):
    mod = module()
    payload, result, row = evidence["payloads"][0]
    checked = mod.rescore_selected(payload, result, row, score_env())
    assert checked["targets_per_window"] == 19 and checked["analysis_subset_match"]
    bad = copy.deepcopy(result)
    bad["full_observation"]["targets"][2] += 1
    with pytest.raises(ValueError, match="differs"):
        mod.rescore_selected(payload, bad, row, score_env())
    bad_row = {**row, "gt_protection_trip_count": 1}
    with pytest.raises(ValueError, match="analysis subset"):
        mod.rescore_selected(payload, result, bad_row, score_env())


def test_report_inside_review_directory_uses_the_same_immutable_snapshot(evidence, tmp_path):
    report = evidence["run"] / "review/final_report.md"
    report.write_bytes(evidence["report"].read_bytes())
    receipt = module().build_bundle(evidence["run"], tmp_path / "review_report.zip",
        evidence["batches"], evidence["selection"], report, workspace_root=evidence["root"])
    assert receipt["selected_raw_count"] == 12


def test_build_embeds_exact_preregistered_preference_for_relocated_registration(evidence, tmp_path):
    definition = evidence["definitions"][0]
    preference = evidence["root"] / "preferences/frozen.json"
    write(preference, {"preference": "frozen"})
    plan_path = Path(definition["plan"])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan.update(preference_path=str(preference), preference_sha256=sha(preference))
    write(plan_path, plan)
    audit_path = Path(definition["audit"])
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["plan_sha256"] = sha(plan_path)
    write(audit_path, audit)
    archive = tmp_path / "preference.zip"
    module().build_bundle(evidence["run"], archive, evidence["batches"],
        evidence["selection"], evidence["report"], workspace_root=evidence["root"])
    extracted = tmp_path / "preference_package"
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(extracted)
    package = module().verify_integrity(extracted)
    first = package["batches"][0]
    assert (extracted / first["preference_file"]).read_bytes() == preference.read_bytes()
    assert first["preference_sha256"] == sha(preference)


def test_batches_reject_duplicate_names_changed_bindings_and_final_scenes(evidence):
    mod = module()
    rows = copy.deepcopy(evidence["definitions"])
    write(evidence["batches"], rows + rows[:1])
    with pytest.raises(ValueError, match="Duplicate"):
        mod.load_batches(evidence["batches"], workspace_root=evidence["root"])
    write(evidence["batches"], rows)
    Path(rows[0]["manifest"]).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="binding"):
        mod.load_batches(evidence["batches"], workspace_root=evidence["root"])


def test_verification_requires_the_extracted_copy_of_its_own_source(tmp_path):
    with pytest.raises(ValueError, match="package source"):
        module().verify_bundle(tmp_path)
