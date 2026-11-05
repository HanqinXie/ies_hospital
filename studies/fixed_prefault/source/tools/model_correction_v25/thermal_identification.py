"""Finite V25 re-identification after actual output mapping is demonstrated."""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import time

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from tools.model_correctness_v23.thermal_data import ZONES,sha256
from tools.model_correctness_v23.thermal_model import paired_metrics
from tools.engineering_completion_v24.thermal_diagnostics import LIMITS,calibration_gate
from tools.model_correction_v25.thermal_model import prepare_path,replay_path,replay_pair,advance_completed,kernel


def require_mapping_gate(source):
    gate = json.loads((Path(source)/'OUTPUT_MAPPING_GATE.json').read_text(encoding='utf-8'))
    if not gate.get('mapping_gate_passed') or gate.get('actual_EnergyPlus_runs') != 6 or not math.isfinite(gate.get('mapping_max_abs_C',math.inf)) or gate['mapping_max_abs_C'] > 1e-9:
        raise ValueError('Six actual original-case output mappings must pass before fitting')
    return gate


def load_cases(reference,original,parameters):
    cases=[]
    for season,month,day in [('summer',7,20),('winter',1,22)]:
        baseid=f'native10_cal_{season}_0h'
        base=pd.read_csv(reference/baseid/'API_ALIGNED_DETAIL.csv',parse_dates=['bucket_end'])
        for hours in [2,6]:
            actualid=f'native10_cal_{season}_{hours}h'
            actual=pd.read_csv(reference/actualid/'API_ALIGNED_DETAIL.csv',parse_dates=['bucket_end'])
            for zone in ZONES:
                normal_native=pd.read_csv(original/f'{baseid}_{zone}.csv',index_col=0,parse_dates=True)
                actual_native=pd.read_csv(original/f'{actualid}_{zone}.csv',index_col=0,parse_dates=True)
                if not normal_native.index.equals(actual_native.index):
                    raise ValueError('Original paired clocks differ')
                frame=pd.DataFrame(dict(baseline_temperature_C=normal_native.temperature_C,
                    actual_temperature_C=actual_native.temperature_C,setpoint_C=normal_native.setpoint_C))
                scale=parameters['zone_scales'][zone]
                normal_path=base[base.zone==zone].copy()
                actual_path=actual[actual.zone==zone].copy()
                cases.append(dict(case_id=f'cal_{season}_{hours}h_{zone}',split='calibration',
                    season=season,zone=zone,event_start=pd.Timestamp(2006,month,day,12).isoformat(),
                    outage_hours=hours,scale=scale,frame=frame,
                    normal=prepare_path(normal_path,scale=scale),actual=prepare_path(actual_path,scale=scale),
                    normal_native=normal_native,actual_native=actual_native,
                    actual_delta_C=(actual_native.temperature_C-normal_native.temperature_C).to_numpy()))
    return cases


