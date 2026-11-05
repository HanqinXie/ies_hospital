"""Canonical continuous cases retain frozen physics and complete source support."""
import copy
import importlib

import pytest

from tools.operating_rules_study.registration import load_registry
from tools.engineering_basis_study.registration import make_case as old_case
from tools.sci_engineering.design import source_support


SOURCE = 'b07_L4_GT_ER_disturbed_M'
GRID_SOURCE = 'b08_L4_GRID_OR_disturbed_M'


def api():
    try:
        return importlib.import_module('tools.annual_readiness.registration')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_readiness.registration':
            raise
        pytest.fail('The canonical annual-readiness registration is missing', pytrace=False)


@pytest.fixture(scope='module')
def registry():
    return load_registry()


def make(registry, method='M_ready', **kwargs):
    values=dict(start_hour=33)
    values.update(kwargs)
    return api().make_case(registry, SOURCE, method, **values)


def test_short_default_case_has_new_identity_and_original_scenario_physics(registry):
    c=make(registry, 'M_available')
    old=old_case(registry, SOURCE, 'M_available', start_hour=33)
    assert c['id'].startswith('arp_') and c['id'] != old['id']
    assert c['scenario']['id'] == c['id']
    assert 'engineering_basis_study' not in c
    assert c['policy'] == old['policy']
    assert c['configuration'] == old['configuration']
    assert c['operating_rule_id'] == old['operating_rule_id']
    for key in ('faults','shock_by_zone','fault_start_minute','fault_duration_minutes',
                'shock_start_minute','shock_duration_minutes','initial_reserve_kWh',
                'automatic_fast_shedding','grid_limit_kW','pre_event_warmup_steps'):
        assert c['scenario'][key] == old['scenario'][key]
    meta=c['annual_readiness']
    assert meta['qualification_is_functional_not_DC_energy'] is True
    assert meta['original_final12_untouched'] is True
    assert meta['clock_contract'] == 'event_triggered'
    assert meta['parameters']['requalification_minutes'] is None
    assert api().validate_case(c, registry) == c


@pytest.mark.parametrize('method,underlying', [('M_ready','M_available'),('C_ready','C_available'),
    ('M_available','M_available'),('C_available','C_available'),('M_cooling_available','M_cooling_available')])
def test_complete_frozen_planner_config_is_preserved_for_every_named_method(registry, method, underlying):
    c=make(registry, method)
    old=old_case(registry, SOURCE, underlying, start_hour=33)
    assert c['policy'] == old['policy']
    assert c['policy']['config']['online_GT_count_order'] == 'after_cost'
    if method.startswith('C_'):
        assert c['policy']['alias'] == 'C'
        assert c['policy']['config']['policy_mode'] == 'coordinated'
    assert c['operating_rule_id'] == ('sustained_critical_60min' if method.endswith('_ready') else old['operating_rule_id'])


def test_seven_day_normal_episode_retains_endpoint_lookahead_and_no_midnight_reset(registry):
    c=make(registry, mode='N', duration_minutes=10080.)
    s=c['scenario'];support=source_support(s)
    assert s['duration_steps'] == 672
    assert s['step_minutes'] == 15.
    assert support == {'raw_start_minute': 1920., 'raw_end_minute': 12060.}
    assert s['full_source_support'] == support
    assert s['source_calendar_days'] == list(range(1,9))
    assert s['normal_verification'] is True
    assert s['canonical_class'] == 'N'
    assert s['model_closure_events'] == []
    assert s['source_block_id'].startswith('annual_readiness_')
    assert s['new_independent_background'] is False
    assert c['annual_readiness']['case_kind'] == 'normal'


def test_grid_source_and_unchanged_h2_profile_are_explicit(registry):
    c=api().make_case(registry, GRID_SOURCE, 'M_cooling_available', start_hour=1977,
        configuration='H2', boiler_restart_minutes=5.)
    assert c['configuration'] == 'H2'
    assert c['scenario']['faults'] == {'GRID': 0.}
    assert c['policy']['config']['cooling_preparation'] is True
    assert c['annual_readiness']['source_id'] == GRID_SOURCE


def test_multiple_calendar_faults_have_distinct_non_clean_normal_scope(registry):
    events=[dict(time_minute=60.,availability={'GRID': 0.}),
        dict(time_minute=120.,availability={'GRID': 1.}),
        dict(time_minute=180.,availability={'TOWER': .01}),
        dict(time_minute=240.,availability={'TOWER': 1.})]
    saved=copy.deepcopy(events)
    c=make(registry, mode='N', calendar=events, duration_minutes=360.)
    s=c['scenario']
    assert s['model_closure_events'] == events
    assert s['faults'] == s['shock_by_zone'] == {}
    assert s['normal_verification'] is False
    assert s['canonical_class'] == 'calendar'
    assert c['annual_readiness']['case_kind'] == 'calendar_multi_event'
    assert s['tail_interval_minutes'] == [240.,360.]
    assert events == saved
    events[0]['availability']['GRID']=.5
    assert s['model_closure_events'][0]['availability']['GRID'] == 0.
    assert api().validate_case(c, registry) == c


def test_new_external_calendar_has_its_own_stream_identity(registry):
    normal=make(registry,mode='N',duration_minutes=360.)
    disturbed=make(registry,mode='N',duration_minutes=360.,calendar=[
        dict(time_minute=60.,availability={'GRID': 0.}),
        dict(time_minute=120.,availability={'GRID': 1.})])
    assert normal['scenario']['exogenous_stream_id'] != disturbed['scenario']['exogenous_stream_id']
    assert normal['scenario']['base_state_id'] == disturbed['scenario']['base_state_id']


