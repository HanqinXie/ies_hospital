"""Completed service telemetry only; no solver, physical run or raw-file reads."""
import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def api():
    try:
        return importlib.import_module('tools.executed_service.history')
    except ModuleNotFoundError:
        pytest.fail('Completed executed-service observation adapter is missing')


def segment(start, end, *, p2_e=0., p3_c=0., command_id=1):
    loads = [[10., 10., 10.] for _ in range(4)]
    unserved = [[0., 0., 0.] for _ in range(4)]
    unserved[2][0] = p2_e
    unserved[3][2] = p3_c
    return dict(start_minute=start, end_minute=end, command_id=command_id,
                loads_kW=loads, unserved_kW=unserved)


def env_at(t, segments):
    return SimpleNamespace(core=SimpleNamespace(time=t, segments=copy.deepcopy(segments)),
        config=dict(p0_solver_feasibility_tol_kW=.001,
                    p0_unserved_abs_tol_kW=.01, p0_unserved_rel_tol=.001))


def base_at(t):
    return dict(version='p2a.observation.v1', decision_time_min=t,
        observation_time_min=t, history_start_min=0., history_end_min=t,
        features={'time_minute': t}, feature_metadata={
            'time_minute': dict(available_time_min=t, valid_time_min=t)},
        known_command_queue=[], arrived_command_id=None, graph={})


def snapshot(t, segments):
    return api().make_snapshot(env_at(t, segments), base_at(t))


def test_fractional_subsegments_integrate_ens_and_equivalent_minutes():
    a = api()
    parts = [segment(0., 1.5, p2_e=2., p3_c=5.),
             segment(1.5, 4., p2_e=4., command_id=2)]
    h = snapshot(4., parts)['executed_service_history']
    assert h['contract_id'] == a.EXECUTED_SERVICE_HISTORY_CONTRACT_ID
    assert h['groups'] == ['P2', 'P3'] and h['carriers'] == ['e', 'h', 'c']
    assert h['cumulative_ENS_kWh'][0] == pytest.approx([13./60., 0., 0.])
    assert h['cumulative_ENS_kWh'][1] == pytest.approx([0., 0., 7.5/60.])
    assert h['cumulative_equivalent_loss_minutes'][0] == pytest.approx([1.3, 0., 0.])
    assert h['cumulative_equivalent_loss_minutes'][1] == pytest.approx([0., 0., .75])
    assert h['cumulative_material_minutes'] == [[4., 0., 0.], [0., 0., 1.5]]
    assert h['trailing_material_minutes'] == [[4., 0., 0.], [0., 0., 0.]]
    assert h['last_executed_segment'] == dict(start_minute=1.5, end_minute=4.,
        command_id=2, loads_kW=[[10.]*3, [10.]*3], unserved_kW=[[4., 0., 0.], [0.]*3])
    split = [parts[0], segment(1.5, 2.5, p2_e=4.), segment(2.5, 4., p2_e=4.)]
    split_h = snapshot(4., split)['executed_service_history']
    for key in ('cumulative_ENS_kWh', 'cumulative_equivalent_loss_minutes',
                'cumulative_material_minutes', 'trailing_material_minutes'):
        for actual, expected in zip(split_h[key], h[key]):
            assert actual == pytest.approx(expected)


def test_clean_service_ends_the_streak_without_repaying_earlier_loss():
    parts = [segment(0., 4., p2_e=2.)]
    before = snapshot(4., parts)['executed_service_history']
    parts.append(segment(4., 10., command_id=2))
    clean = snapshot(10., parts)['executed_service_history']
    for key in ('cumulative_ENS_kWh', 'cumulative_equivalent_loss_minutes',
                'cumulative_material_minutes'):
        assert clean[key] == before[key]
    assert clean['trailing_material_minutes'] == [[0.]*3, [0.]*3]
    parts.append(segment(10., 12., p2_e=.5, command_id=3))
    again = snapshot(12., parts)['executed_service_history']
    assert again['trailing_material_minutes'][0][0] == 2.
    assert again['cumulative_material_minutes'][0][0] == 6.
    assert again['cumulative_equivalent_loss_minutes'][0][0] == pytest.approx(.9)
    assert again['cumulative_ENS_kWh'][0][0] > before['cumulative_ENS_kWh'][0][0]


