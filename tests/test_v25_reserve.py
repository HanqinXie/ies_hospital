"""Same-snapshot sustained reserves: common balance, not output labels."""
import copy
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from test_sci_environment import scenario


def reserve_case(loads,*,rule='sustained_critical',ups_energy=8.,cold_energy=30.,grid=20.,cold_capacity=60.,ups_capacity=40.):
    from tools.model_correction_v25.environment import OperatingHospitalIESEnv
    authority=json.loads((ROOT/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json').read_text(encoding='utf-8'))
    sealed=[int(x['time_block_id'].rsplit('_',1)[1]) for x in authority['p2b_time_blocks'] if x['reserved_only']]
    cfg=dict(sci_engineering=dict(ups=dict(capacity_kWh=ups_capacity,power_kW=160.,initial_soc=0.),
        cold_store=dict(capacity_kWh=cold_capacity,power_kW=120.,initial_soc=0.)))
    arguments=dict(allowed_hours=[4001,4002],sealed_days=sealed,model_config=cfg)
    try:env=OperatingHospitalIESEnv(ROOT,operating_rule=dict(name=rule,design_minutes=30.),**arguments)
    except TypeError as exc:
        if 'operating_rule' not in str(exc):raise
        env=OperatingHospitalIESEnv(ROOT,**arguments)  # RED executes old behavior.
    spec=scenario();spec.update(grid_limit_kW=grid,fault_start_minute=0.,fault_duration_minutes=45.,
        faults=dict(GT_1=0.,GT_2=0.,GT_3=0.,DIESEL=0.,ABS=0.,BOILER_1=0.,BOILER_2=0.,BOILER_3=0.))
    env.reset(scenario=spec,seed=25);core=env.core;original=core._hardware_data_at
    loads=np.asarray(loads,float)
    def current(minute):
        h,row,_,f=original(minute);row=copy.copy(row)
        for group in range(4):
            for carrier,index in [('e',0),('h',1),('c',2)]:setattr(row,f'P{group}_reference_{carrier}_kW',loads[group,index])
            for kind in ('fan','chw_pump','heat_pump'):setattr(row,f'P{group}_{kind}_kW',0.)
        return h,row,loads.copy(),f
    core._hardware_data_at=current;core.ups.energy_kWh=ups_energy;core.cold_store.energy_kWh=cold_energy
    permission=dict(core.executed,gt_electric_kW=[0.,0.,0.],grid_import_kW=grid,diesel_kW=0.,
        boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[200.,0.,0.])
    core.executed=copy.deepcopy(permission);core.arrived_dispatch=copy.deepcopy(permission);core.pending_commands=[]
    return env,permission


def solve_case(env,permission,minutes=15.):
    core=env.core;_,_,loads,f=core.data_at(0.)
    return core._solve(loads,f,minutes/60.,'v25_reserve_counterexample',{},execution_action=permission)


def test_ups_cannot_release_other_source_to_general_below_sustained_target():
    loads=np.zeros((4,3));loads[0,0]=20.;loads[2,0]=50.
    old,p=reserve_case(loads,rule='current_service_bridge',cold_capacity=0.,cold_energy=0.)
    historical=solve_case(old,p)
    new,p=reserve_case(loads,cold_capacity=0.,cold_energy=0.);result=solve_case(new,p)
    assert historical['action']['ups_discharge_kW']>1.
    assert result['action']['ups_discharge_kW']<1e-6
    assert result['lost'][0,0]<1e-4
    assert result['lost'][2,0]>historical['lost'][2,0]+1.


def test_cold_to_critical_releasing_ec_to_general_counts_as_reserve_use():
    loads=np.zeros((4,3));loads[0]=[20.,0.,100.];loads[2,2]=100.
    old,p=reserve_case(loads,rule='current_service_bridge',ups_capacity=0.,ups_energy=0.,grid=50.)
    historical=solve_case(old,p)
    new,p=reserve_case(loads,ups_capacity=0.,ups_energy=0.,grid=50.);result=solve_case(new,p)
    assert historical['action']['cold_discharge_kW']>1.
    assert result['action']['cold_discharge_kW']<1e-6
    assert np.max(result['lost'][:2])<1e-4
    assert result['lost'][2,2]>historical['lost'][2,2]+1.


def test_below_target_stock_still_supplies_current_critical_deficits():
    loads=np.zeros((4,3));loads[0]=[100.,0.,100.]
    env,p=reserve_case(loads,ups_energy=10.,cold_energy=10.,grid=2.)
    result=solve_case(env,p,minutes=1.)
    assert result['action']['ups_discharge_kW']>99.9
    assert result['action']['cold_discharge_kW']>99.9
    assert np.max(result['lost'][:2])<1e-4


def test_reserve_charge_may_yield_general_but_preserves_current_critical():
    loads=np.zeros((4,3));loads[0]=[20.,0.,10.];loads[2,0]=50.
    old,p=reserve_case(loads,rule='current_service_bridge',ups_capacity=0.,ups_energy=0.,cold_energy=0.,grid=30.)
    historical=solve_case(old,p)
    new,p=reserve_case(loads,ups_capacity=0.,ups_energy=0.,cold_energy=0.,grid=30.);result=solve_case(new,p)
    assert historical['action']['cold_charge_kW']<1e-6
    assert result['action']['cold_charge_kW']>1.
    assert np.all(result['lost'][:2]<=np.asarray(historical['lost'])[:2]+1e-9)
    assert result['lost'][2,0]>historical['lost'][2,0]+1.


def test_uncapped_reserve_shortfall_is_not_hidden_by_capacity_truncation():
    loads=np.zeros((4,3));loads[0]=[100.,0.,200.]
    env,_=reserve_case(loads,ups_energy=40.,cold_energy=60.)
    observed=env.core.observe().get('m25_reserve',{})
    assert observed.get('ups',{}).get('uncapped_target_kWh')==pytest.approx(100.*.5/.95)
    assert observed['ups']['capacity_shortfall_kWh']>12.
    assert observed['cold']['capacity_shortfall_kWh']>45.
    assert observed['branch_reference']['building_auxiliary_already_included']
    assert not observed['branch_reference']['building_UPS_supplies_station']


def test_only_original_four_carrier_sum_service_objectives_remain():
    loads=np.zeros((4,3));loads[0]=[20.,0.,100.];loads[2,0]=50.
    env,p=reserve_case(loads,grid=50.);result=solve_case(env,p)
    names=[row['name'] for row in result['_m23_objective_hierarchy']]
    assert [x for x in names if isinstance(x,int)]==[0,1,2,3]
    assert names.index('bridge')<names.index(2)


def test_selected_action_has_pure_same_snapshot_no_store_witness():
    loads=np.zeros((4,3));loads[0]=[20.,0.,100.];loads[2,2]=100.
    env,p=reserve_case(loads,grid=50.);core=env.core
    before=(core.time,core.ups.energy_kWh,core.cold_store.energy_kWh,copy.deepcopy(core.pending_commands))
    result=solve_case(env,p)
    witness=result.get('m25_reserve',{}).get('no_storage_witness',{})
    assert witness.get('status')=='solved'
    assert witness['inventory_committed'] is False
    assert witness['same_current_state'] and witness['pending_target_credited'] is False
    assert before==(core.time,core.ups.energy_kWh,core.cold_store.energy_kWh,core.pending_commands)


def test_true_inventory_surplus_may_support_general_service():
    loads=np.zeros((4,3));loads[0,0]=20.;loads[2,0]=50.
    env,p=reserve_case(loads,ups_energy=40.,cold_capacity=0.,cold_energy=0.)
    result=solve_case(env,p)
    assert result['action']['ups_discharge_kW']>1.
    assert result['lost'][2,0]<50.-1.
    remaining=40.-result['action']['ups_discharge_kW']*.25/.95
    assert remaining>=20.*.5/.95-1e-6


def test_design_target_accounts_for_declared_storage_self_loss():
    import math
    loads=np.zeros((4,3));loads[0,2]=100.
    env,_=reserve_case(loads);env.core.cold_store.config['loss_per_hour']=.01
    reported=env.core.observe()['m25_reserve']['cold']
    expected=100.*.5/.95*math.exp(.01*.5)
    assert reported['uncapped_target_kWh']==pytest.approx(expected)
    assert reported.get('standing_loss_bound_included') is True


def test_tank_pump_cannot_be_supplied_from_building_ups_only():
    loads=np.zeros((4,3));loads[0]=[20.,0.,100.]
    env,p=reserve_case(loads,ups_energy=40.,cold_energy=60.,grid=0.)
    result=solve_case(env,p,minutes=1.)
    assert result['action']['cold_discharge_kW']<1e-6
    assert result['action']['cold_storage_pump_kW']<1e-6
    assert result['action']['ups_discharge_kW']>19.9
    assert result['lost'][0,2]>99.9
    assert result['m25_reserve']['cold']['current_path_deliverable'] is False


def test_legacy_heat_reference_is_retained_and_honestly_qualified():
    loads=np.zeros((4,3));loads[0]=[20.,10.,100.]
    env,p=reserve_case(loads)
    legacy='existing gas boiler has no separate electrical auxiliary in this model'
    diagnostic=dict(model_closeout=dict(critical_reserve_rule=dict(heat_reference=legacy,reference_shaft_kW=123.)))
    _,observed=env.core._prepare_command_dispatch(p,diagnostic,loads)
    corrected=observed['model_closeout']['critical_reserve_rule']
    assert corrected.get('legacy_heat_reference')==legacy
    assert corrected['reference_shaft_kW']==123.
    assert 'historical policy reference' in corrected['heat_reference']
    assert corrected['legacy_reference_omits_station_auxiliary']


def test_cold_charge_cannot_take_power_before_actual_boiler_restart():
    loads=np.zeros((4,3));loads[0]=[20.,0.,10.];loads[2,0]=50.
    env,p=reserve_case(loads,ups_capacity=0.,ups_energy=0.,cold_energy=0.,grid=25.)
    core=env.core;original=core._hardware_data_at
    def boiler_available(minute):
        h,row,current,f=original(minute);f['BOILER_1']=1.
        return h,row,current,f
    core._hardware_data_at=boiler_available
    p['boiler_steam_lbph']=300.;core.arrived_dispatch=copy.deepcopy(p);core.executed=copy.deepcopy(p)
    core.station.boiler_ready=False;core.station.boiler_powered_minutes=0.
    result=solve_case(env,p,minutes=1.)
    names=[row['name'] for row in result['_m23_objective_hierarchy']]
    assert names.index('start')<names.index('bridge')<names.index(2)
    assert result['action']['boiler_restart_electric_kW']==pytest.approx(.5)
    assert result['action']['cold_charge_kW']>1.
    assert np.max(result['lost'][:2])<1e-4
    assert not core.station.boiler_ready and core.station.boiler_powered_minutes==0.
