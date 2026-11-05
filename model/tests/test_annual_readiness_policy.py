"""Ordinary readiness requests never become actual source or service claims."""
import copy
import importlib
from pathlib import Path
from types import MethodType, SimpleNamespace as NS

import numpy as np
import pytest


def implementation():
    try:
        return importlib.import_module('tools.annual_readiness.policy')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_readiness.policy':
            raise
        pytest.fail('The new current-observation preparation helper is missing', pytrace=False)


def command(targets=None):
    return dict(gt_electric_kW=[0., 0., 0.] if targets is None else targets,
        grid_import_kW=100., boiler_steam_lbph=3500., diesel_kW=0.,
        electric_chiller_cooling_kW=[12., 3., 0.], absorption_cooling_kW=7.,
        priority_contacts=[[0., 0., 0.] for _ in range(4)],
        nested={'cold_inventory_kWh': 59.403, 'untouched': ['original']})


def prepare(dispatch=None, **overrides):
    values=dict(actual_gt_kW=[0., 0., 0.],
        availability={'GT_1': 1., 'GT_2': 1., 'GT_3': 1.},
        rated_kW=100., minimum_fraction=.4, critical_request_kW=1.)
    values.update(overrides)
    return implementation().prepare_online_readiness(command() if dispatch is None else dispatch, **values)


def test_requests_first_eligible_minimum_without_mutating_any_input():
    dispatch=command(); before=copy.deepcopy(dispatch)
    actual=[0., 0., 0.]; availability={'GT_1': 1., 'GT_2': 1., 'GT_3': 1.}
    result, trace=prepare(dispatch, actual_gt_kW=actual, availability=availability)
    assert result['gt_electric_kW'] == [40., 0., 0.]
    assert dispatch == before
    assert actual == [0., 0., 0.]
    assert availability == {'GT_1': 1., 'GT_2': 1., 'GT_3': 1.}
    assert {k:v for k,v in result.items() if k != 'gt_electric_kW'} == {
        k:v for k,v in before.items() if k != 'gt_electric_kW'}
    result['nested']['untouched'].append('copy')
    assert dispatch == before
    assert trace['selected_GT'] == 'GT_1'
    assert trace['input_target_kW'] == [0., 0., 0.]
    assert trace['requested_target_kW'] == [40., 0., 0.]
    assert trace['actual_online_mask'] == [False, False, False]
    assert trace['minimum_stable_kW'] == 40.


def test_actual_online_eligible_unit_precedes_an_existing_commanded_offline_unit():
    result, trace=prepare(command([45., 0., 0.]), actual_gt_kW=[0., 23., 0.])
    assert result['gt_electric_kW'] == [45., 40., 0.]
    assert trace['selected_GT'] == 'GT_2'
    assert trace['actual_online_mask'] == [False, True, False]
    assert trace['selection_basis'] == 'current_actual_online_eligible_GT'


def test_pending_or_requested_target_is_not_actual_online_supply():
    result, trace=prepare(command([0., 0., 60.]))
    assert result['gt_electric_kW'] == [40., 0., 60.]
    assert trace['selected_GT'] == 'GT_1'
    assert trace['actual_online_mask'] == [False, False, False]
    assert trace['pending_target_credited_as_actual'] is False


def test_existing_larger_legal_target_is_retained():
    result, trace=prepare(command([0., 68., 0.]), actual_gt_kW=[0., 60., 0.])
    assert result['gt_electric_kW'] == [0., 68., 0.]
    assert trace['changed'] is False


def test_current_hardware_loss_excludes_previously_online_unit():
    result, trace=prepare(actual_gt_kW=[30., 0., 0.],
        availability={'GT_1': 0., 'GT_2': 1., 'GT_3': 1.})
    assert result['gt_electric_kW'] == [0., 40., 0.]
    assert trace['actual_online_mask'] == [True, False, False]
    assert trace['eligible_mask'] == [False, True, True]
    assert trace['selected_GT'] == 'GT_2'


def test_partial_capacity_must_reach_original_rated_minimum():
    result, trace=prepare(actual_gt_kW=[20., 0., 0.],
        availability={'GT_1': .39, 'GT_2': .4, 'GT_3': .8})
    assert result['gt_electric_kW'] == [0., 40., 0.]
    assert trace['effective_capacity_kW'] == [39., 40., 80.]
    assert trace['eligible_mask'] == [False, True, True]


