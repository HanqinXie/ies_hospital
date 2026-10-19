"""The single frozen allowance recipe over lawful completed-service snapshots."""
import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tools.executed_service.history import make_snapshot


def api():
    try:
        return importlib.import_module('tools.executed_service.policy')
    except ModuleNotFoundError:
        pytest.fail('Executed-service allowance policy is missing')


def segment(start, end, loss=None):
    return dict(start_minute=start, end_minute=end, command_id=1,
        loads_kW=[[10.]*3 for _ in range(4)],
        unserved_kW=[[0.]*3, [0.]*3] + copy.deepcopy(loss or [[0.]*3, [0.]*3]))


def row(t=0., segments=None):
    features = dict(grid_import_limit_kW=350., arrived_grid_import_limit_kW=350.,
        grid_import_prev_kW=120., diesel_prev_kW=0., diesel_energy_kWh=2400.,
        diesel_status='OFF', topology_mode='legacy_explicit_permission',
        availability_GRID=1., control_available=True)
    for i in (1, 2, 3):
        features.update({f'pre_GT_{i}_kW':120. if i < 3 else 0.,
            f'state_GT_{i}':'RUNNING' if i < 3 else 'OFF',
            f'overload_minutes_GT_{i}':0.})
    features.update({f'{g}_{c}_demand_kW':10. for g in ('P0','P1','P2','P3') for c in ('e','h','c')})
    features['zone_margin_RADIOLOGY_FLR_4'] = 1.
    base = dict(decision_time_min=t, observation_time_min=t, features=features,
        feature_metadata={k:dict(available_time_min=t, valid_time_min=t) for k in features},
        known_command_queue=[], arrived_command_id=None)
    parts = segments if segments is not None else [] if t == 0. else [segment(0., t)]
    env = SimpleNamespace(core=SimpleNamespace(time=t, segments=parts), config=dict(
        p0_solver_feasibility_tol_kW=.001, p0_unserved_abs_tol_kW=.01, p0_unserved_rel_tol=.001))
    return make_snapshot(env, base)


def test_zero_observed_loss_exactly_preserves_old_fixed_request():
    a = api()
    from tools.coordinated_dispatch.policy import CoordinatedDispatchPolicy
    records = [row(), row(15.)]
    fixed = CoordinatedDispatchPolicy({'coordination_mode':'fixed'}, 'old')
    policy = a.ExecutedServicePolicy({}, 'feedback')
    request, diagnostic = policy.decide(records)
    assert request == fixed.decide(records)[0]
    assert diagnostic['base_service_budget_fractions'] == [.05, .20]
    assert diagnostic['effective_service_budget_fractions'] == [.05, .20]
    assert diagnostic['feedback_saturation_equivalent_minutes'] == [6., 24.]
    assert diagnostic['executed_service_feedback_contract_id'] == a.EXECUTED_SERVICE_POLICY_CONTRACT_ID
    assert diagnostic['coordination_mode'] == 'fixed'
    assert diagnostic['coordination_preference'] == 'standby_first'
    assert not hasattr(policy, 'bind_current')


def test_each_group_uses_its_largest_carrier_loss_and_only_changes_allowance():
    a = api()
    r = row(30., [segment(0., 30., [[1./3., 1., 2./3.], [2., 4., 1.]])])
    policy = a.ExecutedServicePolicy({}, 'feedback')
    request, diagnostic = policy.decide([r])
    assert diagnostic['groupmax_equivalent_loss_minutes'] == pytest.approx([3., 12.])
    assert diagnostic['effective_service_budget_fractions'] == pytest.approx([.025, .1])
    assert request['coordinated_joint']['options']['service_budget_fractions'] == pytest.approx([.025, .1])
    from tools.coordinated_dispatch.policy import CoordinatedDispatchPolicy
    original, _ = CoordinatedDispatchPolicy({'coordination_mode':'fixed'}, 'old').decide([r])
    actual_options = copy.deepcopy(request['coordinated_joint']['options'])
    actual_options['service_budget_fractions'] = [.05, .20]
    assert actual_options == original['coordinated_joint']['options']
    assert request['coordinated_joint']['risk_loads_kW'] == original['coordinated_joint']['risk_loads_kW']
    assert diagnostic['requested_joint_options'] == request['coordinated_joint']['options']
    assert diagnostic['cumulative_ENS_kWh'] == r['executed_service_history']['cumulative_ENS_kWh']


