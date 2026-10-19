"""Synthetic successor proof guards; no waiting, collection or science."""
import copy
import importlib.util
from pathlib import Path
import sys
import pytest

HERE=Path(__file__).resolve().parent

def module():
    if '_normal03_successor_tested' not in sys.modules:
        spec=importlib.util.spec_from_file_location('_normal03_successor_tested',HERE/'normal_window_analysis.py')
        m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    return sys.modules['_normal03_successor_tested']


def predecessor_fixture():
    names=[f'v20_normal_{i:03d}' for i in range(12) if i not in (8,9)]
    normal=dict(name='normal03',exit_code=125)
    s3=dict(status='failed_unknown',source_modified=False,all_attempts_preserved=True,
        checkpoint=dict(registered=406,actual_started=405,actual_finished=405,active=0,not_started=1,failed_terminal_journals=[]))
    old=dict(mode='run',exit_code=125,state='explicit_stop_new_launches',existing_qualified_skipped=['v20_normal_009'],
        pending_not_started=['v20_normal_008'],actual_scientific_children_started=10,
        completed=[dict(batch=n,passed=True,exit_code=0) for n in names],
        successful_Popen_proofs=[dict(batch=n,pid=100+i,successful_Popen=True,owned_child_drained=True,exit_code=0) for i,n in enumerate(names)])
    return normal,s3,old


def test_intentional125_and_failedS3_are_preserved_not_relabelled_success():
    proof=module().validate_predecessor_stop(*predecessor_fixture())
    assert proof['normal03_exit_code']==125 and proof['normal_009_resource_status']=='unknown'
    assert proof['canonical02_measured_batches']==10 and proof['pending_new008']==['v20_normal_008']


@pytest.mark.parametrize('mutation',['normal_success','wrong_capture','S3_success','S3_active','S3_only404','S3_bad_journal',
    'source_changed','old_success','wrong_stop_state','old_11','009_measured','008_already_ran','extra_pending','missing_completion','duplicate_launch','bad_pid'])
def test_predecessor_stop_never_erases_or_expands_real_attempts(mutation):
    n,s,o=predecessor_fixture()
    if mutation=='normal_success':n['exit_code']=0
    elif mutation=='wrong_capture':n['name']='normal02'
    elif mutation=='S3_success':s['status']='passed'
    elif mutation=='S3_active':s['checkpoint']['active']=1
    elif mutation=='S3_only404':s['checkpoint']['actual_finished']=404
    elif mutation=='S3_bad_journal':s['checkpoint']['failed_terminal_journals']=['bad']
    elif mutation=='source_changed':s['source_modified']=True
    elif mutation=='old_success':o['exit_code']=0
    elif mutation=='wrong_stop_state':o['state']='all_twelve_strictly_qualified'
    elif mutation=='old_11':o['actual_scientific_children_started']=11
    elif mutation=='009_measured':o['completed'][0]['batch']='v20_normal_009'
    elif mutation=='008_already_ran':o['successful_Popen_proofs'][0]['batch']='v20_normal_008'
    elif mutation=='extra_pending':o['pending_not_started'].append('v20_normal_007')
    elif mutation=='missing_completion':o['completed'].pop()
    elif mutation=='duplicate_launch':o['successful_Popen_proofs'][-1]=copy.deepcopy(o['successful_Popen_proofs'][0])
    elif mutation=='bad_pid':o['successful_Popen_proofs'][0]['pid']=None
    with pytest.raises(ValueError):module().validate_predecessor_stop(n,s,o)


def test_no_S4_evidence_is_not_permission_to_import_numerical_collector():
    assert module().assess_successor_readiness(s4_exists=False,normal_terminals=12,report_exists=True)['ready'] is False


def test_no_persistent_wait_mode_exists_for_new03():
    text=(HERE/'normal_window_analysis.py').read_text(encoding='utf-8-sig')
    assert "mode.add_argument('--wait'" not in text, 'New03 must provide no heavy persistent wait path'



def s4_fixture():
    summary=dict(status='passed',actual_scientific_children_started=1,last_child=dict(batch='v20_normal_008',pid=800,
        successful_Popen=True,owned_child_drained=True,exit_code=0,resource_result_sha256='a'*64))
    proof=dict(schema='hospital_ies.v20.publication_final_campaign_qualification.v1',status='passed',model_id='M20_load_accounting',
        actual_complete_batches=148,actual_complete_pairs=406,actual_started_attempts=406,actual_finished_attempts=406,
        failed_or_unknown_canonical_pairs=0,exact_registered_duration_and_service_ledger=True,actual_reset_matching=True,
        actual_applied_streams_recomputed=True,raw_files_opened=0,annual_values_parsed=0,physical_episodes_started=0,sealed_final12='unopened')
    return summary,proof


