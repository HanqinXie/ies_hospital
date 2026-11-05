"""Completed-input absolute three-store V26 study; no EnergyPlus launcher.

The normal probe is zero-fit. The optimizer refuses an unbound/expanded finite
registration, and records its start before every actual optimizer invocation.
"""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache
from datetime import datetime,timezone
import argparse,json,time,hashlib
import numpy as np
import pandas as pd

from tools.model_correction_v26.temperature_inputs import ZONES,read_epw,completed_weather,solar_vector,incident_solar,zone_geometry
from tools.model_correction_v26.temperature_surface import advance_three_stores
from tools.model_correction_v26.temperature_air_balance import _downsample

PARAMETER_ORDER=['furniture_capacity_kWh_K','construction_capacity_kWh_K','total_air_solid_conductance_kW_K','furniture_fraction']
OBJECTIVE='absolute_N_F2_F6_and_paired_delta2_delta6'


def initial_state(snapshot,case,geometry,zone):
    mapping={(c['key'].upper(),c['variable']):label for label,c in case['channels'].items()}
    states=[]
    for bi in [1,2]:
        surfaces=[s for s in geometry['surfaces'] if s['body_index']==bi]
        weights=np.array([s['area_m2'] for s in surfaces])
        values=np.array([snapshot['surfaces'][mapping[s['name'],'Surface Inside Face Temperature']] for s in surfaces])
        states.append(float(np.dot(weights,values)/weights.sum()))
    return np.array([snapshot['zones'][zone]['pre_temperature_C'],*states])


def prepare_case(reg,case_id,zone,geometry,zone_parameters,weather):
    case=next(c for c in reg['cases'] if c['case_id']==case_id);out=Path(reg['output_root'])/case_id
    q=json.loads((out/'OBSERVATION_QUALIFICATION.json').read_text())
    if not q['output_only_qualified']:raise ValueError('Only output-preserving completed observations are accepted')
    detail=pd.read_csv(out/'API_ALIGNED_DETAIL.csv',parse_dates=['bucket_end','standard_bucket_end'])
    detail=detail[detail.zone==zone].reset_index(drop=True)
    port=pd.read_csv(out/'OBSERVED_DETAIL.csv',parse_dates=['bucket_end','standard_bucket_end'])
    if len(detail)!=len(port) or not detail.bucket_end.equals(port.bucket_end) or not np.array_equal(detail.dt_h,port.dt_h):raise ValueError('Room/channel exact clock mismatch')
    mapping={(c['key'].upper(),c['variable']):label for label,c in case['channels'].items()}
    def value(key,var):return port[mapping[key.upper(),var]].to_numpy(float)
    scale=zone_parameters['zone_scales'][zone];mult=zone_parameters['zone_multipliers'][zone]
    inputs={}
    for key,var,divisor in [('system_sensible_kW','Zone Air Heat Balance System Air Transfer Rate',1000*scale*mult),
                            ('internal_convective_kW','Zone Air Heat Balance Internal Convective Heat Gain Rate',1000*scale),
                            ('system_convective_kW','Zone Air Heat Balance System Convective Heat Gain Rate',1000*scale),
                            ('internal_radiant_kW','Zone Total Internal Radiant Heating Rate',1000*scale),
                            ('internal_visible_kW','Zone Total Internal Visible Radiation Heating Rate',1000*scale)]:
        inputs[key]=value(zone,var)/divisor
    outdoor=value('VAV_OR_OAInlet Node','System Node Temperature')
    forcing=[];ages=[]
    for i,row in port.iterrows():
        if i+1==len(port) or port.bucket_end.iloc[i+1]!=row.bucket_end:at=row.standard_bucket_end
        else:at=row.standard_bucket_end-pd.Timedelta(int(round((1/6-row.substep_end_h)*3.6e12)),unit='ns')
        w=completed_weather(weather,at);sun=solar_vector(at,**weather.attrs['location'])
        current={k:float(v[i]) for k,v in inputs.items()}
        current.update(outdoor_C=float(outdoor[i]),sky_C=w['sky_C'],ground_C=float(outdoor[i]),wind_m_s=w['wind_m_s'],
            solar_by_surface_W_m2={s['name']:incident_solar(w,s['normal'],sun) for s in geometry['surfaces'] if s['exterior']},
            weather_record_end=str(w['record_end']),completed_standard_time=str(at))
        if w['record_end']>at:raise ValueError('Future weather packet reached a current temperature input')
        forcing.append(current);ages.append(w['weather_age_h'])
    normal_id=case_id.rsplit('_',1)[0]+'_0h';nc=next(c for c in reg['cases'] if c['case_id']==normal_id)
    snapshot=json.loads((Path(reg['output_root'])/normal_id/'INITIAL_OBSERVED_STATE.json').read_text())
    code,clock=pd.factorize(detail.bucket_end,sort=False)
    observed=np.bincount(code,weights=detail.temperature_C*detail.dt_h)/np.bincount(code,weights=detail.dt_h)
    return dict(case_id=case_id,zone=zone,geometry=geometry,initial_state=initial_state(snapshot,nc,geometry,zone),
        dt_h=detail.dt_h.to_numpy(float),bucket_code=code,bucket_clock=pd.DatetimeIndex(clock),forcing=forcing,
        observed_mean_C=observed,weather_age_max_h=float(max(ages)),actual_substeps=len(detail),
        initial_history_assumption='Uniform air history at first known pre-temperature; never fitted.',
        input_source_files=[out/'API_ALIGNED_DETAIL.csv',out/'OBSERVED_DETAIL.csv',Path(reg['output_root'])/normal_id/'INITIAL_OBSERVED_STATE.json'])


