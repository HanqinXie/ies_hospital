"""R1: causal recovery EC permission coverage and warm converter continuity.

The original controller still produces the original upper request. Only the
existing internal command-preparation hook selects a different EC permission
envelope. Every actual source, flow, dynamic, service and queue constraint is
owned by the unchanged native implementation. Permissions are never credited as
delivered supply, a warmup commit, or a native optimizer certificate.
"""
from __future__ import annotations

from copy import deepcopy
import math
from types import MethodType


R1_CONFIG = dict(schema='hospital_ies.recovery_dispatch.configuration.v1',
    policy_id='recovery_converter_continuity_R1', candidate='R1',
    support_devices=['TOWER', 'AUX_CHW_PUMP'])
RECEIPT_SCHEMA = 'hospital_ies.recovery_dispatch.command_selection.v1'


def _number(value, name, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Invalid current ' + name)
    result = float(value)
    if not math.isfinite(result) or result < 0. or (maximum is not None and result > maximum):
        raise ValueError('Invalid current ' + name)
    return result


def _clock(value, name, now):
    result = _number(value, name)
    if result > now:
        raise ValueError('Unavailable or future observation clock: ' + name)
    return result


def _block_clocks(block, name, now):
    for key in ('observation_time_minute', 'available_time_minute'):
        _clock(block[key], name + '.' + key, now)
    if block['observation_time_minute'] != now:
        raise ValueError('Stale block lacks the current observation clock: ' + name)


def current_observation(record):
    """Read only named, provenanced current observations; never a scene/calendar."""
    now = _number(record['decision_time_min'], 'decision clock')
    _clock(record['observation_time_min'], 'observation clock', now)
    features, metadata = record['features'], record['feature_metadata']

    def feature(name, maximum=None):
        provenance = metadata[name]
        for key in ('available_time_min', 'valid_time_min'):
            _clock(provenance[key], name + '.' + key, now)
        if provenance['valid_time_min'] != now:
            raise ValueError('Stale feature lacks the current valid clock: ' + name)
        return _number(features[name], name, maximum)

    tower = feature('availability_TOWER', 1.)
    auxiliary = record['sci_auxiliary']
    _block_clocks(auxiliary, 'sci_auxiliary', now)
    pump = _number(auxiliary['availability']['AUX_CHW_PUMP'], 'AUX_CHW_PUMP availability', 1.)
    _block_clocks(record['minute1_sampling'], 'minute1_sampling', now)
    dynamic = record['cooling_dynamics']
    elapsed = _number(dynamic['elapsed_minutes'], 'cooling dynamics clock')
    if abs(elapsed - now) > 1e-8:
        raise ValueError('Cooling dynamics observation must have the current clock')
    cooling = [_number(value, 'observed cooling capability') for value in dynamic['cooling_kW']]
    if len(cooling) != 4:
        raise ValueError('Four current cooling capability observations required')
    capacities, availabilities = [], []
    for i in (1, 2, 3):
        availability = feature(f'availability_EC_{i}', 1.)
        capacity = feature(f'capacity_EC_{i}')
        # data_contract.graph_view already multiplies nominal rating by current
        # availability. Multiplying here again would double-derate partial units.
        if availability == 0. and capacity != 0.:
            raise ValueError('Unavailable EC has positive observed effective capacity')
        availabilities.append(availability)
        capacities.append(capacity)
    abs_availability = feature('availability_ABS', 1.)
    abs_capacity = feature('capacity_ABS')
    if abs_availability == 0. and abs_capacity != 0.:
        raise ValueError('Unavailable ABS has positive observed effective capacity')
    demand = math.fsum(feature(f'P{group}_c_demand_kW') for group in range(4))
    telemetry = record['actual_supply_telemetry']
    _block_clocks(telemetry, 'actual_supply_telemetry', now)
    abs_actual = 0.
    if telemetry['sample_kind'] == 'completed_interval_average':
        valid = _clock(telemetry['valid_time_minute'], 'actual telemetry valid clock', now)
        end = _clock(telemetry['interval_end_minute'], 'actual telemetry end clock', now)
        observed = _number(telemetry['actual_dispatch']['absorption_cooling_kW'], 'actual ABS cooling')
        # Stale supply is not a current guarantee. Its numeric value is kept in
        # the original observation, but receives no coverage credit here.
        if valid == now and end == now:
            abs_actual = observed
    elif telemetry['sample_kind'] != 'no_completed_interval':
        raise ValueError('Unknown actual cooling telemetry observation kind')
    return dict(time_minute=now, supports={'TOWER': tower, 'AUX_CHW_PUMP': pump},
        current_cold_demand_kW=demand, ec_effective_capacities_kW=capacities,
        ec_availabilities=availabilities, observed_ec_cooling_kW=cooling[1:],
        observed_abs_coverage_kW=min(abs_actual, cooling[0], abs_capacity),
        coverage_is_delivered_guarantee=False, storage_future_supply_credited=False)


class RecoveryMonitor:
    def __init__(self):
        self.reset()

    def reset(self):
        self.seen_zero = {}
        self.recovered_supports = []
        self.activated_minute = None
        self.active = False
        self.current = None

    def observe(self, record):
        current = current_observation(record)
        now = current['time_minute']
        if self.current is not None and now <= self.current['time_minute']:
            raise ValueError('Recovery monitor requires increasing current observation clocks')
        for support, availability in current['supports'].items():
            if availability == 0.:
                self.seen_zero[support] = now
            elif support in self.seen_zero and now > self.seen_zero[support]:
                if support not in self.recovered_supports:
                    self.recovered_supports.append(support)
                if self.activated_minute is None:
                    self.activated_minute = now
        # A renewed shared-support outage suspends the intervention. The native
        # command remains untouched until current observations show return again.
        self.active = self.activated_minute is not None and all(v > 0. for v in current['supports'].values())
        self.current = current


def select_ec_permissions(dispatch, observation):
    """Pure arithmetic selection, bounded by effective current machine ratings."""
    original = [_number(v, 'native EC permission') for v in dispatch['electric_chiller_cooling_kW']]
    capacities = observation['ec_effective_capacities_kW']
    warm = observation['observed_ec_cooling_kW']
    if len(original) != 3:
        raise ValueError('Three native EC command permissions required')
    native_total, capacity_total = math.fsum(original), math.fsum(capacities)
    if native_total > capacity_total:
        raise ValueError('Native EC permission total exceeds current effective capacities; cannot preserve and cap it')
    abs_coverage = min(observation['observed_abs_coverage_kW'],
        _number(dispatch['absorption_cooling_kW'], 'native ABS permission'))
    residual = max(0., observation['current_cold_demand_kW'] - abs_coverage)
    requested = max(native_total, residual)
    remaining = min(requested, capacity_total)
    order = sorted(range(3), key=lambda i: (-warm[i], i))
    selected = [0., 0., 0.]
    for i in order:
        selected[i] = min(remaining, capacities[i])
        remaining = max(0., remaining - selected[i])
    result = deepcopy(dispatch)
    result['electric_chiller_cooling_kW'] = selected
    receipt = dict(schema=RECEIPT_SCHEMA, policy_id=R1_CONFIG['policy_id'], candidate='R1',
        selection='current_cold_residual_envelope_warm_first',
        current_cold_demand_kW=observation['current_cold_demand_kW'],
        observed_abs_coverage_kW=abs_coverage, native_ec_permission_total_kW=native_total,
        requested_ec_envelope_kW=requested, selected_ec_permission_total_kW=math.fsum(selected),
        uncovered_permission_request_kW=max(0., requested - math.fsum(selected)),
        ec_effective_capacities_kW=deepcopy(capacities),
        observed_ec_cooling_kW=deepcopy(warm), warm_first_order=[i + 1 for i in order],
        native_prepared_dispatch=deepcopy(dispatch), transformed_dispatch=deepcopy(result),
        permission_changed=selected != original,
        native_optimizer_proof_covers_transformed_command=False,
        native_solver_diagnostic_retained_unmodified=True,
        transformed_dispatch_is_physical_feasibility_certificate=False,
        actual_flow_and_dynamic_limits_unchanged=True, ordinary_command_queue_unchanged=True,
        upper_action_schema_unchanged=True, source_ABS_boiler_contact_selection_unchanged=True,
        storage_future_supply_credited=False, future_calendar_or_repair_endpoint_used=False)
    return result, receipt


class RecoveryPolicy:
    def __init__(self, base_controller, config):
        self.base_controller = base_controller
        self.config = deepcopy(config)
        self.monitor = RecoveryMonitor()
        self.policy_id = config['policy_id']

    def reset_policy_memory(self):
        reset = getattr(self.base_controller, 'reset_policy_memory', None)
        if reset is not None:
            reset()
        self.monitor.reset()

    def decide(self, records):
        request, diagnostic = self.base_controller.decide(records)
        if not records:
            raise ValueError('Current recovery observation history is required')
        self.monitor.observe(records[-1])
        # No request/diagnostic copy or policy option change, including when
        # active. The native controller and its private mailbox remain in charge.
        return request, diagnostic


class RecoveryCommandMixin:
    def _prepare_command_dispatch(self, dispatch, diagnostic, loads):
        native_raw = deepcopy(dispatch)
        value, trace = super()._prepare_command_dispatch(dispatch, diagnostic, loads)
        monitor = self.env.recovery_dispatch_controller.monitor
        if not monitor.active:
            return value, trace
        if monitor.current['time_minute'] != float(self.time):
            raise ValueError('Recovery command generation lacks the same current observation clock')
        selected, receipt = select_ec_permissions(value, monitor.current)
        receipt.update(generated_minute=float(self.time), activation_minute=monitor.activated_minute,
            recovered_supports=deepcopy(monitor.recovered_supports),
            current_support_availability=deepcopy(monitor.current['supports']),
            native_raw_dispatch=native_raw,
            native_raw_dispatch_meaning='Original solver-emitted dispatch before the existing native preparation hooks; not a reconstructed primal',
            arrival_minute_source='unchanged native command_generated event; no future arrival read')
        selected_trace = deepcopy(trace)
        selected_trace['recovery_dispatch_selection'] = receipt
        return selected, selected_trace


def attach_recovery_policy(env, base_controller, config=None):
    """Install only this command hook before native reset; return its controller."""
    selected = deepcopy(R1_CONFIG if config is None else config)
    if selected != R1_CONFIG:
        raise ValueError('Only the complete registered R1 configuration is supported')
    if getattr(env, 'core', None) is not None or hasattr(env, 'recovery_dispatch_controller'):
        raise ValueError('Attach R1 once before the original environment reset')
    controller = RecoveryPolicy(base_controller, selected)
    env.recovery_dispatch_controller = controller
    env.engineering_core_type = type('RecoveryDispatchR1Core',
        (RecoveryCommandMixin, env.engineering_core_type), {})
    original_reset = env.reset

    def reset(self, *, scenario, seed=None):
        controller.reset_policy_memory()
        return original_reset(scenario=scenario, seed=seed)

    env.reset = MethodType(reset, env)
    return controller
