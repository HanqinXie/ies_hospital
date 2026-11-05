"""Cadence-independent bridge over existing annual, responsive environments.

No local scenario validator is loosened. No native-witness receipt is forged.
The installer refuses the legacy result consumer until its distinct engineering
owner contract is explicitly selected by the integrating runner.
"""
from copy import deepcopy
from functools import partial
import hashlib
import json
import math
from pathlib import Path
from types import MethodType

import numpy as np

from hospital_ies.model import RT_TO_KW, solve_hour
from tools.annual_program.source_runtime import AnnualSourceMixin
from tools.cooling_dynamics_20261104 import CoolingDynamics, default_config
from tools.single_fault_model_20261102.runtime import ModelSpec as StorageSpec, SingleFaultCoreMixin
from tools.single_fault_fuel_20261102.runtime import ModelSpec as FuelSpec, FuelCoreMixin
from tools.single_fault_fuel_20261102.fuel import FuelCurve

from . import MODEL_ID, OWNER_SCHEMA

METHODS = frozenset(('M_available', 'M_ready', 'C_ready'))
STATE_SCHEMA = 'hospital_ies.annual_dynamic.cooling_state.v1'
CAUSE_SCHEMA = 'hospital_ies.annual_dynamic.current_cause.v1'
_DYNAMIC_KEYS = frozenset(('config', '_taus', '_tower_tau', '_pump_tau', '_q',
                         '_tower_ready', '_pump_ready', '_elapsed', '_delivered_kWh'))


class IntegrationNotReady(RuntimeError):
    """The caller has not integrated an honest result owner/annual consumer."""


