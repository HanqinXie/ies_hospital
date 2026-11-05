"""Typed quiescent checkpoints, independent of observation and physical solves.

This opt-in helper follows the V27 typed snapshot design, without changing its
global class registry. Every instance field of env/core/controller requires an
explicit disposition: ``state``, ``inplace`` (a factory-shared plain dict),
``rebuild``, ``external``, or ``alias:OWNER[.FIELD]``. No field is inferred away.
V2 explicitly declares shared mutable values and local references across all
three state partitions; undeclared cycles remain unsupported.
Rebuilt functions use the explicit ``python.code_fields.v1`` fingerprint;
marshal-based fingerprints require a fresh source-bound checkpoint. Their
interpreter string-intern/reference flags are not part of program behavior.

The caller constructs a fresh, source-bound *unreset* template using the same
factory, deterministically activates its input frames, and explicitly supplies
all rebuild/external fields. Rebuild fingerprints verify actual values, including
pandas frames. External fields preserve type but intentionally rebind I/O/history
resources; their durable byte/cursor identity must be included in ``binding`` or
``runner_state`` and independently checked by the runner before calling restore.

Only constructors' types/instance dictionaries are used during restoration.
No reset, observe, decision snapshot, core constructor or controller constructor
is invoked. Full annual resume equivalence and durable journal admission remain
runner integration gates, not qualifications established by this helper.
"""
from __future__ import annotations

import copy
from dataclasses import is_dataclass
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
from types import CodeType, FunctionType, MethodType

import numpy as np

from tools.integrated_learning.snapshot import rng_state, restore_rng
from tools.model_review_v27.objective_snapshot import PHYSICAL_CLASSES as V27_CLASSES


SCHEMA = 'hospital_ies.annual_program.quiescent_checkpoint.v2'
PHYSICAL_CLASSES = frozenset(V27_CLASSES) | {
    'tools.annual_readiness.station.ContinuousStationState'}
OWNERS = ('env', 'core', 'controller')
KINDS = frozenset(('state', 'rebuild', 'external', 'inplace'))
_DOCUMENT_FIELDS = frozenset(('schema', 'binding', 'minute', 'owner_types',
    'coverage', 'state', 'runner_state', 'rng', 'content_sha256'))
_PENDING_REFERENCE = object()
CODE_FINGERPRINT_ALGORITHM = 'python.code_fields.v1'


def _name(value):
    return type(value).__module__ + '.' + type(value).__qualname__


def _type_signature(class_type):
    if not isinstance(class_type, type):
        raise TypeError('An explicit runtime class is required')
    return [base.__module__ + '.' + base.__qualname__ for base in class_type.__mro__]


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(',', ':')).encode('utf8')


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _mutable_state(value):
    return (type(value) in (dict, list, set, np.ndarray, np.random.Generator)
            or _name(value) in PHYSICAL_CLASSES)


