"""Tower-recovery ABS permission limited by freshly observed useful steam.

R1 remains unchanged and runs first. This additional command selection uses
completed actual HRSG/boiler production after reserving every current heating
request. It is a permission choice, never a forecast or delivered-supply proof.
No source setting, thermal state, command queue or native solver is replaced.
"""
from __future__ import annotations

from copy import deepcopy
import math
from types import MethodType

from hospital_ies.model import RT_TO_KW
from tools.recovery_dispatch_20261104 import policy as r1


R2_CONFIG = dict(schema='hospital_ies.recovery_steam.configuration.v1',
    policy_id='observed_steam_recovery_R2', candidate='R2',
    base_configuration=deepcopy(r1.R1_CONFIG), additional_support='TOWER',
    steam_rule='fresh_completed_actual_HRSG_plus_boiler_minus_current_heat')
RECEIPT_SCHEMA = 'hospital_ies.recovery_steam.command_selection.v1'


def _positive(value, name):
    result = r1._number(value, name)
    if result == 0.:
        raise ValueError('Positive original plant conversion required: ' + name)
    return result


def actual_steam_observation(record, conversion):
    """Reject absent/stale actual source meters; reserve all current P0-P3 heat."""
    try:
        now = r1._number(record['decision_time_min'], 'steam decision clock')
        source = record['actual_supply_telemetry']
        r1._block_clocks(source, 'actual steam telemetry', now)
        if source['sample_kind'] != 'completed_interval_average':
            raise ValueError('Fresh completed actual steam telemetry required')
        for name in ('valid_time_minute', 'interval_end_minute'):
            if r1._clock(source[name], 'actual steam ' + name, now) != now:
                raise ValueError('Fresh actual steam interval must end at the current decision clock')
        start = r1._clock(source['interval_start_minute'], 'actual steam interval start', now)
        if start >= now:
            raise ValueError('Actual steam requires a completed positive interval')
        actual = source['actual_dispatch']
        hrsg = r1._number(actual['hrsg_steam_lbph'], 'actual HRSG steam lb/h')
        boiler = r1._number(actual['boiler_steam_lbph'], 'actual boiler steam lb/h')
        heat = []
        for group in range(4):
            name = f'P{group}_h_demand_kW'
            metadata = record['feature_metadata'][name]
            r1._clock(metadata['available_time_min'], 'current heat available clock', now)
            if r1._clock(metadata['valid_time_min'], 'current heat valid clock', now) != now:
                raise ValueError('Current heat steam reservation requires a fresh valid clock')
            heat.append(r1._number(record['features'][name], 'current heat request'))
        capacity = r1._number(record['features']['capacity_ABS'], 'current effective ABS capacity')
    except (KeyError, TypeError) as exc:
        raise ValueError('Missing fresh actual steam source or current heat observation') from exc
    heat_steam = math.fsum(heat) / (conversion['steam_kWh_per_lb'] * conversion['exchanger_eff'])
    available = max(0., math.fsum((hrsg, boiler)) - heat_steam)
    return dict(time_minute=now, observation_interval_start_minute=start,
        observation_interval_end_minute=now, observed_actual_hrsg_steam_lbph=hrsg,
        observed_actual_boiler_steam_lbph=boiler, current_heat_request_by_priority_kW=heat,
        current_heat_reserved_steam_lbph=heat_steam, observed_steam_after_heat_lbph=available,
        observed_steam_supported_abs_permission_kW=available * conversion['RT_TO_KW'] / conversion['abs_steam_lb_per_rt'],
        current_effective_abs_capacity_kW=capacity,
        commanded_steam_credited=False, actual_source_meter_is_future_guarantee=False)


