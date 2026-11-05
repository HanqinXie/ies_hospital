"""Unused output-only alternatives to the existing JSON logging walkers.

No caller is switched here. ``normalize_json_exact`` preserves the dispatch and
copy semantics of sci_engineering.solver_log.normalize_json. Only exact built-in
scalar leaves bypass recursion; subclasses and unknown objects retain the native
dict/list/tuple/tolist/float dispatch. ``iter_row_references_exact`` yields the
original reference objects and fresh location lists, without caching prefixes.
"""
from math import isfinite


def normalize_json_exact(value):
    """Return the native normalized value, avoiding calls for scalar leaves."""
    value_type = type(value)
    if value_type is float:
        return value if isfinite(value) else None
    if (value_type is str or value_type is int or value_type is bool
            or value is None or value_type is bytes):
        return value
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            # Native comprehension evaluates str(key) before normalizing item.
            key = str(key)
            item_type = type(item)
            if item_type is float:
                result[key] = item if isfinite(item) else None
            elif (item_type is str or item_type is int or item_type is bool
                    or item is None or item_type is bytes):
                result[key] = item
            else:
                result[key] = normalize_json_exact(item)
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            item_type = type(item)
            if item_type is float:
                result.append(item if isfinite(item) else None)
            elif (item_type is str or item_type is int or item_type is bool
                    or item is None or item_type is bytes):
                result.append(item)
            else:
                result.append(normalize_json_exact(item))
        return result
    # Keep two separate attribute lookups, including their native exceptions.
    if hasattr(value, 'tolist'):
        return normalize_json_exact(value.tolist())
    if isinstance(value, float) and not isfinite(value):
        return None
    return value


def iter_row_references_exact(value, location=()):
    """Yield native (original reference dict, new location list) pairs lazily."""
    if isinstance(value, (list, tuple)):
        ordinary_prefix = type(location) is tuple
        for index, item in enumerate(value):
            item_type = type(item)
            if ordinary_prefix and (item_type is float or item_type is int
                    or item_type is str or item_type is bool or item is None
                    or item_type is bytes):
                continue
            yield from iter_row_references_exact(item, location + (index,))
    elif isinstance(value, dict):
        ordinary_prefix = type(location) is tuple
        for key, item in value.items():
            if key == 'rows_ref' and isinstance(item, dict):
                yield item, list(location)
            else:
                item_type = type(item)
                if ordinary_prefix and (item_type is float or item_type is int
                        or item_type is str or item_type is bool or item is None
                        or item_type is bytes):
                    continue
                yield from iter_row_references_exact(item, location + (key,))
