"""V17 one-command preparation uses real carrier, readiness and shaft limits."""
import copy
import importlib

import numpy as np
import pytest

from test_engineering_planner import problem, BASE
from test_operating_readiness import current, dispatch


def api(module='planner'):
    try:
        return importlib.import_module('tools.state_coordinated.' + module)
    except ModuleNotFoundError:
        pytest.fail('The separate V17 state-coordinated implementation is missing')


def context(p, **changes):
    c = current(p['state'].gt_prev.tolist())
    c['arrived_dispatch'] = dispatch(p['state'].gt_prev.tolist(), grid=p['grid_import_limit_kW'])
    c.update(changes)
    return c


def solve(p, coordination=None, readiness=None):
    return api().solve_joint(**p, joint_options=BASE,
        readiness_context=readiness or context(p), coordination_options=coordination)


def test_request_is_separate_strict_and_does_not_accept_future_events():
    policy = api('policy')
    request = policy.validate_action({'state_coordinated_joint': {}})
    assert set(request) == {'state_coordinated_joint'}
    assert request['state_coordinated_joint']['coordination']['response_minutes'] == 30.
    assert request['state_coordinated_joint']['coordination']['support_minutes'] == 60.
    with pytest.raises(ValueError):
        policy.validate_action({'operating_joint': {}})
    with pytest.raises(ValueError):
        policy.validate_action({'state_coordinated_joint': {'future_faults': []}})
    with pytest.raises(ValueError):
        policy.validate_action({'state_coordinated_joint': {'coordination': {'response_minutes': 0.}}})

def test_response_before_registered_unknown_command_arrival_is_not_claimed_ready():
    with pytest.raises(ValueError,match='response|delay'):
        api('readiness').coordination_options(dict(response_minutes=15.,delay_bound_minutes=30.))


def test_unknown_queue_does_not_turn_off_gt_into_thirty_minute_supply(problem):
    readiness = api('readiness')
    c = context(problem)
    c['known_command_queue'] = [dict(command_id=1, generated_minute=0.,
        planned_arrival_minute=None, permitted_dispatch=dispatch([128.,128.,128.]))]
    envelope = readiness.response_envelope(c, response_minutes=30., delay_bound_minutes=30.,
        gt_start_delay_minutes=10., gt_ramp_kW_per_h=900., rated_gt_kW=320.)
    assert envelope['gt_max_kW'][1] == 0.
    assert envelope['actual_arrival_known'] is False
    assert envelope['conditional_on_registered_delay_bound'] is True
    later = readiness.response_envelope(c, response_minutes=60., delay_bound_minutes=30.,
        gt_start_delay_minutes=10., gt_ramp_kW_per_h=900., rated_gt_kW=320.)
    assert later['gt_max_kW'][1] == 320.


def test_real_pending_start_can_be_ready_but_minimum_off_still_binds(problem):
    readiness = api('readiness')
    c = context(problem)
    c['gt_physical_status'][1] = 'STARTING'
    c['pending_starts'][1] = dict(started=0., ready=10., requested=180.)
    e = readiness.response_envelope(c, response_minutes=30., delay_bound_minutes=30.,
        gt_start_delay_minutes=10., gt_ramp_kW_per_h=900., rated_gt_kW=320.)
    assert e['gt_max_kW'][1] == 180.
    c['pending_starts'][1] = None; c['gt_physical_status'][1] = 'OFF'
    c['earliest_start_minute'][1] = 80.
    e = readiness.response_envelope(c, response_minutes=60., delay_bound_minutes=30.,
        gt_start_delay_minutes=10., gt_ramp_kW_per_h=900., rated_gt_kW=320.)
    assert e['gt_max_kW'][1] == 0.


def test_shaft_support_reserves_loss_start_energy_and_never_credits_rotor_as_fuel(problem):
    readiness = api('readiness')
    c = context(problem)
    budget = readiness.diesel_support_budget(c, rated_kW=900., remaining_kWh=60.,
        support_minutes=60., startup_delay_minutes=5.)
    assert budget['shaft_loss_bound_kW'] == pytest.approx(21.78)
    assert budget['startup_shaft_work_kWh'] > .2
    assert budget['electric_support_ceiling_kW'] < 38.22
    longer = readiness.diesel_support_budget(c, rated_kW=900., remaining_kWh=60.,
        support_minutes=120., startup_delay_minutes=5.)
    assert longer['electric_support_ceiling_kW'] < budget['electric_support_ceiling_kW']


def test_converter_loss_copies_have_independent_ordinary_ceilings_and_joint_sources(problem):
    p = copy.deepcopy(problem)
    p['loads'][:] = 0.; p['loads'][0] = [300., 0., 400.]; p['loads'][3,0] = 200.
    p['grid_import_limit_kW'] = 850.
    result = solve(p)
    cases = result['joint_diagnostic']['risk_scenarios']
    assert {'ABS_loss', 'EC_1_loss', 'EC_2_loss', 'EC_3_loss'} <= set(cases)
    assert cases['ABS_loss']['absorption_cooling_kW'] == 0.
    for name in ('ABS_loss','EC_1_loss','EC_2_loss','EC_3_loss'):
        assert cases[name]['unserved_kW'][0][2] < .001
    assert result['action']['electric_chiller_cooling_kW'][0] >= result['planned_current_action']['electric_chiller_cooling_kW'][0]
    assert max(map(abs, result['energy_balance_residuals'])) < 1e-5


