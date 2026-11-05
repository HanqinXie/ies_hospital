"""Registered conditional response and shaft-work support, never future scripts."""
from __future__ import annotations
import copy
import math

from tools.operating_response import readiness as original
from tools.operating_response.diesel import validate_diesel_config

operating_options = original.operating_options
preparation_profile = original.preparation_profile
EXTRA_FIELDS = frozenset(('diesel_local_response', 'diesel_start_minute'))
COORDINATION_DEFAULTS = dict(response_minutes=30., support_minutes=60., delay_bound_minutes=30.,
    cooling_preparation=True, time_energy_preparation=True, execution_recovery=True)


def coordination_options(value=None):
    if value is not None and (not isinstance(value, dict) or set(value)-set(COORDINATION_DEFAULTS)):
        raise ValueError('Unsupported coordination options; no future inputs')
    result = {**COORDINATION_DEFAULTS, **copy.deepcopy(value or {})}
    for key in ('response_minutes','support_minutes','delay_bound_minutes'):
        result[key] = original.number(result[key], key)
        if key != 'delay_bound_minutes' and result[key] == 0.:
            raise ValueError(key+' must be positive')
    for key in ('cooling_preparation','time_energy_preparation','execution_recovery'):
        if type(result[key]) is not bool:
            raise ValueError(key+' must be boolean')
    if result['response_minutes'] < result['delay_bound_minutes']:
        raise ValueError('response_minutes must cover the registered unknown command delay bound')
    return result


validate_coordination_options = coordination_options


def validate_context(value):
    if not isinstance(value,dict) or set(value)-original.CONTEXT_FIELDS-EXTRA_FIELDS:
        raise ValueError('Unsupported current coordination context')
    base = original.validate_context({k:v for k,v in value.items() if k not in EXTRA_FIELDS})
    if 'diesel_start_minute' in value:
        start = value['diesel_start_minute']
        if start is not None:
            start = original.number(start,'diesel_start_minute')
            if start > base['observation_time_minute']:
                raise ValueError('Future diesel start is forbidden')
        base['diesel_start_minute'] = start
    if 'diesel_local_response' in value:
        local = value['diesel_local_response']
        if not isinstance(local,dict) or set(local) != {'model_id','rotor','actual_electric_kW','arrived_ceiling_kW','permission_status'}:
            raise ValueError('Unsupported observed diesel response fields')
        if not isinstance(local['model_id'],str) or not isinstance(local['permission_status'],str):
            raise ValueError('Invalid observed diesel identity/status')
        rotor = local['rotor']
        if not isinstance(rotor,dict) or 'config' not in rotor:
            raise ValueError('Missing observed rotor parameters')
        validate_diesel_config(rotor['config'])
        for key in ('rotor_energy_kWs','nominal_rotor_energy_kWs','mechanical_kW','speed_pu'):
            original.number(rotor.get(key),key)
        if rotor['nominal_rotor_energy_kWs'] <= 0.:
            raise ValueError('Rotor reference energy must be positive')
        if bool(rotor.get('fuel_exhausted',False)) and base['actual_diesel_kW'] > 1e-9:
            raise ValueError('Exhausted rotor cannot have actual electric delivery')
        for key,base_key in (('actual_electric_kW','actual_diesel_kW'),('arrived_ceiling_kW','diesel_arrived_cap_kW')):
            val=original.number(local[key],key)
            if not math.isclose(val,base[base_key],rel_tol=0.,abs_tol=1e-8):
                raise ValueError('Diesel response observation disagrees with physical readiness')
        base['diesel_local_response']=copy.deepcopy(local)
    return base


def original_context(context):
    c=validate_context(context)
    return {k:v for k,v in c.items() if k in original.CONTEXT_FIELDS}


def phase_sources(context, command, **kwargs):
    limits=kwargs.pop('execution_limits',None)
    result=original.phase_sources(original_context(context),command,**kwargs)
    if limits is not None:
        from .numerics import validate_execution_limits
        limits=validate_execution_limits(limits);held=result['held_before_arrival']
        held['conditional_arrived_grid_permission_kW']=held['grid_import_limit_kW']
        held['conditional_arrived_diesel_permission_kW']=held['diesel_permitted_cap_kW']
        held['grid_import_limit_kW']=limits['effective_grid_import_limit_kW']
        held['diesel_permitted_cap_kW']=limits['effective_diesel_limit_kW']
        held['grid_bus_state']=limits['grid_bus_state']
    return result


