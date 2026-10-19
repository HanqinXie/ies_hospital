"""Pinned final-round numeric failures; original thresholds and service order."""
import copy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import pandas as pd
import pytest
from scipy import sparse
from scipy.optimize import Bounds,LinearConstraint
from hospital_ies.model import Plant
from hospital_ies.resilience import RuntimeState
from hospital_ies.solver_backend import milp
from tools.execution_recovery import numerics,planner

ROOT=Path(__file__).resolve().parents[1]
B=ROOT/'outputs/v18_execution_recovery_20261018_run01'


def domain(case):
    path=B/'review/final_failure_domains_capture01'/case
    with np.load(path/'domain.npz') as z:d={key:z[key].copy() for key in z.files}
    meta=json.loads((path/'rows_variables.json').read_text(encoding='utf8'))
    d['matrix']=sparse.csr_matrix((d['matrix_data'],d['matrix_indices'],d['matrix_indptr']),shape=d['matrix_shape'])
    return d,meta


def refine(case,**changes):
    assert hasattr(numerics,'refine_priority_lock_candidate'),'bounded same-layer candidate refinement missing'
    d,m=domain(case);value=float(d['objective']@d['failed_x'])
    args=dict(native_candidate=d['failed_x'],previous_candidate=d['previous_x'],objective=d['objective'],matrix=d['matrix'],
        row_lower=d['lower'],row_upper=d['upper'],lower=d['bounds_lower'],upper=d['bounds_upper'],integrality=d['integrality'],
        rows=m['rows'],variables=m['variables'],native_objective=value,native_dual_bound=value,native_gap=0.,allowed_relative_gap=1e-6,remaining_budget_seconds=5.)
    args.update(changes);return numerics.refine_priority_lock_candidate(**args)


@pytest.mark.parametrize('case',['long_final_004','long_final_006'])
def test_saved_K1_refinement_meets_all_original_rows_both_objective_contracts_and_critical_coordinates(case):
    d,m=domain(case);candidate,evidence=refine(case)
    assert candidate is not None and evidence['accepted']
    assert np.array_equal(candidate[14:20],d['failed_x'][14:20])
    assert evidence['validation']['max_original_violation']<=1e-7
    assert evidence['validation']['max_lock_violation']<=1e-10
    assert evidence['validation']['max_integrality_distance']<=1e-9
    assert evidence['relative_gap']<=1e-6 and evidence['objective_consistency_passed']
    assert evidence['exact_optimum_claimed'] is False and evidence['same_integer_point']
    assert 0.<evidence['alpha']<1. and evidence['candidate_coordinate_changes']


def test_saved_C_preparation_gap_is_not_hidden_as_objective_equivalence():
    candidate,evidence=refine('long_final_002')
    assert candidate is None and not evidence['accepted']


@pytest.mark.parametrize('change',['integer','material_critical','material_negative'])
def test_refinement_rejects_integer_changes_critical_service_changes_or_material_previous_negatives(change):
    d,_=domain('long_final_004');previous=d['previous_x'].copy()
    if change=='integer':previous[3]=1.-previous[3]
    elif change=='material_critical':previous[14]+=1.
    else:previous[11]=-1.
    candidate,evidence=refine('long_final_004',previous_candidate=previous)
    assert candidate is None and not evidence['accepted']


def test_roundoff_different_endpoint_can_be_pinned_without_changing_native_critical_service():
    # The initial new test required endpoint bit equality. The actual contract
    # protects FINAL native critical coordinates; a separately revalidated,
    # roundoff-only pinned endpoint may differ. Material-gap negatives remain.
    d,_=domain('long_final_004');previous=d['previous_x'].copy();previous[14]+=1e-11
    candidate,evidence=refine('long_final_004',previous_candidate=previous)
    assert candidate is not None and np.array_equal(candidate[14:20],d['failed_x'][14:20])
    assert evidence['critical_endpoint_adjustments'] and evidence['critical_endpoint_original_validation_passed']


def test_K1_failed_held_diagnostic_cannot_skip_uncompleted_command_objectives():
    from tools.execution_recovery import operating_planner
    assert hasattr(operating_planner,'held_diagnostic_command_layers_complete')
    required={'current_P0','current_P1','current_P2','current_P3','operating_cost_and_original_preferences','actual_GT_power_change'}
    assert operating_planner.held_diagnostic_command_layers_complete('held_actual_diagnostic',required,required)
    assert not operating_planner.held_diagnostic_command_layers_complete('held_actual_diagnostic',required-{'operating_cost_and_original_preferences'},required)
    assert not operating_planner.held_diagnostic_command_layers_complete('current_P3',required,required)


@pytest.mark.parametrize('bad',[np.array([np.nan]),np.zeros(2)])
def test_refinement_does_not_relax_nonfinite_or_wrong_shape(bad):
    with pytest.raises(ValueError):refine('long_final_004',native_candidate=bad)


@pytest.mark.parametrize('changes',[dict(remaining_budget_seconds=0.),dict(allowed_relative_gap=0.),dict(native_gap=1.)])
def test_refinement_cannot_bypass_budget_or_original_gap_contract(changes):
    candidate,evidence=refine('long_final_004',**changes)
    assert candidate is None and not evidence['accepted']