def test_common_tower_failure_cannot_be_fixed_by_adding_ec_and_abs(problem):
    p = copy.deepcopy(problem); p['availability']['TOWER'] = 0.
    p['loads'][:] = 0.; p['loads'][0] = [300., 0., 400.]
    result = solve(p)
    assert result['lost'][0,2] > 399.99
    for case in result['joint_diagnostic']['risk_scenarios'].values():
        assert case['absorption_cooling_kW'] + sum(case['electric_chiller_cooling_kW']) < .001


def test_response_copy_does_not_credit_unarrived_new_gt_or_its_hrsg(problem):
    r = solve(problem)
    d = r['joint_diagnostic']; response = d['coordination']['response_envelope']
    assert response['gt_max_kW'][1] == 0.
    for name, case in d['risk_scenarios'].items():
        if name.startswith('response_'):
            assert case['gt_electric_kW'][1] == 0.
            assert case['hrsg_gt_basis_kW'][1] == 0.
    assert 'response_GRID_loss' in d['risk_scenarios']
    assert 'GRID_loss' in d['risk_scenarios']


def test_sustained_copy_respects_shaft_budget_without_capping_current_p0_service(problem):
    p = copy.deepcopy(problem); p['loads'][:] = 0.; p['loads'][0,0] = 700.
    p['availability']['GRID'] = 0.; p['availability']['GT_2'] = 0.; p['availability']['GT_3'] = 0.
    p['state'].gt_prev[:] = [250.,0.,0.]; p['state'].diesel_energy_remaining_kWh = 60.
    p['state'].diesel_status = 'CONNECTED'; p['diesel_start_available'] = True
    c = context(p, diesel_connected=True, actual_diesel_kW=100., diesel_arrived_cap_kW=900.)
    c['arrived_dispatch'] = dispatch([250.,0.,0.], grid=0., diesel=900.)
    r = solve(p, readiness=c)
    bound = r['joint_diagnostic']['coordination']['diesel_support']['electric_support_ceiling_kW']
    assert bound < 38.22 + 1e-8
    assert r['planned_current_action']['diesel_kW'] > bound + 1.
    for name, case in r['joint_diagnostic']['risk_scenarios'].items():
        if name != 'held_actual' and name != 'arrived_current':
            assert case['diesel_kW'] <= bound + 1e-5
    ablated = solve(p, dict(time_energy_preparation=False), readiness=c)
    np.testing.assert_allclose(r['lost'][:2], ablated['lost'][:2], atol=1e-5)


def test_registered_ablation_removes_only_its_preparation_mechanism(problem):
    r = solve(problem, dict(cooling_preparation=False,time_energy_preparation=False))
    cases = r['joint_diagnostic']['risk_scenarios']
    assert 'ABS_loss' not in cases
    assert not any(k.startswith('response_') for k in cases)
    assert r['joint_diagnostic']['coordination']['time_energy_constraints_active'] is False
    assert max(map(abs, r['energy_balance_residuals'])) < 1e-5


def test_service_locks_precede_new_preparation_and_no_old_combination_lock(problem):
    r = solve(problem)
    order = r['joint_diagnostic']['objective_order']
    assert order.index('current_P0') < order.index('preparation_P0')
    assert order.index('current_P1') < order.index('preparation_P0')
    assert 'normal_current_GT_state_changes' not in order


def test_idle_connected_diesel_still_has_shaft_fuel_pressure(problem):
    p=copy.deepcopy(problem);p['availability']['GRID']=0.
    p['state'].diesel_energy_remaining_kWh=5.;p['state'].diesel_status='CONNECTED'
    p['diesel_start_available']=True
    c=context(p,diesel_connected=True,diesel_arrived_cap_kW=900.)
    c['arrived_dispatch']=dispatch(p['state'].gt_prev.tolist(),grid=0.,diesel=900.)
    r=solve(p,readiness=c)
    engineering=r['joint_diagnostic']['engineering']
    assert 0.<engineering['actual_diesel_fuel_support_hours']<1.
    assert engineering['endurance_preparation_active'] is True
    assert engineering['fuel_pressure_basis']=='shaft_work_with_loss_startup_and_positive_storage'


