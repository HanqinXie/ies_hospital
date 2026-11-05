"""Annual opt-in context for output-only completed-leaf input metadata.

The scope accepts a caller's source assertion without authenticating it. It
does not query a model, state, filesystem or optimizer. Exact-result ownership
and source verification belong to the later actual-result recorder/observer.
Unsupported probe layouts remain explicitly unknown; ordinary execution with
capture disabled does not inspect or normalize supplied leaf arguments.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from .compiled_flow_input import _plain, _source, _index_map, build_compiled_flow_input

_ACTIVE=ContextVar('hospital_ies_annual_compiled_flow_capture',default=None)


def capture_enabled():
    return _ACTIVE.get() is not None


@contextmanager
def capture_scope(source_origin):
    """Temporarily retain a detached, format-validated source assertion."""
    origin=_plain(source_origin)
    _source(origin)
    token=_ACTIVE.set(origin)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def flow_input_fields(**leaf_inputs):
    """Return private leaf fields; this never marks a candidate as actual."""
    origin=_ACTIVE.get()
    if origin is None:return {}
    if 'source_origin' in leaf_inputs:
        raise ValueError('Leaf arguments cannot override capture-scope source_origin')
    nvars=leaf_inputs.get('nvars')
    reason=None
    if type(nvars) is int and nvars not in (47,48):reason='unsupported_leaf_layout'
    elif type(nvars) is int:
        try:_index_map(_plain(leaf_inputs.get('index_map')),nvars)
        except ValueError:reason='unsupported_leaf_index_map'
    if reason is not None:
        return {'_compiled_flow_input_unavailable':dict(
            schema='hospital_ies.annual_program.compiled_flow_capture_unavailable.v1',
            status='unknown',reason=reason,nvars=nvars,
            source_origin=_plain(origin),actual_execution_bound=False,
            source_verification_performed=False,physics_validation_performed=False)}
    return {'_compiled_flow_input':build_compiled_flow_input(
        **leaf_inputs,source_origin=_plain(origin))}
