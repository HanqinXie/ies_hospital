"""Executable current service precedes conditional target and endpoint goals."""
import copy
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from hospital_ies.model import gt_level_properties
from tools.operating_response.physical_balance import solve_physical
from tools.sci_engineering import planner,policy
from test_engineering_planner import problem
from test_state_coordinated_planner import context


def ready_record(result):
    d=result['joint_diagnostic']
    return d.get('current_service_phases',d.get('risk_scenarios',{}))['arrived_current']


@pytest.mark.parametrize('alias',['M','F','C','A'])
@pytest.mark.parametrize('source_case',['adequate_current_P0','near_feasible_current_P0'])
def test_current_P0_is_not_sacrificed_for_unready_target_P0_or_P1(problem,alias,source_case):
    p=copy.deepcopy(problem)
    p['loads'][:]=0.;p['loads'][0,0]=150.;p['loads'][1,0]=100.
    rated=float(gt_level_properties(p['plant'],p['row'].outdoor_temperature_C)[0][-1])
    previous=160. if source_case=='adequate_current_P0' else 149.7
    availability=.6 if source_case=='adequate_current_P0' else 149.7/rated
    p['state'].gt_prev[:]=[previous,0.,0.]
    p['availability'].update(GRID=0.,GT_1=availability,GT_2=1.,GT_3=0.)
    result=planner.solve_joint(**p,readiness_context=context(p),coordination_options=policy.policy_config(alias))
    ready=np.asarray(ready_record(result)['unserved_kW'])
    # A real, same-hardware current-port witness, not an optimizer score.
    delivered=min(150.,rated*availability)
    ordinary=dict(gt_electric_kW=[delivered,0.,0.],grid_import_kW=0.,diesel_kW=0.,
        boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.])
    physical=solve_physical(**p,execution_action=ordinary)
    assert ready[0,0]<=physical['lost'][0,0]+1e-4
    assert max(map(abs,physical['energy_balance_residuals']))<=1e-4
    # The conditional goal must not require full present P1 by a new GT.
    assert result['action']['gt_electric_kW'][0]>=delivered-1e-4


@pytest.mark.parametrize('alias',['M','F','C','A'])
def test_every_executable_critical_stage_precedes_conditional_targets(problem,alias):
    result=planner.solve_joint(**problem,readiness_context=context(problem),
        coordination_options=policy.policy_config(alias))
    order=result['joint_diagnostic']['objective_order']
    assert order.index('arrived_current_P0')<order.index('arrived_current_P1')
    assert order.index('arrived_current_P1')<order.index('current_P0')
    assert order.index('arrived_current_P1')<order.index('current_P1')
    if 'regulated_current_P0' in order:
        assert order.index('arrived_current_P1')<order.index('regulated_current_P0')
    assert order.index('arrived_current_P2')<order.index('arrived_current_P3')
    assert order.index('arrived_current_P2')<order.index('current_P3')
    if alias=='M':
        assert order.index('arrived_current_P3')<order.index('current_P0')


@pytest.mark.parametrize('reserve,prepare',[(False,False),(True,False),(False,True),(True,True)])
def test_mechanism_order_is_independent_after_current_critical_locks(problem,reserve,prepare):
    p=copy.deepcopy(problem);p['availability']['GRID']=0.;p['diesel_start_available']=True
    readiness=context(p,diesel_connected=True,diesel_arrived_cap_kW=900.)
    config=policy.policy_config('C',dict(reserve_enabled=reserve,
        critical_preparation_first=prepare,legacy_C19_order=False))
    result=planner.solve_joint(**p,readiness_context=readiness,coordination_options=config)
    order=result['joint_diagnostic']['objective_order']
    current=order.index('arrived_current_P1');general=order.index('arrived_current_P2')
    assert current<general
    assert current<order.index('preparation_P0')
    assert (order.index('preparation_P0')<general) is prepare
    assert ('current_critical_energy_reserve' in order) is reserve
    if reserve:
        assert current<order.index('current_critical_energy_reserve')<general
