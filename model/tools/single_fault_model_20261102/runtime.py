"""Explicit SF1 physical revision; historical factories never select it."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import math
from numbers import Real

from hospital_ies.state_transition import EPS
from tools.sci_engineering.storage import FiniteStore
from .storage import ContinuousLossStore

MODEL_ID = 'SF1_single_fault_model_20261102'
PHYSICAL_REVISION = 'single_fault.physical_revision.sf1.v1'
INTEGRATION_CONTRACT = ContinuousLossStore.integration_contract


@dataclass(frozen=True)
class ModelSpec:
    physical_model_id: str = MODEL_ID
    physical_revision: str = PHYSICAL_REVISION
    storage_integration_contract: str = INTEGRATION_CONTRACT
    physical_max_segment_minutes: float = 1.

    def __post_init__(self):
        if (self.physical_model_id != MODEL_ID or self.physical_revision != PHYSICAL_REVISION
                or self.storage_integration_contract != INTEGRATION_CONTRACT):
            raise ValueError('SF1 requires the explicit loaded physical version and integration contract')
        value = self.physical_max_segment_minutes
        if isinstance(value, bool) or not isinstance(value, Real) or value not in (1., .5):
            raise ValueError('SF1 physical segment must be 1 or 0.5 minutes; decision grid stays 15 minutes')

    def as_dict(self):
        return asdict(self)


def validate_single_fault_scenario(scene):
    """Additional scope restriction, never a replacement for canonical validation."""
    minutes = scene.get('step_minutes', 15.)
    if (isinstance(minutes, bool) or not isinstance(minutes, Real)
            or not math.isfinite(minutes) or minutes != 15.):
        raise ValueError('SF1 requires the unchanged 15 minute control interval')

    def positive_amplitude(value):
        if isinstance(value, dict):
            return any(positive_amplitude(item) for item in value.values())
        return isinstance(value, Real) and value > 0.

    if any(positive_amplitude(scene.get(name, {})) for name in
            ('shock', 'shock_by_priority', 'shock_by_zone', 'demand_amplitude')):
        raise ValueError('SF1 forbids additional positive external demand shock')
    base = {name for group in ('faults', 'auxiliary_faults')
            for name, value in scene.get(group, {}).items() if value < 1.}
    events = scene.get('model_closure_events', [])
    targets = base | {name for event in events for name in event['availability']}
    if len(targets) > 1 or (base and events):
        raise ValueError('SF1 permits only one exogenous fault target and its repair calendar')
    down = repaired = False
    for event in events:
        for value in event['availability'].values():
            if value < 1.:
                if down or repaired:
                    raise ValueError('SF1 permits one down/repair cycle; a second fault is out of scope')
                down = True
            elif not down or repaired:
                raise ValueError('SF1 repair requires the same single prior fault target')
            else:
                repaired = True


class ArrivedStopBeforeReadyMixin:
    """Cancel only not-yet-online starts before inherited ready processing."""
    def boundary(self):
        due = [c for c in self.pending_commands if c['arrival_minute'] <= self.time+EPS]
        latest = max(due, key=lambda c: (c['generated_minute'], c['id'])) if due else None
        if latest is not None:
            for index, power in enumerate(self.executed['gt_electric_kW']):
                if (power <= EPS and index in self.gt_start_pending
                        and latest['dispatch']['gt_electric_kW'][index] <= EPS):
                    pending = self.gt_start_pending.pop(index)
                    self.log('sf1_pending_start_cancelled_by_arrived_stop', device=f'GT_{index+1}',
                        command_id=latest['id'], generated_minute=latest['generated_minute'],
                        arrival_minute=latest['arrival_minute'], cancelled_start=deepcopy(pending),
                        actual_output_kW=float(power), startup_resources_consumed=False,
                        resource_scope='No resource consumed by this cancellation; earlier actual intervals retained')
        return super().boundary()


# Capture loaded methods once. Replacing an instance or class method is a model
# change and must fail rather than quietly use the historical integration.
_STORE_METHODS = {name:getattr(ContinuousLossStore, name) for name in
    ('_quantity', '_inputs', 'flow_hours', 'project_energy', 'limits', 'boundary_hours', 'commit', 'observe')}
_BASE_STORE_METHODS = {name:getattr(FiniteStore, name) for name in
    ('project_energy', 'limits', 'boundary_hours', 'commit', 'observe')}


class SingleFaultCoreMixin(ArrivedStopBeforeReadyMixin):
    def __init__(self, env, initial_action):
        super().__init__(env, initial_action)
        self._single_fault_spec_value = env.single_fault_model_spec.as_dict()
        self._single_fault_store_config = {}
        for name in ('ups', 'cold_store'):
            old = getattr(self, name)
            replacement = ContinuousLossStore(deepcopy(old.config))
            replacement.energy_kWh = old.energy_kWh
            replacement.initial_charged_input_kWh = old.initial_charged_input_kWh
            setattr(self, name, replacement)
            self._single_fault_store_config[name] = deepcopy(old.config)
        self._check_single_fault_model()
        self.log('sf1_physical_model_installed', **self.single_fault_metadata(),
                 inventory_reset=False, inherited_preparation_retained=True)

    def _check_single_fault_model(self):
        spec = self.env.single_fault_model_spec
        if (type(spec) is not ModelSpec or spec is not self.env._single_fault_model_bound_spec
                or spec.as_dict() != self._single_fault_spec_value
                or self.env.operating_model_config.get('single_fault_model') != self._single_fault_spec_value):
            raise ValueError('SF1 frozen physical model specification changed')
        for name in ('ups', 'cold_store'):
            store = getattr(self, name)
            if (type(store) is not ContinuousLossStore or store.config != self._single_fault_store_config[name]
                    or store.integration_contract != INTEGRATION_CONTRACT):
                raise ValueError('SF1 actual storage class/configuration/integration changed')
            for method, loaded in _STORE_METHODS.items():
                actual = getattr(store, method)
                if getattr(actual, '__func__', actual) is not loaded:
                    raise ValueError('SF1 loaded storage method changed: '+method)
        if any(getattr(FiniteStore, name) is not loaded for name, loaded in _BASE_STORE_METHODS.items()):
            raise ValueError('SF1 loaded inherited storage method changed')

    def single_fault_metadata(self):
        self._check_single_fault_model()
        return dict(self._single_fault_spec_value, actual_storage_integration={
            name:getattr(self, name).integration_contract for name in ('ups', 'cold_store')})

    def boundary(self):
        self._check_single_fault_model()
        return super().boundary()

    def _next_boundary(self, end):
        self._check_single_fault_model()
        return min(super()._next_boundary(end), self.time+self._single_fault_spec_value['physical_max_segment_minutes'])

    def _solve(self, *args, **kwargs):
        self._check_single_fault_model()
        return super()._solve(*args, **kwargs)

    def observe(self):
        self._check_single_fault_model()
        value = super().observe()
        value['single_fault_model'] = self.single_fault_metadata()
        return value

    def _after_actual_segment(self, segment, result):
        self._check_single_fault_model()
        super()._after_actual_segment(segment, result)
        segment['single_fault_model'] = self.single_fault_metadata()
