"""Current snapshot coordination; no simulator, CurrentProblem or future API.

Fixed and feedback policies share planner options, budgets and pressure copies.
Feedback changes only the preparation-order enum. Internal sensors retain the
existing ideal-observation assumption; queued ETAs describe prior plans only.
"""
from __future__ import annotations
import copy
import math

import numpy as np
from hospital_ies.resilience import ZONE_PRIORITY
from tools.integrated_learning.policies import legal_history
from .planner import _options

EPS = 1e-9
PREFERENCES = frozenset(('standby_first', 'bridge_first', 'service_first', 'bridge_service_first'))
POLICY_OPTIONS = frozenset(('coordination_mode', 'fixed_preference'))
DEFAULT_PLANNING_OPTIONS = dict(variant='coupled', support_hours=2.,
    critical_reserve_multiplier=1.25, connection_mode='retain_when_no_gt',
    regulator_consistent=True, preserve_one_gt_standby=True,
    service_budget_enabled=True, service_budget_fractions=[.05, .20],
    handoff_enabled=True, features_enabled=True)
FORBIDDEN_OUTCOMES = frozenset(('labels', 'actual_execution', 'post_outcome',
    'first_failure_observation_interval', 'first_failure_observed_minute'))
P2_MARGIN_KEYS = tuple('zone_margin_'+z for z,p in ZONE_PRIORITY.items() if p == 2)


def validate_policy_config(value):
    if not isinstance(value, dict):
        raise ValueError('Policy configuration must be a mapping')
    supplied = copy.deepcopy(value)
    mode = supplied.pop('coordination_mode', 'state')
    fixed = supplied.pop('fixed_preference', supplied.get('coordination_preference', 'standby_first'))
    if not isinstance(mode,str) or not isinstance(fixed,str) or mode not in ('fixed', 'state') or fixed not in PREFERENCES:
        raise ValueError('Unsupported coordination mode or fixed preference')
    options = _options({**DEFAULT_PLANNING_OPTIONS, **supplied})
    options['coordination_preference'] = fixed
    return dict(options, coordination_mode=mode, fixed_preference=fixed)


