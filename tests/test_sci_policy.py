"""Causal V21 upper arrangements; no annual data or sealed outcomes."""
import copy
import importlib
from pathlib import Path
import sys

import numpy as np
import pytest

SOURCE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SOURCE), str(SOURCE/'src'), str(SOURCE/'tests')]
from test_engineering_planner import problem
from test_state_coordinated_planner import context


def api(name='policy'):
    try:
        return importlib.import_module('tools.sci_engineering.'+name)
    except ModuleNotFoundError as exc:
        pytest.fail('SCI upper arrangement is missing: '+str(exc))


def observed(time=0., outage=0., energy=2400., **changes):
    features = dict(time_minute=time, grid_outage_elapsed_minutes=outage,
        outdoor_temperature_C=20., grid_import_limit_kW=850., diesel_energy_kWh=energy,
        grid_import_prev_kW=0., diesel_prev_kW=0.)
    for group, values in zip(('P0','P1','P2','P3'), ((120,0,100),(110,0,80),(50,0,40),(417,0,368))):
        for carrier, value in zip(('e','h','c'), values):
            features[f'{group}_{carrier}_demand_kW'] = float(value)
            features[f'{group}_{carrier}_disconnected_kW'] = 0.
    for device in ('GRID','DIESEL','ABS','TOWER', *(f'{kind}_{i}' for kind in ('GT','HRSG','EC','BOILER') for i in (1,2,3))):
        features['availability_'+device] = 1.
    for i, power in enumerate((125.,0.,156.), 1):
        features[f'pre_GT_{i}_kW'] = power
    features.update(changes)
    return dict(decision_time_min=time, features=features,
        feature_metadata={key:dict(available_time_min=time,valid_time_min=time) for key in features})


def test_c_is_strong_c19_config_and_identity_does_not_change_action():
    p = api()
    one = p.SciPolicy('C', policy_id='C_identity_one').decide([observed()])[0]
    two = p.SciPolicy('C', policy_id='C_identity_two').decide([observed()])[0]
    assert one == two
    c = one['state_coordinated_joint']['coordination']
    assert c['reserve_enabled'] and c['critical_preparation_first']
    assert c['reserve_rule'] == 'critical_current'
    assert c['cooling_preparation'] and c['time_energy_preparation'] and c['execution_recovery']
    assert c['response_minutes'] == 30. and c['support_minutes'] == 60.


def test_no_policy_uses_future_scenario_or_outcomes():
    p = api()
    with pytest.raises(ValueError):
        p.SciPolicy('A', {'actual_repair_minute': 500.})
    row = observed()
    row['events'] = [{'end_minute': 500.}]
    with pytest.raises(ValueError):
        p.SciPolicy('A').decide([row])
    with pytest.raises(ValueError):
        p.validate_action({'state_coordinated_joint': {'future_faults': []}})


def test_R_emits_direct_dispatch_with_no_joint_request_and_original_denominator(problem):
    p = api()
    action, diagnostic = p.SciPolicy('R', plant=problem['plant']).decide([observed()])
    assert set(action) == {'ordinary_dispatch'}
    ordinary = action['ordinary_dispatch']
    assert np.asarray(ordinary['disconnected_kW']).shape == (4,3)
    np.testing.assert_array_equal(ordinary['disconnected_kW'], np.zeros((4,3)))
    assert set(ordinary['dispatch']) == set(p.DISPATCH_FIELDS)
    assert diagnostic['upper_optimizer_called'] is False
    assert ordinary['dispatch']['grid_import_kW'] == 850.


def test_current_only_M_has_no_extra_reserve_or_preparation(problem):
    planner = api('planner')
    result = planner.solve_joint(**problem, readiness_context=context(problem),
        coordination_options=api().policy_config('M'))
    d = result['joint_diagnostic']
    assert d['sci']['policy_mode'] == 'current'
    assert d['sci']['reserve_enabled'] is False
    assert not any('preparation' in name or 'reserve' in name for name in d['objective_order'])
    assert max(map(abs, result['energy_balance_residuals'])) <= 1e-4


@pytest.mark.parametrize('reserve,prepare', [(False,False),(True,False),(False,True),(True,True)])
def test_mechanism_switches_change_independent_solver_order(problem, reserve, prepare):
    p = copy.deepcopy(problem)
    p['availability']['GRID'] = 0.
    p['state'].diesel_status = 'CONNECTED'
    p['diesel_start_available'] = True
    cfg = api().policy_config('C', {'reserve_enabled':reserve,'critical_preparation_first':prepare})
    readiness = context(p, diesel_connected=True, diesel_arrived_cap_kW=900.)
    result = api('planner').solve_joint(**p, readiness_context=readiness, coordination_options=cfg)
    order = result['joint_diagnostic']['objective_order']
    service = order.index('arrived_current_P2')
    preparation = order.index('preparation_P0')
    assert (preparation < service) is prepare
    assert ('current_critical_energy_reserve' in order) is reserve
    if reserve:
        assert order.index('current_critical_energy_reserve') < service
        assert (preparation < order.index('current_critical_energy_reserve')) is prepare


