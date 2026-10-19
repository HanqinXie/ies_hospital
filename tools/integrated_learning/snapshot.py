"""Exact offline execution snapshots; never an online policy input.

The full future scenario belongs to continuation, while controllers receive
only the legal feature history in policies.py. Restore avoids reset and core
constructor side effects and re-establishes the original event/segment aliases.
"""
from __future__ import annotations
import copy
from dataclasses import is_dataclass
import hashlib
import json
import math
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import numpy as np

SHARED = {'root', 'hourly', 'zones', 'groups', 'plant', 'thermal_cache'}
ALIASES = {'event_history', 'segment_history', 'solver_errors'}


def encode(value):
    if isinstance(value, np.ndarray):
        return {'_type': 'ndarray', 'dtype': str(value.dtype), 'shape': list(value.shape), 'values': encode(value.tolist())}
    if isinstance(value, np.generic):
        return encode(value.item())
    if isinstance(value, dict):
        return {'_type': 'dict', 'items': [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, tuple):
        return {'_type': 'tuple', 'values': [encode(v) for v in value]}
    if isinstance(value, set):
        encoded = [encode(v) for v in value]
        return {'_type': 'set', 'values': sorted(encoded, key=lambda v: json.dumps(v, sort_keys=True))}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, Path):
        return {'_type': 'path', 'value': str(value)}
    if isinstance(value, float) and not math.isfinite(value):
        return {'_type': 'nonfinite', 'value': 'nan' if math.isnan(value) else 'inf' if value > 0 else '-inf'}
    if isinstance(value, np.random.Generator):
        return {'_type': 'numpy_generator', 'bit_generator': type(value.bit_generator).__name__, 'state': encode(value.bit_generator.state)}
    if is_dataclass(value) or isinstance(value, SimpleNamespace):
        return {'_type': 'state_object', 'class': type(value).__module__ + '.' + type(value).__qualname__, 'fields': encode(vars(value))}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError('Unregistered snapshot state type: ' + repr(type(value)))


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    kind = value['_type']
    if kind == 'dict':
        return {decode(k): decode(v) for k, v in value['items']}
    if kind == 'tuple':
        return tuple(decode(v) for v in value['values'])
    if kind == 'set':
        return set(decode(v) for v in value['values'])
    if kind == 'ndarray':
        return np.asarray(decode(value['values']), dtype=value['dtype']).reshape(value['shape'])
    if kind == 'path':
        return Path(value['value'])
    if kind == 'nonfinite':
        return {'nan': float('nan'), 'inf': float('inf'), '-inf': float('-inf')}[value['value']]
    if kind == 'numpy_generator':
        name = value['bit_generator']
        if name not in ('PCG64', 'PCG64DXSM', 'MT19937', 'Philox', 'SFC64'):
            raise ValueError('Unregistered random generator')
        generator = np.random.Generator(getattr(np.random, name)())
        generator.bit_generator.state = decode(value['state'])
        return generator
    if kind == 'state_object':
        fields = decode(value['fields'])
        if value['class'] == 'types.SimpleNamespace':
            return SimpleNamespace(**fields)
        if value['class'] == 'hospital_ies.resilience.RuntimeState':
            from hospital_ies.resilience import RuntimeState
            return RuntimeState(**fields)
        raise ValueError('Unregistered physical state class: ' + value['class'])
    raise ValueError('Unknown typed snapshot value: ' + kind)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def rng_state():
    result = {'python': random.getstate(), 'numpy': np.random.get_state()}
    torch = sys.modules.get('torch')
    if torch is not None:
        result['torch_cpu'] = torch.get_rng_state().tolist()
        if torch.cuda.is_initialized():
            result['torch_cuda'] = [v.tolist() for v in torch.cuda.get_rng_state_all()]
    return result


def restore_rng(value):
    random.setstate(value['python']); np.random.set_state(value['numpy'])
    if 'torch_cpu' in value:
        import torch
        torch.set_rng_state(torch.tensor(value['torch_cpu'], dtype=torch.uint8))
        if 'torch_cuda' in value:
            torch.cuda.set_rng_state_all([torch.tensor(v, dtype=torch.uint8) for v in value['torch_cuda']])


class ExecutionSnapshot:
    def __init__(self, template, dynamic, state, core, rng, binding):
        self.template_type = type(template); self.core_type = type(template.core)
        self.shared = {k: v for k, v in vars(template).items() if k in SHARED}
        self.dynamic = copy.deepcopy(dynamic); self.state = copy.deepcopy(state)
        self.core = copy.deepcopy(core); self.rng = copy.deepcopy(rng); self.binding = copy.deepcopy(binding)

    def to_dict(self):
        return {'schema': 'integrated.execution_snapshot.v1', 'binding': copy.deepcopy(self.binding),
            'environment_type': self.template_type.__module__ + '.' + self.template_type.__qualname__,
            'core_type': self.core_type.__module__ + '.' + self.core_type.__qualname__,
            'dynamic': encode(self.dynamic), 'physical_state': encode(self.state), 'core_state': encode(self.core), 'rng': encode(self.rng)}

    @property
    def content_sha256(self):
        return digest(self.to_dict())

    def fork(self, *, restore_random=True):
        env = object.__new__(self.template_type)
        env.__dict__.update(self.shared)
        env.__dict__.update(copy.deepcopy(self.dynamic))
        env.state = copy.deepcopy(self.state)
        env.core = object.__new__(self.core_type)
        env.core.__dict__.update(copy.deepcopy(self.core)); env.core.env = env
        env.event_history = env.core.events; env.segment_history = env.core.segments; env.solver_errors = env.core.errors
        if restore_random:
            restore_rng(copy.deepcopy(self.rng))
        return env

    def save(self, path):
        path = Path(path)
        if path.exists():
            raise FileExistsError('Do not overwrite an execution snapshot')
        document = self.to_dict()
        path.write_text(json.dumps({'payload': document, 'content_sha256': digest(document)}, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    @classmethod
    def load(cls, path, *, template, expected_binding):
        wrapper = json.loads(Path(path).read_text(encoding='utf-8')); document = wrapper['payload']
        if digest(document) != wrapper['content_sha256'] or document.get('schema') != 'integrated.execution_snapshot.v1':
            raise ValueError('Execution snapshot content/schema mismatch')
        if document['binding'] != expected_binding:
            raise ValueError('Execution snapshot physical/data source binding differs')
        if document['environment_type'] != type(template).__module__ + '.' + type(template).__qualname__ or document['core_type'] != type(template.core).__module__ + '.' + type(template.core).__qualname__:
            raise ValueError('Execution snapshot environment/core type differs')
        return cls(template, decode(document['dynamic']), decode(document['physical_state']), decode(document['core_state']), decode(document['rng']), document['binding'])


def capture_execution(env, *, binding):
    if not binding or env.core is None:
        raise ValueError('Snapshot needs a bound initialized execution state')
    dynamic = {k: v for k, v in vars(env).items() if k not in SHARED | ALIASES | {'core', 'state'}}
    core = {k: v for k, v in vars(env.core).items() if k != 'env'}
    return ExecutionSnapshot(env, dynamic, env.state, core, rng_state(), binding)
