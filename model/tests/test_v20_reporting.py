"""Saved-report denominators and exact interval unions without any replay."""
from pathlib import Path
import copy
import json
import hashlib

import pytest

from scripts import v20_report as report


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf8")


def job(index=0, cohort="main"):
    return dict(name=f"v20_{cohort}_{index:03d}", cohort=cohort,
                source_proposal="MAIN_FINAL_R1_PROPOSAL.json", policies=["E"],
                scene=dict(id=f"scene_{index}",canonical_class="L1",source_day_index=5,
                           duration_steps=40,step_minutes=15.,physical_scenario_signature=f"physical_{index}"))


def saved_pair(run, definition, complete=True, observed=600.):
    directory = run / "experiments" / definition["name"]
    spec = dict(policy_id="E_M20",display_alias="E",kind="engineering",config={},source_policy_id="old_E")
    manifest = dict(signature="manifest",scenarios=[definition["scene"]],execution_model_id="M20_load_accounting")
    write(directory / "manifest.json", manifest)
    plan = dict(model_id="M20_load_accounting",model_config={},source_sha256={"x":"a"*64},
                policy_specs=[spec],phase=definition["cohort"],manifest_signature="manifest",
                manifest_sha256=report.sha(directory / "manifest.json"),registered_pairs=1)
    write(directory / "plan.json",plan)
    carrier = {p:{c:dict(ENS_kWh=0.,disconnected_ENS_kWh=0.,connected_unserved_ENS_kWh=0.,
                         material_minutes=0.,loss_runs=[]) for c in ("e","h","c")} for p in ("P0","P1","P2","P3")}
    carrier["P0"]["e"].update(ENS_kWh=2.,material_minutes=10.,loss_runs=[dict(start_minute=0.,end_minute=10.)])
    carrier["P0"]["c"].update(ENS_kWh=3.,material_minutes=10.,loss_runs=[dict(start_minute=5.,end_minute=15.)])
    service = {p:{c:dict(requested_kWh=10.,delivered_kWh=9.,ENS_kWh=1.,disconnected_ENS_kWh=.4,
                         connected_unserved_ENS_kWh=.6) for c in ("e","h","c")} for p in carrier}
    ledger = dict(complete=complete,window_minutes=[0.,600.],observed_finish_minute=observed,observed_prefix=service,
                  all_registered=service if complete else None,actual_source_integrals={},
                  DG_budget_start_kWh_equivalent=850.,DG_budget_finish_kWh_equivalent=800.,observed_DG_exhaustion_minutes=[])
    window = dict(window_minutes=[0.,600.],complete=complete,observed_prefix=dict(carrier_service=carrier,
                  economics=dict(actual_energy_fee_USD=7.),temperature={}),all_registered=None)
    analysis = dict(execution_model_id="M20_load_accounting",observed_until_minute=observed,
                    v18=dict(windows={"full":window,"registered_full":copy.deepcopy(window)}),
                    v20=dict(episode_audit=dict(status="passed",issues=[]),service_ledgers={"full":ledger,"registered_full":copy.deepcopy(ledger)}))
    record = dict(scenario_id=definition["scene"]["id"],policy_id=spec["policy_id"],model_id=plan["model_id"],
                  manifest_signature="manifest",status="completed" if complete else "error",analysis=analysis)
    path = directory / "pairs" / (record["scenario_id"]+"__"+record["policy_id"]) / "RESULT.json"
    write(path,record)
    (directory / "ATTEMPTS.jsonl").write_text("\n".join(json.dumps(dict(event=e,scenario_id=record["scenario_id"],policy_id=record["policy_id"],status=record["status"],model_id=record["model_id"],analysis_status="saved",result_sha256=report.sha(path))) for e in ("started","finished")),encoding="utf8")
    evidence = dict(same_initial_state=True,same_exogenous_definition=True,
                    initial_state_sha256=["a"*64],applied_exogenous_definition_sha256=["b"*64])
    write(directory / "BATCH_RESULT.json",dict(source_unchanged=True,manifest_sha256=report.sha(directory / "manifest.json"),
          plan_sha256=report.sha(directory / "plan.json"),matching_evidence={record["scenario_id"]:evidence}))
    return record,path


