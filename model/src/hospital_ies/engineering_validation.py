"""Volatile exact memoization of immutable parameter derivations only.

The caller supplies a frozen source/input/case/profile identity and invalidates
at its full-byte validation boundaries. This module neither reads source files
nor authenticates them. Current state, constraints, paths and solver vectors
are deliberately outside this cache's integration points.
"""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import fields, is_dataclass
import os
import threading

_ACTIVE = ContextVar('hospital_ies_engineering_parameter_validation', default=None)
MAX_ENTRIES = 512


def immutable_token(value):
    """Typed exact token; mutable containers are inspected on every request."""
    kind = type(value)
    if kind is float:
        return (kind, value.hex())
    if kind in (str, int, bool, type(None)):
        return (kind, value)
    if kind in (tuple, list):
        return (kind, tuple(immutable_token(item) for item in value))
    if kind is dict and all(type(key) is str for key in value):
        return (kind, tuple((key, immutable_token(value[key])) for key in sorted(value)))
    raise TypeError('Unsupported exact parameter token: ' + kind.__name__)


def _immutable(value):
    if type(value) in (str, int, float, bool, type(None)):
        return True
    if type(value) is tuple:
        return all(_immutable(item) for item in value)
    if is_dataclass(value) and getattr(type(value), '__dataclass_params__').frozen:
        return all(_immutable(getattr(value, field.name)) for field in fields(value))
    return False


class _ParameterCache:
    def __init__(self, run_identity, max_entries):
        if type(run_identity) is not str or not run_identity or len(run_identity) > 256:
            raise ValueError('Nonempty frozen-run identity of at most 256 characters required')
        if type(max_entries) is not int or not 1 <= max_entries <= MAX_ENTRIES:
            raise ValueError('Bounded explicit parameter cache size required')
        self.run_identity = run_identity
        self.max_entries = max_entries
        self.pid = os.getpid()
        self.thread = threading.get_ident()
        self.generation = 0
        self.entries = OrderedDict()
        self.namespaces = {}
        self.invalidations = []
        self.evictions = 0

    def _owner(self):
        if os.getpid() != self.pid or threading.get_ident() != self.thread:
            raise RuntimeError('Parameter validation cache cannot cross its process/thread owner')

    def get(self, namespace, key, compute, *, guard=None):
        self._owner()
        counts = self.namespaces.setdefault(namespace, dict(calls=0, hits=0, misses=0,
            computations=0, failed_computations=0, rejected_cached_values=0, mutable_output_skips=0))
        counts['calls'] += 1
        location = (namespace, key)
        prior = self.entries.get(location)
        if prior is not None:
            value, certificate = prior
            try:
                valid = guard is None or guard(value) == certificate
            except Exception:
                valid = False
            if valid:
                self.entries.move_to_end(location)
                counts['hits'] += 1
                return value
            counts['rejected_cached_values'] += 1
            self.entries.pop(location)
        counts['misses'] += 1
        counts['computations'] += 1
        try:
            value = compute()
        except BaseException:
            counts['failed_computations'] += 1
            raise
        # Unknown/mutable outputs still work, but never become shared values.
        if not _immutable(value):
            counts['mutable_output_skips'] += 1
            return value
        certificate = None if guard is None else guard(value)
        self.entries[location] = (value, certificate)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)
            self.evictions += 1
        return value

    def invalidate(self, reason):
        self._owner()
        if type(reason) is not str or not reason or len(reason) > 256:
            raise ValueError('Explicit bounded full-validation boundary reason required')
        count = len(self.entries)
        self.entries.clear()
        self.generation += 1
        self.invalidations.append(dict(generation=self.generation, reason=reason, discarded_entries=count))

    def stats(self):
        self._owner()
        return dict(schema='hospital_ies.engineering_parameter_validation_cache.v1',
            enabled=True, run_identity=self.run_identity, process_id=self.pid,
            generation=self.generation, entries=len(self.entries), max_entries=self.max_entries,
            evictions=self.evictions, namespaces={key:dict(value) for key,value in self.namespaces.items()},
            invalidations=[dict(value) for value in self.invalidations], checkpointed=False,
            scope='exact immutable fuel-curve identity derivation only',
            source_byte_validation_performed=False, actual_state_validation_cached=False)


def active_validation_cache():
    cache = _ACTIVE.get()
    if cache is not None:
        cache._owner()
    return cache


@contextmanager
def engineering_validation_scope(*, run_identity, max_entries=128):
    """Start empty, never checkpoint; full-validation boundaries call invalidate."""
    if _ACTIVE.get() is not None:
        raise RuntimeError('Nested engineering parameter cache scopes are unsupported')
    cache = _ParameterCache(run_identity, max_entries)
    token = _ACTIVE.set(cache)
    try:
        yield cache
    finally:
        cache.entries.clear()
        _ACTIVE.reset(token)
