"""Native failure semantics with exact disk-prefix references, without solves."""
import copy
from dataclasses import asdict
import hashlib
import importlib
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from hospital_ies.execution import ModelConstraintInfeasible, SolverExecutionError
from hospital_ies.resilience import RuntimeState
from hospital_ies.state_transition import TransitionCore, clean
from tools.annual_program.disk_history import DiskHistory
from tools.annual_program.source_runtime import AnnualSourceMixin


def implementation():
    try:
        return importlib.import_module('tools.annual_program.failure_history')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_program.failure_history':
            raise
        pytest.fail('Bounded native failure-history adapter is missing', pytrace=False)


class Plant:
    def as_dict(self):
        return {'unchanged_plant': 37.}


def native_core():
    core = object.__new__(TransitionCore)
    state = RuntimeState(np.array([1., 2., 3.]), 17., {'GT_2'}, np.zeros(3), {})
    core.env = SimpleNamespace(state=state, scenario={'id': 'native_failure_fixture'},
                               steps_done=19, config={'unchanged_config': 2},
                               execution_contract={'unchanged_contract': 3}, plant=Plant())
    core.time = 2007.
    core.failed = False
    core.executed = {'current': [3., 4.]}
    core.pending_commands = [{'id': 91, 'arrival_minute': 2008.}]
    core.pending_shed = {'pending': 1}
    core.local_down = {'local': 2}
    core.gt_start_pending = {0: 2008.}
    core.auto_shed_latched = True
    core.automatic_disconnected = np.ones((4, 3))
    core.disconnected_fraction = np.zeros((4, 3))
    core.automatic_fraction = np.zeros((4, 3))
    core.connection_basis_loads = np.full((4, 3), 5.)
    core.grid_outage_start = 2000.
    core.ramp_time, core.ramp_anchor, core.ramp_budget = 2006., [1., 2., 3.], 1.
    core.errors, core.solver_calls = [], []
    core.segments = [{'start_minute': 2006., 'end_minute': 2007., 'actual': 9}]
    core.events = [{'event_id': 1, 'kind': 'before_failure'}]
    row = SimpleNamespace(timestamp='fixture-current-hour', outdoor_temperature_C=25.)
    core.data_at = lambda minute: (1, row, None, None)
    return core


def arguments(error):
    return (error, np.full((4, 3), 4.), {'GRID': 0.}, .25,
            'physical_execution', {'original_action': 5.}, time.perf_counter())


def disk_histories(core, tmp_path):
    originals = {}
    for name in ('segments', 'events'):
        identity = {'case_id': 'native_failure_fixture', 'stream': name}
        history = DiskHistory(tmp_path / (name + '.sqlite'), identity=identity)
        rows = ([{'start_minute': float(n), 'end_minute': float(n + 1), 'actual': n}
                 for n in range(2004)] if name == 'segments' else
                [{'event_id': n + 1, 'kind': 'native_event', 'actual': n} for n in range(2004)])
        history.extend(copy.deepcopy(rows))
        history.commit(keep_tail=2)
        tail = ([{'start_minute': float(n), 'end_minute': float(n + 1), 'actual': n}
                 for n in range(2004, 2007)] if name == 'segments' else
                [{'event_id': n + 1, 'kind': 'current_uncommitted_event'} for n in range(2004, 2007)])
        history.extend(tail)
        originals[name] = rows + copy.deepcopy(tail)
        setattr(core, name, history)
    return originals


def forbid_history_materialization(self, memo):
    raise AssertionError('Native failure attempted to materialize an entire DiskHistory')