def test_union_is_clipped_overlap_union_and_rejects_nonfinite():
    runs=[dict(start_minute=-2.,end_minute=5.),dict(start_minute=3.,end_minute=8.),dict(start_minute=12.,end_minute=20.)]
    assert report.union_minutes(runs,0.,15.) == 11.
    with pytest.raises(ValueError):
        report.union_minutes([dict(start_minute=0.,end_minute=float("nan"))],0.,15.)


def test_checkpoint_keeps_not_registered_and_not_started_denominator(tmp_path):
    run=tmp_path/"v20";jobs=[job(0),job(1),job(2,"fixed8")]
    saved_pair(run,jobs[0])
    result=report.collect_campaign(run,jobs=jobs)
    assert result["summary"]["expected_pairs"] == 3
    assert len(result["status"]) == 3
    assert result["summary"]["not_started_pairs"] == 2
    assert result["summary"]["new_independent_background_count"] == 0
    assert result["summary"]["fixed_eight_backgrounds_are_seen_rechecks"] is True
    main=next(row for row in result["windows"] if row["batch"]==jobs[0]["name"] and row["window"]=="full")
    assert main["P0_material_union_minutes"] == 15.
    assert main["P0_e_ENS_kWh"] == 2.
    missing=next(row for row in result["windows"] if row["batch"]==jobs[1]["name"] and row["window"]=="full")
    assert missing["actual_energy_fee_USD"] is None
    assert missing["P0_material_union_minutes"] is None
    service=next(row for row in result["service"] if row["batch"]==jobs[0]["name"] and row["window"]=="full" and row["priority"]=="P0" and row["carrier"]=="e")
    assert service["requested_kWh"] == 10.


def test_partial_saved_service_is_not_full_window_zero_or_estimate(tmp_path):
    run=tmp_path/"v20";definition=job();saved_pair(run,definition,complete=False,observed=300.)
    result=report.collect_campaign(run,jobs=[definition])
    row=next(row for row in result["service"] if row["priority"]=="P0" and row["carrier"]=="e")
    assert row["observed_requested_kWh"] == 10.
    assert row["requested_kWh"] is None and row["delivered_kWh"] is None
    assert result["summary"]["unknown_full_pairs"] == 1


def test_old_compact_request_and_delivery_are_unknown_not_reconstructed(tmp_path):
    run=tmp_path/"v20";definition=job();record,_=saved_pair(run,definition)
    old_record=copy.deepcopy(record);old_record["model_id"]="M19_model_closeout"
    old_record["analysis"].pop("v20")
    old_record["analysis"]["execution_model_id"]="M19_model_closeout"
    old=report.window_metrics(old_record,"full",[0.,600.],expected_model="M19_model_closeout")
    assert old["P0_e_requested_kWh"] is None
    assert old["P0_e_delivered_kWh"] is None
    assert old["P0_e_ENS_kWh"] == 2.
    assert old["actual_energy_fee_USD"] == 7.


def test_saved_full_window_needs_exact_bounds_and_ledger_completion(tmp_path):
    record,_=saved_pair(tmp_path/"v20",job())
    record["analysis"]["v20"]["service_ledgers"]["full"]["complete"]=False
    row=report.window_metrics(record,"full",[0.,600.])
    assert row["P0_e_requested_kWh"] is None
    assert row["observed_P0_e_requested_kWh"] == 10.
    wrong=report.window_metrics(record,"full",[0.,675.])
    assert wrong["complete"] is False
    assert wrong["P0_e_ENS_kWh"] is None
    assert wrong["DG_budget_start_kWh_equivalent"] is None
    assert wrong["DG_budget_finish_kWh_equivalent"] is None
    assert wrong["observed_DG_exhaustion_minutes"] is None


