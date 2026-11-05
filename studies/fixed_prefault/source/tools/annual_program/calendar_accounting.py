"""Unused calendar-registration checks; never an online controller input.

Specified main fault years require actual positive availability-loss exposure.
Zero-event literature draws remain valid N/D physical years, with their original
random-year identity retained by the caller. No event is drawn, changed, dropped
or created here, and calendar exposure never establishes a service failure.
"""
from __future__ import annotations

import copy
import math
from numbers import Real

from tools.model_closure_v22.environment import validate_events
from tools.sci_engineering.design import digest
from .data_contract import YEAR_MINUTES

SCHEMA='hospital_ies.annual_program.calendar_exposure.v1'


def calendar_exposure(events, *, horizon_minutes=YEAR_MINUTES):
    """Integrate half-open external availability, initially all ports available.

    Equal-time changes follow their original native ordered last-write behavior.
    An event at the observation endpoint has no exposure inside the interval.
    Availability below one counts as derated time; its fractional loss also has
    a separately named full-outage-equivalent integral. Neither is service ENS.
    """
    if (isinstance(horizon_minutes,bool) or not isinstance(horizon_minutes,Real)
            or not math.isfinite(horizon_minutes) or not 0.<horizon_minutes<=YEAR_MINUTES):
        raise ValueError('Finite positive observation horizon within the source year required')
    horizon=float(horizon_minutes)
    checked=validate_events(events)
    for row in checked:
        if isinstance(row['time_minute'],bool) or not isinstance(row['time_minute'],Real):
            raise ValueError('Numeric native calendar clock required')
    ports=sorted({port for row in checked for port in row['availability']})
    state={port:1. for port in ports}
    down={port:[] for port in ports};equivalent={port:[] for port in ports}
    union=[];cursor=0.;unobserved=0

    def integrate(end):
        nonlocal cursor
        duration=end-cursor
        if duration<0.:raise ValueError('Calendar observation clocks are unordered')
        active=False
        for port,availability in state.items():
            if availability<1. and duration>0.:
                active=True;down[port].append(duration)
                equivalent[port].append(duration*(1.-availability))
        if active:union.append(duration)
        cursor=end

    for row in checked:
        t=float(row['time_minute'])
        if t>=horizon:
            unobserved+=1
            continue
        integrate(t)
        state.update({port:float(value) for port,value in row['availability'].items()})
    integrate(horizon)
    by_port={port:dict(unavailable_minutes=math.fsum(down[port]),
        equivalent_full_outage_minutes=math.fsum(equivalent[port])) for port in ports}
    joint=math.fsum(union)
    return dict(schema=SCHEMA,calendar_sha256=digest(checked),horizon_minutes=horizon,
        initial_availability='all_ports_one_before_time_zero',
        interval_convention='[0,horizon); ordered equal-time last write',
        positive_fault_exposure=joint>0.,by_port=by_port,any_unavailable_minutes=joint,
        summed_asset_unavailable_minutes=math.fsum(row['unavailable_minutes'] for row in by_port.values()),
        availability_at_observation_end_left=state,unobserved_event_count=unobserved,
        post_horizon_recovery_observed=False,service_failure_inferred=False,
        random_sampling_or_calendar_modification_performed=False)


def require_specified_fault(events, *, horizon_minutes=YEAR_MINUTES):
    """Only for a specified main F/FD design, not a random-year admission gate."""
    report=calendar_exposure(events,horizon_minutes=horizon_minutes)
    if not report['positive_fault_exposure']:
        raise ValueError('Specified fault year requires positive in-horizon availability loss')
    return report


def literature_physical_route(events, *, demand_growth=False,horizon_minutes=YEAR_MINUTES):
    """Preserve a zero-event random year, without forcing a failure realization.

    The caller must bind the unchanged source schedule/profile/seed separately
    from the canonical physical case. Identical N/D computation may be reused
    only after the campaign's full state/policy/input equivalence checks.
    """
    if type(demand_growth) is not bool:raise ValueError('Explicit boolean demand-growth flag required')
    report=calendar_exposure(events,horizon_minutes=horizon_minutes)
    # Nonempty zero-exposure calendars stay on the exact F/FD event path. Even
    # an all-up/restoration event can affect the inherited response clock; zero
    # availability loss does not establish N/D state or queue equivalence.
    has_calendar=bool(events)
    return dict(mode=('FD' if demand_growth else 'F') if has_calendar else ('D' if demand_growth else 'N'),
        calendar=copy.deepcopy(events),exposure=report,zero_event_random_year=not events,
        zero_observed_fault_exposure=not report['positive_fault_exposure'],
        random_year_must_remain_in_denominator=True,random_calendar_provenance_required=True,
        redraw_performed=False,physical_prefix_reuse_automatically_authorized=False)