def test_grid_permission_can_prepare_above_current_use_with_a_real_floor_heat_witness(problem):
    p=copy.deepcopy(problem);p['grid_import_limit_kW']=850.
    p['state'].gt_prev[:]=[200.,0.,200.]
    p['loads'][:]=0.;p['loads'][0]=[150.,200.,150.];p['loads'][3,0]=450.
    r=solve(p)
    assert r['action']['grid_import_kW']>849.99
    assert r['planned_current_action']['grid_import_kW']<r['action']['grid_import_kW']-1.
    floor=r['joint_diagnostic']['risk_scenarios']['regulated_current']
    assert max(np.ravel(floor['unserved_kW']))<.001
    assert floor['electric_surplus_defect_kW']<1e-6
    assert floor['boiler_steam_lbph']==pytest.approx(r['action']['boiler_steam_lbph'])
    assert max(map(abs,floor['balance_residuals']))<1e-5


def response_choice_problem(problem):
    p=copy.deepcopy(problem)
    p['grid_import_limit_kW']=0.;p['availability']['GRID']=0.;p['availability']['GT_3']=0.
    p['state'].gt_prev[:]=[128.,0.,0.]
    p['loads'][:]=0.;p['loads'][0,0]=100.;p['loads'][3,0]=500.
    p['execution_contract']['gt_start_delay_minutes']=10.
    return p


def test_response_contingencies_cannot_selectively_hide_an_already_ready_pending_gt(problem):
    p=response_choice_problem(problem)
    c=context(p)
    c['gt_physical_status'][1]='STARTING'
    c['pending_starts'][1]=dict(started=0.,ready=200./900.*60.,requested=200.)
    r=solve(p,readiness=c)
    assert r['action']['gt_electric_kW'][1]>0.
    values={name:case['gt_electric_kW'][1] for name,case in r['joint_diagnostic']['risk_scenarios'].items()
        if name.startswith('response_') and name!='response_GT_2_loss'}
    assert min(values.values())>0.,values
    assert max(values.values())-min(values.values())<1e-8,values


def test_new_gt_at_exact_middle_response_limit_is_ready_in_every_response_copy(problem):
    p=response_choice_problem(problem);c=context(p)
    c['gt_ramp_budget_kW']=150.
    r=api().solve_joint(**p,joint_options=BASE,readiness_context=c,
        coordination_options=dict(response_minutes=40.,delay_bound_minutes=30.),
        tracking_targets=dict(gt_2=150.))
    cap=r['joint_diagnostic']['coordination']['response_envelope']['gt_max_kW'][1]
    assert cap==150.
    assert r['action']['gt_electric_kW'][1]==pytest.approx(cap,abs=1e-8)
    values={name:case['gt_electric_kW'][1] for name,case in r['joint_diagnostic']['risk_scenarios'].items()
        if name.startswith('response_') and name!='response_GT_2_loss'}
    assert min(values.values())>0.,values
    assert max(values.values())-min(values.values())<1e-8,values


def test_support_duration_changes_actual_joint_diesel_preparation_and_carrier_loss(problem):
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][0,0]=700.
    p['availability']['GRID']=0.;p['availability']['GT_2']=0.;p['availability']['GT_3']=0.
    p['state'].gt_prev[:]=[250.,0.,0.];p['state'].diesel_energy_remaining_kWh=60.
    p['state'].diesel_status='CONNECTED';p['diesel_start_available']=True
    c=context(p,diesel_connected=True,diesel_arrived_cap_kW=900.)
    c['arrived_dispatch']=dispatch([250.,0.,0.],grid=0.,diesel=900.)
    short=solve(p,dict(support_minutes=30.),readiness=c)
    long=solve(p,dict(support_minutes=120.),readiness=c)
    sc=short['joint_diagnostic']['risk_scenarios']['GRID_loss']
    lc=long['joint_diagnostic']['risk_scenarios']['GRID_loss']
    assert sc['diesel_kW']>lc['diesel_kW']+50.
    assert sc['unserved_kW'][0][0]<lc['unserved_kW'][0][0]-50.
    np.testing.assert_allclose(short['lost'][:2],long['lost'][:2],atol=1e-5)


def test_pending_target_above_current_derated_capacity_can_be_cancelled(problem):
    p=response_choice_problem(problem);p['availability']['GT_2']=.2
    c=context(p);c['gt_physical_status'][1]='STARTING'
    c['pending_starts'][1]=dict(started=0.,ready=200./900.*60.,requested=200.)
    r=solve(p,readiness=c)
    assert r['action']['gt_electric_kW'][1]==0.
    assert max(map(abs,r['energy_balance_residuals']))<1e-5


def test_healthy_converter_permissions_survive_shared_tower_outage_without_free_cold(problem):
    p=copy.deepcopy(problem);p['availability']['TOWER']=0.
    r=solve(p)
    command=r['action'];used=r['planned_current_action']
    assert command['absorption_cooling_kW']>0.
    assert min(command['electric_chiller_cooling_kW'])>0.
    assert used['absorption_cooling_kW']+sum(used['electric_chiller_cooling_kW'])==0.
    assert r['lost'][0,2]>0.
    assert max(map(abs,r['energy_balance_residuals']))<1e-5
    no_cooling=solve(p,dict(cooling_preparation=False))
    assert 'healthy_converter_permission' not in no_cooling['joint_diagnostic']['objective_order']


