"""One bounded shared V26 EnergyPlus observer, with no simulator actuation."""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

import numpy as np
import pandas as pd

from tools.model_correction_v26.cold_topology import cold_topology,idf_objects,ZONES
from tools.model_correctness_v23.thermal_data import sha256,read_native
from tools.model_correction_v25.thermal_observation import align_completed_api
from tools.engineering_completion_v24.thermal_nodes import sizing


def physics_objects(text):
    return sorted(tuple(x.strip().lower() for x in f) for f in idf_objects(text)
                  if not f[0].lower().startswith(('output:','outputcontrol:')))


def append_observers(text,channels):
    pattern=r'(?im)^\s*Output:Diagnostics\s*,[^;]*;'
    found=re.search(pattern,text)
    if found:
        original=found.group(0)
        addition=original if 'displayadvancedreportvariables' in original.lower() else original[:-1]+',DisplayAdvancedReportVariables;'
        out=text[:found.start()]+addition+text[found.end():]
    else:
        out=text+'\nOutput:Diagnostics,DisplayAdvancedReportVariables;\n'
    pairs={(c['key'],c['variable']) for c in channels.values()}
    out+='\n'+'\n'.join(f'Output:Variable,{k},{v},Detailed;' for k,v in sorted(pairs))+'\n'
    if not any(f[0].lower()=='output:variabledictionary' for f in idf_objects(text)):
        out+='Output:VariableDictionary,IDF;\n'
    if physics_objects(text)!=physics_objects(out):
        raise ValueError('Observer altered an original physical/control object')
    return out


