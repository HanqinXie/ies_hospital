"""Output-only witnesses of a supplied final actual result; no physical solve."""
from copy import deepcopy
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from reviewed_source_fixtures import reviewed_source_bytes

from test_engineering_basis_steam_meter import case as legacy_case
from tools.sci_engineering.solver_log import SolverCallLog, SolverRowSidecar


def api():
    path=Path(__file__).parents[1]/'tools/annual_program/witness.py'
    assert path.is_file(), 'final actual-result witness logger is not implemented'
    return importlib.import_module('tools.annual_program.witness')


@pytest.fixture
def actual(legacy_case):
    old=legacy_case
    for relative in ('tools/essential_fixes/execution.py','tools/model_correction_v25/reserve.py',
                     'tools/execution_recovery/numerics.py','tools/engineering_model/environment.py'):
        target=old.source/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        # In particular reserve.py is now a different scientific model. Never
        # copy current bytes into this legacy14 native-return fixture.
        target.write_bytes(reviewed_source_bytes(relative))
    segment=deepcopy(old.payload['segments'][0])
    segment['loads_kW']=deepcopy(segment['connected_loads_kW'])
    segment['disconnected_loads_kW']=[[0.]*3 for _ in range(4)]
    ledger=segment.pop('service_request_ledger')
    layers=deepcopy(old.payload['solver_calls'][1]['solver_backend_layers'])
    attempt=layers[-1]['attempts'][0]
    attempt['validation'].pop('rows_ref')
    attempt['validation']['rows']=deepcopy(old.sidecars[0]['rows'])
    attempt['validation']['integer_indices']=[3,4,5,42,44,45]
    result=dict(action=deepcopy(segment['action']),lost=deepcopy(ledger['connected_unserved_kW']),
        served=deepcopy(ledger['delivered_kW']),solver_backend_layers=layers,
        m22_mode_selection=deepcopy(segment['m24_cold_mode_selection']),
        m23_boiler_domains=deepcopy(segment['m23_boiler_domains']))
    writer=SolverRowSidecar(old.producer/'actual_witness_rows.jsonl.gz')
    def forbidden(*args,**kwargs):raise AssertionError('Recorder called a physical/context/snapshot API')
    core=SimpleNamespace(time=segment['end_minute'],solver_calls=SolverCallLog(writer=writer),
        env=SimpleNamespace(root=old.source),state=SimpleNamespace(physical_marker=['unchanged']),
        _solve=forbidden,make_snapshot=forbidden,_current_context=forbidden,data_at=forbidden)
    yield SimpleNamespace(old=old,core=core,segment=segment,result=result,writer=writer)
    if not writer.closed:writer.close()


@pytest.mark.parametrize('size',[47,48])
def test_direct_final_primal_is_externalized_by_original_sink_without_side_effects(actual,size):
    if size==48:actual.result['solver_backend_layers'][-1]['attempts'][0]['raw_x'].append(.123)
    before_result=deepcopy(actual.result);before_segment=deepcopy(actual.segment)
    state=actual.core.state
    module=api();receipt=module.record_actual_witness(actual.core,actual.segment,actual.result)
    assert receipt['status']=='recorded'
    assert actual.segment[module.FIELD]==receipt
    assert receipt['unused_steam_lbph']==5.
    assert receipt['raw_x_length']==size
    assert receipt['primal_selected_by_action_matching'] is False
    call=actual.core.solver_calls[receipt['solver_call_index']]
    attempt=call['solver_backend_layers'][0]['attempts'][0]
    assert attempt['raw_x']==before_result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    assert 'rows' not in attempt['validation']
    assert attempt['validation']['rows_ref']==receipt['rows_ref']
    assert call['phase']==module.PHASE
    assert call['evidence_copy_only'] is True and call['additional_native_solver_calls']==0
    assert actual.result==before_result
    assert {k:v for k,v in actual.segment.items() if k!=module.FIELD}==before_segment
    assert actual.core.time==15. and actual.core.state is state
    assert actual.core.state.physical_marker==['unchanged']
    assert actual.writer.entry_count==1


def test_later_guard_log_and_alternative_candidate_never_select_actual_primal(actual):
    guard=deepcopy(actual.result['solver_backend_layers'])
    guard[-1]['attempts'][0]['raw_x'][13]+=6.821210263296962e-13
    actual.core.solver_calls.append(dict(phase='local_down_endpoint',status='success',solver_backend_layers=guard))
    receipt=api().record_actual_witness(actual.core,actual.segment,actual.result)
    assert receipt['status']=='recorded'
    assert receipt['solver_call_index']==1
    assert receipt['unused_steam_lbph']==5.
    assert actual.core.solver_calls[0]['solver_backend_layers'][0]['attempts'][0]['raw_x'][13]!=5.