def test_allowance_saturates_at_zero_without_changing_physical_capability():
    a = api()
    r = row(60., [segment(0., 60., [[1., 0., 0.], [0., 5., 0.]])])
    request, info = a.ExecutedServicePolicy({}, 'feedback').decide([r])
    assert info['groupmax_equivalent_loss_minutes'] == [6., 30.]
    assert info['effective_service_budget_fractions'] == [0., 0.]
    assert request['coordinated_joint']['options']['critical_reserve_multiplier'] == 1.25
    assert request['coordinated_joint']['options']['preserve_one_gt_standby'] is True
    assert request['coordinated_joint']['options']['handoff_enabled'] is True
    assert info['executed_service_feedback_is_service_guarantee'] is False


def test_recovery_ends_a_streak_but_never_restores_budget():
    a = api()
    affected = segment(0., 15., [[1., 0., 0.], [0.]*3])
    before = row(15., [affected])
    recovered = row(30., [affected, segment(15., 30.)])
    policy = a.ExecutedServicePolicy({}, 'feedback')
    first = policy.decide([before])[1]
    after = policy.decide([before, recovered])[1]
    assert recovered['executed_service_history']['trailing_material_minutes'][0][0] == 0.
    assert first['effective_service_budget_fractions'] == pytest.approx([.0375, .2])
    assert after['effective_service_budget_fractions'] == first['effective_service_budget_fractions']


@pytest.mark.parametrize('field', ['cumulative_ENS_kWh',
    'cumulative_equivalent_loss_minutes', 'cumulative_material_minutes'])
def test_backwards_cumulative_ledger_is_rejected(field):
    a = api()
    affected = segment(0., 15., [[1., 0., 0.], [0.]*3])
    before = row(15., [affected])
    after = row(30., [affected, segment(15., 30.)])
    after['executed_service_history'][field][0][0] /= 2.
    with pytest.raises(ValueError):
        a.ExecutedServicePolicy({}, 'feedback').decide([before, after])


@pytest.mark.parametrize('change', ['missing', 'future_clock', 'extra_field'])
def test_missing_or_corrupt_service_observation_is_not_zero_filled(change):
    a = api()
    r = row(15.)
    if change == 'missing': del r['executed_service_history']
    elif change == 'future_clock': r['executed_service_history']['valid_time_min'] = 16.
    else: r['executed_service_history']['future_event'] = 30.
    with pytest.raises(ValueError):
        a.ExecutedServicePolicy({}, 'feedback').decide([r])


def test_inputs_config_and_returned_diagnostics_do_not_alias():
    a = api()
    config = {'coordination_mode':'fixed', 'budget_feedback':False}
    records = [row(15., [segment(0., 15., [[1., 0., 0.], [0.]*3])])]
    before_config = copy.deepcopy(config)
    before_records = copy.deepcopy(records)
    policy = a.ExecutedServicePolicy(config, 'feedback')
    request, info = policy.decide(records)
    assert config == before_config and records == before_records
    assert policy.config['service_budget_fractions'] == [.05, .2]
    request['coordinated_joint']['options']['service_budget_fractions'][0] = 999.
    info['cumulative_equivalent_loss_minutes'][0][0] = 999.
    assert records == before_records and policy.config['service_budget_fractions'] == [.05, .2]


def test_hidden_outcome_rejection_precedes_service_history_access_and_raw_is_ignored():
    a = api()
    policy = a.ExecutedServicePolicy({}, 'feedback')
    class Poison:
        def __deepcopy__(self, memo): raise AssertionError('Hidden data was copied')
        def __getattr__(self, name): raise AssertionError('Hidden data was read')
    r = row(15.)
    expected = policy.decide([r])
    r.update(raw=Poison(), env=Poison(), scenario=Poison(), future_events=Poison())
    assert policy.decide([r]) == expected
    r['post_outcome'] = {'failure':True}
    r['executed_service_history'] = Poison()
    with pytest.raises(ValueError):
        policy.decide([r])


@pytest.mark.parametrize('config', [
    {'budget_feedback':True}, {'budget_feedback':0}, {'coordination_mode':'state'},
    {'fixed_preference':'bridge_first'}, {'support_hours':1.},
    {'service_budget_fractions':[.1, .2]}, {'service_budget_enabled':False},
])
def test_only_the_preregistered_fixed_recipe_is_accepted(config):
    with pytest.raises(ValueError):
        api().ExecutedServicePolicy(config, 'invalid')
