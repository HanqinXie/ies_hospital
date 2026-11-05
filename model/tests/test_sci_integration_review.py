"""Independent integration review: causal inputs, permission and energy events."""
import copy
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_sci_environment import env,scenario
from hospital_ies.model import RT_TO_KW
from tools.sci_engineering import MODEL_ID
from tools.sci_engineering.environment import decision_snapshot
from tools.sci_engineering.policy import SciPolicy


def _direct(dispatch):
    return {'ordinary_dispatch':dict(dispatch=copy.deepcopy(dispatch),
        disconnected_kW=np.zeros((4,3)).tolist())}


def _advance_policy(e,policy,steps=1):
    for _ in range(steps):
        command,_=policy.decide([decision_snapshot(e)])
        e.step(command)


def test_reset_without_resource_override_restores_registered_default():
    e=env();s=scenario();default=e.config['diesel_initial_energy_kWh']
    override=copy.deepcopy(s);override['initial_reserve_kWh']=123.
    e.reset(scenario=override,seed=12)
    assert e.state.diesel_energy_remaining_kWh==123.
    e.reset(scenario=s,seed=12)
    assert e.state.diesel_energy_remaining_kWh==default


def test_committed_segments_keep_the_actual_M21_identity():
    e=env();e.reset(scenario=scenario(aux=True),seed=21)
    _advance_policy(e,SciPolicy('M',plant=e.plant))
    assert e.segment_history
    assert {x['execution_model_id'] for x in e.segment_history}=={MODEL_ID}
    assert all('service_dependencies' in x for x in e.segment_history)


def test_different_future_events_have_same_current_observation_and_request():
    a=env();b=env();one=scenario();two=copy.deepcopy(one)
    one.update(fault_start_minute=30.,fault_duration_minutes=10.)
    two.update(fault_start_minute=35.,fault_duration_minutes=5.)
    two['faults']={'TOWER':0.}
    a.reset(scenario=one,seed=19);b.reset(scenario=two,seed=19)
    oa,ob=decision_snapshot(a),decision_snapshot(b)
    assert oa['features']==ob['features']
    assert oa['sci_auxiliary']==ob['sci_auxiliary']
    for alias in ('R','M','F','C','A'):
        left=SciPolicy(alias,plant=a.plant).decide([oa])[0]
        right=SciPolicy(alias,plant=b.plant).decide([ob])[0]
        assert left==right,alias


def test_rule_command_is_not_sent_through_upper_optimizer(monkeypatch):
    from tools.sci_engineering import planner
    e=env();e.reset(scenario=scenario(),seed=14)
    command,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
    def forbidden(*args,**kwargs):
        raise AssertionError('An ordinary rule command was replanned')
    monkeypatch.setattr(planner,'solve_joint',forbidden)
    queued=e.core.generate_command(command)
    assert queued['dispatch']==command['ordinary_dispatch']['dispatch']


def test_unarrived_converter_permission_does_not_increase_actual_cooling():
    e=env();s=scenario();s['faults']={'GT_3':0.}
    s.update(fault_start_minute=15.,fault_duration_minutes=30.,response_delay_minutes=15.)
    e.reset(scenario=s,seed=24)
    original=copy.deepcopy(e.core.executed)
    low=copy.deepcopy(original)
    low['absorption_cooling_kW']=0.;low['electric_chiller_cooling_kW']=[0.,0.,0.]
    e.core.generate_command(_direct(low));e.core.advance(15.)
    increased=e.core.generate_command(_direct(original))
    assert increased['arrival_minute']==30.
    e.core.advance(29.)
    pending=[x for x in e.segment_history if 15.<=x['start_minute']<29.]
    assert pending
    assert all(x['action']['absorption_cooling_kW']+sum(x['action']['electric_chiller_cooling_kW'])<1e-6 for x in pending)
    assert all(x['arrived_source_permissions']['absorption_cooling_kW']==0. for x in pending)


def test_storage_trial_solves_do_not_debit_actual_inventory():
    config=dict(sci_engineering=dict(configuration_id='H1',ups=dict(capacity_kWh=.1,power_kW=1000.,initial_soc=1.)))
    e=env(config);s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.)
    s['faults'].update({f'GT_{i}':0. for i in (1,2,3)})
    e.reset(scenario=s,seed=22);e.core.boundary()
    _,_,loads,availability=e.core.data_at(0.)
    before=e.core.ups.energy_kWh
    for _ in range(2):
        result=e.core._solve(loads,availability,1./60.,'review_noncommitting_probe',{},
            execution_action=copy.deepcopy(e.core.executed))
        assert result['action']['ups_discharge_kW']>0.
        assert e.core.ups.energy_kWh==before


def _finite_bridge(max_minutes):
    config=dict(sci_engineering=dict(configuration_id='H1',storage_max_segment_minutes=max_minutes,
        ups=dict(capacity_kWh=.1,power_kW=1000.,initial_soc=1.)))
    e=env(config);s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.)
    s['faults'].update({f'GT_{i}':0. for i in (1,2,3)})
    e.reset(scenario=s,seed=29)
    _advance_policy(e,SciPolicy('M',plant=e.plant))
    return e


def test_finite_bridge_preserves_a_positive_full_service_prefix_before_exhaustion():
    e=_finite_bridge(1.)
    first=e.segment_history[0]
    assert first['end_minute']>first['start_minute']
    assert first['end_minute']-first['start_minute']<.99
    assert first['unserved_kW'][0][0]<.01
    assert first['m21_storage']['ups']['final_kWh']<1e-7
    assert e.core.ups.energy_kWh>=0.


