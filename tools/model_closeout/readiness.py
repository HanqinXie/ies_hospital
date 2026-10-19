"""Declared current-critical reserve; inherited readiness clocks unchanged."""
from __future__ import annotations
import math
import numpy as np
from tools.execution_recovery.readiness import (validate_context,operating_options,
    preparation_profile,phase_sources,response_envelope,diesel_support_budget)
from tools.execution_recovery.readiness import coordination_options as original_options

def coordination_options(value=None):
    supplied=dict(value or {})
    reserve=supplied.pop('critical_reserve',False)
    if type(reserve) is not bool:raise ValueError('critical_reserve must be boolean')
    return dict(original_options(supplied),critical_reserve=reserve)

validate_coordination_options=coordination_options

def critical_reserve_rule(critical_loads,*,remaining_kWh,ec_kWe_per_kWc,
                          shaft_loss_bound_kW,minimum_hours=1.):
    demand=np.asarray(critical_loads,float)
    values=[remaining_kWh,ec_kWe_per_kWc,shaft_loss_bound_kW,minimum_hours]
    if (demand.shape!=(2,3) or not np.isfinite(demand).all() or np.any(demand<0.)
        or any(isinstance(v,bool) or not math.isfinite(v) or v<0. for v in values)
        or minimum_hours<=0.):raise ValueError('Invalid observed critical reserve inputs')
    electric=float(demand[:,0].sum()+demand[:,2].sum()*ec_kWe_per_kWc)
    shaft=max(.001,electric+shaft_loss_bound_kW)
    horizon=max(minimum_hours,float(remaining_kWh)/shaft)
    return dict(reference_critical_electric_kW=electric,reference_shaft_kW=shaft,
        reserve_hours=horizon,remaining_shaft_work_kWh=float(remaining_kWh),
        minimum_hours=float(minimum_hours),actual_outage_endpoint_used=False,
        cold_reference='all existing electric-chiller conversion auxiliary; not extra demand',
        heat_reference='existing gas boiler has no separate electrical auxiliary in this model',
        bound_is_policy_preference=True,actual_power_or_energy=False)
