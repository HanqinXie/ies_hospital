"""Compact only native execution lookup maps after a durable segment commit.

This helper does not archive history or authenticate a journal. Its caller must
first durably acknowledge every segment through ``committed_end_minute``. The
native journal retains the complete per-segment permissions and boundary facts.

The consumers are ExecutionRecoveryCore.advance (execution.py:397-401): future
segments look up their command ID and the latest boundary predecessor. Commands
are assigned unique positive integer IDs by TransitionCore.generate_command
(state_transition.py:188-193). ExecutionRecoveryCore.boundary inserts an arrived
permission once on its arrival (execution.py:212-225), so dictionary insertion
order identifies the latest arrival even when release and generation orders
differ. No physical state, command, action or threshold is changed here.
"""
from __future__ import annotations

import math


SCHEMA = 'hospital_ies.annual_program.execution_map_compaction.v1'


def _minute(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0.:
        raise ValueError('Expected finite nonnegative ' + name)
    return value


def _command_id(value, *, allow_none=False):
    if value is None and allow_none:
        return value
    if type(value) is not int or value <= 0:
        raise ValueError('Unclassifiable native command ID')
    return value


def _counts(before, after):
    return dict(before_count=before, kept_count=after, purged_count=before-after)


def compact_execution_maps(core, *, committed_end_minute):
    """Keep exact future lookup support at a healthy post-commit boundary.

    Preserve both dictionary objects and retained value identities. All input
    validation precedes mutation. The acknowledgement is a caller precondition,
    not a claim that this helper alone establishes a complete historical archive.
    """
    end = _minute(committed_end_minute, 'acknowledged endpoint')
    try:
        now = _minute(core.time, 'physical clock')
        if (now != end or core.failed is not False
                or type(getattr(core, '_m23_advance_depth', 0)) is not int
                or getattr(core, '_m23_advance_depth', 0) != 0):
            raise ValueError('Execution-map compaction requires a healthy quiescent acknowledged clock')
        if not core.segments:
            raise ValueError('A completed acknowledged final segment is required')
        last = core.segments[-1]
        if (not isinstance(last, dict)
                or _minute(last['end_minute'], 'last segment endpoint') != now
                or _minute(last['start_minute'], 'last segment start') >= now):
            raise ValueError('Final completed segment does not end at the acknowledged clock')
        _command_id(last['command_id'], allow_none=True)
        arrived, boundaries = core.arrived_history, core.boundary_history
        active, pending = core.active_command, core.pending_commands
    except (AttributeError, KeyError, TypeError, IndexError) as exc:
        raise ValueError('Missing or unclassifiable execution-map state') from exc

    if type(arrived) is not dict or None not in arrived:
        raise ValueError('The native arrived-permission map and initial None permission are required')
    for command_id, value in arrived.items():
        _command_id(command_id, allow_none=True)
        if not isinstance(value, dict):
            raise ValueError('Unclassifiable native arrived-permission value')
    keep = {None}
    # Native assignment inserts each unique arrived ID once; no numerical ID
    # ordering assumption is needed for differently delayed commands.
    latest = next((command_id for command_id in reversed(arrived) if command_id is not None), None)
    keep.add(latest)
    if active is not None:
        if not isinstance(active, dict) or 'id' not in active:
            raise ValueError('Unclassifiable active command')
        active_id = _command_id(active['id'])
        if active_id not in arrived:
            raise ValueError('Active command lacks its native arrived permission')
        keep.add(active_id)
    if type(pending) is not list:
        raise ValueError('Unclassifiable native pending-command queue')
    for command in pending:
        if not isinstance(command, dict) or 'id' not in command:
            raise ValueError('Unclassifiable pending command')
        command_id = _command_id(command['id'])
        if command_id in arrived:
            keep.add(command_id)

    if type(boundaries) is not dict or not boundaries:
        raise ValueError('A native boundary predecessor map is required')
    for minute, value in boundaries.items():
        _minute(minute, 'native boundary key')
        if minute > now:
            raise ValueError('Future boundary evidence cannot be compacted')
        if not isinstance(value, dict):
            raise ValueError('Unclassifiable native boundary value')
    predecessor = max(boundaries)
    before_arrived, before_boundaries = len(arrived), len(boundaries)
    for command_id in tuple(arrived):
        if command_id not in keep:
            del arrived[command_id]
    for minute in tuple(boundaries):
        if minute != predecessor:
            del boundaries[minute]
    return dict(schema=SCHEMA, committed_end_minute=end,
                arrived_history=_counts(before_arrived, len(arrived)),
                boundary_history=_counts(before_boundaries, len(boundaries)),
                complete_history_archive=False,
                requires_prior_durable_segment_acknowledgement=True)
