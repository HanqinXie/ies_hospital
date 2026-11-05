"""M23 preparation is queued permission, never actual station consumption."""
import copy
import json
from pathlib import Path
import sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_sci_environment import scenario

def make(config=None):
    from tools.model_correctness_v23.environment import OperatingHospitalIESEnv
    authority=json.loads((ROOT/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json').read_text(encoding='utf-8'))
    sealed=[int(x['time_block_id'].rsplit('_',1)[1]) for x in authority['p2b_time_blocks'] if x['reserved_only']]
    return OperatingHospitalIESEnv(ROOT,allowed_hours=[4001,4002],sealed_days=sealed,model_config=config or {})

@pytest.mark.parametrize('alias',['R','M','F','C','A'])
def test_current_heat_keeps_queued_preparation_and_pays_actual_restart(alias):
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    e=make();s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.,faults={f'GT_{i}':0. for i in (1,2,3)},
        model_closure_events=[dict(time_minute=0.,availability={'STATION_AC':0.}),dict(time_minute=.5,availability={'STATION_AC':1.})])
    e.reset(scenario=s,seed=7);c=e.core
    request,_=SciPolicy(alias,plant=e.plant).decide([decision_snapshot(e)])
    command=c.generate_command(request)
    assert command['dispatch']['boiler_steam_lbph']>1.
    assert command['joint_dispatch']['diagnostic']['m23_preparation']['conditional_permission_is_actual_supply'] is False
    c.advance(2.)
    assert all(x['execution_model_id']=='M23_model_correctness' for x in e.segment_history)
    assert all(x['action']['boiler_steam_lbph']<1e-4 for x in e.segment_history if x['start_minute']<1.5-1e-8)
    assert any(x['action']['boiler_steam_lbph']>1. for x in e.segment_history if x['start_minute']>=1.5-1e-8)
    assert sum(x['action']['boiler_restart_electric_kW']*(x['end_minute']-x['start_minute'])/60. for x in e.segment_history)==pytest.approx(.5/60.,abs=1e-7)
    assert all(max(map(abs,x['energy_balance_residuals']))<1e-4 for x in e.segment_history)

@pytest.mark.parametrize('negative',['station_branch','permission_pending','grid_not_connected','restart_power_lost'])
def test_recovery_negative_conditions_do_not_complete_restart(negative):
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    e=make();s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.,faults={f'GT_{i}':0. for i in (1,2,3)})
    if negative=='station_branch':s['model_closure_events']=[dict(time_minute=0.,availability={'STATION_AC':0.})]
    if negative=='restart_power_lost':s['model_closure_events']=[dict(time_minute=0.,availability={'STATION_AC':0.}),dict(time_minute=.5,availability={'STATION_AC':1.}),dict(time_minute=1.,availability={'STATION_AC':0.}),dict(time_minute=1.5,availability={'STATION_AC':1.})]
    if negative=='grid_not_connected':s['faults'].update(GRID=0.,DIESEL=0.)
    e.reset(scenario=s,seed=7);c=e.core
    request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)]);command=c.generate_command(request)
    if negative=='permission_pending':command['arrival_minute']=5.;c.arrived_dispatch['boiler_steam_lbph']=0.;c.executed['boiler_steam_lbph']=0.
    c.advance(2.)
    assert all(x['action']['boiler_steam_lbph']<1e-4 for x in e.segment_history)
    assert not c.station.boiler_ready
    if negative=='restart_power_lost':assert c.station.boiler_powered_minutes==pytest.approx(.5)

def test_second_supply_loss_requires_another_full_paid_restart():
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    e=make();s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.,faults={f'GT_{i}':0. for i in (1,2,3)},
        model_closure_events=[dict(time_minute=0.,availability={'STATION_AC':0.}),dict(time_minute=.5,availability={'STATION_AC':1.}),
            dict(time_minute=2.,availability={'STATION_AC':0.}),dict(time_minute=2.5,availability={'STATION_AC':1.})])
    e.reset(scenario=s,seed=7);c=e.core;request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
    c.generate_command(request);c.advance(4.)
    assert any(x['action']['boiler_steam_lbph']>1. for x in e.segment_history if 1.5<=x['start_minute']<2.)
    assert all(x['action']['boiler_steam_lbph']<1e-4 for x in e.segment_history if 2.<=x['start_minute']<3.5-1e-8)
    assert any(x['action']['boiler_steam_lbph']>1. for x in e.segment_history if x['start_minute']>=3.5-1e-8)
    assert sum(x['action']['boiler_restart_electric_kW']*(x['end_minute']-x['start_minute'])/60. for x in e.segment_history)==pytest.approx(1./60.,abs=1e-7)

