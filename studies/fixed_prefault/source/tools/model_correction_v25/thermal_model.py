"""Passive V25 thermal state with explicitly distinct report observations.

`replay_path` consumes archived realised substeps for offline identification.
`advance_completed` is the causal own-clock interface: only already-delivered
current-interval rate and elapsed time are accepted.  Neither enables coupling.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np
import pandas as pd

from tools.model_correctness_v23.thermal_model import _discretize
from tools.engineering_completion_v24.thermal_low_order import _kernel
from tools.model_correction_v25.thermal_observation import aggregate_completed


def kernel(parameters,dt_h):
    p = tuple(float(x) for x in parameters)
    if len(p) not in (4,6) or not np.isfinite(p).all() or min(p) <= 0 or not np.isfinite(dt_h) or dt_h <= 0:
        raise ValueError('Positive finite passive parameters/duration required')
    if len(p) == 4:
        a,b,phi,gamma = _discretize(*p,dt_h)
    else:
        a,b,phi,gamma = _kernel(p,dt_h)
    integral_state = np.linalg.solve(a,phi-np.eye(len(a)))
    integral_input = np.linalg.solve(a,gamma-dt_h*b)
    return a,b,phi,gamma,integral_state,integral_input


@dataclass(frozen=True)
class PreparedPath:
    dt_h: np.ndarray
    q_kW: np.ndarray
    bucket_code: np.ndarray
    bucket_clock: pd.DatetimeIndex
    last_index: np.ndarray


def prepare_path(frame,*,scale=1.):
    if not np.isfinite(scale) or scale <= 0 or 'net_sensible_kW' not in frame:
        raise ValueError('Finite actual net sensible rates and positive zone scale required')
    if not np.isfinite(frame.net_sensible_kW).all():
        raise ValueError('Nonfinite realized input')
    aggregate_completed(frame.assign(temperature_C=0.))
    clock = pd.to_datetime(frame.bucket_end)
    code,unique = pd.factorize(clock,sort=False)
    if len(unique) == 0 or not pd.DatetimeIndex(unique).is_monotonic_increasing or np.any(np.diff(code) < 0):
        raise ValueError('Actual completed path must be chronological')
    last = np.flatnonzero(np.r_[np.diff(code) != 0,True])
    return PreparedPath(frame.dt_h.to_numpy(float),frame.net_sensible_kW.to_numpy(float)/scale,
                        code,pd.DatetimeIndex(unique),last)


def replay_path(frame,parameters,*,scale=1.):
    """Advance each actual path and report discrete/continuous/end separately."""
    path = frame if isinstance(frame,PreparedPath) else prepare_path(frame,scale=scale)
    size = 2 if len(parameters) == 4 else 3
    state = np.zeros(size)
    ends,integrals = np.empty((len(path.dt_h),size)),np.empty(len(path.dt_h))
    kernels = {float(dt):kernel(parameters,float(dt)) for dt in np.unique(path.dt_h)}
    for i,(dt,q) in enumerate(zip(path.dt_h,path.q_kW)):
        _,_,phi,gamma,im,iq = kernels[float(dt)]
        integrals[i] = (im@state+iq*q)[0]
        state = phi@state+gamma*q
        ends[i] = state
    durations = np.bincount(path.bucket_code,weights=path.dt_h)
    mean = np.bincount(path.bucket_code,weights=ends[:,0]*path.dt_h)/durations
    continuous = np.bincount(path.bucket_code,weights=integrals)/durations
    result = pd.DataFrame(dict(mean_C=mean,endpoint_C=ends[path.last_index,0],continuous_mean_C=continuous),index=path.bucket_clock)
    for i in range(size):
        result[f'state{i}_C'] = ends[path.last_index,i]
    return result


def replay_pair(normal,actual,parameters,*,scale=1.):
    base,fault = replay_path(normal,parameters,scale=scale),replay_path(actual,parameters,scale=scale)
    if not base.index.equals(fault.index):
        raise ValueError('Paired paths have different completed zone clocks')
    return fault-base


def advance_completed(state,parameters,*,net_sensible_kW,dt_h,max_step_minutes=1.):
    """Own-clock reporting for an already completed causal input interval.

    Holds the realised interval rate constant and uses <=1min local substeps.
    This is an approximation to the *reference reporting schedule*, not an
    exact reproduction of the unknown future EnergyPlus adaptive schedule.
    """
    p = np.asarray(parameters,float)
    initial = np.asarray(state,float)
    size = 2 if len(p) == 4 else 3
    if initial.shape != (size,) or not np.isfinite(initial).all() or not np.isfinite(net_sensible_kW):
        raise ValueError('Finite current state and actual interval rate required')
    if not np.isfinite(max_step_minutes) or not 0 < max_step_minutes <= 1:
        raise ValueError('Positive declared local reporting step <=1min required')
    kernel(p,float(dt_h))
    count = max(1,int(math.ceil(dt_h*60/max_step_minutes-1e-12)))
    subdt = dt_h/count
    _,_,phi,gamma,im,iq = kernel(p,subdt)
    current,weighted,integral = initial.copy(),0.,np.zeros(size)
    for _ in range(count):
        integral += im@current+iq*net_sensible_kW
        current = phi@current+gamma*net_sensible_kW
        weighted += current[0]*subdt
    capacities = p[:size] if size == 3 else p[:2]
    passive = p[-1]*integral[-1]
    change = float(np.dot(capacities,current-initial))
    return dict(state=current,endpoint_C=float(current[0]),reported_mean_C=float(weighted/dt_h),
        continuous_mean_C=float(integral[0]/dt_h),substeps=count,
        actual_sensible_input_kWh=float(net_sensible_kW*dt_h),passive_heat_rejection_kWh=float(passive),
        thermal_energy_change_kWh=change,balance_residual_kWh=float(net_sensible_kW*dt_h-passive-change),
        approximation='Own fixed substeps and held realised interval rate; no future EnergyPlus schedule')
