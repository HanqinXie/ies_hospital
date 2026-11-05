"""Current-state joint planning regressions; synthetic loads, no annual data."""
import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from hospital_ies.execution import execution_contract
from hospital_ies.model import load_plant
from hospital_ies.resilience import RuntimeState, _solve_milp, empty_faults


def planner():
    try:
        return importlib.import_module('tools.autonomous_dispatch.planner').solve_joint
    except ModuleNotFoundError:
        pytest.fail('The independent joint current-state planner is not implemented')


@pytest.fixture
def problem():
    return dict(row=SimpleNamespace(timestamp=pd.Timestamp('2025-01-15 10:00'), outdoor_temperature_C=20.),
        plant=load_plant(ROOT / 'configs/plant_configs.json', 'doe_capacity_matched_exploratory'),
        loads=np.array([[80., 30., 40.], [60., 20., 30.], [100., 40., 60.], [120., 50., 70.]]),
        availability=empty_faults(),
        state=RuntimeState(np.array([150., 150., 150.]), 2400., set(), np.zeros(3), {}),
        dt_h=.25, grid_import_limit_kW=850., diesel_start_available=True,
        gt_ramp_kW_per_h=900., solver_time_limit_sec=6.,
        config=json.loads((ROOT / 'configs/resilience_assumptions.json').read_text(encoding='utf-8')),
        execution_contract=execution_contract({'grid_import_mode': 'arrived_ceiling'}))


def diesel_only(problem, critical=(20., 20.), low=(200., 200.), energy=240.):
    p=copy.deepcopy(problem)
    p['loads']=np.zeros((4, 3));p['loads'][:, 0]=[*critical, *low]
    p['availability'].update({f'GT_{i}': 0. for i in (1, 2, 3)})
    p['grid_import_limit_kW']=0.
    p['state'].gt_prev[:]=0.;p['state'].diesel_energy_remaining_kWh=energy
    return p


@pytest.mark.parametrize('condition', ['normal', 'fuel_shortfall', 'cold_capacity_loss'])
def test_economic_preserves_original_objectives_and_main_balance(problem, condition):
    if condition=='fuel_shortfall':
        problem=diesel_only(problem, energy=15.)
    elif condition=='cold_capacity_loss':
        problem=copy.deepcopy(problem);problem['availability']['TOWER']=0.
    new=planner()(**problem, joint_options={'variant': 'economic'})
    old=_solve_milp(**problem)
    np.testing.assert_allclose(new['lex_values'], old['lex_values'], atol=1e-7, rtol=0.)
    assert new['cost_USD_step']==pytest.approx(old['cost_USD_step'], abs=1e-5, rel=1e-6)
    assert max(map(abs, new['energy_balance_residuals']))<1e-4
    assert new['action']['grid_import_kW']==pytest.approx(new['planned_current_action']['grid_import_kW'])
    assert all(x['backend']=='gurobipy' for x in new['solver_backend_layers'])


def test_endurance_can_reduce_low_service_and_total_diesel(problem):
    p=diesel_only(problem)
    base=planner()(**p, joint_options={'variant': 'economic'})
    new=planner()(**p, joint_options={'variant': 'endurance', 'support_hours': 2.})
    assert base['action']['diesel_kW']>400.
    assert new['action']['diesel_kW']==pytest.approx(120., abs=1e-3)
    np.testing.assert_allclose(new['lost'][:2], base['lost'][:2], atol=1e-3, rtol=0.)
    assert new['lost'][2:, 0].sum()>300.
    assert new['joint_diagnostic']['fuel_excess_kW']<1e-4


def test_critical_diesel_uses_preference_slack_instead_of_p0_p1_loss(problem):
    p=diesel_only(problem, low=(0., 0.), energy=20.)
    new=planner()(**p, joint_options={'variant': 'endurance', 'support_hours': 2.})
    assert new['lost'][:2].sum()<1e-3
    assert new['action']['diesel_kW']==pytest.approx(40., abs=1e-3)
    assert new['joint_diagnostic']['fuel_excess_kW']==pytest.approx(30., abs=1e-3)


def test_common_gt_loss_removes_electricity_and_hrsg_together(problem):
    new=planner()(**problem, joint_options={'variant': 'diverse'})
    common=new['joint_diagnostic']['risk_scenarios']['GT_common_loss']
    assert common['gt_electric_kW']==pytest.approx([0., 0., 0.])
    assert common['hrsg_steam_lbph']==pytest.approx(0.)
    assert max(map(abs, common['balance_residuals']))<1e-4
    assert common['single_shared_command'] is True