def test_no_legal_unit_preserves_command_and_records_preparation_unavailable():
    dispatch=command()
    result, trace=prepare(dispatch, availability={'GT_1': .39, 'GT_2': 0., 'GT_3': .1})
    assert result == dispatch
    assert trace['selected_GT'] is None
    assert trace['cannot_prepare_reason'] == 'no_current_GT_capacity_reaches_original_minimum'
    assert trace['status'] == 'unavailable'
    assert trace['changed'] is False


def test_zero_critical_request_only_disables_this_new_preparation():
    dispatch=command([0., 0., 60.])
    result, trace=prepare(dispatch, critical_request_kW=0.)
    assert result == dispatch
    assert trace['status'] == 'inactive_no_current_critical_request'
    assert trace['selected_GT'] is None


def test_critical_scalar_is_only_activation_and_not_a_cross_carrier_score():
    low, low_trace=prepare(critical_request_kW=.001)
    high, high_trace=prepare(critical_request_kW=1e6)
    assert low == high
    assert low_trace['critical_request_activation_only'] is True
    assert high_trace['critical_request_activation_only'] is True
    assert low_trace['cross_carrier_service_score_computed'] is False


def test_requested_floor_is_not_a_sink_start_or_service_guarantee():
    _, trace=prepare()
    assert trace['ordinary_queued_target_only'] is True
    assert trace['current_observation_only'] is True
    assert trace['future_event_information_used'] is False
    assert trace['queue_bypassed'] is False
    assert trace['actual_supply_guaranteed'] is False
    assert trace['electric_sink_feasibility'] == 'unknown_not_checked_by_this_rule'
    assert trace['startup_and_service_feasibility'] == 'original_execution_constraints_required'


def test_no_future_scenario_or_pending_api_is_accepted():
    first, trace=prepare()
    second, repeated=prepare()
    assert (first, trace) == (second, repeated)
    for forbidden in ('scenario', 'future_events', 'pending_gt_kW'):
        with pytest.raises(TypeError):
            prepare(**{forbidden: {'future_fault_start_minute': 15.}})


@pytest.mark.parametrize('field,value', [
    ('actual_gt_kW', [0., 0.]), ('actual_gt_kW', [0., 0., -1.]),
    ('actual_gt_kW', [0., float('nan'), 0.]), ('actual_gt_kW', [0., True, 0.]),
    ('rated_kW', 0.), ('rated_kW', -1.), ('rated_kW', float('inf')), ('rated_kW', True),
    ('minimum_fraction', 0.), ('minimum_fraction', 1.01), ('minimum_fraction', float('nan')),
    ('critical_request_kW', -1.), ('critical_request_kW', float('inf')), ('critical_request_kW', True),
    ('availability', {'GT_1': 1., 'GT_2': 1.}),
    ('availability', {'GT_1': 1.01, 'GT_2': 1., 'GT_3': 1.}),
    ('availability', {'GT_1': True, 'GT_2': 1., 'GT_3': 1.}),
    ('availability', {'GT_1': float('nan'), 'GT_2': 1., 'GT_3': 1.}),
])
def test_invalid_current_input_is_rejected(field, value):
    with pytest.raises(ValueError):
        prepare(**{field: value})


@pytest.mark.parametrize('targets', [[0., 0.], [0., -1., 0.], [float('nan'), 0., 0.],
    [True, 0., 0.]])
def test_invalid_existing_target_is_not_silently_rounded_or_repaired(targets):
    dispatch=command(targets); before=copy.deepcopy(dispatch)
    with pytest.raises(ValueError):
        prepare(dispatch)
    if not any(isinstance(v, float) and np.isnan(v) for v in targets):
        assert dispatch == before


def test_existing_target_above_reference_is_preserved_for_original_projection():
    # Epoch3 called finite request ceilings malformed when above its nominal
    # preparation reference. The real t900 ambient-domain witness disproved
    # that assumption. Replace rejection coverage with exact preservation.
    dispatch=command([41., 0., 0.]);before=copy.deepcopy(dispatch)
    result, trace=prepare(dispatch, availability={'GT_1': .4, 'GT_2': 1., 'GT_3': 1.})
    assert result==dispatch==before
    assert trace['preexisting_above_reference_mask']==[True, False, False]
    assert trace['preexisting_above_reference_kW']==[1., 0., 0.]
    assert trace['preparation_delta_kW']==[0., 0., 0.]
    assert trace['original_execution_projection_required'] is True


