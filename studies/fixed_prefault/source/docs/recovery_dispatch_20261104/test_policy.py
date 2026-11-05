"""Focused R1 command-selection contract tests; no plant trajectory is run."""
from copy import deepcopy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def policy_module():
    try:
        return importlib.import_module('tools.recovery_dispatch_20261104.policy')
    except ModuleNotFoundError as exc:
        if exc.name in ('tools.recovery_dispatch_20261104', 'tools.recovery_dispatch_20261104.policy'):
            pytest.fail('Registered R1 recovery policy is not implemented')
        raise


def record(minute, *, tower=1., pump=1., warm=(0., 0., 0.), abs_actual=20.,
           caps=(100., 100., 150.), demand=220.):
    features = dict(availability_TOWER=tower, availability_ABS=1., capacity_ABS=80.)
    for i, capacity in enumerate(caps, 1):
        features[f'availability_EC_{i}'] = 0.5 if i == 2 else 1.
        features[f'capacity_EC_{i}'] = capacity
    for group in range(4):
        features[f'P{group}_c_demand_kW'] = demand / 4.
    metadata = {key: dict(available_time_min=minute, valid_time_min=minute) for key in features}
    return dict(decision_time_min=minute, observation_time_min=minute,
        features=features, feature_metadata=metadata,
        sci_auxiliary=dict(observation_time_minute=minute, available_time_minute=minute,
            availability={'AUX_CHW_PUMP': pump}),
        minute1_sampling=dict(observation_time_minute=minute, available_time_minute=minute),
        cooling_dynamics=dict(elapsed_minutes=minute, cooling_kW=[abs_actual, *warm]),
        actual_supply_telemetry=dict(observation_time_minute=minute, available_time_minute=minute,
            valid_time_minute=minute, interval_end_minute=minute,
            sample_kind='completed_interval_average',
            actual_dispatch={'absorption_cooling_kW': abs_actual}))


def native_dispatch():
    return dict(gt_electric_kW=[0., 0., 0.], grid_import_kW=850., diesel_kW=0.,
        boiler_steam_lbph=3000., absorption_cooling_kW=80.,
        electric_chiller_cooling_kW=[25., 100., 0.], hrsg_steam_lbph=0.,
        tower_rejection_kW=300., gt_fuel_MMBtu_h=0.)


class OriginalController:
    def __init__(self):
        self.request = {'original_request': {'unchanged': [1, 2]}}
        self.diagnostic = {'native_proof': {'unmodified': True}}
        self.calls = []
        self.reset_count = 0

    def decide(self, records):
        self.calls.append(records)
        return self.request, self.diagnostic

    def reset_policy_memory(self):
        self.reset_count += 1


class BaseCore:
    def __init__(self, env):
        self.env = env
        self.time = 0.
        self.failed = False
        self.sequence = 0
        self.pending_commands = []
        self.solver_calls = []
        self.logged = []
        self.native_diagnostic = {'native_solver_proof': {'accepted': True}}

    def _prepare_command_dispatch(self, dispatch, diagnostic, loads):
        self.super_input = (dispatch, diagnostic, loads)
        self.super_output = (dispatch, diagnostic)
        return self.super_output

    def data_at(self, minute):
        import numpy as np
        return 1, SimpleNamespace(), np.asarray([[1., 1., 55.]] * 4), {}

    def readiness_context(self):
        return {'unchanged_native_context': True}

    def delayed_at(self, minute):
        return True, minute + 15.

    def log(self, kind, **values):
        self.logged.append((kind, deepcopy(values)))


class Environment:
    core = None
    engineering_core_type = BaseCore

    def reset(self, *, scenario, seed=None):
        self.core = self.engineering_core_type(self)
        return {'original_reset_return': True}, {'seed': seed}


def attached():
    module = policy_module()
    env = Environment()
    original = OriginalController()
    controller = module.attach_recovery_policy(env, original)
    env.reset(scenario={}, seed=123)
    return module, env, original, controller


