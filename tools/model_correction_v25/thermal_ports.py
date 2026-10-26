"""Limited physical cooling-source/fan-path reference checks, no online claim."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from tools.model_correctness_v23.thermal_data import ZONES,read_native,sha256,_replace_object
from tools.engineering_completion_v24.thermal_nodes import sizing
from tools.model_correction_v25.thermal_observation import observation_idf,align_completed_api
from tools.model_correction_v25.thermal_reference import collect_api

CASES=('normal','one_main_source_off','all_cold_sources_off','or_fan_path_off')


def channels():
    result={'event_available':('V25_PORT_AVAILABILITY','Schedule Value',1.),
        'fan_mass_kg_s':('VAV_OR Fan','Fan Air Mass Flow Rate',1.),
        'fan_electric_kW':('VAV_OR Fan','Fan Electricity Rate',1000.),
        'ahu_coil_total_cooling_kW':('VAV_OR Cooling Coil','Cooling Coil Total Cooling Rate',1000.),
        'ahu_coil_sensible_cooling_kW':('VAV_OR Cooling Coil','Cooling Coil Sensible Cooling Rate',1000.),
        'plant_cooling_demand_kW':('CoolSys1_Supply','Plant Supply Side Cooling Demand Rate',1000.)}
    for name,key in [('source1','CoolSys1 Chiller1'),('source2','CoolSys1 Chiller2'),('hrc','Heat Recovery Chiller')]:
        result[name+'_evap_cooling_kW']=(key,'Chiller Evaporator Cooling Rate',1000.)
        result[name+'_evap_mass_kg_s']=(key,'Chiller Evaporator Mass Flow Rate',1.)
        result[name+'_electric_kW']=(key,'Chiller Electricity Rate',1000.)
    for zone in ZONES:
        label=zone.split('_')[0]
        key=zone.replace('_FLR_','_Flr_').replace('MULT','Mult')
        result[label+'_supply_mass_kg_s']=(key+' VAV Box Outlet Node','System Node Mass Flow Rate',1.)
        result[label+'_supply_C']=(key+' VAV Box Outlet Node','System Node Temperature',1.)
        result[label+'_supply_humidity']=(key+' VAV Box Outlet Node','System Node Humidity Ratio',1.)
        result[label+'_room_humidity']=(zone,'Zone Air Humidity Ratio',1.)
        result[label+'_reheat_kW']=(key+' VAV Box Reheat Coil','Heating Coil Heating Rate',1000.)
        result[label+'_system_air_kW']=(zone,'Zone Air Heat Balance System Air Transfer Rate',1000.)
    return result


def port_idf(original,case):
    if case not in CASES:
        raise ValueError('Only the four preregistered reference port cases are permitted')
    result=observation_idf(original)
    if case=='normal':
        result+='\nSchedule:Compact,V25_PORT_AVAILABILITY,Fraction,Through:12/31,For:AllDays,Until:24:00,1;\n'
    else:
        result+='\nSchedule:Compact,V25_PORT_AVAILABILITY,Fraction,Through:7/19,For:AllDays,Until:24:00,1,Through:7/20,For:AllDays,Until:12:00,1,Until:18:00,0,Until:24:00,1,Through:12/31,For:AllDays,Until:24:00,1;\n'
    if case=='or_fan_path_off':
        result=_replace_object(result,'AvailabilityManagerAssignmentList','VAV_OR Availability Manager List',
            lambda f:[*f,'AvailabilityManager:ScheduledOff','V25_OR_ForcedOff'])
        result+='\nAvailabilityManager:ScheduledOff,V25_OR_ForcedOff,V25_PORT_AVAILABILITY;\n'
    elif case in ('one_main_source_off','all_cold_sources_off'):
        # Component EMS0 alone failed actual qualification. Operation schemes
        # express actual equipment eligibility, preserving all nominal sizes.
        def main_operation(fields):
            if fields[4] != 'PlantOnSched':
                raise ValueError('Unexpected original operation availability')
            fields[4]='V25_PORT_AVAILABILITY'
            if case=='one_main_source_off':
                fields+=['PlantEquipmentOperation:CoolingLoad','V25_LIMITED_OPERATION','V25_PORT_LIMIT_ACTIVE']
            return fields
        result=_replace_object(result,'PlantEquipmentOperationSchemes','CoolSys1 Loop Operation Scheme List',main_operation)
        if case=='one_main_source_off':
            result+='\nSchedule:Compact,V25_PORT_LIMIT_ACTIVE,Fraction,Through:7/19,For:AllDays,Until:24:00,0,Through:7/20,For:AllDays,Until:12:00,0,Until:18:00,1,Until:24:00,0,Through:12/31,For:AllDays,Until:24:00,0;\n'
            result+='\nPlantEquipmentOperation:CoolingLoad,V25_LIMITED_OPERATION,0,100000000000000,CoolSys1 Equipment List 1;\n'
        else:
            def hrc_operation(fields):
                if fields[4] != 'PlantOnSched':
                    raise ValueError('Unexpected original HRC operation availability')
                fields[4]='V25_PORT_AVAILABILITY'
                return fields
            result=_replace_object(result,'PlantEquipmentOperationSchemes','Heat Recovery Chiller Bypass Loop Operation',hrc_operation)
    for key,variable,_ in channels().values():
        result+=f'\nOutput:Variable,{key},{variable},Detailed;'
    return result+'\n'


def port_integrals(frame,*,multiplier):
    values=frame[['dt_h','heating_kW','cooling_kW']].to_numpy(float)
    if not np.isfinite(values).all() or np.any(values[:,0]<=0) or np.any(values[:,1:]<0) or not np.isfinite(multiplier) or multiplier<=0:
        raise ValueError('Positive durations/multiplier and actual finite H/C required')
    heat=float(np.dot(values[:,0],values[:,1]))
    cold=float(np.dot(values[:,0],values[:,2]))
    return dict(representative_heating_kWh=heat,representative_cooling_kWh=cold,
        representative_net_sensible_kWh=heat-cold,physical_group_net_sensible_kWh=(heat-cold)*multiplier)


def port_qualification(case,disabled,fan,zoneflows,restored_fan,restored_cooling,integral_error,*,restored_zoneflows=None):
    """Qualify actual execution and restoration, never the command label."""
    values=[*disabled.values(),fan,*zoneflows.values(),restored_fan,restored_cooling,integral_error]
    if not np.isfinite(values).all() or abs(integral_error)>=1e-8:
        return False
    expected={'source2'} if case=='one_main_source_off' else {'source1','source2','hrc'} if case=='all_cold_sources_off' else set()
    if set(disabled)!=expected or any(abs(value)>=1e-8 for value in disabled.values()):
        return False
    if case in ('one_main_source_off','all_cold_sources_off') and restored_cooling<=1e-8:
        return False
    if case=='or_fan_path_off':
        recovered=restored_zoneflows or {}
        if (set(zoneflows)!=set(ZONES) or set(recovered)!=set(ZONES) or
                abs(fan)>=1e-9 or any(abs(v)>=1e-9 for v in zoneflows.values()) or
                restored_fan<=1e-9 or any(not np.isfinite(v) or v<=1e-9 for v in recovered.values())):
            return False
    return case in CASES


def verify_port(source,original,destination,case,receipt):
    old,new=sizing(source/'native10_cal_summer_0h/eplusout.eio'),sizing(destination/'eplusout.eio')
    if old!=new:
        raise ValueError('Reference intervention changed original equipment sizing')
    native=read_native(destination/'eplusout.sql',interval_minutes=10)
    detail=pd.read_csv(destination/'API_DETAIL.csv',parse_dates=['bucket_end'])
    ports=pd.read_csv(destination/'API_PORT.csv',parse_dates=['bucket_end'])
    aligned=align_completed_api(ports.assign(temperature_C=0.),native[ZONES[0]].index)
    aligned.to_csv(destination/'PORT_DETAILED.csv',index=False)
    config=json.loads(Path('configs/model_correctness_v23/temperature_parameters.json').read_text())
    event=pd.Timestamp(2006,7,20,12)
    end=event+pd.Timedelta(hours=6)
    rows=[]
    aligned_zones=[]
    for zone in ZONES:
        actual=native[zone]
        baseline=pd.read_csv(source/f'native10_cal_summer_0h_{zone}.csv',index_col=0,parse_dates=True)
        if not actual.index.equals(baseline.index):
            raise ValueError('Port case altered original complete-zone clocks')
        difference=actual.select_dtypes('number')-baseline.select_dtypes('number')
        pre=float(difference.loc[:event].abs().to_numpy().max())
        if pre>1e-9 or (case=='normal' and float(difference.abs().to_numpy().max())>1e-9):
            raise ValueError('Reference changed outside its registered intervention')
        d=align_completed_api(detail[detail.zone==zone],actual.index)
        aligned_zones.append(d)
        for phase,mask in dict(normal_before=d.bucket_end<=event,event=(d.bucket_end>event)&(d.bucket_end<=end),recovery_first6h=(d.bucket_end>end)&(d.bucket_end<=end+pd.Timedelta(hours=6)),recovery_tail=d.bucket_end>end+pd.Timedelta(hours=6)).items():
            subset=d.loc[mask]
            summary=port_integrals(subset,multiplier=config['zone_multipliers'][zone])
            native_mask=actual.index.isin(subset.bucket_end.unique())
            heat=float(actual.heating_kW[native_mask].sum()/6)
            cold=float(actual.cooling_kW[native_mask].sum()/6)
            rows.append(dict(case_id=case,zone=zone,phase=phase,pre_event_original_max_abs_difference=pre,
                **summary,native_heat_integral_kWh=heat,native_cold_integral_kWh=cold,
                detailed_native_heat_integral_difference_kWh=summary['representative_heating_kWh']-heat,
                detailed_native_cold_integral_difference_kWh=summary['representative_cooling_kWh']-cold))
        actual.assign(net_sensible_kW=actual.heating_kW-actual.cooling_kW,
            normal_temperature_C=baseline.temperature_C,delta_temperature_C=actual.temperature_C-baseline.temperature_C).to_csv(destination/f'PORT_NATIVE_{zone}.csv',index_label='timestamp')
    pd.concat(aligned_zones,ignore_index=True).to_csv(destination/'API_ALIGNED_DETAIL.csv',index=False)
    pd.DataFrame(rows).to_csv(destination/'PORT_INTEGRALS.csv',index=False)
    eventmask=(aligned.bucket_end>event)&(aligned.bucket_end<=end)
    recoverymask=aligned.bucket_end>end
    target_source=['source2'] if case=='one_main_source_off' else ['source1','source2','hrc'] if case=='all_cold_sources_off' else []
    disabled={s:float(abs(aligned.loc[eventmask,s+'_evap_cooling_kW']).max()) for s in target_source}
    fan=float(aligned.loc[eventmask,'fan_mass_kg_s'].abs().max())
    zoneflows={z:float(aligned.loc[eventmask,z.split('_')[0]+'_supply_mass_kg_s'].abs().max()) for z in ZONES}
    maximum_integral=max(max(abs(r['detailed_native_heat_integral_difference_kWh']),abs(r['detailed_native_cold_integral_difference_kWh'])) for r in rows)
    restored_fan=float(aligned.loc[recoverymask,'fan_mass_kg_s'].max())
    restored_cooling=float(aligned.loc[recoverymask,['source1_evap_cooling_kW','source2_evap_cooling_kW','hrc_evap_cooling_kW']].sum(axis=1).max())
    restored_zoneflows={z:float(aligned.loc[recoverymask,z.split('_')[0]+'_supply_mass_kg_s'].max()) for z in ZONES}
    qualified=port_qualification(case,disabled,fan,zoneflows,restored_fan,restored_cooling,maximum_integral,restored_zoneflows=restored_zoneflows)
    receipt.update(case_id=case,identical_sizing_fields=len(new),actual_disabled_source_max_cooling_kW=disabled,
        event_fan_max_mass_kg_s=fan,event_zone_supply_max_mass_kg_s=zoneflows,
        post_release_fan_max_mass_kg_s=restored_fan,
        post_release_total_source_max_cooling_kW=restored_cooling,
        post_release_zone_supply_max_mass_kg_s=restored_zoneflows,
        native_detailed_integral_max_difference_kWh=maximum_integral,interface_reference_gate_passed=qualified,
        input_boundary='Actual delivered representative-zone H-C; multiply once; gross plant/coil and fan electricity not added again',
        default_thermal_coupling=None,online_or_feedback_coupling_implemented=False,
        original_idf_sha256=sha256(original),registration_sha256=sha256('configs/model_correction_v25/thermal_port_dispatch_registration.json' if case in ('one_main_source_off','all_cold_sources_off') else 'configs/model_correction_v25/thermal_port_registration.json'))
    (destination/'PORT_RECEIPT.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    if not qualified:
        raise ValueError('Actual physical port qualification failed; evidence retained')
    return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--case',required=True,choices=CASES)
    p.add_argument('--installation',type=Path,default=Path('C:/EnergyPlusV26-2-0'))
    p.add_argument('--weather',type=Path,default=Path('inputs/energyplus/SanDiego_BrownField_TMY3.epw'))
    a=p.parse_args()
    target=a.destination/a.case
    target.mkdir(parents=True,exist_ok=False)
    temp=a.destination/'temp'
    temp.mkdir(parents=True,exist_ok=True)
    os.environ.update(TEMP=str(temp.resolve()),TMP=str(temp.resolve()))
    original=a.source/'native10_cal_summer_0h.idf'
    original_hash=sha256(original)
    idf=a.destination/(a.case+'.idf')
    if idf.exists():
        raise ValueError('Cannot overwrite an intervention IDF')
    idf.write_text(port_idf(original.read_text(encoding='utf-8'),a.case),encoding='utf-8')
    receipt=collect_api(idf.resolve(),a.weather.resolve(),target.resolve(),a.installation.resolve(),extra_channels=channels())
    receipt=verify_port(a.source,original,target,a.case,receipt)
    if sha256(original)!=original_hash:
        raise ValueError('Original source IDF changed')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    main()
