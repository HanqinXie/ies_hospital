"""SF1 opt-in regressions; the first RED uses inherited real GT execution."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def api():
    assert (ROOT/'tools/single_fault_model_20261102/runtime.py').exists(), 'SF1 explicit runtime missing'
    return importlib.import_module('tools.single_fault_model_20261102.runtime')


def boundary_core(*, install=True):
    spec = importlib.util.spec_from_file_location('sf_gt_fixture', ROOT/'tests/test_engineering_dynamics.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    x = fixture.core()
    if install and (ROOT/'tools/single_fault_model_20261102/runtime.py').exists():
        x.__class__ = type('SFBoundary', (api().ArrivedStopBeforeReadyMixin, type(x)), {})
    x.env.scenario.update(duration_steps=4, step_minutes=15., stage=1,
                          fault_duration_minutes=0., shock_duration_minutes=0.)
    x.time = 12.
    x._sync_connections = lambda loads: None
    x._update_support_states = lambda availability: None
    x.grid_outage_start = x.last_availability = x.last_shock_active = None
    x.auto_shed_latched = False
    x.pending_shed = x.local_down = x.active_command = None
    x.state.disconnected_loads = np.zeros((4, 3))
    x.automatic_disconnected = np.zeros((4, 3))
    x.disconnected_fraction = np.zeros((4, 3))
    x.automatic_fraction = np.zeros((4, 3))
    x.aux = lambda dispatch: 0.
    x.gt_start_pending = {0:dict(started=0., ready=12., requested=180., start_event_recorded=True)}
    x.pending_commands = [command(x, 2, 0., 12., 0.)]
    return x


def command(x, identifier, generated, arrival, power):
    dispatch = deepcopy(x.executed)
    dispatch['gt_electric_kW'][0] = power
    return dict(id=identifier, generated_minute=generated, arrival_minute=arrival,
                dispatch=dispatch, disconnected=np.zeros((4, 3)).tolist(), reference_cooling_kW=0.)


def legal_delayed_start_stop_core(*, install=True):
    """Real inherited clamp/ledger with a 15-minute command and decision grid."""
    x = boundary_core(install=install)
    x.time = 15.
    x.gt_start_pending = {}
    x.pending_commands = [command(x, 1, 0., 15., 225.)]
    x.boundary()
    assert x.active_command['id'] == 1
    assert x.gt_start_pending[0]['started'] == 15.
    assert x.gt_start_pending[0]['ready'] == 30.
    assert x.executed['gt_electric_kW'][0] == 0.
    x.pending_commands = [command(x, 2, 15., 30., 0.)]
    x.time = 30.
    return x


def assert_legal_delayed_stop_cancels_without_actual_start(x):
    x.boundary()
    assert x.executed['gt_electric_kW'][0] == 0., repr(x.events)
    assert not any(e['kind'] == 'gt_actual_started' for e in x.events)
    assert x.active_command['id'] == 2 and not x.gt_start_pending
    assert x.gt_clocks.running.tolist() == [False, False, False]


def test_legal_fifteen_minute_queue_same_time_stop_cancels_pending_start():
    assert_legal_delayed_stop_cancels_without_actual_start(legal_delayed_start_stop_core())


def test_legal_future_stop_command_does_not_cancel_ready_start():
    x = legal_delayed_start_stop_core()
    x.pending_commands[0].update(generated_minute=30., arrival_minute=45.)
    x.boundary()
    assert x.executed['gt_electric_kW'][0] == 225.
    assert any(e['kind'] == 'gt_actual_started' for e in x.events)
    assert x.pending_commands[0]['arrival_minute'] == 45.


def test_same_time_arrived_stop_cancels_before_real_gt_actual_start():
    x = boundary_core()
    pending = deepcopy(x.gt_start_pending[0])
    x.boundary()
    assert x.executed['gt_electric_kW'][0] == 0., repr(x.events)
    assert not any(e['kind'] == 'gt_actual_started' for e in x.events)
    assert not x.gt_clocks.running[0] and not x.gt_start_pending
    event = next(e for e in x.events if e['kind'] == 'sf1_pending_start_cancelled_by_arrived_stop')
    assert event['command_id'] == 2 and event['arrival_minute'] == 12.
    assert event['cancelled_start'] == pending and event['startup_resources_consumed'] is False


def test_future_stop_does_not_cancel_ready_start():
    x = boundary_core()
    x.pending_commands[0]['arrival_minute'] = 12.5
    x.boundary()
    assert x.executed['gt_electric_kW'][0] == 180.
    assert any(e['kind'] == 'gt_actual_started' for e in x.events)
    assert len(x.pending_commands) == 1


@pytest.mark.parametrize('new_generated,new_id', [(1., 1), (0., 3)])
def test_superseded_stop_does_not_cancel_final_valid_positive_command(new_generated, new_id):
    x = boundary_core()
    x.pending_commands.append(command(x, new_id, new_generated, 12., 180.))
    x.boundary()
    assert x.executed['gt_electric_kW'][0] == 180.
    assert not any(e['kind'].startswith('sf1_pending_start_cancelled') for e in x.events)
    assert x.active_command['id'] == new_id


def test_already_online_min_up_and_true_protection_remain_original():
    x = boundary_core()
    x.gt_start_pending.clear()
    x.executed['gt_electric_kW'][0] = 180.
    x.state.gt_prev = np.array([180., 0., 0.])
    x.gt_clocks.commit([180., 0., 0.], 11.)
    x.boundary()
    assert x.executed['gt_electric_kW'][0] == 180.
    assert any(e['kind'] == 'minimum_up_normal_stop_held' for e in x.events)
    x.time = 13.
    hardware = deepcopy(x.data_at(13.)[-1])
    hardware['GT_1'] = 0.
    x.data_at = lambda t: (1, SimpleNamespace(outdoor_temperature_C=20.), np.ones((4, 3))*100., hardware)
    x.boundary()
    assert x.executed['gt_electric_kW'][0] == 0.


def test_model_spec_is_named_frozen_and_rejects_other_substeps():
    spec = api().ModelSpec()
    assert spec.physical_model_id == 'SF1_single_fault_model_20261102'
    assert spec.storage_integration_contract == 'single_fault.continuous_loss_held_flow.v1'
    with pytest.raises(FrozenInstanceError):
        spec.physical_max_segment_minutes = .5
    for value in (0., 2., True, float('nan'), '1'):
        with pytest.raises(ValueError):
            api().ModelSpec(physical_max_segment_minutes=value)
    with pytest.raises(ValueError):
        api().ModelSpec(physical_revision='invented')


class NativeCore:
    def __init__(self, env, initial_action):
        from tools.sci_engineering.storage import FiniteStore
        self.env = env
        self.time = 0.
        self.ups = FiniteStore(env.sci_config['ups'])
        self.cold_store = FiniteStore(env.sci_config['cold_store'])
        # Replacement must preserve actual present energy and preparation cost.
        self.ups.energy_kWh = 17.
        self.cold_store.energy_kWh = 23.
        self.events = []
    def log(self, kind, **values): self.events.append(dict(kind=kind, **values))
    def _next_boundary(self, end): return min(end, self.time+1.)
    def _solve(self, *a, **kw): return 'native-solve'
    def observe(self): return {'native':True}
    def _after_actual_segment(self, segment, result): segment['native_readiness_hook'] = True
    def boundary(self): return 'native-boundary'


def fake_readiness(calls):
    def create(*args, **kwargs):
        calls.append(('constructor', deepcopy(kwargs)))
        config = dict(capacity_kWh=100., power_kW=10., initial_soc=.5,
                      charge_efficiency=.9, discharge_efficiency=.8, loss_per_hour=.01)
        env = SimpleNamespace(engineering_core_type=NativeCore, model_id='old',
            operating_model_config=deepcopy(kwargs.get('model_config', {})),
            sci_config=dict(ups=config, cold_store=deepcopy(config)), core=None)
        def observation(): return env.core.observe()
        env._observation = observation
        def reset(*, scenario, seed=None):
            calls.append(('reset', deepcopy(scenario)))
            env.model_id = 'annual_readiness_20261030'
            env.core = env.engineering_core_type(env, {})
            env.operating_model_config['annual_readiness'] = {'registered':True}
            return observation(), {'native_info':True}
        env.reset = reset
        return env
    return SimpleNamespace(OperatingHospitalIESEnv=create, controller_factory='original-controller',
                           validate_events='original-events', validate_scenario='original-scenario')


def fake_environment(monkeypatch, *, segment=.5):
    api()
    environment = importlib.import_module('tools.single_fault_model_20261102.environment')
    calls = []
    monkeypatch.setattr(environment, 'with_readiness', lambda *a, **kw: fake_readiness(calls))
    original_config = {'immutable_original':{'value':1}}
    factory = environment.with_single_fault_model('M_available', model_spec=api().ModelSpec(
        physical_max_segment_minutes=segment))
    env = factory.OperatingHospitalIESEnv(ROOT, model_config=original_config)
    assert calls == [('constructor', {'model_config':original_config})]
    assert original_config == {'immutable_original':{'value':1}}
    env.reset(scenario={'registered':'scene'})
    return env, calls, factory


def test_factory_installs_exact_new_stores_without_inventory_reset_and_restores_sf1_id(monkeypatch):
    from tools.single_fault_model_20261102.storage import ContinuousLossStore
    env, calls, factory = fake_environment(monkeypatch)
    assert len([c for c in calls if c[0] == 'reset']) == 1
    assert type(env.core.ups) is ContinuousLossStore and type(env.core.cold_store) is ContinuousLossStore
    assert env.core.ups.energy_kWh == 17. and env.core.cold_store.energy_kWh == 23.
    assert env.core.ups.initial_charged_input_kWh == 50./.9
    assert env.model_id == api().MODEL_ID
    assert env.operating_model_config['single_fault_model'] == env.single_fault_model_spec.as_dict()
    assert factory.controller_factory == 'original-controller'
    observed = env.core.observe()['single_fault_model']
    assert observed['actual_storage_integration'] == dict(ups=ContinuousLossStore.integration_contract,
                                                       cold_store=ContinuousLossStore.integration_contract)
    segment = {}
    env.core._after_actual_segment(segment, {})
    assert segment['native_readiness_hook'] is True and segment['single_fault_model'] == observed


@pytest.mark.parametrize('change', ['spec', 'config', 'store', 'method', 'class_method'])
@pytest.mark.parametrize('entry', ['_solve', '_next_boundary', 'observe', 'boundary'])
def test_sf1_runtime_fails_closed_on_bound_model_or_storage_drift(monkeypatch, change, entry):
    from tools.sci_engineering.storage import FiniteStore
    from tools.single_fault_model_20261102.storage import ContinuousLossStore
    env, _, _ = fake_environment(monkeypatch)
    if change == 'spec': env.single_fault_model_spec = api().ModelSpec(physical_max_segment_minutes=1.)
    elif change == 'config': env.operating_model_config['single_fault_model']['physical_max_segment_minutes'] = 1.
    elif change == 'store': env.core.ups = FiniteStore(env.core.ups.config)
    elif change == 'method': env.core.ups.project_energy = lambda **kw: 0.
    else: monkeypatch.setattr(ContinuousLossStore, 'commit', lambda *a, **kw: {})
    with pytest.raises(ValueError, match='SF1'):
        getattr(env.core, entry)(90.) if entry == '_next_boundary' else getattr(env.core, entry)()


def test_max_physical_substep_only_splits_native_boundary(monkeypatch):
    env, _, _ = fake_environment(monkeypatch, segment=.5)
    assert env.core._next_boundary(15.) == .5
    env.core.time = .4
    assert env.core._next_boundary(.6) == .6
    # No command/controller hook belongs to the physical boundary interface.
    assert env.core.events[0]['kind'] == 'sf1_physical_model_installed'


def test_core_bound_spec_is_plain_deterministic_snapshot_data(monkeypatch):
    from tools.model_review_v27.objective_snapshot import _encode
    env, _, _ = fake_environment(monkeypatch)
    metadata = {key:value for key,value in vars(env.core).items() if key.startswith('_single_fault')}
    assert '_single_fault_spec_identity' not in metadata, 'Process memory addresses cannot bind physical initial receipts'
    _encode(metadata)


def test_factory_scope_check_runs_before_native_reset(monkeypatch):
    env, calls, _ = fake_environment(monkeypatch)
    before = len(calls)
    with pytest.raises(ValueError, match='one exogenous'):
        env.reset(scenario={'faults':{'GT_1':0., 'GRID':0.}})
    assert len(calls) == before


def test_single_exogenous_source_rejects_second_target_and_second_down_edge():
    base = dict(faults={}, auxiliary_faults={}, model_closure_events=[])
    assert api().validate_single_fault_scenario(base) is None
    valid = dict(base, model_closure_events=[{'time_minute':60., 'availability':{'GRID':0.}},
                                            {'time_minute':90., 'availability':{'GRID':1.}}])
    api().validate_single_fault_scenario(valid)
    for bad in (dict(base, faults={'GT_1':0., 'GT_2':0.}),
                dict(base, faults={'GT_1':0.}, auxiliary_faults={'AUX_CHW_PUMP':0.}),
                dict(base, model_closure_events=valid['model_closure_events']+
                    [{'time_minute':120., 'availability':{'GRID':0.}}]),
                dict(base, model_closure_events=[{'time_minute':60., 'availability':{'GRID':0., 'ABS':1.}}])):
        with pytest.raises(ValueError, match='single|one|target|cycle'):
            api().validate_single_fault_scenario(bad)