def _encode(value, _active=None, _seen=None, _declare=True):
    """V27-compatible typed values with local registry and strict subclasses."""
    active = set() if _active is None else _active
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else {'type': 'nonfinite', 'value':
            'nan' if math.isnan(value) else 'inf' if value > 0 else '-inf'}
    if isinstance(value, np.generic):
        if value.dtype.kind not in 'biuf':
            raise TypeError('Unsupported checkpoint scalar dtype: ' + str(value.dtype))
        return {'type': 'scalar', 'dtype': str(value.dtype),
                'value': _encode(value.item(), active, _seen)}
    if id(value) in active:
        raise ValueError('Cyclic state requires an explicit alias classification')
    if _seen is not None and _mutable_state(value) and _declare:
        if id(value) in _seen:
            return {'type': 'ref', 'value': _seen[id(value)][0]}
        token = len(_seen)
        _seen[id(value)] = (token, value)  # Retain values against temporary ID reuse.
        return {'type': 'shared', 'id': token,
                'value': _encode(value, active, _seen, _declare=False)}
    active.add(id(value))
    try:
        encode = lambda item: _encode(item, active, _seen)
        if type(value) is np.ndarray:
            if value.dtype.hasobject or value.dtype.fields or value.dtype.kind not in 'biuf':
                raise TypeError('Unsupported snapshot array dtype: ' + str(value.dtype))
            return {'type': 'array', 'dtype': str(value.dtype), 'shape': list(value.shape),
                    'value': encode(value.tolist())}
        if type(value) is dict:
            return {'type': 'dict', 'value': [[encode(k), encode(v)] for k, v in value.items()]}
        if type(value) is tuple:
            return {'type': 'tuple', 'value': [encode(item) for item in value]}
        if type(value) in (set, frozenset):
            if _seen is not None:
                # Determine canonical traversal before assigning reference IDs;
                # sorting encoded declarations afterwards could create forward refs.
                items = sorted(value, key=lambda item: _json(_encode(item)))
                return {'type': 'frozenset' if type(value) is frozenset else 'set',
                        'value': [encode(item) for item in items]}
            items = [encode(item) for item in value]
            return {'type': 'frozenset' if type(value) is frozenset else 'set',
                    'value': sorted(items, key=lambda item: _json(item))}
        if type(value) is list:
            return [encode(item) for item in value]
        if isinstance(value, (list, tuple, dict, set, frozenset)):
            raise TypeError('Unsupported behavioral container subclass: ' + _name(value))
        if isinstance(value, Path):
            return {'type': 'path', 'value': str(value)}
        if type(value) is np.random.Generator:
            return {'type': 'rng', 'class': type(value.bit_generator).__name__,
                    'value': encode(value.bit_generator.state)}
        if _name(value) in PHYSICAL_CLASSES:
            return {'type': 'object', 'class': _name(value), 'value': encode(vars(value))}
        raise TypeError('Unregistered checkpoint state type: ' + _name(value))
    finally:
        active.remove(id(value))


def _decode(value, _refs=None):
    refs = {} if _refs is None else _refs
    decode = lambda item: _decode(item, refs)
    if type(value) is list:
        return [decode(item) for item in value]
    if type(value) in (str, bool, int, float) or value is None:
        if type(value) is float and not math.isfinite(value):
            raise ValueError('Nonfinite checkpoint values require a typed representation')
        return value
    if type(value) is not dict:
        raise ValueError('Unsupported encoded checkpoint value')
    kind, data = value.get('type'), value.get('value')
    extra = ({'dtype', 'shape'} if kind == 'array' else {'dtype'} if kind == 'scalar'
             else {'class'} if kind in ('object', 'rng') else {'id'} if kind == 'shared' else set())
    if set(value) != {'type', 'value'} | extra:
        raise ValueError('Unsupported typed checkpoint fields')
    if kind == 'ref':
        if type(data) is not int or data < 0 or data not in refs or refs[data] is _PENDING_REFERENCE:
            raise ValueError('Unknown, forward or cyclic typed state reference')
        return refs[data]
    if kind == 'shared':
        token = value['id']
        if type(token) is not int or token < 0 or token in refs:
            raise ValueError('Invalid or duplicate shared state declaration')
        refs[token] = _PENDING_REFERENCE
        try:
            result = decode(data)
            if not _mutable_state(result):
                raise ValueError('Shared state declaration requires a mutable typed value')
        except Exception:
            refs.pop(token, None)
            raise
        refs[token] = result
        return result
    if kind == 'dict':
        if type(data) is not list or any(type(pair) is not list or len(pair) != 2 for pair in data):
            raise ValueError('Invalid typed dictionary')
        result = {}
        for key, item in data:
            key = decode(key)
            if key in result:
                raise ValueError('Duplicate typed dictionary key')
            result[key] = decode(item)
        return result
    if kind in ('tuple', 'set', 'frozenset'):
        if type(data) is not list:
            raise ValueError('Invalid typed collection')
        return {'tuple': tuple, 'set': set, 'frozenset': frozenset}[kind](decode(v) for v in data)
    if kind == 'array':
        dtype = np.dtype(value['dtype'])
        shape = value['shape']
        if dtype.hasobject or dtype.fields or dtype.kind not in 'biuf' or type(shape) is not list or any(
                type(n) is not int or n < 0 for n in shape):
            raise ValueError('Unsupported typed array dtype or shape')
        return np.asarray(decode(data), dtype=dtype).reshape(shape)
    if kind == 'scalar':
        dtype = np.dtype(value['dtype'])
        if dtype.kind not in 'biuf':
            raise ValueError('Unsupported typed scalar dtype')
        decoded = decode(data)
        if type(decoded) not in (bool, int, float):
            raise ValueError('Invalid typed scalar value')
        return dtype.type(decoded)
    if kind == 'path' and type(data) is str:
        return Path(data)
    if kind == 'nonfinite' and data in ('nan', 'inf', '-inf'):
        return {'nan': float('nan'), 'inf': float('inf'), '-inf': float('-inf')}[data]
    if kind == 'rng':
        name = value['class']
        if name not in {'PCG64', 'PCG64DXSM', 'MT19937', 'Philox', 'SFC64'}:
            raise ValueError('Unregistered checkpoint RNG class')
        result = np.random.Generator(getattr(np.random, name)())
        result.bit_generator.state = decode(data)
        return result
    if kind == 'object':
        name = value['class']
        if name not in PHYSICAL_CLASSES:
            raise ValueError('Unregistered checkpoint physical class: ' + str(name))
        module_name, class_name = name.rsplit('.', 1)
        class_type = getattr(importlib.import_module(module_name), class_name)
        attributes = decode(data)
        if type(attributes) is not dict or any(type(key) is not str for key in attributes):
            raise ValueError('Invalid typed object fields')
        result = (class_type.__new__(class_type) if name == 'types.SimpleNamespace'
                  else object.__new__(class_type))
        result.__dict__.update(attributes)
        return result
    raise ValueError('Unknown typed checkpoint kind: ' + str(kind))


