"""Pure, causal HRSG heat-ready gate for an explicit M15 sensitivity.

Zero delay preserves the original static availability exactly. A positive delay
withholds a newly running GT's HRSG until actual last_on + delay. It is a
research availability assumption, not a thermal energy state, OEM startup time,
physical upper bound, repair model or permission to reset actual GT history.
The owner supplies the existing GT clock and splits execution at returned
deadlines. This module never imports an environment or mutates a clock.
"""
from __future__ import annotations

import copy
import math
from numbers import Real

EPS=1e-9


def _number(value,name,*,nonnegative=True):
    if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(float(value)):
        raise ValueError('Finite actual HRSG readiness value required: '+name)
    result=float(value)
    if nonnegative and result<0.:
        raise ValueError('Nonnegative HRSG readiness value required: '+name)
    return result


def validate_hrsg_thermal_config(value=None):
    """Return a new explicit config; fifteen minutes is a study choice only."""
    if value is not None and (not isinstance(value,dict) or set(value)-{'warmup_delay_minutes'}):
        raise ValueError('Unknown HRSG thermal-readiness configuration')
    return dict(warmup_delay_minutes=_number((value or {}).get('warmup_delay_minutes',0.),'warmup_delay_minutes'))


def _clocks(now,gt_running,last_actual_on_minute):
    if gt_running is None and last_actual_on_minute is None:return None
    if gt_running is None or last_actual_on_minute is None:
        raise ValueError('Running flags and actual last-on history must be supplied together')
    if len(gt_running)!=3 or len(last_actual_on_minute)!=3 or any(type(v) is not bool for v in gt_running):
        raise ValueError('Three actual boolean running flags and last-on values required')
    running=list(gt_running)
    on=[_number(v,'last_actual_on_minute',nonnegative=False) for v in last_actual_on_minute]
    if any(v>now+EPS for v in on):raise ValueError('Actual last-on history cannot be in the future')
    return running,on


def hrsg_thermal_readiness(availability,*,time_minute,gt_running,last_actual_on_minute,config=None):
    """Return raw/effective availability and a per-HRSG causal explanation.

    Feed ``clock.running.tolist()`` and ``clock.last_on.tolist()``. Existing
    negative initial last_on values are retained; initially online sources are
    warm only under that original explicit age assumption. Both clock fields
    may be None during owner bootstrap: raw initialization is retained and
    readiness is explicitly unknown, without fabricating actual ages.
    """
    now=_number(time_minute,'time_minute');cfg=validate_hrsg_thermal_config(config)
    delay=cfg['warmup_delay_minutes'];clock=_clocks(now,gt_running,last_actual_on_minute)
    if not isinstance(availability,dict):raise ValueError('Actual hardware availability mapping required')
    raw=copy.deepcopy(availability);effective=copy.deepcopy(availability);devices=[]
    for i in range(3):
        gt=f'GT_{i+1}';hrsg=f'HRSG_{i+1}'
        if gt not in raw or hrsg not in raw:raise ValueError('Missing actual GT/HRSG hardware availability')
        gt_av=_number(raw[gt],gt);hrsg_av=_number(raw[hrsg],hrsg)
        if gt_av>1. or hrsg_av>1.:raise ValueError('Availability fractions must not exceed one')
        running=clock[0][i] if clock is not None else None
        last_on=clock[1][i] if clock is not None else None
        deadline=last_on+delay if running else None
        ready=(bool(running and gt_av>0. and now+EPS>=deadline) if clock is not None else None)
        if delay==0.:
            reason='static_zero_delay';applied=False
        elif clock is None:
            reason='bootstrap_actual_clock_unavailable';applied=False
        else:
            applied=True;effective[hrsg]=hrsg_av if ready else 0.
            reason=('GT_not_running' if not running else 'GT_hardware_unavailable' if gt_av<=0. else
                    'waiting_actual_GT_heat_delay' if not ready else 'HRSG_hardware_unavailable' if hrsg_av<=0. else 'heat_ready')
        devices.append(dict(device=hrsg,source_GT=gt,GT_running=running,last_actual_on_minute=last_on,
            heat_ready_minute=deadline,thermal_ready=ready,hardware_availability=hrsg_av,
            effective_availability=effective[hrsg],gate_applied=applied,reason=reason))
    return dict(schema='hospital_ies.hrsg_thermal_readiness.v15.v1',observation_time_minute=now,
        config=cfg,clock_history_available=clock is not None,hardware_availability=raw,
        effective_availability=effective,devices=devices,
        timing_basis='Existing actual GT running/last_on only; no pending command or exogenous recovery schedule',
        validity='Research heat-availability delay only; no calibrated HRSG thermal state or OEM/physical upper-bound claim')


def next_hrsg_ready_minute(*,time_minute,gt_running,last_actual_on_minute,config=None):
    """Return the next strictly future actual running-GT heat deadline or None.

    No future command, fault, repair or candidate start is inspected. A physical
    stop removes its pending heat deadline; a real later start updates the
    owner's existing clock and therefore creates its own new deadline.
    """
    now=_number(time_minute,'time_minute');delay=validate_hrsg_thermal_config(config)['warmup_delay_minutes']
    clock=_clocks(now,gt_running,last_actual_on_minute)
    if delay==0. or clock is None:return None
    future=[on+delay for running,on in zip(*clock) if running and on+delay>now+EPS]
    return min(future) if future else None