def real_day10_pending_context(problem):
    """Development01 C command at minute45, copied from its saved CSV event.

    Its real startup began at37.5 and is ready47.5; a new112.5kW ordinary
    window must not erase that prior startup target135.98950399999995kW.
    """
    p=copy.deepcopy(problem);p['row'].outdoor_temperature_C=6.416666666666667
    p['state'].gt_prev[:]=0.;p['state'].diesel_energy_remaining_kWh=2395.2700032970856
    p['state'].extra_faulted={'GT_1'};p['state'].diesel_status='CONNECTED'
    p['state'].diesel_start_minute=22.5;p['diesel_start_available']=True
    p['availability']['GT_1']=0.;p['availability']['GRID']=0.
    p['execution_contract']['gt_start_delay_minutes']=10.
    p['loads']=np.array([[58.989377160467825,71.75552686691184,29.958868255656306],
        [64.84766000823724,123.16208790996089,21.19187740275785],
        [25.42056046736894,0.,22.058900982686364],[274.7049055603004,0.,0.]])
    c=context(p,observation_time_minute=45.,gt_physical_status=['PROTECTION_LOCKED','STARTING','OFF'],
        earliest_start_minute=[67.5,45.,45.],gt_ramp_budget_kW=112.5,
        diesel_connected=True,diesel_start_minute=22.5)
    c['pending_starts'][1]=dict(started=37.5,ready=47.5,requested=135.98950399999995)
    c['arrived_dispatch']=dispatch([0.,0.,0.],grid=0.,diesel=0.)
    c['arrived_dispatch'].update(boiler_steam_lbph=683.9214553574482,
        absorption_cooling_kW=73.20964664110053,
        electric_chiller_cooling_kW=[73.20964664109958,73.20964664110053,73.20964664109987])
    c['observed_critical_unserved_kW']=[[58.989377160467825,0.,29.958868255656306],
        [64.84766000823724,0.,21.19187740275785]]
    return p,c


def test_m16_keeps_real_pending_target_clock_and_zero_delivery_in_a_short_new_window(problem):
    from test_essential_execution import small_core
    p,c=real_day10_pending_context(problem)
    core,_,f,row=small_core(gt=0.,load=300.,budget=112.5)
    row.outdoor_temperature_C=p['row'].outdoor_temperature_C
    core.time=45.;core.ramp_time=45.;core.ramp_anchor=np.zeros(3)
    core.env.config['gt_ramp_kW_per_hour']=900.
    core.env.execution_contract['gt_start_delay_minutes']=10.
    pending=dict(c['pending_starts'][1]);core.gt_start_pending[1]=dict(pending)
    command=copy.deepcopy(core.executed);command['gt_electric_kW'][1]=pending['requested']
    clamped=core._clamp(command,f,normal_ramp=True)
    assert clamped['gt_electric_kW'][1]==0.
    assert {k:core.gt_start_pending[1][k] for k in pending}==pending
    assert core.ramp_budget==112.5
    core.time=47.5
    completed=core._clamp(command,f,normal_ramp=False)
    assert completed['gt_electric_kW'][1]==pending['requested']
    assert core.gt_start_pending[1]['started']==37.5


def test_c_preserves_real_pending_command_without_crediting_it_as_current_power(problem):
    p,c=real_day10_pending_context(problem)
    r=solve(p,readiness=c)
    assert r['action']['gt_electric_kW'][1]==c['pending_starts'][1]['requested']
    assert r['planned_current_action']['gt_electric_kW'][1]==0.
    assert r['joint_diagnostic']['risk_scenarios']['arrived_current']['gt_electric_kW'][1]==0.


def cold_contact_problem(problem):
    p=copy.deepcopy(problem);p['state'].gt_prev[:]=[128.,0.,0.]
    p['grid_import_limit_kW']=850.;p['availability']['TOWER']=0.
    p['loads'][:]=0.;p['loads'][0]=[200.,0.,100.];p['loads'][1]=[100.,0.,80.]
    return p


@pytest.mark.parametrize('options',[None,dict(cooling_preparation=False),
    dict(time_energy_preparation=False),dict(cooling_preparation=False,time_energy_preparation=False)])
def test_critical_cold_request_stays_connected_while_tower_deficit_remains_real(problem,options):
    p=cold_contact_problem(problem);r=solve(p,options)
    np.testing.assert_allclose(r['lost'][:2,2],p['loads'][:2,2],atol=1e-8)
    np.testing.assert_array_equal(r['command_disconnected'][:2,2],[0.,0.])
    assert r['planned_current_action']['absorption_cooling_kW']+sum(r['planned_current_action']['electric_chiller_cooling_kW'])==0.
    assert max(map(abs,r['energy_balance_residuals']))<1e-5
    np.testing.assert_allclose(r['command_disconnected'][:2,:2],r['lost'][:2,:2],atol=1e-8)
    assert np.all(r['command_disconnected'][2:]<=r['lost'][2:]+1e-8)


