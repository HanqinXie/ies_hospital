"""V18 tests operate on a retained legal observation, never an episode."""
import importlib.util
import copy
import json
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('v18_replay',ROOT/'outputs/v18_execution_recovery_20261018_run01/review/c18_constraint_localization/replay.py')
replay=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay)


def test_day100_critical_service_and_ordered_endurance():
    from tools.execution_recovery import planner
    result,_=replay.replay(planner,execution_recovery=True)
    lost=np.asarray(result['lost'])
    assert lost[:2].max()<=.001
    assert lost[2,0]<=.001
    assert result['action']['diesel_kW']<400.
    assert result['joint_diagnostic']['engineering']['endurance_preparation_active']
    for case in result['joint_diagnostic']['coordination']['response_scenarios'].values():
        if case.get('effective_preparation_credit'):
            assert case['electric_surplus_defect_kW']<=1e-7


def test_c4_compatibility_keeps_original_preferences():
    from tools.execution_recovery import planner
    result,_=replay.replay(planner,execution_recovery=False)
    original=replay.fixture()['original_command']['dispatch']
    assert np.allclose(result['action']['gt_electric_kW'],original['gt_electric_kW'],rtol=0,atol=1e-7)
    assert abs(result['action']['diesel_kW']-original['diesel_kW'])<=1e-7
    assert abs(result['command_disconnected'][2,0]-86.21188810590604)<=1e-6


def test_heat_preparation_is_real_paid_boiler_output():
    from tools.execution_recovery import planner
    f=json.loads((replay.HERE/'day8_75_fixture.json').read_text(encoding='utf-8'))
    kwargs=replay.inputs(f);kwargs['coordination_options']['execution_recovery']=True
    result=planner.solve_joint(**kwargs)
    heat=float(np.asarray(f['loads_kW'])[:2,1].sum())
    actual_heat=result['action']['boiler_steam_lbph']*f['plant']['steam_kWh_per_lb']*f['plant']['exchanger_eff']
    assert actual_heat>=1.5*heat-.001
    assert result['cost_USD_step']>0.
    assert result['joint_diagnostic']['coordination']['boiler_command_is_actual_fixed_steam']


def test_endurance_starts_with_island_even_with_long_observed_support():
    from tools.execution_recovery import planner
    f=copy.deepcopy(replay.fixture());f['state']['diesel_energy_remaining_kWh']=2400.
    kwargs=replay.inputs(f);kwargs['coordination_options']['execution_recovery']=True
    result=planner.solve_joint(**kwargs)
    engineering=result['joint_diagnostic']['engineering']
    assert engineering['actual_diesel_fuel_support_hours']>1.
    assert engineering['endurance_preparation_active']


def test_original_protection_reference_limits_a_cold_weather_target():
    from tools.execution_recovery import planner
    f=copy.deepcopy(replay.fixture());f['row']['outdoor_temperature_C']=0.
    f['state']['overload_minutes']=[0.,0.,10.]
    kwargs=replay.inputs(f);kwargs['coordination_options']['execution_recovery']=True
    result=planner.solve_joint(**kwargs)
    assert max(result['action']['gt_electric_kW'])<=320.*1.05+1e-7
    assert np.asarray(result['lost'])[:2].max()<.001
    assert result['joint_diagnostic']['engineering']['protection_reference_kW']==320.


@pytest.mark.parametrize('near_trip',[0,1])
def test_unavoidable_gt_excess_avoids_the_observed_near_hold_machine(near_trip):
    from tools.execution_recovery import planner
    f=copy.deepcopy(replay.fixture())
    f['row']['outdoor_temperature_C']=0.
    f['loads_kW']=np.zeros((4,3)).tolist();f['loads_kW'][0][0]=680.
    f['request']['risk_loads_kW']=np.zeros((4,3)).tolist()
    f['availability'].update(GT_1=1.,GT_2=1.,GT_3=0.,GRID=0.)
    f['state'].update(gt_prev=[340.,340.,0.],diesel_energy_remaining_kWh=0.,
        diesel_status='EMPTY',last_diesel_kW=0.,disconnected_loads=np.zeros((4,3)).tolist())
    # Exercise the explicit observed M18 ledger, with opposite hidden state
    # clocks: the selected preference must use the supplied legal observation.
    f['state']['overload_minutes']=[0.,0.,0.]
    c=f['readiness_context']
    c.update(actual_gt_kW=[340.,340.,0.],gt_ramp_anchor_kW=[340.,340.,0.],
        gt_physical_status=['RUNNING','RUNNING','EXOGENOUS_UNAVAILABLE'],gt_can_stop=[False,False,True],
        actual_disconnected_kW=np.zeros((4,3)).tolist(),observed_critical_unserved_kW=np.zeros((2,3)).tolist(),
        diesel_connected=False,actual_diesel_kW=0.,diesel_arrived_cap_kW=0.)
    c['arrived_dispatch'].update(gt_electric_kW=[340.,340.,0.],grid_import_kW=0.,diesel_kW=0.,boiler_steam_lbph=0.)
    c['diesel_local_response'].update(actual_electric_kW=0.,arrived_ceiling_kW=0.,permission_status='EMPTY')
    c['diesel_local_response']['rotor'].update(rotor_energy_kWs=0.,speed_pu=0.,mechanical_kW=0.,fuel_exhausted=True)
    clocks=[0.,0.,0.];clocks[near_trip]=14.
    kwargs=replay.inputs(f);kwargs['coordination_options']['execution_recovery']=True
    kwargs['execution_limits']=dict(schema='hospital_ies.execution_limits.v18.v1',
        hardware_grid_available_fraction=0.,grid_bus_state='ISLAND',effective_grid_import_limit_kW=0.,
        effective_diesel_limit_kW=0.,diesel_conditionally_permitted=False,grid_transfer_remaining_minutes=None,
        gt_protection_reference_kW=320.,gt_trip_multiple=1.05,gt_hold_minutes=15.,gt_overload_minutes=clocks)
    result=planner.solve_joint(**kwargs)
    ports=result['action']['gt_electric_kW']
    assert result['lost'][0,0]<.001
    assert sum(ports)>=680.-.001
    assert ports[near_trip]<=336.+1e-7
    assert ports[1-near_trip]>=344.-.001
    assert result['joint_diagnostic']['engineering']['protection_excess_weights'][near_trip]==pytest.approx(1.+14./15.)


