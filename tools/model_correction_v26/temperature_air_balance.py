"""Read-only air histories from EnergyPlus26.2.0-4bd7a1f26f.

The zone air solver uses BDF3 histories while its native storage REPORT uses a
first difference. These functions keep those quantities distinct. They never
claim that low-order lumped solid states reproduce EnergyPlus CTF surfaces.

Source: ZoneTempPredictorCorrector.cc in commit4bd7a1f26f, actual solver
L4008-4021, report L5689-5697, zone push L4257, system push L4354-4362,
update/reset L6847-6882. Exact source URLs/hashes are retained with the V26
temperature observation evidence; this module does not inspect simulator RAM.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class AirStorage:
    solver_storage_kW: float
    native_report_storage_kW: float
    first_difference_is_solver_storage: bool


def air_storage_rate(capacity_kWh_K,dt_h,temperature_C,history_C,*,algorithm='ThirdOrder',
                     temperature_independent_kW=None,temperature_dependent_kW_K=None,
                     native_report_capacity_kWh_K=None):
    """Compute actual algorithm storage and separate report formula.

    Capacity for the solver is its pre-corrector air coefficient times dt.
    The report evaluates rho/cp after correction, so callers with measured
    properties can pass a distinct native-report capacity. NaN histories stay
    unknown. No residual is filled with zero.
    """
    if not np.isfinite([capacity_kWh_K,dt_h,temperature_C]).all() or min(capacity_kWh_K,dt_h)<=0:
        raise ValueError('Positive finite air capacity/duration and temperature required')
    h=np.asarray(history_C,float)
    if h.ndim!=1 or len(h)<1:
        raise ValueError('Declared previous air history required')
    rc=capacity_kWh_K if native_report_capacity_kWh_K is None else native_report_capacity_kWh_K
    if not np.isfinite(rc) or rc<=0:
        raise ValueError('Positive report capacity required')
    native=float(rc/dt_h*(temperature_C-h[0]))
    if algorithm=='ThirdOrder':
        if len(h)<3:raise ValueError('Three BDF3 air histories required')
        solver=float(capacity_kWh_K/dt_h*(11/6*temperature_C-3*h[0]+1.5*h[1]-h[2]/3))
        return AirStorage(solver,native,False)
    if algorithm=='EulerMethod':
        return AirStorage(float(capacity_kWh_K/dt_h*(temperature_C-h[0])),native,True)
    if algorithm=='AnalyticalSolution':
        if temperature_independent_kW is None or temperature_dependent_kW_K is None or not np.isfinite([temperature_independent_kW,temperature_dependent_kW_K]).all():
            raise ValueError('Actual analytical temperature coefficients required')
        solver=float(temperature_independent_kW-temperature_dependent_kW_K*temperature_C)
        return AirStorage(solver,solver,False)
    raise ValueError('Unsupported actual zone air solution algorithm')


def _downsample(x,ratio):
    """Exact source DownInterpolate4HistoryValues branch conventions."""
    if abs(ratio-2)<.01:return np.array([x[0],(x[0]+x[1])/2,x[1],(x[1]+x[2])/2])
    if abs(ratio-3)<.01:return np.array([x[0],x[0]+(x[1]-x[0])/3,x[0]+2*(x[1]-x[0])/3,x[1]])
    return x[0]+np.arange(4)*(x[1]-x[0])/ratio


def reconstruct_air_histories(frame,*,interval_minutes=10.,initial_zone_history_C=None,
                              initial_system_history_C=None):
    """Attach the exact XMAT/DSXMAT histories USED by completed air substeps.

    This is an OFFLINE observer of a known completed reference path. Counts are
    not used by an online predictor. XMAT receives the duration-weighted zone
    mean; DSXMAT receives actual completed system endpoints only on shortened
    zone intervals. On a changed shortened count, DSXMAT is reinitialized by the
    official interpolation of zone history. Unknown initial values remain NaN.
    Uniform system duration within a zone bucket is an observed prerequisite
    of this configured EnergyPlus adaptive algorithm, not a global simulator
    assumption. A nonuniform new path fails explicitly and needs source review.
    """
    required={'bucket_end','dt_h','temperature_C'}
    if frame.empty or not required<=set(frame) or not np.isfinite(interval_minutes) or interval_minutes<=0:
        raise ValueError('Complete known air endpoint path required')
    f=frame.copy();f['bucket_end']=pd.to_datetime(f.bucket_end)
    if f.bucket_end.isna().any() or not f.bucket_end.is_monotonic_increasing or not np.isfinite(f[['dt_h','temperature_C']]).all().all() or (f.dt_h<=0).any():
        raise ValueError('Chronological finite air endpoints and actual durations required')
    def initial(values):
        if values is None:return np.full(4,np.nan)
        a=np.asarray(values,float)
        if a.shape!=(4,):raise ValueError('Four explicit initial histories required')
        return a.copy()
    x=initial(initial_zone_history_C);ds=initial(initial_system_history_C)
    last_count=1;result=[];zone_h=interval_minutes/60
    for bucket,g in f.groupby('bucket_end',sort=False):
        n=len(g);dt=float(g.dt_h.iloc[0])
        if abs(g.dt_h.sum()-zone_h)>1e-10 or np.ptp(g.dt_h)>1e-10:
            raise ValueError('Complete uniform EnergyPlus system substeps within each zone bucket required')
        source='zone_history'
        if n>1:
            if n!=last_count:
                ds=_downsample(x,zone_h/dt);source='zone_history_downinterpolated'
            else:source='previous_system_history'
        for k,(_,row) in enumerate(g.iterrows()):
            hist=(x if n==1 else ds).copy()
            item=row.to_dict()
            item.update(ZTM0_C=float(hist[0]),ZTM1_C=float(hist[1]),ZTM2_C=float(hist[2]),
                        history_complete=bool(np.isfinite(hist[:3]).all()),
                        history_source=source if k==0 else 'previous_system_history',
                        system_count=n,substep_ordinal=k,history_origin='source_reconstructed_offline')
            result.append(item)
            if n>1:ds=np.r_[row.temperature_C,ds[:3]]
        mean=float(np.dot(g.temperature_C,g.dt_h)/zone_h)
        x=np.r_[mean,x[:3]];last_count=n
    return pd.DataFrame(result)
