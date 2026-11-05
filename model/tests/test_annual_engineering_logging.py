"""No solver calls: explicit engineering copies omit only safe probe tables."""
from copy import deepcopy
import importlib
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.model_correctness_v23 import evidence as evidence
from tools.sci_engineering.solver_log import SolverCallLog,SolverRowSidecar,restore_solver_calls
from test_annual_program_witness import actual
from test_engineering_basis_steam_meter import case as legacy_case


def api():
    assert (ROOT/'tools/annual_program/engineering_logging.py').is_file(), 'engineering helper missing'
    return importlib.import_module('tools.annual_program.engineering_logging')


def layers():
    rows=[dict(name='physical.heat',kind='physical',violation=2e-9,activity=1.),
          dict(name='priority.P0',kind='priority_lock',violation=0.,activity=0.)]
    validation=dict(rows=rows,worst_rows=deepcopy(rows),max_original_violation=2e-9,
        max_lock_violation=0.,max_integrality_distance=0.,bound_violation=[0.],extra='keep')
    return [dict(rank=0,layer='cost',backend='gurobipy',status=0,
        attempts=[dict(attempt=1,status=0,accepted=True,native_status=2,error=None,error_type=None,
            objective_consistent=True,raw_x=[1.,0.],raw_objective=3.,validation=validation)])]


def call(phase='m24_cold_mode_candidate'):
    return dict(phase=phase,status='success',time_minute=12.,dt_h=.25,ready=False,
        critical_unserved_kW=[[0.,1.,0.]],cold_mode='idle',inventory_committed=False,
        unrelated=dict(rows=['ordinary user table']),solver_backend_layers=layers())


def store(tmp_path,enabled=True):
    value=evidence.DomainEvidenceStore(tmp_path/'cas',enable_all_validation_rows=True)
    if enabled is not None:value.engineering_summary=enabled
    return value


def validation(value):return value[0]['attempts'][0]['validation']


def assert_summary(value):
    assert 'rows' not in value and 'worst_rows' not in value and 'rows_ref' not in value
    summary=value['rows_summary']
    assert summary['schema']=='hospital_ies.annual_program.validation_rows_summary.v1'
    assert summary['row_count']==2 and summary['worst_row_count']==2
    assert summary['max_row_violation']==2e-9
    assert summary['rows_retained'] is False and summary['raw_rows_recoverable'] is False
    assert value['max_original_violation']==2e-9 and value['extra']=='keep'


def test_helper_keeps_raw_x_ready_targets_and_all_nonrow_fields_without_mutation():
    original=call();before=deepcopy(original)
    result=api().summarize_solver_call(original)
    assert_summary(validation(result['solver_backend_layers']))
    assert original==before
    for key in original.keys()-{'solver_backend_layers'}:assert result[key]==original[key]
    assert result['solver_backend_layers'][0]['attempts'][0]['raw_x']==[1.,0.]
    result['solver_backend_layers'][0]['attempts'][0]['raw_x'][0]=9.
    assert original==before


@pytest.mark.parametrize('phase',['m24_cold_mode_candidate','m24_current_port_selection',
    'm22_current_port_selection','m25_selected_no_storage_witness',
    'm22_non_DG_release_witness','m23_sustained_non_DG_release_witness'])
def test_only_registered_clean_internal_calls_avoid_both_writers(tmp_path,monkeypatch,phase):
    value=store(tmp_path);writes=[]
    monkeypatch.setattr(value,'store',lambda rows:writes.append(rows))
    with SolverRowSidecar(tmp_path/'rows.gz') as writer:
        logs=evidence.all_rows_solver_log(SolverCallLog(writer=writer),value)
        logs.append(call(phase))
        assert_summary(validation(logs[0]['solver_backend_layers']))
        assert writer.entry_count==0 and not writes


