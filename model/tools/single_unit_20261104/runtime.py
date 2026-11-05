"""Explicit declaration-to-functional scene adapter; no new physical transition."""
from copy import deepcopy
import math
from types import MethodType

from tools.cooling_dynamics_20261104.run_worker import build_runtime as _native_build_runtime
from tools.sci_engineering.design import digest

from . import MODEL_ID
from .registration import validate_case

CAUSAL_SCHEMA = 'hospital_ies.single_unit.current_cause.v1'


def causal_ledger(case, minute, hardware):
    """Current-only causal meaning of the native functional availability map.

    The separately authenticated declaration is the raw-component evidence.
    Native hardware_availability includes the derived functional interlock and
    must not be relabelled as compressor damage or measured branch hydraulics.
    """
    if isinstance(minute, bool) or not math.isfinite(float(minute)) or minute < 0.:
        raise ValueError('Finite current causal observation minute required')
    meta = case['unit_study']; target = meta['declared_target']; available = 1.
    for event in case['scenario']['model_closure_events']:
        if event['time_minute'] > minute:
            break
        available = float(event['availability'][target])
    functional = {key: float(value) for key, value in hardware.items()}
    if any(not math.isfinite(v) or not 0. <= v <= 1. for v in functional.values()):
        raise ValueError('Invalid current functional availability')
    raw = dict(functional)
    native_target = meta['native_functional_target']
    if target is not None and functional.get(native_target, 1.) != available:
        raise ValueError('Native functional state differs from the sole declared cause')
    if functional.get('AUX_CHW_PUMP', 1.) != 1. or functional.get('TOWER', 1.) != 1.:
        raise ValueError('Single-unit case unexpectedly changes a common cooling support')
    for key in ('EC_2', 'EC_3', 'TOWER_2', 'TOWER_3', 'TOWER_4'):
        if functional.get(key, 1.) != 1.:
            raise ValueError('Single-unit case unexpectedly changes another cooling unit: ' + key)
    if meta['kind'] == 'primary_pump':
        raw.update(EC_1=1., EC_1_PRIMARY_PUMP=available)
    return dict(schema=CAUSAL_SCHEMA, model_id=MODEL_ID, kind=meta['kind'],
        original_topic=meta['original_topic'], current_time_minute=float(minute),
        available_time_minute=float(minute), sole_exogenous_cause=target,
        raw_component_availability=raw, effective_functional_availability=functional,
        current_cause_active=bool(target is not None and available < 1.),
        native_availability_semantics=meta['native_calendar_semantics'],
        common_secondary_distribution_and_storage_path_faulted=False,
        compressor_hardware_faulted=False, hydraulic_flow_measured=False,
        declared_scene_sha256=meta['declared_scene_sha256'],
        native_scene_sha256=meta['native_scene_sha256'])


class SingleUnitLedgerMixin:
    """Add a supplementary receipt after exactly one unchanged native commit."""
    def _after_actual_segment(self, segment, result):
        super()._after_actual_segment(segment, result)
        segment['single_unit_causal_ledger'] = causal_ledger(self.env.single_unit_case,
            segment['start_minute'], result['_cooling_raw_availability'])


def install_adapter(env, case):
    """Bind one exact declared reset to its separately authenticated native scene."""
    registered = validate_case(case)
    env.single_unit_case = deepcopy(registered)
    bound_digest = digest(registered)
    env.engineering_core_type = type('SingleUnitCausalLedgerCore',
        (SingleUnitLedgerMixin, env.engineering_core_type), {})
    old_reset = env.reset

    def check(self):
        if digest(self.single_unit_case) != bound_digest:
            raise ValueError('Bound single-unit declaration/lowering changed during execution')

    def reset(self, *, scenario, seed=None):
        check(self)
        if digest(scenario) != registered['unit_study']['declared_scene_sha256']:
            raise ValueError('Reset accepts only the exact registered declared single-unit scene')
        native = deepcopy(registered['unit_study']['native_scene'])
        if digest(native) != registered['unit_study']['native_scene_sha256']:
            raise ValueError('Authenticated native functional scene changed')
        result = old_reset(scenario=native, seed=seed)
        check(self)
        # The accepted native reset installs its own snapshot function. Keep
        # that full source/physical snapshot and add only current causal state.
        native_snapshot = self.episode_snapshot_function
        def snapshot(current):
            check(current)
            value = native_snapshot(current)
            now = current.core.time
            value['single_unit_causal_ledger'] = causal_ledger(registered, now,
                current.core._hardware_data_at(now)[-1])
            return value
        self.episode_snapshot_function = snapshot
        return result

    env.reset = MethodType(reset, env)
    return env


def build_runtime(case, source_freeze, cooling_config):
    """Construct the unchanged canonical base, then bind an explicit lowering."""
    registered = validate_case(case)
    env, controller, resolved = _native_build_runtime(registered['unit_study']['base_case'],
                                                     source_freeze, cooling_config)
    install_adapter(env, registered)
    resolved['single_unit_study'] = deepcopy(registered['unit_study'])
    resolved['single_unit_study'].pop('base_case')
    resolved['single_unit_study']['native_physical_functions_unchanged'] = True
    resolved['single_unit_study']['native_hardware_availability_is_functional'] = True
    return env, controller, resolved
