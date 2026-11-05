"""Lawful snapshot feedback only; no environment, input data or optimizer runs."""
import copy
import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def api():
    try:
        return importlib.import_module('tools.coordinated_dispatch.policy')
    except ModuleNotFoundError:
        pytest.fail('Independent coordinated policy is missing')


def row(t=0., **changes):
    from hospital_ies.resilience import ZONE_PRIORITY
    features = dict(grid_import_limit_kW=350., arrived_grid_import_limit_kW=350.,
        grid_import_prev_kW=120., diesel_prev_kW=0., diesel_energy_kWh=2400.,
        diesel_status='OFF', topology_mode='legacy_explicit_permission',
        availability_GRID=1., control_available=True)
    for i in (1, 2, 3):
        features.update({f'pre_GT_{i}_kW':120. if i < 3 else 0.,
            f'state_GT_{i}':'RUNNING' if i < 3 else 'OFF',
            f'overload_minutes_GT_{i}':0.})
    features.update({f'{g}_{c}_demand_kW':20. for g in ('P0','P1','P2','P3') for c in ('e','h','c')})
    features.update({'zone_margin_'+z:1. for z,p in ZONE_PRIORITY.items() if p==2})
    features.update(changes)
    return dict(decision_time_min=t, observation_time_min=t, features=features,
        feature_metadata={k:dict(available_time_min=t,valid_time_min=t) for k in features},
        known_command_queue=[], arrived_command_id=None)


def queued(generated=15., arrival=45.):
    return dict(command_id=2, generated_minute=generated, planned_arrival_minute=arrival,
        request={}, permitted_dispatch=dict(gt_electric_kW=[120.,120.,0.],
        grid_import_kW=350.,diesel_kW=0.))


def test_fixed_and_feedback_share_every_option_except_actual_preference():
    a=api(); state=a.CoordinatedDispatchPolicy({}, 'state')
    fixed=a.CoordinatedDispatchPolicy({'coordination_mode':'fixed','fixed_preference':'standby_first'}, 'fixed')
    assert not hasattr(state,'bind_current')
    r=row(15.,availability_GRID=0.,diesel_status='STARTING',grid_import_prev_kW=0.,arrived_grid_import_limit_kW=0.)
    before=copy.deepcopy(r); action, info=state.decide([r]); other,_=fixed.decide([r])
    options=action['coordinated_joint']['options']; fixed_options=other['coordinated_joint']['options']
    assert options['coordination_preference']=='bridge_first' and info['coordination_state']=='SUPPORT'
    assert {k:v for k,v in options.items() if k!='coordination_preference'}=={k:v for k,v in fixed_options.items() if k!='coordination_preference'}
    assert options['service_budget_fractions']==[.05,.20] and options['support_hours']==2.
    assert options['critical_reserve_multiplier']==1.25 and options['handoff_enabled'] is True
    assert options['preserve_one_gt_standby'] is True and options['regulator_consistent'] is True
    assert action['coordinated_joint']['risk_loads_kW']==[[20.]*3 for _ in range(4)]
    assert state.decide([row()])[0]['coordinated_joint']['options']['coordination_preference']=='standby_first'
    assert r==before and info['actual_diesel_kW']==0. and info['future_rollouts_online']==0


def test_observed_recovery_waits_for_actual_handoff_and_thermal_combines_preferences():
    a=api(); policy=a.CoordinatedDispatchPolicy({},'feedback')
    outage=row(15.,availability_GRID=0.,diesel_status='CONNECTED',diesel_prev_kW=200.,
        grid_import_prev_kW=0.,arrived_grid_import_limit_kW=0.)
    recovery=row(30.,grid_import_prev_kW=0.,arrived_grid_import_limit_kW=0.,
        pre_GT_1_kW=0.,pre_GT_2_kW=0.,state_GT_1='OFF',state_GT_2='FAILED')
    recovery['known_command_queue']=[queued()]
    request,info=policy.decide([row(),outage,recovery])
    assert info['coordination_state']=='RECOVERY' and request['coordinated_joint']['options']['coordination_preference']=='bridge_first'
    assert info['known_command_queue'][0]['planned_arrival_minute']==45.
    only_plan=row(30.,grid_import_prev_kW=0.,arrived_grid_import_limit_kW=350.,
        pre_GT_1_kW=0.,pre_GT_2_kW=0.,state_GT_1='OFF',state_GT_2='OFF')
    assert policy.decide([row(),outage,only_plan])[1]['coordination_state']=='RECOVERY'
    weak_actual=copy.deepcopy(only_plan);weak_actual['features']['pre_GT_1_kW']=1.;weak_actual['features']['state_GT_1']='RUNNING'
    assert policy.decide([row(),outage,weak_actual])[1]['coordination_state']=='RECOVERY'
    ready=row(45.,grid_import_prev_kW=80.,arrived_grid_import_limit_kW=350.)
    assert policy.decide([row(),outage,recovery,ready])[1]['coordination_state']=='NORMAL'
    thermal=row(); key=next(k for k in thermal['features'] if k.startswith('zone_margin_'))
    thermal['features'][key]=0.
    assert policy.decide([thermal])[0]['coordinated_joint']['options']['coordination_preference']=='service_first'
    thermal['features']['overload_minutes_GT_1']=2.
    assert policy.decide([thermal])[0]['coordinated_joint']['options']['coordination_preference']=='bridge_service_first'


def test_queue_and_feature_clock_checks_reject_future_or_hidden_outcomes():
    a=api(); policy=a.CoordinatedDispatchPolicy({},'feedback')
    for poison in ('labels','actual_execution','post_outcome','first_failure_observation_interval'):
        r=row();r[poison]={'future':999.}
        with pytest.raises(ValueError):policy.decide([r])
    r=row();r['feature_metadata']['diesel_energy_kWh']['valid_time_min']=1.
    with pytest.raises(ValueError):policy.decide([r])
    r=row(30.);r['known_command_queue']=[queued(31.,45.)]
    with pytest.raises(ValueError):policy.decide([r])
    r=row(30.);r['known_command_queue']=[queued(15.,float('nan'))]
    with pytest.raises(ValueError):policy.decide([r])
    r=row();r['features']['future_grid_end_minute']=360.;r['feature_metadata']['future_grid_end_minute']={'available_time_min':0.,'valid_time_min':0.}
    with pytest.raises(ValueError):policy.decide([r])
    r=row();del r['features']['diesel_status'];del r['feature_metadata']['diesel_status']
    with pytest.raises(ValueError):policy.decide([r])


def test_scenario_root_poison_is_ignored_and_current_budget_is_not_mutated():
    a=api(); policy=a.CoordinatedDispatchPolicy({},'feedback')
    r=row(30.,diesel_status='CONNECTED',diesel_prev_kW=200.,diesel_energy_kWh=10.)
    expected=policy.decide([r]);bad=copy.deepcopy(r)
    class Poison:
        def __deepcopy__(self,memo):raise AssertionError('Hidden scenario/root read')
        def __getattr__(self,name):raise AssertionError('Hidden scenario/root read')
    bad.update(scenario=Poison(),root=Poison(),scenario_id='unseen',stage=99)
    assert policy.decide([bad])==expected
    assert expected[1]['fuel_coverage_hours']==.05
    assert expected[0]['coordinated_joint']['options']['service_budget_fractions']==[.05,.20]
    for cfg in ({'coordination_mode':'oracle'},{'fixed_preference':'future'},{'future_grid_end':100.}):
        with pytest.raises(ValueError):a.CoordinatedDispatchPolicy(cfg,'bad')