@pytest.mark.parametrize('method',['successful_backend_evidence','retained_backend_evidence'])
def test_early_diagnostic_copy_summarizes_before_CAS_and_can_be_logged_again(tmp_path,monkeypatch,method):
    value=store(tmp_path);writes=[];original=layers();before=deepcopy(original)
    monkeypatch.setattr(value,'store',lambda rows:writes.append(rows))
    with evidence.evidence_scope(value):result=getattr(evidence,method)(original)
    assert_summary(validation(result));assert not writes and original==before
    entry=call();entry['solver_backend_layers']=result
    with SolverRowSidecar(tmp_path/'rows.gz') as writer:
        logs=evidence.all_rows_solver_log(SolverCallLog(writer=writer),value);logs.append(entry)
        assert writer.entry_count==0 and not writes
        assert logs[0]['solver_backend_layers']==result


@pytest.mark.parametrize('flag',[None,False,1,'yes'])
def test_absent_or_non_boolean_true_switch_preserves_full_default(tmp_path,flag):
    value=store(tmp_path,flag);original=layers()
    with evidence.evidence_scope(value):result=evidence.successful_backend_evidence(original)
    assert evidence.restore_domain_evidence(result,value.directory)==original
    assert 'rows_ref' in validation(result) and 'rows_summary' not in validation(result)
    assert value.receipt()['closed_blobs']==1


@pytest.mark.parametrize('phase',['annual_program_actual_witness','fixed_execution',
    'local_event_split','local_down_endpoint','unknown_probe'])
def test_actual_and_unknown_phases_preserve_complete_ordered_rows_and_exact_owner(tmp_path,phase):
    value=store(tmp_path);original=call(phase);before=deepcopy(original)
    with SolverRowSidecar(tmp_path/'rows.gz') as writer:
        logs=evidence.all_rows_solver_log(SolverCallLog(writer=writer),value)
        logs.append(call());logs.append(original)
    assert writer.entry_count==1 and original==before
    restored=evidence.restore_domain_evidence(restore_solver_calls(logs,writer.path),value.directory)
    assert restored[1]==original
    assert validation(logs[1]['solver_backend_layers'])['rows_ref']['record_id']=='rows_000000001'
    from tools.sci_engineering.solver_log import iter_sidecar_entries
    assert list(iter_sidecar_entries(writer.path))[0]['location']==[1,'solver_backend_layers',0,'attempts',0,'validation']


def change_bad(entry,kind):
    layer=entry['solver_backend_layers'][0];attempt=layer['attempts'][0]
    if kind=='recovery':layer['attempts'].insert(0,dict(status=2,accepted=False,validation=deepcopy(attempt['validation'])))
    elif kind=='second_attempt':attempt['attempt']=2
    elif kind=='fallback':entry['fallback']={'reason':'failed first route'}
    elif kind=='projection':entry['projection']={'changed':True}
    elif kind=='error':attempt['error']='native failed'
    elif kind=='rejected':attempt['accepted']=False
    elif kind=='failed_layer':layer['status']=4
    elif kind=='validation':attempt['validation']['max_lock_violation']=1e-8
    elif kind=='nan':attempt['validation']['max_original_violation']=float('nan')
    elif kind=='unknown':layer['attempts']='not a known attempt list'
    elif kind=='unknown_status':entry['status']='unknown'
    elif kind=='ref':attempt['validation']['rows_ref']={'schema':'unknown'}
    elif kind=='nested_failure':entry['domain']={'solver_backend_layers':[dict(status=4,attempts=[])]}
    elif kind=='validation_error':attempt['validation']['error']='post-validation failed'
    elif kind=='validation_nested_failure':attempt['validation']['check']={'status':4}
    elif kind=='domain_status':layer['domain_status']='compiler_or_solver_error'


@pytest.mark.parametrize('kind',['recovery','second_attempt','fallback','projection','error',
    'rejected','failed_layer','validation','nan','unknown','unknown_status','ref','nested_failure',
    'validation_error','validation_nested_failure','domain_status'])