def replay_case(case,parameters,structure,*,retain_details=False):
    current=case['initial_state'].copy();x=np.full(4,current[0]);ds=x.copy();last_count=1
    values=[];ledger=[];start=time.perf_counter();max_balance=0.;iterations=0
    code=case['bucket_code'];last=np.flatnonzero(np.r_[np.diff(code)!=0,True]);first=np.r_[0,last[:-1]+1]
    for start_i,end_i in zip(first,last):
        n=end_i-start_i+1;dts=case['dt_h'][start_i:end_i+1]
        if abs(dts.sum()-1/6)>1e-10 or np.ptp(dts)>1e-10:raise ValueError('Current source history requires uniform complete native ten-minute buckets')
        if n>1 and n!=last_count:ds=_downsample(x,(1/6)/dts[0])
        bucket_values=[]
        for i in range(start_i,end_i+1):
            history=x[:3] if n==1 else ds[:3]
            r=advance_three_stores(current,history,parameters=parameters,geometry=case['geometry'],forcing=case['forcing'][i],dt_h=case['dt_h'][i],structure=structure)
            current=r['state'];bucket_values.append(current[0]);max_balance=max(max_balance,abs(r['balance_residual_kWh']));iterations+=r['newton_iterations']
            if n>1:ds=np.r_[current[0],ds[:3]]
            if retain_details:ledger.append(dict(substep=i,dt_h=case['dt_h'][i],air_C=current[0],furniture_C=current[1],construction_C=current[2],**{k:v for k,v in r.items() if k not in ['state','inside_faces','outside_faces']}))
        mean=float(np.dot(bucket_values,dts)/(1/6));x=np.r_[mean,x[:3]];last_count=n
        values.append((mean,current[0],current[1],current[2]))
    out=pd.DataFrame(values,index=case['bucket_clock'],columns=['mean_C','endpoint_C','furniture_C','construction_C'])
    out.attrs.update(elapsed_seconds=time.perf_counter()-start,max_balance_residual_kWh=max_balance,
        actual_substeps=case['actual_substeps'],total_newton_iterations=iterations,initial_history_assumption=case['initial_history_assumption'])
    return (out,pd.DataFrame(ledger)) if retain_details else out