def held_boiler_fault_inputs(*, recovery, version):
    """One lawful current snapshot; raw arrival survives a boiler outage."""
    f=copy.deepcopy(replay.fixture())
    f['loads_kW']=np.zeros((4,3)).tolist();f['loads_kW'][0][0]=200.
    f['request']['risk_loads_kW']=np.zeros((4,3)).tolist()
    f['availability'].update(GT_1=1.,GT_2=0.,GT_3=0.,GRID=0.,
        BOILER_1=0.,BOILER_2=0.,BOILER_3=0.)
    f['state'].update(gt_prev=[200.,0.,0.],diesel_energy_remaining_kWh=0.,
        diesel_status='EMPTY',last_diesel_kW=0.,disconnected_loads=np.zeros((4,3)).tolist())
    c=f['readiness_context']
    c.update(actual_gt_kW=[200.,0.,0.],gt_ramp_anchor_kW=[200.,0.,0.],
        gt_physical_status=['RUNNING','EXOGENOUS_UNAVAILABLE','EXOGENOUS_UNAVAILABLE'],
        gt_can_stop=[False,True,True],actual_disconnected_kW=np.zeros((4,3)).tolist(),
        observed_critical_unserved_kW=np.zeros((2,3)).tolist(),
        diesel_connected=False,actual_diesel_kW=0.,diesel_arrived_cap_kW=0.)
    c['arrived_dispatch'].update(gt_electric_kW=[200.,0.,0.],grid_import_kW=0.,diesel_kW=0.,boiler_steam_lbph=300.)
    c['diesel_local_response'].update(actual_electric_kW=0.,arrived_ceiling_kW=0.,permission_status='EMPTY')
    c['diesel_local_response']['rotor'].update(rotor_energy_kWs=0.,speed_pu=0.,mechanical_kW=0.,fuel_exhausted=True)
    kwargs=replay.inputs(f);kwargs['coordination_options']['execution_recovery']=recovery
    kwargs['engineering_options']={'diagnose_full_supply':True}
    limits=dict(schema='hospital_ies.execution_limits.v18.'+version,
        hardware_grid_available_fraction=0.,grid_bus_state='ISLAND',effective_grid_import_limit_kW=0.,
        effective_diesel_limit_kW=0.,diesel_conditionally_permitted=False,grid_transfer_remaining_minutes=None,
        gt_protection_reference_kW=320.,gt_trip_multiple=1.05,gt_hold_minutes=15.,gt_overload_minutes=[0.,0.,0.])
    if version=='v2':
        limits['effective_dispatch']=copy.deepcopy(c['arrived_dispatch'])
        limits['effective_dispatch']['boiler_steam_lbph']=0.
    kwargs['execution_limits']=limits
    return kwargs


@pytest.mark.parametrize('recovery',[True,False],ids=['C18','C4compat'])
@pytest.mark.parametrize('version',['v1','v2'])
def test_held_boiler_outage_uses_effective_actuator_without_erasing_permission(recovery,version):
    from tools.execution_recovery import planner
    kwargs=held_boiler_fault_inputs(recovery=recovery,version=version)
    raw=copy.deepcopy(kwargs['readiness_context']['arrived_dispatch'])
    result=planner.solve_joint(**kwargs)
    diagnostic=result['joint_diagnostic']
    held=diagnostic['risk_scenarios']['held_actual']
    assert held['boiler_steam_lbph']==0.
    probe=diagnostic['engineering']['same_state_diagnostics']['execution_held_full_supply']
    assert probe['status']=='feasible'
    assert probe['action']['boiler_steam_lbph']==0.
    assert result['action']['boiler_steam_lbph']==0.
    assert kwargs['readiness_context']['arrived_dispatch']==raw
    assert raw['boiler_steam_lbph']==300.
