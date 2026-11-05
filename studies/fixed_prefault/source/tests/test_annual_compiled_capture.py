"""Opt-in capture context; pure metadata, never actual qualification."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib

import pytest
from test_annual_compiled_flow_input import fixture as leaf_values


def api():return importlib.import_module('tools.annual_program.compiled_capture')


def test_disabled_scope_returns_empty_fields_without_reading_leaf_values():
    class Bomb:
        def __getattribute__(self,name):raise AssertionError('disabled capture inspected an argument')
    m=api();assert m.capture_enabled() is False
    assert m.flow_input_fields(nvars=Bomb(),closure=Bomb())=={}


@pytest.mark.parametrize('nvars',[47,48])
def test_enabled_scope_attaches_only_the_exact_detached_leaf_input(nvars):
    m=api();values=leaf_values(nvars);origin=values.pop('source_origin')
    with m.capture_scope(origin):
        assert m.capture_enabled() is True
        result=m.flow_input_fields(**values)
        assert set(result)=={'_compiled_flow_input'}
        packet=result['_compiled_flow_input']
        assert packet['raw_x_length']==nvars
        assert packet['closure']['gt_start_kW']==10.
        assert packet['actual_execution_bound'] is packet['source_verification_performed'] is False
        origin['source_root']='mutated after scope entry'
        values['closure']['gt_start_kW']=99.
        assert packet['source_origin']['source_root']=='synthetic_fixture_only'
        assert packet['closure']['gt_start_kW']==10.
    assert m.capture_enabled() is False


def test_nested_scope_and_exception_restore_the_previous_context():
    m=api();values=leaf_values();a=values.pop('source_origin');b=deepcopy(a);b['source_root']='inner'
    with m.capture_scope(a):
        with pytest.raises(RuntimeError):
            with m.capture_scope(b):
                assert m.flow_input_fields(**values)['_compiled_flow_input']['source_origin']==b
                raise RuntimeError('controlled fixture')
        assert m.flow_input_fields(**values)['_compiled_flow_input']['source_origin']==a
    assert not m.capture_enabled()


def test_capture_is_not_inherited_by_an_unrelated_thread():
    m=api();values=leaf_values();origin=values.pop('source_origin')
    with m.capture_scope(origin):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(m.capture_enabled).result() is False
        assert m.capture_enabled()


@pytest.mark.parametrize('change',[
    lambda x:x.pop('source_root'),lambda x:x.update(schema='foreign'),
    lambda x:x['source_files'].update({'../escape':'a'*64}),
    lambda x:x['source_files'].update({'bad.py':'not-a-sha'}),
])
def test_invalid_source_assertion_is_rejected_before_entering_capture(change):
    m=api();origin=leaf_values()['source_origin'];change(origin)
    with pytest.raises(ValueError):
        with m.capture_scope(origin):pytest.fail('invalid capture scope entered')
    assert not m.capture_enabled()


def test_leaf_cannot_override_the_scope_source_origin():
    m=api();values=leaf_values();origin=values['source_origin']
    with m.capture_scope(origin):
        with pytest.raises(ValueError,match='source_origin'):
            m.flow_input_fields(**values)


def test_unsupported_probe_layout_is_explicit_unknown_without_query_or_fake_payload():
    m=api();origin=leaf_values()['source_origin']
    with m.capture_scope(origin):
        result=m.flow_input_fields(nvars=42)
    assert set(result)=={'_compiled_flow_input_unavailable'}
    assert result['_compiled_flow_input_unavailable']['status']=='unknown'
    assert result['_compiled_flow_input_unavailable']['nvars']==42
    assert result['_compiled_flow_input_unavailable']['actual_execution_bound'] is False


@pytest.mark.parametrize('nvars',[47,48])
def test_tracking_probe_with_same_length_and_shifted_indices_remains_explicit_unknown(nvars):
    m=api();values=leaf_values(nvars);origin=values.pop('source_origin')
    values['index_map']['auxiliary_indices']=[[27+3*g+k for k in range(3)] for g in range(4)]
    with m.capture_scope(origin):
        result=m.flow_input_fields(**values)
    assert set(result)=={'_compiled_flow_input_unavailable'}
    reason=result['_compiled_flow_input_unavailable']
    assert reason['status']=='unknown' and reason['reason']=='unsupported_leaf_index_map'
    assert reason['actual_execution_bound'] is reason['physics_validation_performed'] is False
