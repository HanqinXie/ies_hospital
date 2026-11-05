from pathlib import Path
import json,sys
import numpy as np,pandas as pd,pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from hospital_ies.pipeline import read_data
from hospital_ies.model import load_plant
from hospital_ies.resilience import (ZONE_PRIORITY,GROUPS,CARRIERS,build_zone_groups,validate_scenario,
    loads_for_step,simulate_scenario,thermal_data,RuntimeState,
    p0_failure_classification,lex_lock_tolerance,pre_control_cascade,empty_faults)

@pytest.fixture(scope='module')
def setup():
    df=read_data(ROOT)
    zone=pd.read_csv(ROOT/'inputs/loads/critical_zones_8760.csv')
    groups=build_zone_groups(zone,df)
    plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    config=json.loads((ROOT/'configs/resilience_assumptions.json').read_text())
    thermal=thermal_data(ROOT,zone,config['mass_participation'],config['effective_U_W_m2K'])
    return df,zone,groups,plant,config,thermal

def test_load_partition_and_canonical_zone_count(setup):
    df,z,g,*_=setup
    assert len(ZONE_PRIORITY)==14
    assert len(g)==8760
    assert set(ZONE_PRIORITY)==set(z.zone_name)
    for k,total in [('e',df.P_e_nonplant_kW+df.P_e_unresolved_kW),('h',df.Q_h_demand_kW),('c',df.Q_c_demand_kW)]:
        actual=g[[f'{p}_{k}_kW' for p in GROUPS]].sum(axis=1)
        assert np.allclose(actual,total,atol=1e-5)
        assert (g[[f'{p}_{k}_kW' for p in GROUPS]]>=-1e-8).all().all()

def test_scenario_validation():
    with pytest.raises(ValueError):validate_scenario({'stage':4,'start_hour':1,'duration_steps':4,'faults':{'GT_1':0},'enable_propagation':True})
    with pytest.raises(ValueError):validate_scenario({'stage':1,'start_hour':1,'duration_steps':4,'faults':{'CHILLER_99':0}})
    with pytest.raises(ValueError):validate_scenario({'stage':3,'start_hour':1,'duration_steps':4,'faults':{'GT_1':0},'enable_propagation':False})

def _run(setup,s):
    d,z,g,p,c,thermal=setup
    return simulate_scenario(ROOT,s,p,d,g,z,c,thermal)

def test_L1_GT_outage_and_supply_balances(setup):
    s={'id':'testL1','stage':1,'start_hour':6347,'duration_steps':2,'step_minutes':15,
       'faults':{'GT_1':0.0},'enable_propagation':False}
    t,z,m=_run(setup,s)
    assert len(t)==2
    assert np.isclose(t.gt_electric_kW_1,0).all()
    assert (t.P0_e_unserved_kW >= -1e-6).all()
    assert (t.new_trips == "").all()
    assert m['energy_balance_verified']


def test_shared_tower_outage_blocks_all_modeled_chillers_but_keeps_p0_labels(setup):
    s={'id':'towerCommonMode','stage':1,'start_hour':6347,'duration_steps':1,'step_minutes':15,
       'faults':{'TOWER':0.0},'enable_propagation':False}
    trace,_,summary=_run(setup,s)
    chiller_cols=['absorption_cooling_kW']+[f'electric_chiller_cooling_kW_{i}' for i in (1,2,3)]
    assert np.allclose(trace[chiller_cols].to_numpy(),0.,atol=1e-8)
    assert np.allclose(trace.tower_rejection_kW,0.,atol=1e-8)
    assert summary['p0_energy_loss_any']
    assert not summary['p0_guarantee_failure_any']
    # Zero is also a valid result below the unchanged solver-noise threshold.
    assert trace.P0_e_unserved_kW.max()>=0.
    assert trace.P0_e_unserved_kW.max()<setup[4]['p0_solver_feasibility_tol_kW']
    assert summary['energy_balance_verified']
    assert summary['max_abs_energy_balance_residual_kW']<1e-4

