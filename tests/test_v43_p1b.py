"""P1B execution contracts: fixed incidents, explicit capabilities, no P2 data."""
import copy,json,sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.resilience import empty_faults,RuntimeState,_solve_milp,update_thermal
def fixed_scenarios():
    # P2A migration: frozen ORIGINAL incidents, independent of repaired sampler.
    return json.loads((ROOT/'tests/fixtures/p1b_fixed_scenarios.json').read_text(encoding='utf-8'))[:8]

def profile(**overrides):
    p=json.loads((ROOT/'configs/p1b_execution_research.json').read_text())
    p.update(overrides);return p

def l4():
    return dict(id='testL4',stage=4,start_hour=4300,duration_steps=2,step_minutes=15,
        shock={'e':.85,'c':.55},enable_propagation=True,pre_event_warmup_steps=8,
        grid_limit_kW=250,response_delay_steps=2,automatic_fast_shedding=True)

def run_case(env,s,policy='priority_milp'):
    obs,_=env.reset(scenario=copy.deepcopy(s),seed=20261012)
    initial=copy.deepcopy(env.core.executed)
    for _ in range(s['duration_steps']):
        obs,_,done,_,_=env.step({} if policy=='priority_milp' else {'security':{'gt_loading_penalty_USD_per_kWh':.35}})
    assert done
    return initial,env.traces()[0]

def test_original_l4_completes_and_cold_proxy_is_not_electric_shedding():
    env=HospitalIESEnv(ROOT,execution_contract=profile())
    _,trace=run_case(env,l4())
    assert len(trace)==2 and not env.state.extra_faulted
    events=[x for x in env.event_history if x['kind']=='automatic_load_shed']
    assert events
    for e in events:
        assert e['actual_electric_disconnected_kW']<=e['real_electric_request_kW']+1e-4
        assert e['cold_proxy_kW']>=0
    assert max(max(map(abs,s['energy_balance_residuals'])) for s in env.segment_history)<=1e-4

def test_original_smoke_8_families_16_trajectories_complete():
    env=HospitalIESEnv(ROOT,execution_contract=profile())
    scenarios=fixed_scenarios()
    assert len(scenarios)==8
    for s in scenarios:
        frozen=copy.deepcopy(s);initials=[]
        for policy in ('priority_milp','secure_milp'):
            initial,trace=run_case(env,s,policy);initials.append(initial)
            assert len(trace)==6 and env.steps_done==6
            assert not env.solver_errors
            assert max(max(map(abs,x['energy_balance_residuals'])) for x in env.segment_history)<=1e-4
        assert initials[0]==initials[1] and s==frozen

def test_strict_fixed_original_l3_keeps_model_infeasibility():
    from hospital_ies.execution import ModelConstraintInfeasible
    env=HospitalIESEnv(ROOT)
    s=next(x for x in fixed_scenarios() if x['id']=='smoketest_L3_0000_h3255')
    with pytest.raises(ModelConstraintInfeasible):run_case(env,s)
    assert env.solver_errors[-1]['category']=='model_constraint_infeasible'
    assert env.core.time==60. and len(env.history)==4

def test_original_l3_interlock_is_not_a_hidden_down_ramp():
    env=HospitalIESEnv(ROOT,execution_contract=profile())
    s=next(x for x in fixed_scenarios() if x['id']=='smoketest_L3_0000_h3255')
    run_case(env,s)
    ev=[x for x in env.event_history if x['kind']=='no_export_interlock']
    assert ev and ev[0]['time_minute']==60.
    assert ev[0]['power_surplus_kW']>200.
    assert ev[0]['command_arrival_minute']==75.
    assert not [x for x in env.event_history if x['kind']=='command_executed' and 30<=x['time_minute']<75]
    assert any(x['trigger']=='no_export_interlock' for x in env.trip_events)
    assert 'GT_1' in env.traces()[0].iloc[4]['new_trips']

@pytest.mark.parametrize('delay',[.01,1.])
def test_local_action_too_late_has_no_invented_energy_sink(delay):
    from hospital_ies.execution import ModelConstraintInfeasible
    env=HospitalIESEnv(ROOT,execution_contract=profile(surplus_interlock_delay_minutes=delay))
    s=next(x for x in fixed_scenarios() if x['id']=='smoketest_L3_0000_h3255')
    with pytest.raises(ModelConstraintInfeasible):run_case(env,s)
    assert env.core.time==60. and env.steps_done==4
    assert not any(x['kind']=='no_export_interlock' for x in env.event_history)

