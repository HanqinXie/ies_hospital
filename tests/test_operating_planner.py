"""Real bounded joint solves: finite one-command phases affect decisions."""
import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from test_engineering_planner import problem, BASE, NORMAL_START_STATES, P0_PREPARATION_STATES, normal_start_problem, current_arrival_without_new_GT
from test_operating_readiness import current, dispatch


def api():
    try: return importlib.import_module('tools.operating_response.planner')
    except ModuleNotFoundError: pytest.fail('The v15 phase-aware joint planner is missing')


def solve(p, **options):
    c = current(p['state'].gt_prev.tolist())
    c['arrived_dispatch'] = dispatch(p['state'].gt_prev.tolist(), grid=p['grid_import_limit_kW'])
    return api().solve_joint(**p, joint_options=BASE, readiness_context=c,
        operating_options=dict(preparation_profile='K2', **options))


def test_early_single_GT_preparation_does_not_credit_new_GT_before_ready(problem):
    p = normal_start_problem(problem, NORMAL_START_STATES[0])
    result = solve(p)
    phases = result['joint_diagnostic']['readiness_phases']
    assert phases['held_before_arrival']['actual_gt_electric_kW'] == p['state'].gt_prev.tolist()
    assert phases['arrived_before_new_ready']['gt_electric_kW'][0] == 0.
    assert phases['arrived_before_new_ready']['hrsg_gt_basis_kW'][0] == 0.
    assert result['joint_diagnostic']['preparation_profile']['critical_multiplier'] == 2.
    assert max(map(abs, result['energy_balance_residuals'])) < 1e-4


@pytest.mark.parametrize('value', NORMAL_START_STATES[:2])
def test_phase_service_constraint_changes_unsafe_static_preparation_choice(problem, value):
    p = normal_start_problem(problem, value)
    disabled = solve(p, phase_constraints_enabled=False)
    assert current_arrival_without_new_GT(p, disabled)['lost'][3, 0] > 1.
    guarded = solve(p, phase_constraints_enabled=True)
    executed = current_arrival_without_new_GT(p, guarded)
    assert executed['lost'][3, 0] < .01
    assert np.max(executed['lost'][:, 2]) < .01
    d = guarded['joint_diagnostic']; order = d['objective_order']
    assert order.index('arrived_current_P3') < order.index('preparation_P0')
    assert order.index('preparation_P1') < order.index('normal_current_GT_state_changes')
    assert d['engineering']['normal_commitment_preference']['P1_preparation_precedes_commitment'] is True
    assert np.max(guarded['lost'][2:]) < 1e-7
    assert d['readiness_phases']['arrived_before_new_ready']['unserved_kW'][3][0] < .01


def test_nonbinding_preparation_keeps_actual_GT_ports_without_useless_switching(problem):
    p = copy.deepcopy(problem); p['loads'][:] = 0.; p['loads'][:, 0] = [20., 20., 100., 300.]
    p['state'].gt_prev[:] = [128.,128.,0.]; p['grid_import_limit_kW'] = 850.
    p['execution_contract']['gt_start_delay_minutes'] = 10.
    result = solve(p)
    assert (np.array(result['action']['gt_electric_kW'])>1e-4).tolist() == [True,True,False]
    assert np.max(result['lost'][2:]) < 1e-7


def test_connected_zero_diesel_is_not_counted_in_held_actual_supply(problem):
    c = current(problem['state'].gt_prev.tolist(), diesel_connected=True, diesel_arrived_cap_kW=300.,
        arrived_dispatch=dispatch(problem['state'].gt_prev.tolist(), diesel=300.))
    result = api().solve_joint(**problem, joint_options=BASE, readiness_context=c)
    assert result['joint_diagnostic']['readiness_phases']['held_before_arrival']['actual_diesel_kW'] == 0.


def test_readiness_actual_GT_must_equal_physical_current_state(problem):
    c = current([0.,0.,0.])
    with pytest.raises(ValueError): api().solve_joint(**problem, readiness_context=c)


