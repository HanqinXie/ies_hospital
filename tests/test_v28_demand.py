"""V28 counterexamples: current service must not consume direct demand."""
from pathlib import Path
from types import SimpleNamespace
import copy
import importlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
KINDS=('fan','chw_pump','heat_pump')

def api():
    try:return importlib.import_module('tools.model_review_v28.demand')
    except ModuleNotFoundError as exc:
        if exc.name in ('tools.model_review_v28','tools.model_review_v28.demand'):
            pytest.fail('V28 fixed-direct request builder is absent; cold still consumes direct demand')
        raise

def fixture_row():
    data=dict(sci_demand_boundary_contract='hospital_ies.fixed_direct_aux.v28.v1',
        source_fans_total_kW=20.,source_chw_pump_kW=8.,source_heat_pump_kW=4.,
        Q_h_DHW_water_use_kW=0.,outdoor_temperature_C=25.,timestamp=pd.Timestamp('2017-07-20 08:00'))
    loads=np.empty((4,3))
    for g in range(4):
        d=30.+g
        data[f'P{g}_base_e_kW']=d+(28. if g==3 else 0.)
        for k,v in zip(KINDS,[5.,2.,1.]):data[f'P{g}_{k}_kW']=v
        loads[g]=[d+8.,60.,100.]
        for k,v in zip('ehc',loads[g]):data[f'P{g}_reference_{k}_kW']=v
    return SimpleNamespace(**data),loads

def test_signed_cold_change_has_fixed_direct_and_single_auxiliary_electricity():
    m=api();row,base=fixture_row();before=copy.deepcopy(vars(row))
    for factor in (1.25,.75):
        request=base.copy();request[:,2]*=factor
        newrow,out=m.bind_request(row,request)
        q=newrow.sci_direct_aux_request
        assert np.array_equal(q['direct_request_kW'],np.arange(30.,34.))
        assert np.allclose(out[:,0]-base[:,0],np.sum(q['auxiliary_request_kW'],axis=1)-8.,atol=1e-12,rtol=0)
        assert np.allclose(out[:,0]-np.sum(q['auxiliary_request_kW'],axis=1),np.arange(30.,34.),atol=1e-12,rtol=0)
        assert np.allclose(m.bind_request(row,request)[1],out,atol=0,rtol=0)
    assert vars(row)==before

def test_explicit_direct_shock_does_not_feed_derived_auxiliary_back_to_fans():
    m=api();row,base=fixture_row();raw=base.copy();raw[:,0]+=4.;raw[:,2]*=1.1
    r,loads=m.bind_request(row,raw)
    q=r.sci_direct_aux_request
    assert np.array_equal(q['direct_request_kW'],np.arange(34.,38.))
    expected_fan=5.*np.maximum((base[:,0]+4.)/base[:,0],1.1)
    assert np.allclose(np.asarray(q['auxiliary_request_kW'])[:,0],expected_fan,atol=1e-12,rtol=0)
    assert np.allclose(loads[:,0],np.arange(34.,38.)+np.sum(q['auxiliary_request_kW'],axis=1),atol=1e-12,rtol=0)

def test_missing_negative_and_zero_reference_cannot_be_silently_clipped():
    m=api();row,loads=fixture_row();del row.P0_base_e_kW
    with pytest.raises(ValueError,match='source|missing|base'):m.bind_request(row,loads)
    row,loads=fixture_row();row.P3_base_e_kW=27.
    with pytest.raises(ValueError,match='negative|direct'):m.bind_request(row,loads)
    row,loads=fixture_row();row.P2_reference_h_kW=0.
    with pytest.raises(ValueError,match='zero|reference'):m.bind_request(row,loads)

def test_connected_service_cuts_do_not_create_new_direct_electric_sink():
    m=api();row,loads=fixture_row();row,full=m.bind_request(row,loads)
    connected=full.copy();connected[:,1:]=0.
    detail=m.dependency_request(row,connected)
    assert np.array_equal(detail['direct_cap_kW'],np.arange(30.,34.))
    assert np.allclose(np.asarray(detail['auxiliary_required_kW'])[:,1:],0.,atol=0,rtol=0)
    assert np.array_equal(row.sci_direct_aux_request['direct_request_kW'],np.arange(30.,34.))

