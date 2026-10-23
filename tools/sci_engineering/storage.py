"""Finite stored energy; charge input and useful discharge have explicit losses."""
from __future__ import annotations
import math

DEFAULTS=dict(capacity_kWh=0.,power_kW=0.,initial_soc=0.,charge_efficiency=.95,
              discharge_efficiency=.95,loss_per_hour=0.)

class FiniteStore:
    def __init__(self,config=None):
        if not isinstance(config or {},dict) or set(config or {})-set(DEFAULTS):
            raise ValueError('Unknown storage parameter')
        self.config=dict(DEFAULTS,**(config or {}))
        for key,value in self.config.items():
            if isinstance(value,bool) or not math.isfinite(float(value)) or value<0.:
                raise ValueError('Storage parameter must be finite and nonnegative: '+key)
            self.config[key]=float(value)
        if (self.config['initial_soc']>1. or self.config['loss_per_hour']>1.
                or any(not 0.<self.config[k]<=1. for k in ('charge_efficiency','discharge_efficiency'))):
            raise ValueError('Storage fractions/efficiency out of bounds')
        self.energy_kWh=self.config['capacity_kWh']*self.config['initial_soc']
        self.initial_charged_input_kWh=self.energy_kWh/self.config['charge_efficiency']

    def limits(self,dt_h,*,charge_allowed):
        if not math.isfinite(dt_h) or dt_h<=0.:raise ValueError('Positive storage interval required')
        c=self.config;retained=self.energy_kWh*math.exp(-c['loss_per_hour']*dt_h)
        return dict(discharge_kW=0. if charge_allowed else min(c['power_kW'],retained*c['discharge_efficiency']/dt_h),
            charge_kW=min(c['power_kW'],max(0.,c['capacity_kWh']-retained)/c['charge_efficiency']/dt_h) if charge_allowed else 0.)

    def boundary_hours(self,*,discharge_kW,charge_kW,max_hours):
        """First energy boundary under the actual held interval powers.

        The same standing-loss convention is used by commit. This locates a
        real event rather than spreading a few seconds of energy over a long
        observation interval. The caller re-solves at the returned boundary.
        """
        c=self.config
        def inventory(t):
            return (self.energy_kWh*math.exp(-c['loss_per_hour']*t)
                    +charge_kW*c['charge_efficiency']*t-discharge_kW/c['discharge_efficiency']*t)
        end=inventory(max_hours)
        depleted=discharge_kW>0. and end < -1e-10
        filled=charge_kW>0. and end > c['capacity_kWh']+1e-10
        if not depleted and not filled:return max_hours
        lo=0.;hi=max_hours
        for _ in range(60):
            mid=(lo+hi)/2.;value=inventory(mid)
            crossed=value<0. if depleted else value>c['capacity_kWh']
            if crossed:hi=mid
            else:lo=mid
        return lo

    def commit(self,*,discharge_kW,charge_kW,dt_h):
        if any(not math.isfinite(v) or v<0. for v in (discharge_kW,charge_kW,dt_h)) or dt_h<=0.:
            raise ValueError('Invalid actual storage interval')
        c=self.config
        if min(discharge_kW,charge_kW)>1e-7:raise ValueError('Simultaneous storage charge/discharge')
        if max(discharge_kW,charge_kW)>c['power_kW']+1e-7:raise ValueError('Storage power exceeded')
        before=self.energy_kWh;standing=before*(1.-math.exp(-c['loss_per_hour']*dt_h))
        charged=charge_kW*dt_h;discharged=discharge_kW*dt_h
        after=before-standing+charged*c['charge_efficiency']-discharged/c['discharge_efficiency']
        if after < -1e-7 or after > c['capacity_kWh']+1e-7:raise ValueError('Actual storage energy overdraft or overfill')
        self.energy_kWh=min(c['capacity_kWh'],max(0.,after))
        return dict(initial_kWh=before,final_kWh=self.energy_kWh,charged_input_kWh=charged,
            discharged_kWh=discharged,standing_loss_kWh=standing,
            conversion_loss_kWh=charged*(1.-c['charge_efficiency'])+discharged*(1./c['discharge_efficiency']-1.),
            balance_residual_kWh=before+charged-discharged-standing-
                (charged*(1.-c['charge_efficiency'])+discharged*(1./c['discharge_efficiency']-1.))-self.energy_kWh)

    def observe(self):
        return dict(energy_kWh=self.energy_kWh,**self.config,
            initial_charged_input_kWh=self.initial_charged_input_kWh)