def test_joint_critical_cooling_auxiliary_is_not_independent_electric_capacity(problem):
    p = copy.deepcopy(problem); p['loads'][:] = 0.; p['loads'][0] = [300.,0.,5000.]
    p['grid_import_limit_kW'] = 0.; p['availability']['GRID'] = 0.
    result = solve(p)
    pressure = result['joint_diagnostic']['risk_scenarios']
    assert any(np.asarray(case['unserved_kW'])[0,2] > 1. for case in pressure.values())
    assert result['lost'][0,2] > 1.


def test_current_pre_ready_diesel_cannot_exceed_already_arrived_connected_cap(problem):
    p = copy.deepcopy(problem); p['loads'][:] = 0.; p['loads'][:,0] = [300.,300.,100.,100.]
    p['state'].gt_prev[:] = [128.,0.,128.]; p['availability']['GRID'] = 0.
    p['diesel_start_available'] = True; p['state'].last_diesel_kW = 100.
    c = current(p['state'].gt_prev.tolist(),diesel_connected=True,actual_diesel_kW=100.,
        diesel_arrived_cap_kW=100.,arrived_dispatch=dispatch(p['state'].gt_prev.tolist(),grid=0.,diesel=100.))
    r = api().solve_joint(**p,joint_options=BASE,readiness_context=c)
    witness = r['joint_diagnostic']['risk_scenarios']['arrived_current']
    assert witness['diesel_kW'] <= 100.+1e-6
    assert r['joint_diagnostic']['readiness_phases']['arrived_before_new_ready']['diesel_permitted_cap_kW'] <= 100.+1e-6


def test_each_single_hrsg_loss_record_uses_that_contingencys_heat_mask(problem):
    r = solve(problem)
    for name,case in r['joint_diagnostic']['risk_scenarios'].items():
        if name.startswith('HRSG_'):
            i = int(name[5])-1
            basis = np.asarray(case['gt_electric_kW']).copy(); basis[i] = 0.
            # The output report must name the same physical heat source used
            # in this joint witness, not report the unfailed aggregate steam.
            assert case['hrsg_gt_basis_kW'] == basis.tolist()


def test_pre_ready_outage_preparation_is_reported_even_when_post_command_is_feasible(problem):
    # Use the actual early45 legal pre-event load: NORMAL_START_STATES[0]
    # has P0=60.38kW, so even K2 P0 fits the single running GT and is not a
    # valid positive-P0-deficit fixture. No tolerance/assertion is weakened.
    p = copy.deepcopy(problem); temp,loads,_,_ = P0_PREPARATION_STATES[0]
    p['row'].outdoor_temperature_C = temp; p['loads'] = np.asarray(loads)
    p['state'].gt_prev[:] = [0.,132.343808,0.]; p['grid_import_limit_kW'] = 850.
    p['execution_contract']['gt_start_delay_minutes'] = 10.
    r = solve(p); risks = r['joint_diagnostic']['risk_scenarios']
    assert risks['pre_GRID_loss']['gt_electric_kW'][0] == 0.
    assert risks['pre_GRID_loss']['hrsg_gt_basis_kW'][0] == 0.
    assert risks['pre_GRID_loss']['unserved_kW'][0][0] > 0.
    assert 'pre_GRID_loss' in r['joint_diagnostic']['readiness_phases']['arrived_before_new_ready']['preparation_scenarios']
    assert 'GRID_loss' in r['joint_diagnostic']['readiness_phases']['committed_ready']['preparation_scenarios']


def test_public_planner_default_profile_actually_creates_its_joint_preparation_copies(problem):
    r = api().solve_joint(**problem,readiness_context=current(problem['state'].gt_prev.tolist()))
    assert r['joint_diagnostic']['preparation_profile']['name'] == 'K1'
    assert 'GRID_loss' in r['joint_diagnostic']['risk_scenarios']
    assert 'pre_GRID_loss' in r['joint_diagnostic']['risk_scenarios']


