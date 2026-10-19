"""Short-lived postprocessing gate tests: never run a physical model."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest

HERE=Path(__file__).resolve().parent

def module():
    p=HERE/'collect_normal_once03.py'
    assert p.is_file(), 'A standard-library gate must precede the frozen collector import'
    if '_collect_normal_once03_tested' not in sys.modules:
        spec=importlib.util.spec_from_file_location('_collect_normal_once03_tested',p)
        value=importlib.util.module_from_spec(spec);sys.modules[spec.name]=value;spec.loader.exec_module(value)
    return sys.modules['_collect_normal_once03_tested']


def ready_fixture():
    s3=dict(status='passed',strict_batches=148,source_modified=False,continuation_of='supervision02',normal_capture_name='normal03',normal_supervision_name='canonical02',previous_helper_error_preserved=True,existing_normal_009_strictly_skipped=True,normal_009_resource_peak_unknown=True,checkpoint=dict(registered=406,actual_started=406,actual_finished=406,active=0,not_started=0,failed_terminal_journals=[]))
    normal=dict(name='normal03',exit_code=0)
    report=dict(name='final_readback01',exit_code=0)
    summary=dict(expected_pairs=406,actual_started_pairs=406,actual_finished_pairs=406,complete_registered_full_pairs=406,eligible_terminal_pairs=406,unknown_full_pairs=0,status_counts=dict(completed=406))
    return s3,normal,report,summary


def test_full_stage_records_authorize_only_readonly_validation():
    assert module().validate_ready_records(*ready_fixture()) is None


@pytest.mark.parametrize('mutation',['s3_failed','405','active','strict147','normal02','normal_nonzero','report_nonzero','not_FINAL01','summary405','unknown','source_changed','009_falsely_measured'])
def test_stage_status_or_scalar_success_cannot_override_terminal_denominator(mutation):
    s,n,r,q=ready_fixture()
    if mutation=='s3_failed':s['status']='failed_unknown'
    elif mutation=='405':s['checkpoint']['actual_finished']=405
    elif mutation=='active':s['checkpoint']['active']=1
    elif mutation=='strict147':s['strict_batches']=147
    elif mutation=='normal02':s['normal_capture_name']='normal02'
    elif mutation=='normal_nonzero':n['exit_code']=2
    elif mutation=='report_nonzero':r['exit_code']=1
    elif mutation=='not_FINAL01':r['name']='other'
    elif mutation=='summary405':q['eligible_terminal_pairs']=405
    elif mutation=='unknown':q['unknown_full_pairs']=1
    elif mutation=='source_changed':s['source_modified']=True
    elif mutation=='009_falsely_measured':s['normal_009_resource_peak_unknown']=False
    with pytest.raises(ValueError):module().validate_ready_records(s,n,r,q)


def stop_fixture():
    cap=dict(name='normal_analysis_wait02',exit_code=15)
    stop=dict(status='owned_readonly_waiter_stopped_preserve_actual_exit',actual_terminated_pid=82524,observed_OS_process_exit_code=15,capture_record=copy.deepcopy(cap),scientific_execution_failed=False,numeric_analysis_generated=False,READONLY_OWNER_preserved=True,new_oneshot_collection_required_after_S3=True,identity_rechecked=dict(pid=82524,children=[]))
    return stop,cap


def test_waiter_exit15_stays_distinct_from_successful_collection():
    module().validate_stop_records(*stop_fixture())


@pytest.mark.parametrize('mutation',['erased_exit','wrong_pid','rewritten_capture','claimed_numeric','owner_deleted','child_tree'])
def test_stopping_receipt_cannot_be_rewritten_as_success_or_science_stop(mutation):
    s,c=stop_fixture()
    if mutation=='erased_exit':s['observed_OS_process_exit_code']=0
    elif mutation=='wrong_pid':s['actual_terminated_pid']=9504
    elif mutation=='rewritten_capture':s['capture_record']['exit_code']=0
    elif mutation=='claimed_numeric':s['numeric_analysis_generated']=True
    elif mutation=='owner_deleted':s['READONLY_OWNER_preserved']=False
    elif mutation=='child_tree':s['identity_rechecked']['children']=[dict(pid=1)]
    with pytest.raises(ValueError):module().validate_stop_records(s,c)


def test_missing_S3_does_not_even_import_frozen_helper(tmp_path,monkeypatch):
    m=module();calls=[]
    monkeypatch.setattr(m,'_load_helper',lambda *a:calls.append(a))
    with pytest.raises(ValueError,match='not ready'):m.collect_once(tmp_path)
    assert calls==[]
    assert not list(tmp_path.rglob('ONESHOT_INTENT01.json'))


def document_fixture(tmp_path):
    root=tmp_path;out=root/'review';out.mkdir();doc=root/'docs/NORMAL.md';doc.parent.mkdir()
    text='value 123.456 kg; fee -7.2 USD\n'+'\n'.join('['+str(i)+']('+module().OLD_PREFIX+'table'+str(i)+'.csv)' for i in range(8))+'\n'
    doc.write_text(text,encoding='utf-8');table=out/'VALUES.csv';table.write_text('fee,co2\n-7.2,123.456\n',encoding='utf-8')
    receipt=out/'COLLECTION_RECEIPT.json';receipt.write_text(json.dumps(dict(outputs=[dict(path=doc.relative_to(root).as_posix(),sha256=module().sha(doc)),dict(path=table.relative_to(root).as_posix(),sha256=module().sha(table))])),encoding='utf-8')
    return root,out,doc,table,receipt,text


def test_document_fix_changes_only_eight_link_prefixes_and_preserves_generated_bytes(tmp_path):
    root,out,doc,table,receipt,text=document_fixture(tmp_path);original_receipt=receipt.read_bytes();original_table=table.read_bytes()
    proof=module().correct_document_links(root,out,doc,receipt)
    assert doc.read_text(encoding='utf-8')==text.replace(module().OLD_PREFIX,module().NEW_PREFIX)
    assert (out/'NORMAL_WINDOW_ANALYSIS_AS_GENERATED01.md').read_text(encoding='utf-8')==text
    assert receipt.read_bytes()==original_receipt and table.read_bytes()==original_table
    assert proof['replacements']==8 and proof['only_link_prefix_changed'] is True
    assert proof['numeric_tables_unchanged'] is True


def test_wrong_document_or_receipt_sha_refuses_link_rewrite(tmp_path):
    root,out,doc,table,receipt,text=document_fixture(tmp_path)
    doc.write_text(text+'changed scientific value',encoding='utf-8')
    with pytest.raises(ValueError):module().correct_document_links(root,out,doc,receipt)
    assert not (out/'DOCUMENT_LINK_CORRECTION01.json').exists()



def test_new_pid_owner_is_saved_before_import_and_original_wait_exit_is_not_overwritten(tmp_path,monkeypatch):
    import os
    m=module();out=tmp_path/m.RUN_REL/'review/normal_window_analysis03';out.mkdir(parents=True)
    old=out/'READONLY_OWNER.json';old.write_text('{"pid":82524}',encoding='utf-8')
    monkeypatch.setattr(m,'readiness',lambda root:dict(ready=True,original_waiter_exit_code=15))
    def load(path):
        owner=m.read(out/'ONESHOT_OWNER01.json')
        assert owner['pid']==os.getpid() and owner['command']==sys.argv
        assert owner['cwd']==str(Path.cwd())
        assert owner['original_waiter_exit_code']==15 and owner['helper_sha256']==m.HELPER_SHA
        raise RuntimeError('synthetic loader stop; no prepared helper imported')
    monkeypatch.setattr(m,'_load_helper',load)
    with pytest.raises(RuntimeError,match='synthetic loader stop'):m.collect_once(tmp_path)
    assert old.read_text(encoding='utf-8')=='{"pid":82524}'
    assert m.read(out/'ONESHOT_RESULT01.json')['status']=='failed_preserve_postprocess_evidence'
    with pytest.raises(ValueError,match='already exist'):m.collect_once(tmp_path)



def successor_fixture():
    s=dict(status='passed',exit_code=0,strict_batches=148,qualified_pairs=406,no_frozen_science_modified=True,
        old_STOP_preserved=True,normal009_resource_peak_unknown=True,old_measured_resource_batches=10,new_measured_resource_batches=1,
        actual_scientific_children_started=1,last_child=dict(pid=12,batch='v20_normal_008',successful_Popen=True,owned_child_drained=True,exit_code=0,resource_result_sha='a'*64))
    q=dict(schema='hospital_ies.v20.publication_final_campaign_qualification.v1',status='passed',model_id='M20_load_accounting',
        actual_complete_batches=148,actual_complete_pairs=406,actual_started_attempts=406,actual_finished_attempts=406,
        failed_or_unknown_canonical_pairs=0,exact_registered_duration_and_service_ledger=True,actual_reset_matching=True,
        actual_applied_streams_recomputed=True,raw_files_opened=0,annual_values_parsed=0,physical_episodes_started=0)
    cap=dict(name='normal008_lightweight_run02',exit_code=0)
    report=dict(name='final_readback01',exit_code=0)
    summary=dict(expected_pairs=406,actual_started_pairs=406,actual_finished_pairs=406,complete_registered_full_pairs=406,eligible_terminal_pairs=406,unknown_full_pairs=0,status_counts=dict(completed=406))
    return s,q,cap,report,summary


def test_successor_gate_requires_trueRUN02_not_failed_or_idle_predecessors():
    module().validate_successor_ready_records(*successor_fixture())


@pytest.mark.parametrize('mutation',['RUN01','outer15','not406','two_children','old11','009measured','bad_ledger','bad_stream','new_qualification_physics','report_failed'])
def test_successor_bootstrap_never_imports_from_scalar_success_only(mutation):
    s,q,c,r,z=successor_fixture()
    if mutation=='RUN01':c['name']='normal008_lightweight_run01'
    elif mutation=='outer15':c['exit_code']=15
    elif mutation=='not406':q['actual_complete_pairs']=405
    elif mutation=='two_children':s['actual_scientific_children_started']=2
    elif mutation=='old11':s['old_measured_resource_batches']=11
    elif mutation=='009measured':s['normal009_resource_peak_unknown']=False
    elif mutation=='bad_ledger':q['exact_registered_duration_and_service_ledger']=False
    elif mutation=='bad_stream':q['actual_applied_streams_recomputed']=False
    elif mutation=='new_qualification_physics':q['physical_episodes_started']=406
    elif mutation=='report_failed':r['exit_code']=1
    with pytest.raises(ValueError):module().validate_successor_ready_records(s,q,c,r,z)


def test_new03_document_links_are_verified_without_modifying_generated_document(tmp_path):
    m=module();root,out,doc,table,receipt,text=document_fixture(tmp_path)
    corrected=text.replace(m.OLD_PREFIX,m.NEW_PREFIX);doc.write_text(corrected,encoding='utf-8')
    data=m.read(receipt);data['outputs'][0]['sha256']=m.sha(doc);receipt.write_text(json.dumps(data),encoding='utf-8')
    before=doc.read_bytes();proof=m.verify_document_links(root,out,doc,receipt)
    assert doc.read_bytes()==before and proof['document_modified'] is False
    assert proof['link_prefix']==m.NEW_PREFIX and proof['links_verified']==8
