"""Current bridge target and transactional cold recharge hysteresis."""
from __future__ import annotations
import math

DEFAULTS=dict(preserve_bridge=True,bridge_observation_minutes=15.,cold_recharge_deadband_kWh=1.,
    cold_recharge_deadband_fraction=.02)

def bridge_target_kWh(critical_building_kW,capacity_kWh,discharge_efficiency,*,observation_minutes,start_minutes,transfer_minutes):
    values=(critical_building_kW,capacity_kWh,observation_minutes,start_minutes,transfer_minutes)
    if any(not math.isfinite(float(v)) or v<0. for v in values) or not 0.<discharge_efficiency<=1.:raise ValueError('Invalid current bridge reference')
    return min(capacity_kWh,critical_building_kW*(max(observation_minutes,start_minutes)+transfer_minutes)/60./discharge_efficiency)

class ColdRechargeState:
    def __init__(self,deadband_kWh=1.,deadband_fraction=.02):
        self.active=False;self.deadband_kWh=float(deadband_kWh);self.deadband_fraction=float(deadband_fraction)

    def allowed(self,energy,capacity):
        if capacity<=0. or energy>=capacity-1e-9:return False
        return self.active or energy<=capacity-max(self.deadband_kWh,self.deadband_fraction*capacity)

    def commit(self,before,after,capacity,*,charged_input_kWh):
        if after>=capacity-1e-9:self.active=False
        elif charged_input_kWh>1e-9 and self.allowed(before,capacity):self.active=True
