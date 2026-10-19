"""Independent v15 policy identity and current-input boundaries."""
import copy
import importlib
import pytest
from test_coordinated_dispatch_policy import row


def api():
    try: return importlib.import_module('tools.operating_response.policy')
    except ModuleNotFoundError: pytest.fail('The separate v15 policy is missing')


def test_default_policy_uses_separate_action_schema_and_documented_K1_profile():
    p = api().OperatingPolicy({}, 'readiness_handoff_v15_K1')
    request, info = p.decide([row()])
    assert set(request) == {'operating_joint'}
    assert request['operating_joint']['operating_options']['preparation_profile'] == 'K1'
    assert request['operating_joint']['operating_options']['phase_constraints_enabled'] is True
    assert info['future_rollouts_online'] == 0
    assert info['policy_id'] == 'readiness_handoff_v15_K1'


def test_changing_scenario_day_root_labels_cannot_change_online_action():
    p = api().OperatingPolicy({'preparation_profile':'K2'}, 'readiness_handoff_v15_K2')
    a = row(); a.update(scenario_id='one', root_id='a', source_day_index=51)
    before = copy.deepcopy(a); request, _ = p.decide([a]); assert a == before
    a.update(scenario_id='different', root_id='b', source_day_index=212)
    assert p.decide([a])[0] == request


@pytest.mark.parametrize('bad', [dict(future_events=[]), dict(preparation_profile='1.925'),
    dict(phase_constraints_enabled=1), dict(scenario_id='one')])
def test_unknown_or_invalid_configuration_is_rejected(bad):
    with pytest.raises(ValueError): api().OperatingPolicy(bad, 'invalid')


def test_future_features_and_outcomes_are_rejected():
    p = api().OperatingPolicy({}, 'v15'); a = row()
    a['feature_metadata']['availability_GRID']['available_time_min'] = 15.
    with pytest.raises(ValueError): p.decide([a])
    a = row(); a['labels'] = {'30':1}
    with pytest.raises(ValueError): p.decide([a])


def test_phase_disabled_ablation_is_explicit_and_does_not_relabel_E6():
    request, _ = api().OperatingPolicy({'phase_constraints_enabled':False}, 'v15_no_phase').decide([row()])
    assert request['operating_joint']['operating_options']['phase_constraints_enabled'] is False
    with pytest.raises(ValueError): api().validate_action({'engineering_joint':{}})

