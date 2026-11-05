"""Complete append-only list history with a durable prefix and mutable tail.

Only plain typed row data is accepted; solver-log subclasses and live resources
are not rows. The SQLite prefix has global indices, an identity-bound checksum
chain and a monotonically increasing commit version. ``from_state`` rejects any
newer prefix/version, even when the old checkpoint still names a valid prefix.

Every public archived-value mutation is rejected. ``deepcopy`` provides detached
ordinary mutable containers. New rows retain normal list reference semantics
until archived. ``close`` never silently commits or discards an ambiguous prefix;
the caller must durably save ``to_state`` to preserve the current mutable tail.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import numpy as np

from .checkpoint import _encode, _decode


SCHEMA = 'hospital_ies.annual_program.disk_history.v2.gzip'
_STATE_FIELDS = frozenset(('schema', 'path', 'identity', 'identity_sha256',
    'prefix_count', 'version', 'last_digest', 'tail', 'global_count', 'content_sha256'))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(',', ':')).encode('utf8')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _reject(*args, **kwargs):
    raise TypeError('Archived history is immutable; deepcopy it for a detached mutable value')


class _FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _reject

    def __deepcopy__(self, memo):
        result = {}
        memo[id(self)] = result
        for key, value in self.items():
            result[copy.deepcopy(key, memo)] = copy.deepcopy(value, memo)
        return result


class _FrozenList(list):
    __setitem__ = __delitem__ = append = extend = insert = pop = remove = clear = sort = reverse = __iadd__ = __imul__ = _reject

    def __deepcopy__(self, memo):
        result = []
        memo[id(self)] = result
        result.extend(copy.deepcopy(value, memo) for value in self)
        return result


class _FrozenSet(set):
    add = clear = difference_update = discard = intersection_update = pop = remove = symmetric_difference_update = update = __iand__ = __ior__ = __isub__ = __ixor__ = _reject

    def __deepcopy__(self, memo):
        result = set(copy.deepcopy(value, memo) for value in self)
        memo[id(self)] = result
        return result


def _freeze(value):
    if type(value) is dict:
        return _FrozenDict((_freeze(k), _freeze(v)) for k, v in value.items())
    if type(value) is list:
        return _FrozenList(_freeze(v) for v in value)
    if type(value) is tuple:
        return tuple(_freeze(v) for v in value)
    if type(value) is set:
        return _FrozenSet(_freeze(v) for v in value)
    if type(value) is frozenset:
        return frozenset(_freeze(v) for v in value)
    if type(value) is np.ndarray:
        # Immutable bytes backing prevents callers from re-enabling writes.
        return np.frombuffer(value.tobytes(order='C'), dtype=value.dtype).reshape(value.shape)
    return value


def _plain_row(value, active=None):
    active = set() if active is None else active
    if value is None or type(value) in (str, bool, int, float) or isinstance(value, Path):
        return
    if isinstance(value, np.generic):
        if value.dtype.kind not in 'biuf':
            raise TypeError('Unsupported history scalar dtype')
        return
    if type(value) is np.ndarray:
        if value.dtype.hasobject or value.dtype.fields or value.dtype.kind not in 'biuf':
            raise TypeError('Unsupported history array dtype')
        return
    if type(value) not in (dict, list, tuple, set, frozenset):
        raise TypeError('History rows require plain typed data, not behavior-bearing objects')
    if id(value) in active:
        raise ValueError('Cyclic history row data is unsupported')
    active.add(id(value))
    try:
        if type(value) is dict:
            for key, item in value.items():
                _plain_row(key, active)
                _plain_row(item, active)
        else:
            for item in value:
                _plain_row(item, active)
    finally:
        active.remove(id(value))


def _row_bytes(value):
    _plain_row(value)
    return gzip.compress(_json(_encode(value)),mtime=0)


def _decode_row(payload):
    try:
        value = _decode(json.loads(gzip.decompress(bytes(payload)).decode('utf8')))
        _plain_row(value)
    except Exception as exc:
        raise ValueError('Invalid typed disk history row') from exc
    if _row_bytes(value) != bytes(payload):
        raise ValueError('Noncanonical disk history row payload')
    return value


def _row_digest(index, previous, identity_sha, payload_sha):
    return _sha(_json(dict(index=index, previous_digest=previous,
        identity_sha256=identity_sha, payload_sha256=payload_sha)))


def _identity(value):
    if type(value) is not dict or not value:
        raise ValueError('A nonempty disk history identity is required')
    encoded = _json(value)
    return json.loads(encoded), _sha(encoded)


class DiskHistory(list):
    """List-compatible global history; only appends preserve global indices.

    SQLite stores the immutable prefix. At most ``keep_tail`` current objects
    remain resident after ``commit``. Ordinary slices intentionally return a
    list and preserve current-tail object references, as existing exporters
    amend newly completed segment dictionaries before the next commit.
    """
    def __init__(self, path, *, identity):
        list.__init__(self)
        self.path = Path(path).resolve()
        self.identity, self._identity_sha = _identity(identity)
        self._tail = []
        self._prefix_count = self._version = 0
        self._last_digest = None
        self._closed = False
        self._write_error = None
        self._connection = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('xb'):
            pass
        try:
            self._connection = sqlite3.connect(str(self.path), isolation_level=None)
            self._configure()
            self._connection.execute('BEGIN IMMEDIATE')
            self._connection.execute('CREATE TABLE header (id INTEGER PRIMARY KEY CHECK(id=1), schema TEXT NOT NULL, identity BLOB NOT NULL, identity_sha TEXT NOT NULL, prefix_count INTEGER NOT NULL, version INTEGER NOT NULL, last_digest TEXT)')
            self._connection.execute('CREATE TABLE entries (idx INTEGER PRIMARY KEY, payload BLOB NOT NULL, payload_sha TEXT NOT NULL, previous_digest TEXT, digest TEXT NOT NULL)')
            self._connection.execute('INSERT INTO header VALUES (1, ?, ?, ?, 0, 0, NULL)',
                (SCHEMA, _json(self.identity), self._identity_sha))
            self._connection.commit()
            self._fsync()
        except Exception:
            if self._connection is not None:
                self._connection.close()
            self._closed = True
            raise

    def _configure(self):
        self._connection.execute('PRAGMA journal_mode=DELETE')
        self._connection.execute('PRAGMA synchronous=FULL')
        self._connection.execute('PRAGMA busy_timeout=3000')

    def _fsync(self):
        with self.path.open('r+b') as stream:
            os.fsync(stream.fileno())

    def _check_open(self):
        if self._closed:
            raise RuntimeError('Disk history is closed')
        if self._write_error is not None:
            raise RuntimeError('Disk history has an unacknowledged write failure') from self._write_error

    def _header(self):
        rows = self._connection.execute('SELECT schema, identity, identity_sha, prefix_count, version, last_digest FROM header').fetchall()
        if len(rows) != 1:
            raise ValueError('Invalid disk history header count')
        schema, raw_identity, sha, count, version, last = rows[0]
        if (schema != SCHEMA or bytes(raw_identity) != _json(self.identity) or sha != self._identity_sha
                or type(count) is not int or count < 0 or type(version) is not int or version < 0):
            raise ValueError('Disk history identity or header corruption')
        return count, version, last

    def _check_version(self):
        if self._header() != (self._prefix_count, self._version, self._last_digest):
            raise ValueError('Disk history has an ambiguous newer prefix or version')

    def _verify_prefix(self):
        if self._connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('SQLite history integrity check failed')
        count, previous = 0, None
        for index, payload, payload_sha, prior, digest in self._connection.execute(
                'SELECT idx, payload, payload_sha, previous_digest, digest FROM entries ORDER BY idx'):
            if (index != count or prior != previous or payload_sha != _sha(bytes(payload))
                    or digest != _row_digest(index, previous, self._identity_sha, payload_sha)):
                raise ValueError('Disk history prefix checksum chain corruption')
            _decode_row(payload)
            previous, count = digest, count + 1
        if (count, self._version, previous) != self._header():
            raise ValueError('Disk history prefix count or terminal digest mismatch')
        if count != self._prefix_count or previous != self._last_digest:
            raise ValueError('Disk history state does not bind the complete prefix')

    @property
    def retained_count(self):
        self._check_open()
        return len(self._tail)

    def __len__(self):
        self._check_open()
        return self._prefix_count + len(self._tail)

    def append(self, value):
        self._check_open()
        _plain_row(value)
        self._tail.append(value)

    def extend(self, values):
        for value in values:
            self.append(value)

    def __iadd__(self, values):
        self.extend(values)
        return self

    def __getitem__(self, key):
        self._check_open()
        if isinstance(key, slice):
            return [self[index] for index in range(*key.indices(len(self)))]
        if not isinstance(key, int):
            try:
                key = key.__index__()
            except (AttributeError, TypeError) as exc:
                raise TypeError('History indices must be integers or slices') from exc
        if key < 0:
            key += len(self)
        if key < 0 or key >= len(self):
            raise IndexError('Disk history index out of range')
        if key >= self._prefix_count:
            return self._tail[key - self._prefix_count]
        row = self._connection.execute('SELECT payload, payload_sha, previous_digest, digest FROM entries WHERE idx=?', (key,)).fetchone()
        if row is None:
            raise ValueError('Missing archived history row')
        payload, payload_sha, previous, digest = row
        if payload_sha != _sha(bytes(payload)) or digest != _row_digest(key, previous, self._identity_sha, payload_sha):
            raise ValueError('Archived history row checksum corruption')
        return _freeze(_decode_row(payload))

    def __setitem__(self, key, value):
        self._check_open()
        if isinstance(key, slice):
            raise TypeError('Disk history global indices are append-only')
        if not isinstance(key, int):
            try:
                key = key.__index__()
            except (AttributeError, TypeError) as exc:
                raise TypeError('History indices must be integers') from exc
        if key < 0:
            key += len(self)
        if key < 0 or key >= len(self):
            raise IndexError('Disk history index out of range')
        if key < self._prefix_count:
            _reject()
        _plain_row(value)
        self._tail[key - self._prefix_count] = value

    __delitem__ = insert = pop = remove = clear = reverse = sort = __imul__ = _reject

    def __iter__(self):
        self._check_open()
        for index in range(len(self)):
            yield self[index]

    def __reversed__(self):
        self._check_open()
        for index in range(len(self)-1, -1, -1):
            yield self[index]

    def __contains__(self, value):
        return any(item == value for item in self)

    def __add__(self, other):
        if not isinstance(other, list):
            return NotImplemented
        return list(self) + list(other)

    def __radd__(self, other):
        if not isinstance(other, list):
            return NotImplemented
        return list(other) + list(self)

    def __mul__(self, count):
        return list(self) * count

    def __rmul__(self, count):
        return self.__mul__(count)

    def __eq__(self, other):
        if not isinstance(other, (list, tuple)):
            return NotImplemented
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))

    def __ne__(self, other):
        result = self.__eq__(other)
        return NotImplemented if result is NotImplemented else not result

    def count(self, value):
        return sum(item == value for item in self)

    def index(self, value, start=0, stop=None):
        start, stop, _ = slice(start, stop).indices(len(self))
        for index in range(start, stop):
            if self[index] == value:
                return index
        raise ValueError('Value is not in disk history')

    def copy(self):
        return list(self)

    def __copy__(self):
        return list(self)

    def __deepcopy__(self, memo):
        result = []
        memo[id(self)] = result
        result.extend(copy.deepcopy(value, memo) for value in self)
        return result

    def __repr__(self):
        if getattr(self, '_closed', True):
            return 'DiskHistory(closed)'
        return f'DiskHistory(path={str(self.path)!r}, count={len(self)}, retained={self.retained_count})'

    def commit(self, *, keep_tail=1):
        """Durably archive older tail rows and return an acknowledged version."""
        self._check_open()
        if type(keep_tail) is not int or keep_tail < 0:
            raise ValueError('keep_tail must be a nonnegative integer')
        self._check_version()
        archive_count = max(0, len(self._tail) - keep_tail)
        additions, previous = [], self._last_digest
        for offset in range(archive_count):
            index = self._prefix_count + offset
            payload = _row_bytes(self._tail[offset])
            payload_sha = _sha(payload)
            digest = _row_digest(index, previous, self._identity_sha, payload_sha)
            additions.append((index, payload, payload_sha, previous, digest))
            previous = digest
        new_count, new_version = self._prefix_count + archive_count, self._version + 1
        try:
            self._connection.execute('BEGIN IMMEDIATE')
            self._check_version()
            self._connection.executemany('INSERT INTO entries VALUES (?, ?, ?, ?, ?)', additions)
            self._connection.execute('UPDATE header SET prefix_count=?, version=?, last_digest=? WHERE id=1',
                (new_count, new_version, previous))
            self._connection.commit()
            self._fsync()
            if self._header() != (new_count, new_version, previous):
                raise ValueError('Persisted history commit receipt mismatch')
            for expected in additions:
                actual = self._connection.execute('SELECT idx, payload, payload_sha, previous_digest, digest FROM entries WHERE idx=?', (expected[0],)).fetchone()
                if actual != expected:
                    raise ValueError('Persisted history row differs from committed bytes')
        except Exception as exc:
            if self._connection.in_transaction:
                self._connection.rollback()
            self._write_error = exc
            raise
        del self._tail[:archive_count]
        self._prefix_count, self._version, self._last_digest = new_count, new_version, previous
        return dict(schema=SCHEMA, identity_sha256=self._identity_sha,
            prefix_count=new_count, version=new_version, last_digest=previous,
            global_count=len(self), retained_count=len(self._tail), durable=True)

    def to_state(self):
        self._check_open()
        self._check_version()
        for value in self._tail:
            _plain_row(value)
        state = dict(schema=SCHEMA, path=str(self.path), identity=copy.deepcopy(self.identity),
            identity_sha256=self._identity_sha, prefix_count=self._prefix_count,
            version=self._version, last_digest=self._last_digest,
            tail=_encode(self._tail), global_count=len(self))
        state['content_sha256'] = _sha(_json(state))
        return state

    @classmethod
    def from_state(cls, state, *, path=None, identity):
        wanted_identity, wanted_sha = _identity(identity)
        if type(state) is not dict or set(state) != _STATE_FIELDS or state.get('schema') != SCHEMA:
            raise ValueError('Invalid disk history resume state schema')
        if state['content_sha256'] != _sha(_json({k: v for k, v in state.items() if k != 'content_sha256'})):
            raise ValueError('Disk history state content checksum mismatch')
        if state['identity'] != wanted_identity or state['identity_sha256'] != wanted_sha:
            raise ValueError('Disk history resume identity mismatch')
        if any(type(state[key]) is not int or state[key] < 0 for key in ('prefix_count', 'version', 'global_count')):
            raise ValueError('Invalid disk history resume counts')
        tail = _decode(state['tail'])
        if type(tail) is not list or state['prefix_count'] + len(tail) != state['global_count']:
            raise ValueError('Disk history mutable tail/count mismatch')
        for value in tail:
            _plain_row(value)
        result = list.__new__(cls)
        list.__init__(result)
        result.path = Path(state['path'] if path is None else path).resolve()
        result.identity, result._identity_sha = wanted_identity, wanted_sha
        result._tail = tail
        result._prefix_count, result._version, result._last_digest = (
            state['prefix_count'], state['version'], state['last_digest'])
        result._closed = False
        result._write_error = None
        result._connection = None
        try:
            result._connection = sqlite3.connect(result.path.as_uri() + '?mode=rw', uri=True, isolation_level=None)
            result._configure()
            result._check_version()
            result._verify_prefix()
        except Exception as exc:
            if result._connection is not None:
                result._connection.close()
            result._closed = True
            if isinstance(exc, ValueError):
                raise
            raise ValueError('Cannot verify disk history for resume') from exc
        return result

    def close(self):
        if not getattr(self, '_closed', True):
            if self._connection is not None:
                self._connection.close()
            self._closed = True

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