def test_actual_dc_crank_spends_attempt_despite_zero_command_and_third_is_blocked():
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    e=make();s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.,faults=dict(GRID=0.,GT_1=0.,GT_2=0.,GT_3=0.),
        model_closure_events=[dict(time_minute=.5,availability={'DG_START_DC':0.}),dict(time_minute=.75,availability={'DG_START_DC':1.}),
            dict(time_minute=1.25,availability={'DG_START_DC':0.}),dict(time_minute=1.5,availability={'DG_START_DC':1.})])
    e.reset(scenario=s,seed=7);c=e.core;request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
    command=c.generate_command(request);cancel=copy.deepcopy(command);cancel.update(id=2,generated_minute=0.,arrival_minute=.1)
    cancel['dispatch']['diesel_kW']=0.;c.pending_commands.append(cancel)
    c.advance(.25)
    assert c.state.diesel_status=='STARTING' and c.state.diesel_start_minute==0.
    assert c.arrived_dispatch['diesel_kW']==0. and c.station.diesel_starts_used==1
    assert any(x['diesel_local_response']['mechanical_work_kWh']>0. for x in e.segment_history)
    c.advance(2.)
    starts=[x for x in c.events if x['kind']=='m22_DG_dedicated_DC_start']
    assert len(starts)==2 and c.station.diesel_starts_used==2
    assert c._hardware_data_at(2.)[-1]['DG_START_DC']==1.
    assert c._hardware_data_at(2.)[-1]['DIESEL']==0.
    assert all(x['action']['diesel_kW']==0. for x in e.segment_history if x['start_minute']>=1.5)

def test_thermal_episode_reset_rebuilds_frozen_initial_state(tmp_path):
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    # Explicit synthetic integration fixture. Real E+ native inputs and
    # frozen parameter validation are independently retained by the study.
    reference=tmp_path/'reference.csv'
    reference.write_text('elapsed_interval_start_minute,interval_minutes,zone,multiplier,heating_kW,cooling_kW\n0,10,OR1,1,0,1\n10,10,OR1,1,0,1\n20,10,OR1,1,0,1\n',encoding='utf-8')
    parameters=tmp_path/'parameters.json'
    parameters.write_text(json.dumps(dict(reference_parameters=[.2,4.,2.,.1],zone_scales={'OR1':1.},zone_multipliers={'OR1':1.})),encoding='utf-8')
    e=make(dict(model_correctness_v23=dict(thermal_coupling=dict(reference_csv=str(reference),parameters_json=str(parameters)))))
    states=[]
    for _ in range(2):
        e.reset(scenario=scenario(),seed=8)
        assert e.thermal_coupling.observe()['revision']==0
        assert e.thermal_coupling.observe()['last_end_minute']==0.
        request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
        e.core.generate_command(request);e.core.advance(1.)
        states.append(e.thermal_coupling.observe())
        assert e.segment_history[-1]['m23_temperature_diagnostic']['cold_ENS_offset_kWh']==0.
    assert states[0]==states[1]

def test_current_recovery_demand_adds_queued_converter_headroom(tmp_path):
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    reference=tmp_path/'reference.csv'
    reference.write_text('elapsed_interval_start_minute,interval_minutes,zone,multiplier,heating_kW,cooling_kW\n'+''.join(f'{t},10,OR1,1,0,1\n' for t in range(0,50,10)),encoding='utf-8')
    parameters=tmp_path/'parameters.json';parameters.write_text(json.dumps(dict(reference_parameters=[.2,4.,2.,.1],zone_scales={'OR1':1.},zone_multipliers={'OR1':1.})),encoding='utf-8')
    e=make(dict(model_correctness_v23=dict(thermal_coupling=dict(reference_csv=str(reference),parameters_json=str(parameters)))))
    s=scenario(aux=True);s.update(fault_start_minute=0.,fault_duration_minutes=10.)
    e.reset(scenario=s,seed=10);policy=SciPolicy('R',plant=e.plant)
    request,_=policy.decide([decision_snapshot(e)]);e.step(request)
    current=decision_snapshot(e);request,_=policy.decide([current]);command=e.core.generate_command(request)
    diagnostic=command['joint_dispatch']['diagnostic']['m23_preparation']
    assert diagnostic['current_extra_recovery_request_kW']>0.
    assert diagnostic['queued_extra_converter_headroom_kW']>0.
    assert command['arrival_minute']>=command['generated_minute']==15.
    e.core.advance(30.)
    actual=[x for x in e.segment_history if x['start_minute']>=15.]
    assert any(x['action']['recovery_cooling_kW']>0. for x in actual)
    assert all(x['m23_temperature_diagnostic']['cold_ENS_offset_kWh']==0. for x in actual)
    assert all(max(map(abs,x['energy_balance_residuals']))<1e-4 for x in actual)

def test_actual_prefix_observer_sees_final_ledgers_and_never_repeats_segments():
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    e=make();observed=[];e.actual_segment_observer=lambda segment:observed.append(copy.deepcopy(segment))
    e.reset(scenario=scenario(),seed=11)
    request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
    e.core.generate_command(request);e.core.advance(1.);e.core.advance(2.)
    assert len(observed)==len(e.segment_history)
    assert [(x['start_minute'],x['end_minute']) for x in observed]==[(x['start_minute'],x['end_minute']) for x in e.segment_history]
    assert all(x['execution_model_id']=='M23_model_correctness' and 'service_request_ledger' in x and 'm21_storage' in x for x in observed)