def digest(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')
    except (ValueError, TypeError) as exc:
        raise ValueError('Finite JSON state required') from exc
    return hashlib.sha256(encoded).hexdigest()


def number(value, name, *, upper=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0.:
        raise ValueError('Finite nonnegative ' + name + ' required')
    if upper is not None and value > upper:
        raise ValueError(name + ' exceeds its registered domain')
    return float(value)


def vector(values, name):
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise ValueError('Four ' + name + ' values required')
    return [number(v, name) for v in values]


def checked_calendar(events):
    if type(events) is not list:
        raise ValueError('Explicit declared calendar list required')
    result = deepcopy(events); previous = -1.
    for event in result:
        if type(event) is not dict or set(event) != {'time_minute', 'availability'}:
            raise ValueError('Exact declared event fields required')
        minute = number(event['time_minute'], 'event minute')
        values = event['availability']
        if minute <= previous or type(values) is not dict or not values:
            raise ValueError('Strictly ordered nonempty availability events required')
        for key, value in values.items():
            if type(key) is not str or not key:
                raise ValueError('Named declared component required')
            number(value, 'availability', upper=1.)
        previous = minute
    return result


def lower_calendar(events):
    """Series branch interlock, with raw EC1 health independent of its pump."""
    result = []; compressor = branch = 1.
    for event in checked_calendar(events):
        values = deepcopy(event['availability'])
        changed = 'EC_1' in values or 'EC_1_PRIMARY_PUMP' in values
        compressor = values.get('EC_1', compressor)
        branch = values.pop('EC_1_PRIMARY_PUMP', branch)
        if changed:
            values['EC_1'] = compressor * branch
        result.append(dict(time_minute=float(event['time_minute']), availability=values))
    return result


def current_cause_ledger(events, minute, functional_hardware):
    """Export current declared causes only; never export the future calendar."""
    now = number(minute, 'current observation minute')
    functional = {k: number(v, 'functional availability', upper=1.) for k, v in functional_hardware.items()}
    raw = dict(functional); declared = {}
    for event in checked_calendar(events):
        if event['time_minute'] > now:
            break
        declared.update(event['availability'])
    branch = declared.get('EC_1_PRIMARY_PUMP', 1.)
    compressor = declared.get('EC_1', 1.)
    # The branch is a fixed declared component, including in the normal case.
    # Its field set must never depend on whether a future script faults it.
    if functional.get('EC_1', 1.) != compressor * branch:
        raise ValueError('Current functional EC1 differs from raw compressor/branch interlock')
    raw.update(EC_1=compressor, EC_1_PRIMARY_PUMP=branch)
    return dict(schema=CAUSE_SCHEMA, observation_time_minute=now, available_time_minute=now,
        raw_component_availability=raw, effective_functional_availability=functional,
        active_declared_causes=sorted(k for k, v in declared.items() if v < 1.),
        branch_lowering='effective_EC1=raw_EC1*raw_EC1_PRIMARY_PUMP',
        raw_compressor_damage_inferred=False, hydraulic_measurement_claimed=False)


class CoolingBridgeState:
    """Own the exact accepted dynamic state; previews cannot mutate it."""
    def __init__(self, *, initial_cooling_kW, initial_availability, config=None):
        selected = deepcopy(default_config() if config is None else config)
        if selected != default_config():
            raise ValueError('Only the unchanged accepted cooling configuration is registered')
        self.dynamic = CoolingDynamics(selected, vector(initial_cooling_kW, 'initial cooling'), initial_availability)
        self.committed_until_minute = 0.

    def preview(self, dt_h, target, raw):
        hours = number(dt_h, 'actual interval hours')
        if hours <= 0. or hours * 60. > 1. + 1e-8:
            raise ValueError('Positive physical interval of at most one minute required')
        target = vector(target, 'attainable cooling reference')
        p = self.dynamic.preview(hours * 60., target, raw)
        effective = dict(raw)
        effective['TOWER'] = raw.get('TOWER', 1.) * p['tower_mean_factor']
        effective['AUX_CHW_PUMP'] = raw.get('AUX_CHW_PUMP', 1.) * p['pump_mean_factor']
        return target, p, effective

    def commit(self, start, end, actual, target, raw, *, support_running=None):
        start = number(start, 'commit start'); end = number(end, 'commit end')
        if start != self.committed_until_minute or end <= start or end-start > 1.+1e-8:
            raise ValueError('Actual dynamic commit continuity/once-only contract failed')
        if abs(self.dynamic.observe()['elapsed_minutes'] - start) > 1e-8:
            raise ValueError('Dynamic and physical commit clock differ')
        receipt = self.dynamic.commit_actual(vector(actual, 'actual cooling'), end-start,
            vector(target, 'reference cooling'), raw, support_running=support_running)
        self.committed_until_minute = end
        return receipt

    def to_state(self):
        value = dict(schema=STATE_SCHEMA, model_id=MODEL_ID,
            committed_until_minute=self.committed_until_minute, dynamic=deepcopy(vars(self.dynamic)))
        return dict(value, content_sha256=digest(value))

    @classmethod
    def from_state(cls, document, *, expected_minute):
        value = deepcopy(document)
        if type(value) is not dict:
            raise ValueError('Complete dynamic state required')
        checksum = value.pop('content_sha256', None)
        if set(value) != {'schema', 'model_id', 'committed_until_minute', 'dynamic'} or digest(value) != checksum:
            raise ValueError('Dynamic state integrity/coverage mismatch')
        if value['schema'] != STATE_SCHEMA or value['model_id'] != MODEL_ID:
            raise ValueError('Dynamic model identity mismatch')
        raw = value['dynamic']
        if type(raw) is not dict or set(raw) != _DYNAMIC_KEYS:
            raise ValueError('Complete dynamic field coverage required')
        if raw['config'] != default_config():
            raise ValueError('Accepted dynamic configuration changed')
        for key in ('_q', '_delivered_kWh'):
            vector(raw[key], key)
        for key in ('_tower_ready', '_pump_ready'):
            number(raw[key], key, upper=1.)
        minute = number(expected_minute, 'expected physical clock')
        if abs(number(raw['_elapsed'], 'dynamic clock') - minute) > 1e-8 or value['committed_until_minute'] != minute:
            raise ValueError('Restored dynamic clock differs from actual physical clock')
        obj = cls(initial_cooling_kW=raw['_q'], initial_availability={}, config=raw['config'])
        if any(raw[key] != vars(obj.dynamic)[key] for key in ('_taus', '_tower_tau', '_pump_tau')):
            raise ValueError('Restored time constants differ from accepted configuration')
        vars(obj.dynamic).update(deepcopy(raw)); obj.committed_until_minute = minute
        return obj


def cooling_vector(action):
    return vector([action.get('absorption_cooling_kW', 0.),
                   *action.get('electric_chiller_cooling_kW', [0., 0., 0.])], 'cooling action')


def extend_current_observation(env, base_record):
    """Append current-only telemetry to an already legal native observation."""
    core = env.core; now = number(core.time, 'current physical clock')
    if base_record.get('decision_time_min') != now:
        raise ValueError('Annual native observation and physical clock differ')
    record = deepcopy(base_record)
    dynamic = core.annual_cooling_bridge.dynamic.observe()
    if abs(dynamic['elapsed_minutes']-now) > 1e-8:
        raise ValueError('Current dynamic observation clock differs')
    record['cooling_dynamics'] = dict(dynamic, observation_time_minute=now, available_time_minute=now)
    record['annual_dynamic_causal_ledger'] = current_cause_ledger(env.annual_dynamic_declared_calendar,
        now, core._hardware_data_at(now)[-1])
    record['annual_dynamic_execution_model_id'] = MODEL_ID
    return record


class ResponsiveCoolingSolveMixin:
    """SF2 _solve must precede this hook; no accepted solver method is replaced."""
    def _solve(self, loads, f, dt_h, phase, action, **kwargs):
        result = super()._solve(loads, f, dt_h, phase, action, **kwargs)
        bridge = getattr(self, 'annual_cooling_bridge', None)
        if bridge is None:
            return result  # Original immutable initialization, before dynamic installation.
        p = self.env.plant
        caps = np.asarray([p.absorption_rt, *p.electric_chillers_rt], float) * RT_TO_KW
        av = np.asarray([f.get('ABS', 1.), *[f.get(f'EC_{i}', 1.) for i in (1,2,3)]])
        target = np.minimum(cooling_vector(result['action']), caps*av).tolist()
        target, preview, effective = bridge.preview(dt_h, target, f)
        original = kwargs.get('execution_action', self.executed)
        fixed = deepcopy(original)
        fixed['absorption_cooling_kW'] = min(float(fixed['absorption_cooling_kW']), preview['mean_cooling_limits_kW'][0])
        fixed['electric_chiller_cooling_kW'] = np.minimum(fixed['electric_chiller_cooling_kW'], preview['mean_cooling_limits_kW'][1:]).tolist()
        changed = (not np.allclose(cooling_vector(fixed), cooling_vector(original), rtol=0., atol=1e-7) or effective != f)
        if changed:
            result = super()._solve(loads, effective, dt_h, phase, action, **dict(kwargs, execution_action=fixed))
        result['_annual_dynamic_target_kW'] = target
        result['_annual_dynamic_raw_availability'] = deepcopy(f)
        result['_annual_dynamic_preview'] = preview
        return result


class SkipLegacyNativeWitnessMixin:
    def _after_actual_segment(self, segment, result):
        # Preserve the old annual source clocks/delay methods, while explicitly
        # bypassing only its incompatible native-witness ownership exporter.
        return super(AnnualSourceMixin, self)._after_actual_segment(segment, result)


class AnnualBridgeCommitMixin:
    def __init__(self, env, initial_action):
        self.annual_cooling_bridge = None
        super().__init__(env, initial_action)
        self.annual_cooling_bridge = CoolingBridgeState(initial_cooling_kW=cooling_vector(initial_action),
            initial_availability=self.data_at(self.time)[-1], config=env.annual_dynamic_config)

    def _after_actual_segment(self, segment, result):
        if segment['start_minute'] != self.annual_cooling_bridge.committed_until_minute:
            raise ValueError('Actual dynamic commit continuity/once-only contract failed before physical commit')
        super()._after_actual_segment(segment, result)
        if segment.get('sf2_running_fuel', {}).get('accounting_committed') is not True:
            raise IntegrationNotReady('Accepted SF2 physical path receipt is missing at actual commit')
        raw = result['_annual_dynamic_raw_availability']; a = segment['action']
        support = dict(tower=bool(a.get('tower_rejection_kW', 0.) > 1e-9),
            pump=bool(sum(cooling_vector(a)) + a.get('cold_discharge_kW', 0.) + a.get('cold_charge_kW', 0.) > 1e-9))
        segment['cooling_dynamics'] = self.annual_cooling_bridge.commit(segment['start_minute'],
            segment['end_minute'], cooling_vector(a), result['_annual_dynamic_target_kW'], raw,
            support_running=support)
        segment['annual_dynamic_causal_ledger'] = current_cause_ledger(self.env.annual_dynamic_declared_calendar,
            segment['start_minute'], raw)
        plant = self.env.plant
        abs_steam = a['absorption_cooling_kW']/RT_TO_KW*plant.abs_steam_lb_per_rt
        heat_steam = float(np.asarray(result['served'])[:,1].sum())/(plant.steam_kWh_per_lb*plant.exchanger_eff)
        segment['unused_steam_lbph'] = max(0., a['hrsg_steam_lbph'] + a['boiler_steam_lbph'] - abs_steam - heat_steam)
        if 'annual_program_actual_witness' in segment:
            raise IntegrationNotReady('Legacy native owner must not authenticate the annual dynamic bridge')
        segment['annual_dynamic_actual_owner'] = dict(schema=OWNER_SCHEMA, model_id=MODEL_ID,
            result_owner='accepted_native_optimizer_with_dynamic_limits',
            accepted_sf2_path_receipt_present=bool(segment.get('sf2_running_fuel', {}).get('accounting_committed')),
            legacy_annual_native_witness_claimed=False, dynamic_commit_count=1)
        profile=getattr(self.env,'engineering_numerical_profile',None)
        if profile is not None:
            receipt=segment['sf2_running_fuel']['native_result_owner']
            segment['annual_dynamic_actual_owner'].update(
                result_owner='engineering_solver_with_current_domain_validation',
                numerical_profile=profile['profile'], optimizer_called=receipt['optimizer_called'],
                engineering_acceptance=deepcopy(receipt['engineering_acceptance']),
                native_optimal_status_claimed=receipt['native_optimal_status_claimed'])


def _verify_sources(root, source_freeze):
    required = ('configs/plant_configs.json', 'src/hospital_ies/model.py',
        'tools/single_fault_model_20261102/storage.py', 'tools/single_fault_model_20261102/runtime.py',
        'tools/single_fault_fuel_20261102/fuel.py', 'tools/single_fault_fuel_20261102/runtime.py',
        'tools/cooling_dynamics_20261104/dynamics.py', 'tools/annual_program/source_runtime.py')
    for rel in required:
        path = Path(root)/rel
        if not path.is_file() or source_freeze.get(rel) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise IntegrationNotReady('Source freeze missing or changed: ' + rel)


def install_into_env(env, *, method, declared_calendar, source_freeze):
    """Install into a constructed, unreset annual environment selected by caller.

    The caller must have frozen a native CASE containing lower_calendar(declared)
    through the normal annual input loader, and select the new owner consumer.
    This installer neither changes the CASE nor grants source/input access.
    """
    if method not in METHODS:
        raise ValueError('Only the three registered responsive annual methods are supported')
    if getattr(env, 'annual_dynamic_owner_contract', None) != OWNER_SCHEMA:
        raise IntegrationNotReady('Explicit new engineering owner consumer required; legacy native owner is unsupported')
    if getattr(env, 'core', None) is not None or hasattr(env, 'annual_dynamic_config'):
        raise ValueError('Install once before the original annual reset')
    if not issubclass(env.engineering_core_type, AnnualSourceMixin):
        raise IntegrationNotReady('Existing AnnualSourceMixin clock/data capability required')
    _verify_sources(env.root, source_freeze)
    declared = checked_calendar(declared_calendar); native = lower_calendar(declared)
    storage = StorageSpec(); fuel = FuelSpec(); curve = FuelCurve.from_source(env.root, env.plant)
    env.single_fault_model_spec = env._single_fault_model_bound_spec = storage
    env.single_fault_fuel_spec = env._single_fault_fuel_bound_spec = fuel
    env.single_fault_fuel_curve = env._single_fault_fuel_bound_curve = curve
    solver = partial(solve_hour, sf2_fuel_config=curve.as_dict())
    env.reference_initialization_solver = env._sf2_bound_reference_solver = solver
    env.config.update(_sf2_fuel_curve=curve.as_dict(), _sf2_nonactual_fuel_role='point_probe',
        _sf2_free_fuel_role='free_reference_probe', strict_emergency_source_partition=True)
    env.annual_dynamic_config = default_config()
    env.annual_dynamic_declared_calendar = declared
    env.annual_dynamic_source_freeze = deepcopy(source_freeze)
    env.operating_model_config.update(single_fault_model=storage.as_dict(), single_fault_fuel_model=fuel.as_dict(),
        single_fault_fuel_curve=curve.as_dict(), annual_dynamic_model=MODEL_ID,
        cooling_dynamics=deepcopy(env.annual_dynamic_config), strict_emergency_source_partition=True)
    env.engineering_core_type = type('AnnualResponsiveDynamicCore', (AnnualBridgeCommitMixin,
        FuelCoreMixin, ResponsiveCoolingSolveMixin, SingleFaultCoreMixin,
        SkipLegacyNativeWitnessMixin, env.engineering_core_type), {})
    old_reset = env.reset

    def reset(self, *, scenario, seed=None):
        _verify_sources(self.root, self.annual_dynamic_source_freeze)
        if scenario.get('model_closure_events', []) != native:
            raise IntegrationNotReady('Actual annual native calendar differs from declared lowering')
        returned = old_reset(scenario=scenario, seed=seed)
        if self.model_closure_events != native:
            raise IntegrationNotReady('Detached annual native calendar differs from declared lowering')
        self.core._check_single_fault_model(); self.core._check_sf2_model()
        return returned

    env.reset = MethodType(reset, env)
    return env
