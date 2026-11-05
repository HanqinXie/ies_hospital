import copy
import sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src')]

def example():
    from hospital_ies.resilience import ZONE_PRIORITY,build_zone_groups
    zones=pd.DataFrame([dict(hour_of_year=1,zone_name=z,zone_multiplier=1.,interior_equipment_kW=1.,interior_lighting_kW=0.,
        associated_reheat_coil_heating_kW=0.,zone_sensible_cooling_kW=1.) for z in ZONE_PRIORITY])
    hourly=pd.DataFrame(dict(hour_of_year=[1],Q_c_demand_kW=[30.],Q_c_terminal_coils_kW=[30.],Q_h_demand_kW=[5.],P_e_nonplant_kW=[20.],P_e_unresolved_kW=[0.]))
    for g in range(4):hourly[f'P{g}_reference_c_kW']=float(g+1)
    hourly['source_chw_pump_kW']=3.
    env=SimpleNamespace(hourly=hourly,zones=zones,groups=build_zone_groups(zones,hourly).set_index('hour_of_year'),state={'fuel':2400.,'GT':[0.,2.,3.]})
    newzones=zones.copy();newzones.zone_sensible_cooling_kW+=.1
    newtotal=hourly[['hour_of_year','Q_c_demand_kW','Q_c_terminal_coils_kW']].copy();newtotal.Q_c_demand_kW+=10.;newtotal.Q_c_terminal_coils_kW+=10.
    groups=build_zone_groups(newzones, newtotal.merge(hourly.drop(columns=['Q_c_demand_kW','Q_c_terminal_coils_kW']),on='hour_of_year'))
    mapping=pd.DataFrame(dict(hour_of_year=[1],total_original_c_kW=[30.],total_new_c_kW=[40.]))
    for g in range(4):mapping[f'P{g}_new_c_kW']=groups[f'P{g}_c_kW']
    return env,newtotal,newzones,mapping

def test_cold_overlay_changes_current_demands_but_preserves_source_auxiliary_reference_and_all_noncold_inputs():
    from tools.model_review_v27.environment import apply_cooling_profile
    env,total,zones,mapping=example();before=copy.deepcopy(env)
    apply_cooling_profile(env,total,zones,mapping)
    assert env.hourly.Q_c_demand_kW.iloc[0]==40.
    assert env.groups.filter(regex='_c_kW$').sum(axis=1).iloc[0]==pytest.approx(40.)
    assert env.hourly.drop(columns=['Q_c_demand_kW','Q_c_terminal_coils_kW']).equals(before.hourly.drop(columns=['Q_c_demand_kW','Q_c_terminal_coils_kW']))
    assert env.groups.filter(regex='_[eh]_kW$').equals(before.groups.filter(regex='_[eh]_kW$'))
    assert env.state==before.state

def test_cooling_profile_cannot_silently_clip_negative_or_accept_a_different_annual_baseline():
    from tools.model_review_v27.environment import apply_cooling_profile
    env,total,zones,mapping=example();total.loc[0,'Q_c_demand_kW']=-1.
    with pytest.raises(ValueError,match='negative|finite'):apply_cooling_profile(env,total,zones,mapping)
    env,total,zones,mapping=example();mapping.loc[0,'total_original_c_kW']=29.
    with pytest.raises(ValueError,match='baseline'):apply_cooling_profile(env,total,zones,mapping)

def test_cooling_profile_checks_native_zone_allocation_against_declared_groups():
    from tools.model_review_v27.environment import apply_cooling_profile
    env,total,zones,mapping=example();mapping.loc[0,'P0_new_c_kW']+=2.
    with pytest.raises(ValueError,match='allocation|group'):apply_cooling_profile(env,total,zones,mapping)

def test_cold_overlay_does_not_silently_compress_an_impossible_new_critical_proxy():
    from tools.model_review_v27.environment import apply_cooling_profile
    env,total,zones,mapping=example();zones.zone_sensible_cooling_kW=10.
    with pytest.raises(ValueError,match='proxy exceeds total'):apply_cooling_profile(env,total,zones,mapping)

def test_conditional_mapping_does_not_read_withheld_physical_hours_or_expand_source_grant():
    from tools.model_review_v27.environment import apply_cooling_profile
    env,total,zones,mapping=example();env.allowed_hours=frozenset([1])
    unknown=env.hourly.iloc[[0]].copy();unknown.loc[:,'hour_of_year']=2;unknown.loc[:,'Q_c_demand_kW']=np.nan
    env.hourly=pd.concat([env.hourly,unknown],ignore_index=True)
    extra_total=total.copy();extra_total.hour_of_year=2
    extra_map=mapping.copy();extra_map.hour_of_year=2
    full_total=pd.concat([total,extra_total],ignore_index=True);full_map=pd.concat([mapping,extra_map],ignore_index=True)
    receipt=apply_cooling_profile(env,full_total,zones,full_map)
    assert receipt['registered_source_hours']==2 and receipt['hours']==1
    assert env.allowed_hours==frozenset([1]) and np.isnan(env.hourly.loc[1,'Q_c_demand_kW'])
    assert set(env.groups.index)=={1}