def _fold_fingerprints(label, items):
    digest = hashlib.sha256()
    count = 0
    for item in items:
        encoded = _json(item)
        digest.update(str(len(encoded)).encode('ascii') + b':' + encoded)
        count += 1
    return {'collection_type': label, 'count': count, 'sha256': digest.hexdigest()}


def _code_constant(value):
    """Encode compiled literals without interpreter interning/reference flags."""
    if type(value) is CodeType:
        return {'type': 'code', 'value': _code_fields(value)}
    if value is Ellipsis:
        return {'type': 'ellipsis'}
    if type(value) is bytes:
        return {'type': 'bytes', 'value': value.hex()}
    if type(value) is complex:
        return {'type': 'complex', 'real': _encode(value.real), 'imag': _encode(value.imag)}
    if type(value) is tuple:
        return {'type': 'tuple', 'value': [_code_constant(item) for item in value]}
    if type(value) is frozenset:
        return {'type': 'frozenset', 'value': sorted(
            (_code_constant(item) for item in value), key=_json)}
    if value is None or type(value) in (str, bool, int, float):
        return _encode(value)
    raise TypeError('Unsupported compiled code constant: ' + _name(value))


def _code_fields(code):
    """Bind bytecode, nested literals, names, calling convention and source maps."""
    fields = {name: getattr(code, name) for name in (
        'co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals',
        'co_stacksize', 'co_flags', 'co_filename', 'co_name', 'co_firstlineno')}
    fields.update({name: list(getattr(code, name)) for name in (
        'co_names', 'co_varnames', 'co_freevars', 'co_cellvars')})
    fields['co_code'] = code.co_code.hex()
    fields['co_consts'] = [_code_constant(item) for item in code.co_consts]
    # These explicit source/exception-map fields vary by Python version, rather
    # than with execution or string interning. Bind every field the host exposes.
    for name in ('co_qualname', 'co_linetable', 'co_exceptiontable'):
        if hasattr(code, name):
            value = getattr(code, name)
            fields[name] = value.hex() if type(value) is bytes else value
    if not hasattr(code, 'co_linetable'):
        fields['co_lnotab'] = code.co_lnotab.hex()
    return fields