def fit_models(cases,registration,destination):
    attempts,models=[],{}
    destination.mkdir(parents=True,exist_ok=False)
    if registration['maximum_actual_optimizer_fits'] != 16 or registration['maximum_starts_per_zone_structure'] != 2 or registration['maximum_nfev'] != 200:
        raise ValueError('The preregistered finite budget changed')
    for structure,definition in registration['structures'].items():
        models[structure]={}
        for zone in ZONES:
            group=[c for c in cases if c['zone']==zone and c['season']=='summer']
            if len(group)!=2:
                raise ValueError('Exactly the original two strong summer calibration events are required')
            bounds=definition['bounds'][zone]
            if len(bounds['starts'])!=2:
                raise ValueError('Exactly two preregistered starts required')
            selected=[]
            for index,initial in enumerate(bounds['starts']):
                fit_id=f'{structure}_{zone}_start{index+1}'
                started=datetime.now(timezone.utc).isoformat()
                begin=time.perf_counter()
                calls=0
                def unpack(free):
                    return [bounds['fixed_air_capacity_kWh_K'],*free] if structure=='three_state' else list(free)
                def residual(free):
                    nonlocal calls
                    calls+=1
                    p=unpack(free)
                    return np.concatenate([replay_pair(c['normal'],c['actual'],p).mean_C.to_numpy()-c['actual_delta_C'] for c in group])
                record=dict(fit_id=fit_id,structure=structure,zone=zone,initial=initial,
                    lower=bounds['lower'],upper=bounds['upper'],started_utc=started,
                    fit_case_ids=[c['case_id'] for c in group],validation_used=False)
                try:
                    fit=least_squares(residual,initial,bounds=(bounds['lower'],bounds['upper']),max_nfev=200,
                        ftol=1e-10,xtol=1e-10,gtol=1e-10)
                    record.update(parameters=unpack(fit.x),success=bool(fit.success),message=fit.message,
                        cost=float(fit.cost),nfev=int(fit.nfev),residual_calls=calls,optimality=float(fit.optimality))
                    selected.append(record)
                except Exception as exc:
                    record.update(success=False,error=repr(exc),residual_calls=calls)
                record.update(completed_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.perf_counter()-begin)
                (destination/(fit_id+'.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
                attempts.append(record)
                print(json.dumps(record),flush=True)
            if not selected:
                raise ValueError(f'Both registered starts failed numerically for {structure}/{zone}')
            models[structure][zone]=min(selected,key=lambda r:r['cost'])['parameters']
    if len(attempts)!=16:
        raise ValueError('Actual fit count disagrees with preregistration')
    return models,attempts


def online_native_path(native,parameters,scale):
    state=np.zeros(2 if len(parameters)==4 else 3)
    values=[]
    for q in (native.heating_kW-native.cooling_kW).to_numpy()/scale:
        out=advance_completed(state,parameters,net_sensible_kW=float(q),dt_h=1/6,max_step_minutes=1.)
        state=out['state']
        values.append((out['reported_mean_C'],out['endpoint_C'],out['continuous_mean_C']))
    return pd.DataFrame(values,index=native.index,columns=['mean_C','endpoint_C','continuous_mean_C'])


def evaluate(cases,models,destination):
    rows,approx=[],[]
    traces=destination/'traces'
    traces.mkdir()
    gates={}
    for structure,zone_parameters in models.items():
        mainrows=[]
        for case in cases:
            pars=zone_parameters[case['zone']]
            offline=replay_pair(case['normal'],case['actual'],pars)
            frame=case['frame']
            if not offline.index.equals(frame.index):
                raise ValueError('Model/reference zone output clocks differ')
            online=online_native_path(case['actual_native'],pars,case['scale'])-online_native_path(case['normal_native'],pars,case['scale'])
            for observation,column in [('actual_substep_mean','mean_C'),('actual_substep_endpoint','endpoint_C'),('continuous_analytic_diagnostic','continuous_mean_C'),('online_own1min_from_completed10min_rates','online')]:
                predicted=online.mean_C.to_numpy() if column=='online' else offline[column].to_numpy()
                metrics=paired_metrics(case,frame,predicted)
                row=dict(case_id=case['case_id'],season=case['season'],zone=case['zone'],model=structure,
                    observation=observation,maximum_actual_delta_C=float(abs(case['actual_delta_C']).max()),**metrics)
                row['passed']=all(row.get(k) is not None and np.isfinite(row[k]) and row[k]<=limit for k,limit in LIMITS.items())
                rows.append(row)
                if observation=='actual_substep_mean':
                    mainrows.append(row)
            difference=online.mean_C-offline.mean_C
            continuous_difference=offline.continuous_mean_C-offline.mean_C
            approx.append(dict(model=structure,case_id=case['case_id'],season=case['season'],zone=case['zone'],
                own1min_native_input_vs_actual_discrete_max_C=float(difference.abs().max()),
                own1min_native_input_vs_actual_discrete_rmse_C=float(np.sqrt(np.mean(difference**2))),
                continuous_vs_actual_discrete_max_C=float(continuous_difference.abs().max())))
            trace=frame.assign(actual_delta_C=case['actual_delta_C'],model_discrete_mean_delta_C=offline.mean_C,
                model_endpoint_delta_C=offline.endpoint_C,continuous_diagnostic_delta_C=offline.continuous_mean_C,
                online_own1min_mean_delta_C=online.mean_C)
            trace.to_csv(traces/f'{structure}_{case["case_id"]}.csv',index_label='timestamp')
        gates[structure]=calibration_gate(mainrows)
    pd.DataFrame(rows).to_csv(destination/'ALL_CALIBRATION_METRICS.csv',index=False)
    pd.DataFrame(approx).to_csv(destination/'ONLINE_REPORT_APPROXIMATION.csv',index=False)
    return gates


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--original',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--registration',type=Path,required=True)
    a=p.parse_args()
    require_mapping_gate(a.source)
    a.destination.mkdir(parents=True,exist_ok=False)
    registration=json.loads(a.registration.read_text(encoding='utf-8'))
    config=json.loads(Path('configs/model_correctness_v23/temperature_parameters.json').read_text(encoding='utf-8'))
    source_paths=[Path(__file__),Path('tools/model_correction_v25/thermal_model.py'),a.registration,a.source/'OUTPUT_MAPPING_GATE.json']
    source_paths += sorted((a.source/'reference').glob('*/API_ALIGNED_DETAIL.csv'))
    source_paths += sorted(a.original.glob('native10_cal_*_OR*.csv'))
    source_hashes={str(p):sha256(p) for p in source_paths}
    (a.destination/'SOURCE_AT_FIT.json').write_text(json.dumps(source_hashes,indent=2),encoding='utf-8')
    cases=load_cases(a.source/'reference',a.original,config)
    models,attempts=fit_models(cases,registration,a.destination/'attempts')
    gates=evaluate(cases,models,a.destination)
    time_constants={structure:{z:sorted((-1/np.linalg.eigvals(kernel(p,1/6)[0])).tolist()) for z,p in group.items()} for structure,group in models.items()}
    result=dict(models=models,actual_optimizer_fits=len(attempts),calibration_gates=gates,
        frozen_for_independent_validation=False,time_constants_h=time_constants,
        thermal_coupling_default=None,source_sha256=source_hashes,
        status='Qualified calibration requires explicit freeze and two new summer dates' if any(g['qualified_for_new_validation'] for g in gates.values()) else 'Unqualified: no new-date precision validation; complete bounded thermal-port reference checks',
        offline_reference_schedule_only=True,online_reporting_approximation_separate=True,
        completed_utc=datetime.now(timezone.utc).isoformat())
    (a.destination/'FIT_RESULT.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    if {str(p):sha256(p) for p in source_paths}!=source_hashes:
        raise ValueError('Fit source changed during attempt')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