def cold_contact_core(p,result,*,arrival_minute,hardware_return_minute,previous_cold_cuts=False):
    """Compile one ordinary command into the real M16 boundary queue."""
    from test_essential_execution import small_core
    core,_,_,_=small_core(gt=128.,load=300.,budget=225.)
    core.env.config=copy.deepcopy(p['config'])
    core.env.config.update(gt_ramp_kW_per_hour=900.,grid_import_limit_kW=850.,solver_time_limit_sec=6.)
    core.env.execution_contract=copy.deepcopy(p['execution_contract'])
    core.env.operating_execution_contract=dict(core.env.execution_contract,diesel_dispatch_mode='arrived_ceiling')
    core.executed=copy.deepcopy(result['action'])
    core.state.gt_prev[:]=core.executed['gt_electric_kW']
    core.connection_basis_loads=p['loads'].copy()
    core.state.disconnected_loads[:]=0.
    if previous_cold_cuts:core.state.disconnected_loads[:2,2]=p['loads'][:2,2]
    core.disconnected_fraction=np.divide(core.state.disconnected_loads,p['loads'],
        out=np.zeros((4,3)),where=p['loads']>0.)
    def data_at(minute):
        availability=copy.deepcopy(p['availability'])
        availability['TOWER']=0. if minute<hardware_return_minute else 1.
        return 1,p['row'],p['loads'].copy(),availability
    core.data_at=data_at
    command=dict(id=1,generated_minute=60.,arrival_minute=arrival_minute,
        dispatch=copy.deepcopy(result['action']),disconnected=result['command_disconnected'].tolist(),
        reference_cooling_kW=float(p['loads'][:,2].sum()),request={},weight=0.)
    core.pending_commands.append(command)
    return core


def cold_contact_actual(core,p,minute):
    core.time=minute;core.boundary()
    _,_,loads,f=core.data_at(minute)
    connected=np.maximum(0.,loads-core.state.disconnected_loads)
    physical=core._solve(connected,f,.25,'critical_cold_contact_regression',core.active_command,
        execution_action=core.executed)
    action=physical['action'];cold=action['absorption_cooling_kW']+sum(action['electric_chiller_cooling_kW'])
    total_unserved=physical['lost']+core.state.disconnected_loads
    return cold,total_unserved


def test_m16_same_arrived_caps_restore_connected_critical_cold_at_hardware_return(problem):
    p=cold_contact_problem(problem);r=solve(p)
    core=cold_contact_core(p,r,arrival_minute=60.,hardware_return_minute=120.)
    for minute in (60.,90.,119.):
        cold,lost=cold_contact_actual(core,p,minute)
        assert cold==0.
        np.testing.assert_allclose(lost[:2,2],p['loads'][:2,2],atol=1e-8)
    held=copy.deepcopy(core.executed);events_before=len(core.events)
    cold,lost=cold_contact_actual(core,p,120.)
    assert cold>=float(p['loads'][:2,2].sum())-1e-6
    assert np.max(lost[:2,2])<1e-6
    assert core.executed==held and not core.pending_commands
    assert not any(e['kind']=='command_arrived' for e in core.events[events_before:])


def test_critical_cold_reconnect_waits_for_the_ordinary_delayed_command(problem):
    p=cold_contact_problem(problem);r=solve(p)
    core=cold_contact_core(p,r,arrival_minute=75.,hardware_return_minute=70.,previous_cold_cuts=True)
    for minute in (60.,70.,74.):
        cold,lost=cold_contact_actual(core,p,minute)
        assert cold==0.
        np.testing.assert_allclose(core.state.disconnected_loads[:2,2],p['loads'][:2,2],atol=1e-8)
        np.testing.assert_allclose(lost[:2,2],p['loads'][:2,2],atol=1e-8)
        assert len(core.pending_commands)==1
    cold,lost=cold_contact_actual(core,p,75.)
    assert cold>=float(p['loads'][:2,2].sum())-1e-6
    assert np.max(lost[:2,2])<1e-6
    assert any(e['kind']=='load_connection_restored' and e['time_minute']==75. for e in core.events)


def fail_named_layer(monkeypatch,p,layer_name,*,corrupt_prior=False):
    """Inject a native failure after real accepted preceding MILP solves."""
    from scipy.optimize import OptimizeResult
    baseline=solve(p)
    layer=next(z for z in baseline['solver_backend_layers'] if z['layer']==layer_name)
    target_rank=layer['rank'];module=api();native=module.solve_layer
    state=dict(failed=False,previous_result=None,remaining_lock_names=[])
    def solve_layer(*args,**kwargs):
        rank=kwargs['rank']
        if rank==target_rank or (layer_name=='operating_cost_and_original_preferences' and rank>target_rank):
            state['failed']=True
            if corrupt_prior:
                # run_layer holds this same native ndarray. Mutate it only
                # after the actual action was extracted, to make the strict
                # optional-fallback validation prove rejection is necessary.
                state['previous_result'].x[11]+=1.
            return OptimizeResult(status=4,x=None,message='forced saved-class finite native rejection',
                backend='injected_failure',attempts=[dict(attempt=1,accepted=False,status=4,
                    error_type='ForcedRegressionFailure',error=layer_name)])
        if state['failed']:
            state['remaining_lock_names'] += [r['name'] for r in kwargs['rows'] if r.get('kind')=='priority_lock']
        answer=native(*args,**kwargs)
        state['previous_result']=answer
        return answer
    monkeypatch.setattr(module,'solve_layer',solve_layer)
    # The cost layer's one unchanged-domain presentation recovery must fail
    # as well; otherwise this would only test its existing successful retry.
    if layer_name=='operating_cost_and_original_preferences':
        monkeypatch.setattr(module,'original_solve_layer',solve_layer)
    return baseline,state