def test_finite_101_target_replaces_old_invalid_target_assumption():
    # The previous malformed-input parameter101 was actually a finite
    # ordinary request, not a capacity certificate. No target is clipped.
    dispatch=command([101., 0., 0.]);before=copy.deepcopy(dispatch)
    result, trace=prepare(dispatch)
    assert result==dispatch==before
    assert trace['preexisting_above_reference_mask']==[True, False, False]
    assert trace['actual_supply_guaranteed'] is False


def test_recorded_cold_ambient_GT_component_is_not_rejected_by_nominal_reference():
    # Recorded GT component/hook scalars: READY26H_FAILURE_DIAGNOSTIC.json.
    # The failed producer's complete dispatch was not directly saved; the
    # other fields in command() are only this pure test's request fixture.
    from hospital_ies.model import gt_level_properties, load_plant
    plant=load_plant(Path(__file__).resolve().parents[1]/'configs/plant_configs.json',
        'doe_capacity_matched_exploratory')
    witness=322.43248166310775
    physical=float(gt_level_properties(plant,6.583333333333333)[0][-1])
    assert plant.gt_rated_kW==320. and plant.gt_rated_kW<witness<physical
    dispatch=command([0., 0., witness]);before=copy.deepcopy(dispatch)
    result, trace=prepare(dispatch,actual_gt_kW=[0.,0.,135.989504],rated_kW=320.)
    assert result==dispatch==before
    assert trace['selected_GT']=='GT_3' and trace['changed'] is False
    assert trace['minimum_stable_kW']==128.
    assert trace['eligible_mask']==[True,True,True]
    assert trace['preexisting_above_reference_mask']==[False,False,True]
    assert trace['preexisting_above_reference_kW']==[0.,0.,witness-320.]
    assert trace['preparation_delta_kW']==[0.,0.,0.]
    assert trace['nominal_preparation_reference_kW']==[320.,320.,320.]
    assert trace['effective_capacity_kW']==trace['nominal_preparation_reference_kW']
    assert trace['effective_capacity_is_physical_limit'] is False
    assert trace['capacity_reference_basis']=='nominal_rating_times_current_availability'
    assert trace['schema']=='hospital_ies.annual_readiness.current_GT_preparation.v2'


def test_now_ineligible_previous_request_is_not_reduced_when_other_floor_added():
    dispatch=command([101.,0.,0.]);before=copy.deepcopy(dispatch)
    result, trace=prepare(dispatch,actual_gt_kW=[30.,0.,0.],
        availability={'GT_1':.39,'GT_2':1.,'GT_3':1.})
    assert dispatch==before
    assert result['gt_electric_kW']==[101.,40.,0.]
    assert trace['selected_GT']=='GT_2'
    assert trace['eligible_mask']==[False,True,True]
    assert trace['preexisting_above_reference_mask']==[True,False,False]
    assert trace['preparation_delta_kW']==[0.,40.,0.]


def test_inactive_critical_request_preserves_oversized_finite_request():
    dispatch=command([0.,0.,101.]);result,trace=prepare(dispatch,critical_request_kW=0.)
    assert result==dispatch
    assert trace['status']=='inactive_no_current_critical_request'
    assert trace['preexisting_above_reference_mask']==[False,False,True]
    assert trace['preparation_delta_kW']==[0.,0.,0.]