def test_manifest_drift_and_ambiguous_old_matches_cannot_be_compared(tmp_path):
    run=tmp_path/"v20";definition=job();saved_pair(run,definition)
    directory=run/"experiments"/definition["name"]
    manifest=json.loads((directory/"manifest.json").read_text())
    manifest["scenarios"][0]["duration_steps"]+=1
    write(directory/"manifest.json",manifest)
    result=report.collect_campaign(run,jobs=[definition])
    assert result["status"][0]["registration_valid"] is False
    assert result["status"][0]["comparison_eligible"] is False
    assert result["summary"]["unknown_full_pairs"] == 1


def old_saved_pair(root,record,definition,suffix="000"):
    directory=root/report.OLD_REL/"experiments"/("main_final_r1_"+suffix)
    spec=dict(policy_id=report.OLD_IDS["E"],kind="engineering",config={})
    manifest=dict(signature="old_manifest",scenarios=[definition["scene"]],execution_model_id="M19_model_closeout")
    write(directory/"manifest.json",manifest)
    plan=dict(model_id="M19_model_closeout",model_config={},policy_specs=[spec],registered_pairs=1,
              manifest_signature="old_manifest",manifest_sha256=report.sha(directory/"manifest.json"))
    write(directory/"plan.json",plan)
    evidence=dict(same_initial_state=True,same_exogenous_definition=True,initial_state_sha256=["c"*64],applied_exogenous_definition_sha256=["d"*64])
    write(directory/"BATCH_RESULT.json",dict(source_unchanged=True,manifest_sha256=report.sha(directory/"manifest.json"),
          plan_sha256=report.sha(directory/"plan.json"),matching_evidence={record["scenario_id"]:evidence}))
    record=copy.deepcopy(record);record.update(model_id="M19_model_closeout",policy_id=spec["policy_id"],manifest_signature="old_manifest")
    audit=record["analysis"]["v20"]["episode_audit"]
    record["analysis"].pop("v20")
    record["analysis"]["v19"]=dict(episode_audit=audit)
    record["analysis"]["execution_model_id"]="M19_model_closeout"
    path=directory/"pairs"/(record["scenario_id"]+"__"+record["policy_id"])/"RESULT.json"
    write(path,record)
    (directory/"ATTEMPTS.jsonl").write_text("\n".join(json.dumps(dict(event=e,scenario_id=record["scenario_id"],policy_id=record["policy_id"],status=record["status"],model_id=record["model_id"],analysis_status="saved",result_sha256=report.sha(path))) for e in ("started","finished")),encoding="utf8")
    return path


def test_baseline_comparison_keeps_m19_identity_and_rejects_duplicate_matches(tmp_path):
    run=tmp_path/"v20";definition=job();record,_=saved_pair(run,definition)
    baseline=tmp_path/"old";old_saved_pair(baseline,record,definition)
    result=report.collect_campaign(run,jobs=[definition],baseline_root=baseline)
    row=next(row for row in result["comparisons"] if row["window"]=="full")
    assert row["comparison_valid"] is True
    assert row["old_model_id"] == "M19_model_closeout"
    assert row["new_model_id"] == "M20_load_accounting"
    assert row["old_P0_e_requested_kWh"] is None
    assert row["delta_P0_e_ENS_kWh"] == 0.
    old_saved_pair(baseline,record,definition,"001")
    ambiguous=report.collect_campaign(run,jobs=[definition],baseline_root=baseline)
    row=next(row for row in ambiguous["comparisons"] if row["window"]=="full")
    assert row["baseline_candidate_count"] == 2
    assert row["comparison_valid"] is False
    assert row["delta_P0_e_ENS_kWh"] is None