@pytest.mark.parametrize('layer_name',[
    'actual_GT_combination_changes','ordinary_low_priority_disconnected_power',
    'actual_GT_power_change','healthy_converter_permission','coordinated_GRID_permission'])
def test_c4_post_cost_optional_failure_retains_strict_incumbent_without_false_optimum(problem,monkeypatch,layer_name):
    if layer_name=='ordinary_low_priority_disconnected_power':
        problem['engineering_options']=dict(contact_mode='separate')
    elif layer_name=='coordinated_GRID_permission':
        # This preference is only active when the regulated-current endpoint
        # can preserve current service; use the existing real feasible case.
        problem['grid_import_limit_kW']=850.;problem['state'].gt_prev[:]=[200.,0.,200.]
        problem['loads'][:]=0.;problem['loads'][0]=[150.,200.,150.];problem['loads'][3,0]=450.
    baseline,state=fail_named_layer(monkeypatch,problem,layer_name)
    r=solve(problem)
    assert state['failed']
    assert max(map(abs,r['energy_balance_residuals']))<1e-5
    np.testing.assert_allclose(r['lost'][:2],baseline['lost'][:2],atol=1e-8)
    records=[z for z in r['joint_diagnostic']['late_preference_fallbacks'] if z['layer']==layer_name]
    assert len(records)==1
    record=records[0]
    assert record['optional_layer_optimum_claimed'] is False
    assert record['validation']['max_original_violation']<=1e-7
    assert record['validation']['max_lock_violation']<=1e-10
    assert record['validation']['max_integrality_distance']<=1e-9
    result_layer=next(z for z in r['joint_diagnostic']['layers'] if z['name']==layer_name)
    assert result_layer['optimization_status']=='not_optimized_reused_preceding_feasible_incumbent'
    assert result_layer['optional_layer_optimum_claimed'] is False
    assert 'lock.'+layer_name not in state['remaining_lock_names']
    assert any(z['layer']==layer_name and z['status']==4 and z['attempts']
        for z in r['solver_backend_layers'])


def test_c4_held_diagnostic_failure_preserves_exact_command_and_marks_witness_unknown(problem,monkeypatch):
    baseline,state=fail_named_layer(monkeypatch,problem,'held_actual_diagnostic')
    r=solve(problem)
    assert state['failed']
    assert r['action']==baseline['action']
    assert r['planned_current_action']==baseline['planned_current_action']
    np.testing.assert_array_equal(r['command_disconnected'],baseline['command_disconnected'])
    np.testing.assert_array_equal(r['lost'],baseline['lost'])
    d=r['joint_diagnostic'];held=d['risk_scenarios']['held_actual']
    assert held['status']=='unknown_solver_failure'
    for field in ('unserved_kW','absorption_cooling_kW','electric_chiller_cooling_kW',
                  'grid_import_kW','electric_surplus_defect_kW','balance_residuals','steam_balance_residual_lbph'):
        assert held[field] is None
    phase=d['readiness_phases']['held_before_arrival']
    assert phase['unserved_kW'] is None
    assert phase['joint_witness_status']=='unknown_solver_failure'
    assert phase['joint_balance_residuals'] is None
    assert phase['electric_surplus_defect_kW'] is None
    assert phase['grid_import_kW'] is None
    diagnostic_layer=next(z for z in d['layers'] if z['name']=='held_actual_diagnostic')
    assert diagnostic_layer['objective_value'] is None
    assert diagnostic_layer['optional_layer_optimum_claimed'] is False
    assert any(z['layer']=='held_actual_diagnostic' and z['status']==4
        and z['attempts'][0]['error']=='held_actual_diagnostic' for z in r['solver_backend_layers'])


@pytest.mark.parametrize('layer_name',['current_P0','preparation_P0','operating_cost_and_original_preferences'])
def test_c4_mandatory_service_preparation_and_unrecovered_cost_failure_still_raise(problem,monkeypatch,layer_name):
    from hospital_ies.execution import SolverExecutionError
    fail_named_layer(monkeypatch,problem,layer_name)
    with pytest.raises(SolverExecutionError,match=layer_name):
        solve(problem)


def test_c4_optional_held_failure_cannot_retain_a_physically_invalid_prior_vector(problem,monkeypatch):
    from hospital_ies.execution import SolverExecutionError
    fail_named_layer(monkeypatch,problem,'held_actual_diagnostic',corrupt_prior=True)
    with pytest.raises(SolverExecutionError,match='held_actual_diagnostic'):
        solve(problem)