def observation_channels(topology):
    channels={}
    def add(key,var,unit,scope='whole modeled component',native_clock='System',purpose='A cold boundary'):
        label=re.sub(r'[^a-z0-9]+','_',key.lower()).strip('_')+'__'+re.sub(r'[^a-z0-9]+','_',var.lower()).strip('_')
        record=dict(key=key,variable=var,unit=unit,native_clock=native_clock,scope=scope,purpose=purpose)
        if label in channels and channels[label]!=record:
            # A repeated port can have multiple purposes but only one native value.
            channels[label]['purpose']+='; '+purpose
        else:
            channels[label]=record
    def node(name,air=False):
        for var,unit in [('Temperature','C'),('Mass Flow Rate','kg/s'),('Enthalpy','J/kg')]+([('Humidity Ratio','kgWater/kgDryAir')] if air else []):
            add(name,'System Node '+var,unit,'native node; no extra zone multiplier')
    for loop in topology['cold_loops']:
        add(loop['name'],'Plant Supply Side Cooling Demand Rate','W','request only; excluded from energy balance')
        for side in ['Demand','Supply']:
            for suffix,unit in [('Temperature','C'),('Heat Transport Rate','W'),('Heat Storage Rate','W')]:
                add(loop['name'],f'Plant {side} Side Lumped Capacitance '+suffix,unit,
                    'whole half-loop; temperature is analytical interval mean')
        for side in loop['sides']:
            node(side['inlet']);node(side['outlet'])
    for component in topology['cold_components']:
        node(component['inlet']);node(component['outlet'])
    for chiller in topology['chillers']:
        key=chiller['name']
        variables=[('Electricity Rate','W'),('Evaporator Cooling Rate','W'),('Evaporator Mass Flow Rate','kg/s'),
                   ('Evaporator Inlet Temperature','C'),('Evaporator Outlet Temperature','C'),
                   ('Condenser Heat Transfer Rate','W'),('Condenser Mass Flow Rate','kg/s'),
                   ('Condenser Inlet Temperature','C'),('Condenser Outlet Temperature','C')]
        if key=='Heat Recovery Chiller':
            variables += [('Total Recovered Heat Rate','W'),('Heat Recovery Mass Flow Rate','kg/s'),
                          ('Heat Recovery Inlet Temperature','C'),('Heat Recovery Outlet Temperature','C')]
        for var,unit in variables:add(key,'Chiller '+var,unit)
    for c in topology['cross_boundary_components']:
        node(c['inlet']);node(c['outlet'])
    for hx in topology['heat_exchangers']:
        for var,unit in [('Heat Transfer Rate','W'),('Effectiveness','1'),('Operation Status','1')]:
            add(hx['name'],'Fluid Heat Exchanger '+var,unit,'native HX sign: positive cools API supply side')
        for side in ['Supply','Demand']:
            for var,unit in [('Mass Flow Rate','kg/s'),('Inlet Temperature','C'),('Outlet Temperature','C')]:
                add(hx['name'],f'Fluid Heat Exchanger Loop {side} Side '+var,unit)
    for pump in topology['pumps']:
        add(pump['name'],'Pump Fluid Heat Gain Rate','W')
        add(pump['name'],'Pump Electricity Rate','W')
    for coil in topology['cooling_coils']:
        for var in ['Total','Sensible']:add(coil['name'],f'Cooling Coil {var} Cooling Rate','W')
        node(coil['air_inlet'],True);node(coil['air_outlet'],True)
    for c in topology['or_air_components']:
        node(c['inlet'],True);node(c['outlet'],True)
        if c['type'].lower().startswith('coil:heating:'):add(c['name'],'Heating Coil Heating Rate','W')
    add('VAV_OR Fan','Fan Electricity Rate','W')
    add('VAV_OR Fan','Fan Heat Gain to Air','W')
    add('VAV_OR Fan','Fan Air Mass Flow Rate','kg/s')
    add('VAV_OR Humidifier','Humidifier Electricity Rate','W')
    add('VAV_OR Humidifier','Humidifier Water Volume Flow Rate','m3/s')
    for f in topology['or_aux_objects']:
        kind=f[0].lower()
        if kind=='outdoorair:mixer':
            for name in f[2:6]:node(name,True)
        elif kind in ('airloophvac:zonesplitter','airloophvac:zonemixer'):
            for name in f[2:]:node(name,True)
        elif kind=='coil:heating:water':
            add(f[1],'Heating Coil Heating Rate','W')
            for name in f[7:9]:node(name,True)
    terms=['Internal Convective Heat Gain','Surface Convection','Interzone Air Transfer','Outdoor Air Transfer',
           'System Air Transfer','System Convective Heat Gain','Air Energy Storage','Deviation']
    for zone in ZONES:
        for term in terms:add(zone,'Zone Air Heat Balance '+term+' Rate','W','raw per-field multiplier; see B contract',purpose='B source air equation')
        for var,unit in [('Zone Air Temperature','C'),('Zone Air Humidity Ratio','kgWater/kgDryAir'),
                         ('Zone Mean Radiant Temperature','C'),('Zone Air System Sensible Heating Rate','W'),
                         ('Zone Air System Sensible Cooling Rate','W')]:
            add(zone,var,unit,'representative zone except native system term',purpose='B source air equation')
        for var in ['Zone Total Internal Radiant Heating Rate','Zone Total Internal Visible Radiation Heating Rate']:
            add(zone,var,'W','representative zone',native_clock='Zone',purpose='B actual internal radiative source')
    add('Environment','Site Outdoor Air Barometric Pressure','Pa','site',purpose='B density')
    for fields in topology['or_surfaces']:
        for var,unit in [('Temperature','C'),('Convection Heat Transfer Coefficient','W/m2-K'),
                         ('Convection Heat Gain Rate','W'),('Adjacent Air Temperature','C')]:
            add(fields[1],'Surface Inside Face '+var,unit,'representative physical surface',native_clock='Zone',purpose='B enclosure feedback')
    for zone in topology['adjacent_zones']:
        add(zone,'Zone Air Temperature','C','representative neighboring zone',purpose='B adjacent boundary')
    return channels


