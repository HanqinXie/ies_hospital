"""Detach environment method closures when copying a live physical checkpoint."""
from copy import deepcopy
from types import FunctionType, MethodType


def _cell(value):
    return (lambda: value).__closure__[0]


def copy_checkpoint(original):
    copied = deepcopy(original)
    functions = {}

    def function(fn):
        if fn in functions:
            return functions[fn]
        if not fn.__closure__:
            return fn
        # Factory wrappers capture earlier bound reset/step methods. deepcopy
        # alone leaves those cells pointing at the source environment.
        cells = []
        for cell in fn.__closure__:
            value = cell.cell_contents
            if value is original:
                value = copied
            elif isinstance(value, MethodType) and value.__self__ is original:
                value = MethodType(function(value.__func__), copied)
            cells.append(_cell(value))
        detached = FunctionType(fn.__code__, fn.__globals__, fn.__name__, fn.__defaults__, tuple(cells))
        detached.__kwdefaults__ = fn.__kwdefaults__
        detached.__annotations__ = fn.__annotations__
        detached.__dict__.update(fn.__dict__)
        functions[fn] = detached
        return detached

    for name, value in vars(original).items():
        if isinstance(value, MethodType) and value.__self__ is original:
            setattr(copied, name, MethodType(function(value.__func__), copied))
    return copied