@pytest.mark.parametrize('day',['250','310'])
def test_c4_actual_saved_native_counterexample_keeps_a_valid_current_command(day):
    """The actual failing current input, without reset/step or prefix replay."""
    import json
    from pathlib import Path
    from types import SimpleNamespace
    import pandas as pd
    from hospital_ies.model import Plant
    from hospital_ies.resilience import RuntimeState
    root=Path(__file__).resolve().parents[1]
    path=root/'outputs/v17_state_coordinated_20261018_run01/baseline'/('C3_day'+day+'_current_problem.json')
    p=json.loads(path.read_text(encoding='utf-8'))['inputs']
    p['row']=SimpleNamespace(**p['row']);p['row'].timestamp=pd.Timestamp(p['row'].timestamp)
    p['plant']=Plant(**p['plant'])
    p['state']['gt_prev']=np.asarray(p['state']['gt_prev'])
    p['state']['disconnected_loads']=np.asarray(p['state']['disconnected_loads'])
    p['state']['extra_faulted']=set(p['state']['extra_faulted'])
    p['state']=RuntimeState(**p['state'])
    r=api().solve_joint(**p)
    assert max(map(abs,r['energy_balance_residuals']))<1e-5
    assert any(z['status']==4 for z in r['solver_backend_layers'])
    assert r['joint_diagnostic']['late_preference_fallbacks']
    if day=='310':
        assert r['joint_diagnostic']['risk_scenarios']['held_actual']['status']=='unknown_solver_failure'


def command_energy_problem(problem,*,fuel=300.,actual_diesel=700.,loss_fraction=.02):
    """All GT/GRID unavailable; current DG can serve all four priorities."""
    from tools.essential_fixes.diesel import DieselRotor
    p=copy.deepcopy(problem);p['loads'][:]=0.;p['loads'][:,0]=[100.,80.,100.,200.]
    p['availability'].update(GRID=0.,GT_1=0.,GT_2=0.,GT_3=0.)
    p['state'].gt_prev[:]=0.;p['state'].diesel_energy_remaining_kWh=fuel
    p['state'].diesel_status='CONNECTED';p['state'].last_diesel_kW=actual_diesel
    p['state'].diesel_start_minute=0.;p['diesel_start_available']=True
    rotor=DieselRotor(rated_kW=900.,config=dict(shaft_loss_fraction=loss_fraction))
    rotor.spin_up(900.,0.,1.)
    c=context(p,observation_time_minute=60.,gt_physical_status=['EXOGENOUS_UNAVAILABLE']*3,
        earliest_start_minute=[60.]*3,diesel_connected=True,actual_diesel_kW=actual_diesel,
        diesel_arrived_cap_kW=900.,diesel_start_minute=0.)
    c['arrived_dispatch']=dispatch([0.,0.,0.],grid=0.,diesel=900.)
    c['diesel_local_response']=dict(model_id='M16_essential',rotor=rotor.as_dict(),
        actual_electric_kW=actual_diesel,arrived_ceiling_kW=900.,permission_status='CONNECTED')
    return p,c


def test_c4_actual_command_shaft_budget_precedes_low_priority_but_preserves_critical(problem):
    p,c=command_energy_problem(problem)
    r=solve(p,readiness=c);d=r['joint_diagnostic'];order=d['objective_order']
    assert max(np.ravel(r['lost'][:2]))<.001
    assert r['action']['diesel_kW']<300.
    assert r['planned_current_action']['diesel_kW']<=r['action']['diesel_kW']+1e-7
    assert r['lost'][2:,0].sum()>180.
    assert order.index('preparation_P1')<order.index('actual_command_shaft_support')<order.index('current_P2')
    assert order.index('arrived_current_P1')<order.index('actual_command_shaft_support')
    support=d['coordination']['actual_command_support']
    assert support['active'] is True
    assert support['budget_excess_kWh']<1e-6
    assert support['budget_requirement_kWh']<=p['state'].diesel_energy_remaining_kWh+1e-6
    assert support['support_minutes']==60.
    assert support['uses_command_diesel_ceiling'] is True


def test_c4_positive_support_excess_is_not_a_hard_cap_or_fuel_debit(problem):
    p,c=command_energy_problem(problem,fuel=100.)
    before=p['state'].diesel_energy_remaining_kWh
    r=solve(p,readiness=c)
    assert max(np.ravel(r['lost'][:2]))<.001
    assert r['action']['diesel_kW']>=179.99
    support=r['joint_diagnostic']['coordination']['actual_command_support']
    assert support['budget_excess_kWh']>100.
    assert support['static_duration_budget_satisfied'] is False
    assert support['actual_supply_guaranteed'] is False
    assert p['state'].diesel_energy_remaining_kWh==before
    assert max(map(abs,r['energy_balance_residuals']))<1e-5


