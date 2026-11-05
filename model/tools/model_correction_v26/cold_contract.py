"""Signed observation algebra from EnergyPlus 26.2.0 HVACInterfaceManager.

This computes/reconstructs reported states; it never controls the simulator.
Native lumped-capacitance temperature is an interval mean, not an endpoint.
"""
from __future__ import annotations

import math
import numpy as np


def _finite(*values):
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError('Cold ledger requires finite actual values; unknown is not zero')


def tank_step(start_C, inlet_C, mass_flow_kg_s, pump_W, mass_kg, dt_s, cp_J_kgK):
    """Exact no-common-pipe half-loop equation, with cp frozen at start_C."""
    _finite(start_C,inlet_C,mass_flow_kg_s,pump_W,mass_kg,dt_s,cp_J_kgK)
    if min(mass_kg,dt_s,cp_J_kgK)<=0 or mass_flow_kg_s<0:
        raise ValueError('Positive tank mass/cp/duration and nonnegative flow required')
    capacity=mass_kg*cp_J_kgK
    if mass_flow_kg_s>0:
        rate=mass_flow_kg_s*cp_J_kgK
        alpha=mass_flow_kg_s*dt_s/mass_kg
        factor=-math.expm1(-alpha)/alpha
        equilibrium=inlet_C+pump_W/rate
        end=equilibrium+(start_C-equilibrium)*math.exp(-alpha)
        mean=equilibrium+(start_C-equilibrium)*factor
    else:
        end=start_C+pump_W*dt_s/capacity
        mean=(start_C+end)/2
    return dict(start_C=float(start_C),endpoint_C=float(end),mean_C=float(mean),
                cp_J_kgK=float(cp_J_kgK),storage_W=capacity*(end-start_C)/dt_s,
                transport_W=mass_flow_kg_s*cp_J_kgK*(inlet_C-mean))


def reconstruct_tank(mean_C,inlet_C,mass_flow_kg_s,pump_W,mass_kg,dt_s,cp_function):
    """Invert the native mean equation, without consuming native storage rate.

    The result is explicitly reconstructed from the published algorithm, not a
    directly exposed EnergyPlus endpoint. All source terms must be observed.
    """
    _finite(mean_C,inlet_C,mass_flow_kg_s,pump_W,mass_kg,dt_s)
    if min(mass_kg,dt_s)<=0 or mass_flow_kg_s<0:
        raise ValueError('Invalid half-loop state inversion')
    start=float(mean_C)
    for _ in range(100):
        cp=float(cp_function(start))
        _finite(cp)
        if cp<=0:
            raise ValueError('Water cp must be positive')
        if mass_flow_kg_s>0:
            alpha=mass_flow_kg_s*dt_s/mass_kg
            factor=-math.expm1(-alpha)/alpha
            trial=inlet_C+(mean_C-inlet_C)/factor-pump_W/(mass_flow_kg_s*cp)*(1-factor)/factor
        else:
            trial=mean_C-pump_W*dt_s/(2*mass_kg*cp)
        if abs(trial-start)<1e-12:
            start=trial
            break
        start=trial
    else:
        raise ValueError('Source-equation endpoint inversion did not converge')
    result=tank_step(start,inlet_C,mass_flow_kg_s,pump_W,mass_kg,dt_s,float(cp_function(start)))
    if abs(result['mean_C']-mean_C)>1e-9:
        raise ValueError('Reconstructed state does not reproduce native mean')
    result['endpoint_reconstructed']=True
    return result


def hx_water_heat(supply_flow,supply_cp,supply_in,supply_out,demand_flow,demand_cp,demand_in,demand_out,*,native_heat_W=None):
    """API HX side labels: positive native Q cools the HX supply side."""
    _finite(supply_flow,supply_cp,supply_in,supply_out,demand_flow,demand_cp,demand_in,demand_out)
    if min(supply_flow,demand_flow)<0 or min(supply_cp,demand_cp)<=0:
        raise ValueError('Invalid HX flows/cp')
    if native_heat_W is not None:_finite(native_heat_W)
    supply=supply_flow*supply_cp*(supply_out-supply_in)
    demand=demand_flow*demand_cp*(demand_out-demand_in)
    return dict(supply_water_gain_W=supply,demand_water_gain_W=demand,two_side_residual_W=supply+demand,
                zero_flow_native_nonzero=(supply_flow==0 or demand_flow==0) and native_heat_W is not None and native_heat_W!=0)


def residual_summary(residual_W,dt_h,exchange_terms_W,*,absolute_floor_kWh):
    residual=np.asarray(residual_W,dtype=float)
    dt=np.asarray(dt_h,dtype=float)
    terms=np.asarray(exchange_terms_W,dtype=float)
    if (residual.ndim!=1 or residual.shape!=dt.shape or terms.ndim!=2 or len(terms)!=len(dt)
            or not np.isfinite(residual).all() or not np.isfinite(dt).all()
            or not np.isfinite(terms).all() or np.any(dt<=0) or not math.isfinite(absolute_floor_kWh) or absolute_floor_kWh<=0):
        raise ValueError('Complete actual residual/duration/exchange rows required')
    signed=float(np.dot(residual,dt)/1000)
    absolute=float(np.dot(abs(residual),dt)/1000)
    scale=max(float(np.dot(abs(terms).sum(axis=1),dt)/1000),absolute_floor_kWh)
    return dict(signed_residual_kWh=signed,absolute_residual_kWh=absolute,
                maximum_substep_residual_W=float(abs(residual).max()),
                exchange_scale_kWh=scale,relative_absolute_residual=absolute/scale,
                duration_h=float(dt.sum()),rows=len(dt))
