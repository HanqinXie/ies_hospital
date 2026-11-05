"""Post-acknowledgement compaction must preserve native future decoration."""
import copy
import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from tools.execution_recovery.execution import ExecutionRecoveryCore, StateCoordinatedCore


def implementation():
    try:
        return importlib.import_module('tools.annual_program.runtime_history')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_program.runtime_history':
            raise
        pytest.fail('Exact post-commit execution-map compaction is missing', pytrace=False)


def permission(number):
    return {'grid_import_kW': float(number), 'nested': {'source': number}}


def boundary(number):
    return {'hardware_availability': {'GRID': float(number)},
            'execution_limits': {'native_boundary': number}}


def core_fixture(*, no_active=False):
    core = object.__new__(ExecutionRecoveryCore)
    core.time = 15.
    core.failed = False
    core._m23_advance_depth = 0
    core.segments = [{'start_minute': 14., 'end_minute': 15.,
                      'command_id': None if no_active else 2}]
    core.arrived_history = {None: permission(0)}
    if not no_active:
        core.arrived_history.update({1: permission(1), 2: permission(2)})
    core.active_command = None if no_active else {'id': 2}
    core.arrived_dispatch = copy.deepcopy(core.arrived_history[None if no_active else 2])
    core.pending_commands = []
    core.boundary_history = {0.: boundary(0), 8.: boundary(8), 14.: boundary(14)}
    return core


def test_preserves_map_and_value_identity_and_reports_exact_retention():
    core = core_fixture()
    core.arrived_history.update({3: permission(3), 4: permission(4), 5: permission(5)})
    # Latest arrival is insertion order, not greatest command ID: release times
    # can differ from generation order. Retain all actual pending references.
    core.arrived_history[3] = core.arrived_history.pop(3)
    core.arrived_dispatch = copy.deepcopy(core.arrived_history[3])
    core.pending_commands = [{'id': 4}, {'id': 9}]
    maps = (core.arrived_history, core.boundary_history)
    values = {key: core.arrived_history[key] for key in (None, 2, 3, 4)}
    last_boundary = core.boundary_history[14.]

    receipt = implementation().compact_execution_maps(core, committed_end_minute=15.)

    assert core.arrived_history is maps[0] and core.boundary_history is maps[1]
    assert set(core.arrived_history) == {None, 2, 3, 4}
    assert all(core.arrived_history[key] is value for key, value in values.items())
    assert core.boundary_history == {14.: last_boundary}
    assert core.boundary_history[14.] is last_boundary
    assert receipt['schema'] == 'hospital_ies.annual_program.execution_map_compaction.v1'
    assert receipt['arrived_history'] == {'before_count': 6, 'kept_count': 4, 'purged_count': 2}
    assert receipt['boundary_history'] == {'before_count': 3, 'kept_count': 1, 'purged_count': 2}
    assert receipt['complete_history_archive'] is False
    assert receipt['requires_prior_durable_segment_acknowledgement'] is True


@pytest.mark.parametrize('case', ['held_command', 'midstep_command', 'pending_release', 'no_active'])
@pytest.mark.parametrize('raise_after_prefix', [False, True])
def test_real_advance_wrapper_decorates_future_prefix_exactly_like_unpruned(
        monkeypatch, case, raise_after_prefix):
    original = core_fixture(no_active=case == 'no_active')
    if case == 'pending_release':
        original.pending_commands = [{'id': 3, 'arrival_minute': 18., 'dispatch': permission(3)}]
    compacted = copy.deepcopy(original)
    implementation().compact_execution_maps(compacted, committed_end_minute=15.)
    first = len(original.segments)

    class ExpectedStop(RuntimeError):
        pass

    def deterministic_parent_advance(core, end):
        assert end == 20.
        command_id = None if core.active_command is None else core.active_command['id']
        core.segments.append({'start_minute': 15., 'end_minute': 18.,
                              'command_id': command_id,
                              'arrived_source_setpoints': {'effective': 101.}})
        core.time = 18.
        if case in ('midstep_command', 'pending_release'):
            core.arrived_history[3] = permission(3)
            core.arrived_dispatch = copy.deepcopy(core.arrived_history[3])
            core.active_command = {'id': 3}
            core.pending_commands = []
            command_id = 3
        core.boundary_history[18.] = boundary(18)
        if raise_after_prefix:
            raise ExpectedStop('positive physical prefix before failure')
        core.segments.append({'start_minute': 18., 'end_minute': end,
                              'command_id': command_id,
                              'arrived_source_setpoints': {'effective': 102.}})
        core.time = end
        return 'native-parent-result'

    # Only the inherited physical advance is replaced. The real M18 finally
    # wrapper performs every permission and predecessor lookup under test.
    monkeypatch.setattr(StateCoordinatedCore, 'advance', deterministic_parent_advance)
    for core in (original, compacted):
        if raise_after_prefix:
            with pytest.raises(ExpectedStop, match='positive physical prefix'):
                core.advance(20.)
        else:
            assert core.advance(20.) == 'native-parent-result'
    assert compacted.segments[first:] == original.segments[first:]
    observed = compacted.segments[first:]
    assert observed[0]['arrived_source_permissions'] == permission(0 if case == 'no_active' else 2)
    assert observed[0]['execution_limits'] == {'native_boundary': 14}
    assert observed[0]['effective_source_setpoints'] == {'effective': 101.}
    if not raise_after_prefix:
        assert observed[1]['execution_limits'] == {'native_boundary': 18}
        expected = 3 if case in ('midstep_command', 'pending_release') else (0 if case == 'no_active' else 2)
        assert observed[1]['arrived_source_permissions'] == permission(expected)


