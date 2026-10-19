from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from hospital_ies.pipeline import read_data, run
from hospital_ies.demand_extensions import attach_demand_extensions, oxygen_profile
from hospital_ies.idf_parameters import zone_parameter_table
from hospital_ies.thermal_inertia import simulate_cooling_interruption
from hospital_ies.model import load_plant, solve_hour


def inputs():
    return read_data(ROOT),pd.read_csv(ROOT/'inputs/loads/electricity_boundary_8760.csv')


def test_default_fan_is_bookkeeping_only():
    data,electricity=inputs()
    enhanced=attach_demand_extensions(data.head(24),electricity.head(24))
    assert np.allclose(enhanced.electric_extra_demand_kW,0)
    assert np.allclose(enhanced.DOE_fans_existing_kW,electricity.fans_kW.head(24))
    assert np.all(enhanced.DOE_fans_existing_kW <= enhanced.P_e_nonplant_kW)
    pl=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    row=next(enhanced.itertuples(index=False))
    actual=solve_hour(row,pl)
    original=solve_hour(next(data.itertuples(index=False)),pl)
    for k in ('P_e_service_including_unresolved_kW','Q_h_demand_kW','Q_c_demand_kW','GT_electric_kW','grid_import_kW','operating_cost_USD_h'):
        assert np.isclose(actual[k],original[k],atol=1e-5)


def test_fan_modification_applies_difference_only():
    data,e=inputs()
    d=attach_demand_extensions(data.head(24),e.head(24),fan_ratio=0.5)
    assert np.allclose(d.electric_extra_demand_kW,-0.5*e.fans_kW.head(24))
    assert np.allclose(d.P_e_nonplant_kW,data.P_e_nonplant_kW.head(24))


def test_oxygen_constant_and_profile(tmp_path):
    data,e=inputs()
    selected=data.head(4)
    ox=oxygen_profile(selected,hourly_flow_Nm3=10,specific_kWh_Nm3=1.04)
    assert np.allclose(ox.oxygen_incremental_electric_kW,10.4)
    e2=attach_demand_extensions(selected,e.head(4),oxygen=ox)
    assert np.allclose(e2.electric_extra_demand_kW,10.4)
    profile=tmp_path/'profile.csv'
    pd.DataFrame({'hour_of_year':selected.hour_of_year.to_numpy(),
                  'oxygen_production_Nm3_h':[0,1,2,3]}).to_csv(profile,index=False)
    op=oxygen_profile(selected,profile_csv=profile)
    assert op.oxygen_production_Nm3_h.tolist()==[0,1,2,3]
    with pytest.raises(ValueError):oxygen_profile(selected)
    with pytest.raises(ValueError):oxygen_profile(selected,hourly_flow_Nm3=-1)


def test_thermal_idf_parameters_grounded_and_scenario():
    path=ROOT/'inputs/energyplus/Hospital_SanDiego.idf'
    geom=zone_parameter_table(path)
    assert len(geom)>=55
    z={r['zone_name']:r for r in geom}
    assert 'ICU_FLR_2' in z
    assert z['ICU_FLR_2']['IDF_furniture_capacity_kWh_K']>0
    df=read_data(ROOT).head(48)
    zones=pd.read_csv(ROOT/'inputs/loads/critical_zones_8760.csv')
    scenario, summary=simulate_cooling_interruption(zones,df,path,12,3,1.0)
    assert len(scenario)==48*14
    assert scenario.loc[scenario.hour_of_year<12,'delta_temperature_C'].max()<1e-8
    assert scenario.loc[scenario.hour_of_year.between(12,14),'delta_temperature_C'].max()>0
    assert scenario.loc[scenario.hour_of_year>14,'delta_temperature_C'].max()>0
    assert len(summary)==14
    none,_=simulate_cooling_interruption(zones,df,path,12,3,0.0)
    assert np.allclose(none.fault_zone_temp_C,none.baseline_zone_temp_C)


def test_pipeline_e2e(tmp_path):
    # Full-size realistic set of inputs is used, but limit the MILP to 3 hours.
    summary=run(ROOT,'doe_capacity_matched_exploratory',3,False,
                fan_ratio=1.0,oxygen_flow_Nm3_h=2.0,thermal_demo=True,
                event_hour=2,event_duration=1,scenario_tag='pytest_v2')
    assert summary['load_met_all_hours']
    assert np.isclose(summary['annual_integrals']['oxygen_incremental_electric_kW'],3*2*1.04)
    out=ROOT/'outputs/doe_capacity_matched_exploratory_first3h__pytest_v2'
    assert (out/'critical_zone_thermal_response.csv').exists()
    assert (out/'idf_thermal_parameters_all_zones.csv').exists()
    assert (out/'demand_extensions_hourly.csv').exists()