def test_c4_support_overhead_deficit_retains_signed_rhs_and_original_normalized_locks(problem):
    p,c=command_energy_problem(problem,fuel=1.)
    prepared=solve(p,readiness=c)
    ablated=solve(p,dict(time_energy_preparation=False),readiness=c)
    np.testing.assert_allclose(prepared['lex_values'][:2],ablated['lex_values'][:2],atol=1e-10)
    support=prepared['joint_diagnostic']['coordination']['actual_command_support']
    overhead=prepared['joint_diagnostic']['coordination']['diesel_support']['overhead_shaft_work_kWh']
    assert overhead>p['state'].diesel_energy_remaining_kWh
    required=prepared['action']['diesel_kW']+overhead
    assert support['budget_requirement_kWh']==pytest.approx(required,abs=1e-8)
    assert support['budget_excess_kWh']==pytest.approx(required-1.,abs=1e-7)
    assert support['static_duration_budget_satisfied'] is False


def test_c4_no_time_energy_ablation_removes_actual_cap_preference(problem):
    p,c=command_energy_problem(problem)
    prepared=solve(p,readiness=c)
    ablated=solve(p,dict(time_energy_preparation=False),readiness=c)
    assert prepared['action']['diesel_kW']<ablated['action']['diesel_kW']-150.
    assert ablated['action']['diesel_kW']>479.99
    assert ablated['lost'][2:,0].sum()<.001
    assert 'actual_command_shaft_support' not in ablated['joint_diagnostic']['objective_order']
    assert ablated['joint_diagnostic']['coordination']['actual_command_support']['active'] is False
    np.testing.assert_allclose(prepared['lost'][:2],ablated['lost'][:2],atol=1e-5)


def test_c4_actual_cap_uses_support_duration_and_real_shaft_loss(problem):
    p,c=command_energy_problem(problem)
    short=solve(p,dict(support_minutes=30.),readiness=c)
    long=solve(p,dict(support_minutes=60.),readiness=c)
    lossy,lossy_context=command_energy_problem(problem,loss_fraction=.04)
    higher_loss=solve(lossy,dict(support_minutes=60.),readiness=lossy_context)
    assert short['action']['diesel_kW']>long['action']['diesel_kW']+150.
    assert long['action']['diesel_kW']>higher_loss['action']['diesel_kW']+20.
    for r in (short,long,higher_loss):
        assert max(np.ravel(r['lost'][:2]))<.001
        assert r['joint_diagnostic']['coordination']['actual_command_support']['uses_command_diesel_ceiling'] is True


def test_c4_reduced_diesel_cap_waits_for_real_ordinary_arrival_and_changes_actual_supply(problem):
    from test_essential_execution import small_core
    p,c=command_energy_problem(problem);r=solve(p,readiness=c)
    core,_,_,_=small_core(gt=0.,load=480.,budget=225.)
    core.env.config=copy.deepcopy(p['config']);core.env.config['solver_time_limit_sec']=6.
    core.env.execution_contract=copy.deepcopy(p['execution_contract'])
    core.env.operating_execution_contract=dict(core.env.execution_contract,diesel_dispatch_mode='arrived_ceiling')
    core.data_at=lambda minute:(1,p['row'],p['loads'].copy(),copy.deepcopy(p['availability']))
    core.state.diesel_status='CONNECTED';core.state.diesel_energy_remaining_kWh=300.
    core.state.diesel_start_minute=0.;core.state.gt_prev[:]=0.
    core.executed=copy.deepcopy(r['action']);core.executed['diesel_kW']=900.
    core.state.disconnected_loads[:]=0.;core.disconnected_fraction[:]=0.
    core.connection_basis_loads=p['loads'].copy()
    core.pending_commands=[dict(id=1,generated_minute=60.,arrival_minute=75.,dispatch=copy.deepcopy(r['action']),
        disconnected=r['command_disconnected'].tolist(),reference_cooling_kW=0.,request={},weight=0.)]
    for minute in (60.,74.):
        core.time=minute;core.boundary()
        assert core.executed['diesel_kW']==900.
        before=core._solve(p['loads'],p['availability'],.25,'before_ordinary_DG_cap_arrival',None,
            execution_action=core.executed)
        assert before['action']['diesel_kW']>479.99
        # M16 retains the original normalized P3 remainder (~0.0048 kW here).
        # Critical service and delivered DG power are the relevant controls.
        assert max(np.ravel(before['lost'][:2]))<.001
    core.time=75.;core.boundary()
    assert core.executed['diesel_kW']==pytest.approx(r['action']['diesel_kW'],abs=1e-8)
    connected=p['loads']-core.state.disconnected_loads
    after=core._solve(connected,p['availability'],.25,'after_ordinary_DG_cap_arrival',core.active_command,
        execution_action=core.executed)
    assert after['action']['diesel_kW']<300.
    assert max(np.ravel(after['lost'][:2]+core.state.disconnected_loads[:2]))<.001
    assert core.state.disconnected_loads[2:,0].sum()+after['lost'][2:,0].sum()>180.

