"""Synthetic files exercise the loader contract; they are not native evidence."""
import copy
from collections import Counter
import hashlib
import importlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hospital_ies.resilience import ZONE_PRIORITY


def api():
    try:
        return importlib.import_module('tools.minute1_20261103.source')
    except ModuleNotFoundError as exc:
        if exc.name not in ('tools.minute1_20261103', 'tools.minute1_20261103.source'):
            raise
        pytest.fail('Explicit minute-source loader is missing', pytrace=False)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture_files(tmp_path, *, count=62):
    # Different adjacent intervals make hourly repetition and off-by-one reads visible.
    minutes = np.arange(1, count+1)
    starts = pd.Timestamp('2017-01-01')+pd.to_timedelta(minutes-1, unit='min')
    ends = starts+pd.Timedelta(minutes=1)
    clock = dict(minute_of_year=minutes, timestamp_start_standard=starts,
        timestamp_end_standard=ends, hour_of_year=(minutes-1)//60+1, source_dst=0)
    loads = pd.DataFrame(clock)
    loads['timestamp'] = ends
    for name in ('P_e_facility_kW', 'P_e_nonplant_kW', 'P_e_plant_kW',
                 'P_e_unresolved_kW', 'balance_residual_kW', 'Q_h_demand_kW',
                 'Q_c_demand_kW', 'Q_c_terminal_coils_kW', 'Q_c_chiller_evaporator_kW',
                 'Q_h_heating_coils_kW', 'Q_h_DHW_water_use_kW', 'outdoor_temperature_C'):
        loads[name] = 0.
    loads['outdoor_temperature_C'] = 10.+minutes/100.
    for g in range(4):
        for carrier in 'ehc':
            loads[f'P{g}_base_{carrier}_kW'] = 1.
            loads[f'P{g}_reference_{carrier}_kW'] = 1.
            loads[f'P{g}_{carrier}_kW'] = 1.
        for kind in ('fan', 'chw_pump', 'heat_pump'):
            loads[f'P{g}_{kind}_kW'] = 0.
    original_aux = pd.read_csv(Path(__file__).resolve().parents[1]/'inputs/sci_engineering/auxiliary_allocation_8760.csv', nrows=0)
    for column in original_aux:
        if column not in loads:
            loads[column] = 0.
    loads['source_fans_total_kW'] = loads['source_chw_pump_kW'] = loads['source_heat_pump_kW'] = 0.
    loads['P_e_facility_kW'] = loads['P_e_nonplant_kW'] = 4.
    loads['Q_c_demand_kW'] = loads['Q_c_terminal_coils_kW'] = 4.
    loads['Q_h_heating_coils_kW'] = 3.
    loads['Q_h_DHW_water_use_kW'] = 2.
    loads['v28_ordinary_DHW_source_delivered_kW'] = 1.
    loads['Q_h_demand_kW'] = 5.
    loads['P3_h_kW'] = 2.
    loads['sci_demand_boundary_contract'] = 'hospital_ies.fixed_direct_aux.v28.v1'
    thermal = pd.DataFrame(clock)
    for name in ('ordinary_DHW_target_kW', 'ordinary_DHW_original_delivered_kW',
        'space_heating_original_kW', 'central_heat_request_kW',
        'local_laundry_source_reference_NaturalGas_input_kW',
        'local_booster_source_reference_electric_input_already_in_Dref_kW',
        'booster_unmodelled_upstream_preheat_useful_kW'):
        thermal[name] = 0.
    thermal['ordinary_DHW_target_kW'] = 2.
    thermal['ordinary_DHW_original_delivered_kW'] = 1.
    thermal['space_heating_original_kW'] = 3.
    thermal['central_heat_request_kW'] = 5.
    rows = []
    group_counts=Counter(ZONE_PRIORITY.values())
    for i in range(count):
        for zone in ZONE_PRIORITY:
            count_at_priority=group_counts[ZONE_PRIORITY[zone]]
            rows.append(dict(**{k:loads.iloc[i][k] for k in clock}, timestamp=ends[i],
                zone_name=zone, zone_multiplier=1., interior_equipment_kW=.6/count_at_priority,
                interior_lighting_kW=.4/count_at_priority, zone_mean_air_temperature_C=21.+(i+1)/100.,
                thermostat_heating_setpoint_C=20., thermostat_cooling_setpoint_C=24.,
                zone_sensible_heating_kW=1./count_at_priority, zone_sensible_cooling_kW=1./count_at_priority,
                associated_reheat_coil_name=zone+' COIL', associated_reheat_coil_heating_kW=1./count_at_priority))
    frames = dict(loads=loads, critical_zones=pd.DataFrame(rows), thermal_components=thermal)
    meta = dict(schema='hospital_ies.minute1.metadata.v1', interval_minutes=1.,
        native_timestep_minutes=1., calendar_year=2017, warmup_excluded=True,
        source_provenance={'fixture_only': 'Synthetic fixture, never native source evidence'},
        support_intervals=[dict(start_minute_of_year=1, end_minute_of_year_exclusive=count+1)])
    manifest = dict(schema='hospital_ies.ep_minute_source_manifest.v1', files={})
    for name, frame in frames.items():
        path=tmp_path/(name+'.csv'); frame.to_csv(path,index=False)
        manifest['files'][name]=dict(path=path.name,sha256=sha(path),rows=len(frame))
    path=tmp_path/'metadata.json'; path.write_text(json.dumps(meta),encoding='utf8')
    manifest['files']['metadata']=dict(path=path.name,sha256=sha(path))
    return manifest, frames, meta


def rewrite(tmp_path, spec, name, frame):
    path=tmp_path/spec['files'][name]['path']
    frame.to_csv(path,index=False)
    spec['files'][name].update(sha256=sha(path),rows=len(frame))


def test_exact_interval_sampling_does_not_read_next_or_hourly_value(tmp_path):
    spec,_,_=fixture_files(tmp_path)
    source=api().MinuteSource(tmp_path,spec)
    for t,minute in ((0.,1),(.5,1),(1.,2),(59.,60),(60.,61)):
        h,row,groups=source.at_elapsed(t,1)
        assert h == (minute-1)//60+1
        assert row.minute_of_year == minute
        assert row.outdoor_temperature_C == pytest.approx(10.+minute/100.)
        assert groups['P3_h_kW'] == 2.
        assert row.minute1_thermal_service_trace['minute_of_year'] == minute
        assert row.minute1_thermal_service_trace['path'] == str((tmp_path/'thermal_components.csv').resolve())
        assert not hasattr(row,'v28_thermal_service_trace')
    thermal=source.thermal_at_elapsed(.5,1)
    assert set(thermal) == set(ZONE_PRIORITY)
    assert all(r.zone_mean_air_temperature_C == pytest.approx(21.01) for r in thermal.values())
    # Returned objects cannot modify future accesses or frozen source state.
    row.outdoor_temperature_C=-999.;groups['P3_h_kW']=999.
    assert source.at_elapsed(60.,1)[1].outdoor_temperature_C == pytest.approx(10.61)


@pytest.mark.parametrize('time,start',[(62.,1),(-.1,1),(float('nan'),1),(0.,2),(0.,True),(True,1)])
def test_outside_or_invalid_current_support_is_rejected(tmp_path,time,start):
    spec,_,_=fixture_files(tmp_path,count=2)
    with pytest.raises(ValueError):
        api().MinuteSource(tmp_path,spec).at_elapsed(time,start)


@pytest.mark.parametrize('damage',['missing_minute','duplicate_minute','wrong_hour','wrong_start','nonfinite','missing_column','zone_missing','zone_duplicate','heat_mismatch','reference_zero','aux_mismatch','electric_reference_zero'])
def test_incomplete_mixed_or_nonphysical_source_is_rejected(tmp_path,damage):
    spec,frames,_=fixture_files(tmp_path,count=3)
    name='loads'; frame=frames[name].copy()
    if damage=='missing_minute':frame=frame.drop(index=1)
    elif damage=='duplicate_minute':frame=pd.concat([frame,frame.iloc[[0]]],ignore_index=True)
    elif damage=='wrong_hour':frame.loc[0,'hour_of_year']=2
    elif damage=='wrong_start':frame.loc[0,'timestamp_start_standard']=pd.Timestamp('2017-01-01 00:01')
    elif damage=='nonfinite':frame.loc[0,'P_e_nonplant_kW']=float('inf')
    elif damage=='missing_column':frame=frame.drop(columns=['P0_fan_kW'])
    elif damage=='heat_mismatch':frame.loc[0,'Q_h_demand_kW']=999.
    elif damage=='reference_zero':
        frame.loc[0,'P3_reference_h_kW']=0.
    elif damage=='aux_mismatch':frame.loc[0,'P0_fan_kW']=1.
    elif damage=='electric_reference_zero':
        frame.loc[0,'P0_reference_e_kW']=0.
        frame.loc[0,'P0_base_e_kW']=0.
    elif damage=='zone_missing':
        name='critical_zones';frame=frames[name].iloc[1:]
    elif damage=='zone_duplicate':
        name='critical_zones';frame=pd.concat([frames[name],frames[name].iloc[[0]]],ignore_index=True)
    rewrite(tmp_path,spec,name,frame)
    with pytest.raises(ValueError):api().MinuteSource(tmp_path,spec)


def test_hash_and_native_metadata_are_required_before_loading(tmp_path):
    spec,_,meta=fixture_files(tmp_path,count=2)
    changed=copy.deepcopy(spec);changed['files']['loads']['sha256']='0'*64
    with pytest.raises(ValueError,match='hash'):api().MinuteSource(tmp_path,changed)
    meta['warmup_excluded']=False
    p=tmp_path/'metadata.json';p.write_text(json.dumps(meta),encoding='utf8')
    spec['files']['metadata']['sha256']=sha(p)
    with pytest.raises(ValueError,match='warmup'):api().MinuteSource(tmp_path,spec)


def test_wrong_zone_priority_allocation_cannot_hide_behind_correct_building_totals(tmp_path):
    spec,frames,_=fixture_files(tmp_path,count=2)
    zones=frames['critical_zones'].copy()
    zones.loc[0,'zone_sensible_cooling_kW']+=.2
    rewrite(tmp_path,spec,'critical_zones',zones)
    with pytest.raises(ValueError,match='priority.*source|source.*priority'):
        api().MinuteSource(tmp_path,spec)