def test_abnormal_or_unknown_calls_never_summarize(kind):
    value=call();change_bad(value,kind)
    assert api().summarize_solver_call(value) is None


@pytest.mark.parametrize('method',['successful_backend_evidence','retained_backend_evidence'])
def test_true_solver_failure_keeps_all_rows_and_CAS_failure_is_not_swallowed(tmp_path,monkeypatch,method):
    value=store(tmp_path);entry=call();change_bad(entry,'recovery');original=entry['solver_backend_layers']
    with evidence.evidence_scope(value):result=getattr(evidence,method)(original)
    assert evidence.restore_domain_evidence(result,value.directory)==original
    assert 'rows_summary' not in validation(result)
    monkeypatch.setattr(value,'store',lambda rows:(_ for _ in ()).throw(OSError('disk failed')))
    with evidence.evidence_scope(value),pytest.raises(OSError,match='disk failed'):
        getattr(evidence,method)(original)


def test_bad_summary_function_falls_back_full_but_OSError_propagates(tmp_path,monkeypatch):
    helper=api();value=store(tmp_path)
    def bad(_):raise ValueError('unexpected summary shape')
    monkeypatch.setattr(helper,'summarize_backend_layers',bad)
    with evidence.evidence_scope(value):result=evidence.successful_backend_evidence(layers())
    assert evidence.restore_domain_evidence(result,value.directory)==layers()
    def io(_):raise OSError('summary IO must propagate')
    monkeypatch.setattr(helper,'summarize_backend_layers',io)
    with evidence.evidence_scope(value),pytest.raises(OSError,match='must propagate'):
        evidence.successful_backend_evidence(layers())


def test_list_log_has_same_summary_boundary_and_no_CAS_writes(tmp_path,monkeypatch):
    value=store(tmp_path);writes=[]
    monkeypatch.setattr(value,'store',lambda rows:writes.append(rows))
    logs=evidence.all_rows_solver_log([],value);logs.append(call())
    assert_summary(validation(logs[0]['solver_backend_layers']));assert not writes


def test_real_final_result_hook_keeps_exact_full_owner_after_summarized_probe(actual,tmp_path):
    from tools.annual_program.witness import record_actual_witness
    value=store(tmp_path);original=deepcopy(actual.result)
    actual.core.solver_calls=evidence.all_rows_solver_log(actual.core.solver_calls,value)
    actual.core.solver_calls.append(call())
    receipt=record_actual_witness(actual.core,actual.segment,actual.result)
    assert receipt['status']=='recorded' and receipt['solver_call_index']==1
    assert actual.result==original and actual.writer.entry_count==1
    logged=actual.core.solver_calls[1]['solver_backend_layers'][0]['attempts'][0]
    assert logged['raw_x']==original['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    assert logged['validation']['rows_ref']==receipt['rows_ref']
    assert 'rows_summary' not in logged['validation']
    actual.writer.close()
    # The fixture also owns historical diagnostic CAS refs in another directory;
    # the final native rows here are owned by this exact ordered writer alone.
    restored=restore_solver_calls(actual.core.solver_calls,actual.writer.path)
    assert restored[1]['solver_backend_layers'][0]['attempts'][0]['validation']['rows']==original['solver_backend_layers'][-1]['attempts'][0]['validation']['rows']


@pytest.mark.parametrize('kind',['fallback','projection','recovery','rejected','nested_failure'])
def test_abnormal_registered_phase_follows_original_full_path(tmp_path,kind):
    value=store(tmp_path);entry=call();change_bad(entry,kind)
    with SolverRowSidecar(tmp_path/'rows.gz') as writer:
        logs=evidence.all_rows_solver_log(SolverCallLog(writer=writer),value);logs.append(entry)
    restored=evidence.restore_domain_evidence(restore_solver_calls(logs,writer.path),value.directory)
    assert restored==[entry] and 'rows_summary' not in validation(restored[0]['solver_backend_layers'])
