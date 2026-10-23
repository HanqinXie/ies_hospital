"""Five causal upper arrangements; identities never grant execution power.

R returns an ordinary dispatch directly. The other arrangements ask the one
V21 planner for a queued command. Present demand remains the service denominator.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from hospital_ies.model import RT_TO_KW, gt_level_properties
from tools.integrated_learning.policies import legal_history
from tools.engineering_model.policy import FORBIDDEN_OUTCOMES
from tools.operating_response.policy import DEFAULT_JOINT
from tools.model_closeout.readiness import coordination_options as c19_options
from tools.model_closeout.readiness import critical_reserve_rule as c19_reserve_rule
from tools.execution_recovery.numerics import validate_execution_limits

ACTION_SCHEMA = 'sci_engineering.upper_arrangements.v21.v1'
PLANNING_INTERFACE = 'sci_engineering.ordinary_joint_or_rule_command.v21.v1'
DISPATCH_FIELDS = ('gt_electric_kW', 'grid_import_kW', 'diesel_kW', 'boiler_steam_lbph',
    'absorption_cooling_kW', 'electric_chiller_cooling_kW', 'hrsg_steam_lbph',
    'tower_rejection_kW', 'gt_fuel_MMBtu_h')
EXTRA_DEFAULTS = dict(policy_mode='coordinated', reserve_enabled=True,
    critical_preparation_first=True, reserve_rule='critical_current',
    legacy_C19_order=False,
    fixed_reserve_hours=1., release_after_minutes=120., release_floor_hours=.5,
    observed_outage_minutes=0., critical_non_diesel_ready=False)
FIXED_RESERVE_CANDIDATES_HOURS = (.5, 1., 2.)
ADAPTIVE_RELEASE_CANDIDATES_MINUTES = (120., 240.)


def coordination_options(value=None):
    supplied = copy.deepcopy(value or {})
    if not isinstance(supplied, dict):
        raise ValueError('Current arrangement options must be a mapping')
    extra = {key:supplied.pop(key, copy.deepcopy(default)) for key,default in EXTRA_DEFAULTS.items()}
    supplied['critical_reserve'] = extra['reserve_enabled']
    result = dict(c19_options(supplied), **extra)
    for key in ('reserve_enabled','critical_preparation_first','critical_non_diesel_ready','legacy_C19_order'):
        if type(result[key]) is not bool:
            raise ValueError(key+' must be boolean')
    if result['policy_mode'] not in ('current','coordinated'):
        raise ValueError('Unknown upper arrangement mode')
    if result['reserve_rule'] not in ('none','critical_current','fixed_hours','causal_release'):
        raise ValueError('Unknown reserve rule')
    for key in ('fixed_reserve_hours','release_after_minutes','release_floor_hours','observed_outage_minutes'):
        value = result[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0.:
            raise ValueError('Invalid finite current reserve option: '+key)
        if key != 'observed_outage_minutes' and value == 0.:
            raise ValueError(key+' must be positive')
        result[key] = float(value)
    if result['policy_mode'] == 'current' and result['reserve_enabled']:
        raise ValueError('Current-only arrangement cannot reserve future energy')
    if result['legacy_C19_order'] and (not result['reserve_enabled']
            or not result['critical_preparation_first'] or result['reserve_rule'] != 'critical_current'):
        raise ValueError('A legacy C19 order cannot bind a mechanism override; explicitly disable legacy_C19_order')
    return result


def policy_config(alias, overrides=None):
    if alias not in ('R','M','F','C','A'):
        raise ValueError('Unknown engineering arrangement')
    options = dict(response_minutes=30., support_minutes=60., delay_bound_minutes=30.,
        cooling_preparation=True, time_energy_preparation=True, execution_recovery=True)
    options.update(EXTRA_DEFAULTS)
    if alias in ('R','M'):
        options.update(policy_mode='current',reserve_enabled=False,critical_preparation_first=False,
            reserve_rule='none',cooling_preparation=False,time_energy_preparation=False)
    elif alias == 'F':
        options.update(reserve_rule='fixed_hours',critical_preparation_first=False)
    elif alias == 'A':
        options.update(reserve_rule='causal_release')
    elif alias == 'C':
        options.update(legacy_C19_order=True)
    if overrides is not None:
        if not isinstance(overrides,dict) or set(overrides)-set(options)-{'critical_reserve'}:
            raise ValueError('Unsupported causal policy configuration')
        options.update(copy.deepcopy(overrides))
        if ({'reserve_enabled','critical_preparation_first'} & set(overrides)
                and 'legacy_C19_order' not in overrides):
            options['legacy_C19_order'] = False
    return coordination_options(options)


def reserve_reference(critical_loads, *, remaining_kWh, ec_kWe_per_kWc,
                      shaft_loss_bound_kW, minimum_hours=1., options=None):
    cfg = coordination_options(options)
    reference = c19_reserve_rule(critical_loads,remaining_kWh=remaining_kWh,
        ec_kWe_per_kWc=ec_kWe_per_kWc,shaft_loss_bound_kW=shaft_loss_bound_kW,
        minimum_hours=minimum_hours)
    reference.update(reserve_rule=cfg['reserve_rule'], reserve_enabled=cfg['reserve_enabled'],
        observed_outage_minutes=cfg['observed_outage_minutes'],
        critical_non_diesel_ready=cfg['critical_non_diesel_ready'], causal_release_active=False,
        true_repair_or_outage_endpoint_used=False)
    if cfg['reserve_rule'] == 'fixed_hours':
        reference['reserve_hours'] = cfg['fixed_reserve_hours']
    elif cfg['reserve_rule'] == 'causal_release':
        elapsed = cfg['observed_outage_minutes']
        active = cfg['critical_non_diesel_ready'] and elapsed >= cfg['release_after_minutes']
        reference['causal_release_active'] = active
        if active:
            # Observed running alternative support permits release. This is a
            # registered rule, never an estimate of the true outage endpoint.
            reference['reserve_hours'] = min(reference['reserve_hours'],cfg['release_floor_hours'])
    return reference


def validate_action(action):
    if not isinstance(action,dict) or len(action) != 1:
        raise ValueError('Exactly one current upper command route is required')
    if 'ordinary_dispatch' in action:
        command = action['ordinary_dispatch']
        if not isinstance(command,dict) or set(command)-{'dispatch','disconnected_kW','rule_diagnostic'}:
            raise ValueError('Unsupported ordinary dispatch fields')
        dispatch = command.get('dispatch')
        if not isinstance(dispatch,dict) or set(dispatch) != set(DISPATCH_FIELDS):
            raise ValueError('Complete canonical ordinary dispatch is required')
        for key,value in dispatch.items():
            a = np.asarray(value,float)
            expected = (3,) if key in ('gt_electric_kW','electric_chiller_cooling_kW') else ()
            if a.shape != expected or not np.isfinite(a).all() or np.any(a < 0.):
                raise ValueError('Invalid canonical dispatch: '+key)
        cuts = np.asarray(command.get('disconnected_kW'),float)
        if cuts.shape != (4,3) or not np.isfinite(cuts).all() or np.any(cuts < 0.):
            raise ValueError('Invalid actual carrier contact requests')
        return copy.deepcopy(action)
    if set(action) != {'state_coordinated_joint'}:
        raise ValueError('Unsupported SCI command route')
    joint = action['state_coordinated_joint']
    if not isinstance(joint,dict) or set(joint)-{'options','coordination','risk_loads_kW'}:
        raise ValueError('Unsupported current coordinated request fields')
    from .planner import _options
    result = copy.deepcopy(action)
    request = result['state_coordinated_joint']
    request['options'] = _options(request.get('options',DEFAULT_JOINT))
    request['coordination'] = coordination_options(request.get('coordination'))
    if 'risk_loads_kW' in request:
        demand = np.asarray(request['risk_loads_kW'],float)
        if demand.shape != (4,3) or not np.isfinite(demand).all() or np.any(demand < 0.):
            raise ValueError('Invalid observed historical demand floor')
    return result


def _current_sci_blocks(record):
    """Validate the versioned current hardware blocks without broadening v20."""
    t = float(record['decision_time_min'])
    for name in ('sci_auxiliary','sci_storage'):
        block = record.get(name)
        if block is None:
            continue
        if not isinstance(block,dict):
            raise ValueError('Invalid current '+name+' observation')
        for clock in ('observation_time_minute','available_time_minute'):
            value = block.get(clock)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value > t or value < 0.:
                raise ValueError('Unavailable or future '+name+' clock')
    auxiliary = record.get('sci_auxiliary',{})
    available = auxiliary.get('availability',{})
    permitted = {f'AUX_FAN_P{g}' for g in range(4)} | {'AUX_CHW_PUMP','AUX_HEAT_PUMP'}
    if not isinstance(available,dict) or set(available)-permitted:
        raise ValueError('Unknown current auxiliary observation')
    for value in available.values():
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0. <= value <= 1.:
            raise ValueError('Invalid current auxiliary availability')
    if auxiliary:
        required = np.asarray(auxiliary.get('required_kW'),float)
        if required.shape != (4,3) or not np.isfinite(required).all() or np.any(required < 0.):
            raise ValueError('Invalid current auxiliary request ledger')
    return copy.deepcopy(available)


def _non_diesel_ready(features, loads, plant, auxiliary):
    if plant is None:
        return False
    electric = sum(float(features.get(f'pre_GT_{i}_kW',0.))*float(features.get(f'availability_GT_{i}',0.)) for i in (1,2,3))
    electric += float(features.get('grid_import_prev_kW',0.))*float(features.get('availability_GRID',0.))
    cold = float(loads[:2,2].sum())
    critical_power = float(loads[:2,0].sum()) + cold*plant.ec_electric_kW_per_rt/RT_TO_KW
    # All considered cooling requires the shared tower. Running GT support is
    # counted at measured output; pending starts and unused capacity are zero.
    tower_bank = sum(float(features.get(f'availability_TOWER_{i}',1.)) for i in (1,2,3,4))/4.
    rejection = plant.cooling_tower_rejection_kW*float(features.get('availability_TOWER',0.))*tower_bank
    tower = rejection >= cold*(1.+plant.ec_electric_kW_per_rt/RT_TO_KW)
    ec = sum(plant.electric_chillers_rt[i-1]*RT_TO_KW*float(features.get(f'availability_EC_{i}',0.)) for i in (1,2,3))
    boilers = sum(float(features.get(f'availability_BOILER_{i}',0.)) for i in (1,2,3))
    heat = float(loads[:2,1].sum())
    heat_cap = plant.boiler_steam_lbph/3.*boilers*plant.steam_kWh_per_lb*plant.exchanger_eff
    auxiliary_ready = True
    for g in (0,1):
        if loads[g,1] > 1e-9 or loads[g,2] > 1e-9:
            auxiliary_ready &= auxiliary.get(f'AUX_FAN_P{g}',1.) >= 1.-1e-9
        if loads[g,2] > 1e-9:
            auxiliary_ready &= auxiliary.get('AUX_CHW_PUMP',1.) >= 1.-1e-9
        if loads[g,1] > 1e-9:
            auxiliary_ready &= auxiliary.get('AUX_HEAT_PUMP',1.) >= 1.-1e-9
    return bool(auxiliary_ready and electric >= critical_power and (cold <= 1e-9 or (tower and ec >= cold)) and heat_cap >= heat)


def _rule_dispatch(features, loads, plant, execution_limits=None):
    ppoints,spoints,fpoints = gt_level_properties(plant,float(features['outdoor_temperature_C']))
    rated = float(ppoints[-1]); steam_slope = float(spoints[-1]/rated)
    grid = float(features.get('grid_import_limit_kW',0.))*float(features.get('availability_GRID',0.))
    grid_permission = (float(features.get('grid_import_limit_kW',0.))*execution_limits['hardware_grid_available_fraction']
        if execution_limits is not None else grid)
    bus = execution_limits['grid_bus_state'] if execution_limits is not None else ('GRID' if grid > 0. else 'ISLAND')
    electric = float(loads[:,0].sum()); cooling = float(loads[:,2].sum())
    requested_cooling = cooling
    if float(features.get('availability_TOWER',0.)) <= 0.:
        cooling = 0.
    heat_steam = float(loads[:,1].sum())/(plant.steam_kWh_per_lb*plant.exchanger_eff)
    capacities = np.array([rated*float(features.get(f'availability_GT_{i}',0.)) for i in (1,2,3)])
    running = np.array([float(features.get(f'pre_GT_{i}_kW',0.)) > 1e-9 for i in (1,2,3)])
    order = sorted(range(3),key=lambda i:(not running[i],i))
    # Conventional sequence: one useful online unit in normal operation;
    # enough available units for current electric demand when grid is absent.
    need = max(0.,electric+cooling*plant.ec_electric_kW_per_rt/RT_TO_KW-grid)
    if grid > 0. and electric >= .4*rated:
        need = max(need,.4*rated)
    gt = np.zeros(3); remaining = need
    for i in order:
        if remaining <= 1e-9 or capacities[i] <= 0.:
            continue
        desired = min(capacities[i],max(.4*capacities[i],remaining))
        # A low-load unit is not requested where even full current electric
        # and chiller load cannot absorb its original stable output.
        if desired > electric+cooling*plant.ec_electric_kW_per_rt/RT_TO_KW and not np.any(gt):
            continue
        gt[i] = desired; remaining = max(0.,remaining-desired)
    hrsg = sum(gt[i]*steam_slope*float(features.get(f'availability_HRSG_{i+1}',0.)) for i in range(3))
    ready_hrsg = sum(min(gt[i],float(features.get(f'pre_GT_{i+1}_kW',0.)))*steam_slope
        *float(features.get(f'availability_GT_{i+1}',0.))*float(features.get(f'availability_HRSG_{i+1}',0.))
        for i in range(3))
    # Source/heat availability determines the ordinary absorption ceiling.
    absorption = min(cooling,plant.absorption_rt*RT_TO_KW*float(features.get('availability_ABS',0.)),
        max(0.,ready_hrsg-heat_steam)/plant.abs_steam_lb_per_rt*RT_TO_KW)
    ec = np.zeros(3); residual = max(0.,cooling-absorption)
    for i in range(3):
        ec[i] = min(residual,plant.electric_chillers_rt[i]*RT_TO_KW*float(features.get(f'availability_EC_{i+1}',0.)))
        residual -= ec[i]
    boiler_cap = plant.boiler_steam_lbph/3.*sum(float(features.get(f'availability_BOILER_{i}',0.)) for i in (1,2,3))
    boiler = min(boiler_cap,max(0.,heat_steam+absorption/RT_TO_KW*plant.abs_steam_lb_per_rt-ready_hrsg))
    diesel = plant.emergency_diesel_kW*float(features.get('availability_DIESEL',0.)) if bus != 'GRID' else 0.
    actual_aux = absorption/RT_TO_KW*plant.abs_electric_kW_per_rt+float(ec.sum())/RT_TO_KW*plant.ec_electric_kW_per_rt
    rejection = absorption*(1.+plant.abs_steam_lb_per_rt*plant.steam_kWh_per_lb/RT_TO_KW)
    rejection += float(ec.sum())*(1.+plant.ec_electric_kW_per_rt/RT_TO_KW)
    dispatch = dict(gt_electric_kW=gt.tolist(),grid_import_kW=grid_permission,diesel_kW=diesel,
        boiler_steam_lbph=boiler,absorption_cooling_kW=absorption,electric_chiller_cooling_kW=ec.tolist(),
        hrsg_steam_lbph=float(hrsg),tower_rejection_kW=float(rejection),
        gt_fuel_MMBtu_h=float(gt.sum()*fpoints[-1]/rated))
    return dispatch,dict(rule='running_units_first_current_load_merit_sequence',
        current_building_electric_kW=electric,current_cooling_kW=requested_cooling,
        requested_generation_kW=float(gt.sum()),converter_auxiliary_at_ceiling_kW=actual_aux,
        observed_ready_hrsg_support_lbph=float(ready_hrsg),
        observed_grid_hardware_fraction=(execution_limits['hardware_grid_available_fraction'] if execution_limits is not None
            else float(features.get('availability_GRID',0.))),grid_bus_state=bus,
        conditional_grid_permission_is_actual_supply=False,
        pending_start_is_delivered_supply=False,upper_optimizer_called=False,
        derived_diagnostic_fields=['hrsg_steam_lbph','tower_rejection_kW','gt_fuel_MMBtu_h'])


class SciPolicy:
    def __init__(self, alias, config=None, policy_id=None, plant=None):
        self.alias = alias
        self.config = policy_config(alias,config)
        self.policy_id = policy_id or alias+'_sci_v21'
        if not isinstance(self.policy_id,str) or not self.policy_id.strip():
            raise ValueError('Explicit nonempty policy identity required')
        if alias == 'R' and plant is None:
            raise ValueError('R requires immutable common plant parameters')
        self.plant = copy.deepcopy(plant)

    def reset_policy_memory(self):
        pass

    def decide(self, records):
        if any(FORBIDDEN_OUTCOMES.intersection(record) for record in records):
            raise ValueError('Hidden outcomes are not current observations')
        auxiliary = [_current_sci_blocks(record) for record in records]
        limits = [validate_execution_limits(record['execution_limits']) if 'execution_limits' in record else None
            for record in records]
        history = legal_history(records,max_length=len(records))
        demands = [np.array([[r['features'][f'{g}_{c}_demand_kW'] for c in ('e','h','c')]
            for g in ('P0','P1','P2','P3')],float) for r in history]
        if not all(np.isfinite(d).all() and np.all(d >= 0.) for d in demands):
            raise ValueError('Invalid current carrier demand')
        features = history[-1]['features']; current = demands[-1]
        diagnostic = dict(policy_id=self.policy_id,engineering_alias=self.alias,strategy_fallback=False,
            strategy_status='causal_current_upper_arrangement',future_rollouts_online=0,
            planning_interface=PLANNING_INTERFACE,command_is_immediate_execution=False)
        if self.alias == 'R':
            dispatch,rule = _rule_dispatch(features,current,self.plant,limits[-1])
            request = validate_action({'ordinary_dispatch':dict(dispatch=dispatch,
                disconnected_kW=np.zeros((4,3)).tolist(),rule_diagnostic=rule)})
            diagnostic.update(upper_optimizer_called=False,rule_diagnostic=rule)
            return request,diagnostic
        options = copy.deepcopy(self.config)
        options['observed_outage_minutes'] = float(features.get('grid_outage_elapsed_minutes',0.))
        options['critical_non_diesel_ready'] = _non_diesel_ready(features,current,self.plant,auxiliary[-1])
        request = validate_action({'state_coordinated_joint':dict(options=copy.deepcopy(DEFAULT_JOINT),
            coordination=options,risk_loads_kW=np.min(demands,axis=0).tolist())})
        diagnostic.update(upper_optimizer_called=True,coordination=copy.deepcopy(options))
        return request,diagnostic


def policy_specs():
    return {alias:dict(policy_id=alias+'_sci_v21',kind='sci_engineering',display_alias=alias,
        config=policy_config(alias)) for alias in ('R','M','F','C','A')}


def factory(spec, plant=None):
    if not isinstance(spec,dict) or spec.get('kind') != 'sci_engineering':
        raise ValueError('Explicit SCI policy specification required')
    alias = spec.get('display_alias')
    if alias not in ('R','M','F','C','A'):
        raise ValueError('Explicit engineering alias required; identities are not control inputs')
    return SciPolicy(alias,config=spec.get('config'),policy_id=spec.get('policy_id'),plant=plant)


def mechanism_specs():
    result = {}
    for reserve in (False,True):
        for preparation in (False,True):
            key = 'reserve'+str(int(reserve))+'_preparation'+str(int(preparation))
            result[key] = dict(policy_id=key+'_sci_v21',kind='sci_engineering',display_alias='C',
                mechanism_comparison=True,config=policy_config('C',dict(reserve_enabled=reserve,
                    critical_preparation_first=preparation,legacy_C19_order=False)))
    return result


def candidate_specs():
    result = {}
    for hours in FIXED_RESERVE_CANDIDATES_HOURS:
        key = 'F_hours_'+str(hours)
        result[key] = dict(policy_id=key+'_sci_v21',kind='sci_engineering',display_alias='F',
            selection_role='development_only',config=policy_config('F',dict(fixed_reserve_hours=hours)))
    for minutes in ADAPTIVE_RELEASE_CANDIDATES_MINUTES:
        key = 'A_release_'+str(int(minutes))
        result[key] = dict(policy_id=key+'_sci_v21',kind='sci_engineering',display_alias='A',
            selection_role='development_only',config=policy_config('A',dict(release_after_minutes=minutes)))
    return result