def test_fixed_hours_and_adaptive_release_are_transparent_and_finite():
    p = api()
    f = p.reserve_reference(np.array([[100.,0.,0.],[100.,0.,0.]]), remaining_kWh=1200.,
        ec_kWe_per_kWc=.2, shaft_loss_bound_kW=20., minimum_hours=1.,
        options=p.policy_config('F', {'fixed_reserve_hours':2.}))
    assert f['reserve_hours'] == 2.
    early = p.reserve_reference(np.array([[100.,0.,0.],[100.,0.,0.]]), remaining_kWh=1200.,
        ec_kWe_per_kWc=.2, shaft_loss_bound_kW=20., minimum_hours=1.,
        options=p.policy_config('A', {'observed_outage_minutes':30.,'critical_non_diesel_ready':True}))
    late = p.reserve_reference(np.array([[100.,0.,0.],[100.,0.,0.]]), remaining_kWh=1200.,
        ec_kWe_per_kWc=.2, shaft_loss_bound_kW=20., minimum_hours=1.,
        options=p.policy_config('A', {'observed_outage_minutes':300.,'critical_non_diesel_ready':True}))
    assert late['reserve_hours'] < early['reserve_hours']
    assert late['reserve_hours'] >= .5
    no_path = p.reserve_reference(np.array([[100.,0.,0.],[100.,0.,0.]]), remaining_kWh=1200.,
        ec_kWe_per_kWc=.2, shaft_loss_bound_kW=20., minimum_hours=1.,
        options=p.policy_config('A', {'observed_outage_minutes':300.,'critical_non_diesel_ready':False}))
    assert no_path['reserve_hours'] == early['reserve_hours']


@pytest.mark.parametrize('grid,energy', [(0.,2400.),(1.,2400.),(0.,0.)])
def test_C_migration_retains_C19_mechanisms_with_corrected_current_priority(problem, grid, energy):
    from tools.model_closeout import planner as original
    p = copy.deepcopy(problem)
    p['availability']['GRID'] = grid
    p['state'].diesel_energy_remaining_kWh = energy
    old = original.solve_joint(**p, readiness_context=context(p), coordination_options={'critical_reserve':True})
    new = api('planner').solve_joint(**p, readiness_context=context(p), coordination_options=api().policy_config('C'))
    # Exact old actions/order would preserve the proved target-before-ready
    # defect. Keep the old implementation as a reference, but require the
    # same strong mechanism goals and no loss of current critical priority.
    before=old['joint_diagnostic'];after=new['joint_diagnostic']
    assert set(before['objective_order'])==set(after['objective_order'])
    assert set(before['risk_scenarios'])==set(after['risk_scenarios'])
    order=after['objective_order']
    assert order.index('arrived_current_P1')<order.index('current_P0')
    options=after['coordination']['options']
    assert all(options[key] for key in ('legacy_C19_order','reserve_enabled',
        'critical_preparation_first','cooling_preparation','time_energy_preparation','execution_recovery'))
    scale=np.maximum(1.,p['loads'].sum(axis=0))
    a=np.asarray(before['risk_scenarios']['arrived_current']['unserved_kW'])/scale
    b=np.asarray(after['risk_scenarios']['arrived_current']['unserved_kW'])/scale
    assert b[0].sum()<=a[0].sum()+1e-7
    if abs(b[0].sum()-a[0].sum())<=1e-7:
        assert b[1].sum()<=a[1].sum()+1e-7


def test_R_refuses_missing_immutable_plant_and_bad_direct_dispatch():
    p = api()
    with pytest.raises(ValueError, match='plant'):
        p.SciPolicy('R')
    with pytest.raises(ValueError):
        p.validate_action({'ordinary_dispatch': {'dispatch':{}, 'disconnected_kW':np.zeros((4,3)).tolist()}})


