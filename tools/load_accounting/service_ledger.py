"""Exact actual-request/delivery accounting, separate from unchanged classifiers."""
from __future__ import annotations
import math
import numpy as np
from tools.engineering_model.analysis import _actual_matrices

PRIORITIES=('P0','P1','P2','P3')
CARRIERS=('e','h','c')
TIME_EPS=1e-8

def service_ledger(payload,start,end):
    start,end=float(start),float(end)
    if not all(math.isfinite(v) for v in (start,end)) or start<0 or end<start:
        raise ValueError('Invalid service accounting window')
    observed=float(payload['summary']['observed_until_minute'])
    if not math.isfinite(observed) or observed<0:
        raise ValueError('Invalid observed end')
    finish=max(start,min(end,observed))
    requested=np.zeros((4,3));ens=np.zeros((4,3));cuts=np.zeros((4,3));unmet=np.zeros((4,3))
    cursor=start;first=last=None;energy={};coverage={}
    source_fields={
        'GT_electric_kWh':('gt_electric_kW','kWh_e'),
        'GRID_electric_kWh':('grid_import_kW','kWh_e'),
        'DG_electric_kWh':('diesel_kW','kWh_e'),
        'ABS_cooling_kWh':('absorption_cooling_kW','kWh_c'),
        'EC_cooling_kWh':('electric_chiller_cooling_kW','kWh_c'),
        'TOWER_rejection_kWh':('tower_rejection_kW','kWh_h'),
        'HRSG_steam_lb':('hrsg_steam_lbph','lb'),
        'BOILER_steam_lb':('boiler_steam_lbph','lb')}
    for s in payload.get('segments',[]):
        original_start=float(s['start_minute']);original_end=float(s['end_minute'])
        if not all(math.isfinite(t) for t in (original_start,original_end)) or original_end<=original_start:
            raise ValueError('Invalid recorded segment endpoints')
        a=max(start,original_start);b=min(finish,original_end)
        if b<=a:continue
        if abs(a-cursor)>TIME_EPS:
            raise ValueError('Gap or overlap in actual service intervals')
        loads,loss,disconnected,_,remaining,_=_actual_matrices(s)
        dt=(b-a)/60.
        requested+=loads*dt;ens+=loss*dt;cuts+=disconnected*dt;unmet+=remaining*dt
        if first is None:first=(s,a)
        last=(s,b);cursor=b
        action=s.get('action',{})
        for key,(field,unit) in source_fields.items():
            if action.get(field) is None:continue
            value=np.asarray(action[field],dtype=float)
            exact=(abs(a-original_start)<=TIME_EPS and abs(b-original_end)<=TIME_EPS)
            if key=='GT_electric_kWh' and 'source_start_gt_kW' in s and 'source_end_gt_kW' in s:
                left=np.asarray(s['source_start_gt_kW'],dtype=float)
                right=np.asarray(s['source_end_gt_kW'],dtype=float)
                if left.shape!=(3,) or right.shape!=(3,):raise ValueError('Invalid saved GT ramp endpoints')
                value=left+(right-left)*(((a+b)/2.-original_start)/(original_end-original_start))
                exact=True
            if not np.isfinite(value).all():raise ValueError('Nonfinite actual source value')
            energy[key]=energy.get(key,0.)+float(value.sum())*dt
            # A clipped interval mean does not identify non-GT dynamics.
            # Preserve unknown rather than interpolate rotor/steam/work data.
            coverage[key]=coverage.get(key,0.)+(b-a if exact else 0.)
    if abs(cursor-finish)>TIME_EPS:
        raise ValueError('Observed service interval has an unrecorded tail')
    values={}
    for i,p in enumerate(PRIORITIES):
        values[p]={}
        for j,c in enumerate(CARRIERS):
            values[p][c]=dict(requested_kWh=float(requested[i,j]),delivered_kWh=float(requested[i,j]-ens[i,j]),
                ENS_kWh=float(ens[i,j]),disconnected_ENS_kWh=float(cuts[i,j]),
                connected_unserved_ENS_kWh=float(unmet[i,j]))
    def budget(endpoint,before):
        if endpoint is None:return None
        segment,t=endpoint;boundary=float(segment['start_minute' if before else 'end_minute'])
        if abs(t-boundary)>TIME_EPS:return None
        state=segment.get('pre_state' if before else 'post_state') or {}
        value=state.get('diesel_energy_remaining_kWh')
        return None if value is None else float(value)
    complete=observed>=end-TIME_EPS
    source_integrals={key:dict(value=(energy[key] if abs(coverage[key]-(finish-start))<=TIME_EPS else None),
        unit=source_fields[key][1],covered_minutes=coverage[key],
        basis='saved linear GT endpoints; other sources require whole saved intervals') for key in energy}
    events=payload.get('events') or []
    exhausted=[float(e['time_minute']) for e in events if e.get('kind')=='diesel_fuel_exhausted'
        and e.get('time_minute') is not None and start<=float(e['time_minute'])<=finish]
    return dict(schema='hospital_ies.v20.actual_service_ledger.v1',window_minutes=[start,end],
        observed_finish_minute=finish,complete=complete,unknown_tail_minutes=None if complete else [finish,end],
        observed_prefix=values,all_registered=values if complete else None,
        actual_source_integrals=source_integrals,
        DG_budget_start_kWh_equivalent=budget(first,True),DG_budget_finish_kWh_equivalent=budget(last,False),
        observed_DG_exhaustion_minutes=exhausted,DG_budget_is_chemical_fuel=False,
        no_new_service_or_failure_thresholds=True,partial_DG_budget_not_interpolated=True)
