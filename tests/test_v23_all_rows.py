"""Opt-in all-row logging retains complete rejected/failed native evidence."""
import copy
import importlib
import json
from pathlib import Path
import sys
import os
import gzip
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from tools.model_correctness_v23 import evidence as m
from tools.sci_engineering.solver_log import SolverCallLog,SolverRowSidecar,restore_solver_calls,normalize_json

def real_failure():
    row=dict(name='m22.actual_recovery_after_all_service',kind='priority_lock',
        activity=0.,upper=-9.565482024440257e-10,violation=9.565482024440257e-10)
    attempt=dict(status=0,accepted=False,raw_x=[0.],validation=dict(rows=[row],
        max_lock_violation=row['violation']))
    return dict(status='compiler_or_solver_error',phase='fixed_execution',time_minute=470.,
        solver_backend_layers=[dict(status=4,attempts=[copy.deepcopy(attempt),copy.deepcopy(attempt)])])

def all_refs(value):
    if isinstance(value,list):return sum((all_refs(item) for item in value),[])
    if not isinstance(value,dict):return []
    return ([value['rows_ref']] if 'rows_ref' in value else [])+sum((all_refs(v) for k,v in value.items() if k!='rows_ref'),[])

def test_real_native_failure_roundtrips_all_rows_and_metadata_deduplicated(tmp_path):
    value=real_failure();original=copy.deepcopy(value)
    store=m.DomainEvidenceStore(tmp_path/'cas',enable_all_validation_rows=True)
    with m.evidence_scope(store):
        first=m.retained_backend_evidence(value);second=m.retained_backend_evidence(value)
    assert original==value and first==second
    assert m.restore_domain_evidence(first,store.directory)==normalize_json(value)
    assert len(all_refs(first))==2
    assert store.receipt()['closed_blobs']<=2
    assert first['status']==original['status']
    for before,after in zip(original['solver_backend_layers'][0]['attempts'],first['solver_backend_layers'][0]['attempts']):
        assert before['status']==after['status'] and before['accepted']==after['accepted']
        assert after['validation']['max_lock_violation']==before['validation']['max_lock_violation']
        assert after['raw_x']==before['raw_x']

def test_actual_rejected_layer_roundtrips_without_losing_rejection(tmp_path):
    value=real_failure();value['status']='candidate_rejected'
    store=m.DomainEvidenceStore(tmp_path/'cas',enable_all_validation_rows=True)
    with m.evidence_scope(store):encoded=m.retained_backend_evidence(value)
    assert encoded['status']=='candidate_rejected' and all_refs(encoded)
    assert m.restore_domain_evidence(encoded,store.directory)==normalize_json(value)

def test_old_default_keeps_failure_and_rejected_evidence_inline(tmp_path):
    store=m.DomainEvidenceStore(tmp_path/'cas');value=real_failure()
    with m.evidence_scope(store):encoded=m.retained_backend_evidence(value)
    assert encoded==value and not all_refs(encoded) and store.receipt()['closed_blobs']==0

def test_ordered_success_and_CAS_failure_are_independent_and_reversible(tmp_path):
    value=real_failure();value['status']='success'
    good=copy.deepcopy(value['solver_backend_layers'][0]);good['status']=0
    good['attempts']=good['attempts'][:1];good['attempts'][0].update(status=0,accepted=True)
    value['solver_backend_layers'].insert(0,good)
    store=m.DomainEvidenceStore(tmp_path/'cas',enable_all_validation_rows=True)
    with SolverRowSidecar(tmp_path/'solver_rows.jsonl.gz') as ordered:
        logs=m.all_rows_solver_log(SolverCallLog(writer=ordered),store)
        logs.append(value);logs.append(value)
    restored_ordered=restore_solver_calls(logs,ordered.path)
    restored=m.restore_domain_evidence(restored_ordered,store.directory)
    assert restored==normalize_json([value,value])
    assert ordered.entry_count==2  # accepted success rows only, one per call
    assert store.receipt()['closed_blobs']<=2

def test_no_ordered_writer_still_externalizes_all_rows(tmp_path):
    store=m.DomainEvidenceStore(tmp_path/'cas',enable_all_validation_rows=True)
    logs=m.all_rows_solver_log([],store);value=real_failure();logs.extend([value,value])
    assert m.restore_domain_evidence(logs,store.directory)==normalize_json([value,value])
    assert all_refs(logs)