def normal_probe(args):
    destination=Path(args.destination);destination.mkdir(parents=True,exist_ok=False)
    reg=json.loads(Path(args.registration).read_text());cfg=json.loads(Path(args.parameters).read_text());fit=json.loads(Path(args.original_fit).read_text())['models']['three_state']
    source=Path(args.idf).read_text(encoding='utf-8-sig');geometry=zone_geometry(source,args.geometry_sql,cfg['zone_scales']);weather=read_epw(args.weather)
    (destination/'GEOMETRY.json').write_text(json.dumps(geometry,indent=2));rows=[]
    for season in ['summer','winter']:
        for zone in ZONES:
            case=prepare_case(reg,f'native10_cal_{season}_0h',zone,geometry[zone],cfg,weather)
            original=fit[zone];p=original[1:5]
            for structure in ['baseline','surface']:
                result,ledger=replay_case(case,p,structure,retain_details=True);observed=case['observed_mean_C'];error=result.mean_C.to_numpy()-observed
                result.assign(observed_mean_C=observed,error_C=error).to_csv(destination/f'{structure}_{season}_{zone}_TRACE.csv')
                ledger.to_csv(destination/f'{structure}_{season}_{zone}_LEDGER.csv',index=False)
                rows.append(dict(structure=structure,season=season,zone=zone,absolute_mean_rmse_C=float(np.sqrt(np.mean(error**2))),absolute_max_error_C=float(abs(error).max()),absolute_bias_C=float(error.mean()),
                    model_min_C=float(result.mean_C.min()),model_max_C=float(result.mean_C.max()),actual_min_C=float(min(observed)),actual_max_C=float(max(observed)),
                    weather_age_max_h=case['weather_age_max_h'],**result.attrs))
                print(json.dumps(rows[-1]),flush=True)
    pd.DataFrame(rows).to_csv(destination/'NORMAL_SANITY.csv',index=False)
    (destination/'RECEIPT.json').write_text(json.dumps(dict(actual_optimizer_fits=0,actual_EnergyPlus_runs=0,rows=len(rows),
        profile='Fixed frozen V25 Cf,Cc,G,f without optimizer; physical exterior R/current weather replaces the old Go proxy.',
        geometry_origin='unchanged original IDF and qualified normal SQL',
        online_qualified=False,candidate_precision_qualified=False,thermal_coupling_default=None,
        initial_bulk_assumption='uniform face-based initial bulk, fixed across2h/6h and all starts',
        weather='Latest fully completed EPW record, no future interpolation; actual current OA node drybulb, held hourly solar/IR/wind.',
        omitted='Diffuse internal radiative redistribution between grouped faces and full CTF phase; no fourth thermal inventory.'),indent=2))


def validate_fit_registration(plan):
    if plan.get('structures')!=['baseline','surface']:raise ValueError('Exactly the two registered structures are permitted')
    if plan.get('maximum_actual_optimizer_fits')!=16 or plan.get('maximum_starts_per_zone_structure')!=2:
        raise ValueError('Maximum16 calls and exactly two fixed starts per zone/structure required')
    if not 0<int(plan.get('maximum_nfev',0))<=200:raise ValueError('Registered max_nfev must not exceed200')
    if plan.get('fit_outages_h')!=[2,6] or plan.get('fit_dates')!=['07-20']:raise ValueError('The original July20 2h/6h events must be jointly fitted')
    if plan.get('parameter_order')!=PARAMETER_ORDER or plan.get('objective_name')!=OBJECTIVE:
        raise ValueError('Declared four physical parameters and absolute-plus-delta objective required')
    if set(plan.get('bounds',{}))!=set(ZONES):raise ValueError('Exactly the original four zones required')
    for bound in plan['bounds'].values():
        lower=np.asarray(bound['lower'],float);upper=np.asarray(bound['upper'],float)
        if lower.shape!=(4,) or upper.shape!=(4,) or not np.isfinite(np.r_[lower,upper]).all() or (lower>=upper).any() or min(lower[:3])<=0 or lower[3]<=0 or upper[3]>=1:
            raise ValueError('Finite positive original physical parameter bounds required')
        if len(bound['starts'])!=2:raise ValueError('Exactly two fixed starts required')
        for start in bound['starts']:
            v=np.asarray(start,float)
            if v.shape!=(4,) or not np.isfinite(v).all() or (v<lower).any() or (v>upper).any():raise ValueError('A preregistered start is outside its physical bounds')
    return 16


def fit_residuals(normal,two,six,observed_normal,observed_two,observed_six):
    n,a,b,on,oa,ob=[np.asarray(x,float) for x in [normal,two,six,observed_normal,observed_two,observed_six]]
    if any(x.ndim!=1 or x.shape!=n.shape or not np.isfinite(x).all() for x in [n,a,b,on,oa,ob]):raise ValueError('Complete equal native report arrays required')
    return np.concatenate([n-on,a-oa,b-ob,(a-n)-(oa-on),(b-n)-(ob-on)])


