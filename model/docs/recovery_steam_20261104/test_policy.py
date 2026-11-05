"""R2-specific contracts; reuse the unchanged R1 fixture and real native queue."""
from copy import deepcopy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from docs.recovery_dispatch_20261104.test_policy import Environment, OriginalController, record, native_dispatch
from tools.recovery_dispatch_20261104 import policy as r1


def module():
    try:
        return importlib.import_module('tools.recovery_steam_20261104.policy')
    except ModuleNotFoundError as exc:
        if exc.name in ('tools.recovery_steam_20261104', 'tools.recovery_steam_20261104.policy'):
            pytest.fail('Registered R2 recovery steam selection is not implemented')
        raise


def current(minute, *, tower=1., pump=1., abs_actual=0., hrsg=2325.00604680056,
            boiler=0., heat=(95.5333634968101, 28.185488494015956, 0., 38.85438813954836), cold=1000.):
    value = record(minute, tower=tower, pump=pump, warm=(70., 2., 0.), abs_actual=abs_actual, demand=cold)
    value['features']['capacity_ABS'] = 700.
    for group, demand in enumerate(heat):
        name = f'P{group}_h_demand_kW'
        value['features'][name] = demand
        value['feature_metadata'][name] = dict(available_time_min=minute, valid_time_min=minute)
    value['actual_supply_telemetry']['interval_start_minute'] = max(0., minute - .25)
    value['actual_supply_telemetry']['actual_dispatch'].update(hrsg_steam_lbph=hrsg, boiler_steam_lbph=boiler)
    return value


def dispatch():
    value = native_dispatch()
    value['absorption_cooling_kW'] = 700.
    value['electric_chiller_cooling_kW'] = [100., 100., 150.]
    return value


def attached(*, r1_only=False):
    env, base = Environment(), OriginalController()
    env.plant = SimpleNamespace(steam_kWh_per_lb=.3, exchanger_eff=.95, abs_steam_lb_per_rt=19.5)
    controller = (r1.attach_recovery_policy(env, base) if r1_only else
        module().attach_recovery_policy(env, base, config=module().R2_CONFIG))
    env.reset(scenario={}, seed=9)
    return env, base, controller


def recover(controller, *, support='TOWER', latest=None):
    first = current(11., tower=0. if support == 'TOWER' else 1.,
        pump=0. if support == 'AUX_CHW_PUMP' else 1.)
    latest = current(47.) if latest is None else latest
    controller.decide([first])
    result = controller.decide([first, latest])
    return latest, result


def test_abs_permission_uses_actual_steam_after_all_current_heat_and_does_not_latch_zero():
    env, base, controller = attached()
    observed, (request, diagnostic) = recover(controller)
    env.core.time = 47.
    original = dispatch()
    result, trace = env.core._prepare_command_dispatch(original, {'native_proof': [1, 2]}, None)
    heat = sum(observed['features'][f'P{g}_h_demand_kW'] for g in range(4))
    expected = (2325.00604680056 - heat / (.3 * .95)) * 3.51685284 / 19.5
    assert result['absorption_cooling_kW'] == pytest.approx(expected)
    assert 300. < expected < 320.
    assert observed['actual_supply_telemetry']['actual_dispatch']['absorption_cooling_kW'] == 0.
    assert observed['actual_supply_telemetry']['actual_dispatch']['boiler_steam_lbph'] == 0.
    assert original['boiler_steam_lbph'] == 3000.  # A permission must not create steam credit.
    receipt = trace['recovery_steam_selection']
    assert receipt['current_heat_reserved_steam_lbph'] == pytest.approx(heat / .285)
    assert receipt['observed_actual_boiler_steam_lbph'] == 0.
    assert receipt['native_optimizer_proof_covers_transformed_command'] is False
    assert request is base.request
    assert diagnostic is base.diagnostic


