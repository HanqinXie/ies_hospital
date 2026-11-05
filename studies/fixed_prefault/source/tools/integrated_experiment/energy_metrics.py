"""Observed source expense at unchanged illustrative prices; no physical logic.

Existing cost_USD_step additionally contains an unused-steam objective tie-break.
This separately named metric uses actual GT/boiler fuel, grid import and diesel.
Prices remain research assumptions, not hospital invoices.
"""
from __future__ import annotations
import math
from hospital_ies.model import illustrative_tariff,KWH_PER_MMBTU


def segment_energy_fee_USD(segment,plant,hourly):
    """Integrate constant/mean-power subsegment at its original hourly tariff."""
    raw_hour=segment['hour_of_year'];hour=int(raw_hour)
    if hour!=raw_hour or not 1<=hour<=len(hourly):raise ValueError('Observed segment hour outside original hourly input')
    row=hourly.iloc[hour-1]
    if 'hour_of_year' in hourly and int(row.hour_of_year)!=hour:raise ValueError('Hourly input ordinal does not match hour_of_year')
    duration=float(segment['end_minute'])-float(segment['start_minute'])
    if not math.isfinite(duration) or duration<=0:raise ValueError('Invalid observed fee interval')
    powers=[float(segment['action'][k]) for k in ('gt_fuel_MMBtu_h','boiler_steam_lbph','grid_import_kW','diesel_kW')]
    if not all(math.isfinite(x) for x in powers):raise ValueError('Nonfinite observed source output')
    fuel,boiler,grid,diesel=powers
    rate=(fuel*plant.gas_price_usd_per_mmbtu+
        boiler*plant.steam_kWh_per_lb/plant.boiler_eff/KWH_PER_MMBTU*plant.gas_price_usd_per_mmbtu+
        grid*illustrative_tariff(row.timestamp,plant)+diesel*.30)
    return float(rate*duration/60.)


def window_energy_fee(payload,plant,hourly,start=60.,end=420.):
    """Full fee remains null for truncated windows; observed prefix is separate.

    A later error does not erase an already observed complete scoring window.
    """
    if not math.isfinite(float(start)) or not math.isfinite(float(end)) or not 0<=start<end:raise ValueError('Invalid fee scoring window')
    fee=0.;covered=0.;previous=0.
    for segment in payload['segments']:
        left=float(segment['start_minute']);right=float(segment['end_minute'])
        if abs(left-previous)>1e-8 or right<=left:raise ValueError('Fee requires contiguous actual observed subsegments')
        previous=right;overlap=max(0.,min(end,right)-max(start,left))
        if overlap:
            fee+=segment_energy_fee_USD(segment,plant,hourly)*overlap/(right-left);covered+=overlap
    observed=payload.get('summary',{}).get('observed_until_minute',previous)
    if abs(previous-observed)>1e-8:raise ValueError('Fee observed prefix differs from raw subsegments')
    full=previous>=end-1e-9 and abs(covered-(end-start))<=1e-8
    return dict(actual_energy_fee_USD=float(fee) if full else None,observed_actual_energy_fee_USD=float(fee),energy_fee_outcome_valid=bool(full))
