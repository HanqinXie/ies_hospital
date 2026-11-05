"""Adopt verified ordinary-DHW target; preserve source space heat and local sources."""
from __future__ import annotations
import copy,hashlib,json
from pathlib import Path
import numpy as np
import pandas as pd

def _sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def apply_thermal_profile(env,spec):
    files=spec.get('files',{})
    if set(files)!={'thermal_service_components','thermal_input_metadata'}:raise ValueError('Complete registered thermal input/metadata pair required')
    paths={}
    for name,item in files.items():
        p=Path(item['path']);p=p if p.is_absolute() else env.root/p
        if not p.is_file() or _sha(p)!=item['sha256']:raise ValueError('Registered thermal input changed: '+name)
        paths[name]=p
    meta=json.loads(paths['thermal_input_metadata'].read_text(encoding='utf8'))
    if meta.get('schema')!='hospital_ies.v28.adopted_thermal_input.v1' or meta.get('boundary')!='ordinary_wateruse_central_target_v28':raise ValueError('Unregistered ordinary thermal-service boundary')
    source=meta.get('source_input',{})
    if source.get('path')!='inputs/loads/hospital_ies_demand_8760.csv' or _sha(env.root/source['path'])!=source.get('sha256'):raise ValueError('Different original heat-source input')
    if meta.get('target_column')!='ordinary_DHW_target_kW' or meta.get('legacy_delivery_column')!='Q_h_DHW_water_use_kW':raise ValueError('Different target/delivery column contract')
    frame=pd.read_csv(paths['thermal_service_components'],float_precision='round_trip')
    columns=['ordinary_DHW_target_kW','ordinary_DHW_original_delivered_kW','space_heating_original_kW','central_heat_request_kW','local_laundry_source_reference_NaturalGas_input_kW','local_booster_source_reference_electric_input_already_in_Dref_kW','booster_unmodelled_upstream_preheat_useful_kW']
    if not {'hour_of_year','timestamp',*columns}<=set(frame):raise ValueError('Incomplete thermal service components')
    if len(frame)!=8760 or frame.hour_of_year.tolist()!=list(range(1,8761)):raise ValueError('Exactly ordered unique8760 source hours required')
    numeric=frame[columns].to_numpy(float)
    if not np.isfinite(numeric).all() or (numeric<0).any():raise ValueError('Negative/nonfinite thermal target or source component; no clipping')
    if not np.allclose(frame.central_heat_request_kW,frame.space_heating_original_kW+frame.ordinary_DHW_target_kW,atol=1e-8,rtol=0):raise ValueError('Central thermal target components do not close')
    indexed=frame.set_index('hour_of_year');hours=sorted(env.allowed_hours)
    current=env.hourly.set_index('hour_of_year').loc[hours];candidate=indexed.loc[hours]
    if not np.array_equal(pd.to_datetime(current.timestamp).to_numpy(),pd.to_datetime(candidate.timestamp).to_numpy()):raise ValueError('Thermal source standard-time clock mismatch')
    if not np.allclose(current.Q_h_DHW_water_use_kW,candidate.ordinary_DHW_original_delivered_kW,atol=1e-8,rtol=0):raise ValueError('Original ordinary-DHW delivered baseline mismatch')
    if not np.allclose(current.Q_h_demand_kW,candidate.space_heating_original_kW+candidate.ordinary_DHW_original_delivered_kW,atol=1e-8,rtol=0):raise ValueError('Original total/space/DHW heat baseline mismatch')
    heat_columns=[f'P{g}_h_kW' for g in range(4)]
    group=env.groups.loc[hours,heat_columns].to_numpy(float)
    if not np.isfinite(group).all() or (group<0).any() or not np.allclose(group.sum(axis=1),current.Q_h_demand_kW,atol=1e-8,rtol=0):raise ValueError('Original priority heat boundary does not close')
    delta=(candidate.ordinary_DHW_target_kW-candidate.ordinary_DHW_original_delivered_kW).to_numpy(float)
    proposed=group.copy();proposed[:,3]+=delta
    if (proposed<0).any() or not np.allclose(proposed.sum(axis=1),candidate.central_heat_request_kW,atol=1e-8,rtol=0):raise ValueError('New priority heat target does not close; no clipping')
    # Commit after all validation. Source references and all electric/cold
    # inputs remain original; the common builder pays the heat-pump response.
    hourly=env.hourly.copy(deep=True);groups=env.groups.copy(deep=True)
    ix=hourly.set_index('hour_of_year').index.get_indexer(hours)
    for column in ['v28_thermal_service_trace','v28_local_thermal_source_reference']:
        hourly[column]=pd.Series(None,index=hourly.index,dtype=object)
    hourly.loc[hourly.index[ix],'v28_ordinary_DHW_source_delivered_kW']=candidate.ordinary_DHW_original_delivered_kW.to_numpy()
    hourly.loc[hourly.index[ix],'Q_h_DHW_water_use_kW']=candidate.ordinary_DHW_target_kW.to_numpy()
    hourly.loc[hourly.index[ix],'Q_h_demand_kW']=candidate.central_heat_request_kW.to_numpy()
    for pos,h in zip(ix,hours):
        row=candidate.loc[h]
        hourly.at[hourly.index[pos],'v28_thermal_service_trace']=dict(path=files['thermal_service_components']['path'],sha256=files['thermal_service_components']['sha256'],hour_of_year=int(h),request_column='ordinary_DHW_target_kW',legacy_delivery_column='Q_h_DHW_water_use_kW',boundary='ordinary_wateruse_central_target_v28')
        hourly.at[hourly.index[pos],'v28_local_thermal_source_reference']=dict(hour_of_year=int(h),laundry_naturalgas_kW=float(row.local_laundry_source_reference_NaturalGas_input_kW),booster_electric_kW_already_in_Dref=float(row.local_booster_source_reference_electric_input_already_in_Dref_kW),booster_unmodelled_upstream_preheat_useful_kW=float(row.booster_unmodelled_upstream_preheat_useful_kW),assurance='Source-reference ledger only; no local dynamic service or upstream central assurance inferred')
    groups.loc[hours,heat_columns]=proposed
    env.hourly=hourly;env.groups=groups
    return dict(boundary='ordinary_wateruse_central_target_v28',applied_hours=len(hours),input_bindings=copy.deepcopy(files),signed_request_delta_kW=delta.tolist(),source_reference_unchanged=True,electric_and_cold_inputs_unchanged=True,ordinary_DHW_allocated_to_original_P3_residual=True,local_heater_energy_is_source_reference_only=True,booster_electric_already_in_direct_baseline=True,booster_60C_preheat_central_assurance=False,thermal_coupling=None)