def completed_clock(frame):
    out=frame.copy()
    starts=[];ends=[]
    for _,part in out.groupby('bucket_end',sort=False):
        dt=part.dt_h.to_numpy(float)
        if not np.isfinite(dt).all() or np.any(dt<=0) or abs(dt.sum()-1/6)>1e-10:
            raise ValueError('Incomplete/invalid original ten-minute bucket; tail remains unknown')
        if part.substep_index.tolist()!=list(range(len(part))):
            raise ValueError('Substep sequence is not complete')
        cumulative=np.cumsum(dt)
        starts.extend(np.r_[0.,cumulative[:-1]])
        ends.extend(cumulative)
    out['substep_start_h']=starts
    out['substep_end_h']=ends
    return out


def case_plan(source_root):
    root=Path(source_root)
    result=[]
    for season in ['summer','winter']:
        for duration in [0,2,6]:
            name=f'native10_cal_{season}_{duration}h'
            shared=season=='summer' and duration==0
            result.append(dict(case_id=name,normal_for_tolerance=duration==0,also_port_normal=shared,
                source_idf=root/'ports/normal/derived.idf' if shared else root/'reference'/f'{name}.idf',
                original_api_dir=root/'ports/normal' if shared else root/'reference'/name,
                expected_native_prefix=root/'source_native'/name,expected_native_kind='reference',
                month=7 if season=='summer' else 1,day=20 if season=='summer' else 22,event_hours=duration))
    for case in ['one_main_source_off','all_cold_sources_off','or_fan_path_off']:
        result.append(dict(case_id=case,normal_for_tolerance=False,also_port_normal=False,
            source_idf=root/'ports'/case/'derived.idf',original_api_dir=root/'ports'/case,
            expected_native_prefix=root/'ports'/case/'PORT_NATIVE',expected_native_kind='port',month=7,day=20,event_hours=6))
    # Normal-first order is the actual registered execution order.
    return sorted(result,key=lambda r:(not r['normal_for_tolerance'],[x['case_id'] for x in result].index(r['case_id'])))


def require_normal_gate(case,tolerance,contract_sha):
    if case['normal_for_tolerance']:
        return
    if (not tolerance or tolerance.get('channel_contract_sha256')!=contract_sha
            or not tolerance.get('two_normal_observations_qualified')
            or not tolerance.get('frozen_before_constrained_observations')):
        raise ValueError('Both normal observations and frozen interface tolerance must qualify before constrained launch')


def compare_original_ports(observed,previous,channels,old_channels):
    if len(observed)!=len(previous) or not np.array_equal(observed.dt_h.to_numpy(),previous.dt_h.to_numpy()):
        raise ValueError('Original port system clock changed')
    differences={}
    for old_label,(key,var,divisor) in old_channels.items():
        labels=[label for label,c in channels.items() if c['key'].lower()==key.lower() and c['variable']==var]
        if len(labels)!=1:raise ValueError('Original actual port is missing from new observer')
        difference=float(np.max(abs(observed[labels[0]].to_numpy()/divisor-previous[old_label].to_numpy())))
        if not np.isfinite(difference) or difference>1e-9:raise ValueError('Observer changed original actual port '+old_label)
        differences[old_label]=difference
    return differences