def _hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fit_preflight(args,plan):
    validate_fit_registration(plan)
    for name,expected_path in plan['runtime_arguments'].items():
        if Path(getattr(args,name)).resolve()!=Path(expected_path).resolve():
            raise ValueError('Actual fit argument differs from registered input role: '+name)
    for path,expected in plan['source_and_data_sha256'].items():
        if _hash(path)!=expected:raise ValueError('A registered source/data byte changed before fitting: '+path)
    reg=json.loads(Path(args.registration).read_text());cfg=json.loads(Path(args.parameters).read_text());weather=read_epw(args.weather)
    geometry=zone_geometry(Path(args.idf).read_text(encoding='utf-8-sig'),args.geometry_sql,cfg['zone_scales'])
    for zone,g in geometry.items():
        upper=plan['bounds'][zone]['upper']
        if upper[0]>g['furniture_full_capacity_kWh_K']+1e-12 or upper[1]>g['construction_full_capacity_kWh_K']+1e-12:
            raise ValueError('Fit registration exceeds an actual original per-zone material inventory')
    # Preflight only prepares current observations. No optimizer is invoked.
    prepared={(season,h,z):prepare_case(reg,f'native10_cal_{season}_{h}h',z,geometry[z],cfg,weather) for season in ['summer','winter'] for h in [0,2,6] for z in ZONES}
    return prepared,geometry


def evaluate_models(prepared,models,args,destination):
    from tools.model_correctness_v23.thermal_model import paired_metrics
    from tools.engineering_completion_v24.thermal_diagnostics import LIMITS,calibration_gate
    import math
    rows=[];normal_rows=[];gates={};trace_dir=destination/'traces';trace_dir.mkdir()
    for structure,zones in models.items():
        main=[];paired=[]
        for zone,p in zones.items():
            for season,month,day in [('summer',7,20),('winter',1,22)]:
                normal=prepared[season,0,zone];nobs=normal['observed_mean_C'];normal_native=pd.read_csv(Path(args.original_native)/f'native10_cal_{season}_0h_{zone}.csv',index_col=0,parse_dates=True)
                nmodel=replay_case(normal,p,structure) if p is not None else None
                if nmodel is not None:
                    err=nmodel.mean_C.to_numpy()-nobs
                    normal_rows.append(dict(structure=structure,season=season,zone=zone,rmse_C=float(np.sqrt(np.mean(err**2))),maximum_absolute_error_C=float(abs(err).max()),bias_C=float(err.mean())))
                for hours in [2,6]:
                    case=prepared[season,hours,zone];aobs=case['observed_mean_C'];cid=f'cal_{season}_{hours}h_{zone}'
                    frame=pd.DataFrame(dict(baseline_temperature_C=nobs,actual_temperature_C=aobs,setpoint_C=normal_native.setpoint_C.to_numpy()),index=normal['bucket_clock'])
                    definition=dict(event_start=pd.Timestamp(2006,month,day,12).isoformat(),outage_hours=hours)
                    amodel=replay_case(case,p,structure) if p is not None else None
                    for view in ['absolute_model','paired_perturbation']:
                        if amodel is None:
                            metrics={k:None for k in LIMITS};metrics.update(status='No finite parameter vector returned by either registered start')
                        else:
                            absolute=amodel.mean_C.to_numpy();relative=absolute-nmodel.mean_C.to_numpy()
                            # paired_metrics API is unchanged: this subtraction
                            # makes its modeled temperature exactly absolute.
                            pseudo_delta=absolute-nobs if view=='absolute_model' else relative
                            metrics=paired_metrics(definition,frame,pseudo_delta)
                        row=dict(case_id=cid,model=structure,view=view,season=season,zone=zone,hours=hours,maximum_actual_delta_C=float(abs(aobs-nobs).max()),**metrics)
                        row['passed']=all(row.get(k) is not None and math.isfinite(float(row[k])) and float(row[k])<=limit for k,limit in LIMITS.items())
                        rows.append(row);(main if view=='absolute_model' else paired).append(row)
                    if amodel is not None:
                        frame.assign(model_normal_absolute_C=nmodel.mean_C,model_actual_absolute_C=amodel.mean_C,model_paired_delta_C=amodel.mean_C-nmodel.mean_C,
                                     actual_delta_C=aobs-nobs).to_csv(trace_dir/f'{structure}_{cid}.csv',index_label='timestamp')
        ag=calibration_gate(main);pg=calibration_gate(paired)
        gates[structure]=dict(absolute_model_gate=ag,paired_perturbation_gate=pg,
            qualified_for_new_validation=ag['qualified_for_new_validation'] and pg['qualified_for_new_validation'],
            common_absolute_bias_reported=True)
    pd.DataFrame(rows).to_csv(destination/'ALL_METRICS.csv',index=False);pd.DataFrame(normal_rows).to_csv(destination/'ABSOLUTE_NORMAL_METRICS.csv',index=False)
    return gates