@pytest.mark.parametrize('day',[282,47])
def test_recorded_healthy_single_step_does_not_fail_optional_power_tie_break(day):
    from hospital_ies.model import Plant
    from hospital_ies.resilience import RuntimeState
    root = Path(__file__).resolve().parents[1]
    path = root/f'outputs/v15_engineering_operation_20261017_run01/review/planner_numerical_repros_ramp_v2/day{day}_K1_single_step.json'
    d = json.loads(path.read_text(encoding='utf-8')); state = copy.deepcopy(d['state'])
    for key in ('gt_prev','overload_minutes','disconnected_loads'):state[key] = np.asarray(state[key],float)
    state['extra_faulted'] = set(state['extra_faulted'])
    kwargs = {key:d[key] for key in ('dt_h','grid_import_limit_kW','diesel_start_available',
        'gt_ramp_kW_per_h','solver_time_limit_sec','config','execution_contract',
        'joint_options','operating_options','risk_loads','readiness_context')}
    result = api().solve_joint(row=SimpleNamespace(timestamp=pd.Timestamp(d['row']['timestamp']),
        outdoor_temperature_C=d['row']['outdoor_temperature_C']),plant=Plant(**d['plant']),
        loads=np.asarray(d['loads']),availability=d['availability'],state=RuntimeState(**state),**kwargs)
    assert max(map(abs,result['energy_balance_residuals'])) < 1e-4
    assert result['joint_diagnostic']['current_priority_locks_unchanged'] is True
    fallback = result['joint_diagnostic']['late_preference_fallbacks']
    assert len(fallback) == 1 and fallback[0]['layer'] == 'actual_GT_power_change'
    assert fallback[0]['optional_layer_optimum_claimed'] is False
    assert fallback[0]['validation']['max_lock_violation'] <= 1e-10
    assert fallback[0]['validation']['max_original_violation'] <= 1e-7
    layer = next(x for x in result['joint_diagnostic']['layers'] if x['name']=='actual_GT_power_change')
    assert layer['optimization_status'] == 'not_optimized_reused_preceding_feasible_incumbent'
    assert result['optimizer_warning']
    # Original-unit posterior lock acceptance remains1e-10; a solver-native
    # OPTIMAL result outside it must not be silently promoted to passing.
    for layer in result['solver_backend_layers']:
        for attempt in layer.get('attempts',[]):
            if attempt.get('accepted'):
                assert attempt['validation']['max_lock_violation'] <= 1e-10


def test_recorded_early45_t60_soft_cost_layer_can_return_a_valid_current_command():
    """Recorded numerical cost-domain false infeasibility, one current state."""
    from hospital_ies.model import Plant
    from hospital_ies.resilience import RuntimeState
    root = Path(__file__).resolve().parents[1]
    path = root/'outputs/v15_engineering_operation_20261017_run01/review/planner_numerical_repros_ramp_v2/early45_K1_t60_single_step.json'
    d = json.loads(path.read_text(encoding='utf-8'));state=copy.deepcopy(d['state'])
    for key in ('gt_prev','overload_minutes','disconnected_loads'):state[key]=np.asarray(state[key],float)
    state['extra_faulted']=set(state['extra_faulted'])
    kwargs={key:d[key] for key in ('dt_h','grid_import_limit_kW','diesel_start_available',
        'gt_ramp_kW_per_h','solver_time_limit_sec','config','execution_contract',
        'joint_options','operating_options','risk_loads','readiness_context')}
    result=api().solve_joint(row=SimpleNamespace(timestamp=pd.Timestamp(d['row']['timestamp']),
        outdoor_temperature_C=d['row']['outdoor_temperature_C']),plant=Plant(**d['plant']),
        loads=np.asarray(d['loads']),availability=d['availability'],state=RuntimeState(**state),**kwargs)
    assert max(map(abs,result['energy_balance_residuals'])) < 1e-4
    assert result['action']['gt_electric_kW'][:2] == [0.,0.]
    recovery=result['joint_diagnostic']['numerical_presentation_recoveries']
    assert len(recovery)==1 and recovery[0]['layer']=='operating_cost_and_original_preferences'
    assert recovery[0]['original_status']==2
    assert recovery[0]['original_domain_and_thresholds_unchanged'] is True
    assert recovery[0]['previous_incumbent_validation']['max_lock_violation'] <= 1e-10
    assert result['joint_diagnostic']['solver_row_scaling'] is True
    cost=[x for x in result['solver_backend_layers'] if x['layer']=='operating_cost_and_original_preferences']
    assert [x['status'] for x in cost]==[2,0]
    assert cost[1]['presentation']=='positive_row_scaling_same_original_domain'
    assert all(x['validation']['max_lock_violation']<=1e-10 for x in cost[1]['attempts'] if x['accepted'])
