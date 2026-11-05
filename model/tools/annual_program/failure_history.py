"""Opt-in annual failure-log migration without changing the native error body.

Only direct deepcopy(DiskHistory) arguments become typed references. Ordinary
lists and every other native diagnostic field retain their original shape.
The original TransitionCore._record_error code runs in an isolated namespace;
neither the native module globals nor copy.deepcopy are monkeypatched.

Migration: completed_segments/events may contain SCHEMA references instead of
inline lists. Each reference contains the exact DiskHistory.to_state including
its complete current mutable tail. It is not a new commit acknowledgement or an
independent archive. The producer must remain quiescent and MUST NOT commit the
referenced segment/event databases afterwards: from_state rejects newer versions.
Annual integration must preserve this condition after a failed acknowledged block.
Legacy callers are unchanged until explicitly routed through this adapter.

At the exact annual source endpoint only, an isolated forwarding self facade
redirects the native diagnostic data_at query to the exact last actual interval
start. The native error clock/state/logging remain unchanged, and the record
explicitly labels this row provenance. Missing trustworthy interval context is
an explicit diagnostic-unavailable failure retaining the original error cause;
it never invents a row or queries hour8761.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import FunctionType

from hospital_ies.state_transition import TransitionCore
from .disk_history import DiskHistory, _freeze


SCHEMA = 'hospital_ies.annual_program.failure_history_reference.v1'
LOG_MIGRATION_ID = 'annual_native_failure_inline_history_to_exact_disk_reference.v1'
NATIVE_SOURCE_SHA256 = 'c77ecd4387706ef1468afd6c797308554c25b126cff7bd8481ccb24eb13ca3d6'
DISK_HISTORY_SOURCE_SHA256 = '88b80dbc378b7233ae854a6bbb0de6aeeff3d22bf1ddb6bd25931d08cf2d19d8'
_NATIVE_ERROR = TransitionCore._record_error
_NATIVE_FROM_STATE = DiskHistory.from_state.__func__
_REFERENCE_FIELDS = frozenset(('schema', 'log_migration_id', 'native_source_sha256',
    'disk_history_source_sha256', 'history_state', 'reference_is_commit_acknowledgement',
    'reference_is_independent_archive', 'content_sha256'))


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                         separators=(',', ':')).encode('utf8')
    return hashlib.sha256(encoded).hexdigest()


def _source_pin(function, expected, name):
    try:
        actual = hashlib.sha256(Path(function.__code__.co_filename).read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError(name + ' source binding cannot be verified') from exc
    if actual != expected:
        raise RuntimeError(name + ' source pin differs from the reviewed implementation')


class HistoryReferenceUnavailable(RuntimeError):
    """Explicit unknown, retaining the original native failure as its cause."""
    category = 'annual_history_reference_unavailable'

    def __init__(self, history, original_error, failure):
        super().__init__('Native failure history reference unavailable: ' + str(failure))
        self.simulation_diagnostic = dict(schema=SCHEMA + '.unavailable',
            status='history_reference_unavailable',
            history_path=str(history.path), complete_history_recoverable=None,
            original_error=dict(type=type(original_error).__name__, message=str(original_error),
                                category=getattr(original_error, 'category', 'solver_exception')),
            reference_error=dict(type=type(failure).__name__, message=str(failure)),
            native_record_error_completed=False)


def _history_reference(history, original_error):
    try:
        state = history.to_state()
        value = dict(schema=SCHEMA, log_migration_id=LOG_MIGRATION_ID,
            native_source_sha256=NATIVE_SOURCE_SHA256,
            disk_history_source_sha256=DISK_HISTORY_SOURCE_SHA256,
            history_state=state, reference_is_commit_acknowledgement=False,
            reference_is_independent_archive=False)
        return dict(value, content_sha256=_digest(value))
    except Exception as failure:
        raise HistoryReferenceUnavailable(history, original_error, failure) from original_error


class _HistoryCopyProxy:
    def __init__(self, original_error):
        self.original_error = original_error

    def deepcopy(self, value, *args, **kwargs):
        if isinstance(value, DiskHistory):
            return _history_reference(value, self.original_error)
        return copy.deepcopy(value, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(copy, name)


class DiagnosticRowUnavailable(RuntimeError):
    """Retain the primary physical failure when no truthful row can be logged."""
    def __init__(self, core, original_error, failure):
        super().__init__('Terminal diagnostic row unavailable: ' + str(failure))
        self.category = getattr(original_error, 'category', 'solver_exception')
        self.solver_backend_attempts = copy.deepcopy(getattr(original_error, 'solver_backend_attempts', []))
        self.simulation_diagnostic = dict(
            schema='hospital_ies.annual_program.terminal_diagnostic_row.unavailable.v1',
            status='diagnostic_row_unavailable',time_minute=core.time,
            original_error=dict(type=type(original_error).__name__,message=str(original_error),category=self.category),
            diagnostic_row_error=dict(type=type(failure).__name__,message=str(failure)),
            native_record_error_completed=False,physical_source_query_at_terminal=False)


class _DiagnosticErrorLog:
    def __init__(self, sink, context):
        self.sink,self.context = sink,context

    def append(self, record):
        # Label before the unchanged native body copies the record onto the
        # raised error. The original sink still owns the very same record.
        record['diagnostic_row_context'] = copy.deepcopy(self.context)
        return self.sink.append(record)


class _TerminalDiagnosticSelf:
    """Forward native state and mutations; override only diagnostic row access."""
    def __init__(self, core, context, original_error):
        object.__setattr__(self,'_core',core)
        object.__setattr__(self,'_context',context)
        object.__setattr__(self,'_original_error',original_error)
        object.__setattr__(self,'_errors',_DiagnosticErrorLog(core.errors,context))

    def __getattr__(self, name):
        return self._errors if name == 'errors' else getattr(self._core,name)

    def __setattr__(self, name, value):
        setattr(self._core,name,value)

    def data_at(self, minute):
        if minute != self._context['actual_error_time_minute']:
            raise ValueError('Unexpected native terminal diagnostic query clock')
        try:
            return self._core.data_at(self._context['query_minute'])
        except Exception as failure:
            self._core.failed = True
            raise DiagnosticRowUnavailable(self._core,self._original_error,failure) from self._original_error


def record_error_with_history_refs(core, *original_args, **kwargs):
    """Run the source-pinned original native error body with direct references."""
    _source_pin(_NATIVE_ERROR, NATIVE_SOURCE_SHA256, 'Native _record_error')
    if TransitionCore._record_error is not _NATIVE_ERROR:
        raise RuntimeError('Native _record_error source binding has been replaced')
    original_error = original_args[0] if original_args else kwargs.get('exc')
    context_provider = getattr(core, '_annual_error_row_context', None)
    try:
        context = context_provider() if context_provider is not None else None
    except Exception as failure:
        core.failed = True
        raise DiagnosticRowUnavailable(core,original_error,failure) from original_error
    target = _TerminalDiagnosticSelf(core,context,original_error) if context is not None else core
    namespace = dict(_NATIVE_ERROR.__globals__, copy=_HistoryCopyProxy(original_error))
    native = FunctionType(_NATIVE_ERROR.__code__, namespace, _NATIVE_ERROR.__name__,
                          _NATIVE_ERROR.__defaults__, _NATIVE_ERROR.__closure__)
    native.__kwdefaults__ = _NATIVE_ERROR.__kwdefaults__
    return native(target, *original_args, **kwargs)


def _read_only(*args, **kwargs):
    raise TypeError('Failure-history reference view is read-only')


class _ReadOnlyHistory(DiskHistory):
    # from_state constructs without __init__. Native schema/header/version/chain
    # validation is retained, but no writer PRAGMA is issued on this connection.
    def _configure(self):
        pass

    def __getitem__(self, key):
        return _freeze(super().__getitem__(key))

    append = extend = __iadd__ = __setitem__ = commit = _read_only


class _ReadOnlySqliteProxy:
    @staticmethod
    def connect(database, *args, **kwargs):
        if (not isinstance(database, str) or not database.endswith('?mode=rw')
                or kwargs.get('uri') is not True):
            raise ValueError('Unexpected native history connection contract')
        return sqlite3.connect(database[:-len('?mode=rw')] + '?mode=ro&immutable=1', *args, **kwargs)


def _reject_sidecars(path):
    if any(Path(str(path) + suffix).exists() for suffix in ('-journal', '-wal', '-shm')):
        raise ValueError('Ambiguous SQLite sidecar prevents immutable reference resolution')


def _file_stamp(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def resolve_history_reference(reference, expected_identity):
    """Return a verified read-only history view; caller closes it after use.

    Reuse the pinned native from_state validation body in an isolated namespace,
    changing only connection opening to mode=ro,immutable=1 and disabling writer
    configuration. No path override or prefix truncation is allowed. The producer
    must be quiescent; sidecars and detected concurrent file changes are rejected.
    """
    try:
        if (type(reference) is not dict or set(reference) != _REFERENCE_FIELDS
                or reference['schema'] != SCHEMA
                or reference['log_migration_id'] != LOG_MIGRATION_ID
                or reference['native_source_sha256'] != NATIVE_SOURCE_SHA256
                or reference['disk_history_source_sha256'] != DISK_HISTORY_SOURCE_SHA256
                or reference['reference_is_commit_acknowledgement'] is not False
                or reference['reference_is_independent_archive'] is not False
                or reference['content_sha256'] != _digest({key: value for key, value in reference.items()
                                                        if key != 'content_sha256'})):
            raise ValueError('Invalid typed failure-history reference integrity or migration')
        state = reference['history_state']
        if type(state) is not dict or not isinstance(state.get('path'), str):
            raise ValueError('Missing exact failure-history database path')
        path = Path(state['path'])
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or substituted failure-history database')
        _reject_sidecars(path)
        before = _file_stamp(path)
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError('Invalid or unavailable failure-history reference') from exc
    _source_pin(_NATIVE_FROM_STATE, DISK_HISTORY_SOURCE_SHA256, 'DiskHistory.from_state')
    if DiskHistory.from_state.__func__ is not _NATIVE_FROM_STATE:
        raise ValueError('Native DiskHistory.from_state binding has been replaced')
    namespace = dict(_NATIVE_FROM_STATE.__globals__, sqlite3=_ReadOnlySqliteProxy())
    restore = FunctionType(_NATIVE_FROM_STATE.__code__, namespace, _NATIVE_FROM_STATE.__name__,
                           _NATIVE_FROM_STATE.__defaults__, _NATIVE_FROM_STATE.__closure__)
    restore.__kwdefaults__ = _NATIVE_FROM_STATE.__kwdefaults__
    result = restore(_ReadOnlyHistory, state, identity=expected_identity)
    try:
        _reject_sidecars(path)
        if _file_stamp(path) != before:
            raise ValueError('Failure-history database changed during reference verification')
    except Exception:
        result.close()
        raise
    return result
