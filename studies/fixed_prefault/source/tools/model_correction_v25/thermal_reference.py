"""Bounded V25 original-date EnergyPlus outputs with unrounded API durations.

Each process runs one old physical case, appending outputs only.  Callbacks
observe completed intervals and never actuate EnergyPlus.  Data are offline
reference material and are unavailable to online IES state transitions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

from tools.model_correctness_v23.thermal_data import ZONES,read_native,sha256
from tools.engineering_completion_v24.thermal_nodes import sizing
from tools.model_correction_v25.thermal_observation import DETAILED,observation_idf,aggregate_completed,read_detailed_eso,align_completed_api


def collect_api(idf, weather, destination, installation, *, extra_channels=None):
    """Run the installed API and retain exact completed system dt and T/H/C."""
    sys.path.insert(0,str(installation))
    from pyenergyplus.api import EnergyPlusAPI
    api = EnergyPlusAPI()
    state = api.state_manager.new_state()
    handles, pending, detail, zones, failures, ports = {}, [], [], [], [], []
    extra_channels = extra_channels or {}
    requests = dict(DETAILED,reported_mean_C='Zone Mean Air Temperature')
    for zone in ZONES:
        for variable in requests.values():
            api.exchange.request_variable(state,variable,zone)
    for key,variable,_ in extra_channels.values():
        api.exchange.request_variable(state,variable,key)
    def ready(s):
        if not api.exchange.api_data_fully_ready(s) or api.exchange.warmup_flag(s) or api.exchange.kind_of_sim(s) != 3:
            return False
        if not handles:
            for zone in ZONES:
                for label,variable in requests.items():
                    handle = api.exchange.get_variable_handle(s,variable,zone)
                    if handle < 0:
                        raise ValueError(f'Missing API variable {zone}/{variable}')
                    handles[zone,label] = handle
            for label,(key,variable,_) in extra_channels.items():
                handle = api.exchange.get_variable_handle(s,variable,key)
                if handle < 0:
                    raise ValueError(f'Missing API port output {key}/{variable}')
                handles['port',label] = handle
        return True
    def system(s):
        try:
            if ready(s):
                duration = api.exchange.system_time_step(s)
                values = {zone:{label:api.exchange.get_variable_value(s,handles[zone,label])
                                for label in DETAILED} for zone in ZONES}
                port_values = {label:api.exchange.get_variable_value(s,handles['port',label])/divisor
                               for label,(_,_,divisor) in extra_channels.items()}
                pending.append((float(duration),values,port_values))
        except Exception as exc:
            failures.append(repr(exc))
            api.runtime.stop_simulation(s)
    def zone_end(s):
        try:
            if not ready(s):
                return
            dt_zone = float(api.exchange.zone_time_step(s))
            if not pending or abs(sum(row[0] for row in pending)-dt_zone) > 1e-10:
                raise ValueError('API system durations do not complete current zone timestep')
            month,day = api.exchange.month(s),api.exchange.day_of_month(s)
            dst = int(api.exchange.daylight_savings_time_indicator(s))
            clock = float(api.exchange.current_time(s))
            hour = int(api.exchange.hour(s))
            zone_number = int(api.exchange.zone_time_step_number(s))
            standard_end = pd.Timestamp(2006,month,day)+pd.Timedelta(hours=hour+zone_number*dt_zone)
            # Round only the complete original zone boundary, never a system
            # interval. Original zone timestep remains exactly ten minutes.
            standard_end = standard_end.round('s')
            bucket = standard_end+pd.Timedelta(hours=dst)
            elapsed = 0.
            for index,(duration,values,port_values) in enumerate(pending):
                elapsed += duration
                for zone in ZONES:
                    v = dict(values[zone])
                    v['heating_kW'] /= 1000
                    v['cooling_kW'] /= 1000
                    detail.append(dict(bucket_end=bucket,standard_bucket_end=standard_end,zone=zone,
                        substep_index=index,dt_h=duration,substep_elapsed_h=elapsed,
                        timestamp=standard_end-pd.Timedelta(hours=dt_zone-elapsed)+pd.Timedelta(hours=dst),
                        dst=dst,**v,net_sensible_kW=v['heating_kW']-v['cooling_kW']))
                if extra_channels:
                    ports.append(dict(bucket_end=bucket,standard_bucket_end=standard_end,
                        substep_index=index,dt_h=duration,**port_values))
            for zone in ZONES:
                zones.append(dict(bucket_end=bucket,standard_bucket_end=standard_end,zone=zone,
                    api_reported_mean_C=api.exchange.get_variable_value(s,handles[zone,'reported_mean_C']),
                    dt_h=dt_zone,substeps=len(pending),api_current_time_h=clock,api_hour=hour,
                    api_zone_time_step_number=zone_number,dst=dst))
            pending.clear()
        except Exception as exc:
            failures.append(repr(exc))
            api.runtime.stop_simulation(s)
    api.runtime.callback_end_system_timestep_after_hvac_reporting(state,system)
    api.runtime.callback_end_zone_timestep_after_zone_reporting(state,zone_end)
    api.runtime.set_console_output_status(state,False)
    command = ['-w',str(weather),'-d',str(destination),str(idf)]
    started,begin = datetime.now(timezone.utc).isoformat(),time.perf_counter()
    messages = []
    api.runtime.callback_message(state,lambda m:messages.append(m.decode('utf-8',errors='replace')))
    exit_code = api.runtime.run_energyplus(state,command)
    api.state_manager.delete_state(state)
    (destination/'api_messages.log').write_text('\n'.join(messages),encoding='utf-8')
    pd.DataFrame(detail).to_csv(destination/'API_DETAIL.csv',index=False)
    pd.DataFrame(zones).to_csv(destination/'API_TIMESTEP.csv',index=False)
    if extra_channels:
        pd.DataFrame(ports).to_csv(destination/'API_PORT.csv',index=False)
    errors = (destination/'eplusout.err').read_text(encoding='utf-8',errors='replace')
    receipt = dict(command=['EnergyPlusAPI',*command],started_utc=started,
        completed_utc=datetime.now(timezone.utc).isoformat(),exit_code=int(exit_code),
        elapsed_seconds=time.perf_counter()-begin,severe=errors.count('** Severe **'),
        fatal=errors.count('**  Fatal  **'),callback_failures=failures,
        api_detail_rows=len(detail),api_zone_rows=len(zones),
        executable_sha256=sha256(installation/'energyplus.exe'),
        api_dll_sha256=sha256(installation/'energyplusapi.dll'),
        exact_duration_source='EnergyPlusAPI system_time_step at end-system-after-reporting',
        idf_sha256=sha256(idf),temp=os.environ.get('TEMP'),tmp=os.environ.get('TMP'))
    (destination/'RUN_RECEIPT.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    if exit_code or failures:
        raise RuntimeError(f'EnergyPlus/API failure retained at {destination}: {failures}')
    return receipt


def verify_reference(source, original_idf, destination, receipt):
    name = original_idf.stem
    old,new = sizing(source/name/'eplusout.eio'),sizing(destination/'eplusout.eio')
    if old != new:
        raise ValueError('Output-only addition changed original equipment sizing')
    native = read_native(destination/'eplusout.sql',interval_minutes=10)
    detail = pd.read_csv(destination/'API_DETAIL.csv',parse_dates=['bucket_end','timestamp'])
    api_zone = pd.read_csv(destination/'API_TIMESTEP.csv',parse_dates=['bucket_end'])
    eso = read_detailed_eso(destination/'eplusout.eso')
    # ESO can contain sizing environments. Select original weather-period name.
    environment = eso.environment.iloc[-1]
    eso = eso[eso.environment == environment].copy()
    rows, aligned_all = [], []
    for zone in ZONES:
        raw_detail = detail[detail.zone == zone].reset_index(drop=True)
        d = align_completed_api(raw_detail,native[zone].index)
        aligned_all.append(d)
        e = eso[eso.zone == zone].reset_index(drop=True)
        if len(d) != len(e):
            raise ValueError(f'ESO/API substep count mismatch: {zone}/{len(d)}/{len(e)}')
        if np.max(np.abs(d.temperature_C-e.temperature_C)) > 1e-10:
            raise ValueError('ESO/API endpoint temperature disagreement')
        for field in ['heating_kW','cooling_kW']:
            if np.max(np.abs(d[field]-e[field])) > 1e-9:
                raise ValueError('ESO/API actual heat-rate disagreement')
        e = pd.concat([d[['bucket_end','substep_index','dt_h','timestamp']],e],axis=1)
        e.to_csv(destination/f'ESO_API_{zone}.csv',index=False)
        weighted = aggregate_completed(d)
        old_frame = pd.read_csv(source/f'{name}_{zone}.csv',index_col=0,parse_dates=True)
        if not native[zone].index.equals(old_frame.index) or not weighted.index.equals(old_frame.index):
            raise ValueError('Output addition changed original native clock')
        original_max = float(np.abs(native[zone].select_dtypes('number')-old_frame.select_dtypes('number')).to_numpy().max())
        api_values = api_zone[api_zone.zone == zone].api_reported_mean_C.to_numpy()
        if len(api_values) != len(native[zone]):
            raise ValueError('API complete-zone means count mismatch')
        api_means = pd.Series(api_values,index=native[zone].index)
        mapping = weighted.assign(reference_mean_C=native[zone].temperature_C,
            api_reported_mean_C=api_means,original_temperature_C=old_frame.temperature_C)
        mapping['mapping_error_C'] = mapping.temperature_C-mapping.reference_mean_C
        mapping['api_mean_error_C'] = mapping.api_reported_mean_C-mapping.reference_mean_C
        mapping.to_csv(destination/f'MAPPING_{zone}.csv',index_label='timestamp')
        rows.append(dict(zone=zone,original_native_max_difference=original_max,
            exact_mapping_max_error_C=float(mapping.mapping_error_C.abs().max()),
            api_mean_max_error_C=float(mapping.api_mean_error_C.abs().max()),
            eso_api_temperature_max_difference_C=float(abs(d.temperature_C-e.temperature_C).max()),
            eso_dt_rounding_max_h=float(abs(d.dt_h-e.eso_dt_h).max()),
            complete_zone_intervals=len(mapping),actual_system_intervals=len(d)))
    if any(r['original_native_max_difference'] > 1e-9 or r['exact_mapping_max_error_C'] > 1e-9 for r in rows):
        raise ValueError('Original forcing preservation or actual output mapping gate failed')
    pd.concat(aligned_all,ignore_index=True).to_csv(destination/'API_ALIGNED_DETAIL.csv',index=False)
    receipt.update(identical_sizing_fields=len(new),output_only=True,verification=rows,
                   original_idf_sha256=sha256(original_idf),old_source_readonly=True)
    (destination/'RUN_RECEIPT.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--case',required=True,choices=[f'native10_cal_{s}_{h}h' for s in ['summer','winter'] for h in [0,2,6]])
    p.add_argument('--installation',type=Path,default=Path('C:/EnergyPlusV26-2-0'))
    p.add_argument('--weather',type=Path,default=Path('inputs/energyplus/SanDiego_BrownField_TMY3.epw'))
    a = p.parse_args()
    target = a.destination/a.case
    target.mkdir(parents=True,exist_ok=False)
    temporary = a.destination/'temp'
    temporary.mkdir(parents=True,exist_ok=True)
    os.environ.update(TEMP=str(temporary.resolve()),TMP=str(temporary.resolve()))
    original = a.source/(a.case+'.idf')
    derived = a.destination/(a.case+'.idf')
    if derived.exists():
        raise ValueError('Existing output-only IDF is immutable')
    original_hash = sha256(original)
    derived.write_text(observation_idf(original.read_text(encoding='utf-8')),encoding='utf-8')
    receipt = collect_api(derived.resolve(),a.weather.resolve(),target.resolve(),a.installation.resolve())
    receipt = verify_reference(a.source,original,target,receipt)
    if sha256(original) != original_hash:
        raise ValueError('Original IDF changed')
    print(json.dumps(receipt,indent=2))


if __name__ == '__main__':
    main()