def test_limited_down_ramp_and_minimum_output():
    from hospital_ies.execution import limited_down_endpoint
    p=np.array([300.,150.,0.]);floor=np.array([124.,124.,124.]);target=np.zeros(3)
    q=limited_down_endpoint(p,target,floor,900.,.25)
    np.testing.assert_allclose(q,[296.25,146.25,0.],atol=1e-8,rtol=0)
    q=limited_down_endpoint(p,target,floor,900.,30.)
    np.testing.assert_allclose(q,[124.,124.,0.],atol=1e-8,rtol=0)

def test_environment_change_cannot_raise_held_gt_to_new_minimum():
    env=HospitalIESEnv(ROOT,execution_contract=profile())
    s=next(x for x in fixed_scenarios() if x['id']=='smoketest_L3_0000_h3255')
    run_case(env,s)
    ev=[x for x in env.event_history if x['kind']=='minimum_output_interlock']
    assert ev and ev[0]['time_minute']==60.
    assert ev[0]['actual_kW']<ev[0]['minimum_kW']

def test_solver_error_is_not_model_infeasibility_or_p0_failure(monkeypatch):
    from hospital_ies.execution import SolverExecutionError
    import hospital_ies.state_transition as tr
    env=HospitalIESEnv(ROOT,execution_contract=profile());env.reset(scenario=l4())
    def fail(*args,**kwargs):raise TimeoutError('P1B injected timeout')
    monkeypatch.setattr(tr,'_solve_milp',fail)
    with pytest.raises(SolverExecutionError):env.step({})
    assert env.solver_errors[-1]['category']=='solver_exception'
    assert not env.history and env.steps_done==0 and not env.state.extra_faulted

@pytest.fixture(scope='module')
def unit_env():return HospitalIESEnv(ROOT)

def basic(**kw):
    s=dict(id='P1B_isolated',stage=1,start_hour=5456,duration_steps=4,step_minutes=15,
        enable_propagation=False,faults={},response_delay_minutes=0)
    s.update(kw);return s

def test_diesel_start_ats_permissions_failure_recovery_and_empty(unit_env):
    e=unit_env;e.execution_contract=profile(diesel_mode='emergency_bus',ats_transfer_minutes=2.)
    e.reset(scenario=basic(faults={'GRID':0.},fault_duration_minutes=60))
    for t,status in [(0,'STARTING'),(14,'STARTING'),(15,'READY'),(17,'CONNECTED')]:
        e.core.time=t;e.core.boundary()
        assert e.state.diesel_status==status
        assert e.core.diesel_available(t,e.core.data_at(t)[3])==(t>=17)
    f=e.core.data_at(17)[3];f['DIESEL']=0
    e.core._update_support_states(f);assert e.state.diesel_status=='FAILED'
    f['DIESEL']=1;e.core.time=20;e.core._update_support_states(f)
    assert e.state.diesel_status=='STARTING'
    e.state.diesel_energy_remaining_kWh=0;e.core._update_support_states(f)
    assert e.state.diesel_status=='EMPTY'
    e.reset(scenario=basic(diesel_on_grid_allowed=True));e.core.time=30;e.core.boundary()
    assert e.state.diesel_status=='OFF' and not e.core.diesel_available(30,empty_faults())

def test_emergency_diesel_cannot_supply_other_priorities_or_auxiliaries(unit_env):
    e=unit_env;row=next(e.hourly.iloc[[5455]].itertuples(index=False));f=empty_faults()
    for key in ['GT_1','GT_2','GT_3','GRID']:f[key]=0.
    loads=np.zeros((4,3));loads[0,0]=20.;loads[3,0]=300.;loads[3,2]=200.
    state=RuntimeState(np.zeros(3),2400,set(),np.zeros(3),{})
    x=_solve_milp(row,e.plant,loads,f,state,.25,850,True,900,config=e.config,
        execution_contract=profile(diesel_mode='emergency_bus',diesel_allowed_priorities=['P0']))
    assert x['action']['diesel_kW']<=20+1e-4
    assert x['lost'][3,0]>=300-1e-4 and x['lost'][3,2]>=200-1e-4