def _fingerprint(value):
    """Verify rebuilt values, rather than trusting an asserted identity token."""
    if isinstance(value, type):
        return {'class_mro': _type_signature(value)}
    if isinstance(value, (MethodType, FunctionType)):
        function = value.__func__ if isinstance(value, MethodType) else value
        return {'function': function.__module__ + '.' + function.__qualname__,
                'code_fingerprint_algorithm': CODE_FINGERPRINT_ALGORITHM,
                'code_sha256': _digest(_code_fields(function.__code__)),
                'bound_method': isinstance(value, MethodType),
                'receiver_type': _type_signature(type(value.__self__)) if isinstance(value, MethodType) else None,
                'defaults': _fingerprint(function.__defaults__),
                'kwdefaults': _fingerprint(function.__kwdefaults__),
                'closure': {key: _fingerprint(cell.cell_contents)
                    for key, cell in zip(function.__code__.co_freevars, function.__closure__ or ())}}
    if _name(value) in ('pandas.core.frame.DataFrame', 'pandas.core.series.Series'):
        import pandas as pd
        columns = value.columns.tolist() if hasattr(value, 'columns') else [value.name]
        dtypes = [str(dtype) for dtype in value.dtypes] if hasattr(value, 'columns') else [str(value.dtype)]
        frame = value if hasattr(value, 'columns') else value.to_frame()
        objects = [i for i, dtype in enumerate(frame.dtypes) if pd.api.types.is_object_dtype(dtype)]
        # pandas may stringify unhashable object cells, truncating nested arrays
        # and accepting behavior-bearing objects. Hash those cells by their typed
        # values instead, retaining only one cell's encoding at a time. Ordinary
        # numeric/datetime columns keep pandas' vectorized hashing path.
        known = frame.iloc[:, [i for i in range(len(frame.columns)) if i not in objects]] if objects else value
        hashed = pd.util.hash_pandas_object(known, index=True, categorize=False)
        result = {'pandas_type': _name(value), 'shape': list(value.shape),
                'columns': _encode(columns), 'dtypes': dtypes,
                'index_type': _name(value.index), 'index_names': _encode(list(value.index.names)),
                'index_dtype': str(value.index.dtype),
                'values_sha256': hashlib.sha256(hashed.to_numpy().tobytes()).hexdigest(),
                'attrs': _fingerprint(value.attrs)}
        if objects:
            result['object_columns'] = [{'position': i, 'cells': _fold_fingerprints(
                'pandas.object_cells', (_fingerprint(cell) for cell in frame.iloc[:, i]))}
                for i in objects]
        return result
    if type(value) is dict:
        return _fold_fingerprints('builtins.dict',
            ([_fingerprint(k), _fingerprint(v)] for k, v in value.items()))
    if type(value) in (list, tuple):
        return _fold_fingerprints(_name(value), (_fingerprint(v) for v in value))
    if (isinstance(value, tuple) and type(getattr(value, '_fields', None)) is tuple
            and all(type(key) is str for key in value._fields)):
        return {'namedtuple_type': _name(value), 'fields': list(value._fields),
                'values': _fold_fingerprints('namedtuple.values',
                    (_fingerprint(v) for v in value))}
    if is_dataclass(value) and not isinstance(value, type):
        return {'dataclass': _name(value), 'fields': _fingerprint(vars(value))}
    return {'typed_sha256': _digest(_encode(value))}


def _check_binding(binding):
    if type(binding) is not dict or any(not binding.get(key) for key in ('source', 'inputs', 'config')):
        raise ValueError('Checkpoint binding requires source, inputs and config identities')
    _json(binding)


def _check_quiescent(core, runner):
    minute = getattr(core, 'time', None)
    if type(minute) not in (int, float) or not math.isfinite(minute) or minute < 0.:
        raise ValueError('A finite nonnegative quiescent core clock is required')
    if getattr(core, '_m23_advance_depth', 0) != 0:
        raise ValueError('Checkpoint requires a quiescent post-step boundary')
    if getattr(core, 'failed', False):
        raise ValueError('A failed core cannot be admitted for resume')
    if type(runner) is not dict or type(runner.get('committed_end_minute')) not in (int, float) or runner['committed_end_minute'] != minute:
        raise ValueError('Runner committed boundary differs from actual core clock')
    return float(minute)


