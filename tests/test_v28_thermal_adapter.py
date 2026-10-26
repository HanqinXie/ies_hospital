"""Adopted target versus legacy delivery; no electric or reference overwrite."""
from pathlib import Path
from types import SimpleNamespace
import copy,hashlib,json
import numpy as np
import pandas as pd
import pytest

def fixture(tmp_path,*,invalid=None):
    root=tmp_path;folder=root/'inputs/model_review_v28';folder.mkdir(parents=True)
    hourly=pd.DataFrame(dict(hour_of_year=[1,2,3],timestamp=pd.date_range('2017-01-01 01:00',periods=3,freq='h').astype(str),Q_h_demand_kW=[11.]*3,Q_h_DHW_water_use_kW=[1.]*3,P_e_demand_kW=[50.]*3,P3_reference_h_kW=[7.]*3))
    source=root/'inputs/loads/hospital_ies_demand_8760.csv';source.parent.mkdir();hourly.to_csv(source,index=False)
    frame=pd.DataFrame(dict(hour_of_year=range(1,8761),timestamp=pd.date_range('2017-01-01 01:00',periods=8760,freq='h').astype(str),ordinary_DHW_target_kW=np.ones(8760),ordinary_DHW_original_delivered_kW=np.ones(8760),space_heating_original_kW=np.full(8760,10.),central_heat_request_kW=np.full(8760,11.),local_laundry_source_reference_NaturalGas_input_kW=np.ones(8760),local_booster_source_reference_electric_input_already_in_Dref_kW=np.full(8760,.1),booster_unmodelled_upstream_preheat_useful_kW=np.ones(8760)))
    frame.loc[1,['ordinary_DHW_target_kW','central_heat_request_kW']]=[3.,13.]
    frame.loc[2,['ordinary_DHW_target_kW','central_heat_request_kW']]=[.5,10.5]
    if invalid=='negative':frame.loc[1,'ordinary_DHW_target_kW']=-1.
    if invalid=='missing_hour':frame=frame.iloc[:-1]
    if invalid=='old_delivery':frame.loc[1,'ordinary_DHW_original_delivered_kW']=2.
    path=folder/'THERMAL_SERVICE_COMPONENTS_8760.csv';frame.to_csv(path,index=False)
    meta=dict(schema='hospital_ies.v28.adopted_thermal_input.v1',boundary='ordinary_wateruse_central_target_v28',source_input=dict(path='inputs/loads/hospital_ies_demand_8760.csv',sha256=hashlib.sha256(source.read_bytes()).hexdigest()),target_column='ordinary_DHW_target_kW',legacy_delivery_column='Q_h_DHW_water_use_kW',local_laundry='retained_source_reference_only',booster='electricity_already_in_direct_reference_no_central_preheat_assurance')
    mp=folder/'THERMAL_INPUT_METADATA.json';mp.write_text(json.dumps(meta))
    spec=dict(files={k:dict(path=p.relative_to(root).as_posix(),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for k,p in [('thermal_service_components',path),('thermal_input_metadata',mp)]})
    groups=pd.DataFrame(dict(hour_of_year=[1,2,3],P0_h_kW=[2.]*3,P1_h_kW=[1.]*3,P2_h_kW=[1.]*3,P3_h_kW=[7.]*3)).set_index('hour_of_year')
    return SimpleNamespace(root=root,hourly=hourly,groups=groups,allowed_hours={2,3}),spec

def test_target_replaces_only_current_ordinary_heat_and_preserves_signed_delta(tmp_path):
    from tools.model_review_v28.thermal_adapter import apply_thermal_profile
    env,spec=fixture(tmp_path);before=env.hourly.copy(deep=True)
    receipt=apply_thermal_profile(env,spec)
    assert env.hourly.Q_h_demand_kW.tolist()==[11.,13.,10.5]
    assert env.groups.P3_h_kW.tolist()==[7.,9.,6.5]
    assert env.hourly.P_e_demand_kW.equals(before.P_e_demand_kW)
    assert env.hourly.P3_reference_h_kW.equals(before.P3_reference_h_kW)
    assert env.hourly.loc[1,'Q_h_DHW_water_use_kW']==3.
    assert env.hourly.loc[1,'v28_ordinary_DHW_source_delivered_kW']==1.
    trace=env.hourly.loc[1,'v28_thermal_service_trace']
    assert trace['request_column']=='ordinary_DHW_target_kW' and trace['hour_of_year']==2
    assert receipt['local_heater_energy_is_source_reference_only'] is True

@pytest.mark.parametrize('invalid',['negative','missing_hour','old_delivery'])
def test_invalid_target_or_source_contract_is_rejected_atomically(tmp_path,invalid):
    from tools.model_review_v28.thermal_adapter import apply_thermal_profile
    env,spec=fixture(tmp_path,invalid=invalid);before=env.hourly.copy(deep=True);groups=env.groups.copy(deep=True)
    with pytest.raises(ValueError):apply_thermal_profile(env,spec)
    pd.testing.assert_frame_equal(env.hourly,before);pd.testing.assert_frame_equal(env.groups,groups)