def test_S4_is_one_new008_child_plus_all406_strict_metadata_not406_new_replays():
    value=module().validate_successor_final(*s4_fixture())
    assert value['new008_actual_child_count']==1 and value['actual_complete_pairs']==406


@pytest.mark.parametrize('mutation',['status','two_children','pid_none','not_owned','not_drained','exit_nonzero','bad_resource_sha',
    'wrong_model','405','147','unknown','missing_ledger','missing_reset','unrecomputed_stream','opened_raw','new_qualification_physics'])
def test_new_successor_must_prove_one_real008_and_all406_qualified(mutation):
    s,p=s4_fixture()
    if mutation=='status':s['status']='failed_unknown'
    elif mutation=='two_children':s['actual_scientific_children_started']=2
    elif mutation=='pid_none':s['last_child']['pid']=None
    elif mutation=='not_owned':s['last_child']['successful_Popen']=False
    elif mutation=='not_drained':s['last_child']['owned_child_drained']=False
    elif mutation=='exit_nonzero':s['last_child']['exit_code']=1
    elif mutation=='bad_resource_sha':s['last_child']['resource_result_sha256']='unknown'
    elif mutation=='wrong_model':p['model_id']='M19_model_closeout'
    elif mutation=='405':p['actual_complete_pairs']=405
    elif mutation=='147':p['actual_complete_batches']=147
    elif mutation=='unknown':p['failed_or_unknown_canonical_pairs']=1
    elif mutation=='missing_ledger':p['exact_registered_duration_and_service_ledger']=False
    elif mutation=='missing_reset':p['actual_reset_matching']=False
    elif mutation=='unrecomputed_stream':p['actual_applied_streams_recomputed']=False
    elif mutation=='opened_raw':p['raw_files_opened']=1
    elif mutation=='new_qualification_physics':p['physical_episodes_started']=406
    with pytest.raises(ValueError):module().validate_successor_final(s,p)


def test_actual_stopped405_predecessor_matches_new_schema_without_assumed_outer_exit():
    m=module()
    proof=m.validate_predecessor_stop(m.read(m.RUN/'logs/normal03.command.json'),m.read(m.RUN/'review/supervision03/RESULT.json'),
        m.read(m.PROFILE/'supervision_canonical02/SUMMARY.json'))
    assert proof['normal03_exit_code']==125
    assert 'supervision03_process_exit_code' not in proof



def test_new_probe_waits_forS4_instead_of_requiring_oldS3_success(tmp_path,monkeypatch):
    m=module();run=tmp_path/'run'
    for name,value in [('ROOT',tmp_path),('RUN',run),('REPORT',run/'paper_results/FINAL01'),('S4',run/'review/normal008_continuation01/L72_RUN02'),('SUCCESSOR_CONTRACT',tmp_path/'missing_contract.json')]:
        monkeypatch.setattr(m,name,value,raising=False)
    jobs=[dict(name=f'v20_normal_{i:03d}') for i in range(12)]
    value=m.probe(jobs)
    assert value['ready'] is False and value['state']=='pending_successor_no_project_import'
    assert value['successor']=='normal008_continuation01/L72_RUN02'


@pytest.mark.parametrize('batch,origin',[('v20_normal_000','canonical02'),('v20_normal_008','S4_L72_RUN02'),('v20_normal_009','unknown_original_normal02')])
def test_resource_context_selects_exact10_plus1_plus_unknown(batch,origin):
    m=module();value=m.resource_context(batch,{'source':'canonical02'},{'source':'S4'})
    assert value['origin']==origin
    assert value['measurement_unknown'] is (batch=='v20_normal_009')


def test_prepare_without_bound_successor_contract_does_not_import_numeric_project(tmp_path,monkeypatch):
    m=module();monkeypatch.setattr(m,'SUCCESSOR_CONTRACT',tmp_path/'not_bound_yet.json',raising=False)
    monkeypatch.setattr(m,'dependencies',lambda:pytest.fail('Prepared-only missing contract imported numeric project'))
    monkeypatch.setattr(m,'registration_preflight',lambda:pytest.fail('Prepared-only missing contract imported numeric project'))
    assert m.prepare()['status']=='prepared_pending_successor_contract'