def capture_checkpoint(env, controller, *, binding, field_plan, runner_state):
    """Capture all declared live fields without calling any runtime method."""
    _check_binding(binding)
    core = getattr(env, 'core', None)
    if core is None:
        raise ValueError('A live core is required for checkpoint capture')
    minute = _check_quiescent(core, runner_state)
    owners = dict(env=env, core=core, controller=controller)
    if type(field_plan) is not dict or set(field_plan) != set(OWNERS):
        raise ValueError('Field coverage requires env, core and controller partitions')
    if (field_plan['env'].get('core') != 'alias:core'
            or field_plan['core'].get('env') != 'alias:env'):
        raise ValueError('Root core/env back-reference requires explicit alias classification')
    coverage, states = {}, {}
    seen = {}
    for owner, value in owners.items():
        fields = vars(value)
        plan = field_plan[owner]
        if type(plan) is not dict or set(plan) != set(fields):
            raise ValueError('Unclassified or extra field coverage in ' + owner)
        coverage[owner], payload = {}, {}
        for key, kind in plan.items():
            if type(key) is not str or type(kind) is not str or (kind not in KINDS and not kind.startswith('alias:')):
                raise ValueError('Invalid field partition at ' + owner + '.' + str(key))
            item = fields[key]
            entry = {'kind': kind, 'type': _type_signature(type(item))}
            if kind in ('state', 'inplace'):
                if kind == 'inplace' and type(item) is not dict:
                    raise TypeError('In-place shared fields must be plain dictionaries')
                payload[key] = item
            elif kind == 'rebuild':
                entry['fingerprint'] = _fingerprint(item)
            elif kind.startswith('alias:'):
                target = kind[6:].split('.')
                if len(target) not in (1, 2) or target[0] not in owners:
                    raise ValueError('Invalid checkpoint alias target: ' + kind)
                target_value = owners[target[0]]
                if len(target) == 2:
                    target_value = vars(target_value).get(target[1], object())
                if target_value is not item:
                    raise ValueError('Checkpoint alias does not match live object identity: ' + kind)
            coverage[owner][key] = entry
        states[owner] = _encode(payload, _seen=seen)
    document = dict(schema=SCHEMA, binding=copy.deepcopy(binding), minute=minute,
        owner_types={owner: _type_signature(type(value)) for owner, value in owners.items()},
        coverage=coverage, state=states, runner_state=_encode(runner_state), rng=_encode(rng_state()))
    document['content_sha256'] = _digest(document)
    _validate(document, binding)
    return document


def _validate(document, expected_binding):
    _check_binding(expected_binding)
    if type(document) is not dict or set(document) != _DOCUMENT_FIELDS or document.get('schema') != SCHEMA:
        raise ValueError('Checkpoint schema or document fields mismatch')
    if document.get('content_sha256') != _digest({key: value for key, value in document.items() if key != 'content_sha256'}):
        raise ValueError('Checkpoint content SHA integrity mismatch')
    if document['binding'] != expected_binding:
        raise ValueError('Checkpoint source/input/config binding mismatch')
    for field in ('owner_types', 'coverage', 'state'):
        if type(document[field]) is not dict or set(document[field]) != set(OWNERS):
            raise ValueError('Checkpoint owner coverage mismatch')
    if (document['coverage']['env'].get('core', {}).get('kind') != 'alias:core'
            or document['coverage']['core'].get('env', {}).get('kind') != 'alias:env'):
        raise ValueError('Checkpoint root back-reference alias coverage mismatch')
    decoded, references = {}, {}
    for owner in OWNERS:
        coverage = document['coverage'][owner]
        if type(coverage) is not dict:
            raise ValueError('Invalid checkpoint field coverage')
        fields = _decode(document['state'][owner], references)
        if type(fields) is not dict:
            raise ValueError('Invalid checkpoint state fields')
        expected_fields = set()
        for key, entry in coverage.items():
            if type(key) is not str or type(entry) is not dict:
                raise ValueError('Invalid checkpoint coverage entry')
            kind = entry.get('kind')
            if type(kind) is not str or (kind not in KINDS and not kind.startswith('alias:')):
                raise ValueError('Invalid checkpoint coverage disposition')
            if set(entry) != {'kind', 'type'} | ({'fingerprint'} if kind == 'rebuild' else set()):
                raise ValueError('Invalid checkpoint coverage fields')
            if kind in ('state', 'inplace'):
                expected_fields.add(key)
        if set(fields) != expected_fields:
            raise ValueError('Checkpoint state and field coverage partition differ')
        decoded[owner] = fields
    runner = _decode(document['runner_state'])
    minute = document['minute']
    if (type(minute) not in (int, float) or not math.isfinite(minute) or minute < 0. or
            type(runner) is not dict or type(runner.get('committed_end_minute')) not in (int, float) or
            runner['committed_end_minute'] != minute):
        raise ValueError('Checkpoint committed boundary mismatch')
    random_state = _decode(document['rng'])
    if type(random_state) is not dict or not {'python', 'numpy'} <= set(random_state):
        raise ValueError('Checkpoint RNG state is incomplete')
    return decoded, runner, random_state


