"""SF1 is installed before the native local runtime binds its configuration."""
from __future__ import annotations

from copy import deepcopy
from types import MethodType, SimpleNamespace

from tools.annual_readiness.environment import with_readiness
from .runtime import MODEL_ID, ModelSpec, SingleFaultCoreMixin, validate_single_fault_scenario


def with_single_fault_model(method, *, model_spec=None, **readiness_options):
    spec = ModelSpec() if model_spec is None else model_spec
    if type(spec) is not ModelSpec:
        raise ValueError('SF1 requires an immutable ModelSpec')
    original = with_readiness(method, **readiness_options)

    def create(*args, **kwargs):
        # Canonical constructor arguments are delegated byte-for-byte in value;
        # the explicit opt-in specification is bound only after construction.
        env = original.OperatingHospitalIESEnv(*args, **kwargs)
        env.single_fault_model_spec = spec
        env._single_fault_model_bound_spec = spec
        env.operating_model_config['single_fault_model'] = spec.as_dict()
        env.engineering_core_type = type('SingleFaultCore', (SingleFaultCoreMixin, env.engineering_core_type), {})
        old_reset = env.reset

        def reset(self, *, scenario, seed=None):
            validate_single_fault_scenario(scenario)
            if (self.single_fault_model_spec is not spec
                    or self.operating_model_config.get('single_fault_model') != spec.as_dict()):
                raise ValueError('SF1 bound model changed before native reset')
            returned = old_reset(scenario=scenario, seed=seed)
            self.model_id = MODEL_ID
            self.core._check_single_fault_model()
            observation = self._observation()
            return (observation, returned[1]) if isinstance(returned, tuple) else observation

        env.reset = MethodType(reset, env)
        env.model_id = MODEL_ID
        env.episode_snapshot_function = decision_snapshot
        return env

    return SimpleNamespace(**dict(vars(original), OperatingHospitalIESEnv=create))


def decision_snapshot(env):
    from tools.model_correction_v25.environment import decision_snapshot as original_snapshot
    value = original_snapshot(env)
    value['single_fault_model'] = deepcopy(env.core.single_fault_metadata())
    value['physical_execution_model_id'] = MODEL_ID
    return value