def _number(value, name, *, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Missing or invalid numeric observation: '+name)
    result = float(value)
    if not math.isfinite(result) or result < 0 or (maximum is not None and result > maximum):
        raise ValueError('Missing or invalid numeric observation: '+name)
    return result


def lawful_views(records):
    """Copy the old feature whitelist and an explicit previously generated ledger.

    Scenario/root metadata is not read or copied. Missing sensors fail rather
    than becoming zeros. Future planned ETA is allowed, future knowledge is not.
    """
    if not records or any(FORBIDDEN_OUTCOMES.intersection(row) for row in records):
        raise ValueError('No lawful history or hidden outcome attached to a decision')
    history = legal_history(records, max_length=len(records))
    views = []
    required = {'availability_GRID', 'grid_import_limit_kW', 'arrived_grid_import_limit_kW',
        'grid_import_prev_kW', 'diesel_prev_kW', 'diesel_energy_kWh', 'diesel_status', 'control_available'}
    required.update(f'{prefix}{i}{suffix}' for i in (1,2,3)
        for prefix,suffix in (('pre_GT_', '_kW'), ('state_GT_', ''), ('overload_minutes_GT_', '')))
    required.update(P2_MARGIN_KEYS)
    required.update(f'{g}_{c}_demand_kW' for g in ('P0','P1','P2','P3') for c in ('e','h','c'))
    for original, legal in zip(records, history):
        t = legal['decision_time_min']; f = legal['features']
        if not required <= set(f):
            raise ValueError('Required current coordination sensors are missing')
        if 'observation_time_min' in original and float(original['observation_time_min']) != t:
            raise ValueError('Observation clock differs from decision clock')
        for key in required - set(P2_MARGIN_KEYS) - {'diesel_status','control_available',*(f'state_GT_{i}' for i in (1,2,3))}:
            _number(f[key], key, maximum=1. if key == 'availability_GRID' else None)
        for key in P2_MARGIN_KEYS:
            if isinstance(f[key], bool) or not isinstance(f[key], (int,float)) or not math.isfinite(float(f[key])):
                raise ValueError('Invalid P2 temperature margin')
        if type(f['control_available']) is not bool or f['diesel_status'] not in ('OFF','STARTING','READY','CONNECTED','FAILED','EMPTY'):
            raise ValueError('Invalid current support state')
        if any(f[f'state_GT_{i}'] not in ('OFF','STARTING','RUNNING','FAILED') for i in (1,2,3)):
            raise ValueError('Invalid current GT state')
        queue = original.get('known_command_queue')
        if not isinstance(queue, list):
            raise ValueError('Known command queue observation is missing')
        copied = []; ids = set()
        for command in queue:
            allowed = {'command_id','generated_minute','planned_arrival_minute','request','permitted_dispatch'}
            if not isinstance(command, dict) or set(command) != allowed:
                raise ValueError('Unsupported known command ledger fields')
            cid = command['command_id']
            generated = _number(command['generated_minute'], 'command generation')
            eta = _number(command['planned_arrival_minute'], 'planned arrival')
            if type(cid) is not int or cid <= 0 or cid in ids or generated > t or eta < generated:
                raise ValueError('Command plan was not generated at or before this observation')
            ids.add(cid)
            # Validate the historical request without copying unrelated metadata.
            from .interface import validate_action
            validate_action(command['request'])
            dispatch = command['permitted_dispatch']
            if not isinstance(dispatch, dict):
                raise ValueError('Missing prior permitted dispatch')
            gt = dispatch.get('gt_electric_kW')
            if not isinstance(gt, (list,tuple)) or len(gt) != 3:
                raise ValueError('Invalid prior GT plan')
            sources = dict(gt_electric_kW=[_number(v,'queued GT') for v in gt],
                grid_import_kW=_number(dispatch.get('grid_import_kW'),'queued GRID ceiling'),
                diesel_kW=_number(dispatch.get('diesel_kW'),'queued DG'))
            copied.append(dict(command_id=cid,generated_minute=generated,planned_arrival_minute=eta,
                               permitted_dispatch=sources))
        arrived = original.get('arrived_command_id')
        if arrived is not None and (type(arrived) is not int or arrived <= 0):
            raise ValueError('Invalid arrived command ID')
        views.append(dict(decision_time_min=t,features=f,known_command_queue=copied,arrived_command_id=arrived))
    return views


def _pending_source_change(view):
    f = view['features']
    actual = [float(f[f'pre_GT_{i}_kW']) for i in (1,2,3)]
    # Plans may be superseded, clamped or fail. This flag never credits output.
    return any(any(abs(x-y)>EPS for x,y in zip(c['permitted_dispatch']['gt_electric_kW'],actual))
        or abs(c['permitted_dispatch']['diesel_kW']-f['diesel_prev_kW'])>EPS
        or abs(c['permitted_dispatch']['grid_import_kW']-f['arrived_grid_import_limit_kW'])>EPS
        for c in view['known_command_queue'])


def _feedback(views, support_hours):
    dependent = False; state = 'NORMAL'; reasons = []
    for view in views:
        f = view['features']; queued_change = _pending_source_change(view)
        grid_degraded = f['availability_GRID'] < 1.-EPS
        dg_engaged = f['diesel_prev_kW'] > EPS or f['diesel_status'] in ('STARTING','READY','CONNECTED')
        starting = any(f[f'state_GT_{i}']=='STARTING' for i in (1,2,3))
        stressed = any(f[f'overload_minutes_GT_{i}'] > 0. for i in (1,2,3))
        coverage = f['diesel_energy_kWh']/f['diesel_prev_kW'] if f['diesel_prev_kW']>EPS else None
        actual_non_diesel = f['grid_import_prev_kW'] + sum(f[f'pre_GT_{i}_kW'] for i in (1,2,3))
        critical_electric = f['P0_e_demand_kW'] + f['P1_e_demand_kW']
        # A current base-electric bridge reference only. Chiller auxiliaries,
        # steam/cold feasibility and delayed future service are not certified.
        electric_bridge = actual_non_diesel > EPS and actual_non_diesel >= critical_electric-EPS
        ready = (f['availability_GRID'] >= 1.-EPS and f['arrived_grid_import_limit_kW'] > EPS and electric_bridge
                 and f['diesel_prev_kW'] <= EPS and f['diesel_status']=='OFF' and not starting and not queued_change)
        recovering = dependent and not grid_degraded and not ready
        reasons = []
        for present, label in ((grid_degraded,'GRID_degraded'),(dg_engaged,'DG_dependency'),
            (starting,'GT_starting'),(stressed,'protection_accumulating'),
            (queued_change,'known_source_command_pending'),(not f['control_available'],'current_control_delay'),
            (coverage is not None and coverage < support_hours,'fuel_coverage_below_registered_preference')):
            if present:reasons.append(label)
        state = 'RECOVERY' if recovering else 'SUPPORT' if reasons else 'NORMAL'
        if recovering:reasons.append('observed_recovery_without_completed_handoff')
        if grid_degraded or dg_engaged:dependent = True
        elif ready:dependent = False
    thermal = any(float(views[-1]['features'][k]) <= 0. for k in P2_MARGIN_KEYS)
    if thermal:reasons.append('P2_observed_thermal_margin_nonpositive')
    bridge = state != 'NORMAL'
    preference = ('bridge_service_first' if thermal else 'bridge_first') if bridge else ('service_first' if thermal else 'standby_first')
    return state, preference, reasons, coverage


class CoordinatedDispatchPolicy:
    def __init__(self, config, policy_id):
        self.config = validate_policy_config(config)
        self.policy_id = policy_id

    def decide(self, records):
        views = lawful_views(records)
        state, preference, reasons, coverage = _feedback(views, self.config['support_hours'])
        if self.config['coordination_mode'] == 'fixed':preference = self.config['fixed_preference']
        options = {k:copy.deepcopy(v) for k,v in self.config.items() if k not in POLICY_OPTIONS}
        options['coordination_preference'] = preference
        values = [np.asarray([[v['features'][f'{g}_{c}_demand_kW'] for c in ('e','h','c')]
                   for g in ('P0','P1','P2','P3')],float) for v in views]
        envelope = np.min(values,axis=0)
        request = {'coordinated_joint':{'options':_options(options),'risk_loads_kW':envelope.tolist()}}
        from .interface import PLANNING_INTERFACE, validate_action
        request = validate_action(request)
        return request, dict(strategy_status='current_state_coordination',strategy_fallback=False,
            policy_id=self.policy_id,model_id=None,mode='current_joint_dispatch',
            coordination_mode=self.config['coordination_mode'],coordination_state=state,
            coordination_preference=preference,coordination_reasons=reasons,
            fuel_coverage_hours=coverage,actual_diesel_kW=float(views[-1]['features']['diesel_prev_kW']),
            actual_non_diesel_electric_kW=float(views[-1]['features']['grid_import_prev_kW']+
                sum(views[-1]['features'][f'pre_GT_{i}_kW'] for i in (1,2,3))),
            current_critical_base_electric_kW=float(views[-1]['features']['P0_e_demand_kW']+
                views[-1]['features']['P1_e_demand_kW']),
            recovery_readiness_semantics='Current actual non-DG ports cover P0/P1 base electric demand; not a coupled heat/cold or future service guarantee',
            known_command_queue=copy.deepcopy(views[-1]['known_command_queue']),
            arrived_command_id=views[-1]['arrived_command_id'],future_rollouts_online=0,
            history_observation_count=len(views),requested_joint_options=copy.deepcopy(options),
            history_envelope_semantics='Episode legal observed componentwise demand minimum; pressure assumption, not forecast',
            queue_eta_semantics='Previously generated plan; not promised actual future arrival',
            planning_interface=PLANNING_INTERFACE,source_total_equal_to_priority_required=False)
