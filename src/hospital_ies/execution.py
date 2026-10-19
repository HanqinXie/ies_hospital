"""Explicit P1B actuator contracts. Defaults retain P1A strict source holding.

The opt-in no-export interlock is an algebraic research protection boundary,
not a calibrated transient relay or a dispatch optimizer.
"""
import copy
import numpy as np
from scipy.optimize import linprog
from .model import RT_TO_KW, gt_level_properties

class ModelConstraintInfeasible(RuntimeError):
    category = 'model_constraint_infeasible'

class SolverExecutionError(RuntimeError):
    category = 'solver_exception'

DEFAULT = dict(name='strict_hold', provenance='P1A compatibility',
    grid_import_mode='fixed', gt_local_down_rate_kW_per_hour=0.,
    gt_local_down_delay_minutes=0., local_max_segment_minutes=.25,
    surplus_interlock='none', surplus_interlock_delay_minutes=0.,
    automatic_shedding_delay_minutes=0., gt_start_delay_minutes=0.,
    diesel_mode='legacy_explicit_permission', ats_transfer_minutes=0.,
    diesel_allowed_priorities=['P0','P1'], thermal_mode='legacy_loss_fraction')

def execution_contract(value=None):
    c=copy.deepcopy(DEFAULT)
    if value is not None:
        if set(value)-set(c):raise ValueError('Unknown execution contract fields')
        c.update(copy.deepcopy(value))
    for key,allowed in dict(grid_import_mode=('fixed','arrived_ceiling'),
            surplus_interlock=('none','isolate_largest_gt'),
            diesel_mode=('legacy_explicit_permission','emergency_bus'),
            thermal_mode=('legacy_loss_fraction','attributed')).items():
        if c[key] not in allowed:raise ValueError('Invalid '+key)
    for k,v in c.items():
        if k.endswith('_minutes') or k.endswith('_per_hour'):
            if not np.isfinite(v) or v<0:raise ValueError('Invalid '+k)
    if c['local_max_segment_minutes']<=0:raise ValueError('Local segment must be positive')
    if set(c['diesel_allowed_priorities'])-{'P0','P1','P2','P3'}:raise ValueError('Invalid diesel priorities')
    return c

def limited_down_endpoint(current,target,minimum,rate,minutes):
    current=np.asarray(current,float);target=np.asarray(target,float)
    floor=np.minimum(current,np.where(current>1e-9,minimum,0.))
    return np.minimum(current,np.maximum(np.maximum(target,floor),current-rate*minutes/60))

def maximum_auxiliary_sink(plant,row,loads,f,action):
    """Upper bound from REAL held converters, cold demand, steam and towers.

    A diagnostic LP only. Its value is never entered in the energy balance.
    """
    _,heat,_=gt_level_properties(plant,float(row.outdoor_temperature_C))
    rated=float(gt_level_properties(plant,float(row.outdoor_temperature_C))[0][-1])
    steam_per_kW=float(heat[-1])/rated
    steam=sum(action['gt_electric_kW'][i]*steam_per_kW*f[f'HRSG_{i+1}'] for i in range(3))+action['boiler_steam_lbph']
    bounds=[(0,min(action['absorption_cooling_kW']/RT_TO_KW,plant.absorption_rt*f['ABS']))]
    bounds += [(0,min(action['electric_chiller_cooling_kW'][i]/RT_TO_KW,plant.electric_chillers_rt[i]*f[f'EC_{i+1}'])) for i in range(3)]
    aux=np.array([plant.abs_electric_kW_per_rt]+[plant.ec_electric_kW_per_rt]*3)
    tower=np.array([RT_TO_KW+plant.abs_steam_lb_per_rt*plant.steam_kWh_per_lb]+[RT_TO_KW+plant.ec_electric_kW_per_rt]*3)
    res=linprog(-aux,A_ub=[np.full(4,RT_TO_KW),[plant.abs_steam_lb_per_rt,0,0,0],tower],
        b_ub=[loads[:,2].sum(),max(0,steam),plant.cooling_tower_rejection_kW*f['TOWER']*sum(f[f'TOWER_{i}'] for i in range(1,5))/4],
        bounds=bounds,method='highs')
    if not res.success:raise SolverExecutionError('Auxiliary diagnostic LP: '+res.message)
    return float(aux@res.x)