def registered_fit(args):
    from scipy.optimize import least_squares
    plan=json.loads(Path(args.fit_plan).read_text());prepared,geometry=_fit_preflight(args,plan)
    dest=Path(args.destination);dest.mkdir(parents=True,exist_ok=False);attempt_dir=dest/'attempts';attempt_dir.mkdir()
    (dest/'FIT_PLAN_AT_START.json').write_text(json.dumps(plan,indent=2));(dest/'GEOMETRY.json').write_text(json.dumps(geometry,indent=2))
    started=datetime.now(timezone.utc).isoformat();attempts=[];models={};fit_count=0
    for structure in plan['structures']:
        models[structure]={}
        for zone in ZONES:
            b=plan['bounds'][zone];group=[prepared['summer',h,zone] for h in [0,2,6]];returned=[]
            for start_index,initial in enumerate(b['starts'],1):
                fit_count+=1;fit_id=f'{structure}_{zone}_start{start_index}';calls=0;begin=time.perf_counter()
                record=dict(fit_id=fit_id,actual_fit_number=fit_count,status='RUNNING',started_utc=datetime.now(timezone.utc).isoformat(),
                    initial=initial,lower=b['lower'],upper=b['upper'],maximum_nfev=plan['maximum_nfev'],fit_cases=[c['case_id'] for c in group],
                    objective_name=OBJECTIVE,validation_used=False,initial_state_not_fitted=True)
                path=attempt_dir/(fit_id+'.json');path.write_text(json.dumps(record,indent=2))
                print(json.dumps(dict(event='FIT_STARTED',**record)),flush=True)
                def residual(parameters):
                    nonlocal calls
                    calls+=1;pred=[replay_case(c,parameters,structure).mean_C.to_numpy() for c in group]
                    result=fit_residuals(*pred,*[c['observed_mean_C'] for c in group])
                    if calls%20==0:print(json.dumps(dict(event='FIT_PROGRESS',fit_id=fit_id,residual_calls=calls,elapsed_seconds=time.perf_counter()-begin,objective_rmse_C=float(np.sqrt(np.mean(result**2))))),flush=True)
                    return result
                try:
                    fit=least_squares(residual,initial,bounds=(b['lower'],b['upper']),max_nfev=int(plan['maximum_nfev']),ftol=1e-10,xtol=1e-10,gtol=1e-10)
                    record.update(status='COMPLETED',parameters=fit.x.tolist(),optimizer_success=bool(fit.success),message=str(fit.message),
                                  nfev=int(fit.nfev),cost=float(fit.cost),optimality=float(fit.optimality))
                    returned.append(record.copy())
                except Exception as exc:record.update(status='FAILED',error=repr(exc),optimizer_success=False)
                record.update(residual_calls=calls,completed_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.perf_counter()-begin)
                path.write_text(json.dumps(record,indent=2));attempts.append(record);print(json.dumps(dict(event='FIT_TERMINAL',**record)),flush=True)
            models[structure][zone]=min(returned,key=lambda r:r['cost'])['parameters'] if returned else None
    if fit_count!=16:raise RuntimeError('Actual optimizer invocation count disagrees with the registered finite plan')
    gates=evaluate_models(prepared,models,args,dest)
    result=dict(models=models,actual_optimizer_fits=fit_count,attempts=attempts,gates=gates,started_utc=started,completed_utc=datetime.now(timezone.utc).isoformat(),
        fit_plan_sha256=_hash(args.fit_plan),source_and_data_sha256=plan['source_and_data_sha256'],
        status='All original summer absolute and paired gates passed for a structure; freeze before new dates' if any(g['qualified_for_new_validation'] for g in gates.values()) else 'Finite calibration unqualified: stop independent-date/causal qualification; retire these low-order models as standalone temperature acceptance tools.',
        independent_dates_executed=False,causal_own_clock_qualified=False,thermal_coupling_default=None)
    (dest/'FIT_RESULT.json').write_text(json.dumps(result,indent=2));print(json.dumps(dict(actual_optimizer_fits=fit_count,gates=gates,status=result['status'])),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['normal-probe','fit'])
    p.add_argument('--registration',required=True);p.add_argument('--parameters',required=True);p.add_argument('--original-fit',required=True);p.add_argument('--idf',required=True);p.add_argument('--geometry-sql',required=True);p.add_argument('--weather',required=True);p.add_argument('--destination',required=True)
    p.add_argument('--fit-plan');p.add_argument('--original-native')
    args=p.parse_args()
    if args.mode=='normal-probe':normal_probe(args)
    else:
        if not args.fit_plan or not args.original_native:p.error('Fitting requires a fixed --fit-plan and --original-native')
        registered_fit(args)

if __name__=='__main__':main()
