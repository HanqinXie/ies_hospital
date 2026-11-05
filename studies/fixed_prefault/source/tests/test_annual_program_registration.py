"""Explicit annual scope must not weaken the historical seven-day guard."""
import copy
import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def implementation():
    try:return importlib.import_module('tools.annual_program.registration')
    except ModuleNotFoundError as exc:
        if exc.name!='tools.annual_program.registration':raise
        pytest.fail('Canonical annual study registration is missing',pytrace=False)


@pytest.mark.parametrize('method',['M_available','M_ready','C_ready'])
def test_exact_year_has_35040_decisions_and_no_hour8761(method):
    m=implementation();case=m.make_case(method)
    scene=case['scenario'];meta=case['annual_program']
    assert scene['start_hour']==1 and scene['duration_steps']==35040
    assert scene['full_source_support']==dict(raw_start_minute=0.,raw_end_minute=525600.)
    assert len(m.required_hours(case))==8760 and max(m.required_hours(case))==8760
    assert meta['annual_study_is_blind'] is False
    assert meta['original_benchmark_run'] is False
    assert 'original_final12_untouched' not in case.get('annual_readiness',{})
    assert case==m.validate_case(case)


def test_last_source_hour_short_case_and_end_carryout_are_explicit():
    m=implementation();events=[dict(time_minute=15.,availability={'ABS':0.}),
        dict(time_minute=180.,availability={'ABS':1.})]
    case=m.make_case('M_ready',start_hour=8760,duration_minutes=60.,mode='F',calendar=events)
    assert m.required_hours(case)=={8760}
    assert case['annual_program']['unobserved_calendar_events']==[events[-1]]
    assert case['scenario']['model_closure_events']==events
    assert case['annual_program']['recovery_after_source_end']=='right_censored_no_extrapolation'


def test_d_and_fd_have_identical_existing_zone_growth_and_fault_calendar_pairing():
    m=implementation();events=[dict(time_minute=60.,availability={'GRID':0.}),
        dict(time_minute=120.,availability={'GRID':1.})]
    d=m.make_case('M_ready',mode='D',duration_minutes=180.)
    fd=m.make_case('M_ready',mode='FD',calendar=events,duration_minutes=180.)
    f=m.make_case('M_ready',mode='F',calendar=events,duration_minutes=180.)
    assert d['scenario']['shock_by_zone']==fd['scenario']['shock_by_zone']
    assert d['scenario']['shock_by_zone']=={'ER_TRAUMA1_FLR_1':{'c':.25}}
    assert fd['scenario']['model_closure_events']==f['scenario']['model_closure_events']
    assert d['scenario']['shock_start_minute']==0.
    assert d['scenario']['shock_duration_minutes']==180.


@pytest.mark.parametrize('changes',[dict(configuration='H3'),dict(method='future_MPC'),
    dict(duration_minutes=525615.),dict(start_hour=8760,duration_minutes=75.),
    dict(mode='F'),dict(mode='N',calendar=[dict(time_minute=5.,availability={'GRID':0.})])])
def test_invalid_or_unregistered_scope_rejected(changes):
    m=implementation();args=dict(method='M_ready');args.update(changes)
    with pytest.raises(ValueError):m.make_case(**args)


def test_capacity_policy_delay_and_identity_changes_fail_canonical_validation():
    m=implementation();case=m.make_case('C_ready',duration_minutes=180.)
    for path,value in [(('policy','config','support_minutes'),999.),
                       (('scenario','response_delay_minutes'),0.),(('configuration',),'H0')]:
        changed=copy.deepcopy(case);current=changed
        for key in path[:-1]:current=current[key]
        current[path[-1]]=value
        with pytest.raises(ValueError):m.validate_case(changed)


def test_historical_registration_still_rejects_annual_duration():
    from tools.annual_readiness.registration import make_case,load_registry
    with pytest.raises(ValueError):
        make_case(load_registry(),'b07_L4_GT_ER_disturbed_M','M_ready',mode='N',duration_minutes=525600.)


def test_short_pilot_reserves_its_existing_normal_endpoint_hour():
    m=implementation();case=m.make_case('M_ready',duration_minutes=180.)
    assert m.required_hours(case)=={1,2,3,4}
    assert case['scenario']['full_source_support']['raw_end_minute']==240.
    assert case['annual_program']['endpoint_source_hour_reserved'] is True


@pytest.mark.parametrize('availability',['0','0.5',True,float('nan')])
def test_calendar_numeric_strings_and_booleans_not_runtime_availability(availability):
    with pytest.raises(ValueError):implementation().make_case('M_ready',mode='F',
        calendar=[dict(time_minute=60.,availability={'GRID':availability})])


def test_actual_initial_state_and_source_season_replace_template_metadata():
    m=implementation();first=m.make_case('M_ready',duration_minutes=60.)
    last=m.make_case('M_ready',start_hour=8760,duration_minutes=60.)
    assert first['scenario']['base_state_id']!=last['scenario']['base_state_id']
    assert 'season_quarter' not in last['scenario']
    assert last['annual_program']['source_quarter_indices']==[3]
    fd=m.make_case('M_ready',mode='FD',calendar=[dict(time_minute=60.,availability={'GRID':0.})])
    assert fd['annual_readiness']['case_kind']=='annual_FD'
    assert fd['annual_readiness']['parameters']['demand_amplitude']==.25


