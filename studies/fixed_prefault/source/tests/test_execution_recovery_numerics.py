"""Exact old failure and bounded original-domain command finalization."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import numpy as np
import pytest
from scipy.optimize import LinearConstraint

from hospital_ies.model import RT_TO_KW
from tools.operating_response.readiness import validate_dispatch
from test_engineering_planner import problem, BASE, context as engineering_context
from test_operating_readiness import current

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'outputs/v17_state_coordinated_20261018_run01/review/ablation_interface_math_01'


def api():
    name = 'tools.execution_recovery.numerics'
    assert importlib.util.find_spec(name) is not None, 'bounded command finalization is missing'
    return importlib.import_module(name).finalize_command_vector


def dispatch(x):
    return dict(gt_electric_kW=[0., 0., 0.], grid_import_kW=float(x[0]), diesel_kW=0.)


def simple(x, *, matrix=None, low=None, high=None, kind='physical', scale=1., integer=0,
           build=dispatch, upper=10.):
    a = np.zeros((0, 1)) if matrix is None else np.asarray(matrix).reshape(-1, 1)
    m = len(a)
    return api()(x, constraints=[LinearConstraint(a, np.full(m, -np.inf) if low is None else low,
                                                 np.full(m, np.inf) if high is None else high)],
        lower=np.array([0.]), upper=np.array([upper]), integrality=np.array([integer]),
        rows=[dict(name='original', kind=kind, unit='native') for _ in range(m)],
        variables=[dict(index=0, name='command.grid', unit='native')],
        emitted_coordinates={0: scale}, build_command=build, objectives=[('objective', np.ones(1))])


def test_saved_old_exact_interface_failure_is_reproduced():
    with np.load(FIXTURE / 'selected_original_domain.npz') as z:
        x = z['x']
    with pytest.raises(ValueError, match='electric_chiller_cooling_kW'):
        validate_dispatch(dict(gt_electric_kW=x[:3].tolist(), grid_import_kW=0., diesel_kW=0.,
            electric_chiller_cooling_kW=(x[28:31] * RT_TO_KW).tolist()))


def test_saved_two_coordinate_repair_preserves_all_original_locks_and_objectives():
    with np.load(FIXTURE / 'selected_original_domain.npz') as z:
        domain = {k: z[k].copy() for k in z.files}
    x = domain['x']; original = x.copy()
    rows = json.loads((FIXTURE / 'original_rows.json').read_text(encoding='utf-8'))
    objectives = []
    for path in sorted((FIXTURE / 'layers').glob('*.npz')):
        with np.load(path) as z:
            objectives.append((path.stem, z['objective'].copy()))
    def build(y):
        return dict(gt_electric_kW=y[:3].tolist(), grid_import_kW=0., diesel_kW=0.,
            electric_chiller_cooling_kW=(y[28:31] * RT_TO_KW).tolist())
    y, action, log = api()(x,
        constraints=[LinearConstraint(domain['matrix'], domain['lower'], domain['upper'])],
        lower=domain['bounds_lower'], upper=domain['bounds_upper'], integrality=domain['integrality'],
        rows=rows, variables=[dict(index=i, name=f'x{i}', unit='RT' if i in (28, 29, 30) else 'original')
                             for i in range(len(x))],
        emitted_coordinates={28: RT_TO_KW, 29: RT_TO_KW, 30: RT_TO_KW},
        build_command=build, objectives=objectives)
    assert np.array_equal(x, original)
    assert np.flatnonzero(x != y).tolist() == [28, 29]
    assert np.array_equal(y[14:26], x[14:26])
    assert action['electric_chiller_cooling_kW'][:2] == [0., 0.]
    assert len(log['objective_deltas']) == 21
    assert all(item['delta'] == 0. for item in log['objective_deltas'])
    assert log['validation']['max_original_violation'] <= 1e-7
    assert log['validation']['max_lock_violation'] <= 1e-10
    assert log['validation']['max_integrality_distance'] <= 1e-9
    assert [c['index'] for c in log['corrections']] == [28, 29]
    assert len(log['validation']['rows']) == 630


@pytest.mark.parametrize('value', [-1e-3, -1.01e-7])
def test_material_negative_is_rejected(value):
    with pytest.raises(ValueError, match='boundary|domain'):
        simple([value])


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_is_rejected(value):
    with pytest.raises(ValueError, match='finite'):
        simple([value])


@pytest.mark.parametrize('value', [[[0.]], [], [False], ['0']])
def test_wrong_shape_or_numeric_type_is_rejected(value):
    with pytest.raises(ValueError, match='shape|numeric'):
        simple(value)


def test_unit_conversion_cannot_expand_emitted_residual_budget():
    with pytest.raises(ValueError, match='emitted|boundary'):
        simple([-5e-8], scale=RT_TO_KW)


def test_repair_that_breaks_original_physical_row_is_rejected():
    with pytest.raises(ValueError, match='original domain'):
        simple([-5e-9], matrix=[[1e4]], low=[-5e-5], high=[-5e-5])


def test_repair_that_breaks_original_lock_is_rejected():
    with pytest.raises(ValueError, match='original domain'):
        simple([-5e-9], matrix=[[1.]], high=[-4e-9], kind='priority_lock')


def test_integer_failure_is_not_hidden_by_command_repair():
    with pytest.raises(ValueError, match='original domain'):
        simple([.5], integer=1)


def test_positive_interior_is_not_rounded_to_zero():
    y, action, log = simple([5e-12])
    assert y[0] == 5e-12 and action['grid_import_kW'] == 5e-12
    assert log['corrections'] == []


def test_small_upper_boundary_residue_is_finalized_and_logged():
    y, action, log = simple([10. + 5e-9])
    assert y[0] == action['grid_import_kW'] == 10.
    assert log['corrections'][0]['delta'] < 0.


@pytest.mark.parametrize('build', [lambda y: dict(gt_electric_kW=[[0., 0., 0.]], grid_import_kW=0., diesel_kW=0.),
    lambda y: dict(gt_electric_kW=[0., 0., 0.], grid_import_kW=float('nan'), diesel_kW=0.),
    lambda y: dict(gt_electric_kW=[0., 0., 0.], grid_import_kW=-1e-12, diesel_kW=0.)])
def test_final_actual_serialized_command_is_exactly_validated(build):
    with pytest.raises(ValueError, match='readiness'):
        simple([0.], build=build)


def limits(**changes):
    value = dict(schema='hospital_ies.execution_limits.v18.v1', hardware_grid_available_fraction=0.,
        grid_bus_state='ISLAND', effective_grid_import_limit_kW=0., effective_diesel_limit_kW=0.,
        diesel_conditionally_permitted=True, grid_transfer_remaining_minutes=None,
        gt_protection_reference_kW=320., gt_trip_multiple=1.05, gt_hold_minutes=15.,
        gt_overload_minutes=[0., 0., 0.])
    value.update(changes)
    return value


@pytest.mark.parametrize('module', ['engineering_planner', 'operating_planner'])
def test_baseline_forks_can_send_conditional_diesel_without_prestarting_delivery(problem, module):
    name = 'tools.execution_recovery.' + module
    assert importlib.util.find_spec(name) is not None, 'M18 baseline compatibility fork is missing'
    planner = importlib.import_module(name)
    p = problem
    p['loads'][:] = 0.; p['loads'][0, 0] = 200.
    p['availability']['GRID'] = 0.
    for i in (1, 2):
        p['availability'][f'GT_{i+1}'] = 0.
    p['state'].gt_prev[:] = [250., 0., 0.]
    kwargs = dict(joint_options=BASE, execution_limits=limits())
    if module == 'engineering_planner':
        kwargs['execution_context'] = engineering_context(p)
    else:
        kwargs['readiness_context'] = current([250., 0., 0.])
    result = planner.solve_joint(**p, **kwargs)
    assert result['planned_current_action']['diesel_kW'] == 0.
    assert result['planned_current_action']['grid_import_kW'] == 0.
    assert result['action']['diesel_kW'] > 1.
    diag = result['joint_diagnostic']['m18_interface_compatibility']
    assert diag['conditional_grid_permission_upper_kW'] == p['grid_import_limit_kW']
    assert isinstance(diag['execution_limits'], dict)
    assert diag['original_preference_order_and_coefficients_preserved'] is True
    assert result['joint_diagnostic']['numerical_finalization']['exact_finite_nonnegative_dispatch_validated']


@pytest.mark.parametrize('module', ['engineering_planner', 'operating_planner'])
def test_baseline_forks_reject_unregistered_execution_limits(problem, module):
    name = 'tools.execution_recovery.' + module
    assert importlib.util.find_spec(name) is not None, 'M18 baseline compatibility fork is missing'
    planner = importlib.import_module(name)
    kwargs = dict(readiness_context=current(problem['state'].gt_prev.tolist())) if module == 'operating_planner' else {}
    with pytest.raises(ValueError, match='execution limits'):
        planner.solve_joint(**problem, execution_limits=limits(future_repair_minute=100.), **kwargs)


@pytest.mark.parametrize('module', ['engineering_planner', 'operating_planner'])
def test_baseline_compatibility_preserves_original_without_m18_limits(problem, module):
    new = importlib.import_module('tools.execution_recovery.' + module)
    old = importlib.import_module('tools.engineering_model.planner' if module == 'engineering_planner'
                                  else 'tools.operating_response.planner')
    kwargs = dict(joint_options=BASE)
    if module == 'engineering_planner':
        kwargs['execution_context'] = engineering_context(problem)
    else:
        kwargs['readiness_context'] = current(problem['state'].gt_prev.tolist())
    original, migrated = old.solve_joint(**problem, **kwargs), new.solve_joint(**problem, **kwargs)
    assert [x['name'] for x in original['joint_diagnostic']['layers']] == [x['name'] for x in migrated['joint_diagnostic']['layers']]
    np.testing.assert_allclose(original['lost'], migrated['lost'], atol=1e-7, rtol=0.)
    for key in original['action']:
        np.testing.assert_allclose(original['action'][key], migrated['action'][key], atol=1e-7, rtol=0.)


@pytest.mark.parametrize('module', ['engineering_planner', 'operating_planner'])
def test_baseline_connected_diesel_new_permission_is_not_locked_to_old_zero(problem, module):
    planner = importlib.import_module('tools.execution_recovery.' + module)
    p = problem; p['loads'][:] = 0.; p['loads'][0, 0] = 200.
    p['availability']['GRID'] = 0.
    for i in range(3): p['availability'][f'GT_{i+1}'] = 0.
    p['state'].gt_prev[:] = 0.; p['diesel_start_available'] = True
    kwargs = dict(joint_options=BASE, execution_limits=limits())
    if module == 'engineering_planner': kwargs['execution_context'] = engineering_context(p)
    else:
        c = current([0., 0., 0.]); c['diesel_connected'] = True
        kwargs['readiness_context'] = c
    result = planner.solve_joint(**p, **kwargs)
    assert result['planned_current_action']['diesel_kW'] > 199.
    assert result['action']['diesel_kW'] >= result['planned_current_action']['diesel_kW'] - 1e-7
    assert result['planned_current_action']['grid_import_kW'] == 0.


@pytest.mark.parametrize('module', ['engineering_planner', 'operating_planner'])
def test_baseline_preparation_does_not_credit_parallel_grid_and_diesel(problem, module):
    planner = importlib.import_module('tools.execution_recovery.' + module)
    p = problem
    kwargs = dict(joint_options=BASE, execution_limits=limits(grid_bus_state='GRID',
        hardware_grid_available_fraction=1., effective_grid_import_limit_kW=350.))
    if module == 'engineering_planner': kwargs['execution_context'] = engineering_context(p)
    else: kwargs['readiness_context'] = current(p['state'].gt_prev.tolist())
    result = planner.solve_joint(**p, **kwargs)
    for name, case in result['joint_diagnostic']['risk_scenarios'].items():
        assert min(case['grid_import_kW'], case['diesel_kW']) <= 1e-7, name


@pytest.mark.parametrize('module', ['engineering_planner', 'operating_planner'])
@pytest.mark.parametrize('remaining', [1., 0.])
def test_conditional_permission_is_capacity_and_low_fuel_only_limits_actual_use(problem, module, remaining):
    planner = importlib.import_module('tools.execution_recovery.' + module)
    p = problem; p['loads'][:] = 0.; p['loads'][0, 0] = 200.
    p['availability']['GRID'] = 0.
    for i in range(3): p['availability'][f'GT_{i+1}'] = 0.
    p['state'].gt_prev[:] = 0.; p['state'].diesel_energy_remaining_kWh = remaining
    p['diesel_start_available'] = True
    kwargs = dict(joint_options=BASE, execution_limits=limits(diesel_conditionally_permitted=remaining > 0.))
    if module == 'engineering_planner': kwargs['execution_context'] = engineering_context(p)
    else:
        c = current([0., 0., 0.]); c['diesel_connected'] = True
        kwargs['readiness_context'] = c
    result = planner.solve_joint(**p, **kwargs)
    d = result['joint_diagnostic']['m18_interface_compatibility']
    assert d['conditional_diesel_permission_upper_kW'] == (p['plant'].emergency_diesel_kW if remaining else 0.)
    assert d['actual_diesel_limit_kW'] == remaining / p['dt_h']
    assert result['planned_current_action']['diesel_kW'] <= remaining / p['dt_h'] + 1e-7
    assert result['lost'][0, 0] >= 200. - remaining / p['dt_h'] - 1e-4


def conditional_permission(*, execution=None, locked_upper=40., start=0.):
    module = importlib.import_module('tools.execution_recovery.numerics')
    assert hasattr(module, 'finalize_conditional_grid_permission'), 'common M18 return-permission compatibility is missing'
    x = np.array([200., 0., 0., 1., 0., 0., start])
    matrix = np.zeros((2, 7)); matrix[0, 0] = 1.; matrix[1, 6] = 1.
    return module.finalize_conditional_grid_permission(x,
        constraints=[LinearConstraint(matrix, [200., -np.inf], [200., locked_upper])],
        lower=np.zeros(7), upper=np.array([400.,400.,400.,1.,1.,1.,350.]),
        integrality=np.array([0,0,0,1,1,1,0]), ceiling=6,
        rows=[dict(name='actual_GT_fixed',kind='physical'),dict(name='original_permission_lock',kind='priority_lock')],
        variables=[dict(index=i,name=f'x{i}',unit='kW') for i in range(7)],
        execution_limits=execution or limits(hardware_grid_available_fraction=1.),
        objectives=[('actual_cost', np.array([1.,0.,0.,0.,0.,0.,0.]))])


def test_common_return_permission_changes_only_masked_permission_inside_original_lock():
    y, d = conditional_permission()
    assert 39. < y[6] <= 40.
    assert np.array_equal(y[:6], [200.,0.,0.,1.,0.,0.])
    assert d['applied'] and d['all_non_permission_coordinates_unchanged']
    assert d['validation']['max_lock_violation'] <= 1e-10
    assert all(x['delta'] == 0. for x in d['objective_deltas'])


@pytest.mark.parametrize('execution', [limits(hardware_grid_available_fraction=0.),
    limits(hardware_grid_available_fraction=1.,grid_bus_state='GRID',effective_grid_import_limit_kW=350.)])
def test_common_return_permission_does_not_enable_hardware_fault_or_live_grid(execution):
    y, d = conditional_permission(execution=execution)
    assert y[6] == 0. and not d['applied']


def test_common_return_permission_respects_original_lock_that_forbids_increase():
    y, d = conditional_permission(locked_upper=0.)
    assert y[6] == 0. and not d['applied']
    assert d['reason'] == 'original_domain_does_not_allow_larger_permission'


@pytest.mark.parametrize('module', ['engineering_planner', 'operating_planner'])
def test_common_m18_return_permission_avoids_baseline_masking_self_gate(problem, module):
    planner = importlib.import_module('tools.execution_recovery.' + module)
    p = problem; p['loads'][:] = 0.; p['loads'][0, 0] = 200.
    p['availability']['GRID'] = 0.; p['state'].gt_prev[:] = [250.,0.,0.]
    kwargs = dict(joint_options=BASE, execution_limits=limits(hardware_grid_available_fraction=1.))
    if module == 'engineering_planner': kwargs['execution_context'] = engineering_context(p)
    else: kwargs['readiness_context'] = current(p['state'].gt_prev.tolist())
    result = planner.solve_joint(**p, **kwargs)
    assert result['planned_current_action']['grid_import_kW'] == 0.
    assert result['action']['grid_import_kW'] > 1.
    assert result['joint_diagnostic']['m18_conditional_return_permission']['applied']
    # This common compatibility does not turn on the old live-GRID preference.
    assert result['joint_diagnostic']['engineering']['options']['grid_headroom_preference'] is False


@pytest.mark.parametrize('case', ['key01_000', 'key01_001'])
def test_same_domain_priority_row_presentation_recovers_saved_native_lock_failure(case):
    from scipy.optimize import Bounds
    from hospital_ies.solver_backend import milp
    from hospital_ies.solver_diagnostics import validate_vector, LayerCollector, collect_layers
    module = importlib.import_module('tools.execution_recovery.numerics')
    assert hasattr(module, 'solve_layer_with_lock_presentation'), 'bounded same-domain lock presentation is missing'
    path = ROOT/'outputs/v18_execution_recovery_20261018_run01/review/key01_c4_solver'/case
    with np.load(path/'domain.npz') as z:
        d = {key:z[key].copy() for key in z.files}
    metadata = json.loads((path/'rows_variables.json').read_text(encoding='utf-8'))
    collector = LayerCollector()
    with collect_layers(collector):
        result = module.solve_layer_with_lock_presentation(milp,d['objective'],
            integrality=d['integrality'],bounds=Bounds(d['bounds_lower'],d['bounds_upper']),
            constraints=[LinearConstraint(d['matrix'],d['lower'],d['upper'])],
            options=dict(time_limit=6.,mip_rel_gap=1e-6),rank=14,variables=metadata['variables'],
            rows=metadata['rows'],locks=[],input_context=dict(saved_case=case),row_scaling=False)
    assert result.status == 0
    validation = validate_vector(d['matrix'],d['lower'],d['upper'],d['bounds_lower'],d['bounds_upper'],
                                 d['integrality'],result.x,metadata['rows'])
    assert validation['max_original_violation'] <= 1e-7
    assert validation['max_lock_violation'] <= 1e-10
    assert validation['max_integrality_distance'] <= 1e-9
    assert len(result.attempts) >= 3
    assert not result.attempts[0]['accepted']
    assert result.attempts[-1]['accepted']
    assert result.attempts[-1]['presentation'] == 'priority_lock_rows_positive_x1000'
    evidence = result.same_domain_presentation_recovery
    assert evidence['recovery_attempted'] and evidence['recovery_accepted']
    assert 0. < evidence['remaining_budget_seconds_at_recovery'] <= 6.
    assert evidence['original_objective_bounds_and_locks_unchanged']
    assert len(collector.layers) == 1
    assert collector.layers[0]['same_domain_presentation_recovery']['recovery_accepted']


def saved_failure_layer_call(solver, collector, *, budget=6.):
    from scipy.optimize import Bounds
    from hospital_ies.solver_diagnostics import collect_layers
    module = importlib.import_module('tools.execution_recovery.numerics')
    path = ROOT/'outputs/v18_execution_recovery_20261018_run01/review/key01_c4_solver/key01_000'
    with np.load(path/'domain.npz') as z:
        d = {key:z[key].copy() for key in z.files}
    m = json.loads((path/'rows_variables.json').read_text(encoding='utf-8'))
    with collect_layers(collector):
        return module.solve_layer_with_lock_presentation(solver,d['objective'],
            integrality=d['integrality'],bounds=Bounds(d['bounds_lower'],d['bounds_upper']),
            constraints=[LinearConstraint(d['matrix'],d['lower'],d['upper'])],
            options=dict(time_limit=budget,mip_rel_gap=1e-6),rank=14,variables=m['variables'],
            rows=m['rows'],locks=[],input_context=dict(saved_case='key01_000'),row_scaling=False)


def test_recovery_exception_retains_completed_real_native_attempts_and_collector_evidence():
    from hospital_ies.execution import SolverExecutionError
    from hospital_ies.solver_backend import milp
    from hospital_ies.solver_diagnostics import LayerCollector
    collector = LayerCollector(); calls = []
    def native_then_raise(c, **kwargs):
        calls.append(kwargs['options']['time_limit'])
        if len(calls) == 2:
            raise SolverExecutionError('injected recovery transport exception after real native failure')
        return milp(c, **kwargs)
    with pytest.raises(SolverExecutionError, match='recovery transport exception'):
        saved_failure_layer_call(native_then_raise, collector)
    assert len(calls) == 2 and len(collector.layers) == 1
    layer = collector.layers[0]
    assert layer['result']['error_type'] == 'SolverExecutionError'
    evidence = layer['same_domain_presentation_recovery']
    assert evidence['recovery_attempted'] and not evidence['recovery_accepted']
    assert evidence['exception_type'] == 'SolverExecutionError'
    assert len(evidence['relative_row_scales']) == 900
    assert layer['solver']['max_attempts_per_layer'] == 4
    attempts = layer['result']['attempts']
    assert len(attempts) == 2 and all(not a['accepted'] for a in attempts)
    assert all(a['presentation'] == 'initial_original_domain_rows' for a in attempts)


def test_exhausted_original_budget_skips_recovery_with_explicit_failure_evidence():
    import time
    from hospital_ies.solver_backend import milp
    from hospital_ies.solver_diagnostics import LayerCollector
    collector = LayerCollector(); calls = []; budget = .4
    def native_then_consume_original_budget(c, **kwargs):
        calls.append(kwargs['options']['time_limit'])
        started = time.perf_counter()
        result = milp(c, **kwargs)
        time.sleep(max(0., budget+.02-(time.perf_counter()-started)))
        return result
    result = saved_failure_layer_call(native_then_consume_original_budget, collector, budget=budget)
    assert len(calls) == 1 and result.status == 4
    evidence = collector.layers[0]['same_domain_presentation_recovery']
    assert not evidence['recovery_attempted'] and not evidence['recovery_accepted']
    assert evidence['reason'] == 'original_layer_time_budget_exhausted_before_recovery'
    assert evidence['remaining_budget_seconds_at_recovery'] == 0.
    assert evidence['original_budget_seconds'] == budget
    assert len(collector.layers[0]['result']['attempts']) == 2


@pytest.mark.parametrize('module', ['engineering_planner', 'operating_planner'])
def test_legacy_static_surplus_is_explicitly_uncredited_without_changing_policy(problem, module):
    new = importlib.import_module('tools.execution_recovery.' + module)
    old = importlib.import_module('tools.engineering_model.planner' if module == 'engineering_planner'
                                  else 'tools.operating_response.planner')
    kwargs = dict(joint_options=BASE, risk_loads=np.zeros((4,3)))
    if module == 'engineering_planner': kwargs['execution_context'] = engineering_context(problem)
    else: kwargs['readiness_context'] = current(problem['state'].gt_prev.tolist())
    original = old.solve_joint(**problem, **kwargs)
    result = new.solve_joint(**problem, **kwargs)
    risks = result['joint_diagnostic']['risk_scenarios']
    assert risks['low_history']['electric_surplus_defect_kW'] > 1.
    for case in risks.values():
        assert case['executable_preparation_credit'] is False
        assert case['has_physical_balance_certificate'] is False
        assert case['preparation_credit_schema'] == 'hospital_ies.static_preparation_credit.v18.v1'
        assert case['preparation_status'] == ('uncredited_static_witness' if case['electric_surplus_defect_kW'] > 0.
                                               else 'uncertified_static_witness')
    # A log/schema migration cannot alter the original mathematical program.
    np.testing.assert_array_equal(original['lost'], result['lost'])
    for key in original['action']:
        np.testing.assert_array_equal(original['action'][key], result['action'][key])
    # The earlier numerical migration adds held-diagnostic final-value
    # metadata; every original optimization field must still match exactly.
    assert len(original['joint_diagnostic']['layers']) == len(result['joint_diagnostic']['layers'])
    for old_layer,new_layer in zip(original['joint_diagnostic']['layers'],result['joint_diagnostic']['layers']):
        for key,value in old_layer.items():
            assert new_layer[key] == value
    if module == 'operating_planner':
        phases = result['joint_diagnostic']['readiness_phases']
        assert phases['committed_ready']['executable_preparation_credit'] is False
        for name in ('held_before_arrival','arrived_before_new_ready'):
            assert phases[name]['executable_preparation_credit'] is False


def v2_limits(**updates):
    from test_operating_readiness import dispatch
    effective = dispatch([200.,0.,0.],grid=0.)
    effective.update(boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.]*3)
    return limits(schema='hospital_ies.execution_limits.v18.v2',effective_dispatch=effective,**updates)


def test_v2_effective_dispatch_is_strict_and_copied():
    import copy
    module = importlib.import_module('tools.execution_recovery.numerics')
    source = v2_limits(); result = module.validate_execution_limits(source)
    assert result['effective_dispatch']['boiler_steam_lbph'] == 0.
    result['effective_dispatch']['gt_electric_kW'][0] = 0.
    assert source['effective_dispatch']['gt_electric_kW'][0] == 200.
    for change in ('missing','extra','nonfinite','shape','inconsistent_grid'):
        bad = copy.deepcopy(source)
        if change == 'missing': bad['effective_dispatch'].pop('boiler_steam_lbph')
        elif change == 'extra': bad['effective_dispatch']['future_boiler_output'] = 300.
        elif change == 'nonfinite': bad['effective_dispatch']['boiler_steam_lbph'] = float('nan')
        elif change == 'shape': bad['effective_dispatch']['electric_chiller_cooling_kW'] = [[0.,0.,0.]]
        else: bad['effective_dispatch']['grid_import_kW'] = 5.
        with pytest.raises(ValueError): module.validate_execution_limits(bad)


@pytest.mark.parametrize('module', ['engineering_planner','operating_planner'])
@pytest.mark.parametrize('schema', ['v1','v2'])
def test_held_boiler_probe_uses_effective_setting_not_fault_masked_raw_permission(problem,module,schema):
    from test_operating_readiness import dispatch
    planner = importlib.import_module('tools.execution_recovery.'+module)
    p=problem; p['loads'][:]=0.;p['loads'][0,0]=200.;p['state'].gt_prev[:]=[200.,0.,0.]
    p['availability']['GRID']=0.
    for i in range(3):p['availability'][f'BOILER_{i+1}']=0.
    raw=dispatch([200.,0.,0.],grid=350.)
    kwargs=dict(joint_options=BASE,execution_limits=v2_limits() if schema=='v2' else limits(),
                engineering_options=dict(diagnose_full_supply=True))
    if module=='engineering_planner':
        kwargs['execution_context']=dict(engineering_context(p),arrived_dispatch=raw,
            arrived_grid_import_limit_kW=350.,actual_disconnected_kW=np.zeros((4,3)).tolist())
    else:kwargs['readiness_context']=current([200.,0.,0.],arrived_dispatch=raw)
    result=planner.solve_joint(**p,**kwargs)
    held=result['joint_diagnostic']['engineering']['same_state_diagnostics']['execution_held_full_supply']
    assert held['status']=='feasible'
    assert held['planned_current_action']['boiler_steam_lbph']==0.
    assert held['held_dispatch_provenance']['represents_actual_delivery'] is False
    assert raw['boiler_steam_lbph']==300.
    if module=='operating_planner':
        witness=result['joint_diagnostic']['risk_scenarios']['held_actual']
        assert witness['boiler_steam_lbph']==0.
        assert abs(witness['steam_balance_residual_lbph'])<=1e-7


def test_v1_held_caps_fallback_masks_hardware_without_inventing_actual_ports(problem):
    from test_operating_readiness import dispatch
    module=importlib.import_module('tools.execution_recovery.numerics')
    assert hasattr(module,'effective_held_dispatch'),'effective held actuator adapter is missing'
    p=problem;p['availability']['GRID']=0.;p['availability']['ABS']=0.;p['availability']['EC_1']=0.
    for i in range(3):p['availability'][f'BOILER_{i+1}']=0.
    raw=dispatch([200.,0.,0.],grid=350.,diesel=20.)
    caps,meta=module.effective_held_dispatch(raw,execution_limits=limits(),availability=p['availability'],
        plant=p['plant'],grid_import_limit_kW=p['grid_import_limit_kW'])
    assert caps['boiler_steam_lbph']==caps['grid_import_kW']==caps['diesel_kW']==caps['absorption_cooling_kW']==0.
    assert caps['electric_chiller_cooling_kW'][0]==0.
    assert meta['represents_actual_delivery'] is False and meta['source']=='v1_hardware_masked_permission_fallback'
    assert 'gt_electric_kW' in meta['unsampled_source_fields']
    assert raw['boiler_steam_lbph']==300. and raw['electric_chiller_cooling_kW'][0]==20.