def test_startup_delay_and_reset_isolation(unit_env):
    e=unit_env;e.execution_contract=profile(gt_start_delay_minutes=2.)
    e.reset(scenario=basic());e.core.executed['gt_electric_kW']=[0.,0.,0.];e.state.gt_prev[:]=0.
    a=copy.deepcopy(e.core.executed);a['gt_electric_kW']=[200.,0.,0.]
    one=e.core._clamp(a,empty_faults(),normal_ramp=True)
    assert one['gt_electric_kW'][0]==0 and e.core.gt_start_pending[0]['ready']>=2.
    ready=e.core.gt_start_pending[0]['ready']
    e.core.time=2;e.core.boundary();assert e.core.executed['gt_electric_kW'][0]==0.
    e.core.time=ready;e.core.boundary()
    assert 0<e.core.executed['gt_electric_kW'][0]<=900.*ready/60+1e-4
    e.reset(scenario=basic())
    assert e.core.gt_start_pending=={} and e.core.pending_shed is None and e.core.local_down is None

def test_attributed_sensible_and_process_cold_are_distinct(unit_env):
    e=unit_env;e.execution_contract=profile(thermal_mode='attributed')
    name='ICU_FLR_2';temps=[]
    for sensible,process in [(0,0),(20,0),(40,0),(0,40)]:
        e.reset(scenario=basic(thermal_disturbance={'sensible_kW':{name:sensible},'process_cold_kW':{'P0':process}}))
        h,_,loads,_=e.core.data_at(0)
        params,byhour=e.thermal_cache
        update_thermal(e.state,params,byhour[h],loads.copy(),loads,.25,2.,
            attribution=e.core.thermal_attribution(0))
        temps.append(e.state.thermal_delta[name])
    assert temps[2]>temps[1]>temps[0] and temps[3]==pytest.approx(temps[0],abs=1e-8)

def test_automatic_delay_and_recovery_are_logged(unit_env):
    e=unit_env;e.execution_contract=profile(automatic_shedding_delay_minutes=2.)
    e.reset(scenario=l4());e.step({})
    ev=[x for x in e.event_history if x['kind']=='automatic_load_shed']
    assert ev and ev[0]['time_minute']==2.
    e.step({})
    assert any(x['kind']=='load_connection_restored' for x in e.event_history)

def test_finite_local_down_energy_and_step_halving(unit_env):
    results=[]
    for resolution in (.25,.125):
        e=unit_env;e.execution_contract=profile(local_max_segment_minutes=resolution)
        e.reset(scenario=basic(stage=2,shock={},response_delay_minutes=90,automatic_fast_shedding=False))
        # An explicit initial source setpoint with an arrived import ceiling.
        # The grid port absorbs a FINITE GT decrease; it cannot export.
        e.core.executed['grid_import_kW']+=50.
        initial=np.array(e.core.executed['gt_electric_kW'])
        e.step({})
        e.core.advance(20.) # unchanged controls, five-minute steady tail
        moving=[s for s in e.segment_history if s['execution_mode']=='finite_local_down']
        assert moving and moving[0]['start_minute']==.25
        assert not any(x['kind'].endswith('interlock') for x in e.event_history)
        completed=[x for x in e.event_history if x['kind']=='local_down_completed']
        assert all(x['time_minute']==x['completed_minute'] for x in completed)
        # A tolerance-sized unserved amount is not reusable local response.
        np.testing.assert_allclose(e.segment_history[-1]['unserved_kW'],e.segment_history[-2]['unserved_kW'],atol=1e-4,rtol=0)
        for s in moving:
            dt=(s['end_minute']-s['start_minute'])/60
            change=np.array(s['source_start_gt_kW'])-np.array(s['source_end_gt_kW'])
            assert np.all(change>=-1e-6) and np.all(change<=900*dt+1e-4)
            np.testing.assert_allclose(s['action']['gt_electric_kW'],(np.array(s['source_start_gt_kW'])+s['source_end_gt_kW'])/2,atol=1e-6,rtol=0)
        results.append((np.sum([np.r_[s['action']['gt_electric_kW'],s['action']['grid_import_kW']]*
            (s['end_minute']-s['start_minute'])/60 for s in e.segment_history],axis=0),copy.deepcopy(e.state.thermal_delta)))
    np.testing.assert_allclose(results[0][0],results[1][0],atol=1e-4,rtol=0)
    np.testing.assert_allclose(list(results[0][1].values()),list(results[1][1].values()),atol=1e-5,rtol=0)

