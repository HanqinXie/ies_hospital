"""Read-only native cold-water ledgers; requests never fill energy residuals."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

from tools.model_correction_v26.cold_contract import residual_summary,reconstruct_tank
from tools.model_correction_v26.cold_observer import require_normal_gate

LOOP_MASS_CONVERSION_C=5.05 # DataGlobalConstants.hh:591, exact4bd7a1f26f.


def loop_mass_from_volume(volume_m3,density_function):
    """Fixed simulator water mass; do not use design or current water T."""
    density=float(density_function(LOOP_MASS_CONVERSION_C))
    if not np.isfinite(volume_m3) or not np.isfinite(density) or min(volume_m3,density)<=0:
        raise ValueError('Finite positive water volume/density required')
    return float(volume_m3*density)


def water_residual(storage,coils,pumps,evaporators,external_boundary):
    arrays=[np.asarray(x,dtype=float) for x in [storage,coils,pumps,evaporators,external_boundary]]
    if any(x.shape!=arrays[0].shape or not np.isfinite(x).all() for x in arrays):
        raise ValueError('Unknown or mismatched actual water ledger terms')
    return arrays[0]-arrays[1]-arrays[2]+arrays[3]-arrays[4]


def channel_label(channels,key,variable):
    found=[label for label,c in channels.items() if c['key'].lower()==key.lower() and c['variable']==variable]
    if len(found)!=1:raise ValueError(f'Missing/ambiguous measured contract {key}/{variable}')
    return found[0]


def phase_masks(frame,case,channels):
    ends=pd.to_datetime(frame.timestamp)
    start=pd.Timestamp(2006,case['month'],case['day'],12)
    end=start+pd.Timedelta(hours=case['event_hours'] or 6)
    marker=[label for label,c in channels.items() if c['key'].upper()=='V25_PORT_AVAILABILITY']
    if marker and np.any(frame[marker[0]]<.5):
        off=frame[marker[0]]<.5
        first=np.flatnonzero(off.to_numpy())[0]
        start=ends.iloc[first]-pd.Timedelta(hours=float(frame.dt_h.iloc[first]))
        end=ends[off].iloc[-1]
    return dict(all=np.ones(len(frame),dtype=bool),
        pre_event=(ends>start-pd.Timedelta(hours=6))&(ends<=start),
        event=(ends>start)&(ends<=end),recovery_first6h=(ends>end)&(ends<=end+pd.Timedelta(hours=6)),
        event_first10min=(ends>start)&(ends<=start+pd.Timedelta(minutes=10)),
        event_first60min=(ends>start)&(ends<=start+pd.Timedelta(minutes=60)),
        event_last60min=(ends>end-pd.Timedelta(minutes=60))&(ends<=end),
        recovery_first10min=(ends>end)&(ends<=end+pd.Timedelta(minutes=10)),
        recovery_first60min=(ends>end)&(ends<=end+pd.Timedelta(minutes=60)))


def analyze_case(registration,case_id,destination,*,absolute_floor_kWh,tolerance=None):
    reg=json.loads(Path(registration).read_text(encoding='utf-8'))
    case=next(x for x in reg['cases'] if x['case_id']==case_id)
    require_normal_gate(case,tolerance,reg['channel_contract_sha256'])
    source=Path(reg['output_root'])/case_id
    qualification=json.loads((source/'OBSERVATION_QUALIFICATION.json').read_text(encoding='utf-8'))
    if not qualification['output_only_qualified']:raise ValueError('Unqualified observation cannot enter ledger')
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=False)
    frame=pd.read_csv(source/'OBSERVED_DETAIL.csv')
    channels=case['channels'];topology=reg['topology']
    def get(key,var):
        values=frame[channel_label(channels,key,var)].to_numpy(float)
        if not np.isfinite(values).all():raise ValueError('Unknown measured energy state')
        return values
    sys.path.insert(0,reg['installation'])
    from pyenergyplus.api import EnergyPlusAPI
    # The Functional API initializes schedule/output managers even without a
    # simulation. Put its tiny bootstrap files in this new E output directory.
    previous_cwd=Path.cwd()
    try:
        os.chdir(destination)
        api=EnergyPlusAPI();state=api.state_manager.new_state();water=api.functional.glycol(state,'water')
        density=loop_mass_from_volume(1.,lambda temperature:water.density(state,temperature))
    finally:
        os.chdir(previous_cwd)
    cp=lambda t:water.specific_heat(state,float(t))
    con=sqlite3.connect(f'file:{(source/"eplusout.sql").as_posix()}?mode=ro',uri=True)
    volumes={name.lower():float(value) for name,value in con.execute("select CompName,Value from ComponentSizes where lower(CompType)='plantloop' and Description='Plant Loop Volume'")}
    con.close()
    summaries=[];detail={};endpoint_rows=[]
    phases=phase_masks(frame,case,channels)
    def record(boundary,residual,terms):
        detail[boundary]=np.asarray(residual)
        terms=np.asarray(terms,dtype=float).T
        for phase,mask in phases.items():
            mask=np.asarray(mask,dtype=bool)
            if np.any(mask):summaries.append(dict(boundary=boundary,phase=phase,**residual_summary(np.asarray(residual)[mask],frame.dt_h.to_numpy()[mask],terms[mask],absolute_floor_kWh=absolute_floor_kWh)))
    pump_rates={c['name']:get(c['name'],'Pump Fluid Heat Gain Rate') for c in topology['pumps']}
    source_rates={c['name']:get(c['name'],'Chiller Evaporator Cooling Rate') for c in topology['chillers']}
    coil_rates={}
    for c in topology['cooling_coils']:
        name=c['name'];q=get(name,'Cooling Coil Total Cooling Rate');coil_rates[name]=q
        m=get(c['inlet'],'System Node Mass Flow Rate')
        hin=get(c['inlet'],'System Node Enthalpy');hout=get(c['outlet'],'System Node Enthalpy')
        water_q=m*(hout-hin)
        air_m=get(c['air_inlet'],'System Node Mass Flow Rate')
        air_q=air_m*(get(c['air_inlet'],'System Node Enthalpy')-get(c['air_outlet'],'System Node Enthalpy'))
        record('coil_water_local_enthalpy__'+name,water_q-q,[water_q,q])
        record('coil_air_total__'+name,air_q-q,[air_q,q])
        detail['coil_total__'+name]=q
        detail['coil_sensible__'+name]=get(name,'Cooling Coil Sensible Cooling Rate')
        detail['coil_latent__'+name]=q-detail['coil_sensible__'+name]
        tin=get(c['inlet'],'System Node Temperature');tout=get(c['outlet'],'System Node Temperature')
        water_temp_q=m*np.array([cp(x) for x in tin])*(tout-tin)
        record('coil_water_local_cpDeltaT__'+name,water_temp_q-q,[water_temp_q,q])
    hx_gains={l['name']:[] for l in topology['cold_loops']}
    zero_flow_rows=[]
    for hx in topology['heat_exchangers']:
        name=hx['name'];native=get(name,'Fluid Heat Exchanger Heat Transfer Rate')
        side_gains={}
        flows={}
        for side in ['Supply','Demand']:
            prefix=f'Fluid Heat Exchanger Loop {side} Side '
            m=get(name,prefix+'Mass Flow Rate');flows[side]=m
            tin=get(name,prefix+'Inlet Temperature');tout=get(name,prefix+'Outlet Temperature')
            gain=m*np.array([cp(x) for x in tin])*(tout-tin);side_gains[side]=gain
            expected=-native if side=='Supply' else native
            record('hx_'+side.lower()+'__'+name,gain-expected,[gain,expected])
        record('hx_two_sides__'+name,side_gains['Supply']+side_gains['Demand'],list(side_gains.values()))
        hx_gains[hx['api_supply_loop']].append(-native)
        hx_gains[hx['api_demand_loop']].append(native)
        zero=(flows['Supply']==0)|(flows['Demand']==0)
        zero_flow_rows.append(dict(hx=name,zero_flow_rows=int(zero.sum()),maximum_native_heat_at_zero_flow_W=float(abs(native[zero]).max()) if np.any(zero) else None))
    all_storage=[];all_coils=[];all_pumps=[];all_sources=[]
    for loop in topology['cold_loops']:
        name=loop['name'];storage=[]
        for side in loop['sides']:
            side_name=side['side']
            mean=get(name,f'Plant {side_name} Side Lumped Capacitance Temperature')
            heat=get(name,f'Plant {side_name} Side Lumped Capacitance Heat Transport Rate')
            stored=get(name,f'Plant {side_name} Side Lumped Capacitance Heat Storage Rate');storage.append(stored)
            pumps=[pump_rates[c['name']] for c in topology['pumps'] if c['loop']==name and c['side']==side_name]
            pump=sum(pumps,np.zeros(len(frame)))
            record('tank_native_equation__'+name+'__'+side_name,stored-heat-pump,[stored,heat,pump])
            opposite=next(s for s in loop['sides'] if s['side']!=side_name)
            tin=get(opposite['outlet'],'System Node Temperature');flow=get(opposite['outlet'],'System Node Mass Flow Rate')
            mass=.5*volumes[name.lower()]*density
            predicted=[];last_endpoint=None
            for i in range(len(frame)):
                reconstructed=reconstruct_tank(mean[i],tin[i],flow[i],pump[i],mass,float(frame.dt_h.iloc[i])*3600,cp)
                predicted.append(reconstructed['storage_W'])
                endpoint_rows.append(dict(loop=name,side=side_name,row=i,timestamp=frame.timestamp.iloc[i],dt_h=frame.dt_h.iloc[i],mass_kg=mass,
                    observed_mean_C=mean[i],observed_storage_W=stored[i],observed_transport_W=heat[i],observed_pump_W=pump[i],
                    source_equation_start_continuity_error_C=None if last_endpoint is None else reconstructed['start_C']-last_endpoint,**reconstructed))
                last_endpoint=reconstructed['endpoint_C']
            record('tank_endpoint_reconstruction__'+name+'__'+side_name,np.asarray(predicted)-stored,[np.asarray(predicted),stored])
        coils=[coil_rates[c['name']] for c in topology['cooling_coils'] if c['loop']==name]
        pumps=[pump_rates[c['name']] for c in topology['pumps'] if c['loop']==name]
        sources=[source_rates[c['name']] for c in topology['chillers'] if c['loop']==name]
        boundary=hx_gains[name]
        total=lambda rows:sum(rows,np.zeros(len(frame)))
        residual=water_residual(total(storage),total(coils),total(pumps),total(sources),total(boundary))
        record('loop_complete__'+name,residual,coils+pumps+sources+boundary)
        all_storage.extend(storage);all_coils.extend(coils);all_pumps.extend(pumps);all_sources.extend(sources)
    total=lambda rows:sum(rows,np.zeros(len(frame)))
    overall=water_residual(total(all_storage),total(all_coils),total(all_pumps),total(all_sources),np.zeros(len(frame)))
    record('cold_three_loops_complete',overall,all_coils+all_pumps+all_sources)
    detail.update(native_storage_total_W=total(all_storage),coil_total_W=total(all_coils),pump_total_W=total(all_pumps),evaporator_total_W=total(all_sources))
    for key,values in source_rates.items():detail['evaporator__'+key]=values
    for key,values in pump_rates.items():detail['pump__'+key]=values
    api.state_manager.delete_state(state)
    pd.DataFrame(dict(timestamp=frame.timestamp,dt_h=frame.dt_h,**detail)).to_csv(destination/'BALANCE_DETAIL.csv',index=False)
    pd.DataFrame(endpoint_rows).to_csv(destination/'TANK_ENDPOINT_RECONSTRUCTION.csv',index=False)
    summary=pd.DataFrame(summaries);summary.to_csv(destination/'BALANCE_SUMMARY.csv',index=False)
    report=dict(case_id=case_id,normal_tolerance_establishment_only=case['normal_for_tolerance'] and tolerance is None,
        interface_qualified=False,absolute_floor_kWh=absolute_floor_kWh,channel_contract_sha256=reg['channel_contract_sha256'],
        raw_observation_source=str(source),loop_volumes_m3=volumes,water_density_at_InitConvTemp_5p05C_kg_m3=density,
        mass_conversion_temperature_C=LOOP_MASS_CONVERSION_C,
        mass_conversion_source='EnergyPlus4bd7a1f26f DataGlobalConstants.hh:591; PlantManager.cc:3560',
        endpoint_state='source-equation reconstruction, not directly measured; compare independent native storage',
        zero_flow_hx_checks=zero_flow_rows,summary_rows=len(summary),
        all_period=summary[(summary.phase=='all')&(summary.boundary=='cold_three_loops_complete')].to_dict('records'),
        plant_cooling_demand_used_to_close_balance=False)
    (destination/'LEDGER_RECEIPT.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--registration',type=Path,required=True);p.add_argument('--case',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--absolute-floor-kwh',type=float,required=True)
    p.add_argument('--tolerance-registration',type=Path)
    a=p.parse_args()
    tolerance=json.loads(a.tolerance_registration.read_text(encoding='utf-8')) if a.tolerance_registration else None
    print(json.dumps(analyze_case(a.registration,a.case,a.output,absolute_floor_kWh=a.absolute_floor_kwh,tolerance=tolerance),indent=2))


if __name__=='__main__':main()
