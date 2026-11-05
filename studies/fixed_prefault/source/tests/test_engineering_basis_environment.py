"""Both planners must receive the same current-observation adjustment."""
import copy
import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.operating_rules_study.registration import load_registry
from tools.model_closeout_v31.rules import observe_boiler_capacity


def api():
    try:
        return importlib.import_module('tools.engineering_basis_study.environment')
    except ModuleNotFoundError:
        pytest.fail('New engineering-basis adapter has not been implemented', pytrace=False)


@pytest.mark.parametrize('pair,alias', [(('M_current', 'M_available'), 'M'),
                                      (('C_current', 'C_available'), 'C')])
def test_same_planner_configuration_for_each_range_pair(pair, alias):
    reg = load_registry()
    old = next(c for c in reg['cases'] if c['id'] == 'b07_L4_GT_ER_disturbed_'+alias)
    left = api().method_config(old['policy']['config'], pair[0])
    right = api().method_config(old['policy']['config'], pair[1])
    assert left == right == old['policy']['config']
    assert left is not old['policy']['config']


@pytest.mark.parametrize('method', ['M_available', 'C_available', 'M_cooling_available'])
def test_same_observed_capacity_rule_and_no_forced_output(method):
    record = dict(decision_time_min=45., features={f'availability_BOILER_{i}': a for i, a in [(1, 1.), (2, .5), (3, 0.)]},
                  feature_metadata={f'availability_BOILER_{i}': dict(available_time_min=45., valid_time_min=45.) for i in (1, 2, 3)})
    receipt = observe_boiler_capacity(record, 3000.)
    dispatch = dict(boiler_steam_lbph=200., gt_electric_kW=[1., 2., 3.], untouched='yes')
    before = copy.deepcopy(dispatch)
    result, diagnostic = api().prepare_available_range(method, dispatch, {}, receipt, 45.)
    assert dispatch == before
    assert result['boiler_steam_lbph'] == 1500.
    assert {k:v for k,v in result.items() if k != 'boiler_steam_lbph'} == {
        k:v for k,v in before.items() if k != 'boiler_steam_lbph'}
    assert diagnostic['engineering_available_range']['actual_output_is_forced'] is False
    assert diagnostic['engineering_available_range']['queue_bypassed'] is False
    assert diagnostic['v31_steam_permission']['method'] == method
    assert diagnostic['v31_steam_permission']['rule_origin'] == 'V31.current_observed_healthy_boiler_ceiling'


def test_current_methods_do_not_sneak_in_a_new_range():
    for method in ('M_current', 'C_current', 'M_cooling_current'):
        result, diagnostic = api().prepare_available_range(method, {'boiler_steam_lbph': 12.}, {}, None, 45.)
        assert result == {'boiler_steam_lbph': 12.}
        assert diagnostic['engineering_available_range']['enabled'] is False


def test_stale_or_future_observation_cannot_authorize_range():
    receipt = dict(decision_time_minute=60., boiler_total_rating_lbph=3000., healthy_capacity_lbph=3000.)
    with pytest.raises(ValueError):
        api().prepare_available_range('C_available', {'boiler_steam_lbph': 200.}, {}, receipt, 45.)


def test_available_rule_is_independent_of_future_scenario():
    # Its interface accepts only a bound current receipt, not a scene/event script.
    receipt = dict(decision_time_minute=45., boiler_total_rating_lbph=3000., healthy_capacity_lbph=3000.)
    a = api().prepare_available_range('C_available', {'boiler_steam_lbph': 200.}, {}, receipt, 45.)
    b = api().prepare_available_range('C_available', {'boiler_steam_lbph': 200.}, {}, copy.deepcopy(receipt), 45.)
    assert a == b
    with pytest.raises(TypeError):
        api().prepare_available_range('C_available', {'boiler_steam_lbph': 200.}, {}, receipt, 45., scenario={})