def test_bridge_first_electric_gap_is_independent_of_half_step_recording():
    fine=_finite_bridge(.5);coarse=_finite_bridge(1.)
    def gap(e):
        return next(x['start_minute'] for x in e.segment_history if x['unserved_kW'][0][0]>.01)
    assert gap(fine)>0.
    assert gap(coarse)==pytest.approx(gap(fine),abs=1e-6)


def test_auxiliary_decomposition_does_not_add_its_electricity_twice():
    e=env();s=scenario();s['faults']={};s['fault_start_minute']=30.
    e.reset(scenario=s,seed=25)
    _advance_policy(e,SciPolicy('M',plant=e.plant))
    p=e.plant
    for segment in e.segment_history:
        action=segment['action'];loads=np.asarray(segment['loads_kW']);unmet=np.asarray(segment['unserved_kW'])
        delivered=loads-unmet
        available=sum(action['gt_electric_kW'])+action['grid_import_kW']+action['diesel_kW']
        plant_aux=action['absorption_cooling_kW']/RT_TO_KW*p.abs_electric_kW_per_rt
        plant_aux+=sum(action['electric_chiller_cooling_kW'])/RT_TO_KW*p.ec_electric_kW_per_rt
        assert available-plant_aux==pytest.approx(delivered[:,0].sum(),abs=1e-4)
        detail=segment['service_dependencies']
        assert sum(detail['direct_electric_delivered_kW'])+np.asarray(detail['auxiliary_delivered_kW']).sum()==pytest.approx(delivered[:,0].sum(),abs=1e-4)


def test_same_ordinary_command_identity_preserves_storage_and_all_actual_states():
    config=dict(sci_engineering=dict(configuration_id='H1',ups=dict(capacity_kWh=.1,power_kW=1000.,initial_soc=1.)))
    a=env(config);b=env(config);s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.)
    s['faults'].update({f'GT_{i}':0. for i in (1,2,3)})
    for e in (a,b):
        e.reset(scenario=s,seed=33);e.core.boundary()
    command=_direct(a.core.executed)
    ca=a.core.generate_command(command);cb=b.core.generate_command(command)
    ca['policy_id']='identity_a';cb['policy_id']='identity_b'
    a.core.advance(1.);b.core.advance(1.)
    assert len(a.segment_history)==len(b.segment_history)
    for left,right in zip(a.segment_history,b.segment_history):
        for field in ('pre_state','post_state','m21_storage','action'):
            assert left[field]==right[field],field
        np.testing.assert_allclose(left['unserved_kW'],right['unserved_kW'],atol=1e-7,rtol=0.)
    assert a.core.ups.energy_kWh==b.core.ups.energy_kWh


def test_repair_does_not_refill_storage_and_actual_recharge_has_paid_input():
    config=dict(sci_engineering=dict(configuration_id='H1',ups=dict(capacity_kWh=.1,power_kW=1000.,initial_soc=1.)))
    e=env(config);s=scenario();s['duration_steps']=4
    s['faults'].update({f'GT_{i}':0. for i in (1,2,3)})
    e.reset(scenario=s,seed=34);p=SciPolicy('M',plant=e.plant)
    _advance_policy(e,p,steps=2)
    assert e.core.ups.energy_kWh<1e-7
    # Hardware repair is not actual reconnection. Include the inherited
    # destination readiness, finite bridge wait and transfer tail through 60m.
    _advance_policy(e,p,steps=2)
    receipts=[(x['start_minute'],x['m21_storage']['ups']) for x in e.segment_history]
    charged=sum(r['charged_input_kWh'] for _,r in receipts)
    discharged=sum(r['discharged_kWh'] for _,r in receipts)
    loss=sum(r['conversion_loss_kWh']+r['standing_loss_kWh'] for _,r in receipts)
    assert charged>0.
    first_charge=next((t,r) for t,r in receipts if r['charged_input_kWh']>0.)
    assert first_charge[0]>=30.
    assert first_charge[1]['initial_kWh']<1e-7
    assert .1+charged-discharged-loss-e.core.ups.energy_kWh==pytest.approx(0.,abs=1e-7)


@pytest.mark.parametrize('alias',['R','M'])
def test_R_and_M_request_observed_healthy_utility_and_complete_real_return(alias):
    e=env();s=scenario();s['duration_steps']=6
    s['faults'].update({f'GT_{i}':0. for i in (1,2,3)})
    e.reset(scenario=s,seed=42);p=SciPolicy(alias,plant=e.plant)
    _advance_policy(e,p,steps=2)
    snapshot=decision_snapshot(e)
    assert snapshot['features']['availability_GRID']==0.
    assert snapshot['execution_limits']['hardware_grid_available_fraction']==1.
    assert snapshot['execution_limits']['grid_bus_state']=='ISLAND'
    request,_=p.decide([snapshot]);queued=e.core.generate_command(request)
    assert queued['dispatch']['grid_import_kW']>0.
    assert queued['dispatch']['diesel_kW']>0.
    e.core.advance(45.)
    # Observe a complete recovery tail; a healthy hardware instant does not
    # promise that the actual GT/boiler path can bridge the OPEN interval.
    for end in (60.,75.,90.):
        request,_=p.decide([decision_snapshot(e)]);e.core.generate_command(request);e.core.advance(end)
    assert e.core.grid_bus_state=='GRID'
    assert any(x['kind']=='grid_bus_state_changed' and x['after']=='GRID' for x in e.event_history)