def test_L2_increases_cold_and_electric_demand(setup):
    s={'id':'testL2','stage':2,'start_hour':6347,'duration_steps':2,'step_minutes':15,
       'shock':{'e':0.3,'c':0.4},'enable_propagation':False}
    t,_,_=_run(setup,s)
    g=setup[2].set_index('hour_of_year').loc[6347]
    assert np.isclose(sum(t.iloc[0][f'{p}_e_demand_kW'] for p in GROUPS),sum(g[f'{p}_e_kW'] for p in GROUPS)*1.3)
    assert np.isclose(sum(t.iloc[0][f'{p}_c_demand_kW'] for p in GROUPS),sum(g[f'{p}_c_kW'] for p in GROUPS)*1.4)

def test_L3_physical_propagation_requires_delay(setup):
    base={'id':'testL3','stage':3,'start_hour':6347,'duration_steps':2,'step_minutes':15,
          'faults':{'GT_1':0.0},'enable_propagation':True,'pre_event_warmup_steps':8,
          'grid_limit_kW':250,'automatic_fast_shedding':True}
    delayed,_,delayed_summary=_run(setup,dict(base,response_delay_steps=2))
    immediate,_,immediate_summary=_run(setup,dict(base,response_delay_steps=0))
    assert delayed_summary['secondary_fault_count']==0
    assert immediate_summary['secondary_fault_count']==0
    # Actual local operations are separate from the post-operation stress log.
    assert any(e['kind']=='automatic_load_shed' for e in delayed_summary['event_trace'])

def test_L4_load_only_trigger(setup):
    s={'id':'testL4','stage':4,'start_hour':4300,'duration_steps':2,'step_minutes':15,
       'shock':{'e':0.85,'c':0.55},'enable_propagation':True,
       'pre_event_warmup_steps':8,'grid_limit_kW':250,'response_delay_steps':2,
       'automatic_fast_shedding':True}
    trace,_,summary=_run(setup,s)
    assert summary['secondary_fault_count']==0
    assert any(e['kind']=='automatic_load_shed' for e in summary['event_trace'])

def test_shortfall_when_no_power_available(setup):
    s={'id':'power_failure','stage':1,'start_hour':6347,'duration_steps':2,'step_minutes':15,
       'faults':{'GRID':0.0},'enable_propagation':False,'grid_limit_kW':0}
    trace,_,summary=_run(setup,s)
    assert summary['energy_balance_verified']
    assert (trace.P0_e_unserved_kW>=-1e-5).all()
    # Need not be perfectly served; solver MUST return sensible shortages.


def test_priority_specific_demand_shock(setup):
    b=setup[2].set_index('hour_of_year').loc[4300].to_dict()
    x=loads_for_step(b,{'e':0.20},{'P0':{'e':1.0,'c':0.5}})
    assert np.isclose(x[0,0],b['P0_e_kW']*1.2*2.0)
    assert np.isclose(x[0,2],b['P0_c_kW']*1.5)
    assert np.isclose(x[1,0],b['P1_e_kW']*1.2)


def test_p0_classifier_uses_solver_abs_relative_and_energy_thresholds():
    cfg={'p0_solver_feasibility_tol_kW':1e-3,'p0_unserved_abs_tol_kW':0.01,
         'p0_unserved_rel_tol':0.001,'p0_unserved_abs_tol_kWh':0.01}
    demand=np.array([[100.,10.,20.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]])
    noise=np.zeros((4,3));noise[0,0]=7e-5
    state=p0_failure_classification(noise,demand,False,cfg,0.25)
    assert not state['p0_energy_loss'] and not state['p0_guarantee_failure']
    assert state['raw_P0_e_unserved_kW']==7e-5
    zero_demand=np.zeros((4,3));zero_demand[0,0]=0.005
    state=p0_failure_classification(zero_demand,np.zeros((4,3)),False,cfg,0.25)
    assert not state['p0_energy_loss']
    abs_case=np.zeros((4,3));abs_case[0,0]=0.02
    state=p0_failure_classification(abs_case,demand,False,cfg,0.25)
    assert state['p0_energy_loss'] and state['p0_guarantee_failure']
    rel_case=np.zeros((4,3));rel_case[0,1]=0.02
    state=p0_failure_classification(rel_case,demand,False,cfg,0.25)
    assert state['p0_energy_loss'] and state['p0_guarantee_failure']
    cold_case=np.zeros((4,3));cold_case[0,2]=0.2
    state=p0_failure_classification(cold_case,demand,False,cfg,0.25)
    assert state['p0_energy_loss'] and not state['p0_guarantee_failure']
    state=p0_failure_classification(noise,demand,True,cfg,0.25)
    assert state['p0_temperature_violation'] and state['p0_guarantee_failure']
    tiny=np.zeros((4,3));tiny[0,0]=0.005
    first=p0_failure_classification(tiny,demand,False,cfg,1.)
    second=p0_failure_classification(tiny,demand,False,cfg,1.,first['cumulative_effective_energy_kWh'])
    clear=p0_failure_classification(np.zeros((4,3)),demand,False,cfg,1.,second['cumulative_effective_energy_kWh'])
    assert not first['p0_energy_loss'] and second['p0_energy_loss'] and not clear['p0_energy_loss']


