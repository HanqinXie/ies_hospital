"""Independent targeted EnergyPlus checks of the unchanged 1R1C surrogate.

Derived IDFs and all runs go to an explicit new destination. A single OR airloop
cooling coil is disabled while fan schedules, other coils, zone loads, equipment,
thermostats, constructions and weather remain unchanged. The four OR zones share
this real cooling path; OR1 is the representative temperature observation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import time

import numpy as np
import pandas as pd

from hospital_ies.idf_parameters import zone_parameter_table

CASES = (
    dict(case_id='cal_summer_2h', split='calibration', month=6, day=15, outage_hours=2.),
    dict(case_id='cal_transition_4h', split='calibration', month=9, day=15, outage_hours=4.),
    dict(case_id='val_winter_2h', split='validation', month=1, day=15, outage_hours=2.),
    dict(case_id='val_summer_4h', split='validation', month=8, day=15, outage_hours=4.),
)
ZONE = 'OR1_FLR_2'
COIL = 'VAV_OR Cooling Coil'


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024*1024), b''):
            digest.update(data)
    return digest.hexdigest()


def _replace_object(text, kind, name, transform):
    count = 0
    def replace(match):
        nonlocal count
        clean = '\n'.join(line.split('!',1)[0] for line in match.group().splitlines())
        fields = [v.strip() for v in clean.rstrip().rstrip(';').split(',')]
        if fields[0].upper() != kind.upper():
            return match.group()
        if name is not None and fields[1].upper() != name.upper():
            return match.group()
        count += 1
        result = transform(fields)
        return '\n' + ',\n  '.join(result) + ';'
    output = re.sub(r'[^;]+;', replace, text)
    if count != 1:
        raise ValueError(f'Expected one source object {kind}/{name}, found {count}')
    return output


def make_intervention_idf(source_text, *, month, day, outage_hours, target=COIL):
    event = datetime(2006, int(month), int(day))
    first, last = event - timedelta(days=1), event + timedelta(days=2)
    def period(fields):
        fields[1:9] = ['SCI_SHORT_PAIRED_PERIOD', str(first.month), str(first.day), '',
            str(last.month), str(last.day), '', first.strftime('%A')]
        return fields
    result = _replace_object(source_text, 'RunPeriod', None, period)
    if outage_hours:
        def coil(fields):
            fields[2] = 'SCI_OR_COOLING_AVAILABILITY'
            return fields
        result = _replace_object(result, 'Coil:Cooling:Water', target, coil)
        end_minute = int(round((12. + float(outage_hours)) * 60.))
        if not 0 < outage_hours < 12:
            raise ValueError('Validation outage must be finite and inside the event day')
        result += ('\nSchedule:Compact,SCI_OR_COOLING_AVAILABILITY,Fraction,\n'
            f'  Through: {first.month}/{first.day},For: AllDays,Until: 24:00,1,\n'
            f'  Through: {event.month}/{event.day},For: AllDays,Until: 12:00,1,\n'
            f'  Until: {end_minute//60:02d}:{end_minute%60:02d},0,Until: 24:00,1,\n'
            '  Through: 12/31,For: AllDays,Until: 24:00,1;\n')
    for variable in ('Zone Mean Air Temperature','Zone Air System Sensible Cooling Rate',
                     'Zone Thermostat Cooling Setpoint Temperature'):
        result += f'\nOutput:Variable,{ZONE},{variable},Timestep;'
    result += f'\nOutput:Variable,{COIL},Cooling Coil Total Cooling Rate,Timestep;\n'
    return result


def predict_surrogate_delta(baseline_cooling_kW, loss_fraction, *, ua_kW_K,
                            capacity_kWh_K, step_hours):
    cooling, loss = np.asarray(baseline_cooling_kW), np.asarray(loss_fraction)
    if cooling.shape != loss.shape or ua_kW_K <= 0 or capacity_kWh_K <= 0 or step_hours <= 0:
        raise ValueError('Invalid thermal surrogate inputs')
    decay = math.exp(-ua_kW_K/capacity_kWh_K*step_hours)
    delta = 0.
    result = []
    for q, fraction in zip(cooling, loss):
        delta = decay*delta + (-math.expm1(-ua_kW_K/capacity_kWh_K*step_hours))*float(q)*float(fraction)/ua_kW_K
        result.append(delta)
    return np.array(result)


def run_energyplus(executable, weather, idf_path, destination):
    """Run one explicit case and retain stdout, actual exit, elapsed and memory."""
    destination.mkdir(parents=True, exist_ok=False)
    command = [str(executable), '-w', str(weather), '-d', str(destination), str(idf_path)]
    began = time.perf_counter()
    peak = 0
    try:
        import psutil
    except ImportError:
        psutil = None
    with (destination/'process.log').open('wb') as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        while process.poll() is None:
            if psutil is not None:
                try:
                    tracked = psutil.Process(process.pid)
                    peak = max(peak, sum(p.memory_info().rss for p in [tracked]+tracked.children(recursive=True)))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            time.sleep(.25)
    errors = (destination/'eplusout.err').read_text(encoding='utf-8',errors='replace') if (destination/'eplusout.err').is_file() else ''
    receipt = dict(command=command, exit_code=process.returncode, elapsed_seconds=time.perf_counter()-began,
        peak_process_tree_rss_bytes=peak or None, idf_sha256=_hash(idf_path),
        severe_message_markers=errors.count('** Severe **'), fatal_message_markers=errors.count('**  Fatal  **'),
        status='completed' if process.returncode == 0 else 'failed',
        sql_path=str(destination/'eplusout.sql'))
    (destination/'RUN_RECEIPT.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt


def sql_timestamp(month, day, hour, minute, dst):
    """26.2 SQL timestep Time.Hour is clock hour; schedules use local DST."""
    return datetime(2006,month,day)+timedelta(hours=hour+dst,minutes=minute)


def _series(connection, name, key):
    rows = connection.execute('SELECT t.Month,t.Day,t.Hour,t.Minute,t.Dst,x.VariableValue,d.ReportingFrequency '
        'FROM ReportVariableData x JOIN ReportVariableDataDictionary d USING(ReportVariableDataDictionaryIndex) '
        'JOIN Time t USING(TimeIndex) JOIN EnvironmentPeriods e USING(EnvironmentPeriodIndex) '
        "WHERE e.EnvironmentType=3 AND d.VariableName=? AND d.KeyValue=? AND d.ReportingFrequency IN ('Zone Timestep','Timestep') "
        'ORDER BY t.TimeIndex', (name,key)).fetchall()
    if not rows:
        available = connection.execute('SELECT DISTINCT ReportingFrequency FROM ReportVariableDataDictionary '
            'WHERE VariableName=? AND KeyValue=?',(name,key)).fetchall()
        raise ValueError(f'Timestep output missing for {name}/{key}; frequencies={available}')
    stamps = []
    values = []
    for month,day,hour,minute,dst,value,_ in rows:
        stamps.append(sql_timestamp(month,day,hour,minute,dst))
        values.append(float(value))
    if len(set(stamps)) != len(stamps):
        raise ValueError('Duplicate timestep temperature clock')
    if any((b-a).total_seconds()!=600 for a,b in zip(stamps,stamps[1:])):
        raise ValueError('Temperature timestep clock is not continuous at 10-minute resolution')
    return pd.Series(values,index=pd.DatetimeIndex(stamps))


def read_temperature(database):
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True) as connection:
        temp = _series(connection,'Zone Mean Air Temperature',ZONE)
        cooling = _series(connection,'Zone Air System Sensible Cooling Rate',ZONE)/1000.
        setpoint = _series(connection,'Zone Thermostat Cooling Setpoint Temperature',ZONE)
        coil = _series(connection,'Cooling Coil Total Cooling Rate',COIL.upper())/1000.
    return pd.DataFrame(dict(temperature_C=temp, cooling_kW=cooling,
        cooling_setpoint_C=setpoint, airloop_coil_cooling_kW=coil))


def _crossing(values, limits, stamps):
    ix = np.flatnonzero(np.asarray(values)>np.asarray(limits)+1e-7)
    return stamps[ix[0]].isoformat() if len(ix) else None


def _recovery_minutes(delta, stamps, recovery):
    values = np.asarray(delta)
    for i in range(len(stamps)-2):
        if stamps[i] > recovery and np.all(np.abs(values[i:i+3]) <= .2):
            return float((stamps[i]-recovery).total_seconds()/60.)
    return None


def summarize_pair(case, baseline, interrupted, parameter, *, label):
    if not baseline.index.equals(interrupted.index):
        raise ValueError('Independent paired clocks do not match')
    stamps = baseline.index
    step_h = (stamps[1]-stamps[0]).total_seconds()/3600.
    event = datetime(2006,case['month'],case['day'],12)
    recovery = event + timedelta(hours=case['outage_hours'])
    off = (stamps > event) & (stamps <= recovery)
    if not np.any(off) or float(interrupted.airloop_coil_cooling_kW[off].max()) > 1e-6:
        raise ValueError('Independent target cooling coil did not remain off during the registered event')
    prediction = predict_surrogate_delta(baseline.cooling_kW.to_numpy(),off.astype(float),
        ua_kW_K=parameter['assumed_UA_kW_K'],capacity_kWh_K=parameter['effective_C_kWh_K'],step_hours=step_h)
    actual_delta = interrupted.temperature_C - baseline.temperature_C
    predicted_temp = baseline.temperature_C + prediction
    evaluate = stamps > event
    error = np.asarray(predicted_temp-interrupted.temperature_C)[evaluate]
    all_error = np.asarray(predicted_temp-interrupted.temperature_C)
    first_six_recovery = (stamps > recovery) & (stamps <= recovery+timedelta(hours=6))
    limits = baseline.cooling_setpoint_C+2.
    raw = pd.DataFrame(dict(timestamp=stamps,baseline_temperature_C=baseline.temperature_C.to_numpy(),
        independent_temperature_C=interrupted.temperature_C.to_numpy(), surrogate_temperature_C=np.asarray(predicted_temp),
        independent_delta_C=actual_delta.to_numpy(),surrogate_delta_C=prediction,
        baseline_sensible_cooling_kW=baseline.cooling_kW.to_numpy(),
        independent_sensible_cooling_kW=interrupted.cooling_kW.to_numpy(),
        baseline_airloop_coil_cooling_kW=baseline.airloop_coil_cooling_kW.to_numpy(),
        independent_airloop_coil_cooling_kW=interrupted.airloop_coil_cooling_kW.to_numpy(),
        cooling_setpoint_C=baseline.cooling_setpoint_C.to_numpy(),outage_active=off))
    result = dict(case_id=case['case_id'], split=case['split'], parameter_label=label,
        event_start=event.isoformat(), event_end=recovery.isoformat(),outage_hours=case['outage_hours'],
        zone=ZONE, modeled_airloop='VAV_OR', rms_temperature_error_C=float(np.sqrt(np.mean(error**2))),
        outage_rms_temperature_error_C=float(np.sqrt(np.mean(all_error[off]**2))),
        first6h_recovery_rms_temperature_error_C=float(np.sqrt(np.mean(all_error[first_six_recovery]**2))) if np.any(first_six_recovery) else None,
        maximum_absolute_temperature_error_C=float(np.max(np.abs(error))),
        independent_peak_delta_C=float(actual_delta[evaluate].max()),
        surrogate_peak_delta_C=float(prediction[evaluate].max()),
        independent_recovery_within_0p2C_minutes=_recovery_minutes(actual_delta,stamps,recovery),
        surrogate_recovery_within_0p2C_minutes=_recovery_minutes(prediction,stamps,recovery),
        independent_first_setpoint_plus2_crossing=_crossing(interrupted.temperature_C[evaluate],limits[evaluate],stamps[evaluate]),
        surrogate_first_setpoint_plus2_crossing=_crossing(predicted_temp[evaluate],limits[evaluate],stamps[evaluate]),
        baseline_event_zone_sensible_cooling_kWh=float(baseline.cooling_kW[off].sum()*step_h),
        independent_event_zone_sensible_cooling_kWh=float(interrupted.cooling_kW[off].sum()*step_h),
        disabled_airloop_coil_max_cooling_kW=float(interrupted.airloop_coil_cooling_kW[off].max()),
        recovery_additional_zone_cooling_kWh=float(np.maximum(0.,np.asarray(interrupted.cooling_kW-baseline.cooling_kW)[stamps>recovery]).sum()*step_h),
        scope='paired DOE prototype and same TMY; research +2 C threshold, no clinical validation',
        mass_participation=parameter['mass_participation'], effective_U_W_m2K=parameter['effective_U_W_m2K'])
    return result,raw


def run_validation(root, source, executable, destination, *, pilot=False, replay_from=None):
    root,source,executable,destination=map(Path,(root,source,executable,destination))
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Refuse to overwrite previous independent validation evidence')
    destination.mkdir(parents=True,exist_ok=True)
    idf=source/'modified_idf/Hospital_SanDiego_262_engineering_audit.idf'
    original=source/'Hospital_SanDiego.idf'
    weather=source/'SanDiego_BrownField_TMY3.epw'
    sources={str(p.resolve()):_hash(p) for p in (idf,original,weather,executable)}
    version=subprocess.run([str(executable),'--version'],capture_output=True,text=True).stdout.strip()
    plan=dict(source_sha256=sources,engine_version=version,cases=list(CASES),
        intervention='Only VAV_OR water cooling coil availability; zone loads/fans/other coils unchanged',
        representative_zone=ZONE,complete_affected_zones=['OR1_FLR_2','OR2_MULT5_FLR_2','OR3_FLR_2','OR4_FLR_2'],
        clock='SQL 26.2 timestep Hour+Minute in standard clock, then +Dst to match original schedule local time',
        calibration_fit_separated=True,production_core_parameters_changed=False,
        evaluation_margin_C=2.,margin_is_clinical_standard=False)
    (destination/'PROTOCOL.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    text=idf.read_text(encoding='utf-8-sig')
    pairs=[];receipts=[]
    for case in CASES[:1] if pilot else CASES:
        paths={}
        for mode in ('baseline',) if pilot else ('baseline','interruption'):
            name=case['case_id']+'_'+mode
            if replay_from is not None:
                original_run=Path(replay_from)/name
                receipt=json.loads((original_run/'RUN_RECEIPT.json').read_text(encoding='utf-8'))
                derived=Path(replay_from)/(name+'.idf')
                if _hash(derived)!=receipt['idf_sha256']:
                    raise ValueError('Existing independent IDF hash mismatch')
                receipt=dict(receipt,reused_sql_from=str(original_run),new_energyplus_run=False)
            else:
                derived=destination/(name+'.idf')
                derived.write_text(make_intervention_idf(text,month=case['month'],day=case['day'],
                    outage_hours=0. if mode=='baseline' else case['outage_hours']),encoding='utf-8')
                receipt=run_energyplus(executable,weather,derived,destination/name)
            receipts.append(dict(case_id=case['case_id'],mode=mode,**receipt))
            (destination/'RUN_STATUS.json').write_text(json.dumps(receipts,indent=2),encoding='utf-8')
            print(json.dumps(dict(case_id=case['case_id'],mode=mode,**receipt)),flush=True)
            if receipt['exit_code']:
                raise RuntimeError('Independent EnergyPlus case failed: '+name)
            paths[mode]=read_temperature(receipt['sql_path'])
        if not pilot:pairs.append((case,paths['baseline'],paths['interruption']))
    if pilot:return receipts
    default=next(p for p in zone_parameter_table(original,.5,1.5) if p['zone_name']==ZONE)
    default.update(mass_participation=.5,effective_U_W_m2K=1.5)
    candidates=[]
    for mass in (.1,.25,.5,.75,1.):
        for u in (.5,1.,1.5,3.,5.):
            parameter=next(p for p in zone_parameter_table(original,mass,u) if p['zone_name']==ZONE)
            parameter.update(mass_participation=mass,effective_U_W_m2K=u)
            error=np.mean([summarize_pair(case,base,fault,parameter,label='candidate')[0]['rms_temperature_error_C']
                for case,base,fault in pairs if case['split']=='calibration'])
            candidates.append(dict(parameter=parameter,calibration_mean_rmse_C=float(error)))
    fitted=min(candidates,key=lambda item:item['calibration_mean_rmse_C'])
    (destination/'CALIBRATION_CANDIDATES.json').write_text(json.dumps(candidates,indent=2),encoding='utf-8')
    results=[]
    for case,base,fault in pairs:
        for label,parameter in (('unchanged_production_proxy',default),('calibration_only_fit',fitted['parameter'])):
            result,raw=summarize_pair(case,base,fault,parameter,label=label)
            results.append(result)
            raw.to_csv(destination/(case['case_id']+'_'+label+'.csv'),index=False,float_format='%.12g')
    pd.DataFrame(results).to_csv(destination/'TEMPERATURE_VALIDATION_RESULTS.csv',index=False)
    final=dict(status='completed',pairs=len(pairs),independent_source_dates=4,
        sources_unchanged=all(_hash(Path(p))==h for p,h in sources.items()),
        original_parameters=default,calibration_selected=fitted,
        production_core_parameters_changed=False,
        missing_scope=['No measured hospital temperatures','Same DOE building and TMY weather',
            'One four-zone OR airloop; no clinical air quality or pressure certification'])
    (destination/'RESULT.json').write_text(json.dumps(final,indent=2),encoding='utf-8')
    return final


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--energyplus',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--pilot',action='store_true')
    parser.add_argument('--replay-from',type=Path)
    args=parser.parse_args()
    print(json.dumps(run_validation(args.root,args.source,args.energyplus,args.destination,
        pilot=args.pilot,replay_from=args.replay_from)))


if __name__=='__main__':main()