def test_connected_result_losses_and_actual_cut_total_are_separate_before_ledger(actual):
    actual.segment['loads_kW'][0][1]+=7.
    actual.segment['disconnected_loads_kW'][0][1]=7.
    actual.segment['unserved_kW'][0][1]=7.
    assert 'service_request_ledger' not in actual.segment
    receipt=api().record_actual_witness(actual.core,actual.segment,actual.result)
    assert receipt['status']=='recorded'
    basis=actual.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']['service_basis']
    assert basis['connected_unserved_kW'][0][1]==0.
    assert basis['active_cut_kW'][0][1]==7.
    assert basis['total_unserved_kW'][0][1]==7.


@pytest.mark.parametrize('change,reason',[
    ('two_accepted','nonunique_accepted_final_cost_attempt'),
    ('rejected','no_unique_accepted_final_cost_attempt'),
    ('missing_x','missing_accepted_raw_x'),
    ('missing_rows','missing_inline_final_native_rows'),
    ('foreign_backend','unsupported_final_native_backend')])
def test_unavailable_attribution_is_unknown_without_a_success_record(actual,change,reason):
    layer=actual.result['solver_backend_layers'][-1];attempt=layer['attempts'][0]
    if change=='two_accepted':layer['attempts'].append(deepcopy(attempt))
    elif change=='rejected':attempt['accepted']=False
    elif change=='missing_x':attempt.pop('raw_x')
    elif change=='missing_rows':attempt['validation'].pop('rows')
    elif change=='foreign_backend':layer['backend']='unproved_backend'
    module=api();receipt=module.record_actual_witness(actual.core,actual.segment,actual.result)
    assert receipt['status']=='unknown' and reason in receipt['reasons']
    assert receipt['solver_call_index'] is None and receipt['unused_steam_lbph'] is None
    assert actual.core.solver_calls==[] and actual.writer.entry_count==0
    assert actual.segment[module.FIELD]['status']=='unknown'


@pytest.mark.parametrize('change',['nonfinite','negative_waste','bool_waste','bad_total','different_actual_action'])
def test_bad_actual_data_fails_before_any_recorded_flag_or_log(actual,change):
    x=actual.result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    if change=='nonfinite':x[13]=float('nan')
    elif change=='negative_waste':x[13]=-1.
    elif change=='bool_waste':x[13]=True
    elif change=='bad_total':actual.segment['unserved_kW'][0][1]=2.
    elif change=='different_actual_action':actual.segment['action']['grid_import_kW']=1.
    module=api()
    with pytest.raises(ValueError):module.record_actual_witness(actual.core,actual.segment,actual.result)
    assert actual.core.solver_calls==[]
    assert module.FIELD not in actual.segment


@pytest.mark.parametrize('relative',[
    'src/hospital_ies/solver_backend.py','tools/model_correctness_v23/numerics.py',
    'src/hospital_ies/solver_diagnostics.py','tools/execution_recovery/numerics.py',
    'tools/engineering_model/environment.py','tools/model_closure_v22/environment.py',
    'tools/engineering_completion_v24/cold_modes.py'])
def test_changed_native_control_flow_source_stays_unknown(actual,relative):
    path=actual.old.source/relative
    path.write_text(path.read_text('utf8')+'\n# changed source contract\n',encoding='utf8')
    receipt=api().record_actual_witness(actual.core,actual.segment,actual.result)
    assert receipt['status']=='unknown'
    assert 'native_source_contract_not_supported' in receipt['reasons']
    assert actual.core.solver_calls==[]


def test_original_sink_io_error_cannot_publish_a_recorded_segment_flag(actual):
    actual.writer.close()
    module=api()
    with pytest.raises(RuntimeError):module.record_actual_witness(actual.core,actual.segment,actual.result)
    assert module.FIELD not in actual.segment


def test_boolean_final_layer_status_is_not_a_native_success(actual):
    actual.result['solver_backend_layers'][-1]['status']=False
    receipt=api().record_actual_witness(actual.core,actual.segment,actual.result)
    assert receipt['status']=='unknown'
    assert actual.core.solver_calls==[]