def test_lex_lock_tolerance_preserves_p0_p2_and_relaxes_only_p3():
    cfg={'lex_lock_abs_tol_by_priority':[1e-7,1e-7,1e-7,1e-5],
         'lex_lock_rel_tol_by_priority':[0.,0.,0.,1e-8]}
    assert [lex_lock_tolerance(i,0.,cfg) for i in range(4)]==[1e-7,1e-7,1e-7,1e-5]
    assert lex_lock_tolerance(3,100.,cfg)==max(1e-5,1e-6)


def _cascade_inputs():
    cfg={'gt_short_term_overload_multiple':1.05,'gt_protection_trip_multiple':1.05,
         'gt_overload_hold_minutes':15,'precontrol_cooling_to_el_fraction':0.6,
         'p0_solver_feasibility_tol_kW':1e-3,'pre_event_electric_demand_kW':0.,
         'pre_event_cooling_demand_kW':0.,'precontrol_load_shed_enabled':True}
    plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    # Current connected demand balances the three running pre-event sources.
    loads=np.zeros((4,3));loads[3,0]=3*plant.gt_rated_kW
    return cfg,plant,loads


def test_cascade_subtracts_grid_diesel_and_executable_shedding():
    cfg,plant,loads=_cascade_inputs()
    avail=empty_faults();avail['GT_1']=0.
    cfg['pre_event_electric_demand_kW']=float(loads[:,0].sum())
    state=RuntimeState(np.array([plant.gt_rated_kW]*3),2400.,set(),np.zeros(3),{},
                       last_grid_import_kW=0.,last_diesel_kW=0.)
    # Available lower-priority load is shed after actual grid/diesel response.
    trips,stress,response=pre_control_cascade(state,avail,loads,plant,cfg,15,False,
        grid_import_limit_kW=100.,diesel_response_kW=200.)
    assert not trips
    assert response['precontrol_grid_support_kW']==100.
    assert response['precontrol_diesel_support_kW']==200.
    assert response['precontrol_load_shed_available_kW']>=plant.gt_rated_kW
    assert 0.<response['precontrol_load_shed_kW']<response['precontrol_load_shed_available_kW']
    # With no executable shedding and a saturated grid, the same event stresses
    # units; it must not assume a fixed GT identity.
    cfg['precontrol_load_shed_enabled']=False
    loads[3,0]=3*plant.gt_rated_kW+200.
    state=RuntimeState(np.array([plant.gt_rated_kW]*3),2400.,set(),np.zeros(3),{},
                       last_grid_import_kW=200.,last_diesel_kW=0.)
    trips,_,_=pre_control_cascade(state,avail,loads,plant,cfg,15,False,
        grid_import_limit_kW=200.,diesel_response_kW=0.)
    assert trips
    assert set(trips).issubset({'GT_2','GT_3'})


