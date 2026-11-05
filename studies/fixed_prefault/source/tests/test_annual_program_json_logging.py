"""Equivalence to existing output logging; no solver or production integration."""
import importlib
import json
import math
from pathlib import Path
import random

import numpy as np
import pytest

from tools.annual_readiness.runner import _row_references as native_references
from tools.sci_engineering.solver_log import normalize_json as native_normalize


def api():
    path = Path(__file__).parents[1] / 'tools/annual_program/json_logging.py'
    assert path.is_file(), 'unused exact JSON logging helpers are not implemented'
    return importlib.import_module('tools.annual_program.json_logging')


def wire(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def assert_same(left, right):
    """Compare container copies and exact scalar types without opaque equality."""
    assert type(left) is type(right)
    if type(left) is dict:
        assert list(left) == list(right)
        for key in left:
            assert_same(left[key], right[key])
    elif type(left) is list:
        assert len(left) == len(right)
        for a, b in zip(left, right):
            assert_same(a, b)
    elif type(left) is float:
        assert left == right
        assert math.copysign(1., left) == math.copysign(1., right)
    else:
        assert left is right


@pytest.mark.parametrize('value', [None, True, False, 2**100, '中文', b'opaque',
                                  -0., 0., 1.25, float('nan'), float('inf'),
                                  -float('inf')])
def test_exact_primitives_preserve_native_type_identity_and_nonfinite_null(value):
    assert_same(native_normalize(value), api().normalize_json_exact(value))


def test_nested_key_conversion_order_collisions_and_json_bytes():
    source = {1: ('中', None, True, -0., float('nan')),
              '1': [float('inf'), -float('inf'), 4],
              (2, 3): {False: 0., None: 'last'}}
    expected = native_normalize(source)
    actual = api().normalize_json_exact(source)
    assert_same(expected, actual)
    assert list(actual) == ['1', '(2, 3)']
    assert wire(actual) == wire(expected)
    assert isinstance(source[1], tuple) and math.isnan(source[1][-1])


def test_container_subclasses_use_native_iteration_and_ignore_their_tolist():
    class Dict(dict):
        def items(self):
            yield 'override', [float('inf')]
        def tolist(self):
            raise AssertionError('dict dispatch must precede tolist')
    class List(list):
        def __iter__(self):
            return iter((float('nan'), 9))
        def tolist(self):
            raise AssertionError('list dispatch must precede tolist')
    class Tuple(tuple):
        def __iter__(self):
            return iter((float('-inf'), 10))
        def tolist(self):
            raise AssertionError('tuple dispatch must precede tolist')
    source = [Dict(ignored=1), List([0]), Tuple([0])]
    actual = api().normalize_json_exact(source)
    assert actual == [{'override': [None]}, [None, 9], [None, 10]]
    assert wire(actual) == wire(native_normalize(source))


def test_scalar_subclasses_and_unknowns_are_not_coerced():
    class Int(int): pass
    class Str(str): pass
    class Float(float): pass
    class AsListFloat(float):
        def tolist(self): return [float('inf'), -0.]
    class Opaque: pass
    source = [Int(12), Str('s'), Float(1.5), Float('nan'),
              AsListFloat('nan'), Opaque(), complex(1, 2)]
    actual = api().normalize_json_exact(source)
    assert_same(native_normalize(source), actual)
    assert actual[0] is source[0] and actual[1] is source[1]
    assert actual[2] is source[2] and actual[5] is source[5]
    assert actual[3] is None and actual[4] == [None, -0.]


def test_int_and_string_subclass_tolist_and_class_spoof_follow_native_dispatch():
    class Int(int):
        def tolist(self): return {'from_int': float('inf')}
    class Str(str):
        def tolist(self): return [float('nan')]
    class LooksLikeDict:
        @property
        def __class__(self): return dict
        def items(self): return [('spoofed', float('-inf'))]
    source = [Int(2), Str('text'), LooksLikeDict()]
    assert wire(api().normalize_json_exact(source)) == wire(native_normalize(source))
    assert api().normalize_json_exact(source) == [
        {'from_int': None}, [None], {'spoofed': None}]


def test_tolist_lookup_call_and_key_conversion_event_order():
    def fixture():
        events = []
        class Key:
            def __str__(self):
                events.append('key')
                return 'converted'
        class Convert:
            def __getattribute__(self, name):
                if name == 'tolist': events.append('lookup')
                return object.__getattribute__(self, name)
            def tolist(self):
                events.append('call')
                return (float('nan'), {'x': 2})
        return {Key(): Convert()}, events
    source, expected_events = fixture()
    expected = native_normalize(source)
    source, actual_events = fixture()
    actual = api().normalize_json_exact(source)
    assert wire(actual) == wire(expected)
    assert actual_events == expected_events == ['key', 'lookup', 'lookup', 'call']


def test_shared_aliases_are_copied_each_time_opaque_leaves_stay_shared():
    opaque = object()
    shared = {'a': [1., opaque]}
    source = [shared, shared, (shared,)]
    actual = api().normalize_json_exact(source)
    assert_same(native_normalize(source), actual)
    assert actual[0] is not shared and actual[0] is not actual[1]
    assert actual[0]['a'] is not actual[1]['a']
    assert actual[2][0] is not actual[0]
    assert actual[0]['a'][1] is opaque
    actual[0]['a'].append('new')
    assert source[0]['a'] == [1., opaque]
    assert actual[1]['a'] == [1., opaque]


@pytest.mark.parametrize('value', [np.float32(float('nan')), np.float64(float('inf')),
    np.float32(-float('inf')), np.int64(7), np.bool_(True),
    np.array(3.), np.array(float('nan')), np.array([[1., float('inf')], [-0., -2.]]),
    np.array([{'x': np.float64('nan')}, (np.int64(4),)], dtype=object)])
def test_numpy_tolist_recurses_exactly(value):
    expected = native_normalize(value)
    actual = api().normalize_json_exact(value)
    assert wire(actual) == wire(expected)
    assert type(actual) is type(expected)


@pytest.mark.parametrize('kind', ['noncallable', 'raise'])
def test_tolist_errors_are_not_swallowed(kind):
    class Convert:
        if kind == 'noncallable':
            tolist = 3
        else:
            def tolist(self): raise ValueError('native conversion error')
    errors = []
    for function in (native_normalize, api().normalize_json_exact):
        with pytest.raises(Exception) as exc:
            function(Convert())
        errors.append((type(exc.value), str(exc.value)))
    assert errors[0] == errors[1]


def test_tolist_lookup_error_and_cycles_are_not_silently_discarded():
    class LookupError:
        @property
        def tolist(self): raise RuntimeError('lookup failure')
    cycle = []
    cycle.append(cycle)
    for function in (native_normalize, api().normalize_json_exact):
        with pytest.raises(RuntimeError, match='lookup failure'):
            function(LookupError())
        with pytest.raises(RecursionError): function(cycle)
    for function in (native_references, api().iter_row_references_exact):
        with pytest.raises(RecursionError): list(function(cycle))


def random_value(rng, depth):
    leaves = [None, True, False, 2**70, -9, '中\\n', -0., 1.25,
              float('nan'), float('inf'), -float('inf')]
    if depth <= 0 or rng.randrange(4) == 0:
        return rng.choice(leaves)
    kind = rng.randrange(5)
    if kind == 0:
        return {rng.choice(['x', 'rows_ref', 4, None, False]): random_value(rng, depth-1)
                for _ in range(rng.randrange(5))}
    if kind < 3:
        result = [random_value(rng, depth-1) for _ in range(rng.randrange(5))]
        return result if kind == 1 else tuple(result)
    if kind == 3:
        return np.array([rng.choice([0., -0., 2.5, float('nan'), float('inf')])
                         for _ in range(rng.randrange(5))])
    return np.int64(rng.randrange(-50, 50))


def test_seeded_bounded_random_normalization_and_reference_equivalence():
    module = api()
    rng = random.Random(20261030)
    for _ in range(400):
        source = random_value(rng, 5)
        assert wire(module.normalize_json_exact(source)) == wire(native_normalize(source))
        assert_references_same(source, module.iter_row_references_exact)


def assert_references_same(source, function, location=()):
    expected = list(native_references(source, location))
    actual = list(function(source, location))
    assert len(actual) == len(expected)
    for (reference, path), (expected_ref, expected_path) in zip(actual, expected):
        assert reference is expected_ref
        assert type(path) is list and path == expected_path
    assert len({id(path) for _, path in actual}) == len(actual)
    return actual


def test_reference_identity_order_barriers_malformed_refs_and_tuple_locations():
    a = {'sha256': 'a', 'rows_ref': {'hidden': True}}
    b = {}
    shared = {'rows_ref': a}
    source = {'first': (shared, {'rows_ref': [shared, {'rows_ref': b}]}),
              'rows_ref': None, 'last': [shared, {'rows_ref': 4}, b'opaque']}
    pairs = assert_references_same(source, api().iter_row_references_exact, ('root', 9))
    assert [p for _, p in pairs] == [
        ['root', 9, 'first', 0], ['root', 9, 'first', 1, 'rows_ref', 0],
        ['root', 9, 'first', 1, 'rows_ref', 1], ['root', 9, 'last', 0]]
    assert pairs[0][0] is pairs[1][0] is pairs[3][0] is a
    assert pairs[2][0] is b
    assert source['first'][0] is shared


def test_reference_subclasses_and_nonstring_key_objects():
    class Dict(dict):
        def items(self): return iter([('rows_ref', ref)])
    class Tuple(tuple):
        def __iter__(self): return iter([Dict()])
    class List(list):
        def __iter__(self): return iter([Tuple()])
    ref = {'anything': 'accepted'}
    key = object()
    source = {key: List(), 'numpy_leaf': np.array([{'rows_ref': {}}], dtype=object)}
    pairs = assert_references_same(source, api().iter_row_references_exact)
    assert len(pairs) == 1 and pairs[0][0] is ref
    assert pairs[0][1][0] is key


@pytest.mark.parametrize('location', [[], ['prefix'], 'prefix', None, 3])
@pytest.mark.parametrize('source', [[4], {'scalar': 1}, {'rows_ref': {}}])
def test_reference_non_tuple_prefix_errors_and_success_match_native(location, source):
    results = []
    for function in (native_references, api().iter_row_references_exact):
        try:
            pairs = list(function(source, location))
            results.append(('ok', [(id(ref), path) for ref, path in pairs]))
        except Exception as exc:
            results.append((type(exc), str(exc)))
    assert results[0] == results[1]


def test_reference_generator_preserves_laziness_and_fresh_paths():
    ref = {'sha': 'before'}
    source = [{'rows_ref': ref}, {'rows_ref': ref}]
    iterator = api().iter_row_references_exact(source)
    assert iter(iterator) is iterator
    first, first_path = next(iterator)
    ref['sha'] = 'after'
    second, second_path = next(iterator)
    assert first is second is ref and second['sha'] == 'after'
    assert first_path == [0] and second_path == [1]
    first_path.append('changed')
    assert second_path == [1]
    with pytest.raises(StopIteration): next(iterator)