def test_full_annual_source_decomposition_matches_existing_zero_disturbance_boundary():
    m=api()
    from tools.sci_engineering.auxiliary import compute_auxiliary_allocation,apply_auxiliary_allocation
    from hospital_ies.resilience import build_zone_groups
    a=compute_auxiliary_allocation(ROOT)
    source=pd.read_csv(ROOT/'inputs/loads/hospital_ies_demand_8760.csv')
    zones=pd.read_csv(ROOT/'inputs/loads/critical_zones_8760.csv')
    old=apply_auxiliary_allocation(build_zone_groups(zones,source),a)
    assert len(a)==8760
    for i,rawrow in enumerate(a.itertuples(index=False)):
        row=SimpleNamespace(**rawrow._asdict(),sci_demand_boundary_contract=m.DEMAND_CONTRACT_ID)
        loads=old.loc[i, [f'P{g}_{k}_kW' for g in range(4) for k in 'ehc']].to_numpy(float).reshape(4,3)
        for g in range(4):
            for k,v in zip('ehc',loads[g]):setattr(row,f'P{g}_reference_{k}_kW',float(v))
        newrow,out=m.bind_request(row,loads)
        assert np.allclose(out,loads,atol=2e-12,rtol=0)
        assert (np.asarray(newrow.sci_direct_aux_request['direct_request_kW'])>=0.).all()

def test_shared_dependency_does_not_scale_derived_electricity_a_second_time():
    m=api()
    from tools.sci_engineering.service import dependency_requirements
    row,raw=fixture_row();raw[:,0]+=20.
    row,loads=m.bind_request(row,raw)
    assert np.allclose(dependency_requirements(row,loads),row.sci_direct_aux_request['auxiliary_request_kW'],atol=1e-12,rtol=0)

@pytest.mark.parametrize('fan_failed',[False,True])
def test_actual_service_keeps_direct_cap_and_records_real_auxiliary_shortfall(fan_failed):
    m=api()
    from hospital_ies.model import load_plant
    from hospital_ies.resilience import RuntimeState,empty_faults
    from tools.operating_response.physical_balance import solve_physical
    row,raw=fixture_row();row,full=m.bind_request(row,raw)
    connected=full.copy();connected[:,1:]=0.
    faults=empty_faults()
    if fan_failed:faults['AUX_FAN_P0']=0.
    state=RuntimeState(np.zeros(3),2400.,set(),np.zeros(3),{})
    result=solve_physical(row,load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory'),
        connected,faults,state,.25,850.,False,900.,6.,{'sci_service_dependencies':True})
    detail=result['service_dependencies'];direct=np.asarray(detail['direct_electric_delivered_kW'])
    assert (direct<=np.arange(30.,34.)+1e-7).all()
    assert np.allclose(result['served'][:,0],direct+np.sum(detail['auxiliary_delivered_kW'],axis=1),atol=1e-10,rtol=0)
    original=detail['demand_components']['original_request']
    assert np.array_equal(original['direct_request_kW'],np.arange(30.,34.))
    assert np.sum(detail['demand_components']['auxiliary_unserved_kW'])>0.
    if fan_failed:assert detail['auxiliary_delivered_kW'][0][0]<1e-8

def test_actual_core_adds_clinical_shock_and_current_cold_auxiliary_once(monkeypatch):
    m=api()
    from tools.sci_engineering.environment import EngineeringCore
    from tools.load_accounting.execution import LoadAccountingCore
    from hospital_ies.resilience import empty_faults
    row,base=fixture_row();raw=base.copy();raw[:,2]*=1.2
    source=next(pd.DataFrame([vars(row)]).itertuples(index=False))
    monkeypatch.setattr(LoadAccountingCore,'_hardware_data_at',lambda self,t:(1,source,raw.copy(),empty_faults()))
    core=EngineeringCore.__new__(EngineeringCore)
    core.env=SimpleNamespace(
        scenario=dict(duration_steps=4,shock_by_zone={'OR1_FLR_2':{'e':.5,'c':.5}},shock_start_minute=0.,shock_duration_minutes=60.),
        zones=pd.DataFrame([dict(hour_of_year=1,zone_name='OR1_FLR_2',interior_equipment_kW=6.,interior_lighting_kW=2.,associated_reheat_coil_heating_kW=0.,zone_sensible_cooling_kW=20.,zone_multiplier=2.)]),
        auxiliary_metadata=dict(root_input_sha256={'inputs/loads/hospital_ies_demand_8760.csv':'a'*64},fan_component_columns={}))
    _,bound,actual,_=core._hardware_data_at(0.)
    expected_raw=raw.copy();expected_raw[0,0]+=4.;expected_raw[0,2]+=20.
    expected_row,expected=m.bind_request(row,expected_raw)
    assert np.allclose(actual,expected,atol=1e-12,rtol=0)
    assert bound.sci_direct_aux_request==expected_row.sci_direct_aux_request
    assert np.array_equal(np.asarray(bound.sci_clinical_increment_kW)[0],[4.,0.,20.])
    _,later,laterloads,_=core._hardware_data_at(1.)
    assert np.array_equal(laterloads,actual)
    assert later.sci_direct_aux_request==bound.sci_direct_aux_request

