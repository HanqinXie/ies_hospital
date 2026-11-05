"""Current-only source-side power and bridge preferences, shared by compilers."""
from __future__ import annotations
import math

DEFAULT_CURRENT = dict(station_ac_available=True, boiler_ready=True,
    boiler_fixed_kW=.5, boiler_kW_per_kWsteam=.012, gt_start_kW=0.,
    ups_energy_kWh=0., ups_target_kWh=0., ups_discharge_efficiency=.95,
    preserve_bridge=True, recovery_cooling_request_kW=0., recovery_pump_kW_per_kW=.02,
    recovery_fan_kW_per_kW=0.,boiler_restart_kW=0.,instantaneous_diesel_current=True,
    research_stress_proxy=False,cold_energy_kWh=0.,cold_discharge_efficiency=.95,
    gt_starter_kW_per_unit=10.)

def current_context(config):
    value=(config or {}).get('_model_closure_v22')
    if value is None:return None
    if not isinstance(value,dict) or set(value)-set(DEFAULT_CURRENT):
        raise ValueError('Unknown M22 current-only physical context')
    result=dict(DEFAULT_CURRENT,**value)
    booleans={'station_ac_available','boiler_ready','preserve_bridge','instantaneous_diesel_current','research_stress_proxy'}
    for key in booleans:
        if type(result[key]) is not bool:raise ValueError('Boolean M22 state required: '+key)
    for key in set(result)-booleans:
        x=result[key]
        if isinstance(x,bool) or not math.isfinite(float(x)) or float(x)<0.:
            raise ValueError('Nonnegative finite M22 current quantity required: '+key)
        result[key]=float(x)
    if not 0.<result['ups_discharge_efficiency']<=1.:raise ValueError('Invalid UPS discharge efficiency')
    if not 0.<result['cold_discharge_efficiency']<=1.:raise ValueError('Invalid cold discharge efficiency')
    if result['recovery_cooling_request_kW']>0. and result['recovery_fan_kW_per_kW']<=0.:
        raise ValueError('Active recovery requires registered actual fan electricity')
    return result

def bridge_withdrawal_without_debt_kWh(context):
    """Existing stock above the current target; a target never creates stock."""
    return max(0.,context['ups_energy_kWh']-min(context['ups_energy_kWh'],context['ups_target_kWh']))

def boiler_auxiliary_kW(steam_lbph,on,steam_kWh_per_lb,context):
    return context['boiler_fixed_kW']*float(on)+context['boiler_kW_per_kWsteam']*steam_kWh_per_lb*steam_lbph
