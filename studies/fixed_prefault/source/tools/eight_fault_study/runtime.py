"""Local calendar clock and source-only actual-witness binding.

This context never reaches the input loader and cannot grant numerical access.
The unchanged native loader and reset retain the original local/sealed support
checks. Only AnnualSourceMixin's final-result hook and causal calendar clock are
inherited; annual data endpoints and annual failure-history behavior are bypassed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MethodType, SimpleNamespace

from tools.annual_program.source_runtime import AnnualSourceMixin
from tools.sci_engineering.design import SEALED_DAYS, allowed_hours, digest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'hospital_ies.eight_fault_study.local_actual_witness_source_context.v1'


class LocalActualCoreMixin(AnnualSourceMixin):
    """Keep the exact actual-result hook and detached causal calendar clock."""
    def data_at(self, minute):
        return super(AnnualSourceMixin, self).data_at(minute)

    def boundary(self):
        return super(AnnualSourceMixin, self).boundary()

    def _record_error(self, *args, **kwargs):
        return super(AnnualSourceMixin, self)._record_error(*args, **kwargs)

    def _annual_error_row_context(self):
        native = getattr(super(AnnualSourceMixin, self), '_annual_error_row_context', None)
        return native() if native is not None else None

    def _annual_absolute_minute(self, minute):
        raise ValueError('Annual source endpoints are unavailable in the local adapter')


def runtime_configuration(case, registry):
    """Resolve the same canonical short-window hardware, rule and policy."""
    from tools.annual_readiness.registration import load_registry, validate_case, worker_registry
    from tools.model_correctness_v23 import policy
    from tools.model_closure_v22.validation import runtime_policy
    from tools.model_correction_v25.validation import resolve_operating_rule
    trusted = load_registry()
    case = validate_case(case, trusted)
    if digest(registry) != digest(worker_registry(case, trusted)):
        raise ValueError('Local worker registry differs from canonical registration')
    config = deepcopy(registry['hardware_profiles'][case['configuration']])
    for key in ('model_closure_v22', 'model_correctness_v23', 'engineering_completion_v24', 'model_correction_v25'):
        config[key] = deepcopy(case.get(key, registry[key]))
    if case['annual_readiness']['method'].endswith('_ready'):
        rule_id, rule = case['operating_rule_id'], dict(name='sustained_critical', design_minutes=60.)
    else:
        rule_id, rule = resolve_operating_rule(registry, case)
    return dict(model_config=config, control_rule_id=rule_id,
        operating_rule=deepcopy(rule), policy=runtime_policy(case, registry, policy))


def _hours(value):
    from numbers import Integral
    values = list(value)
    if (not values or any(isinstance(hour, bool) or not isinstance(hour, Integral)
            or not 1 <= hour <= 8760 for hour in values) or len(values) != len(set(values))):
        raise ValueError('Exact finite local source support required')
    return sorted(int(hour) for hour in values)


def _days(value):
    from numbers import Integral
    values = list(value)
    if (any(isinstance(day, bool) or not isinstance(day, Integral) for day in values)
            or len(values) != len(set(values)) or sorted(values) != list(SEALED_DAYS)):
        raise ValueError('Canonical original twelve sealed days must remain protected')
    return sorted(int(day) for day in values)


def create_source_context(root, *, case, configuration, bindings):
    """Authenticate real BINDINGS before construction; grant no numeric access."""
    from tools.annual_readiness.registration import load_registry, validate_case, worker_registry
    from tools.annual_readiness.runner import _bindings
    root = Path(root).resolve()
    if root != ROOT:
        raise ValueError('Local context must use the loaded canonical producer root')
    registry = load_registry()
    case = validate_case(case, registry)
    expected = runtime_configuration(case, worker_registry(case, registry))
    if configuration != expected:
        raise ValueError('Local configuration differs from canonical case')
    if bindings != _bindings():
        raise ValueError('Local source/input/HEAD BINDINGS differ from current bytes')
    hours = _hours(allowed_hours(dict(scenarios=[case['scenario']])))
    if any((hour-1)//24 in SEALED_DAYS for hour in hours):
        raise ValueError('Local source support overlaps an original sealed final day')
    scene = deepcopy(case['scenario'])
    if scene.get('is_final_blind') or scene.get('original_final12_evaluated'):
        raise ValueError('Local context cannot execute an original final-blind background')
    calendar = scene.pop('model_closure_events', [])
    value = dict(schema=SCHEMA, root=str(root), case=deepcopy(case),
        configuration=deepcopy(configuration), scenario=scene, detached_calendar=calendar,
        allowed_hours=hours, sealed_days=list(SEALED_DAYS), bindings=deepcopy(bindings),
        numeric_access_granted=False, annual_numeric_grant=False,
        calendar_is_online_policy_input=False, original_final12_untouched=True)
    value['signature'] = digest(value)
    return value


@dataclass
class _BoundLocalContext:
    declaration: dict
    env: object
    resolved_model_config: dict
    execution_configuration: dict
    reset_complete: bool = False


def _execution_configuration(env):
    """Copy static parameters, separating exact native derived contexts."""
    result = {name:deepcopy(getattr(env, name, None)) for name in
        ('config', 'sci_config', 'closure_config', 'correctness_config',
         'engineering_completion_config', 'model_correction_config',
         'execution_contract', 'operating_execution_contract')}
    if isinstance(result['config'], dict):
        contexts = ['_model_closure_v22', '_sci_storage_ports']
        if getattr(env, 'operating_rule', {}).get('name') == 'sustained_critical':
            # M22 generate_command caches M25's command-duration context here.
            # Actual solves rebuild it for their own duration/current stock.
            contexts.append('_model_correction_v25_reserve')
        for name in contexts:
            result['config'].pop(name, None)
    return result


def _check_env(env, bound, *, require_reset):
    from tools.model_correction_v25.reserve import operating_rule_config
    value = bound.declaration
    unsigned = deepcopy(value)
    signature = unsigned.pop('signature', None)
    if signature != digest(unsigned) or value.get('schema') != SCHEMA:
        raise ValueError('Local source declaration changed')
    if bound.env is not env or Path(env.root).resolve() != Path(value['root']):
        raise ValueError('Local actual environment/root differs from frozen producer')
    if getattr(env, 'annual_program_data_context', None) is not None:
        raise ValueError('Annual and local source contexts cannot be combined')
    if getattr(env, 'sci_final_release_receipt', None) is not None:
        raise ValueError('Final-blind release cannot accompany a local source context')
    if _hours(env.allowed_hours) != value['allowed_hours'] or _days(env.sealed_days) != value['sealed_days']:
        raise ValueError('Actual local source support/sealed protection differs from frozen case')
    if (env.operating_rule != operating_rule_config(value['configuration']['operating_rule'])
            or env.control_rule_id != value['configuration']['control_rule_id']
            or digest(env.operating_model_config) != digest(bound.resolved_model_config)):
        raise ValueError('Actual local configuration/rule differs from bound runtime')
    if _execution_configuration(env) != bound.execution_configuration:
        raise ValueError('Actual local execution parameters differ from bound runtime')
    if require_reset:
        if not bound.reset_complete:
            raise ValueError('Local actual witness requires a verified native reset')
        if (digest(env.scenario) != digest(value['scenario'])
                or digest(env.model_closure_events) != digest(value['detached_calendar'])):
            raise ValueError('Actual local scene/detached calendar differs from frozen case')
        if getattr(env, 'study_clock_contract', None) != 'event_triggered':
            raise ValueError('Actual local response clock changed')


def resolve_local_source_freeze(env):
    """Verify the actual environment; return a detached source-only freeze map."""
    bound = getattr(env, 'local_actual_witness_source_context', None)
    if not isinstance(bound, _BoundLocalContext):
        raise ValueError('An adapter-bound local actual-witness source context is required')
    _check_env(env, bound, require_reset=True)
    return deepcopy(bound.declaration['bindings']['source'])


def with_local_runtime(original, source_context):
    """Reject invalid contexts before delegating to any numeric constructor."""
    value = deepcopy(source_context)

    def create(root, **kwargs):
        if kwargs.get('annual_program_data_context') is not None:
            raise ValueError('Annual and local source contexts cannot be combined')
        if kwargs.get('sci_final_release_receipt') is not None:
            raise ValueError('Final-blind release cannot accompany a local source context')
        expected = create_source_context(root, case=value['case'],
            configuration=value['configuration'], bindings=value['bindings'])
        if value != expected:
            raise ValueError('Local source freeze differs from current canonical bindings')
        config = value['configuration']
        if (_hours(kwargs['allowed_hours']) != value['allowed_hours']
                or _days(kwargs['sealed_days']) != value['sealed_days']
                or kwargs.get('model_config') != config['model_config']
                or kwargs.get('operating_rule') != config['operating_rule']):
            raise ValueError('Actual local constructor support/configuration differs from source freeze')
        env = original.OperatingHospitalIESEnv(root, **kwargs)
        bound = _BoundLocalContext(deepcopy(value), env, deepcopy(env.operating_model_config),
            _execution_configuration(env))
        env.local_actual_witness_source_context = bound
        _check_env(env, bound, require_reset=False)
        env.engineering_core_type = type('EightFaultLocalCore',
            (LocalActualCoreMixin, env.engineering_core_type), {})
        old_reset = env.reset

        def reset(self, *, scenario, seed=None):
            _check_env(self, bound, require_reset=False)
            if digest(scenario) != digest(value['case']['scenario']):
                raise ValueError('Actual reset scene/calendar differs from frozen local case')
            before = deepcopy(self.operating_model_config)
            execution_before = _execution_configuration(self)
            returned = old_reset(scenario=scenario, seed=seed)
            after = deepcopy(self.operating_model_config)
            # Readiness adds its registered metadata on reset. All previously
            # constructed model fields must retain their actual values.
            before.pop('annual_readiness', None)
            after.pop('annual_readiness', None)
            if before != after:
                raise ValueError('Native reset changed the bound local model configuration')
            # The unchanged SCI reset applies exactly the case's registered
            # initial shaft-equivalent inventory. Other parameters stay fixed.
            if isinstance(execution_before['config'], dict):
                if 'initial_reserve_kWh' in scenario:
                    execution_before['config']['diesel_initial_energy_kWh'] = float(scenario['initial_reserve_kWh'])
            execution_after = _execution_configuration(self)
            if execution_before != execution_after:
                raise ValueError('Native reset changed unregistered local execution parameters')
            bound.resolved_model_config = deepcopy(self.operating_model_config)
            bound.execution_configuration = execution_after
            bound.reset_complete = True
            _check_env(self, bound, require_reset=True)
            return returned

        env.reset = MethodType(reset, env)
        return env

    fields = vars(original).copy()
    fields['OperatingHospitalIESEnv'] = create
    return SimpleNamespace(**fields)