@pytest.mark.parametrize('minutes',[None,30.,60.,120.])
def test_only_named_functional_requalification_rules_are_registered(registry, minutes):
    c=make(registry, requalification_minutes=minutes)
    assert c['annual_readiness']['parameters']['requalification_minutes'] == minutes
    assert c['annual_readiness']['qualification_is_functional_not_DC_energy'] is True


def test_restart_worker_registry_changes_only_named_restart_and_drops_cases(registry):
    before=copy.deepcopy(registry)
    c=make(registry, boiler_restart_minutes=15.)
    worker=api().worker_registry(c, registry)
    expected=copy.deepcopy({k:v for k,v in registry.items() if k!='cases'})
    expected['model_closure_v22']['station']['boiler_restart_minutes']=15.
    assert worker == expected
    assert worker['model_closure_v22']['station']['control_autonomy_minutes'] == 1440.
    assert worker['model_closure_v22']['station']['dg_qualified_starts'] == 2
    assert registry == before


@pytest.mark.parametrize('kwargs',[
    dict(duration_minutes=10095.), dict(duration_minutes=0.), dict(duration_minutes=181.),
    dict(duration_minutes=True), dict(duration_minutes=float('inf')),
    dict(start_hour=True), dict(start_hour=33.5), dict(start_hour=0), dict(start_hour=8761),
    dict(mode='N',start_hour=4593,duration_minutes=10080.),
    dict(mode='N',start_hour=8759,duration_minutes=180.),
    dict(fault_duration_minutes=180.,duration_minutes=285.),
    dict(requalification_minutes=True), dict(requalification_minutes=15.),
    dict(requalification_minutes=float('nan')), dict(boiler_restart_minutes=True),
    dict(boiler_restart_minutes=0.), dict(configuration='H3'),
    dict(fault_duration_minutes=True), dict(response_wait_minutes=True), dict(demand_amplitude=True),
])
def test_invalid_boundaries_or_unregistered_numbers_are_rejected(registry, kwargs):
    with pytest.raises(ValueError):
        make(registry, **kwargs)


@pytest.mark.parametrize('calendar',[
    [dict(time_minute=120.,availability={'GRID': 0.})],
    [dict(time_minute=180.,availability={'GRID': 1.})],
    [dict(time_minute=90.,availability={'GRID': 0.}),dict(time_minute=60.,availability={'GRID': 1.})],
    [dict(time_minute=True,availability={'GRID': 0.})],
    [dict(time_minute=60.,availability={'new_DC_charger': 1.})],
    [dict(time_minute=60.,availability={'GRID': True})],
    [dict(time_minute=60.,availability={'GRID': 1.1})],
    [dict(time_minute=60.,availability={'GRID': 0.},future=True)],
])
def test_calendar_requires_original_schema_and_sixty_minute_tail(registry, calendar):
    with pytest.raises(ValueError):
        make(registry, mode='N',calendar=calendar,duration_minutes=165.)


def test_calendar_cannot_overlap_base_fault_and_demand_registration(registry):
    with pytest.raises(ValueError):
        make(registry,mode='FD',calendar=[dict(time_minute=60.,availability={'GRID': 0.})])


@pytest.mark.parametrize('mutate',[
    lambda c:c.__setitem__('id','forged'),
    lambda c:c['scenario'].__setitem__('id','forged'),
    lambda c:c['scenario'].__setitem__('grid_limit_kW',99999.),
    lambda c:c['scenario'].__setitem__('original_final12_evaluated',True),
    lambda c:c['scenario'].__setitem__('initial_reserve_kWh',99999.),
    lambda c:c['policy'].__setitem__('alias','C'),
    lambda c:c['policy']['config'].__setitem__('response_minutes',60.),
    lambda c:c.__setitem__('configuration','H2'),
    lambda c:c.__setitem__('model_config',{}),
    lambda c:c['scenario']['full_source_support'].__setitem__('raw_end_minute',999999.),
    lambda c:c['annual_readiness']['parameters'].__setitem__('duration_minutes',181.),
    lambda c:c['annual_readiness'].__setitem__('qualification_is_functional_not_DC_energy',False),
    lambda c:c['scenario'].__setitem__('enable_propagation',1),
])
def test_case_cannot_be_forged_by_changing_physics_policy_scope_or_identity(registry, mutate):
    c=make(registry)
    mutate(c)
    with pytest.raises(ValueError):
        api().validate_case(c, registry)


@pytest.mark.parametrize('mutate',[
    lambda r:r['hardware_profiles']['H2']['sci_engineering']['cold_store'].__setitem__('capacity_kWh',999.),
    lambda r:r['model_closure_v22']['station'].__setitem__('control_autonomy_minutes',99999.),
    lambda r:r['model_closure_v22'].__setitem__('research_stress_proxy',True),
    lambda r:r['model_correctness_v23'].__setitem__('thermal_coupling',{}),
    lambda r:r['cases'][0]['policy']['config'].__setitem__('response_minutes',60.),
])
def test_registry_drift_cannot_redefine_the_frozen_physical_or_policy_baseline(registry, mutate):
    changed=copy.deepcopy(registry);mutate(changed)
    with pytest.raises(ValueError):
        make(changed)


def test_no_unknown_runtime_override_keywords_are_accepted(registry):
    for key in ('model_config','faults','policy_override_alias','initial_reserve_kWh'):
        with pytest.raises(TypeError):
            make(registry, **{key:{}})


def test_longer_fault_extends_recovery_tail_but_never_changes_demand_duration(registry):
    c=make(registry,fault_duration_minutes=180.,duration_minutes=360.)
    s=c['scenario']
    assert s['fault_duration_minutes'] == 180.
    assert s['shock_duration_minutes'] == 60.
    assert s['recovery_start_minute'] == 240.
    assert s['tail_interval_minutes'] == [240.,360.]