@pytest.mark.parametrize('error_type', [ModelConstraintInfeasible, SolverExecutionError, ValueError])
def test_real_native_failure_keeps_exception_state_logs_and_recoverable_complete_history(
        tmp_path, monkeypatch, error_type):
    api = implementation()
    core = native_core()
    originals = disk_histories(core, tmp_path)
    before_state = clean(asdict(core.state))
    before_pending = copy.deepcopy(core.pending_commands)
    before_copy_module = TransitionCore._record_error.__globals__['copy']
    error = error_type('original physical failure')
    error.solver_backend_attempts = [{'status': 4, 'actual_attempt': 7}]
    monkeypatch.setattr(DiskHistory, '__deepcopy__', forbid_history_materialization)
    with pytest.raises(error_type) as captured:
        api.record_error_with_history_refs(core, *arguments(error))
    assert captured.value is error
    assert core.failed is True
    assert clean(asdict(core.state)) == before_state
    assert core.pending_commands == before_pending
    assert core.time == 2007. and core.executed == {'current': [3., 4.]}
    assert TransitionCore._record_error.__globals__['copy'] is before_copy_module is copy
    assert len(core.errors) == 1
    record = core.errors[0]
    assert record['error_type'] == error_type.__name__
    assert record['category'] == getattr(error, 'category', 'solver_exception')
    assert record['state'] == before_state
    assert record['solver_backend_attempts'] == error.solver_backend_attempts
    assert record['row'] == {'timestamp': 'fixture-current-hour', 'outdoor_temperature_C': 25.}
    assert core.solver_calls[0]['status'] == 'error'
    assert core.solver_calls[0]['solver_backend_layers'] == error.solver_backend_attempts
    assert core.events[-1]['kind'] == 'solver_error'
    assert core.events[-1]['event_id'] == len(originals['events']) + 1
    assert error.simulation_diagnostic == record
    assert error.simulation_diagnostic is not record
    for field, name in (('completed_segments', 'segments'), ('events', 'events')):
        reference = record[field]
        assert reference['schema'] == api.SCHEMA
        assert reference['reference_is_commit_acknowledgement'] is False
        assert reference['reference_is_independent_archive'] is False
        assert reference['native_source_sha256'] == api.NATIVE_SOURCE_SHA256
        assert reference['log_migration_id'] == api.LOG_MIGRATION_ID
        state = reference['history_state']
        assert state['prefix_count'] == 2002
        assert state['global_count'] == len(originals[name]) == 2007
        if name == 'segments':
            assert state['tail'] == core.segments.to_state()['tail']
        else:
            # Native log('solver_error') happens after the diagnostic snapshot.
            assert state['global_count'] == 2007
        before_bytes = hashlib.sha256(Path(state['path']).read_bytes()).hexdigest()
        restored = api.resolve_history_reference(reference, expected_identity=state['identity'])
        try:
            assert restored[:] == originals[name]
            assert restored.retained_count == 5
        finally:
            restored.close()
        assert hashlib.sha256(Path(state['path']).read_bytes()).hexdigest() == before_bytes
    core.segments.close()
    core.events.close()


def test_plain_lists_preserve_the_original_native_diagnostic_shape(monkeypatch):
    api = implementation()
    native, adapted = native_core(), native_core()
    native_error, adapted_error = ValueError('same native error'), ValueError('same native error')
    with pytest.raises(ValueError):
        TransitionCore._record_error(native, *arguments(native_error))
    with pytest.raises(ValueError):
        api.record_error_with_history_refs(adapted, *arguments(adapted_error))
    assert adapted.errors == native.errors
    assert adapted_error.simulation_diagnostic == native_error.simulation_diagnostic
    assert adapted.events == native.events
    assert isinstance(adapted.errors[0]['completed_segments'], list)
    assert isinstance(adapted.errors[0]['events'], list)


def test_unclassified_exception_keeps_the_original_native_wrapping_and_cause():
    api = implementation()
    core = native_core()
    original = RuntimeError('unclassified backend failure')
    with pytest.raises(SolverExecutionError) as captured:
        api.record_error_with_history_refs(core, *arguments(original))
    assert captured.value is not original
    assert captured.value.__cause__ is original
    assert str(captured.value) == str(original)
    assert captured.value.simulation_diagnostic == core.errors[0]
    assert core.failed is True and core.events[-1]['kind'] == 'solver_error'


class DiagnosticSourceCore(TransitionCore):
    """Only fixture source rows: the actual native error body remains unchanged."""
    def data_at(self, minute):
        self.diagnostic_queries.append(minute)
        absolute = (self.env.scenario['start_hour'] - 1) * 60. + minute
        assert 0. <= absolute < 8760. * 60., 'No diagnostic query may read hour8761'
        return (8760, SimpleNamespace(timestamp='actual-source-at-' + str(minute),
            outdoor_temperature_C=25.), None, None)


class AnnualDiagnosticCore(AnnualSourceMixin, DiagnosticSourceCore):
    pass


def terminal_core():
    source = native_core()
    core = object.__new__(AnnualDiagnosticCore)
    core.__dict__.update(source.__dict__)
    core.__dict__.pop('data_at')
    core.env.scenario['start_hour'] = 8760
    core.time = 60.
    core.segments = [{'start_minute': 59.75, 'end_minute': 60., 'actual': 9}]
    core.diagnostic_queries = []
    return core