@pytest.mark.parametrize('duration',[15.,60.,180.])
def test_registered_scene_passes_the_actual_native_parser_before_initialization(duration):
    from tools.model_review_v28.environment import validate_scenario
    case=implementation().make_case('M_ready',duration_minutes=duration)
    scene=copy.deepcopy(case['scenario']);scene.pop('model_closure_events')
    assert validate_scenario(scene)['duration_steps']==int(duration/15.)


def common_annual_calendar():
    return [dict(time_minute=30.,availability={'GRID':0.}),
            dict(time_minute=90.,availability={'GRID':1.})]


@pytest.mark.parametrize('method',['M_available','M_ready','C_ready'])
@pytest.mark.parametrize('mode',['N','F','D','FD'])
def test_four_annual_conditions_pass_actual_parser_without_switching_propagation(method,mode):
    from tools.model_review_v28.environment import validate_scenario
    calendar=common_annual_calendar() if mode in ('F','FD') else None
    case=implementation().make_case(method,mode=mode,calendar=calendar)
    scene=copy.deepcopy(case['scenario']);detached=scene.pop('model_closure_events')
    # This is the real parser called at reset, before any initialization LP.
    checked=validate_scenario(scene)
    assert checked['stage']==(2 if mode in ('D','FD') else 1)
    assert checked['event_class']==('L2' if mode in ('D','FD') else 'L1')
    assert checked['enable_propagation'] is False
    assert checked['canonical_class']==mode
    assert checked['native_event_class_is_parser_encoding_only'] is True
    assert checked['faults']=={}  # F/FD hardware consumes the detached M23 calendar.
    assert detached==(calendar or [])
    assert case==implementation().validate_case(case)


@pytest.mark.parametrize('method,mode,expected_sha',[
    ('M_available','N','3b4b09acbad006b6d03062c9ff6d5b05258d35bfcfa941e94e5368d546f24839'),
    ('M_available','F','8d20dc80f52eca5b1a17fe3e580303ce4e09f033da1151534317d4ea33a11311'),
    ('M_ready','N','fbe62e2da0b818f80a044e996fa459b9442851f487f0633e518f01aebc9a01dc'),
    ('M_ready','F','9360a5c4e4577bc03deb69676f0321f077ca3f3ccb549884a50a73971f5697de'),
    ('C_ready','N','0d607581ab747c04df96cd51cd5abfc8f8481c57d1ce0dedaf58c763ba14d9d8'),
    ('C_ready','F','7d946d2bca427f38b0b9b665561b8dd08e3714287b99411fb4541d76ebd82382')])
def test_normal_and_fault_case_full_identity_is_unchanged_by_parser_fix(method,mode,expected_sha):
    from tools.sci_engineering.design import digest
    case=implementation().make_case(method,mode=mode,
        calendar=common_annual_calendar() if mode=='F' else None)
    assert digest(case)==expected_sha


@pytest.mark.parametrize('method',['M_available','M_ready','C_ready'])
def test_parser_labels_do_not_change_any_physical_config_or_protection_flag(method):
    from tools.annual_program.runner import _configuration
    from tools.annual_readiness.registration import load_registry
    from tools.sci_engineering.design import digest
    registry=load_registry();hashes=[]
    for mode in ('N','F','D','FD'):
        case=implementation().make_case(method,mode=mode,
            calendar=common_annual_calendar() if mode in ('F','FD') else None)
        model=_configuration(case,registry)['model_config']
        hashes.append(digest(model))
        assert model['model_closure_v22']['research_stress_proxy'] is False
        assert case['scenario']['enable_propagation'] is False
        assert case['scenario']['automatic_fast_shedding'] is False
    assert hashes==['1e6afc9843af730b51f1f261e9ec7c1b8d9fa1e171b8db6984b6b637be4adc42']*4


@pytest.mark.parametrize('mode',['D','FD'])
def test_demand_onset_response_and_external_calendar_are_independent_of_parser_stage(mode):
    from tools.operating_rules_study.environment import response_windows
    calendar=common_annual_calendar() if mode=='FD' else None
    case=implementation().make_case('M_ready',mode=mode,calendar=calendar)
    scene=case['scenario']
    assert response_windows(scene,'event_triggered',observed_until=0.)==[(0.,15.)]
    assert scene['shock_by_zone']=={'ER_TRAUMA1_FLR_1':{'c':.25}}
    assert scene['shock_start_minute']==0. and scene['shock_duration_minutes']==525600.
    assert scene['model_closure_events']==(calendar or [])


def test_parser_repair_does_not_add_a_global_realized_fault_exposure_filter():
    # Empty random years require future explicit physical N/D routing plus
    # random-calendar provenance. This constructor's old F admission is kept;
    # metadata-only all-normal/future calendars are not redrawn or relabelled.
    m=implementation()
    for calendar in ([dict(time_minute=30.,availability={'GRID':1.})],
                     [dict(time_minute=525601.,availability={'GRID':0.})]):
        case=m.make_case('M_ready',mode='F',calendar=calendar)
        assert case['scenario']['model_closure_events']==calendar
    with pytest.raises(ValueError):m.make_case('M_ready',mode='F',calendar=[])