def native_validate_vector():
    # Read the actual source module without relying on an editable pip install.
    import importlib.util
    path=Path(__file__).parents[1]/'src/hospital_ies/solver_diagnostics.py'
    spec=importlib.util.spec_from_file_location('_annual_native_solver_diagnostics',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_vector


def install_real_native_validation(actual,side):
    """Native validator only; no optimizer or environment construction."""
    import numpy as np
    validate_vector=native_validate_vector()
    x=np.asarray(actual.result['solver_backend_layers'][-1]['attempts'][0]['raw_x'])
    matrix=np.zeros((2,len(x)))
    matrix[0,0]=actual.segment['action']['hrsg_steam_lbph']/x[0]
    matrix[0,13]=-1.
    matrix[1,0]=1.
    steam=float((matrix@x)[0]);power=float(x[0])
    lower=np.array([steam,-np.inf if side in ('upper','free') else power-1.])
    upper=np.array([steam,np.inf if side in ('lower','free') else power+1.])
    integrality=np.zeros(len(x),dtype=int);integrality[[3,4,5,42,44,45]]=1
    rows=[dict(name='balance.steam',unit='lb/h',kind='physical',source='synthetic native validator fixture'),
          dict(name='native.'+side,unit='kW',kind='physical',source='native one-sided inequality fixture')]
    validation=validate_vector(matrix,lower,upper,np.zeros(len(x)),np.full(len(x),np.inf),integrality,x,rows)
    actual.result['solver_backend_layers'][-1]['attempts'][0]['validation']=validation
    return validation


@pytest.mark.parametrize('side',['upper','lower','free'])
def test_source_native_unbounded_rows_record_with_explicit_absence_semantics(actual,side):
    from tools.sci_engineering.solver_log import iter_sidecar_entries
    validation=install_real_native_validation(actual,side)
    before=deepcopy(actual.result)
    module=api();receipt=module.record_actual_witness(actual.core,actual.segment,actual.result)
    assert receipt['status']=='recorded'
    assert actual.result==before
    assert receipt['unused_steam_lbph']==before['solver_backend_layers'][-1]['attempts'][0]['raw_x'][13]
    metadata=actual.core.solver_calls[receipt['solver_call_index']]['annual_actual_witness']
    encoding=metadata['native_row_bound_encoding']
    assert encoding['normalized_null_means_unbounded_endpoint_not_zero'] is True
    absence=encoding['rows'][0]
    assert absence['row_position']==1
    assert absence['lower_unbounded'] is (side in ('upper','free'))
    assert absence['upper_unbounded'] is (side in ('lower','free'))
    actual.writer.close()
    stored=list(iter_sidecar_entries(actual.writer.path))[0]['rows'][1]
    if side in ('upper','free'):
        assert stored['lower'] is None and stored['lower_residual'] is None
    if side in ('lower','free'):
        assert stored['upper'] is None and stored['upper_residual'] is None
    assert validation['max_original_violation']==0.


@pytest.mark.parametrize('change',[
    'nan_lower','nan_upper','positive_infinite_lower','negative_infinite_upper',
    'wrong_lower_residual_sign','wrong_upper_residual_sign','finite_side_infinite_residual',
    'infinite_activity','nan_activity','infinite_violation','infinite_scaled_violation',
    'infinite_coefficients','infinite_numpy_coefficients','infinite_other_metadata','nan_worst_row_activity','nonfinite_raw_x','nonfinite_service'])
def test_only_native_unbounded_endpoint_pairs_are_allowed_before_recorded_flag(actual,change):
    validation=install_real_native_validation(actual,'upper' if change!='wrong_upper_residual_sign' else 'lower')
    row=validation['rows'][1]
    if change=='nan_lower':row['lower']=float('nan')
    elif change=='nan_upper':row['upper']=float('nan')
    elif change=='positive_infinite_lower':row['lower']=float('inf')
    elif change=='negative_infinite_upper':row['upper']=-float('inf')
    elif change=='wrong_lower_residual_sign':row['lower_residual']=float('inf')
    elif change=='wrong_upper_residual_sign':row['upper_residual']=float('inf')
    elif change=='finite_side_infinite_residual':row['upper_residual']=-float('inf')
    elif change=='infinite_activity':row['activity']=float('inf')
    elif change=='nan_activity':row['activity']=float('nan')
    elif change=='infinite_violation':row['violation']=float('inf')
    elif change=='infinite_scaled_violation':row['scaled_violation']=float('inf')
    elif change=='infinite_coefficients':row['coefficients']=[1.,float('inf')]
    elif change=='infinite_numpy_coefficients':
        import numpy as np
        row['coefficients']=np.array([1.,np.inf])
    elif change=='infinite_other_metadata':row['arbitrary_nested']={'upper':float('inf')}
    elif change=='nan_worst_row_activity':validation['worst_rows']=[dict(row,activity=float('nan'))]
    elif change=='nonfinite_raw_x':actual.result['solver_backend_layers'][-1]['attempts'][0]['raw_x'][13]=float('inf')
    elif change=='nonfinite_service':actual.result['served'][0][1]=float('inf')
    module=api()
    with pytest.raises(ValueError):module.record_actual_witness(actual.core,actual.segment,actual.result)
    assert actual.core.solver_calls==[]
    assert module.FIELD not in actual.segment


def test_native_nonfinite_matrix_coefficient_cannot_hide_in_a_one_sided_row(actual):
    import numpy as np
    validate_vector=native_validate_vector()
    attempt=actual.result['solver_backend_layers'][-1]['attempts'][0]
    x=np.asarray(attempt['raw_x']);matrix=np.zeros((1,len(x)));matrix[0,0]=np.inf
    with np.errstate(invalid='ignore'):
        attempt['validation']=validate_vector(matrix,np.array([-np.inf]),np.array([101.]),
            np.zeros(len(x)),np.full(len(x),np.inf),np.zeros(len(x),dtype=int),x,
            [dict(name='bad.coefficient',unit='kW',kind='physical')])
    attempt['validation']['integer_indices']=[3,4,5,42,44,45]
    module=api()
    with pytest.raises(ValueError):module.record_actual_witness(actual.core,actual.segment,actual.result)
    assert actual.core.solver_calls==[] and module.FIELD not in actual.segment