def test_aux_diagnostic_error_is_retained(unit_env,monkeypatch):
    import hospital_ies.state_transition as tr
    from hospital_ies.execution import SolverExecutionError
    e=unit_env;e.execution_contract=profile();e.reset(scenario=l4())
    def fail(*a,**k):raise SolverExecutionError('aux diagnostic injected')
    monkeypatch.setattr(tr,'maximum_auxiliary_sink',fail)
    with pytest.raises(SolverExecutionError) as caught:e.step({})
    assert e.core.failed and e.solver_errors[-1]['phase']=='surplus_diagnostic'
    assert caught.value.simulation_diagnostic==e.solver_errors[-1] and not e.history

def test_disconnected_contacts_do_not_reconnect_on_load_rebound(unit_env):
    e=unit_env;e.execution_contract=profile();e.reset(scenario=basic())
    loads=e.core.data_at(0)[2];e.state.disconnected_loads[3,0]=.1*loads[3,0]
    e.core.automatic_disconnected[3,0]=.1*loads[3,0]
    for ratio in (.05,0.,1.,2.):
        changed=loads*ratio;e.core._sync_connections(changed)
        assert e.state.disconnected_loads[3,0]==pytest.approx(.1*changed[3,0])
        assert e.core.automatic_disconnected[3,0]==pytest.approx(.1*changed[3,0])
    assert not any(x['kind']=='load_connection_restored' for x in e.event_history)

def test_reset_reference_solver_error_is_retained(unit_env,monkeypatch):
    import hospital_ies.control_env as ce
    e=unit_env
    def fail(*args,**kwargs):raise TimeoutError('reference reset injected')
    monkeypatch.setattr(ce,'solve_hour',fail)
    with pytest.raises(TimeoutError) as caught:e.reset(scenario=basic())
    assert e.terminated and e.core is None and e.solver_errors
    assert e.solver_errors[-1]['phase']=='reference_initialization'
    assert caught.value.simulation_diagnostic==e.solver_errors[-1]

@pytest.mark.parametrize('mode',['legacy_explicit_permission','emergency_bus'])
def test_diesel_recovery_restarts_for_both_modes(unit_env,mode):
    e=unit_env;e.execution_contract=profile(diesel_mode=mode)
    e.reset(scenario=basic(faults={'GRID':0.},fault_duration_minutes=60))
    f=e.core.data_at(0)[3];e.core.boundary();e.core.time=15;e.core.boundary()
    assert e.core.diesel_available(15,f)
    f['DIESEL']=0;e.core.time=20;e.core._update_support_states(f)
    f['DIESEL']=1;e.core.time=21;e.core._update_support_states(f)
    assert not e.core.diesel_available(21,f)

def test_start_larger_request_extends_ready_time(unit_env):
    e=unit_env;e.execution_contract=profile(gt_start_delay_minutes=2.)
    e.reset(scenario=basic());e.core.executed['gt_electric_kW']=[0.,0.,0.]
    a=copy.deepcopy(e.core.executed);a['gt_electric_kW']=[130.,0.,0.]
    e.core._clamp(a,empty_faults(),True);first=e.core.gt_start_pending[0]['ready']
    a['gt_electric_kW']=[220.,0.,0.];e.core._clamp(a,empty_faults(),True)
    assert e.core.gt_start_pending[0]['ready']>first

def test_start_short_command_interval_keeps_valid_online_target(unit_env):
    e=unit_env;e.execution_contract=profile(gt_start_delay_minutes=2.)
    e.reset(scenario=basic());e.core.executed['gt_electric_kW']=[0.,0.,0.]
    a=copy.deepcopy(e.core.executed);a['gt_electric_kW']=[130.,0.,0.]
    e.core._clamp(a,empty_faults(),True)
    e.core.time=2.;a['gt_electric_kW']=[200.,0.,0.]
    result=e.core._clamp(a,empty_faults(),True)
    assert result['gt_electric_kW'][0]==0.
    assert e.core.gt_start_pending[0]['requested']==200.
    assert e.core.gt_start_pending[0]['ready']==pytest.approx(200/900*60,abs=1e-8)