def save_checkpoint(document, path):
    """Write once, fsync, reread and verify; publication is the runner's job."""
    _validate(document, document.get('binding', {}))
    data = _json(document) + b'\n'
    path = Path(path)
    with path.open('xb') as stream:
        if stream.write(data) != len(data):
            raise OSError('Incomplete checkpoint write')
        stream.flush()
        os.fsync(stream.fileno())
    actual = path.read_bytes()
    if actual != data:
        raise ValueError('Persisted checkpoint bytes differ')
    load_checkpoint(path, expected_binding=document['binding'])
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(actual).hexdigest(),
            'bytes': len(actual), 'content_sha256': document['content_sha256']}


def load_checkpoint(path, *, expected_binding):
    document = json.loads(Path(path).read_text(encoding='utf8'))
    _validate(document, expected_binding)
    return document


def _rebind_inplace_aliases(owners, coverage, inplace):
    """Redirect decoded graph links to factory dictionaries without mutating them."""
    replacements, template_sources = {}, {}
    for template, restored, _ in inplace:
        if (id(restored) in replacements and replacements[id(restored)] is not template
                or id(template) in template_sources and template_sources[id(template)] is not restored):
            raise ValueError('Inplace shared dictionary template identity mismatch')
        replacements[id(restored)] = template
        template_sources[id(template)] = restored
    seen = set()

    def rebind(value):
        if id(value) in replacements:
            return replacements[id(value)]
        if id(value) in seen:
            return value
        seen.add(id(value))
        if type(value) is dict:
            for key, item in list(value.items()):
                value[key] = rebind(item)
        elif type(value) is list:
            value[:] = [rebind(item) for item in value]
        elif type(value) is tuple:
            return tuple(rebind(item) for item in value)
        elif type(value) is set:
            items = [rebind(item) for item in value]
            value.clear()
            value.update(items)
        elif type(value) is frozenset:
            return frozenset(rebind(item) for item in value)
        elif _name(value) in PHYSICAL_CLASSES:
            rebind(vars(value))
        return value

    # Payload dictionaries are distinct from their factory targets until commit.
    for _, restored, _ in inplace:
        for key, value in list(restored.items()):
            restored[key] = rebind(value)
    for owner in OWNERS:
        for key, entry in coverage[owner].items():
            if entry['kind'] == 'state':
                vars(owners[owner])[key] = rebind(vars(owners[owner])[key])