def prepare_registration(source_root,output_root,weather,installation):
    output_root=Path(output_root)
    output_root.mkdir(parents=True,exist_ok=True)
    target=output_root/'OBSERVATION_REGISTRATION.json'
    if target.exists():
        raise ValueError('Observation registration is immutable; use a new directory')
    plans=case_plan(source_root)
    topology=cold_topology(plans[0]['source_idf'].read_text(encoding='utf-8-sig'))
    channels=observation_channels(topology)
    canonical=json.dumps(channels,sort_keys=True,separators=(',',':')).encode()
    contract_sha=hashlib.sha256(canonical).hexdigest()
    cases=[]
    for plan in plans:
        case={k:str(v.resolve()) if isinstance(v,Path) else v for k,v in plan.items()}
        original=plan['source_idf'].read_text(encoding='utf-8-sig')
        case_channels=dict(channels)
        if any(len(f)>1 and f[1].upper()=='V25_PORT_AVAILABILITY' for f in idf_objects(original)):
            case_channels['v25_port_availability__schedule_value']=dict(key='V25_PORT_AVAILABILITY',variable='Schedule Value',unit='1',native_clock='Zone',scope='event marker only',purpose='actual schedule chronology')
        derived=append_observers(original,case_channels)
        case.update(source_idf_sha256=sha256(plan['source_idf']),observer_idf_sha256=hashlib.sha256(derived.encode('utf-8')).hexdigest(),
                    channels=case_channels,physics_objects_preserved=physics_objects(original)==physics_objects(derived))
        cases.append(case)
    reg=dict(version='V26_shared_observation_01',created_utc=datetime.now(timezone.utc).isoformat(),
        source_root=str(Path(source_root).resolve()),output_root=str(output_root.resolve()),
        weather=str(Path(weather).resolve()),weather_sha256=sha256(weather),installation=str(Path(installation).resolve()),
        executable_sha256=sha256(Path(installation)/'energyplus.exe'),api_dll_sha256=sha256(Path(installation)/'energyplusapi.dll'),
        exact_version='26.2.0-4bd7a1f26f',channel_contract_sha256=contract_sha,
        source_files={str(Path(__file__).with_name(n)):sha256(Path(__file__).with_name(n)) for n in ['cold_topology.py','cold_contract.py','cold_observer.py','cold_ledger.py']},
        topology=topology,cases=cases,physical_intervention_changes=False,
        callback='end_system_timestep_after_hvac_reporting; begin_system_timestep_before_predictor pre-state',
        time_contract='API exact dt_h is authoritative; native zone buckets only label completed original ten-minute intervals',
        inventory_contract='Native heat storage rate primary; LC temperature analytical mean, true endpoints reconstructed and flagged',
        normal_gate='Two normals only until source/precision-based tolerances are registered and immutable; no constrained balance read before that gate',
        extra_diagnostic_hypotheses=0,extra_diagnostic_runs=0)
    target.write_text(json.dumps(reg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output_root/'TOPOLOGY.json').write_text(json.dumps(topology,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return target


def collect_observed(idf,weather,destination,installation,channels):
    """V25 completed-clock convention plus explicit pre-predictor observation."""
    sys.path.insert(0,str(installation))
    from pyenergyplus.api import EnergyPlusAPI
    api=EnergyPlusAPI()
    state=api.state_manager.new_state()
    handles={};pending=[];detail=[];ports=[];zone_rows=[];failures=[];messages=[];pre={}
    pre_sequence=0;last_consumed_pre=-1
    zone_vars=dict(temperature_C='Zone Air Temperature',humidity_ratio='Zone Air Humidity Ratio',
                   heating_kW='Zone Air System Sensible Heating Rate',cooling_kW='Zone Air System Sensible Cooling Rate',
                   reported_mean_C='Zone Mean Air Temperature')
    for zone in ZONES:
        for var in zone_vars.values():api.exchange.request_variable(state,var,zone)
    for c in channels.values():api.exchange.request_variable(state,c['variable'],c['key'])
    def ready(s):
        if not api.exchange.api_data_fully_ready(s) or api.exchange.warmup_flag(s) or api.exchange.kind_of_sim(s)!=3:return False
        if not handles:
            for zone in ZONES:
                for label,var in zone_vars.items():handles[zone,label]=api.exchange.get_variable_handle(s,var,zone)
            for label,c in channels.items():handles['port',label]=api.exchange.get_variable_handle(s,c['variable'],c['key'])
            missing=[str(k) for k,h in handles.items() if h<0]
            (destination/'API_DATA_AVAILABLE.csv').write_bytes(api.exchange.list_available_api_data_csv(s))
            if missing:raise ValueError('Missing requested native handles: '+', '.join(missing))
        return True
    def capture_pre(s):
        nonlocal pre_sequence
        try:
            if ready(s):
                pre_sequence+=1
                pre.clear()
                pre.update({zone:dict(pre_temperature_C=api.exchange.get_variable_value(s,handles[zone,'temperature_C']),
                    pre_humidity_ratio=api.exchange.get_variable_value(s,handles[zone,'humidity_ratio'])) for zone in ZONES})
                if not (destination/'INITIAL_OBSERVED_STATE.json').exists():
                    snapshot=dict(month=api.exchange.month(s),day=api.exchange.day_of_month(s),api_current_time_h=api.exchange.current_time(s),
                        timing='first weather-period begin-system-before-predictor; observed initial state only',zones=pre.copy(),
                        surfaces={label:api.exchange.get_variable_value(s,handles['port',label]) for label,c in channels.items()
                                  if c['variable'].startswith('Surface Inside Face ')})
                    (destination/'INITIAL_OBSERVED_STATE.json').write_text(json.dumps(snapshot,indent=2)+'\n',encoding='utf-8')
        except Exception as exc:failures.append(repr(exc));api.runtime.stop_simulation(s)
    def system(s):
        nonlocal last_consumed_pre
        try:
            if ready(s):
                values={z:{label:api.exchange.get_variable_value(s,handles[z,label]) for label in zone_vars if label!='reported_mean_C'} for z in ZONES}
                for z in ZONES:
                    values[z].update(pre.get(z,dict(pre_temperature_C=float('nan'),pre_humidity_ratio=float('nan'))))
                    values[z].update(pre_state_callback_sequence=pre_sequence,
                        pre_state_fresh_callback=pre_sequence!=last_consumed_pre,
                        pre_state_callback='begin_system_timestep_before_predictor; zone-trial timing, not guaranteed substep pre-state')
                last_consumed_pre=pre_sequence
                pending.append((float(api.exchange.system_time_step(s)),values,
                    {label:api.exchange.get_variable_value(s,handles['port',label]) for label in channels}))
        except Exception as exc:failures.append(repr(exc));api.runtime.stop_simulation(s)
    def zone_end(s):
        try:
            if not ready(s):return
            dt_zone=float(api.exchange.zone_time_step(s))
            if not pending or abs(sum(x[0] for x in pending)-dt_zone)>1e-10:raise ValueError('Incomplete exact API system duration bucket')
            month,day=api.exchange.month(s),api.exchange.day_of_month(s)
            dst=int(api.exchange.daylight_savings_time_indicator(s))
            hour=int(api.exchange.hour(s));number=int(api.exchange.zone_time_step_number(s))
            standard=(pd.Timestamp(2006,month,day)+pd.Timedelta(hours=hour+number*dt_zone)).round('s')
            bucket=standard+pd.Timedelta(hours=dst)
            elapsed=0.
            for i,(dt,values,observations) in enumerate(pending):
                elapsed+=dt
                clock=dict(bucket_end=bucket,standard_bucket_end=standard,substep_index=i,dt_h=dt,dst=dst,
                           substep_start_h=elapsed-dt,substep_end_h=elapsed,
                           timestamp=standard-pd.Timedelta(hours=dt_zone-elapsed)+pd.Timedelta(hours=dst))
                ports.append(dict(clock,**observations))
                for z in ZONES:
                    v=values[z];v['heating_kW']/=1000;v['cooling_kW']/=1000
                    detail.append(dict(clock,zone=z,**v,net_sensible_kW=v['heating_kW']-v['cooling_kW']))
            for z in ZONES:zone_rows.append(dict(bucket_end=bucket,standard_bucket_end=standard,zone=z,
                api_reported_mean_C=api.exchange.get_variable_value(s,handles[z,'reported_mean_C']),dt_h=dt_zone,substeps=len(pending)))
            pending.clear()
        except Exception as exc:failures.append(repr(exc));api.runtime.stop_simulation(s)
    api.runtime.callback_begin_system_timestep_before_predictor(state,capture_pre)
    api.runtime.callback_end_system_timestep_after_hvac_reporting(state,system)
    api.runtime.callback_end_zone_timestep_after_zone_reporting(state,zone_end)
    api.runtime.callback_message(state,lambda m:messages.append(m.decode('utf-8',errors='replace')))
    api.runtime.set_console_output_status(state,False)
    command=['-w',str(weather),'-d',str(destination),str(idf)]
    started=datetime.now(timezone.utc).isoformat();begin=time.perf_counter()
    code=api.runtime.run_energyplus(state,command)
    api.state_manager.delete_state(state)
    (destination/'api_messages.log').write_text('\n'.join(messages),encoding='utf-8')
    pd.DataFrame(detail).to_csv(destination/'API_DETAIL.csv',index=False)
    pd.DataFrame(ports).to_csv(destination/'API_PORT.csv',index=False)
    pd.DataFrame(zone_rows).to_csv(destination/'API_TIMESTEP.csv',index=False)
    errors=(destination/'eplusout.err').read_text(encoding='utf-8',errors='replace') if (destination/'eplusout.err').exists() else ''
    receipt=dict(command=['EnergyPlusAPI',*command],started_utc=started,completed_utc=datetime.now(timezone.utc).isoformat(),
        exit_code=int(code),elapsed_seconds=time.perf_counter()-begin,callback_failures=failures,
        severe=errors.count('** Severe **'),fatal=errors.count('**  Fatal  **'),pending_unknown_system_intervals=len(pending),
        api_detail_rows=len(detail),api_port_rows=len(ports),all_handles_found=not failures,
        observation_only=True,endpoint_inventory_directly_exposed=False,
        idf_sha256=sha256(idf),executable_sha256=sha256(installation/'energyplus.exe'),api_dll_sha256=sha256(installation/'energyplusapi.dll'))
    (destination/'RUN_RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    if code or failures or pending:raise RuntimeError('Observed attempt failed; raw output and unknown tail retained')
    return receipt


def qualify_observation(case,destination,receipt):
    original=Path(case['original_api_dir'])
    old_sizing=sizing(original/'eplusout.eio');new_sizing=sizing(destination/'eplusout.eio')
    if old_sizing!=new_sizing:raise ValueError('Observation changed original equipment sizing')
    native=read_native(destination/'eplusout.sql',interval_minutes=10)
    comparisons=[];details=[]
    raw=pd.read_csv(destination/'API_DETAIL.csv',parse_dates=['bucket_end','timestamp'])
    for zone in ZONES:
        expected=pd.read_csv(str(case['expected_native_prefix'])+'_'+zone+'.csv',index_col=0,parse_dates=True)
        actual=native[zone]
        shared=[f for f in actual.columns if f in expected.columns and pd.api.types.is_numeric_dtype(actual[f])]
        if not actual.index.equals(expected.index):raise ValueError('Observer changed original native clock')
        difference=float((actual[shared]-expected[shared]).abs().to_numpy().max())
        if difference>1e-9:raise ValueError('Observer changed original native temperature/heat flow')
        d=align_completed_api(raw[raw.zone==zone].reset_index(drop=True),actual.index)
        d=completed_clock(d)
        details.append(d)
        comparisons.append(dict(zone=zone,native_fields=shared,max_abs_difference=difference))
    pd.concat(details,ignore_index=True).to_csv(destination/'API_ALIGNED_DETAIL.csv',index=False)
    port=pd.read_csv(destination/'API_PORT.csv',parse_dates=['bucket_end','timestamp'])
    aligned=align_completed_api(port.assign(temperature_C=0.),native[ZONES[0]].index).drop(columns=['temperature_C'])
    completed_clock(aligned).to_csv(destination/'OBSERVED_DETAIL.csv',index=False)
    # Verify the inherited exact API substep durations and original rates too.
    old_detail=pd.read_csv(original/'API_DETAIL.csv')
    for zone in ZONES:
        a=raw[raw.zone==zone].reset_index(drop=True);b=old_detail[old_detail.zone==zone].reset_index(drop=True)
        if len(a)!=len(b) or not np.array_equal(a.dt_h.to_numpy(),b.dt_h.to_numpy()):raise ValueError('Observer changed original exact adaptive system clock')
        if np.max(abs(a[['temperature_C','heating_kW','cooling_kW']].to_numpy()-b[['temperature_C','heating_kW','cooling_kW']].to_numpy()))>1e-9:raise ValueError('Observer changed original completed endpoint/rate')
    original_ports={}
    if (original/'API_PORT.csv').exists():
        from tools.model_correction_v25.thermal_ports import channels as legacy_channels
        original_ports=compare_original_ports(port,pd.read_csv(original/'API_PORT.csv'),case['channels'],legacy_channels())
    result=dict(case_id=case['case_id'],output_only_qualified=True,identical_sizing_fields=len(new_sizing),
                native_comparisons=comparisons,exact_api_clock_preserved=True,original_endpoint_and_rates_preserved=True,
                normal_for_tolerance=case['normal_for_tolerance'],also_port_normal=case['also_port_normal'],
                all_requested_api_handles_resolved=receipt['all_handles_found'])
    result['original_port_maximum_absolute_differences']=original_ports
    (destination/'OBSERVATION_QUALIFICATION.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='mode',required=True)
    prep=sub.add_parser('prepare')
    prep.add_argument('--source-root',type=Path,required=True);prep.add_argument('--output-root',type=Path,required=True)
    prep.add_argument('--weather',type=Path,required=True);prep.add_argument('--installation',type=Path,default=Path('C:/EnergyPlusV26-2-0'))
    run=sub.add_parser('run')
    run.add_argument('--registration',type=Path,required=True);run.add_argument('--case',required=True)
    run.add_argument('--tolerance-registration',type=Path)
    a=p.parse_args()
    if a.mode=='prepare':
        print(prepare_registration(a.source_root,a.output_root,a.weather,a.installation));return
    reg=json.loads(a.registration.read_text(encoding='utf-8'))
    case=next(c for c in reg['cases'] if c['case_id']==a.case)
    tolerance=json.loads(a.tolerance_registration.read_text(encoding='utf-8')) if a.tolerance_registration else None
    require_normal_gate(case,tolerance,reg['channel_contract_sha256'])
    for path,expected in reg['source_files'].items():
        if sha256(path)!=expected:raise ValueError('Registered observer scientific source changed')
    if sha256(case['source_idf'])!=case['source_idf_sha256'] or sha256(reg['weather'])!=reg['weather_sha256']:raise ValueError('Read-only input binding changed')
    installation=Path(reg['installation'])
    if sha256(installation/'energyplus.exe')!=reg['executable_sha256'] or sha256(installation/'energyplusapi.dll')!=reg['api_dll_sha256']:raise ValueError('Registered installed engine changed')
    target=Path(reg['output_root'])/case['case_id'];target.mkdir(parents=True,exist_ok=False)
    temp=Path(reg['output_root'])/'temp';temp.mkdir(exist_ok=True)
    os.environ.update(TEMP=str(temp),TMP=str(temp))
    idf=target/'observer.idf'
    idf.write_text(append_observers(Path(case['source_idf']).read_text(encoding='utf-8-sig'),case['channels']),encoding='utf-8',newline='\n')
    if sha256(idf)!=case['observer_idf_sha256']:raise ValueError('Derived observer IDF differs from preregistration')
    receipt=collect_observed(idf,Path(reg['weather']),target,installation,case['channels'])
    print(json.dumps(qualify_observation(case,target,receipt),indent=2))


if __name__=='__main__':main()