def test_C_preparation_P1_late_stop_requires_all_service_T_protection_and_prior_preparation_locks():
    d,m=domain('long_final_002');phases={'arrived_current':{}}
    assert planner.late_soft_service_locks_complete('preparation_P1',m['rows'],phases,recovery=True)
    assert not planner.late_soft_service_locks_complete('preparation_P1',m['rows'],phases,recovery=False)
    assert not planner.late_soft_service_locks_complete('current_P3',m['rows'],phases,recovery=True)
    for name in ['lock.current_P3','lock.arrived_current_P3','lock.actual_command_shaft_support','lock.protection_target_excess',
        'lock.preparation_P0','lock.startup_bridge_P0','lock.startup_bridge_P1','lock.startup_bridge_surplus']:
        subset=[row for row in m['rows'] if row['name']!=name]
        assert not planner.late_soft_service_locks_complete('preparation_P1',subset,phases,recovery=True),name


@pytest.mark.parametrize('case',['long_final_004','long_final_006'])
def test_common_solver_wrapper_refines_saved_K1_without_dropping_failed_attempts(case):
    d,m=domain(case)
    result=numerics.solve_layer_with_lock_presentation(milp,d['objective'],integrality=d['integrality'],
        bounds=Bounds(d['bounds_lower'],d['bounds_upper']),constraints=[LinearConstraint(d['matrix'],d['lower'],d['upper'])],
        options=dict(time_limit=6.,mip_rel_gap=1e-6),rank=13 if case.endswith('004') else 11,variables=m['variables'],rows=m['rows'],locks=m['locks'],input_context={},
        previous_candidate=d['previous_x'])
    assert result.status==0 and len(result.attempts)==4
    assert all(not attempt['accepted'] for attempt in result.attempts)
    assert result.priority_lock_refinement['accepted'] and result.priority_lock_refinement['exact_optimum_claimed'] is False


def saved_inputs(case):
    extracted=json.loads((B/'review/final_failure_selected_extract01'/(case+'.json')).read_text(encoding='utf8'))
    obs=extracted['selected'];record=json.loads(Path(extracted['result_path']).read_text(encoding='utf8'));d=record['error']['diagnostic']
    is_c='state_coordinated_joint' in obs['requested_command'];request=obs['requested_command']['state_coordinated_joint' if is_c else 'operating_joint']
    context=copy.deepcopy(obs['operating_readiness'])
    if is_c:context.update(diesel_local_response=obs['diesel_local_response'],diesel_start_minute=d['state']['diesel_start_minute'])
    state=copy.deepcopy(d['state']);state['gt_prev']=np.asarray(state['gt_prev']);state['disconnected_loads']=np.asarray(state['disconnected_loads']);state['extra_faulted']=set(state['extra_faulted'])
    limits=obs['execution_limits'];available=bool(limits['grid_bus_state'] in ('ISLAND','RETURN_PREP') and state['diesel_status']=='CONNECTED' and d['availability']['DIESEL']>1e-9 and state['diesel_energy_remaining_kWh']>1e-9 and not obs['diesel_local_response']['rotor']['fuel_exhausted'])
    kwargs=dict(row=SimpleNamespace(**{**d['row'],'timestamp':pd.Timestamp(d['row']['timestamp'])}),plant=Plant(**d['plant']),loads=d['loads_kW'],availability=d['availability'],state=RuntimeState(**state),dt_h=d['dt_h'],grid_import_limit_kW=obs['features']['grid_import_limit_kW'],diesel_start_available=available,
        gt_ramp_kW_per_h=d['config']['gt_ramp_kW_per_hour'],solver_time_limit_sec=d['config']['solver_time_limit_sec'],config=d['config'],execution_contract=d['execution_contract'],joint_options=request['options'],risk_loads=request.get('risk_loads_kW'),readiness_context=context,execution_limits=limits)
    kwargs['coordination_options' if is_c else 'operating_options']=request['coordination' if is_c else 'operating_options']
    return importlib.import_module('tools.execution_recovery.'+('planner' if is_c else 'operating_planner')),kwargs


def test_saved_C_preparation_failure_returns_only_strict_prior_service_witness_with_unknown_soft_optima():
    module,inputs=saved_inputs('long_final_002');result=module.solve_joint(**inputs);diag=result['joint_diagnostic']
    fallback=diag['late_preference_fallbacks'][-1]
    assert fallback['layer']=='preparation_P1' and fallback['all_current_and_arrived_service_locks_complete']
    assert fallback['optional_layer_optimum_claimed'] is False and fallback['new_optimum_lock_added'] is False
    assert diag['engineering']['cost_optimum_USD_step'] is None
    assert 'preparation_P1' in fallback['unoptimized_soft_layers']
    assert 'lock_upper' not in next(layer for layer in diag['layers'] if layer['name']=='preparation_P1')


@pytest.mark.parametrize('case',['long_final_004','long_final_006'])
def test_saved_K1_full_pure_planner_emits_finite_legal_command_after_same_layer_refinement(case):
    from tools.operating_response.readiness import validate_dispatch
    module,inputs=saved_inputs(case);result=module.solve_joint(**inputs);diag=result['joint_diagnostic']
    validate_dispatch(result['action'])
    recovered=[item for item in diag['numerical_presentation_recoveries'] if item.get('accepted')]
    assert recovered and all(item['exact_optimum_claimed'] is False for item in recovered)
    expected='arrived_current_P3' if case.endswith('004') else 'current_P3'
    assert any(item['layer']==expected for item in recovered)
    layer=next(item for item in diag['layers'] if item['name']==expected)
    assert layer['optimization_status']=='gap_certified_feasible_numerical_refinement'
    assert layer['lock_upper']==layer['objective_value']
    held=diag['risk_scenarios']['held_actual']
    assert held['optimization_status']=='unknown_solver_failure' and held['executable_preparation_credit'] is False
    assert diag['engineering']['cost_optimum_USD_step'] is not None
