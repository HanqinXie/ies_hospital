"""Minute clocks and final-hook order, without numerical solves or source admission."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.minute1_20261103.runtime import (
    MinuteActualCoreMixin, MinuteSamplingSpec, validate_minute_scenario,
)


@pytest.mark.parametrize('changes', [
    {'load_interval_minutes': 15.}, {'load_interval_minutes': True},
    {'control_interval_minutes': 15.}, {'control_interval_minutes': 1},
    {'physical_max_segment_minutes': 2.}, {'physical_max_segment_minutes': True},
    {'model_id': 'SF2_old'}, {'contract': 'implicit'},
])
def test_minute_contract_refuses_mixed_or_ambiguous_clocks(changes):
    with pytest.raises(ValueError):
        MinuteSamplingSpec(**changes)


@pytest.mark.parametrize('physical', [1., .5])
def test_physical_refinement_does_not_change_load_or_control_clock(physical):
    spec = MinuteSamplingSpec(physical_max_segment_minutes=physical)
    assert spec.load_interval_minutes == spec.control_interval_minutes == 1.
    assert spec.physical_max_segment_minutes == physical


def test_scenario_scope_check_never_rewrites_the_actual_minute_scene():
    scene = dict(step_minutes=1., duration_steps=180,
        response_delay_minutes=15., model_closure_events=[
            dict(time_minute=60., availability={'GRID': 0.}),
            dict(time_minute=120., availability={'GRID': 1.}),
        ])
    before = deepcopy(scene)
    validate_minute_scenario(scene)
    assert scene == before
    assert scene['response_delay_minutes'] == 15.
    scene['model_closure_events'].append(dict(time_minute=150., availability={'ABS': 0.}))
    with pytest.raises(ValueError, match='one exogenous'):
        validate_minute_scenario(scene)


@pytest.mark.parametrize('field,value', [
    ('step_minutes', 15.), ('step_minutes', 1),
    ('duration_steps', 12), ('duration_steps', 180.),
])
def test_incomplete_or_old_grid_scene_is_rejected(field, value):
    scene = dict(step_minutes=1., duration_steps=180)
    scene[field] = value
    with pytest.raises(ValueError):
        validate_minute_scenario(scene)


class Source:
    def __init__(self):
        self.queries = []
        self.spec = {'schema': 'hospital_ies.ep_minute_source_manifest.v1',
            'files': {name: {'path': 'E:/minute_fixture/'+name, 'sha256': 'a'*64}
                for name in ('loads', 'critical_zones', 'thermal_components', 'metadata')}}
    def at_elapsed(self, minute, start_hour):
        self.queries.append((minute, start_hour))
        return start_hour, SimpleNamespace(minute_of_year=1921,
            timestamp=datetime(2017, 1, 2, 8, 1),
            timestamp_start_standard='2017-01-02 08:00:00',
            timestamp_end_standard='2017-01-02 08:01:00'), {}


class PhysicalReceiver:
    def __init__(self, env, initial_action):
        self.env, self.time, self.logs = env, 0., []
    def log(self, name, **values): self.logs.append((name, values))
    def data_at(self, minute): return ('causal_physical_data', minute)
    def boundary(self): return 'original_local_boundary'
    def observe(self): return {'native_physical_state': True}
    def _after_actual_segment(self, segment, result):
        segment['physical_commit_completed'] = True


class Core(MinuteActualCoreMixin, PhysicalReceiver): pass


def make_core():
    spec, source = MinuteSamplingSpec(), Source()
    prices = json.loads((Path(__file__).resolve().parents[1]/
        'configs/plant_configs.json').read_text(encoding='utf8'))['historical_2001_prices_USD']
    env = SimpleNamespace(minute1_sampling_spec=spec, _minute1_bound_sampling_spec=spec,
        minute_source=source, _minute1_bound_source=source,
        operating_model_config={'minute1_sampling': spec.as_dict()}, dt_minutes=1.,
        scenario={'start_hour': 33}, plant=SimpleNamespace(tariff=prices))
    return Core(env, {}), env


def test_local_source_access_does_not_gain_annual_endpoint_permissions():
    core, _ = make_core()
    assert core.data_at(.25) == ('causal_physical_data', .25)
    assert core.boundary() == 'original_local_boundary'
    with pytest.raises(ValueError, match='not granted'):
        core._annual_absolute_minute(0.)


@pytest.mark.parametrize('damage', ['source', 'spec', 'config', 'dt'])
def test_binding_drift_fails_before_data_or_observation(damage):
    core, env = make_core()
    if damage == 'source': env.minute_source = Source()
    elif damage == 'spec': env.minute1_sampling_spec = MinuteSamplingSpec()
    elif damage == 'config': env.operating_model_config['minute1_sampling']['control_interval_minutes'] = 15.
    else: env.dt_minutes = 15.
    with pytest.raises(ValueError, match='binding changed'): core.data_at(.25)
    with pytest.raises(ValueError, match='binding changed'): core.observe()


def test_fractional_segment_witness_uses_start_source_and_follows_physical_commit(monkeypatch):
    import tools.annual_program.witness as witness
    core, env = make_core()
    seen = []
    def capture(actual_core, segment, result):
        assert segment['physical_commit_completed'] is True
        seen.append((actual_core, deepcopy(segment), result))
    monkeypatch.setattr(witness, 'record_actual_witness', capture)
    segment, result = {'start_minute': .25, 'end_minute': 1.}, object()
    core._after_actual_segment(segment, result)
    assert env.minute_source.queries == [(.25, 33)]
    assert seen[0][0] is core and seen[0][2] is result
    assert seen[0][1]['minute1_sampling']['actual_interval_minutes'] == .75
    assert seen[0][1]['minute1_sampling']['minute_of_year'] == 1921
    context = seen[0][1]['minute1_source_context']
    assert context['files'] == env.minute_source.spec['files']
    assert context['source_interval_start_absolute_minute'] == 1920.
    assert context['actual_interval_end_absolute_minute'] == 1921.
    assert context['source_interval_start_absolute_minute'] <= context['actual_interval_start_absolute_minute']
    context['files']['loads']['sha256'] = 'b'*64
    assert env.minute_source.spec['files']['loads']['sha256'] == 'a'*64


def test_actual_segment_cannot_cross_into_a_later_source_minute(monkeypatch):
    import tools.annual_program.witness as witness
    core, _ = make_core()
    monkeypatch.setattr(witness, 'record_actual_witness', lambda *args: pytest.fail('Invalid source span published'))
    with pytest.raises(ValueError, match='source interval'):
        core._after_actual_segment({'start_minute': .25, 'end_minute': 1.25}, {})


def test_current_observation_does_not_fetch_a_future_source_sample():
    core, env = make_core()
    core.time = 60.
    observation = core.observe()
    assert env.minute_source.queries == []
    assert observation['native_physical_state'] is True
    assert observation['minute1_sampling']['observation_time_minute'] == 60.
    assert observation['minute1_sampling']['available_time_minute'] == 60.
    assert observation['minute1_sampling']['future_samples_exposed'] is False


def clock_segment(monkeypatch, stamp):
    """Call the actual minute hook at an aligned native minute, without solves."""
    import tools.annual_program.witness as witness
    core, env = make_core()
    stamp = datetime.fromisoformat(stamp)
    begin = stamp-timedelta(minutes=1)
    source_minute = int((stamp-datetime(stamp.year, 1, 1)).total_seconds()/60)
    hour = int((source_minute-1)//60)+1
    elapsed = float((source_minute-1)%60)
    env.scenario['start_hour'] = hour
    def at_elapsed(actual, start_hour):
        assert actual == elapsed and start_hour == hour
        return hour, SimpleNamespace(minute_of_year=source_minute,
            timestamp=stamp, timestamp_start_standard=begin.isoformat(sep=' '),
            timestamp_end_standard=stamp.isoformat(sep=' ')), {}
    monkeypatch.setattr(env.minute_source, 'at_elapsed', at_elapsed)
    seen = []
    monkeypatch.setattr(witness, 'record_actual_witness',
        lambda owner, segment, result: seen.append(deepcopy(segment)))
    segment = dict(start_minute=elapsed, end_minute=elapsed+1., hour_of_year=hour,
        action=dict(grid_import_kW=792.9417530284793, gt_fuel_MMBtu_h=0.,
            boiler_steam_lbph=358.9882812030496),
        diesel_local_response=dict(mechanical_work_kWh=0., electrical_work_kWh=0.))
    original_action = deepcopy(segment['action'])
    core._after_actual_segment(segment, object())
    assert segment['action'] == original_action
    assert len(seen) == 1 and seen[0]['physical_commit_completed'] is True
    return segment, seen[0]


@pytest.mark.parametrize('stamp,expected', [
    ('2017-07-31 07:01:00', .05456),
    ('2017-07-31 07:59:00', .05456),
    ('2017-07-31 08:00:00', .07356),
    ('2017-07-31 11:59:00', .07356),
    ('2017-07-31 12:00:00', .09256),
    ('2017-07-31 17:59:00', .09256),
    ('2017-07-31 18:00:00', .07356),
    ('2017-07-31 21:59:00', .07356),
    ('2017-07-31 22:00:00', .05456),
    ('2017-07-30 12:00:00', .05456),
])
def test_actual_minute_tariff_uses_bound_native_timestamp_before_witness(monkeypatch, stamp, expected):
    segment, witnessed = clock_segment(monkeypatch, stamp)
    assert segment['grid_tariff_USD_kWh'] == expected
    assert witnessed['grid_tariff_USD_kWh'] == expected


def test_minute_grid_and_first_preparation_fees_do_not_borrow_hour_end_tariff(monkeypatch):
    from tools.model_review_v30.evaluation import independent_energy_audit
    segment, _ = clock_segment(monkeypatch, '2017-07-31 07:01:00')
    parameters = dict(steam_kWh_per_lb=.30, boiler_eff=.80,
        kWh_per_MMBtu=293.0710702, gas_price_usd_per_mmbtu=4.84,
        dg_shaft_fee_USD_per_kWh=.30)
    result = independent_energy_audit(dict(segments=[segment],
        continuation_start_minute=0., summary=dict(requested_end_minute=1.),
        m21_initial_storage=dict(ups=10., cold_store=0.)),
        accounting_parameters=parameters,
        tariffs_by_hour={5072: .07356})
    assert result['grid_fee_USD2001'] == pytest.approx(.7210483674205638, rel=0., abs=1e-12)
    assert result['observed_operating_fee_USD2001'] == pytest.approx(.7581021629350152, rel=0., abs=1e-12)
    assert result['initial_preparation_fee_USD2001'] == pytest.approx(.5456, rel=0., abs=1e-12)
    assert result['actual_fee_USD2001'] == pytest.approx(1.3037021629350152, rel=0., abs=1e-12)