def test_risk_surplus_is_diagnostic_and_never_main_supply_sink(problem):
    p=diesel_only(problem, critical=(100., 0.), low=(0., 0.))
    low=np.zeros((4, 3));low[0, 0]=10.
    new=planner()(**p, joint_options={'variant': 'coupled'}, risk_loads=low)
    risk=new['joint_diagnostic']['risk_scenarios']['low_history']
    assert risk['electric_surplus_defect_kW']>89.
    assert new['lost'][0, 0]<1e-3
    assert max(map(abs, new['energy_balance_residuals']))<1e-4
    assert 'electric_surplus_defect_kW' not in new['action']


def test_fixed_grid_contract_cannot_gain_automatic_import_ceiling(problem):
    p=copy.deepcopy(problem);p['execution_contract']=execution_contract()
    new=planner()(**p, joint_options={'variant': 'diverse'})
    assert new['action']['grid_import_kW']==pytest.approx(new['planned_current_action']['grid_import_kW'])
    assert new['joint_diagnostic']['risk_scenarios']['GT_common_loss']['grid_import_kW']==pytest.approx(new['action']['grid_import_kW'])


def prepared_grid_problem(problem):
    p=copy.deepcopy(problem);p['loads']=np.zeros((4, 3));p['loads'][0, 0]=200.
    p['diesel_start_available']=False
    return p


def test_one_command_can_prepare_import_ceiling_without_unused_supply(problem):
    p=prepared_grid_problem(problem)
    new=planner()(**p, joint_options={'variant': 'diverse'})
    assert new['action']['grid_import_kW']>=199.999
    assert new['planned_current_action']['grid_import_kW']<1e-3
    assert sum(new['planned_current_action']['gt_electric_kW'])==pytest.approx(200., abs=1e-3)
    assert max(map(abs, new['energy_balance_residuals']))<1e-4
    assert new['joint_diagnostic']['risk_scenarios']['GRID_loss']['unserved_kW'][0][0]<1e-3
    assert new['joint_diagnostic']['risk_scenarios']['GT_common_loss']['unserved_kW'][0][0]<1e-3


def test_grid_tracking_tracks_generated_ceiling_in_existing_ceiling_contract(problem):
    p=prepared_grid_problem(problem)
    new=planner()(**p, tracking_targets={'grid_import': 850.}, joint_options={'variant': 'diverse'})
    assert new['action']['grid_import_kW']==pytest.approx(850., abs=1e-3)
    assert new['planned_current_action']['grid_import_kW']<1e-3
    assert new['tracking_penalty_USD_step']<1e-4


def test_shared_emergency_bus_overproduction_cannot_remove_current_p0_feasibility(problem):
    p=diesel_only(problem, critical=(100., 0.), low=(0., 0.))
    p['availability']['GRID']=0.
    p['execution_contract']=execution_contract({'diesel_mode': 'emergency_bus'})
    low=np.zeros((4, 3));low[0, 0]=10.
    new=planner()(**p, joint_options={'variant': 'coupled'}, risk_loads=low)
    assert new['lost'][0, 0]<1e-3
    assert new['joint_diagnostic']['risk_scenarios']['low_history']['electric_surplus_defect_kW']>89.
    assert max(map(abs, new['energy_balance_residuals']))<1e-4


@pytest.mark.parametrize('options', [
    {'variant': 'MPC'}, {'future_outage_end': 180.}, {'horizon_steps': 4},
    {'variant': 'economic', 'support_hours': True}, {'risk_tolerance': float('nan')},
    {'support_hours': 0.}, {'risk_tolerance': -1.},
])
def test_unknown_future_and_invalid_options_are_rejected(problem, options):
    with pytest.raises(ValueError):
        planner()(**problem, joint_options=options)


def test_low_history_must_be_finite_nonnegative_current_history_floor(problem):
    solve=planner()
    for risk in (np.zeros(3), np.full((4, 3), np.nan), -np.ones((4, 3)), problem['loads']+1.):
        with pytest.raises(ValueError):
            solve(**problem, joint_options={'variant': 'coupled'}, risk_loads=risk)


def test_planner_does_not_mutate_current_state_or_history_floor(problem):
    p=copy.deepcopy(problem);low=p['loads']*.5;before=copy.deepcopy(p);floor_before=low.copy()
    planner()(**p, joint_options={'variant': 'coupled'}, risk_loads=low)
    np.testing.assert_array_equal(p['loads'], before['loads'])
    np.testing.assert_array_equal(p['state'].gt_prev, before['state'].gt_prev)
    np.testing.assert_array_equal(p['state'].overload_minutes, before['state'].overload_minutes)
    np.testing.assert_array_equal(p['state'].disconnected_loads, before['state'].disconnected_loads)
    np.testing.assert_array_equal(low, floor_before)
    assert p['availability']==before['availability']
    assert p['config']==before['config']
    assert p['execution_contract']==before['execution_contract']
    assert p['state'].diesel_energy_remaining_kWh==before['state'].diesel_energy_remaining_kWh