def gt_rows():
    import json
    return [dict(start_minute='0',end_minute='30',source_start_gt_kW=json.dumps([0,0,0]),source_end_gt_kW=json.dumps([60,0,0]),scenario_id='s',policy_id='p',raw_sha256='a'*64),
        dict(start_minute='30',end_minute='60',source_start_gt_kW=json.dumps([60,20,0]),source_end_gt_kW=json.dumps([60,20,0]),scenario_id='s',policy_id='p',raw_sha256='a'*64)]


def test_GT_energy_uses_actual_linear_source_endpoints_not_arrived_action():
    rows=gt_rows();rows[0]['action']='{"gt_electric_kW":[999,999,999]}'
    value=module().integrate_actual_gt(rows,60.,'s','p','a'*64)
    assert value['electric_kWh']==55. and value['unit_electric_kWh']==[45.,10.,0.]


@pytest.mark.parametrize('mutation',['gap','overlap','tail_unknown','missing_endpoint','wrong_policy','wrong_raw','negative_GT','not_three_units'])
def test_saved_GT_csv_cannot_fill_unknown_or_foreign_actual_power(mutation):
    rows=gt_rows()
    if mutation=='gap':rows[1]['start_minute']='31'
    elif mutation=='overlap':rows[1]['start_minute']='29'
    elif mutation=='tail_unknown':rows[1]['end_minute']='59'
    elif mutation=='missing_endpoint':rows[0]['source_end_gt_kW']=''
    elif mutation=='wrong_policy':rows[1]['policy_id']='foreign'
    elif mutation=='wrong_raw':rows[1]['raw_sha256']='b'*64
    elif mutation=='negative_GT':rows[0]['source_start_gt_kW']='[-1,0,0]'
    elif mutation=='not_three_units':rows[0]['source_start_gt_kW']='[1,2]'
    with pytest.raises(ValueError):module().integrate_actual_gt(rows,60.,'s','p','a'*64)



def idle_fixture():
    result=dict(status='owned_idle_monitor_stopped_no_science',actual_wait_exit_code=15,scientific_episodes_started=0,
        no_008_attempt_after_stop=True,pid_no_longer_exists=True,old_S3_and_normal03_records_not_modified=True,
        source_sha256='68701e844ffca923e91f82fff52116d4b18a4357f1239d5c4a416135c8ed95a3',
        preserved_implementation_sha256='68701e844ffca923e91f82fff52116d4b18a4357f1239d5c4a416135c8ed95a3')
    command=dict(name='normal008_lightweight_run01',exit_code=15)
    return result,command


def test_idleRUN01_exit15_is_not_a_scientific_attempt_or_a_success():
    assert module().validate_idle_predecessor(*idle_fixture())['RUN01_scientific_episodes_started']==0


@pytest.mark.parametrize('mutation',['success','exit0','wrong_capture','science_started','008_attempt','still_alive','changed_old','wrong_source'])
def test_aborted_idle_successor_truth_cannot_be_erased(mutation):
    r,c=idle_fixture()
    if mutation=='success':r['status']='passed'
    elif mutation=='exit0':c['exit_code']=0
    elif mutation=='wrong_capture':c['name']='normal008_lightweight_run02'
    elif mutation=='science_started':r['scientific_episodes_started']=1
    elif mutation=='008_attempt':r['no_008_attempt_after_stop']=False
    elif mutation=='still_alive':r['pid_no_longer_exists']=False
    elif mutation=='changed_old':r['old_S3_and_normal03_records_not_modified']=False
    elif mutation=='wrong_source':r['preserved_implementation_sha256']='a'*64
    with pytest.raises(ValueError):module().validate_idle_predecessor(r,c)



def test_actual_complete_normal000_GT_endpoint_units_match_saved_M20_source_ledger():
    m=module();d=m.RUN/'experiments/v20_normal_000';record=m.read(next((d/'pairs').glob('*/RESULT.json')))
    csv_path=d/'analysis/ENGINEERING_INTERVALS.csv';terminal=m.read(d/'BATCH_RESULT.json')
    assert m.sha(csv_path)==terminal['analysis_receipt']['compatibility_export_receipt']['output_sha256']['ENGINEERING_INTERVALS.csv']
    ledger=record['analysis']['v20']['service_ledgers']['registered_full']
    got=m.integrate_actual_gt(m.csv_rows(csv_path),ledger['window_minutes'][1],record['scenario_id'],record['policy_id'],record['raw_sha256'])
    assert got['electric_kWh']==pytest.approx(ledger['actual_source_integrals']['GT_electric_kWh']['value'],abs=1e-6)
