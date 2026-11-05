"""A separate lawful v14 request, leaving legacy F/H/Z factories untouched."""
import copy
import importlib
import pytest
from test_coordinated_dispatch_policy import row


def api():
    try:
        return importlib.import_module('tools.engineering_model.policy')
    except ModuleNotFoundError:
        pytest.fail('Engineering policy is missing')


def test_policy_requests_current_service_recovery_without_scenario_metadata():
    a = api(); policy = a.EngineeringPolicy({'mode': 'service_restore'}, 'service_restore_v14')
    observation = row(); observation['scenario_id'] = 'forbidden_to_use'
    before = copy.deepcopy(observation)
    request, info = policy.decide([observation])
    assert set(request) == {'engineering_joint'}
    assert request['engineering_joint']['engineering_options']['mode'] == 'service_restore'
    assert info['policy_id'] == 'service_restore_v14'
    assert info['restoration_request'] is True
    assert observation == before
    observation['scenario_id'] = 'different'
    assert policy.decide([observation])[0] == request


def test_endurance_uses_only_current_disturbed_state_and_real_fuel():
    a = api(); policy = a.EngineeringPolicy({'mode': 'endurance_restore'}, 'endurance_restore_v14')
    normal, normal_info = policy.decide([row()])
    disturbed, info = policy.decide([row(15., availability_GRID=0., diesel_prev_kW=200.,
        diesel_energy_kWh=50., diesel_status='CONNECTED')])
    assert not normal_info['endurance_preparation_requested']
    assert info['endurance_preparation_requested']
    assert disturbed['engineering_joint']['engineering_options']['mode'] == 'endurance_restore'
    assert normal['engineering_joint']['engineering_options']['mode'] == 'endurance_restore'


def test_policy_and_request_reject_hidden_future_invalid_fields():
    a = api()
    with pytest.raises(ValueError): a.EngineeringPolicy({'mode': 'service_restore', 'scenario_id': 'N2'}, 'v14')
    with pytest.raises(ValueError): a.EngineeringPolicy({}, '')
    with pytest.raises(ValueError): a.validate_action({'engineering_joint': {}, 'coordinated_joint': {}})
    with pytest.raises(ValueError): a.validate_action({'engineering_joint': {'future_events': []}})
    r = row(); r['feature_metadata']['availability_GRID']['available_time_min'] = 15.
    with pytest.raises(ValueError): a.EngineeringPolicy({}, 'v14').decide([r])


def test_explicit_E_r3_requests_guarded_headroom_without_changing_default_routes():
    a = api()
    policy = a.EngineeringPolicy({'mode': 'endurance_restore', 'grid_headroom_preference': True},
        'endurance_restore_headroom_r3_v14')
    request, info = policy.decide([row()])
    assert request['engineering_joint']['engineering_options']['grid_headroom_preference'] is True
    assert info['policy_id'] == 'endurance_restore_headroom_r3_v14'
    for mode in ('service_restore', 'endurance_restore'):
        ordinary, _ = a.EngineeringPolicy({'mode': mode}, 'unchanged_default').decide([row()])
        assert ordinary['engineering_joint']['engineering_options']['grid_headroom_preference'] is False
    for bad in (0, 1, 'true', None):
        with pytest.raises(ValueError):
            a.EngineeringPolicy({'grid_headroom_preference': bad}, 'bad_headroom')


def test_execution_start_preference_is_explicit_and_keeps_old_S_C_E_defaults():
    a = api()
    p = a.EngineeringPolicy(dict(mode='endurance_restore', grid_headroom_preference=True,
        execution_aware_start_preference=True), 'endurance_restore_execution_v14r4')
    request, info = p.decide([row()])
    assert request['engineering_joint']['engineering_options']['execution_aware_start_preference'] is True
    assert info['policy_id'] == 'endurance_restore_execution_v14r4'
    for config in ({'mode':'service_restore'}, {'mode':'service_restore','contact_mode':'separate'}, {'mode':'endurance_restore'}):
        request, _ = a.EngineeringPolicy(config, 'prior_registered_identity').decide([row()])
        assert request['engineering_joint']['engineering_options']['execution_aware_start_preference'] is False
    for bad in (0, 1, None, 'true'):
        with pytest.raises(ValueError):a.EngineeringPolicy({'execution_aware_start_preference':bad}, 'bad')


def test_execution_commitment_preference_is_explicit_without_redefining_R4():
    a=api()
    policy=a.EngineeringPolicy(dict(mode='endurance_restore',grid_headroom_preference=True,
        execution_aware_commitment_preference=True),'endurance_restore_commitment_v14r5')
    request,_=policy.decide([row()]);options=request['engineering_joint']['engineering_options']
    assert options['execution_aware_commitment_preference'] is True
    assert options['execution_aware_start_preference'] is False
    old,_=a.EngineeringPolicy(dict(mode='endurance_restore',execution_aware_start_preference=True),
        'endurance_restore_executable_v14r4').decide([row()])
    assert old['engineering_joint']['engineering_options']['execution_aware_commitment_preference'] is False
    assert old['engineering_joint']['engineering_options']['execution_aware_start_preference'] is True
    for bad in (0,1,None,'true'):
        with pytest.raises(ValueError):a.EngineeringPolicy({'execution_aware_commitment_preference':bad},'bad')


def test_p0_preparation_is_explicit_and_old_R5_default_is_unchanged():
    a=api()
    policy=a.EngineeringPolicy(dict(mode='endurance_restore',execution_aware_commitment_preference=True,
        p0_preparation_before_commitment=True),'endurance_restore_p0prepared_v14r6')
    request,_=policy.decide([row()]);options=request['engineering_joint']['engineering_options']
    assert options['p0_preparation_before_commitment'] is True
    assert options['execution_aware_start_preference'] is False
    old,_=a.EngineeringPolicy(dict(mode='endurance_restore',execution_aware_commitment_preference=True),
        'endurance_restore_continuous_v14r5').decide([row()])
    assert old['engineering_joint']['engineering_options']['p0_preparation_before_commitment'] is False
    for bad in (0,1,None,'true'):
        with pytest.raises(ValueError):a.EngineeringPolicy({'p0_preparation_before_commitment':bad},'bad')