@pytest.mark.parametrize("problem",["status","error","analysis_error","audit_issues","audit_missing","duplicate_start","duplicate_finish","finish_sha","malformed_journal"])
def test_numeric_complete_but_unqualified_records_never_enter_comparisons_or_aggregates(tmp_path,problem):
    run=tmp_path/"v20";definition=job();record,path=saved_pair(run,definition)
    if problem=="status":record["status"]="error"
    if problem=="error":record["error"]=dict(message="saved physical failure")
    if problem=="analysis_error":record["analysis_error"]=dict(message="saved analysis failure")
    if problem=="audit_issues":record["analysis"]["v20"]["episode_audit"]=dict(status="issues_recorded",issues=["actual conservation issue"])
    if problem=="audit_missing":record["analysis"]["v20"].pop("episode_audit")
    write(path,record)
    journal=path.parents[2]/"ATTEMPTS.jsonl"
    events=[json.loads(line) for line in journal.read_text().splitlines()]
    for row in events:
        if row["event"]=="finished":row.update(result_sha256=report.sha(path),status=record["status"])
    if problem=="duplicate_start":events.append(copy.deepcopy(events[0]))
    if problem=="duplicate_finish":events.append(copy.deepcopy(events[1]))
    if problem=="finish_sha":events[1]["result_sha256"]="0"*64
    journal.write_text("\n".join(json.dumps(row) for row in events),encoding="utf8")
    if problem=="malformed_journal":
        with journal.open("a",encoding="utf8") as stream:stream.write("\n{partial_unknown_record")
    result=report.collect_campaign(run,jobs=[definition])
    status=result["status"][0]
    assert status["comparison_eligible"] is False
    assert status["execution_qualified"] is False
    assert status["registered_full_complete"] is True
    assert result["summary"]["unknown_full_pairs"] == 0
    assert result["summary"]["numeric_complete_but_disqualified_pairs"] == 1
    aggregate=next(row for row in result["aggregates"] if row["canonical_class"]=="ALL")
    assert aggregate["eligible_complete_pairs"] == 0
    assert aggregate["actual_energy_fee_USD"] is None
    observed=next(row for row in result["windows"] if row["window"]=="full")
    assert observed["actual_energy_fee_USD"] == 7.


def test_old_audit_issues_disqualify_comparison_without_discarding_observation(tmp_path):
    run=tmp_path/"v20";definition=job();record,_=saved_pair(run,definition)
    baseline=tmp_path/"old";path=old_saved_pair(baseline,record,definition)
    old=json.loads(path.read_text());old["analysis"]["v19"]["episode_audit"]=dict(status="issues_recorded",issues=["bad balance"])
    write(path,old)
    journal=path.parents[2]/"ATTEMPTS.jsonl";events=[json.loads(line) for line in journal.read_text().splitlines()]
    events[1]["result_sha256"]=report.sha(path);journal.write_text("\n".join(json.dumps(row) for row in events))
    result=report.collect_campaign(run,jobs=[definition],baseline_root=baseline)
    row=next(row for row in result["comparisons"] if row["window"]=="full")
    assert row["comparison_valid"] is False
    assert row["old_complete"] is True
    assert row["old_actual_energy_fee_USD"] == 7.
    assert row["delta_actual_energy_fee_USD"] is None


def test_cross_batch_strategy_comparison_requires_same_runtime_inputs():
    def row(alias,inventory):
        return dict(cohort="normal",scenario_id="same",alias=alias,batch=alias,canonical_class="N2",
            window="registered_full",window_start_minute=0.,window_end_minute=1440.,is_primary_window=True,
            complete=True,comparison_eligible=True,actual_initial_state_sha256="reset",actual_exogenous_sha256="events",
            source_binding_sha256="source",model_config_sha256="config",runtime_inputs_sha256=inventory,
            physical_scenario_signature="physical")
    values=[row("E","inputs_a"),row("K1","inputs_b")]
    assert report.strategy_pairs(values)[0]["comparison_valid"] is False
    values[1]["runtime_inputs_sha256"]="inputs_a"
    assert report.strategy_pairs(values)[0]["comparison_valid"] is True