def test_prior_incumbent_aliases_are_external_and_unrelated_rows_stay_inline(tmp_path):
    value=real_failure();value['status']='success'
    layer=value['solver_backend_layers'][0];layer['status']=0
    layer['previous_incumbent_validation']=copy.deepcopy(layer['attempts'][0]['validation'])
    layer['priority_lock_refinement']={'previous_validation':copy.deepcopy(layer['previous_incumbent_validation'])}
    value['previous_validation']=copy.deepcopy(layer['previous_incumbent_validation'])
    value['unrelated']={'rows':['ordinary user data']}
    store=m.DomainEvidenceStore(tmp_path/'cas',enable_all_validation_rows=True)
    with SolverRowSidecar(tmp_path/'solver_rows.jsonl.gz') as ordered:
        logs=m.all_rows_solver_log(SolverCallLog(writer=ordered),store);logs.append(value)
    assert logs[0]['unrelated']==value['unrelated']
    logged=logs[0]['solver_backend_layers'][0]
    assert 'rows' not in logged['previous_incumbent_validation']
    assert 'rows' not in logged['priority_lock_refinement']['previous_validation']
    assert 'rows' not in logs[0]['previous_validation']
    assert m.restore_domain_evidence(restore_solver_calls(logs,ordered.path),store.directory)==normalize_json([value])

def test_CAS_failure_aborts_logging_without_appending_partial_call(tmp_path,monkeypatch):
    store=m.DomainEvidenceStore(tmp_path/'cas',enable_all_validation_rows=True)
    logs=m.all_rows_solver_log([],store)
    def failed(rows):raise OSError('injected all-row CAS write')
    monkeypatch.setattr(store,'store',failed)
    with pytest.raises(OSError,match='injected all-row'):logs.append(real_failure())
    assert not logs

@pytest.mark.parametrize('kind',['infeasible','rejected_command'])
def test_failed_or_rejected_domain_evidence_IO_never_selects_an_alternative(tmp_path,monkeypatch,kind):
    from tools.model_correctness_v23 import domains
    from hospital_ies.execution import ModelConstraintInfeasible
    store=m.DomainEvidenceStore(tmp_path/'cas',enable_all_validation_rows=True);calls=[]
    def failed(rows):raise OSError('injected rejected-domain CAS write')
    monkeypatch.setattr(store,'store',failed)
    def solver(**kwargs):
        calls.append(kwargs)
        if kind=='infeasible':
            error=ModelConstraintInfeasible('actual checked domain')
            error.solver_backend_attempts=real_failure()['solver_backend_layers'];raise error
        truth=dict(stages={'current':dict(station_on_value=1.,actual_steam_lbph=0.)},zero_permission_phantom_stages=[])
        return dict(joint_diagnostic={'m22_boiler_command_truth':truth},solver_backend_layers=real_failure()['solver_backend_layers'])
    with m.evidence_scope(store),pytest.raises(OSError,match='rejected-domain CAS'):
        if kind=='infeasible':domains.solve_execution({'config':{'_model_correctness_v23':{}}},solver)
        else:domains.solve_command({},solver)
    assert len(calls)==1

def test_runtime_all_row_flag_requires_actual_evidence_directory():
    from tools.model_correctness_v23.environment import OperatingHospitalIESEnv
    with pytest.raises(ValueError,match='requires an evidence directory'):
        OperatingHospitalIESEnv(ROOT,allowed_hours=[4001,4002],sealed_days=[21],enable_all_validation_rows=True)

@pytest.mark.parametrize('corrupt',['sha','count'])
def test_CAS_restore_checks_hash_and_count(tmp_path,corrupt):
    store=m.DomainEvidenceStore(tmp_path/'cas',enable_all_validation_rows=True)
    with m.evidence_scope(store):encoded=m.retained_backend_evidence(real_failure())
    reference=all_refs(encoded)[0]
    if corrupt=='sha':reference['rows_sha256']='0'*64
    else:reference['row_count']+=1
    with pytest.raises(ValueError,match='SHA/row count'):m.restore_domain_evidence(encoded,store.directory)