def test_no_fault_objective_matches_frozen_p1a(unit_env):
    import importlib.util
    path=ROOT/'outputs/v43_p1b_20261012_235940/.before/src/hospital_ies/resilience.py'
    spec=importlib.util.spec_from_file_location('hospital_ies.p1a_reference',path)
    old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
    e=unit_env
    for h in (4300,5456,3255):
        row=next(e.hourly.iloc[[h-1]].itertuples(index=False))
        from hospital_ies.resilience import loads_for_step
        loads=loads_for_step(e.groups.loc[h].to_dict(),{})
        state=RuntimeState(np.array([150.,150.,150.]),2400.,set(),np.zeros(3),{})
        args=(row,e.plant,loads,empty_faults(),state,.25,850.,False,900.)
        a=_solve_milp(*args,config=e.config);b=old._solve_milp(*args,config=e.config)
        assert a['cost_USD_step']==pytest.approx(b['cost_USD_step'],rel=1e-6,abs=1e-4)
        np.testing.assert_allclose(a['lost'],b['lost'],atol=1e-4,rtol=0)
        assert max(map(abs,a['energy_balance_residuals']))<=1e-4

def test_p1b_both_entrypoints_same_energy(unit_env):
    from hospital_ies.resilience import simulate_scenario
    e=unit_env;e.execution_contract=profile();_,trace=run_case(e,l4())
    other,_,summary=simulate_scenario(ROOT,l4(),e.plant,e.hourly,e.groups.reset_index(),e.zones,e.config,e.thermal_cache,execution_contract=profile())
    cols=[f'{g}_{k}_unserved_kW' for g in ('P0','P1','P2','P3') for k in ('e','h','c')]
    np.testing.assert_allclose(trace[cols],other[cols],atol=1e-6,rtol=0)
    assert summary['event_trace']==e.event_history

def test_protection_preview_does_not_reset_accumulator(unit_env):
    e=unit_env;e.execution_contract=profile();e.reset(scenario=basic())
    e.state.overload_minutes[:]=7.
    e.core._protection(np.zeros((4,3)),0.)
    np.testing.assert_allclose(e.state.overload_minutes,[7.,7.,7.],atol=0,rtol=0)

def test_local_ramp_threshold_is_split_at_actual_crossing(unit_env,monkeypatch):
    e=unit_env;e.execution_contract=profile();e.reset(scenario=basic())
    h,row,loads,f=e.core.data_at(0);before=copy.deepcopy(e.state.overload_minutes)
    def pure_solve(loads,f,dt,phase,cmd,**kwargs):return {'action':kwargs['execution_action']}
    def stress(loads,sol):return {'GT_1':sol['action']['gt_electric_kW'][0]/320.}
    monkeypatch.setattr(e.core,'_solve',pure_solve);monkeypatch.setattr(e.core,'_stress_for_solution',stress)
    # Isolated integration boundary: fixed absolute seconds, not step counts.
    t=e.core._ramp_threshold_boundary(loads,f,row,.25,{},np.array([338.,0,0]),np.array([334.,0,0]))
    expected=(338.-320.*(1.05+1e-9))/4.*.25 # existing strict comparison tolerance
    assert t==pytest.approx(expected,abs=1e-8)
    np.testing.assert_allclose(e.state.overload_minutes,before,atol=0,rtol=0)

def test_shed_request_cancels_when_deficit_clears(unit_env):
    e=unit_env;e.execution_contract=profile(automatic_shedding_delay_minutes=2.)
    e.reset(scenario=l4());loads=e.core.data_at(0)[2]
    loss=np.zeros((4,3));loss[3,0]=10.
    e.core._automatic_shed(loads,dict(lost=loss));assert e.core.pending_shed['arrival']==2.
    e.core.time=1;e.core._automatic_shed(loads,dict(lost=np.zeros((4,3))))
    assert e.core.pending_shed is None
    e.core.time=10;e.core._automatic_shed(loads,dict(lost=loss))
    assert e.core.pending_shed['arrival']==12. and not any(x['kind']=='automatic_load_shed' for x in e.event_history)

def test_one_shedding_request_cannot_repeat_on_numerical_subsegments(unit_env):
    e=unit_env;e.execution_contract=profile();e.reset(scenario=l4())
    loads=e.core.data_at(0)[2];loss=np.zeros((4,3));loss[3,0]=10.
    e.core._automatic_shed(loads,dict(lost=loss));first=e.state.disconnected_loads.copy()
    for _ in range(5):e.core._automatic_shed(loads,dict(lost=loss))
    np.testing.assert_allclose(e.state.disconnected_loads,first,atol=1e-8,rtol=0)
    assert len([x for x in e.event_history if x['kind']=='automatic_load_shed'])==1