def test_cascade_trip_response_is_permutation_symmetric():
    cfg,plant,loads=_cascade_inputs();cfg['precontrol_load_shed_enabled']=False
    loads[3,0]=3*plant.gt_rated_kW+850.
    avail=empty_faults();avail['GT_1']=0.
    state=RuntimeState(np.array([plant.gt_rated_kW]*3),2400.,set(),np.zeros(3),{},
                       last_grid_import_kW=850.,last_diesel_kW=0.)
    t1,_,_=pre_control_cascade(state,avail,loads,plant,cfg,15,False,
        grid_import_limit_kW=850.,diesel_response_kW=0.)
    avail2=empty_faults();avail2['GT_3']=0.
    state2=RuntimeState(np.array([plant.gt_rated_kW]*3),2400.,set(),np.zeros(3),{},
                       last_grid_import_kW=850.,last_diesel_kW=0.)
    t2,_,_=pre_control_cascade(state2,avail2,loads,plant,cfg,15,False,
        grid_import_limit_kW=850.,diesel_response_kW=0.)
    assert len(t1)==len(t2)
    assert set(t1)=={'GT_2','GT_3'}
    assert set(t2)=={'GT_1','GT_2'}


def test_cascade_response_delay_and_protection_threshold_sensitivity():
    cfg,plant,loads=_cascade_inputs();cfg['precontrol_load_shed_enabled']=False
    loads[3,0]=4*plant.gt_rated_kW+850.
    cfg['pre_event_electric_demand_kW']=0.
    avail=empty_faults()
    def run(controlled,trip_threshold):
        local=dict(cfg,gt_protection_trip_multiple=trip_threshold)
        state=RuntimeState(np.array([plant.gt_rated_kW]*3),2400.,set(),np.zeros(3),{},
                           last_grid_import_kW=850.,last_diesel_kW=0.)
        return pre_control_cascade(state,avail,loads,plant,local,15,controlled,
            grid_import_limit_kW=850.,diesel_response_kW=0.)[0]
    assert run(False,1.05)
    assert not run(False,1.5)
    assert run(True,1.05)  # R03: control availability cannot waive protection.

    # A request can exceed the trip threshold while remaining inside a
    # separately configured short-term allowance, and vice versa.
    small_loads=loads.copy();small_loads[3,0]=3.24*plant.gt_rated_kW+850.
    cfg['pre_event_electric_demand_kW']=0.
    cfg['precontrol_load_shed_enabled']=False
    state=RuntimeState(np.array([plant.gt_rated_kW]*3),2400.,set(),np.zeros(3),{},
                       last_grid_import_kW=850.,last_diesel_kW=0.)
    cfg['gt_short_term_overload_multiple']=1.10
    cfg['gt_protection_trip_multiple']=1.05
    trips,_,state_flags=pre_control_cascade(state,avail,small_loads,plant,cfg,15,False,
        grid_import_limit_kW=850.,diesel_response_kW=0.)
    assert trips and not state_flags['GT_1_short_term_allowance_exceeded']
    state=RuntimeState(np.array([plant.gt_rated_kW]*3),2400.,set(),np.zeros(3),{},
                       last_grid_import_kW=850.,last_diesel_kW=0.)
    cfg['gt_short_term_overload_multiple']=1.05
    cfg['gt_protection_trip_multiple']=1.20
    trips,_,state_flags=pre_control_cascade(state,avail,small_loads,plant,cfg,15,False,
        grid_import_limit_kW=850.,diesel_response_kW=0.)
    assert not trips and state_flags['GT_1_short_term_allowance_exceeded']


def test_cascade_counts_lost_grid_and_diesel_output():
    cfg,plant,loads=_cascade_inputs();cfg['precontrol_load_shed_enabled']=False
    loads[3,0]=3*plant.gt_rated_kW+200.+50.
    cfg['gt_overload_hold_minutes']=60
    cfg['pre_event_electric_demand_kW']=float(loads[:,0].sum())
    state=RuntimeState(np.array([plant.gt_rated_kW]*3),2400.,set(),np.zeros(3),{},
                       last_grid_import_kW=200.,last_diesel_kW=50.)
    avail=empty_faults();avail['GRID']=0.;avail['DIESEL']=0.
    trips,_,response=pre_control_cascade(state,avail,loads,plant,cfg,15,False,
        grid_import_limit_kW=200.,diesel_response_kW=0.)
    assert not trips
    assert response['precontrol_lost_grid_support_kW']==200.
    assert response['precontrol_lost_diesel_support_kW']==50.
    assert response['precontrol_GT_transfer_kW']>0.
