"""Registered engineering changes must alter actual inputs, not just labels."""
import copy
import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.operating_rules_study.registration import load_registry
from tools.sci_engineering.design import SEALED_DAYS


def api():
    try:
        return importlib.import_module('tools.engineering_basis_study.registration')
    except ModuleNotFoundError:
        pytest.fail('New engineering-basis registration has not been implemented', pytrace=False)


@pytest.fixture(scope='module')
def registry():
    return load_registry()


def make(registry, **kwargs):
    return api().make_case(registry, 'b07_L4_GT_ER_disturbed_M', 'M_available',
                           start_hour=33, **kwargs)


def test_growth_changes_actual_zones_not_only_display_amplitude(registry):
    a, b = make(registry, demand_amplitude=.25), make(registry, demand_amplitude=.5)
    assert a['id'] != b['id']
    assert a['scenario']['shock_by_zone']
    for zone, carriers in a['scenario']['shock_by_zone'].items():
        for carrier, value in carriers.items():
            assert b['scenario']['shock_by_zone'][zone][carrier] == value * 2
    forged = copy.deepcopy(a)
    forged['scenario']['demand_amplitude'] = .5
    with pytest.raises(ValueError):
        api().validate_case(forged, registry)


def test_fault_duration_changes_separately_from_growth_duration(registry):
    a = make(registry, fault_duration_minutes=15)
    b = make(registry, fault_duration_minutes=180)
    assert a['scenario']['fault_duration_minutes'] == 15
    assert b['scenario']['fault_duration_minutes'] == 180
    assert a['scenario']['shock_duration_minutes'] == b['scenario']['shock_duration_minutes'] == 60
    assert a['scenario']['recovery_start_minute'] == 120
    assert b['scenario']['recovery_start_minute'] == 240
    assert a['scenario']['duration_steps'] == 12
    assert b['scenario']['duration_steps'] == 20


@pytest.mark.parametrize('mode,has_f,has_d', [('N', False, False), ('F', True, False),
                                           ('D', False, True), ('FD', True, True)])
def test_modes_have_only_registered_actual_events(registry, mode, has_f, has_d):
    c = make(registry, mode=mode)
    s = c['scenario']
    assert bool(s['faults'] or s['auxiliary_faults']) is has_f
    assert bool(s['shock_by_zone']) is has_d
    assert s['fault_duration_minutes'] == (60 if has_f else 0)
    assert s['shock_duration_minutes'] == (60 if has_d else 0)
    assert s['demand_amplitude'] == (.25 if has_d else 0)
    # The legacy L4b schema requires this flag, while the actual damage proxy stays off.
    assert s['enable_propagation'] is (mode == 'FD')
    assert c['engineering_basis_study']['endogenous_damage_enabled'] is False
    assert registry['model_closure_v22']['research_stress_proxy'] is False


def test_method_comparison_keeps_complete_c_configuration(registry):
    a = api().make_case(registry, 'b07_L4_GT_ER_disturbed_M', 'C_current', start_hour=33)
    b = api().make_case(registry, 'b07_L4_GT_ER_disturbed_M', 'C_available', start_hour=33)
    assert a['policy'] == b['policy']
    assert a['policy']['alias'] == 'C'
    assert a['policy']['config']['policy_mode'] == 'coordinated'
    assert a['policy']['config']['online_GT_count_order'] == 'after_cost'
    assert a['id'] != b['id']


def test_named_restart_assumption_cannot_change_unrelated_station_fields(registry):
    c = make(registry, boiler_restart_minutes=5)
    resolved = api().worker_registry(c, registry)
    original = registry['model_closure_v22']['station']
    actual = resolved['model_closure_v22']['station']
    assert actual['boiler_restart_minutes'] == 5
    assert {k: v for k, v in actual.items() if k != 'boiler_restart_minutes'} == {
        k: v for k, v in original.items() if k != 'boiler_restart_minutes'}
    assert registry['model_closure_v22']['station']['boiler_restart_minutes'] == 1
    forged = copy.deepcopy(c)
    forged['model_closure_v22'] = {'station': {'control_autonomy_minutes': 999999}}
    with pytest.raises(ValueError):
        api().validate_case(forged, registry)