def test_diesel_port_energy_and_grid_recovery(unit_env):
    e=unit_env;e.execution_contract=profile(diesel_mode='emergency_bus')
    e.reset(scenario=basic(faults={'GRID':0.},fault_duration_minutes=40.,response_delay_minutes=90.))
    e.core.boundary();e.core.time=15;e.core.boundary()
    e.core.executed['gt_electric_kW']=[0.,0.,0.];e.core.executed['grid_import_kW']=0.
    e.core.executed['diesel_kW']=5.;e.state.diesel_energy_remaining_kWh=1.
    e.core.advance(30.)
    delivered=sum(s['action']['diesel_kW']*(s['end_minute']-s['start_minute'])/60 for s in e.segment_history)
    assert delivered==pytest.approx(1.,abs=1e-6) and e.state.diesel_energy_remaining_kWh==0.
    assert e.state.diesel_status=='EMPTY' and e.core.executed['diesel_kW']==0.
    assert any(s['end_minute']==27. for s in e.segment_history)
    e.state.diesel_energy_remaining_kWh=100.;e.core.time=40.;e.core.boundary()
    assert e.state.diesel_status=='OFF' and e.core.executed['diesel_kW']==0.

@pytest.mark.parametrize('remaining',[.1,.2])
def test_real_local_ramp_protection_halving(unit_env,remaining):
    e=unit_env;original=e.groups.copy(deep=True);results=[]
    try:
        for resolution in (.25,.125):
            e.execution_contract=profile(local_max_segment_minutes=resolution)
            e.reset(scenario=basic(stage=4,shock={},enable_propagation=True,response_delay_minutes=90.))
            # Isolated constant load test, not any of the original incidents.
            loads=np.zeros((4,3));loads[0]=[1000.,0.,2000.]
            for i,g in enumerate(('P0','P1','P2','P3')):
                for k,c in enumerate(('e','h','c')):e.groups.loc[5456,f'{g}_{c}_kW']=loads[i,k]
            e.core.connection_basis_loads=loads.copy();e.state.disconnected_loads[:]=0.
            a=e.core.executed;a['gt_electric_kW']=[300.,300.,300.];a['grid_import_kW']=200.
            a['diesel_kW']=0.;a['boiler_steam_lbph']=0.;a['absorption_cooling_kW']=0.;a['electric_chiller_cooling_kW']=[0.,0.,0.]
            e.core._commit_sources(a);e.core.local_down=dict(arrival=0.,target=[296.25,300.,300.])
            measured=e.core._solve(loads,empty_faults(),.25/60,'test_initial_port',{},execution_action=a)
            from hospital_ies.model import RT_TO_KW
            factor=e.plant.ec_electric_kW_per_rt/RT_TO_KW*e.config['precontrol_cooling_to_el_fraction']
            transfer=(320.*(e.config['gt_protection_trip_multiple']+1e-9)+1.875-300.)*3
            e.state.cooling_reference_kW=2000.-(transfer-float(measured['lost'][:,0].sum()))/factor
            assert e.state.cooling_reference_kW>=0.
            e.state.overload_minutes[:]=0.;e.state.overload_minutes[0]=15.-remaining
            seg=e.core.advance(.25)
            times=[x['trip_time_minute'] for x in e.trip_events if x['device']=='GT_1']
            if remaining==.1:assert times==pytest.approx([.1],abs=1e-8)
            else:assert not times and 'GT_1' not in e.state.extra_faulted
            results.append((times,np.sum([np.asarray(x['unserved_kW'])*(x['end_minute']-x['start_minute'])/60 for x in seg],axis=0),
                np.sum([np.r_[x['action']['gt_electric_kW'],x['action']['grid_import_kW']]*(x['end_minute']-x['start_minute'])/60 for x in seg],axis=0),copy.deepcopy(e.state.thermal_delta)))
            e.groups=original.copy(deep=True)
        np.testing.assert_allclose(results[0][1],results[1][1],atol=1e-4,rtol=0)
        np.testing.assert_allclose(results[0][2],results[1][2],atol=1e-4,rtol=0)
        np.testing.assert_allclose(list(results[0][3].values()),list(results[1][3].values()),atol=1e-5,rtol=0)
    finally:e.groups=original
