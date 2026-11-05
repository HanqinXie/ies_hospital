"""V26 explicit relative state, common background and room sensible port.

This is a report/field contract, not a qualified autonomous temperature model.
Measured backgrounds are allowed only in explicitly offline diagnostics.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _report(path, values, interval_minutes):
    if not np.isfinite(interval_minutes) or interval_minutes<=0:
        raise ValueError('Positive report interval required')
    if not {'bucket_end','dt_h'}<=set(path) or path.empty:
        raise ValueError('Completed bucket and actual durations required')
    v=np.asarray(values,float)
    dt=path.dt_h.to_numpy(float)
    clock=pd.DatetimeIndex(pd.to_datetime(path.bucket_end))
    if v.shape!=(len(path),) or not np.isfinite(v).all() or not np.isfinite(dt).all() or (dt<=0).any() or clock.hasnans or not clock.is_monotonic_increasing:
        raise ValueError('Finite chronological values and positive actual durations required')
    code,unique=pd.factorize(clock,sort=False)
    duration=np.bincount(code,weights=dt)
    if np.max(abs(duration-interval_minutes/60))>1e-10:
        raise ValueError('Only complete report buckets are accepted')
    return pd.Series(np.bincount(code,weights=v*dt)/duration,index=pd.DatetimeIndex(unique))


def relative_report(normal,actual,perturbation_actual_C,*,background_normal_C=None,
                    background_actual_C=None,background_source=None,online=False,
                    completed_h=None,interval_minutes=10.):
    """Report Ra(delta)+(Ra-Rn)(background), with no hidden baseline.

    `perturbation_actual_C` is the relative physical state sampled on the actual
    path. Both background arrays represent the SAME declared physical normal
    trajectory evaluated on the two report clocks. This function does not
    infer/interpolate that trajectory or interpret HVAC-only response as an
    absolute temperature. Online callers must supply a completed-time cutoff;
    accepting an array never establishes model precision or coupling readiness.
    `model_state` is provenance declared by the producer, not a proof that its
    supplier was causal; that supplier needs an independent causal-input audit.
    If elapsed_h is absent the cumulative duration is only a relative cutoff
    check; it does not establish an externally synchronized online clock.
    """
    if background_normal_C is None or background_actual_C is None or background_source is None:
        raise ValueError('Explicit common physical background and source required')
    if background_source not in {'model_state','offline_measured_reference','analytic_example'}:
        raise ValueError('Unknown background provenance')
    if online:
        if background_source!='model_state':
            raise ValueError('Offline/reference backgrounds cannot be used online')
        if completed_h is None or not np.isfinite(completed_h):
            raise ValueError('Explicit completed-time cutoff required online')
        for path in [normal,actual]:
            t=path.elapsed_h.to_numpy(float) if 'elapsed_h' in path else np.cumsum(path.dt_h.to_numpy(float))
            if not np.isfinite(t).all() or (t>completed_h+1e-12).any():
                raise ValueError('Only already completed samples may be reported online')
    bn=_report(normal,background_normal_C,interval_minutes)
    ba=_report(actual,background_actual_C,interval_minutes)
    delta=_report(actual,perturbation_actual_C,interval_minutes)
    if not bn.index.equals(ba.index) or not ba.index.equals(delta.index):
        raise ValueError('Normal and actual completed report buckets differ')
    result=pd.DataFrame(dict(perturbation_report_C=delta,
        common_background_report_difference_C=ba-bn,delta_C=delta+ba-bn))
    result.attrs.update(background_source=background_source,online_inputs_checked=bool(online),
                        establishes_temperature_qualification=False)
    return result


def room_sensible_ports(frame,*,group_multiplier,area_scale=1.):
    """Signed kW into representative air, then optional area normalization.

    For the exact V25/V26 archived report implementation System Air Transfer
    uses physical-group inlet mass. All other balance terms and H/C below are
    representative-zone quantities. This is a per-field contract, not a global
    rule for arbitrary future EnergyPlus outputs.
    """
    fields=['system_air_kW','heating_kW','cooling_kW','surface_convection_kW',
            'internal_convective_kW','interzone_kW','outdoor_transfer_kW','system_convective_kW']
    if not np.isfinite([group_multiplier,area_scale]).all() or min(group_multiplier,area_scale)<=0:
        raise ValueError('Positive finite group multiplier and area scale required')
    if not set(fields)<=set(frame) or not np.isfinite(frame[fields].to_numpy(float)).all():
        raise ValueError('Complete finite measured room sensible ports required')
    r=frame[['surface_convection_kW','internal_convective_kW','interzone_kW','outdoor_transfer_kW','system_convective_kW']].copy()/area_scale
    r['system_sensible_kW']=frame.system_air_kW/group_multiplier/area_scale
    r['delivered_h_minus_c_kW']=(frame.heating_kW-frame.cooling_kW)/area_scale
    r['sensible_into_air_kW']=r.drop(columns='delivered_h_minus_c_kW').sum(axis=1)
    return r