def response_envelope(context, *, response_minutes, delay_bound_minutes,
                      gt_start_delay_minutes, gt_ramp_kW_per_h, rated_gt_kW):
    c=validate_context(context)
    response=original.number(response_minutes,'response_minutes')
    lag=original.number(delay_bound_minutes,'delay_bound_minutes')
    delay=original.number(gt_start_delay_minutes,'gt_start_delay_minutes')
    rate=original.number(gt_ramp_kW_per_h,'gt_ramp_kW_per_h')
    rated=original.number(rated_gt_kW,'rated_gt_kW')
    if response <= 0. or rate <= 0.:
        raise ValueError('A positive registered response and inherited ramp are required')
    now=c['observation_time_minute']; deadline=now+response
    caps=[]; reasons=[]
    for i,status in enumerate(c['gt_physical_status']):
        pending=c['pending_starts'][i]
        if status in ('PROTECTION_LOCKED','EXOGENOUS_UNAVAILABLE','INTERLOCK_LATCHED'):
            cap=0.; reason='not_currently_startable'
        elif status=='RUNNING':
            # The selected ordinary target has its own unchanged shared ramp
            # row. This envelope does not issue a second future ramp budget.
            cap=rated; reason='already_running_same_ordinary_target'
        elif pending is not None:
            cap=min(rated,pending['requested']) if pending['ready']<=deadline else 0.
            reason='actual_pending_start_ready' if cap else 'actual_pending_start_after_response'
        else:
            earliest=max(now+lag,c['earliest_start_minute'][i])
            available=deadline-earliest
            cap=min(rated,max(0.,available)*rate/60.) if available>=delay else 0.
            reason='conditional_new_command_ready' if cap else 'registered_arrival_and_start_after_response'
        caps.append(float(cap));reasons.append(reason)
    return dict(gt_max_kW=caps,reason_by_GT=reasons,response_minutes=response,
        delay_bound_minutes=lag,deadline_minute=deadline,actual_arrival_known=False,
        conditional_on_registered_delay_bound=True,actual_supply=False,
        queue_promises_are_not_actual_supply=True,new_future_ramp_budget_issued=False)


def diesel_support_budget(context, *, rated_kW, remaining_kWh, support_minutes,
                          startup_delay_minutes):
    c=validate_context(context);rated=original.number(rated_kW,'rated_kW')
    fuel=original.number(remaining_kWh,'remaining_kWh')
    duration=original.number(support_minutes,'support_minutes')/60.
    delay=original.number(startup_delay_minutes,'startup_delay_minutes')
    if duration<=0.:raise ValueError('Support duration must be positive')
    local=c.get('diesel_local_response');rotor={} if local is None else local['rotor']
    cfg=validate_diesel_config(rotor.get('config'))
    nominal=cfg['inertia_H_seconds']*rated/cfg['power_factor_base']
    energy=original.number(rotor.get('rotor_energy_kWs',0.),'observed_rotor_energy')
    nominal_loss=rated*cfg['shaft_loss_fraction']
    loss_bound=nominal_loss*cfg['speed_max_pu']**2
    # Reserve positive storage change independently of fuel; never spend
    # existing rotating energy as sustained electrical fuel. This bound also
    # covers finite speed regulation within the declared research domain.
    storage_reserve=max(0.,nominal*cfg['speed_max_pu']**2-energy)/3600.
    start_work=0.
    if not c['diesel_connected']:
        now=c['observation_time_minute'];start=c.get('diesel_start_minute')
        remaining_start=delay if start is None else max(0.,delay-(now-start))
        # Exact integral of the owner's linear spin-up energy profile.
        final=max(energy,nominal)
        start_work=max(0.,nominal-energy)/3600.+nominal_loss*(remaining_start/60.)*(energy+final)/(2.*nominal) if nominal else 0.
        storage_reserve=max(0.,nominal*cfg['speed_max_pu']**2-final)/3600.
    overhead=start_work+storage_reserve+loss_bound*duration
    exhausted=bool(rotor.get('fuel_exhausted',False)) or (local is not None and local['permission_status']=='EMPTY')
    cap=0. if exhausted else min(rated,max(0.,(fuel-overhead)/duration))
    observed_shaft_rate=c['actual_diesel_kW']+loss_bound
    observed_support=(max(0.,fuel-start_work-storage_reserve)/observed_shaft_rate
        if observed_shaft_rate>0. else None)
    if not c['diesel_connected'] and c.get('diesel_start_minute') is None:
        observed_support=None
    if exhausted:observed_support=0.
    return dict(electric_support_ceiling_kW=cap,remaining_shaft_work_kWh=fuel,
        support_minutes=duration*60.,shaft_loss_bound_kW=loss_bound,
        startup_shaft_work_kWh=start_work,positive_rotor_storage_reserve_kWh=storage_reserve,
        overhead_shaft_work_kWh=overhead,shaft_budget_equation='T_hours*Pe + startup + positive_rotor_storage + T_hours*loss_bound <= remaining',
        rotor_is_sustained_fuel=False,actual_connection=c['diesel_connected'],
        actual_shaft_support_hours=observed_support,
        rotor_observed=local is not None,finite_speed_path_certified=False,
        missing_rotor_assumption='rest and inherited registered research parameters' if local is None else None)
