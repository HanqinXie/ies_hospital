"""Finite research station qualifications, advanced only by actual execution."""
from __future__ import annotations
import math

DEFAULTS=dict(boiler_fixed_kW=.5,boiler_kW_per_kWsteam=.012,boiler_restart_minutes=1.,
    gt_starter_kW_per_unit=10.,gt_control_dc_available=True,dg_start_dc_available=True,
    control_autonomy_minutes=1440.,dg_qualified_starts=2)

def validate_config(value=None):
    if not isinstance(value or {},dict) or set(value or {})-set(DEFAULTS):raise ValueError('Unknown M22 station parameter')
    result=dict(DEFAULTS,**(value or {}))
    for key in ('gt_control_dc_available','dg_start_dc_available'):
        if type(result[key]) is not bool:raise ValueError('Station qualification must be boolean')
    for key in set(result)-{'gt_control_dc_available','dg_start_dc_available'}:
        v=result[key]
        if isinstance(v,bool) or not math.isfinite(float(v)) or float(v)<0.:raise ValueError('Invalid station parameter: '+key)
        result[key]=float(v)
    if not result['dg_qualified_starts'].is_integer():raise ValueError('Dedicated DG starts must be an integer')
    result['dg_qualified_starts']=int(result['dg_qualified_starts'])
    return result

class StationState:
    def __init__(self,config=None,*,warm_boiler=False):
        self.config=validate_config(config);self.boiler_ready=bool(warm_boiler)
        self.boiler_powered_minutes=0.;self.diesel_starts_used=0

    def control_available(self,minute,hardware=True):
        return bool(hardware and minute < self.config['control_autonomy_minutes'])

    def diesel_start_available(self,minute,hardware=True):
        return bool(self.config['dg_start_dc_available'] and self.control_available(minute,hardware)
            and self.diesel_starts_used<self.config['dg_qualified_starts'])

    def claim_diesel_start(self,minute,hardware=True):
        if not self.diesel_start_available(minute,hardware):return False
        self.diesel_starts_used+=1;return True

    def commit(self,minutes,*,boiler_requested,boiler_restart_supplied,station_live):
        if not math.isfinite(minutes) or minutes<=0.:raise ValueError('Positive actual station interval required')
        if not station_live or not boiler_requested:
            self.boiler_ready=False;self.boiler_powered_minutes=0.
        elif not self.boiler_ready:
            self.boiler_powered_minutes=(self.boiler_powered_minutes+minutes if boiler_restart_supplied else 0.)
            self.boiler_ready=self.boiler_powered_minutes>=self.config['boiler_restart_minutes']-1e-9
        return self.observe()

    def observe(self):
        return dict(boiler_ready=self.boiler_ready,boiler_continuous_power_minutes=self.boiler_powered_minutes,
            diesel_starts_used=self.diesel_starts_used,diesel_starts_remaining=self.config['dg_qualified_starts']-self.diesel_starts_used,
            control_autonomy_minutes=self.config['control_autonomy_minutes'],qualification_is_research_assumption=True)
