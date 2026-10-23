"""DHW is useful heat, but the identified P3 component is not an air coil."""
from pathlib import Path
from types import SimpleNamespace
import copy,sys
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from hospital_ies.model import load_plant
from hospital_ies.resilience import RuntimeState,empty_faults
from tools.operating_response.physical_balance import solve_physical
from tools.sci_engineering import service,planner,policy
from test_engineering_planner import problem
from test_state_coordinated_planner import context

def fixture(heat=40.,dhw=10.,fan=0.,pump=1.,group=3):
    row=SimpleNamespace(timestamp=pd.Timestamp('2017-07-10 12:00'),outdoor_temperature_C=25.,
        Q_h_DHW_water_use_kW=dhw,P3_reference_h_kW=heat,
        sci_heat_decomposition_fixture='explicit synthetic regression, not hospital parameter',
        **{f'P{g}_{kind}_kW':(5. if kind=='fan' else 2. if kind=='heat_pump' else 0.) if g==group else 0.
           for g in range(4) for kind in ('fan','chw_pump','heat_pump')})
    loads=np.zeros((4,3));loads[group]=[20.,heat,0.]
    availability=empty_faults();availability[f'AUX_FAN_P{group}']=fan;availability['AUX_HEAT_PUMP']=pump
    return row,loads,availability

def physical(row,loads,availability):
    plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    state=RuntimeState(np.zeros(3),2400.,set(),np.zeros(3),{})
    return solve_physical(row,plant,loads,availability,state,.25,850.,False,900.,6.,{'sci_service_dependencies':True})

def selected(path,hours):
    # Skip other physical rows before parsing, preserving confirmation isolation.
    identity=pd.read_csv(path,usecols=['hour_of_year'])
    return pd.read_csv(path,skiprows=[i+1 for i,h in enumerate(identity.hour_of_year) if int(h) not in hours])

@pytest.mark.parametrize('hour',[4002,4003])
def test_real_source_hour_retains_identified_DHW_with_fan_failed(hour):
    allocation=selected(ROOT/'inputs/sci_engineering/auxiliary_allocation_8760.csv',{hour}).iloc[0]
    source=selected(ROOT/'inputs/loads/hospital_ies_demand_8760.csv',{hour}).iloc[0]
    assert source.Q_h_heating_coils_kW+source.Q_h_DHW_water_use_kW==pytest.approx(source.Q_h_demand_kW,abs=1e-5)
    assert allocation.P3_base_h_kW>source.Q_h_DHW_water_use_kW>0.
    row,loads,availability=fixture(heat=float(allocation.P3_base_h_kW),dhw=float(source.Q_h_DHW_water_use_kW))
    row.P3_fan_kW=float(allocation.P3_fan_kW);loads[3,0]=row.P3_fan_kW+12.
    result=physical(row,loads,availability)
    assert result['served'][3,1]==pytest.approx(source.Q_h_DHW_water_use_kW,abs=1e-7)
    detail=result['service_dependencies']['fan_independent_heat']
    assert detail['maximum_fan_independent_heat_kW'][3]==pytest.approx(source.Q_h_DHW_water_use_kW)
    assert detail['represents_actual_DHW_delivery'] is False
    assert max(map(abs,result['energy_balance_residuals']))<1e-4

@pytest.mark.parametrize('dhw,expected',[(0.,0.),(10.,10.),(60.,40.)])
def test_independent_heat_bound_zero_partial_and_capped_by_P3_request(dhw,expected):
    row,loads,availability=fixture(dhw=dhw)
    assert physical(row,loads,availability)['served'][3,1]==pytest.approx(expected,abs=1e-7)

@pytest.mark.parametrize('group',[0,1,2])
def test_DHW_does_not_reclassify_or_relax_P0_to_P2_heat(group):
    row,loads,availability=fixture(group=group)
    assert physical(row,loads,availability)['served'][group,1]<1e-7

def test_non_air_heat_still_needs_real_distribution_pump_and_source():
    row,loads,availability=fixture(pump=0.)
    assert physical(row,loads,availability)['served'][3,1]<1e-7
    row,loads,availability=fixture()
    for kind in ('GT','HRSG','BOILER'):
        for i in (1,2,3):availability[f'{kind}_{i}']=0.
    assert physical(row,loads,availability)['served'][3,1]<1e-7

def test_full_fan_normal_case_unchanged_and_requests_not_mutated():
    row,loads,availability=fixture(fan=1.)
    no_decomposition=copy.deepcopy(row);no_decomposition.Q_h_DHW_water_use_kW=0.
    baseline=physical(no_decomposition,loads,availability)
    before=loads.copy();new=physical(row,loads,availability)
    np.testing.assert_array_equal(loads,before)
    np.testing.assert_allclose(new['served'],baseline['served'],rtol=0.,atol=1e-7)
    for key in new['action']:np.testing.assert_allclose(new['action'][key],baseline['action'][key],rtol=0.,atol=1e-7)

def test_connected_clipping_is_maximum_allowance_not_new_DHW_priority():
    row,loads,_=fixture();loads[3,1]=3.
    assert hasattr(service,'fan_independent_heat_bound')
    detail=service.fan_independent_heat_bound(row,loads)
    assert detail['maximum_fan_independent_heat_kW']==[0.,0.,0.,3.]
    assert detail['original_P3_identified_heat_kW']==10.
    assert detail['represents_actual_DHW_delivery'] is False