def select_steam_permissions(dispatch, steam, current, conversion):
    """Only lower ABS permission; recompute the existing bounded R1 EC envelope."""
    before = deepcopy(dispatch)
    native_abs = r1._number(dispatch['absorption_cooling_kW'], 'R1 ABS permission')
    capped = deepcopy(dispatch)
    capped['absorption_cooling_kW'] = min(native_abs,
        steam['observed_steam_supported_abs_permission_kW'], steam['current_effective_abs_capacity_kW'])
    selected, ec_receipt = r1.select_ec_permissions(capped, current)
    receipt = dict(schema=RECEIPT_SCHEMA, policy_id=R2_CONFIG['policy_id'], candidate='R2',
        selection='observed_actual_steam_after_all_current_heat', **deepcopy(steam),
        original_plant_conversion=deepcopy(conversion), r1_dispatch_before_steam_selection=before,
        r2_dispatch_after_steam_selection=deepcopy(selected),
        original_abs_permission_kW=native_abs, selected_abs_permission_kW=selected['absorption_cooling_kW'],
        abs_permission_is_actual_cooling_or_warm_state=False,
        ec_permission_total_before_kW=math.fsum(dispatch['electric_chiller_cooling_kW']),
        ec_permission_total_after_kW=math.fsum(selected['electric_chiller_cooling_kW']),
        ec_requested_envelope_kW=ec_receipt['requested_ec_envelope_kW'],
        ec_uncovered_permission_request_kW=ec_receipt['uncovered_permission_request_kW'],
        ec_effective_capacities_kW=deepcopy(ec_receipt['ec_effective_capacities_kW']),
        native_optimizer_proof_covers_transformed_command=False,
        r1_receipt_is_prior_stage_only=True, native_and_R1_solver_diagnostics_retained=True,
        transformed_command_is_physical_feasibility_certificate=False,
        GT_grid_DG_boiler_contacts_unchanged=True, ordinary_command_queue_unchanged=True,
        actual_solver_dynamic_limits_and_state_commits_unchanged=True,
        oversized_total_converter_permission_preservation_claimed=False,
        future_events_or_repair_endpoint_used=False)
    return selected, receipt


class RecoverySteamPolicy:
    def __init__(self, r1_controller, config, conversion):
        self.r1_controller = r1_controller
        self.config = deepcopy(config)
        self.conversion = deepcopy(conversion)
        self.policy_id = config['policy_id']
        self.steam_observation = None

    @property
    def monitor(self):
        return self.r1_controller.monitor

    @property
    def active(self):
        return self.monitor.active and 'TOWER' in self.monitor.recovered_supports

    def reset_policy_memory(self):
        self.r1_controller.reset_policy_memory()
        self.steam_observation = None

    def decide(self, records):
        request, diagnostic = self.r1_controller.decide(records)
        self.steam_observation = None
        if self.active:
            self.steam_observation = actual_steam_observation(records[-1], self.conversion)
        return request, diagnostic


class RecoverySteamCommandMixin:
    def _prepare_command_dispatch(self, dispatch, diagnostic, loads):
        value, trace = super()._prepare_command_dispatch(dispatch, diagnostic, loads)
        controller = self.env.recovery_steam_controller
        if not controller.active:
            return value, trace
        steam = controller.steam_observation
        if steam is None or steam['time_minute'] != float(self.time):
            raise ValueError('Fresh actual steam observation required at command generation')
        selected, receipt = select_steam_permissions(value, steam, controller.monitor.current, controller.conversion)
        receipt.update(generated_minute=float(self.time),
            observed_recovered_supports=deepcopy(controller.monitor.recovered_supports),
            native_raw_dispatch=deepcopy(trace['recovery_dispatch_selection']['native_raw_dispatch']),
            arrival_minute_source='original queued command event; no future arrival read')
        updated = deepcopy(trace)
        updated['recovery_steam_selection'] = receipt
        return selected, updated


def attach_recovery_policy(env, base_controller, config=None):
    """Attach the unchanged R1 first, then R2; caller passes the native controller."""
    selected = deepcopy(R2_CONFIG if config is None else config)
    if selected != R2_CONFIG:
        raise ValueError('Only the complete registered R2 configuration is supported')
    if hasattr(env, 'recovery_steam_controller'):
        raise ValueError('Attach R2 once before the original environment reset')
    conversion = {name: _positive(getattr(env.plant, name), name) for name in
        ('steam_kWh_per_lb', 'exchanger_eff', 'abs_steam_lb_per_rt')}
    conversion['RT_TO_KW'] = _positive(RT_TO_KW, 'RT_TO_KW')
    r1_controller = r1.attach_recovery_policy(env, base_controller, config=r1.R1_CONFIG)
    controller = RecoverySteamPolicy(r1_controller, selected, conversion)
    env.recovery_steam_controller = controller
    env.engineering_core_type = type('RecoverySteamR2Core',
        (RecoverySteamCommandMixin, env.engineering_core_type), {})
    original_reset = env.reset

    def reset(self, *, scenario, seed=None):
        # The unchanged inner R1 reset clears its own/base-controller memory.
        # Clear only R2 here so the original controller is reset exactly once.
        controller.steam_observation = None
        return original_reset(scenario=scenario, seed=seed)

    env.reset = MethodType(reset, env)
    return controller