@pytest.mark.parametrize('error_type', [ModelConstraintInfeasible, SolverExecutionError, ValueError])
def test_exact_annual_terminal_native_error_keeps_primary_failure_and_direct_history_refs(tmp_path,error_type):
    api = implementation();core = terminal_core();before_pending = copy.deepcopy(core.pending_commands)
    before_state = clean(asdict(core.state));before_fields = set(vars(core))
    for name in ('segments', 'events'):
        history = DiskHistory(tmp_path/(name+'.sqlite'), identity={'stream': name})
        history.extend(core.segments if name == 'segments' else core.events)
        history.commit(keep_tail=0)
        setattr(core, name, history)
    error = error_type('original terminal native failure')
    error.solver_backend_attempts = [{'status': 4, 'original_terminal_attempt': True}]
    with pytest.raises(error_type) as captured:
        core._record_error(*arguments(error))
    assert captured.value is error and core.failed is True
    assert core.time == 60. and core.pending_commands == before_pending
    assert clean(asdict(core.state)) == before_state and set(vars(core)) == before_fields
    assert core.diagnostic_queries == [59.75]
    record = core.errors[0]
    assert record['time_minute'] == 60. and record['row']['timestamp'] == 'actual-source-at-59.75'
    context = record['diagnostic_row_context']
    assert context['source'] == 'last_completed_actual_interval'
    assert context['query_minute'] == 59.75 and context['actual_error_time_minute'] == 60.
    assert context['physical_source_query_at_terminal'] is False
    assert error.simulation_diagnostic == record
    assert record['category'] == getattr(error, 'category', 'solver_exception')
    assert core.solver_calls[-1]['status'] == 'error'
    assert core.solver_calls[-1]['solver_backend_layers'] == error.solver_backend_attempts
    assert core.events[-1]['kind'] == 'solver_error'
    for field, name in (('completed_segments', 'segments'), ('events', 'events')):
        reference = record[field]
        view = api.resolve_history_reference(reference, reference['history_state']['identity'])
        try:assert len(view) == 1
        finally:view.close()
        getattr(core, name).close()
    with pytest.raises(ValueError, match='terminal boundary'):
        core.data_at(60.)
    assert core.diagnostic_queries == [59.75]


def test_ordinary_native_error_keeps_current_row_query_without_terminal_annotation():
    core = terminal_core();core.env.scenario['start_hour'] = 8759
    original = ValueError('ordinary current-source failure')
    with pytest.raises(ValueError) as captured:
        core._record_error(*arguments(original))
    assert captured.value is original and core.diagnostic_queries == [60.]
    assert core.errors[0]['row']['timestamp'] == 'actual-source-at-60.0'
    assert 'diagnostic_row_context' not in core.errors[0]


def test_terminal_unclassified_error_retains_native_wrapping_and_original_cause():
    core = terminal_core();original = RuntimeError('original terminal backend error')
    with pytest.raises(SolverExecutionError) as captured:
        core._record_error(*arguments(original))
    assert captured.value.__cause__ is original and str(captured.value) == str(original)
    assert captured.value.simulation_diagnostic == core.errors[0]
    assert core.errors[0]['error_type'] == 'RuntimeError' and core.failed is True
    assert core.diagnostic_queries == [59.75]


def test_unavailable_last_actual_source_row_retains_primary_native_error(monkeypatch):
    core = terminal_core();original = SolverExecutionError('primary terminal physical error')
    def unavailable(owner,minute):
        owner.diagnostic_queries.append(minute)
        raise ValueError('last actual source row unavailable')
    monkeypatch.setattr(DiagnosticSourceCore,'data_at',unavailable)
    with pytest.raises(RuntimeError,match='[Dd]iagnostic.*row') as captured:
        core._record_error(*arguments(original))
    assert captured.value.__cause__ is original and core.failed is True
    assert captured.value.simulation_diagnostic['native_record_error_completed'] is False
    assert core.diagnostic_queries == [59.75] and core.time == 60.


@pytest.mark.parametrize('last', [None, {}, {'start_minute': 59.75, 'end_minute': 59.9},
    {'start_minute': 60., 'end_minute': 60.}, {'start_minute': -1., 'end_minute': 60.},
    {'start_minute': True, 'end_minute': 60.}, {'start_minute': float('nan'), 'end_minute': 60.}])
def test_terminal_missing_or_malformed_interval_reports_unavailable_without_losing_primary_error(last):
    core = terminal_core();core.segments = [] if last is None else [last]
    original = SolverExecutionError('primary failure without trustworthy terminal row')
    with pytest.raises(RuntimeError, match='[Dd]iagnostic.*row|[Ll]ast.*interval') as captured:
        core._record_error(*arguments(original))
    assert captured.value.__cause__ is original and core.failed is True
    diagnostic = captured.value.simulation_diagnostic
    assert diagnostic['status'] == 'diagnostic_row_unavailable'
    assert diagnostic['time_minute'] == 60.
    assert diagnostic['original_error']['message'] == str(original)
    assert diagnostic['original_error']['category'] == original.category
    assert diagnostic['native_record_error_completed'] is False
    assert core.diagnostic_queries == [] and core.time == 60.