def test_auxiliary_power_is_required_in_current_and_each_preparation_copy(problem):
    p = copy.deepcopy(problem)
    p['row'].P0_fan_kW = 10.
    p['row'].P0_chw_pump_kW = 5.
    p['config']['sci_service_dependencies'] = True
    p['availability']['AUX_FAN_P0'] = 0.
    result = api('planner').solve_joint(**p, readiness_context=context(p),
        coordination_options=api().policy_config('C'))
    assert result['lost'][0,2] == pytest.approx(p['loads'][0,2],abs=1e-7)
    for name, case in result['joint_diagnostic']['risk_scenarios'].items():
        assert case['unserved_kW'][0][2] == pytest.approx(p['loads'][0,2],abs=1e-7), name
    p['availability']['AUX_FAN_P0'] = 1.
    restored = api('planner').solve_joint(**p, readiness_context=context(p),
        coordination_options=api().policy_config('C'))
    assert restored['lost'][0,2] < 1e-5
    assert max(map(abs, restored['energy_balance_residuals'])) <= 1e-4


def test_A_does_not_release_reserve_through_failed_auxiliary_or_failed_source(problem):
    p = api()
    healthy = observed(time=300.,outage=300.)
    expected = p.SciPolicy('A',plant=problem['plant']).decide([healthy])[0]
    assert expected['state_coordinated_joint']['coordination']['critical_non_diesel_ready'] is True
    failed = observed(time=300.,outage=300.)
    failed['sci_auxiliary'] = dict(observation_time_minute=300.,available_time_minute=300.,
        availability={'AUX_FAN_P0':0.},required_kW=np.zeros((4,3)).tolist())
    request = p.SciPolicy('A',plant=problem['plant']).decide([failed])[0]
    assert request['state_coordinated_joint']['coordination']['critical_non_diesel_ready'] is False
    upstream = observed(time=300.,outage=300.,availability_GT_1=0.,availability_GT_3=0.)
    request = p.SciPolicy('A',plant=problem['plant']).decide([upstream])[0]
    assert request['state_coordinated_joint']['coordination']['critical_non_diesel_ready'] is False


def test_new_auxiliary_observation_rejects_future_clock(problem):
    row = observed(time=300.,outage=300.)
    row['sci_auxiliary'] = dict(observation_time_minute=300.,available_time_minute=301.,
        availability={'AUX_FAN_P0':0.},required_kW=np.zeros((4,3)).tolist())
    with pytest.raises(ValueError,match='future|clock'):
        api().SciPolicy('A',plant=problem['plant']).decide([row])


def test_R_does_not_request_cold_from_a_failed_shared_tower(problem):
    request, diagnostic = api().SciPolicy('R',plant=problem['plant']).decide(
        [observed(availability_TOWER=0.)])
    dispatch = request['ordinary_dispatch']['dispatch']
    assert dispatch['absorption_cooling_kW'] == 0.
    assert sum(dispatch['electric_chiller_cooling_kW']) == 0.
    assert diagnostic['rule_diagnostic']['current_cooling_kW'] == 588.


def test_factory_uses_explicit_alias_and_supports_registered_candidates(problem):
    p = api()
    spec = p.policy_specs()['F']
    spec['policy_id'] = 'fixed_two_hours'
    spec['config']['fixed_reserve_hours'] = 2.
    policy = p.factory(spec,plant=problem['plant'])
    assert policy.policy_id == 'fixed_two_hours'
    assert policy.config['fixed_reserve_hours'] == 2.


def test_R_keeps_boiler_support_until_requested_GT_heat_is_actually_running(problem):
    row = observed(pre_GT_1_kW=0.,pre_GT_2_kW=0.,pre_GT_3_kW=0.,P0_h_demand_kW=100.)
    request, _ = api().SciPolicy('R',plant=problem['plant']).decide([row])
    dispatch = request['ordinary_dispatch']['dispatch']
    assert sum(dispatch['gt_electric_kW']) > 0.
    heat_required = 100./(problem['plant'].steam_kWh_per_lb*problem['plant'].exchanger_eff)
    assert dispatch['boiler_steam_lbph'] >= heat_required-1e-7


def test_legacy_C19_order_cannot_silently_bind_a_mechanism_override():
    p = api()
    config = p.policy_config('C')
    config['reserve_enabled'] = False
    with pytest.raises(ValueError,match='legacy|mechanism'):
        p.SciPolicy('C',config)
    specs = p.mechanism_specs()
    assert len(specs) == 4
    combinations = set()
    for spec in specs.values():
        config = p.factory(spec).config
        assert config['legacy_C19_order'] is False
        combinations.add((config['reserve_enabled'],config['critical_preparation_first']))
    assert combinations == {(False,False),(True,False),(False,True),(True,True)}


def test_candidate_registry_is_only_small_development_parameter_grid():
    candidates = list(api().candidate_specs().values())
    assert len(candidates) == 5
    assert {s['config']['fixed_reserve_hours'] for s in candidates if s['display_alias']=='F'} == {.5,1.,2.}
    assert {s['config']['release_after_minutes'] for s in candidates if s['display_alias']=='A'} == {120.,240.}
    assert all(s['selection_role']=='development_only' for s in candidates)