def restore_checkpoint(document, *, template_env, template_controller, expected_binding,
                       rebuilt_fields=None, external_fields=None, restore_random=True):
    """Restore constructor-free using explicit, verified factory/I/O rebinds.

    Factory-shared ``inplace`` dicts remain the exact template objects. Rebind
    mappings must contain exactly their classified fields, as ``owner.field``.
    New template fields are rejected; templates may lack state-only runtime
    fields created during reset. All original captured fields are reconstructed.
    """
    states, runner, random_state = _validate(document, expected_binding)
    template_core = getattr(template_env, 'core', None)
    core_type = type(template_core) if template_core is not None else getattr(template_env, 'engineering_core_type', None)
    templates = dict(env=template_env, core=template_core, controller=template_controller)
    types = dict(env=type(template_env), core=core_type, controller=type(template_controller))
    for owner in OWNERS:
        if _type_signature(types[owner]) != document['owner_types'][owner]:
            raise ValueError('Checkpoint template runtime type signature mismatch: ' + owner)
        if templates[owner] is not None and set(vars(templates[owner])) - set(document['coverage'][owner]):
            raise ValueError('Unclassified template field outside checkpoint coverage: ' + owner)
    supplied = dict(rebuild=dict(rebuilt_fields or {}), external=dict(external_fields or {}))
    required = {kind: {owner + '.' + key for owner in OWNERS
                for key, entry in document['coverage'][owner].items() if entry['kind'] == kind}
                for kind in ('rebuild', 'external')}
    for kind in required:
        if set(supplied[kind]) != required[kind]:
            raise ValueError('Explicit ' + kind + ' rebind fields mismatch')
    owners = {owner: object.__new__(types[owner]) for owner in OWNERS}
    inplace, aliases = [], []
    for owner in OWNERS:
        fields = vars(owners[owner])
        for key, entry in document['coverage'][owner].items():
            kind = entry['kind']
            location = owner + '.' + key
            if kind == 'state':
                item = states[owner][key]
            elif kind == 'inplace':
                template = templates[owner]
                item = vars(template).get(key) if template is not None else None
                restored_value = states[owner][key]
                if type(item) is not dict or type(restored_value) is not dict:
                    raise ValueError('An existing shared dictionary is required for inplace restore: ' + location)
                inplace.append((item, restored_value, copy.deepcopy(item)))
            elif kind in ('rebuild', 'external'):
                item = supplied[kind][location]
                if _type_signature(type(item)) != entry['type']:
                    raise ValueError('Rebound field type signature mismatch: ' + location)
                if kind == 'rebuild':
                    if _fingerprint(item) != entry['fingerprint']:
                        raise ValueError('Rebuild value signature mismatch: ' + location)
                    if isinstance(item, MethodType) and item.__self__ is templates[owner]:
                        item = MethodType(item.__func__, owners[owner])
            else:
                aliases.append((owner, key, kind[6:]))
                continue
            fields[key] = item
    _rebind_inplace_aliases(owners, document['coverage'], inplace)
    while aliases:
        remaining = []
        for owner, key, target in aliases:
            pieces = target.split('.')
            if len(pieces) not in (1, 2) or pieces[0] not in owners:
                raise ValueError('Invalid checkpoint alias target')
            item = owners[pieces[0]]
            if len(pieces) == 2:
                if pieces[1] not in vars(item):
                    remaining.append((owner, key, target))
                    continue
                item = vars(item)[pieces[1]]
            if _type_signature(type(item)) != document['coverage'][owner][key]['type']:
                raise ValueError('Checkpoint alias type signature mismatch')
            vars(owners[owner])[key] = item
        if len(remaining) == len(aliases):
            raise ValueError('Unresolved or cyclic checkpoint field aliases')
        aliases = remaining
    _check_quiescent(owners['core'], runner)
    if float(owners['core'].time) != document['minute']:
        raise ValueError('Restored core clock differs from checkpoint')
    original_rng = rng_state() if restore_random else None
    try:
        if restore_random:
            restore_rng(random_state)
        for item, restored, _ in inplace:
            item.clear()
            item.update(restored)
    except Exception:
        for item, _, original in inplace:
            item.clear()
            item.update(original)
        if original_rng is not None:
            restore_rng(original_rng)
        raise
    return {'env': owners['env'], 'controller': owners['controller'], 'runner_state': runner,
            'coverage': copy.deepcopy(document['coverage']),
            'content_sha256': document['content_sha256'], 'reset_called': False,
            'observation_called': False, 'constructors_called': False}
