"""TDD tests for the bounded, explicitly offline mathematical reference."""
from pathlib import Path
import copy
import importlib.util
import json
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]
HERE=ROOT/'tools/model_review_v30'
FIXTURE=ROOT/'configs/model_review_v30/tiny_reference_fixture.json'


def api():
    path=HERE/'offline.py'
    assert path.exists(), 'Missing multi-period reference implementation'
    spec=importlib.util.spec_from_file_location('reference_under_test',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_solver_matches_hand_verified_hard_service_and_fuel_certificate():
    m=api(); spec=m.fixture_spec(json.loads(FIXTURE.read_text(encoding='utf-8')))
    result=m.solve_reference(spec)
    assert result['status']=='optimal'
    assert result['incumbent'] is True
    assert result['objective_fee']==pytest.approx(11.0625,abs=1e-8)
    audit=m.audit_plan(spec,result['plan'])
    assert audit['valid'],audit
    assert audit['fuel_used']['diesel']==pytest.approx(8)
    assert result['plan']['battery.stock'][-1]==pytest.approx(0,abs=1e-8)
    assert sum(result['plan']['battery.discharge'])==pytest.approx(1.5)


def test_subcritical_diesel_budget_is_proved_infeasible_not_zero_plan():
    m=api(); spec=m.fixture_spec(json.loads(FIXTURE.read_text(encoding='utf-8')))
    spec['fuel_budgets']['diesel']=7.9
    result=m.solve_reference(spec)
    assert result['status']=='infeasible'
    assert result['incumbent'] is False
    assert result['plan'] is None
    assert result['objective_fee'] is None


def test_thermal_storage_uses_current_end_stock_implicit_decay():
    m=api(); spec=m.fixture_spec(json.loads(FIXTURE.read_text(encoding='utf-8')))
    # Isolated two-step thermal store. Fixed charge/discharge are physically
    # imposed by heat supply/requests, with no alternative thermal source.
    spec.update(duration_h=[1,1],demand_e=[0,0],demand_h=[0,0.4],demand_c=[0,0],
                generators=[],grid_capacity=[0,0],grid_price=[0,0],
                boiler={'capacity':[0.25,0],'fuel_slope':0,'fuel_type':'gas'},
                electric_chillers=[],absorption={'capacity':[0,0],'heat_per_c':2,'electric_per_c':0},
                fuel_budgets={},fuel_prices={},tower_capacity=[100,100])
    spec['storages']=[{'name':'hot','carrier':'h','capacity':2,'power':0.5,'initial':1,
                       'charge_efficiency':0.8,'discharge_efficiency':0.8,'decay_per_h':0.1,
                       'loss_mode':'implicit_end','charge_allowed':[True,False],
                       'discharge_allowed':[False,True],'terminal_min':65/121,
                       'terminal_max':65/121}]
    result=m.solve_reference(spec)
    assert result['status']=='optimal'
    assert result['plan']['hot.charge']==pytest.approx([0.25,0],abs=1e-8)
    assert result['plan']['hot.discharge']==pytest.approx([0,0.4],abs=1e-8)
    assert result['plan']['hot.stock']==pytest.approx([12/11,65/121],abs=1e-8)
    assert m.audit_plan(spec,result['plan'])['valid']


def test_independent_audit_detects_output_stock_fuel_and_integrality_corruption():
    m=api(); spec=m.fixture_spec(json.loads(FIXTURE.read_text(encoding='utf-8')))
    result=m.solve_reference(spec)
    for key in ['grid','battery.stock','DG.on']:
        plan=copy.deepcopy(result['plan']);plan[key][0]+=0.1
        assert m.audit_plan(spec,plan)['valid'] is False,key


def test_status_distinguishes_limit_with_without_incumbent_and_infeasible():
    m=api()
    assert m.classify_termination(SimpleNamespace(status=1,x=None))['status']=='no_incumbent'
    with_solution=m.classify_termination(SimpleNamespace(status=1,x=[1.0]))
    assert with_solution=={'status':'time_limit','termination_reason':'limit_reached','incumbent':True}
    assert m.classify_termination(SimpleNamespace(status=2,x=None))['status']=='infeasible'
    assert m.classify_termination(SimpleNamespace(status=0,x=[1.0]))['status']=='optimal'


def test_finite_nonnegative_input_and_explicit_offline_permission_required():
    m=api(); spec=m.fixture_spec(json.loads(FIXTURE.read_text(encoding='utf-8')))
    for change in [('duration_h',[-1,1,1]),('demand_c',[2,float('nan'),2])]:
        bad=copy.deepcopy(spec);bad[change[0]]=change[1]
        with pytest.raises(ValueError):m.solve_reference(bad)
    spec['information_permission']='causal_online'
    with pytest.raises(ValueError,match='offline'):m.solve_reference(spec)


def test_dispatch_extraction_is_canonical_and_omits_storage_control_ports():
    m=api(); spec=m.fixture_spec(json.loads(FIXTURE.read_text(encoding='utf-8')))
    spec['dispatch_mapping']={'gt_names':['CHP',None,None],'dg_name':'DG','ec_names':['EC',None,None],
                              'boiler_heat_to_steam':1,'gt_steam_per_e':[1,0,0]}
    result=m.solve_reference(spec)
    extracted=m.extract_plan_actions(spec,result['plan'],preparation_lead_minutes=60)
    assert extracted['execution_identity']=='offline_onoff_preparation_plus_common_realtime_allocation'
    assert len(extracted['requests'])==3
    command=extracted['requests'][0]['action']['ordinary_dispatch']
    assert set(command['dispatch'])=={'gt_electric_kW','grid_import_kW','diesel_kW','boiler_steam_lbph',
        'absorption_cooling_kW','electric_chiller_cooling_kW','hrsg_steam_lbph','tower_rejection_kW','gt_fuel_MMBtu_h'}
    assert command['disconnected_kW']==[[0.,0.,0.]]*4
    assert command['dispatch']['gt_electric_kW'][0]>0 # prepare for the known outage
    assert 'battery.charge' in extracted['requests'][0]['unexecuted_storage_plan']
    assert not any('charge' in key or 'stock' in key for key in command['dispatch'])
    assert extracted['actual_replayed'] is False


def test_plan_action_rejects_failed_or_unmapped_plan_and_invalid_stock_bounds():
    m=api(); spec=m.fixture_spec(json.loads(FIXTURE.read_text(encoding='utf-8')))
    with pytest.raises(ValueError):m.extract_plan_actions(spec,None)
    result=m.solve_reference(spec)
    with pytest.raises(ValueError,match='mapping'):m.extract_plan_actions(spec,result['plan'])


def hospital_snapshot_fixture():
    return dict(plant={'gt_count':3,'steam_kWh_per_lb':0.3,'exchanger_eff':0.95,
        'absorption_rt':2,'electric_chillers_rt':[2,2,3],'boiler_steam_lbph':30,
        'emergency_diesel_kW':9,'abs_steam_lb_per_rt':19.5,'abs_electric_kW_per_rt':0.14,
        'ec_electric_kW_per_rt':0.77,'boiler_eff':0.8,'gas_price_usd_per_mmbtu':4.84},
        periods=[dict(start_minute=0,end_minute=15,original_demand_kW=[[1,1,1],[2,2,2],[3,3,3],[4,4,4]],
        availability={**{f'GT_{i}':1 for i in range(1,4)},**{f'HRSG_{i}':1 for i in range(1,4)},
            **{f'BOILER_{i}':1 for i in range(1,4)},**{f'EC_{i}':1 for i in range(1,4)},
            'ABS':1,'GRID':0,'DIESEL':1,'TOWER':1},
        gt_capacity_kW=3,gt_steam_lbph_per_kW=2,gt_fuel_MMBtu_per_kWh=0.01,grid_price=0.1)],
        grid_limit_kW=8.5,tower_capacity_kW=40,diesel_shaft_budget_kWh=24,diesel_loss_kW=0.18,
        boiler_station_fixed_kW=0.5,boiler_electric_per_kW_steam=0.012,cold_pump_electric_per_kW=0.02,
        stores={'ups':dict(capacity_kWh=4,power_kW=2,charge_efficiency=0.95,discharge_efficiency=0.95,loss_per_hour=0),
                'cold_store':dict(capacity_kWh=6,power_kW=3,charge_efficiency=0.95,discharge_efficiency=0.95,loss_per_hour=0.01)},
        initial_stocks={'ups':4,'cold_store':6},provenance={'evidence_class':'artificial_adapter_fixture'})


def test_hospital_adapter_keeps_existing_steam_coupling_and_critical_storage_scope():
    m=api(); snap=hospital_snapshot_fixture(); spec=m.hospital_envelope_spec(snap)
    assert spec['demand_e']==[10] and spec['demand_h']==[10] and spec['demand_c']==[10]
    assert spec['generators'][0]['heat_slope']==pytest.approx([2*0.3*0.95])
    assert spec['absorption']['heat_per_c']==pytest.approx(19.5*0.3*0.95/3.51685284)
    assert spec['fuel_budgets']['diesel_shaft']==24
    ups=next(s for s in spec['storages'] if s['name']=='ups')
    assert ups['discharge_limit']==[3] # P0+P1 building e only, no station/converter sink
    assert ups['initial']==4 # actual stock, not initial charged input
    cold=next(s for s in spec['storages'] if s['name']=='cold_store')
    assert cold['discharge_limit']==[3]
    assert cold['loss_mode']=='exponential_beginning'
    assert spec['evidence_class']=='historical_C_conditioned_static_planning_envelope'
    assert spec['physical_execution_validated'] is False


def test_hospital_adapter_does_not_treat_missing_traceability_as_zero_demand():
    m=api(); snap=hospital_snapshot_fixture();del snap['periods'][0]['original_demand_kW']
    with pytest.raises(ValueError,match='original'):m.hospital_envelope_spec(snap)



def test_canonical_requests_record_tiny_solver_bound_roundoff_without_changing_plan():
    m=api(); spec=m.fixture_spec(json.loads(FIXTURE.read_text(encoding='utf-8')))
    spec['dispatch_mapping']={'gt_names':['CHP',None,None],'dg_name':'DG','ec_names':['EC',None,None],
                              'boiler_heat_to_steam':1,'gt_steam_per_e':[1,0,0]}
    plan=m.solve_reference(spec)['plan'];plan['CHP'][0]=-1e-14
    actions=m.extract_plan_actions(spec,plan)
    assert plan['CHP'][0]==-1e-14 # retain native solver evidence
    row=actions['requests'][0]
    assert row['action']['ordinary_dispatch']['dispatch']['gt_electric_kW'][0]==0
    assert any(c['field']=='gt_electric_kW[0]' and c['before']==-1e-14 for c in row['numeric_roundoff_corrections'])
    plan['CHP'][0]=-1e-7
    with pytest.raises(ValueError,match='negative'):m.extract_plan_actions(spec,plan)


def test_identical_generator_labels_do_not_create_false_early_preparation():
    m=api(); spec=m.fixture_spec(json.loads(FIXTURE.read_text(encoding='utf-8')))
    spec.update(duration_h=[1,1],demand_e=[1,1],demand_h=[0,0],demand_c=[0,0],grid_capacity=[0,0],grid_price=[1,1],
        storages=[],electric_chillers=[],boiler={'capacity':0,'fuel_slope':0,'fuel_type':'gas'},
        absorption={'capacity':0,'heat_per_c':1,'electric_per_c':0},tower_capacity=[100,100],fuel_budgets={'gas':100},fuel_prices={'gas':1})
    spec['generators']=[dict(name=k,capacity=1,min_power=1,fuel_type='gas',fuel_slope=1,fuel_fixed=0,heat_slope=0,heat_fixed=0) for k in ['A','B','C']]
    plan={k:[0.,0.] for k in ['grid','boiler','boiler.on','ABS','heat_waste','A','A.on','B','B.on','C','C.on']}
    plan.update({'A':[0,1],'A.on':[0,1],'B':[1,0],'B.on':[1,0]})
    stabilized=m.stabilize_symmetric_generators(spec,plan,['A','B','C'],[1,0,0])
    assert stabilized['plan']['A']==[1,1] and stabilized['plan']['B']==[0,0]
    assert stabilized['verified_equivalent'] is True
    assert stabilized['independent_audit']['valid']
    assert plan['A']==[0,1] # raw plan preserved
    spec['generators'][1]['capacity']=[1,0.5]
    unchanged=m.stabilize_symmetric_generators(spec,plan,['A','B','C'],[1,0,0])
    assert unchanged['verified_equivalent'] is False
    assert unchanged['reason']=='not_identical_over_complete_window'


def test_horizon_arrays_and_terminal_inventory_domains_fail_at_input_boundary():
    m=api(); base=m.fixture_spec(json.loads(FIXTURE.read_text(encoding='utf-8')))
    for bad in ['short_array','nested_array','minimum_above_capacity','terminal_above_capacity']:
        spec=copy.deepcopy(base)
        if bad=='short_array':spec['demand_e']=[2]
        elif bad=='nested_array':spec['demand_e']=[[2,2,2]]
        elif bad=='minimum_above_capacity':spec['storages'][0]['minimum']=2
        else:spec['storages'][0]['terminal_min']=2
        with pytest.raises(ValueError):m.solve_reference(spec)
