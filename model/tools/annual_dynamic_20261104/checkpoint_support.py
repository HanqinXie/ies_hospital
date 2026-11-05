"""Explicit integration aids; no reset, observe, solver or runtime constructor."""
from functools import partial

from hospital_ies.model import solve_hour
from tools.cooling_dynamics_20261104 import CoolingDynamics, default_config
from tools.single_fault_fuel_20261102.fuel import FuelCurve

from .bridge import CoolingBridgeState, IntegrationNotReady, _verify_sources, digest


REBUILD_ENV_FIELDS = frozenset(('single_fault_model_spec', '_single_fault_model_bound_spec',
    'single_fault_fuel_spec', '_single_fault_fuel_bound_spec', 'single_fault_fuel_curve',
    '_single_fault_fuel_bound_curve', 'reference_initialization_solver', '_sf2_bound_reference_solver'))
ENV_ALIASES = {'_single_fault_model_bound_spec': 'single_fault_model_spec',
    '_single_fault_fuel_bound_spec': 'single_fault_fuel_spec',
    '_single_fault_fuel_bound_curve': 'single_fault_fuel_curve',
    '_sf2_bound_reference_solver': 'reference_initialization_solver'}


def physical_classes():
    """Add these exact classes to the NEW annual checkpoint whitelist only."""
    return frozenset(c.__module__+'.'+c.__qualname__ for c in (CoolingBridgeState, CoolingDynamics))


def capture_bridge_state(core):
    value = core.annual_cooling_bridge.to_state()
    CoolingBridgeState.from_state(value, expected_minute=core.time)
    return value


def restore_bridge_state(core, document):
    """Attach decoded dynamic state to an already restored physical core."""
    restored = CoolingBridgeState.from_state(document, expected_minute=core.time)
    core.annual_cooling_bridge = restored
    return restored


def reference_solver_fingerprint(value):
    """A bounded partial handler for the integrating checkpoint fingerprint."""
    if type(value) is not partial or value.func is not solve_hour or value.args != ():
        raise ValueError('Only the source-bound SF2 reference solve_hour partial is supported')
    if type(value.keywords) is not dict or set(value.keywords) != {'sf2_fuel_config'}:
        raise ValueError('Exact SF2 reference keyword contract required')
    curve = FuelCurve.from_config(value.keywords['sf2_fuel_config'])
    return dict(schema='hospital_ies.annual_dynamic.reference_partial.v1',
        function=solve_hour.__module__+'.'+solve_hour.__qualname__,
        args=[], sf2_fuel_curve_identity=curve.identity, sf2_fuel_config=curve.as_dict())


def validate_restored_core(core):
    """Validate the fully restored core without querying a terminal source row."""
    env = core.env
    if env.annual_dynamic_config != default_config():
        raise ValueError('Restored annual dynamic configuration changed')
    _verify_sources(env.root, env.annual_dynamic_source_freeze)
    if (env.single_fault_model_spec is not env._single_fault_model_bound_spec
            or env.single_fault_fuel_spec is not env._single_fault_fuel_bound_spec
            or env.single_fault_fuel_curve is not env._single_fault_fuel_bound_curve
            or env.reference_initialization_solver is not env._sf2_bound_reference_solver):
        raise ValueError('Restored SF1/SF2 immutable aliases differ')
    reference_solver_fingerprint(env.reference_initialization_solver)
    core._check_single_fault_model(); core._check_sf2_model()
    capture_bridge_state(core)
    return dict(schema='hospital_ies.annual_dynamic.restored_component_check.v1',
        physical_clock=core.time, dynamic_state_sha256=digest(capture_bridge_state(core)),
        input_source_query_performed=False, reset_performed=False,
        scope='SF1/SF2 typed invariants and dynamic state only; journal/queue/full-state admission remains caller responsibility')