@pytest.mark.parametrize('fan',[0.,.25,.5,1.])
def test_partial_fan_scales_only_the_air_served_remainder(fan):
    row,loads,availability=fixture(fan=fan)
    assert physical(row,loads,availability)['served'][3,1]==pytest.approx(10.+30.*fan,abs=1e-7)

def test_heat_increase_cannot_invent_a_larger_independent_DHW_component():
    row,loads,_=fixture();loads[3,1]=80.
    detail=service.fan_independent_heat_bound(row,loads)
    assert detail['maximum_fan_independent_heat_kW']==[0.,0.,0.,10.]

@pytest.mark.parametrize('bad_value',[-1.,float('nan'),float('inf')])
def test_invalid_source_DHW_is_rejected_without_clipping(bad_value):
    row,loads,_=fixture(dhw=bad_value)
    with pytest.raises(ValueError,match='DHW|decomposition'):
        service.fan_independent_heat_bound(row,loads)

@pytest.mark.parametrize('change',[dict(sha256='missing'),dict(source_DHW_kW=12.),dict(original_P3_h_kW=float('nan'))])
def test_required_source_trace_rejects_malformed_or_mismatching_fields(change):
    row,loads,_=fixture();row.sci_heat_decomposition_required=True
    row.sci_heat_decomposition_source=dict(path='inputs/loads/hospital_ies_demand_8760.csv',
        sha256='a'*64,hour_of_year=4002,DHW_column='Q_h_DHW_water_use_kW',source_DHW_kW=10.,original_P3_h_kW=40.)
    row.sci_heat_decomposition_source.update(change)
    with pytest.raises(ValueError,match='DHW|decomposition'):
        service.fan_independent_heat_bound(row,loads)

@pytest.mark.parametrize('malformation',['missing_original_P3','boolean_hour'])
def test_required_source_trace_rejects_missing_original_or_boolean_hour(malformation):
    row,loads,_=fixture();row.sci_heat_decomposition_required=True
    row.sci_heat_decomposition_source=dict(path='inputs/loads/hospital_ies_demand_8760.csv',
        sha256='a'*64,hour_of_year=4002,DHW_column='Q_h_DHW_water_use_kW',source_DHW_kW=10.,original_P3_h_kW=40.)
    if malformation=='missing_original_P3':row.sci_heat_decomposition_source.pop('original_P3_h_kW')
    else:row.sci_heat_decomposition_source['hour_of_year']=True
    with pytest.raises(ValueError,match='DHW|decomposition'):
        service.fan_independent_heat_bound(row,loads)

def test_real_M21_trace_cannot_silently_default_missing_DHW_to_zero():
    row,loads,_=fixture();del row.Q_h_DHW_water_use_kW
    row.sci_heat_decomposition_required=True
    assert hasattr(service,'fan_independent_heat_bound')
    with pytest.raises(ValueError,match='DHW|decomposition'):
        service.fan_independent_heat_bound(row,loads)
    row.sci_heat_decomposition_required=False
    detail=service.fan_independent_heat_bound(row,loads)
    assert detail['maximum_fan_independent_heat_kW']==[0.,0.,0.,0.]
    assert detail['decomposition_status']=='fixture_without_decomposition_assumed_zero'

def test_planner_current_and_all_diagnostic_copies_share_identical_P3_gate(problem):
    from hospital_ies.solver_diagnostics import LayerCollector,collect_layers
    p=copy.deepcopy(problem);row,loads,availability=fixture()
    p.update(row=row,loads=loads,availability=availability)
    p['state'].gt_prev[:]=0.;p['config']['sci_service_dependencies']=True
    collector=LayerCollector()
    with collect_layers(collector):
        result=planner.solve_joint(**p,readiness_context=context(p),coordination_options=policy.policy_config('C'))
    assert result['served'][3,1]==pytest.approx(10.,abs=1e-7)
    layer=collector.layers[0]
    matched=[i for i,r in enumerate(layer['rows']) if r['name'].endswith('P3.h_needs_fan')]
    assert len(matched)>=3
    assert all(layer['lower'][i]==30. for i in matched)
    assert all(np.max(layer['matrix'].getrow(i).toarray())==30. for i in matched)

def test_actual_row_adapter_binds_original_source_and_requires_DHW_column():
    from tools.sci_engineering.environment import OperatingHospitalIESEnv
    from tools.sci_engineering.design import SEALED_DAYS
    e=OperatingHospitalIESEnv(ROOT,allowed_hours=[4001,4002],sealed_days=SEALED_DAYS,model_config={})
    scene=dict(id='dhw_row_source_regression',stage=1,event_class='L1',start_hour=4001,
        duration_steps=2,step_minutes=15.,faults={'GRID':0.},fault_start_minute=15.,fault_duration_minutes=15.,
        response_delay_minutes=0.,enable_propagation=False,shock={},design_family='dhw_source_regression')
    e.reset(scenario=scene,seed=1)
    row=e.core.data_at(0.)[1]
    assert row.sci_heat_decomposition_required is True
    trace=row.sci_heat_decomposition_source
    assert trace['path']=='inputs/loads/hospital_ies_demand_8760.csv'
    assert len(trace['sha256'])==64 and trace['hour_of_year']==4001
    assert trace['DHW_column']=='Q_h_DHW_water_use_kW'
    assert e.core.observe()['sci_auxiliary']['fan_independent_heat']['represents_actual_DHW_delivery'] is False