def test_named_preparation_copy_gets_its_own_request_not_current_direct_cap():
    m=api();row,raw=fixture_row();row,full=m.bind_request(row,raw)
    scaled=full.copy();scaled[:2]*=1.1
    copyrow,demand=m.counterfactual_request(row,scaled)
    assert np.allclose(copyrow.sci_direct_aux_request['direct_request_kW'][:2],np.arange(30.,32.)*1.1,atol=1e-12,rtol=0)
    assert np.allclose(demand[:2,0],scaled[:2,0],atol=1e-12,rtol=0)
    assert np.array_equal(row.sci_direct_aux_request['direct_request_kW'],np.arange(30.,34.))

def test_C_planner_preparation_copies_use_same_corrected_request_builder():
    m=api()
    sys.path[:0]=[str(ROOT/'tests')]
    from test_engineering_planner import problem as problem_fixture
    from test_state_coordinated_planner import context
    from tools.sci_engineering import planner,policy
    p=problem_fixture.__wrapped__();row,raw=fixture_row();row,loads=m.bind_request(row,raw)
    p.update(row=row,loads=loads);p['config']['sci_service_dependencies']=True
    result=planner.solve_joint(**p,readiness_context=context(p),coordination_options=policy.policy_config('C',dict(online_GT_count_order='after_cost')))
    d=result['joint_diagnostic']
    assert np.allclose(d['sci']['current_auxiliary_required_kW'],row.sci_direct_aux_request['auxiliary_request_kW'],atol=1e-12,rtol=0)
    assert d['sci']['current_demand_components']['direct_request_kW']==[30.,31.,32.,33.]
    for record in d['risk_scenarios'].values():
        if 'demand_components' in record:
            q=record['demand_components']
            assert np.allclose(np.asarray(record['loads_kW'])[:,0],np.asarray(q['direct_request_kW'])+np.sum(q['auxiliary_request_kW'],axis=1),atol=1e-10,rtol=0)

def test_new_ordinary_DHW_target_is_fan_independent_with_bound_new_source_trace():
    m=api()
    from tools.sci_engineering.service import fan_independent_heat_bound
    row,raw=fixture_row();raw[3,1]=110.
    row.Q_h_DHW_water_use_kW=95.;row.v28_ordinary_DHW_source_delivered_kW=10.
    row.v28_thermal_service_trace=dict(path='inputs/model_review_v28/ordinary_target_8760.csv',sha256='b'*64,
        hour_of_year=1,request_column='ordinary_DHW_target_kW',legacy_delivery_column='Q_h_DHW_water_use_kW',boundary='ordinary_wateruse_central_target_v28')
    row,loads=m.bind_request(row,raw)
    result=fan_independent_heat_bound(row,loads)
    assert result['maximum_fan_independent_heat_kW']==[0.,0.,0.,95.]
    assert result['decomposition_status']=='source_bound_ordinary_DHW_target_v28'
    assert result['source_trace']==row.v28_thermal_service_trace
    assert result['legacy_DHW_delivered_kW']==10.
    assert row.P3_reference_h_kW==60.
    row.v28_thermal_service_trace['path']='inputs/loads/hospital_ies_demand_8760.csv'
    with pytest.raises(ValueError,match='trace|source'):fan_independent_heat_bound(row,loads)

def test_lower_history_copy_does_not_invalidate_current_target_source_qualification():
    m=api()
    from tools.sci_engineering.service import fan_independent_heat_bound
    row,raw=fixture_row();raw[3,1]=110.
    row.Q_h_DHW_water_use_kW=95.;row.v28_ordinary_DHW_source_delivered_kW=10.
    row.v28_thermal_service_trace=dict(path='inputs/model_review_v28/ordinary_target_8760.csv',sha256='b'*64,
        hour_of_year=1,request_column='ordinary_DHW_target_kW',legacy_delivery_column='Q_h_DHW_water_use_kW',boundary='ordinary_wateruse_central_target_v28')
    row,loads=m.bind_request(row,raw);lower=loads.copy();lower[3,1]=20.
    copyrow,copyloads=m.counterfactual_request(row,lower)
    result=fan_independent_heat_bound(copyrow,copyloads)
    assert result['maximum_fan_independent_heat_kW']==[0.,0.,0.,20.]
    assert result['bound_current_P3_heat_request_kW']==110.
    assert row.sci_direct_aux_request['aggregate_request_kW'][3][1]==110.