def test_initial_none_permission_and_boundary_predecessor_survive_repeated_compaction():
    core = core_fixture(no_active=True)
    first = implementation().compact_execution_maps(core, committed_end_minute=15.)
    second = implementation().compact_execution_maps(core, committed_end_minute=15.)
    assert core.arrived_history == {None: permission(0)}
    assert first['arrived_history']['purged_count'] == 0
    assert second['arrived_history']['purged_count'] == 0
    assert second['boundary_history'] == {'before_count': 1, 'kept_count': 1, 'purged_count': 0}


def invalid_core(kind):
    core = core_fixture()
    acknowledgement = 15.
    if kind == 'ack_mismatch': acknowledgement = 14.
    elif kind == 'ack_nonfinite': acknowledgement = float('inf')
    elif kind == 'ack_bool': acknowledgement = True
    elif kind == 'clock_nonfinite': core.time = float('nan')
    elif kind == 'inside_advance': core._m23_advance_depth = 1
    elif kind == 'failed': core.failed = True
    elif kind == 'missing_segments': core.segments = []
    elif kind == 'last_endpoint_mismatch': core.segments[-1]['end_minute'] = 14.
    elif kind == 'last_endpoint_nonfinite': core.segments[-1]['end_minute'] = float('nan')
    elif kind == 'future_boundary': core.boundary_history[15.000001] = boundary(16)
    elif kind == 'nonfinite_boundary': core.boundary_history[float('nan')] = boundary(16)
    elif kind == 'bool_boundary': core.boundary_history[True] = boundary(1)
    elif kind == 'negative_boundary': core.boundary_history[-1.] = boundary(-1)
    elif kind == 'missing_boundary': core.boundary_history.clear()
    elif kind == 'string_id': core.arrived_history['2'] = permission(2)
    elif kind == 'bool_id': core.arrived_history[False] = permission(0)
    elif kind == 'negative_id': core.arrived_history[-1] = permission(-1)
    elif kind == 'missing_none': core.arrived_history.pop(None)
    elif kind == 'missing_active': core.arrived_history.pop(2)
    elif kind == 'active_string_id': core.active_command = {'id': '2'}
    elif kind == 'active_missing_id': core.active_command = {}
    elif kind == 'pending_string_id': core.pending_commands = [{'id': '3'}]
    elif kind == 'pending_missing_id': core.pending_commands = [{}]
    else: raise AssertionError(kind)
    return core, acknowledgement


@pytest.mark.parametrize('kind', [
    'ack_mismatch', 'ack_nonfinite', 'ack_bool', 'clock_nonfinite',
    'inside_advance', 'failed', 'missing_segments', 'last_endpoint_mismatch',
    'last_endpoint_nonfinite', 'future_boundary', 'nonfinite_boundary',
    'bool_boundary', 'negative_boundary', 'missing_boundary', 'string_id',
    'bool_id', 'negative_id', 'missing_none', 'missing_active',
    'active_string_id', 'active_missing_id', 'pending_string_id', 'pending_missing_id',
])
def test_invalid_or_ambiguous_state_is_rejected_before_either_map_changes(kind):
    core, acknowledgement = invalid_core(kind)
    maps = (core.arrived_history, core.boundary_history)
    items = (list(maps[0].items()), list(maps[1].items()))
    with pytest.raises(ValueError):
        implementation().compact_execution_maps(core, committed_end_minute=acknowledgement)
    assert core.arrived_history is maps[0] and core.boundary_history is maps[1]
    assert list(maps[0].items()) == items[0]
    assert list(maps[1].items()) == items[1]