@pytest.mark.parametrize('mutate', [
    lambda c: c['scenario'].__setitem__('grid_limit_kW', 99999),
    lambda c: c['scenario'].__setitem__('initial_reserve_kWh', 99999),
    lambda c: c['scenario'].__setitem__('automatic_fast_shedding', True),
    lambda c: c['scenario'].__setitem__('original_final12_evaluated', True),
    lambda c: c['policy']['config'].__setitem__('online_GT_count_order', 'before_cost'),
    lambda c: c.__setitem__('id', 'forged'),
    lambda c: c['scenario'].__setitem__('enable_propagation', False),
])
def test_rejects_mutations_of_physics_policy_identity_and_cascade(registry, mutate):
    c = make(registry)
    mutate(c)
    with pytest.raises(ValueError):
        api().validate_case(c, registry)


def test_sealed_and_source_endpoints_are_not_available_for_expansion(registry):
    with pytest.raises(ValueError):
        api().make_case(registry, 'b07_L4_GT_ER_disturbed_M', 'M_current',
                        start_hour=min(SEALED_DAYS)*24+1)
    with pytest.raises(ValueError):
        api().make_case(registry, 'b07_L4_GT_ER_disturbed_M', 'M_current',
                        start_hour=8759, fault_duration_minutes=180)


@pytest.mark.parametrize('kwargs', [dict(demand_amplitude=float('nan')),
                                   dict(demand_amplitude=-.1), dict(demand_amplitude=1.1),
                                   dict(fault_duration_minutes=0),
                                   dict(shock_duration_minutes=-1),
                                   dict(boiler_restart_minutes=1440),
                                   dict(observation_end_minute=75),
                                   dict(observation_end_minute=1441)])
def test_invalid_or_unregistered_parameters_fail_before_running(registry, kwargs):
    with pytest.raises(ValueError):
        make(registry, **kwargs)


def test_mode_n_deduplicates_inactive_nominal_growth(registry):
    a = make(registry, mode='N', demand_amplitude=.25)
    b = make(registry, mode='N', demand_amplitude=.5)
    assert a == b


def test_fractional_phase_keeps_a_complete_recovery_tail(registry):
    c = make(registry, event_start_minute=67.5, observation_end_minute=195)
    assert c['scenario']['fault_start_minute'] == 67.5
    assert c['scenario']['recovery_start_minute'] == 127.5
    assert 195-c['scenario']['recovery_start_minute'] >= 60


def test_changing_actual_target_is_in_full_identity(registry):
    a = make(registry, faults={'GT_1': 0.})
    b = make(registry, faults={'GT_3': 0.})
    assert a['id'] != b['id']
    assert a['engineering_basis_study']['scenario_sha256'] != b['engineering_basis_study']['scenario_sha256']


@pytest.mark.parametrize('method', ['M_cooling_current', 'M_cooling_available'])
def test_cooling_diagnostics_prepare_the_frozen_config_exactly_once(registry, method):
    c = api().make_case(registry, 'b08_L4_GRID_OR_disturbed_M', method,
                        start_hour=1977, boiler_restart_minutes=5)
    assert c['policy']['alias'] == 'M'
    assert c['policy']['config']['cooling_preparation'] is True
    assert api().validate_case(c, registry) == c
    assert api().worker_registry(c, registry)['model_closure_v22']['station']['boiler_restart_minutes'] == 5


@pytest.mark.parametrize('key,value', [('enable_propagation', 1), ('fault_availability', False),
                                      ('original_p0_load_multiplier', True), ('source_day_index', True)])
def test_case_identity_rejects_equal_valued_boolean_numeric_substitution(registry, key, value):
    c = make(registry)
    c['scenario'][key] = value
    with pytest.raises(ValueError):
        api().validate_case(c, registry)