def activate(controller, *, device='TOWER', times=(23., 91.)):
    first = record(times[0], tower=0. if device == 'TOWER' else 1.,
        pump=0. if device == 'AUX_CHW_PUMP' else 1.)
    second = record(times[1], tower=0.25 if device == 'TOWER' else 1.,
        pump=0.25 if device == 'AUX_CHW_PUMP' else 1., warm=(70., 2., 0.))
    controller.decide([first])
    controller.decide([first, second])
    return second


def test_original_requests_diagnostics_and_inactive_hook_are_exact():
    _, env, original, controller = attached()
    records = [record(0.), record(7.)]
    before = deepcopy(records)
    request, diagnostic = controller.decide(records)
    assert request is original.request
    assert diagnostic is original.diagnostic
    assert original.calls[-1] is records
    assert records == before
    dispatch = native_dispatch()
    proof = {'raw': [1, 2, 3]}
    result, trace = env.core._prepare_command_dispatch(dispatch, proof, None)
    assert result is dispatch
    assert trace is proof
    assert env.core.super_output[0] is dispatch
    assert env.core.super_output[1] is proof
    assert not controller.monitor.active


@pytest.mark.parametrize('device', ['TOWER', 'AUX_CHW_PUMP'])
def test_repair_requires_observed_zero_then_positive_and_not_a_fixed_clock(device):
    _, env, original, controller = attached()
    controller.decide([record(120.)])
    assert not controller.monitor.active
    controller.reset_policy_memory()
    recovered = activate(controller, device=device, times=(211., 247.))
    assert controller.monitor.active
    assert controller.monitor.activated_minute == recovered['decision_time_min']
    assert controller.monitor.recovered_supports == [device]
    assert original.calls[-1][-1] is recovered
    env.core.time = 247.
    _, trace = env.core._prepare_command_dispatch(native_dispatch(), {}, None)
    assert trace['recovery_dispatch_selection']['activation_minute'] == 247.


@pytest.mark.parametrize('future_scope', ['feature', 'auxiliary', 'telemetry', 'dynamic'])
def test_future_or_mismatched_observation_clocks_are_rejected(future_scope):
    _, _, _, controller = attached()
    current = record(23.)
    if future_scope == 'feature':
        current['feature_metadata']['capacity_EC_1']['available_time_min'] = 24.
    elif future_scope == 'auxiliary':
        current['sci_auxiliary']['available_time_minute'] = 24.
    elif future_scope == 'telemetry':
        current['actual_supply_telemetry']['valid_time_minute'] = 24.
    else:
        current['cooling_dynamics']['elapsed_minutes'] = 24.
    with pytest.raises(ValueError, match='clock|future|current'):
        controller.decide([current])


def test_active_warm_first_permissions_cover_current_cold_and_preserve_native_fields():
    _, env, original, controller = attached()
    latest = activate(controller)
    env.core.time = latest['decision_time_min']
    dispatch = native_dispatch()
    proof = {'raw_native_primal': [9., 8.], 'status': 'accepted'}
    raw_dispatch, raw_proof = deepcopy(dispatch), deepcopy(proof)
    result, trace = env.core._prepare_command_dispatch(dispatch, proof, None)
    # Current ABS coverage is 20, not the native permission80: EC must cover200.
    assert result['electric_chiller_cooling_kW'] == pytest.approx([100., 100., 0.])
    assert dispatch == raw_dispatch
    assert proof == raw_proof
    assert {k: v for k, v in result.items() if k != 'electric_chiller_cooling_kW'} == {
        k: v for k, v in raw_dispatch.items() if k != 'electric_chiller_cooling_kW'}
    receipt = trace['recovery_dispatch_selection']
    assert trace['raw_native_primal'] == raw_proof['raw_native_primal']
    assert receipt['native_raw_dispatch'] == raw_dispatch
    assert receipt['native_prepared_dispatch'] == raw_dispatch
    assert receipt['transformed_dispatch'] == result
    assert receipt['current_cold_demand_kW'] == 220.
    assert receipt['observed_abs_coverage_kW'] == 20.
    assert receipt['native_optimizer_proof_covers_transformed_command'] is False
    assert original.request == {'original_request': {'unchanged': [1, 2]}}