def test_original_queue_and_arrival_projection_remain_authoritative_without_solver():
    from hospital_ies.model import gt_level_properties, load_plant
    from hospital_ies.state_transition import TransitionCore
    from tools.sci_engineering.environment import EngineeringCore
    plant=load_plant(Path(__file__).resolve().parents[1]/'configs/plant_configs.json',
        'doe_capacity_matched_exploratory')
    row=NS(outdoor_temperature_C=6.583333333333333)
    availability=dict(GRID=1.,DIESEL=1.,ABS=1.,**{f'GT_{i}':1. for i in (1,2,3)},
        **{f'EC_{i}':1. for i in (1,2,3)},**{f'BOILER_{i}':1. for i in (1,2,3)})
    loads=np.ones((4,3));actual=[0.,0.,135.989504]
    dispatch=dict(gt_electric_kW=[0.,0.,400.],grid_import_kW=850.,diesel_kW=0.,
        boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.],
        hrsg_steam_lbph=0.,tower_rejection_kW=0.,gt_fuel_MMBtu_h=0.)
    state=NS(gt_prev=np.array(actual),last_grid_import_kW=0.,last_diesel_kW=0.,
        diesel_energy_remaining_kWh=2400.,disconnected_loads=np.zeros((4,3)))
    env=NS(plant=plant,state=state,dt_minutes=15.,dt_h=.25,
        config={'gt_ramp_kW_per_hour':900.,'grid_import_limit_kW':850.},
        execution_contract={'gt_start_delay_minutes':10.,'grid_import_mode':'arrived_ceiling'},
        scenario={'grid_limit_kW':850.,'duration_steps':104,'shock_start_minute':60.,'shock_duration_minutes':0.})
    c=NS(env=env,state=state,time=900.,failed=False,sequence=0,pending_commands=[],solver_calls=[],
        events=[],executed=dict(dispatch,gt_electric_kW=actual.copy()),active_command=None,
        gt_start_pending={},ramp_time=900.,ramp_anchor=np.array(actual),ramp_budget=0.,
        last_availability=None,last_shock_active=None,grid_outage_start=None,
        automatic_disconnected=np.zeros((4,3)),automatic_fraction=np.zeros((4,3)),
        disconnected_fraction=np.zeros((4,3)),pending_shed=None,local_down=None)
    c.data_at=lambda minute:(1,row,loads,availability)
    c.readiness_context=lambda:{}
    c.delayed_at=lambda minute:(True,915.)
    c.diesel_available=lambda minute,hardware:False
    c._sync_connections=lambda current:None
    c._update_support_states=lambda hardware:None
    for name in ('_clamp','_commit_sources','log','aux'):
        setattr(c,name,MethodType(getattr(TransitionCore,name),c))
    def hook(value,diagnostic,current_loads):
        prepared,trace=prepare(value,actual_gt_kW=actual,rated_kW=320.)
        return prepared,dict(diagnostic,annual_online_preparation=trace)
    c._prepare_command_dispatch=hook
    request={'ordinary_dispatch':{'dispatch':dispatch,'disconnected_kW':np.zeros((4,3)).tolist()}}
    before=copy.deepcopy(request)
    generated=EngineeringCore.generate_command(c,request)
    assert request==before
    assert generated['generated_minute']==900. and generated['arrival_minute']==915.
    assert generated['dispatch']['gt_electric_kW']==[0.,0.,400.]
    assert c.pending_commands==[generated] and c.active_command is None
    assert c.state.gt_prev.tolist()==actual
    c.time=914.;TransitionCore.boundary(c)
    assert c.state.gt_prev.tolist()==actual and c.pending_commands==[generated]
    assert not [e for e in c.events if e['kind']=='command_executed']
    # Capacity changes before arrival. The ORIGINAL arrival clamp reads the
    # current ambient/fault cap, not the helper's old preparation reference.
    availability['GT_3']=.5;c.time=915.;TransitionCore.boundary(c)
    physical=float(gt_level_properties(plant,row.outdoor_temperature_C)[0][-1])*.5
    assert c.executed['gt_electric_kW'][2]==physical
    assert c.state.gt_prev[2]==physical and physical<400.
    assert c.pending_commands==[] and c.active_command['id']==generated['id']
    event=next(e for e in c.events if e['kind']=='command_executed')
    assert event['time_minute']==915. and event['arrival_minute']==915.
    assert event['requested_dispatch']['gt_electric_kW'][2]==400.
    assert event['executed_dispatch']['gt_electric_kW'][2]==physical
    assert event['reason']=='revalidated_against_current_capacity_and_ramp'
    assert c.ramp_budget==225.
    assert all(call['upper_optimizer_called'] is False for call in c.solver_calls)


def test_numpy_current_vectors_are_supported_without_mutation():
    dispatch=command(np.array([0., 0., 0.])); actual=np.array([0., 50., 0.])
    result, trace=prepare(dispatch, actual_gt_kW=actual)
    assert list(result['gt_electric_kW']) == [0., 40., 0.]
    assert np.array_equal(dispatch['gt_electric_kW'], [0., 0., 0.])
    assert np.array_equal(actual, [0., 50., 0.])
    assert trace['selected_GT'] == 'GT_2'