def test_existing_material_thresholds_do_not_erase_small_actual_ens():
    s = segment(0., 3., p2_e=.002, p3_c=.002)
    s['loads_kW'][3][2] = 1.
    h = snapshot(3., [s])['executed_service_history']
    assert h['cumulative_ENS_kWh'][0][0] == pytest.approx(.0001)
    assert h['cumulative_equivalent_loss_minutes'][0][0] == pytest.approx(.0006)
    assert h['cumulative_material_minutes'][0][0] == 0.
    assert h['cumulative_material_minutes'][1][2] == 3.
    assert h['material_thresholds'] == dict(solver_tol_kW=.001,
        absolute_kW=.01, relative=.001)


def test_zero_demand_has_zero_equivalent_minutes_without_clamping_raw_ens():
    s = segment(0., 2., p2_e=.1)
    s['loads_kW'][2][0] = 0.
    h = snapshot(2., [s])['executed_service_history']
    assert h['cumulative_equivalent_loss_minutes'][0][0] == 0.
    assert h['cumulative_ENS_kWh'][0][0] == pytest.approx(.2/60.)


def test_equivalent_minutes_and_material_ratio_keep_their_distinct_demand_rules():
    s = segment(0., 2., p2_e=.002)
    s['loads_kW'][2][0] = .0001
    h = snapshot(2., [s])['executed_service_history']
    # q uses every positive demand; the inherited material ratio is used only
    # above the original solver feasibility tolerance (metrics.py:153).
    assert h['cumulative_equivalent_loss_minutes'][0][0] == pytest.approx(40.)
    assert h['cumulative_material_minutes'][0][0] == 0.


def test_t0_is_an_explicit_empty_history():
    h = snapshot(0., [])['executed_service_history']
    assert h['last_executed_segment'] is None
    assert h['history_start_min'] == h['history_end_min'] == 0.
    assert h['available_time_min'] == h['valid_time_min'] == 0.
    for key in ('cumulative_ENS_kWh', 'cumulative_equivalent_loss_minutes',
                'cumulative_material_minutes', 'trailing_material_minutes'):
        assert h[key] == [[0.]*3, [0.]*3]


@pytest.mark.parametrize('t,parts', [
    (4., []), (4., [segment(1., 4.)]),
    (4., [segment(0., 2.), segment(2.5, 4.)]),
    (4., [segment(0., 2.), segment(1.5, 4.)]),
    (4., [segment(0., 5.)]), (4., [segment(0., 4.), segment(4., 5.)]),
    (4., [segment(0., 3.)]), (0., [segment(0., 1.)]),
    (4., [segment(0., float('nan'))]), (4., [segment(0., 0.)]),
])
def test_incomplete_overlap_or_future_ledger_is_rejected(t, parts):
    with pytest.raises(ValueError):
        snapshot(t, parts)


@pytest.mark.parametrize('value', [-.1, float('nan'), float('inf'), True])
def test_invalid_actual_loss_is_rejected_instead_of_silently_clamped(value):
    s = segment(0., 4., p2_e=value)
    with pytest.raises(ValueError):
        snapshot(4., [s])


def test_snapshot_wrapping_is_pure_and_two_call_sites_receive_equal_information():
    a = api()
    env = env_at(4., [segment(0., 4., p2_e=2.)])
    before_env = copy.deepcopy(vars(env))
    base = base_at(4.)
    before = copy.deepcopy(base)
    exporter = a.make_snapshot(env, base)
    policy = a.make_snapshot(env, base_at(4.))
    assert exporter == policy
    assert base == before and vars(env) == before_env
    assert exporter['features'] == base['features']
    assert exporter['feature_metadata'] == base['feature_metadata']
    exporter['executed_service_history']['cumulative_ENS_kWh'][0][0] = 999.
    assert policy['executed_service_history']['cumulative_ENS_kWh'][0][0] != 999.
    assert a.validate_history(policy['executed_service_history'], 4.) == policy['executed_service_history']


@pytest.mark.parametrize('change', ['clock', 'negative', 'missing', 'extra', 'last_future'])
def test_policy_side_validation_rejects_corrupted_history(change):
    a = api()
    h = snapshot(4., [segment(0., 4., p2_e=2.)])['executed_service_history']
    if change == 'clock': h['available_time_min'] = 5.
    elif change == 'negative': h['cumulative_equivalent_loss_minutes'][0][0] = -1.
    elif change == 'missing': del h['cumulative_ENS_kWh']
    elif change == 'extra': h['future_failure_minute'] = 60.
    else: h['last_executed_segment']['end_minute'] = 5.
    with pytest.raises(ValueError):
        a.validate_history(h, 4.)


def test_decision_and_observation_clocks_must_match_completed_execution():
    a = api()
    env = env_at(4., [segment(0., 4.)])
    for base in (base_at(3.), dict(base_at(4.), observation_time_min=5.)):
        with pytest.raises(ValueError):
            a.make_snapshot(env, base)