def test_effective_device_capacity_is_not_derated_twice_or_replaced_by_nominal_capacity():
    _, env, _, controller = attached()
    latest = activate(controller)
    latest['features'].update(capacity_EC_1=40., capacity_EC_2=60., capacity_EC_3=0.)
    latest['features']['P3_c_demand_kW'] = 500.
    # A fresh current record, with EC2 effective capacity60 already reflecting availability.5.
    latest['decision_time_min'] = 92.
    latest['observation_time_min'] = 92.
    for metadata in latest['feature_metadata'].values():
        metadata.update(available_time_min=92., valid_time_min=92.)
    for scope in ('sci_auxiliary', 'minute1_sampling', 'actual_supply_telemetry'):
        latest[scope].update(observation_time_minute=92., available_time_minute=92.)
    latest['actual_supply_telemetry'].update(valid_time_minute=92., interval_end_minute=92.)
    latest['cooling_dynamics']['elapsed_minutes'] = 92.
    controller.decide([latest])
    env.core.time = 92.
    dispatch = native_dispatch()
    dispatch['electric_chiller_cooling_kW'] = [20., 20., 0.]
    result, trace = env.core._prepare_command_dispatch(dispatch, {}, None)
    assert result['electric_chiller_cooling_kW'] == [40., 60., 0.]
    assert trace['recovery_dispatch_selection']['uncovered_permission_request_kW'] > 0.


def test_native_ec_total_is_preserved_when_larger_than_current_residual_demand():
    _, env, _, controller = attached()
    activate(controller)
    env.core.time = 91.
    dispatch = native_dispatch()
    dispatch['electric_chiller_cooling_kW'] = [100., 100., 100.]
    result, trace = env.core._prepare_command_dispatch(dispatch, {}, None)
    assert sum(result['electric_chiller_cooling_kW']) == 300.
    assert trace['recovery_dispatch_selection']['requested_ec_envelope_kW'] == 300.


@pytest.mark.parametrize('overflow', [1e-9, 1.])
def test_impossible_native_total_is_explicit_instead_of_silently_reduced(overflow):
    module = policy_module()
    dispatch = native_dispatch()
    dispatch['electric_chiller_cooling_kW'] = [100., 100., 150. + overflow]
    context = module.current_observation(record(91.))
    with pytest.raises(ValueError, match='exceeds current effective capacities'):
        module.select_ec_permissions(dispatch, context)


@pytest.mark.parametrize('stale_scope', ['support_feature', 'capacity_feature', 'auxiliary'])
def test_stale_support_and_capacity_values_cannot_trigger_current_recovery(stale_scope):
    _, _, _, controller = attached()
    controller.decide([record(90., tower=0., pump=0.)])
    current = record(91.)
    if stale_scope == 'auxiliary':
        current['sci_auxiliary']['observation_time_minute'] = 89.
    else:
        name = 'availability_TOWER' if stale_scope == 'support_feature' else 'capacity_EC_2'
        current['feature_metadata'][name]['valid_time_min'] = 89.
    with pytest.raises(ValueError, match='current|stale'):
        controller.decide([current])


def test_native_queue_and_cut_decisions_remain_unchanged_and_reset_clears_recovery():
    from tools.sci_engineering.environment import EngineeringCore
    _, env, original, controller = attached()
    activate(controller)
    env.core.time = 91.
    cuts = [[0., 0., 0.] for _ in range(4)]
    cuts[3][0] = 0.25
    request = {'ordinary_dispatch': dict(dispatch=native_dispatch(), disconnected_kW=cuts,
        rule_diagnostic={'native_only': True})}
    command = EngineeringCore.generate_command(env.core, request)
    assert command['generated_minute'] == 91.
    assert command['arrival_minute'] == 106.
    assert command['request'] == request
    assert command['disconnected'] == cuts
    assert env.core.pending_commands == [command]
    assert env.core.logged[-1][0] == 'command_generated'
    before_resets = original.reset_count
    returned = env.reset(scenario={}, seed=44)
    assert returned == ({'original_reset_return': True}, {'seed': 44})
    assert original.reset_count == before_resets + 1
    assert not controller.monitor.active
    controller.decide([record(300.)])
    assert not controller.monitor.active