def test_more_current_p0_heat_reduces_abs_budget_and_real_ec_caps_still_bind():
    env, _, controller = attached()
    observed = current(47., heat=(124.0333634968101, 28.185488494015956, 0., 38.85438813954836))
    recover(controller, latest=observed)
    env.core.time = 47.
    original = dispatch()
    before = deepcopy(original)
    result, trace = env.core._prepare_command_dispatch(original, {}, None)
    heat = sum(observed['features'][f'P{g}_h_demand_kW'] for g in range(4))
    expected = (2325.00604680056 - heat / .285) * 3.51685284 / 19.5
    assert result['absorption_cooling_kW'] == pytest.approx(expected)
    assert result['electric_chiller_cooling_kW'] == [100., 100., 150.]
    assert sum(result['electric_chiller_cooling_kW']) == 350.
    assert original == before
    assert {k: v for k, v in result.items() if k not in (
        'absorption_cooling_kW', 'electric_chiller_cooling_kW')} == {
        k: v for k, v in before.items() if k not in (
            'absorption_cooling_kW', 'electric_chiller_cooling_kW')}
    receipt = trace['recovery_steam_selection']
    assert receipt['r1_dispatch_before_steam_selection']['absorption_cooling_kW'] == 700.
    assert receipt['r2_dispatch_after_steam_selection'] == result
    assert receipt['ec_uncovered_permission_request_kW'] > 0.


def test_pump_only_recovery_is_exactly_unchanged_r1_and_requires_no_steam_fields():
    env, base, controller = attached()
    env1, _, controller1 = attached(r1_only=True)
    observed = current(47.)
    observed['actual_supply_telemetry']['actual_dispatch'].pop('hrsg_steam_lbph')
    recover(controller, support='AUX_CHW_PUMP', latest=deepcopy(observed))
    recover(controller1, support='AUX_CHW_PUMP', latest=deepcopy(observed))
    env.core.time = env1.core.time = 47.
    value = dispatch()
    result, trace = env.core._prepare_command_dispatch(deepcopy(value), {'native': True}, None)
    result1, trace1 = env1.core._prepare_command_dispatch(deepcopy(value), {'native': True}, None)
    assert result == result1
    assert trace == trace1
    assert 'recovery_steam_selection' not in trace
    assert base.request == {'original_request': {'unchanged': [1, 2]}}


@pytest.mark.parametrize('bad_source', ['missing_actual_hrsg', 'stale_actual_interval'])
def test_tower_recovery_refuses_missing_or_stale_actual_steam_source(bad_source):
    _, _, controller = attached()
    latest = current(47.)
    if bad_source == 'missing_actual_hrsg':
        latest['actual_supply_telemetry']['actual_dispatch'].pop('hrsg_steam_lbph')
    else:
        latest['actual_supply_telemetry'].update(valid_time_minute=46., interval_end_minute=46.)
    with pytest.raises(ValueError, match='actual|fresh|steam'):
        recover(controller, latest=latest)


def test_steam_hook_precedes_original_queue_and_outage_or_reset_suspends_it():
    from tools.sci_engineering.environment import EngineeringCore
    env, _, controller = attached()
    recover(controller)
    env.core.time = 47.
    cuts = [[0., 0., 0.] for _ in range(4)]
    cuts[3][0] = .25
    request = {'ordinary_dispatch': dict(dispatch=dispatch(), disconnected_kW=cuts,
        rule_diagnostic={'original': True})}
    command = EngineeringCore.generate_command(env.core, request)
    assert command['generated_minute'] == 47.
    assert command['arrival_minute'] == 62.
    assert command['disconnected'] == cuts
    assert command['request'] == request
    assert command['dispatch']['absorption_cooling_kW'] < 320.
    assert env.core.pending_commands == [command]
    assert 'recovery_steam_selection' in command['joint_dispatch']['diagnostic']
    controller.decide([current(48., tower=0.)])
    env.core.time = 48.
    returned, trace = env.core._prepare_command_dispatch(dispatch(), {}, None)
    assert returned == dispatch()
    assert trace == {}
    env.reset(scenario={}, seed=10)
    assert controller.steam_observation is None
    assert not controller.r1_controller.monitor.active