def reference_fixture(tmp_path):
    api = implementation()
    core = native_core()
    core.segments = DiskHistory(tmp_path / 'reference.sqlite', identity={'stream': 'segments'})
    core.segments.extend([{'actual': n} for n in range(4)])
    core.segments.commit(keep_tail=1)
    with pytest.raises(ValueError):
        api.record_error_with_history_refs(core, *arguments(ValueError('expected native failure')))
    return api, core, core.errors[0]['completed_segments']


@pytest.mark.parametrize('damage', ['content', 'identity', 'newer_version', 'missing_database', 'database_bytes'])
def test_resolver_rejects_corruption_wrong_identity_or_changed_database_without_overwrite(tmp_path, damage):
    api, core, reference = reference_fixture(tmp_path)
    expected = reference['history_state']['identity']
    path = core.segments.path
    if damage == 'content':
        reference = copy.deepcopy(reference)
        reference['history_state']['global_count'] += 1
    elif damage == 'identity': expected = {'stream': 'different'}
    elif damage == 'newer_version': core.segments.commit(keep_tail=1)
    elif damage == 'missing_database':
        core.segments.close()
        path.unlink()
    elif damage == 'database_bytes':
        core.segments.close()
        path.write_bytes(b'explicit corrupt database evidence')
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(ValueError):
        api.resolve_history_reference(reference, expected_identity=expected)
    assert (path.read_bytes() if path.exists() else None) == before
    core.segments.close()


def test_closed_history_raises_explicit_unknown_with_original_error_retained(tmp_path):
    api = implementation()
    core = native_core()
    core.segments = DiskHistory(tmp_path / 'closed.sqlite', identity={'stream': 'segments'})
    core.segments.append({'actual_partial_segment': 1})
    core.segments.close()
    original = ValueError('original solver error retained as cause')
    with pytest.raises(api.HistoryReferenceUnavailable) as captured:
        api.record_error_with_history_refs(core, *arguments(original))
    assert captured.value.__cause__ is original
    assert captured.value.simulation_diagnostic['status'] == 'history_reference_unavailable'
    assert captured.value.simulation_diagnostic['original_error']['message'] == str(original)
    assert captured.value.simulation_diagnostic['history_path'] == str(core.segments.path)
    assert captured.value.simulation_diagnostic['complete_history_recoverable'] is None


def test_source_pin_rejects_unreviewed_native_implementation(monkeypatch):
    api = implementation()
    monkeypatch.setattr(api, 'NATIVE_SOURCE_SHA256', '0' * 64)
    core = native_core()
    with pytest.raises(RuntimeError, match='source.*binding|source.*pin'):
        api.record_error_with_history_refs(core, *arguments(ValueError('native failure')))
    assert core.failed is False and core.errors == []


def test_resolver_forces_readonly_immutable_uri_and_never_configures_a_writer(tmp_path, monkeypatch):
    api, core, reference = reference_fixture(tmp_path)
    original_connect = sqlite3.connect
    opened = []

    def connect(database, *args, **kwargs):
        opened.append(database)
        assert kwargs.get('uri') is True
        assert database.endswith('?mode=ro&immutable=1')
        return original_connect(database, *args, **kwargs)

    def reject_writer_configuration(self):
        raise AssertionError('Reference resolver invoked writer configuration')

    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setattr(DiskHistory, '_configure', reject_writer_configuration)
    restored = api.resolve_history_reference(reference, expected_identity={'stream': 'segments'})
    try:
        assert opened and restored[:] == [{'actual': n} for n in range(4)]
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            restored._connection.execute('UPDATE header SET version=version+1')
        with pytest.raises(TypeError, match='read.only'):
            restored.append({'unacknowledged': 4})
        with pytest.raises(TypeError, match='read.only'):
            restored.commit(keep_tail=0)
        with pytest.raises(TypeError):
            restored[-1]['actual'] = 99
    finally:
        restored.close()
        core.segments.close()


@pytest.mark.parametrize('suffix', ['-journal', '-wal', '-shm'])
def test_resolver_rejects_ambiguous_sqlite_sidecars_without_touching_them(tmp_path, suffix):
    api, core, reference = reference_fixture(tmp_path)
    sidecar = Path(str(core.segments.path) + suffix)
    sidecar.write_bytes(b'preserved ambiguous sqlite sidecar')
    before = sidecar.read_bytes()
    with pytest.raises(ValueError, match='sidecar'):
        api.resolve_history_reference(reference, expected_identity={'stream': 'segments'})
    assert sidecar.read_bytes() == before
    core.segments.close()
